"""The board's constraints: ``constraint get`` and ``constraint import``.

Layout's own automation reads the rules but cannot change them. Constraint Manager's automation
server ``ConstraintsAuto``, an in-process COM server, loads the board's side of the project's
constraints (``DesignContext`` 2, the layout context), changes them, and writes them into the
project database when it unloads, before any ``doc save``. ``ProjectIntegration.LoadCES`` then
makes Layout load them (Layout Automation Reference, Project Integration). So an import reports
``persistence: saved`` and is not one of the board's unsaved changes (DCLI-058).

The board's side alone is changed: the schematic's side keeps its rules, and a later
``SynchCES`` carries over only what changed on the schematic's side. An import is refused while
the board's side holds changes Layout has not loaded yet, since ``LoadCES`` would load them too.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .._driver import AdapterError
from . import base
from . import layout as L
from .base import Context
from .layout_board import project_of
from .layout_output import stackup_impedance, write_output
from .plan import PlanError
from .plan import project_file
from .plan import rules as R
from .platform import com, env

CES_PROGID = "ConstraintsAuto"
CONTEXT_LAYOUT = 2  # DesignParams.DesignContext: 1 is the schematic's side, 2 the board's
NETS_DISPID = 1610743811  # IDesign.Nets([in, optional] long p_nNetMask = 1)
NET_MASK = 1 | 4  # NM_Nets | NM_PowerNets: the default mask leaves out GND and the supplies
CONSTRAINT_MASK = 7
CLASS_CLEARANCE_STANDARD = 0  # EClassClearanceType CCT_Standard
MASTER = "(Master)"
LOAD_STATES = {1: "required", 2: "in_sync", 3: "no_ces"}  # EPrjIntStatus
CONSTRAINTS_KEY = "/constraints"
SUMMARY_CLASSES = 30


def _rules_error(exc: PlanError) -> AdapterError:
    return AdapterError(exc.code, exc.message, dispatch_state="not_started", **exc.details)


def _items(obj: Any) -> list[Any]:
    if obj is None:
        return []
    count = int(obj.Count)
    return [obj.Item(index) for index in range(1, count + 1)]


def _name(obj: Any) -> str:
    try:
        return str(obj.Name or "") if obj is not None else ""
    except Exception:  # noqa: BLE001 - an object without a name
        return ""


def board_project(path: str) -> tuple[str, str]:
    """The project file of the board ``path`` and the name of its board design there."""
    prj = project_of(path)
    if prj is None:
        raise AdapterError("E_NOT_FOUND", f"No project file beside {path} names this board; constraint commands need the "
                           "board's project.", dispatch_state="not_started")
    text = Path(prj).read_text(encoding="utf-8", errors="replace")
    for design in project_file.designs(text):
        if design.pcb_path and L.same_path(project_file.local_path(Path(prj).parent, design.pcb_path), path):
            return prj, design.name
    design = project_file.board_design(project_file.designs(text))
    if design is None:
        raise AdapterError("E_NOT_FOUND", f"{prj} names no board design.", dispatch_state="not_started")
    return prj, design.name


@contextmanager
def ces_design(prj: str, design_name: str) -> Iterator[Any]:
    """Constraint Manager's automation with the board's side of ``design_name`` loaded; unloading
    it writes what changed into the project database."""
    env.configure_environment()
    try:
        auto = com._client().Dispatch(CES_PROGID)
        design = auto.Design
        params = design.CreateDesignParams()
        params.ProjectFile = prj
        params.Board = design_name
        params.DesignContext = CONTEXT_LAYOUT
        design.Load(params)
    except AdapterError:
        raise
    except Exception as exc:  # noqa: BLE001 - classified
        raise L.com_error(exc, "loading the board's constraints in Constraint Manager's automation", "not_started") from None
    try:
        yield design
    finally:
        try:
            design.UnLoad()
        except Exception:  # noqa: BLE001 - unloading a design that failed to change: nothing more to do
            pass


def _master(design: Any) -> Any:
    schemes = design.PhysicalRules.Schemes
    for scheme in _items(schemes):
        if _name(scheme) == MASTER:
            return scheme
    return schemes.Item(1)


def _layer_values(layers: Any, fields: dict[str, int]) -> dict[str, dict[str, float]]:
    """``{"1": {field: mm}}`` from a rule's layers, in stackup order (the first is layer 1)."""
    types = {number: name for name, number in fields.items()}
    out: dict[str, dict[str, float]] = {}
    for index, layer in enumerate(_items(layers), start=1):
        row: dict[str, float] = {}
        for constraint in _items(layer.GetConstraints(CONSTRAINT_MASK)):
            name = types.get(int(constraint.ConstraintType))
            if name and constraint.Value is not None:
                row[name] = R.mm(constraint.Value)
        out[str(index)] = dict(sorted(row.items()))
    return out


