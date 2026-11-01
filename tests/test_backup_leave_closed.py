"""Backup end-state policy fixtures; ZIP integrity never proves native persistence."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from test_project_backup import _folder

from xpedition_cli import project_backup
from xpedition_cli.cli import project as commands
from xpedition_cli.errors import CLIError


@pytest.mark.parametrize("leave_closed", [False, True])
@pytest.mark.parametrize(
    "designer_closed,layout_closed", [(True, True), (True, False), (False, True), (False, False)]
)
def test_backup_only_reopens_a_previously_matching_designer_when_requested_policy_allows(
    tmp_path, monkeypatch, leave_closed, designer_closed, layout_closed
):
    project = _folder(tmp_path)
    archive = tmp_path / "delivery.zip"
    calls = []
    release = {
        "quiescent": True,
        "designer_closed": designer_closed,
        "layout_closed": layout_closed,
        "release_progress": {
            "designer": {"save_count": int(designer_closed), "close_count": int(designer_closed)},
            "layout": {"save_count": int(layout_closed), "close_count": int(layout_closed)},
        },
    }
    original_release = copy.deepcopy(release)

    def reopen(method, params, **kwargs):
        calls.append(method)
        assert method == "reopen_project" and params == {"project": str(project)}
        return {"reopened": True}

    def released(source, preflight):
        assert source == project
        calls.append("release_project")
        return SimpleNamespace(invoke=reopen), release

    monkeypatch.setattr(commands, "_release_for_archive", released)
    result = commands.backup(
        {"project": str(project), "output": str(archive), "leave_closed": leave_closed}
    )
    should_reopen = designer_closed and not leave_closed
    assert calls == (
        ["release_project", "reopen_project"] if should_reopen else ["release_project"]
    )
    assert result["requested_leave_closed"] is leave_closed
    assert result["closed_and_reopened"] is should_reopen
    assert result["actual_closed_without_reopen"] is (not should_reopen)
    assert result["complete"] is True and archive.is_file()
    assert result["release"] == release == original_release
    assert result["atomic_snapshot"] is False


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false"])
def test_nonboolean_leave_closed_refuses_before_release_or_archive(tmp_path, monkeypatch, value):
    project = _folder(tmp_path)
    monkeypatch.setattr(
        commands, "_release_for_archive", lambda *args: pytest.fail("release dispatched")
    )
    with pytest.raises(CLIError) as error:
        commands.backup(
            {"project": str(project), "output": str(tmp_path / "x.zip"), "leave_closed": value}
        )
    assert error.value.code == "E_VALIDATION"
    assert error.value.details["write_attempted"] is False


def test_default_backup_policy_keeps_existing_designer_reopen_behavior(tmp_path, monkeypatch):
    project = _folder(tmp_path)
    calls = []

    def reopen(method, params, **kwargs):
        calls.append(method)
        return {"reopened": True}

    monkeypatch.setattr(
        commands,
        "_release_for_archive",
        lambda *args: (
            SimpleNamespace(invoke=reopen),
            {"quiescent": True, "designer_closed": True},
        ),
    )
    result = commands.backup({"project": str(project), "output": str(tmp_path / "default.zip")})
    assert calls == ["reopen_project"]
    assert result["requested_leave_closed"] is False
    assert result["closed_and_reopened"] is True
    assert result["actual_closed_without_reopen"] is False


def test_leave_closed_preserves_release_failure_without_archive_or_extra_open(
    tmp_path, monkeypatch
):
    project = _folder(tmp_path)
    original = CLIError(
        "E_VERIFY_INCOMPLETE",
        "native Close return was not observed",
        {"release_progress": {"save_count": 1, "close_count": 1}},
    )

    def refused(*args):
        raise original

    monkeypatch.setattr(commands, "_release_for_archive", refused)
    monkeypatch.setattr(
        project_backup, "create", lambda *args, **kwargs: pytest.fail("archive should not start")
    )
    with pytest.raises(CLIError) as error:
        commands.backup(
            {"project": str(project), "output": str(tmp_path / "x.zip"), "leave_closed": True}
        )
    assert error.value is original
    assert error.value.details == {"release_progress": {"save_count": 1, "close_count": 1}}


@pytest.mark.parametrize("leave_closed", [False, True])
def test_archive_failure_keeps_actual_release_facts_and_never_recovers_with_an_open(
    tmp_path, monkeypatch, leave_closed
):
    project = _folder(tmp_path)
    release = {
        "quiescent": True,
        "designer_closed": True,
        "layout_closed": True,
        "release_progress": {
            "designer": {"save_count": 1, "close_count": 1},
            "layout": {"save_count": 1, "close_count": 1},
        },
    }
    backend = SimpleNamespace(
        invoke=lambda *args, **kwargs: pytest.fail("failure must not trigger Open")
    )
    monkeypatch.setattr(commands, "_release_for_archive", lambda *args: (backend, release))

    def fail_archive(*args, **kwargs):
        raise project_backup.BackupError("source changed during archive")

    monkeypatch.setattr(project_backup, "create", fail_archive)
    with pytest.raises(CLIError) as error:
        commands.backup(
            {
                "project": str(project),
                "output": str(tmp_path / "x.zip"),
                "leave_closed": leave_closed,
            }
        )
    assert error.value.code == "E_IO"
    assert error.value.details["backup_complete"] is False
    assert error.value.details["release"] == release
    assert error.value.details["requested_leave_closed"] is leave_closed
    assert error.value.details["actual_closed_without_reopen"] is True


@pytest.mark.parametrize("reply", [False, None, "exception"])
def test_default_reopen_failure_is_not_reported_as_a_leave_closed_success(
    tmp_path, monkeypatch, reply
):
    project = _folder(tmp_path)
    calls = []

    def reopen(method, params, **kwargs):
        calls.append(method)
        if reply == "exception":
            raise CLIError("E_NATIVE_FAILURE", "original native Open exception")
        return {"reopened": reply}

    release = {"quiescent": True, "designer_closed": True}
    monkeypatch.setattr(
        commands, "_release_for_archive", lambda *args: (SimpleNamespace(invoke=reopen), release)
    )
    with pytest.raises(CLIError) as error:
        commands.backup({"project": str(project), "output": str(tmp_path / "x.zip")})
    result = error.value.details["backup"]
    assert calls == ["reopen_project"]
    assert error.value.details["backup_complete"] is True
    assert result["closed_and_reopened"] is False
    assert result["actual_closed_without_reopen"] is False
    assert result["requested_leave_closed"] is False


@pytest.mark.parametrize("leave_closed", [False, True])
def test_public_backup_preview_freezes_end_state_policy_and_dispatches_one_release(
    tmp_path, monkeypatch, leave_closed
):
    from test_backup_preflight_public import scene

    from xpedition_cli.backends.native_xpedition import NativeBackend
    from xpedition_cli.driver import runtime

    args, report, calls = scene(tmp_path, monkeypatch)
    args += ["--leave-closed"] if leave_closed else []

    def invoke(self, method, params, **kwargs):
        calls.append((method, copy.deepcopy(params)))
        if method == "release_project_preflight":
            return copy.deepcopy(report)
        if method == "release_project":
            assert params["expected_release_preflight"] == report
            return {"quiescent": True, "designer_closed": True, "layout_closed": True}
        assert method == "reopen_project" and not leave_closed
        return {"reopened": True}

    monkeypatch.setattr(NativeBackend, "invoke", invoke)
    with runtime(state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        legacy = preview.envelope["data"]["preview"]["extensions"]["legacy_preview"]
        assert legacy["params"].get("leave_closed", False) is leave_closed
        assert all(method == "release_project_preflight" for method, _ in calls)
        applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert applied.envelope["ok"], applied.envelope
    result = applied.envelope["data"]["receipt"]["extensions"]["result"]
    assert result["requested_leave_closed"] is leave_closed
    assert result["closed_and_reopened"] is (not leave_closed)
    assert result["actual_closed_without_reopen"] is leave_closed
    assert sum(method == "release_project" for method, _ in calls) == 1
    assert sum(method == "reopen_project" for method, _ in calls) == int(not leave_closed)


@pytest.mark.parametrize("preview_leave_closed", [False, True])
def test_public_confirmation_cannot_flip_leave_closed_policy(
    tmp_path, monkeypatch, preview_leave_closed
):
    from test_backup_preflight_public import scene

    from xpedition_cli.driver import runtime

    args, _, calls = scene(tmp_path, monkeypatch)
    preview_args = [*args, *(["--leave-closed"] if preview_leave_closed else [])]
    confirm_args = [*args, *([] if preview_leave_closed else ["--leave-closed"])]
    with runtime(state_dir=tmp_path / "state") as cli:
        preview = cli.run([*preview_args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        applied = cli.run([*confirm_args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert applied.envelope["ok"] is False
    assert applied.envelope["error"]["code"] == "E_CONFLICT"
    assert "release_project" not in [method for method, _ in calls]
    assert "reopen_project" not in [method for method, _ in calls]
