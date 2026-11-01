"""Public SDK entrance + actual diagnostic producer against a recording fake COM.

Closed-copy and owned-start receipts are made by the real public CLI in an owned
temporary journal. No process/COM application, SDK executable or CAD is run.
"""

from __future__ import annotations

import copy
import ctypes
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from drc_fakes import write_review_evidence
from test_driver_v2 import _scope
from test_project_isolated_copy import fixture as source_fixture
from test_session_start_generation import startup  # noqa: F401 - shared fixture

from xpedition_cli import close_diagnostic as D
from xpedition_cli import native_close_diagnostic as N
from xpedition_cli import native_com_adapter as bridge
from xpedition_cli.cli import diagnostics, sessions
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError
from xpedition_cli.native_identity import adapter_identity


def test_window_pid_contract_keeps_pointer_width_without_real_win32(monkeypatch):
    hwnd = 0x123456789

    def query(actual, output):
        assert actual == hwnd
        ctypes.cast(output, ctypes.POINTER(ctypes.c_ulong)).contents.value = 22220
        return 123

    monkeypatch.setattr(
        N.ctypes, "windll", NS(user32=NS(GetWindowThreadProcessId=query)), raising=False
    )
    assert N._pid(hwnd) == 22220
    assert query.argtypes == (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    assert query.restype is ctypes.c_ulong


@pytest.fixture
def case(startup, tmp_path, monkeypatch, request):  # noqa: F811
    _scope(tmp_path, monkeypatch)
    scenario = getattr(request, "param", None)
    scenario = (
        scenario if isinstance(scenario, dict) else {"custom_journal": scenario == "custom-journal"}
    )
    source, archive, project, _ = source_fixture(tmp_path)
    board = project.parent / "PCB/Board.pcb"
    events, requests, after = [], [], []
    value = NS(
        fault=None,
        pending=False,
        dispatched=False,
        closed=False,
        events=events,
        requests=requests,
        after=after,
        startup=startup,
        project=project,
        board=board,
        tmp_path=tmp_path,
        open_changes=scenario.get("open_changes", False),
        before_open_bytes=None,
        after_open_bytes=None,
    )

    class Document:
        @property
        def FullName(self):
            events.append("Document.FullName")
            assert not value.pending, "CAD getter after uncertain native terminal"
            return str(board)

        def Validate(self, key):
            events.append("Document.Validate")
            return 100 if key == 0 else True

        def Save(self):
            events.append("Document.Save")
            if value.fault == "save_reply_lost":
                value.pending = True
                raise RuntimeError("synthetic lost Save reply")
            if value.fault == "save_untyped":
                value.pending = True
                return 1
            return value.fault != "save_false"

        def Close(self, save):
            assert save is True
            events.append("Document.Close(True)")
            if value.fault == "close_false":
                return False
            value.closed = True
            if value.fault == "close_reply_lost":
                value.pending = True
                raise RuntimeError("synthetic lost Close reply")
            if value.fault == "close_untyped":
                value.pending = True
                return None
            return True

    doc = Document()

    class Library:
        def GetLibAttr(self):
            events.append("TypeLib.GetLibAttr")
            return ("synthetic-typelib", 0, 3, 151, 0, 0)

        def GetDocumentation(self, index):
            assert index == -1
            events.append("TypeLib.GetDocumentation(-1)")
            return ("synthetic-Layout",)

    class Info:
        def GetContainingTypeLib(self):
            events.append("TypeInfo.GetContainingTypeLib")
            return Library(), 0

    class Ole:
        def GetTypeInfo(self):
            events.append("Application.GetTypeInfo")
            assert not value.pending
            return Info()

    class App:
        _oleobj_ = Ole()

        @property
        def FullName(self):
            events.append("Application.FullName")
            return startup.identities[22220]["executable"]

        @property
        def Version(self):
            events.append("Application.Version")
            return "151.0 synthetic"

        @property
        def Gui(self):
            events.append("Application.Gui")

            class Gui:
                @property
                def HWND(self):
                    events.append("Gui.HWND")
                    return 123

            return Gui()

        @property
        def ActiveDocument(self):
            events.append("Application.ActiveDocument")
            assert not value.pending, "CAD access after uncertain native terminal"
            if value.fault == "foreign_document" and value.closed:
                return NS(FullName=str(tmp_path / "foreign.pcb"))
            return None if value.closed else doc

    app = App()

    class Client:
        def GetActiveObject(self, progid):
            events.append("GetActiveObject:" + progid)
            assert progid == "MGCPCB.Application.151"
            assert not value.pending
            return app

        def Dispatch(self, progid):
            events.append("Dispatch:" + progid)
            assert progid == "MGCPCBAutomationLicensing.Application"

            def get_token(key):
                events.append("Licensing.GetToken")
                return 200

            return NS(GetToken=get_token)

    monkeypatch.setattr(N, "_pid", lambda hwnd: 22220)
    monkeypatch.setattr(
        N, "process_identity", lambda domain, pid: copy.deepcopy(startup.identities[int(pid)])
    )
    monkeypatch.setattr(
        bridge,
        "_import_com",
        lambda: (
            NS(
                CoInitialize=lambda: events.append("CoInitialize"),
                CoUninitialize=lambda: events.append("CoUninitialize"),
            ),
            Client(),
        ),
    )
    monkeypatch.setattr(bridge, "_heal_gen_py_cache", lambda: pytest.fail("generic wrapper cache"))
    original = startup.backend.invoke

    class Backend:
        def invoke(self, method, params, **kwargs):
            # Match the real NativeBackend boundary, including the one startup/Open.
            # Qualification consumes this request's actual native-call evidence.
            from xpedition_cli.operation_budget import native_call_budget

            with native_call_budget(method, kwargs.get("timeout_seconds", 30.0)):
                return self._invoke(method, params, **kwargs)

        def _invoke(self, method, params, **kwargs):
            requests.append(method)
            if value.dispatched:
                after.append(method)
            if method in {"close_diagnostic_state", "close_diagnostic"}:
                if method == "close_diagnostic_state" and value.fault == "candidate_drift":
                    reply = bridge.dispatch(method, params)
                    reply["adapter"]["build_sha256"] = "c" * 64
                    return reply
                if method == "close_diagnostic":
                    assert kwargs["timeout_seconds"] == 180
                    value.dispatched = True
                    if value.fault in {"worker_timeout", "nested_timeout"}:
                        details = {}
                        if value.fault == "nested_timeout":
                            life = {
                                "save_count": 1,
                                "save_returned": True,
                                "save_result": True,
                                "save_acknowledged": True,
                                "close_count": 1,
                                "close_returned": True,
                                "close_result": True,
                            }
                            details = {
                                "native_result": {
                                    "contract": D.CONTRACT,
                                    "board_lifecycle": life,
                                    "terminal_state": "returned",
                                    "binding_sha256": D.digest(params["binding"]),
                                }
                            }
                        # The enclosing timeout overrides even typed returned progress.
                        raise CLIError(
                            "E_NATIVE_FAILURE",
                            "synthetic wrapped transport timeout",
                            {"cause": {"code": "E_TIMEOUT", "details": details}},
                        )
                if method == "close_diagnostic_state" and value.dispatched:
                    if value.fault == "observation_timeout" or (
                        value.fault == "second_observation_timeout" and len(after) == 2
                    ):
                        value.pending = True
                        raise CLIError("E_TIMEOUT", "synthetic first observation timeout")
                    if value.fault == "worker_drift":
                        reply = bridge.dispatch(method, params)
                        reply["adapter"]["build_sha256"] = "b" * 64
                        return reply
                try:
                    return bridge.dispatch(method, params)
                except bridge.AdapterError as error:
                    raise CLIError(error.code, error.message, error.details) from error
            reply = original(method, params, **kwargs)
            if method == "identity":
                reply["adapter"] = adapter_identity()
                if startup.opened:
                    reply["application"]["document"]["path"] = str(board)
            if method == "snapshot":
                reply["project"] = str(project)
            if method == "open":
                value.before_open_bytes = board.read_bytes()
                if value.open_changes:
                    board.write_bytes(
                        value.before_open_bytes + b"\nsynthetic session header changed by Open\n"
                    )
                value.after_open_bytes = board.read_bytes()
                reply["project"] = str(board)
            return reply

    backend = Backend()
    value.backend = backend
    monkeypatch.setattr(sessions, "native", lambda: backend)
    monkeypatch.setattr(diagnostics, "native", lambda: backend)
    runtime_options = (
        {"state_dir": tmp_path / "custom-sdk-journal"} if scenario.get("custom_journal") else {}
    )
    with runtime(Adapter(backend), **runtime_options) as cli:
        args = [
            "project",
            "create",
            "--template",
            str(source),
            "--project",
            str(project),
            "--source-backup",
            str(archive),
            "--closed-copy",
            "--dangerous",
            "--quiet",
        ]
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert applied.envelope["ok"], applied.envelope
        receipt = applied.envelope["data"]["receipt"]
        origin = copy.deepcopy(
            receipt["extensions"]["result"]["closed_copy"]["registration"]["data"]
        )
        origin["copy_operation_id"] = receipt["operation_id"]
        copy_receipt = copy.deepcopy(receipt)
        origin_path = tmp_path / "origin.json"
        origin_path.write_text(json.dumps(origin), encoding="utf-8")
        args = [
            "session",
            "start",
            "--kind",
            "pcb",
            "--project",
            str(project),
            "--quiet",
        ]
        value.start_args = args
        value.start_output = None
        value.start_id = None
        value.post_open_token = None
        if not scenario.get("defer_start"):
            preview = cli.run([*args, "--dry-run"])
            assert preview.envelope["ok"], preview.envelope
            started = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
            assert started.envelope["ok"], started.envelope
            value.start_output = started
            value.start_id = started.envelope["data"]["receipt"]["operation_id"]
            qualification = (
                started.envelope["data"].get("extensions", {}).get("closed_copy_startup") or {}
            )
            value.post_open_token = qualification.get("token")
            assert value.post_open_token, {
                "response": started.envelope,
                "stderr": started.stderr,
            }
        value.preparation = {
            "kind": "synthetic_public_copy_and_owned_start_before_diagnostic",
            "copy_receipt": copy_receipt,
            "startup_receipt": value.start_output.envelope["data"]["receipt"]
            if value.start_output
            else None,
            "preparation_requests": list(requests),
        }
        value.origin, value.origin_path = origin, origin_path
        value.cli = cli
        value.args = [
            "pcb",
            "diagnostics",
            "close",
            "--project",
            str(project),
            "--pid",
            "22220",
            "--progid",
            "MGCPCB.Application.151",
            "--isolated-origin",
            str(origin_path),
            *(["--post-open-token", value.post_open_token] if value.post_open_token else []),
            "--quiet",
        ]
        requests.clear()
        events.clear()
        yield value


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "save_false",
        "save_reply_lost",
        "save_untyped",
        "close_false",
        "close_reply_lost",
        "close_untyped",
        "worker_timeout",
        "nested_timeout",
        "observation_timeout",
        "second_observation_timeout",
        "foreign_document",
        "worker_drift",
        "file_access",
        "file_changed",
    ],
)
def test_public_close_entry_binds_one_shot_and_stops_unknown(case, monkeypatch, fault):
    case.fault = fault
    initial_profile = copy.deepcopy(D.files.drc.planned_side_effects(case.board))
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    token = preview.envelope["data"]["confirm_token"]
    before_events = len(case.events)
    captures = []
    original_capture = D.files._capture
    original_read = D.files.drc._read_contents

    def read(path, **kwargs):
        if fault == "file_access" and case.closed and Path(path) == case.board:
            raise PermissionError("synthetic access denied at the file boundary")
        return original_read(path, **kwargs)

    def capture(bound):
        frame = original_capture(bound)
        if case.dispatched:
            captures.append(frame)
            if fault == "file_changed" and len(captures) == 1:
                case.board.write_bytes(b"actual synthetic file replacement between the two frames")
        return frame

    monkeypatch.setattr(D.files, "_capture", capture)
    monkeypatch.setattr(D.files.drc, "_read_contents", read)
    applied = case.cli.run([*case.args, "--confirm", token])
    assert not applied.envelope["ok"], applied.envelope
    assert applied.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", applied.envelope
    assert "receipt" in applied.envelope["error"]["details"], applied.envelope
    receipt = applied.envelope["error"]["details"]["receipt"]
    observation = receipt["extensions"]["independent_readback"]
    unknown = fault in {
        "save_reply_lost",
        "save_untyped",
        "close_reply_lost",
        "close_untyped",
        "worker_timeout",
        "nested_timeout",
    }
    if unknown:
        assert case.after == []
        assert observation["status"] == "unobserved" and observation["frames"] == []
        assert observation["native_request_dispatched"] is False
        assert receipt["outcome"]["execution"] == "unknown"
    elif fault == "second_observation_timeout":
        assert case.after == ["close_diagnostic_state"] * 2
        assert observation["status"] == "incomplete"
        assert len(observation["frames"]) == 1
        assert len(observation["identity_attempts"]) == 2
        assert observation["identity_attempts"][-1]["error"]["code"] == "E_TIMEOUT"
    elif fault in {
        "observation_timeout",
        "foreign_document",
        "worker_drift",
        "save_false",
        "close_false",
    }:
        assert case.after == ["close_diagnostic_state"]
        assert observation["status"] == (
            "unobserved" if fault == "observation_timeout" else "incomplete"
        )
    else:
        assert case.after == ["close_diagnostic_state"] * 2
        assert len(observation["frames"]) == 2
        assert observation["status"] == ("complete" if fault is None else "incomplete")
        assert all(len(frame["files"]) == 12 for frame in observation["frames"])
    assert receipt["outcome"]["persistence"] == "unknown"
    assert receipt["extensions"]["persistence_independently_reopened"] is False
    ack = fault not in {"save_false", "save_reply_lost", "save_untyped", "worker_timeout"}
    assert receipt["extensions"]["save_acknowledged"] is ack
    if receipt["extensions"]["result"]:
        assert receipt["extensions"]["result"]["board_lifecycle"]["save_acknowledged"] is ack
    save_count = 0 if fault in {"worker_timeout", "nested_timeout"} else 1
    close_count = (
        0
        if fault
        in {"save_false", "save_reply_lost", "save_untyped", "worker_timeout", "nested_timeout"}
        else 1
    )
    assert case.events.count("Document.Save") == save_count
    assert case.events.count("Document.Close(True)") == close_count
    if unknown and save_count:
        last = "Document.Close(True)" if close_count else "Document.Save"
        assert case.events[case.events.index(last) + 1 :] == ["CoUninitialize"]
    if fault is None:
        counts = Counter(case.events)
        for name in (
            "GetActiveObject:MGCPCB.Application.151",
            "Application.GetTypeInfo",
            "TypeInfo.GetContainingTypeLib",
            "TypeLib.GetLibAttr",
            "TypeLib.GetDocumentation(-1)",
            "Application.FullName",
            "Application.Version",
            "Application.Gui",
            "Gui.HWND",
            "CoInitialize",
            "CoUninitialize",
        ):
            assert counts[name] == 6, {"name": name, "counts": counts}
        assert counts["Application.ActiveDocument"] == 7
        assert counts["Document.FullName"] == 6
        assert counts["Document.Validate"] == 2
        assert counts["Dispatch:MGCPCBAutomationLicensing.Application"] == 1
        assert counts["Licensing.GetToken"] == 1
    assert not any(
        name in case.requests
        for name in (
            "identity",
            "snapshot",
            "cell_refresh_state",
            "batch_drc",
            "open",
            "save",
            "close",
        )
    )
    operation_id = receipt["operation_id"]
    stored = copy.deepcopy(case.cli._store.get(operation_id))
    assert stored["status"] == "unknown" and stored["resolution"] is None
    event_count, request_count = len(case.events), len(case.requests)
    shown = case.cli.run(["operation", "show", "--id", operation_id, "--quiet"])
    assert shown.envelope["data"]["operation_status"]["receipt"] == stored["receipt"]
    replay = case.cli.run([*case.args, "--confirm", token])
    assert not replay.envelope["ok"]
    assert (len(case.events), len(case.requests)) == (event_count, request_count)
    assert case.cli._store.get(operation_id)["receipt"] == stored["receipt"]
    assert D.files.drc.planned_side_effects(case.board) == initial_profile
    write_review_evidence(
        "close-diagnostic-" + str(fault),
        {
            "kind": "offline_public_cli_and_recording_COM_not_native_execution",
            "fault": fault,
            "receipt": receipt,
            "public_preview": preview.envelope["data"]["preview"],
            "preparation": case.preparation,
            "requests": case.requests,
            "post_dispatch_requests": case.after,
            "events": case.events,
            "native_call_counts": dict(Counter(case.events)),
            "confirm_event_offset": before_events,
            "explicit_save_calls": save_count,
            "explicit_close_calls": close_count,
            "replay_rejected": True,
            "journal_unknown_unchanged": True,
        },
        case.tmp_path,
    )


