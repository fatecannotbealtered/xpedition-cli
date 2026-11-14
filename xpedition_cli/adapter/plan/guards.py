"""Ground guards and outline rings of a routing plan: traces of a ground net that run beside the
signals a plan names, or around the board inside its outline, with vias along them.

A guard runs on both sides of a group of lanes, at a gap from their copper, along the same edge of
the outline, so it keeps their shape through every turn; with ``between``, a guard also runs
between each two neighbours of the group. A ring follows the whole outline at a distance from it,
on each layer named. Neither may touch another net's copper: each is kept only where it clears
every pad, trace and via of other nets by the clearance rules, so it stops short of a connector's
fan-out or a pad near the board's edge, and goes on past it. What is left of each is one trace per
stretch; stretches shorter than ``min_run_mm`` are dropped. Vias of the guard's net stand along the
stretches at a pitch, each where it clears all copper on every layer it passes through, and none in
a bend zone. A trace end that touches nothing is a hanger to DRC, and a guard between two signals
may have no room for a via anywhere along it: each end stops on a via of its own where one fits
near it, or else joins a pin or via of its net nearby by a path that clears the other nets, such as
on along the signal it guards to a ground pin of the connector the signal fans out to.

Pure Python, in mm; the adapter supplies the board's copper, the rules and the bend zones.
"""
from __future__ import annotations

import bisect
import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any

from . import board_metrics
from . import trace_plan as TP
from .lanes import Lane, _fitted, _jogged, edge_chain, offset_chain
from .stitching import CUTOUT_CLEARANCE_MM
from .shapes import inside, point_segment

Point = tuple[float, float]
STEP_MM = 0.1  # a guard or ring is tested for clearance in pieces this long
CHORD_MM = 0.002  # its arcs become chords this close to them
CELL_MM = 0.5  # the grid the copper is indexed by
ROW_MM = 0.02  # the rows a border's crossings are kept for
REACH_MM = 1.0  # no clearance rule is larger than this
VIA_SHIFT_MM = 0.1  # a via that cannot stand at its pitch point is tried this far along at a time
LAY_STEP_MM = 0.05  # vias along a stretch are looked for this far apart
END_SNAP_MM = 0.01  # an end this near a via's centre is moved onto it when drawn, not run on to it (layout_edit.SNAP_MM)
ACROSS_STEP_MM = 0.05  # a via along a line is tried this far apart across it
PITCH_SPARE_MM = 0.01  # vias along a stretch stand this much closer than the pitch, so that the board's rounding keeps them within it
END_REACH_MM = 4.0  # an end looks this far along for a via spot, past a bend zone it may start in
VIA_MARGIN_MM = 0.003  # a via, free to stand anywhere along, keeps this much more than a rule: other copper's arcs are tested as chords
MIN_RUN_MM = 1.0
GUARD_KEYS = ("nets", "net", "width_mm", "gap_mm")
RING_KEYS = ("net", "layers", "width_mm", "offset_mm")


@dataclass
class Guard:
    nets: list[list[str]]  # the groups it guards, in any order: a net alone, or nets kept together such as a pair
    net: str
    width: float
    gap: float
    layer: int | None = None
    between: bool = False
    pitch: float | None = None
    padstack: str | None = None
    min_run: float = MIN_RUN_MM


@dataclass
class Ring:
    net: str
    layers: list[int]
    width: float
    offset: float
    pitch: float | None = None
    padstack: str | None = None
    min_run: float = MIN_RUN_MM


def _number(value: Any, what: str, low: float, high: float, low_open: bool = True) -> float:
    """A length of more than ``low`` (or of ``low``, without ``low_open``) and at most ``high`` mm."""
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value > high
            or value < low or (low_open and value == low)):
        span = f"more than {low:g} and at most {high:g}" if low_open else f"{low:g} to {high:g}"
        raise TP._error(f"{what} is a length of {span} mm")
    return float(value)


def _vias(item: dict[str, Any], what: str) -> tuple[float | None, str | None]:
    pitch, padstack = item.get("via_pitch_mm"), item.get("padstack")
    if (pitch is None) != (padstack is None):
        raise TP._error(f"{what} gives via_pitch_mm and padstack together, for vias along it, or neither")
    if padstack is not None and (not isinstance(padstack, str) or not padstack):
        raise TP._error(f"{what}.padstack is a via padstack name")
    return (None if pitch is None else _number(pitch, f"{what}.via_pitch_mm", 0.2, 50)), padstack


def parse_guards(items: Any) -> list[Guard]:
    if not isinstance(items, list) or not items:
        raise TP._error("guards is a list of at least one group")
    out = []
    for index, item in enumerate(items):
        what = f"guards[{index}]"
        allowed = {*GUARD_KEYS, "layer", "between", "via_pitch_mm", "padstack", "min_run_mm"}
        if not isinstance(item, dict) or set(item) - allowed or set(GUARD_KEYS) - set(item):
            raise TP._error(f"{what} has the keys {', '.join(GUARD_KEYS)} and, if wanted, layer, between, via_pitch_mm with padstack, "
                            "and min_run_mm")
        nets = item["nets"]
        groups = [[n] if isinstance(n, str) else n for n in nets] if isinstance(nets, list) else []
        flat = [n for group in groups if isinstance(group, list) for n in group]
        if (not groups or not all(isinstance(g, list) and g for g in groups) or not all(isinstance(n, str) and n for n in flat)
                or len(set(flat)) != len(flat)):
            raise TP._error(f"{what}.nets lists the nets of the lanes it guards, each once: a net alone, or a list of nets kept "
                            "together, such as a pair")
        if not isinstance(item["net"], str) or not item["net"]:
            raise TP._error(f"{what}.net is the guard's net, such as GND")
        layer = item.get("layer")
        if layer is not None and (type(layer) is not int or layer < 1):
            raise TP._error(f"{what}.layer is a conductor layer number, 1 at the top")
        if not isinstance(item.get("between", False), bool):
            raise TP._error(f"{what}.between is true or false")
        pitch, padstack = _vias(item, what)
        out.append(Guard(groups, item["net"], _number(item["width_mm"], f"{what}.width_mm", 0, 5),
                         _number(item["gap_mm"], f"{what}.gap_mm", 0, 5), layer, item.get("between", False), pitch, padstack,
                         _number(item.get("min_run_mm", MIN_RUN_MM), f"{what}.min_run_mm", 0, 100, low_open=False)))
    return out


def parse_rings(items: Any) -> list[Ring]:
    if not isinstance(items, list) or not items:
        raise TP._error("rings is a list of at least one ring")
    out = []
    for index, item in enumerate(items):
        what = f"rings[{index}]"
        if not isinstance(item, dict) or set(item) - {*RING_KEYS, "via_pitch_mm", "padstack", "min_run_mm"} or set(RING_KEYS) - set(item):
            raise TP._error(f"{what} has the keys {', '.join(RING_KEYS)} and, if wanted, via_pitch_mm with padstack, and min_run_mm")
        layers = item["layers"]
        if not isinstance(layers, list) or not layers or not all(type(v) is int and v >= 1 for v in layers) or len(set(layers)) != len(layers):
            raise TP._error(f"{what}.layers lists conductor layer numbers, each once, 1 at the top")
        if not isinstance(item["net"], str) or not item["net"]:
            raise TP._error(f"{what}.net is the ring's net, such as GND")
        pitch, padstack = _vias(item, what)
        out.append(Ring(item["net"], layers, _number(item["width_mm"], f"{what}.width_mm", 0, 5),
                        _number(item["offset_mm"], f"{what}.offset_mm", 0, 50), pitch, padstack,
                        _number(item.get("min_run_mm", MIN_RUN_MM), f"{what}.min_run_mm", 0, 100, low_open=False)))
    return out


# ------------------------------------------------------------------ where guards and rings run


def members(guard: Guard, lanes: list[Lane], what: str) -> list[list[Lane]]:
    """The groups of lanes a guard runs beside, each sorted by distance from the edge and the groups
    in that order; they share a layer and an edge, and no group lies between the lanes of another."""
    groups: list[list[Lane]] = []
    for nets in guard.nets:
        group = []
        for net in nets:
            mine = [lane for lane in lanes if lane.net == net and (guard.layer is None or lane.layer == guard.layer)]
            if not mine:
                raise TP._error(f"{what} names {net}, which no lane of the plan has" + (f" on layer {guard.layer}" if guard.layer else ""))
            if len(mine) > 1 and len({lane.layer for lane in mine}) > 1:
                raise TP._error(f"{what} names {net}, which has lanes on layers {sorted(lane.layer for lane in mine)}; name the layer")
            if len(mine) > 1:
                raise TP._error(f"{what} names {net}, which has {len(mine)} lanes on layer {mine[0].layer}; a guard follows one lane of "
                                "each net along one edge. Route the net as one lane, which jogs moves to another distance from its "
                                "edge, or leave it out of the guard and lay its ground as lanes of the ground net beside it")
            group.append(mine[0])
        groups.append(sorted(group, key=lambda lane: lane.offset))
    every = [lane for group in groups for lane in group]
    if len({lane.layer for lane in every}) > 1 or len({(lane.edge[0], lane.edge[1]) for lane in every}) > 1:
        raise TP._error(f"{what} names lanes on more than one layer or edge; a guard runs beside lanes of one edge on one layer")
    groups.sort(key=lambda group: group[0].offset)
    for a, b in zip(groups, groups[1:], strict=False):
        if a[-1].offset > b[0].offset:
            raise TP._error(f"{what} names {b[0].net} between the lanes of {', '.join(lane.net for lane in a)}, which it keeps together")
    return groups


