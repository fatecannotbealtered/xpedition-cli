"""The fan-out of a two-row connector, and lanes that start from it."""
from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import PlanError
from xpedition_cli.adapter.plan import fanout as FO
from xpedition_cli.adapter.plan import lanes as LN
from xpedition_cli.adapter.plan.outline import rounded

PITCH, FAR_Y, NEAR_Y = 0.35, 0.933, 2.493


def _pin(number: int, net: str | None, x: float, y: float) -> FO.Pin:
    return FO.Pin(str(number), net, (x, y), (x - 0.09, y - 0.275, x + 0.09, y + 0.275), 3)


def _connector() -> list[FO.Pin]:
    """Columns from the left: far row then near row above it, the board's body above both."""
    far = ["GND", "A", "B", "(Net0)", "P", "P", "P", "C"]
    near = ["GND", "N1", "N2", "GND", "N3", "N4", "N5", "GND"]
    pins = [_pin(i + 1, net, 1.0 + i * PITCH, FAR_Y) for i, net in enumerate(far)]
    pins += [_pin(i + 31, net, 1.0 + i * PITCH, NEAR_Y) for i, net in enumerate(near)]
    return pins


class FanoutTest(unittest.TestCase):
    def setUp(self):
        self.fan = FO.plan(_connector(), (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, set(), 0.1)

    def test_the_far_row_changes_layer_through_staggered_vias_between_the_rows(self):
        spots = {net: at for net, at in self.fan.vias}
        self.assertEqual(sorted({round(y, 3) for _net, (_x, y) in self.fan.vias}), [1.508, 1.918])
        self.assertEqual(round(spots["A"][1], 3), 1.918)  # the second column, staggered from the first
        self.assertEqual(self.fan.exits["A"], [(spots["A"], 1)])  # A leaves on layer 1 from its via
        self.assertEqual(self.fan.exits["N1"], [((1.35, NEAR_Y), 3)])  # the near row leaves from its pad
        self.assertEqual(self.fan.toward, (0.0, 1.0))

    def test_shared_and_free_vias_joins_and_loose_pins(self):
        gnd = [at for net, at in self.fan.vias if net == "GND"]
        self.assertEqual(len(gnd), 2)  # the first column, shared by both GND pins, and the free (Net0) column
        stubs = [pts for net, _layer, _w, pts in self.fan.traces if net == "GND"]
        self.assertIn([(1.0, NEAR_Y), gnd[0]], stubs)  # the near GND pin shares the far one's via
        self.assertEqual(self.fan.loose, ["38 of GND"])  # the GND pin over C's via has none
        joins = [pts for net, _layer, _w, pts in self.fan.traces if net == "P" and pts[0][1] == pts[1][1]]
        self.assertEqual(len(joins), 2)  # three consecutive P pins joined pin to pin
        self.assertNotIn("GND", self.fan.exits)

    def test_a_net_that_gives_way_leaves_its_via_to_a_plane_pin_above(self):
        pins = _connector()
        next(p for p in pins if p.number == "36").net = "GND"  # the near pin over the middle P pin
        fan = FO.plan(pins, (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, set(), 0.1, {"P"})
        self.assertEqual(len([n for n, _at in fan.vias if n == "P"]), 2)  # the middle P pin gives its via away
        self.assertIn(("GND", (2.75, 1.918)), [(n, (round(x, 3), round(y, 3))) for n, (x, y) in fan.vias])
        self.assertEqual(len([pts for n, _l, _w, pts in fan.traces if n == "P" and pts[0][1] == pts[1][1]]), 2)  # still joined
        self.assertEqual(pins[0].net, "GND")  # the caller's pins are left as they were

    def test_only_the_nets_named_are_fanned_out(self):
        fan = FO.plan(_connector(), (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, set(), 0.1, only={"A"})
        self.assertEqual(sorted({net for net, _at in fan.vias}), ["A", "GND"])  # the plane net keeps its vias
        self.assertFalse([pts for net, _l, _w, pts in fan.traces if net == "P"])  # nor joined pin to pin

    def test_a_near_pin_whose_lane_runs_on_another_layer_changes_layer_past_the_row(self):
        fan = FO.plan(_connector(), (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, set(), 0.1, lift={"N1": 1, "N2": 1, "N3": 3})
        spots = {net: at for net, at in fan.vias}
        self.assertEqual(fan.exits["N1"], [(spots["N1"], 1)])
        self.assertEqual(fan.exits["N3"], [((2.4, NEAR_Y), 3)])  # its lane runs on the pads' layer: no via
        near_edge = NEAR_Y + 0.275
        self.assertAlmostEqual(min(spots["N1"][1], spots["N2"][1]), near_edge + 0.1 + 0.2, places=6)  # just past the near row
        self.assertGreater(abs(spots["N2"][1] - spots["N1"][1]), 0.2)  # staggered from its neighbour
        self.assertIn([(1.35, NEAR_Y), spots["N1"]], [pts for net, _l, _w, pts in fan.traces if net == "N1"])
        self.assertGreater(fan.clear_at, max(spots["N1"][1], spots["N2"][1]))  # the pad layer leaves past them

    def test_a_skipped_net_is_left_to_the_caller(self):
        fan = FO.plan(_connector(), (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, {"P"}, 0.1)
        self.assertFalse([at for net, at in fan.vias if net == "P"])
        self.assertFalse([pts for net, _l, _w, pts in fan.traces if net == "P"])  # no stub and no join pin to pin

    def test_rows_too_close_for_a_via_are_refused(self):
        pins = [_pin(i, "A" if i % 2 else "B", i * PITCH, 0.0) for i in range(6)]
        pins += [_pin(i + 10, "C", i * PITCH, 0.8) for i in range(6)]
        with self.assertRaises(PlanError) as caught:
            FO.plan(pins, (0.0, 50.0), 0.2, 0.1, 0.1, 1, set(), set(), 0.1)
        self.assertIn("no room between them", caught.exception.message)


class LaneFromFanoutTest(unittest.TestCase):
    def test_a_lane_leaves_the_connector_and_joins_its_line_before_the_edge_begins(self):
        fan = FO.plan(_connector(), (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, set(), 0.1)
        ring = LN.outline_corners(list(zip(*rounded(0, 0, 10, 100, 0.5), strict=True)))
        lane = LN.parse([{"net": "N2", "layer": 3, "width_mm": 0.1, "from": "J1", "to_mm": [[2.5, 95]],
                          "edge_mm": [[0, 5], [0, 95]], "offset_mm": 2.5}])[0]
        trace = LN.expand(lane, ring, "lanes[0]", {"J1": fan}, 0.1)
        points = [(round(x, 4), round(y, 4)) for x, y in trace.points]
        self.assertEqual(points[0], (1.7, NEAR_Y))  # N2's pin
        self.assertEqual(points[1], (1.7, 2.918))  # clear of the near row's pads by 0.1 mm
        self.assertEqual(points[2], (2.5, 3.718))  # across to the lane's line at 45 degrees, before y 5
        self.assertTrue(all(x == 2.5 for x, _y in points[2:]))
        with self.assertRaises(PlanError) as caught:  # N2 does not leave J1 on layer 1
            LN.expand(LN.parse([{**{"net": "N2", "layer": 1, "width_mm": 0.1, "from": "J1", "to_mm": [[2.5, 95]],
                                    "edge_mm": [[0, 5], [0, 95]], "offset_mm": 2.5}}])[0], ring, "lanes[1]", {"J1": fan}, 0.1)
        self.assertIn("leaves on layer [3]", caught.exception.message)
        via_lane = LN.parse([{"net": "A", "layer": 1, "width_mm": 0.1, "from": "J1", "to_mm": [[2.0, 95]],
                              "edge_mm": [[0, 5], [0, 95]], "offset_mm": 2.0}])[0]
        rows = [(round(x, 4), round(y, 4)) for x, y in LN.expand(via_lane, ring, "lanes[2]", {"J1": fan}, 0.1).points[:3]]
        self.assertEqual(rows, [(1.35, 1.918), (1.35, 2.268), (2.0, 2.918)])  # on layer 1 only the vias are in the way


if __name__ == "__main__":
    unittest.main()
