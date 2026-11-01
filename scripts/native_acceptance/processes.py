"""Synchronous child processes in an unnamed, finite Windows job.

The job controls only newly spawned test children. Existing CAD applications are
never assigned to it or terminated. Closing it prevents an orphan adapter/converter
from becoming a background service after a timeout or runner interruption.
"""

from __future__ import annotations

import ctypes as C
import os
import subprocess
from ctypes import wintypes as W

from checks import require


class Basic(C.Structure):
    _fields_ = [
        ("process_time", C.c_longlong),
        ("job_time", C.c_longlong),
        ("flags", W.DWORD),
        ("minimum_working_set", C.c_size_t),
        ("maximum_working_set", C.c_size_t),
        ("active_process_limit", W.DWORD),
        ("affinity", C.c_size_t),
        ("priority", W.DWORD),
        ("scheduling", W.DWORD),
    ]


class IO(C.Structure):
    _fields_ = [
        (name, C.c_ulonglong)
        for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")
    ]


class Extended(C.Structure):
    _fields_ = [
        ("basic", Basic),
        ("io", IO),
        ("process_memory", C.c_size_t),
        ("job_memory", C.c_size_t),
        ("peak_process_memory", C.c_size_t),
        ("peak_job_memory", C.c_size_t),
    ]


class ThreadEntry(C.Structure):
    _fields_ = [
        ("size", W.DWORD),
        ("usage", W.DWORD),
        ("tid", W.DWORD),
        ("pid", W.DWORD),
        ("base_priority", W.LONG),
        ("delta_priority", W.LONG),
        ("flags", W.DWORD),
    ]


def run(argv, *, cwd, env, timeout):
    require(os.name == "nt", "E_PLATFORM", "This fixed acceptance profile requires Windows")
    api = C.WinDLL("kernel32", use_last_error=True)
    api.CreateJobObjectW.argtypes, api.CreateJobObjectW.restype = [W.LPVOID, W.LPCWSTR], W.HANDLE
    api.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD]
    api.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
    api.CloseHandle.argtypes = [W.HANDLE]
    api.CreateToolhelp32Snapshot.argtypes, api.CreateToolhelp32Snapshot.restype = (
        [W.DWORD, W.DWORD],
        W.HANDLE,
    )
    api.Thread32First.argtypes = api.Thread32Next.argtypes = [W.HANDLE, C.POINTER(ThreadEntry)]
    api.OpenThread.argtypes, api.OpenThread.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
    api.ResumeThread.argtypes, api.ResumeThread.restype = [W.HANDLE], W.DWORD
    job = api.CreateJobObjectW(None, None)
    require(
        job,
        "E_PROCESS_BOUNDARY",
        "Cannot create the finite child-process job",
        {"winerror": C.get_last_error()},
    )
    process = None
    started = False
    try:
        limits = Extended()
        limits.basic.flags = 0x2000 | 0x8  # KILL_ON_JOB_CLOSE + ACTIVE_PROCESS
        limits.basic.active_process_limit = 16
        require(
            api.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)),
            "E_PROCESS_BOUNDARY",
            "Cannot bound the test child job",
            {"winerror": C.get_last_error()},
        )
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW | 0x4,
        )
        require(
            api.AssignProcessToJobObject(job, W.HANDLE(int(process._handle))),
            "E_PROCESS_BOUNDARY",
            "Cannot contain the suspended test child; it was not allowed to execute",
            {"winerror": C.get_last_error(), "pid": process.pid},
        )
        snapshot = api.CreateToolhelp32Snapshot(4, 0)
        require(
            snapshot != W.HANDLE(-1).value,
            "E_PROCESS_BOUNDARY",
            "Cannot locate the suspended test child's thread",
        )
        thread = None
        try:
            entry = ThreadEntry()
            entry.size = C.sizeof(entry)
            more = api.Thread32First(snapshot, C.byref(entry))
            while more:
                if entry.pid == process.pid:
                    thread = api.OpenThread(0x2, False, entry.tid)
                    break
                more = api.Thread32Next(snapshot, C.byref(entry))
        finally:
            api.CloseHandle(snapshot)
        require(thread, "E_PROCESS_BOUNDARY", "Cannot resume the contained test child")
        try:
            require(
                api.ResumeThread(thread) != 0xFFFFFFFF,
                "E_PROCESS_BOUNDARY",
                "Cannot resume the contained test child",
                {"winerror": C.get_last_error()},
            )
            started = True
        finally:
            api.CloseHandle(thread)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return {
                "exit_code": process.returncode,
                "stdout": stdout.decode("utf-8", "replace"),
                "stderr": stderr.decode("utf-8", "replace"),
                "pid": process.pid,
                "timed_out": False,
                "child_execution_started": True,
            }
        except subprocess.TimeoutExpired:
            api.CloseHandle(job)
            job = None
            stdout, stderr = process.communicate(timeout=10)
            return {
                "exit_code": process.returncode,
                "stdout": stdout.decode("utf-8", "replace"),
                "stderr": stderr.decode("utf-8", "replace"),
                "pid": process.pid,
                "timed_out": True,
                "child_execution_started": started,
            }
    finally:
        if process is not None and process.poll() is None:
            # Only this newly created child, never an existing CAD PID.
            process.kill()
        if job:
            api.CloseHandle(job)
        if process is not None and process.poll() is None:
            process.wait(timeout=10)
