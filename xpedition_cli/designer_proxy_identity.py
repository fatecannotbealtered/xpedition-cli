"""One non-authorizing, window-independent observation of a held COM proxy.

Only the standard in-process GIT may be created. It transfers the already held
IDispatch between Python bindings; it does not discover or activate CAD. The
documented CoDecodeProxy debugging API is attempted once on that actual typed
interface, after canonical-IUnknown equality is established. Failure never
falls back to pointer-layout, repr, OBJREF or window scanning.
"""

from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager

from . import designer_window_identity as W

MAX_READ_OPERATIONS = 32
MAX_WALL_SECONDS = 15.0
SOURCE = "held_IDispatch.standard_GIT.CoDecodeProxy"
STD_GIT_CLSID = "{00000323-0000-0000-C000-000000000046}"
GIT_IID = "{00000146-0000-0000-C000-000000000046}"


class ObservationGap(ValueError):
    """A fixed reason, never a native description, address or private path."""


class Budget:
    def __init__(self, clock):
        self.clock, self.started, self.calls = clock, clock(), 0
        self.point = "proxy.ownership"

    def read(self, point, operation):
        self.point = point
        if self.calls >= MAX_READ_OPERATIONS:
            raise ObservationGap("proxy_read_limit")
        if self.clock() - self.started > MAX_WALL_SECONDS:
            raise ObservationGap("proxy_wall_limit")
        self.calls += 1
        value = operation()
        if self.clock() - self.started > MAX_WALL_SECONDS:
            raise ObservationGap("proxy_wall_limit")
        return value


def _same(left, right, reason):
    value = left == right
    if type(value) is not bool or value is not True:
        raise ObservationGap(reason)


