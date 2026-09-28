"""project create | info | backup | restore."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import project_file
from ..errors import CLIError
from .common import (
    check_gate,
    confirmed,
    input_file,
    native,
    previewed,
    require_dangerous,
    text,
)
from .common import project_file as existing_project


def create(options: dict[str, Any]) -> dict[str, Any]:
    """A new Xpedition project copied from a template project.

    Designer cannot create a project through automation, so the adapter copies a
    known-good project folder, renames the `.prj`, points its absolute library keys
    into the copy and opens it. The token binds template and destination.
    """
    check_gate(options, "project create")
    path = Path(text(options, "project")).expanduser().resolve()
    template = Path(text(options, "template")).expanduser().resolve()
    if template.suffix.lower() != ".prj" or not template.is_file():
        raise CLIError(
            "E_NOT_FOUND", "template project file was not found", {"template": str(template)}
        )
    if path.suffix.lower() != ".prj":
        raise CLIError("E_VALIDATION", "the new project path must end in .prj", {"path": str(path)})
    if not str(path).isascii():
        raise CLIError(
            "E_VALIDATION",
            "the new project path must be ASCII: Designer cannot load new symbol files from "
            "a folder whose path has other characters",
            {"path": str(path)},
        )
    if path.exists():
        raise CLIError("E_CONFLICT", "project file already exists", {"path": str(path)})
    if path.parent.exists() and any(path.parent.iterdir()):
        raise CLIError(
            "E_CONFLICT", "the new project folder is not empty", {"path": str(path.parent)}
        )
    scope = {"operation": "project_create", "template": str(template), "project": str(path)}
    if options.get("dry_run"):
        preview = {
            "path": str(path),
            "project": path.stem,
            "template": str(template),
            "changes": [
                {
                    "action": "copy_project_folder",
                    "from": str(template.parent),
                    "to": str(path.parent),
                    "skipped": [
                        "Templates",
                        "Work",
                        "LogFiles",
                        "ProjectBackup",
                        "Thumbnail",
                        "*.bak",
                    ],
                },
                {"action": "rename_project_file", "path": str(path)},
                {"action": "rewrite_library_keys", "keys": ["CentralLibrary", "DBCFile"]},
                {"action": "open_project", "path": str(path)},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "a new project folder; if Designer has the template open it is closed "
                    "while the folder is copied"
                ),
            },
        }
        return previewed(preview, scope)
    backend = native()
    confirmed(options, scope)
    return backend.invoke(
        "clone_project", {"template": str(template), "project": str(path)}, timeout_seconds=600.0
    )


def _key(text_value: str, section: str, key: str) -> str:
    body = re.search(
        rf"^SECTION {re.escape(section)}[ \t]*\r?\n(.*?)^ENDSECTION", text_value, re.S | re.M
    )
    if body is None:
        return ""
    match = re.search(rf'^KEY {re.escape(key)}[ \t]+"([^"]*)"', body.group(1), re.M)
    return match.group(1) if match else ""


def _section_list(text_value: str, section: str, name: str) -> list[str]:
    body = re.search(
        rf"^SECTION {re.escape(section)}[ \t]*\r?\n(.*?)^ENDSECTION", text_value, re.S | re.M
    )
    return project_file.list_entries(body.group(1), name) if body else []


def info(options: dict[str, Any]) -> dict[str, Any]:
    """What the .prj says, from the file alone: no application is asked."""
    path = existing_project(options, "project info")
    content = path.read_text(encoding="utf-8", errors="replace")
    library = _key(content, "DesignInfo", "CentralLibrary")
    library_path = Path(library) if library else None
    if library_path is not None and not library_path.is_absolute():
        library_path = (path.parent / library_path).resolve()
    listed = project_file.designs(content)
    roots = {_key(content, item.name, "RootBlock") for item in listed if item.is_board()}
    designs = []
    for design in listed:
        board = design.pcb_path
        board_path = None
        if board:
            candidate = Path(board)
            board_path = candidate if candidate.is_absolute() else path.parent / candidate
        entry: dict[str, Any] = {
            "name": design.name,
            "kind": "board"
            if design.is_board()
            else ("schematic" if design.name in roots else "other"),
            "config_type": design.config_type or None,
        }
        if design.is_board():
            entry.update(
                {
                    "root_block": _key(content, design.name, "RootBlock") or None,
                    "board": str(board_path) if board_path else None,
                    "board_exists": bool(board_path and board_path.is_file()),
                    "layout_template": design.layout_template or None,
                    "symbol_libraries": _section_list(content, design.name, "Symbols"),
                    "parts_databases": _section_list(content, design.name, "PDBs"),
                    "cell_libraries": _section_list(content, design.name, "2dCellLibraries"),
                }
            )
        designs.append(entry)
    boards = [item for item in designs if item["kind"] == "board"]
    return {
        "project": path.stem,
        "path": str(path),
        "xpedition_version": _key(content, "DesignInfo", "DxD_Version") or None,
        "central_library": {
            "path": str(library_path) if library_path else None,
            "exists": bool(library_path and library_path.is_file()),
        },
        "designs": designs,
        "board": boards[0]["board"] if boards else None,
        "ascii_path": str(path).isascii(),
        "_untrusted": ["project", "path", "central_library", "designs", "board"],
    }


def _archive_outside(project: Path, archive: Path) -> None:
    folder = project.parent.resolve()
    if archive.resolve().parent == folder or folder in archive.resolve().parents:
        raise CLIError(
            "E_VALIDATION",
            "a backup inside the project folder would be removed by its own restore; "
            "write it beside the folder",
            {"output": str(archive), "project_folder": str(folder)},
        )


def backup(options: dict[str, Any]) -> dict[str, Any]:
    """The project folder zipped: the .prj, the schematic database, the board and the
    central library when it lives in the folder. Designer and Layout may keep it open;
    what they have saved is what is backed up."""
    from .. import __version__, project_backup

    project = existing_project(options, "project backup")
    raw = text(options, "output")
    archive = Path(raw).expanduser().resolve() if raw else project_backup.default_archive(project)
    if archive.suffix.lower() != ".zip":
        raise CLIError("E_VALIDATION", "--output is a .zip file", {"output": str(archive)})
    if archive.exists():
        raise CLIError("E_CONFLICT", "the backup file exists already", {"path": str(archive)})
    _archive_outside(project, archive)
    try:
        result = project_backup.create(project, archive, version=__version__)
    except project_backup.BackupError as exc:
        raise CLIError("E_IO", str(exc), {"output": str(archive)}) from exc
    result["closed_and_reopened"] = False
    if result["skipped"]:
        # Designer's iCDB server holds database/icdb.dat while the project is open: the
        # schematic itself could not be read. Let go of the project, back up again, and
        # open it again -- a backup without its schematic is no backup.
        from ..backends import NativeBackend

        backend = NativeBackend()
        if backend.status().get("available"):
            released = backend.invoke(
                "release_project", {"project": str(project)}, timeout_seconds=180
            )
            archive.unlink(missing_ok=True)
            try:
                result = project_backup.create(project, archive, version=__version__)
            finally:
                if released.get("designer_closed"):
                    backend.invoke("reopen_project", {"project": str(project)}, timeout_seconds=180)
            result["closed_and_reopened"] = bool(
                released.get("designer_closed") or released.get("layout_closed")
            )
    library = project_backup.central_library(project)
    result.update({"project": str(project), "central_library": library})
    if library["path"] and not library["inside"]:
        result["note"] = (
            "the central library lives outside the project folder and is not in this backup"
        )
    result["_untrusted"] = ["project", "backup", "skipped", "central_library"]
    return result


def restore(options: dict[str, Any]) -> dict[str, Any]:
    """Make the project folder what a backup holds.

    The dry run compares the backup with the folder file by file: what would be added,
    replaced and removed. The confirmed run (with --dangerous) closes the project in
    Designer and its board in Layout, zips the folder as it is first -- so a restore
    can itself be undone --, restores, and opens the project in Designer again.
    """
    import hashlib

    from .. import __version__, project_backup
    from ..backends import NativeBackend

    check_gate(options, "project restore")
    project = existing_project(options, "project restore")
    archive = input_file(options, "backup", "project restore", "the backup .zip")
    _archive_outside(project, archive)
    try:
        plan = project_backup.plan_restore(project, archive)
    except project_backup.BackupError as exc:
        raise CLIError("E_VALIDATION", str(exc), {"backup": str(archive)}) from exc
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    scope = {
        "operation": "project_restore",
        "project": str(project),
        "backup": str(archive),
        "archive": digest[:16],
        "plan": plan["digest"],
    }
    safety = project_backup.default_archive(project)
    if options.get("dry_run"):
        preview = {
            **project_backup.summary(plan),
            "project": str(project),
            "dangerous": True,
            "safety_backup": str(safety),
            "changes": [
                {"action": "close_in_designer_and_layout", "reopened_after": True},
                {"action": "back_up_the_folder_first", "archive": str(safety)},
                {"action": "restore_files", "count": len(plan["add"]) + len(plan["replace"])},
                {"action": "remove_files", "count": len(plan["remove"])},
            ],
            "risk": {
                "tier": "T2",
                "blast_radius": (
                    "every file of the project folder the backup differs from: the "
                    "schematic, the board and the library as they are now are replaced; "
                    "they are zipped first"
                ),
            },
        }
        return previewed(preview, scope)
    require_dangerous(options, "project restore replaces and removes the project's files")
    confirmed(options, scope)
    backend = NativeBackend()
    released: dict[str, Any] | None = None
    if backend.status().get("available"):
        released = backend.invoke("release_project", {"project": str(project)}, timeout_seconds=180)
    try:
        before = project_backup.create(project, safety, version=__version__)
    except project_backup.BackupError as exc:
        raise CLIError(
            "E_IO",
            f"the folder could not be backed up before the restore, so nothing was restored: {exc}",
            {"safety_backup": str(safety)},
        ) from exc
    result = project_backup.restore(project, archive, plan)
    reopened = None
    if released and released.get("designer_closed"):
        reopened = backend.invoke("reopen_project", {"project": str(project)}, timeout_seconds=180)
    return {
        "project": str(project),
        "backup": str(archive),
        "safety_backup": before["backup"],
        **result,
        "released": released,
        "reopened": bool(reopened and reopened.get("reopened")),
        "ok": result["complete"],
        "_untrusted": ["project", "backup", "safety_backup", "failed", "released"],
    }
