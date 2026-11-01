"""A symbol may not display the same pin name twice.

Designer names a net after the pin a wire meets, so two pins of one symbol
sharing a displayed name put their wires on the same auto-named net; the second
explicit label is then a second label on a labelled net and the draw stops with
6035 "Net already labeled", minutes in and partway through. The plan is the place
to catch it, where --dry-run reports it for free.
"""

from __future__ import annotations

import pytest

from xpedition_cli.schematic_layout import DesignError, _symbol_from_spec


def spec(**sides):
    return {"kind": "box", **sides}


def test_a_repeated_pin_name_is_refused_with_the_pins_that_share_it() -> None:
    with pytest.raises(DesignError) as caught:
        _symbol_from_spec("J1", spec(left=[["1", "PROBE_A"], ["3", "PROBE_A"]]))
    message = str(caught.value)
    assert "PROBE_A" in message
    assert "left pin 1" in message and "left pin 3" in message
    assert "6035" in message, "name the Designer error the caller would otherwise hit"


def test_the_repeat_is_caught_across_sides() -> None:
    with pytest.raises(DesignError) as caught:
        _symbol_from_spec("U1", spec(left=[["1", "GND"]], bottom=[["9", "GND"]]))
    assert "left pin 1" in str(caught.value) and "bottom pin 9" in str(caught.value)


def test_distinct_names_are_accepted() -> None:
    # The working shape from the report: distinct pin names, net on the label.
    symbol = _symbol_from_spec("J1", spec(left=[["1", "A_HI"], ["2", "A_NC"], ["3", "A_LO"]]))
    assert [pin.name for pin in symbol.pins] == ["A_HI", "A_NC", "A_LO"]


def test_a_pin_named_after_its_own_net_is_fine_when_it_is_the_only_one() -> None:
    symbol = _symbol_from_spec("U1", spec(left=[["1", "VCC"], ["2", "IN"]]))
    assert [pin.name for pin in symbol.pins] == ["VCC", "IN"]


def test_gap_rows_do_not_count_as_repeats() -> None:
    # An empty (number, name) pair separates pin groups; several are normal.
    symbol = _symbol_from_spec(
        "U1", spec(left=[["1", "A"], ["", ""], ["2", "B"], ["", ""], ["3", "C"]])
    )
    assert [pin.name for pin in symbol.pins] == ["A", "B", "C"]


def test_the_report_lists_every_repeated_name() -> None:
    with pytest.raises(DesignError) as caught:
        _symbol_from_spec(
            "J1",
            spec(
                left=[["1", "GND"], ["2", "GND"]],
                right=[["3", "NC"], ["4", "NC"]],
            ),
        )
    message = str(caught.value)
    assert "GND" in message and "NC" in message
