"""Where a part's pads lie on marks drawn on the board: a structure drawing carries a copy of a
connector's land pattern, and imported into Layout as placement keepouts its marks give the part's
place. A fit turns and shifts the part, never mirrors it, so that pads lie on marks of the same
area.

Pure geometry on plain data, in mm: a pad or a mark is its centroid and area.
"""
from __future__ import annotations

import math
from typing import Any

Shape = tuple[float, float, float]  # centroid x, centroid y, area

AREA_TOLERANCE = 0.03  # share of the area two shapes may differ by and still be the same shape
AREA_FLOOR_MM2 = 0.002  # and this much, for tiny pads
POSITION_TOLERANCE_MM = 0.01  # a pad lies on a mark when their centroids are this close
MIN_SPAN_MM = 0.3  # two pads closer than this give too short a lever to find a turn from
MIN_PADS = 3  # a fit puts at least this many pads on marks


def path_shape(path: list[list[float]]) -> Shape | None:
    """The centroid and area of a closed path of (x, y, r) rows; its arcs are taken as straight."""
    points = [(p[0], p[1]) for p in path]
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        return None
    twice, cx, cy = 0.0, 0.0, 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1], strict=True):
        cross = x0 * y1 - x1 * y0
        twice += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(twice) < 1e-12:
        return None
    return cx / (3 * twice), cy / (3 * twice), abs(twice) / 2


def circle_shape(x: float, y: float, r: float) -> Shape:
    return x, y, math.pi * r * r


def same_area(a: float, b: float) -> bool:
    return abs(a - b) <= max(AREA_TOLERANCE * max(a, b), AREA_FLOOR_MM2)


def _turn(x: float, y: float, cos: float, sin: float) -> tuple[float, float]:
    return x * cos - y * sin, x * sin + y * cos


def _score(pads: list[tuple[str, Shape]], marks: list[Shape], cos: float, sin: float, dx: float,
           dy: float) -> list[tuple[str, Shape, Shape]]:
    """Each pad the placement puts on a mark of the same area, with that mark."""
    matched = []
    for pin, pad in pads:
        tx, ty = _turn(pad[0], pad[1], cos, sin)
        tx, ty = tx + dx, ty + dy
        near = [(math.hypot(m[0] - tx, m[1] - ty), m) for m in marks if same_area(pad[2], m[2])]
        if near:
            distance, mark = min(near)
            if distance <= POSITION_TOLERANCE_MM:
                matched.append((pin, pad, mark))
    return matched


def _off(pairs: list[tuple[str, Shape, Shape]], cos: float, sin: float, dx: float, dy: float) -> float:
    """How far the pads lie from their marks under the placement, on average."""
    total = 0.0
    for _pin, pad, mark in pairs:
        x, y = _turn(pad[0], pad[1], cos, sin)
        total += math.hypot(x + dx - mark[0], y + dy - mark[1])
    return total / len(pairs)


def _refine(pairs: list[tuple[str, Shape, Shape]]) -> tuple[float, float, float]:
    """The turn (radians) and shift that put the pads on their marks best, in the least squares."""
    n = len(pairs)
    px, py = sum(p[0] for _n, p, _m in pairs) / n, sum(p[1] for _n, p, _m in pairs) / n
    qx, qy = sum(m[0] for _n, _p, m in pairs) / n, sum(m[1] for _n, _p, m in pairs) / n
    cross = sum((p[0] - px) * (m[1] - qy) - (p[1] - py) * (m[0] - qx) for _n, p, m in pairs)
    dot = sum((p[0] - px) * (m[0] - qx) + (p[1] - py) * (m[1] - qy) for _n, p, m in pairs)
    theta = math.atan2(cross, dot)
    rx, ry = _turn(px, py, math.cos(theta), math.sin(theta))
    return theta, qx - rx, qy - ry


