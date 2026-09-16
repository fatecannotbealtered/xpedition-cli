"""Central-library content as HKP text: padstacks, cells and parts.

Xpedition's library databases are binary, but the installation ships converters
in `common/win64/bin` that read plain ASCII: `HKP2PadstackDB`, `HKP2CellDB` and
`HKP2PartsDB` (their `-a` exports are the grammar reference, see
`docs/COMPATIBILITY.md`). So a minimal library for a design can be generated
as text, exactly like the schematic symbols, and imported without a GUI.

This module is pure Python. `plan_library(design)` turns a schematic design
(the `schematic draw` format) into the three HKP texts plus a summary, using
placeholder packages: chips for two-terminal parts, SOT-23 for transistors,
dual-row packages for ICs, 2.54 mm headers for connectors, a pad for test
points, an M2 hole for mounting holes. A design may override the package of a
symbol or a reference designator through `"packages": {"U302": "TSSOP20"}`.

Units are millimetres throughout (`.UNITS MM`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import schematic_layout as L

PARTITION = L.PARTITION
LAYERS = 4
SILK_WIDTH = 0.15
MASK_EXPANSION = 0.1
KICAD_PREFIX = "kicad:"  # package key of a KiCad footprint: kicad:Library:Footprint


@dataclass(frozen=True)
class Pad:
    name: str
    shape: str  # ROUND, RECTANGLE, OBLONG, RADIUS_CORNER_RECTANGLE
    width: float
    height: float = 0.0
    radius: float = 0.0


@dataclass(frozen=True)
class Hole:
    name: str
    diameter: float
    plated: bool = True
    slot: tuple[float, float] | None = None  # (width, height) of a slotted hole


@dataclass(frozen=True)
class Padstack:
    name: str
    kind: str  # PIN_SMD, PIN_THROUGH, MOUNTING_HOLE
    pad: str | None = None
    mask: str | None = None
    hole: str | None = None
    clearance: str | None = None


@dataclass(frozen=True)
class CellPin:
    number: str
    padstack: str
    x: float
    y: float
    rotation: float = 0


@dataclass(frozen=True)
class Graphic:
    """One drawn element of a cell: a path block inside an outline block."""

    block: str  # SILKSCREEN_OUTLINE, ASSEMBLY_OUTLINE, PLACEMENT_OUTLINE
    path: str  # POLYLINE_PATH, POLYLINE_SHAPE, RECT_PATH, CIRCLE_PATH
    points: tuple[tuple[float, float], ...]
    width: float = 0.0
    radius: float = 0.0  # CIRCLE_PATH


@dataclass(frozen=True)
class RefDesText:
    x: float
    y: float
    height: float
    stroke: float
    rotation: float = 0


@dataclass
class Cell:
    name: str
    group: str  # DISCRETE_CHIP, IC_SOIC, CONNECTOR, TEST_POINT, GENERAL ...
    mount: str  # SURFACE, THROUGH, MIXED
    pins: list[CellPin]
    body: tuple[float, float, float, float]
    height: float
    description: str = ""
    mechanical: bool = False
    graphics: list[Graphic] = field(default_factory=list)  # drawn instead of the box outlines
    refdes: RefDesText | None = None  # on the silkscreen
    refdes_assembly: RefDesText | None = None  # the same text on the assembly layer
    holes: list[CellPin] = field(default_factory=list)  # unnumbered holes of a package cell
    external: bool = False  # lives in another partition already; referenced, not rendered
    partition: str = ""


@dataclass
class Part:
    number: str
    prefix: str
    cell: str
    symbol: str
    pin_names: list[str]
    pin_numbers: list[str]
    part_type: str
    description: str = ""
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class LibraryPlan:
    pads: dict[str, Pad] = field(default_factory=dict)
    holes: dict[str, Hole] = field(default_factory=dict)
    padstacks: dict[str, Padstack] = field(default_factory=dict)
    cells: dict[str, Cell] = field(default_factory=dict)
    parts: dict[str, Part] = field(default_factory=dict)
    mapping: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    partition: str = PARTITION
    cell_partitions: set[str] = field(default_factory=set)  # partitions of referenced cells

    def summary(self) -> dict[str, Any]:
        return {
            "padstacks": sorted(self.padstacks),
            "cells": sorted(name for name, cell in self.cells.items() if not cell.external),
            "referenced_cells": sorted(name for name, cell in self.cells.items() if cell.external),
            "cell_partitions": sorted(self.cell_partitions),
            "parts": sorted(self.parts),
            "mapping": self.mapping,
            "issues": self.issues,
        }


# -- geometry helpers ----------------------------------------------------------------


def _num(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _rect_points(x1: float, y1: float, x2: float, y2: float) -> str:
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    return " ".join(f"({_num(x)}, {_num(y)})" for x, y in corners)


def _pad_name(shape: str, width: float, height: float = 0.0, radius: float = 0.0) -> str:
    if shape == "ROUND":
        return f"RND{_num(width)}"
    if shape == "OBLONG":
        return f"OBL{_num(width)}X{_num(height)}"
    if shape == "RADIUS_CORNER_RECTANGLE":
        return f"RRECT{_num(width)}X{_num(height)}R{_num(radius)}"
    return f"RECT{_num(width)}X{_num(height)}"


def _hole_name(drill: tuple[float, float], plated: bool) -> str:
    plating = "PLATED" if plated else "NONPLATED"
    if abs(drill[0] - drill[1]) < 1e-6:
        return f"C{_num(drill[0])}-{plating}"
    return f"S{_num(drill[0])}X{_num(drill[1])}-{plating}"


class _Stock:
    """Pads, holes and padstacks named by their geometry, created on demand."""

    def __init__(self, plan: LibraryPlan) -> None:
        self.plan = plan

    def pad(self, shape: str, width: float, height: float = 0.0, radius: float = 0.0) -> str:
        name = _pad_name(shape, width, height, radius)
        self.plan.pads.setdefault(name, Pad(name, shape, width, height, radius))
        return name

    def _mask(self, shape: str, width: float, height: float, radius: float) -> str:
        """The same shape grown by the mask expansion."""
        grow = MASK_EXPANSION
        return self.pad(shape, width + grow, height + grow, radius + grow / 2 if radius else 0.0)

    def _hole(self, drill: tuple[float, float], plated: bool) -> str:
        name = _hole_name(drill, plated)
        slot = None if abs(drill[0] - drill[1]) < 1e-6 else (drill[0], drill[1])
        self.plan.holes.setdefault(name, Hole(name, drill[0], plated=plated, slot=slot))
        return name

    def smd_stack(self, shape: str, width: float, height: float = 0.0, radius: float = 0.0) -> str:
        """A surface-mount pin padstack with a mask opening 0.1 mm larger."""
        pad = self.pad(shape, width, height, radius)
        mask = self._mask(shape, width, height, radius)
        name = f"SMD-{pad}"
        self.plan.padstacks.setdefault(name, Padstack(name, "PIN_SMD", pad=pad, mask=mask))
        return name

    def through_stack(
        self,
        shape: str,
        width: float,
        height: float,
        radius: float,
        drill: tuple[float, float],
        plated: bool = True,
    ) -> str:
        """A through pin padstack: the pad on every layer around a drilled hole."""
        pad = self.pad(shape, width, height, radius)
        mask = self._mask(shape, width, height, radius)
        hole = self._hole(drill, plated)
        name = f"TH-{pad}-{hole}"
        self.plan.padstacks.setdefault(
            name, Padstack(name, "PIN_THROUGH", pad=pad, mask=mask, hole=hole)
        )
        return name

    def hole(self, drill: tuple[float, float], plated: bool = False) -> str:
        """A mounting-hole padstack: a hole with a clearance and a mask opening, no pad."""
        hole = self._hole(drill, plated)
        largest = max(drill)
        clearance = self.pad("ROUND", largest + 0.5)
        mask = self.pad("ROUND", largest + 0.2)
        name = f"MH-{hole}"
        self.plan.padstacks.setdefault(
            name, Padstack(name, "MOUNTING_HOLE", mask=mask, hole=hole, clearance=clearance)
        )
        return name

    def smd(self, width: float, height: float) -> str:
        return self.smd_stack("RECTANGLE", width, height)

    def smd_round(self, diameter: float) -> str:
        return self.smd_stack("ROUND", diameter, diameter)

    def through(self, pad_diameter: float, drill: float) -> str:
        return self.through_stack("ROUND", pad_diameter, pad_diameter, 0.0, (drill, drill))

    def mounting_hole(self, drill: float) -> str:
        return self.hole((drill, drill), plated=False)


# -- package generators (placeholder footprints, millimetres) --------------------------


def _chip(
    stock: _Stock,
    name: str,
    pitch: float,
    pad: tuple[float, float],
    body: tuple[float, float],
    height: float,
) -> Cell:
    padstack = stock.smd(*pad)
    half = pitch / 2
    pins = [CellPin("1", padstack, -half, 0), CellPin("2", padstack, half, 0)]
    bx, by = body[0] / 2, body[1] / 2
    return Cell(name, "DISCRETE_CHIP", "SURFACE", pins, (-bx, -by, bx, by), height, f"chip {name}")


def _sot23(stock: _Stock, name: str) -> Cell:
    padstack = stock.smd(0.6, 1.0)
    pins = [
        CellPin("1", padstack, -0.95, -1.0),
        CellPin("2", padstack, 0.95, -1.0),
        CellPin("3", padstack, 0.0, 1.0),
    ]
    return Cell(name, "DISCRETE_OTHER", "SURFACE", pins, (-1.45, -0.65, 1.45, 0.65), 1.1, "SOT-23")


def _dual_row(
    stock: _Stock,
    name: str,
    count: int,
    pitch: float,
    row_gap: float,
    pad: tuple[float, float],
    body: tuple[float, float],
    height: float,
    group: str,
) -> Cell:
    """Pins 1..n/2 down the left side, n/2+1..n up the right side (SOIC numbering).

    `pad` is (across the row, along the pitch): the long side points at the body, the
    short side runs along the column, or neighbouring pads overlap — Batch DRC found
    exactly that (required 0.254 mm, actual 0) before the two were swapped here.
    """
    padstack = stock.smd(pad[0], pad[1])
    per_side = (count + 1) // 2
    span = (per_side - 1) * pitch
    pins: list[CellPin] = []
    for index in range(per_side):
        y = span / 2 - index * pitch
        pins.append(CellPin(str(index + 1), padstack, -row_gap / 2, y, 0))
    for index in range(count - per_side):
        y = -span / 2 + index * pitch
        pins.append(CellPin(str(per_side + index + 1), padstack, row_gap / 2, y, 0))
    bx, by = body[0] / 2, max(body[1], span + pitch) / 2
    return Cell(name, group, "SURFACE", pins, (-bx, -by, bx, by), height, f"{name} ({count} pins)")


def _header(stock: _Stock, name: str, count: int) -> Cell:
    padstack = stock.through(1.6, 1.0)
    pitch = 2.54
    span = (count - 1) * pitch
    pins = [CellPin(str(i + 1), padstack, -span / 2 + i * pitch, 0) for i in range(count)]
    return Cell(
        name,
        "CONNECTOR",
        "THROUGH",
        pins,
        (-span / 2 - pitch / 2, -pitch / 2, span / 2 + pitch / 2, pitch / 2),
        8.5,
        f"{count}-pin 2.54 mm header",
    )


def _test_point(stock: _Stock, name: str) -> Cell:
    padstack = stock.smd_round(1.0)
    return Cell(
        name,
        "TEST_POINT",
        "SURFACE",
        [CellPin("1", padstack, 0, 0)],
        (-0.6, -0.6, 0.6, 0.6),
        0,
        "test point",
    )


def _mounting_hole(stock: _Stock, name: str, drill: float) -> Cell:
    padstack = stock.mounting_hole(drill)
    r = drill / 2 + 0.3
    return Cell(
        name,
        "GENERAL",
        "THROUGH",
        [CellPin("1", padstack, 0, 0)],
        (-r, -r, r, r),
        0,
        f"M{_num(drill - 0.2)} mounting hole",
        mechanical=True,
    )


def build_package(stock: _Stock, key: str, pin_count: int) -> Cell:
    """A placeholder cell for a package key; `HDR`, `SOIC`, `TSSOP` take the pin count."""
    key = key.upper()
    if key == "0402":
        return _chip(stock, "CLI_0402", 1.0, (0.6, 0.55), (1.0, 0.5), 0.5)
    if key == "0603":
        return _chip(stock, "CLI_0603", 1.6, (0.9, 0.95), (1.6, 0.8), 0.8)
    if key == "0805":
        return _chip(stock, "CLI_0805", 1.9, (1.0, 1.3), (2.0, 1.25), 1.0)
    if key == "SOT23":
        return _sot23(stock, "CLI_SOT23")
    if key == "TP":
        return _test_point(stock, "CLI_TP")
    if key in ("HOLE", "HOLE_M2"):
        return _mounting_hole(stock, "CLI_HOLE_M2", 2.2)
    if key.startswith("HDR"):
        count = int(key[3:] or pin_count)
        return _header(stock, f"CLI_HDR{count}", count)
    # Layout's Database Load refuses a cell whose pin count differs from the part's, so
    # an odd count stays odd: the last position on the right row is simply left empty.
    if key.startswith("SOIC"):
        count = int(key[4:] or pin_count)
        return _dual_row(
            stock, f"CLI_SOIC{count}", count, 1.27, 5.4, (1.55, 0.6), (3.9, 4.9), 1.75, "IC_SOIC"
        )
    if key.startswith("TSSOP"):
        count = int(key[5:] or pin_count)
        # 0.8 mm pitch with 0.45 mm pads leaves 0.35 mm between pads, above the stock
        # 0.254 mm clearance; a true 0.65 mm pitch placeholder would be unroutable
        return _dual_row(
            stock, f"CLI_TSSOP{count}", count, 0.8, 5.7, (1.4, 0.45), (4.4, 6.5), 1.2, "IC_SOIC"
        )
    raise ValueError(f"unknown package {key!r}")


TWO_PIN_PACKAGE = {
    "RES": "0402",
    "CAP": "0402",
    "CAPP": "0805",
    "NTC": "0402",
    "IND": "0603",
    "DIODE": "0603",
    "LED": "0603",
    "SW": "HDR2",
    "BAT": "HDR2",
}
PART_TYPES = {
    "R": "Resistor",
    "C": "Capacitor",
    "L": "Inductor",
    "D": "Diode",
    "Q": "MOSFET",
    "U": "IC",
    "J": "Connector",
    "SW": "Switch",
    "BT": "Misc",
    "RT": "Resistor",
    "TP": "Misc",
    "H": "Misc",
}


def default_package(kind: str, refdes: str, pin_count: int) -> str:
    if kind in TWO_PIN_PACKAGE:
        return TWO_PIN_PACKAGE[kind]
    if kind in ("NMOS", "PMOS"):
        return "SOT23"
    if kind == "TP":
        return "TP"
    if kind == "HOLE":
        return "HOLE"
    prefix = _prefix(refdes)
    if prefix == "J":
        return f"HDR{pin_count}"
    if pin_count == 3:
        return "SOT23"
    # 1.27 mm pitch up to 16 pins: the stock templates route 0.254 mm traces with
    # 0.254 mm clearances, which a 0.65 mm pitch placeholder cannot be reached under
    if pin_count <= 16:
        return f"SOIC{pin_count}"
    return f"TSSOP{pin_count}"


def _prefix(refdes: str) -> str:
    match = re.match(r"^([A-Za-z]+)", refdes)
    return match.group(1).upper() if match else ""


def _clean_number(value: str) -> str:
    return " ".join(str(value).split()) or "PART"


def _kicad_cell(spec: str, root: Any, cache: dict[str, Cell], library: LibraryPlan) -> Cell:
    """The cell standing for the KiCad footprint `spec` (`Library:Name`), read from the
    footprint folder `root` (or the one `kicad_footprints.default_root` finds). The cell is
    not rendered: it is in the partition `kicad_import` converted its library into."""
    from pathlib import Path

    from . import kicad_footprints

    if spec in cache:
        return cache[spec]
    folder = Path(str(root)) if root else kicad_footprints.default_root()
    if folder is None or not folder.is_dir():
        raise ValueError(
            'no KiCad footprint folder: set "kicad_footprints" in the design or the '
            "XPEDITION_KICAD_FOOTPRINTS environment variable"
        )
    cell, _partition, issues = kicad_footprints.reference_cell(folder, spec)
    library.issues += [f"{KICAD_PREFIX}{spec}: {issue}" for issue in issues]
    cache[spec] = cell
    return cell


# -- the plan ----------------------------------------------------------------------------


def plan_library(design: dict[str, Any]) -> LibraryPlan:
    """Padstacks, cells and parts for every part a design places.

    Part numbers are the values the schematic carries as `Part Number`, so the
    packager finds them without touching the drawing; a central library with
    real part numbers replaces this placeholder set later.
    """
    plan = L.plan(design)
    library = LibraryPlan(partition=plan.partition)
    stock = _Stock(library)
    overrides = {str(k): str(v) for k, v in dict(design.get("packages", {})).items()}
    kicad_root = design.get("kicad_footprints")
    references: dict[str, Cell] = {}
    for part in plan.parts:
        refdes = str(part["refdes"])
        kind = str(part["symbol"])
        symbol_name = str(part["symbol_name"])
        pins = plan.symbol_pins.get(symbol_name, [])
        key = (
            overrides.get(refdes) or overrides.get(kind) or default_package(kind, refdes, len(pins))
        )
        try:
            if key.lower().startswith(KICAD_PREFIX):
                cell = _kicad_cell(key[len(KICAD_PREFIX) :], kicad_root, references, library)
            else:
                cell = build_package(stock, key, len(pins))
        except (ValueError, FileNotFoundError, OSError) as exc:
            library.issues.append(f"{refdes}: {exc}")
            continue
        library.cells.setdefault(cell.name, cell)
        if cell.external:
            library.cell_partitions.add(cell.partition)
        numbers = [number for number, _ in pins]
        cell_numbers = {pin.number for pin in cell.pins}
        missing = [n for n in numbers if n not in cell_numbers]
        if missing:
            library.issues.append(
                f"{refdes}: symbol pins {missing} have no pin in cell {cell.name}"
            )
        number = _clean_number(str(part.get("value") or ""))
        if not pins:
            # a mechanical mark (mounting hole): its cell is generated, but the symbol is
            # not forwarded to the PCB, so the parts database does not need it
            library.mapping.append(
                {
                    "refdes": refdes,
                    "symbol": kind,
                    "part_number": None,
                    "cell": cell.name,
                    "package": key,
                    "partition": cell.partition or library.partition,
                }
            )
            continue
        prefix = _prefix(refdes)
        existing = library.parts.get(number)
        if existing is not None and (existing.symbol != symbol_name or existing.cell != cell.name):
            number = f"{number} [{kind}]"
        if number not in library.parts:
            library.parts[number] = Part(
                number=number,
                prefix=prefix,
                cell=cell.name,
                symbol=symbol_name,
                pin_names=[name or num for num, name in pins],
                pin_numbers=numbers,
                part_type=PART_TYPES.get(prefix, "Misc"),
                description=f"{kind} {number} (placeholder part)",
                properties={"Value": number},
            )
        library.mapping.append(
            {
                "refdes": refdes,
                "symbol": kind,
                "part_number": number,
                "cell": cell.name,
                "package": key,
                "partition": cell.partition or library.partition,
            }
        )
    return library


# -- rendering --------------------------------------------------------------------------


def render_padstacks(plan: LibraryPlan) -> str:
    lines = [
        ".FILETYPE PADSTACK_LIBRARY",
        '.VERSION "02.10"',
        ".SCHEMA_VERSION 20",
        '.CREATOR "xpedition-cli"',
        "",
        ".UNITS MM",
        "",
    ]
    for pad in sorted(plan.pads.values(), key=lambda p: p.name):
        lines.append(f'.PAD "{pad.name}"')
        if pad.shape == "ROUND":
            lines += ["..ROUND", f"...DIAMETER {_num(pad.width)}"]
        else:
            lines += [
                f"..{pad.shape}",
                f"...WIDTH {_num(pad.width)}",
                f"...HEIGHT {_num(pad.height)}",
            ]
            if pad.shape == "RADIUS_CORNER_RECTANGLE":
                lines.append(f"...RADIUS {_num(pad.radius)}")
        lines += ["..PAD_OPTIONS USER_GENERATED_NAME", "..OFFSET (0, 0)", ""]
    for hole in sorted(plan.holes.values(), key=lambda h: h.name):
        plating = "PLATED" if hole.plated else "NON_PLATED"
        lines.append(f'.HOLE "{hole.name}"')
        if hole.slot is None:
            lines += ["..ROUND", f"...DIAMETER {_num(hole.diameter)}"]
        else:
            lines += ["..SLOT", f"...WIDTH {_num(hole.slot[0])}", f"...HEIGHT {_num(hole.slot[1])}"]
        lines += [
            f"..HOLE_OPTIONS {plating} DRILLED USER_GENERATED_NAME",
            "..DEPTH_ASSIGNMENT_METHOD THROUGH_HOLE",
            "",
        ]
    for stack in sorted(plan.padstacks.values(), key=lambda s: s.name):
        lines += [
            f'.PADSTACK "{stack.name}"',
            f"..PADSTACK_TYPE {stack.kind}",
            '..TECHNOLOGY "(Default)"',
            "...TECHNOLOGY_OPTIONS NONE",
        ]
        if stack.kind == "PIN_SMD":
            lines += [
                f'...TOP_PAD "{stack.pad}"',
                f'...BOTTOM_PAD "{stack.pad}"',
                f'...TOP_SOLDERMASK_PAD "{stack.mask}"',
                f'...BOTTOM_SOLDERMASK_PAD "{stack.mask}"',
                f'...TOP_SOLDERPASTE_PAD "{stack.pad}"',
                f'...BOTTOM_SOLDERPASTE_PAD "{stack.pad}"',
            ]
        elif stack.kind == "PIN_THROUGH":
            lines += [
                f'...TOP_PAD "{stack.pad}"',
                f'...INTERNAL_PAD "{stack.pad}"',
                f'...BOTTOM_PAD "{stack.pad}"',
                f'...TOP_SOLDERMASK_PAD "{stack.mask}"',
                f'...BOTTOM_SOLDERMASK_PAD "{stack.mask}"',
                f'...HOLE_NAME "{stack.hole}"',
                "....OFFSET (0, 0)",
            ]
        else:
            lines += [
                f'...CLEARANCE_PAD "{stack.clearance}"',
                f'...TOP_SOLDERMASK_PAD "{stack.mask}"',
                f'...BOTTOM_SOLDERMASK_PAD "{stack.mask}"',
                f'...HOLE_NAME "{stack.hole}"',
                "....OFFSET (0, 0)",
            ]
        lines.append("")
    return "\n".join(lines) + "\n"


def _outline(
    keyword: str, body: tuple[float, float, float, float], width: float, extra: list[str]
) -> list[str]:
    x1, y1, x2, y2 = body
    return [
        f"..{keyword}",
        "...SIDE MNT_SIDE",
        *extra,
        "...POLYLINE_PATH",
        f"....WIDTH {_num(width)}",
        f"....XY {_rect_points(x1, y1, x2, y2)}",
    ]


def render_cells(plan: LibraryPlan) -> str:
    lines = [
        ".FILETYPE CELL_LIBRARY",
        '.VERSION "02.52"',
        '.LIBRARY_DB_TYPE "XPED"',
        '.CREATOR "xpedition-cli"',
        "",
        ".UNITS MM",
        "",
    ]
    for cell in sorted(plan.cells.values(), key=lambda c: c.name):
        if cell.external:
            continue
        description = cell.description.replace('"', "'")
        placement = _outline(
            "PLACEMENT_OUTLINE",
            cell.body,
            0,
            [f"...HEIGHT {_num(cell.height)}", "...UNDERSIDE_SPACE 0"],
        )
        if cell.graphics:
            placement = [
                line
                for graphic in cell.graphics
                if graphic.block == "PLACEMENT_OUTLINE"
                for line in _graphic_lines(graphic, cell.height)
            ]
        if cell.mechanical:
            # A mounting hole is a mechanical cell: the hole is an object, not a pin,
            # and the importer allows no package group or mount type on it.
            lines += [
                f'.MECHANICAL_CELL "{cell.name}"',
                f"..NUMBER_LAYERS {LAYERS}",
                f'..DESCRIPTION "{description}"',
            ]
            for pin in cell.pins + cell.holes:
                lines += _hole_lines(pin)
            lines += placement
            lines += [
                line
                for graphic in cell.graphics
                if graphic.block != "PLACEMENT_OUTLINE"
                for line in _graphic_lines(graphic, cell.height)
            ]
            lines.append("")
            continue
        lines += [
            f'.PACKAGE_CELL "{cell.name}"',
            f"..NUMBER_LAYERS {LAYERS}",
            f"..PACKAGE_GROUP {cell.group}",
            f"..MOUNT_TYPE {cell.mount}",
            f'..DESCRIPTION "{description}"',
        ]
        for pin in cell.pins:
            lines += [
                f'..PIN "{pin.number}"',
                f'...PADSTACK "{pin.padstack}"',
                f"...XY ({_num(pin.x)}, {_num(pin.y)})",
                f"...ROTATION {_num(pin.rotation)}",
                "...PIN_OPTIONS NONE",
            ]
        for hole in cell.holes:
            lines += _hole_lines(hole)
        lines += placement
        if cell.graphics:
            lines += [
                line
                for graphic in cell.graphics
                if graphic.block != "PLACEMENT_OUTLINE"
                for line in _graphic_lines(graphic, cell.height)
            ]
        else:
            x1, y1, x2, y2 = cell.body
            grow = SILK_WIDTH
            lines += _outline("ASSEMBLY_OUTLINE", cell.body, 0, [])
            lines += _outline(
                "SILKSCREEN_OUTLINE", (x1 - grow, y1 - grow, x2 + grow, y2 + grow), SILK_WIDTH, []
            )
        if cell.refdes is not None:
            lines += _refdes_lines(cell.refdes, cell.refdes_assembly)
        lines.append("")
    return "\n".join(lines) + "\n"


def _hole_lines(hole: CellPin) -> list[str]:
    return [
        "..MOUNTING_HOLE",
        f'...PADSTACK "{hole.padstack}"',
        f"...XY ({_num(hole.x)}, {_num(hole.y)})",
        f"...ROTATION {_num(hole.rotation)}",
    ]


def _points(points: tuple[tuple[float, float], ...]) -> str:
    """Points one per line after the first, as the library's own exports write them."""
    return "\n      ".join(f"({_num(x)}, {_num(y)})" for x, y in points)


