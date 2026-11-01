"""Read-only Windows COM diagnosis, suitable for one ordinary-user terminal run.

Reads allowlisted COM registry metadata, product process identity/bitness and
Windows security metadata. Never activates applications, binds documents, writes
registry, changes desktops/permissions, reads credentials or stores raw tokens.
The only persistent write is the explicitly supplied JSON evidence file.
"""

from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import json
import os
import struct
import sys
import uuid
import winreg
from ctypes import wintypes as W
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGIDS = tuple(
    base + suffix
    for base in ("Viewdraw.Application", "MGCPCB.Application", "MGCPCB.ExpeditionPCBApplication")
    for suffix in (".155", "", ".60")
)
PRODUCTS = {"viewdraw.exe", "expeditionpcb.exe"}
K = C.WinDLL("kernel32", use_last_error=True)
A = C.WinDLL("advapi32", use_last_error=True)
U = C.WinDLL("user32", use_last_error=True)
O = C.WinDLL("ole32", use_last_error=True)  # noqa: E741 -- original read-only probe symbol
OA = C.WinDLL("oleaut32", use_last_error=True)
K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
K.OpenProcess.restype = W.HANDLE
K.CloseHandle.argtypes = [W.HANDLE]
K.GetCurrentThreadId.restype = W.DWORD
K.ProcessIdToSessionId.argtypes = [W.DWORD, C.POINTER(W.DWORD)]
K.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
K.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
K.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
K.CreateToolhelp32Snapshot.restype = W.HANDLE
K.LocalFree.argtypes = [W.HLOCAL]
A.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
A.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)]
A.ConvertSidToStringSidW.argtypes = [W.LPVOID, C.POINTER(W.LPWSTR)]
A.LookupAccountSidW.argtypes = [
    W.LPCWSTR,
    W.LPVOID,
    W.LPWSTR,
    C.POINTER(W.DWORD),
    W.LPWSTR,
    C.POINTER(W.DWORD),
    C.POINTER(W.DWORD),
]
A.IsTokenRestricted.argtypes = [W.HANDLE]
U.GetProcessWindowStation.restype = W.HANDLE
U.GetThreadDesktop.argtypes = [W.DWORD]
U.GetThreadDesktop.restype = W.HANDLE
U.GetUserObjectInformationW.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)]
O.CoInitializeEx.argtypes = [W.LPVOID, W.DWORD]
O.CoInitializeEx.restype = C.c_long
O.CoUninitialize.argtypes = []


class PROCESSENTRY32(C.Structure):
    _fields_ = [
        ("dwSize", W.DWORD),
        ("cntUsage", W.DWORD),
        ("pid", W.DWORD),
        ("heap", C.c_size_t),
        ("module", W.DWORD),
        ("threads", W.DWORD),
        ("parent", W.DWORD),
        ("priority", W.LONG),
        ("flags", W.DWORD),
        ("exe", W.WCHAR * 260),
    ]


class THREADENTRY32(C.Structure):
    _fields_ = [
        ("size", W.DWORD),
        ("usage", W.DWORD),
        ("tid", W.DWORD),
        ("pid", W.DWORD),
        ("base_priority", W.LONG),
        ("delta_priority", W.LONG),
        ("flags", W.DWORD),
    ]


class LUID(C.Structure):
    _fields_ = [("low", W.DWORD), ("high", W.LONG)]


class TOKEN_STATISTICS(C.Structure):
    _fields_ = [
        ("token_id", LUID),
        ("authentication_id", LUID),
        ("expiration", C.c_longlong),
        ("token_type", W.DWORD),
        ("impersonation_level", W.DWORD),
        ("dynamic_charged", W.DWORD),
        ("dynamic_available", W.DWORD),
        ("group_count", W.DWORD),
        ("privilege_count", W.DWORD),
        ("modified_id", LUID),
    ]


class GUID(C.Structure):
    _fields_ = [("data1", W.DWORD), ("data2", W.WORD), ("data3", W.WORD), ("data4", C.c_ubyte * 8)]


K.Process32FirstW.argtypes = [W.HANDLE, C.POINTER(PROCESSENTRY32)]
K.Process32NextW.argtypes = [W.HANDLE, C.POINTER(PROCESSENTRY32)]
K.Thread32First.argtypes = [W.HANDLE, C.POINTER(THREADENTRY32)]
K.Thread32Next.argtypes = [W.HANDLE, C.POINTER(THREADENTRY32)]
OA.GetActiveObject.argtypes = [C.POINTER(GUID), W.LPVOID, C.POINTER(W.LPVOID)]
OA.GetActiveObject.restype = C.c_long


