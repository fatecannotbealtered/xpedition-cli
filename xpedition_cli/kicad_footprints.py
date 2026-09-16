"""KiCad footprints (`.kicad_mod`, S-expressions) as Xpedition cells.

KiCad ships about fifteen thousand footprints as plain text under
`share/kicad/footprints/<library>.pretty/`. Each file lists pads, graphics on the
silkscreen / fabrication / courtyard layers and a reference-designator text. This
module reads them into the `library_hkp` cell and padstack model, so a whole
`.pretty` folder becomes one cell partition of a central library, imported through
the stock HKP converters like the placeholder packages are.

Conventions: KiCad's Y axis points down and Xpedition's up, so every Y is negated;
rotations are counter-clockwise on screen in both, so angles are kept. Pads without
copper on the mount side (paste-only pieces of a thermal pad, back-side pads of edge
connectors, `connect` pads) are dropped, and a second pad with a number already used
is dropped too, because a cell's pin count has to match its part's. Arcs become short
polylines; a custom pad becomes the rectangle around its primitives.

The KiCad libraries are CC-BY-SA 4.0 with the KiCad library exception: designs made
with them carry no obligation; a converted library keeps the attribution.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import library_hkp as H

TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()"]+')
LAYER_BLOCKS = {
    "F.SilkS": "SILKSCREEN_OUTLINE",
    "F.Fab": "ASSEMBLY_OUTLINE",
    "F.CrtYd": "PLACEMENT_OUTLINE",
}
COPPER_LAYERS = ("F.Cu", "*.Cu")
ARC_STEP_DEGREES = 10.0
JOIN_TOLERANCE = 0.002
COURTYARD_FALLBACK_MARGIN = 0.25
DESCRIPTION_LIMIT = 200
CELL_NAME_LIMIT = 64  # HKP2CellDB refuses longer cell names
DEFAULT_ROUNDRECT_RATIO = 0.25
ENVIRONMENT_ROOTS = ("XPEDITION_KICAD_FOOTPRINTS", "KICAD9_FOOTPRINT_DIR", "KICAD8_FOOTPRINT_DIR")
GROUP_BY_LIBRARY = {
    "Resistor_SMD": "DISCRETE_CHIP",
    "Capacitor_SMD": "DISCRETE_CHIP",
    "Inductor_SMD": "DISCRETE_CHIP",
    "LED_SMD": "DISCRETE_CHIP",
    "Diode_SMD": "DISCRETE_CHIP",
    "Fuse": "DISCRETE_CHIP",
    "Package_SO": "IC_SOIC",
    "TestPoint": "TEST_POINT",
}
GROUP_BY_PREFIX = (
    ("Connector", "CONNECTOR"),
    ("Resistor", "DISCRETE_OTHER"),
    ("Capacitor", "DISCRETE_OTHER"),
    ("Inductor", "DISCRETE_OTHER"),
    ("Diode", "DISCRETE_OTHER"),
    ("LED", "DISCRETE_OTHER"),
    ("Crystal", "DISCRETE_OTHER"),
    ("Oscillator", "DISCRETE_OTHER"),
    ("Package_TO", "DISCRETE_OTHER"),
)


# -- S-expressions -------------------------------------------------------------------


def parse(text: str) -> list[Any]:
    """`text` as nested lists of strings; quoted strings lose their quotes."""
    stack: list[list[Any]] = [[]]
    for match in TOKEN.finditer(text):
        token = match.group(0)
        if token == "(":
            stack.append([])
        elif token == ")":
            if len(stack) == 1:
                raise ValueError("unbalanced ')'")
            done = stack.pop()
            stack[-1].append(done)
        elif token.startswith('"'):
            stack[-1].append(token[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        else:
            stack[-1].append(token)
    if len(stack) != 1:
        raise ValueError("unbalanced '('")
    return stack[0]


def _child(node: list[Any], key: str) -> list[Any] | None:
    for item in node:
        if isinstance(item, list) and item and item[0] == key:
            return item
    return None


def _point(node: list[Any]) -> tuple[float, float]:
    return float(node[1]), float(node[2])


# -- the footprint model ---------------------------------------------------------------


@dataclass
class FpPad:
    number: str
    kind: str  # smd, thru_hole, np_thru_hole, connect
    shape: str  # circle, rect, roundrect, oval, trapezoid, custom
    x: float
    y: float
    rotation: float
    width: float
    height: float
    layers: tuple[str, ...]
    drill: tuple[float, float] | None = None
    drill_offset: tuple[float, float] | None = None
    radius_ratio: float = DEFAULT_ROUNDRECT_RATIO
    extent: tuple[float, float] | None = None  # custom pads: the box around the primitives


@dataclass
class FpGraphic:
    kind: str  # line, arc, circle, rect, poly
    layer: str
    points: list[tuple[float, float]]
    width: float = 0.0
    filled: bool = False


@dataclass
class FpText:
    x: float
    y: float
    rotation: float
    height: float
    thickness: float
    layer: str
    hidden: bool = False


@dataclass
class Footprint:
    name: str
    layer: str = "F.Cu"
    attributes: tuple[str, ...] = ()
    description: str = ""
    tags: str = ""
    pads: list[FpPad] = field(default_factory=list)
    graphics: list[FpGraphic] = field(default_factory=list)
    reference: FpText | None = None  # the silkscreen reference designator
    reference_fab: FpText | None = None  # `${REFERENCE}` on the fabrication layer


def read_footprint(text: str) -> Footprint:
    """The footprint a `.kicad_mod` text describes."""
    root = parse(text)
    node = next(
        (n for n in root if isinstance(n, list) and n and n[0] in ("footprint", "module")), None
    )
    if node is None or len(node) < 2:
        raise ValueError("not a KiCad footprint")
    footprint = Footprint(name=str(node[1]))
    for item in node[2:]:
        if not isinstance(item, list) or not item:
            continue
        key = item[0]
        if key == "layer" and len(item) > 1:
            footprint.layer = str(item[1])
        elif key == "descr" and len(item) > 1:
            footprint.description = str(item[1])
        elif key == "tags" and len(item) > 1:
            footprint.tags = str(item[1])
        elif key == "attr":
            footprint.attributes = tuple(str(v) for v in item[1:] if not isinstance(v, list))
        elif key == "pad":
            footprint.pads.append(_pad(item))
        elif key in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly"):
            graphic = _graphic(item)
            if graphic is not None:
                footprint.graphics.append(graphic)
        elif key == "property" and len(item) > 2 and item[1] == "Reference":
            footprint.reference = _text(item)
        elif key == "fp_text" and len(item) > 2 and item[1] == "reference":
            footprint.reference = _text(item)
        elif key == "fp_text" and len(item) > 2 and item[1] == "user" and item[2] == "${REFERENCE}":
            footprint.reference_fab = _text(item)
    return footprint


def _pad(item: list[Any]) -> FpPad:
    number, kind, shape = str(item[1]), str(item[2]), str(item[3])
    x = y = rotation = width = height = 0.0
    layers: tuple[str, ...] = ()
    drill: tuple[float, float] | None = None
    offset: tuple[float, float] | None = None
    ratio = DEFAULT_ROUNDRECT_RATIO
    extent: tuple[float, float] | None = None
    for sub in item[4:]:
        if not isinstance(sub, list) or not sub:
            continue
        key = sub[0]
        if key == "at":
            x, y = _point(sub)
            rotation = float(sub[3]) if len(sub) > 3 else 0.0
        elif key == "size":
            width, height = _point(sub)
        elif key == "drill":
            drill, offset = _drill(sub)
        elif key == "layers":
            layers = tuple(str(v) for v in sub[1:] if not isinstance(v, list))
        elif key == "roundrect_rratio":
            ratio = float(sub[1])
        elif key == "primitives":
            extent = _primitives_extent(sub)
    return FpPad(
        number, kind, shape, x, y, rotation, width, height, layers, drill, offset, ratio, extent
    )


def _drill(node: list[Any]) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    values: list[float] = []
    oval = False
    offset: tuple[float, float] | None = None
    for sub in node[1:]:
        if isinstance(sub, list):
            if sub and sub[0] == "offset":
                offset = _point(sub)
        elif sub == "oval":
            oval = True
        else:
            values.append(float(sub))
    if not values:
        return None, offset
    if oval and len(values) >= 2:
        return (values[0], values[1]), offset
    return (values[0], values[0]), offset


def _primitives_extent(node: list[Any]) -> tuple[float, float] | None:
    points: list[tuple[float, float]] = []
    for sub in node[1:]:
        if not isinstance(sub, list) or not sub:
            continue
        pts = _child(sub, "pts")
        if pts is not None:
            points += [_point(p) for p in pts[1:] if isinstance(p, list) and p[0] == "xy"]
        for key in ("start", "end", "mid"):
            found = _child(sub, key)
            if found is not None:
                points.append(_point(found))
        centre = _child(sub, "center")
        end = _child(sub, "end")
        if sub[0] == "gr_circle" and centre is not None and end is not None:
            cx, cy = _point(centre)
            r = math.dist((cx, cy), _point(end))
            points += [(cx - r, cy - r), (cx + r, cy + r)]
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return max(xs) - min(xs), max(ys) - min(ys)


def _graphic(item: list[Any]) -> FpGraphic | None:
    kind = str(item[0])[3:]
    layer = ""
    width = 0.0
    filled = False
    named: dict[str, tuple[float, float]] = {}
    points: list[tuple[float, float]] = []
    for sub in item[1:]:
        if not isinstance(sub, list) or not sub:
            continue
        key = sub[0]
        if key in ("start", "end", "mid", "center"):
            named[key] = _point(sub)
        elif key == "pts":
            points = [_point(p) for p in sub[1:] if isinstance(p, list) and p[0] == "xy"]
        elif key == "layer" and len(sub) > 1:
            layer = str(sub[1])
        elif key == "stroke":
            found = _child(sub, "width")
            width = float(found[1]) if found else 0.0
        elif key == "width" and len(sub) > 1:
            width = float(sub[1])
        elif key == "fill" and len(sub) > 1:
            filled = str(sub[1]) in ("yes", "solid")
    if kind == "line" and "start" in named and "end" in named:
        return FpGraphic(kind, layer, [named["start"], named["end"]], width)
    if kind == "arc" and all(k in named for k in ("start", "mid", "end")):
        return FpGraphic(kind, layer, [named["start"], named["mid"], named["end"]], width)
    if kind == "circle" and "center" in named and "end" in named:
        return FpGraphic(kind, layer, [named["center"], named["end"]], width, filled)
    if kind == "rect" and "start" in named and "end" in named:
        return FpGraphic(kind, layer, [named["start"], named["end"]], width, filled)
    if kind == "poly" and len(points) >= 3:
        return FpGraphic(kind, layer, points, width, filled)
    return None


def _text(item: list[Any]) -> FpText:
    x = y = rotation = 0.0
    height, thickness = 1.0, 0.15
    layer = ""
    hidden = False
    for sub in item[3:]:
        if not isinstance(sub, list) or not sub:
            continue
        key = sub[0]
        if key == "at":
            x, y = _point(sub)
            rotation = float(sub[3]) if len(sub) > 3 else 0.0
        elif key == "layer" and len(sub) > 1:
            layer = str(sub[1])
        elif key == "hide":
            hidden = len(sub) < 2 or str(sub[1]) == "yes"
        elif key == "effects":
            font = _child(sub, "font")
            if font is not None:
                size = _child(font, "size")
                if size is not None:
                    height = float(size[1])
                stroke = _child(font, "thickness")
                if stroke is not None:
                    thickness = float(stroke[1])
    return FpText(x, y, rotation, height, thickness, layer, hidden)


# -- conversion ------------------------------------------------------------------------


def package_group(library: str, footprint: Footprint) -> str:
    if library in GROUP_BY_LIBRARY:
        return GROUP_BY_LIBRARY[library]
    for prefix, group in GROUP_BY_PREFIX:
        if library.startswith(prefix):
            return group
    return "GENERAL"


def mount_type(footprint: Footprint, kinds: set[str] | None = None) -> str:
    """The mount type from the pads that became pins (`kinds`), else from all pads."""
    used = kinds if kinds is not None else {p.kind for p in footprint.pads}
    through = "thru_hole" in used
    surface = "smd" in used
    if "through_hole" in footprint.attributes:
        return "MIXED" if surface and through else "THROUGH"
    if "smd" in footprint.attributes:
        return "MIXED" if through else "SURFACE"
    return "THROUGH" if through else "SURFACE"


def _round(value: float) -> float:
    return round(value + 0.0, 4)


def _shape(pad: FpPad) -> tuple[str, float, float, float]:
    """Xpedition pad shape, width, height and corner radius for a KiCad pad."""
    width, height = pad.width, pad.height
    if pad.shape == "circle":
        return "ROUND", width, width, 0.0
    if pad.shape == "oval":
        if abs(width - height) < 1e-6:
            return "ROUND", width, width, 0.0
        return "OBLONG", width, height, 0.0
    if pad.shape == "roundrect":
        radius = min(pad.radius_ratio * min(width, height), min(width, height) / 2)
        if radius < 0.001:  # a zero ratio is a plain rectangle; the library refuses radius 0
            return "RECTANGLE", width, height, 0.0
        return "RADIUS_CORNER_RECTANGLE", width, height, radius
    if pad.shape == "custom" and pad.extent is not None:
        return "RECTANGLE", max(width, pad.extent[0]), max(height, pad.extent[1]), 0.0
    return "RECTANGLE", width, height, 0.0


def _arc_points(
    start: tuple[float, float], mid: tuple[float, float], end: tuple[float, float]
) -> list[tuple[float, float]]:
    """The arc through three points as a polyline (start and end included)."""
    ax, ay = start
    bx, by = mid
    cx, cy = end
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return [start, end]
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d
    radius = math.dist((ux, uy), start)
    a0 = math.atan2(ay - uy, ax - ux)
    a1 = math.atan2(cy - uy, cx - ux)
    am = math.atan2(by - uy, bx - ux)
    sweep = (a1 - a0) % (2 * math.pi)
    if (am - a0) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    steps = max(2, int(math.ceil(abs(math.degrees(sweep)) / ARC_STEP_DEGREES)))
    points = [
        (
            ux + radius * math.cos(a0 + sweep * i / steps),
            uy + radius * math.sin(a0 + sweep * i / steps),
        )
        for i in range(steps + 1)
    ]
    points[0], points[-1] = start, end
    return points


def _flip(points: list[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    return tuple((_round(x), _round(-y)) for x, y in points)


def _polyline(graphic: FpGraphic) -> list[tuple[float, float]]:
    """The graphic as points in KiCad coordinates; closed shapes repeat their first point."""
    if graphic.kind == "line":
        return list(graphic.points)
    if graphic.kind == "arc":
        return _arc_points(*graphic.points)
    if graphic.kind == "rect":
        (x1, y1), (x2, y2) = graphic.points
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    if graphic.kind == "circle":
        (cx, cy), end = graphic.points
        r = math.dist((cx, cy), end)
        n = 24
        ring = [
            (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
            for i in range(n)
        ]
        return ring + [ring[0]]
    points = list(graphic.points)
    if points[0] != points[-1]:
        points.append(points[0])
    return points


def _paths(block: str, graphic: FpGraphic) -> list[H.Graphic]:
    """`graphic` as one Xpedition path block of the given kind."""
    width = _round(graphic.width)
    if graphic.kind == "circle":
        (cx, cy), end = graphic.points
        return [
            H.Graphic(
                block,
                "CIRCLE_PATH",
                ((_round(cx), _round(-cy)),),
                width,
                _round(math.dist((cx, cy), end)),
            )
        ]
    if graphic.kind == "rect" and not graphic.filled:
        return [H.Graphic(block, "RECT_PATH", _flip(graphic.points), width)]
    points = _flip(_polyline(graphic))
    if graphic.filled and graphic.kind in ("rect", "poly"):
        return [H.Graphic(block, "POLYLINE_SHAPE", points, 0.0)]
    return [H.Graphic(block, "POLYLINE_PATH", points, width)]


def _key(point: tuple[float, float]) -> tuple[int, int]:
    return round(point[0] / JOIN_TOLERANCE), round(point[1] / JOIN_TOLERANCE)


def _loops(chains: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Closed loops made by chaining polylines end to end (open leftovers are dropped)."""
    closed: list[list[tuple[float, float]]] = []
    pending = [list(c) for c in chains if len(c) >= 2]
    while pending:
        chain = pending.pop(0)
        grown = True
        while grown and _key(chain[0]) != _key(chain[-1]):
            grown = False
            for index, other in enumerate(pending):
                if _key(other[0]) == _key(chain[-1]):
                    chain += other[1:]
                elif _key(other[-1]) == _key(chain[-1]):
                    chain += list(reversed(other))[1:]
                elif _key(other[-1]) == _key(chain[0]):
                    chain = other[:-1] + chain
                elif _key(other[0]) == _key(chain[0]):
                    chain = list(reversed(other))[:-1] + chain
                else:
                    continue
                pending.pop(index)
                grown = True
                break
        if _key(chain[0]) == _key(chain[-1]) and len(chain) >= 4:
            closed.append(chain)
    return closed


