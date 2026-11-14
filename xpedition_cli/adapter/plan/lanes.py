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
from dataclasses import dataclass, field
from typing import Any, Callable

from . import fanout as FO
from . import trace_plan as TP

Point = tuple[float, float]
Corner = tuple[Point, float]  # a corner and the radius of the arc that rounds it, 0 for a sharp one
JOIN_RADIUS_MM = 0.5  # the radius of the turns onto and off the lane, unless the lane gives one
ARC_PART = math.radians(120)  # an outline arc is taken in parts of at most this sweep
NOTCH_SLACK_MM = 0.01  # a notch's arc ending this close to a bend zone reaches it
STRAIGHT = 1e-4  # sine of the angle under which three outline points count as one straight run
TAB_MM = 5.0  # a lane runs straight past a part of the outline that sticks out over a base this long or shorter
TAB_ALIGN = math.cos(math.radians(10))  # and only where the edge runs on in line on both sides of it
SNAP_MM = 0.005  # points of a lane this close are one: a hair of a segment between them reads as a sharp corner in pcb check
ON_LINE_MM = 1e-3  # a point this close to a lane's line is on it: a real outline's straight edge is straight to a few tenths of a micrometre
KEYS = ("net", "layer", "width_mm", "edge_mm", "offset_mm")
FANOUT_CORNER_MM = 0.2  # the radius of the turn where a fan-out leaves the connector
BEND_ROUND_SHARE = 0.45  # a sharp corner of the outline in a bend zone is rounded over at most this share of each run beside it
BEND_ROUND_MAX_MM = 20.0  # with at most this radius
BEND_ROUND_SAG_MM = 0.01  # and at a corner turning away from the board, an arc that lies at most this far outside the corner
ZONE_SLACK_MM = 0.01  # a corner this close to a bend zone's outline lies in it
FIT_MARGIN_MM = 0.01  # arcs made smaller to fit a segment leave this much of it: a point read back a hair off still fits them, and the piece left is long enough that its direction reads true


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
    from_ref: str | None = None  # the connector whose fan-out the lane starts from, instead of ``start``
    to_ref: str | None = None
    neck: float | None = None  # the width from each end to where the lane joins its line, such as between a connector's vias
    jogs: list[tuple[Point, float, float]] = field(default_factory=list)  # (where, the new offset, how much earlier it starts)
    slants: tuple[bool, bool] = (False, False)  # an end on a named pin, reached from the line at 45 degrees



def parse(items: Any) -> list[Lane]:
    if not isinstance(items, list) or not items:
        raise TP._error("lanes is a list of at least one lane")
    lanes = []
    for index, item in enumerate(items):
        what = f"lanes[{index}]"
        if (not isinstance(item, dict) or set(item) - {*KEYS, "radius_mm", "from_mm", "to_mm", "from", "to", "neck_width_mm", "jogs"}
                or set(KEYS) - set(item) or ("from_mm" in item) == ("from" in item) or ("to_mm" in item) == ("to" in item)):
            raise TP._error(f"{what} has the keys {', '.join(KEYS)}, from_mm or from, to_mm or to, and, if wanted, radius_mm, "
                            "neck_width_mm and jogs")
        net, layer, width = item["net"], item["layer"], item["width_mm"]
        if not isinstance(net, str) or not net:
            raise TP._error(f"{what}.net is a net name")
        if type(layer) is not int or layer < 1:
            raise TP._error(f"{what}.layer is a conductor layer number, 1 at the top")
        if isinstance(width, bool) or not isinstance(width, (int, float)) or not 0 < width <= 25:
            raise TP._error(f"{what}.width_mm is a width of more than 0 and at most 25 mm")
        ends: list[list[Corner]] = []
        refs: list[str | None] = []
        for key in ("from", "to"):
            if key in item:
                if not isinstance(item[key], str) or not item[key]:
                    raise TP._error(f"{what}.{key} is the reference designator of a connector in fanouts, or a pin as J1.5")
                ends.append([])
                refs.append(item[key])
                continue
            points = item[key + "_mm"]
            if not isinstance(points, list) or not points:
                raise TP._error(f"{what}.{key}_mm has at least one point")
            ends.append([TP._corner(p, f"{what}.{key}_mm[{i}]") for i, p in enumerate(points)])
            refs.append(None)
        if (ends[0] and ends[0][0][1]) or (ends[1] and ends[1][-1][1]):
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
        neck = item.get("neck_width_mm")
        if neck is not None and (isinstance(neck, bool) or not isinstance(neck, (int, float)) or not 0 < neck < width):
            raise TP._error(f"{what}.neck_width_mm is a width of more than 0 and less than its width_mm")
        jogs = []
        for k, jog in enumerate(item.get("jogs") or []):
            if not isinstance(jog, dict) or set(jog) != {"at_mm", "offset_mm"} or isinstance(jog["offset_mm"], bool) \
                    or not isinstance(jog["offset_mm"], (int, float)) or not 0 < jog["offset_mm"] <= 100:
                raise TP._error(f"{what}.jogs[{k}] has at_mm, a point beside the edge where the lane moves, and offset_mm, its "
                                "new distance from the edge, more than 0")
            jogs.append((TP._point(jog["at_mm"], f"{what}.jogs[{k}].at_mm"), float(jog["offset_mm"]), 0.0))
        if "jogs" in item and not jogs:
            raise TP._error(f"{what}.jogs is a list of at least one jog")
        lanes.append(Lane(net, layer, round(float(width), 4), ends[0], ends[1],
                          (TP._point(edge[0], f"{what}.edge_mm[0]"), TP._point(edge[1], f"{what}.edge_mm[1]")),
                          float(offset), float(radius), refs[0], refs[1], None if neck is None else round(float(neck), 4), jogs))
    return lanes


