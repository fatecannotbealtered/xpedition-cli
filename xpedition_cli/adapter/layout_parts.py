"""Components on the open board: reading them (component list and component get on a board) and
moving them (component move); the board's mounting holes (mounting-hole list, create and delete); and the
reference designators on the silkscreen (refdes move).

Positions are board coordinates in mm: a component's position is its cell origin, as Layout
places it; its rotation is counter-clockwise in degrees, seen from the top.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from . import base, layout as L
from .base import Context, need_input
from .platform import com

ANCHORS = {0: "none", 1: "semi_fixed", 2: "fixed", 3: "locked"}  # EPcbAnchorType
ANCHOR_CODES = {name: code for code, name in ANCHORS.items()}
SIDES = {1: "top", 512: "bottom"}  # EPcbSide
HELD = ("fixed", "locked")  # what Layout keeps where it is
LISTED_PLACES = 5  # other places a part fits its marks, listed
SAME_SPOT_MM = 0.05  # another fit this close to the chosen one is the part turned on its spot: its marks do not show pin 1
MARK_SLACK_MM = 0.05  # a placement keepout this far past the part's footprint still lies inside it
NO_SHOVE = 3  # EditorControl.PartsOnlineDRC epcbECDRCPreventiveNoShovePart: refuse a spot that is taken, move nothing else
DRC_OFF = 0  # epcbECDRCOff: lets a part over its structure marks, once the adapter has checked nothing else is in the way
PLACEMENT_REFUSED = 10203  # Layout's error number when its placement check refuses a spot ("DRC violation")


@contextmanager
def _parts_drc(doc: Any, mode: int = NO_SHOVE) -> Iterator[None]:
    """Layout's online DRC for parts set to refuse, not shove, while a part moves, and restored
    afterwards. Set to shove, as boards come, a move onto another part pushes that part aside,
    a change nobody asked for; set to refuse, the part lands where it was sent or not at all.
    A move onto structure marks sets it off, after checking that nothing but the marks is in the way."""
    control, previous = None, None
    try:
        control = doc.EditorControl
        previous = int(control.PartsOnlineDRC)
        control.PartsOnlineDRC = mode
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
    """The component named ``refdes``: the exact name, then in any case; E_AMBIGUOUS when several
    differ only in case, E_NOT_FOUND when none matches."""
    found = L.find_component(doc, refdes)
    if found is not None and refdes_of(found):
        return found
    matches = [c for c in L.collection(doc, "Components") if refdes_of(c).casefold() == refdes.casefold()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = sorted(refdes_of(c) for c in matches)
        raise AdapterError("E_AMBIGUOUS", f"{refdes} matches the components {', '.join(names)}, which differ only in case; give "
                           "the reference designator exactly.", dispatch_state="not_started",
                           candidates=[{"refdes": name} for name in names])
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
    if ctx.inputs.get("to_marks"):
        if x is not None or y is not None:
            raise AdapterError("E_VALIDATION", "--to-marks finds the place from the marks; leave out --x and --y.",
                               dispatch_state="not_started")
    elif x is None and y is None and rotation is None and side is None:
        raise AdapterError("E_VALIDATION", "component move needs --x and --y, --rotation or --side.", dispatch_state="not_started")
    return (refdes, None if x is None else round(float(x), 4), None if y is None else round(float(y), 4),
            None if rotation is None else round(float(rotation) % 360.0, 4), side)


def _destination(component: Any, ctx: Context, doc: Any = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Where the component stands now, where the move puts it and, with --to-marks, the marks it fits."""
    refdes, x, y, rotation, side = _move_inputs(ctx)
    now = board_row(component)
    if ctx.inputs.get("to_marks"):
        fit = _mark_fit(doc, component, now, side, rotation)
        goal = {"placed": True, "x_mm": fit["x_mm"], "y_mm": fit["y_mm"], "rotation_deg": fit["rotation_deg"], "side": now["side"]}
        return now, goal, fit
    if not now["placed"] and (x is None or y is None):
        raise AdapterError("E_VALIDATION", f"{now['refdes']} is not placed: give --x and --y to place it.", dispatch_state="not_started")
    if now["placed"] and now["side"] is None and side is None:
        raise AdapterError("E_VALIDATION", f"Layout does not say which side {now['refdes']} is on; give --side to keep it there.",
                           dispatch_state="not_started")
    goal = {"placed": True, "x_mm": now["x_mm"] if x is None else x, "y_mm": now["y_mm"] if y is None else y,
            "rotation_deg": (now["rotation_deg"] or 0.0) if rotation is None else rotation,
            "side": (now["side"] or "top") if side is None else side}
    return now, goal, None


