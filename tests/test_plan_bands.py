"""A board's shape cut into hatched and solid pieces by bend zones at a slant (plan/bands.py and the ties across them)."""
from __future__ import annotations

import math
import unittest

from xpedition_cli.adapter.plan import bands as B
from xpedition_cli.adapter.plan import bend_ties as BT
from xpedition_cli.adapter.plan import trace_plan as TP

U_BOARD = [(0.0, 0.0), (10.0, 0.0), (10.0, 30.0), (6.0, 30.0), (6.0, 10.0), (4.0, 10.0), (4.0, 30.0), (0.0, 30.0)]  # two arms


class BandsTest(unittest.TestCase):
    def test_a_slanted_zone_cuts_the_shape_into_pieces_that_cover_it_once(self):
        zone = [(-1.0, 20.0), (5.0, 21.0), (5.0, 23.0), (-1.0, 22.0)]  # across the left arm only
        inside, outside = B.inside(U_BOARD, zone), B.outside(U_BOARD, zone)
        self.assertEqual([round(B.area(p), 4) for p in inside], [8.0])  # 4 mm of arm by 2 mm of zone
        self.assertAlmostEqual(sum(B.area(p) for p in inside + outside), B.area(U_BOARD), places=6)
        across = [(-1.0, 20.0), (11.0, 20.0), (11.0, 22.0), (-1.0, 22.0)]
        self.assertEqual(len(B.inside(U_BOARD, across)), 2)  # one piece in each arm, not joined along the cut

    def test_a_zone_marked_inside_the_edges_reaches_past_them(self):
        zone = [(0.2, 20.0), (3.8, 20.0), (3.8, 22.0), (0.2, 22.0)]  # across the left arm, 0.2 mm inside its edges
        wide = B.reaching(zone, U_BOARD, 1.0)
        self.assertEqual(sorted((round(x, 6), round(y, 6)) for x, y in wide), [(-0.8, 20.0), (-0.8, 22.0), (4.8, 20.0), (4.8, 22.0)])
        self.assertAlmostEqual(sum(B.area(p) for p in B.inside(U_BOARD, wide)), 8.0, places=6)
        along = [(0.2, 12.0), (3.8, 12.0), (3.8, 28.0), (0.2, 28.0)]  # a whole arm as one zone: it reaches out across, not along
        reached = B.reaching(along, U_BOARD, 1.0)
        self.assertEqual(sorted({round(y, 6) for _x, y in reached}), [12.0, 28.0])


class TiesAcrossTest(unittest.TestCase):
    def test_ties_cross_a_slanted_zone_square_to_it(self):
        c, s = math.cos(math.radians(20.0)), math.sin(math.radians(20.0))
        local = [(-3.0, -1.0), (3.0, -1.0), (3.0, 1.0), (-3.0, 1.0)]  # 6 mm long, 2 mm across, turned 20 degrees
        zone = [(10 + x * c - y * s, 10 + x * s + y * c) for x, y in local]
        found = BT.ties_across(zone, "GND", 2, 0.1, TP.Copper(layer_count=3), lambda a, b: True)
        self.assertEqual(len(found), 3)
        for a, b in found:
            direction = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180
            self.assertAlmostEqual(direction, 110.0, places=6)  # square to the zone's long sides
            self.assertAlmostEqual(math.dist(a, b), 2.0 + 2 * BT.EXTEND_MM, places=6)  # across it and into the copper beside
        self.assertEqual(BT.ties_across(zone, "GND", 2, 0.1, TP.Copper(layer_count=3), lambda a, b: False), [])
        arm = [(0.2, 12.0), (3.8, 12.0), (3.8, 28.0), (0.2, 28.0)]  # a whole arm as one zone: traces cross it along the arm
        run = B.edge_run(arm, U_BOARD)
        self.assertEqual((abs(run[0]), round(abs(run[1]), 6)), (0.0, 3.6))  # square to its ends, the sides inside the board
        ties = BT.ties_across(arm, "GND", 2, 0.1, TP.Copper(layer_count=3), lambda a, b: True, run)
        self.assertTrue(all(abs(a[0] - b[0]) < 1e-9 for a, b in ties))  # each along the arm, between its ends
        self.assertAlmostEqual(abs(ties[0][1][1] - ties[0][0][1]), 16.0 + 2 * BT.EXTEND_MM)


if __name__ == "__main__":
    unittest.main()
