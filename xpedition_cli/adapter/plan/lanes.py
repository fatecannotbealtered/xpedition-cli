"""Lanes of a routing plan: traces that follow an edge of the board outline at a distance.

A lane names an edge of the outline by two points near it, and the distance of the lane's centre
line from that edge. Its trace runs from its first points, such as a pin and the point where it
leaves for the lane, onto the edge's offset, follows it, and leaves it for its last points. The
offset keeps the outline's shape: a straight edge gives a parallel run, and an arc of the outline an
arc about the same centre, its radius smaller or larger by the distance. Lanes laid at even
distances from one edge stay evenly spaced through every turn, and lanes that turn one way and then
back come out the same length.

The outline comes as Layout's (x, y, r) rows in mm, a row with r != 0 the centre of an arc (see
``shapes.flatten``). It is taken as a closed polygon of corners, each with the radius of the arc
that rounds it, the form a trace of the routing plan takes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from . import trace_plan as TP

Point = tuple[float, float]
Corner = tuple[Point, float]  # a corner and the radius of the arc that rounds it, 0 for a sharp one
JOIN_RADIUS_MM = 0.5  # the radius of the turns onto and off the lane, unless the lane gives one
ARC_PART = math.radians(120)  # an outline arc is taken in parts of at most this sweep
STRAIGHT = 1e-4  # sine of the angle under which three outline points count as one straight run
TAB_MM = 5.0  # a lane runs straight past a part of the outline that sticks out over a base this long or shorter
TAB_ALIGN = math.cos(math.radians(10))  # and only where the edge runs on in line on both sides of it
KEYS = ("net", "layer", "width_mm", "from_mm", "to_mm", "edge_mm", "offset_mm")


@dataclass
class Lane:
    net: str
    layer: int
    width: float
    start: list[Corner]  # from the first end to the point where the trace leaves for the lane
    end: list[Corner]  # from the point where it leaves the lane to the last end
    edge: tuple[Point, Point]
    offset: float
    radius: float


def parse(items: Any) -> list[Lane]:
    if not isinstance(items, list) or not items:
        raise TP._error("lanes is a list of at least one lane")
    lanes = []
    for index, item in enumerate(items):
        what = f"lanes[{index}]"
        if not isinstance(item, dict) or set(item) - {*KEYS, "radius_mm"} or set(KEYS) - set(item):
            raise TP._error(f"{what} has the keys {', '.join(KEYS)} and, if wanted, radius_mm")
        net, layer, width = item["net"], item["layer"], item["width_mm"]
        if not isinstance(net, str) or not net:
            raise TP._error(f"{what}.net is a net name")
        if type(layer) is not int or layer < 1:
            raise TP._error(f"{what}.layer is a conductor layer number, 1 at the top")
        if isinstance(width, bool) or not isinstance(width, (int, float)) or not 0 < width <= 25:
            raise TP._error(f"{what}.width_mm is a width of more than 0 and at most 25 mm")
        ends = []
        for key in ("from_mm", "to_mm"):
            points = item[key]
            if not isinstance(points, list) or not points:
                raise TP._error(f"{what}.{key} has at least one point")
            ends.append([TP._corner(p, f"{what}.{key}[{i}]") for i, p in enumerate(points)])
        if ends[0][0][1] or ends[1][-1][1]:
            raise TP._error(f"{what} rounds no corner at its ends, from_mm[0] and the last point of to_mm")
        edge = item["edge_mm"]
        if not isinstance(edge, list) or len(edge) != 2:
            raise TP._error(f"{what}.edge_mm is two points near the outline, the edge the lane follows between them")
        offset, radius = item["offset_mm"], item.get("radius_mm", JOIN_RADIUS_MM)
        for value, key in ((offset, "offset_mm"), (radius, "radius_mm")):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
                raise TP._error(f"{what}.{key} is a distance of 0 to 100 mm")
        if offset <= 0:
            raise TP._error(f"{what}.offset_mm is the distance of the lane's centre line from the edge, more than 0")
        lanes.append(Lane(net, layer, round(float(width), 4), ends[0], ends[1],
                          (TP._point(edge[0], f"{what}.edge_mm[0]"), TP._point(edge[1], f"{what}.edge_mm[1]")),
                          float(offset), float(radius)))
    return lanes


# ------------------------------------------------------------------ the outline as corners


def _unit(a: Point, b: Point) -> Point:
    length = math.dist(a, b)
    return (b[0] - a[0]) / length, (b[1] - a[1]) / length


def _cross(u: Point, v: Point) -> float:
    return u[0] * v[1] - u[1] * v[0]


def outline_corners(rows: list[tuple[float, float, float]]) -> list[Corner]:
    """The outline as a closed ring of corners: each arc as the corner where its two tangents meet,
    with its radius, in parts of at most 120 degrees; every other point as a sharp corner. Points on
    a straight run are left out."""
    rows = [(float(x), float(y), float(r)) for x, y, r in rows]
    ring: list[Corner] = []
    i = 0
    while i < len(rows):
        x, y, _r = rows[i]
        if i + 2 < len(rows) and rows[i + 1][2]:
            (cx, cy, r), (ex, ey, _) = rows[i + 1], rows[i + 2]
            radius = math.hypot(x - cx, y - cy)
            a0, a1 = math.atan2(y - cy, x - cx), math.atan2(ey - cy, ex - cx)
            sweep = (a1 - a0) % (2 * math.pi)
            if r > 0:
                sweep -= 2 * math.pi  # a positive radius runs clockwise
            parts = max(1, math.ceil(abs(sweep) / ARC_PART - 1e-9))
            ring.append(((x, y), 0.0))
            for k in range(parts):
                mid = a0 + sweep * (k + 0.5) / parts
                reach = radius / math.cos(abs(sweep) / parts / 2)
                ring.append(((cx + reach * math.cos(mid), cy + reach * math.sin(mid)), radius))
            i += 2  # the arc's end is the next row's point
            continue
        ring.append(((x, y), 0.0))
        i += 1
    if len(ring) > 1 and math.dist(ring[0][0], ring[-1][0]) < 1e-6:
        ring.pop()
    return _straightened(ring, closed=True)


def _straightened(ring: list[Corner], closed: bool) -> list[Corner]:
    """The corners without repeats and without sharp points on a straight run."""
    out: list[Corner] = []
    for point, radius in ring:
        if out and math.dist(out[-1][0], point) < 1e-6:
            out[-1] = (out[-1][0], max(out[-1][1], radius))
            continue
        out.append((point, radius))
    changed = True
    while changed and len(out) > 3:
        changed = False
        count = len(out)
        for i in range(count if closed else count - 1):
            if not closed and i == 0:
                continue
            a, (p, radius), b = out[i - 1][0], out[i], out[(i + 1) % count][0]
            u, v = _unit(a, p), _unit(p, b)
            if radius == 0 and abs(_cross(u, v)) < STRAIGHT and u[0] * v[0] + u[1] * v[1] > 0:
                del out[i]
                changed = True
                break
    return out


# ------------------------------------------------------------------ the edge and its offset


def _project(points: list[Point], p: Point, closed: bool) -> tuple[int, float, Point, float]:
    """The nearest point of a polyline: its segment, the share along it, the point, and the distance."""
    best = (0, 0.0, points[0], math.inf)
    count = len(points)
    for i in range(count if closed else count - 1):
        a, b = points[i], points[(i + 1) % count]
        length2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / length2))
        foot = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        distance = math.dist(p, foot)
        if distance < best[3] - 1e-12:
            best = (i, t, foot, distance)
    return best


def _length(points: list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:], strict=False))


def _arc_length(chain: list[Corner]) -> float:
    """The chain's length along its arcs, not through the corners they round."""
    points = [c[0] for c in chain]
    total = _length(points)
    for i in range(1, len(points) - 1):
        if chain[i][1] > 0 and math.dist(points[i - 1], points[i]) > 0 and math.dist(points[i], points[i + 1]) > 0:
            theta = _interior(points, i)
            if 1e-6 < theta < math.pi - 1e-6:
                total += chain[i][1] * (math.pi - theta) - 2 * chain[i][1] / math.tan(theta / 2)
    return total


