"""The board commands that change the open board: forward annotation, the outline, placement,
plane shapes and the autorouter.

Each is a session-mode write: it changes the board in Layout's memory and never saves, closes
or reopens it (DCLI-058, DCLI-201); ``doc save`` does that. Its preview reads the board and
changes nothing, and ``read_back`` reads the preview's target keys again (``layout.read_keys``).
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from . import base, sessions
from . import layout as L
from .base import Context
from .plan import PlanError, board_layout, logs, project_file
from .plan import outline as plan_outline
from .plan import routing as plan_routing
from .platform import com, dialogs

# ------------------------------------------------------------------ forward annotation

ANNOTATION_STEPS = 3
LOG_FRESH_S = 2.0  # a log older than the run's start (less this) was written by an earlier run
DESIGNER_PROBE_S = 3.0
BUSY_STATES = ("blocked", "unresponsive", "busy", "starting")


def project_of(board: str) -> str | None:
    """The project (``.prj``) whose board design is ``board``: looked for beside the ``PCB``
    folder, then in it. The only ``.prj`` beside the folder when none names the board."""
    pcb = Path(board).absolute()
    for folder in (pcb.parent.parent, pcb.parent):
        for prj in sorted(folder.glob("*.prj")):
            try:
                text = prj.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for design in project_file.designs(text):
                if design.pcb_path and L.same_path(project_file.local_path(prj.parent, design.pcb_path), pcb):
                    return str(prj)
    beside = sorted(pcb.parent.parent.glob("*.prj"))
    return str(beside[0]) if len(beside) == 1 else None


def _designer_by_com() -> list[dict[str, Any]]:
    """A light read of the project the running Designer has open, when the designer module
    offers no session data."""
    def read() -> str | None:
        app = com.active_object(com.progids(L.DESIGNER_PROGID))
        if app is None:
            return None
        data = com.member(app, "GetProjectData")
        return str(com.member(data, "GetProjectFilePath") or "") if data is not None else ""

    try:
        found = com.call_with_timeout(read, DESIGNER_PROBE_S)
    except Exception:  # noqa: BLE001 - Designer does not answer
        return [{"pid": None, "state": "unresponsive", "documents": []}]
    if found is None:
        return []
    return [{"pid": None, "state": "ready", "documents": [found] if found else []}]


def designer_projects() -> list[dict[str, Any]]:
    """The running Designers as session data: ``{pid, state, documents}``."""
    from . import designer

    probe = getattr(designer, "instances", None)
    if callable(probe):
        return list(probe(sessions.INSTANCE_TIMEOUT_S))
    return _designer_by_com()


def _designer_name(row: dict[str, Any]) -> str:
    try:
        return f"session {base.make_session_id(base.DESIGNER, int(row['pid']))}"
    except Exception:  # noqa: BLE001 - no process to name
        return "Designer"


def refuse_if_designer_holds(board: str) -> None:
    """Forward annotation fails in its packaging phase while Designer has the project open, and
    closing Designer's project is the person's or the Designer session's business: refuse first."""
    project = project_of(board)
    if project is None:
        return
    try:
        rows = designer_projects()
    except AdapterError:
        rows = [{"pid": None, "state": "unresponsive", "documents": []}]
    for row in rows:
        documents = [str(item) for item in row.get("documents") or []]
        if any(L.same_path(item, project) for item in documents):
            raise AdapterError(
                "E_VALIDATION", f"Designer has the project open ({_designer_name(row)}); forward annotation fails in its packaging "
                "phase while it does. In that Designer session, save the project with doc save and close it with doc close, then "
                "run forward-annotation run again.", dispatch_state="not_started")
        if not documents and row.get("state") in BUSY_STATES:
            raise AdapterError(
                "E_VALIDATION", f"A running Designer ({_designer_name(row)}) does not say which project it has open, and forward "
                "annotation fails while Designer has this project open. Make sure the project is saved and closed there (doc save, "
                "doc close), or close Designer, then run forward-annotation run again.", dispatch_state="not_started")


def refuse_unless_annotatable(state: dict[str, Any]) -> None:
    if state["status"] == "no_data":
        raise AdapterError("E_VALIDATION", "Layout finds no packaged schematic for this board (status 3, no CES data); run packager "
                           "run on the project first.", dispatch_state="not_started", knowledge=["forward-annotation-in-synch"])
    if state["allowed"] is False:
        raise AdapterError("E_VALIDATION", "Layout does not allow forward annotation now (IsForwardAnnotationAllowed is false).",
                           dispatch_state="not_started")


def annotation_summary(log: Path, started: float) -> dict[str, Any]:
    """What ``PCB/LogFiles/ForwardAnnotation.txt`` says, when this run wrote it."""
    try:
        fresh = log.stat().st_mtime >= started - LOG_FRESH_S
    except OSError:
        fresh = False
    if not fresh:
        return {"errors": [], "warnings": [], "components": None, "nets": None, "pins": None, "completed": False}
    return logs.annotation_summary(log)


def _annotation_result(outcome: str, summary: dict[str, Any] | None) -> dict[str, Any]:
    summary = summary or {}
    return {"outcome": outcome, "components": summary.get("components"), "nets": summary.get("nets"),
            "pins": summary.get("pins"), "errors": list(summary.get("errors") or []), "warnings": list(summary.get("warnings") or [])}


