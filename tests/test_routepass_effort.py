"""One explicitly bound native effort choice, with no automatic route retries."""

import copy
from pathlib import Path

import pytest
from test_routepass_geometry import baseline
from test_routepass_guard import native_case
from test_routepass_three_pad import sdk_three_case

from xpedition_cli import board_routepass as R
from xpedition_cli import driver
from xpedition_cli import native_com_adapter as A
from xpedition_cli.cli import pcb
from xpedition_cli.errors import CLIError


@pytest.mark.parametrize("effort", [0, 3, 5, -1, True, False, None, "2", 2.0])
def test_direct_request_rejects_unsupported_or_untyped_native_effort(effort):
    with pytest.raises(ValueError):
        R.validate({"net": "A", "layer": 1, "effort": effort})


@pytest.mark.parametrize("effort", [0, 3, 5, -1, True, False, None, "0", "3", "02", "2.0", 2.0])
def test_public_handler_rejects_unsupported_effort_before_native(effort):
    options = {
        "mode": "bounded-native",
        "nets": "A",
        "layers": "1",
        "source_backup": "owned.zip",
        "native_effort": effort,
    }
    with pytest.raises(CLIError):
        pcb.native_route_options(options)


@pytest.mark.parametrize("effort", [None, 1, "1", 2, "2"])
def test_public_request_keeps_default_observation_shape_and_binds_only_effort_two(effort):
    options = {"mode": "bounded-native", "nets": "A", "layers": "1", "source_backup": "owned.zip"}
    if effort is not None:
        options["native_effort"] = effort
    actual = pcb.native_route_options(options)
    expected = {"net": "A", "layer": 1, **({"effort": 2} if effort in (2, "2") else {})}
    assert actual == expected
    assert R.validate(actual) == expected
    assert R.validate({"net": "A", "layer": 1, "effort": 1}) == {"net": "A", "layer": 1}
    assert "effort" not in R._item(actual)


def test_effort_two_is_an_exact_configuration_and_fingerprint_input():
    state = baseline()
    ordinary = R.bind(state, {"net": "A", "layer": 1}, [], A)
    effort_two = R.bind(state, {"net": "A", "layer": 1, "effort": 2}, [], A)
    assert ordinary["configuration"]["pass_type"] == [6, 1, 1, False, False]
    assert effort_two["configuration"]["pass_type"] == [6, 2, 2, False, False]
    assert ordinary["fingerprint"] != effort_two["fingerprint"]
    for key in ("config", "items", "order", "remove_layers", "add_layer", "go_count"):
        assert ordinary["configuration"][key] == effort_two["configuration"][key]


def test_existing_public_leaf_parses_native_effort_two_without_new_command():
    from xpedition_cli.main import parse_argv

    command, options = parse_argv(
        "pcb routing auto --mode bounded-native --project owned.prj --nets A --layers 1 "
        "--source-backup owned.zip --native-effort 2 --dry-run".split()
    )
    assert command.path == "pcb routing auto"
    assert pcb.native_route_options(options) == {"net": "A", "layer": 1, "effort": 2}


def test_actual_dispatch_and_receipt_use_both_effort_two_arguments_once(tmp_path, monkeypatch):
    app, params, events, *_ = native_case(tmp_path, monkeypatch)
    params["routepass_request"]["effort"] = 2
    planned = R.execute(params, object(), A)
    params.update(apply=True, expected=planned["state_digest"])
    result = R.execute(params, object(), A)
    assert events[0] == ("PassType", 6, 2, 2, False, False)
    assert sum(event[0] == "Go" for event in events) == 1
    assert R.verify_receipt(result, planned["routepass"], app.ActiveDocument.state)["valid"]
    result["native_calls_returned"][0]["arguments"] = [6, 1, 1, False, False]
    assert not R.verify_receipt(result, planned["routepass"], app.ActiveDocument.state)["valid"]


