"""Numbers for how good a board's placement and routing are, from `pcb snapshot`'s file.

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

`board_rules` turns them into findings, with the silkscreen's own: designators over
pads or holes, on each other, across silkscreen lines, under other parts, past the
board's edge or too small to print, and silkscreen lines over pads.

Pure Python: no Layout. Extents are Layout's component extrema, a proxy for the
courtyard; supply and ground nets are recognised by name and listed, so a wrong guess
is visible.
"""

from __future__ import annotations

import math
import re
from itertools import combinations
from typing import Any

from .shapes import Outline, box_disc, cross, overlap

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
        if net_a != net_b and cross(edge_a, edge_b)
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


DECOUPLING_LIMIT_MM = 5.0  # a capacitor farther than this from its IC's supply pin
EDGE_LIMIT_MM = 5.0  # a connector farther than this from the nearest board edge
MIN_TEXT_HEIGHT_MM = 0.6  # silkscreen text lower than this prints hard to read
MIN_TEXT_PEN_MM = 0.1  # and strokes thinner than this break up


def _rule(rule: str, severity: str, finding: str, **extra: Any) -> dict[str, Any]:
    return {"rule": rule, "severity": severity, "finding": finding, **extra}


# -- silkscreen texts ---------------------------------------------------------------------


def _text_box(text: dict[str, Any]) -> Rect | None:
    """A silkscreen text's box as Layout gives it (`extents`, the pen included)."""
    extents = text.get("extents")
    if not isinstance(extents, (list, tuple)) or len(extents) != 4:
        return None
    x1, y1, x2, y2 = (float(value) for value in extents)
    return (x1, y1, x2, y2) if x1 < x2 and y1 < y2 else None


def _text_name(text: dict[str, Any]) -> str:
    string = str(text.get("text") or "")
    if text.get("kind") == "refdes":
        return f"designator {string}"
    return f"silkscreen text {string!r}"


def _centre(box: Rect) -> list[float]:
    return [round((box[0] + box[2]) / 2, 3), round((box[1] + box[3]) / 2, 3)]


def _bare_holes(model: dict[str, Any], pads: list[Outline]) -> list[tuple[float, ...]]:
    """The holes with no copper around them on a side: not a pin's (inside one of its
    `pads`) and not a via's. A text over a pad's hole is reported for the pad."""
    vias = set()
    for via in model.get("vias") or []:
        if via.get("circle") or via.get("path"):
            x, y = Outline(via).centre()
            vias.add((round(x, 2), round(y, 2)))
    bare = []
    for row in model.get("holes") or []:
        x, y, size = (float(value) for value in row[:3])
        if (round(x, 2), round(y, 2)) in vias:
            continue
        spot = (x - 0.001, y - 0.001, x + 0.001, y + 0.001)
        if any(pad.reaches(spot) for pad in pads):
            continue
        bare.append((x, y, size))
    return bare


