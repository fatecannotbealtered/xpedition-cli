from __future__ import annotations

from xpedition_cli import routing_plan as R


def _model() -> dict:
    return {
        "layers": 4,
        "outline": [{"path": [[0, 0, 0], [40, 0, 0], [40, 30, 0], [0, 30, 0]]}],
        "pads": [
            {
                "path": [[9.5, 9.5, 0], [10.5, 9.5, 0], [10.5, 10.5, 0], [9.5, 10.5, 0]],
                "layer": 1,
                "net": "A",
                "refdes": "R1",
            },
            {
                "path": [[14.5, 9.5, 0], [15.5, 9.5, 0], [15.5, 10.5, 0], [14.5, 10.5, 0]],
                "layer": 1,
                "net": "GND",
                "refdes": "R1",
            },
            {"circle": [30.0, 20.0, 0.9], "layer": 1, "net": "B", "refdes": "J1"},
            {"circle": [30.0, 20.0, 0.9], "layer": 4, "net": "B", "refdes": "J1"},
        ],
        "traces": [{"path": [[20, 5, 0], [20, 25, 0]], "layer": 1, "width": 0.254, "net": "B"}],
        "vias": [{"circle": [25.0, 25.0, 0.33], "net": "B"}],
        "components": [
            {
                "refdes": "R1",
                "pins": [
                    {"pin": "1", "net": "A", "x": 10.0, "y": 10.0},
                    {"pin": "2", "net": "GND", "x": 15.0, "y": 10.0},
                ],
            },
            {"refdes": "J1", "pins": [{"pin": "1", "net": "B", "x": 30.0, "y": 20.0}]},
        ],
    }


def test_check_plan_flags_angles_and_clearances() -> None:
    board = R.Board(_model())
    assert board.pin("R1.1") == (10.0, 10.0)
    assert "J1" in board.through_hole and "R1" not in board.through_hole
    clean = [
        {
            "kind": "trace",
            "net": "A",
            "layer": 1,
            "width": 0.254,
            "points": [(10, 10), (10, 14), (12, 16)],
        }
    ]
    assert R.check_plan(clean, board) == []
    bad_angle = [{"kind": "trace", "net": "A", "layer": 1, "points": [(10, 10), (12, 11)]}]
    assert any("not 0/45/90" in p for p in R.check_plan(bad_angle, board))
    through_pad = [{"kind": "trace", "net": "A", "layer": 1, "points": [(10, 10), (16, 10)]}]
    assert any("pad R1 (GND)" in p for p in R.check_plan(through_pad, board))
    # the through-hole pad blocks every layer, an SMD pad only its own
    over_tht = [{"kind": "trace", "net": "A", "layer": 4, "points": [(28, 20), (32, 20)]}]
    assert any("pad J1" in p for p in R.check_plan(over_tht, board))
    under_smd = [{"kind": "trace", "net": "A", "layer": 4, "points": [(14, 10), (16, 10)]}]
    assert R.check_plan(under_smd, board) == []
    # existing copper of another net counts; the same net does not
    crossing = [{"kind": "trace", "net": "A", "layer": 1, "points": [(18, 15), (22, 15)]}]
    assert any("traces" in p for p in R.check_plan(crossing, board))
    same_net = [{"kind": "trace", "net": "B", "layer": 1, "points": [(18, 15), (22, 15)]}]
    assert R.check_plan(same_net, board) == []
    near_via = [{"kind": "via", "net": "A", "at": (25.5, 25.0)}]
    assert any("vias" in p for p in R.check_plan(near_via, board))
    # a via pad touching any pad, its own net included
    own_pad = [{"kind": "via", "net": "A", "at": (10.9, 10.0)}]
    assert any("via A" in p for p in R.check_plan(own_pad, board))


def test_stitch_vias_finds_room_beside_ground_pads() -> None:
    board = R.Board(_model())
    items, left = R.stitch_vias(board, "GND")
    assert left == []
    vias = [item for item in items if item["kind"] == "via"]
    stubs = [item for item in items if item["kind"] == "trace"]
    assert len(vias) == 1 and len(stubs) == 1
    assert stubs[0]["points"][0] == (15.0, 10.0) and stubs[0]["points"][1] == vias[0]["at"]
    assert R.check_plan(items, board) == []


def test_plan_builder_resolves_pins_and_checks(tmp_path) -> None:
    plan = R.PlanBuilder(_model())
    plan.trace("A", 1, ["R1.1", ("R1.1", 0, 3.0), (13.0, 16.0)])
    plan.via("A", (13.0, 16.0))
    assert plan.check() == []
    items = plan.items()
    assert items[0]["points"][0] == [10.0, 10.0] and items[1]["at"] == [13.0, 16.0]
    out = tmp_path / "plan.json"
    plan.write(str(out))
    assert '"items"' in out.read_text(encoding="utf-8")
