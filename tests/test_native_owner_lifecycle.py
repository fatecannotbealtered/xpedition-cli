"""Adapter-shaped method-owner tests without native dependency activation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types

import pytest

from scripts.native_acceptance import owner_lifecycle as lifecycle


class FakeFormal:
    def member(self, obj, library, typ, name, kind):
        return {"dispid": {"Load": 11, "UnLoad": 12}.get(name, 99)}, {"fake": "formal"}


class FakeBaseNative:
    def __init__(self, log, deadline):
        self.calls = []

    def check(self):
        return None


class FakeParent:
    CMA_GETS = {}

    def __init__(self, log, deadline, mode):
        self.formal = FakeFormal()


class RawDispatch:
    def __init__(self, failure=None):
        self.invocations = []
        self.failure = failure

    def Invoke(self, *args):  # noqa: N802 - real raw PyIDispatch adapter spelling
        self.invocations.append(args)
        if self.failure:
            raise self.failure
        return None


class FailingStream:
    def __init__(self, phase):
        self.phase = phase

    def write(self, value):
        if self.phase == "write":
            raise OSError("synthetic event stream write failure")
        return len(value)

    def flush(self):
        if self.phase == "flush":
            raise OSError("synthetic event stream flush failure")


def make_owner(tmp_path, monkeypatch, *, log_failure=None):
    monkeypatch.setattr(lifecycle, "WindowsProcessObserver", lambda: object())
    log = lifecycle.Evidence(tmp_path / "evidence")
    if log_failure:
        log.stream.close()
        log.stream = FailingStream(log_failure)
    else:
        # No native method needs actual durable disk I/O in this synthetic test.
        monkeypatch.setattr(lifecycle.os, "fsync", lambda fd: None)
    minimum = types.SimpleNamespace(classes=lambda base, layout: FakeParent)
    base = types.SimpleNamespace(Native=FakeBaseNative)
    layout = types.SimpleNamespace(
        ReadOnlyNative=types.SimpleNamespace(GETS={}, READ_METHODS=set())
    )
    owner = lifecycle.Owner(
        {"board": "D:/owned-F/PCB/QA_ENTERPRISE.pcb"}, log, minimum, base, layout, None
    )
    return owner, log


@pytest.mark.parametrize("failure", ["write", "flush"])
def test_unload_raw_invoke_occurs_once_despite_actual_event_stream_failure(
    tmp_path, monkeypatch, failure
):
    owner, log = make_owner(tmp_path, monkeypatch, log_failure=failure)
    raw = RawDispatch()
    assert (
        owner.native.call(raw, "ConstraintsAuto", "IDesign", "UnLoad", kind=lifecycle.METHOD)
        is None
    )
    assert owner.counts["UnLoad"] == 1
    assert raw.invocations == [(12, 0, lifecycle.METHOD, 1)]
    assert owner.native.calls == [{"member": "ConstraintsAuto/IDesign.UnLoad", "completed": True}]
    assert len(log.errors) == 2
    assert {error["type"] for error in log.errors} == {"OSError"}


@pytest.mark.parametrize(
    "library,typ,name,kind,args",
    [
        ("ConstraintsAuto", "IConstraint", "Value", lifecycle.PUT, (14,)),
        ("MGCPCB", "IMGCPCBDocument", "Save", lifecycle.METHOD, ()),
        ("MGCPCB", "IProjectIntegration", "SynchCES", lifecycle.METHOD, ()),
    ],
)
def test_non_authorized_value_save_sync_never_reach_raw_invoke(
    tmp_path, monkeypatch, library, typ, name, kind, args
):
    owner, log = make_owner(tmp_path, monkeypatch)
    raw = RawDispatch()
    try:
        with pytest.raises(ValueError, match="allowlist refused"):
            owner.native.call(raw, library, typ, name, *args, kind=kind)
        assert raw.invocations == []
        assert set(owner.counts.values()) == {0}
        assert owner.native.calls == []
    finally:
        log.stream.close()


@pytest.mark.parametrize("failure", ["write", "flush"])
def test_load_durable_event_failure_is_pre_dispatch_zero_attempt(tmp_path, monkeypatch, failure):
    owner, _ = make_owner(tmp_path, monkeypatch, log_failure=failure)
    raw = RawDispatch()
    with pytest.raises(OSError, match="event stream"):
        owner.native.call(
            raw, "ConstraintsAuto", "IDesign", "Load", object(), kind=lifecycle.METHOD
        )
    assert owner.counts["Load"] == 0
    assert raw.invocations == []
    assert owner.native.calls == []


def test_raw_native_exception_has_one_actual_attempt_and_no_retry(tmp_path, monkeypatch):
    owner, log = make_owner(tmp_path, monkeypatch)
    raw = RawDispatch(failure=RuntimeError("raw native UnLoad failure"))
    try:
        with pytest.raises(RuntimeError, match="raw native UnLoad failure"):
            owner.native.call(raw, "ConstraintsAuto", "IDesign", "UnLoad", kind=lifecycle.METHOD)
        assert owner.counts["UnLoad"] == 1
        assert owner.native.calls == [
            {"member": "ConstraintsAuto/IDesign.UnLoad", "completed": False}
        ]
        with pytest.raises(ValueError, match="already attempted"):
            owner.native.call(raw, "ConstraintsAuto", "IDesign", "UnLoad", kind=lifecycle.METHOD)
        assert len(raw.invocations) == 1
        assert owner.counts["UnLoad"] == 1
    finally:
        log.stream.close()


@pytest.mark.parametrize(
    "case,expected_exited", [("absent87", True), ("access5", False), ("reused", True)]
)
def test_exit_observer_distinguishes_absence_denial_and_original_generation_end(
    case, expected_exited
):
    expected = {"pid": 9, "birth_filetime_100ns": "133000000000000001"}

    def observe(pid):
        assert pid == expected["pid"]
        if case == "reused":
            return {"pid": pid, "birth_filetime_100ns": "133000000000000002", "alive": True}
        error = OSError("synthetic Win32 observation failure")
        error.winerror = 87 if case == "absent87" else 5
        raise error

    result = lifecycle.observe_exit(types.SimpleNamespace(observe_pid=observe), expected, seconds=0)
    assert result["exited"] is expected_exited
    assert result["pid_reused"] is (case == "reused")
    if case == "reused":
        assert result["samples"][0]["original_generation_ended"] is True
        assert result["samples"][0]["observed"]["birth_filetime_100ns"] == "133000000000000002"
    else:
        assert result["samples"][0]["winerror"] == (87 if case == "absent87" else 5)


@pytest.mark.parametrize("unreadable", ["locked.db", "<enumeration>"])
def test_unreadable_or_unenumerated_file_is_not_reported_deleted(unreadable):
    kept = {"path": "readable.txt", "bytes": 1, "sha256": "a"}
    before = {
        "files": [kept, {"path": "locked.db", "bytes": 2, "sha256": "b"}],
        "errors": [],
        "complete": True,
    }
    after = {
        "files": [kept],
        "errors": [{"path": unreadable, "type": "PermissionError", "errno": 13}],
        "complete": False,
    }
    result = lifecycle.files_delta(before, after)
    assert result["removed"] == []
    assert result["unobserved"] == [unreadable]
    assert result["complete"] is False
    assert result["manual_Save_called"] is False
    assert result["rule_persistence_proven"] is False


def test_missing_request_real_python_child_reports_bound_zero_cad_marker(tmp_path):
    # Any native dependency import is an error in this pre-input-failure branch.
    # This is a fresh Python child, not a CAD process or an EXE product test.
    child = """
