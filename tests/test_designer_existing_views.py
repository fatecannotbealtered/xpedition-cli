"""Existing-view diagnostics use only explicit fake COM/window/process endpoints."""

from types import SimpleNamespace

import pytest
from designer_identity_fakes import FakeComError, Scene, hard_no_native  # noqa: F401

from xpedition_cli import designer_existing_views as G
from xpedition_cli import native_identity as N
from xpedition_cli import native_process as P
from xpedition_cli import native_release_preflight as R


@pytest.fixture(autouse=True)
def no_default_dispatch_factory(monkeypatch):
    hits = []

    def refuse(value):
        hits.append(True)
        pytest.fail("A fake enum must explicitly inject its existing-dispatch wrapper")

    monkeypatch.setattr(G, "_wrap_dispatch", refuse)
    yield
    assert hits == []


def prerequisite():
    return {
        "status": "incomplete",
        "stage": "formal_candidates",
        "reason": "no_complete_formal_window_binding",
        "attempts": [
            {
                "source": G.W.SOURCE_APP,
                "stage": "query_interface",
                "status": "unsupported",
                "reason": "iolewindow_not_supported",
                "hresult_hex": "0x80004002",
            },
            {
                "source": G.W.SOURCE_FRAMEWORK,
                "stage": "formal_provider",
                "call_point": "framework.return_dispatch",
                "reason": "framework_dispatch_wrapper_missing",
                "framework_observation": {
                    "getter_callable": True,
                    "framework_return_present": False,
                    "dispatch_wrapper_present": False,
                },
            },
        ],
    }


class Info:
    def __init__(self, iid):
        self.iid = iid

    def GetTypeAttr(self):
        return SimpleNamespace(iid=self.iid, cFuncs=4)

    def GetContainingTypeLib(self):
        return SimpleNamespace(GetLibAttr=lambda: (G.W.VIEWDRAW_GUID, 0, 3, 151, 0, 8)), 0


class Object:
    def __init__(self, graph, iid):
        self.graph, self.unknown, self.info = graph, object(), Info(iid)
        self.typeinfo_count = 1
        self._oleobj_ = SimpleNamespace(
            QueryInterface=self.query,
            GetTypeInfo=self.get_typeinfo,
            GetTypeInfoCount=lambda: self.typeinfo_count,
        )

    def get_typeinfo(self, locale=1024, index=0):
        self.graph.calls.append(f"GetTypeInfo.{locale}.{index}")
        if (locale, index) != (0, 0):
            raise FakeComError(-2147352565)  # DISP_E_BADINDEX, e.g. unsupported default locale.
        return self.info

    def query(self, iid, use_iid=None):
        self.graph.calls.append("QI." + iid)
        if iid == "unknown":
            return self.unknown
        if iid == "{" + self.info.iid + "}":
            assert use_iid == "unknown"
            return SimpleNamespace(QueryInterface=lambda requested: self.unknown)
        raise FakeComError()


class Enum:
    def __init__(self, collection):
        self.collection, self.cursor = collection, 0

    def QueryInterface(self, iid):
        return self.collection.unknown

    def Next(self, count):
        self.collection.graph.calls.append("Next")
        assert count == 1
        if self.cursor >= len(self.collection.values):
            return ()
        value = self.collection.values[self.cursor]
        self.cursor += 1
        return (value,)


class Collection(Object):
    def __init__(self, graph, iid, values=()):
        super().__init__(graph, iid)
        self.values, self.Application = list(values), graph.app
        self.reported_count, self.enum_unsupported = len(values), False
        self.enum = Enum(self)
        self.member_ids = dict(G.COLLECTION_MEMBERS[iid])
        self.declared_qi_unsupported = False
        self._oleobj_.GetIDsOfNames = self.get_ids
        self._oleobj_.InvokeTypes = self.invoke_types

    def get_ids(self, name):
        assert type(name) is str
        return self.member_ids[name]

    def invoke_types(self, dispid, locale, flags, result_type, args):
        assert locale == 0 and flags == 2 and args == ()
        if dispid == self.member_ids["Count"]:
            assert result_type == (3, 0)
            return self.Count
        assert dispid == self.member_ids["Application"] and result_type == (9, 0)
        return self.Application

    @property
    def Count(self):
        return self.reported_count

    def query(self, iid, use_iid=None):
        if iid == "enum":
            if self.enum_unsupported:
                raise FakeComError()
            return self.enum
        if self.declared_qi_unsupported and iid == "{" + self.info.iid + "}":
            raise FakeComError()
        return super().query(iid, use_iid)


