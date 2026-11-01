"""Offline-testable observations for a bounded, single-thread COM owner.

This module never activates, locates, authenticates or releases COM objects and
never opens a document. The caller supplies its original activation reference,
its authenticated document and readers operating in that same COM context.
Document.Application is an observation reference, never a replacement owner.
IUnknown equality is optional injected diagnostics; no Python object identity,
pointer, repr or cross-process pointer comparison is used or recorded.

All tests of this module use synthetic adapters. Passing them proves the guard's
behaviour, not native CAD identity or a Load/UnLoad lifecycle.
"""

from __future__ import annotations

import json
import ntpath
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

Reader = Callable[[Any, str], Any]
TypeReader = Callable[[Any], Mapping[str, Any]]
ProcessReader = Callable[[int], Mapping[str, Any]]
UnknownComparer = Callable[[Any, Any], bool]

_TYPE_FIELDS = (
    "library_guid",
    "library_name",
    "library_major",
    "library_minor",
    "interface_guid",
    "interface_name",
)


def _path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value))


def _equivalent(field: str, left: Any, right: Any) -> bool:
    if field in {"document_path", "exe_path", "executable", "path"}:
        return isinstance(left, str) and isinstance(right, str) and _path(left) == _path(right)
    if field.endswith("guid"):
        return (
            isinstance(left, str)
            and isinstance(right, str)
            and left.strip("{}").upper() == right.strip("{}").upper()
        )
    return type(left) is type(right) and left == right


def _observed(call: Callable[[], Any], field: str) -> dict[str, Any]:
    try:
        value = call()
        # Reject opaque proxies instead of accidentally serializing repr/pointers.
        json.dumps(value, allow_nan=False)
        return {"ok": True, "value": value}
    except Exception as exc:
        return _failed(exc, field)


def _failed(exc: Exception, field: str) -> dict[str, Any]:
    failure = {"field": field, "error_type": type(exc).__name__, "message": str(exc)}
    if isinstance(getattr(exc, "hresult", None), int):
        failure["hresult"] = exc.hresult
    return {"ok": False, "failure": failure}


def _unavailable(field: str, reason: str) -> dict[str, Any]:
    return {"ok": False, "failure": {"field": field, "reason": reason}}


def _value(result: Mapping[str, Any]) -> Any:
    return result.get("value") if result.get("ok") else None


