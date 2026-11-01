"""Toolhelp API doubles, including failed enumeration with non-terminal errors."""

import ctypes
from types import SimpleNamespace

import pytest

from xpedition_cli import native_process as P

REAL_PROCESSES = P.processes


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


def api(monkeypatch, first_error=None, next_error=18, own=False):
    # conftest disables real OS discovery globally; restore the actual function
    # only after replacing every native API it can call with the double below.
    monkeypatch.setattr(P, "processes", REAL_PROCESSES)
    error = [999]
    closed = []

    def first(handle, pointer):
        if first_error is not None:
            error[0] = first_error
            return 0
        row = pointer._obj
        row.pid = 4242
        row.parent_pid = 7
        row.exe = "ExpeditionPCB.exe" if own else "unrelated.exe"
        return 1

    def following(*args):
        error[0] = next_error
        return 0

    functions = SimpleNamespace(
        CreateToolhelp32Snapshot=Function(lambda *a: 123),
        Process32FirstW=Function(first),
        Process32NextW=Function(following),
        OpenProcess=Function(lambda *a: 0),
        QueryFullProcessImageNameW=Function(lambda *a: 0),
        CloseHandle=Function(lambda handle: closed.append(handle) or 1),
    )
    monkeypatch.setattr(P, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: functions, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error[0], raising=False)
    monkeypatch.setattr(
        ctypes, "set_last_error", lambda value: error.__setitem__(0, value), raising=False
    )
    return closed


@pytest.mark.parametrize("stage", ["first", "next"])
@pytest.mark.parametrize("error", [0, 5, 87])
def test_api_failure_cannot_prove_process_absence(monkeypatch, stage, error):
    closed = api(monkeypatch, first_error=error if stage == "first" else None, next_error=error)
    with pytest.raises(OSError):
        P.observe_exit("pcb", "4242", timeout=0)
    assert closed == [123]


def test_partial_enumeration_is_not_returned_as_a_complete_list(monkeypatch):
    api(monkeypatch, next_error=5, own=True)
    with pytest.raises(OSError):
        P.processes("pcb")


@pytest.mark.parametrize("first_error", [None, 18])
def test_only_documented_end_can_prove_absence(monkeypatch, first_error):
    closed = api(monkeypatch, first_error=first_error)
    assert P.observe_exit("pcb", "4242", timeout=0)["process_absence_observed"]
    assert closed == [123]


def test_matching_process_without_image_permission_still_exists(monkeypatch):
    api(monkeypatch, own=True)
    assert not P.observe_exit("pcb", "4242", timeout=0)["process_absence_observed"]