def _nets(design: Any) -> list[Any]:
    return _items(com.propget(design, NETS_DISPID, NET_MASK))


def read_current(design: Any, layer_count: int) -> R.Current:
    scheme = _master(design)
    nets = _nets(design)
    net_class_of: dict[str, str] = {}
    for net in nets:
        try:
            net_class_of[_name(net)] = _name(net.NetClass) or R.DEFAULT_CLASS
        except Exception:  # noqa: BLE001 - a net without a class sits in the default class
            net_class_of[_name(net)] = R.DEFAULT_CLASS
    classes: dict[str, dict[str, dict[str, float]]] = {}
    for cls in _items(design.NetClasses):
        name = _name(cls)
        classes[name] = _layer_values(scheme.NetClasses.Item(name).Layers, R.WIDTH_FIELDS)
    pairs = [sorted([_name(pair.FirstNet), _name(pair.SecondNet)]) for pair in _items(design.DifferentialPairs)]
    clearances = {_name(rule): _layer_values(rule.Layers, R.CLEARANCE_FIELDS) for rule in _items(scheme.ClearanceRules)}
    class_clearances: dict[tuple[str, str], str] = {}
    for item in _items(scheme.ClassClearances):
        src = _name(item.SourceNetClass) or R.ALL_CLASSES
        dst = _name(item.TargetNetClass) or R.ALL_CLASSES
        class_clearances[(src, dst)] = _name(item.ClearanceRule)
    layers = [str(n) for n in range(1, layer_count + 1)]
    return R.Current(layers=layers, nets=sorted(net_class_of), net_class_of=net_class_of, net_classes=classes,
                     differential_pairs=pairs, clearances=clearances, class_clearances=class_clearances)


def _set_values(layers: Any, values: dict[str, dict[str, float]], fields: dict[str, int]) -> None:
    by_layer = {str(index): layer for index, layer in enumerate(_items(layers), start=1)}
    for layer_name, row in values.items():
        layer = by_layer[layer_name]
        constraints = layer.GetConstraints(CONSTRAINT_MASK)
        present = {int(c.ConstraintType): c for c in _items(constraints)}
        for field_name, value in row.items():
            number = fields[field_name]
            if number in present:
                present[number].Value = R.th(value)
            else:
                constraints.AddByType(number, R.th(value))


