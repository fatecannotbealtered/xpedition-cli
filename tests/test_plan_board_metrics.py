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
