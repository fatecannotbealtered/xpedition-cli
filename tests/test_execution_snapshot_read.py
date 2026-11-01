"""Real local snapshot I/O regressions. No CAD/COM or old runtime is accessed."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import execution_lifecycle as L


@pytest.mark.skipif(os.name != "nt", reason="Windows native error and ABI wrapper")
def test_unsupported_posix_snapshot_publish_is_single_attempt_without_fallback(
    tmp_path, monkeypatch
):
    calls = []

    def opened(*args):
        calls.append("source_open")
        return 123

    def information(handle, value):
        ctypes.cast(value, ctypes.POINTER(L._SnapshotFileInfo)).contents.attributes = 0x20
        return True

    def rename(handle, io, buffer, size, info_class):
        calls.append("publish_attempt")
        value = ctypes.cast(buffer, ctypes.POINTER(L._SnapshotRenameInfo)).contents
        assert info_class == 65 and value.flags == 3 and value.root is None
        encoded = ctypes.string_at(
            ctypes.addressof(buffer) + L._SnapshotRenameInfo.name.offset, value.name_bytes
        )
        assert encoded.decode("utf-16-le") == "snapshot.json"
        return -1073741811  # STATUS_INVALID_PARAMETER, not GetLastError.

    def closed(handle):
        calls.append("source_close")
        return True

    api = SimpleNamespace(
        CreateFileW=opened,
        GetFileInformationByHandle=information,
        CloseHandle=closed,
    )
    native = SimpleNamespace(NtSetInformationFile=rename, RtlNtStatusToDosError=lambda status: 87)
    monkeypatch.setattr(
        L.ctypes, "WinDLL", lambda name, **kwargs: api if name == "kernel32" else native
    )
    monkeypatch.setattr(
        L.ctypes, "get_last_error", lambda: pytest.fail("NT failure must use returned status")
    )
    monkeypatch.setattr(
        L, "os", SimpleNamespace(name="nt", replace=lambda *args: pytest.fail("no legacy fallback"))
    )
    with pytest.raises(OSError) as caught:
        L._replace_snapshot(tmp_path / "snapshot-0123456789abcdef.tmp", tmp_path / "snapshot.json")
    assert caught.value.winerror == 87
    assert caught.value.snapshot_callpoint == "NtSetInformationFile"
    assert caught.value.snapshot_ntstatus == -1073741811
    assert calls == ["source_open", "publish_attempt", "source_close"]


def test_snapshot_publisher_refuses_non_snapshot_or_other_directory_before_io(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        L.ctypes, "WinDLL", lambda *args, **kwargs: pytest.fail("no native call"), raising=False
    )
    for source, destination in (
        (tmp_path / "input.vbs", tmp_path / "snapshot.json"),
        (tmp_path / "snapshot-0123456789abcdef.tmp", tmp_path / "board.pcb"),
        (tmp_path / "other" / "snapshot-0123456789abcdef.tmp", tmp_path / "snapshot.json"),
    ):
        with pytest.raises(L.LifecycleError, match="private_snapshot_publication_paths_required"):
            L._replace_snapshot(source, destination)


@pytest.mark.skipif(os.name != "nt", reason="Windows native entry-point wrapper")
def test_missing_native_snapshot_publisher_does_not_open_or_fall_back(tmp_path, monkeypatch):
    def unexpected(*args):
        pytest.fail("no source open or fallback when entry point is unavailable")

    api = SimpleNamespace(
        CreateFileW=unexpected, GetFileInformationByHandle=unexpected, CloseHandle=unexpected
    )
    native = SimpleNamespace(RtlNtStatusToDosError=unexpected)
    monkeypatch.setattr(
        L.ctypes, "WinDLL", lambda name, **kwargs: api if name == "kernel32" else native
    )
    monkeypatch.setattr(L, "os", SimpleNamespace(name="nt", replace=unexpected))
    with pytest.raises(AttributeError):
        L._replace_snapshot(tmp_path / "snapshot-0123456789abcdef.tmp", tmp_path / "snapshot.json")


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing contract")
def test_plain_reader_blocks_replace_and_share_delete_reader_preserves_both_versions(tmp_path):
    key = secrets.token_bytes(32)
    old = L._pack({"version": 1, "original_operation_status": "unknown"}, key)
    new = L._pack({"version": 2, "original_operation_status": "unknown"}, key)
    legacy = tmp_path / "legacy-reader"
    legacy.mkdir()
    target, pending = legacy / "snapshot.json", legacy / "next.tmp"
    target.write_bytes(old)
    pending.write_bytes(new)
    with target.open("rb") as reader:
        with pytest.raises(OSError) as caught:
            os.replace(pending, target)
        assert caught.value.winerror in (5, 32)
        assert reader.read() == old
    assert target.read_bytes() == old and pending.read_bytes() == new
    (legacy / "expected-denial.json").write_text(
        json.dumps(
            {
                "winerror": caught.value.winerror,
                "first_historical_holder_identified": False,
                "writer_retry_attempted": False,
            }
        ),
        encoding="utf-8",
    )

    compatible = tmp_path / "share-delete-reader"
    compatible.mkdir()
    target, pending = compatible / "snapshot.json", compatible / "snapshot-0123456789abcdef.tmp"
    target.write_bytes(old)
    pending.write_bytes(new)
    with L._snapshot_reader(target) as reader:
        L._replace_snapshot(pending, target)
        assert reader.read() == old
        current, _ = L._read(target, key)
        assert current == {"version": 2, "original_operation_status": "unknown"}
    assert target.read_bytes() == new


def test_snapshot_reader_keeps_record_size_and_mac_fail_closed(tmp_path):
    key = secrets.token_bytes(32)
    path = tmp_path / "snapshot.json"
    path.write_bytes(b"x" * (L.MAX_RECORD_BYTES + 1))
    with pytest.raises(L.LifecycleError, match="record_byte_limit"):
        L._read(path, key)
    path.write_bytes(L._pack({"original_operation_status": "unknown"}, key))
    with pytest.raises(L.LifecycleError, match="record_integrity_incomplete"):
        L._read(path, b"wrong-key")


@pytest.mark.skipif(os.name != "nt", reason="Real Windows writer/observer child handles")
def test_real_writer_and_observer_children_do_not_change_initial_unknown(tmp_path):
    root = tmp_path / "child-pair"
    L._private_mkdir(root)
    L._private_mkdir(root / "events")
    key = secrets.token_bytes(32)
    intent = {
        "execution_id": "exec-" + secrets.token_hex(16),
        "execution_epoch": secrets.token_hex(16),
    }
    (root / "key.bin").write_bytes(key)
    (root / "intent.json").write_text(json.dumps(intent), encoding="utf-8")
    initial = L._pack({"original_operation_status": "unknown", "cad_verified": False}, key)
    (root / "initial.json").write_bytes(initial)
    initial_hash = hashlib.sha256(initial).hexdigest()
    fixture = Path(__file__).parent / "fixtures" / "execution_snapshot_io_case.py"
    children = []
    try:
        for role in ("writer", "observer"):
            children.append(
                L._DetachedChild(
                    [
                        sys.executable,
                        "-X",
                        "utf8",
                        "-B",
                        str(fixture),
                        "--root",
                        str(root),
                        "--role",
                        role,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(tmp_path),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            )
        for child in children:
            output, errors = child.communicate(timeout=12)
            assert child.returncode == 0, (output, errors)
    finally:
        (root / "stop").write_bytes(b"cooperative fixture boundary only\n")
        for child in children:
            if child.returncode is None:
                child.communicate(timeout=5)  # No terminate/kill fallback.
            child.close_parent_handle()
    writer = json.loads((root / "writer-result.json").read_text(encoding="utf-8"))
    observer = json.loads((root / "observer-result.json").read_text(encoding="utf-8"))
    assert writer["append_count"] == 33
    assert observer["valid_snapshots"] >= 5
    assert observer["original_reader_full_version"] is True
    assert writer["cad_used"] is observer["cad_used"] is False
    assert hashlib.sha256((root / "initial.json").read_bytes()).hexdigest() == initial_hash
    values = L._events(root, key, intent)
    assert len(values) == 33
    assert all(value["details"]["original_operation_status"] == "unknown" for value in values)
    assert all(value["details"]["cad_verified"] is False for value in values)
