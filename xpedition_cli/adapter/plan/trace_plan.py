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
import re
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
    free_ends: bool = False  # a guard or ring, whose ends need not touch copper of its net
    joins: tuple[int, int] | None = None  # a lane's: the points where it turns onto its line and off it
    origin: str = ""  # the plan's entry it comes from, such as lanes[3] or the fan-out of J1, named in the check's reasons

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
    origin: str = ""  # the plan's entry it comes from, named in the check's reasons


@dataclass
class Plan:
    traces: list[Trace] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    fanouts: list[dict[str, Any]] = field(default_factory=list)  # the connectors the tool fans out (``parse_fanouts``)
    notes: list[str] = field(default_factory=list)  # what the fan-outs leave to the Agent
    guards: list[Any] = field(default_factory=list)  # ground guards beside lanes (``guards.parse_guards``)
    findings: list[dict[str, Any]] = field(default_factory=list)  # guard and ring lines that would float, ends left free
    rings: list[Any] = field(default_factory=list)  # ground rings inside the outline (``guards.parse_rings``)


FANOUT_KEYS = ("refdes", "padstack", "layer")


def parse_fanouts(items: Any) -> list[dict[str, Any]]:
    """The fan-outs a plan asks for: each connector's ``refdes``, the via ``padstack``, the ``layer``
    its far row changes to, and, if wanted, its ``plane_nets`` (pins of the near row that take a
    free via between the rows), the nets to ``skip`` (far pins without a via), the only ``nets`` to
    fan out (every other net but the plane nets is skipped), those that ``give_way`` (no via where
    the near pin of the column is of a plane net) and ``stub_width_mm``."""
    if not isinstance(items, list) or not items:
        raise _error("fanouts is a list of at least one connector")
    out = []
    for index, item in enumerate(items):
        what = f"fanouts[{index}]"
        if (not isinstance(item, dict) or set(item) - {*FANOUT_KEYS, "plane_nets", "skip", "nets", "give_way", "stub_width_mm"}
                or set(FANOUT_KEYS) - set(item)):
            raise _error(f"{what} has the keys {', '.join(FANOUT_KEYS)} and, if wanted, plane_nets, skip, nets, give_way and "
                         "stub_width_mm")
        if not isinstance(item["refdes"], str) or not item["refdes"] or not isinstance(item["padstack"], str) or not item["padstack"]:
            raise _error(f"{what} names the connector's refdes and a via padstack")
        if type(item["layer"]) is not int or item["layer"] < 1:
            raise _error(f"{what}.layer is the conductor layer the far row changes to, 1 at the top")
        lists = {}
        for key in ("plane_nets", "skip", "give_way"):
            value = item.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                raise _error(f"{what}.{key} is a list of net names")
            lists[key] = value
        only = item.get("nets")
        if only is not None and (not isinstance(only, list) or not only or not all(isinstance(v, str) and v for v in only)):
            raise _error(f"{what}.nets is a list of one or more net names")
        lists["nets"] = only
        width = item.get("stub_width_mm", 0.1)
        if isinstance(width, bool) or not isinstance(width, (int, float)) or not 0 < width <= 2:
            raise _error(f"{what}.stub_width_mm is a width of more than 0 and at most 2 mm")
        out.append({"refdes": item["refdes"], "padstack": item["padstack"], "layer": item["layer"], **lists, "stub_width": float(width)})
    return out


@dataclass
class Copper:
    """The board's copper the plan meets, in mm."""

    layer_count: int
    pads: list[tuple[int, str, list[Point]]] = field(default_factory=list)  # (layer, net, polygon)
    traces: list[tuple[int, str, float, list[Point]]] = field(default_factory=list)  # (layer, net, width, points)
    vias: list[tuple[str, float, float, float]] = field(default_factory=list)  # (net, x, y, radius); vias go through every layer
    nets: set[str] = field(default_factory=set)
    keepouts: list[tuple[str, int | None, list[Point]]] = field(default_factory=list)  # (kind, layer, polygon) of route and via keepouts
    planes: list[tuple[int, str, list[Point]]] = field(default_factory=list)  # (layer, net, polygon) of the plane shapes
    border: list[Point] | None = None  # Layout's route border, which copper keeps inside (its DRC reports what reaches past it)


