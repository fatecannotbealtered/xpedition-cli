import pytest

from xpedition_cli import schematic_layout as L


def _design(**overrides):
    design = {
        "title": "TEST",
        "sheet_size": "B",
        "symbols": {
            "LDO": {
                "kind": "box",
                "left": [["1", "VIN"], ["3", "EN"]],
                "right": [["5", "VOUT"]],
                "bottom": [["2", "GND"]],
                "pintypes": {"1": "POWER", "2": "GROUND", "5": "POWER"},
            }
        },
        "sheets": [
            {
                "number": 2,
                "title": "02 LDO",
                "blocks": [
                    {
                        "kind": "ic",
                        "refdes": "U201",
                        "symbol": "LDO",
                        "value": "XC6206",
                        "x": 600,
                        "y": 600,
                        "pins": {
                            "1": "label:VBAT",
                            "3": "label:VBAT",
                            "5": "label:+3V3",
                            "2": "gnd",
                        },
                    },
                    {
                        "kind": "ladder",
                        "x": 900,
                        "y": 700,
                        "path": [
                            "power:+3V3",
                            {"refdes": "R201", "symbol": "RES", "value": "10k 1%"},
                            "label:FB",
                            {"refdes": "R202", "symbol": "RES", "value": "18k 1%"},
                            "gnd",
                        ],
                    },
                    {
                        "kind": "chain",
                        "x": 1100,
                        "y": 900,
                        "path": [
                            "power:VBAT",
                            {"refdes": "L201", "symbol": "IND", "value": "2.2uH"},
                            "label:SW",
                        ],
                    },
                ],
                "notes": ["Note 2-1: test"],
            }
        ],
    }
    design.update(overrides)
    return design


def test_ladder_nodes_are_on_grid_and_sixty_apart():
    plan = L.plan(_design())
    r201 = next(p for p in plan.parts if p["refdes"] == "R201")
    r202 = next(p for p in plan.parts if p["refdes"] == "R202")
    assert (r201["x"], r201["y"]) == (900, 670)
    assert (r202["x"], r202["y"]) == (900, 610)
    assert r201["orientation"] == 3
    wires = [op for op in plan.ops if op["op"] == "wire"]
    assert all(px % 10 == 0 and py % 10 == 0 for op in wires for px, py in op["points"])


def test_ladder_and_chain_produce_the_expected_netlist():
    plan = L.plan(_design())
    assert plan.nets["+3V3"] == {"U201.5", "R201.1"}
    assert plan.nets["FB"] == {"R201.2", "R202.1"}
    assert plan.nets["GND"] == {"U201.2", "R202.2"}
    assert plan.nets["VBAT"] == {"U201.1", "U201.3", "L201.1"}
    assert plan.nets["SW"] == {"L201.2"}
    assert plan.links == []


def test_none_node_becomes_an_unnamed_link():
    design = _design()
    design["sheets"][0]["blocks"][1]["path"][2] = "none"
    plan = L.plan(design)
    assert plan.links == [("R201.2", "R202.1")]
    assert "FB" not in plan.nets


def test_ic_pins_get_stubs_labels_boxes_and_a_ground_symbol():
    plan = L.plan(_design())
    ops = plan.ops
    labels = [op["label"]["net"] for op in ops if op["op"] == "wire" and op.get("label")]
    assert labels.count("VBAT") == 2 and "+3V3" in labels and "FB" in labels and "SW" in labels
    boxes = [op for op in ops if op["op"] == "box"]
    assert len(boxes) == len(labels)
    grounds = [op for op in ops if op["op"] == "place_symbol" and op["symbol"] == "gnd"]
    assert len(grounds) == 2  # U201 bottom pin and the ladder foot
    powers = [op for op in ops if op["op"] == "place_symbol" and op["symbol"].startswith("PWR_")]
    assert len(powers) == 2


def test_sheet_ops_open_wipe_title_notes_footer_and_save():
    plan = L.plan(_design(status="REVIEW DRAFT", revision="R1"))
    kinds = [op["op"] for op in plan.ops]
    assert kinds[:2] == ["open_sheet", "wipe_sheet"]
    assert plan.ops[0]["number"] == 2
    assert kinds[-1] == "save"
    texts = [op["text"] for op in plan.ops if op["op"] == "text"]
    assert "02 LDO" in texts and "Note 2-1: test" in texts
    assert any(t.startswith("REVIEW DRAFT | TEST | Sheet 2/1 | R1") for t in texts)


def test_symbols_are_named_by_content_and_rendered_once():
    plan = L.plan(_design())
    names = sorted(plan.symbols)
    assert any(n.startswith("RES_") for n in names)
    assert any(n.startswith("LDO_") for n in names)
    assert any(n.startswith("PWR_P3V3_") for n in names)
    assert sum(n.startswith("RES_") for n in names) == 1
    res_name = next(n for n in names if n.startswith("RES_"))
    assert plan.symbols[res_name].startswith("V 53\n")
    placed = {op["symbol"] for op in plan.ops if op["op"] == "place_part"}
    assert placed <= set(names)


def test_untreated_pin_is_rejected():
    design = _design()
    del design["sheets"][0]["blocks"][0]["pins"]["3"]
    with pytest.raises(L.DesignError, match="DS-06"):
        L.plan(design)


