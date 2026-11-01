"""Real sharing failures are distinct from successful ledger-recovery units."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import time

import pytest
from confirmation_holders import held_file, storage_trace

from xpedition_cli import confirm
from xpedition_cli import confirmation_store as store
from xpedition_cli.errors import CLIError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows CreateFile sharing semantics")
SCOPE = {"operation": "test-only", "project": "synthetic", "backend": "mock"}


@pytest.fixture
def config(tmp_path, monkeypatch):
    directory = tmp_path / "private-confirmation-store"
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(directory))
    return directory


@pytest.fixture
def holder(record_property):
    resources = []
    yield lambda path, mode: held_file(path, mode, resources)
    record_property("owned_holder_resources", json.dumps(resources, sort_keys=True))
    assert len(resources) == 1
    assert all(
        item.get("handle_closed") is True
        and item["exit_code"] == 0
        and not item["termination_needed"]
        and not item["alive_after_cleanup"]
        for item in resources
    ), resources


def _warning(reason):
    return (
        f"WARNING: confirmation replay protection degraded ({reason}); "
        "do not automatically retry writes.\n"
    )


@pytest.mark.parametrize(
    ("mode", "target", "reasons", "failure_function"),
    [
        ("deny_delete", "ledger", ["ledger_unreadable", "ledger_write_failed"], "atomic_write"),
        (
            "deny_all_sharing",
            "sidecar",
            ["lock_unavailable", "ledger_unreadable"],
            "exclusive_lock",
        ),
        (
            "deny_read_and_delete",
            "ledger",
            ["ledger_unreadable", "ledger_write_failed"],
            "atomic_write",
        ),
    ],
)
def test_real_sharing_failure_preserves_damaged_ledger_and_warns(
    config, holder, capsys, record_property, mode, target, reasons, failure_function
):
    token, _ = confirm.issue(SCOPE)
    ledger = config / "confirm-consumed.json"
    sidecar = config / "confirm-consumed.json.lock"
    ledger.write_bytes(b"\xff")
    sidecar.touch()
    with holder(ledger if target == "ledger" else sidecar, mode):
        with storage_trace() as observed:
            confirm.consume(token, SCOPE)
    record_property("storage_observation", json.dumps(observed, sort_keys=True))
    assert any(
        item["function"] == failure_function and item["errno"] == errno.EACCES
        for item in observed["errors"]
    ), observed
    if target == "sidecar":
        assert "atomic_write" not in observed["calls"]
    else:
        assert any(
            item["function"] == "atomic_write" and item["winerror"] in {5, 32}
            for item in observed["errors"]
        ), observed
    assert ledger.read_bytes() == b"\xff"
    with pytest.raises(UnicodeDecodeError):
        ledger.read_text(encoding="utf-8")
    assert sidecar.exists()
    assert not list(config.glob("*.tmp"))
    streams = capsys.readouterr()
    assert streams.out == ""
    assert streams.err == "".join(_warning(reason) for reason in reasons)
    assert token not in streams.err and str(config) not in streams.err


def test_damaged_ledger_recovers_on_disk_after_sharing_is_released(
    config, holder, capsys, record_property
):
    degraded_token, _ = confirm.issue(SCOPE)
    ledger = config / "confirm-consumed.json"
    ledger.write_bytes(b"\xff")

    with holder(ledger, "deny_delete"):
        with storage_trace() as blocked:
            confirm.consume(degraded_token, SCOPE)
        assert ledger.read_bytes() == b"\xff"
    record_property("blocked_storage_observation", json.dumps(blocked, sort_keys=True))
    assert any(
        item["function"] == "atomic_write"
        and item["errno"] == errno.EACCES
        and item["winerror"] in {5, 32}
        for item in blocked["errors"]
    ), blocked
    assert not list(config.glob("*.tmp"))
    streams = capsys.readouterr()
    assert streams.out == ""
    assert streams.err == _warning("ledger_unreadable") + _warning("ledger_write_failed")
    assert degraded_token not in streams.err and str(config) not in streams.err

    # Use a new token after the owned holder has closed, not a retry of the
    # degraded attempt. The real lock, read and replacement all remain in use.
    recovery_token, _ = confirm.issue(SCOPE)
    assert recovery_token != degraded_token
    with storage_trace() as recovered:
        confirm.consume(recovery_token, SCOPE)
    record_property("recovered_storage_observation", json.dumps(recovered, sort_keys=True))
    assert "atomic_write" in recovered["calls"] and recovered["errors"] == [], recovered
    entries = json.loads(ledger.read_text(encoding="utf-8"))
    fingerprint = hashlib.sha256(recovery_token.encode("utf-8")).hexdigest()
    assert list(entries) == [fingerprint]
    assert entries[fingerprint] > time.time()
    streams = capsys.readouterr()
    assert streams.out == "" and streams.err == _warning("ledger_unreadable")
    assert recovery_token not in streams.err and str(config) not in streams.err

    before = ledger.read_bytes()
    with storage_trace() as replay:
        with pytest.raises(CLIError, match="already used") as failure:
            confirm.consume(recovery_token, SCOPE)
    assert failure.value.code == "E_CONFLICT"
    assert "atomic_write" not in replay["calls"]
    assert ledger.read_bytes() == before
    streams = capsys.readouterr()
    assert streams.out == "" and streams.err == ""


def test_real_byte_lock_contention_is_conflict_without_storage_degradation(
    config, holder, monkeypatch, capsys, record_property
):
    token, _ = confirm.issue(SCOPE)
    ledger = config / "confirm-consumed.json"
    sidecar = config / "confirm-consumed.json.lock"
    ledger.write_bytes(b"\xff")
    monkeypatch.setattr(store, "LOCK_TIMEOUT_SECONDS", 0.12)
    with holder(sidecar, "byte_lock"):
        with storage_trace() as observed:
            with pytest.raises(CLIError) as failure:
                confirm.consume(token, SCOPE)
    record_property("storage_observation", json.dumps(observed, sort_keys=True))
    assert any(
        item["function"] == "_try_lock" and item["errno"] == errno.EACCES
        for item in observed["errors"]
    ), observed
    assert failure.value.code == "E_CONFLICT"
    assert failure.value.details == {"stage": "confirmation_lock", "write_attempted": False}
    assert "read_consumed" not in observed["calls"]
    assert "atomic_write" not in observed["calls"]
    assert ledger.read_bytes() == b"\xff"
    assert sidecar.exists()
    streams = capsys.readouterr()
    assert streams.out == "" and streams.err == ""


@pytest.mark.parametrize(
    ("mode", "target"), [("deny_all_sharing", "sidecar"), ("deny_delete", "ledger")]
)
def test_known_consumption_still_rejects_replay_during_real_sharing_failure(
    config, holder, capsys, record_property, mode, target
):
    token, _ = confirm.issue(SCOPE)
    confirm.consume(token, SCOPE)
    ledger = config / "confirm-consumed.json"
    sidecar = config / "confirm-consumed.json.lock"
    before = ledger.read_bytes()
    with holder(ledger if target == "ledger" else sidecar, mode):
        with storage_trace() as observed:
            with pytest.raises(CLIError, match="already used") as failure:
                confirm.consume(token, SCOPE)
    record_property("storage_observation", json.dumps(observed, sort_keys=True))
    assert failure.value.code == "E_CONFLICT"
    assert "atomic_write" not in observed["calls"]
    assert ledger.read_bytes() == before
    assert sidecar.exists()
    streams = capsys.readouterr()
    assert streams.out == ""
    assert streams.err == (_warning("lock_unavailable") if target == "sidecar" else "")
    assert token not in streams.err and str(config) not in streams.err
