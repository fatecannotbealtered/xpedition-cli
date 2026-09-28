"""A planned schematic as geometry, as a picture, and as the texts it overlaps.

`schematic draw` wipes every sheet it draws, and until now the only picture of the
result was a capture of Designer's window after the fact. This module lays the plan
out the way Designer will draw it -- the generated symbols (every graphic in them is a
polyline), the wires, net labels and their boxes, the power, ground and no-connect
symbols, the part attributes, the free text and boxes -- and from that one layout:

- `text_overlaps` finds the texts that collide with another text, a line or a label's
  box (DS-17); nothing measured readability before, and a crowded sheet passed;
- `render` draws each sheet as a PNG, with the drawable area dashed and the
  planner's findings boxed in red, so an agent can look at a sheet before it
  confirms the draw.

Text is measured the way the planner measures it (`CHAR_WIDTH` per character at size
8, so 0.75 of the size, and a full size for a wide character), which keeps an
overlap the preview shows an overlap the planner means. The stock `Globals:gnd` and
`builtin:No_Connect` symbols are stand-ins of the size the planner reserves for them.
Coordinates are sheet units, y up. Only drawing needs Pillow.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from . import schematic_layout as L

Point = tuple[float, float]
Rect = tuple[float, float, float, float]

COLOURS = {
    "background": (255, 255, 255, 255),
    "frame": (150, 150, 150, 255),
    "usable": (120, 160, 220, 255),
    "symbol": (150, 20, 20, 255),
    "wire": (0, 120, 60, 255),
    "label": (20, 40, 170, 255),
    "text": (20, 20, 20, 255),
    "value": (110, 110, 110, 255),
    "box": (90, 90, 90, 255),
    "issue": (225, 0, 0, 255),
}
TEXT_ATTRIBUTE = 8  # attribute text size on a sheet, as the symbols write it
WIDTH_RATIO = L.CHAR_WIDTH / 8  # the planner's character width at size 8
# Designer's VdOrigin (VDALIGN_UL = 1 ... VDALIGN_LR = 9): the point of a text that
# sits at its location, as (fraction across, fraction down) of the text's box
ANCHORS = {
    1: (0.0, 0.0),
    2: (0.0, 0.5),
    3: (0.0, 1.0),
    4: (0.5, 0.0),
    5: (0.5, 0.5),
    6: (0.5, 1.0),
    7: (1.0, 0.0),
    8: (1.0, 0.5),
    9: (1.0, 1.0),
}
FONTS = (
    # a CJK face first: titles and notes may be Chinese
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
SCALE = 2.0  # pixels per sheet unit
MAX_EDGE = 4096  # pixels on a picture's long side: an A0 sheet at 2 px/unit is 9362 wide
OVERLAP_AREA = 1.0  # square units two texts may share before they count as colliding
TOUCH = 0.5  # units a line may run inside a text's edge before it crosses the text


# -- the layout -------------------------------------------------------------------------


@dataclass
class Text:
    x: float
    y: float
    text: str
    size: float
    anchor: int
    colour: str
    owner: str  # the part, label, power symbol or "text" it belongs to
    role: str = ""  # "refdes" and "value" are the part attributes the planner may move
    # a symbol's own text turns with the symbol (quarter turns counter-clockwise); its
    # extent on the sheet is then `rect`, computed where the symbol is placed
    turns: int = 0
    rect: Rect | None = None

    def box(self) -> Rect:
        if self.rect is not None:
            return self.rect
        width = text_width(self.text, self.size)
        fx, fy = ANCHORS.get(self.anchor, ANCHORS[3])
        left = self.x - fx * width
        top = self.y + fy * self.size  # y up: a lower anchor puts the text above
        return (left, top - self.size, left + width, top)


@dataclass
class Line:
    points: list[Point]
    colour: str
    owner: str
    width: float = 1.0


@dataclass
class Box:
    rect: Rect
    role: str  # "label" for a net label's own box, "frame" for any other
    owner: str


@dataclass
class SheetLayout:
    number: int
    width: int
    height: int
    usable: list[int] | None
    texts: list[Text] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    boxes: list[Box] = field(default_factory=list)
    parts: dict[str, Rect] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class _Art:
    """What a symbol draws, in its own coordinates."""

    lines: list[list[Point]] = field(default_factory=list)
    # (x, y, size, anchor, name, value, visibility) of every attribute text it carries
    texts: list[tuple[float, float, int, int, str, str, int]] = field(default_factory=list)


def text_width(text: str, size: float) -> float:
    """A text's width in sheet units, measured the planner's way."""
    return sum(
        size if unicodedata.east_asian_width(ch) in {"W", "F"} else size * WIDTH_RATIO
        for ch in text
    )


def parse_symbol(text: str) -> _Art:
    """The polylines and the attribute texts of a `V 53` symbol file."""
    art = _Art()
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        kind = fields[0]
        if kind == "l" and len(fields) >= 2:
            count = int(fields[1])
            numbers = [float(v) for v in fields[2 : 2 + 2 * count]]
            art.lines.append(list(zip(numbers[0::2], numbers[1::2], strict=False)))
        elif kind in {"U", "A"} and len(fields) >= 8 and "=" in line:
            # x y size rotation anchor visibility NAME=value; visibility 0 is hidden
            name, _, value = " ".join(fields[7:]).partition("=")
            art.texts.append(
                (
                    float(fields[1]),
                    float(fields[2]),
                    int(fields[3]),
                    int(fields[5]),
                    name,
                    value,
                    int(fields[6]),
                )
            )
    return art


def _turn(x: float, y: float, orientation: int) -> Point:
    """Designer's instance orientation: 0/1/2/3 = 0°/90°/180°/270° counter-clockwise."""
    return {0: (x, y), 1: (-y, x), 2: (-x, -y), 3: (y, -x)}[orientation % 4]


