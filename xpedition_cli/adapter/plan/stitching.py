"""Where via create puts stitching vias: a grid inside the board, kept clear of other copper.

A stitching via ties a net's copper on several layers together, so a via stands only where the
net has a plane shape on at least two layers. A grid point without room moves to the nearest free
spot within half the pitch, so a board crossed by traces still gets a via wherever there is room. It must keep the rules' clearances: to
every pad (Layout refuses a via close to a pad of its own net too), to the traces of other nets,
to every via and hole, out of every keepout of vias on any layer, ``CUTOUT_CLEARANCE_MM`` to every cutout (an internal contour, such as a
slot for a screw), and the margin to the board outline. On a flex board no via stands in a bend
zone, where bending would crack it. The adapter reads the board into the
shapes below, in mm; this module only does the geometry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import PlanError
from .shapes import flatten, inside, point_segment

Point = tuple[float, float]
MAX_POINTS = 2000  # a grid this dense is a pitch mistake
# Layout's DRC asked a via to keep 0.254 mm from an internal contour on the flex board measured;
# the setting it comes from cannot be read through automation, so the plan keeps that distance.
CUTOUT_CLEARANCE_MM = 0.254
SEARCH_STEP_MM = 0.2  # a grid point without room looks for a free spot on rings this far apart


@dataclass
class Board:
    outline: list[Point]
    pads: list[list[Point]] = field(default_factory=list)  # every pad as a polygon (a circle as its bounding polygon)
    traces: list[tuple[list[Point], float, str]] = field(default_factory=list)  # (points, width, net)
    vias: list[Point] = field(default_factory=list)
    holes: list[tuple[float, float, float]] = field(default_factory=list)  # (x, y, diameter)
    planes: dict[int, list[list[Point]]] = field(default_factory=dict)  # the net's plane shapes by layer
    cutouts: list[list[Point]] = field(default_factory=list)  # internal contours: slots and cutouts
    bends: list[tuple[float, float, float, float]] = field(default_factory=list)  # a flex board's bend zones, which take no vias
    keepouts: list[list[Point]] = field(default_factory=list)  # keepouts of vias on any layer, which a via passes through
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)  # each trace's extents, filled by ``free``


@dataclass
class Clearances:
    via_to_pad: float
    via_to_trace: float
    via_to_via: float


@dataclass
class Plan:
    points: list[Point]
    dropped: dict[str, int]


def polygon(path: list[list[float]] | None = None, circle: list[float] | None = None) -> list[Point]:
    """A geometry record's outline as a polygon: its path flattened, or a circle's octagon around it."""
    if circle is not None:
        x, y, r = circle
        r = r / math.cos(math.pi / 8)  # the octagon contains the circle
        return [(x + r * math.cos(k * math.pi / 4), y + r * math.sin(k * math.pi / 4)) for k in range(8)]
    return flatten([(p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in path or []])


def edge_distance(point: Point, poly: list[Point]) -> float:
    """The distance from a point to the nearest edge of a polygon, inside or out."""
    return min(point_segment(point, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def distance_to_polygon(point: Point, poly: list[Point]) -> float:
    """0 inside the polygon, else the distance to its nearest edge."""
    if len(poly) >= 3 and inside(point, poly):
        return 0.0
    return edge_distance(point, poly)


def grid(outline: list[Point], pitch: float, margin: float, region: tuple[float | None, ...] | None = None) -> list[Point]:
    """Grid points ``pitch`` apart inside the outline, at least ``margin`` from its edges, the grid
    centred on the outline's extents; with ``region`` (x0, y0, x1, y1, a side None for open), only
    the points inside it, so a small region on a large board stays a small grid."""
    if pitch <= 0:
        raise PlanError("E_VALIDATION", "--pitch is a length of more than 0.")
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    nx, ny = int((x1 - x0 - 2 * margin) // pitch) + 1, int((y1 - y0 - 2 * margin) // pitch) + 1
    if nx < 1 or ny < 1:
        return []
    ox = x0 + (x1 - x0 - (nx - 1) * pitch) / 2
    oy = y0 + (y1 - y0 - (ny - 1) * pitch) / 2
    rx0, ry0, rx1, ry1 = region if region is not None else (None, None, None, None)
    i0 = 0 if rx0 is None else max(0, math.ceil((rx0 - ox) / pitch - 1e-9))
    i1 = nx - 1 if rx1 is None else min(nx - 1, math.floor((rx1 - ox) / pitch + 1e-9))
    j0 = 0 if ry0 is None else max(0, math.ceil((ry0 - oy) / pitch - 1e-9))
    j1 = ny - 1 if ry1 is None else min(ny - 1, math.floor((ry1 - oy) / pitch + 1e-9))
    if i1 < i0 or j1 < j0:
        return []
    if (i1 - i0 + 1) * (j1 - j0 + 1) > MAX_POINTS * 4:
        raise PlanError("E_VALIDATION", f"A {pitch:g} mm pitch makes about {(i1 - i0 + 1) * (j1 - j0 + 1)} grid points; use a pitch "
                        f"that gives at most {MAX_POINTS} vias.")
    points = [(round(ox + i * pitch, 4), round(oy + j * pitch, 4)) for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]
    return [p for p in points if inside(p, outline) and edge_distance(p, outline) >= margin - 1e-9]


def in_bend(point: Point, radius: float, bends: list[tuple[float, float, float, float]]) -> bool:
    """Whether a via of ``radius`` at ``point`` reaches into a bend zone."""
    return any(x0 - radius < point[0] < x1 + radius and y0 - radius < point[1] < y1 + radius for x0, y0, x1, y1 in bends)


def free(point: Point, net: str, radius: float, board: Board, clear: Clearances) -> str | None:
    """Why a via of ``radius`` cannot stand at ``point``, or None when it can."""
    if in_bend(point, radius, board.bends):
        return "in a bend zone"
    if any(distance_to_polygon(point, keepout) < radius - 1e-9 for keepout in board.keepouts if len(keepout) >= 3):
        return "in a via keepout"
    for pad in board.pads:
        if distance_to_polygon(point, pad) < radius + clear.via_to_pad - 1e-9:
            return "too close to a pad"
    if len(board.boxes) != len(board.traces):
        board.boxes = [(min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
                       for pts, _width, _owner in board.traces]
    for (points, width, owner), (x0, y0, x1, y1) in zip(board.traces, board.boxes, strict=True):
        if owner == net:
            continue
        reach = radius + width / 2 + clear.via_to_trace
        if point[0] < x0 - reach or point[0] > x1 + reach or point[1] < y0 - reach or point[1] > y1 + reach:
            continue
        if any(point_segment(point, points[i], points[i + 1]) < reach - 1e-9 for i in range(len(points) - 1)):
            return "too close to a trace of another net"
    for x, y in board.vias:
        if math.hypot(point[0] - x, point[1] - y) < 2 * radius + clear.via_to_via - 1e-9:
            return "too close to a via"
    for x, y, diameter in board.holes:
        if math.hypot(point[0] - x, point[1] - y) < radius + diameter / 2 + clear.via_to_via - 1e-9:
            return "too close to a hole"
    for cutout in board.cutouts:
        if distance_to_polygon(point, cutout) < radius + CUTOUT_CLEARANCE_MM - 1e-9:
            return "too close to a cutout"
    return None


def _around(point: Point, reach: float) -> list[Point]:
    """``point`` first, then spots on rings round it ``SEARCH_STEP_MM`` apart, out to ``reach``, nearest first."""
    spots = [point]
    for ring in range(1, int(reach / SEARCH_STEP_MM + 1e-9) + 1):
        radius, count = ring * SEARCH_STEP_MM, 8 * ring
        spots += [(point[0] + radius * math.cos(2 * math.pi * k / count), point[1] + radius * math.sin(2 * math.pi * k / count))
                  for k in range(count)]
    return spots


def stitch(net: str, radius: float, board: Board, clear: Clearances, pitch: float, margin: float,
           region: tuple[float | None, ...] | None = None) -> Plan:
    """Where a stitching via of ``net`` can stand, one for each grid point that has room: the grid
    point itself, or the nearest free spot within half the pitch of it, at least half the pitch from
    the vias kept already; and how many grid points were dropped why, by the reason at the point."""
    layers_with_planes = [layer for layer, shapes in board.planes.items() if shapes]
    if len(layers_with_planes) < 2:
        raise PlanError("E_CONFLICT", f"{net} has plane shapes on {len(layers_with_planes)} layer(s); stitching vias tie a net's "
                        "copper on at least two layers together, so pour it with plane create on two layers first.")
    kept: list[Point] = []
    dropped: dict[str, int] = {}
    apart = max(2 * radius + clear.via_to_via, pitch / 2)
    outline_margin = margin + radius
    for point in grid(board.outline, pitch, outline_margin, region):
        first: str | None = None
        chosen = None
        for spot in _around(point, pitch / 2):
            if spot is not point and (not inside(spot, board.outline) or edge_distance(spot, board.outline) < outline_margin - 1e-9
                                      or not _in_region(spot, region)):
                continue
            covered = sum(1 for layer in layers_with_planes
                          if any(len(shape) >= 3 and inside(spot, shape) for shape in board.planes[layer]))
            reason = "outside the net's copper on two layers" if covered < 2 else free(spot, net, radius, board, clear)
            if reason is None and any(math.hypot(spot[0] - x, spot[1] - y) < apart - 1e-9 for x, y in kept):
                reason = "too close to a via"
            if reason is None:
                chosen = spot
                break
            first = first or reason
        if chosen is not None:
            kept.append((round(chosen[0], 4), round(chosen[1], 4)))
            if len(kept) > MAX_POINTS:
                raise PlanError("E_VALIDATION", f"More than {MAX_POINTS} vias would stand on this grid; use a larger --pitch.")
        else:
            dropped[first or "no room"] = dropped.get(first or "no room", 0) + 1
    return Plan(kept, dropped)


def _in_region(point: Point, region: tuple[float | None, ...] | None) -> bool:
    if region is None:
        return True
    x0, y0, x1, y1 = region
    return ((x0 is None or point[0] >= x0) and (x1 is None or point[0] <= x1) and (y0 is None or point[1] >= y0)
            and (y1 is None or point[1] <= y1))
