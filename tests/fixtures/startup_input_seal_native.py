"""Historical rejected WRITE_DAC-keeper experiment; never a product lease.

An owner-rights ACE deliberately removes the owner's implicit WRITE_DAC. A
narrow security-only handle retained from CREATE_NEW is the cleanup capability.
No data handle is kept across the SDK's exclusive read. Losing the last keeper
may orphan a sealed input; this module never takes ownership or enables privilege.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from contextlib import contextmanager
from ctypes import wintypes as w
from pathlib import Path

from xpedition_cli.startup_input_seal import evaluate_probe, query_granted_access, safe_api_error

PARENT_SDDL = "D:P(A;;GRGWGX;;;OW)"
OUTER_SDDL = "D:P(A;;0xe0010040;;;OW)"  # GR|GW|GX|DELETE|FILE_DELETE_CHILD.
INITIAL_FILE_SDDL = "D:P(A;;GRGW;;;OW)"
SEALED_FILE_SDDL = "D:P(A;;GR;;;OW)"
CLEANUP_FILE_SDDL = "D:P(A;;GRSD;;;OW)"
KEEPER_ACCESS = 0x00020000 | 0x00040000 | 0x80  # RC | WD | RATTR; no data/DELETE.
DIRECTORY_PIN_ACCESS = 0x00010000 | 0x80  # DELETE | RATTR, no WRITE_DAC/OWNER.
MAX_INPUT_BYTES = 65536


class SealError(ValueError):
    """Fixed reason only; a failed lease remains the owner's responsibility."""


class _Info(ctypes.Structure):
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


class _Security(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]


class _Win32:
    def __init__(self):
        if os.name != "nt":
            raise SealError("startup_input_seal_requires_windows")
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        for name, args, result in (
            ("CreateDirectoryW", (w.LPCWSTR, ctypes.POINTER(_Security)), w.BOOL),
            (
                "CreateFileW",
                (
                    w.LPCWSTR,
                    w.DWORD,
                    w.DWORD,
                    ctypes.POINTER(_Security),
                    w.DWORD,
                    w.DWORD,
                    w.HANDLE,
                ),
                w.HANDLE,
            ),
            ("ReOpenFile", (w.HANDLE, w.DWORD, w.DWORD, w.DWORD), w.HANDLE),
            ("CloseHandle", (w.HANDLE,), w.BOOL),
            ("GetFileInformationByHandle", (w.HANDLE, ctypes.POINTER(_Info)), w.BOOL),
            (
                "WriteFile",
                (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p),
                w.BOOL,
            ),
            (
                "ReadFile",
                (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p),
                w.BOOL,
            ),
            ("FlushFileBuffers", (w.HANDLE,), w.BOOL),
            (
                "SetFilePointerEx",
                (w.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), w.DWORD),
                w.BOOL,
            ),
            (
                "SetFileInformationByHandle",
                (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD),
                w.BOOL,
            ),
            ("DeleteFileW", (w.LPCWSTR,), w.BOOL),
            ("MoveFileExW", (w.LPCWSTR, w.LPCWSTR, w.DWORD), w.BOOL),
            ("CreateHardLinkW", (w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p), w.BOOL),
            ("LocalFree", (ctypes.c_void_p,), ctypes.c_void_p),
        ):
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result
        convert = self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes, convert.restype = (
            (w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p),
            w.BOOL,
        )

    @contextmanager
    def security(self, sddl):
        pointer = ctypes.c_void_p()
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(pointer), None
        ):
            raise OSError(ctypes.get_last_error(), "seal_security_descriptor_failed")
        try:
            yield _Security(ctypes.sizeof(_Security), pointer, False)
        finally:
            self.k.LocalFree(pointer)

    def mkdir(self, path, sddl):
        with self.security(sddl) as security:
            if not self.k.CreateDirectoryW(str(path), ctypes.byref(security)):
                raise OSError(ctypes.get_last_error(), "seal_new_directory_failed")

    def open(self, path, access, share=7, *, creation=3, flags=0, sddl=None):
        def call(security):
            handle = self.k.CreateFileW(str(path), access, share, security, creation, flags, None)
            if handle in (None, ctypes.c_void_p(-1).value):
                raise OSError(ctypes.get_last_error(), "seal_file_open_failed")
            return handle

        if sddl is None:
            return call(None)
        with self.security(sddl) as security:
            return call(ctypes.byref(security))

    def close(self, handle):
        if handle is not None and not self.k.CloseHandle(handle):
            raise OSError(ctypes.get_last_error(), "seal_handle_close_failed")

    def info(self, handle):
        info = _Info()
        if not self.k.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), "seal_file_identity_unobservable")
        return {
            "file_id": (int(info.volume), (int(info.index_high) << 32) | int(info.index_low)),
            "bytes": (int(info.size_high) << 32) | int(info.size_low),
            "links": int(info.links),
            "attributes": int(info.attributes),
        }

    def set_dacl(self, handle, sddl):
        import win32security

        descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1)
        win32security.SetSecurityInfo(
            handle,
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            descriptor.GetSecurityDescriptorDacl(),
            None,
        )

    def dacl(self, handle):
        import win32security

        descriptor = win32security.GetSecurityInfo(
            handle, win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
        )
        return win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
            descriptor, 1, win32security.DACL_SECURITY_INFORMATION
        )

    def write_read_hash(self, writer, payload):
        written = w.DWORD()
        if not self.k.WriteFile(
            writer, payload, len(payload), ctypes.byref(written), None
        ) or written.value != len(payload):
            raise SealError("startup_input_write_incomplete")
        if not self.k.FlushFileBuffers(writer):
            raise SealError("startup_input_flush_incomplete")
        if not self.k.SetFilePointerEx(writer, 0, None, 0):
            raise SealError("startup_input_readback_seek_failed")
        output, read = ctypes.create_string_buffer(len(payload) + 1), w.DWORD()
        if not self.k.ReadFile(writer, output, len(payload) + 1, ctypes.byref(read), None):
            raise SealError("startup_input_readback_failed")
        observed = output.raw[: read.value]
        if observed != payload:
            raise SealError("startup_input_readback_differs")
        return hashlib.sha256(observed).hexdigest()