def capture_identity(
    application: Any,
    document: Any | None,
    *,
    phase: str,
    sample: str,
    read_member: Reader,
    describe_type: TypeReader,
    observe_process: ProcessReader,
    context_id: str,
    same_iunknown: UnknownComparer | None = None,
) -> dict[str, Any]:
    """Read each reference afresh; preserve every unavailable field separately.

    ``describe_type`` returns the six ``_TYPE_FIELDS`` keys. ``observe_process``
    returns ``hwnd``, ``pid``, ``birth_filetime_100ns``, ``executable`` and ``alive`` for the actual
    HWND owner, rather than the launcher process. ``read_member`` only needs
    formal members: InstanceGuid, Gui, HWND, ActiveDocument, FullName, Application.
    The supplied document must already have been authenticated by the caller.

    Only ``pre_open`` and ``post_close`` permit no document. Sample IDs
    distinguish two actual observations; the COM context ID and Python client
    thread must remain equal.
    """
    active_references: dict[str, Any] = {}

    def describe_document(ref: Any | None, role: str) -> dict[str, Any]:
        if ref is None:
            return {"readable": True, "present": False}
        return {
            "readable": True,
            "present": True,
            "type": _observed(lambda: dict(describe_type(ref)), f"{role}.type"),
            "path": _observed(lambda: read_member(ref, "FullName"), f"{role}.FullName"),
        }

    def describe_application(ref: Any, role: str) -> dict[str, Any]:
        result = {
            "type": _observed(lambda: dict(describe_type(ref)), f"{role}.type"),
            "instance_guid": _observed(
                lambda: read_member(ref, "InstanceGuid"), f"{role}.InstanceGuid"
            ),
        }
        try:
            gui = read_member(ref, "Gui")
            result["hwnd"] = _observed(lambda: read_member(gui, "HWND"), f"{role}.Gui.HWND")
        except Exception as exc:
            result["hwnd"] = _failed(exc, f"{role}.Gui")
        hwnd = _value(result["hwnd"])
        if type(hwnd) is int and hwnd > 0:
            result["process"] = _observed(lambda: dict(observe_process(hwnd)), f"{role}.process")
        else:
            result["process"] = _unavailable(f"{role}.process", "valid_hwnd_unavailable")
        try:
            active = read_member(ref, "ActiveDocument")
            active_references[role] = active
            result["active_document"] = describe_document(active, f"{role}.ActiveDocument")
        except Exception as exc:
            result["active_document"] = {
                "readable": False,
                "read_failure": _failed(exc, f"{role}.ActiveDocument"),
            }
        return result

    def compare_unknown(left: Any, right: Any, name: str) -> dict[str, Any]:
        if same_iunknown is None:
            return _unavailable(name, "IUnknown_comparer_not_supplied")

        def call() -> bool:
            value = same_iunknown(left, right)
            if type(value) is not bool:
                raise TypeError("IUnknown comparer must return an equality boolean")
            return value

        return _observed(call, name)

    result: dict[str, Any] = {
        "schema_version": "1",
        "phase": phase,
        "sample": sample,
        "context_id": context_id,
        "client_thread_id": threading.get_ident(),
        "observed_at": datetime.now(UTC).isoformat(),
        "activation_application": describe_application(application, "activation_application"),
        "authenticated_document": describe_document(document, "authenticated_document"),
        "iunknown": {},
    }
    if document is None:
        result["document_application"] = {
            "readable": False,
            "reason": "no_authenticated_document_in_this_phase",
        }
        return result
    owner_active = active_references.get("activation_application")
    if owner_active is not None:
        result["iunknown"]["activation_active_vs_authenticated"] = compare_unknown(
            owner_active, document, "activation_active_vs_authenticated"
        )
    try:
        observer = read_member(document, "Application")
        if observer is None:
            raise ValueError("Document.Application returned no reference")
        result["document_application"] = {
            "readable": True,
            **describe_application(observer, "document_application"),
        }
        result["iunknown"]["activation_vs_document_application"] = compare_unknown(
            application, observer, "activation_vs_document_application"
        )
        observer_active = active_references.get("document_application")
        if observer_active is not None:
            result["iunknown"]["observer_active_vs_authenticated"] = compare_unknown(
                observer_active, document, "observer_active_vs_authenticated"
            )
    except Exception as exc:
        result["document_application"] = {
            "readable": False,
            "read_failure": _failed(exc, "authenticated_document.Application"),
        }
    return result


