"""Real non-CAD children; teardown release is not product cancellation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import execution_lifecycle as L

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-owned subprocess prototype")
FIXTURE = Path(__file__).parent / "fixtures" / "offline_execution_case.py"


def _eventually(root, execution, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = L.observe_execution(root, execution)
        if predicate(result):
            return result
        time.sleep(0.01)
    raise AssertionError("bounded fixture observation did not reach the expected state")


def _release_all(root):
    # Only this test teardown can release the deliberately blocked fixture.
    executions = root / "executions"
    if executions.exists():
        for directory in executions.iterdir():
            if L.EXECUTION_RE.fullmatch(directory.name):
                for name in ("fixture-only-release", "fixture-only-callback-release"):
                    flag = directory / name
                    try:
                        with flag.open("xb") as stream:
                            stream.write(b"fixture teardown only; not product stop\n")
                    except FileExistsError:
                        pass


def _settled(root, execution):
    return _eventually(root, execution, lambda result: result["owner_state"] == "verified_exited")


def _run_case(root, mode, execution=None, **constraints):
    arguments = [sys.executable, "-X", "utf8", "-B", str(FIXTURE), mode, "--root", str(root)]
    if execution is not None:
        arguments += ["--execution", execution]
    for name, value in constraints.items():
        arguments += ["--" + name, str(value)]
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(root.parent),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        output, errors = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        # No kill/terminate: release only our fixture and let the own caller exit.
        _release_all(root)
        process.communicate(timeout=10)
        raise AssertionError("caller exceeded bounded observation") from None
    assert process.returncode == 0, errors.decode("utf-8", errors="replace")
    return json.loads(output)


def _initial_hash(root, execution):
    return hashlib.sha256(
        (root / "executions" / execution / "initial-observation.json").read_bytes()
    ).hexdigest()


def test_real_owner_survives_normal_caller_exit_with_resource_still_held(tmp_path):
    root = tmp_path / "new-offline-root"
    execution = None
    try:
        started = time.monotonic()
        caller = _run_case(root, "launch")
        assert time.monotonic() - started < 5
        execution = caller["observation"]["execution_id"]
        observed = _eventually(root, execution, lambda value: value.get("phase") == "call_entered")
        assert observed["owner_state"] == "verified_alive"
        assert observed["owner_pid"] != caller["caller_pid"]
        assert (
            observed["executor_state"] == "unresolved_owner_alive"
            and not observed["cleanup_verified"]
        )
        resource = root / "executions" / execution / "fixture-resource.bin"
        with pytest.raises(OSError):
            with resource.open("rb") as stream:
                stream.read(1)
        assert observed["original_operation_status"] == "unknown" and not observed["cad_verified"]
    finally:
        _release_all(root)
        if execution:
            assert _settled(root, execution)["cleanup_verified"]


def test_fresh_subprocess_observer_rejects_stale_epoch_or_birth_and_legacy_ids(tmp_path):
    root = tmp_path / "new-offline-root"
    execution = None
    try:
        launched = L.launch_fixture(root)
        execution = launched["execution_id"]
        original = _eventually(root, execution, lambda value: value.get("phase") == "call_entered")
        fresh = _run_case(root, "observe", execution)
        assert fresh["caller_pid"] != os.getpid()
        assert fresh["observation"]["owner_pid"] == original["owner_pid"]
        assert (
            fresh["observation"]["owner_birth_filetime_100ns"]
            == original["owner_birth_filetime_100ns"]
        )
        wrong_epoch = _run_case(root, "observe", execution, epoch="0" * 32)["observation"]
        wrong_birth = _run_case(
            root, "observe", execution, birth=str(int(original["owner_birth_filetime_100ns"]) + 1)
        )["observation"]
        assert wrong_epoch["owner_state"] == wrong_birth["owner_state"] == "identity_mismatch"
        assert not wrong_epoch["cleanup_verified"] and not wrong_birth["cleanup_verified"]
        absent = tmp_path / "must-not-be-created"
        old = L.observe_execution(absent, "op-legacy-fixture-id")
        assert old["executor_state"] == "legacy_unmanaged_unknown" and not absent.exists()
    finally:
        _release_all(root)
        if execution:
            _settled(root, execution)


def test_product_stop_does_not_interrupt_a_blocked_real_child(tmp_path):
    root = tmp_path / "new-offline-root"
    execution = None
    try:
        execution = L.launch_fixture(root)["execution_id"]
        _eventually(root, execution, lambda value: value.get("phase") == "call_entered")
        immutable = _initial_hash(root, execution)
        stopped = L.request_stop(root, execution)
        assert stopped["accepted"] and stopped["blocked_call_interrupted"] is False
        _run_case(root, "observe", execution)
        current = L.observe_execution(root, execution)
        assert current["owner_state"] == "verified_alive" and current["phase"] == "call_entered"
        assert current["stop_requested"] and not current["cleanup_verified"]
        assert current["cleanup_verified"] is None
        assert current["cleanup_observation"] == "in_progress"
        assert current["owner_image_matches"] is True
        assert _initial_hash(root, execution) == immutable
    finally:
        _release_all(root)
        if execution:
            settled = _settled(root, execution)
            assert settled["cleanup_verified"] and settled["original_operation_status"] == "unknown"


@pytest.mark.parametrize("scenario", ["block_until_fixture_release", "return_immediately"])
def test_late_real_return_and_normal_cleanup_append_without_rewriting_unknown(tmp_path, scenario):
    root = tmp_path / "new-offline-root"
    execution = None
    try:
        execution = L.launch_fixture(root, scenario, observation_budget_seconds=0)["execution_id"]
        before = (
            _eventually(root, execution, lambda value: value.get("phase") == "call_entered")
            if scenario == "block_until_fixture_release"
            else L.observe_execution(root, execution)
        )
        initial = _initial_hash(root, execution)
        event_count = before["event_count"]
        _release_all(root)
        complete = _settled(root, execution)
        assert complete["executor_state"] == "completed" and complete["cleanup_verified"]
        assert complete["event_count"] >= event_count and complete["phase"] == "cleanup_complete"
        _, directory, key, intent = L._context(root, execution)
        phases = [event["phase"] for event in L._events(directory, key, intent)]
        assert "call_entered" in phases and "call_returned" in phases
        assert (
            complete["original_operation_status"] == "unknown" and complete["cad_verified"] is False
        )
        assert _initial_hash(root, execution) == initial
        assert not (root / "executions" / execution / "fixture-resource.bin").exists()
    finally:
        _release_all(root)
        if execution:
            _settled(root, execution)


def test_no_permit_abandons_before_dispatch_and_cleanup_failure_remains_unresolved(tmp_path):
    root = tmp_path / "new-offline-root"
    abandoned = L.launch_fixture(root, "no_permit", observation_budget_seconds=0.01)["execution_id"]
    settled = _settled(root, abandoned)
    assert settled["cleanup_verified"] and settled["original_operation_status"] == "unknown"
    assert settled["cleanup_verified"] is True and settled["cleanup_observation"] == "complete"
    assert settled["owner_image_matches"] in (None, True)
    _, directory, key, intent = L._context(root, abandoned)
    events = L._events(directory, key, intent)
    assert "call_entered" not in [event["phase"] for event in events]
    assert events[-2]["phase"] == "abandoned_before_dispatch"
    failed = L.launch_fixture(root, "cleanup_failure")["execution_id"]
    result = _settled(root, failed)
    assert (
        result["executor_state"] == "failed_cleanup_unresolved" and not result["cleanup_verified"]
    )
    assert (root / "executions" / failed / "fixture-resource.bin").exists()
    assert result["original_operation_status"] == "unknown"
    assert result["cleanup_verified"] is False and result["cleanup_observation"] == "failed"
    assert L._owner_image_match(None, {"executable": "expected"}) is None
    assert L._owner_image_match({"executable": None}, {"executable": "expected"}) is None
    assert L._owner_image_match({"executable": "different"}, {"executable": "expected"}) is False


def test_real_callback_owner_keeps_lock_after_call_return_until_fixture_teardown(tmp_path):
    root = tmp_path / "new-offline-root"
    execution = None
    try:
        execution = L.launch_fixture(
            root, "callback_until_fixture_release", observation_budget_seconds=0.05
        )["execution_id"]
        pending = _eventually(
            root, execution, lambda value: value.get("phase") == "callback_inflight"
        )
        assert pending["owner_state"] == "verified_alive" and not pending["cleanup_verified"]
        initial = _initial_hash(root, execution)
        resource = root / "executions" / execution / "fixture-resource.bin"
        with pytest.raises(OSError):
            with resource.open("rb") as stream:
                stream.read(1)
        assert L.request_stop(root, execution)["blocked_call_interrupted"] is False
        fresh = _run_case(root, "observe", execution)["observation"]
        assert fresh["phase"] == "callback_inflight" and not fresh["cleanup_verified"]
        assert fresh["original_operation_status"] == "unknown"
    finally:
        _release_all(root)
        if execution:
            complete = _settled(root, execution)
            assert complete["cleanup_verified"] and complete["owner_state"] == "verified_exited"
            assert _initial_hash(root, execution) == initial


def test_per_root_quota_refuses_third_live_owner_without_spawning_another(tmp_path):
    root = tmp_path / "new-offline-root"
    executions = []
    try:
        for _ in range(2):
            execution = L.launch_fixture(root, observation_budget_seconds=0.02)["execution_id"]
            executions.append(execution)
            _eventually(root, execution, lambda value: value.get("phase") == "call_entered")
        before = {item.name for item in (root / "executions").iterdir()}
        with pytest.raises(L.LifecycleError, match="executor_owner_limit_reached"):
            L.launch_fixture(root)
        assert {item.name for item in (root / "executions").iterdir()} == before
    finally:
        _release_all(root)
        for execution in executions:
            assert _settled(root, execution)["cleanup_verified"]


def test_fresh_observer_leaves_truncated_own_fixture_event_unresolved_and_unchanged(tmp_path):
    root = tmp_path / "new-offline-root"
    execution = None
    expected_owner = None
    try:
        execution = L.launch_fixture(root)["execution_id"]
        _eventually(root, execution, lambda value: value.get("phase") == "call_entered")
        _, directory, key, _ = L._context(root, execution)
        registration, _ = L._read(directory / "owner.json", key)
        expected_owner = registration["owner"]
        initial_hash = _initial_hash(root, execution)
        record = directory / "events" / "event-0002.json"
        original = record.read_bytes()
        # Deliberate corruption is confined to this newly created test fixture.
        record.write_bytes(original[: len(original) // 2])
        corrupted_hash = hashlib.sha256(record.read_bytes()).hexdigest()
        executions_before = {item.name for item in (root / "executions").iterdir()}
        event_names_before = {item.name for item in (directory / "events").iterdir()}
        fresh = _run_case(root, "observe", execution)["observation"]
        assert fresh["executor_state"] == "unresolved"
        assert fresh["reason"] == "record_integrity_incomplete"
        assert fresh["cleanup_verified"] is None and fresh["cleanup_observation"] == "unobserved"
        assert (
            fresh["owner_image_matches"] is None and fresh["original_operation_status"] == "unknown"
        )
        assert not fresh["cad_verified"]
        assert hashlib.sha256(record.read_bytes()).hexdigest() == corrupted_hash
        assert _initial_hash(root, execution) == initial_hash
        assert {item.name for item in (root / "executions").iterdir()} == executions_before
        assert {item.name for item in (directory / "events").iterdir()} == event_names_before
    finally:
        _release_all(root)
        if expected_owner is not None:
            # The corrupt journal cannot qualify cleanup. Teardown waits only
            # on this fixture's previously registered own process generation.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                owner_state, _ = L._fresh_owner(expected_owner)
                if owner_state == "verified_exited":
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("own corrupted fixture executor did not exit normally")
            assert L.observe_execution(root, execution)["cleanup_verified"] is None


def test_local_runtime_preflight_rejects_unc_and_remote_drive_before_filesystem(monkeypatch):
    calls = []

    class DriveType:
        def __call__(self, drive):
            calls.append(drive)
            return 4  # A mapped remote drive must not reach lstat/exists.

    def forbidden(*args, **kwargs):
        raise AssertionError("Filesystem lookup happened before root rejection")

    monkeypatch.setattr(Path, "lstat", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)
    monkeypatch.setattr(
        L.ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace(GetDriveTypeW=DriveType())
    )
    for value in ("\\\\server\\share\\runtime", "//server/share/runtime", "relative-runtime"):
        with pytest.raises(L.LifecycleError, match="plain_local_windows_runtime_root_required"):
            L._plain_path(value)
    assert calls == []
    with pytest.raises(L.LifecycleError, match="runtime_local_fixed_drive_required"):
        L._plain_path("Z:/runtime")
    assert calls == ["Z:\\"]
