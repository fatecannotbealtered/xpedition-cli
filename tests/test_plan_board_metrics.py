"""Numbers for how good a placement and its routing are, from a board's geometry."""

from __future__ import annotations

import math
import unittest

from xpedition_cli.adapter.plan import board_metrics as M


def _pin(name: str, x: float, y: float, net: str) -> dict:
    return {"pin": name, "x": x, "y": y, "net": net, "layer": 1}


def _part(refdes: str, extents: list[float], *pins: dict, placed: bool = True) -> dict:
    return {
        "refdes": refdes,
        "cell": "CELL",
        "placed": placed,
        "extents": extents,
        "pins": list(pins),
    }


def _board() -> dict:
    """A 40 x 30 mm board with one of each thing the metrics look for."""
    return {
        "outline": [{"path": [[0, 0, 0], [40, 0, 0], [40, 30, 0], [0, 30, 0], [0, 0, 0]]}],
        "pads": [],
        "vias": [{"circle": [5, 5, 0.3], "net": "GND"}, {"circle": [6, 5, 0.3], "net": "GND"}],
        "traces": [
            # a 45-degree corner that folds back on itself (acute), then a clean 90-degree one
            {"net": "NETA", "layer": 1, "width": 0.25, "path": [[20, 2], [24, 2], [22, 4]]},
            {"net": "NETB", "layer": 2, "width": 0.25, "path": [[20, 8], [20, 12], [24, 12]]},
        ],
        "components": [
            _part(
                "U1",
                [10, 10, 16, 16],
                _pin("VDD", 10.5, 15.5, "+3V3"),
                _pin("GND", 15.5, 10.5, "GND"),
            ),
            # decoupling: C1 beside U1's supply pin, C2 across the board
            _part("C1", [8, 15, 9, 16], _pin("1", 8.2, 15.5, "+3V3"), _pin("2", 8.8, 15.5, "GND")),
            _part(
                "C2", [35, 25, 36, 26], _pin("1", 35.2, 25.5, "+3V3"), _pin("2", 35.8, 25.5, "GND")
            ),
            _part("R1", [15, 12, 17, 13]),  # overlaps U1 by 1 x 1 mm
            _part("R2", [38, 5, 42, 6]),  # 2 mm past the right edge
            _part("R3", [], placed=False),
            _part("J1", [1, 5, 4, 9]),  # 1 mm from the left edge
            _part("J2", [18, 18, 21, 21]),  # 9 mm from the top edge
            # two signal nets whose airwires cross once
            _part("T1", [19.5, 1.5, 20.5, 2.5], _pin("1", 20, 2, "NETA")),
            _part("T2", [27.5, 7.5, 28.5, 8.5], _pin("1", 28, 8, "NETA")),
            _part("T3", [19.5, 7.5, 20.5, 8.5], _pin("1", 20, 8, "NETB")),
            _part("T4", [27.5, 1.5, 28.5, 2.5], _pin("1", 28, 2, "NETB")),
        ],
    }


# -- silkscreen texts ---------------------------------------------------------------------


def _label(string: str, box: list[float], refdes: str | None = None, **extra) -> dict:
    """A silkscreen text as the board read gives it: its box is Layout's extents."""
    return {
        "text": string,
        "refdes": refdes,
        "kind": "refdes" if refdes == string else "text",
        "side": "top",
        "height": 0.8,
        "pen": 0.12,
        "extents": box,
        **extra,
    }


