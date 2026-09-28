"""Footprints from a datasheet's dimensions: IPC-7351B land patterns as cells.

Every land comes from three sums (IPC-7351B, section 3):

    Zmax = Lmin + 2 JT + sqrt(CL² + F² + P²)    outer edge to outer edge
    Gmin = Smax - 2 JH - sqrt(CS² + F² + P²)    inner edge to inner edge
    Xmax = Wmin + 2 JS + sqrt(CW² + F² + P²)    pad width

L is the span over the leads (toe to toe), S the span between the heels (L - 2T,
T the length of a lead's foot), W the lead width and C each one's tolerance. JT,
JH and JS are the solder fillets the standard asks for at the toe, heel and side,
per package family and density level (M most, N nominal, L least). F is the
board's fabrication tolerance (0.1 mm) and P the placement tolerance (0.05 mm).
Datasheets give T, not S, so S's tolerance is taken statistically, as the
standard allows: sqrt(CL² + 2 CT²), centred in the worst-case range. Z and G
are rounded to 0.05 mm (0.02 mm for chips under 1.6 mm), as the tables say.

Checked against the KiCad library, whose footprints come from the same formulas:
SOIC-8 (JEDEC MS-012) gives 1.95 x 0.6 mm pads 4.95 mm apart and TSSOP-20
(MO-153) 1.475 x 0.4 mm pads 5.725 mm apart, exactly as KiCad has them.

Through-hole pins follow IPC-2221/2222 instead: the hole is the largest lead
(its diagonal, for a square lead) plus 0.2 mm, the land the hole plus 0.6 mm.

Pure Python; `footprint(spec)` returns a `library_hkp.Cell` whose padstacks are
created in the `_Stock` it is given, so a part's cell imports like any other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from . import library_hkp as H

MANUFACTURING = 0.1  # F: board fabrication tolerance, mm
PLACEMENT = 0.05  # P: part placement tolerance, mm
DENSITIES = ("M", "N", "L")
SILK_WIDTH = 0.12
SILK_CLEARANCE = 0.2  # silkscreen to copper
SILK_OFFSET = 0.11  # silkscreen outside the body
EP_CLEARANCE = 0.2  # exposed pad to the pads around it
ROUND_RATIO = 0.25  # corner radius as a share of a pad's short side
ROUND_MAX = 0.25  # mm

# (toe, heel, side) fillet goals per density, the courtyard excess per density and
# the round-off, from the IPC-7351B tables
_FAMILIES: dict[str, dict[str, Any]] = {
    # Table 3-2: gull-wing leads, pitch above 0.625 mm
    "gullwing": {
        "fillets": {"M": (0.55, 0.45, 0.05), "N": (0.35, 0.35, 0.03), "L": (0.15, 0.25, 0.01)},
        "courtyard": {"M": 0.5, "N": 0.25, "L": 0.1},
        "round": 0.05,
    },
    # Table 3-3: gull-wing leads, pitch 0.625 mm and below
    "gullwing_fine": {
        "fillets": {"M": (0.55, 0.45, 0.01), "N": (0.35, 0.35, -0.02), "L": (0.15, 0.25, -0.04)},
        "courtyard": {"M": 0.5, "N": 0.25, "L": 0.1},
        "round": 0.05,
    },
    # Table 3-4: J leads
    "jlead": {
        "fillets": {"M": (0.1, 0.55, 0.05), "N": (0.0, 0.35, 0.03), "L": (-0.1, 0.15, 0.01)},
        "courtyard": {"M": 0.5, "N": 0.25, "L": 0.1},
        "round": 0.05,
    },
    # Table 3-15: flat no-lead (QFN, DFN, SON) with a toe fillet
    "nolead": {
        "fillets": {"M": (0.4, 0.0, -0.04), "N": (0.3, 0.0, -0.04), "L": (0.2, 0.0, -0.04)},
        "courtyard": {"M": 0.5, "N": 0.25, "L": 0.1},
        "round": 0.05,
    },
    # Table 3-18: flat no-lead whose terminals are pulled back from the body edge
    "nolead_pullback": {
        "fillets": {"M": (0.05, 0.05, 0.05), "N": (0.0, 0.0, 0.0), "L": (-0.05, -0.05, -0.05)},
        "courtyard": {"M": 0.5, "N": 0.25, "L": 0.1},
        "round": 0.05,
    },
    # Table 3-5: rectangular chips, 1.6 mm (0603) and longer
    "chip": {
        "fillets": {"M": (0.55, 0.0, 0.05), "N": (0.35, 0.0, 0.0), "L": (0.15, 0.0, -0.05)},
        "courtyard": {"M": 0.5, "N": 0.25, "L": 0.1},
        "round": 0.05,
    },
    # Table 3-6: rectangular chips shorter than 1.6 mm
    "chip_small": {
        "fillets": {"M": (0.3, 0.0, 0.05), "N": (0.2, 0.0, 0.0), "L": (0.1, 0.0, -0.05)},
        "courtyard": {"M": 0.2, "N": 0.15, "L": 0.1},
        "round": 0.02,
    },
    # inward L-bend leads (molded tantalum capacitors, SMA/SMB diodes, SOD-123F): the
    # heel is at the body's edge, so the outer land edge takes the heel goal
    "molded": {
        "fillets": {"M": (0.25, 0.8, 0.01), "N": (0.15, 0.5, -0.05), "L": (0.07, 0.2, -0.1)},
        "courtyard": {"M": 0.5, "N": 0.25, "L": 0.1},
        "round": 0.05,
    },
}
# through-hole: hole over the largest lead, land over the hole (IPC-2221/2222 levels A/B/C)
_HOLE_ALLOWANCE = {"M": 0.25, "N": 0.2, "L": 0.15}
_LAND_ALLOWANCE = {"M": 0.8, "N": 0.6, "L": 0.5}

FAMILIES = ("chip", "molded", "gullwing", "jlead", "nolead", "through")
CHIP_SIZES = {
    # EIA code: body length, body width, terminal length (min, max) and height, mm;
    # the resistor and capacitor dimensions most manufacturers share
    "0201": ((0.57, 0.63), (0.27, 0.33), (0.1, 0.2), 0.26),
    "0402": ((0.95, 1.05), (0.45, 0.55), (0.15, 0.3), 0.35),
    "0603": ((1.45, 1.75), (0.65, 0.95), (0.2, 0.45), 0.5),
    "0805": ((1.8, 2.2), (1.05, 1.45), (0.25, 0.55), 0.6),
    "1206": ((3.0, 3.4), (1.4, 1.8), (0.25, 0.65), 0.65),
    "1210": ((3.0, 3.4), (2.3, 2.7), (0.25, 0.65), 0.65),
    "2010": ((4.8, 5.2), (2.3, 2.7), (0.35, 0.75), 0.7),
    "2512": ((6.1, 6.5), (3.0, 3.4), (0.35, 0.75), 0.7),
}


class FootprintError(ValueError):
    """The footprint spec cannot be turned into a cell."""


@dataclass(frozen=True)
class Span:
    """A toleranced dimension, mm."""

    minimum: float
    maximum: float

    @property
    def nominal(self) -> float:
        return (self.minimum + self.maximum) / 2

    @property
    def tolerance(self) -> float:
        return self.maximum - self.minimum


def span(value: Any, what: str) -> Span:
    """A number (exact), [min, max], or {"nominal": n, "tolerance": t} (n ± t)."""
    if isinstance(value, bool):
        raise FootprintError(f"{what} must be a number, [min, max] or {{nominal, tolerance}}")
    if isinstance(value, (int, float)):
        return Span(float(value), float(value))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            low, high = float(value[0]), float(value[1])
        except (TypeError, ValueError) as exc:
            raise FootprintError(f"{what} must hold two numbers") from exc
        if high < low:
            raise FootprintError(f"{what}: the maximum {high} is below the minimum {low}")
        return Span(low, high)
    if isinstance(value, dict) and "nominal" in value:
        try:
            nominal = float(value["nominal"])
            tolerance = abs(float(value.get("tolerance", 0.0)))
        except (TypeError, ValueError) as exc:
            raise FootprintError(f"{what}: nominal and tolerance must be numbers") from exc
        return Span(nominal - tolerance, nominal + tolerance)
    raise FootprintError(f"{what} must be a number, [min, max] or {{nominal, tolerance}}")


def _round(value: float, base: float) -> float:
    return round(round(value / base) * base, 4)


def _rms(*tolerances: float) -> float:
    return math.sqrt(sum(t * t for t in tolerances))


def lands(
    family: str, density: str, lead_span: Span, foot: Span, width: Span
) -> tuple[float, float, float]:
    """(G, Z, X): the inner and outer extent of a pair of lands and their width."""
    table = _FAMILIES[family]
    toe, heel, side = table["fillets"][density]
    fab = MANUFACTURING**2 + PLACEMENT**2
    # S = L - 2T, its tolerance combined statistically and centred in its range
    s_min = lead_span.minimum - 2 * foot.maximum
    s_max = lead_span.maximum - 2 * foot.minimum
    s_tol = min(s_max - s_min, _rms(lead_span.tolerance, math.sqrt(2) * foot.tolerance))
    s_max_rms = s_max - ((s_max - s_min) - s_tol) / 2
    z = lead_span.minimum + 2 * toe + math.sqrt(lead_span.tolerance**2 + fab)
    g = s_max_rms - 2 * heel - math.sqrt(s_tol**2 + fab)
    x = width.minimum + 2 * side + math.sqrt(width.tolerance**2 + fab)
    base = table["round"]
    return _round(g, base), _round(z, base), _round(x, base)


# -- pads and cells --------------------------------------------------------------------


@dataclass
class Land:
    number: str
    x: float
    y: float
    width: float  # along x
    height: float  # along y
    shape: str = "RECTANGLE"  # RECTANGLE, ROUND, OBLONG
    drill: float = 0.0
    paste: float = 1.0  # share of the land's area the paste window covers

    def box(self, grow: float = 0.0) -> tuple[float, float, float, float]:
        return (
            self.x - self.width / 2 - grow,
            self.y - self.height / 2 - grow,
            self.x + self.width / 2 + grow,
            self.y + self.height / 2 + grow,
        )


def _padstack(stock: H._Stock, land: Land, corners: str) -> str:
    if land.drill:
        if land.shape == "ROUND":
            return stock.through(land.width, land.drill)
        return stock.through_stack(
            land.shape, land.width, land.height, 0.0, (land.drill, land.drill)
        )
    if land.shape == "ROUND":
        return stock.smd_round(land.width)
    if land.shape == "OBLONG":
        return stock.smd_stack("OBLONG", land.width, land.height)
    short = min(land.width, land.height)
    radius = round(min(short * ROUND_RATIO, ROUND_MAX), 3) if corners == "round" else 0.0
    if radius >= 0.001:
        name = stock.smd_stack("RADIUS_CORNER_RECTANGLE", land.width, land.height, radius)
    else:
        name = stock.smd_stack("RECTANGLE", land.width, land.height)
    if land.paste < 0.999:
        stack = stock.plan.padstacks[name]
        side = math.sqrt(max(land.paste, 0.05))
        paste = stock.pad("RECTANGLE", round(land.width * side, 3), round(land.height * side, 3))
        name = f"{name}-P{round(land.paste * 100)}"
        stock.plan.padstacks.setdefault(
            name, H.Padstack(name, "PIN_SMD", pad=stack.pad, mask=stack.mask, paste=paste)
        )
    return name


def _rect(x1: float, y1: float, x2: float, y2: float) -> tuple[tuple[float, float], ...]:
    return ((x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1))


def _grid_out(value: float, step: float = 0.01) -> float:
    """Away from zero to the next `step`, for an outline that must enclose."""
    scaled = abs(value) / step
    return math.copysign(math.ceil(scaled - 1e-9) * step, value)


def _subtract(
    start: float, end: float, blocks: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """[start, end] with the intervals in `blocks` taken out."""
    pieces = [(start, end)]
    for low, high in blocks:
        cut: list[tuple[float, float]] = []
        for a, b in pieces:
            if high <= a or low >= b:
                cut.append((a, b))
                continue
            if low > a:
                cut.append((a, low))
            if high < b:
                cut.append((high, b))
        pieces = cut
    return pieces


def _silk_outline(
    box: tuple[float, float, float, float], lands_: list[Land], minimum: float = 0.2
) -> list[tuple[tuple[float, float], ...]]:
    """The edges of `box` less where they would come within the clearance of a land."""
    x1, y1, x2, y2 = box
    grow = SILK_CLEARANCE + SILK_WIDTH / 2
    boxes = [land.box(grow) for land in lands_]
    segments: list[tuple[tuple[float, float], ...]] = []
    for y in (y1, y2):
        blocks = [(bx1, bx2) for bx1, by1, bx2, by2 in boxes if by1 < y < by2]
        for a, b in _subtract(x1, x2, blocks):
            if b - a >= minimum:
                segments.append(((round(a, 3), y), (round(b, 3), y)))
    for x in (x1, x2):
        blocks = [(by1, by2) for bx1, by1, bx2, by2 in boxes if bx1 < x < bx2]
        for a, b in _subtract(y1, y2, blocks):
            if b - a >= minimum:
                segments.append(((x, round(a, 3)), (x, round(b, 3))))
    return segments


def _pin_one_marker(land: Land, lands_: list[Land], body: tuple[float, ...]) -> list[Any]:
    """A small triangle pointing at pin 1 from the outer end of its land."""
    x1, y1, x2, y2 = land.box()
    size = 0.4
    gap = SILK_CLEARANCE + SILK_WIDTH
    cx = (body[0] + body[2]) / 2
    cy = (body[1] + body[3]) / 2
    # point in along the axis the land sticks out on: from its outer end
    if abs(land.x - cx) * (body[3] - body[1]) >= abs(land.y - cy) * (body[2] - body[0]):
        direction = -1 if land.x < cx else 1
        tip_x = (x1 - gap) if direction < 0 else (x2 + gap)
        base_x = tip_x + direction * size
        points = ((tip_x, land.y), (base_x, land.y + size / 2), (base_x, land.y - size / 2))
    else:
        direction = -1 if land.y < cy else 1
        tip_y = (y1 - gap) if direction < 0 else (y2 + gap)
        base_y = tip_y + direction * size
        points = ((land.x, tip_y), (land.x - size / 2, base_y), (land.x + size / 2, base_y))
    closed = tuple((round(x, 3), round(y, 3)) for x, y in (*points, points[0]))
    return [H.Graphic("SILKSCREEN_OUTLINE", "POLYLINE_PATH", closed, SILK_WIDTH)]


def _cell(
    name: str,
    lands_: list[Land],
    body: tuple[float, float],
    height: float,
    density: str,
    family_key: str,
    stock: H._Stock,
    *,
    group: str,
    description: str,
    corners: str,
    pin_one: bool,
    mount: str = "SURFACE",
) -> H.Cell:
    """Pins, assembly and placement outlines and a silkscreen clear of the lands."""
    bx, by = body[0] / 2, body[1] / 2
    body_box = (-bx, -by, bx, by)
    pins = [
        H.CellPin(land.number, _padstack(stock, land, corners), round(land.x, 4), round(land.y, 4))
        for land in lands_
    ]
    graphics: list[H.Graphic] = []
    # the assembly outline is the body, its pin 1 corner cut when the part has one
    # pin 1 is the land numbered 1; a package whose first land is another number keeps
    # its first land as the marked one
    first = next((land for land in lands_ if land.number == "1"), lands_[0] if lands_ else None)
    if pin_one and first is not None:
        corner = min(0.8, 0.25 * min(body))
        cut_left = first.x <= 0
        cut_top = first.y >= 0
        cx, cy = (-bx if cut_left else bx), (by if cut_top else -by)
        points = [(-bx, -by), (bx, -by), (bx, by), (-bx, by)]
        index = points.index((cx, cy))
        before = points[index - 1]
        after = points[(index + 1) % 4]

        def toward(p: tuple[float, float], q: tuple[float, float]) -> tuple[float, float]:
            length = math.hypot(q[0] - p[0], q[1] - p[1]) or 1.0
            return (p[0] + (q[0] - p[0]) * corner / length, p[1] + (q[1] - p[1]) * corner / length)

        ring = (
            points[:index]
            + [toward((cx, cy), before), toward((cx, cy), after)]
            + points[index + 1 :]
        )
        ring.append(ring[0])
        assembly = tuple((round(x, 3), round(y, 3)) for x, y in ring)
    else:
        assembly = _rect(*body_box)
    graphics.append(H.Graphic("ASSEMBLY_OUTLINE", "POLYLINE_PATH", assembly, 0.0))
    silk_box = (-bx - SILK_OFFSET, -by - SILK_OFFSET, bx + SILK_OFFSET, by + SILK_OFFSET)
    for segment in _silk_outline(silk_box, lands_):
        graphics.append(H.Graphic("SILKSCREEN_OUTLINE", "POLYLINE_PATH", segment, SILK_WIDTH))
    if pin_one and first is not None:
        graphics += _pin_one_marker(first, lands_, body_box)
    extent = [body_box] + [land.box() for land in lands_]
    extent += [
        (
            min(x for x, _ in g.points),
            min(y for _, y in g.points),
            max(x for x, _ in g.points),
            max(y for _, y in g.points),
        )
        for g in graphics
        if g.block == "SILKSCREEN_OUTLINE"
    ]
    excess = _FAMILIES[family_key]["courtyard"][density] if family_key in _FAMILIES else 0.25
    courtyard = (
        _grid_out(min(e[0] for e in extent) - excess),
        _grid_out(min(e[1] for e in extent) - excess),
        _grid_out(max(e[2] for e in extent) + excess),
        _grid_out(max(e[3] for e in extent) + excess),
    )
    graphics.append(H.Graphic("PLACEMENT_OUTLINE", "POLYLINE_PATH", _rect(*courtyard), 0.0))
    cell = H.Cell(
        name,
        group,
        mount,
        pins,
        body_box,
        round(height, 3),
        description,
        graphics=graphics,
    )
    return H.with_refdes(cell)


# -- names -----------------------------------------------------------------------------


def _hundredths(value: float) -> str:
    return str(int(round(value * 100)))


def _tenths(value: float) -> str:
    return f"{int(round(value * 10)):02d}"


# -- the families ----------------------------------------------------------------------


def _density(spec: dict[str, Any]) -> str:
    density = str(spec.get("density", "N")).upper()
    if density not in DENSITIES:
        raise FootprintError(f"density is M, N or L, not {density!r}")
    return density


def _height(spec: dict[str, Any]) -> float:
    try:
        height = float(spec["height"])
    except KeyError as exc:
        raise FootprintError("height (mm, the part's seated height) is required") from exc
    except (TypeError, ValueError) as exc:
        raise FootprintError("height must be a number") from exc
    if not 0 < height <= 50:
        raise FootprintError(f"height {height} mm is out of range")
    return height


def _count(spec: dict[str, Any], key: str, minimum: int = 1) -> int:
    value = spec.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise FootprintError(f"{key} must be an integer of at least {minimum}")
    return value


def _positive(spec: dict[str, Any], key: str) -> float:
    try:
        value = float(spec[key])
    except KeyError as exc:
        raise FootprintError(f"{key} is required") from exc
    except (TypeError, ValueError) as exc:
        raise FootprintError(f"{key} must be a number") from exc
    if value <= 0:
        raise FootprintError(f"{key} must be positive")
    return value


def _chip(spec: dict[str, Any], stock: H._Stock, kind: str, molded: bool) -> H.Cell:
    density = _density(spec)
    size = spec.get("size")
    if size is not None:
        if molded or str(size) not in CHIP_SIZES:
            raise FootprintError(f"size is one of {sorted(CHIP_SIZES)} (chip family only)")
        length, width, terminal, height = CHIP_SIZES[str(size)]
        body_length, body_width, foot = Span(*length), Span(*width), Span(*terminal)
        height = float(spec.get("height", height))
    else:
        body_length = span(spec.get("body_length"), "body_length")
        body_width = span(spec.get("body_width"), "body_width")
        foot = span(spec.get("terminal"), "terminal")
        height = _height(spec)
    lead = span(spec["lead_width"], "lead_width") if molded else body_width
    if foot.maximum * 2 >= body_length.minimum:
        raise FootprintError("terminal: two terminals are longer than the body")
    if molded:
        family = "molded"
    elif body_length.nominal < 1.6 - 1e-9:
        family = "chip_small"
    else:
        family = "chip"
    g, z, x = lands(family, density, body_length, foot, lead)
    x = round(x, 4)
    length = round((z - g) / 2, 4)
    centre = round((z + g) / 4, 4)
    lands_ = [Land("1", -centre, 0, length, x), Land("2", centre, 0, length, x)]
    polarised = bool(spec.get("polarized") or spec.get("polarised"))
    prefix = {"R": "RES", "C": "CAP", "L": "IND", "D": "DIO", "LED": "LED", "F": "FUS"}.get(
        kind, "CHIP" if not molded else "MOLD"
    )
    code = "M" if molded else "C"
    default = (
        f"{prefix}{code}{_tenths(body_length.nominal)}{_tenths(body_width.nominal)}"
        f"X{_hundredths(height)}{density}"
    )
    return _cell(
        str(spec.get("name") or default),
        lands_,
        (body_length.nominal, body_width.nominal),
        height,
        density,
        family,
        stock,
        group="DISCRETE_CHIP",
        description=str(spec.get("description") or f"{default} (IPC-7351B, density {density})"),
        corners=_corners(spec),
        pin_one=polarised,
    )


def _corners(spec: dict[str, Any]) -> str:
    corners = str(spec.get("corners", "round")).lower()
    if corners not in {"round", "square"}:
        raise FootprintError("corners is round or square")
    return corners


def _positions(spec: dict[str, Any], count: int) -> tuple[int, list[int]]:
    """How many lead positions the package has and which of them (1-based) it leaves out."""
    positions = spec.get("positions", count)
    if isinstance(positions, bool) or not isinstance(positions, int) or positions < count:
        raise FootprintError("positions must be an integer, at least pins")
    omit = spec.get("omit") or []
    if not isinstance(omit, list) or not all(
        isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= positions for p in omit
    ):
        raise FootprintError("omit lists positions (1-based integers) within positions")
    if positions - len(set(omit)) != count:
        raise FootprintError(
            f"pins {count} must equal positions {positions} less the {len(set(omit))} omitted"
        )
    return positions, sorted(set(omit))


def _rows(
    per_side: list[int],
    pitch: float,
    offsets: tuple[float, float],
    size: tuple[float, float],
    omit: list[int],
) -> list[Land]:
    """Lands numbered counter-clockwise from the top of the left row.

    `per_side` is the count on (left, bottom, right, top); `offsets` the distance of
    the left/right rows and of the bottom/top rows from the centre; `size` the land's
    (length, width). Omitted positions keep their place and lose their land; the
    lands that remain are numbered in order, as SOT-23 and SOT-23-5 are.
    """
    lands_: list[Land] = []
    position = 0
    length, width = size
    sides = ("left", "bottom", "right", "top")
    for side, count in zip(sides, per_side, strict=True):
        run = (count - 1) * pitch
        for index in range(count):
            position += 1
            if position in omit:
                continue
            along = -run / 2 + index * pitch
            if side == "left":
                land = Land("", -offsets[0], -along, length, width)
            elif side == "bottom":
                land = Land("", along, -offsets[1], width, length)
            elif side == "right":
                land = Land("", offsets[0], along, length, width)
            else:
                land = Land("", -along, offsets[1], width, length)
            lands_.append(land)
    for number, land in enumerate(lands_, start=1):
        land.number = str(number)
        land.x, land.y = round(land.x, 4) + 0.0, round(land.y, 4) + 0.0
        land.width, land.height = round(land.width, 4), round(land.height, 4)
    return lands_


def _exposed(
    spec: dict[str, Any], pins: int, g: tuple[float, float]
) -> tuple[list[Land], tuple[float, float]]:
    """The exposed pad, and the inner land edges pulled back to clear it by 0.2 mm."""
    pad = spec.get("exposed_pad")
    if not pad:
        return [], g
    if not isinstance(pad, dict) or "size" not in pad:
        raise FootprintError('exposed_pad is {"size": [w, h], "pin": "N", "paste": 0.7}')
    size = pad["size"]
    if not (isinstance(size, list) and len(size) == 2):
        raise FootprintError("exposed_pad.size is [width, height] in mm")
    width, height = float(size[0]), float(size[1])
    pin = str(pad.get("pin") or pins + 1)
    paste = float(pad.get("paste", 0.7))
    if not 0.2 <= paste <= 1.0:
        raise FootprintError("exposed_pad.paste is the share of the pad under paste, 0.2 to 1")
    gx = max(g[0], width + 2 * EP_CLEARANCE)
    gy = max(g[1], height + 2 * EP_CLEARANCE)
    return [Land(pin, 0.0, 0.0, width, height, paste=paste)], (gx, gy)


def _extra(spec: dict[str, Any]) -> list[Land]:
    return [_custom_land(item, index) for index, item in enumerate(spec.get("extra_pads") or [])]


def _leaded(spec: dict[str, Any], stock: H._Stock, family: str) -> H.Cell:
    """Gull-wing and J-lead packages: two rows (SOIC, SOP, SOT) or four (QFP, PLCC)."""
    density = _density(spec)
    pins = _count(spec, "pins")
    pitch = _positive(spec, "pitch")
    rows = spec.get("rows", 2)
    if rows not in (2, 4):
        raise FootprintError("rows is 2 (dual) or 4 (quad)")
    positions, omit = _positions(spec, pins)
    per_side = _per_side(spec, positions, rows)
    span_x = span(spec.get("span"), "span")
    span_y = span(spec.get("span_y", spec.get("span")), "span_y")
    foot = span(spec.get("terminal"), "terminal")
    lead = span(spec.get("lead_width"), "lead_width")
    body = _body(spec)
    height = _height(spec)
    if family == "gullwing" and pitch <= 0.625 + 1e-9:
        table = "gullwing_fine"
    else:
        table = family
    gx, zx, x = lands(table, density, span_x, foot, lead)
    gy, zy, _ = lands(table, density, span_y, foot, lead)
    ep, (gx, gy) = _exposed(spec, pins, (gx, gy))
    if x >= pitch:
        raise FootprintError(f"lands {x} mm wide do not fit a {pitch} mm pitch")
    lands_ = _rows(
        per_side,
        pitch,
        ((zx + gx) / 4, (zy + gy) / 4),
        ((zx - gx) / 2, x),
        omit,
    )
    lands_ = [*lands_, *ep, *_extra(spec)]
    if rows == 2:
        kind = (
            "SOIC" if abs(pitch - 1.27) < 1e-6 else ("SOT" if pins <= 8 and body[0] < 2 else "SOP")
        )
        group = "IC_SOIC" if kind != "SOT" else "DISCRETE_OTHER"
        default = f"{kind}{_hundredths(pitch)}P{_hundredths(span_x.nominal)}X{_hundredths(height)}"
    else:
        kind = "QFP" if family == "gullwing" else "PLCC"
        group = "GENERAL"
        default = (
            f"{kind}{_hundredths(pitch)}P{_hundredths(span_x.nominal)}"
            f"X{_hundredths(span_y.nominal)}X{_hundredths(height)}"
        )
    total = pins + len(ep)
    default += f"-{total}{density}"
    return _cell(
        str(spec.get("name") or default),
        lands_,
        body,
        height,
        density,
        table,
        stock,
        group=group,
        description=str(spec.get("description") or f"{default} (IPC-7351B, density {density})"),
        corners=_corners(spec),
        pin_one=True,
    )


def _per_side(spec: dict[str, Any], positions: int, rows: int) -> list[int]:
    if rows == 2:
        if positions % 2:
            raise FootprintError("a dual-row package has an even number of positions")
        half = positions // 2
        return [half, 0, half, 0]
    along_y = spec.get("pins_y")  # on the left and on the right side
    along_x = spec.get("pins_x")  # on the bottom and on the top side
    if along_y is None and along_x is None:
        if positions % 4:
            raise FootprintError("a quad package splits its positions over four sides")
        return [positions // 4] * 4
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (along_x, along_y)):
        raise FootprintError("pins_x and pins_y are integers, given together")
    if 2 * (along_x + along_y) != positions:
        raise FootprintError("2 x (pins_x + pins_y) must equal the positions")
    return [along_y, along_x, along_y, along_x]


def _body(spec: dict[str, Any]) -> tuple[float, float]:
    body = spec.get("body")
    if not (isinstance(body, list) and len(body) == 2):
        raise FootprintError("body is [width across the rows, length along them] in mm")
    return (span(body[0], "body width").nominal, span(body[1], "body length").nominal)


def _nolead(spec: dict[str, Any], stock: H._Stock) -> H.Cell:
    """QFN (four sides), DFN and SON (two): terminals at the body's edge."""
    density = _density(spec)
    pins = _count(spec, "pins")
    pitch = _positive(spec, "pitch")
    rows = spec.get("rows", 4)
    if rows not in (2, 4):
        raise FootprintError("rows is 2 (DFN, SON) or 4 (QFN)")
    positions, omit = _positions(spec, pins)
    per_side = _per_side(spec, positions, rows)
    body_value = spec.get("body")
    if not (isinstance(body_value, list) and len(body_value) == 2):
        raise FootprintError("body is [width, length] in mm, each a number or [min, max]")
    body_x, body_y = span(body_value[0], "body width"), span(body_value[1], "body length")
    foot = span(spec.get("terminal"), "terminal")
    lead = span(spec.get("lead_width"), "lead_width")
    height = _height(spec)
    pull = float(spec.get("pull_back", 0.0) or 0.0)
    table = "nolead_pullback" if pull > 0 else "nolead"
    outer_x = Span(body_x.minimum - 2 * pull, body_x.maximum - 2 * pull)
    outer_y = Span(body_y.minimum - 2 * pull, body_y.maximum - 2 * pull)
    gx, zx, x = lands(table, density, outer_x, foot, lead)
    gy, zy, _ = lands(table, density, outer_y, foot, lead)
    ep, (gx, gy) = _exposed(spec, pins, (gx, gy))
    if x >= pitch:
        raise FootprintError(f"lands {x} mm wide do not fit a {pitch} mm pitch")
    if zx <= gx or zy <= gy:
        raise FootprintError("the exposed pad leaves no room for the terminals' lands")
    lands_ = _rows(per_side, pitch, ((zx + gx) / 4, (zy + gy) / 4), ((zx - gx) / 2, x), omit)
    lands_ = [*lands_, *ep, *_extra(spec)]
    kind = "QFN" if rows == 4 else "SON"
    default = (
        f"{kind}{_hundredths(pitch)}P{_hundredths(body_x.nominal)}X{_hundredths(body_y.nominal)}"
        f"X{_hundredths(height)}-{pins + len(ep)}{density}"
    )
    return _cell(
        str(spec.get("name") or default),
        lands_,
        (body_x.nominal, body_y.nominal),
        height,
        density,
        table,
        stock,
        group="GENERAL",
        description=str(spec.get("description") or f"{default} (IPC-7351B, density {density})"),
        corners=_corners(spec),
        pin_one=True,
    )


