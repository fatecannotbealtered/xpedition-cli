"""Source-only, non-CAD prototype of an operation-owned executor lifecycle.

No CAD operation, old operation ID, dynamic handler, shell, service or native
adapter can be dispatched here. A bounded caller observation never owns the
executor's resources. Later child observations cannot revise the immutable
initial unknown observation or qualify any CAD operation.

Caller exit survival is tested only for these source fixtures. Windows job
termination, logoff or reboot can still lose an owner and must remain unresolved.
Frozen/onefile execution is refused: independent bundle lifetime is unqualified.
Only plain local fixed-drive runtime paths are accepted. Observation budgets
bound polling, not the latency of uncancellable filesystem or kernel queries.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import hmac
import itertools
import json
import math
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from ctypes import wintypes as w
from pathlib import Path

COMPONENT = "xpedition-offline-executor-prototype-v1"
SCENARIOS = (
    "block_until_fixture_release",
    "callback_until_fixture_release",
    "return_immediately",
    "cleanup_failure",
    "no_permit",
)
MAX_OWNERS = 2
MAX_EXECUTIONS = 8
MAX_EVENTS = 64
MAX_RECORD_BYTES = 8192
PERMIT_WAIT_SECONDS = 1.5
EXECUTION_RE = re.compile(r"exec-[0-9a-f]{32}\Z")


def native_execution_enabled() -> bool:
    """Pure fail-closed gate: this prototype has no production/native executor."""
    return False


class LifecycleError(ValueError):
    """Fixed local reason; never prints request contents or external exceptions."""


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _plain_path(value):
    path = Path(value)
    # Lexical rejection precedes all filesystem/kernel work, including exists().
    if (
        os.name != "nt"
        or not re.fullmatch(r"[A-Za-z]:", path.drive)
        or not path.is_absolute()
        or ".." in path.parts
    ):
        raise LifecycleError("plain_local_windows_runtime_root_required")
    api = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    api.argtypes, api.restype = (w.LPCWSTR,), w.UINT
    if int(api(path.drive + "\\")) != 3:  # DRIVE_FIXED only; no mapped remote volume.
        raise LifecycleError("runtime_local_fixed_drive_required")
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()  # Inspect ancestors before visiting descendants.
        except FileNotFoundError:
            break
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise LifecycleError("runtime_reparse_path_refused")
    return Path(os.path.normpath(str(path)))


def _locator(root):
    return hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()


def _write_new(path, data):
    if len(data) > MAX_RECORD_BYTES:
        raise LifecycleError("record_byte_limit")
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _pack(payload, key):
    return _canonical(
        {"payload": payload, "mac": hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()}
    )


class _SnapshotFileInfo(ctypes.Structure):
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


class _SnapshotRenameInfo(ctypes.Structure):
    _fields_ = [
        ("flags", w.DWORD),
        ("root", w.HANDLE),
        ("name_bytes", w.DWORD),
        ("name", w.WCHAR * 1),
    ]


class _SnapshotStatusValue(ctypes.Union):
    _fields_ = [("status", w.LONG), ("pointer", ctypes.c_void_p)]


class _SnapshotIoStatus(ctypes.Structure):
    _fields_ = [("value", _SnapshotStatusValue), ("information", ctypes.c_size_t)]


def _replace_snapshot(source, destination):
    """Publish only a same-directory derived snapshot, once, without fallback.

    Windows legacy rename can reject an open destination even when its readers
    share deletion. Native same-directory RenameEx + POSIX preserves old open
    versions without reopening pinned ancestors for directory write. Unsupported
    Windows/filesystem combinations fail closed; immutable records and CAD
    inputs never use this helper. Non-Windows retains os.replace.
    """
    source, destination = Path(source), Path(destination)
    if (
        not source.is_absolute()
        or source.parent != destination.parent
        or destination.name != "snapshot.json"
        or not re.fullmatch(r"snapshot-[a-f0-9]{16}\.tmp", source.name)
    ):
        raise LifecycleError("private_snapshot_publication_paths_required")
    if os.name != "nt":
        os.replace(source, destination)
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        (
            "CreateFileW",
            (w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE),
            w.HANDLE,
        ),
        ("GetFileInformationByHandle", (w.HANDLE, ctypes.POINTER(_SnapshotFileInfo)), w.BOOL),
        ("CloseHandle", (w.HANDLE,), w.BOOL),
    ):
        method = getattr(kernel, name)
        method.argtypes, method.restype = args, result
    native = ctypes.WinDLL("ntdll")
    native.NtSetInformationFile.argtypes, native.NtSetInformationFile.restype = (
        (w.HANDLE, ctypes.POINTER(_SnapshotIoStatus), ctypes.c_void_p, w.ULONG, ctypes.c_int),
        w.LONG,
    )
    native.RtlNtStatusToDosError.argtypes, native.RtlNtStatusToDosError.restype = (w.LONG,), w.ULONG
    handle = None
    point = "CreateFileW"
    try:
        handle = kernel.CreateFileW(str(source), 0x00110080, 7, None, 3, 0x00200000, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        point = "GetFileInformationByHandle"
        info = _SnapshotFileInfo()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x410:
            raise LifecycleError("snapshot_publication_regular_file_required")
        # NULL RootDirectory plus a single leaf is relative to the source's
        # directory in this native operation, independent of the process CWD.
        encoded = "snapshot.json".encode("utf-16-le")
        offset = _SnapshotRenameInfo.name.offset
        buffer = ctypes.create_string_buffer(offset + len(encoded) + 2)
        value = ctypes.cast(buffer, ctypes.POINTER(_SnapshotRenameInfo)).contents
        value.flags, value.root, value.name_bytes = 3, None, len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
        point = "NtSetInformationFile"
        io = _SnapshotIoStatus()
        status = int(native.NtSetInformationFile(handle, ctypes.byref(io), buffer, len(buffer), 65))
        if status != 0:
            error = ctypes.WinError(int(native.RtlNtStatusToDosError(status)))
            error.snapshot_ntstatus = status
            raise error
    except OSError as error:
        error.snapshot_callpoint = point
        raise
    finally:
        if handle is not None and not kernel.CloseHandle(handle):
            error = ctypes.WinError(ctypes.get_last_error())
            error.snapshot_callpoint = "CloseHandle"
            raise error


@contextlib.contextmanager
def _snapshot_reader(path):
    """Pin a complete snapshot version without preventing its atomic replacement.

    Windows CRT readers do not promise FILE_SHARE_DELETE. The explicit handle
    here permits read/write/delete sharing, refuses a reparse leaf, and transfers
    ownership once to a binary Python stream. No writer retry is introduced.
    """
    if os.name != "nt":
        with path.open("rb") as stream:
            yield stream
        return
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes, kernel.CreateFileW.restype = (
        (w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE),
        w.HANDLE,
    )
    kernel.GetFileInformationByHandle.argtypes, kernel.GetFileInformationByHandle.restype = (
        (w.HANDLE, ctypes.POINTER(_SnapshotFileInfo)),
        w.BOOL,
    )
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = (w.HANDLE,), w.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x00200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = None
    try:
        info = _SnapshotFileInfo()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x410:
            raise LifecycleError("record_reparse_path_refused")
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = None  # The CRT descriptor now owns the original HANDLE.
        stream = os.fdopen(descriptor, "rb")
        descriptor = None  # The stream now owns the descriptor.
        with stream:
            yield stream
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if handle is not None and not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _read(path, key):
    if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
        raise LifecycleError("record_reparse_path_refused")
    reader = _snapshot_reader(path) if path.name == "snapshot.json" else path.open("rb")
    with reader as stream:
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise LifecycleError("record_byte_limit")
    try:
        wrapped = json.loads(raw)
        if set(wrapped) != {"payload", "mac"} or not isinstance(wrapped["payload"], dict):
            raise ValueError
        expected = hmac.new(key, _canonical(wrapped["payload"]), hashlib.sha256).hexdigest()
        if not isinstance(wrapped["mac"], str) or not hmac.compare_digest(expected, wrapped["mac"]):
            raise ValueError
        return wrapped["payload"], hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, KeyError) as exc:
        raise LifecycleError("record_integrity_incomplete") from exc


def _private_mkdir(path):
    """Only a new runtime directory's ACL is set; no system policy is changed."""
    if os.name != "nt":
        raise LifecycleError("prototype_requires_windows_process_identity")
    advapi, kernel = (
        ctypes.WinDLL("advapi32", use_last_error=True),
        ctypes.WinDLL("kernel32", use_last_error=True),
    )
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes, convert.restype = (
        (w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p),
        w.BOOL,
    )
    descriptor = ctypes.c_void_p()
    if not convert("D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)", 1, ctypes.byref(descriptor), None):
        raise LifecycleError("private_runtime_acl_unavailable")

    class Security(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]

    attrs = Security(ctypes.sizeof(Security), descriptor, False)
    kernel.CreateDirectoryW.argtypes, kernel.CreateDirectoryW.restype = (
        (w.LPCWSTR, ctypes.POINTER(Security)),
        w.BOOL,
    )
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = (ctypes.c_void_p,), ctypes.c_void_p
    try:
        if not kernel.CreateDirectoryW(str(path), ctypes.byref(attrs)):
            raise LifecycleError("new_private_runtime_directory_required")
    finally:
        kernel.LocalFree(descriptor)