def apply_plan(design: Any, plan: R.Plan) -> list[str]:
    """Make the changes of ``plan``; returns the steps done, for a failure part way."""
    done: list[str] = []
    scheme = _master(design)
    nets = {_name(net): net for net in _nets(design)}
    for name in plan.new_classes:
        design.NetClasses.Add(name)
        done.append(f"net class {name} created")
    for net, cls in plan.moves.items():
        design.NetClasses.Item(cls).AssignNet(nets[net])
        done.append(f"{net} moved to {cls}")
    for cls, values in plan.class_values.items():
        _set_values(scheme.NetClasses.Item(cls).Layers, values, R.WIDTH_FIELDS)
        done.append(f"widths of {cls}")
    for first, second in plan.new_pairs:
        design.DifferentialPairs.Add(nets[first], nets[second])
        done.append(f"differential pair {R.pair_name([first, second])}")
    for rule in plan.new_rules:
        design.ClearanceRules.Add(rule)
        done.append(f"clearance rule {rule} created")
    for rule, values in plan.rule_values.items():
        _set_values(scheme.ClearanceRules.Item(rule).Layers, values, R.CLEARANCE_FIELDS)
        done.append(f"clearances of {rule}")
    if plan.class_rules:
        existing = {(_name(i.SourceNetClass) or R.ALL_CLASSES, _name(i.TargetNetClass) or R.ALL_CLASSES): i
                    for i in _items(scheme.ClassClearances)}
        for (src, dst), rule in plan.class_rules.items():
            rule_obj = design.ClearanceRules.Item(rule)
            item = existing.get((src, dst)) or existing.get((dst, src))
            if item is not None:
                item.ClearanceRule = rule_obj
            else:
                source = None if src == R.ALL_CLASSES else design.NetClasses.Item(src)
                target = None if dst == R.ALL_CLASSES else design.NetClasses.Item(dst)
                design.PhysicalRules.ClassClearances.Add(CLASS_CLEARANCE_STANDARD, f"{src} - {dst}", scheme, source, target, rule_obj)
            done.append(f"{src} to {dst} uses {rule}")
    return done


# ------------------------------------------------------------------ Layout's side


def load_state(doc: Any) -> str:
    """Whether Layout has loaded the board's side of the constraints: in_sync, required or no_ces."""
    integration = com.member(doc, "ProjectIntegration")
    if integration is None:
        raise AdapterError("E_BACKEND_UNAVAILABLE", "This Layout offers no ProjectIntegration object.", dispatch_state="not_started")
    value = com.member(integration, "LoadCESStatus")
    try:
        return LOAD_STATES.get(int(value), f"status {value}")
    except (TypeError, ValueError):
        return f"status {value}"


def load_into_layout(doc: Any) -> bool:
    return bool(com.member(com.member(doc, "ProjectIntegration"), "LoadCES"))


def _layer_count(doc: Any) -> int:
    return int(doc.LayerCount)


def _summary(current: R.Current, state: str) -> dict[str, Any]:
    members: dict[str, int] = {}
    for cls in current.net_class_of.values():
        members[cls] = members.get(cls, 0) + 1
    classes = [{"name": name, "nets": members.get(name, 0), "layers": layers}
               for name, layers in list(current.net_classes.items())[:SUMMARY_CLASSES]]
    return {"layers": len(current.layers), "nets": len(current.nets), "net_class_count": len(current.net_classes),
            "net_classes": classes, "differential_pairs": [R.pair_name(p) for p in current.differential_pairs],
            "clearance_rules": sorted(current.clearances),
            "class_clearances": [{"from": s, "to": d, "rule": r} for (s, d), r in sorted(current.class_clearances.items())],
            "loaded": state == "in_sync"}


def _add_impedance(doc: Any, summary: dict[str, Any]) -> None:
    """Each class's single-ended impedance for its width on each signal layer, or why there is none."""
    try:
        z0, layers, missing = stackup_impedance(doc)
        if z0 is None:
            summary["z0_unavailable"] = missing
            return
        for cls in summary["net_classes"]:
            cls["layers"] = {layer: {**row, "z0_ohm": round(z0(layer, row["width_mm"]), 1)} if layer in layers and "width_mm" in row
                             else dict(row) for layer, row in cls["layers"].items()}
    except Exception as exc:  # noqa: BLE001 - the rules are read; the impedance is extra
        summary["z0_unavailable"] = f"Layout did not compute impedance: {exc}"