def guard_offsets(guard: Guard, groups: list[list[Lane]], what: str) -> list[tuple[float, float]]:
    """The guard lines, each its distance from the edge and its width: one on each side of the
    groups and, with ``between``, one between each two neighbouring groups, as wide as the room
    leaves at the gap from both, so that it keeps the gap on either side; a line with no room is refused."""
    half = guard.width / 2
    first, last = groups[0][0], groups[-1][-1]
    inner = first.offset - first.width / 2 - guard.gap - half
    if inner - half <= 0:
        raise TP._error(f"{what} has no room between the edge and {first.net} for a guard {guard.width:g} mm wide at {guard.gap:g} mm")
    lines = [(inner, guard.width)]
    if guard.between:
        for left, right in zip(groups, groups[1:], strict=False):
            a, b = left[-1], right[0]
            room = (b.offset - b.width / 2) - (a.offset + a.width / 2)
            if room < 2 * guard.gap + guard.width - 1e-9:
                raise TP._error(f"{what} has no room for a guard between {a.net} and {b.net}: their copper is {room:g} mm apart, and a "
                                f"guard {guard.width:g} mm wide at {guard.gap:g} mm needs {2 * guard.gap + guard.width:g} mm")
            lines.append((((a.offset + a.width / 2) + (b.offset - b.width / 2)) / 2, round(room - 2 * guard.gap, 4)))
    lines.append((last.offset + last.width / 2 + guard.gap + half, guard.width))
    return lines


@dataclass
class Line:
    """A guard line: its layer, net, distance from the edge, width, the lanes it runs beside, how
    its vias are placed, and how wide a join from its free end is: the guard's own width, not the
    width of a line that fills the room between two groups. Lines of one net that touch are merged
    into one as wide as both."""

    layer: int
    net: str
    offset: float
    width: float
    beside: list[Lane]
    pitch: float | None
    padstack: str | None
    min_run: float
    join_width: float
    jogs: list[tuple[Point, float, float]] = field(default_factory=list)  # where it moves with its lanes: at, distance, start earlier by
    forward: bool = True  # whether its lanes run with the edge's chain, which says where a jog starts


def merged(lines: list[Line]) -> list[Line]:
    """The lines with those of one layer, net and edge whose copper overlaps or touches made one."""
    out: list[Line] = []
    for line in sorted(lines, key=lambda item: (item.layer, item.net, item.beside[0].edge, item.offset)):
        last = out[-1] if out else None
        if (last is not None and (last.layer, last.net, last.beside[0].edge) == (line.layer, line.net, line.beside[0].edge)
                and line.offset - line.width / 2 <= last.offset + last.width / 2 + 1e-6):
            low = min(last.offset - last.width / 2, line.offset - line.width / 2)
            high = max(last.offset + last.width / 2, line.offset + line.width / 2)
            jogs = last.jogs
            if len(last.jogs) == len(line.jogs):  # both move with their lanes: the merged line moves as both do
                jogs = [(at, (min(a - last.width / 2, b - line.width / 2) + max(a + last.width / 2, b + line.width / 2)) / 2,
                         max(early_a, early_b)) for (at, a, early_a), (_at, b, early_b) in zip(last.jogs, line.jogs, strict=True)]
            out[-1] = Line(last.layer, last.net, (low + high) / 2, high - low, last.beside + line.beside, last.pitch or line.pitch,
                           last.padstack or line.padstack, min(last.min_run, line.min_run), min(last.join_width, line.join_width),
                           jogs, last.forward)
            continue
        out.append(line)
    return out


def fine(points: list[Point], radii: list[float], chord: float = CHORD_MM) -> list[Point]:
    """A centre line with each arc as chords within ``chord`` of it."""
    arcs = TP._corners(points, radii)
    out = [points[0]]
    for i in range(1, len(points) - 1):
        arc = arcs[i]
        if arc is None:
            out.append(points[i])
            continue
        start, centre, end, r, ccw = arc
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
        sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
        step = 2 * math.acos(max(-1.0, 1 - chord / r)) if r > chord else math.pi / 2
        count = max(1, math.ceil(abs(sweep) / step))
        out += [(centre[0] + r * math.cos(a0 + sweep * k / count), centre[1] + r * math.sin(a0 + sweep * k / count))
                for k in range(count + 1)]
    out.append(points[-1])
    return [p for i, p in enumerate(out) if i == 0 or math.dist(p, out[i - 1]) > 1e-9]


def guard_line(lane: Lane, offset: float, ring: list[tuple[Point, float]], jogs: list[tuple[Point, float, float]] | None = None,
               forward: bool = True) -> list[Point]:
    """A guard line as short chords: the offset of the lane's edge at ``offset``, moved where ``jogs``
    say, the whole length of the edge."""
    return guard_chain(lane, offset, ring, jogs, forward).line


def guard_jogs(guard: Guard, groups: list[list[Lane]], what: str) -> list[list[tuple[Point, float]]]:
    """For each line of ``guard_offsets``, where it moves with the lanes it guards: at each place the
    lanes jog, the distance ``guard_offsets`` gives for the lanes' new distances. Lanes that jog at
    different places, or not all of them, are refused: the guard could follow none of them."""
    beside = [lane for group in groups for lane in group]
    counts = {len(lane.jogs) for lane in beside}
    lines = guard_offsets(guard, groups, what)
    if counts == {0}:
        return [[] for _ in lines]
    places = {tuple((round(at[0], 0), round(at[1], 0)) for at, _o, _e in lane.jogs) for lane in beside}
    if len(counts) > 1 or len(places) > 1:
        raise TP._error(f"{what} guards lanes that jog at different places, or only some of them; a guard moves with its lanes only "
                        "where all of them jog at one place: give them the same jogs")
    out: list[list[tuple[Point, float]]] = [[] for _ in lines]
    for k in range(next(iter(counts))):
        moved = sorted((sorted((replace(lane, offset=lane.jogs[k][1]) for lane in group), key=lambda lane: lane.offset)
                        for group in groups), key=lambda group: group[0].offset)
        after = guard_offsets(guard, moved, f"{what}, after the lanes' jog {k}")
        if len(after) != len(lines):
            raise TP._error(f"{what}: its lanes change order at their jog {k}, so its lines cannot follow them")
        for line, (offset, _width) in zip(out, after, strict=True):
            line.append((beside[0].jogs[k][0], offset))
    return out


def position(line: list[Point], point: Point) -> float:
    """How far along ``line`` the point nearest to ``point`` lies."""
    best, at, walked = math.inf, 0.0, 0.0
    for a, b in zip(line, line[1:], strict=False):
        length = math.dist(a, b)
        t = 0.0 if not length else max(0.0, min(1.0, ((point[0] - a[0]) * (b[0] - a[0]) + (point[1] - a[1]) * (b[1] - a[1])) / length ** 2))
        foot = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        distance = math.dist(point, foot)
        if distance < best - 1e-12:
            best, at = distance, walked + t * length
        walked += length
    return at


def cut(line: list[Point], start: float, end: float) -> list[Point]:
    """The part of ``line`` from ``start`` to ``end`` along it."""
    out: list[Point] = []
    walked = 0.0
    for a, b in zip(line, line[1:], strict=False):
        length = math.dist(a, b)
        low, high = max(start, walked), min(end, walked + length)
        if high > low or (length == 0 and start <= walked <= end):
            for at in (low, high):
                t = (at - walked) / length if length else 0.0
                point = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                if not out or math.dist(out[-1], point) > 1e-9:
                    out.append(point)
        walked += length
    return out


class _Segments:
    """A polyline's segments indexed on a grid, for the distance from a point to it."""

    def __init__(self, points: list[Point], closed: bool = False) -> None:
        self.crossings: dict[int, list[float]] = {}  # a closed line's x crossings along each thin row, sorted
        self.cells: dict[tuple[int, int], list[tuple[Point, Point]]] = defaultdict(list)
        self.rows: dict[int, list[tuple[Point, Point]]] = defaultdict(list)
        pairs = list(zip(points, points[1:] + points[:1] if closed else points[1:], strict=False))
        for a, b in pairs:
            for i in range(math.floor(min(a[0], b[0]) / CELL_MM), math.floor(max(a[0], b[0]) / CELL_MM) + 1):
                for j in range(math.floor(min(a[1], b[1]) / CELL_MM), math.floor(max(a[1], b[1]) / CELL_MM) + 1):
                    self.cells[(i, j)].append((a, b))
            for j in range(math.floor(min(a[1], b[1]) / CELL_MM), math.floor(max(a[1], b[1]) / CELL_MM) + 1):
                self.rows[j].append((a, b))

    def near(self, point: Point, reach: float) -> float:
        """The distance to the nearest segment within ``reach`` of the point, else infinity."""
        best = math.inf
        for i in range(math.floor((point[0] - reach) / CELL_MM), math.floor((point[0] + reach) / CELL_MM) + 1):
            for j in range(math.floor((point[1] - reach) / CELL_MM), math.floor((point[1] + reach) / CELL_MM) + 1):
                for a, b in self.cells.get((i, j), ()):
                    best = min(best, point_segment(point, a, b))
        return best

    def piece_near(self, a: Point, b: Point, reach: float) -> float:
        """The distance from the segment a-b to the nearest segment of the line within ``reach`` of it, else infinity."""
        best = math.inf
        seen: set[tuple[Point, Point]] = set()
        for i in range(math.floor((min(a[0], b[0]) - reach) / CELL_MM), math.floor((max(a[0], b[0]) + reach) / CELL_MM) + 1):
            for j in range(math.floor((min(a[1], b[1]) - reach) / CELL_MM), math.floor((max(a[1], b[1]) + reach) / CELL_MM) + 1):
                for segment in self.cells.get((i, j), ()):
                    if segment not in seen:
                        seen.add(segment)
                        best = min(best, TP._segment_distance(a, b, *segment))
        return best

    def encloses(self, point: Point) -> bool:
        """Whether a closed polyline encloses the point: a ray to the right crosses it an odd number of
        times. The crossings are taken along the middle of a row ``ROW_MM`` thick, so the answer holds
        for a point at least that far from the line, as one kept clear of a border is."""
        row = math.floor(point[1] / ROW_MM)
        xs = self.crossings.get(row)
        if xs is None:
            y = (row + 0.5) * ROW_MM
            xs = sorted(x1 + (y - y1) * (x2 - x1) / (y2 - y1) for (x1, y1), (x2, y2) in self.rows.get(math.floor(y / CELL_MM), ())
                        if (y1 > y) != (y2 > y))
            self.crossings[row] = xs
        return (len(xs) - bisect.bisect_right(xs, point[0])) % 2 == 1


def lane_span(line: list[Point], member_line: list[Point], traces: list[TP.Trace]) -> tuple[float, float] | None:
    """Where along ``line`` a lane runs on its own line, ``member_line``, by the points of its traces
    that lie on it: the first and last such points, projected onto ``line``."""
    index = _Segments(member_line)
    on = [p for trace in traces for p in fine(trace.points, trace.radii) if index.near(p, 2e-3) <= 1e-3]
    if not on:
        return None
    first = min(on, key=lambda p: position(member_line, p))
    last = max(on, key=lambda p: position(member_line, p))
    start, end = position(line, first), position(line, last)
    return (start, end) if end > start else None


