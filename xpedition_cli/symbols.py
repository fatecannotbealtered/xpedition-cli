"""Generate Xpedition Designer schematic symbols as ``V 53`` text files.

Designer stores symbols as plain ASCII under ``SymbolLibs/<partition>/sym/<name>.1``.
A ``V 53`` file uses sheet units directly: one unit is 10 mil, so the 100 mil grid
is 10 units and every pin's connection end lands on a multiple of ``GRID``.
Shapes follow the xpedition-schematic Skill's ``reference/schematic-conventions.md``:
IEC forms for two-terminal parts, rectangles with grouped pins for ICs and connectors.

Verified on Xpedition Standard XPED2604: files written this way load, place and
connect through ``AddPartInstance``.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path

GRID = 10
PIN_LEN_BOX = 20
PIN_LEN_PASSIVE = 20
TEXT_PIN_NUMBER = 5
TEXT_PIN_NAME = 6
TEXT_ATTRIBUTE = 8
CHAR_WIDTH = 6

PIN_TYPES = {"IN", "OUT", "BI", "TRI", "OCL", "OEM", "ANALOG", "POWER", "GROUND"}


@dataclass(frozen=True)
class Pin:
    """One symbol pin.

    ``x``/``y`` is the connection end (the first point of the ``P`` record),
    ``bx``/``by`` the end on the body. ``side`` is ``left``, ``right``, ``top`` or
    ``bottom`` and tells the wiring code which way a stub leaves the pin.
    """

    number: str
    name: str
    x: int
    y: int
    bx: int
    by: int
    side: str
    pintype: str = "BI"

    @property
    def direction(self) -> tuple[int, int]:
        return {"left": (-1, 0), "right": (1, 0), "top": (0, 1), "bottom": (0, -1)}[self.side]


@dataclass
class Symbol:
    """A symbol ready to render.

    ``netname`` marks a power symbol: Designer's symbol type record ``Y`` is then
    4 (a "pin" symbol that names a global net) instead of 1 (a module, i.e. a
    part), and the ``NETNAME`` attribute carries the net.
    """

    name: str
    device: str
    pins: list[Pin]
    shapes: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    netname: str | None = None
    show_pin_text: bool = False

    def pin(self, number: str) -> Pin:
        for pin in self.pins:
            if pin.number == str(number):
                return pin
        raise KeyError(f"{self.name} has no pin {number!r}")

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    def render(self) -> str:
        """Serialise to the ``V 53`` text format."""
        if self.netname is not None:
            return self._render_power()
        x1, y1, x2, y2 = self.bbox
        key = zlib.crc32(self.name.encode("utf-8")) % 10**9
        head = [
            "V 53",
            f"K {key} {self.name}.1",
            "F Case",
            f"D {x1} {y2 + 2 * TEXT_ATTRIBUTE} {x2} {y1 - 2 * TEXT_ATTRIBUTE}",
            "Y 1",
            "Z 10",
            "i 11",
            f"U {x1} {y2 + TEXT_ATTRIBUTE + 2} {TEXT_ATTRIBUTE} 0 8 3 REFDES=",
            f"U {x1} {y1 - TEXT_ATTRIBUTE - 4} {TEXT_ATTRIBUTE} 0 4 0 DEVICE={self.device}",
        ]
        body: list[str] = list(self.shapes)
        pin_id = 10
        show_numbers = self.show_pin_text
        for pin in self.pins:
            body.append(f"l 2 {pin.x} {pin.y} {pin.bx} {pin.by}")
            body.append(f"P {pin_id} {pin.x} {pin.y} {pin.bx} {pin.by} 0 3 0")
            # The `L` record is the pin's name as the packager sees it: without it the
            # pin is called `$1P10` and no parts-database mapping matches. It is not
            # drawn (visibility 0); the NAME attribute below is the visible text.
            body.append(f"L {pin.bx} {pin.by} {TEXT_PIN_NAME} 0 3 0 0 0 {pin.name or pin.number}")
            # Pin attributes take absolute symbol coordinates. `#` is the pin number
            # (shown on multi-pin parts only) and `NAME` the pin name inside the body;
            # an `L` record after the pin is not displayed on a part symbol.
            nx, ny = _pin_number_anchor(pin)
            body.append(
                f"A {nx} {ny} {TEXT_PIN_NUMBER} 0 3 {3 if show_numbers else 0} #={pin.number}"
            )
            # Every pin carries a NAME: the parts database maps symbol pin names to
            # cell pin numbers, so a passive's pins are named like their numbers. The
            # name is only drawn on boxes, where it differs from the number.
            lx, ly = _pin_name_anchor(pin)
            shown = 3 if show_numbers and pin.name and pin.name != pin.number else 0
            body.append(f"A {lx} {ly} {TEXT_PIN_NAME} 0 3 {shown} NAME={pin.name or pin.number}")
            body.append(f"A 0 10 10 0 3 0 PINTYPE={pin.pintype}")
            pin_id += 1
        return "\n".join(head + body + self.labels + ["E", ""])

    def _render_power(self) -> str:
        """Mirror the stock ``Globals:pwr_bar``: pin at the origin pointing up to a bar.

        ``FORWARD_PCB=0`` keeps the packager from treating the symbol as a part.
        """
        key = zlib.crc32(self.name.encode("utf-8")) % 10**9
        lines = [
            "V 53",
            f"K {key} {self.name}.1",
            "F Case",
            "Y 4",
            "D -20 0 20 40",
            "Z 10",
            "i 11",
            "U 0 0 10 0 1 0 FORWARD_PCB=0",
            f"U 0 26 {TEXT_ATTRIBUTE + 2} 0 4 3 NETNAME={self.netname}",
            f"U 0 0 10 0 3 0 NAME_PLACEHOLDER={self.name}",
            "l 2 -20 20 20 20",
            "P 10 0 0 0 20 0 1 0",
            "A 0 25 10 0 3 0 PINTYPE=POWER",
            "E",
            "",
        ]
        return "\n".join(lines)


def _pin_number_anchor(pin: Pin) -> tuple[int, int]:
    """Just outside the body, beside the pin leg."""
    if pin.side == "left":
        return pin.x + 4, pin.y + 2
    if pin.side == "right":
        return pin.bx + 3, pin.y + 2
    if pin.side == "top":
        return pin.x + 2, pin.by + 6
    return pin.x + 2, pin.y + 2


def _pin_name_anchor(pin: Pin) -> tuple[int, int]:
    """Just inside the body, at the pin's row or column."""
    if pin.side == "left":
        return pin.bx + 3, pin.y - 3
    if pin.side == "right":
        return pin.bx - 3 - CHAR_WIDTH * len(pin.name), pin.y - 3
    if pin.side == "top":
        return pin.x - 2, pin.by - 10
    return pin.x - 2, pin.by + 4


