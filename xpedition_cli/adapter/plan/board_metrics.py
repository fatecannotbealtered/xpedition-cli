"""Numbers for how good a board's placement and routing are, from the board as `pcb get` reads it.

DRC says whether a board breaks a rule; it says nothing about whether a placement is
good. An agent that moves a part needs to know if the board got better, so this module
measures what a layout engineer looks at, as numbers that can be compared between two
states of the same board:

- the ratsnest: per net, the shortest tree through its pins (the airwires as if nothing
  were routed), and how often signal airwires of different nets cross;
- parts whose extents overlap, stick out of the board, or are not placed;
- each decoupling capacitor's distance to the nearest IC pin on its supply net;
- each connector's distance to the nearest board edge, and the placement density;
- the routing: trace length per layer, vias, and corners sharper than 90 degrees;
- each differential pair's two routed lengths, the pairs recognised by name (`USB_DP` and
  `USB_DN`, `TX1_P` and `TX1_N`, `CLK+` and `CLK-`) and listed, so a wrong guess is visible.

`board_rules` turns them into findings, with the silkscreen's own: designators over
pads or holes, on each other, across silkscreen lines, under other parts, past the
board's edge or too small to print, and silkscreen lines over pads; and on a flex board,
what its bend zones and stiffeners forbid (`flex_rules`).

Pure Python: no Layout. Extents are Layout's component extrema, a proxy for the
courtyard; supply and ground nets are recognised by name and listed, so a wrong guess
is visible.
"""

from __future__ import annotations

import math
import re
from itertools import combinations
from typing import Any

from .shapes import Outline, box_disc, cross, flatten, overlap

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
    "pair_skew_max_mm",
)
PAIR_ENDS = (("P", ("N", "M")), ("+", ("-",)))  # the last character of a pair's first net, and of its second by preference
SKEW_LIMIT_MM = 0.127  # 5 mil between a pair's two lengths
SQUARE_TOLERANCE = 5.0  # degrees a trace may cross a bend zone off square
INSIDE_EPSILON = 0.01  # mm a point lies inside a zone's edge to count as in it
BAND_COVER = 0.8  # a placement keepout across this share of the board's width marks a bend zone


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
    by_net: dict[str, float] = {}
    acute: list[dict[str, Any]] = []
    for trace in model.get("traces") or []:
        points = _trace_points(trace)
        layer = str(trace.get("layer") or "")
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:], strict=False))
        by_layer[layer] = by_layer.get(layer, 0.0) + length
        net = str(trace.get("net") or "")
        by_net[net] = by_net.get(net, 0.0) + length
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
    unrouted = {str(row.get("net")) for row in model.get("unrouted") or []}
    pairs = []
    for first, second in _pairs(sorted(name for name in by_net if name)):
        if first in unrouted or second in unrouted:
            continue
        a, b = by_net[first], by_net[second]
        pairs.append({"nets": [first, second], "mm": [round(a, 3), round(b, 3)], "skew_mm": round(abs(a - b), 3)})
    pairs.sort(key=lambda row: (-row["skew_mm"], row["nets"]))
    return {
        "trace_mm": round(sum(by_layer.values()), 3),
        "by_layer": {layer: round(length, 3) for layer, length in sorted(by_layer.items())},
        "vias": len(model.get("vias") or []),
        "acute_corners": acute,
        "pairs": pairs,
    }


def _trace_points(trace: dict[str, Any]) -> list[Point]:
    # an arc's row carries its centre and radius: lengths and corners follow the arc, not its centre
    rows = trace.get("path") or []
    return flatten([(float(row[0]), float(row[1]), float(row[2]) if len(row) > 2 else 0.0) for row in rows])


GUARD_STEP_MM = 0.1  # a trace is looked at in pieces this long for the ground beside it
GROUP_REACH_MM = 1.0  # a net of the same group, such as a pair's other half, covers a side it runs this near