JOG_MARGIN_MM = 0.05  # a jog's diagonal keeps this far from the turns of the edge either side of it
STAGGER_ROUND_MM = 0.01


def stagger(lanes: list[Lane], clearance: float) -> None:
    """Jogs of a band, lanes of one layer and edge that move at one place, start one after another,
    so their diagonals keep the clearance: those moving away from the edge from the outermost in,
    those moving towards it from the innermost out, each ``STAGGER`` earlier than the next, where
    the diagonals of lanes a pitch apart would come closer than their widths and the clearance."""
    groups: dict[tuple[Any, ...], list[tuple[Lane, int]]] = {}
    for lane in lanes:
        for k, (at, _offset, _early) in enumerate(lane.jogs):
            key = (lane.layer, lane.edge, round(at[0], 0), round(at[1], 0), k)
            groups.setdefault(key, []).append((lane, k))
    for members in groups.values():
        if len(members) < 2:
            continue
        before = [lane.offset if k == 0 else lane.jogs[k - 1][1] for lane, k in members]
        order = sorted(range(len(members)), key=lambda i: before[i])
        pitch = min((before[order[i + 1]] - before[order[i]] for i in range(len(order) - 1)), default=0.0)
        widest = max(lane.width for lane, _k in members)
        step = max(0.0, math.sqrt(2.0) * (widest + clearance) - pitch)
        step = math.ceil(step / STAGGER_ROUND_MM) * STAGGER_ROUND_MM
        for rank, i in enumerate(order):
            lane, k = members[i]
            at, offset, _early = lane.jogs[k]
            outward = offset > before[i]
            lane.jogs[k] = (at, offset, step * (rank if outward else len(order) - 1 - rank))


def _jogged(lane: Lane, chain: list[Corner], left: bool, path_forward: bool, what: str) -> list[Corner]:
    """The lane's line with its jogs: on its offset, then across at 45 degrees to each jog's offset
    where the jog says, on a straight run of the edge. ``path_forward`` says whether the lane runs
    with the chain."""
    offsets = [lane.offset] + [offset for _at, offset, _early in lane.jogs]
    lines = [offset_chain(chain, d, left) for d in offsets]
    if not path_forward:
        lines = [line[::-1] for line in lines]
    out = list(lines[0])
    for k, (at, offset, early) in enumerate(lane.jogs):
        current = [c[0] for c in out]
        i, share, foot, _distance = _project(current, at, closed=False)
        a, b = current[i], current[i + 1]
        u = _unit(a, b)
        length = math.dist(a, b)
        where = share * length - early
        move = abs(offset - offsets[k])
        start_r, end_r = out[i][1] if i > 0 else 0.0, out[i + 1][1] if i + 1 < len(out) - 1 else 0.0
        if where - JOG_MARGIN_MM - _tangent_room(out, i, start_r) < 0 or where + move + JOG_MARGIN_MM + _tangent_room(out, i + 1, end_r) > length:
            raise TP._error(f"{what}.jogs[{k}] needs {move + 2 * JOG_MARGIN_MM:g} mm of a straight run of the edge; move at_mm "
                            "to the middle of a straight run, away from the edge's turns")
        start = (a[0] + u[0] * where, a[1] + u[1] * where)
        target = [c[0] for c in lines[k + 1]]
        j, share2, foot2, _d2 = _project(target, (start[0] + u[0] * move, start[1] + u[1] * move), closed=False)
        out = out[:i + 1] + [(start, 0.0), (foot2, 0.0)] + list(lines[k + 1][j + 1:])
    return out