def _foundation_probe(root, *, case_name="foundation", create_root=True):
    """One new synthetic case, never a product permission/qualification flag."""
    root = Path(root)
    if create_root:
        if root.exists():
            raise SealError("new_synthetic_root_required")
        root.mkdir()
    elif str(root).replace("\\", "/").casefold() != "c:/exampleprojects/task" or not root.is_dir():
        raise SealError("declared_synthetic_root_required")
    case = root / case_name
    parent, payload = case / "inputs", b"synthetic startup input, no CAD or credentials\n"
    api = _Win32()
    api.mkdir(case, OUTER_SDDL)
    api.mkdir(parent, PARENT_SDDL)
    directory = api.open(parent, DIRECTORY_PIN_ACCESS, 3, flags=0x02200000)
    keeper = writer = None
    result = {
        "outer_creation_sddl": OUTER_SDDL,
        "cad_used": False,
        "com_used": False,
        "creation_keeper_access": KEEPER_ACCESS,
        "directory_pin_access": DIRECTORY_PIN_ACCESS,
        "file_flags": 0,
    }
    try:
        keeper = api.open(
            parent / "identity.vbs", KEEPER_ACCESS, creation=1, sddl=INITIAL_FILE_SDDL
        )
        result["security_only_create_new_succeeded"] = True
        granted = query_granted_access(keeper)
        result["keeper_access_observation"] = granted
        result["keeper_granted_access"] = granted["granted_access"]
        if granted["status"] != "observed" or not granted["granted_access"] & 0x40000:
            raise SealError("keeper_write_dac_not_granted")
        result["birth_dacl"] = api.dacl(keeper)
        writer = api.open(parent / "identity.vbs", 0xC0000000, 0)
        result["independent_exclusive_writer_succeeded"] = True
        result["readback_sha256"] = api.write_read_hash(writer, payload)
        result["actual_full_readback_hash_verified"] = True
        result["before_seal"] = api.info(keeper)
        api.set_dacl(keeper, SEALED_FILE_SDDL)
        result["seal_using_original_keeper_succeeded"] = True
        result["sealed_dacl"] = api.dacl(keeper)
        api.close(writer)
        writer = None
        result["data_handles_closed"] = True
        reader = api.open(parent / "identity.vbs", 0x80000000, 0)
        api.close(reader)
        result["sdk_style_read_share_zero_succeeded"] = True
        for label, access in (
            ("write", 0x40000000),
            ("delete", 0x10000),
            ("write_dac", 0x40000),
            ("write_owner", 0x80000),
        ):
            try:
                unauthorized = api.open(parent / "identity.vbs", access)
            except OSError as error:
                result[label + "_reopen_error"] = error.errno
            else:
                result[label + "_reopen_error"] = 0
                api.close(unauthorized)
        result["status"] = "observed"
    except BaseException as error:
        result.update(
            status="incomplete",
            error_type=type(error).__name__,
            error=safe_api_error(error, "SetSecurityInfo"),
        )
    finally:
        if writer is not None:
            api.close(writer)
        # First proof retains its keeper in this helper only until safe terminal
        # readback of the proof. Cleanup is deliberately measured separately.
        if keeper is not None:
            try:
                api.set_dacl(keeper, CLEANUP_FILE_SDDL)
                result["terminal_delete_grant_from_keeper_succeeded"] = True
            except BaseException:
                result["terminal_delete_grant_from_keeper_succeeded"] = False
                import json
                import threading

                result.update(evaluate_probe(result))
                try:
                    with (case / "incomplete.json").open("x", encoding="utf-8") as record:
                        record.write(json.dumps(result, sort_keys=True))
                except OSError:
                    pass
                threading.Event().wait()  # Preserve the only cleanup capability.
            api.close(keeper)
        api.close(directory)
    result.update(evaluate_probe(result))
    return result
