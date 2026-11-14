"""Ground guards beside lanes and rings inside the outline (plan/guards.py)."""
from __future__ import annotations

import math
import unittest

from xpedition_cli.adapter.plan import PlanError
from xpedition_cli.adapter.plan import guards as GD
from xpedition_cli.adapter.plan import lanes as LN
from xpedition_cli.adapter.plan import trace_plan as TP
from xpedition_cli.adapter.plan.outline import rounded

BOARD = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 40.0, 0.0), (0.0, 40.0, 0.0)]  # 10 x 40 mm, counter-clockwise
RULES = TP.Rules(net_class_of={}, widths={}, clearance=lambda a, b, layer, kind: 0.1)


def lane(net: str, offset: float, width: float = 0.1, layer: int = 3) -> LN.Lane:
    # lanes along the left edge, from y 5 to y 35
    return LN.Lane(net, layer, width, [((offset, 5.0), 0.0)], [((offset, 35.0), 0.0)], ((0.0, 5.0), (0.0, 35.0)), offset, 0.5)


def obstacles(plan: TP.Plan | None = None, pads: list | None = None) -> GD.Obstacles:
    copper = TP.Copper(layer_count=3, pads=pads or [], nets={"GND", "A", "B", "C"})
    return GD.Obstacles(copper, plan or TP.Plan(), RULES)


