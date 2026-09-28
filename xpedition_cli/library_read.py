"""Read a central library: its parts, cells, padstacks and symbols, and check them.

The parts, cell and padstack databases are binary, but the stock converters export
them as plain text: `PartsDB2HKP`, `CellDB2HKP` and `PadstackDB2HKP`, each run with
`-a` (unencrypted) and `-u mm`. The adapter runs them (method `library_export`); this
module turns their output into records. Symbols need no converter: Designer keeps
each one as a text file, `SymbolLibs/<partition>/sym/<name>.<version>`, and the
highest version is the current one.

Pure Python, so everything here is tested without Xpedition. Units are millimetres
for cells and padstacks and sheet units (10 mil) for symbols; a `V 54` symbol file
writes its coordinates 25400 times larger than a `V 53` one and is scaled back.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# -- the HKP record tree -----------------------------------------------------------------

_RECORD = re.compile(r"^\s*(\.+)([A-Za-z0-9_]+)(.*)$")
_TOKEN = re.compile(r'"[^"]*"|\([^)]*\)|[^\s,()"]+')


@dataclass
class Record:
    keyword: str
    values: list[Any] = field(default_factory=list)
    children: list[Record] = field(default_factory=list)
    level: int = 1

    def all(self, keyword: str) -> list[Record]:
        return [child for child in self.children if child.keyword == keyword]

    def first(self, keyword: str) -> Record | None:
        return next((child for child in self.children if child.keyword == keyword), None)

    def value(self, keyword: str, default: Any = None) -> Any:
        child = self.first(keyword)
        if child is None or not child.values:
            return default
        return child.values[0]

    def points(self) -> list[tuple[float, ...]]:
        return [value for value in self.values if isinstance(value, tuple)]


def _strip_comment(line: str) -> str:
    """The line up to a `!` that is not inside quotes."""
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == "!" and not quoted:
            return line[:index]
    return line


def _value(token: str) -> Any:
    if token.startswith('"'):
        return token[1:-1]
    if token.startswith("("):
        parts = [part.strip() for part in token[1:-1].split(",") if part.strip()]
        try:
            return tuple(float(part) for part in parts)
        except ValueError:
            return tuple(parts)
    try:
        return float(token)
    except ValueError:
        return token


def parse_records(text: str) -> list[Record]:
    """The top-level records of an HKP text, each with its nested children.

    A record is a line of dots, a keyword and values; the number of dots is its depth.
    A line without dots carries more values of the record above it, as the point
    lists of an outline do. `!` starts a comment.
    """
    roots: list[Record] = []
    stack: list[Record] = []
    last: Record | None = None
    for raw in text.splitlines():
        if raw.lstrip().startswith("!"):
            continue
        line = _strip_comment(raw)
        if not line.strip():
            continue
        match = _RECORD.match(line)
        if match is None:
            if last is not None:
                last.values += [_value(token) for token in _TOKEN.findall(line)]
            continue
        level = len(match.group(1))
        record = Record(
            match.group(2), [_value(t) for t in _TOKEN.findall(match.group(3))], level=level
        )
        while stack and stack[-1].level >= level:
            stack.pop()
        (stack[-1].children if stack else roots).append(record)
        stack.append(record)
        last = record
    return roots


def _text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# -- parts -----------------------------------------------------------------------------


def parse_parts(text: str, partition: str = "") -> list[dict[str, Any]]:
    """Every part of a `PartsDB2HKP` export.

    A part maps the pins of its symbol to the pins of its cell: the symbol lists pin
    names per swap group, and each slot (gate) of that swap group lists the cell pin
    numbers in the same order. `pins` flattens that to one row per mapped pin.
    """
    parts: list[dict[str, Any]] = []
    for record in parse_records(text):
        if record.keyword != "Number" or not record.values:
            continue
        properties: dict[str, str] = {}
        for prop in record.all("Prop"):
            if len(prop.values) >= 2:
                properties[_text(prop.values[0])] = _text(prop.values[1])
        names_by_group: dict[str, tuple[str, list[str]]] = {}
        symbols: list[str] = []
        for symbol in record.all("Symbol"):
            reference = _text(symbol.values[0]) if symbol.values else ""
            symbols.append(reference)
            for group in symbol.all("Symbol_SwapGroup"):
                key = _text(group.values[0]) if group.values else ""
                names = [_text(pin.values[0]) for pin in group.all("PinName") if pin.values]
                names_by_group[key] = (reference, names)
        pins: list[dict[str, Any]] = []
        slots = 0
        for block in record.all("Slots"):
            for slot in block.all("Slot_SwapGroup"):
                key = _text(slot.values[0]) if slot.values else ""
                slot_id = int(_number(slot.value("SlotID"), 1))
                slots = max(slots, slot_id)
                numbers = [_text(pin.values[0]) for pin in slot.all("PinNumber") if pin.values]
                reference, names = names_by_group.get(key, ("", []))
                for index, pin_number in enumerate(numbers):
                    pins.append(
                        {
                            "slot": slot_id,
                            "symbol": reference,
                            "name": names[index] if index < len(names) else "",
                            "number": pin_number,
                        }
                    )
        parts.append(
            {
                "number": _text(record.values[0]),
                "partition": partition,
                "name": _text(record.value("Name", "")),
                "label": _text(record.value("Label", "")),
                "description": _text(record.value("Desc", "")),
                "prefix": _text(record.value("RefPrefix", "")),
                "cell": _text(record.value("TopCell", "")),
                "type": properties.get("Type", ""),
                "properties": properties,
                "symbols": symbols,
                "slots": slots,
                "pins": pins,
            }
        )
    return parts


# -- cells -----------------------------------------------------------------------------

CELL_KINDS = {"PACKAGE_CELL": "package", "MECHANICAL_CELL": "mechanical", "DRAWING_CELL": "drawing"}
_OUTLINES = {
    "PLACEMENT_OUTLINE": "placement",
    "ASSEMBLY_OUTLINE": "assembly",
    "SILKSCREEN_OUTLINE": "silkscreen",
}


def _graphic(path: Record) -> dict[str, Any] | None:
    points = [point[:2] for point in (path.first("XY") or Record("XY")).points()]
    if not points:
        points = [point[:2] for point in (path.first("XYR") or Record("XYR")).points()]
    if not points:
        return None
    kind = {
        "CIRCLE_PATH": "circle",
        "POLYLINE_SHAPE": "shape",
        "POLYARC_SHAPE": "shape",
    }.get(path.keyword, "path")
    graphic: dict[str, Any] = {
        "kind": kind,
        "points": [[round(x, 4), round(y, 4)] for x, y in points],
        "width": _number(path.value("WIDTH")),
    }
    if kind == "circle":
        graphic["radius"] = _number(path.value("RADIUS"))
    return graphic


def _cell_pin(record: Record) -> dict[str, Any]:
    xy = (record.first("XY") or Record("XY")).points()
    x, y = (xy[0][0], xy[0][1]) if xy else (0.0, 0.0)
    return {
        "number": _text(record.values[0]) if record.values else "",
        "x": round(x, 4),
        "y": round(y, 4),
        "padstack": _text(record.value("PADSTACK", "")),
        "rotation": _number(record.value("ROTATION")),
    }


def parse_cells(text: str, partition: str = "") -> list[dict[str, Any]]:
    """Every cell of a `CellDB2HKP` export: pins, holes, outlines and the height."""
    cells: list[dict[str, Any]] = []
    for record in parse_records(text):
        kind = CELL_KINDS.get(record.keyword)
        if kind is None or not record.values:
            continue
        outlines: dict[str, list[dict[str, Any]]] = {name: [] for name in _OUTLINES.values()}
        height = 0.0
        for keyword, name in _OUTLINES.items():
            for block in record.all(keyword):
                if keyword == "PLACEMENT_OUTLINE":
                    height = max(height, _number(block.value("HEIGHT")))
                for path in block.children:
                    graphic = _graphic(path) if path.keyword.endswith(("_PATH", "_SHAPE")) else None
                    if graphic is not None:
                        outlines[name].append(graphic)
        texts = []
        for item in record.all("TEXT"):
            for attr in item.all("DISPLAY_ATTR"):
                xy = (attr.first("XY") or Record("XY")).points()
                if xy:
                    texts.append(
                        {
                            "text": _text(item.values[0]) if item.values else "",
                            "type": _text(item.value("TEXT_TYPE", "")),
                            "layer": _text(attr.value("TEXT_LYR", "")),
                            "x": round(xy[0][0], 4),
                            "y": round(xy[0][1], 4),
                            "height": _number(attr.value("HEIGHT")),
                        }
                    )
        cells.append(
            {
                "name": _text(record.values[0]),
                "partition": partition,
                "kind": kind,
                "group": _text(record.value("PACKAGE_GROUP", "")),
                "mount": _text(record.value("MOUNT_TYPE", "")),
                "layers": int(_number(record.value("NUMBER_LAYERS"), 0)),
                "description": _text(record.value("DESCRIPTION", "")),
                "height": height,
                "pins": [_cell_pin(pin) for pin in record.all("PIN")],
                "holes": [_cell_pin(hole) for hole in record.all("MOUNTING_HOLE")],
                "texts": texts,
                **outlines,
            }
        )
    return cells


# -- padstacks -------------------------------------------------------------------------

_PAD_SHAPES = (
    "ROUND",
    "SQUARE",
    "RECTANGLE",
    "OBLONG",
    "RADIUS_CORNER_RECTANGLE",
    "CHAMFERED_RECTANGLE",
    "OCTAGON",
    "ROUND_DONUT",
    "SQUARE_DONUT",
    "CUSTOM",
)
_PADSTACK_LAYERS = {
    "TOP_PAD": "top",
    "INTERNAL_PAD": "internal",
    "BOTTOM_PAD": "bottom",
    "CLEARANCE_PAD": "clearance",
    "TOP_SOLDERMASK_PAD": "top_mask",
    "BOTTOM_SOLDERMASK_PAD": "bottom_mask",
    "TOP_SOLDERPASTE_PAD": "top_paste",
    "BOTTOM_SOLDERPASTE_PAD": "bottom_paste",
    "HOLE_NAME": "hole",
}


def _shape(record: Record) -> dict[str, Any]:
    shape = next((child for child in record.children if child.keyword in _PAD_SHAPES), None)
    if shape is None:
        other = next((child for child in record.children if child.first("DIAMETER")), None)
        shape = other or Record("UNKNOWN")
    diameter = _number(shape.value("DIAMETER"))
    width = _number(shape.value("WIDTH"), diameter)
    height = _number(shape.value("HEIGHT"), width)
    result: dict[str, Any] = {
        "shape": shape.keyword,
        "width": round(width, 4),
        "height": round(height, 4),
    }
    if shape.first("RADIUS") is not None:
        result["radius"] = _number(shape.value("RADIUS"))
    return result


def parse_padstacks(text: str) -> dict[str, dict[str, dict[str, Any]]]:
    """The pads, holes and padstacks of a `PadstackDB2HKP` export."""
    pads: dict[str, dict[str, Any]] = {}
    holes: dict[str, dict[str, Any]] = {}
    padstacks: dict[str, dict[str, Any]] = {}
    for record in parse_records(text):
        if not record.values:
            continue
        name = _text(record.values[0])
        if record.keyword == "PAD":
            pads[name] = _shape(record)
        elif record.keyword == "HOLE":
            options = [_text(v) for v in (record.first("HOLE_OPTIONS") or Record("")).values]
            holes[name] = {
                **_shape(record),
                "plated": "PLATED" in options,
            }
        elif record.keyword == "PADSTACK":
            stack: dict[str, Any] = {"type": _text(record.value("PADSTACK_TYPE", ""))}
            technology = record.first("TECHNOLOGY") or Record("TECHNOLOGY")
            for keyword, key in _PADSTACK_LAYERS.items():
                value = technology.value(keyword)
                if value is not None:
                    stack[key] = _text(value)
            padstacks[name] = stack
    return {"pads": pads, "holes": holes, "padstacks": padstacks}


def padstack_geometry(library: dict[str, Any], name: str) -> dict[str, Any] | None:
    """A padstack's copper on the mount side and its hole, looked up through its pads."""
    stack = library.get("padstacks", {}).get(name)
    if stack is None:
        return None
    pad = library.get("pads", {}).get(stack.get("top") or stack.get("internal") or "")
    hole = library.get("holes", {}).get(stack.get("hole") or "")
    return {
        "name": name,
        "type": stack.get("type", ""),
        "pad": pad,
        "hole": hole,
        "mask": library.get("pads", {}).get(stack.get("top_mask") or ""),
    }