def _ownership(ownership, identity):
    if not isinstance(ownership, dict) or ownership.get("status") != "verified":
        raise ObservationGap("proxy_original_start_ownership_missing")
    if ownership.get("domain") != "schematic" or identity.get("domain") != "schematic":
        raise ObservationGap("proxy_original_start_domain_mismatch")
    if not re.fullmatch(r"op-[a-zA-Z0-9_-]{1,80}", str(ownership.get("startup_operation_id"))):
        raise ObservationGap("proxy_original_start_id_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(ownership.get("source_sha256"))):
        raise ObservationGap("proxy_original_start_digest_invalid")
    rows = ownership.get("generations")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2:
        raise ObservationGap("proxy_original_start_generations_invalid")
    if any(not isinstance(row, dict) for row in rows):
        raise ObservationGap("proxy_original_start_generation_invalid")
    if [row.get("role") for row in rows].count("application") != 1 or any(
        row.get("role") not in ("application", "launcher") for row in rows
    ):
        raise ObservationGap("proxy_original_start_roles_invalid")
    if any(type(row.get("pid")) is not int or not 0 < row["pid"] < 2**32 for row in rows):
        raise ObservationGap("proxy_original_start_pid_invalid")
    if len({row["pid"] for row in rows}) != len(rows):
        raise ObservationGap("proxy_original_start_pid_duplicate")
    for row in rows:
        birth = row.get("creation_time_100ns")
        if (
            not isinstance(birth, str)
            or not birth.isascii()
            or not birth.isdecimal()
            or int(birth) <= 0
        ):
            raise ObservationGap("proxy_original_start_birth_invalid")
        W._path(row.get("executable"))
    worker = next(row for row in rows if row["role"] == "application")
    if W._path(identity.get("public_fields", {}).get("FullName")) != W._path(worker["executable"]):
        raise ObservationGap("proxy_original_start_image_context_mismatch")
    if identity.get("document", {}).get("open") is not True or W._path(
        identity["document"].get("path")
    ) != W._path(ownership.get("project")):
        raise ObservationGap("proxy_original_start_project_context_mismatch")
    return worker


def _matches_generation(row, observed):
    try:
        return (
            isinstance(observed, dict)
            and set(observed) == {"pid", "alive", "birth_filetime_100ns", "executable"}
            and type(observed.get("pid")) is int
            and observed["pid"] == row["pid"]
            and observed.get("alive") is True
            and observed.get("birth_filetime_100ns") == row["creation_time_100ns"]
            and W._path(observed.get("executable")) == W._path(row["executable"])
        )
    except (ValueError, TypeError):
        return False


@contextmanager
def _process_pin(row):
    """Hold one query-only handle for the entire proxy-to-process observation."""
    import ctypes
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = (w.DWORD, w.BOOL, w.DWORD), w.HANDLE
    kernel.GetProcessTimes.argtypes = (w.HANDLE,) + (ctypes.POINTER(w.FILETIME),) * 4
    kernel.GetProcessTimes.restype = w.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = (
        w.HANDLE,
        w.DWORD,
        w.LPWSTR,
        ctypes.POINTER(w.DWORD),
    )
    kernel.QueryFullProcessImageNameW.restype = w.BOOL
    kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = (
        (w.HANDLE, w.DWORD),
        w.DWORD,
    )
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = (w.HANDLE,), w.BOOL
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, row["pid"])
    if not handle:
        raise ObservationGap("proxy_process_handle_unavailable")

    def read():
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            raise ObservationGap("proxy_process_times_unavailable")
        length, image = w.DWORD(32768), ctypes.create_unicode_buffer(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
            raise ObservationGap("proxy_process_image_unavailable")
        birth, ended = [(item.dwHighDateTime << 32) | item.dwLowDateTime for item in times[:2]]
        wait = kernel.WaitForSingleObject(handle, 0)
        if wait not in (0, 258):
            raise ObservationGap("proxy_process_wait_failed")
        return {
            "pid": row["pid"],
            "alive": ended == 0 and wait == 258,
            "birth_filetime_100ns": str(birth),
            "executable": image.value,
        }

    try:
        yield read
    finally:
        if not kernel.CloseHandle(handle):
            raise ObservationGap("proxy_process_handle_cleanup_failed")


def _typed_git():
    """Lazy binding; importing comtypes.git would itself create a GIT object."""
    import ctypes
    from ctypes.wintypes import DWORD

    import comtypes
    from comtypes import GUID, HRESULT, STDMETHOD, IUnknown
    from comtypes.automation import IDispatch

    class IGlobalInterfaceTable(IUnknown):
        _iid_ = GUID(GIT_IID)
        _methods_ = [
            STDMETHOD(
                HRESULT,
                "RegisterInterfaceInGlobal",
                [ctypes.POINTER(IUnknown), ctypes.POINTER(GUID), ctypes.POINTER(DWORD)],
            ),
            STDMETHOD(HRESULT, "RevokeInterfaceFromGlobal", [DWORD]),
            STDMETHOD(
                HRESULT,
                "GetInterfaceFromGlobal",
                [DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.POINTER(IUnknown))],
            ),
        ]

    git = comtypes.CoCreateInstance(
        GUID(STD_GIT_CLSID), interface=IGlobalInterfaceTable, clsctx=comtypes.CLSCTX_INPROC_SERVER
    )

    def get(cookie):
        ptr = ctypes.POINTER(IDispatch)()
        git.GetInterfaceFromGlobal(cookie, ctypes.byref(IDispatch._iid_), ctypes.byref(ptr))
        return ptr

    return get


def _pointer_value(typed):
    import ctypes

    from comtypes.automation import IDispatch

    if type(typed) is not ctypes.POINTER(IDispatch):
        raise ObservationGap("proxy_git_return_not_typed_dispatch")
    return ctypes.cast(typed, ctypes.c_void_p).value


