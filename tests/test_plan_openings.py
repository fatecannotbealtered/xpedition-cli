"""The ears of an outline, and where a soldermask opening bares one net's copper alone."""
from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import ears as EA
from xpedition_cli.adapter.plan import lanes as LN
from xpedition_cli.adapter.plan import openings as OP


def rows(points):
    return [(x, y, 0.0) for x, y in points]


class EarsTest(unittest.TestCase):
    def test_a_tab_on_a_straight_edge_is_an_ear(self):
        # a 10 x 40 mm strip with a 2 mm tab on its right edge between y 18 and 22
        ring = LN.outline_corners(rows([(0, 0), (10, 0), (10, 18), (12, 18), (12, 22), (10, 22), (10, 40), (0, 40)]))
        found = EA.ears(ring)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["base_mm"], [[10.0, 18.0], [10.0, 22.0]])
        self.assertEqual(found[0]["depth_mm"], 2.0)
        self.assertEqual(found[0]["extents_mm"], [10.0, 18.0, 12.0, 22.0])
        self.assertEqual(found[0]["points_mm"], [[10.0, 18.0], [12.0, 18.0], [12.0, 22.0], [10.0, 22.0]])

    def test_a_rounded_ear_on_a_slanted_edge_is_found_with_its_arcs(self):
        # the same tab with rounded corners, turned 45 degrees: Layout's rows give each arc by its centre
        import math

        def turn(x, y):
            c, s = math.cos(math.pi / 4), math.sin(math.pi / 4)
            return (round(x * c - y * s, 6), round(x * s + y * c, 6))

        pts = [(0, 0), (10, 0), (10, 18), (11.5, 18), None, (12, 18.5), (12, 21.5), None, (11.5, 22), (10, 22), (10, 40), (0, 40)]
        centres = iter([(11.5, 18.5), (11.5, 21.5)])
        out = []
        for p in pts:
            if p is None:
                cx, cy = next(centres)
                x, y = turn(cx, cy)
                out.append((x, y, -0.5))
            else:
                out.append((*turn(*p), 0.0))
        found = EA.ears(LN.outline_corners(out))
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["depth_mm"], 2.0, places=3)
        self.assertEqual(len(found[0]["points_mm"]), 4)

    def test_two_tabs_side_by_side_are_two_ears(self):
        ring = LN.outline_corners(rows([(0, 0), (10, 0), (10, 15), (12, 15), (12, 17), (10, 17), (10, 20), (12, 20), (12, 22), (10, 22),
                                        (10, 40), (0, 40)]))
        self.assertEqual([e["base_mm"] for e in EA.ears(ring)], [[[10.0, 15.0], [10.0, 17.0]], [[10.0, 20.0], [10.0, 22.0]]])

    def test_a_corner_a_widened_end_or_a_long_arm_is_no_ear(self):
        widened = LN.outline_corners(rows([(-1, 0), (11, 0), (11, 4), (10, 4), (10, 40), (0, 40), (0, 4), (-1, 4)]))
        arm = LN.outline_corners(rows([(0, 0), (10, 0), (10, 18), (30, 18), (30, 22), (10, 22), (10, 40), (0, 40)]))
        self.assertEqual(EA.ears(widened), [])
        self.assertEqual(EA.ears(arm), [])


