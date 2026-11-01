"""Public backup previews must expose and bind the actual read-only release gate."""

from __future__ import annotations

import copy
import json

import pytest

from xpedition_cli import native_process
from xpedition_cli.backends.native_xpedition import NativeBackend
from xpedition_cli.driver import runtime
from xpedition_cli.errors import CLIError


def scope(tmp_path, monkeypatch):
    from xpedition_cli.audit import config_dir

    path = tmp_path / "scope.json"
    path.write_text(
        json.dumps(
            {
                "scope_id": "backup-preflight-offline-fixture",
                "tool": "xpedition-cli",
                "granted_by": "offline test fixture",
                "granted_at": "2026-10-06T00:00:00Z",
                "expires_at": None,
                "effects": ["observe", "session", "artifact", "persist", "change"],
                "dangerous": False,
                "roots": [str(tmp_path), str(config_dir())],
                "outputs": [str(tmp_path)],
                "destinations": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("XPEDITION_CLI_SCOPE_FILE", str(path))


def scene(tmp_path, monkeypatch):
    scope(tmp_path, monkeypatch)
    folder = tmp_path / "owned"
    folder.mkdir()
    project = folder / "RC.prj"
    project.write_bytes(b"synthetic project; no native database")
    report = {
        "project": str(project),
        "board": None,
        "library_state": {"status": "fixture_no_library_process"},
        "release_preflight": {
            name: {"processes": [], "application": None, "document": None}
            for name in ("library", "schematic", "pcb")
        },
        "read_only": True,
        "save_count": 0,
        "close_count": 0,
    }
    calls = []
    # This suite exercises the retained owned-native release route. An empty
    # inventory now correctly uses file-only release without an adapter RPC.
    monkeypatch.setattr(
        native_process,
        "processes",
        lambda domain: [{"pid": 4242, "fixture": True}] if domain == "schematic" else [],
    )
    monkeypatch.setattr(NativeBackend, "status", lambda self: {"available": True})

    def invoke(self, method, params, **kwargs):
        calls.append((method, copy.deepcopy(params)))
        if method == "release_project_preflight":
            return copy.deepcopy(report)
        if method == "release_project":
            assert params["expected_release_preflight"] == report
            return {"quiescent": True, "designer_closed": False, "layout_closed": False}
        raise AssertionError(f"Unexpected native method {method}")

    monkeypatch.setattr(NativeBackend, "invoke", invoke)
    args = ["project", "backup", "--project", str(project), "--output", str(tmp_path / "all.zip")]
    return args, report, calls


def test_public_preview_reports_read_only_release_and_no_save_close(tmp_path, monkeypatch):
    args, report, calls = scene(tmp_path, monkeypatch)
    with runtime(state_dir=tmp_path / "state") as cli:
        result = cli.run([*args, "--dry-run"])
    assert result.envelope["ok"], result.envelope
    preview = result.envelope["data"]["preview"]
    assert preview["extensions"]["legacy_preview"]["release_plan"] == report
    assert preview["extensions"]["legacy_preview"]["native_observed"] is True
    assert preview["base_snapshot"]["extensions"]["facts"]["release_preflight"] == report
    assert all(method == "release_project_preflight" for method, _ in calls)
    assert not (tmp_path / "all.zip").exists()


def test_unreadable_layout_refuses_public_preview_before_confirmation(tmp_path, monkeypatch):
    args, _, calls = scene(tmp_path, monkeypatch)

    def blocked(self, method, params, **kwargs):
        calls.append((method, params))
        assert method == "release_project_preflight"
        raise CLIError(
            "E_HUMAN_REQUIRED",
            "Layout document cannot be observed",
            {"write_attempted": False, "save_count": 0, "close_count": 0},
        )

    monkeypatch.setattr(NativeBackend, "invoke", blocked)
    with runtime(state_dir=tmp_path / "state") as cli:
        result = cli.run([*args, "--dry-run"])
    assert not result.envelope["ok"]
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert result.envelope["error"]["details"]["write_attempted"] is False
    assert all(method == "release_project_preflight" for method, _ in calls)


def test_process_generation_drift_refuses_before_public_release(tmp_path, monkeypatch):
    args, report, calls = scene(tmp_path, monkeypatch)
    with runtime(state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run"])
        assert planned.envelope["ok"], planned.envelope
        report["release_preflight"]["pcb"]["processes"] = [
            {"pid": 123, "birth_filetime_100ns": "200", "executable": "fixture"}
        ]
        result = cli.run([*args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert result.envelope["error"]["code"] == "E_CONFLICT", result.envelope
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert "release_project" not in [method for method, _ in calls]
    assert not (tmp_path / "all.zip").exists()


def test_successful_public_backup_forwards_frozen_dependencies_and_cannot_replay(
    tmp_path, monkeypatch
):
    args, _, calls = scene(tmp_path, monkeypatch)
    with runtime(state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run"])
        token = planned.envelope["data"]["confirm_token"]
        result = cli.run([*args, "--confirm", token])
        assert (tmp_path / "all.zip").is_file(), result.envelope
        before = [method for method, _ in calls].count("release_project")
        repeated = cli.run([*args, "--confirm", token])
    assert before == 1
    assert [method for method, _ in calls].count("release_project") == 1
    assert not repeated.envelope["ok"]


@pytest.mark.parametrize("after_observer_fails", [False, True])
def test_failed_release_retains_acknowledged_save_and_unknown_persistence(
    tmp_path, monkeypatch, after_observer_fails
):
    args, report, calls = scene(tmp_path, monkeypatch)
    dispatched = False
    progress = {
        "designer": {"save_count": 0, "close_count": 0},
        "layout": {
            "save_count": 1,
            "close_count": 1,
            "save_returned": True,
            "save_acknowledged": True,
            "closed_observed": True,
        },
        "designer_closed": False,
        "layout_closed": True,
    }

    def incomplete(self, method, params, **kwargs):
        nonlocal dispatched
        calls.append((method, copy.deepcopy(params)))
        if method == "release_project_preflight":
            if dispatched and after_observer_fails:
                raise CLIError("E_HUMAN_REQUIRED", "Post-close identity unreadable")
            return copy.deepcopy(report)
        assert method == "release_project"
        dispatched = True
        raise CLIError(
            "E_HUMAN_REQUIRED",
            "Release verification incomplete after actual Save/Close",
            {"release_progress": progress, "write_attempted": True},
        )

    monkeypatch.setattr(NativeBackend, "invoke", incomplete)
    with runtime(state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
    receipt = result.envelope["error"]["details"]["receipt"]
    assert receipt["outcome"]["persistence"] == "unknown"
    assert receipt["extensions"]["handler_error"]["details"]["release_progress"] == progress
    if after_observer_fails:
        assert receipt["extensions"]["save_acknowledged"] is True
    assert [method for method, _ in calls].count("release_project") == 1
    assert not (tmp_path / "all.zip").exists()


@pytest.mark.parametrize("field,value", [("read_only", 1), ("save_count", False)])
def test_malformed_release_evidence_is_not_a_valid_public_plan(tmp_path, monkeypatch, field, value):
    args, report, calls = scene(tmp_path, monkeypatch)
    report[field] = value
    with runtime(state_dir=tmp_path / "state") as cli:
        result = cli.run([*args, "--dry-run"])
    assert result.envelope["error"]["code"] == "E_HUMAN_REQUIRED", result.envelope
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert "release_project" not in [method for method, _ in calls]


@pytest.mark.parametrize("reopen_failure", ["false", "exception"])
def test_completed_archive_and_release_survive_public_reopen_failure(
    tmp_path, monkeypatch, reopen_failure
):
    import zipfile

    args, report, calls = scene(tmp_path, monkeypatch)
    progress = {
        "designer": {
            "save_count": 1,
            "close_count": 1,
            "save_acknowledged": True,
            "closed_observed": True,
        },
        "layout": {"save_count": 0, "close_count": 0},
        "designer_closed": True,
        "layout_closed": False,
    }
    released = {
        "quiescent": True,
        "designer_closed": True,
        "layout_closed": False,
        "release_progress": progress,
    }
    native_error = {"stage": "reopen_project", "native_reply": "retained-original-error"}

    def invoke(self, method, params, **kwargs):
        calls.append((method, copy.deepcopy(params)))
        if method == "release_project_preflight":
            return copy.deepcopy(report)
        if method == "release_project":
            return copy.deepcopy(released)
        assert method == "reopen_project"
        if reopen_failure == "false":
            return {"reopened": False, "reason": "fixture-refused"}
        raise CLIError("E_NATIVE_FAILURE", "Original reopen failure", native_error)

    monkeypatch.setattr(NativeBackend, "invoke", invoke)
    with runtime(state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"]
    archive = tmp_path / "all.zip"
    assert archive.is_file()
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        assert "RC.prj" in bundle.namelist()
    receipt = result.envelope["error"]["details"]["receipt"]
    details = receipt["extensions"]["handler_error"]["details"]
    assert details["backup_complete"] is True
    assert details["backup"]["backup"] == str(archive)
    assert details["backup"]["complete"] is True
    assert details["backup"]["closed_and_reopened"] is False
    assert details["backup"]["release"] == details["release"] == released
    assert receipt["outcome"]["persistence"] == "unknown"
    if reopen_failure == "false":
        assert details["reopen_error"]["code"] == "E_VERIFY_INCOMPLETE"
        assert details["reopen"] == {"reopened": False, "reason": "fixture-refused"}
    else:
        assert details["reopen_error"]["code"] == "E_NATIVE_FAILURE"
        assert details["reopen_error"]["message"] == "Original reopen failure"
        assert details["reopen_error"]["details"] == native_error
    assert [method for method, _ in calls].count("release_project") == 1
    assert [method for method, _ in calls].count("reopen_project") == 1


def test_nested_completed_backup_retains_release_progress():
    from xpedition_cli.driver import _backup_release_progress

    progress = {"designer": {"save_count": 1, "close_count": 1, "save_acknowledged": True}}
    error = CLIError(
        "E_HUMAN_REQUIRED",
        "Reopen failed",
        {"backup_complete": True, "backup": {"release": {"release_progress": progress}}},
    )
    assert _backup_release_progress(None, error) == progress


def test_lost_release_reply_cannot_claim_unchanged_from_identical_public_snapshot(
    tmp_path, monkeypatch
):
    args, report, calls = scene(tmp_path, monkeypatch)

    def lost(self, method, params, **kwargs):
        calls.append((method, copy.deepcopy(params)))
        if method == "release_project_preflight":
            return copy.deepcopy(report)  # same readable identity is not Save absence evidence
        assert method == "release_project"
        raise CLIError("E_TIMEOUT", "Release reply lost", {"method": "release_project"})

    monkeypatch.setattr(NativeBackend, "invoke", lost)
    with runtime(state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
    receipt = result.envelope["error"]["details"]["receipt"]
    assert receipt["outcome"]["persistence"] == "unknown"
    assert receipt["extensions"]["handler_error"]["code"] == "E_TIMEOUT"
    assert [method for method, _ in calls].count("release_project") == 1
    assert "reopen_project" not in [method for method, _ in calls]
    assert not (tmp_path / "all.zip").exists()


def test_explicit_native_preflight_refusal_after_confirm_remains_not_started(tmp_path, monkeypatch):
    args, report, calls = scene(tmp_path, monkeypatch)

    def refused(self, method, params, **kwargs):
        calls.append((method, copy.deepcopy(params)))
        if method == "release_project_preflight":
            return copy.deepcopy(report)
        assert method == "release_project"
        raise CLIError(
            "E_CONFLICT",
            "Target drifted before Save",
            {"write_attempted": False, "save_count": 0, "close_count": 0},
        )

    monkeypatch.setattr(NativeBackend, "invoke", refused)
    with runtime(state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
    details = result.envelope["error"]["details"]
    assert details["dispatch_state"] == "not_started"
    assert details["write_attempted"] is False
    assert details["save_count"] == details["close_count"] == 0
    assert "reopen_project" not in [method for method, _ in calls]
    assert not (tmp_path / "all.zip").exists()