@contextmanager
def _git_bridge(raw, com, facts, budget):
    git = budget.read(
        "proxy.git.create_standard_table",
        lambda: com.CoCreateInstance(
            com.CLSID_StdGlobalInterfaceTable,
            None,
            com.CLSCTX_INPROC_SERVER,
            com.IID_IGlobalInterfaceTable,
        ),
    )
    cookie = None

    def register():
        nonlocal cookie
        cookie = git.RegisterInterfaceInGlobal(raw, com.IID_IDispatch)
        if type(cookie) is not int or not 0 < cookie < 2**32:
            raise ObservationGap("proxy_git_cookie_invalid")
        facts["registered"] = True
        return cookie

    try:
        budget.read("proxy.git.register_held_dispatch", register)
        getter = budget.read("proxy.git.bind_standard_table", _typed_git)
        typed = budget.read("proxy.git.get_held_dispatch", lambda: getter(cookie))
        pointer = _pointer_value(typed)
        if type(pointer) is not int or not 0 < pointer < 2**64:
            raise ObservationGap("proxy_git_dispatch_pointer_invalid")
        facts["typed_dispatch_present"] = True
        yield pointer
    finally:
        # Resource cleanup is mandatory even when the soft observation budget expired.
        if type(cookie) is int and 0 < cookie < 2**32:
            facts["revoke_attempted"] = True
            try:
                git.RevokeInterfaceFromGlobal(cookie)
            except Exception as exc:
                budget.point = "proxy.git.revoke_created_cookie"
                raise ObservationGap("proxy_git_registration_cleanup_failed") from exc
            facts["revoked"] = True
        # The typed pointer and any ObjectFromAddress wrapper own independent COM
        # references. Their bindings release them; do not manually Release again.


def _decode(pointer, progress):
    import ctypes
    from ctypes import wintypes as w

    class ServerInformation(ctypes.Structure):
        _fields_ = [("pid", w.DWORD), ("tid", w.DWORD), ("address", ctypes.c_uint64)]

    progress["api_stage"] = "abi"
    if ctypes.sizeof(ServerInformation) != 16 or ctypes.sizeof(w.DWORD) != 4:
        raise ObservationGap("proxy_decode_abi_invalid")
    progress["api_stage"] = "resolve"
    try:
        api = ctypes.WinDLL("combase", use_last_error=True).CoDecodeProxy
    except (AttributeError, OSError) as exc:
        raise ObservationGap("proxy_decode_api_unavailable") from exc
    api.argtypes, api.restype = (
        (w.DWORD, ctypes.c_uint64, ctypes.POINTER(ServerInformation)),
        ctypes.c_long,
    )
    info = ServerInformation()
    progress["api_available"] = True
    progress["api_stage"] = "call"
    progress["decode_attempts"] += 1
    hr = int(api(os.getpid(), pointer, ctypes.byref(info))) & 0xFFFFFFFF
    progress["decode_returns"] += 1
    progress["api_stage"] = "returned"
    progress["hresult"] = f"0x{hr:08x}"
    # Never expose either local interface or remote server addresses.
    return {
        "hresult": hr,
        "server_pid": int(info.pid),
        "server_tid": int(info.tid),
        "server_address_present": bool(info.address),
    }


