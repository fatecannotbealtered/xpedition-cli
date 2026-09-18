"""Plan a readable schematic from a compact design description.

A design names its sheets; each sheet holds blocks: IC or connector symbols
with a treatment per pin (label, power symbol, ground, no-connect), and
*ladders* (vertical) or *chains* (horizontal) of two-terminal parts strung
between power, ground and labelled nodes. That is the vocabulary of a hardware
review schematic — pull-ups, dividers, decoupling, LED chains, input chains —
and it is what the Skill's drawing conventions ask for.

The planner turns a design into drawing operations for the native adapter's
``draw`` method, the netlist those operations must produce, the symbol files
they rely on, and the extent checks of the conventions (DS-07, DS-08). It is
pure Python: nothing here touches Xpedition.

Units are sheet units (10 mil); everything lands on the 10-unit grid.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Any

from . import symbols as S

GRID = 10
STUB = 20
NODE_PITCH = 60
MARGIN = 40
MIN_GAP = 30
LABEL_HEIGHT = 8
CHAR_WIDTH = 6
# The stock central library registers a `PartQuest` partition for symbols, cells and
# parts; a partition the library does not register cannot be packaged, so it is the
# default.
PARTITION = "PartQuest"
SHEET_SIZES = {
    "A": (1100, 850),
    "B": (1700, 1100),
    "C": (2200, 1700),
    "D": (3400, 2200),
    "A4": (1169, 827),
    "A3": (1654, 1169),
}
# Designer's border symbol and VDSHEET_* size code for each sheet size.
SHEET_BORDERS = {
    "A": ("asheet", 0),
    "B": ("bsheet", 1),
    "C": ("csheet", 2),
    "D": ("dsheet", 3),
    "A4": ("a4sheet", 5),
    "A3": ("a3sheet", 6),
}
NC_ORIENTATION = {"left": 0, "right": 2, "top": 3, "bottom": 1}
TWO_TERMINAL = {
    "RES": lambda: S.resistor("RES"),
    "CAP": lambda: S.capacitor("CAP"),
    "CAPP": lambda: S.capacitor("CAPP", polarised=True),
    "IND": lambda: S.inductor("IND"),
    "DIODE": lambda: S.diode("DIODE"),
    "LED": lambda: S.diode("LED", led=True),
    "SW": lambda: S.switch("SW"),
    "BAT": lambda: S.battery("BAT"),
    "NTC": lambda: S.thermistor("NTC"),
}
# Three-terminal parts go in `ic` blocks: every pin gets a treatment, like an IC.
THREE_TERMINAL = {
    "NMOS": lambda: S.mosfet("NMOS", channel="N"),
    "PMOS": lambda: S.mosfet("PMOS", channel="P"),
}
# Marks with one pin (test points) or none (mounting holes) also go in `ic` blocks.
MARKS = {
    "TP": lambda: S.test_point("TP"),
    "HOLE": lambda: S.mounting_hole("HOLE"),
}
BUILTIN = {**TWO_TERMINAL, **THREE_TERMINAL, **MARKS}


class DesignError(ValueError):
    """The design description cannot be drawn as written."""


@dataclass
class Plan:
    ops: list[dict[str, Any]] = field(default_factory=list)
    nets: dict[str, set[str]] = field(default_factory=dict)
    links: list[tuple[str, str]] = field(default_factory=list)
    symbols: dict[str, str] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    parts: list[dict[str, Any]] = field(default_factory=list)
    sheets: list[dict[str, Any]] = field(default_factory=list)
    partition: str = PARTITION
    # generated symbol name -> [(pin number, pin name)], for the parts database
    symbol_pins: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "sheets": self.sheets,
            "parts": len(self.parts),
            "operations": len(self.ops),
            "nets": {name: sorted(pins) for name, pins in sorted(self.nets.items())},
            "links": [list(link) for link in self.links],
            "symbols": sorted(self.symbols),
            "issues": self.issues,
        }


def _rotate(x: int, y: int, orientation: int) -> tuple[int, int]:
    """Designer's instance orientation: 0/1/2/3 = 0°/90°/180°/270° counter-clockwise."""
    return {0: (x, y), 1: (-y, x), 2: (-x, -y), 3: (y, -x)}[orientation]


