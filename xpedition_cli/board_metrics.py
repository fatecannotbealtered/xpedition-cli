"""Numbers for how good a board's placement and routing are, from `pcb geometry`'s file.

DRC says whether a board breaks a rule; it says nothing about whether a placement is
good. An agent that moves a part needs to know if the board got better, so this module
measures what a layout engineer looks at, as numbers that can be compared between two
states of the same board:

- the ratsnest: per net, the shortest tree through its pins (the airwires as if nothing
  were routed), and how often signal airwires of different nets cross;
- parts whose extents overlap, stick out of the board, or are not placed;
- each decoupling capacitor's distance to the nearest IC pin on its supply net;
- each connector's distance to the nearest board edge, and the placement density;
- the routing: trace length per layer, vias, and corners sharper than 90 degrees.

Pure Python: no Layout. Extents are Layout's component extrema, a proxy for the
courtyard; supply and ground nets are recognised by name and listed, so a wrong guess
is visible.
"""

from __future__ import annotations

import math
import re
from itertools import combinations
from typing import Any

Point = tuple[float, float]
Rect = tuple[float, float, float, float]

GROUND_NET = re.compile(r"^(?:[ADPSC]?GND|VSS|EARTH|CHASSIS)\w*$", re.IGNORECASE)
SUPPLY_NET = re.compile(
    r"^(?:\+.+|-\d.*|[AD]?V(?:CC|DD|EE|BAT|BUS|SYS|IN|REF|CORE|IO)\w*|\d+V\d*\w*|P\d+V\d*\w*)$",
    re.IGNORECASE,
)
CAPACITOR = re.compile(r"^C\d")
IC = re.compile(r"^(?:U|IC)\d")
CONNECTOR = re.compile(r"^(?:J|P|CN|USB|X)\d")
AREA_EPSILON = 0.01  # mm², below which two extents only touch
DISTANCE_EPSILON = 0.01  # mm
ACUTE_TOLERANCE = 0.5  # degrees
LIST_LIMIT = 50  # longest lists are cut to this many entries; totals stay complete
SUMMARY_KEYS = (
    "ratsnest_mm",
    "signal_ratsnest_mm",
    "crossings",
    "overlaps",
    "outside",
    "unplaced",
    "decoupling_max_mm",
    "edge_max_mm",
    "density",
    "trace_mm",
    "vias",
    "acute_corners",
)


def net_kind(name: str) -> str:
    """`ground`, `supply` or `signal`, by the net's name."""
    if GROUND_NET.match(name):
        return "ground"
    if SUPPLY_NET.match(name):
        return "supply"
    return "signal"


def _mst(points: list[Point]) -> list[tuple[Point, Point]]:
    """The shortest tree through `points` (Prim's algorithm; nets are small)."""
    if len(points) < 2:
        return []
    inside = [False] * len(points)
    best = [math.inf] * len(points)
    parent = [-1] * len(points)
    best[0] = 0.0
    edges: list[tuple[Point, Point]] = []
    for _ in range(len(points)):
        current = min((i for i in range(len(points)) if not inside[i]), key=lambda i: best[i])
        inside[current] = True
        if parent[current] >= 0:
            edges.append((points[parent[current]], points[current]))
        for other in range(len(points)):
            if not inside[other]:
                distance = math.dist(points[current], points[other])
                if distance < best[other]:
                    best[other] = distance
                    parent[other] = current
    return edges


def _orient(p: Point, q: Point, r: Point) -> float:
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _cross(a: tuple[Point, Point], b: tuple[Point, Point]) -> bool:
    """Proper crossing: the segments meet at a point inside both, not at an end."""
    (p, q), (r, s) = a, b
    o1, o2 = _orient(p, q, r), _orient(p, q, s)
    o3, o4 = _orient(r, s, p), _orient(r, s, q)
    return o1 * o2 < 0 and o3 * o4 < 0


def _bounds(model: dict[str, Any]) -> Rect | None:
    points = [
        (float(row[0]), float(row[1]))
        for shape in model.get("outline") or []
        for row in shape.get("path") or []
    ]
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _overlap(a: Rect, b: Rect) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return width * height if width > 0 and height > 0 else 0.0


