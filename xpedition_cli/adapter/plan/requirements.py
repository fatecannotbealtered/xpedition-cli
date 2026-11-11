"""The requirements a reviewer sets for a board, as checks that can be measured.

A requirements file is JSON, ``{"requirements": [...]}``; each requirement has a ``kind``, the
values that kind takes, and, if wanted, a ``label``, the reviewer's own words, which its result
repeats. The kinds:

- ``guard``: ground on both sides of nets, ``nets`` (a pair or a group guarded as one joined with
  ``+``, such as ``TX_P+TX_N``), ``min_both_pct`` and ``gap_mm`` (0.3 unless given);
- ``ring``: a trace of ``net`` round the board ``offset_mm`` inside the outline on ``layers``,
  covering at least ``min_pct`` of the way round, a trace counting within ``tolerance_mm`` of
  the line (0.05 unless given);
- ``via_padstack``: every via, or every via of ``nets``, uses ``padstack``;
- ``via_pitch``: along every trace of ``net`` at least ``min_length_mm`` long (2 unless given),
  vias at most ``max_mm`` apart, the trace's ends counting as vias; a longer gap where no via
  fits with ``ROOM_MM`` to spare, such as between two signals or across a bend zone, is listed
  apart and does not fail;
- ``plane``: ``net`` poured on every one of ``layers``, hatched over each bend zone with
  ``hatched_in_bends``.

Each result gives what was measured against what is needed, whether it passes, and the worst
places. The measure takes a board model as ``board_metrics`` reads it: the board's own (pcb
check), or the board's copper with a routing plan's laid over it (trace check), which has no
planes yet, so a ``plane`` requirement is left unmeasured there.

Pure Python, in mm.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from . import PlanError, board_metrics
from . import guards as GD
from . import lanes as LN

LISTED = 3  # the worst places each result lists; the counts are whole
GUARD_SHORT_MM = 2.0  # a guarded net's stretch on a layer shorter than this, such as a stub at a connector, is left out
STEP_MM = 0.1  # a line is measured in pieces this long
ROOM_MM = 0.04  # a via counts as fitting a gap only with this much to spare, so that laying vias LAY_STEP_MM apart finds every spot this finds
END_JOIN_MM = 1e-3  # trace ends this close are one point
KEYS = {
    "guard": ({"nets", "min_both_pct"}, {"gap_mm"}),
    "ring": ({"net", "layers", "offset_mm", "min_pct"}, {"tolerance_mm"}),
    "via_padstack": ({"padstack"}, {"nets"}),
    "via_pitch": ({"net", "max_mm"}, {"min_length_mm"}),
    "plane": ({"net", "layers"}, {"hatched_in_bends"}),
}
Point = tuple[float, float]
Box = tuple[float, float, float, float]


@dataclass
class Requirement:
    index: int
    kind: str
    label: str
    values: dict[str, Any]


def _error(message: str) -> PlanError:
    return PlanError("E_VALIDATION", f"The requirements file is not valid: {message}.")


def load(path: str) -> list[Requirement]:
    """The requirements of a file; E_NOT_FOUND when it cannot be read, E_VALIDATION when it is not one."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise PlanError("E_NOT_FOUND", f"The requirements file {path} cannot be read: {exc.strerror}.") from None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise _error(f"it is not JSON: {exc}") from None
    return parse(data)


def parse(data: Any) -> list[Requirement]:
    items = data.get("requirements") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise _error('it holds {"requirements": [...]}, a list of one or more requirements')
    out = []
    for index, item in enumerate(items):
        what = f"requirements[{index}]"
        if not isinstance(item, dict) or item.get("kind") not in KEYS:
            raise _error(f"{what} has a kind of {', '.join(KEYS)}")
        kind = item["kind"]
        needed, optional = KEYS[kind]
        missing = sorted(needed - set(item))
        extra = sorted(set(item) - needed - optional - {"kind", "label"})
        if missing or extra:
            raise _error(f"{what} ({kind}) takes {', '.join(sorted(needed))}" + (f" and, if wanted, {', '.join(sorted(optional))}"
                                                                                   if optional else "") + " and a label")
        values = {key: item[key] for key in needed | optional if key in item}
        out.append(Requirement(index, kind, str(item.get("label") or ""), _checked(kind, values, what)))
    return out


