"""A board's shape cut into hatched and solid pieces by bend zones at a slant (plan/bands.py and the ties across them)."""
from __future__ import annotations

import math
import unittest

from xpedition_cli.adapter.plan import bands as B
from xpedition_cli.adapter.plan import board_metrics as BM
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
        self.assertEqual(len(B.reaching([(0.2, 20.0), (3.8, 20.0), (0.2, 20.0)], U_BOARD, 1.0)), 2)  # a zone drawn as a line


class TiesAcrossTest(unittest.TestCase):
    def test_a_zone_drawn_with_its_first_point_again_at_the_end_cuts_the_same(self):
        zone = [(-1.0, 20.0), (5.0, 20.0), (5.0, 22.0), (-1.0, 22.0)]
        closed = [*zone, zone[0]]  # a keepout's outline as Layout gives it
        self.assertEqual([round(B.area(p), 4) for p in B.inside(U_BOARD, closed)], [round(B.area(p), 4) for p in B.inside(U_BOARD, zone)])
        self.assertAlmostEqual(sum(B.area(p) for p in B.inside(U_BOARD, closed) + B.outside(U_BOARD, closed)), B.area(U_BOARD), places=6)

    def test_a_side_marked_in_pieces_along_the_edge_moves_out_as_one(self):
        zone = [(0.2, 20.0), (3.8, 20.0), (3.8, 21.0), (3.8, 22.0), (0.2, 22.0), (0.2, 21.0)]  # each long side in two pieces
        wide = B.reaching(zone, U_BOARD, 1.0)
        self.assertEqual(sorted({round(x, 6) for x, _y in wide}), [-0.8, 4.8])  # the points between the pieces move out 1 mm, not 2

    def test_ties_cross_a_slanted_zone_square_to_it(self):
        c, s = math.cos(math.radians(20.0)), math.sin(math.radians(20.0))
        local = [(-3.0, -1.0), (3.0, -1.0), (3.0, 1.0), (-3.0, 1.0)]  # 6 mm long, 2 mm across, turned 20 degrees
        zone = [(10 + x * c - y * s, 10 + x * s + y * c) for x, y in local]
        found = BT.ties_across(zone, "GND", 2, 0.1, TP.Copper(layer_count=3), lambda a, b: True)
        self.assertEqual(len(found), 3)
        for (ax, ay, _), (bx, by, _) in found:
            a, b = (ax, ay), (bx, by)
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

    def test_ties_turn_across_a_zone_over_the_turn_of_an_arm(self):
        # the arm runs up, then leans 20 degrees right: the zone's lower side is level, its upper side square to the lean
        c, s = math.cos(math.radians(20.0)), math.sin(math.radians(20.0))
        top_right = (4.0 + 4 * s, 2.0 + 4 * c)
        top_left = (top_right[0] - 4 * c, top_right[1] + 4 * s)
        zone = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), top_right, top_left, (0.0, 2.0)]
        board = [(0.0, -5.0), (4.0, -5.0), (4.0, 2.0), top_right, (top_right[0] + 5 * s, top_right[1] + 5 * c),
                 (top_left[0] + 5 * s, top_left[1] + 5 * c), top_left, (0.0, 2.0)]  # the arm, the zone's other sides on its edges
        sides = B.crossed_sides(zone, board)
        self.assertEqual(sides, [((0.0, 0.0), (4.0, 0.0)), (top_right, top_left)])  # the lower and upper sides, inside the board
        found = BT.ties_turning(zone, (sides[0], sides[1]), "GND", 2, 0.1, TP.Copper(layer_count=3), lambda a, b: True)
        self.assertEqual(len(found), 3)
        for rows in found:
            self.assertEqual(len(rows), 5)  # in, the arc's start, its centre with the radius, its end, out
            (x0, y0, _), (x1, y1, _), (_cx, _cy, r), (x2, y2, _), (x3, y3, _) = rows
            self.assertAlmostEqual(x0, x1, places=6)  # square to the lower side
            self.assertAlmostEqual(math.degrees(math.atan2(y3 - y2, x3 - x2)), 70.0, places=6)  # square to the upper side
            self.assertGreater(r, 0)  # turning right from straight up: clockwise, a positive radius
            self.assertGreaterEqual(abs(r), BM.BEND_ARC_MM)  # too wide to count as a turn in the zone
            self.assertAlmostEqual(y0, -BT.EXTEND_MM, places=6)
        model = {"traces": [{"net": "GND", "layer": 2, "width": 0.1, "path": [list(row) for row in rows]} for rows in found]}
        self.assertEqual([BM._path_turns(t) for t in model["traces"]], [[], [], []])
        self.assertEqual(BT.ties_turning(zone, (sides[0], sides[1]), "GND", 2, 0.1, TP.Copper(layer_count=3), lambda a, b: False), [])


if __name__ == "__main__":
    unittest.main()