def _edge_distance(extents: Rect, board: Rect) -> float:
    return min(
        extents[0] - board[0],
        board[2] - extents[2],
        extents[1] - board[1],
        board[3] - extents[3],
    )


def _pins(components: list[dict[str, Any]]) -> dict[str, list[tuple[str, Point]]]:
    """Net name -> the placed pins on it, as (`REFDES.PIN`, position)."""
    nets: dict[str, list[tuple[str, Point]]] = {}
    for component in components:
        if not component.get("placed"):
            continue
        for pin in component.get("pins") or []:
            net = str(pin.get("net") or "")
            if not net:
                continue
            ref = f"{component.get('refdes')}.{pin.get('pin')}"
            nets.setdefault(net, []).append((ref, (float(pin["x"]), float(pin["y"]))))
    return nets


def _ratsnest(nets: dict[str, list[tuple[str, Point]]]) -> dict[str, Any]:
    per_net: list[dict[str, Any]] = []
    signal_edges: list[tuple[str, tuple[Point, Point]]] = []
    for name, pins in sorted(nets.items()):
        if len(pins) < 2:
            continue
        edges = _mst([point for _ref, point in pins])
        length = sum(math.dist(a, b) for a, b in edges)
        kind = net_kind(name)
        per_net.append({"net": name, "kind": kind, "pins": len(pins), "mm": round(length, 3)})
        if kind == "signal":
            signal_edges += [(name, edge) for edge in edges]
    crossings = sum(
        1
        for (net_a, edge_a), (net_b, edge_b) in combinations(signal_edges, 2)
        if net_a != net_b and _cross(edge_a, edge_b)
    )
    per_net.sort(key=lambda row: (-row["mm"], row["net"]))
    return {
        "total_mm": round(sum(row["mm"] for row in per_net), 3),
        "signal_mm": round(sum(row["mm"] for row in per_net if row["kind"] == "signal"), 3),
        "crossings": crossings,
        "supply_nets": sorted(row["net"] for row in per_net if row["kind"] != "signal"),
        "longest": per_net[:LIST_LIMIT],
    }


def _decoupling(
    components: list[dict[str, Any]], nets: dict[str, list[tuple[str, Point]]]
) -> list[dict[str, Any]]:
    """Each capacitor between a supply net and ground: its distance to the nearest IC pin
    on that supply net (a capacitor with no IC on its rail is not decoupling one)."""
    rows: list[dict[str, Any]] = []
    for component in components:
        refdes = str(component.get("refdes") or "")
        pins = component.get("pins") or []
        if not component.get("placed") or not CAPACITOR.match(refdes) or len(pins) != 2:
            continue
        kinds = {net_kind(str(pin.get("net") or "")): pin for pin in pins if pin.get("net")}
        if set(kinds) != {"supply", "ground"}:
            continue
        pin = kinds["supply"]
        net = str(pin["net"])
        position = (float(pin["x"]), float(pin["y"]))
        targets = [(ref, point) for ref, point in nets.get(net, []) if IC.match(ref)]
        if not targets:
            continue
        ref, point = min(targets, key=lambda item: math.dist(position, item[1]))
        rows.append(
            {
                "capacitor": refdes,
                "net": net,
                "nearest": ref,
                "mm": round(math.dist(position, point), 3),
            }
        )
    rows.sort(key=lambda row: (-row["mm"], row["capacitor"]))
    return rows


def _routing(model: dict[str, Any]) -> dict[str, Any]:
    by_layer: dict[str, float] = {}
    acute: list[dict[str, Any]] = []
    for trace in model.get("traces") or []:
        points = [(float(row[0]), float(row[1])) for row in trace.get("path") or []]
        layer = str(trace.get("layer") or "")
        by_layer[layer] = by_layer.get(layer, 0.0) + sum(
            math.dist(a, b) for a, b in zip(points, points[1:], strict=False)
        )
        for before, vertex, after in zip(points, points[1:], points[2:], strict=False):
            ax, ay = before[0] - vertex[0], before[1] - vertex[1]
            bx, by = after[0] - vertex[0], after[1] - vertex[1]
            norm = math.hypot(ax, ay) * math.hypot(bx, by)
            if norm == 0:
                continue
            cosine = max(-1.0, min(1.0, (ax * bx + ay * by) / norm))
            angle = math.degrees(math.acos(cosine))
            if angle < 90.0 - ACUTE_TOLERANCE:
                acute.append(
                    {
                        "net": str(trace.get("net") or ""),
                        "layer": trace.get("layer"),
                        "at": [round(vertex[0], 3), round(vertex[1], 3)],
                        "degrees": round(angle, 1),
                    }
                )
    return {
        "trace_mm": round(sum(by_layer.values()), 3),
        "by_layer": {layer: round(length, 3) for layer, length in sorted(by_layer.items())},
        "vias": len(model.get("vias") or []),
        "acute_corners": acute,
    }