def observe(
    app,
    identity,
    ownership,
    *,
    com_module=None,
    bridge_factory=None,
    process_pin_factory=None,
    decoder=None,
    document_reader=None,
    clock=time.monotonic,
):
    """Diagnostic only: even a complete match does not authorize a writer."""
    result = {
        "status": "incomplete",
        "source": SOURCE,
        "authority": False,
        "formal_relation_observed": False,
        "reason": None,
        "call_point": "proxy.ownership",
        "decode_attempts": 0,
        "decode_returns": 0,
        "api_available": False,
        "api_stage": "not_started",
        "git": {
            "registered": False,
            "typed_dispatch_present": False,
            "revoke_attempted": False,
            "revoked": False,
        },
    }
    budget = Budget(clock)
    try:
        worker = _ownership(ownership, identity)
        com = com_module if com_module is not None else W._load_com()
        raw = app._oleobj_
        if type(raw) is not getattr(com, "TypeIIDs", {}).get(com.IID_IDispatch):
            raise ObservationGap("proxy_held_dispatch_type_unqualified")
        reader = document_reader if document_reader is not None else W._read_document
        binding = W.document_binding(identity.get("document"))
        held = budget.read(
            "proxy.held_IUnknown.before", lambda: raw.QueryInterface(com.IID_IUnknown)
        )
        with (process_pin_factory or _process_pin)(worker) as generation:
            before = budget.read("proxy.generation.before", generation)
            if not _matches_generation(worker, before):
                raise ObservationGap("proxy_generation_before_mismatch")
            _same(
                binding,
                W.document_binding(budget.read("proxy.document.before", lambda: reader(app))),
                "proxy_document_before_mismatch",
            )
            with (bridge_factory or _git_bridge)(raw, com, result["git"], budget) as pointer:
                if type(pointer) is not int or not 0 < pointer < 2**64:
                    raise ObservationGap("proxy_git_dispatch_pointer_invalid")
                bridged = budget.read(
                    "proxy.pythoncom_bridge",
                    lambda: com.ObjectFromAddress(pointer, com.IID_IDispatch),
                )
                if type(bridged) is not getattr(com, "TypeIIDs", {}).get(com.IID_IDispatch):
                    raise ObservationGap("proxy_bridge_return_type_unqualified")
                equivalent = budget.read(
                    "proxy.bridge_IUnknown.before", lambda: bridged.QueryInterface(com.IID_IUnknown)
                )
                _same(held, equivalent, "proxy_bridge_canonical_mismatch")
                result["bridge_canonical_equal"] = True

                def call_decode():
                    return (decoder or _decode)(pointer, result)

                decoded = budget.read("proxy.CoDecodeProxy", call_decode)
                if not isinstance(decoded, dict) or set(decoded) != {
                    "hresult",
                    "server_pid",
                    "server_tid",
                    "server_address_present",
                }:
                    raise ObservationGap("proxy_decode_result_shape_invalid")
                hr = decoded["hresult"]
                if type(hr) is not int or not 0 <= hr < 2**32:
                    raise ObservationGap("proxy_decode_hresult_invalid")
                result["hresult"] = f"0x{hr:08x}"
                if hr != 0:
                    reasons = {
                        0x80070005: "proxy_decode_access_denied",
                        0x80010113: "proxy_decode_invalid_ipid",
                    }
                    raise ObservationGap(reasons.get(hr, "proxy_decode_native_failure"))
                if (
                    type(decoded["server_pid"]) is not int
                    or not 0 < decoded["server_pid"] < 2**32
                    or type(decoded["server_tid"]) is not int
                    or not 0 <= decoded["server_tid"] < 2**32
                    or type(decoded["server_address_present"]) is not bool
                    or decoded["server_address_present"] is not True
                ):
                    raise ObservationGap("proxy_decode_server_information_invalid")
                result.update(
                    server_pid=decoded["server_pid"],
                    server_thread_kind={0: "mta", 0xFFFF: "na"}.get(decoded["server_tid"], "sta"),
                    server_address_present=True,
                )
                if decoded["server_pid"] != worker["pid"]:
                    raise ObservationGap("proxy_decode_server_differs_from_owned_worker")
                _same(
                    held,
                    budget.read(
                        "proxy.held_IUnknown.after", lambda: raw.QueryInterface(com.IID_IUnknown)
                    ),
                    "proxy_held_canonical_drift",
                )
                _same(
                    held,
                    budget.read(
                        "proxy.bridge_IUnknown.after",
                        lambda: bridged.QueryInterface(com.IID_IUnknown),
                    ),
                    "proxy_bridge_canonical_drift",
                )
                _same(
                    binding,
                    W.document_binding(budget.read("proxy.document.after", lambda: reader(app))),
                    "proxy_document_drift",
                )
                after = budget.read("proxy.generation.after", generation)
                if not _matches_generation(worker, after):
                    raise ObservationGap("proxy_generation_after_mismatch")
                result.update(
                    native_document_binding_sha256=binding,
                    process_birth_filetime_100ns=worker["creation_time_100ns"],
                    generation_rechecked=True,
                )
        result.update(
            status="observed",
            reason="held_proxy_server_generation_observed",
            formal_relation_observed=True,
        )
    except ObservationGap as exc:
        result["reason"] = str(exc)
    except Exception as exc:
        result["reason"] = "proxy_observation_failed"
        hr = getattr(exc, "hresult", None)
        if type(hr) is int:
            result["hresult"] = f"0x{hr & 0xFFFFFFFF:08x}"
    result["call_point"] = budget.point
    result["read_operations"] = budget.calls
    result["elapsed_ms"] = max(0, round((clock() - budget.started) * 1000))
    result["budgets"] = {
        "read_operations": MAX_READ_OPERATIONS,
        "soft_wall_seconds": MAX_WALL_SECONDS,
        "decode_attempts": 1,
    }
    return result