def _hashed_name(base: str, text: str) -> str:
    """Designer keeps the definition of a placed symbol, so a changed geometry
    must arrive under a new name; hashing the rendered text does that."""
    return f"{base}_{zlib.crc32(text.encode('utf-8')) & 0xFFFFFFFF:08x}"


class _Library:
    """Symbols the plan needs, named by content so cached definitions never bite."""

    def __init__(self, specs: dict[str, Any]) -> None:
        self._specs = specs
        self._built: dict[str, S.Symbol] = {}
        self.files: dict[str, str] = {}
        self.pins: dict[str, list[tuple[str, str]]] = {}

    def symbol(self, name: str) -> S.Symbol:
        if name in self._built:
            return self._built[name]
        spec = self._specs.get(name)
        if spec is None and name in BUILTIN:
            symbol = BUILTIN[name]()
        elif spec is None:
            raise DesignError(
                f"symbol {name!r} is neither defined nor a built-in kind "
                f"({', '.join(sorted(BUILTIN))})"
            )
        else:
            symbol = _symbol_from_spec(name, spec)
        symbol.name = _hashed_name(name, symbol.render())
        self._built[name] = symbol
        self.files[symbol.name] = symbol.render()
        self.pins[symbol.name] = [(pin.number, pin.name or pin.number) for pin in symbol.pins]
        return symbol

    def power(self, net: str) -> S.Symbol:
        key = "power:" + net
        if key in self._built:
            return self._built[key]
        symbol = S.power_symbol(net)
        symbol.name = _hashed_name(S.power_symbol_name(net), symbol.render())
        self._built[key] = symbol
        self.files[symbol.name] = symbol.render()
        return symbol


def _reject_duplicate_pin_names(name: str, sides: dict[str, list[tuple[str, str]]]) -> None:
    """Refuse a symbol whose displayed pin names repeat.

    Designer names a net after the pin a wire meets. Two pins of one symbol
    sharing a displayed name put their wires on the same auto-named net, so the
    second explicit label lands on an already-labelled net and Designer stops the
    draw with 6035 "Net already labeled" -- minutes in, partway through. The pin
    name is what matters, not the net: a pin named after its own net is fine as
    long as it is the only pin with that name.

    Renaming here is not an option: the `L` record is what the parts database
    maps to cell pin numbers, so a generated suffix would break that mapping. The
    working shape is distinct pin names with the shared net on the wire's label,
    which is also what the netlist already uses.
    """
    seen: dict[str, list[str]] = {}
    for side, pairs in sides.items():
        for number, text in pairs:
            if not number or not text:
                continue  # a gap row, which separates pin groups
            seen.setdefault(text, []).append(f"{side} pin {number}")
    duplicates = {text: pins for text, pins in sorted(seen.items()) if len(pins) > 1}
    if duplicates:
        detail = "; ".join(f"{text!r} on {', '.join(pins)}" for text, pins in duplicates.items())
        raise DesignError(
            f"symbol {name!r} repeats displayed pin names ({detail}). Designer names a net "
            "after the pin a wire meets, so repeated names collide with the explicit labels "
            "and the draw fails with 6035. Give each pin a distinct name and keep the shared "
            "net on the wire's label."
        )


def _symbol_from_spec(name: str, spec: dict[str, Any]) -> S.Symbol:
    kind = str(spec.get("kind", "box"))
    if kind == "box":
        pairs = lambda side: [(str(n), str(t)) for n, t in spec.get(side, [])]  # noqa: E731
        sides = {side: pairs(side) for side in ("left", "right", "top", "bottom")}
        _reject_duplicate_pin_names(name, sides)
        return S.box(
            name,
            left=sides["left"],
            right=sides["right"],
            top=sides["top"],
            bottom=sides["bottom"],
            pintypes={str(k): str(v) for k, v in spec.get("pintypes", {}).items()},
        )
    if kind in TWO_TERMINAL:
        return TWO_TERMINAL[kind]()
    raise DesignError(f"symbol {name!r}: unknown kind {kind!r}")


