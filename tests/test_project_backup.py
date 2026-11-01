"""project backup | restore: the folder zipped, and made what a backup holds."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from xpedition_cli import project_backup as B


def _active_fake_designer(monkeypatch):
    from xpedition_cli import native_process

    # These scenarios specifically exercise adapter release/reopen. The global
    # empty inventory correctly takes the independent, no-adapter closed path.
    monkeypatch.setattr(
        native_process,
        "processes",
        lambda domain: (
            [{"pid": 4242, "source": "offline fixture"}] if domain == "schematic" else []
        ),
    )


def _folder(tmp_path: Path) -> Path:
    """A project folder: the .prj, a schematic database, a board, a library, scratch."""
    root = tmp_path / "board-a"
    for relative, content in {
        "Board.prj": 'KEY CentralLibrary "Lib/Lib.lmc"\n',
        "database/Schematic1.sch": "sheet one",
        "PCB/Board.pcb": "board",
        "Lib/Lib.lmc": "library",
        "Lib/Work/xpedition-cli/parts.hkp": "scratch",
        "PCB/LogFiles/drc.txt": "log",
        "database/old.bak": "old",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root / "Board.prj"


def test_a_backup_holds_all_files_including_scratch(tmp_path: Path) -> None:
    project = _folder(tmp_path)
    archive = B.default_archive(project)
    assert archive.parent == tmp_path / "board-a-backups" and archive.name.startswith("Board-")
    result = B.create(project, archive, version="9.9")
    assert result["complete"] is True and result["files"] == 7
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        manifest = json.loads(bundle.read(B.MANIFEST))
    assert names == {
        "Board.prj",
        "database/Schematic1.sch",
        "PCB/Board.pcb",
        "Lib/Lib.lmc",
        "Lib/Work/xpedition-cli/parts.hkp",
        "PCB/LogFiles/drc.txt",
        "database/old.bak",
        B.MANIFEST,
    }
    assert manifest["project"] == "Board.prj" and manifest["tool_version"] == "9.9"
    assert B.central_library(project)["inside"] is True
    with pytest.raises(B.BackupError, match="exists"):
        B.create(project, archive)


def test_locked_critical_file_retains_incomplete_and_never_completes(tmp_path, monkeypatch):
    project = _folder(tmp_path)
    critical = project.parent / "database/icdb.dat"
    critical.write_bytes(b"critical")
    original = Path.read_bytes

    def locked(path):
        if path == critical:
            raise PermissionError("locked critical database")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", locked)
    archive = tmp_path / "backup.zip"
    with pytest.raises(B.BackupError):
        B.create(project, archive)
    assert not archive.exists()
    assert archive.with_suffix(".zip.incomplete").exists()


def test_backup_release_failure_prevents_archive_and_reopen(cli, adapter, tmp_path, monkeypatch):
    _active_fake_designer(monkeypatch)
    project = _folder(tmp_path)
    adapter.fail("release_project", "E_HUMAN_REQUIRED", "closure not observed")
    archive = tmp_path / "blocked.zip"
    code, body = cli("project", "backup", "--project", str(project), "--output", str(archive))
    assert code != 0 and body["error"]["code"] == "E_HUMAN_REQUIRED"
    assert not archive.exists() and not archive.with_suffix(".zip.incomplete").exists()
    assert adapter.methods() == ["release_project"]


def test_completed_archive_retained_when_designer_reopen_is_not_observed(
    cli, adapter, tmp_path, monkeypatch
):
    _active_fake_designer(monkeypatch)
    project = _folder(tmp_path)
    archive = tmp_path / "saved.zip"
    adapter.on("release_project", {"quiescent": True, "designer_closed": True})
    adapter.on("reopen_project", {"reopened": False})
    code, body = cli("project", "backup", "--project", str(project), "--output", str(archive))
    assert code != 0 and body["error"]["code"] == "E_HUMAN_REQUIRED"
    assert body["error"]["details"]["backup_complete"] is True
    assert archive.exists() and zipfile.ZipFile(archive).testzip() is None


def test_a_restore_adds_replaces_and_removes_what_differs(tmp_path: Path) -> None:
    project = _folder(tmp_path)
    archive = tmp_path / "keep.zip"
    B.create(project, archive)
    root = project.parent
    (root / "PCB" / "Board.pcb").write_text("board, routed badly", encoding="utf-8")
    (root / "database" / "Schematic1.sch").unlink()
    (root / "database" / "Schematic2.sch").write_text("a sheet added since", encoding="utf-8")
    (root / "PCB" / "LogFiles" / "new.txt").write_text("a log", encoding="utf-8")
    plan = B.plan_restore(project, archive)
    assert plan["add"] == ["database/Schematic1.sch"]
    assert plan["replace"] == ["PCB/Board.pcb"]
    assert plan["remove"] == ["PCB/LogFiles/new.txt", "database/Schematic2.sch"]
    assert plan["unchanged"] == 5
    result = B.restore(project, archive, plan)
    assert result == {"written": 2, "removed": 2, "failed": [], "complete": True}
    assert (root / "PCB" / "Board.pcb").read_text(encoding="utf-8") == "board"
    assert (root / "database" / "Schematic1.sch").exists()
    assert not (root / "database" / "Schematic2.sch").exists()
    assert not (root / "PCB" / "LogFiles" / "new.txt").exists()
    assert B.plan_restore(project, archive)["unchanged"] == 7


def test_only_this_project_s_backup_is_restored(tmp_path: Path) -> None:
    project = _folder(tmp_path)
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as bundle:
        bundle.writestr(B.MANIFEST, json.dumps({"project": "Else.prj"}))
    with pytest.raises(B.BackupError, match="not 'Board.prj'"):
        B.plan_restore(project, other)
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as bundle:
        bundle.writestr("Board.prj", "x")
    with pytest.raises(B.BackupError, match="not a backup"):
        B.plan_restore(project, plain)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr(B.MANIFEST, json.dumps({"project": "Board.prj"}))
        bundle.writestr("../outside.txt", "x")
    with pytest.raises(B.BackupError, match="outside the project"):
        B.plan_restore(project, evil)


# ---- the commands ----


def test_backup_command_writes_beside_the_folder_and_notes_an_outside_library(
    cli, adapter, tmp_path
) -> None:
    adapter.on(
        "release_project", {"quiescent": True, "designer_closed": False, "layout_closed": False}
    )
    project = _folder(tmp_path)
    code, payload = cli(
        "project", "backup", "--project", str(project), "--output", str(tmp_path / "first.zip")
    )
    data = payload["data"]
    assert code == 0 and data["complete"] is True and Path(data["backup"]).is_file()
    assert data["central_library"]["inside"] is True and "note" not in data
    inside = project.parent / "x.zip"
    code, payload = cli("project", "backup", "--project", str(project), "--output", str(inside))
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"
    project.write_text('KEY CentralLibrary "D:/Shared/Lib.lmc"\n', encoding="utf-8")
    code, payload = cli(
        "project", "backup", "--project", str(project), "--output", str(tmp_path / "b.zip")
    )
    assert code == 0 and "outside the project folder" in payload["data"]["note"]


def test_restore_previews_then_needs_dangerous_and_backs_up_first(
    cli, adapter, tmp_path, monkeypatch
) -> None:
    _active_fake_designer(monkeypatch)
    project = _folder(tmp_path)
    archive = tmp_path / "keep.zip"
    B.create(project, archive)
    board = project.parent / "PCB" / "Board.pcb"
    board.write_text("broken", encoding="utf-8")
    adapter.on(
        "release_project",
        {
            "designer_closed": True,
            "layout_closed": True,
            "quiescent": True,
            "project": str(project),
        },
    )
    adapter.on("reopen_project", {"reopened": True, "project": str(project)})
    args = ["project", "restore", "--project", str(project), "--backup", str(archive)]
    code, payload = cli(*args, "--dry-run")
    preview = payload["data"]["preview"]
    assert code == 0 and preview["replace"] == {"count": 1, "sample": ["PCB/Board.pcb"]}
    assert preview["dangerous"] is True and adapter.calls == []
    token = payload["data"]["confirm_token"]
    code, payload = cli(*args, "--confirm", token)
    assert payload["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert board.read_text(encoding="utf-8") == "broken"
    code, payload = cli(*args, "--dangerous", "--confirm", token)
    data = payload["data"]
    assert code == 0 and data["ok"] is True and data["written"] == 1
    assert board.read_text(encoding="utf-8") == "board"
    assert adapter.methods() == ["release_project", "reopen_project"]
    with zipfile.ZipFile(data["safety_backup"]) as bundle:
        assert bundle.read("PCB/Board.pcb") == b"broken"  # the state before, kept


def test_a_restore_token_does_not_survive_a_folder_that_changed(cli, adapter, tmp_path) -> None:
    project = _folder(tmp_path)
    archive = tmp_path / "keep.zip"
    B.create(project, archive)
    (project.parent / "PCB" / "Board.pcb").write_text("changed", encoding="utf-8")
    args = ["project", "restore", "--project", str(project), "--backup", str(archive)]
    _, payload = cli(*args, "--dry-run")
    (project.parent / "database" / "Schematic3.sch").write_text("more", encoding="utf-8")
    code, payload = cli(*args, "--dangerous", "--confirm", payload["data"]["confirm_token"])
    assert code == 6 and payload["error"]["code"] == "E_CONFLICT"
