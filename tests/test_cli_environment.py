"""context, doctor, reference, changelog, version."""

from __future__ import annotations

from xpedition_cli import __version__
from xpedition_cli.backends import native_xpedition
from xpedition_cli.session import record_native_timeout


def test_context_reports_the_installation_and_the_project(cli, adapter, project) -> None:
    code, payload = cli("context", "--project", str(project))
    data = payload["data"]
    assert code == 0 and data["version"] == __version__
    assert data["xpedition"]["ready"] is True and data["xpedition"]["fix"] is None
    assert data["project"] == {"path": str(project), "exists": True}
    assert data["credentials"]["managed_by"] == "Xpedition licensing"
    assert "config.directory" in data["_untrusted"]
    assert adapter.calls == []  # context never starts a subprocess


def test_context_without_xpedition_says_how_to_fix_it(cli, monkeypatch) -> None:
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", "Z:/no/such/adapter.exe")
    code, payload = cli("context")
    xpedition = payload["data"]["xpedition"]
    assert code == 0 and xpedition["ready"] is False
    assert xpedition["reason"] == "configured native COM adapter was not found"
    assert "XPEDITION_NATIVE_COMMAND" in xpedition["fix"]


def test_doctor_passes_when_both_applications_run(cli, adapter, project) -> None:
    code, payload = cli("doctor", "--project", str(project))
    checks = {check["check"]: check for check in payload["data"]["checks"]}
    assert code == 0
    assert checks["xpedition"]["status"] == "pass"
    assert checks["applications"]["status"] == "pass"
    assert checks["applications"]["message"] == "running: Layout, Designer"
    assert (
        checks["project"]["status"] == "pass" and "a board design" in checks["project"]["message"]
    )
    assert checks["release_readiness"]["status"] in {"pass", "warn"}


def test_doctor_names_the_application_a_task_still_needs(cli, adapter) -> None:
    adapter.on("health", {"application_running": False, "designer_application_running": True})
    _, payload = cli("doctor")
    applications = next(c for c in payload["data"]["checks"] if c["check"] == "applications")
    assert applications["status"] == "warn" and applications["message"] == "running: Designer"
    assert "session start --kind pcb" in applications["fix"]


def test_doctor_reports_a_probe_that_failed_without_failing_itself(cli, adapter) -> None:
    adapter.fail("health", "E_SERVER", "adapter crashed")
    code, payload = cli("doctor")
    applications = next(c for c in payload["data"]["checks"] if c["check"] == "applications")
    assert code == 0 and applications["status"] == "warn"
    assert applications["message"].startswith("not probed:")


def test_doctor_reports_a_stale_session_and_the_way_out(cli, adapter) -> None:
    record_native_timeout("draw")
    _, payload = cli("doctor")
    session = next(c for c in payload["data"]["checks"] if c["check"] == "session")
    assert session["status"] == "fail" and "draw" in session["message"]
    assert session["fix"] == "session stop, then session start"


def test_doctor_without_an_adapter_fails_the_xpedition_check(cli, monkeypatch) -> None:
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", "Z:/no/such/adapter.exe")

    def forbidden(*args, **kwargs):
        raise AssertionError("no adapter to call")

    monkeypatch.setattr(native_xpedition.subprocess, "run", forbidden)
    code, payload = cli("doctor")
    checks = {check["check"]: check for check in payload["data"]["checks"]}
    assert code == 0 and checks["xpedition"]["status"] == "fail"
    assert checks["applications"]["message"].startswith("not probed")


def test_doctor_checks_the_project_file(cli, adapter, tmp_path) -> None:
    missing = tmp_path / "Missing.prj"
    _, payload = cli("doctor", "--project", str(missing))
    check = next(c for c in payload["data"]["checks"] if c["check"] == "project")
    assert check["status"] == "fail" and "does not exist" in check["message"]
    odd = tmp_path / "board.json"
    odd.write_text("{}", encoding="utf-8")
    _, payload = cli("doctor", "--project", str(odd))
    check = next(c for c in payload["data"]["checks"] if c["check"] == "project")
    assert check["status"] == "fail" and check["fix"] == "pass the project's .prj"
    folder = tmp_path / "项目"
    folder.mkdir()
    chinese = folder / "B.prj"
    chinese.write_text("", encoding="utf-8")
    _, payload = cli("doctor", "--project", str(chinese))
    check = next(c for c in payload["data"]["checks"] if c["check"] == "project")
    assert check["status"] == "fail" and "ASCII" in check["fix"]


def test_reference_selects_one_command_one_domain_or_one_schema(cli) -> None:
    code, payload = cli("reference", "--command", "pcb route")
    data = payload["data"]
    assert code == 0 and [c["path"] for c in data["commands"]] == ["pcb route"]
    assert set(data["schemas"]) == {"pcb_route", "pcb_route_preview"}
    assert data["selection"]["kind"] == "command"
    code, payload = cli("reference", "--domain", "bom")
    assert [c["path"] for c in payload["data"]["commands"]] == ["bom export", "bom check"]
    code, payload = cli("reference", "--schema", "pcb_check")
    assert payload["data"]["commands"] == [] and list(payload["data"]["schemas"]) == ["pcb_check"]


def test_reference_says_when_a_selection_names_nothing(cli) -> None:
    code, payload = cli("reference", "--command", "pcb drc")
    assert code == 3 and payload["error"]["code"] == "E_NOT_FOUND"
    code, payload = cli("reference", "--domain", "agent")
    assert code == 3


def test_changelog_filters_by_version(cli) -> None:
    code, payload = cli("changelog")
    versions = [entry["version"] for entry in payload["data"]["entries"]]
    assert code == 0 and "1.0.0" in versions
    code, payload = cli("changelog", "--since", "1.0.0")
    assert all(entry["version"] != "1.0.0" for entry in payload["data"]["entries"])
    assert payload["data"]["since"] == "1.0.0"
