"""Reading the converters' HKP exports and the symbol files, and checking them."""

from __future__ import annotations

from pathlib import Path

import pytest

from xpedition_cli import library_read as R

PARTS = """!.LCID 2052
.Filetype ASCII_PDB
.Units mm

.Number "LDO-1"
\t..Name "LDO-1"
\t\t...Default
\t..Desc "regulator, 5 pins"
\t..RefPrefix "U"
\t..TopCell "SOT-5"
\t..Modified\t1790631272 ! 2026/9/29 5:34
\t..Prop "Type",\t"IC",\t"Text"
\t..Prop "Value",\t"0",\t"Text"
\t..SwapGroup\t"G1"
\t\t...SwapID\t"P0"
\t..Symbol\t"Parts:LDO_1"
\t\t...Default
\t\t...Symbol_SwapGroup\t"G1"
\t\t\t....PinName\t"IN"
\t\t\t....PinName\t"GND"
\t..Slots
\t\t...Slot_SwapGroup\t"G1"
\t\t\t....SlotID\t1
\t\t\t....SwapCode\t0
\t\t\t....PinNumber\t"1"
\t\t\t....PinNumber\t"2"

.Number "DUAL-OP"
\t..Desc "two amplifiers"
\t..RefPrefix "U"
\t..TopCell "SO-8"
\t..Symbol\t"Parts:OPAMP"
\t\t...Symbol_SwapGroup\t"A"
\t\t\t....PinName\t"OUT"
\t..Slots
\t\t...Slot_SwapGroup\t"A"
\t\t\t....SlotID\t1
\t\t\t....PinNumber\t"1"
\t\t...Slot_SwapGroup\t"A"
\t\t\t....SlotID\t2
\t\t\t....PinNumber\t"7"
"""

CELLS = """.FILETYPE CELL_LIBRARY
.UNITS MM

.PACKAGE_CELL "SOT-5"
 ..PACKAGE_GROUP IC_SOIC
 ..MOUNT_TYPE SURFACE
 ..NUMBER_LAYERS 4
 ..DESCRIPTION "five pins"
 ..TIMESTAMP "1790631272" ! "2026/09/29@05:34:32"
 ..PIN "1"
  ...XY (-1.15,0.95)
  ...PADSTACK "SMD-A"
  ...ROTATION 0
 ..PIN "2"
  ...XY (-1.15, -0.95)
  ...PADSTACK "SMD-A"
  ...ROTATION 90
 ..PLACEMENT_OUTLINE
  ...HEIGHT 1.45
  ...POLYLINE_PATH
   ....WIDTH 0
   ....XY (-2, -1.8)
          (2, -1.8)
          (2, 1.8)
 ..SILKSCREEN_OUTLINE
  ...CIRCLE_PATH
   ....WIDTH 0.12
   ....XY (0, 0)
   ....RADIUS 0.3
 ..TEXT "Ref Des"
  ...TEXT_TYPE REF_DES
  ...DISPLAY_ATTR
   ....XY (0,2.2)
   ....TEXT_LYR SILKSCREEN_MNT_LYR
   ....HEIGHT 0.8

.MECHANICAL_CELL "HOLE"
 ..NUMBER_LAYERS 4
 ..MOUNTING_HOLE
  ...PADSTACK "MH-2"
  ...XY (0, 0)
"""

PADSTACKS = """.FILETYPE PADSTACK_LIBRARY
.UNITS MM

.PAD "RECT1X0.5"
..RECTANGLE
...WIDTH 1
...HEIGHT 0.5
..OFFSET (0, 0)

.PAD "RND2"
..ROUND
...DIAMETER 2

.HOLE "C1-NONPLATED"
..ROUND
...DIAMETER 1
..HOLE_OPTIONS NON_PLATED DRILLED USER_GENERATED_NAME

.PADSTACK "SMD-A"
..PADSTACK_TYPE PIN_SMD
..TECHNOLOGY "(Default)"
...TOP_PAD "RECT1X0.5"
...TOP_SOLDERMASK_PAD "RND2"

.PADSTACK "MH-2"
..PADSTACK_TYPE MOUNTING_HOLE
..TECHNOLOGY "(Default)"
...HOLE_NAME "C1-NONPLATED"
....OFFSET (0, 0)
"""

SYMBOL_53 = """V 53
K 1 LDO_1.1
Y 1
D -40 40 40 -40
U -40 30 8 0 8 3 REFDES=
U -40 -30 8 0 4 0 DEVICE=LDO
l 5 -20 -20 20 -20 20 20 -20 20 -20 -20
P 10 -40 10 -20 10 0 3 0
L -20 10 6 0 3 0 0 0 IN
A -24 12 5 0 3 3 #=1
A 0 10 10 0 3 0 PINTYPE=POWER
P 11 0 -40 0 -20 0 3 0
L 0 -20 6 0 3 0 0 0 GND
A 2 -24 5 0 3 3 #=2
A 0 10 10 0 3 0 PINTYPE=GROUND
E
"""


def test_records_nest_by_dots_and_take_continuation_lines() -> None:
    records = R.parse_records(CELLS)
    cell = next(r for r in records if r.keyword == "PACKAGE_CELL")
    outline = cell.first("PLACEMENT_OUTLINE")
    assert outline is not None and outline.value("HEIGHT") == 1.45
    path = outline.first("POLYLINE_PATH")
    assert path is not None and path.first("XY").points() == [(-2, -1.8), (2, -1.8), (2, 1.8)]
    assert cell.value("TIMESTAMP") == "1790631272"  # the comment after it is gone


