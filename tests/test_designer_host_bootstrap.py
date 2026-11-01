"""Explicit fake-only bootstrap tests; Windows file tests never acquire COM."""

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_host_bootstrap as H


class Raw:
    def __init__(self, scene, kind="host"):
        self.scene, self.kind = scene, kind
        self.unknown = scene.host_unknown if kind == "host" else scene.candidate_unknown
        self.project, self.opened, self.major, self.iid = scene.project, True, 151, H.APP_IID
        self.calls, self.canonical_reads = [], 0

    def QueryInterface(self, iid, *args):
        self.calls.append("QI:" + str(iid))
        if iid == "unknown":
            self.canonical_reads += 1
            if self.scene.canonical_drift and self.kind == "host" and self.canonical_reads > 2:
                return object()
        return self.unknown

    def GetTypeInfo(self, lcid, index):
        assert (lcid, index) == (0, 0)
        self.calls.append("typeinfo")
        lib = SimpleNamespace(
            GetLibAttr=lambda: ("{" + H.W.VIEWDRAW_GUID + "}", 0, 3, self.major, 0, 8),
            GetDocumentation=lambda index: ("ViewDraw", None, 0, None),
        )
        return SimpleNamespace(
            GetContainingTypeLib=lambda: (lib, 0), GetTypeAttr=lambda: SimpleNamespace(iid=self.iid)
        )

    def GetIDsOfNames(self, lcid, name):
        assert lcid == 0
        self.calls.append("name:" + name)
        return {
            "CommandsManager": 220,
            "ExecuteCommand": 327,
            "IsProjectOpened": 295,
            "GetProjectData": 228,
            "GetProjectFilePath": 3,
        }[name] + (1 if self.scene.bad_member == name else 0)

    def InvokeTypes(self, dispid, locale, kind, result_type, arg_types, *values):
        assert locale == 0 and kind == 1
        self.calls.append("invoke:" + str(dispid))
        if dispid == 220:
            return self.scene.manager
        if dispid == 327:
            assert self.kind == "manager" and self.scene.guard_locked
            self.scene.dispatches += 1
            self.scene.dispatch_thread = threading.get_ident()
            assert values == ("run " + str(self.scene.guard_path),)
            if self.scene.dispatch_entered is not None:
                self.scene.dispatch_entered.set()
            if self.scene.dispatch_exception:
                raise RuntimeError("private path and capability must not escape")
            if self.scene.dispatch_wait is not None:
                self.scene.dispatch_wait.wait()
            if self.scene.callback_enabled:
                self.scene.runtime.binding.accept(self.scene.host)
            if self.scene.duplicate_callback:
                self.scene.runtime.binding.accept(self.scene.host)
            return self.scene.dispatch_result
        if dispid == 295:
            return self.opened
        if dispid == 228:
            return self.scene.project_data
        if dispid == 3:
            return self.scene.host.project
        raise AssertionError("unexpected native business call")


