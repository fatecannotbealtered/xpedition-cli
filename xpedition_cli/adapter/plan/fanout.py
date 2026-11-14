"""The fan-out of a two-row connector: the row away from the board's body changes layer through vias
staggered between the rows, and every net is given the point where it leaves the connector.

A board-to-board connector has two rows of pads on one side. The row nearer the board's body can
leave on the pad layer straight away; the other row is blocked by it, so its pins go through vias
between the rows to another layer: one via for each run of consecutive pins of one net, which are
joined pin to pin, so no via is left joined on the pad layer alone. With the pins at a fine pitch,
the vias stand in two staggered lines, one per column. A pin of the near row whose net is the far
pin's below it shares that via; a pin of a plane net, such as GND, takes the via of a column whose
far pin needs none. Consecutive pins of one net in a row are joined pin to pin. A near pin whose net runs on
another layer, such as a lane on the top layer leaving a connector on the bottom, changes layer
through a via just past the near row, on the body's side, staggered as the vias between the rows.

A connector set at an angle, such as one turned to follow a slanted head of a flex board, is
fanned out in its own frame, its rows along x, and the result turned back.

Keepouts count: where a keepout of vias, or one of traces on the pad layer, closes the gap
between the rows (a connector whose mating part covers it, say), the far row's vias stand in one
line beyond the far row instead, outside the connector's body, as they do when asked (``beyond``,
where no via may stand under the connector), each run's via moved along the line as little as the vias'
spacing allows, and a stub leaves the pad outwards to it. The near row's pins of a plane net then
reach no via; they are listed as loose.

Coordinates in mm. Pure Python: the adapter reads the pins and pads from Layout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from . import PlanError

Point = tuple[float, float]
Box = tuple[float, float, float, float]
ROW_TOLERANCE = 0.05  # mm two pins' cross coordinates may differ and stay in one row
SQUARE_DEGREES = 0.05  # rows this close to the x or y axis are taken as along it


@dataclass
class Pin:
    number: str
    net: str | None
    at: Point
    pad: Box  # the pad's extents on its layer
    layer: int
    shape: list[Point] | None = None  # the pad's outline, which gives its extents in a turned connector's frame


@dataclass
class Fanout:
    """What a connector's fan-out adds to the plan, and where each net leaves it."""

    traces: list[tuple[str, int, float, list[Point]]] = field(default_factory=list)  # (net, layer, width, points)
    vias: list[tuple[str, Point]] = field(default_factory=list)
    exits: dict[str, list[tuple[Point, int]]] = field(default_factory=dict)  # net -> (point, layer) where it leaves
    loose: list[str] = field(default_factory=list)  # pins the fan-out leaves to the Agent
    toward: Point = (0.0, 1.0)  # the unit direction from the connector towards the board's body
    pad_layer: int = 1
    clear_at: float = 0.0  # along ``toward``, where the near row's pads end
    clear_vias_at: float = 0.0  # along ``toward``, where the vias between the rows end (or the line beyond the far row)
    lifted: list[Point] = field(default_factory=list)  # the vias just past the near row, which a trace beside them clears
    via_radius: float = 0.0


def no_connect(net: str | None) -> bool:
    """A pin without a net: no net, or one Layout names in brackets, such as (Net0)."""
    return not net or (net.startswith("(") and net.endswith(")"))


def centroid(polygon: list[Point]) -> Point:
    area = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area) < 1e-12:
        return sum(p[0] for p in polygon) / len(polygon), sum(p[1] for p in polygon) / len(polygon)
    return cx / (3 * area), cy / (3 * area)


