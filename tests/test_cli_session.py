"""session start | status | stop."""

from __future__ import annotations

import pytest

from xpedition_cli.cli import sessions
from xpedition_cli.session import read_state, record_native_timeout


def _launched(domain: str, ready: bool = True) -> dict:
    return {
        "started": True,
        "domain": domain,
        "pid": 4242,
        "executable": "C:/SDD_HOME/common/win64/bin/app.exe",
        "automation_ready": ready,
    }


def test_start_attaches_to_an_application_already_running(cli, adapter) -> None:
    adapter.on("attach", {"attached": True, "domain": "pcb"})
    code, payload = cli("session", "start", "--kind", "pcb")
    data = payload["data"]
    assert code == 0 and data["started"] is False and data["attached"] is True
    assert data["application"] == "Xpedition Layout" and data["project"] is None
    assert adapter.methods() == ["health", "attach"]
    assert read_state()["state"] == "attached"


def test_start_launches_one_that_is_not_running_and_opens_the_project(
    cli, adapter, project
) -> None:
    adapter.on("health", {"application_running": False, "designer_application_running": False})
    adapter.on("start", _launched("schematic"))
    adapter.on("open", {"opened": True, "domain": "schematic", "project": "Board", "prompts": []})
    code, payload = cli("session", "start", "--kind", "schematic", "--project", str(project))
    data = payload["data"]
    assert code == 0 and data["started"] is True and data["pid"] == 4242
    assert data["project"] == "Board"
    assert adapter.methods() == ["health", "start", "open"]
    # the application is running by now: open attaches, it does not start a second one
    assert adapter.last("open") == {"project": str(project), "domain": "schematic", "start": False}
    assert read_state()["state"] == "started"


def test_start_waits_for_a_slow_application_to_accept_automation(cli, adapter, monkeypatch) -> None:
    monkeypatch.setattr(sessions.time, "sleep", lambda seconds: None)
    answers = iter([False, False, True])

    def health(params):
        return {"application_running": next(answers, True), "designer_application_running": False}

    adapter.on("health", health)
    adapter.on("start", _launched("pcb", ready=False))
    code, payload = cli("session", "start", "--kind", "pcb")
    assert code == 0 and payload["data"]["started"] is True
    assert adapter.methods().count("health") == 3


def test_start_that_never_becomes_ready_times_out(cli, adapter, monkeypatch) -> None:
    monkeypatch.setattr(sessions.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sessions, "STARTUP_SECONDS", 0.0)
    adapter.on("health", {"application_running": False, "designer_application_running": False})
    adapter.on("start", _launched("pcb", ready=False))
    code, payload = cli("session", "start", "--kind", "pcb")
    assert code == 8 and payload["error"]["code"] == "E_TIMEOUT"
    assert payload["error"]["retryable"] is True


def test_a_failed_launch_is_recorded(cli, adapter) -> None:
    adapter.on("health", {"application_running": False, "designer_application_running": False})
    adapter.fail("start", "E_SERVER", "Xpedition exited during startup", {"exit_code": -1})
    code, payload = cli("session", "start", "--kind", "pcb")
    assert code == 7 and payload["error"]["code"] == "E_SERVER"
    assert read_state()["state"] == "crashed"


def test_a_stale_session_with_the_application_still_up_must_be_stopped_first(cli, adapter) -> None:
    record_native_timeout("draw")
    code, payload = cli("session", "start", "--kind", "schematic")
    assert code == 6 and payload["error"]["code"] == "E_CONFLICT"
    assert "session stop --kind schematic" in payload["error"]["details"]["hint"]


def test_a_stale_session_whose_application_is_gone_starts_fresh(cli, adapter) -> None:
    record_native_timeout("draw")
    adapter.on("health", {"application_running": False, "designer_application_running": False})
    adapter.on("start", _launched("schematic"))
    code, payload = cli("session", "start", "--kind", "schematic")
    assert code == 0 and read_state()["state"] == "started"


