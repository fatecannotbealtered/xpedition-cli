"""Offline reproductions of owner dependency/process/logging failures."""

from __future__ import annotations

import ctypes
import hashlib
import types
from ctypes import wintypes

import pytest

from scripts.native_acceptance import owner_support as support


def test_missing_function_is_rejected_before_activation():
    calls = []
    pythoncom = types.SimpleNamespace(
        __name__="pythoncom", CoCreateInstance=lambda: calls.append("COM")
    )
    with pytest.raises(support.OwnerPreflightError, match="pythoncom.MakeIID"):
        support.require_callables([(pythoncom, ("MakeIID", "CoCreateInstance"))])
    assert calls == []


def test_factory_uses_fixed_clsid_without_missing_resolver():
    calls = []
    pythoncom = types.SimpleNamespace(MakeIID=lambda value: calls.append(value) or value)
    assert (
        support.registered_layout_factory(pythoncom, support.LAYOUT_CLSID.lower())
        == support.LAYOUT_CLSID
    )
    assert calls == [support.LAYOUT_CLSID]
    with pytest.raises(support.OwnerPreflightError, match="differs"):
        support.registered_layout_factory(pythoncom, "{WRONG}")
    assert calls == [support.LAYOUT_CLSID]


def test_child_environment_inherits_license_without_global_mutation(tmp_path, monkeypatch):
    release = tmp_path / "XENTP2604" / "SDD_HOME"
    work = tmp_path / "work"
    for suffix in ("standard", "common/win64/bin", "wg/win64/lib"):
        (release / suffix).mkdir(parents=True)
    work.mkdir()
    monkeypatch.setenv("OWNER_SUPPORT_SENTINEL", "parent")
    inherited = {
        "PATH": "oldpath",
        "MGLS_LICENSE_FILE": "configured-license",
        "custom_license": "opaque",
    }
    declared = {
        "XPEDITION_SDD_HOME": str(release),
        "XPEDITION_MGC_WD": str(work),
        "TEMP": str(tmp_path),
    }
    before = dict(inherited)
    actual = support.child_environment(declared, inherited)
    assert inherited == before
    assert actual["MGLS_LICENSE_FILE"] == "configured-license"
    assert actual["custom_license"] == "opaque"
    assert actual["SDD_HOME"] == str(release.resolve())
    assert actual["SDD_WG"] == "wg"
    assert actual["PATH"].endswith("oldpath")
    assert "SDD_HOME" not in inherited
    monkeypatch.setattr(support.os, "environ", {**inherited, "OWNER_SUPPORT_SENTINEL": "parent"})
    assert support.child_environment(declared)["OWNER_SUPPORT_SENTINEL"] == "parent"
    assert not (release.parent / "MGC_HOME.ixw").exists()


def test_environment_conflict_is_redacted_and_no_directory_is_created(tmp_path):
    declared = {
        "XPEDITION_SDD_HOME": str(tmp_path / "absent"),
        "XPEDITION_MGC_WD": str(tmp_path),
        "TEMP": str(tmp_path),
    }
    with pytest.raises(support.OwnerPreflightError) as caught:
        support.child_environment(declared, {"xpedition_sdd_home": "secret-conflicting-value"})
    assert "secret-conflicting-value" not in str(caught.value)
    with pytest.raises(support.OwnerPreflightError, match="directory missing"):
        support.child_environment(declared, {})
    assert not (tmp_path / "absent").exists()