def test_parts_map_symbol_pins_to_cell_pins_per_slot() -> None:
    parts = R.parse_parts(PARTS, "Parts")
    ldo, dual = parts
    assert ldo["number"] == "LDO-1" and ldo["partition"] == "Parts"
    assert ldo["cell"] == "SOT-5" and ldo["type"] == "IC" and ldo["properties"]["Value"] == "0"
    assert [(p["name"], p["number"]) for p in ldo["pins"]] == [("IN", "1"), ("GND", "2")]
    assert dual["slots"] == 2
    assert [(p["slot"], p["name"], p["number"]) for p in dual["pins"]] == [
        (1, "OUT", "1"),
        (2, "OUT", "7"),
    ]


def test_cells_carry_pins_outlines_texts_and_holes() -> None:
    cell, hole = R.parse_cells(CELLS, "Parts")
    assert cell["group"] == "IC_SOIC" and cell["height"] == 1.45 and cell["layers"] == 4
    assert cell["pins"][1] == {
        "number": "2",
        "x": -1.15,
        "y": -0.95,
        "padstack": "SMD-A",
        "rotation": 90.0,
    }
    assert cell["silkscreen"][0]["kind"] == "circle" and cell["silkscreen"][0]["radius"] == 0.3
    assert cell["texts"][0]["layer"] == "SILKSCREEN_MNT_LYR"
    assert hole["kind"] == "mechanical" and hole["holes"][0]["padstack"] == "MH-2"


def test_padstacks_resolve_through_their_pads_and_holes() -> None:
    library = R.parse_padstacks(PADSTACKS)
    smd = R.padstack_geometry(library, "SMD-A")
    assert smd["pad"] == {"shape": "RECTANGLE", "width": 1.0, "height": 0.5}
    assert smd["mask"]["width"] == 2.0 and smd["hole"] is None
    hole = R.padstack_geometry(library, "MH-2")
    assert hole["hole"]["plated"] is False and hole["hole"]["width"] == 1.0
    assert R.padstack_geometry(library, "nope") is None


def test_symbols_read_pins_in_both_file_versions() -> None:
    symbol = R.parse_symbol(SYMBOL_53, "Parts", "LDO_1")
    assert symbol["kind"] == "part" and symbol["device"] == "LDO"
    first, ground = symbol["pins"]
    assert (first["number"], first["name"], first["type"], first["side"]) == (
        "1",
        "IN",
        "POWER",
        "left",
    )
    assert ground["side"] == "bottom" and symbol["bbox"] == [-40.0, -40.0, 20.0, 20.0]
    scaled = SYMBOL_53.replace("V 53", "V 54")
    for number in ("-40", "-20", "40", "20", "10", "-24", "12", "-30", "30"):
        scaled = scaled.replace(f" {number} ", f" {int(number) * 25400} ")
    assert R.parse_symbol(scaled)["pins"][0]["x"] == -40.0


def test_the_highest_symbol_version_is_the_current_one(tmp_path: Path) -> None:
    folder = tmp_path / "Parts" / "sym"
    folder.mkdir(parents=True)
    (folder / "LDO_1.1").write_text("V 53\nE\n", encoding="utf-8")
    (folder / "LDO_1.3").write_text(SYMBOL_53, encoding="utf-8")
    (folder / "notes.txt").write_text("", encoding="utf-8")
    assert [(p, n, f.name) for p, n, f in R.symbol_files(tmp_path)] == [
        ("Parts", "LDO_1", "LDO_1.3")
    ]
    symbol = R.read_symbol(tmp_path, "Parts", "LDO_1")
    assert symbol is not None and symbol["version"] == 3 and len(symbol["pins"]) == 2


def _library() -> R.Library:
    return R.Library(
        parts=R.parse_parts(PARTS, "Parts"),
        cells=R.parse_cells(CELLS, "Parts"),
        padstacks=R.parse_padstacks(PADSTACKS),
        symbols=[R.parse_symbol(SYMBOL_53, "Parts", "LDO_1")],
    )


def test_check_names_each_broken_link() -> None:
    findings = R.check(_library())
    rules = {(f["rule"], f["item"]) for f in findings}
    # DUAL-OP names a cell and a symbol the library does not hold
    assert ("part_cell_missing", "part DUAL-OP") in rules
    assert ("part_symbol_missing", "part DUAL-OP") in rules
    assert findings[0]["severity"] == "high"
    # LDO-1 is whole: its cell's two pins and its symbol's two pins are all mapped
    assert not [f for f in findings if f["item"] == "part LDO-1"]


def test_check_finds_unmapped_pins_repeated_names_and_numbers_in_two_partitions() -> None:
    library = _library()
    library.parts[0]["pins"] = library.parts[0]["pins"][:1]
    library.parts.append({**library.parts[0], "partition": "Other"})
    library.symbols[0]["pins"][1]["name"] = "IN"
    rules = {f["rule"] for f in R.check(library)}
    assert {"cell_pad_unmapped", "symbol_pin_names_repeat", "part_number_repeats"} <= rules
    only = R.check(library, ["other"])
    assert {f["item"] for f in only} <= {"part LDO-1"}


@pytest.mark.parametrize("line", ["!.LCID 2052", "   ", "! a comment"])
def test_comment_and_blank_lines_are_skipped(line: str) -> None:
    assert R.parse_records(line) == []