def error(stage, number=None):
    value = C.get_last_error() if number is None else number
    return {"stage": stage, "winerror": value, "message": C.FormatError(value).strip()}


def hresult(value):
    code = value & 0xFFFFFFFF
    return {
        "signed": value,
        "hex": f"0x{code:08X}",
        "name": {
            0: "S_OK",
            1: "S_FALSE",
            0x800401E3: "MK_E_UNAVAILABLE",
            0x80070005: "E_ACCESSDENIED",
            0x80040154: "REGDB_E_CLASSNOTREG",
            0x800401F3: "CO_E_CLASSSTRING",
            0x80010106: "RPC_E_CHANGED_MODE",
        }.get(code, "other"),
    }


def user_object_name(handle):
    text, needed = C.create_unicode_buffer(1024), W.DWORD()
    if not handle:
        return error("GetThreadDesktop/GetProcessWindowStation")
    if U.GetUserObjectInformationW(handle, 2, text, C.sizeof(text), C.byref(needed)):
        return text.value
    return error("GetUserObjectInformationW(UOI_NAME)")


def process_list():
    handle = K.CreateToolhelp32Snapshot(2, 0)
    if handle == W.HANDLE(-1).value:
        return [], error("CreateToolhelp32Snapshot(processes)")
    entry = PROCESSENTRY32()
    entry.dwSize = C.sizeof(entry)
    rows = []
    try:
        more = K.Process32FirstW(handle, C.byref(entry))
        while more:
            rows.append({"pid": entry.pid, "parent_pid": entry.parent, "exe": entry.exe})
            more = K.Process32NextW(handle, C.byref(entry))
    finally:
        K.CloseHandle(handle)
    return rows, None


def thread_desktops(pids):
    result = {pid: [] for pid in pids}
    handle = K.CreateToolhelp32Snapshot(4, 0)
    if handle == W.HANDLE(-1).value:
        return {"error": error("CreateToolhelp32Snapshot(threads)")}
    entry = THREADENTRY32()
    entry.size = C.sizeof(entry)
    try:
        more = K.Thread32First(handle, C.byref(entry))
        while more:
            if entry.pid in result and len(result[entry.pid]) < 4:
                previous_denial = any(
                    isinstance(item["desktop"], dict) and item["desktop"].get("winerror") == 5
                    for item in result[entry.pid]
                )
                if not previous_denial:
                    result[entry.pid].append(
                        {
                            "thread_id": entry.tid,
                            "desktop": user_object_name(U.GetThreadDesktop(entry.tid)),
                        }
                    )
            more = K.Thread32Next(handle, C.byref(entry))
    finally:
        K.CloseHandle(handle)
    return result


def token_buffer(handle, kind):
    needed = W.DWORD()
    A.GetTokenInformation(handle, kind, None, 0, C.byref(needed))
    if needed.value == 0 or needed.value > 65536:
        raise OSError(C.get_last_error(), "GetTokenInformation size")
    buf = C.create_string_buffer(needed.value)
    if not A.GetTokenInformation(handle, kind, buf, needed, C.byref(needed)):
        raise OSError(C.get_last_error(), "GetTokenInformation")
    return buf


def sid_text(ptr):
    text = W.LPWSTR()
    if not A.ConvertSidToStringSidW(ptr, C.byref(text)):
        raise OSError(C.get_last_error(), "ConvertSidToStringSidW")
    try:
        return text.value
    finally:
        K.LocalFree(C.cast(text, W.HLOCAL))