# -- symbols ---------------------------------------------------------------------------

V54_SCALE = 25400.0


def _pin_side(x: float, y: float, bx: float, by: float) -> str:
    if abs(x - bx) >= abs(y - by):
        return "left" if x < bx else "right"
    return "bottom" if y < by else "top"


def parse_symbol(text: str, partition: str = "", name: str = "") -> dict[str, Any]:
    """A symbol file's pins, lines and attributes, in `V 53` sheet units.

    A pin is a `P` record (connection end, then the end on the body); the `L` record
    after it is the pin's name, the one the parts database maps, and its `A` records
    carry the pin number (`#=`) and type (`PINTYPE=`).
    """
    lines = text.splitlines()
    scale = V54_SCALE if lines and lines[0].split()[:2] == ["V", "54"] else 1.0

    def num(token: str) -> float:
        return round(float(token) / scale, 3)

    result: dict[str, Any] = {
        "partition": partition,
        "name": name,
        "kind": "",
        "device": "",
        "pins": [],
        "lines": [],
        "attributes": {},
        "bbox": [0.0, 0.0, 0.0, 0.0],
    }
    pin: dict[str, Any] | None = None
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        kind = fields[0]
        try:
            if kind == "K" and len(fields) >= 3 and not name:
                result["name"] = fields[2].rsplit(".", 1)[0] if "." in fields[2] else fields[2]
            elif kind == "Y" and len(fields) >= 2:
                result["kind"] = {"1": "part", "4": "power"}.get(fields[1], fields[1])
            elif kind == "l" and len(fields) >= 2:
                count = int(fields[1])
                values = [num(v) for v in fields[2 : 2 + 2 * count]]
                result["lines"].append(
                    [[x, y] for x, y in zip(values[0::2], values[1::2], strict=False)]
                )
            elif kind == "P" and len(fields) >= 6:
                x, y, bx, by = (num(v) for v in fields[2:6])
                pin = {
                    "number": "",
                    "name": "",
                    "type": "",
                    "x": x,
                    "y": y,
                    "bx": bx,
                    "by": by,
                    "side": _pin_side(x, y, bx, by),
                }
                result["pins"].append(pin)
            elif kind == "L" and pin is not None and len(fields) >= 10:
                pin["name"] = " ".join(fields[9:])
            elif kind in {"A", "U"} and "=" in line and len(fields) >= 8:
                key, _, value = " ".join(fields[7:]).partition("=")
                if kind == "A" and pin is not None:
                    if key == "#":
                        pin["number"] = value
                    elif key == "PINTYPE":
                        pin["type"] = value
                    elif key == "NAME" and not pin["name"]:
                        pin["name"] = value
                else:
                    result["attributes"][key] = value
                    if key == "DEVICE":
                        result["device"] = value
        except (ValueError, IndexError):
            continue
    for index, item in enumerate(result["pins"], start=1):
        # a pin without a `#=` attribute is numbered by its order, as Designer shows it
        item["number"] = item["number"] or str(index)
        item["name"] = item["name"] or item["number"]
    xs = [p[0] for path in result["lines"] for p in path] + [
        v for p in result["pins"] for v in (p["x"], p["bx"])
    ]
    ys = [p[1] for path in result["lines"] for p in path] + [
        v for p in result["pins"] for v in (p["y"], p["by"])
    ]
    if xs and ys:
        result["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
    return result


def symbol_files(root: Path, partitions: list[str] | None = None) -> list[tuple[str, str, Path]]:
    """`(partition, name, path)` of the current version of every symbol under `root`
    (a `SymbolLibs` folder), optionally only in `partitions` (any case)."""
    wanted = {p.casefold() for p in partitions} if partitions else None
    found: list[tuple[str, str, Path]] = []
    if not root.is_dir():
        return found
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or (wanted is not None and folder.name.casefold() not in wanted):
            continue
        latest: dict[str, tuple[int, Path]] = {}
        sym = folder / "sym"
        if not sym.is_dir():
            continue
        for path in sym.iterdir():
            stem, _, version = path.name.rpartition(".")
            if not stem or not version.isdigit() or not path.is_file():
                continue
            if stem not in latest or int(version) > latest[stem][0]:
                latest[stem] = (int(version), path)
        found += [(folder.name, stem, latest[stem][1]) for stem in sorted(latest)]
    return found


def read_symbol(root: Path, partition: str, name: str) -> dict[str, Any] | None:
    """The current version of `partition:name` under `root`, parsed; None if absent."""
    for found_partition, stem, path in symbol_files(root, [partition]):
        if stem == name:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
            symbol = parse_symbol(text, found_partition, stem)
            symbol["version"] = int(path.suffix[1:])
            symbol["path"] = str(path)
            return symbol
    return None


# -- the library as a whole -------------------------------------------------------------


@dataclass
class Library:
    """What a central library holds, as the converters and symbol files report it."""

    root: str = ""
    parts: list[dict[str, Any]] = field(default_factory=list)
    cells: list[dict[str, Any]] = field(default_factory=list)
    padstacks: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=lambda: {"pads": {}, "holes": {}, "padstacks": {}}
    )
    symbols: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)

    def find_parts(self, number: str) -> list[dict[str, Any]]:
        return [part for part in self.parts if part["number"] == number]

    def find_cell(self, name: str) -> dict[str, Any] | None:
        return next((cell for cell in self.cells if cell["name"] == name), None)

    def find_symbol(self, reference: str) -> dict[str, Any] | None:
        partition, _, name = reference.rpartition(":")
        return next(
            (
                symbol
                for symbol in self.symbols
                if symbol["name"] == name and (not partition or symbol["partition"] == partition)
            ),
            None,
        )