def _segment_cells(traces: list[dict[str, Any]]) -> dict[tuple[int, int, int], list[tuple[Point, Point, float]]]:
    cells: dict[tuple[int, int, int], list[tuple[Point, Point, float]]] = {}
    for trace in traces:
        points = _trace_points(trace)
        width, layer = float(trace.get("width") or 0.0), int(trace.get("layer") or 0)
        for a, b in zip(points, points[1:], strict=False):
            for i in range(math.floor(min(a[0], b[0])), math.floor(max(a[0], b[0])) + 1):
                for j in range(math.floor(min(a[1], b[1])), math.floor(max(a[1], b[1])) + 1):
                    cells.setdefault((layer, i, j), []).append((a, b, width))
    return cells


def guard_cover(model: dict[str, Any], groups: list[list[str]], gap: float) -> list[dict[str, Any]]:
    """For each net named and each layer it runs on: how much of its trace length has a ground
    trace on its layer within ``gap`` of its copper on the left, on the right and on both sides,
    in per cent, and how many mm have none on either side. Ground is a net named as ground, such
    as GND; its planes do not count, only its traces, such as guards beside a signal. Nets named
    as one group, such as a pair, cover each other: a member's side towards another running within
    ``GROUP_REACH_MM`` of its copper counts as guarded, however far apart the pair's spacing is set."""
    traces = model.get("traces") or []
    cells = _segment_cells([t for t in traces if GROUND_NET.match(str(t.get("net") or ""))])
    rows: list[dict[str, Any]] = []
    for group in groups:
        for net in group:
            rows += _net_cover(traces, net, [t for t in traces if t.get("net") in group and t.get("net") != net], cells, gap)
    return rows


def _net_cover(traces: list[dict[str, Any]], net: str, partners: list[dict[str, Any]],
               cells: dict[tuple[int, int, int], list[tuple[Point, Point, float]]], gap: float) -> list[dict[str, Any]]:
    near = _segment_cells(partners) if partners else {}
    rows: list[dict[str, Any]] = []
    mine = [t for t in traces if t.get("net") == net]
    for layer in sorted({int(t.get("layer") or 0) for t in mine}):
        total = left = right = both = 0.0
        for trace in (t for t in mine if int(t.get("layer") or 0) == layer):
            points, width = _trace_points(trace), float(trace.get("width") or 0.0)
            for a, b in zip(points, points[1:], strict=False):
                length = math.dist(a, b)
                if length <= 0:
                    continue
                u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
                count = max(1, math.ceil(length / GUARD_STEP_MM))
                for k in range(count):
                    p = (a[0] + u[0] * length * (k + 0.5) / count, a[1] + u[1] * length * (k + 0.5) / count)
                    sides = _ground_sides(p, u, width, gap, layer, cells) | _ground_sides(p, u, width, GROUP_REACH_MM, layer, near)
                    piece = length / count
                    total += piece
                    left += piece if "left" in sides else 0.0
                    right += piece if "right" in sides else 0.0
                    both += piece if len(sides) == 2 else 0.0
        if total:
            rows.append({"net": net, "layer": layer, "length_mm": round(total, 3), "both_sides_pct": round(100 * both / total, 1),
                         "left_pct": round(100 * left / total, 1), "right_pct": round(100 * right / total, 1),
                         "unguarded_mm": round(total - left - right + both, 3)})
    return rows


