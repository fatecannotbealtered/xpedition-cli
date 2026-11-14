"""The fan-out of a two-row connector, and lanes that start from it."""
from __future__ import annotations

import math
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
        p_vias = [at for net, at in self.fan.vias if net == "P"]
        self.assertEqual([(round(x, 3), round(y, 3)) for x, y in p_vias], [(2.75, 1.918)])  # one via for the run, in its middle
        self.assertNotIn("GND", self.fan.exits)

    def test_a_net_that_gives_way_leaves_its_via_to_a_plane_pin_above(self):
        pins = _connector()
        next(p for p in pins if p.number == "36").net = "GND"  # the near pin over the middle P pin
        fan = FO.plan(pins, (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, set(), 0.1, {"P"})
        self.assertEqual([(round(x, 3), round(y, 3)) for n, (x, y) in fan.vias if n == "P"], [(2.4, 1.508)])  # beside the GND pin above the middle
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
        self.assertAlmostEqual(FO.leave(fan, (2.05, NEAR_Y), 3, 0.1, 0.1)[1], near_edge + 0.1 + 0.05)  # the pads alone
        self.assertAlmostEqual(FO.leave(fan, (2.05, NEAR_Y), 3, 0.1, 0.1, past=4.0)[1], 4.15)  # past a via in its way
        ring = LN.outline_corners(list(zip(*rounded(0, 0, 10, 100, 0.5), strict=True)))
        lone = FO.Fanout(toward=(0.0, 1.0), pad_layer=3, clear_at=2.8, clear_vias_at=2.0, lifted=[(0.8, 3.2)], via_radius=0.2,
                         exits={"X": [((1.2, 2.5), 3)]})
        west = LN.parse([{"net": "X", "layer": 3, "width_mm": 0.1, "from": "F", "to_mm": [[0.4, 95]],
                          "edge_mm": [[0, 5], [0, 95]], "offset_mm": 0.4}])[0]
        self.assertAlmostEqual(LN.expand(west, ring, "lanes[0]", {"F": lone}, 0.1).points[1][1], 3.2 + 0.2 + 0.1 + 0.05)  # past it
        east = LN.parse([{"net": "X", "layer": 3, "width_mm": 0.1, "from": "F", "to_mm": [[5.0, 95]],
                          "edge_mm": [[10, 5], [10, 95]], "offset_mm": 5.0}])[0]
        self.assertAlmostEqual(LN.expand(east, ring, "lanes[1]", {"F": lone}, 0.1).points[1][1], 2.8 + 0.1 + 0.05)  # away from it

    def test_a_skipped_net_is_left_to_the_caller(self):
        fan = FO.plan(_connector(), (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, {"P"}, 0.1)
        self.assertFalse([at for net, at in fan.vias if net == "P"])
        self.assertFalse([pts for net, _l, _w, pts in fan.traces if net == "P"])  # no stub and no join pin to pin

    def test_a_keepout_between_the_rows_puts_the_vias_in_one_line_beyond_the_far_row(self):
        strip = [(0.5, 1.30), (4.0, 1.30), (4.0, 1.45), (0.5, 1.45)]  # keeps traces out next to the far row's pads
        fan = FO.plan(_connector(), (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, set(), 0.1, keepouts=[("trace", strip)])
        far_edge = FAR_Y - 0.275
        line = far_edge - 0.1 - 0.2
        self.assertTrue(fan.vias and all(abs(y - line) < 1e-9 for _net, (_x, y) in fan.vias))  # all beyond the far row
        xs = sorted(x for _net, (x, _y) in fan.vias)
        self.assertTrue(all(b - a >= 0.5 - 1e-9 for a, b in zip(xs, xs[1:], strict=False)))  # a via's width and its clearance apart
        self.assertEqual(sorted(net for net, _at in fan.vias), ["A", "B", "C", "GND", "P"])  # one via for each run of one net
        a_stub = next(pts for net, layer, _w, pts in fan.traces if net == "A" and layer == 3 and len(pts) >= 2 and pts[0][1] == FAR_Y)
        self.assertLess(a_stub[-1][1], far_edge)  # the stub leaves the pad outwards, never across the strip
        self.assertIn("31 of GND", fan.loose)  # the near row's ground pins reach no via now
        self.assertEqual(fan.exits["N1"], [((1.35, NEAR_Y), 3)])

    def test_beyond_puts_the_vias_outside_the_connector_without_a_keepout(self):
        fan = FO.plan(_connector(), (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, set(), 0.1, beyond=True)
        line = FAR_Y - 0.275 - 0.1 - 0.2
        self.assertTrue(fan.vias and all(abs(y - line) < 1e-9 for _net, (_x, y) in fan.vias))  # none between the rows
        self.assertIn("31 of GND", fan.loose)

    def test_a_connector_at_an_angle_is_fanned_out_along_its_own_rows(self):
        c, s = math.cos(math.radians(20.0)), math.sin(math.radians(20.0))

        def turn(p):
            return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)

        pins = []
        for pin in _connector():
            corners = [turn(q) for q in ((pin.pad[0], pin.pad[1]), (pin.pad[2], pin.pad[1]), (pin.pad[2], pin.pad[3]), (pin.pad[0], pin.pad[3]))]
            box = (min(q[0] for q in corners), min(q[1] for q in corners), max(q[0] for q in corners), max(q[1] for q in corners))
            pins.append(FO.Pin(pin.number, pin.net, turn(pin.at), box, pin.layer, corners))
        self.assertAlmostEqual(math.degrees(FO.row_angle(pins)), 20.0, places=6)
        turned = FO.plan(pins, turn((2.0, 50.0)), 0.2, 0.1, 0.1, 1, {"GND"}, set(), 0.1)
        want = sorted((net, round(x, 6), round(y, 6)) for net, at in self.fan.vias for x, y in [turn(at)])
        self.assertEqual(sorted((net, round(x, 6), round(y, 6)) for net, (x, y) in turned.vias), want)
        self.assertAlmostEqual(turned.toward[0], -s)
        self.assertAlmostEqual(turned.toward[1], c)

    def test_a_trace_leaving_a_pin_clears_its_row_before_it_turns(self):
        pins = _connector()
        exit_at = FO.pin_exit(pins, "32", (2.0, 50.0), 0.1, 0.1)  # N1 in the near row, the board's body above
        self.assertAlmostEqual(exit_at[0], 1.35)
        self.assertAlmostEqual(exit_at[1], NEAR_Y + 0.275 + 0.1 + 0.05)
        self.assertIsNone(FO.pin_exit(pins, "99", (2.0, 50.0), 0.1, 0.1))

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

    def test_a_lane_turns_onto_its_line_before_a_bend_zone_it_would_turn_in(self):
        fan = FO.plan(_connector(), (2.0, 50.0), 0.2, 0.1, 0.1, 1, {"GND"}, set(), 0.1)
        ring = LN.outline_corners(list(zip(*rounded(0, 0, 10, 100, 0.5), strict=True)))
        lane = LN.parse([{"net": "N2", "layer": 3, "width_mm": 0.1, "from": "J1", "to_mm": [[2.5, 95]],
                          "edge_mm": [[0, 5], [0, 95]], "offset_mm": 2.5}])[0]
        points = [(round(x, 4), round(y, 4)) for x, y in LN.expand(lane, ring, "lanes[0]", {"J1": fan}, 0.1, [[(0, 3.5), (10, 3.5), (10, 6), (0, 6)]]).points]
        self.assertEqual(points[1:3], [(1.7, 2.918), (2.5, 3.0)])  # on its line 0.5 mm, its corner's radius, before y 3.5


if __name__ == "__main__":
    unittest.main()
