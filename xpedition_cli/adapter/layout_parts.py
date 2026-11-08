"""Components on the open board: reading them (component list and component get on a board) and
moving them (component move); and the board's mounting holes (mounting-hole create and delete).

Positions are board coordinates in mm: a component's position is its cell origin, as Layout
places it; its rotation is counter-clockwise in degrees, seen from the top.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from . import base, layout as L
from .base import Context, need_input
from .platform import com

ANCHORS = {0: "none", 1: "semi_fixed", 2: "fixed", 3: "locked"}  # EPcbAnchorType
SIDES = {1: "top", 512: "bottom"}  # EPcbSide
HELD = ("fixed", "locked")  # what Layout keeps where it is
NO_SHOVE = 3  # EditorControl.PartsOnlineDRC epcbECDRCPreventiveNoShovePart: refuse a spot that is taken, move nothing else
PLACEMENT_REFUSED = 10203  # Layout's error number when its placement check refuses a spot ("DRC violation")


@contextmanager
def _no_shove(doc: Any) -> Iterator[None]:
    """Layout's online DRC for parts set to refuse, not shove, while a part moves, and restored
    afterwards. Set to shove, as boards come, a move onto another part pushes that part aside,
    a change nobody asked for; set to refuse, the part lands where it was sent or not at all."""
    control, previous = None, None
    try:
        control = doc.EditorControl
        previous = int(control.PartsOnlineDRC)
        control.PartsOnlineDRC = NO_SHOVE
    except Exception:  # noqa: BLE001 - this release lacks the switch; Layout moves as it is set
        control = None
    try:
        yield
    finally:
        if control is not None and previous is not None:
            try:
                control.PartsOnlineDRC = previous
            except Exception:  # noqa: BLE001 - nothing more can be done
                pass


def natural(text: str) -> list[Any]:
    from .plan.bom import natural as key

    return key(text)


def component_key(refdes: str) -> str:
    return f"{L.COMPONENTS_KEY}/{L.segment(refdes)}"


def refdes_of(component: Any) -> str:
    """A component's reference designator: ``RefDes``, which every release has, else ``Name``."""
    try:
        return str(component.RefDes or "")
    except Exception:  # noqa: BLE001 - read the name instead
        return L.name_of(component)


def _anchor(component: Any) -> str:
    try:
        return ANCHORS.get(int(component.Anchor), "none")
    except Exception:  # noqa: BLE001 - an anchor that cannot be read
        return "none"


def board_row(component: Any) -> dict[str, Any]:
    """A component as component list shows it on a board."""
    values = L.component_values(component)
    return {"refdes": refdes_of(component), "part_number": str(component.PartNumber or ""), "cell": str(component.CellName or ""),
            "placed": values["placed"], "x_mm": values["x_mm"], "y_mm": values["y_mm"], "rotation_deg": values["rotation_deg"],
            "side": values.get("side"), "anchor": _anchor(component)}


def board_pins(component: Any) -> list[dict[str, Any]]:
    """Each pin with its net and, on a placed component, where it stands."""
    placed = bool(component.Placed)
    rows = []
    for pin in L.collection(component, "Pins"):
        row: dict[str, Any] = {"number": L.name_of(pin), "net": L.net_name(pin) or None, "x_mm": None, "y_mm": None}
        if placed:
            try:
                row["x_mm"] = round(float(pin.GetPositionX(L.UNIT_MM)), 4)
                row["y_mm"] = round(float(pin.GetPositionY(L.UNIT_MM)), 4)
            except Exception:  # noqa: BLE001 - a pin without a position
                pass
        rows.append(row)
    return sorted(rows, key=lambda r: natural(r["number"]))


def find(doc: Any, refdes: str) -> Any:
    """The component named ``refdes``: the exact name, then in any case; E_NOT_FOUND otherwise."""
    found = L.find_component(doc, refdes)
    if found is not None and refdes_of(found):
        return found
    matches = [c for c in L.collection(doc, "Components") if refdes_of(c).casefold() == refdes.casefold()]
    if len(matches) == 1:
        return matches[0]
    raise AdapterError("E_NOT_FOUND", f"The board has no component {refdes}; component list lists them.", dispatch_state="not_started")