@dataclass
class Rules:
    """Widths per net class and layer, and the clearance between two nets on a layer."""

    net_class_of: dict[str, str]
    widths: dict[str, dict[int, tuple[float, float]]]  # class -> layer -> (min, max)
    clearance: Any  # callable(net_a, net_b, layer, kind) -> mm; kind is trace_to_pad, trace_to_trace, trace_to_via, ...
    pairs: list[list[str]] = field(default_factory=list)  # the differential pairs the design defines


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


CHORD_MM = 0.002  # an arc is checked as chords this close to it, as the board's own arcs are read
MIN_ARC_MM = 1e-3  # a rounded corner whose arc would start closer than this to the corner stays sharp
CLEARANCE_TOLERANCE = 2.5e-3  # mm a distance may fall short of a rule: arcs are checked as chords, which bring an arc
# beside another up to a chord's depth closer than the copper is


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


def sampled(points: list[Point], radii: list[float], chord: float = CHORD_MM, arcs: list[bool] | None = None) -> list[Point]:
    """The centre line with each arc as chords within ``chord`` of it. ``arcs``, when given, gets
    for each piece of the line whether it is such a chord."""
    if not any(radii):
        if arcs is not None:
            arcs.extend([False] * (len(points) - 1))
        return list(points)
    path = [points[0]]
    marks: list[bool] = []
    for i, arc in enumerate(_corners(points, radii)[1:-1], start=1):
        if arc is None:
            path.append(points[i])
            marks.append(False)
            continue
        start, centre, end, r, ccw = arc
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
        sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
        step = 2 * math.acos(max(-1.0, 1 - chord / r)) if r > chord else math.pi / 2
        steps = max(1, math.ceil(abs(sweep) / step))
        path += [(centre[0] + r * math.cos(a0 + sweep * k / steps), centre[1] + r * math.sin(a0 + sweep * k / steps))
                 for k in range(steps + 1)]
        marks += [False] + [True] * steps  # on to the arc's start, then along it
    path.append(points[-1])
    marks.append(False)
    if arcs is not None:
        arcs.extend(marks)
    return path


def length(trace: Trace) -> float:
    """The trace's length in mm along its centre line, its arcs included."""
    path = trace.path
    return sum(math.dist(a, b) for a, b in zip(path, path[1:], strict=False))


def parse(data: Any, lanes: bool = False) -> Plan:
    """The plan's traces and vias; with ``lanes``, the plan has lanes, fan-outs or rings besides, so it may name none."""
    if not isinstance(data, dict) or not (data or lanes):
        raise _error("the file holds one JSON object with traces, vias and lanes")
    unknown = [k for k in data if k not in ("traces", "vias")]
    if unknown:
        raise _error(f"unknown key {unknown[0]!r}; the keys are traces, vias, lanes, fanouts, guards and rings")
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
        trace = Trace(net, layer, round(float(width), 4), [c[0] for c in corners], [c[1] for c in corners], origin=what)
        _corners(trace.points, trace.radii)  # a radius that does not fit is refused here
        plan.traces.append(trace)
    for index, item in enumerate(data.get("vias", [])):
        what = f"vias[{index}]"
        if not isinstance(item, dict) or set(item) - {"net", "at_mm", "padstack"}:
            raise _error(f"{what} has the keys net, at_mm and padstack")
        net, padstack = item.get("net"), item.get("padstack")
        if not isinstance(net, str) or not net or not isinstance(padstack, str) or not padstack:
            raise _error(f"{what} needs a net and a padstack")
        plan.vias.append(Via(net, _point(item.get("at_mm"), f"{what}.at_mm"), padstack, origin=what))
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
    for layer, net, width, points in copper.traces:  # Layout joins an end to a trace only on its centre line
        if net == trace.net and layer == trace.layer and any(point_segment(end, a, b) <= ON_CENTRE_MM for a, b in _segments(points)):
            return True
    for net, x, y, radius in copper.vias:
        if net == trace.net and math.hypot(end[0] - x, end[1] - y) <= radius + 1e-6:
            return True
    for layer, net, poly in copper.planes:  # a pour of the net makes its copper round the end
        if net == trace.net and layer == trace.layer and len(poly) >= 3 and inside(end, poly):
            return True
    for index, other in enumerate(plan.traces):
        if index != own and other.net == trace.net and other.layer == trace.layer and any(
                point_segment(end, a, b) <= ON_CENTRE_MM for a, b in _segments(other.path)):
            return True
    return any(via.net == trace.net and math.hypot(end[0] - via.at[0], end[1] - via.at[1]) <= via.radius + 1e-6 for via in plan.vias)