def _labelled_board() -> dict:
    """A 40 x 30 mm board with one silkscreen text for each thing that makes one hard
    to read, and texts that are fine."""
    square = [[10.5, 15, 0], [11.5, 15, 0], [11.5, 15.8, 0], [10.5, 15.8, 0], [10.5, 15, 0]]
    return {
        "layers": 2,
        "outline": [{"path": [[0, 0, 0], [40, 0, 0], [40, 30, 0], [0, 30, 0], [0, 0, 0]]}],
        "pads": [{"path": square, "layer": 1, "refdes": "U1", "pin": "1", "net": "SIG"}],
        "vias": [{"circle": [33, 12, 0.3], "net": "GND"}],
        "holes": [[25, 25, 1.0], [33, 12, 0.3]],  # a bare hole and the via's
        "silk": [{"path": [[5, 25, 0], [15, 25, 0]], "width": 0.12, "side": "top", "refdes": "U1"}],
        "components": [
            _part("U1", [10, 10, 16, 16]),
            _part("R1", [20, 10, 22, 11]),
            _part("C1", [30, 20, 31, 21]),
        ],
        "texts": [
            _label("U1", [10.2, 15.5, 12.0, 16.4], "U1"),  # over U1.1
            _label("R1", [11.5, 16.0, 13.0, 16.9], "R1"),  # on U1's designator
            _label("C1", [20.5, 10.2, 21.5, 10.8], "C1"),  # on R1's place
            _label("J1", [38.5, 5, 41, 6], "J1"),  # 1 mm past the right edge
            _label("Q1", [30, 5, 31, 5.5], "Q1", height=0.4),  # too small
            _label("NOTE", [9, 24.8, 11, 25.6]),  # across U1's outline
            _label("K1", [24.8, 24.8, 26, 25.4], "K1"),  # over the bare hole
            _label("V1", [32.8, 11.8, 34, 12.4], "V1"),  # over a via: not reported
            _label("B1", [10.2, 15.5, 12.0, 16.4], "B1", side="bottom"),  # the other side
            _label("R2", [2, 2, 3, 2.8], "R2"),  # clear of everything
        ],
    }


