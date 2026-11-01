"""A part's attribute text starts where the plan puts it.

Designer's attributes carry a `VdOrigin`: which corner of the text sits at the
location. A symbol leaves its reference designator at `VDALIGN_MR` (8, middle
*right*), so setting a location placed the text's right edge there and the string
ran back its own width -- 17 units for a refdes, against the planner's +12 offset
-- landing on the part's centreline. Every part on every drawn sheet had its
refdes and value printed across its own body.
"""

from __future__ import annotations

import pytest

from xpedition_cli.schematic_layout import (
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


@pytest.mark.parametrize("vertical", [False, True])
def test_every_attribute_is_left_anchored(vertical) -> None:
    attributes = part_attributes(two_terminal(vertical))
    assert attributes, "a two-terminal part carries its refdes and value"
    for attribute in attributes:
        assert attribute["origin"] in LEFT_ANCHORED, attribute


@pytest.mark.parametrize("vertical", [False, True])
def test_the_refdes_and_the_value_both_carry_an_origin(vertical) -> None:
    names = {a["name"]: a for a in part_attributes(two_terminal(vertical))}
    assert set(names) == {"Ref Designator", "Part Number"}
    # Without an origin the adapter leaves the symbol's own, which is right-anchored.
    assert names["Ref Designator"]["origin"] == ORIGIN_MIDDLE_LEFT
    assert names["Part Number"]["origin"] == ORIGIN_UPPER_LEFT


def test_the_left_anchors_are_the_documented_vdorigin_codes() -> None:
    # Read from the installed type library: VDALIGN_UL = 1, VDALIGN_ML = 2,
    # VDALIGN_UC = 4, VDALIGN_MR = 8. Only the *L codes start the text at x.
    assert (ORIGIN_UPPER_LEFT, ORIGIN_MIDDLE_LEFT) == (1, 2)


def test_an_attribute_keeps_its_planned_position() -> None:
    # The origin fixes which end of the text the position means; it must not move
    # the position the planner chose.
    attributes = {a["name"]: a for a in part_attributes(two_terminal(False))}
    for attribute in attributes.values():
        assert isinstance(attribute["x"], int) and isinstance(attribute["y"], int)
    assert attributes["Ref Designator"]["y"] > attributes["Part Number"]["y"]