def test_declared_child_temp_and_encoding_override_preserves_parent_and_license(
    tmp_path, monkeypatch
):
    release, work = tmp_path / "XENTP2604" / "SDD_HOME", tmp_path / "work"
    release.mkdir(parents=True)
    work.mkdir()
    inherited = {
        "TEMP": "C:/system-temp",
        "TMP": "C:/system-tmp",
        "PYTHONIOENCODING": "cp936",
        "MGLS_LICENSE_FILE": "",
        "LM_LICENSE_FILE": "configured-license",
        "PATH": "original-path",
    }
    before = dict(inherited)
    monkeypatch.setattr(support.os, "environ", inherited)
    declared = {
        "XPEDITION_SDD_HOME": str(release),
        "XPEDITION_MGC_WD": str(work),
        "TEMP": str(tmp_path / "declared-runtime-temp"),
        "TMP": str(tmp_path / "declared-runtime-tmp"),
        "PYTHONIOENCODING": "utf-8",
    }
    child = support.child_environment(declared)
    for name in ("TEMP", "TMP", "PYTHONIOENCODING"):
        assert child[name] == declared[name]
    for name in ("MGLS_LICENSE_FILE", "LM_LICENSE_FILE"):
        assert child[name] == before[name]
    assert support.os.environ == before
    assert not (tmp_path / "declared-runtime-temp").exists()
    with pytest.raises(support.OwnerPreflightError, match="MGLS_LICENSE_FILE"):
        support.child_environment({**declared, "MGLS_LICENSE_FILE": "replacement"})
    assert support.os.environ == before


def process(
    pid,
    executable="D:/XENTP2604/SDD_HOME/wg/win64/bin/ExpeditionPCB.exe",
    birth="133000000000000001",
    **extra,
):
    return {"pid": pid, "executable": executable, "birth_filetime_100ns": birth, **extra}


def test_same_name_launcher_outside_worker_path_does_not_reject_worker():
    original = process(7)
    launcher = process(8, "D:/XENTP2604/SDD_HOME/common/win64/bin/ExpeditionPCB.exe")
    worker = process(9)
    actual = support.classify_process_roles(
        [original], [original, launcher, worker], {**worker, "hwnd": 55}, worker["executable"]
    )
    assert actual["returned_worker"] == worker
    assert actual["other_processes"][1]["process"] == launcher
    assert actual["other_processes"][1]["role"] == "same_name_launcher_or_other_instance"


@pytest.mark.parametrize(
    "case",
    [
        "old_rot",
        "pid_reused",
        "generation_changed",
        "launcher_returned",
        "forbidden",
        "missing_birth",
        "window_missing",
    ],
)
def test_returned_worker_identity_negative_cases(case):
    original = process(7)
    worker = process(9)
    before, after, returned, forbidden = [original], [original, worker], {**worker, "hwnd": 55}, []
    if case == "old_rot":
        returned = {**original, "hwnd": 55}
    elif case == "pid_reused":
        before.append({**worker, "birth_filetime_100ns": "133000000000000000"})
    elif case == "generation_changed":
        returned["birth_filetime_100ns"] = "133000000000000002"
    elif case == "launcher_returned":
        returned["executable"] = "D:/XENTP2604/SDD_HOME/common/win64/bin/ExpeditionPCB.exe"
        after[-1] = dict(returned)
    elif case == "forbidden":
        forbidden = [worker]
    elif case == "missing_birth":
        returned.pop("birth_filetime_100ns")
    elif case == "window_missing":
        returned.pop("hwnd")
    with pytest.raises(support.OwnerPreflightError):
        support.classify_process_roles(
            before, after, returned, worker["executable"], forbidden=forbidden
        )


def test_log_growth_is_incomplete_not_an_unload_interrupt(tmp_path, monkeypatch):
    source, target = tmp_path / "live.log", tmp_path / "captured.log"
    source.write_bytes(b"first")
    actual_read = support._read_sample

    def growing(path, limit):
        data, before, after = actual_read(path, limit)
        with source.open("ab") as stream:
            stream.write(b"later")
        return data, before, after

    monkeypatch.setattr(support, "_read_sample", growing)
    sample = support.capture_live_log(source, target)
    calls = []
    # No finally Save/Close: an established, authorized owner continues its one
    # explicit UnLoad even though this noncritical sample is incomplete.
    if sample["complete"] is False:
        calls.append("UnLoad")
    assert calls == ["UnLoad"]
    assert "source_grew_during_sample" in sample["reason_codes"]
    assert target.read_bytes() == b"first"
    assert sample["sample_sha256"] == hashlib.sha256(b"first").hexdigest()
    assert sample["sample_persisted_exact"] is True


