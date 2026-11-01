"""Explicit new retained11 filesystem probe. Never imports COM or touches CAD.

Prepare/review first. Execution requires both frozen source hashes on argv.
Only this new case is writable; retained input files are never cleanup targets.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import types
from ctypes import wintypes as w
from pathlib import Path

ROOT = Path("C:/ExampleProjects/Task")
CASE = ROOT / "case-retained11"
MOVED_CASE = ROOT / "case-retained11-unexpected-move"
OUTER_SDDL = "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)"
MAX_SOURCE = 1024 * 1024
PAYLOADS = {
    "identity.vbs": b"Option Explicit\n' Synthetic read-only input; never executed.\n",
    "scripts.ini": b"[ViewDraw]\nScript#0=identity.vbs\n",
}


class ProbeFailure(ValueError):
    pass


def denial_oracle(operation, succeeded, error):
    combined = {
        "integrity.source_move",
        "integrity.replace_existing",
        "integrity.hardlink",
        "inputs.rename",
        "new_case.rename",
    }
    acl_only = {"inputs.directory_write_attr", "inputs.directory_write_ea"}
    acl_only |= {
        name + suffix
        for name in PAYLOADS
        for suffix in (
            ".acl_write",
            ".acl_append",
            ".acl_truncate",
            ".acl_delete_access",
            ".acl_write_dac",
            ".acl_write_owner",
            ".DeleteFile",
        )
    }
    allowed = (
        {5, 32}
        if operation in combined
        else {5}
        if operation in acl_only
        else ({32} if operation == "inputs.directory_write_data" else set())
    )
    return {
        "passed": succeeded is False and type(error) is int and error in allowed,
        "allowed_winerrors": sorted(allowed),
        "actual_denial": "acl_access_denied"
        if not succeeded and error == 5
        else ("sharing_violation" if not succeeded and error == 32 else "unexpected_result"),
    }


def unchanged_acceptance(matrix, post_read, parent_verify):
    return matrix is True and post_read is True and parent_verify is True


def _new_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _source(path, expected):
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ProbeFailure("expected_source_sha_required")
    with path.open("rb") as stream:
        raw = stream.read(MAX_SOURCE + 1)
    if len(raw) > MAX_SOURCE or _sha(raw) != expected:
        raise ProbeFailure("source_byte_binding_mismatch")
    return raw


class Info(ctypes.Structure):
    _fields_ = [
        ("attributes", w.DWORD),
        ("created", w.FILETIME),
        ("accessed", w.FILETIME),
        ("written", w.FILETIME),
        ("volume", w.DWORD),
        ("size_high", w.DWORD),
        ("size_low", w.DWORD),
        ("links", w.DWORD),
        ("index_high", w.DWORD),
        ("index_low", w.DWORD),
    ]


class Api:
    def __init__(self):
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.calls, self.elapsed_ns = 0, 0
        for name, args, result in (
            (
                "CreateFileA",
                (w.LPCSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE),
                w.HANDLE,
            ),
            (
                "CreateFileW",
                (w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE),
                w.HANDLE,
            ),
            (
                "ReadFile",
                (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p),
                w.BOOL,
            ),
            (
                "WriteFile",
                (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p),
                w.BOOL,
            ),
            ("GetFileInformationByHandle", (w.HANDLE, ctypes.POINTER(Info)), w.BOOL),
            ("FlushFileBuffers", (w.HANDLE,), w.BOOL),
            ("CloseHandle", (w.HANDLE,), w.BOOL),
            ("DeleteFileW", (w.LPCWSTR,), w.BOOL),
            ("MoveFileExW", (w.LPCWSTR, w.LPCWSTR, w.DWORD), w.BOOL),
            ("CreateHardLinkW", (w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p), w.BOOL),
            ("CreateDirectoryW", (w.LPCWSTR, ctypes.c_void_p), w.BOOL),
            ("RemoveDirectoryW", (w.LPCWSTR,), w.BOOL),
        ):
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result

    def call(self, name, *args):
        ctypes.set_last_error(0)
        started = time.perf_counter_ns()
        try:
            value = getattr(self.k, name)(*args)
            return value, ctypes.get_last_error()
        finally:
            self.calls += 1
            self.elapsed_ns += time.perf_counter_ns() - started

    @property
    def elapsed_ms(self):
        return self.elapsed_ns / 1_000_000

    def opened(self, value):
        return value not in (None, ctypes.c_void_p(-1).value)

    def close(self, handle):
        value, error = self.call("CloseHandle", handle)
        if not value:
            raise ProbeFailure("own_handle_close_failed_" + str(error))


def create_product_case(facts):
    """Create only the fresh case with the product outer-directory birth ACL."""

    class Security(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]

    api = Api()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes, convert.restype = (
        (w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p),
        w.BOOL,
    )
    api.k.LocalFree.argtypes, api.k.LocalFree.restype = (ctypes.c_void_p,), ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    if not convert(OUTER_SDDL, 1, ctypes.byref(descriptor), None):
        raise ProbeFailure("outer_security_descriptor_failed")
    try:
        security = Security(ctypes.sizeof(Security), descriptor, False)
        value, error = api.call("CreateDirectoryW", str(CASE), ctypes.byref(security))
        if not value:
            raise ProbeFailure("new_outer_directory_failed_" + str(error))
        facts.update(created=True, requested_sddl=OUTER_SDDL)
    finally:
        api.k.LocalFree(descriptor)


def observe_outer_dc(module):
    """Observe the actual DACL and a new DC-granted handle, then close it."""
    import win32security

    api = Api()
    handle, error = api.call("CreateFileW", str(CASE), 0x200C0, 7, None, 3, 0x02000000, None)
    if not api.opened(handle):
        raise ProbeFailure("outer_delete_child_open_failed_" + str(error))
    try:
        granted = module.query_granted_access(handle)
        descriptor = win32security.GetSecurityInfo(
            handle, win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
        )
        dacl = descriptor.GetSecurityDescriptorDacl()
        actual = []
        for index in range(dacl.GetAceCount()):
            (kind, flags), mask, sid = dacl.GetAce(index)
            sid = win32security.ConvertSidToStringSid(sid)
            actual.append((kind, flags, mask, sid))
        required = {(0, 3, 0x1F01FF, "S-1-3-4"), (0, 3, 0x1F01FF, "S-1-5-18")}
        protected = bool(descriptor.GetSecurityDescriptorControl()[0] & 0x1000)
        dacl_exact = len(actual) == 2 and set(actual) == required and protected
        has_dc = granted.get("status") == "observed" and bool(
            granted.get("granted_access", 0) & 0x40
        )
        if not dacl_exact or not has_dc:
            raise ProbeFailure("outer_actual_delete_child_proof_incomplete")
        return {
            "requested_sddl": OUTER_SDDL,
            "actual_dacl_exact": dacl_exact,
            "actual_handle_access": granted,
            "file_delete_child_granted": has_dc,
            "temporary_dc_handle_closed_before_inputs_prepare": True,
        }
    finally:
        api.close(handle)


def child(case, expected_fixture):
    if case != CASE or os.name != "nt":
        raise ProbeFailure("new_declared_case_only")
    _source(Path(__file__), expected_fixture)
    expected = json.loads((case / "probe-input.json").read_text(encoding="utf-8"))
    api, started, records = Api(), time.perf_counter_ns(), []
    evidence_root = [case]
    phase = "helper.initialized"
    first_failure = None

    def record(name, success, **facts):
        row = {
            "phase": name,
            "passed": success,
            **facts,
            "api_calls": api.calls,
            "cumulative_api_ns": api.elapsed_ns,
            "cumulative_api_ms": round(api.elapsed_ms, 3),
        }
        records.append(row)
        with (evidence_root[0] / "helper-observations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
        if not success:
            raise ProbeFailure("first_assertion_failed")

    def denied(name, succeeded, error, **facts):
        decision = denial_oracle(name, succeeded, error)
        passed = decision.pop("passed")
        record(name, passed, winerror=0 if succeeded else error, **decision, **facts)

    try:
        for name, payload in PAYLOADS.items():
            path = case / "inputs" / name
            phase = name + ".sdk_read_A_share_zero"
            handle, error = api.call(
                "CreateFileA", str(path).encode("ascii"), 0x80000000, 0, None, 3, 0x80, None
            )
            record(
                phase + ".open", api.opened(handle), winerror=error if not api.opened(handle) else 0
            )
            try:
                info, count = Info(), w.DWORD()
                output = ctypes.create_string_buffer(len(payload) + 1)
                value, error = api.call(
                    "ReadFile", handle, output, len(payload) + 1, ctypes.byref(count), None
                )
                record(
                    phase + ".bytes",
                    bool(value) and output.raw[: count.value] == payload,
                    winerror=error if not value else 0,
                )
                value, error = api.call("GetFileInformationByHandle", handle, ctypes.byref(info))
                identity = [int(info.volume), (int(info.index_high) << 32) | int(info.index_low)]
                record(
                    phase + ".identity",
                    bool(value) and identity == expected["files"][name]["identity"]["file_id"],
                    winerror=error if not value else 0,
                )
            finally:
                api.close(handle)
            for label, access, disposition in (
                ("write", 0x40000000, 3),
                ("append", 4, 3),
                ("truncate", 0x40000000, 5),
                ("delete_access", 0x10000, 3),
                ("write_dac", 0x40000, 3),
                ("write_owner", 0x80000, 3),
            ):
                phase = name + ".acl_" + label
                handle, error = api.call(
                    "CreateFileW", str(path), access, 7, None, disposition, 0x80, None
                )
                opened = api.opened(handle)
                if opened:
                    api.close(handle)
                denied(phase, opened, error)
            phase = name + ".DeleteFile"
            value, error = api.call("DeleteFileW", str(path))
            denied(phase, bool(value), error)
        for label, access in (
            ("write_data", 2),
            ("write_attr", 0x100),
            ("write_ea", 0x10),
        ):
            phase = "inputs.directory_" + label
            handle, error = api.call(
                "CreateFileW", str(case / "inputs"), access, 7, None, 3, 0x02000000, None
            )
            opened = api.opened(handle)
            if opened:
                api.close(handle)
            denied(phase, opened, error)
        phase = "cache.create_write_normal_cleanup"
        cache = case / "inputs" / "probe-cache"
        value, error = api.call("CreateDirectoryW", str(cache), None)
        record(phase + ".mkdir", bool(value), winerror=0 if value else error)
        handle, error = api.call(
            "CreateFileW", str(cache / "cache.bin"), 0x40000000, 0, None, 1, 0x80, None
        )
        record(phase + ".open", api.opened(handle), winerror=0 if api.opened(handle) else error)
        try:
            raw, count = b"new synthetic mutable cache\n", w.DWORD()
            value, error = api.call("WriteFile", handle, raw, len(raw), ctypes.byref(count), None)
            record(
                phase + ".write",
                bool(value) and count.value == len(raw),
                winerror=0 if value else error,
            )
            value, error = api.call("FlushFileBuffers", handle)
            record(phase + ".flush", bool(value), winerror=0 if value else error)
        finally:
            api.close(handle)
        value, error = api.call("DeleteFileW", str(cache / "cache.bin"))
        record(phase + ".delete_cache_file", bool(value), winerror=0 if value else error)
        value, error = api.call("RemoveDirectoryW", str(cache))
        record(phase + ".remove_cache_dir", bool(value), winerror=0 if value else error)
        original = case / "inputs" / "identity.vbs"
        phase = "integrity.source_move"
        value, error = api.call(
            "MoveFileExW", str(original), str(case / "unused-input-alias.vbs"), 0
        )
        denied(phase, bool(value), error)
        phase = "integrity.prepare_replacement"
        replacement = case / "replacement.bin"
        handle, error = api.call(
            "CreateFileW", str(replacement), 0x40000000, 0, None, 1, 0x80, None
        )
        record(phase + ".open", api.opened(handle), winerror=0 if api.opened(handle) else error)
        try:
            raw, count = b"synthetic replacement must not enter inputs\n", w.DWORD()
            value, error = api.call("WriteFile", handle, raw, len(raw), ctypes.byref(count), None)
            record(
                phase + ".write",
                bool(value) and count.value == len(raw),
                winerror=0 if value else error,
            )
        finally:
            api.close(handle)
        phase = "integrity.replace_existing"
        value, error = api.call("MoveFileExW", str(replacement), str(original), 1)
        denied(phase, bool(value), error)
        phase = "integrity.hardlink"
        value, error = api.call(
            "CreateHardLinkW", str(case / "unused-hardlink.vbs"), str(original), None
        )
        links = None
        if value:
            # Observe the unexpected alias effect without removing/restoring it.
            handle, query_error = api.call(
                "CreateFileW", str(original), 0x80, 7, None, 3, 0x80, None
            )
            if api.opened(handle):
                try:
                    info = Info()
                    known, _ = api.call("GetFileInformationByHandle", handle, ctypes.byref(info))
                    links = int(info.links) if known else None
                finally:
                    api.close(handle)
        denied(phase, bool(value), error, link_count_after_unexpected_success=links)
        # Only the two newly owned directories, never ROOT or any older case.
        for path in (case / "inputs", case):
            phase = "inputs.rename" if path.name == "inputs" else "new_case.rename"
            value, error = api.call(
                "MoveFileExW", str(path), str(path.with_name(path.name + "-unexpected-move")), 0
            )
            if value and path == case:
                # Keep failure evidence at the moved new case; never restore it.
                evidence_root[0] = MOVED_CASE
            denied(phase, bool(value), error)
    except BaseException as error:
        first_failure = {
            "phase": phase,
            "error_type": type(error).__name__,
            "last_observation": records[-1] if records else None,
        }
    result = {
        "status": "observed" if first_failure is None else "incomplete",
        "matrix_checks_pass": first_failure is None,
        "minimum_probe_pass": False,
        "independent_post_read_required": True,
        "first_failure": first_failure,
        "observations": records,
        "api_calls": api.calls,
        "cumulative_api_ns": api.elapsed_ns,
        "cumulative_api_ms": round(api.elapsed_ms, 3),
        "wall_ns": time.perf_counter_ns() - started,
        "wall_ms": round((time.perf_counter_ns() - started) / 1_000_000, 3),
        "unobserved": [
            name
            for name in (
                "cache.create_write_normal_cleanup.remove_cache_dir",
                "integrity.source_move",
                "integrity.replace_existing",
                "integrity.hardlink",
                "inputs.rename",
                "new_case.rename",
            )
            if not any(row["phase"] == name for row in records)
        ],
        "full_namespace_matrix_complete": False,
        "cad_used": False,
        "com_used": False,
        "restoration_attempted": False,
    }
    _new_json(evidence_root[0] / "helper-result.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "observations"}, sort_keys=True))
    return 0 if result["matrix_checks_pass"] else 21


def readback_child(expected_fixture):
    """A second fresh process reopens both names after every negative case."""
    _source(Path(__file__), expected_fixture)
    expected = json.loads((CASE / "probe-input.json").read_text(encoding="utf-8"))
    api, started, records, failure = Api(), time.perf_counter_ns(), [], None
    phase = "postread.started"

    def record(success, **facts):
        row = {
            "phase": phase,
            "passed": success,
            "api_calls": api.calls,
            "cumulative_api_ns": api.elapsed_ns,
            **facts,
        }
        records.append(row)
        with (CASE / "postread-observations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
        if not success:
            raise ProbeFailure("independent_postread_changed_or_incomplete")

    try:
        for name, payload in PAYLOADS.items():
            phase = name + ".post_sdk_share_zero.open"
            handle, error = api.call(
                "CreateFileA",
                str(CASE / "inputs" / name).encode("ascii"),
                0x80000000,
                0,
                None,
                3,
                0x80,
                None,
            )
            record(api.opened(handle), winerror=0 if api.opened(handle) else error)
            try:
                phase = name + ".post_sdk_share_zero.bytes"
                output, count = ctypes.create_string_buffer(len(payload) + 1), w.DWORD()
                value, error = api.call(
                    "ReadFile", handle, output, len(payload) + 1, ctypes.byref(count), None
                )
                record(
                    bool(value) and output.raw[: count.value] == payload,
                    winerror=0 if value else error,
                )
                phase = name + ".post_sdk_share_zero.file_id_and_links"
                info = Info()
                value, error = api.call("GetFileInformationByHandle", handle, ctypes.byref(info))
                identity = [int(info.volume), (int(info.index_high) << 32) | int(info.index_low)]
                original = expected["files"][name]["identity"]
                record(
                    bool(value)
                    and identity == original["file_id"]
                    and info.links == original["links"],
                    winerror=0 if value else error,
                )
            finally:
                api.close(handle)
    except BaseException as error:
        failure = {
            "phase": phase,
            "error_type": type(error).__name__,
            "last_observation": records[-1] if records else None,
        }
    result = {
        "post_read_verified": failure is None,
        "first_failure": failure,
        "observations": records,
        "api_calls": api.calls,
        "cumulative_api_ns": api.elapsed_ns,
        "wall_ns": time.perf_counter_ns() - started,
        "cad_used": False,
        "com_used": False,
    }
    _new_json(CASE / "postread-result.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "observations"}, sort_keys=True))
    return 0 if failure is None else 21


def parent(args):
    started, phase, lease, created = time.perf_counter_ns(), "source.capture", None, False
    result = {
        "status": "incomplete",
        "cad_used": False,
        "com_used": False,
        "namespace_native_qualified": False,
        "account_wide_quota": "not_implemented",
        "retained_inputs_cleanup": "permanently_retained_read_only_no_cleanup_promise",
        "helper_normal_exit_zero": None,
        "helper_forced_termination": False,
        "post_read_verified": None,
        "postread_normal_exit_zero": None,
        "api_calls": None,
        "cumulative_api_ms": None,
        "cumulative_api_ns": None,
        "case_creation": {"created": False},
        "verification_after_helper": None,
        "terminal_facts": None,
        "first_failure": None,
    }
    helper = None
    try:
        raw = _source(Path(args.seal_source), args.seal_sha256)
        fixture = _source(Path(__file__), args.fixture_sha256)
        if CASE.exists() or MOVED_CASE.exists() or not ROOT.is_dir():
            raise ProbeFailure("new_declared_case_required")
        create_product_case(result["case_creation"])
        created = True
        source = CASE / "source"
        source.mkdir()
        (source / "seal.py").write_bytes(raw)
        (source / "startup_inputs_retained_probe.py").write_bytes(fixture)
        _new_json(
            CASE / "source-binding.json",
            {
                "seal_sha256": _sha(raw),
                "fixture_sha256": _sha(fixture),
                "seal_bytes": len(raw),
                "fixture_bytes": len(fixture),
            },
        )
        module = types.ModuleType("retained11_frozen_seal")
        module.__file__ = str(source / "seal.py")
        sys.modules[module.__name__] = module
        exec(compile(raw, module.__file__, "exec"), module.__dict__)
        phase = "case.actual_delete_child_observation"
        result["case_parent_proof"] = observe_outer_dc(module)
        phase = "lease.prepare"
        lease = module.InputsLease(CASE / "inputs")  # Retain owner before native preparation.
        lease.prepare(PAYLOADS)
        prepared = lease.verify_prelaunch()
        if prepared.get("input_contract_verified") is not True:
            raise ProbeFailure("prepared_contract_not_verified")
        _new_json(CASE / "probe-input.json", lease.private_binding())
        phase = "independent_helper"
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.upper().startswith(("XPEDITION_", "PYTHON"))
        }
        env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        helper = subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(source / "startup_inputs_retained_probe.py"),
                "--child",
                "--fixture-sha256",
                _sha(fixture),
            ],
            cwd=ROOT,  # A CASE CWD would itself confound its ancestor-rename test.
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        helper_started = time.perf_counter_ns()
        try:
            stdout, stderr = helper.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            result["helper_forced_termination"] = True
            helper.kill()  # Only this exact new non-CAD Popen HANDLE; never CAD/PID lookup.
            stdout, stderr = helper.communicate(
                timeout=max(0.01, 10 - (time.perf_counter_ns() - helper_started) / 1_000_000_000)
            )
            output_root = CASE if CASE.is_dir() else MOVED_CASE
            (output_root / "helper-stdout.txt").write_bytes(stdout)
            (output_root / "helper-stderr.txt").write_bytes(stderr)
            raise ProbeFailure("helper_budget_exceeded_forced_nonCAD_teardown") from None
        result["helper_wall_ns"] = time.perf_counter_ns() - helper_started
        result["helper_wall_ms"] = round(result["helper_wall_ns"] / 1_000_000, 3)
        output_root = CASE if CASE.is_dir() else MOVED_CASE
        (output_root / "helper-stdout.txt").write_bytes(stdout)
        (output_root / "helper-stderr.txt").write_bytes(stderr)
        result["helper_normal_exit_zero"] = helper.returncode == 0
        result["helper_result"] = json.loads(
            (output_root / "helper-result.json").read_text(encoding="utf-8")
        )
        result["api_calls"] = result["helper_result"].get("api_calls")
        result["cumulative_api_ms"] = result["helper_result"].get("cumulative_api_ms")
        result["cumulative_api_ns"] = result["helper_result"].get("cumulative_api_ns")
        if helper.returncode != 0 or result["helper_result"].get("matrix_checks_pass") is not True:
            result["first_failure"] = result["helper_result"].get("first_failure")
            raise ProbeFailure("independent_helper_first_failure")
        if result["helper_wall_ms"] > 10000:
            raise ProbeFailure("helper_wall_budget_exceeded_after_return")
        phase = "independent_postread_helper"
        helper = subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(source / "startup_inputs_retained_probe.py"),
                "--child",
                "--readback-only",
                "--fixture-sha256",
                _sha(fixture),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        postread_started = time.perf_counter_ns()
        try:
            stdout, stderr = helper.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            result["helper_forced_termination"] = True
            helper.kill()  # Only this new, non-CAD original process handle.
            stdout, stderr = helper.communicate(
                timeout=max(0.01, 10 - (time.perf_counter_ns() - postread_started) / 1e9)
            )
            (CASE / "postread-stdout.txt").write_bytes(stdout)
            (CASE / "postread-stderr.txt").write_bytes(stderr)
            raise ProbeFailure("postread_budget_exceeded_forced_nonCAD_teardown") from None
        (CASE / "postread-stdout.txt").write_bytes(stdout)
        (CASE / "postread-stderr.txt").write_bytes(stderr)
        result["postread_helper_wall_ns"] = time.perf_counter_ns() - postread_started
        result["postread_normal_exit_zero"] = helper.returncode == 0
        result["postread_result"] = json.loads(
            (CASE / "postread-result.json").read_text(encoding="utf-8")
        )
        result["post_read_verified"] = result["postread_result"].get("post_read_verified")
        if helper.returncode != 0 or result["post_read_verified"] is not True:
            result["first_failure"] = result["postread_result"].get("first_failure")
            raise ProbeFailure("independent_postread_failed")
        if result["postread_helper_wall_ns"] > 10_000_000_000:
            raise ProbeFailure("postread_wall_budget_exceeded_after_return")
        phase = "lease.verify_after_helper"
        result["verification_after_helper"] = lease.verify_prelaunch().get(
            "input_contract_verified"
        )
        if result["verification_after_helper"] is not True:
            raise ProbeFailure("retained_contract_changed")
        result["status"] = "observed"
    except BaseException as error:
        failure = {
            "phase": phase,
            "error_type": type(error).__name__,
            "reason": str(error) if re.fullmatch(r"[a-z][A-Za-z0-9_]{0,100}", str(error)) else None,
            "winerror": getattr(error, "winerror", None)
            if type(getattr(error, "winerror", None)) is int
            else None,
        }
        if result["first_failure"] is None:
            result["first_failure"] = failure
        else:
            result["parent_failure_observation"] = failure
    finally:
        if lease is not None and (helper is None or helper.poll() is not None):
            try:
                result["terminal_facts"] = lease.terminal_release()
            except BaseException as error:
                result["terminal_release_error_type"] = type(error).__name__
        terminal = result["terminal_facts"] or {}
        result["terminal_three_facts_pass"] = all(
            (
                terminal.get("resources_released") is True,
                terminal.get("files_removed") is False,
                terminal.get("immutable_files_retained") is True,
            )
        )
        result["minimum_probe_pass"] = (
            result["status"] == "observed"
            and result["helper_normal_exit_zero"] is True
            and result["postread_normal_exit_zero"] is True
            and unchanged_acceptance(
                result.get("helper_result", {}).get("matrix_checks_pass"),
                result["post_read_verified"],
                result["verification_after_helper"],
            )
            and result["terminal_three_facts_pass"]
            and not result["helper_forced_termination"]
        )
        result["wall_ns"] = time.perf_counter_ns() - started
        result["wall_ms"] = round(result["wall_ns"] / 1_000_000, 3)
        result["source_sha256"] = args.seal_sha256
        result["fixture_sha256"] = args.fixture_sha256
        if created or result["case_creation"].get("created"):
            output_root = CASE if CASE.is_dir() else MOVED_CASE
            _new_json(output_root / "result.json", result)
        print(json.dumps(result, sort_keys=True))
    return 0 if result["minimum_probe_pass"] else 21


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--readback-only", action="store_true")
    parser.add_argument("--seal-source")
    parser.add_argument("--seal-sha256")
    parser.add_argument("--fixture-sha256", required=True)
    args = parser.parse_args()
    if args.child:
        if args.readback_only:
            return readback_child(args.fixture_sha256)
        return child(CASE, args.fixture_sha256)
    if not args.seal_source or not args.seal_sha256:
        parser.error("parent requires --seal-source and --seal-sha256")
    return parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