def part_row(part: dict[str, Any]) -> dict[str, Any]:
    """A part as `library list` shows it: what identifies it, without the pin map."""
    return {
        "number": part["number"],
        "partition": part["partition"],
        "description": part["description"],
        "prefix": part["prefix"],
        "type": part["type"],
        "cell": part["cell"],
        "symbols": part["symbols"],
        "pins": len(part["pins"]),
        "value": part["properties"].get("Value", ""),
    }


def cell_row(cell: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": cell["name"],
        "partition": cell["partition"],
        "kind": cell["kind"],
        "group": cell["group"],
        "mount": cell["mount"],
        "description": cell["description"],
        "pins": len(cell["pins"]),
        "pads": len(cell["pins"]),
        "pin_numbers": len({pin["number"] for pin in cell["pins"]}),
        "holes": len(cell["holes"]),
        "height": cell["height"],
    }


def symbol_row(symbol: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": symbol["name"],
        "partition": symbol["partition"],
        "reference": f"{symbol['partition']}:{symbol['name']}",
        "kind": symbol["kind"],
        "version": symbol.get("version"),
        "pins": len(symbol["pins"]),
    }


def padstack_rows(library: Library) -> list[dict[str, Any]]:
    rows = []
    for name in sorted(library.padstacks.get("padstacks", {})):
        geometry = padstack_geometry(library.padstacks, name) or {}
        pad = geometry.get("pad") or {}
        hole = geometry.get("hole") or {}
        rows.append(
            {
                "name": name,
                "type": geometry.get("type", ""),
                "shape": pad.get("shape", ""),
                "width": pad.get("width"),
                "height": pad.get("height"),
                "drill": hole.get("width"),
                "plated": hole.get("plated"),
            }
        )
    return rows