def security_metadata(process):
    handle = W.HANDLE()
    if not A.OpenProcessToken(process, 0x0008, C.byref(handle)):
        return {"available": False, "error": error("OpenProcessToken(TOKEN_QUERY)")}
    facts = {
        "available": True,
        "scope": "nonsecret owner/logon/integrity metadata only; no token content retained",
    }
    try:
        try:
            buf = token_buffer(handle, 1)
            sid = C.cast(buf, C.POINTER(W.LPVOID))[0]
            facts["user_sid"] = sid_text(sid)
            name, domain = C.create_unicode_buffer(256), C.create_unicode_buffer(256)
            n, d, use = W.DWORD(256), W.DWORD(256), W.DWORD()
            if A.LookupAccountSidW(None, sid, name, C.byref(n), domain, C.byref(d), C.byref(use)):
                facts["account"] = domain.value + "\\" + name.value
        except OSError as exc:
            facts["owner_error"] = str(exc)
        for key, kind in (
            ("token_session_id", 12),
            ("elevation_type", 18),
            ("elevated", 20),
            ("app_container", 29),
        ):
            try:
                facts[key] = C.cast(token_buffer(handle, kind), C.POINTER(W.DWORD))[0]
            except OSError as exc:
                facts[key + "_error"] = str(exc)
        try:
            buf = token_buffer(handle, 25)
            facts["integrity_sid"] = sid_text(C.cast(buf, C.POINTER(W.LPVOID))[0])
        except OSError as exc:
            facts["integrity_error"] = str(exc)
        try:
            buf = token_buffer(handle, 10)
            stats = C.cast(buf, C.POINTER(TOKEN_STATISTICS)).contents
            facts["logon_authentication_luid"] = (
                f"0x{stats.authentication_id.high & 0xFFFFFFFF:08X}:"
                f"{stats.authentication_id.low:08X}"
            )
        except OSError as exc:
            facts["logon_error"] = str(exc)
        facts["restricted_token"] = bool(A.IsTokenRestricted(handle))
    finally:
        K.CloseHandle(handle)
    return facts


def process_identity(row):
    facts = dict(row)
    session = W.DWORD()
    facts["session_id"] = (
        session.value
        if K.ProcessIdToSessionId(row["pid"], C.byref(session))
        else error("ProcessIdToSessionId")
    )
    handle = K.OpenProcess(0x1000, False, row["pid"])
    if not handle:
        facts["query_error"] = error("OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)")
        return facts
    try:
        text, size = C.create_unicode_buffer(32768), W.DWORD(32768)
        facts["path"] = (
            text.value
            if K.QueryFullProcessImageNameW(handle, 0, text, C.byref(size))
            else error("QueryFullProcessImageNameW")
        )
        times = [W.FILETIME() for _ in range(4)]
        if K.GetProcessTimes(handle, *(C.byref(t) for t in times)):
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            facts["created_utc"] = datetime.fromtimestamp(
                ticks / 10000000 - 11644473600, UTC
            ).isoformat()
        if hasattr(K, "IsWow64Process2"):
            K.IsWow64Process2.argtypes = [W.HANDLE, C.POINTER(W.WORD), C.POINTER(W.WORD)]
            machine, native = W.WORD(), W.WORD()
            if K.IsWow64Process2(handle, C.byref(machine), C.byref(native)):
                facts["architecture"] = {
                    "process_machine": hex(machine.value),
                    "native_machine": hex(native.value),
                    "bitness": 64
                    if machine.value == 0 and native.value in {0x8664, 0xAA64}
                    else 32,
                }
            else:
                facts["architecture"] = error("IsWow64Process2")
        facts["security_metadata"] = security_metadata(handle)
    finally:
        K.CloseHandle(handle)
    return facts


def registry_value(path, view, name=""):
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path, 0, winreg.KEY_READ | view) as key:
            return {"value": winreg.QueryValueEx(key, name)[0]}
    except OSError as exc:
        return {"winerror": exc.winerror}


def registrations():
    rows = []
    for label, view in (("64-bit", winreg.KEY_WOW64_64KEY), ("32-bit", winreg.KEY_WOW64_32KEY)):
        for progid in PROGIDS:
            clsid = registry_value(progid + "\\CLSID", view)
            row = {"view": label, "progid": progid, "clsid": clsid}
            if clsid.get("value"):
                base = "CLSID\\" + clsid["value"]
                row["class_metadata"] = {
                    name: registry_value(base + "\\" + name, view)
                    for name in (
                        "ProgID",
                        "VersionIndependentProgID",
                        "TypeLib",
                        "Version",
                        "LocalServer32",
                        "InprocServer32",
                    )
                }
            rows.append(row)
    return rows


def release_unknown(pointer):
    if pointer:
        table = C.cast(pointer, C.POINTER(C.POINTER(W.LPVOID))).contents
        release = C.WINFUNCTYPE(W.ULONG, W.LPVOID)(table[2])
        release(pointer)


def active_object(clsid):
    guid = GUID.from_buffer_copy(uuid.UUID(clsid).bytes_le)
    ptr = W.LPVOID()
    code = OA.GetActiveObject(C.byref(guid), None, C.byref(ptr))
    result = {
        "stage": "OleAut32.GetActiveObject(CLSID), before IDispatch/type-library wrapper",
        "hresult": hresult(code),
        "unknown_pointer_returned": bool(ptr),
        "activation_attempted": False,
    }
    release_unknown(ptr)
    return result