def _root(root, *, create=False):
    root = _plain_path(root)
    if not root.exists():
        if not create or not root.parent.is_dir():
            raise LifecycleError("runtime_root_not_owned")
        _private_mkdir(root)
        key = secrets.token_bytes(32)
        _write_new(root / "integrity-key.bin", key)
        marker = {
            "component": COMPONENT,
            "root_epoch": secrets.token_hex(16),
            "locator_sha256": _locator(root),
        }
        _write_new(root / "root.json", _pack(marker, key))
        _private_mkdir(root / "executions")
    try:
        key_path = root / "integrity-key.bin"
        if key_path.is_symlink() or getattr(key_path.lstat(), "st_file_attributes", 0) & 0x400:
            raise LifecycleError("runtime_key_reparse_refused")
        with key_path.open("rb") as stream:
            key = stream.read(33)
        if len(key) != 32:
            raise LifecycleError("runtime_key_invalid")
        marker, _ = _read(root / "root.json", key)
        if (
            marker.get("component") != COMPONENT
            or marker.get("locator_sha256") != _locator(root)
            or not re.fullmatch(r"[a-f0-9]{32}", str(marker.get("root_epoch")))
        ):
            raise LifecycleError("runtime_root_binding_invalid")
        return root, key, marker
    except OSError as exc:
        raise LifecycleError("runtime_root_not_owned") from exc


