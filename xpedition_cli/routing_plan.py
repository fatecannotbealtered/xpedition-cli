"""A hand-routing plan checked against the board's geometry, before Layout sees it.

`pcb geometry` writes the board as data; a plan (the items `pcb trace --file` takes)
can be checked against that data here: only 0/45/90° segments, the clearance rule to
pads, traces and vias of other nets, and a via's clearance to any pad it does not sit
inside (Layout refuses a via pad that touches a surface-mount pad, its own net
included). `stitch_vias` writes the plan for a via beside every surface-mount pad of a
net (ground stitching), where the checker finds room.
"""

from __future__ import annotations

import math
from typing import Any

CLEARANCE_MM = 0.254  # the stock templates' rule
VIA_PAD_RADIUS_MM = 0.33  # the stock 026VIA
ANGLE_TOLERANCE_MM = 0.05
Point = tuple[float, float]
Rect = tuple[float, float, float, float]


def _dist_point_segment(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _orient(p: Point, q: Point, r: Point) -> float:
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    o1, o2 = _orient(a, b, c), _orient(a, b, d)
    o3, o4 = _orient(c, d, a), _orient(c, d, b)
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def _dist_segments(a: Point, b: Point, c: Point, d: Point) -> float:
    if _segments_cross(a, b, c, d):
        return 0.0
    return min(
        _dist_point_segment(a, c, d),
        _dist_point_segment(b, c, d),
        _dist_point_segment(c, a, b),
        _dist_point_segment(d, a, b),
    )


def _dist_point_rect(p: Point, rect: Rect) -> float:
    x0, y0, x1, y1 = rect
    dx = max(x0 - p[0], 0.0, p[0] - x1)
    dy = max(y0 - p[1], 0.0, p[1] - y1)
    return math.hypot(dx, dy)


def _dist_segment_rect(a: Point, b: Point, rect: Rect) -> float:
    x0, y0, x1, y1 = rect
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    edges = list(zip(corners, corners[1:] + corners[:1], strict=True))
    if any(_segments_cross(a, b, c, d) for c, d in edges):
        return 0.0
    if x0 <= a[0] <= x1 and y0 <= a[1] <= y1:
        return 0.0
    return min(
        min(_dist_point_segment(a, c, d) for c, d in edges),
        min(_dist_point_segment(b, c, d) for c, d in edges),
        min(_dist_point_segment(c, a, b) for c in corners),
    )


def _pad_rect(pad: dict[str, Any]) -> Rect:
    if pad.get("circle"):
        cx, cy, r = pad["circle"]
        return (cx - r, cy - r, cx + r, cy + r)
    xs = [row[0] for row in pad.get("path") or [(0, 0, 0)]]
    ys = [row[1] for row in pad.get("path") or [(0, 0, 0)]]
    return (min(xs), min(ys), max(xs), max(ys))


class Board:
    """The board's copper as obstacles, from `pcb geometry`'s model."""

    def __init__(self, model: dict[str, Any]) -> None:
        self.model = model
        self.pins: dict[str, dict[str, Any]] = {}
        for component in model.get("components") or []:
            for pin in component.get("pins") or []:
                self.pins[f"{component['refdes']}.{pin['pin']}"] = {
                    **pin,
                    "refdes": component["refdes"],
                }
        self.pads: list[tuple[Rect, str, int, str]] = []
        layers_by_ref: dict[str, set[int]] = {}
        for pad in model.get("pads") or []:
            layer = int(pad.get("layer") or 0)
            ref = str(pad.get("refdes") or "")
            self.pads.append((_pad_rect(pad), str(pad.get("net") or ""), layer, ref))
            layers_by_ref.setdefault(ref, set()).add(layer)
        self.through_hole = {ref for ref, layers in layers_by_ref.items() if len(layers) >= 2}
        self.segments: list[tuple[Point, Point, int, str, float, int]] = []
        for trace in model.get("traces") or []:
            points = [(row[0], row[1]) for row in trace.get("path") or []]
            for a, b in zip(points, points[1:], strict=False):
                self.segments.append(
                    (
                        a,
                        b,
                        int(trace.get("layer") or 1),
                        str(trace.get("net") or ""),
                        float(trace.get("width") or CLEARANCE_MM),
                        -1,
                    )
                )
        self.vias: list[tuple[Point, str, int]] = []
        for via in model.get("vias") or []:
            if via.get("circle"):
                self.vias.append(
                    ((via["circle"][0], via["circle"][1]), str(via.get("net") or ""), -1)
                )
        outline = (model.get("outline") or [{}])[0]
        xs = [row[0] for row in outline.get("path") or [(0, 0, 0)]]
        ys = [row[1] for row in outline.get("path") or [(0, 0, 0)]]
        self.bounds: Rect = (min(xs), min(ys), max(xs), max(ys))

    def pin(self, name: str) -> Point:
        pin = self.pins[name]
        return (float(pin["x"]), float(pin["y"]))


def check_plan(
    items: list[dict[str, Any]],
    board: Board,
    clearance: float = CLEARANCE_MM,
    via_radius: float = VIA_PAD_RADIUS_MM,
) -> list[str]:
    """What Layout would refuse or a reviewer would flag: angles, clearances to pads,
    traces and vias of other nets, a via pad touching any pad."""
    segments = list(board.segments)
    vias = list(board.vias)
    for index, item in enumerate(items):
        if item.get("kind") == "via":
            vias.append((tuple(item["at"]), str(item["net"]), index))  # type: ignore[arg-type]
        else:
            points = [tuple(p) for p in item["points"]]
            for a, b in zip(points, points[1:], strict=False):
                segments.append(
                    (
                        a,
                        b,
                        int(item.get("layer") or 1),
                        str(item["net"]),
                        float(item.get("width") or CLEARANCE_MM),
                        index,
                    )  # type: ignore[arg-type]
                )
    problems: list[str] = []
    for a, b, layer, net, width, index in segments:
        if index < 0:
            continue
        dx, dy = b[0] - a[0], b[1] - a[1]
        if (
            min(abs(dx), abs(dy)) > ANGLE_TOLERANCE_MM
            and abs(abs(dx) - abs(dy)) > ANGLE_TOLERANCE_MM
        ):
            problems.append(f"item {index} {net}: segment {a}->{b} is not 0/45/90 degrees")
        for rect, pad_net, pad_layer, ref in board.pads:
            if pad_net == net:
                continue
            if pad_layer not in (0, layer) and ref not in board.through_hole:
                continue
            d = _dist_segment_rect(a, b, rect) - width / 2
            if d < clearance - 1e-6:
                problems.append(
                    f"item {index} {net} L{layer}: {a}->{b} is {d:.2f} mm from pad {ref} "
                    f"({pad_net or 'no net'})"
                )
    for i, (a, b, layer, net, width, index) in enumerate(segments):
        for a2, b2, layer2, net2, width2, index2 in segments[i + 1 :]:
            if layer != layer2 or net == net2 or (index < 0 and index2 < 0):
                continue
            d = _dist_segments(a, b, a2, b2) - width / 2 - width2 / 2
            if d < clearance - 1e-6:
                problems.append(
                    f"items {index}/{index2} {net}/{net2} L{layer}: traces {d:.2f} mm apart"
                )
    for p, net, index in vias:
        if index < 0:
            continue
        for rect, pad_net, _pad_layer, ref in board.pads:
            d = _dist_point_rect(p, rect) - via_radius
            if d < clearance - 1e-6:
                problems.append(
                    f"item {index} via {net} at {p}: {d:.2f} mm from pad {ref} "
                    f"({pad_net or 'no net'})"
                )
    for p, net, index in vias:
        for a, b, layer, net2, width, index2 in segments:
            if net2 == net or (index < 0 and index2 < 0):
                continue
            d = _dist_point_segment(p, a, b) - via_radius - width / 2
            if d < clearance - 1e-6:
                problems.append(
                    f"via {index} {net} at {p} vs item {index2} {net2} L{layer}: {d:.2f} mm"
                )
        for p2, net2, index2 in vias:
            if p2 is p or net2 == net or (index < 0 and index2 < 0):
                continue
            d = math.hypot(p[0] - p2[0], p[1] - p2[1]) - 2 * via_radius
            if d < clearance - 1e-6:
                problems.append(f"vias {index}/{index2} {net}/{net2}: {d:.2f} mm apart")
    return problems


class PlanBuilder:
    """A hand-routing plan written pin by pin: `trace(net, layer, points, width)` and
    `via(net, point)`, where a point is `(x, y)`, a pin name `"U302.5"`, or
    `("U302.5", dx, dy)`; `check()` runs `check_plan` against the board, `items()` is
    what `pcb trace --file` takes."""

    def __init__(self, geometry: dict[str, Any] | str) -> None:
        import json
        from pathlib import Path

        model = (
            geometry
            if isinstance(geometry, dict)
            else json.loads(Path(geometry).read_text(encoding="utf-8"))
        )
        self.board = Board(model)
        self._items: list[dict[str, Any]] = []

    def point(self, spec: Any) -> Point:
        if isinstance(spec, str):
            return self.board.pin(spec)
        if isinstance(spec, tuple) and len(spec) == 3 and isinstance(spec[0], str):
            x, y = self.board.pin(spec[0])
            return (round(x + float(spec[1]), 4), round(y + float(spec[2]), 4))
        return (round(float(spec[0]), 4), round(float(spec[1]), 4))

    def trace(self, net: str, layer: int, points: list[Any], width: float = CLEARANCE_MM) -> None:
        self._items.append(
            {
                "kind": "trace",
                "net": net,
                "layer": int(layer),
                "width": float(width),
                "points": [self.point(p) for p in points],
            }
        )

    def via(self, net: str, point: Any) -> None:
        self._items.append({"kind": "via", "net": net, "at": self.point(point)})

    def check(self) -> list[str]:
        return check_plan(self._items, self.board)

    def items(self) -> list[dict[str, Any]]:
        return [
            {**item, "at": list(item["at"])}
            if item["kind"] == "via"
            else {**item, "points": [list(p) for p in item["points"]]}
            for item in self._items
        ]

    def write(self, path: str) -> None:
        import json
        from pathlib import Path

        Path(path).write_text(
            json.dumps({"items": self.items()}, ensure_ascii=False, indent=1), encoding="utf-8"
        )


def stitch_vias(
    board: Board,
    net: str = "GND",
    width: float = 0.3,
    distances: tuple[float, ...] = (1.1, 1.3, 1.5, 1.8),
    margin: float = 1.5,
) -> tuple[list[dict[str, Any]], list[str]]:
    """A via beside every surface-mount pad of `net` with a short top-layer stub to it,
    at the first of eight directions and four distances the checker accepts; the pins
    that got none are named."""
    items: list[dict[str, Any]] = []
    left: list[str] = []
    min_x, min_y, max_x, max_y = board.bounds
    for name, pin in sorted(board.pins.items()):
        if pin.get("net") != net or pin["refdes"] in board.through_hole:
            continue
        px, py = float(pin["x"]), float(pin["y"])
        found = None
        for distance in distances:
            for angle in range(0, 360, 45):
                vx = round(px + distance * math.cos(math.radians(angle)), 3)
                vy = round(py + distance * math.sin(math.radians(angle)), 3)
                if not (
                    min_x + margin < vx < max_x - margin and min_y + margin < vy < max_y - margin
                ):
                    continue
                trial = items + [
                    {"kind": "via", "net": net, "at": (vx, vy)},
                    {
                        "kind": "trace",
                        "net": net,
                        "layer": 1,
                        "width": width,
                        "points": [(px, py), (vx, vy)],
                    },
                ]
                if not check_plan(trial, board):
                    found = (vx, vy)
                    break
            if found:
                break
        if found is None:
            left.append(name)
            continue
        items.append({"kind": "via", "net": net, "at": found})
        items.append(
            {"kind": "trace", "net": net, "layer": 1, "width": width, "points": [(px, py), found]}
        )
    return items, left