class BoardMetricsTest(unittest.TestCase):
    def test_the_metrics_of_a_board_state(self):
        result = M.measure(_board())
        summary = result["summary"]
        self.assertEqual(result["board"], {"width_mm": 40.0, "height_mm": 30.0})
        # airwires: NETA and NETB 10 mm each and crossing once; the rails' trees on top
        self.assertEqual(summary["signal_ratsnest_mm"], 20.0)
        self.assertEqual(summary["crossings"], 1)
        supply = math.dist((8.2, 15.5), (10.5, 15.5)) + math.dist((10.5, 15.5), (35.2, 25.5))
        ground = math.dist((15.5, 10.5), (8.8, 15.5)) + math.dist((15.5, 10.5), (35.8, 25.5))
        self.assertAlmostEqual(summary["ratsnest_mm"], 20 + supply + ground, delta=0.01)
        self.assertEqual(result["ratsnest"]["supply_nets"], ["+3V3", "GND"])
        self.assertEqual(result["overlaps"], [{"a": "R1", "b": "U1", "mm2": 1.0}])
        self.assertEqual(result["outside"], [{"refdes": "R2", "beyond_mm": 2.0}])
        self.assertEqual(result["parts"]["unplaced"], ["R3"])
        self.assertEqual(summary["unplaced"], 1)
        # the far capacitor first: 26.6 mm from U1.VDD is not decoupling it
        self.assertEqual([(r["capacitor"], r["nearest"]) for r in result["decoupling"]], [
            ("C2", "U1.VDD"),
            ("C1", "U1.VDD"),
        ])
        self.assertEqual(result["decoupling"][1]["mm"], 2.3)
        self.assertAlmostEqual(summary["decoupling_max_mm"], math.dist((35.2, 25.5), (10.5, 15.5)), delta=0.001)
        self.assertEqual(result["edge_parts"], [
            {"refdes": "J2", "edge_mm": 9.0},
            {"refdes": "J1", "edge_mm": 1.0},
        ])
        routing = result["routing"]
        self.assertEqual(routing["vias"], 2)
        self.assertEqual(set(routing["by_layer"]), {"1", "2"})
        self.assertAlmostEqual(routing["by_layer"]["1"], 6.828, delta=0.001)
        self.assertEqual(routing["by_layer"]["2"], 8.0)
        self.assertEqual(routing["acute_corners"], [
            {"net": "NETA", "layer": 1, "at": [24.0, 2.0], "degrees": 45.0}
        ])
        self.assertEqual(summary["acute_corners"], 1)
        self.assertEqual(summary["overlaps"], 1)
        self.assertEqual(summary["outside"], 1)

    def test_an_arc_is_measured_along_its_curve_and_is_no_corner(self):
        # a quarter circle of radius 2 from (0, 0) about (0, 2) to (2, 2), the way Layout reports it
        model = {"traces": [{"net": "A", "layer": 1, "path": [[0.0, 0.0, 0.0], [0.0, 2.0, -2.0], [2.0, 2.0, 0.0]]}]}
        routing = M._routing(model)
        self.assertEqual(routing["acute_corners"], [])
        self.assertAlmostEqual(routing["by_layer"]["1"], 3.1416, delta=0.01)

    def test_supply_and_ground_nets_are_told_from_signals_by_name(self):
        kinds = {
            name: M.net_kind(name)
            for name in ("GND", "AGND", "VSS", "+3V3", "VBUS", "AVDD", "5V", "SDA", "MCU_RST_N")
        }
        self.assertEqual(kinds, {
            "GND": "ground",
            "AGND": "ground",
            "VSS": "ground",
            "+3V3": "supply",
            "VBUS": "supply",
            "AVDD": "supply",
            "5V": "supply",
            "SDA": "signal",
            "MCU_RST_N": "signal",
        })

    def test_an_empty_board_measures_without_failing(self):
        result = M.measure({"pads": [], "components": []})
        self.assertIsNone(result["board"])
        self.assertEqual(result["summary"]["ratsnest_mm"], 0)
        self.assertIsNone(result["summary"]["decoupling_max_mm"])
        self.assertIsNone(result["summary"]["density"])

    def test_a_move_is_compared_with_the_baseline(self):
        before = M.measure(_board())
        board = _board()
        # C2 moved next to U1's supply pin
        board["components"][2] = _part(
            "C2", [8, 13, 9, 14], _pin("1", 8.2, 13.5, "+3V3"), _pin("2", 8.8, 13.5, "GND")
        )
        delta = M.compare(M.measure(board), before)
        self.assertEqual(set(delta), set(M.SUMMARY_KEYS))
        decoupling = delta["decoupling_max_mm"]
        self.assertGreater(decoupling["before"], 26)
        self.assertEqual(decoupling["after"], 3.048)
        self.assertLess(decoupling["change"], -20)
        self.assertLess(delta["ratsnest_mm"]["change"], 0)
        self.assertEqual(delta["crossings"], {"before": 1, "after": 1, "change": 0})

    def test_board_rules_list_what_drc_does_not(self):
        rules = M.board_rules({**_board(), "unrouted": [{"net": "NETA", "opens": 2}]})
        found = {(rule["rule"], rule.get("refdes") or rule.get("net")) for rule in rules}
        self.assertIn(("unplaced", "R3"), found)
        self.assertIn(("outside", "R2"), found)
        self.assertIn(("overlap", "R1"), found)
        self.assertIn(("decoupling", "C2"), found)
        self.assertNotIn(("decoupling", "C1"), found)
        self.assertIn(("connector_edge", "J2"), found)
        self.assertNotIn(("connector_edge", "J1"), found)
        self.assertIn(("acute_corner", "NETA"), found)
        self.assertIn(("unrouted", "NETA"), found)
        # the worst first
        severities = [rule["severity"] for rule in rules]
        self.assertEqual(severities, sorted(severities, key=["high", "medium", "low"].index))

    def test_a_clean_board_has_no_board_rules(self):
        board = {
            "outline": [{"path": [[0, 0, 0], [20, 0, 0], [20, 20, 0], [0, 20, 0], [0, 0, 0]]}],
            "traces": [],
            "vias": [],
            "components": [_part("U1", [5, 5, 8, 8], _pin("1", 6, 6, "SIG"))],
        }
        self.assertEqual(M.board_rules(board), [])

    def test_label_rules_find_the_silkscreen_texts_that_will_not_read(self):
        rules = M.label_rules(_labelled_board())
        found = {(rule["rule"], rule["text"], rule.get("other")) for rule in rules}
        self.assertIn(("label_over_pad", "U1", "U1.1"), found)
        self.assertIn(("label_overlap", "U1", "R1"), found)
        self.assertIn(("label_under_part", "C1", "R1"), found)
        self.assertIn(("label_off_board", "J1", None), found)
        self.assertIn(("label_small", "Q1", None), found)
        self.assertIn(("label_over_silk", "NOTE", "U1"), found)
        self.assertIn(("label_over_pad", "K1", "hole at 25.00,25.00"), found)
        reported = {rule["text"] for rule in rules} | {rule.get("other") for rule in rules}
        self.assertFalse({"V1", "B1", "R2"} & reported)
        self.assertEqual(len(rules), 7)
        severity = {rule["rule"]: rule["severity"] for rule in rules}
        self.assertEqual(severity, {
            "label_over_pad": "medium",
            "label_overlap": "medium",
            "label_off_board": "medium",
            "label_under_part": "low",
            "label_over_silk": "low",
            "label_small": "low",
        })
        off = next(rule for rule in rules if rule["rule"] == "label_off_board")
        self.assertIn("1.0 mm past", off["finding"])
        self.assertEqual(off["refdes"], "J1")
        self.assertEqual(off["at"], [39.75, 5.5])

    def test_the_lowest_text_that_passes_can_be_raised(self):
        small = {
            rule["text"] for rule in M.label_rules(_labelled_board()) if rule["rule"] == "label_small"
        }
        self.assertEqual(small, {"Q1"})
        strict = M.board_rules(_labelled_board(), min_text_height=1.0)
        small = {rule["text"] for rule in strict if rule["rule"] == "label_small"}
        self.assertIn("R2", small)
        self.assertIn("Q1", small)
        lenient = M.label_rules(_labelled_board(), min_text_height=0.3)
        self.assertFalse([rule for rule in lenient if rule["rule"] == "label_small"])

    def test_texts_without_a_box_are_not_judged(self):
        board = _labelled_board()
        for label in board["texts"]:
            label.pop("extents")
        self.assertEqual(M.label_rules(board), [])

    def test_silk_rules_name_the_silkscreen_over_pads_once_per_part(self):
        square = [[0, 0, 0], [0.6, 0, 0], [0.6, 0.55, 0], [0, 0.55, 0], [0, 0, 0]]

        def pad(pin: str, dx: float, refdes: str = "C1") -> dict:
            return {
                "path": [[x + dx, y, r] for x, y, r in square],
                "layer": 1,
                "refdes": refdes,
                "pin": pin,
            }

        board = {
            "layers": 2,
            "pads": [pad("1", 0), pad("2", 1.0), pad("1", 5.0, "R1")],
            "silk": [
                # C1's box straight through both its pads (the old placeholder outline)
                {"path": [[0.3, -0.2, 0], [0.3, 0.8, 0]], "width": 0.12, "side": "top", "refdes": "C1"},
                {"path": [[1.3, -0.2, 0], [1.3, 0.8, 0]], "width": 0.12, "side": "top", "refdes": "C1"},
                # R1's line clear of its pad, a board line over it, a bottom line ignored
                {"path": [[5, 1, 0], [6, 1, 0]], "width": 0.12, "side": "top", "refdes": "R1"},
                {"path": [[5.3, -1, 0], [5.3, 1, 0]], "width": 0.2, "side": "top"},
                {"path": [[5.3, -1, 0], [5.3, 1, 0]], "width": 0.2, "side": "bottom", "refdes": "R1"},
            ],
        }
        rules = M.silk_rules(board)
        found = {(rule["refdes"], rule["other"]): rule for rule in rules}
        self.assertEqual(set(found), {("C1", "C1"), (None, "R1")})
        self.assertEqual(found[("C1", "C1")]["pins"], ["1", "2"])
        self.assertIn("its pads 1, 2", found[("C1", "C1")]["finding"])
        self.assertIn("the board's silkscreen runs over R1's pads 1", found[(None, "R1")]["finding"])
        self.assertTrue(all(rule["severity"] == "low" for rule in rules))
        self.assertEqual([r["rule"] for r in M.board_rules(board)].count("silk_over_pad"), 2)


