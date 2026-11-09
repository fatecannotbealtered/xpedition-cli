"""The routing plan of trace create and its offline check (adapter/plan/trace_plan.py)."""
from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import PlanError
from xpedition_cli.adapter.plan import trace_plan as TP


def square(x: float, y: float, half: float = 0.2) -> list[tuple[float, float]]:
    return [(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)]


def copper() -> TP.Copper:
    """Two pads of A on layer 1 at (0, 0) and (10, 0), a pad of B at (5, 2), nets A, B and GND."""
    return TP.Copper(layer_count=4, nets={"A", "B", "GND"},
                     pads=[(1, "A", square(0.0, 0.0)), (1, "A", square(10.0, 0.0)), (1, "B", square(5.0, 2.0))])


def rules(widths: tuple[float, float] = (0.1, 0.3), gap: float = 0.15) -> TP.Rules:
    return TP.Rules(net_class_of={"A": "SIG"}, widths={"SIG": {layer: widths for layer in range(1, 5)}},
                    clearance=lambda a, b, layer, kind: gap)


def trace(points, net: str = "A", layer: int = 1, width: float = 0.1) -> dict:
    return {"net": net, "layer": layer, "width_mm": width, "points_mm": points}


class ParseTest(unittest.TestCase):
    def test_traces_and_vias_are_read_in_mm(self):
        plan = TP.parse({"traces": [trace([[0, 0], [10.00004, 0]])],
                         "vias": [{"net": "A", "at_mm": [5, 0], "padstack": "VIA_D0.2_P0.4"}]})
        self.assertEqual(plan.traces, [TP.Trace("A", 1, 0.1, [(0.0, 0.0), (10.0, 0.0)])])
        self.assertEqual((plan.vias[0].at, plan.vias[0].padstack), ((5.0, 0.0), "VIA_D0.2_P0.4"))

    def test_every_malformed_plan_is_refused_with_its_reason(self):
        cases = {
            "one JSON object": [],
            "unknown key 'nets'": {"nets": []},
            "names no trace or via": {"traces": []},
            "keys net, layer, width_mm and points_mm": {"traces": [{**trace([[0, 0], [1, 0]]), "style": 1}]},
            "a net name": {"traces": [trace([[0, 0], [1, 0]], net="")]},
            "conductor layer number": {"traces": [trace([[0, 0], [1, 0]], layer=0)]},
            "at most 25 mm": {"traces": [trace([[0, 0], [1, 0]], width=0)]},
            "at least two points": {"traces": [trace([[0, 0]])]},
            "a point [x, y]": {"traces": [trace([[0, 0], [1, "x"]])]},
            "needs a net and a padstack": {"vias": [{"net": "A", "at_mm": [0, 0]}]},
        }
        for wanted, data in cases.items():
            with self.subTest(wanted=wanted), self.assertRaises(PlanError) as caught:
                TP.parse(data)
            self.assertEqual(caught.exception.code, "E_VALIDATION")
            self.assertIn(wanted, caught.exception.message)

    def test_a_plan_has_a_size_limit(self):
        with self.assertRaises(PlanError):
            TP.parse({"traces": [trace([[0, 0], [1, 0]])] * (TP.MAX_ITEMS + 1)})


