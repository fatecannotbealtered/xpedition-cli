from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import __version__
from .audit import config_dir, record
from .backends import ExchangeBackend, MockBackend, NativeBackend
from .capabilities import CapabilityRegistry
from .changelog import markdown as changelog_markdown
from .changeset import (
    apply_operations,
    bom_rows,
    load_changeset,
    persist_changes,
    preview_changes,
    validate_changeset,
    verify_project,
)
from .confirm import consume, issue
from .contract_gen import SCHEMA_VERSION
from .errors import CLIError
from .mcp_server import MCPServer
from .models import append_history, load_history, restore_backup
from .output import emit, failure, redact, success
from .query_page import query_page
from .reference_data import reference, release_readiness
from .reference_query import SELECTOR_FLAGS, validate_reference_options
from .review_engine import run_review
from .session import (
    clear_native,
    record_native_attach,
    record_native_failure,
    record_native_start,
)
from .session import (
    logs as session_logs,
)
from .session import (
    status as session_status,
)

VALUE_FLAGS = {
    *SELECTOR_FLAGS,
    "--format",
    "--fields",
    "--backend",
    "--pace",
    "--project",
    "--other-project",
    "--input",
    "--name",
    "--kind",
    "--output",
    "--color",
    "--schematic",
    "--design",
    "--sheet",
    "--template",
    "--partition",
    "--scheme",
    "--width",
    "--height",
    "--net",
    "--layer",
    "--margin",
    "--radius",
    "--diameter",
    "--inset",
    "--formats",
    "--class",
    "--side",
    "--scale",
    "--points",
    "--at",
    "--padstack",
    "--to",
    "--rotate",
    "--refdes",
    "--file",
    "--gap",
    "--geometry",
    "--nets",
    "--min",
    "--expansion",
    "--passes",
    "--layers",
    "--changeset",
    "--confirm",
    "--since",
    "--rules",
    "--limit",
    "--offset",
    "--query",
    "--transport",
}
BOOL_FLAGS = {
    "--all",
    "--top-view",
    "--no-run",
    "--online",
    "--unroute",
    "--replace",
    "--json",
    "--compact",
    "--dry-run",
    "--quiet",
    "--backup",
    "--dangerous",
    "--package",
    "--help",
    "-h",
}


def parse_argv(argv: list[str]) -> tuple[list[str], dict[str, Any]]:
    positionals: list[str] = []
    options: dict[str, Any] = {
        "format": "json",
        "fields": None,
        "backend": "mock",
        "compact": False,
        "dry_run": False,
        "confirm": None,
        "project": None,
        "other_project": None,
        "input": None,
        "name": None,
        "kind": None,
        "output": None,
        "color": None,
        "schematic": None,
        "design": None,
        "sheet": None,
        "changeset": None,
        "since": None,
        "rules": None,
        "limit": None,
        "offset": None,
        "query": None,
        "transport": None,
        "quiet": False,
        "backup": True,
        "dangerous": False,
        "help": False,
    }
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--version":
            options["version_flag"] = True
            index += 1
            continue
        if token in BOOL_FLAGS:
            if token in {"--help", "-h"}:
                options["help"] = True
            elif token == "--json":
                options["format"] = "json"
            else:
                options[token[2:].replace("-", "_")] = True
            index += 1
            continue
        if token.startswith("--"):
            name, separator, inline = token.partition("=")
            if name not in VALUE_FLAGS:
                raise CLIError("E_USAGE", f"unknown option: {name}", {"option": name})
            value = inline if separator else None
            if value is None:
                index += 1
                if index >= len(argv):
                    raise CLIError("E_USAGE", f"option {name} requires a value", {"option": name})
                value = argv[index]
            key = name[2:].replace("-", "_")
            if name in SELECTOR_FLAGS:
                if key in options:
                    raise CLIError("E_USAGE", f"option {name} may only be supplied once")
                if not separator and str(value).startswith("--"):
                    raise CLIError("E_USAGE", f"option {name} requires a value")
            options[key] = value
            index += 1
            continue
        positionals.append(token)
        index += 1
    if options.get("format") not in {"json", "text", "raw"}:
        raise CLIError("E_VALIDATION", "--format must be json, text, or raw")
    if options.get("since") and not re.match(r"^\d+\.\d+\.\d+(?:[-+].*)?$", str(options["since"])):
        raise CLIError("E_VALIDATION", "--since must be a semantic version")
    for key in ("limit", "offset"):
        if options.get(key) is not None:
            try:
                options[key] = int(options[key])
            except ValueError as exc:
                raise CLIError("E_VALIDATION", f"--{key} must be an integer") from exc
            if options[key] < 0:
                raise CLIError("E_VALIDATION", f"--{key} must not be negative")
    validate_reference_options(positionals, options)
    return positionals, options


def _project_path(positionals: list[str], options: dict[str, Any]) -> str | None:
    if options.get("project"):
        return str(options["project"])
    if len(positionals) > 2 and not positionals[2].startswith("-"):
        return positionals[2]
    return None


def _schematic_export(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`schematic export`: render the project's schematic to a new PDF with sch2pdf.

    The output is a derived report, not a design change, so there is no
    dry-run/confirm gate; the adapter refuses to replace an existing file instead.
    """
    project_path = _project_path(positionals, options)
    if not project_path:
        raise CLIError("E_USAGE", "schematic export requires --project PATH")
    output = options.get("output")
    if output and not str(output).lower().endswith(".pdf"):
        raise CLIError(
            "E_VALIDATION",
            "schematic export writes PDF; --output must end in .pdf",
            {"output": str(output)},
        )
    if str(options.get("backend", "mock")) != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "schematic export renders through Xpedition's sch2pdf and needs the NativeBackend",
            {"hint": "pass --backend native_xpedition with the .prj path"},
        )
    native = NativeBackend()
    native.require_implemented()
    params: dict[str, Any] = {"project": str(project_path)}
    if output:
        params["output"] = str(output)
    if options.get("color") is not None:
        try:
            params["color"] = int(options["color"])
        except ValueError as exc:
            raise CLIError(
                "E_VALIDATION",
                "--color must be an integer from 0 to 4",
                {"color": str(options["color"])},
            ) from exc
    if options.get("schematic"):
        params["schematic"] = str(options["schematic"])
    return native.invoke("export_pdf", params, timeout_seconds=600.0)


def _project_init_native(path: Path, template: str, options: dict[str, Any]) -> dict[str, Any]:
    """`project init --template`: a new Xpedition project copied from a template project.

    Designer cannot create a project through automation, so the adapter copies a
    known-good project folder, renames the `.prj`, points its absolute library
    keys into the copy and opens it. Dry-run first; the token binds template and
    destination.
    """
    if str(options.get("backend", "mock")) != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "project init --template copies an Xpedition project and needs the NativeBackend",
            {"hint": "pass --backend native_xpedition with the template .prj and the new .prj"},
        )
    template_path = Path(template).expanduser().resolve()
    if template_path.suffix.lower() != ".prj" or not template_path.is_file():
        raise CLIError(
            "E_NOT_FOUND", "template project file was not found", {"template": str(template_path)}
        )
    if path.suffix.lower() != ".prj":
        raise CLIError("E_VALIDATION", "the new project path must end in .prj", {"path": str(path)})
    if not str(path).isascii():
        raise CLIError(
            "E_VALIDATION",
            "the new project path must be ASCII: Designer cannot load new symbol files "
            "from a folder whose path has other characters",
            {"path": str(path)},
        )
    if path.parent.exists() and any(path.parent.iterdir()):
        raise CLIError(
            "E_CONFLICT", "the new project folder is not empty", {"path": str(path.parent)}
        )
    scope = {
        "operation": "project_init_native",
        "template": str(template_path),
        "project": str(path),
    }
    preview = {
        "path": str(path),
        "project": path.stem,
        "template": str(template_path),
        "changes": [
            {
                "action": "copy_project_folder",
                "from": str(template_path.parent),
                "to": str(path.parent),
                "skipped": ["Templates", "Work", "LogFiles", "ProjectBackup", "Thumbnail", "*.bak"],
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
        "_untrusted": ["path", "project", "template", "changes", "risk.blast_radius"],
    }
    if options.get("dry_run"):
        token, expires_at = issue(scope)
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    if options.get("confirm") is None:
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "project init requires --dry-run, then --confirm <confirm_token>",
        )
    native = NativeBackend()
    native.require_implemented()
    consume(str(options["confirm"]), scope)
    return native.invoke(
        "clone_project",
        {"template": str(template_path), "project": str(path)},
        timeout_seconds=600.0,
    )


def _read_design(options: dict[str, Any], command: str) -> tuple[Path, dict[str, Any]]:
    design_path = options.get("design")
    if not design_path:
        raise CLIError("E_USAGE", f"{command} requires --design FILE")
    design_file = Path(str(design_path)).expanduser()
    try:
        design = json.loads(design_file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CLIError(
            "E_NOT_FOUND", f"design file cannot be read: {exc}", {"design": str(design_file)}
        ) from exc
    except json.JSONDecodeError as exc:
        raise CLIError(
            "E_VALIDATION", f"design file is not valid JSON: {exc}", {"design": str(design_file)}
        ) from exc
    return design_file, design


def _library_build(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`library build`: placeholder padstacks, cells and parts for a design.

    The plan is pure Python (`xpedition_cli.library_hkp`), so `--dry-run` shows
    every part-to-cell mapping on any backend. The confirmed run imports the
    three HKP texts into the project's central library through the stock
    converters and, with `--package`, runs the packager afterwards.
    """
    import hashlib

    from . import library_hkp, schematic_layout

    project_path = _project_path(positionals, options)
    if not project_path:
        raise CLIError("E_USAGE", "library build requires --project PATH")
    design_file, design = _read_design(options, "library build")
    canonical = str(Path(str(project_path)).expanduser().resolve())
    try:
        plan, texts = library_hkp.library_texts(design, options.get("partition"))
    except (schematic_layout.DesignError, ValueError) as exc:
        raise CLIError(
            "E_VALIDATION", f"design cannot be packaged: {exc}", {"design": str(design_file)}
        ) from exc
    partition = plan.partition
    summary = plan.summary()
    digest = hashlib.sha256(
        "".join(texts[key] for key in sorted(texts)).encode("utf-8")
    ).hexdigest()[:16]
    scope = {
        "operation": "library_build",
        "project": canonical,
        "partition": partition,
        "digest": digest,
    }
    preview = {
        "partition": partition,
        "padstacks": len(plan.padstacks),
        "cells": len(plan.cells),
        "parts": len(plan.parts),
        "changes": [
            {"action": "merge_padstacks", "into": "Layout/PadstackDB.psk"},
            {"action": "write_cell_partition", "file": f"CellDBLibs/{partition}.cel"},
            {"action": "write_parts_partition", "file": f"PartsDBLibs/{partition}.pdb"},
            {"action": "register_pdb_in_project", "entry": f"PartsDBLibs\\{partition}.pdb"},
            {"action": "register_cells_in_project", "list": "2dCellLibraries"},
        ],
        "summary": summary,
        "risk": {
            "tier": "T1",
            "blast_radius": (
                "the project's central library gains padstacks, a cell partition and a "
                "parts partition; Designer's project is closed and reopened meanwhile"
            ),
        },
        "_untrusted": ["summary", "risk.blast_radius"],
    }
    if plan.cell_partitions:
        preview["changes"].append(
            {
                "action": "register_cell_partitions_in_project",
                "partitions": sorted(plan.cell_partitions),
                "note": "cells of KiCad footprints imported earlier; the parts reference them",
            }
        )
    if options.get("dry_run"):
        token, expires_at = issue(scope)
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    if options.get("confirm") is None:
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "library build requires --dry-run, then --confirm <confirm_token>",
        )
    if str(options.get("backend", "mock")) != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "library build imports into an Xpedition central library and needs the NativeBackend",
            {"hint": "pass --backend native_xpedition with the .prj path"},
        )
    native = NativeBackend()
    native.require_implemented()
    consume(str(options["confirm"]), scope)
    request = {
        "project": canonical,
        "partition": partition,
        "cell_partitions": sorted(plan.cell_partitions),
        **texts,
    }
    result = native.invoke("library_import", request, timeout_seconds=900.0)
    if options.get("package") and result.get("ok"):
        result["package"] = native.invoke("package", {"project": canonical}, timeout_seconds=900.0)
    result["summary"] = summary
    untrusted = list(result.get("_untrusted") or [])
    untrusted.extend(["summary", "package"])
    result["_untrusted"] = untrusted
    return result


def _board_request(
    positionals: list[str], options: dict[str, Any], command: str
) -> tuple[Path, str]:
    project_path = _project_path(positionals, options)
    if not project_path:
        raise CLIError("E_USAGE", f"{command} requires --project PATH")
    path = Path(str(project_path)).expanduser().resolve()
    if path.suffix.lower() not in {".prj", ".pcb"}:
        raise CLIError(
            "E_VALIDATION", f"{command} expects a .prj or .pcb path", {"path": str(path)}
        )
    if not path.is_file():
        raise CLIError("E_NOT_FOUND", "project file was not found", {"path": str(path)})
    return path, str(path)


