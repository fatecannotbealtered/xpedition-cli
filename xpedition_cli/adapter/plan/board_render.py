"""A picture of the board from its geometry, drawn without a screen or an image library.

pcb render reads the board as data (outline, cutouts, pads, traces, vias, plane shapes,
soldermask openings, holes) and this module draws it as a PNG, the way a board editor colours it: top copper
red, bottom copper blue, inner layers amber, plane shapes translucent, vias white with a dark
drill, the outline light, soldermask openings green, on a dark background. Seen from the top the
picture is the board as Layout shows it; seen from the bottom it is mirrored left to right.

Only the Python standard library is used: polygons are filled by scanlines, a stroked path is its
segments as quadrilaterals with round joints, and the PNG is written with zlib. Coordinates are mm,
y up; pixels count from the top left.
"""
from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass, field

from .shapes import flatten

Point = tuple[float, float]
MAX_PIXELS = 4000  # the longest side of the picture
MARGIN_MM = 1.0

BACKGROUND = (0, 16, 35)
COPPER = {"top": (200, 52, 52), "bottom": (77, 127, 196), "inner": (190, 140, 60)}
VIA = (236, 236, 236)
DRILL = (10, 10, 10)
OUTLINE = (208, 210, 205)
OPENING = (60, 200, 90)


@dataclass
class Picture:
    """What pcb render draws, in mm."""

    outline: list[Point]
    layer_count: int
    pads: list[tuple[int, list[Point]]] = field(default_factory=list)  # (layer, polygon)
    traces: list[tuple[int, list[Point], float]] = field(default_factory=list)  # (layer, points, width)
    planes: list[tuple[int, list[Point]]] = field(default_factory=list)  # plane shapes, drawn on a layer with no copper made for it
    copper: list[tuple[int, list[list[Point]]]] = field(default_factory=list)  # the plane copper Layout made: (layer, [outline, holes...])
    vias: list[tuple[float, float, float]] = field(default_factory=list)  # (x, y, pad radius)
    holes: list[tuple[float, float, float]] = field(default_factory=list)  # (x, y, diameter)
    openings: list[tuple[str, tuple[float, float, float, float]]] = field(default_factory=list)  # (side, box)
    cutouts: list[list[Point]] = field(default_factory=list)  # internal contours: slots and cutouts through the board


class Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.pixels = bytearray(bytes(BACKGROUND) * (width * height))

    def _blend(self, index: int, colour: tuple[int, int, int], alpha: float) -> None:
        offset = index * 3
        if alpha >= 1.0:
            self.pixels[offset:offset + 3] = bytes(colour)
            return
        for k in range(3):
            self.pixels[offset + k] = int(self.pixels[offset + k] * (1 - alpha) + colour[k] * alpha)

    def polygon(self, points: list[Point], colour: tuple[int, int, int], alpha: float = 1.0) -> None:
        """Fill a polygon given in pixels (even-odd), sampling each pixel at its centre."""
        self.rings([points], colour, alpha)

    def rings(self, rings: list[list[Point]], colour: tuple[int, int, int], alpha: float = 1.0) -> None:
        """Fill the area inside the first ring and outside the others, its holes (even-odd), in pixels."""
        rings = [ring for ring in rings if len(ring) >= 3]
        if not rings:
            return
        ys = [p[1] for ring in rings for p in ring]
        top, bottom = max(0, int(math.floor(min(ys)))), min(self.height - 1, int(math.ceil(max(ys))))
        edges = [(ring[i], ring[(i + 1) % len(ring)]) for ring in rings for i in range(len(ring))]
        for row in range(top, bottom + 1):
            y = row + 0.5
            xs = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0) for (x0, y0), (x1, y1) in edges if (y0 > y) != (y1 > y))
            for left, right in zip(xs[::2], xs[1::2], strict=False):
                start, end = max(0, int(math.ceil(left - 0.5))), min(self.width - 1, int(math.floor(right - 0.5)))
                for column in range(start, end + 1):
                    self._blend(row * self.width + column, colour, alpha)

    def circle(self, x: float, y: float, r: float, colour: tuple[int, int, int], alpha: float = 1.0) -> None:
        r = max(r, 0.5)
        for row in range(max(0, int(y - r)), min(self.height - 1, int(y + r)) + 1):
            dy = row + 0.5 - y
            if abs(dy) > r:
                continue
            half = math.sqrt(r * r - dy * dy)
            for column in range(max(0, int(math.ceil(x - half - 0.5))), min(self.width - 1, int(math.floor(x + half - 0.5))) + 1):
                self._blend(row * self.width + column, colour, alpha)

    def stroke(self, points: list[Point], width: float, colour: tuple[int, int, int], alpha: float = 1.0) -> None:
        """A path of ``width`` pixels: each segment a quadrilateral, each joint a disc."""
        half = max(width / 2, 0.5)
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            length = math.hypot(x1 - x0, y1 - y0)
            if length == 0:
                continue
            nx, ny = -(y1 - y0) / length * half, (x1 - x0) / length * half
            self.polygon([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)], colour, alpha)
        for x, y in points:
            self.circle(x, y, half, colour, alpha)

    def png(self) -> bytes:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        stride = self.width * 3
        raw = b"".join(b"\x00" + bytes(self.pixels[row * stride:(row + 1) * stride]) for row in range(self.height))
        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def polygon_of(path: list[list[float]] | None = None, circle: list[float] | None = None) -> list[Point]:
    """A geometry record as a polygon in mm: a path with its arcs flattened, a circle as 24 sides."""
    if circle is not None:
        x, y, r = circle
        return [(x + r * math.cos(k * math.pi / 12), y + r * math.sin(k * math.pi / 12)) for k in range(24)]
    return flatten([(p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in path or []])


def layer_colour(layer: int, layer_count: int) -> tuple[int, int, int]:
    if layer == 1:
        return COPPER["top"]
    if layer == layer_count:
        return COPPER["bottom"]
    return COPPER["inner"]


def drawn(picture: Picture, *, side: str = "top", layers: list[int] | None = None) -> dict[str, int]:
    """What ``render`` draws with the same choices: the pads, traces and plane shapes on the layers
    drawn (a shape counts whether its copper or its outline is drawn), the pieces of plane copper
    Layout made on them, every via, and the openings on the side drawn."""
    wanted = set(layers or range(1, picture.layer_count + 1))
    return {"pads": sum(1 for layer, _poly in picture.pads if layer in wanted),
            "traces": sum(1 for layer, _points, _width in picture.traces if layer in wanted),
            "vias": len(picture.vias),
            "planes": sum(1 for layer, _poly in picture.planes if layer in wanted),
            "plane_copper": sum(1 for layer, _rings in picture.copper if layer in wanted),
            "openings": sum(1 for opening_side, _box in picture.openings if opening_side == side)}


def render(picture: Picture, *, side: str = "top", layers: list[int] | None = None, scale: float = 20.0) -> tuple[bytes, int, int]:
    """The PNG of the picture and its size in pixels. ``layers`` are the copper layers drawn (every
    layer by default), the far ones first so the near side lies on top; ``scale`` is pixels per mm,
    lowered when the picture would pass ``MAX_PIXELS``."""
    xs, ys = [p[0] for p in picture.outline], [p[1] for p in picture.outline]
    x0, x1, y0, y1 = min(xs) - MARGIN_MM, max(xs) + MARGIN_MM, min(ys) - MARGIN_MM, max(ys) + MARGIN_MM
    scale = min(scale, MAX_PIXELS / max(x1 - x0, y1 - y0))
    width, height = max(1, int(math.ceil((x1 - x0) * scale))), max(1, int(math.ceil((y1 - y0) * scale)))
    canvas = Canvas(width, height)

    def px(point: Point) -> Point:
        x = (point[0] - x0) * scale if side == "top" else (x1 - point[0]) * scale
        return x, (y1 - point[1]) * scale

    wanted = layers or list(range(1, picture.layer_count + 1))
    order = sorted(wanted, reverse=(side == "top"))  # far layers first
    for layer in order:
        colour = layer_colour(layer, picture.layer_count)
        near = layer == (1 if side == "top" else picture.layer_count)
        made = [rings for copper_layer, rings in picture.copper if copper_layer == layer]
        for rings in made:  # the copper Layout made, with its clearances, hatching and islands
            canvas.rings([[px(p) for p in ring] for ring in rings], colour, 0.45 if near else 0.25)
        for plane_layer, poly in picture.planes if not made else ():
            if plane_layer == layer:
                canvas.polygon([px(p) for p in poly], colour, 0.35 if near else 0.2)
        for trace_layer, points, trace_width in picture.traces:
            if trace_layer == layer:
                canvas.stroke([px(p) for p in points], trace_width * scale, colour, 1.0 if near else 0.6)
        for pad_layer, poly in picture.pads:
            if pad_layer == layer:
                canvas.polygon([px(p) for p in poly], colour, 1.0 if near else 0.6)
    for x, y, radius in picture.vias:
        cx, cy = px((x, y))
        canvas.circle(cx, cy, radius * scale, VIA)
        canvas.circle(cx, cy, radius * scale * 0.5, DRILL)
    for x, y, diameter in picture.holes:
        cx, cy = px((x, y))
        canvas.circle(cx, cy, diameter / 2 * scale, DRILL)
    for cutout in picture.cutouts:
        corners = [px(p) for p in cutout]
        canvas.polygon(corners, BACKGROUND)
        canvas.stroke(corners + corners[:1], max(1.5, 0.1 * scale), OUTLINE)
    for opening_side, (bx0, by0, bx1, by1) in picture.openings:
        if opening_side == side:
            corners = [px((bx0, by0)), px((bx1, by0)), px((bx1, by1)), px((bx0, by1)), px((bx0, by0))]
            canvas.stroke(corners, max(1.5, 0.08 * scale), OPENING)
    outline = [px(p) for p in picture.outline]
    canvas.stroke(outline + outline[:1], max(1.5, 0.1 * scale), OUTLINE)
    return canvas.png(), width, height
