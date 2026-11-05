"""DS-07 checks the area a design may draw in, not just the border.

Three regions inside the border are already taken: the sheet title strip along
the top, the notes band across the lower left, and Designer's title block in the
lower right. Checking only the border passed a sheet with a whole stage drawn
through the notes and capacitors sitting on the title block, reported with
`issues: []`. The planner puts the title and the notes itself, so those two are
computed rather than measured.
"""

from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan.schematic_layout import MARGIN, plan, usable_rectangle


def sheet(parts_y: int, notes: list[str] | None = None) -> dict:
    return {
        "sheet_size": "A4",
        "sheets": [
            {
                "number": 1,
                "title": "One",
                "notes": notes or [],
                "blocks": [
                    {
                        "kind": "ladder",
                        "x": 300,
                        "y": parts_y,
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


def ds07(result) -> list[dict]:
    return [issue for issue in result.issues if issue["check"] == "DS-07"]


class UsableAreaTest(unittest.TestCase):
    def test_the_notes_band_is_reserved_and_grows_with_the_notes(self):
        bare = usable_rectangle(1169, 827, 0)
        with_notes = usable_rectangle(1169, 827, 4)
        self.assertGreater(with_notes[1], bare[1], msg="each note takes another 20 units off the bottom")
        self.assertEqual(with_notes[1] - bare[1], 80)

    def test_the_title_strip_is_reserved_at_the_top(self):
        x1, y1, x2, y2 = usable_rectangle(1169, 827, 0)
        self.assertLess(y2, 827 - MARGIN, msg="the sheet title and description sit above the drawable area")
        self.assertEqual((x1, x2), (MARGIN, 1169 - MARGIN))

    def test_a_part_in_the_notes_band_is_reported(self):
        # Low enough to land in the band a four-note sheet reserves.
        result = plan(sheet(parts_y=MARGIN + 60, notes=["a", "b", "c", "d"]))
        found = ds07(result)
        self.assertTrue(found, msg="a part drawn through the notes must not pass")
        self.assertTrue(found[0]["usable"], msg="the report carries the rectangle it was measured against")

    def test_a_part_in_the_open_area_is_not_reported(self):
        self.assertEqual(ds07(plan(sheet(parts_y=400))), [])

    def test_the_plan_publishes_the_rectangle_per_sheet(self):
        summary = plan(sheet(parts_y=400, notes=["one"])).summary()
        self.assertEqual(summary["usable"]["1"], list(usable_rectangle(1169, 827, 1, "A4")))

    def test_the_rectangle_tracks_the_sheet_size(self):
        a4 = usable_rectangle(1169, 827, 0)
        a3 = usable_rectangle(1654, 1169, 0)
        self.assertGreater(a3[2], a4[2])
        self.assertGreater(a3[3], a4[3])

    def test_a_measured_border_tightens_the_computed_rectangle(self):
        # A4's title block and frame are measured; the computed rectangle alone is
        # looser than what the border actually leaves.
        computed = usable_rectangle(1169, 827, 0)
        measured = usable_rectangle(1169, 827, 0, "A4")
        self.assertGreaterEqual(measured[0], computed[0])
        self.assertGreaterEqual(measured[1], computed[1])
        self.assertLessEqual(measured[2], computed[2])
        self.assertLessEqual(measured[3], computed[3])

    def test_an_unmeasured_size_keeps_the_computed_rectangle(self):
        self.assertEqual(usable_rectangle(1700, 1100, 0, "B"), usable_rectangle(1700, 1100, 0))