def _symbol_art(plan: L.Plan, library: str, name: str) -> _Art | None:
    if name in plan.symbols:
        return parse_symbol(plan.symbols[name])
    if (library, name) == ("Globals", "gnd"):
        # the stock Globals:gnd, as its file draws it: a pin down to a triangle, and the
        # net name hanging under it (VDALIGN_UC at y -15, size 8)
        return _Art(
            lines=[[(0, 0), (0, -5)], [(0, -13), (-8, -5), (8, -5), (0, -13)]],
            texts=[(0, -15, 8, 4, "NETNAME", "GND", 3)],
        )
    if (library, name) == ("builtin", "No_Connect"):
        # the stock builtin:No_Connect: a lead from the pin end to a cross beside it
        return _Art(lines=[[(0, 0), (-10, 0)], [(-14, -4), (-6, 4)], [(-14, 4), (-6, -4)]])
    return None


def _place(
    layout: SheetLayout,
    art: _Art,
    op: dict[str, Any],
    owner: str,
    shown: dict[str, str],
    colour: str = "symbol",
) -> Rect:
    """Lay a placed symbol out; its extent on the sheet."""
    x0, y0 = float(op["x"]), float(op["y"])
    orientation = int(op.get("orientation") or 0)
    xs: list[float] = [x0]
    ys: list[float] = [y0]
    for polyline in art.lines:
        points = [(x0 + dx, y0 + dy) for dx, dy in (_turn(x, y, orientation) for x, y in polyline)]
        xs += [p[0] for p in points]
        ys += [p[1] for p in points]
        layout.lines.append(Line(points, colour, owner))
    for tx, ty, size, anchor, name, value, visibility in art.texts:
        text = shown.get(name, value if visibility else "")
        if not text:
            continue
        # the text's box in the symbol's own coordinates, turned with the symbol:
        # Designer draws a rotated symbol's own text rotated with it (which is why the
        # planner sets part attributes horizontal explicitly)
        local = Text(tx, ty, text, size, anchor, colour, owner).box()
        corners = [
            _turn(cx, cy, orientation)
            for cx, cy in (
                (local[0], local[1]),
                (local[2], local[1]),
                (local[2], local[3]),
                (local[0], local[3]),
            )
        ]
        rect = (
            x0 + min(c[0] for c in corners),
            y0 + min(c[1] for c in corners),
            x0 + max(c[0] for c in corners),
            y0 + max(c[1] for c in corners),
        )
        dx, dy = _turn(tx, ty, orientation)
        layout.texts.append(
            Text(
                x0 + dx,
                y0 + dy,
                text,
                size,
                anchor,
                "value" if name == "DEVICE" else colour,
                owner,
                turns=orientation % 4,
                rect=rect,
            )
        )
    return (min(xs), min(ys), max(xs), max(ys))