def edge_chain(ring: list[Corner], edge: tuple[Point, Point]) -> tuple[list[Corner], bool]:
    """The outline between the two edge points, the shorter way round, from the first to the second,
    and whether the board lies on its left."""
    points = [c[0] for c in ring]
    count = len(points)
    if count < 3:
        raise TP._error("the board outline has too few points for a lane to follow")
    ia, ta, fa, _ = _project(points, edge[0], closed=True)
    ib, tb, fb, _ = _project(points, edge[1], closed=True)
    ahead = (ib - ia) % count or (count if ta > tb else 0)
    back = (ia - ib) % count or (count if tb > ta else 0)
    forward = [(fa, 0.0)] + [ring[(ia + k) % count] for k in range(1, ahead + 1)] + [(fb, 0.0)]
    backward = [(fa, 0.0)] + [ring[(ia - k) % count] for k in range(back)] + [(fb, 0.0)]
    area = sum(_cross(points[i], points[(i + 1) % count]) for i in range(count)) / 2
    if _arc_length(forward) <= _arc_length(backward):
        chain, left = forward, area > 0
    else:
        chain, left = backward, area < 0
    return _bridged(_straightened(chain, closed=False), left), left


def _bridged(chain: list[Corner], left: bool) -> list[Corner]:
    """The chain without the tabs that stick out of the board: where a straight edge leaves the
    board's side of a base of at most ``TAB_MM`` and comes back to run on in line, however far it
    sticks out, the chain runs along the base. A corner or the end of a narrow tail is no tab: the
    edge does not run on in line past it."""
    side = 1.0 if left else -1.0  # the board lies where the cross product has this sign
    out = [chain[0]]
    i = 0
    while i < len(chain) - 1:
        a = chain[i][0]
        reach = i + 1
        before = _unit(chain[i - 1][0], a) if i >= 1 else None
        for j in range(i + 2, len(chain) - 1):
            b = chain[j][0]
            span = math.dist(a, b)
            if before is None or not 1e-9 < span <= TAB_MM:
                continue
            base = ((b[0] - a[0]) / span, (b[1] - a[1]) / span)
            after = _unit(b, chain[j + 1][0])
            if base[0] * before[0] + base[1] * before[1] < TAB_ALIGN or base[0] * after[0] + base[1] * after[1] < TAB_ALIGN:
                continue
            sides = [side * _cross(base, (chain[k][0][0] - a[0], chain[k][0][1] - a[1])) for k in range(i + 1, j)]
            if all(s < 1e-3 for s in sides) and min(sides) < -1e-3:
                reach = j
        out.append(chain[reach])
        i = reach
    return out