def _checked(kind: str, values: dict[str, Any], what: str) -> dict[str, Any]:
    def number(key: str, low: float, high: float, default: float | None = None) -> float:
        value = values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low < float(value) <= high:
            raise _error(f"{what} ({kind}) has {key}, a number of more than {low:g} and at most {high:g}")
        return float(value)

    def percent(key: str) -> float:
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 100:
            raise _error(f"{what} ({kind}) has {key}, a percentage from 0 to 100")
        return float(value)

    def name(key: str) -> str:
        value = values.get(key)
        if not isinstance(value, str) or not value.strip():
            raise _error(f"{what} ({kind}) names a {key}")
        return value.strip()

    def names(key: str) -> list[str]:
        value = values.get(key)
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
            raise _error(f"{what} ({kind}) has {key}, a list of one or more names")
        return [v.strip() for v in value]

    def layers() -> list[int]:
        value = values.get("layers")
        if not isinstance(value, list) or not value or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in value):
            raise _error(f"{what} ({kind}) has layers, a list of conductor layer numbers such as [1, 3]")
        return sorted(set(value))

    if kind == "guard":
        return {"nets": names("nets"), "min_both_pct": percent("min_both_pct"), "gap_mm": number("gap_mm", 0, 5, 0.3)}
    if kind == "ring":
        return {"net": name("net"), "layers": layers(), "offset_mm": number("offset_mm", 0, 50), "min_pct": percent("min_pct"),
                "tolerance_mm": number("tolerance_mm", 0, 1, 0.05)}
    if kind == "via_padstack":
        return {"padstack": name("padstack"), "nets": names("nets") if "nets" in values else None}
    if kind == "via_pitch":
        return {"net": name("net"), "max_mm": number("max_mm", 0, 100), "min_length_mm": number("min_length_mm", 0, 1000, 2.0)}
    hatched = values.get("hatched_in_bends", False)
    if not isinstance(hatched, bool):
        raise _error(f"{what} (plane) has hatched_in_bends, true or false")
    return {"net": name("net"), "layers": layers(), "hatched_in_bends": hatched}


# ------------------------------------------------------------------ measuring


def measure(model: dict[str, Any], requirements: list[Requirement], bends: list[Box],
            obstacles: GD.Obstacles | None = None, via_radius: float | None = None) -> list[dict[str, Any]]:
    """Each requirement's result on ``model``. ``obstacles``, all the board's copper, tells where a via
    could still stand along a gap a ``via_pitch`` requirement finds too long, on the trace's line or
    off it as far as a via along a guard may stand; the spots one requirement names keep clear of
    each other, so that they can all be taken together, and ``obstacles`` is left as it was.
    ``via_radius`` is the pad radius of such a via, the net's own vias' when not given."""
    out = []
    for item in requirements:
        row: dict[str, Any] = {"index": item.index, "kind": item.kind}
        if item.label:
            row["label"] = item.label
        try:
            row.update(MEASURES[item.kind](model, item.values, bends, obstacles, via_radius))
        except PlanError as exc:  # a requirement that cannot be measured on this board, such as a ring on a board without an outline
            row.update(ok=False, reason=exc.message.removeprefix("The routing plan is not valid: ").rstrip("."))
        out.append(row)
    return out