@contextlib.contextmanager
def _launch_lock(root):
    import msvcrt

    with (root / "launch.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + 1.0
        while True:
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LifecycleError("bounded_launch_lock_busy") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _kernel():
    if os.name != "nt":
        raise LifecycleError("prototype_requires_windows_process_identity")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        ("GetProcessId", (w.HANDLE,), w.DWORD),
        ("GetProcessTimes", (w.HANDLE,) + (ctypes.POINTER(w.FILETIME),) * 4, w.BOOL),
        (
            "QueryFullProcessImageNameW",
            (w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)),
            w.BOOL,
        ),
        ("WaitForSingleObject", (w.HANDLE, w.DWORD), w.DWORD),
        ("OpenProcess", (w.DWORD, w.BOOL, w.DWORD), w.HANDLE),
        ("CloseHandle", (w.HANDLE,), w.BOOL),
        ("GetCurrentProcess", (), w.HANDLE),
    ):
        method = getattr(kernel, name)
        method.argtypes, method.restype = args, result
    return kernel


def _identity(handle):
    kernel = _kernel()
    pid = int(kernel.GetProcessId(handle))
    times = [w.FILETIME() for _ in range(4)]
    if pid <= 0 or not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
        raise LifecycleError("executor_generation_unobservable")
    wait = int(kernel.WaitForSingleObject(handle, 0))
    if wait not in (0, 258):
        raise LifecycleError("executor_liveness_unobservable")
    size, image = w.DWORD(32768), ctypes.create_unicode_buffer(32768)
    image_ok = bool(kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)))
    if not image_ok and wait != 0:
        raise LifecycleError("executor_image_unobservable")
    return {
        "pid": pid,
        "birth_filetime_100ns": str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime),
        "executable": os.path.normcase(image.value) if image_ok else None,
        "alive": wait == 258,
    }


def _fresh_owner(expected):
    kernel = _kernel()
    handle = kernel.OpenProcess(0x00101000, False, expected["pid"])
    if not handle:
        if ctypes.get_last_error() == 87:
            return "verified_exited", None
        return "unobservable", None
    try:
        actual = _identity(handle)
        if (
            actual["pid"] != expected["pid"]
            or actual["birth_filetime_100ns"] != expected["birth_filetime_100ns"]
        ):
            return "identity_mismatch", actual
        if not actual["alive"]:
            return "verified_exited", actual
        if actual["executable"] != expected["executable"]:
            return "identity_mismatch", actual
        return "verified_alive", actual
    except LifecycleError:
        return "unobservable", None
    finally:
        kernel.CloseHandle(handle)