class ExposureTest(unittest.TestCase):
    def setUp(self):
        self.copper = OP.LayerCopper(regions=[("GND", [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
                                                        [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]])],
                                     traces=[("SIG", 0.2, [(1.0, 8.0), (9.0, 8.0)])],
                                     vias=[("PWR", (5.0, 5.0), 0.3)])

    def test_the_nets_an_opening_bares_and_those_it_comes_near(self):
        on_ground = OP.exposure(OP.rect_ring((1, 1, 3, 3)), self.copper, 0.1)
        near_trace = OP.exposure(OP.rect_ring((1, 1, 3, 7.85)), self.copper, 0.1)
        over_via = OP.exposure(OP.rect_ring((4.5, 4.5, 5.5, 5.5)), self.copper, 0.1)
        self.assertEqual(on_ground["bared"], ["GND"])
        self.assertEqual([row["net"] for row in on_ground["near"]], ["GND"])
        self.assertEqual(near_trace["near"][1]["net"], "SIG")
        self.assertAlmostEqual(near_trace["near"][1]["gap_mm"], 0.05, places=3)
        self.assertEqual(over_via["bared"], ["PWR"])  # inside the plane's hole, on the via alone

    def test_fit_finds_the_largest_rectangle_on_the_net_alone(self):
        rect = OP.fit(OP.rect_ring((0, 0, 10, 10)), self.copper, "GND", 0.1)
        x1, y1, x2, y2 = rect
        self.assertLess(y2, 7.75)
        self.assertTrue(x1 < 0.2 and x2 > 9.8 and y1 < 0.2, rect)
        self.assertEqual(OP.exposure(OP.rect_ring(rect), self.copper, 0.1)["bared"], ["GND"])
        self.assertEqual([row["net"] for row in OP.exposure(OP.rect_ring(rect), self.copper, 0.1)["near"]], ["GND"])

    def test_fit_keeps_off_copper_just_outside_its_region(self):
        copper = OP.LayerCopper(regions=[("GND", [[(-5.0, -5.0), (20.0, -5.0), (20.0, 20.0), (-5.0, 20.0)]])],
                                traces=[("SIG", 0.1, [(10.06, -5.0), (10.06, 20.0)])], vias=[("PWR", (10.2, 5.0), 0.15)])
        for margin in (0.1, 0.2):
            rect = OP.fit(OP.rect_ring((0, 0, 10, 10)), copper, "GND", margin)
            others = [row for row in OP.exposure(OP.rect_ring(rect), copper, margin)["near"] if row["net"] != "GND"]
            self.assertEqual(others, [], (margin, rect))

    def test_fit_on_a_coarse_grid_still_sees_a_thin_trace(self):
        copper = OP.LayerCopper(regions=[("GND", [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]])],
                                traces=[("SIG", 0.1, [(50.03, 0.0), (50.03, 100.0)]), ("SIG2", 0.1, [(0.0, 30.01), (100.0, 30.01)])])
        rect = OP.fit(OP.rect_ring((0, 0, 100, 100)), copper, "GND", 0.1)
        self.assertEqual([row["net"] for row in OP.exposure(OP.rect_ring(rect), copper, 0.1)["near"]], ["GND"])

    def test_a_zero_clearance_still_names_the_bared_copper(self):
        self.assertEqual(OP.exposure(OP.rect_ring((1, 1, 3, 3)), self.copper, 0.0)["bared"], ["GND"])

    def test_fit_stays_out_of_the_boxes_to_avoid(self):
        rect = OP.fit(OP.rect_ring((0, 0, 10, 10)), self.copper, "GND", 0.1, avoid=[(0.0, 0.0, 10.0, 3.0)])
        self.assertGreater(rect[1], 3.1)

    def test_a_wide_clearance_grows_quickly(self):
        import time

        copper = OP.LayerCopper(regions=[("GND", [[(0.0, 0.0), (25.0, 0.0), (25.0, 25.0), (0.0, 25.0)]]),
                                         ("PWR", [[(0.0, 12.5), (25.0, 12.5), (25.0, 25.0), (0.0, 25.0)]])])
        started = time.monotonic()
        OP.fit(OP.rect_ring((0, 0, 25, 25)), copper, "GND", 2.0)
        self.assertLess(time.monotonic() - started, 10.0)

    def test_fit_finds_nothing_where_the_net_has_no_room(self):
        self.assertIsNone(OP.fit(OP.rect_ring((4.1, 4.1, 5.9, 5.9)), self.copper, "GND", 0.1))
        self.assertIsNone(OP.fit(OP.rect_ring((20, 20, 25, 25)), self.copper, "GND", 0.1))

    def test_fit_skips_slivers_thinner_than_its_smallest_side(self):
        thin = OP.LayerCopper(regions=[("GND", [[(0.0, 0.0), (10.0, 0.0), (10.0, 0.25), (0.0, 0.25)]])])
        self.assertIsNone(OP.fit(OP.rect_ring((0, 0, 10, 1)), thin, "GND", 0.1))


if __name__ == "__main__":
    unittest.main()
