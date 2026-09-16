from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .audit import config_dir


def _state_path() -> Path:
    return config_dir() / "session.json"


def _write_state(state: dict[str, Any]) -> None:
    path = _state_path()
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def record_native_start(result: dict[str, Any]) -> None:
    _write_state(
        {
            "backend": "native_xpedition",
            "state": "started",
            "session_id": f"native-{result.get('pid')}" if result.get("pid") else None,
            "pid": result.get("pid"),
            "executable": result.get("executable"),
            "domain": result.get("domain"),
            "xpedition_process": True,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )


def record_native_attach(result: dict[str, Any]) -> None:
    _write_state(
        {
            "backend": "native_xpedition",
            "state": "attached",
            "session_id": None,
            "pid": None,
            "domain": result.get("domain"),
            "xpedition_process": True,
            "attached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )


def record_native_failure(error: dict[str, Any]) -> None:
    _write_state(
        {
            "backend": "native_xpedition",
            "state": "crashed",
            "session_id": None,
            "pid": error.get("pid"),
            "executable": error.get("executable"),
            "xpedition_process": False,
            "error_code": error.get("code"),
            "error_details": error.get("details", {}),
            "failed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )


def clear_native() -> None:
    try:
        _state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: Any) -> bool:
    if pid is None:
        return False
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(value)
    try:
        os.kill(value, 0)
        return True
    except OSError:
        return False


def _pid_alive_windows(pid: int) -> bool:
    """Probe liveness through the Win32 API.

    ``os.kill(pid, 0)`` is not a liveness probe on Windows: ``signal.CTRL_C_EVENT``
    is 0, so CPython routes signal 0 to ``GenerateConsoleCtrlEvent``, which takes a
    process *group* id, delivers a console Ctrl+C when one matches, and fails with
    ERROR_INVALID_PARAMETER for an ordinary pid — reporting every GUI process, including
    a running Xpedition, as dead.
    """
    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    WAIT_TIMEOUT = 0x00000102

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # A live process owned by another integrity level denies SYNCHRONIZE but
        # still answers a query-only open; only a missing pid fails both.
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def status(backend: str = "mock") -> dict[str, Any]:
    path = _state_path()
    state: dict[str, Any] = {}
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                state = value
        except (OSError, json.JSONDecodeError):
            state = {}
    state_name = str(state.get("state", "not_started"))
    pid = state.get("pid")
    if backend == "native_xpedition" and state_name in {"started", "attached", "running"}:
        if pid is not None and not _pid_alive(pid):
            state_name = "crashed"
    return {
        "backend": backend,
        "state": state_name,
        "session_id": str(state["session_id"]) if state.get("session_id") is not None else None,
        "pid": pid,
        "xpedition_process": bool(state.get("xpedition_process", False))
        and (backend != "native_xpedition" or pid is None or _pid_alive(pid)),
        "reason": (
            "MockBackend is file based and does not start an Xpedition process"
            if backend == "mock"
            else (
                (
                    "recorded native startup failed"
                    if state.get("error_code")
                    else "recorded native process is no longer running"
                )
                if state_name == "crashed"
                else None
            )
        ),
        "_untrusted": ["session_id", "pid", "reason"],
    }


def logs(limit: int | None = None, offset: int = 0) -> dict[str, Any]:
    path = config_dir() / "session.log"
    lines: list[str] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    offset = max(0, int(offset))
    end = len(lines) if limit is None else min(len(lines), offset + max(0, int(limit)))
    items = lines[offset:end]
    next_offset = end if end < len(lines) else None
    return {
        "items": items,
        "count": len(items),
        "offset": offset,
        "next_offset": next_offset,
        "has_more": next_offset is not None,
        "_untrusted": ["items"],
    }
