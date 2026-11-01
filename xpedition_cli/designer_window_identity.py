"""Formal Designer object-to-window identity; no activation or guessed window.

Only two declared COM relationships are observed: the held application itself,
or its formal ViewDraw 151.0 GetFramework(242) result. An unsupported interface,
zero window, incomplete generation or contradictory document never authorizes a
writer. No GUI message, title match, RunTcl or new application is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

SOURCE_APP = "Application.IOleWindow.GetWindow"
SOURCE_FRAMEWORK = "Application.GetFramework.IOleWindow.GetWindow"
SOURCES = (SOURCE_APP, SOURCE_FRAMEWORK)
VIEWDRAW_GUID = "4adef4e1-690a-11ce-9261-0020c5e26659"
FRAMEWORK_IID = "77cd6602-703b-4afa-a4c1-b1652a257f15"
FRAMEWORK_DECLARATION = {
    "member": "GetFramework",
    "dispid": 242,
    "invkind": 1,
    "argument_count": 0,
    "framework_iid": FRAMEWORK_IID,
}


class IdentityGap(ValueError):
    """An internal fixed reason, never arbitrary COM exception text."""


def _basename(value):
    name = Path(value).name if isinstance(value, str) else None
    return name if name and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) else None


def _path(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise IdentityGap("absolute_path_required")
    return os.path.normcase(str(Path(value).resolve()))


def document_binding(document):
    """Supplemental open/path binding; the original full document is retained."""
    if (
        not isinstance(document, dict)
        or document.get("status") != "observed"
        or any("error" in str(key).lower() for key in document)
    ):
        raise IdentityGap("document_not_observed")
    opened = document.get("open")
    if type(opened) is not bool:
        raise IdentityGap("document_open_not_boolean")
    path = _path(document.get("path")) if opened else None
    if not opened and document.get("path") is not None:
        raise IdentityGap("closed_document_path_not_empty")
    value = {"open": opened, "path": path}
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read_document(app):
    opened = app.IsProjectOpened()
    if type(opened) is not bool:
        raise IdentityGap("document_open_not_boolean")
    path = app.GetProjectData().GetProjectFilePath() if opened else None
    return {"status": "observed", "open": opened, "path": path}


def _load_com():
    import pythoncom

    return pythoncom


def _window(hwnd):
    import ctypes
    from ctypes import wintypes as w

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindow.argtypes, user.IsWindow.restype = (w.HWND,), w.BOOL
    user.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
    user.GetWindowThreadProcessId.restype = w.DWORD
    pid = w.DWORD()
    exists = bool(user.IsWindow(hwnd))
    thread = user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) if exists else 0
    return {
        "hwnd": hwnd,
        "is_window": exists,
        "pid": int(pid.value),
        "thread_id": int(thread),
    }


def _generation(pid):
    from .native_release_preflight import process_generation

    return process_generation(pid)


def _guid(value):
    return str(uuid.UUID(str(value)))


def _framework(app, observation):
    """Read only the officially declared zero-argument ViewDraw 151 getter."""
    observation["call_point"] = "framework.application_typeinfo"
    info = app._oleobj_.GetTypeInfo()
    observation["call_point"] = "framework.application_typelib"
    library, _ = info.GetContainingTypeLib()
    attrs = library.GetLibAttr()
    if (
        _guid(attrs[0]) != VIEWDRAW_GUID
        or attrs[3:5] != (151, 0)
        or library.GetDocumentation(-1)[0] != "ViewDraw"
    ):
        raise IdentityGap("framework_application_type_unqualified")
    observation["call_point"] = "framework.declared_getter"
    count = info.GetTypeAttr().cFuncs
    if type(count) is not int or not 0 <= count <= 4096:
        raise IdentityGap("framework_type_member_limit_or_missing")
    declarations = []
    for index in range(count):
        desc = info.GetFuncDesc(index)
        if info.GetNames(desc[0])[0] == "GetFramework":
            declarations.append(desc)
    if (
        len(declarations) != 1
        or declarations[0][0] != 242
        or declarations[0][4] != 1
        or len(declarations[0][2]) != 0
    ):
        raise IdentityGap("framework_getter_not_exact_declared_method")
    facts = {}
    observation["framework_observation"] = facts
    observation["call_point"] = "framework.getter_call"
    getter = getattr(app, "GetFramework", None)
    facts["getter_callable"] = callable(getter)
    if not callable(getter):
        raise IdentityGap("framework_declared_getter_unavailable")
    framework = getter()
    facts["framework_return_present"] = framework is not None
    observation["call_point"] = "framework.return_dispatch"
    dispatch = getattr(framework, "_oleobj_", None)
    facts["dispatch_wrapper_present"] = dispatch is not None
    if dispatch is None:
        raise IdentityGap("framework_dispatch_wrapper_missing")
    observation["call_point"] = "framework.return_typeinfo"
    type_info = dispatch.GetTypeInfo()
    facts["type_info_present"] = type_info is not None
    observation["call_point"] = "framework.return_typeattr"
    type_attr = type_info.GetTypeAttr()
    observation["call_point"] = "framework.typeattr_iid"
    actual = getattr(type_attr, "iid", None)
    facts["typeattr_iid_present"] = actual is not None
    if actual is None:
        raise IdentityGap("framework_typeattr_iid_missing")
    try:
        actual = _guid(actual)
    except (TypeError, ValueError) as exc:
        raise IdentityGap("framework_typeattr_iid_invalid") from exc
    facts["observed_interface_iid"] = actual
    if _guid(actual) != FRAMEWORK_IID:
        raise IdentityGap("framework_return_interface_unqualified")
    return framework


def _generation_observation(value, pid, executable):
    value = value if isinstance(value, dict) else {}
    birth = value.get("birth_filetime_100ns")
    valid_birth = (
        isinstance(birth, str) and birth.isascii() and birth.isdecimal() and int(birth) > 0
    )
    try:
        image_matches = _path(value.get("executable")) == _path(executable)
        image_absolute = True
    except (TypeError, ValueError):
        image_matches, image_absolute = False, False
    facts = {
        "window_pid": pid,
        "observed_process_pid": value.get("pid") if type(value.get("pid")) is int else None,
        "alive": value.get("alive") if type(value.get("alive")) is bool else None,
        "birth_present": birth is not None,
        "birth_valid": valid_birth,
        "image_absolute": image_absolute,
        "image_matches_native_executable": image_matches,
        "expected_executable_basename": _basename(executable),
        "observed_executable_basename": _basename(value.get("executable")),
    }
    if set(value) != {"pid", "alive", "birth_filetime_100ns", "executable"}:
        reason = "process_generation_fields_incomplete_or_unexpected"
    elif type(value.get("pid")) is not int or value["pid"] <= 0:
        reason = "process_pid_missing_or_invalid"
    elif value["pid"] != pid:
        reason = "process_pid_differs_from_window"
    elif value.get("alive") is not True:
        reason = "process_not_alive"
    elif not valid_birth:
        reason = "process_birth_missing_or_invalid"
    elif not image_absolute:
        reason = "process_image_missing_or_not_absolute"
    elif not image_matches:
        reason = "process_image_differs_from_native_executable"
    else:
        reason = None
    return facts, reason


def _generation_valid(value, pid, executable):
    return _generation_observation(value, pid, executable)[1] is None


def _window_facts(value):
    value = value if isinstance(value, dict) else {}
    return {
        "hwnd": value.get("hwnd") if type(value.get("hwnd")) is int else None,
        "is_window": value.get("is_window") if type(value.get("is_window")) is bool else None,
        "pid": value.get("pid") if type(value.get("pid")) is int else None,
        "thread_id": value.get("thread_id") if type(value.get("thread_id")) is int else None,
    }


def _same(left, right, reason):
    same = left == right
    if type(same) is not bool or same is not True:
        raise IdentityGap(reason)


def _observe_one(app, document, executable, source, com, window_reader, generation_reader):
    result = {
        "attempted": True,
        "source": source,
        "status": "incomplete",
        "stage": "document",
        "call_point": "application.native_project.before",
    }
    try:
        expected = document_binding(document)
        if document_binding(_read_document(app)) != expected:
            raise IdentityGap("native_document_changed")
        result["call_point"] = "application.FullName.before"
        if _path(app.FullName) != _path(executable):
            raise IdentityGap("native_executable_changed")
        result["stage"] = "application_iunknown"
        result["call_point"] = "application.QueryInterface.IUnknown.before"
        app_unknown = app._oleobj_.QueryInterface(com.IID_IUnknown)
        result["stage"] = "formal_provider"
        provider = app if source == SOURCE_APP else _framework(app, result)
        result["call_point"] = "provider.QueryInterface.IUnknown.before"
        provider_unknown = provider._oleobj_.QueryInterface(com.IID_IUnknown)
        result["stage"] = "query_interface"
        result["call_point"] = "provider.QueryInterface.IOleWindow"
        native_window = provider._oleobj_.QueryInterface(com.IID_IOleWindow)
        result["stage"] = "window_iunknown"
        result["call_point"] = "provider.IOleWindow.QueryInterface.IUnknown"
        _same(
            native_window.QueryInterface(com.IID_IUnknown),
            provider_unknown,
            "iolewindow_provider_iunknown_mismatch",
        )
        result["stage"] = "get_window"
        result["call_point"] = "provider.GetWindow.before"
        hwnd = native_window.GetWindow()
        if type(hwnd) is int and hwnd == 0:
            result["observed_hwnd"] = 0
            raise IdentityGap("native_window_handle_zero")
        if type(hwnd) is not int or hwnd <= 0:
            raise IdentityGap("native_window_handle_not_positive_integer")
        result["stage"] = "window_process"
        result["call_point"] = "win32.WindowToProcess.before"
        first = window_reader(hwnd)
        result["observed_window"] = _window_facts(first)
        if not isinstance(first, dict) or set(first) != {
            "hwnd",
            "is_window",
            "pid",
            "thread_id",
        }:
            raise IdentityGap("native_window_fields_incomplete_or_unexpected")
        pid = first.get("pid")
        if type(first.get("hwnd")) is not int or first["hwnd"] != hwnd:
            raise IdentityGap("actual_window_handle_contradiction")
        if first.get("is_window") is not True:
            raise IdentityGap("actual_window_not_live")
        if type(pid) is not int or pid <= 0:
            raise IdentityGap("actual_window_pid_missing")
        if type(first.get("thread_id")) is not int or first["thread_id"] <= 0:
            raise IdentityGap("actual_window_thread_missing")
        result["stage"] = "process_generation"
        result["call_point"] = "win32.ProcessGeneration.before"
        before = generation_reader(pid)
        facts, reason = _generation_observation(before, pid, executable)
        result["observed_generation"] = facts
        if reason:
            raise IdentityGap(reason)
        result["stage"] = "recheck"
        result["call_point"] = "provider.GetWindow.recheck"
        second_hwnd = native_window.GetWindow()
        if type(second_hwnd) is not int or second_hwnd != hwnd:
            result["rechecked_hwnd_matches"] = False
            raise IdentityGap("formal_native_window_handle_changed")
        result["call_point"] = "win32.WindowToProcess.recheck"
        second = window_reader(hwnd)
        result["rechecked_window"] = _window_facts(second)
        if second != first:
            if isinstance(second, dict) and second.get("pid") != pid:
                raise IdentityGap("actual_window_pid_changed")
            raise IdentityGap("actual_window_or_thread_changed")
        result["call_point"] = "win32.ProcessGeneration.recheck"
        after = generation_reader(pid)
        facts, reason = _generation_observation(after, pid, executable)
        facts["birth_matches_first_observation"] = (
            isinstance(after, dict)
            and after.get("birth_filetime_100ns") == before["birth_filetime_100ns"]
        )
        result["rechecked_generation"] = facts
        if reason:
            raise IdentityGap(reason)
        if before != after:
            raise IdentityGap(
                "process_birth_changed"
                if not facts["birth_matches_first_observation"]
                else "process_generation_changed"
            )
        result["call_point"] = "application.native_project.recheck"
        if document_binding(_read_document(app)) != expected:
            raise IdentityGap("native_document_changed")
        result["call_point"] = "application.FullName.recheck"
        if _path(app.FullName) != _path(executable):
            raise IdentityGap("native_executable_changed")
        again = app if source == SOURCE_APP else _framework(app, result)
        result["call_point"] = "provider.QueryInterface.IUnknown.recheck"
        _same(
            again._oleobj_.QueryInterface(com.IID_IUnknown),
            provider_unknown,
            "formal_provider_iunknown_changed",
        )
        result["call_point"] = "application.QueryInterface.IUnknown.recheck"
        _same(
            app._oleobj_.QueryInterface(com.IID_IUnknown),
            app_unknown,
            "application_iunknown_changed",
        )
        result.update(
            status="observed",
            stage="complete",
            hwnd=hwnd,
            pid=pid,
            api="GetWindowThreadProcessId",
            canonical_iunknown_equal=True,
            application_iunknown_stable=True,
            provider_iunknown_stable=True,
            framework_declaration=dict(FRAMEWORK_DECLARATION)
            if source == SOURCE_FRAMEWORK
            else None,
            native_document_binding_sha256=expected,
            process_generation=before,
            window_observation=first,
            rechecked=True,
        )
    except Exception as exc:
        result.update(
            reason=str(exc) if isinstance(exc, IdentityGap) else "native_interface_read_failed",
            error_type=type(exc).__name__,
        )
        hr = getattr(exc, "hresult", None)
        if type(hr) is int:
            result["hresult_hex"] = f"0x{hr & 0xFFFFFFFF:08x}"
            if hr & 0xFFFFFFFF == 0x80004002 and result["stage"] == "query_interface":
                result.update(status="unsupported", reason="iolewindow_not_supported")
    return result


def _may_try_framework(result):
    """A missing first interface/window can try the second; contradictions cannot."""
    return result.get("source") == SOURCE_APP and (
        (
            result.get("stage") == "query_interface"
            and result.get("status") == "unsupported"
            and result.get("hresult_hex") == "0x80004002"
        )
        or (
            result.get("stage") == "get_window"
            and result.get("reason") == "native_window_handle_zero"
            and type(result.get("observed_hwnd")) is int
            and result["observed_hwnd"] == 0
        )
        or (result.get("stage") == "get_window" and result.get("hresult_hex") == "0x80004005")
    )


def observe(
    app,
    document,
    executable,
    *,
    com_module=None,
    window_reader=None,
    generation_reader=None,
):
    """At most two formal providers; all acquisition is read-only and bounded."""
    try:
        com = com_module if com_module is not None else _load_com()
    except Exception as exc:
        return {
            "attempted": False,
            "status": "incomplete",
            "stage": "load_wrapper",
            "reason": "formal_iolewindow_wrapper_unavailable",
            "error_type": type(exc).__name__,
            "attempts": [],
        }
    attempts = []
    for source in SOURCES:
        result = _observe_one(
            app,
            document,
            executable,
            source,
            com,
            window_reader or _window,
            generation_reader or _generation,
        )
        if result["status"] == "observed":
            return {**result, "attempts": attempts}
        attempts.append(result)
        if not _may_try_framework(result):
            break
    return {
        "attempted": True,
        "status": "incomplete",
        "stage": "formal_candidates",
        "reason": "no_complete_formal_window_binding",
        "attempts": attempts,
    }


def validate(identity, rows):
    """Pure strict-release validation for the two exact native object sources."""
    try:
        proof, source = identity["designer_window_binding"], identity["pid_source"]
        pid, hwnd = identity["pid"], proof["hwnd"]
        matches = [row for row in rows if row["pid"] == pid]
        if len(matches) != 1 or proof["source"] not in SOURCES:
            return False
        row, generation = matches[0], proof["process_generation"]
        expected_declaration = (
            FRAMEWORK_DECLARATION if proof["source"] == SOURCE_FRAMEWORK else None
        )
        if proof["source"] == SOURCE_FRAMEWORK and proof.get("framework_observation") != {
            "getter_callable": True,
            "framework_return_present": True,
            "dispatch_wrapper_present": True,
            "type_info_present": True,
            "typeattr_iid_present": True,
            "observed_interface_iid": FRAMEWORK_IID,
        }:
            return False
        attempts = proof["attempts"]
        if (
            "ActiveViewHandle" in identity["public_fields"]
            and type(identity["public_fields"]["ActiveViewHandle"]) is not int
        ):
            return False
        if (
            proof["source"] == SOURCE_APP
            and attempts != []
            or proof["source"] == SOURCE_FRAMEWORK
            and (
                not isinstance(attempts, list)
                or len(attempts) != 1
                or not _may_try_framework(attempts[0])
            )
        ):
            return False
        return (
            identity.get("domain") == "schematic"
            and type(pid) is int
            and pid > 0
            and type(hwnd) is int
            and hwnd > 0
            and proof["status"] == "observed"
            and proof["stage"] == "complete"
            and proof["pid"] == pid
            and proof["api"] == "GetWindowThreadProcessId"
            and proof["canonical_iunknown_equal"] is True
            and proof["application_iunknown_stable"] is True
            and proof["provider_iunknown_stable"] is True
            and proof["framework_declaration"] == expected_declaration
            and proof["rechecked"] is True
            and proof["native_document_binding_sha256"] == document_binding(identity["document"])
            and source
            == {
                "property": proof["source"],
                "hwnd": hwnd,
                "api": "GetWindowThreadProcessId",
            }
            and generation["birth_filetime_100ns"] == row["birth_filetime_100ns"]
            and _generation_valid(generation, pid, identity["public_fields"]["FullName"])
            and _path(generation["executable"]) == _path(row["executable"])
            and proof["window_observation"]["hwnd"] == hwnd
            and proof["window_observation"]["is_window"] is True
            and proof["window_observation"]["pid"] == pid
            and type(proof["window_observation"]["thread_id"]) is int
            and proof["window_observation"]["thread_id"] > 0
        )
    except (KeyError, TypeError, ValueError):
        return False
