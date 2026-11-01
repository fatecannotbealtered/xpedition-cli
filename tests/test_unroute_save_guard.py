"""Exercise real unroute execution; corruption is confined to synthetic COM objects."""

import copy
import json
import os
import subprocess
import sys

import pytest
from test_driver_v2 import _scope, _worker_fixture
from test_unroute_native import native_case

from xpedition_cli import board_unroute as U
from xpedition_cli import native_com_adapter as A
from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


@pytest.mark.parametrize(
    "fault",
    [
        "foreign_trace",
        "protected_hole",
        "protected_component",
        "added_trace",
        "missing_connectivity",
        "foreign_connectivity",
        "missing_opens",
        "wrong_disconnect",
        "identity_failure",
        "observation_failure",
    ],
)
def test_protection_failure_never_saves_closes_or_reopens(tmp_path, monkeypatch, fault):
    app, params, events = native_case(tmp_path, monkeypatch, fault)
    preview = A._unroute_nets(params, object())
    with pytest.raises(A.AdapterError) as caught:
        A._unroute_nets(
            {**params, "apply": True, "expected": preview["unroute"]["fingerprint"]}, object()
        )
    assert events == ["delete:91"], "Protection failure must stop before Save/Close/Open"
    result = caught.value.details["native_result"]
    assert result["persistence_state"] == "unknown"  # Live state says nothing about disk.
    assert result["saved"] is False and result["persistence_verified"] is False
    assert all(result["stages"][s] == "not_started" for s in ("save", "close", "reopen"))
    assert result["delete_attempts"] == [
        {"uid": "91", "kind": "trace", "net": "A", "returned": True}
    ]
    assert app.ActiveDocument is not None