def list_components(ctx: Context) -> dict[str, Any]:
    if ctx.inputs.get("sheet") is not None:
        raise AdapterError("E_VALIDATION", "--sheet applies to a schematic; a board has no sheets.", dispatch_state="not_started")

    def read(app: Any, doc: Any) -> dict[str, Any]:
        rows = [board_row(c) for c in L.collection(doc, "Components") if refdes_of(c)]
        return {"items": sorted(rows, key=lambda r: natural(r["refdes"]))}

    return L.in_layout(ctx, read, budget_s=L.READ_BUDGET_S, what="reading the components")


def get_component(ctx: Context) -> dict[str, Any]:
    refdes = str(need_input(ctx, "refdes"))

    def read(app: Any, doc: Any) -> dict[str, Any]:
        component = find(doc, refdes)
        return {**board_row(component), "pins": board_pins(component)}

    return L.in_layout(ctx, read, budget_s=L.READ_BUDGET_S, what="reading the component")


# ------------------------------------------------------------------ component move


def _move_inputs(ctx: Context) -> tuple[str, float | None, float | None, float | None, str | None]:
    refdes = str(need_input(ctx, "refdes"))
    x, y, rotation = ctx.inputs.get("x_mm"), ctx.inputs.get("y_mm"), ctx.inputs.get("rotation_deg")
    side = ctx.inputs.get("side")
    if side is not None:
        side = str(side).strip().lower()
        if side not in ("top", "bottom"):
            raise AdapterError("E_VALIDATION", "--side is top or bottom.", dispatch_state="not_started")
    if x is None and y is None and rotation is None and side is None:
        raise AdapterError("E_VALIDATION", "component move needs --x and --y, --rotation or --side.", dispatch_state="not_started")
    return (refdes, None if x is None else round(float(x), 4), None if y is None else round(float(y), 4),
            None if rotation is None else round(float(rotation) % 360.0, 4), side)


def _destination(component: Any, ctx: Context) -> tuple[dict[str, Any], dict[str, Any]]:
    """Where the component stands now and where the move puts it."""
    refdes, x, y, rotation, side = _move_inputs(ctx)
    now = board_row(component)
    if not now["placed"] and (x is None or y is None):
        raise AdapterError("E_VALIDATION", f"{now['refdes']} is not placed: give --x and --y to place it.", dispatch_state="not_started")
    goal = {"placed": True, "x_mm": now["x_mm"] if x is None else x, "y_mm": now["y_mm"] if y is None else y,
            "rotation_deg": (now["rotation_deg"] or 0.0) if rotation is None else rotation,
            "side": (now["side"] or "top") if side is None else side}
    return now, goal