class Scene:
    def __init__(self, tmp_path, monkeypatch):
        self.project, self.exe = str(tmp_path / "private.prj"), str(tmp_path / "wv/viewdraw.exe")
        self.host_unknown, self.candidate_unknown = object(), object()
        self.candidate = Raw(self, "candidate")
        self.host, self.manager, self.project_data = (
            Raw(self),
            Raw(self, "manager"),
            Raw(self, "project"),
        )
        self.pid, self.birth = 91, "134300012345678901"
        self.ownership = {
            "status": "verified",
            "domain": "schematic",
            "authority_granted": False,
            "scope_id": "scope-fixture-only",
            "project_scope_verified": True,
            "verification_basis": "original_sdk_start_receipt_and_registry",
            "startup_operation_id": "op-original-start",
            "source_sha256": "a" * 64,
            "project": self.project,
            "generations": [
                {
                    "role": "application",
                    "pid": self.pid,
                    "executable": self.exe,
                    "creation_time_100ns": self.birth,
                }
            ],
        }
        self.generation = {
            "pid": self.pid,
            "executable": self.exe,
            "creation_time_100ns": self.birth,
            "alive": True,
        }
        self.peer = {"status": 0, "protocol_sequence": 3, "client_pid": self.pid}
        self.inventory_calls = 0
        self.inventory_rows = {
            self.pid: {
                "identity": {
                    key: value for key, value in self.generation.items() if key != "alive"
                },
                "parent_pid": 50,
            }
        }
        self.second_inventory = None
        self.resolve_calls, self.pin_closed, self.dispatches = 0, 0, 0
        self.init_threads, self.lookup_threads = [], []
        self.guard_locked, self.guard_path = False, tmp_path / "private-task/identity.vbs"
        self.guard_rechecks, self.guard_fail, self.close_fail = 0, None, False
        self.dispatch_exception, self.canonical_drift = False, False
        self.callback_enabled, self.duplicate_callback, self.dispatch_result = True, False, True
        self.dispatch_wait, self.bad_member, self.runtime = None, None, None
        self.dispatch_entered = None
        self.endpoint = "objref:U0VDUkVULUNBUEFCSUxJVFk=:"
        self.com = SimpleNamespace(
            IID_IUnknown="unknown",
            IID_IDispatch="dispatch",
            TypeIIDs={"dispatch": Raw},
            VT_BOOL=11,
            VT_BSTR=8,
            VT_DISPATCH=9,
            CoInitializeEx=lambda flags: self.init_threads.append((flags, threading.get_ident())),
            CoUninitialize=lambda: None,
        )

        def forbidden(*args, **kwargs):
            pytest.fail("Default COM, process or broker acquisition is forbidden")

        monkeypatch.setattr(H, "_preload_com", forbidden)
        monkeypatch.setattr(H, "_process_pin", forbidden)
        monkeypatch.setattr(H, "_native_runtime", forbidden)
        monkeypatch.setattr(H, "_designer_inventory", forbidden)

    @contextmanager
    def pin(self, expected):
        assert expected == self.ownership["generations"][0]
        try:
            yield lambda: deepcopy(self.generation)
        finally:
            self.pin_closed += 1

    def rpc(self, progress):
        progress.update(api_attempts=1, api_returns=1, status=self.peer["status"])
        return deepcopy(self.peer)

    def resolver(self, incoming):
        self.resolve_calls += 1
        return object(), incoming

    def factory(self, expected, generation, com, budget):
        binding = H.HostBinding(expected, generation, com, budget, self.rpc, self.resolver)

        def close():
            binding.close()
            if self.close_fail:
                raise RuntimeError("private reference error")

        self.runtime = SimpleNamespace(endpoint=self.endpoint, binding=binding, close=close)
        return self.runtime

    @contextmanager
    def script(self, root, payload):
        assert H.PLACEHOLDER not in payload and self.endpoint.encode() in payload
        self.guard_locked = True

        def recheck():
            self.guard_rechecks += 1
            if self.guard_fail == self.guard_rechecks:
                raise H.BootstrapGap("bootstrap_runtime_script_bytes_changed")

        guard = SimpleNamespace(
            path=self.guard_path,
            recheck=recheck,
            removed=True,
            facts={"locked": True, "same_file": True, "bytes_match": True, "rechecked": True},
        )
        try:
            yield guard
        finally:
            self.guard_locked = False

    def lookup(self):
        self.lookup_threads.append(threading.get_ident())
        return self.candidate

    def inventory(self):
        self.inventory_calls += 1
        if self.inventory_calls == 2 and self.second_inventory is not None:
            return deepcopy(self.second_inventory)
        return deepcopy(self.inventory_rows)

    def run(self, **overrides):
        arguments = dict(
            expected_pid=self.pid,
            candidate_factory=self.lookup,
            com_module=self.com,
            pin_factory=self.pin,
            runtime_factory=self.factory,
            script_guard_factory=self.script,
            inventory_reader=self.inventory,
        )
        arguments.update(overrides)
        return H._execute_owned(self.ownership, "D:/private-bootstrap-root", **arguments)


@pytest.fixture
def scene(tmp_path, monkeypatch):
    return Scene(tmp_path, monkeypatch)


def test_template_preview_is_only_fixed_packaged_content(scene):
    result = H.template_identity()
    assert result["sha256"] == H.TEMPLATE_SHA256
    assert result["placeholder_count"] == 1 and result["engineering_writes"] is False
    assert "objref:" not in json.dumps(result)
    assert not scene.lookup_threads and scene.dispatches == 0