class ParseTest(unittest.TestCase):
    def test_a_guard_and_a_ring_with_their_keys(self):
        guard = GD.parse_guards([{"nets": ["A", "B"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.1, "between": True,
                                  "via_pitch_mm": 2, "padstack": "V"}])[0]
        self.assertEqual((guard.nets, guard.between, guard.pitch, guard.padstack), ([["A"], ["B"]], True, 2.0, "V"))
        pairs = GD.parse_guards([{"nets": [["A", "B"], "C"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.1}])[0]
        self.assertEqual(pairs.nets, [["A", "B"], ["C"]])
        ring = GD.parse_rings([{"net": "GND", "layers": [1, 3], "width_mm": 0.2, "offset_mm": 0.32}])[0]
        self.assertEqual((ring.layers, ring.pitch), ([1, 3], None))

    def test_wrong_keys_and_values_are_refused(self):
        wrong = [{"nets": ["A"], "net": "GND", "width_mm": 0.1},  # no gap
                 {"nets": ["A", "A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.1},
                 {"nets": ["A"], "net": "GND", "width_mm": 0.1, "gap_mm": 0.1, "via_pitch_mm": 2},  # no padstack
                 {"nets": ["A"], "net": "GND", "width_mm": 0, "gap_mm": 0.1}]
        for item in wrong:
            with self.subTest(item=item), self.assertRaises(PlanError):
                GD.parse_guards([item])
        with self.assertRaises(PlanError):
            GD.parse_rings([{"net": "GND", "layers": [], "width_mm": 0.2, "offset_mm": 0.3}])


class GuardTest(unittest.TestCase):
    def test_guards_stand_beside_the_group_and_between_its_members(self):
        guard = GD.Guard([["A"], ["B"]], "GND", 0.1, 0.1, between=True)
        groups = GD.members(guard, [lane("B", 2.0), lane("A", 1.0), lane("C", 4.0)], "guards[0]")
        self.assertEqual([[m.net for m in g] for g in groups], [["A"], ["B"]])
        self.assertEqual([(round(o, 4), w) for o, w in GD.guard_offsets(guard, groups, "guards[0]")], [(0.8, 0.1), (1.5, 0.7), (2.2, 0.1)])
        with self.assertRaises(PlanError) as caught:  # 0.2 mm between the copper of A and B leaves no room
            GD.guard_offsets(guard, [[lane("A", 1.0)], [lane("B", 1.3)]], "guards[0]")
        self.assertIn("no room for a guard between A and B", caught.exception.message)

    def test_pairs_get_a_guard_between_them_and_none_inside(self):
        guard = GD.Guard([["P1", "N1"], ["P2", "N2"]], "GND", 0.1, 0.15, between=True)
        groups = GD.members(guard, [lane("P1", 1.0, 0.06), lane("N1", 1.16, 0.06), lane("P2", 2.0, 0.06), lane("N2", 2.16, 0.06)], "g")
        offsets = [(round(o, 4), w) for o, w in GD.guard_offsets(guard, groups, "g")]
        # outside both and once between them, filling the room at 0.15 mm from either pair, never between P and N
        self.assertEqual(offsets, [(0.77, 0.1), (1.58, 0.48), (2.39, 0.1)])
        with self.assertRaises(PlanError):  # a net between the two of a pair
            GD.members(GD.Guard([["P1", "N1"], ["X"]], "GND", 0.1, 0.1), [lane("P1", 1.0), lane("X", 1.1), lane("N1", 1.2)], "g")

    def test_guards_and_their_vias_keep_clear_of_a_cutout(self):
        slot = [(2.0, 10.0), (3.0, 10.0), (3.0, 12.0), (2.0, 12.0)]
        found = GD.Obstacles(TP.Copper(layer_count=3), TP.Plan(), RULES, cutouts=[slot])
        self.assertFalse(found.piece_clear((1.75, 9.0), (1.75, 13.0), "GND", 1, 0.1))  # 0.25 mm off: less than 0.254 and half its width
        self.assertTrue(found.piece_clear((1.6, 9.0), (1.6, 13.0), "GND", 1, 0.1))
        self.assertFalse(found.via_clear((1.6, 11.0), "GND", 0.2))  # 0.4 mm off: the via needs 0.454 and a margin
        self.assertTrue(found.via_clear((1.5, 11.0), "GND", 0.2))
        wide = GD.Obstacles(TP.Copper(layer_count=3), TP.Plan(), RULES, cutouts=[[(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]])
        self.assertFalse(wide.piece_clear((9.0, 10.0), (11.0, 10.0), "GND", 1, 0.1))  # inside it, far from its edges
        self.assertFalse(wide.via_clear((10.0, 10.0), "GND", 0.2))
        self.assertIn("a cutout", [name for name, _gap, _need in wide.via_blockers((10.0, 10.0), "GND", 0.2)])
        self.assertTrue(wide.via_clear((25.0, 10.0), "GND", 0.2))

    def test_a_via_under_a_part_is_refused_and_says_so(self):
        copper = TP.Copper(layer_count=3)
        copper.bodies = [[(2.0, 2.0), (6.0, 2.0), (6.0, 4.0), (2.0, 4.0)]]  # a connector's placement outline
        found = GD.Obstacles(copper, TP.Plan(), RULES)
        self.assertFalse(found.via_clear((4.0, 3.0), "GND", 0.2))
        self.assertIn("a part's placement outline", [name for name, _gap, _need in found.via_blockers((4.0, 3.0), "GND", 0.2)])
        self.assertFalse(found.via_clear((6.1, 3.0), "GND", 0.2))  # beside it, its pad reaching under
        self.assertTrue(found.via_clear((6.3, 3.0), "GND", 0.2))

    def test_guards_and_their_vias_keep_out_of_keepouts(self):
        strip = [(2.0, 0.0), (3.0, 0.0), (3.0, 20.0), (2.0, 20.0)]
        copper = TP.Copper(layer_count=3, keepouts=[("trace", 1, strip), ("via", None, [(10.0, 0.0), (12.0, 0.0), (12.0, 20.0), (10.0, 20.0)])])
        found = GD.Obstacles(copper, TP.Plan(), RULES)
        self.assertFalse(found.piece_clear((2.5, 5.0), (2.5, 8.0), "GND", 1, 0.1))  # in the keepout of traces on its layer
        self.assertTrue(found.piece_clear((2.5, 5.0), (2.5, 8.0), "GND", 3, 0.1))  # another layer's is no keepout for it
        self.assertTrue(found.via_clear((2.5, 5.0), "GND", 0.2))  # nor for a via
        self.assertFalse(found.via_clear((11.0, 5.0), "GND", 0.2))
        self.assertFalse(found.via_clear((9.9, 5.0), "GND", 0.2))  # its pad reaches in

    def test_a_stretch_ends_on_a_via_where_one_fits(self):
        stretch = [(1.0, 5.0), (1.0, 15.0)]
        bend = [(0.0, 4.0, 10.0, 6.0)]  # its first end lies in a bend zone
        trimmed, spots = GD.end_vias(stretch, "GND", 0.2, 1.0, obstacles(), bend)
        self.assertEqual(spots, [(1.0, 15.0)])  # none within 1 mm of the end in the bend; one at the other end
        self.assertEqual(trimmed, stretch)
        trimmed, spots = GD.end_vias(stretch, "GND", 0.2, 3.0, obstacles(), bend)
        self.assertEqual(spots[0], (1.0, 7.2))  # the first spot the margin from the zone allows, the stretch cut back to it
        self.assertAlmostEqual(trimmed[0][1], 7.2)

    def test_a_free_end_joins_the_nearest_copper_of_its_net_by_a_clear_path(self):
        end, outward = (1.0, 5.0), (0.0, -1.0)  # an end pointing down
        self.assertEqual(GD.join(end, outward, "GND", 1, 0.1, obstacles(), [(1.0, 3.0), (3.0, 4.0)]), [(1.0, 5.0), (1.0, 3.0)])
        across = TP.Plan(traces=[TP.Trace("A", 1, 0.1, [(-5.0, 4.0), (7.0, 4.0)])])  # another net's trace in the way, on layer 1
        self.assertIsNone(GD.join(end, outward, "GND", 1, 0.1, obstacles(across), [(1.0, 3.0)]))
        self.assertEqual(GD.join(end, outward, "GND", 3, 0.1, obstacles(across), [(1.0, 3.0)]), [(1.0, 5.0), (1.0, 3.0)])  # not on 3
        bent = GD.join(end, outward, "GND", 1, 0.1, obstacles(), [(2.0, 2.0)])
        self.assertEqual(bent, [(1.0, 5.0), (2.0, 2.0)])  # straight when it clears
        self.assertIsNone(GD.join(end, outward, "GND", 1, 0.1, obstacles(), [(1.0, 20.0)]))  # too far

    def test_a_free_end_with_no_straight_way_takes_the_path_a_search_finds_around_the_copper(self):
        end, outward = (1.0, 5.0), (0.0, -1.0)
        short = TP.Plan(traces=[TP.Trace("A", 1, 0.1, [(0.0, 4.0), (2.0, 4.0)])])  # in the way, with room past its right end
        found = obstacles(short)
        path = GD.join(end, outward, "GND", 1, 0.1, found, [(1.0, 3.0)])
        self.assertEqual((path[0], path[-1]), ((1.0, 5.0), (1.0, 3.0)))
        self.assertGreaterEqual(max(abs(x - 1.0) for x, _y in path), 1.2 - 1e-9)  # round one of the trace's ends, 0.1 mm clear of it
        self.assertTrue(all(found.piece_clear(a, b, "GND", 1, 0.1, GD.JOIN_SLACK_MM) for a, b in zip(path, path[1:], strict=False)))
        self.assertLessEqual(len(path), 6)  # straightened: the grid path has dozens of points

    def test_an_end_squeezed_between_two_signals_off_the_grid_goes_on_beside_one_to_where_they_part(self):
        a = [(0.002, 2.0), (0.0, 0.0), (-1.0, -1.0)]  # two signals 0.4 mm apart, leaning off square, the left one turning away
        b = [(0.402, 2.0), (0.4, 0.0), (0.4, -2.0)]
        plan = TP.Plan(traces=[TP.Trace("A", 1, 0.1, a), TP.Trace("B", 1, 0.1, b)])
        found = obstacles(plan)
        end, outward, target = (0.202, 2.0), (0.0, -1.0), (0.19, -1.2)
        self.assertIsNone(GD.join(end, outward, "GND", 1, 0.1, found, [target]))  # no grid step keeps 0.1 mm from both
        path = GD.join(end, outward, "GND", 1, 0.1, found, [target], walls=[("A", 0.1, a, [False, False]), ("B", 0.1, b, [False, False])])
        self.assertEqual((path[0], path[-1]), (end, target))
        self.assertTrue(all(found.piece_clear(p, q, "GND", 1, 0.1) for p, q in zip(path, path[1:], strict=False)))

    def test_an_end_goes_over_to_a_target_in_line_with_a_gap_and_on_through_it_square(self):
        vias = TP.Plan(vias=[TP.Via("A", (0.65, 0.0), "V", 0.2), TP.Via("A", (1.35, 0.0), "V", 0.2)])  # a gap just wide enough at x 1
        found = obstacles(vias)
        end, leaning = (0.81, 3.0), (0.0002, -1.0)  # off the grid of the gap, its line leaning by 0.2 mrad
        length = math.hypot(*leaning)
        path = GD.join(end, (leaning[0] / length, leaning[1] / length), "GND", 1, 0.1, found, [(1.0, -0.5)])
        self.assertEqual((path[0], path[-1]), (end, (1.0, -0.5)))
        self.assertAlmostEqual(path[1][0], 1.0, places=9)  # over to the target's line first, then straight down it
        self.assertTrue(all(found.piece_clear(p, q, "GND", 1, 0.1, GD.JOIN_SLACK_MM) for p, q in zip(path, path[1:], strict=False)))

    def test_a_via_along_a_guard_tries_its_edges_and_beyond_either_way(self):
        self.assertEqual(GD.across(0.1, 0.2), [0.0, 0.05, -0.05, 0.1, -0.1, 0.15, -0.15, 0.2, -0.2, 0.21, -0.21])
        wall = TP.Plan(traces=[TP.Trace("A", 1, 0.1, [(0.0, 0.25), (10.0, 0.25)])])  # a signal 0.25 mm above the guard's line
        line = [(0.0, 0.0), (10.0, 0.0)]
        self.assertIsNone(GD.via_spot(line, "GND", 0.2, obstacles(wall), []))  # on the line a via comes too near it
        spot = GD.via_spot(line, "GND", 0.2, obstacles(wall), [], GD.across(0.1, 0.2))
        self.assertLess(spot[1], 0)  # moved off the line, away from the signal

    def test_a_stretch_that_starts_where_an_arc_starts_keeps_the_arc(self):
        chain = GD.make_chain([(0.0, 10.0), (0.0, 0.0), (10.0, 0.0)], [0.0, 1.0, 0.0])
        begin, finish = chain.spans[1]
        for start, end in ((begin, chain.length), (0.0, finish), (begin, finish)):
            points, radii = GD.drawn(chain, start, end)
            self.assertIn((0.0, 0.0), [(round(x, 6), round(y, 6)) for x, y in points])  # the corner, rounded, not cut across
            self.assertIn(1.0, radii)

    def test_a_guard_moves_with_lanes_that_jog(self):
        guard = GD.Guard([["P", "N"]], "GND", 0.1, 0.15)
        p, n = lane("P", 1.0, 0.06), lane("N", 1.16, 0.06)
        for each in (p, n):
            each.jogs = [((0.0, 20.0), each.offset + 1.0, 0.0)]
        groups = GD.members(guard, [p, n], "g")
        before = [round(o, 4) for o, _w in GD.guard_offsets(guard, groups, "g")]
        after = [[(at, round(o, 4)) for at, o in line] for line in GD.guard_jogs(guard, groups, "g")]
        self.assertEqual(after, [[((0.0, 20.0), round(before[0] + 1.0, 4))], [((0.0, 20.0), round(before[1] + 1.0, 4))]])
        ring = LN.outline_corners(BOARD)
        line = GD.guard_line(p, before[0], ring, [((0.0, 20.0), before[0] + 1.0, 0.0)])
        xs = {round(x, 4) for x, y in line if 5.0 <= y <= 15.0} | {round(x, 4) for x, y in line if 25.0 <= y <= 35.0}
        self.assertEqual(xs, {before[0], round(before[0] + 1.0, 4)})  # on its first distance before the jog, on the new one after
        n.jogs = [((0.0, 30.0), n.offset + 1.0, 0.0)]
        with self.assertRaises(PlanError) as caught:  # lanes that jog at different places
            GD.guard_jogs(guard, GD.members(guard, [p, n], "g"), "g")
        self.assertIn("jog at different places", caught.exception.message)

    def test_guard_lines_that_touch_are_merged_as_wide_as_both(self):
        beside = [lane("A", 1.0)]
        lines = [GD.Line(3, "GND", 2.0, 0.1, beside, None, None, 1.0, 0.1), GD.Line(3, "GND", 2.08, 0.1, beside, 2.0, "V", 1.0, 0.08),
                 GD.Line(3, "GND", 3.0, 0.1, beside, None, None, 1.0, 0.1), GD.Line(1, "GND", 2.0, 0.1, beside, None, None, 1.0, 0.1)]
        out = GD.merged(lines)
        self.assertEqual(len(out), 3)
        wide = next(line for line in out if line.layer == 3 and line.offset < 2.5)
        self.assertAlmostEqual(wide.offset, 2.04)
        self.assertAlmostEqual(wide.width, 0.18)
        self.assertEqual((wide.pitch, wide.padstack), (2.0, "V"))
        self.assertEqual(wide.join_width, 0.08)  # a join from it is as narrow as the narrower guard, not as wide as the line

    def test_a_guard_stops_short_of_other_copper_and_goes_on_past_it(self):
        ring = LN.outline_corners(BOARD)
        line = GD.guard_line(lane("A", 1.0), 0.8, ring)
        self.assertEqual((line[0], line[-1]), ((0.8, 5.0), (0.8, 35.0)))
        pad = [(0.7, 19.9), (0.9, 19.9), (0.9, 20.1), (0.7, 20.1)]  # a pad of B on the guard's line
        stretches = GD.runs(line, "GND", 3, 0.1, obstacles(pads=[(3, "B", pad)]), 1.0)
        self.assertEqual(len(stretches), 2)
        self.assertLess(stretches[0][-1][1], 19.9 - 0.15 + 1e-6)  # stops short by the clearance and half its width
        self.assertGreater(stretches[1][0][1], 20.1 + 0.15 - 1e-6)
        self.assertEqual(GD.runs(line, "GND", 1, 0.1, obstacles(pads=[(3, "B", pad)]), 1.0), [line])  # on another layer, nothing in the way

    def test_vias_stand_along_a_guard_at_their_pitch_outside_bends_and_clear_of_traces(self):
        stretch = [(1.0, 5.0), (1.0, 25.0)]
        neighbour = TP.Plan(traces=[TP.Trace("A", 1, 0.1, [(1.3, 4.0), (1.3, 12.0)])])  # on layer 1, 0.3 mm beside the via spots
        found = obstacles(neighbour)
        vias = GD.vias_along(stretch, "GND", 0.2, 2.0, found, [(0.0, 14.0, 10.0, 16.0)])
        ys = [y for _x, y in vias]
        from xpedition_cli.adapter.plan.shapes import point_segment
        self.assertTrue(all(point_segment(v, (1.3, 4.0), (1.3, 12.0)) >= 0.35 - 1e-6 for v in vias))  # 0.1 + 0.2 + 0.05 from it
        self.assertFalse([y for y in ys if y < 12.0])
        self.assertFalse([y for y in ys if 14.0 - 0.2 < y < 16.0 + 0.2])  # none in the bend zone
        self.assertTrue(all(b - a >= 0.5 for a, b in zip(ys, ys[1:], strict=False)))
        self.assertGreaterEqual(len(vias), 5)

    def test_a_via_keeps_off_the_ends_of_the_plans_traces_of_its_net(self):
        found = obstacles(TP.Plan(traces=[TP.Trace("GND", 1, 0.5, [(0.0, 0.0), (5.0, 0.0)])]))
        self.assertTrue(found.covers_end((5.15, 0.15), "GND", 0.2))  # drawing would run the end on to its centre
        self.assertFalse(found.covers_end((5.0, 0.0), "GND", 0.2))  # on the end itself: moved, not run on
        self.assertFalse(found.covers_end((5.15, 0.15), "A", 0.2))
        self.assertFalse(found.covers_end((5.25, 0.0), "GND", 0.2))
        self.assertEqual(found.covered_end((5.15, 0.15), "GND", 0.2), (5.0, 0.0))
        found.add_via("GND", (5.15, 0.15), 0.2)
        centre, box = found.holder((5.0, 0.0), "GND", 1)
        self.assertEqual(centre, (5.15, 0.15))
        self.assertEqual([round(v, 6) for v in box], [4.95, -0.05, 5.35, 0.35])
        self.assertIsNone(found.holder((0.0, 0.0), "GND", 1))
        pads = obstacles(pads=[(2, "GND", [(9.8, -0.2), (10.2, -0.2), (10.2, 0.2), (9.8, 0.2)])])
        self.assertEqual(pads.holder((10.1, 0.0), "GND", 2)[0], (10.0, 0.0))  # a pad of the net on the end's layer
        self.assertIsNone(pads.holder((10.1, 0.0), "GND", 1))

    def test_a_walker_finds_places_along_a_line_as_walking_it_does(self):
        line = [(0.0, 0.0), (3.0, 0.0), (3.0, 0.0), (3.0, 4.0), (6.0, 8.0)]  # with a piece of no length
        walker = GD.Walker(line)
        self.assertEqual(walker.length, 12.0)
        for distance in (-1.0, 0.0, 1.5, 3.0, 5.0, 7.0, 12.0, 13.0):
            self.assertEqual(walker.at(distance), GD._at_length(line, distance))
            self.assertEqual(walker.direction(distance), GD._direction(line, distance))

    def test_vias_along_a_guard_stay_within_the_pitch_wherever_one_fits(self):
        stretch = [(1.0, 5.0), (1.0, 25.0)]
        found = obstacles(TP.Plan(traces=[TP.Trace("A", 1, 0.1, [(1.3, 9.0), (1.3, 10.0)])]))  # no via from about 8.8 to 10.2
        found.add_via("GND", (1.0, 17.0), 0.2)  # one already standing on it
        vias = GD.vias_along(stretch, "GND", 0.2, 2.0, found, [])
        ys = sorted([y for _x, y in vias] + [17.0])
        self.assertTrue(all(b - a <= 2.0 for a, b in zip(ys, ys[1:], strict=False)))  # past the trace too, with room either side
        self.assertLessEqual(ys[0] - 5.0, 1.0)  # within half the pitch of each end, so the next stretch keeps it across the joint
        self.assertLessEqual(25.0 - ys[-1], 1.0)
        self.assertNotIn(17.0, [y for _x, y in vias])  # the via standing there is not placed again
        self.assertTrue(all(not 8.8 < y < 10.2 for y in ys))
        wide = GD.vias_along([(1.0, 30.0), (1.0, 31.5)], "GND", 0.2, 2.0, obstacles(), [], GD.across(0.53, 0.2), width=0.53)
        self.assertTrue(wide)  # no pad over either end, which Layout would run on to the via's centre at the line's width
        self.assertTrue(all(30.0 + 0.465 - 1e-6 <= y <= 31.5 - 0.465 + 1e-6 for _x, y in wide))

    def test_a_via_keeps_its_pad_off_a_joint_as_pcb_check_does_and_no_further(self):
        # a 0.1 mm join meets a 0.6 mm line at y 7; pins at either end leave room only near the joint
        stretch, widths = [(1.0, 5.0), (1.0, 7.0), (1.0, 12.0)], [0.1, 0.6]
        found = obstacles(pads=[(1, "GND", [(0.8, 5.5), (1.2, 5.5), (1.2, 6.5), (0.8, 6.5)])])
        found.add_via("GND", (1.0, 9.0), 0.2)
        vias = GD.vias_along(stretch, "GND", 0.2, 2.0, found, [], width=widths, half_ends=False, joints=[2.0])
        ys = sorted([5.0, 9.0, 12.0] + [y for _x, y in vias])
        self.assertTrue(all(b - a <= 2.0 for a, b in zip(ys, ys[1:], strict=False)), ys)  # half the line's width kept off left a gap
        self.assertTrue(all(abs(y - 7.0) < GD.END_SNAP_MM or abs(y - 7.0) > 0.2 for y in ys))  # a pad on the joint only at its centre


class ChainTest(unittest.TestCase):
    def test_a_ring_is_drawn_with_whole_arcs_and_its_cuts_leave_them_whole(self):
        rounded_board = list(zip(*rounded(0, 0, 10, 40, 2), strict=True))
        chain, _left = GD.ring_chain(LN.outline_corners(rounded_board), 0.5)
        points, radii = GD.drawn(chain, 0.0, chain.length)
        self.assertEqual(sorted(r for r in radii if r), [1.5, 1.5, 1.5, 1.5])  # each corner an arc 0.5 mm smaller
        begin, end = next(iter(chain.spans.values()))
        inside = (begin + end) / 2
        self.assertEqual(GD.snapped(chain, inside, chain.length), (end, chain.length))  # a cut inside an arc moves out of it
        self.assertEqual(GD.snapped(chain, 0.0, inside), (0.0, begin))
        self.assertTrue(GD.in_arc(chain, inside))
        self.assertFalse(GD.in_arc(chain, end + 0.1))


class RingTest(unittest.TestCase):
    def test_a_ring_runs_all_the_way_round_inside_the_outline(self):
        line, left = GD.ring_line(LN.outline_corners(BOARD), 0.3)
        self.assertTrue(left)
        self.assertEqual(line[0], line[-1])
        xs, ys = [p[0] for p in line], [p[1] for p in line]
        self.assertAlmostEqual(min(xs), 0.3)
        self.assertAlmostEqual(max(xs), 9.7)
        self.assertAlmostEqual(min(ys), 0.3)
        self.assertAlmostEqual(max(ys), 39.7)
        length = sum(math.dist(a, b) for a, b in zip(line, line[1:], strict=False))
        self.assertAlmostEqual(length, 2 * (9.4 + 39.4), places=6)

    def test_a_ring_cut_by_a_pad_keeps_one_stretch_through_its_start(self):
        line, _left = GD.ring_line(LN.outline_corners(BOARD), 0.3)
        pad = [(4.9, 0.2), (5.1, 0.2), (5.1, 0.4), (4.9, 0.4)]  # on the ring's bottom side
        stretches = GD.runs(line, "GND", 1, 0.2, obstacles(pads=[(1, "B", pad)]), 1.0, closed=True)
        self.assertEqual(len(stretches), 1)  # one stretch round the board from one side of the pad to the other
        ends = sorted([stretches[0][0][0], stretches[0][-1][0]])
        self.assertLessEqual(ends[0], 4.9 - 0.2 + 1e-6)  # the clearance and half the ring's width from the pad
        self.assertGreaterEqual(ends[1], 5.1 + 0.2 - 1e-6)

    def test_a_ring_keeps_inside_the_border_and_its_vias_move_inwards(self):
        line, left = GD.ring_line(LN.outline_corners(BOARD), 0.3)
        border = [(0.2, 0.2), (9.8, 0.2), (9.8, 39.8), (0.2, 39.8)]
        found = GD.Obstacles(TP.Copper(layer_count=3), TP.Plan(), RULES, border)
        self.assertEqual(len(GD.runs(line, "GND", 1, 0.19, found, 1.0, closed=True)), 1)  # 0.095 mm either side of 0.3: inside
        self.assertEqual(GD.runs(line, "GND", 1, 0.2, found, 1.0, closed=True), [])  # an edge on the border reaches past it for DRC
        vias = GD.vias_along([(0.3, 5.0), (0.3, 30.0)], "GND", 0.2, 5.0, found, [], [0.0, 0.1, 0.26], left=False)
        self.assertTrue(vias)
        self.assertTrue(all(abs(x - 0.56) < 1e-6 for x, _y in vias))  # moved inwards until the pad keeps off the border

    def test_a_via_of_the_tool_stands_under_no_part(self):
        copper = TP.Copper(layer_count=3, bodies=[[(0.0, 4.0), (2.0, 4.0), (2.0, 8.0), (0.0, 8.0)]])  # a part over the line
        found = GD.Obstacles(copper, TP.Plan(), RULES)
        self.assertFalse(found.via_clear((1.0, 6.0), "GND", 0.2))
        self.assertFalse(found.via_clear((1.0, 8.1), "GND", 0.2))  # its pad would reach under the part
        self.assertTrue(found.via_clear((1.0, 8.3), "GND", 0.2))

    def test_a_join_that_turns_back_sharper_than_square_is_a_hairpin(self):
        self.assertTrue(GD._hairpin((1.0, 0.0), [(0.0, 0.0), (1.0, 0.2), (0.4, -0.5)]))  # out, then back past square
        self.assertTrue(GD._hairpin((1.0, 0.0), [(0.0, 0.0), (-1.0, 0.5)]))  # back over the line it ends
        self.assertFalse(GD._hairpin((1.0, 0.0), [(0.0, 0.0), (1.0, 1.0), (1.0, 3.0)]))
        found = GD.Obstacles(TP.Copper(layer_count=3), TP.Plan(), RULES)
        steps = [(0.0, 0.0), (0.1, 0.01), (0.2, -0.01), (0.3, 0.0), (2.0, 0.0)]  # a walk in short jogs, nothing in its way
        self.assertEqual(GD._pulled(steps, "GND", 1, 0.1, found), [(0.0, 0.0), (2.0, 0.0)])

    def test_a_long_piece_that_leaves_the_border_between_its_ends_is_refused(self):
        notched = [(0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (2.0, 2.0), (2.0, 3.0), (10.0, 3.0), (10.0, 10.0), (0.0, 10.0)]
        found = GD.Obstacles(TP.Copper(layer_count=3), TP.Plan(), RULES, notched)
        self.assertFalse(found.piece_clear((5.0, 0.5), (5.0, 9.5), "GND", 1, 0.1))  # ends and middle inside, the notch crossed
        self.assertTrue(found.piece_clear((1.0, 0.5), (1.0, 9.5), "GND", 1, 0.1))  # beside the notch's end


if __name__ == "__main__":
    unittest.main()
