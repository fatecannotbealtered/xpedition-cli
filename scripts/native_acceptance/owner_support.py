"""Offline support for a bounded native-method owner, not a public CLI backend.

Import performs no COM, process, registry or filesystem observation. Log sampling
is deliberately incomplete; protected source pins remain strict. Nothing here
saves, closes, kills, unlocks or selects an application through the ROT.
"""

from __future__ import annotations

import ctypes
import datetime
import hashlib
import ntpath
import os
from pathlib import Path

LAYOUT_PROGID = "MGCPCB.ExpeditionPCBApplication.151"
LAYOUT_CLSID = "{67055703-6CCD-1014-8B2B-F78B135EB980}"


class OwnerPreflightError(RuntimeError):
    """A required dependency or frozen input was not established."""


class CriticalInputDrift(OwnerPreflightError):
    pass


def require_callables(requirements):
    """Check the actual imported objects, without calling them or creating COM."""
    checked = []
    for module, names in requirements:
        module_name = getattr(module, "__name__", type(module).__name__)
        for name in names:
            qualified = f"{module_name}.{name}"
            if not callable(getattr(module, name, None)):
                raise OwnerPreflightError(f"Required runtime callable unavailable: {qualified}")
            checked.append(qualified)
    return checked


def registered_layout_factory(pythoncom, registry_value):
    """Consume the caller's readonly HKCR64 .151\\CLSID default REG_SZ value.

    The caller must record the key, registry view and value type. This does not
    resolve CurVer or call the unavailable pythoncom.CLSIDFromProgID helper.
    """
    require_callables([(pythoncom, ("MakeIID",))])
    if not isinstance(registry_value, str) or registry_value.casefold() != LAYOUT_CLSID.casefold():
        raise OwnerPreflightError(
            "Versioned HKCR64 Layout CLSID differs from frozen official CLSID"
        )
    clsid = pythoncom.MakeIID(LAYOUT_CLSID)
    if str(clsid).casefold() != LAYOUT_CLSID.casefold():
        raise OwnerPreflightError("MakeIID did not preserve the fixed official Layout CLSID")
    return clsid


def child_environment(declared, inherited=None):
    """Build a child-only environment, preserving existing licensing verbatim.

    No environment values are logged, no registry is searched for license
    fallbacks, and no work, temporary or installation directory is created.
    """
    result = dict(os.environ if inherited is None else inherited)

    def put(name, value, *, allow_override=False):
        value = str(value)
        existing = next((key for key in result if key.casefold() == name.casefold()), None)
        if existing is not None:
            if allow_override:
                result[existing] = value
            elif result[existing] != value:
                raise OwnerPreflightError(f"Inherited environment conflicts with declared {name}")
        else:
            result[name] = value

    for name, value in declared.items():
        put(name, value, allow_override=name.casefold() in {"temp", "tmp", "pythonioencoding"})
    try:
        release = Path(declared["XPEDITION_SDD_HOME"]).resolve()
        work = Path(declared["XPEDITION_MGC_WD"]).resolve()
        temporary = str(declared["TEMP"])
    except (KeyError, TypeError) as exc:
        raise OwnerPreflightError("Required declared installation/work/TEMP field missing") from exc
    if not release.is_dir() or not work.is_dir():
        raise OwnerPreflightError("Declared installation/work directory missing")
    additions = {
        "SDD_HOME": str(release),
        "SDD_PLATFORM": "win64",
        "SDD_VERSION": release.parent.name,
        "SDD_WG": "wg",
        "SDD_WV": "wv",
        "SDD_DX": "dx",
        "VCO": "ixw",
        "TARGET": str(release.parent),
        "MENTOR_ROOT": str(release.parent),
        "MGC_WD": str(work),
        "WDIR": os.pathsep.join((str(work), str(release / "standard"))),
        "MGC_TMPDIR": temporary,
    }
    native_home = release.parent / "MGC_HOME.ixw"
    if native_home.is_dir():
        additions.update(MGC_HOME=str(native_home), SDD_MGC_HOME=str(native_home))
    for name, value in additions.items():
        put(name, value)
    paths = [
        release / suffix
        for suffix in ("common/win64/bin", "common/win64/lib", "wg/win64/bin", "wg/win64/lib")
    ]
    path_key = next((key for key in result if key.casefold() == "path"), "PATH")
    result[path_key] = os.pathsep.join(
        [str(path) for path in paths if path.is_dir()] + [result.get(path_key, "")]
    )
    return result


def _now():
    return datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")


def _stat(value):
    return {
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
        "device": value.st_dev,
        "inode": value.st_ino,
        "mode": value.st_mode,
    }


def _error(phase, exc):
    return {
        "phase": phase,
        "type": type(exc).__name__,
        "errno": getattr(exc, "errno", None),
        "message": str(exc),
    }


def _read_sample(source, max_bytes):
    with source.open("rb") as stream:
        before = _stat(os.fstat(stream.fileno()))
        data = stream.read(max_bytes)
        after = _stat(os.fstat(stream.fileno()))
    return data, before, after