@pytest.mark.parametrize("fault", [None, "partial", "partial_same_net"])
def test_only_protected_authorized_changes_reach_save(tmp_path, monkeypatch, fault):
    _, params, events = native_case(tmp_path, monkeypatch, fault)
    preview = A._unroute_nets(params, object())
    options = {**params, "apply": True, "expected": preview["unroute"]["fingerprint"]}
    if fault:
        with pytest.raises(A.AdapterError) as caught:
            A._unroute_nets(options, object())
        result = caught.value.details["native_result"]
    else:
        result = A._unroute_nets(options, object())
    assert events == [
        "delete:91",
        *(["delete:93" if fault == "partial_same_net" else "delete:92"] if fault else []),
        "save",
        "close",
        "reopen",
    ]
    assert result["persistence_state"] == "saved" and result["persistence_verified"] is True
    assert result["change_status"] == ("partial" if fault else "applied")
    assert result["protection"]["protection_valid"] is True
    if fault == "partial_same_net":
        assert [r["uid"] for r in result["remaining_objects"]] == ["93"]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "partial",
        "partial_same_net",
        "protected_hole",
        "observation_failure",
        "missing_connectivity",
        "missing_opens",
        "connectivity_getter",
        "connectivity_missing",
    ],
)
def test_public_actual_execute_and_fresh_process_preserve_save_guard(tmp_path, monkeypatch, fault):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    _scope(tmp_path, monkeypatch)
    app, params, events = native_case(tmp_path, monkeypatch, fault)

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, options, **kwargs):
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "cell_refresh_state":
                if fault in {"connectivity_getter", "connectivity_missing"}:
                    try:
                        return U.observe(app.ActiveDocument, A)
                    except A.AdapterError as error:
                        raise CLIError(error.code, str(error), error.details) from error
                # A later independent observation may succeed after the handler's
                # observation failed. This cannot retroactively establish saving.
                return copy.deepcopy(app.ActiveDocument.state)
            assert method == "unroute_nets"
            try:
                return U.execute(options, object(), A)
            except A.AdapterError as error:
                raise CLIError(error.code, str(error), error.details) from error

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = [
        "pcb",
        "routing",
        "unroute",
        "--project",
        params["project"],
        "--nets",
        ",".join(params["nets"]),
        "--continue-on-error",
        "false",
        "--dangerous",
    ]
    with runtime(Adapter(backend)) as cli:
        plan = cli.run([*args, "--dry-run"]).envelope
        assert plan["ok"], plan
        token = plan["data"]["confirm_token"]
        op = plan["data"]["preview"]["operation_id"]
        envelope = cli.run([*args, "--confirm", token]).envelope
        receipt = (
            envelope["data"]["receipt"]
            if envelope["ok"]
            else envelope["error"]["details"]["receipt"]
        )
        outcome = receipt["outcome"]
        saved = fault in {None, "partial", "partial_same_net"}
        unobserved = fault in {
            "observation_failure",
            "connectivity_getter",
            "connectivity_missing",
        }
        assert outcome["persistence"] == ("saved" if saved else "unknown")
        assert outcome["change"] == (
            "applied" if fault is None else "unknown" if unobserved else "partial"
        )
        lifecycle = {step: events.count(step) for step in ("save", "close", "reopen")}
        assert lifecycle == dict.fromkeys(lifecycle, 1 if saved else 0)
        assert events[0] == "delete:91"
        if fault == "partial_same_net":
            assert events[:2] == ["delete:91", "delete:93"]
            assert [row["uid"] for row in app.ActiveDocument.state["routing_objects"]] == [
                "92",
                "93",
            ]
            assert outcome["verification"]["status"] != "passed"
        if fault:
            assert not envelope["ok"]
            assert outcome["verification"]["status"] in {"incomplete", "failed"}
        native_result = receipt["extensions"]["result"]
        assert native_result["delete_attempts"][0] == {
            "uid": "91",
            "kind": "trace",
            "net": "A",
            "returned": True,
        }
        assert native_result["saved"] is saved
        assert native_result["persistence_verified"] is saved
        if not saved:
            assert all(
                native_result["stages"][step] == "not_started"
                for step in ("save", "close", "reopen")
            )
        if fault == "partial_same_net":
            assert [row["uid"] for row in native_result["removed_objects"]] == ["91"]
            assert [row["uid"] for row in native_result["remaining_objects"]] == ["93"]
            assert native_result["deletion_errors"] == [
                {"kind": "trace", "uid": "93", "cause": "RuntimeError"}
            ]
            assert native_result["protection"]["protection_valid"] is True
            assert native_result["protection"]["removal_complete"] is False
        if fault in {"connectivity_getter", "connectivity_missing"}:
            failed_reads = [row for row in app.connectivity_reads if row["after_delete"]]
            # Both handler readback and the following independent observation
            # exercise the real strict native reader; repeated failure is not 0 opens.
            assert len(failed_reads) >= 2
            assert all(
                row["result"] == ("raised" if fault == "connectivity_getter" else "missing")
                for row in failed_reads
            )
            assert receipt["extensions"]["independent_readback_completed"] is False
            assert receipt["extensions"]["readback_error"]["code"] == "E_NATIVE_FAILURE"
        observed_events = list(events)
        assert not cli.run([*args, "--confirm", token]).envelope["ok"]
        assert events == observed_events
        observed_reads = list(app.connectivity_reads)
    queried = subprocess.run(
        [sys.executable, "-m", "xpedition_cli", "operation", "show", "--id", op, "--compact"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env=os.environ.copy(),
    )
    assert queried.returncode == 0, queried.stderr
    query_envelope = json.loads(queried.stdout)
    stored = query_envelope["data"]["operation_status"]
    assert stored["receipt"]["outcome"] == outcome
    assert stored["receipt"] == receipt
    assert events == observed_events, "Operation query must not retry native writes"
    assert app.connectivity_reads == observed_reads
    (tmp_path / "public-execution-evidence.json").write_text(
        json.dumps(
            {
                "source": "public SDK and real unroute handler with synthetic native objects",
                "fault": fault,
                "preview": plan,
                "confirm": envelope,
                "query": query_envelope,
                "query_command": queried.args,
                "query_returncode": queried.returncode,
                "events": events,
                "lifecycle_call_counts": lifecycle,
                "connectivity_reads": app.connectivity_reads,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
