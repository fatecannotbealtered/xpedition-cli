"""Fixed writer lifetime and admission at fake COM boundaries; never CAD."""

import copy
import threading

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401
from test_designer_dual_invoke import NativeError, Raw
from test_designer_dual_invoke import scene as scene  # noqa: F401

from xpedition_cli import designer_dual_invoke as D
from xpedition_cli import designer_native_owner as O


@pytest.fixture
def writer(scene, monkeypatch):
    state = scene
    state.write_calls, state.declarations = [], []
    state.write_error = state.contract_error = False
    state.returned_doc = Raw(D.DOCUMENT_IID)
    original = state.app.InvokeTypes

    def getter(member, *args):
        return state.returned_doc if member == 208 else original(member, *args)

    state.app.InvokeTypes = getter
    state.app.GetIDsOfNames = lambda lcid, name: {"Documents": 1, "ActiveDocument": 208}[name]

    def declaration(self, library, operation):
        state.declarations.append(operation)
        if state.contract_error:
            raise D.DualInvokeError("fixed_fixture_contract_rejected")
        return object(), {"fixed": operation}

    def invoke(
        bridge,
        info,
        receiver,
        member,
        argument,
        returns_document,
        observation,
        on_dispatch=None,
        on_return=None,
    ):
        def native():
            if on_dispatch:
                on_dispatch()
            observation["invoke_attempted"] = True
            state.write_calls.append((member, argument, returns_document))
            if state.write_error:
                raise NativeError()
            observation["invoke_returned"] = True
            if on_return:
                on_return()

        bridge.probe.read("document.ITypeInfo_Invoke", native)
        if returns_document:
            bridge.refs.append(state.returned_doc)
            return state.returned_doc
        return None

    monkeypatch.setattr(D.FixedDocumentCall, "_document_declaration", declaration)
    monkeypatch.setattr(D._NativeBridge, "invoke_document", invoke, raising=False)

    def create(operation=None, argument=None):
        return D.FixedDocumentCall(
            state.app,
            state.probe.com,
            state.probe.budget,
            owner_thread=threading.current_thread(),
            owner_ident=threading.get_ident(),
            operation=operation,
            argument=argument,
        )

    state.create = create
    return state


def test_empty_application_preflight_needs_no_doc_and_invokes_no_member(writer):
    call = writer.create()
    facts = call.run()
    assert facts["status"] == "metadata_verified" and facts["invoke_attempted"] is False
    assert set(facts["contracts"]) == {"open_design", "save_all", "save_document"}
    assert writer.write_calls == []
    assert not any(row[0] == "getter" for row in writer.calls)
    assert call.resources_released is True


@pytest.mark.parametrize("operation,member", [("save_all", 10), ("save_document", 12)])
def test_void_return_is_call_evidence_and_not_persistence(writer, operation, member):
    call = writer.create(operation)
    events = []
    result = call.run(
        on_dispatch=lambda: events.append("attempt"), on_return=lambda: events.append("return")
    )
    assert writer.write_calls == [(member, None, False)]
    assert events == ["attempt", "return"]
    assert result["invoke_returned"] is True and result["persistence_verified"] is False
    assert result["return_vartype"] is None and call.resources_released is True


def test_successful_open_transfers_independent_return_and_cannot_replay(writer):
    call = writer.create("open_design", "ObservedDesign")
    result = call.run()
    assert writer.write_calls == [(7, "ObservedDesign", True)]
    assert call.bridge.refs == [] and call.returned_document is writer.returned_doc
    assert call.resources_released is True
    assert call.take_document() is writer.returned_doc
    assert call.result["return_reference_transferred"] is True
    with pytest.raises(D.DualInvokeError, match="already_attempted"):
        call.run()
    with pytest.raises(D.DualInvokeError, match="not_available"):
        call.take_document()
    assert result["persistence_verified"] is False


@pytest.mark.parametrize("failure", ["native", "postcheck", "cleanup"])
def test_dispatched_failure_keeps_parameters_output_and_owner_responsibility(writer, failure):
    call = writer.create("open_design", "ObservedDesign")
    writer.write_error = failure == "native"
    writer.cleanup_failure = failure == "cleanup"

    def postcheck():
        if failure == "postcheck":
            raise D.DualInvokeError("fixed_fixture_scope_drift")

    with pytest.raises(D.DualInvokeError) as error:
        call.run(postcheck=postcheck)
    assert error.value.dispatch_started is True
    assert call.result["status"] == "cleanup_incomplete"
    assert call.resources_released is False and call.bridge.refs and call.application is writer.app
    with pytest.raises(D.DualInvokeError, match="already_attempted"):
        call.run()
    assert len(writer.write_calls) == 1