def test_extent_and_overlap_checks_report_issues():
    design = _design()
    design["sheets"][0]["blocks"][1]["x"] = 1690
    design["sheets"][0]["blocks"].append(
        {
            "kind": "ladder",
            "x": 600,
            "y": 700,
            "path": ["power:+3V3", {"refdes": "C201", "symbol": "CAP", "value": "1uF"}, "gnd"],
        }
    )
    plan = L.plan(design)
    checks = {issue["check"] for issue in plan.issues}
    assert "DS-07" in checks and "DS-08" in checks


def test_png_encoder_writes_a_valid_header():
    import struct
    import zlib

    from xpedition_cli import win_dialogs

    width, height = 3, 2
    bgra = bytes([255, 0, 0, 0] * width * height)  # pure blue pixels in BGRA
    data = win_dialogs.png_bytes(width, height, bgra)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (width, height)
    idat_start = data.index(b"IDAT") + 4
    idat_len = struct.unpack(">I", data[data.index(b"IDAT") - 4 : data.index(b"IDAT")])[0]
    raw = zlib.decompress(data[idat_start : idat_start + idat_len])
    assert raw == (b"\x00" + bytes([0, 0, 255]) * width) * height


def test_plan_to_params_carries_ops_symbols_and_verification():
    params = L.plan_to_params(_design(), "D:/x/RcSmoke.prj")
    assert params["project"] == "D:/x/RcSmoke.prj"
    assert params["library"] == "PartQuest"
    assert set(params) >= {"ops", "symbols", "verify", "summary"}
    assert params["verify"]["nets"]["FB"] == ["R201.2", "R202.1"]


def test_mosfet_in_an_ic_block_and_thermistor_in_a_ladder():
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
                        "pins": {"1": "label:GATE", "2": "power:VBAT", "3": "label:OUT"},
                    },
                    {
                        "kind": "ladder",
                        "x": 700,
                        "y": 500,
                        "path": [
                            "label:OUT",
                            {"refdes": "RT1", "symbol": "NTC", "value": "10k B3435"},
                            "gnd",
                        ],
                    },
                ],
            }
        ],
    }
    result = L.plan(design)
    assert result.nets["VBAT"] == {"Q1.2"}
    assert result.nets["OUT"] == {"Q1.3", "RT1.1"}
    assert result.nets["GND"] == {"RT1.2"}
    wires = [op for op in result.ops if op["op"] == "wire"]
    gate = next(op for op in wires if op.get("start") == "Q1.1")
    assert gate["points"] == [[480, 400], [460, 400]]
    source = next(op for op in wires if op.get("start") == "Q1.2")
    assert source["points"] == [[500, 420], [500, 440]]
    assert not result.issues
    assert any(name.startswith("PMOS_") for name in result.symbols)
    assert any(name.startswith("NTC_") for name in result.symbols)


def test_two_bottom_ground_pins_share_a_bar_with_the_ground_on_its_free_end():
    design = {
        "sheet_size": "A4",
        "symbols": {
            "SENSOR": {
                "kind": "box",
                "top": [["1", "VDD"]],
                "bottom": [["6", "GND"], ["2", "ADD0"]],
                "pintypes": {"1": "POWER", "6": "GROUND"},
            }
        },
        "sheets": [
            {
                "number": 1,
                "blocks": [
                    {
                        "kind": "ic",
                        "refdes": "U1",
                        "symbol": "SENSOR",
                        "value": "TMP102",
                        "x": 500,
                        "y": 400,
                        "pins": {"1": "power:+3V3", "6": "gnd", "2": "gnd"},
                    }
                ],
            }
        ],
    }
    result = L.plan(design)
    assert result.nets["GND"] == {"U1.6", "U1.2"}
    grounds = [op for op in result.ops if op["op"] == "place_symbol" and op["symbol"] == "gnd"]
    assert len(grounds) == 1
    bar = next(op for op in result.ops if op["op"] == "wire" and op.get("start") is None)
    assert bar["points"][0][1] == bar["points"][1][1]
    assert (grounds[0]["x"], grounds[0]["y"]) == tuple(bar["points"][1])
    assert bar["points"][1][0] == bar["points"][0][0] + 10 + 20


def test_test_points_and_holes_are_ic_blocks_with_one_or_no_pins():
    design = {
        "sheet_size": "A4",
        "sheets": [
            {
                "number": 1,
                "blocks": [
                    {
                        "kind": "ic",
                        "refdes": "TP1",
                        "symbol": "TP",
                        "value": "TP",
                        "x": 300,
                        "y": 400,
                        "pins": {"1": "label:NTC_ADC"},
                    },
                    {
                        "kind": "ic",
                        "refdes": "TP2",
                        "symbol": "TP",
                        "value": "TP",
                        "x": 400,
                        "y": 400,
                        "pins": {"1": "gnd"},
                    },
                    {
                        "kind": "ic",
                        "refdes": "H1",
                        "symbol": "HOLE",
                        "value": "M2",
                        "x": 600,
                        "y": 400,
                    },
                ],
            }
        ],
    }
    result = L.plan(design)
    assert result.nets["NTC_ADC"] == {"TP1.1"} and result.nets["GND"] == {"TP2.1"}
    stub = next(op for op in result.ops if op["op"] == "wire" and op.get("start") == "TP1.1")
    assert stub["points"] == [[300, 380], [300, 360]]
    assert stub["label"]["net"] == "NTC_ADC"
    placed = [op["refdes"] for op in result.ops if op["op"] == "place_part"]
    assert placed == ["TP1", "TP2", "H1"]
    assert not result.issues