class ForwardAnnotation(L.ReadsBack):
    """``ProjectIntegration.ForwardAnnotate``: the packaged schematic into the open board.

    It takes 30 to 70 seconds and shows progress windows, so it runs as a long job. The call is
    dispatched once, as a method (the object has no type information, and a property probe must
    not run a method that changes the board); a failure may leave a partly annotated board, so
    it is never repeated. Layout's own "annotate now?" is answered Yes meanwhile."""

    command = "forward-annotation run"
    long_job = True

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        state = L.in_layout(ctx, lambda app, doc: L.annotation_state(doc), budget_s=L.READ_BUDGET_S,
                            what="reading the forward-annotation status")
        targets = [base.target(path, "pcb", L.ANNOTATION_KEY, label="forward annotation")]
        if state["status"] == "in_synch":
            return base.preview("The board is already in sync with the packaged schematic; forward annotation has nothing to bring in.",
                                targets, [])
        refuse_unless_annotatable(state)
        refuse_if_designer_holds(path)
        change = base.change(base.key(path, L.ANNOTATION_KEY), "status", state["status"], "in_synch")
        return base.preview(f"Forward-annotate the packaged schematic into {Path(path).name}: Layout adds and updates its components "
                            "and nets (unplaced), and the board is not saved.", targets, [change])

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        ref = ctx.need_session(base.LAYOUT)
        path = ctx.document_path()
        log = Path(path).parent / "LogFiles" / "ForwardAnnotation.txt"
        progress(0, ANNOTATION_STEPS, "steps")
        refuse_if_designer_holds(path)
        return L.run_com(lambda: self._annotate(ref.pid, path, log, progress))

    def _annotate(self, pid: int, path: str, log: Path, progress: Any) -> dict[str, Any]:
        doc = L.board(L.attach(pid), path)
        state = L.annotation_state(doc)
        if state["status"] == "in_synch":
            return {"change": "not_applied", "persistence": "unchanged", "result": _annotation_result("in_synch", None)}
        refuse_unless_annotatable(state)
        integration = com.member(doc, "ProjectIntegration")
        progress(1, ANNOTATION_STEPS, "steps")
        started = time.time()
        rules = dialogs.LAYOUT_PROMPTS_ANNOTATE
        try:
            with dialogs.PromptAnswerer(rules, pid), L.watch_dialogs(progress, pid, rules):
                done = com.invoke_method(integration, "ForwardAnnotate", result_type=(com.VT_BOOL, 0))
        except AdapterError as exc:
            raise self._failure(exc, annotation_summary(log, started)) from None
        progress(2, ANNOTATION_STEPS, "steps")
        after = L.annotation_state(doc)["status"]
        summary = annotation_summary(log, started)
        if done is not True and after != "in_synch":
            raise AdapterError("E_NATIVE_FAILURE", "Layout's ForwardAnnotate answered false and the board is not in sync; read the "
                               f"board before anything else.{_log_note(summary)}", dispatch_state="dispatched", change="unknown")
        progress(3, ANNOTATION_STEPS, "steps")
        return {"change": "applied", "persistence": "unchanged", "result": _annotation_result("annotated", summary)}

    @staticmethod
    def _failure(exc: AdapterError, summary: dict[str, Any]) -> AdapterError:
        data = dict(exc.data)
        code = data.pop("code", "E_NATIVE_FAILURE")
        state = data.pop("dispatch_state", None)
        if state != "not_started":
            data["change"] = "unknown"  # a failed annotation may leave a partly updated board
            if "1001" in str(data.get("native_message", "")):
                data["knowledge"] = ["forward-annotation-in-synch"]
        message = (f"{exc} If it failed in the packaging phase, check that Designer has the project closed, close and open the "
                   f"board again (doc close, doc open), then run forward-annotation run again.{_log_note(summary)}")
        return AdapterError(code, message, dispatch_state=state, **data)


def _log_note(summary: dict[str, Any]) -> str:
    """Where the reasons are. The log's lines are Layout's text, so they are counted, not quoted."""
    errors = summary.get("errors") or []
    if not errors:
        return ""
    return f" PCB/LogFiles/ForwardAnnotation.txt lists {len(errors)} error lines; read them there."


# ------------------------------------------------------------------ the outline

OUTLINE_PEN_MM = 0.15
OUTLINE_SETTERS = (("board_outline", "PutBoardOutline", "board outline"), ("route_border", "PutRouteBorder", "route border"),
                   ("manufacturing_outline", "PutManufacturingOutline", "manufacturing outline"))


def _outline_inputs(ctx: Context) -> tuple[float, float, float]:
    width, height = ctx.inputs.get("width_mm"), ctx.inputs.get("height_mm")
    if width is None or height is None:
        raise AdapterError("E_VALIDATION", "pcb update needs both --width and --height.", dispatch_state="not_started")
    radius = ctx.inputs.get("radius_mm") or 0.0
    try:
        plan_outline.targets(width, height, radius)
    except PlanError as exc:
        raise L.plan_error(exc) from None
    return float(width), float(height), float(radius)


def _rows(points: list[list[float]]) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(round(float(value), 4) for value in row) for row in points)