ON_CENTRE_MM = 0.005  # an end this close to a trace's centre line lies on it: Layout joins it there, not anywhere on its copper
TRACE_KEEPOUTS = ("trace", "trace_and_via")  # the obstruct kinds that keep traces out on their layer
VIA_KEEPOUTS = ("via", "trace_and_via")  # those that keep vias out; a via passes through every layer
CELL_MM = 1.0  # the grid the check finds nearby copper by
REACH_MM = 1.0  # no clearance rule is larger than this


class _Grid:
    """Items indexed by the grid cells their extents cover."""

    def __init__(self) -> None:
        self.cells: dict[tuple[int, int], list[Any]] = {}

    def add(self, item: Any, points: list[Point], grow: float) -> None:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        for i in range(math.floor((min(xs) - grow) / CELL_MM), math.floor((max(xs) + grow) / CELL_MM) + 1):
            for j in range(math.floor((min(ys) - grow) / CELL_MM), math.floor((max(ys) + grow) / CELL_MM) + 1):
                self.cells.setdefault((i, j), []).append(item)

    def near(self, points: list[Point], reach: float) -> list[Any]:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        found: dict[int, Any] = {}
        for i in range(math.floor((min(xs) - reach) / CELL_MM), math.floor((max(xs) + reach) / CELL_MM) + 1):
            for j in range(math.floor((min(ys) - reach) / CELL_MM), math.floor((max(ys) + reach) / CELL_MM) + 1):
                for item in self.cells.get((i, j), ()):
                    found[id(item)] = item
        return list(found.values())


BORDER_TOUCH_MM = 0.005  # copper whose edge meets the route border reaches past it for Layout's DRC (seen live)


def _border_grid(border: list[Point] | None) -> _Grid | None:
    if not border or len(border) < 3:
        return None
    grid = _Grid()
    for a, b in zip(border, border[1:] + border[:1], strict=True):
        grid.add((a, b), [a, b], 0.0)
    return grid


def _past_border(points: list[Point], reach: float, border: list[Point], grid: _Grid) -> bool:
    """Whether copper along ``points`` (a piece of trace, or a via's centre alone) comes within
    ``reach`` of the route border's line, or lies outside it."""
    if not all(inside(p, border) for p in points):
        return True
    pieces = list(zip(points, points[1:], strict=False)) or [(points[0], points[0])]
    for a, b in pieces:
        for c, d in grid.near([a, b], reach):
            if _segment_distance(a, b, c, d) < reach + BORDER_TOUCH_MM:
                return True
    return False


