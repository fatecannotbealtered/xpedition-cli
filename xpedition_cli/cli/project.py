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


def closed_copy_plan(options):
    """Pure file-only preflight shared with the public SDK's closed-copy branch."""
    from ..project_isolated_copy import CopyError, plan_copy

    if options.get("design") is not None:
        raise CLIError(
            "E_USAGE",
            "--closed-copy retains the source designs; --design is unsupported",
            {"write_attempted": False},
        )
    if not options.get("source_backup"):
        raise CLIError(
            "E_USAGE", "--closed-copy requires --source-backup", {"write_attempted": False}
        )
    try:
        return plan_copy(options.get("template"), options.get("project"), options["source_backup"])
    except CopyError as error:
        raise CLIError(
            error.code, error.message, {**error.details, "write_attempted": False}
        ) from error


def _closed_copy(options):
    from ..project_isolated_copy import CopyError, apply_copy

    plan = closed_copy_plan(options)
    scope = {
        "operation": "project_create_closed_copy",
        "project": plan["target_project"],
        "fingerprint": plan["fingerprint"],
    }
    if options.get("dry_run"):
        root = Path(plan["target_project"]).parent
        return previewed(
            {
                "path": plan["target_project"],
                "template": plan["source_project"]["path"],
                "creation_method": "closed_backup_copy",
                "closed_copy": plan,
                "native_calls_dispatched": 0,
                "opened": False,
                "changes": [
                    {
                        "action": "copy_complete_closed_backup",
                        "allowed_paths": [
                            str(root / name) for name in plan["genesis"]["file_manifest"]
                        ],
                    },
                    {
                        "action": "relocate_public_project_library_keys_only",
                        "exact_differences": plan["exact_differences"],
                    },
                    {"action": "verify_complete_target_file_set_and_hashes_without_native_calls"},
                ],
                "risk": {
                    "tier": "T1",
                    "blast_radius": "Only the new disjoint target folder; no Designer/Layout/"
                    "Library, Open, Save or Close. Complete byte copy does not prove native "
                    "openability.",
                },
            },
            scope,
        )
    confirmed(options, scope)
    try:
        return apply_copy(plan)
    except CopyError as error:
        raise CLIError(error.code, error.message, error.details) from error


