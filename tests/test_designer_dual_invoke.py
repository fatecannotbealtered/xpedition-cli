"""Strict no-CAD tests for the fixed151 dual Count contract and ownership."""

from __future__ import annotations

import copy
import sys
import threading
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_dual_invoke as D
from xpedition_cli import designer_window_identity as W


class NativeError(Exception):
    hresult = -2147467262


class Raw:
    def __init__(self, iid, *, library=None):
        self.iid, self.library, self.canonical = iid, library, object()
        self.wrong_iid = self.wrong_identity = False

    def QueryInterface(self, iid, *args):
        if iid == "IUnknown":
            return object() if self.wrong_identity else self.canonical
        if self.wrong_iid:
            raise NativeError("secret endpoint must not escape")
        return self

    def GetTypeInfo(self, index, lcid):
        assert (index, lcid) == (0, 0)
        return NS(
            GetTypeAttr=lambda: NS(iid=self.iid),
            GetContainingTypeLib=lambda: (self.library, 0),
        )


class Declaration:
    def __init__(self, iid, count_id):
        self.iid, self.count_id, self.kind, self.flags = iid, count_id, 4, 64
        self.result_vt, self.param_flags, self.optional = 22, 10, 0
        self.input_count, self.dispid, self.invkind = 1, count_id, 2
        self.callconv, self.vtable_offset = 4, 56
        self.twin = None

    def GetTypeAttr(self):
        return NS(guid=self.iid, typekind=self.kind, wTypeFlags=self.flags, cFuncs=1)

    def GetRefTypeOfImplType(self, index):
        assert index == -1
        return 0

    def GetRefTypeInfo(self, reference):
        assert reference == 0
        return self.twin

    def GetFuncDesc(self, index):
        assert index == 0
        param = NS(
            _=NS(paramdesc=NS(wParamFlags=self.param_flags)),
            tdesc=NS(vt=26, lptdesc=NS(contents=NS(vt=self.result_vt))),
        )
        return NS(
            memid=self.dispid,
            invkind=self.invkind,
            funckind=1,
            cParams=self.input_count,
            cParamsOpt=self.optional,
            elemdescFunc=NS(tdesc=NS(vt=25)),
            lprgelemdescParam=[param],
            wFuncFlags=0,
            callconv=self.callconv,
            cScodes=0,
            oVft=self.vtable_offset,
        )

    def GetNames(self, member, count):
        assert count == 1
        return ["Count"]


