"""Unreadable or changing critical files cannot yield a completed backup."""

import json
import zipfile
from pathlib import Path

import pytest

from xpedition_cli import native_com_adapter as adapter


def project(tmp_path):
    root = tmp_path / "QA"
    (root / "database").mkdir(parents=True)
    (root / "LogFiles").mkdir()
    (root / "QA.prj").write_bytes(b"project")
    (root / "database/icdb.dat").write_bytes(b"critical")
    (root / "LogFiles/report.log").write_bytes(b"diagnostic")
    (root / "QA.pcb.bak").write_bytes(b"previous")
    return root


def test_complete_backup_includes_every_file_and_verifiable_manifest(tmp_path):
    root = project(tmp_path)
    archive = adapter._backup_layout_folder(root)
    with zipfile.ZipFile(archive) as bundle:
        expected = {"QA/QA.prj", "QA/database/icdb.dat", "QA/LogFiles/report.log", "QA/QA.pcb.bak"}
        assert expected <= set(bundle.namelist())
        manifest = json.loads(bundle.read("__xpedition_backup__/manifest.json"))
        assert manifest["complete"] is True and len(manifest["files"]) == 4
        assert bundle.testzip() is None


def test_locked_icdb_fails_before_any_completed_archive(tmp_path, monkeypatch):
    root = project(tmp_path)
    original = Path.read_bytes

    def read(path):
        if path.name == "icdb.dat":
            raise PermissionError("locked")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(adapter.AdapterError) as failure:
        adapter._backup_layout_folder(root)
    assert failure.value.code == "E_IO"
    assert failure.value.details["backup_complete"] is False
    assert not list(tmp_path.glob("QA-backup-*.zip"))
    assert (root / "database/icdb.dat").is_file()


def test_source_drift_fails_and_preserves_original_files(tmp_path, monkeypatch):
    root = project(tmp_path)
    original = Path.read_bytes
    count = 0

    def read(path):
        nonlocal count
        if path.name == "icdb.dat":
            count += 1
            if count == 2:
                path.write_bytes(b"changed")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(adapter.AdapterError) as failure:
        adapter._backup_layout_folder(root)
    assert failure.value.code == "E_CONFLICT"
    assert failure.value.details["backup_complete"] is False
    assert not list(tmp_path.glob("QA-backup-*.zip"))


def test_removal_stops_when_project_icdb_cannot_be_backed_up(tmp_path, monkeypatch):
    root = project(tmp_path)
    (root / "PCB").mkdir()
    (root / "PCB/QA.pcb").write_bytes(b"board")
    original = Path.read_bytes

    def read(path):
        if path.name == "icdb.dat":
            raise PermissionError("locked")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(adapter, "_close_board_if_open", lambda *a: False)
    monkeypatch.setattr(
        adapter.shutil, "rmtree", lambda *a, **k: pytest.fail("Delete after failed backup")
    )
    with pytest.raises(adapter.AdapterError):
        adapter._remove_board(None, root / "QA.prj", "PCB/QA.pcb", "QA")
    assert (root / "PCB/QA.pcb").is_file()
