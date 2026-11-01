"""Bounded, non-authorizing observation of existing Designer documents/views.

No document/view is opened, activated or created. Direct IEnumVARIANT is probed
explicitly; its absence does not imply that a separate _NewEnum is unavailable.
This diagnostic never supplies pid_source or relaxes release/close admission.
"""

from __future__ import annotations

import hashlib
import time

from . import designer_window_identity as W

DOCUMENTS_IID = "608ca7f4-a52a-4206-9580-3beb17ce0073"
DOCUMENT_IID = "d8b95af2-d943-4295-8d86-71b4f904c3aa"
VIEWS_IID = "dc3a8af0-91b4-11ce-8243-00001b4d36b5"
VIEW_IID = "0892a013-86bc-11ce-8238-00001b4d36b5"
APP_IIDS = {"ea4abd71-84b0-11ce-8237-00001b4d36b5", "8e0609dd-208c-42b6-a1b0-00daa3e45423"}
MAX_DOCUMENTS = 64
MAX_VIEWS_PER_DOCUMENT = 16
MAX_TOTAL_VIEWS = 128
MAX_TYPE_MEMBERS = 4096
MAX_READ_OPERATIONS = 512
MAX_WALL_SECONDS = 30.0
COLLECTION_MEMBERS = {
    DOCUMENTS_IID: {"Count": 101, "Application": 102, "Item": 104},
    VIEWS_IID: {"Count": 1, "Application": 2, "Item": 4},
}


class ObservationGap(ValueError):
    """Fixed safe reason, not native exception text."""


def eligible(binding):
    """Only the actually observed C11 missing-provider prerequisite may probe."""
    if (
        not isinstance(binding, dict)
        or binding.get("status") != "incomplete"
        or binding.get("stage") != "formal_candidates"
        or binding.get("reason") != "no_complete_formal_window_binding"
    ):
        return False
    attempts = binding.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 2:
        return False
    first, second = attempts
    return (
        first.get("source") == W.SOURCE_APP
        and first.get("stage") == "query_interface"
        and first.get("status") == "unsupported"
        and first.get("reason") == "iolewindow_not_supported"
        and first.get("hresult_hex") == "0x80004002"
        and second.get("source") == W.SOURCE_FRAMEWORK
        and second.get("stage") == "formal_provider"
        and second.get("call_point") == "framework.return_dispatch"
        and second.get("reason") == "framework_dispatch_wrapper_missing"
        and second.get("framework_observation")
        == {
            "getter_callable": True,
            "framework_return_present": False,
            "dispatch_wrapper_present": False,
        }
    )


class Budget:
    def __init__(self, clock, wrap_dispatch):
        self.clock, self.started, self.calls = clock, clock(), 0
        self.wrap_dispatch = wrap_dispatch
        self.protocols = []
        self.point = "existing_views.prerequisite"

    def check(self):
        if self.clock() - self.started > MAX_WALL_SECONDS:
            raise ObservationGap("existing_view_observer_wall_limit")

    def read(self, point, operation):
        self.point = point
        self.check()
        if self.calls >= MAX_READ_OPERATIONS:
            raise ObservationGap("existing_view_observer_read_limit")
        self.calls += 1
        value = operation()
        self.check()
        return value


def _same(left, right, reason):
    value = left == right
    if type(value) is not bool or value is not True:
        raise ObservationGap(reason)


def _unknown(obj, com, budget, point):
    return budget.read(point, lambda: obj._oleobj_.QueryInterface(com.IID_IUnknown))


def _wrap_dispatch(value):
    from win32com.client import Dispatch

    return Dispatch(value)


def _bridge_existing_dispatch(value, com, budget, point):
    """Wrap only a real already-returned PyIDispatch, never a string or CLSID."""
    raw_type = getattr(com, "TypeIIDs", {}).get(getattr(com, "IID_IDispatch", None))
    if raw_type is not None and type(value) is raw_type:
        raw = budget.read(point + ".raw.IUnknown", lambda: value.QueryInterface(com.IID_IUnknown))
        wrapped = budget.read(
            point + ".wrap_existing_IDispatch", lambda: budget.wrap_dispatch(value)
        )
        _same(
            _unknown(wrapped, com, budget, point + ".wrapped.IUnknown"),
            raw,
            "native_dispatch_bridge_identity_mismatch",
        )
        return wrapped
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        raise ObservationGap("native_enum_member_not_existing_dispatch")
    if getattr(value, "_oleobj_", None) is None:
        raise ObservationGap("native_enum_member_dispatch_wrapper_missing")
    return value


