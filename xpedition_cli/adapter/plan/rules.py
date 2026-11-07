"""The rules file of ``constraint get`` and ``constraint import``, and the changes a file makes.

A rules file names net classes, differential pairs and clearance rules:

```json
{
  "net_classes": [
    {"name": "USB_90", "nets": ["USB_TX_P", "USB_TX_N"],
     "layers": {"all": {"width_mm": 0.1, "min_width_mm": 0.08, "differential_spacing_mm": 0.12}}}
  ],
  "differential_pairs": [{"nets": ["USB_TX_P", "USB_TX_N"]}],
  "clearances": [
    {"rule": "FINE", "layers": {"all": {"trace_to_trace_mm": 0.1, "trace_to_pad_mm": 0.1}},
     "classes": [["USB_90", "(All)"]]}
  ]
}
```

``constraint get`` writes the board's rules in this shape, and ``constraint import`` creates or
changes only what a file names: an Agent reads the rules, edits a few values and imports them.
Lengths are millimetres; a layer is a conductor layer number written as a string, or ``all``.
A net named under a class moves into that class; a class's other nets stay in it. A width
outside its class's minimum or maximum also moves the limit the file leaves out to that width.

This module is pure Python: the adapter reads the board's current rules into the shape of
``Current`` and applies the ``Plan``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import PlanError

TH_PER_MM = 1000.0 / 25.4  # Constraint Manager's automation counts lengths in thousandths of an inch
DEFAULT_CLASS = "(Default)"
DEFAULT_RULE = "(Default Rule)"
ALL_CLASSES = "(All)"
MAX_LENGTH_MM = 25.0  # no width or clearance on a board is larger; a bigger value is a unit mistake

# field -> Constraint Manager constraint type (CT_*)
WIDTH_FIELDS = {"width_mm": 439, "min_width_mm": 283, "max_width_mm": 183, "differential_spacing_mm": 144}
CLEARANCE_FIELDS = {"trace_to_trace_mm": 434, "trace_to_pad_mm": 432, "trace_to_via_mm": 435, "trace_to_plane_mm": 433,
                    "pad_to_pad_mm": 300, "pad_to_via_mm": 302, "via_to_via_mm": 460, "via_to_plane_mm": 459}
TOP_KEYS = ("net_classes", "differential_pairs", "clearances")
MAX_ITEMS = 500


def th(mm: float) -> float:
    return round(mm * TH_PER_MM, 4)


def mm(value_th: float) -> float:
    return round(float(value_th) / TH_PER_MM, 4)


def pair_name(nets: list[str] | tuple[str, ...]) -> str:
    """A differential pair's name as Constraint Manager writes it: the two nets in order, a comma between."""
    return ",".join(sorted(nets))


@dataclass
class Current:
    """The board's rules as the adapter read them, lengths in mm."""

    layers: list[str]  # the conductor layers, "1" at the top
    nets: list[str]
    net_class_of: dict[str, str]  # net -> its net class
    net_classes: dict[str, dict[str, dict[str, float]]]  # class -> layer -> field -> mm
    differential_pairs: list[list[str]] = field(default_factory=list)
    clearances: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)  # rule -> layer -> field -> mm
    class_clearances: dict[tuple[str, str], str] = field(default_factory=dict)  # (from, to) -> rule

    def as_file(self) -> dict[str, Any]:
        """The rules in the rules file's shape, every class and rule with all its values."""
        members: dict[str, list[str]] = {name: [] for name in self.net_classes}
        for net, cls in sorted(self.net_class_of.items()):
            members.setdefault(cls, []).append(net)
        return {
            "net_classes": [{"name": name, "nets": members.get(name, []), "layers": layers}
                            for name, layers in self.net_classes.items()],
            "differential_pairs": [{"nets": sorted(pair)} for pair in self.differential_pairs],
            "clearances": [{"rule": rule, "layers": layers,
                            "classes": [[src, dst] for (src, dst), name in sorted(self.class_clearances.items()) if name == rule]}
                           for rule, layers in self.clearances.items()],
        }


@dataclass
class Plan:
    """What a rules file changes, as the preview lists it and the adapter applies it."""

    new_classes: list[str] = field(default_factory=list)
    moves: dict[str, str] = field(default_factory=dict)  # net -> new class
    class_values: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)  # class -> layer -> field -> mm
    new_pairs: list[list[str]] = field(default_factory=list)
    new_rules: list[str] = field(default_factory=list)
    rule_values: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    class_rules: dict[tuple[str, str], str] = field(default_factory=dict)  # (from, to) -> rule
    changes: list[dict[str, Any]] = field(default_factory=list)  # {path, field, before, after}
    blocked: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.changes


def _error(message: str) -> PlanError:
    return PlanError("E_VALIDATION", f"The rules file is not valid: {message}.")


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise _error(f"{what} must be a name of 1 to 100 characters")
    return value