def _tangent_room(corners: list[Corner], index: int, radius: float) -> float:
    """How far along its runs the arc of a rounded corner reaches: a jog keeps clear of it."""
    if radius <= 0 or index <= 0 or index >= len(corners) - 1:
        return 0.0
    points = [c[0] for c in corners]
    return _tangent(points, index, radius)


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


def sharpened(ring: list[Corner], bends: list[tuple[float, float, float, float]],
              zones: list[list[Point]] | None = None) -> list[Corner]:
    """The ring with each rounded notch, a corner where the outline turns away from the board's
    inside, made sharp where its arc reaches a bend zone. Lanes, guards and rings then run straight
    on to the end of its edges and turn about the point where the edges meet, so they cross the
    bend zone straight and turn outside it, still evenly spaced. Given the zones' own outlines, each
    sharp corner of the outline inside a zone, such as where a flex arm drawn as straight runs curves
    to meet its head, is rounded with the widest arc that fits: what follows the edge then curves
    there about the arc's centre, evenly spaced, and turns at no corner inside the zone."""
    if not bends or len(ring) < 3:
        return ring
    points = [c[0] for c in ring]
    count = len(points)
    area = sum(points[i][0] * points[(i + 1) % count][1] - points[(i + 1) % count][0] * points[i][1] for i in range(count))
    out = list(ring)
    for i, (corner, radius) in enumerate(ring):
        if radius <= 0:
            continue
        u0, u1 = _unit(points[i - 1], corner), _unit(corner, points[(i + 1) % count])
        turn = _cross(u0, u1)
        if abs(turn) < 1e-12 or (turn > 0) == (area > 0):
            continue  # it turns towards the inside: a corner, not a notch
        reach = radius * math.tan(math.acos(max(-1.0, min(1.0, u0[0] * u1[0] + u0[1] * u1[1]))) / 2)
        ends = ((corner[0] - u0[0] * reach, corner[1] - u0[1] * reach), (corner[0] + u1[0] * reach, corner[1] + u1[1] * reach))
        if zones:  # a zone's own outline, which a slanted zone's band does not cover
            reached = any(_in_zones(end, zones, NOTCH_SLACK_MM) for end in ends)
        else:
            reached = any(z[0] - NOTCH_SLACK_MM <= x <= z[2] + NOTCH_SLACK_MM and z[1] - NOTCH_SLACK_MM <= y <= z[3] + NOTCH_SLACK_MM
                          for x, y in ends for z in bends)
        if reached:
            out[i] = (corner, 0.0)
    return _rounded(out, [radius > 0 for _corner, radius in ring], zones, area > 0) if zones else out


def _in_zones(point: Point, zones: list[list[Point]], slack: float) -> bool:
    """Whether a point lies in one of the zones' outlines, or within ``slack`` of one."""
    return any(TP.inside(point, zone) or min(TP.point_segment(point, zone[k], zone[(k + 1) % len(zone)]) for k in range(len(zone)))
               <= slack for zone in zones if len(zone) >= 3)


