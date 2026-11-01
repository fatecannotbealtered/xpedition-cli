"""Original-generation exit proof; fake APIs plus one retained non-CAD child."""

from __future__ import annotations

import copy
import ctypes
import ntpath
import os
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import native_process as P
from xpedition_cli.backends.native_xpedition import NativeBackend

EXPECTED = {
    "pid": 4242,
    "executable": "C:/fixture/SDD_HOME/wv/win64/bin/viewdraw.exe",
    "creation_time_100ns": "134375464979013265",
}


class Function:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


@pytest.fixture(autouse=True)
def no_cad(monkeypatch, request):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Exit tests cannot call COM, CAD or an un-injected process API")

    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    monkeypatch.setattr(NativeBackend, "invoke", forbidden)
    if "real_retained_python_child" not in request.node.name:
        monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    yield
    assert hits == []


@pytest.fixture
def api(monkeypatch):
    case = NS(
        handle=99,
        pid=EXPECTED["pid"],
        birth=int(EXPECTED["creation_time_100ns"]),
        times_ok=True,
        waited=0,
        waits=[],
        image_ok=False,
        image_error=31,
        image=EXPECTED["executable"],
        error=5,
        closed=[],
        calls=[],
        close_ok=True,
    )

    def pid(handle):
        case.calls.append("pid")
        return case.pid

    def times(handle, created, *unused):
        case.calls.append("birth")
        created._obj.dwHighDateTime = case.birth >> 32
        created._obj.dwLowDateTime = case.birth & 0xFFFFFFFF
        return case.times_ok

    def wait(*args):
        case.calls.append("wait")
        return case.waits.pop(0) if case.waits else case.waited

    def image(handle, flags, buffer, size):
        case.calls.append("image")
        if not case.image_ok:
            case.error = case.image_error
            return False
        buffer.value = case.image
        return True

    def close(handle):
        case.closed.append(handle)
        return case.close_ok

    functions = NS(
        OpenProcess=Function(lambda *args: case.handle),
        GetProcessId=Function(pid),
        GetProcessTimes=Function(times),
        WaitForSingleObject=Function(wait),
        QueryFullProcessImageNameW=Function(image),
        CloseHandle=Function(close),
    )
    monkeypatch.setattr(P, "os", NS(name="nt", path=ntpath))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: functions, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: case.error, raising=False)
    return case


def observe():
    return P.observe_exit("schematic", "4242", timeout=0, expected_identity=EXPECTED)


def test_signaled_original_generation_does_not_need_its_invalid_image_getter(api):
    before = copy.deepcopy(EXPECTED)
    result = observe()
    assert result["process_absence_observed"] is True
    facts = result["exit_observation"]
    assert facts["wait_result"] == 0 and facts["pid_reused"] is False
    assert facts["image_observation"] == "unobserved_after_exit"
    assert facts["image_query_winerror"] is None
    assert facts["observed_process_identity"]["executable"] is None
    assert api.calls == ["pid", "birth", "wait"]
    assert api.closed == [99] and EXPECTED == before


def test_image_error_31_while_same_generation_alive_never_proves_exit(api):
    api.waited = 258
    with pytest.raises(OSError) as failure:
        observe()
    assert getattr(failure.value, "winerror", failure.value.errno) == 31
    assert api.calls == ["pid", "birth", "wait", "image", "pid", "birth", "wait"]
    assert api.closed == [99]


def test_exit_between_wait_and_image_is_rechecked_on_the_same_handle(api):
    api.waits = [258, 0]
    result = observe()
    assert result["process_absence_observed"] is True
    facts = result["exit_observation"]
    assert facts["image_observation"] == "unavailable"
    assert facts["image_query_winerror"] == 31
    assert facts["observed_process_identity"]["executable"] is None
    assert facts["observations"] == 2
    assert api.closed == [99]


def test_reused_pid_proves_only_original_birth_ended_not_new_app_identity(api):
    api.birth += 1
    api.waited = 258
    result = observe()
    assert result["process_absence_observed"] is True
    facts = result["exit_observation"]
    assert facts["pid_reused"] is True and facts["wait_result"] == 258
    assert facts["observed_process_identity"]["executable"] is None
    assert "image" not in api.calls
    assert api.closed == [99]


@pytest.mark.parametrize(
    "case", ["unknown_birth", "times_failed", "wait_failed", "wrong_handle_pid"]
)
def test_incomplete_kernel_evidence_cannot_be_reported_as_exit(api, case):
    if case == "unknown_birth":
        api.birth = 0
    elif case == "times_failed":
        api.times_ok = False
    elif case == "wait_failed":
        api.waited = 0xFFFFFFFF
    else:
        api.pid += 1
    with pytest.raises((OSError, ValueError)):
        observe()
    assert "image" not in api.calls
    assert api.closed == [99]


def test_live_same_generation_still_requires_exact_executable(api):
    api.waited, api.image_ok = 258, True
    api.image = "C:/foreign-installation/viewdraw.exe"
    with pytest.raises(ValueError):
        observe()
    assert api.closed == [99]


def test_live_original_generation_with_observed_image_remains_live(api):
    api.waited, api.image_ok = 258, True
    result = observe()
    assert result["process_absence_observed"] is False
    assert result["exit_observation"]["observed_process_identity"] == EXPECTED
    assert result["exit_observation"]["image_observation"] == "observed"


@pytest.mark.parametrize("error, ended", [(87, True), (5, False)])
def test_only_documented_missing_pid_error_proves_absence(api, error, ended):
    api.handle, api.error = 0, error
    if ended:
        assert observe()["process_absence_observed"] is True
    else:
        with pytest.raises(OSError):
            observe()
    assert api.calls == [] and api.closed == []


def test_query_handle_release_failure_is_not_a_successful_observation(api):
    api.close_ok = False
    with pytest.raises(OSError):
        observe()
    assert api.closed == [99]


@pytest.mark.parametrize(
    "changes", [{"pid": 7}, {"creation_time_100ns": ""}, {"executable": "viewdraw.exe"}]
)
def test_trusted_expected_generation_must_be_complete_before_any_api(changes):
    with pytest.raises(ValueError):
        P.observe_exit("schematic", "4242", timeout=0, expected_identity={**EXPECTED, **changes})


@pytest.mark.skipif(os.name != "nt", reason="Real retained HANDLE proof uses Windows")
def test_real_retained_python_child_normal_exit_without_cad_or_termination(tmp_path):
    release = tmp_path / "release-fixture"
    script = (
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); end=time.monotonic()+8\n"
        "while not p.exists() and time.monotonic()<end: time.sleep(.01)\n"
        "raise SystemExit(0 if p.exists() else 3)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", script, str(release)],
        cwd=str(tmp_path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        with P.ProcessHandle("schematic", process.pid, check_name=False) as pinned:
            pinned.require_spawned(process)
            expected = copy.deepcopy(pinned.identity)
            release.touch()
            assert process.wait(timeout=10) == 0
            assert process._handle is not None  # Keep the original Popen HANDLE retained.
            result = P.observe_exit(
                "schematic", str(process.pid), timeout=0, expected_identity=expected
            )
            assert result["process_absence_observed"] is True
            assert result["exit_observation"]["wait_result"] == 0
            assert result["exit_observation"]["pid_reused"] is False
            assert result["exit_observation"]["observed_process_identity"]["executable"] is None
            assert result["process_identity"] == expected
    finally:
        release.touch(exist_ok=True)
        # The fixture always has its own bounded normal return. No terminate,
        # kill or operation against another process is part of this proof.
        assert process.wait(timeout=10) == 0