def test_legacy_observer_never_launches_a_daemon_or_native_candidate(scene, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unqualified observer must not create a thread or inspect a target")

    monkeypatch.setattr(threading, "Thread", forbidden)
    result = H.observe(
        scene.ownership, "unused", expected_pid=scene.pid, candidate_factory=forbidden
    )
    assert result["reason"] == "bootstrap_execution_owner_required"
    assert result["dispatch"] == {"attempted": False, "returned": None, "result_bool": None}
    assert result["callback_observation"] == result["cleanup_observation"] == "unobserved"
    assert all(value is None for value in result["callbacks"].values())
    assert all(value is None for value in result["cleanup"].values())
    assert result["callback_rpc"] is None and result["host_object"] is None
    assert scene.dispatches == 0 and not scene.lookup_threads


def test_snapshot_is_atomic_across_correlated_values_and_does_not_alias():
    state = H.HostObservation()
    entered, release = threading.Event(), threading.Event()

    def writer():
        with state.edit() as value:
            value["callbacks"]["received"] = 4
            entered.set()
            assert release.wait(2)
            value["callbacks"]["completed"] = 4
            value["callback_observation"] = "synchronized_live"

    thread = threading.Thread(target=writer)
    thread.start()
    assert entered.wait(2)
    snapshots = []
    reader_started, reader_done = threading.Event(), threading.Event()

    def read_snapshot():
        reader_started.set()
        snapshots.append(state.snapshot())
        reader_done.set()

    reader = threading.Thread(target=read_snapshot)
    reader.start()
    assert reader_started.wait(2)
    assert not reader_done.wait(0.05)
    release.set()
    thread.join(2)
    reader.join(2)
    assert not thread.is_alive() and not reader.is_alive()
    frozen = snapshots[0]
    assert frozen["callbacks"]["received"] == frozen["callbacks"]["completed"] == 4
    with state.edit() as value:
        value["callbacks"]["received"] = 5
    assert frozen["callbacks"]["received"] == 4
    assert frozen["snapshot"]["consistency"] == "synchronized"


def test_fixed_script_is_explicit_host_application_handoff_only():
    body = H._template_bytes().decode("ascii")
    assert "Set hostApplication = Application" in body
    assert 'GetObject("__BROKER_OBJREF__")' in body
    for forbidden in (
        "DontExit",
        "SetClientAdvisor",
        "CommandBars",
        "OpenProject",
        "Save",
        "Close",
        "CreateObject",
        "GetActiveObject",
    ):
        assert forbidden not in body


def test_new_host_source_is_independent_of_weak_candidate_canonical(scene):
    result = scene.run()
    assert result["status"] == "observed" and result["authority"] is False
    assert result["dispatch"] == {"attempted": True, "returned": True, "result_bool": True}
    assert result["callbacks"] == {"received": 1, "completed": 1, "accepted": 1, "rejected": 0}
    assert result["host_object"]["candidate_canonical_equal"] is False
    assert result["host_object"]["canonical_before_after_equal"] is True
    assert result["host_object"]["native_project_matches_authorized"] is True
    assert all(
        result["cleanup"][name]
        for name in (
            "closed_to_new_calls",
            "reference_variables_cleared",
            "files_removed",
            "worker_completed",
        )
    )
    assert (
        scene.lookup_threads == [scene.dispatch_thread]
        and scene.dispatch_thread == threading.get_ident()
    )
    assert scene.init_threads == [(0, scene.dispatch_thread)] and scene.dispatches == 1
    assert scene.pin_closed == 1 and scene.runtime.binding.raw is None
    assert scene.guard_rechecks == 2 and result["candidate_lookups"] == 1
    assert scene.inventory_calls == 2
    assert len(result["process_inventories"]) == 2
    assert all(
        row["complete"] and row["all_current_generations_allowed"]
        for row in result["process_inventories"]
    )
    text = json.dumps(result)
    for secret in (scene.endpoint, scene.project, scene.exe, str(scene.guard_path), "private path"):
        assert secret not in text


@pytest.mark.parametrize(
    "change",
    [{"client_pid": 999}, {"client_pid": True}, {"status": 1725}, {"protocol_sequence": 1}],
)
def test_wrong_rpc_source_is_refused_before_incoming_app_unwrap_or_getter(scene, change):
    scene.peer.update(change)
    result = scene.run()
    assert result["status"] == "incomplete"
    assert result["reason"] == "bootstrap_callback_origin_not_expected_native_process"
    assert scene.resolve_calls == 0 and scene.host.calls == []
    assert result["dispatch"]["attempted"] is True and result["dispatch"]["returned"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("creation_time_100ns", "134300012345678902"),
        ("alive", False),
        ("executable", "D:/foreign/viewdraw.exe"),
    ],
)
def test_original_pid_without_exact_live_generation_cannot_dispatch(scene, field, value):
    scene.generation[field] = value
    result = scene.run()
    assert result["reason"] == "bootstrap_generation_before_dispatch_mismatch"
    assert scene.lookup_threads == [] and scene.dispatches == 0