@pytest.mark.parametrize("fault", ["unowned", "origin", "board_bytes", "pid", "progid"])
def test_public_admission_refuses_without_saving(case, monkeypatch, fault):
    if fault == "unowned":
        monkeypatch.setattr(case.cli.adapter, "_ownership", lambda *a, **kw: ({}, None))
    elif fault == "origin":
        case.origin["copy_operation_id"] = "op-missing00000000"
        case.origin_path.write_text(json.dumps(case.origin), encoding="utf-8")
    elif fault == "board_bytes":
        case.board.write_bytes(b"a used copy")
    elif fault == "pid":
        case.args[case.args.index("22220")] = "5252"
    else:
        case.args[case.args.index("MGCPCB.Application.151")] = "MGCPCB.Application"
    result = case.cli.run([*case.args, "--dry-run"])
    assert not result.envelope["ok"], result.envelope
    assert not case.dispatched
    assert "Document.Save" not in case.events and "Document.Close(True)" not in case.events


def test_a_new_token_cannot_repeat_save_close_on_the_same_copy(case):
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    case.closed = False  # External reopen is simulated, never dispatched by the CLI.
    case.requests.clear()
    refused = case.cli.run([*case.args, "--dry-run"])
    assert not refused.envelope["ok"], refused.envelope
    assert case.requests == []
    assert case.events.count("Document.Save") == case.events.count("Document.Close(True)") == 1