class Document(Object):
    def __init__(self, graph):
        super().__init__(graph, G.DOCUMENT_IID)
        self.Application, self.FullName = graph.app, "PhoneMain.1"
        self.views = Collection(graph, G.VIEWS_IID)

    def GetViews(self):
        return self.views


class View(Object):
    def __init__(self, graph, document):
        super().__init__(graph, G.VIEW_IID)
        self.application, self.document, self.window_unsupported = graph.app, document, False

    def Application(self):
        return self.application

    def Document(self):
        return self.document

    def query(self, iid, use_iid=None):
        if iid == "window":
            if self.window_unsupported:
                raise FakeComError()
            return SimpleNamespace(
                QueryInterface=lambda requested: self.unknown,
                GetWindow=lambda: self.graph.scene.hwnd,
            )
        return super().query(iid, use_iid)


class RawDispatch:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def QueryInterface(self, iid):
        return self.wrapped.unknown


class Graph:
    def __init__(self, tmp_path, documents=1, views=1, raw=False):
        self.scene, self.calls = Scene(tmp_path, framework=True), []
        self.app = self.scene.app
        self.app._oleobj_.GetTypeInfo = lambda locale=0, index=0: self.scene.info
        self.app._oleobj_.GetTypeInfoCount = lambda: 1
        self.com = SimpleNamespace(
            IID_IUnknown="unknown",
            IID_IOleWindow="window",
            IID_IEnumVARIANT="enum",
            IID_IDispatch="dispatch",
            TypeIIDs={"dispatch": RawDispatch},
        )
        self.documents = [Document(self) for _ in range(documents)]
        self.all_views = []
        for doc in self.documents:
            values = [View(self, doc) for _ in range(views)]
            self.all_views.extend(values)
            doc.views = Collection(
                self, G.VIEWS_IID, [RawDispatch(v) if raw else v for v in values]
            )
        self.docs = Collection(
            self, G.DOCUMENTS_IID, [RawDispatch(d) if raw else d for d in self.documents]
        )
        self.app.SchematicSheetDocuments = lambda: self.docs
        self.wrapped = []

    def wrap(self, raw):
        assert type(raw) is RawDispatch
        self.wrapped.append(raw)
        return raw.wrapped

    def run(self, **options):
        defaults = dict(
            com_module=self.com,
            window_reader=self.scene.window_reader,
            generation_reader=self.scene.generation_reader,
            wrap_dispatch=self.wrap,
        )
        defaults.update(options)
        return G.observe(
            self.app, self.scene.document(), self.scene.exe, 40, prerequisite(), **defaults
        )


def test_empty_native_open_documents_is_complete_without_enum_or_view_creation(tmp_path):
    graph = Graph(tmp_path, 0)
    result = graph.run()
    assert result["observation_complete"] is True and result["document_count"] == 0
    assert result["reason"] == "no_existing_native_documents"
    assert result["total_view_count"] == 0 and result["authority_granted"] is False
    assert "Next" not in graph.calls


@pytest.mark.parametrize("raw", [False, True])
def test_complete_existing_graph_does_not_grant_release_authority(tmp_path, raw):
    graph = Graph(tmp_path, raw=raw)
    result = graph.run()
    assert result["observation_complete"] is True
    assert result["document_count"] == result["total_view_count"] == 1
    assert result["complete_window_relation_count"] == 1 and result["authority_granted"] is False
    assert len(graph.wrapped) == (2 if raw else 0)
    assert all(
        not any(word in call for word in ("Open", "Add", "Activate", "Reset", "Item"))
        for call in graph.calls
    )


def test_open_document_with_zero_existing_views_is_not_zero_documents(tmp_path):
    graph = Graph(tmp_path, views=0)
    result = graph.run()
    assert result["observation_complete"] is True and result["document_count"] == 1
    assert result["total_view_count"] == 0 and result["reason"] == "no_existing_native_views"


