from __future__ import annotations

import json
import unittest
from pathlib import Path

from xpedition_cli.adapter.plan import PlanError
from xpedition_cli.adapter.plan import library_hkp as H

EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "demo-sensor-board.json"


def _design(blocks, **extra):
    return {"sheet_size": "A4", "sheets": [{"number": 1, "blocks": blocks}], **extra}


class LibraryHkpTest(unittest.TestCase):
    def test_demo_design_gets_a_cell_and_a_part_for_every_placed_part(self):
        design = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        plan, texts = H.library_texts(design)
        self.assertEqual(plan.issues, [])
        self.assertEqual(len(plan.mapping), 30)
        cells = {row["cell"] for row in plan.mapping}
        self.assertLessEqual({"CLI_0402", "CLI_0603", "CLI_TP", "CLI_HOLE_M2", "CLI_HDR4", "CLI_SOIC8"}, cells)
        self.assertIn('.PACKAGE_CELL "CLI_0402"', texts["cells"])
        self.assertIn('.PADSTACK "SMD-RECT0.6X0.55"', texts["padstacks"])
        self.assertIn('.Number "10k 1%"', texts["parts"])
        numbers = {row["part_number"] for row in plan.mapping} - {None}
        self.assertEqual(numbers, set(plan.parts))
        self.assertTrue(all(row["part_number"] is None for row in plan.mapping if row["symbol"] == "HOLE"))

    def test_parts_map_symbol_pin_names_to_cell_pin_numbers(self):
        design = _design(
            [
                {
                    "kind": "ic",
                    "refdes": "Q1",
                    "symbol": "PMOS",
                    "value": "AO3401",
                    "x": 500,
                    "y": 400,
                    "pins": {"1": "label:G", "2": "power:VBAT", "3": "label:D"},
                }
            ]
        )
        plan, texts = H.library_texts(design)
        part = plan.parts["AO3401"]
        self.assertEqual(part.pin_names, ["G", "S", "D"])
        self.assertEqual(part.pin_numbers, ["1", "2", "3"])
        self.assertEqual(part.cell, "CLI_SOT23")
        self.assertEqual(part.prefix, "Q")
        self.assertEqual(part.part_type, "MOSFET")
        self.assertIn('\t\t\t....PinName\t"G"', texts["parts"])
        self.assertIn('\t\t\t....PinNumber\t"3"', texts["parts"])
        self.assertEqual(texts["parts"].count('...SwapID\t"P'), 3)

    def test_package_override_and_unknown_package_are_reported(self):
        ic = {
            "kind": "box",
            "top": [["1", "VDD"]],
            "left": [["2", "IN"]],
            "right": [["3", "OUT"], ["4", "NC"]],
            "bottom": [["5", "GND"]],
        }
        blocks = [
            {
                "kind": "ic",
                "refdes": "U1",
                "symbol": "AMP",
                "value": "OPA333",
                "x": 500,
                "y": 400,
                "pins": {"1": "power:+3V3", "2": "label:IN", "3": "label:OUT", "4": "nc", "5": "gnd"},
            },
            {
                "kind": "ladder",
                "x": 800,
                "y": 500,
                "path": ["power:+3V3", {"refdes": "R1", "symbol": "RES", "value": "10k"}, "gnd"],
            },
        ]
        plan, _ = H.library_texts(_design(blocks, symbols={"AMP": ic}, packages={"U1": "TSSOP20"}))
        self.assertEqual(plan.parts["OPA333"].cell, "CLI_TSSOP20")
        self.assertEqual(plan.parts["10k"].cell, "CLI_0402")
        plan, _ = H.library_texts(_design(blocks, symbols={"AMP": ic}, packages={"RES": "BGA"}))
        self.assertTrue(any("unknown package" in issue for issue in plan.issues))

    def test_padstack_text_declares_pads_holes_and_stacks_in_millimetres(self):
        plan, texts = H.library_texts(
            _design(
                [
                    {
                        "kind": "ladder",
                        "x": 300,
                        "y": 500,
                        "path": [
                            "power:VBAT",
                            {"refdes": "SW1", "symbol": "SW", "value": "LID"},
                            "gnd",
                        ],
                    },
                    {"kind": "ic", "refdes": "H1", "symbol": "HOLE", "value": "M2", "x": 600, "y": 400},
                ]
            )
        )
        text = texts["padstacks"]
        self.assertIn(".UNITS MM", text)
        self.assertIn('.HOLE "C1-PLATED"', text)
        self.assertIn("..HOLE_OPTIONS PLATED DRILLED", text)
        self.assertIn('.HOLE "C2.2-NONPLATED"', text)
        self.assertIn("NON_PLATED", text)
        self.assertIn("..PADSTACK_TYPE PIN_THROUGH", text)
        self.assertIn("..PADSTACK_TYPE MOUNTING_HOLE", text)
        self.assertEqual(plan.cells["CLI_HDR2"].mount, "THROUGH")

    def test_dual_row_pads_run_across_the_row_and_never_touch(self):
        # Batch DRC on a routed board found neighbouring SOIC pads overlapping (required
        # 0.254 mm, actual 0): the pad's long side had been laid along the pitch.
        import re

        stock = H._Stock(H.LibraryPlan())
        for key, pitch in (("SOIC8", 1.27), ("TSSOP20", 0.8)):
            cell = H.build_package(stock, key, 8)
            left = sorted((pin for pin in cell.pins if pin.x < 0), key=lambda pin: pin.y)
            self.assertGreaterEqual(len(left), 2)
            self.assertLess(abs(abs(left[1].y - left[0].y) - pitch), 1e-9)
            width, height = map(float, re.search(r"RECT([\d.]+)X([\d.]+)", left[0].padstack).groups())
            self.assertLess(height, pitch, msg=(key, left[0].padstack))
            self.assertGreater(width, height)

    def test_design_text_that_would_write_its_own_records_is_refused(self):
        crafted = 'AO3401" LF .Number "INJECTED_PART'.replace(" LF ", chr(10))
        block = {"kind": "ic", "refdes": "Q1", "symbol": "PMOS", "value": crafted, "x": 500, "y": 400}
        block["pins"] = {"1": "label:G", "2": "power:VBAT", "3": "label:D"}
        with self.assertRaises(PlanError) as caught:
            H.library_texts(_design([block]))
        self.assertEqual(caught.exception.code, "E_VALIDATION")
        self.assertTrue(caught.exception.details["fields"][0]["value"].startswith("AO3401"))

    def test_a_generated_cell_carries_its_reference_designator(self):
        """Without one the placed part had no designator on the silkscreen at all."""
        design = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        plan, texts = H.library_texts(design)
        chip = plan.cells["CLI_0402"]
        self.assertIsNotNone(chip.refdes)
        self.assertIsNotNone(chip.refdes_assembly)
        # above the body and its pads, centred
        self.assertEqual(chip.refdes.x, 0)
        self.assertGreater(chip.refdes.y, max(pin.y for pin in chip.pins) + 0.5)
        self.assertIsNone(plan.cells["CLI_HOLE_M2"].refdes)  # a hole's designator means nothing
        block = texts["cells"].split('.PACKAGE_CELL "CLI_0402"')[1].split(".PACKAGE_CELL")[0]
        self.assertIn("TEXT_TYPE REF_DES", block)
        self.assertIn("SILKSCREEN_MNT_LYR", block)
        self.assertIn("ASSEMBLY_MNT_LYR", block)

    def test_placeholder_outlines_keep_the_silkscreen_off_the_lands(self):
        """The placeholders used to draw a box just outside the body, straight across the
    lands of a chip; the silkscreen keeps its clearance from the lands now, and the
    placement outline holds the lands, not only the body."""
        from xpedition_cli.adapter.plan import library_read as R

        keys = (("0402", 2), ("0603", 2), ("0805", 2), ("SOT23", 3), ("TP", 1))
        keys += (("HDR4", 4), ("SOIC8", 8), ("TSSOP14", 14))
        plan = H.LibraryPlan()
        stock = H._Stock(plan)
        for key, count in keys:
            cell = H.build_package(stock, key, count)
            plan.cells[cell.name] = cell
        text = H.render_cells(plan)
        library = R.Library(
            cells=R.parse_cells(text, "P"), padstacks=R.parse_padstacks(H.render_padstacks(plan))
        )
        for cell in library.cells:
            self.assertEqual(R.silk_over_lands(cell, library), [], msg=cell["name"])
            placement = [p for g in cell["placement"] for p in g["points"]]
            xs, ys = [p[0] for p in placement], [p[1] for p in placement]
            for pin in cell["pins"]:
                self.assertTrue(min(xs) < pin["x"] < max(xs) and min(ys) < pin["y"] < max(ys), msg=cell["name"])
        by_name = {cell["name"]: cell for cell in library.cells}
        # a chip too small for a line clear of its lands keeps none; a bigger one keeps
        # the two short lines between them; the header its whole box
        self.assertEqual(by_name["CLI_0402"]["silkscreen"], [])
        self.assertEqual(len(by_name["CLI_0805"]["silkscreen"]), 2)
        self.assertEqual(len(by_name["CLI_HDR4"]["silkscreen"]), 4)
        # the old box, drawn over the lands, is what the library check reports
        old = dict(by_name["CLI_0402"])
        old["silkscreen"] = [
            {
                "kind": "path",
                "points": [[-0.65, -0.4], [0.65, -0.4], [0.65, 0.4], [-0.65, 0.4], [-0.65, -0.4]],
                "width": 0.15,
            }
        ]
        self.assertEqual(R.silk_over_lands(old, library), ["1", "2"])
