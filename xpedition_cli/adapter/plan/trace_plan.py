"""The routing plan of trace create, and the offline check it passes before Layout is touched.

A plan names traces and vias by net, in mm:

```json
{"traces": [{"net": "USB_TX_P", "layer": 1, "width_mm": 0.1, "points_mm": [[5.0, 9.0], [45.0, 9.0, 2.0], [45.0, 30.0]]}],
 "vias": [{"net": "GND", "at_mm": [45.0, 3.0], "padstack": "VIA_D0.2_P0.4"}]}
```

A point between the ends may carry a third value, the radius in mm of an arc that rounds the
corner there; the arc meets both segments at a tangent.

Coordinates are the board's, in mm, the ones pcb get --output writes, so an Agent reads the pads
there, plans and draws. The check refuses, with every reason: a net or layer the board lacks; a
width outside its net class's minimum and maximum on that layer; a trace end that touches no copper
of its own net on its layer (a pad, a trace, a via, or one of the plan's own); and a trace or via
closer to another net's pad, trace or via than the clearance rule for the two nets' classes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import PlanError
from .shapes import inside, point_segment

Point = tuple[float, float]
MAX_ITEMS = 2000


@dataclass
class Trace:
    net: str
    layer: int
    width: float
    points: list[Point]
    radii: list[float] = field(default_factory=list)  # each point's corner radius in mm, 0 for a sharp corner

    def __post_init__(self) -> None:
        if not self.radii:
            self.radii = [0.0] * len(self.points)

    @property
    def path(self) -> list[Point]:
        """The trace's centre line with its arcs as short chords, for distances and lengths."""
        return sampled(self.points, self.radii)


@dataclass
class Via:
    net: str
    at: Point
    padstack: str
    radius: float = 0.0  # filled in by the adapter from the padstack


@dataclass
class Plan:
    traces: list[Trace] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)


@dataclass
class Copper:
    """The board's copper the plan meets, in mm."""

    layer_count: int
    pads: list[tuple[int, str, list[Point]]] = field(default_factory=list)  # (layer, net, polygon)
    traces: list[tuple[int, str, float, list[Point]]] = field(default_factory=list)  # (layer, net, width, points)
    vias: list[tuple[str, float, float, float]] = field(default_factory=list)  # (net, x, y, radius); vias go through every layer
    nets: set[str] = field(default_factory=set)


@dataclass
class Rules:
    """Widths per net class and layer, and the clearance between two nets on a layer."""

    net_class_of: dict[str, str]
    widths: dict[str, dict[int, tuple[float, float]]]  # class -> layer -> (min, max)
    clearance: Any  # callable(net_a, net_b, layer, kind) -> mm; kind is trace_to_pad, trace_to_trace, trace_to_via, ...


def _error(message: str) -> PlanError:
    return PlanError("E_VALIDATION", f"The routing plan is not valid: {message}.")


def _point(value: Any, what: str) -> Point:
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)):
        raise _error(f"{what} is a point [x, y] in mm")
    return round(float(value[0]), 4), round(float(value[1]), 4)


def _corner(value: Any, what: str) -> tuple[Point, float]:
    """A trace point [x, y] or [x, y, corner radius]."""
    if isinstance(value, list) and len(value) == 3:
        radius = value[2]
        if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius < 0:
            raise _error(f"{what} has a corner radius of 0 or more mm as its third value")
        return _point(value[:2], what), round(float(radius), 4)
    if isinstance(value, list) and len(value) == 2:
        return _point(value, what), 0.0
    raise _error(f"{what} is a point [x, y] in mm, or [x, y, corner radius] between the ends")


ARC_STEP = math.radians(5)  # the chord step of an arc in the check
MIN_ARC_MM = 1e-3  # a rounded corner whose arc would start closer than this to the corner stays sharp
CLEARANCE_TOLERANCE = 1e-3  # mm a distance may fall short of a rule: arcs are checked as chords, which bring two concentric
# arcs a thousandth of their distance closer than the copper is


