"""Parts for the central library from a parts file: a symbol, a footprint and the pin map.

A parts file is JSON:

    {
      "partition": "PartQuest",
      "parts": [
        {
          "number": "TPS7A2033PDBVR",
          "description": "LDO regulator, 3.3 V, 300 mA",
          "prefix": "U",
          "value": "3.3V",
          "properties": {"Manufacturer": "Texas Instruments"},
          "symbol": {"left": [["1", "IN"], ["3", "EN"]], "right": [["5", "OUT"], ["4", "NC"]],
                     "bottom": [["2", "GND"]], "pintypes": {"1": "POWER", "2": "GROUND"}},
          "footprint": {"family": "gullwing", "pins": 5, "positions": 6, "omit": [5],
                        "pitch": 0.95, "span": [2.6, 3.0], "terminal": [0.3, 0.6],
                        "lead_width": [0.3, 0.5], "body": [1.6, 2.9], "height": 1.45}
        }
      ]
    }

The symbol is a box as a design's symbols are written, or a built-in kind
(`{"kind": "RES"}`: RES, CAP, CAPP, IND, DIODE, LED, SW, BAT, NTC, NMOS, PMOS, TP).
The footprint is an IPC-7351B family (`ipc7351`), lands given one by one (`pads`;
several lands may carry one pin number), a placeholder package (`{"package":
"SOIC8"}`) or a cell the library holds (`{"cell": "NAME"}`).
Each symbol pin maps to the cell pin of the same number unless `pinmap` says
otherwise, and the two sets of numbers must match: Layout's Database Load refuses a
cell whose pins differ from its part's.

`plan(spec, library)` is pure: it builds everything, then compares it with what the
library holds so that nothing is overwritten unasked -- an identical symbol, cell,
padstack or pad is left alone, a different one of the same name is a replacement.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ipc7351
from . import library_hkp as H
from . import library_read as R
from . import schematic_layout as L
from . import symbols as S

MAX_PARTS = 200
PREFIX = re.compile(r"[A-Za-z]{1,8}")
NUMBER_LIMIT = 100
PLACEHOLDER = "(placeholder part)"  # how `library build` marks the parts it makes up


class PartsFileError(ValueError):
    """The parts file cannot be turned into library content."""


@dataclass
class AddPlan:
    partition: str
    library: H.LibraryPlan
    symbols: dict[str, str] = field(default_factory=dict)  # symbol files to write
    parts: list[dict[str, Any]] = field(default_factory=list)
    cells: dict[str, H.Cell] = field(default_factory=dict)  # every cell a part uses
    symbol_objects: dict[str, Any] = field(default_factory=dict)  # name -> symbols.Symbol
    actions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    replaces: list[str] = field(default_factory=list)  # what the import would overwrite
    kept: set[str] = field(default_factory=set)  # parts the library holds as they are

    def texts(self) -> dict[str, str]:
        return {
            "padstacks": H.render_padstacks(self.library) if self.library.padstacks else "",
            "cells": H.render_cells(self.library)
            if any(not c.external for c in self.library.cells.values())
            else "",
            "parts": self._parts_text(),
        }

    def _parts_text(self) -> str:
        """The parts to write: those the library does not hold as they are."""
        parts = {n: p for n, p in self.library.parts.items() if n not in self.kept}
        if not parts:
            return ""
        return H.render_parts(dataclasses.replace(self.library, parts=parts))


def load(path: Path) -> dict[str, Any]:
    import json

    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise PartsFileError(f"the parts file cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PartsFileError(f"the parts file is not valid JSON: {exc}") from exc
    return validate(spec)


def validate(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise PartsFileError("a parts file is an object with a parts list")
    parts = spec.get("parts")
    if not isinstance(parts, list) or not parts:
        raise PartsFileError("parts must be a non-empty list")
    if len(parts) > MAX_PARTS:
        raise PartsFileError(f"at most {MAX_PARTS} parts a file")
    partition = str(spec.get("partition") or H.PARTITION)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", partition):
        raise PartsFileError(f"partition {partition!r} must be a plain identifier")
    numbers = [str(p.get("number", "")) if isinstance(p, dict) else "" for p in parts]
    repeated = sorted({n for n in numbers if n and numbers.count(n) > 1})
    if repeated:
        raise PartsFileError(f"part numbers repeat in the file: {repeated}")
    return {**spec, "partition": partition}


def _safe_name(text: str, limit: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_+-]+", "_", text).strip("_") or "PART"
    if not re.match(r"[A-Za-z0-9_]", cleaned):
        cleaned = "P" + cleaned
    return cleaned[:limit]


def _symbol(item: dict[str, Any], where: str, number: str) -> tuple[Any, str | None]:
    """The symbol and, for a generated one, its file text."""
    spec = item.get("symbol")
    if not isinstance(spec, dict):
        raise PartsFileError(f"{where}: symbol is an object (a box, or a built-in kind)")
    kind = str(spec.get("kind", "box"))
    if kind in L.BUILTIN:
        # named as a design names it, so a design's RES and the library's are one file
        base, specs = kind, {}
    elif kind == "box":
        base = str(spec.get("name") or _safe_name(number, 38))
        specs = {base: {**spec, "kind": "box"}}
    else:
        raise PartsFileError(
            f"{where}: symbol kind is box or one of {', '.join(sorted(L.BUILTIN))}"
        )
    library = L._Library(specs)
    try:
        symbol = library.symbol(base)
    except L.DesignError as exc:
        raise PartsFileError(f"{where}: {exc}") from exc
    if not symbol.pins:
        raise PartsFileError(f"{where}: the symbol has no pins; a part needs at least one")
    return symbol, library.files.get(symbol.name)


def _external_cell(
    name: str, existing: R.Library | None, where: str
) -> tuple[H.Cell, list[dict[str, Any]]]:
    if existing is None:
        raise PartsFileError(f"{where}: cell {name!r} needs the library read first")
    found = existing.find_cell(name)
    if found is None:
        raise PartsFileError(f"{where}: the library has no cell {name!r}")
    pins = [
        H.CellPin(pin["number"], pin["padstack"], pin["x"], pin["y"], pin["rotation"])
        for pin in found["pins"]
    ]
    cell = H.Cell(
        found["name"],
        found["group"],
        found["mount"],
        pins,
        (0.0, 0.0, 0.0, 0.0),
        found["height"],
        found["description"],
        external=True,
        partition=found["partition"],
    )
    return cell, found["pins"]


def _footprint(
    item: dict[str, Any],
    where: str,
    number: str,
    prefix: str,
    pin_count: int,
    stock: H._Stock,
    existing: R.Library | None,
) -> H.Cell:
    spec = item.get("footprint")
    if not isinstance(spec, dict):
        raise PartsFileError(f"{where}: footprint is an object")
    try:
        if "cell" in spec:
            cell, _pins = _external_cell(str(spec["cell"]), existing, where)
            return cell
        if "package" in spec:
            return H.build_package(stock, str(spec["package"]), pin_count)
        return ipc7351.footprint(spec, stock, kind=prefix, name=_safe_name(number, 60))
    except (ipc7351.FootprintError, ValueError) as exc:
        raise PartsFileError(f"{where}: footprint: {exc}") from exc


def _pinmap(item: dict[str, Any], where: str) -> dict[str, str]:
    raw = item.get("pinmap") or {}
    if not isinstance(raw, dict):
        raise PartsFileError(f"{where}: pinmap maps symbol pin numbers to cell pin numbers")
    return {str(k): str(v) for k, v in raw.items()}


def _texts(part: H.Part) -> list[tuple[str, str]]:
    where = f"part {part.number}"
    texts = [
        (where, part.number),
        (f"{where} description", part.description),
        (f"{where} type", part.part_type),
    ]
    for key, value in part.properties.items():
        texts += [(f"{where} property name", key), (f"{where} property {key}", value)]
    return texts


def plan(spec: dict[str, Any], existing: R.Library | None = None) -> AddPlan:
    """Everything the parts file adds, and how it meets what the library holds."""
    spec = validate(spec)
    partition = spec["partition"]
    library = H.LibraryPlan(partition=partition)
    stock = H._Stock(library)
    result = AddPlan(partition=partition, library=library)
    for index, item in enumerate(spec["parts"]):
        where = f"parts[{index}]"
        if not isinstance(item, dict):
            raise PartsFileError(f"{where} must be an object")
        number = " ".join(str(item.get("number", "")).split())
        if not number or len(number) > NUMBER_LIMIT:
            raise PartsFileError(f"{where}: number is the part number, 1 to {NUMBER_LIMIT} chars")
        where = f"part {number}"
        prefix = str(item.get("prefix", ""))
        if not PREFIX.fullmatch(prefix):
            raise PartsFileError(f"{where}: prefix is the reference designator's letters, as U")
        symbol, text = _symbol(item, where, number)
        if text is not None:
            result.symbols[symbol.name] = text
        result.symbol_objects[symbol.name] = symbol
        cell = _footprint(item, where, number, prefix.upper(), len(symbol.pins), stock, existing)
        library.cells.setdefault(cell.name, cell)
        result.cells[cell.name] = cell
        if cell.external and cell.partition and cell.partition != partition:
            library.cell_partitions.add(cell.partition)
        pinmap = _pinmap(item, where)
        symbol_numbers = [pin.number for pin in symbol.pins]
        unknown = sorted(set(pinmap) - set(symbol_numbers))
        if unknown:
            raise PartsFileError(f"{where}: pinmap names symbol pins {unknown} it does not have")
        mapped = [pinmap.get(n, n) for n in symbol_numbers]
        cell_numbers = {pin.number for pin in cell.pins}
        missing = sorted(set(mapped) - cell_numbers)
        unmapped = sorted(cell_numbers - set(mapped))
        if missing or unmapped or len(set(mapped)) != len(mapped):
            detail = []
            if missing:
                detail.append(f"symbol pins go to cell pins {missing} the cell does not have")
            if unmapped:
                detail.append(f"cell pins {unmapped} have no symbol pin")
            if len(set(mapped)) != len(mapped):
                detail.append("two symbol pins go to one cell pin")
            raise PartsFileError(
                f"{where}: the symbol ({len(symbol_numbers)} pins) and cell {cell.name} "
                f"({len(cell_numbers)} pin numbers) do not match: {'; '.join(detail)}. Every "
                "cell pin needs a symbol pin (add NC pins to the symbol), or use pinmap"
            )
        prefix = prefix.upper()
        extra = item.get("properties") or {}
        if not isinstance(extra, dict):
            raise PartsFileError(f"{where}: properties is an object of text values")
        properties = {str(k): str(v) for k, v in extra.items() if str(k) != "Value"}
        value = item.get("value", extra.get("Value"))
        if value not in (None, ""):
            numeric = H.library_value(str(value), whole=True)
            if numeric is None:
                raise PartsFileError(
                    f"{where}: value {value!r} is not one the library keeps: its Value "
                    "property is a number with an SI multiplier (10k, 4.7k, 100n, 1u), and "
                    "anything else is stored as 0. Leave value out, or put the text in "
                    "description or another property"
                )
            properties = {"Value": numeric, **properties}
        part = H.Part(
            number=number,
            prefix=prefix,
            cell=cell.name,
            symbol=symbol.name,
            pin_names=[pin.name or pin.number for pin in symbol.pins],
            pin_numbers=mapped,
            part_type=str(item.get("type") or H.PART_TYPES.get(prefix, "Misc")),
            description=" ".join(str(item.get("description", "")).split()),
            properties=properties,
        )
        bad = H.unquotable(_texts(part))
        if bad:
            raise PartsFileError(
                f"{where}: {bad[0][0]} holds a double quote or a line break, which the "
                "library files cannot quote"
            )
        library.parts[number] = part
        lands = len(cell.pins)
        result.parts.append(
            {
                "number": number,
                "prefix": prefix,
                "symbol": f"{partition}:{symbol.name}",
                "symbol_pins": len(symbol.pins),
                "cell": cell.name,
                "cell_partition": cell.partition or partition,
                "lands": lands,
                "pin_numbers": len(cell_numbers),
                "multi_pad_pins": sorted(
                    n for n in cell_numbers if sum(1 for p in cell.pins if p.number == n) > 1
                ),
                "footprint": _footprint_kind(item.get("footprint") or {}),
                "pins": [
                    {"number": n, "name": pin.name or pin.number, "cell_pin": m}
                    for pin, n, m in zip(symbol.pins, symbol_numbers, mapped, strict=True)
                ],
            }
        )
    bad_cells = H.unquotable([t for c in library.cells.values() for t in H.cell_texts(c)])
    if bad_cells:
        raise PartsFileError(f"{bad_cells[0][0]} holds a double quote or a line break")
    _compare(result, existing)
    return result


def _footprint_kind(spec: dict[str, Any]) -> str:
    for key in ("cell", "package", "pads"):
        if key in spec:
            return key
    return f"ipc7351:{spec.get('family', '')}"


def _same_part(ours: dict[str, Any] | None, theirs: dict[str, Any]) -> bool:
    """The part this file makes is the one the library holds: the same content as read."""
    return ours is not None and R.part_content(ours) == R.part_content(theirs)


def _same_cell(ours: H.Cell, theirs: dict[str, Any]) -> bool:
    mine = sorted((p.number, round(p.x, 3), round(p.y, 3), p.padstack) for p in ours.pins)
    held = sorted(
        (p["number"], round(p["x"], 3), round(p["y"], 3), p["padstack"]) for p in theirs["pins"]
    )
    my_holes = sorted((round(h.x, 3), round(h.y, 3), h.padstack) for h in ours.holes)
    held_holes = sorted(
        (round(h["x"], 3), round(h["y"], 3), h["padstack"]) for h in theirs.get("holes", [])
    )
    return mine == held and my_holes == held_holes


def _compare(result: AddPlan, existing: R.Library | None) -> None:
    """Sort every item into add, keep (identical, left alone) or replace."""
    library = result.library
    actions: dict[str, list[dict[str, Any]]] = {
        "parts": [],
        "symbols": [],
        "cells": [],
        "padstacks": [],
    }
    held = existing or R.Library()
    # the parts as the library would read them back, to meet what it holds
    ours = {
        part["number"]: part for part in R.parse_parts(H.render_parts(library), result.partition)
    }
    kept: list[str] = []
    for number in sorted(library.parts):
        places = sorted({p["partition"] for p in held.find_parts(number)})
        elsewhere = [p for p in places if p != result.partition]
        if elsewhere:
            raise PartsFileError(
                f"part {number} is in partition {', '.join(elsewhere)} already; the packager "
                "would find two parts of one number. Add it there, or give another number"
            )
        if places:
            old = held.find_parts(number)[0]
            if _same_part(ours.get(number), old):
                actions["parts"].append({"number": number, "action": "keep"})
                kept.append(number)
                continue
            action = "replace"
            if old["description"].endswith(PLACEHOLDER):
                action = "replace_placeholder"
            actions["parts"].append({"number": number, "action": action})
            result.replaces.append(f"part {number}")
        else:
            actions["parts"].append({"number": number, "action": "add"})
    # left alone: the import does not write them again
    result.kept.update(kept)
    for name in sorted(result.symbols):
        found = held.find_symbol(f"{result.partition}:{name}")
        # a symbol is named after a hash of its text: the same name is the same symbol
        actions["symbols"].append({"name": name, "action": "keep" if found else "add"})
    for name in [n for n in list(result.symbols) if held.find_symbol(f"{result.partition}:{n}")]:
        del result.symbols[name]
    for name, cell in sorted(library.cells.items()):
        if cell.external:
            if existing is not None and held.find_cell(name) is None:
                raise PartsFileError(
                    f"cell {name} is not in the library; list the cells it holds with "
                    "library list --kind cells"
                )
            actions["cells"].append(
                {"name": name, "action": "use", "partition": cell.partition or result.partition}
            )
            continue
        found = held.find_cell(name)
        if found is None:
            actions["cells"].append({"name": name, "action": "add"})
        elif _same_cell(cell, found):
            actions["cells"].append(
                {"name": name, "action": "keep", "partition": found["partition"]}
            )
            cell.external = True
            cell.partition = found["partition"]
            if found["partition"] != result.partition:
                library.cell_partitions.add(found["partition"])
        else:
            users = sorted(p["number"] for p in held.parts if p["cell"] == name)
            actions["cells"].append(
                {
                    "name": name,
                    "action": "replace",
                    "partition": found["partition"],
                    "used_by": users,
                }
            )
            result.replaces.append(f"cell {name}")
            if found["partition"] != result.partition:
                raise PartsFileError(
                    f"cell {name} is in partition {found['partition']} with other lands; "
                    "give the footprint another name"
                )
    stacks = held.padstacks
    for name in sorted(library.padstacks):
        stack = library.padstacks[name]
        theirs = stacks.get("padstacks", {}).get(name)
        if theirs is None:
            actions["padstacks"].append({"name": name, "action": "add"})
            continue
        same = (
            theirs.get("top") == stack.pad
            and (theirs.get("top_mask") or None) == stack.mask
            and (theirs.get("hole") or None) == stack.hole
            and (theirs.get("top_paste") or stack.pad) == (stack.paste or stack.pad)
        )
        if stack.kind == "MOUNTING_HOLE":
            same = (theirs.get("hole") or None) == stack.hole
        actions["padstacks"].append({"name": name, "action": "keep" if same else "replace"})
        if not same:
            result.replaces.append(f"padstack {name}")
    for name in [a["name"] for a in actions["padstacks"] if a["action"] == "keep"]:
        del library.padstacks[name]
    # pads and holes stay in the file even when the library has them: the new padstacks
    # refer to them, and merging an identical definition changes nothing
    for kind, table in (("pads", library.pads), ("holes", library.holes)):
        known = stacks.get(kind, {})
        for name, ours in table.items():
            theirs = known.get(name)
            if theirs is None:
                continue
            if kind == "pads":
                width = ours.width
                height = ours.width if ours.shape == "ROUND" else ours.height
            else:
                width, height = ours.slot or (ours.diameter, ours.diameter)
            same = (
                abs(float(theirs.get("width") or 0) - width) < 1e-3
                and abs(float(theirs.get("height") or 0) - height) < 1e-3
            )
            if not same:
                result.replaces.append(f"{kind[:-1]} {name}")
    result.actions = actions


def placeholder(part: dict[str, Any]) -> bool:
    return str(part.get("description", "")).endswith(PLACEHOLDER)


def views(result: AddPlan, existing: R.Library | None = None) -> list[dict[str, Any]]:
    """Each planned part as `library_render` draws it: the symbol and cell rendered to the
    library's own text formats and parsed back, so the picture is what the import reads."""
    held = existing or R.Library()
    stacks = R.parse_padstacks(H.render_padstacks(result.library))
    merged = {
        kind: {**held.padstacks.get(kind, {}), **stacks.get(kind, {})}
        for kind in ("pads", "holes", "padstacks")
    }
    rows = []
    for part in result.parts:
        _partition, _, symbol_name = part["symbol"].rpartition(":")
        symbol_object = result.symbol_objects.get(symbol_name)
        symbol = (
            R.parse_symbol(symbol_object.render(), result.partition, symbol_name)
            if symbol_object is not None
            else held.find_symbol(part["symbol"])
        )
        cell_object = result.cells.get(part["cell"])
        cell = None
        if cell_object is not None and cell_object.pins and cell_object.graphics:
            single = H.LibraryPlan(partition=result.partition)
            copy = H.Cell(**{**cell_object.__dict__, "external": False})
            single.cells[copy.name] = copy
            parsed = R.parse_cells(H.render_cells(single), result.partition)
            cell = parsed[0] if parsed else None
        if cell is None:
            cell = held.find_cell(part["cell"])
        rows.append(
            {
                "number": part["number"],
                "description": result.library.parts[part["number"]].description,
                "symbol": symbol,
                "cell": cell,
                "padstacks": merged,
            }
        )
    return rows