# -- checks ----------------------------------------------------------------------------

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _finding(severity: str, rule: str, item: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"severity": severity, "rule": rule, "item": item, "message": message, **extra}


def check_part(library: Library, part: dict[str, Any]) -> list[dict[str, Any]]:
    """What is wrong with one part against the cells, symbols and padstacks it names."""
    findings: list[dict[str, Any]] = []
    label = f"part {part['number']}"
    cell = library.find_cell(part["cell"]) if part["cell"] else None
    if not part["cell"]:
        findings.append(_finding("high", "part_without_cell", label, "the part names no cell"))
    elif cell is None:
        findings.append(
            _finding(
                "high",
                "part_cell_missing",
                label,
                f"cell {part['cell']!r} is not in the library",
                cell=part["cell"],
            )
        )
    symbol_pins: dict[str, set[str]] = {}
    for reference in part["symbols"]:
        symbol = library.find_symbol(reference)
        if symbol is None:
            findings.append(
                _finding(
                    "high",
                    "part_symbol_missing",
                    label,
                    f"symbol {reference!r} is not in the library",
                    symbol=reference,
                )
            )
            continue
        symbol_pins[reference] = {pin["name"] for pin in symbol["pins"]}
    mapped_names: dict[str, set[str]] = {}
    for row in part["pins"]:
        mapped_names.setdefault(row["symbol"], set()).add(row["name"])
        names = symbol_pins.get(row["symbol"])
        if names is not None and row["name"] not in names:
            findings.append(
                _finding(
                    "high",
                    "pin_not_on_symbol",
                    label,
                    f"pin {row['name']!r} is mapped but symbol {row['symbol']} has no such pin",
                    pin=row["name"],
                )
            )
    for reference, names in symbol_pins.items():
        unmapped = sorted(names - mapped_names.get(reference, set()))
        if unmapped and part["pins"]:
            findings.append(
                _finding(
                    "medium",
                    "symbol_pin_unmapped",
                    label,
                    f"symbol {reference} pins {unmapped[:10]} map to no cell pin; they carry "
                    "no net onto the board",
                    pins=unmapped,
                )
            )
    if cell is not None:
        numbers = {pin["number"] for pin in cell["pins"]}
        mapped = {row["number"] for row in part["pins"]}
        missing = sorted(mapped - numbers)
        if missing:
            findings.append(
                _finding(
                    "high",
                    "pin_not_on_cell",
                    label,
                    f"pin numbers {missing[:10]} are mapped but cell {cell['name']} has no "
                    "such pin",
                    pins=missing,
                )
            )
        unmapped = sorted(numbers - mapped)
        if unmapped and part["pins"]:
            findings.append(
                _finding(
                    "medium",
                    "cell_pad_unmapped",
                    label,
                    f"cell {cell['name']} pins {unmapped[:10]} have no symbol pin; their pads "
                    "carry no net",
                    pins=unmapped,
                )
            )
    if not part["description"]:
        findings.append(_finding("low", "part_without_description", label, "no description"))
    return findings


