"""Ties that give a hatched band over a bend zone its copper.

Layout makes a plane shape's copper only where a pad, trace or via of its net on its layer touches
the shape. A band hatched over a bend zone on an inner ground layer touches none: no via may stand
in a bend zone and nothing else runs there, so the band stays empty while the solid shapes beside
it are poured. A tie is a trace of the net across the band, square to each side it crosses as bend
zones want, reaching into the solid copper on both sides: it gives the band something to touch and
joins it to its net. It runs straight where the band's two crossed sides are parallel; where they
lie at an angle, such as a zone over the turn of an arm, it turns from the one to the other in one
arc too wide to count as a turn in the zone.

The ties stand at a quarter, the half and three quarters of the band's length, each moved to the
nearest spot where it clears other nets' copper on its layer, best where it does not lie straight
above or below a trace of another layer crossing the band. Where the other layers' traces fill the
band, a tie lies over one of them as the hatch's own lines do: it is part of the mesh.
"""
from __future__ import annotations

import math
from typing import Callable

from . import board_metrics as BM
from . import trace_plan as TP
from .shapes import box_disc, box_polygon, inside, point_segment, segment_box, segment_polygon

Point = tuple[float, float]
Box = tuple[float, float, float, float]
Row = tuple[float, float, float]  # a tie's (x, y, r) rows as Layout draws them (``trace_plan.drawn``)
EXTEND_MM = 0.3  # how far a tie reaches past the band into the copper beside it
SHARES = (0.25, 0.5, 0.75)
STEP_MM = 0.1
STACK_GAP_MM = 0.05  # a tie keeps this much more than half the two widths off a crossing of another layer


def touches(zone: Box, net: str, layer: int, copper: TP.Copper) -> bool:
    """Whether a pad, trace or via of the net on the layer reaches into the band."""
    if any(n == net and lay == layer and box_polygon(zone, poly) for lay, n, poly in copper.pads):
        return True
    if any(n == net and lay == layer and any(segment_box(a, b, zone) < w / 2 for a, b in zip(pts, pts[1:], strict=False))
           for lay, n, w, pts in copper.traces):
        return True
    return any(n == net and box_disc(zone, x, y, r) for n, x, y, r in copper.vias)


def _crossings(zone: Box, along_x: bool, layer: int, copper: TP.Copper) -> list[tuple[float, float]]:
    """Where the traces of the other layers cross the band, and their widths."""
    out = []
    for lay, _net, width, points in copper.traces:
        if lay == layer:
            continue
        for a, b in zip(points, points[1:], strict=False):
            part = BM._clipped(a, b, zone)
            if part is None:
                continue
            middle = ((part[0][0] + part[1][0]) / 2, (part[0][1] + part[1][1]) / 2)
            out.append((middle[0] if along_x else middle[1], width))
    return out


def ties(zone: Box, along_x: bool, net: str, layer: int, width: float, copper: TP.Copper,
         clear: Callable[[Point, Point], bool]) -> list[list[Row]] | None:
    """The ties across a band that runs along x (``along_x``) or y, or None where the band touches
    the net on its layer already and needs none. ``clear`` says whether a tie clears other nets'
    copper and keeps inside the board."""
    if touches(zone, net, layer, copper):
        return None
    low, high = (zone[0], zone[2]) if along_x else (zone[1], zone[3])
    across = (zone[1] - EXTEND_MM, zone[3] + EXTEND_MM) if along_x else (zone[0] - EXTEND_MM, zone[2] + EXTEND_MM)
    others = _crossings(zone, along_x, layer, copper)
    reach = (high - low) / 8
    chosen: list[float] = []
    out: list[tuple[Point, Point]] = []
    for share in SHARES:
        target = low + share * (high - low)
        spots = [target] + [target + sign * k * STEP_MM for k in range(1, int(math.floor(reach / STEP_MM)) + 1) for sign in (1, -1)]
        found = None
        for staggered in (True, False):  # off the other layers' traces where it can be
            for at in spots:
                if any(abs(at - c) < 1.0 for c in chosen):
                    continue
                if staggered and any(abs(at - c) < (width + w) / 2 + STACK_GAP_MM for c, w in others):
                    continue
                a, b = ((at, across[0]), (at, across[1])) if along_x else ((across[0], at), (across[1], at))
                if clear(a, b):
                    found = (at, a, b)
                    break
            if found:
                break
        if found:
            chosen.append(found[0])
            out.append(_straight(found[1], found[2]))
    return out


def _straight(a: Point, b: Point) -> list[Row]:
    return [(a[0], a[1], 0.0), (b[0], b[1], 0.0)]


def touches_polygon(zone: list[Point], net: str, layer: int, copper: TP.Copper) -> bool:
    """Whether a pad, trace or via of the net on the layer reaches into a zone given as its polygon."""
    if any(n == net and lay == layer and (any(inside(p, zone) for p in poly) or any(inside(p, poly) for p in zone))
           for lay, n, poly in copper.pads):
        return True
    if any(n == net and lay == layer and any(segment_polygon(a, b, zone) < w / 2 for a, b in zip(pts, pts[1:], strict=False))
           for lay, n, w, pts in copper.traces):
        return True
    return any(n == net and (inside((x, y), zone) or min(point_segment((x, y), a, b) for a, b in zip(zone, zone[1:] + zone[:1],
                                                                                                    strict=True)) < r)
               for n, x, y, r in copper.vias)


