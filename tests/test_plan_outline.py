"""A board's rounded-rectangle outline as the three contours Layout keeps."""

from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import PlanError, outline


class RoundedTest(unittest.TestCase):
    def test_rounded_outline_points_carry_the_arc_centre_with_a_negative_radius(self):
        xs, ys, rs = outline.rounded(0.0, 0.0, 60.0, 45.0, 3.0)
        self.assertEqual(len(xs), 13)
        self.assertEqual((xs[0], ys[0], rs[0]), (57.0, 0.0, 0.0))
        self.assertEqual([i for i, r in enumerate(rs) if r], [1, 4, 7, 10])
        self.assertEqual({r for r in rs if r}, {-3.0})
        self.assertEqual((xs[1], ys[1]), (57.0, 3.0))  # the centre of the first corner's arc
        self.assertEqual((xs[-1], ys[-1]), (xs[0], ys[0]))  # closed
        self.assertTrue(outline.closed([xs, ys, rs]))

    def test_square_corners_are_five_points(self):
        plain = outline.rounded(0.0, 0.0, 60.0, 45.0, 0.0)
        self.assertEqual(plain, [[0.0, 60.0, 60.0, 0.0, 0.0], [0.0, 0.0, 45.0, 45.0, 0.0], [0.0] * 5])


class TargetsTest(unittest.TestCase):
    def test_the_route_border_sits_inside_the_board_outline(self):
        target = outline.targets(50.0, 40.0, 2.0)
        self.assertEqual(set(target), set(outline.OUTLINE_MEMBERS))
        board = target["board_outline"]
        border = target["route_border"]
        self.assertEqual(board["points_mm"], outline.rounded(0.0, 0.0, 50.0, 40.0, 2.0))
        self.assertEqual(target["manufacturing_outline"]["points_mm"], board["points_mm"])
        self.assertIsNot(target["manufacturing_outline"]["points_mm"], board["points_mm"])
        xs, ys, rs = border["points_mm"]
        self.assertAlmostEqual(min(xs), outline.INSET_MM)
        self.assertAlmostEqual(max(xs), 50.0 - outline.INSET_MM)
        self.assertAlmostEqual(min(ys), outline.INSET_MM)
        self.assertAlmostEqual({r for r in rs if r}.pop(), -(2.0 - outline.INSET_MM))
        for record in target.values():
            self.assertEqual((record["cutout_count"], record["closed"]), (0, True))

    def test_a_radius_smaller_than_the_inset_gives_the_border_square_corners(self):
        border = outline.targets(20.0, 10.0, 0.2)["route_border"]["points_mm"]
        self.assertEqual(len(border[0]), 5)
        self.assertFalse(any(border[2]))

    def test_impossible_outlines_are_refused(self):
        for width, height, radius in ((0.5, 10, 0), (10, 0.6, 0), (10, 10, -1), (10, 10, 5), (float("nan"), 10, 0), ("10", 10, 0)):
            with self.subTest(width=width, height=height, radius=radius):
                with self.assertRaises(PlanError) as caught:
                    outline.targets(width, height, radius)
                self.assertEqual(caught.exception.code, "E_VALIDATION")


class ExpectationTest(unittest.TestCase):
    def test_a_board_that_already_has_the_outline_is_a_no_op(self):
        target = outline.targets(30.0, 20.0, 1.0)
        result = outline.expectation({"width": 30, "height": 20, "radius": 1}, {"objects": target})
        self.assertEqual(result["requested"], {"width": 30.0, "height": 20.0, "radius": 1.0})
        self.assertEqual(result["target"], target)
        self.assertIs(result["no_op"], True)

    def test_another_outline_or_none_read_is_a_change(self):
        before = outline.targets(30.0, 20.0, 0.0)
        self.assertIs(outline.expectation({"width": 30, "height": 20, "radius": 1}, {"objects": before})["no_op"], False)
        self.assertIs(outline.expectation({"width": 30, "height": 20})["no_op"], False)
        self.assertEqual(outline.expectation({"width": 30, "height": 20})["requested"]["radius"], 0.0)

    def test_shapes_compare_within_the_tolerance(self):
        record = outline.targets(30.0, 20.0, 1.0)["board_outline"]
        nudged = {**record, "points_mm": [[v + 1e-9 for v in row] for row in record["points_mm"]]}
        self.assertTrue(outline.same_shape(record, nudged))
        moved = {**record, "points_mm": [[v + 0.01 for v in row] for row in record["points_mm"]]}
        self.assertFalse(outline.same_shape(record, moved))
        self.assertFalse(outline.same_shape(record, {**record, "cutout_count": 1}))
        self.assertFalse(outline.same_shape(record, {**record, "points_mm": [[0, 1], [0, 1]]}))
        self.assertFalse(outline.same_points(record["points_mm"], "not points"))

    def test_a_polygon_outline_rounds_its_corners_and_insets_its_route_border(self):
        target = outline.polygon_targets([[0, 0, 1], [90, 0, 1], [90, 18, 1], [34, 18], [34, 20, 0.5], [30, 20, 0.5], [30, 18],
                                          [0, 18, 1]])
        main = list(zip(*target["board_outline"]["points_mm"]))
        border = list(zip(*target["route_border"]["points_mm"]))
        self.assertEqual(main[0], (45.0, 0.0, 0.0))  # it starts halfway along its first side, so every corner takes its arc
        self.assertIn((89.0, 1.0, -1.0), main)  # a rounded corner's centre, counter-clockwise
        self.assertEqual((min(p[1] for p in border), max(p[0] for p in border)), (0.3, 89.7))
        self.assertTrue(outline.same_shape(target["board_outline"], target["manufacturing_outline"]))

    def test_a_clockwise_polygon_is_turned_and_a_wrong_one_refused(self):
        clockwise = outline.polygon_targets([[0, 0], [0, 10], [10, 10], [10, 0]])
        xs, ys = clockwise["board_outline"]["points_mm"][:2]
        area = sum(xs[i - 1] * ys[i] - xs[i] * ys[i - 1] for i in range(len(xs))) / 2
        self.assertGreater(area, 0)
        for points, words in (([[0, 0], [10, 0]], "three or more corners"), ([[0, 0], [10, 10], [10, 0], [0, 10]], "cross"),
                              ([[0, 0, 6], [10, 0, 6], [10, 10], [0, 10]], "radius"), ([[0, 0], [10, 0, -1], [10, 10]], "negative")):
            with self.assertRaises(PlanError) as caught:
                outline.polygon_targets(points)
            self.assertIn(words, caught.exception.message)


if __name__ == "__main__":
    unittest.main()
