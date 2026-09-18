"""A timed-out native call leaves a session that must not be used as if it were fine.

After a read timed out, every later command failed with Designer refusing to open
a project it already had -- `IsProjectOpened()` had gone false with the project
still open, and the refusal's text is about scripts and GUIs. Nothing connected
that back to the timeout. The timeout is now recorded, the next task command says
so, and the refusal maps to a diagnostic that names the state it came from.
"""

from __future__ import annotations

import subprocess

import pytest

from xpedition_cli import session
from xpedition_cli.backends import native_xpedition
from xpedition_cli.errors import CLIError
from xpedition_cli.native_com_adapter import _com_error


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    binary = tmp_path / "native-adapter.exe"
    binary.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(binary))
    return monkeypatch


def time_out(monkeypatch) -> None:
    def explode(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1.0)

    monkeypatch.setattr(native_xpedition.subprocess, "run", explode)


def test_a_timeout_marks_the_session_stale_and_says_so(adapter) -> None:
    time_out(adapter)
    with pytest.raises(CLIError) as caught:
        native_xpedition.NativeBackend().invoke("snapshot", {})
    assert caught.value.code == "E_TIMEOUT"
    assert "stale" in caught.value.message
    assert session.read_state()["state"] == "stale"
    assert session.read_state()["timed_out_method"] == "snapshot"


def test_the_next_task_command_reports_the_stale_session(adapter) -> None:
    time_out(adapter)
    with pytest.raises(CLIError):
        native_xpedition.NativeBackend().invoke("snapshot", {})

    def unexpected(argv, **kwargs):
        raise AssertionError("a stale session must be reported before the adapter is run")

    adapter.setattr(native_xpedition.subprocess, "run", unexpected)
    with pytest.raises(CLIError) as caught:
        native_xpedition.NativeBackend().invoke("footprints", {})
    assert caught.value.code == "E_CONFLICT"
    assert caught.value.details["timed_out_method"] == "snapshot"
    assert "session stop" in caught.value.details["hint"]


@pytest.mark.parametrize("method", ["health", "start", "close"])
def test_recovery_methods_still_run_against_a_stale_session(adapter, method) -> None:
    time_out(adapter)
    with pytest.raises(CLIError):
        native_xpedition.NativeBackend().invoke("snapshot", {})
    ran: list[str] = []

    def fake_run(argv, **kwargs):
        ran.append(method)
        return subprocess.CompletedProcess(argv, 0, '{"ok":true,"data":{}}', "")

    adapter.setattr(native_xpedition.subprocess, "run", fake_run)
    native_xpedition.NativeBackend().invoke(method, {})
    assert ran == [method], "inspecting and restarting a stale session must stay possible"


def test_starting_a_session_clears_the_stale_mark(adapter) -> None:
    time_out(adapter)
    with pytest.raises(CLIError):
        native_xpedition.NativeBackend().invoke("snapshot", {})
    session.record_native_start({"pid": 1234, "executable": "viewdraw.exe", "domain": "schematic"})
    assert session.read_state()["state"] == "started"


def test_a_refusal_against_current_state_is_not_reported_as_a_server_fault() -> None:
    refusal = Exception(
        -2147352567,
        "发生意外。",
        (0, "Xpedition Designer", "Xpedition supports only scripts without GUI", None, 64185, -1),
        None,
    )
    error = _com_error(refusal, "open_project")
    assert error.code == "E_CONFLICT"
    assert "session" in error.details["hint"]


def test_an_ordinary_fault_is_still_a_server_fault() -> None:
    error = _com_error(Exception(-2147352571, "类型不匹配。", None, 5), "open_project")
    assert error.code == "E_SERVER"