import importlib.abc
import runpy
import sys
class RefuseNativeImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pythoncom', 'win32com', 'comtypes'}:
            raise AssertionError('unexpected native dependency import: ' + fullname)
sys.meta_path.insert(0, RefuseNativeImports())
script = sys.argv.pop(1)
runpy.run_path(script, run_name='__main__')
"""
    operation, digest = "op_synthetic_missing_request", "a" * 64
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            lifecycle.__file__,
            "--request",
            str(tmp_path / "absent.json"),
            "--request-sha256",
            digest,
            "--operation-id",
            operation,
        ],
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    marker = json.loads(result.stderr)
    assert marker["schema"] == "owned-control-early-failure/v1"
    assert marker["operation_id"] == operation
    assert marker["request_sha256"] == digest
    assert marker["native_dispatch_state"] == "not_started"
    assert marker["counts"] == dict.fromkeys(lifecycle.LIMITS, 0)
    assert marker["error"]["type"] == "FileNotFoundError"


def setup_fresh_document_boundary(owner, monkeypatch, observed):
    application, retained, fresh = object(), object(), object()
    owner.application, owner.document = application, retained
    calls = []

    def native_call(obj, library, typ, name, *args, **kwargs):
        calls.append((obj, library, typ, name))
        if (obj, typ, name) == (application, "IMGCPCBApplication", "ActiveDocument"):
            return fresh
        if (obj, typ, name) == (fresh, "IMGCPCBDocument", "FullName"):
            return owner.contract["board"]
        raise AssertionError("Unexpected native boundary call")

    def authenticate(document):
        assert document is fresh
        calls.append("authenticate-fresh")
        return document

    def observe(document, label):
        assert document is fresh
        assert label.endswith("-fresh")
        calls.append("observe-fresh")
        return observed

    monkeypatch.setattr(owner.native, "call", native_call)
    monkeypatch.setattr(owner, "authenticate", authenticate)
    monkeypatch.setattr(owner, "observe_document", observe)
    monkeypatch.setattr(
        owner, "describe_type", lambda reference: {"interface_name": "IMGCPCBDocument"}
    )
    monkeypatch.setattr(owner, "same_iunknown", lambda left, right: False)
    return calls


def test_fresh_same_path_distinct_iunknown_equal_fingerprint_is_observation_not_interchangeability(
    tmp_path, monkeypatch
):
    owner, log = make_owner(tmp_path, monkeypatch)
    held = {"widths": {"Master": 12}, "geometry": {"parts": ["J1", "R1"]}}
    calls = setup_fresh_document_boundary(owner, monkeypatch, held)
    try:
        row = owner.compare_fresh_document("after-open", held)
        assert row["same_IUnknown"] is False
        assert row["fingerprints_equal"] is True
        assert row["interchangeability_proven"] is False
        assert row["fresh_path"] == owner.contract["board"]
        assert calls[-1] == "observe-fresh"
        persisted = json.loads(
            (log.directory / "after-open-physical-document-comparison.json").read_text("utf-8")
        )
        assert persisted == row
    finally:
        log.stream.close()


def test_fresh_same_path_distinct_iunknown_mismatching_actual_fingerprint_is_rejected(
    tmp_path, monkeypatch
):
    owner, log = make_owner(tmp_path, monkeypatch)
    held = {"widths": {"Master": 12}}
    setup_fresh_document_boundary(owner, monkeypatch, {"widths": {"Master": 14}})
    try:
        with pytest.raises(ValueError, match="physical observations disagree"):
            owner.compare_fresh_document("after-open", held)
        persisted = json.loads(
            (log.directory / "after-open-physical-document-comparison.json").read_text("utf-8")
        )
        assert persisted["fresh_path"] == owner.contract["board"]
        assert persisted["same_IUnknown"] is False
        assert persisted["fingerprints_equal"] is False
        assert persisted["interchangeability_proven"] is False
        assert owner.reference_after_open is None
    finally:
        log.stream.close()


@pytest.mark.parametrize("missing", ["comparison", "baseline"])
def test_load_missing_actual_post_open_comparison_or_baseline_refuses_before_cma_create(
    tmp_path, monkeypatch, missing
):
    owner, log = make_owner(tmp_path, monkeypatch)
    creates = []
    monkeypatch.setitem(
        sys.modules,
        "pythoncom",
        types.SimpleNamespace(CoCreateInstance=lambda *args: creates.append(args)),
    )
    if missing == "comparison":
        owner.baseline = {"widths": {"Master": 12}}
    else:
        owner.reference_after_open = {"fingerprints_equal": True}
    try:
        with pytest.raises(ValueError, match="comparison required before Load"):
            owner.load()
        assert creates == []
        assert owner.cma_activations == 0
        assert owner.counts["Load"] == 0
        assert owner.native.calls == []
    finally:
        log.stream.close()


def test_close_missing_actual_post_unload_comparison_refuses_before_close_or_quit(
    tmp_path, monkeypatch
):
    owner, log = make_owner(tmp_path, monkeypatch)
    invokes = []
    monkeypatch.setattr(owner.native, "call", lambda *args, **kwargs: invokes.append(args))
    try:
        with pytest.raises(ValueError, match="post-UnLoad Document comparison missing"):
            owner.close_successful_control()
        assert invokes == []
        assert owner.counts["Close"] == owner.counts["Quit"] == 0
        assert owner.native.calls == []
    finally:
        log.stream.close()


def test_final_identity_evidence_failure_refuses_before_close_or_quit(tmp_path, monkeypatch):
    owner, log = make_owner(tmp_path, monkeypatch)
    owner.reference_after_unload = {"fingerprints_equal": True}
    owner.phase_result = {"control_complete": True}
    invokes = []
    monkeypatch.setattr(owner.native, "call", lambda *args, **kwargs: invokes.append(args))
    monkeypatch.setattr(owner, "protected", lambda phase: True)

    def final_identity(phase):
        assert phase == "before-close"
        owner.missing_required_evidence.append({"path": "before-close.json", "error": "disk"})
        return True

    monkeypatch.setattr(owner, "identity", final_identity)
    try:
        with pytest.raises(ValueError, match="Final identity evidence"):
            owner.close_successful_control()
        assert invokes == []
        assert owner.counts["Close"] == owner.counts["Quit"] == 0
    finally:
        log.stream.close()


def test_observe_exit_records_same_generation_with_actual_exit_filetime():
    expected = {"pid": 314, "birth_filetime_100ns": "132100000000000000"}
    row = dict(expected, alive=False, exit_filetime_100ns="132100000010000000", executable=None)
    observer = types.SimpleNamespace(observe_pid=lambda pid: row)
    result = lifecycle.observe_exit(observer, expected, seconds=0)
    assert result["exited"] is True
    assert result["pid_reused"] is False
    assert result["samples"][0]["observed"] == row


def test_observe_exit_does_not_infer_exit_from_incomplete_dead_flag():
    expected = {"pid": 314, "birth_filetime_100ns": "132100000000000000"}
    row = dict(expected, alive=False, exit_filetime_100ns=None)
    observer = types.SimpleNamespace(observe_pid=lambda pid: row)
    assert lifecycle.observe_exit(observer, expected, seconds=0)["exited"] is False


def test_observe_exit_rejects_different_pid_even_when_dead():
    expected = {"pid": 314, "birth_filetime_100ns": "132100000000000000"}
    row = dict(expected, pid=315, alive=False, exit_filetime_100ns="132100000010000000")
    observer = types.SimpleNamespace(observe_pid=lambda pid: row)
    with pytest.raises(ValueError, match="different PID"):
        lifecycle.observe_exit(observer, expected, seconds=0)


def test_actual_post_close_identity_file_failure_preserves_close_and_blocks_quit(
    tmp_path, monkeypatch
):
    owner, log = make_owner(tmp_path, monkeypatch)
    owner.reference_after_unload = {"fingerprints_equal": True}
    owner.phase_result = {"control_complete": True}
    owner.process = types.SimpleNamespace(observe_window=lambda hwnd: None)
    app_unknown, doc_unknown = object(), object()
    native_invocations = []

    class Reference:
        def __init__(self, unknown, role):
            self.unknown, self.role = unknown, role

        def QueryInterface(self, iid):  # noqa: N802 - raw COM adapter boundary
            return self.unknown

        def Invoke(self, *args):  # noqa: N802 - raw COM adapter boundary
            native_invocations.append((self.role, args))
            return True if self.role == "document" else None

    owner.application = Reference(app_unknown, "application")
    owner.document = Reference(doc_unknown, "document")
    owner.application_unknown, owner.document_unknown = app_unknown, doc_unknown
    monkeypatch.setitem(
        sys.modules, "pythoncom", types.SimpleNamespace(IID_IUnknown="formal-IUnknown")
    )
    monkeypatch.setattr(owner, "protected", lambda phase: True)
    monkeypatch.setattr(lifecycle, "capture_identity", lambda *args, **kwargs: {"iunknown": {}})
    monkeypatch.setattr(
        lifecycle,
        "compare_identity",
        lambda *args, **kwargs: {"can_continue_original_chain": True, "errors": []},
    )
    actual_file = log.file
    post_close_attempts = []

    def fail_only_post_close(name, value):
        if "-post_close-" in name:
            post_close_attempts.append(name)
            raise PermissionError(13, "synthetic post_close artifact write failure")
        return actual_file(name, value)

    monkeypatch.setattr(log, "file", fail_only_post_close)
    try:
        with pytest.raises(ValueError, match="Post-close identity evidence"):
            owner.close_successful_control()
        assert len(post_close_attempts) == 3
        assert all("post_close" in row["name"] for row in owner.missing_required_evidence)
        assert all(
            row["error_type"] == "PermissionError" for row in owner.missing_required_evidence
        )
        assert owner.counts["Close"] == 1
        assert owner.counts["Quit"] == 0
        assert len(native_invocations) == 1
        assert native_invocations[0][0] == "document"
        assert owner.stage == "post_close"
        result = {
            "stage": owner.stage,
            "counts": owner.counts,
            # Simulate an incorrect upstream success flag; final validation
            # must independently downgrade it using actual owner evidence.
            "control_completed": True,
            "evidence_complete": True,
            "verification": {"status": "passed"},
            "required_evidence": [],
        }
        assert lifecycle.finalize_required_evidence(result, owner) is False
        assert result["control_completed"] is False
        assert result["evidence_complete"] is False
        assert result["verification"]["status"] == "incomplete"
        assert result["counts"]["Close"] == 1
        assert result["counts"]["Quit"] == 0
        assert result["required_evidence_validation"]["checked_pins"] == 3
    finally:
        log.stream.close()


def test_final_evidence_gate_checks_actual_pin_drift_even_with_upstream_passed(tmp_path):
    artifact = tmp_path / "identity.json"
    artifact.write_text('{"observed":true}', encoding="utf-8")
    pin = lifecycle.file_pin(artifact)
    artifact.write_text('{"observed":false}', encoding="utf-8")
    result = {
        "control_completed": True,
        "evidence_complete": True,
        "stage": "control_completed",
        "verification": {"status": "passed"},
        "required_evidence": [pin],
        "counts": {"Close": 1, "Quit": 1},
    }
    assert lifecycle.finalize_required_evidence(result) is False
    assert result["verification"]["status"] == "incomplete"
    assert result["stage"] == "required_evidence_incomplete"
    assert result["counts"] == {"Close": 1, "Quit": 1}
    assert result["required_evidence_validation"]["pin_failures"][0]["reference"] == pin


def test_final_evidence_gate_preserves_supported_success_with_exact_existing_pin(tmp_path):
    artifact = tmp_path / "identity.json"
    artifact.write_text('{"observed":true}', encoding="utf-8")
    result = {
        "control_completed": True,
        "evidence_complete": True,
        "stage": "control_completed",
        "verification": {"status": "passed"},
        "required_evidence": [lifecycle.file_pin(artifact)],
    }
    assert lifecycle.finalize_required_evidence(result) is True
    assert result["control_completed"] is True
    assert result["verification"]["status"] == "passed"
    assert result["required_evidence_validation"]["checked_pins"] == 1