class CheckTest(unittest.TestCase):
    def test_a_route_between_two_pads_through_a_via_passes(self):
        plan = TP.parse({"traces": [trace([[0, 0], [3, 0]]), trace([[3, 0], [7, 0]], layer=4), trace([[7, 0], [10, 0]])],
                         "vias": [{"net": "A", "at_mm": [3, 0], "padstack": "V"}, {"net": "A", "at_mm": [7, 0], "padstack": "V"}]})
        for via in plan.vias:
            via.radius = 0.2
        self.assertEqual(TP.check(plan, copper(), rules()), [])

    def test_an_end_on_nothing_and_a_width_outside_the_class_are_refused(self):
        plan = TP.parse({"traces": [trace([[0, 0], [4, 0]], width=0.5)]})
        blocked = TP.check(plan, copper(), rules())
        self.assertIn("width 0.5 mm is outside its class SIG's 0.1 to 0.3 mm on layer 1", blocked[0])
        self.assertIn("its end at (4, 0) mm touches no copper of A on layer 1", blocked[1])

    def test_a_net_or_layer_the_board_lacks(self):
        plan = TP.parse({"traces": [trace([[0, 0], [10, 0]], net="NOPE"), trace([[0, 0], [10, 0]], layer=6)],
                         "vias": [{"net": "NOPE", "at_mm": [3, 0], "padstack": "V"}]})
        blocked = TP.check(plan, copper(), rules())
        self.assertIn("the board has no net NOPE", blocked[0])
        self.assertIn("there is no layer 6", blocked[1])
        self.assertIn("via 1 of NOPE: the board has no net NOPE", blocked[2])

    def test_another_nets_pad_trace_and_via_keep_their_clearance(self):
        near = TP.parse({"traces": [trace([[0, 0], [5, 1.7], [10, 0]])]})  # passes 0.1 mm below B's pad
        self.assertIn("closer to a pad of B than 0.15 mm", TP.check(near, copper(), rules())[0])
        board = copper()
        board.traces.append((1, "B", 0.1, [(0.0, 0.2), (10.0, 0.2)]))
        board.vias.append(("GND", 5.0, -0.35, 0.2))
        straight = TP.parse({"traces": [trace([[0, 0], [10, 0]])]})
        blocked = TP.check(straight, board, rules())
        self.assertIn("closer to a trace of B", blocked[0])
        self.assertIn("closer to a via of GND", blocked[1])
        self.assertEqual(TP.check(straight, board, rules(gap=0.05)), [])

    def test_two_traces_of_the_plan_keep_their_clearance(self):
        plan = TP.parse({"traces": [trace([[0, 0], [10, 0]]), trace([[2, 0.15], [8, 0.15]], net="B")]})
        blocked = TP.check(plan, copper(), rules(gap=0.1))
        self.assertTrue(any("closer to trace 2 of B in the plan" in reason for reason in blocked))

    def test_a_via_keeps_clear_of_pads_traces_and_vias(self):
        board = copper()
        board.traces.append((2, "B", 0.1, [(2.0, -5.0), (2.0, 5.0)]))
        board.vias.append(("GND", 8.0, 0.0, 0.2))
        plan = TP.parse({"vias": [{"net": "A", "at_mm": [0.3, 0], "padstack": "V"}, {"net": "A", "at_mm": [2.2, 1], "padstack": "V"},
                                  {"net": "A", "at_mm": [8.4, 0], "padstack": "V"}]})
        for via in plan.vias:
            via.radius = 0.2
        blocked = TP.check(plan, board, rules())
        self.assertIn("via 1 of A: closer to a pad of A", blocked[0])  # Layout refuses a via on its own net's pad too
        self.assertIn("via 2 of A: closer to a trace of B", blocked[1])
        self.assertIn("via 3 of A: closer to a via of GND", blocked[2])


if __name__ == "__main__":
    unittest.main()


class CornerTest(unittest.TestCase):
    def test_a_corner_that_barely_turns_gets_no_arc(self):
        rows = TP.drawn([(0.0, 0.0), (0.0, 40.0), (0.0002, 80.0)], [0.0, 0.5, 0.0])
        self.assertEqual(rows, [(0.0, 0.0, 0.0), (0.0, 40.0, 0.0), (0.0002, 80.0, 0.0)])  # no arc a micrometre long

    def test_a_rounded_corner_is_drawn_as_an_arc_tangent_to_both_segments(self):
        plan = TP.parse({"traces": [trace([[0, 0], [10, 0, 2.0], [10, 10]])]})
        rows = TP.drawn(plan.traces[0].points, plan.traces[0].radii)
        # a left turn: start, centre with a negative (counter-clockwise) radius, end
        self.assertEqual([tuple(round(v, 6) for v in row) for row in rows],
                         [(0.0, 0.0, 0.0), (8.0, 0.0, 0.0), (8.0, 2.0, -2.0), (10.0, 2.0, 0.0), (10.0, 10.0, 0.0)])
        right = TP.drawn([(0.0, 0.0), (10.0, 0.0), (10.0, -10.0)], [0.0, 2.0, 0.0])
        self.assertEqual(round(right[2][2], 6), 2.0)  # a right turn is clockwise
        self.assertAlmostEqual(TP.length(plan.traces[0]), 8 + 8 + 3.14159 * 2 / 2, places=2)

    def test_the_check_measures_along_the_arc(self):
        # B's pad sits just outside the corner at (10, 0): the sharp corner comes within 0.08 mm of it, the arc stays clear
        pads = TP.Copper(layer_count=4, nets={"A", "B"}, pads=[(1, "A", square(0.0, 0.0)), (1, "A", square(10.0, 10.0)),
                                                                 (1, "B", square(10.12, -0.12, 0.03))])
        sharp = TP.parse({"traces": [trace([[0, 0], [10, 0], [10, 10]])]})
        round_ = TP.parse({"traces": [trace([[0, 0], [10, 0, 1.0], [10, 10]])]})
        self.assertTrue(TP.check(sharp, pads, rules()))
        self.assertEqual(TP.check(round_, pads, rules()), [])

    def test_a_radius_that_does_not_fit_or_one_on_an_end_is_refused(self):
        for points in ([[0, 0], [1, 0, 2.0], [1, 1]], [[0, 0, 1.0], [5, 0]], [[0, 0], [5, 0, -1], [5, 5]], [[0, 0], [5, 0, 1, 2], [5, 5]]):
            with self.subTest(points=points), self.assertRaises(PlanError) as caught:
                TP.parse({"traces": [trace(points)]})
            self.assertEqual(caught.exception.code, "E_VALIDATION")