def _corners(points: list[Point], radii: list[float]) -> list[tuple[Point, Point, Point, float, bool] | None]:
    """For each point, the arc that rounds its corner: (start, centre, end, radius, counter-clockwise),
    or None for a sharp corner, an end or a straight run; a radius that does not fit its segments is refused."""
    arcs: list[tuple[Point, Point, Point, float, bool] | None] = [None] * len(points)
    cut = [0.0] * len(points)
    for i in range(1, len(points) - 1):
        r = radii[i]
        if r <= 0:
            continue
        a, p, b = points[i - 1], points[i], points[i + 1]
        la, lb = math.dist(a, p), math.dist(b, p)
        if la == 0 or lb == 0:
            raise _error(f"point {i + 1} repeats its neighbour, so its corner cannot be rounded")
        u1 = ((a[0] - p[0]) / la, (a[1] - p[1]) / la)
        u2 = ((b[0] - p[0]) / lb, (b[1] - p[1]) / lb)
        theta = math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))
        if theta > math.pi - 1e-6:
            continue  # a straight run has no corner to round
        if theta < 1e-6:
            raise _error(f"the trace turns back on itself at point {i + 1}")
        tangent = r / math.tan(theta / 2)
        if tangent < MIN_ARC_MM:
            continue  # a corner that turns by a few microradians: no arc Layout could hold
        cut[i] = tangent
        bisector = (u1[0] + u2[0], u1[1] + u2[1])
        norm = math.hypot(*bisector)
        centre = (p[0] + bisector[0] / norm * r / math.sin(theta / 2), p[1] + bisector[1] / norm * r / math.sin(theta / 2))
        start, end = (p[0] + u1[0] * tangent, p[1] + u1[1] * tangent), (p[0] + u2[0] * tangent, p[1] + u2[1] * tangent)
        ccw = (p[0] - a[0]) * (b[1] - p[1]) - (p[1] - a[1]) * (b[0] - p[0]) > 0
        arcs[i] = (start, centre, end, r, ccw)
    for i in range(len(points) - 1):
        if cut[i] + cut[i + 1] > math.dist(points[i], points[i + 1]) + 1e-6:
            raise _error(f"the corner radius at point {i + 1 if cut[i] else i + 2} is too large for the segments beside it")
    return arcs


def drawn(points: list[Point], radii: list[float]) -> list[tuple[float, float, float]]:
    """The (x, y, r) rows Layout takes: a rounded corner as its arc's start, its centre with the radius
    (negative for a counter-clockwise arc) and its end."""
    rows = [(points[0][0], points[0][1], 0.0)]
    for i, arc in enumerate(_corners(points, radii)[1:-1], start=1):
        if arc is None:
            rows.append((points[i][0], points[i][1], 0.0))
            continue
        start, centre, end, r, ccw = arc
        rows += [(start[0], start[1], 0.0), (centre[0], centre[1], -r if ccw else r), (end[0], end[1], 0.0)]
    rows.append((points[-1][0], points[-1][1], 0.0))
    return rows  # unrounded: Layout joins a trace to a pin only on the pin's exact origin


def sampled(points: list[Point], radii: list[float]) -> list[Point]:
    """The centre line with each arc as chords of at most 5 degrees."""
    if not any(radii):
        return list(points)
    path = [points[0]]
    for i, arc in enumerate(_corners(points, radii)[1:-1], start=1):
        if arc is None:
            path.append(points[i])
            continue
        start, centre, end, r, ccw = arc
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
        sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
        steps = max(1, math.ceil(abs(sweep) / ARC_STEP))
        path += [(centre[0] + r * math.cos(a0 + sweep * k / steps), centre[1] + r * math.sin(a0 + sweep * k / steps))
                 for k in range(steps + 1)]
    path.append(points[-1])
    return path


def length(trace: Trace) -> float:
    """The trace's length in mm along its centre line, its arcs included."""
    path = trace.path
    return sum(math.dist(a, b) for a, b in zip(path, path[1:], strict=False))