@pytest.mark.parametrize("fault", ["role", "project", "canonical"])
def test_same_caller_foreign_role_project_or_changed_object_is_refused(scene, fault):
    if fault == "role":
        scene.host.iid = "00000000-0000-0000-0000-000000000000"
    elif fault == "project":
        scene.host.project = "D:/foreign/other.prj"
    else:
        scene.canonical_drift = True
    result = scene.run()
    assert result["status"] == "incomplete"
    assert result["dispatch"]["attempted"] is True and scene.dispatches == 1


def test_closed_project_is_not_derived_from_zero_sheet_windows(scene):
    scene.host.opened = False
    result = scene.run()
    assert result["status"] == "observed"
    assert result["host_object"]["native_project_open"] is False
    assert result["host_object"]["native_project_matches_authorized"] is None
    assert "invoke:228" not in scene.host.calls


@pytest.mark.parametrize("member", ["CommandsManager", "ExecuteCommand"])
def test_declared_command_mapping_mismatch_never_dispatches(scene, member):
    scene.bad_member = member
    result = scene.run()
    assert result["status"] == "incomplete" and scene.dispatches == 0


def test_dispatched_native_error_remains_dispatched_unknown_without_replay(scene):
    scene.dispatch_exception = True
    result = scene.run()
    assert result["status"] == "incomplete"
    assert result["dispatch"] == {"attempted": True, "returned": None, "result_bool": None}
    assert scene.dispatches == 1 and "private path" not in json.dumps(result)


@pytest.mark.parametrize("answer", [False, 1, None])
def test_execute_return_does_not_become_success_by_truthiness(scene, answer):
    scene.dispatch_result = answer
    result = scene.run()
    assert result["reason"] == "bootstrap_execute_did_not_return_true"
    assert result["dispatch"]["returned"] is True and result["status"] == "incomplete"


def test_duplicate_callback_invalidates_the_diagnostic(scene):
    scene.duplicate_callback = True
    result = scene.run()
    assert result["status"] == "incomplete" and result["callbacks"]["rejected"] == 1


def test_script_tamper_after_native_return_preserves_dispatched_facts(scene):
    scene.guard_fail = 2
    result = scene.run()
    assert result["status"] == "incomplete" and result["dispatch"]["returned"] is True
    assert result["reason"] == "bootstrap_runtime_script_bytes_changed"


def test_reference_cleanup_failure_is_not_normal_completion(scene):
    scene.close_fail = True
    result = scene.run()
    assert (
        result["status"] == "incomplete"
        and result["reason"] == "bootstrap_runtime_reference_cleanup_failed"
    )
    assert result["cleanup"]["reference_variables_cleared"] is None


def test_missing_trusted_original_packet_never_starts_worker(scene):
    scene.ownership["verification_basis"] = "copied_journal"
    result = scene.run()
    assert result["reason"] == "bootstrap_trusted_original_start_packet_required"
    assert not scene.lookup_threads and scene.dispatches == 0


def test_pid_argument_cannot_select_another_owned_instance(scene):
    result = scene.run(expected_pid=999)
    assert result["reason"] == "bootstrap_requested_pid_differs_from_original_worker"
    assert not scene.lookup_threads


@pytest.mark.skipif(os.name != "nt", reason="Real query-only Windows file locks")
def test_windows_script_guard_blocks_write_delete_and_holds_verified_bytes(tmp_path):
    payload = b"Option Explicit\nDim example\n"
    with H.LockedScript(tmp_path, payload) as guard:
        path = guard.path
        assert path.read_bytes() == payload
        with pytest.raises(OSError):
            path.write_bytes(b"tampered")
        with pytest.raises(OSError):
            path.unlink()
        with pytest.raises(OSError):
            path.parent.rename(path.parent.with_name("moved"))
        guard.recheck()
        assert all(guard.facts.values())
    assert guard.removed is True and not path.exists() and not path.parent.exists()


def test_template_has_exact_sha_and_no_runtime_placeholder_in_hash_preview():
    template = H._template_bytes()
    assert hashlib.sha256(template).hexdigest() == H.template_identity()["sha256"]


def test_host_protection_proxy_is_an_explicit_current_151_application_role(scene):
    scene.host.iid = H.APP_PROXY_IID
    result = scene.run()
    assert result["status"] == "observed"
    assert result["host_object"]["role"]["interface_iid"] == H.APP_PROXY_IID
    assert result["authority"] is result["authority_granted"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope_id", None),
        ("scope_id", ""),
        ("project_scope_verified", False),
        ("project_scope_verified", 1),
    ],
)
def test_missing_or_unverified_current_project_scope_never_dispatches(scene, field, value):
    scene.ownership[field] = value
    result = scene.run()
    assert result["reason"] == "bootstrap_trusted_original_start_packet_required"
    assert scene.dispatches == 0 and not scene.lookup_threads


