"""pcb: the board from creation to its fabrication package, against a faked Layout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_routing_plan import _model as routing_model

from xpedition_cli.placement import plan_placement


@pytest.fixture
def board(tmp_path) -> Path:
    path = tmp_path / "PCB" / "Board.pcb"
    path.parent.mkdir()
    path.write_text("", encoding="utf-8")
    return path


def _both_runs(cli, adapter, method: str, argv: list[str], answer: dict) -> tuple[dict, dict]:
    """The dry run and the confirmed run of a planned Layout write."""
    adapter.on(method, lambda params: {**answer, "applied": params.get("apply")})
    code, dry = cli(*argv, "--dry-run")
    assert code == 0, dry
    assert adapter.last(method)["apply"] is False
    code, done = cli(*argv, "--confirm", dry["data"]["confirm_token"])
    assert code == 0, done
    assert adapter.last(method)["apply"] is True
    return dry["data"], done["data"]


# -- the board -----------------------------------------------------------------------


def test_create_reads_the_prj_and_runs_jobwizard(cli, adapter, project) -> None:
    adapter.on("pcb_create", {"ok": True, "pcb": "PCB/Board.pcb"})
    code, payload = cli("pcb", "create", "--project", str(project), "--dry-run")
    preview = payload["data"]["preview"]
    assert code == 0 and preview["design"] == "Board1" and preview["pcb"] == "PCB\\Board.pcb"
    assert preview["template"] == "4 Layer Template" and preview["dangerous"] is False
    assert adapter.calls == []
    code, payload = cli(
        "pcb", "create", "--project", str(project), "--confirm", payload["data"]["confirm_token"]
    )
    assert code == 0 and adapter.last("pcb_create")["design"] == "Board1"


def test_create_over_an_existing_board_needs_replace_and_dangerous(cli, adapter, project) -> None:
    text = project.read_text(encoding="utf-8").replace(
        'KEY PCBDesignPath ""', 'KEY PCBDesignPath "PCB\\Old.pcb"'
    )
    project.write_text(text, encoding="utf-8")
    code, payload = cli("pcb", "create", "--project", str(project), "--dry-run")
    assert code == 6 and payload["error"]["details"]["hint"] == "pass --replace"
    adapter.on("pcb_create", {"ok": True})
    code, payload = cli("pcb", "create", "--project", str(project), "--replace", "--dry-run")
    assert payload["data"]["preview"]["dangerous"] is True
    token = payload["data"]["confirm_token"]
    code, payload = cli("pcb", "create", "--project", str(project), "--replace", "--confirm", token)
    assert code == 5 and "--dangerous" in payload["error"]["message"]
    code, payload = cli(
        "pcb", "create", "--project", str(project), "--replace", "--confirm", token, "--dangerous"
    )
    assert code == 0 and adapter.last("pcb_create")["replace"] is True


def test_create_refuses_a_bad_name_or_a_project_without_a_board_design(
    cli, adapter, project, tmp_path
) -> None:
    code, payload = cli(
        "pcb", "create", "--project", str(project), "--name", "my board", "--dry-run"
    )
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"
    plain = tmp_path / "Plain.prj"
    plain.write_text(
        'SECTION iCDB\nLIST Designs\nVALUE "S1"\nENDLIST\nENDSECTION\n', encoding="utf-8"
    )
    code, payload = cli("pcb", "create", "--project", str(plain), "--dry-run")
    assert code == 3 and payload["error"]["details"]["designs"] == ["S1"]


def test_annotate_needs_a_board_and_dangerous_to_unroute(cli, adapter, project, board) -> None:
    code, payload = cli("pcb", "annotate", "--project", str(project), "--dry-run")
    assert code == 3 and payload["error"]["details"]["hint"] == "run pcb create first"
    adapter.on("forward_annotate", {"outcome": "complete", "saved": True})
    code, payload = cli("pcb", "annotate", "--project", str(board), "--unroute", "--dry-run")
    assert code == 0 and payload["data"]["preview"]["dangerous"] is True
    token = payload["data"]["confirm_token"]
    code, _ = cli("pcb", "annotate", "--project", str(board), "--unroute", "--confirm", token)
    assert code == 5
    code, _ = cli(
        "pcb", "annotate", "--project", str(board), "--unroute", "--confirm", token, "--dangerous"
    )
    assert code == 0 and adapter.last("forward_annotate") == {
        "project": str(board),
        "start": True,
        "unroute": True,
    }


def test_outline_holes_rules_and_pour_plan_against_the_board(cli, adapter, board) -> None:
    dry, done = _both_runs(
        cli,
        adapter,
        "board_outline",
        [
            "pcb",
            "outline",
            "--project",
            str(board),
            "--width",
            "60",
            "--height",
            "45",
            "--radius",
            "3",
        ],
        {"pcb": "Board.pcb", "before": {"width": 50}},
    )
    assert dry["preview"]["before"] == {"width": 50} and done["applied"] is True
    assert adapter.last("board_outline")["radius"] == 3.0
    _both_runs(
        cli,
        adapter,
        "mounting_holes",
        ["pcb", "holes", "--project", str(board), "--diameter", "3.2"],
        {"pcb": "Board.pcb", "planned": [[4, 4]], "existing": []},
    )
    dry, _ = _both_runs(
        cli,
        adapter,
        "net_rules",
        [
            "pcb",
            "rules",
            "--project",
            str(board),
            "--class",
            "POWER",
            "--nets",
            "VBAT,+3V3",
            "--width",
            "0.5",
        ],
        {"pcb": "Board.pcb", "classes": ["(Default)"], "widths": {"1": 0.5}, "missing_nets": []},
    )
    assert dry["preview"]["nets"] == ["VBAT", "+3V3"] and dry["preview"]["classes"] == ["(Default)"]
    _both_runs(
        cli,
        adapter,
        "plane_pour",
        ["pcb", "pour", "--project", str(board), "--net", "GND", "--layer", "2"],
        {"pcb": "Board.pcb", "layers": 4, "existing": []},
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["pcb", "outline", "--width", "0", "--height", "10"],
        ["pcb", "outline", "--width", "10", "--height", "10", "--radius", "5"],
        ["pcb", "holes", "--diameter", "-1"],
        ["pcb", "rules", "--class", "P", "--width", "0"],
        ["pcb", "rules", "--class", "P", "--width", "0.3", "--min", "0"],
        ["pcb", "pour", "--layer", "0"],
        ["pcb", "labels", "--gap", "-0.1"],
        ["pcb", "render", "--output", "b.png", "--side", "left"],
        ["pcb", "render", "--output", "b.png", "--scale", "500"],
        ["pcb", "render", "--output", "b.jpg"],
        ["pcb", "export", "--formats", "odb,step"],
        ["pcb", "unroute", "--nets", ","],
    ],
)
def test_board_writes_validate_before_layout_is_asked(cli, adapter, board, argv) -> None:
    code, payload = cli(
        *argv[:2],
        "--project",
        str(board),
        *argv[2:],
        *(["--dry-run"] if argv[1] not in {"render"} else []),
    )
    assert code == 2 and payload["ok"] is False
    assert adapter.calls == []


def test_a_board_command_needs_an_existing_prj_or_pcb(cli, adapter, tmp_path) -> None:
    code, payload = cli("pcb", "check", "--project", str(tmp_path / "No.pcb"))
    assert code == 3
    other = tmp_path / "board.json"
    other.write_text("{}", encoding="utf-8")
    code, payload = cli("pcb", "check", "--project", str(other))
    assert code == 2 and adapter.calls == []


def test_a_write_run_without_the_gate_is_refused(cli, adapter, board) -> None:
    code, payload = cli(
        "pcb", "outline", "--project", str(board), "--width", "10", "--height", "10"
    )
    assert code == 5 and payload["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    code, payload = cli("pcb", "labels", "--project", str(board), "--dry-run", "--confirm", "t")
    assert code == 2 and adapter.calls == []


def test_a_token_is_single_use_and_bound_to_its_arguments(cli, adapter, board) -> None:
    adapter.on("board_outline", {"pcb": "Board.pcb"})
    base = ["pcb", "outline", "--project", str(board), "--height", "45"]
    _, payload = cli(*base, "--width", "60", "--dry-run")
    token = payload["data"]["confirm_token"]
    code, payload = cli(*base, "--width", "61", "--confirm", token)
    assert code == 6
    code, _ = cli(*base, "--width", "60", "--confirm", token)
    assert code == 0
    code, payload = cli(*base, "--width", "60", "--confirm", token)
    assert code == 6 and payload["error"]["code"] == "E_CONFLICT"


# -- placement ---------------------------------------------------------------------------


def test_arrange_is_dangerous_only_when_the_board_is_routed(cli, adapter, board) -> None:
    routing = {"traces": 0, "vias": 0}
    adapter.on(
        "arrange_components",
        lambda params: {
            "pcb": "Board.pcb",
            "plan": [{"refdes": "R1"}],
            "digest": "d1",
            "routing": routing,
        },
    )
    code, payload = cli("pcb", "arrange", "--project", str(board), "--dry-run")
    assert code == 0 and payload["data"]["preview"]["dangerous"] is False
    code, _ = cli(
        "pcb", "arrange", "--project", str(board), "--confirm", payload["data"]["confirm_token"]
    )
    assert code == 0 and adapter.last("arrange_components")["apply"] is True
    routing["traces"] = 12
    code, payload = cli("pcb", "arrange", "--project", str(board), "--dry-run")
    assert payload["data"]["preview"]["dangerous"] is True
    token = payload["data"]["confirm_token"]
    code, _ = cli("pcb", "arrange", "--project", str(board), "--confirm", token)
    assert code == 5
    code, _ = cli("pcb", "arrange", "--project", str(board), "--confirm", token, "--dangerous")
    assert code == 0


def test_arrange_takes_zone_labels_from_the_design(cli, adapter, board, tmp_path) -> None:
    design = tmp_path / "design.json"
    design.write_text(
        json.dumps({"sheets": [{"number": 2, "zone": "POWER"}, {"number": 3}]}), encoding="utf-8"
    )
    adapter.on("arrange_components", {"pcb": "Board.pcb", "plan": [], "digest": "d", "routing": {}})
    cli("pcb", "arrange", "--project", str(board), "--design", str(design), "--dry-run")
    assert adapter.last("arrange_components")["zones"] == {"2": "POWER"}


def test_move_one_part(cli, adapter, board) -> None:
    dry, _ = _both_runs(
        cli,
        adapter,
        "move_component",
        [
            "pcb",
            "move",
            "--project",
            str(board),
            "--refdes",
            "U1",
            "--to",
            "34,35.5",
            "--rotate",
            "90",
        ],
        {"pcb": "Board.pcb", "before": {"x": 1, "y": 2}, "target": {"x": 34, "y": 35.5}},
    )
    assert dry["preview"]["before"] == {"x": 1, "y": 2}
    sent = adapter.last("move_component")
    assert (sent["refdes"], sent["x"], sent["y"], sent["rotation"]) == ("U1", 34.0, 35.5, 90.0)


def test_move_needs_a_part_and_a_place_or_a_task(cli, adapter, board) -> None:
    code, payload = cli("pcb", "move", "--project", str(board), "--refdes", "U1", "--dry-run")
    assert code == 2 and "--file" in payload["error"]["message"]
    code, payload = cli(
        "pcb", "move", "--project", str(board), "--refdes", "U1", "--to", "north", "--dry-run"
    )
    assert code == 2 and adapter.calls == []


def _task(tmp_path: Path) -> Path:
    path = tmp_path / "task.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "unit": "mm",
                "selection": ["R1", "R2"],
                "steps": [{"op": "translate", "dx": 1.0, "dy": 0.0}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _observed() -> list[dict]:
    return [
        {
            "refdes": name,
            "object_id": name,
            "x": x,
            "y": 5.0,
            "rotation": 0.0,
            "side": "top",
            "placed": True,
            "unit": "mm",
            "anchor": 0,
            "fix_lock": 0,
        }
        for name, x in (("R1", 1.0), ("R2", 3.0))
    ]


def _placement(params: dict) -> dict:
    planned = plan_placement(params["request"], _observed())
    result = {**planned, "pcb": "Board.pcb"}
    if params.get("apply"):
        rows = [
            {"id": row["id"], "ok": True, "observed": row["target"]} for row in planned["results"]
        ]
        result = {
            **result,
            "results": rows,
            "outcome": "complete",
            "verification": {"valid": True},
            "write_attempted": True,
            "drc_restored": True,
            "saved": True,
        }
    return result


def test_move_a_selection_by_a_task_file(cli, adapter, board, tmp_path) -> None:
    task = _task(tmp_path)
    adapter.on("placement_batch", _placement)
    code, payload = cli("pcb", "move", "--project", str(board), "--file", str(task), "--dry-run")
    assert code == 0 and payload["data"]["preview"]["summary"]["changed_count"] == 2
    token = payload["data"]["confirm_token"]
    code, payload = cli(
        "pcb", "move", "--project", str(board), "--file", str(task), "--confirm", token
    )
    assert code == 0 and payload["data"]["outcome"] == "complete"


def test_a_task_the_adapter_did_not_complete_is_reported(cli, adapter, board, tmp_path) -> None:
    task = _task(tmp_path)

    def half(params):
        result = _placement(params)
        if params.get("apply"):
            result["outcome"] = "partial"
        return result

    adapter.on("placement_batch", half)
    _, payload = cli("pcb", "move", "--project", str(board), "--file", str(task), "--dry-run")
    code, payload = cli(
        "pcb",
        "move",
        "--project",
        str(board),
        "--file",
        str(task),
        "--confirm",
        payload["data"]["confirm_token"],
    )
    assert code == 2 and payload["error"]["code"] == "E_PROJECT_INVALID"
    assert payload["error"]["details"]["stage"] == "execution"


def test_an_inconsistent_placement_preview_is_not_signed(cli, adapter, board, tmp_path) -> None:
    task = _task(tmp_path)
    adapter.on("placement_batch", lambda params: {**_placement(params), "state_digest": "forged"})
    code, payload = cli("pcb", "move", "--project", str(board), "--file", str(task), "--dry-run")
    assert code == 7 and payload["error"]["code"] == "E_SERVER"


def test_labels(cli, adapter, board) -> None:
    dry, _ = _both_runs(
        cli,
        adapter,
        "tidy_labels",
        ["pcb", "labels", "--project", str(board), "--gap", "0.3"],
        {"pcb": "Board.pcb", "moved": 3, "labels": []},
    )
    assert dry["preview"]["moved"] == 3 and adapter.last("tidy_labels")["gap"] == 0.3


# -- routing -----------------------------------------------------------------------------


def test_route_and_its_unroute_gate(cli, adapter, board) -> None:
    adapter.on("route_board", {"pcb": "Board.pcb", "passes": ["route"], "before": {"routed": 0.4}})
    code, payload = cli("pcb", "route", "--project", str(board), "--dry-run")
    assert code == 0 and payload["data"]["preview"]["dangerous"] is False
    assert adapter.last("route_board")["passes"] == "route:1-5,viamin:1-3,smooth:1-3"
    code, _ = cli(
        "pcb", "route", "--project", str(board), "--confirm", payload["data"]["confirm_token"]
    )
    assert code == 0
    _, payload = cli("pcb", "route", "--project", str(board), "--unroute", "--dry-run")
    token = payload["data"]["confirm_token"]
    assert cli("pcb", "route", "--project", str(board), "--unroute", "--confirm", token)[0] == 5
    assert (
        cli(
            "pcb", "route", "--project", str(board), "--unroute", "--confirm", token, "--dangerous"
        )[0]
        == 0
    )


def test_trace_and_via(cli, adapter, board) -> None:
    adapter.on(
        "hand_route",
        lambda params: {"pcb": "Board.pcb", "items": params["items"], "applied": params["apply"]},
    )
    code, payload = cli(
        "pcb",
        "trace",
        "--project",
        str(board),
        "--net",
        "I2C_SCL",
        "--layer",
        "1",
        "--width",
        "0.2",
        "--points",
        "1,1 5,1",
        "--dry-run",
    )
    assert code == 0 and payload["data"]["preview"]["changes"][0]["traces"] == 1
    item = adapter.last("hand_route")["items"][0]
    assert item["kind"] == "trace" and item["net"] == "I2C_SCL"
    code, payload = cli(
        "pcb", "via", "--project", str(board), "--net", "GND", "--at", "3,3", "--dry-run"
    )
    assert code == 0 and payload["data"]["preview"]["changes"][0]["vias"] == 1
    code, payload = cli("pcb", "trace", "--project", str(board), "--points", "1,1 5,1", "--dry-run")
    assert code == 2 and "--net" in payload["error"]["message"]


def test_a_trace_plan_is_checked_against_the_geometry_first(cli, adapter, board, tmp_path) -> None:
    geometry = tmp_path / "board.json"
    geometry.write_text(json.dumps(routing_model()), encoding="utf-8")
    adapter.on("hand_route", lambda params: {"pcb": "Board.pcb", "items": params["items"]})
    # straight through J1's pad on net B: a clearance problem
    argv = [
        "pcb",
        "trace",
        "--project",
        str(board),
        "--net",
        "A",
        "--points",
        "10,10 30,20",
        "--geometry",
        str(geometry),
    ]
    code, payload = cli(*argv, "--dry-run")
    assert code == 2 and payload["error"]["details"]["count"] >= 1
    assert "hand_route" not in adapter.methods()
    code, payload = cli(*argv, "--dry-run", "--dangerous")
    assert code == 0 and payload["data"]["preview"]["problems"]


def test_stitch_plans_from_the_geometry_file(cli, adapter, tmp_path) -> None:
    geometry = tmp_path / "board.json"
    geometry.write_text(json.dumps(routing_model()), encoding="utf-8")
    plan = tmp_path / "stitch.json"
    code, payload = cli(
        "pcb", "stitch", "--geometry", str(geometry), "--net", "GND", "--output", str(plan)
    )
    assert code == 0 and payload["data"]["vias"] >= 1 and adapter.calls == []
    assert json.loads(plan.read_text(encoding="utf-8"))["items"] == payload["data"]["items"]
    code, payload = cli("pcb", "stitch", "--geometry", str(geometry), "--output", str(plan))
    assert code == 6
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    code, payload = cli("pcb", "stitch", "--geometry", str(bad))
    assert code == 2


def test_unroute_is_always_dangerous(cli, adapter, board) -> None:
    adapter.on(
        "unroute_nets", {"pcb": "Board.pcb", "targets": ["A", "B"], "to_delete": {"traces": 4}}
    )
    code, payload = cli("pcb", "unroute", "--project", str(board), "--nets", "A,B", "--dry-run")
    assert code == 0 and payload["data"]["preview"]["total"] == 2
    token = payload["data"]["confirm_token"]
    assert (
        cli("pcb", "unroute", "--project", str(board), "--nets", "A,B", "--confirm", token)[0] == 5
    )
    assert (
        cli(
            "pcb",
            "unroute",
            "--project",
            str(board),
            "--nets",
            "A,B",
            "--confirm",
            token,
            "--dangerous",
        )[0]
        == 0
    )
    assert adapter.last("unroute_nets")["nets"] == ["A", "B"]
    code, payload = cli("pcb", "unroute", "--project", str(board), "--dry-run")
    assert code == 2 and "--nets" in payload["error"]["message"]
    code, _ = cli(
        "pcb",
        "unroute",
        "--project",
        str(board),
        "--all",
        "--continue-on-error",
        "false",
        "--dry-run",
    )
    assert code == 0 and adapter.last("unroute_nets")["continue_on_error"] is False


# -- inspection ------------------------------------------------------------------------


def test_info_counts_the_board(cli, adapter, board) -> None:
    code, payload = cli("pcb", "info", "--project", str(board))
    data = payload["data"]
    assert code == 0 and data["component_count"] == 3 and data["placed_count"] == 2
    assert data["layer_count"] == 4 and data["track_count"] == 1
    assert adapter.last("snapshot")["domain"] == "pcb"


def test_geometry_render_show_and_check_reach_layout(cli, adapter, board, tmp_path) -> None:
    adapter.on("board_geometry", {"pcb": "Board.pcb", "counts": {"pads": 10}})
    adapter.on("render_board", {"pcb": "Board.pcb", "picture": "b.png"})
    adapter.on("show_board", {"pcb": "Board.pcb", "foreground": True})
    adapter.on("batch_drc", {"pcb": "Board.pcb", "ran": True, "count": 0, "clean": True})
    assert (
        cli("pcb", "geometry", "--project", str(board), "--output", str(tmp_path / "g.json"))[0]
        == 0
    )
    assert adapter.last("board_geometry")["output"] == str(tmp_path / "g.json")
    assert (
        cli(
            "pcb",
            "render",
            "--project",
            str(board),
            "--output",
            str(tmp_path / "b.png"),
            "--side",
            "bottom",
        )[0]
        == 0
    )
    assert adapter.last("render_board")["side"] == "bottom"
    assert cli("pcb", "show", "--project", str(board), "--top-view")[0] == 0
    assert adapter.last("show_board")["top_view"] is True
    code, payload = cli("pcb", "check", "--project", str(board), "--no-run")
    assert code == 0 and payload["data"]["clean"] is True
    assert adapter.last("batch_drc") == {
        "project": str(board),
        "start": True,
        "run": False,
        "online": False,
    }
    code, payload = cli(
        "pcb", "geometry", "--project", str(board), "--output", str(tmp_path / "g.txt")
    )
    assert code == 2


# -- fabrication ---------------------------------------------------------------------------


def test_export_plans_the_package_then_writes_it(cli, adapter, board, tmp_path) -> None:
    dry, done = _both_runs(
        cli,
        adapter,
        "manufacturing_output",
        [
            "pcb",
            "export",
            "--project",
            str(board),
            "--formats",
            "gerber,ncdrill",
            "--output",
            str(tmp_path / "fab"),
        ],
        {
            "pcb": "Board.pcb",
            "formats": ["gerber", "ncdrill"],
            "package": str(tmp_path / "fab"),
            "setup_changes": {},
        },
    )
    assert dry["preview"]["formats"] == ["gerber", "ncdrill"]
    assert adapter.last("manufacturing_output")["formats"] == "gerber,ncdrill"
    assert done["applied"] is True
