"""Lanes of a routing plan: traces that follow an edge of the board outline at a distance."""
from __future__ import annotations

import math
import unittest

from xpedition_cli.adapter.plan import PlanError
from xpedition_cli.adapter.plan import lanes as LN
from xpedition_cli.adapter.plan import trace_plan as TP
from xpedition_cli.adapter.plan.outline import rounded


def _rows(x0: float, y0: float, x1: float, y1: float, radius: float) -> list[tuple[float, float, float]]:
    return list(zip(*rounded(x0, y0, x1, y1, radius), strict=True))


def _lane(**changes: object) -> dict:
    lane = {"net": "A", "layer": 1, "width_mm": 0.1, "from_mm": [[5, 5], [5, 8, 0.5]], "to_mm": [[5, 92, 0.5], [5, 95]],
            "edge_mm": [[0, 1], [0, 99]], "offset_mm": 2}
    return {**lane, **changes}


class OutlineTest(unittest.TestCase):
    def test_a_rounded_rectangle_is_four_corners_with_their_radius(self):
        ring = LN.outline_corners(_rows(0, 0, 20, 100, 1))
        self.assertEqual(sorted((round(p[0], 6), round(p[1], 6), r) for p, r in ring),
                         [(0, 0, 1), (0, 100, 1), (20, 0, 1), (20, 100, 1)])

    def test_an_offset_keeps_the_turns_concentric(self):
        chain = [((0.0, 0.0), 0.0), ((0.0, 50.0), 10.0), ((50.0, 50.0), 0.0)]  # up, then right: the board on the right
        inner = LN.offset_chain(chain, 2.0, left=False)
        outer = LN.offset_chain(chain, 2.0, left=True)
        self.assertEqual([(round(p[0], 6), round(p[1], 6), r) for p, r in inner], [(2, 0, 0), (2, 48, 8), (50, 48, 0)])
        self.assertEqual([(round(p[0], 6), round(p[1], 6), r) for p, r in outer], [(-2, 0, 0), (-2, 52, 12), (50, 52, 0)])
        sharp = LN.offset_chain([((0.0, 0.0), 0.0), ((0.0, 50.0), 0.0), ((50.0, 50.0), 0.0)], 2.0, left=True)
        self.assertEqual(sharp[1][1], 2.0)  # a sharp corner turning away gets an arc of the distance about it


class ExpandTest(unittest.TestCase):
    def test_a_lane_runs_parallel_to_the_edge_between_its_ends(self):
        ring = LN.outline_corners(_rows(0, 0, 20, 100, 1))
        lane = LN.parse([_lane()])[0]
        trace = LN.expand(lane, ring, "lanes[0]")
        self.assertEqual([(round(x, 6), round(y, 6)) for x, y in trace.points], [(5, 5), (5, 8), (2, 8), (2, 92), (5, 92), (5, 95)])
        self.assertEqual(trace.radii, [0.0, 0.5, 0.5, 0.5, 0.5, 0.0])

    def test_lanes_follow_an_arc_of_the_outline_about_its_centre(self):
        # a board 20 mm wide that runs up and turns right with an outer radius of 30 mm
        rows = [(0.0, 0.0, 0.0), (20.0, 0.0, 0.0), (20.0, 50.0, 0.0), (30.0, 50.0, 10.0), (30.0, 60.0, 0.0), (80.0, 60.0, 0.0),
                (80.0, 80.0, 0.0), (30.0, 80.0, 0.0), (30.0, 50.0, -30.0), (0.0, 50.0, 0.0), (0.0, 0.0, 0.0)]
        ring = LN.outline_corners(rows)
        lengths = []
        for offset in (2.0, 4.0):
            lane = LN.parse([{"net": "A", "layer": 1, "width_mm": 0.1, "from_mm": [[offset, 5]], "to_mm": [[75, 80 - offset]],
                              "edge_mm": [[0, 10], [70, 80]], "offset_mm": offset, "radius_mm": 0}])[0]
            trace = LN.expand(lane, ring, "lanes[0]")
            arc = [p for p in trace.path if p[0] < 30 and p[1] > 50]
            radii = {round(math.dist(p, (30, 50)), 6) for p in arc}
            self.assertEqual(radii, {30 - offset})  # concentric with the outline's arc
            lengths.append(TP.length(trace))
        self.assertAlmostEqual(lengths[0] - lengths[1], math.pi, places=2)  # the arc 2 mm further out, a quarter turn, as chords

    def test_a_lane_runs_straight_past_a_small_tab_but_around_a_notch(self):
        # a tab 2 mm out of the left edge at y 56 to 60, and a notch 2 mm into it at y 30 to 34
        rows = [(0.0, 0.0, 0.0), (20.0, 0.0, 0.0), (20.0, 100.0, 0.0), (0.0, 100.0, 0.0), (0.0, 60.0, 0.0), (-2.0, 60.0, 0.0),
                (-2.0, 56.0, 0.0), (0.0, 56.0, 0.0), (0.0, 34.0, 0.0), (2.0, 34.0, 0.0), (2.0, 30.0, 0.0), (0.0, 30.0, 0.0), (0.0, 0.0, 0.0)]
        ring = LN.outline_corners(rows)
        lane = LN.parse([_lane(radius_mm=0)])[0]
        trace = LN.expand(lane, ring, "lanes[0]")
        xs = {round(x, 6) for x, y in trace.points if 9 < y < 91}
        self.assertEqual(xs, {2.0, 4.0})  # 2 mm from the edge and around the notch, never out into the tab

    def test_a_lane_is_refused_with_its_name(self):
        for wrong, text in (({"offset_mm": 0}, "offset_mm"), ({"edge_mm": [[0, 1]]}, "edge_mm"), ({"from_mm": [[5, 5, 1]]}, "rounds no corner"),
                            ({"via": 1}, "has the keys")):
            with self.assertRaises(PlanError) as caught:
                LN.parse([_lane(**wrong)])
            self.assertIn(text, caught.exception.message)
        ring = LN.outline_corners(_rows(0, 0, 20, 100, 1))
        with self.assertRaises(PlanError) as caught:
            LN.expand(LN.parse([_lane(edge_mm=[[0, 50], [0, 50]])])[0], ring, "lanes[3]")
        self.assertIn("lanes[3].edge_mm names no edge", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