def _rounded(ring: list[Corner], arcs: list[bool], zones: list[list[Point]], counter_clockwise: bool) -> list[Corner]:
    """The ring with each sharp corner inside one of the ``zones`` that was no arc of the outline
    (``arcs``) rounded: its arc takes at most ``BEND_ROUND_SHARE`` of a run it shares with another
    such corner, and of any other run what the arc at its far end leaves. At a corner where the
    outline turns away from the board, the arc lies outside the corner, nearer what follows the
    edge on the inside: there it lies at most ``BEND_ROUND_SAG_MM`` from it."""
    points = [c[0] for c in ring]
    count = len(points)

    def turn(i: int) -> float:
        u0, u1 = _unit(points[i - 1], points[i]), _unit(points[i], points[(i + 1) % count])
        return math.acos(max(-1.0, min(1.0, u0[0] * u1[0] + u0[1] * u1[1])))

    chosen = {i for i, (p, radius) in enumerate(ring)
              if radius == 0 and not arcs[i] and 1e-6 < turn(i) < math.pi - 1e-3 and _in_zones(p, zones, ZONE_SLACK_MM)}
    out = list(ring)
    for i in sorted(chosen):
        rooms = []
        for j in ((i - 1) % count, (i + 1) % count):
            run = math.dist(points[i], points[j])
            if j in chosen:
                rooms.append(run * BEND_ROUND_SHARE)
            else:
                rooms.append(max(run - ring[j][1] * math.tan(turn(j) / 2), 0.0) * 2 * BEND_ROUND_SHARE)
        radius = min(min(rooms) / math.tan(turn(i) / 2), BEND_ROUND_MAX_MM)
        if (_cross(_unit(points[i - 1], points[i]), _unit(points[i], points[(i + 1) % count])) > 0) != counter_clockwise:
            radius = min(radius, BEND_ROUND_SAG_MM / (1 / math.cos(turn(i) / 2) - 1))
        out[i] = (points[i], radius)
    return out


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
            spare = room - sum(cuts[k] for k in (i, i + 1) if k not in give) - FIT_MARGIN_MM
            scale = max(spare, 0.0) / sum(cuts[k] for k in give)
            for k in give:
                radii[k], cuts[k] = radii[k] * scale, cuts[k] * scale
        if fine:
            break
    return radii


def _from_fanout(fanout: FO.Fanout, lane: Lane, path: list[Point], what: str, ref: str, clearance: float,
                 bends: list[list[Point]] | None = None) -> list[Corner]:
    """The points from where the lane's net leaves the connector to the lane's line, ``path[0]``
    being the lane's end at that connector: straight towards the board's body until clear of the
    connector's pads, then across to the lane's line at no more than 45 degrees, before the lane's
    edge begins, and steeper where that turn onto the line would fall in one of the ``bends``, so
    that it ends before the zone."""
    exits = [at for at, layer in fanout.exits.get(lane.net, []) if layer == lane.layer]
    if not exits:
        on = sorted({layer for _at, layer in fanout.exits.get(lane.net, [])})
        raise TP._error(f"{what} starts at {ref}, where {lane.net} leaves on layer {on or 'none'}, not on layer {lane.layer}")
    a, b = path[0], path[1]
    u = _unit(a, b)

    def off_line(p: Point) -> float:
        return _cross(u, (p[0] - a[0], p[1] - a[1]))

    start = min(exits, key=lambda p: abs(off_line(p)))
    past: float | None = None
    for _attempt in range(len(fanout.lifted) + 1):
        clear = FO.leave(fanout, start, lane.layer, lane.width, clearance, past)
        reach = (clear[0] - a[0]) * u[0] + (clear[1] - a[1]) * u[1]
        if reach > 1e-6:
            raise TP._error(f"{what} leaves {ref} past where its edge begins; begin the edge further along, after the connector")
        side = off_line(clear)
        if abs(side) <= ON_LINE_MM:
            return [(start, 0.0)]  # on the lane's line already: straight on into the lane
        forward = min(reach + abs(side), 0.0)
        entry = _bend_entry(a, u, reach, bends or [])
        if entry is not None and forward > entry - lane.radius and entry - lane.radius > reach + 1e-6:
            forward = entry - lane.radius  # steeper than 45 degrees, its turn onto the line done before the bend zone
        join = (a[0] + u[0] * forward, a[1] + u[1] * forward)
        # a via the fan-out lifts a near pin through, standing in this way: leave past it, then cross
        need = fanout.via_radius + clearance + lane.width / 2
        hit = [via for via in fanout.lifted if via != start
               and min(TP.point_segment(via, start, clear), TP.point_segment(via, clear, join)) < need - 1e-9]
        if not hit:
            break
        tx, ty = fanout.toward
        past = max(via[0] * tx + via[1] * ty for via in hit) + fanout.via_radius
    corners: list[Corner] = [(start, 0.0)]
    if math.dist(start, clear) > 1e-6:
        # the turn's arc fits the shorter run beside it: a lane a hair off the pin's line turns with a hair of an arc
        room = min(math.dist(start, clear), math.dist(clear, join))
        corners.append((clear, min(FANOUT_CORNER_MM, room / (2 * math.tan(math.radians(22.5))) * 0.9)))
    if math.dist(corners[-1][0], join) > 1e-6:
        corners.append((join, 0.0))
    return corners


