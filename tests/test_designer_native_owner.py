"""Owner interface tests with fake COM boundaries, never a native COM import."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import designer_host_bootstrap as B
from xpedition_cli import designer_native_owner as O
from xpedition_cli.designer_owner_transport import ExpectedGeneration


@pytest.fixture
def pure_diagnostics(monkeypatch):
    import ctypes

    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Pure diagnostic tests must not initialize native code or scan processes")

    monkeypatch.setattr(O, "create_native_runtime", forbidden)
    monkeypatch.setattr(O.L, "_kernel", forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)

    yield
    assert hits == []


def test_complete_observation_does_not_keep_an_earlier_partial_read_reason(monkeypatch):
    calls = []

    def context(ticket):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("concurrent partial event")
        return Path("D:/fresh-fixture"), b"private-fixture-key", {}

    monkeypatch.setattr(O, "_context", context)
    monkeypatch.setattr(O, "_registration", lambda *args: {})
    monkeypatch.setattr(O.L, "_events", lambda *args: [{"phase": "owner_started", "details": {}}])
    monkeypatch.setattr(O.L, "_fresh_owner", lambda *args: ("verified_alive", None))
    monkeypatch.setattr(O.time, "sleep", lambda *args: None)
    result = O.observe_owner(object(), timeout_seconds=0.1)
    assert len(calls) == 2
    assert result["phase"] == "owner_started" and result["status"] == "unknown"
    assert result["cleanup_verified"] is None and "reason" not in result


class Raw:
    def __init__(self):
        self.unknown = object()
        self.invocations = []

    def QueryInterface(self, iid, *result_interface):
        self.invocations.append(("QI", iid))
        return self.unknown

    def GetIDsOfNames(self, lcid, name):
        self.invocations.append(("member", name))
        return {"ActiveDocument": 208, "Quit": 226}.get(name, 42)

    def InvokeTypes(self, *arguments):
        self.invocations.append(("invoke", arguments))


def _application(monkeypatch, *, opened=False):
    raw, consumed = Raw(), []
    generation = {
        "pid": 123,
        "birth_filetime_100ns": "100",
        "executable": r"C:\licensed\viewdraw.exe",
        "alive": True,
    }
    lease = SimpleNamespace(snapshot=lambda: dict(generation))
    expected = ExpectedGeneration(123, "100", r"C:\licensed\viewdraw.exe")

    def consume(stream, iid):
        consumed.append((stream, iid, threading.current_thread()))
        return raw

    com = SimpleNamespace(
        IID_IDispatch="dispatch",
        IID_IUnknown="unknown",
        VT_EMPTY=0,
        VT_DISPATCH=9,
        CoGetInterfaceAndReleaseStream=consume,
    )
    monkeypatch.setattr(
        B, "_role", lambda *args: {"library_name": "ViewDraw", "library_major": 151}
    )
    monkeypatch.setattr(
        B,
        "_native_project",
        lambda *args: {"open": opened, "path": r"c:\scope\board.prj" if opened else None},
    )
    application = O.NativeApplication("trusted-stream", com, expected, lease, r"C:\scope\board.prj")
    return application, raw, consumed, generation


def test_owner_gate_remains_deny_and_no_arbitrary_command_exists(monkeypatch):
    assert O.native_owner_enabled() is False
    monkeypatch.setattr(
        O, "_context", lambda *args: pytest.fail("invalid command must not read runtime")
    )
    with pytest.raises(O.OwnerError, match="fixed_owner_command_required"):
        O.submit_owner_command(None, "eval-python")
    assert O.COMMANDS == ("identity", "read_project", "save_close_project", "quit_empty")


def test_owner_sta_consumes_once_and_probes_retained_same_object(monkeypatch):
    application, raw, consumed, _ = _application(monkeypatch)
    first = application.probe()
    second = application.probe()
    assert len(consumed) == 1
    assert consumed[0][:2] == ("trusted-stream", "dispatch")
    assert consumed[0][2] is threading.current_thread()
    assert first["canonical_stable"] is second["canonical_stable"] is True
    assert first["native_project_open"] is False
    assert r"C:\\scope\\board.prj" not in json.dumps(first)
    assert application.raw is raw


def test_owner_thread_cannot_be_substituted_by_equal_numeric_ident(monkeypatch):
    application, raw, _, _ = _application(monkeypatch)
    count = len(raw.invocations)
    application._thread = object()
    with pytest.raises(O.OwnerError, match="native_application_owner_sta_required"):
        application.probe()
    assert len(raw.invocations) == count


def test_original_generation_loss_and_canonical_change_reject_probe(monkeypatch):
    application, raw, _, generation = _application(monkeypatch)
    generation["birth_filetime_100ns"] = "101"
    with pytest.raises(O.OwnerError, match="native_generation_before_probe_mismatch"):
        application.probe()
    generation["birth_filetime_100ns"] = "100"
    raw.unknown = object()
    with pytest.raises(O.OwnerError, match="retained_application_canonical_changed"):
        application.probe()


def test_foreign_role_and_real_project_difference_are_rejected(monkeypatch):
    application, _, _, _ = _application(monkeypatch)

    def foreign_role(*args):
        raise B.BootstrapGap("bootstrap_current_viewdraw_151_role_required")

    monkeypatch.setattr(B, "_role", foreign_role)
    with pytest.raises(B.BootstrapGap, match="current_viewdraw_151_role_required"):
        application.probe()
    monkeypatch.setattr(B, "_role", lambda *args: {"library_name": "ViewDraw"})
    monkeypatch.setattr(
        B, "_native_project", lambda *args: {"open": True, "path": "foreign-project"}
    )
    with pytest.raises(O.OwnerError, match="native_project_outside_authorized_scope"):
        application.probe()


def test_empty_quit_is_one_dispatch_and_exit_remains_separate(monkeypatch):
    application, raw, _, _ = _application(monkeypatch)
    result = application.quit_empty()
    assert application.quit_attempted is True
    assert application.quit_returned is True
    assert result == {"quit_reply_received": True, "process_exit_verified": None}
    invokes = [item for item in raw.invocations if item[0] == "invoke"]
    assert len(invokes) == 2
    assert invokes[0][1] == (208, 0, 2, (9, 0), ())
    assert invokes[1][1] == (226, 0, 1, (0, 0), ())
    with pytest.raises(O.OwnerError, match="native_quit_already_dispatched"):
        application.quit_empty()
    application.release()
    assert application.raw is application.unknown is None


def test_open_project_cannot_be_quit_as_empty(monkeypatch):
    application, raw, _, _ = _application(monkeypatch)
    monkeypatch.setattr(application, "probe", lambda: {"native_project_open": True})
    with pytest.raises(O.OwnerError, match="fresh_empty_project_required_before_quit"):
        application.quit_empty()
    assert application.quit_attempted is False
    assert not any(item[0] == "invoke" for item in raw.invocations)


def test_quit_exception_preserves_attempt_and_never_claims_reply(monkeypatch):
    application, raw, _, _ = _application(monkeypatch)

    def fail(*args):
        if args[0] == 208:
            return None
        raise RuntimeError("untrusted native detail")

    monkeypatch.setattr(raw, "InvokeTypes", fail)
    with pytest.raises(RuntimeError):
        application.quit_empty()
    assert application.quit_attempted is True
    assert application.quit_returned is None


def _lifecycle_application(monkeypatch, *, failure=None, close_result=True, remains_open=False):
    application, raw, _, _ = _application(monkeypatch, opened=True)
    state = {"open": True, "path": B.W._path(application.project)}
    calls, events = [], []
    documents = Raw()
    documents.declared_save_id = 10
    application.com.VT_DISPATCH = 9
    application.com.VT_BOOL = 11
    application.com.TypeIIDs = {application.com.IID_IDispatch: Raw}
    monkeypatch.setattr(B, "_native_project", lambda *args: dict(state))
    monkeypatch.setattr(
        raw, "GetIDsOfNames", lambda lcid, name: {"Documents": 1, "CloseProject": 274}[name]
    )
    monkeypatch.setattr(documents, "GetIDsOfNames", lambda lcid, name: 10)

    def app_invoke(member, lcid, flags, result_type, arguments):
        if member == 1:
            assert flags == 2 and result_type == (9, 0) and arguments == ()
            return documents
        assert member == 274 and flags == 1 and result_type == (11, 0) and arguments == ()
        calls.append("CloseProject")
        if failure == "close":
            raise RuntimeError("synthetic close reply missing")
        state["open"] = remains_open
        state["path"] = state["path"] if remains_open else None
        return close_result

    def save_invoke(member, lcid, flags, result_type, arguments):
        assert member == 10 and flags == 1 and result_type == (0, 0) and arguments == ()
        calls.append("SaveAll")
        if failure == "save":
            raise RuntimeError("synthetic save reply missing")
        if failure == "scope_drift":
            state["path"] = "foreign-project"

    monkeypatch.setattr(raw, "InvokeTypes", app_invoke)
    monkeypatch.setattr(documents, "InvokeTypes", save_invoke)

    def fixed_save(operation, argument=None, *, on_dispatch=None, on_return=None):
        # This fixture isolates save/close sequencing at the new fixed-member
        # bridge boundary. Opaque grant/typelib/ABI guards have dedicated tests.
        assert operation == "save_all" and argument is None
        if documents.declared_save_id != 10 or documents.QueryInterface(O.DOCS_IID) is None:
            raise O.OwnerError("fixed_fixture_document_contract_rejected")
        on_dispatch()
        documents.InvokeTypes(10, 0, 1, (0, 0), ())
        on_return()

    monkeypatch.setattr(application, "_invoke_fixed_document", fixed_save)
    return application, calls, events, state, documents


def test_save_close_exact_scope_records_separate_native_stages(monkeypatch):
    application, calls, events, _, documents = _lifecycle_application(monkeypatch)
    result = application.save_close_project(lambda phase, facts: events.append((phase, facts)))
    assert calls == ["SaveAll", "CloseProject"]
    assert documents.invocations == [("QI", O.DOCS_IID)]
    assert [phase for phase, _ in events] == [
        "save_call_intent_recorded",
        "save_call_returned",
        "close_call_intent_recorded",
        "close_call_returned",
        "post_close_probe_returned",
    ]
    assert events[0][1]["save_call_attempted"] is False
    assert events[1][1]["save_call_returned"] is True
    assert events[2][1]["close_call_attempted"] is False
    assert result == {
        "save_call_attempted": True,
        "save_call_returned": True,
        "save_persistence_verified": None,
        "close_call_attempted": True,
        "close_call_returned": True,
        "close_result_bool": True,
        "post_close_empty_observed": True,
    }
    with pytest.raises(O.OwnerError, match="native_save_close_already_started_no_replay"):
        application.save_close_project()
    assert calls == ["SaveAll", "CloseProject"]


@pytest.mark.parametrize(
    "failure,expected_calls",
    [("save", ["SaveAll"]), ("close", ["SaveAll", "CloseProject"]), ("scope_drift", ["SaveAll"])],
)
def test_partial_save_close_preserves_unknown_and_stops_further_writes(
    monkeypatch, failure, expected_calls
):
    application, calls, _, _, _ = _lifecycle_application(monkeypatch, failure=failure)
    with pytest.raises((RuntimeError, O.OwnerError)):
        application.save_close_project()
    observed = application.lifecycle_snapshot()
    assert calls == expected_calls
    assert observed["save_call_attempted"] is True
    assert observed["save_call_returned"] is (None if failure == "save" else True)
    assert observed["close_call_attempted"] is (failure == "close")
    assert observed["close_call_returned"] is None
    assert observed["post_close_empty_observed"] is None
    assert observed["save_persistence_verified"] is None
    with pytest.raises(O.OwnerError, match="native_save_close_already_started_no_replay"):
        application.save_close_project()
    assert calls == expected_calls


def test_save_close_refuses_empty_unscoped_and_foreign_targets_before_write(monkeypatch):
    application, calls, _, state, _ = _lifecycle_application(monkeypatch)
    state.update(open=False, path=None)
    with pytest.raises(O.OwnerError, match="fresh_exact_open_project_required_before_save_close"):
        application.save_close_project()
    state.update(open=True, path="foreign-project")
    with pytest.raises(O.OwnerError, match="native_project_outside_authorized_scope"):
        application.save_close_project()
    application.project = None
    with pytest.raises(O.OwnerError, match="authorized_project_required_before_save_close"):
        application.save_close_project()
    assert calls == []
    assert application.lifecycle_snapshot()["save_call_attempted"] is False


@pytest.mark.parametrize(
    "closed,remains_open", [(False, False), (True, True), (None, False), (1, False)]
)
def test_close_reply_and_empty_readback_cannot_substitute_for_each_other(
    monkeypatch, closed, remains_open
):
    application, calls, _, _, _ = _lifecycle_application(
        monkeypatch, close_result=closed, remains_open=remains_open
    )
    with pytest.raises(O.OwnerError):
        application.save_close_project()
    observed = application.lifecycle_snapshot()
    assert calls == ["SaveAll", "CloseProject"]
    assert observed["close_call_returned"] is True
    assert observed["close_result_bool"] is (closed if type(closed) is bool else None)
    assert observed["post_close_empty_observed"] is (
        not remains_open if type(closed) is bool else None
    )


def test_save_close_log_failure_before_native_call_cannot_claim_dispatch(monkeypatch):
    application, calls, _, _, _ = _lifecycle_application(monkeypatch)

    def fail(phase, facts):
        raise OSError("synthetic event failure")

    with pytest.raises(OSError):
        application.save_close_project(fail)
    assert calls == []
    assert application.lifecycle_snapshot()["save_call_attempted"] is False
    with pytest.raises(O.OwnerError, match="native_save_close_already_started_no_replay"):
        application.save_close_project()


@pytest.mark.parametrize("invalid", ["save_id", "close_id", "docs_interface"])
def test_save_close_validates_all_declared_interfaces_and_members_before_save(monkeypatch, invalid):
    application, calls, _, _, documents = _lifecycle_application(monkeypatch)
    if invalid == "save_id":
        documents.declared_save_id = 999
    elif invalid == "close_id":
        monkeypatch.setattr(
            application.raw, "GetIDsOfNames", lambda lcid, name: 1 if name == "Documents" else 999
        )
    else:
        monkeypatch.setattr(documents, "QueryInterface", lambda *args: None)
    with pytest.raises(O.OwnerError):
        application.save_close_project()
    assert calls == []
    assert application.lifecycle_snapshot()["save_call_attempted"] is False
    assert application.lifecycle_snapshot()["close_call_attempted"] is False


def test_legacy_operation_query_does_not_adopt_or_touch_a_runtime(monkeypatch):
    monkeypatch.setattr(
        O, "_context", lambda *args: pytest.fail("legacy ID must not access runtime")
    )
    result = O.observe_owner_command(None, "op-existing-unknown")
    assert result == {
        "status": "legacy_unmanaged_unknown",
        "original_outcome": "unknown",
        "adopted": False,
    }


def test_owner_command_observer_keeps_original_id_on_snapshot_race(monkeypatch, pure_diagnostics):
    command_id, epoch = "owner-command-" + "b" * 32, "a" * 32
    initial = {
        "command_id": command_id,
        "epoch": epoch,
        "original_outcome": "unknown",
        "immutable": True,
    }
    original = json.dumps(initial, sort_keys=True)
    ticket = SimpleNamespace(epoch=epoch)
    monkeypatch.setattr(O, "_context", lambda ticket: (Path("D:/new-fixture"), b"k" * 32, {}))
    monkeypatch.setattr(O.L, "_read", lambda *a: (initial, "fixture-digest"))
    monkeypatch.setattr(
        O.L,
        "_events",
        lambda *a: (_ for _ in ()).throw(O.L.LifecycleError("execution_snapshot_not_complete")),
    )
    monkeypatch.setattr(O.L, "_write_new", lambda *a: pytest.fail("Observation must not publish"))
    answer = O.observe_owner_command(ticket, command_id)
    assert answer["command_id"] == command_id and answer["status"] == "unknown"
    assert answer["observation"] == "unobserved" and answer["request_replayed"] is False
    assert answer["initial_observation"]["original_outcome"] == "unknown"
    assert json.dumps(initial, sort_keys=True) == original


def test_owner_command_observer_recovers_same_id_without_sticky_incomplete_reason(
    monkeypatch, pure_diagnostics
):
    command_id, epoch = "owner-command-" + "b" * 32, "a" * 32
    initial = {
        "command_id": command_id,
        "epoch": epoch,
        "original_outcome": "unknown",
        "immutable": True,
    }
    calls = []
    monkeypatch.setattr(O, "_context", lambda ticket: (Path("D:/new-fixture"), b"k" * 32, {}))
    monkeypatch.setattr(O.L, "_read", lambda *a: (initial, "fixture-digest"))

    def events(*args):
        calls.append(True)
        if len(calls) == 1:
            raise O.L.LifecycleError("execution_snapshot_not_complete")
        return [{"phase": "command_returned", "details": {"command_id": command_id}}]

    monkeypatch.setattr(O.L, "_events", events)
    monkeypatch.setattr(O, "observe_owner", lambda *a: {"status": "unknown"})
    answer = O.observe_owner_command(SimpleNamespace(epoch=epoch), command_id, timeout_seconds=0.2)
    assert len(calls) == 2
    assert answer["command_id"] == command_id and "reason" not in answer
    assert answer["observations_current"] is True
    assert answer["observations"][-1]["phase"] == "command_returned"
    assert answer["initial_observation"]["original_outcome"] == "unknown"


def test_real_observer_child_reads_append_snapshot_gap_then_same_terminal_id(tmp_path):
    """Real child/file IPC only; no owner launch, CAD, COM or PID query."""
    from xpedition_cli import execution_lifecycle as L

    root, key, epoch = tmp_path / "synthetic-command", b"k" * 32, "a" * 32
    command_id = "owner-command-" + "b" * 32
    directory = root / "commands" / command_id
    directory.mkdir(parents=True)
    (root / "events").mkdir()
    intent = {"execution_id": "designer-owner-" + "c" * 32, "execution_epoch": epoch}
    initial = {
        "command_id": command_id,
        "epoch": epoch,
        "original_outcome": "unknown",
        "immutable": True,
    }
    L._write_new(directory / "initial.json", L._pack(initial, key))
    L._write_new(
        directory / "request.json",
        L._pack({"command_id": command_id, "command": "quit_empty"}, key),
    )
    before = [(directory / name).read_bytes() for name in ("initial.json", "request.json")]
    event = {
        **intent,
        "sequence": 1,
        "previous_sha256": None,
        "phase": "command_started",
        "details": {"command_id": command_id},
    }
    raw = L._pack(event, key)
    L._write_new(root / "events/event-0001.json", raw)
    L._write_new(
        root / "snapshot.json", L._pack({**intent, "sequence": 0, "event_sha256": None}, key)
    )
    code = r"""
