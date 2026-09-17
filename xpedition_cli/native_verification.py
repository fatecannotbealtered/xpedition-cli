"""Verify requested NativeBackend postconditions against an observed snapshot.

This is NOT a durability test or an ERC/DRC substitute. It checks the final
state of affected objects, so successive moves/properties are not mistaken for
failed intermediate states. Missing observations and unsupported operations
are never reported as verified.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

# Serialization round-off in native coordinates, not a manufacturing tolerance.
_COORDINATE_ABS_TOLERANCE = 1e-6
_MISSING = object()


def _at(value: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _same(expected: Any, observed: Any, *, coordinate: bool = False) -> bool:
    if observed is _MISSING:
        return False
    if coordinate:
        if isinstance(expected, bool) or isinstance(observed, bool):
            return False
        if not isinstance(expected, (int, float)) or not isinstance(observed, (int, float)):
            return False
        return (
            math.isfinite(expected)
            and math.isfinite(observed)
            and math.isclose(expected, observed, rel_tol=0.0, abs_tol=_COORDINATE_ABS_TOLERANCE)
        )
    if isinstance(expected, dict):
        return isinstance(observed, Mapping) and all(
            _same(item, observed.get(key, _MISSING)) for key, item in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(observed, list)
            and len(expected) == len(observed)
            and all(_same(left, right) for left, right in zip(expected, observed, strict=True))
        )
    return type(expected) is type(observed) and expected == observed


def _index(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict) and row.get(key) is not None:
            result[str(row[key])].append(row)
    return result


def verify_native_changes(
    expected: dict[str, Any], observed: dict[str, Any], operations: list[dict[str, Any]]
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    targets: dict[tuple[str, str], set[tuple[str, ...]]] = defaultdict(set)
    net_fields: dict[str, set[str]] = defaultdict(set)
    connected_nets: set[str] = set()
    for index, operation in enumerate(operations):
        kind = operation["type"]
        if kind in {
            "place_component",
            "move_component",
            "delete_component",
            "set_property",
            "place_pcb_component",
            "move_pcb_component",
        }:
            section = (
                "pcb" if kind in {"place_pcb_component", "move_pcb_component"} else "schematic"
            )
            fields = targets[(section, str(operation["refdes"]))]
            if kind.startswith("move_"):
                fields.update({("x",), ("y",)})
            elif kind == "set_property":
                fields.add(("properties", str(operation["name"])))
            elif kind.startswith("place_"):
                mapping = (
                    {"part_number": "internal_part_no", "footprint": "package"}
                    if section == "schematic"
                    else {}
                )
                keys = (
                    (
                        "part_number",
                        "mpn",
                        "manufacturer",
                        "description",
                        "value",
                        "footprint",
                        "package",
                        "x",
                        "y",
                        "properties",
                        "pins",
                    )
                    if section == "schematic"
                    else ("footprint", "part_number", "x", "y", "rotation", "side")
                )
                fields.update((mapping.get(key, key),) for key in keys if key in operation)
        elif kind == "create_net":
            fields = net_fields[str(operation["name"])]
            if "net_type" in operation:
                fields.add("type")
            if "class" in operation:
                fields.add("class")
        elif kind == "connect":
            connected_nets.add(str(operation["net"]))
        else:
            issues.append({"kind": "unverified_operation", "index": index, "operation": kind})

    for section in ("schematic", "pcb"):
        desired = expected if section == "schematic" else expected.get("pcb", {})
        actual = observed if section == "schematic" else observed.get("pcb", {})
        desired_index = _index(desired.get("components", []), "refdes")
        actual_index = _index(actual.get("components", []), "refdes")
        for (domain, refdes), fields in targets.items():
            if domain != section:
                continue
            want, got = desired_index.get(refdes, []), actual_index.get(refdes, [])
            if not want:
                if got:
                    issues.append(
                        {"kind": "component_not_deleted", "domain": domain, "refdes": refdes}
                    )
                if domain == "schematic" and any(
                    str(pin).startswith(refdes + ".")
                    for connection in observed.get("connections", [])
                    for pin in connection.get("pins", [])
                ):
                    issues.append({"kind": "deleted_component_still_connected", "refdes": refdes})
                continue
            if len(want) != 1 or len(got) != 1:
                issues.append(
                    {
                        "kind": "component_count_mismatch",
                        "domain": domain,
                        "refdes": refdes,
                        "expected": len(want),
                        "observed": len(got),
                    }
                )
                continue
            for path in sorted(fields):
                expected_value, observed_value = _at(want[0], path), _at(got[0], path)
                if expected_value is _MISSING:
                    continue  # An earlier operation was superseded by delete/re-place.
                if not _same(
                    expected_value,
                    observed_value,
                    coordinate=path in {("x",), ("y",), ("rotation",)},
                ):
                    issues.append(
                        {
                            "kind": "field_mismatch",
                            "domain": domain,
                            "refdes": refdes,
                            "field": ".".join(path),
                            "expected": expected_value,
                            "observed": None if observed_value is _MISSING else observed_value,
                            "observed_present": observed_value is not _MISSING,
                        }
                    )

    desired_nets = _index(expected.get("nets", []), "name")
    actual_nets = _index(observed.get("nets", []), "name")
    for name, fields in net_fields.items():
        want, got = desired_nets.get(name, []), actual_nets.get(name, [])
        if len(want) != 1 or len(got) != 1:
            issues.append(
                {
                    "kind": "net_count_mismatch",
                    "net": name,
                    "expected": len(want),
                    "observed": len(got),
                }
            )
            continue
        for key in sorted(fields):
            if not _same(want[0].get(key, _MISSING), got[0].get(key, _MISSING)):
                issues.append({"kind": "net_field_mismatch", "net": name, "field": key})

    def pins_by_net(project: dict[str, Any]) -> dict[str, set[str]]:
        result: dict[str, set[str]] = defaultdict(set)
        for connection in project.get("connections", []):
            result[str(connection.get("net"))].update(
                str(pin) for pin in connection.get("pins", [])
            )
        return result

    desired_pins, actual_pins = pins_by_net(expected), pins_by_net(observed)
    for name in sorted(connected_nets):
        want, got = desired_pins.get(name, set()), actual_pins.get(name, set())
        if want != got or len(actual_nets.get(name, [])) != 1:
            issues.append(
                {
                    "kind": "connectivity_mismatch",
                    "net": name,
                    "missing_pins": sorted(want - got),
                    "unexpected_pins": sorted(got - want),
                }
            )
        other_nets = sorted(net for net, pins in actual_pins.items() if net != name and want & pins)
        if other_nets:
            issues.append({"kind": "pins_on_other_nets", "net": name, "other_nets": other_nets})
    return {
        "valid": not issues,
        "status": "verified" if not issues else "failed",
        "scope": "requested_postconditions",
        "checked_operations": len(operations),
        "component_count": len(observed.get("components", [])),
        "net_count": len(observed.get("nets", [])),
        "issues": issues,
        "_untrusted": ["issues"],
    }
