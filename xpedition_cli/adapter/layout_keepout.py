"""Placement keepouts: ``keepout create`` and ``keepout delete``.

A placement keepout on one side keeps parts out of an area; with a height it keeps out parts taller
than that, and on a flex board it marks where a stiffener is laminated, which ``pcb get`` lists
under ``keepout_kinds.height_limits`` and the flex checks and openings read. A keepout is known by
its side and its extents: the target key is ``/keepouts/placement/<side>/<x1,y1,x2,y2>``.
"""
from __future__ import annotations

from typing import Any

from .._driver import AdapterError
from . import base
from . import layout as L
from .base import Context
from .layout_edit import _overlaps, _rect, _side
from .platform import com

PLACEMENT_OBSTRUCTS_DISPID = 40  # Document.PlacementObstructs(eSelectOnly, nLayerOnly)
SIDES = {1: "top", 512: "bottom"}  # EPcbSide
PEN_MM = 0.1
MAX_HEIGHT_MM = 50.0
Box = tuple[float, float, float, float]


def keepout_key(side: str, box: Box) -> str:
    return f"/keepouts/placement/{side}/" + ",".join(f"{v:g}" for v in box)


def placement_keepouts(doc: Any) -> list[tuple[Any, str, Box, float | None]]:
    """The board's own placement keepouts: (object, side, extents, height in mm or None)."""
    scale = L.geometry_scale(doc)
    out = []
    for item in L.items(com.propget(doc, PLACEMENT_OBSTRUCTS_DISPID, 0, 0)):
        try:
            if item.Component is not None:
                continue
            side = SIDES.get(int(item.Side), "top")
            box = tuple(round(v, 4) for v in L.extrema_mm(item))
            height = float(item.Height) * scale if scale else None
            out.append((item, side, box, round(height, 4) if height else None))
        except Exception:  # noqa: BLE001 - a keepout that cannot be read is left alone
            continue
    return out


def _same(box: Box, other: Box) -> bool:
    return all(abs(a - b) < 0.002 for a, b in zip(box, other, strict=True))


def keepout_at(doc: Any, side: str, box: Box) -> dict[str, Any]:
    """Whether a placement keepout with those extents is on the side, and its height: the reader
    ``read_keys`` uses."""
    found = [height for _item, where, extents, height in placement_keepouts(doc) if where == side and _same(extents, box)]
    return {"exists": bool(found), **({"height_mm": found[0]} if found else {})}


def _request(ctx: Context) -> tuple[str, Box, float | None]:
    side, _code = _side(ctx)
    box = _rect(ctx)
    if box is None:
        raise AdapterError("E_VALIDATION", "keepout create needs --x1, --y1, --x2 and --y2: two opposite corners.",
                           dispatch_state="not_started")
    height = ctx.inputs.get("height_mm")
    if height is not None and not 0 < float(height) <= MAX_HEIGHT_MM:
        raise AdapterError("E_VALIDATION", f"--height is more than 0 and at most {MAX_HEIGHT_MM:g} mm; leave it out for a keepout of "
                           "every part.", dispatch_state="not_started")
    return side, box, None if height is None else round(float(height), 4)


