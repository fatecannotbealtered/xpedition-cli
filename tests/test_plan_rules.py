"""The rules file of constraint get and constraint import: parsing and the changes it makes."""
from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import PlanError
from xpedition_cli.adapter.plan import rules as R

STOCK = {"width_mm": 0.254, "min_width_mm": 0.254, "max_width_mm": 0.254, "differential_spacing_mm": 0.254}
CLEAR = {name: 0.254 for name in R.CLEARANCE_FIELDS}


def board(**extra) -> R.Current:
    nets = ["A_P", "A_N", "B_P", "B_N", "VBUS", "GND"]
    current = R.Current(layers=["1", "2"], nets=nets, net_class_of={n: R.DEFAULT_CLASS for n in nets},
                        net_classes={R.DEFAULT_CLASS: {"1": dict(STOCK), "2": dict(STOCK)}},
                        clearances={R.DEFAULT_RULE: {"1": dict(CLEAR), "2": dict(CLEAR)}},
                        class_clearances={(R.ALL_CLASSES, R.ALL_CLASSES): R.DEFAULT_RULE})
    for key, value in extra.items():
        setattr(current, key, value)
    return current


class ParseTest(unittest.TestCase):
    def test_a_full_file_parses(self) -> None:
        rules = R.parse({"net_classes": [{"name": "USB", "nets": ["A_P", "A_N", "A_P"],
                                          "layers": {"all": {"width_mm": 0.1}, "2": {"width_mm": 0.12}}}],
                         "differential_pairs": [{"nets": ["A_P", "A_N"]}],
                         "clearances": [{"rule": "FINE", "layers": {"all": {"trace_to_pad_mm": 0}}, "classes": [["USB", "(All)"]]}]})
        self.assertEqual(rules["net_classes"][0]["nets"], ["A_P", "A_N"])  # a repeated net counts once
        self.assertEqual(rules["differential_pairs"], [["A_N", "A_P"]])
        self.assertEqual(rules["clearances"][0]["layers"]["all"], {"trace_to_pad_mm": 0.0})  # a clearance may be 0

    def test_malformed_files_are_refused_with_the_reason(self) -> None:
        cases = [
            ([], "one JSON object"),
            ({}, "names no net class"),
            ({"classes": []}, "unknown key 'classes'"),
            ({"net_classes": [{"name": "X", "layers": {"all": {"width_mm": 0}}}]}, "must be more than 0"),
            ({"net_classes": [{"name": "X", "layers": {"all": {"width": 0.1}}}]}, "has no field 'width'"),
            ({"net_classes": [{"name": "X", "layers": {"top": {"width_mm": 0.1}}}]}, "is not a conductor layer number"),
            ({"net_classes": [{"name": "X", "layers": {"1": {"width_mm": 30}}}]}, "at most 25 mm"),
            ({"net_classes": [{"name": "X"}, {"name": "X"}]}, "named twice"),
            ({"net_classes": [{"name": "(All)"}]}, "is not a net class"),
            ({"differential_pairs": [{"nets": ["A", "A"]}]}, "two different net names"),
            ({"clearances": [{"rule": "R", "classes": [["A"]]}]}, "[from class, to class] pairs"),
        ]
        for data, words in cases:
            with self.subTest(words=words), self.assertRaises(PlanError) as caught:
                R.parse(data)
            self.assertEqual(caught.exception.code, "E_VALIDATION")
            self.assertIn(words, caught.exception.message)

    def test_lengths_convert_to_and_from_thousandths_of_an_inch(self) -> None:
        self.assertEqual(R.th(0.254), 10.0)
        self.assertEqual(R.mm(3.937), 0.1)