class OutlineUpdate(L.ReadsBack):
    """The board outline as a rectangle from the origin, with rounded corners when asked
    (``plan.outline``): ``PutBoardOutline``, ``PutRouteBorder`` 0.3 mm inside it and
    ``PutManufacturingOutline`` equal to it."""

    command = "pcb update"

    def preview(self, ctx: Context) -> dict[str, Any]:
        width, height, radius = _outline_inputs(ctx)
        path = ctx.document_path()
        current = L.in_layout(ctx, lambda app, doc: L.outline_values(doc), budget_s=L.READ_BUDGET_S, what="reading the board outline")
        key = base.key(path, L.OUTLINE_KEY)
        changes = [base.change(key, field, current.get(field), value)
                   for field, value in (("width_mm", width), ("height_mm", height), ("radius_mm", radius))]
        corners = f" with {radius:g} mm corners" if radius else ""
        return base.preview(f"Replace the board outline by a {width:g} x {height:g} mm rectangle from the origin{corners}; the route border "
                            f"follows {plan_outline.INSET_MM:g} mm inside it and the manufacturing outline matches it.",
                            [base.target(path, "outline", L.OUTLINE_KEY, label="board outline")], changes)

    def apply(self, ctx: Context) -> dict[str, Any]:
        width, height, radius = _outline_inputs(ctx)
        contours = plan_outline.targets(width, height, radius)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            done: list[str] = []
            for key, setter, label in OUTLINE_SETTERS:
                rows = _rows(contours[key]["points_mm"])
                try:
                    if setter == "PutManufacturingOutline":
                        getattr(doc, setter)(len(rows[0]), rows, L.UNIT_MM)
                    else:
                        getattr(doc, setter)(len(rows[0]), rows, OUTLINE_PEN_MM, L.UNIT_MM)
                except Exception as exc:  # noqa: BLE001 - classified
                    return L.failed_step(exc, f"writing the {label}", done)
                done.append(f"the {label}")
            return base.facts("applied", "unchanged", result={"width_mm": width, "height_mm": height, "radius_mm": radius,
                                                              "route_border_inset_mm": plan_outline.INSET_MM})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="writing the board outline")


# ------------------------------------------------------------------ placement

CORNER_KEEPOUT_MM = 7.0  # kept free in each corner once the board has mounting holes
LABEL_LAYER_SILKSCREEN = 2  # epcbFabSilkscreen
SIDE_TOP = 1  # epcbSideTop
LABEL_PEN_MM = 0.15
LABEL_FONT = "VeriBest Gerber 0"
PLACE_BUDGET_S = 34.0  # no part is placed past this, so that apply ends before 45 s


