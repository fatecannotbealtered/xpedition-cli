"""The fan-out of a two-row connector: the row away from the board's body changes layer through vias
staggered between the rows, and every net is given the point where it leaves the connector.

A board-to-board connector has two rows of pads on one side. The row nearer the board's body can
leave on the pad layer straight away; the other row is blocked by it, so each of its pins goes
through a via between the rows to another layer. With the pins at a fine pitch, the vias stand in
two staggered lines, one per column. A pin of the near row whose net is the far pin's below it
shares that via; a pin of a plane net, such as GND, takes the via of a column whose far pin needs
none. Consecutive pins of one net in a row are joined pin to pin.

Coordinates in mm. Pure Python: the adapter reads the pins and pads from Layout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from . import PlanError

Point = tuple[float, float]
Box = tuple[float, float, float, float]
ROW_TOLERANCE = 0.05  # mm two pins' cross coordinates may differ and stay in one row


@dataclass
class Pin:
    number: str
    net: str | None
    at: Point
    pad: Box  # the pad's extents on its layer
    layer: int


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
    clear_vias_at: float = 0.0  # along ``toward``, where the vias between the rows end


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


def plan(pins: list[Pin], body: Point, via_radius: float, via_to_pad: float, via_to_via: float, layer: int,
         plane_nets: set[str], skip: set[str], stub_width: float, give_way: set[str] | None = None) -> Fanout:
    """The fan-out of one connector towards ``body``, its far row changing to ``layer``. A far pin
    of a net in ``skip`` gets no via; one of a net in ``give_way`` gets none where the near pin of
    its column is of a plane net, which takes the via instead."""
    give_way = give_way or set()
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

    near_at = {round(p.at[along], 3): p for p in near}
    for index, pin in enumerate(far):
        partner = near_at.get(round(pin.at[along], 3))
        yields = pin.net in give_way and partner is not None and partner.net in plane_nets
        if not no_connect(pin.net) and pin.net not in skip and not yields:
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
    for index, pin in enumerate(near):
        if no_connect(pin.net):
            continue
        if pin.net in plane_nets:
            run = _run(near, index, original)
            if not any(p.number in handled for p in run):  # no pin of its run of one net reaches a via
                out.loose.append(f"{pin.number} of {pin.net}")
            continue
        out.exits.setdefault(pin.net, []).append((pin.at, pad_layer))
    # consecutive pins of one net in a row, joined pin to pin
    for row in (near, far):
        for a, b in zip(row, row[1:], strict=False):
            if original[a.number] == original[b.number] and not no_connect(original[a.number]):
                width = min(a.pad[along + 2] - a.pad[along], b.pad[along + 2] - b.pad[along])
                out.traces.append((original[a.number], pad_layer, round(width, 4), [a.at, b.at]))
    near_reach = max(p.pad[across + 2] for p in near) if sign > 0 else min(p.pad[across] for p in near)
    out.clear_at = near_reach * sign  # measured along ``toward``
    out.clear_vias_at = max(level * sign for level in levels) + via_radius
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


def leave(fanout: Fanout, start: Point, layer: int, width: float, clearance: float) -> Point:
    """The point a trace leaving the connector at ``start`` on ``layer`` reaches straight towards the
    body: clear of the near row's pads by ``clearance`` on the pad layer, and of the vias between the
    rows on any other layer, which has no pads of the connector."""
    tx, ty = fanout.toward
    edge = fanout.clear_at if layer == fanout.pad_layer else fanout.clear_vias_at
    reach = edge + clearance + width / 2
    along = start[0] * tx + start[1] * ty
    step = max(reach - along, 0.0)
    return start[0] + tx * step, start[1] + ty * step
