"""Components on the open board: reading them (component list and component get on a board) and
moving them (component move).

Positions are board coordinates in mm: a component's position is its cell origin, as Layout
places it; its rotation is counter-clockwise in degrees, seen from the top.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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


HANDLERS: list[base.Handler] = [ComponentMove()]
