"""A board's shape cut into pieces by straight lines: the solid and hatched bands of a flex board
whose bend zones lie at a slant, each zone a convex polygon across a neck or an arm.

A shape is clipped by one side of a line at a time (Sutherland-Hodgman). Clipping a concave shape,
such as a board with two arms cut across both, joins the pieces it leaves by an edge run there and
back along the clip line; ``untangled`` takes such a shape apart again. A line at a slant is turned
level first, so that the same untangling serves every line.

Pure geometry in mm.
"""
from __future__ import annotations

import math

Point = tuple[float, float]


def untangled(polygon: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """A clipped polygon as the pieces it holds. Clipping a concave shape, such as a U-shaped board
    cut across both its arms, joins the pieces by an edge run there and back along the clip line,
    copper of no width; the edges along each horizontal or vertical line are counted by direction,
    what is run both ways is dropped, and the rest is walked into closed pieces."""
    edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    runs: dict[tuple[int, float], list[tuple[float, float, int]]] = {}
    count = len(polygon)
    for i in range(count):
        a, b = polygon[i], polygon[(i + 1) % count]
        if math.dist(a, b) < 1e-9:
            continue
        for axis in (1, 0):  # a horizontal edge keeps its y, a vertical one its x
            if abs(a[axis] - b[axis]) < 1e-7:
                along = 1 - axis
                low, high = sorted((a[along], b[along]))
                runs.setdefault((axis, round(a[axis], 6)), []).append((low, high, 1 if b[along] > a[along] else -1))
                break
        else:
            edges.append((a, b))
    for (axis, at), pieces in runs.items():
        cuts = sorted({v for low, high, _sign in pieces for v in (low, high)})
        for u, w in zip(cuts, cuts[1:], strict=False):
            net = sum(sign for low, high, sign in pieces if low <= u + 1e-9 and w <= high + 1e-9) if w - u > 1e-9 else 0
            if net:
                p, q = ((u, at), (w, at)) if axis == 1 else ((at, u), (at, w))
                edges.append((p, q) if net > 0 else (q, p))

    def key(point: tuple[float, float]) -> tuple[float, float]:
        return round(point[0], 6), round(point[1], 6)

    leaving: dict[tuple[float, float], list[tuple[tuple[float, float], tuple[float, float]]]] = {}
    for a, b in edges:
        leaving.setdefault(key(a), []).append((a, b))
    out = []
    for start in list(leaving):
        while leaving[start]:
            a, b = leaving[start].pop()
            loop, here = [a], b
            while key(here) != key(a) and leaving.get(key(here)) and len(loop) <= len(edges):
                loop.append(here)
                here = leaving[key(here)].pop()[1]
            if key(here) == key(a):
                loop = [q for i, q in enumerate(loop)
                        if abs((q[0] - loop[i - 1][0]) * (loop[(i + 1) % len(loop)][1] - q[1])
                               - (q[1] - loop[i - 1][1]) * (loop[(i + 1) % len(loop)][0] - q[0])) > 1e-12]  # no point in line
                if len(loop) >= 3:
                    out.append(loop)
    return out


def _turned(points: list[Point], c: float, s: float) -> list[Point]:
    return [(x * c - y * s, x * s + y * c) for x, y in points]


def keep_side(polygon: list[Point], a: Point, b: Point, left: bool) -> list[list[Point]]:
    """The pieces of ``polygon`` on the left (``left``) or right of the line through a and b."""
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    c, s = math.cos(-angle), math.sin(-angle)
    level = _turned(polygon, c, s)
    at = _turned([a], c, s)[0][1]  # the line, level: y = at; its left is above
    keep = 1.0 if left else -1.0
    out: list[Point] = []
    count = len(level)
    for i in range(count):
        p, q = level[i - 1], level[i]
        p_in, q_in = keep * (p[1] - at) >= 0, keep * (q[1] - at) >= 0
        if p_in != q_in:
            t = (at - p[1]) / (q[1] - p[1])
            out.append((p[0] + (q[0] - p[0]) * t, at))
        if q_in:
            out.append(q)
    if len(out) < 3:
        return []
    back = math.cos(angle), math.sin(angle)
    return [_turned(piece, *back) for piece in untangled(out)]


def counter_clockwise(polygon: list[Point]) -> list[Point]:
    """The polygon counter-clockwise, without a point repeated, such as its first again at its end:
    a side of no length gives no line to keep a side of."""
    points: list[Point] = []
    for point in polygon:
        if not points or math.dist(points[-1], point) > 1e-9:
            points.append(point)
    while len(points) > 1 and math.dist(points[0], points[-1]) <= 1e-9:
        points.pop()
    area = sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(points, points[1:] + points[:1], strict=True))
    return points if area > 0 else points[::-1]


def inside(polygon: list[Point], zone: list[Point]) -> list[list[Point]]:
    """The pieces of ``polygon`` inside the convex ``zone``."""
    pieces = [polygon]
    ring = counter_clockwise(zone)
    for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
        pieces = [part for piece in pieces for part in keep_side(piece, a, b, True)]
    return pieces


def outside(polygon: list[Point], zone: list[Point]) -> list[list[Point]]:
    """The pieces of ``polygon`` outside the convex ``zone``: for each side of the zone, what lies
    beyond it and within the sides before it, so that no two pieces overlap."""
    out: list[list[Point]] = []
    rest = [polygon]
    ring = counter_clockwise(zone)
    for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
        out += [part for piece in rest for part in keep_side(piece, a, b, False)]
        rest = [part for piece in rest for part in keep_side(piece, a, b, True)]
    return out


