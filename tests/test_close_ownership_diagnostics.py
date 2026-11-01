"""Close ownership refusals expose fixed reasons without dropping provenance."""

from __future__ import annotations

import copy
import json

import pytest
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli import driver, native_process, session, session_ownership
from xpedition_cli.driver import Adapter, runtime

ACTUAL = {
    "pid": 4242,
    "executable": "C:/CAD/ExpeditionPCB.exe",
    "creation_time_100ns": "133123456789012345",
}
FOREIGN = {
    "pid": 5252,
    "executable": "C:/synthetic-private-token/Viewdraw.exe",
    "creation_time_100ns": "133123456789012346",
}


def _provenance(identity, domain="schematic"):
    return {
        "version": native_process.STARTUP_LINEAGE_VERSION,
        "domain": domain,
        "ownership": "proven_this_start",
        "launcher_identity": copy.deepcopy(identity),
        "process_chain": [copy.deepcopy(identity)],
        "parent_edges": [],
    }


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("incomplete", "startup_provenance_incomplete"),
        ("other_application", "startup_provenance_other_application"),
        ("invalid_chain", "startup_provenance_chain_invalid"),
    ],
)
def test_existing_ownership_failures_have_fixed_reasons_and_still_refuse(monkeypatch, mode, reason):
    provenance = _provenance(FOREIGN if mode == "other_application" else ACTUAL, "pcb")
    if mode == "incomplete":
        provenance.pop("ownership")
    elif mode == "invalid_chain":
        monkeypatch.setattr(native_process, "_startup_chain", lambda *args: None)
    original = copy.deepcopy(provenance)
    with pytest.raises(native_process.StartupOwnershipError) as caught:
        native_process.owned_startup_launchers(provenance, ACTUAL)
    assert isinstance(caught.value, ValueError)
    assert caught.value.reason == reason
    assert provenance == original
    assert "synthetic-private-token" not in str(caught.value)


@pytest.mark.parametrize("with_save", [False, True])
def test_public_cross_domain_close_preserves_state_and_reports_exact_predispatch_reason(
    tmp_path, monkeypatch, with_save
):
    _scope(tmp_path, monkeypatch)
    session.record_native_start(
        {
            "domain": "schematic",
            "pid": FOREIGN["pid"],
            "process_identity": FOREIGN,
            "startup_provenance": _provenance(FOREIGN),
        }
    )
    before = copy.deepcopy(session.read_state())
    calls = []

    class Backend:
        def invoke(self, method, params, **kwargs):
            calls.append(method)
            assert method == "identity"
            return {"application": {"pid": ACTUAL["pid"]}, "adapter": _worker_fixture()}

    monkeypatch.setattr(native_process, "process_identity", lambda *args: dict(ACTUAL))
    monkeypatch.setattr(
        driver, "_handler", lambda *args: pytest.fail("No handler or Quit is permitted")
    )
    board = tmp_path / "Representative.pcb"
    board.write_bytes(b"offline fixture")
    args = ["session", "close", "--kind", "pcb", "--pid", str(ACTUAL["pid"])]
    if with_save:
        args.extend(["--save-project", str(board)])
    with runtime(Adapter(Backend()), state_dir=tmp_path / "sdk-state") as cli:
        result = cli.run([*args, "--dry-run"])
    assert result.exit_code == 6, result.envelope
    details = result.envelope["error"]["details"]
    assert result.envelope["error"]["code"] == "E_CONFLICT"
    assert details["dispatch_state"] == "not_started"
    assert details["write_attempted"] is False
    assert details["lifecycle_diagnostic"] == {
        "stage": "setup",
        "callsite": "driver._target.owned_startup_launchers",
        "reason": "startup_provenance_other_application",
        "requested_domain": "pcb",
        "recorded_domain": "schematic",
    }
    assert "data" not in result.envelope
    assert "synthetic-private-token" not in json.dumps(result.envelope)
    assert session.read_state() == before
    assert calls == ["identity"]


def test_unclassified_ownership_exception_never_exposes_arbitrary_message(tmp_path, monkeypatch):
    _scope(tmp_path, monkeypatch)
    session.record_native_start(
        {
            "domain": "untrusted-domain-marker",
            "pid": ACTUAL["pid"],
            "process_identity": ACTUAL,
            "startup_provenance": _provenance(ACTUAL, "pcb"),
        }
    )

    class Backend:
        def invoke(self, method, params, **kwargs):
            assert method == "identity"
            return {"application": {"pid": ACTUAL["pid"]}, "adapter": _worker_fixture()}

    def fail(*args):
        raise ValueError("synthetic-private-token in a private path or credential")

    monkeypatch.setattr(native_process, "process_identity", lambda *args: dict(ACTUAL))
    monkeypatch.setattr(native_process, "owned_startup_launchers", fail)
    monkeypatch.setattr(session_ownership, "owned_startup_launchers", fail)
    with runtime(Adapter(Backend()), state_dir=tmp_path / "sdk-state") as cli:
        result = cli.run(
            ["session", "close", "--kind", "pcb", "--pid", str(ACTUAL["pid"]), "--dry-run"]
        )
    details = result.envelope["error"]["details"]
    assert details["lifecycle_diagnostic"]["reason"] == "startup_provenance_validation_failed"
    assert details["lifecycle_diagnostic"]["recorded_domain"] == "unknown"
    assert "synthetic-private-token" not in json.dumps(result.envelope)
    assert "untrusted-domain-marker" not in json.dumps(result.envelope)
    assert details["dispatch_state"] == "not_started"


def test_close_process_identity_failure_is_located_without_claiming_dispatch(tmp_path, monkeypatch):
    _scope(tmp_path, monkeypatch)

    class Backend:
        def invoke(self, method, params, **kwargs):
            assert method == "identity"
            return {"application": {"pid": ACTUAL["pid"]}, "adapter": _worker_fixture()}

    def fail(*args):
        raise OSError("synthetic-private-token")

    monkeypatch.setattr(native_process, "process_identity", fail)
    with runtime(Adapter(Backend()), state_dir=tmp_path / "sdk-state") as cli:
        result = cli.run(
            ["session", "close", "--kind", "pcb", "--pid", str(ACTUAL["pid"]), "--dry-run"]
        )
    details = result.envelope["error"]["details"]
    assert details["lifecycle_diagnostic"] == {
        "stage": "setup",
        "callsite": "driver._target.process_identity",
        "reason": "process_identity_unavailable",
    }
    assert details["dispatch_state"] == "not_started"
    assert details["write_attempted"] is False
    assert "synthetic-private-token" not in json.dumps(result.envelope)