def _shape(record: dict[str, Any] | None) -> tuple[float, float, float] | None:
    from .plan import marks as M

    if record is None:
        return None
    return M.circle_shape(*record["circle"]) if "circle" in record else M.path_shape(record["path"])


def _pad_shapes(doc: Any, component: Any, side: str) -> list[tuple[str, tuple[float, float, float]]]:
    """Each pad of the part on the outer layer of its side: its pin, centroid and area, in mm."""
    from .layout_output import geometry_record

    scale = L.geometry_scale(doc)
    layer = 1 if side == "top" else int(doc.LayerCount)
    shapes = []
    for pin in L.collection(component, "Pins"):
        for pad in L.collection(pin, "Pads"):
            if int(pad.Layer) != layer:
                continue
            for geometry in L.collection(pad, "Geometries"):
                shape = _shape(geometry_record(geometry, scale)) if scale else None
                if shape is not None:
                    shapes.append((L.name_of(pin), shape))
    return shapes


def _mark_shapes(doc: Any, side: str) -> list[tuple[float, float, float]]:
    """The placement keepouts on ``side``: each one's centroid and area."""
    from .layout_keepout import placement_keepouts
    from .layout_output import geometry_record

    scale = L.geometry_scale(doc)
    out = []
    for item, where, _box, _height in placement_keepouts(doc):
        if where != side or not scale:
            continue
        try:
            shape = _shape(geometry_record(item.Geometry, scale))
        except Exception:  # noqa: BLE001 - a keepout without a shape is no mark
            continue
        if shape is not None:
            out.append(shape)
    return out


def _mark_fit(doc: Any, component: Any, now: dict[str, Any], side: str | None, rotation: float | None) -> dict[str, Any]:
    """Where the part's pads lie on placement keepouts of the same shapes on its side, a structure
    drawing's marks: the fit nearest where the part stands, at ``rotation`` when given."""
    from .plan import marks as M

    if not now["placed"] or now["side"] is None:
        raise AdapterError("E_VALIDATION", f"{now['refdes']} is not placed: place it near its marks with --x and --y first, then "
                           "move it with --to-marks.", dispatch_state="not_started")
    if side is not None and side != now["side"]:
        raise AdapterError("E_VALIDATION", f"--to-marks keeps {now['refdes']} on the {now['side']}; move it to the {side} first.",
                           dispatch_state="not_started")
    pads = _pad_shapes(doc, component, now["side"])
    marks = _mark_shapes(doc, now["side"])
    best, others = M.choose(M.fits(pads, marks), now["x_mm"], now["y_mm"], now["rotation_deg"] or 0.0, rotation)
    if best is None:
        at = f" at {rotation:g} degrees" if rotation is not None else ""
        raise AdapterError("E_NOT_FOUND", f"No placement keepouts on the {now['side']} match {M.MIN_PADS} or more of {now['refdes']}'s "
                           f"{len(pads)} pads in shape and spacing{at}; the board has {len(marks)} placement keepouts there. Give "
                           "--x, --y and --rotation instead.", dispatch_state="not_started")
    return {"x_mm": best["x_mm"], "y_mm": best["y_mm"], "rotation_deg": best["rotation_deg"],
            "pins": sorted(best["matched"], key=natural), "pads": len(pads), "off_mm": round(best["residual_mm"], 4),
            "other_places": [{**{k: o[k] for k in ("x_mm", "y_mm", "rotation_deg")},
                              "same_spot": math.hypot(o["x_mm"] - best["x_mm"], o["y_mm"] - best["y_mm"]) < SAME_SPOT_MM}
                             for o in others[:LISTED_PLACES]]}


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