def _through(spec: dict[str, Any], stock: H._Stock) -> H.Cell:
    """Headers and DIPs: a grid of plated holes, pin 1 on a square land."""
    density = _density(spec)
    pins = _count(spec, "pins")
    pitch = _positive(spec, "pitch")
    rows = spec.get("rows", 1)
    if rows not in (1, 2):
        raise FootprintError("rows is 1 or 2")
    if rows == 2 and pins % 2:
        raise FootprintError("two rows need an even pin count")
    row_pitch = _positive(spec, "row_pitch") if rows == 2 else 0.0
    lead_value = spec.get("lead")
    if isinstance(lead_value, dict) and "square" in lead_value:
        lead = span(lead_value["square"], "lead.square").maximum * math.sqrt(2)
    else:
        lead = span(lead_value, "lead").maximum
    drill = (
        float(spec.get("drill") or 0)
        or math.ceil((lead + _HOLE_ALLOWANCE[density]) / 0.05 - 1e-9) * 0.05
    )
    land = (
        float(spec.get("pad") or 0)
        or math.ceil((drill + _LAND_ALLOWANCE[density]) / 0.05 - 1e-9) * 0.05
    )
    drill, land = round(drill, 3), round(land, 3)
    if land >= pitch:
        raise FootprintError(f"lands {land} mm across do not fit a {pitch} mm pitch")
    numbering = str(spec.get("numbering", "around" if rows == 2 else "row"))
    if numbering not in {"row", "around", "zigzag"}:
        raise FootprintError("numbering is around (DIP) or zigzag (two-row header)")
    per_row = pins // rows
    run = (per_row - 1) * pitch
    lands_: list[Land] = []
    # columns top to bottom, pin 1 at the top left, as the SMD packages are drawn
    for index in range(pins):
        if rows == 1:
            x, y = 0.0, run / 2 - index * pitch
        elif numbering == "zigzag":
            row, column = divmod(index, 2)
            x, y = (-row_pitch / 2 if column == 0 else row_pitch / 2), run / 2 - row * pitch
        else:  # around: down the left column, back up the right one (DIP)
            if index < per_row:
                x, y = -row_pitch / 2, run / 2 - index * pitch
            else:
                x, y = row_pitch / 2, -run / 2 + (index - per_row) * pitch
        shape = "RECTANGLE" if index == 0 else "ROUND"
        lands_.append(
            Land(str(index + 1), round(x, 4) + 0.0, round(y, 4) + 0.0, land, land, shape, drill)
        )
    body = spec.get("body")
    if body is not None:
        body_size = _body(spec)
    else:
        body_size = ((row_pitch + pitch) if rows == 2 else pitch, run + pitch)
    height = _height(spec)
    default = (
        f"{'DIP' if rows == 2 and numbering == 'around' else 'HDR'}{_hundredths(pitch)}P"
        f"{rows}X{per_row}-D{_hundredths(drill)}{density}"
    )
    return _cell(
        str(spec.get("name") or default),
        lands_,
        body_size,
        height,
        density,
        "gullwing",
        stock,
        group="CONNECTOR" if numbering != "around" else "GENERAL",
        description=str(spec.get("description") or f"{default} (hole {drill} mm, land {land} mm)"),
        corners="square",
        pin_one=False,
        mount="THROUGH",
    )