def ring_line(ring: list[tuple[Point, float]], offset: float) -> tuple[list[Point], bool]:
    """The outline moved ``offset`` inwards as short chords, closed, and whether the board's inside lies on its left."""
    chain, left = ring_chain(ring, offset)
    return chain.line, left


# ------------------------------------------------------------------ the copper in the way


def in_pad_box(point: Point, centre: Point, radius: float) -> bool:
    """Whether ``point`` lies within a via's extents, as drawing finds the via an end lies on."""
    return abs(point[0] - centre[0]) <= radius + 1e-6 and abs(point[1] - centre[1]) <= radius + 1e-6


@dataclass
class _Item:
    kind: str  # pad, trace or via
    layer: int | None  # None for a via, which passes through every layer
    net: str
    geometry: Any  # a pad's polygon, a trace's (a, b, width), a via's (x, y, radius)
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # its copper's extents
    arc: bool = True  # a trace's piece is a chord of an arc, which lies up to TP.CHORD_MM beyond it


class Obstacles:
    """The copper a guard, a ring or their vias must clear, indexed on a grid."""

    def __init__(self, copper: TP.Copper, plan: TP.Plan, rules: TP.Rules, border: list[Point] | None = None,
                 cutouts: list[list[Point]] | None = None) -> None:
        self.rules = rules
        self._needs: dict[tuple[str, str, Any, str], float] = {}
        self.border = _Segments(border, closed=True) if border else None  # copper keeps inside it, such as Layout's route border
        self.cells: dict[tuple[int, int], list[_Item]] = defaultdict(list)
        self.ends: dict[tuple[int, int], list[tuple[str, Point]]] = defaultdict(list)  # the plan's trace ends, which it draws
        for layer, net, poly in copper.pads:
            self._add(_Item("pad", layer, net, poly), poly)
        for layer, net, width, points in copper.traces:
            for a, b in zip(points, points[1:], strict=False):
                self._add(_Item("trace", layer, net, (a, b, width)), [a, b], width / 2)
        for net, x, y, radius in copper.vias:
            self.add_via(net, (x, y), radius)
        for trace in plan.traces:
            arcs: list[bool] = []
            path = TP.sampled(trace.points, trace.radii, arcs=arcs)
            self.add_trace(trace.net, trace.layer, trace.width, path, arcs)
        for via in plan.vias:
            self.add_via(via.net, via.at, via.radius)
        self.bodies = [poly for poly in getattr(copper, "bodies", None) or [] if len(poly) >= 3]  # parts, which the vias it places keep out of
        self.body_boxes = [(min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly))
                           for poly in self.bodies]
        self.keepouts = [(kind, layer, poly, (min(q[0] for q in poly), min(q[1] for q in poly), max(q[0] for q in poly),
                                              max(q[1] for q in poly)))
                         for kind, layer, poly in getattr(copper, "keepouts", None) or [] if len(poly) >= 3]
        self.holes: list[tuple[tuple[float, float, float, float], _Segments]] = []
        for cutout in cutouts or []:  # slots and holes inside the outline, which copper keeps clear of: each edge, and inside
            for c, d in zip(cutout, cutout[1:] + cutout[:1], strict=False):
                self._add(_Item("cutout", None, "", (c, d)), [c, d])
            xs, ys = [q[0] for q in cutout], [q[1] for q in cutout]
            self.holes.append(((min(xs), min(ys), max(xs), max(ys)), _Segments(cutout, closed=True)))

    def _add(self, item: _Item, points: list[Point], grow: float = 0.0) -> None:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        item.box = (min(xs) - grow, min(ys) - grow, max(xs) + grow, max(ys) + grow)
        for i in range(math.floor((min(xs) - grow) / CELL_MM), math.floor((max(xs) + grow) / CELL_MM) + 1):
            for j in range(math.floor((min(ys) - grow) / CELL_MM), math.floor((max(ys) + grow) / CELL_MM) + 1):
                self.cells[(i, j)].append(item)

    def add_trace(self, net: str, layer: int, width: float, path: list[Point], arcs: list[bool] | None = None) -> None:
        """A trace's pieces, each marked by ``arcs`` as a chord of an arc or not; unmarked, each counts as one."""
        for index, (a, b) in enumerate(zip(path, path[1:], strict=False)):
            arc = arcs[index] if arcs is not None and index < len(arcs) else True
            self._add(_Item("trace", layer, net, (a, b, width), arc=arc), [a, b], width / 2)
        for end in (path[0], path[-1]) if path else ():
            self.ends[(math.floor(end[0] / CELL_MM), math.floor(end[1] / CELL_MM))].append((net, end))

    def covered_end(self, at: Point, net: str, radius: float) -> Point | None:
        """The end of a trace of ``net`` that the plan draws and that a via of ``net`` at ``at``
        would cover off its centre: drawing runs such an end on to the centre (``holder``)."""
        for i in range(math.floor((at[0] - radius) / CELL_MM), math.floor((at[0] + radius) / CELL_MM) + 1):
            for j in range(math.floor((at[1] - radius) / CELL_MM), math.floor((at[1] + radius) / CELL_MM) + 1):
                for owner, end in self.ends.get((i, j), ()):
                    if owner == net and in_pad_box(end, at, radius) and math.dist(end, at) >= END_SNAP_MM:
                        return end
        return None

    def covers_end(self, at: Point, net: str, radius: float) -> bool:
        return self.covered_end(at, net, radius) is not None

    def holder(self, end: Point, net: str, layer: int) -> tuple[Point, tuple[float, float, float, float]] | None:
        """The centre and extents of a via of ``net``, or of a pad of ``net`` on ``layer``, that
        ``end`` lies on: drawing runs the end on to that centre (the pad's middle stands for its pin's
        origin), at the trace's width."""
        for item in self._near([end], REACH_MM):
            if item.net != net:
                continue
            if item.kind == "via":
                x, y, radius = item.geometry
                centre, box = (x, y), (x - radius, y - radius, x + radius, y + radius)
            elif item.kind == "pad" and item.layer == layer and item.geometry:
                xs, ys = [p[0] for p in item.geometry], [p[1] for p in item.geometry]
                box = (min(xs), min(ys), max(xs), max(ys))
                centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            else:
                continue
            if box[0] - 1e-6 <= end[0] <= box[2] + 1e-6 and box[1] - 1e-6 <= end[1] <= box[3] + 1e-6:
                return centre, box
        return None

    def add_via(self, net: str, at: Point, radius: float) -> None:
        self._add(_Item("via", None, net, (at[0], at[1], radius)), [at], radius)

    def _near(self, points: list[Point], reach: float) -> list[_Item]:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        seen: dict[int, _Item] = {}
        for i in range(math.floor((min(xs) - reach) / CELL_MM), math.floor((max(xs) + reach) / CELL_MM) + 1):
            for j in range(math.floor((min(ys) - reach) / CELL_MM), math.floor((max(ys) + reach) / CELL_MM) + 1):
                for item in self.cells.get((i, j), ()):
                    seen[id(item)] = item
        return list(seen.values())

    def _in_hole(self, point: Point) -> bool:
        """Whether a point lies inside a cutout; one near a cutout's edge is found by the edge."""
        return any(x0 <= point[0] <= x1 and y0 <= point[1] <= y1 and outline.encloses(point) for (x0, y0, x1, y1), outline in self.holes)

    def _within(self, point: Point, reach: float) -> bool:
        border = self.border
        if border is None:
            return True
        return border.near(point, reach + TP.BORDER_TOUCH_MM) >= reach + TP.BORDER_TOUCH_MM and border.encloses(point)

    def piece_clear(self, a: Point, b: Point, net: str, layer: int, width: float, slack: float = TP.CLEARANCE_TOLERANCE,
                    sag: float = 0.0, border: bool = True) -> bool:
        """Whether a piece of trace lies inside the border and clears every other net's copper on its
        layer, short of the rules by at most ``slack``: a guard, concentric with the lanes beside it,
        is tested as chords; a straight join, tested as it is drawn, takes none. ``sag`` widens
        the chords of other traces' arcs by as much as the arcs lie off them, for a piece that is not
        concentric with them. Without ``border``, the route border does not count, as for a pour,
        which keeps to its own shape."""
        # both ends inside and the whole piece clear of the border's line: a long piece, such as a tie across a bend
        # zone, may leave the border between its ends where the outline turns
        if border and not (self._within(a, width / 2) and self._within(b, width / 2) and
                           (self.border is None or
                            self.border.piece_near(a, b, width / 2 + TP.BORDER_TOUCH_MM) >= width / 2 + TP.BORDER_TOUCH_MM)):
            return False
        if self.holes and (self._in_hole(a) or self._in_hole(b)):
            return False
        if border and self._in_keepout(a, b, layer, width / 2):
            return False
        x0, x1, y0, y1 = min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])
        # the tolerance is for copper concentric with the piece; against a pad or a via, whose shape is exact, a piece
        # tested with it is a chord of an arc that may lie up to TP.CHORD_MM nearer
        exact, own = min(slack, JOIN_SLACK_MM), (TP.CHORD_MM if slack > JOIN_SLACK_MM else 0.0)
        for item in self._near([a, b], REACH_MM + width / 2):
            if item.net == net or (item.layer is not None and item.layer != layer):
                continue
            bx0, by0, bx1, by1 = item.box
            if bx0 > x1 + REACH_MM or bx1 < x0 - REACH_MM or by0 > y1 + REACH_MM or by1 < y0 - REACH_MM:
                continue  # too far to matter whatever the rule
            if item.kind == "cutout":
                if TP._segment_distance(a, b, *item.geometry) < CUTOUT_CLEARANCE_MM + width / 2:
                    return False
            elif item.kind == "pad":
                need = self.need(net, item.net, layer, "trace_to_pad") + width / 2 + own
                if TP._segment_polygon(a, b, item.geometry) < need - exact:
                    return False
            elif item.kind == "trace":
                c, d, other = item.geometry
                need = self.need(net, item.net, layer, "trace_to_trace") + (width + other) / 2 + (sag if item.arc else 0.0)
                if TP._segment_distance(a, b, c, d) < need - slack:
                    return False
            else:
                x, y, radius = item.geometry
                need = self.need(net, item.net, layer, "trace_to_via") + width / 2 + radius + own
                if point_segment((x, y), a, b) < need - exact:
                    return False
        return True

    def piece_blockers(self, a: Point, b: Point, net: str, layer: int, width: float,
                       sag: float = 0.0) -> list[tuple[str, float, float]]:
        """What a piece of trace comes closer to than the rules allow: each blocker's name (its net,
        or the board's edge or a cutout), the clearance the piece leaves it and the clearance needed."""
        out: list[tuple[str, float, float]] = []
        if self.border is not None:
            for p in (a, b):
                if not self._within(p, width / 2):
                    out.append(("the board's edge", round(self.border.near(p, REACH_MM) - width / 2, 4), 0.0))
        if self.holes and (self._in_hole(a) or self._in_hole(b)):
            out.append(("a cutout", round(-width / 2, 4), CUTOUT_CLEARANCE_MM))
        for item in self._near([a, b], REACH_MM + width / 2):
            if item.net == net or (item.layer is not None and item.layer != layer):
                continue
            if item.kind == "cutout":
                gap, need = TP._segment_distance(a, b, *item.geometry) - width / 2, CUTOUT_CLEARANCE_MM
                name = "a cutout"
            elif item.kind == "pad":
                gap, need = TP._segment_polygon(a, b, item.geometry) - width / 2, self.need(net, item.net, layer, "trace_to_pad")
                name = item.net
            elif item.kind == "trace":
                c, d, other = item.geometry
                gap = TP._segment_distance(a, b, c, d) - (width + other) / 2 - (sag if item.arc else 0.0)
                need, name = self.need(net, item.net, layer, "trace_to_trace"), item.net
            else:
                x, y, radius = item.geometry
                gap = point_segment((x, y), a, b) - width / 2 - radius
                need, name = self.need(net, item.net, layer, "trace_to_via"), item.net
            if gap < need - JOIN_SLACK_MM:
                out.append((name, round(gap, 4), round(need, 4)))
        return out

    def _under_part(self, at: Point, radius: float) -> bool:
        """Whether a via of ``radius`` at ``at`` reaches under a part's placement outline."""
        for body, (x0, y0, x1, y1) in zip(self.bodies, self.body_boxes, strict=True):
            if at[0] < x0 - radius or at[0] > x1 + radius or at[1] < y0 - radius or at[1] > y1 + radius:
                continue
            if inside(at, body) or min(point_segment(at, body[i], body[(i + 1) % len(body)]) for i in range(len(body))) < radius:
                return True
        return False

    def via_blockers(self, at: Point, net: str, radius: float) -> list[tuple[str, float, float]]:
        """What a via at ``at`` comes closer to than the rules allow, on any layer: each blocker's name,
        the clearance left and the clearance needed."""
        out: list[tuple[str, float, float]] = []
        if self._under_part(at, radius):
            out.append(("a part's placement outline", round(-radius, 4), 0.0))
        if not self._within(at, radius):
            edge = min(self.border.near(at, REACH_MM), REACH_MM) if self.border is not None else 0.0
            enclosed = self.border is None or self.border.encloses(at)
            out.append(("the board's edge", round((edge if enclosed else -edge) - radius, 4), 0.0))
        if self.holes and self._in_hole(at):
            out.append(("a cutout", round(-radius, 4), CUTOUT_CLEARANCE_MM))
        for item in self._near([at], REACH_MM + radius):
            if item.kind == "cutout":
                gap, need, name = point_segment(at, *item.geometry) - radius, CUTOUT_CLEARANCE_MM, "a cutout"
            elif item.kind == "pad":
                poly = item.geometry
                distance = 0.0 if len(poly) >= 3 and inside(at, poly) else min(
                    point_segment(at, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))
                gap, need, name = distance - radius, self.need(net, item.net, item.layer, "pad_to_via"), item.net
            elif item.kind == "trace":
                if item.net == net:
                    continue
                c, d, other = item.geometry
                gap = point_segment(at, c, d) - radius - other / 2
                need, name = self.need(net, item.net, item.layer, "trace_to_via"), item.net
            else:
                x, y, other = item.geometry
                gap = math.hypot(at[0] - x, at[1] - y) - radius - other
                need, name = self.need(net, item.net, 1, "via_to_via"), item.net
            if gap < need + VIA_MARGIN_MM:
                out.append((name, round(gap, 4), round(need, 4)))
        return out

    def need(self, net: str, other: str, layer: Any, kind: str) -> float:
        """The clearance the rules ask between two nets, looked up once for each pair, layer and kind."""
        key = (net, other, layer, kind)
        if key not in self._needs:
            self._needs[key] = self.rules.clearance(net, other, layer, kind)
        return self._needs[key]

    def _in_keepout(self, a: Point, b: Point, layer: int | None, reach: float) -> bool:
        """Whether a piece of trace on ``layer`` (or a via, ``layer`` None, at ``a``) comes within ``reach`` of a
        keepout that keeps it out: one of traces on its layer, or one of vias on any layer."""
        for kind, keep_layer, poly, (x0, y0, x1, y1) in self.keepouts:
            if layer is None and kind not in TP.VIA_KEEPOUTS or layer is not None and (kind not in TP.TRACE_KEEPOUTS or keep_layer != layer):
                continue
            if max(a[0], b[0]) < x0 - reach or min(a[0], b[0]) > x1 + reach or max(a[1], b[1]) < y0 - reach or min(a[1], b[1]) > y1 + reach:
                continue
            if TP._segment_polygon(a, b, poly) < reach - 1e-9:
                return True
        return False

    def via_clear(self, at: Point, net: str, radius: float, items: list[_Item] | None = None) -> bool:
        """Whether a via lies inside the border, under no part, and clears every pad (its own net's too),
        every other net's trace and every via, on every layer. ``items`` may hold the copper near it already
        (``_near`` of a point within reach), for several spots tried around one point."""
        if not self._within(at, radius) or (self.holes and self._in_hole(at)):
            return False
        if self._in_keepout(at, at, None, radius):
            return False
        if self._under_part(at, radius):
            return False  # under a part, which Layout's DRC reports
        for item in self._near([at], REACH_MM + radius) if items is None else items:
            if item.kind == "cutout":
                if point_segment(at, *item.geometry) < CUTOUT_CLEARANCE_MM + radius + VIA_MARGIN_MM:
                    return False
            elif item.kind == "pad":
                need = self.need(net, item.net, item.layer, "pad_to_via") + radius
                poly = item.geometry
                distance = 0.0 if len(poly) >= 3 and inside(at, poly) else min(
                    point_segment(at, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))
                if distance < need + VIA_MARGIN_MM:
                    return False
            elif item.kind == "trace":
                c, d, other = item.geometry
                if item.net == net:
                    continue
                need = self.need(net, item.net, item.layer, "trace_to_via") + radius + other / 2
                if point_segment(at, c, d) < need + VIA_MARGIN_MM:
                    return False
            else:
                x, y, other = item.geometry
                need = self.need(net, item.net, 1, "via_to_via") + radius + other
                if math.hypot(at[0] - x, at[1] - y) < need + VIA_MARGIN_MM:
                    return False
        return True


