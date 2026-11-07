"""Where via create puts stitching vias (plan/stitching.py)."""
from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import PlanError
from xpedition_cli.adapter.plan import stitching as S

OUTLINE = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
CLEAR = S.Clearances(via_to_pad=0.15, via_to_trace=0.15, via_to_via=0.2)


def board(**extra) -> S.Board:
    return S.Board(outline=OUTLINE, planes={2: [OUTLINE], 3: [OUTLINE]}, **extra)


class GridTest(unittest.TestCase):
    def test_the_grid_is_centred_and_keeps_the_margin(self):
        points = S.grid(OUTLINE, 1.0, 0.5)
        xs, ys = sorted({x for x, _ in points}), sorted({y for _, y in points})
        self.assertEqual(xs[0], 10 - xs[-1])  # centred both ways
        self.assertEqual(ys[0], 5 - ys[-1])
        self.assertTrue(all(min(x, 10 - x) >= 0.5 and min(y, 5 - y) >= 0.5 for x, y in points))
        self.assertEqual(len(xs) - 1, round((xs[-1] - xs[0]) / 1.0))

    def test_a_pitch_of_zero_or_a_huge_grid_is_refused(self):
        for pitch in (0.0, 0.001):
            with self.subTest(pitch=pitch), self.assertRaises(PlanError):
                S.grid(OUTLINE, pitch, 0.5)


class StitchTest(unittest.TestCase):
    def test_points_keep_clear_of_pads_other_traces_vias_and_holes(self):
        b = board(pads=[S.polygon(circle=[5.0, 2.5, 0.3])], traces=[([(0.0, 1.0), (10.0, 1.0)], 0.1, "SIG"),
                                                                    ([(0.0, 4.0), (10.0, 4.0)], 0.1, "GND")],
                  vias=[(2.0, 2.0)], holes=[(8.0, 3.0, 0.8)])
        plan = S.stitch("GND", 0.2, b, CLEAR, 1.0, 0.5)
        self.assertTrue(plan.points)
        for point in plan.points:
            self.assertIsNone(S.free(point, "GND", 0.2, b, CLEAR))
        self.assertIn("too close to a trace of another net", plan.dropped)
        self.assertIn("too close to a pad", plan.dropped)
        self.assertTrue(any(abs(y - 4.0) < 0.3 for _x, y in plan.points))  # its own net's trace is no obstacle

    def test_copper_on_two_layers_is_needed(self):
        with self.assertRaises(PlanError) as caught:
            S.stitch("GND", 0.2, S.Board(outline=OUTLINE, planes={2: [OUTLINE]}), CLEAR, 1.0, 0.5)
        self.assertEqual(caught.exception.code, "E_CONFLICT")
        half = [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)]
        plan = S.stitch("GND", 0.2, S.Board(outline=OUTLINE, planes={2: [OUTLINE], 3: [half]}), CLEAR, 1.0, 0.5)
        self.assertTrue(all(x < 5.0 for x, _y in plan.points))
        self.assertIn("outside the net's copper on two layers", plan.dropped)

    def test_a_cutout_keeps_vias_its_clearance_away(self):
        slot = S.polygon([[4.5, 1.5, 0.0], [5.5, 1.5, 0.0], [5.5, 3.5, 0.0], [4.5, 3.5, 0.0], [4.5, 1.5, 0.0]])
        b = board(cutouts=[slot])
        plan = S.stitch("GND", 0.2, b, CLEAR, 1.0, 0.5)
        self.assertTrue(all(S.distance_to_polygon(p, slot) >= 0.2 + S.CUTOUT_CLEARANCE_MM - 1e-9 for p in plan.points))
        self.assertIn("too close to a cutout", plan.dropped)
        self.assertEqual(S.free((5.0, 2.5), "GND", 0.2, b, CLEAR), "too close to a cutout")

    def test_new_vias_keep_their_distance_from_each_other(self):
        plan = S.stitch("GND", 0.4, board(), CLEAR, 0.5, 0.5)
        for i, a in enumerate(plan.points):
            for b in plan.points[i + 1:]:
                self.assertGreaterEqual(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5, 2 * 0.4 + 0.2 - 1e-9)


if __name__ == "__main__":
    unittest.main()