def _poly(points: list[tuple[int, int]]) -> str:
    return f"l {len(points)} " + " ".join(f"{x} {y}" for x, y in points)


def _text(x: int, y: int, size: int, text: str, anchor: int = 3) -> str:
    """Free text on a symbol: a value-only attribute, since `L` records do not render."""
    return f"U {x} {y} {size} 0 {anchor} 3 MARK={text}"


def _two_terminal(
    name: str,
    device: str,
    shapes: list[str],
    labels: list[str],
    half: int,
    *,
    first: str = "1",
    second: str = "2",
    first_name: str = "1",
    second_name: str = "2",
    half_h: int = 8,
    pintype: str = "BI",
) -> Symbol:
    """A horizontal two-pin part: connection ends 40 units apart, on grid.

    Resistors and capacitors keep the default `BI` pins: Designer's verification
    exempts them. Other passives are typed `ANALOG` so the BI-to-power and
    BI-to-ground checks do not fire on a battery or a diode.
    """
    end = 2 * GRID
    pins = [
        Pin(first, first_name, -end, 0, -half, 0, "left", pintype),
        Pin(second, second_name, end, 0, half, 0, "right", pintype),
    ]
    return Symbol(name, device, pins, shapes, labels, (-end, -half_h, end, half_h))


def resistor(name: str = "R", device: str | None = None) -> Symbol:
    """IEC resistor: a 20 × 8 rectangle."""
    shapes = [_poly([(-10, -4), (10, -4), (10, 4), (-10, 4), (-10, -4)])]
    return _two_terminal(name, device or name, shapes, [], 10)


def capacitor(name: str = "C", device: str | None = None, polarised: bool = False) -> Symbol:
    """Two plates 12 long and 4 apart; the polarised form marks the positive plate."""
    shapes = [_poly([(-2, -6), (-2, 6)]), _poly([(2, -6), (2, 6)])]
    labels = []
    if polarised:
        labels.append(_text(-9, 9, TEXT_PIN_NAME, "+"))
    return _two_terminal(
        name,
        device or name,
        shapes,
        labels,
        2,
        first_name="+" if polarised else "1",
        second_name="-" if polarised else "2",
        half_h=10,
    )


def inductor(name: str = "L", device: str | None = None) -> Symbol:
    """Four semicircular humps approximated by short polylines."""
    shapes = []
    for hump in range(4):
        cx = -15 + hump * 10
        points = []
        for step in range(7):
            angle = math.pi - math.pi * step / 6
            points.append((round(cx + 5 * math.cos(angle)), round(5 * math.sin(angle))))
        shapes.append(_poly(points))
    return _two_terminal(name, device or name, shapes, [], 20, half_h=8, pintype="ANALOG")