def input_schema() -> dict[str, Any]:
    """The parts file, as `reference --command "library add"` shows it."""
    toleranced = {
        "description": "mm: a number, [min, max], or {nominal, tolerance}",
        "oneOf": [
            {"type": "number"},
            {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            {
                "type": "object",
                "required": ["nominal"],
                "properties": {"nominal": {"type": "number"}, "tolerance": {"type": "number"}},
            },
        ],
    }
    pins = {
        "type": "array",
        "description": "[number, name] pairs from the top (left, right) or the left (top, "
        'bottom); ["", ""] leaves a gap between pin groups',
        "items": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
    }
    land = {
        "type": "object",
        "required": ["pin", "x", "y", "width"],
        "properties": {
            "pin": {"type": "string", "description": "the pin number; lands may share one"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "width": {"type": "number"},
            "height": {"type": "number"},
            "shape": {"enum": ["rect", "round", "oblong"]},
            "drill": {
                "description": "a hole in the land, mm: a diameter, or a slot's [width, height]",
                "oneOf": [
                    {"type": "number"},
                    {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                ],
            },
            "plated": {"type": "boolean", "description": "a plated hole (true by default)"},
        },
    }
    hole = {
        "type": "object",
        "required": ["x", "y", "drill"],
        "description": "a hole that is no pin, such as a locating peg's",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "drill": land["properties"]["drill"],
            "plated": {"type": "boolean", "description": "false by default"},
        },
    }
    ipc = {
        "type": "object",
        "required": ["family"],
        "properties": {
            "family": {"enum": list(ipc7351.FAMILIES)},
            "density": {"enum": list(ipc7351.DENSITIES), "description": "IPC level (N)"},
            "size": {"enum": sorted(ipc7351.CHIP_SIZES), "description": "chip: EIA code"},
            "pins": {"type": "integer"},
            "positions": {"type": "integer", "description": "lead positions, with omit"},
            "omit": {"type": "array", "items": {"type": "integer"}},
            "pitch": {"type": "number"},
            "rows": {"enum": [1, 2, 4]},
            "pins_x": {"type": "integer"},
            "pins_y": {"type": "integer"},
            "span": {**toleranced, "description": "lead span, toe to toe"},
            "span_y": toleranced,
            "terminal": {**toleranced, "description": "foot (terminal) length"},
            "lead_width": toleranced,
            "body_length": toleranced,
            "body_width": toleranced,
            "body": {"type": "array", "minItems": 2, "maxItems": 2, "items": toleranced},
            "height": {"type": "number", "description": "seated height, mm"},
            "exposed_pad": {
                "type": "object",
                "required": ["size"],
                "properties": {
                    "size": {"type": "array", "items": {"type": "number"}},
                    "pin": {"type": "string"},
                    "paste": {"type": "number", "description": "share under paste (0.7)"},
                },
            },
            "pull_back": {"type": "number"},
            "row_pitch": {"type": "number"},
            "lead": toleranced,
            "numbering": {"enum": ["row", "around", "zigzag"]},
            "polarized": {"type": "boolean"},
            "corners": {"enum": ["round", "square"]},
            "extra_pads": {"type": "array", "items": land},
            "name": {"type": "string"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["parts"],
        "properties": {
            "partition": {"type": "string", "description": "central-library partition"},
            "parts": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_PARTS,
                "items": {
                    "type": "object",
                    "required": ["number", "prefix", "symbol", "footprint"],
                    "properties": {
                        "number": {"type": "string", "description": "the part number"},
                        "description": {"type": "string"},
                        "prefix": {"type": "string", "description": "refdes letters, as U"},
                        "type": {"type": "string"},
                        "value": {"type": "string"},
                        "properties": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                        "symbol": {
                            "oneOf": [
                                {
                                    "type": "object",
                                    "properties": {
                                        "kind": {"const": "box"},
                                        "name": {"type": "string"},
                                        "left": pins,
                                        "right": pins,
                                        "top": pins,
                                        "bottom": pins,
                                        "pintypes": {
                                            "type": "object",
                                            "additionalProperties": {"enum": sorted(S.PIN_TYPES)},
                                        },
                                    },
                                },
                                {
                                    "type": "object",
                                    "required": ["kind"],
                                    "properties": {"kind": {"enum": sorted(L.BUILTIN)}},
                                },
                            ]
                        },
                        "footprint": {
                            "oneOf": [
                                ipc,
                                {
                                    "type": "object",
                                    "required": ["pads", "height"],
                                    "properties": {
                                        "pads": {"type": "array", "items": land},
                                        "holes": {"type": "array", "items": hole},
                                        "body": {"type": "array"},
                                        "height": {"type": "number"},
                                        "name": {"type": "string"},
                                        "pin_one": {"type": "boolean"},
                                    },
                                },
                                {"type": "object", "required": ["package"]},
                                {"type": "object", "required": ["cell"]},
                            ]
                        },
                        "pinmap": {
                            "type": "object",
                            "description": "symbol pin number -> cell pin number",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                },
            },
        },
    }