@pytest.mark.parametrize("fault", ["unsupported", "partial", "extra", "duplicate", "enum_identity"])
def test_count_positive_enum_problems_remain_incomplete_not_empty(tmp_path, fault):
    graph = Graph(tmp_path)
    if fault == "unsupported":
        graph.docs.enum_unsupported = True
    elif fault == "partial":
        graph.docs.reported_count = 2
    elif fault == "extra":
        graph.docs.values.append(Document(graph))
    elif fault == "duplicate":
        graph.docs.values.append(graph.docs.values[0])
        graph.docs.reported_count = 2
    elif fault == "enum_identity":
        graph.docs.enum.QueryInterface = lambda iid: object()
    result = graph.run()
    assert result["observation_complete"] is False and result["document_count"] > 0
    assert result["total_view_count"] is None
    if fault == "unsupported":
        assert result["reason"] == "direct_enum_unsupported_standard_new_enum_unprobed"


@pytest.mark.parametrize("value", [True, -1, "1", 65])
def test_document_count_invalid_or_over_limit_refuses_without_enum(tmp_path, value):
    graph = Graph(tmp_path)
    graph.docs.reported_count = value
    result = graph.run()
    assert result["observation_complete"] is False and "Next" not in graph.calls


@pytest.mark.parametrize(
    "fault",
    ["collection_app", "doc_app", "view_app", "view_doc", "view_type", "window_pid", "birth"],
)
def test_foreign_objects_and_physical_generation_conflicts_never_qualify(tmp_path, fault):
    graph = Graph(tmp_path)
    other = Scene(tmp_path).app
    if fault == "collection_app":
        graph.docs.Application = other
    elif fault == "doc_app":
        graph.documents[0].Application = other
    elif fault == "view_app":
        graph.all_views[0].application = other
    elif fault == "view_doc":
        graph.all_views[0].document = Document(graph)
    elif fault == "view_type":
        graph.all_views[0].info.iid = G.DOCUMENT_IID
    elif fault == "window_pid":
        graph.scene.window["pid"] = 41
    elif fault == "birth":
        original = graph.scene.generation_reader

        def changed(pid):
            if graph.scene.generation_reads:
                graph.scene.generation["birth_filetime_100ns"] = "987654321"
            return original(pid)

        graph.scene.generation_reader = changed
    result = graph.run()
    assert result["observation_complete"] is False
    assert result["complete_window_relation_count"] == 0
    assert result["authority_granted"] is False
    if fault == "window_pid":
        facts = result["documents"][0]["views"][0]["window"]
        assert facts["observed_window"]["pid"] == 41
        assert facts["expected_application_pid"] == 40


def test_view_unsupported_is_separate_from_enumeration_and_empty(tmp_path):
    graph = Graph(tmp_path)
    graph.all_views[0].window_unsupported = True
    result = graph.run()
    assert result["observation_complete"] is True and result["total_view_count"] == 1
    assert result["complete_window_relation_count"] == 0
    assert result["documents"][0]["views"][0]["window"]["status"] == "unsupported"


@pytest.mark.parametrize("value", [None, "Viewdraw.Application.151", 12])
def test_no_string_clsid_or_none_is_wrapped(tmp_path, value):
    graph = Graph(tmp_path)
    graph.docs.values = [value]
    result = graph.run()
    assert result["observation_complete"] is False and graph.wrapped == []


def test_raw_dispatch_bridge_must_preserve_canonical_identity(tmp_path):
    graph = Graph(tmp_path, raw=True)
    result = graph.run(wrap_dispatch=lambda raw: Document(graph))
    assert result["observation_complete"] is False
    assert result["reason"] == "native_dispatch_bridge_identity_mismatch"


def test_read_and_wall_limits_fail_closed_without_truncated_clear(monkeypatch, tmp_path):
    graph = Graph(tmp_path)
    monkeypatch.setattr(G, "MAX_READ_OPERATIONS", 2)
    result = graph.run()
    assert result["reason"] == "existing_view_observer_read_limit"
    monkeypatch.setattr(G, "MAX_READ_OPERATIONS", 512)
    ticks = iter((0, 31, 31))
    result = graph.run(clock=lambda: next(ticks))
    assert result["reason"] == "existing_view_observer_wall_limit"


def test_prior_identity_contradiction_does_not_start_graph(monkeypatch, tmp_path):
    graph = Graph(tmp_path)
    prior = prerequisite()
    prior["attempts"][0]["reason"] = "canonical_identity_mismatch"
    result = G.observe(graph.app, graph.scene.document(), graph.scene.exe, 40, prior)
    assert result["reason"] == "existing_view_prerequisite_not_observed"
    assert result["read_operation_count"] == 0


