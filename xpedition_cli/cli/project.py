"""project create | info."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import project_file
from ..errors import CLIError
from .common import check_gate, confirmed, native, previewed, text
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