class _DetachedChild(subprocess.Popen):
    """Closing a parent HANDLE is not termination or a claim of child cleanup."""

    def close_parent_handle(self):
        handle = getattr(self, "_handle", None)
        if handle is not None:
            handle.Close()
            self._handle = None

    def __del__(self):
        try:
            self.close_parent_handle()
        except Exception:
            pass


def _source_binding():
    result = {}
    for name in ("execution_lifecycle.py", "offline_executor.py"):
        with Path(__file__).with_name(name).open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise LifecycleError("prototype_source_byte_limit")
        result[name] = hashlib.sha256(data).hexdigest()
    return result


def _context(root, execution_id):
    root, key, marker = _root(root)
    directory = root / "executions" / execution_id
    _plain_path(directory)
    intent, _ = _read(directory / "intent.json", key)
    if (
        intent.get("execution_id") != execution_id
        or intent.get("root_epoch") != marker["root_epoch"]
        or intent.get("scenario") not in SCENARIOS
        or not re.fullmatch(r"fixture-op-[a-f0-9]{32}", str(intent.get("operation_id")))
        or not re.fullmatch(r"[a-f0-9]{32}", str(intent.get("execution_epoch")))
    ):
        raise LifecycleError("execution_intent_binding_invalid")
    initial, _ = _read(directory / "initial-observation.json", key)
    if initial != {
        "schema_version": "1.0",
        "execution_id": execution_id,
        "operation_id": intent["operation_id"],
        "original_operation_status": "unknown",
        "immutable_initial_observation": True,
        "cad_verified": False,
    }:
        raise LifecycleError("initial_unknown_observation_binding_invalid")
    return root, directory, key, intent


def _events(directory, key, intent):
    files = sorted(itertools.islice((directory / "events").iterdir(), MAX_EVENTS + 1))
    if len(files) > MAX_EVENTS:
        raise LifecycleError("execution_event_limit")
    values, previous = [], None
    for sequence, path in enumerate(files, 1):
        if path.name != f"event-{sequence:04d}.json":
            raise LifecycleError("execution_event_sequence_incomplete")
        event, digest = _read(path, key)
        if (
            event.get("sequence") != sequence
            or event.get("previous_sha256") != previous
            or event.get("execution_id") != intent["execution_id"]
            or event.get("execution_epoch") != intent["execution_epoch"]
        ):
            raise LifecycleError("execution_event_chain_invalid")
        values.append(event)
        previous = digest
    if values:
        snapshot, _ = _read(directory / "snapshot.json", key)
        if snapshot != {
            "execution_id": intent["execution_id"],
            "execution_epoch": intent["execution_epoch"],
            "sequence": len(values),
            "event_sha256": previous,
        }:
            raise LifecycleError("execution_snapshot_not_complete")
    return values


def _base(execution_id):
    return {
        "schema_version": "1.0",
        "execution_id": execution_id,
        "original_operation_status": "unknown",
        "cad_verified": False,
        "native_execution_enabled": False,
        "executor_state": "unresolved",
        "owner_state": "unobservable",
        "cleanup_verified": None,
        "cleanup_observation": "unobserved",
        "owner_image_matches": None,
    }


def _owner_image_match(actual, expected):
    image = actual.get("executable") if isinstance(actual, dict) else None
    return image == expected["executable"] if isinstance(image, str) else None


