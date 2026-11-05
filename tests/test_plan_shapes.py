"""The 2-D tests the silkscreen checks share: a line, a filled shape or a disc against
a pad or a box."""

from __future__ import annotations

import math
import unittest

from xpedition_cli.adapter.plan.shapes import Outline, flatten


def _square(x1: float, y1: float, x2: float, y2: float) -> dict:
    return {"path": [[x1, y1, 0], [x2, y1, 0], [x2, y2, 0], [x1, y2, 0], [x1, y1, 0]]}


PAD = Outline(_square(0, 0, 1, 1))


class ShapesTest(unittest.TestCase):
    def test_a_line_crosses_a_pad_by_its_width(self):
        across = Outline({"path": [[-1, 0.5, 0], [2, 0.5, 0]], "width": 0.12})
        self.assertTrue(across.crosses(PAD))
        # 0.05 mm beside the pad: half of a 0.12 mm line still reaches it
        beside = Outline({"path": [[-1, 1.05, 0], [2, 1.05, 0]], "width": 0.12})
        self.assertTrue(beside.crosses(PAD))
        # 0.1 mm beside it, it does not
        clear = Outline({"path": [[-1, 1.1, 0], [2, 1.1, 0]], "width": 0.12})
        self.assertFalse(clear.crosses(PAD))
        # a line that ends inside the pad crosses it too
        self.assertTrue(Outline({"path": [[0.5, 0.5, 0], [0.5, 3, 0]], "width": 0.1}).crosses(PAD))

    def test_round_pads_rings_and_filled_shapes(self):
        disc = Outline({"circle": [0, 0, 0.5]})
        self.assertTrue(Outline({"path": [[-1, 0.5, 0], [1, 0.5, 0]], "width": 0.12}).crosses(disc))
        self.assertFalse(Outline({"path": [[-1, 0.6, 0], [1, 0.6, 0]], "width": 0.12}).crosses(disc))
        # a silkscreen circle drawn as a line around a pad, clear of it, and across it
        ring = Outline({"circle": [0.5, 0.5, 1.2], "width": 0.12})
        self.assertFalse(ring.crosses(PAD))
        self.assertTrue(Outline({"circle": [0.5, 0.5, 0.6], "width": 0.12}).crosses(PAD))
        # a filled shape overlapping the pad, and one only touching its edge
        self.assertTrue(Outline({**_square(0.9, 0.9, 2, 2), "width": 0}).crosses(PAD))
        self.assertFalse(Outline({**_square(1, 0, 2, 1), "width": 0}).crosses(PAD))
        self.assertTrue(Outline({"circle": [1.2, 0.5, 0.3]}).crosses(PAD))

    def test_a_shape_reaches_into_a_box_by_its_width_or_area(self):
        line = Outline({"path": [[0, 0, 0], [4, 0, 0]], "width": 0.2})
        self.assertTrue(line.reaches((1, -0.05, 2, 1)))
        self.assertFalse(line.reaches((1, 0.2, 2, 1)))
        self.assertTrue(PAD.reaches((0.5, 0.5, 3, 3)))
        self.assertFalse(PAD.reaches((1, 1, 3, 3)))
        self.assertTrue(Outline({"circle": [0, 0, 1]}).reaches((0.5, 0.5, 2, 2)))

    def test_flatten_turns_arc_centres_into_segments_the_signed_way(self):
        # a quarter circle from (10, 0) to (0, 10) about the origin: a negative radius
        # goes counter-clockwise, the short way here
        ccw = flatten([(10, 0, 0), (0, 0, -10), (0, 10, 0)])
        self.assertEqual((ccw[0], ccw[-1]), ((10, 0), (0, 10)))
        self.assertGreater(len(ccw), 5)
        middle = ccw[len(ccw) // 2]
        self.assertTrue(middle[0] > 0 and middle[1] > 0)  # through the first quadrant
        self.assertAlmostEqual(math.hypot(*middle), 10)
        # a positive radius goes clockwise, the long way round for these end points
        cw = flatten([(10, 0, 0), (0, 0, 10), (0, 10, 0)])
        self.assertGreater(len(cw), len(ccw))
        self.assertTrue(any(x < 0 for x, _y in cw))
        # rows without a radius are plain corners
        self.assertEqual(flatten([(0, 0, 0), (5, 0, 0), (5, 5, 0)]), [(0, 0), (5, 0), (5, 5)])

    def test_the_outline_of_a_rounded_board_path_is_its_flattened_ring(self):
        shape = Outline({"path": [[0, 0, 0], [8, 0, 0], [8, 2, -2], [10, 2, 0], [10, 10, 0], [0, 10, 0]]})
        self.assertAlmostEqual(shape.bounds[2], 10.0)
        self.assertFalse(shape.stroked)
        self.assertTrue(shape.reaches((4, 4, 6, 6)))