def layout_sheet(plan: L.Plan, number: int, sheet_size: str) -> SheetLayout:
    """Everything the plan draws on sheet `number`, as geometry."""
    width, height = L.SHEET_SIZES[sheet_size]
    layout = SheetLayout(number, width, height, plan.usable.get(number))
    counts = {"parts": 0, "symbols": 0, "wires": 0, "labels": 0, "texts": 0}
    current: int | None = None
    for op in plan.ops:
        kind = op["op"]
        if kind == "open_sheet":
            current = int(op["number"])
            continue
        if current != number:
            continue
        if kind == "place_part":
            refdes = str(op["refdes"])
            art = _symbol_art(plan, str(op["library"]), str(op["symbol"]))
            attributes = op.get("attributes") or []
            placed = {a["name"] for a in attributes}
            # a part the plan places attributes for shows them there; one it does not
            # shows the symbol's refdes, and Designer puts the part number where the
            # symbol keeps its DEVICE text
            shown = {
                "REFDES": "" if "Ref Designator" in placed else refdes,
                "DEVICE": "" if "Part Number" in placed else str(op.get("part") or ""),
            }
            if art is not None:
                layout.parts[refdes] = _place(layout, art, op, refdes, shown)
            for attribute in attributes:
                is_refdes = attribute["name"] == "Ref Designator"
                layout.texts.append(
                    Text(
                        float(attribute["x"]),
                        float(attribute["y"]),
                        refdes if is_refdes else str(op.get("part") or ""),
                        TEXT_ATTRIBUTE,
                        int(attribute.get("origin") or 3),
                        "symbol" if is_refdes else "value",
                        refdes,
                        "refdes" if is_refdes else "value",
                    )
                )
            counts["parts"] += 1
        elif kind == "place_symbol":
            art = _symbol_art(plan, str(op["library"]), str(op["symbol"]))
            if art is not None:
                netname = next((v for *_, n, v, _vis in art.texts if n == "NETNAME"), "")
                owner = (
                    f"power {netname}" if netname else f"{op['symbol']} at ({op['x']}, {op['y']})"
                )
                _place(layout, art, op, owner, {})
            counts["symbols"] += 1
        elif kind == "wire":
            label = op.get("label")
            owner = f"label {label['net']}" if label else f"wire {op.get('start') or ''}"
            layout.lines.append(
                Line([(float(x), float(y)) for x, y in op["points"]], "wire", owner, 1.5)
            )
            if label:
                layout.texts.append(
                    Text(
                        float(label["x"]),
                        float(label["y"]),
                        str(label["net"]),
                        L.LABEL_HEIGHT,
                        3,
                        "label",
                        f"label {label['net']}",
                    )
                )
                counts["labels"] += 1
            counts["wires"] += 1
        elif kind == "text":
            layout.texts.append(
                Text(float(op["x"]), float(op["y"]), str(op["text"]), op["size"], 3, "text", "text")
            )
            counts["texts"] += 1
        elif kind == "box":
            role = str(op.get("role") or "frame")
            owner = f"label {op['net']}" if role == "label" and op.get("net") else "frame"
            layout.boxes.append(
                Box(
                    (float(op["x1"]), float(op["y1"]), float(op["x2"]), float(op["y2"])),
                    role,
                    owner,
                )
            )
    layout.counts = counts
    return layout


# -- the readability check --------------------------------------------------------------


def _area(a: Rect, b: Rect) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return width * height if width > 0 and height > 0 else 0.0


def _shrink(rect: Rect, by: float) -> Rect:
    return (rect[0] + by, rect[1] + by, rect[2] - by, rect[3] - by)


def _segment_hits(a: Point, b: Point, rect: Rect) -> bool:
    """Whether segment a-b passes through the inside of `rect` (Liang-Barsky)."""
    x0, y0, x1, y1 = rect
    if x0 >= x1 or y0 >= y1:
        return False
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q <= 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 >= t1:
            return False
    return True