def _bend_entry(a: Point, u: Point, start: float, bends: list[list[Point]]) -> float | None:
    """How far along the line through ``a`` in direction ``u`` it first enters a bend zone, each
    zone its own polygon, from ``start`` on; None when it enters none."""
    first = None
    for zone in bends:
        if len(zone) < 3:
            continue
        hits = []
        for c, d in zip(zone, zone[1:] + zone[:1], strict=True):
            sx, sy = d[0] - c[0], d[1] - c[1]
            denominator = u[0] * sy - u[1] * sx
            if abs(denominator) < 1e-12:
                continue
            t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / denominator
            w = ((c[0] - a[0]) * u[1] - (c[1] - a[1]) * u[0]) / denominator
            if -1e-9 <= w <= 1 + 1e-9:
                hits.append(t)
        point = (a[0] + u[0] * start, a[1] + u[1] * start)
        if TP.inside(point, zone):
            entry = start
        else:
            later = [t for t in hits if t >= start]
            if not later:
                continue
            entry = min(later)
        if first is None or entry < first:
            first = entry
    return first


def expand(lane: Lane, ring: list[Corner], what: str, fanouts: dict[str, FO.Fanout] | None = None,
           clearance: float = 0.1, bends: list[list[Point]] | None = None) -> TP.Trace:
    """The lane's trace: its first points, the turn onto the edge's offset, the offset with its
    turns, the turn off it, and its last points; at a connector of ``fanouts``, from where its net
    leaves the connector."""
    chain, left = edge_chain(ring, lane.edge)
    if len(chain) < 2 or _length([c[0] for c in chain]) < 1e-6:
        raise TP._error(f"{what}.edge_mm names no edge: its two points meet the outline at one place")
    lane_corners = offset_chain(chain, lane.offset, left)
    path = [c[0] for c in lane_corners]
    if lane.jogs:  # the line it runs on changes part-way: which way the lane runs decides where each jog starts
        if lane.from_ref is not None:
            exits = [at for spots in (fanouts or {}).get(lane.from_ref, FO.Fanout()).exits.values() for at, _layer in spots]
            first = exits[0] if exits else path[0]
        else:
            first = lane.start[-1][0]
        forward = math.dist(first, path[0]) <= math.dist(first, path[-1])
        lane_corners = _jogged(lane, chain, left, forward, what)
        if not forward:
            lane_corners = lane_corners[::-1]
        path = [c[0] for c in lane_corners]
    for ref, at_start in ((lane.from_ref, True), (lane.to_ref, False)):
        if ref is None:
            continue
        if not fanouts or ref not in fanouts:
            raise TP._error(f"{what} names {ref}, which fanouts does not list")
        exits = [at for spots in fanouts[ref].exits.values() for at, _layer in spots]
        probe = exits[0] if exits else path[0]
        near_start = math.dist(probe, path[0]) <= math.dist(probe, path[-1])
        points = _from_fanout(fanouts[ref], lane, path if near_start else path[::-1], what, ref, clearance, bends)
        if at_start:
            lane.start = points
        else:
            lane.end = points[::-1]
    if lane.slants[0] or _past_end(lane.start, path, True):
        lane.start = _slanted(lane.start, path, True)
    if lane.slants[1] or _past_end(lane.end, path, False):
        lane.end = _slanted(lane.end, path, False)
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
        u = _unit(a, b)
        beyond = (point[0] - a[0]) * u[0] + (point[1] - a[1]) * u[1] < -1e-6
        if beyond and abs(_cross(u, (point[0] - a[0], point[1] - a[1]))) > ON_LINE_MM:
            raise TP._error(f"{what} leaves for its lane or returns from it beyond the ends of its edge, off the lane's line; put the "
                            "points of edge_mm at or beyond where the lane joins and leaves the edge, or the point on the lane's line")
        if beyond:  # on the line run on past the edge's end: the lane turns there, not at the end
            if i == 0:
                on = point
            else:
                off = point
    corners: list[tuple[Point, float, bool]] = [(p, r, False) for p, r in lane.start]
    corners.append((on, lane.radius, True))
    corners += [(p, r, False) for p, r in lane_corners[s + 1:e + 1]]
    corners.append((off, lane.radius, True))
    corners += [(p, r, False) for p, r in lane.end]
    merged: list[tuple[Point, float, bool]] = []
    for point, radius, free in corners:
        if merged and math.dist(merged[-1][0], point) < SNAP_MM:  # such as a given point a hair off the lane's line
            # the point on the lane's line stays, unless the other is the lane's end: a line a hair off its offset runs at a slant
            keep = point if free and len(merged) > 1 else merged[-1][0]
            merged[-1] = (keep, max(merged[-1][1], radius), merged[-1][2] or free)
            continue
        merged.append((point, radius, free))
    # a point on a straight run, such as where the edge ends short of where the lane leaves its line, is no corner:
    # left in, it cuts the run beside an arc into a piece shorter than the arc needs
    merged = [c for i, c in enumerate(merged) if i in (0, len(merged) - 1) or c[1] > 0 or c[2]
              or _interior([merged[i - 1][0], c[0], merged[i + 1][0]], 1) < math.pi - 1e-9]
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
    joins = (next(i for i, c in enumerate(merged) if c[2]), max(i for i, c in enumerate(merged) if c[2]))
    return TP.Trace(lane.net, lane.layer, lane.width, points, radii, joins=joins, origin=what)