def capture_live_log(source, destination, *, max_bytes=8 * 1024 * 1024):
    """Persist actual sampled bytes; ordinary source/output races never escape.

    This evidence is never an atomic live-source snapshot. Existing artifacts
    are not replaced. A logging failure must not interrupt a separately
    authorized UnLoad whose owner and loaded-design identity remain established.
    """
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    source, destination = Path(source), Path(destination)
    row = {
        "schema": "noncritical-live-log-sample/v1",
        "source": str(source),
        "destination": str(destination),
        "started_at": _now(),
        "complete": False,
        "critical_input": False,
        "reason_codes": ["live_source_atomic_snapshot_not_established"],
        "errors": [],
        "source_stat_before": None,
        "source_stat_after": None,
        "descriptor_stat_before": None,
        "descriptor_stat_after": None,
        "sample_bytes_read": None,
        "sample_sha256": None,
        "captured": None,
        "earlier_inventory_sha_used": False,
        "sample_persisted_exact": False,
    }
    try:
        row["source_stat_before"] = _stat(source.stat())
    except Exception as exc:
        row["errors"].append(_error("source_stat_before", exc))
        row["reason_codes"].append("source_stat_before_failed")
    data = None
    try:
        data, row["descriptor_stat_before"], row["descriptor_stat_after"] = _read_sample(
            source, max_bytes
        )
        row["sample_bytes_read"] = len(data)
        row["sample_sha256"] = hashlib.sha256(data).hexdigest()
    except Exception as exc:
        row["errors"].append(_error("source_read", exc))
        row["reason_codes"].append("source_read_failed")
    try:
        row["source_stat_after"] = _stat(source.stat())
    except Exception as exc:
        row["errors"].append(_error("source_stat_after", exc))
        row["reason_codes"].append("source_stat_after_failed")
    pairs = [
        (row["source_stat_before"], row["source_stat_after"]),
        (row["descriptor_stat_before"], row["descriptor_stat_after"]),
        (row["source_stat_before"], row["descriptor_stat_before"]),
        (row["descriptor_stat_after"], row["source_stat_after"]),
    ]
    for before, after in pairs:
        if before is None or after is None:
            continue
        if after["size"] > before["size"]:
            row["reason_codes"].append("source_grew_during_sample")
        if after["size"] < before["size"]:
            row["reason_codes"].append("source_shrank_during_sample")
        if before != after:
            row["reason_codes"].append("source_stat_changed_during_sample")
        if (
            before["inode"]
            and after["inode"]
            and (before["device"], before["inode"]) != (after["device"], after["inode"])
        ):
            row["reason_codes"].append("source_identity_changed_during_sample")
    if data is not None:
        sizes = [
            item["size"]
            for item in (
                row["source_stat_before"],
                row["source_stat_after"],
                row["descriptor_stat_before"],
                row["descriptor_stat_after"],
            )
            if item is not None
        ]
        if len(data) == max_bytes:
            row["reason_codes"].append("sample_read_limit_reached")
        if sizes and max(sizes) > len(data):
            row["reason_codes"].append("sample_does_not_cover_all_observed_source_bytes")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                if stream.write(data) != len(data):
                    raise OSError("Short write while saving log sample")
            persisted = destination.read_bytes()
            row["captured"] = {
                "path": str(destination.resolve()),
                "bytes": len(persisted),
                "sha256": hashlib.sha256(persisted).hexdigest(),
            }
            row["sample_persisted_exact"] = persisted == data
            if persisted != data:
                row["reason_codes"].append("captured_output_changed_or_incomplete")
        except Exception as exc:
            row["errors"].append(_error("capture_output", exc))
            row["reason_codes"].append("capture_output_failed")
    row["reason_codes"] = list(dict.fromkeys(row["reason_codes"]))
    row["ended_at"] = _now()
    return row


def strict_file_pin(reference):
    """Frozen inputs differ from live logs: unreadable or changed is fatal."""
    source = Path(reference["path"])
    try:
        before = _stat(source.stat())
        data = source.read_bytes()
        after = _stat(source.stat())
    except Exception as exc:
        raise CriticalInputDrift("Critical input unreadable: " + str(source)) from exc
    digest = hashlib.sha256(data).hexdigest()
    if before != after or len(data) != reference["bytes"] or digest != reference["sha256"]:
        raise CriticalInputDrift("Critical input changed: " + str(source))
    return {"path": str(source.resolve()), "bytes": len(data), "sha256": digest}


def _process_identity(row):
    pid, birth, executable = row.get("pid"), row.get("birth_filetime_100ns"), row.get("executable")
    if (
        type(pid) is not int
        or pid <= 0
        or not isinstance(birth, str)
        or not birth.isdecimal()
        or int(birth) <= 0
    ):
        raise OwnerPreflightError("Process PID/birth generation missing or invalid")
    if not isinstance(executable, str) or not ntpath.isabs(executable):
        raise OwnerPreflightError("Actual process executable path missing or invalid")
    return pid, birth, ntpath.normcase(ntpath.normpath(executable))