def _place(doc: Any, component: Any, now: dict[str, Any], goal: dict[str, Any]) -> None:
    """``Place`` the part at the goal. Layout's placement check refuses a placed part that changes
    side in place, where its own footprint lies; lifted off the board first, it takes the place. A
    part refused again goes back where it was, and the refusal stands."""
    keep = ANCHOR_CODES.get(now["anchor"], 0)  # a semi-fixed part stays semi-fixed
    try:
        component.Place(goal["x_mm"], goal["y_mm"], goal["rotation_deg"], goal["side"] == "top", keep, L.UNIT_MM, 0)
        return
    except Exception as exc:  # noqa: BLE001 - classified by the caller
        if not now["placed"] or com.excepinfo_codes(exc)[0] != PLACEMENT_REFUSED or board_row(component) != now:
            raise
        refused = exc
    component.Delete()  # unplaces the part: its own footprint no longer stands in the way
    try:
        component.Place(goal["x_mm"], goal["y_mm"], goal["rotation_deg"], goal["side"] == "top", keep, L.UNIT_MM, 0)
    except Exception:  # noqa: BLE001 - put it back where it was, and report the refusal
        try:
            component.Place(now["x_mm"], now["y_mm"], now["rotation_deg"] or 0.0, now["side"] == "top", keep, L.UNIT_MM, 0)
        except Exception as exc:  # noqa: BLE001 - the part is left off the board
            raise AdapterError("E_NATIVE_FAILURE", f"{now['refdes']} was lifted to change side, Layout refused it at the new place and "
                               f"would not put it back: it is not placed now. Place it again with component move --x {now['x_mm']:g}mm "
                               f"--y {now['y_mm']:g}mm --side {now['side']}.", dispatch_state="dispatched",
                               native_message=com.com_text(exc)) from None
        raise refused from None


