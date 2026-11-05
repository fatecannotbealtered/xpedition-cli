"""Parts files: the symbol, footprint and pin map of each part, and what the library holds."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from xpedition_cli.adapter.plan import library_import
from xpedition_cli.adapter.plan import library_parts as P
from xpedition_cli.adapter.plan import library_read as R
from xpedition_cli.adapter.plan import schematic_layout as L


def _spec(*parts: dict, partition: str = "PartQuest") -> dict:
    return {"partition": partition, "parts": [copy.deepcopy(p) for p in parts]}


HERE = Path(__file__).resolve().parent
LIBRARY_PARTS = json.loads((Path(__file__).resolve().parent / "fixtures" / "library-parts.json").read_text(encoding="utf-8"))
RES, MCU = LIBRARY_PARTS["parts"]


def _held(spec: dict) -> R.Library:
    """What the library holds after importing `spec`: its texts parsed back."""
    plan = P.plan(spec)
    texts = plan.texts()
    return R.Library(
        parts=R.parse_parts(texts["parts"], spec["partition"]),
        cells=R.parse_cells(texts["cells"], spec["partition"]),
        padstacks=R.parse_padstacks(texts["padstacks"]),
        symbols=[
            R.parse_symbol(text, spec["partition"], name) for name, text in plan.symbols.items()
        ],
    )


class LibraryPartsTest(unittest.TestCase):
    def test_a_builtin_symbol_is_the_one_a_design_draws(self):
        plan = P.plan(_spec(RES))
        design_symbol = L._Library({}).symbol("RES")
        self.assertEqual(list(plan.symbols), [design_symbol.name])
        self.assertEqual(plan.parts[0]["symbol"], f"PartQuest:{design_symbol.name}")

    def test_the_texts_parse_back_to_the_parts_with_their_pin_maps(self):
        held = _held(_spec(RES, MCU))
        mcu = held.find_parts("MCU-8")[0]
        self.assertEqual(mcu["cell"], "SOIC127P600X175-8N")
        self.assertTrue(mcu["description"].startswith("micro"))
        self.assertEqual({row["name"]: row["number"] for row in mcu["pins"]}["SWCLK"], "5")
        resistor = held.find_parts("RES-10K")[0]
        self.assertEqual(resistor["properties"]["Value"], "10k")
        self.assertEqual(R.check(held), [])

    def test_identical_content_is_kept_and_other_content_is_a_replacement(self):
        held = _held(_spec(RES, MCU))
        plan = P.plan(_spec(RES), held)
        self.assertEqual(plan.actions["parts"], [{"number": "RES-10K", "action": "keep"}])
        self.assertEqual({a["action"] for a in plan.actions["cells"]}, {"keep"})
        self.assertEqual({a["action"] for a in plan.actions["padstacks"]}, {"keep"})
        self.assertEqual(plan.symbols, {})
        self.assertEqual(plan.texts()["cells"], "")
        self.assertEqual(plan.texts()["parts"], "")
        self.assertEqual(plan.replaces, [])
        changed = {**RES, "description": RES["description"] + " (rev B)"}
        plan = P.plan(_spec(changed), held)
        self.assertEqual(plan.actions["parts"], [{"number": "RES-10K", "action": "replace"}])
        self.assertEqual(plan.replaces, ["part RES-10K"])
        self.assertIn(".Number", plan.texts()["parts"])
        moved = copy.deepcopy(MCU)
        moved["number"] = "MCU-9"
        moved["footprint"]["pitch"] = 1.25
        plan = P.plan(_spec(moved), held)
        cells = {a["name"]: a for a in plan.actions["cells"]}
        self.assertEqual(cells["SOP125P600X175-8N"]["action"], "add")
        moved["footprint"]["name"] = "SOIC127P600X175-8N"
        plan = P.plan(_spec(moved), held)
        replaced = next(a for a in plan.actions["cells"] if a["name"] == "SOIC127P600X175-8N")
        self.assertEqual(replaced["action"], "replace")
        self.assertEqual(replaced["used_by"], ["MCU-8"])
        self.assertIn("cell SOIC127P600X175-8N", plan.replaces)

    def test_a_placeholder_from_library_build_is_marked_as_such(self):
        held = _held(_spec(RES))
        held.parts[0]["description"] = "RES 10k (placeholder part)"
        plan = P.plan(_spec(RES), held)
        self.assertEqual(plan.actions["parts"][0]["action"], "replace_placeholder")

    def test_the_symbol_and_cell_pins_must_match_unless_a_pinmap_says_how(self):
        fet = {
            "number": "FET-1",
            "prefix": "Q",
            "symbol": {"kind": "NMOS"},
            "footprint": {
                "pads": [
                    {"pin": "G", "x": -1, "y": 0, "width": 0.6},
                    {"pin": "S", "x": 1, "y": -0.5, "width": 0.6},
                    {"pin": "D", "x": 1, "y": 0.5, "width": 0.6},
                ],
                "height": 1,
            },
        }
        with self.assertRaisesRegex(P.PartsFileError, "do not match"):
            P.plan(_spec(fet))
        fet["pinmap"] = {"1": "G", "2": "S", "3": "D"}
        plan = P.plan(_spec(fet))
        self.assertEqual({row["name"]: row["cell_pin"] for row in plan.parts[0]["pins"]}, {
            "G": "G",
            "S": "S",
            "D": "D",
        })
        fet["pinmap"] = {"9": "G"}
        with self.assertRaisesRegex(P.PartsFileError, "pinmap names symbol pins"):
            P.plan(_spec(fet))

    def test_value_must_be_one_the_library_keeps(self):
        plan = P.plan(_spec({**RES, "value": "100nF"}))
        self.assertEqual(plan.library.parts["RES-10K"].properties["Value"], "100n")
        with self.assertRaisesRegex(P.PartsFileError, "stored as 0"):
            P.plan(_spec({**RES, "value": "10k 1%"}))
        plan = P.plan(_spec({**MCU}))
        self.assertNotIn("Value", plan.library.parts["MCU-8"].properties)

    def test_bad_parts_are_refused_with_the_reason(self):
        for change, message in [
            ({"number": ""}, "number is the part number"),
            ({"prefix": "U1"}, "prefix is the reference designator"),
            ({"symbol": {"kind": "SPRING"}}, "symbol kind"),
            ({"symbol": {"kind": "HOLE"}}, "no pins"),
            ({"description": 'say "hi"'}, "double quote"),
            ({"footprint": {"family": "chip", "size": "9999"}}, "size is one of"),
        ]:
            with self.subTest(change=change, message=message):
                with self.assertRaisesRegex(P.PartsFileError, message):
                    P.plan(_spec({**RES, **change}))

    def test_the_file_itself_is_checked(self):
        with self.assertRaisesRegex(P.PartsFileError, "non-empty"):
            P.validate({"parts": []})
        with self.assertRaisesRegex(P.PartsFileError, "repeat"):
            P.validate({"parts": [RES, RES]})
        with self.assertRaisesRegex(P.PartsFileError, "plain identifier"):
            P.validate({"partition": "My Parts", "parts": [RES]})
        with self.assertRaisesRegex(P.PartsFileError, "in partition PartQuest already"):
            P.plan(_spec(RES, partition="Other"), _held(_spec(RES)))

    def test_a_cell_the_library_holds_is_used_as_it_is(self):
        held = _held(_spec(MCU))
        other = {**MCU, "number": "MCU-8B", "footprint": {"cell": "SOIC127P600X175-8N"}}
        plan = P.plan(_spec(other, partition="More"), held)
        self.assertEqual(plan.actions["cells"], [
            {"name": "SOIC127P600X175-8N", "action": "use", "partition": "PartQuest"}
        ])
        self.assertEqual(plan.library.cell_partitions, {"PartQuest"})
        self.assertEqual(plan.texts()["cells"], "")
        with self.assertRaisesRegex(P.PartsFileError, "no cell"):
            P.plan(_spec({**other, "footprint": {"cell": "NOPE"}}), held)

    def test_views_are_what_the_import_would_read(self):
        views = P.views(P.plan(_spec(MCU)))
        view = views[0]
        self.assertEqual(view["number"], "MCU-8")
        self.assertEqual(len(view["cell"]["pins"]), 8)
        self.assertEqual(view["symbol"]["pins"][0]["name"], "VDD")
        stack = view["cell"]["pins"][0]["padstack"]
        self.assertEqual(R.padstack_geometry(view["padstacks"], stack)["pad"]["width"], 1.95)

    def test_the_input_schema_describes_the_file(self):
        schema = P.input_schema()
        item = schema["properties"]["parts"]["items"]
        self.assertEqual(item["required"], ["number", "prefix", "symbol", "footprint"])
        families = item["properties"]["footprint"]["oneOf"][0]["properties"]["family"]["enum"]
        self.assertIn("gullwing", families)
        self.assertIn("nolead", families)

    def test_a_held_cell_without_the_holes_is_not_the_same_cell(self):
        sys.path.insert(0, str(HERE))  # the sibling test module, however the tests are run
        from test_plan_ipc7351 import USB_LEGS

        part = {
            "number": "USB-C-TEST",
            "description": "USB-C receptacle",
            "prefix": "J",
            "symbol": {"left": [["A1", "VBUS"]], "right": [["S1", "SHIELD"]]},
            "footprint": USB_LEGS,
        }
        spec = {"partition": "PartQuest", "parts": [part]}
        held = copy.deepcopy(spec)
        held["parts"][0]["footprint"] = {**USB_LEGS, "holes": []}
        # the library holds the cell and padstacks the held spec made, not its part
        texts = P.plan(held).texts()
        library = R.Library(cells=R.parse_cells(texts["cells"], "PartQuest"), padstacks=R.parse_padstacks(texts["padstacks"]))
        plan = P.plan(spec, library)
        self.assertEqual({c["name"]: c["action"] for c in plan.actions["cells"]}["USB-LEGS"], "replace")

    def test_a_symbol_read_back_compares_by_what_it_draws(self):
        """`part create`'s read-back: the symbol the library holds against the one written."""
        plan = P.plan(_spec(MCU))
        [(name, text)] = plan.symbols.items()
        written = R.parse_symbol(text, "PartQuest", name)
        held = {**R.parse_symbol(text, "Other", name), "path": "D:/work/demo/SymbolLibs/x.3", "version": 3}
        self.assertEqual(library_import._symbol_content(written), library_import._symbol_content(held))
        moved = copy.deepcopy(held)
        moved["pins"][0]["x"] += 10
        self.assertNotEqual(library_import._symbol_content(written), library_import._symbol_content(moved))
        self.assertIsNone(library_import._symbol_content(None))

    def test_a_value_the_library_keeps_in_its_own_case_is_the_same_value(self):
        # the parts database keeps 10k as 10K: the part is the same, not a replacement
        held = _held(_spec(RES))
        for part in held.parts:
            part["properties"]["Value"] = part["properties"]["Value"].upper()
        plan = P.plan(_spec(RES), held)
        self.assertEqual(plan.actions["parts"], [{"number": "RES-10K", "action": "keep"}])
        self.assertEqual((R.value_key("10k"), R.value_key("1m")), (R.value_key("10K"), "0.001"))
        self.assertNotEqual(R.value_key("1m"), R.value_key("1M"))

    def test_a_held_cell_whose_outline_moved_is_not_the_same_cell(self):
        # the lands and holes the same, the body drawn elsewhere: another footprint, which
        # the add used to call the same and leave out
        held = _held(_spec(MCU))
        for cell in held.cells:
            for graphic in cell["assembly"]:
                graphic["points"] = [[x + 0.5, y] for x, y in graphic["points"]]
        plan = P.plan(_spec(MCU), held)
        actions = {a["name"]: a["action"] for a in plan.actions["cells"]}
        self.assertEqual(actions["SOIC127P600X175-8N"], "replace")
        self.assertEqual(P.plan(_spec(MCU), _held(_spec(MCU))).actions["cells"][0]["action"], "keep")