@dataclass
class _Placed:
    refdes: str
    symbol: S.Symbol
    x: int
    y: int
    orientation: int

    def pin_end(self, number: str) -> tuple[int, int]:
        pin = self.symbol.pin(number)
        dx, dy = _rotate(pin.x, pin.y, self.orientation)
        return self.x + dx, self.y + dy

    def pin_side(self, number: str) -> str:
        pin = self.symbol.pin(number)
        dx, dy = _rotate(pin.x - pin.bx, pin.y - pin.by, self.orientation)
        if abs(dx) >= abs(dy):
            return "left" if dx < 0 else "right"
        return "bottom" if dy < 0 else "top"

    def bbox(self) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = self.symbol.bbox
        corners = [
            _rotate(x, y, self.orientation) for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
        ]
        xs = [self.x + cx for cx, _ in corners]
        ys = [self.y + cy for _, cy in corners]
        return min(xs), min(ys), max(xs), max(ys)


class _SheetPlanner:
    def __init__(self, plan: Plan, library: _Library, width: int, height: int, boxed: bool) -> None:
        self.plan = plan
        self.partition = plan.partition
        self.library = library
        self.width = width
        self.height = height
        self.boxed = boxed
        self.placed: dict[str, _Placed] = {}
        self.symbol_boxes: list[tuple[str, tuple[int, int, int, int]]] = []
        # parts wired to each other inside one ladder or chain may sit closer than MIN_GAP
        self.neighbours: set[frozenset[str]] = set()

    # -- primitives --------------------------------------------------------------
    def op(self, **fields: Any) -> None:
        self.plan.ops.append(fields)

    def part(
        self, refdes: str, symbol_name: str, value: str, x: int, y: int, orientation: int
    ) -> _Placed:
        if refdes in self.placed:
            raise DesignError(f"reference designator {refdes!r} is used twice")
        if x % GRID or y % GRID:
            raise DesignError(f"{refdes}: position ({x}, {y}) is off the {GRID}-unit grid")
        symbol = self.library.symbol(symbol_name)
        placed = _Placed(refdes, symbol, x, y, orientation)
        self.placed[refdes] = placed
        attributes: list[dict[str, Any]] = []
        if len(symbol.pins) == 2:
            # keep the value and refdes of a two-terminal part horizontal, beside it
            x1, y1, x2, y2 = placed.bbox()
            if orientation in (1, 3):
                attributes = [
                    {"name": "Ref Designator", "x": x2 + 4, "y": y + 2, "orientation": 0},
                    {"name": "Part Number", "x": x2 + 4, "y": y - 10, "orientation": 0},
                ]
            else:
                attributes = [
                    {"name": "Ref Designator", "x": x1 + 10, "y": y2 + 4, "orientation": 0},
                    {"name": "Part Number", "x": x1 + 10, "y": y1 - 12, "orientation": 0},
                ]
        self.op(
            op="place_part",
            refdes=refdes,
            library=self.partition,
            symbol=symbol.name,
            part=value,
            x=x,
            y=y,
            orientation=orientation,
            attributes=attributes,
        )
        self.plan.parts.append(
            {
                "refdes": refdes,
                "symbol": symbol_name,
                "symbol_name": symbol.name,
                "value": value,
                "x": x,
                "y": y,
                "orientation": orientation,
            }
        )
        return placed

    def wire(
        self,
        points: list[tuple[int, int]],
        start: str | None = None,
        end: str | None = None,
        label: dict[str, Any] | None = None,
    ) -> None:
        for px, py in points:
            if px % GRID or py % GRID:
                raise DesignError(f"wire point ({px}, {py}) is off the grid")
        for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
            if ax != bx and ay != by:
                raise DesignError(f"wire from ({ax}, {ay}) to ({bx}, {by}) is not orthogonal")
        self.op(
            op="wire", points=[[px, py] for px, py in points], start=start, end=end, label=label
        )

    def power(self, net: str, x: int, y: int) -> None:
        symbol = self.library.power(net)
        self.op(
            op="place_symbol", library=self.partition, symbol=symbol.name, x=x, y=y, orientation=0
        )
        self.symbol_boxes.append((f"power {net}", (x - 20, y, x + 20, y + 40)))

    def ground(self, x: int, y: int) -> None:
        self.op(op="place_symbol", library="Globals", symbol="gnd", x=x, y=y, orientation=0)
        self.symbol_boxes.append(("gnd", (x - 20, y - 40, x + 20, y)))

    def no_connect(self, x: int, y: int, side: str) -> None:
        self.op(
            op="place_symbol",
            library="builtin",
            symbol="No_Connect",
            x=x,
            y=y,
            orientation=NC_ORIENTATION[side],
        )

    def label(self, net: str, x: int, y: int, side: str) -> dict[str, Any]:
        """Label anchor for a stub ending at (x, y) that leaves the part towards `side`."""
        width = CHAR_WIDTH * len(net)
        if side == "left":
            lx, ly = x - width - 4, y + 2
        elif side == "right":
            lx, ly = x + 4, y + 2
        elif side == "top":
            lx, ly = x + 4, y + 2
        else:
            lx, ly = x + 4, y - LABEL_HEIGHT - 4
        if self.boxed:
            self.op(op="box", x1=lx - 2, y1=ly - 2, x2=lx + width + 2, y2=ly + LABEL_HEIGHT + 2)
        return {"net": net, "x": lx, "y": ly}

    def text(self, text: str, x: int, y: int, size: int) -> None:
        self.op(op="text", text=text, x=x, y=y, size=size)

    def connect(self, net: str, pin: str) -> None:
        self.plan.nets.setdefault(net, set()).add(pin)

    # -- node handling shared by ladders, chains and IC pins ------------------------
    @staticmethod
    def parse_node(node: Any) -> tuple[str, str]:
        if not isinstance(node, str):
            raise DesignError(
                f"node {node!r} must be a string such as 'power:+3V3', 'gnd', "
                "'label:NAME' or 'none'"
            )
        if node == "gnd":
            return "gnd", "GND"
        if node == "none":
            return "none", ""
        if node == "nc":
            return "nc", ""
        kind, sep, name = node.partition(":")
        if kind in ("power", "label") and sep and name:
            return kind, name
        raise DesignError(f"node {node!r} is not power:NET, gnd, label:NAME, none or nc")

    # -- blocks --------------------------------------------------------------------
    def ic(self, block: dict[str, Any]) -> None:
        refdes = str(block["refdes"])
        placed = self.part(
            refdes,
            str(block["symbol"]),
            str(block.get("value", "")),
            int(block["x"]),
            int(block["y"]),
            int(block.get("orientation", 0)),
        )
        treatments = {str(k): v for k, v in dict(block.get("pins", {})).items()}
        untreated = [p.number for p in placed.symbol.pins if p.number not in treatments]
        if untreated:
            raise DesignError(f"{refdes}: pins without a treatment (DS-06): {untreated}")
        unknown = sorted(set(treatments) - {p.number for p in placed.symbol.pins})
        if unknown:
            raise DesignError(f"{refdes}: treatments for pins that do not exist: {unknown}")
        ground_ends: list[tuple[int, int]] = []
        for pin in placed.symbol.pins:
            kind, name = self.parse_node(treatments[pin.number])
            px, py = placed.pin_end(pin.number)
            side = placed.pin_side(pin.number)
            dx, dy = {"left": (-1, 0), "right": (1, 0), "top": (0, 1), "bottom": (0, -1)}[side]
            ex, ey = px + dx * STUB, py + dy * STUB
            ref = f"{refdes}.{pin.number}"
            if kind == "nc":
                self.no_connect(px, py, side)
            elif kind == "label":
                self.wire([(px, py), (ex, ey)], start=ref, label=self.label(name, ex, ey, side))
                self.connect(name, ref)
            elif kind == "power":
                self.wire([(px, py), (ex, ey)], start=ref)
                self.power(name, ex, ey)
                self.connect(name, ref)
            elif kind == "gnd":
                self.wire([(px, py), (ex, ey)], start=ref)
                if side == "bottom":
                    ground_ends.append((ex, ey))
                else:
                    self.ground(ex, ey)
                self.connect("GND", ref)
            else:
                raise DesignError(f"{refdes}.{pin.number}: an IC pin cannot be 'none'")
        if ground_ends:
            ground_ends.sort()
            gx, gy = ground_ends[0]
            if len(ground_ends) > 1:
                # Designer merges a stub and a bar meeting at one point into a polyline,
                # and a symbol pin on that corner does not connect; so the bar runs one
                # stub past the last pin and the ground symbol sits on its free end.
                gx, gy = ground_ends[-1][0] + STUB, ground_ends[-1][1]
                self.wire([ground_ends[0], (gx, gy)])
            self.ground(gx, gy)

    def ladder(self, block: dict[str, Any], vertical: bool) -> None:
        path = list(block.get("path", []))
        if len(path) < 3 or len(path) % 2 == 0:
            raise DesignError(
                "a ladder or chain path alternates node, part, node, ... and ends on a node"
            )
        x0, y0 = int(block["x"]), int(block["y"])
        nodes = path[0::2]
        parts = path[1::2]
        node_points = []
        for index in range(len(nodes)):
            if vertical:
                node_points.append((x0, y0 - index * NODE_PITCH))
            else:
                node_points.append((x0 + index * NODE_PITCH, y0))
        node_kinds = [self.parse_node(node) for node in nodes]
        for index, (kind, _name) in enumerate(node_kinds):
            if kind == "nc":
                raise DesignError(f"node {index} of the ladder at ({x0}, {y0}) cannot be 'nc'")
            if kind == "gnd" and index != len(nodes) - 1:
                raise DesignError(f"ladder at ({x0}, {y0}): ground may only end a ladder")
        # A label sits on the wire that enters its node: the head wire of the part below
        # it, or the tail wire of the last part for the closing node.
        labels: dict[int, dict[str, Any]] = {}
        for index, (kind, name) in enumerate(node_kinds):
            if kind == "label":
                nx, ny = node_points[index]
                labels[index] = self.label(name, nx, ny, "right" if vertical else "top")
        pin_at_node: dict[int, list[str]] = {i: [] for i in range(len(nodes))}
        for index, spec in enumerate(parts):
            if not isinstance(spec, dict):
                raise DesignError(
                    f"ladder element {spec!r} must be an object with refdes, symbol and value"
                )
            nx, ny = node_points[index]
            cx, cy = (nx, ny - NODE_PITCH // 2) if vertical else (nx + NODE_PITCH // 2, ny)
            flip = bool(spec.get("flip", False))
            orientation = (1 if flip else 3) if vertical else (2 if flip else 0)
            placed = self.part(
                str(spec["refdes"]),
                str(spec["symbol"]),
                str(spec.get("value", "")),
                cx,
                cy,
                orientation,
            )
            if len(placed.symbol.pins) != 2:
                raise DesignError(
                    f"{placed.refdes}: ladders and chains take two-terminal parts only"
                )
            first, second = placed.symbol.pins
            head, tail = (first, second) if not flip else (second, first)
            hx, hy = placed.pin_end(head.number)
            tx, ty = placed.pin_end(tail.number)
            nx2, ny2 = node_points[index + 1]
            head_ref, tail_ref = f"{placed.refdes}.{head.number}", f"{placed.refdes}.{tail.number}"
            tail_label = labels.get(index + 1) if index == len(parts) - 1 else None
            self.wire([(nx, ny), (hx, hy)], end=head_ref, label=labels.get(index))
            self.wire([(tx, ty), (nx2, ny2)], start=tail_ref, label=tail_label)
            pin_at_node[index].append(head_ref)
            pin_at_node[index + 1].append(tail_ref)
            if index:
                self.neighbours.add(frozenset({str(parts[index - 1]["refdes"]), placed.refdes}))
        for index, (kind, name) in enumerate(node_kinds):
            nx, ny = node_points[index]
            pins = pin_at_node[index]
            if kind == "power":
                self.power(name, nx, ny)
                for ref in pins:
                    self.connect(name, ref)
            elif kind == "gnd":
                self.ground(nx, ny)
                for ref in pins:
                    self.connect("GND", ref)
            elif kind == "label":
                for ref in pins:
                    self.connect(name, ref)
            elif kind == "none":
                if len(pins) == 2:
                    self.plan.links.append((pins[0], pins[1]))
                elif len(pins) == 1:
                    raise DesignError(
                        f"node {index} of the ladder at ({x0}, {y0}) leaves {pins[0]} open"
                    )

    def free_text(self, block: dict[str, Any]) -> None:
        self.text(str(block["text"]), int(block["x"]), int(block["y"]), int(block.get("size", 8)))

    def free_box(self, block: dict[str, Any]) -> None:
        self.op(
            op="box",
            x1=int(block["x1"]),
            y1=int(block["y1"]),
            x2=int(block["x2"]),
            y2=int(block["y2"]),
        )

    # -- checks --------------------------------------------------------------------
    def check(self, sheet_number: int) -> None:
        boxes = [(p.refdes, p.bbox()) for p in self.placed.values()]
        for name, (x1, y1, x2, y2) in boxes + self.symbol_boxes:
            if x1 < MARGIN or y1 < MARGIN or x2 > self.width - MARGIN or y2 > self.height - MARGIN:
                self.plan.issues.append(
                    {
                        "check": "DS-07",
                        "sheet": sheet_number,
                        "object": name,
                        "bbox": [x1, y1, x2, y2],
                        "message": "outside the drawable area",
                    }
                )
        for i, (a, (ax1, ay1, ax2, ay2)) in enumerate(boxes):
            for b, (bx1, by1, bx2, by2) in boxes[i + 1 :]:
                gap = max(bx1 - ax2, ax1 - bx2, by1 - ay2, ay1 - by2)
                if gap < MIN_GAP and frozenset({a, b}) not in self.neighbours:
                    self.plan.issues.append(
                        {
                            "check": "DS-08",
                            "sheet": sheet_number,
                            "objects": [a, b],
                            "gap": gap,
                            "message": f"closer than {MIN_GAP} units",
                        }
                    )


def plan(design: dict[str, Any]) -> Plan:
    """Turn a design description into drawing operations plus their expected netlist."""
    if (
        not isinstance(design, dict)
        or not isinstance(design.get("sheets"), list)
        or not design["sheets"]
    ):
        raise DesignError("a design needs a non-empty 'sheets' list")
    size = str(design.get("sheet_size", "B")).upper()
    if size not in SHEET_SIZES:
        raise DesignError(f"unknown sheet size {size!r}; use one of {sorted(SHEET_SIZES)}")
    width, height = SHEET_SIZES[size]
    library = _Library(dict(design.get("symbols", {})))
    result = Plan(partition=str(design.get("partition") or PARTITION))
    boxed = bool(design.get("boxed_labels", True))
    total = len(design["sheets"])
    status = str(design.get("status", "")).strip()
    title = str(design.get("title", "")).strip()
    revision = str(design.get("revision", "")).strip()
    date = str(design.get("date", "")).strip()
    for index, sheet in enumerate(design["sheets"], start=1):
        number = int(sheet.get("number", index))
        planner = _SheetPlanner(result, library, width, height, boxed)
        planner.op(op="open_sheet", number=number)
        planner.op(op="wipe_sheet")
        border, code = SHEET_BORDERS[size]
        planner.op(op="set_sheet", border=border, size=code)
        sheet_title = str(sheet.get("title", "")).strip()
        if sheet_title:
            planner.text(sheet_title, MARGIN + 20, height - MARGIN - 30, 14)
        if sheet.get("description"):
            planner.text(str(sheet["description"]), MARGIN + 20, height - MARGIN - 55, 9)
        for block in sheet.get("blocks", []):
            kind = str(block.get("kind", ""))
            if kind in ("ic", "connector"):
                planner.ic(block)
            elif kind == "ladder":
                planner.ladder(block, vertical=True)
            elif kind == "chain":
                planner.ladder(block, vertical=False)
            elif kind == "text":
                planner.free_text(block)
            elif kind == "box":
                planner.free_box(block)
            else:
                raise DesignError(f"sheet {number}: unknown block kind {kind!r}")
        y = MARGIN + 20 * len(sheet.get("notes", [])) + 40
        for note in sheet.get("notes", []):
            planner.text(str(note), MARGIN + 20, y, 8)
            y -= 20
        footer = " | ".join(
            part for part in (status, title, f"Sheet {number}/{total}", revision, date) if part
        )
        if footer:
            planner.text(footer, MARGIN + 20, MARGIN + 10, 8)
        planner.op(op="save")
        planner.check(number)
        result.sheets.append({"number": number, "title": sheet_title, "parts": len(planner.placed)})
    result.symbols = dict(library.files)
    result.symbol_pins = dict(library.pins)
    return result


def plan_to_params(design: dict[str, Any], project: str) -> dict[str, Any]:
    """The `draw` request the native adapter expects."""
    result = plan(design)
    return {
        "project": project,
        "library": result.partition,
        "symbols": result.symbols,
        "ops": result.ops,
        "verify": {
            "nets": {name: sorted(pins) for name, pins in result.nets.items()},
            "links": [list(link) for link in result.links],
        },
        "summary": result.summary(),
    }
