"""Reading the converters' HKP exports and the symbol files, and checking them."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xpedition_cli.adapter.plan import PlanError
from xpedition_cli.adapter.plan import library_read as R

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


def _library() -> R.Library:
    return R.Library(
        parts=R.parse_parts(PARTS, "Parts"),
        cells=R.parse_cells(CELLS, "Parts"),
        padstacks=R.parse_padstacks(PADSTACKS),
        symbols=[R.parse_symbol(SYMBOL_53, "Parts", "LDO_1")],
    )


class LibraryReadTest(unittest.TestCase):
    def tmp_dir(self) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name)

    def test_records_nest_by_dots_and_take_continuation_lines(self):
        records = R.parse_records(CELLS)
        cell = next(r for r in records if r.keyword == "PACKAGE_CELL")
        outline = cell.first("PLACEMENT_OUTLINE")
        self.assertIsNotNone(outline)
        self.assertEqual(outline.value("HEIGHT"), 1.45)
        path = outline.first("POLYLINE_PATH")
        self.assertIsNotNone(path)
        self.assertEqual(path.first("XY").points(), [(-2, -1.8), (2, -1.8), (2, 1.8)])
        self.assertEqual(cell.value("TIMESTAMP"), "1790631272")  # the comment after it is gone

    def test_parts_map_symbol_pins_to_cell_pins_per_slot(self):
        parts = R.parse_parts(PARTS, "Parts")
        ldo, dual = parts
        self.assertEqual(ldo["number"], "LDO-1")
        self.assertEqual(ldo["partition"], "Parts")
        self.assertEqual(ldo["cell"], "SOT-5")
        self.assertEqual(ldo["type"], "IC")
        self.assertEqual(ldo["properties"]["Value"], "0")
        self.assertEqual([(p["name"], p["number"]) for p in ldo["pins"]], [("IN", "1"), ("GND", "2")])
        self.assertEqual(dual["slots"], 2)
        self.assertEqual([(p["slot"], p["name"], p["number"]) for p in dual["pins"]], [
            (1, "OUT", "1"),
            (2, "OUT", "7"),
        ])

    def test_cells_carry_pins_outlines_texts_and_holes(self):
        cell, hole = R.parse_cells(CELLS, "Parts")
        self.assertEqual(cell["group"], "IC_SOIC")
        self.assertEqual(cell["height"], 1.45)
        self.assertEqual(cell["layers"], 4)
        self.assertEqual(cell["pins"][1], {
            "number": "2",
            "x": -1.15,
            "y": -0.95,
            "padstack": "SMD-A",
            "rotation": 90.0,
        })
        self.assertEqual(cell["silkscreen"][0]["kind"], "circle")
        self.assertEqual(cell["silkscreen"][0]["radius"], 0.3)
        self.assertEqual(cell["texts"][0]["layer"], "SILKSCREEN_MNT_LYR")
        self.assertEqual(hole["kind"], "mechanical")
        self.assertEqual(hole["holes"][0]["padstack"], "MH-2")

    def test_padstacks_resolve_through_their_pads_and_holes(self):
        library = R.parse_padstacks(PADSTACKS)
        smd = R.padstack_geometry(library, "SMD-A")
        self.assertEqual(smd["pad"], {"shape": "RECTANGLE", "width": 1.0, "height": 0.5})
        self.assertEqual(smd["mask"]["width"], 2.0)
        self.assertIsNone(smd["hole"])
        hole = R.padstack_geometry(library, "MH-2")
        self.assertIs(hole["hole"]["plated"], False)
        self.assertEqual(hole["hole"]["width"], 1.0)
        self.assertIsNone(R.padstack_geometry(library, "nope"))

    def test_symbols_read_pins_in_both_file_versions(self):
        symbol = R.parse_symbol(SYMBOL_53, "Parts", "LDO_1")
        self.assertEqual(symbol["kind"], "part")
        self.assertEqual(symbol["device"], "LDO")
        first, ground = symbol["pins"]
        self.assertEqual((first["number"], first["name"], first["type"], first["side"]), (
            "1",
            "IN",
            "POWER",
            "left",
        ))
        self.assertEqual(ground["side"], "bottom")
        self.assertEqual(symbol["bbox"], [-40.0, -40.0, 20.0, 20.0])
        scaled = SYMBOL_53.replace("V 53", "V 54")
        for number in ("-40", "-20", "40", "20", "10", "-24", "12", "-30", "30"):
            scaled = scaled.replace(f" {number} ", f" {int(number) * 25400} ")
        self.assertEqual(R.parse_symbol(scaled)["pins"][0]["x"], -40.0)

    def test_the_highest_symbol_version_is_the_current_one(self):
        tmp_path = self.tmp_dir()
        folder = tmp_path / "Parts" / "sym"
        folder.mkdir(parents=True)
        (folder / "LDO_1.1").write_text("V 53\nE\n", encoding="utf-8")
        (folder / "LDO_1.3").write_text(SYMBOL_53, encoding="utf-8")
        (folder / "notes.txt").write_text("", encoding="utf-8")
        self.assertEqual([(p, n, f.name) for p, n, f in R.symbol_files(tmp_path)], [
            ("Parts", "LDO_1", "LDO_1.3")
        ])
        symbol = R.read_symbol(tmp_path, "Parts", "LDO_1")
        self.assertIsNotNone(symbol)
        self.assertEqual(symbol["version"], 3)
        self.assertEqual(len(symbol["pins"]), 2)

    def test_check_names_each_broken_link(self):
        findings = R.check(_library())
        rules = {(f["rule"], f["item"]) for f in findings}
        # DUAL-OP names a cell and a symbol the library does not hold
        self.assertIn(("part_cell_missing", "part DUAL-OP"), rules)
        self.assertIn(("part_symbol_missing", "part DUAL-OP"), rules)
        self.assertEqual(findings[0]["severity"], "high")
        # LDO-1 is whole: its cell's two pins and its symbol's two pins are all mapped
        self.assertFalse([f for f in findings if f["item"] == "part LDO-1"])

    def test_check_finds_unmapped_pins_repeated_names_and_numbers_in_two_partitions(self):
        library = _library()
        library.parts[0]["pins"] = library.parts[0]["pins"][:1]
        library.parts.append({**library.parts[0], "partition": "Other"})
        library.symbols[0]["pins"][1]["name"] = "IN"
        rules = {f["rule"] for f in R.check(library)}
        self.assertLessEqual({"cell_pad_unmapped", "symbol_pin_names_repeat", "part_number_repeats"}, rules)
        only = R.check(library, ["other"])
        self.assertLessEqual({f["item"] for f in only}, {"part LDO-1"})

    def test_comment_and_blank_lines_are_skipped(self):
        for line in ["!.LCID 2052", "   ", "! a comment"]:
            with self.subTest(line=line):
                self.assertEqual(R.parse_records(line), [])

    def test_check_finds_silkscreen_over_a_land_the_way_the_pin_turns(self):
        """Pin 1's pad is 1 x 0.5 mm; pin 2's is the same turned 90 degrees, so 0.5 mm
    wide: a line 0.4 mm right of its centre misses it, and would not if the turn were
    ignored."""
        library = _library()
        cell = next(c for c in library.cells if c["pins"])
        cell["silkscreen"] = [
            {"kind": "path", "points": [[-1.4, 0.95], [-0.9, 0.95]], "width": 0.12},
            {"kind": "path", "points": [[-0.75, -1.5], [-0.75, -0.5]], "width": 0.12},
        ]
        self.assertEqual(R.silk_over_lands(cell, library), ["1"])
        found = [f for f in R.check(library) if f["rule"] == "silk_over_land"]
        self.assertTrue(found)
        self.assertEqual(found[0]["pins"], ["1"])
        self.assertEqual(found[0]["severity"], "low")
        cell["silkscreen"] = cell["silkscreen"][1:]
        self.assertFalse([f for f in R.check(library) if f["rule"] == "silk_over_land"])


class SymbolCaseTest(unittest.TestCase):
    """A symbol reference names one symbol: its exact identity first, else a unique match
    that differs only in ASCII case; never one of several."""

    def candidates(self, *names: tuple[str, str]) -> list[dict]:
        return [{"partition": partition, "name": name} for partition, name in names]

    def test_exact_identity_has_priority_over_a_case_variant(self):
        rows = self.candidates(("Parts", "ldo_1"), ("Parts", "LDO_1"))
        self.assertIs(R._select_symbol(rows, "Parts", "LDO_1"), rows[1])

    def test_a_unique_case_variant_is_the_symbol_with_its_own_spelling(self):
        rows = self.candidates(("parts", "Ldo_1"), ("Parts", "OTHER"))
        self.assertIs(R._select_symbol(rows, "Parts", "LDO_1"), rows[0])
        self.assertIsNone(R._select_symbol(rows, "Parts", "MISSING"))

    def test_several_case_variants_or_repeated_identities_are_refused(self):
        for rows, reason in (
            (self.candidates(("Parts", "ldo_1"), ("Parts", "Ldo_1")), "ambiguous_symbol_case_match"),
            (self.candidates(("Parts", "LDO_1"), ("Parts", "LDO_1")), "duplicate_exact_symbol_identity"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(PlanError) as caught:
                    R._select_symbol(rows, "Parts", "LDO_1")
                self.assertEqual(caught.exception.code, "E_VALIDATION")
                self.assertEqual(caught.exception.details["reason"], reason)
                self.assertEqual(caught.exception.details["matched_candidate_count"], 2)

    def test_non_ascii_names_match_only_exactly(self):
        rows = self.candidates(("Parts", "ÄBC"))
        self.assertIsNone(R._select_symbol(rows, "Parts", "äbc"))
        self.assertIs(R._select_symbol(rows, "Parts", "ÄBC"), rows[0])

    def test_a_library_finds_a_symbol_by_partition_and_name(self):
        library = R.Library(symbols=[R.parse_symbol(SYMBOL_53, "Parts", "LDO_1")])
        self.assertEqual(library.find_symbol("Parts:ldo_1")["name"], "LDO_1")
        self.assertEqual(library.find_symbol("LDO_1")["partition"], "Parts")
        self.assertIsNone(library.find_symbol("Other:LDO_1"))