def _gap(point: Point, ring: list[Point]) -> float:
    """How far ``point`` lies from the nearest side of ``ring``."""
    best = math.inf
    for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = dx * dx + dy * dy
        t = 0.0 if not length else max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length))
        best = min(best, math.dist(point, (a[0] + dx * t, a[1] + dy * t)))
    return best


def reaching(zone: list[Point], shape: list[Point], reach: float, near: float = 1.0) -> list[Point]:
    """A convex zone with each side that runs along the shape's edge, its ends and its middle within
    ``near`` of it, moved out ``reach``: a zone marked from edge to edge just inside the board's
    edges then reaches past them. A corner between a side moved and one kept slides along the one
    kept, so that the zone's ends stay where they are."""
    ring = counter_clockwise(zone)
    count = len(ring)
    sides = list(zip(ring, ring[1:] + ring[:1], strict=True))
    moved = [all(_gap(p, shape) <= near for p in (a, b, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))) for a, b in sides]

    def normal(i: int) -> Point:
        (a, b) = sides[i]
        length = math.dist(a, b) or 1.0
        return (b[1] - a[1]) / length, -(b[0] - a[0]) / length  # outward for a counter-clockwise ring

    out = []
    for i, point in enumerate(ring):
        before, after = (i - 1) % count, i  # the sides that meet at this corner
        if moved[before] and moved[after]:  # where the two moved sides meet: twice as far out at a square corner, as far on a straight run
            n1, n2 = normal(before), normal(after)
            dot = n1[0] * n2[0] + n1[1] * n2[1]
            if dot < -1.0 + 1e-6:  # the two sides fold back onto each other, as in a zone drawn as a line: out along the first
                out.append((point[0] + n1[0] * reach, point[1] + n1[1] * reach))
                continue
            meet = reach / (1.0 + dot)
            out.append((point[0] + (n1[0] + n2[0]) * meet, point[1] + (n1[1] + n2[1]) * meet))
        elif moved[before] or moved[after]:
            n = normal(before if moved[before] else after)
            kept = sides[after] if moved[before] else sides[before]
            d = (kept[1][0] - kept[0][0], kept[1][1] - kept[0][1])
            length = math.hypot(*d) or 1.0
            d = (d[0] / length, d[1] / length)
            along = d[0] * n[0] + d[1] * n[1]
            if abs(along) < 0.2:  # the side kept runs nearly along the one moved: move the corner straight out
                out.append((point[0] + n[0] * reach, point[1] + n[1] * reach))
            else:
                out.append((point[0] + d[0] * reach / along, point[1] + d[1] * reach / along))
        else:
            out.append(point)
    return out


def crossed_sides(zone: list[Point], shape: list[Point], near: float = 1.0) -> list[tuple[Point, Point]]:
    """The sides of a zone that traces cross: those inside the shape, not along its edge (the ones
    ``reaching`` moves out), in the zone's own order."""
    return [(a, b) for a, b in zip(zone, zone[1:] + zone[:1], strict=True)
            if not all(_gap(p, shape) <= near for p in (a, b, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)))]


def edge_run(zone: list[Point], shape: list[Point], near: float = 1.0) -> Point | None:
    """The way traces cross a zone, square to the longest side they cross; None when every side
    runs along the shape's edge."""
    sides = crossed_sides(zone, shape, near)
    if not sides:
        return None
    a, b = max(sides, key=lambda side: math.dist(*side))
    return -(b[1] - a[1]), b[0] - a[0]


def area(polygon: list[Point]) -> float:
    return abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(polygon, polygon[1:] + polygon[:1], strict=True))) / 2


def merged(pieces: list[list[Point]]) -> list[list[Point]]:
    """Pieces that touch along an edge made one: each edge split where another piece's corner lies on
    it, the edges two pieces share dropped, and the rest walked into closed shapes. Cutting a shape
    by the sides of several zones leaves it in many pieces; poured one by one, a piece that holds no
    pad or via of its net would get no copper."""
    rings = [counter_clockwise(piece) for piece in pieces if len(piece) >= 3]
    corners = [p for ring in rings for p in ring]

    def key(point: Point) -> tuple[float, float]:
        return round(point[0], 5), round(point[1], 5)

    edges: list[tuple[Point, Point]] = []
    for ring in rings:
        for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
            length = math.dist(a, b)
            if length < 1e-9:
                continue
            on = sorted(((((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / length ** 2), p) for p in corners
                        if abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / length < 1e-6)
            stops = [a] + [p for t, p in on if 1e-9 < t < 1 - 1e-9] + [b]
            edges += [(p, q) for p, q in zip(stops, stops[1:], strict=False) if math.dist(p, q) > 1e-9]
    count: dict[tuple[tuple[float, float], tuple[float, float]], int] = {}
    for a, b in edges:
        count[(key(a), key(b))] = count.get((key(a), key(b)), 0) + 1
    kept = [(a, b) for a, b in edges if not count.get((key(b), key(a)))]
    leaving: dict[tuple[float, float], list[tuple[Point, Point]]] = {}
    for a, b in kept:
        leaving.setdefault(key(a), []).append((a, b))
    out: list[list[Point]] = []
    for start in list(leaving):
        while leaving[start]:
            a, b = leaving[start].pop()
            loop, here = [a], b
            while key(here) != key(a) and leaving.get(key(here)) and len(loop) <= len(kept):
                loop.append(here)
                here = leaving[key(here)].pop()[1]
            if key(here) == key(a):
                loop = [q for i, q in enumerate(loop)
                        if abs((q[0] - loop[i - 1][0]) * (loop[(i + 1) % len(loop)][1] - q[1])
                               - (q[1] - loop[i - 1][1]) * (loop[(i + 1) % len(loop)][0] - q[0])) > 1e-12]
                if len(loop) >= 3:
                    out.append(loop)
    return out