def parse(data: Any, lanes: bool = False) -> Plan:
    """The plan's traces and vias; with ``lanes``, the plan has lanes besides, so it may name none."""
    if not isinstance(data, dict) or not (data or lanes):
        raise _error("the file holds one JSON object with traces, vias and lanes")
    unknown = [k for k in data if k not in ("traces", "vias")]
    if unknown:
        raise _error(f"unknown key {unknown[0]!r}; the keys are traces, vias and lanes")
    plan = Plan()
    for index, item in enumerate(data.get("traces", [])):
        what = f"traces[{index}]"
        if not isinstance(item, dict) or set(item) - {"net", "layer", "width_mm", "points_mm"}:
            raise _error(f"{what} has the keys net, layer, width_mm and points_mm")
        net, layer, width, points = item.get("net"), item.get("layer"), item.get("width_mm"), item.get("points_mm")
        if not isinstance(net, str) or not net:
            raise _error(f"{what}.net is a net name")
        if type(layer) is not int or layer < 1:
            raise _error(f"{what}.layer is a conductor layer number, 1 at the top")
        if isinstance(width, bool) or not isinstance(width, (int, float)) or not 0 < width <= 25:
            raise _error(f"{what}.width_mm is a width of more than 0 and at most 25 mm")
        if not isinstance(points, list) or len(points) < 2:
            raise _error(f"{what}.points_mm has at least two points")
        corners = [_corner(p, f"{what}.points_mm[{i}]") for i, p in enumerate(points)]
        if corners[0][1] or corners[-1][1]:
            raise _error(f"{what}.points_mm rounds corners only between its ends")
        trace = Trace(net, layer, round(float(width), 4), [c[0] for c in corners], [c[1] for c in corners])
        _corners(trace.points, trace.radii)  # a radius that does not fit is refused here
        plan.traces.append(trace)
    for index, item in enumerate(data.get("vias", [])):
        what = f"vias[{index}]"
        if not isinstance(item, dict) or set(item) - {"net", "at_mm", "padstack"}:
            raise _error(f"{what} has the keys net, at_mm and padstack")
        net, padstack = item.get("net"), item.get("padstack")
        if not isinstance(net, str) or not net or not isinstance(padstack, str) or not padstack:
            raise _error(f"{what} needs a net and a padstack")
        plan.vias.append(Via(net, _point(item.get("at_mm"), f"{what}.at_mm"), padstack))
    if not plan.traces and not plan.vias and not lanes:
        raise _error("it names no trace or via")
    if len(plan.traces) + len(plan.vias) > MAX_ITEMS:
        raise _error(f"it names more than {MAX_ITEMS} items")
    return plan