def _box(
    points: list[tuple[float, float]], margin: float = 0.0
) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


def _placement(
    courtyard: list[FpGraphic], fallback: list[tuple[float, float]]
) -> tuple[H.Graphic, tuple[float, float, float, float]]:
    """One closed placement outline (KiCad coordinates in, Xpedition out) and its box."""
    loops = _loops([_polyline(g) for g in courtyard])
    if len(loops) == 1 and len(courtyard) == 1 and courtyard[0].kind == "rect":
        points = _flip(courtyard[0].points)
        return H.Graphic("PLACEMENT_OUTLINE", "RECT_PATH", points, 0.0), _box(list(points))
    if len(loops) == 1:
        points = _flip(loops[0])
        return H.Graphic("PLACEMENT_OUTLINE", "POLYLINE_PATH", points, 0.0), _box(list(points))
    if courtyard:
        source = [p for g in courtyard for p in _polyline(g)]
        margin = 0.0
    else:
        source = fallback
        margin = COURTYARD_FALLBACK_MARGIN
    x1, y1, x2, y2 = _box(list(_flip(source)), margin)
    corners = tuple(
        (_round(x), _round(y)) for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1))
    )
    return H.Graphic("PLACEMENT_OUTLINE", "POLYLINE_PATH", corners, 0.0), (x1, y1, x2, y2)