def _changed(now: dict[str, Any], goal: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    out = {}
    for field in ("placed", "x_mm", "y_mm", "rotation_deg", "side"):
        before, after = now.get(field), goal[field]
        same = (before is not None and isinstance(after, float) and abs(float(before) - after) < 0.0005) or before == after
        if not same:
            out[field] = (before, after)
    return out


def _extents(component: Any) -> tuple[float, float, float, float] | None:
    try:
        return L.extrema_mm(component)
    except Exception:  # noqa: BLE001 - no extents
        return None


def _inside(doc: Any, component: Any) -> bool | None:
    box, board = _extents(component), None
    try:
        board = L.board_rect(doc)
    except Exception:  # noqa: BLE001 - a board without an outline
        return None
    if box is None:
        return None
    return board[0] - 0.001 <= box[0] and box[2] <= board[2] + 0.001 and board[1] - 0.001 <= box[1] and box[3] <= board[3] + 0.001


class ComponentMove(L.ReadsBack):
    """Put one component at a position, rotation and side: ``Move`` and ``Orientation`` for a
    placed component that stays on its side, ``Place`` for one that changes side or is not placed."""

    command = "component move"

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()

        def look(app: Any, doc: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            return _destination(find(doc, str(need_input(ctx, "refdes"))), ctx)

        now, goal = L.in_layout(ctx, look, budget_s=L.READ_BUDGET_S, what="reading the component")
        refdes = now["refdes"]
        domain = component_key(refdes)
        target = [base.target(path, "component", domain, label=refdes)]
        changes = _changed(now, goal)
        where = f"({goal['x_mm']:g}, {goal['y_mm']:g}) mm, {goal['rotation_deg']:g} degrees, on the {goal['side']}"
        if not changes:
            return base.preview(f"{refdes} stands at {where} already; nothing changes.", target, [])
        blocked = None
        if now["anchor"] in HELD:
            blocked = [f"{refdes} is {now['anchor']} in Layout, which keeps it where it is; a person releases it in Layout first"]
        verb = "Place" if not now["placed"] else "Move"
        return base.preview(f"{verb} {refdes} at {where}; a spot another component takes is refused, and no other component "
                            "moves. Traces on its pins stay where they are. The board is not saved.", target,
                            [base.change(base.key(path, domain), field, before, after) for field, (before, after) in changes.items()],
                            blocked_by=blocked)

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            component = find(doc, str(need_input(ctx, "refdes")))
            now, goal = _destination(component, ctx)
            if now["anchor"] in HELD:
                raise base.blocked(f"{now['refdes']} is {now['anchor']} in Layout, which keeps it where it is; a person releases it "
                                   "in Layout first.")
            if not _changed(now, goal):
                return base.facts("not_applied", "unchanged", result=self._result(doc, component))
            try:
                with _no_shove(doc):
                    if now["placed"] and now["side"] == goal["side"]:
                        if abs(goal["x_mm"] - now["x_mm"]) >= 0.0005 or abs(goal["y_mm"] - now["y_mm"]) >= 0.0005:
                            component.Move(goal["x_mm"], goal["y_mm"], L.UNIT_MM)
                        if abs(goal["rotation_deg"] - (now["rotation_deg"] or 0.0)) >= 0.0005:
                            component.Orientation = goal["rotation_deg"]
                    else:
                        component.Place(goal["x_mm"], goal["y_mm"], goal["rotation_deg"], goal["side"] == "top", 0, L.UNIT_MM, 0)
            except Exception as exc:  # noqa: BLE001 - classified
                if com.excepinfo_codes(exc)[0] == PLACEMENT_REFUSED and board_row(component) == now:
                    raise base.blocked(f"Layout's placement check refuses to put {now['refdes']} there: the spot overlaps another "
                                       "component. Nothing moved; component list shows where the "
                                       "others stand, so choose a free spot.", knowledge=["layout-placement-drc"],
                                       native_message=com.com_text(exc)) from None
                raise L.com_error(exc, f"moving {now['refdes']}", com.call_state(exc)) from None
            return base.facts("applied", "unchanged", result=self._result(doc, component))

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="moving the component")

    @staticmethod
    def _result(doc: Any, component: Any) -> dict[str, Any]:
        row = board_row(component)
        return {**{k: row[k] for k in ("refdes", "placed", "x_mm", "y_mm", "rotation_deg", "side")},
                "inside_board": _inside(doc, component)}


# ------------------------------------------------------------------ mounting holes

SPOT_TOLERANCE_MM = 0.01  # two holes this close stand on one spot


def hole_key(x: float, y: float) -> str:
    return f"/mounting-holes/{x:g},{y:g}"


def board_holes(doc: Any) -> list[tuple[Any, dict[str, Any]]]:
    """The board's own mounting holes, not those of a part's cell: each with its centre and padstack."""
    found = []
    for hole in L.collection(doc, "MountingHoles"):
        try:
            if hole.Component is not None:
                continue
            row = {"x_mm": round(float(hole.GetPositionX(L.UNIT_MM)), 4), "y_mm": round(float(hole.GetPositionY(L.UNIT_MM)), 4),
                   "padstack": L.name_of(hole.CurrentPadstack) or L.name_of(hole), "anchor": _anchor(hole)}
        except Exception:  # noqa: BLE001 - a hole that cannot be read is left alone
            continue
        found.append((hole, row))
    return found


def hole_at(doc: Any, spot: str) -> dict[str, Any]:
    """Whether a board mounting hole stands at ``"x,y"`` (mm), and its padstack: the reader ``read_keys`` uses."""
    x, y = (float(v) for v in spot.split(","))
    for _hole, row in board_holes(doc):
        if abs(row["x_mm"] - x) <= SPOT_TOLERANCE_MM and abs(row["y_mm"] - y) <= SPOT_TOLERANCE_MM:
            return {"exists": True, "padstack": row["padstack"]}
    return {"exists": False, "padstack": None}


def _library_hole(path: str, name: str) -> dict[str, Any] | None:
    """The central library's row of the padstack ``name``, which must be a mounting hole; None when
    the library cannot be read, such as one on a drive this machine does not have."""
    from .layout_rules import board_project
    from .library import default_cache, read_library
    from .plan import padstacks as PS

    try:
        prj, _design = board_project(path)
        library = read_library(Path(prj), default_cache(), kinds=("padstacks",), symbols=False)
    except AdapterError as exc:
        if exc.data.get("code") not in ("E_NOT_FOUND", "E_VALIDATION"):
            raise
        return None
    if library.failed:
        return None
    if name not in library.padstacks.get("padstacks", {}):
        raise AdapterError("E_NOT_FOUND", f"The central library holds no padstack {name}; padstack list --type mounting_hole "
                           "lists its mounting holes.", dispatch_state="not_started")
    row = PS.row(library.padstacks, name)
    if row["type"] != "mounting_hole":
        raise AdapterError("E_VALIDATION", f"{name} is a padstack of type {row['type']}, not a mounting hole; padstack list --type "
                           "mounting_hole lists the mounting holes.", dispatch_state="not_started")
    return row


def _hole_spots(doc: Any, ctx: Context, drill: float | None) -> list[tuple[float, float]]:
    """The centres the holes go to, each inside the board outline by at least the hole's radius."""
    corners = bool(ctx.inputs.get("corners"))
    x, y, inset = ctx.inputs.get("x_mm"), ctx.inputs.get("y_mm"), ctx.inputs.get("inset_mm")
    if corners and (x is not None or y is not None):
        raise AdapterError("E_VALIDATION", "Give --x and --y for one hole, or --corners for one in each corner, not both.",
                           dispatch_state="not_started")
    if not corners and (x is None or y is None):
        raise AdapterError("E_VALIDATION", "mounting-hole create needs --x and --y for one hole, or --corners with --inset for "
                           "one in each corner of the board.", dispatch_state="not_started")
    if corners and (inset is None or float(inset) <= 0):
        raise AdapterError("E_VALIDATION", "--corners needs --inset: how far each hole's centre lies in from the board's edges.",
                           dispatch_state="not_started")
    x0, y0, x1, y1 = L.board_rect(doc)
    if corners:
        d = float(inset)
        spots = [(x0 + d, y0 + d), (x1 - d, y0 + d), (x1 - d, y1 - d), (x0 + d, y1 - d)]
    else:
        spots = [(float(x), float(y))]
    spots = [(round(sx, 4), round(sy, 4)) for sx, sy in spots]
    radius = (drill or 0.0) / 2
    for sx, sy in spots:
        if not (x0 + radius <= sx <= x1 - radius and y0 + radius <= sy <= y1 - radius):
            raise AdapterError("E_VALIDATION", f"A hole at ({sx:g}, {sy:g}) mm would reach past the board outline ({x0:g}, {y0:g} to "
                               f"{x1:g}, {y1:g} mm); give a spot, or an --inset, further in.", dispatch_state="not_started")
    return spots


def _hole_look(doc: Any, path: str, ctx: Context) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    """The padstack, its library row, and for each spot the hole that stands there now, if any."""
    name = str(need_input(ctx, "padstack"))
    row = _library_hole(path, name)
    if row is None:
        from .layout_edit import board_padstack

        if board_padstack(doc, name) is None:
            raise AdapterError("E_NOT_FOUND", f"The central library cannot be read and the board has no padstack {name}.",
                               dispatch_state="not_started")
    holes = board_holes(doc)
    plan = []
    for sx, sy in _hole_spots(doc, ctx, (row or {}).get("drill_mm")):
        there = next((r for _h, r in holes if abs(r["x_mm"] - sx) <= SPOT_TOLERANCE_MM and abs(r["y_mm"] - sy) <= SPOT_TOLERANCE_MM),
                     None)
        plan.append({"x_mm": sx, "y_mm": sy, "there": there})
    return name, row, plan


class MountingHoleList(base.Handler):
    command = "mounting-hole list"

    def read(self, ctx: Context) -> dict[str, Any]:
        rows = L.in_layout(ctx, lambda app, doc: [row for _h, row in board_holes(doc)], budget_s=L.READ_BUDGET_S,
                           what="reading the mounting holes")
        return {"items": sorted(rows, key=lambda r: (r["x_mm"], r["y_mm"]))}


class MountingHoleCreate(L.ReadsBack):
    """Mounting holes of one padstack of the central library, at one spot or in the four corners of
    the board outline: ``Document.PutMountingHoleEx``, which takes the padstack from the library when
    the board lacks it."""

    command = "mounting-hole create"

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        name, row, plan = L.in_layout(ctx, lambda app, doc: _hole_look(doc, path, ctx), budget_s=L.READ_BUDGET_S,
                                      what="reading the mounting holes")
        targets, changes, blocked, kept = [], [], [], []
        for spot in plan:
            domain = hole_key(spot["x_mm"], spot["y_mm"])
            targets.append(base.target(path, "hole", domain, label=f"mounting hole at {spot['x_mm']:g},{spot['y_mm']:g} mm"))
            there = spot["there"]
            if there is None:
                changes.append(base.change(base.key(path, domain), "exists", False, True))
            elif there["padstack"] == name:
                kept.append(f"({spot['x_mm']:g}, {spot['y_mm']:g})")
            else:
                blocked.append(f"a mounting hole of {there['padstack']} stands at ({spot['x_mm']:g}, {spot['y_mm']:g}) mm already; "
                               "delete it first with mounting-hole delete")
        new = [f"({s['x_mm']:g}, {s['y_mm']:g})" for s in plan if s["there"] is None]
        if not new:
            return base.preview(f"The board has these mounting holes of {name} already; nothing changes.", targets, [],
                                blocked_by=blocked or None)
        hole = ""
        if row and row.get("drill_mm"):
            hole = f", a {row['drill_mm']:g} mm {'plated' if row.get('plated') else 'unplated'} hole,"
        action = f"Place mounting holes of {name}{hole} at {', '.join(new)} mm."
        if kept:
            action += f" The board has one at {', '.join(kept)} mm already."
        return base.preview(action + " The board is not saved.", targets, changes, blocked_by=blocked or None)

    def apply(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()

        def work(app: Any, doc: Any) -> dict[str, Any]:
            name, row, plan = _hole_look(doc, path, ctx)
            if any(s["there"] is not None and s["there"]["padstack"] != name for s in plan):
                raise base.blocked("A mounting hole of another padstack stands on one of the spots; delete it first with "
                                   "mounting-hole delete.")
            placed: list[dict[str, Any]] = []
            try:
                for spot in plan:
                    if spot["there"] is None:
                        doc.PutMountingHoleEx(spot["x_mm"], spot["y_mm"], name, True, 0, False, None, None, 0, L.UNIT_MM)
                    placed.append({"x_mm": spot["x_mm"], "y_mm": spot["y_mm"], "created": spot["there"] is None})
            except Exception as exc:  # noqa: BLE001 - classified
                done = sum(1 for p in placed if p["created"])
                raise L.com_error(exc, f"placing the mounting holes ({done} placed)",
                                  com.call_state(exc) if not done else "dispatched") from None
            holes = [{**p, "exists": hole_at(doc, f"{p['x_mm']},{p['y_mm']}")["exists"]} for p in placed]
            result = {"padstack": name, "drill_mm": (row or {}).get("drill_mm"), "plated": (row or {}).get("plated"), "holes": holes}
            return base.facts("applied" if any(p["created"] for p in placed) else "not_applied", "unchanged", result=result)

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="placing the mounting holes")


def _holes_to_delete(doc: Any, ctx: Context) -> list[dict[str, Any]]:
    holes = board_holes(doc)
    x, y = ctx.inputs.get("x_mm"), ctx.inputs.get("y_mm")
    if ctx.inputs.get("all"):
        if x is not None or y is not None:
            raise AdapterError("E_VALIDATION", "Give --all, or --x and --y, not both.", dispatch_state="not_started")
        return [row for _h, row in holes]
    if x is None or y is None:
        raise AdapterError("E_VALIDATION", "mounting-hole delete needs --x and --y, a point inside the hole, or --all.",
                           dispatch_state="not_started")
    hits = []
    for hole, row in holes:
        try:
            x0, y0, x1, y1 = L.extrema_mm(hole)
        except Exception:  # noqa: BLE001 - its centre alone
            x0, y0, x1, y1 = row["x_mm"], row["y_mm"], row["x_mm"], row["y_mm"]
        t = SPOT_TOLERANCE_MM
        if x0 - t <= float(x) <= x1 + t and y0 - t <= float(y) <= y1 + t:
            hits.append(row)
    if not hits:
        raise AdapterError("E_NOT_FOUND", f"No mounting hole of the board holds ({float(x):g}, {float(y):g}) mm.",
                           dispatch_state="not_started")
    if len(hits) > 1:
        raise AdapterError("E_AMBIGUOUS", f"{len(hits)} mounting holes hold ({float(x):g}, {float(y):g}) mm; give a point inside one "
                           "only.", dispatch_state="not_started", candidates=[hole_key(r["x_mm"], r["y_mm"]) for r in hits])
    return hits


def _held_holes(rows: list[dict[str, Any]]) -> list[str]:
    return [f"the mounting hole at ({r['x_mm']:g}, {r['y_mm']:g}) mm is {r['anchor']} in Layout, which keeps it; a person "
            "releases it in Layout first" for r in rows if r["anchor"] in HELD]


class MountingHoleDelete(L.ReadsBack):
    """Delete the board's mounting hole that holds a point, or all of them; the holes of a part's cell stay."""

    command = "mounting-hole delete"

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        rows = L.in_layout(ctx, lambda app, doc: _holes_to_delete(doc, ctx), budget_s=L.READ_BUDGET_S,
                           what="finding the mounting holes")
        targets = [base.target(path, "hole", hole_key(r["x_mm"], r["y_mm"]),
                               label=f"mounting hole at {r['x_mm']:g},{r['y_mm']:g} mm") for r in rows]
        if not rows:
            return base.preview("The board has no mounting holes of its own; nothing changes.", targets, [])
        changes = [base.change(base.key(path, hole_key(r["x_mm"], r["y_mm"])), "exists", True, False) for r in rows]
        where = ", ".join(f"({r['x_mm']:g}, {r['y_mm']:g})" for r in rows)
        return base.preview(f"Delete the mounting holes at {where} mm. The board is not saved.", targets, changes,
                            blocked_by=_held_holes(rows) or None)

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            rows = _holes_to_delete(doc, ctx)
            held = _held_holes(rows)
            if held:
                raise base.blocked(held[0] + ".")
            wanted = {(r["x_mm"], r["y_mm"]) for r in rows}
            deleted: list[dict[str, Any]] = []
            try:
                for hole, row in board_holes(doc):
                    if (row["x_mm"], row["y_mm"]) in wanted:
                        hole.Delete()
                        deleted.append({k: row[k] for k in ("x_mm", "y_mm", "padstack")})
            except Exception as exc:  # noqa: BLE001 - classified
                raise L.com_error(exc, f"deleting the mounting holes ({len(deleted)} deleted)",
                                  com.call_state(exc) if not deleted else "dispatched") from None
            return base.facts("applied" if deleted else "not_applied", "unchanged", result={"deleted": deleted})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="deleting the mounting holes")


HANDLERS: list[base.Handler] = [ComponentMove(), MountingHoleList(), MountingHoleCreate(), MountingHoleDelete()]
