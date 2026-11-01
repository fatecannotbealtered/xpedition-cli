"""pcb: the board from creation to its fabrication package, against a faked Layout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import Failure
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
    if method == "tidy_labels":
        answer = {**answer, "state_guard": "labels_input_sha256_v1", "state_digest": "a" * 64}
    adapter.on(method, lambda params: {**answer, "applied": params.get("apply")})
    code, dry = cli(*argv, "--dry-run")
    assert code == 0, dry
    assert adapter.last(method)["apply"] is False
    code, done = cli(*argv, "--confirm", dry["data"]["confirm_token"])
    if method == "tidy_labels":
        assert code == 2 and done["error"]["code"] == "E_UNSUPPORTED"
        assert all(p["apply"] is False for p in adapter.params(method))
        return dry["data"], done
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
    code, payload = cli("pcb", "update", "--project", str(project), "--dry-run")
    assert code == 3 and payload["error"]["details"]["hint"] == "run pcb create first"
    adapter.on("forward_annotate", {"outcome": "complete", "saved": True})
    code, payload = cli("pcb", "update", "--project", str(board), "--unroute", "--dry-run")
    assert code == 0 and payload["data"]["preview"]["dangerous"] is True
    token = payload["data"]["confirm_token"]
    code, _ = cli("pcb", "update", "--project", str(board), "--unroute", "--confirm", token)
    assert code == 5
    code, _ = cli(
        "pcb", "update", "--project", str(board), "--unroute", "--confirm", token, "--dangerous"
    )
    assert code == 0 and adapter.last("forward_annotate") == {
        "project": str(board),
        "start": True,
        "unroute": True,
        "verify": True,
    }


def test_outline_holes_rules_and_pour_plan_against_the_board(cli, adapter, board) -> None:
    dry, done = _both_runs(
        cli,
        adapter,
        "board_outline",
        [
            "pcb",
            "outline",
            "set",
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
        ["pcb", "holes", "add-corners", "--project", str(board), "--diameter", "3.2"],
        {"pcb": "Board.pcb", "planned": [[4, 4]], "existing": []},
    )
    rules_args = [
        "pcb",
        "constraints",
        "widths",
        "--project",
        str(board),
        "--class",
        "POWER",
        "--nets",
        "VBAT,+3V3",
        "--width",
        "0.5",
    ]
    calls = len(adapter.calls)
    code, payload = cli(*rules_args, "--dry-run")
    assert code == 0 and payload["data"]["preview"]["can_apply"] is False
    assert payload["data"]["preview"]["nets"] == ["VBAT", "+3V3"]
    assert payload["data"]["preview"]["native_state"] == "not_observed"
    for _ in range(2):
        code, refused = cli(*rules_args, "--confirm", payload["data"]["confirm_token"])
        assert code != 0 and refused["error"]["code"] == "E_UNSUPPORTED"
        assert refused["error"]["details"]["write_attempted"] is False
    assert len(adapter.calls) == calls
    _both_runs(
        cli,
        adapter,
        "plane_pour",
        ["pcb", "copper", "pour", "--project", str(board), "--net", "GND", "--layer", "2"],
        {
            "pcb": "Board.pcb",
            "copper": {
                "before": {"geometry": {"model": {}}},
                "target": {"net": "GND", "net_uid": "11", "layer": 2},
                "rectangle": [0, 0, 10, 10],
                "expected_assignment_changes": [],
                "fingerprint": "fixture",
            },
        },
    )
    calls = len(adapter.calls)
    code, refused = cli(
        "pcb",
        "copper",
        "remove",
        "--project",
        str(board),
        "--net",
        "GND",
        "--layer",
        "1",
        "--dry-run",
    )
    assert code != 0 and refused["error"]["code"] == "E_UNSUPPORTED"
    assert len(adapter.calls) == calls
    code, payload = cli(
        "pcb",
        "copper",
        "pour",
        "--project",
        str(board),
        "--layer",
        "1",
        "--remove",
        "--replace",
        "--dry-run",
    )
    assert code == 2 and payload["error"]["code"] == "E_USAGE"


@pytest.mark.parametrize(
    "argv",
    [
        ["pcb", "outline", "set", "--width", "0", "--height", "10"],
        ["pcb", "outline", "set", "--width", "10", "--height", "10", "--radius", "5"],
        ["pcb", "holes", "add-corners", "--diameter", "-1"],
        ["pcb", "constraints", "widths", "--class", "P", "--width", "0"],
        ["pcb", "constraints", "widths", "--class", "P", "--width", "0.3", "--min", "0"],
        ["pcb", "copper", "pour", "--net", "GND", "--layer", "0"],
        ["pcb", "silkscreen", "labels", "--gap", "-0.1"],
        ["pcb", "render", "--output", "b.png", "--side", "left"],
        ["pcb", "render", "--output", "b.png", "--scale", "500"],
        ["pcb", "render", "--output", "b.jpg"],
        ["manufacturing", "export", "--formats", "odb,step"],
        ["pcb", "routing", "unroute", "--nets", ","],
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
    code, payload = cli("pcb", "checks", "run", "--project", str(tmp_path / "No.pcb"))
    assert code == 3
    other = tmp_path / "board.json"
    other.write_text("{}", encoding="utf-8")
    code, payload = cli("pcb", "checks", "run", "--project", str(other))
    assert code == 2 and adapter.calls == []


def test_a_write_run_without_the_gate_is_refused(cli, adapter, board) -> None:
    code, payload = cli(
        "pcb", "outline", "set", "--project", str(board), "--width", "10", "--height", "10"
    )
    assert code == 5 and payload["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    code, payload = cli(
        "pcb", "silkscreen", "labels", "--project", str(board), "--dry-run", "--confirm", "t"
    )
    assert code == 2 and adapter.calls == []


def test_a_token_is_single_use_and_bound_to_its_arguments(cli, adapter, board) -> None:
    adapter.on("board_outline", {"pcb": "Board.pcb"})
    base = ["pcb", "outline", "set", "--project", str(board), "--height", "45"]
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
    code, payload = cli("pcb", "placement", "auto", "--project", str(board), "--dry-run")
    assert code == 0 and payload["data"]["preview"]["dangerous"] is False
    code, _ = cli(
        "pcb",
        "placement",
        "auto",
        "--project",
        str(board),
        "--confirm",
        payload["data"]["confirm_token"],
    )
    assert code == 0 and adapter.last("arrange_components")["apply"] is True
    routing["traces"] = 12
    code, payload = cli("pcb", "placement", "auto", "--project", str(board), "--dry-run")
    assert payload["data"]["preview"]["dangerous"] is True
    token = payload["data"]["confirm_token"]
    code, _ = cli("pcb", "placement", "auto", "--project", str(board), "--confirm", token)
    assert code == 5
    code, _ = cli(
        "pcb", "placement", "auto", "--project", str(board), "--confirm", token, "--dangerous"
    )
    assert code == 0


def test_arrange_takes_zone_labels_from_the_design(cli, adapter, board) -> None:
    """A sheet's label goes to the group its parts fall in: the demo draws its 1xx parts
    on sheet 2, so the POWER label belongs to group 1, not to sheet 1's OVERVIEW."""
    design = Path(__file__).resolve().parents[1] / "examples" / "demo-sensor-board.json"
    adapter.on("arrange_components", {"pcb": "Board.pcb", "plan": [], "digest": "d", "routing": {}})
    cli("pcb", "placement", "auto", "--project", str(board), "--design", str(design), "--dry-run")
    zones = adapter.last("arrange_components")["zones"]
    assert zones == {"1": "POWER", "2": "MCU / SENSOR", "3": "IO / TEST"}


