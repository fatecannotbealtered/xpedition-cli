"""Pure metadata probes; no native object is activated or member invoked."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_host_bootstrap as B
from xpedition_cli import designer_native_owner as O

SHEET_IID = "{608CA7F4-A52A-4206-9580-3BEB17CE0073}"
DISPATCH = "{00020400-0000-0000-C000-000000000046}"
UNKNOWN = "{00000000-0000-0000-C000-000000000046}"


class NativeError(Exception):
    def __init__(self, hresult):
        self.hresult = hresult
        super().__init__("private path objref:secret must not escape")


class Info:
    def __init__(self, iid, name, library):
        self.iid, self.name, self.library = iid, name, library

    def GetTypeAttr(self):
        return NS(iid=self.iid, typekind=4, wTypeFlags=4160)

    def GetDocumentation(self, index):
        assert index == -1
        return (self.name,)

    def GetContainingTypeLib(self):
        return self.library, 0


class Library:
    def __init__(self):
        self.major = 151
        self.docs = Info(O.DOCS_IID, "IVdDocs", self)
        self.sheet = Info(SHEET_IID, "IVdSchematicSheetDocuments", self)

    def GetLibAttr(self):
        return (B.W.VIEWDRAW_GUID, 0, 3, self.major, 0, 8)

    def GetDocumentation(self, index):
        assert index == -1
        return ("ViewDraw",)

    def GetTypeInfoOfGuid(self, iid):
        return {O.DOCS_IID: self.docs, SHEET_IID: self.sheet}[iid]


class Raw:
    def __init__(self, info, calls, *, canonical=None):
        self.info, self.calls = info, calls
        self.canonical = canonical if canonical is not None else object()
        self.queried = self
        self.member = 110
        self.query_error = self.role_error = None

    def QueryInterface(self, iid, wrapper=None):
        self.calls.append(("QI", iid))
        if iid == UNKNOWN:
            return self.canonical
        assert iid in {O.DOCS_IID, SHEET_IID} and wrapper == DISPATCH
        if self.query_error:
            raise self.query_error
        return self.queried

    def GetTypeInfo(self, index, lcid):
        assert (index, lcid) == (0, 0)
        if self.role_error:
            raise self.role_error
        return self.info

    def GetIDsOfNames(self, lcid, name):
        assert (lcid, name) == (0, "Open")
        self.calls.append(("member", name))
        return self.member

    def InvokeTypes(self, *args):
        pytest.fail("A collection/document member must never be invoked by this probe")


@pytest.fixture
def scene():
    calls, library = [], Library()
    # The object can support IVdDocs QI while its default dispatch reports the
    # SheetDocs type info and Open110. This is an observation, not admission.
    docs = Raw(library.sheet, calls)
    docs.queried = Raw(library.sheet, calls, canonical=docs.canonical)
    sheets = Raw(library.sheet, calls)
    state = NS(calls=calls, library=library, docs=docs, sheets=sheets, getter=None, reads=0)

    class App:
        def GetTypeInfo(self, index, lcid):
            assert (index, lcid) == (0, 0)
            return Info("{EA4ABD71-84B0-11CE-8237-00001B4D36B5}", "IVdApp", library)

        def GetIDsOfNames(self, lcid, name):
            assert lcid == 0
            return {"Documents": 1, "SchematicSheetDocuments": 212}[name]

        def InvokeTypes(self, member, lcid, kind, result, args):
            assert (lcid, result, args) == (0, (9, 0), ())
            assert (member, kind) in {(1, 2), (212, 1)}
            calls.append(("application_getter", member, kind))
            if member == 1:
                return state.getter() if state.getter else docs
            return sheets

    class Budget:
        def read(self, point, operation):
            state.reads += 1
            assert state.reads <= 128
            return operation()

    state.application, state.budget = App(), Budget()
    state.com = NS(
        IID_IUnknown=UNKNOWN, IID_IDispatch=DISPATCH, VT_DISPATCH=9, TypeIIDs={DISPATCH: Raw}
    )
    return state


def probe(scene):
    return O._document_dispatch_roles(scene.application, scene.com, scene.budget)


def test_base_qi_and_same_canonical_do_not_relabel_default_dispatch_or_admit(scene):
    result = probe(scene)
    points = result["observations"]
    assert points["documents.returned.Open_member"]["value"] == 110
    assert points["documents.queried.Open_member"]["value"] == 110
    assert points["documents.same_canonical"]["value"] is True
    assert points["documents.queried.role"]["value"]["interface_iid"] == B.W._guid(SHEET_IID)
    assert points["documents.declared.role"]["value"]["interface_iid"] == B.W._guid(O.DOCS_IID)
    assert result["metadata_observation_complete"] is True
    assert result["diagnostic_only"] is True and result["native_writer_admitted"] is False
    assert result["collection_member_invocations"] == 0
    assert [row for row in scene.calls if row[0] == "application_getter"] == [
        ("application_getter", 1, 2),
        ("application_getter", 212, 1),
    ]


def test_canonical_mismatch_and_wrong_library_are_reported_as_actual_facts(scene):
    scene.docs.queried.canonical = object()
    scene.library.major = 150
    result = probe(scene)
    assert result["observations"]["documents.same_canonical"]["value"] is False
    assert (
        result["observations"]["documents.queried.role"]["value"]["current_viewdraw_151"] is False
    )
    assert result["native_writer_admitted"] is False


def test_null_getter_never_becomes_zero_count_or_a_missing_interface_verdict(scene):
    scene.getter = lambda: None
    result = probe(scene)
    assert result["observations"]["documents.getter"]["status"] == "observed_null"
    assert result["observations"]["documents.queried.role"]["status"] == "not_attempted"
    assert result["observations"]["documents.same_canonical"]["value"] is None
    assert result["metadata_observation_complete"] is False
    assert "Count" not in json.dumps(result)


def test_role_failure_preserves_independent_name_resolution_without_error_text(scene):
    scene.docs.role_error = NativeError(-2147417848)
    result = probe(scene)
    point = result["observations"]["documents.returned.role"]
    assert point["status"] == "error" and point["hresult"] == -2147417848
    assert point["failed_callpoint"] == "document_role.typeinfo"
    assert result["observations"]["documents.application_declared.role"]["status"] == "observed"
    assert result["observations"]["documents.returned.Open_member"]["value"] == 110
    assert result["observations"]["sheet_documents.returned.role"]["status"] == "observed"
    assert "secret" not in json.dumps(result) and "private path" not in json.dumps(result)


def test_missing_requested_interface_preserves_unobserved_canonical_and_queried_role(scene):
    scene.docs.query_error = NativeError(-2147467262)
    result = probe(scene)
    points = result["observations"]
    assert points["documents.requested_interface"]["status"] == "unsupported"
    assert points["documents.queried.role"]["status"] == "not_attempted"
    assert points["documents.same_canonical"]["value"] is None
    assert points["sheet_documents.same_canonical"]["value"] is True


def test_bool_dispatch_id_is_error_not_a_numeric_mapping(scene):
    scene.docs.member = True
    result = probe(scene)
    point = result["observations"]["documents.returned.Open_member"]
    assert point["status"] == "error" and point["value"] is None
    assert result["collection_member_invocations"] == 0