def test_log_rotation_records_descriptor_and_path_identity(tmp_path, monkeypatch):
    source, target = tmp_path / "live.log", tmp_path / "captured.log"
    source.write_bytes(b"before")
    actual_read = support._read_sample

    def rotated(path, limit):
        data, before, after = actual_read(path, limit)
        source.rename(tmp_path / "old.log")
        source.write_bytes(b"replacement")
        return data, before, after

    monkeypatch.setattr(support, "_read_sample", rotated)
    sample = support.capture_live_log(source, target)
    assert sample["complete"] is False
    assert "source_identity_changed_during_sample" in sample["reason_codes"]
    assert target.read_bytes() == b"before"
    assert source.read_bytes() == b"replacement"


def test_log_temporary_unreadability_records_error_then_later_sample(tmp_path, monkeypatch):
    source = tmp_path / "live.log"
    source.write_bytes(b"evidence")
    actual_read = support._read_sample

    def unavailable(*args):
        raise PermissionError(13, "temporary lock")

    monkeypatch.setattr(support, "_read_sample", unavailable)
    sample = support.capture_live_log(source, tmp_path / "first.log")
    assert sample["complete"] is False
    assert sample["captured"] is None
    assert sample["errors"][0]["phase"] == "source_read"
    assert sample["errors"][0]["errno"] == 13
    monkeypatch.setattr(support, "_read_sample", actual_read)
    later = support.capture_live_log(source, tmp_path / "later.log")
    assert later["complete"] is False
    assert later["sample_persisted_exact"] is True


def test_log_missing_or_existing_output_is_noncritical(tmp_path):
    missing = support.capture_live_log(tmp_path / "absent.log", tmp_path / "none.log")
    assert missing["complete"] is False
    assert missing["captured"] is None
    source, target = tmp_path / "source.log", tmp_path / "old.log"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    result = support.capture_live_log(source, target)
    assert "capture_output_failed" in result["reason_codes"]
    assert target.read_bytes() == b"old"


def test_critical_pin_drift_still_raises(tmp_path):
    source = tmp_path / "critical.json"
    source.write_bytes(b"frozen")
    pin = {"path": str(source), "bytes": 6, "sha256": hashlib.sha256(b"frozen").hexdigest()}
    assert support.strict_file_pin(pin)["sha256"] == pin["sha256"]
    source.write_bytes(b"drift!")
    with pytest.raises(support.CriticalInputDrift):
        support.strict_file_pin(pin)


def test_filetime_generation_keeps_integer_precision():
    # Deliberately above binary64's exact range; these births must not collapse.
    worker = process(9, birth="133000000000000001")
    other = process(9, birth="133000000000000002", hwnd=55)
    with pytest.raises(support.OwnerPreflightError, match="generation"):
        support.classify_process_roles([], [worker], other, worker["executable"])


@pytest.mark.parametrize("failure", [None, "path", "window_changed"])
def test_readonly_process_handle_is_released_on_success_and_observer_failure(failure):
    closed = []
    observer = support.WindowsProcessObserver.__new__(support.WindowsProcessObserver)
    observer.w = wintypes
    pids = iter([9, 10] if failure == "window_changed" else [9, 9])
    observer._pid = lambda hwnd: next(pids)
    expected_birth = 133000000000000001

    def times(handle, creation, *unused):
        value = ctypes.cast(creation, ctypes.POINTER(wintypes.FILETIME)).contents
        value.dwHighDateTime = expected_birth >> 32
        value.dwLowDateTime = expected_birth & 0xFFFFFFFF
        return True

    def image(handle, flags, buffer, size):
        if failure == "path":
            raise OSError("synthetic temporary process-query failure")
        buffer.value = "D:/XENTP2604/SDD_HOME/wg/win64/bin/ExpeditionPCB.exe"
        return True

    observer.kernel = types.SimpleNamespace(
        OpenProcess=lambda *args: 123,
        GetProcessTimes=times,
        QueryFullProcessImageNameW=image,
        CloseHandle=lambda handle: closed.append(handle) or True,
    )
    if failure:
        with pytest.raises((OSError, support.OwnerPreflightError)):
            observer.observe_window(55)
    else:
        actual = observer.observe_window(55)
        assert actual["birth_filetime_100ns"] == str(expected_birth)
        assert actual["alive"] is True
        assert actual["process_handle_closed"] is True
    assert closed == [123]