def ties_across(zone: list[Point], net: str, layer: int, width: float, copper: TP.Copper,
                clear: Callable[[Point, Point], bool], run: Point | None = None) -> list[list[Row]] | None:
    """The ties across a zone that lies at a slant, a convex polygon: each running ``run``, the way
    traces cross the zone (along the sides it shares with the board's edges), or else square to its
    long sides, at a quarter, the half and three quarters of its width, moved to the nearest spot that
    clears; None where the zone touches the net on its layer already."""
    xs, ys = [p[0] for p in zone], [p[1] for p in zone]
    if touches_polygon(zone, net, layer, copper):
        return None
    cx, cy = sum(xs) / len(zone), sum(ys) / len(zone)
    if run is not None:
        length = math.hypot(*run) or 1.0
        n = (run[0] / length, run[1] / length)
        u = (n[1], -n[0])
    else:
        sxx = sum((x - cx) ** 2 for x in xs)
        syy = sum((y - cy) ** 2 for y in ys)
        sxy = sum((x - cx) * (y - cy) for x, y in zone)
        angle = 0.5 * math.atan2(2 * sxy, sxx - syy)
        u, n = (math.cos(angle), math.sin(angle)), (-math.sin(angle), math.cos(angle))
    along = [(x - cx) * u[0] + (y - cy) * u[1] for x, y in zone]
    across = [(x - cx) * n[0] + (y - cy) * n[1] for x, y in zone]
    low, high = min(along), max(along)
    near, far = min(across) - EXTEND_MM, max(across) + EXTEND_MM
    reach = (high - low) / 8

    def at(t: float, v: float) -> Point:
        return cx + u[0] * t + n[0] * v, cy + u[1] * t + n[1] * v

    chosen: list[float] = []
    out: list[tuple[Point, Point]] = []
    for share in SHARES:
        target = low + share * (high - low)
        spots = [target] + [target + sign * k * STEP_MM for k in range(1, int(math.floor(reach / STEP_MM)) + 1) for sign in (1, -1)]
        for t in spots:
            if any(abs(t - c) < 1.0 for c in chosen):
                continue
            a, b = at(t, near), at(t, far)
            if clear(a, b):
                chosen.append(t)
                out.append(_straight(a, b))
                break
    return out


def ties_turning(zone: list[Point], sides: tuple[tuple[Point, Point], tuple[Point, Point]], net: str, layer: int, width: float,
                 copper: TP.Copper, clear: Callable[[Point, Point], bool]) -> list[list[Row]] | None:
    """The ties across a zone whose two crossed ``sides``, taken in the zone's order, lie at an angle
    to each other: each leaves the first square to it, turns in one arc and leaves the second square
    to it, at a quarter, the half and three quarters of the way along them, moved to the nearest spot
    that clears; None where the zone touches the net on its layer already."""
    if touches_polygon(zone, net, layer, copper):
        return None
    (a1, b1), (a2, b2) = sides if _signed_area(zone) > 0 else ((sides[1][1], sides[1][0]), (sides[0][1], sides[0][0]))
    # counter-clockwise, the sides run opposite ways around the zone: a share of the first matches one minus that share of the second
    d1, d2 = _inward(a1, b1), _inward(a2, b2)
    d2 = (-d2[0], -d2[1])  # leaving through the second side
    shortest = min(math.dist(a1, b1), math.dist(a2, b2))
    reach = 1 / 8
    step = STEP_MM / shortest
    chosen: list[float] = []
    out: list[list[Row]] = []
    for share in SHARES:
        spots = [share] + [share + sign * k * step for k in range(1, int(math.floor(reach / step)) + 1) for sign in (1, -1)]
        for f in spots:
            if any(abs(f - c) * shortest < 1.0 for c in chosen):
                continue
            tie = _turned((a1[0] + f * (b1[0] - a1[0]), a1[1] + f * (b1[1] - a1[1])), d1,
                          (a2[0] + (1 - f) * (b2[0] - a2[0]), a2[1] + (1 - f) * (b2[1] - a2[1])), d2)
            if tie is None:
                continue
            path = TP.sampled(*tie)
            if all(clear(p, q) for p, q in zip(path, path[1:], strict=False)):
                chosen.append(f)
                out.append(TP.drawn(*tie))
                break
    return out


def _signed_area(polygon: list[Point]) -> float:
    return sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(polygon, polygon[1:] + polygon[:1], strict=True)) / 2


def _inward(a: Point, b: Point) -> Point:
    """The unit normal of side a-b into a counter-clockwise zone."""
    length = math.dist(a, b) or 1.0
    return -(b[1] - a[1]) / length, (b[0] - a[0]) / length


def _turned(start: Point, enter: Point, end: Point, leave: Point) -> tuple[list[Point], list[float]] | None:
    """A tie entering at ``start`` along ``enter`` and leaving at ``end`` along ``leave``, each reaching
    ``EXTEND_MM`` past its side: its corners and radii, the turn rounded with half the shorter
    straight run as its tangent, or None where the two runs do not meet ahead of both ends."""
    det = enter[0] * leave[1] - enter[1] * leave[0]
    if abs(det) < 1e-9:
        return None
    w = (end[0] - start[0], end[1] - start[1])
    t1 = (w[0] * leave[1] - w[1] * leave[0]) / det
    t2 = (enter[0] * w[1] - enter[1] * w[0]) / det
    if t1 <= 0 or t2 <= 0:
        return None
    corner = (start[0] + t1 * enter[0], start[1] + t1 * enter[1])
    turn = math.acos(max(-1.0, min(1.0, enter[0] * leave[0] + enter[1] * leave[1])))
    radius = min(t1, t2) / 2 / math.tan(turn / 2)
    if radius < BM.BEND_ARC_MM:
        return None
    return ([(start[0] - EXTEND_MM * enter[0], start[1] - EXTEND_MM * enter[1]), corner,
             (end[0] + EXTEND_MM * leave[0], end[1] + EXTEND_MM * leave[1])], [0.0, radius, 0.0])