def _flex() -> dict:
    """A 20 x 100 mm flex board: stiffeners at both ends, a bend zone at y 40 to 50 where GND on layer 2 is hatched."""
    return {
        "outline": [{"path": [[0, 0, 0], [20, 0, 0], [20, 100, 0], [0, 100, 0], [0, 0, 0]]}],
        "components": [],
        "planes": [{"net": "GND", "layer": 2, "hatch": "solid", "extents_mm": [0, 0, 20, 100]},
                   {"net": "GND", "layer": 2, "hatch": "diagonal", "extents_mm": [0, 40, 20, 50]}],
        "keepouts": [{"kind": "placement", "height_mm": 0.15, "extents_mm": [0, 0, 20, 10]},
                     {"kind": "placement", "height_mm": 0.15, "extents_mm": [0, 90, 20, 100]}],
        "vias": [{"circle": [5, 45, 0], "net": "GND"}, {"circle": [5, 60, 0], "net": "SIG"}, {"circle": [5, 5, 0], "net": "SIG2"}],
        "traces": [
            {"net": "A", "layer": 1, "width": 0.1, "path": [[3, 20], [3, 80]]},
            {"net": "B", "layer": 3, "width": 0.1, "path": [[3.05, 20], [3.05, 80]]},
            {"net": "C", "layer": 1, "width": 0.1, "path": [[8, 20], [8, 45], [9, 46], [9, 80]]},
            {"net": "D", "layer": 1, "width": 0.1, "path": [[12, 20], [12, 45]]},
            {"net": "D", "layer": 1, "width": 0.2, "path": [[12, 45], [12, 80]]},
            {"net": "E", "layer": 3, "width": 0.1, "path": [[16, 20], [16, 80]]},
            {"net": "USB_DP", "layer": 1, "width": 0.1, "path": [[2, 60], [12, 60]]},
            {"net": "USB_DN", "layer": 1, "width": 0.1, "path": [[2, 61], [12.5, 61]]},
        ],
    }