@pytest.fixture
def scene(monkeypatch):
    state = NS(
        calls=[],
        app_role="{EA4ABD71-84B0-11CE-8237-00001B4D36B5}",
        major=151,
        syskind=3,
        count=0,
        vt=3,
        invoke_error=False,
        cleanup_failure=False,
        budget_rejection=None,
        instances=[],
        invoke_infos=[],
        source_checks=0,
    )
    declarations = {}
    for iid, dispid in ((D.DOCS_IID, 1), (D.SHEETS_IID, 101)):
        info, twin = Declaration(iid, dispid), Declaration(iid, dispid)
        twin.kind = 3
        info.twin = twin
        declarations[iid] = info
    library = Raw(D.LIBRARY_IID)
    library.GetLibAttr = lambda: (W.VIEWDRAW_GUID, 0, state.syskind, state.major, 0, 8)
    library.GetDocumentation = lambda index: ("ViewDraw",)
    typed_library = NS(
        GetLibAttr=lambda: NS(
            guid=W.VIEWDRAW_GUID,
            syskind=state.syskind,
            wMajorVerNum=state.major,
            wMinorVerNum=0,
            lcid=0,
        ),
        GetTypeInfoOfGuid=lambda iid: declarations[iid],
    )
    local_declarations = copy.deepcopy(declarations)
    local_library = NS(GetTypeInfoOfGuid=lambda iid: local_declarations[iid])
    documents, sheets = Raw(D.DOCS_IID), Raw(D.SHEETS_IID)
    app = Raw(state.app_role, library=library)
    app.GetIDsOfNames = lambda lcid, name: {"Documents": 1, "SchematicSheetDocuments": 212}[name]

    def invoke(member, lcid, kind, result, args):
        assert (lcid, result, args) == (0, (9, 0), ())
        assert (member, kind) in {(1, 2), (212, 1)}
        state.calls.append(("getter", member))
        return documents if member == 1 else sheets

    app.InvokeTypes = invoke

    class Budget:
        def read(self, point, operation):
            state.calls.append(("read", point))
            assert len(state.calls) <= 150
            if point == "dual_count.count.ITypeInfo_Invoke" and state.budget_rejection == "before":
                raise D.DualInvokeError("fixture_budget_before_dispatch")
            value = operation()
            if point == "dual_count.count.ITypeInfo_Invoke" and state.budget_rejection == "after":
                raise D.DualInvokeError("fixture_budget_after_return")
            return value

    class Bridge:
        def __init__(self, probe):
            self.probe, self.entries, self.refs = probe, [], []
            self.guid = str
            state.instances.append(self)

        def hold(self, point, operation):
            value = self.probe.read(point, operation)
            self.refs.append(value)
            return value

        def prepare(self):
            state.calls.append(("prepare",))

        def exact(self, raw, iid):
            state.calls.append(("exact", iid))
            raw.QueryInterface(iid)
            if raw.wrong_identity:
                raise D.DualInvokeError("dual_count_canonical_mismatch")
            self.entries.append(
                {
                    "cookie": len(self.entries) + 1,
                    "register_attempted": True,
                    "register_returned": True,
                    "revoke_attempted": False,
                    "revoke_returned": None,
                }
            )
            self.refs.append(raw)
            return (typed_library if iid == D.LIBRARY_IID else raw), raw, raw.canonical

        def invoke_count(self, info, interface, dispid, observation):
            def invoke():
                observation["invoke_attempted"] = True
                state.invoke_infos.append(info)
                state.calls.append(("count", dispid))
                if state.invoke_error:
                    raise NativeError("private path must not escape")
                observation["invoke_returned"] = True
                return state.vt, state.count

            return self.probe.read("count.ITypeInfo_Invoke", invoke)

        def release(self):
            for entry in self.entries:
                if not entry["revoke_attempted"]:
                    entry.update(revoke_attempted=True, revoke_returned=not state.cleanup_failure)
            if state.cleanup_failure:
                self.probe.cleanup_error(NativeError(), "git.RevokeInterfaceFromGlobal")
                return False
            self.refs.clear()
            return True

    monkeypatch.setattr(D, "_NativeBridge", Bridge)

    def local(self, attributes):
        self.result["type_library_source"].update(status="observed", regkind=2)
        return local_library

    def stable(self):
        state.source_checks += 1

    monkeypatch.setattr(D.ReadonlyCountProbe, "_load_local_library", local)
    monkeypatch.setattr(D.ReadonlyCountProbe, "_verify_local_source", stable)
    state.app, state.documents, state.sheets = app, documents, sheets
    state.library, state.declarations = library, declarations
    state.local_declarations = local_declarations
    state.typed_library, state.local_library = typed_library, local_library
    state.probe = D.ReadonlyCountProbe(
        app,
        NS(IID_IUnknown="IUnknown", VT_DISPATCH=9),
        Budget(),
        owner_thread=threading.current_thread(),
        owner_ident=threading.get_ident(),
    )
    return state


def test_two_fixed_counts_use_formal_current_library_with_no_default_child_typeinfo(scene):
    scene.documents.GetTypeInfo = lambda *args: pytest.fail("Child default role is irrelevant")
    scene.sheets.GetTypeInfo = scene.documents.GetTypeInfo
    result = scene.probe.observe()
    assert result["status"] == "observed"
    assert result["native_writer_admitted"] is False
    assert [r for r in scene.calls if r[0] == "count"] == [("count", 1), ("count", 101)]
    assert all(
        row["value"] == 0 and row["same_canonical"] is True for row in result["counts"].values()
    )
    assert result["cleanup"]["resources_released"] is True
    assert result["cleanup"]["revoke_attempts"] == result["cleanup"]["revoke_returned_count"] == 3
    assert state_invoked_local_only(scene)
    assert scene.source_checks == 4
    with pytest.raises(D.DualInvokeError, match="already_attempted"):
        scene.probe.observe()


def state_invoked_local_only(scene):
    local = [value.twin for value in scene.local_declarations.values()]
    return len(scene.invoke_infos) == 2 and all(value in local for value in scene.invoke_infos)


@pytest.mark.parametrize(
    "fault",
    ["library", "syskind", "app_iid", "target_iid", "canonical", "parameters", "retval", "member"],
)
def test_wrong_source_or_count_contract_never_invokes_that_member(scene, fault):
    if fault == "library":
        scene.major = 150
    elif fault == "syskind":
        scene.syskind = 1
    elif fault == "app_iid":
        scene.app.iid = D.DOCS_IID
    elif fault == "target_iid":
        scene.documents.wrong_iid = True
    elif fault == "canonical":
        scene.documents.wrong_identity = True
    else:
        declaration = scene.declarations[D.DOCS_IID].twin
        setattr(
            declaration,
            {"parameters": "input_count", "retval": "result_vt", "member": "dispid"}[fault],
            4,
        )
    result = scene.probe.observe()
    assert result["status"] == "incomplete"
    assert ("count", 1) not in scene.calls
    assert result["cleanup"]["resources_released"] is True
    assert result["native_writer_admitted"] is False


