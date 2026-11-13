"""Ties that give a hatched band over a bend zone its copper.

Layout makes a plane shape's copper only where a pad, trace or via of its net on its layer touches
the shape. A band hatched over a bend zone on an inner ground layer touches none: no via may stand
in a bend zone and nothing else runs there, so the band stays empty while the solid shapes beside
it are poured. A tie is a straight trace of the net across the band, square to it as bend zones
want, reaching into the solid copper on both sides: it gives the band something to touch and joins
it to its net.

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
         clear: Callable[[Point, Point], bool]) -> list[tuple[Point, Point]] | None:
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
            out.append((found[1], found[2]))
    return out


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
                clear: Callable[[Point, Point], bool], run: Point | None = None) -> list[tuple[Point, Point]] | None:
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
                out.append((a, b))
                break
    return out
