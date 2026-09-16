"""A picture of a board from its geometry, without a screen.

Layout's own window capture needs an unlocked desktop; the automation, on the other
hand, hands out every shape on the board (outline, pads, traces, vias, generated
planes, silkscreen graphics and texts). This module draws that data with Pillow the
way KiCad's editor colours a board: top copper red, bottom copper blue, planes
translucent, silkscreen light, drills dark, on a dark background.

The model is plain data (see `Model`), so the drawing is testable without
Xpedition. Coordinates are millimetres, y up, as Layout reports them; a path is a
list of `(x, y, r)` rows where a row with `r != 0` is the centre of an arc from the
previous row to the next one (`r > 0` clockwise, `r < 0` counter-clockwise, the way
Layout's `PointsArray` reports and takes them).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Point = tuple[float, float, float]

COLOURS = {
    "background": (0, 16, 35, 255),
    "copper_top": (200, 52, 52, 255),
    "copper_bottom": (77, 127, 196, 255),
    "copper_inner": (170, 120, 60, 255),
    "plane_alpha": 150,
    "far_alpha": 90,
    "pad_through": (194, 194, 0, 255),
    "via": (236, 236, 236, 255),
    "drill": (10, 10, 10, 255),
    "silk_top": (240, 240, 240, 255),
    "silk_bottom": (232, 178, 167, 255),
    "outline": (208, 210, 205, 255),
    "hole_ring": (120, 120, 120, 255),
}
ARC_STEP_DEG = 6.0
FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@dataclass
class Shape:
    """One filled or stroked outline on a layer (pad, trace, plane, silkscreen line)."""

    path: list[Point]
    layer: int = 0  # copper layer number; 0 for non-copper
    side: str = "top"  # for silkscreen
    width: float = 0.0  # stroke width (mm); 0 = filled polygon
    circle: tuple[float, float, float] | None = None  # (x, y, r) when the shape is a circle
    cutouts: list[list[Point]] = field(default_factory=list)
    net: str = ""
    kind: str = ""  # pad / via / trace / plane / silk / outline


@dataclass
class Text:
    x: float
    y: float
    text: str
    height: float
    orientation: float = 0.0
    side: str = "top"
    mirrored: bool = False


@dataclass
class Model:
    layers: int = 2
    outline: list[Shape] = field(default_factory=list)
    pads: list[Shape] = field(default_factory=list)
    vias: list[Shape] = field(default_factory=list)
    traces: list[Shape] = field(default_factory=list)
    planes: list[Shape] = field(default_factory=list)
    holes: list[tuple[float, float, float]] = field(default_factory=list)  # x, y, diameter
    silk: list[Shape] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)

    def bounds(self) -> tuple[float, float, float, float]:
        xs: list[float] = []
        ys: list[float] = []
        shapes = self.outline or (self.pads + self.traces + self.planes)
        for shape in shapes:
            for x, y in flatten(shape.path):
                xs.append(x)
                ys.append(y)
            if shape.circle:
                cx, cy, r = shape.circle
                xs.extend((cx - r, cx + r))
                ys.extend((cy - r, cy + r))
        if not xs:
            return (0.0, 0.0, 10.0, 10.0)
        return (min(xs), min(ys), max(xs), max(ys))


def flatten(path: list[Point]) -> list[tuple[float, float]]:
    """The path as straight segments, the arcs stepped every few degrees."""
    points: list[tuple[float, float]] = []
    count = len(path)
    for index, (x, y, r) in enumerate(path):
        if r and 0 < index < count - 1:
            px, py, _ = path[index - 1]
            nx, ny, _ = path[index + 1]
            start = math.atan2(py - y, px - x)
            end = math.atan2(ny - y, nx - x)
            radius = math.hypot(px - x, py - y) or abs(r)
            sweep = (end - start) % (2 * math.pi)  # the counter-clockwise way round
            if r > 0:
                sweep = sweep - 2 * math.pi  # positive radius: clockwise
            if abs(sweep) < 1e-9:
                # a full circle described as two half arcs, or a degenerate arc
                sweep = -2 * math.pi if r > 0 else 2 * math.pi
            steps = max(2, int(abs(sweep) / math.radians(ARC_STEP_DEG)))
            for step in range(1, steps):
                angle = start + sweep * step / steps
                points.append((x + radius * math.cos(angle), y + radius * math.sin(angle)))
            continue
        points.append((x, y))
    return points


def model_from_dict(data: dict[str, Any]) -> Model:
    """A `Model` from the plain data the adapter returns (or a JSON file)."""

    def shape(item: dict[str, Any]) -> Shape:
        return Shape(
            path=[tuple(row) for row in item.get("path") or []],  # type: ignore[misc]
            layer=int(item.get("layer") or 0),
            side=str(item.get("side") or "top"),
            width=float(item.get("width") or 0.0),
            circle=tuple(item["circle"]) if item.get("circle") else None,  # type: ignore[arg-type]
            cutouts=[[tuple(row) for row in cut] for cut in item.get("cutouts") or []],  # type: ignore[misc]
            net=str(item.get("net") or ""),
            kind=str(item.get("kind") or ""),
        )

    model = Model(layers=int(data.get("layers") or 2))
    for key in ("outline", "pads", "vias", "traces", "planes", "silk"):
        setattr(model, key, [shape(item) for item in data.get(key) or []])
    model.holes = [tuple(row) for row in data.get("holes") or []]  # type: ignore[misc]
    model.texts = [
        Text(
            x=float(item["x"]),
            y=float(item["y"]),
            text=str(item.get("text") or ""),
            height=float(item.get("height") or 1.0),
            orientation=float(item.get("orientation") or 0.0),
            side=str(item.get("side") or "top"),
            mirrored=bool(item.get("mirrored", False)),
        )
        for item in data.get("texts") or []
    ]
    return model


class _Canvas:
    """Board millimetres → image pixels, with the y axis turned and the side chosen."""

    def __init__(self, model: Model, scale: float, side: str, margin: float) -> None:
        from PIL import Image

        self.side = side
        min_x, min_y, max_x, max_y = model.bounds()
        self.scale = scale
        self.margin = margin
        self.min_x, self.min_y, self.max_x, self.max_y = min_x, min_y, max_x, max_y
        self.width = int(math.ceil((max_x - min_x + 2 * margin) * scale))
        self.height = int(math.ceil((max_y - min_y + 2 * margin) * scale))
        self.image = Image.new("RGBA", (self.width, self.height), COLOURS["background"])

    def px(self, x: float, y: float) -> tuple[float, float]:
        if self.side == "bottom":
            x = self.max_x + self.min_x - x  # looking from below: mirrored
        return (
            (x - self.min_x + self.margin) * self.scale,
            (self.max_y - y + self.margin) * self.scale,
        )

    def layer(self) -> Any:
        from PIL import Image

        return Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))

    def composite(self, layer: Any) -> None:
        from PIL import Image

        self.image = Image.alpha_composite(self.image, layer)


def _with_alpha(colour: tuple[int, int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return (colour[0], colour[1], colour[2], alpha)


def _draw_shape(draw: Any, canvas: _Canvas, shape: Shape, colour: Any) -> None:
    if shape.circle:
        cx, cy, r = shape.circle
        x, y = canvas.px(cx, cy)
        rp = r * canvas.scale
        if shape.width:
            wp = max(1.0, shape.width * canvas.scale)
            draw.ellipse((x - rp, y - rp, x + rp, y + rp), outline=colour, width=int(wp))
        else:
            draw.ellipse((x - rp, y - rp, x + rp, y + rp), fill=colour)
        return
    points = [canvas.px(x, y) for x, y in flatten(shape.path)]
    if len(points) < 2:
        if points and shape.width:
            x, y = points[0]
            rp = shape.width * canvas.scale / 2
            draw.ellipse((x - rp, y - rp, x + rp, y + rp), fill=colour)
        return
    if shape.width:
        wp = max(1.0, shape.width * canvas.scale)
        draw.line(points, fill=colour, width=int(round(wp)), joint="curve")
        rp = wp / 2
        for x, y in (points[0], points[-1]):
            draw.ellipse((x - rp, y - rp, x + rp, y + rp), fill=colour)
        return
    if len(points) >= 3:
        draw.polygon(points, fill=colour)
    for cut in shape.cutouts:
        cut_points = [canvas.px(x, y) for x, y in flatten(cut)]
        if len(cut_points) >= 3:
            draw.polygon(cut_points, fill=(0, 0, 0, 0))


def _copper_colour(layer: int, layers: int) -> tuple[int, int, int, int]:
    if layer <= 1:
        return COLOURS["copper_top"]
    if layer >= layers:
        return COLOURS["copper_bottom"]
    return COLOURS["copper_inner"]


def _font(size_px: int) -> Any:
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, max(6, size_px))
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_text(canvas: _Canvas, layer: Any, text: Text, colour: Any) -> None:
    from PIL import Image, ImageDraw

    if not text.text.strip():
        return
    size = max(6, int(round(text.height * canvas.scale * 1.35)))
    font = _font(size)
    probe = ImageDraw.Draw(layer)
    left, top, right, bottom = probe.textbbox((0, 0), text.text, font=font)
    width, height = int(right - left) + 4, int(bottom - top) + 4
    tile = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((2 - left, 2 - top), text.text, font=font, fill=colour)
    angle = text.orientation % 360
    mirror = text.mirrored != (canvas.side == "bottom")
    if mirror:
        tile = tile.transpose(Image.FLIP_LEFT_RIGHT)
        angle = (-angle) % 360
    if angle:
        tile = tile.rotate(angle, expand=True, resample=Image.BICUBIC)
    x, y = canvas.px(text.x, text.y)
    layer.alpha_composite(tile, (int(round(x - tile.width / 2)), int(round(y - tile.height / 2))))


def render(
    model: Model,
    output: Path,
    scale: float = 20.0,
    side: str = "top",
    margin: float = 2.0,
    supersample: int = 2,
) -> dict[str, Any]:
    """Draw the model to `output` (PNG); returns the picture's size and what it holds."""
    from PIL import Image, ImageDraw

    side = "bottom" if str(side).lower() == "bottom" else "top"
    canvas = _Canvas(model, scale * supersample, side, margin)
    near = 1 if side == "top" else model.layers
    far = model.layers if side == "top" else 1
    counts = {
        "pads": len(model.pads),
        "vias": len(model.vias),
        "traces": len(model.traces),
        "planes": len(model.planes),
        "holes": len(model.holes),
        "silk": len(model.silk),
        "texts": len(model.texts),
    }

    def copper(layer_number: int, alpha: int) -> None:
        colour = _with_alpha(_copper_colour(layer_number, model.layers), alpha)
        plane_layer = canvas.layer()
        plane_draw = ImageDraw.Draw(plane_layer)
        for plane in model.planes:
            if plane.layer == layer_number:
                _draw_shape(plane_draw, canvas, plane, _with_alpha(colour, COLOURS["plane_alpha"]))
        canvas.composite(plane_layer)
        layer = canvas.layer()
        draw = ImageDraw.Draw(layer)
        for trace in model.traces:
            if trace.layer == layer_number:
                _draw_shape(draw, canvas, trace, colour)
        for pad in model.pads:
            if pad.layer == layer_number:
                _draw_shape(draw, canvas, pad, colour)
        canvas.composite(layer)

    # the far side first, dimmed; inner layers are not drawn
    copper(far, COLOURS["far_alpha"])
    copper(near, 255)
    layer = canvas.layer()
    draw = ImageDraw.Draw(layer)
    for via in model.vias:
        _draw_shape(draw, canvas, via, COLOURS["via"])
    for x, y, diameter in model.holes:
        px, py = canvas.px(x, y)
        rp = diameter / 2 * canvas.scale
        draw.ellipse((px - rp, py - rp, px + rp, py + rp), fill=COLOURS["drill"])
    canvas.composite(layer)
    # silkscreen of the near side, then its texts
    silk_colour = COLOURS["silk_top"] if side == "top" else COLOURS["silk_bottom"]
    layer = canvas.layer()
    draw = ImageDraw.Draw(layer)
    for item in model.silk:
        if item.side == side:
            _draw_shape(draw, canvas, item, silk_colour)
    canvas.composite(layer)
    layer = canvas.layer()
    for text in model.texts:
        if text.side == side:
            _draw_text(canvas, layer, text, silk_colour)
    canvas.composite(layer)
    layer = canvas.layer()
    draw = ImageDraw.Draw(layer)
    for item in model.outline:
        outline = Shape(path=item.path, width=item.width or 0.15, circle=item.circle)
        _draw_shape(draw, canvas, outline, COLOURS["outline"])
    canvas.composite(layer)
    image = canvas.image
    if supersample > 1:
        image = image.resize(
            (max(1, image.width // supersample), max(1, image.height // supersample)),
            Image.LANCZOS,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(str(output), format="PNG")
    return {
        "path": str(output),
        "width": image.width,
        "height": image.height,
        "side": side,
        "scale_px_per_mm": scale,
        "bounds_mm": [
            round(canvas.min_x, 3),
            round(canvas.min_y, 3),
            round(canvas.max_x, 3),
            round(canvas.max_y, 3),
        ],
        "counts": counts,
    }
