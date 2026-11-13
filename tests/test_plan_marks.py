"""Where a part's pads lie on the marks a structure drawing left on the board."""
from __future__ import annotations

import math
import unittest

from xpedition_cli.adapter.plan import marks as MK


def _rect(cx: float, cy: float, w: float, h: float) -> list[list[float]]:
    return [[cx - w / 2, cy - h / 2, 0], [cx + w / 2, cy - h / 2, 0], [cx + w / 2, cy + h / 2, 0], [cx - w / 2, cy + h / 2, 0]]


def _connector() -> list[tuple[str, MK.Shape]]:
    """Two rows of five signal pads at a 0.4 mm pitch and two larger pads holding it down at its ends, about its origin."""
    pads = []
    for i in range(5):
        x = -0.8 + i * 0.4
        pads.append((str(i + 1), MK.path_shape(_rect(x, -0.6, 0.2, 0.5))))
        pads.append((str(i + 6), MK.path_shape(_rect(x, 0.6, 0.2, 0.5))))
    pads.append(("M1", MK.path_shape(_rect(-1.6, 0.0, 0.5, 0.8))))
    pads.append(("M2", MK.path_shape(_rect(1.6, 0.0, 0.5, 0.8))))
    return pads


def _moved(pads: list[tuple[str, MK.Shape]], degrees: float, dx: float, dy: float) -> list[MK.Shape]:
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [(x * c - y * s + dx, x * s + y * c + dy, area) for _pin, (x, y, area) in pads]


class MarksTest(unittest.TestCase):
    def test_a_rectangle_has_its_centre_and_area(self):
        x, y, area = MK.path_shape(_rect(2.0, 3.0, 0.2, 0.5))
        self.assertAlmostEqual(x, 2.0)
        self.assertAlmostEqual(y, 3.0)
        self.assertAlmostEqual(area, 0.1)
        self.assertIsNone(MK.path_shape([[0, 0, 0], [1, 0, 0]]))

    def test_the_part_turns_and_moves_onto_marks_of_its_pads(self):
        pads = _connector()
        marks = _moved(pads, 30.0, 10.0, 5.0)
        best, others = MK.choose(MK.fits(pads, marks), 0.0, 0.0, 0.0)
        self.assertEqual(len(best["matched"]), len(pads))
        self.assertAlmostEqual(best["rotation_deg"], 30.0, places=3)
        self.assertAlmostEqual(best["x_mm"], 10.0, places=3)
        self.assertAlmostEqual(best["y_mm"], 5.0, places=3)
        self.assertLess(best["residual_mm"], 1e-6)
        self.assertEqual([round(o["rotation_deg"]) for o in others], [210])  # its pads are symmetric: turned round it fits too

    def test_a_rotation_asked_for_picks_among_the_fits(self):
        pads = _connector()
        marks = _moved(pads, 30.0, 10.0, 5.0)
        best, _others = MK.choose(MK.fits(pads, marks), 0.0, 0.0, 0.0, wanted_rotation=210.0)
        self.assertAlmostEqual(best["rotation_deg"], 210.0, places=3)
        self.assertEqual(MK.choose(MK.fits(pads, marks), 0.0, 0.0, 0.0, wanted_rotation=90.0), (None, []))

    def test_the_nearest_of_two_marked_places_wins(self):
        pads = _connector()
        marks = _moved(pads, 0.0, 20.0, 0.0) + _moved(pads, 0.0, 4.0, 0.0)
        best, others = MK.choose(MK.fits(pads, marks), 0.0, 0.0, 0.0)
        self.assertEqual((round(best["x_mm"], 3), round(best["rotation_deg"], 3)), (4.0, 0.0))
        self.assertIn(20.0, [round(o["x_mm"], 3) for o in others])

    def test_marks_of_other_shapes_give_no_fit(self):
        pads = _connector()
        marks = [(x, y, area * 2.0) for x, y, area in _moved(pads, 0.0, 3.0, 3.0)]
        self.assertEqual(MK.fits(pads, marks), [])
        few = _moved(pads[:2], 0.0, 3.0, 3.0)  # two pads' marks alone are too few to place it by
        self.assertEqual(MK.fits(pads, few), [])


if __name__ == "__main__":
    unittest.main()