def diode(name: str = "D", device: str | None = None, led: bool = False) -> Symbol:
    """Triangle and bar, anode on the left; an LED adds two arrows."""
    shapes = [
        _poly([(-6, -6), (-6, 6), (6, 0), (-6, -6)]),
        _poly([(6, -6), (6, 6)]),
    ]
    if led:
        shapes.append(_poly([(0, 6), (6, 12), (3, 12), (6, 12), (6, 9)]))
        shapes.append(_poly([(4, 4), (10, 10), (7, 10), (10, 10), (10, 7)]))
    return _two_terminal(
        name,
        device or name,
        shapes,
        [],
        6,
        first="2",
        second="1",
        first_name="A",
        second_name="K",
        half_h=14 if led else 8,
        pintype="ANALOG",
    )


def switch(name: str = "SW", device: str | None = None) -> Symbol:
    """A contact blade lifted off the second terminal."""
    shapes = [
        _poly([(-10, 0), (8, 8)]),
        _poly([(-12, 0), (-10, 2), (-8, 0), (-10, -2), (-12, 0)]),
        _poly([(8, 0), (10, 2), (12, 0), (10, -2), (8, 0)]),
    ]
    return _two_terminal(name, device or name, shapes, [], 12, half_h=10, pintype="ANALOG")


def battery(name: str = "BT", device: str | None = None) -> Symbol:
    """Long plate for the positive terminal, short plate for the negative one."""
    shapes = [_poly([(-2, -8), (-2, 8)]), _poly([(2, -4), (2, 4)])]
    labels = [_text(-11, 11, TEXT_PIN_NAME, "+")]
    return _two_terminal(
        name,
        device or name,
        shapes,
        labels,
        2,
        first_name="+",
        second_name="-",
        half_h=12,
        pintype="ANALOG",
    )


def thermistor(name: str = "NTC", device: str | None = None) -> Symbol:
    """IEC thermistor: the resistor body crossed by a diagonal with a foot, marked -t."""
    shapes = [
        _poly([(-10, -4), (10, -4), (10, 4), (-10, 4), (-10, -4)]),
        _poly([(-16, -9), (-11, -9), (11, 9)]),
    ]
    labels = [_text(2, -12, TEXT_PIN_NAME, "-t")]
    return _two_terminal(name, device or name, shapes, labels, 10, half_h=12, pintype="ANALOG")


def mosfet(name: str = "NMOS", device: str | None = None, channel: str = "N") -> Symbol:
    """Enhancement MOSFET with the gate on the left, drawn upright for a switch.

    Pin numbers follow SOT-23: 1 gate, 2 source, 3 drain. An N-channel part has
    its drain on top and its source at the bottom; a P-channel part is the other
    way up, so a high-side switch reads source-to-supply, drain-to-load. The body
    arrow points into the channel for N and out of it for P.
    """
    p_channel = str(channel).upper() == "P"
    gate = Pin("1", "G", -20, 0, -10, 0, "left", "IN")
    if p_channel:
        top = Pin("2", "S", 0, 20, 0, 10, "top", "ANALOG")
        bottom = Pin("3", "D", 0, -20, 0, -10, "bottom", "ANALOG")
    else:
        top = Pin("3", "D", 0, 20, 0, 10, "top", "ANALOG")
        bottom = Pin("2", "S", 0, -20, 0, -10, "bottom", "ANALOG")
    shapes = [
        _poly([(-10, -10), (-10, 10)]),
        _poly([(-6, -12), (-6, -5)]),
        _poly([(-6, -3), (-6, 3)]),
        _poly([(-6, 5), (-6, 12)]),
        _poly([(-6, 8), (0, 8), (0, 10)]),
        _poly([(-6, -8), (0, -8), (0, -10)]),
        _poly([(-6, 0), (0, 0), (0, 8 if p_channel else -8)]),
    ]
    if p_channel:
        shapes.append(_poly([(0, 0), (-4, 2), (-4, -2), (0, 0)]))
    else:
        shapes.append(_poly([(-6, 0), (-2, 2), (-2, -2), (-6, 0)]))
    labels = [
        _text(-19, 3, TEXT_PIN_NAME, "G"),
        _text(3, 12, TEXT_PIN_NAME, top.name),
        _text(3, -18, TEXT_PIN_NAME, bottom.name),
    ]
    return Symbol(name, device or name, [gate, top, bottom], shapes, labels, (-20, -20, 20, 20))


def _ring(cx: int, cy: int, radius: int) -> str:
    points = []
    for step in range(13):
        angle = 2 * math.pi * step / 12
        points.append((round(cx + radius * math.cos(angle)), round(cy + radius * math.sin(angle))))
    return _poly(points)