@pytest.mark.parametrize(
    ("kind", "suffix", "ok"),
    [("schematic", ".pcb", False), ("pcb", ".pcb", True), ("schematic", ".prj", True)],
)
def test_the_project_must_suit_the_application(cli, adapter, tmp_path, kind, suffix, ok) -> None:
    path = tmp_path / f"Board{suffix}"
    path.write_text("", encoding="utf-8")
    adapter.on("attach", {"attached": True})
    adapter.on("open", {"opened": True, "project": path.name, "prompts": []})
    code, payload = cli("session", "start", "--kind", kind, "--project", str(path))
    assert (code == 0) is ok
    if not ok:
        assert payload["error"]["code"] == "E_VALIDATION" and adapter.calls == []


def test_start_with_a_missing_project_touches_nothing(cli, adapter, tmp_path) -> None:
    code, payload = cli("session", "start", "--kind", "pcb", "--project", str(tmp_path / "No.prj"))
    assert code == 3 and adapter.calls == []


def test_status_reports_both_applications_and_the_recorded_session(cli, adapter) -> None:
    adapter.on("health", {"application_running": True, "designer_application_running": False})
    code, payload = cli("session", "status")
    data = payload["data"]
    assert (
        code == 0 and data["layout"] == {"running": True} and data["designer"] == {"running": False}
    )
    assert data["stale"] is False and data["recorded"]["state"] == "not_started"
    record_native_timeout("snapshot")
    _, payload = cli("session", "status")
    assert payload["data"]["stale"] is True
    assert payload["data"]["recorded"]["timed_out_method"] == "snapshot"


def test_status_without_an_adapter_still_answers(cli, monkeypatch) -> None:
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", "Z:/no/such/adapter.exe")
    code, payload = cli("session", "status")
    assert code == 0 and payload["data"]["xpedition"]["ready"] is False
    assert payload["data"]["layout"] == {"running": None}


def test_status_reports_a_probe_that_failed(cli, adapter) -> None:
    adapter.fail("health", "E_SERVER", "adapter crashed")
    code, payload = cli("session", "status")
    assert code == 0 and payload["data"]["probe_error"]["code"] == "E_SERVER"


def test_stop_previews_then_quits_the_one_running_application(cli, adapter) -> None:
    adapter.on("health", {"application_running": False, "designer_application_running": True})
    adapter.on("close", {"closed": True, "domain": "schematic"})
    code, payload = cli("session", "stop", "--dry-run")
    preview = payload["data"]["preview"]
    assert code == 0 and preview["domain"] == "schematic"
    assert "unsaved design work" in preview["risk"]["blast_radius"]
    token = payload["data"]["confirm_token"]
    code, payload = cli("session", "stop", "--confirm", token)
    assert code == 0 and payload["data"]["closed"] is True
    assert payload["data"]["application"] == "Xpedition Designer"
    assert adapter.last("close") == {"domain": "schematic"}


def test_stop_refuses_to_guess_when_both_run(cli, adapter) -> None:
    code, payload = cli("session", "stop", "--dry-run")
    assert code == 2 and payload["error"]["code"] == "E_USAGE"
    assert payload["error"]["details"]["running"] == ["pcb", "schematic"]


def test_stop_of_an_application_that_is_not_running_is_not_found(cli, adapter) -> None:
    adapter.on("health", {"application_running": False, "designer_application_running": True})
    code, payload = cli("session", "stop", "--kind", "pcb", "--dry-run")
    assert code == 3 and payload["error"]["code"] == "E_NOT_FOUND"
    adapter.on("health", {"application_running": False, "designer_application_running": False})
    code, payload = cli("session", "stop", "--dry-run")
    assert code == 3


def test_stop_needs_the_dry_run_first_and_a_token_bound_to_its_application(cli, adapter) -> None:
    code, payload = cli("session", "stop", "--kind", "pcb")
    assert code == 5 and payload["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    _, payload = cli("session", "stop", "--kind", "pcb", "--dry-run")
    token = payload["data"]["confirm_token"]
    code, payload = cli("session", "stop", "--kind", "schematic", "--confirm", token)
    assert code == 6 and payload["error"]["code"] == "E_CONFLICT"
    assert "close" not in adapter.methods()


def test_stop_without_a_probe_needs_the_kind(cli, adapter) -> None:
    adapter.fail("health", "E_SERVER", "adapter crashed")
    code, payload = cli("session", "stop", "--dry-run")
    assert code == 2 and "--kind" in payload["error"]["message"]
    code, payload = cli("session", "stop", "--kind", "pcb", "--dry-run")
    assert code == 0 and payload["data"]["preview"]["domain"] == "pcb"
