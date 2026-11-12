"""The ears of a board outline: small parts that stick out of a straight edge, such as the tabs of a
flex board that hold a screw slot or meet a grounding spring.

An ear leaves an edge and comes back to it: the edge runs on in line on both sides of it, its base
(where it leaves the edge to where it comes back) is at most ``EAR_BASE_MM`` long, and it sticks out
no further than its base is long. A corner of the board, the widened end of a flex tail or a long
arm is no ear: the edge does not run on in line past it, or it sticks out further than it is wide.

The outline comes as the closed ring of corners of ``lanes.outline_corners``.
"""
from __future__ import annotations

import math
from typing import Any

from . import trace_plan as TP

Point = tuple[float, float]
Corner = tuple[Point, float]
EAR_BASE_MM = 10.0
EAR_ALIGN = math.cos(math.radians(10))  # the edge runs on in line within this angle on both sides
OUTSIDE_MM = 1e-3  # a point this far past the base line, away from the board, sticks out


def _unit(a: Point, b: Point) -> Point:
    length = math.dist(a, b)
    return ((b[0] - a[0]) / length, (b[1] - a[1]) / length) if length > 0 else (0.0, 0.0)


def _cross(u: Point, v: Point) -> float:
    return u[0] * v[1] - u[1] * v[0]


def ears(ring: list[Corner]) -> list[dict[str, Any]]:
    """Each ear of the outline, in order round it: its base's two ends, how far it sticks out, its
    extents and its shape (the outline's corners from one end of the base to the
    other as ``points_mm``: a rounded corner's point lies just outside its arc, off the board)."""
    points = [corner[0] for corner in ring]
    count = len(points)
    if count < 5:
        return []
    area = sum(_cross(points[i], points[(i + 1) % count]) for i in range(count)) / 2
    side = 1.0 if area > 0 else -1.0  # the board lies where the cross product has this sign
    found: list[tuple[int, int]] = []
    for i in range(count):
        a = points[i]
        before = _unit(points[i - 1], a)
        reach = None
        for k in range(2, count - 1):
            j = (i + k) % count
            b = points[j]
            span = math.dist(a, b)
            if not 1e-9 < span <= EAR_BASE_MM:
                continue
            base = _unit(a, b)
            after = _unit(b, points[(j + 1) % count])
            if base[0] * before[0] + base[1] * before[1] < EAR_ALIGN or base[0] * after[0] + base[1] * after[1] < EAR_ALIGN:
                continue
            sides = [side * _cross(base, (points[(i + m) % count][0] - a[0], points[(i + m) % count][1] - a[1])) for m in range(1, k)]
            if sides and all(s < -OUTSIDE_MM for s in sides) and -min(sides) <= span:  # every corner past the base: two tabs side by side stay two
                reach = k
        if reach is not None:
            found.append((i, reach))
    kept = [(i, k) for i, k in found
            if not any((i - i2) % count + k <= k2 and (i, k) != (i2, k2) for i2, k2 in found)]  # inside a larger ear
    out = []
    for i, k in kept:
        corners = [ring[(i + m) % count] for m in range(k + 1)]
        a, b = corners[0][0], corners[-1][0]
        base = _unit(a, b)
        shape = TP.sampled([c[0] for c in corners], [0.0] + [c[1] for c in corners[1:-1]] + [0.0])
        xs, ys = [p[0] for p in shape], [p[1] for p in shape]
        depth = max(-side * _cross(base, (p[0] - a[0], p[1] - a[1])) for p in shape)
        out.append({"base_mm": [[round(a[0], 4), round(a[1], 4)], [round(b[0], 4), round(b[1], 4)]],
                    "depth_mm": round(depth, 3),
                    "extents_mm": [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)],
                    "points_mm": [[round(c[0][0], 3), round(c[0][1], 3)] for c in corners]})
    return out