@pytest.mark.parametrize("fault", ["origin", "birth", "candidate_drift"])
def test_public_confirmation_rechecks_original_admission_without_save(case, fault):
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    if fault == "origin":
        case.origin_path.write_text(json.dumps(case.origin) + "\n", encoding="utf-8")
    elif fault == "birth":
        case.startup.identities[22220]["creation_time_100ns"] = "133000000000999999"
    else:
        case.fault = fault
    refused = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not refused.envelope["ok"], refused.envelope
    assert refused.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert not case.dispatched
    assert "Document.Save" not in case.events and "Document.Close(True)" not in case.events


def _planned_alias_move(case, monkeypatch, cli=None, target=None):
    """Create the competing .pcb plan through the public SDK with fake handlers."""
    from xpedition_cli import driver

    cli = cli or case.cli
    original = driver._handler

    def handler(command):
        if command.path == "pcb placement move":
            return lambda options: {"confirm_token": "synthetic-move", "changes": []}
        return original(command)

    monkeypatch.setattr(driver, "_handler", handler)
    args = [
        "pcb",
        "placement",
        "move",
        "--project",
        str(target or case.board),
        "--refdes",
        "R1",
        "--to",
        "1,2",
        "--quiet",
    ]
    preview = cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    return args, preview.envelope["data"]


