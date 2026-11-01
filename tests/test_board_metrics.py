"""`pcb metrics`: numbers for how good a placement and its routing are, from a geometry file."""

from __future__ import annotations

import json
import math
from pathlib import Path

import handler_harness as cli
import pytest

from xpedition_cli import board_metrics as M


def _pin(name: str, x: float, y: float, net: str) -> dict:
    return {"pin": name, "x": x, "y": y, "net": net, "layer": 1}


def _part(refdes: str, extents: list[float], *pins: dict, placed: bool = True) -> dict:
    return {
        "refdes": refdes,
        "cell": "CELL",
        "placed": placed,
        "extents": extents,
        "pins": list(pins),
    }


def _board() -> dict:
    """A 40 x 30 mm board with one of each thing the metrics look for."""
    return {
        "outline": [{"path": [[0, 0, 0], [40, 0, 0], [40, 30, 0], [0, 30, 0], [0, 0, 0]]}],
        "pads": [],
        "vias": [{"circle": [5, 5, 0.3], "net": "GND"}, {"circle": [6, 5, 0.3], "net": "GND"}],
        "traces": [
            # a 45-degree corner that folds back on itself (acute), then a clean 90-degree one
            {"net": "NETA", "layer": 1, "width": 0.25, "path": [[20, 2], [24, 2], [22, 4]]},
            {"net": "NETB", "layer": 2, "width": 0.25, "path": [[20, 8], [20, 12], [24, 12]]},
        ],
        "components": [
            _part(
                "U1",
                [10, 10, 16, 16],
                _pin("VDD", 10.5, 15.5, "+3V3"),
                _pin("GND", 15.5, 10.5, "GND"),
            ),
            # decoupling: C1 beside U1's supply pin, C2 across the board
            _part("C1", [8, 15, 9, 16], _pin("1", 8.2, 15.5, "+3V3"), _pin("2", 8.8, 15.5, "GND")),
            _part(
                "C2", [35, 25, 36, 26], _pin("1", 35.2, 25.5, "+3V3"), _pin("2", 35.8, 25.5, "GND")
            ),
            _part("R1", [15, 12, 17, 13]),  # overlaps U1 by 1 x 1 mm
            _part("R2", [38, 5, 42, 6]),  # 2 mm past the right edge
            _part("R3", [], placed=False),
            _part("J1", [1, 5, 4, 9]),  # 1 mm from the left edge
            _part("J2", [18, 18, 21, 21]),  # 9 mm from the top edge
            # two signal nets whose airwires cross once
            _part("T1", [19.5, 1.5, 20.5, 2.5], _pin("1", 20, 2, "NETA")),
            _part("T2", [27.5, 7.5, 28.5, 8.5], _pin("1", 28, 8, "NETA")),
            _part("T3", [19.5, 7.5, 20.5, 8.5], _pin("1", 20, 8, "NETB")),
            _part("T4", [27.5, 1.5, 28.5, 2.5], _pin("1", 28, 2, "NETB")),
        ],
    }


def test_the_metrics_of_a_board_state() -> None:
    result = M.measure(_board())
    summary = result["summary"]
    assert result["board"] == {"width_mm": 40.0, "height_mm": 30.0}
    # airwires: NETA and NETB 10 mm each and crossing once; the rails' trees on top
    assert summary["signal_ratsnest_mm"] == 20.0 and summary["crossings"] == 1
    supply = math.dist((8.2, 15.5), (10.5, 15.5)) + math.dist((10.5, 15.5), (35.2, 25.5))
    ground = math.dist((15.5, 10.5), (8.8, 15.5)) + math.dist((15.5, 10.5), (35.8, 25.5))
    assert summary["ratsnest_mm"] == pytest.approx(20 + supply + ground, abs=0.01)
    assert result["ratsnest"]["supply_nets"] == ["+3V3", "GND"]
    assert result["overlaps"] == [{"a": "R1", "b": "U1", "mm2": 1.0}]
    assert result["outside"] == [{"refdes": "R2", "beyond_mm": 2.0}]
    assert result["parts"]["unplaced"] == ["R3"] and summary["unplaced"] == 1
    # the far capacitor first: 26.6 mm from U1.VDD is not decoupling it
    assert [(r["capacitor"], r["nearest"]) for r in result["decoupling"]] == [
        ("C2", "U1.VDD"),
        ("C1", "U1.VDD"),
    ]
    assert result["decoupling"][1]["mm"] == 2.3
    assert summary["decoupling_max_mm"] == pytest.approx(
        math.dist((35.2, 25.5), (10.5, 15.5)), abs=0.001
    )
    assert result["edge_parts"] == [
        {"refdes": "J2", "edge_mm": 9.0},
        {"refdes": "J1", "edge_mm": 1.0},
    ]
    routing = result["routing"]
    assert routing["vias"] == 2 and routing["by_layer"] == {
        "1": pytest.approx(6.828, abs=0.001),
        "2": 8.0,
    }
    assert routing["acute_corners"] == [
        {"net": "NETA", "layer": 1, "at": [24.0, 2.0], "degrees": 45.0}
    ]
    assert summary["acute_corners"] == 1 and summary["overlaps"] == 1 and summary["outside"] == 1