def test_effort_drift_at_confirmation_refuses_before_configuration_or_go(tmp_path, monkeypatch):
    _, params, events, *_ = native_case(tmp_path, monkeypatch)
    params["routepass_request"]["effort"] = 2
    planned = R.execute(params, object(), A)
    params["routepass_request"].pop("effort")
    params.update(apply=True, expected=planned["state_digest"])
    with pytest.raises(A.AdapterError) as caught:
        R.execute(params, object(), A)
    assert caught.value.code == "E_CONFLICT"
    assert events == []


@pytest.mark.parametrize("lost_reply", [False, True])
def test_public_three_pin_effort_two_has_one_go_and_no_old_confirmation_replay(
    tmp_path, monkeypatch, lost_reply
):
    case = sdk_three_case(tmp_path, monkeypatch, fault="go_reply_loss" if lost_reply else None)
    args = [*case.args, "--native-effort", "2"]
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        raw = planned.envelope["data"]["preview"]["extensions"]["legacy_preview"]
        assert raw["routepass"]["request"] == {"net": "A", "layer": 1, "effort": 2}
        token = planned.envelope["data"]["confirm_token"]
        done = cli.run([*args, "--confirm", token])
        assert (done.exit_code != 0) is lost_reply, done.envelope
        assert case.events[0] == ("PassType", 6, 2, 2, False, False)
        assert sum(event[0] == "Go" for event in case.events) == 1
        events = copy.deepcopy(case.events)
        repeated = cli.run([*args, "--confirm", token])
        assert repeated.exit_code != 0, repeated.envelope
        assert case.events == events
        if lost_reply:
            operation_id = planned.envelope["data"]["preview"]["operation_id"]
            shown = cli.run(["operation", "show", "--id", operation_id])
            assert shown.envelope["data"]["operation_status"]["status"] == "unknown"
            refused = cli.run([*args, "--dry-run"])
            assert refused.exit_code != 0
            assert case.events == events
            assert not any(event[0] in {"Save", "Close", "Open"} for event in events)


def test_public_confirmation_cannot_reuse_effort_one_plan_as_effort_two(tmp_path, monkeypatch):
    case = sdk_three_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        refused = cli.run(
            [
                *case.args,
                "--native-effort",
                "2",
                "--confirm",
                planned.envelope["data"]["confirm_token"],
            ]
        )
        assert refused.exit_code != 0, refused.envelope
        assert not any(event[0] in {"Go", "Save", "Close", "Open"} for event in case.events)


@pytest.mark.parametrize("relation_drift", [False, True])
def test_zero_copper_unknown_effort_one_still_blocks_uncoordinated_effort_two_plan(
    tmp_path, monkeypatch, relation_drift
):
    case = sdk_three_case(tmp_path, monkeypatch, fault="no_route")
    before = copy.deepcopy(case.app.ActiveDocument.state)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        failed = cli.run([*case.args, "--confirm", planned.envelope["data"]["confirm_token"]])
        assert failed.exit_code != 0, failed.envelope
        assert case.app.ActiveDocument.state == before
        events = copy.deepcopy(case.events)
        shown = cli.run(
            ["operation", "show", "--id", planned.envelope["data"]["preview"]["operation_id"]]
        )
        assert shown.envelope["data"]["operation_status"]["status"] == "unknown"
        if relation_drift:
            Path(case.params["project"]).write_bytes(b"missing native project-board relation")
        refused = cli.run([*case.args, "--native-effort", "2", "--dry-run"])
        details = refused.envelope["error"]["details"]
        if relation_drift:
            assert details["conflict"] == "state_drift"
            assert details["pcb_document_guard"]["reason"] == "project_board_relation_unavailable"
        else:
            assert details["conflict"] == "unresolved_operation"
            assert details["pcb_document_guard"]["reason"] == "same_physical_pcb_unresolved"
            assert (
                details["related_operation_id"]
                == planned.envelope["data"]["preview"]["operation_id"]
            )
        assert case.events == events
        assert [event[0] for event in events if event[0] in {"Go", "Save", "Close", "Open"}] == [
            "Go"
        ]