def _slanted(points: list[Corner], path: list[Point], first: bool) -> list[Corner]:
    """A lane end off the lane's line, such as a pin, or a pin and the point where its trace clears its
    row: reached from the line at 45 degrees, leaving the line (or its run on past the end) as far
    before the point nearest it as that point lies beside it."""
    if not points or len(points) > 2:
        return points
    point = points[-1][0] if first else points[0][0]
    a, b = (path[1], path[0]) if first else (path[-2], path[-1])  # the end's run, pointing out of the lane
    u = _unit(a, b)
    along = (point[0] - a[0]) * u[0] + (point[1] - a[1]) * u[1]
    beside = abs(_cross(u, (point[0] - a[0], point[1] - a[1])))
    if beside <= ON_LINE_MM or along - beside < 0.0:
        return points  # on the line, or too close to the run's start for a slant: as given
    on_line = (a[0] + u[0] * (along - beside), a[1] + u[1] * (along - beside))
    return [*points, (on_line, 0.0)] if first else [(on_line, 0.0), *points]


def _past_end(points: list[Corner], path: list[Point], first: bool) -> bool:
    """Whether a lane end is one point beyond its edge's end and off its line, such as a via before a bend
    zone where the edge begins: it is reached at 45 degrees, as a pin is."""
    if len(points) != 1 or len(path) < 2:
        return False
    a, b = (path[1], path[0]) if first else (path[-2], path[-1])
    u = _unit(a, b)
    point = points[0][0]
    along = (point[0] - b[0]) * u[0] + (point[1] - b[1]) * u[1]
    return along > 1e-6 and abs(_cross(u, (point[0] - b[0], point[1] - b[1]))) > ON_LINE_MM


