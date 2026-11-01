from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from xpedition_cli.session import _pid_alive


def test_pid_alive_accepts_the_current_process() -> None:
    assert _pid_alive(os.getpid()) is True


@pytest.mark.parametrize("value", [None, "", "not-a-pid", 0, -1])
def test_pid_alive_rejects_unusable_values(value: object) -> None:
    assert _pid_alive(value) is False


def test_pid_alive_tracks_a_child_across_its_lifetime() -> None:
    """Guards the Windows probe: os.kill(pid, 0) reports live processes as dead there."""
    child = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not _pid_alive(child.pid):
            time.sleep(0.05)
        assert _pid_alive(child.pid) is True
    finally:
        child.kill()
        child.wait(timeout=10)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _pid_alive(child.pid):
        time.sleep(0.05)
    assert _pid_alive(child.pid) is False


def test_pid_alive_rejects_a_pid_that_cannot_exist() -> None:
    assert _pid_alive(0x7FFFFFFF) is False