@pytest.mark.parametrize("count,vt", [(True, 3), (-1, 3), (65, 3), (0, 11), (None, 3)])
def test_failed_or_invalid_count_never_becomes_observed_zero(scene, count, vt):
    scene.count, scene.vt = count, vt
    result = scene.probe.observe()
    assert all(x["status"] == "error" and x["value"] is None for x in result["counts"].values())
    assert result["cleanup"]["resources_released"] is True


def test_native_exception_has_fixed_callpoint_null_value_and_no_external_text(scene):
    scene.invoke_error = True
    result = scene.probe.observe()
    assert all(
        x["value"] is None and x["hresult"] == -2147467262 for x in result["counts"].values()
    )
    assert "private path" not in str(result)
    assert result["cleanup"]["resources_released"] is True


@pytest.mark.parametrize("field", ["callconv", "vtable_offset"])
def test_local_and_remote_full_count_contract_mismatch_blocks_invoke(scene, field):
    declaration = scene.local_declarations[D.DOCS_IID].twin
    setattr(declaration, field, getattr(declaration, field) + 8)
    result = scene.probe.observe()
    assert result["status"] == "incomplete"
    assert ("count", 1) not in scene.calls
    assert result["counts"]["documents"]["different_contract_fields"] == [
        "vtable_offset" if field == "vtable_offset" else "callconv"
    ]


@pytest.mark.parametrize("phase", ["before", "after"])
def test_budget_guards_distinguish_no_dispatch_from_completed_unvalidated_read(scene, phase):
    scene.budget_rejection = phase
    result = scene.probe.observe()
    assert result["status"] == "incomplete"
    assert all(
        row["status"] == "error" and row["value"] is None for row in result["counts"].values()
    )
    assert all(
        row["invoke_attempted"] is (phase == "after")
        and row["invoke_returned"] is (True if phase == "after" else None)
        and row["return_vartype"] is None
        for row in result["counts"].values()
    )
    assert [row for row in scene.calls if row[0] == "count"] == (
        [("count", 1), ("count", 101)] if phase == "after" else []
    )
    assert result["cleanup"]["resources_released"] is True


def test_cleanup_failure_is_sticky_retains_graph_and_cannot_replay_probe(scene):
    scene.cleanup_failure = True
    result = scene.probe.observe()
    bridge = scene.instances[0]
    assert result["status"] == "cleanup_incomplete"
    assert result["cleanup"]["resources_released"] is False
    assert result["cleanup"]["revoke_returned_count"] == 0
    assert bridge.refs and scene.probe.application is scene.app
    with pytest.raises(D.DualInvokeError, match="already_attempted"):
        scene.probe.observe()
    assert all(e["revoke_attempted"] and e["revoke_returned"] is False for e in bridge.entries)


def test_unknown_registration_is_not_reported_as_zero_registered_or_successful_cleanup(
    scene, monkeypatch
):
    base = D._NativeBridge

    class RegistrationUnknown(base):
        def exact(self, raw, iid):
            self.refs.append(raw)
            self.entries.append(
                {
                    "cookie": None,
                    "register_attempted": True,
                    "register_returned": None,
                    "revoke_attempted": False,
                    "revoke_returned": None,
                }
            )
            raise NativeError()

        def release(self):
            return False

    monkeypatch.setattr(D, "_NativeBridge", RegistrationUnknown)
    result = scene.probe.observe()
    assert result["status"] == "cleanup_incomplete"
    assert result["cleanup"]["resources_released"] is False
    assert result["cleanup"]["registered_cookie_count"] is None
    assert result["cleanup"]["known_registered_cookie_count"] == 0
    assert result["cleanup"]["registration_attempts"] == 1
    assert result["cleanup"]["registration_returned_count"] == 0
    assert result["cleanup"]["revoke_attempts"] == 0
    assert scene.instances[-1].refs and scene.probe.application is scene.app


def test_foreign_thread_cannot_acquire_any_native_resource(scene):
    outcomes = []
    worker = threading.Thread(
        target=lambda: outcomes.append(pytest.raises(D.DualInvokeError, scene.probe.observe))
    )
    worker.start()
    worker.join(1)
    assert not worker.is_alive() and len(outcomes) == 1
    assert scene.calls == [] and scene.instances == []