@pytest.mark.parametrize("terminal", ["dispatched", "unknown"])
def test_public_diagnostic_rejects_alias_dispatched_after_revision(case, monkeypatch, terminal):
    """A legacy/interleaved ledger dispatch must be caught without a further CAD request."""
    import time

    from xpedition_cli import driver

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    _, move = _planned_alias_move(case, monkeypatch)
    operation_id = move["preview"]["operation_id"]
    board_before = case.board.read_bytes()
    original_revision = case.cli.adapter.revision
    retained = {}

    def revision_then_competing_write(target):
        result = original_revision(target)
        if target["extensions"]["command"] != D.COMMAND:
            return result
        store = case.cli._store_open()
        record = store.get(operation_id)
        store.dispatch(
            operation_id,
            fingerprint="synthetic-legacy-move-" + operation_id,
            token_expires=time.time() + 300,
            key=None,
            digest=record["request_digest"],
            target_keys=[record["target_key"]],
            lease=120,
        )
        if terminal == "unknown":
            cause = CLIError("E_NATIVE_FAILURE", "synthetic Place failure", {"partial": True})
            call = NS(command="pcb placement move", effects=("change", "persist"))
            fields = driver._unobserved_receipt(
                call,
                {
                    "change_status": "partial",
                    "persistence_state": "unsaved",
                    "unplace_returned": True,
                    "place_failed": True,
                    "rollback_performed": False,
                },
                cause,
                None,
            )
            fields["outcome"]["execution"] = "unknown"
            receipt = case.cli._complete_receipt(fields, record, record["plan"], None)
            assert receipt is not None
            store.finish(operation_id, "unknown", receipt)
        retained["record"] = copy.deepcopy(store.get(operation_id))
        retained["requests"] = list(case.requests)
        case.pending = True
        return result

    monkeypatch.setattr(case.cli.adapter, "revision", revision_then_competing_write)
    blocked = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not blocked.envelope["ok"], blocked.envelope
    assert blocked.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert not case.dispatched
    assert "Document.Save" not in case.events and "Document.Close(True)" not in case.events
    assert case.requests == retained["requests"]
    assert case.cli._store_open().get(operation_id) == retained["record"]
    assert case.board.read_bytes() == board_before