def test_native_identity_integration_adds_graph_without_promoting_pid_source(monkeypatch, tmp_path):
    graph = Graph(tmp_path, 0)
    graph.scene.install_dependencies(monkeypatch)
    graph.app.GetFramework = lambda: None
    monkeypatch.setattr(
        P, "unique_executable_pid", lambda domain, executable: graph.scene.rows()[0]
    )
    identity = N.application_identity(graph.app, "schematic")
    assert identity["pid_source"]["property"] == "FullName"
    assert (
        identity["designer_existing_views_observation"]["reason"] == "no_existing_native_documents"
    )
    with pytest.raises(Exception) as failure:
        R._application_pin(identity, "schematic", graph.scene.rows())
    assert failure.value.code == "E_HUMAN_REQUIRED"


def test_external_native_error_text_is_not_copied(tmp_path):
    graph = Graph(tmp_path)

    def error():
        raise AttributeError("secret/private engineering path")

    graph.app.SchematicSheetDocuments = error
    result = graph.run()
    assert result["observation_complete"] is False and "secret/private" not in str(result)


def test_view_limits_fail_without_truncating_a_complete_graph(monkeypatch, tmp_path):
    graph = Graph(tmp_path, views=17)
    result = graph.run()
    assert result["observation_complete"] is False and result["document_count"] == 1
    graph = Graph(tmp_path, documents=2, views=1)
    monkeypatch.setattr(G, "MAX_TOTAL_VIEWS", 1)
    result = graph.run()
    assert result["reason"] == "existing_native_total_view_limit"
    assert result["observation_complete"] is False


@pytest.mark.parametrize("value", [[], (None, None), "not-an-enumerator-tuple"])
def test_enum_next_requires_exact_bounded_tuple(tmp_path, value):
    graph = Graph(tmp_path)
    graph.docs.enum.Next = lambda count: value
    result = graph.run()
    assert result["reason"] == "native_enum_result_shape_unobserved"
    assert graph.wrapped == []


def test_zero_count_is_not_reported_complete_if_count_changes(monkeypatch, tmp_path):
    graph = Graph(tmp_path, 0)
    counts = iter((0, 1))
    monkeypatch.setattr(Collection, "Count", property(lambda self: next(counts)))
    result = graph.run()
    assert result["reason"] == "native_document_count_changed"
    assert result["observation_complete"] is False


@pytest.mark.parametrize("qi_supported", [True, False])
def test_zero_typeinfo_uses_declared_collection_protocol_before_real_count(tmp_path, qi_supported):
    graph = Graph(tmp_path, 0)
    graph.docs.typeinfo_count = 0
    graph.docs.declared_qi_unsupported = not qi_supported

    def no_typeinfo(*args):
        pytest.fail("A zero-typeinfo instance must not be asked for index0")

    graph.docs._oleobj_.GetTypeInfo = no_typeinfo
    result = graph.run()
    assert result["observation_complete"] is True and result["document_count"] == 0
    protocol = result["object_protocol_observations"][-1]
    assert protocol["typeinfo_count"] == 0
    assert protocol["basis"] == (
        "declared_return_interface_qi" if qi_supported else "declared_IDispatch_collection_protocol"
    )
    assert protocol["members"]["Count"]["observed_dispid"] == 101
    assert protocol["Count_read_signature"] == "PROPERTYGET/VT_I4/no_arguments"
    assert protocol["Item_argument_invocation"] == "not_probed"
    assert result["authority_granted"] is False


def test_typeinfo_one_uses_proven_neutral_locale_and_zero_index(tmp_path):
    graph = Graph(tmp_path, 0)
    with pytest.raises(FakeComError):
        graph.docs.get_typeinfo()  # The valid no-arg call uses USER_DEFAULT, not locale0.
    result = graph.run()
    assert result["observation_complete"] is True
    protocol = result["object_protocol_observations"][-1]
    assert protocol["typeinfo_count"] == 1
    assert protocol["requested_lcid"] == protocol["requested_typeinfo_index"] == 0
    assert protocol["basis"] == "runtime_typeinfo_ViewDraw151_lcid0"


def test_typeinfo_one_explicit_failure_is_not_treated_as_typeinfo_zero(tmp_path):
    graph = Graph(tmp_path, 0)

    def bad_index(locale=1024, index=0):
        raise FakeComError(-2147352565)

    graph.docs._oleobj_.GetTypeInfo = bad_index
    result = graph.run()
    assert result["observation_complete"] is False and result["document_count"] is None
    assert result["hresult_hex"] == "0x8002000b"
    assert result["call_point"] == "documents.GetTypeInfo.locale0.index0"
    assert result["object_protocol_observations"][-1]["typeinfo_count"] == 1