def _typed(obj, expected_iids, budget, point, com):
    if obj is None:
        raise ObservationGap("existing_native_object_null")
    raw = obj._oleobj_
    facts = {
        "object_role": point,
        "typeinfo_count": None,
        "basis": None,
        "declared_interface_iids": sorted(expected_iids),
    }
    budget.protocols.append(facts)
    count = budget.read(point + ".GetTypeInfoCount", raw.GetTypeInfoCount)
    facts["typeinfo_count"] = count if type(count) is int else None
    if type(count) is not int or count not in (0, 1):
        raise ObservationGap("native_typeinfo_count_invalid")
    if count == 0:
        if len(expected_iids) != 1:
            raise ObservationGap("native_typeinfo_missing_no_single_declared_interface")
        expected = next(iter(expected_iids))
        owner = _unknown(obj, com, budget, point + ".IUnknown")
        try:
            interface = budget.read(
                point + ".QueryInterface.declared_return",
                lambda: raw.QueryInterface("{" + expected + "}", com.IID_IUnknown),
            )
            returned = budget.read(
                point + ".declared_return.IUnknown",
                lambda: interface.QueryInterface(com.IID_IUnknown),
            )
            equal = returned == owner
            facts["declared_return_canonical_equal"] = equal if type(equal) is bool else None
            _same(returned, owner, "declared_return_interface_identity_mismatch")
            facts["declared_return_interface_qi"] = "observed"
            facts["basis"] = "declared_return_interface_qi"
        except Exception as exc:
            if getattr(exc, "hresult", None) != -2147467262:
                raise
            facts["declared_return_interface_qi"] = "unsupported"
            facts["declared_return_interface_hresult_hex"] = "0x80004002"
            if expected not in COLLECTION_MEMBERS:
                raise ObservationGap(
                    "declared_return_interface_unsupported_no_collection_protocol"
                ) from exc
            facts["basis"] = "declared_IDispatch_collection_protocol"
        if expected in COLLECTION_MEMBERS:
            facts["members"] = {}
            for name, dispid in COLLECTION_MEMBERS[expected].items():
                try:
                    actual = budget.read(
                        point + ".GetIDsOfNames." + name, lambda name=name: raw.GetIDsOfNames(name)
                    )
                    missing_error = None
                except Exception as exc:
                    hr = getattr(exc, "hresult", None)
                    if (
                        name != "Item"
                        or type(hr) is not int
                        or hr & 0xFFFFFFFF
                        not in (
                            0x80020003,
                            0x80020006,
                        )
                    ):
                        raise
                    actual, missing_error = None, f"0x{hr & 0xFFFFFFFF:08x}"
                used = name in {"Count", "Application"}
                matched = type(actual) is int and actual == dispid
                member = {
                    "declared_dispid": dispid,
                    "observed_dispid": actual if type(actual) is int else None,
                    "matched": matched,
                    "used_for_read_protocol": used,
                }
                if not used:
                    member.update(verified=False, invoked=False)
                    if missing_error is not None:
                        member["lookup_hresult_hex"] = missing_error
                facts["members"][name] = member
                if used and not matched:
                    raise ObservationGap("declared_collection_member_dispid_mismatch")
            facts["count_dispid"] = COLLECTION_MEMBERS[expected]["Count"]
            facts["application_dispid"] = COLLECTION_MEMBERS[expected]["Application"]
            facts["Count_read_signature"] = "PROPERTYGET/VT_I4/no_arguments"
            facts["Item_argument_invocation"] = "not_probed"
        return facts
    # Fixed pywin32's order is (locale, index), unlike Win32's (index, locale).
    # Both the installed 151 wrapper and the original live app libattr declare LCID0.
    facts["requested_lcid"], facts["requested_typeinfo_index"] = 0, 0
    info = budget.read(point + ".GetTypeInfo.locale0.index0", lambda: raw.GetTypeInfo(0, 0))
    attr = budget.read(point + ".GetTypeAttr", info.GetTypeAttr)
    iid = getattr(attr, "iid", None)
    if iid is None:
        raise ObservationGap("existing_native_typeattr_iid_missing")
    actual_iid = W._guid(iid)
    facts["observed_interface_iid"] = actual_iid
    if actual_iid not in expected_iids:
        raise ObservationGap("existing_native_interface_unqualified")
    if type(attr.cFuncs) is not int or not 0 <= attr.cFuncs <= MAX_TYPE_MEMBERS:
        raise ObservationGap("existing_native_type_member_limit")
    library, _ = budget.read(point + ".GetContainingTypeLib", info.GetContainingTypeLib)
    lib = budget.read(point + ".GetLibAttr", library.GetLibAttr)
    facts["observed_library_lcid"] = lib[1] if type(lib[1]) is int else None
    if W._guid(lib[0]) != W.VIEWDRAW_GUID or lib[1] != 0 or lib[3:5] != (151, 0):
        raise ObservationGap("existing_native_library_unqualified")
    facts["basis"] = "runtime_typeinfo_ViewDraw151_lcid0"
    return facts