def label_rules(
    model: dict[str, Any], min_text_height: float = MIN_TEXT_HEIGHT_MM
) -> list[dict[str, Any]]:
    """What makes the silkscreen texts hard to read, each text by the box Layout gives
    it: over a pad or a bare hole of its side (the board house clips the ink away), on
    another text, past the board's edge, across a silkscreen line, under another part
    (hidden once that part is fitted) or lower than `min_text_height` mm."""
    layers = int(model.get("layers") or 2)
    board = _bounds(model)
    texts = [(text, box) for text in model.get("texts") or [] if (box := _text_box(text))]
    placed = [
        part for part in model.get("components") or [] if part.get("placed") and part.get("extents")
    ]
    findings: list[dict[str, Any]] = []
    for side in ("top", "bottom"):
        layer = layers if side == "bottom" else 1
        pads = [
            Outline(pad) for pad in model.get("pads") or [] if int(pad.get("layer") or 0) == layer
        ]
        holes = _bare_holes(model, pads)
        silk = [Outline(item) for item in model.get("silk") or [] if str(item.get("side")) == side]
        on_side = [(text, box) for text, box in texts if str(text.get("side") or "top") == side]
        parts = [
            (str(part.get("refdes")), tuple(float(value) for value in part["extents"]))
            for part in placed
            if str(part.get("side") or "top") == side
        ]
        for text, box in on_side:
            owner = text.get("refdes")
            name = _text_name(text)
            where = {"refdes": owner, "text": str(text.get("text") or ""), "at": _centre(box)}
            for pad in pads:
                if pad.reaches(box):
                    shape = pad.shape
                    other = (
                        f"{shape.get('refdes')}.{shape.get('pin')}" if shape.get("pin") else "a pad"
                    )
                    findings.append(
                        _rule(
                            "label_over_pad",
                            "medium",
                            f"{name} runs over pad {other}",
                            other=other,
                            **where,
                        )
                    )
            for x, y, size in holes:
                if box_disc(box, x, y, size / 2):
                    other = f"hole at {x:.2f},{y:.2f}"
                    findings.append(
                        _rule(
                            "label_over_pad",
                            "medium",
                            f"{name} runs over the {other}",
                            other=other,
                            **where,
                        )
                    )
            crossed = {
                str(item.shape.get("refdes") or "the board") for item in silk if item.reaches(box)
            }
            for other in sorted(crossed):
                findings.append(
                    _rule(
                        "label_over_silk",
                        "low",
                        f"{name} crosses a silkscreen line of {other}",
                        other=other,
                        **where,
                    )
                )
            for refdes, extents in parts:
                if refdes == owner:
                    continue
                area = overlap(box, extents)  # type: ignore[arg-type]
                if area > AREA_EPSILON:
                    findings.append(
                        _rule(
                            "label_under_part",
                            "low",
                            f"{name} lies on {refdes}'s place ({round(area, 3)} mm2), hidden "
                            "once that part is fitted",
                            other=refdes,
                            **where,
                        )
                    )
            if board is not None:
                beyond = -_edge_distance(box, board)
                if beyond > DISTANCE_EPSILON:
                    findings.append(
                        _rule(
                            "label_off_board",
                            "medium",
                            f"{name} reaches {round(beyond, 3)} mm past the board edge",
                            **where,
                        )
                    )
            height, pen = float(text.get("height") or 0.0), float(text.get("pen") or 0.0)
            if height < min_text_height - 1e-6 or 0 < pen < MIN_TEXT_PEN_MM - 1e-6:
                findings.append(
                    _rule(
                        "label_small",
                        "low",
                        f"{name} is {round(height, 3)} mm high with a {round(pen, 3)} mm pen "
                        f"(at least {min_text_height:g} mm and {MIN_TEXT_PEN_MM:g} mm print "
                        "readably)",
                        **where,
                    )
                )
        for (text_a, box_a), (text_b, box_b) in combinations(on_side, 2):
            area = overlap(box_a, box_b)
            if area > AREA_EPSILON:
                findings.append(
                    _rule(
                        "label_overlap",
                        "medium",
                        f"{_text_name(text_a)} and {_text_name(text_b)} overlap by "
                        f"{round(area, 3)} mm2",
                        refdes=text_a.get("refdes"),
                        text=str(text_a.get("text") or ""),
                        other=str(text_b.get("text") or ""),
                        at=_centre(box_a),
                    )
                )
    return findings


PADS_LISTED = 10  # pads named in one finding; the count stays whole


