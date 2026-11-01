"""No live CAD: generation ownership, explicit save, one Quit, OS-only completion."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as N
from xpedition_cli import native_process as P
from xpedition_cli import session
from xpedition_cli.cli import sessions as S
from xpedition_cli.errors import CLIError

IDENTITY = {
    "pid": 4242,
    "executable": "C:/CAD/ExpeditionPCB.exe",
    "creation_time_100ns": "133123456789012345",
}


@pytest.fixture
def owned(monkeypatch):
    provenance = {
        "version": P.STARTUP_LINEAGE_VERSION,
        "domain": "pcb",
        "launcher_identity": dict(IDENTITY),
        "process_chain": [dict(IDENTITY)],
        "parent_edges": [],
        "ownership": "proven_this_start",
        "source": "pure single-generation original-start fixture",
    }
    assert P.owned_startup_launchers(provenance, IDENTITY) == []
    session.record_native_attach(
        {
            "domain": "pcb",
            "pid": 4242,
            "process_identity": IDENTITY,
            "startup_provenance": provenance,
        }
    )
    document = {"open": False, "path": None, "status": "observed"}
    replies = {"closed": True, "process_absence_observed": True}
    calls = []

    class Backend:
        def invoke(self, method, params, **kwargs):
            calls.append((method, params))
            if method == "identity":
                return {"application": {"pid": 4242, "document": dict(document)}}
            if isinstance(replies.get("error"), CLIError):
                raise replies["error"]
            return dict(replies)

    monkeypatch.setattr(S, "native", Backend)
    monkeypatch.setattr(S, "_stop_domain", lambda options: "pcb")
    monkeypatch.setattr(S, "process_identity", lambda domain, pid: dict(IDENTITY))
    monkeypatch.setattr(S, "check_gate", lambda *args: None)
    monkeypatch.setattr(S, "confirmed", lambda *args: None)
    monkeypatch.setattr(S, "previewed", lambda preview, scope: {"preview": preview, "scope": scope})
    return document, replies, calls


def test_owned_empty_close_previews_generation_then_clears_only_os_success(owned):
    document, replies, calls = owned
    options = {"kind": "pcb", "pid": 4242, "_operation_id": "op-original"}
    preview = S.stop({**options, "dry_run": True})["preview"]
    assert preview["process_identity"] == IDENTITY
    assert preview["owned_process"] is True
    assert preview["document"] == document
    assert not any(method == "close" for method, params in calls)
    result = S.stop(options)
    assert result["closed"] is True
    assert session.read_state() == {}
    close = calls[-1][1]
    assert close["expected_process_identity"] == IDENTITY
    assert close["save_project"] is None


@pytest.mark.parametrize("document", [{}, {"open": None}, {"open": True}, {"open": False}])
def test_session_only_unknown_or_nonempty_document_refuses_before_quit(owned, document):
    observed, replies, calls = owned
    observed.clear()
    observed.update(document)
    with pytest.raises(CLIError) as caught:
        S.stop({"kind": "pcb", "pid": 4242, "dry_run": True})
    assert caught.value.details["write_attempted"] is False
    assert not any(method == "close" for method, params in calls)


@pytest.mark.parametrize("drift", ["pid", "creation_time_100ns", "executable", "unowned"])
def test_foreign_or_reused_process_refused_before_quit(owned, monkeypatch, drift):
    _document, _replies, calls = owned
    changed = dict(IDENTITY)
    if drift == "unowned":
        session.clear_native()
    else:
        changed[drift] = {
            "pid": 9000,
            "creation_time_100ns": "133123456789012346",
            "executable": "C:/other/ExpeditionPCB.exe",
        }[drift]
        monkeypatch.setattr(S, "process_identity", lambda domain, pid: changed)
    with pytest.raises(CLIError) as caught:
        S.stop({"kind": "pcb", "pid": 4242, "dry_run": True})
    assert caught.value.code == "E_CONFLICT"
    assert caught.value.details["write_attempted"] is False
    assert not any(method == "close" for method, params in calls)


def test_signed_process_generation_must_still_match(owned):
    with pytest.raises(CLIError):
        S.stop(
            {
                "kind": "pcb",
                "pid": 4242,
                "_expected_process_identity": {**IDENTITY, "creation_time_100ns": "999"},
            }
        )


def test_explicit_save_project_is_bound_and_foreign_board_refused(owned, project):
    document, replies, calls = owned
    # The shared fixture has no linked board. Naming a project must not save any
    # arbitrary open document, even though its process belongs to this task.
    document.update(open=True, path="C:/other/Foreign.pcb")
    with pytest.raises(CLIError):
        S.stop({"kind": "pcb", "pid": 4242, "save_project": str(project), "dry_run": True})
    assert not any(method == "close" for method, params in calls)


@pytest.mark.parametrize("backend_error", [False, True])
def test_pending_close_retains_id_and_cannot_repeat_quit(owned, backend_error):
    document, replies, calls = owned
    replies.update(closed=False, process_absence_observed=False, closing_pending=True)
    if backend_error:
        replies["error"] = CLIError("E_TIMEOUT", "Quit reply lost", {"write_attempted": True})
    options = {"kind": "pcb", "pid": 4242, "_operation_id": "op-keep-original"}
    if backend_error:
        with pytest.raises(CLIError):
            S.stop(options)
    else:
        assert S.stop(options)["closing_pending"] is True
    assert session.read_state()["state"] == "closing"
    assert session.read_state()["closing_operation_id"] == "op-keep-original"
    count = len(calls)
    with pytest.raises(CLIError) as caught:
        S.stop(options)
    assert caught.value.details["operation_id"] == "op-keep-original"
    assert len(calls) == count


@pytest.mark.parametrize("action", [S.start, S.attach])
@pytest.mark.parametrize("domain", ["pcb", "schematic"])
def test_pending_close_cannot_be_erased_by_attach_or_another_start(owned, action, domain):
    _document, replies, calls = owned
    replies.update(closed=False, process_absence_observed=False)
    S.stop({"kind": "pcb", "pid": 4242, "_operation_id": "op-no-repeat"})
    count = len(calls)
    with pytest.raises(CLIError) as caught:
        action({"kind": domain, "pid": 4242})
    if action is S.attach and domain == "schematic":
        # The dedicated architecture rejects legacy Designer attach before
        # reading any existing session; the original close still stays intact.
        assert caught.value.code == "E_HUMAN_REQUIRED"
    else:
        assert caught.value.details["operation_id"] == "op-no-repeat"
    assert session.read_state()["state"] == "closing"
    assert session.read_state()["closing_operation_id"] == "op-no-repeat"
    assert len(calls) == count


def test_late_exit_is_only_observed_without_com_and_original_id_retained(owned, monkeypatch):
    document, replies, calls = owned
    replies.update(closed=False, process_absence_observed=False)
    S.stop({"kind": "pcb", "pid": 4242, "_operation_id": "op-late"})
    count = len(calls)
    monkeypatch.setattr(
        S, "NativeBackend", lambda: SimpleNamespace(status=lambda: {"available": True})
    )
    observations = []

    def observe(domain, pid, **kwargs):
        observations.append((domain, pid, kwargs))
        return {"pid": None, "process_absence_observed": True}

    monkeypatch.setattr(S, "observe_exit", observe)
    monkeypatch.setattr(
        session,
        "_pid_alive",
        lambda pid: (
            True if str(pid) == str(IDENTITY["pid"]) else pytest.fail("Foreign fixture PID")
        ),
    )
    result = S.status({})
    assert len(calls) == count
    assert observations == [("pcb", "4242", {"timeout": 0, "expected_identity": IDENTITY})]
    assert result["closure_observation"]["original_operation_id"] == "op-late"
    assert result["closure_observation"]["historical_receipt_changed"] is False
    assert session.read_state() == {}


def test_close_journal_write_failure_stops_before_native_dispatch(owned, monkeypatch):
    _document, _replies, calls = owned
    monkeypatch.setattr(session, "_write_state", lambda value: None)
    with pytest.raises(CLIError) as caught:
        S.stop({"kind": "pcb", "pid": 4242, "_operation_id": "op-not-recorded"})
    assert caught.value.code == "E_IO"
    assert not any(method == "close" for method, params in calls)


@pytest.fixture
def native_close(monkeypatch, tmp_path):
    clock = [0.0]
    events = []
    state = {"save": True, "close": True, "alive": False, "document_unknown": False}
    board = tmp_path / "Demo.pcb"
    board.write_bytes(b"offline placeholder")

    class Document:
        FullName = str(board)

        def Save(self):
            events.append("Save")
            return state["save"]

        def Close(self, save):
            events.append("Close")
            if state["close"] is True:
                app.document = None
            return state["close"]

    class Application:
        document = Document()

        @property
        def ActiveDocument(self):
            if state["document_unknown"]:
                raise RuntimeError("synthetic unavailable getter")
            return self.document

        def Quit(self):
            events.append("Quit")
            if state.get("quit_error"):
                raise RuntimeError("synthetic reply lost")

    app = Application()

    class Handle:
        def __init__(self, domain, pid, expected=None, **kwargs):
            events.append("pin_process")
            self.identity = dict(IDENTITY)
            if state.get("foreign"):
                raise ValueError("process generation changed")

        def exited(self):
            events.append("OS_exit_probe")
            return not state["alive"]

        def close(self):
            events.append("release_process_handle")

    monkeypatch.setattr(P, "ProcessHandle", Handle)
    monkeypatch.setattr(
        N,
        "_import_com",
        lambda: (
            SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None),
            SimpleNamespace(),
        ),
    )
    monkeypatch.setattr(N, "_heal_gen_py_cache", lambda: None)
    monkeypatch.setattr(N, "_application", lambda *args, **kwargs: app)
    monkeypatch.setattr(N, "_licensed_document", lambda application: app.ActiveDocument)
    monkeypatch.setattr(N, "application_identity", lambda *args: {"pid": state.get("pid", 4242)})
    monkeypatch.setattr(N, "_layout_board_path", lambda params: board)
    monkeypatch.setattr(N, "_settle", lambda seconds: None)
    monkeypatch.setattr(
        N,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
    )
    monkeypatch.setattr(
        N,
        "_PromptAnswerer",
        lambda *args, **kwargs: pytest.fail("session closure may not click GUI prompts"),
    )
    params = {
        "domain": "pcb",
        "pid": 4242,
        "expected_process_identity": IDENTITY,
        "save_project": str(board),
        "exit_wait_seconds": 0.4,
    }
    return app, state, events, params


def test_native_explicit_save_close_then_single_quit_and_original_os_handle(native_close):
    app, state, events, params = native_close
    result = N.dispatch("close", params)
    assert result["closed"] is True
    assert result["lifecycle"]["save_acknowledged"] is True
    assert result["lifecycle"]["close_acknowledged"] is True
    assert result["lifecycle"]["empty_document_observed"] is True
    assert (
        events.index("Save")
        < events.index("Close")
        < events.index("Quit")
        < events.index("OS_exit_probe")
    )
    assert events.count("Quit") == 1
    assert result["exit_observation"]["com_unregistration_is_exit_evidence"] is False


@pytest.mark.parametrize("stage", ["save", "close"])
@pytest.mark.parametrize("reply", [False, None])
def test_save_close_false_or_unknown_cannot_quit(native_close, stage, reply):
    app, state, events, params = native_close
    state[stage] = reply
    with pytest.raises(N.AdapterError):
        N.dispatch("close", params)
    assert "Quit" not in events
    if stage == "save":
        assert "Close" not in events


@pytest.mark.parametrize(
    "case", ["foreign", "different_pid", "unknown_document", "session_only_nonempty"]
)
def test_native_foreign_and_unknown_refuse_before_save_or_quit(native_close, case):
    app, state, events, params = native_close
    if case == "foreign":
        state["foreign"] = True
    elif case == "different_pid":
        state["pid"] = 9000
    elif case == "unknown_document":
        state["document_unknown"] = True
    else:
        params.pop("save_project")
    with pytest.raises(N.AdapterError):
        N.dispatch("close", params)
    assert "Save" not in events and "Quit" not in events


def test_quit_reply_does_not_prove_os_exit_or_retry_quit(native_close):
    app, state, events, params = native_close
    state["alive"] = True
    result = N.dispatch("close", params)
    assert result["closed"] is False and result["closing_pending"] is True
    assert result["lifecycle"]["quit_reply_received"] is True
    assert events.count("Quit") == 1
    assert result["exit_observation"]["elapsed_seconds"] == 0.4


def test_lost_quit_reply_is_recorded_and_never_repeated(native_close):
    app, state, events, params = native_close
    state["quit_error"] = True
    with pytest.raises(N.AdapterError) as caught:
        N.dispatch("close", params)
    assert caught.value.details["quit_dispatched"] is True
    assert caught.value.details["lifecycle"]["quit_count"] == 1
    assert events.count("Quit") == 1


@pytest.mark.parametrize("timeout", [-1, 61, float("nan"), float("inf")])
def test_process_wait_is_bounded(timeout):
    with pytest.raises(ValueError):
        P.observe_exit("pcb", "4242", timeout=timeout, expected_identity=IDENTITY)


def test_process_identity_never_accepts_pid_only():
    assert not P.same_identity({"pid": 4242}, IDENTITY)
    assert not P.same_identity(IDENTITY, {**IDENTITY, "creation_time_100ns": ""})


@pytest.mark.parametrize("reused", [False, True])
def test_bound_os_observation_handles_late_exit_and_pid_reuse(process_api, monkeypatch, reused):
    state, closed = process_api
    state["birth"] = 999 if reused else int(IDENTITY["creation_time_100ns"])
    state["wait"] = 258 if reused else 0
    state["image_ok"] = False
    state["image_error"] = 31
    monkeypatch.setattr(
        P,
        "ProcessHandle",
        lambda *args, **kwargs: pytest.fail("An exit observer must not require live admission"),
    )
    result = P.observe_exit("pcb", "4242", timeout=0, expected_identity=IDENTITY)
    assert result["process_absence_observed"] is True
    assert result["exit_observation"]["pid_reused"] is reused
    assert result["exit_observation"]["wait_result"] == (258 if reused else 0)
    assert result["exit_observation"]["observed_process_identity"]["creation_time_100ns"] == str(
        state["birth"]
    )
    assert result["exit_observation"]["observed_process_identity"]["executable"] is None
    assert state["image_queries"] == 0
    assert closed == [99]


@pytest.fixture
def process_api(monkeypatch):
    import ctypes
    import os

    state = {
        "open": 99,
        "pid": IDENTITY["pid"],
        "path": IDENTITY["executable"],
        "birth": int(IDENTITY["creation_time_100ns"]),
        "wait": 258,
        "times_ok": True,
        "image_ok": True,
        "image_error": 31,
        "image_queries": 0,
        "error": 5,
    }
    closed = []

    class Function:
        def __init__(self, call):
            self.call = call

        def __call__(self, *args):
            return self.call(*args)

    def image(handle, flags, buffer, size):
        state["image_queries"] += 1
        if not state["image_ok"]:
            state["error"] = state["image_error"]
            return False
        buffer.value = state["path"]
        return True

    def times(handle, creation, exited, kernel, user):
        creation._obj.dwHighDateTime = state["birth"] >> 32
        creation._obj.dwLowDateTime = state["birth"] & 0xFFFFFFFF
        return state["times_ok"]

    api = SimpleNamespace(
        OpenProcess=Function(lambda *args: state["open"]),
        GetProcessId=Function(lambda *args: state["pid"]),
        CloseHandle=Function(lambda handle: closed.append(handle) or 1),
        WaitForSingleObject=Function(lambda *args: state["wait"]),
        QueryFullProcessImageNameW=Function(image),
        GetProcessTimes=Function(times),
    )
    monkeypatch.setattr(P, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: api, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state["error"], raising=False)
    return state, closed


def test_os_process_identity_has_real_creation_time_and_releases_handle(process_api):
    _state, closed = process_api
    assert P.process_identity("pcb", 4242) == IDENTITY
    assert closed == [99]


@pytest.mark.parametrize(
    "case", ["wrong_executable", "creation_drift", "times_unavailable", "wait_failed"]
)
def test_os_process_identity_failures_cannot_authorize_close(process_api, case):
    state, closed = process_api
    if case == "wrong_executable":
        state["path"] = "C:/CAD/foreign.exe"
    elif case == "creation_drift":
        state["birth"] += 1
    elif case == "times_unavailable":
        state["times_ok"] = False
    else:
        state["wait"] = 0xFFFFFFFF
    with pytest.raises((ValueError, OSError)):
        with P.ProcessHandle("pcb", 4242, IDENTITY) as handle:
            handle.exited()
    assert closed == [99]


def test_os_access_denied_is_unknown_and_never_proves_exit(process_api):
    state, closed = process_api
    state["open"] = 0
    with pytest.raises(OSError):
        P.observe_exit("pcb", "4242", timeout=0, expected_identity=IDENTITY)
    assert closed == []
