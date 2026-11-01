from __future__ import annotations

from pathlib import Path

import pytest

from xpedition_cli.driver import _disk, _project_files


def test_unreadable_native_database_remains_explicit(tmp_path, monkeypatch):
    native = tmp_path / "database/icdb.dat"
    native.parent.mkdir()
    native.write_bytes(b"locked fixture")
    original = Path.open

    def opened(path, *args, **kwargs):
        if path == native:
            raise PermissionError("fixture native lock")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(PermissionError):
        _disk(native)
    observed = _disk(tmp_path, allow_unreadable=True)["files"][0]
    assert observed["sha256"] is None
    assert observed["content_observed"] is False
    assert observed["read_error"] == "PermissionError"
    assert observed["size"] == 14


def test_native_log_change_is_not_design_drift(tmp_path):
    log = tmp_path / "database/cdbsvr/log/session/log.txt"
    log.parent.mkdir(parents=True)
    log.write_text("first", encoding="utf-8")
    design = tmp_path / "database/icdb.dat"
    design.write_bytes(b"real design fixture")
    first = _project_files(tmp_path)
    log.write_text("second audit entry", encoding="utf-8")
    second = _project_files(tmp_path)
    assert first["files"] == second["files"]
    assert first["native_logs"] != second["native_logs"]
    design.write_bytes(b"changed design fixture")
    assert _project_files(tmp_path)["files"] != second["files"]
