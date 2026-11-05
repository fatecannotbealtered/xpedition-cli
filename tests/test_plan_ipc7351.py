"""IPC-7351B land patterns: the sums, the families, and the cells they become."""

from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import ipc7351 as I
from xpedition_cli.adapter.plan import library_hkp as H


def _cell(spec: dict, kind: str = "") -> tuple[H.Cell, H.LibraryPlan]:
    plan = H.LibraryPlan()
    return I.footprint(spec, H._Stock(plan), kind=kind), plan


def _pad(plan: H.LibraryPlan, cell: H.Cell, number: str) -> tuple[H.CellPin, H.Pad]:
    pin = next(p for p in cell.pins if p.number == number)
    return pin, plan.pads[plan.padstacks[pin.padstack].pad]


SOIC8 = {
    "family": "gullwing",
    "pins": 8,
    "pitch": 1.27,
    "span": {"nominal": 6.0, "tolerance": 0.2},
    "terminal": [0.4, 1.27],
    "lead_width": [0.31, 0.51],
    "body": [3.9, 4.9],
    "height": 1.75,
}


USB_LEGS = {
    "name": "USB-LEGS",
    "height": 3.2,
    "pads": [
        {"pin": "A1", "x": -3.0, "y": 0.0, "width": 0.6, "height": 1.0},
        # the shield's legs: plated slots in oblong lands, both on one pin
        {
            "pin": "S1",
            "x": -4.3,
            "y": 1.5,
            "width": 1.0,
            "height": 2.1,
            "shape": "oblong",
            "drill": [0.6, 1.7],
        },
        {
            "pin": "S1",
            "x": 4.3,
            "y": 1.5,
            "width": 1.0,
            "height": 2.1,
            "shape": "oblong",
            "drill": [0.6, 1.7],
        },
    ],
    # the locating pegs: unplated holes that are no pin
    "holes": [{"x": -2.9, "y": 2.0, "drill": 0.65}, {"x": 2.9, "y": 2.0, "drill": 0.65}],
}


