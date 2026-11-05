"""Every sheet the planner draws is landscape, so every page it asks for must be too."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from xpedition_cli.adapter.plan import pdf_pages
from xpedition_cli.adapter.plan import schematic_layout as L

LANDSCAPE_FAMILY = range(11, 21)  # VDSHEET_AL_SIZE ... VDSHEET_A0L_SIZE
# plain codes recorded printing a landscape page: A4 842 x 595 pt, A3 1190 x 842 pt
RECORDED_LANDSCAPE = {5, 6}


def _one_part(size: str) -> dict:
    return {
        "sheet_size": size,
        "sheets": [
            {
                "number": 1,
                "title": "ONE",
                "blocks": [
                    {
                        "kind": "ladder",
                        "x": 300,
                        "y": 500,
                        "path": [
                            "power:+3V3",
                            {"refdes": "R1", "symbol": "RES", "value": "10k"},
                            "gnd",
                        ],
                    }
                ],
            }
        ],
    }


class SheetSizesTest(unittest.TestCase):
    def test_every_sheet_size_asks_for_a_landscape_page(self):
        self.assertEqual(set(L.SHEET_SIZES), set(L.SHEET_BORDERS))
        for size, (width, height) in L.SHEET_SIZES.items():
            border, code = L.SHEET_BORDERS[size]
            self.assertGreater(width, height, msg=size)
            # the border symbols without a suffix are the landscape ones
            self.assertEqual(border, f"{size.lower()}sheet", msg=size)
            self.assertTrue(code in LANDSCAPE_FAMILY or code in RECORDED_LANDSCAPE, msg=size)

    def test_the_ansi_sizes_leave_the_portrait_family(self):
        # VDSHEET_CSIZE (2) printed a landscape C border on a 17 x 22 in page
        self.assertEqual({size: L.SHEET_BORDERS[size][1] for size in "ABCDE"}, {
            "A": 11,
            "B": 12,
            "C": 13,
            "D": 14,
            "E": 15,
        })

    def test_a_design_s_sheet_size_reaches_the_draw(self):
        for size, border, code, measured in [
            ("C", "csheet", 13, False),
            ("A4", "a4sheet", 5, True),
            ("A2", "a2sheet", 18, False),
            ("A0", "a0sheet", 20, False),
        ]:
            with self.subTest(size=size, border=border, code=code, measured=measured):
                params = L.plan_to_params(_one_part(size), "X.prj")
                [set_sheet] = [op for op in params["ops"] if op["op"] == "set_sheet"]
                self.assertEqual((set_sheet["border"], set_sheet["size"]), (border, code))
                width, height = L.SHEET_SIZES[size]
                x1, y1, x2, y2 = params["summary"]["usable"]["1"]
                self.assertTrue(0 < x1 < x2 < width)
                self.assertTrue(0 < y1 < y2 < height)
                # a size without a measured title block says its usable area runs into it
                self.assertIs(params["summary"]["usable_measured"], measured)

    def test_a_square_or_landscape_page_carries_no_warning(self):
        found = pdf_pages.pages(b"<< /MediaBox [0 0 842 595] >> << /MediaBox [0 0 600 600] >>")
        self.assertEqual([p["orientation"] for p in found], ["landscape", "square"])
        self.assertEqual(pdf_pages.warnings(found), [])
        self.assertEqual(pdf_pages.pages(b"%PDF-1.4 no pages"), [])

    def test_page_sizes_are_read_back_from_the_pdf(self):
        data = (
            b"%PDF-1.4\n1 0 obj << /Type /Page /MediaBox [0 0 1224 1584] >> endobj\n"
            b"2 0 obj << /Type /Page /MediaBox [ 0 0 1190.55 841.89 ] >> endobj\n"
        )
        found = pdf_pages.pages(data)
        self.assertEqual([(p["orientation"], p["width_mm"], p["height_mm"]) for p in found], [
            ("portrait", 431.8, 558.8),
            ("landscape", 420.0, 297.0),
        ])
        [warning] = pdf_pages.warnings(found)
        self.assertTrue(warning.startswith("page 1 is portrait (1224 x 1584 pt)"))

    def test_the_example_designs_still_plan_on_their_recorded_a4_code(self):
        example = Path(__file__).resolve().parent / "fixtures" / "demo-sensor-board.json"
        params = L.plan_to_params(json.loads(example.read_text(encoding="utf-8")), "X.prj")
        sizes = {op["size"] for op in params["ops"] if op["op"] == "set_sheet"}
        self.assertEqual(sizes, {5})

    def test_a_landscape_page_code_reads_back_as_its_plain_size(self):
        # CL_SIZE (13) set, CSIZE (2) read, and the page exported landscape: a set that took
        taken = L.sheet_size_taken
        self.assertTrue(taken(2, 13))
        self.assertTrue(taken(1, 12))
        self.assertTrue(taken(9, 20))
        self.assertTrue(taken(2, 23))
        self.assertTrue(taken(5, 5))
        self.assertTrue(taken(13, 13))
        self.assertFalse(taken(3, 13))
        self.assertFalse(taken(2, 5))
