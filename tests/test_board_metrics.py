"""`pcb metrics`: numbers for how good a placement and its routing are, from a geometry file."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from xpedition_cli import board_metrics as M
from xpedition_cli import main as cli


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