def test_public_competing_writer_stops_while_diagnostic_is_exclusive(case, monkeypatch):
    """The reverse interleaving cannot reach a native query, dispatch or write."""
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    with runtime(Adapter(case.cli.adapter.backend)) as other:
        args, move = _planned_alias_move(case, monkeypatch, other)
        move_id = move["preview"]["operation_id"]
        original_record = copy.deepcopy(other._store_open().get(move_id))
        original_invoke = case.cli.adapter.backend.invoke
        competing = {}

        def invoke(method, params, **kwargs):
            if method == "close_diagnostic":
                prior = list(case.requests)
                competing["result"] = other.run([*args, "--confirm", move["confirm_token"]])
                assert case.requests == prior
                assert other._store_open().get(move_id) == original_record
            return original_invoke(method, params, **kwargs)

        monkeypatch.setattr(case.cli.adapter.backend, "invoke", invoke)
        applied = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert not competing["result"].envelope["ok"]
        assert competing["result"].envelope["error"]["details"]["dispatch_state"] == "not_started"
        assert not applied.envelope["ok"]
        receipt = applied.envelope["error"]["details"]["receipt"]
        diagnostic_id = receipt["operation_id"]
        retained = copy.deepcopy(case.cli._store_open().get(diagnostic_id))
        assert retained["status"] == "unknown" and retained["receipt"] == receipt
        prior = list(case.requests)
        refused = other.run([*args, "--confirm", move["confirm_token"]])
        assert not refused.envelope["ok"]
        assert case.requests == prior
        assert other._store_open().get(move_id) == original_record
        assert case.cli._store_open().get(diagnostic_id) == retained
        assert case.events.count("Document.Save") == case.events.count("Document.Close(True)") == 1


