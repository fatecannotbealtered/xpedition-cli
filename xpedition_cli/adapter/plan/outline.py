"""A board's rounded-rectangle outline as the three contours Layout keeps.

Layout holds a board's shape three times: the board outline, the route border (where
copper may go) and the manufacturing outline. `pcb update` writes all three from one
width, height and corner radius: the board and manufacturing outlines are the rectangle
itself, the route border is the same shape `INSET_MM` inside it.

A contour is Layout's points array: three rows, the x values, the y values, and a radius
per point. A rounded corner is three points -- the arc's start, its centre carrying the
radius, its end -- and the radius on the centre row is negative: that draws the 90° arc
on a counter-clockwise outline, a positive one draws the other 270° (found by drawing
both). Millimetres throughout, the origin at the lower left corner.
"""

from __future__ import annotations

import math
from typing import Any

from . import PlanError

OUTLINE_MEMBERS = {
    "board_outline": "BoardOutline",
    "route_border": "RouteBorder",
    "manufacturing_outline": "ManufacturingOutline",
}
MAX_CONTOUR_POINTS = 128
UNIT_SCALE_MM = 0.0254  # a mil, the unit Layout's geometry reports in by default
INSET_MM = 0.3  # the route border inside the board outline
TOLERANCE_MM = 0.000001


def rounded(x0: float, y0: float, x1: float, y1: float, radius: float) -> list[list[float]]:
    """A closed rectangle from (x0, y0) to (x1, y1), counter-clockwise from the lower edge,
    with corners of `radius` (0: square corners, five points; else thirteen)."""
    if radius == 0:
        return [[x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], [0.0] * 5]
    points = [
        [x1 - radius, y0, 0],
        [x1 - radius, y0 + radius, -radius],
        [x1, y0 + radius, 0],
        [x1, y1 - radius, 0],
        [x1 - radius, y1 - radius, -radius],
        [x1 - radius, y1, 0],
        [x0 + radius, y1, 0],
        [x0 + radius, y1 - radius, -radius],
        [x0, y1 - radius, 0],
        [x0, y0 + radius, 0],
        [x0 + radius, y0 + radius, -radius],
        [x0 + radius, y0, 0],
        [x1 - radius, y0, 0],
    ]
    return [list(row) for row in zip(*points, strict=True)]


def _number(value: Any, what: str = "an outline coordinate") -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise PlanError("E_VALIDATION", f"{what} is not a finite number", {"value": repr(value)[:40]})
    return float(value)


def targets(width: float, height: float, radius: float = 0.0) -> dict[str, dict[str, Any]]:
    """The three contours of a `width` x `height` board with corners of `radius`, by the keys
    of `OUTLINE_MEMBERS`; each is `{"points_mm", "cutout_count": 0, "closed": True}`."""
    width, height = _number(width, "width"), _number(height, "height")
    radius = _number(radius, "radius")
    if width <= 2 * INSET_MM or height <= 2 * INSET_MM or radius < 0 or radius >= min(width, height) / 2:
        raise PlanError(
            "E_VALIDATION",
            f"the outline needs width and height above {2 * INSET_MM:g} mm (the route border sits "
            f"{INSET_MM:g} mm inside it) and a radius from 0 to less than half the shorter side",
            {"width": width, "height": height, "radius": radius},
        )
    main = rounded(0.0, 0.0, width, height, radius)
    inner = rounded(INSET_MM, INSET_MM, width - INSET_MM, height - INSET_MM, max(radius - INSET_MM, 0.0))
    return {
        "board_outline": {"points_mm": main, "cutout_count": 0, "closed": True},
        "route_border": {"points_mm": inner, "cutout_count": 0, "closed": True},
        "manufacturing_outline": {"points_mm": [list(row) for row in main], "cutout_count": 0, "closed": True},
    }


