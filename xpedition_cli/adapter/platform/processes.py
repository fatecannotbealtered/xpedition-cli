"""Running Windows processes, read without COM: which Xpedition programs run, whether a
process is still alive, and which process owns a window.

The process list comes from a Toolhelp32 snapshot. Its enumeration ends only with
ERROR_NO_MORE_FILES; any other failure means the list is incomplete, and an incomplete list
cannot show that a program is not running, so it raises instead of returning less.
"""
from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from typing import Any

from ..._driver import AdapterError
from ..._driver.records import pid_alive

TH32CS_SNAPPROCESS = 0x2
ERROR_NO_MORE_FILES = 18
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259  # the exit code of a process that runs
PROCESS_TERMINATE = 0x0001
FILETIME_PER_SECOND = 10_000_000


@dataclass(frozen=True)
class ProcessInfo:
    """One process. ``start_time`` is in seconds, as the runtime's session IDs count it; 0 and an
    empty ``path`` when Windows does not let this process read them."""

    pid: int
    path: str
    start_time: int
    parent_pid: int = 0


@functools.lru_cache(maxsize=1)
def _entry_type() -> Any:
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
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

    return ProcessEntry32W


def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    entry = ctypes.POINTER(_entry_type())
    for name, args, result in (
        ("CreateToolhelp32Snapshot", (wintypes.DWORD, wintypes.DWORD), wintypes.HANDLE),
        ("Process32FirstW", (wintypes.HANDLE, entry), wintypes.BOOL),
        ("Process32NextW", (wintypes.HANDLE, entry), wintypes.BOOL),
        ("OpenProcess", (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
        ("QueryFullProcessImageNameW", (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)),
         wintypes.BOOL),
        ("GetProcessTimes", (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4, wintypes.BOOL),
        ("GetExitCodeProcess", (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)), wintypes.BOOL),
        ("CloseHandle", (wintypes.HANDLE,), wintypes.BOOL),
    ):
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def _last_error() -> int:
    import ctypes

    return ctypes.get_last_error()


def _clear_last_error() -> None:
    import ctypes

    ctypes.set_last_error(0)


def _snapshot(api: Any) -> list[tuple[int, int, str]]:
    """Every process as ``(pid, parent_pid, image name)``."""
    import ctypes

    handle = api.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not handle or handle in (-1, ctypes.c_void_p(-1).value):  # INVALID_HANDLE_VALUE
        raise AdapterError("E_NATIVE_FAILURE", f"The process list could not be read (Windows error {_last_error()}).")
    rows: list[tuple[int, int, str]] = []

    def advance(function: Any, entry: Any) -> bool:
        _clear_last_error()
        more = function(handle, ctypes.byref(entry))
        if not more:
            error = _last_error()  # read at once: the next call can change it
            if error != ERROR_NO_MORE_FILES:
                raise AdapterError("E_NATIVE_FAILURE", f"The process list could not be read completely (Windows error {error}).")
        return bool(more)

    try:
        entry = _entry_type()()
        entry.size = ctypes.sizeof(entry)
        more = advance(api.Process32FirstW, entry)
        while more:
            rows.append((int(entry.pid), int(entry.parent_pid), str(entry.exe)))
            more = advance(api.Process32NextW, entry)
    finally:
        api.CloseHandle(handle)
    return rows


def _identity(api: Any, pid: int) -> tuple[str, int] | None:
    """The executable path and the start time in seconds of a process, or ``("", 0)`` when it
    cannot be opened. None for a process that has ended: the snapshot still lists an ended
    process while another one holds a handle to it, as a quitting Designer for a while."""
    import ctypes
    from ctypes import wintypes

    handle = api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "", 0
    try:
        code = wintypes.DWORD()
        if api.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != STILL_ACTIVE:
            return None
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        path = buffer.value if api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ""
        times = [wintypes.FILETIME() for _ in range(4)]
        start = 0
        if api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            start = ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) // FILETIME_PER_SECOND
        return path, start
    finally:
        api.CloseHandle(handle)


def processes(image_name: str) -> list[ProcessInfo]:
    """The running processes whose image is ``image_name`` (``ExpeditionPCB.exe``; any case)."""
    if os.name != "nt":
        return []
    api = _kernel32()
    want = image_name.lower()
    found: list[ProcessInfo] = []
    for pid, parent, exe in _snapshot(api):
        if exe.lower() == want:
            identity = _identity(api, pid)
            if identity is None:
                continue
            path, start = identity
            found.append(ProcessInfo(pid=pid, path=path, start_time=start, parent_pid=parent))
    return found


def start_time(pid: int) -> int | None:
    """When a process started, in seconds; 0 when Windows does not tell, None when it has ended."""
    if os.name != "nt" or pid <= 0:
        return 0
    identity = _identity(_kernel32(), int(pid))
    return None if identity is None else identity[1]


def descendants(pid: int) -> list[int]:
    """The processes ``pid`` started, and theirs: a launcher's real program, for one."""
    if os.name != "nt":
        return []
    rows = _snapshot(_kernel32())
    children: dict[int, list[int]] = {}
    for child, parent, _ in rows:
        if child != parent:
            children.setdefault(parent, []).append(child)
    found: list[int] = []
    seen = {pid}
    queue = [pid]
    while queue:
        for child in children.get(queue.pop(0), []):
            if child not in seen:  # a reused PID can make the parent links loop
                seen.add(child)
                found.append(child)
                queue.append(child)
    return found


def alive(pid: int) -> bool:
    """Whether the process still runs; one Windows refuses to open still exists."""
    return pid_alive(int(pid))


def end(pid: int) -> bool:
    """End a process as Task Manager does (TerminateProcess); whether Windows took the request."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.restype = wintypes.HANDLE
    api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    api.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = api.OpenProcess(PROCESS_TERMINATE, False, int(pid))
    if not handle:
        return False
    try:
        return bool(api.TerminateProcess(handle, 1))
    finally:
        api.CloseHandle(handle)


def window_pid(hwnd: int) -> int:
    """The process that owns a window (GetWindowThreadProcessId), or 0."""
    if os.name != "nt" or not hwnd:
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    pid = wintypes.DWORD(0)
    if not user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid)):
        return 0
    return int(pid.value)