def _project(app, budget, point):
    opened = budget.read(point + ".IsProjectOpened", app.IsProjectOpened)
    if type(opened) is not bool:
        raise ObservationGap("native_project_open_not_boolean")
    path = None
    if opened:
        data = budget.read(point + ".GetProjectData", app.GetProjectData)
        path = budget.read(point + ".GetProjectFilePath", data.GetProjectFilePath)
    return {"status": "observed", "open": opened, "path": path}


def _count(collection, maximum, budget, point, protocol):
    if protocol["typeinfo_count"] == 0:
        value = budget.read(
            point + ".InvokeTypes",
            lambda: collection._oleobj_.InvokeTypes(protocol["count_dispid"], 0, 2, (3, 0), ()),
        )
    else:
        value = budget.read(point, lambda: collection.Count)
    if type(value) is not int or not 0 <= value <= maximum:
        raise ObservationGap("existing_native_collection_count_invalid_or_over_limit")
    return value


def _collection_application(collection, protocol, com, budget):
    if protocol["typeinfo_count"] == 0:
        raw = budget.read(
            "documents.Application.InvokeTypes",
            lambda: collection._oleobj_.InvokeTypes(
                protocol["application_dispid"], 0, 2, (9, 0), ()
            ),
        )
        return _bridge_existing_dispatch(raw, com, budget, "documents.Application")
    return budget.read("documents.Application", lambda: collection.Application)


def _members(collection, count, com, budget, point):
    """No index-base guessing, Reset or generic __iter__ fallback."""
    if count == 0:
        return []
    owner = _unknown(collection, com, budget, point + ".IUnknown")
    try:
        enum = budget.read(
            point + ".QueryInterface.IEnumVARIANT",
            lambda: collection._oleobj_.QueryInterface(com.IID_IEnumVARIANT),
        )
    except Exception as exc:
        if getattr(exc, "hresult", None) == -2147467262:
            raise ObservationGap("direct_enum_unsupported_standard_new_enum_unprobed") from exc
        raise
    _same(
        budget.read(point + ".enum.IUnknown", lambda: enum.QueryInterface(com.IID_IUnknown)),
        owner,
        "direct_enum_canonical_identity_mismatch",
    )
    result, identities = [], []
    for ordinal in range(count + 1):
        values = budget.read(point + ".Next", lambda: enum.Next(1))
        if type(values) is not tuple or len(values) > 1:
            raise ObservationGap("native_enum_result_shape_unobserved")
        if ordinal == count:
            if values:
                raise ObservationGap("native_enum_exceeds_reported_count")
            break
        if not values:
            raise ObservationGap("native_enum_ended_before_reported_count")
        item = _bridge_existing_dispatch(values[0], com, budget, point + ".member")
        identity = _unknown(item, com, budget, point + ".member.IUnknown")
        for previous in identities:
            equal = identity == previous
            if type(equal) is not bool or equal:
                raise ObservationGap("native_enum_duplicate_or_unobserved_identity")
        identities.append(identity)
        result.append(item)
    return result