@pytest.mark.parametrize("case", ["success", "times_failure", "exited"])
def test_pid_observer_uses_one_handle_and_exact_filetime_generation(case):
    observer = support.WindowsProcessObserver.__new__(support.WindowsProcessObserver)
    observer.w = wintypes
    calls = []
    expected_birth = 133000000000000001

    def open_process(access, inherit, pid):
        calls.append(("open", access, inherit, pid))
        return 456

    def times(handle, creation, exit_time, *unused):
        calls.append(("times", handle))
        if case == "times_failure":
            raise PermissionError("synthetic process-time query failure")
        value = ctypes.cast(creation, ctypes.POINTER(wintypes.FILETIME)).contents
        value.dwHighDateTime = expected_birth >> 32
        value.dwLowDateTime = expected_birth & 0xFFFFFFFF
        if case == "exited":
            ctypes.cast(exit_time, ctypes.POINTER(wintypes.FILETIME)).contents.dwLowDateTime = 1
        return True

    def image(handle, flags, buffer, size):
        calls.append(("image", handle))
        buffer.value = "D:/XENTP2604/SDD_HOME/wg/win64/bin/ExpeditionPCB.exe"
        return True

    observer.kernel = types.SimpleNamespace(
        OpenProcess=open_process,
        GetProcessTimes=times,
        QueryFullProcessImageNameW=image,
        CloseHandle=lambda handle: calls.append(("close", handle)) or True,
    )
    if case in {"success", "exited"}:
        actual = observer.observe_pid(9)
        assert actual["pid"] == 9
        assert "hwnd" not in actual
        assert actual["birth_filetime_100ns"] == str(expected_birth)
        assert actual["alive"] is (case == "success")
        assert actual["exit_filetime_100ns"] == ("1" if case == "exited" else "0")
        assert actual["process_handle_closed"] is True
    else:
        with pytest.raises((PermissionError, support.OwnerPreflightError)):
            observer.observe_pid(9)
    assert calls[0] == ("open", 0x1000, False, 9)
    assert calls[-1] == ("close", 456)
    assert calls.count(("close", 456)) == 1
    assert calls.count(("times", 456)) == 1


@pytest.mark.parametrize("exited", [True, False])
def test_image_failure_only_preserves_exit_when_getprocesstimes_observed_nonzero_exit(exited):
    observer = support.WindowsProcessObserver.__new__(support.WindowsProcessObserver)
    observer.w = wintypes
    closed = []
    birth, exit_ticks = 133000000000000001, 133000000000000123

    def times(handle, creation, exit_time, *unused):
        created = ctypes.cast(creation, ctypes.POINTER(wintypes.FILETIME)).contents
        created.dwHighDateTime, created.dwLowDateTime = birth >> 32, birth & 0xFFFFFFFF
        if exited:
            ended = ctypes.cast(exit_time, ctypes.POINTER(wintypes.FILETIME)).contents
            ended.dwHighDateTime, ended.dwLowDateTime = exit_ticks >> 32, exit_ticks & 0xFFFFFFFF
        return True

    def image(*args):
        raise PermissionError(5, "synthetic image query access denied")

    observer.kernel = types.SimpleNamespace(
        OpenProcess=lambda *args: 789,
        GetProcessTimes=times,
        QueryFullProcessImageNameW=image,
        CloseHandle=lambda handle: closed.append(handle) or True,
    )
    if exited:
        actual = observer.observe_pid(9)
        assert actual["pid"] == 9
        assert actual["birth_filetime_100ns"] == str(birth)
        assert actual["exit_filetime_100ns"] == str(exit_ticks)
        assert actual["alive"] is False
        assert actual["executable"] is None
        assert actual["observation_errors"][0]["phase"] == "process_image"
        assert actual["observation_errors"][0]["type"] == "PermissionError"
        assert actual["process_handle_closed"] is True
    else:
        with pytest.raises(PermissionError, match="access denied"):
            observer.observe_pid(9)
    assert closed == [789]
