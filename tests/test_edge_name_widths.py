"""DS-10: pin names on a top or bottom edge that cannot be read at the pitch.

Those names are drawn horizontally inside the body at the pin pitch. At
`CHAR_WIDTH` per character over a 10-unit pitch about 1.6 characters fit, so a
four-ground bottom edge rendered as `AGNBPGNBCNE2AD` -- `AGND`, `PGND1`, `PGND2`
and `EPAD` written on top of one another. The connectivity was right and the
drawing was not misleading; only the name row was unreadable, and nothing said
so.
"""

from __future__ import annotations

from xpedition_cli.schematic_layout import plan


def design(sides: dict, pins: dict) -> dict:
    return {
        "sheet_size": "A4",
        "symbols": {"U": {"kind": "box", **sides}},
        "sheets": [
            {
                "number": 1,
                "title": "One",
                "blocks": [
                    {
                        "kind": "ic",
                        "refdes": "U1",
                        "symbol": "U",
                        "value": "",
                        "x": 500,
                        "y": 400,
                        "pins": pins,
                    }
                ],
            }
        ],
    }


def ds10(result) -> list[dict]:
    return [issue for issue in result.issues if issue["check"] == "DS-10"]


def test_a_bottom_edge_of_ground_names_is_reported() -> None:
    result = plan(
        design(
            {"bottom": [["1", "AGND"], ["2", "PGND1"], ["3", "PGND2"], ["4", "EPAD"]]},
            {"1": "gnd", "2": "gnd", "3": "gnd", "4": "gnd"},
        )
    )
    found = ds10(result)
    assert found, "names that overlap into one row must not pass unremarked"
    assert found[0]["side"] == "bottom"
    assert set(found[0]["names"]) == {"AGND", "PGND1", "PGND2", "EPAD"}


def test_the_same_arithmetic_applies_to_the_top_edge() -> None:
    result = plan(
        design(
            {"top": [["1", "VDDA"], ["2", "VDDB"]]},
            {"1": "power:+3V3", "2": "power:+3V3"},
        )
    )
    assert [issue["side"] for issue in ds10(result)] == ["top"]


def test_names_that_fit_the_pitch_are_not_reported() -> None:
    # One character is 6 units against a 10-unit pitch.
    result = plan(
        design({"bottom": [["1", "A"], ["2", "B"]]}, {"1": "gnd", "2": "gnd"}),
    )
    assert ds10(result) == []


def test_a_single_pin_edge_is_not_reported() -> None:
    # Nothing to collide with.
    result = plan(design({"bottom": [["1", "AGND"]]}, {"1": "gnd"}))
    assert ds10(result) == []


def test_left_and_right_edges_are_left_alone() -> None:
    # Their names run along the row, away from the neighbours.
    result = plan(
        design(
            {"left": [["1", "VSENSE"], ["2", "ISENSE"]], "right": [["3", "OUTPUT"]]},
            {"1": "label:A", "2": "label:B", "3": "label:C"},
        )
    )
    assert ds10(result) == []


def test_pins_named_after_their_numbers_are_not_measured() -> None:
    # A passive's pins are named like their numbers and nothing is drawn.
    result = plan(design({"bottom": [["1", "1"], ["2", "2"]]}, {"1": "gnd", "2": "gnd"}))
    assert ds10(result) == []