def _name_digest(document, budget, point):
    value = budget.read(point, lambda: document.FullName)
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ObservationGap("native_document_name_missing_or_over_limit")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _window(
    view,
    view_unknown,
    com,
    expected_pid,
    executable,
    generation,
    budget,
    window_reader,
    generation_reader,
    observation,
):
    point = "existing_view.QueryInterface.IOleWindow"
    try:
        native = budget.read(point, lambda: view._oleobj_.QueryInterface(com.IID_IOleWindow))
    except Exception as exc:
        if getattr(exc, "hresult", None) == -2147467262:
            observation.update(
                {
                    "status": "unsupported",
                    "reason": "existing_view_iolewindow_unsupported",
                    "call_point": point,
                    "hresult_hex": "0x80004002",
                }
            )
            return observation
        raise
    _same(
        budget.read(
            "existing_view.IOleWindow.IUnknown", lambda: native.QueryInterface(com.IID_IUnknown)
        ),
        view_unknown,
        "existing_view_iolewindow_identity_mismatch",
    )
    hwnd = budget.read("existing_view.GetWindow", native.GetWindow)
    observation["observed_hwnd"] = hwnd if type(hwnd) is int else None
    if type(hwnd) is not int or hwnd <= 0:
        raise ObservationGap("existing_view_window_handle_missing")
    first = budget.read("existing_view.WindowToProcess", lambda: window_reader(hwnd))
    observation["observed_window"] = W._window_facts(first)
    observation["expected_application_pid"] = expected_pid
    if (
        W._window_facts(first) != first
        or first.get("is_window") is not True
        or first.get("hwnd") != hwnd
        or first.get("thread_id", 0) <= 0
    ):
        raise ObservationGap("existing_view_window_unobserved")
    if first.get("pid") != expected_pid:
        raise ObservationGap("existing_view_window_pid_differs_from_native_application")
    actual = budget.read("existing_view.ProcessGeneration", lambda: generation_reader(expected_pid))
    facts, reason = W._generation_observation(actual, expected_pid, executable)
    observation["observed_generation"] = facts
    if reason:
        raise ObservationGap(reason)
    if actual != generation:
        raise ObservationGap("existing_view_process_generation_changed")
    if budget.read("existing_view.GetWindow.recheck", native.GetWindow) != hwnd:
        raise ObservationGap("existing_view_formal_window_changed")
    if budget.read("existing_view.WindowToProcess.recheck", lambda: window_reader(hwnd)) != first:
        raise ObservationGap("existing_view_window_mapping_changed")
    if (
        budget.read(
            "existing_view.ProcessGeneration.recheck", lambda: generation_reader(expected_pid)
        )
        != generation
    ):
        raise ObservationGap("existing_view_process_generation_changed")
    observation.update(
        {
            "status": "observed",
            "hwnd": hwnd,
            "window": first,
            "process_generation": actual,
            "view_iolewindow_canonical_equal": True,
        }
    )
    return observation


