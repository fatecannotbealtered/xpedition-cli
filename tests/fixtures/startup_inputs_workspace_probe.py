"""One fresh non-CAD workspace sharing probe; no installed/runtime changes."""

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
PAYLOADS = {
    "identity.vbs": b"Option Explicit\n' inert synthetic startup input, never executed\n",
    "scripts.ini": b"[ViewDraw]\nScript#0=identity.vbs\n",
}


def main():
    if ROOT.exists():
        raise ValueError("fresh_probe_root_required")
    ROOT.mkdir()
    sys.path.insert(0, str(REPO))
    from xpedition_cli import startup_input_seal as S

    started = time.perf_counter()
    rows = []
    lease = None
    result = {
        "scope": "fresh_non_cad_workspace_share_transition_only",
        "cad_used": False,
        "com_used": False,
        "system_configuration_changed": False,
        "source_sha256": hashlib.sha256(Path(S.__file__).read_bytes()).hexdigest(),
        "first_failure": None,
    }

    def record(phase, passed, **fields):
        row = {"phase": phase, "passed": passed, **fields}
        rows.append(row)
        with (ROOT / "observations.ndjson").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
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

    def call(name, *args):
        value = getattr(api, name)(*args)
        error = ctypes.get_last_error() if not value else None
        return value, error

    def denied_open(phase, path, access, disposition=3):
        handle = api.CreateFileW(str(path), access, 7, None, disposition, 0x80, None)
        opened = handle not in (None, ctypes.c_void_p(-1).value)
        error = None if opened else ctypes.get_last_error()
        if opened:
            record(phase + ".close", bool(api.CloseHandle(handle)))
        record(phase, not opened and error in (5, 32), winerror=error)

    try:
        lease = S.InputsLease(ROOT / "inputs")
        lease.prepare(PAYLOADS)
        original_binding = lease.private_binding()
        record(
            "initial.prepare_verified", lease.verify_prelaunch()["input_contract_verified"] is True
        )
        ancestors = [h for h in lease._holders if h["kind"] == "ancestor"]
        ancestor_handles = tuple(h["handle"] for h in ancestors)
        original = next(h for h in lease._holders if h["kind"] == "workspace")
        directory_id = original["identity"]["file_id"]
        cache_source = lease.directory / "cache-new.tmp"
        cache_target = lease.directory / "cache.cfg"
        for path, payload in ((cache_source, b"new-cache\n"), (cache_target, b"old-cache\n")):
            with path.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            record(path.name + ".create_write_readback", path.read_bytes() == payload)
        value, error = call("MoveFileExW", str(cache_source), str(cache_target), 1)
        record("share1.absolute_replace_control", not value and error == 32, winerror=error)

        opened = lease._api._open("inputs", ancestors[-1]["handle"], S.DIRECTORY_ACCESS, 3, 1, 0x21)
        replacement = lease._hold(opened, "workspace_candidate", lease.directory)
        record("candidate.opened_existing", opened["disposition"] == 1)
        lease._access(replacement, S.DIRECTORY_ACCESS, S.DIRECTORY_ACCESS)
        replacement["identity"] = lease._directory_identity(replacement)
        record(
            "candidate.same_original_directory", replacement["identity"]["file_id"] == directory_id
        )
        record(
            "candidate.exact_original_dacl",
            lease._api.security_exact(replacement["handle"], S.DIRECTORY_MASK),
        )
        lease._verify_win32(replacement, lease.directory, directory=True)
        record(
            "candidate.full_reverification_before_retire",
            lease.verify_prelaunch()["input_contract_verified"] is True,
        )
        record("candidate.original_still_held", original["state"] == "open")
        lease._close(original)
        original["kind"] = "retired_workspace"
        replacement["kind"] = "workspace"
        record("workspace.old_pin_closed_after_new_proof", original["state"] == "closed")
        record(
            "workspace.ancestor_handles_unchanged",
            tuple(h["handle"] for h in ancestors) == ancestor_handles,
        )
        record(
            "workspace.full_reverification_after_retire",
            lease.verify_prelaunch()["input_contract_verified"] is True,
        )

        value, error = call("MoveFileExW", str(cache_source), str(cache_target), 1)
        record("share3.absolute_cache_replace", bool(value), winerror=error)
        record("share3.cache_complete_readback", cache_target.read_bytes() == b"new-cache\n")
        for name, payload in PAYLOADS.items():
            path = lease.directory / name
            for label, access, disposition in (
                ("write", 0x40000000, 3),
                ("append", 4, 3),
                ("truncate", 0x40000000, 5),
                ("delete", 0x10000, 3),
                ("write_dac", 0x40000, 3),
                ("write_owner", 0x80000, 3),
            ):
                denied_open(name + "." + label, path, access, disposition)
            denied_open(name + ".alternate_data_stream", str(path) + ":probe:$DATA", 0x40000000, 2)
            value, error = call("DeleteFileW", str(path))
            record(name + ".delete_file", not value and error in (5, 32), winerror=error)
            value, error = call("MoveFileExW", str(path), str(path) + ".moved", 0)
            record(name + ".rename", not value and error in (5, 32), winerror=error)
            hostile = lease.directory / (name + ".replacement")
            hostile.write_bytes(b"synthetic replacement; must not replace startup input\n")
            value, error = call("MoveFileExW", str(hostile), str(path), 1)
            record(name + ".replacement", not value and error in (5, 32), winerror=error)
            record(name + ".bytes_after_attacks", path.read_bytes() == payload)
        for path, destination, label in (
            (lease.directory, ROOT / "inputs-moved", "workspace"),
            (ROOT, ROOT.with_name(ROOT.name + "-moved"), "ancestor"),
        ):
            value, error = call("MoveFileExW", str(path), str(destination), 0)
            record(label + ".rename_denied", not value and error in (5, 32), winerror=error)
        target = ROOT / "reparse-target"
        target.mkdir()
        handle = api.CreateFileW(str(lease.directory), 2, 7, None, 3, 0x02200000, None)
        opened = handle not in (None, ctypes.c_void_p(-1).value)
        record(
            "workspace.reparse_write_data_handle",
            opened,
            winerror=None if opened else ctypes.get_last_error(),
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
            value, error = call(
                "DeviceIoControl",
                handle,
                0x000900A4,
                buffer,
                len(raw),
                None,
                0,
                ctypes.byref(returned),
                None,
            )
            record("workspace.nonempty_reparse_denied", not value and error == 145, winerror=error)
        finally:
            record("workspace.reparse_handle_closed", bool(api.CloseHandle(handle)))
        record(
            "final.full_contract_verified",
            lease.verify_prelaunch()["input_contract_verified"] is True,
        )
        record(
            "final.original_ids_and_hashes_unchanged", lease.private_binding() == original_binding
        )
        record(
            "final.no_com_imports",
            not any(
                name.split(".")[0] in {"pythoncom", "win32com", "comtypes"} for name in sys.modules
            ),
        )
    except BaseException as error:
        result["first_failure"] = {
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
        result["source_after_sha256"] = hashlib.sha256(Path(S.__file__).read_bytes()).hexdigest()
        result["source_unchanged"] = result["source_after_sha256"] == result["source_sha256"]
        result["checks"] = rows
        result["wall_seconds"] = round(time.perf_counter() - started, 3)
        result["passed"] = (
            result["first_failure"] is None
            and result["source_unchanged"]
            and result.get("terminal", {}).get("resources_released") is True
        )
        with (ROOT / "result.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
        print(
            json.dumps(
                {
                    "passed": result["passed"],
                    "checks": len(rows),
                    "first_failure": result["first_failure"],
                    "wall_seconds": result["wall_seconds"],
                }
            )
        )
    return 0 if result["passed"] else 21


if __name__ == "__main__":
    raise SystemExit(main())