def test_missing_callback_is_not_replayed_to_make_it_arrive(scene, monkeypatch):
    scene.callback_enabled = False
    monkeypatch.setattr(H, "MAX_CALLBACK_SECONDS", 0.01)
    result = scene.run()
    assert result["reason"] == "bootstrap_host_callback_not_completed"
    assert scene.dispatches == 1 and result["callbacks"]["received"] == 0


def test_live_snapshot_is_synchronized_and_detached_while_owner_call_is_blocked(scene):
    scene.dispatch_wait = threading.Event()
    scene.dispatch_entered = threading.Event()
    observation = H.HostObservation()
    returned = []
    # Test control only. Product execution has no observer-owned/daemon thread.
    owner = threading.Thread(target=lambda: returned.append(scene.run(observation=observation)))
    owner.start()
    try:
        assert scene.dispatch_entered.wait(2)
        result = observation.snapshot()
        assert not returned and owner.is_alive()
        assert result["dispatch"] == {"attempted": True, "returned": None, "result_bool": None}
        assert result["callback_observation"] == "synchronized_live"
        assert result["callbacks"] == {"received": 0, "completed": 0, "accepted": 0, "rejected": 0}
        assert all(value is None for value in result["cleanup"].values())
        snapshot = deepcopy(result)
    finally:
        scene.dispatch_wait.set()
        owner.join(2)
    assert not owner.is_alive()
    assert result == snapshot
    assert returned[0]["cleanup"]["worker_completed"] is True
    assert scene.runtime.binding.raw is None


def test_callback_generation_change_rejects_before_business_getters(scene):
    original = scene.resolver

    def changed(incoming):
        resolved = original(incoming)
        scene.generation["creation_time_100ns"] = "134300012345678902"
        return resolved

    scene.resolver = changed
    result = scene.run()
    assert result["reason"] == "bootstrap_callback_generation_after_mismatch"
    assert not any(call.startswith("invoke:") for call in scene.host.calls)


@pytest.mark.skipif(os.name != "nt", reason="Real local Windows directory lock failure")
def test_child_directory_pin_failure_never_disposes_an_existing_parent(tmp_path, monkeypatch):
    marker = tmp_path / "keep.txt"
    marker.write_text("preserve existing root", encoding="ascii")
    original = H.LockedScript._open

    def fail_child(self, path, *args):
        if path.name.startswith("designer-host-"):
            raise H.BootstrapGap("injected_child_pin_failure")
        return original(self, path, *args)

    monkeypatch.setattr(H.LockedScript, "_open", fail_child)
    with pytest.raises(H.BootstrapGap):
        with H.LockedScript(tmp_path, b"Option Explicit\n"):
            pytest.fail("Failed child lock entered the guarded body")
    assert tmp_path.is_dir() and marker.read_text(encoding="ascii") == "preserve existing root"


@pytest.mark.skipif(os.name != "nt", reason="Actual installed Automation bindings, no CAD")
def test_real_local_typelib_pywin32_role_attributes_are_used_correctly(tmp_path):
    import ctypes

    import pythoncom
    from comtypes import GUID
    from comtypes.typeinfo import SYS_WIN64, TKIND_DISPATCH, CreateTypeLib, ITypeLib

    # No SaveAllChanges: the local typelib remains in memory and is never registered.
    unsaved = tmp_path / "not-saved.tlb"
    creator = CreateTypeLib(str(unsaved), SYS_WIN64)
    creator.SetGuid(GUID("{" + H.W.VIEWDRAW_GUID + "}"))
    creator.SetName("ViewDraw")
    creator.SetVersion(151, 0)
    type_creator = creator.CreateTypeInfo("IVdApp", TKIND_DISPATCH)
    type_creator.SetGuid(GUID("{" + H.APP_IID + "}"))
    type_creator.LayOut()
    library = creator.QueryInterface(ITypeLib)
    info = library.GetTypeInfoOfGuid(GUID("{" + H.APP_IID + "}"))
    raw_info = pythoncom.ObjectFromAddress(
        ctypes.cast(info, ctypes.c_void_p).value, pythoncom.IID_ITypeInfo
    )
    carrier = SimpleNamespace(GetTypeInfo=lambda locale, index: raw_info)
    result = {"phase": "local-typeinfo"}
    role = H._role(carrier, pythoncom, H.Budget(result, H.time.monotonic))
    assert role == {
        "library_guid": H.W.VIEWDRAW_GUID,
        "library_name": "ViewDraw",
        "library_major": 151,
        "library_minor": 0,
        "library_lcid": library.GetLibAttr().lcid,
        "interface_iid": H.APP_IID,
    }
    assert not unsaved.exists()


