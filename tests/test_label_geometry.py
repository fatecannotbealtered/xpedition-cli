"""Nothing of one net may land on the free end of another net's wire.

Designer finishes a net at a wire's free end. A ground symbol reaches 40 units
past its own end -- four slots at the 10-unit pin pitch -- and a boxed label runs
sideways from a top-edge pin across its neighbours. Either refuses the draw
(6031 "Unable to finish net", 6035 "Net already labeled"), and a box that lands
*exactly* on an end draws without complaint while merging two nets. DS-07 and
DS-08 check symbol extents and spacing; none of this was checked at all, so three
draws failed on plans reported with `issues: []`.
"""

from __future__ import annotations

from xpedition_cli.schematic_layout import plan


def design(pins: dict[str, str], *, boxed: bool = True, symbol: dict | None = None) -> dict:
    return {
        "boxed_labels": boxed,
        "symbols": {"U": symbol or {"kind": "box", "left": [["1", "A"], ["2", "B"], ["3", "C"]]}},
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
                        "x": 600,
                        "y": 600,
                        "pins": pins,
                    }
                ],
            }
        ],
    }


def ds09(result) -> list[dict]:
    return [issue for issue in result.issues if issue["check"] == "DS-09"]


def test_a_ground_symbol_covering_the_next_pins_end_is_reported() -> None:
    # The ground on pin 1 reaches 40 units below its own end, over pin 2's.
    found = ds09(plan(design({"1": "gnd", "2": "label:SIGNAL", "3": "label:OTHER"})))
    assert found, "a ground symbol over another net's wire end must be reported"
    assert any(issue["covers"]["net"] == "SIGNAL" for issue in found)
    assert "ground symbol" in found[0]["object"]


def test_a_wide_label_box_over_neighbouring_ends_is_reported() -> None:
    # A six-character net is 40 units of box over a 10-unit pin pitch.
    top = {"kind": "box", "top": [["1", "A"], ["2", "B"], ["3", "C"], ["4", "D"]]}
    found = ds09(
        plan(
            design(
                {"1": "label:NETXYZ", "2": "label:SECOND", "3": "label:THIRD", "4": "label:FOURTH"},
                symbol=top,
            )
        )
    )
    assert found, "a label box reaching over a neighbour's wire end must be reported"
    assert any("label box" in issue["object"] for issue in found)


def test_a_box_that_only_touches_an_end_is_still_reported() -> None:
    # The dangerous one: it draws without an error and can merge two nets.
    top = {"kind": "box", "top": [["1", "A"], ["2", "B"]]}
    found = ds09(plan(design({"1": "label:ABCDE", "2": "label:OTHER"}, symbol=top)))
    assert found, "touching an end is the silent failure, not a safe margin"


def test_a_clean_design_reports_nothing() -> None:
    # Labels on left pins run away from the body, clear of their neighbours.
    result = plan(design({"1": "label:A_ONE", "2": "label:B_TWO", "3": "label:C_THREE"}))
    assert ds09(result) == []


def test_a_label_over_its_own_nets_end_is_not_an_issue() -> None:
    # Every label sits on the end of the wire it names; only another net matters.
    result = plan(design({"1": "label:SIG", "2": "nc", "3": "nc"}))
    assert ds09(result) == []


def test_unboxed_labels_do_not_raise_box_issues() -> None:
    top = {"kind": "box", "top": [["1", "A"], ["2", "B"]]}
    result = plan(design({"1": "label:ABCDEF", "2": "label:OTHER"}, boxed=False, symbol=top))
    assert not [issue for issue in ds09(result) if "label box" in issue["object"]]