def _edges(rect: Rect) -> list[tuple[Point, Point]]:
    x0, y0, x1, y1 = rect
    return [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]


def text_overlaps(layout: SheetLayout) -> list[dict[str, Any]]:
    """DS-17: texts that collide with another text, with a line drawn through them, or
    with a net label's box that is not their own. A text inside a block's frame is
    fine; one the frame's edge runs through is not."""
    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def report(a: Text, other: str, what: str, rect: Rect) -> None:
        key = (a.owner, a.text, other, what)
        if key in seen:
            return
        seen.add(key)
        issues.append(
            {
                "check": "DS-17",
                "sheet": layout.number,
                "object": a.owner,
                "text": a.text,
                "with": other,
                "bbox": [round(v, 1) for v in rect],
                "message": f"{a.owner}: text '{a.text}' {what} {other}",
            }
        )

    boxes = [(text, text.box()) for text in layout.texts]
    if layout.usable:
        # DS-07 checks parts and power symbols; a net label or a part's text that runs
        # past the drawable area is just as far outside it
        ux1, uy1, ux2, uy2 = layout.usable
        for text, rect in boxes:
            outside = rect[0] < ux1 or rect[1] < uy1 or rect[2] > ux2 or rect[3] > uy2
            if outside and (text.role or text.owner.startswith("label ")):
                issues.append(
                    {
                        "check": "DS-07",
                        "sheet": layout.number,
                        "object": text.owner,
                        "text": text.text,
                        "bbox": [round(v, 1) for v in rect],
                        "usable": list(layout.usable),
                        "message": f"{text.owner}: text '{text.text}' is outside the drawable area",
                    }
                )
    for (a, box_a), (b, box_b) in combinations(boxes, 2):
        if _area(box_a, box_b) > OVERLAP_AREA:
            if (b.owner, b.text, f"{a.owner} '{a.text}'", "overlaps text of") in seen:
                continue
            report(a, f"{b.owner} '{b.text}'", "overlaps text of", box_a)
    for text, rect in boxes:
        inner = _shrink(rect, TOUCH)
        for line in layout.lines:
            hit = any(
                _segment_hits(p, q, inner)
                for p, q in zip(line.points, line.points[1:], strict=False)
            )
            if hit:
                what = "wire" if line.colour == "wire" else "lines"
                report(text, f"{line.owner} ({what})", "is crossed by", rect)
        for box in layout.boxes:
            if box.role == "label":
                if box.owner != text.owner and _area(rect, box.rect) > OVERLAP_AREA:
                    report(text, f"the box of {box.owner}", "overlaps", rect)
            elif any(_segment_hits(p, q, inner) for p, q in _edges(box.rect)):
                report(text, "a frame's edge", "is crossed by", rect)
    return issues


def _candidates(extent: Rect, size: float, role: str) -> list[tuple[float, float, int]]:
    """Where a part's refdes or value may go, in order of preference, as (x, y, origin):
    beside the part's extent, one text height clear of it. The refdes prefers above or
    left, the value below or right, as the conventions ask."""
    x1, y1, x2, y2 = extent
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    above, below = y2 + size, y1 - size
    upper, lower = cy + size / 2 + 1, cy - size / 2 - 1
    if role == "refdes":
        return [
            (x1, above, 8),
            (x2, above, 2),
            (x1 - 4, upper, 8),
            (x2 + 4, upper, 2),
            (cx, above, 5),
            (x1, below, 8),
            (x2, below, 2),
            (cx, below, 5),
        ]
    return [
        (x2, above, 2),
        (x2 + 4, lower, 2),
        (x2, below, 2),
        (x1, below, 8),
        (x1 - 4, lower, 8),
        (cx, below, 5),
        (x1, above + size + 2, 8),
        (x2, above + size + 2, 2),
    ]


def _collides(rect: Rect, layout: SheetLayout, texts: list[Rect], owner: str) -> bool:
    if layout.usable:
        ux1, uy1, ux2, uy2 = layout.usable
        if rect[0] < ux1 or rect[1] < uy1 or rect[2] > ux2 or rect[3] > uy2:
            return True
    if any(_area(rect, other) > OVERLAP_AREA for other in texts):
        return True
    inner = _shrink(rect, TOUCH)
    for line in layout.lines:
        if any(
            _segment_hits(p, q, inner) for p, q in zip(line.points, line.points[1:], strict=False)
        ):
            return True
    for box in layout.boxes:
        if box.role == "label":
            if box.owner != owner and _area(rect, box.rect) > OVERLAP_AREA:
                return True
        elif any(_segment_hits(p, q, inner) for p, q in _edges(box.rect)):
            return True
    return False


