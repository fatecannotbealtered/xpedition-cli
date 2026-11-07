"""Where via create puts stitching vias: a grid inside the board, kept clear of other copper.

A stitching via ties a net's copper on several layers together, so a grid point is kept only
where the net has a plane shape on at least two layers. It must keep the rules' clearances: to
every pad (Layout refuses a via close to a pad of its own net too), to the traces of other nets,
to every via and hole, ``CUTOUT_CLEARANCE_MM`` to every cutout (an internal contour, such as a
slot for a screw), and the margin to the board outline. The adapter reads the board into the
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


@dataclass
class Board:
    outline: list[Point]
    pads: list[list[Point]] = field(default_factory=list)  # every pad as a polygon (a circle as its bounding polygon)
    traces: list[tuple[list[Point], float, str]] = field(default_factory=list)  # (points, width, net)
    vias: list[Point] = field(default_factory=list)
    holes: list[tuple[float, float, float]] = field(default_factory=list)  # (x, y, diameter)
    planes: dict[int, list[list[Point]]] = field(default_factory=dict)  # the net's plane shapes by layer
    cutouts: list[list[Point]] = field(default_factory=list)  # internal contours: slots and cutouts


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


def grid(outline: list[Point], pitch: float, margin: float) -> list[Point]:
    """Grid points ``pitch`` apart inside the outline, at least ``margin`` from its edges, the grid
    centred on the outline's extents."""
    if pitch <= 0:
        raise PlanError("E_VALIDATION", "--pitch is a length of more than 0.")
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    nx, ny = int((x1 - x0 - 2 * margin) // pitch) + 1, int((y1 - y0 - 2 * margin) // pitch) + 1
    if nx < 1 or ny < 1:
        return []
    if nx * ny > MAX_POINTS * 4:
        raise PlanError("E_VALIDATION", f"A {pitch:g} mm pitch makes about {nx * ny} grid points; use a pitch that gives at most "
                        f"{MAX_POINTS} vias.")
    ox = x0 + (x1 - x0 - (nx - 1) * pitch) / 2
    oy = y0 + (y1 - y0 - (ny - 1) * pitch) / 2
    points = [(round(ox + i * pitch, 4), round(oy + j * pitch, 4)) for j in range(ny) for i in range(nx)]
    return [p for p in points if inside(p, outline) and edge_distance(p, outline) >= margin - 1e-9]


def free(point: Point, net: str, radius: float, board: Board, clear: Clearances) -> str | None:
    """Why a via of ``radius`` cannot stand at ``point``, or None when it can."""
    for pad in board.pads:
        if distance_to_polygon(point, pad) < radius + clear.via_to_pad - 1e-9:
            return "too close to a pad"
    for points, width, owner in board.traces:
        if owner == net:
            continue
        reach = radius + width / 2 + clear.via_to_trace
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


def stitch(net: str, radius: float, board: Board, clear: Clearances, pitch: float, margin: float) -> Plan:
    """The grid points where a stitching via of ``net`` can stand, and how many were dropped why."""
    layers_with_planes = [layer for layer, shapes in board.planes.items() if shapes]
    if len(layers_with_planes) < 2:
        raise PlanError("E_CONFLICT", f"{net} has plane shapes on {len(layers_with_planes)} layer(s); stitching vias tie a net's "
                        "copper on at least two layers together, so pour it with plane create on two layers first.")
    kept: list[Point] = []
    dropped: dict[str, int] = {}
    for point in grid(board.outline, pitch, margin + radius):
        covered = sum(1 for layer in layers_with_planes
                      if any(len(shape) >= 3 and inside(point, shape) for shape in board.planes[layer]))
        reason = "outside the net's copper on two layers" if covered < 2 else free(point, net, radius, board, clear)
        if reason is None:
            for x, y in kept:
                if math.hypot(point[0] - x, point[1] - y) < 2 * radius + clear.via_to_via - 1e-9:
                    reason = "too close to a via"
                    break
        if reason is None:
            kept.append(point)
            if len(kept) > MAX_POINTS:
                raise PlanError("E_VALIDATION", f"More than {MAX_POINTS} vias would stand on this grid; use a larger --pitch.")
        else:
            dropped[reason] = dropped.get(reason, 0) + 1
    return Plan(kept, dropped)