# ------------------------------------------------------------------ the stretches that clear, and their vias


def _pieces(line: list[Point], step: float) -> list[tuple[Point, Point]]:
    out = []
    for a, b in zip(line, line[1:], strict=False):
        length = math.dist(a, b)
        count = max(1, math.ceil(length / step - 1e-9))
        for k in range(count):
            p = (a[0] + (b[0] - a[0]) * k / count, a[1] + (b[1] - a[1]) * k / count)
            q = (a[0] + (b[0] - a[0]) * (k + 1) / count, a[1] + (b[1] - a[1]) * (k + 1) / count)
            out.append((p, q))
    return out


def _simplified(points: list[Point]) -> list[Point]:
    """The points without those on a straight run between their neighbours."""
    out = [points[0]]
    for i in range(1, len(points) - 1):
        a, p, b = out[-1], points[i], points[i + 1]
        if abs((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) > 1e-9 * max(1.0, math.dist(a, b)):
            out.append(p)
    out.append(points[-1])
    return out


def runs(line: list[Point], net: str, layer: int, width: float, obstacles: Obstacles, min_run: float,
         closed: bool = False) -> list[list[Point]]:
    """The stretches of a line that clear other nets' copper, each at least ``min_run`` long."""
    stretches: list[list[Point]] = []
    current: list[Point] = []
    for a, b in _pieces(line, STEP_MM):
        if obstacles.piece_clear(a, b, net, layer, width):
            if not current:
                current = [a]
            current.append(b)
        elif current:
            stretches.append(current)
            current = []
    if current:
        if closed and stretches and stretches[0][0] == line[0]:
            stretches[0] = current + stretches[0][1:]  # a ring's stretch through its start is one
        else:
            stretches.append(current)
    kept = []
    for stretch in stretches:
        if sum(math.dist(p, q) for p, q in zip(stretch, stretch[1:], strict=False)) >= min_run - 1e-9:
            kept.append(_simplified(stretch))
    return kept


class Walker:
    """A line's places by their distance along it, found by bisection: looking for a via spot every
    few hundredths of a millimetre along a line of thousands of chords, walking it from its start
    each time cost the most of a plan's check."""

    def __init__(self, points: list[Point]) -> None:
        self.points = points
        self.marks = [0.0]
        for a, b in zip(points, points[1:], strict=False):
            self.marks.append(self.marks[-1] + math.dist(a, b))

    @property
    def length(self) -> float:
        return self.marks[-1]

    def _piece(self, distance: float) -> int:
        """The first piece whose end lies at or past ``distance``, as ``_at_length`` walks to it."""
        index = bisect.bisect_left(self.marks, distance - 1e-12, 1) - 1
        return max(0, min(index, len(self.points) - 2))

    def at(self, distance: float) -> Point:
        if len(self.points) < 2 or distance >= self.length:
            return self.points[-1]
        i = self._piece(distance)
        a, b = self.points[i], self.points[i + 1]
        length = self.marks[i + 1] - self.marks[i]
        t = (distance - self.marks[i]) / length if length else 0.0
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t

    def direction(self, distance: float) -> Point:
        i = self._piece(distance) if distance <= self.length else len(self.points) - 2
        while i < len(self.points) - 2 and self.marks[i + 1] - self.marks[i] <= 0:
            i += 1
        a, b = self.points[i], self.points[i + 1]
        length = math.dist(a, b) or 1.0
        return (b[0] - a[0]) / length, (b[1] - a[1]) / length


def _at_length(points: list[Point], distance: float) -> Point:
    for a, b in zip(points, points[1:], strict=False):
        length = math.dist(a, b)
        if distance <= length:
            t = distance / length if length else 0.0
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        distance -= length
    return points[-1]


def _direction(points: list[Point], distance: float) -> Point:
    for a, b in zip(points, points[1:], strict=False):
        length = math.dist(a, b)
        if distance <= length and length:
            return (b[0] - a[0]) / length, (b[1] - a[1]) / length
        distance -= length
    a, b = points[-2], points[-1]
    length = math.dist(a, b) or 1.0
    return (b[0] - a[0]) / length, (b[1] - a[1]) / length


def end_vias(stretch: list[Point], net: str, radius: float, reach: float, obstacles: Obstacles,
             bends: list[tuple[float, float, float, float]], aside: list[float] | None = None,
             left: bool = True) -> tuple[list[Point], list[Point]]:
    """A via at each end of a stretch, at the first spot within ``reach`` of the end where one
    clears all copper and stands outside the bend zones, and the stretch cut back to end on it: a
    trace end that touches nothing is a hanger to DRC. ``aside`` and ``left`` move the via off the
    stretch as ``vias_along`` does. An end with no spot stays as it is."""
    total = sum(math.dist(p, q) for p, q in zip(stretch, stretch[1:], strict=False))
    placed: list[Point] = []
    start, end = 0.0, total
    side = 1.0 if left else -1.0
    for from_start in (True, False):
        at, found = 0.0, False
        while at <= min(reach, total / 2) + 1e-9 and not found:
            where = at if from_start else total - at
            base, (ux, uy) = _at_length(stretch, where), _direction(stretch, where)
            for off in aside or [0.0]:
                spot = (round(base[0] - side * uy * off, 4), round(base[1] + side * ux * off, 4))
                if in_bend(spot, radius, bends):
                    continue
                if obstacles.via_clear(spot, net, radius):
                    obstacles.add_via(net, spot, radius)
                    placed.append(spot)
                    if from_start:
                        start = at
                    else:
                        end = total - at
                    found = True
                    break
            at += VIA_SHIFT_MM
    trimmed = cut(stretch, start, end) if (start, end) != (0.0, total) else stretch
    return (trimmed if len(trimmed) >= 2 else stretch), placed


def contacts(points: list[Point], widths: list[float], p: Point, radius: float) -> list[float]:
    """Where along ``points`` a via at ``p`` touches the line, its pad of ``radius`` on the copper of
    a piece ``widths`` wide: one place for each stretch of the line it touches, the nearest point of
    that stretch. A line that runs back past the via, such as a ring's end joined back to a via on the
    ring, meets it twice."""
    spans: list[tuple[float, float, float, float]] = []  # each touching stretch's start, end, least distance and its place
    walked = 0.0
    for a, b, width in zip(points, points[1:], widths, strict=False):
        reach = width / 2 + radius
        dx, dy = b[0] - a[0], b[1] - a[1]
        ax, ay = a[0] - p[0], a[1] - p[1]
        length2 = dx * dx + dy * dy
        length = math.sqrt(length2)
        if length2 > 0:
            half_b, c = ax * dx + ay * dy, ax * ax + ay * ay - reach * reach
            disc = half_b * half_b - length2 * c
            if disc >= 0:
                root = math.sqrt(disc)
                low, high = max(0.0, (-half_b - root) / length2), min(1.0, (-half_b + root) / length2)
                if low <= high:
                    t = min(max(-half_b / length2, low), high)
                    nearest = (math.hypot(ax + t * dx, ay + t * dy), walked + t * length)
                    if spans and walked + low * length - spans[-1][1] <= 1e-9:
                        start, _end, *best = spans[-1]
                        spans[-1] = (start, walked + high * length, *min(tuple(best), nearest))
                    else:
                        spans.append((walked + low * length, walked + high * length, *nearest))
        walked += length
    return [span[3] for span in spans]


def length_of(points: list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:], strict=False))


