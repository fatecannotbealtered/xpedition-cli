"""Fresh non-CAD check through the final InputsLease.prepare implementation."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import struct
import sys
import time
from ctypes import wintypes as w
from pathlib import Path

ROOT = Path("C:/ExampleProjects/Task")
REPO = Path("C:/ExampleProjects/Task/xpedition-cli")
EXPECTED = "37438b26ab91e45e4b3c083e70c7a6b92cba7e511b7cea3ebb7ab17bfc2b57f7"
PAYLOADS = {
    "identity.vbs": b"Option Explicit\n' Final product-file synthetic fixture, never executed\n",
    "scripts.ini": b"[ViewDraw]\nScript#0=identity.vbs\n",
}


def main():
    if ROOT.exists():
        raise ValueError("fresh_product_probe_root_required")
    ROOT.mkdir()
    sys.path.insert(0, str(REPO))
    from xpedition_cli import startup_input_seal as S

    rows, lease, failure = [], None, None
    started = time.perf_counter()
    source = Path(S.__file__)
    result = {
        "scope": "fresh_final_product_prepare_non_cad_only",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cad_used": False,
        "com_used": False,
        "old_runtime_or_system_configuration_changed": False,
    }

    def check(phase, passed, **fields):
        rows.append({"phase": phase, "passed": passed, **fields})
        if not passed:
            raise AssertionError(phase)

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, returned in (
        (
            "CreateFileW",
            (w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE),
            w.HANDLE,
        ),
        ("CloseHandle", (w.HANDLE,), w.BOOL),
        ("MoveFileExW", (w.LPCWSTR, w.LPCWSTR, w.DWORD), w.BOOL),
        ("DeleteFileW", (w.LPCWSTR,), w.BOOL),
        (
            "ReadFile",
            (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p),
            w.BOOL,
        ),
        (
            "DeviceIoControl",
            (
                w.HANDLE,
                w.DWORD,
                ctypes.c_void_p,
                w.DWORD,
                ctypes.c_void_p,
                w.DWORD,
                ctypes.POINTER(w.DWORD),
                ctypes.c_void_p,
            ),
            w.BOOL,
        ),
    ):
        function = getattr(api, name)
        function.argtypes, function.restype = args, returned

    def denied_open(phase, path, access, disposition=3):
        handle = api.CreateFileW(str(path), access, 7, None, disposition, 0x80, None)
        opened = handle not in (None, ctypes.c_void_p(-1).value)
        error = None if opened else ctypes.get_last_error()
        if opened:
            check(phase + ".close", bool(api.CloseHandle(handle)))
        check(phase, not opened and error in (5, 32), winerror=error)

    def denied_move(phase, first, second, flags=0):
        value = api.MoveFileExW(str(first), str(second), flags)
        error = None if value else ctypes.get_last_error()
        check(phase, not value and error in (5, 32), winerror=error)

    try:
        check("source.expected", result["source_sha256"] == EXPECTED)
        lease = S.InputsLease(ROOT / "inputs")
        lease.prepare(PAYLOADS)
        baseline = lease.private_binding()
        facts = lease.verify_prelaunch()
        result["initial_facts"] = facts
        check("product.prepare_verified", facts["input_contract_verified"] is True)
        check(
            "product.workspace_transition_complete",
            facts["workspace_pin_transition_complete"] is True
            and facts["workspace_pin_share"] == 3,
        )
        check(
            "product.original_pin_retired",
            sum(h["kind"] == "retired_workspace" and h["state"] == "closed" for h in lease._holders)
            == 1,
        )
        check(
            "product.ancestor_pins_retained",
            all(h["state"] == "open" for h in lease._holders if h["kind"] == "ancestor"),
        )
        cache, replacement = lease.directory / "cache.cfg", lease.directory / "cache.tmp"
        for path, payload in ((cache, b"original-cache\n"), (replacement, b"replacement-cache\n")):
            with path.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            check(path.name + ".write_readback", path.read_bytes() == payload)
        value = api.MoveFileExW(str(replacement), str(cache), 1)
        check(
            "cache.absolute_replace",
            bool(value),
            winerror=None if value else ctypes.get_last_error(),
        )
        check("cache.complete_readback", cache.read_bytes() == b"replacement-cache\n")
        for name, payload in PAYLOADS.items():
            path = lease.directory / name
            handle = api.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
            check(name + ".sdk_exclusive_reader", handle not in (None, ctypes.c_void_p(-1).value))
            try:
                buffer, read = ctypes.create_string_buffer(len(payload) + 1), w.DWORD()
                value = api.ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None)
                check(
                    name + ".sdk_complete_bytes",
                    bool(value) and buffer.raw[: read.value] == payload,
                )
            finally:
                check(name + ".sdk_reader_closed", bool(api.CloseHandle(handle)))
            for label, access in (
                ("write", 0x40000000),
                ("delete", 0x10000),
                ("write_dac", 0x40000),
                ("write_owner", 0x80000),
            ):
                denied_open(name + "." + label, path, access)
            denied_open(name + ".ads", str(path) + ":probe:$DATA", 0x40000000, 2)
            value = api.DeleteFileW(str(path))
            error = None if value else ctypes.get_last_error()
            check(name + ".DeleteFile_denied", not value and error in (5, 32), winerror=error)
            denied_move(name + ".rename_denied", path, str(path) + ".moved")
            hostile = lease.directory / (name + ".replacement")
            hostile.write_bytes(b"synthetic replacement\n")
            denied_move(name + ".replace_denied", hostile, path, 1)
        denied_move("workspace.rename_denied", lease.directory, ROOT / "inputs-moved")
        denied_move("ancestor.rename_denied", ROOT, ROOT.with_name(ROOT.name + "-moved"))
        target = ROOT / "reparse-target"
        target.mkdir()
        handle = api.CreateFileW(str(lease.directory), 2, 7, None, 3, 0x02200000, None)
        check(
            "workspace.write_data_handle_available", handle not in (None, ctypes.c_void_p(-1).value)
        )
        try:
            substitute, printed = (
                ("\\??\\" + str(target)).encode("utf-16-le"),
                str(target).encode("utf-16-le"),
            )
            paths = substitute + b"\0\0" + printed + b"\0\0"
            body = (
                struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printed)) + paths
            )
            raw = struct.pack("<IHH", 0xA0000003, len(body), 0) + body
            buffer, returned = ctypes.create_string_buffer(raw), w.DWORD()
            value = api.DeviceIoControl(
                handle, 0x900A4, buffer, len(raw), None, 0, ctypes.byref(returned), None
            )
            error = None if value else ctypes.get_last_error()
            check("workspace.nonempty_reparse_denied", not value and error == 145, winerror=error)
        finally:
            check("workspace.reparse_handle_closed", bool(api.CloseHandle(handle)))
        check(
            "final.input_contract_verified",
            lease.verify_prelaunch()["input_contract_verified"] is True,
        )
        check("final.original_binding_unchanged", lease.private_binding() == baseline)
        check(
            "final.all_bytes_unchanged",
            all(
                (lease.directory / name).read_bytes() == payload
                for name, payload in PAYLOADS.items()
            ),
        )
        check(
            "final.no_com_imports",
            not any(
                name.split(".")[0] in {"pythoncom", "win32com", "comtypes"} for name in sys.modules
            ),
        )
    except BaseException as error:
        failure = {
            "type": type(error).__name__,
            "phase": str(error) if isinstance(error, AssertionError) else "probe_incomplete",
            "winerror": getattr(error, "winerror", None),
        }
    finally:
        if lease is not None:
            try:
                result["terminal"] = lease.terminal_release()
            except BaseException as error:
                result["terminal"] = {
                    "resources_released": False,
                    "category": getattr(error, "category", "probe_incomplete"),
                }
        result["source_after_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        result["source_unchanged"] = (
            result["source_sha256"] == result["source_after_sha256"] == EXPECTED
        )
        result["first_failure"], result["checks"] = failure, rows
        result["wall_seconds"] = round(time.perf_counter() - started, 3)
        terminal = result.get("terminal", {})
        result["passed"] = (
            failure is None
            and result["source_unchanged"]
            and terminal.get("resources_released") is True
            and terminal.get("files_removed") is False
            and terminal.get("immutable_files_retained") is True
        )
        with (ROOT / "result.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
        print(
            json.dumps(
                {
                    "passed": result["passed"],
                    "checks": len(rows),
                    "first_failure": failure,
                    "wall_seconds": result["wall_seconds"],
                }
            )
        )
    return 0 if result["passed"] else 21


if __name__ == "__main__":
    raise SystemExit(main())
