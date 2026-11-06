"""Process discovery (adapter/platform/processes.py) over a fake Toolhelp32 API.

The fake API writes into the real ctypes structures the code passes, so these tests need
Windows' ctypes; off Windows the functions return empty lists and only that is tested.
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from xpedition_cli._driver import AdapterError
from xpedition_cli.adapter.platform import processes as P

ON_WINDOWS = os.name == "nt"
SNAPSHOT = 123
FILETIME_2026 = 13_412_000_000 * 10_000_000 + 12345  # 100 ns since 1601


class FakeKernel:
    """Toolhelp32 over a list of ``(pid, parent_pid, exe)``. ``fail_at`` makes the n-th
    enumeration step fail with ``error``; the documented end is ERROR_NO_MORE_FILES."""

    def __init__(self, rows, *, fail_at: int | None = None, error: int = 5, images: dict | None = None,
                 snapshot: int | None = SNAPSHOT, ended: tuple[int, ...] = ()) -> None:
        self.rows, self.fail_at, self.error, self.images, self.snapshot = list(rows), fail_at, error, images or {}, snapshot
        self.ended = set(ended)
        self.step = 0
        self.closed: list[int] = []
        self.last_error = 0

    # Toolhelp32
    def CreateToolhelp32Snapshot(self, flags, pid):  # noqa: N802 - Win32 spelling
        if self.snapshot is None:
            self.last_error = 5
        self.step = 0
        return self.snapshot

    def _advance(self, pointer):
        index = self.step
        self.step += 1
        if self.fail_at is not None and index == self.fail_at:
            self.last_error = self.error
            return 0
        if index >= len(self.rows):
            self.last_error = P.ERROR_NO_MORE_FILES
            return 0
        entry = pointer._obj
        entry.pid, entry.parent_pid, entry.exe = self.rows[index]
        return 1

    def Process32FirstW(self, handle, pointer):  # noqa: N802
        return self._advance(pointer)

    def Process32NextW(self, handle, pointer):  # noqa: N802
        return self._advance(pointer)

    # one process's identity
    def OpenProcess(self, access, inherit, pid):  # noqa: N802
        return 1000 + pid if pid in self.images else 0

    def QueryFullProcessImageNameW(self, handle, flags, buffer, size):  # noqa: N802
        buffer.value = self.images[handle - 1000]
        return 1

    def GetProcessTimes(self, handle, created, exited, kernel, user):  # noqa: N802
        created._obj.dwHighDateTime = FILETIME_2026 >> 32
        created._obj.dwLowDateTime = FILETIME_2026 & 0xFFFFFFFF
        return 1

    def GetExitCodeProcess(self, handle, code):  # noqa: N802
        code._obj.value = 0 if handle - 1000 in self.ended else P.STILL_ACTIVE
        return 1

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return 1


@unittest.skipUnless(ON_WINDOWS, "the Toolhelp32 structures are Windows ctypes")
class Toolhelp(unittest.TestCase):
    def use(self, kernel: FakeKernel) -> FakeKernel:
        def clear() -> None:
            kernel.last_error = 0

        for name, value in (("_kernel32", lambda: kernel), ("_last_error", lambda: kernel.last_error), ("_clear_last_error", clear)):
            patcher = mock.patch.object(P, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return kernel

    def test_matching_processes_carry_their_path_and_start_time(self) -> None:
        program = "C:/Siemens/XPED2604/SDD_HOME/wg/win64/bin/ExpeditionPCB.exe"
        kernel = self.use(FakeKernel([(4, 0, "System"), (4242, 7, "ExpeditionPCB.exe"), (5000, 7, "viewdraw.exe")],
                                     images={4242: program}))
        found = P.processes("expeditionpcb.EXE")
        self.assertEqual(found, [P.ProcessInfo(pid=4242, path=program, start_time=FILETIME_2026 // 10_000_000, parent_pid=7)])
        self.assertIn(SNAPSHOT, kernel.closed)
        self.assertIn(1000 + 4242, kernel.closed)

    def test_a_process_that_has_ended_is_not_listed(self) -> None:
        program = "C:/Siemens/XPED2604/SDD_HOME/wg/win64/bin/viewdraw.exe"
        kernel = self.use(FakeKernel([(5000, 7, "viewdraw.exe")], images={5000: program}, ended=(5000,)))
        self.assertEqual(P.processes("viewdraw.exe"), [])
        self.assertIn(1000 + 5000, kernel.closed)

    def test_a_process_this_user_may_not_open_is_still_listed(self) -> None:
        self.use(FakeKernel([(4242, 7, "ExpeditionPCB.exe")]))
        self.assertEqual(P.processes("ExpeditionPCB.exe"), [P.ProcessInfo(pid=4242, path="", start_time=0, parent_pid=7)])

    def test_only_the_documented_end_can_show_absence(self) -> None:
        kernel = self.use(FakeKernel([]))
        self.assertEqual(P.processes("ExpeditionPCB.exe"), [])
        self.assertEqual(kernel.closed, [SNAPSHOT])

    def test_a_failed_enumeration_is_not_returned_as_a_complete_list(self) -> None:
        for stage in (0, 1):
            for error in (0, 5, 87):
                with self.subTest(stage=stage, error=error):
                    kernel = self.use(FakeKernel([(4242, 7, "ExpeditionPCB.exe"), (4243, 7, "x.exe")], fail_at=stage, error=error))
                    with self.assertRaises(AdapterError) as caught:
                        P.processes("ExpeditionPCB.exe")
                    self.assertEqual(caught.exception.data["code"], "E_NATIVE_FAILURE")
                    self.assertEqual(kernel.closed, [SNAPSHOT])

    def test_a_snapshot_that_cannot_be_taken_is_an_error(self) -> None:
        self.use(FakeKernel([], snapshot=None))
        with self.assertRaises(AdapterError):
            P.processes("ExpeditionPCB.exe")

    def test_descendants_follow_the_parent_links_without_looping(self) -> None:
        self.use(FakeKernel([
            (100, 1, "PartsDB2HKP.exe"),  # the launcher
            (200, 100, "PartsDB2HKP.exe"),  # the real program
            (300, 200, "conhost.exe"),
            (400, 1, "unrelated.exe"),
            (500, 600, "a.exe"), (600, 500, "b.exe"),  # reused PIDs that point at each other
            (700, 700, "self.exe"),
        ]))
        self.assertEqual(P.descendants(100), [200, 300])
        self.assertEqual(P.descendants(500), [600])
        self.assertEqual(P.descendants(700), [])
        self.assertEqual(P.descendants(400), [])


class Basics(unittest.TestCase):
    def test_off_windows_nothing_runs(self) -> None:
        with mock.patch.object(P, "os", SimpleNamespace(name="posix")):
            self.assertEqual(P.processes("ExpeditionPCB.exe"), [])
            self.assertEqual(P.descendants(1), [])
            self.assertEqual(P.window_pid(1234), 0)

    def test_this_process_is_alive_and_a_gone_one_is_not(self) -> None:
        self.assertTrue(P.alive(os.getpid()))
        self.assertFalse(P.alive(0))
        self.assertFalse(P.alive(-5))

    def test_no_window_has_no_process(self) -> None:
        self.assertEqual(P.window_pid(0), 0)


if __name__ == "__main__":
    unittest.main()