def test_move_one_part(cli, adapter, board) -> None:
    dry, _ = _both_runs(
        cli,
        adapter,
        "move_component",
        [
            "pcb",
            "placement",
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
    code, payload = cli(
        "pcb", "placement", "move", "--project", str(board), "--refdes", "U1", "--dry-run"
    )
    assert code == 2 and "--file" in payload["error"]["message"]
    code, payload = cli(
        "pcb",
        "placement",
        "move",
        "--project",
        str(board),
        "--refdes",
        "U1",
        "--to",
        "north",
        "--dry-run",
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
    from test_placement import material_state

    planned = plan_placement(
        params["request"], _observed(), protection_state=material_state(_observed())
    )
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
            "persistence_verified": True,
        }
    return result


def test_move_a_selection_by_a_task_file(cli, adapter, board, tmp_path) -> None:
    task = _task(tmp_path)
    adapter.on("placement_batch", _placement)
    code, payload = cli(
        "pcb", "placement", "move", "--project", str(board), "--file", str(task), "--dry-run"
    )
    assert code == 0 and payload["data"]["preview"]["summary"]["changed_count"] == 2
    token = payload["data"]["confirm_token"]
    code, payload = cli(
        "pcb", "placement", "move", "--project", str(board), "--file", str(task), "--confirm", token
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
    _, payload = cli(
        "pcb", "placement", "move", "--project", str(board), "--file", str(task), "--dry-run"
    )
    code, payload = cli(
        "pcb",
        "placement",
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
    code, payload = cli(
        "pcb", "placement", "move", "--project", str(board), "--file", str(task), "--dry-run"
    )
    assert code == 7 and payload["error"]["code"] == "E_SERVER"


def test_labels(cli, adapter, board) -> None:
    dry, _ = _both_runs(
        cli,
        adapter,
        "tidy_labels",
        ["pcb", "silkscreen", "labels", "--project", str(board), "--gap", "0.3"],
        {
            "pcb": "Board.pcb",
            "moved": 3,
            "labels": [],
            "saved": True,
            "verification": {"valid": True},
        },
    )
    assert dry["preview"]["moved"] == 3 and adapter.last("tidy_labels")["gap"] == 0.3


# -- routing -----------------------------------------------------------------------------


def test_autorouter_and_blanket_unroute_are_disabled(cli, adapter, board) -> None:
    for flags in ([], ["--unroute"], ["--unroute", "--dangerous"]):
        code, payload = cli("pcb", "routing", "auto", "--project", str(board), *flags, "--dry-run")
        assert code == 2 and payload["error"]["code"] == "E_UNSUPPORTED"
        assert payload["error"]["details"]["write_attempted"] is False
    assert "route_board" not in adapter.methods()


def test_trace_exposes_blockers_and_via_refuses(cli, adapter, board) -> None:
    adapter.on(
        "hand_route",
        lambda params: {
            "pcb": str(board),
            "items": params["items"],
            "nets": ["I2C_SCL"],
            "trace": {
                "fingerprint": "a" * 64,
                "can_apply": False,
                "blockers": [{"code": "local_rule_applicability_unproven"}],
            },
        },
    )
    code, payload = cli(
        "pcb",
        "routing",
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
    assert code == 0 and payload["data"]["preview"]["can_apply"] is False
    assert payload["data"]["preview"]["changes"][0]["action"] == "write_refused"
    item = adapter.last("hand_route")["items"][0]
    assert item["kind"] == "trace" and item["net"] == "I2C_SCL"
    assert adapter.last("hand_route")["start"] is False
    code, payload = cli(
        "pcb", "routing", "via", "--project", str(board), "--net", "GND", "--at", "3,3", "--dry-run"
    )
    assert code == 2 and payload["error"]["code"] == "E_UNSUPPORTED"
    code, payload = cli(
        "pcb", "routing", "trace", "--project", str(board), "--points", "1,1 5,1", "--dry-run"
    )
    assert code == 2 and "--net" in payload["error"]["message"]


def test_a_trace_plan_is_checked_against_the_geometry_first(cli, adapter, board, tmp_path) -> None:
    geometry = tmp_path / "board.json"
    geometry.write_text(json.dumps(routing_model()), encoding="utf-8")
    adapter.on(
        "hand_route",
        lambda params: {
            "pcb": "Board.pcb",
            "items": params["items"],
            "trace": {"fingerprint": "a" * 64, "can_apply": False, "blockers": []},
        },
    )
    # straight through J1's pad on net B: a clearance problem
    argv = [
        "pcb",
        "routing",
        "trace",
        "--project",
        str(board),
        "--net",
        "A",
        "--layer",
        "1",
        "--width",
        "0.254",
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
        "pcb",
        "routing",
        "stitch-plan",
        "--geometry",
        str(geometry),
        "--net",
        "GND",
        "--output",
        str(plan),
    )
    assert code == 0 and payload["data"]["vias"] >= 1 and adapter.calls == []
    assert json.loads(plan.read_text(encoding="utf-8"))["items"] == payload["data"]["items"]
    code, payload = cli(
        "pcb", "routing", "stitch-plan", "--geometry", str(geometry), "--output", str(plan)
    )
    assert code == 6
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    code, payload = cli("pcb", "routing", "stitch-plan", "--geometry", str(bad))
    assert code == 2


def test_unroute_is_always_dangerous(cli, adapter, board) -> None:
    adapter.on(
        "unroute_nets",
        {
            "pcb": "Board.pcb",
            "targets": ["A", "B"],
            "to_delete": {"traces": 4},
            "unroute": {"fingerprint": "synthetic-gate", "selected_objects": [{"uid": "1"}]},
        },
    )
    code, payload = cli(
        "pcb", "routing", "unroute", "--project", str(board), "--nets", "A,B", "--dry-run"
    )
    assert code == 0 and payload["data"]["preview"]["total"] == 2
    token = payload["data"]["confirm_token"]
    assert (
        cli(
            "pcb",
            "routing",
            "unroute",
            "--project",
            str(board),
            "--nets",
            "A,B",
            "--confirm",
            token,
        )[0]
        == 5
    )
    assert (
        cli(
            "pcb",
            "routing",
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
    code, payload = cli("pcb", "routing", "unroute", "--project", str(board), "--dry-run")
    assert code == 2 and "--nets" in payload["error"]["message"]
    code, refused = cli(
        "pcb",
        "routing",
        "unroute",
        "--project",
        str(board),
        "--all",
        "--continue-on-error",
        "false",
        "--dry-run",
    )
    assert code != 0 and refused["error"]["code"] == "E_UNSUPPORTED"


# -- inspection ------------------------------------------------------------------------


def test_info_reads_the_stackup_and_net_classes(cli, adapter, board) -> None:
    answer = {
        "pcb": str(board),
        "component_count": 3,
        "placed_count": 2,
        "net_count": 2,
        "track_count": 1,
        "via_count": 0,
        "layer_count": 4,
        "thickness_mm": 1.6,
        "stackup": [{"board_layer": 2, "kind": "conductor", "layer": 1, "usage": "signal"}],
        "net_classes": [{"name": "POWER", "nets": ["GND"], "widths": {"1": {"typical": 0.5}}}],
        "keepout_count": 0,
        "prompts": [],
    }
    adapter.on("board_info", answer)
    code, payload = cli("pcb", "inspect", "--project", str(board), "--timeout", "200")
    data = payload["data"]
    assert code == 0 and data["layer_count"] == 4 and data["net_classes"][0]["name"] == "POWER"
    assert adapter.calls[-1]["timeout"] == 200.0
    assert adapter.last("board_info") == {"project": str(board), "start": True}


def test_named_nets_do_not_override_autorouter_impact_refusal(cli, adapter, board) -> None:
    code, payload = cli(
        "pcb",
        "routing",
        "auto",
        "--project",
        str(board),
        "--nets",
        "I2C_SCL,I2C_SDA",
        "--layers",
        "1,4",
        "--dry-run",
    )
    assert code == 2 and payload["error"]["code"] == "E_UNSUPPORTED"
    assert "route_board" not in adapter.methods()


def test_geometry_filters_and_what_refuses_a_filtered_model(cli, adapter, board, tmp_path) -> None:
    adapter.on("board_geometry", {"pcb": "Board.pcb", "counts": {}})
    cli("pcb", "snapshot", "--project", str(board), "--refdes", "U1", "--nets", "GND,+3V3")
    sent = adapter.last("board_geometry")
    assert sent["refdes"] == ["U1"] and sent["nets"] == ["GND", "+3V3"]
    filtered = {**routing_model(), "filter": {"refdes": ["U1"], "nets": []}}
    path = tmp_path / "filtered.json"
    path.write_text(json.dumps(filtered), encoding="utf-8")
    for command in (("pcb", "metrics"), ("pcb", "routing", "stitch-plan")):
        code, payload = cli(*command, "--geometry", str(path))
        assert code == 2 and "whole board" in payload["error"]["message"], command


def test_metrics_can_measure_the_live_board(cli, adapter, board) -> None:
    adapter.on("board_geometry", lambda params: {"pcb": "Board.pcb", "model": routing_model()})
    code, payload = cli("pcb", "metrics", "--project", str(board))
    assert code == 0 and payload["data"]["summary"]["vias"] == 1
    assert "output" not in adapter.last("board_geometry")
    code, payload = cli("pcb", "metrics", "--project", str(board), "--geometry", "board.json")
    assert code == 2
    code, payload = cli("pcb", "metrics")
    assert code == 2 and "--geometry" in payload["error"]["message"]


def test_check_adds_the_board_rules_to_the_drc(cli, adapter, board) -> None:
    model = routing_model()
    model["components"].append({"refdes": "R9", "placed": False, "pins": []})
    model["unrouted"] = [{"net": "A", "opens": 1}]
    adapter.on("batch_drc", {"pcb": "Board.pcb", "ran": True, "count": 0, "clean": True})
    adapter.on("board_geometry", {"pcb": "Board.pcb", "model": model})
    code, payload = cli("pcb", "checks", "run", "--project", str(board))
    data = payload["data"]
    rules = {(rule["rule"], rule.get("refdes") or rule.get("net")) for rule in data["board_rules"]}
    assert code == 0 and ("unplaced", "R9") in rules and ("unrouted", "A") in rules
    assert data["clean"] is False and data["board_rule_count"] == len(data["board_rules"])


def test_geometry_render_show_and_check_reach_layout(cli, adapter, board, tmp_path) -> None:
    adapter.on("board_geometry", {"pcb": "Board.pcb", "counts": {"pads": 10}})
    adapter.on("render_board", {"pcb": "Board.pcb", "picture": "b.png"})
    adapter.on("show_board", {"pcb": "Board.pcb", "foreground": True})
    adapter.on("batch_drc", {"pcb": "Board.pcb", "ran": True, "count": 0, "clean": True})
    assert (
        cli("pcb", "snapshot", "--project", str(board), "--output", str(tmp_path / "g.json"))[0]
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
    assert cli("pcb", "view", "--project", str(board), "--top-view")[0] == 0
    assert adapter.last("show_board")["top_view"] is True
    code, payload = cli("pcb", "checks", "run", "--project", str(board))
    assert code == 0 and payload["data"]["clean"] is False
    assert payload["data"]["verification"]["status"] == "incomplete"
    assert adapter.last("batch_drc") == {
        "project": str(board),
        "start": True,
        "run": True,
        "online": False,
        "rules": None,
        "elements": None,
    }
    code, payload = cli(
        "pcb", "snapshot", "--project", str(board), "--output", str(tmp_path / "g.txt")
    )
    assert code == 2


def test_show_is_the_top_view_unless_a_scheme_is_named_and_zooms_onto_parts(
    cli, adapter, board
) -> None:
    adapter.on("show_board", {"pcb": "Board.pcb", "foreground": True})
    assert cli("pcb", "view", "--project", str(board))[0] == 0
    # nothing named: the adapter takes the top view
    assert adapter.last("show_board") == {"project": str(board), "start": True}
    code, _payload = cli(
        "pcb", "view", "--project", str(board), "--refdes", "J1,U2", "--scheme", "Loc: All On"
    )
    assert code == 0
    sent = adapter.last("show_board")
    assert sent["refdes"] == ["J1", "U2"] and sent["scheme"] == "Loc: All On"
    code, payload = cli(
        "pcb", "view", "--project", str(board), "--scheme", "Loc: All On", "--top-view"
    )
    assert code == 2 and payload["error"]["code"] == "E_USAGE"


def test_render_draws_the_parts_named_at_the_scale_asked_or_its_own(
    cli, adapter, board, tmp_path
) -> None:
    adapter.on("render_board", {"pcb": "Board.pcb", "picture": {"path": "j1.png"}})
    output = str(tmp_path / "j1.png")
    code, _payload = cli(
        "pcb", "render", "--project", str(board), "--output", output, "--refdes", "J1"
    )
    assert code == 0
    sent = adapter.last("render_board")
    assert sent["refdes"] == ["J1"] and "scale" not in sent  # the adapter sizes a close-up
    code, _payload = cli(
        "pcb", "render", "--project", str(board), "--output", output, "--scale", "80"
    )
    assert code == 0 and adapter.last("render_board")["scale"] == 80.0
    assert "refdes" not in adapter.last("render_board")
    code, payload = cli(
        "pcb", "render", "--project", str(board), "--output", output, "--scale", "300"
    )
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"


def test_check_judges_the_silkscreen_texts_against_the_height_asked(cli, adapter, board) -> None:
    model = routing_model()
    model["texts"] = [
        {
            "text": "R1",
            "refdes": "R1",
            "kind": "refdes",
            "side": "top",
            "height": 0.6,
            "pen": 0.12,
            "extents": [1.0, 1.0, 2.0, 1.72],
        }
    ]
    adapter.on("batch_drc", {"pcb": "Board.pcb", "ran": True, "count": 0, "clean": True})
    adapter.on("board_geometry", {"pcb": "Board.pcb", "model": model})
    code, payload = cli("pcb", "checks", "run", "--project", str(board))
    assert code == 0
    assert not [rule for rule in payload["data"]["board_rules"] if rule["rule"] == "label_small"]
    code, payload = cli("pcb", "checks", "run", "--project", str(board), "--min-text-height", "0.8")
    small = [rule for rule in payload["data"]["board_rules"] if rule["rule"] == "label_small"]
    assert code == 0 and [rule["text"] for rule in small] == ["R1"]
    assert small[0]["severity"] == "low"
    code, payload = cli("pcb", "checks", "run", "--project", str(board), "--min-text-height", "9")
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"


def test_labels_can_give_every_designator_one_size(cli, adapter, board) -> None:
    adapter.on(
        "tidy_labels",
        lambda params: {
            "pcb": "Board.pcb",
            "labels": [{"refdes": "C1", "moved": True, "resized": True, "text_height": 0.8}],
            "state_guard": "labels_input_sha256_v1",
            "state_digest": "a" * 64,
            "moved": 1,
            "resized": 1 if params.get("height") or params.get("min_height") else 0,
            "unplaced": [],
            "applied": params.get("apply"),
            "saved": params.get("apply"),
            "verification": {"valid": params.get("apply")},
        },
    )
    args = [
        "pcb",
        "silkscreen",
        "labels",
        "--project",
        str(board),
        "--height",
        "0.8",
        "--pen",
        "0.12",
    ]
    code, payload = cli(*args, "--dry-run")
    preview = payload["data"]["preview"]
    assert code == 0 and preview["resized"] == 1
    assert preview["changes"][0] == {
        "action": "resize_silkscreen_designators",
        "count": 1,
        "height": 0.8,
        "pen": 0.12,
    }
    assert "change size" in preview["risk"]["blast_radius"]
    sent = adapter.last("tidy_labels")
    assert sent["height"] == 0.8 and sent["pen"] == 0.12 and sent["apply"] is False
    token = payload["data"]["confirm_token"]
    # the token is for this size: another size is another write
    code, refused = cli(
        "pcb", "silkscreen", "labels", "--project", str(board), "--height", "1", "--confirm", token
    )
    assert code != 0 and refused["ok"] is False
    code, done = cli(*args, "--confirm", token)
    assert code == 2 and done["error"]["code"] == "E_UNSUPPORTED"
    assert all(p["apply"] is False for p in adapter.params("tidy_labels"))
    for bad in (
        ["--height", "9"],
        ["--min-height", "0.1"],
        ["--pen", "2"],
        ["--height", "0.6", "--pen", "0.3"],
        ["--min-height", "0.6", "--pen", "0.3"],
    ):
        code, payload = cli(
            "pcb", "silkscreen", "labels", "--project", str(board), *bad, "--dry-run"
        )
        assert code == 2 and payload["error"]["code"] == "E_VALIDATION", bad
    # a company minimum raises only the lower designators; it is not also a height
    code, payload = cli(
        "pcb", "silkscreen", "labels", "--project", str(board), "--min-height", "0.8", "--dry-run"
    )
    assert code == 0 and adapter.last("tidy_labels")["min_height"] == 0.8
    assert "height" not in adapter.last("tidy_labels")
    assert payload["data"]["preview"]["changes"][0] == {
        "action": "resize_silkscreen_designators",
        "count": 1,
        "min_height": 0.8,
    }
    code, payload = cli(
        "pcb",
        "silkscreen",
        "labels",
        "--project",
        str(board),
        "--min-height",
        "0.8",
        "--height",
        "1",
        "--dry-run",
    )
    assert code == 2 and payload["error"]["code"] == "E_USAGE"


# -- fabrication ---------------------------------------------------------------------------


def _explicit_label_args(board, tmp_path):
    source = tmp_path / "explicit-labels.json"
    source.write_text(
        json.dumps(
            {
                "unit": "mm",
                "moves": [
                    {
                        "refdes": "C1",
                        "text_uid": "actual-text-1",
                        "text": "C1",
                        "type": 2,
                        "text_type": 1,
                        "side": "top",
                        "side_name": "Top",
                        "layer": 1,
                        "layer_name": "Silkscreen Top",
                        "from": [10, 10],
                        "to": [10, 12],
                    }
                ],
            }
        )
    )
    return ["pcb", "silkscreen", "labels", "--project", str(board), "--file", str(source)]


@pytest.mark.parametrize(
    "bad",
    [
        {"failures": [{"refdes": "C1", "stage": "write"}]},
        {"saved": False},
        {"verification": {"valid": False}},
        {"verification": None},
        {"applied": False},
        {"state_guard": None},
        {"state_digest": "b" * 64},
    ],
)
def test_labels_do_not_hide_partial_or_unverified_writes(
    cli, adapter, board, bad, tmp_path
) -> None:
    answer = {
        "pcb": str(board),
        "state_guard": "explicit_silk_translation_v2",
        "state_digest": "a" * 64,
        "labels": [{"refdes": "C1", "x": 10, "y": 12, "text_height": 0.8, "pen": 0.12}],
        "moved": 1,
        "saved": True,
        "persistence_verified": True,
        "applied": True,
        "write_attempted": True,
        "verification": {"valid": True},
    }
    adapter.on("tidy_labels", lambda params: answer if not params["apply"] else {**answer, **bad})
    argv = _explicit_label_args(board, tmp_path)
    code, planned = cli(*argv, "--dry-run")
    assert code == 0 and adapter.last("tidy_labels")["apply"] is False
    code, failed = cli(*argv, "--confirm", planned["data"]["confirm_token"])
    assert code == 2 and failed["error"]["code"] == "E_PROJECT_INVALID"
    assert failed["error"]["retryable"] is False
    report = failed["error"]["details"]["native_result"]
    assert report["write_attempted"] is True and report["moved"] == 1
    assert failed["error"]["details"]["retry_safe"] is False


def test_labels_response_loss_does_not_invite_replay(cli, adapter, board, tmp_path) -> None:
    plan = {
        "labels": [],
        "moved": 1,
        "state_guard": "explicit_silk_translation_v2",
        "state_digest": "a" * 64,
    }
    adapter.on("tidy_labels", plan)
    argv = _explicit_label_args(board, tmp_path)
    code, planned = cli(*argv, "--dry-run")
    assert code == 0

    def lose_response(params):
        if params["apply"]:
            raise Failure("E_TIMEOUT", "response lost after submission")
        return plan

    adapter.on("tidy_labels", lose_response)
    token = planned["data"]["confirm_token"]
    code, lost = cli(*argv, "--confirm", token)
    assert code == 2 and lost["error"]["code"] == "E_PROJECT_INVALID"
    assert lost["error"]["retryable"] is False
    assert lost["error"]["details"]["outcome"] == "unknown"
    assert lost["error"]["details"]["write_attempted"] is True
    calls = len(adapter.calls)
    code, replay = cli(*argv, "--confirm", token)
    assert code == 6 and replay["error"]["code"] == "E_CONFLICT"
    assert len(adapter.params("tidy_labels")) == calls + 1
    assert adapter.last("tidy_labels")["apply"] is False


def test_labels_bind_the_recomputed_layout_to_the_confirm_token(cli, adapter, board) -> None:
    state = {"state_guard": "labels_input_sha256_v1", "state_digest": "a" * 64, "labels": []}
    adapter.on("tidy_labels", lambda params: dict(state))
    argv = ["pcb", "silkscreen", "labels", "--project", str(board)]
    code, planned = cli(*argv, "--dry-run")
    assert code == 0
    state["state_digest"] = "b" * 64
    code, drift = cli(*argv, "--confirm", planned["data"]["confirm_token"])
    assert code == 6 and drift["error"]["code"] == "E_CONFLICT"
    assert all(params["apply"] is False for params in adapter.params("tidy_labels"))


@pytest.mark.parametrize("mode", ["legacy", "malformed"])
def test_labels_refuse_an_adapter_without_a_known_state_guard(cli, adapter, board, mode) -> None:
    state = {"labels": []}
    if mode == "malformed":
        state.update({"state_guard": "labels_input_sha256_v1", "state_digest": "not-a-digest"})
    adapter.on("tidy_labels", state)
    argv = ["pcb", "silkscreen", "labels", "--project", str(board)]
    for gate in (["--dry-run"], ["--confirm", "old-token"]):
        code, refused = cli(*argv, *gate)
        assert code == 4 and refused["error"]["code"] == "E_BACKEND_UNAVAILABLE"
        assert refused["error"]["details"]["write_attempted"] is False
    assert all(params["apply"] is False for params in adapter.params("tidy_labels"))


def test_labels_keep_a_known_native_prewrite_conflict(cli, adapter, board, tmp_path) -> None:
    state = {"state_guard": "explicit_silk_translation_v2", "state_digest": "a" * 64, "labels": []}

    def answer(params):
        if params["apply"]:
            raise Failure("E_CONFLICT", "layout changed before writing", {"write_attempted": False})
        return state

    adapter.on("tidy_labels", answer)
    argv = _explicit_label_args(board, tmp_path)
    code, planned = cli(*argv, "--dry-run")
    assert code == 0
    code, refused = cli(*argv, "--confirm", planned["data"]["confirm_token"])
    assert code == 6 and refused["error"]["code"] == "E_CONFLICT"
    assert refused["error"]["details"]["write_attempted"] is False


def test_export_plans_the_package_then_writes_it(cli, adapter, board, tmp_path) -> None:
    dry, done = _both_runs(
        cli,
        adapter,
        "manufacturing_output",
        [
            "manufacturing",
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