def test_public_context_exit_timeout_retains_two_frames_and_ack(case, monkeypatch):
    from xpedition_cli.operation_budget import current_budget

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    original_observe = D.observe

    def complete_then_expire(*args):
        observation = original_observe(*args)
        assert observation["status"] == "complete" and len(observation["frames"]) == 2
        budget = current_budget()
        assert budget.active_stage["stage"] == "observe_after"
        budget.active_stage["_deadline"] = budget.clock()
        return observation

    monkeypatch.setattr(D, "observe", complete_then_expire)
    applied = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not applied.envelope["ok"]
    details = applied.envelope["error"]["details"]
    receipt = details["receipt"]
    observation = receipt["extensions"]["independent_readback"]
    assert details["diagnostic_complete"] is False
    assert observation["status"] == "incomplete" and len(observation["frames"]) == 2
    assert observation["error"]["code"] == "E_TIMEOUT"
    assert receipt["extensions"]["save_acknowledged"] is True
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["extensions"]["independent_readback_completed"] is False
    assert details["operation_budget"]["failed_stage"] == "observe_after"
    assert case.after == ["close_diagnostic_state"] * 2
    assert case.events.count("Document.Save") == case.events.count("Document.Close(True)") == 1
    saved = copy.deepcopy(case.cli._store_open().get(receipt["operation_id"]))
    assert saved["status"] == "unknown" and saved["receipt"] == receipt
    previous = list(case.requests)
    case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert case.requests == previous
    assert case.cli._store_open().get(receipt["operation_id"]) == saved