def _native_for_write(command: str, options: dict[str, Any]) -> NativeBackend:
    if str(options.get("backend", "mock")) != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            f"{command} drives Xpedition Layout and needs the NativeBackend",
            {"hint": "pass --backend native_xpedition with the .prj path"},
        )
    native = NativeBackend()
    native.require_implemented()
    return native


def _pcb_create(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb create`: the project's board from a layout template.

    The preview reads the `.prj` (pure text): which design is the board design and
    what JobWizard will be asked to do. The confirmed run goes through the adapter,
    which copies the template into the central library when it is missing, lists the
    board design first, runs `JobWizard -createnew`, and registers the cell
    partitions the board will need for forward annotation.
    """
    from . import project_file

    path, canonical = _board_request(positionals, options, "pcb create")
    if path.suffix.lower() != ".prj":
        raise CLIError("E_VALIDATION", "pcb create expects the .prj path", {"path": canonical})
    text = path.read_text(encoding="utf-8", errors="replace")
    listed = project_file.designs(text)
    design = project_file.board_design(listed, options.get("design"))
    if design is None:
        raise CLIError(
            "E_NOT_FOUND",
            "the project lists no PCB design",
            {"project": canonical, "designs": [item.name for item in listed]},
        )
    replace = bool(options.get("replace", False))
    if design.pcb_path and not replace:
        raise CLIError(
            "E_CONFLICT",
            "the design already names a board",
            {"design": design.name, "pcb": design.pcb_path, "hint": "pass --replace"},
        )
    template = str(options.get("template") or project_file.DEFAULT_LAYOUT_TEMPLATE)
    name = str(options.get("name") or path.stem)
    if not re.match(r"^[A-Za-z0-9_-]+$", name):
        raise CLIError("E_VALIDATION", "--name must be a plain identifier", {"name": name})
    board = f"PCB\\{name}.pcb"
    scope = {
        "operation": "pcb_create",
        "project": canonical,
        "design": design.name,
        "template": template,
        "name": name,
        "replace": replace,
    }
    preview = {
        "project": canonical,
        "design": design.name,
        "template": template,
        "pcb": board,
        "replace": replace,
        "changes": [
            *(
                [
                    {
                        "action": "back_up_layout_folder",
                        "folder": "PCB",
                        "archive": "PCB-backup-<timestamp>.zip",
                    },
                    {"action": "remove_layout_data", "pcb": design.pcb_path, "folder": "PCB"},
                ]
                if replace
                else []
            ),
            {"action": "ensure_layout_template", "path": f"Templates/Layout/{template}"},
            {"action": "list_design_first", "design": design.name},
            {
                "action": "run_jobwizard",
                "arguments": [
                    "-createnew",
                    "-f",
                    "-prj",
                    canonical,
                    "-newpcb",
                    board,
                    "-template",
                    template,
                ],
            },
            {"action": "register_cells_in_project", "list": project_file.CELL_LIST},
        ],
        "risk": {
            "tier": "T1",
            "blast_radius": (
                (
                    "the design's whole existing layout — placement, routing, pours, "
                    "output setups — is zipped to PCB-backup-<timestamp>.zip beside the "
                    "project and the PCB folder is then deleted; a Layout process still "
                    "holding a file in it is ended; then "
                )
                if replace
                else ""
            )
            + (
                "a new PCB folder beside the .prj; the .prj gains the board path, the "
                "template name and a cell-library list; Designer's project is closed and "
                "reopened meanwhile"
            ),
        },
        "_untrusted": ["project", "risk.blast_radius"],
    }
    if options.get("dry_run"):
        token, expires_at = issue(scope)
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    if options.get("confirm") is None:
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb create requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb create", options)
    consume(str(options["confirm"]), scope)
    return native.invoke(
        "pcb_create",
        {
            "project": canonical,
            "design": design.name,
            "template": template,
            "name": name,
            "replace": replace,
        },
        timeout_seconds=600.0,
    )


def _pcb_annotate(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb annotate`: forward-annotate the packaged schematic into the board.

    Layout runs the packager, Database Load and the netload itself through its
    Project Integration object; the CLI opens the board (answering Layout's own
    prompts), runs it, reads `ForwardAnnotation.txt` back and saves the board.
    """
    from . import project_file

    path, canonical = _board_request(positionals, options, "pcb annotate")
    pcb = canonical if path.suffix.lower() == ".pcb" else None
    if pcb is None:
        text = path.read_text(encoding="utf-8", errors="replace")
        design = project_file.board_design(project_file.designs(text), options.get("design"))
        if design is None:
            raise CLIError("E_NOT_FOUND", "the project lists no PCB design", {"project": canonical})
        if not design.pcb_path:
            raise CLIError(
                "E_NOT_FOUND",
                "the design has no board yet",
                {"project": canonical, "design": design.name, "hint": "run pcb create first"},
            )
        board = Path(design.pcb_path)
        pcb = str(board if board.is_absolute() else path.parent / board)
    unroute = bool(options.get("unroute", False))
    scope = {"operation": "pcb_annotate", "project": canonical, "unroute": unroute}
    preview = {
        "project": canonical,
        "pcb": pcb,
        "changes": [
            {"action": "open_board", "pcb": pcb},
            *([{"action": "delete_traces_and_vias"}] if unroute else []),
            {"action": "forward_annotate"},
            {"action": "save_board"},
        ],
        "risk": {
            "tier": "T1",
            "blast_radius": (
                "the board's components and nets are replaced by the packaged schematic's "
                "and Layout saves the board"
            ),
        },
        "_untrusted": ["project", "pcb", "risk.blast_radius"],
    }
    if options.get("dry_run"):
        token, expires_at = issue(scope)
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    if options.get("confirm") is None:
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb annotate requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb annotate", options)
    consume(str(options["confirm"]), scope)
    return native.invoke(
        "forward_annotate",
        {"project": canonical, "start": True, "unroute": unroute},
        timeout_seconds=900.0,
    )


def _pcb_arrange(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb arrange`: a first placement of the board's unplaced parts, in rows by sheet.

    Both runs need Layout: the plan depends on each footprint's measured size and
    on the board outline. The dry run asks the adapter for the plan (nothing is
    saved) and binds the token to the plan's digest; the confirmed run asks for the
    plan again, so a board that changed in between no longer matches the token,
    then places the parts and saves the board. `--all` includes placed parts.
    """
    path, canonical = _board_request(positionals, options, "pcb arrange")
    include_all = bool(options.get("all", False))
    zones = _design_zones(options)
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb arrange requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb arrange", options)
    request: dict[str, Any] = {
        "project": canonical,
        "all": include_all,
        "zones": zones,
        "start": True,
    }
    result = native.invoke("arrange_components", {**request, "apply": False}, timeout_seconds=900.0)
    digest = str(result.get("digest") or "")
    scope = {
        "operation": "pcb_arrange",
        "project": canonical,
        "all": include_all,
        "digest": digest,
    }
    if options.get("dry_run"):
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": result.get("pcb"),
            "board": result.get("board"),
            "plan": result.get("plan"),
            "labels": result.get("labels"),
            "clusters": result.get("clusters"),
            "summary": result.get("summary"),
            "skipped_placed": result.get("skipped_placed"),
            "digest": digest,
            "routing": result.get("routing"),
            "changes": [
                {"action": "delete_traces_and_vias", **(result.get("routing") or {})},
                {"action": "place_components", "count": len(result.get("plan") or [])},
                {"action": "write_zone_labels", "count": len(result.get("labels") or [])},
                {"action": "save_board"},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "every listed part is placed on the top side at the planned position "
                    "and the board is saved; parts already placed are left alone unless --all"
                ),
            },
            "_untrusted": ["project", "pcb", "plan", "skipped_placed", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke(
        "arrange_components",
        {**request, "apply": True, "digest": digest, "pace": _pace_option(options, "pcb arrange")},
        timeout_seconds=900.0,
    )


def _pcb_show(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb show`: bring the board to the front under a display scheme that shows parts.

    The view changes, the design does not, so there is no confirmation gate. The
    default scheme is `Loc: All On`; `--scheme` picks another of the board's
    schemes; `--output` captures the window to a new PNG.
    """
    path, canonical = _board_request(positionals, options, "pcb show")
    output = options.get("output")
    if output and not str(output).lower().endswith(".png"):
        raise CLIError(
            "E_VALIDATION",
            "pcb show captures PNG; --output must end in .png",
            {"output": str(output)},
        )
    if str(options.get("backend", "mock")) != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "pcb show drives Xpedition Layout's window and needs the NativeBackend",
            {"hint": "pass --backend native_xpedition with the .prj path"},
        )
    native = NativeBackend()
    native.require_implemented()
    params: dict[str, Any] = {"project": canonical, "start": True}
    if options.get("scheme"):
        params["scheme"] = str(options["scheme"])
    if options.get("top_view"):
        params["top_view"] = True
    if output:
        params["output"] = str(output)
    return native.invoke("show_board", params, timeout_seconds=420.0)


def _pcb_geometry(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb geometry`: the board as data — outline, pads, traces, vias, planes, holes,
    silkscreen and the components with their pins — for planning a hand layout."""
    path, canonical = _board_request(positionals, options, "pcb geometry")
    output = options.get("output")
    if output and not str(output).lower().endswith(".json"):
        raise CLIError("E_VALIDATION", "pcb geometry writes JSON; --output must end in .json")
    if str(options.get("backend", "mock")) != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "pcb geometry reads the board through Xpedition Layout and needs the NativeBackend",
            {"hint": "pass --backend native_xpedition with the .prj path"},
        )
    native = NativeBackend()
    native.require_implemented()
    params: dict[str, Any] = {
        "project": canonical,
        "replace": bool(options.get("replace", False)),
        "start": True,
    }
    if output:
        params["output"] = str(Path(str(output)).expanduser().resolve())
    return native.invoke("board_geometry", params, timeout_seconds=600.0)


def _routing_items(options: dict[str, Any], command: str) -> list[dict[str, Any]]:
    """The trace/via items of `pcb trace` (one trace from the flags, or a plan file)."""
    from .native_com_adapter import AdapterError, plan_items

    plan_file = options.get("file")
    try:
        if plan_file:
            path = Path(str(plan_file)).expanduser().resolve()
            if not path.is_file():
                raise CLIError("E_NOT_FOUND", "plan file was not found", {"path": str(path)})
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CLIError("E_VALIDATION", f"cannot read the plan: {exc}") from exc
            return plan_items(data)
        if command == "pcb via":
            item: dict[str, Any] = {
                "kind": "via",
                "net": options.get("net"),
                "at": options.get("at"),
                "padstack": options.get("padstack"),
            }
        else:
            item = {
                "kind": "trace",
                "net": options.get("net"),
                "layer": options.get("layer") or 1,
                "width": options.get("width") or 0.254,
                "points": options.get("points"),
            }
        return plan_items({"items": [item]})
    except AdapterError as exc:
        raise CLIError(exc.code, exc.message, exc.details) from exc


def _geometry_model(options: dict[str, Any], command: str) -> dict[str, Any] | None:
    """The board geometry file named by --geometry, for the offline checks."""
    if not options.get("geometry"):
        return None
    geometry = Path(str(options["geometry"])).expanduser().resolve()
    if not geometry.is_file():
        raise CLIError("E_NOT_FOUND", "geometry file was not found", {"path": str(geometry)})
    try:
        model = json.loads(geometry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CLIError("E_VALIDATION", f"cannot read the geometry: {exc}") from exc
    if not isinstance(model, dict) or "pads" not in model:
        raise CLIError("E_VALIDATION", f"{command}: --geometry is the file pcb geometry writes")
    return model


def _pcb_stitch(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb stitch`: the plan for a via beside every surface-mount pad of a net (ground
    stitching), from the geometry file alone — no Layout needed."""
    from . import routing_plan

    model = _geometry_model(options, "pcb stitch")
    if model is None:
        raise CLIError("E_USAGE", "pcb stitch requires --geometry board.json (from pcb geometry)")
    net = str(options.get("net") or "GND")
    try:
        width = float(options.get("width") or 0.3)
    except ValueError as exc:
        raise CLIError("E_VALIDATION", "--width is millimetres") from exc
    if width <= 0:
        raise CLIError("E_VALIDATION", "--width must be positive")
    board = routing_plan.Board(model)
    items, left = routing_plan.stitch_vias(board, net, width)
    result: dict[str, Any] = {
        "net": net,
        "width": width,
        "vias": sum(1 for item in items if item["kind"] == "via"),
        "items": [
            {**item, "at": list(item["at"])}
            if item["kind"] == "via"
            else {**item, "points": [list(p) for p in item["points"]]}
            for item in items
        ],
        "without_room": left,
        "_untrusted": ["items", "without_room"],
    }
    if options.get("output"):
        output = Path(str(options["output"])).expanduser().resolve()
        if output.exists() and not options.get("replace"):
            raise CLIError("E_CONFLICT", "output file already exists", {"path": str(output)})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({"items": result["items"]}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        result["path"] = str(output)
    return result


def _pace_option(options: dict[str, Any], command: str) -> float:
    """`--pace SECONDS`: the wait between placed items so a person at Layout's screen
    watches the work grow (0, the default, is as fast as Layout takes it)."""
    if options.get("pace") is None:
        return 0.0
    try:
        pace = float(options["pace"])
    except ValueError as exc:
        raise CLIError("E_VALIDATION", f"{command}: --pace is seconds") from exc
    if pace < 0 or pace > 10:
        raise CLIError("E_VALIDATION", f"{command}: --pace is 0 to 10 seconds", {"pace": pace})
    return pace


def _pcb_trace(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb trace` / `pcb via`: traces and vias placed where the person says."""
    command = "pcb via" if positionals[1] == "via" else "pcb trace"
    path, canonical = _board_request(positionals, options, command)
    items = _routing_items(options, command)
    model = _geometry_model(options, command)
    checked: list[str] = []
    if model is not None:
        from . import routing_plan

        checked = routing_plan.check_plan(items, routing_plan.Board(model))
        if checked and not options.get("dangerous"):
            raise CLIError(
                "E_VALIDATION",
                f"{command}: the plan fails the clearance check against the geometry "
                "(fix it, or pass --dangerous to let Layout judge)",
                {"problems": checked[:40], "count": len(checked)},
            )
    pace = _pace_option(options, command)
    digest = hashlib.sha256(
        json.dumps(items, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    scope = {"operation": "pcb_trace", "project": canonical, "items": digest}
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            f"{command} requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write(command, options)
    request: dict[str, Any] = {"project": canonical, "items": items, "start": True}
    if pace:
        request["pace"] = pace
    if options.get("dry_run"):
        plan = native.invoke("hand_route", {**request, "apply": False}, timeout_seconds=600.0)
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": plan.get("pcb"),
            "items": plan.get("items"),
            "nets": plan.get("nets"),
            "opens_before": plan.get("opens_before"),
            "warnings": plan.get("warnings"),
            "checked_against_geometry": model is not None,
            "checker": checked,
            "changes": [
                {
                    "action": "put_traces_and_vias",
                    "traces": sum(1 for item in items if item["kind"] == "trace"),
                    "vias": sum(1 for item in items if item["kind"] == "via"),
                },
                {"action": "regenerate_planes"},
                {"action": "save"},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "the board gains the traces and vias listed; Layout refuses any that "
                    "violate a clearance; the board is saved"
                ),
            },
            "_untrusted": ["project", "pcb", "items", "warnings", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke("hand_route", {**request, "apply": True}, timeout_seconds=1200.0)


def _pcb_unroute(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb unroute`: delete the traces and vias of some nets, or of all."""
    path, canonical = _board_request(positionals, options, "pcb unroute")
    nets = [item.strip() for item in str(options.get("nets") or "").split(",") if item.strip()]
    everything = bool(options.get("all", False))
    at = str(options.get("at") or "").strip()
    layer = options.get("layer")
    if not nets and not everything and not at:
        raise CLIError("E_USAGE", "pcb unroute requires --nets A,B, --all, or --at x,y [--layer N]")
    if layer is not None and not str(layer).isdigit():
        raise CLIError("E_VALIDATION", "--layer is a layer number")
    scope = {
        "operation": "pcb_unroute",
        "project": canonical,
        "nets": "all" if everything else ",".join(sorted(nets)),
        "at": at,
        "layer": str(layer or ""),
    }
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb unroute requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb unroute", options)
    request: dict[str, Any] = {"project": canonical, "nets": nets, "all": everything, "start": True}
    if at:
        request["at"] = at
        if layer is not None:
            request["layer"] = int(layer)
    if options.get("dry_run"):
        plan = native.invoke("unroute_nets", {**request, "apply": False}, timeout_seconds=600.0)
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": plan.get("pcb"),
            "nets": plan.get("nets"),
            "at": plan.get("at"),
            "layer": plan.get("layer"),
            "to_delete": plan.get("to_delete"),
            "changes": [
                {"action": "delete_traces_and_vias", **(plan.get("to_delete") or {})},
                {"action": "regenerate_planes"},
                {"action": "save"},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": "the routing of the named nets is deleted and the board saved",
            },
            "_untrusted": ["project", "pcb", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke("unroute_nets", {**request, "apply": True}, timeout_seconds=600.0)


def _pcb_move(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb move`: one part to a position (its cell origin) and rotation."""
    path, canonical = _board_request(positionals, options, "pcb move")
    refdes = str(options.get("refdes") or (positionals[2] if len(positionals) > 2 else "")).strip()
    if not refdes:
        raise CLIError("E_USAGE", "pcb move requires --refdes U1 --to x,y [--rotate DEG]")
    from .native_com_adapter import AdapterError, parse_points

    try:
        x, y = parse_points(options.get("to"), 1)[0]
    except AdapterError as exc:
        raise CLIError("E_USAGE", "--to takes x,y in millimetres") from exc
    rotation = None
    if options.get("rotate") is not None:
        try:
            rotation = float(options["rotate"])
        except ValueError as exc:
            raise CLIError("E_VALIDATION", "--rotate is degrees") from exc
    scope = {
        "operation": "pcb_move",
        "project": canonical,
        "refdes": refdes,
        "to": f"{x},{y}",
        "rotate": rotation,
    }
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb move requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb move", options)
    request: dict[str, Any] = {
        "project": canonical,
        "refdes": refdes,
        "x": x,
        "y": y,
        "rotation": rotation,
        "start": True,
    }
    if options.get("dry_run"):
        plan = native.invoke("move_component", {**request, "apply": False}, timeout_seconds=600.0)
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": plan.get("pcb"),
            "refdes": refdes,
            "before": plan.get("before"),
            "target": plan.get("target"),
            "routing": plan.get("routing"),
            "changes": [
                {"action": "place_component", "refdes": refdes, "target": plan.get("target")},
                {"action": "save"},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "one part moves (its traces stay where they were and may need rerouting); "
                    "Layout refuses a position that touches another part; the board is saved"
                ),
            },
            "_untrusted": ["project", "pcb", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke("move_component", {**request, "apply": True}, timeout_seconds=600.0)


def _pcb_labels(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb labels`: every silkscreen designator moved to a free spot beside its part."""
    path, canonical = _board_request(positionals, options, "pcb labels")
    try:
        gap = float(options["gap"]) if options.get("gap") is not None else None
    except ValueError as exc:
        raise CLIError("E_VALIDATION", "--gap is millimetres") from exc
    if gap is not None and gap < 0:
        raise CLIError("E_VALIDATION", "--gap must not be negative")
    scope = {"operation": "pcb_labels", "project": canonical, "gap": gap}
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb labels requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb labels", options)
    request: dict[str, Any] = {"project": canonical, "start": True}
    if gap is not None:
        request["gap"] = gap
    if options.get("dry_run"):
        plan = native.invoke("tidy_labels", {**request, "apply": False}, timeout_seconds=600.0)
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": plan.get("pcb"),
            "labels": plan.get("labels"),
            "moved": plan.get("moved"),
            "unplaced": plan.get("unplaced"),
            "changes": [
                {"action": "move_silkscreen_designators", "count": plan.get("moved")},
                {"action": "save"},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "the silkscreen designators move; nothing electrical changes; "
                    "the board is saved"
                ),
            },
            "_untrusted": ["project", "pcb", "labels", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke("tidy_labels", {**request, "apply": True}, timeout_seconds=600.0)


def _pcb_render(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb render`: a PNG of the board drawn from its geometry (no screen needed)."""
    path, canonical = _board_request(positionals, options, "pcb render")
    output = options.get("output")
    if not output:
        raise CLIError("E_USAGE", "pcb render requires --output board.png")
    if not str(output).lower().endswith(".png"):
        raise CLIError(
            "E_VALIDATION",
            "pcb render writes PNG; --output must end in .png",
            {"output": str(output)},
        )
    side = str(options.get("side") or "top").lower()
    if side not in {"top", "bottom"}:
        raise CLIError("E_VALIDATION", "--side is top or bottom", {"side": side})
    try:
        scale = float(options.get("scale") or 20.0)
    except ValueError as exc:
        raise CLIError("E_VALIDATION", "--scale is pixels per millimetre") from exc
    if not 1.0 <= scale <= 200.0:
        raise CLIError("E_VALIDATION", "--scale must be between 1 and 200")
    if str(options.get("backend", "mock")) != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "pcb render reads the board through Xpedition Layout and needs the NativeBackend",
            {"hint": "pass --backend native_xpedition with the .prj path"},
        )
    native = NativeBackend()
    native.require_implemented()
    params: dict[str, Any] = {
        "project": canonical,
        "output": str(Path(str(output)).expanduser().resolve()),
        "side": side,
        "scale": scale,
        "replace": bool(options.get("replace", False)),
        "start": True,
    }
    return native.invoke("render_board", params, timeout_seconds=600.0)


def _design_zones(options: dict[str, Any]) -> dict[str, str]:
    """Zone labels per sheet from a design file: `sheets[].zone`, else its `number`."""
    if not options.get("design"):
        return {}
    _design_file, design = _read_design(options, "pcb arrange")
    zones: dict[str, str] = {}
    for index, sheet in enumerate(design.get("sheets") or [], 1):
        if not isinstance(sheet, dict):
            continue
        number = str(sheet.get("number") or index)
        key = number.lstrip("0") or number
        label = str(sheet.get("zone") or "").strip()
        if label:
            zones[key] = label
    return zones


def _pcb_outline(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb outline`: replace the board outline by a width × height rectangle (mm)."""
    path, canonical = _board_request(positionals, options, "pcb outline")
    try:
        width = float(options.get("width") or 0)
        height = float(options.get("height") or 0)
    except ValueError as exc:
        raise CLIError("E_VALIDATION", "--width and --height are millimetres") from exc
    if width <= 0 or height <= 0:
        raise CLIError("E_USAGE", "pcb outline requires --width MM and --height MM")
    try:
        radius = float(options.get("radius") or 0)
    except ValueError as exc:
        raise CLIError("E_VALIDATION", "--radius is millimetres") from exc
    if radius < 0 or radius >= min(width, height) / 2:
        raise CLIError("E_VALIDATION", "--radius must be below half of the shorter side")
    scope = {
        "operation": "pcb_outline",
        "project": canonical,
        "width": width,
        "height": height,
        "radius": radius,
    }
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb outline requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb outline", options)
    request: dict[str, Any] = {
        "project": canonical,
        "width": width,
        "height": height,
        "radius": radius,
        "start": True,
    }
    if options.get("dry_run"):
        current = native.invoke("board_outline", {**request, "apply": False}, timeout_seconds=420.0)
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": current.get("pcb"),
            "before": current.get("before"),
            "requested": {"width": width, "height": height, "radius": radius},
            "changes": [
                {"action": "put_board_outline", "width": width, "height": height, "radius": radius},
                {"action": "save_board"},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "the board outline becomes the requested rectangle from the origin; "
                    "parts outside it stay where they are"
                ),
            },
            "_untrusted": ["project", "pcb", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke("board_outline", {**request, "apply": True}, timeout_seconds=420.0)


def _pcb_rules(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb rules`: a net class with its trace widths (Constraint Manager automation)."""
    path, canonical = _board_request(positionals, options, "pcb rules")
    class_name = str(options.get("class") or "").strip()
    nets = [item.strip() for item in str(options.get("nets") or "").split(",") if item.strip()]
    try:
        width = float(options["width"]) if options.get("width") is not None else None
        minimum = float(options["min"]) if options.get("min") is not None else None
        expansion = float(options["expansion"]) if options.get("expansion") is not None else None
    except ValueError as exc:
        raise CLIError("E_VALIDATION", "--width, --min and --expansion are millimetres") from exc
    if not class_name or width is None:
        raise CLIError("E_USAGE", "pcb rules requires --class NAME and --width MM (and --nets)")
    if (
        width <= 0
        or (minimum is not None and minimum <= 0)
        or (expansion is not None and expansion <= 0)
    ):
        raise CLIError("E_VALIDATION", "widths must be positive")
    scope = {
        "operation": "pcb_rules",
        "project": canonical,
        "class": class_name,
        "nets": ",".join(nets),
        "width": width,
        "min": minimum,
        "expansion": expansion,
    }
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb rules requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb rules", options)
    request: dict[str, Any] = {
        "project": canonical,
        "class": class_name,
        "nets": nets,
        "width": width,
        "minimum": minimum,
        "expansion": expansion,
        "start": True,
    }
    if options.get("dry_run"):
        current = native.invoke("net_rules", {**request, "apply": False}, timeout_seconds=300.0)
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": current.get("pcb"),
            "class": class_name,
            "nets": nets,
            "widths": current.get("widths"),
            "classes": current.get("classes"),
            "missing_nets": current.get("missing_nets"),
            "changes": [
                {"action": "ensure_net_class", "class": class_name},
                {"action": "assign_nets", "nets": nets},
                {"action": "set_trace_widths", "widths": current.get("widths")},
                {"action": "synch_ces"},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "the constraint database gains or changes one net class and its trace "
                    "widths on every layer; Layout re-reads its constraints"
                ),
            },
            "_untrusted": ["project", "pcb", "classes", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke("net_rules", {**request, "apply": True}, timeout_seconds=600.0)


def _pcb_export(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb export`: the fabrication package — ODB++, Gerber, NC drill, centroid, BOM."""
    path, canonical = _board_request(positionals, options, "pcb export")
    formats_text = str(options.get("formats") or "odb,gerber,ncdrill")
    formats = [item.strip().lower() for item in formats_text.split(",") if item.strip()]
    unknown = [
        item for item in formats if item not in {"odb", "odbpp", "gerber", "ncdrill", "drill"}
    ]
    if unknown or not formats:
        raise CLIError(
            "E_VALIDATION",
            "--formats takes a comma list of odb, gerber, ncdrill",
            {"unknown": unknown},
        )
    output = (
        str(Path(str(options["output"])).expanduser().resolve()) if options.get("output") else ""
    )
    scope = {
        "operation": "pcb_export",
        "project": canonical,
        "formats": formats_text,
        "output": output,
    }
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb export requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb export", options)
    request: dict[str, Any] = {"project": canonical, "formats": formats_text, "start": True}
    if output:
        request["output"] = output
    if options.get("dry_run"):
        plan = native.invoke(
            "manufacturing_output", {**request, "apply": False}, timeout_seconds=120.0
        )
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": plan.get("pcb"),
            "formats": plan.get("formats"),
            "package": plan.get("package"),
            "setup_changes": plan.get("setup_changes"),
            "existing": plan.get("existing"),
            "changes": [
                {
                    "action": "patch_output_setups",
                    "files": list((plan.get("setup_changes") or {}).keys()),
                },
                {"action": "run_output_dialogs", "formats": plan.get("formats")},
                {"action": "write_package", "folder": plan.get("package")},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "the board is closed and reopened in Layout when its output setups "
                    "need patching; Layout writes its Output folders; the package folder "
                    "is written"
                ),
            },
            "_untrusted": ["project", "pcb", "package", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke("manufacturing_output", {**request, "apply": True}, timeout_seconds=1800.0)


def _pcb_holes(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb holes`: a non-plated mounting hole in each corner of the outline."""
    path, canonical = _board_request(positionals, options, "pcb holes")
    try:
        diameter = float(options.get("diameter") or 2.2)
        inset = float(options.get("inset") or 3.5)
    except ValueError as exc:
        raise CLIError("E_VALIDATION", "--diameter and --inset are millimetres") from exc
    if diameter <= 0 or inset <= 0:
        raise CLIError("E_VALIDATION", "--diameter and --inset must be positive millimetres")
    replace = bool(options.get("replace", False))
    scope = {
        "operation": "pcb_holes",
        "project": canonical,
        "diameter": diameter,
        "inset": inset,
        "replace": replace,
    }
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb holes requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb holes", options)
    request: dict[str, Any] = {
        "project": canonical,
        "diameter": diameter,
        "inset": inset,
        "replace": replace,
        "start": True,
    }
    if options.get("dry_run"):
        current = native.invoke(
            "mounting_holes", {**request, "apply": False}, timeout_seconds=420.0
        )
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": current.get("pcb"),
            "board": current.get("board"),
            "padstack": current.get("padstack"),
            "existing": current.get("existing"),
            "planned": current.get("planned"),
            "changes": [
                {"action": "put_mounting_holes", "count": len(current.get("planned") or [])},
                {"action": "remove_existing_holes", "count": len(current.get("existing") or [])}
                if replace
                else {"action": "keep_existing_holes", "count": len(current.get("existing") or [])},
                {"action": "save_board"},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "non-plated mounting holes are added at the corners of the outline and "
                    "the board is saved; parts already there are not moved"
                ),
            },
            "_untrusted": ["project", "pcb", "existing", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke("mounting_holes", {**request, "apply": True}, timeout_seconds=420.0)


def _pcb_pour(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb pour`: a plane shape for a net on a layer, inset from the outline."""
    path, canonical = _board_request(positionals, options, "pcb pour")
    net = str(options.get("net") or "GND")
    try:
        layer = int(options.get("layer") or 2)
        margin = float(options.get("margin")) if options.get("margin") is not None else 1.0
    except ValueError as exc:
        raise CLIError("E_VALIDATION", "--layer is an integer and --margin millimetres") from exc
    if layer < 1 or margin < 0:
        raise CLIError("E_VALIDATION", "--layer starts at 1 and --margin is not negative")
    replace = bool(options.get("replace", False))
    scope = {
        "operation": "pcb_pour",
        "project": canonical,
        "net": net,
        "layer": layer,
        "margin": margin,
        "replace": replace,
    }
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb pour requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb pour", options)
    request: dict[str, Any] = {
        "project": canonical,
        "net": net,
        "layer": layer,
        "margin": margin,
        "replace": replace,
        "start": True,
    }
    if options.get("dry_run"):
        current = native.invoke("plane_pour", {**request, "apply": False}, timeout_seconds=420.0)
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": current.get("pcb"),
            "net": net,
            "layer": layer,
            "layers": current.get("layers"),
            "rectangle": current.get("rectangle"),
            "existing": current.get("existing"),
            "changes": [
                {"action": "put_plane_shape", "net": net, "layer": layer, "replace": replace},
                {"action": "save_board"},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "one plane shape on the layer, assigned to the net; the board is saved"
                ),
            },
            "_untrusted": ["project", "pcb", "existing", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke("plane_pour", {**request, "apply": True}, timeout_seconds=420.0)


def _pcb_route(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb route`: Layout's autorouter passes over every net, then save.

    `--passes` names the passes and their effort, `route:1-5,viamin:1-3,smooth:1-3`
    by default; the dry run reports how much of the board is routed now.
    """
    path, canonical = _board_request(positionals, options, "pcb route")
    passes = str(options.get("passes") or "route:1-5,viamin:1-3,smooth:1-3")
    layers = str(options.get("layers") or "")
    unroute = bool(options.get("unroute", False))
    scope = {
        "operation": "pcb_route",
        "project": canonical,
        "passes": passes,
        "layers": layers,
        "unroute": unroute,
    }
    if options.get("confirm") is None and not options.get("dry_run"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "pcb route requires --dry-run, then --confirm <confirm_token>",
        )
    native = _native_for_write("pcb route", options)
    request: dict[str, Any] = {
        "project": canonical,
        "passes": passes,
        "layers": layers,
        "unroute": unroute,
        "start": True,
    }
    if options.get("dry_run"):
        current = native.invoke("route_board", {**request, "apply": False}, timeout_seconds=420.0)
        token, expires_at = issue(scope)
        preview = {
            "project": canonical,
            "pcb": current.get("pcb"),
            "passes": current.get("passes"),
            "before": current.get("before"),
            "unrouted_before": current.get("unrouted_before"),
            "changes": [
                {"action": "run_route_passes", "count": len(current.get("passes") or [])},
                {"action": "save_board"},
            ],
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "traces and vias are added or changed on every net the passes touch; "
                    "the board is saved"
                ),
            },
            "_untrusted": ["project", "pcb", "unrouted_before", "risk.blast_radius"],
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    consume(str(options["confirm"]), scope)
    return native.invoke("route_board", {**request, "apply": True}, timeout_seconds=1800.0)


def _pcb_drc(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`pcb drc`: Layout's Batch DRC, then every hazard it left on the board.

    A check, not a design change, so there is no confirmation gate. `--no-run`
    only reads the hazards already there; `--online` adds the online DRC's.
    """
    path, canonical = _board_request(positionals, options, "pcb drc")
    if str(options.get("backend", "mock")) != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "pcb drc runs Xpedition Layout's Batch DRC and needs the NativeBackend",
            {"hint": "pass --backend native_xpedition with the .prj path"},
        )
    native = NativeBackend()
    native.require_implemented()
    params: dict[str, Any] = {
        "project": canonical,
        "start": True,
        "run": not bool(options.get("no_run", False)),
        "online": bool(options.get("online", False)),
    }
    return native.invoke("batch_drc", params, timeout_seconds=900.0)


def _schematic_draw(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`schematic draw`: plan a schematic from a design description, then draw it.

    The plan is pure Python, so `--dry-run` works on any backend and shows the
    sheets, parts, netlist and convention issues before a token is issued. The
    confirmed run wipes and redraws every listed sheet through Designer, reopens
    the project and reports whether the netlist read back matches the plan.
    """
    import hashlib

    from . import schematic_layout

    project_path = _project_path(positionals, options)
    if not project_path:
        raise CLIError("E_USAGE", "schematic draw requires --project PATH")
    design_path = options.get("design")
    if not design_path:
        raise CLIError("E_USAGE", "schematic draw requires --design FILE")
    design_file = Path(str(design_path)).expanduser()
    try:
        design = json.loads(design_file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CLIError(
            "E_NOT_FOUND", f"design file cannot be read: {exc}", {"design": str(design_file)}
        ) from exc
    except json.JSONDecodeError as exc:
        raise CLIError(
            "E_VALIDATION", f"design file is not valid JSON: {exc}", {"design": str(design_file)}
        ) from exc
    canonical = str(Path(str(project_path)).expanduser().resolve())
    try:
        params = schematic_layout.plan_to_params(design, canonical)
    except schematic_layout.DesignError as exc:
        raise CLIError(
            "E_VALIDATION", f"design cannot be drawn: {exc}", {"design": str(design_file)}
        ) from exc
    summary = params["summary"]
    digest = hashlib.sha256(json.dumps(params["ops"], sort_keys=True).encode("utf-8")).hexdigest()[
        :16
    ]
    scope = {"operation": "schematic_draw", "project": canonical, "plan": digest}
    preview = {
        "changes": [
            {
                "action": "wipe_and_draw_sheet",
                "sheet": sheet["number"],
                "title": sheet["title"],
                "parts": sheet["parts"],
            }
            for sheet in summary["sheets"]
        ],
        "summary": summary,
        "risk": {
            "tier": "T1",
            "blast_radius": (
                "every sheet listed in the design is wiped and redrawn; symbol files are "
                "written into the project's central-library partition"
            ),
        },
        "_untrusted": ["summary", "risk.blast_radius"],
    }
    if options.get("dry_run"):
        token, expires_at = issue(scope)
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expires_at,
            "_untrusted": ["preview"],
        }
    if options.get("confirm") is None:
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            "schematic draw requires --dry-run, then --confirm <confirm_token>",
        )
    if str(options.get("backend", "mock")) != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "schematic draw draws through Xpedition Designer and needs the NativeBackend",
            {"hint": "pass --backend native_xpedition with the .prj path"},
        )
    native = NativeBackend()
    native.require_implemented()
    consume(str(options["confirm"]), scope)
    request = {key: value for key, value in params.items() if key != "summary"}
    result = native.invoke("draw", request, timeout_seconds=1800.0)
    result["summary"] = summary
    untrusted = list(result.get("_untrusted") or [])
    untrusted.append("summary")
    result["_untrusted"] = untrusted
    return result


def _schematic_show(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    """`schematic show`: activate a sheet, fit it and raise Designer's window.

    The view changes, the design does not, so there is no confirmation gate; an
    optional `--output` PNG captures the window and is never overwritten.
    """
    project_path = _project_path(positionals, options)
    if not project_path:
        raise CLIError("E_USAGE", "schematic show requires --project PATH")
    sheet = 1
    if options.get("sheet") is not None:
        try:
            sheet = int(options["sheet"])
        except ValueError as exc:
            raise CLIError(
                "E_VALIDATION",
                "--sheet must be a positive integer",
                {"sheet": str(options["sheet"])},
            ) from exc
        if sheet < 1:
            raise CLIError("E_VALIDATION", "--sheet must be a positive integer", {"sheet": sheet})
    output = options.get("output")
    if output and not str(output).lower().endswith(".png"):
        raise CLIError(
            "E_VALIDATION",
            "schematic show captures PNG; --output must end in .png",
            {"output": str(output)},
        )
    if str(options.get("backend", "mock")) != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "schematic show drives Xpedition Designer's window and needs the NativeBackend",
            {"hint": "pass --backend native_xpedition with the .prj path"},
        )
    native = NativeBackend()
    native.require_implemented()
    params: dict[str, Any] = {"project": str(project_path), "sheet": sheet}
    if output:
        params["output"] = str(output)
    return native.invoke("show", params, timeout_seconds=180.0)


def _changeset_path(positionals: list[str], options: dict[str, Any]) -> str | None:
    if options.get("changeset"):
        return str(options["changeset"])
    if len(positionals) > 2 and not positionals[2].startswith("-"):
        return positionals[2]
    return None


def _input_path(positionals: list[str], options: dict[str, Any]) -> str | None:
    if options.get("input"):
        return str(options["input"])
    if len(positionals) > 2 and not positionals[2].startswith("-"):
        return positionals[2]
    return None


def _backend(options: dict[str, Any]) -> MockBackend | NativeBackend:
    name = str(options.get("backend") or "mock")
    if name == "mock":
        return MockBackend()
    if name == "native_xpedition":
        backend = NativeBackend()
        backend.require_implemented()
        return backend
    raise CLIError("E_VALIDATION", f"unsupported backend: {name}", {"backend": name})


def _project_for_changeset(
    backend: MockBackend | NativeBackend,
    project_path: str | None,
    changeset: dict[str, Any],
    domain: str | None = None,
) -> tuple[dict[str, Any], Path | None]:
    project, path = (
        backend.load(project_path, domain=domain)
        if backend.name == "native_xpedition"
        else backend.load(project_path)
    )
    if path is None or not path.exists():
        project["project"] = str(changeset["project"])
    return project, path


def _project_hash(project: dict[str, Any]) -> str:
    canonical = json.dumps(
        project, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scope(
    project_path: Path | None,
    changeset: dict[str, Any],
    project: dict[str, Any],
    command: str = "change apply",
    backend: str = "mock",
) -> dict[str, Any]:
    return {
        "command": command,
        "backend": backend,
        "project_path": str(project_path) if project_path else None,
        "account": "local-user",
        "permission": "write",
        "project_hash": _project_hash(project),
        "changeset": changeset,
    }


def _rollback_scope(path: Path, project: dict[str, Any], backup: Path) -> dict[str, Any]:
    return {
        "operation": "rollback",
        "project_path": str(path),
        "account": "local-user",
        "permission": "write",
        "project_hash": _project_hash(project),
        "backup_hash": _file_hash(backup),
    }


def _exchange_scope(
    source: Path, target: Path, project: dict[str, Any], imported: dict[str, Any]
) -> dict[str, Any]:
    return {
        "operation": "exchange_import",
        "source": str(source),
        "source_hash": _file_hash(source),
        "project_path": str(target),
        "project_hash": _project_hash(project),
        "import_hash": _project_hash(imported),
        "account": "local-user",
        "permission": "write",
    }


def _init_scope(path: Path, project_name: str) -> dict[str, Any]:
    return {
        "command": "project init",
        "operation": "project_init",
        "project_path": str(path),
        "project_name": project_name,
        "account": "local-user",
        "permission": "write",
        "target_exists": False,
    }


_SESSION_DOMAINS = {
    "pcb": "pcb",
    "layout": "pcb",
    "schematic": "schematic",
    "designer": "schematic",
}


def _session_domain(options: dict[str, Any]) -> str:
    """Pick the Xpedition application a session verb targets.

    Layout and Designer are separate applications with separate COM classes, so an
    agent has to be able to say which one it means; without this the session verbs
    could only ever reach Layout.
    """
    raw = options.get("kind")
    if raw is None and options.get("project"):
        path = str(options["project"]).lower()
        return "schematic" if path.endswith((".prj", ".dproj")) else "pcb"
    if raw is None:
        return "pcb"
    value = str(raw).strip().lower()
    if value not in _SESSION_DOMAINS:
        raise CLIError(
            "E_VALIDATION",
            f"unsupported session kind: {raw}",
            {"kind": str(raw), "choices": sorted(set(_SESSION_DOMAINS))},
        )
    return _SESSION_DOMAINS[value]


def _changelog(since: str | None) -> dict[str, Any]:
    text = changelog_markdown()
    entries: list[dict[str, Any]] = []
    matches = list(
        re.finditer(r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?\s*$", text, re.MULTILINE)
    )
    for index, match in enumerate(matches):
        version = match.group(1)
        if version.lower() == "unreleased":
            continue
        if since and _semver_key(version) <= _semver_key(since):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        changes = {}
        for category in ("added", "changed", "fixed", "deprecated", "removed", "security"):
            section = re.search(
                rf"^### {category.title()}\s*$([\s\S]*?)(?=^### |\Z)", body, re.MULTILINE
            )
            changes[category] = (
                [
                    line[2:].strip()
                    for line in section.group(1).splitlines()
                    if line.strip().startswith("-")
                ]
                if section
                else []
            )
        entries.append({"version": version, "date": match.group(2), "changes": changes})
    result: dict[str, Any] = {"current_version": __version__, "entries": entries}
    if since:
        result["since"] = since
    return result


def _semver_key(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(value))
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def _context(options: dict[str, Any]) -> dict[str, Any]:
    backend_name = str(options.get("backend") or "mock")
    project_path = options.get("project")
    path = Path(project_path).expanduser().resolve() if project_path else None
    return {
        "version": __version__,
        "backend": backend_name,
        "config": {"directory": str(config_dir())},
        "project": {"path": str(path) if path else None, "exists": bool(path and path.exists())},
        "credentials": {"configured": False, "backend": "none"},
        "notices": [],
        "_untrusted": ["config.directory", "project"],
    }


def _doctor(options: dict[str, Any]) -> dict[str, Any]:
    native = NativeBackend().status()
    native_session = session_status("native_xpedition")
    native_runtime_failed = native_session.get("state") == "crashed"
    native_ready = bool(native.get("available")) and not native_runtime_failed
    checks = [
        {"check": "backend", "status": "pass", "fix": None, "message": "MockBackend is available"},
        {
            "check": "native_xpedition",
            "status": "pass" if native_ready else "warn",
            "fix": None
            if native_ready
            else (
                "inspect session status and repair Xpedition startup"
                if native_runtime_failed
                else (
                    "run scripts/register-xpedition-user.ps1 (or the official "
                    "Administrator registration)"
                    if native.get("automation_command_configured")
                    else "install the native COM adapter"
                )
            ),
            "message": (
                native_session.get("reason") if native_runtime_failed else native.get("reason")
            )
            or "native automation entry point configured",
        },
        {
            "check": "credentials",
            "status": "pass",
            "fix": None,
            "message": (
                "no login is required for MockBackend; native credentials are managed by Xpedition"
            ),
        },
    ]
    if options.get("project"):
        project_path = Path(str(options["project"])).expanduser().resolve()
        try:
            MockBackend().load(str(project_path))
            project_status = "pass" if project_path.exists() else "warn"
            checks.append(
                {
                    "check": "project",
                    "status": project_status,
                    "fix": None
                    if project_path.exists()
                    else "create the project file before native work",
                    "message": str(project_path),
                }
            )
        except CLIError as error:
            checks.append(
                {
                    "check": "project",
                    "status": "fail",
                    "fix": "repair the project JSON before continuing",
                    "message": error.message,
                }
            )
    # Read the single source rather than restating it: a hardcoded copy here went stale
    # the moment the smoke evidence changed, and doctor is what an agent checks first.
    readiness = release_readiness()
    level = str(readiness.get("level", "unknown"))
    checks.append(
        {
            "check": "release_readiness",
            "status": "pass" if level == "stable" else "warn",
            "fix": (
                None
                if level == "stable"
                else "record the licensed Xpedition R-C smoke loop before declaring stable"
            ),
            "message": f"{level}: {readiness.get('reason', '')}",
            "details": {
                "fcc_status": readiness.get("fcc_status"),
                "mock_upstream_status": readiness.get("mock_upstream_status"),
                "live_smoke_status": readiness.get("live_smoke_status"),
            },
        }
    )
    return {
        "checks": checks,
        "version": __version__,
        "backend": options.get("backend", "mock"),
        "_untrusted": ["checks[].message", "checks[].details"],
    }


def _query_matches(item: Any, query: str | None) -> bool:
    if not query:
        return True
    return str(query).casefold() in json.dumps(item, ensure_ascii=False, sort_keys=True).casefold()


def _list_data(
    project: dict[str, Any],
    items: Iterable[Any],
    options: dict[str, Any],
    untrusted: list[str] | None = None,
) -> dict[str, Any]:
    page = query_page(
        items,
        query=options.get("query"),
        limit=options.get("limit"),
        offset=int(options.get("offset") or 0),
    )
    return {
        "project": project["project"],
        "revision": project["revision"],
        **page,
        "_untrusted": untrusted or ["project", "items"],
    }


def _schematic_pins(project: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for component in project["components"]:
        refdes = str(component.get("refdes", ""))
        for pin in component.get("pins", []) or []:
            if isinstance(pin, dict):
                rows.append(
                    {
                        "id": f"{refdes}.{pin.get('number', pin.get('name', ''))}",
                        "refdes": refdes,
                        "number": str(pin.get("number", "")),
                        "name": str(pin.get("name", "")),
                        "type": str(pin.get("type", "")),
                        "net": pin.get("net"),
                        "no_connect": bool(pin.get("no_connect", False)),
                    }
                )
    return rows


def _schematic_unconnected(project: dict[str, Any]) -> list[dict[str, Any]]:
    connected = {
        str(pin) for connection in project["connections"] for pin in connection.get("pins", [])
    }
    # A pin under a no-connect mark is open on purpose and is not reported here.
    return [
        pin
        for pin in _schematic_pins(project)
        if pin["id"] not in connected and not pin.get("no_connect")
    ]


def _schematic_power(project: dict[str, Any]) -> list[dict[str, Any]]:
    power_names = {"gnd", "ground", "vcc", "vdd", "3v3", "5v", "12v"}
    return [
        net
        for net in project["nets"]
        if str(net.get("type", "")).casefold() in {"power", "ground"}
        or str(net.get("name", "")).casefold() in power_names
    ]


def _pcb_data(project: dict[str, Any]) -> dict[str, Any]:
    return project.get("pcb", {})


def _flatten_analysis(project: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind in ("erc", "drc", "dfm", "results"):
        for item in project.get("analysis", {}).get(kind, []):
            rows.append({"kind": kind, "result": item})
    return rows


def _run_mock_analysis(project: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if kind not in {"all", "erc", "drc", "dfm"}:
        raise CLIError("E_VALIDATION", "analysis --kind must be all, erc, drc, or dfm")
    findings: list[dict[str, Any]] = []
    checks = verify_project(project)
    if kind in {"all", "erc"}:
        for net in checks["connections_with_missing_nets"]:
            findings.append(
                {
                    "kind": "erc",
                    "severity": "error",
                    "code": "MISSING_NET",
                    "message": "connection references a missing net",
                    "evidence": [net],
                }
            )
        for pin in _schematic_unconnected(project):
            findings.append(
                {
                    "kind": "erc",
                    "severity": "warning",
                    "code": "UNCONNECTED_PIN",
                    "message": "component pin is not connected",
                    "evidence": [pin["id"]],
                }
            )
    if kind in {"all", "drc"}:
        for refdes in checks["duplicate_refdes"]:
            findings.append(
                {
                    "kind": "drc",
                    "severity": "error",
                    "code": "DUPLICATE_REFDES",
                    "message": "duplicate reference designator",
                    "evidence": [refdes],
                }
            )
        for component in project["components"]:
            if component.get("x") is None or component.get("y") is None:
                findings.append(
                    {
                        "kind": "drc",
                        "severity": "warning",
                        "code": "MISSING_COORDINATE",
                        "message": "component has no placement coordinate",
                        "evidence": [component["refdes"]],
                    }
                )
    if kind in {"all", "dfm"}:
        for row in bom_rows(project):
            if not row.get("internal_part_no") or not row.get("mpn"):
                findings.append(
                    {
                        "kind": "dfm",
                        "severity": "warning",
                        "code": "INCOMPLETE_BOM_ROW",
                        "message": "BOM row is missing a part identifier",
                        "evidence": [row.get("refdes")],
                    }
                )
    return findings


def _bom_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        part = str(row.get("internal_part_no") or row.get("mpn") or "UNSPECIFIED")
        group = groups.setdefault(part, {"part_number": part, "quantity": 0, "refdes": []})
        group["quantity"] += int(row.get("quantity") or 1)
        group["refdes"].append(str(row.get("refdes", "")))
    return list(groups.values())


def _bom_validation(rows: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        missing = [key for key in ("refdes", "internal_part_no") if not row.get(key)]
        if missing:
            issues.append({"index": index, "refdes": row.get("refdes"), "missing": missing})
        if int(row.get("quantity") or 0) <= 0:
            issues.append(
                {
                    "index": index,
                    "refdes": row.get("refdes"),
                    "message": "quantity must be positive",
                }
            )
    return not issues, issues


def _bom_compare(
    base_rows: list[dict[str, Any]], other_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    base = {str(row.get("refdes")): row for row in base_rows}
    other = {str(row.get("refdes")): row for row in other_rows}
    added = [other[key] for key in sorted(other.keys() - base.keys())]
    removed = [base[key] for key in sorted(base.keys() - other.keys())]
    changed = [
        {"refdes": key, "before": base[key], "after": other[key]}
        for key in sorted(base.keys() & other.keys())
        if base[key] != other[key]
    ]
    return {"added": added, "removed": removed, "changed": changed}


def _paged_review(
    project: dict[str, Any],
    rules_path: str | None,
    options: dict[str, Any],
    extra_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report = run_review(project, rules_path, extra_findings)
    findings = report["findings"]
    offset = min(len(findings), int(options.get("offset") or 0))
    limit = options.get("limit")
    end = len(findings) if limit is None else min(len(findings), offset + int(limit))
    report["findings"] = findings[offset:end]
    report["offset"] = offset
    report["next_offset"] = end if end < len(findings) else None
    report["has_more"] = end < len(findings)
    return report


def _project_diff(current: dict[str, Any], backup: dict[str, Any]) -> dict[str, Any]:
    added = []
    removed = []
    changed = []
    for key in sorted(set(current) | set(backup)):
        if key == "revision":
            continue
        if key not in backup:
            added.append({"field": key, "value": current[key]})
        elif key not in current:
            removed.append({"field": key, "value": backup[key]})
        elif current[key] != backup[key]:
            changed.append({"field": key, "before": backup[key], "after": current[key]})
    return {"added": added, "removed": removed, "changed": changed}


def _agent_query(project: dict[str, Any], query: str | None) -> Iterable[dict[str, Any]]:
    if not query:
        raise CLIError("E_USAGE", "agent query requires --query")
    items = (
        {"kind": kind, "value": item}
        for kind, key in (
            ("component", "components"),
            ("net", "nets"),
            ("connection", "connections"),
        )
        for item in project[key]
    )
    return (item for item in items if _query_matches(item, query))


def _agent_request(request: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    method = str(request.get("method", ""))
    params = request.get("params") or {}
    if not isinstance(params, dict):
        raise CLIError("E_VALIDATION", "agent request params must be an object")
    if method == "capabilities":
        return CapabilityRegistry().summary()
    project_path = params.get("project") or options.get("project")
    backend = _backend(
        {**options, "backend": params.get("backend", options.get("backend", "mock"))}
    )
    project, _ = backend.load(str(project_path) if project_path else None)
    if method == "snapshot":
        return backend.snapshot(project)
    if method == "query":
        query_options = {**options, "query": params.get("query")}
        return _list_data(
            project,
            _agent_query(project, query_options.get("query")),
            {**query_options, "query": None},
        )
    if method == "review":
        return _paged_review(project, params.get("rules"), options)
    raise CLIError("E_USAGE", f"unknown agent method: {method}", {"method": method})


def _agent_serve(options: dict[str, Any]) -> int:
    if options.get("transport") not in {None, "stdio", "mcp"}:
        raise CLIError("E_VALIDATION", "agent serve supports --transport stdio or mcp")
    if options.get("format") != "json":
        raise CLIError(
            "E_VALIDATION", "agent serve emits JSONL and does not support text or raw format"
        )
    if options.get("transport") == "mcp":

        def handler(method: str, params: dict[str, Any]) -> dict[str, Any]:
            return _agent_request({"method": method, "params": params}, options)

        return MCPServer(handler).serve()
    count = 0
    for line in sys.stdin:
        count += 1
        started = time.perf_counter()
        raw = line.strip()
        request_id: Any = None
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise CLIError("E_VALIDATION", "agent request must be a JSON object")
            request_id = request.get("id")
            if request_id is not None:
                request_id = str(request_id)
            data = _agent_request(request, options)
            response = {
                "ok": True,
                "schema_version": SCHEMA_VERSION,
                "type": "result",
                "id": request_id,
                "data": data,
                "meta": {"duration_ms": max(0, int((time.perf_counter() - started) * 1000))},
            }
        except CLIError as error:
            response = {
                "ok": False,
                "schema_version": SCHEMA_VERSION,
                "type": "error",
                "id": request_id,
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details or {},
                    "retryable": error.is_retryable,
                },
                "meta": {"duration_ms": max(0, int((time.perf_counter() - started) * 1000))},
            }
        except (json.JSONDecodeError, TypeError) as error:
            response = {
                "ok": False,
                "schema_version": SCHEMA_VERSION,
                "type": "error",
                "id": request_id,
                "error": {
                    "code": "E_VALIDATION",
                    "message": "agent request must be valid JSON",
                    "details": {"type": type(error).__name__},
                    "retryable": False,
                },
                "meta": {"duration_ms": max(0, int((time.perf_counter() - started) * 1000))},
            }
        except Exception as error:  # pragma: no cover - stream safety boundary
            response = {
                "ok": False,
                "schema_version": SCHEMA_VERSION,
                "type": "error",
                "id": request_id,
                "error": {
                    "code": "E_UNKNOWN",
                    "message": "unexpected agent request failure",
                    "details": {"type": type(error).__name__},
                    "retryable": False,
                },
                "meta": {"duration_ms": max(0, int((time.perf_counter() - started) * 1000))},
            }
        sys.stdout.write(
            json.dumps(redact(response), ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        sys.stdout.flush()
    summary = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "type": "summary",
        "id": None,
        "data": {"count": count},
        "meta": {"duration_ms": 0},
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


def dispatch(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    validate_reference_options(positionals, options)
    if not positionals:
        raise CLIError("E_USAGE", "a command is required; use --help to list commands")
    if options.get("dangerous"):
        raise CLIError(
            "E_USAGE", "this phase has no dangerous command; NativeBackend is unavailable"
        )
    command = tuple(positionals[:2])
    if positionals[0] in {
        "project",
        "design",
        "change",
        "schematic",
        "pcb",
        "constraints",
        "analysis",
        "manufacturing",
        "library",
        "agent",
        "exchange",
    }:
        maximum = 3
    elif positionals[0] in {"system", "review", "bom", "session"}:
        maximum = 2
    else:
        maximum = 1
    if len(positionals) > maximum:
        raise CLIError(
            "E_USAGE", "too many positional arguments", {"arguments": positionals[maximum:]}
        )
    if command == ("context",):
        return _context(options)
    if command == ("doctor",) or command == ("system", "doctor"):
        return _doctor(options)
    if command == ("reference",):
        return reference(
            command=options.get("command"),
            domain=options.get("domain"),
            schema=options.get("schema"),
        )
    if command == ("changelog",):
        return _changelog(options.get("since"))
    if command == ("version",) or command == ("system", "version"):
        return {"version": __version__}
    if command in {("capabilities",), ("system", "capabilities")}:
        return CapabilityRegistry().summary()
    if command in {("license",), ("system", "license")}:
        data = NativeBackend().status()
        data["_untrusted"] = ["reason"]
        return data

    if positionals[0] == "agent":
        verb = positionals[1] if len(positionals) > 1 else ""
        backend = _backend(options)
        project, _ = backend.load(_project_path(positionals, options))
        if verb == "snapshot":
            return backend.snapshot(project)
        if verb == "query":
            return _list_data(
                project, _agent_query(project, options.get("query")), {**options, "query": None}
            )
        if verb == "review":
            return run_review(project, options.get("rules"))
        if verb == "capabilities":
            return CapabilityRegistry().summary()
        if verb == "serve":
            raise CLIError("E_USAGE", "agent serve is a streaming command")
        raise CLIError("E_USAGE", f"unknown agent command: {verb}")

    if positionals[0] == "session":
        verb = positionals[1] if len(positionals) > 1 else "status"
        backend_name = str(options.get("backend", "mock"))
        if backend_name != "mock":
            # a malformed --kind is a usage error whether or not an adapter is installed,
            # so it is reported before the backend is asked for
            domain = _session_domain(options) if verb != "status" else ""
            native = NativeBackend()
            native.require_implemented()
            if verb == "status":
                health = native.invoke("health", {})
                state = session_status(backend_name)
                if health.get("application_running"):
                    state["state"] = "running"
                    state["xpedition_process"] = True
                    state["reason"] = None
                state["health"] = health
                state["_untrusted"] = ["health", "session_id", "pid"]
                return state
            if verb == "start":
                clear_native()
                params: dict[str, Any] = {"visible": True, "domain": domain}
                if options.get("project"):
                    params["project"] = str(options["project"])
                try:
                    result = native.invoke("start", params, timeout_seconds=180.0)
                except CLIError as error:
                    record_native_failure({"code": error.code, "details": error.details or {}})
                    raise
                record_native_start(result)
                return result
            if verb == "attach":
                result = native.invoke("attach", {"domain": domain})
                record_native_attach(result)
                return result
            if verb == "open":
                if not options.get("project"):
                    raise CLIError("E_USAGE", "session open requires --project PATH")
                # Layout takes about a minute to start and may stop on a design-status
                # or recovery question that the adapter answers meanwhile.
                result = native.invoke(
                    "open",
                    {"project": str(options["project"]), "domain": domain, "start": True},
                    timeout_seconds=420.0,
                )
                record_native_attach({"domain": domain})
                return result
            if verb == "stop":
                # Quitting the application discards anything the user has not saved,
                # so this goes through the same preview/confirm gate as a design write.
                scope = {"operation": "session_stop", "domain": domain}
                preview = {
                    "changes": [{"action": "quit_xpedition", "domain": domain}],
                    "risk": {
                        "tier": "T1",
                        "blast_radius": (
                            "the running Xpedition application; unsaved design work is lost"
                        ),
                    },
                    "_untrusted": ["risk.blast_radius"],
                }
                if options.get("dry_run"):
                    token, expires_at = issue(scope)
                    return {
                        "preview": preview,
                        "confirm_token": token,
                        "expires_at": expires_at,
                        "_untrusted": ["preview.risk.blast_radius"],
                    }
                if options.get("confirm") is None:
                    raise CLIError(
                        "E_CONFIRMATION_REQUIRED",
                        "session stop requires --dry-run, then --confirm <confirm_token>",
                    )
                consume(str(options["confirm"]), scope)
                result = native.invoke("close", {"domain": domain})
                clear_native()
                return result
        if verb == "status":
            return session_status(backend_name)
        if verb == "logs":
            return session_logs(options.get("limit"), int(options.get("offset") or 0))
        if verb in {"start", "attach", "open", "stop"}:
            raise CLIError(
                "E_BACKEND_UNAVAILABLE",
                "session start/attach/open/stop require a verified NativeBackend adapter",
                {"hint": "pass --backend native_xpedition once the COM adapter is ready"},
            )
        raise CLIError("E_USAGE", f"unknown session command: {verb}")

    if positionals[0] == "exchange":
        verb = positionals[1] if len(positionals) > 1 else "inspect"
        if verb not in {"inspect", "import"}:
            raise CLIError("E_USAGE", f"unknown exchange command: {verb}")
        source_input = _input_path(positionals, options)
        if not source_input:
            raise CLIError("E_USAGE", "exchange command requires --input PATH")
        exchange = ExchangeBackend()
        imported, source, format_name = exchange.parse(source_input)
        if verb == "inspect":
            return {
                "format": format_name,
                "source": str(source),
                "project": imported,
                "record_count": len(imported["components"]),
                "_untrusted": ["format", "source", "project"],
            }
        target_input = options.get("project")
        if not target_input:
            raise CLIError("E_CONFIG", "exchange import requires --project TARGET")
        target = Path(str(target_input)).expanduser().resolve()
        current, _ = MockBackend().load(str(target))
        preview = {
            "source": str(source),
            "format": format_name,
            "target": str(target),
            "current_revision": current["revision"],
            "import_revision": imported["revision"],
            "component_count": len(imported["components"]),
            "changes": _project_diff(imported, current),
            "risk": {"tier": "T1", "blast_radius": "the selected local project file"},
            "_untrusted": ["source", "format", "target", "changes"],
        }
        if options.get("dry_run") and options.get("confirm"):
            raise CLIError("E_USAGE", "use either --dry-run or --confirm, not both")
        if options.get("dry_run"):
            token, expires_at = issue(_exchange_scope(source, target, current, imported))
            return {
                "preview": preview,
                "confirm_token": token,
                "expires_at": expires_at,
                "_untrusted": ["preview.source", "preview.target", "preview.changes"],
            }
        if options.get("confirm") is None:
            raise CLIError(
                "E_CONFIRMATION_REQUIRED",
                "exchange import requires --dry-run, then --confirm <confirm_token>",
            )
        consume(str(options["confirm"]), _exchange_scope(source, target, current, imported))
        saved, backup_path = persist_changes(imported, target, True)
        verification = verify_project(saved)
        if not verification["valid"]:
            raise CLIError(
                "E_PROJECT_INVALID",
                "exchange import verification failed",
                {"verification": verification},
            )
        append_history(
            target,
            {
                "operation": "exchange_import",
                "revision": saved["revision"],
                "backup_path": backup_path,
            },
        )
        return {
            "project": saved["project"],
            "path": str(target),
            "revision": saved["revision"],
            "format": format_name,
            "source": str(source),
            "backup_path": backup_path,
            "verification": verification,
            "_untrusted": ["project", "path", "format", "source"],
        }

    if command == ("change", "history"):
        project_path = _project_path(positionals, options)
        if not project_path:
            raise CLIError("E_CONFIG", "change history requires --project")
        path = Path(project_path).expanduser().resolve()
        entries = load_history(path)
        offset = min(len(entries), int(options.get("offset") or 0))
        limit = options.get("limit")
        end = len(entries) if limit is None else min(len(entries), offset + int(limit))
        rows = entries[offset:end]
        return {
            "project": path.stem,
            "path": str(path),
            "items": rows,
            "count": len(rows),
            "offset": offset,
            "next_offset": end if end < len(entries) else None,
            "has_more": end < len(entries),
            "_untrusted": ["project", "path", "items"],
        }

    if command == ("change", "rollback"):
        if options.get("dry_run") and options.get("confirm"):
            raise CLIError("E_USAGE", "use either --dry-run or --confirm, not both")
        project_path = _project_path(positionals, options)
        if not project_path:
            raise CLIError("E_CONFIG", "change rollback requires --project")
        backend = _backend(options)
        project, path = backend.load(project_path)
        if path is None:
            raise CLIError("E_CONFIG", "change rollback requires --project")
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            raise CLIError(
                "E_NOT_FOUND", "no project backup is available for rollback", {"path": str(backup)}
            )
        backup_project, _ = backend.load(str(backup))
        preview = {
            "project": project["project"],
            "path": str(path),
            "from_revision": project["revision"],
            "to_revision": backup_project["revision"],
            "backup_path": str(backup),
            "risk": {"tier": "T1", "blast_radius": "the selected local project file"},
            "_untrusted": ["project", "path", "backup_path"],
        }
        if options.get("dry_run"):
            token, expires_at = issue(_rollback_scope(path, project, backup))
            return {
                "preview": preview,
                "confirm_token": token,
                "expires_at": expires_at,
                "_untrusted": ["preview.project", "preview.path", "preview.backup_path"],
            }
        if options.get("confirm") is None:
            raise CLIError(
                "E_CONFIRMATION_REQUIRED",
                "change rollback requires --dry-run, then --confirm <confirm_token>",
            )
        consume(str(options["confirm"]), _rollback_scope(path, project, backup))
        restored = restore_backup(path)
        verification = verify_project(restored)
        if not verification["valid"]:
            raise CLIError(
                "E_PROJECT_INVALID", "rollback verification failed", {"verification": verification}
            )
        append_history(
            path,
            {"operation": "rollback", "revision": restored["revision"], "backup_path": str(backup)},
        )
        return {
            "project": restored["project"],
            "path": str(path),
            "revision": restored["revision"],
            "restored_from": str(backup),
            "verification": verification,
            "_untrusted": ["project", "path", "restored_from"],
        }

    if command == ("project", "init"):
        if options.get("dry_run") and options.get("confirm"):
            raise CLIError("E_USAGE", "use either --dry-run or --confirm, not both")
        project_path = _project_path(positionals, options)
        if not project_path:
            raise CLIError("E_CONFIG", "project init requires --project PATH")
        path = Path(project_path).expanduser().resolve()
        if path.exists():
            raise CLIError("E_CONFLICT", "project file already exists", {"path": str(path)})
        if options.get("template"):
            return _project_init_native(path, str(options["template"]), options)
        project_name = str(options.get("name") or path.stem or "mock-project")
        preview = {
            "path": str(path),
            "project": project_name,
            "revision": "R00",
            "changes": [{"action": "create_project", "path": str(path)}],
            "risk": {"tier": "T1", "blast_radius": "the new local project file"},
            "_untrusted": ["path", "project", "changes", "risk.blast_radius"],
        }
        if options.get("dry_run"):
            token, expires_at = issue(_init_scope(path, project_name))
            return {
                "preview": preview,
                "confirm_token": token,
                "expires_at": expires_at,
                "_untrusted": ["preview.path", "preview.project", "preview.changes"],
            }
        if options.get("confirm") is None:
            raise CLIError(
                "E_CONFIRMATION_REQUIRED",
                "project init requires --dry-run, then --confirm <confirm_token>",
            )
        if path.exists():
            raise CLIError(
                "E_CONFLICT", "project file was created after dry-run", {"path": str(path)}
            )
        consume(str(options["confirm"]), _init_scope(path, project_name))
        project, _ = MockBackend().load(None)
        project["project"] = project_name
        saved, _ = persist_changes(project, path, False)
        verification = verify_project(saved)
        if not verification["valid"]:
            raise CLIError(
                "E_PROJECT_INVALID",
                "new project verification failed",
                {"verification": verification},
            )
        append_history(path, {"operation": "project_init", "revision": saved["revision"]})
        return {
            "project": saved["project"],
            "path": str(path),
            "revision": saved["revision"],
            "created": True,
            "verification": verification,
            "_untrusted": ["project", "path"],
        }

    if command in {
        ("project", "info"),
        ("project", "snapshot"),
        ("project", "tree"),
        ("project", "diff"),
        ("design", "snapshot"),
    }:
        backend = _backend(options)
        project_path = _project_path(positionals, options)
        if command == ("design", "snapshot") and backend.name == "native_xpedition":
            project, path = backend.load(project_path, domain="schematic")
        else:
            project, path = backend.load(project_path)
        if command == ("project", "diff"):
            if path is None:
                raise CLIError("E_CONFIG", "project diff requires --project")
            backup = path.with_suffix(path.suffix + ".bak")
            if not backup.exists():
                raise CLIError(
                    "E_NOT_FOUND", "no project backup is available for diff", {"path": str(backup)}
                )
            backup_project, _ = backend.load(str(backup))
            return {
                "project": project["project"],
                "path": str(path),
                "current_revision": project["revision"],
                "backup_revision": backup_project["revision"],
                "has_backup": True,
                **_project_diff(project, backup_project),
                "_untrusted": ["project", "path", "added", "removed", "changed"],
            }
        if command == ("project", "info"):
            return {
                "project": project["project"],
                "path": str(path) if path else None,
                "exists": bool(path and path.exists()),
                "revision": project["revision"],
                "component_count": len(project["components"]),
                "net_count": len(project["nets"]),
                "connection_count": len(project["connections"]),
                "backend": backend.name,
                "_untrusted": ["project", "path"],
            }
        if command == ("project", "tree"):
            return {
                "project": project["project"],
                "revision": project["revision"],
                "sheets": project["sheets"],
                "components": [
                    {
                        "refdes": item.get("refdes"),
                        "part_number": item.get("internal_part_no"),
                    }
                    for item in project["components"]
                ],
                "_untrusted": ["project", "sheets", "components"],
            }
        return backend.snapshot(project)

    if positionals[0] == "schematic" and len(positionals) > 1 and positionals[1] == "export":
        return _schematic_export(positionals, options)

    if positionals[0] == "schematic" and len(positionals) > 1 and positionals[1] == "draw":
        return _schematic_draw(positionals, options)

    if positionals[0] == "schematic" and len(positionals) > 1 and positionals[1] == "show":
        return _schematic_show(positionals, options)

    if positionals[0] == "library" and len(positionals) > 1 and positionals[1] == "build":
        return _library_build(positionals, options)

    if positionals[0] == "schematic" and not (len(positionals) > 1 and positionals[1] == "apply"):
        backend = _backend(options)
        project_path = _project_path(positionals, options)
        project, _ = (
            backend.load(project_path, domain="schematic")
            if backend.name == "native_xpedition"
            else backend.load(project_path)
        )
        verb = positionals[1] if len(positionals) > 1 else ""
        if verb == "sheets":
            return _list_data(project, project["sheets"], options)
        if verb == "components":
            return _list_data(project, project["components"], options)
        if verb == "pins":
            return _list_data(project, _schematic_pins(project), options)
        if verb == "nets":
            return _list_data(project, project["nets"], options)
        if verb == "connectivity":
            return _list_data(project, project["connections"], options)
        if verb == "unconnected":
            return _list_data(project, _schematic_unconnected(project), options)
        if verb == "power":
            return _list_data(project, _schematic_power(project), options)
        if verb == "interfaces":
            return _list_data(project, project["interfaces"], options)
        if verb == "query":
            if not options.get("query"):
                raise CLIError("E_USAGE", "schematic query requires --query")
            all_items = (
                {"kind": kind, "value": item}
                for kind, key in (
                    ("component", "components"),
                    ("net", "nets"),
                    ("connection", "connections"),
                )
                for item in project[key]
            )
            return _list_data(project, all_items, options)
        raise CLIError("E_USAGE", f"unknown schematic command: {verb}")

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "create":
        return _pcb_create(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "annotate":
        return _pcb_annotate(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "arrange":
        return _pcb_arrange(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "render":
        return _pcb_render(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "geometry":
        return _pcb_geometry(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] in ("trace", "via"):
        return _pcb_trace(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "stitch":
        return _pcb_stitch(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "unroute":
        return _pcb_unroute(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "move":
        return _pcb_move(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "labels":
        return _pcb_labels(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "show":
        return _pcb_show(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "outline":
        return _pcb_outline(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "rules":
        return _pcb_rules(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "export":
        return _pcb_export(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "holes":
        return _pcb_holes(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "pour":
        return _pcb_pour(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "route":
        return _pcb_route(positionals, options)

    if positionals[0] == "pcb" and len(positionals) > 1 and positionals[1] == "drc":
        return _pcb_drc(positionals, options)

    if positionals[0] == "pcb":
        backend = _backend(options)
        project_path = _project_path(positionals, options)
        project, _ = (
            backend.load(project_path, domain="pcb")
            if backend.name == "native_xpedition"
            else backend.load(project_path)
        )
        pcb = _pcb_data(project)
        verb = positionals[1] if len(positionals) > 1 else ""
        if verb == "info":
            return {
                "project": project["project"],
                "revision": project["revision"],
                "component_count": len(pcb["components"]),
                "footprint_count": len(pcb["footprints"]),
                "net_count": len(pcb["nets"]),
                "layer_count": len(pcb["layers"]),
                "track_count": len(pcb["tracks"]),
                "via_count": len(pcb["vias"]),
                "zone_count": len(pcb["zones"]),
                "keepout_count": len(pcb["keepouts"]),
                "stackup_count": len(pcb["stackup"]),
                "_untrusted": ["project"],
            }
        pcb_keys = {
            "components",
            "footprints",
            "nets",
            "layers",
            "stackup",
            "tracks",
            "vias",
            "zones",
            "keepouts",
        }
        if verb in pcb_keys:
            return _list_data(project, pcb[verb], options)
        if verb == "query":
            if not options.get("query"):
                raise CLIError("E_USAGE", "pcb query requires --query")
            all_items = (
                {"kind": kind, "value": item} for kind in sorted(pcb_keys) for item in pcb[kind]
            )
            return _list_data(project, all_items, options)
        raise CLIError("E_USAGE", f"unknown pcb command: {verb}")

    if positionals[0] == "constraints":
        backend = _backend(options)
        project, _ = backend.load(_project_path(positionals, options))
        verb = positionals[1] if len(positionals) > 1 else ""
        if verb in {"list", "query", "export"}:
            if verb == "query" and not options.get("query"):
                raise CLIError("E_USAGE", "constraints query requires --query")
            return _list_data(project, project["constraints"], options)
        if verb == "validate":
            issues = []
            for index, item in enumerate(project["constraints"]):
                if not isinstance(item, dict) or not item.get("name"):
                    issues.append({"index": index, "message": "constraint requires a name"})
            return {
                "project": project["project"],
                "revision": project["revision"],
                "valid": not issues,
                "constraints": project["constraints"],
                "issues": issues,
                "_untrusted": ["project", "constraints", "issues[].message"],
            }
        raise CLIError("E_USAGE", f"unknown constraints command: {verb}")

    if positionals[0] == "analysis":
        backend = _backend(options)
        project, _ = backend.load(_project_path(positionals, options))
        verb = positionals[1] if len(positionals) > 1 else "results"
        if verb == "run":
            kind = str(options.get("kind") or "all")
            items = _run_mock_analysis(project, kind)
            result = _list_data(project, items, options, ["project", "items", "summary"])
            result["engine"] = "mock"
            result["kind"] = kind
            result["summary"] = {
                "total": len(items),
                "by_severity": {"error": 0, "warning": 0, "info": 0},
            }
            for item in items:
                severity = item.get("severity")
                if severity in result["summary"]["by_severity"]:
                    result["summary"]["by_severity"][severity] += 1
            return result
        if verb in {"results", "erc", "drc", "dfm"}:
            if verb == "results":
                items = _flatten_analysis(project)
            else:
                items = [{"kind": verb, "result": item} for item in project["analysis"][verb]]
            result = _list_data(project, items, options)
            result["summary"] = {
                "by_kind": {
                    kind: sum(1 for item in items if item.get("kind") == kind)
                    for kind in ("erc", "drc", "dfm", "results")
                }
            }
            result["_untrusted"] = ["project", "items", "summary"]
            return result
        raise CLIError("E_USAGE", f"unknown analysis command: {verb}")

    if positionals[0] == "manufacturing":
        backend = _backend(options)
        project, _ = backend.load(_project_path(positionals, options))
        verb = positionals[1] if len(positionals) > 1 else "verify"
        if verb == "bom":
            rows = bom_rows(project)
            return {
                "project": project["project"],
                "revision": project["revision"],
                "items": rows,
                "count": len(rows),
                "offset": 0,
                "next_offset": None,
                "has_more": False,
                "_untrusted": ["project", "items"],
            }
        if verb == "artifacts":
            return _list_data(project, project["manufacturing"]["artifacts"], options)
        if verb == "verify":
            artifacts = project["manufacturing"]["artifacts"]
            issues = []
            for index, artifact in enumerate(artifacts):
                if (
                    not isinstance(artifact, dict)
                    or not artifact.get("kind")
                    or not artifact.get("path")
                ):
                    issues.append({"index": index, "message": "artifact requires kind and path"})
            return {
                "project": project["project"],
                "revision": project["revision"],
                "valid": not issues,
                "artifacts": artifacts,
                "issues": issues,
                "_untrusted": ["project", "artifacts", "issues[].message"],
            }
        raise CLIError("E_USAGE", f"unknown manufacturing command: {verb}")

    if positionals[0] == "library":
        backend = _backend(options)
        project, _ = backend.load(_project_path(positionals, options))
        verb = positionals[1] if len(positionals) > 1 else "search"
        keys = {"parts", "symbols", "footprints", "padstacks", "models"}
        if verb == "search":
            query = options.get("query")
            if not query:
                raise CLIError("E_USAGE", "library search requires --query")
            items = (
                {"kind": kind, "value": item}
                for kind in sorted(keys)
                for item in project["library"][kind]
            )
            result = _list_data(project, items, options, ["project", "items", "query"])
            result["query"] = query
            return result
        if verb in keys:
            return _list_data(project, project["library"][verb], options)
        if verb == "validate":
            required_key = {
                "parts": "part_number",
                "symbols": "name",
                "footprints": "name",
                "padstacks": "name",
                "models": "name",
            }
            issues = []
            record_count = 0
            for kind, key in required_key.items():
                for index, item in enumerate(project["library"][kind]):
                    record_count += 1
                    if not isinstance(item, dict) or not item.get(key):
                        issues.append(
                            {
                                "kind": kind,
                                "index": index,
                                "message": f"{kind} record requires {key}",
                            }
                        )
            return {
                "project": project["project"],
                "revision": project["revision"],
                "valid": not issues,
                "record_count": record_count,
                "issues": issues,
                "_untrusted": ["project", "issues"],
            }
        raise CLIError("E_USAGE", f"unknown library command: {verb}")

    if command == ("change", "validate"):
        changeset, _ = load_changeset(_changeset_path(positionals, options))
        return validate_changeset(changeset)

    if command in {
        ("change", "preview"),
        ("change", "apply"),
        ("schematic", "apply"),
    }:
        is_apply = command in {("change", "apply"), ("schematic", "apply")}
        if options.get("dry_run") and options.get("confirm"):
            raise CLIError("E_USAGE", "use either --dry-run or --confirm, not both")
        if not is_apply and options.get("confirm"):
            raise CLIError("E_USAGE", "change preview is read-only; do not pass --confirm")
        backend = _backend(options)
        changeset, _ = load_changeset(_changeset_path(positionals, options))
        project_path = str(options["project"]) if options.get("project") else None
        changeset_domain = "schematic" if command == ("schematic", "apply") else None
        project, path = _project_for_changeset(backend, project_path, changeset, changeset_domain)
        projected, changes = apply_operations(project, changeset["operations"])
        preview = preview_changes(project, changeset)
        scope_command = "schematic apply" if command == ("schematic", "apply") else "change apply"
        if not is_apply or options.get("dry_run"):
            token, expires_at = issue(_scope(path, changeset, project, scope_command, backend.name))
            return {
                "preview": preview,
                "confirm_token": token,
                "expires_at": expires_at,
                "_untrusted": ["preview.project", "preview.changes", "preview.risk.blast_radius"],
            }
        if options.get("confirm") is None:
            raise CLIError(
                "E_CONFIRMATION_REQUIRED",
                "write apply requires --dry-run, then --confirm <confirm_token>",
            )
        consume(
            str(options["confirm"]),
            _scope(path, changeset, project, scope_command, backend.name),
        )
        if backend.name == "native_xpedition":
            if path is None:
                raise CLIError(
                    "E_CONFIG",
                    "native apply requires --project so the target document is unambiguous",
                )
            native_result = backend.invoke(
                "apply_changeset",
                {
                    "project": str(path),
                    "domain": changeset_domain or "pcb",
                    "operations": changeset["operations"],
                    "save": True,
                },
            )
            reread, _ = backend.load(str(path), domain=changeset_domain or "pcb")
            return {
                "project": reread["project"],
                "path": str(path),
                "revision": reread["revision"],
                "applied": native_result.get("applied", []),
                "backend": backend.name,
                "verification": {
                    "valid": True,
                    "saved": True,
                    "reread": True,
                    "component_count": len(reread["components"]),
                    "net_count": len(reread["nets"]),
                },
                "_untrusted": ["project", "path", "applied"],
            }
        saved, backup_path = persist_changes(projected, path, bool(options.get("backup")))
        verification = verify_project(saved)
        if not verification["valid"]:
            raise CLIError(
                "E_PROJECT_INVALID",
                "post-apply project verification failed",
                {"verification": verification},
            )
        append_history(
            Path(path),
            {
                "operation": "apply",
                "revision": saved["revision"],
                "backup_path": backup_path,
                "actions": [item.get("action") for item in changes],
            },
        )
        return {
            "project": saved["project"],
            "path": str(path),
            "revision": saved["revision"],
            "applied": changes,
            "backup_path": backup_path,
            "verification": verification,
            "_untrusted": ["project", "path", "applied"],
        }

    if positionals[0] == "review":
        backend = _backend(options)
        project_argument = _project_path(positionals, options)
        project, _ = backend.load(project_argument)
        verb = positionals[1] if len(positionals) > 1 else "run"
        if verb in {"run", "findings", "report"}:
            # On a live design, Designer's own verification (its ERC and graphical
            # checks) runs too and its findings are merged under `xpedition/...`.
            extra: list[dict[str, Any]] | None = None
            if backend.name == "native_xpedition" and project_argument:
                tool = backend.invoke(
                    "verify",
                    {"project": str(Path(str(project_argument)).expanduser().resolve())},
                    timeout_seconds=300.0,
                )
                extra = list(tool.get("findings") or [])
            return _paged_review(project, options.get("rules"), options, extra)
        raise CLIError("E_USAGE", f"unknown review command: {verb}")

    if positionals[0] == "bom":
        backend = _backend(options)
        project, _ = backend.load(_project_path(positionals, options))
        verb = positionals[1] if len(positionals) > 1 else ""
        rows = bom_rows(project)
        if verb in {"export", "normalize", "variants", "missing", "duplicates"}:
            if verb == "variants":
                rows = [row for row in rows if row.get("variant")]
            elif verb == "missing":
                rows = [
                    row for row in rows if not row.get("internal_part_no") or not row.get("mpn")
                ]
            elif verb == "duplicates":
                counts: dict[str, int] = {}
                for row in rows:
                    part = str(row.get("internal_part_no") or row.get("mpn") or "UNSPECIFIED")
                    counts[part] = counts.get(part, 0) + 1
                rows = [
                    row
                    for row in rows
                    if counts.get(
                        str(row.get("internal_part_no") or row.get("mpn") or "UNSPECIFIED"), 0
                    )
                    > 1
                ]
            return _list_data(project, rows, options)
        if verb == "group":
            groups = _bom_groups(rows)
            return {
                "project": project["project"],
                "revision": project["revision"],
                "groups": groups,
                "count": len(groups),
                "_untrusted": ["project", "groups"],
            }
        if verb == "validate":
            valid, issues = _bom_validation(rows)
            return {
                "project": project["project"],
                "revision": project["revision"],
                "valid": valid,
                "issues": issues,
                "count": len(rows),
                "_untrusted": ["project", "issues"],
            }
        if verb == "compare":
            other_path = options.get("other_project")
            if not other_path:
                raise CLIError("E_USAGE", "bom compare requires --other-project")
            other, other_file = backend.load(str(other_path))
            diff = _bom_compare(rows, bom_rows(other))
            return {
                "base_project": project["project"],
                "base_revision": project["revision"],
                "other_project": other["project"],
                "other_revision": other["revision"],
                "other_path": str(other_file) if other_file else None,
                **diff,
                "_untrusted": [
                    "base_project",
                    "other_project",
                    "other_path",
                    "added",
                    "removed",
                    "changed",
                ],
            }
        raise CLIError("E_USAGE", f"unknown bom command: {verb}")
    raise CLIError("E_USAGE", f"unknown command: {' '.join(positionals)}", {"command": positionals})


HELP = """xpedition-cli — agent-native Xpedition design control layer

Usage:
  xpedition-cli <command> [options]

Commands:
  context                         show runtime and credential context
  doctor                         check environment and release readiness
  reference                      show the live machine contract
  changelog                      show release changes
  version                        show the CLI version
  system capabilities|license    inspect backend capabilities or native license status
  session status|logs            inspect local session state and logs
  exchange inspect|import        inspect or import JSON/CSV/BOM exchange files
  project init|info|tree|snapshot|diff
                                  create or inspect a normalized project
  design snapshot                 alias for project snapshot
  schematic sheets|components|pins|nets|connectivity|power|interfaces|unconnected|query|apply
                                  inspect normalized schematic data
  pcb info|components|footprints|nets|layers|stackup|tracks|vias|zones|keepouts|query
                                  inspect normalized PCB data
  constraints list|query|validate|export
                                  inspect and validate constraints
  analysis run|results|erc|drc|dfm
                                  inspect or run deterministic analysis
  manufacturing artifacts|verify|bom
                                  inspect manufacturing outputs
  library search|parts|symbols|footprints|padstacks|models|validate
                                  inspect normalized library records
  agent snapshot|query|review|capabilities|serve
                                  expose read-only Agent integration (serve uses NDJSON stdio)
  change validate|preview|apply|history|rollback
                                  validate, apply, inspect or rollback a ChangeSet
  review run|findings|report      run or read deterministic design checks
  bom export|normalize|group|variants|missing|duplicates|validate|compare
                                  inspect, validate or compare BOM data

Common options:
  --format json|text|raw  --compact  --fields a,b  --backend mock
  --project PATH          --changeset PATH  --dry-run  --confirm TOKEN
  --backup                --rules PATH      --limit N  --since VERSION

Reference selectors (choose one):
  reference --command "pcb trace" | --domain pcb | --schema context

Native Xpedition automation is intentionally unavailable until a licensed,
audited adapter is configured. Use MockBackend for offline development.
"""


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, OSError):
            pass
    started = time.perf_counter()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    command_text = "xpedition-cli"
    if "--version" in raw_argv and raw_argv.count("--version") == 1:
        sys.stdout.write(f"xpedition-cli {__version__}\n")
        return 0
    options: dict[str, Any] = {}
    try:
        positionals, options = parse_argv(raw_argv)
        command_text = " ".join(positionals) or "xpedition-cli"
        if options.get("help"):
            sys.stdout.write(HELP)
            return 0
        if positionals[:2] == ["agent", "serve"]:
            try:
                exit_code = _agent_serve(options)
            except CLIError as error:
                payload = failure(error, started)
                emit(payload, options.get("format", "json"), bool(options.get("compact")))
                exit_code = error.exit_code
            record(
                command_text,
                exit_code,
                max(0, int((time.perf_counter() - started) * 1000)),
                raw_argv,
            )
            return exit_code
        data = dispatch(positionals, options)
        payload = success(data, started, options.get("fields"))
        emit(payload, options.get("format", "json"), bool(options.get("compact")))
        exit_code = 0
    except CLIError as error:
        payload = failure(error, started)
        emit(
            payload,
            options.get("format", "json") if options else "json",
            bool(options.get("compact")) if options else False,
        )
        exit_code = error.exit_code
    except Exception as error:  # pragma: no cover - last-resort process boundary
        cli_error = CLIError(
            "E_UNKNOWN", "unexpected internal error", {"type": type(error).__name__}
        )
        payload = failure(cli_error, started)
        emit(
            payload,
            options.get("format", "json") if options else "json",
            bool(options.get("compact")) if options else False,
        )
        exit_code = cli_error.exit_code
    record(command_text, exit_code, max(0, int((time.perf_counter() - started) * 1000)), raw_argv)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
