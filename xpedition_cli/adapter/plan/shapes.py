"""Plain 2-D tests the checks share: whether a silkscreen text's box or line reaches
into a pad, a hole or another shape.

Millimetres throughout. A shape comes as the board model gives it: a `circle`
(x, y, r) or a `path` of (x, y, r) rows -- a row with `r != 0` is the centre of an
arc, see `flatten` -- with a line `width`, 0 for a filled shape. The
library check builds the same records from a cell's outlines and lands.
"""

from __future__ import annotations

import math
from typing import Any

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
CIRCLE_STEPS = 32  # a circle drawn as a line is taken as a polygon of this many sides
ARC_STEP_DEG = 6.0  # an arc is flattened into a straight segment every this many degrees


def flatten(path: list[tuple[float, float, float]]) -> list[tuple[float, float]]:
    """The path as straight segments, the arcs stepped every few degrees.

    A path is a list of `(x, y, r)` rows where a row with `r != 0` is the centre of an arc
    from the previous row to the next one (`r > 0` clockwise, `r < 0` counter-clockwise, the
    way Layout's `PointsArray` reports and takes them).
    """
    points: list[tuple[float, float]] = []
    count = len(path)
    for index, (x, y, r) in enumerate(path):
        if r and 0 < index < count - 1:
            px, py, _ = path[index - 1]
            nx, ny, _ = path[index + 1]
            start = math.atan2(py - y, px - x)
            end = math.atan2(ny - y, nx - x)
            radius = math.hypot(px - x, py - y) or abs(r)
            sweep = (end - start) % (2 * math.pi)  # the counter-clockwise way round
            if r > 0:
                sweep = sweep - 2 * math.pi  # positive radius: clockwise
            if abs(sweep) < 1e-9:
                # a full circle described as two half arcs, or a degenerate arc
                sweep = -2 * math.pi if r > 0 else 2 * math.pi
            steps = max(2, int(abs(sweep) / math.radians(ARC_STEP_DEG)))
            for step in range(1, steps):
                angle = start + sweep * step / steps
                points.append((x + radius * math.cos(angle), y + radius * math.sin(angle)))
            continue
        points.append((x, y))
    return points


def orient(p: Point, q: Point, r: Point) -> float:
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def cross(a: tuple[Point, Point], b: tuple[Point, Point]) -> bool:
    """Proper crossing: the segments meet at a point inside both, not at an end."""
    (p, q), (r, s) = a, b
    o1, o2 = orient(p, q, r), orient(p, q, s)
    o3, o4 = orient(r, s, p), orient(r, s, q)
    return o1 * o2 < 0 and o3 * o4 < 0