def _ground_sides(p: Point, u: Point, width: float, gap: float, layer: int,
                  cells: dict[tuple[int, int, int], list[tuple[Point, Point, float]]]) -> set[str]:
    """The sides of a trace at ``p``, running along ``u``, that have a ground trace within ``gap`` of its copper."""
    reach = width / 2 + gap + 1.0
    sides: set[str] = set()
    for i in range(math.floor(p[0] - reach), math.floor(p[0] + reach) + 1):
        for j in range(math.floor(p[1] - reach), math.floor(p[1] + reach) + 1):
            for a, b, ground_width in cells.get((layer, i, j), ()):
                dx, dy = b[0] - a[0], b[1] - a[1]
                length2 = dx * dx + dy * dy
                t = 0.0 if not length2 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
                q = (a[0] + dx * t, a[1] + dy * t)
                if abs((q[0] - p[0]) * u[0] + (q[1] - p[1]) * u[1]) >= GUARD_STEP_MM / 2 - 1e-9:
                    continue  # not beside this piece but ahead of or behind it, such as past a guard's end
                distance = math.dist(p, q)
                if distance - width / 2 - ground_width / 2 <= gap + 1e-6:
                    sides.add("left" if u[0] * (q[1] - p[1]) - u[1] * (q[0] - p[0]) > 0 else "right")
    return sides


def _pairs(names: list[str]) -> list[tuple[str, str]]:
    """The differential pairs among the routed nets, by name: two nets whose names differ only in a
    last character of ``PAIR_ENDS``."""
    by_upper = {name.upper(): name for name in names}
    pairs = []
    for upper, name in sorted(by_upper.items()):
        for plus, minuses in PAIR_ENDS:
            if len(upper) >= 2 and upper.endswith(plus):
                other = next((by_upper[upper[:-1] + minus] for minus in minuses if upper[:-1] + minus in by_upper), None)
                if other is not None:
                    pairs.append((name, other))
    return pairs


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
                f"trace corner of {row['degrees']} degrees at {_at(row['at'])} on layer {row['layer']}",
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
    for row in metrics["routing"]["pairs"]:
        if row["skew_mm"] > SKEW_LIMIT_MM:
            findings.append(_rule("pair_skew", "medium", f"the pair's lengths differ by {row['skew_mm']} mm, {row['mm'][0]} and "
                                  f"{row['mm'][1]} mm (limit {SKEW_LIMIT_MM:g} mm; a pair recognised by name)",
                                  net=row["nets"][0], other=row["nets"][1]))
    findings += label_rules(model, min_text_height)
    findings += silk_rules(model)
    findings += flex_rules(model)
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda row: (order.get(row["severity"], 3), row["rule"]))
    return findings


# -- flex boards ---------------------------------------------------------------------------


def _at(point: Any) -> str:
    return f"({point[0]:g}, {point[1]:g}) mm"


def _zone_text(zone: Rect) -> str:
    return f"x {zone[0]:g} to {zone[2]:g}, y {zone[1]:g} to {zone[3]:g} mm"


def _outline_points(model: dict[str, Any]) -> list[Point]:
    for record in model.get("outline") or []:
        rows = record.get("path") or []
        if rows:
            return flatten([(float(r[0]), float(r[1]), float(r[2]) if len(r) > 2 else 0.0) for r in rows])
    return []


def _chord(outline: list[Point], along_x: bool, at: float, centre: float) -> tuple[float, float] | None:
    """The board's span through a point: along x at y ``at`` (or along y at x ``at``), the part of
    the line inside the outline that holds ``centre``; None off the board."""
    hits = []
    for (x1, y1), (x2, y2) in zip(outline, outline[1:] + outline[:1], strict=True):
        a1, a2, b1, b2 = (y1, y2, x1, x2) if along_x else (x1, x2, y1, y2)
        if (a1 > at) != (a2 > at):
            hits.append(b1 + (at - a1) * (b2 - b1) / (a2 - a1))
    hits.sort()
    return next(((low, high) for low, high in zip(hits[0::2], hits[1::2], strict=False) if low <= centre <= high), None)