class ConstraintGet(base.Handler):
    """The board's side of the constraints, read with Constraint Manager's automation."""

    command = "constraint get"

    def read(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        output = ctx.inputs.get("output")
        prj, design_name = board_project(path)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            state = load_state(doc)
            with ces_design(prj, design_name) as design:
                current = read_current(design, _layer_count(doc))
            out = _summary(current, state)
            _add_impedance(doc, out)
            if output:
                out["output"] = write_output(str(output), current.as_file())
            return out

        return L.in_layout(ctx, work, budget_s=L.READ_BUDGET_S, what="reading the constraints")


def _rules_input(ctx: Context) -> dict[str, Any]:
    source = str(base.need_input(ctx, "file"))
    try:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    except OSError as exc:
        raise AdapterError("E_NOT_FOUND", f"The rules file {source} cannot be read: {exc.strerror}.", dispatch_state="not_started") from None
    except ValueError as exc:
        raise AdapterError("E_VALIDATION", f"The rules file {source} is not JSON: {exc}.", dispatch_state="not_started") from None
    try:
        return R.parse(data)
    except PlanError as exc:
        raise _rules_error(exc) from None


def _plan(doc: Any, prj: str, design_name: str, rules: dict[str, Any]) -> tuple[R.Plan, str, list[dict[str, Any]]]:
    state = load_state(doc)
    with ces_design(prj, design_name) as design:
        current = read_current(design, _layer_count(doc))
    plan = R.plan(rules, current)
    impedance: list[dict[str, Any]] = []
    if any(item.get("impedance") for item in rules["net_classes"]):
        z0, layers, missing = stackup_impedance(doc)
        if z0 is None:
            plan.blocked.append(f"the rules file names impedance targets, but {missing}")
        else:
            impedance = R.impedance(rules, current, plan, z0, layers)
    if state == "required":
        plan.blocked.append("the board's side of the constraints holds changes Layout has not loaded yet; LoadCES would load "
                            "them together with this import, so load them in Layout first (Setup > Project Integration)")
    elif state == "no_ces":
        plan.blocked.append("this board's design flow does not use Constraint Manager, so its rules cannot be changed this way")
    return plan, state, impedance


def _impedance_text(rows: list[dict[str, Any]]) -> str:
    text = ", ".join(f"{row['net_class']} layer {row['layer']} {row['z0_ohm']:g} ohm" for row in rows[:12])
    return text + (" ..." if len(rows) > 12 else "")


def _changes(path: str, plan: R.Plan, state: str) -> list[dict[str, Any]]:
    changes = [base.change(base.key(path, _domain(c["path"])), c["field"], c["before"], c["after"]) for c in plan.changes]
    if changes:
        changes.append(base.change(base.key(path, CONSTRAINTS_KEY), "loaded", state == "in_sync", True))
    return changes


def _domain(rules_path: str) -> str:
    parts = rules_path.strip("/").split("/")
    return "/" + "/".join(L.segment(part) for part in parts)


class ConstraintImport(base.Handler):
    """Create or change the net classes, widths, differential spacing, differential pairs and
    clearance rules a rules file names, then make Layout load them (``LoadCES``)."""

    command = "constraint import"
    long_job = True

    def preview(self, ctx: Context) -> dict[str, Any]:
        rules = _rules_input(ctx)
        path = ctx.document_path()
        prj, design_name = board_project(path)
        plan, state, impedance = L.in_layout(ctx, lambda app, doc: _plan(doc, prj, design_name, rules), budget_s=L.READ_BUDGET_S,
                                             what="reading the constraints")
        target = [base.target(path, "constraint", CONSTRAINTS_KEY, label="the board's constraints")]
        held = f" Every impedance target holds: {_impedance_text(impedance)}." if impedance and not plan.blocked else ""
        if plan.empty() and not plan.blocked:
            return base.preview(f"The board's constraints already match the rules file; nothing changes.{held}", target, [])
        counts = (f"{len(plan.new_classes)} new net classes, {len(plan.moves)} nets moved, {len(plan.new_pairs)} new differential "
                  f"pairs, {len(plan.new_rules)} new clearance rules, {len(plan.changes)} changes in all")
        return base.preview(f"Change the board's side of the constraints as the rules file says ({counts}), write them into the "
                            f"project database, then load them into Layout. The schematic's side keeps its rules.{held}",
                            target, _changes(path, plan, state), blocked_by=plan.blocked or None)

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        rules = _rules_input(ctx)
        path = ctx.document_path()
        prj, design_name = board_project(path)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            plan, _state, impedance = _plan(doc, prj, design_name, rules)
            if plan.blocked:
                raise base.blocked("; ".join(plan.blocked))
            if plan.empty():
                return base.facts("not_applied", "unchanged", result={"changes": 0, "loaded": True, "impedance": impedance})
            done: list[str] = []
            try:
                with ces_design(prj, design_name) as design:
                    done = apply_plan(design, plan)
            except AdapterError:
                raise
            except Exception as exc:  # noqa: BLE001 - classified
                return L.failed_step(exc, "changing the constraints", done)
            try:
                loaded = load_into_layout(doc)
            except Exception as exc:  # noqa: BLE001 - the rules are written; Layout did not load them
                return {**L.failed_step(exc, "loading the constraints into Layout", done), "persistence": "saved"}
            return base.facts("applied", "saved", result={
                "changes": len(plan.changes), "net_classes_created": plan.new_classes, "nets_moved": len(plan.moves),
                "differential_pairs_created": [R.pair_name(p) for p in plan.new_pairs],
                "clearance_rules_created": plan.new_rules, "loaded": bool(loaded), "impedance": impedance})

        return L.in_layout(ctx, work, budget_s=L.job_budget(L.WRITE_BUDGET_S), write=True, what="importing the constraints")

    def read_back(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        targets = list(ctx.request.get("targets") or [])
        fields = list(ctx.request.get("fields") or [])
        prj, design_name = board_project(path)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            state = load_state(doc)
            with ces_design(prj, design_name) as design:
                current = read_current(design, _layer_count(doc))
            return read_keys(current, state, path, targets, fields)

        return L.in_layout(ctx, work, budget_s=L.READ_BUDGET_S, what="reading the constraints back")


def read_keys(current: R.Current, state: str, path: str, targets: list[str], fields: list[str]) -> dict[str, Any]:
    """The fields of the constraint target keys, as the import's preview names them."""
    observed: dict[str, dict[str, Any]] = {}
    unreadable: set[str] = set()
    pairs = {R.pair_name(p) for p in current.differential_pairs}
    for target in targets:
        parts = L.fragment(target, path)
        values: dict[str, Any] | None = None
        if parts and parts[0] == "constraints":
            rest = parts[1:]
            if not rest:
                values = {"loaded": state == "in_sync"}
            elif rest[0] == "net-classes" and len(rest) == 2:
                values = {"exists": rest[1] in current.net_classes}
            elif rest[0] == "net-classes" and len(rest) == 4 and rest[2] == "layers":
                values = dict(current.net_classes.get(rest[1], {}).get(rest[3], {}))
            elif rest[0] == "nets" and len(rest) == 2:
                values = {"net_class": current.net_class_of.get(rest[1])}
            elif rest[0] == "differential-pairs" and len(rest) == 2:
                values = {"exists": rest[1] in pairs}
            elif rest[0] == "clearance-rules" and len(rest) == 2:
                values = {"exists": rest[1] in current.clearances}
            elif rest[0] == "clearance-rules" and len(rest) == 4 and rest[2] == "layers":
                values = dict(current.clearances.get(rest[1], {}).get(rest[3], {}))
            elif rest[0] == "class-clearances" and len(rest) == 3:
                key = (rest[1], rest[2])
                values = {"rule": current.class_clearances.get(key) or current.class_clearances.get((rest[2], rest[1]))}
        if values is None:
            unreadable.update(fields)
            continue
        observed[target] = {name: values[name] for name in fields if name in values}
    return {"observed": observed, "unreadable": sorted(unreadable)}


HANDLERS: list[base.Handler] = [ConstraintGet(), ConstraintImport()]