def classify_process_roles(before, after, returned, expected_worker, *, forbidden=()):
    """Validate the returned HWND's new worker; retain other launcher identities.

    Same-name launcher executables may live outside the pinned wg worker path.
    They do not establish ownership. PID reuse remains a rejection, including
    when its birth generation differs from a pre-activation process.
    """
    expected_worker = ntpath.normcase(ntpath.normpath(expected_worker))
    returned_key = _process_identity(returned)
    if type(returned.get("hwnd")) is not int or returned["hwnd"] <= 0:
        raise OwnerPreflightError("Returned HWND identity missing or invalid")
    prior = [_process_identity(row) for row in before]
    current = [_process_identity(row) for row in after]
    if len({item[0] for item in current}) != len(current):
        raise OwnerPreflightError("Duplicate current process PID")
    if returned_key[0] in {item[0] for item in prior}:
        raise OwnerPreflightError("Returned process PID existed before activation or was reused")
    if returned_key[0] in {_process_identity(row)[0] for row in forbidden}:
        raise OwnerPreflightError("Returned process is protected/forbidden")
    matches = [row for row, key in zip(after, current, strict=True) if key[0] == returned_key[0]]
    if len(matches) != 1 or _process_identity(matches[0]) != returned_key:
        raise OwnerPreflightError(
            "Returned HWND process generation/executable contradicts inventory"
        )
    if returned_key[2] != expected_worker:
        raise OwnerPreflightError(
            "Returned HWND does not belong to the pinned wg worker executable"
        )
    others = []
    for row, key in zip(after, current, strict=True):
        if key[0] == returned_key[0]:
            continue
        role = (
            "same_name_launcher_or_other_instance"
            if ntpath.basename(key[2]) == ntpath.basename(expected_worker)
            else "other_process"
        )
        others.append({"role": role, "process": dict(row)})
    return {
        "returned_worker": dict(matches[0]),
        "other_processes": others,
        "boundary": "OS identity only; COM and project identity require separate checks",
    }


class WindowsProcessObserver:
    """Readonly HWND-to-PID/generation/executable observer; no UI input."""

    def __init__(self):
        if os.name != "nt":
            raise OwnerPreflightError("Windows process observation requires Windows")
        from ctypes import wintypes as w

        self.w = w
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.GetWindowThreadProcessId.restype = w.DWORD
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
        self.kernel.GetProcessTimes.restype = w.BOOL
        self.kernel.QueryFullProcessImageNameW.argtypes = [
            w.HANDLE,
            w.DWORD,
            w.LPWSTR,
            ctypes.POINTER(w.DWORD),
        ]
        self.kernel.QueryFullProcessImageNameW.restype = w.BOOL
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        require_callables(
            [
                (self.user, ("GetWindowThreadProcessId",)),
                (
                    self.kernel,
                    ("OpenProcess", "GetProcessTimes", "QueryFullProcessImageNameW", "CloseHandle"),
                ),
            ]
        )

    def _pid(self, hwnd):
        pid = self.w.DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            raise ctypes.WinError(ctypes.get_last_error())
        return pid.value

    def observe_window(self, hwnd):
        """Read the process through its handle, then recheck the window mapping."""
        if type(hwnd) is not int or hwnd <= 0:
            raise OwnerPreflightError("A positive actual HWND is required")
        pid = self._pid(hwnd)
        observed = self.observe_pid(pid)
        if self._pid(hwnd) != pid:
            raise OwnerPreflightError("Window process mapping changed during observation")
        return {"hwnd": hwnd, **observed}

    def observe_pid(self, pid):
        """Read exact generation/exit FILETIMEs from one query-only handle.

        A nonzero observed exit time is an ended-process fact, not an arbitrary
        identity error. If its image is no longer readable, retain that failure
        beside the known PID/birth/exit fields. Live-process image failure still
        raises; an access error is never converted into an exit observation.
        """
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            raise OwnerPreflightError("A positive actual DWORD PID is required")
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            creation, exit_time, kernel, user = (self.w.FILETIME() for _ in range(4))
            if not self.kernel.GetProcessTimes(
                handle, *(ctypes.byref(item) for item in (creation, exit_time, kernel, user))
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            exit_ticks = (exit_time.dwHighDateTime << 32) | exit_time.dwLowDateTime
            observed = {
                "pid": pid,
                "executable": None,
                "alive": exit_ticks == 0,
                "birth_filetime_100ns": str(
                    (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                ),
                "exit_filetime_100ns": str(exit_ticks),
                "observation_errors": [],
            }
            try:
                size = self.w.DWORD(32768)
                executable = ctypes.create_unicode_buffer(size.value)
                if not self.kernel.QueryFullProcessImageNameW(
                    handle, 0, executable, ctypes.byref(size)
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                observed["executable"] = executable.value
            except Exception as exc:
                if exit_ticks == 0:
                    raise
                observed["observation_errors"].append(_error("process_image", exc))
        finally:
            if not self.kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        observed["process_handle_closed"] = True
        return observed