def resolve_pins(lanes: list[Lane], pins: dict[str, Point | list[Point]]) -> None:
    """Lane ends named as a pin, ``J1.5``: the pin's position, and where given the point where a trace
    leaving it straight clears its row's pads, from which the lane's line is reached at 45 degrees."""
    for index, lane in enumerate(lanes):
        for ref, first in ((lane.from_ref, True), (lane.to_ref, False)):
            if ref is None or "." not in ref:
                continue
            if ref not in pins:
                raise TP._error(f"lanes[{index}] ends at {ref}, which is no placed pin of the board")
            given = pins[ref]
            way = [given] if isinstance(given, tuple) else list(given)
            corners = [(point, 0.0) for point in way]
            if first:
                lane.start, lane.from_ref, lane.slants = corners, None, (True, lane.slants[1])
            else:
                lane.end, lane.to_ref, lane.slants = corners[::-1], None, (lane.slants[0], True)


def swapped(traces: list[TP.Trace]) -> list[str]:
    """Two lanes of one layer that reach their lines in the other order than they lie at an end, such
    as two pins that two connectors put in opposite orders: from that end to their lines they must
    cross. Each such pair once, with where, before the check finds them crossing or too close."""
    ends = []
    for trace in traces:
        if trace.joins is None or len(trace.points) < 3:
            continue
        first, last = trace.joins
        points = trace.points
        # from each end straight to where it joins its line, then on along the line to its next corner
        start = [points[0], points[first], points[min(first + 1, len(points) - 1)]]
        finish = [points[-1], points[last], points[max(last - 1, 0)]]
        ends.append((trace, [start, finish]))
    notes = []
    for i, (a, ways_a) in enumerate(ends):
        for b, ways_b in ends[i + 1:]:
            if a.layer != b.layer or a.net == b.net:
                continue
            for way_a, way_b in zip(ways_a, ways_b, strict=True):
                if not any(_crossing(p, q, r, t) for p, q in zip(way_a, way_a[1:], strict=False)
                           for r, t in zip(way_b, way_b[1:], strict=False) if math.dist(p, q) > 1e-6 and math.dist(r, t) > 1e-6):
                    continue
                p = way_a[0]
                notes.append(f"{a.origin} ({a.net}) and {b.origin} ({b.net}) lie in one order where they end near ({p[0]:.3f}, "
                             f"{p[1]:.3f}) mm and in the other on their lines, so on layer {a.layer} they must cross there: put one "
                             "on another layer through vias, or swap their distances from the edge")
                break
    return notes


def _crossing(p: Point, q: Point, r: Point, t: Point) -> bool:
    """Whether segments p-q and r-t cross each other, not just touch at an end."""
    def side(a: Point, b: Point, c: Point) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2, d3, d4 = side(p, q, r), side(p, q, t), side(r, t, p), side(r, t, q)
    return d1 * d2 < -1e-12 and d3 * d4 < -1e-12


WIDEN_STEP_MM = 0.05  # a neck is tested at the lane's width in pieces this long
WIDEN_MIN_MM = 0.1  # a stretch at the lane's width shorter than this stays at the neck's: no width changes a hair apart