def _graphic_lines(graphic: Graphic, height: float) -> list[str]:
    """One outline block holding one path, in the grammar the `-a` exports show."""
    lines = [f"..{graphic.block}"]
    if graphic.block == "PLACEMENT_OUTLINE":
        lines += [f"...HEIGHT {_num(height)}", "...UNDERSIDE_SPACE 0"]
    lines.append("...SIDE MNT_SIDE")
    if graphic.path == "CIRCLE_PATH":
        lines += [
            "...CIRCLE_PATH",
            f"....WIDTH {_num(graphic.width)}",
            f"....XY {_points(graphic.points)}",
            f"....RADIUS {_num(graphic.radius)}",
        ]
    elif graphic.path == "POLYLINE_SHAPE":
        lines += [
            "...POLYLINE_SHAPE",
            f"....XY {_points(graphic.points)}",
            "....SHAPE_OPTIONS FILLED",
        ]
    else:
        lines += [
            f"...{graphic.path}",
            f"....WIDTH {_num(graphic.width)}",
            f"....XY {_points(graphic.points)}",
        ]
    return lines


def _refdes_lines(text: RefDesText, assembly: RefDesText | None = None) -> list[str]:
    """The reference designator placed on the mount-side silkscreen (and, when given, on
    the assembly layer) at the cell's own size; without it Layout draws the reference
    designator at its default size, on both layers."""
    lines = ['..TEXT "Ref Des"', "...TEXT_TYPE REF_DES"]
    for layer, item in (("SILKSCREEN_MNT_LYR", text), ("ASSEMBLY_MNT_LYR", assembly)):
        if item is None:
            continue
        lines += [
            "...DISPLAY_ATTR",
            f"....XY ({_num(item.x)}, {_num(item.y)})",
            f"....TEXT_LYR {layer}",
            "....DISPLAY_CONDITION ANY_MOUNT",
            "....HORZ_JUST CENTER",
            "....VERT_JUST CENTER",
            f"....HEIGHT {_num(item.height)}",
            f"....WIDTH {_num(item.height * 3)}",
            f"....STROKE_WIDTH {_num(item.stroke)}",
            f"....ROTATION {_num(item.rotation)}",
            '....FONT "vf_std"',
            "....TEXT_OPTIONS NONE",
        ]
    return lines