class GuardCoverTest(unittest.TestCase):
    def test_ground_traces_beside_a_net_are_measured_per_side(self):
        def trace(net, layer, width, points):
            return {"net": net, "layer": layer, "width": width, "path": [[x, y, 0.0] for x, y in points]}

        model = {"traces": [trace("SIG", 3, 0.1, [(0, 0), (10, 0)]),
                            trace("GND", 3, 0.1, [(0, 0.3), (10, 0.3)]),  # left, 0.2 mm off the signal's copper
                            trace("GND", 3, 0.1, [(5, -0.3), (10, -0.3)]),  # right, along its second half
                            trace("GND", 1, 0.1, [(0, -0.3), (5, -0.3)]),  # another layer
                            trace("AGND_FAR", 3, 0.1, [(0, -1.0), (5, -1.0)])]}  # too far
        (row,) = M.guard_cover(model, [["SIG"]], 0.3)
        self.assertEqual((row["net"], row["layer"], row["length_mm"]), ("SIG", 3, 10.0))
        self.assertEqual((row["left_pct"], row["right_pct"], row["both_sides_pct"]), (100.0, 50.0, 50.0))
        self.assertEqual(row["unguarded_mm"], 0.0)
        (narrow,) = M.guard_cover(model, [["SIG"]], 0.1)  # the guards stand 0.2 mm off: not within 0.1 mm
        self.assertEqual((narrow["left_pct"], narrow["unguarded_mm"]), (0.0, 10.0))

    def test_the_two_of_a_pair_cover_each_others_inner_side(self):
        def trace(net, y):
            return {"net": net, "layer": 3, "width": 0.06, "path": [[0.0, y, 0.0], [10.0, y, 0.0]]}

        model = {"traces": [trace("P", 0.0), trace("N", 0.16), {**trace("GND", -0.25), "width": 0.1}, {**trace("GND", 0.41), "width": 0.1}]}
        rows = M.guard_cover(model, [["P", "N"]], 0.2)
        self.assertEqual([(r["net"], r["both_sides_pct"]) for r in rows], [("P", 100.0), ("N", 100.0)])
        alone = M.guard_cover(model, [["P"], ["N"]], 0.2)
        self.assertEqual([r["both_sides_pct"] for r in alone], [0.0, 0.0])

    def test_a_pair_set_wider_apart_than_the_ground_gap_still_covers_its_inner_side(self):
        def trace(net, y, width=0.1):
            return {"net": net, "layer": 1, "width": width, "path": [[0.0, y, 0.0], [10.0, y, 0.0]]}

        # the halves 0.4 mm apart, a guard 0.15 mm off each outer side, a gap of 0.3 mm asked
        model = {"traces": [trace("P", 0.0), trace("N", 0.5), trace("GND", -0.25), trace("GND", 0.75)]}
        rows = M.guard_cover(model, [["P", "N"]], 0.3)
        self.assertEqual([(r["net"], r["both_sides_pct"]) for r in rows], [("P", 100.0), ("N", 100.0)])
        far = {"traces": [trace("P", 0.0), trace("N", 1.5), trace("GND", -0.25), trace("GND", 1.75)]}  # 1.4 mm apart
        self.assertEqual([r["both_sides_pct"] for r in M.guard_cover(far, [["P", "N"]], 0.3)], [0.0, 0.0])


