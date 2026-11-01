"""Owned, bounded Windows file holders for offline confirmation-store tests."""

from __future__ import annotations

import ctypes
import multiprocessing
import sys
from contextlib import contextmanager, suppress
from ctypes import wintypes
from pathlib import Path

from xpedition_cli import confirmation_store as store


def _holder(path_string, mode, pipe):
    handle = None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    try:
        if mode == "byte_lock":
            with store.exclusive_lock(Path(path_string)):
                pipe.send({"ready": True})
                if pipe.poll(20):
                    pipe.recv()
            pipe.send({"closed": True})
            return
        share = {"deny_delete": 3, "deny_all_sharing": 0, "deny_read_and_delete": 2}[mode]
        handle = kernel.CreateFileW(path_string, 0x80000000, share, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            handle = None
            pipe.send({"ready": False, "winerror": ctypes.get_last_error()})
            return
        pipe.send({"ready": True})
        if pipe.poll(20):
            pipe.recv()
        closed = bool(kernel.CloseHandle(handle))
        handle = None
        pipe.send({"closed": closed})
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)
        pipe.close()


@contextmanager
def held_file(path, mode, resources):
    """Hold only the named test file; release and join even on assertion failure."""
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_holder, args=(str(path), mode, child))
    process.start()
    child.close()
    resource = {"pid": process.pid, "mode": mode, "maximum_wait_seconds": 20}
    resources.append(resource)
    try:
        assert parent.poll(10), "test holder did not become ready"
        ready = parent.recv()
        assert ready == {"ready": True}, ready
        yield
    finally:
        if process.is_alive():
            with suppress(BrokenPipeError, EOFError, OSError):
                parent.send("release")
        if parent.poll(5):
            with suppress(EOFError):
                resource["handle_closed"] = parent.recv().get("closed", False)
        process.join(5)
        resource["termination_needed"] = process.is_alive()
        if process.is_alive():
            process.terminate()  # Only this test's owned, non-CAD child.
            process.join(5)
        resource["exit_code"] = process.exitcode
        resource["alive_after_cleanup"] = process.is_alive()
        parent.close()
        process.close()


@contextmanager
def storage_trace():
    """Capture call sites and numeric errors, never paths or exception text."""
    observation = {"calls": [], "errors": []}
    source = store.__file__

    def trace(frame, event, arg):
        if frame.f_code.co_filename != source:
            return None
        if event == "call":
            observation["calls"].append(frame.f_code.co_name)
        elif event == "exception" and isinstance(arg[1], OSError):
            error = arg[1]
            observation["errors"].append(
                {
                    "function": frame.f_code.co_name,
                    "line": frame.f_lineno,
                    "type": type(error).__name__,
                    "errno": error.errno,
                    "winerror": getattr(error, "winerror", None),
                }
            )
        return trace

    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        yield observation
    finally:
        sys.settrace(previous)