SHAPES = {"rect": "RECTANGLE", "round": "ROUND", "oblong": "OBLONG"}


def _custom_land(item: Any, index: int) -> Land:
    where = f"pads[{index}]"
    if not isinstance(item, dict):
        raise FootprintError(f"{where} must be an object")
    try:
        number = str(item["pin"])
        x, y = float(item["x"]), float(item["y"])
        width = float(item["width"])
        height = float(item.get("height", width))
    except KeyError as exc:
        raise FootprintError(f"{where} needs pin, x, y and width") from exc
    except (TypeError, ValueError) as exc:
        raise FootprintError(f"{where}: x, y, width and height are numbers") from exc
    shape = SHAPES.get(str(item.get("shape", "rect")).lower())
    if shape is None:
        raise FootprintError(f"{where}: shape is one of {sorted(SHAPES)}")
    drill = float(item.get("drill", 0) or 0)
    if width <= 0 or height <= 0 or drill < 0 or (drill and drill >= min(width, height)):
        raise FootprintError(f"{where}: sizes must be positive, a drill inside its land")
    if not number.strip():
        raise FootprintError(f"{where}: pin is the pin number the land belongs to")
    return Land(number, x, y, width, height, shape, drill)


def _custom(spec: dict[str, Any], stock: H._Stock, name: str) -> H.Cell:
    """Lands as given; several lands may share a pin number (one pin, several pads)."""
    items = spec.get("pads")
    if not isinstance(items, list) or not items:
        raise FootprintError("pads is a non-empty list")
    lands_ = [_custom_land(item, index) for index, item in enumerate(items)]
    if "body" in spec:
        body = _body(spec)
    else:
        extent = [land.box() for land in lands_]
        body = (
            round(max(e[2] for e in extent) - min(e[0] for e in extent), 3),
            round(max(e[3] for e in extent) - min(e[1] for e in extent), 3),
        )
    height = _height(spec)
    through = any(land.drill for land in lands_)
    surface = any(not land.drill for land in lands_)
    mount = "MIXED" if through and surface else ("THROUGH" if through else "SURFACE")
    cell_name = str(spec.get("name") or name)
    return _cell(
        cell_name,
        lands_,
        body,
        height,
        _density(spec),
        "gullwing",
        stock,
        group=str(spec.get("group") or "GENERAL"),
        description=str(spec.get("description") or f"{cell_name} (lands as given)"),
        corners=_corners(spec),
        pin_one=bool(spec.get("pin_one", False)),
        mount=mount,
    )


def footprint(spec: dict[str, Any], stock: H._Stock, *, kind: str = "", name: str = "") -> H.Cell:
    """The cell for a footprint spec: an IPC family, or custom lands (`pads`).

    `kind` is the part's reference prefix (R, C, L, D ...), which names chip cells the
    IPC way (RESC1608X55N); `name` names a custom cell that gives none.
    """
    if not isinstance(spec, dict):
        raise FootprintError("a footprint is an object")
    if "pads" in spec:
        return _custom(spec, stock, name or "CUSTOM")
    family = str(spec.get("family", "")).lower()
    if family == "chip":
        return _chip(spec, stock, kind, molded=False)
    if family == "molded":
        return _chip(spec, stock, kind, molded=True)
    if family in ("gullwing", "jlead"):
        return _leaded(spec, stock, family)
    if family == "nolead":
        return _nolead(spec, stock)
    if family == "through":
        return _through(spec, stock)
    raise FootprintError(f"family is one of {', '.join(FAMILIES)}, or give pads")