def overlap(a: Rect, b: Rect) -> float:
    """The area two rectangles share; 0 when they only touch."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return width * height if width > 0 and height > 0 else 0.0


def corners(box: Rect) -> list[Point]:
    return [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]


def edges(points: list[Point]) -> list[tuple[Point, Point]]:
    """The sides of a closed ring of points."""
    return list(zip(points, points[1:] + points[:1], strict=True))


def inside(point: Point, polygon: list[Point]) -> bool:
    """Whether a point is inside a polygon (ray casting)."""
    x, y = point
    within = False
    for (x1, y1), (x2, y2) in edges(polygon):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            within = not within
    return within


def box_polygon(box: Rect, polygon: list[Point]) -> bool:
    """Whether a box and a filled polygon share area (edges only touching do not)."""
    if len(polygon) < 3:
        return False
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    if overlap(box, (min(xs), min(ys), max(xs), max(ys))) <= 0:
        return False
    if any(inside(corner, polygon) for corner in corners(box)):
        return True
    if any(box[0] < x < box[2] and box[1] < y < box[3] for x, y in polygon):
        return True
    return any(cross(edge, side) for edge in edges(corners(box)) for side in edges(polygon))


def box_disc(box: Rect, x: float, y: float, radius: float) -> bool:
    """Whether a disc reaches inside a box."""
    nearest_x, nearest_y = min(max(x, box[0]), box[2]), min(max(y, box[1]), box[3])
    return math.hypot(x - nearest_x, y - nearest_y) < radius


def point_segment(point: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length
    t = max(0.0, min(1.0, t))
    return math.dist(point, (a[0] + t * dx, a[1] + t * dy))


def segment_box(a: Point, b: Point, box: Rect) -> float:
    """How far a segment stays from a box: 0 when it enters it."""
    if any(box[0] <= x <= box[2] and box[1] <= y <= box[3] for x, y in (a, b)):
        return 0.0
    if any(cross((a, b), edge) for edge in edges(corners(box))):
        return 0.0
    ends = [
        math.hypot(x - min(max(x, box[0]), box[2]), y - min(max(y, box[1]), box[3]))
        for x, y in (a, b)
    ]
    return min(*ends, *(point_segment(corner, a, b) for corner in corners(box)))


def segment_polygon(a: Point, b: Point, polygon: list[Point]) -> float:
    """How far a segment stays from a filled polygon: 0 when it enters it."""
    if inside(a, polygon) or inside(b, polygon):
        return 0.0
    sides = edges(polygon)
    if any(cross((a, b), side) for side in sides):
        return 0.0
    return min(
        min(point_segment(a, p, q), point_segment(b, p, q), point_segment(p, a, b))
        for p, q in sides
    )


class Outline:
    """A pad or a silkscreen shape made ready to be tested against many others: a
    disc, a filled polygon, or a line of some width (a circle drawn as a line becomes
    a ring of `CIRCLE_STEPS` sides), and the rectangle around it all."""

    def __init__(self, shape: dict[str, Any]) -> None:
        self.shape = shape
        self.width = float(shape.get("width") or 0.0)
        self.disc: tuple[float, float, float] | None = None
        self.points: list[Point] = []
        if shape.get("circle"):
            x, y, radius = (float(value) for value in shape["circle"][:3])
            if self.width:
                steps = [2 * math.pi * i / CIRCLE_STEPS for i in range(CIRCLE_STEPS + 1)]
                self.points = [(x + radius * math.cos(t), y + radius * math.sin(t)) for t in steps]
            else:
                self.disc = (x, y, radius)
        else:
            self.points = flatten([tuple(row) for row in shape.get("path") or []])  # type: ignore[misc]
        if self.disc is not None:
            x, y, radius = self.disc
            self.bounds = (x - radius, y - radius, x + radius, y + radius)
        else:
            grow = self.width / 2
            xs = [p[0] for p in self.points] or [0.0]
            ys = [p[1] for p in self.points] or [0.0]
            self.bounds = (min(xs) - grow, min(ys) - grow, max(xs) + grow, max(ys) + grow)

    @property
    def stroked(self) -> bool:
        return self.disc is None and self.width > 0

    def centre(self) -> Point:
        return ((self.bounds[0] + self.bounds[2]) / 2, (self.bounds[1] + self.bounds[3]) / 2)

    def segments(self) -> list[tuple[Point, Point]]:
        return list(zip(self.points, self.points[1:], strict=False))

    def reaches(self, box: Rect) -> bool:
        """Whether the shape reaches into the box: a line by its width, a filled shape
        by its area."""
        if overlap(self.bounds, box) <= 0:
            return False
        if self.disc is not None:
            return box_disc(box, *self.disc)
        if self.stroked:
            return any(segment_box(a, b, box) < self.width / 2 for a, b in self.segments())
        return box_polygon(box, self.points)

    def crosses(self, pad: Outline) -> bool:
        """Whether this shape reaches into a filled one (a pad): a line by its width, a
        filled shape by its area."""
        if overlap(self.bounds, pad.bounds) <= 0:
            return False
        half = self.width / 2
        if self.stroked:
            if pad.disc is not None:
                x, y, radius = pad.disc
                return any(point_segment((x, y), a, b) < radius + half for a, b in self.segments())
            return any(segment_polygon(a, b, pad.points) < half for a, b in self.segments())
        if self.disc is not None and pad.disc is not None:
            return math.dist(self.disc[:2], pad.disc[:2]) < self.disc[2] + pad.disc[2]
        if self.disc is not None or pad.disc is not None:
            disc, ring = (
                (self.disc, pad.points) if self.disc is not None else (pad.disc, self.points)
            )
            assert disc is not None
            x, y, radius = disc
            if len(ring) >= 3 and inside((x, y), ring):
                return True
            return any(point_segment((x, y), a, b) < radius for a, b in edges(ring))
        if len(self.points) < 3 or len(pad.points) < 3:
            return False
        if any(inside(point, pad.points) for point in self.points):
            return True
        if any(inside(point, self.points) for point in pad.points):
            return True
        return any(cross(a, b) for a in edges(self.points) for b in edges(pad.points))