def observe(
    app,
    document,
    executable,
    expected_pid,
    prior_binding,
    *,
    com_module=None,
    window_reader=None,
    generation_reader=None,
    wrap_dispatch=None,
    clock=time.monotonic,
):
    result = {
        "kind": "designer_existing_open_document_views_v1",
        "read_only": True,
        "authority_granted": False,
        "status": "incomplete",
        "observation_complete": False,
        "document_count": None,
        "total_view_count": None,
        "documents": [],
        "complete_window_relation_count": 0,
        "limits": {
            "documents": MAX_DOCUMENTS,
            "views_per_document": MAX_VIEWS_PER_DOCUMENT,
            "total_views": MAX_TOTAL_VIEWS,
            "read_operations": MAX_READ_OPERATIONS,
            "wall_seconds_checked_between_calls": MAX_WALL_SECONDS,
            "individual_call_hard_timeout": "existing_native_adapter_request",
        },
    }
    budget = Budget(clock, wrap_dispatch or _wrap_dispatch)
    try:
        if not eligible(prior_binding):
            raise ObservationGap("existing_view_prerequisite_not_observed")
        if type(expected_pid) is not int or expected_pid <= 0:
            raise ObservationGap("native_application_pid_unobserved")
        com = com_module if com_module is not None else W._load_com()
        window_reader, generation_reader = (
            window_reader or W._window,
            generation_reader or W._generation,
        )
        expected = W.document_binding(document)
        if W.document_binding(_project(app, budget, "project.before")) != expected:
            raise ObservationGap("native_project_changed")
        generation = budget.read(
            "application.ProcessGeneration.before", lambda: generation_reader(expected_pid)
        )
        facts, reason = W._generation_observation(generation, expected_pid, executable)
        result["application_process_observation"] = facts
        if reason:
            raise ObservationGap(reason)
        _typed(app, APP_IIDS, budget, "application", com)
        owner = _unknown(app, com, budget, "application.IUnknown.before")
        docs = budget.read("application.SchematicSheetDocuments", app.SchematicSheetDocuments)
        doc_protocol = _typed(docs, {DOCUMENTS_IID}, budget, "documents", com)
        docs_app = _collection_application(docs, doc_protocol, com, budget)
        _same(
            _unknown(docs_app, com, budget, "documents.Application.IUnknown"),
            owner,
            "native_documents_application_identity_mismatch",
        )
        count = _count(docs, MAX_DOCUMENTS, budget, "documents.Count", doc_protocol)
        result["document_count"] = count
        documents = _members(docs, count, com, budget, "documents")
        total = 0
        for ordinal, doc in enumerate(documents):
            row = {"ordinal": ordinal, "view_count": None, "views": []}
            result["documents"].append(row)
            _typed(doc, {DOCUMENT_IID}, budget, "document", com)
            doc_unknown = _unknown(doc, com, budget, "document.IUnknown")
            doc_app = budget.read("document.Application", lambda doc=doc: doc.Application)
            _same(
                _unknown(doc_app, com, budget, "document.Application.IUnknown"),
                owner,
                "native_document_application_identity_mismatch",
            )
            row["full_name_sha256"] = _name_digest(doc, budget, "document.FullName")
            views = budget.read("document.GetViews", doc.GetViews)
            view_protocol = _typed(views, {VIEWS_IID}, budget, "views", com)
            view_count = _count(views, MAX_VIEWS_PER_DOCUMENT, budget, "views.Count", view_protocol)
            row["view_count"] = view_count
            total += view_count
            if total > MAX_TOTAL_VIEWS:
                raise ObservationGap("existing_native_total_view_limit")
            for view_ordinal, view in enumerate(_members(views, view_count, com, budget, "views")):
                _typed(view, {VIEW_IID}, budget, "view", com)
                view_unknown = _unknown(view, com, budget, "view.IUnknown")
                view_app = budget.read("view.Application", view.Application)
                _same(
                    _unknown(view_app, com, budget, "view.Application.IUnknown"),
                    owner,
                    "native_view_application_identity_mismatch",
                )
                view_doc = budget.read("view.Document", view.Document)
                _same(
                    _unknown(view_doc, com, budget, "view.Document.IUnknown"),
                    doc_unknown,
                    "native_view_document_identity_mismatch",
                )
                window_observation = {"status": "incomplete"}
                row["views"].append(
                    {
                        "ordinal": view_ordinal,
                        "native_application_equal": True,
                        "independent_document_equal": True,
                        "window": window_observation,
                    }
                )
                window = _window(
                    view,
                    view_unknown,
                    com,
                    expected_pid,
                    executable,
                    generation,
                    budget,
                    window_reader,
                    generation_reader,
                    window_observation,
                )
                if window["status"] == "observed":
                    result["complete_window_relation_count"] += 1
            if (
                _count(views, MAX_VIEWS_PER_DOCUMENT, budget, "views.Count.recheck", view_protocol)
                != view_count
            ):
                raise ObservationGap("native_view_count_changed")
            if _name_digest(doc, budget, "document.FullName.recheck") != row["full_name_sha256"]:
                raise ObservationGap("native_document_name_changed")
        if _count(docs, MAX_DOCUMENTS, budget, "documents.Count.recheck", doc_protocol) != count:
            raise ObservationGap("native_document_count_changed")
        _same(
            _unknown(app, com, budget, "application.IUnknown.recheck"),
            owner,
            "held_application_identity_changed",
        )
        if W.document_binding(_project(app, budget, "project.recheck")) != expected:
            raise ObservationGap("native_project_changed")
        if (
            budget.read(
                "application.ProcessGeneration.recheck", lambda: generation_reader(expected_pid)
            )
            != generation
        ):
            raise ObservationGap("native_application_process_generation_changed")
        result.update(
            status="observed",
            observation_complete=True,
            total_view_count=total,
            reason="no_existing_native_documents"
            if count == 0
            else "no_existing_native_views"
            if total == 0
            else "existing_native_graph_observed",
        )
    except Exception as exc:
        result.update(
            reason=str(exc)
            if isinstance(exc, (ObservationGap, W.IdentityGap))
            else "existing_native_graph_read_failed",
            error_type=type(exc).__name__,
        )
        hr = getattr(exc, "hresult", None)
        if type(hr) is int:
            result["hresult_hex"] = f"0x{hr & 0xFFFFFFFF:08x}"
    result["call_point"], result["read_operation_count"] = budget.point, budget.calls
    result["object_protocol_observations"] = budget.protocols
    return result