def render_parts(plan: LibraryPlan) -> str:
    lines = [
        ".Filetype ASCII_PDB",
        '.Version "02.02"',
        '.LIBRARY_DB_TYPE "XPED"',
        ".Units th",
        ".Notation Si",
        "",
    ]
    for index, part in enumerate(sorted(plan.parts.values(), key=lambda p: p.number), start=1):
        # swap-group names are resolved across the whole file, so each part gets its own
        gate = f"G{index}"
        lines += [
            f'.Number "{part.number}"',
            f'\t..Name "{part.number}"',
            "\t\t...Default",
            f'\t..Label "{part.number}"',
            "\t\t...Default",
            f'\t..Desc "{part.description}"',
            f'\t..RefPrefix "{part.prefix}"',
            f'\t..TopCell "{part.cell}"',
            f'\t..Prop "Type",\t"{part.part_type}",\t"Text"',
        ]
        for name, value in part.properties.items():
            lines.append(f'\t..Prop "{name}",\t"{value}",\t"Text"')
        if not part.pin_numbers:
            # a mechanical part (mounting hole): the symbol is referenced, nothing is mapped
            lines += [f'\t..Symbol\t"{plan.partition}:{part.symbol}"', "\t\t...Default", ""]
            continue
        lines.append(f'\t..SwapGroup\t"{gate}"')
        for index in range(len(part.pin_numbers)):
            lines.append(f'\t\t...SwapID\t"P{index}"')
        lines += [
            f'\t..Symbol\t"{plan.partition}:{part.symbol}"',
            "\t\t...Default",
            f'\t\t...Symbol_SwapGroup\t"{gate}"',
        ]
        for name in part.pin_names:
            lines.append(f'\t\t\t....PinName\t"{name}"')
        lines += [
            "\t..Slots",
            f'\t\t...Slot_SwapGroup\t"{gate}"',
            "\t\t\t....SlotID\t1",
            "\t\t\t....SwapCode\t0",
        ]
        for number in part.pin_numbers:
            lines.append(f'\t\t\t....PinNumber\t"{number}"')
        lines.append("")
    return "\n".join(lines) + "\n"


def library_texts(
    design: dict[str, Any], partition: str | None = None
) -> tuple[LibraryPlan, dict[str, str]]:
    if partition:
        design = {**design, "partition": partition}
    plan = plan_library(design)
    texts = {
        "padstacks": render_padstacks(plan),
        "cells": render_cells(plan),
        "parts": render_parts(plan),
    }
    return plan, texts