def check(library: Library, partitions: list[str] | None = None) -> list[dict[str, Any]]:
    """Every finding across the library, most severe first.

    Parts against their cells, symbols and pin maps; cells against the padstack
    database; symbols for repeated pin names (Designer names a net after the pin a
    wire meets, so two pins of one name collide in the draw); part numbers used in
    more than one partition.
    """
    wanted = {p.casefold() for p in partitions} if partitions else None

    def included(item: dict[str, Any]) -> bool:
        return wanted is None or str(item.get("partition", "")).casefold() in wanted

    findings: list[dict[str, Any]] = []
    for part in library.parts:
        if included(part):
            findings += check_part(library, part)
    stacks = library.padstacks.get("padstacks", {})
    for cell in library.cells:
        if not included(cell):
            continue
        missing = sorted({pin["padstack"] for pin in cell["pins"] + cell["holes"]} - set(stacks))
        if missing:
            findings.append(
                _finding(
                    "high",
                    "padstack_missing",
                    f"cell {cell['name']}",
                    f"padstacks {missing[:10]} are not in the padstack database",
                    padstacks=missing,
                )
            )
        if cell["kind"] == "package" and not cell["pins"]:
            findings.append(
                _finding("high", "cell_without_pins", f"cell {cell['name']}", "no pins")
            )
    for symbol in library.symbols:
        if not included(symbol) or symbol["kind"] != "part":
            continue
        counts = Counter(pin["name"] for pin in symbol["pins"])
        repeated = sorted(name for name, count in counts.items() if count > 1)
        if repeated:
            findings.append(
                _finding(
                    "medium",
                    "symbol_pin_names_repeat",
                    f"symbol {symbol['partition']}:{symbol['name']}",
                    f"pin names {repeated[:10]} repeat; Designer names a net after the pin a "
                    "wire meets, so their wires collide (6035)",
                    pins=repeated,
                )
            )
    numbers = Counter(part["number"] for part in library.parts)
    for number, count in sorted(numbers.items()):
        if count > 1:
            places = sorted(p["partition"] for p in library.find_parts(number))
            if wanted is None or any(p.casefold() in wanted for p in places):
                findings.append(
                    _finding(
                        "medium",
                        "part_number_repeats",
                        f"part {number}",
                        f"the part number is in {count} partitions ({', '.join(places)}); the "
                        "packager takes whichever it finds first",
                        partitions=places,
                    )
                )
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 3), f["rule"], f["item"]))
    return findings