def fits(pads: list[tuple[str, Shape]], marks: list[Shape]) -> list[dict[str, Any]]:
    """Each placement that puts at least MIN_PADS pads on marks of the same area: the turn in degrees
    (counter-clockwise) and the shift that move the pads from where they stand, the pins matched and
    their mean distance from the marks. Most pins matched first.

    Two pads of one area and two marks of that area the same distance apart fix a turn and a shift;
    each such pairing is tried, from the pads of the rarest area on, and kept when it puts enough
    pads on marks."""
    classes: list[list[tuple[str, Shape]]] = []
    for pad in pads:
        for group in classes:
            if same_area(group[0][1][2], pad[1][2]):
                group.append(pad)
                break
        else:
            classes.append([pad])
    classes.sort(key=len)
    found: dict[tuple[float, float, float], dict[str, Any]] = {}
    tried: set[tuple[float, float, float]] = set()
    for group in classes:
        if len(group) < 2:
            continue
        candidates = [m for m in marks if same_area(group[0][1][2], m[2])]
        if len(candidates) < 2:
            continue
        for i, (_pa, (ax, ay, _)) in enumerate(group):
            for _pb, (bx, by, _) in group[i + 1:]:
                span = math.hypot(bx - ax, by - ay)
                if span < MIN_SPAN_MM:
                    continue
                for m1 in candidates:
                    for m2 in candidates:
                        if m1 is m2 or abs(math.hypot(m2[0] - m1[0], m2[1] - m1[1]) - span) > POSITION_TOLERANCE_MM:
                            continue
                        theta = math.atan2(m2[1] - m1[1], m2[0] - m1[0]) - math.atan2(by - ay, bx - ax)
                        cos, sin = math.cos(theta), math.sin(theta)
                        rx, ry = _turn(ax, ay, cos, sin)
                        dx, dy = m1[0] - rx, m1[1] - ry
                        key = (round(math.degrees(theta) % 360.0, 1) % 360.0, round(dx, 2), round(dy, 2))
                        if key in tried:
                            continue
                        tried.add(key)
                        pairs = _score(pads, marks, cos, sin, dx, dy)
                        if len(pairs) < MIN_PADS:
                            continue
                        theta, dx, dy = _refine(pairs)
                        cos, sin = math.cos(theta), math.sin(theta)
                        pairs = _score(pads, marks, cos, sin, dx, dy)
                        turn = round(math.degrees(theta), 4) % 360.0
                        final = (round(turn, 2) % 360.0, round(dx, 3), round(dy, 3))
                        if len(pairs) >= MIN_PADS and final not in found:
                            found[final] = {"turn_deg": turn, "dx": dx, "dy": dy, "matched": [n for n, _p, _m in pairs],
                                            "residual_mm": _off(pairs, cos, sin, dx, dy)}
    return sorted(found.values(), key=lambda f: (-len(f["matched"]), f["residual_mm"]))


def placement(fit: dict[str, Any], x: float, y: float, rotation: float) -> tuple[float, float, float]:
    """Where the part's origin and rotation go under a fit, from where they stand."""
    theta = math.radians(fit["turn_deg"])
    tx, ty = _turn(x, y, math.cos(theta), math.sin(theta))
    return round(tx + fit["dx"], 4), round(ty + fit["dy"], 4), round(round(rotation + fit["turn_deg"], 4) % 360.0, 4)


def choose(fits_found: list[dict[str, Any]], x: float, y: float, rotation: float,
           wanted_rotation: float | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The fit to take and the others: among those that match the most pins (at ``wanted_rotation``
    when it is given), the one nearest where the part stands, then the one that turns it least."""
    rows = []
    for fit in fits_found:
        nx, ny, nrot = placement(fit, x, y, rotation)
        if wanted_rotation is not None and min(abs(nrot - wanted_rotation) % 360.0, 360.0 - abs(nrot - wanted_rotation) % 360.0) > 0.05:
            continue
        turn = min(fit["turn_deg"], 360.0 - fit["turn_deg"])
        rows.append({**fit, "x_mm": nx, "y_mm": ny, "rotation_deg": nrot, "distance_mm": math.hypot(nx - x, ny - y), "turn": turn})
    if not rows:
        return None, []
    most = max(len(r["matched"]) for r in rows)
    best = min((r for r in rows if len(r["matched"]) == most), key=lambda r: (round(r["distance_mm"], 1), round(r["turn"], 1)))
    return best, [r for r in rows if r is not best and len(r["matched"]) == most]