def measure(model: dict[str, Any]) -> dict[str, Any]:
    """The metrics of one board state (see the module docstring)."""
    components = [c for c in model.get("components") or [] if isinstance(c, dict)]
    board = _bounds(model)
    placed = [c for c in components if c.get("placed") and c.get("extents")]
    extents = {
        str(c.get("refdes")): tuple(float(v) for v in c["extents"])  # type: ignore[misc]
        for c in placed
    }
    nets = _pins(components)
    ratsnest = _ratsnest(nets)
    overlaps = []
    for (a, rect_a), (b, rect_b) in combinations(sorted(extents.items()), 2):
        area = _overlap(rect_a, rect_b)  # type: ignore[arg-type]
        if area > AREA_EPSILON:
            overlaps.append({"a": a, "b": b, "mm2": round(area, 3)})
    overlaps.sort(key=lambda row: (-row["mm2"], row["a"], row["b"]))
    outside: list[dict[str, Any]] = []
    edge_parts: list[dict[str, Any]] = []
    board_info: dict[str, Any] | None = None
    density = None
    if board is not None:
        width, height = board[2] - board[0], board[3] - board[1]
        board_info = {"width_mm": round(width, 3), "height_mm": round(height, 3)}
        for refdes, rect in sorted(extents.items()):
            margin = _edge_distance(rect, board)  # type: ignore[arg-type]
            if margin < -DISTANCE_EPSILON:
                outside.append({"refdes": refdes, "beyond_mm": round(-margin, 3)})
            if CONNECTOR.match(refdes):
                edge_parts.append({"refdes": refdes, "edge_mm": round(max(margin, 0.0), 3)})
        edge_parts.sort(key=lambda row: (-row["edge_mm"], row["refdes"]))
        area = width * height
        if area > 0:
            parts_area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in extents.values())
            density = round(parts_area / area, 3)
    unplaced = sorted(str(c.get("refdes")) for c in components if not c.get("placed"))
    decoupling = _decoupling(components, nets)
    routing = _routing(model)
    summary = {
        "ratsnest_mm": ratsnest["total_mm"],
        "signal_ratsnest_mm": ratsnest["signal_mm"],
        "crossings": ratsnest["crossings"],
        "overlaps": len(overlaps),
        "outside": len(outside),
        "unplaced": len(unplaced),
        "decoupling_max_mm": decoupling[0]["mm"] if decoupling else None,
        "edge_max_mm": edge_parts[0]["edge_mm"] if edge_parts else None,
        "density": density,
        "trace_mm": routing["trace_mm"],
        "vias": routing["vias"],
        "acute_corners": len(routing["acute_corners"]),
    }
    return {
        "board": board_info,
        "parts": {"total": len(components), "placed": len(placed), "unplaced": unplaced},
        "ratsnest": ratsnest,
        "overlaps": overlaps[:LIST_LIMIT],
        "outside": outside,
        "decoupling": decoupling,
        "edge_parts": edge_parts,
        "routing": {**routing, "acute_corners": routing["acute_corners"][:LIST_LIMIT]},
        "summary": summary,
    }


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Per summary key: the baseline, the current value and the change (current minus
    baseline). Lower is better for every key but `density`, which is neither."""
    before = baseline.get("summary") or {}
    after = current.get("summary") or {}
    delta: dict[str, Any] = {}
    for key in SUMMARY_KEYS:
        old, new = before.get(key), after.get(key)
        change = None
        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            change = round(new - old, 3)
        delta[key] = {"before": old, "after": new, "change": change}
    return delta
