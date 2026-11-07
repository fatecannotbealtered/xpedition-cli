"""The routing plan of trace create, and the offline check it passes before Layout is touched.

A plan names traces and vias by net, in mm:

```json
{"traces": [{"net": "USB_TX_P", "layer": 1, "width_mm": 0.1, "points_mm": [[5.0, 9.0], [85.0, 9.0]]}],
 "vias": [{"net": "GND", "at_mm": [45.0, 3.0], "padstack": "VIA_D0.2_P0.4"}]}
```

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


def parse(data: Any) -> Plan:
    if not isinstance(data, dict) or not data:
        raise _error("the file holds one JSON object with traces and vias")
    unknown = [k for k in data if k not in ("traces", "vias")]
    if unknown:
        raise _error(f"unknown key {unknown[0]!r}; the keys are traces and vias")
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
        plan.traces.append(Trace(net, layer, round(float(width), 4), [_point(p, f"{what}.points_mm") for p in points]))
    for index, item in enumerate(data.get("vias", [])):
        what = f"vias[{index}]"
        if not isinstance(item, dict) or set(item) - {"net", "at_mm", "padstack"}:
            raise _error(f"{what} has the keys net, at_mm and padstack")
        net, padstack = item.get("net"), item.get("padstack")
        if not isinstance(net, str) or not net or not isinstance(padstack, str) or not padstack:
            raise _error(f"{what} needs a net and a padstack")
        plan.vias.append(Via(net, _point(item.get("at_mm"), f"{what}.at_mm"), padstack))
    if not plan.traces and not plan.vias:
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
                point_segment(end, a, b) <= other.width / 2 + 1e-6 for a, b in _segments(other.points)):
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
        for a, b in _segments(trace.points):
            for layer, net, poly in copper.pads:
                if layer == trace.layer and net != trace.net:
                    need = rules.clearance(trace.net, net, layer, "trace_to_pad") + trace.width / 2
                    if _segment_polygon(a, b, poly) < need - 1e-6:
                        blocked.append(f"{name}: closer to a pad of {net or 'no net'} than {need - trace.width / 2:g} mm on layer {layer}")
                        break
            for layer, net, width, points in copper.traces:
                if layer == trace.layer and net != trace.net:
                    need = rules.clearance(trace.net, net, layer, "trace_to_trace") + (trace.width + width) / 2
                    if any(_segment_distance(a, b, c, d) < need - 1e-6 for c, d in _segments(points)):
                        blocked.append(f"{name}: closer to a trace of {net} than the clearance on layer {layer}")
                        break
            for net, x, y, radius in copper.vias:
                if net != trace.net:
                    need = rules.clearance(trace.net, net, trace.layer, "trace_to_via") + trace.width / 2 + radius
                    if point_segment((x, y), a, b) < need - 1e-6:
                        blocked.append(f"{name}: closer to a via of {net} than the clearance on layer {trace.layer}")
                        break
        for other_index, other in enumerate(plan.traces):
            if other_index > index and other.net != trace.net and other.layer == trace.layer:
                need = rules.clearance(trace.net, other.net, trace.layer, "trace_to_trace") + (trace.width + other.width) / 2
                if any(_segment_distance(a, b, c, d) < need - 1e-6 for a, b in _segments(trace.points) for c, d in _segments(other.points)):
                    blocked.append(f"{name}: closer to trace {other_index + 1} of {other.net} in the plan than the clearance")
    for index, via in enumerate(plan.vias):
        name = f"via {index + 1} of {via.net}"
        if via.net not in copper.nets:
            blocked.append(f"{name}: the board has no net {via.net}")
            continue
        for layer, net, poly in copper.pads:
            need = rules.clearance(via.net, net, layer, "pad_to_via") + via.radius
            if _polygon_distance(via.at, poly) < need - 1e-6:  # Layout refuses a via close to a pad of its own net too
                blocked.append(f"{name}: closer to a pad of {net or 'no net'} than {need - via.radius:g} mm on layer {layer}")
                break
        for layer, net, width, points in copper.traces:
            if net != via.net:
                need = rules.clearance(via.net, net, layer, "trace_to_via") + via.radius + width / 2
                if any(point_segment(via.at, a, b) < need - 1e-6 for a, b in _segments(points)):
                    blocked.append(f"{name}: closer to a trace of {net} than the clearance on layer {layer}")
                    break
        for net, x, y, radius in copper.vias:
            need = rules.clearance(via.net, net, 1, "via_to_via") + via.radius + radius
            if math.hypot(via.at[0] - x, via.at[1] - y) < need - 1e-6:
                blocked.append(f"{name}: closer to a via of {net} than the clearance")
                break
    return blocked