def rows(pins: list[Pin]) -> tuple[int, list[list[Pin]]]:
    """The axis the rows run along (0 for x, 1 for y) and the two rows, each sorted along it; pins
    outside the two longest rows, such as a column at an end, are left out."""
    if len(pins) < 4:
        raise PlanError("E_VALIDATION", "a fan-out needs a connector with two rows of pins")
    xs, ys = [p.at[0] for p in pins], [p.at[1] for p in pins]
    along = 0 if max(xs) - min(xs) >= max(ys) - min(ys) else 1
    across = 1 - along
    groups: list[list[Pin]] = []
    for pin in sorted(pins, key=lambda p: p.at[across]):
        if groups and abs(groups[-1][-1].at[across] - pin.at[across]) <= ROW_TOLERANCE:
            groups[-1].append(pin)
        else:
            groups.append([pin])
    groups.sort(key=len, reverse=True)
    if len(groups) < 2 or len(groups[1]) < 2:
        raise PlanError("E_VALIDATION", "a fan-out needs a connector with two rows of pins")
    return along, [sorted(g, key=lambda p: p.at[along]) for g in groups[:2]]


def row_angle(pins: list[Pin]) -> float:
    """The angle in radians of the line the connector's rows run along, from the spread of its pins;
    0 for rows along x or y."""
    n = len(pins)
    if n < 2:
        return 0.0
    mx, my = sum(p.at[0] for p in pins) / n, sum(p.at[1] for p in pins) / n
    sxx = sum((p.at[0] - mx) ** 2 for p in pins)
    syy = sum((p.at[1] - my) ** 2 for p in pins)
    sxy = sum((p.at[0] - mx) * (p.at[1] - my) for p in pins)
    angle = 0.5 * math.atan2(2 * sxy, sxx - syy)
    off_axis = math.degrees(angle) % 90.0
    return 0.0 if min(off_axis, 90.0 - off_axis) <= SQUARE_DEGREES else angle


def _turned(point: Point, cos: float, sin: float) -> Point:
    return point[0] * cos - point[1] * sin, point[0] * sin + point[1] * cos


