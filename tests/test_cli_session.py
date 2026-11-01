"""Private handler regressions; these fakes establish no native Designer readiness."""

from __future__ import annotations

import ctypes

import pytest

from xpedition_cli import native_com_adapter, native_process, session
from xpedition_cli.backends import native_xpedition
from xpedition_cli.cli import sessions
from xpedition_cli.errors import CLIError
from xpedition_cli.session import read_state, record_native_attach, record_native_timeout


@pytest.fixture(autouse=True)
def no_real_native(monkeypatch):
    """The explicit FakeAdapter may replace transport; no real fallback is allowed."""
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Handler regression attempted a real native/COM transport")

    monkeypatch.setattr(native_xpedition.subprocess, "run", forbidden)
    monkeypatch.setattr(native_com_adapter, "_import_com", forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    yield
    assert hits == []


def _generation(domain):
    name = "viewdraw.exe" if domain == "schematic" else "ExpeditionPCB.exe"
    return {
        "pid": 4242,
        "executable": f"C:/SDD_HOME/common/win64/bin/{name}",
        "creation_time_100ns": "133123456789012345",
    }


@pytest.fixture(autouse=True)
def observed_generations(request, monkeypatch):
    """Older handler fixtures now supply actual COM/OS identities, not a PID alone."""
    if "adapter" not in request.fixturenames:
        return
    adapter = request.getfixturevalue("adapter")

    def identity(domain, pid):
        assert int(pid) == 4242
        return _generation(domain)

    class Handle:
        def __init__(self, domain, pid, expected=None):
            self.identity = identity(domain, pid)
            assert expected is None or native_process.same_identity(self.identity, expected)

        def exited(self):
            return False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(sessions, "process_identity", identity)
    monkeypatch.setattr(sessions, "ProcessHandle", Handle)
    monkeypatch.setattr(native_process, "process_identity", identity)
    monkeypatch.setattr(native_process, "ProcessHandle", Handle)

    def application(params):
        domain = params["domain"]
        opens = [
            row["params"]
            for row in adapter.calls
            if row["method"] == "open" and row["params"].get("domain") == domain
        ]
        document = {
            "open": bool(opens),
            "path": opens[-1]["project"] if opens else None,
            "status": "observed",
        }
        return {
            "application": {
                "pid": 4242,
                "public_fields": {"FullName": _generation(domain)["executable"]},
                "document": document,
            }
        }

    adapter.on("identity", application)


@pytest.fixture
def owned_close(adapter, monkeypatch):
    def establish(domain):
        identity = {
            "pid": 4242,
            "executable": "C:/CAD/app.exe",
            "creation_time_100ns": "133123456789012345",
        }
        monkeypatch.setattr(sessions, "process_identity", lambda domain, pid: identity)
        record_native_attach({"domain": domain, "pid": 4242, "process_identity": identity})
        adapter.on(
            "identity",
            {
                "application": {
                    "pid": 4242,
                    "document": {"open": False, "status": "observed", "path": None},
                }
            },
        )
        return identity

    return establish


def _launched(domain: str, ready: bool = True) -> dict:
    identity = _generation(domain)
    return {
        "started": True,
        "domain": domain,
        "pid": 4242,
        "executable": identity["executable"],
        "launcher_pid": 4242,
        "launcher_identity": identity,
        "startup_seed": {
            "version": native_process.STARTUP_LINEAGE_VERSION,
            "domain": domain,
            "launcher_identity": identity,
            "candidates": [{"process_chain": [identity], "parent_edges": []}],
        },
        "automation_ready": ready,
    }


def test_start_refuses_existing_application_and_attach_is_explicit(cli, adapter) -> None:
    adapter.on("attach", {"attached": True, "domain": "pcb"})
    code, payload = cli("session", "start", "--kind", "pcb")
    assert code == 6 and payload["error"]["code"] == "E_CONFLICT"
    assert adapter.methods() == ["health"]
    code, payload = cli("session", "attach", "--kind", "pcb", "--pid", "4242")
    assert code == 0 and payload["data"]["started"] is False and payload["data"]["attached"] is True
    assert read_state()["state"] == "attached"


def test_designer_start_with_project_refuses_without_changing_state(cli, adapter, project):
    record_native_attach({"domain": "pcb", "pid": 4242, "process_identity": _generation("pcb")})
    before = session._state_path().read_bytes()
    code, payload = cli("session", "start", "--kind", "schematic", "--project", str(project))
    assert code != 0 and payload["error"]["code"] == "E_UNSUPPORTED"
    assert payload["error"]["details"]["write_attempted"] is False
    assert adapter.calls == []
    assert session._state_path().read_bytes() == before


def test_layout_start_launches_one_that_is_not_running_and_opens_the_board(cli, adapter, tmp_path):
    board = tmp_path / "Board.pcb"
    board.write_bytes(b"fake Layout document; no native qualification")
    adapter.on("health", {"application_running": False, "designer_application_running": False})
    adapter.on("start", _launched("pcb"))
    adapter.on("open", {"opened": True, "domain": "pcb", "project": "Board", "prompts": []})
    code, payload = cli("session", "start", "--kind", "pcb", "--project", str(board))
    data = payload["data"]
    assert code == 0 and data["started"] is True and data["pid"] == 4242
    assert data["project"] == "Board"
    assert adapter.methods() == [
        "health",
        "start",
        "identity",
        "identity",
        "open",
        "identity",
        "identity",
    ]
    # the application is running by now: open attaches, it does not start a second one
    assert adapter.last("open") == {
        "project": str(board),
        "domain": "pcb",
        "start": False,
        "pid": 4242,
    }
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
    assert read_state()["state"] == "startup_incomplete"
    assert payload["error"]["details"]["write_attempted"] is True


@pytest.mark.parametrize("kind", ["schematic", "pcb"])
def test_stale_session_with_application_up_has_no_implicit_designer_fallback(cli, adapter, kind):
    record_native_attach({"domain": kind, "pid": 4242, "process_identity": _generation(kind)})
    record_native_timeout("draw")
    before = session._state_path().read_bytes()
    code, payload = cli("session", "start", "--kind", kind)
    assert code != 0
    if kind == "schematic":
        assert payload["error"]["code"] == "E_CONFIG"
        assert payload["error"]["details"]["write_attempted"] is False
        assert adapter.calls == []
    else:
        assert payload["error"]["code"] == "E_CONFLICT"
        assert "session close --kind pcb" in payload["error"]["details"]["hint"]
        assert adapter.methods() == ["health"]
    assert session._state_path().read_bytes() == before


@pytest.mark.parametrize("kind", ["schematic", "pcb"])
def test_only_layout_stale_session_can_use_legacy_fresh_start(cli, adapter, kind):
    record_native_attach({"domain": kind, "pid": 4242, "process_identity": _generation(kind)})
    record_native_timeout("draw")
    before = session._state_path().read_bytes()
    adapter.on("health", {"application_running": False, "designer_application_running": False})
    adapter.on("start", _launched(kind))
    code, payload = cli("session", "start", "--kind", kind)
    if kind == "schematic":
        assert code != 0 and payload["error"]["code"] == "E_CONFIG"
        assert payload["error"]["details"]["write_attempted"] is False
        assert adapter.calls == [] and session._state_path().read_bytes() == before
    else:
        assert code == 0 and read_state()["state"] == "started"
        assert adapter.methods().count("start") == 1


def test_an_operation_id_alone_cannot_authorize_stale_designer_restart(adapter):
    record_native_attach(
        {"domain": "schematic", "pid": 4242, "process_identity": _generation("schematic")}
    )
    record_native_timeout("draw")
    before = session._state_path().read_bytes()
    adapter.on("health", {"application_running": False, "designer_application_running": False})
    with pytest.raises(CLIError) as caught:
        sessions.start({"kind": "schematic", "_operation_id": "op-not-a-typed-start-request"})
    assert caught.value.code == "E_HUMAN_REQUIRED"
    assert caught.value.details["write_attempted"] is False
    assert adapter.calls == [] and session._state_path().read_bytes() == before


@pytest.mark.parametrize(
    ("kind", "suffix", "error"),
    [
        ("schematic", ".pcb", "E_UNSUPPORTED"),
        ("pcb", ".pcb", None),
        ("schematic", ".prj", "E_UNSUPPORTED"),
        ("pcb", ".txt", "E_VALIDATION"),
    ],
)
def test_project_selector_obeys_dedicated_designer_and_layout_contract(
    cli, adapter, tmp_path, kind, suffix, error
):
    path = tmp_path / f"Board{suffix}"
    path.write_text("", encoding="utf-8")
    adapter.on("health", {"application_running": False, "designer_application_running": False})
    adapter.on("start", _launched(kind))
    adapter.on("open", {"opened": True, "project": path.name, "prompts": []})
    code, payload = cli("session", "start", "--kind", kind, "--project", str(path))
    assert (code == 0) is (error is None)
    if error is not None:
        assert payload["error"]["code"] == error and adapter.calls == []


def test_start_with_a_missing_project_touches_nothing(cli, adapter, tmp_path) -> None:
    code, payload = cli("session", "start", "--kind", "pcb", "--project", str(tmp_path / "No.prj"))
    assert code == 3 and adapter.calls == []


def test_status_reports_layout_and_recorded_metadata_without_designer_discovery(cli, adapter):
    adapter.on("health", {"application_running": True, "designer_application_running": False})
    code, payload = cli("session", "inspect")
    data = payload["data"]
    assert code == 0 and data["layout"]["running"] is True and data["designer"]["running"] is None
    assert data["designer"]["com_probe_skipped"] == "no recorded dedicated Designer owner"
    assert all(row["params"].get("domain") == "pcb" for row in adapter.calls)
    assert data["stale"] is False and data["recorded"]["state"] == "not_started"
    record_native_timeout("snapshot")
    _, payload = cli("session", "inspect")
    assert payload["data"]["stale"] is True
    assert payload["data"]["recorded"]["timed_out_method"] == "snapshot"


def test_status_without_an_adapter_still_answers(cli, monkeypatch) -> None:
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", "Z:/no/such/adapter.exe")
    code, payload = cli("session", "inspect")
    assert code == 0 and payload["data"]["xpedition"]["ready"] is False
    assert payload["data"]["layout"] == {"running": None}


def test_status_reports_a_probe_that_failed(cli, adapter) -> None:
    adapter.fail("health", "E_SERVER", "adapter crashed")
    code, payload = cli("session", "inspect")
    assert code == 0 and payload["data"]["probe_error"]["code"] == "E_SERVER"


def test_layout_stop_previews_then_quits_its_owned_application(cli, adapter, owned_close):
    identity = owned_close("pcb")
    adapter.on("health", {"application_running": True, "designer_application_running": True})
    adapter.on("close", {"closed": True, "domain": "pcb", "process_absence_observed": True})
    code, payload = cli("session", "close", "--pid", "4242", "--dry-run")
    preview = payload["data"]["preview"]
    assert code == 0 and preview["domain"] == "pcb"
    assert "document is confirmed closed" in preview["risk"]["blast_radius"]
    token = payload["data"]["confirm_token"]
    code, payload = cli("session", "close", "--pid", "4242", "--confirm", token)
    assert code == 0 and payload["data"]["closed"] is True
    assert payload["data"]["application"] == "Xpedition Layout"
    assert adapter.last("close") == {
        "domain": "pcb",
        "pid": 4242,
        "expected_process_identity": identity,
        "save_project": None,
        "exit_wait_seconds": sessions.EXIT_OBSERVATION_SECONDS,
    }
    assert read_state() == {}


def test_stop_refuses_to_guess_a_designer_without_explicit_kind_and_owner(
    cli, adapter, owned_close
):
    owned_close("schematic")
    before = session._state_path().read_bytes()
    code, payload = cli("session", "close", "--pid", "4242", "--dry-run")
    assert code == 2 and payload["error"]["code"] == "E_USAGE"
    assert "--kind schematic" in payload["error"]["message"]
    assert adapter.calls == [] and session._state_path().read_bytes() == before
    code, payload = cli("session", "close", "--kind", "schematic", "--pid", "4242", "--dry-run")
    assert code != 0 and payload["error"]["code"] == "E_VALIDATION"
    assert adapter.calls == [] and session._state_path().read_bytes() == before


def test_stop_of_an_application_that_is_not_running_is_not_found(cli, adapter) -> None:
    adapter.on("health", {"application_running": False, "designer_application_running": True})
    code, payload = cli("session", "close", "--pid", "4242", "--kind", "pcb", "--dry-run")
    assert code == 3 and payload["error"]["code"] == "E_NOT_FOUND"
    adapter.on("health", {"application_running": False, "designer_application_running": False})
    code, payload = cli("session", "close", "--pid", "4242", "--dry-run")
    assert code == 3


def test_stop_needs_the_dry_run_first_and_a_token_bound_to_its_application(
    cli, adapter, owned_close
) -> None:
    owned_close("pcb")
    code, payload = cli("session", "close", "--pid", "4242", "--kind", "pcb")
    assert code == 5 and payload["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    _, payload = cli("session", "close", "--pid", "4242", "--kind", "pcb", "--dry-run")
    token = payload["data"]["confirm_token"]
    code, payload = cli(
        "session", "close", "--pid", "4242", "--kind", "schematic", "--confirm", token
    )
    assert code == 6 and payload["error"]["code"] == "E_CONFLICT"
    assert "close" not in adapter.methods()


def test_stop_without_a_probe_needs_the_kind(cli, adapter, owned_close) -> None:
    owned_close("pcb")
    adapter.fail("health", "E_SERVER", "adapter crashed")
    code, payload = cli("session", "close", "--pid", "4242", "--dry-run")
    assert code == 2 and "--kind" in payload["error"]["message"]
    code, payload = cli("session", "close", "--pid", "4242", "--kind", "pcb", "--dry-run")
    assert code == 0 and payload["data"]["preview"]["domain"] == "pcb"