def clear_of_ends(total: float, radius: float, width: float, end_width: float | None = None) -> tuple[float, float]:
    """How far along a line ``total`` long a via may stand without its pad covering either end of
    the line, its first piece ``width`` wide and its last ``end_width``: Layout runs a trace end that
    lies in a via's pad on to the via's centre, at the trace's width, copper that may come too near
    other nets."""
    return radius + width / 2, total - radius - (width if end_width is None else end_width) / 2


def vias_along(stretch: list[Point], net: str, radius: float, pitch: float, obstacles: Obstacles,
               bends: list[tuple[float, float, float, float]], aside: list[float] | None = None, left: bool = True,
               width: float | list[float] = 0.0, half_ends: bool = True, joints: list[float] | None = None) -> list[Point]:
    """Vias along a stretch no more than ``pitch`` apart wherever a via fits: each one at the
    farthest spot within ``pitch`` (less ``PITCH_SPARE_MM``) of the one before where a via clears all
    copper and stands outside the bend zones, the first and last within half that of the stretch's
    ends, so that the next stretch's vias keep the pitch across the joint. Where none fits within the
    pitch, the next goes at the first spot that does. A via of the net already on the stretch, its
    pad on the stretch's copper ``width`` wide (or each piece's width, listed), counts as one placed
    (``contacts``). No pad covers either end of the stretch (``clear_of_ends``), nor, off its centre,
    a place of ``joints`` along it where one trace ends and the next begins, as pcb check finds them. Without ``half_ends`` the first and last vias
    may stand a whole pitch from the ends, as pcb check measures a line that ends on a pin or a via. ``aside`` lists distances to try the via off the stretch, to its left (or right),
    such as a ring's vias moved inwards off a route border, the pad still on the ring's copper."""
    total = sum(math.dist(p, q) for p, q in zip(stretch, stretch[1:], strict=False))
    widths = list(width) if isinstance(width, list) else [width] * (len(stretch) - 1)
    low, high = clear_of_ends(total, radius, widths[0], widths[-1])
    if high < low:
        return []
    step = pitch - PITCH_SPARE_MM
    side = 1.0 if left else -1.0
    standing = sorted({at for item in obstacles._near(stretch, max(widths) / 2 + radius) if item.kind == "via" and item.net == net
                       for x, y, r in [item.geometry] for at in contacts(stretch, widths, (x, y), r)})
    placed: list[Point] = []
    offsets = aside or [0.0]
    spread = max(abs(off) for off in offsets)
    walker = Walker(stretch)
    seams = [walker.at(joint) for joint in joints or []]  # a pad over one, off its centre, has Layout run that end on to it

    def fits(at: float) -> Point | None:
        base, (ux, uy) = walker.at(at), walker.direction(at)
        items = obstacles._near([base], REACH_MM + radius + spread)  # the copper near every spot tried here
        for off in offsets:
            point = (round(base[0] - side * uy * off, 4), round(base[1] + side * ux * off, 4))
            if in_bend(point, radius, bends):
                continue
            if any(in_pad_box(seam, point, radius) and math.dist(seam, point) >= END_SNAP_MM for seam in seams):
                continue
            if obstacles.via_clear(point, net, radius, items) and not obstacles.covers_end(point, net, radius):
                return point
        return None

    lead = step / 2 if half_ends else step  # the farthest the first and last vias stand from the ends
    prev = lead - step  # as if a via stood that far before the start
    while total - prev > lead + 1e-9:
        reach = min(prev + step, total) if prev >= 0 or not half_ends else min(step / 2, total / 2)
        already = [at for at in standing if prev < at <= reach + 1e-9]
        if already:
            prev = already[-1]
            continue
        target = min(max(reach, low), high)
        if target <= prev + 1e-9:  # the rest is too near the stretch's end for a via
            break
        spot, at = None, target
        floor = max(prev + 1e-9 if prev >= 0 else -1e-9, low - 1e-9)
        while at > floor and spot is None:  # back from the pitch towards the via before
            spot = fits(at)
            at = at if spot is not None else round(at - LAY_STEP_MM, 6)
        if spot is None:  # no room within the pitch: the first spot beyond it, short of a via standing further on
            ahead = next((s for s in standing if s > target + 1e-9), None)
            at = round(target + LAY_STEP_MM, 6)
            while at < (high + 1e-9 if ahead is None else ahead) and spot is None:
                spot = fits(at)
                at = at if spot is not None else round(at + LAY_STEP_MM, 6)
            if spot is None:
                if ahead is None:
                    break
                prev = ahead
                continue
        obstacles.add_via(net, spot, radius)
        placed.append(spot)
        prev = at
    return placed


@dataclass
class Chain:
    """A guard's or ring's line twice: as points with the radii of the arcs that round its corners,
    the way it is drawn, so that it keeps the same distance as the lanes beside it through every
    turn; and as short chords, ``line``, to test it against other copper. ``spans`` gives, for each
    corner, where along ``line`` its arc starts and ends (one place for a sharp corner)."""

    points: list[Point]
    radii: list[float]
    line: list[Point]
    spans: dict[int, tuple[float, float]]

    @property
    def length(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.line, self.line[1:], strict=False))


def make_chain(points: list[Point], radii: list[float]) -> Chain:
    arcs = TP._corners(points, radii)
    line: list[Point] = [points[0]]
    walked = 0.0
    spans: dict[int, tuple[float, float]] = {}

    def to(point: Point) -> None:
        nonlocal walked
        if math.dist(point, line[-1]) > 1e-9:
            walked += math.dist(point, line[-1])
            line.append(point)

    for i in range(1, len(points) - 1):
        arc = arcs[i]
        if arc is None:
            to(points[i])
            spans[i] = (walked, walked)
            continue
        start, centre, end, r, ccw = arc
        to(start)
        begin = walked
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
        sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
        step = 2 * math.acos(max(-1.0, 1 - CHORD_MM / r)) if r > CHORD_MM else math.pi / 2
        count = max(1, math.ceil(abs(sweep) / step))
        for k in range(1, count + 1):
            to((centre[0] + r * math.cos(a0 + sweep * k / count), centre[1] + r * math.sin(a0 + sweep * k / count)))
        spans[i] = (begin, walked)
    to(points[-1])
    return Chain(points, radii, line, spans)