def _layers(value: Any, fields: dict[str, int], what: str, allow_zero: bool) -> dict[str, dict[str, float]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _error(f"{what}: layers is an object of layer number (or \"all\") to values")
    out: dict[str, dict[str, float]] = {}
    for layer, values in value.items():
        if layer != "all" and not (isinstance(layer, str) and layer.isdigit() and int(layer) >= 1):
            raise _error(f"{what}: layer {layer!r} is not a conductor layer number or \"all\"")
        if not isinstance(values, dict) or not values:
            raise _error(f"{what}: layer {layer} needs at least one of {', '.join(fields)}")
        row: dict[str, float] = {}
        for name, number in values.items():
            if name not in fields:
                raise _error(f"{what}: layer {layer} has no field {name!r}; the fields are {', '.join(fields)}")
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                raise _error(f"{what}: layer {layer} {name} must be a number of millimetres")
            if number < 0 or (number == 0 and not allow_zero) or number > MAX_LENGTH_MM:
                low = "0" if allow_zero else "more than 0"
                raise _error(f"{what}: layer {layer} {name} must be {low} and at most {MAX_LENGTH_MM:g} mm")
            row[name] = round(float(number), 4)
        out[layer] = row
    return out


def parse(data: Any) -> dict[str, Any]:
    """The checked contents of a rules file; refuses one that is malformed."""
    if not isinstance(data, dict):
        raise _error("the file holds one JSON object")
    unknown = [key for key in data if key not in TOP_KEYS]
    if unknown:
        raise _error(f"unknown key {unknown[0]!r}; the keys are {', '.join(TOP_KEYS)}")
    out: dict[str, Any] = {"net_classes": [], "differential_pairs": [], "clearances": []}
    for key in TOP_KEYS:
        items = data.get(key, [])
        if not isinstance(items, list):
            raise _error(f"{key} is a list")
        if len(items) > MAX_ITEMS:
            raise _error(f"{key} has more than {MAX_ITEMS} items")
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise _error(f"{key}[{index}] is an object")
    seen: set[str] = set()
    for index, item in enumerate(data.get("net_classes", [])):
        what = f"net_classes[{index}]"
        name = _name(item.get("name"), f"{what}.name")
        if name in (ALL_CLASSES,):
            raise _error(f"{what}: {ALL_CLASSES} is not a net class")
        if name in seen:
            raise _error(f"net class {name} is named twice")
        seen.add(name)
        extra = [key for key in item if key not in ("name", "nets", "layers")]
        if extra:
            raise _error(f"{what} has no key {extra[0]!r}")
        nets = item.get("nets", [])
        if not isinstance(nets, list) or not all(isinstance(n, str) and n for n in nets):
            raise _error(f"{what}.nets is a list of net names")
        out["net_classes"].append({"name": name, "nets": list(dict.fromkeys(nets)),
                                   "layers": _layers(item.get("layers"), WIDTH_FIELDS, f"net class {name}", False)})
    for index, item in enumerate(data.get("differential_pairs", [])):
        nets = item.get("nets")
        if (not isinstance(nets, list) or len(nets) != 2 or not all(isinstance(n, str) and n for n in nets)
                or nets[0] == nets[1]):
            raise _error(f"differential_pairs[{index}].nets is two different net names")
        extra = [key for key in item if key != "nets"]
        if extra:
            raise _error(f"differential_pairs[{index}] has no key {extra[0]!r}")
        out["differential_pairs"].append(sorted(nets))
    rules: set[str] = set()
    for index, item in enumerate(data.get("clearances", [])):
        what = f"clearances[{index}]"
        rule = _name(item.get("rule"), f"{what}.rule")
        if rule in rules:
            raise _error(f"clearance rule {rule} is named twice")
        rules.add(rule)
        extra = [key for key in item if key not in ("rule", "layers", "classes")]
        if extra:
            raise _error(f"{what} has no key {extra[0]!r}")
        classes = item.get("classes", [])
        if not isinstance(classes, list) or not all(
                isinstance(p, list) and len(p) == 2 and all(isinstance(c, str) and c for c in p) for p in classes):
            raise _error(f"{what}.classes is a list of [from class, to class] pairs")
        out["clearances"].append({"rule": rule, "classes": [list(p) for p in classes],
                                  "layers": _layers(item.get("layers"), CLEARANCE_FIELDS, f"clearance rule {rule}", True)})
    if not any(out[key] for key in TOP_KEYS):
        raise _error("it names no net class, differential pair or clearance rule")
    return out


def _expand(layers: dict[str, dict[str, float]], board_layers: list[str], what: str, blocked: list[str]) -> dict[str, dict[str, float]]:
    """``all`` spread over every conductor layer, then the named layers on top of it."""
    out: dict[str, dict[str, float]] = {}
    for layer in board_layers:
        if "all" in layers:
            out[layer] = dict(layers["all"])
    for layer, values in layers.items():
        if layer == "all":
            continue
        if layer not in board_layers:
            blocked.append(f"{what}: the board has conductor layers 1 to {len(board_layers)}; there is no layer {layer}")
            continue
        out.setdefault(layer, {}).update(values)
    return out


def class_path(name: str) -> str:
    return f"/constraints/net-classes/{name}"


def plan(rules: dict[str, Any], current: Current) -> Plan:
    """The changes ``rules`` (from ``parse``) make to ``current``; what stands in the way goes to
    ``blocked`` and nothing is applied."""
    p = Plan()
    known_nets = set(current.nets)
    file_classes = {item["name"] for item in rules["net_classes"]}
    all_classes = set(current.net_classes) | file_classes
    claimed: dict[str, str] = {}
    for item in rules["net_classes"]:
        name = item["name"]
        exists = name in current.net_classes
        if not exists:
            p.new_classes.append(name)
            p.changes.append({"path": class_path(name), "field": "exists", "before": False, "after": True})
        for net in item["nets"]:
            if net not in known_nets:
                p.blocked.append(f"net class {name}: the board has no net {net}")
                continue
            if net in claimed and claimed[net] != name:
                p.blocked.append(f"net {net} is named under both {claimed[net]} and {name}")
                continue
            claimed[net] = name
            if current.net_class_of.get(net) != name:
                p.moves[net] = name
                p.changes.append({"path": f"/constraints/nets/{net}", "field": "net_class",
                                  "before": current.net_class_of.get(net), "after": name})
        wanted = _expand(item["layers"], current.layers, f"net class {name}", p.blocked)
        now_layers = current.net_classes.get(name) or current.net_classes.get(DEFAULT_CLASS, {})
        values: dict[str, dict[str, float]] = {}
        for layer, row in wanted.items():
            now = now_layers.get(layer, {})
            typ = row.get("width_mm")
            if typ is not None:
                # a width outside the class's limits moves the limit the file leaves out to the width
                if "min_width_mm" not in row and now.get("min_width_mm", 0.0) > typ + 1e-9:
                    row["min_width_mm"] = typ
                if "max_width_mm" not in row and now.get("max_width_mm", typ) + 1e-9 < typ:
                    row["max_width_mm"] = typ
            merged = {**now, **row}
            lo, typ, hi = merged.get("min_width_mm"), merged.get("width_mm"), merged.get("max_width_mm")
            if lo is not None and typ is not None and lo > typ + 1e-9:
                p.blocked.append(f"net class {name} layer {layer}: min_width_mm {lo:g} is more than width_mm {typ:g}")
            if hi is not None and typ is not None and hi + 1e-9 < typ:
                p.blocked.append(f"net class {name} layer {layer}: max_width_mm {hi:g} is less than width_mm {typ:g}")
            for fname, after in row.items():
                before = now.get(fname) if exists else None
                if exists and before is not None and abs(before - after) < 1e-6:
                    continue
                values.setdefault(layer, {})[fname] = after
                p.changes.append({"path": f"{class_path(name)}/layers/{layer}", "field": fname, "before": before, "after": after})
        if values:
            p.class_values[name] = values
    existing_pairs = {pair_name(pair) for pair in current.differential_pairs}
    paired = {net: pair_name(pair) for pair in current.differential_pairs for net in pair}
    for nets in rules["differential_pairs"]:
        name = pair_name(nets)
        missing = [n for n in nets if n not in known_nets]
        if missing:
            p.blocked.append(f"differential pair {name}: the board has no net {missing[0]}")
            continue
        if name in existing_pairs:
            continue
        taken = [n for n in nets if n in paired]
        if taken:
            p.blocked.append(f"differential pair {name}: {taken[0]} is in the pair {paired[taken[0]]} already")
            continue
        for n in nets:
            paired[n] = name
        existing_pairs.add(name)
        p.new_pairs.append(sorted(nets))
        p.changes.append({"path": f"/constraints/differential-pairs/{name}", "field": "exists", "before": False, "after": True})
    for item in rules["clearances"]:
        rule = item["rule"]
        exists = rule in current.clearances
        if not exists:
            p.new_rules.append(rule)
            p.changes.append({"path": f"/constraints/clearance-rules/{rule}", "field": "exists", "before": False, "after": True})
        wanted = _expand(item["layers"], current.layers, f"clearance rule {rule}", p.blocked)
        now_layers = current.clearances.get(rule) or current.clearances.get(DEFAULT_RULE, {})
        values: dict[str, dict[str, float]] = {}
        for layer, row in wanted.items():
            now = now_layers.get(layer, {})
            for fname, after in row.items():
                before = now.get(fname) if exists else None
                if exists and before is not None and abs(before - after) < 1e-6:
                    continue
                values.setdefault(layer, {})[fname] = after
                p.changes.append({"path": f"/constraints/clearance-rules/{rule}/layers/{layer}", "field": fname,
                                  "before": before, "after": after})
        if values:
            p.rule_values[rule] = values
        for src, dst in item["classes"]:
            unknown = [c for c in (src, dst) if c != ALL_CLASSES and c not in all_classes]
            if unknown:
                p.blocked.append(f"clearance rule {rule}: there is no net class {unknown[0]}")
                continue
            key = (src, dst)
            before = current.class_clearances.get(key) or current.class_clearances.get((dst, src))
            if before == rule:
                continue
            p.class_rules[key] = rule
            p.changes.append({"path": f"/constraints/class-clearances/{src}/{dst}", "field": "rule", "before": before, "after": rule})
    return p
