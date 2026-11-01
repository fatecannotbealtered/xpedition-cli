"""Read-only-from-birth startup inputs with explicit retain-only ownership.

InputsLease atomically creates its directory and pins the existing namespace,
writes each already read-only input through its original creator, then retains
metadata-only handles. It never changes an ACL or deletes retained files.
Only after both immutable files are verified does it replace the workspace's
read-sharing pin with a read/write-sharing pin. Ancestors stay read-sharing-only;
the workspace is permanently nonempty and every directory pin denies deletion.
The rejected WRITE_DAC-keeper route is historical. Import is inert; filesystem
namespace and CAD qualification remain distinct and public eligibility is false.
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import os
import re
import threading
from ctypes import wintypes as w
from pathlib import Path, PureWindowsPath

WRITE_DAC = 0x00040000
DATA_OR_DELETE_ACCESS = 0x00010027
MAX_BYTES = 65536
INPUT_NAMES = frozenset({"identity.vbs", "scripts.ini"})
DIRECTORY_ACCESS = 0x00120081  # LIST_DIRECTORY | RATTR | RC | SYNCHRONIZE.
METADATA_ACCESS = 0x00120080
CREATOR_MASK = 0x0012019F
DIRECTORY_MASK = 0x001200AF
FILE_MASK = 0x00120089
DIRECTORY_SDDL = "D:P(A;;0x001200af;;;OW)"
FILE_SDDL = "D:P(A;;GR;;;OW)"
CATEGORIES = frozenset(
    "startup_input_" + suffix
    for suffix in (
        "contract_incomplete",
        "access_mask_mismatch",
        "identity_mismatch",
        "readback_mismatch",
        "security_mismatch",
        "resource_limit",
        "native_api_failed",
        "handle_release_failed",
    )
)


class SealError(ValueError):
    """Fixed safe diagnostic reason, never a request for privilege escalation."""

    def __init__(self, category, *, winerror=None):
        self.category = (
            category
            if isinstance(category, str) and category in CATEGORIES
            else "startup_input_contract_incomplete"
        )
        self.winerror = winerror if type(winerror) is int else None
        super().__init__(self.category)


class InputsLease:
    """Owner-held startup inputs. Construction is inert; files are never removed.

    The launch owner must assign this object before calling prepare, and must
    enforce its native terminal boundary before terminal_release. No constructor
    I/O, automatic context cleanup, destructor, privilege or deletion is used.
    """

    def __init__(self, workspace):
        self._workspace_input = workspace
        self.directory = None
        self._api = None
        self._holders = []
        self._files = {}
        self._created_names = []
        self._birth_verified = set()
        self._uncertain_creation = False
        self._owner = threading.current_thread()
        self._owner_ident = threading.get_ident()
        self._state = "new"
        self._released = None
        self._verified = False
        self._phase = "new"
        self._workspace_share = None
        self._workspace_transition_complete = False

    def _owner_thread(self):
        if (
            threading.current_thread() is not self._owner
            or threading.get_ident() != self._owner_ident
        ):
            raise SealError("startup_input_contract_incomplete")

    def _hold(self, opened, kind, path):
        holder = {
            "handle": opened["handle"],
            "state": "open",
            "kind": kind,
            "path": path,
            "disposition": opened["disposition"],
        }
        if opened["handle"] is not None:
            self._holders.append(holder)
        if opened.get("ntstatus", 0) != 0 or opened["handle"] is None:
            holder["state"] = "close_unknown"
            if kind in ("workspace", "creator"):
                self._uncertain_creation = True
            raise SealError("startup_input_native_api_failed", winerror=opened.get("winerror"))
        return holder

    def _access(self, holder, required, allowed):
        self._phase = holder["kind"] + ".access_query"
        actual = self._api.access(holder["handle"])
        if type(actual) is not int or actual & required != required or actual & ~allowed:
            raise SealError("startup_input_access_mask_mismatch")
        holder["granted_access"] = actual

    def _close(self, holder):
        if holder["state"] == "closed":
            return
        if holder["state"] != "open":
            raise SealError("startup_input_handle_release_failed")
        try:
            self._phase = holder["kind"] + ".close"
            self._api.close(holder["handle"])
        except BaseException as error:
            holder["state"] = "close_unknown"
            self._released = False
            raise SealError(
                "startup_input_handle_release_failed", winerror=_error_number(error)
            ) from None
        holder["handle"] = None
        holder["state"] = "closed"

    def _directory_identity(self, holder):
        self._phase = holder["kind"] + ".identity"
        info = self._api.info(holder["handle"])
        if not info["attributes"] & 0x10 or info["attributes"] & 0x400:
            raise SealError("startup_input_identity_mismatch")
        return info

    def prepare(self, payloads):
        self._owner_thread()
        if self._state != "new":
            raise SealError("startup_input_contract_incomplete")
        workspace, components = _workspace_parts(self._workspace_input)
        if (
            type(payloads) is not dict
            or set(payloads) != INPUT_NAMES
            or any(type(v) is not bytes or not v for v in payloads.values())
        ):
            raise SealError("startup_input_contract_incomplete")
        if (
            any(len(v) > MAX_BYTES for v in payloads.values())
            or sum(map(len, payloads.values())) > MAX_BYTES
        ):
            raise SealError("startup_input_resource_limit")
        self._state = "preparing"
        try:
            self._api = _make_api()
            self._api.require_local_fixed_drive(workspace.drive)
            previous = None
            for index, component in enumerate(components[:-1]):
                self._phase = "ancestor.open"
                opened = self._api.directory_open(
                    component, previous["handle"] if previous else None, create=False
                )
                holder = self._hold(
                    opened, "ancestor", workspace.parents[len(components) - index - 2]
                )
                if opened["disposition"] != 1:
                    raise SealError("startup_input_identity_mismatch")
                self._access(holder, DIRECTORY_ACCESS, DIRECTORY_ACCESS)
                holder["identity"] = self._directory_identity(holder)
                if index == 0:
                    self._api.require_ntfs(holder["handle"])
                previous = holder
            self._phase = "workspace.atomic_create"
            opened = self._api.directory_open(components[-1], previous["handle"], create=True)
            directory = self._hold(opened, "workspace", workspace)
            self._workspace_share = 1
            self.directory = Path(str(workspace))
            if opened["disposition"] != 2:
                raise SealError("startup_input_identity_mismatch")
            self._access(directory, DIRECTORY_ACCESS, DIRECTORY_ACCESS)
            directory["identity"] = self._directory_identity(directory)
            if not self._api.security_exact(directory["handle"], DIRECTORY_MASK):
                raise SealError("startup_input_security_mismatch")
            raw_dos = self._api.final_dos_path(directory["handle"])
            canonical = _canonical_dos_directory(raw_dos, workspace)
            self.directory = Path(str(canonical))
            for name in sorted(INPUT_NAMES):
                payload = payloads[name]
                self._phase = "creator.atomic_create"
                opened = self._api.file_create(name, directory["handle"])
                creator = self._hold(opened, "creator", workspace / name)
                if opened["disposition"] != 2:
                    raise SealError("startup_input_identity_mismatch")
                self._created_names.append(name)
                self._access(creator, CREATOR_MASK, CREATOR_MASK)
                initial = self._api.info(creator["handle"])
                if initial["attributes"] & 0x410 or initial["links"] != 1 or initial["bytes"] != 0:
                    raise SealError("startup_input_identity_mismatch")
                if not self._api.security_exact(creator["handle"], FILE_MASK):
                    raise SealError("startup_input_security_mismatch")
                self._birth_verified.add(name)
                self._phase = "creator.write_flush_full_readback"
                observed = self._api.write_read_hash(creator["handle"], payload)
                if observed != hashlib.sha256(payload).hexdigest():
                    raise SealError("startup_input_readback_mismatch")
                self._phase = "metadata.open"
                opened = self._api.metadata_open(name, directory["handle"])
                metadata = self._hold(opened, "metadata", workspace / name)
                self._access(metadata, METADATA_ACCESS, METADATA_ACCESS)
                if (
                    opened["disposition"] != 1
                    or self._api.info(metadata["handle"])["file_id"] != initial["file_id"]
                ):
                    raise SealError("startup_input_identity_mismatch")
                self._files[name] = {"holder": metadata, "sha256": observed, "bytes": len(payload)}
                self._close(creator)
                baseline = self._api.info(metadata["handle"])
                if (
                    baseline["bytes"] != len(payload)
                    or baseline["links"] != 1
                    or baseline["attributes"] & 0x410
                ):
                    raise SealError("startup_input_identity_mismatch")
                self._files[name]["identity"] = baseline
            self._state = "prepared"
            self.verify_prelaunch()
            self._replace_prepared_workspace_pin(directory, previous, components[-1])
            return self
        except BaseException as error:
            self._state, self._verified = "failed", False
            if isinstance(error, SealError):
                raise
            raise SealError(
                "startup_input_native_api_failed", winerror=_error_number(error)
            ) from None

    def _replace_prepared_workspace_pin(self, original, parent, name):
        """Permit cache operations only after immutable children prevent emptiness.

        NTFS rejects setting a reparse point on a nonempty directory (MS-FSA
        2.1.5.10.37). The original share1 pin stays held until a separately owned
        share3 pin proves the same directory and both birth-read-only files have
        been reverified. No ancestor pin or ACL is changed, and neither pin
        shares DELETE. This is not an empty-directory sharing relaxation.
        """
        if (
            self._state != "prepared"
            or self._verified is not True
            or set(self._files) != INPUT_NAMES
            or self._birth_verified != INPUT_NAMES
            or self._workspace_transition_complete
            or original["kind"] != "workspace"
            or original["state"] != "open"
            or parent["kind"] != "ancestor"
            or parent["state"] != "open"
        ):
            raise SealError("startup_input_contract_incomplete")
        self._verified = False
        self._phase = "workspace.reopen_prepared_nonempty"
        opened = self._api.workspace_reopen(name, parent["handle"])
        replacement = self._hold(opened, "workspace_candidate", self.directory)
        if opened["disposition"] != 1:
            raise SealError("startup_input_identity_mismatch")
        self._access(replacement, DIRECTORY_ACCESS, DIRECTORY_ACCESS)
        replacement["identity"] = self._directory_identity(replacement)
        if replacement["identity"]["file_id"] != original["identity"]["file_id"]:
            raise SealError("startup_input_identity_mismatch")
        if not self._api.security_exact(replacement["handle"], DIRECTORY_MASK):
            raise SealError("startup_input_security_mismatch")
        self._verify_win32(replacement, self.directory, directory=True)
        self.verify_prelaunch()  # Original pin and all immutable children still held.
        self._verified = False
        self._close(original)
        original["kind"] = "retired_workspace"
        replacement["kind"] = "workspace"
        self._workspace_share = 3
        self.verify_prelaunch()
        self._workspace_transition_complete = True

    def verify_prelaunch(self):
        self._owner_thread()
        self._verified = False
        if (
            self._state != "prepared"
            or self._released is not None
            or set(self._files) != INPUT_NAMES
        ):
            raise SealError("startup_input_contract_incomplete")
        try:
            if self._uncertain_creation or any(
                h["state"] == "close_unknown" for h in self._holders
            ):
                raise SealError("startup_input_handle_release_failed")
            for holder in self._holders:
                if holder["kind"] not in ("ancestor", "workspace"):
                    continue
                if holder["state"] != "open":
                    raise SealError("startup_input_identity_mismatch")
                self._access(holder, DIRECTORY_ACCESS, DIRECTORY_ACCESS)
                if self._directory_identity(holder)["file_id"] != holder["identity"]["file_id"]:
                    raise SealError("startup_input_identity_mismatch")
                if holder["kind"] == "workspace" and not self._api.security_exact(
                    holder["handle"], DIRECTORY_MASK
                ):
                    raise SealError("startup_input_security_mismatch")
                if holder["kind"] == "workspace":
                    self._verify_win32(holder, self.directory, directory=True)
            for name, record in self._files.items():
                holder = record["holder"]
                if holder["state"] != "open":
                    raise SealError("startup_input_identity_mismatch")
                self._access(holder, METADATA_ACCESS, METADATA_ACCESS)
                if self._api.info(holder["handle"]) != record["identity"]:
                    raise SealError("startup_input_identity_mismatch")
                if not self._api.security_exact(holder["handle"], FILE_MASK):
                    raise SealError("startup_input_security_mismatch")
                self._verify_win32(
                    holder, self.directory / name, directory=False, expected=record["identity"]
                )
            if any(h["kind"] == "creator" and h["state"] != "closed" for h in self._holders):
                raise SealError("startup_input_access_mask_mismatch")
            self._verified = True
            self._phase = "verified"
            return self.facts()
        except BaseException as error:
            self._state = "failed"
            if isinstance(error, SealError):
                raise
            raise SealError(
                "startup_input_native_api_failed", winerror=_error_number(error)
            ) from None

    def _verify_win32(self, original, path, *, directory, expected=None):
        opened = self._api.win32_metadata_open(path, directory=directory)
        temporary = self._hold(opened, "interop", path)
        self._access(temporary, METADATA_ACCESS, METADATA_ACCESS)
        actual = self._api.info(temporary["handle"])
        if directory:
            matches = (
                actual["file_id"] == original["identity"]["file_id"]
                and actual["attributes"] & 0x10
                and not actual["attributes"] & 0x400
            )
        else:
            matches = actual == expected and not actual["attributes"] & 0x410
        if not matches:
            raise SealError("startup_input_identity_mismatch")
        self._close(temporary)
        assert self._holders[-1] is temporary
        self._holders.pop()  # Closed transient handles do not accumulate.

    def terminal_release(self):
        self._owner_thread()
        if self._state == "releasing":
            raise SealError("startup_input_contract_incomplete")
        self._verified = False
        if self._uncertain_creation:
            self._released = False
            self._state = "release_failed"
            raise SealError("startup_input_handle_release_failed")
        if set(self._created_names) != self._birth_verified:
            self._released = False
            self._state = "release_failed"
            raise SealError("startup_input_security_mismatch")
        self._state = "releasing"
        try:
            for holder in reversed(self._holders):
                self._close(holder)
        except BaseException:
            self._state = "release_failed"
            raise
        self._released = True
        self._verified = False
        self._state = "released"
        self._phase = "released"
        return self.facts()

    def facts(self):
        self._owner_thread()
        return {
            "scheme": "startup-inputs-retain-only-v2",
            "state": self._state,
            "phase": self._phase,
            "native_seal_enabled": False,
            "resources_released": self._released,
            "files_removed": False,
            "immutable_files_retained": True if self._released is True else None,
            "retention_policy": "retain_read_only_inputs_no_ordinary_cleanup_promise",
            "input_contract_verified": self._verified is True,
            "file_count": len(self._created_names),
            "files_created": list(self._created_names),
            "metadata_proof_count": len(self._files),
            "birth_read_only_verified_count": len(self._birth_verified),
            "verification": "metadata_and_dacl_only" if self._verified else "unobserved",
            "per_attempt_limits": {"files": 2, "bytes_total": MAX_BYTES},
            "account_wide_quota": "not_implemented",
            "ordinary_user_cleanup": "not_promised",
            "pin_policy_version": "prepared_nonempty_workspace_share3_v1",
            "ancestor_pin_share_policy": 1,
            "workspace_pin_share": self._workspace_share,
            "workspace_pin_active": any(
                h["kind"] == "workspace" and h["state"] == "open" for h in self._holders
            ),
            "workspace_pin_transition_complete": self._workspace_transition_complete,
        }

    def private_binding(self):
        self._owner_thread()
        return {
            "files": {
                name: {
                    key: copy.deepcopy(value) for key, value in record.items() if key != "holder"
                }
                for name, record in self._files.items()
            }
        }


def native_seal_enabled() -> bool:
    return False


def _error_number(error):
    for name in ("winerror", "errno"):
        value = getattr(error, name, None)
        if type(value) is int:
            return value
    return None


def _workspace_parts(value):
    try:
        raw = os.fspath(value)
    except TypeError:
        raise SealError("startup_input_contract_incomplete") from None
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z]:[\\/][A-Za-z0-9_./\\-]+", raw):
        raise SealError("startup_input_contract_incomplete")
    path = PureWindowsPath(raw)
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *("COM" + str(i) for i in range(10)),
        *("LPT" + str(i) for i in range(10)),
    }
    if ".." in path.parts or any(
        part.endswith((".", " ")) or part.split(".")[0].upper() in reserved
        for part in path.parts[1:]
    ):
        raise SealError("startup_input_contract_incomplete")
    if len(path.parts) < 2 or len(path.parts) > 32 or len(str(path)) > 220:
        raise SealError("startup_input_resource_limit")
    return path, ("\\??\\" + path.anchor, *path.parts[1:])


def _canonical_dos_directory(value, requested):
    if not isinstance(value, str) or not value.startswith("\\\\?\\"):
        raise SealError("startup_input_identity_mismatch")
    canonical, _ = _workspace_parts(value[4:])
    if str(canonical).casefold() != str(requested).casefold():
        raise SealError("startup_input_identity_mismatch")
    return canonical


class _Unicode(ctypes.Structure):
    _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", w.LPWSTR)]


class _Attributes(ctypes.Structure):
    _fields_ = [
        ("Length", w.ULONG),
        ("RootDirectory", w.HANDLE),
        ("ObjectName", ctypes.POINTER(_Unicode)),
        ("Attributes", w.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _IoStatusValue(ctypes.Union):
    _fields_ = [("Status", w.LONG), ("Pointer", ctypes.c_void_p)]


class _IoStatus(ctypes.Structure):
    _fields_ = [("Value", _IoStatusValue), ("Information", ctypes.c_size_t)]


class _FileInfo(ctypes.Structure):
    _fields_ = [
        ("Attributes", w.DWORD),
        ("Created", w.FILETIME),
        ("Accessed", w.FILETIME),
        ("Written", w.FILETIME),
        ("Volume", w.DWORD),
        ("SizeHigh", w.DWORD),
        ("SizeLow", w.DWORD),
        ("Links", w.DWORD),
        ("IndexHigh", w.DWORD),
        ("IndexLow", w.DWORD),
    ]


class _WindowsApi:
    """Documented NT create/open plus Win32 metadata; no backup/restore flags."""

    def __init__(self):
        if os.name != "nt":
            raise SealError("startup_input_contract_incomplete")
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.n = ctypes.WinDLL("ntdll")
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        create = self.n.NtCreateFile
        create.argtypes = (
            ctypes.POINTER(w.HANDLE),
            w.ULONG,
            ctypes.POINTER(_Attributes),
            ctypes.POINTER(_IoStatus),
            ctypes.c_void_p,
            w.ULONG,
            w.ULONG,
            w.ULONG,
            w.ULONG,
            ctypes.c_void_p,
            w.ULONG,
        )
        create.restype = w.LONG
        self.n.RtlNtStatusToDosError.argtypes = (w.LONG,)
        self.n.RtlNtStatusToDosError.restype = w.ULONG
        self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
            w.LPCWSTR,
            w.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        )
        self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
        for name, args, result in (
            ("GetFinalPathNameByHandleW", (w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD), w.DWORD),
            (
                "CreateFileW",
                (w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE),
                w.HANDLE,
            ),
            ("GetDriveTypeW", (w.LPCWSTR,), w.UINT),
            (
                "GetVolumeInformationByHandleW",
                (
                    w.HANDLE,
                    w.LPWSTR,
                    w.DWORD,
                    ctypes.POINTER(w.DWORD),
                    ctypes.POINTER(w.DWORD),
                    ctypes.POINTER(w.DWORD),
                    w.LPWSTR,
                    w.DWORD,
                ),
                w.BOOL,
            ),
            ("GetFileInformationByHandle", (w.HANDLE, ctypes.POINTER(_FileInfo)), w.BOOL),
            ("CloseHandle", (w.HANDLE,), w.BOOL),
            ("LocalFree", (ctypes.c_void_p,), ctypes.c_void_p),
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
            ("SetFilePointerEx", (w.HANDLE, ctypes.c_longlong, ctypes.c_void_p, w.DWORD), w.BOOL),
        ):
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result

    def _error(self):
        return SealError("startup_input_native_api_failed", winerror=ctypes.get_last_error())

    def require_local_fixed_drive(self, drive):
        if int(self.k.GetDriveTypeW(drive + "\\")) != 3:
            raise SealError("startup_input_contract_incomplete")

    def require_ntfs(self, handle):
        serial, maximum, flags = w.DWORD(), w.DWORD(), w.DWORD()
        name = ctypes.create_unicode_buffer(32)
        if not self.k.GetVolumeInformationByHandleW(
            handle,
            None,
            0,
            ctypes.byref(serial),
            ctypes.byref(maximum),
            ctypes.byref(flags),
            name,
            len(name),
        ):
            raise self._error()
        if name.value != "NTFS" or not flags.value & 8:
            raise SealError("startup_input_contract_incomplete")

    def _open(self, name, root, access, share, disposition, options, sddl=None):
        buffer = ctypes.create_unicode_buffer(name)
        length = len(name.encode("utf-16-le"))
        text = _Unicode(length, length + 2, ctypes.cast(buffer, w.LPWSTR))
        descriptor = ctypes.c_void_p()
        if sddl is not None and not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        ):
            raise self._error()
        attributes = _Attributes(
            ctypes.sizeof(_Attributes), root, ctypes.pointer(text), 0x1040, descriptor, None
        )  # CASE_INSENSITIVE | DONT_REPARSE; not inherited.
        handle, io = w.HANDLE(), _IoStatus()
        try:
            status = int(
                self.n.NtCreateFile(
                    ctypes.byref(handle),
                    access,
                    ctypes.byref(attributes),
                    ctypes.byref(io),
                    None,
                    0x10 if options & 1 else 0x80,
                    share,
                    disposition,
                    options,
                    None,
                    0,
                )
            )
        finally:
            if descriptor:
                self.k.LocalFree(descriptor)
        return {
            "handle": int(handle.value) if handle.value else None,
            "disposition": int(io.Information),
            "ntstatus": status,
            "winerror": int(self.n.RtlNtStatusToDosError(status)) if status else None,
        }

    def directory_open(self, name, root, *, create):
        return self._open(
            name,
            root,
            DIRECTORY_ACCESS,
            1,
            2 if create else 1,
            0x21,
            DIRECTORY_SDDL if create else None,
        )

    def workspace_reopen(self, name, root):
        # Called only after the original workspace and both immutable children
        # pass prepare's complete verification. Existing ancestors retain share1.
        return self._open(name, root, DIRECTORY_ACCESS, 3, 1, 0x21)

    def file_create(self, name, root):
        return self._open(name, root, 0xC0100000, 0, 2, 0x60, FILE_SDDL)

    def metadata_open(self, name, root):
        return self._open(name, root, METADATA_ACCESS, 7, 1, 0x60)

    def final_dos_path(self, handle):
        buffer = ctypes.create_unicode_buffer(225)
        length = int(self.k.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0))
        if length == 0:
            raise self._error()
        if length >= len(buffer):
            raise SealError("startup_input_resource_limit")
        return buffer.value

    def win32_metadata_open(self, path, *, directory):
        flags = 0x00200000 | (0x02000000 if directory else 0)
        handle = self.k.CreateFileW(str(path), METADATA_ACCESS, 7, None, 3, flags, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise self._error()
        return {"handle": int(handle), "disposition": 1, "ntstatus": 0}

    def access(self, handle):
        result = query_granted_access(handle)
        if result["status"] != "observed":
            raise SealError("startup_input_native_api_failed")
        return result["granted_access"]

    def info(self, handle):
        value = _FileInfo()
        if not self.k.GetFileInformationByHandle(handle, ctypes.byref(value)):
            raise self._error()
        return {
            "file_id": (int(value.Volume), (int(value.IndexHigh) << 32) | int(value.IndexLow)),
            "attributes": int(value.Attributes),
            "bytes": (int(value.SizeHigh) << 32) | int(value.SizeLow),
            "links": int(value.Links),
            "last_write_100ns": (int(value.Written.dwHighDateTime) << 32)
            | int(value.Written.dwLowDateTime),
        }

    def security_exact(self, handle, expected_mask):
        import win32security

        descriptor = win32security.GetSecurityInfo(
            handle, win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
        )
        control, _ = descriptor.GetSecurityDescriptorControl()
        dacl = descriptor.GetSecurityDescriptorDacl()
        if not control & 0x1000 or dacl is None or dacl.GetAceCount() != 1:
            return False
        header, mask, sid = dacl.GetAce(0)
        mapped = mask & 0x0FFFFFFF
        for generic, specific in (
            (0x80000000, FILE_MASK),
            (0x40000000, 0x120116),
            (0x20000000, 0x1200A0),
            (0x10000000, 0x1F01FF),
        ):
            if mask & generic:
                mapped |= specific
        return (
            header == (0, 0)
            and mapped == expected_mask
            and win32security.ConvertSidToStringSid(sid) == "S-1-3-4"
        )

    def write_read_hash(self, handle, payload):
        written, read = w.DWORD(), w.DWORD()
        if not self.k.WriteFile(handle, payload, len(payload), ctypes.byref(written), None):
            raise self._error()
        if written.value != len(payload):
            raise SealError("startup_input_readback_mismatch")
        if not self.k.FlushFileBuffers(handle) or not self.k.SetFilePointerEx(handle, 0, None, 0):
            raise self._error()
        output = ctypes.create_string_buffer(len(payload) + 1)
        if not self.k.ReadFile(handle, output, len(payload) + 1, ctypes.byref(read), None):
            raise self._error()
        actual = output.raw[: read.value]
        if actual != payload:
            raise SealError("startup_input_readback_mismatch")
        return hashlib.sha256(actual).hexdigest()

    def close(self, handle):
        if not self.k.CloseHandle(handle):
            raise SealError("startup_input_handle_release_failed", winerror=ctypes.get_last_error())


def _make_api():
    return _WindowsApi()


def capability_report() -> dict:
    return {
        "status": "native_unqualified",
        "native_seal_enabled": False,
        "input_lease_implementation": "retain_only_read_only_from_birth",
        "rejected_legacy_keeper": "birth_owner_rights_write_dac_not_granted",
        "ordinary_file_birth_and_exclusive_read": "synthetic_observed",
        "atomic_namespace_os_qualification": "pending",
        "cad_qualification": "not_tested",
        "privileged_recovery_implemented": False,
    }


def create_new(parent, payload, *, name="identity.vbs"):
    """Fail before any filesystem/native access; no caller flag enables it."""
    raise SealError("atomic_startup_input_seal_unqualified")


class _Basic(ctypes.Structure):
    # Documented PUBLIC_OBJECT_BASIC_INFORMATION. Reserved fields stay opaque.
    _fields_ = [
        ("Attributes", w.ULONG),
        ("GrantedAccess", w.ULONG),
        ("HandleCount", w.ULONG),
        ("PointerCount", w.ULONG),
        ("Reserved", w.ULONG * 10),
    ]


def _native_granted_access(handle):
    if os.name != "nt":
        raise SealError("granted_access_observation_requires_windows")
    query = ctypes.WinDLL("ntdll").NtQueryObject
    query.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.ULONG, ctypes.POINTER(w.ULONG))
    query.restype = w.LONG
    value, returned = _Basic(), w.ULONG()
    status = int(
        query(handle, 0, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(returned))
    )
    return status, int(value.GrantedAccess), int(returned.value)


def query_granted_access(handle) -> dict:
    """Explicit read-only query of a current-process handle, not requested access.

    Callers must already own the synthetic handle. No PID lookup, duplication,
    object-name output, policy change or assumption about another process occurs.
    A nonzero NTSTATUS never exports an unqualified buffer's access mask.
    """
    if type(handle) is not int or handle <= 0:
        raise SealError("owned_current_process_handle_required")
    status, granted, returned = _native_granted_access(handle)
    complete = status == 0 and returned >= ctypes.sizeof(_Basic)
    return {
        "status": "observed" if complete else "incomplete",
        "ntstatus": status,
        "granted_access": granted if complete else None,
        "returned_bytes": returned,
        "native_seal_enabled": False,
    }


def safe_api_error(error, function) -> dict:
    """Numeric error facts only; arbitrary error text/args are never exported."""
    allowed = {
        "CreateDirectoryW",
        "CreateFileW",
        "SetSecurityInfo",
        "GetSecurityInfo",
        "NtQueryObject",
        "ReOpenFile",
        "SetFileInformationByHandle",
        "CloseHandle",
    }
    winerror = getattr(error, "winerror", None)
    number = getattr(error, "errno", None)
    arguments = getattr(error, "args", ())
    fallback = (
        arguments[0]
        if isinstance(arguments, tuple) and arguments and type(arguments[0]) is int
        else None
    )
    return {
        "function": function if function in allowed else "native_operation",
        "winerror": winerror if type(winerror) is int else None,
        "errno": number if type(number) is int else None,
        "args0": fallback,
    }


def evaluate_probe(facts) -> dict:
    """Pure fixture accounting, never a native permission/admission receipt."""
    access = facts.get("keeper_granted_access")
    keeper = type(access) is int and bool(access & WRITE_DAC) and not bool(access & ~0x00160080)
    forbidden = ("write", "delete", "write_dac", "write_owner")
    denied = all(
        type(facts.get(name + "_reopen_error")) is int and facts[name + "_reopen_error"] == 5
        for name in forbidden
    )
    complete = all(
        facts.get(name) is True
        for name in (
            "security_only_create_new_succeeded",
            "independent_exclusive_writer_succeeded",
            "actual_full_readback_hash_verified",
            "seal_using_original_keeper_succeeded",
            "sealed_dacl_exact",
            "file_id_unchanged",
            "data_handles_closed",
            "sdk_style_read_share_zero_succeeded",
            "parent_and_ancestors_pinned",
            "namespace_attack_cases_rejected",
        )
    )
    return {
        "keeper_write_dac_observed_without_data_or_delete": keeper,
        "all_forbidden_reopens_denied_by_acl": denied,
        "protection_pass": bool(keeper and denied and complete),
        "native_seal_enabled": False,
        "scope": "fixture_observations_only",
    }