def offset_chain(chain: list[Corner], distance: float, left: bool) -> list[Corner]:
    """The chain moved ``distance`` towards its left (or right) side: each run parallel, each
    corner where two moved runs meet, an arc's radius smaller by the distance where the chain turns
    towards that side and larger where it turns away; a sharp corner turning away gets an arc of the
    distance about it."""
    points = [c[0] for c in chain]
    units = [_unit(a, b) for a, b in zip(points, points[1:], strict=False)]

    def normal(u: Point) -> Point:
        return (-u[1], u[0]) if left else (u[1], -u[0])

    first, last = normal(units[0]), normal(units[-1])
    out: list[Corner] = [((points[0][0] + distance * first[0], points[0][1] + distance * first[1]), 0.0)]
    for i in range(1, len(points) - 1):
        u0, u1 = units[i - 1], units[i]
        n0, n1 = normal(u0), normal(u1)
        p0 = (points[i][0] + distance * n0[0], points[i][1] + distance * n0[1])
        p1 = (points[i][0] + distance * n1[0], points[i][1] + distance * n1[1])
        turn = _cross(u0, u1)
        if abs(turn) < 1e-12:
            out.append((p0, chain[i][1]))
            continue
        t = _cross((p1[0] - p0[0], p1[1] - p0[1]), u1) / turn
        corner = (p0[0] + u0[0] * t, p0[1] + u0[1] * t)
        inward = (turn > 0) == left
        out.append((corner, max(chain[i][1] - distance, 0.0) if inward else chain[i][1] + distance))
    out.append(((points[-1][0] + distance * last[0], points[-1][1] + distance * last[1]), 0.0))
    return _unturned(out, units)


def _unturned(corners: list[Corner], units: list[Point]) -> list[Corner]:
    """The offset without the runs it turned round, such as the short side of a chamfer moved
    further than its length: the runs on either side meet instead."""
    corners, units = list(corners), list(units)
    changed = True
    while changed and len(corners) > 3:
        changed = False
        for k in range(1, len(corners) - 2):
            a, b = corners[k][0], corners[k + 1][0]
            if (b[0] - a[0]) * units[k][0] + (b[1] - a[1]) * units[k][1] > 1e-9:
                continue
            u0, u1 = units[k - 1], units[k + 1]
            turn = _cross(u0, u1)
            if abs(turn) < 1e-12:
                continue
            p0, p1 = corners[k - 1][0], corners[k + 2][0]
            s = _cross((p1[0] - p0[0], p1[1] - p0[1]), u1) / turn
            meet = (p0[0] + u0[0] * s, p0[1] + u0[1] * s)
            corners[k:k + 2] = [(meet, max(corners[k][1], corners[k + 1][1]))]
            del units[k]
            changed = True
            break
    return corners