def plan(pins: list[Pin], body: Point, via_radius: float, via_to_pad: float, via_to_via: float, layer: int,
         plane_nets: set[str], skip: set[str], stub_width: float, give_way: set[str] | None = None,
         only: set[str] | None = None, lift: dict[str, int] | None = None,
         keepouts: list[tuple[str, list[Point]]] | None = None, beyond: bool = False) -> Fanout:
    """The fan-out of one connector towards ``body``, its far row changing to ``layer``. The pins of
    a net in ``skip`` are left to the caller: no via, no join pin to pin; with ``only``, so are those
    of every net neither in it nor a plane net. A far pin of a net in ``give_way`` gets no via where
    the near pin of its column is of a plane net, which takes the via instead. A near pin of a net
    in ``lift`` whose layer there is not the pads' changes to it through a via past the near row.
    A connector at an angle is fanned out in its own frame and the result turned back. ``keepouts``
    are ("via", polygon) for a keepout of vias on any layer and ("trace", polygon) for one of traces
    on the pads' layer. With ``beyond`` the far row's vias stand beyond it, outside the connector's
    body, as a keepout between the rows makes them."""
    angle = row_angle(pins)
    if angle == 0.0:
        return _plan(pins, body, via_radius, via_to_pad, via_to_via, layer, plane_nets, skip, stub_width, give_way, only, lift,
                     keepouts, beyond)
    back, there = (math.cos(angle), math.sin(angle)), (math.cos(-angle), math.sin(-angle))
    local = []
    for pin in pins:
        x0, y0, x1, y1 = pin.pad
        outline = pin.shape or [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        corners = [_turned(q, *there) for q in outline]
        box = (min(q[0] for q in corners), min(q[1] for q in corners), max(q[0] for q in corners), max(q[1] for q in corners))
        local.append(Pin(pin.number, pin.net, _turned(pin.at, *there), box, pin.layer, corners))
    turned_keepouts = [(kind, [_turned(q, *there) for q in poly]) for kind, poly in keepouts or []]
    out = _plan(local, _turned(body, *there), via_radius, via_to_pad, via_to_via, layer, plane_nets, skip, stub_width, give_way,
                only, lift, turned_keepouts, beyond)
    out.traces = [(net, lay, width, [_turned(q, *back) for q in points]) for net, lay, width, points in out.traces]
    out.vias = [(net, _turned(at, *back)) for net, at in out.vias]
    out.exits = {net: [(_turned(at, *back), lay) for at, lay in spots] for net, spots in out.exits.items()}
    out.toward = _turned(out.toward, *back)  # the clearances along it stay: a turn keeps every projection
    return out


def _inside(point: Point, polygon: list[Point]) -> bool:
    x, y, odd = point[0], point[1], False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            odd = not odd
    return odd


def _segment_gap(a: Point, b: Point, c: Point, d: Point) -> float:
    """The distance between segments a-b and c-d, 0 where they cross."""
    def orient(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def point_gap(p: Point, u: Point, v: Point) -> float:
        dx, dy = v[0] - u[0], v[1] - u[1]
        t = max(0.0, min(1.0, ((p[0] - u[0]) * dx + (p[1] - u[1]) * dy) / (dx * dx + dy * dy or 1.0)))
        return math.hypot(p[0] - u[0] - t * dx, p[1] - u[1] - t * dy)

    if (orient(a, b, c) > 0) != (orient(a, b, d) > 0) and (orient(c, d, a) > 0) != (orient(c, d, b) > 0):
        return 0.0
    return min(point_gap(a, c, d), point_gap(b, c, d), point_gap(c, a, b), point_gap(d, a, b))


def _blocked(points: list[Point], reach: float, polygons: list[list[Point]]) -> bool:
    """Whether copper along ``points`` (a stub, or a via's centre alone) comes within ``reach`` of a keepout."""
    pieces = list(zip(points, points[1:], strict=False)) or [(points[0], points[0])]
    for poly in polygons:
        if any(_inside(q, poly) for q in points):
            return True
        for a, b in pieces:
            if any(_segment_gap(a, b, c, d) < reach - 1e-9 for c, d in zip(poly, poly[1:] + poly[:1], strict=True)):
                return True
    return False


def _spread(ideal: list[float], need: float) -> list[float]:
    """Positions along a line as near ``ideal`` (sorted) as keeps each two ``need`` apart: the least
    squares placement, by pooling neighbours that would come too close."""
    shifted = [x - i * need for i, x in enumerate(ideal)]
    blocks: list[list[float]] = []  # [sum, count]
    for value in shifted:
        blocks.append([value, 1.0])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            total, count = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
    out: list[float] = []
    for total, count in blocks:
        out += [total / count] * int(count)
    return [x + i * need for i, x in enumerate(out)]


def _plan(pins: list[Pin], body: Point, via_radius: float, via_to_pad: float, via_to_via: float, layer: int,
          plane_nets: set[str], skip: set[str], stub_width: float, give_way: set[str] | None = None,
          only: set[str] | None = None, lift: dict[str, int] | None = None,
          keepouts: list[tuple[str, list[Point]]] | None = None, beyond: bool = False) -> Fanout:
    """The fan-out of a connector whose rows run along x or y."""
    give_way = give_way or set()
    lift = lift or {}
    if only is not None:
        skip = set(skip) | {p.net for p in pins if p.net and not no_connect(p.net) and p.net not in only and p.net not in plane_nets}
    along, (first, second) = rows([replace(p) for p in pins])
    across = 1 - along
    level = [sum(p.at[across] for p in row) / len(row) for row in (first, second)]
    near, far = (first, second) if abs(level[0] - body[across]) < abs(level[1] - body[across]) else (second, first)
    near_level, far_level = (level[0], level[1]) if near is first else (level[1], level[0])
    sign = 1.0 if near_level > far_level else -1.0  # from the far row towards the near row, and on to the body
    toward = (0.0, sign) if across == 1 else (sign, 0.0)
    pad_layer = far[0].layer
    out = Fanout(toward=toward, pad_layer=far[0].layer)

    # the band between the rows where a via clears both rows' pads
    far_edge = max(p.pad[across + 2] for p in far) if sign > 0 else min(p.pad[across] for p in far)
    near_edge = min(p.pad[across] for p in near) if sign > 0 else max(p.pad[across + 2] for p in near)
    low = far_edge + sign * (via_to_pad + via_radius)
    high = near_edge - sign * (via_to_pad + via_radius)
    if (high - low) * sign < -1e-9:
        raise PlanError("E_VALIDATION", f"the rows are {abs(near_level - far_level):g} mm apart: no room between them for a via of "
                        f"{2 * via_radius:g} mm with {via_to_pad:g} mm from the pads")
    pitch = min((b.at[along] - a.at[along] for a, b in zip(far, far[1:], strict=False)), default=1.0)
    need = 2 * via_radius + via_to_via
    if math.hypot(pitch, high - low) >= need - 1e-9:
        levels = (low, high)
    elif pitch >= need - 1e-9:
        levels = ((low + high) / 2, (low + high) / 2)
    else:
        raise PlanError("E_VALIDATION", f"the pins are {pitch:g} mm apart and the rows leave {abs(high - low):g} mm between them: "
                        f"too little for vias of {2 * via_radius:g} mm in staggered lines")

    def spot(index: int, position: float) -> Point:
        cross = levels[index % 2]
        return (position, cross) if along == 0 else (cross, position)

    via_keepouts = [poly for kind, poly in keepouts or [] if kind == "via" and len(poly) >= 3]
    trace_keepouts = [poly for kind, poly in keepouts or [] if kind == "trace" and len(poly) >= 3]
    runs: list[list[int]] = []  # consecutive far pins of one net, which share one via through their join pin to pin
    for index, pin in enumerate(far):
        if runs and far[runs[-1][-1]].net == pin.net and not no_connect(pin.net) and pin.net not in skip:
            runs[-1].append(index)
        else:
            runs.append([index])
    closed = beyond or any(_blocked([spot(i, far[i].at[along])], via_radius, via_keepouts)
                 or _blocked([far[i].at, spot(i, far[i].at[along])], stub_width / 2, trace_keepouts)
                 for i, pin in enumerate(far) if not no_connect(pin.net) and pin.net not in skip)
    if closed:
        return _outer(out, pins, near, far, runs, along, sign, via_radius, via_to_pad, need, layer, pad_layer, plane_nets,
                      stub_width, lift, skip)
    near_at = {round(p.at[along], 3): p for p in near}
    for run in runs:
        net = far[run[0]].net
        partners = [near_at.get(round(far[i].at[along], 3)) for i in run]
        chosen = None
        if not no_connect(net) and net not in skip:
            def rank(k: int, net: str | None = net, partners: list[Pin | None] = partners, run: list[int] = run) -> tuple[int, float]:
                # a column whose near pin shares the net, else one whose near pin is no plane pin, nearest the run's middle
                above = partners[k]
                kind = 0 if above is not None and above.net == net else 2 if above is not None and above.net in plane_nets else 1
                return kind, abs(k - (len(run) - 1) / 2)

            chosen = min(range(len(run)), key=rank)
            above = partners[chosen]
            if net in give_way and above is not None and above.net in plane_nets:
                chosen = None  # the run gives its via to the plane pin above
        for k, index in enumerate(run):
            pin, partner = far[index], partners[k]
            if k == chosen:
                at = spot(index, pin.at[along])
                out.vias.append((pin.net, at))
                out.traces.append((pin.net, pad_layer, stub_width, [pin.at, at]))
                if pin.net not in plane_nets:  # a plane net ends on the plane, not on a lane
                    out.exits.setdefault(pin.net, []).append((at, layer))
                if partner is not None and partner.net == pin.net:
                    out.traces.append((pin.net, pad_layer, stub_width, [partner.at, at]))
                    partner.net = None  # handled
            elif partner is not None and partner.net in plane_nets:
                at = spot(index, pin.at[along])
                out.vias.append((partner.net, at))
                out.traces.append((partner.net, pad_layer, stub_width, [partner.at, at]))
                partner.net = None
    handled = {p.number for p in near if p.net is None}
    original = {p.number: p.net for p in pins}
    near_reach = max(p.pad[across + 2] for p in near) if sign > 0 else min(p.pad[across] for p in near)
    lift_low = near_reach + sign * (via_to_pad + via_radius)
    lift_levels = (lift_low, lift_low + sign * math.sqrt(max(need * need - pitch * pitch, 0.0)))
    lifted: list[float] = []
    for index, pin in enumerate(near):
        if no_connect(pin.net):
            continue
        if pin.net in plane_nets:
            run = _run(near, index, original)
            if not any(p.number in handled for p in run):  # no pin of its run of one net reaches a via
                out.loose.append(f"{pin.number} of {pin.net}")
            continue
        target = lift.get(pin.net)
        if target is not None and target != pad_layer and pin.net not in skip:
            cross = lift_levels[index % 2]
            at = (pin.at[along], cross) if along == 0 else (cross, pin.at[along])
            out.vias.append((pin.net, at))
            out.traces.append((pin.net, pad_layer, stub_width, [pin.at, at]))
            out.exits.setdefault(pin.net, []).append((at, target))
            lifted.append(cross)
            out.lifted.append(at)
            continue
        out.exits.setdefault(pin.net, []).append((pin.at, pad_layer))
    # consecutive pins of one net in a row, joined pin to pin; a skipped net's pins are the caller's to join
    for row in (near, far):
        for a, b in zip(row, row[1:], strict=False):
            if original[a.number] == original[b.number] and not no_connect(original[a.number]) and original[a.number] not in skip:
                width = min(a.pad[along + 2] - a.pad[along], b.pad[along + 2] - b.pad[along])
                out.traces.append((original[a.number], pad_layer, round(width, 4), [a.at, b.at]))
    out.clear_at = near_reach * sign  # measured along ``toward``
    out.clear_vias_at = max(level * sign for level in levels) + via_radius
    out.via_radius = via_radius
    return out


def _outer(out: Fanout, pins: list[Pin], near: list[Pin], far: list[Pin], runs: list[list[int]], along: int, sign: float,
           via_radius: float, via_to_pad: float, need: float, layer: int, pad_layer: int, plane_nets: set[str],
           stub_width: float, lift: dict[str, int], skip: set[str]) -> Fanout:
    """The fan-out with the far row's vias in one line beyond it: each run of one net gets a via, as
    near its middle as the line's spacing allows, and a stub that leaves its nearest pad outwards."""
    across = 1 - along
    outer_edge = min(p.pad[across] for p in far) if sign > 0 else max(p.pad[across + 2] for p in far)
    line = outer_edge - sign * (via_to_pad + via_radius)
    bend = outer_edge - sign * (via_to_pad + stub_width / 2)  # where a stub may turn aside, clear of the pads beside it
    wanted = [run for run in runs if not no_connect(far[run[0]].net) and far[run[0]].net not in skip]
    ideal = [sum(far[i].at[along] for i in run) / len(run) for run in wanted]
    order = sorted(range(len(wanted)), key=lambda k: ideal[k])
    placed = _spread([ideal[k] for k in order], need)
    where = {order[n]: placed[n] for n in range(len(order))}
    for k, run in enumerate(wanted):
        net = far[run[0]].net
        position = where[k]
        start = min((far[i] for i in run), key=lambda p: abs(p.at[along] - position))
        at = (position, line) if along == 0 else (line, position)
        turn = (start.at[along], bend) if along == 0 else (bend, start.at[along])
        out.vias.append((net, at))
        out.traces.append((net, pad_layer, stub_width, [start.at, turn, at] if abs(start.at[along] - position) > 1e-6 else [start.at, at]))
        if net not in plane_nets:
            out.exits.setdefault(net, []).append((at, layer))
    original = {p.number: p.net for p in pins}
    near_reach = max(p.pad[across + 2] for p in near) if sign > 0 else min(p.pad[across] for p in near)
    pitch = min((b.at[along] - a.at[along] for a, b in zip(near, near[1:], strict=False)), default=1.0)
    lift_low = near_reach + sign * (via_to_pad + via_radius)
    lift_levels = (lift_low, lift_low + sign * math.sqrt(max(need * need - pitch * pitch, 0.0)))
    lifted: list[float] = []
    for index, pin in enumerate(near):
        if no_connect(pin.net):
            continue
        if pin.net in plane_nets:
            run = _run(near, index, original)
            if index == near.index(run[0]):
                out.loose.append(f"{pin.number} of {pin.net}")  # the far row stands between it and every via
            continue
        target = lift.get(pin.net)
        if target is not None and target != pad_layer and pin.net not in skip:
            # its lane runs on another layer: a via just past the near row, staggered as there
            cross = lift_levels[index % 2]
            at = (pin.at[along], cross) if along == 0 else (cross, pin.at[along])
            out.vias.append((pin.net, at))
            out.traces.append((pin.net, pad_layer, stub_width, [pin.at, at]))
            out.exits.setdefault(pin.net, []).append((at, target))
            lifted.append(cross)
            out.lifted.append(at)
            continue
        out.exits.setdefault(pin.net, []).append((pin.at, pad_layer))
    for row in (near, far):
        for a, b in zip(row, row[1:], strict=False):
            if original[a.number] == original[b.number] and not no_connect(original[a.number]) and original[a.number] not in skip:
                width = min(a.pad[along + 2] - a.pad[along], b.pad[along + 2] - b.pad[along])
                out.traces.append((original[a.number], pad_layer, round(width, 4), [a.at, b.at]))
    out.clear_at = near_reach * sign
    out.clear_vias_at = line * sign + via_radius  # the vias stand behind the far row: a lane on another layer leaves at once
    out.via_radius = via_radius
    return out


def _run(row: list[Pin], index: int, nets: dict[str, str | None]) -> list[Pin]:
    """The pins next to ``row[index]`` in its row that share its net, itself included."""
    net = nets[row[index].number]
    low = index
    while low > 0 and nets[row[low - 1].number] == net:
        low -= 1
    high = index
    while high < len(row) - 1 and nets[row[high + 1].number] == net:
        high += 1
    return row[low:high + 1]


def pin_exit(pins: list[Pin], number: str, toward: Point, clearance: float, width: float) -> Point | None:
    """Where a trace that leaves pin ``number`` straight along its pad's long side, on the side of
    ``toward`` (the board's body), clears the pads of the pin's row by ``clearance``, as a fan-out's
    near row leaves; None for a pin the part does not have."""
    pin = next((p for p in pins if p.number == number), None)
    if pin is None:
        return None
    x0, y0, x1, y1 = pin.pad
    shape = pin.shape or [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    cx, cy = sum(q[0] for q in shape) / len(shape), sum(q[1] for q in shape) / len(shape)
    sxx = sum((q[0] - cx) ** 2 for q in shape)
    syy = sum((q[1] - cy) ** 2 for q in shape)
    sxy = sum((q[0] - cx) * (q[1] - cy) for q in shape)
    angle = 0.5 * math.atan2(2 * sxy, sxx - syy)  # the pad's long side
    u = (math.cos(angle), math.sin(angle))
    if (toward[0] - pin.at[0]) * u[0] + (toward[1] - pin.at[1]) * u[1] < 0:
        u = (-u[0], -u[1])
    own = max(abs((q[0] - pin.at[0]) * u[0] + (q[1] - pin.at[1]) * u[1]) for q in shape)
    row = [p for p in pins if abs((p.at[0] - pin.at[0]) * u[0] + (p.at[1] - pin.at[1]) * u[1]) < own]
    reach = max(((q[0] - pin.at[0]) * u[0] + (q[1] - pin.at[1]) * u[1])
                for p in row for q in (p.shape or [(p.pad[0], p.pad[1]), (p.pad[2], p.pad[1]), (p.pad[2], p.pad[3]), (p.pad[0], p.pad[3])]))
    step = reach + clearance + width / 2
    return pin.at[0] + u[0] * step, pin.at[1] + u[1] * step


def leave(fanout: Fanout, start: Point, layer: int, width: float, clearance: float, past: float | None = None) -> Point:
    """The point a trace leaving the connector at ``start`` on ``layer`` reaches straight towards the
    body: clear of the near row's pads by ``clearance`` on the pad layer, and of the vias between the
    rows on any other layer, which has no pads of the connector; at least ``past`` along ``toward``
    when given, such as past a via the fan-out lifts a near pin through that its way would meet."""
    tx, ty = fanout.toward
    edge = fanout.clear_at if layer == fanout.pad_layer else fanout.clear_vias_at
    if past is not None:
        edge = max(edge, past)
    reach = edge + clearance + width / 2
    along = start[0] * tx + start[1] * ty
    step = max(reach - along, 0.0)
    return start[0] + tx * step, start[1] + ty * step