def polygon_targets(points: Any) -> dict[str, dict[str, Any]]:
    """The three contours of a board whose outline is the polygon given, by the keys of
    `OUTLINE_MEMBERS`: `points` lists its corners in order round it, each `[x, y]`, or
    `[x, y, r]` for a corner rounded by an arc of radius `r` tangent to both sides, as the
    corners of a routing plan's traces. The board and manufacturing outlines are the polygon,
    the route border the same shape `INSET_MM` inside it."""
    from . import lanes, shapes
    from . import trace_plan as TP

    if not isinstance(points, list) or len(points) < 3:
        raise PlanError("E_VALIDATION", "the outline lists three or more corners, each [x, y] or [x, y, radius]")
    corners: list[tuple[tuple[float, float], float]] = []
    for index, point in enumerate(points):
        if not isinstance(point, (list, tuple)) or len(point) not in (2, 3):
            raise PlanError("E_VALIDATION", f"corner {index + 1} of the outline is [x, y] or [x, y, radius]")
        x, y = _number(point[0]), _number(point[1])
        radius = _number(point[2], "a corner radius") if len(point) == 3 else 0.0
        if radius < 0:
            raise PlanError("E_VALIDATION", f"corner {index + 1} of the outline has a negative radius")
        corners.append(((x, y), radius))
    if math.dist(corners[0][0], corners[-1][0]) < 1e-9:
        corners.pop()
    ring = [c[0] for c in corners]
    sides = shapes.edges(ring)
    if len(ring) < 3 or any(shapes.cross(a, b) for i, a in enumerate(sides) for b in sides[i + 2:]
                            if not (i == 0 and b == sides[-1])):
        raise PlanError("E_VALIDATION", "the outline's sides cross each other, or it has fewer than three corners; list its corners "
                        "in order round it")
    area = sum(ring[i - 1][0] * ring[i][1] - ring[i][0] * ring[i - 1][1] for i in range(len(ring))) / 2
    if abs(area) < 1e-6:
        raise PlanError("E_VALIDATION", "the outline encloses no area")
    if area < 0:  # counter-clockwise, as Layout draws an outline
        corners.reverse()
    try:
        main = _ring_rows(corners, TP)
        inner_corners = lanes.offset_chain([corners[-1], *corners, corners[0]], INSET_MM, True)[1:-1]
        inner = _ring_rows(inner_corners, TP)
    except PlanError as exc:
        raise PlanError("E_VALIDATION", exc.message.replace("The routing plan is not valid: ", "the outline: ").rstrip(".")) from None
    if max(len(main[0]), len(inner[0])) > MAX_CONTOUR_POINTS:
        raise PlanError("E_VALIDATION", f"the outline takes more than {MAX_CONTOUR_POINTS} points as Layout holds it; give fewer corners")
    return {
        "board_outline": {"points_mm": main, "cutout_count": 0, "closed": True},
        "route_border": {"points_mm": inner, "cutout_count": 0, "closed": True},
        "manufacturing_outline": {"points_mm": [list(row) for row in main], "cutout_count": 0, "closed": True},
    }


def _ring_rows(corners: list[tuple[tuple[float, float], float]], TP: Any) -> list[list[float]]:
    """A closed ring of corners as Layout's three rows, starting halfway along its first side so
    that every corner, the first too, can carry its arc."""
    (a, _ra), (b, _rb) = corners[0], corners[1]
    start = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    ordered = corners[1:] + corners[:1]
    points = [start, *[c[0] for c in ordered], start]
    radii = [0.0, *[c[1] for c in ordered], 0.0]
    rows = TP.drawn(points, radii)
    return [[round(r[0], 6) for r in rows], [round(r[1], 6) for r in rows], [round(r[2], 6) for r in rows]]


def _array(value: Any) -> list[list[float]]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(not isinstance(row, (list, tuple)) for row in value)
        or len({len(row) for row in value}) != 1
        or not 4 <= len(value[0]) <= MAX_CONTOUR_POINTS
    ):
        raise PlanError("E_VALIDATION", f"a contour is three equal rows of 4 to {MAX_CONTOUR_POINTS} values (x, y, radius)")
    return [[_number(v) for v in row] for row in value]


def same_points(left: Any, right: Any) -> bool:
    """Whether two points arrays hold the same points, to `TOLERANCE_MM`."""
    try:
        one, two = _array(left), _array(right)
    except PlanError:
        return False
    return len(one[0]) == len(two[0]) and all(
        abs(a - b) <= TOLERANCE_MM for row_a, row_b in zip(one, two, strict=True) for a, b in zip(row_a, row_b, strict=True)
    )


def closed(points: list[list[float]]) -> bool:
    """Whether a points array ends where it starts."""
    return all(abs(row[0] - row[-1]) <= TOLERANCE_MM for row in points)


def same_shape(left: Any, right: Any) -> bool:
    """Whether two contour records (`points_mm`, `cutout_count`, `closed`) are the same closed
    shape without cutouts."""
    return (
        isinstance(left, dict)
        and isinstance(right, dict)
        and left.get("closed") is True
        and right.get("closed") is True
        and left.get("cutout_count") == right.get("cutout_count") == 0
        and same_points(left.get("points_mm"), right.get("points_mm"))
    )


def expectation(options: dict[str, Any], before_outlines: dict[str, Any] | None = None) -> dict[str, Any]:
    """What `pcb update` writes for `options` (`width`, `height`, `radius` in mm): the three
    target contours, and whether the board already has them (`no_op`), judged from
    `before_outlines` (`{"objects": {key: contour record}}`, as read before the write)."""
    width = _number(options.get("width"), "width")
    height = _number(options.get("height"), "height")
    radius = _number(0.0 if options.get("radius") is None else options["radius"], "radius")
    target = targets(width, height, radius)
    objects = (before_outlines or {}).get("objects") or {}
    return {
        "requested": {"width": width, "height": height, "radius": radius},
        "target": target,
        "no_op": bool(objects) and all(same_shape(objects.get(key), target[key]) for key in OUTLINE_MEMBERS),
    }