def observe_execution(root, execution_id, *, expected_epoch=None, expected_birth=None):
    if not isinstance(execution_id, str) or not EXECUTION_RE.fullmatch(execution_id):
        return {
            **_base(None),
            "executor_state": "legacy_unmanaged_unknown",
            "owner_state": "not_owned",
            "reason": "no_original_operation_adoption",
        }
    result = _base(execution_id)
    try:
        _, directory, key, intent = _context(root, execution_id)
        registration, _ = _read(directory / "owner.json", key)
        if (
            registration.get("execution_epoch") != intent["execution_epoch"]
            or registration.get("execution_id") != execution_id
        ):
            raise LifecycleError("owner_registration_epoch_invalid")
        owner = registration["owner"]
        if (
            not isinstance(owner, dict)
            or type(owner.get("pid")) is not int
            or owner["pid"] <= 0
            or not isinstance(owner.get("birth_filetime_100ns"), str)
            or not owner["birth_filetime_100ns"].isascii()
            or not owner["birth_filetime_100ns"].isdecimal()
            or int(owner["birth_filetime_100ns"]) <= 0
            or not isinstance(owner.get("executable"), str)
        ):
            raise LifecycleError("owner_registration_incomplete")
        if expected_epoch is not None and expected_epoch != intent["execution_epoch"]:
            return {
                **result,
                "owner_state": "identity_mismatch",
                "reason": "observer_epoch_constraint_differs",
            }
        if expected_birth is not None and expected_birth != owner["birth_filetime_100ns"]:
            return {
                **result,
                "owner_state": "identity_mismatch",
                "reason": "observer_birth_constraint_differs",
            }
        values = _events(directory, key, intent)
        state, actual = _fresh_owner(owner)
        result.update(
            owner_state=state,
            execution_epoch=intent["execution_epoch"],
            owner_pid=owner["pid"],
            owner_birth_filetime_100ns=owner["birth_filetime_100ns"],
            owner_image_matches=_owner_image_match(actual, owner),
            event_count=len(values),
            phase=values[-1]["phase"] if values else "not_observed",
            latest_observation_sequence=len(values),
        )
        result["stop_requested"] = (directory / "stop-request.json").exists()
        if result["stop_requested"]:
            stop, _ = _read(directory / "stop-request.json", key)
            if stop != {
                "execution_id": execution_id,
                "execution_epoch": intent["execution_epoch"],
                "request": "stop_at_safe_boundary",
            }:
                raise LifecycleError("stop_request_binding_invalid")
        completed = bool(
            values
            and state in ("verified_alive", "verified_exited")
            and values[-1]["phase"] == "cleanup_complete"
            and values[-1]["details"].get("resource_removed") is True
            and not (directory / "fixture-resource.bin").exists()
        )
        failed = bool(values and values[-1]["phase"] == "cleanup_failed")
        if completed:
            result.update(cleanup_verified=True, cleanup_observation="complete")
        elif failed:
            result.update(cleanup_verified=False, cleanup_observation="failed")
        elif values and values[-1]["phase"] == "cleanup_complete":
            result["cleanup_observation"] = "unverified"
        elif state == "verified_alive" and any(
            event["phase"] == "call_entered" for event in values
        ):
            result["cleanup_observation"] = "in_progress"
        if completed:
            result["executor_state"] = (
                "completed" if state == "verified_exited" else "cleanup_complete_owner_still_alive"
            )
        elif failed:
            result["executor_state"] = "failed_cleanup_unresolved"
        elif state == "verified_alive":
            result["executor_state"] = "unresolved_owner_alive"
        else:
            result["executor_state"] = "owner_lost_unresolved"
        return result
    except (OSError, KeyError, TypeError, ValueError) as exc:
        result["reason"] = (
            str(exc) if isinstance(exc, LifecycleError) else "execution_observation_incomplete"
        )
        return result