@pytest.mark.parametrize("case", ["custom-journal"], indirect=True)
def test_public_diagnostic_uses_its_custom_sdk_journal(case):
    from xpedition_cli.audit import config_dir

    assert not (config_dir() / "driver-v2" / "operations.sqlite3").exists()
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    applied = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not applied.envelope["ok"]
    receipt = applied.envelope["error"]["details"]["receipt"]
    assert receipt["extensions"]["independent_readback"]["status"] == "complete"
    assert case.events.count("Document.Save") == case.events.count("Document.Close(True)") == 1
    assert case.cli._store_open().get(receipt["operation_id"])["status"] == "unknown"


def test_public_other_journal_same_ownership_root_stops_before_cad(case):
    with runtime(
        Adapter(case.cli.adapter.backend), state_dir=case.tmp_path / "other-journal"
    ) as other:
        previous = list(case.requests)
        refused = other.run(
            [
                "pcb",
                "placement",
                "move",
                "--project",
                str(case.board),
                "--refdes",
                "R1",
                "--to",
                "1,2",
                "--dry-run",
                "--quiet",
            ]
        )
        assert not refused.envelope["ok"]
        assert refused.envelope["error"]["details"]["dispatch_state"] == "not_started"
        assert refused.envelope["error"]["details"]["pcb_document_guard"]["reason"] == (
            "journal_or_candidate_mismatch"
        )
        assert case.requests == previous


def test_public_alternate_project_partial_history_refuses_without_cad(case, monkeypatch):
    import time

    from xpedition_cli import driver

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    alias = case.tmp_path / "alternate.prj"
    alias.write_text(
        'SECTION iCDB\nLIST Designs\nVALUE "Main"\nENDLIST\nENDSECTION\n'
        'SECTION Main\nKEY ConfigType "PCB"\nKEY RootBlock "Root"\n'
        f'KEY PCBDesignPath "{case.board}"\nENDSECTION\n',
        encoding="utf-8",
    )
    _, move = _planned_alias_move(case, monkeypatch, target=alias)
    operation_id = move["preview"]["operation_id"]
    store = case.cli._store_open()
    record = store.get(operation_id)
    store.dispatch(
        operation_id,
        fingerprint="synthetic-partial-" + operation_id,
        token_expires=time.time() + 300,
        key=None,
        digest=record["request_digest"],
        target_keys=[record["target_key"]],
        lease=120,
    )
    cause = CLIError("E_NATIVE_FAILURE", "synthetic Place failure", {"partial": True})
    fields = driver._unobserved_receipt(
        NS(command="pcb placement move", effects=("change", "persist")),
        {"change_status": "partial", "persistence_state": "unsaved", "rollback_performed": False},
        cause,
        None,
    )
    receipt = case.cli._complete_receipt(fields, record, record["plan"], None)
    assert receipt is not None
    store.finish(operation_id, "partial", receipt)
    retained = copy.deepcopy(store.get(operation_id))
    previous, board = list(case.requests), case.board.read_bytes()
    refused = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not refused.envelope["ok"]
    assert refused.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert case.requests == previous and not case.dispatched
    assert "Document.Save" not in case.events and "Document.Close(True)" not in case.events
    assert store.get(operation_id) == retained and case.board.read_bytes() == board


