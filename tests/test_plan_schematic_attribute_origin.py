"""A part's attribute text starts where the plan puts it.

Designer's attributes carry a `VdOrigin`: which corner of the text sits at the
location. A symbol leaves its reference designator at `VDALIGN_MR` (8, middle
*right*), so setting a location placed the text's right edge there and the string
ran back its own width -- 17 units for a refdes, against the planner's +12 offset
-- landing on the part's centreline. Every part on every drawn sheet had its
refdes and value printed across its own body.
"""

from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan.schematic_layout import (
    ORIGIN_MIDDLE_LEFT,
    ORIGIN_UPPER_LEFT,
    plan,
)

LEFT_ANCHORED = {ORIGIN_UPPER_LEFT, ORIGIN_MIDDLE_LEFT}


def two_terminal(orientation_vertical: bool) -> dict:
    axis = "ladder" if orientation_vertical else "chain"
    return {
        "sheets": [
            {
                "number": 1,
                "title": "One",
                "blocks": [
                    {
                        "kind": axis,
                        "x": 300,
                        "y": 300,
                        "path": [
                            "power:+3V3",
                            {"refdes": "R1", "symbol": "RES", "value": "10k"},
                            "gnd",
                        ],
                    }
                ],
            }
        ]
    }


def part_attributes(design: dict) -> list[dict]:
    result = plan(design)
    return [
        attribute
        for op in result.ops
        if op.get("op") == "place_part"
        for attribute in (op.get("attributes") or [])
    ]


class AttributeOriginTest(unittest.TestCase):
    def test_every_attribute_is_left_anchored(self):
        for vertical in [False, True]:
            with self.subTest(vertical=vertical):
                attributes = part_attributes(two_terminal(vertical))
                self.assertTrue(attributes, msg="a two-terminal part carries its refdes and value")
                for attribute in attributes:
                    self.assertIn(attribute["origin"], LEFT_ANCHORED, msg=attribute)

    def test_the_refdes_and_the_value_both_carry_an_origin(self):
        for vertical in [False, True]:
            with self.subTest(vertical=vertical):
                names = {a["name"]: a for a in part_attributes(two_terminal(vertical))}
                self.assertEqual(set(names), {"Ref Designator", "Part Number"})
                # Without an origin the adapter leaves the symbol's own, which is right-anchored.
                self.assertEqual(names["Ref Designator"]["origin"], ORIGIN_MIDDLE_LEFT)
                self.assertEqual(names["Part Number"]["origin"], ORIGIN_UPPER_LEFT)

    def test_the_left_anchors_are_the_documented_vdorigin_codes(self):
        # Read from the installed type library: VDALIGN_UL = 1, VDALIGN_ML = 2,
        # VDALIGN_UC = 4, VDALIGN_MR = 8. Only the *L codes start the text at x.
        self.assertEqual((ORIGIN_UPPER_LEFT, ORIGIN_MIDDLE_LEFT), (1, 2))

    def test_an_attribute_keeps_its_planned_position(self):
        # The origin fixes which end of the text the position means; it must not move
        # the position the planner chose.
        attributes = {a["name"]: a for a in part_attributes(two_terminal(False))}
        for attribute in attributes.values():
            self.assertTrue(isinstance(attribute["x"], int))
            self.assertTrue(isinstance(attribute["y"], int))
        self.assertGreater(attributes["Ref Designator"]["y"], attributes["Part Number"]["y"])