def _fitted_chain(corners: list[tuple[Point, float]]) -> Chain:
    points = [c[0] for c in corners]
    radii = _fitted(points, [0.0, *[c[1] for c in corners[1:-1]], 0.0], [False, *[True] * (len(points) - 2), False])
    return make_chain(points, radii)


def guard_chain(lane: Lane, offset: float, ring: list[tuple[Point, float]], jogs: list[tuple[Point, float, float]] | None = None,
                forward: bool = True) -> Chain:
    """A guard line: the offset of the lane's edge at ``offset``, moved where ``jogs`` say as a lane
    is (``forward``: as its lanes run, with the edge's chain or against it), the whole length of the
    edge, in the chain's direction."""
    chain, left = edge_chain(ring, lane.edge)
    if not jogs:
        return _fitted_chain(offset_chain(chain, offset, left))
    corners = _jogged(replace(lane, offset=offset, jogs=list(jogs)), chain, left, forward, "a guard")
    return _fitted_chain(corners if forward else corners[::-1])


def ring_chain(ring: list[tuple[Point, float]], offset: float) -> tuple[Chain, bool]:
    """The outline moved ``offset`` inwards, all the way round, closed: its first point is its
    last, in the middle of a straight side; and whether the board's inside lies on its left."""
    points = [c[0] for c in ring]
    count = len(points)
    area = sum(points[i][0] * points[(i + 1) % count][1] - points[(i + 1) % count][0] * points[i][1] for i in range(count)) / 2
    longest = max(range(count), key=lambda i: math.dist(points[i], points[(i + 1) % count]) if not ring[i][1] and not ring[(i + 1) % count][1]
                  else 0.0)
    a, b = points[longest], points[(longest + 1) % count]
    middle = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    corners = [(middle, 0.0)] + [ring[(longest + 1 + k) % count] for k in range(count)] + [(middle, 0.0)]
    chain = _fitted_chain(offset_chain(corners, offset, area > 0))
    chain.points[-1] = chain.points[0]
    chain.line[-1] = chain.line[0]
    return chain, area > 0


def in_arc(chain: Chain, at: float) -> bool:
    return any(begin + 1e-9 < at < end - 1e-9 for begin, end in chain.spans.values())


def snapped(chain: Chain, start: float, end: float) -> tuple[float, float]:
    """A stretch with an end that falls inside an arc moved back to where the arc meets the straight
    run, so that the stretch is drawn with whole arcs."""
    for begin, finish in chain.spans.values():
        if begin + 1e-9 < start < finish - 1e-9:
            start = finish
        if begin + 1e-9 < end < finish - 1e-9:
            end = begin
    return start, end


def drawn(chain: Chain, start: float, end: float) -> tuple[list[Point], list[float]]:
    """The points and corner radii of the stretch from ``start`` to ``end``, ends on straight runs."""
    points: list[Point] = [_at_length(chain.line, start)]
    radii: list[float] = [0.0]
    for index in sorted(chain.spans):
        begin, finish = chain.spans[index]
        if finish - begin > 1e-9:  # an arc, whole within the stretch: a stretch may start or end where it does
            inside = start - 1e-9 <= begin and finish <= end + 1e-9
        else:  # a sharp corner, between the ends
            inside = start + 1e-9 < begin < end - 1e-9
        if inside:
            points.append(chain.points[index])
            radii.append(chain.radii[index])
    points.append(_at_length(chain.line, end))
    radii.append(0.0)
    return points, radii


def spans(chain: Chain, net: str, layer: int, width: float, obstacles: Obstacles, min_run: float,
          closed: bool = False) -> list[tuple[float, float]]:
    """Where along the line it clears other nets' copper, each stretch at least ``min_run`` long,
    its ends on straight runs. A closed line's stretch through its start comes as two, which meet there."""
    found: list[list[float]] = []
    at = 0.0
    for a, b in _pieces(chain.line, STEP_MM):
        length = math.dist(a, b)
        if obstacles.piece_clear(a, b, net, layer, width):
            if found and abs(found[-1][1] - at) < 1e-9:
                found[-1][1] = at + length
            else:
                found.append([at, at + length])
        at += length
    out = [snapped(chain, s, e) for s, e in found]
    out = [(s, e) for s, e in out if e > s + 1e-9]
    joined = closed and len(out) >= 2 and out[0][0] < 1e-9 and out[-1][1] > chain.length - 1e-9
    kept = []
    for index, (start, end) in enumerate(out):
        size = end - start
        if joined and index in (0, len(out) - 1):  # the two halves of the stretch through a closed line's start
            size = out[0][1] - out[0][0] + out[-1][1] - out[-1][0]
        if size >= min_run - 1e-9:
            kept.append((start, end))
    return kept


def end_spots(chain: Chain, start: float, end: float, net: str, radius: float, reach: float, obstacles: Obstacles,
              bends: list[tuple[float, float, float, float]], aside: list[float] | None = None, left: bool = True,
              ends: tuple[bool, bool] = (True, True), layer: int | None = None, width: float = 0.0,
              thin: float | None = None) -> tuple[float, float, list[Point], tuple[bool, bool]]:
    """A via at each end of a stretch named in ``ends``, at the first spot within ``reach`` of it on a
    straight run where one clears all copper and stands outside the bend zones, and the stretch cut
    back to end on it: a trace end that touches nothing is a hanger to DRC. ``aside`` and ``left``
    move the via off the line as ``vias_along`` does; given the ``layer``, the piece from the line's
    end to a via moved off it must clear too, at the line's ``width`` where the end lies on the via
    (drawing runs it on to the centre) and else at ``thin``, the width of the stub that joins them.
    An end with no spot stays where it is."""
    placed: list[Point] = []
    done = [False, False]
    side = 1.0 if left else -1.0
    for number, (from_start, wanted) in enumerate(zip((True, False), ends, strict=True)):
        if not wanted:
            continue
        at, found = 0.0, False
        while at <= min(reach, (end - start) / 2) + 1e-9 and not found:
            where = start + at if from_start else end - at
            if not in_arc(chain, where):
                base, (ux, uy) = _at_length(chain.line, where), _direction(chain.line, where)
                for off in aside or [0.0]:
                    spot = (round(base[0] - side * uy * off, 4), round(base[1] + side * ux * off, 4))
                    if in_bend(spot, radius, bends):
                        continue
                    if not obstacles.via_clear(spot, net, radius) or obstacles.covers_end(spot, net, radius):
                        continue
                    if layer is not None and math.dist(base, spot) >= END_SNAP_MM:
                        piece = width if in_pad_box(base, spot, radius) else (width if thin is None else thin)
                        if not _path_clear([base, spot], net, layer, piece, obstacles):
                            continue
                    obstacles.add_via(net, spot, radius)
                    placed.append(spot)
                    found = True
                    break
            if found:
                done[number] = True
                if from_start:
                    start = where
                else:
                    end = where
                break
            at += VIA_SHIFT_MM
    return start, end, placed, (done[0], done[1])


JOIN_REACH_MM = 4.0  # a free end looks this far for copper of its net to join
JOIN_SLACK_MM = 1e-7  # a join, drawn as it is tested, keeps the rules but for rounding
MAZE_STEP_MM = 0.05  # a join no straight path makes is searched on a grid this fine, laid from the end
JOIN_STRAIGHT = 8  # a free end tries straight paths to this many of the nearest targets and as many most in line with it
WALL_CHORD_MM = 1e-4  # a path beside a signal is offset from its arcs taken as chords this close: off chords of 2 um it fell 1.7 um short
MAZE_NODES = 2000  # the grid points one search may expand
_DIRECTIONS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def in_bend(point: Point, radius: float, bends: list[tuple[float, float, float, float]]) -> bool:
    """Whether a via of ``radius`` at ``point`` comes nearer a bend zone than ``board_metrics.BEND_VIA_MARGIN_MM``."""
    reach = radius + board_metrics.BEND_VIA_MARGIN_MM
    return any(x0 - reach < point[0] < x1 + reach and y0 - reach < point[1] < y1 + reach for x0, y0, x1, y1 in bends)


CUT_BACK_MM = 2.0  # a line's end is cut back at most this far to leave a join without a hairpin
CUT_STEP_MM = 0.1  # in steps of this much


def heads_back(outward: Point, path: list[Point]) -> bool:
    """Whether a join leaves its end turning back over the line, at a sharper corner than square."""
    if len(path) < 2:
        return False
    first = (path[1][0] - path[0][0], path[1][1] - path[0][1])
    return first[0] * outward[0] + first[1] * outward[1] < -1e-9 * (math.hypot(*first) or 1.0)


