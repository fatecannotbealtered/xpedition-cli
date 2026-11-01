"""A timed-out native call leaves a session that must not be used as if it were fine.

After a read timed out, every later command failed with Designer refusing to open
a project it already had -- `IsProjectOpened()` had gone false with the project
still open, and the refusal's text is about scripts and GUIs. Nothing connected
that back to the timeout. The timeout is now recorded, the next task command says
so, and the refusal maps to a diagnostic that names the state it came from.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from test_cli_session import (
    no_real_native,  # noqa: F401 - hard fallback guard, not CAD qualification
)

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


@pytest.mark.parametrize("method", ["health", "identity"])
def test_pure_observation_timeout_does_not_mark_session_stale(adapter, method):
    time_out(adapter)
    with pytest.raises(CLIError) as caught:
        native_xpedition.NativeBackend().invoke(method, {})
    assert caught.value.details["native_query_only"] is True
    assert session.read_state().get("state") != "stale"


def test_explicit_attachment_recovers_only_a_pure_read_timeout(adapter):
    session.record_native_timeout("health")
    native_xpedition._refuse_a_stale_session("identity")
    native_xpedition._refuse_a_stale_session("attach")
    session.record_native_timeout("apply_batch")
    with pytest.raises(CLIError):
        native_xpedition._refuse_a_stale_session("attach")


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
    assert "session close" in caught.value.details["hint"]


@pytest.mark.parametrize("method", ["health", "identity", "start", "close"])
def test_recovery_methods_still_run_against_a_stale_session(adapter, method) -> None:
    time_out(adapter)
    with pytest.raises(CLIError):
        native_xpedition.NativeBackend().invoke("snapshot", {})
    ran: list[str] = []

    def fake_run(argv, **kwargs):
        ran.append(method)
        return subprocess.CompletedProcess(argv, 0, '{"ok":true,"data":{}}', "")

    adapter.setattr(native_xpedition.subprocess, "run", fake_run)
    native_xpedition.NativeBackend().invoke(method, {"domain": "pcb"})
    assert ran == [method], (
        "Explicit Layout recovery keeps its fake transport; no Designer takeover"
    )


def test_starting_a_session_clears_the_stale_mark(adapter) -> None:
    """Persistence helper only; this is not evidence of a real Designer start."""
    time_out(adapter)
    with pytest.raises(CLIError):
        native_xpedition.NativeBackend().invoke("snapshot", {})
    session.record_native_start({"pid": 1234, "executable": "viewdraw.exe", "domain": "schematic"})
    assert session.read_state()["state"] == "started"


def test_reconciliation_reads_are_allowed_but_do_not_clear_the_stale_write_guard(adapter):
    session.record_native_timeout("apply_batch")
    adapter.setenv("XPEDITION_OBSERVE_ONLY", "1")
    adapter.setenv("XPEDITION_EXPECTED_PID", "42")
    adapter.setenv("XPEDITION_REQUIRE_EXISTING", "1")
    calls = []

    def observed(argv, **kwargs):
        calls.append(kwargs["input"])
        return subprocess.CompletedProcess(argv, 0, '{"ok":true,"data":{"fixture":true}}', "")

    adapter.setattr(native_xpedition.subprocess, "run", observed)
    native_xpedition.NativeBackend().invoke("identity", {})
    native_xpedition.NativeBackend().invoke(
        "snapshot",
        {"project": "owned.pcb", "observe_only": True, "start": False, "pid": 42},
    )
    with pytest.raises(CLIError) as caught:
        native_xpedition.NativeBackend().invoke("apply_batch", {})
    assert caught.value.code == "E_CONFLICT"
    assert len(calls) == 2
    assert session.read_state()["state"] == "stale"


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


def test_public_live_geometry_observes_a_stale_write_without_clearing_it(adapter, tmp_path):
    from test_driver_v2 import _scope

    from xpedition_cli.driver import Adapter, runtime

    _scope(tmp_path, adapter)
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic")
    session.record_native_timeout("refresh_cells")
    before = session.read_state()
    calls = []

    def observed(argv, **kwargs):
        request = json.loads(kwargs["input"])
        calls.append(request)
        assert request["method"] == "board_info"
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "pcb": str(board),
                        "observation": {
                            "geometry": {},
                            "persistence": "unknown",
                            "engineering_write_dispatched": False,
                        },
                    },
                }
            ),
            "",
        )

    adapter.setattr(native_xpedition.subprocess, "run", observed)
    with runtime(
        Adapter(native_xpedition.NativeBackend()), state_dir=tmp_path / "operations"
    ) as cli:
        result = cli.run(
            ["pcb", "inspect", "--project", str(board), "--live-geometry", "--pid", "42"]
        )
    assert result.envelope["ok"], result.envelope
    assert calls[0]["params"] == {
        "project": str(board),
        "geometry": True,
        "start": False,
        "pid": 42,
    }
    assert session.read_state() == before
    with pytest.raises(CLIError):
        native_xpedition.NativeBackend().invoke("apply_batch", {})


@pytest.mark.parametrize("previous_stale", [False, True])
def test_live_geometry_timeout_is_only_a_read_and_preserves_previous_state(adapter, previous_stale):
    adapter.setenv("XPEDITION_OBSERVE_ONLY", "1")
    if previous_stale:
        session.record_native_timeout("refresh_cells")
    before = session.read_state()
    time_out(adapter)
    with pytest.raises(CLIError) as caught:
        native_xpedition.NativeBackend().invoke(
            "board_info", {"geometry": True, "start": False, "pid": 42}, timeout_seconds=1
        )
    assert caught.value.code == "E_TIMEOUT"
    assert caught.value.details["native_query_only"] is True
    assert caught.value.details["write_attempted"] is False
    assert session.read_state() == before


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"geometry": True},
        {"geometry": True, "start": True, "pid": 42},
        {"geometry": True, "start": False, "pid": 0},
    ],
)
def test_stale_geometry_exception_does_not_allow_ordinary_opening_inspection(adapter, params):
    adapter.setenv("XPEDITION_OBSERVE_ONLY", "1")
    session.record_native_timeout("refresh_cells")
    adapter.setattr(
        native_xpedition.subprocess, "run", lambda *a, **k: pytest.fail("must not dispatch")
    )
    with pytest.raises(CLIError) as caught:
        native_xpedition.NativeBackend().invoke("board_info", params)
    assert caught.value.code == "E_CONFLICT"
