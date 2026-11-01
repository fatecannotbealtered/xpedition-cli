"""Native identity failures retain the public dispatch phase and original evidence.

All process, CAD snapshot and write facts here are explicit offline boundary fixtures.
"""

from __future__ import annotations

import copy

import pytest
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli import driver
from xpedition_cli.errors import CLIError
from xpedition_cli.operation_budget import current_budget


def _native_snapshot(*, applied=False):
    return {
        "revision": "native",
        "metadata": {
            "backend": "native_xpedition",
            "source": "MGCPCB.ExpeditionPCBApplication",
        },
        "components": [
            {
                "uid": "native-owner-73",
                "refdes": "J73",
                "value": "after" if applied else "before",
                "pins": [{"number": "1", "net": "SIGNAL"}],
            }
        ],
        "unnamed_components": [],
        "nets": [{"name": "SIGNAL"}],
        "sheets": [],
        "connections": [{"net": "SIGNAL", "pins": ["J73.1"]}],
    }


def _break_identity(snapshot, fault):
    value = copy.deepcopy(snapshot)
    if fault == "missing_owner_uid":
        del value["components"][0]["uid"]
    elif fault == "duplicate_owner_uid":
        value["components"].append(copy.deepcopy(value["components"][0]))
    else:
        del value["components"][0]["pins"][0]["number"]
    return value


@pytest.fixture
def revision_boundary(tmp_path, monkeypatch):
    _scope(tmp_path, monkeypatch)
    project = tmp_path / "own-isolated-boundary.prj"
    project.write_bytes(b"synthetic complete target; no CAD installation used")

    class Boundary:
        fault = "missing_owner_uid"
        fail_phase = None
        applied = False
        result_mode = "verified"

        def __init__(self):
            self.calls = []
            self.writes = []

        def invoke(self, method, params, **kwargs):
            self.calls.append((method, copy.deepcopy(params)))
            if method == "identity":
                return {
                    "application": {
                        "pid": 4242,
                        "document": {"open": True, "path": str(project)},
                    },
                    "adapter": _worker_fixture(),
                }
            assert method == "snapshot", "No engineering RPC is part of this fixture"
            snapshot = _native_snapshot(applied=self.applied)
            budget = current_budget()
            if budget.active_stage["stage"] == self.fail_phase:
                snapshot = _break_identity(snapshot, self.fault)
            return snapshot

        def handler(self, options):
            if options.get("dry_run"):
                return {"confirm_token": "synthetic-native-revision-plan", "preview": {}}
            self.writes.append("one-returned-handler-call")
            self.applied = True
            if self.result_mode == "reply_lost":
                raise CLIError("E_TIMEOUT", "Synthetic native handler response was lost", {})
            verified = self.result_mode == "verified"
            return {
                "applied": True,
                "saved": True,
                "persistence_verified": verified,
                "save_count": 1,
                "close_count": int(verified),
                "open_count": int(verified),
                "verification": {
                    "source": "native_getters_after_save_close_open" if verified else "save_ack"
                },
                "fixture": "reported native result; never independent CAD qualification",
            }

    backend = Boundary()
    monkeypatch.setattr(driver, "_handler", lambda declaration: backend.handler)
    args = ["pcb", "update", "--project", str(project), "--dangerous"]
    return backend, args, tmp_path / "operations"


@pytest.mark.parametrize(
    "fault", ["missing_owner_uid", "duplicate_owner_uid", "missing_pin_number"]
)
@pytest.mark.parametrize("phase", ["observe_plan", "observe_confirm"])
def test_public_invalid_native_revision_before_dispatch_is_a_known_read_refusal(
    revision_boundary, fault, phase
):
    backend, args, store = revision_boundary
    backend.fault = fault
    with driver.runtime(driver.Adapter(backend), state_dir=store) as cli:
        if phase == "observe_plan":
            backend.fail_phase = phase
            failed = cli.run([*args, "--dry-run", "--quiet"])
        else:
            planned = cli.run([*args, "--dry-run", "--quiet"])
            assert planned.envelope["ok"], planned.envelope
            operation_id = planned.envelope["data"]["preview"]["operation_id"]
            backend.fail_phase = phase
            failed = cli.run(
                [*args, "--confirm", planned.envelope["data"]["confirm_token"], "--quiet"]
            )
            assert cli._store.get(operation_id)["status"] == "planned"
            assert failed.envelope["error"]["details"]["operation_id"] == operation_id
        assert not failed.envelope["ok"]
        error = failed.envelope["error"]
        assert error["code"] == "E_NATIVE_FAILURE", failed.envelope
        assert error["retryable"] is False and failed.exit_code == 1
        details = error["details"]
        assert details["cause_code"] == "E_VERIFY_INCOMPLETE"
        assert details["native_snapshot_identity_incomplete"] is True
        assert details["dispatch_state"] == "not_started"
        assert details["write_attempted"] is False
        assert details["native_query_only"] is True
        assert "receipt" not in details
        assert details["operation_budget"]["dispatch_started"] is False
    assert backend.writes == []
    assert all(method in {"identity", "snapshot"} for method, _ in backend.calls)