def test_point(name: str = "TP", device: str | None = None) -> Symbol:
    """A test point: a ring on a short leg, its single pin pointing down."""
    pins = [Pin("1", "1", 0, -20, 0, 0, "bottom", "ANALOG")]
    return Symbol(name, device or name, pins, [_ring(0, 8, 8)], [], (-10, -20, 10, 16))


def mounting_hole(name: str = "HOLE", device: str | None = None) -> Symbol:
    """A mounting hole: a ring with a cross and no pins; placed like any part."""
    shapes = [_ring(0, 0, 10), _poly([(-14, 0), (14, 0)]), _poly([(0, -14), (0, 14)])]
    # FORWARD_PCB=0: the packager skips it; holes are placed in Layout, not packaged.
    labels = ["U 0 0 10 0 1 0 FORWARD_PCB=0"]
    return Symbol(name, device or name, [], shapes, labels, (-14, -14, 14, 14))


def box(
    name: str,
    left: list[tuple[str, str]] | None = None,
    right: list[tuple[str, str]] | None = None,
    top: list[tuple[str, str]] | None = None,
    bottom: list[tuple[str, str]] | None = None,
    device: str | None = None,
    pintypes: dict[str, str] | None = None,
    min_half_width: int = 30,
) -> Symbol:
    """A rectangle with pins on any side, grouped top to bottom / left to right.

    Each side is a list of ``(number, name)``; an empty name with number ``""``
    leaves a gap row, which is how pin groups are separated.
    """
    left, right, top, bottom = left or [], right or [], top or [], bottom or []
    pintypes = pintypes or {}
    longest = max([len(n) for _, n in left] + [0]) + max([len(n) for _, n in right] + [0])
    half_w = max(min_half_width, (longest * CHAR_WIDTH + 20) // 2)
    half_w = ((half_w + GRID - 1) // GRID) * GRID
    rows = max(len(left), len(right), 1)
    cols = max(len(top), len(bottom), 0)
    half_w = max(half_w, ((cols + 1) * GRID) // 2 + GRID)
    half_w = ((half_w + GRID - 1) // GRID) * GRID
    half_h = ((rows + 1) * GRID) // 2
    half_h = ((half_h + GRID - 1) // GRID) * GRID
    pins: list[Pin] = []
    labels: list[str] = []

    def side_pins(entries, side):
        for index, (number, pname) in enumerate(entries):
            if not number:
                continue
            ptype = pintypes.get(number, "BI")
            if side in ("left", "right"):
                y = half_h - GRID * (index + 1)
                if side == "left":
                    pins.append(
                        Pin(number, pname, -half_w - PIN_LEN_BOX, y, -half_w, y, side, ptype)
                    )
                else:
                    pins.append(Pin(number, pname, half_w + PIN_LEN_BOX, y, half_w, y, side, ptype))
            else:
                x = -half_w + GRID * (index + 1)
                if side == "top":
                    pins.append(Pin(number, pname, x, half_h + PIN_LEN_BOX, x, half_h, side, ptype))
                else:
                    pins.append(
                        Pin(number, pname, x, -half_h - PIN_LEN_BOX, x, -half_h, side, ptype)
                    )

    side_pins(left, "left")
    side_pins(right, "right")
    side_pins(top, "top")
    side_pins(bottom, "bottom")
    corners = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
    shapes = [_poly(corners + corners[:1])]
    x_ext = half_w + (PIN_LEN_BOX if (left or right) else 0)
    y_ext = half_h + (PIN_LEN_BOX if (top or bottom) else 0)
    return Symbol(
        name,
        device or name,
        pins,
        shapes,
        labels,
        (-x_ext, -y_ext, x_ext, y_ext),
        show_pin_text=True,
    )


def power_symbol_name(net: str, prefix: str = "PWR_") -> str:
    """``+5V`` -> ``PWR_P5V``: a file-safe symbol name for a power net."""
    safe = net.replace("+", "P").replace("-", "N")
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in safe)
    return f"{prefix}{safe}"


def power_symbol(net: str, prefix: str = "PWR_") -> Symbol:
    """A power bar whose ``NETNAME`` is fixed to ``net``.

    The stock ``Globals:pwr_bar`` cannot be renamed per instance through
    automation, so every power net gets its own symbol.
    """
    name = power_symbol_name(net, prefix)
    pins = [Pin("1", net, 0, 0, 0, 20, "bottom", "POWER")]
    shapes = [_poly([(-20, 20), (20, 20)])]
    return Symbol(name, name, pins, shapes, [], (-20, 0, 20, 30), netname=net)


def write_symbol(library_root: Path, partition: str, symbol: Symbol) -> Path:
    """Write ``<library_root>/<partition>/sym/<name>.1`` and return the path."""
    target = Path(library_root) / partition / "sym"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{symbol.name}.1"
    path.write_text(symbol.render(), encoding="utf-8")
    return path


def on_grid(value: int) -> bool:
    return value % GRID == 0