def test_supply_and_ground_nets_are_told_from_signals_by_name() -> None:
    kinds = {
        name: M.net_kind(name)
        for name in ("GND", "AGND", "VSS", "+3V3", "VBUS", "AVDD", "5V", "SDA", "MCU_RST_N")
    }
    assert kinds == {
        "GND": "ground",
        "AGND": "ground",
        "VSS": "ground",
        "+3V3": "supply",
        "VBUS": "supply",
        "AVDD": "supply",
        "5V": "supply",
        "SDA": "signal",
        "MCU_RST_N": "signal",
    }


def test_an_empty_board_measures_without_failing() -> None:
    result = M.measure({"pads": [], "components": []})
    assert result["board"] is None and result["summary"]["ratsnest_mm"] == 0
    assert result["summary"]["decoupling_max_mm"] is None and result["summary"]["density"] is None


# -- the command ------------------------------------------------------------------------


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main([*argv, "--compact"])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def geometry(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "board.json"
    path.write_text(json.dumps(_board()), encoding="utf-8")
    return path


def test_pcb_metrics_measures_a_geometry_file_and_saves_it(capsys, geometry, tmp_path) -> None:
    saved = tmp_path / "before.json"
    code, result = _run(
        capsys, "pcb", "metrics", "--geometry", str(geometry), "--output", str(saved)
    )
    assert code == 0 and result["ok"] is True
    data = result["data"]
    assert data["summary"]["crossings"] == 1 and data["path"] == str(saved.resolve())
    assert "delta" not in data and "ratsnest" in data["_untrusted"]
    assert json.loads(saved.read_text(encoding="utf-8"))["summary"] == data["summary"]
    code, again = _run(
        capsys, "pcb", "metrics", "--geometry", str(geometry), "--output", str(saved)
    )
    assert code == 6 and again["error"]["code"] == "E_CONFLICT"  # never overwritten silently


def test_pcb_metrics_compares_a_move_with_the_baseline(capsys, geometry, tmp_path) -> None:
    saved = tmp_path / "before.json"
    _run(capsys, "pcb", "metrics", "--geometry", str(geometry), "--output", str(saved))
    board = _board()
    # C2 moved next to U1's supply pin
    board["components"][2] = _part(
        "C2", [8, 13, 9, 14], _pin("1", 8.2, 13.5, "+3V3"), _pin("2", 8.8, 13.5, "GND")
    )
    after = tmp_path / "after.json"
    after.write_text(json.dumps(board), encoding="utf-8")
    code, result = _run(
        capsys, "pcb", "metrics", "--geometry", str(after), "--baseline", str(saved)
    )
    assert code == 0
    delta = result["data"]["delta"]
    decoupling = delta["decoupling_max_mm"]
    assert decoupling["before"] > 26 and decoupling["after"] == 3.048
    assert delta["decoupling_max_mm"]["change"] < -20 and delta["ratsnest_mm"]["change"] < 0
    assert delta["crossings"]["change"] == 0
    # the whole envelope of an earlier run is a baseline too
    envelope = tmp_path / "envelope.json"
    envelope.write_text(json.dumps({"ok": True, "data": json.loads(saved.read_text())}), "utf-8")
    code, result = _run(
        capsys, "pcb", "metrics", "--geometry", str(after), "--baseline", str(envelope)
    )
    assert code == 0 and result["data"]["delta"]["crossings"]["before"] == 1


def test_pcb_metrics_refuses_what_it_cannot_measure(capsys, geometry, tmp_path) -> None:
    code, result = _run(capsys, "pcb", "metrics")
    assert code == 2 and result["error"]["code"] == "E_USAGE"
    not_metrics = tmp_path / "not-metrics.json"
    not_metrics.write_text(json.dumps({"items": []}), encoding="utf-8")
    code, result = _run(
        capsys, "pcb", "metrics", "--geometry", str(geometry), "--baseline", str(not_metrics)
    )
    assert code == 2 and result["error"]["code"] == "E_VALIDATION"
    code, result = _run(capsys, "pcb", "metrics", "--geometry", str(tmp_path / "missing.json"))
    assert code == 3 and result["error"]["code"] == "E_NOT_FOUND"


def test_board_rules_list_what_drc_does_not() -> None:
    rules = M.board_rules({**_board(), "unrouted": [{"net": "NETA", "opens": 2}]})
    found = {(rule["rule"], rule.get("refdes") or rule.get("net")) for rule in rules}
    assert ("unplaced", "R3") in found
    assert ("outside", "R2") in found
    assert ("overlap", "R1") in found
    assert ("decoupling", "C2") in found and ("decoupling", "C1") not in found
    assert ("connector_edge", "J2") in found and ("connector_edge", "J1") not in found
    assert ("acute_corner", "NETA") in found
    assert ("unrouted", "NETA") in found
    # the worst first
    severities = [rule["severity"] for rule in rules]
    assert severities == sorted(severities, key=["high", "medium", "low"].index)


def test_a_clean_board_has_no_board_rules() -> None:
    board = {
        "outline": [{"path": [[0, 0, 0], [20, 0, 0], [20, 20, 0], [0, 20, 0], [0, 0, 0]]}],
        "traces": [],
        "vias": [],
        "components": [_part("U1", [5, 5, 8, 8], _pin("1", 6, 6, "SIG"))],
    }
    assert M.board_rules(board) == []


# -- silkscreen texts ---------------------------------------------------------------------


def _label(string: str, box: list[float], refdes: str | None = None, **extra) -> dict:
    """A silkscreen text as `pcb snapshot` gives it: its box is Layout's extents."""
    return {
        "text": string,
        "refdes": refdes,
        "kind": "refdes" if refdes == string else "text",
        "side": "top",
        "height": 0.8,
        "pen": 0.12,
        "extents": box,
        **extra,
    }


def _labelled_board() -> dict:
    """A 40 x 30 mm board with one silkscreen text for each thing that makes one hard
    to read, and texts that are fine."""
    square = [[10.5, 15, 0], [11.5, 15, 0], [11.5, 15.8, 0], [10.5, 15.8, 0], [10.5, 15, 0]]
    return {
        "layers": 2,
        "outline": [{"path": [[0, 0, 0], [40, 0, 0], [40, 30, 0], [0, 30, 0], [0, 0, 0]]}],
        "pads": [{"path": square, "layer": 1, "refdes": "U1", "pin": "1", "net": "SIG"}],
        "vias": [{"circle": [33, 12, 0.3], "net": "GND"}],
        "holes": [[25, 25, 1.0], [33, 12, 0.3]],  # a bare hole and the via's
        "silk": [{"path": [[5, 25, 0], [15, 25, 0]], "width": 0.12, "side": "top", "refdes": "U1"}],
        "components": [
            _part("U1", [10, 10, 16, 16]),
            _part("R1", [20, 10, 22, 11]),
            _part("C1", [30, 20, 31, 21]),
        ],
        "texts": [
            _label("U1", [10.2, 15.5, 12.0, 16.4], "U1"),  # over U1.1
            _label("R1", [11.5, 16.0, 13.0, 16.9], "R1"),  # on U1's designator
            _label("C1", [20.5, 10.2, 21.5, 10.8], "C1"),  # on R1's place
            _label("J1", [38.5, 5, 41, 6], "J1"),  # 1 mm past the right edge
            _label("Q1", [30, 5, 31, 5.5], "Q1", height=0.4),  # too small
            _label("NOTE", [9, 24.8, 11, 25.6]),  # across U1's outline
            _label("K1", [24.8, 24.8, 26, 25.4], "K1"),  # over the bare hole
            _label("V1", [32.8, 11.8, 34, 12.4], "V1"),  # over a via: not reported
            _label("B1", [10.2, 15.5, 12.0, 16.4], "B1", side="bottom"),  # the other side
            _label("R2", [2, 2, 3, 2.8], "R2"),  # clear of everything
        ],
    }


def test_label_rules_find_the_silkscreen_texts_that_will_not_read() -> None:
    rules = M.label_rules(_labelled_board())
    found = {(rule["rule"], rule["text"], rule.get("other")) for rule in rules}
    assert ("label_over_pad", "U1", "U1.1") in found
    assert ("label_overlap", "U1", "R1") in found
    assert ("label_under_part", "C1", "R1") in found
    assert ("label_off_board", "J1", None) in found
    assert ("label_small", "Q1", None) in found
    assert ("label_over_silk", "NOTE", "U1") in found
    assert ("label_over_pad", "K1", "hole at 25.00,25.00") in found
    reported = {rule["text"] for rule in rules} | {rule.get("other") for rule in rules}
    assert not {"V1", "B1", "R2"} & reported
    assert len(rules) == 7
    severity = {rule["rule"]: rule["severity"] for rule in rules}
    assert severity == {
        "label_over_pad": "medium",
        "label_overlap": "medium",
        "label_off_board": "medium",
        "label_under_part": "low",
        "label_over_silk": "low",
        "label_small": "low",
    }
    off = next(rule for rule in rules if rule["rule"] == "label_off_board")
    assert "1.0 mm past" in off["finding"] and off["refdes"] == "J1" and off["at"] == [39.75, 5.5]


def test_the_lowest_text_that_passes_can_be_raised() -> None:
    small = {
        rule["text"] for rule in M.label_rules(_labelled_board()) if rule["rule"] == "label_small"
    }
    assert small == {"Q1"}
    strict = M.board_rules(_labelled_board(), min_text_height=1.0)
    small = {rule["text"] for rule in strict if rule["rule"] == "label_small"}
    assert "R2" in small and "Q1" in small
    lenient = M.label_rules(_labelled_board(), min_text_height=0.3)
    assert not [rule for rule in lenient if rule["rule"] == "label_small"]


def test_texts_without_a_box_are_not_judged() -> None:
    board = _labelled_board()
    for label in board["texts"]:
        label.pop("extents")
    assert M.label_rules(board) == []


def test_silk_rules_name_the_silkscreen_over_pads_once_per_part() -> None:
    square = [[0, 0, 0], [0.6, 0, 0], [0.6, 0.55, 0], [0, 0.55, 0], [0, 0, 0]]

    def pad(pin: str, dx: float, refdes: str = "C1") -> dict:
        return {
            "path": [[x + dx, y, r] for x, y, r in square],
            "layer": 1,
            "refdes": refdes,
            "pin": pin,
        }

    board = {
        "layers": 2,
        "pads": [pad("1", 0), pad("2", 1.0), pad("1", 5.0, "R1")],
        "silk": [
            # C1's box straight through both its pads (the old placeholder outline)
            {"path": [[0.3, -0.2, 0], [0.3, 0.8, 0]], "width": 0.12, "side": "top", "refdes": "C1"},
            {"path": [[1.3, -0.2, 0], [1.3, 0.8, 0]], "width": 0.12, "side": "top", "refdes": "C1"},
            # R1's line clear of its pad, a board line over it, a bottom line ignored
            {"path": [[5, 1, 0], [6, 1, 0]], "width": 0.12, "side": "top", "refdes": "R1"},
            {"path": [[5.3, -1, 0], [5.3, 1, 0]], "width": 0.2, "side": "top"},
            {"path": [[5.3, -1, 0], [5.3, 1, 0]], "width": 0.2, "side": "bottom", "refdes": "R1"},
        ],
    }
    rules = M.silk_rules(board)
    found = {(rule["refdes"], rule["other"]): rule for rule in rules}
    assert set(found) == {("C1", "C1"), (None, "R1")}
    assert found[("C1", "C1")]["pins"] == ["1", "2"]
    assert "its pads 1, 2" in found[("C1", "C1")]["finding"]
    assert "the board's silkscreen runs over R1's pads 1" in found[(None, "R1")]["finding"]
    assert all(rule["severity"] == "low" for rule in rules)
    assert [r["rule"] for r in M.board_rules(board)].count("silk_over_pad") == 2
