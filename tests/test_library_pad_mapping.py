"""A cell pad with no symbol pin carries no net, and `--dry-run` has to say so.

The pin check ran one way only: every symbol pin had to have a cell pin. A cell
with *more* pads than the symbol has pins passed. A power MOSFET in DFN or
PowerPAK has several pads per electrode and a drain paddle, so its converted
cell had seven pads against a three-pin symbol and the preview came back with
`issues: []` -- a library that looks built and is not.
"""

from __future__ import annotations

from xpedition_cli import library_hkp as H


def mosfet(package: str | None = None) -> dict:
    design = {
        "sheet_size": "A4",
        "sheets": [
            {
                "number": 1,
                "blocks": [
                    {
                        "kind": "ic",
                        "refdes": "Q1",
                        "symbol": "PMOS",
                        "value": "AO3401",
                        "x": 500,
                        "y": 400,
                        "pins": {"1": "label:G", "2": "power:VBAT", "3": "label:D"},
                    }
                ],
            }
        ],
    }
    if package:
        design["packages"] = {"Q1": package}
    return design


def test_a_cell_with_more_pads_than_the_symbol_has_pins_is_reported() -> None:
    plan, _ = H.library_texts(mosfet("SOIC8"))
    unmapped = [issue for issue in plan.issues if "no pin on symbol" in issue]
    assert unmapped, "a pad the symbol cannot reach must not pass as a clean build"
    message = unmapped[0]
    assert "Q1" in message and "PMOS" in message
    # The count is what makes it obvious the package does not fit the part.
    assert "pads against" in message


def test_the_matching_package_reports_nothing() -> None:
    plan, _ = H.library_texts(mosfet())
    assert [issue for issue in plan.issues if "no pin on symbol" in issue] == []


def test_a_symbol_pin_with_no_pad_is_still_reported() -> None:
    # The original direction of the check has to keep working.
    plan, _ = H.library_texts(mosfet("0402"))
    assert [issue for issue in plan.issues if "have no pin in cell" in issue]


def test_a_pinless_mark_is_not_measured_against_its_cell() -> None:
    # A mounting hole is not forwarded to the PCB, so its pads map to nothing by
    # design and must not be reported.
    design = {
        "sheet_size": "A4",
        "sheets": [
            {
                "number": 1,
                "blocks": [
                    {
                        "kind": "ic",
                        "refdes": "H1",
                        "symbol": "HOLE",
                        "value": "M2",
                        "x": 500,
                        "y": 400,
                        "pins": {},
                    }
                ],
            }
        ],
    }
    plan, _ = H.library_texts(design)
    assert [issue for issue in plan.issues if "no pin on symbol" in issue] == []