class PlanTest(unittest.TestCase):
    def test_a_new_class_moves_its_nets_and_sets_every_layer(self) -> None:
        rules = R.parse({"net_classes": [{"name": "USB", "nets": ["A_P", "A_N"],
                                          "layers": {"all": {"width_mm": 0.1, "differential_spacing_mm": 0.12}}}]})
        plan = R.plan(rules, board())
        self.assertEqual(plan.blocked, [])
        self.assertEqual(plan.new_classes, ["USB"])
        self.assertEqual(plan.moves, {"A_P": "USB", "A_N": "USB"})
        # the new class starts from the default's 0.254 mm limits, so the minimum follows the width down
        row = {"width_mm": 0.1, "min_width_mm": 0.1, "differential_spacing_mm": 0.12}
        self.assertEqual(plan.class_values["USB"], {"1": row, "2": row})
        fields = {(c["path"], c["field"]): (c["before"], c["after"]) for c in plan.changes}
        self.assertEqual(fields[("/constraints/net-classes/USB", "exists")], (False, True))
        self.assertEqual(fields[("/constraints/nets/A_P", "net_class")], ("(Default)", "USB"))
        self.assertEqual(fields[("/constraints/net-classes/USB/layers/2", "width_mm")], (None, 0.1))

    def test_values_already_in_place_make_no_change(self) -> None:
        current = board(net_classes={R.DEFAULT_CLASS: {"1": dict(STOCK), "2": dict(STOCK)},
                                     "USB": {"1": {**STOCK, "width_mm": 0.1, "min_width_mm": 0.08},
                                             "2": {**STOCK, "width_mm": 0.1, "min_width_mm": 0.08}}},
                        net_class_of={"A_P": "USB", "A_N": "USB", "B_P": "(Default)", "B_N": "(Default)", "VBUS": "(Default)",
                                      "GND": "(Default)"},
                        differential_pairs=[["A_N", "A_P"]])
        rules = R.parse({"net_classes": [{"name": "USB", "nets": ["A_P"], "layers": {"all": {"width_mm": 0.1}}}],
                         "differential_pairs": [{"nets": ["A_P", "A_N"]}]})
        plan = R.plan(rules, current)
        self.assertTrue(plan.empty())
        self.assertEqual(plan.blocked, [])

    def test_a_named_layer_overrides_all(self) -> None:
        rules = R.parse({"net_classes": [{"name": "USB", "layers": {"all": {"width_mm": 0.1}, "2": {"width_mm": 0.15}}}]})
        self.assertEqual(R.plan(rules, board()).class_values["USB"], {"1": {"width_mm": 0.1, "min_width_mm": 0.1},
                                                                       "2": {"width_mm": 0.15, "min_width_mm": 0.15}})

    def test_what_stands_in_the_way_is_blocked(self) -> None:
        current = board(differential_pairs=[["B_N", "B_P"]])
        rules = R.parse({"net_classes": [{"name": "USB", "nets": ["NOPE"], "layers": {"3": {"width_mm": 0.1}}},
                                         {"name": "PWR", "nets": ["VBUS"],
                                          "layers": {"all": {"width_mm": 0.3, "max_width_mm": 0.2}}},
                                         {"name": "OTHER", "nets": ["VBUS"]}],
                         "differential_pairs": [{"nets": ["B_P", "A_P"]}],
                         "clearances": [{"rule": "FINE", "classes": [["USB", "MISSING"]]}]})
        blocked = " | ".join(R.plan(rules, current).blocked)
        self.assertIn("the board has no net NOPE", blocked)
        self.assertIn("there is no layer 3", blocked)
        self.assertIn("max_width_mm 0.2 is less than width_mm 0.3", blocked)
        self.assertIn("VBUS is named under both PWR and OTHER", blocked)
        self.assertIn("B_P is in the pair B_N,B_P already", blocked)
        self.assertIn("there is no net class MISSING", blocked)

    def test_a_min_above_the_board_width_is_blocked(self) -> None:
        rules = R.parse({"net_classes": [{"name": R.DEFAULT_CLASS, "layers": {"all": {"min_width_mm": 0.3}}}]})
        self.assertIn("min_width_mm 0.3 is more than width_mm 0.254", " ".join(R.plan(rules, board()).blocked))

    def test_a_wider_width_raises_the_maximum(self) -> None:
        rules = R.parse({"net_classes": [{"name": "PWR", "layers": {"1": {"width_mm": 0.5}}}]})
        self.assertEqual(R.plan(rules, board()).class_values["PWR"], {"1": {"width_mm": 0.5, "max_width_mm": 0.5}})

    def test_a_clearance_rule_and_its_class_pair(self) -> None:
        rules = R.parse({"net_classes": [{"name": "USB", "nets": ["A_P"]}],
                         "clearances": [{"rule": "FINE", "layers": {"all": {"trace_to_pad_mm": 0.1}}, "classes": [["USB", "(All)"]]},
                                        {"rule": R.DEFAULT_RULE, "layers": {"1": {"trace_to_trace_mm": 0.254}}}]})
        plan = R.plan(rules, board())
        self.assertEqual(plan.new_rules, ["FINE"])
        self.assertEqual(plan.rule_values, {"FINE": {"1": {"trace_to_pad_mm": 0.1}, "2": {"trace_to_pad_mm": 0.1}}})
        self.assertEqual(plan.class_rules, {("USB", "(All)"): "FINE"})
        self.assertNotIn(R.DEFAULT_RULE, plan.rule_values)  # its value is already 0.254

    def test_as_file_round_trips(self) -> None:
        current = board(differential_pairs=[["A_N", "A_P"]])
        again = R.parse(current.as_file())
        self.assertTrue(R.plan(again, current).empty())


if __name__ == "__main__":
    unittest.main()