def check(plan: Plan, copper: Copper, rules: Rules) -> list[str]:
    """Every reason the plan cannot go onto the board; an empty list when it can."""
    blocked: list[str] = []
    edge = _border_grid(copper.border)
    board = _Grid()
    for layer, net, poly in copper.pads:
        board.add(("pad", layer, net, poly), poly, 0.0)
    for layer, net, width, points in copper.traces:
        for a, b in _segments(points):
            board.add(("trace", layer, net, (a, b, width)), [a, b], width / 2)
    for net, x, y, radius in copper.vias:
        board.add(("via", None, net, (x, y, radius)), [(x, y)], radius)
    for number, via in enumerate(plan.vias):  # the plan's own vias stand like the board's
        board.add(("via", None, via.net, (via.at[0], via.at[1], via.radius), number), [via.at], via.radius)
    own = _Grid()
    for index, trace in enumerate(plan.traces):
        for a, b in _segments(trace.path):
            own.add((index, a, b), [a, b], trace.width / 2)
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
        for end in () if trace.free_ends else (trace.points[0], trace.points[-1]):
            if not _touches(end, trace, copper, plan, index):
                blocked.append(f"{name}: its end at ({end[0]:g}, {end[1]:g}) mm touches no copper of {trace.net} on layer {trace.layer}")
        found: dict[str, Point] = {}  # each reason once, with the first place it holds
        others: set[int] = set()
        if edge is not None and copper.border:
            for a, b in _segments(trace.path):
                if _past_border([a, b], trace.width / 2, copper.border, edge):
                    found.setdefault(f"{name}: reaches past the route border on layer {trace.layer}, where Layout's DRC reports it; "
                                     "keep it further from the board's edge", _nearest_on(a, b, _edges(copper.border)))
                    break
        for a, b in _segments(trace.path):
            for kind, layer, poly in copper.keepouts:
                if kind in TRACE_KEEPOUTS and layer == trace.layer and _segment_polygon(a, b, poly) < trace.width / 2 - CLEARANCE_TOLERANCE:
                    found.setdefault(f"{name}: runs into a keepout of traces on layer {layer}", _nearest_on(a, b, _edges(poly)))
            for kind, layer, net, shape, *_ in board.near([a, b], REACH_MM + trace.width / 2):
                if net == trace.net:
                    continue
                if kind == "pad" and layer == trace.layer:
                    need = rules.clearance(trace.net, net, layer, "trace_to_pad") + trace.width / 2
                    if _segment_polygon(a, b, shape) < need - CLEARANCE_TOLERANCE:
                        found.setdefault(f"{name}: closer to a pad of {net or 'no net'} than {need - trace.width / 2:g} mm on layer {layer}",
                                         _nearest_on(a, b, _edges(shape)))
                elif kind == "trace" and layer == trace.layer:
                    c, d, width = shape
                    need = rules.clearance(trace.net, net, layer, "trace_to_trace") + (trace.width + width) / 2
                    if _segment_distance(a, b, c, d) < need - CLEARANCE_TOLERANCE:
                        found.setdefault(f"{name}: closer to a trace of {net} than the clearance on layer {layer}", _nearest_on(a, b, [(c, d)]))
                elif kind == "via":
                    x, y, radius = shape
                    need = rules.clearance(trace.net, net, trace.layer, "trace_to_via") + trace.width / 2 + radius
                    if point_segment((x, y), a, b) < need - CLEARANCE_TOLERANCE:
                        found.setdefault(f"{name}: closer to a via of {net} than the clearance on layer {trace.layer}", _foot((x, y), a, b))
            for other_index, c, d in own.near([a, b], REACH_MM + trace.width / 2):
                other = plan.traces[other_index]
                if other_index <= index or other_index in others or other.net == trace.net or other.layer != trace.layer:
                    continue
                need = rules.clearance(trace.net, other.net, trace.layer, "trace_to_trace") + (trace.width + other.width) / 2
                gap = _segment_distance(a, b, c, d)
                if gap < need - CLEARANCE_TOLERANCE:
                    others.add(other_index)
                    if gap <= 0.0:
                        found.setdefault(f"{name}: crosses trace {other_index + 1} of {other.net}{_from(other)} on layer {trace.layer}: "
                                         "their ends lie in opposite orders along the way, so on one layer they must cross; put one on "
                                         "another layer through vias, or run it round the other's end", _nearest_on(a, b, [(c, d)]))
                    else:
                        found.setdefault(f"{name}: closer to trace {other_index + 1} of {other.net} in the plan{_from(other)} than the "
                                         "clearance", _nearest_on(a, b, [(c, d)]))
        blocked += [reason + where(trace.origin, at) for reason, at in found.items()]
    for index, via in enumerate(plan.vias):
        name = f"via {index + 1} of {via.net}"
        before = len(blocked)
        if via.net not in copper.nets:
            blocked.append(f"{name}: the board has no net {via.net}")
            continue
        near = [item for item in board.near([via.at], REACH_MM + via.radius) if item[4:] != (index,)]  # not the via itself
        near += [("trace", plan.traces[i].layer, plan.traces[i].net, (c, d, plan.traces[i].width))
                 for i, c, d in own.near([via.at], REACH_MM + via.radius)]
        pad = next((item for item in near if item[0] == "pad"
                    and _polygon_distance(via.at, item[3]) < rules.clearance(via.net, item[2], item[1], "pad_to_via") + via.radius
                    - CLEARANCE_TOLERANCE), None)
        if pad is not None:  # Layout refuses a via close to a pad of its own net too
            need = rules.clearance(via.net, pad[2], pad[1], "pad_to_via")
            blocked.append(f"{name}: closer to a pad of {pad[2] or 'no net'} than {need:g} mm on layer {pad[1]}")
        trace = next((item for item in near if item[0] == "trace" and item[2] != via.net
                      and point_segment(via.at, item[3][0], item[3][1]) < rules.clearance(via.net, item[2], item[1], "trace_to_via")
                      + via.radius + item[3][2] / 2 - CLEARANCE_TOLERANCE), None)
        if trace is not None:
            blocked.append(f"{name}: closer to a trace of {trace[2]} than the clearance on layer {trace[1]}")
        other = next((item for item in near if item[0] == "via"
                      and math.hypot(via.at[0] - item[3][0], via.at[1] - item[3][1]) < rules.clearance(via.net, item[2], 1, "via_to_via")
                      + via.radius + item[3][2] - CLEARANCE_TOLERANCE), None)
        if other is not None:
            blocked.append(f"{name}: closer to a via of {other[2]} than the clearance")
        if edge is not None and copper.border and _past_border([via.at], via.radius, copper.border, edge):
            blocked.append(f"{name}: reaches past the route border, where Layout's DRC reports it")
        keepout = next((layer for kind, layer, poly in copper.keepouts
                        if kind in VIA_KEEPOUTS and _polygon_distance(via.at, poly) < via.radius - CLEARANCE_TOLERANCE), 0)
        if keepout != 0:
            blocked.append(f"{name}: stands in a keepout of vias on layer {keepout}")
        blocked[before:] = [reason + where(via.origin, via.at) for reason in blocked[before:]]
    return blocked


