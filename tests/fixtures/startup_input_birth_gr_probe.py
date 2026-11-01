"""One recoverable ordinary-file experiment; no protected-namespace qualification."""

from __future__ import annotations

import ctypes
import hashlib
import json
import sys
from ctypes import wintypes as w
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from startup_input_seal_native import OUTER_SDDL, SEALED_FILE_SDDL, _Win32

from xpedition_cli.startup_input_seal import query_granted_access, safe_api_error

ROOT = Path("C:/ExampleProjects/Task")


def main():
    case, payload = ROOT / "ordinary-08", b"synthetic immutable-from-birth input, no CAD\n"
    if case.exists() or not ROOT.is_dir():
        raise RuntimeError("new_declared_synthetic_case_required")
    api = _Win32()
    api.mkdir(case, OUTER_SDDL)  # Ordinary recoverable parent with explicit 0x40.
    path, writer, metadata, reader, cleanup = case / "input.bin", None, None, None, None
    result = {
        "cad_used": False,
        "com_used": False,
        "namespace_qualified": False,
        "native_seal_enabled": False,
        "cleanup_scope": "ordinary_parent_delete_child",
    }
    phase, function = "create_new_final_gr", "CreateFileW"
    try:
        writer = api.open(path, 0xC0010000, 0, creation=1, sddl=SEALED_FILE_SDDL)
        result["creator_access"] = query_granted_access(writer)
        result["birth_dacl"] = api.dacl(writer)
        phase = "initial_write_flush_full_readback"
        result["actual_readback_sha256"] = api.write_read_hash(writer, payload)
        result["actual_readback_matches_payload"] = (
            result["actual_readback_sha256"] == hashlib.sha256(payload).hexdigest()
        )
        phase = "independent_metadata_keeper"
        metadata = api.open(path, 0x20080, 7)
        result["metadata_access"] = query_granted_access(metadata)
        identity = api.info(metadata)
        result["independent_metadata_same_file"] = identity == api.info(writer)
        function, phase = "CloseHandle", "close_all_data_writer_handles"
        api.close(writer)
        writer = None
        function, phase = "CreateFileW", "sdk_read_share_zero"
        reader = api.open(path, 0x80000000, 0)
        output, count = ctypes.create_string_buffer(len(payload) + 1), w.DWORD()
        function = "ReadFile"
        if not api.k.ReadFile(reader, output, len(payload) + 1, ctypes.byref(count), None):
            raise OSError(ctypes.get_last_error(), "synthetic_sdk_read_failed")
        actual = output.raw[: count.value]
        result["sdk_exclusive_read_same_bytes"] = actual == payload
        result["sdk_exclusive_read_sha256"] = hashlib.sha256(actual).hexdigest()
        result["sdk_reader_same_file"] = api.info(reader) == identity
        api.close(reader)
        reader = None
        phase, function = "terminal_metadata_reopen_delete", "ReOpenFile"
        cleanup = api.k.ReOpenFile(metadata, 0x10080, 7, 0x00200000)
        if cleanup in (None, ctypes.c_void_p(-1).value):
            result["metadata_reopen_delete_error"] = ctypes.get_last_error()
            cleanup = None
            function = "CreateFileW"
            cleanup = api.open(path, 0x10080, 7)
            result["ordinary_parent_dc_cleanup_open"] = True
        else:
            result["metadata_reopen_delete_error"] = 0
        result["cleanup_handle_same_file"] = api.info(cleanup) == identity
        if result["cleanup_handle_same_file"] is not True:
            raise RuntimeError("synthetic_cleanup_file_identity_differs")
        phase, function = "terminal_disposition", "SetFileInformationByHandle"
        deleted = w.BOOL(True)
        if not api.k.SetFileInformationByHandle(
            cleanup, 4, ctypes.byref(deleted), ctypes.sizeof(deleted)
        ):
            raise OSError(ctypes.get_last_error(), "synthetic_disposition_failed")
        result["disposition_ack"] = True
        result["status"] = "observed"
    except BaseException as error:
        result.update(
            status="incomplete", failure_phase=phase, error=safe_api_error(error, function)
        )
    finally:
        for handle in (reader, writer, cleanup, metadata):
            if handle is not None:
                api.close(handle)
        result["own_file_removed"] = not path.exists()
    passed = (
        result.get("actual_readback_matches_payload") is True
        and result.get("independent_metadata_same_file") is True
        and result.get("sdk_exclusive_read_same_bytes") is True
        and result.get("sdk_reader_same_file") is True
        and result.get("cleanup_handle_same_file") is True
        and result.get("disposition_ack") is True
        and result["own_file_removed"]
    )
    result["ordinary_file_steps_passed"] = bool(passed)
    with (ROOT / "ordinary08-result.json").open("x", encoding="utf-8") as record:
        record.write(json.dumps(result, sort_keys=True))
    print(json.dumps(result, sort_keys=True))
    return 0 if passed else 21


if __name__ == "__main__":
    raise SystemExit(main())