def place_attributes(plan: L.Plan, sheet_size: str) -> int:
    """Move the refdes and value of every part the planner gave attribute positions to
    and that is not a two-terminal part (IC and connector boxes, test points, mounting
    holes, transistors) to the first spot beside it that collides with nothing; one
    with no free spot keeps its first choice and DS-17 reports it. Returns how many
    attributes moved."""
    moved = 0
    parts = {
        str(op["refdes"]): op
        for op in plan.ops
        if op["op"] == "place_part" and len(plan.symbol_pins.get(str(op["symbol"]), [])) != 2
    }
    for sheet in plan.sheets:
        layout = layout_sheet(plan, int(sheet["number"]), sheet_size)
        movable = [t for t in layout.texts if t.role in {"refdes", "value"} and t.owner in parts]
        fixed = [t.box() for t in layout.texts if t not in movable]
        for text in movable:
            op = parts[text.owner]
            attribute = next(
                a
                for a in op["attributes"]
                if a["name"] == ("Ref Designator" if text.role == "refdes" else "Part Number")
            )
            extent = layout.parts.get(text.owner)
            if extent is None:
                continue
            choices = _candidates(extent, text.size, text.role)
            chosen = choices[0]
            for x, y, origin in choices:
                trial = Text(x, y, text.text, text.size, origin, text.colour, text.owner)
                if not _collides(trial.box(), layout, fixed, text.owner):
                    chosen = (x, y, origin)
                    break
            x, y, origin = chosen
            if (attribute["x"], attribute["y"], attribute["origin"]) != (x, y, origin):
                moved += 1
            attribute.update({"x": round(x), "y": round(y), "origin": origin, "orientation": 0})
            fixed.append(Text(x, y, text.text, text.size, origin, text.colour, text.owner).box())
    return moved


def plan_text_overlaps(plan: L.Plan, sheet_size: str) -> list[dict[str, Any]]:
    """DS-17 for every sheet of a plan."""
    return [
        issue
        for sheet in plan.sheets
        for issue in text_overlaps(layout_sheet(plan, int(sheet["number"]), sheet_size))
    ]


# -- the picture ------------------------------------------------------------------------


def _font(size_px: int) -> Any:
    from PIL import ImageFont

    for candidate in FONTS:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, max(6, size_px))
            except OSError:
                continue
    return ImageFont.load_default()


class _Canvas:
    def __init__(self, width: int, height: int, scale: float) -> None:
        from PIL import Image, ImageDraw

        self.height, self.scale = height, scale
        self.image = Image.new(
            "RGBA", (int(width * scale) + 1, int(height * scale) + 1), COLOURS["background"]
        )
        self.draw = ImageDraw.Draw(self.image)
        self.fonts: dict[int, Any] = {}

    def px(self, x: float, y: float) -> Point:
        return (x * self.scale, (self.height - y) * self.scale)

    def line(self, points: list[Point], colour: str, width: float = 1.0) -> None:
        if len(points) >= 2:
            self.draw.line(
                [self.px(x, y) for x, y in points],
                fill=COLOURS[colour],
                width=max(1, round(width * self.scale / 2)),
            )

    def rect(self, rect: Rect, colour: str, width: float = 1) -> None:
        left, top = self.px(min(rect[0], rect[2]), max(rect[1], rect[3]))
        right, bottom = self.px(max(rect[0], rect[2]), min(rect[1], rect[3]))
        self.draw.rectangle(
            [left, top, right, bottom], outline=COLOURS[colour], width=max(1, round(width))
        )

    def dashed(self, rect: Rect, colour: str) -> None:
        for a, b in _edges(rect):
            count = max(1, int(math.dist(a, b) / 8.0))
            for i in range(0, count, 2):
                t0, t1 = i / count, min(1.0, (i + 1) / count)
                p = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
                q = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
                self.line([p, q], colour)

    def text(self, text: Text) -> None:
        from PIL import Image, ImageDraw

        if not text.text.strip():
            return
        x0, y0, x1, y1 = text.box()
        width = max(1, round((x1 - x0) * self.scale))
        height = max(1, round((y1 - y0) * self.scale))
        font_px = max(6, round(text.size * self.scale * 1.25))
        font = self.fonts.setdefault(font_px, _font(font_px))
        left, top, right, bottom = self.draw.textbbox((0, 0), text.text, font=font)
        tile = Image.new("RGBA", (max(1, right - left + 2), max(1, bottom - top + 2)))
        ImageDraw.Draw(tile).text(
            (1 - left, 1 - top), text.text, font=font, fill=COLOURS[text.colour]
        )
        if text.turns % 2:
            # a quarter-turned symbol's text runs up the sheet
            tile = tile.resize((height, width)).rotate(90, expand=True)
        else:
            tile = tile.resize((width, height))
        px, py = self.px(x0, y1)
        self.image.alpha_composite(tile, (max(0, round(px)), max(0, round(py))))