def listed(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The results as one response holds them: the first ``LISTED`` worst places of each; the
    counts stay whole, and the whole lists go to a file."""
    out = []
    for row in results:
        row = {**row}
        if isinstance(row.get("worst"), list):
            row["worst"] = row["worst"][:LISTED]
        if isinstance(row.get("no_room"), dict):
            row["no_room"] = {**row["no_room"], "worst": row["no_room"]["worst"][:LISTED]}
        out.append(row)
    return out


def _guard(model: dict[str, Any], values: dict[str, Any], _bends: list[Box], _obstacles: Any, _radius: Any) -> dict[str, Any]:
    traces = model.get("traces") or []
    known = {str(t.get("net") or "") for t in traces}
    rows: list[dict[str, Any]] = []
    for item in values["nets"]:
        group = [net.strip() for net in item.split("+") if net.strip()]
        rows += [{"net": net, "layer": None, "both_sides_pct": 0.0, "unguarded_mm": None, "problem": "no traces"}
                 for net in group if net not in known]
        present = [net for net in group if net in known]
        if present:
            rows += [r for r in board_metrics.guard_cover(model, [present], values["gap_mm"]) if r["length_mm"] >= GUARD_SHORT_MM]
    rows.sort(key=lambda r: (r["both_sides_pct"], r["net"], r["layer"] or 0))
    need = values["min_both_pct"]
    failing = [r for r in rows if r["both_sides_pct"] < need - 1e-9]
    worst = [{k: r[k] for k in ("net", "layer", "both_sides_pct", "unguarded_mm", "problem") if r.get(k) is not None} for r in rows]
    return {"ok": not failing, "measured_pct": rows[0]["both_sides_pct"] if rows else None, "needed_pct": need,
            "count": len(rows), "failed": len(failing), "worst": worst}


def _near_trace(p: Point, layer: int, cells: dict[tuple[int, int, int], list[tuple[Point, Point, float]]], tolerance: float) -> bool:
    for i in range(math.floor(p[0] - 1.5), math.floor(p[0] + 1.5) + 1):
        for j in range(math.floor(p[1] - 1.5), math.floor(p[1] + 1.5) + 1):
            for a, b, width in cells.get((layer, i, j), ()):
                if GD.point_segment(p, a, b) <= width / 2 + tolerance:
                    return True
    return False


def _ring(model: dict[str, Any], values: dict[str, Any], _bends: list[Box], _obstacles: Any, _radius: Any) -> dict[str, Any]:
    outline = (model.get("outline") or [{}])[0].get("path") or []
    if len(outline) < 3:
        raise PlanError("E_UNSUPPORTED", "the board has no outline to measure a ring along")
    chain, _left = GD.ring_chain(LN.outline_corners([(float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0)
                                                     for p in outline]), values["offset_mm"])
    cells = board_metrics._segment_cells([t for t in model.get("traces") or [] if t.get("net") == values["net"]])
    per_layer, gaps = [], []
    for layer in values["layers"]:
        total = covered = 0.0
        run: list[Any] = []  # an uncovered run: [from, to, length]
        for a, b in GD._pieces(chain.line, STEP_MM):
            length = math.dist(a, b)
            total += length
            if _near_trace(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), layer, cells, values["tolerance_mm"]):
                covered += length
                if run:
                    gaps.append({"layer": layer, "from_mm": _xy(run[0]), "to_mm": _xy(run[1]), "length_mm": round(run[2], 3)})
                    run = []
            else:
                run = [run[0], b, run[2] + length] if run else [a, b, length]
        if run:
            gaps.append({"layer": layer, "from_mm": _xy(run[0]), "to_mm": _xy(run[1]), "length_mm": round(run[2], 3)})
        per_layer.append({"layer": layer, "pct": round(100 * covered / total, 1) if total else 0.0})
    need = values["min_pct"]
    gaps.sort(key=lambda g: -g["length_mm"])
    lowest = min(row["pct"] for row in per_layer)
    return {"ok": lowest >= need - 1e-9, "measured_pct": lowest, "needed_pct": need, "layers": per_layer, "worst": gaps}


def _via_padstack(model: dict[str, Any], values: dict[str, Any], _bends: list[Box], _obstacles: Any, _radius: Any) -> dict[str, Any]:
    nets = set(values["nets"]) if values["nets"] else None
    vias = [v for v in model.get("vias") or [] if nets is None or v.get("net") in nets]
    wrong = [v for v in vias if v.get("padstack") != values["padstack"]]
    return {"ok": not wrong, "count": len(vias), "failed": len(wrong),
            "worst": [{"net": v.get("net"), "padstack": v.get("padstack"), "at_mm": _xy(v["circle"])} for v in wrong]}


def chains(traces: list[dict[str, Any]]) -> list[tuple[int, list[Point], list[float]]]:
    """The traces of one net joined end to end where no third trace meets them: each run's layer,
    points, and the width of each piece between them. Layout keeps one drawn trace as several objects."""
    out = []
    for layer in sorted({int(t.get("layer") or 0) for t in traces}):
        edges = [(board_metrics._trace_points(t), float(t.get("width") or 0.0)) for t in traces if int(t.get("layer") or 0) == layer]
        edges = [(points, width) for points, width in edges if len(points) >= 2]

        def key(p: Point) -> tuple[int, int]:
            return round(p[0] / END_JOIN_MM), round(p[1] / END_JOIN_MM)

        ends: dict[tuple[int, int], list[int]] = {}
        for number, (points, _width) in enumerate(edges):
            ends.setdefault(key(points[0]), []).append(number)
            ends.setdefault(key(points[-1]), []).append(number)
        used = [False] * len(edges)

        def walk(start: int, node: tuple[int, int]) -> tuple[list[Point], list[float]]:
            points, width = edges[start]
            line = list(points) if key(points[0]) == node else list(reversed(points))
            widths = [width] * (len(line) - 1)
            used[start] = True
            here = key(line[-1])
            while len(ends.get(here, [])) == 2:
                nxt = next((e for e in ends[here] if not used[e]), None)
                if nxt is None:
                    break
                more, w = edges[nxt]
                more = list(more) if key(more[0]) == here else list(reversed(more))
                line += more[1:]
                widths += [w] * (len(more) - 1)
                used[nxt] = True
                here = key(line[-1])
            return line, widths

        for node, members in ends.items():  # runs start where a trace ends alone or three meet
            if len(members) != 2:
                for edge in members:
                    if not used[edge]:
                        out.append((layer, *walk(edge, node)))
        for edge, done in enumerate(used):  # what is left are closed loops
            if not done:
                out.append((layer, *walk(edge, key(edges[edge][0][0]))))
    return out


def _length(points: list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:], strict=False))


def _in_box(p: Point, boxes: list[Box], grow: float) -> bool:
    return any(x0 - grow < p[0] < x1 + grow and y0 - grow < p[1] < y1 + grow for x0, y0, x1, y1 in boxes)


def _turn(walker: GD.Walker, distance: float) -> Point:
    """The line's direction over ``STEP_MM`` about ``distance``, smoothing the turn at a corner."""
    a, b = walker.at(max(0.0, distance - STEP_MM / 2)), walker.at(distance + STEP_MM / 2)
    length = math.dist(a, b) or 1.0
    return (b[0] - a[0]) / length, (b[1] - a[1]) / length


def _ends(traces: list[dict[str, Any]]) -> dict[tuple[int, int], list[Point]]:
    """The ends of the traces, on a grid of 1 mm cells: a via whose pad covers one off its centre
    is no spot for a via, as laying sees it (``guards.Obstacles.covers_end``)."""
    grid: dict[tuple[int, int], list[Point]] = {}
    for trace in traces:
        points = board_metrics._trace_points(trace)
        for end in (points[0], points[-1]) if points else ():
            grid.setdefault((math.floor(end[0]), math.floor(end[1])), []).append(end)
    return grid


def _covers(ends: dict[tuple[int, int], list[Point]], p: Point, radius: float) -> bool:
    return any(GD.in_pad_box(end, p, radius) and math.dist(end, p) >= GD.END_SNAP_MM
               for i in range(math.floor(p[0] - radius), math.floor(p[0] + radius) + 1)
               for j in range(math.floor(p[1] - radius), math.floor(p[1] + radius) + 1) for end in ends.get((i, j), ()))


def _via_pitch(model: dict[str, Any], values: dict[str, Any], bends: list[Box], obstacles: GD.Obstacles | None,
               radius: float | None) -> dict[str, Any]:
    net, pitch = values["net"], values["max_mm"]
    if obstacles is None:
        return {"ok": None, "reason": "where a via could still stand is not known: the board's copper and clearance rules were not read"}
    vias = [v["circle"] for v in model.get("vias") or [] if v.get("net") == net]
    if not radius:  # the net's usual via, such as its stitching vias; on a tie the larger, which finds less room
        sizes = Counter(round(float(c[2]), 4) for c in vias)
        radius = max(sizes, key=lambda r: (sizes[r], r)) if sizes else 0.2
    ends = _ends([t for t in model.get("traces") or [] if t.get("net") == net])
    spacing = 2 * radius + (obstacles.rules.clearance(net, net, 1, "via_to_via") if obstacles is not None else 0.0) + ROOM_MM
    taken: list[Point] = []  # the spots named so far, as if placed, so that they can all be taken together
    too_long, no_room = [], []
    runs = 0
    measured = bare = bare_mm = 0.0  # the length of the lines measured; the lines with no via at all, and their length
    for layer, points, widths in chains([t for t in model.get("traces") or [] if t.get("net") == net]):
        width, widest = min(widths), max(widths)
        length = _length(points)
        if length < values["min_length_mm"] - 1e-9:
            continue
        runs += 1
        measured += length
        closed = math.dist(points[0], points[-1]) < END_JOIN_MM
        stops = sorted({at for c in vias for at in GD.contacts(points, widths, (c[0], c[1]), c[2])})
        if not stops:
            bare, bare_mm = bare + 1, bare_mm + length
        if closed:  # a loop has no ends: its last gap runs on through its start to its first via
            line = points + points[1:]
            gaps = list(zip(stops, stops[1:], strict=False)) + ([(stops[-1], stops[0] + length)] if stops else [(0.0, length)])
        else:
            line = points
            gaps = list(zip([0.0, *stops], [*stops, length], strict=False))
        walker = GD.Walker(line)
        for a, b in gaps:
            if b - a <= pitch + 1e-6:
                continue
            row = {"layer": layer, "from_mm": _xy(walker.at(a)), "to_mm": _xy(walker.at(b)), "gap_mm": round(b - a, 3)}
            spot = None
            offsets = GD.across(width, radius)
            if obstacles is not None:
                low, high = (-math.inf, math.inf) if closed else GD.clear_of_ends(length, radius, widest)  # no pad over an end
                at = max(a + radius, low)
                while at < min(b - radius, high + 1e-9) and spot is None:
                    base, (ux, uy) = walker.at(at), _turn(walker, at)
                    items = obstacles._near([base], GD.REACH_MM + radius + ROOM_MM + max(offsets))
                    for off in offsets:  # on the line, or off it as far as a via along it may stand
                        p = (base[0] - uy * off, base[1] + ux * off)
                        if (not _in_box(p, bends, radius + ROOM_MM) and obstacles.via_clear(p, net, radius + ROOM_MM, items)
                                and not _covers(ends, p, radius + ROOM_MM) and all(math.dist(p, t) >= spacing for t in taken)):
                            spot = p
                            taken.append(p)
                            break
                    at += STEP_MM
            if obstacles is not None and spot is None:
                no_room.append(row)
            else:
                if spot is not None:
                    row["via_fits_at_mm"] = _xy(spot)
                too_long.append(row)
    too_long.sort(key=lambda r: -r["gap_mm"])
    no_room.sort(key=lambda r: -r["gap_mm"])
    out = {"ok": not too_long, "measured_mm": too_long[0]["gap_mm"] if too_long else None, "needed_mm": pitch, "count": runs,
           "failed": len(too_long), "worst": too_long}
    if no_room:  # passing, but where no via fits the lines are grounded only at their ends: the reviewer may not accept that
        room_mm = sum(row["gap_mm"] for row in no_room)
        out["no_room"] = {"count": len(no_room), "length_mm": round(room_mm, 1), "share_pct": round(100 * room_mm / measured, 1) if measured else 0.0,
                          "without_via": int(bare), "without_via_mm": round(bare_mm, 1), "worst": no_room}
    return out


def _overlap(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _plane(model: dict[str, Any], values: dict[str, Any], bends: list[Box], _obstacles: Any, _radius: Any) -> dict[str, Any]:
    if "planes" not in model:
        return {"ok": None, "reason": "the planes are poured after the plan is drawn; pcb check measures this"}
    shapes = [p for p in model["planes"] if p.get("net") == values["net"] and p.get("extents_mm")]
    problems = []
    for layer in values["layers"]:
        mine = [p for p in shapes if p.get("layer") == layer]
        if not mine:
            problems.append({"layer": layer, "problem": f"no pour of {values['net']}"})
            continue
        if all(p.get("generated") is False for p in mine):  # a shape in Draft, or one that touches nothing of its net
            problems.append({"layer": layer, "problem": f"the pour of {values['net']} made no copper"})
            continue
        if not values["hatched_in_bends"]:
            continue
        for box in bends:
            area = (box[2] - box[0]) * (box[3] - box[1])
            hatched = sum(_overlap(tuple(p["extents_mm"]), box) for p in mine if p.get("hatch") not in (None, "solid")) >= 0.9 * area
            solid = any(p.get("hatch") == "solid" and _overlap(tuple(p["extents_mm"]), box) > 0.1 * area for p in mine)
            if not hatched or solid:
                problems.append({"layer": layer, "problem": "the bend zone is not hatched" if not hatched else "solid copper over the bend zone",
                                 "at_mm": _xy(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2))})
    return {"ok": not problems, "count": len(values["layers"]), "failed": len(problems), "worst": problems}


def _xy(p: Any) -> list[float]:
    return [round(float(p[0]), 4), round(float(p[1]), 4)]


MEASURES = {"guard": _guard, "ring": _ring, "via_padstack": _via_padstack, "via_pitch": _via_pitch, "plane": _plane}
