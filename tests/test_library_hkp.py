import json
from pathlib import Path

from xpedition_cli import library_hkp as H

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "demo-sensor-board.json"


def _design(blocks, **extra):
    return {"sheet_size": "A4", "sheets": [{"number": 1, "blocks": blocks}], **extra}


def test_demo_design_gets_a_cell_and_a_part_for_every_placed_part():
    design = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    plan, texts = H.library_texts(design)
    assert plan.issues == []
    assert len(plan.mapping) == 30
    cells = {row["cell"] for row in plan.mapping}
    assert {"CLI_0402", "CLI_0603", "CLI_TP", "CLI_HOLE_M2", "CLI_HDR4", "CLI_SOIC8"} <= cells
    assert '.PACKAGE_CELL "CLI_0402"' in texts["cells"]
    assert '.PADSTACK "SMD-RECT0.6X0.55"' in texts["padstacks"]
    assert '.Number "10k 1%"' in texts["parts"]
    numbers = {row["part_number"] for row in plan.mapping} - {None}
    assert numbers == set(plan.parts)
    assert all(row["part_number"] is None for row in plan.mapping if row["symbol"] == "HOLE")


def test_parts_map_symbol_pin_names_to_cell_pin_numbers():
    design = _design(
        [
            {
                "kind": "ic",
                "refdes": "Q1",
                "symbol": "PMOS",
                "value": "AO3401",
                "x": 500,
                "y": 400,
                "pins": {"1": "label:G", "2": "power:VBAT", "3": "label:D"},
            }
        ]
    )
    plan, texts = H.library_texts(design)
    part = plan.parts["AO3401"]
    assert part.pin_names == ["G", "S", "D"]
    assert part.pin_numbers == ["1", "2", "3"]
    assert part.cell == "CLI_SOT23" and part.prefix == "Q" and part.part_type == "MOSFET"
    assert '\t\t\t....PinName\t"G"' in texts["parts"]
    assert '\t\t\t....PinNumber\t"3"' in texts["parts"]
    assert texts["parts"].count('...SwapID\t"P') == 3


def test_package_override_and_unknown_package_are_reported():
    ic = {
        "kind": "box",
        "top": [["1", "VDD"]],
        "left": [["2", "IN"]],
        "right": [["3", "OUT"], ["4", "NC"]],
        "bottom": [["5", "GND"]],
    }
    blocks = [
        {
            "kind": "ic",
            "refdes": "U1",
            "symbol": "AMP",
            "value": "OPA333",
            "x": 500,
            "y": 400,
            "pins": {"1": "power:+3V3", "2": "label:IN", "3": "label:OUT", "4": "nc", "5": "gnd"},
        },
        {
            "kind": "ladder",
            "x": 800,
            "y": 500,
            "path": ["power:+3V3", {"refdes": "R1", "symbol": "RES", "value": "10k"}, "gnd"],
        },
    ]
    plan, _ = H.library_texts(_design(blocks, symbols={"AMP": ic}, packages={"U1": "TSSOP20"}))
    assert plan.parts["OPA333"].cell == "CLI_TSSOP20"
    assert plan.parts["10k"].cell == "CLI_0402"
    plan, _ = H.library_texts(_design(blocks, symbols={"AMP": ic}, packages={"RES": "BGA"}))
    assert any("unknown package" in issue for issue in plan.issues)


def test_padstack_text_declares_pads_holes_and_stacks_in_millimetres():
    plan, texts = H.library_texts(
        _design(
            [
                {
                    "kind": "ladder",
                    "x": 300,
                    "y": 500,
                    "path": [
                        "power:VBAT",
                        {"refdes": "SW1", "symbol": "SW", "value": "LID"},
                        "gnd",
                    ],
                },
                {"kind": "ic", "refdes": "H1", "symbol": "HOLE", "value": "M2", "x": 600, "y": 400},
            ]
        )
    )
    text = texts["padstacks"]
    assert ".UNITS MM" in text
    assert '.HOLE "C1-PLATED"' in text and "..HOLE_OPTIONS PLATED DRILLED" in text
    assert '.HOLE "C2.2-NONPLATED"' in text and "NON_PLATED" in text
    assert "..PADSTACK_TYPE PIN_THROUGH" in text and "..PADSTACK_TYPE MOUNTING_HOLE" in text
    assert plan.cells["CLI_HDR2"].mount == "THROUGH"


def test_dual_row_pads_run_across_the_row_and_never_touch() -> None:
    # Batch DRC on a routed board found neighbouring SOIC pads overlapping (required
    # 0.254 mm, actual 0): the pad's long side had been laid along the pitch.
    import re

    stock = H._Stock(H.LibraryPlan())
    for key, pitch in (("SOIC8", 1.27), ("TSSOP20", 0.8)):
        cell = H.build_package(stock, key, 8)
        left = sorted((pin for pin in cell.pins if pin.x < 0), key=lambda pin: pin.y)
        assert len(left) >= 2
        assert abs(abs(left[1].y - left[0].y) - pitch) < 1e-9
        width, height = map(float, re.search(r"RECT([\d.]+)X([\d.]+)", left[0].padstack).groups())
        assert height < pitch, (key, left[0].padstack)
        assert width > height