@pytest.mark.parametrize("phase", ["before", "after", "native_error"])
def test_actual_invoke_primitive_records_the_native_boundary_not_budget_entry(monkeypatch, phase):
    native_calls = []

    def read(point, operation):
        assert point == "count.ITypeInfo_Invoke"
        if phase == "before":
            raise D.DualInvokeError("fixture_budget_before_dispatch")
        value = operation()
        if phase == "after":
            raise D.DualInvokeError("fixture_budget_after_return")
        return value

    def invoke(*args):
        native_calls.append(True)
        if phase == "native_error":
            raise NativeError()

    monkeypatch.setitem(
        sys.modules,
        "comtypes.automation",
        NS(DISPPARAMS=lambda: NS(), VARIANT=lambda: NS(vt=3, value=0)),
    )
    bridge = D._NativeBridge(NS(read=read))
    bridge.ctypes = NS(cast=lambda *args: None, c_void_p=object, byref=lambda value: value)
    observation = {"invoke_attempted": False, "invoke_returned": None}
    with pytest.raises((D.DualInvokeError, NativeError)):
        bridge.invoke_count(NS(Invoke=invoke), object(), 1, observation)
    assert len(native_calls) == (0 if phase == "before" else 1)
    assert observation["invoke_attempted"] is (phase != "before")
    assert observation["invoke_returned"] is (True if phase == "after" else None)
    assert len(bridge.refs) == 3  # VARIANT and parameter storage remains owned.


@pytest.mark.parametrize(
    "fault", [None, "pre_qi_identity", "recipient_identity", "null", "partial", "register_unknown"]
)
def test_actual_transfer_registers_only_canonical_unknown_even_with_distinct_dual_pointer(fault):
    canonical, precise_pointer, typed_pointer = object(), object(), object()
    calls = []

    class Required:
        def QueryInterface(self, iid):
            assert iid == "IUnknown"
            return object() if fault == "pre_qi_identity" else canonical

    class Raw:
        def QueryInterface(self, iid, wrapped=None):
            if iid == "IUnknown":
                return canonical
            assert (iid, wrapped) == (D.DOCS_IID, "IUnknown")
            calls.append(("exact_source", precise_pointer))
            return Required()

    class Recipient:
        acquired = False

        def __bool__(self):
            return self.acquired

        def QueryInterface(self, interface):
            assert interface == "exact-documents" and self.acquired
            return typed_pointer

    class NativeGit:
        def RegisterInterfaceInGlobal(self, value, iid):
            assert value is canonical and value is not precise_pointer
            assert iid == "IUnknown"
            calls.append(("register", value, iid))
            if fault == "register_unknown":
                raise NativeError()  # Registration may exist without a returned cookie.
            return 19

        def RevokeInterfaceFromGlobal(self, cookie):
            assert cookie == 19
            calls.append(("revoke", cookie))

    class TypedGit:
        def GetInterfaceFromGlobal(self, cookie, iid, recipient):
            assert cookie == 19 and iid == "IUnknown"
            if fault != "null":
                recipient.acquired = True
            if fault == "partial":
                raise NativeError()

    wrapper = NS(
        QueryInterface=lambda iid: object() if fault == "recipient_identity" else canonical
    )
    probe = NS(
        com=NS(
            IID_IUnknown="IUnknown",
            IID_IDispatch="IDispatch",
            ObjectFromAddress=lambda address, iid: wrapper,
        ),
        read=lambda point, operation: operation(),
        owner=lambda: None,
        cleanup_error=lambda error, point: None,
    )
    bridge = D._NativeBridge(probe)
    bridge.ctypes = NS(
        POINTER=lambda interface: Recipient,
        byref=lambda value: value,
        cast=lambda pointer, kind: NS(value=7001),
        c_void_p=object,
    )
    bridge.IUnknown = NS(_iid_="IUnknown")
    bridge.interfaces = {D.DOCS_IID: "exact-documents"}
    bridge.native_git, bridge.typed_git = NativeGit(), TypedGit()
    if fault is None:
        transferred, returned, identity = bridge.exact(Raw(), D.DOCS_IID)
        assert transferred is typed_pointer and returned is wrapper and identity is canonical
    else:
        with pytest.raises((D.DualInvokeError, NativeError)):
            bridge.exact(Raw(), D.DOCS_IID)
    assert len([call for call in calls if call[0] == "register"]) == (
        0 if fault == "pre_qi_identity" else 1
    )
    released = bridge.release()
    assert released is (fault not in {"partial", "register_unknown"})
    assert len([call for call in calls if call[0] == "revoke"]) == (
        0 if fault in {"pre_qi_identity", "register_unknown"} else 1
    )
    if fault in {"partial", "register_unknown"}:
        assert bridge.refs and bridge.uncertain is True
        assert bridge.release() is False
        assert len([call for call in calls if call[0] == "revoke"]) == (
            0 if fault == "register_unknown" else 1
        )
        if fault == "register_unknown":
            assert bridge.entries[0]["register_attempted"] is True
            assert bridge.entries[0]["register_returned"] is None
            assert bridge.entries[0]["cookie"] is None
    else:
        assert bridge.refs == []