def _area(loop: list[tuple[float, float]]) -> float:
    return abs(
        sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(loop, loop[1:] + loop[:1], strict=True))
        / 2
    )


def _assembly(fabrication: list[FpGraphic]) -> H.Graphic:
    """One assembly outline: the library keeps a single one per cell, so the largest closed
    loop of the fabrication drawing (the body) wins, else the box around the drawing."""
    loops = _loops([_polyline(g) for g in fabrication])
    if loops:
        body = max(loops, key=_area)
        return H.Graphic("ASSEMBLY_OUTLINE", "POLYLINE_PATH", _flip(body), 0.0)
    x1, y1, x2, y2 = _box(list(_flip([p for g in fabrication for p in _polyline(g)])))
    corners = ((x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1))
    return H.Graphic(
        "ASSEMBLY_OUTLINE", "POLYLINE_PATH", tuple((_round(x), _round(y)) for x, y in corners), 0.0
    )


def _copper_area(pad: FpPad) -> float:
    if pad.shape == "custom" and pad.extent is not None:
        return max(pad.width, pad.extent[0]) * max(pad.height, pad.extent[1])
    return pad.width * pad.height


def _largest_by_number(pads: list[FpPad]) -> dict[str, int]:
    """For every pad number, the index of its largest copper pad on the mount side."""
    chosen: dict[str, int] = {}
    for index, pad in enumerate(pads):
        if not pad.number or pad.kind not in ("smd", "thru_hole"):
            continue
        if not any(layer in COPPER_LAYERS for layer in pad.layers):
            continue
        if pad.number not in chosen or _copper_area(pad) > _copper_area(pads[chosen[pad.number]]):
            chosen[pad.number] = index
    return chosen