def join(end: Point, outward: Point, net: str, layer: int, width: float, obstacles: Obstacles,
         targets: list[Point], reach: float = JOIN_REACH_MM,
         walls: list[tuple[str, float, list[Point], list[bool]]] | None = None) -> list[Point] | None:
    """A path from a free end to the nearest of ``targets`` (pin origins and via centres of its
    net) within ``reach``, that clears other nets' copper: straight, or first on along ``outward``
    (the way the end points) and then straight to the target; failing those, on beside one of
    ``walls`` (the net, width and centre line of other nets' traces near the end, with which of its
    pieces are chords of arcs) at the least distance the rules allow, then straight to a target;
    failing that too, the shortest path a grid search finds. None when none clears.

    A guard between two signals keeps the least clearance from both, and their lines need not run
    square to the grid: a grid step off the line breaks a rule by micrometres, while a path beside
    the signal keeps the rule's distance from it, past its turns to the connector it fans out to,
    whose ground pins and vias stand among the signal's. The signals' lines are as exact as the
    outline they follow, read to the micrometre, so the path beside them, like the guard, may fall
    short of a rule by ``TP.CLEARANCE_TOLERANCE``; the straight piece on to the target keeps it."""
    near = sorted((t for t in targets if 1e-6 < math.dist(end, t) <= reach), key=lambda t: math.dist(end, t))
    if not near:
        return None
    outward = _squared(outward)

    def aside(target: Point) -> float:  # how far a target lies off the end's line, ahead of it; behind it, last
        along = (target[0] - end[0]) * outward[0] + (target[1] - end[1]) * outward[1]
        return abs((target[0] - end[0]) * outward[1] - (target[1] - end[1]) * outward[0]) if along > 0 else math.inf

    tried = list(dict.fromkeys([*near[:JOIN_STRAIGHT], *sorted(near, key=aside)[:JOIN_STRAIGHT]]))  # the nearest, and those in line
    for target in tried:
        along = (target[0] - end[0]) * outward[0] + (target[1] - end[1]) * outward[1]
        paths = [[end, target]]
        if along > 1e-6:
            foot = (end[0] + outward[0] * along, end[1] + outward[1] * along)
            across = math.dist(foot, target)
            paths.append([end, foot, target])
            if along > across + 1e-6:
                # on along the line, then over to the target at 45 degrees; or over first, then on along the target's own line,
                # which passes a gap between other copper that the target stands in line with, such as a connector's vias
                paths.append([end, (end[0] + outward[0] * (along - across), end[1] + outward[1] * (along - across)), target])
                paths.append([end, (end[0] + outward[0] * across + target[0] - foot[0], end[1] + outward[1] * across + target[1] - foot[1]),
                              target])
        for path in paths:
            if _path_clear(path, net, layer, width, obstacles):
                return path
    rows = []
    for other, other_width, line, arcs in walls or []:
        need = obstacles.rules.clearance(net, other, layer, "trace_to_trace") + (width + other_width) / 2
        distance = _closest(line, end)[0]
        rows.append((abs(distance - need), need if abs(distance - need) <= TP.CLEARANCE_TOLERANCE else distance, line, arcs))
    for _off, distance, line, arcs in sorted(rows, key=lambda row: row[0])[:2]:
        beside = _beside(end, outward, line, distance, reach, arcs)
        if len(beside) < 2 or not obstacles.piece_clear(end, beside[0], net, layer, width):
            continue
        travelled = math.dist(end, beside[0])
        done = 0
        for index, (a, b) in enumerate(_pieces(beside, STEP_MM)):
            if not obstacles.piece_clear(a, b, net, layer, width):  # as close as the guard keeps
                break
            travelled += math.dist(a, b)
            done = index + 1
            for target in near:
                if travelled + math.dist(b, target) <= reach and _path_clear([b, target], net, layer, width, obstacles):
                    path = _pulled(_onto(end, _simplified([*_upto(beside, done), target])), net, layer, width, obstacles)
                    if not _hairpin(outward, path):
                        return path
        if done:  # where the line beside stops, such as at the signal's via, a search goes on from it
            start = _upto(beside, done)
            rest = _maze(start[-1], net, layer, width, obstacles, [t for t in near if travelled + math.dist(start[-1], t) <= reach],
                         reach - travelled)
            if rest:
                path = _pulled(_onto(end, _simplified([*start[:-1], *rest])), net, layer, width, obstacles)
                if not _hairpin(outward, path):
                    return path
    path = _maze(end, net, layer, width, obstacles, near, reach)
    return None if path is None or _hairpin(outward, path) else path


def _pulled(path: list[Point], net: str, layer: int, width: float, obstacles: Obstacles) -> list[Point]:
    """The path with every point it can skip left out: from each point on, straight to the farthest
    point a straight piece reaches clear, so a walk beside a wall in short steps and its jogs go."""
    out, i = [path[0]], 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1 and not _path_clear([path[i], path[j]], net, layer, width, obstacles):
            j -= 1
        out.append(path[j])
        i = j
    return out


def _hairpin(outward: Point, path: list[Point]) -> bool:
    """Whether a join turns sharper than square anywhere: off the line's end, or at a corner of its own,
    a corner pcb check lists as acute."""
    ways = [outward, *((b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:], strict=False) if math.dist(a, b) > 1e-9)]
    return any(u[0] * v[0] + u[1] * v[1] < -1e-9 * (math.hypot(*u) * math.hypot(*v) or 1.0) for u, v in zip(ways, ways[1:], strict=False))


def _onto(end: Point, path: list[Point]) -> list[Point]:
    """``path`` from ``end``, which it starts at or a step off."""
    return path if math.dist(end, path[0]) < 1e-9 else [end, *path]


def _closest(line: list[Point], point: Point) -> tuple[float, int, Point]:
    """The distance from ``point`` to a polyline, the segment nearest it and the nearest point."""
    best = (math.inf, 0, line[0])
    for i, (a, b) in enumerate(zip(line, line[1:], strict=False)):
        dx, dy = b[0] - a[0], b[1] - a[1]
        size = dx * dx + dy * dy
        t = 0.0 if size == 0 else max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / size))
        foot = (a[0] + t * dx, a[1] + t * dy)
        distance = math.dist(point, foot)
        if distance < best[0]:
            best = (distance, i, foot)
    return best


def _beside(end: Point, outward: Point, wall: list[Point], distance: float, reach: float, arcs: list[bool] | None = None) -> list[Point]:
    """The line ``distance`` from ``wall`` on the side ``end`` lies, from beside the point of the
    wall nearest ``end`` on the way ``outward`` points, as long as ``reach``: each piece of the wall
    offset, and their offsets met where they cross, so it keeps that distance. A piece ``arcs``
    marks as a chord of an arc is offset ``TP.CHORD_MM`` farther, as far as the board's arcs may
    lie off the chords other paths are tested against. Empty when ``end`` lies on the wall."""
    near, index, foot = _closest(wall, end)
    if near < 1e-9:
        return []
    a, b = wall[index], wall[index + 1]
    marks = arcs if arcs is not None and len(arcs) == len(wall) - 1 else [False] * (len(wall) - 1)
    forward = (b[0] - a[0]) * outward[0] + (b[1] - a[1]) * outward[1] >= 0
    if forward:
        points, flags = [foot, *wall[index + 1:]], marks[index:]
    else:
        points, flags = [foot, *reversed(wall[:index + 1])], list(reversed(marks[:index + 1]))
    run, kinds = [points[0]], []
    for point, flag in zip(points[1:], flags, strict=False):
        if math.dist(point, run[-1]) > 1e-9:
            run.append(point)
            kinds.append(flag)
    if len(run) < 2:
        return []
    first = (run[1][0] - run[0][0], run[1][1] - run[0][1])
    side = 1.0 if first[0] * (end[1] - foot[1]) - first[1] * (end[0] - foot[0]) > 0 else -1.0  # left of the way on
    lines = []
    for (p, q), arc in zip(zip(run, run[1:], strict=False), kinds, strict=True):
        length = math.dist(p, q)
        off = side * (distance + (TP.CHORD_MM if arc else 0.0))
        nx, ny = -(q[1] - p[1]) / length * off, (q[0] - p[0]) / length * off
        lines.append(((p[0] + nx, p[1] + ny), (q[0] + nx, q[1] + ny)))
    out = [lines[0][0]]
    for number, ((p0, p1), (q0, q1)) in enumerate(zip(lines, lines[1:], strict=False)):
        if kinds[number] != kinds[number + 1]:  # where an arc starts or ends, a step across: nearly in line, the offsets would cross far off
            out += [p1, q0]
            continue
        meet = _crossing(p0, p1, q0, q1)
        out.append(meet if meet is not None else p1)
    out.append(lines[-1][1])
    total, kept = 0.0, [out[0]]
    for p in out[1:]:
        step = math.dist(kept[-1], p)
        if total + step >= reach:
            kept.append(_toward(kept[-1], p, reach - total))
            break
        total += step
        kept.append(p)
    return kept


def _crossing(p0: Point, p1: Point, q0: Point, q1: Point) -> Point | None:
    """Where the line through p0, p1 crosses the line through q0, q1; None when they run parallel."""
    rx, ry, sx, sy = p1[0] - p0[0], p1[1] - p0[1], q1[0] - q0[0], q1[1] - q0[1]
    cross = rx * sy - ry * sx
    if abs(cross) < 1e-12:
        return None
    t = ((q0[0] - p0[0]) * sy - (q0[1] - p0[1]) * sx) / cross
    return p0[0] + t * rx, p0[1] + t * ry


def _toward(a: Point, b: Point, distance: float) -> Point:
    length = math.dist(a, b) or 1.0
    return a[0] + (b[0] - a[0]) * distance / length, a[1] + (b[1] - a[1]) * distance / length


def _upto(line: list[Point], count: int) -> list[Point]:
    """The polyline as far as the end of its ``count``-th piece of ``STEP_MM``."""
    pieces = _pieces(line, STEP_MM)[:count]
    return [pieces[0][0], *[b for _a, b in pieces]] if pieces else line[:1]


SQUARE_RAD = 0.01  # a direction this close to a multiple of 45 degrees is taken as that


def _squared(direction: Point) -> Point:
    """``direction`` as the nearest multiple of 45 degrees when it lies that close to it: a lane's
    line follows the outline read to the micrometre, while a connector's pads and vias, which a
    join passes between and ends on, stand square."""
    angle = math.atan2(direction[1], direction[0])
    square = round(angle / (math.pi / 4)) * (math.pi / 4)
    return (math.cos(square), math.sin(square)) if abs(angle - square) <= SQUARE_RAD else direction


def _path_clear(path: list[Point], net: str, layer: int, width: float, obstacles: Obstacles) -> bool:
    """Whether a join's path keeps the rules exactly, other traces' arcs taken where they lie, not as chords."""
    return all(obstacles.piece_clear(a, b, net, layer, width, JOIN_SLACK_MM, TP.CHORD_MM) for a, b in _pieces(path, STEP_MM))