def _across(box: Rect, outline: list[Point]) -> tuple[Rect, bool, float] | None:
    """The band a box makes across the board: the box widened to the board's edges along the axis
    where it covers more of the board's width, whether that axis is x, and the share it covers."""
    best: tuple[Rect, bool, float] | None = None
    for along_x in (True, False):
        if along_x:
            chord = _chord(outline, True, (box[1] + box[3]) / 2, (box[0] + box[2]) / 2)
            band = ((chord[0], box[1], chord[1], box[3]), True, (box[2] - box[0]) / (chord[1] - chord[0])) if chord else None
        else:
            chord = _chord(outline, False, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            band = ((box[0], chord[0], box[2], chord[1]), False, (box[3] - box[1]) / (chord[1] - chord[0])) if chord else None
        if band is not None and (best is None or band[2] > best[2]):
            best = band
    return best


def bend_zones(model: dict[str, Any]) -> list[dict[str, Any]]:
    """The bend zones, each a band across the board, with the axis it runs along (traces cross it
    along the other) and what marks it: ``hatched_plane``, a hatched plane shape of a net that is
    solid elsewhere on the same layer; ``placement_keepout``, on a board with stiffeners, a
    placement keepout of no height at least twice as long as wide. Either must span most of the
    board's width, so a hatched patch on a rigid board is no bend. Each band reaches the board's
    edges along its length; bands that overlap are one zone."""
    outline = _outline_points(model)
    if not outline:
        return []
    found: list[tuple[Rect, bool, str]] = []
    planes = [p for p in model.get("planes") or [] if p.get("extents_mm")]
    solid = {(p.get("net"), p.get("layer")) for p in planes if p.get("hatch") == "solid"}
    marks: list[tuple[dict[str, Any], str]] = [(p, "hatched_plane") for p in planes
                                               if p.get("hatch") not in (None, "solid") and (p.get("net"), p.get("layer")) in solid]
    if stiffeners(model):
        marks += [(k, "placement_keepout") for k in model.get("keepouts") or []
                  if k.get("kind") == "placement" and not k.get("height_mm") and k.get("extents_mm")]
    for item, mark in marks:
        x0, y0, x1, y1 = (float(v) for v in item["extents_mm"])
        long, short = max(x1 - x0, y1 - y0), min(x1 - x0, y1 - y0)
        if mark == "placement_keepout" and not (short > 0 and long >= 2 * short):
            continue
        band = _across((x0, y0, x1, y1), outline)
        if band is not None and band[2] >= BAND_COVER:
            found.append((band[0], band[1], mark))
    zones: list[tuple[Rect, bool, set[str]]] = []
    for box, along_x, mark in found:
        named = {mark}
        for other in [z for z in zones if overlap(z[0], box) > 0]:
            zones.remove(other)
            box = (min(box[0], other[0][0]), min(box[1], other[0][1]), max(box[2], other[0][2]), max(box[3], other[0][3]))
            along_x, named = other[1], named | other[2]
        zones.append((box, along_x, named))
    return [{"extents_mm": [round(v, 3) for v in box], "along": "x" if along_x else "y", "marked_by": sorted(named)}
            for box, along_x, named in sorted(zones, key=lambda z: z[0])]


def stiffeners(model: dict[str, Any]) -> list[Rect]:
    """The extents of the placement keepouts that limit height, a flex board's stiffeners."""
    boxes: list[Rect] = []
    for keepout in model.get("keepouts") or []:
        if keepout.get("kind") == "placement" and keepout.get("height_mm") and keepout.get("extents_mm"):
            x0, y0, x1, y1 = (float(v) for v in keepout["extents_mm"])
            boxes.append((x0, y0, x1, y1))
    return boxes


def _inside(point: Point, zone: Rect, epsilon: float = INSIDE_EPSILON) -> bool:
    return zone[0] + epsilon < point[0] < zone[2] - epsilon and zone[1] + epsilon < point[1] < zone[3] - epsilon


def _clipped(a: Point, b: Point, zone: Rect) -> tuple[Point, Point] | None:
    """The part of segment a-b inside the zone (Liang-Barsky), or None."""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - zone[0]), (dx, zone[2] - a[0]), (-dy, a[1] - zone[1]), (dy, zone[3] - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    if t1 - t0 <= 1e-9:
        return None
    return (a[0] + dx * t0, a[1] + dy * t0), (a[0] + dx * t1, a[1] + dy * t1)


def _turn(before: Point, vertex: Point, after: Point) -> float:
    """How many degrees a path turns at a vertex; 0 when it runs straight on."""
    first = math.atan2(vertex[1] - before[1], vertex[0] - before[0])
    second = math.atan2(after[1] - vertex[1], after[0] - vertex[0])
    return abs(math.degrees((second - first + math.pi) % (2 * math.pi) - math.pi))


def _via_at(via: dict[str, Any]) -> Point:
    return float(via["circle"][0]), float(via["circle"][1])


def flex_rules(model: dict[str, Any]) -> list[dict[str, Any]]:
    """What a flex board's bend zones and stiffeners forbid (knowledge
    ``layout-flex-bend-and-stiffener``): a via in a bend zone; a trace that turns, changes width or
    crosses off square inside one; traces of two layers straight above each other across one; and,
    where the board has stiffeners, a via of a net without plane shapes, a layer change, outside them."""
    zones: list[Rect] = []
    along: dict[Rect, bool] = {}
    for zone in bend_zones(model):
        x0, y0, x1, y1 = zone["extents_mm"]
        zones.append((x0, y0, x1, y1))
        along[(x0, y0, x1, y1)] = zone["along"] == "x"
    if not zones:
        return []
    findings: list[dict[str, Any]] = []
    for via in model.get("vias") or []:
        at = _via_at(via)
        for zone in zones:
            if _inside(at, zone, 0.0):
                findings.append(_rule("bend_via", "high", f"via at {_at(at)} in the bend zone {_zone_text(zone)}, which takes no vias",
                                      net=via.get("net"), at=list(at)))
    crossing: dict[Rect, list[tuple[int, str, float, float]]] = {zone: [] for zone in zones}
    ends: dict[tuple[Rect, str, int, Point], set[float]] = {}
    turns: dict[tuple[Rect, str, int], Point] = {}
    angles: dict[tuple[Rect, str, int], float] = {}
    for trace in model.get("traces") or []:
        points = _trace_points(trace)
        net, layer, width = str(trace.get("net") or ""), int(trace.get("layer") or 0), float(trace.get("width") or 0.0)
        for zone in zones:
            turned, off = _crossing(points, net, layer, width, zone, along[zone], crossing[zone])
            if turned is not None:
                turns.setdefault((zone, net, layer), turned)
            if off > SQUARE_TOLERANCE:
                angles[(zone, net, layer)] = max(off, angles.get((zone, net, layer), 0.0))
            for end in (points[0], points[-1]) if points else ():
                if _inside(end, zone):
                    ends.setdefault((zone, net, layer, (round(end[0], 3), round(end[1], 3))), set()).add(round(width, 4))
    for (zone, net, layer), at in turns.items():
        findings.append(_rule("bend_turn", "medium", f"trace turns at {_at(at)} on layer {layer} inside the bend zone {_zone_text(zone)}",
                              net=net, at=list(at)))
    for (zone, net, layer), off in angles.items():
        findings.append(_rule("bend_angle", "medium", f"trace crosses the bend zone {_zone_text(zone)} {off:.1f} degrees off square "
                              f"on layer {layer}", net=net))
    for (zone, net, layer, at), widths in sorted(ends.items(), key=lambda item: (item[0][1], item[0][2], item[0][3])):
        if len(widths) > 1:
            text = " and ".join(f"{w:g}" for w in sorted(widths))
            findings.append(_rule("bend_width", "medium", f"trace width changes, {text} mm, at {_at(at)} on layer {layer} inside the "
                                  f"bend zone {_zone_text(zone)}", net=net, at=list(at)))
    for zone, rows in crossing.items():
        meshed = _meshed(model, zone)  # a trace of a net hatched over the zone on its layer is part of that mesh
        rows = [row for row in rows if (row[1], row[0]) not in meshed]
        stacked = sorted({(la, na, lb, nb) for (la, na, ca, wa), (lb, nb, cb, wb) in combinations(sorted(rows), 2)
                          if la != lb and abs(ca - cb) < (wa + wb) / 2})
        if stacked:
            la, na, lb, nb = stacked[0]
            findings.append(_rule("bend_stacked", "low", f"{len(stacked)} pairs of traces on two layers lie straight above each other "
                                  f"across the bend zone {_zone_text(zone)}, such as {na} on layer {la} and {nb} on layer {lb}; "
                                  "stagger them where the widths allow", net=na, other=nb))
    rigid = stiffeners(model)
    if rigid:
        plane_nets = {p.get("net") for p in model.get("planes") or []}
        for via in model.get("vias") or []:
            at = _via_at(via)
            if via.get("net") not in plane_nets and not any(_inside(at, box, -INSIDE_EPSILON) for box in rigid):
                findings.append(_rule("layer_change_outside_stiffener", "low", f"via at {_at(at)} changes layers outside the stiffeners",
                                      net=via.get("net"), at=list(at)))
    return findings


def _meshed(model: dict[str, Any], zone: Rect) -> set[tuple[str, int]]:
    """The nets and layers whose hatched plane shapes cover the bend zone."""
    area = max((zone[2] - zone[0]) * (zone[3] - zone[1]), 1e-9)
    covered: dict[tuple[str, int], float] = {}
    for plane in model.get("planes") or []:
        box = plane.get("extents_mm")
        if not box or plane.get("hatch") in (None, "solid"):
            continue
        key = (str(plane.get("net") or ""), int(plane.get("layer") or 0))
        covered[key] = covered.get(key, 0.0) + overlap(tuple(box), zone)
    return {key for key, value in covered.items() if value >= 0.9 * area}


def _crossing(points: list[Point], net: str, layer: int, width: float, zone: Rect, along_x: bool,
              rows: list[tuple[int, str, float, float]]) -> tuple[Point | None, float]:
    """Where one trace first turns inside one bend zone, or None, and how many degrees off square
    it runs across it at most; each square run is added to ``rows`` as (layer, net, where it
    crosses, width). A band that runs along x is crossed in y."""
    turned = next((v for b, v, a in zip(points, points[1:], points[2:], strict=False)
                   if _inside(v, zone) and _turn(b, v, a) > ACUTE_TOLERANCE), None)
    off = 0.0
    for a, b in zip(points, points[1:], strict=False):
        part = _clipped(a, b, zone)
        if part is None or math.dist(*part) < 0.05 or not _inside(((part[0][0] + part[1][0]) / 2, (part[0][1] + part[1][1]) / 2), zone):
            continue  # a run along the zone's edge, outside it, is not in it
        dx, dy = abs(part[1][0] - part[0][0]), abs(part[1][1] - part[0][1])
        degrees = math.degrees(math.atan2(dx, dy) if along_x else math.atan2(dy, dx))
        off = max(off, degrees)
        if degrees <= SQUARE_TOLERANCE:
            rows.append((layer, net, (part[0][0] + part[1][0]) / 2 if along_x else (part[0][1] + part[1][1]) / 2, width))
    return turned, off


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
        "pair_skew_max_mm": routing["pairs"][0]["skew_mm"] if routing["pairs"] else None,
    }
    return {
        "board": board_info,
        "parts": {"total": len(components), "placed": len(placed), "unplaced": unplaced},
        "ratsnest": ratsnest,
        "overlaps": overlaps[:LIST_LIMIT],
        "outside": outside,
        "decoupling": decoupling,
        "edge_parts": edge_parts,
        "routing": {**routing, "acute_corners": routing["acute_corners"][:LIST_LIMIT], "pairs": routing["pairs"][:LIST_LIMIT]},
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