def cell_name(name: str) -> str:
    """A cell name the library accepts: `HKP2CellDB` refuses parentheses and names over 64
    characters (685 KiCad footprints), so parentheses become underscores and long names are
    cut and tagged with a hash of the full name, which keeps them unique and reproducible."""
    cleaned = name.replace("(", "_").replace(")", "_")  # refused inside a cell name
    if len(cleaned) <= CELL_NAME_LIMIT:
        return cleaned
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:7]
    return f"{cleaned[: CELL_NAME_LIMIT - 8]}~{digest}"


def to_cell(
    footprint: Footprint, stock: H._Stock, library: str = "", name: str | None = None
) -> tuple[H.Cell, list[str]]:
    """`footprint` as a cell whose padstacks are created in `stock`; with the issues found."""
    issues: list[str] = []
    pins: list[H.CellPin] = []
    holes: list[H.CellPin] = []
    chosen = _largest_by_number(footprint.pads)
    kinds: set[str] = set()
    dropped: dict[str, int] = {}
    for index, pad in enumerate(footprint.pads):
        x, y = _round(pad.x), _round(-pad.y)
        if pad.kind == "np_thru_hole":
            if pad.drill is None:
                dropped["hole_without_drill"] = dropped.get("hole_without_drill", 0) + 1
                continue
            holes.append(H.CellPin("", stock.hole(pad.drill, plated=False), x, y, pad.rotation))
            continue
        copper = any(layer in COPPER_LAYERS for layer in pad.layers)
        if pad.kind == "connect" or not copper:
            reason = (
                "connect"
                if pad.kind == "connect"
                else ("back_side" if "B.Cu" in pad.layers else "no_copper")
            )
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        shape, width, height, radius = _shape(pad)
        if pad.kind == "thru_hole" and pad.drill is None:
            dropped["pin_without_drill"] = dropped.get("pin_without_drill", 0) + 1
            continue
        if not pad.number:
            if pad.kind == "thru_hole" and pad.drill is not None:
                holes.append(H.CellPin("", stock.hole(pad.drill, plated=True), x, y, pad.rotation))
            else:
                dropped["unnumbered"] = dropped.get("unnumbered", 0) + 1
            continue
        if chosen.get(pad.number) != index:
            # the same number on several pads (a thermal pad with its vias and paste
            # windows): the largest piece of copper is the pin, the rest is dropped
            dropped["duplicate_number"] = dropped.get("duplicate_number", 0) + 1
            continue
        if pad.drill_offset is not None and any(abs(v) > 1e-6 for v in pad.drill_offset):
            dropped["drill_offset_ignored"] = dropped.get("drill_offset_ignored", 0) + 1
        if pad.kind == "thru_hole" and pad.drill is not None:
            padstack = stock.through_stack(shape, width, height, radius, pad.drill)
        else:
            padstack = stock.smd_stack(shape, width, height, radius)
        kinds.add(pad.kind)
        pins.append(H.CellPin(pad.number, padstack, x, y, pad.rotation))
    graphics: list[H.Graphic] = []
    courtyard: list[FpGraphic] = []
    fabrication: list[FpGraphic] = []
    for graphic in footprint.graphics:
        block = LAYER_BLOCKS.get(graphic.layer)
        if block is None:
            continue
        if block == "PLACEMENT_OUTLINE":
            courtyard.append(graphic)
        elif block == "ASSEMBLY_OUTLINE":
            fabrication.append(graphic)
        else:
            graphics += _paths(block, graphic)
    if fabrication:
        graphics.append(_assembly(fabrication))
    fallback = [(p.x, p.y) for p in footprint.pads] + [
        pt for g in footprint.graphics if g.layer in LAYER_BLOCKS for pt in _polyline(g)
    ]
    if not fallback:
        fallback = [(-0.5, -0.5), (0.5, 0.5)]
    placement, body = _placement(courtyard, fallback)
    graphics.append(placement)
    refdes = assembly = None
    if footprint.reference is not None:
        text = footprint.reference
        refdes = H.RefDesText(
            _round(text.x), _round(-text.y), text.height, text.thickness, text.rotation
        )
        fab = footprint.reference_fab
        if fab is not None:
            assembly = H.RefDesText(
                _round(fab.x), _round(-fab.y), fab.height, fab.thickness, fab.rotation
            )
        else:
            x1, y1, x2, y2 = body
            size = max(0.3, min(0.8, 0.5 * min(x2 - x1, y2 - y1)))
            assembly = H.RefDesText(_round((x1 + x2) / 2), _round((y1 + y2) / 2), size, 0.12, 0)
    description = " ".join(footprint.description.split()).replace('"', "'")[:DESCRIPTION_LIMIT]
    cell = H.Cell(
        name or cell_name(footprint.name),
        package_group(library, footprint),
        mount_type(footprint, kinds),
        pins,
        body,
        0.0,
        description or footprint.name,
        mechanical=not pins and bool(holes),
        graphics=graphics,
        refdes=refdes,
        refdes_assembly=assembly,
        holes=holes,
    )
    if dropped:
        detail = ", ".join(f"{k} {v}" for k, v in sorted(dropped.items()))
        issues.append(f"{footprint.name}: pads dropped ({detail})")
    if not pins and not holes:
        issues.append(f"{footprint.name}: no pins")
    return cell, issues