def render_sheet(
    plan: L.Plan,
    number: int,
    sheet_size: str,
    scale: float = SCALE,
    findings: list[dict[str, Any]] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """The picture of one planned sheet (a Pillow image) and what is on it. `findings`
    are the issues to box in red (the plan's own and DS-17 for the sheet, by default)."""
    layout = layout_sheet(plan, number, sheet_size)
    canvas = _Canvas(layout.width, layout.height, scale)
    canvas.rect((0, 0, layout.width, layout.height), "frame", 2)
    canvas.rect((L.MARGIN, L.MARGIN, layout.width - L.MARGIN, layout.height - L.MARGIN), "frame")
    if layout.usable:
        canvas.dashed(tuple(layout.usable), "usable")  # type: ignore[arg-type]
    for box in layout.boxes:
        canvas.rect(box.rect, "box")
    for line in layout.lines:
        canvas.line(line.points, line.colour, line.width)
    for text in layout.texts:
        canvas.text(text)
    if findings is None:
        # the plan's own findings, DS-17 among them
        findings = [i for i in plan.issues if i.get("sheet", number) == number]
    marked = 0
    for issue in findings:
        targets: list[Rect] = []
        if issue.get("bbox"):
            targets.append(tuple(issue["bbox"]))  # type: ignore[arg-type]
        for name in [issue.get("object"), *(issue.get("objects") or [])]:
            if isinstance(name, str) and name in layout.parts and not issue.get("bbox"):
                targets.append(layout.parts[name])
        for x1, y1, x2, y2 in targets:
            canvas.rect((x1 - 2, y1 - 2, x2 + 2, y2 + 2), "issue", 2)
        if targets:
            marked += 1
    info = {
        "sheet": number,
        **layout.counts,
        "issues": len(findings),
        "issues_marked": marked,
        "width": canvas.image.width,
        "height": canvas.image.height,
    }
    return canvas.image, info


def render(
    plan: L.Plan,
    sheet_size: str,
    output: Path,
    sheets: list[int] | None = None,
    scale: float = SCALE,
    replace: bool = False,
) -> list[dict[str, Any]]:
    """Write one PNG per planned sheet: `output` itself for a single sheet, else
    `<stem>-sheet<N>.png` beside it. Refuses to overwrite unless `replace`."""
    numbers = [int(s["number"]) for s in plan.sheets]
    chosen = [n for n in numbers if not sheets or n in sheets]
    targets = {
        n: output
        if len(chosen) == 1
        else output.with_name(f"{output.stem}-sheet{n}{output.suffix or '.png'}")
        for n in chosen
    }
    if not replace:
        existing = [str(path) for path in targets.values() if path.exists()]
        if existing:
            raise FileExistsError(existing[0])
    width, height = L.SHEET_SIZES[sheet_size]
    scale = min(scale, MAX_EDGE / max(width, height))
    pictures = []
    for number in chosen:
        image, info = render_sheet(plan, number, sheet_size, scale)
        path = targets[number]
        path.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(path, "PNG")
        pictures.append({**info, "scale": round(scale, 3), "path": str(path)})
    return pictures