def design_zones(design: Any) -> dict[str, str]:
    """Zone labels by placement group, from the design file's ``sheets[].zone``. A group is the
    sheet a reference designator encodes (C201: group 2), which is not always the sheet it is
    drawn on, so each sheet's label goes to the group most of its parts fall in."""
    if not design:
        return {}
    try:
        data = json.loads(Path(str(design)).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AdapterError("E_NOT_FOUND", f"There is no design file {design}.", dispatch_state="not_started") from None
    except (OSError, ValueError) as exc:
        raise AdapterError("E_VALIDATION", f"The design file {design} cannot be read as JSON: {exc}", dispatch_state="not_started") from None
    from .plan import schematic_layout

    labels: dict[int, str] = {}
    for index, sheet in enumerate(data.get("sheets") or [] if isinstance(data, dict) else [], 1):
        if isinstance(sheet, dict) and str(sheet.get("zone") or "").strip():
            try:
                labels[int(sheet.get("number") or index)] = str(sheet["zone"]).strip()
            except (TypeError, ValueError):
                continue
    by_sheet: dict[int, list[str]] = {}
    for refdes, number in schematic_layout.refdes_by_sheet(data).items():
        by_sheet.setdefault(number, []).append(refdes)
    zones: dict[str, str] = {}
    for number, label in sorted(labels.items()):
        refs = by_sheet.get(number)
        if refs:
            zones.setdefault(Counter(board_layout.group_of(ref) for ref in refs).most_common(1)[0][0], label)
    return zones


class _PlacementDrcOff:
    """``RespectComponentPlacementDRC`` off while parts move, restored afterwards. It does not stop
    Layout refusing a part placed onto another part or onto its own old footprint (knowledge
    ``layout-placement-drc``), so parts are lifted before they are placed."""

    def __init__(self, doc: Any) -> None:
        self.doc = doc
        self.previous: Any = None

    def __enter__(self) -> _PlacementDrcOff:
        try:
            self.previous = bool(self.doc.RespectComponentPlacementDRC)
            self.doc.RespectComponentPlacementDRC = False
        except Exception:  # noqa: BLE001 - this release lacks the switch
            self.previous = None
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.previous is not None:
            try:
                self.doc.RespectComponentPlacementDRC = self.previous
            except Exception:  # noqa: BLE001 - nothing more can be done
                pass


def _components(doc: Any, include_placed: bool) -> tuple[list[tuple[Any, str, bool]], list[str]]:
    """``(component, refdes, placed)`` of every part to plan, and the placed parts left alone."""
    todo: list[tuple[Any, str, bool]] = []
    skipped: list[str] = []
    for component in L.collection(doc, "Components"):
        refdes = str(component.RefDes or "")
        if not refdes:
            continue
        placed = bool(component.Placed)
        if placed and not include_placed:
            skipped.append(refdes)
        else:
            todo.append((component, refdes, placed))
    return todo, sorted(skipped)


def _turn(dx: float, dy: float, degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    return dx * math.cos(angle) - dy * math.sin(angle), dx * math.sin(angle) + dy * math.cos(angle)


def _pin_offsets(component: Any, centre: tuple[float, float], orientation: float) -> dict[str, tuple[float, float]]:
    """Pin name -> offset from the footprint's centre, unturned, in mm."""
    offsets: dict[str, tuple[float, float]] = {}
    for pin in L.collection(component, "Pins"):
        try:
            dx = float(pin.GetPositionX(L.UNIT_MM)) - centre[0]
            dy = float(pin.GetPositionY(L.UNIT_MM)) - centre[1]
        except Exception:  # noqa: BLE001 - a pin without a position is left out
            continue
        if orientation % 360:
            dx, dy = _turn(dx, dy, -orientation)
        offsets[L.name_of(pin)] = (round(dx, 3), round(dy, 3))
    return offsets


def _measure(component: Any, refdes: str, at: tuple[float, float] | None = None) -> board_layout.Part:
    """A part's footprint size, its cell origin's offset from the footprint's centre and its
    pins, in mm, unturned. Layout has extents only for a placed part: an unplaced one is placed
    at ``at`` for the measurement and unplaced again, so only apply measures unplaced parts."""
    placed = bool(component.Placed)
    if not placed:
        if at is None:
            raise AdapterError("E_VALIDATION", f"{refdes} is not placed and cannot be measured without placing it.",
                               dispatch_state="not_started")
        component.Place(at[0], at[1], 0.0, True, 0, L.UNIT_MM, 0)
    try:
        x0, y0, x1, y1 = L.extrema_mm(component)
        width, height = max(x1 - x0, 0.0), max(y1 - y0, 0.0)
        centre = ((x0 + x1) / 2, (y0 + y1) / 2)
        offset = (float(component.GetPositionX(L.UNIT_MM)) - centre[0], float(component.GetPositionY(L.UNIT_MM)) - centre[1])
        orientation = float(component.Orientation or 0.0) if placed else 0.0
        pins = _pin_offsets(component, centre, orientation)
        if placed and orientation % 360:
            # measured as it stands; the planner wants it unturned (a connector at 90 degrees
            # came back 5.5 wide and 10.9 tall, and the plan turned it again)
            offset = _turn(*offset, -orientation)
            if round(orientation) % 180 == 90:
                width, height = height, width
        return board_layout.Part(refdes, width, height, str(component.CellName or ""), round(offset[0], 3), round(offset[1], 3),
                                 pins=pins)
    finally:
        if not placed:
            component.UnPlace()


def _board_nets(doc: Any) -> list[board_layout.Net]:
    """The nets with their (refdes, pin) members; an unplaced part has no pins in ``Net.Pins``."""
    nets: list[board_layout.Net] = []
    for net in L.collection(doc, "Nets"):
        pins: list[tuple[str, str]] = []
        for pin in L.collection(net, "Pins"):
            try:
                pins.append((str(pin.Component.RefDes), L.name_of(pin)))
            except Exception:  # noqa: BLE001 - a pin without a part
                continue
        nets.append(board_layout.Net(L.name_of(net), pins, bool(com.member(net, "IsPower"))))
    return nets


def _plan(doc: Any, board: tuple[float, float, float, float], parts: list[board_layout.Part], zones: dict[str, str]) -> board_layout.Plan:
    keepout = CORNER_KEEPOUT_MM if L.count(doc, "MountingHoles") else 0.0
    return board_layout.arrange(parts, board, _board_nets(doc), zones, grid=board_layout.GRID, corner_keepout=keepout)


def _origin(item: board_layout.Placement, part: board_layout.Part) -> tuple[float, float]:
    """Where the cell origin goes so that the footprint's centre lands on the plan."""
    ox, oy = part.offset_x, part.offset_y
    turn = item.rotation % 360
    if turn == 90:
        ox, oy = -oy, ox
    elif turn == 180:
        ox, oy = -ox, -oy
    elif turn == 270:
        ox, oy = oy, -ox
    return round(item.x + ox, 3), round(item.y + oy, 3)


def _planned(plan: board_layout.Plan, parts: dict[str, board_layout.Part]) -> dict[str, Any]:
    rows: dict[str, dict[str, float]] = {}
    for item in plan.placements:
        x, y = _origin(item, parts[item.refdes])
        rows[item.refdes] = {"x_mm": x, "y_mm": y, "rotation_deg": round(float(item.rotation) % 360.0, 4)}
    return {"rows": rows, "outside": sorted(item.refdes for item in plan.placements if not item.inside)}


def _look(doc: Any, include_placed: bool, zones: dict[str, str]) -> dict[str, Any]:
    """What a placement would do, read without changing the board. When every part to plan is
    placed it is measured where it stands and the plan is made; an unplaced part can only be
    measured by placing it, so then the positions are planned when the change runs."""
    board = L.board_rect(doc)
    todo, skipped = _components(doc, include_placed)
    look: dict[str, Any] = {"skipped": skipped, "routing": L.routing_values(doc), "planned": None,
                            "before": {refdes: L.component_values(component) for component, refdes, _ in todo}}
    if todo and all(placed for _, _, placed in todo):
        parts = {refdes: _measure(component, refdes) for component, refdes, _ in todo}
        look["planned"] = _planned(_plan(doc, board, list(parts.values()), zones), parts)
    return look


def _shown(names: list[str], limit: int = 10) -> str:
    return ", ".join(names[:limit]) + (" ..." if len(names) > limit else "")


def _placement_preview(path: str, look: dict[str, Any]) -> dict[str, Any]:
    todo = sorted(look["before"], key=board_layout.sort_key)
    if not todo:
        return base.preview("Every component is placed already; nothing to place (--all places them again).", [], [])
    targets = [base.target(path, "component", L.COMPONENTS_KEY, label=f"{len(todo)} components")]
    changes: list[dict[str, Any]] = []
    routing = look["routing"]
    if routing.get("traces") or routing.get("vias"):
        # a part placed onto a trace is a DRC violation, and a new placement makes every trace wrong
        targets.append(base.target(path, "pcb", L.ROUTING_KEY, label="traces and vias"))
        key = base.key(path, L.ROUTING_KEY)
        changes += [base.change(key, "traces", routing.get("traces"), 0), base.change(key, "vias", routing.get("vias"), 0)]
    planned = look["planned"]
    for refdes in todo:
        key = base.key(path, f"{L.COMPONENTS_KEY}/{L.segment(refdes)}")
        before = look["before"][refdes]
        if planned is None:
            changes.append(base.change(key, "placed", before["placed"], True))
        else:
            after = planned["rows"][refdes]
            changes += [base.change(key, field, before[field], after[field]) for field in ("x_mm", "y_mm", "rotation_deg")]
    blocked = None
    if planned and planned["outside"]:
        blocked = [f"{len(planned['outside'])} parts would land outside the board outline ({_shown(planned['outside'])}); enlarge "
                   "the board with pcb update, then plan again"]
    where = ("at the planned positions" if planned else
             "where the plan puts them once they are measured (Layout measures an unplaced part only by placing it, so the "
             "positions are planned when the change runs)")
    removal = "delete every trace and via first, then " if routing.get("traces") or routing.get("vias") else ""
    return base.preview(f"Place {len(todo)} components on the top side by their connections, grouped by sheet: {removal}place them "
                        f"{where}, and write one zone label per sheet on the top silkscreen. The board is not saved.",
                        targets, changes, blocked_by=blocked)


def _park(lifted: list[tuple[Any, board_layout.Part]], board: tuple[float, float, float, float], parked: list[Any]) -> None:
    """Park the measured parts in rows below the outline, one gap apart, so that the nets read
    their pins: Layout accepts a part outside the board, not two identical footprints on one spot.
    ``parked`` collects them as they go."""
    gap = board_layout.GAP
    x, row_top, row_height = board[0], board[1] - 2 * gap, 0.0
    for component, part in lifted:
        width, height = max(part.width, 0.5) + gap, max(part.height, 0.5) + gap
        if x > board[0] and x + width > board[2]:
            row_top -= row_height
            x, row_height = board[0], 0.0
        component.Place(round(x + width / 2 + part.offset_x, 3), round(row_top - height / 2 + part.offset_y, 3), 0.0, True, 0,
                        L.UNIT_MM, 0)
        parked.append(component)
        x += width
        row_height = max(row_height, height)


def _unplace(components: list[Any]) -> None:
    for component in components:
        try:
            component.UnPlace()
        except Exception:  # noqa: BLE001 - best effort: the part stays parked below the board
            pass


def _delete_routing(doc: Any) -> dict[str, int]:
    removed = {"traces": 0, "vias": 0}
    for name in ("Traces", "Vias"):
        for item in L.collection(doc, name):
            try:
                item.Delete()
                removed[name.lower()] += 1
            except Exception:  # noqa: BLE001 - the read-back shows what is left
                continue
    return removed


def _write_labels(doc: Any, labels: list[board_layout.Label]) -> list[dict[str, Any]]:
    """The sheet zone labels on the top silkscreen, replacing the board's own texts with the same
    words (an earlier placement's labels)."""
    texts = {label.text for label in labels}
    for text in L.collection(doc, "FabricationLayerTexts"):
        try:
            if text.Component is None and str(text.TextString) in texts:
                text.Delete()
        except Exception:  # noqa: BLE001 - an old label stays
            continue
    written: list[dict[str, Any]] = []
    for label in labels:
        try:
            doc.PutFabricationLayerText(label.text, label.x, label.y, LABEL_LAYER_SILKSCREEN, SIDE_TOP, board_layout.LABEL_HEIGHT, 0.0,
                                        LABEL_PEN_MM, LABEL_FONT, 0, 0, 0, None, L.UNIT_MM, 0)
            written.append({"text": label.text, "written": True})
        except Exception:  # noqa: BLE001 - a label is not a part; the placement stands
            written.append({"text": label.text, "written": False})
    return written


def _place(doc: Any, include_placed: bool, zones: dict[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    board = L.board_rect(doc)
    todo, _skipped = _components(doc, include_placed)
    if not todo:
        return base.facts("not_applied", "unchanged", result={"placed": 0, "clusters": [], "outside": []})
    steps: list[str] = []
    routing = L.routing_values(doc)
    with _PlacementDrcOff(doc):
        centre = ((board[0] + board[2]) / 2, (board[1] + board[3]) / 2)
        parts: dict[str, board_layout.Part] = {}
        lifted: list[tuple[Any, board_layout.Part]] = []
        parked: list[Any] = []
        try:
            for component, refdes, placed in todo:
                parts[refdes] = _measure(component, refdes, centre)  # an unplaced part is placed and unplaced again
                if not placed:
                    lifted.append((component, parts[refdes]))
            _park(lifted, board, parked)
            plan = _plan(doc, board, list(parts.values()), zones)
        except Exception as exc:  # noqa: BLE001 - classified; the parked parts go back first
            _unplace(parked)
            raise L.com_error(exc, "measuring the parts", None if isinstance(exc, AdapterError) else com.call_state(exc)) from None
        outside = sorted(item.refdes for item in plan.placements if not item.inside)
        if outside:
            # placed past the outline a part lands on the parked ones, and Layout stops halfway
            _unplace(parked)
            raise AdapterError("E_VALIDATION", f"{len(outside)} parts would land outside the board outline ({_shown(outside)}); nothing "
                               "was placed. Enlarge the board with pcb update, then run placement run again.",
                               dispatch_state="dispatched" if lifted else "not_started", knowledge=["layout-placement-drc"])
        if parked:
            steps.append("parking the measured parts below the board")
        placed_count, removed = 0, {"traces": 0, "vias": 0}
        try:
            if routing.get("traces") or routing.get("vias"):
                steps.append("deleting the traces and vias")
                removed = _delete_routing(doc)
            for item in plan.placements:
                component = L.find_component(doc, item.refdes)
                if component is not None and bool(component.Placed):
                    if "lifting the parts" not in steps:
                        steps.append("lifting the parts")
                    component.UnPlace()
            for item in plan.placements:
                if time.monotonic() - started > PLACE_BUDGET_S:
                    return base.facts("partial", "unchanged", result=_placement_result(plan, placed_count, []),
                                      native_message=f"Stopped after {placed_count} of {len(plan.placements)} parts to stay within the "
                                      "call's time limit; run placement run --all to place every part again.")
                component = L.find_component(doc, item.refdes)
                x, y = _origin(item, parts[item.refdes])
                component.Place(x, y, float(item.rotation), True, 0, L.UNIT_MM, 0)
                placed_count += 1
                steps.append(f"placing {item.refdes}")
        except Exception as exc:  # noqa: BLE001 - classified
            return L.failed_step(exc, f"placing the parts ({placed_count} of {len(plan.placements)} placed; run placement run --all "
                                 "to place every part again)", steps)
    labels = _write_labels(doc, plan.labels)
    result = _placement_result(plan, placed_count, labels)
    result["routing_removed"] = removed
    return base.facts("applied", "unchanged", result=result)


def _placement_result(plan: board_layout.Plan, placed: int, labels: list[dict[str, Any]]) -> dict[str, Any]:
    clusters = [{"name": name, "parts": parts} for name, parts in sorted(plan.clusters.items(), key=lambda kv: board_layout.sort_key(kv[0]))]
    return {"placed": placed, "clusters": clusters, "outside": [], "labels": labels}


class PlacementRun(L.ReadsBack):
    """A first placement by connectivity (``plan.board_layout.arrange``): clusters per sheet,
    connectors on the edges, test points along the bottom, a zone label per sheet."""

    command = "placement run"

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        zones = design_zones(ctx.inputs.get("design"))
        include_placed = bool(ctx.inputs.get("all"))
        look = L.in_layout(ctx, lambda app, doc: _look(doc, include_placed, zones), budget_s=L.READ_BUDGET_S,
                           what="reading the components")
        return _placement_preview(path, look)

    def apply(self, ctx: Context) -> dict[str, Any]:
        zones = design_zones(ctx.inputs.get("design"))
        include_placed = bool(ctx.inputs.get("all"))
        return L.in_layout(ctx, lambda app, doc: _place(doc, include_placed, zones), budget_s=L.WRITE_BUDGET_S, write=True,
                           what="placing the components")


# ------------------------------------------------------------------ plane shapes

PLANE_OUTLINE_POINTS = 5  # a plain rectangle; a rounded one has 13


def _plane_inputs(ctx: Context) -> tuple[str, int, float]:
    net = str(base.need_input(ctx, "net")).strip()
    layer = base.need_input(ctx, "layer")
    margin = ctx.inputs.get("margin_mm")
    margin = 1.0 if margin is None else margin
    if not net:
        raise AdapterError("E_VALIDATION", "plane create needs a net name.", dispatch_state="not_started")
    if type(layer) is not int or layer < 1:
        raise AdapterError("E_VALIDATION", "--layer is a conductor layer number, 1 at the top.", dispatch_state="not_started")
    if type(margin) not in (int, float) or not math.isfinite(margin) or margin < 0:
        raise AdapterError("E_VALIDATION", "--margin is a distance of 0 or more.", dispatch_state="not_started")
    return net, layer, float(margin)


def _plane_points(doc: Any, margin: float) -> tuple[tuple[float, ...], ...]:
    """The board outline's extents inset by ``margin``; a rounded outline gives the shape its
    corner radius less the margin."""
    x0, y0, x1, y1 = L.board_rect(doc)
    radius = L.outline_radius(doc, doc.BoardOutline)
    if radius is None:
        raise AdapterError("E_UNSUPPORTED", "The board outline has rounded corners, and their radius is known only on a board in "
                           "mils; switch the board's unit to mils in Layout, then run plane create again.", dispatch_state="not_started")
    rect = (x0 + margin, y0 + margin, x1 - margin, y1 - margin)
    if rect[0] >= rect[2] or rect[1] >= rect[3]:
        raise AdapterError("E_VALIDATION", f"A {margin:g} mm margin leaves no room inside the board outline.", dispatch_state="not_started")
    return _rows(plan_outline.rounded(*rect, max(radius - margin, 0.0)))


def _plane_target(doc: Any, net: str, layer: int, margin: float) -> tuple[Any, tuple[tuple[float, ...], ...], dict[str, Any]]:
    """The net object, the shape's points and the plane as it is now (with the board's
    ``layers``); refuses an unknown net or layer, and a net that has a plane shape on that layer
    already."""
    found = L.find_net(doc, net)
    if found is None:
        raise AdapterError("E_NOT_FOUND", f"The board has no net {net}.", dispatch_state="not_started")
    layers = int(doc.LayerCount)
    if not 1 <= layer <= layers:
        raise AdapterError("E_VALIDATION", f"The board has conductor layers 1 to {layers}; there is no layer {layer}.",
                           dispatch_state="not_started")
    now = L.plane_values(doc, net, layer)
    if now["exists"]:
        raise AdapterError("E_VALIDATION", f"{net} has a plane shape on layer {layer} already; plane create adds none beside it.",
                           dispatch_state="not_started")
    return found, _plane_points(doc, margin), {**now, "layers": layers}


def _generated(doc: Any, net: str, layer: int) -> bool:
    try:
        return any(L.net_name(item) == net and int(item.Layer) == layer for item in L.collection(doc, "GeneratedPlanes"))
    except Exception:  # noqa: BLE001 - not known to be generated
        return False


class PlaneCreate(L.ReadsBack):
    """``PutPlaneShape`` for one net on one layer, not obstructing the router, then the net's
    plane assignment to Dynamic (``PlaneDataState = 1``): a new shape starts in Draft, which
    generates no copper (knowledge ``layout-plane-draft-state``)."""

    command = "plane create"

    def preview(self, ctx: Context) -> dict[str, Any]:
        net, layer, margin = _plane_inputs(ctx)
        path = ctx.document_path()
        now = L.in_layout(ctx, lambda app, doc: _plane_target(doc, net, layer, margin)[2], budget_s=L.READ_BUDGET_S,
                          what="reading the planes")
        domain = L.plane_key(net, layer)
        key = base.key(path, domain)
        outer = (" An outer-layer plane is best poured after routing (knowledge layout-pour-after-routing)."
                 if layer in (1, now["layers"]) else "")
        return base.preview(f"Pour a plane shape for {net} on layer {layer}, {margin:g} mm inside the board outline, and switch it to "
                            f"Dynamic so that Layout generates its copper. The board is not saved.{outer}",
                            [base.target(path, "plane", domain, label=f"{net} on layer {layer}")],
                            [base.change(key, "exists", False, True), base.change(key, "state", now["state"], "dynamic")])

    def apply(self, ctx: Context) -> dict[str, Any]:
        net, layer, margin = _plane_inputs(ctx)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            found, points, _now = _plane_target(doc, net, layer, margin)
            try:
                # route obstruct false: an obstructing shape blocks the router on that layer, and laid
                # over routed traces it is refused as a DRC violation; the copper flows around them
                doc.PutPlaneShape(layer, len(points[0]), points, found, False, 0, 0.0, 0.0, None, L.UNIT_MM)
            except Exception as exc:  # noqa: BLE001 - classified
                raise L.com_error(exc, "creating the plane shape", com.call_state(exc)) from None
            try:
                assignment = doc.FindPlaneAssignment(layer, found)
                if assignment is None:
                    raise AdapterError("E_NATIVE_FAILURE", "Layout made the shape but reports no plane assignment for it.")
                assignment.PlaneDataState = L.PLANE_DYNAMIC
            except Exception as exc:  # noqa: BLE001 - classified
                return L.failed_step(exc, "switching the plane to Dynamic", ["the plane shape"])
            return base.facts("applied", "unchanged", result={"net": net, "layer": layer, "margin_mm": margin,
                                                              "generated": _generated(doc, net, layer)})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="pouring the plane")


# ------------------------------------------------------------------ the autorouter

ROUTE_BUDGET_S = 30.0  # no pass starts past this; a running pass cannot be interrupted
SECOND_ROUND_S = 12.0  # a second round after the plane regeneration starts only this early
OPEN_LISTED = 50


def _layers(ctx: Context) -> list[int]:
    try:
        return plan_routing.parse_route_layers(ctx.inputs.get("layers"))
    except PlanError as exc:
        raise L.plan_error(exc) from None


def _check_layers(layers: list[int], layer_count: int) -> None:
    """A route pass can leave out inner layers only: the outer two are always the router's, and
    naming one in ``LayerSelect`` is an invalid parameter."""
    if not layers:
        return
    wrong = [layer for layer in layers if layer > layer_count]
    if wrong:
        raise AdapterError("E_VALIDATION", f"The board has conductor layers 1 to {layer_count}; there is no layer {wrong[0]}.",
                           dispatch_state="not_started")
    if layer_count >= 2 and (1 not in layers or layer_count not in layers):
        raise AdapterError("E_VALIDATION", f"--layers must include the outer layers 1 and {layer_count}: the autorouter always routes "
                           "them, and a route pass can leave out inner layers only.", dispatch_state="not_started")


def _draft_planes(doc: Any) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for assignment in L.collection(doc, "PlaneAssignments"):
        try:
            if int(assignment.PlaneDataState) == L.PLANE_DRAFT:
                rows.append((L.net_name(assignment), int(assignment.LayerObject.ConductorLayer)))
        except Exception:  # noqa: BLE001 - an assignment that cannot be read is left alone
            continue
    return sorted(rows)


def _route_look(doc: Any, layers: list[int]) -> dict[str, Any]:
    layer_count = int(doc.LayerCount)
    _check_layers(layers, layer_count)
    status = L.route_status(doc)
    unplaced = sorted(str(item.RefDes) for item in L.collection(doc, "Components") if not bool(item.Placed))
    return {"layer_count": layer_count, "open": sorted(name for name, row in status.items() if not row["routed"]),
            "unplaced": unplaced, "drafts": _draft_planes(doc)}


def _unplaced_reason(unplaced: list[str]) -> str:
    return (f"{len(unplaced)} components are not placed ({_shown(unplaced)}); place them with placement run first: a route pass "
            "skips unplaced parts and reports their nets routed")


def _route_pass(doc: Any, item: dict[str, Any], layers: list[int], layer_count: int) -> Any:
    """One autorouter pass over every net. ``PassType`` resets every other setting, so it goes first;
    ``LayerSelect(3, n)`` (remove) leaves out each inner layer not asked for."""
    route_pass = doc.NewRoutePass()
    route_pass.PassType(item["type"], item["effort"][0], item["effort"][1], False, False)
    route_pass.Items(plan_routing.ROUTE_ITEMS_ALL_NETS, None)
    route_pass.Order(plan_routing.ROUTE_ORDER_AUTO, 0)
    route_pass.Config(False, True)
    if layers:
        for number in range(2, layer_count):
            if number not in layers:
                route_pass.LayerSelect(plan_routing.ROUTE_LAYER_REMOVE, number)
    return route_pass


def _regenerate_planes(doc: Any) -> int:
    """Vias the router adds tie into a plane only once it is regenerated: each Dynamic plane goes
    to Draft and back, each Draft plane to Dynamic (the preview lists those)."""
    regenerated = 0
    for assignment in L.collection(doc, "PlaneAssignments"):
        state = int(assignment.PlaneDataState)
        if state == L.PLANE_DYNAMIC:
            assignment.PlaneDataState = L.PLANE_DRAFT
            assignment.PlaneDataState = L.PLANE_DYNAMIC
            regenerated += 1
        elif state == L.PLANE_DRAFT:
            assignment.PlaneDataState = L.PLANE_DYNAMIC
            regenerated += 1
    return regenerated


class RouterRun(L.ReadsBack):
    """Layout's batch autorouter (``Document.NewRoutePass`` and ``Go``) with the default passes of
    ``plan.routing``, then the planes regenerated, and once more when a net is still open and
    there is time."""

    command = "router run"

    def preview(self, ctx: Context) -> dict[str, Any]:
        layers = _layers(ctx)
        path = ctx.document_path()
        look = L.in_layout(ctx, lambda app, doc: _route_look(doc, layers), budget_s=L.READ_BUDGET_S, what="reading the nets")
        if look["unplaced"]:
            return base.preview("Route the open nets.", [base.target(path, "net", L.NETS_KEY)], [],
                                blocked_by=[_unplaced_reason(look["unplaced"])])
        if not look["open"]:
            return base.preview("Every net is routed already; the autorouter has nothing to do.", [], [])
        targets = [base.target(path, "net", L.NETS_KEY, label=f"{len(look['open'])} open nets")]
        changes = [base.change(base.key(path, f"{L.NETS_KEY}/{L.segment(name)}"), "routed", False, True) for name in look["open"]]
        for net, layer in look["drafts"]:
            domain = L.plane_key(net, layer)
            targets.append(base.target(path, "plane", domain, label=f"{net} on layer {layer}"))
            changes.append(base.change(base.key(path, domain), "state", "draft", "dynamic"))
        on = f" on layers {', '.join(map(str, layers))}" if layers else ""
        return base.preview(f"Route {len(look['open'])} open nets with the autorouter passes {plan_routing.DEFAULT_ROUTE_PASSES}{on}, then "
                            "regenerate the planes so that the new vias tie in. The board is not saved.", targets, changes,
                            blocked_by=[_unplaced_reason(look["unplaced"])] if look["unplaced"] else None)

    def apply(self, ctx: Context) -> dict[str, Any]:
        layers = _layers(ctx)
        passes = plan_routing.parse_route_passes("")
        return L.in_layout(ctx, lambda app, doc: self._route(doc, layers, passes), budget_s=L.WRITE_BUDGET_S, write=True,
                           what="routing the board")

    @staticmethod
    def _route(doc: Any, layers: list[int], passes: list[dict[str, Any]]) -> dict[str, Any]:
        look = _route_look(doc, layers)
        if look["unplaced"]:
            raise AdapterError("E_VALIDATION", _unplaced_reason(look["unplaced"]) + ".", dispatch_state="not_started",
                               knowledge=["layout-unplaced-parts-invisible"])
        if not look["open"]:
            return base.facts("not_applied", "unchanged", result=_route_result(doc, [], [], 0))
        started = time.monotonic()
        done: list[str] = []
        skipped: list[str] = []
        regenerated = 0
        for round_number in (1, 2):
            for item in passes:
                if time.monotonic() - started > ROUTE_BUDGET_S:
                    if round_number == 1:
                        skipped.append(item["pass"])
                    continue
                try:
                    _route_pass(doc, item, layers, look["layer_count"]).Go()
                except Exception as exc:  # noqa: BLE001 - classified
                    return L.failed_step(exc, f"running the {item['pass']} pass", done)
                done.append(item["pass"] if round_number == 1 else f"{item['pass']} (second round)")
            try:
                regenerated += _regenerate_planes(doc)
            except Exception as exc:  # noqa: BLE001 - classified
                return L.failed_step(exc, "regenerating the planes", done)
            still_open = any(not row["routed"] for row in L.route_status(doc).values())
            if not still_open or time.monotonic() - started > SECOND_ROUND_S:
                break
        message = (f"Passes {', '.join(skipped)} did not run: the call's time limit was near. Run router run again to route what is "
                   "left.") if skipped else None
        return base.facts("partial" if skipped else "applied", "unchanged", result=_route_result(doc, done, skipped, regenerated),
                          native_message=message)


def _route_result(doc: Any, done: list[str], skipped: list[str], regenerated: int) -> dict[str, Any]:
    status = L.route_status(doc)
    still_open = sorted(name for name, row in status.items() if not row["routed"])
    return {"nets": len(status), "routed": len(status) - len(still_open), "open": still_open[:OPEN_LISTED],
            "traces": L.count(doc, "Traces"), "vias": L.count(doc, "Vias"), "passes": done, "skipped_passes": skipped,
            "planes_regenerated": regenerated}


HANDLERS: list[base.Handler] = [ForwardAnnotation(), OutlineUpdate(), PlacementRun(), PlaneCreate(), RouterRun()]
