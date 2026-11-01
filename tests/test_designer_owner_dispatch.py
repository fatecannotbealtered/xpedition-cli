"""Pure fake-only bridge tests; no owner command, COM or CAD is activated."""

from __future__ import annotations

import copy
import ctypes
import threading
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_host_bootstrap as B
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A
from xpedition_cli.designer_native_owner import NativeApplication
from xpedition_cli.designer_owner_transport import ExpectedGeneration, Handoff

FACTORY = A._viewdraw_application
ACTIVE = A._viewdraw_active
WRAP = D._wrap_existing


@pytest.fixture(autouse=True)
def no_default_native_or_loader(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("A pure bridge fixture reached a native loader/ROT/OS fallback")

    for owner, name in (
        (A, "_import_com"),
        (A, "_configure_environment"),
        (A, "_legacy_application_identity"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    yield
    assert hits == [], "Swallowed native attempts still fail this fixture"


@pytest.fixture
def scene(monkeypatch, tmp_path):
    unknown = object()
    state = NS(alive=True, unknown=unknown, document={"open": False, "path": None}, calls=[])
    expected = ExpectedGeneration(123, "133000000000000001", "C:/vendor/viewdraw.exe")

    def generation():
        state.calls.append("generation")
        return {
            "pid": expected.pid,
            "birth_filetime_100ns": expected.birth_100ns,
            "executable": expected.executable,
            "alive": state.alive,
        }

    lease = NS(snapshot=generation)

    class Raw:
        def QueryInterface(self, iid):
            assert iid == "unknown"
            state.calls.append("canonical")
            return state.unknown

        def GetIDsOfNames(self, lcid, name):
            assert (lcid, name) == (0, "GetProjectData")
            return 228

        def InvokeTypes(self, dispid, lcid, kind, result, arguments):
            assert (dispid, lcid, kind, result, arguments) == (228, 0, 1, (9, 0), ())
            return project_data

    raw = Raw()

    class Com:
        IID_IUnknown, IID_IDispatch = "unknown", "dispatch"
        TypeIIDs = {"dispatch": Raw}

        def CoGetInterfaceAndReleaseStream(self, stream, iid):
            assert stream is raw and iid == "dispatch"
            return raw

        def CoInitialize(self):
            state.calls.append("init")

        def CoUninitialize(self):
            state.calls.append("uninit")

    com = Com()
    handoff = Handoff(
        "a" * 32,
        lambda: {"status": 0, "protocol_sequence": 3, "client_pid": 123},
        lambda value: value,
        lambda incoming: incoming,
        lambda stream: None,
    )
    handoff.arm(expected, lease)
    assert handoff.offer(raw, "a" * 32)
    owner = NativeApplication.from_handoff(handoff, com, expected, lease, None)
    role = {
        "library_guid": B.W.VIEWDRAW_GUID,
        "library_name": "ViewDraw",
        "library_major": 151,
        "library_minor": 0,
        "library_lcid": 0,
        "interface_iid": B.APP_IID,
    }
    monkeypatch.setattr(B, "_role", lambda *a: copy.deepcopy(role))
    monkeypatch.setattr(B, "_native_project", lambda *a: copy.deepcopy(state.document))

    class DeclaredInterface:
        def __init__(self, iid, members):
            self.iid, self.members, self.identity = iid, members, object()

        def QueryInterface(self, iid, wrapped=None):
            if iid == A._IUNKNOWN_IID:
                return self.identity
            assert (iid, wrapped) == (self.iid, A._IDISPATCH_IID)
            return self

        def GetIDsOfNames(self, lcid, name):
            assert lcid == 0
            return self.members[name][0]

        def InvokeTypes(self, dispid, lcid, kind, result, arguments, *values):
            rows = [row for row in self.members.values() if row[0] == dispid]
            assert len(rows) == 1
            _id, expected_result, inputs, read = rows[0]
            assert (lcid, kind, result, arguments) == (0, 1, (expected_result, 0), inputs)
            return read(*values)

    design_names = DeclaredInterface(
        A._STRING_LIST_IID,
        {
            "GetCount": (1, 3, (), lambda: 1),
            "GetItem": (2, 8, ((3, 1),), lambda index: "FirstEdit" if index == 1 else None),
        },
    )
    project_data = DeclaredInterface(
        A._PROJECT_DATA_IID,
        {
            "GetiCDBDesigns": (9, 9, (), lambda: design_names),
            "GetProjectFilePath": (3, 8, (), lambda: state.document["path"] or ""),
        },
    )
    app = NS(
        _oleobj_=raw,
        Name="Designer fixture",
        FullName=expected.executable,
        Version="2604-fixture",
    )
    monkeypatch.setattr(D, "_wrap_existing", lambda owner: app)
    state.owner, state.handoff, state.app, state.com = owner, handoff, app, com
    state.grant = D.issue_from_owner(owner, handoff, "a" * 32)
    return state


def test_current_measurement_constructor_cannot_issue_a_dispatch_capability(scene):
    del scene.owner._origin_handoff
    with pytest.raises(D.DispatchError, match="same_consumed_startup_handoff_required"):
        D.issue_from_owner(scene.owner, scene.handoff, "a" * 32)


@pytest.mark.parametrize("value", [None, True, {"verified": True, "pid": 123}])
def test_json_or_flags_cannot_create_a_grant(value):
    with pytest.raises(D.DispatchError, match="native_owner_and_handoff_objects_required"):
        D.issue_from_owner(value, value, "a" * 32)


@pytest.mark.parametrize("method", sorted(D.METHODS - D.WRITE_METHODS))
def test_fixed_methods_use_exact_same_object_and_reset_context(scene, monkeypatch, method):
    def existing(selected, params):
        assert selected == method and D.check_adapter_dispatch(method)
        assert D.current_application() is scene.app
        assert FACTORY(object(), attach_only=True) is scene.app
        assert ACTIVE(object()) is scene.app
        return {"method": selected}

    monkeypatch.setattr(A, "dispatch", existing)
    assert D.dispatch(scene.grant, method, {"domain": "schematic"}) == {"method": method}
    assert D.current_application() is None


def test_real_adapter_identity_and_snapshot_share_app_and_balance_com(scene, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("No ROT or CAD creation is allowed")

    monkeypatch.setattr(
        A, "_import_com", lambda: (scene.com, NS(GetActiveObject=forbidden, Dispatch=forbidden))
    )
    monkeypatch.setattr(A, "_heal_gen_py_cache", lambda: None)
    monkeypatch.setattr(A, "adapter_identity", lambda: {"fixture": True})
    monkeypatch.setattr(A, "_viewdraw_application", FACTORY)
    seen = []
    monkeypatch.setattr(A, "_open_designer_project", lambda app, params: seen.append(app))
    monkeypatch.setattr(A, "_designer_snapshot", lambda app, params: {"same_app": app is scene.app})
    identity = D.dispatch(scene.grant, "identity", {"domain": "schematic"})["application"]
    assert identity["pid"] == 123
    assert identity["document"] == {
        "open": False,
        "path": None,
        "designs": [],
        "status": "observed",
        "source": "dedicated_startup_host_Application_and_authenticated_ncalrpc",
    }
    assert identity["dedicated_startup_binding"]["native_writer_admitted"] is False
    assert "hwnd" not in repr(identity).lower()
    assert D.dispatch(scene.grant, "snapshot", {"domain": "schematic", "observe_only": True}) == {
        "same_app": True
    }
    assert seen == [scene.app]
    assert scene.calls.count("init") == scene.calls.count("uninit") == 2


def test_identity_keeps_actual_project_and_design_names(scene, monkeypatch, tmp_path):
    scene.owner.project = str(tmp_path / "own.prj")
    scene.owner.project_open_verified = True
    scene.document = {"open": True, "path": B.W._path(scene.owner.project)}
    monkeypatch.setattr(
        A, "dispatch", lambda method, params: A.application_identity(scene.app, "schematic")
    )
    identity = D.dispatch(scene.grant, "identity", {"domain": "schematic"})
    assert identity["document"]["path"] == scene.document["path"]
    assert identity["document"]["designs"] == ["FirstEdit"]


@pytest.mark.parametrize("reason", ["epoch", "closed-handoff", "dead-generation", "canonical"])
def test_stale_binding_refuses_before_existing_dispatch(scene, monkeypatch, reason):
    monkeypatch.setattr(A, "dispatch", lambda *a: pytest.fail("Stale binding was dispatched"))
    if reason == "epoch":
        scene.grant.epoch = "b" * 32
    elif reason == "closed-handoff":
        scene.handoff.close()
    elif reason == "dead-generation":
        scene.alive = False
    else:
        scene.unknown = object()
    with pytest.raises(D.DispatchError):
        D.dispatch(scene.grant, "identity", {})


def test_wrong_thread_cannot_touch_the_retained_pointer(scene):
    result = []
    before = list(scene.calls)

    def other_thread():
        try:
            D.dispatch(scene.grant, "identity", {})
        except D.DispatchError as error:
            result.append(str(error))

    worker = threading.Thread(target=other_thread)
    worker.start()
    worker.join()
    assert result == ["original_owner_sta_thread_required"]
    assert scene.calls == before


@pytest.mark.parametrize("method", ["start", "attach", "designer_host_bootstrap", "arbitrary_run"])
def test_old_or_dynamic_entry_never_becomes_an_owner_command(scene, method):
    with pytest.raises(D.DispatchError, match="fixed_owner_adapter_method_required"):
        D.dispatch(scene.grant, method, {})


def test_json_private_selector_and_foreign_domain_refuse(scene):
    for params in (
        {"_nativeom_ownership": {"verified": True}},
        {"domain": "pcb"},
        {"value": object()},
    ):
        with pytest.raises(D.DispatchError):
            D.dispatch(scene.grant, "identity", params)


def test_nested_dispatch_or_method_change_refuses_and_context_resets(scene, monkeypatch):
    def nested(method, params):
        with pytest.raises(D.DispatchError, match="one_owner_command_at_a_time_required"):
            D.dispatch(scene.grant, "identity", {})
        D.check_adapter_dispatch("designer_host_bootstrap")

    monkeypatch.setattr(A, "dispatch", nested)
    with pytest.raises(D.DispatchError, match="owner_dispatch_method_changed_or_unsupported"):
        D.dispatch(scene.grant, "identity", {})
    assert D.current_application() is None and scene.grant.busy is False


def test_post_dispatch_drift_is_marked_dispatched_and_not_replayed(scene, monkeypatch):
    calls = []

    def drift(method, params):
        calls.append(method)
        scene.alive = False
        return {"returned": True}

    monkeypatch.setattr(A, "dispatch", drift)
    monkeypatch.setattr(D, "authorize", lambda *a: None)
    with pytest.raises(D.DispatchError) as failure:
        D.dispatch(scene.grant, "save", {}, editing_scope={})
    assert failure.value.dispatch_started is True
    assert calls == ["save"] and D.current_application() is None
    D.close(scene.grant)
    assert scene.grant.app is None


def test_outside_context_factory_stays_disabled_and_flags_do_not_enable_it():
    with pytest.raises(A.AdapterError) as failure:
        FACTORY(NS(owner_context=True), attach_only=False)
    assert failure.value.code == "E_HUMAN_REQUIRED"
    with pytest.raises(A.AdapterError) as failure:
        A.dispatch("open", {"domain": "schematic", "owner_context": True})
    assert failure.value.code == "E_HUMAN_REQUIRED"


def test_existing_dispatch_wrapper_cannot_accept_a_progid(scene):
    scene.owner.raw = "Viewdraw.Application"
    with pytest.raises(D.DispatchError, match="retained_native_idispatch_required"):
        WRAP(scene.owner)


def test_a_different_canonical_object_never_falls_back_to_legacy_identity(scene, monkeypatch):
    foreign = NS(_oleobj_=NS(QueryInterface=lambda iid: object()))
    monkeypatch.setattr(
        A, "dispatch", lambda method, params: A.application_identity(foreign, "schematic")
    )
    with pytest.raises(A.AdapterError) as failure:
        D.dispatch(scene.grant, "identity", {})
    assert failure.value.code == "E_CONFLICT"
    assert failure.value.details["reason"] == "typed_application_canonical_mismatch"
    assert D.current_application() is None


def test_incomplete_probe_cannot_issue_a_bridge(scene, monkeypatch):
    monkeypatch.setattr(scene.owner, "probe", lambda: {"canonical_stable": False})
    with pytest.raises(D.DispatchError, match="completed_native_probe_required"):
        D.issue_from_owner(scene.owner, scene.handoff, "a" * 32)


@pytest.fixture
def editing_scope(tmp_path):
    import hashlib
    import json

    record = {
        "scope_id": "test-owned-scope",
        "tool": "xpedition-cli",
        "granted_by": "fixture-owner",
        "granted_at": "2026-10-27T00:00:00Z",
        "expires_at": None,
        "effects": ["observe", "session", "change", "persist", "artifact"],
        "dangerous": True,
        "roots": [str(tmp_path)],
        "outputs": [str(tmp_path)],
    }
    path = tmp_path / "scope.json"
    raw = json.dumps(record).encode()
    path.write_bytes(raw)
    return {
        "scope_file": str(path),
        "scope_sha256": hashlib.sha256(raw).hexdigest(),
        "scope_id": record["scope_id"],
    }


def _authorization(scope, command="schematic edit", operation="op-confirmed"):
    return {
        "command": command,
        "scope_id": scope["scope_id"],
        "scope_sha256": scope["scope_sha256"],
        "source_operation_id": operation,
    }


def test_mutating_dispatch_has_no_implicit_scope_even_with_real_handoff(scene, monkeypatch):
    monkeypatch.setattr(A, "dispatch", lambda *a: pytest.fail("No permission was checked"))
    for method in D.WRITE_METHODS:
        with pytest.raises(D.DispatchError, match="public_editing_scope_required"):
            D.dispatch(scene.grant, method, {})
    assert scene.grant.busy is False


def test_scope_uses_actual_sdk_record_and_detects_changed_bytes(editing_scope):
    scope = D.validate_editing_scope(editing_scope)
    assert scope.granted and scope.scope_id == editing_scope["scope_id"]
    from pathlib import Path

    Path(editing_scope["scope_file"]).write_bytes(b"{}")
    with pytest.raises(D.DispatchError, match="scope_source_binding_changed"):
        D.validate_editing_scope(editing_scope)


@pytest.mark.parametrize("mismatch", ["operation", "scope", "command", "project"])
def test_authorization_mismatch_refuses_before_adapter(
    scene, editing_scope, tmp_path, monkeypatch, mismatch
):
    scene.owner.project = str(tmp_path / "own.prj")
    params = {"domain": "schematic", "project": scene.owner.project}
    binding = _authorization(editing_scope)
    if mismatch == "operation":
        binding["source_operation_id"] = "op-other"
    if mismatch == "scope":
        binding["scope_id"] = "other"
    if mismatch == "command":
        binding["command"] = "schematic build"
    if mismatch == "project":
        params["project"] = str(tmp_path / "other.prj")
    monkeypatch.setattr(A, "dispatch", lambda *a: pytest.fail("Unverified request was dispatched"))
    with pytest.raises(D.DispatchError) as rejected:
        D.dispatch(
            scene.grant,
            "apply_changeset",
            params,
            editing_scope=editing_scope,
            sdk_operation_id="op-confirmed",
            authorization_binding=binding,
        )
    assert rejected.value.dispatch_started is False
    assert scene.grant.busy is False


def test_real_scope_same_project_repeated_read_and_one_edit_use_same_application(
    scene, editing_scope, tmp_path, monkeypatch
):
    scene.owner.project = str(tmp_path / "own.prj")
    scene.owner.project_open_verified = True
    scene.document = {"open": True, "path": B.W._path(scene.owner.project)}
    calls = []

    def apply(method, params):
        calls.append((method, D.current_application()))
        return {"returned": True}

    monkeypatch.setattr(A, "dispatch", apply)
    for method in ("snapshot", "apply_changeset", "snapshot", "identity"):
        D.dispatch(
            scene.grant,
            method,
            {"project": scene.owner.project, "observe_only": True},
            editing_scope=editing_scope,
            sdk_operation_id="op-confirmed",
            authorization_binding=_authorization(editing_scope),
        )
    assert [method for method, _ in calls] == [
        "snapshot",
        "apply_changeset",
        "snapshot",
        "identity",
    ]
    assert all(app is scene.app for _, app in calls)


def test_owner_prompt_context_cannot_construct_gui_answerer(scene, monkeypatch):
    monkeypatch.setattr(
        A, "_PromptAnswerer", lambda *a, **k: pytest.fail("Owner cannot answer GUI")
    )

    def fake_dispatch(method, params):
        with A._designer_prompt_context() as prompts:
            assert prompts is None
        return {"same_app": D.current_application() is scene.app}

    monkeypatch.setattr(A, "dispatch", fake_dispatch)
    assert D.dispatch(scene.grant, "identity", {}) == {"same_app": True}


def test_target_resolution_failure_cannot_leave_grant_busy(scene, monkeypatch):
    monkeypatch.setattr(D, "authorize", lambda *a: None)
    monkeypatch.setattr(A, "dispatch", lambda *a: pytest.fail("Failed target reached adapter"))

    class UnavailablePath:
        def __init__(self, *args):
            pass

        def resolve(self):
            raise OSError("synthetic target unavailable")

    monkeypatch.setattr(D, "Path", UnavailablePath)
    with pytest.raises(OSError):
        D.dispatch(scene.grant, "clone_project", {"project": "C:/fresh/own.prj"}, editing_scope={})
    assert scene.grant.busy is False
    assert D.current_application() is None
    D.close(scene.grant)
    assert scene.grant.app is None


@pytest.mark.parametrize(
    "method,params,operation,reason",
    [
        ("apply_changeset", {}, "query-fixture", "query_observation_cannot_authorize_writing"),
        ("snapshot", {}, "op-confirmed", "read_only_snapshot_selector_required"),
        ("snapshot", {"observe_only": 1}, "op-confirmed", "read_only_snapshot_selector_required"),
    ],
)
def test_query_and_snapshot_cannot_silently_open_or_write(
    scene, editing_scope, tmp_path, monkeypatch, method, params, operation, reason
):
    scene.owner.project = str(tmp_path / "own.prj")
    monkeypatch.setattr(A, "dispatch", lambda *a: pytest.fail("Readonly contract was bypassed"))
    with pytest.raises(D.DispatchError, match=reason) as failure:
        D.dispatch(
            scene.grant,
            method,
            {"project": scene.owner.project, **params},
            editing_scope=editing_scope,
            sdk_operation_id=operation,
            authorization_binding=_authorization(editing_scope, operation=operation),
        )
    assert failure.value.dispatch_started is False and scene.grant.busy is False
