"""`library import`: parts of another Xpedition central library, with what they use.

A part names its symbols (`..Symbol "Partition:Name"`) and its cell (`..TopCell`); a
cell names its padstacks, a padstack its pads and its hole. The plan follows those
references through the source library's exports and takes each item's text as the
source's own converters wrote it, so nothing is regenerated. Every item is then met
with the target library's:

- `add`: the target lacks it;
- `keep`: the target holds it with the same content -- compared as read, not by the
  converters' timestamps or swap-group names -- and it is left alone;
- `replace`: the target holds it with other content; the confirm needs --dangerous.

Every item keeps its source partition, so a part imported again later meets itself.
A part number or a cell name the target holds in another partition is refused: the
packager would find two parts of one number, Layout two cells of one name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import library_read as R

MAX_PARTS = 200
# a partition becomes a file and a folder name; Designer loads no new symbol file
# from a folder whose path has other than ASCII characters
PARTITION = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9 _.\-]{0,62}[A-Za-z0-9_])?$")
_UNQUOTABLE = re.compile(r'["\x00-\x1f\x7f]')


class LibraryImportError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class ImportPlan:
    parts: list[dict[str, Any]] = field(default_factory=list)
    symbols: list[dict[str, Any]] = field(default_factory=list)
    cells: list[dict[str, Any]] = field(default_factory=list)
    padstacks: list[dict[str, Any]] = field(default_factory=list)
    replaces: list[str] = field(default_factory=list)
    # what the adapter imports: one unit a partition, the padstack items in one text
    units: list[dict[str, Any]] = field(default_factory=list)
    padstack_text: str = ""
    # the cell partitions the imported parts use, for the design's cell list
    cell_partitions: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.units and not self.padstack_text

    def material(self) -> str:
        """Everything the confirm would send, for the token's digest."""
        pieces = [self.padstack_text]
        for unit in self.units:
            pieces += [unit["partition"], unit["parts"], unit["cells"]]
            pieces += [f"{name}\n{text}" for name, text in sorted(unit["symbols"].items())]
        return "\x00".join(pieces)


def numbers(values: list[str]) -> list[str]:
    """The part numbers asked for, once each, in order."""
    result: list[str] = []
    for value in values:
        number = " ".join(str(value).split())
        if number and number not in result:
            result.append(number)
    if not result:
        raise LibraryImportError("E_USAGE", "name the parts to import: --parts N1,N2")
    if len(result) > MAX_PARTS:
        raise LibraryImportError(
            "E_VALIDATION", f"at most {MAX_PARTS} parts an import; {len(result)} were named"
        )
    return result


class _Items:
    """The items of one library's exports, found by kind, keyword and name."""

    def __init__(self, library: R.Library) -> None:
        self.library = library
        self._blocks: dict[tuple[str, str], dict[tuple[str, str], str]] = {}

    def blocks(self, kind: str, partition: str) -> dict[tuple[str, str], str]:
        key = (kind, partition)
        if key not in self._blocks:
            _header, items = R.hkp_blocks(self.library.texts.get(key, ""))
            self._blocks[key] = {(keyword, name): block for keyword, name, block in items}
        return self._blocks[key]

    def block(self, kind: str, partition: str, name: str) -> tuple[str, str] | None:
        """`(keyword, text)` of the item `name`, whatever its keyword."""
        for (keyword, found), text in self.blocks(kind, partition).items():
            if found == name:
                return keyword, text
        return None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _without(item: dict[str, Any] | None, *keys: str) -> dict[str, Any] | None:
    """The content of a record as read, without `keys`, its lists in one order."""
    if item is None:
        return None
    return R.canonical({key: value for key, value in item.items() if key not in keys})


def _symbol_content(symbol: dict[str, Any] | None) -> dict[str, Any] | None:
    return _without(symbol, "partition", "path", "version")