@pytest.mark.parametrize("value", [True, -1, 2])
def test_typeinfo_count_shape_must_be_exact_zero_or_one(tmp_path, value):
    graph = Graph(tmp_path, 0)
    graph.docs.typeinfo_count = value
    result = graph.run()
    assert result["reason"] == "native_typeinfo_count_invalid"
    assert result["document_count"] is None


@pytest.mark.parametrize("member", ["Count", "Application"])
def test_no_typeinfo_name_lookup_must_match_declared_ids(tmp_path, member):
    graph = Graph(tmp_path, 0)
    graph.docs.typeinfo_count = 0
    graph.docs.member_ids[member] += 50
    result = graph.run()
    assert result["reason"] == "declared_collection_member_dispid_mismatch"
    assert result["document_count"] is None and result["authority_granted"] is False


def test_single_name_lookup_tuple_is_not_coerced_to_an_id(tmp_path):
    graph = Graph(tmp_path, 0)
    graph.docs.typeinfo_count = 0
    graph.docs._oleobj_.GetIDsOfNames = lambda name: (101,)
    result = graph.run()
    assert result["reason"] == "declared_collection_member_dispid_mismatch"


def test_unused_default_item_zero_is_retained_unverified_without_blocking_count(tmp_path):
    graph = Graph(tmp_path, 0)
    graph.docs.typeinfo_count = 0
    graph.docs.member_ids["Item"] = 0
    result = graph.run()
    assert result["observation_complete"] is True and result["document_count"] == 0
    member = result["object_protocol_observations"][-1]["members"]["Item"]
    assert member == {
        "declared_dispid": 104,
        "observed_dispid": 0,
        "matched": False,
        "used_for_read_protocol": False,
        "verified": False,
        "invoked": False,
    }
    assert result["authority_granted"] is False
    assert not any("Item" in call for call in graph.calls)


def test_unused_item_missing_name_is_recorded_not_used_for_count(tmp_path):
    graph = Graph(tmp_path, 0)
    graph.docs.typeinfo_count = 0
    original = graph.docs.get_ids

    def lookup(name):
        if name == "Item":
            raise FakeComError(-2147352570)  # DISP_E_UNKNOWNNAME
        return original(name)

    graph.docs._oleobj_.GetIDsOfNames = lookup
    result = graph.run()
    assert result["observation_complete"] is True and result["document_count"] == 0
    member = result["object_protocol_observations"][-1]["members"]["Item"]
    assert member["observed_dispid"] is None and member["matched"] is False
    assert member["lookup_hresult_hex"] == "0x80020006"
    assert member["verified"] is False and member["invoked"] is False


def test_unused_item_does_not_hide_an_unrelated_native_failure(tmp_path):
    graph = Graph(tmp_path, 0)
    graph.docs.typeinfo_count = 0
    original = graph.docs.get_ids

    def lookup(name):
        if name == "Item":
            raise FakeComError(-2147024891)  # E_ACCESSDENIED
        return original(name)

    graph.docs._oleobj_.GetIDsOfNames = lookup
    result = graph.run()
    assert result["observation_complete"] is False
    assert result["hresult_hex"] == "0x80070005" and result["document_count"] is None


def test_declared_interface_canonical_mismatch_cannot_fall_back_to_names(tmp_path):
    graph = Graph(tmp_path, 0)
    graph.docs.typeinfo_count = 0
    original = graph.docs.query

    def changed(iid, use_iid=None):
        if iid == "{" + G.DOCUMENTS_IID + "}":
            return SimpleNamespace(QueryInterface=lambda requested: object())
        return original(iid, use_iid)

    graph.docs._oleobj_.QueryInterface = changed
    result = graph.run()
    assert result["reason"] == "declared_return_interface_identity_mismatch"
    assert result["document_count"] is None


def test_no_typeinfo_application_raw_dispatch_is_existing_object_only(tmp_path):
    graph = Graph(tmp_path, 0)
    graph.docs.typeinfo_count = 0
    graph.app.unknown = graph.scene.app_unknown
    original = graph.docs.invoke_types

    def invoke(dispid, locale, flags, result_type, arguments):
        value = original(dispid, locale, flags, result_type, arguments)
        return RawDispatch(value) if result_type == (9, 0) else value

    graph.docs._oleobj_.InvokeTypes = invoke
    result = graph.run()
    assert result["observation_complete"] is True and len(graph.wrapped) == 1