def _maze(end: Point, net: str, layer: int, width: float, obstacles: Obstacles, goals: list[Point], reach: float) -> list[Point] | None:
    """The shortest grid path from ``end`` to one of ``goals`` (A*, eight directions, each step
    tested as drawn), straightened where a straight piece clears; None when the search finds none
    within ``reach`` of the end or ``MAZE_NODES`` points."""
    step = MAZE_STEP_MM
    if not goals:
        return None

    def at(key: tuple[int, int]) -> Point:
        return end[0] + key[0] * step, end[1] + key[1] * step

    def rest(point: Point) -> float:
        return min(math.dist(point, goal) for goal in goals)

    best: dict[tuple[int, int], float] = {(0, 0): 0.0}
    came: dict[tuple[int, int], tuple[int, int]] = {}
    heap = [(rest(end), 0.0, (0, 0))]
    expanded = 0
    while heap:
        _, cost, key = heapq.heappop(heap)
        if cost > best.get(key, math.inf) + 1e-12:
            continue
        point = at(key)
        goal = next((g for g in goals if math.dist(point, g) <= step * 1.5 and _path_clear([point, g], net, layer, width, obstacles)), None)
        if goal is not None:
            keys = [key]
            while keys[-1] in came:
                keys.append(came[keys[-1]])
            return _straightened([at(k) for k in reversed(keys)] + [goal], net, layer, width, obstacles)
        expanded += 1
        if expanded > MAZE_NODES:
            return None
        for dx, dy in _DIRECTIONS:
            nxt_key = (key[0] + dx, key[1] + dy)
            nxt = at(nxt_key)
            more = cost + step * math.hypot(dx, dy)
            if more >= best.get(nxt_key, math.inf) - 1e-12 or math.dist(nxt, end) > reach:
                continue
            if not obstacles.piece_clear(point, nxt, net, layer, width, JOIN_SLACK_MM, TP.CHORD_MM):
                continue
            best[nxt_key] = more
            came[nxt_key] = key
            heapq.heappush(heap, (more + rest(nxt), more, nxt_key))
    return None


def _straightened(path: list[Point], net: str, layer: int, width: float, obstacles: Obstacles) -> list[Point]:
    """The path with each run of points it can skip by a straight piece that clears skipped."""
    out = [path[0]]
    at = 0
    while at < len(path) - 1:
        reach = at + 1
        while reach + 1 < len(path) and _path_clear([path[at], path[reach + 1]], net, layer, width, obstacles):
            reach += 1
        out.append(path[reach])
        at = reach
    return out


# ------------------------------------------------------------------ what blocks a via or a join


BLOCK_STEP_MM = 0.25  # a stretch is looked along this often for the spot a via comes nearest to fitting


def _worst(found: list[tuple[str, float, float]], at: Point) -> dict[str, Any]:
    gap, need = min(((g, n) for _name, g, n in found), key=lambda pair: pair[0] - pair[1])
    return {"at_mm": [round(at[0], 4), round(at[1], 4)], "blocked_by": sorted({name for name, _g, _n in found}),
            "gap_mm": round(gap, 4), "needed_mm": round(need, 4)}


POUR_PROBE_MM = 0.1  # the narrowest pour that can reach a line from its side, tested this wide and twice as long


def open_side(line: list[Point], net: str, layer: int, width: float, obstacles: Obstacles) -> Point | None:
    """A point beside ``line`` where a pour of its net on its layer could reach it: a strip
    ``POUR_PROBE_MM`` wide against it that clears other nets' copper, the route border aside, since
    a pour keeps to its own shape, which on a real board reached a ring between it and the edge.
    None when other copper hems the line in on both sides all along, so that no pour can touch it."""
    total = sum(math.dist(a, b) for a, b in zip(line, line[1:], strict=False))
    at = 0.0
    while at <= total + 1e-9:
        p, (ux, uy) = _at_length(line, at), _direction(line, at)
        at += BLOCK_STEP_MM
        for side in (1.0, -1.0):
            off = width / 2 + POUR_PROBE_MM / 2  # a pour of the line's own net meets it, clear of the other nets
            q = (p[0] - side * uy * off, p[1] + side * ux * off)
            a, b = (q[0] - ux * POUR_PROBE_MM, q[1] - uy * POUR_PROBE_MM), (q[0] + ux * POUR_PROBE_MM, q[1] + uy * POUR_PROBE_MM)
            if obstacles.piece_clear(a, b, net, layer, POUR_PROBE_MM, JOIN_SLACK_MM, border=False):
                return (round(q[0], 4), round(q[1], 4))
    return None


END_PIECE_MM = 1.0  # a pour that meets a trace this near its end joins the end to it


def open_around(line: list[Point], at_start: bool, net: str, layer: int, width: float, obstacles: Obstacles) -> Point | None:
    """A point where a pour of a trace's net on its layer could reach the trace near one of its
    ends: beside its last ``END_PIECE_MM`` (``open_side``), or around the end itself, a strip
    ``POUR_PROBE_MM`` wide against it in one of 16 directions that clears other nets' copper; None
    when other copper closes the end in."""
    run = line if at_start else list(reversed(line))
    total, piece = 0.0, [run[0]]
    for a, b in zip(run, run[1:], strict=False):
        step = math.dist(a, b)
        if total + step >= END_PIECE_MM:
            piece.append(_at_length([a, b], END_PIECE_MM - total))
            break
        total += step
        piece.append(b)
    if len(piece) >= 2:
        side = open_side(piece, net, layer, width, obstacles)
        if side is not None:
            return side
    tip = run[0]
    off = width / 2 + POUR_PROBE_MM / 2
    for k in range(16):
        ux, uy = math.cos(k * math.pi / 8), math.sin(k * math.pi / 8)
        q = (tip[0] + ux * off, tip[1] + uy * off)
        a, b = (q[0] - uy * POUR_PROBE_MM, q[1] + ux * POUR_PROBE_MM), (q[0] + uy * POUR_PROBE_MM, q[1] - ux * POUR_PROBE_MM)
        if obstacles.piece_clear(a, b, net, layer, POUR_PROBE_MM, JOIN_SLACK_MM, border=False):
            return (round(q[0], 4), round(q[1], 4))
    return None


def across(width: float, radius: float) -> list[float]:
    """The offsets a via along a line of ``width`` tries from its centre line, either way: on it,
    then every ``ACROSS_STEP_MM`` out to where the pad still overlaps the line's copper, nearest
    first, so that a line wider than the pad, or one beside open board, finds room the centre line
    has not. Laying and measuring try the same offsets."""
    reach = width / 2 + radius * 0.8
    out = [0.0]
    for k in range(1, math.floor(reach / ACROSS_STEP_MM + 1e-9) + 1):
        out += [k * ACROSS_STEP_MM, -k * ACROSS_STEP_MM]
    out += [reach, -reach]
    return list(dict.fromkeys(round(off, 4) for off in out))


def via_spot(line: list[Point], net: str, radius: float, obstacles: Obstacles,
             bends: list[tuple[float, float, float, float]], aside: list[float] | None = None,
             width: float = 0.0) -> Point | None:
    """The first spot along ``line``, looked at every ``VIA_SHIFT_MM`` and at each of ``aside``
    off it (to its left; negative to its right), where a via fits outside the bend zones, its pad
    off the line's ends (``clear_of_ends``)."""
    walker = Walker(line)
    at, end = clear_of_ends(walker.length, radius, width)
    while at <= end + 1e-9:
        base, (ux, uy) = walker.at(at), walker.direction(at)
        at += VIA_SHIFT_MM
        for off in aside or [0.0]:
            spot = (base[0] - uy * off, base[1] + ux * off)
            if in_bend(spot, radius, bends):
                continue
            if obstacles.via_clear(spot, net, radius) and not obstacles.covers_end(spot, net, radius):
                return (round(spot[0], 4), round(spot[1], 4))
    return None


def why_no_via(line: list[Point], net: str, radius: float, obstacles: Obstacles,
               bends: list[tuple[float, float, float, float]], aside: list[float] | None = None,
               width: float = 0.0) -> dict[str, Any] | None:
    """Where along ``line``, or ``aside`` off it as ``via_spot`` looks, a via comes nearest to
    fitting and what stops it there: the spot, the blockers, and the clearance left against the
    clearance needed; None when a via fits somewhere."""
    best: tuple[float, Point, list[tuple[str, float, float]]] | None = None
    walker = Walker(line)
    at, end = clear_of_ends(walker.length, radius, width)
    while at <= end + 1e-9:
        base, (ux, uy) = walker.at(at), walker.direction(at)
        at += VIA_SHIFT_MM
        for off in aside or [0.0]:
            spot = (base[0] - uy * off, base[1] + ux * off)
            if in_bend(spot, radius, bends):
                continue
            found = obstacles.via_blockers(spot, net, radius)
            covered = obstacles.covered_end(spot, net, radius)
            if covered is not None:  # its pad would cover the end of a trace of its net, which drawing runs on to its centre
                found = [*found, (f"the end of a trace of {net}", round(math.dist(spot, covered), 4), radius)]
            if not found:
                return None
            short = max(need - gap for _name, gap, need in found)
            if best is None or short < best[0]:
                best = (short, spot, found)
    if best is None:
        return {"blocked_by": ["the bend zones"], "reason": "it lies in bend zones, where no via stands"}
    return _worst(best[2], best[1])


def why_no_join(end: Point, outward: Point, net: str, layer: int, width: float, obstacles: Obstacles, targets: list[Point],
                reach: float = JOIN_REACH_MM) -> dict[str, Any]:
    """Why a free end found no path to its net: no target within ``reach``, or, on the straight try
    that got furthest, the first piece that breaks a rule, with its blockers and clearances."""
    near = sorted((t for t in targets if 1e-6 < math.dist(end, t) <= reach), key=lambda t: math.dist(end, t))
    if not near:
        return {"blocked_by": [], "reason": f"no pin or via of {net} within {reach:g} mm"}
    outward = _squared(outward)
    best: tuple[float, Point, list[tuple[str, float, float]]] | None = None
    for target in near[:JOIN_STRAIGHT]:
        along = (target[0] - end[0]) * outward[0] + (target[1] - end[1]) * outward[1]
        paths = [[end, target]]
        if along > 1e-6:
            foot = (end[0] + outward[0] * along, end[1] + outward[1] * along)
            across = math.dist(foot, target)
            paths.append([end, foot, target])
            if along > across + 1e-6:
                paths.append([end, (end[0] + outward[0] * across + target[0] - foot[0], end[1] + outward[1] * across + target[1] - foot[1]),
                              target])
        for path in paths:
            gone = 0.0
            for a, b in _pieces(path, STEP_MM):
                found = obstacles.piece_blockers(a, b, net, layer, width, TP.CHORD_MM)
                if found:
                    if best is None or gone > best[0]:
                        best = (gone, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), found)
                    break
                gone += math.dist(a, b)
    if best is None:
        return {"blocked_by": [], "reason": "every straight path clears, but none reaches a target within reach"}
    return _worst(best[2], best[1])