# -- libraries -----------------------------------------------------------------------


def partition_name(library: str) -> str:
    """A partition identifier for a `.pretty` library name."""
    stem = library[:-7] if library.lower().endswith(".pretty") else library
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_")
    if not cleaned:
        cleaned = "KiCad"
    if not cleaned[0].isalpha():
        cleaned = f"K_{cleaned}"
    return cleaned


def default_root() -> Path | None:
    """The KiCad footprint folder named by the environment or found in the usual places."""
    for variable in ENVIRONMENT_ROOTS:
        value = os.environ.get(variable)
        if value and Path(value).is_dir():
            return Path(value)
    candidates = [Path("D:/software/kicad/home/share/kicad/footprints")]
    for base in ("C:/Program Files/KiCad", "C:/Program Files (x86)/KiCad", "D:/software/kicad"):
        root = Path(base)
        if root.is_dir():
            candidates += sorted(root.glob("*/share/kicad/footprints"), reverse=True)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def libraries(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir() and p.name.lower().endswith(".pretty"))


def locate(root: Path, spec: str) -> Path:
    """The `.kicad_mod` for `Library:Name`, `Library/Name` or a bare `Name` (searched)."""
    spec = spec.strip()
    for separator in (":", "/"):
        if separator in spec:
            library, name = spec.split(separator, 1)
            path = root / f"{library}.pretty" / f"{name}.kicad_mod"
            if path.is_file():
                return path
            raise FileNotFoundError(f"KiCad footprint {spec!r} not found under {root}")
    matches = sorted(root.glob(f"*.pretty/{spec}.kicad_mod"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"KiCad footprint {spec!r} not found under {root}")
    names = ", ".join(m.parent.name[:-7] for m in matches)
    raise FileNotFoundError(f"KiCad footprint {spec!r} exists in several libraries: {names}")


def convert_library(
    pretty: Path, names: list[str] | None = None
) -> tuple[H.LibraryPlan, list[str]]:
    """Every footprint of a `.pretty` folder (or the `names` given) as one partition plan."""
    plan = H.LibraryPlan(partition=partition_name(pretty.name))
    stock = H._Stock(plan)
    issues: list[str] = []
    library = pretty.name[:-7] if pretty.name.lower().endswith(".pretty") else pretty.name
    files = sorted(pretty.glob("*.kicad_mod"))
    if names is not None:
        wanted = set(names)
        files = [f for f in files if f.stem in wanted]
    for path in files:
        try:
            footprint = read_footprint(path.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, OSError) as exc:
            issues.append(f"{path.name}: {exc}")
            continue
        if footprint.layer == "B.Cu":
            issues.append(f"{footprint.name}: defined on the back side, skipped")
            continue
        try:
            cell, found = to_cell(footprint, stock, library)
        except Exception as exc:  # one odd file must not stop a library
            issues.append(f"{footprint.name}: not converted ({type(exc).__name__}: {exc})")
            continue
        issues += found
        if cell.pins or cell.holes:
            plan.cells[cell.name] = cell
    return plan, issues


def reference_cell(root: Path, spec: str) -> tuple[H.Cell, str, list[str]]:
    """A cell (not to be rendered) standing for the KiCad footprint `spec`, with the name
    of the partition `convert_library` files it under and the conversion's issues."""
    path = locate(root, spec)
    footprint = read_footprint(path.read_text(encoding="utf-8", errors="replace"))
    scratch = H.LibraryPlan()
    library = path.parent.name[:-7]
    cell, issues = to_cell(footprint, H._Stock(scratch), library)
    cell.external = True
    cell.partition = partition_name(path.parent.name)
    return cell, cell.partition, issues