def _polygon_distance(point: Point, poly: list[Point]) -> float:
    if len(poly) >= 3 and inside(point, poly):
        return 0.0
    return min(point_segment(point, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def _segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    """The distance between two segments (0 when they cross)."""
    def orient(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    if (orient(a, b, c) > 0) != (orient(a, b, d) > 0) and (orient(c, d, a) > 0) != (orient(c, d, b) > 0):
        return 0.0
    return min(point_segment(a, c, d), point_segment(b, c, d), point_segment(c, a, b), point_segment(d, a, b))


def _segment_polygon(a: Point, b: Point, poly: list[Point]) -> float:
    if len(poly) >= 3 and (inside(a, poly) or inside(b, poly)):
        return 0.0
    return min(_segment_distance(a, b, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def _segments(points: list[Point]) -> list[tuple[Point, Point]]:
    return list(zip(points, points[1:], strict=False))


def _touches(end: Point, trace: Trace, copper: Copper, plan: Plan, own: int) -> bool:
    """Whether a trace end lies on copper of its own net on its layer."""
    for layer, net, poly in copper.pads:
        if net == trace.net and layer == trace.layer and _polygon_distance(end, poly) <= trace.width / 2 + 1e-6:
            return True
    for layer, net, width, points in copper.traces:
        if net == trace.net and layer == trace.layer and any(point_segment(end, a, b) <= width / 2 + 1e-6 for a, b in _segments(points)):
            return True
    for net, x, y, radius in copper.vias:
        if net == trace.net and math.hypot(end[0] - x, end[1] - y) <= radius + 1e-6:
            return True
    for index, other in enumerate(plan.traces):
        if index != own and other.net == trace.net and other.layer == trace.layer and any(
                point_segment(end, a, b) <= other.width / 2 + 1e-6 for a, b in _segments(other.path)):
            return True
    return any(via.net == trace.net and math.hypot(end[0] - via.at[0], end[1] - via.at[1]) <= via.radius + 1e-6 for via in plan.vias)


def check(plan: Plan, copper: Copper, rules: Rules) -> list[str]:
    """Every reason the plan cannot go onto the board; an empty list when it can."""
    blocked: list[str] = []
    for index, trace in enumerate(plan.traces):
        name = f"trace {index + 1} of {trace.net}"
        if trace.net not in copper.nets:
            blocked.append(f"{name}: the board has no net {trace.net}")
            continue
        if not 1 <= trace.layer <= copper.layer_count:
            blocked.append(f"{name}: the board has conductor layers 1 to {copper.layer_count}; there is no layer {trace.layer}")
            continue
        cls = rules.net_class_of.get(trace.net, "(Default)")
        low, high = rules.widths.get(cls, {}).get(trace.layer, (0.0, math.inf))
        if not low - 1e-6 <= trace.width <= high + 1e-6:
            blocked.append(f"{name}: width {trace.width:g} mm is outside its class {cls}'s {low:g} to {high:g} mm on layer {trace.layer}")
        for end in (trace.points[0], trace.points[-1]):
            if not _touches(end, trace, copper, plan, index):
                blocked.append(f"{name}: its end at ({end[0]:g}, {end[1]:g}) mm touches no copper of {trace.net} on layer {trace.layer}")
        path = trace.path
        for a, b in _segments(path):
            for layer, net, poly in copper.pads:
                if layer == trace.layer and net != trace.net:
                    need = rules.clearance(trace.net, net, layer, "trace_to_pad") + trace.width / 2
                    if _segment_polygon(a, b, poly) < need - CLEARANCE_TOLERANCE:
                        blocked.append(f"{name}: closer to a pad of {net or 'no net'} than {need - trace.width / 2:g} mm on layer {layer}")
                        break
            for layer, net, width, points in copper.traces:
                if layer == trace.layer and net != trace.net:
                    need = rules.clearance(trace.net, net, layer, "trace_to_trace") + (trace.width + width) / 2
                    if any(_segment_distance(a, b, c, d) < need - CLEARANCE_TOLERANCE for c, d in _segments(points)):
                        blocked.append(f"{name}: closer to a trace of {net} than the clearance on layer {layer}")
                        break
            for net, x, y, radius in copper.vias:
                if net != trace.net:
                    need = rules.clearance(trace.net, net, trace.layer, "trace_to_via") + trace.width / 2 + radius
                    if point_segment((x, y), a, b) < need - CLEARANCE_TOLERANCE:
                        blocked.append(f"{name}: closer to a via of {net} than the clearance on layer {trace.layer}")
                        break
        for other_index, other in enumerate(plan.traces):
            if other_index > index and other.net != trace.net and other.layer == trace.layer:
                need = rules.clearance(trace.net, other.net, trace.layer, "trace_to_trace") + (trace.width + other.width) / 2
                if any(_segment_distance(a, b, c, d) < need - CLEARANCE_TOLERANCE for a, b in _segments(path) for c, d in _segments(other.path)):
                    blocked.append(f"{name}: closer to trace {other_index + 1} of {other.net} in the plan than the clearance")
    for index, via in enumerate(plan.vias):
        name = f"via {index + 1} of {via.net}"
        if via.net not in copper.nets:
            blocked.append(f"{name}: the board has no net {via.net}")
            continue
        for layer, net, poly in copper.pads:
            need = rules.clearance(via.net, net, layer, "pad_to_via") + via.radius
            if _polygon_distance(via.at, poly) < need - CLEARANCE_TOLERANCE:  # Layout refuses a via close to a pad of its own net too
                blocked.append(f"{name}: closer to a pad of {net or 'no net'} than {need - via.radius:g} mm on layer {layer}")
                break
        for layer, net, width, points in copper.traces:
            if net != via.net:
                need = rules.clearance(via.net, net, layer, "trace_to_via") + via.radius + width / 2
                if any(point_segment(via.at, a, b) < need - CLEARANCE_TOLERANCE for a, b in _segments(points)):
                    blocked.append(f"{name}: closer to a trace of {net} than the clearance on layer {layer}")
                    break
        for net, x, y, radius in copper.vias:
            need = rules.clearance(via.net, net, 1, "via_to_via") + via.radius + radius
            if math.hypot(via.at[0] - x, via.at[1] - y) < need - CLEARANCE_TOLERANCE:
                blocked.append(f"{name}: closer to a via of {net} than the clearance")
                break
    return blocked