def where(origin: str, at: Point) -> str:
    """The end of a reason the check gives: the plan's entry the copper comes from and the place."""
    return f" ({origin + ', ' if origin else ''}at ({at[0]:.3f}, {at[1]:.3f}) mm)"


WHERE = re.compile(r" \((?:[^()]*, )?at \([-0-9.]+, [-0-9.]+\) mm\)$")


def without_where(reason: str) -> str:
    """A reason without the place ``where`` gave it, to compare reasons about copper that moved."""
    return WHERE.sub("", reason)


def _from(trace: Trace) -> str:
    return f" ({trace.origin})" if trace.origin else ""


def _edges(points: list[Point]) -> list[tuple[Point, Point]]:
    return list(zip(points, points[1:] + points[:1], strict=True)) if len(points) > 1 else [(points[0], points[0])]


def _foot(p: Point, a: Point, b: Point) -> Point:
    """The point of segment a-b nearest to p."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0 if not length else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    return a[0] + dx * t, a[1] + dy * t


def _nearest_on(a: Point, b: Point, edges: list[tuple[Point, Point]]) -> Point:
    """The point of segment a-b nearest to any of ``edges``: where it comes closest to a shape."""
    best, at = math.inf, a
    for c, d in edges:
        rx, ry, sx, sy = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
        denominator = rx * sy - ry * sx
        if abs(denominator) > 1e-12:  # where they cross, when they do
            t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / denominator
            u = ((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) / denominator
            if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
                return a[0] + rx * t, a[1] + ry * t
        for q in (a, b, _foot(c, a, b), _foot(d, a, b)):
            distance = point_segment(q, c, d)
            if distance < best:
                best, at = distance, q
    return at
