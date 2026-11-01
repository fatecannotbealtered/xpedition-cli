"""General Route public SDK lifecycle, using owned synthetic native objects only."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_board_general import native_general
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli import board_general as G
from xpedition_cli import board_routepass as R
from xpedition_cli import driver
from xpedition_cli import native_com_adapter as A
from xpedition_cli.cli import pcb
from xpedition_cli.errors import CLIError


def setup_case(tmp_path, monkeypatch, *, receipt_fault=None, native_fault=None):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "state"))
    _scope(tmp_path, monkeypatch)
    app, params, events = native_general(tmp_path, monkeypatch, fault=native_fault)
    plan_file = tmp_path / "general-plan.json"
    plan_file.write_text(json.dumps(params["general_request"]), encoding="utf-8")
    calls = []

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, request, **kwargs):
            calls.append((method, request.get("apply")))
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "routepass_state":
                observed = R.observe_request(request, None, A)
                if receipt_fault == "independent_temporary_masks" and any(
                    row[0] == "ProcessCommand" for row in events
                ):
                    counts = observed["state"]["native_mask_proof"]["temporary_masks_count"]
                    counts["before"] += 7
                    counts["after"] += 7
                    assert R._states_equal(
                        observed["state"],
                        app.ActiveDocument.state,
                        G.material_request(params["general_request"]),
                    )
                if receipt_fault == "independent_identity" and any(
                    row[0] == "ProcessCommand" for row in events
                ):
                    observed["identity"]["generation"] = {"birth": "wrong"}
                if receipt_fault == "independent_material" and any(
                    row[0] == "ProcessCommand" for row in events
                ):
                    observed["state"]["native_mask_proof"]["complete"] = False
                return observed
            if method == "route_board":
                try:
                    result = G.execute(request, None, A)
                except A.AdapterError as error:
                    raise CLIError(error.code, str(error), error.details) from error
                # This owned synthetic dispatcher exercises the receipt contract;
                # the production adapter always reports the unproven selector False.
                result["native_dispatch_supported"] = receipt_fault != "unsupported_selector"
                if request.get("apply"):
                    if receipt_fault == "save_none":
                        result["native_calls_returned"][3]["return_kind"] = "none"
                    elif receipt_fault == "missing_consumption":
                        result.pop("state_after")
                    elif receipt_fault == "extra_open":
                        result["native_calls_returned"].append(
                            copy.deepcopy(result["native_calls_returned"][-1])
                        )
                    elif receipt_fault == "wrong_protection":
                        result["state_before_save"]["texts"][0]["position"][0] += 1
                    elif receipt_fault == "producer_flags_only":
                        return {
                            "pcb": result["pcb"],
                            "saved": True,
                            "persistence_verified": True,
                            "geometry_after": result["state_reopened"]["geometry"],
                        }
                return result
            raise AssertionError("Unexpected native operation: " + method)

    backend = Backend()
    monkeypatch.setattr(driver, "NativeBackend", lambda: backend)
    monkeypatch.setattr(pcb, "native", lambda: backend)
    return NS(
        app=app,
        params=params,
        plan_file=plan_file,
        events=events,
        calls=calls,
        args=[
            "pcb",
            "routing",
            "auto",
            "--mode",
            "bounded-general",
            "--project",
            params["project"],
            "--source-backup",
            params["source_backup"],
            "--file",
            str(plan_file),
            "--dangerous",
        ],
    )


def test_sdk_general_uses_independent_material_receipt_and_repeat_has_zero_writes(
    tmp_path, monkeypatch
):
    case = setup_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*case.args, "--dry-run"])
        assert preview.exit_code == 0, preview.envelope
        token = preview.envelope["data"]["confirm_token"]
        done = cli.run([*case.args, "--confirm", token])
        assert done.exit_code == 0, done.envelope
        proof = done.envelope["data"]["receipt"]["extensions"]["file_readback"]
        assert proof["valid"] and not proof["whole_net_complete"]
        assert "General Route" in proof["scope"]
        assert sum(method == "routepass_state" for method, _ in case.calls) >= 3
        assert not any(method == "cell_refresh_state" for method, _ in case.calls)
        before = copy.deepcopy(case.events)
        repeated = cli.run([*case.args, "--confirm", token])
        assert repeated.exit_code == 6, repeated.envelope
        assert case.events == before
        assert sum(event[0] == "ProcessCommand" for event in case.events) == 1


def test_sdk_unproven_selector_preview_and_confirmation_authorize_zero_native_writes(
    tmp_path, monkeypatch
):
    case = setup_case(tmp_path, monkeypatch, receipt_fault="unsupported_selector")
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*case.args, "--dry-run"])
        assert preview.exit_code == 0, preview.envelope
        assert preview.envelope["data"]["preview"]["can_apply"] is False
        result = cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert result.exit_code != 0, result.envelope
    assert case.events == []
    assert not any(method == "route_board" and apply for method, apply in case.calls)


def test_independent_valid_temporary_mask_counts_are_not_saved_design_identity(
    tmp_path, monkeypatch
):
    case = setup_case(tmp_path, monkeypatch, receipt_fault="independent_temporary_masks")
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*case.args, "--dry-run"])
        assert preview.exit_code == 0, preview.envelope
        done = cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert done.exit_code == 0, done.envelope
        receipt = done.envelope["data"]["receipt"]
        assert receipt["extensions"]["file_readback"]["valid"]
        assert sum(event[0] == "ProcessCommand" for event in case.events) == 1


def test_valid_temporary_masks_between_preview_and_confirmation_do_not_change_plan_identity(
    tmp_path, monkeypatch
):
    case = setup_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*case.args, "--dry-run"])
        assert preview.exit_code == 0, preview.envelope
        old = copy.deepcopy(case.app.ActiveDocument.state)
        counts = case.app.ActiveDocument.state["native_mask_proof"]["temporary_masks_count"]
        counts["before"] += 7
        counts["after"] += 7
        assert R._states_equal(
            old, case.app.ActiveDocument.state, G.material_request(case.params["general_request"])
        )
        done = cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert done.exit_code == 0, done.envelope
        assert done.envelope["data"]["receipt"]["extensions"]["file_readback"]["valid"]
        assert sum(event[0] == "ProcessCommand" for event in case.events) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "save_none",
        "missing_consumption",
        "extra_open",
        "wrong_protection",
        "producer_flags_only",
        "independent_identity",
        "independent_material",
    ],
)
def test_sdk_general_cannot_accept_producer_success_with_contradictory_evidence(
    tmp_path, monkeypatch, fault
):
    case = setup_case(tmp_path, monkeypatch, receipt_fault=fault)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*case.args, "--dry-run"])
        assert preview.exit_code == 0, preview.envelope
        result = cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert result.exit_code != 0, result.envelope
        assert sum(event[0] == "ProcessCommand" for event in case.events) == 1
        assert sum(event[0] == "Save" for event in case.events) == 1
        assert sum(event[0] == "Close" for event in case.events) == 1
        assert sum(event[0] == "Open" for event in case.events) == 1


@pytest.mark.parametrize("drift", ["plan", "source", "state"])
def test_sdk_general_refuses_drift_before_selection_or_command(tmp_path, monkeypatch, drift):
    case = setup_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*case.args, "--dry-run"])
        assert preview.exit_code == 0, preview.envelope
        if drift == "plan":
            request = json.loads(case.plan_file.read_text(encoding="utf-8"))
            request["bounds_mm"][0] -= 1
            case.plan_file.write_text(json.dumps(request), encoding="utf-8")
        elif drift == "source":
            Path(case.params["source_backup"]).write_bytes(b"changed closed source")
        else:
            case.app.ActiveDocument.state["texts"][0]["position"][0] += 1
        result = cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert result.exit_code != 0, result.envelope
        assert case.events == []


def test_general_evidence_missing_cannot_fall_back_to_generic_all_net_comparison():
    from xpedition_cli.task_readback import verify_task

    proof = verify_task(
        "pcb routing auto",
        {"mode": "bounded-general"},
        {"saved": True, "persistence_verified": True},
        {},
        {},
        {"extensions": {"legacy_preview": {}}},
    )
    assert proof["valid"] is False
    assert proof["scope"] == "bounded selected General Route"
