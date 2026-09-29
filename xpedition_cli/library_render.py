"""A part as a picture: its symbol beside its footprint, for an agent to look at.

The symbol panel draws the symbol's lines and pins with their numbers and names;
the footprint panel draws the lands (pin numbers on them), drilled holes, the
silkscreen, the assembly outline and the placement outline, with a 1 mm scale bar.
Both come from records as `library_read` parses them, so a part in the library and a
part in a parts file not yet added are drawn the same way -- the file's content is
rendered to HKP text and parsed back first, exactly what the import would read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PANEL = 560  # pixels a panel's drawing may take on its long side
MARGIN = 40
HEADER = 64
COLOURS = {
    "paper": (250, 250, 247, 255),
    "board": (18, 22, 34, 255),
    "ink": (30, 30, 30, 255),
    "pin": (150, 30, 30, 255),
    "pin_number": (40, 70, 160, 255),
    "copper": (205, 70, 60, 255),
    "copper_multi": (225, 140, 50, 255),
    "hole": (10, 10, 10, 255),
    "silk": (235, 235, 225, 255),
    "assembly": (120, 150, 190, 255),
    "placement": (210, 80, 210, 255),
    "text": (240, 240, 240, 255),
    "title": (20, 20, 20, 255),
}


def _font(size: int) -> Any:
    from .schematic_render import _font as font

    return font(size)


def _bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points] or [0.0]
    ys = [p[1] for p in points] or [0.0]
    return min(xs), min(ys), max(xs), max(ys)


def _land_points(pin: dict[str, Any], pad: dict[str, Any] | None) -> list[tuple[float, float]]:
    width = float((pad or {}).get("width") or 0.5)
    height = float((pad or {}).get("height") or width)
    x, y = pin["x"], pin["y"]
    if (pin.get("rotation") or 0) % 180 == 90:
        width, height = height, width
    return [(x - width / 2, y - height / 2), (x + width / 2, y + height / 2)]


def _symbol_panel(symbol: dict[str, Any] | None) -> Any:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (PANEL + 2 * MARGIN, PANEL + 2 * MARGIN), COLOURS["paper"])
    draw = ImageDraw.Draw(image)
    if symbol is None:
        draw.text((MARGIN, MARGIN), "no symbol", fill=COLOURS["ink"], font=_font(16))
        return image
    points = [tuple(p) for line in symbol["lines"] for p in line]
    points += [(p["x"], p["y"]) for p in symbol["pins"]]
    x1, y1, x2, y2 = _bounds(points)  # type: ignore[arg-type]
    pad = 24.0  # sheet units around the symbol, for the pin texts
    x1, y1, x2, y2 = x1 - pad, y1 - pad, x2 + pad, y2 + pad
    scale = min(PANEL / max(x2 - x1, 1.0), PANEL / max(y2 - y1, 1.0), 6.0)
    ox = MARGIN + (PANEL - (x2 - x1) * scale) / 2
    oy = MARGIN + (PANEL - (y2 - y1) * scale) / 2

    def px(x: float, y: float) -> tuple[float, float]:
        return (ox + (x - x1) * scale, oy + (y2 - y) * scale)

    for line in symbol["lines"]:
        if len(line) >= 2:
            draw.line([px(*p) for p in line], fill=COLOURS["ink"], width=2)
    small = _font(max(10, int(scale * 5)))
    for pin in symbol["pins"]:
        draw.line([px(pin["x"], pin["y"]), px(pin["bx"], pin["by"])], fill=COLOURS["pin"], width=2)
        ex, ey = px(pin["x"], pin["y"])
        draw.ellipse([ex - 3, ey - 3, ex + 3, ey + 3], outline=COLOURS["pin"], width=1)
        bx, by = px(pin["bx"], pin["by"])
        mx, my = (ex + bx) / 2, (ey + by) / 2
        number, name = str(pin["number"]), str(pin["name"])
        side = pin.get("side", "left")
        if side in ("left", "right"):
            draw.text((mx, my - 4), number, fill=COLOURS["pin_number"], font=small, anchor="md")
            anchor = "lm" if side == "left" else "rm"
            nx = bx + (6 if side == "left" else -6)
            draw.text((nx, by), name, fill=COLOURS["ink"], font=small, anchor=anchor)
        else:
            draw.text((mx + 4, my), number, fill=COLOURS["pin_number"], font=small, anchor="lm")
            anchor = "ms" if side == "bottom" else "mt"
            ny = by + (-6 if side == "bottom" else 6)
            draw.text((bx, ny), name, fill=COLOURS["ink"], font=small, anchor=anchor)
    return image


def _footprint_panel(cell: dict[str, Any] | None, padstacks: dict[str, Any]) -> Any:
    from PIL import Image, ImageDraw

    from .library_read import padstack_geometry

    image = Image.new("RGBA", (PANEL + 2 * MARGIN, PANEL + 2 * MARGIN), COLOURS["board"])
    draw = ImageDraw.Draw(image)
    if cell is None:
        draw.text((MARGIN, MARGIN), "no cell", fill=COLOURS["text"], font=_font(16))
        return image
    geometry = {
        pin["padstack"]: padstack_geometry(padstacks, pin["padstack"]) or {}
        for pin in cell["pins"] + cell["holes"]
    }
    points: list[tuple[float, float]] = []
    for pin in cell["pins"] + cell["holes"]:
        found = geometry.get(pin["padstack"]) or {}
        points += _land_points(pin, found.get("pad") or found.get("hole"))
    for block in ("placement", "assembly", "silkscreen"):
        for graphic in cell.get(block, []):
            points += [tuple(p) for p in graphic["points"]]  # type: ignore[misc]
    if not points:
        points = [(-1.0, -1.0), (1.0, 1.0)]
    x1, y1, x2, y2 = _bounds(points)
    margin = 0.6
    x1, y1, x2, y2 = x1 - margin, y1 - margin, x2 + margin, y2 + margin
    scale = min(PANEL / max(x2 - x1, 0.5), PANEL / max(y2 - y1, 0.5), 200.0)
    ox = MARGIN + (PANEL - (x2 - x1) * scale) / 2
    oy = MARGIN + (PANEL - (y2 - y1) * scale) / 2

    def px(x: float, y: float) -> tuple[float, float]:
        return (ox + (x - x1) * scale, oy + (y2 - y) * scale)

    def stroke(graphic: dict[str, Any], colour: tuple[int, ...], minimum: int = 1) -> None:
        width = max(minimum, round(float(graphic.get("width") or 0) * scale))
        if graphic["kind"] == "circle" and graphic["points"]:
            cx, cy = px(*graphic["points"][0])
            radius = float(graphic.get("radius") or 0) * scale
            draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius], outline=colour, width=width
            )
            return
        path = [px(*p) for p in graphic["points"]]
        if graphic["kind"] == "shape" and len(path) >= 3:
            draw.polygon(path, fill=colour)
        elif len(path) >= 2:
            draw.line(path, fill=colour, width=width)

    for graphic in cell.get("placement", []):
        stroke(graphic, COLOURS["placement"])
    for graphic in cell.get("assembly", []):
        stroke(graphic, COLOURS["assembly"])
    counts: dict[str, int] = {}
    for pin in cell["pins"]:
        counts[pin["number"]] = counts.get(pin["number"], 0) + 1
    label = _font(max(10, min(22, int(scale * 0.35))))
    for pin in cell["pins"] + cell["holes"]:
        found = geometry.get(pin["padstack"]) or {}
        pad = found.get("pad")
        hole = found.get("hole")
        colour = COLOURS["copper_multi"] if counts.get(pin["number"], 0) > 1 else COLOURS["copper"]
        if pad:
            (ax, ay), (bx, by) = _land_points(pin, pad)
            left, top = px(ax, by)
            right, bottom = px(bx, ay)
            shape = str(pad.get("shape", ""))
            if shape == "ROUND" or (shape == "OBLONG" and abs(pad["width"] - pad["height"]) < 1e-6):
                draw.ellipse([left, top, right, bottom], fill=colour)
            elif shape in ("OBLONG", "RADIUS_CORNER_RECTANGLE"):
                radius = (
                    min(right - left, bottom - top) / 2
                    if shape == "OBLONG"
                    else float(pad.get("radius") or 0) * scale
                )
                draw.rounded_rectangle([left, top, right, bottom], radius=radius, fill=colour)
            else:
                draw.rectangle([left, top, right, bottom], fill=colour)
        if hole:
            cx, cy = px(pin["x"], pin["y"])
            half_w = float(hole.get("width") or 0) * scale / 2
            half_h = float(hole.get("height") or hole.get("width") or 0) * scale / 2
            box = [cx - half_w, cy - half_h, cx + half_w, cy + half_h]
            if abs(half_w - half_h) < 0.5:
                draw.ellipse(box, fill=COLOURS["hole"])
            else:
                # a slot: a stadium along its longer side
                draw.rounded_rectangle(box, radius=min(half_w, half_h), fill=COLOURS["hole"])
        if pin.get("number"):
            cx, cy = px(pin["x"], pin["y"])
            draw.text((cx, cy), str(pin["number"]), fill=COLOURS["text"], font=label, anchor="mm")
    for graphic in cell.get("silkscreen", []):
        stroke(graphic, COLOURS["silk"], 2)
    for text in cell.get("texts", []):
        if "SILK" in str(text.get("layer", "")):
            cx, cy = px(text["x"], text["y"])
            size = max(10, int(float(text.get("height") or 0.8) * scale))
            draw.text((cx, cy), "REF", fill=COLOURS["silk"], font=_font(size), anchor="mm")
    # a 1 mm scale bar in the lower left
    bar = scale
    base_y = PANEL + 2 * MARGIN - 14
    draw.line([(MARGIN, base_y), (MARGIN + bar, base_y)], fill=COLOURS["text"], width=3)
    draw.text((MARGIN + bar + 6, base_y), "1 mm", fill=COLOURS["text"], font=_font(12), anchor="lm")
    return image


def render(view: dict[str, Any], output: Path, *, replace: bool = False) -> dict[str, Any]:
    """Write the part's picture to `output` (PNG): a title line, the symbol, the footprint.

    `view` holds `number`, `description`, `symbol` (a parsed symbol, or None), `cell` (a
    parsed cell, or None) and `padstacks` (a parsed padstack database).
    """
    from PIL import Image, ImageDraw

    if output.exists() and not replace:
        raise FileExistsError(str(output))
    left = _symbol_panel(view.get("symbol"))
    right = _footprint_panel(view.get("cell"), view.get("padstacks") or {})
    width = left.width + right.width
    image = Image.new("RGBA", (width, left.height + HEADER), COLOURS["paper"])
    image.alpha_composite(left, (0, HEADER))
    image.alpha_composite(right, (left.width, HEADER))
    draw = ImageDraw.Draw(image)
    title = str(view.get("number") or "")
    description = str(view.get("description") or "")
    symbol = view.get("symbol") or {}
    cell = view.get("cell") or {}
    draw.text((MARGIN, 10), title, fill=COLOURS["title"], font=_font(22))
    subtitle = " | ".join(
        item
        for item in (
            description[:90],
            f"symbol {symbol.get('partition', '')}:{symbol.get('name', '')}" if symbol else "",
            f"cell {cell.get('name', '')}" if cell else "",
        )
        if item
    )
    draw.text((MARGIN, 38), subtitle, fill=COLOURS["ink"], font=_font(13))
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, "PNG")
    lands = len(cell.get("pins", [])) if cell else 0
    numbers = {pin["number"] for pin in cell.get("pins", [])} if cell else set()
    return {
        "path": str(output),
        "width": image.width,
        "height": image.height,
        "symbol_pins": len(symbol.get("pins", [])) if symbol else 0,
        "lands": lands,
        "pin_numbers": len(numbers),
        "holes": len(cell.get("holes", [])) if cell else 0,
        "extent_mm": _extent(cell) if cell else None,
    }


def _extent(cell: dict[str, Any]) -> list[float] | None:
    points = [tuple(p) for g in cell.get("placement", []) for p in g["points"]]
    if not points:
        return None
    x1, y1, x2, y2 = _bounds(points)  # type: ignore[arg-type]
    return [round(x2 - x1, 3), round(y2 - y1, 3)]
