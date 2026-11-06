"""The board commands that read the open board, check it, or write files from it: ``pcb get``,
``pcb check`` and ``pcb export``.

The board model (``board_model``) is what ``plan.board_metrics`` measures and checks: the
outline, the placed parts with their extents and pins, traces and vias, and for the checks the
pads, holes and silkscreen too, all in mm. Positions read through a getter that takes a unit
(``GetPositionX(4)``, ``Extrema``) come in mm whatever the board's unit; raw geometry
(``PointsArray``, ``CircleX``, ``LineWidth``) comes in the board's unit, which ``geometry_scale``
measures on the board outline. Without an outline on a board that is not in mils, the geometry and
metrics say they are unavailable; the unit is not changed, since that would change the document
in a read.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from .._driver.targets import path_of
from . import base
from . import layout as L
from .base import Context
from .plan import board_metrics, fab_package
from .platform import com, dialogs

STACKUP_DISPID = 157  # Document.LayerStack([in] VARIANT_BOOL bIncludeInsulationLayer)
NET_CLASSES_DISPID = 24  # Document.NetClasses([in] BSTR sNetClassName)
OBSTRUCTS_DISPID = 41  # Document.Obstructs(eSelectType, eSelectOnly, nLayerOnly)
PLACEMENT_OBSTRUCTS_DISPID = 40  # Document.PlacementObstructs(eSelectOnly, nLayerOnly)
THICKNESS_DISPID = 1  # LayerProperties.Thickness([in] EPcbUnit)
NET_CLASS_WIDTHS = {"minimum": 7, "typical": 8, "expansion": 9}  # NetClass.<kind>TraceWidth(layer, scheme, unit)
MASTER_SCHEME = "(Master)"
LAYER_KINDS = {0: "conductor", 1: "fabrication", 2: "user", 3: "insulation"}  # EPcbLayerType
LAYER_USAGES = {1: "signal", 2: "plane", 3: "split_mixed", 4: "flooded_signal", 5: "plating"}
FAB_SILKSCREEN = 2  # FabricationLayerGfx/Text.Type: 1 assembly, 2 silkscreen
TEXT_KINDS = {1: "refdes", 4: "part_number"}  # FabricationLayerText.TextType
SIDE_BOTTOM = 512  # EPcbSide: 1 top, 512 bottom
LISTED = 25  # list entries a result carries; the counts stay whole
UNIT_NOTE = ("The unit of the board's raw geometry could not be measured: the board has no outline and does not count in mils. "
             "Draw the outline with pcb update, then read again.")


# ------------------------------------------------------------------ the board model


def _side(obj: Any) -> str:
    try:
        side = int(obj.Side)
    except Exception:  # noqa: BLE001 - no side: the top
        return "top"
    return "bottom" if side == SIDE_BOTTOM else "top"


def _side_name(obj: Any) -> str:
    try:
        name = str(obj.SideName or "").lower()
    except Exception:  # noqa: BLE001 - the side number instead
        name = ""
    return name if name in ("top", "bottom") else _side(obj)


def geometry_record(geometry: Any, scale: float, **extra: Any) -> dict[str, Any] | None:
    """A geometry as plain data: a ``circle`` (x, y, r) or a ``path`` of (x, y, r) rows, in mm."""
    try:
        if bool(geometry.IsCircle()):
            circle = [round(float(geometry.CircleX) * scale, 4), round(float(geometry.CircleY) * scale, 4),
                      round(float(geometry.CircleR) * scale, 4)]
            return {"circle": circle, **extra}
        rows = geometry.PointsArray
    except Exception:  # noqa: BLE001 - a geometry that cannot be read is left out
        return None
    if not rows or len(rows) < 3:
        return None
    path = [[round(float(x) * scale, 4), round(float(y) * scale, 4), round(float(r) * scale, 4)]
            for x, y, r in zip(rows[0], rows[1], rows[2], strict=False)]
    return {"path": path, **extra}


def _position(obj: Any) -> tuple[float, float]:
    return round(float(obj.GetPositionX(L.UNIT_MM)), 4), round(float(obj.GetPositionY(L.UNIT_MM)), 4)


def _component(component: Any, model: dict[str, Any], scale: float, full: bool) -> dict[str, Any]:
    refdes = str(component.RefDes or "")
    placed = bool(component.Placed)
    row: dict[str, Any] = {"refdes": refdes, "placed": placed, "cell": str(component.CellName or ""), "side": _side(component)}
    if not placed:
        return row  # an unplaced part's pins and texts stand at its cell's own coordinates
    row["extents"] = [round(value, 4) for value in L.extrema_mm(component)]
    row["x"], row["y"] = _position(component)
    row["rotation"] = round(float(component.Orientation or 0.0) % 360.0, 4)
    pins: list[dict[str, Any]] = []
    for pin in L.collection(component, "Pins"):
        name, net = L.name_of(pin), L.net_name(pin)
        x, y = _position(pin)
        pins.append({"pin": name, "net": net, "x": x, "y": y})
        if not full:
            continue
        for pad in L.collection(pin, "Pads"):
            layer = int(pad.Layer)
            for geometry in L.collection(pad, "Geometries"):
                record = geometry_record(geometry, scale, layer=layer, net=net, refdes=refdes, pin=name, kind="pad")
                if record:
                    model["pads"].append(record)
        for hole in L.collection(pin, "Holes"):
            size = float(hole.GetDrillSize(L.UNIT_MM))
            if size > 0:
                model["holes"].append([x, y, round(size, 4)])
    row["pins"] = pins
    return row


def _owner(item: Any) -> tuple[str | None, bool]:
    """Whose a silkscreen item is (None: the board's) and whether it is on the board."""
    component = item.Component
    if component is None:
        return None, True
    return str(component.RefDes), bool(component.Placed)


def _silkscreen(doc: Any, model: dict[str, Any], scale: float) -> None:
    for gfx in L.collection(doc, "FabricationLayerGfxs"):
        try:
            if int(gfx.Type) != FAB_SILKSCREEN:
                continue
            owner, on_board = _owner(gfx)
            if not on_board:
                continue
            geometry = gfx.Geometry
            width = 0.0 if bool(geometry.Filled) else (float(geometry.LineWidth or 0.0) * scale or 0.12)
            record = geometry_record(geometry, scale, side=_side_name(gfx), width=round(width, 4), kind="silk", refdes=owner)
            if record:
                model["silk"].append(record)
        except Exception:  # noqa: BLE001 - a graphic that cannot be read is left out
            continue
    for text in L.collection(doc, "FabricationLayerTexts"):
        try:
            if int(text.Type) != FAB_SILKSCREEN:
                continue
            owner, on_board = _owner(text)
            if not on_board:
                continue
            fmt = text.Format
            record: dict[str, Any] = {"text": str(text.TextString), "kind": TEXT_KINDS.get(int(text.TextType or 0), "text"),
                                      "side": _side_name(text), "refdes": owner, "height": round(float(fmt.Height or 0.0) * scale, 4),
                                      "pen": round(float(fmt.PenWidth or 0.0) * scale, 4)}
            record["extents"] = [round(value, 4) for value in L.extrema_mm(text)]
            model["texts"].append(record)
        except Exception:  # noqa: BLE001 - a text that cannot be read is left out
            continue


def board_model(doc: Any, *, full: bool, status: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The board as ``plan.board_metrics`` takes it, or None on a board that is not in mils.
    ``full`` adds the pads, holes and silkscreen the board rules need."""
    scale = L.geometry_scale(doc)
    if scale is None:
        return None
    status = L.route_status(doc) if status is None else status
    model: dict[str, Any] = {
        "layers": int(doc.LayerCount), "outline": [], "components": [], "pads": [], "vias": [], "traces": [], "holes": [],
        "silk": [], "texts": [], "connectivity": {"complete": True},
        "unrouted": [{"net": name, "opens": row["opens"]} for name, row in sorted(status.items()) if not row["routed"]],
    }
    outline = doc.BoardOutline
    record = geometry_record(outline.Geometry, scale, kind="outline") if outline is not None else None
    if record:
        model["outline"].append(record)
    for component in L.collection(doc, "Components"):
        model["components"].append(_component(component, model, scale, full))
    for via in L.collection(doc, "Vias"):
        x, y = _position(via)
        model["vias"].append({"circle": [x, y, 0.0], "net": L.net_name(via), "kind": "via"})
    for trace in L.collection(doc, "Traces"):
        geometry = trace.Geometry
        record = geometry_record(geometry, scale, layer=int(trace.Layer), net=L.net_name(trace),
                                 width=round(float(geometry.LineWidth or 0.0) * scale, 4), kind="trace")
        if record:
            model["traces"].append(record)
    if full:
        for hole_owner in L.collection(doc, "MountingHoles"):
            x, y = _position(hole_owner)
            for hole in L.collection(hole_owner, "Holes"):
                size = float(hole.GetDrillSize(L.UNIT_MM))
                if size > 0:
                    model["holes"].append([x, y, round(size, 4)])
        _silkscreen(doc, model, scale)
    return model


# ------------------------------------------------------------------ pcb get


def _quietly(fn: Any, default: Any = None) -> Any:
    """A part of the board's report that cannot be read is reported as None, not as an error."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - the rest of the report stands
        return default


def board_stackup(doc: Any) -> list[dict[str, Any]]:
    """Every layer of the stackup from the top, conductors and insulation alike."""
    layers: list[dict[str, Any]] = []
    for layer in L.items(com.propget(doc, STACKUP_DISPID, True)):
        kind = LAYER_KINDS.get(int(layer.Type), "other")
        row: dict[str, Any] = {"board_layer": int(layer.BoardLayer), "kind": kind}
        if kind == "conductor":
            row["layer"] = int(layer.ConductorLayer)
        properties = layer.LayerProperties
        if properties is not None:
            row["name"] = str(properties.StackupLayerName or "")
            row["description"] = str(properties.Description or "")
            row["thickness_mm"] = _quietly(lambda p=properties: round(float(com.propget(p, THICKNESS_DISPID, L.UNIT_MM)), 4))
            if kind == "conductor":
                row["usage"] = LAYER_USAGES.get(int(properties.LayerUsage or 0), "other")
            elif kind == "insulation":
                row["dielectric_constant"] = _quietly(lambda p=properties: float(p.DielectricConstant))
        layers.append(row)
    return layers


def net_classes(doc: Any, layer_count: int) -> list[dict[str, Any]]:
    """Each net class with its number of nets and its trace widths per layer (Master scheme), mm."""
    classes: list[dict[str, Any]] = []
    for net_class in L.items(com.propget(doc, NET_CLASSES_DISPID, "*")):
        widths: dict[str, dict[str, float]] = {}
        for layer in range(1, layer_count + 1):
            row = {f"{kind}_mm": _quietly(lambda d=dispid, n=layer: round(float(com.propget(net_class, d, n, MASTER_SCHEME, L.UNIT_MM)), 4))
                   for kind, dispid in NET_CLASS_WIDTHS.items()}
            widths[str(layer)] = {kind: value for kind, value in row.items() if value is not None}
        classes.append({"name": L.name_of(net_class), "nets": L.count(net_class, "Nets"), "widths": widths})
    return classes


def keepout_count(doc: Any) -> int:
    """The board's obstructs (route, via, plane... keepouts) and its placement keepouts."""
    return int(com.propget(doc, OBSTRUCTS_DISPID, -1, 0, 0).Count) + int(com.propget(doc, PLACEMENT_OBSTRUCTS_DISPID, 0, 0).Count)


def geometry_summary(model: dict[str, Any]) -> dict[str, Any]:
    """The geometry in short: the outline and how many objects the model holds. The whole model
    of a real board is far larger than one response may be."""
    counts = {name: len(model[name]) for name in ("components", "traces", "vias", "unrouted")}
    counts["placed"] = sum(1 for item in model["components"] if item.get("placed"))
    outline = model["outline"][0] if model["outline"] else {}
    return {"available": True, "outline_mm": outline.get("path") or outline.get("circle"), "counts": counts}


def metrics_summary(model: dict[str, Any]) -> dict[str, Any]:
    measured = board_metrics.measure(model)
    return {"available": True, "board": measured["board"], "summary": measured["summary"],
            "unplaced": measured["parts"]["unplaced"][:LISTED], "supply_nets": measured["ratsnest"]["supply_nets"][:LISTED],
            "trace_mm_by_layer": measured["routing"]["by_layer"]}


def board_facts(doc: Any, path: str) -> dict[str, Any]:
    layer_count = int(doc.LayerCount)
    components = L.collection(doc, "Components")
    placed = sum(1 for item in components if bool(item.Placed))
    status = _quietly(lambda: L.route_status(doc))
    nets: dict[str, Any] = {"total": L.count(doc, "Nets"), "routed": None, "open": None}
    if status is not None:
        still_open = sorted(name for name, row in status.items() if not row["routed"])
        nets.update(total=len(status), routed=len(status) - len(still_open), open=len(still_open))
    model = _quietly(lambda: board_model(doc, full=False, status=status)) if status is not None else None
    unavailable = {"available": False, "reason": UNIT_NOTE if L.geometry_scale(doc) is None else "The geometry could not be read."}
    return {
        "path": path, "layers": layer_count,
        "components": {"total": len(components), "placed": placed, "unplaced": len(components) - placed},
        "nets": nets, "traces": L.count(doc, "Traces"), "vias": L.count(doc, "Vias"),
        "outline": _quietly(lambda: L.outline_values(doc)), "stackup": _quietly(lambda: board_stackup(doc)),
        "net_classes": _quietly(lambda: net_classes(doc, layer_count)), "keepouts": _quietly(lambda: keepout_count(doc)),
        "geometry": geometry_summary(model) if model is not None else unavailable,
        "metrics": _quietly(lambda: metrics_summary(model), unavailable) if model is not None else unavailable,
    }


class BoardGet(base.Handler):
    """The open board in numbers: counts, stackup, net classes and widths, keepouts, a geometry
    summary and the layout metrics."""

    command = "pcb get"

    def read(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        output = ctx.inputs.get("output")

        def work(app: Any, doc: Any) -> dict[str, Any]:
            facts = board_facts(doc, path)
            if output:
                model = board_model(doc, full=True) if L.geometry_scale(doc) is not None else None
                full = {**facts, "model": model, "metrics_full": board_metrics.measure(model) if model else None}
                facts["output"] = write_output(str(output), full)
            return facts

        return L.in_layout(ctx, work, budget_s=L.READ_BUDGET_S, what="reading the board")


def write_output(path: str, data: dict[str, Any]) -> str:
    """Write the whole result to ``--output`` and return the path; the response keeps the summary (DCLI-008)."""
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1, default=str)
    return target


# ------------------------------------------------------------------ pcb check

BATCH_DRC_COMMAND = 32769  # Analysis > Batch DRC... (an MFC command id; Batch DRC has no automation call)
HAZARDS_BATCH = 2  # epcbHazardTypeBatch
HAZARD_X, HAZARD_Y, HAZARD_ACTUAL, HAZARD_REQUIRED = 9, 10, 16, 17  # IMGCPCBHazard property dispids, taking an EPcbUnit
HAZARD_OBJECTS = 6
DRC_WAIT_S = 18.0
DRC_QUIET_S = 6.0  # no Processing window this long after the command: the run is over
DRC_POLL_S = 0.5
WARNING_KINDS = frozenset({"ViasUnderParts"})  # a via under a part is normal with tented vias
RULE_SEVERITY = {"high": "error", "medium": "warning", "low": "info"}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
CHECK_LISTED = 10  # hazards and rules each; a longer result would pass the 8000-character response limit
DESCRIPTION_LIMIT = 160


def run_batch_drc(app: Any, pid: int | None) -> dict[str, Any]:
    """Batch DRC on the open board through its menu command; the caller answers its dialog
    (``DRC_PROMPTS``). Layout shows a Processing window while it checks; the run is over when the
    window has come and gone, or has not come within ``DRC_QUIET_S``."""
    started = time.monotonic()
    try:
        accepted = app.Gui.ProcessCommand(BATCH_DRC_COMMAND)
    except Exception as exc:  # noqa: BLE001 - classified
        raise L.com_error(exc, "starting Batch DRC", "not_started") from None
    if not accepted:
        raise AdapterError("E_NATIVE_FAILURE", "Layout did not accept the Batch DRC command.", dispatch_state="not_started")
    seen = False
    while True:
        elapsed = time.monotonic() - started
        try:
            busy = bool(dialogs.processing_windows(pid))
        except Exception:  # noqa: BLE001 - the windows cannot be read
            busy = False
        if busy:
            seen = True
        elif seen or elapsed >= DRC_QUIET_S:
            break
        if elapsed > L.job_budget(DRC_WAIT_S):
            raise AdapterError("E_TIMEOUT", f"Batch DRC was still running after {L.job_budget(DRC_WAIT_S):g} s, and Layout goes on with it. Wait until "
                               "the session is ready again (session get), then run pcb check again.", dispatch_state="not_started")
        time.sleep(DRC_POLL_S)
    return {"seconds": round(time.monotonic() - started, 1), "processing_seen": seen}


def _constants_table() -> Any:
    import win32com.client

    return win32com.client.constants


def hazard_names(app: Any) -> dict[int, str]:
    """Hazard type numbers to names (``PartialNets``, ``ViasUnderParts``...), from the type
    library's ``epcbHazardType*`` constants, which win32com fills once the library is loaded."""
    try:
        com.late_bound(app)
        table = _constants_table()
    except Exception:  # noqa: BLE001 - the numbers stand without names
        return {}
    names: dict[int, str] = {}
    for mapping in getattr(table, "__dicts__", None) or []:
        for key, value in mapping.items():
            if key.startswith("epcbHazardType") and isinstance(value, int):
                names.setdefault(value, key[len("epcbHazardType"):])
    return names


def _mm(owner: Any, dispid: int) -> float | None:
    try:
        return round(float(com.propget(owner, dispid, L.UNIT_MM)), 4)
    except Exception:  # noqa: BLE001 - not reported for this hazard
        return None


def _object_name(item: Any) -> str:
    """What a hazard names: ``R1.2`` for a pin, ``U1`` for a part's outline, else its net or kind."""
    try:
        kind = str(item._oleobj_.GetTypeInfo().GetDocumentation(-1)[0])
    except Exception:  # noqa: BLE001 - no type information
        kind = ""
    refdes = _quietly(lambda: str(item.Component.RefDes or ""), "")
    if refdes and "Pin" in kind:
        return f"{refdes}.{L.name_of(item)}"
    if refdes:
        return refdes
    return L.net_name(item) or kind.replace("IMGCPCB", "") or "object"


def hazard_record(hazard: Any, names: dict[int, str]) -> dict[str, Any]:
    kind = _quietly(lambda: int(hazard.Type))
    name = names.get(kind, f"type {kind}") if kind is not None else "unknown"
    actual, required = _mm(hazard, HAZARD_ACTUAL), _mm(hazard, HAZARD_REQUIRED)
    objects = _quietly(lambda: [_object_name(item) for item in L.items(com.member(hazard, "Objects"))[:HAZARD_OBJECTS]], [])
    return {"type": name, "severity": "warning" if name in WARNING_KINDS else "error",
            "description": _quietly(lambda: str(hazard.Description or "").strip()[:DESCRIPTION_LIMIT], ""), "x_mm": _mm(hazard, HAZARD_X),
            "y_mm": _mm(hazard, HAZARD_Y),
            "objects": objects, "actual_mm": actual if actual is None or actual >= 0 else None,
            "required_mm": required if required is None or required >= 0 else None}


def rule_findings(model: dict[str, Any] | None, reason: str = UNIT_NOTE) -> list[dict[str, Any]]:
    if model is None:
        return [{"id": "board_rules-1", "severity": "info", "rule": "board_rules_unavailable", "message": reason, "objects": []}]
    rows = board_metrics.board_rules(model)
    return [{"id": f"{row['rule']}-{index}", "severity": RULE_SEVERITY.get(row["severity"], "info"), "rule": row["rule"],
             "message": row["finding"], "objects": [str(v) for v in (row.get("refdes"), row.get("other"), row.get("net")) if v]}
            for index, row in enumerate(rows, 1)]


def check_board(app: Any, doc: Any, pid: int | None, output: str | None = None) -> dict[str, Any]:
    """Run Batch DRC and read its hazards and the board rules. The lists carry the first
    ``CHECK_LISTED`` entries, errors first, so that the result fits one response; the counts are whole."""
    drc = run_batch_drc(app, pid)
    try:
        found = L.items(doc.GetHazards(HAZARDS_BATCH))
        names = hazard_names(app) if found else {}  # loading the type library costs time; only for names
        hazards = [hazard_record(item, names) for item in found]
    except Exception as exc:  # noqa: BLE001 - a hazard list that cannot be read is not an empty one
        raise L.com_error(exc, "reading the Batch DRC hazards", "not_started") from None
    hazards.sort(key=lambda row: (row["severity"] != "error", row["type"]))
    if L.geometry_scale(doc) is None:
        rules = rule_findings(None)
    else:
        rules = rule_findings(_quietly(lambda: board_model(doc, full=True)), "The board geometry could not be read, so the board "
                              "rules were not checked; the hazards above stand.")
    rules.sort(key=lambda row: SEVERITY_ORDER.get(row["severity"], 3))
    severities = [row["severity"] for row in hazards] + [row["severity"] for row in rules]
    out = {"hazards": hazards[:CHECK_LISTED], "rules": rules[:CHECK_LISTED], "errors": severities.count("error"),
           "warnings": severities.count("warning"), "hazard_count": len(hazards), "rule_count": len(rules), "drc": drc}
    if output:
        out["output"] = write_output(output, {**out, "hazards": hazards, "rules": rules})
    return out


class BoardCheck(base.Handler):
    """Layout's Batch DRC on the open board, its hazards (``GetHazards``), and the board rules
    DRC does not cover (``plan.board_metrics.board_rules``). Not ``MGCPCBEngines.DRC``: that
    engine saves and closes the board."""

    command = "pcb check"
    answers = dialogs.DRC_PROMPTS

    def read(self, ctx: Context) -> dict[str, Any]:
        ref = ctx.need_session(base.LAYOUT)
        return L.in_layout(ctx, lambda app, doc: check_board(app, doc, ref.pid, ctx.inputs.get("output")),
                           budget_s=L.job_budget(L.READ_BUDGET_S), rules=dialogs.DRC_PROMPTS, what="running Batch DRC")


# ------------------------------------------------------------------ pcb export

OUTPUT_COMMANDS = (("ncdrill", 33018), ("odb", 33017), ("gerber", 33016))  # Output menu commands, in this order
OUTPUT_TIMEOUT_S = 300.0
OUTPUT_SETTLE_S = 2.0
OUTPUT_POLL_S = 1.0
OUTPUT_ROUNDS = 2  # a dialog can write its own settings back over a patched setup: patch and run once more
SETUPS = (("ODBSetup.ocf", fab_package.patch_odb_setup), ("ODBSetup.eocf", fab_package.patch_odb_setup),
          ("PlotSetup.gpf", fab_package.patch_gerber_setup), ("GerberPlot.egpf", fab_package.patch_gerber_setup))
BACKUP_SUFFIX = ".bak-xpedition-cli"
PACKAGE_FILES = ("manifest.json", "README.md", "centroid.csv", "bom.csv")
DIALOG_CLASS = "#32770"
WS_MINIMIZEBOX = 0x00020000


def _has_entries(folder: str) -> bool:
    with os.scandir(folder) as entries:
        return any(True for _ in entries)


def _output(ctx: Context) -> str:
    target = os.path.abspath(str(base.need_input(ctx, "output")))
    if os.path.isfile(target):
        raise AdapterError("E_VALIDATION", f"{target} is a file; --output names the folder for the package.", dispatch_state="not_started")
    if os.path.isdir(target) and _has_entries(target):
        raise AdapterError("E_VALIDATION", f"{target} is not empty; name a new or empty folder with --output.", dispatch_state="not_started")
    return target


def pending_setups(config: Path) -> list[str]:
    """The setup files that lack the package's changes (drill spans and the outline in the ODB++
    job, the cells' silkscreen and the outline in the Gerber set)."""
    names: list[str] = []
    for name, patcher in SETUPS:
        path = config / name
        try:
            if path.is_file() and patcher(path.read_text(encoding="utf-8", errors="replace"))[1]:
                names.append(name)
        except OSError:
            continue
    return names


def patch_setups(config: Path) -> list[str]:
    """Patch the setup files that need it, each backed up once; the names patched."""
    patched: list[str] = []
    for name, patcher in SETUPS:
        path = config / name
        try:
            if not path.is_file():
                continue
            text, changes = patcher(path.read_text(encoding="utf-8", errors="replace"))
            if not changes:
                continue
            backup = config / f"{name}{BACKUP_SUFFIX}"
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise AdapterError("E_NATIVE_FAILURE", f"The output setup {name} could not be patched: {exc}",
                               knowledge=["layout-output-setups"]) from None
        patched.append(name)
    return patched


def output_windows(pid: int | None) -> list[str]:
    """Layout's output dialogs and progress boxes: titled windows that are neither a document
    (``[...]``) nor the main window."""
    found: list[str] = []
    for window in dialogs.top_windows(pid):
        title = str(window.get("title") or "")
        main = not window.get("owner") and bool(window.get("style", 0) & WS_MINIMIZEBOX) and window.get("class") != DIALOG_CLASS
        if title and not title.startswith("[") and not main:
            found.append(title)
    return found


def run_output(app: Any, pid: int | None, command: int, progress: Any) -> dict[str, Any]:
    """One Output menu command: its dialog's OK is pressed meanwhile (``OUTPUT_PROMPTS``), and the
    run waits until the dialog and its progress box are gone."""
    started = time.monotonic()
    with dialogs.PromptAnswerer(dialogs.OUTPUT_PROMPTS, pid, interval_s=0.7), L.watch_dialogs(progress, pid, dialogs.OUTPUT_PROMPTS):
        try:
            accepted = app.Gui.ProcessCommand(command)
        except Exception as exc:  # noqa: BLE001 - classified
            raise L.com_error(exc, f"running the Output command {command}", com.call_state(exc)) from None
        if not accepted:
            raise AdapterError("E_NATIVE_FAILURE", f"Layout did not accept the Output command {command}.", dispatch_state="dispatched")
        time.sleep(OUTPUT_SETTLE_S)
        deadline = time.monotonic() + OUTPUT_TIMEOUT_S
        while time.monotonic() < deadline and output_windows(pid):
            time.sleep(OUTPUT_POLL_S)
    return {"command": command, "seconds": round(time.monotonic() - started, 1), "finished": not output_windows(pid)}


def package_components(doc: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for component in L.collection(doc, "Components"):
        refdes = str(component.RefDes or "")
        if not refdes:
            continue
        placed = bool(component.Placed)
        x, y = _position(component) if placed else (None, None)
        rows.append({"refdes": refdes, "part_number": str(component.PartNumber or ""), "footprint": str(component.CellName or ""),
                     "x": x, "y": y, "rotation": float(component.Orientation or 0.0), "side": _side(component), "placed": placed})
    return rows


class BoardExport(base.Handler):
    """The fabrication package (``plan.fab_package``) from the open board, which stays open.

    Layout has no automation call for ODB++, Gerber or NC drill: their Output menu commands run
    with the dialogs answered. The setups in the board's ``Config`` folder are patched first;
    the dialogs keep their settings in memory and may write them back over the patch, so when a
    setup lacks the patch after the run it is patched and run once more (knowledge
    ``layout-output-setups``)."""

    command = "pcb export"
    answers = dialogs.OUTPUT_PROMPTS
    long_job = True

    def preview(self, ctx: Context) -> dict[str, Any]:
        output = _output(ctx)
        path = ctx.document_path()
        L.in_layout(ctx, lambda app, doc: None, budget_s=L.READ_BUDGET_S, what="checking the open board")
        board_dir = Path(path).parent
        pending = pending_setups(board_dir / "Config")
        targets = [base.target(output, "folder", label="fabrication package"),
                   base.target(str(board_dir / "Output"), "folder", label="Layout's output folder")]
        changes = [base.change(base.key(os.path.join(output, name)), "exists", False, True) for name in PACKAGE_FILES]
        patch = ""
        if pending:
            targets.append(base.target(str(board_dir / "Config"), "folder", label="output setups"))
            changes += [base.change(base.key(str(board_dir / "Config" / name)), "patched", False, True) for name in pending]
            patch = f"First patch the output setups {', '.join(pending)} (each backed up once). "
        return base.preview(f"{patch}Run NC drill, ODB++ and Gerber through Layout's Output dialogs into the board's Output folder, "
                            f"then write the package into {output}: the outputs under board-house names, a centroid file, a BOM, a "
                            "README and a manifest. The board stays open and is not saved.", targets, changes)

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        ref = ctx.need_session(base.LAYOUT)
        path = ctx.document_path()
        target = Path(_output(ctx))
        return L.run_com(lambda: self._export(ref.pid, path, target, progress))

    def _export(self, pid: int, path: str, target: Path, progress: Any) -> dict[str, Any]:
        app = L.attach(pid)
        doc = L.board(app, path)
        pcb = Path(path)
        config, outputs = pcb.parent / "Config", pcb.parent / "Output"
        since = time.time()
        runs: list[dict[str, Any]] = []
        patched: list[str] = []
        step, total = 0, len(OUTPUT_COMMANDS) + 1
        try:
            for round_number in range(1, OUTPUT_ROUNDS + 1):
                patched += patch_setups(config)
                for name, command in OUTPUT_COMMANDS:
                    progress(step, total, "steps")
                    runs.append({**run_output(app, pid, command, progress), "format": name, "round": round_number})
                    step += 1
                if not pending_setups(config) or round_number == OUTPUT_ROUNDS:
                    break
                total += len(OUTPUT_COMMANDS)
        except AdapterError as exc:
            if not (patched or runs):
                raise
            # setups patched or outputs written: some files changed, the package was not written
            data = {k: v for k, v in exc.data.items() if k not in ("code", "dispatch_state", "change", "persistence")}
            raise AdapterError(exc.data.get("code", "E_NATIVE_FAILURE"), f"{exc} The package was not written.",
                               dispatch_state="dispatched", change="not_applicable", persistence="partial", **data) from None
        progress(step, total, "steps")
        rect = _quietly(lambda: L.board_rect(doc))
        size = {"width": round(rect[2] - rect[0], 3) if rect else None, "height": round(rect[3] - rect[1], 3) if rect else None,
                "unit": "mm"}
        layers = int(_quietly(lambda: doc.LayerCount, 0) or 0)
        odb_root = outputs / "ODBpp"
        odb_dir = next((item for item in sorted(odb_root.iterdir()) if item.is_dir()), None) if odb_root.is_dir() else None
        try:
            manifest = fab_package.write_package(target, pcb.stem, size, layers, outputs / "Gerber", outputs / "NCDrill", odb_dir,
                                                 package_components(doc), requested=[name for name, _ in OUTPUT_COMMANDS], runs=runs,
                                                 since=since)
        except OSError as exc:
            raise AdapterError("E_NATIVE_FAILURE", f"The package could not be written: {exc}", dispatch_state="dispatched") from None
        files = [*manifest["files"], str(target / "manifest.json")]
        progress(total, total, "steps")
        # the receipt records every file with its size and SHA-256; the result keeps only the count
        # and the manifest, so that job get stays within one response (DCLI-008)
        result = {"path": str(target), "file_count": len(files), "manifest": str(target / "manifest.json"),
                  "board": {"width_mm": size["width"], "height_mm": size["height"], "layers": layers},
                  "problems": manifest["checks"]["problems"], "setups_patched": sorted(set(patched)),
                  "setups_rewritten": pending_setups(config)}
        return {"change": "not_applicable", "persistence": "saved", "result": result, "files": [{"path": item} for item in files]}

    def read_back(self, ctx: Context) -> dict[str, Any]:
        """Which of the previewed files exist, and whether each output setup the run patched now
        holds the package's changes."""
        fields = list(ctx.request.get("fields") or [])
        observed: dict[str, dict[str, Any]] = {}
        for target in ctx.request.get("targets") or []:
            path = path_of(target)
            if not path:
                continue
            row: dict[str, Any] = {}
            if "exists" in fields:
                row["exists"] = os.path.isfile(path)
            if "patched" in fields and Path(path).name in dict(SETUPS):
                row["patched"] = os.path.isfile(path) and Path(path).name not in pending_setups(Path(path).parent)
            if row:
                observed[target] = row
        return {"observed": observed, "unreadable": sorted(field for field in fields if field not in ("exists", "patched"))}


HANDLERS: list[base.Handler] = [BoardGet(), BoardCheck(), BoardExport()]