def plan(source: R.Library, target: R.Library, wanted: list[str]) -> ImportPlan:
    """What importing the parts `wanted` from `source` into `target` adds, keeps and
    replaces, and the texts that do it."""
    items = _Items(source)
    result = ImportPlan()

    # -- the parts --------------------------------------------------------------------
    chosen: list[dict[str, Any]] = []
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for number in wanted:
        rows = source.find_parts(number)
        places = sorted({row["partition"] for row in rows})
        if not rows:
            missing.append(number)
        elif len(places) > 1:
            ambiguous[number] = places
        else:
            chosen.append(rows[0])
    if missing:
        raise LibraryImportError(
            "E_NOT_FOUND",
            f"the source library has no part {', '.join(missing[:10])}",
            {"parts": missing, "hint": "library list --library <source> --query <number>"},
        )
    if ambiguous:
        raise LibraryImportError(
            "E_VALIDATION",
            "the source library holds a part number in two partitions, and the packager "
            "would take either",
            {"parts": ambiguous},
        )

    part_blocks: dict[str, str] = {}
    unreadable: list[str] = []
    for part in chosen:
        found = items.block("parts", part["partition"], part["number"])
        if found is None:
            unreadable.append(part["number"])
        else:
            part_blocks[part["number"]] = found[1]
    if unreadable:
        raise LibraryImportError(
            "E_VALIDATION",
            "the source library's export does not show these parts as text",
            {"parts": unreadable},
        )

    # -- what the parts use -------------------------------------------------------------
    absent: dict[str, list[str]] = {"symbols": [], "cells": [], "padstacks": []}
    symbol_refs = _unique(
        [ref for block in part_blocks.values() for ref in R.SYMBOL_REFERENCE.findall(block)]
    )
    symbols: dict[str, dict[str, Any]] = {}
    symbol_texts: dict[str, str] = {}
    for reference in symbol_refs:
        symbol = source.find_symbol(reference) if ":" in reference else None
        if symbol is None or not symbol.get("path"):
            absent["symbols"].append(reference)
            continue
        try:
            text = R.decode_text(Path(str(symbol["path"])).read_bytes())
        except OSError:
            absent["symbols"].append(reference)
            continue
        symbols[reference] = symbol
        symbol_texts[reference] = text
    cell_names = _unique(
        [name for block in part_blocks.values() for name in R.CELL_REFERENCE.findall(block)]
    )
    cells: dict[str, dict[str, Any]] = {}
    cell_blocks: dict[str, tuple[str, str]] = {}
    for name in cell_names:
        cell = source.find_cell(name)
        found = items.block("cells", cell["partition"], name) if cell else None
        if cell is None or found is None:
            absent["cells"].append(name)
            continue
        cells[name] = cell
        cell_blocks[name] = found
    if any(absent.values()):
        raise LibraryImportError(
            "E_VALIDATION",
            "the source library's parts use items the library does not hold",
            {"missing": {kind: names for kind, names in absent.items() if names}},
        )
    names = [part["number"] for part in chosen]
    for label, values in (
        ("part number", names),
        ("cell", cell_names),
        ("symbol", symbol_refs),
    ):
        bad = [value for value in values if _UNQUOTABLE.search(value)]
        if bad:
            raise LibraryImportError(
                "E_VALIDATION", f"a {label} has a quote or control character", {"items": bad}
            )
    partitions = sorted(
        {part["partition"] for part in chosen}
        | {cell["partition"] for cell in cells.values()}
        | {symbol["partition"] for symbol in symbols.values()}
    )
    refused = [name for name in partitions if not PARTITION.fullmatch(name)]
    if refused:
        raise LibraryImportError(
            "E_VALIDATION",
            "a partition name must be ASCII letters, digits, spaces and _ . -: it becomes "
            "a file and a folder name, and Designer loads no new symbol from a folder whose "
            "name has other characters",
            {"partitions": refused},
        )

    # -- met with the target ------------------------------------------------------------
    conflicts: list[str] = []
    new_parts: dict[str, list[str]] = {}
    replaced_parts: set[str] = set()
    for part in chosen:
        number, partition = part["number"], part["partition"]
        held = target.find_parts(number)
        elsewhere = sorted({row["partition"] for row in held} - {partition})
        if elsewhere:
            conflicts.append(f"part {number} is in partition {', '.join(elsewhere)} of the library")
            continue
        mine = next((row for row in held if row["partition"] == partition), None)
        if mine is None:
            action = "add"
        elif R.part_content(mine) == R.part_content(part):
            action = "keep"
        else:
            action = "replace"
            result.replaces.append(f"part {number}")
            replaced_parts.add(partition)
        result.parts.append({"number": number, "partition": partition, "action": action})
        if action != "keep":
            new_parts.setdefault(partition, []).append(number)

    new_symbols: dict[str, dict[str, str]] = {}
    symbol_files: dict[str, dict[str, str]] = {}
    for reference, symbol in symbols.items():
        held_symbol = target.find_symbol(reference)
        if held_symbol is None:
            action = "add"
        elif _symbol_content(held_symbol) == _symbol_content(symbol):
            action = "keep"
        else:
            action = "replace"
            result.replaces.append(f"symbol {reference}")
        result.symbols.append(
            {
                "reference": reference,
                "partition": symbol["partition"],
                "name": symbol["name"],
                "action": action,
            }
        )
        if action != "keep":
            new_symbols.setdefault(symbol["partition"], {})[symbol["name"]] = symbol_texts[
                reference
            ]
            # the file itself goes across, byte for byte, whatever its code page
            symbol_files.setdefault(symbol["partition"], {})[symbol["name"]] = str(symbol["path"])

    new_cells: dict[str, set[tuple[str, str]]] = {}
    used_partitions: set[str] = set()
    imported_blocks: list[str] = []
    for name, cell in cells.items():
        partition = cell["partition"]
        held_cell = target.find_cell(name)
        entry: dict[str, Any] = {"name": name, "partition": partition, "action": "add"}
        if held_cell is not None:
            if _without(held_cell, "partition") == _without(cell, "partition"):
                partition = held_cell["partition"]
                entry.update(partition=partition, action="keep")
            elif held_cell["partition"] != partition:
                conflicts.append(
                    f"cell {name} is in partition {held_cell['partition']} of the library "
                    "with other content"
                )
                continue
            else:
                entry.update(
                    action="replace",
                    used_by=sorted(p["number"] for p in target.parts if p["cell"] == name),
                )
                result.replaces.append(f"cell {name}")
        action = entry["action"]
        result.cells.append(entry)
        used_partitions.add(partition)
        if action != "keep":
            keyword, text = cell_blocks[name]
            new_cells.setdefault(partition, set()).add((keyword, name))
            imported_blocks.append(text)
    if conflicts:
        raise LibraryImportError(
            "E_CONFLICT",
            "the library holds items of these names elsewhere; importing would leave two",
            {"conflicts": conflicts},
        )

    # -- the padstacks the imported cells use --------------------------------------------
    source_stacks = items.blocks("padstacks", "")
    held_stacks = target.padstacks
    wanted_stacks = _unique(
        [name for text in imported_blocks for name in R.PADSTACK_REFERENCE.findall(text)]
    )
    chosen_stacks: set[tuple[str, str]] = set()
    for name in wanted_stacks:
        text = source_stacks.get(("PADSTACK", name))
        if text is None:
            absent["padstacks"].append(name)
            continue
        references = [("PAD", "pads", r) for r in _unique(R.PAD_REFERENCE.findall(text))]
        references += [("HOLE", "holes", r) for r in _unique(R.HOLE_REFERENCE.findall(text))]
        lacking = [f"{k.lower()} {r}" for k, _t, r in references if (k, r) not in source_stacks]
        if lacking:
            absent["padstacks"] += lacking
            continue
        theirs = held_stacks["padstacks"].get(name)
        # the same padstack names the same pads and hole, and those are the same too
        same = theirs == source.padstacks["padstacks"].get(name) and all(
            held_stacks[table].get(r) == source.padstacks[table].get(r)
            for _k, table, r in references
        )
        action = "add" if theirs is None else ("keep" if same else "replace")
        if action == "replace":
            result.replaces.append(f"padstack {name}")
        result.padstacks.append({"name": name, "kind": "padstack", "action": action})
        if action == "keep":
            continue
        chosen_stacks.add(("PADSTACK", name))
        # the pads and the hole go along whether the library has them or not: the
        # padstack refers to them, and merging an identical one changes nothing
        for keyword, table, reference in references:
            if (keyword, reference) in chosen_stacks:
                continue
            chosen_stacks.add((keyword, reference))
            held = held_stacks[table].get(reference)
            ours = source.padstacks[table].get(reference)
            sub = "add" if held is None else ("keep" if held == ours else "replace")
            if sub == "replace":
                result.replaces.append(f"{keyword.lower()} {reference}")
            result.padstacks.append({"name": reference, "kind": keyword.lower(), "action": sub})
    if absent["padstacks"]:
        raise LibraryImportError(
            "E_VALIDATION",
            "the source library's cells use padstacks, pads or holes it does not hold",
            {"missing": {"padstacks": absent["padstacks"]}},
        )
    if chosen_stacks:
        result.padstack_text = R.hkp_subset(source.texts.get(("padstacks", ""), ""), chosen_stacks)

    # -- one unit a partition --------------------------------------------------------------
    for partition in sorted(set(new_parts) | set(new_cells) | set(new_symbols)):
        parts_text = ""
        if partition in new_parts:
            parts_text = R.hkp_subset(
                source.texts.get(("parts", partition), ""),
                {("Number", number) for number in new_parts[partition]},
            )
        cells_text = ""
        if partition in new_cells:
            cells_text = R.hkp_subset(
                source.texts.get(("cells", partition), ""), new_cells[partition]
            )
        result.units.append(
            {
                "partition": partition,
                "parts": parts_text,
                "cells": cells_text,
                "symbols": new_symbols.get(partition, {}),
                "symbol_files": symbol_files.get(partition, {}),
                # the parts converter keeps a part it holds unless told to replace it
                "replace": partition in replaced_parts,
            }
        )
    result.cell_partitions = sorted(used_partitions)
    return result