@pytest.mark.skipif(os.name != "nt", reason="Actual local COM stub only, no CAD or RPC")
def test_native_runtime_owns_byref_argument_bridge_without_repr_or_layout_guess():
    import ctypes

    import comtypes
    import pythoncom
    from comtypes import IUnknown
    from comtypes.automation import VARIANT, VT_BYREF, VT_VARIANT, IDispatch

    class LocalDispatch(comtypes.COMObject):
        _com_interfaces_ = [IDispatch]

    first, other = LocalDispatch(), LocalDispatch()
    pointer = first.QueryInterface(IDispatch)
    base = VARIANT(pointer)
    incoming = VARIANT()
    incoming.vt = VT_BYREF | VT_VARIANT
    incoming._.c_void_p = ctypes.cast(ctypes.byref(base), ctypes.c_void_p).value
    budget = H.Budget({}, H.time.monotonic)
    runtime = H._native_runtime({}, lambda: {}, pythoncom, budget)
    try:
        owner, raw = runtime.binding.resolver(incoming)
        original = pythoncom.ObjectFromAddress(
            ctypes.cast(pointer, ctypes.c_void_p).value, pythoncom.IID_IDispatch
        )
        assert raw.QueryInterface(pythoncom.IID_IUnknown) == original.QueryInterface(
            pythoncom.IID_IUnknown
        )
        other_raw = pythoncom.ObjectFromAddress(
            ctypes.cast(other.QueryInterface(IUnknown), ctypes.c_void_p).value,
            pythoncom.IID_IUnknown,
        )
        assert raw.QueryInterface(pythoncom.IID_IUnknown) != other_raw
        assert owner.QueryInterface(IUnknown)
        assert base.vt == 9 and incoming.vt == 16396
        assert runtime.binding.rpc == {
            "api_attempts": 0,
            "api_returns": 0,
            "status": None,
            "client_pid": None,
            "protocol_sequence": None,
        }
    finally:
        runtime.close()
    assert runtime.binding.closed and runtime.binding.raw is None


@pytest.mark.skipif(os.name != "nt", reason="Local fake incoming handler; no RPC or CAD")
def test_native_bind_authentication_refusal_is_false_ack_and_diagnostic_failure():
    import ctypes

    import pythoncom
    from comtypes.automation import DISPPARAMS, VARIANT

    runtime = H._native_runtime({"pid": 91}, lambda: {}, pythoncom, H.Budget({}, H.time.monotonic))
    try:
        runtime.binding.armed = True
        runtime.binding.rpc_reader = lambda progress: {
            "status": 0,
            "protocol_sequence": 3,
            "client_pid": 92,
        }
        arguments = (VARIANT * 1)()
        parameters = DISPPARAMS()
        parameters.rgvarg, parameters.cArgs = arguments, 1
        response = VARIANT()
        status = runtime.broker.IDispatch_Invoke(
            None, 1, None, 0, 1, ctypes.pointer(parameters), ctypes.pointer(response), None, None
        )
        assert status == 0 and response.value is False
        assert runtime.binding.accepted == 0 and runtime.binding.rejected == 1
        assert runtime.binding.error == "bootstrap_callback_origin_not_expected_native_process"
        assert runtime.callback_finished.is_set() and runtime.binding.raw is None
    finally:
        runtime.close()