def widened(trace: TP.Trace, width: float, clear: Callable[[Point, Point, float], bool]) -> list[TP.Trace]:
    """A narrow trace, such as a lane's neck or a fan-out stub, at ``width`` wherever a piece of it
    at that width clears (``clear``), and as narrow as it is only where it must be, such as between
    two of a connector's vias; each corner's arc is taken whole. The pieces in order, each a trace."""
    if width <= trace.width + 1e-9 or len(trace.points) < 2:
        return [trace]
    points, radii = trace.points, trace.radii
    arcs = TP._corners(points, radii)
    parts: list[tuple[list[Point], list[float], bool]] = []  # each straight piece or arc: its points, their radii, wide
    here = points[0]
    for i in range(1, len(points)):
        arc = arcs[i] if i < len(points) - 1 else None
        stop = arc[0] if arc else points[i]
        run = math.dist(here, stop)
        count = max(1, math.ceil(run / WIDEN_STEP_MM - 1e-9)) if run > 1e-9 else 0
        for k in range(count):
            a = (here[0] + (stop[0] - here[0]) * k / count, here[1] + (stop[1] - here[1]) * k / count)
            b = (here[0] + (stop[0] - here[0]) * (k + 1) / count, here[1] + (stop[1] - here[1]) * (k + 1) / count)
            parts.append(([a, b], [0.0, 0.0], clear(a, b, width)))
        if arc:
            start, _centre, end, radius, _ccw = arc
            chords = TP.sampled([start, points[i], end], [0.0, radius, 0.0])
            parts.append(([start, points[i], end], [0.0, radius, 0.0],
                          all(clear(a, b, width) for a, b in zip(chords, chords[1:], strict=False))))
            here = end
        elif i < len(points) - 1:
            here = points[i]
    if not parts or not any(wide for _p, _r, wide in parts):
        return [trace]
    lengths = [sum(math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False)) for pts, _r, _w in parts]
    runs: list[list[int]] = []
    for index, (_pts, _rs, wide) in enumerate(parts):
        if runs and parts[runs[-1][0]][2] == wide:
            runs[-1].append(index)
        else:
            runs.append([index])
    for run in runs:  # a wide stretch too short to be worth its two width changes stays narrow
        if parts[run[0]][2] and sum(lengths[i] for i in run) < WIDEN_MIN_MM:
            for i in run:
                parts[i] = (parts[i][0], parts[i][1], False)
    out: list[TP.Trace] = []
    for index, (pts, rs, wide) in enumerate(parts):
        span = (width if wide else trace.width)
        if out and abs(out[-1].width - span) < 1e-9:
            last = out[-1]
            if rs[1:-1] and any(rs[1:-1]):  # an arc: its corner and end follow the run, which ends at the arc's start
                last.points += pts[1:]
                last.radii[-1:] = [0.0]
                last.radii += rs[1:]
            else:
                last.points.append(pts[-1])
                last.radii.append(0.0)
        else:
            out.append(TP.Trace(trace.net, trace.layer, span, list(pts), list(rs), origin=trace.origin))
        if index == len(parts) - 1:
            break
    for piece in out:  # drop the points a straight run was cut into, keeping corners
        kept_p, kept_r = [piece.points[0]], [0.0]
        for j in range(1, len(piece.points) - 1):
            a, q, b = kept_p[-1], piece.points[j], piece.points[j + 1]
            cross = (q[0] - a[0]) * (b[1] - q[1]) - (q[1] - a[1]) * (b[0] - q[0])
            if piece.radii[j] or abs(cross) > 1e-12:
                kept_p.append(q)
                kept_r.append(piece.radii[j])
        kept_p.append(piece.points[-1])
        kept_r.append(0.0)
        # a piece cut where an arc ends holds that arc to the micrometre: fitted again, so a hair over does not refuse it
        piece.points, piece.radii = kept_p, _fitted(kept_p, kept_r, [True] * len(kept_p))
    return [piece for piece in out if TP.length(piece) > 1e-6]


def necked(lane: Lane, trace: TP.Trace) -> list[TP.Trace]:
    """The lane's trace, its stretches from each end to where it joins its line, its turn onto the
    line included, as narrow as its neck, and the rest as wide as the lane; or the trace whole."""
    joins = trace.joins
    if lane.neck is None or joins is None:
        return [trace]
    arcs = TP._corners(trace.points, trace.radii)
    first, last = joins
    points, radii = trace.points, trace.radii

    def piece(width: float, pts: list[Point], rs: list[float]) -> TP.Trace:
        kept_p, kept_r = [pts[0]], [rs[0]]
        for point, radius in zip(pts[1:], rs[1:], strict=True):
            if math.dist(point, kept_p[-1]) > 1e-9:
                kept_p.append(point)
                kept_r.append(radius)
        kept_r[0] = kept_r[-1] = 0.0
        # a piece cut where an arc ends holds that arc to the micrometre: fitted again, so a hair over does not refuse it
        kept_r = _fitted(kept_p, kept_r, [True] * len(kept_p))
        return TP.Trace(lane.net, lane.layer, width, kept_p, kept_r, origin=trace.origin)

    on = arcs[first][2] if arcs[first] else points[first]  # the end of the turn onto the line
    off = arcs[last][0] if arcs[last] else points[last]  # the start of the turn off it
    head = piece(lane.neck, [*points[:first + 1], on], [*radii[:first + 1], 0.0])
    body = piece(lane.width, [on, *points[first + 1:last], off], [0.0, *radii[first + 1:last], 0.0])
    tail = piece(lane.neck, [off, *points[last:]], [0.0, *radii[last:]])
    return [part for part in (head, body, tail) if len(part.points) >= 2 and TP.length(part) > 1e-6]
