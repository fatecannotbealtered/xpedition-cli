"""151 owned document/sheet dispatch only; no native object is activated."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A


class Interface:
    def __init__(self, iid, methods, calls):
        self.iid, self.methods, self.calls = iid, methods, calls
        self.identity, self.wrong_iid = object(), False

    def QueryInterface(self, iid, wrapper=None):
        if iid == A._IUNKNOWN_IID:
            return self.identity
        if self.wrong_iid:
            raise RuntimeError("fixture E_NOINTERFACE")
        assert (iid, wrapper) == (self.iid, A._IDISPATCH_IID)
        return self

    def GetIDsOfNames(self, lcid, name):
        assert lcid == 0
        return self.methods[name][0]

    def InvokeTypes(self, member, lcid, kind, result, args, *values):
        rows = [row for row in self.methods.values() if row[0] == member]
        assert len(rows) == 1
        _id, expected_kind, expected_result, expected_args, fn = rows[0]
        assert (lcid, kind, result, args) == (0, expected_kind, (expected_result, 0), expected_args)
        assert len(values) == len(args)
        self.calls.append((self.iid, member, kind, values))
        return fn(*values)


@pytest.fixture
def docs(monkeypatch):
    state = NS(
        calls=[], schemes=["DifferentSchematic"], sheets=["1", "4"], opened=[], getter_failure=False
    )

    def strings(values):
        return Interface(
            A._STRING_LIST_IID,
            {
                "GetCount": (1, 1, 3, (), lambda: len(values)),
                "GetItem": (2, 1, 8, ((3, 1),), lambda index: values[index - 1]),
            },
            state.calls,
        )

    document = Interface(
        A._DOCUMENT_IID,
        {"Activate": (8, 1, 24, (), lambda: None), "Save": (12, 1, 24, (), lambda: None)},
        state.calls,
    )
    sheet = Interface(
        A._SHEET_DOCUMENT_IID, {"Activate": (105, 1, 24, (), lambda: None)}, state.calls
    )
    collection = Interface(
        A._SHEET_DOCUMENTS_IID,
        {
            "GetAvailableSchematics": (108, 1, 9, (), lambda: strings(state.schemes)),
            "GetAvailableSheets": (109, 1, 9, ((8, 1),), lambda name: strings(state.sheets)),
            "Open": (
                110,
                1,
                9,
                ((8, 1), (8, 1)),
                lambda name, number: state.opened.append((name, number)) or sheet,
            ),
        },
        state.calls,
    )
    documents = Interface(
        A._DOCUMENTS_IID,
        {
            "Open": (7, 1, 9, ((8, 1),), lambda name: state.opened.append(name) or document),
        },
        state.calls,
    )
    data = Interface(
        A._PROJECT_DATA_IID,
        {"GetiCDBDesigns": (9, 1, 9, (), lambda: strings(["Board1"]))},
        state.calls,
    )

    def getter():
        if state.getter_failure:
            raise RuntimeError("fixture getter failure")
        return documents

    raw = Interface(
        "app",
        {
            "GetProjectData": (228, 1, 9, (), lambda: data),
            "Documents": (1, 2, 9, (), getter),
            "SchematicSheetDocuments": (212, 1, 9, (), lambda: collection),
        },
        state.calls,
    )

    class App:
        _oleobj_ = raw

        def __getattr__(self, name):
            pytest.fail("Dynamic/default dispatch is forbidden: " + name)

    state.app, state.documents, state.fixed_calls = App(), documents, []

    def fixed_boundary(app, operation, argument=None):
        # Adapter unit boundary only. The real opaque grant and formal local
        # TypeInfo invocation are covered by the fixed-writer bridge tests.
        assert app is state.app and operation == "open_design"
        state.fixed_calls.append((operation, argument))
        collection = A._designer_documents(app)
        result = A._designer_declared_call(collection, "Open", 7, 9, ((8, 1),), (argument,))
        return A._designer_typed_dispatch(result, A._DOCUMENT_IID)

    monkeypatch.setattr(D, "invoke_document_member", fixed_boundary)
    monkeypatch.setattr(A, "_owner_application", lambda: state.app)
    monkeypatch.setattr(A, "_settle", lambda seconds: None)
    return state


def test_documents_getter_is_property_and_open_is_one_required_bstr_method(docs):
    result = A._designer_open_design(docs.app, "Board1")
    assert result.iid == A._DOCUMENT_IID and docs.opened == ["Board1"]
    assert ("app", 1, 2, ()) in docs.calls
    assert (A._DOCUMENTS_IID, 7, 1, ("Board1",)) in docs.calls
    assert not any(row[0] == A._DOCUMENT_IID for row in docs.calls)
    assert docs.fixed_calls == [("open_design", "Board1")]


def test_legacy_document_open_retains_raw_contract_without_owner_route(docs, monkeypatch):
    monkeypatch.setattr(A, "_owner_application", lambda: None)
    assert A._designer_open_design(docs.app, "Board1").iid == A._DOCUMENT_IID
    assert docs.fixed_calls == [] and docs.opened == ["Board1"]


@pytest.mark.parametrize("fault", ["getter", "iid", "unobserved_design"])
def test_document_failure_never_retries_or_calls_a_default_member(docs, fault):
    if fault == "getter":
        docs.getter_failure = True
    elif fault == "iid":
        docs.documents.wrong_iid = True
    with pytest.raises((A.AdapterError, RuntimeError)):
        A._designer_open_design(
            docs.app, "WrongDesign" if fault == "unobserved_design" else "Board1"
        )
    assert docs.opened == []


def test_sheet_census_and_exact_open_use_observed_names_without_makepy(docs):
    assert A._sheet_numbers(docs.app) == {1: "DifferentSchematic", 4: "DifferentSchematic"}
    A._activate_sheet(docs.app, 4)
    assert docs.opened == [("DifferentSchematic", "4")]
    assert (A._SHEET_DOCUMENTS_IID, 110, 1, ("DifferentSchematic", "4")) in docs.calls
    assert (A._SHEET_DOCUMENT_IID, 105, 1, ()) in docs.calls


def test_missing_sheet_or_ambiguous_schematic_never_falls_back_to_one(docs):
    with pytest.raises(A.AdapterError, match="does not exist"):
        A._activate_sheet(docs.app, 3)
    docs.schemes = ["First", "Second"]
    with pytest.raises(A.AdapterError, match="unambiguous"):
        A._schematic_name(docs.app)
    assert docs.opened == []


def test_snapshot_does_not_swallow_owned_sheet_observation_failure(docs, monkeypatch):
    docs.app.GetActiveDesign = lambda: "Board1"

    class Empty(list):
        Count = 0

    monkeypatch.setattr(A, "_designer_collection", lambda *args: Empty())
    monkeypatch.setattr(
        A,
        "_sheet_numbers",
        lambda app: (_ for _ in ()).throw(A.AdapterError("E_NATIVE_FAILURE", "fixture")),
    )
    with pytest.raises(A.AdapterError, match="fixture"):
        A._designer_snapshot(docs.app, {"design": "Board1"})


@pytest.mark.parametrize("opened", [False, True])
def test_ensure_preserves_empty_session_semantics_but_never_hides_open_project_path_error(
    monkeypatch, tmp_path, opened
):
    from xpedition_cli import designer_owner_dispatch as D

    target, calls = tmp_path / "new.prj", []
    app = NS(IsProjectOpened=lambda: opened)
    owner = NS(confirm_project_open=lambda: calls.append("confirm"))
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    monkeypatch.setattr(
        A,
        "_designer_project_path",
        lambda app: (_ for _ in ()).throw(RuntimeError("unavailable path")),
    )
    monkeypatch.setattr(A, "_open_project_answering", lambda app, path: calls.append("open"))
    monkeypatch.setattr(A, "_settle", lambda seconds: None)
    monkeypatch.setattr(D, "_project_context", lambda *args: (None, owner))
    token = D._CURRENT.set((None, "open"))
    try:
        if opened:
            with pytest.raises(A.AdapterError):
                A._ensure_project(app, target)
            assert calls == []
        else:
            A._ensure_project(app, target)
            assert calls == ["open", "confirm"]
    finally:
        D._CURRENT.reset(token)