def launch_fixture(
    root, scenario="block_until_fixture_release", *, observation_budget_seconds=0.15
):
    """Launch one allowlisted source fixture; never adopts an operation or CAD."""
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        raise LifecycleError("source_only_prototype_no_frozen_or_mei_owner")
    if scenario not in SCENARIOS:
        raise LifecycleError("built_in_non_cad_fixture_required")
    if (
        type(observation_budget_seconds) not in (int, float)
        or not math.isfinite(observation_budget_seconds)
        or not 0 <= observation_budget_seconds <= 2
    ):
        raise LifecycleError("bounded_observation_budget_required")
    root, key, marker = _root(root, create=True)
    with _launch_lock(root):
        known = list(itertools.islice((root / "executions").iterdir(), MAX_EXECUTIONS + 1))
        if len(known) >= MAX_EXECUTIONS or any(
            not EXECUTION_RE.fullmatch(path.name) for path in known
        ):
            raise LifecycleError("bounded_execution_root_full_or_incomplete")
        occupied = sum(
            not (
                item.get("cleanup_verified") is True
                and item.get("owner_state") == "verified_exited"
            )
            for item in (observe_execution(root, path.name) for path in known)
        )
        if occupied >= MAX_OWNERS:
            raise LifecycleError("executor_owner_limit_reached")
        execution_id, epoch = "exec-" + secrets.token_hex(16), secrets.token_hex(16)
        directory = root / "executions" / execution_id
        _private_mkdir(directory)
        _private_mkdir(directory / "events")
        intent = {
            "component": COMPONENT,
            "execution_id": execution_id,
            "execution_epoch": epoch,
            "root_epoch": marker["root_epoch"],
            "operation_id": "fixture-op-" + secrets.token_hex(16),
            "scenario": scenario,
            "source_sha256": _source_binding(),
            "fixture_release_is_not_product_stop": True,
        }
        _write_new(directory / "intent.json", _pack(intent, key))
        initial = {
            "schema_version": "1.0",
            "execution_id": execution_id,
            "operation_id": intent["operation_id"],
            "original_operation_status": "unknown",
            "immutable_initial_observation": True,
            "cad_verified": False,
        }
        _write_new(directory / "initial-observation.json", _pack(initial, key))
        child = None
        try:
            with (
                (directory / "stdout.log").open("xb") as output,
                (directory / "stderr.log").open("xb") as errors,
            ):
                environment = {
                    name: os.environ[name]
                    for name in ("SystemRoot", "WINDIR", "TEMP", "TMP")
                    if name in os.environ
                }
                environment.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
                child = _DetachedChild(
                    [
                        sys.executable,
                        "-X",
                        "utf8",
                        "-B",
                        str(Path(__file__).with_name("offline_executor.py")),
                        "--root",
                        str(root),
                        "--execution",
                        execution_id,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=errors,
                    cwd=str(directory),
                    close_fds=True,
                    env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                owner = _identity(int(child._handle))
                if owner["pid"] != child.pid or not owner["alive"]:
                    raise LifecycleError("spawned_executor_identity_incomplete")
                _write_new(
                    directory / "owner.json",
                    _pack(
                        {"execution_id": execution_id, "execution_epoch": epoch, "owner": owner},
                        key,
                    ),
                )
                if scenario != "no_permit":
                    _write_new(
                        directory / "permit.json",
                        _pack(
                            {
                                "execution_id": execution_id,
                                "execution_epoch": epoch,
                                "owner": owner,
                                "source_sha256": intent["source_sha256"],
                            },
                            key,
                        ),
                    )
        finally:
            if child is not None:
                child.close_parent_handle()
    deadline = time.monotonic() + observation_budget_seconds
    result = observe_execution(root, execution_id)
    while time.monotonic() < deadline and result["executor_state"] not in (
        "completed",
        "failed_cleanup_unresolved",
    ):
        time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        result = observe_execution(root, execution_id)
    return result


def request_stop(root, execution_id):
    """Cooperative boundary request; cannot interrupt or release a blocked call."""
    current = observe_execution(root, execution_id)
    if current["owner_state"] != "verified_alive" or current["cleanup_verified"]:
        return {
            "accepted": False,
            "reason": "executor_not_verified_running",
            "original_operation_status": "unknown",
        }
    _, directory, key, intent = _context(root, execution_id)
    request = {
        "execution_id": execution_id,
        "execution_epoch": intent["execution_epoch"],
        "request": "stop_at_safe_boundary",
    }
    try:
        _write_new(directory / "stop-request.json", _pack(request, key))
    except FileExistsError:
        existing, _ = _read(directory / "stop-request.json", key)
        if existing != request:
            raise LifecycleError("stop_request_binding_invalid") from None
    return {
        "accepted": True,
        "stop_is_cooperative_only": True,
        "blocked_call_interrupted": False,
        "original_operation_status": "unknown",
    }


class EventWriter:
    """One child writes immutable sequence records and an atomic derived snapshot."""

    def __init__(self, directory, key, intent):
        self.directory, self.key, self.intent = directory, key, intent
        self.sequence, self.previous = 0, None

    def append(self, phase, **details):
        if self.sequence >= MAX_EVENTS:
            raise LifecycleError("execution_event_limit")
        self.sequence += 1
        payload = {
            "execution_id": self.intent["execution_id"],
            "execution_epoch": self.intent["execution_epoch"],
            "sequence": self.sequence,
            "previous_sha256": self.previous,
            "phase": phase,
            "details": details,
        }
        raw = _pack(payload, self.key)
        _write_new(self.directory / "events" / f"event-{self.sequence:04d}.json", raw)
        self.previous = hashlib.sha256(raw).hexdigest()
        snapshot = {
            "execution_id": self.intent["execution_id"],
            "execution_epoch": self.intent["execution_epoch"],
            "sequence": self.sequence,
            "event_sha256": self.previous,
        }
        temporary = self.directory / ("snapshot-" + secrets.token_hex(8) + ".tmp")
        _write_new(temporary, _pack(snapshot, self.key))
        _replace_snapshot(temporary, self.directory / "snapshot.json")


def run_child(root, execution_id):
    """Only the fixed fixture scenarios below can own this executor process."""
    if not isinstance(execution_id, str) or not EXECUTION_RE.fullmatch(execution_id):
        raise LifecycleError("new_offline_execution_id_required")
    if any(name.split(".")[0] in {"pythoncom", "win32com", "comtypes"} for name in sys.modules):
        raise LifecycleError("offline_fixture_com_import_forbidden")
    _, directory, key, intent = _context(root, execution_id)
    if intent["source_sha256"] != _source_binding():
        raise LifecycleError("executor_source_binding_changed")
    writer = EventWriter(directory, key, intent)
    writer.append("owner_ready", native_execution=False)
    deadline = time.monotonic() + PERMIT_WAIT_SECONDS
    permit = None
    while time.monotonic() < deadline:
        try:
            permit, _ = _read(directory / "permit.json", key)
            break
        except FileNotFoundError:
            time.sleep(0.01)
    if permit is None:
        writer.append("abandoned_before_dispatch", permit_received=False)
        writer.append("cleanup_complete", resource_removed=True, call_dispatched=False)
        return 0
    own = _identity(_kernel().GetCurrentProcess())
    if permit != {
        "execution_id": execution_id,
        "execution_epoch": intent["execution_epoch"],
        "owner": own,
        "source_sha256": intent["source_sha256"],
    }:
        raise LifecycleError("executor_permit_generation_binding_invalid")
    resource = directory / "fixture-resource.bin"
    stream = None
    try:
        import msvcrt

        stream = resource.open("x+b")
        stream.write(b"non-cad fixture resource\n")
        stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        writer.append(
            "call_entered", fixture_only=True, resource_owned=True, scenario=intent["scenario"]
        )
        if intent["scenario"] == "block_until_fixture_release":
            # Only test teardown may release this fixture. Product stop does not.
            while not (directory / "fixture-only-release").exists():
                time.sleep(0.01)
        writer.append("call_returned", fixture_value=True, not_cad_evidence=True)
        if intent["scenario"] == "callback_until_fixture_release":
            callback_entered, callback_completed = threading.Event(), threading.Event()

            def callback():
                callback_entered.set()
                try:
                    while not (directory / "fixture-only-callback-release").exists():
                        time.sleep(0.01)
                    callback_completed.set()
                except Exception:
                    return

            task = threading.Thread(target=callback, name="offline-owned-callback", daemon=False)
            task.start()
            if not callback_entered.wait(1):
                raise LifecycleError("fixture_callback_not_entered")
            writer.append("callback_inflight", cleanup_allowed=False, fixture_only=True)
            task.join()  # Only the child owns this wait; the caller remains bounded.
            if not callback_completed.is_set():
                raise LifecycleError("fixture_callback_did_not_complete")
            writer.append("callback_returned", fixture_only=True)
        if (directory / "stop-request.json").exists():
            stop, _ = _read(directory / "stop-request.json", key)
            if stop != {
                "execution_id": execution_id,
                "execution_epoch": intent["execution_epoch"],
                "request": "stop_at_safe_boundary",
            }:
                raise LifecycleError("stop_request_binding_invalid")
            writer.append("stop_acknowledged_at_safe_boundary", blocked_call_interrupted=False)
        writer.append("cleanup_started")
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()
        stream = None
        if intent["scenario"] == "cleanup_failure":
            raise LifecycleError("intentional_fixture_cleanup_failure")
        resource.unlink()
        writer.append("cleanup_complete", resource_removed=True, not_cad_evidence=True)
        return 0
    except Exception:
        if stream is not None:
            stream.close()
        writer.append("cleanup_failed", cleanup_verified=False, fixture_only=True)
        return 20