def create(options: dict[str, Any]) -> dict[str, Any]:
    """A new Xpedition project copied from a template project.

    Initialized projects are cloned; stock setup templates use the native
    NewProject API to initialize their iCDB. The token binds both inputs and target.
    """
    check_gate(options, "project create")
    if options.get("closed_copy"):
        return _closed_copy(options)
    if options.get("source_backup") is not None:
        raise CLIError(
            "E_USAGE", "--source-backup requires --closed-copy", {"write_attempted": False}
        )
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
    if text(options, "design"):
        scope["design"] = text(options, "design")
    state = project_file.template_state(template)
    creation_cost = None
    if state["creation_method"] == "native_new_project":
        from ..project_create_budget import require_cost

        source = options.get("_project_create_source")
        promised = options.get("_project_create_cost")
        if type(promised) is not dict:
            raise CLIError(
                "E_CONFLICT",
                "Native creation requires its public plan's signed resource budget",
                {"write_attempted": False, "native_request_dispatched": False},
            )
        creation_cost = require_cost(source, promised)
        scope["execution_budget"] = creation_cost
    if options.get("dry_run"):
        preview = {
            "path": str(path),
            "project": path.stem,
            "template": str(template),
            "creation_method": state["creation_method"],
            "template_state": state,
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
                {
                    "action": "rewrite_library_keys",
                    "keys": ["CentralLibrary", "DBCFile"],
                    "lists": ["Symbols", "PDBs", "2dCellLibraries"],
                    "scope": "absolute references inside the copied source directory only",
                },
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
        if state["creation_method"] == "native_new_project":
            preview["project_create_cost"] = creation_cost
            preview["changes"].insert(
                2,
                {
                    "action": "initialize_native_database",
                    "design": text(options, "design") or "Board1",
                },
            )
        return previewed(preview, scope)
    backend = native()
    confirmed(options, scope)
    params = {"template": str(template), "project": str(path)}
    if text(options, "design"):
        params["design"] = text(options, "design")
    return backend.invoke(
        "clone_project",
        params,
        timeout_seconds=float(creation_cost["required_seconds"]) if creation_cost else 120.0,
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
    if options.get("preflight"):
        from ..design_preflight import inspect

        return {"preflight": inspect(options.get("requirements"))}
    if options.get("requirements"):
        raise CLIError("E_USAGE", "--requirements requires --preflight")
    path = existing_project(options, "project inspect")
    content = path.read_text(encoding="utf-8", errors="replace")
    library = _key(content, "DesignInfo", "CentralLibrary")
    library_path = project_file.local_path(path.parent, library) if library else None
    listed = project_file.designs(content)
    roots = {_key(content, item.name, "RootBlock") for item in listed if item.is_board()}
    designs = []
    for design in listed:
        board = design.pcb_path
        board_path = project_file.local_path(path.parent, board) if board else None
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


def _offline_release_preflight(project: Path) -> dict[str, Any]:
    """No adapter is needed only when every native process inventory is empty."""
    from ..native_process import processes

    running = {}
    for domain in ("library", "schematic", "pcb"):
        rows = processes(domain)
        if type(rows) is not list:
            raise CLIError(
                "E_HUMAN_REQUIRED",
                "The complete native process inventory is unavailable",
                {
                    "write_attempted": False,
                    "process_inventory_complete": False,
                    "save_count": 0,
                    "close_count": 0,
                },
            )
        running[domain] = rows
    if any(running.values()):
        raise CLIError(
            "E_HUMAN_REQUIRED",
            "Native document state is unavailable; release preflight stopped",
            {"processes": running, "write_attempted": False, "save_count": 0, "close_count": 0},
        )
    return {
        "project": str(project.resolve()),
        "board": None,
        "library_state": {"status": "no_native_processes_without_adapter_dispatch"},
        "release_preflight": {
            domain: {"processes": [], "application": None, "document": None} for domain in running
        },
        "read_only": True,
        "save_count": 0,
        "close_count": 0,
        "observation_basis": "Complete empty native process inventories; no adapter dispatch",
    }


def release_preflight(project: Path) -> dict[str, Any]:
    """Public backup planning observes release eligibility without saving/closing."""
    from ..backends import NativeBackend

    try:
        return _offline_release_preflight(project)
    except CLIError as error:
        if error.code != "E_HUMAN_REQUIRED" or not any(
            (error.details or {}).get("processes", {}).values()
        ):
            raise
    backend = NativeBackend()
    if backend.status().get("available"):
        return backend.invoke(
            "release_project_preflight",
            {"project": str(project.resolve()), "observe_only": True},
            timeout_seconds=180,
        )
    return _offline_release_preflight(project)


def _release_for_archive(
    project: Path, expected_release_preflight: dict[str, Any] | None = None
) -> tuple[Any, dict[str, Any]]:
    from ..backends import NativeBackend

    try:
        observed = _offline_release_preflight(project)
    except CLIError as error:
        if error.code != "E_HUMAN_REQUIRED" or not any(
            (error.details or {}).get("processes", {}).values()
        ):
            raise
        if isinstance(expected_release_preflight, dict) and (
            expected_release_preflight.get("library_state", {}).get("status")
            in {
                "no_native_processes_without_adapter",
                "no_native_processes_without_adapter_dispatch",
            }
        ):
            raise CLIError(
                "E_CONFLICT",
                "Native process inventories changed since the empty backup preview",
                {"write_attempted": False, "save_count": 0, "close_count": 0},
            ) from error
    else:
        if expected_release_preflight is not None and observed != expected_release_preflight:
            raise CLIError(
                "E_CONFLICT",
                "Release dependencies changed since the backup preview",
                {
                    "write_attempted": False,
                    "expected": expected_release_preflight,
                    "observed": observed,
                    "save_count": 0,
                    "close_count": 0,
                },
            )
        return NativeBackend(), {
            "quiescent": True,
            "source": "No native processes observed",
            "release_preflight": observed,
        }
    backend = NativeBackend()
    if backend.status().get("available"):
        params: dict[str, Any] = {"project": str(project)}
        if expected_release_preflight is not None:
            params["expected_release_preflight"] = expected_release_preflight
        try:
            released = backend.invoke("release_project", params, timeout_seconds=180)
        except CLIError as error:
            if (error.details or {}).get("write_attempted") is not False:
                error.details = {**(error.details or {}), "release_outcome_unverified": True}
            raise
        if released.get("quiescent") is not True:
            raise CLIError(
                "E_HUMAN_REQUIRED",
                "Project release was not confirmed; archive stopped",
                {"release": released, "release_outcome_unverified": True},
            )
        return backend, released
    observed = _offline_release_preflight(project)
    if expected_release_preflight is not None and observed != expected_release_preflight:
        raise CLIError(
            "E_CONFLICT",
            "Release dependencies changed since the read-only backup preview",
            {
                "write_attempted": False,
                "expected": expected_release_preflight,
                "observed": observed,
            },
        )
    return backend, {
        "quiescent": True,
        "source": "No native processes observed",
        "release_preflight": observed,
    }


def backup(options: dict[str, Any]) -> dict[str, Any]:
    """Save/release the owned project before a stable, complete folder archive."""
    from .. import __version__, project_backup

    leave_closed = options.get("leave_closed", False)
    if type(leave_closed) is not bool:
        raise CLIError(
            "E_VALIDATION", "--leave-closed must be a boolean", {"write_attempted": False}
        )
    project = existing_project(options, "project backup")
    raw = text(options, "output")
    archive = Path(raw).expanduser().resolve() if raw else project_backup.default_archive(project)
    if archive.suffix.lower() != ".zip":
        raise CLIError("E_VALIDATION", "--output is a .zip file", {"output": str(archive)})
    if archive.exists():
        raise CLIError("E_CONFLICT", "the backup file exists already", {"path": str(archive)})
    _archive_outside(project, archive)
    backend, released = _release_for_archive(project, options.get("_expected_release_preflight"))
    try:
        result = project_backup.create(project, archive, version=__version__)
    except project_backup.BackupError as exc:
        raise CLIError(
            "E_IO",
            str(exc),
            {
                "output": str(archive),
                "backup_complete": False,
                "incomplete": str(archive.with_suffix(".zip.incomplete")),
                "release": released,
                "requested_leave_closed": leave_closed,
                "actual_closed_without_reopen": released.get("quiescent") is True,
            },
        ) from exc
    result["closed_and_reopened"] = False
    result["requested_leave_closed"] = leave_closed
    # This is a closed-document state observation, not a claim that Save/Close
    # ran when the source was already closed, or that any later reopen passed.
    result["actual_closed_without_reopen"] = released.get("quiescent") is True
    result["release"] = released
    result["atomic_snapshot"] = False
    if released.get("designer_closed") and not leave_closed:
        reopened = None
        # Once Open is dispatched this cannot be presented as a leave-closed
        # result, including false replies, exceptions and unknown outcomes.
        result["actual_closed_without_reopen"] = False
        try:
            reopened = backend.invoke(
                "reopen_project", {"project": str(project)}, timeout_seconds=180
            )
            if not isinstance(reopened, dict) or reopened.get("reopened") is not True:
                raise CLIError(
                    "E_VERIFY_INCOMPLETE",
                    "Designer reopening was not confirmed by its returned result",
                    {"reopen": reopened},
                )
        except Exception as error:
            raise CLIError(
                "E_HUMAN_REQUIRED",
                "The archive completed, but Designer reopening was not confirmed",
                {
                    "backup": result,
                    "release": released,
                    "reopen": reopened,
                    "backup_complete": True,
                    "reopen_error": {
                        "code": getattr(error, "code", "E_SERVER"),
                        "message": str(error),
                        "details": getattr(error, "details", {}) or {},
                        "type": type(error).__name__,
                    },
                },
            ) from error
        result["closed_and_reopened"] = True
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
    backend, released = _release_for_archive(project)
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