def _destination_box(component: Any, now: dict[str, Any], goal: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Roughly where the part would stand at the goal: its extents taken about its origin, turned
    by the change of rotation and mirrored for a change of side."""
    box = _extents(component)
    if box is None or not now["placed"] or now["x_mm"] is None:
        return None
    corners = [(x - now["x_mm"], y - now["y_mm"]) for x in (box[0], box[2]) for y in (box[1], box[3])]
    if goal["side"] != now["side"]:
        corners = [(-x, y) for x, y in corners]
    turn = math.radians(goal["rotation_deg"] - (now["rotation_deg"] or 0.0))
    c, s_ = math.cos(turn), math.sin(turn)
    moved = [(goal["x_mm"] + x * c - y * s_, goal["y_mm"] + x * s_ + y * c) for x, y in corners]
    xs, ys = [p[0] for p in moved], [p[1] for p in moved]
    return min(xs), min(ys), max(xs), max(ys)


def _in_the_way(doc: Any, component: Any, side: str, box: tuple[float, float, float, float], marks: bool = False) -> list[str]:
    """What lies under ``box`` on ``side`` that Layout's placement check may refuse: other placed
    parts, vias of nets the part has no pin of, traces of such nets on the side's outer layer, and
    placement keepouts on that side; with ``marks``, only the keepouts that reach past the box,
    since those inside are the structure's marks the part goes over."""
    def overlaps(other: tuple[float, ...]) -> bool:
        return other[0] < box[2] and box[0] < other[2] and other[1] < box[3] and box[1] < other[3]

    mine = refdes_of(component)
    own_nets = {L.net_name(pin) for pin in L.collection(component, "Pins")}
    layer = 1 if side == "top" else int(doc.LayerCount)
    found: list[str] = []
    parts = []
    for other in L.collection(doc, "Components"):
        try:
            if refdes_of(other) != mine and bool(other.Placed) and SIDES.get(int(other.Side)) == side and overlaps(L.extrema_mm(other)):
                parts.append(refdes_of(other))
        except Exception:  # noqa: BLE001 - a part that cannot be read is left out
            continue
    if parts:
        found.append("part " + ", ".join(sorted(parts)[:5]))
    vias = Counter(L.net_name(v) for v in L.collection(doc, "Vias") if L.net_name(v) not in own_nets and overlaps(L.extrema_mm(v)))
    if vias:
        found.append("vias of " + ", ".join(f"{net} x{n}" if n > 1 else net for net, n in vias.most_common(5)))
    traces = Counter(L.net_name(t) for t in L.collection(doc, "Traces")
                     if int(t.Layer) == layer and L.net_name(t) not in own_nets and overlaps(L.extrema_mm(t)))
    if traces:
        found.append(f"traces on layer {layer} of " + ", ".join(net for net, _n in traces.most_common(5)))
    try:
        from .layout_keepout import placement_keepouts

        keepouts = [b for _i, where, b, _h in placement_keepouts(doc) if where == side and overlaps(b)
                    and not (marks and _within(b, box))]
    except Exception:  # noqa: BLE001 - keepouts that cannot be read are left out
        keepouts = []
    if keepouts:
        found.append(f"{len(keepouts)} placement keepout(s), such as {keepouts[0][0]:g},{keepouts[0][1]:g} to {keepouts[0][2]:g},"
                     f"{keepouts[0][3]:g} mm")
    return found


def _within(inner: tuple[float, ...], outer: tuple[float, ...]) -> bool:
    s = MARK_SLACK_MM
    return outer[0] - s <= inner[0] and inner[2] <= outer[2] + s and outer[1] - s <= inner[1] and inner[3] <= outer[3] + s


class ComponentMove(L.ReadsBack):
    """Put one component at a position, rotation and side: ``Move`` and ``Orientation`` for a
    placed component that stays on its side, ``Place`` for one that changes side or is not placed."""

    command = "component move"

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()

        def look(app: Any, doc: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
            return _destination(find(doc, str(need_input(ctx, "refdes"))), ctx, doc)

        now, goal, fit = L.in_layout(ctx, look, budget_s=L.READ_BUDGET_S, what="reading the component")
        refdes = now["refdes"]
        domain = component_key(refdes)
        target = [base.target(path, "component", domain, label=refdes)]
        changes = _changed(now, goal)
        where = f"({goal['x_mm']:g}, {goal['y_mm']:g}) mm, {goal['rotation_deg']:g} degrees, on the {goal['side']}"
        fits = ""
        if fit is not None:
            fits = (f", where {len(fit['pins'])} of its {fit['pads']} pads lie on placement keepouts of the same shapes, the "
                    f"structure's marks ({fit['off_mm']:g} mm off on average)")
        if not changes:
            return base.preview(f"{refdes} stands at {where}{fits} already; nothing changes.", target, [])
        blocked = None
        if now["anchor"] in HELD:
            blocked = [f"{refdes} is {now['anchor']} in Layout, which keeps it where it is; a person releases it in Layout first"]
        verb = "Place" if not now["placed"] else "Move"
        over = ""
        if fit is not None:
            over = " It goes over the placement keepouts inside its footprint there; anything else in the way refuses the move."
            if fit["other_places"]:
                over += " It fits as well at " + "; ".join(f"({o['x_mm']:g}, {o['y_mm']:g}) mm, {o['rotation_deg']:g} degrees"
                                                           for o in fit["other_places"]) + "."
        return base.preview(f"{verb} {refdes} at {where}{fits}; a spot another component takes is refused, and no other component "
                            f"moves.{over} Traces on its pins stay where they are. The board is not saved.", target,
                            [base.change(base.key(path, domain), field, before, after) for field, (before, after) in changes.items()],
                            blocked_by=blocked)

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            component = find(doc, str(need_input(ctx, "refdes")))
            now, goal, fit = _destination(component, ctx, doc)
            if now["anchor"] in HELD:
                raise base.blocked(f"{now['refdes']} is {now['anchor']} in Layout, which keeps it where it is; a person releases it "
                                   "in Layout first.")
            if not _changed(now, goal):
                return base.facts("not_applied", "unchanged", result=self._result(doc, component, fit))
            box = _destination_box(component, now, goal)
            mode = NO_SHOVE
            if fit is not None and box is not None:
                found = _in_the_way(doc, component, goal["side"], box, marks=True)
                if found:
                    raise base.blocked(f"Besides its marks, under the place they give {now['refdes']} lie: {'; '.join(found)}. Nothing "
                                       "moved; move or delete what is in the way.", knowledge=["layout-placement-drc"])
                mode = DRC_OFF
            try:
                with _parts_drc(doc, mode):
                    if now["placed"] and now["side"] == goal["side"]:
                        if abs(goal["x_mm"] - now["x_mm"]) >= 0.0005 or abs(goal["y_mm"] - now["y_mm"]) >= 0.0005:
                            component.Move(goal["x_mm"], goal["y_mm"], L.UNIT_MM)
                        if abs(goal["rotation_deg"] - (now["rotation_deg"] or 0.0)) >= 0.0005:
                            component.Orientation = goal["rotation_deg"]
                    else:
                        _place(doc, component, now, goal)
            except Exception as exc:  # noqa: BLE001 - classified
                if com.excepinfo_codes(exc)[0] == PLACEMENT_REFUSED and board_row(component) == now:
                    found = _in_the_way(doc, component, goal["side"], box) if box is not None else []
                    what = ("; under the place it would take: " + "; ".join(found)) if found else ""
                    marks = (" When those keepouts are a structure drawing's marks of its pads, component move --to-marks puts it on "
                             "them.") if any("placement keepout" in f for f in found) else ""
                    raise base.blocked(f"Layout's placement check (online DRC) refuses to put {now['refdes']} at that place on the "
                                       f"{goal['side']}{what}. Nothing moved; move or delete what is in the way, or choose another "
                                       f"spot.{marks}", knowledge=["layout-placement-drc"], native_message=com.com_text(exc)) from None
                raise L.com_error(exc, f"moving {now['refdes']}", com.call_state(exc)) from None
            return base.facts("applied", "unchanged", result=self._result(doc, component, fit))

        # Layout refuses a taken spot only in a transaction with DRC; over its marks the adapter has checked the way itself
        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="moving the component",
                           checked=not ctx.inputs.get("to_marks"))

    @staticmethod
    def _result(doc: Any, component: Any, fit: dict[str, Any] | None = None) -> dict[str, Any]:
        row = board_row(component)
        result = {**{k: row[k] for k in ("refdes", "placed", "x_mm", "y_mm", "rotation_deg", "side")},
                  "inside_board": _inside(doc, component)}
        if fit is not None:
            result["marks"] = {k: fit[k] for k in ("pins", "pads", "off_mm", "other_places")}
        return result


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


def _hole_radius(row: dict[str, Any] | None) -> float:
    """Half the widest of the padstack's drill and pad, as far as the library tells them."""
    sizes = [float(v) for v in ((row or {}).get("drill_mm"), (row or {}).get("pad_mm"), (row or {}).get("pad_height_mm"))
             if isinstance(v, (int, float))]
    return max(sizes, default=0.0) / 2


def _corner_radius(doc: Any) -> float:
    """The board outline's corner radius in mm; 0 when the corners are square or cannot be measured."""
    try:
        return float(L.outline_radius(doc, doc.BoardOutline) or 0.0)
    except Exception:  # noqa: BLE001 - square corners are assumed
        return 0.0


def _off_board(spot: tuple[float, float], rect: tuple[float, float, float, float], corner: float, radius: float) -> bool:
    """Whether a hole of ``radius`` at ``spot`` reaches past the outline: its edges, or the arc of a
    rounded corner when the spot lies in that corner's square."""
    sx, sy = spot
    x0, y0, x1, y1 = rect
    if not (x0 + radius <= sx <= x1 - radius and y0 + radius <= sy <= y1 - radius):
        return True
    if corner <= 0:
        return False
    cx = x0 + corner if sx < x0 + corner else x1 - corner if sx > x1 - corner else None
    cy = y0 + corner if sy < y0 + corner else y1 - corner if sy > y1 - corner else None
    return cx is not None and cy is not None and ((sx - cx) ** 2 + (sy - cy) ** 2) ** 0.5 > corner - radius + 1e-6


def _hole_spots(doc: Any, ctx: Context, row: dict[str, Any] | None) -> list[tuple[float, float]]:
    """The centres the holes go to, each inside the board outline, rounded corners included, by at
    least half the hole's drill or pad, whichever is wider."""
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
    rect = L.board_rect(doc)
    x0, y0, x1, y1 = rect
    if corners:
        d = float(inset)
        if 2 * d >= min(x1 - x0, y1 - y0):
            raise AdapterError("E_VALIDATION", f"--inset {d:g}mm is half the board's {min(x1 - x0, y1 - y0):g} mm side or more, so "
                               "the corner holes would meet or cross; give a smaller --inset.", dispatch_state="not_started")
        spots = [(x0 + d, y0 + d), (x1 - d, y0 + d), (x1 - d, y1 - d), (x0 + d, y1 - d)]
    else:
        spots = [(float(x), float(y))]
    spots = [(round(sx, 4), round(sy, 4)) for sx, sy in spots]
    radius, corner = _hole_radius(row), _corner_radius(doc)
    for sx, sy in spots:
        if _off_board((sx, sy), rect, corner, radius):
            rounded = f" with corners of {corner:g} mm radius" if corner > 0 else ""
            raise AdapterError("E_VALIDATION", f"A hole {2 * radius:g} mm wide at ({sx:g}, {sy:g}) mm would reach past the board "
                               f"outline ({x0:g}, {y0:g} to {x1:g}, {y1:g} mm{rounded}); give a spot, or an --inset, further in.",
                               dispatch_state="not_started")
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
    for sx, sy in _hole_spots(doc, ctx, row):
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


# ------------------------------------------------------------------ refdes move

FAB_SILKSCREEN, TEXT_REFDES = 2, 1  # FabricationLayerText.Type and .TextType of a silkscreen reference designator
FIX_LOCKS = {0: "none", 1: "semi_fixed", 2: "fixed", 3: "locked"}  # EPcbFixLockType
GAP_MM = 0.2  # between a part's extents and its reference designator placed beside it
EDGE_MM = 0.1  # a reference designator keeps this far inside the board outline


def refdes_key(refdes: str) -> str:
    return f"/refdes/{L.segment(refdes)}"


def silk_refdes(component: Any) -> Any | None:
    """The component's reference designator on the silkscreen; its cell may have none."""
    for text in L.collection(component, "FabricationLayerTexts"):
        try:
            if int(text.Type) == FAB_SILKSCREEN and int(text.TextType) == TEXT_REFDES:
                return text
        except Exception:  # noqa: BLE001 - a text that cannot be read is not it
            continue
    return None


def text_values(text: Any) -> dict[str, Any]:
    """Where a text stands: its anchor in mm, its rotation and its side."""
    try:
        lock = FIX_LOCKS.get(int(text.FixLock), "none")
    except Exception:  # noqa: BLE001 - no lock to read
        lock = "none"
    return {"x_mm": round(float(text.GetPositionX(L.UNIT_MM)), 4), "y_mm": round(float(text.GetPositionY(L.UNIT_MM)), 4),
            "rotation_deg": round(float(text.Format.Orientation or 0.0) % 360.0, 4), "side": SIDES.get(int(text.Side)),
            "anchor": lock}


def refdes_at(doc: Any, refdes: str) -> dict[str, Any]:
    """Where a component's silkscreen reference designator stands: the reader ``read_keys`` uses."""
    text = silk_refdes(find(doc, refdes))
    return {"exists": False} if text is None else {"exists": True, **text_values(text)}


def _overlap(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _obstacles(doc: Any, component: Any, side: str) -> list[tuple[str, tuple[float, float, float, float]]]:
    """What a reference designator on ``side`` keeps clear of: the placed parts on that side, its own
    included, the other parts' silkscreen reference designators, and the board's mounting holes."""
    mine = refdes_of(component)
    found = []
    for other in L.collection(doc, "Components"):
        name = refdes_of(other)
        try:
            if not name or not bool(other.Placed) or SIDES.get(int(other.Side)) != side:
                continue
            found.append((name, L.extrema_mm(other)))  # its own part too: a reference designator over its pads is a fault
            text = silk_refdes(other)
            if text is not None and name != mine:
                found.append((f"{name} reference designator", L.extrema_mm(text)))
        except Exception:  # noqa: BLE001 - what cannot be read is left out
            continue
    for hole, row in board_holes(doc):
        try:
            found.append((f"mounting hole at {row['x_mm']:g},{row['y_mm']:g}", L.extrema_mm(hole)))
        except Exception:  # noqa: BLE001 - no extents
            continue
    return found


def _text_box(text: Any, x: float, y: float, rotation: float) -> tuple[float, float, float, float]:
    """The box the text takes with its anchor at (x, y) and the rotation given: the text turns
    about its anchor, so the box's offset from the anchor turns with it."""
    x0, y0, x1, y1 = L.extrema_mm(text)
    now = text_values(text)
    turn = math.radians(rotation - now["rotation_deg"])
    cos, sin = math.cos(turn), math.sin(turn)
    width = abs((x1 - x0) * cos) + abs((y1 - y0) * sin)
    height = abs((x1 - x0) * sin) + abs((y1 - y0) * cos)
    ox, oy = (x0 + x1) / 2 - now["x_mm"], (y0 + y1) / 2 - now["y_mm"]  # the box centre from the anchor, as it stands
    cx, cy = x + ox * cos - oy * sin, y + ox * sin + oy * cos
    return (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)


def _free_spot(doc: Any, component: Any, text: Any, rotation: float) -> tuple[float, float] | None:
    """An anchor beside the part where its reference designator is clear of the other parts, their
    reference designators and the mounting holes, inside the outline: above, below, right, left,
    then each again one text height further out."""
    px0, py0, px1, py1 = L.extrema_mm(component)
    side = SIDES.get(int(component.Side)) or "top"
    obstacles = [box for _name, box in _obstacles(doc, component, side)]
    bx0, by0, bx1, by1 = L.board_rect(doc)
    now = text_values(text)
    probe = _text_box(text, now["x_mm"], now["y_mm"], rotation)
    width, height = probe[2] - probe[0], probe[3] - probe[1]
    dx, dy = now["x_mm"] - (probe[0] + probe[2]) / 2, now["y_mm"] - (probe[1] + probe[3]) / 2  # anchor from the box centre
    cx, cy = (px0 + px1) / 2, (py0 + py1) / 2
    for step in range(3):
        gap = GAP_MM + step * height
        for x, y in ((cx, py1 + gap + height / 2), (cx, py0 - gap - height / 2),
                     (px1 + gap + width / 2, cy), (px0 - gap - width / 2, cy)):
            box = (x - width / 2, y - height / 2, x + width / 2, y + height / 2)
            inside = bx0 + EDGE_MM <= box[0] and box[2] <= bx1 - EDGE_MM and by0 + EDGE_MM <= box[1] and box[3] <= by1 - EDGE_MM
            if inside and not any(_overlap(box, other) for other in obstacles):
                return round(x + dx, 4), round(y + dy, 4)
    return None


def _refdes_plan(doc: Any, ctx: Context) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    """The component, its silkscreen reference designator, where it stands and where it goes."""
    refdes = str(need_input(ctx, "refdes"))
    x, y, rotation, auto = ctx.inputs.get("x_mm"), ctx.inputs.get("y_mm"), ctx.inputs.get("rotation_deg"), bool(ctx.inputs.get("auto"))
    if auto and (x is not None or y is not None):
        raise AdapterError("E_VALIDATION", "Give --auto, or --x and --y, not both.", dispatch_state="not_started")
    if (x is None) != (y is None):
        raise AdapterError("E_VALIDATION", "Give --x and --y together.", dispatch_state="not_started")
    if not auto and x is None and rotation is None:
        raise AdapterError("E_VALIDATION", "refdes move needs --x and --y, --auto, or --rotation.", dispatch_state="not_started")
    component = find(doc, refdes)
    if not bool(component.Placed):
        raise AdapterError("E_VALIDATION", f"{refdes_of(component)} is not placed; place it with component move first.",
                           dispatch_state="not_started")
    text = silk_refdes(component)
    if text is None:
        raise AdapterError("E_NOT_FOUND", f"{refdes_of(component)} has no reference designator on the silkscreen; its cell "
                           "carries none.", dispatch_state="not_started")
    now = text_values(text)
    turn = now["rotation_deg"] if rotation is None else round(float(rotation) % 360.0, 4)
    if auto:
        spot = _free_spot(doc, component, text, turn)
        if spot is None:
            raise base.blocked(f"No free spot lies beside {refdes_of(component)} for its reference designator, clear of the other "
                               "parts, their reference designators and the mounting holes, inside the outline; give --x and --y.")
        x, y = spot
    goal = {"x_mm": now["x_mm"] if x is None else round(float(x), 4), "y_mm": now["y_mm"] if y is None else round(float(y), 4),
            "rotation_deg": turn}
    return component, text, now, goal


class RefdesMove(L.ReadsBack):
    """Move a component's reference designator on the silkscreen to a position and rotation, or to a
    free spot beside the part: ``FabricationLayerText.Move`` and ``Format.Orientation``."""

    command = "refdes move"

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()

        def look(app: Any, doc: Any) -> tuple[str, dict[str, Any], dict[str, Any]]:
            component, _text, now, goal = _refdes_plan(doc, ctx)
            return refdes_of(component), now, goal

        refdes, now, goal = L.in_layout(ctx, look, budget_s=L.READ_BUDGET_S, what="reading the reference designator")
        domain = refdes_key(refdes)
        target = [base.target(path, "text", domain, label=f"{refdes} reference designator")]
        changes = [base.change(base.key(path, domain), field, now[field], goal[field])
                   for field in ("x_mm", "y_mm", "rotation_deg") if abs(float(now[field]) - goal[field]) >= 0.0005]
        where = f"({goal['x_mm']:g}, {goal['y_mm']:g}) mm, {goal['rotation_deg']:g} degrees"
        if not changes:
            return base.preview(f"{refdes}'s reference designator stands at {where} already; nothing changes.", target, [])
        blocked = None
        if now["anchor"] in HELD:
            blocked = [f"{refdes}'s reference designator is {now['anchor']} in Layout; a person releases it in Layout first"]
        return base.preview(f"Move {refdes}'s reference designator on the {now['side']} silkscreen to {where}. The board is not "
                            "saved.", target, changes, blocked_by=blocked)

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            component, text, now, goal = _refdes_plan(doc, ctx)
            if now["anchor"] in HELD:
                raise base.blocked(f"{refdes_of(component)}'s reference designator is {now['anchor']} in Layout; a person releases "
                                   "it in Layout first.")
            moved = abs(now["x_mm"] - goal["x_mm"]) >= 0.0005 or abs(now["y_mm"] - goal["y_mm"]) >= 0.0005
            turned = abs(now["rotation_deg"] - goal["rotation_deg"]) >= 0.0005
            try:
                if moved:
                    text.Move(goal["x_mm"], goal["y_mm"], L.UNIT_MM)
                if turned:
                    text.Format.Orientation = goal["rotation_deg"]
            except Exception as exc:  # noqa: BLE001 - classified
                raise L.com_error(exc, f"moving {refdes_of(component)}'s reference designator", com.call_state(exc)) from None
            # The text has moved: what follows only describes it, and a part of it that cannot be read is left
            # unknown rather than raised, which would report the move as never sent.
            values = text_values(text)
            side = values["side"] or "top"
            result = {"refdes": refdes_of(component), **{k: values[k] for k in ("x_mm", "y_mm", "rotation_deg", "side")},
                      "inside_board": None, "overlaps": None}
            try:
                box = L.extrema_mm(text)
            except Exception:  # noqa: BLE001 - no extents to compare
                box = None
            if box is not None:
                try:
                    board = L.board_rect(doc)
                    result["inside_board"] = board[0] <= box[0] and box[2] <= board[2] and board[1] <= box[1] and box[3] <= board[3]
                except Exception:  # noqa: BLE001 - a board without an outline
                    pass
                try:
                    result["overlaps"] = [name for name, other in _obstacles(doc, component, side) if _overlap(box, other)]
                except Exception:  # noqa: BLE001 - the other objects cannot be read
                    pass
            return base.facts("applied" if moved or turned else "not_applied", "unchanged", result=result)

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="moving the reference designator")


HANDLERS: list[base.Handler] = [ComponentMove(), MountingHoleList(), MountingHoleCreate(), MountingHoleDelete(), RefdesMove()]