@pytest.mark.parametrize("phase", [1, 2])
@pytest.mark.parametrize(
    "fault",
    [
        "foreign",
        "reused_pid",
        "wrong_image",
        "missing_worker",
        "incomplete",
        "duplicate",
        "over_limit",
    ],
)
def test_complete_viewdraw_inventory_refuses_unknown_or_changed_processes_before_run(
    scene, phase, fault
):
    rows = deepcopy(scene.inventory_rows)
    if fault == "foreign":
        rows[92] = {
            "identity": {"pid": 92, "creation_time_100ns": scene.birth, "executable": scene.exe},
            "parent_pid": 50,
        }
    elif fault == "reused_pid":
        rows[scene.pid]["identity"]["creation_time_100ns"] = "134300012345678902"
    elif fault == "wrong_image":
        rows[scene.pid]["identity"]["executable"] = "D:/foreign/viewdraw.exe"
    elif fault == "missing_worker":
        rows.clear()
    elif fault == "incomplete":
        del rows[scene.pid]["identity"]["creation_time_100ns"]
    elif fault == "duplicate":
        rows[92] = deepcopy(rows[scene.pid])
    else:
        rows = {
            pid: {
                "identity": {
                    "pid": pid,
                    "creation_time_100ns": scene.birth,
                    "executable": scene.exe,
                },
                "parent_pid": 50,
            }
            for pid in range(1, 66)
        }
    if phase == 1:
        scene.inventory_rows = rows
    else:
        scene.second_inventory = rows
    result = scene.run()
    assert result["status"] == "incomplete" and scene.dispatches == 0
    assert result["dispatch"] == {"attempted": False, "returned": None, "result_bool": None}
    assert result["candidate_lookups"] == (0 if phase == 1 else 1)
    assert len(result["process_inventories"]) == phase
    last = result["process_inventories"][-1]
    assert last["snapshot_reader_calls"] == 1 and last["current_process_count"] == len(rows)
    assert last["all_current_generations_allowed"] is (
        None if fault in {"incomplete", "duplicate", "over_limit"} else False
    )
    assert last["complete"] is (fault in {"foreign", "reused_pid", "wrong_image", "missing_worker"})
    assert scene.resolve_calls == 0
    assert "foreign/viewdraw.exe" not in json.dumps(result)


@pytest.mark.parametrize("phase", [1, 2])
def test_unreadable_current_native_process_refuses_complete_inventory_without_raw_error(
    scene, phase
):
    def reader():
        scene.inventory_calls += 1
        if scene.inventory_calls == phase:
            raise OSError("private-path/credential native process unavailable")
        return deepcopy(scene.inventory_rows)

    result = scene.run(inventory_reader=reader)
    assert result["reason"] == "bootstrap_current_viewdraw_inventory_unavailable"
    assert scene.dispatches == 0 and result["candidate_lookups"] == phase - 1
    last = result["process_inventories"][-1]
    assert last["complete"] is None and last["current_process_count"] is None
    assert "private-path" not in json.dumps(result)


@pytest.mark.parametrize("launcher_current", [False, True])
def test_only_original_proven_launcher_may_accompany_worker_and_may_be_absent(
    scene, launcher_current
):
    launcher = {
        "role": "launcher",
        "pid": 80,
        "creation_time_100ns": "134300012345678899",
        "executable": "D:/installed/common/viewdraw.exe",
    }
    scene.ownership["generations"].append(launcher)
    scene.inventory_rows[scene.pid]["parent_pid"] = launcher["pid"]
    if launcher_current:
        scene.inventory_rows[launcher["pid"]] = {
            "identity": {key: value for key, value in launcher.items() if key != "role"},
            "parent_pid": 50,
        }
    result = scene.run()
    assert result["status"] == "observed" and scene.dispatches == 1
    for inventory in result["process_inventories"]:
        assert inventory["complete"] and inventory["all_current_generations_allowed"]
        assert inventory["original_allowed_role_count"] == 2
        assert inventory["current_process_count"] == (2 if launcher_current else 1)
        assert inventory["original_roles_not_current_count"] == (0 if launcher_current else 1)


def test_inventory_guard_is_discovery_constraint_not_substitute_for_rpc_identity(scene):
    scene.peer["client_pid"] = 92
    result = scene.run()
    assert all(row["all_current_generations_allowed"] for row in result["process_inventories"])
    assert result["status"] == "incomplete" and scene.resolve_calls == 0
    assert result["dispatch"]["attempted"] is True
    assert result["reason"] == "bootstrap_callback_origin_not_expected_native_process"