import ctypes,json,sys,time
from pathlib import Path
def forbidden(*a,**k):raise AssertionError("No native code in file observer child")
ctypes.OleDLL=forbidden
sys.path.insert(0,sys.argv[1])
from xpedition_cli import designer_native_owner as O
root=Path(sys.argv[2]);epoch="a"*32;cid="owner-command-"+"b"*32
intent={"execution_id":"designer-owner-"+"c"*32,"execution_epoch":epoch}
O._context=lambda ticket:(root,b"k"*32,intent)
O.observe_owner=lambda *a,**k:{"status":"unknown","owner_state":"unobservable"}
O.submit_owner_command=forbidden
O.create_native_runtime=O.L._kernel=forbidden
ticket=O.OwnerTicket(root,epoch)
first=O.observe_owner_command(ticket,cid)
(root/"reader-first.json").write_text(json.dumps(first),encoding="utf-8")
(root/"reader-ready").write_bytes(b"ready")
deadline=time.monotonic()+3
while not (root/"publisher-ready").exists() and time.monotonic()<deadline:time.sleep(.01)
second=O.observe_owner_command(ticket,cid,timeout_seconds=.5)
print(json.dumps({"first":first,"second":second}))
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-c",
            code,
            str(Path(__file__).resolve().parents[1]),
            str(root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + 3
        while not (root / "reader-ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (root / "reader-ready").is_file()
        first = json.loads((root / "reader-first.json").read_text(encoding="utf-8"))
        assert first["command_id"] == command_id and first["observation"] == "unobserved"
        returned = {
            **intent,
            "sequence": 2,
            "previous_sha256": hashlib.sha256(raw).hexdigest(),
            "phase": "command_returned",
            "details": {"command_id": command_id, "quit_reply_received": True},
        }
        published = L._pack(returned, key)
        L._write_new(root / "events/event-0002.json", published)
        # Deliberate synthetic publisher completion after the child observed
        # the actual valid-event/lagging-snapshot window. No L writer is changed.
        (root / "snapshot.json").write_bytes(
            L._pack(
                {**intent, "sequence": 2, "event_sha256": hashlib.sha256(published).hexdigest()},
                key,
            )
        )
    finally:
        (root / "publisher-ready").write_bytes(b"published")
        stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 0, stderr
    second = json.loads(stdout)["second"]
    assert second["command_id"] == command_id and second["observation"] == "snapshot_complete"
    assert second["observations"][-1]["phase"] == "command_returned"
    assert second["initial_observation"]["original_outcome"] == "unknown"
    assert [(directory / name).read_bytes() for name in ("initial.json", "request.json")] == before
    assert len(list((root / "commands").iterdir())) == 1


def test_failure_diagnostics_keep_known_reason_and_redact_external_text(pure_diagnostics):
    own = O._failure_facts(
        O.OwnerError("native_project_outside_authorized_scope"), "owner.initial_native_probe", True
    )
    assert own["reason"] == "native_project_outside_authorized_scope"
    assert own["dispatched"] is True
    external = O._failure_facts(
        OSError(5, "private endpoint objref:secret and path"), "owner.external", None
    )
    assert external["errno"] == 5
    assert external["reason"] == "external_native_failure"
    assert "objref" not in json.dumps(external)
    assert "private endpoint" not in json.dumps(external)


def _project_reader(monkeypatch):
    owner, _, _, generation = _application(monkeypatch, opened=True)
    calls = []

    class Node:
        def __init__(self, role, members, unknown=None):
            self.role, self.members, self.unknown = role, members, unknown or object()
            self.count_override = None

        def QueryInterface(self, iid, *args):
            calls.append((self.role, "QI", iid))
            return self.unknown

        def GetIDsOfNames(self, lcid, name):
            assert lcid == 0
            return self.members[name][0]

        def InvokeTypes(self, dispid, lcid, kind, result, arguments, *values):
            name, spec = next(
                (name, spec) for name, spec in self.members.items() if spec[0] == dispid
            )
            calls.append((self.role, name, kind, result, arguments, values))
            assert lcid == 0 and kind == (2 if name in {"Count", "Application"} else 1)
            return spec[1](*values)

    def strings(role, values):
        node = Node(role, {})
        node.members = {
            "GetCount": (
                1,
                lambda: len(values) if node.count_override is None else node.count_override,
            ),
            "GetItem": (2, lambda index: values[index - 1]),
        }
        return node

    designs = strings("designs", ["RealDesign"])
    names = strings("schematics", ["RealSchematic"])
    sheets = strings("sheets", ["1", "NamedSheet"])
    app = Node("app", {}, owner.unknown)
    data = Node(
        "project",
        {
            "GetProjectFilePath": (3, lambda: r"C:\scope\board.prj"),
            "GetiCDBDesigns": (9, lambda: designs),
        },
    )
    docs = Node(
        "open-documents",
        {
            "Application": (102, lambda: app),
            "Count": (101, lambda: 0),
            "GetAvailableSchematics": (108, lambda: names),
            "GetAvailableSheets": (109, lambda name: sheets if name == "RealSchematic" else None),
        },
    )
    app.members = {
        "IsProjectOpened": (295, lambda: True),
        "GetProjectData": (228, lambda: data),
        "SchematicSheetDocuments": (212, lambda: docs),
    }
    owner.raw = app
    owner.com.TypeIIDs = {owner.com.IID_IDispatch: Node}
    owner.com.VT_I4, owner.com.VT_BSTR, owner.com.VT_DISPATCH, owner.com.VT_BOOL = 3, 8, 9, 11
    return SimpleNamespace(
        owner=owner,
        calls=calls,
        designs=designs,
        names=names,
        sheets=sheets,
        docs=docs,
        data=data,
        app=app,
        generation=generation,
    )


@pytest.mark.parametrize("opened", [True, False])
def test_read_project_reads_real_object_layers_without_opening_or_collapsing_them(
    monkeypatch, pure_diagnostics, opened
):
    scene = _project_reader(monkeypatch)
    scene.app.members["IsProjectOpened"] = (295, lambda: opened)
    result = scene.owner.read_project()
    assert result["project"] == {
        "open": opened,
        "path": r"C:\scope\board.prj",
        "status": "independent_observations",
    }
    assert all(value is True for value in result["integrity"].values())
    assert result["icdb_designs"] == ["RealDesign"]
    assert result["available_schematics"] == [
        {"schematic": "RealSchematic", "sheets": ["1", "NamedSheet"]}
    ]
    assert result["open_sheet_document_count"] == 0 and result["available_sheet_count"] == 2
    assert result["native_writer_admitted"] is False and result["lists_observation"] == "complete"
    roles = result["observations"]["document_dispatch_roles"]["value"]
    assert roles["diagnostic_only"] is True and roles["native_writer_admitted"] is False
    assert roles["collection_member_invocations"] == 0
    assert all(
        row[1] not in {"Open", "Add", "Activate", "Item", "SaveAll", "Quit"} for row in scene.calls
    )
    item = next(row for row in scene.calls if row[:2] == ("designs", "GetItem"))
    assert item[3:] == ((8, 0), ((3, 1),), (1,))
    available = next(
        row for row in scene.calls if row[:2] == ("open-documents", "GetAvailableSheets")
    )
    assert available[3:] == ((9, 0), ((8, 1),), ("RealSchematic",))


def test_read_project_closed_project_preserves_unobserved_lists(monkeypatch, pure_diagnostics):
    owner, raw, _, _ = _application(monkeypatch, opened=False)
    result = owner.read_project()
    assert result["icdb_designs"] is result["available_schematics"] is None
    assert result["open_sheet_document_count"] is None
    assert result["lists_observation"] == "incomplete"
    assert result["observations"]["project_data"]["status"] == "unsupported"
    assert any(row[:2] == ("member", "GetProjectData") for row in raw.invocations)
    assert not any(row[0] == "invoke" for row in raw.invocations)


@pytest.mark.parametrize("count", [True, -1, 65])
def test_read_project_rejects_invalid_or_unbounded_count_before_items(
    monkeypatch, count, pure_diagnostics
):
    scene = _project_reader(monkeypatch)
    scene.designs.count_override = count
    result = scene.owner.read_project()
    assert result["icdb_designs"] is None
    assert result["observations"]["icdb_designs"]["status"] == "error"
    assert (
        result["observations"]["icdb_designs"]["reason"]
        == "native_read_project_list_count_invalid_or_over_limit"
    )
    assert not any(row[:2] == ("designs", "GetItem") for row in scene.calls)


def test_read_project_does_not_return_a_partial_list_after_getter_failure(
    monkeypatch, pure_diagnostics
):
    scene = _project_reader(monkeypatch)
    scene.designs.members["GetCount"] = (1, lambda: 2)

    def item(index):
        if index == 2:
            raise OSError("secret endpoint/path not retained")
        return "first-native-name"

    scene.designs.members["GetItem"] = (2, item)
    result = scene.owner.read_project()
    assert result["icdb_designs"] is None
    assert result["observations"]["icdb_designs"]["status"] == "error"
    assert "secret" not in json.dumps(result)
    assert any(row[:2] == ("app", "SchematicSheetDocuments") for row in scene.calls)


@pytest.mark.parametrize("change", ["path", "application", "count_drift", "generation"])
def test_read_project_rejects_scope_identity_or_drift(monkeypatch, change, pure_diagnostics):
    scene = _project_reader(monkeypatch)
    if change == "path":
        scene.data.members["GetProjectFilePath"] = (3, lambda: r"C:\foreign\board.prj")
    elif change == "application":
        foreign = type(scene.app)("foreign-app", {}, object())
        scene.docs.members["Application"] = (102, lambda: foreign)
    elif change == "count_drift":
        counts = iter([1, 0])
        scene.designs.members["GetCount"] = (1, lambda: next(counts))
    else:

        def after_count():
            scene.generation["alive"] = False
            return 0

        scene.docs.members["Count"] = (101, after_count)
    if change == "generation":
        with pytest.raises(O.OwnerError, match="identity_after_mismatch"):
            scene.owner.read_project()
    else:
        result = scene.owner.read_project()
        if change == "path":
            assert result["project"]["path"] is None
            assert result["observations"]["project_path"]["outside_bound_project"] is True
            assert "foreign" not in json.dumps(result)
        elif change == "application":
            assert result["observations"]["sheet_documents"]["status"] == "error"
            assert result["available_schematics"] is None
        else:
            assert result["observations"]["icdb_designs"]["status"] == "error"
            assert result["icdb_designs"] is None


def test_source_binding_includes_startup_seal_using_only_fake_paths(monkeypatch, pure_diagnostics):
    observed = []

    class SourcePath:
        def __init__(self, name):
            self.name = name

        def with_name(self, name):
            return SourcePath(name)

        def is_file(self):
            return True

        def read_bytes(self):
            observed.append(self.name)
            return ("fixed-source:" + self.name).encode("ascii")

    monkeypatch.setattr(O, "Path", SourcePath)
    binding = O._source_binding()
    assert (
        binding["startup_input_seal.py"]
        == hashlib.sha256(b"fixed-source:startup_input_seal.py").hexdigest()
    )
    assert observed.count("startup_input_seal.py") == 1
    assert len(binding) == len(observed) == 13
    assert (
        binding["designer_dual_invoke.py"]
        == hashlib.sha256(b"fixed-source:designer_dual_invoke.py").hexdigest()
    )
    assert observed.count("designer_dual_invoke.py") == 1
    assert {
        "designer_owner_dispatch.py",
        "native_com_adapter.py",
        "designer_source_binding.py",
        "schematic_edit_protection.py",
    } <= set(binding)


@pytest.mark.parametrize("value", [5, None, "5", True, 5.0])
def test_failure_winerror_is_only_an_integer_or_unobserved(value, pure_diagnostics):
    error = OSError("private path and objref:not-exported")
    error.winerror = value
    facts = O._failure_facts(error, "owner.launch", None)
    assert facts["winerror"] == (value if type(value) is int else None)
    assert "private path" not in json.dumps(facts)


def test_launch_failure_details_accept_only_fixed_safe_fields_and_keep_unknown(
    monkeypatch, pure_diagnostics
):
    from xpedition_cli.designer_native_launch import LaunchError

    error = LaunchError("dedicated_native_launch_incomplete")
    error.component_stage = "startup.create_inputs"
    error.component_reason = "startup_input_access_mask_mismatch"
    error.winerror = 5
    error.filename = "D:/private/endpoint-and-epoch"
    error.__cause__ = OSError("objref:private-capability")
    facts = O._failure_facts(error, "owner.native_launch", None)
    assert facts["component_stage"] == "startup.create_inputs"
    assert facts["component_reason"] == "startup_input_access_mask_mismatch"
    assert facts["winerror"] == 5 and facts["dispatched"] is None
    assert "private" not in json.dumps(facts) and "objref" not in json.dumps(facts)
    monkeypatch.setattr(O, "_context", lambda *a: pytest.fail("An old unknown runtime was read"))
    assert O.observe_owner_command(None, "op-original-unknown") == {
        "status": "legacy_unmanaged_unknown",
        "original_outcome": "unknown",
        "adopted": False,
    }


@pytest.mark.parametrize(
    "value", ["D:/private/epoch", "a" * 32, "startup." + "a" * 32, "startup.注入", None, True]
)
def test_launch_component_attrs_refuse_paths_epochs_and_non_whitelisted_ids(
    value, pure_diagnostics
):
    from xpedition_cli.designer_native_launch import LaunchError

    error = LaunchError("dedicated_native_launch_incomplete")
    error.component_stage = error.component_reason = value
    facts = O._failure_facts(error, "owner.native_launch", True)
    assert facts["component_stage"] is facts["component_reason"] is None
    assert facts["reason"] == "dedicated_native_launch_incomplete"
    assert facts["dispatched"] is True


def test_nonlaunch_exception_cannot_supply_component_fields(pure_diagnostics):
    error = O.OwnerError("fixed_owner_failure")
    error.component_stage = "startup.spawn"
    error.component_reason = "dedicated_native_launch_incomplete"
    facts = O._failure_facts(error, "owner.native_launch", False)
    assert "component_stage" not in facts and "component_reason" not in facts


@pytest.mark.parametrize("with_inputs", [False, True])
def test_private_launch_binding_keeps_input_seal_facts_out_of_public_result(
    monkeypatch, with_inputs, pure_diagnostics
):
    protection = {"files": [{"name": "identity.vbs", "sha256": "b" * 64, "file_id": [1, 2]}]}
    calls, payloads, writes = [], [], []
    lease = SimpleNamespace(startup_provenance={"fixture": "original-launch"})
    if with_inputs:
        lease.inputs = SimpleNamespace(private_binding=lambda: calls.append(True) or protection)
    monkeypatch.setattr(
        O.L,
        "_pack",
        lambda value, key: payloads.append((value, key)) or b"fixture-private-mac-record",
    )
    monkeypatch.setattr(O.L, "_write_new", lambda path, raw: writes.append((path, raw)))
    expected = ExpectedGeneration(123, "100", r"C:\licensed\viewdraw.exe")
    result = O._launch_evidence(Path("D:/new-fixture-only"), b"fixture-key", expected, lease)
    assert payloads[0][0]["startup_input_protection"] == (protection if with_inputs else None)
    assert calls == ([True] if with_inputs else [])
    assert len(writes) == 1
    assert set(result) == {
        "pid",
        "birth_filetime_100ns",
        "executable_sha256",
        "private_launch_evidence_sha256",
    }
    assert "identity.vbs" not in json.dumps(result) and "file_id" not in json.dumps(result)


def test_failed_resource_close_remains_owned_and_is_not_retried(monkeypatch):
    calls, failures = [], []

    class FixtureAbort(BaseException):
        pass

    def close():
        calls.append("close")
        raise O.OwnerError("synthetic_close_failed")

    steps = []

    def sleep(seconds):
        steps.append(seconds)
        if len(steps) == 2:
            raise FixtureAbort()  # Stop only this pure fixture observation.

    monkeypatch.setattr(O, "time", SimpleNamespace(sleep=sleep))
    lease = SimpleNamespace(
        close=close,
        snapshot=lambda: {
            "worker_generation_observed": True,
            "alive": False,
            "launcher_alive": False,
        },
    )
    with pytest.raises(FixtureAbort):
        O._retain_launch_resources(
            lease, True, lambda: None, on_failure=lambda error: failures.append(str(error))
        )
    assert calls == ["close"]
    assert failures == ["synthetic_close_failed"]


def test_join_writer_and_pump_failures_cannot_skip_lease_retention(monkeypatch):
    calls = []

    class FixtureAbort(BaseException):
        pass

    class Broker:
        joins = 0

        def is_alive(self):
            return self.joins < 2

        def join(self, timeout):
            self.joins += 1
            calls.append("join")

    def report(*args, **kwargs):
        calls.append("report_failed")
        raise OSError("synthetic journal failure")

    def pump():
        calls.append("pump_failed")
        raise RuntimeError("synthetic pump failure")

    def snapshot():
        calls.append("retained_lease_observed")
        return {"alive": None, "launcher_alive": None, "worker_generation_observed": False}

    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise FixtureAbort()  # Test observation ends; no product cancellation.

    monkeypatch.setattr(O, "time", SimpleNamespace(sleep=sleep))
    broker = Broker()
    O._join_broker(
        broker,
        pump,
        {"broker_cleanup_failed": True, "broker_error": {}},
        SimpleNamespace(append=report),
    )
    assert broker.joins == 2
    lease = SimpleNamespace(snapshot=snapshot, close=lambda: pytest.fail("null must retain lease"))
    with pytest.raises(FixtureAbort):
        O._retain_launch_resources(lease, True, pump)
    assert calls.count("report_failed") == 1
    assert calls.count("retained_lease_observed") == 2
    assert calls.count("pump_failed") == 4


@pytest.mark.parametrize("budget", [-1, 2.01, True, "1", float("inf"), float("nan")])
def test_observer_budget_is_finite_and_bounded(budget):
    with pytest.raises(O.OwnerError, match="bounded_owner_observation_required"):
        O._budget(budget)


@pytest.mark.parametrize(
    "callpoint,expected",
    [
        ("SetFileInformationByHandle", "SetFileInformationByHandle"),
        ("NtSetInformationFile", "NtSetInformationFile"),
        ("CloseHandle", "CloseHandle"),
        ("private-path/objref-secret", None),
        ({"private": "capability"}, None),
        (None, None),
    ],
)
def test_snapshot_failure_callpoint_is_fixed_or_unobserved(callpoint, expected):
    error = OSError(13, "private exception content")
    error.snapshot_callpoint = callpoint
    facts = O._failure_facts(error, "owner.broker_start", False)
    assert facts["snapshot_callpoint"] == expected
    assert facts["dispatched"] is False
    assert "private" not in json.dumps(facts)


@pytest.mark.skipif(os.name != "nt", reason="Own Windows helper and byte lock only")
@pytest.mark.parametrize(
    "scenario,expected_exit",
    [("spawned_unresolved", 21), ("unknown_spawn", 23), ("writer_failure", 21)],
)
def test_partial_native_launch_failure_retains_real_owner_and_resource(
    tmp_path, scenario, expected_exit
):
    import msvcrt

    from xpedition_cli import execution_lifecycle as L

    root = tmp_path / scenario
    L._private_mkdir(root)
    key, epoch = secrets.token_bytes(32), secrets.token_hex(16)
    L._write_new(root / "integrity-key.bin", key)
    intent = {
        "component": O.COMPONENT,
        "execution_id": "designer-owner-" + secrets.token_hex(16),
        "execution_epoch": epoch,
        "locator_sha256": L._locator(root),
        "source_sha256": O._source_binding(),
        "plan": {"fixture_only": scenario},
    }
    L._write_new(root / "intent.json", L._pack(intent, key))
    for folder in ("events", "commands"):
        L._private_mkdir(root / folder)
    ticket = O.OwnerTicket(root, epoch)
    child = subprocess.Popen(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(Path(__file__).parent / "fixtures" / "designer_owner_failure_case.py"),
            "--root",
            str(root),
            "--epoch",
            epoch,
            "--scenario",
            scenario,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmp_path),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    identity = L._identity(int(child._handle))
    L._write_new(root / "owner.json", L._pack({"epoch": epoch, "owner": identity}, key))
    try:
        deadline = time.monotonic() + 5
        expected_phase = (
            "native_launch_incomplete" if scenario == "writer_failure" else "owner_unresolved"
        )
        while time.monotonic() < deadline:
            observed = O.observe_owner(ticket)
            if observed.get("phase") == expected_phase:
                break
            time.sleep(0.01)
        assert observed["phase"] == expected_phase
        assert observed["owner_state"] == "verified_alive"
        assert observed["status"] == "unknown"
        assert observed["cleanup_verified"] is None
        assert observed["native_writer_admitted"] is False
        values = L._events(root, key, intent)
        launches = [value for value in values if value["phase"] == "native_launch_incomplete"]
        assert launches, (
            "injected_launch_boundary_not_reached",
            observed.get("latest_observation", {}).get("failure"),
        )
        launch = launches[0]
        assert launch["details"]["original_launch_lease_retained"] is True
        assert launch["details"]["dispatched"] is (None if scenario == "unknown_spawn" else True)
        assert not any(value["phase"] == "owner_cleanup_complete" for value in values)
        with (root / "synthetic-resource.bin").open("r+b") as probe:
            with pytest.raises(OSError):
                msvcrt.locking(probe.fileno(), msvcrt.LK_NBLCK, 1)
        assert child.poll() is None
        # A second bounded query cannot reinterpret null as an exit boundary.
        again = O.observe_owner(ticket, timeout_seconds=0.05)
        assert again["owner_state"] == "verified_alive"
        assert again["cleanup_verified"] is None
    finally:
        (root / "fixture-only-release").write_bytes(b"synthetic test teardown only\n")
        output, errors = child.communicate(timeout=5)
    assert child.returncode == expected_exit, (output, errors)
    report = json.loads((root / "fixture-report.json").read_text(encoding="utf-8"))
    assert report["launch_calls"] == 1
    assert report["native_cleanup_qualified"] is False
    assert report["real_com_imported"] is False
    assert report["fixture_io_failures"] == []
    assert report["synthetic_resource_released_by_fixture_teardown"] is True
    assert report.get("aborted_fixture_only", False) is (scenario == "unknown_spawn")
    assert report.get("writer_failure_observed", False) is (scenario == "writer_failure")
    assert O.observe_owner(ticket)["status"] == "unknown"


def test_quit_checks_active_document_and_exact_declared_quit_before_dispatch(monkeypatch):
    application, raw, _, _ = _application(monkeypatch)
    monkeypatch.setattr(
        raw, "InvokeTypes", lambda *a: object() if a[0] == 208 else pytest.fail("Quit reached")
    )
    with pytest.raises(O.OwnerError, match="fresh_empty_active_document_required"):
        application.quit_empty()
    assert application.quit_attempted is False
    monkeypatch.setattr(raw, "InvokeTypes", lambda *a: None)
    monkeypatch.setattr(
        raw, "GetIDsOfNames", lambda lcid, name: 208 if name == "ActiveDocument" else 227
    )
    with pytest.raises(O.OwnerError, match="native_quit_member_unavailable"):
        application.quit_empty()
    assert application.quit_attempted is False


def test_public_command_chain_keeps_large_result_and_initial_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(O.L, "_plain_path", lambda value: Path(value))
    root, key, epoch = tmp_path, b"k" * 32, "a" * 32
    request = {
        "command_id": "owner-command-" + "c" * 32,
        "epoch": epoch,
        "sdk_operation_id": "op-first",
        "command": "adapter",
        "method": "snapshot",
        "params": {},
        "authorization_binding": {},
    }
    original = b"first unknown stays unchanged"
    (root / "initial.json").write_bytes(original)
    writer = O._CommandJournal(root, key, request)
    writer.append("command_received")
    writer.append("command_started")
    result = {"material": "bounded readback " * 2000}
    writer.append("command_returned", result=result)
    events = O._public_command_observations(root, key, request)
    assert len(events) == 3 and events[-1]["details"]["result"] == result
    assert (root / "initial.json").read_bytes() == original
    with pytest.raises(O.OwnerError, match="fixed_public_command_phase_required"):
        writer.append("command_returned", result=result)
    raw = (root / "observation-02.json").read_bytes()
    (root / "observation-02.json").write_bytes(raw[:-1])
    with pytest.raises(O.OwnerError, match="(?:integrity|chain)_incomplete"):
        O._public_command_observations(root, key, request)


def test_measurement_ticket_cannot_publish_public_adapter_command(monkeypatch):
    monkeypatch.setattr(O, "_context", lambda *a: (None, None, {}))
    with pytest.raises(O.OwnerError, match="measurement_owner_cannot_accept_public_editing"):
        O.submit_owner_adapter_command(
            object(), "identity", {}, sdk_operation_id="op", authorization_binding={}
        )


def _public_submit_context(tmp_path, monkeypatch):
    from contextlib import nullcontext

    from xpedition_cli import designer_owner_dispatch as D

    root = tmp_path / "owner"
    root.mkdir()
    (root / "commands").mkdir()
    intent = {"mode": O.PUBLIC_MODE, "source_sha256": {}, "editing_scope": {}}
    ticket = O.OwnerTicket(root, "a" * 32)
    monkeypatch.setattr(O, "_context", lambda *a: (root, b"k" * 32, intent))
    monkeypatch.setattr(O, "_source_binding", lambda: {})
    monkeypatch.setattr(O, "_registration", lambda *a: {})
    monkeypatch.setattr(O.L, "_fresh_owner", lambda *a: ("verified_alive", {}))
    monkeypatch.setattr(O.L, "_events", lambda *a: [{"phase": "public_dispatch_ready"}])
    monkeypatch.setattr(O.L, "_plain_path", lambda p: Path(p))
    monkeypatch.setattr(O.L, "_private_mkdir", lambda p: p.mkdir())
    monkeypatch.setattr(O.L, "_launch_lock", lambda *a: nullcontext())
    monkeypatch.setattr(D, "validate_editing_scope", lambda *a: None)
    monkeypatch.setattr(
        O,
        "observe_owner_command",
        lambda ticket, cid, **kw: {"command_id": cid, "status": "unknown"},
    )
    return ticket, root


def test_public_submit_no_write_replay_but_repeated_read_allowed(tmp_path, monkeypatch):
    ticket, root = _public_submit_context(tmp_path, monkeypatch)
    arguments = {"sdk_operation_id": "op-confirmed", "authorization_binding": {}}
    first = O.submit_owner_adapter_command(
        ticket, "apply_changeset", {"project": "own.prj"}, **arguments
    )
    original = list((root / "commands" / first["command_id"]).iterdir())
    before = {p.name: p.read_bytes() for p in original}
    with pytest.raises(O.OwnerError, match="already_published_no_replay"):
        O.submit_owner_adapter_command(
            ticket, "apply_changeset", {"project": "own.prj"}, **arguments
        )
    with pytest.raises(O.OwnerError, match="payload_changed_no_replay"):
        O.submit_owner_adapter_command(
            ticket, "apply_changeset", {"project": "other.prj"}, **arguments
        )
    assert len(list((root / "commands").iterdir())) == 1
    assert {p.name: p.read_bytes() for p in original} == before
    second = O.submit_owner_adapter_command(ticket, "snapshot", {}, **arguments)
    third = O.submit_owner_adapter_command(ticket, "snapshot", {}, **arguments)
    assert len({first["command_id"], second["command_id"], third["command_id"]}) == 3


def test_public_write_budget_keeps_slots_for_verification_before_publish(tmp_path, monkeypatch):
    ticket, root = _public_submit_context(tmp_path, monkeypatch)
    monkeypatch.setattr(O, "MAX_PUBLIC_COMMANDS", 3)
    monkeypatch.setattr(O, "VERIFICATION_COMMAND_RESERVE", 2)
    monkeypatch.setattr(O, "CHECKPOINT_COMMAND_RESERVE", 0)
    monkeypatch.setattr(O, "QUIT_COMMAND_RESERVE", 0)
    arguments = {"sdk_operation_id": "op-confirmed", "authorization_binding": {}}
    O.submit_owner_adapter_command(ticket, "apply_changeset", {}, **arguments)
    with pytest.raises(O.OwnerError, match="verification_budget_reserved"):
        O.submit_owner_adapter_command(ticket, "save", {}, **arguments)
    assert len(list((root / "commands").iterdir())) == 1
    O.submit_owner_adapter_command(ticket, "snapshot", {"observe_only": True}, **arguments)
    O.submit_owner_adapter_command(ticket, "identity", {}, **arguments)
    assert len(list((root / "commands").iterdir())) == 3
    with pytest.raises(O.OwnerError, match="verification_budget_reserved"):
        O.submit_owner_adapter_command(ticket, "identity", {}, **arguments)


@pytest.mark.parametrize(
    "phase",
    [
        "owner_unresolved",
        "owner_cleanup_failed",
        "owner_cleanup_complete",
        "native_process_exit_observed",
        "public_dispatch_stopping",
    ],
)
def test_public_owner_terminal_state_rejects_new_command_before_publication(
    tmp_path, monkeypatch, phase
):
    ticket, root = _public_submit_context(tmp_path, monkeypatch)
    monkeypatch.setattr(
        O.L, "_events", lambda *a: [{"phase": "public_dispatch_ready"}, {"phase": phase}]
    )
    with pytest.raises(O.OwnerError, match="not_currently_serviceable"):
        O.submit_owner_adapter_command(
            ticket, "identity", {}, sdk_operation_id="query-only", authorization_binding={}
        )
    assert list((root / "commands").iterdir()) == []


@pytest.mark.parametrize("fault", ["null_data", "getter_exception"])
def test_false_flag_does_not_hide_data_acquisition_or_independent_document_lists(
    monkeypatch, pure_diagnostics, fault
):
    scene = _project_reader(monkeypatch)
    scene.app.members["IsProjectOpened"] = (295, lambda: False)
    if fault == "null_data":
        scene.app.members["GetProjectData"] = (228, lambda: None)
    else:

        class NativeGetterError(Exception):
            hresult = -2147417848

        def unavailable():
            raise NativeGetterError("private endpoint must not be emitted")

        scene.app.members["GetProjectData"] = (228, unavailable)
    result = scene.owner.read_project()
    assert result["project"]["open"] is False
    data = result["observations"]["project_data"]
    assert data["status"] == ("observed_null" if fault == "null_data" else "error")
    assert data["hresult"] == (None if fault == "null_data" else -2147417848)
    assert (
        result["icdb_designs"] is None
        and result["observations"]["project_path"]["status"] == "not_attempted"
    )
    assert result["available_sheet_count"] == 2 and result["open_sheet_document_count"] == 0
    assert all(value is True for value in result["integrity"].values())
    assert "private endpoint" not in json.dumps(result)


@pytest.mark.parametrize("fault", ["generation_before", "canonical_before", "canonical_after"])
def test_read_project_mandatory_generation_and_qi_checks_are_independent_of_fields(
    monkeypatch, pure_diagnostics, fault
):
    scene = _project_reader(monkeypatch)
    if fault == "generation_before":
        scene.generation["alive"] = False
    elif fault == "canonical_before":
        scene.app.unknown = object()
    else:

        def after_data():
            scene.app.unknown = object()
            return 0

        scene.docs.members["Count"] = (101, after_data)
    with pytest.raises(O.OwnerError, match="identity_(?:before|after)_mismatch"):
        scene.owner.read_project()
    if fault.endswith("before"):
        assert not any(row[:2] == ("app", "GetProjectData") for row in scene.calls)