def silk_rules(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Silkscreen lines over pads of their side: the board house clips silkscreen off
    exposed copper, so the line is printed in pieces (and ink on a pad hurts the
    solder joint where it is not clipped). One finding per part's silkscreen and the
    part whose pads it crosses; the board's own silkscreen is `the board`."""
    layers = int(model.get("layers") or 2)
    crossings: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for side in ("top", "bottom"):
        layer = layers if side == "bottom" else 1
        pads = [
            Outline(pad) for pad in model.get("pads") or [] if int(pad.get("layer") or 0) == layer
        ]
        for item in model.get("silk") or []:
            if str(item.get("side") or "top") != side:
                continue
            line = Outline(item)
            owner = str(item.get("refdes") or "the board")
            for pad in pads:
                if line.crosses(pad):
                    key = (owner, str(pad.shape.get("refdes") or ""))
                    crossed = crossings.setdefault(key, [])
                    if pad.shape not in crossed:
                        crossed.append(pad.shape)
    findings: list[dict[str, Any]] = []
    for (owner, part), pads in sorted(crossings.items()):
        names = [str(pad.get("pin") or "?") for pad in pads]
        names = list(dict.fromkeys(names))
        whose = "its" if part == owner else f"{part}'s"
        shown = ", ".join(names[:PADS_LISTED]) + (" ..." if len(names) > PADS_LISTED else "")
        centre = Outline(pads[0]).centre()
        findings.append(
            _rule(
                "silk_over_pad",
                "low",
                f"{owner}'s silkscreen runs over {whose} pads {shown}; the board house clips "
                "it off the copper",
                refdes=None if owner == "the board" else owner,
                other=part,
                pins=names,
                at=[round(centre[0], 3), round(centre[1], 3)],
            )
        )
    return findings


def board_rules(
    model: dict[str, Any], min_text_height: float = MIN_TEXT_HEIGHT_MM
) -> list[dict[str, Any]]:
    """What is wrong with a board beyond its DRC, from the same model the metrics use:
    parts not placed, off the board or on top of each other, decoupling capacitors far
    from the IC they serve, connectors far from an edge, acute trace corners, nets
    still open, silkscreen texts that will not read (`label_rules`) and silkscreen
    lines over pads (`silk_rules`)."""
    metrics = measure(model)
    findings: list[dict[str, Any]] = []
    if model.get("connectivity", {}).get("complete") is False:
        findings.append(
            _rule(
                "connectivity_unobserved",
                "high",
                (
                    "Native connectivity could not be completely read; empty findings do not "
                    "prove zero opens"
                ),
            )
        )
    for refdes in metrics["parts"]["unplaced"]:
        findings.append(_rule("unplaced", "high", "part is not placed", refdes=refdes))
    for row in metrics["outside"]:
        findings.append(
            _rule(
                "outside",
                "high",
                f"part reaches {row['beyond_mm']} mm past the board edge",
                refdes=row["refdes"],
            )
        )
    for row in metrics["overlaps"]:
        findings.append(
            _rule(
                "overlap",
                "medium",
                f"{row['a']} and {row['b']} overlap by {row['mm2']} mm2",
                refdes=row["a"],
                other=row["b"],
            )
        )
    for row in metrics["decoupling"]:
        if row["mm"] > DECOUPLING_LIMIT_MM:
            findings.append(
                _rule(
                    "decoupling",
                    "medium",
                    f"decoupling capacitor is {row['mm']} mm from {row['nearest']}'s "
                    f"{row['net']} pin (limit {DECOUPLING_LIMIT_MM:g} mm)",
                    refdes=row["capacitor"],
                    net=row["net"],
                )
            )
    for row in metrics["edge_parts"]:
        if row["edge_mm"] > EDGE_LIMIT_MM:
            findings.append(
                _rule(
                    "connector_edge",
                    "low",
                    f"connector is {row['edge_mm']} mm from the nearest board edge",
                    refdes=row["refdes"],
                )
            )
    for row in metrics["routing"]["acute_corners"]:
        findings.append(
            _rule(
                "acute_corner",
                "low",
                f"trace corner of {row['degrees']} degrees",
                net=row["net"],
                at=row["at"],
                layer=row["layer"],
            )
        )
    for row in model.get("unrouted") or []:
        findings.append(
            _rule(
                "unrouted",
                "high",
                f"net has {row['opens']} open connections",
                net=row["net"],
            )
        )
    findings += label_rules(model, min_text_height)
    findings += silk_rules(model)
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda row: (order.get(row["severity"], 3), row["rule"]))
    return findings


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
        area = overlap(rect_a, rect_b)  # type: ignore[arg-type]
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