@pytest.mark.parametrize("blocked_step", ["rpc", "resolver"])
def test_closed_binding_cannot_be_revived_by_an_inflight_external_call(scene, blocked_step):
    entered, release, close_done = threading.Event(), threading.Event(), threading.Event()
    answers = []

    def rpc(progress):
        if blocked_step == "rpc":
            entered.set()
            assert release.wait(1)
        return scene.rpc(progress)

    def resolver(incoming):
        if blocked_step == "resolver":
            entered.set()
            assert release.wait(1)
        return scene.resolver(incoming)

    binding = H.HostBinding(
        scene.ownership["generations"][0],
        lambda: deepcopy(scene.generation),
        scene.com,
        H.Budget({}, H.time.monotonic),
        rpc,
        resolver,
    )
    binding.armed = True
    accepter = threading.Thread(
        target=lambda: answers.append(binding.accept(scene.host)), daemon=True
    )

    def close():
        binding.close()
        close_done.set()

    closer = threading.Thread(target=close, daemon=True)
    accepter.start()
    try:
        assert entered.wait(1)
        closer.start()
        assert close_done.wait(1), "State lock was held across an external native call"
        assert binding.closed and not binding.armed
        assert binding.raw is binding.unknown is binding.dispatch_owner is None
        assert binding.accepted == 0 and binding.inflight == 1
        assert binding.cleanup_complete is False
        receipt = {
            "accepted": binding.accepted,
            "cleanup_complete": binding.cleanup_complete,
            "error": binding.error,
            "inflight_at_close": binding.inflight_at_close,
        }
        frozen_receipt = deepcopy(receipt)
    finally:
        release.set()
        accepter.join(1)
        closer.join(1)
    assert not accepter.is_alive() and not closer.is_alive()
    assert answers == [False] and binding.accepted == 0 and binding.rejected == 1
    assert binding.raw is binding.unknown is binding.dispatch_owner is None
    assert binding.closed and binding.completed == 1 and binding.inflight == 0
    assert binding.error == "bootstrap_callback_inflight_during_cleanup"
    assert receipt == frozen_receipt
    # A second cleanup cannot rewrite the first incomplete-close observation.
    binding.close()
    assert binding.cleanup_complete is False and binding.inflight_at_close == 1


def test_observer_reports_inflight_cleanup_incomplete_and_freezes_late_failure(scene, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    callback_threads, answers = [], []
    original_resolver = scene.resolver

    def resolver(incoming):
        entered.set()
        assert release.wait(1)
        return original_resolver(incoming)

    scene.resolver = resolver
    original_invoke = scene.manager.InvokeTypes
    scene.callback_enabled = False

    def asynchronous(dispatched_id, *args):
        if dispatched_id != 327:
            return original_invoke(dispatched_id, *args)
        response = original_invoke(dispatched_id, *args)
        thread = threading.Thread(
            target=lambda: answers.append(scene.runtime.binding.accept(scene.host)), daemon=True
        )
        callback_threads.append(thread)
        thread.start()
        assert entered.wait(1)
        return response

    scene.manager.InvokeTypes = asynchronous
    monkeypatch.setattr(H, "MAX_CALLBACK_SECONDS", 0.01)
    try:
        result = scene.run()
        assert result["status"] == "incomplete"
        assert result["reason"] == "bootstrap_callback_inflight_during_cleanup"
        assert result["dispatch"] == {"attempted": True, "returned": True, "result_bool": True}
        assert result["cleanup"]["closed_to_new_calls"] is True
        assert result["cleanup"]["reference_variables_cleared"] is False
        assert result["cleanup"]["callback_references_quiescent"] is False
        assert result["cleanup"]["inflight_callbacks_at_close"] == 1
        assert result["cleanup"]["mta_uninitialized"] is None
        frozen = deepcopy(result)
    finally:
        release.set()
        for thread in callback_threads:
            thread.join(1)
    assert answers == [False] and result == frozen
    assert scene.runtime.binding.raw is None and scene.runtime.binding.accepted == 0
    assert not any(call.startswith("invoke:") for call in scene.host.calls)


def test_candidate_and_manager_references_drop_before_mta_uninitialize_on_dispatch_error(scene):
    releases, uninitializations = [], []

    class OwnedRaw(Raw):
        def __del__(self):
            releases.append((self.kind, threading.get_ident()))

        def InvokeTypes(self, dispid, *args):
            if dispid == 220:
                return OwnedRaw(scene, "manager")
            return super().InvokeTypes(dispid, *args)

    def uninitialize():
        assert {kind for kind, _ in releases} == {"candidate", "manager"}
        assert all(thread == threading.get_ident() for _, thread in releases)
        uninitializations.append(threading.get_ident())

    scene.com.TypeIIDs["dispatch"] = OwnedRaw
    scene.com.CoUninitialize = uninitialize
    scene.dispatch_exception = True
    result = scene.run(candidate_factory=lambda: OwnedRaw(scene, "candidate"))
    assert result["status"] == "incomplete" and result["dispatch"]["attempted"] is True
    assert result["dispatch"]["returned"] is None
    assert result["cleanup"]["mta_uninitialized"] is True
    assert len(uninitializations) == 1 and len(releases) == 2
