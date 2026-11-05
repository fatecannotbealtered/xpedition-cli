"""A symbol may not display the same pin name twice.

Designer names a net after the pin a wire meets, so two pins of one symbol
sharing a displayed name put their wires on the same auto-named net; the second
explicit label is then a second label on a labelled net and the draw stops with
6035 "Net already labeled", minutes in and partway through. The plan is the place
to catch it, where the preview reports it for free.
"""

from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan.schematic_layout import DesignError, _symbol_from_spec


def spec(**sides):
    return {"kind": "box", **sides}


class DuplicatePinNamesTest(unittest.TestCase):
    def test_a_repeated_pin_name_is_refused_with_the_pins_that_share_it(self):
        with self.assertRaises(DesignError) as caught:
            _symbol_from_spec("J1", spec(left=[["1", "PROBE_A"], ["3", "PROBE_A"]]))
        message = str(caught.exception)
        self.assertIn("PROBE_A", message)
        self.assertIn("left pin 1", message)
        self.assertIn("left pin 3", message)
        self.assertIn("6035", message, msg="name the Designer error the caller would otherwise hit")

    def test_the_repeat_is_caught_across_sides(self):
        with self.assertRaises(DesignError) as caught:
            _symbol_from_spec("U1", spec(left=[["1", "GND"]], bottom=[["9", "GND"]]))
        self.assertIn("left pin 1", str(caught.exception))
        self.assertIn("bottom pin 9", str(caught.exception))

    def test_distinct_names_are_accepted(self):
        # The working shape from the report: distinct pin names, net on the label.
        symbol = _symbol_from_spec("J1", spec(left=[["1", "A_HI"], ["2", "A_NC"], ["3", "A_LO"]]))
        self.assertEqual([pin.name for pin in symbol.pins], ["A_HI", "A_NC", "A_LO"])

    def test_a_pin_named_after_its_own_net_is_fine_when_it_is_the_only_one(self):
        symbol = _symbol_from_spec("U1", spec(left=[["1", "VCC"], ["2", "IN"]]))
        self.assertEqual([pin.name for pin in symbol.pins], ["VCC", "IN"])

    def test_gap_rows_do_not_count_as_repeats(self):
        # An empty (number, name) pair separates pin groups; several are normal.
        symbol = _symbol_from_spec(
            "U1", spec(left=[["1", "A"], ["", ""], ["2", "B"], ["", ""], ["3", "C"]])
        )
        self.assertEqual([pin.name for pin in symbol.pins], ["A", "B", "C"])

    def test_the_report_lists_every_repeated_name(self):
        with self.assertRaises(DesignError) as caught:
            _symbol_from_spec(
                "J1",
                spec(
                    left=[["1", "GND"], ["2", "GND"]],
                    right=[["3", "NC"], ["4", "NC"]],
                ),
            )
        message = str(caught.exception)
        self.assertIn("GND", message)
        self.assertIn("NC", message)