class KeepoutCreate(L.ReadsBack):
    """A rectangular placement keepout on one side, with a height when given:
    ``PutPlacementObstruct`` on the side's outer layer."""

    command = "keepout create"

    def preview(self, ctx: Context) -> dict[str, Any]:
        side, box, height = _request(ctx)
        path = ctx.document_path()

        def look(app: Any, doc: Any) -> tuple[bool, list[Box]]:
            keepouts = placement_keepouts(doc)
            exists = any(where == side and _same(extents, box) for _i, where, extents, _h in keepouts)
            overlapping = [extents for _i, where, extents, _h in keepouts if where == side and _overlaps(extents, box)
                           and not _same(extents, box)]
            return exists, overlapping

        exists, overlapping = L.in_layout(ctx, look, budget_s=L.READ_BUDGET_S, what="reading the keepouts")
        domain = keepout_key(side, box)
        target = [base.target(path, "keepout", domain, label=f"placement keepout on the {side}")]
        where = f"{box[0]:g},{box[1]:g} to {box[2]:g},{box[3]:g} mm"
        if exists:
            return base.preview(f"The {side} has a placement keepout over {where} already; nothing changes.", target, [])
        kind = f"no part taller than {height:g} mm, as a stiffener's area" if height else "no part"
        beside = f" It overlaps {len(overlapping)} other placement keepout(s) on that side." if overlapping else ""
        changes = [base.change(base.key(path, domain), "exists", False, True)]
        if height:
            changes.append(base.change(base.key(path, domain), "height_mm", None, height))
        return base.preview(f"Draw a placement keepout over {where} on the {side}: {kind}.{beside} The board is not saved.", target,
                            changes)

    def apply(self, ctx: Context) -> dict[str, Any]:
        side, box, height = _request(ctx)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            result = {"side": side, "rect_mm": list(box), "height_mm": height}
            if any(where == side and _same(extents, box) for _i, where, extents, _h in placement_keepouts(doc)):
                return base.facts("not_applied", "unchanged", result={**result, "created": False})
            x1, y1, x2, y2 = box
            points = [[x1, x2, x2, x1, x1], [y1, y1, y2, y2, y1], [0.0] * 5]
            layer = 1 if side == "top" else int(doc.LayerCount)
            try:
                doc.PutPlacementObstruct(layer, PEN_MM, 5, points, height or 0.0, None, L.UNIT_MM)
            except Exception as exc:  # noqa: BLE001 - classified
                raise L.com_error(exc, "drawing the keepout", com.call_state(exc)) from None
            return base.facts("applied", "unchanged", result={**result, "created": True})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="drawing the keepout")


class KeepoutDelete(L.ReadsBack):
    """Delete the board's placement keepout that holds a point on one side."""

    command = "keepout delete"

    def _found(self, ctx: Context, doc: Any) -> tuple[str, Any, Box]:
        side, _code = _side(ctx)
        x, y = float(base.need_input(ctx, "x_mm")), float(base.need_input(ctx, "y_mm"))
        hits = [(item, extents) for item, where, extents, _h in placement_keepouts(doc)
                if where == side and extents[0] <= x <= extents[2] and extents[1] <= y <= extents[3]]
        if not hits:
            raise AdapterError("E_NOT_FOUND", f"No placement keepout of the board holds ({x:g}, {y:g}) mm on the {side}.",
                               dispatch_state="not_started")
        if len(hits) > 1:
            raise AdapterError("E_AMBIGUOUS", f"{len(hits)} placement keepouts hold ({x:g}, {y:g}) mm on the {side}; give a point inside "
                               "one only.", dispatch_state="not_started", candidates=[keepout_key(side, box) for _i, box in hits])
        return side, hits[0][0], hits[0][1]

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        side, _item, box = L.in_layout(ctx, lambda app, doc: self._found(ctx, doc), budget_s=L.READ_BUDGET_S, what="finding the keepout")
        domain = keepout_key(side, box)
        return base.preview(f"Delete the placement keepout over {box[0]:g},{box[1]:g} to {box[2]:g},{box[3]:g} mm on the {side}. The "
                            "board is not saved.", [base.target(path, "keepout", domain, label=f"placement keepout on the {side}")],
                            [base.change(base.key(path, domain), "exists", True, False)])

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            side, item, box = self._found(ctx, doc)
            try:
                item.Delete()
            except Exception as exc:  # noqa: BLE001 - classified
                raise L.com_error(exc, "deleting the keepout", com.call_state(exc)) from None
            return base.facts("applied", "unchanged", result={"side": side, "rect_mm": list(box)})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="deleting the keepout")


HANDLERS: list[base.Handler] = [KeepoutCreate(), KeepoutDelete()]