# -- resizing the pads of a cell the library holds ------------------------------------

RECT_RES = copy.deepcopy(RES)
RECT_RES["footprint"] = {
    "name": "RECT_FIXTURE",
    "height": 0.6,
    "pads": [
        {"pin": str(i), "x": x, "y": 0, "width": 1.025, "height": 1.4, "shape": "rect"}
        for i, x in [(1, -0.9125), (2, 0.9125)]
    ],
}


def _held_texts(spec: dict) -> R.Library:
    """`_held`, with the library's own texts kept, as a resize needs them."""
    library = _held(spec)
    library.texts = {(key, spec["partition"]): text for key, text in P.plan(spec).texts().items()}
    return library


class LibraryPadDimensionsTest(unittest.TestCase):
    """Existing-pad resizing preserves the cell source and refuses shared changes."""

    def test_pad_resize_preserves_existing_cell_and_pin_mapping(self):
        existing = _held_texts(_spec(RECT_RES))
        cell = existing.cells[0]
        spec = _spec(RECT_RES)
        spec["parts"][0]["footprint"] = {
            "cell": cell["name"],
            "pad_dimensions": {
                p["number"]: {"copper": [1.225, 1.4], "mask": [1.325, 1.5], "paste": [1.225, 1.4]}
                for p in cell["pins"]
            },
        }
        before = copy.deepcopy(existing)
        plan = P.plan(spec, existing)
        texts = plan.texts()
        self.assertEqual(R.parse_cells(texts["cells"], spec["partition"]), existing.cells)
        self.assertFalse(texts["parts"])
        self.assertFalse(plan.symbols)
        self.assertEqual(plan.actions["cells"][0]["action"], "refresh_dependencies")
        self.assertTrue(plan.replaces)
        self.assertTrue(all(r.startswith("pad ") for r in plan.replaces))
        parsed = R.parse_padstacks(texts["padstacks"])
        self.assertTrue(parsed["pads"])
        self.assertEqual(parsed["padstacks"], existing.padstacks["padstacks"])
        self.assertEqual(existing, before)
        for name, row in parsed["pads"].items():
            self.assertIn(name, existing.padstacks["pads"])
            self.assertIn(row["width"], (1.225, 1.325))
        # Actual readback of dimension values; a repeated request has no replacement.
        existing.padstacks["pads"].update(parsed["pads"])
        self.assertFalse(P.plan(spec, existing).replaces)

    def test_pad_resize_rejects_undeclared_or_unsupported_changes(self):
        for fault in ["other_cell", "missing_pin", "conflict", "oblong"]:
            with self.subTest(fault=fault):
                existing = _held_texts(_spec(RECT_RES))
                cell = existing.cells[0]
                spec = _spec(RECT_RES)
                sizes = {
                    p["number"]: {"copper": [1.225, 1.4], "mask": [1.325, 1.5], "paste": [1.225, 1.4]}
                    for p in cell["pins"]
                }
                spec["parts"][0]["footprint"] = {"cell": cell["name"], "pad_dimensions": sizes}
                if fault == "other_cell":
                    existing.cells.append({**cell, "name": "protected_other_cell"})
                elif fault == "missing_pin":
                    sizes.pop(next(iter(sizes)))
                elif fault == "conflict":
                    sizes[next(iter(sizes))]["paste"] = [1.4, 1.4]
                else:
                    for shape in existing.padstacks["pads"].values():
                        shape["shape"] = "OBLONG"
                with self.assertRaises(P.PartsFileError):
                    P.plan(spec, existing)