def compare_identity(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide only whether observation of the original reference chain may continue.

    Required expectations are document_path, exe_path, application_type and
    document_type (the latter two pin at least library_guid/library_major and
    interface_guid). Optional activation pins pid/birth_filetime_100ns/hwnd/instance_guid;
    forbidden_processes pins old instances by pid. No discovery is performed.

    The observer can be incomplete without replacing or validating the owner.
    Every limitation is returned, and any positively observed process/document
    contradiction rejects. A different observer GUID or IUnknown app identity is
    preserved but does not on its own reject the limited owner chain. Different
    document IUnknown identities prove different COM objects, not different
    physical designs. Such differences or missing QI data require a fresh,
    independently authenticated document fingerprint before Load or Close. This
    guard never certifies those physical fields and never authorizes a write.
    """
    errors: list[dict[str, Any]] = []
    limitations: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    required_physical_checks: list[dict[str, Any]] = []

    def issue(target: list, field: str, reason: str, **details: Any) -> None:
        target.append({"field": field, "reason": reason, **details})

    def need_equal(field: str, observed: Any, wanted: Any, target: list = errors) -> None:
        if not _equivalent(field.rsplit(".", 1)[-1], observed, wanted):
            issue(target, field, "mismatch", observed=observed, expected=wanted)

    def field_value(record: Mapping[str, Any], field: str, role: str, target: list) -> Any:
        observed = record.get(field, {})
        if not observed.get("ok"):
            issue(target, f"{role}.{field}", "unreadable", observation=observed)
            return None
        return observed.get("value")

    phase = first.get("phase")
    if not isinstance(phase, str) or not phase:
        issue(errors, "phase", "missing_or_invalid")
    need_equal("phase", second.get("phase"), phase)
    for field in ("context_id", "client_thread_id"):
        if first.get(field) in (None, ""):
            issue(errors, field, "missing")
        need_equal(field, second.get(field), first.get(field))
    if not first.get("sample") or not second.get("sample"):
        issue(errors, "sample", "missing")
    elif first["sample"] == second["sample"]:
        issue(errors, "sample", "two_distinct_observations_required")
    for field in ("document_path", "exe_path", "application_type", "document_type"):
        if not expected.get(field):
            issue(errors, f"expected.{field}", "missing")
    document_expected = phase not in {"pre_open", "post_close"}

    def validate_type(record: Mapping, role: str, target: list, type_expected: Mapping) -> None:
        observed = field_value(record, "type", role, target)
        if not isinstance(observed, Mapping):
            return
        for key in _TYPE_FIELDS:
            value = observed.get(key)
            if value is None or value == "" or isinstance(value, bool):
                issue(target, f"{role}.type.{key}", "missing_or_invalid")
        for key in ("library_guid", "library_major", "interface_guid"):
            if key not in type_expected:
                issue(errors, f"expected.{role}.type.{key}", "missing")
        for key, wanted in type_expected.items():
            need_equal(f"{role}.type.{key}", observed.get(key), wanted, target)

    def document_info(record: Mapping, role: str, target: list) -> Any:
        if not record.get("readable"):
            issue(target, role, "unreadable", observation=record)
            return None
        if not document_expected:
            if record.get("present") is not False:
                issue(target, role, "empty_document_phase_must_be_absent", observation=record)
            return None
        if record.get("present") is not True:
            issue(target, role, "expected_document_absent", observation=record)
            return None
        validate_type(record, role, target, expected.get("document_type", {}))
        path = field_value(record, "path", role, target)
        if not isinstance(path, str) or not path:
            issue(target, f"{role}.path", "missing_or_invalid")
        else:
            # A known wrong path is a target contradiction even in the observer.
            need_equal(f"{role}.document_path", path, expected.get("document_path"))
        return path

    def application_info(record: Mapping, role: str, target: list) -> dict[str, Any]:
        validate_type(record, role, target, expected.get("application_type", {}))
        result = {}
        for field in ("instance_guid", "hwnd", "process"):
            result[field] = field_value(record, field, role, target)
        if not isinstance(result["instance_guid"], str) or not result["instance_guid"].strip():
            issue(target, f"{role}.instance_guid", "missing_or_invalid")
        if type(result["hwnd"]) is not int or result["hwnd"] <= 0:
            issue(target, f"{role}.hwnd", "missing_or_invalid")
        process = result["process"]
        if not isinstance(process, Mapping):
            issue(target, f"{role}.process", "missing_or_invalid")
            process = {}
        for field in ("hwnd", "pid", "birth_filetime_100ns", "executable", "alive"):
            if field not in process or process[field] in (None, ""):
                issue(target, f"{role}.process.{field}", "missing")
        if process.get("alive") is not True:
            issue(target, f"{role}.process.alive", "not_observed_alive")
        if type(process.get("pid")) is not int or process.get("pid", 0) <= 0:
            issue(target, f"{role}.process.pid", "missing_or_invalid")
        if process.get("hwnd") is not None:
            need_equal(f"{role}.hwnd", process["hwnd"], result["hwnd"])
        birth = process.get("birth_filetime_100ns")
        if not isinstance(birth, str) or not birth.isdecimal() or int(birth) <= 0:
            issue(target, f"{role}.process.birth_filetime_100ns", "missing_or_invalid")
        if process.get("executable"):
            need_equal(f"{role}.executable", process["executable"], expected.get("exe_path"))
        for forbidden in expected.get("forbidden_processes", []):
            if process.get("pid") == forbidden.get("pid"):
                issue(errors, f"{role}.process", "forbidden_existing_pid", observed=process)
        result["process"] = process
        result["document_path"] = document_info(
            record.get("active_document", {}), f"{role}.active_document", target
        )
        return result

    owner_observations = []
    observer_observations = []

    def document_comparison(index: int, snapshot: Mapping, key: str, role: str) -> None:
        qi = snapshot.get("iunknown", {}).get(key, {})
        if qi.get("ok") and qi.get("value") is True:
            return
        reason = (
            "different_COM_objects_do_not_decide_physical_document"
            if qi.get("ok") and qi.get("value") is False
            else "canonical_document_comparison_unavailable"
        )
        issue(limitations, f"sample{index}.{role}.IUnknown", reason, observation=qi)
        required_physical_checks.append(
            {
                "sample": snapshot.get("sample"),
                "comparison": key,
                "canonical_iunknown_equal": qi.get("value") if qi.get("ok") else None,
                "physical_document_equal": "not_established_by_COM_identity",
                "required_before_Load_or_Close": (
                    "fresh original Application.ActiveDocument independently authenticated; "
                    "same-stage widths/components/nets/geometry/planes/ties fingerprint "
                    "strictly compared with the retained authenticated Document"
                ),
                "observer_may_replace_original_or_be_used_for_write": False,
            }
        )

    for index, snapshot in enumerate((first, second)):
        owner = application_info(
            snapshot.get("activation_application", {}), f"sample{index}.activation", errors
        )
        owner_observations.append(owner)
        document_info(
            snapshot.get("authenticated_document", {}), f"sample{index}.authenticated", errors
        )
        for field, wanted in expected.get("activation", {}).items():
            observed = owner.get(field) if field in {"hwnd", "instance_guid"} else None
            if field in {"pid", "birth_filetime_100ns", "executable"}:
                observed = owner["process"].get(field)
            need_equal(f"sample{index}.activation.{field}", observed, wanted)
        if not document_expected:
            continue
        document_comparison(index, snapshot, "activation_active_vs_authenticated", "document")
        document_comparison(
            index, snapshot, "observer_active_vs_authenticated", "observer.document"
        )
        observer = snapshot.get("document_application", {})
        if not observer.get("readable"):
            issue(
                limitations,
                f"sample{index}.document_application",
                "unreadable",
                observation=observer,
            )
            continue
        observed = application_info(observer, f"sample{index}.observer", limitations)
        observer_observations.append(observed)
        for field in ("pid", "birth_filetime_100ns", "executable"):
            left, right = owner["process"].get(field), observed["process"].get(field)
            if left is not None and right is not None:
                need_equal(f"sample{index}.cross_reference.{field}", right, left)
        # A readable observer type contradiction is different from missing data.
        observed_type = _value(observer.get("type", {}))
        if isinstance(observed_type, Mapping):
            for field, wanted in expected.get("application_type", {}).items():
                if observed_type.get(field) is not None:
                    need_equal(f"sample{index}.observer.type.{field}", observed_type[field], wanted)
        if observer.get("active_document", {}).get("readable") and not observer[
            "active_document"
        ].get("present"):
            issue(errors, f"sample{index}.observer.active_document", "known_document_absent")

    def stable(left: Mapping, right: Mapping, role: str, target: list) -> None:
        for field in ("instance_guid", "hwnd", "document_path"):
            if left.get(field) is not None and right.get(field) is not None:
                need_equal(f"{role}.{field}", right[field], left[field], target)
        for field in ("pid", "birth_filetime_100ns", "executable"):
            left_value, right_value = left["process"].get(field), right["process"].get(field)
            if left_value is not None and right_value is not None:
                need_equal(f"{role}.process.{field}", right_value, left_value, target)

    stable(*owner_observations, "activation.stability", errors)
    if len(observer_observations) == 2:
        stable(*observer_observations, "observer.stability", errors)
    if previous is not None:
        for field in ("context_id", "client_thread_id"):
            need_equal(f"previous.{field}", first.get(field), previous.get(field))
        old = previous.get("activation_application", {})
        old_process = _value(old.get("process", {})) or {}
        for field in ("pid", "birth_filetime_100ns", "executable"):
            need_equal(
                f"phase_continuity.{field}",
                owner_observations[0]["process"].get(field),
                old_process.get(field),
            )
        for field in ("instance_guid", "hwnd"):
            before, after = _value(old.get(field, {})), owner_observations[0].get(field)
            if not _equivalent(field, before, after):
                issue(changes, field, "changed_across_phases", before=before, after=after)
                if field not in expected.get("allowed_phase_changes", []):
                    issue(errors, f"phase_continuity.{field}", "undeclared_phase_change")
        # pre_open None is expected to become a document: never compare it as a
        # stale same-time snapshot or call the observer's GUID the owner's GUID.
        if previous.get("phase") != phase:
            issue(changes, "phase", "transition", before=previous.get("phase"), after=phase)

    # Completeness of reading an observation reference is distinct from the
    # physical document question raised by a different/unavailable IUnknown.
    observer_read_limitations = [
        item for item in limitations if not item["field"].endswith(".IUnknown")
    ]
    if expected.get("require_observer_complete") and observer_read_limitations:
        issue(errors, "observer", "complete_observer_required")

    def dimension(field: str) -> str:
        if "IUnknown" in field:
            return "com_object_identity"
        if any(part in field for part in ("document", "authenticated")):
            return "document_identity"
        if any(part in field for part in ("process", "hwnd", "pid", "birth", "executable")):
            return "process_identity"
        return "application_reference"

    dimensions = {}
    for name in (
        "application_reference",
        "process_identity",
        "document_identity",
        "com_object_identity",
    ):
        failures = [error for error in errors if dimension(error["field"]) == name]
        dimensions[name] = {"satisfied": not failures, "errors": failures}
    dimensions["com_object_identity"]["scope"] = (
        "activation_ActiveDocument_vs_authenticated_Document_in_same_context"
        if document_expected
        else "not_required_in_empty_document_phase"
    )
    dimensions["com_object_identity"]["satisfied"] = (
        dimensions["com_object_identity"]["satisfied"] and not required_physical_checks
    )
    return {
        "schema_version": "1",
        "can_continue_original_chain": not errors,
        "phase": phase,
        "identity_scope": "original_activation_and_authenticated_document_in_same_context",
        "continuation_scope": (
            "original_reference_observation_only_not_Load_Close_or_write_approval"
        ),
        "requires_fresh_document_fingerprint": bool(required_physical_checks),
        "required_physical_document_checks": required_physical_checks,
        "physical_document_fingerprint_verified": False,
        "observer_complete": document_expected and not observer_read_limitations,
        "application_references_interchangeable": "not_established_by_this_guard",
        "errors": errors,
        "limitations": limitations,
        "phase_changes": changes,
        "dimensions": dimensions,
        "iunknown_observations": [first.get("iunknown", {}), second.get("iunknown", {})],
        "native_execution_proved_by_offline_tests": False,
    }
