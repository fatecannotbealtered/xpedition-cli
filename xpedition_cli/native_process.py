"""Read-only Windows process discovery for duplicate-launch and identity guards."""

from __future__ import annotations

import copy
import math
import os
import time
from pathlib import Path

EXIT_OBSERVATION_SECONDS = 60.0
STARTUP_LINEAGE_VERSION = "native-start-generation-v1"
MAX_STARTUP_PROCESSES = 64
MAX_STARTUP_CHAIN = 2


class StartupOwnershipError(ValueError):
    """A fixed, public-safe explanation of an unchanged ownership refusal."""

    _MESSAGES = {
        "startup_provenance_incomplete": "Recorded startup ownership proof is incomplete",
        "startup_provenance_other_application": (
            "Recorded startup chain belongs to another actual application"
        ),
        "startup_provenance_chain_invalid": (
            "Recorded launcher relation fails the existing installed-process validator"
        ),
    }

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(self._MESSAGES[reason])


def _normalized(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class ProcessHandle:
    """Pin one Windows process generation, without COM, signals or termination."""

    def __init__(
        self, domain: str, pid: int, expected_identity: dict | None = None, *, check_name=True
    ):
        if os.name != "nt":
            raise OSError("Native process identity requires Windows")
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wintypes = ctypes, wintypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("OpenProcess", (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
            ("CloseHandle", (wintypes.HANDLE,), wintypes.BOOL),
            ("WaitForSingleObject", (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD),
            (
                "QueryFullProcessImageNameW",
                (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)),
                wintypes.BOOL,
            ),
            (
                "GetProcessTimes",
                (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4,
                wintypes.BOOL,
            ),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.OpenProcess(0x00100000 | 0x1000, False, int(pid))
        if not self.handle:
            error = ctypes.get_last_error()
            raise OSError(error, "Cannot pin native process identity", None, error)
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self.api.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)):
                raise OSError(ctypes.get_last_error(), "Cannot read native executable")
            executable = buffer.value
            name = {
                "schematic": "viewdraw.exe",
                "pcb": "expeditionpcb.exe",
                "library": "librarymanager.exe",
            }[domain]
            if check_name and Path(executable).name.lower() != name:
                raise ValueError("Requested PID is not the named native application")
            creation, _exit = self._times()
            self.identity = {
                "pid": int(pid),
                "executable": executable,
                "creation_time_100ns": str(creation),
            }
            if expected_identity is not None and not same_identity(
                self.identity, expected_identity
            ):
                raise ValueError("Native process generation or executable changed")
        except BaseException:
            self.close()
            raise

    def _times(self) -> tuple[int, int]:
        values = [self.wintypes.FILETIME() for _ in range(4)]
        if not self.api.GetProcessTimes(
            self.handle, *(self.ctypes.byref(value) for value in values)
        ):
            raise OSError(self.ctypes.get_last_error(), "Cannot read native process times")
        return tuple((value.dwHighDateTime << 32) | value.dwLowDateTime for value in values[:2])

    def exited(self) -> bool:
        reply = self.api.WaitForSingleObject(self.handle, 0)
        if reply not in (0, 258):
            raise OSError(self.ctypes.get_last_error(), "Native process wait failed")
        return reply == 0

    def require_spawned(self, process) -> None:
        """Corroborate the PID-opened pin with our actual CPython Popen handle."""
        self.api.GetProcessId.argtypes = (self.wintypes.HANDLE,)
        self.api.GetProcessId.restype = self.wintypes.DWORD
        source = getattr(process, "_handle", None)
        if source is None or self.api.GetProcessId(int(source)) != self.identity["pid"]:
            raise ValueError("Original Popen process handle is unavailable or belongs elsewhere")
        times = [self.wintypes.FILETIME() for _ in range(4)]
        if not self.api.GetProcessTimes(
            int(source), *(self.ctypes.byref(value) for value in times)
        ):
            raise OSError(self.ctypes.get_last_error(), "Cannot read original Popen generation")
        birth = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        if str(birth) != self.identity["creation_time_100ns"]:
            raise ValueError("Spawned PID was reused before launcher ownership could be pinned")

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def same_identity(observed: dict, expected: dict) -> bool:
    """Neither an absent creation time nor a PID alone establishes ownership."""
    try:
        return (
            int(observed["pid"]) == int(expected["pid"])
            and str(observed["creation_time_100ns"]).isdecimal()
            and int(observed["creation_time_100ns"]) > 0
            and str(observed["creation_time_100ns"]) == str(expected["creation_time_100ns"])
            and bool(observed["executable"])
            and _normalized(observed["executable"]) == _normalized(expected["executable"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def process_identity(domain: str, pid: int | str) -> dict:
    with ProcessHandle(domain, int(pid)) as pinned:
        if pinned.exited():
            raise ValueError("Native process has already exited")
        return dict(pinned.identity)


def _startup_snapshot(domain: str) -> dict[int, dict]:
    from .native_release_preflight import _inventory

    rows = processes(domain)
    if not isinstance(rows, list) or len(rows) > MAX_STARTUP_PROCESSES:
        raise ValueError("Native startup process observation exceeds its bound")

    def generation(pid):
        identity = process_identity(domain, pid)
        return {
            "pid": identity["pid"],
            "birth_filetime_100ns": identity["creation_time_100ns"],
            "executable": identity["executable"],
            "alive": True,
        }

    return {
        row["pid"]: {
            "identity": {
                "pid": row["pid"],
                "executable": row["executable"],
                "creation_time_100ns": row["birth_filetime_100ns"],
            },
            "parent_pid": row["parent_pid"],
        }
        for row in _inventory(domain, lambda _domain: rows, generation)
    }


def _startup_chain(domain: str, launcher: dict, leaf: dict, rows: dict[int, dict]) -> dict | None:
    from .native_com_adapter import AdapterError
    from .native_release_preflight import _map_processes

    inventory = [
        {
            "pid": item["identity"]["pid"],
            "parent_pid": item["parent_pid"],
            "executable": _normalized(item["identity"]["executable"]),
            "birth_filetime_100ns": item["identity"]["creation_time_100ns"],
        }
        for item in rows.values()
    ]
    try:
        roles = _map_processes(inventory, leaf["pid"], domain)
    except AdapterError:
        return None
    if roles["launcher"] is None and same_identity(leaf, launcher):
        return {"process_chain": [leaf], "parent_edges": []}
    if roles["launcher"] == launcher["pid"] and same_identity(
        rows[launcher["pid"]]["identity"], launcher
    ):
        return {
            "process_chain": [launcher, leaf],
            "parent_edges": [{"pid": leaf["pid"], "parent_pid": launcher["pid"]}],
        }
    return None


def capture_startup_seed(domain: str, launcher: ProcessHandle) -> dict:
    """While the original launcher is pinned/alive, prove its observed descendants.

    Two full OS snapshots and fresh creation identities protect each parent edge.
    Reuse the release preflight's finite installed common->wg/wv/lm mapping.
    An exited/unreadable launcher or an unobserved intermediate process
    leaves startup unproved, rather than using time/name similarity as ownership.
    """
    if launcher.exited() or not same_identity(launcher.identity, launcher.identity):
        raise ValueError("Original launcher ended before startup provenance was observable")
    first, second = _startup_snapshot(domain), _startup_snapshot(domain)
    root = launcher.identity
    if (
        any(
            root["pid"] not in snapshot
            or not same_identity(snapshot[root["pid"]]["identity"], root)
            for snapshot in (first, second)
        )
        or launcher.exited()
    ):
        raise ValueError("Original launcher generation changed during startup observation")
    candidates = []
    for row in first.values():
        chain = _startup_chain(domain, root, row["identity"], first)
        if chain is not None and chain == _startup_chain(domain, root, row["identity"], second):
            candidates.append(chain)
    return {
        "version": STARTUP_LINEAGE_VERSION,
        "domain": domain,
        "launcher_identity": dict(root),
        "candidates": sorted(candidates, key=lambda row: row["process_chain"][-1]["pid"]),
        "source": "pinned original launcher and two complete Toolhelp/GetProcessTimes snapshots",
    }


def resolve_startup_process(domain: str, seed: dict, application: dict) -> dict:
    """Bind the actual COM process to this launch, never to the launcher PID alone."""
    if (
        not isinstance(seed, dict)
        or seed.get("version") != STARTUP_LINEAGE_VERSION
        or seed.get("domain") != domain
        or not same_identity(
            seed.get("launcher_identity") or {}, seed.get("launcher_identity") or {}
        )
        or type(application.get("pid")) is not int
        or application["pid"] <= 0
    ):
        raise ValueError("This startup has no complete original launcher generation seed")
    actual = process_identity(domain, application["pid"])
    com_image = (application.get("public_fields") or {}).get("FullName")
    if (
        not isinstance(com_image, str)
        or not com_image
        or _normalized(com_image) != _normalized(actual["executable"])
    ):
        raise ValueError("COM application executable differs from its native OS generation")
    if any(
        isinstance(candidate, dict)
        and isinstance(candidate.get("process_chain"), list)
        and candidate["process_chain"]
        and candidate["process_chain"][-1].get("pid") == actual["pid"]
        and not same_identity(candidate["process_chain"][-1], actual)
        for candidate in seed.get("candidates", [])
    ):
        raise ValueError("A startup-observed application PID was reused")

    def match(value):
        for candidate in value.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            path, edges = candidate.get("process_chain"), candidate.get("parent_edges")
            if (
                not isinstance(path, list)
                or not 1 <= len(path) <= MAX_STARTUP_CHAIN
                or not isinstance(edges, list)
                or len(edges) != len(path) - 1
                or not same_identity(path[0], value["launcher_identity"])
                or not same_identity(path[-1], actual)
                or len({str(row.get("pid")) for row in path}) != len(path)
                or any(not same_identity(row, row) for row in path)
                or any(
                    edges[index]
                    != {"pid": path[index + 1]["pid"], "parent_pid": path[index]["pid"]}
                    or int(path[index]["creation_time_100ns"])
                    > int(path[index + 1]["creation_time_100ns"])
                    for index in range(len(edges))
                )
            ):
                continue
            rows = {
                item["pid"]: {
                    "identity": item,
                    "parent_pid": 0 if index == 0 else path[index - 1]["pid"],
                }
                for index, item in enumerate(path)
            }
            if _startup_chain(domain, value["launcher_identity"], path[-1], rows) != candidate:
                continue
            return candidate
        return None

    chain = match(seed)
    if chain is None:
        # A later registered child is accepted only while the exact original
        # launcher is still pin-able/alive. A vanished seed is never recreated.
        root = seed["launcher_identity"]
        with ProcessHandle(domain, root["pid"], root) as pinned:
            chain = match(capture_startup_seed(domain, pinned))
    if chain is None or not same_identity(process_identity(domain, actual["pid"]), actual):
        raise ValueError("Actual COM process is not a proven descendant of this launch")
    return {
        "process_identity": actual,
        "startup_provenance": {
            "version": STARTUP_LINEAGE_VERSION,
            "domain": domain,
            "launcher_identity": seed["launcher_identity"],
            **copy.deepcopy(chain),
            "ownership": "proven_this_start",
            "source": "COM PID/image plus original pinned launcher parent/generation proof",
        },
    }


def owned_startup_launchers(provenance: dict | None, actual: dict) -> list[dict]:
    """Only a proven original-start chain grants ownership of a parent launcher."""
    if provenance is None:
        return []  # Explicit attach owns the actual instance; external launcher is unknown.
    if (
        not isinstance(provenance, dict)
        or provenance.get("version") != STARTUP_LINEAGE_VERSION
        or provenance.get("domain") not in {"pcb", "schematic", "library"}
        or provenance.get("ownership") != "proven_this_start"
        or not isinstance(provenance.get("process_chain"), list)
        or not 1 <= len(provenance["process_chain"]) <= MAX_STARTUP_CHAIN
    ):
        raise StartupOwnershipError("startup_provenance_incomplete")
    path = provenance["process_chain"]
    if not same_identity(path[-1], actual):
        raise StartupOwnershipError("startup_provenance_other_application")
    rows = {
        item["pid"]: {"identity": item, "parent_pid": 0 if index == 0 else path[index - 1]["pid"]}
        for index, item in enumerate(path)
        if isinstance(item, dict) and same_identity(item, item)
    }
    expected = _startup_chain(
        provenance.get("domain"), provenance.get("launcher_identity") or {}, actual, rows
    )
    if expected is None or expected.get("parent_edges") != provenance.get("parent_edges"):
        raise StartupOwnershipError("startup_provenance_chain_invalid")
    return copy.deepcopy(path[:-1])


def observe_exit(
    domain: str,
    pid: str,
    timeout: float = EXIT_OBSERVATION_SECONDS,
    expected_identity: dict | None = None,
) -> dict:
    """Bounded OS-only observation after Quit; never retry COM or terminate CAD."""
    if not math.isfinite(timeout) or timeout < 0 or timeout > EXIT_OBSERVATION_SECONDS:
        raise ValueError("Exit observation budget is outside its public bound")
    started = time.monotonic()
    observations = 0
    if expected_identity is not None:
        if (
            not isinstance(expected_identity, dict)
            or not str(pid).isdecimal()
            or int(pid) <= 0
            or str(expected_identity.get("pid")) != str(pid)
            or not same_identity(expected_identity, expected_identity)
            or not isinstance(expected_identity.get("executable"), str)
            or not os.path.isabs(expected_identity["executable"])
        ):
            raise ValueError("Exit observation requires the exact original process identity")
        if os.name != "nt":
            raise OSError("Native process exit observation requires Windows")
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("OpenProcess", (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
            ("GetProcessId", (wintypes.HANDLE,), wintypes.DWORD),
            (
                "GetProcessTimes",
                (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4,
                wintypes.BOOL,
            ),
            ("WaitForSingleObject", (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD),
            (
                "QueryFullProcessImageNameW",
                (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)),
                wintypes.BOOL,
            ),
            ("CloseHandle", (wintypes.HANDLE,), wintypes.BOOL),
        ):
            function = getattr(api, name)
            function.argtypes, function.restype = args, result

        def failed(message, error=None):
            error = ctypes.get_last_error() if error is None else error
            return OSError(error, message, None, error)

        def generation(handle):
            nonlocal observations
            observed_pid = int(api.GetProcessId(handle))
            if observed_pid == 0:
                raise failed("Original exit HANDLE PID is unobservable")
            if observed_pid != int(pid):
                raise ValueError("Original exit HANDLE belongs to another PID")
            times = [wintypes.FILETIME() for _ in range(4)]
            if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                raise failed("Original process birth is unobservable")
            birth = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
            if int(birth) <= 0:
                raise ValueError("Original process birth is unobservable")
            waited = int(api.WaitForSingleObject(handle, 0))
            if waited not in (0, 258):
                raise failed("Original process wait is unobservable")
            observations += 1
            return {
                "pid": int(pid),
                "creation_time_100ns": birth,
                "executable": None,
            }, waited

        def answer(observed, waited, *, image_observation, image_error=None):
            reused = observed["creation_time_100ns"] != str(
                expected_identity["creation_time_100ns"]
            )
            ended = reused or waited == 0
            return {
                "pid": None if ended else int(pid),
                "process_absence_observed": ended,
                "process_identity": copy.deepcopy(expected_identity),
                "exit_observation": {
                    "target_pid": str(pid),
                    "source": (
                        "pinned Windows HANDLE/GetProcessId/GetProcessTimes/WaitForSingleObject"
                    ),
                    "observations": observations,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "budget_seconds": timeout,
                    "original_generation_ended": ended,
                    "pid_reused": reused,
                    "wait_result": waited,
                    "observed_process_identity": observed,
                    "image_observation": image_observation,
                    "image_query_winerror": image_error,
                },
            }

        handle = api.OpenProcess(0x00101000, False, int(pid))
        if not handle:
            error = ctypes.get_last_error()
            if error != 87:
                raise failed("Cannot observe the original native process", error)
            return {
                "pid": None,
                "process_absence_observed": True,
                "process_identity": copy.deepcopy(expected_identity),
                "exit_observation": {
                    "target_pid": str(pid),
                    "source": "OpenProcess/ERROR_INVALID_PARAMETER",
                    "observations": 1,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "budget_seconds": timeout,
                    "original_generation_ended": True,
                    "pid_reused": False,
                    "observed_process_identity": None,
                    "image_observation": "unobserved",
                    "image_query_winerror": None,
                },
            }
        try:
            while True:
                observed, waited = generation(handle)
                if (
                    observed["creation_time_100ns"] != str(expected_identity["creation_time_100ns"])
                    or waited == 0
                ):
                    # A terminated object can outlive its executable-name
                    # getter while another original HANDLE retains the object.
                    return answer(observed, waited, image_observation="unobserved_after_exit")
                size = wintypes.DWORD(32768)
                image = ctypes.create_unicode_buffer(size.value)
                if not api.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                    image_error = ctypes.get_last_error()
                    # It can exit between the wait and image query. Recheck
                    # this same HANDLE; an image error alone proves nothing.
                    observed, waited = generation(handle)
                    if (
                        observed["creation_time_100ns"]
                        != str(expected_identity["creation_time_100ns"])
                        or waited == 0
                    ):
                        return answer(
                            observed,
                            waited,
                            image_observation="unavailable",
                            image_error=image_error,
                        )
                    raise failed("Live original native executable is unobservable", image_error)
                observed["executable"] = image.value
                if not same_identity(observed, expected_identity):
                    raise ValueError("Live native process generation or executable changed")
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    return answer(observed, waited, image_observation="observed")
                time.sleep(min(0.2, timeout - elapsed))
        finally:
            if not api.CloseHandle(handle):
                raise failed("Original exit observation HANDLE could not be released")
    while True:
        running = any(str(item["pid"]) == str(pid) for item in processes(domain))
        observations += 1
        elapsed = time.monotonic() - started
        if not running or elapsed >= timeout:
            return {
                "pid": None if not running else int(pid),
                "process_absence_observed": not running,
                "exit_observation": {
                    "target_pid": str(pid),
                    "source": "Toolhelp32",
                    "observations": observations,
                    "elapsed_seconds": round(elapsed, 3),
                    "budget_seconds": timeout,
                },
            }
        time.sleep(min(0.2, timeout - elapsed))


def processes(domain: str) -> list[dict]:
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    name = {
        "schematic": "viewdraw.exe",
        "pcb": "expeditionpcb.exe",
        "library": "librarymanager.exe",
    }[domain]

    class Entry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD),
            ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD),
            ("threads", wintypes.DWORD),
            ("parent_pid", wintypes.DWORD),
            ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
            ("exe", wintypes.WCHAR * 260),
        ]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = api.Process32NextW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(Entry),
    )
    api.Process32FirstW.restype = api.Process32NextW.restype = wintypes.BOOL
    api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    api.OpenProcess.restype = wintypes.HANDLE
    api.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot inspect native processes before launch")
    result = []

    def advance(function, entry):
        # FALSE is terminal only with the documented ERROR_NO_MORE_FILES (18).
        # Capture immediately: subsequent CloseHandle/image queries can change it.
        ctypes.set_last_error(0)
        more = function(snapshot, ctypes.byref(entry))
        if not more:
            error = ctypes.get_last_error()
            if error != 18:
                raise OSError(error, "Native process enumeration is incomplete")
        return bool(more)

    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        more = advance(api.Process32FirstW, entry)
        while more:
            if entry.exe.lower() == name:
                path = None
                handle = api.OpenProcess(0x1000, False, entry.pid)
                if handle:
                    try:
                        length = wintypes.DWORD(32768)
                        buffer = ctypes.create_unicode_buffer(length.value)
                        if api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                            path = buffer.value
                    finally:
                        api.CloseHandle(handle)
                result.append(
                    {
                        "pid": entry.pid,
                        "parent_pid": entry.parent_pid,
                        "executable": path,
                        "process_name": entry.exe,
                        "source": "Toolhelp32/QueryFullProcessImageNameW",
                    }
                )
            more = advance(api.Process32NextW, entry)
    finally:
        api.CloseHandle(snapshot)
    return result


def unique_executable_pid(domain: str, executable: str) -> dict | None:
    if not executable.lower().endswith(".exe") or not Path(executable).is_absolute():
        return None
    normalized = os.path.normcase(os.path.abspath(executable))
    matches = [
        item
        for item in processes(domain)
        if item["executable"]
        and os.path.normcase(os.path.abspath(item["executable"])) == normalized
    ]
    return matches[0] if len(matches) == 1 else None
