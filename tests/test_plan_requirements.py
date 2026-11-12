"""A reviewer's requirements, read from a file and measured on a board model (plan/requirements.py)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from xpedition_cli.adapter.plan import PlanError
from xpedition_cli.adapter.plan import guards as GD
from xpedition_cli.adapter.plan import requirements as RQ
from xpedition_cli.adapter.plan import trace_plan as TP

RULES = TP.Rules(net_class_of={}, widths={}, clearance=lambda a, b, layer, kind: 0.1)
OUTLINE = [{"path": [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 40.0, 0.0], [0.0, 40.0, 0.0], [0.0, 0.0, 0.0]]}]


def trace(net: str, points: list[tuple[float, float]], layer: int = 1, width: float = 0.1) -> dict:
    return {"net": net, "layer": layer, "width": width, "path": [[x, y, 0.0] for x, y in points]}


def via(net: str, x: float, y: float, padstack: str = "V", radius: float = 0.2) -> dict:
    return {"net": net, "circle": [x, y, radius], "padstack": padstack}


def parsed(*items: dict) -> list[RQ.Requirement]:
    return RQ.parse({"requirements": list(items)})


class ParseTest(unittest.TestCase):
    def test_a_file_of_requirements_is_read_with_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "requirements.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"requirements": [{"kind": "guard", "nets": ["A+B", "C"], "min_both_pct": 90, "label": "GND both sides"}]}, handle)
            rows = RQ.load(path)
        self.assertEqual((rows[0].kind, rows[0].label), ("guard", "GND both sides"))
        self.assertEqual(rows[0].values, {"nets": ["A+B", "C"], "min_both_pct": 90.0, "gap_mm": 0.3})

    def test_what_a_requirement_lacks_or_does_not_take_is_named(self):
        for items, words in (([], "a list of one or more"), ([{"kind": "colour"}], "has a kind of guard"),
                             ([{"kind": "ring", "net": "GND", "layers": [1]}], "ring) takes layers, min_pct, net, offset_mm"),
                             ([{"kind": "guard", "nets": ["A"], "min_both_pct": 120}], "min_both_pct, a percentage"),
                             ([{"kind": "plane", "net": "GND", "layers": [0]}], "conductor layer numbers"),
                             ([{"kind": "via_pitch", "net": "GND", "max_mm": 2, "pitch": 1}], "via_pitch) takes")):
            with self.assertRaises(PlanError) as caught:
                RQ.parse({"requirements": items})
            self.assertIn(words, caught.exception.message)
        with self.assertRaises(PlanError) as caught:
            RQ.load(os.path.join(tempfile.gettempdir(), "no-such-requirements.json"))
        self.assertEqual(caught.exception.code, "E_NOT_FOUND")


class MeasureTest(unittest.TestCase):
    def test_guard_cover_against_the_least_both_sides(self):
        model = {"traces": [trace("A", [(5, 5), (5, 35)]), trace("GND", [(4.8, 5), (4.8, 35)]), trace("GND", [(5.2, 5), (5.2, 20)])]}
        row = RQ.measure(model, parsed({"kind": "guard", "nets": ["A", "MISSING"], "min_both_pct": 90}), [])[0]
        self.assertFalse(row["ok"])
        self.assertEqual(row["worst"][0], {"net": "MISSING", "both_sides_pct": 0.0, "problem": "no traces"})
        self.assertAlmostEqual(row["worst"][1]["both_sides_pct"], 50.0, delta=1.0)
        self.assertEqual((row["count"], row["failed"]), (2, 2))

    def test_a_ring_counts_the_way_round_its_traces_cover_on_each_layer(self):
        ring_line = [(0.5, 20), (0.5, 0.5), (9.5, 0.5), (9.5, 39.5), (0.5, 39.5), (0.5, 20)]
        model = {"outline": OUTLINE, "traces": [trace("GND", ring_line[:4], width=0.2), trace("GND", ring_line, layer=3, width=0.2)]}
        row = RQ.measure(model, parsed({"kind": "ring", "net": "GND", "layers": [1, 3], "offset_mm": 0.5, "min_pct": 90}), [])[0]
        pct = {r["layer"]: r["pct"] for r in row["layers"]}
        self.assertGreater(pct[3], 99)
        self.assertTrue(60 < pct[1] < 80)  # the top and the left side above y 20 are bare on layer 1
        self.assertFalse(row["ok"])
        self.assertEqual(row["worst"][0]["layer"], 1)

    def test_vias_of_another_padstack_are_listed(self):
        model = {"vias": [via("GND", 1, 1), via("GND", 2, 2, "BIG"), via("A", 3, 3, "BIG")]}
        row = RQ.measure(model, parsed({"kind": "via_padstack", "padstack": "V", "nets": ["GND"]}), [])[0]
        self.assertEqual((row["ok"], row["count"], row["failed"]), (False, 2, 1))
        self.assertEqual(row["worst"], [{"net": "GND", "padstack": "BIG", "at_mm": [2.0, 2.0]}])

    def test_a_response_lists_the_first_worst_places_and_the_whole_list_stays_for_a_file(self):
        model = {"vias": [via("GND", float(x), 1.0, "BIG") for x in range(6)]}
        whole = RQ.measure(model, parsed({"kind": "via_padstack", "padstack": "V"}), [])
        self.assertEqual(len(whole[0]["worst"]), 6)
        short = RQ.listed(whole)
        self.assertEqual((len(short[0]["worst"]), short[0]["failed"]), (RQ.LISTED, 6))
        self.assertEqual(len(whole[0]["worst"]), 6)  # left as it was

    def test_via_pitch_fails_a_long_gap_where_a_via_fits_and_lists_one_where_none_does(self):
        # a 10 mm ground trace in two objects, joined end to end; vias at 0, 2 and 4 mm, then nothing to its end at 10
        model = {"traces": [trace("GND", [(1, 1), (1, 5)]), trace("GND", [(1, 5), (1, 11)])],
                 "vias": [via("GND", 1, 1), via("GND", 1, 3), via("GND", 1, 5)]}
        requirement = parsed({"kind": "via_pitch", "net": "GND", "max_mm": 2.5})
        free = GD.Obstacles(TP.Copper(layer_count=2), TP.Plan(vias=[TP.Via("GND", (1.0, y), "V", 0.2) for y in (1, 3, 5)]), RULES)
        row = RQ.measure(model, requirement, [], free)[0]
        self.assertEqual((row["ok"], row["count"], row["failed"]), (False, 1, 1))
        self.assertEqual(row["worst"][0]["gap_mm"], 6.0)
        self.assertIn("via_fits_at_mm", row["worst"][0])
        sides = [TP.Trace("A", 1, 0.1, [(1.25, 5.5), (1.25, 11)]), TP.Trace("B", 1, 0.1, [(0.75, 5.5), (0.75, 11)])]
        one_side = GD.Obstacles(TP.Copper(layer_count=2), TP.Plan(traces=sides[:1], vias=[TP.Via("GND", (1.0, y), "V", 0.2) for y in (1, 3, 5)]),
                                RULES)
        row = RQ.measure(model, requirement, [], one_side)[0]
        self.assertFalse(row["ok"])  # a signal 0.25 mm on one side: a via fits moved off the line the other way
        self.assertLess(row["worst"][0]["via_fits_at_mm"][0], 1.0)
        crowded = GD.Obstacles(TP.Copper(layer_count=2), TP.Plan(traces=sides, vias=[TP.Via("GND", (1.0, y), "V", 0.2) for y in (1, 3, 5)]),
                               RULES)
        row = RQ.measure(model, requirement, [], crowded)[0]
        self.assertTrue(row["ok"])  # signals 0.25 mm on both sides leave no room for a via: listed, not failed
        self.assertEqual(row["no_room"]["count"], 1)
        self.assertEqual((row["no_room"]["length_mm"], row["no_room"]["share_pct"]), (6.0, 60.0))  # 6 of the line's 10 mm
        self.assertEqual((row["no_room"]["without_via"], row["no_room"]["without_via_mm"]), (0, 0.0))
        hemmed = GD.Obstacles(TP.Copper(layer_count=2), TP.Plan(traces=[TP.Trace("A", 1, 0.1, [(1.25, 0.0), (1.25, 12.0)]),
                                                                       TP.Trace("B", 1, 0.1, [(0.75, 0.0), (0.75, 12.0)])]), RULES)
        bare = RQ.measure({"traces": model["traces"], "vias": []}, requirement, [], hemmed)[0]
        self.assertTrue(bare["ok"])  # no room anywhere, so it passes, grounded at its ends alone, which the result says
        self.assertEqual((bare["no_room"]["without_via"], bare["no_room"]["without_via_mm"], bare["no_room"]["share_pct"]), (1, 10.0, 100.0))

    def test_a_closed_loop_counts_the_gap_through_its_start_and_named_spots_keep_clear_of_each_other(self):
        loop = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
        vias = [via("GND", x, 0.0) for x in (1.8, 3.8, 5.8, 7.8, 9.8)] + [via("GND", 10.0, y) for y in (1.8, 3.8, 5.8, 7.8, 9.8)]
        vias += [via("GND", x, 10.0) for x in (8.2, 6.2, 4.2, 2.2, 0.2)] + [via("GND", 0.0, y) for y in (8.2, 6.2, 4.2, 2.2)]
        model = {"traces": [trace("GND", loop)], "vias": vias}
        free = GD.Obstacles(TP.Copper(layer_count=2), TP.Plan(vias=[TP.Via("GND", tuple(v["circle"][:2]), "V", 0.2) for v in vias]), RULES)
        before = len(free.cells)
        row = RQ.measure(model, parsed({"kind": "via_pitch", "net": "GND", "max_mm": 2.0}), [], free)[0]
        self.assertEqual((row["ok"], row["failed"]), (False, 1))  # 2.2 + 1.8 mm through the loop's start, at (0, 0)
        self.assertEqual(row["worst"][0]["gap_mm"], 4.0)
        self.assertEqual(len(free.cells), before)  # the obstacles are left as they were

    def test_a_via_stops_a_line_at_each_place_the_line_touches_it(self):
        back = [(5.0, 0.2), (3.0, 0.0), (12.0, 0.0)]  # an end joined back to a via on its own line
        widths = [0.2, 0.2]
        self.assertEqual([round(at, 3) for at in GD.contacts(back, widths, (5.0, 0.2), 0.2)], [0.0, 4.01])
        self.assertEqual([round(at, 3) for at in GD.contacts(back, widths, (8.0, 0.0), 0.2)], [7.01])
        self.assertEqual(GD.contacts(back, widths, (8.0, 1.0), 0.2), [])
        self.assertEqual(GD.contacts(back, [0.2, 0.1], (8.0, 0.28), 0.2), [])  # the pad clears the thin piece's copper
        self.assertEqual(len(GD.contacts(back, [0.2, 0.2], (8.0, 0.28), 0.2)), 1)

    def test_plane_on_each_layer_and_hatched_over_the_bends(self):
        bend = (0.0, 10.0, 10.0, 12.0)
        planes = [{"net": "GND", "layer": 1, "hatch": "solid", "extents_mm": [0, 0, 10, 10]},
                  {"net": "GND", "layer": 1, "hatch": "diagonal", "extents_mm": [0, 10, 10, 12]},
                  {"net": "GND", "layer": 2, "hatch": "solid", "extents_mm": [0, 0, 10, 40]}]
        row = RQ.measure({"planes": planes}, parsed({"kind": "plane", "net": "GND", "layers": [1, 2, 3], "hatched_in_bends": True}), [bend])[0]
        problems = {(r["layer"], r["problem"]) for r in row["worst"]}
        self.assertEqual(problems, {(2, "the bend zone is not hatched"), (3, "no pour of GND")})
        unpoured = RQ.measure({}, parsed({"kind": "plane", "net": "GND", "layers": [1]}), [])[0]
        self.assertIsNone(unpoured["ok"])  # a plan's copper has no planes yet

    def test_a_pour_that_made_no_copper_fails_and_a_pitch_without_the_rules_is_not_judged(self):
        planes = [{"net": "GND", "layer": 1, "hatch": "solid", "extents_mm": [0, 0, 10, 10], "generated": False}]
        row = RQ.measure({"planes": planes}, parsed({"kind": "plane", "net": "GND", "layers": [1]}), [])[0]
        self.assertEqual(row["worst"], [{"layer": 1, "problem": "the pour of GND made no copper"}])
        hatched = [{"net": "GND", "layer": 1, "hatch": "diagonal", "extents_mm": [0, 10, 5, 12]},
                   {"net": "GND", "layer": 1, "hatch": "diagonal", "extents_mm": [5, 10, 10, 12]}]  # a band cut in two
        row = RQ.measure({"planes": hatched}, parsed({"kind": "plane", "net": "GND", "layers": [1], "hatched_in_bends": True}),
                         [(0.0, 10.0, 10.0, 12.0)])[0]
        self.assertTrue(row["ok"])
        row = RQ.measure({"traces": [trace("GND", [(0, 0), (0, 9)])]}, parsed({"kind": "via_pitch", "net": "GND", "max_mm": 2}), [])[0]
        self.assertIsNone(row["ok"])
        self.assertIn("not known", row["reason"])

    def test_a_requirement_that_cannot_be_measured_says_why(self):
        row = RQ.measure({"traces": []}, parsed({"kind": "ring", "net": "GND", "layers": [1], "offset_mm": 0.5, "min_pct": 90,
                                                  "label": "ring"}), [])[0]
        self.assertEqual((row["ok"], row["label"]), (False, "ring"))
        self.assertIn("no outline", row["reason"])


class OpeningAndTextTest(unittest.TestCase):
    EARS = [{"points_mm": [[10.0, 18.0], [12.0, 18.0], [12.0, 22.0], [10.0, 22.0]]},
            {"points_mm": [[0.0, 30.0], [-2.0, 30.0], [-2.0, 34.0], [0.0, 34.0]]}]

    def test_each_ear_needs_an_opening_that_bares_the_net_alone(self):
        model = {"ears": self.EARS, "openings": [
            {"side": "bottom", "kind": "soldermask", "extents_mm": [10.0, 18.0, 12.0, 22.0], "bared": ["GND"],
             "near": [{"net": "GND", "gap_mm": 0.0, "at_mm": [10, 18]}]},
            {"side": "bottom", "kind": "soldermask", "extents_mm": [-2.0, 30.0, 0.0, 34.0], "bared": ["GND"],
             "near": [{"net": "GND", "gap_mm": 0.0, "at_mm": [-2, 30]}, {"net": "SIG", "gap_mm": 0.05, "at_mm": [0, 31]}]}]}
        found = RQ.measure(model, parsed({"kind": "opening", "net": "GND", "at": "ears", "side": "bottom"}), [])[0]
        loose = RQ.measure(model, parsed({"kind": "opening", "net": "GND", "at": "ears", "side": "bottom", "clearance_mm": 0.04}), [])[0]
        top = RQ.measure(model, parsed({"kind": "opening", "net": "GND", "at": "ears", "side": "top"}), [])[0]
        self.assertEqual((found["ok"], found["count"], found["failed"]), (False, 2, 1))
        self.assertEqual(found["worst"][0], {"at_mm": [-1.0, 32.0], "problem": "SIG's copper comes 0.05 mm from the opening",
                                             "near_mm": [0, 31]})
        self.assertTrue(loose["ok"])
        self.assertEqual([w["problem"] for w in top["worst"]], ["no top opening"] * 2)

    def test_each_stiffener_needs_its_opening_and_a_board_without_them_says_so(self):
        model = {"keepouts": [{"kind": "placement", "side": "top", "height_mm": 0.15, "extents_mm": [0.0, 0.0, 10.0, 4.0]},
                             {"kind": "placement", "side": "bottom", "height_mm": 0.15, "extents_mm": [0.0, 36.0, 10.0, 40.0]}],
                 "ears": [],
                 "openings": [{"side": "top", "kind": "soldermask", "extents_mm": [6.0, 0.5, 9.0, 1.5], "bared": ["GND"],
                               "near": [{"net": "GND", "gap_mm": 0.0, "at_mm": [6, 0.5]}]}]}
        stiffened = RQ.measure(model, parsed({"kind": "opening", "net": "GND", "at": "stiffeners", "side": "top"}), [])[0]
        no_ears = RQ.measure(model, parsed({"kind": "opening", "net": "GND", "at": "ears", "side": "top"}), [])[0]
        planned = RQ.measure({}, parsed({"kind": "opening", "net": "GND", "at": "ears", "side": "top"}), [])[0]
        self.assertEqual((stiffened["ok"], stiffened["count"]), (True, 1))  # the bottom stiffener's glue meets the bottom
        self.assertEqual((no_ears["ok"], no_ears["count"]), (False, 0))
        self.assertIn("the board has no ears", no_ears["reason"])
        self.assertIsNone(planned["ok"])

    def test_a_text_on_its_layer(self):
        model = {"board_texts": [{"text": "BOARD_A", "place": "top"}, {"text": "BOARD_B", "place": "user:silk"}]}
        on_top = RQ.measure(model, parsed({"kind": "text", "text": "BOARD_A"}), [])[0]
        on_silk = RQ.measure(model, parsed({"kind": "text", "text": "BOARD_B", "user_layer": "silk"}), [])[0]
        moved = RQ.measure(model, parsed({"kind": "text", "text": "BOARD_A", "user_layer": "silk"}), [])[0]
        self.assertEqual((on_top["ok"], on_silk["ok"], moved["ok"]), (True, True, False))
        self.assertEqual(moved["worst"][0]["problem"], "not on the user layer silk; it lies on top")

    def test_wrong_values_are_named(self):
        for item, words in (({"kind": "opening", "net": "GND", "at": "tabs", "side": "top"}, "at, ears or stiffeners"),
                            ({"kind": "opening", "net": "GND", "at": "ears", "side": "left"}, "side, top or bottom"),
                            ({"kind": "text", "text": ""}, "names a text")):
            with self.assertRaises(PlanError) as caught:
                parsed(item)
            self.assertIn(words, caught.exception.message)


class ChainTest(unittest.TestCase):
    def test_traces_end_to_end_are_one_run_and_a_branch_splits_them(self):
        traces = [trace("GND", [(0, 0), (0, 5)]), trace("GND", [(0, 10), (0, 5)]), trace("GND", [(0, 10), (3, 10)], width=0.2),
                  trace("GND", [(0, 10), (0, 12)])]
        runs = sorted((round(RQ._length(points), 3), min(widths)) for _layer, points, widths in RQ.chains(traces))
        self.assertEqual(runs, [(2.0, 0.1), (3.0, 0.2), (10.0, 0.1)])


if __name__ == "__main__":
    unittest.main()