def test_public_response_validation_holds_lock_and_preserves_original_receipt(case, monkeypatch):
    from xpedition_cli._vendor.tool_driver_sdk.validation import ContractError

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    with runtime(Adapter(case.cli.adapter.backend)) as other:
        args, move = _planned_alias_move(case, monkeypatch, other)
        original_validate = case.cli.validator.validate
        observed = {}

        def reject_final_response(kind, value):
            details = (
                (value.get("error") or {}).get("details") or {} if isinstance(value, dict) else {}
            )
            receipt = details.get("receipt")
            if (
                kind == "response"
                and isinstance(receipt, dict)
                and details.get("diagnostic_complete")
            ):
                observed["receipt"] = copy.deepcopy(receipt)
                requests = list(case.requests)
                observed["other"] = other.run([*args, "--confirm", move["confirm_token"]])
                assert case.requests == requests
                raise ContractError("synthetic final response rejection")
            return original_validate(kind, value)

        monkeypatch.setattr(case.cli.validator, "validate", reject_final_response)
        result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert not observed["other"].envelope["ok"]
        assert observed["other"].envelope["error"]["details"]["dispatch_state"] == "not_started"
        assert not result.envelope["ok"] and result.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
        operation_id = observed["receipt"]["operation_id"]
        record = case.cli._store_open().get(operation_id)
        assert record["status"] == "unknown" and record["receipt"] is None
        assert record["extra"]["rejected_receipt"] == observed["receipt"]
        marker = (
            case.cli._store_open()
            .connection()
            .execute(
                "SELECT used_by FROM pcb_close_qualification_v1 WHERE copy_operation_id=?",
                (case.origin["copy_operation_id"],),
            )
            .fetchone()
        )
        assert marker[0] == operation_id
        before = list(case.requests)
        refused = other.run([*args, "--confirm", move["confirm_token"]])
        assert not refused.envelope["ok"] and case.requests == before
        assert case.events.count("Document.Save") == case.events.count("Document.Close(True)") == 1


def test_public_qualification_record_failure_stops_before_save(case, monkeypatch):
    from xpedition_cli import pcb_document_guard as guard

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    operation_id = preview.envelope["data"]["preview"]["operation_id"]
    with monkeypatch.context() as scoped:
        scoped.setattr(
            guard,
            "mark_dispatched_copy_use",
            lambda *args: (_ for _ in ()).throw(OSError("synthetic storage failure")),
        )
        refused = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not refused.envelope["ok"]
    assert refused.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert refused.envelope["error"]["details"]["pcb_document_guard"]["reason"] == (
        "copy_use_record_failed"
    )
    assert case.cli._store_open().get(operation_id)["status"] == "planned"
    assert "Document.Save" not in case.events and "Document.Close(True)" not in case.events
    applied = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not applied.envelope["ok"]
    assert applied.envelope["error"]["details"]["diagnostic_complete"] is True
    assert case.events.count("Document.Save") == case.events.count("Document.Close(True)") == 1


def test_public_capacity_does_not_block_new_copy_or_existing_fresh_copy(case, monkeypatch):
    from xpedition_cli import pcb_document_guard as guard

    monkeypatch.setattr(guard, "MAX_COPY_QUALIFICATIONS", 1)
    second = case.tmp_path / "second-copy" / "Board.prj"
    args = [
        "project",
        "create",
        "--template",
        case.origin["origin_project"]["path"],
        "--project",
        str(second),
        "--source-backup",
        case.origin["source_backup"]["path"],
        "--closed-copy",
        "--dangerous",
        "--quiet",
    ]
    preview = case.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    applied = case.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert applied.envelope["ok"], applied.envelope
    assert applied.envelope["data"]["extensions"]["close_diagnostic_qualification"] == {
        "status": "unavailable",
        "reason": "qualification_capacity_reached",
    }
    original = case.cli.run([*case.args, "--dry-run"])
    assert original.envelope["ok"], original.envelope


def test_public_copy_qualification_io_failure_does_not_strand_unbound_pcb(
    startup,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    from xpedition_cli import pcb_document_guard as guard
    from xpedition_cli.audit import config_dir

    _scope(tmp_path, monkeypatch)
    source, archive, project, _ = source_fixture(tmp_path)
    args = [
        "project",
        "create",
        "--template",
        str(source),
        "--project",
        str(project),
        "--source-backup",
        str(archive),
        "--closed-copy",
        "--dangerous",
        "--quiet",
    ]
    with runtime(Adapter(startup.backend)) as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        with monkeypatch.context() as scoped:
            scoped.setattr(
                guard,
                "ensure_qualification_store",
                lambda *args, **kwargs: (_ for _ in ()).throw(OSError("synthetic table failure")),
            )
            applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert applied.envelope["ok"], applied.envelope
        assert applied.envelope["data"]["extensions"]["close_diagnostic_qualification"] == {
            "status": "unavailable",
            "reason": "qualification_record_failed",
        }
        assert not (config_dir() / "pcb-journal-binding-v1.json").exists()
        other = cli.run(
            ["session", "start", "--kind", "pcb", "--project", str(startup.board), "--dry-run"]
        )
        assert other.envelope["ok"], other.envelope