# ------------------------------------------------------------------ a lane's trace


def _interior(points: list[Point], i: int) -> float:
    """The angle between a point's two segments, pi on a straight run."""
    u, v = _unit(points[i], points[i - 1]), _unit(points[i], points[i + 1])
    return math.acos(max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1])))


def _tangent(points: list[Point], i: int, radius: float) -> float:
    if radius <= 0 or i == 0 or i == len(points) - 1:
        return 0.0
    theta = _interior(points, i)
    if theta < 1e-6 or theta > math.pi - 1e-6:
        return 0.0
    return radius / math.tan(theta / 2)


def _fitted(points: list[Point], radii: list[float], free: list[bool]) -> list[float]:
    """The radii, the free ones made smaller where an arc and its neighbour's would not fit the
    segment between them; the others, the outline's turns and the points given, stay."""
    radii = list(radii)
    for _ in range(4):
        cuts = [_tangent(points, i, r) for i, r in enumerate(radii)]
        fine = True
        for i in range(len(points) - 1):
            need, room = cuts[i] + cuts[i + 1], math.dist(points[i], points[i + 1])
            give = [k for k in (i, i + 1) if free[k] and cuts[k] > 0]
            if need <= room + 1e-9 or not give:
                continue
            fine = False
            spare = room - sum(cuts[k] for k in (i, i + 1) if k not in give)
            scale = max(spare, 0.0) / sum(cuts[k] for k in give) * (1 - 1e-9)
            for k in give:
                radii[k], cuts[k] = radii[k] * scale, cuts[k] * scale
        if fine:
            break
    return radii


def expand(lane: Lane, ring: list[Corner], what: str) -> TP.Trace:
    """The lane's trace: its first points, the turn onto the edge's offset, the offset with its
    turns, the turn off it, and its last points."""
    chain, left = edge_chain(ring, lane.edge)
    if len(chain) < 2 or _length([c[0] for c in chain]) < 1e-6:
        raise TP._error(f"{what}.edge_mm names no edge: its two points meet the outline at one place")
    lane_corners = offset_chain(chain, lane.offset, left)
    path = [c[0] for c in lane_corners]
    leave, join = lane.start[-1][0], lane.end[0][0]
    s, ts, on, _ = _project(path, leave, closed=False)
    e, te, off, _ = _project(path, join, closed=False)
    if (s, ts) > (e, te):
        lane_corners = lane_corners[::-1]
        path = path[::-1]
        s, ts, on, _ = _project(path, leave, closed=False)
        e, te, off, _ = _project(path, join, closed=False)
    for point, (i, step) in ((leave, (0, 1)), (join, (len(path) - 1, -1))):
        a, b = path[i], path[i + step]
        if (point[0] - a[0]) * (b[0] - a[0]) + (point[1] - a[1]) * (b[1] - a[1]) < -1e-6:
            raise TP._error(f"{what} leaves for its lane or returns from it beyond the ends of its edge; put the points of edge_mm "
                            "at or beyond where the lane joins and leaves the edge")
    corners: list[tuple[Point, float, bool]] = [(p, r, False) for p, r in lane.start]
    corners.append((on, lane.radius, True))
    corners += [(p, r, False) for p, r in lane_corners[s + 1:e + 1]]
    corners.append((off, lane.radius, True))
    corners += [(p, r, False) for p, r in lane.end]
    merged: list[tuple[Point, float, bool]] = []
    for point, radius, free in corners:
        if merged and math.dist(merged[-1][0], point) < 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], radius), merged[-1][2] or free)
            continue
        merged.append((point, radius, free))
    points = [c[0] for c in merged]
    radii = [c[1] for c in merged]
    radii[0] = radii[-1] = 0.0
    if len(points) < 2:
        raise TP._error(f"{what} starts and ends at one point")
    radii = _fitted(points, radii, [c[2] for c in merged])
    try:
        TP._corners(points, radii)
    except Exception as exc:  # noqa: BLE001 - a PlanError, named after the lane
        raise TP._error(f"{what} cannot follow the edge from where it leaves to where it returns, such as where it joins the edge "
                        f"inside one of its turns; join it on a straight part: {getattr(exc, 'message', exc)}") from None
    return TP.Trace(lane.net, lane.layer, lane.width, points, radii)