class FlexRuleTest(unittest.TestCase):
    def test_bend_zones_come_from_hatched_bands_beside_a_solid_plane(self):
        self.assertEqual(M.bend_zones(_flex()), [{"extents_mm": [0.0, 40.0, 20.0, 50.0], "along": "x", "marked_by": ["hatched_plane"]}])
        self.assertEqual(M.bend_zones({**_flex(), "planes": [{"net": "GND", "layer": 2, "hatch": "diagonal",
                                                                "extents_mm": [0, 0, 20, 100]}]}), [])  # all hatched: no zone

    def test_a_placement_keepout_across_a_stiffened_board_marks_a_bend_zone_too(self):
        board = _flex()
        board["planes"] = [{"net": "GND", "layer": 2, "hatch": "solid", "extents_mm": [1, 1, 19, 99]},
                           {"net": "GND", "layer": 2, "hatch": "diagonal", "extents_mm": [1, 40, 19, 50]}]
        board["keepouts"] = board["keepouts"] + [
            {"kind": "placement", "height_mm": None, "extents_mm": [1, 70, 19, 72]},  # across the board: a bend
            {"kind": "placement", "height_mm": None, "extents_mm": [2, 42, 18, 44]},  # inside the hatched band
            {"kind": "placement", "height_mm": None, "extents_mm": [1, 20, 4, 21]},  # a small keepout
        ]
        self.assertEqual(M.bend_zones(board), [
            {"extents_mm": [0.0, 40.0, 20.0, 50.0], "along": "x", "marked_by": ["hatched_plane", "placement_keepout"]},
            {"extents_mm": [0.0, 70.0, 20.0, 72.0], "along": "x", "marked_by": ["placement_keepout"]}])
        rigid = {**board, "keepouts": [k for k in board["keepouts"] if not k["height_mm"]]}
        self.assertEqual([z["marked_by"] for z in M.bend_zones(rigid)], [["hatched_plane"]])  # no stiffener: no keepout band
        self.assertEqual(M.stiffeners(_flex()), [(0.0, 0.0, 20.0, 10.0), (0.0, 90.0, 20.0, 100.0)])

    def test_a_keepout_from_edge_to_edge_marks_a_bend_on_a_board_without_stiffeners(self):
        def keepout(*box):
            return {"kind": "placement", "height_mm": None, "extents_mm": list(box)}

        board = {"outline": [{"path": [[0, 0, 0], [10, 0, 0], [10, 100, 0], [0, 100, 0], [0, 0, 0]]}],
                 "keepouts": [keepout(0, 40, 10, 47),  # across the neck, board on both sides: a bend, square or not
                              keepout(0, 0, 10, 2),  # a strip along the end of the board
                              keepout(1, 60, 9, 62)]}  # most of the width, not edge to edge
        self.assertEqual(M.bend_zones(board), [{"extents_mm": [0.0, 40.0, 10.0, 47.0], "along": "x", "marked_by": ["placement_keepout"]}])

    def test_a_slanted_bend_zone_is_judged_by_its_own_outline(self):
        # a neck from y 0 to 10, its bend drawn from y 2 to a top edge that falls from (0, 8) to (10, 6)
        bend = [[0, 2, 0], [10, 2, 0], [10, 6, 0], [0, 8, 0], [0, 2, 0]]
        board = {"outline": [{"path": [[0, -5, 0], [10, -5, 0], [10, 15, 0], [0, 15, 0], [0, -5, 0]]}],
                 "keepouts": [{"kind": "placement", "height_mm": None, "extents_mm": [0, 2, 10, 8], "shape": {"path": bend}}]}
        (zone,) = M.bend_zones(board)
        self.assertEqual(zone["polygon_mm"][:4], [[0.0, 2.0], [10.0, 2.0], [10.0, 6.0], [0.0, 8.0]])
        tilt = (2.0 / math.hypot(10, 2), 10.0 / math.hypot(10, 2))  # square to the top edge
        square = {"net": "S", "layer": 1, "width": 0.1, "path": [[5, 0], [5, 4], [5 + 4 * tilt[0], 4 + 4 * tilt[1]]]}
        upright = {"net": "U", "layer": 1, "width": 0.1, "path": [[2, 0], [2, 10]]}
        found = M.flex_rules({**board, "traces": [square, upright], "vias": [{"circle": [9.5, 7.5, 0.1], "net": "GND"}]})
        self.assertEqual({(r["rule"], r.get("net")) for r in found}, {("bend_angle", "U"), ("bend_turn", "S")})  # the via lies beyond the edge

    def test_what_a_bend_zone_and_the_stiffeners_forbid(self):
        found = {(row["rule"], row.get("net")) for row in M.flex_rules(_flex())}
        self.assertEqual(found, {("bend_via", "GND"), ("bend_turn", "C"), ("bend_angle", "C"), ("bend_width", "D"),
                                 ("bend_stacked", "A"), ("layer_change_outside_stiffener", "SIG")})
        self.assertEqual(M.flex_rules({**_flex(), "planes": []}), [])  # no bend zone known: no flex rule

    def test_a_band_runs_across_the_board_and_a_hatched_patch_is_no_bend(self):
        narrow = {**_flex(), "outline": [{"path": [[0, 0, 0], [10, 0, 0], [10, 100, 0], [0, 100, 0], [0, 0, 0]]}],
                  "planes": [{"net": "GND", "layer": 2, "hatch": "solid", "extents_mm": [0, 0, 10, 100]},
                             {"net": "GND", "layer": 2, "hatch": "diagonal", "extents_mm": [0, 40, 10, 52]}]}
        self.assertEqual(M.bend_zones(narrow), [{"extents_mm": [0.0, 40.0, 10.0, 52.0], "along": "x", "marked_by": ["hatched_plane"]}])
        rigid = {"outline": [{"path": [[0, 0, 0], [50, 0, 0], [50, 40, 0], [0, 40, 0], [0, 0, 0]]}],
                 "planes": [{"net": "GND", "layer": 2, "hatch": "solid", "extents_mm": [1, 1, 49, 39]},
                            {"net": "GND", "layer": 2, "hatch": "diagonal", "extents_mm": [40, 30, 48, 38]}]}
        self.assertEqual(M.bend_zones(rigid), [])  # a hatched patch, such as under an antenna

    def test_a_trace_along_a_bend_zone_edge_is_not_in_it(self):
        board = {**_flex(), "vias": [], "traces": [{"net": "F", "layer": 1, "width": 0.1, "path": [[2, 20], [2, 40], [8, 40], [8, 20]]}]}
        self.assertEqual(M.flex_rules(board), [])

    def test_pairs_by_name(self):
        self.assertEqual(M._pairs(["D+", "D-", "X_M", "X_N", "X_P", "INT_N"]), [("D+", "D-"), ("X_P", "X_N")])

    def test_a_pair_recognised_by_name_reports_its_skew(self):
        model = _flex()
        rules = M.board_rules(model)
        skew = next(row for row in rules if row["rule"] == "pair_skew")
        self.assertEqual((skew["net"], skew["other"]), ("USB_DP", "USB_DN"))
        self.assertIn("differ by 0.5 mm, 10.0 and 10.5 mm", skew["finding"])
        self.assertEqual(M.measure(model)["summary"]["pair_skew_max_mm"], 0.5)
        unrouted = M.measure({**model, "unrouted": [{"net": "USB_DP", "opens": 1}]})
        self.assertEqual(unrouted["routing"]["pairs"], [])  # a pair not routed yet has no skew

    def test_each_nets_drawn_widths_are_reported(self):
        model = {"traces": [{"net": "VCC", "layer": 1, "width": 0.15, "path": [[0, 0, 0], [5, 0, 0]]},
                            {"net": "VCC", "layer": 1, "width": 0.1, "path": [[5, 0, 0], [6, 0, 0]]},
                            {"net": "SIG", "layer": 3, "width": 0.1, "path": [[0, 1, 0], [5, 1, 0]]}]}
        self.assertEqual(M.measure(model)["routing"]["widths"], [{"net": "SIG", "min_mm": 0.1, "max_mm": 0.1},
                                                                 {"net": "VCC", "min_mm": 0.1, "max_mm": 0.15}])

    def test_the_pairs_the_design_defines_replace_the_guess_by_name(self):
        self.assertEqual(M._pairs(["D+", "D-", "X_N", "X_P"], [["X_N", "X_P"], ["Y_P", "Y_N"]]), [("X_N", "X_P")])
        model = {**_flex(), "differential_pairs": [["MIPI_P", "MIPI_N"]]}  # USB_DP and USB_DN are no defined pair
        self.assertFalse([row for row in M.board_rules(model) if row["rule"] == "pair_skew"])