def rot_summary(clsids):
    # Python COM only enumerates; no BindToObject or ROT.GetObject on arbitrary monikers.
    try:
        import pythoncom

        rot = pythoncom.GetRunningObjectTable()
        enum = rot.EnumRunning()
        ctx = pythoncom.CreateBindCtx(0)
        count, matches, errors = 0, [], 0
        while count < 4096:
            items = enum.Next(1)
            if not items:
                break
            count += 1
            try:
                text = items[0].GetDisplayName(ctx, None)
                matched = [value for value in clsids if value.lower() in text.lower()]
                if matched:
                    matches.append(
                        {
                            "clsids": matched,
                            "display_name_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        }
                    )
            except Exception:
                errors += 1
        return {
            "stage": "ROT.EnumRunning and moniker.GetDisplayName only",
            "enumerated": count,
            "matching_registered_clsids": matches,
            "display_name_errors": errors,
            "raw_names_retained": False,
            "truncated_at_4096": count == 4096,
        }
    except Exception as exc:
        return {
            "error_type": type(exc).__name__,
            "hresult": getattr(exc, "hresult", None),
            "message": str(exc),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="New JSON evidence path; omit to print JSON only"
    )
    parser.add_argument(
        "--require-account",
        help="Refuse COM probes unless this ordinary, non-elevated Windows account is the caller",
    )
    args = parser.parse_args()
    if args.output and args.output.exists():
        raise SystemExit("Refusing to replace existing evidence")
    if args.output and ROOT.resolve() not in args.output.resolve().parents:
        raise SystemExit("The JSON output must stay in the authorized task workspace")
    all_processes, process_error = process_list()
    selected = [p for p in all_processes if p["exe"].lower() in PRODUCTS or p["pid"] == os.getpid()]
    selected_pids = {p["pid"] for p in selected}
    selected_pids.update(p["parent_pid"] for p in selected)
    selected = [p for p in all_processes if p["pid"] in selected_pids]
    identities = [process_identity(p) for p in selected]
    caller = next((p for p in identities if p["pid"] == os.getpid()), {})
    security = caller.get("security_metadata") or {}
    if args.require_account and (
        security.get("account", "").casefold() != args.require_account.casefold()
        or security.get("elevated") != 0
        or security.get("restricted_token") is not False
    ):
        refusal = {
            "utc": datetime.now(UTC).isoformat(),
            "guard": "ordinary_account_mismatch",
            "expected_account": args.require_account,
            "caller": caller,
            "COM_probe_attempted": False,
            "next_action": "Use the user's existing ordinary terminal; "
            "do not change or impersonate the current identity.",
        }
        if args.output:
            args.output.write_text(
                json.dumps(refusal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(json.dumps(refusal, ensure_ascii=False))
        return 2
    reg = registrations()
    classes = sorted({row["clsid"]["value"] for row in reg if row["clsid"].get("value")})
    init = O.CoInitializeEx(None, 2)
    record = {
        "utc": datetime.now(UTC).isoformat(),
        "current_pid": os.getpid(),
        "python_executable": sys.executable,
        "python_bitness": struct.calcsize("P") * 8,
        "desktop": user_object_name(U.GetThreadDesktop(K.GetCurrentThreadId())),
        "window_station": user_object_name(U.GetProcessWindowStation()),
        "processes": identities,
        "thread_desktop_samples": thread_desktops(selected_pids),
        "process_enumeration_error": process_error,
        "registrations": reg,
        "COM_initialization": hresult(init),
        "read_only": True,
        "application_activation_or_document_binding": False,
        "system_registry_permissions_desktop_changes": False,
        "credentials_license_contents_session_storage_read": False,
        "native_task_acceptance": "not_run",
        "probe_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if init >= 0:
        try:
            record["raw_active_object_probes"] = [
                {"clsid": clsid, **active_object(clsid)} for clsid in classes
            ]
            record["visible_ROT"] = rot_summary(classes)
        finally:
            O.CoUninitialize()
    if args.output:
        args.output.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "desktop": record["desktop"],
                    "raw_hresult": [
                        p["hresult"] for p in record.get("raw_active_object_probes", [])
                    ],
                    "visible_rot": record.get("visible_ROT"),
                    "native_task_acceptance": "not_run",
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.dont_write_bytecode = True
    raise SystemExit(main())