class Ipc7351Test(unittest.TestCase):
    def test_the_sums_give_the_lands_the_kicad_library_has(self):
        # SOIC-8 (JEDEC MS-012): KiCad's lands are 1.95 x 0.6 at x = +-2.475
        g, z, x = I.lands("gullwing", "N", I.Span(5.8, 6.2), I.Span(0.4, 1.27), I.Span(0.31, 0.51))
        self.assertEqual((g, z, x), (3.0, 6.9, 0.6))
        self.assertAlmostEqual((z - g) / 2, 1.95)
        self.assertAlmostEqual((z + g) / 4, 2.475)
        # TSSOP-20 (MO-153): 1.475 x 0.4 at x = +-2.8625
        g, z, x = I.lands("gullwing", "N", I.Span(6.4, 6.4), I.Span(0.45, 0.75), I.Span(0.17, 0.3))
        self.assertAlmostEqual((z - g) / 2, 1.475)
        self.assertAlmostEqual((z + g) / 4, 2.8625)
        self.assertAlmostEqual(x, 0.4)

    def test_density_levels_grow_the_lands_from_least_to_most(self):
        sizes = [
            I.lands("gullwing", level, I.Span(5.8, 6.2), I.Span(0.4, 1.27), I.Span(0.31, 0.51))
            for level in ("L", "N", "M")
        ]
        outer = [z for _g, z, _x in sizes]
        self.assertEqual(outer, sorted(outer))
        self.assertLess(outer[0], outer[2])

    def test_a_soic_is_numbered_down_the_left_and_up_the_right(self):
        cell, plan = _cell(SOIC8)
        self.assertEqual(cell.name, "SOIC127P600X175-8N")
        self.assertEqual(cell.group, "IC_SOIC")
        first, pad = _pad(plan, cell, "1")
        self.assertEqual((first.x, first.y), (-2.475, 1.905))
        self.assertAlmostEqual(pad.width, 1.95)
        self.assertAlmostEqual(pad.height, 0.6)
        self.assertEqual(pad.shape, "RADIUS_CORNER_RECTANGLE")
        self.assertEqual(pad.radius, 0.15)
        last, _ = _pad(plan, cell, "8")
        self.assertEqual((last.x, last.y), (2.475, 1.905))
        fourth, _ = _pad(plan, cell, "4")
        self.assertEqual(fourth.y, -1.905)

    def test_omitted_positions_keep_their_place_and_the_rest_are_renumbered(self):
        sot23 = {
            "family": "gullwing",
            "pins": 3,
            "positions": 6,
            "omit": [2, 4, 6],
            "pitch": 0.95,
            "span": [2.1, 2.64],
            "terminal": [0.4, 0.6],
            "lead_width": [0.3, 0.5],
            "body": [1.3, 2.9],
            "height": 1.12,
        }
        cell, _ = _cell(sot23)
        where = {p.number: (p.x < 0, p.y) for p in cell.pins}
        self.assertEqual(where, {"1": (True, 0.95), "2": (True, -0.95), "3": (False, 0.0)})
        with self.assertRaisesRegex(I.FootprintError, "positions"):
            _cell({**sot23, "omit": [2]})

    def test_a_qfn_runs_counter_clockwise_and_clears_its_exposed_pad(self):
        qfn = {
            "family": "nolead",
            "pins": 16,
            "pitch": 0.5,
            "body": [3.0, 3.0],
            "terminal": [0.3, 0.5],
            "lead_width": [0.18, 0.3],
            "exposed_pad": {"size": [1.7, 1.7]},
            "height": 0.9,
        }
        cell, plan = _cell(qfn)
        self.assertEqual(cell.name, "QFN50P300X300X90-17N")
        side = {p.number: p for p in cell.pins}
        self.assertLess(side["1"].x, 0)
        self.assertGreater(side["1"].y, side["4"].y)  # down the left
        self.assertLess(side["5"].y, 0)
        self.assertLess(side["5"].x, side["8"].x)  # along the bottom
        self.assertGreater(side["9"].x, 0)
        self.assertLess(side["9"].y, side["12"].y)  # up the right
        self.assertGreater(side["13"].y, 0)
        self.assertGreater(side["13"].x, side["16"].x)  # back along the top
        exposed, pad = _pad(plan, cell, "17")
        self.assertEqual((exposed.x, exposed.y), (0.0, 0.0))
        self.assertEqual(pad.width, 1.7)
        stack = plan.padstacks[exposed.padstack]
        paste = plan.pads[stack.paste]
        self.assertAlmostEqual(paste.width * paste.height, 0.7 * 1.7 * 1.7, delta=0.01 * 0.7 * 1.7 * 1.7)
        # every terminal land ends 0.2 mm or more from the exposed pad
        for pin in cell.pins:
            if pin.number == "17":
                continue
            _, land = _pad(plan, cell, pin.number)
            half_w, half_h = land.width / 2, land.height / 2
            gap_x = abs(pin.x) - half_w - 0.85
            gap_y = abs(pin.y) - half_h - 0.85
            self.assertGreaterEqual(max(gap_x, gap_y), 0.2 - 1e-6, msg=pin.number)

    def test_chips_take_their_table_by_size_and_name_themselves_by_kind(self):
        small, plan = _cell({"family": "chip", "size": "0402"}, "C")
        large, plan2 = _cell({"family": "chip", "size": "0603"}, "R")
        self.assertEqual(small.name, "CAPC1005X35N")
        self.assertEqual(large.name, "RESC1608X50N")
        # the small-chip table rounds Z and G to 0.02 mm, the other to 0.05 mm
        length, width, terminal, _height = I.CHIP_SIZES["0402"]
        g, z, _x = I.lands("chip_small", "N", I.Span(*length), I.Span(*terminal), I.Span(*width))
        self.assertEqual(round(g / 0.02, 6), round(g / 0.02))
        self.assertEqual(round(z / 0.02, 6), round(z / 0.02))
        length, width, terminal, _height = I.CHIP_SIZES["0603"]
        g, z, _x = I.lands("chip", "N", I.Span(*length), I.Span(*terminal), I.Span(*width))
        self.assertEqual(round(g / 0.05, 6), round(g / 0.05))
        self.assertEqual(round(z / 0.05, 6), round(z / 0.05))
        _, pad = _pad(plan, small, "1")
        self.assertAlmostEqual(pad.width, 0.55)
        self.assertEqual([p.number for p in small.pins], ["1", "2"])
        self.assertLess(small.pins[0].x, 0)

    def test_through_hole_holes_and_lands_follow_the_lead(self):
        header, plan = _cell(
            {"family": "through", "pins": 4, "pitch": 2.54, "lead": {"square": 0.64}, "height": 8.5}
        )
        first, pad = _pad(plan, header, "1")
        hole = plan.holes[plan.padstacks[first.padstack].hole]
        # the square lead's diagonal is 0.905 mm: 0.2 mm over it, up to 0.05, is 1.15
        self.assertAlmostEqual(hole.diameter, 1.15)
        self.assertAlmostEqual(pad.width, 1.75)
        self.assertEqual(pad.shape, "RECTANGLE")
        self.assertEqual(header.mount, "THROUGH")
        self.assertEqual([p.y for p in header.pins], [3.81, 1.27, -1.27, -3.81])
        dip, _ = _cell(
            {
                "family": "through",
                "pins": 8,
                "rows": 2,
                "pitch": 2.54,
                "row_pitch": 7.62,
                "lead": [0.36, 0.56],
                "height": 5.3,
            }
        )
        where = {p.number: (p.x, p.y) for p in dip.pins}
        self.assertEqual(where["1"], (-3.81, 3.81))
        self.assertEqual(where["4"], (-3.81, -3.81))
        self.assertEqual(where["5"], (3.81, -3.81))
        self.assertEqual(where["8"], (3.81, 3.81))

    def test_lands_given_one_by_one_may_share_a_pin(self):
        cell, _ = _cell(
            {
                "pads": [
                    {"pin": "1", "x": -1, "y": 0, "width": 0.6, "height": 0.8},
                    {"pin": "2", "x": 1, "y": 0.5, "width": 0.6, "height": 0.4},
                    {"pin": "2", "x": 1, "y": -0.5, "width": 0.6, "height": 0.4},
                ],
                "height": 0.8,
                "name": "SPLIT",
            }
        )
        self.assertEqual(cell.name, "SPLIT")
        self.assertEqual([p.number for p in cell.pins], ["1", "2", "2"])
        with self.assertRaisesRegex(I.FootprintError, "drill inside its land"):
            _cell({"pads": [{"pin": "1", "x": 0, "y": 0, "width": 0.6, "drill": 0.8}], "height": 1})

    def test_custom_rect_keeps_square_corners_unless_round_is_explicit(self):
        spec = {
            "pads": [{"pin": "1", "x": 0, "y": 0, "width": 1.025, "height": 1.4, "shape": "rect"}],
            "height": 0.6,
        }
        cell, library = _cell(spec)
        _, pad = _pad(library, cell, "1")
        self.assertEqual(pad.shape, "RECTANGLE")
        self.assertEqual(pad.radius, 0)
        rounded, library = _cell({**spec, "corners": "round"})
        _, pad = _pad(library, rounded, "1")
        self.assertEqual(pad.shape, "RADIUS_CORNER_RECTANGLE")
        self.assertGreater(pad.radius, 0)

    def test_the_silkscreen_stays_off_the_lands_and_the_courtyard_holds_everything(self):
        cell, plan = _cell(SOIC8)
        lands = []
        for pin in cell.pins:
            pad = plan.pads[plan.padstacks[pin.padstack].pad]
            lands.append(
                (
                    pin.x - pad.width / 2,
                    pin.y - pad.height / 2,
                    pin.x + pad.width / 2,
                    pin.y + pad.height / 2,
                )
            )
        silk = [g for g in cell.graphics if g.block == "SILKSCREEN_OUTLINE"]
        self.assertTrue(silk)
        for graphic in silk:
            for x, y in graphic.points:
                for x1, y1, x2, y2 in lands:
                    inside_x = x1 - 0.2 < x < x2 + 0.2
                    inside_y = y1 - 0.2 < y < y2 + 0.2
                    self.assertFalse(inside_x and inside_y, msg=(x, y))
        courtyard = next(g for g in cell.graphics if g.block == "PLACEMENT_OUTLINE")
        xs = [x for x, _ in courtyard.points]
        ys = [y for _, y in courtyard.points]
        for graphic in cell.graphics:
            for x, y in graphic.points:
                self.assertTrue(min(xs) <= x <= max(xs))
                self.assertTrue(min(ys) <= y <= max(ys))
        for x1, y1, x2, y2 in lands:
            self.assertLessEqual(min(xs), x1)
            self.assertLessEqual(x2, max(xs))
            self.assertLessEqual(min(ys), y1)
            self.assertLessEqual(y2, max(ys))

    def test_bad_specs_say_what_is_wrong(self):
        with self.assertRaisesRegex(I.FootprintError, "height"):
            _cell({**SOIC8, "height": None})
        with self.assertRaisesRegex(I.FootprintError, "pitch"):
            _cell({**SOIC8, "pitch": 0.4})
        with self.assertRaisesRegex(I.FootprintError, "family"):
            _cell({"family": "bga"})
        with self.assertRaisesRegex(I.FootprintError, "maximum"):
            I.span([2, 1], "span")
        self.assertEqual(I.span({"nominal": 1.0, "tolerance": 0.1}, "x"), I.Span(0.9, 1.1))

    def test_lands_take_slots_and_a_cell_takes_unplated_holes_of_its_own(self):
        plan = H.LibraryPlan(partition="P")
        stock = H._Stock(plan)
        cell = I.footprint(USB_LEGS, stock, name="USB-LEGS")
        plan.cells[cell.name] = cell
        leg = plan.padstacks[next(p for p in cell.pins if p.number == "S1").padstack]
        self.assertEqual(leg.kind, "PIN_THROUGH")
        self.assertEqual(plan.holes[leg.hole].slot, (0.6, 1.7))
        self.assertTrue(plan.holes[leg.hole].plated)
        self.assertEqual(cell.mount, "MIXED")
        self.assertEqual(len(cell.holes), 2)
        self.assertEqual({h.x for h in cell.holes}, {-2.9, 2.9})
        peg = plan.padstacks[cell.holes[0].padstack]
        self.assertEqual(peg.kind, "MOUNTING_HOLE")
        self.assertFalse(plan.holes[peg.hole].plated)
        # what the converters are handed reads back the same
        from xpedition_cli.adapter.plan import library_read as R

        stacks = R.parse_padstacks(H.render_padstacks(plan))
        slot = stacks["holes"][leg.hole]
        self.assertEqual((slot["shape"], slot["width"], slot["height"], slot["plated"]), (
            "SLOT",
            0.6,
            1.7,
            True,
        ))
        self.assertIs(stacks["holes"][peg.hole]["plated"], False)
        read = R.parse_cells(H.render_cells(plan))[0]
        self.assertEqual(sorted(h["x"] for h in read["holes"]), [-2.9, 2.9])

    def test_a_land_whose_hole_cannot_be_is_refused(self):
        for change, message in [
            ({"drill": [0.6, 2.5]}, "inside its land"),
            ({"drill": [0.6]}, "slot's [width, height]"),
            ({"plated": "yes"}, "plated is true or false"),
        ]:
            with self.subTest(change=change, message=message):
                spec = {**USB_LEGS, "pads": [{**USB_LEGS["pads"][1], **change}]}
                with self.assertRaisesRegex(I.FootprintError, message.replace("[", r"\[").replace("]", r"\]")):
                    I.footprint(spec, H._Stock(H.LibraryPlan(partition="P")), name="X")

    def test_a_hole_needs_a_drill(self):
        spec = {**USB_LEGS, "holes": [{"x": 0, "y": 0}]}
        with self.assertRaisesRegex(I.FootprintError, "needs a drill"):
            I.footprint(spec, H._Stock(H.LibraryPlan(partition="P")), name="X")

    def test_without_a_body_the_outline_is_where_the_lands_are_and_clear_of_holes(self):
        # the lands of USB_LEGS stand from y -0.5 to 2.55: the outline centred on the
        # origin ran through the shell legs and over the locating pegs
        plan = H.LibraryPlan(partition="P")
        cell = I.footprint(USB_LEGS, H._Stock(plan), name="USB-LEGS")
        x1, y1, x2, y2 = cell.body
        self.assertLessEqual(y1, -0.5)
        self.assertGreaterEqual(y2, 2.55)
        self.assertLessEqual(x1, -4.8)
        self.assertGreaterEqual(x2, 4.8)
        silk = [g for g in cell.graphics if g.block == "SILKSCREEN_OUTLINE"]
        for peg in cell.holes:
            opening = (0.65 + 0.2) / 2
            for graphic in silk:
                (ax, ay), (bx, by) = graphic.points
                # distance from the peg to the segment (horizontal or vertical)
                nx = min(max(peg.x, min(ax, bx)), max(ax, bx))
                ny = min(max(peg.y, min(ay, by)), max(ay, by))
                gap = ((peg.x - nx) ** 2 + (peg.y - ny) ** 2) ** 0.5 - opening - I.SILK_WIDTH / 2
                self.assertGreaterEqual(gap, I.SILK_CLEARANCE - 1e-6)