def test_known_metadata_rejection_releases_without_dispatch(writer):
    writer.contract_error = True
    call = writer.create("save_all")
    with pytest.raises(D.DualInvokeError) as error:
        call.run()
    assert error.value.dispatch_started is True and call.resources_released is True
    assert call.result["invoke_attempted"] is False
    assert writer.write_calls == []


def test_preflight_rejection_is_known_to_precede_newproject(writer):
    writer.contract_error = True
    call = writer.create()
    with pytest.raises(D.DualInvokeError) as error:
        call.run()
    assert error.value.dispatch_started is False and call.result["invoke_attempted"] is False
    assert call.resources_released is True and writer.write_calls == []


def test_diagnostic_failure_preserves_return_and_cannot_become_nothing_sent(writer, monkeypatch):
    summary = {"type": "OpaqueNativeObject", "length": 12, "sha256": "a" * 64}

    def failed(self, library, operation):
        self.point = "git.create_comtypes_table"
        error = O.OwnerError("native_boundary_field_contract_invalid")
        error.native_call_returned = True
        error.native_result_summary = summary
        error.native_boundary_trace_incomplete = True
        raise error

    monkeypatch.setattr(D.FixedDocumentCall, "_document_declaration", failed)
    call = writer.create()
    with pytest.raises(D.DualInvokeError) as rejected:
        call.run()
    error = rejected.value
    assert error.dispatch_started is True and error.native_call_returned is True
    assert error.native_result_summary == summary and writer.write_calls == []
    packet = O._adapter_failure(error)
    assert packet["code"] == "E_OUTCOME_UNKNOWN"
    facts = packet["details"]
    assert facts["owner_command_received"] is True
    assert facts["native_metadata_call_entered"] is True
    assert facts["engineering_write_attempted"] is False
    assert facts["fixed_document_member_attempted"] is False
    assert facts["fixed_document_cleanup_observed"] is True
    assert facts["native_request_dispatched"] is True and facts["native_call_unknown"] is True
    assert facts["native_result_summary"] == summary


def test_last_scope_check_stops_member_before_invocation_without_reclassifying_macro(writer):
    call = writer.create("save_all")

    def changed_scope():
        raise D.DualInvokeError("fixed_fixture_last_scope_changed")

    with pytest.raises(D.DualInvokeError) as rejected:
        call.run(precheck=changed_scope)
    assert rejected.value.dispatch_started is True  # Same macro may have earlier writes.
    assert call.result["invoke_attempted"] is False
    assert writer.write_calls == [] and call.resources_released is True


@pytest.mark.parametrize("stage", ["before", "after"])
def test_closed_project_boolean_is_rejected_at_the_nearest_pre_and_post_check(writer, stage):
    call = writer.create("save_all")
    owner = type("ExactProjectObserver", (), {})()
    owner._owner = lambda: None
    owner.probe = lambda: {"native_project_open": False, "open_project_matches_scope": None}

    def check():
        return O.NativeApplication._require_exact_open_document_project(owner)

    with pytest.raises(D.DualInvokeError):
        call.run(
            precheck=check if stage == "before" else None,
            postcheck=check if stage == "after" else None,
        )
    assert call.result["invoke_attempted"] is (stage == "after")
    assert len(writer.write_calls) == (1 if stage == "after" else 0)
    assert call.resources_released is (stage == "before")


@pytest.mark.parametrize(
    "operation,argument",
    [
        ("arbitrary", None),
        ("open_design", True),
        ("open_design", ""),
        ("open_design", "x" * 4097),
        ("open_design", "x\x00y"),
        ("save_all", "extra"),
    ],
)
def test_no_generic_member_or_unbounded_argument_surface(writer, operation, argument):
    before = copy.deepcopy(writer.calls)
    with pytest.raises(D.DualInvokeError):
        writer.create(operation, argument)
    assert writer.calls == before and writer.write_calls == []