@pytest.mark.parametrize(
    "fault", ["missing_owner_uid", "duplicate_owner_uid", "missing_pin_number"]
)
@pytest.mark.parametrize("phase", ["observe_after", "verify_after", "verify_before"])
@pytest.mark.parametrize("result_mode", ["verified", "save_only", "reply_lost"])
def test_public_invalid_native_revision_after_dispatch_keeps_facts_receipt_and_one_handler(
    revision_boundary, monkeypatch, fault, phase, result_mode
):
    backend, args, store = revision_boundary
    backend.fault = fault
    backend.result_mode = result_mode
    if phase == "observe_after":
        backend.fail_phase = phase
    else:
        original_stable = driver._stable

        def fail_selected_verification(value, *, after_dispatch=False):
            budget = current_budget()
            native = value.get("native") if isinstance(value, dict) else None
            if (
                budget is not None
                and budget.active_stage is not None
                and budget.active_stage["stage"] == "verify"
                and isinstance(native, dict)
                and native["components"][0]["value"]
                == ("after" if phase == "verify_after" else "before")
            ):
                assert after_dispatch is True
                value = {**value, "native": _break_identity(native, fault)}
            return original_stable(value, after_dispatch=after_dispatch)

        monkeypatch.setattr(driver, "_stable", fail_selected_verification)

    with driver.runtime(driver.Adapter(backend), state_dir=store) as cli:
        planned = cli.run([*args, "--dry-run", "--quiet"])
        assert planned.envelope["ok"], planned.envelope
        operation_id = planned.envelope["data"]["preview"]["operation_id"]
        token = planned.envelope["data"]["confirm_token"]
        failed = cli.run([*args, "--confirm", token, "--quiet"])
        assert not failed.envelope["ok"]
        assert failed.envelope["error"]["code"] == (
            "E_VERIFY_INCOMPLETE" if result_mode == "verified" else "E_OUTCOME_UNKNOWN"
        ), failed.envelope
        details = failed.envelope["error"]["details"]
        assert details["operation_id"] == operation_id
        assert details["dispatch_state"] == "dispatched"
        assert details.get("write_attempted") is not False
        assert details["failed_stage"] == (
            "observe_after" if phase == "observe_after" else "verify"
        )
        assert details["native_handler_returned"] is (result_mode != "reply_lost")
        assert details["readback_details"]["cause_code"] == "E_VERIFY_INCOMPLETE"
        assert details["readback_details"]["native_snapshot_identity_incomplete"] is True
        assert "write_attempted" not in details["readback_details"]
        assert "native_query_only" not in details["readback_details"]
        assert details["operation_budget"]["dispatch_started"] is True
        receipt = details["receipt"]
        assert receipt["outcome"]["execution"] == (
            "unknown" if result_mode == "reply_lost" else "completed"
        )
        assert receipt["outcome"]["change"] == (
            "unknown" if result_mode == "reply_lost" else "applied"
        )
        assert receipt["outcome"]["persistence"] == (
            "saved" if result_mode == "verified" else "unknown"
        )
        assert receipt["outcome"]["verification"]["status"] == "incomplete"
        if result_mode == "reply_lost":
            assert receipt["extensions"]["result"] is None
            assert receipt["extensions"]["handler_error"]["code"] == "E_TIMEOUT"
        else:
            assert receipt["extensions"]["result"]["fixture"]
            assert receipt["extensions"]["result"]["saved"] is True
        assert receipt["extensions"]["independent_readback_completed"] is False
        assert receipt["extensions"]["save_acknowledged"] is (result_mode != "reply_lost")
        assert receipt["extensions"]["native_reopen_reported"] is (result_mode == "verified")
        stored = copy.deepcopy(cli._store.get(operation_id))
        expected_status = "failed" if result_mode == "verified" else "unknown"
        assert stored["status"] == expected_status and stored["receipt"] == receipt
        lookup = cli.run(["operation", "show", "--id", operation_id, "--quiet"])
        assert lookup.envelope["data"]["operation_status"]["status"] == expected_status
        replay = cli.run([*args, "--confirm", token, "--quiet"])
        assert not replay.envelope["ok"]
        assert cli._store.get(operation_id)["receipt"] == stored["receipt"]
        assert cli._store.get(operation_id)["status"] == expected_status
    assert backend.writes == ["one-returned-handler-call"]
