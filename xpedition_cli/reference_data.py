from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import __version__, placement_contract
from .api_inventory_contract import OUTPUT_SCHEMA as API_OUTPUT_SCHEMA
from .api_inventory_contract import command as api_command
from .contract_gen import CODES
from .pin_assignment_contract import OUTPUT_SCHEMA as PIN_OUTPUT_SCHEMA
from .pin_assignment_contract import commands as pin_commands
from .reference_query import select_reference, selector_params

SCHEMAS: dict[str, dict[str, Any]] = {
    "context": {
        "shape": "object",
        "fields": [
            "version",
            "backend",
            "config",
            "project",
            "credentials",
            "notices",
            "_untrusted",
        ],
        "untrusted_fields": ["config.directory", "project"],
    },
    "doctor": {
        "shape": "object",
        "fields": ["checks", "version", "backend", "_untrusted"],
        "untrusted_fields": ["checks[].message", "checks[].details"],
    },
    "reference": {
        "shape": "object",
        "fields": [
            "tool",
            "version",
            "risk_tier",
            "release_readiness",
            "global_flags",
            "commands",
            "schemas",
            "exit_codes",
            "error_codes",
            "selection",
        ],
        "optional_fields": ["selection"],
        "untrusted_fields": [],
    },
    "changelog": {
        "shape": "object",
        "fields": ["current_version", "since", "entries"],
        "untrusted_fields": ["entries[].changes"],
    },
    "version": {"shape": "object", "fields": ["version"], "untrusted_fields": []},
    "capabilities": {
        "shape": "object",
        "fields": [
            "platform",
            "xpedition_native_command_configured",
            "capabilities",
            "planned_command_domains",
            "_untrusted",
        ],
        "untrusted_fields": ["platform", "capabilities"],
    },
    "license": {
        "shape": "object",
        "fields": [
            "backend",
            "available",
            "licensed",
            "automation_command_configured",
            "reason",
            "_untrusted",
        ],
        "untrusted_fields": ["reason"],
    },
    "project_info": {
        "shape": "object",
        "fields": [
            "project",
            "path",
            "exists",
            "revision",
            "component_count",
            "net_count",
            "connection_count",
            "backend",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "path"],
    },
    "project_snapshot": {
        "shape": "object",
        "fields": [
            "project",
            "revision",
            "sheets",
            "components",
            "nets",
            "connections",
            "interfaces",
            "bom",
            "constraints",
            "pcb",
            "library",
            "analysis",
            "manufacturing",
            "metadata",
            "_untrusted",
        ],
        "untrusted_fields": [
            "project",
            "sheets",
            "components",
            "nets",
            "connections",
            "interfaces",
            "bom",
            "constraints",
            "pcb",
            "library",
            "analysis",
            "manufacturing",
            "metadata",
        ],
    },
    "project_tree": {
        "shape": "object",
        "fields": ["project", "revision", "sheets", "components", "_untrusted"],
        "untrusted_fields": ["project", "sheets", "components"],
    },
    "project_diff": {
        "shape": "object",
        "fields": [
            "project",
            "path",
            "current_revision",
            "backup_revision",
            "has_backup",
            "added",
            "removed",
            "changed",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "path", "added", "removed", "changed"],
    },
    "project_init_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": [
            "preview.path",
            "preview.project",
            "preview.template",
            "preview.changes",
        ],
    },
    "project_init": {
        "shape": "object",
        "fields": [
            "project",
            "path",
            "revision",
            "created",
            "verification",
            "template",
            "files",
            "bytes",
            "rewritten",
            "template_closed",
            "opened",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "path", "template", "rewritten"],
    },
    "list_result": {
        "shape": "object",
        "fields": [
            "project",
            "revision",
            "items",
            "count",
            "offset",
            "next_offset",
            "has_more",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "items"],
    },
    "pcb_info": {
        "shape": "object",
        "fields": [
            "project",
            "revision",
            "component_count",
            "footprint_count",
            "net_count",
            "layer_count",
            "track_count",
            "via_count",
            "zone_count",
            "keepout_count",
            "stackup_count",
            "_untrusted",
        ],
        "untrusted_fields": ["project"],
    },
    "constraints_validation": {
        "shape": "object",
        "fields": ["project", "revision", "valid", "constraints", "issues", "_untrusted"],
        "untrusted_fields": ["project", "constraints", "issues[].message"],
    },
    "analysis_results": {
        "shape": "object",
        "fields": [
            "project",
            "revision",
            "items",
            "count",
            "offset",
            "next_offset",
            "has_more",
            "summary",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "items", "summary"],
    },
    "analysis_run": {
        "shape": "object",
        "fields": [
            "project",
            "revision",
            "engine",
            "kind",
            "items",
            "count",
            "offset",
            "next_offset",
            "has_more",
            "summary",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "items", "summary"],
    },
    "manufacturing_verify": {
        "shape": "object",
        "fields": ["project", "revision", "valid", "artifacts", "issues", "_untrusted"],
        "untrusted_fields": ["project", "artifacts", "issues[].message"],
    },
    "library_build_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "library_build": {
        "shape": "object",
        "fields": [
            "project",
            "library",
            "partition",
            "steps",
            "failed",
            "pdb_registered",
            "ok",
            "package",
            "summary",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "library", "steps", "package", "summary"],
    },
    "pcb_create_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_create": {
        "shape": "object",
        "fields": [
            "project",
            "design",
            "pcb",
            "template",
            "template_copied",
            "designs_reordered",
            "cells_registered",
            "copied_files",
            "replaced",
            "backup",
            "log",
            "ok",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "pcb", "log", "cells_registered", "replaced", "backup"],
    },
    "pcb_annotate_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_annotate": {
        "shape": "object",
        "fields": [
            "pcb",
            "outcome",
            "status_before",
            "status_after",
            "completed",
            "components_found",
            "nets_found",
            "pins_found",
            "board",
            "errors",
            "warnings",
            "prompts",
            "saved",
            "log",
            "routing",
            "routing_removed",
            "board_reopened",
            "attempts",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "errors", "warnings", "prompts", "log"],
    },
    "pcb_geometry": {
        "shape": "object",
        "fields": ["pcb", "layers", "counts", "path", "model", "prompts", "_untrusted"],
        "untrusted_fields": ["pcb", "prompts", "model"],
    },
    "pcb_trace_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_trace": {
        "shape": "object",
        "fields": [
            "pcb",
            "items",
            "nets",
            "opens_before",
            "warnings",
            "placed",
            "failed",
            "opens_after",
            "planes_regenerated",
            "routing",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "items", "warnings", "prompts"],
    },
    "pcb_unroute_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_unroute": {
        "shape": "object",
        "fields": [
            "pcb",
            "nets",
            "at",
            "layer",
            "to_delete",
            "deleted",
            "planes_regenerated",
            "routing",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "prompts"],
    },
    "pcb_move_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_move": {
        "shape": "object",
        "fields": [
            "pcb",
            "refdes",
            "before",
            "target",
            "routing",
            "after",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "prompts"],
    },
    "pcb_stitch": {
        "shape": "object",
        "fields": ["net", "width", "vias", "items", "without_room", "path", "_untrusted"],
        "untrusted_fields": ["items", "without_room"],
    },
    "pcb_labels_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_labels": {
        "shape": "object",
        "fields": [
            "pcb",
            "labels",
            "moved",
            "unplaced",
            "failures",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "labels", "prompts"],
    },
    "pcb_render": {
        "shape": "object",
        "fields": ["pcb", "side", "scale", "picture", "layers", "prompts", "_untrusted"],
        "untrusted_fields": ["pcb", "picture", "prompts"],
    },
    "pcb_show": {
        "shape": "object",
        "fields": [
            "pcb",
            "scheme",
            "scheme_applied",
            "scheme_written",
            "fitted",
            "schemes",
            "components",
            "prompts",
            "foreground",
            "window",
            "screenshot",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "schemes", "prompts"],
    },
    "pcb_arrange_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_arrange": {
        "shape": "object",
        "fields": [
            "pcb",
            "board",
            "plan",
            "labels",
            "clusters",
            "summary",
            "nets",
            "digest",
            "skipped_placed",
            "applied",
            "placed",
            "labels_replaced",
            "routing",
            "routing_removed",
            "saved",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": [
            "pcb",
            "plan",
            "labels",
            "clusters",
            "placed",
            "skipped_placed",
            "prompts",
        ],
    },
    "pcb_drc": {
        "shape": "object",
        "fields": [
            "pcb",
            "ran",
            "seconds",
            "count",
            "summary",
            "hazards",
            "online",
            "clean",
            "errors",
            "warnings",
            "passes",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "hazards", "online", "prompts"],
    },
    "pcb_route_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_route": {
        "shape": "object",
        "fields": [
            "pcb",
            "passes",
            "before",
            "unrouted_before",
            "runs",
            "planes_regenerated",
            "after",
            "unrouted",
            "complete",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "unrouted_before", "unrouted", "runs", "prompts"],
    },
    "pcb_outline_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_outline": {
        "shape": "object",
        "fields": [
            "pcb",
            "before",
            "requested",
            "after",
            "companions",
            "unit",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "prompts"],
    },
    "pcb_rules_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_rules": {
        "shape": "object",
        "fields": [
            "pcb",
            "board",
            "class",
            "nets",
            "widths",
            "classes",
            "missing_nets",
            "created",
            "assigned",
            "constraints_changed",
            "after",
            "synched",
            "layout",
            "seen_by_layout",
            "prompts",
            "applied",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "classes", "prompts"],
    },
    "pcb_export_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_export": {
        "shape": "object",
        "fields": [
            "pcb",
            "formats",
            "package",
            "setup_changes",
            "existing",
            "board",
            "size",
            "layer_count",
            "runs",
            "gerber",
            "drill",
            "odb",
            "centroid_rows",
            "bom_rows",
            "files",
            "checks",
            "rounds",
            "closed_and_reopened",
            "prompts",
            "applied",
            "_untrusted",
        ],
        "untrusted_fields": [
            "pcb",
            "package",
            "prompts",
            "runs",
            "files",
            "gerber",
            "drill",
            "odb",
        ],
    },
    "pcb_holes_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_holes": {
        "shape": "object",
        "fields": [
            "pcb",
            "board",
            "padstack",
            "diameter",
            "inset",
            "existing",
            "planned",
            "replace",
            "removed",
            "placed",
            "existing_after",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "existing", "prompts"],
    },
    "pcb_pour_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "pcb_pour": {
        "shape": "object",
        "fields": [
            "pcb",
            "net",
            "layer",
            "layers",
            "margin",
            "rectangle",
            "existing",
            "replace",
            "removed",
            "shapes",
            "generated",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": ["pcb", "existing", "prompts"],
    },
    "library_search": {
        "shape": "object",
        "fields": [
            "query",
            "project",
            "revision",
            "items",
            "count",
            "offset",
            "next_offset",
            "has_more",
            "_untrusted",
        ],
        "untrusted_fields": ["query", "items"],
    },
    "library_validation": {
        "shape": "object",
        "fields": ["project", "revision", "valid", "record_count", "issues", "_untrusted"],
        "untrusted_fields": ["project", "issues"],
    },
    "exchange_inspect": {
        "shape": "object",
        "fields": ["format", "source", "project", "record_count", "_untrusted"],
        "untrusted_fields": ["format", "source", "project"],
    },
    "exchange_import_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview.source", "preview.target", "preview.changes"],
    },
    "exchange_import": {
        "shape": "object",
        "fields": [
            "project",
            "path",
            "revision",
            "format",
            "source",
            "backup_path",
            "verification",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "path", "format", "source"],
    },
    "agent_stream": {
        "shape": "object",
        "fields": ["type", "id", "data", "error", "meta"],
        "untrusted_fields": ["data", "error.message", "error.details"],
    },
    "session_status": {
        "shape": "object",
        "fields": [
            "backend",
            "state",
            "session_id",
            "pid",
            "xpedition_process",
            "reason",
            "_untrusted",
        ],
        "untrusted_fields": ["session_id", "pid", "reason"],
    },
    "session_logs": {
        "shape": "object",
        "fields": ["items", "count", "offset", "next_offset", "has_more", "_untrusted"],
        "untrusted_fields": ["items"],
    },
    "session_lifecycle": {
        "shape": "object",
        "fields": [
            "started",
            "attached",
            "opened",
            "closed",
            "domain",
            "pid",
            "executable",
            "project",
            "automation_ready",
            "automation_progid",
        ],
        "untrusted_fields": ["executable", "project", "automation_progid"],
    },
    "session_stop_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview.risk.blast_radius"],
    },
    "schematic_draw": {
        "shape": "object",
        "fields": [
            "applied",
            "operations",
            "warnings",
            "netlist",
            "symbols_written",
            "project",
            "summary",
            "_untrusted",
        ],
        "untrusted_fields": ["warnings", "netlist", "symbols_written", "project", "summary"],
    },
    "schematic_draw_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "schematic_show": {
        "shape": "object",
        "fields": ["sheet", "sheet_size", "foreground", "window", "screenshot", "_untrusted"],
        "untrusted_fields": ["window", "screenshot"],
    },
    "schematic_export": {
        "shape": "object",
        "fields": [
            "exported",
            "format",
            "path",
            "size",
            "sheets",
            "exit_code",
            "messages",
            "_untrusted",
        ],
        "untrusted_fields": ["path", "sheets", "messages"],
    },
    "changeset_validation": {
        "shape": "object",
        "fields": [
            "valid",
            "project",
            "base_revision",
            "operation_count",
            "operations",
            "issues",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "operations", "issues[].message"],
    },
    "change_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview.project", "preview.changes", "preview.risk.blast_radius"],
    },
    "change_apply": {
        "shape": "object",
        "fields": [
            "project",
            "path",
            "revision",
            "applied",
            "backup_path",
            "verification",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "path", "applied"],
    },
    "change_history": {
        "shape": "object",
        "fields": [
            "project",
            "path",
            "items",
            "count",
            "offset",
            "next_offset",
            "has_more",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "path", "items"],
    },
    "change_rollback_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview.project", "preview.path", "preview.backup_path"],
    },
    "change_rollback": {
        "shape": "object",
        "fields": ["project", "path", "revision", "restored_from", "verification", "_untrusted"],
        "untrusted_fields": ["project", "path", "restored_from"],
    },
    "review_report": {
        "shape": "object",
        "fields": [
            "project",
            "revision",
            "findings",
            "offset",
            "next_offset",
            "has_more",
            "summary",
            "_untrusted",
        ],
        "untrusted_fields": [
            "project",
            "findings[].finding",
            "findings[].evidence",
            "findings[].suggestion",
        ],
    },
    "bom": {
        "shape": "object",
        "fields": [
            "project",
            "revision",
            "items",
            "count",
            "offset",
            "next_offset",
            "has_more",
            "_untrusted",
        ],
        "untrusted_fields": ["project", "items"],
    },
    "bom_groups": {
        "shape": "object",
        "fields": ["project", "revision", "groups", "count", "_untrusted"],
        "untrusted_fields": ["project", "groups"],
    },
    "bom_validation": {
        "shape": "object",
        "fields": ["project", "revision", "valid", "issues", "count", "_untrusted"],
        "untrusted_fields": ["project", "issues"],
    },
    "bom_compare": {
        "shape": "object",
        "fields": [
            "base_project",
            "base_revision",
            "other_project",
            "other_revision",
            "other_path",
            "added",
            "removed",
            "changed",
            "_untrusted",
        ],
        "untrusted_fields": [
            "base_project",
            "other_project",
            "other_path",
            "added",
            "removed",
            "changed",
        ],
    },
}


SCHEMAS.update(placement_contract.SCHEMAS)


def _param(
    name: str, type_name: str, required: bool = False, multiple: bool = False
) -> dict[str, Any]:
    return {"name": name, "type": type_name, "required": required, "multiple": multiple}


def _command(
    path: str,
    description: str,
    schema: str,
    examples: list[str],
    permission: str = "read",
    params: list[dict[str, Any]] | None = None,
    blast_radius: str = "none",
    dry_run_schema: str | None = None,
) -> dict[str, Any]:
    command = {
        "path": path,
        "type": "write" if permission.startswith("write") else "query",
        "description": description,
        "params": params or [],
        "output_schema": schema,
        "examples": examples,
        "permission_tier": permission,
        "blast_radius": blast_radius,
    }
    if dry_run_schema:
        command["dry_run_output_schema"] = dry_run_schema
    return command


def _mock_example(command: str, extra: str = "") -> str:
    suffix = f" {extra}" if extra else ""
    return f"xpedition-cli {command}{suffix} --backend mock --project ./demo-project.json --compact"


def commands() -> list[dict[str, Any]]:
    result = [
        _command(
            "context",
            "Report runtime, backend and credential context",
            "context",
            ["xpedition-cli context --compact"],
            params=[_param("project", "path")],
        ),
        _command(
            "doctor",
            "Check the local environment and release readiness",
            "doctor",
            ["xpedition-cli doctor --compact"],
            params=[_param("project", "path")],
        ),
        _command(
            "system doctor",
            "Alias for the environment and release readiness check",
            "doctor",
            ["xpedition-cli system doctor --compact"],
            params=[_param("project", "path")],
        ),
        _command(
            "reference",
            "Return the live machine-readable command contract",
            "reference",
            [
                "xpedition-cli reference --compact",
                'xpedition-cli reference --command "pcb trace" --compact',
                "xpedition-cli reference --domain pcb --compact",
                "xpedition-cli reference --schema context --compact",
            ],
            params=selector_params(),
        ),
        _command(
            "changelog",
            "Read the embedded project change history",
            "changelog",
            [
                "xpedition-cli changelog --compact",
                "xpedition-cli changelog --since 0.0.0 --compact",
            ],
            params=[_param("since", "semver")],
        ),
        _command(
            "version",
            "Report the CLI release version",
            "version",
            ["xpedition-cli version --compact"],
        ),
        _command(
            "system version",
            "Alias for the CLI release version",
            "version",
            ["xpedition-cli system version --compact"],
        ),
        _command(
            "system capabilities",
            "List available and planned backends",
            "capabilities",
            ["xpedition-cli system capabilities --compact"],
        ),
        _command(
            "system license",
            "Report native Xpedition license and automation status",
            "license",
            ["xpedition-cli system license --compact"],
        ),
        _command(
            "exchange inspect",
            "Inspect a JSON, CSV or BOM exchange file",
            "exchange_inspect",
            ["xpedition-cli exchange inspect --input ./bom.csv --compact"],
            params=[_param("input", "path", True)],
        ),
        _command(
            "exchange import",
            "Import a supported exchange file into a local project",
            "exchange_import",
            [
                "xpedition-cli exchange import --input ./bom.csv --project "
                "./demo-project.json --dry-run --compact",
                "xpedition-cli exchange import --input ./bom.csv --project "
                "./demo-project.json --confirm <confirm_token> --compact",
            ],
            permission="write",
            params=[_param("input", "path", True), _param("project", "path", True)],
            blast_radius="the selected local project file",
            dry_run_schema="exchange_import_preview",
        ),
        _command(
            "session status",
            "Report local or native session state",
            "session_status",
            ["xpedition-cli session status --compact"],
        ),
        _command(
            "session logs",
            "Read bounded local session log lines",
            "session_logs",
            ["xpedition-cli session logs --compact"],
            params=[_param("limit", "integer"), _param("offset", "integer")],
        ),
        _command(
            "session start",
            "Launch Xpedition Layout or Designer and wait for its automation interface",
            "session_lifecycle",
            [
                "xpedition-cli session start --backend native_xpedition --kind pcb --compact",
                "xpedition-cli session start --backend native_xpedition --kind designer "
                "--project ./board.prj --compact",
            ],
            params=[_param("kind", "enum"), _param("project", "path")],
        ),
        _command(
            "session attach",
            "Attach to an Xpedition Layout or Designer instance that is already running",
            "session_lifecycle",
            ["xpedition-cli session attach --backend native_xpedition --kind designer --compact"],
            params=[_param("kind", "enum")],
        ),
        _command(
            "session open",
            "Open a project or board in the targeted Xpedition application",
            "session_lifecycle",
            [
                "xpedition-cli session open --backend native_xpedition --kind designer "
                "--project ./board.prj --compact"
            ],
            params=[_param("kind", "enum"), _param("project", "path", True)],
        ),
        _command(
            "session stop",
            "Quit the targeted Xpedition application after an explicit confirmation",
            "session_lifecycle",
            [
                "xpedition-cli session stop --backend native_xpedition --kind pcb "
                "--dry-run --compact",
                "xpedition-cli session stop --backend native_xpedition --kind pcb "
                "--confirm <confirm_token> --compact",
            ],
            permission="write",
            params=[_param("kind", "enum")],
            blast_radius="the running Xpedition application; unsaved design work is lost",
            dry_run_schema="session_stop_preview",
        ),
        _command(
            "project info",
            "Summarize a project file",
            "project_info",
            ["xpedition-cli project info --backend mock --project ./demo-project.json --compact"],
            params=[_param("project", "path")],
        ),
        _command(
            "project init",
            "Create a project: an empty MockBackend project, or with --template a new "
            "Xpedition project copied from a template project and opened in Designer",
            "project_init",
            [
                "xpedition-cli project init --project ./demo-project.json --name "
                "demo_board --dry-run --compact",
                "xpedition-cli project init --project ./demo-project.json --name "
                "demo_board --confirm <confirm_token> --compact",
                "xpedition-cli project init --backend native_xpedition --template "
                "D:/tpl/Tpl.prj --project D:/work/new/New.prj --dry-run --compact",
            ],
            permission="write",
            params=[
                _param("project", "path", True),
                _param("name", "string"),
                _param("template", "path"),
            ],
            blast_radius="the new local project file or project folder",
            dry_run_schema="project_init_preview",
        ),
        _command(
            "project snapshot",
            "Read a normalized project design snapshot",
            "project_snapshot",
            [
                "xpedition-cli project snapshot --backend mock --project "
                "./demo-project.json --compact"
            ],
            params=[_param("project", "path")],
        ),
        _command(
            "project tree",
            "Read the project hierarchy and component index",
            "project_tree",
            ["xpedition-cli project tree --backend mock --project ./demo-project.json --compact"],
            params=[_param("project", "path")],
        ),
        _command(
            "project diff",
            "Compare a project with its latest automatic backup",
            "project_diff",
            [_mock_example("project diff")],
            params=[_param("project", "path", True)],
        ),
        _command(
            "design snapshot",
            "Alias for project snapshot",
            "project_snapshot",
            [
                "xpedition-cli design snapshot --backend mock --project "
                "./demo-project.json --compact"
            ],
            params=[_param("project", "path")],
        ),
        _command(
            "change validate",
            "Validate a ChangeSet without reading or writing a project",
            "changeset_validation",
            ["xpedition-cli change validate --changeset ./changeset.json --compact"],
            params=[_param("changeset", "path", True)],
        ),
        _command(
            "change preview",
            "Preview a ChangeSet against a project",
            "change_preview",
            [
                "xpedition-cli change preview --backend mock --project "
                "./demo-project.json --changeset ./changeset.json --compact"
            ],
            params=[_param("changeset", "path", True), _param("project", "path")],
        ),
        _command(
            "change apply",
            "Apply a validated ChangeSet after confirmation",
            "change_apply",
            [
                "xpedition-cli change apply --backend mock --project ./demo-project.json "
                "--changeset ./changeset.json --dry-run --compact",
                "xpedition-cli change apply --backend mock --project ./demo-project.json "
                "--changeset ./changeset.json --confirm <confirm_token> --compact",
            ],
            permission="write",
            params=[_param("changeset", "path", True), _param("project", "path", True)],
            blast_radius="the selected local project file",
            dry_run_schema="change_preview",
        ),
        _command(
            "change history",
            "Read local ChangeSet apply and rollback history",
            "change_history",
            ["xpedition-cli change history --project ./demo-project.json --compact"],
            params=[
                _param("project", "path", True),
                _param("limit", "integer"),
                _param("offset", "integer"),
            ],
        ),
        _command(
            "change rollback",
            "Restore the most recent verified project backup",
            "change_rollback",
            [
                "xpedition-cli change rollback --project ./demo-project.json --dry-run --compact",
                "xpedition-cli change rollback --project ./demo-project.json "
                "--confirm <confirm_token> --compact",
            ],
            permission="write",
            params=[_param("project", "path", True)],
            blast_radius="the selected local project file",
            dry_run_schema="change_rollback_preview",
        ),
        _command(
            "review run",
            "Run the deterministic review: netlist rules, custom rules and, on a live design, "
            "Xpedition's own verification",
            "review_report",
            ["xpedition-cli review run --backend mock --project ./demo-project.json --compact"],
            params=[
                _param("project", "path"),
                _param("rules", "path"),
                _param("limit", "integer"),
                _param("offset", "integer"),
            ],
        ),
        _command(
            "review findings",
            "Read deterministic design review findings",
            "review_report",
            [_mock_example("review findings")],
            params=[
                _param("project", "path"),
                _param("rules", "path"),
                _param("limit", "integer"),
                _param("offset", "integer"),
            ],
        ),
        _command(
            "review report",
            "Read the complete deterministic review report",
            "review_report",
            [_mock_example("review report")],
            params=[
                _param("project", "path"),
                _param("rules", "path"),
                _param("limit", "integer"),
                _param("offset", "integer"),
            ],
        ),
        _command(
            "bom export",
            "Export normalized BOM rows from a project",
            "bom",
            ["xpedition-cli bom export --backend mock --project ./demo-project.json --compact"],
            params=[
                _param("project", "path"),
                _param("limit", "integer"),
                _param("offset", "integer"),
            ],
        ),
        _command(
            "bom normalize",
            "Return normalized BOM rows",
            "list_result",
            [_mock_example("bom normalize")],
            params=[
                _param("project", "path"),
                _param("limit", "integer"),
                _param("offset", "integer"),
            ],
        ),
        _command(
            "bom group",
            "Group BOM rows by part number",
            "bom_groups",
            [_mock_example("bom group")],
            params=[_param("project", "path")],
        ),
        _command(
            "bom variants",
            "List BOM rows assigned to a variant",
            "list_result",
            [_mock_example("bom variants")],
            params=[
                _param("project", "path"),
                _param("limit", "integer"),
                _param("offset", "integer"),
            ],
        ),
        _command(
            "bom missing",
            "List BOM rows missing part identifiers",
            "list_result",
            [_mock_example("bom missing")],
            params=[
                _param("project", "path"),
                _param("limit", "integer"),
                _param("offset", "integer"),
            ],
        ),
        _command(
            "bom duplicates",
            "List BOM rows sharing a part identifier",
            "list_result",
            [_mock_example("bom duplicates")],
            params=[
                _param("project", "path"),
                _param("limit", "integer"),
                _param("offset", "integer"),
            ],
        ),
        _command(
            "bom validate",
            "Validate required BOM identifiers and quantities",
            "bom_validation",
            [_mock_example("bom validate")],
            params=[_param("project", "path")],
        ),
        _command(
            "bom compare",
            "Compare normalized BOM rows between two projects",
            "bom_compare",
            [
                "xpedition-cli bom compare --project ./base.json "
                "--other-project ./other.json --compact"
            ],
            params=[_param("project", "path", True), _param("other_project", "path", True)],
        ),
    ]
    result.extend(
        [
            _command(
                "schematic sheets",
                "List schematic sheet hierarchy entries",
                "list_result",
                [_mock_example("schematic sheets")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "schematic components",
                "List normalized schematic components",
                "list_result",
                [_mock_example("schematic components")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "schematic pins",
                "List component pins and pin types",
                "list_result",
                [_mock_example("schematic pins")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "schematic nets",
                "List schematic nets",
                "list_result",
                [_mock_example("schematic nets")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "schematic connectivity",
                "List normalized net connections",
                "list_result",
                [_mock_example("schematic connectivity")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "schematic unconnected",
                "List pins without a connection",
                "list_result",
                [_mock_example("schematic unconnected")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "schematic power",
                "List power and ground nets",
                "list_result",
                [_mock_example("schematic power")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "schematic interfaces",
                "List declared schematic interfaces",
                "list_result",
                [_mock_example("schematic interfaces")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "schematic query",
                "Search schematic components, nets and connections",
                "list_result",
                [_mock_example("schematic query", "--query 3V3")],
                params=[_param("query", "string", True), _param("project", "path")],
            ),
            _command(
                "schematic apply",
                "Apply an approved schematic ChangeSet",
                "change_apply",
                [
                    "xpedition-cli schematic apply --backend mock --project ./demo-project.json "
                    "--changeset ./changeset.json --dry-run --compact",
                    "xpedition-cli schematic apply --backend mock --project ./demo-project.json "
                    "--changeset ./changeset.json --confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[_param("changeset", "path", True), _param("project", "path", True)],
                blast_radius="the selected local project file",
                dry_run_schema="change_preview",
            ),
            _command(
                "schematic export",
                "Render the schematic to a new PDF file through Xpedition's sch2pdf",
                "schematic_export",
                [
                    "xpedition-cli schematic export --backend native_xpedition "
                    "--project ./board.prj --output ./board.pdf --compact"
                ],
                params=[
                    _param("project", "path", True),
                    _param("output", "path"),
                    _param("color", "integer"),
                    _param("schematic", "string"),
                ],
                blast_radius="one new PDF file at --output; an existing file is never replaced",
            ),
            _command(
                "schematic draw",
                "Plan a schematic from a design description and draw it in Xpedition Designer",
                "schematic_draw",
                [
                    "xpedition-cli schematic draw --backend native_xpedition --project ./board.prj "
                    "--design ./design.json --dry-run --compact",
                    "xpedition-cli schematic draw --backend native_xpedition --project ./board.prj "
                    "--design ./design.json --confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[_param("project", "path", True), _param("design", "path", True)],
                blast_radius=(
                    "every sheet listed in the design is wiped and redrawn; symbol files are "
                    "written into the project's central-library partition"
                ),
                dry_run_schema="schematic_draw_preview",
            ),
            _command(
                "schematic show",
                "Activate a sheet, fit it and bring Xpedition Designer's window to the front",
                "schematic_show",
                [
                    "xpedition-cli schematic show --backend native_xpedition --project ./board.prj "
                    "--sheet 2 --compact",
                    "xpedition-cli schematic show --backend native_xpedition --project ./board.prj "
                    "--sheet 2 --output ./sheet2.png --compact",
                ],
                params=[
                    _param("project", "path", True),
                    _param("sheet", "integer"),
                    _param("output", "path"),
                ],
                blast_radius="the Designer window comes to the front; one new PNG at --output",
            ),
            _command(
                "pcb info",
                "Summarize PCB geometry and stackup counts",
                "pcb_info",
                ["xpedition-cli pcb info --backend mock --project ./demo-project.json --compact"],
                params=[_param("project", "path")],
            ),
            _command(
                "pcb create",
                "Create the project's board from a layout template of its central "
                "library through JobWizard's command line, listing the board design first "
                "and registering the cell partitions forward annotation needs",
                "pcb_create",
                [
                    "xpedition-cli pcb create --backend native_xpedition --project X.prj "
                    "--dry-run --compact",
                    "xpedition-cli pcb create --backend native_xpedition --project X.prj "
                    '--template "4 Layer Template" --confirm <confirm_token> --compact',
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("template", "string"),
                    _param("name", "string"),
                    _param("design", "string"),
                    _param("replace", "boolean"),
                ],
                blast_radius=(
                    "a new PCB folder beside the .prj; the .prj gains the board path, the "
                    "template name and a cell-library list; with --replace the existing "
                    "layout data is removed first"
                ),
                dry_run_schema="pcb_create_preview",
            ),
            _command(
                "pcb geometry",
                "The board as data for planning by hand: outline, pads (with net, layer "
                "and reference designator), traces (with width), vias, generated planes, "
                "holes, silkscreen, texts, and every component with its origin, extents "
                "and pins (name, net, position); to a JSON file with --output, else inline",
                "pcb_geometry",
                [
                    "xpedition-cli pcb geometry --backend native_xpedition --project X.prj "
                    "--output ./board.json --compact",
                ],
                params=[
                    _param("project", "path", True),
                    _param("output", "path"),
                    _param("replace", "boolean"),
                ],
                blast_radius="one JSON file at --output (replaced only with --replace)",
            ),
            _command(
                "pcb trace",
                "A trace drawn where the person says: a net, a layer, a width and its "
                "points in millimetres (Document.PutTrace), or a whole plan file of traces "
                "and vias (--file, JSON with `items`, `traces`, `vias`). With --geometry "
                "(pcb geometry's file) the plan is checked offline first — angles, "
                "clearances to pads, traces and vias of other nets, via pads touching any "
                "pad — and refused with the problems unless --dangerous. Layout's online "
                "DRC refuses an item that violates a clearance; the others go on. The "
                "preview names the nearest pin of every trace end; the result gives the "
                "open count of each touched net before and after",
                "pcb_trace",
                [
                    "xpedition-cli pcb trace --backend native_xpedition --project X.prj "
                    '--net I2C_SCL --layer 1 --width 0.254 --points "34.2,36.0 36.5,36.0 '
                    '36.5,40.5" --dry-run --compact',
                    "xpedition-cli pcb trace --backend native_xpedition --project X.prj "
                    "--file ./routes.json --confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("net", "string"),
                    _param("layer", "number"),
                    _param("width", "number"),
                    _param("points", "string"),
                    _param("file", "path"),
                    _param("geometry", "path"),
                    _param("pace", "number"),
                ],
                blast_radius="the board gains the listed traces and vias and is saved",
                dry_run_schema="pcb_trace_preview",
            ),
            _command(
                "pcb stitch",
                "The plan for ground stitching: a via beside every surface-mount pad of a "
                "net with a short top-layer stub, where the clearance check against the "
                "geometry file finds room (eight directions, four distances); pins without "
                "room are listed. Pure: works from pcb geometry's file, no Layout. Feed the "
                "plan to pcb trace --file",
                "pcb_stitch",
                [
                    "xpedition-cli pcb stitch --geometry ./board.json --net GND "
                    "--output ./stitch.json --compact",
                ],
                params=[
                    _param("geometry", "path", True),
                    _param("net", "string"),
                    _param("width", "number"),
                    _param("output", "path"),
                    _param("replace", "boolean"),
                ],
                blast_radius="one plan file at --output",
            ),
            _command(
                "pcb via",
                "A via placed where the person says (Document.PutVia): a net and a point, "
                "with the board's via padstack unless --padstack names another",
                "pcb_trace",
                [
                    "xpedition-cli pcb via --backend native_xpedition --project X.prj "
                    "--net GND --at 36.5,40.5 --dry-run --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("net", "string", True),
                    _param("at", "string", True),
                    _param("padstack", "string"),
                    _param("pace", "number"),
                ],
                blast_radius="the board gains one via and is saved",
                dry_run_schema="pcb_trace_preview",
            ),
            _command(
                "pcb unroute",
                "Delete the traces and vias of the named nets (--nets A,B), of every net "
                "(--all), or just the trace with a vertex / the via centred at --at x,y "
                "(with --layer N for one layer), then regenerate the planes and save",
                "pcb_unroute",
                [
                    "xpedition-cli pcb unroute --backend native_xpedition --project X.prj "
                    "--nets I2C_SCL,I2C_SDA --dry-run --compact",
                    "xpedition-cli pcb unroute --backend native_xpedition --project X.prj "
                    "--all --confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("nets", "string"),
                    _param("all", "boolean"),
                    _param("at", "string"),
                    _param("layer", "number"),
                ],
                blast_radius="the routing of the named nets is deleted and the board saved",
                dry_run_schema="pcb_unroute_preview",
            ),
            _command(
                "pcb move",
                "Move one part to a position (its cell origin, millimetres) and rotation "
                "with Layout's placement DRC on, so a part moved onto another is refused; "
                "its traces stay where they were",
                "pcb_move",
                [
                    "xpedition-cli pcb move --backend native_xpedition --project X.prj "
                    "--refdes U302 --to 34,35.5 --rotate 90 --dry-run --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("refdes", "string", True),
                    _param("to", "string", True),
                    _param("rotate", "number"),
                ],
                blast_radius="one part moves and the board is saved",
                dry_run_schema="pcb_move_preview",
            ),
            _command(
                "pcb labels",
                "Move every part's silkscreen reference designator to a free spot beside "
                "the part (above, below, left, right, the corners, then further out) clear "
                "of other parts, pads, holes, the zone labels, the other designators and "
                "the board's edge; a designator that fits nowhere stays and is listed",
                "pcb_labels",
                [
                    "xpedition-cli pcb labels --backend native_xpedition --project X.prj "
                    "--dry-run --compact",
                    "xpedition-cli pcb labels --backend native_xpedition --project X.prj "
                    "--gap 0.3 --confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[_param("project", "path", True), _param("gap", "number")],
                blast_radius="the silkscreen designators move and the board is saved",
                dry_run_schema="pcb_labels_preview",
            ),
            _command(
                "pcb render",
                "A PNG of the board drawn from its geometry (outline, pads, traces, vias, "
                "planes, holes, silkscreen) in KiCad-like colours; needs no screen, so it "
                "works with the desktop locked, unlike `pcb show --output`",
                "pcb_render",
                [
                    "xpedition-cli pcb render --backend native_xpedition --project X.prj "
                    "--output ./board_top.png --compact",
                    "xpedition-cli pcb render --backend native_xpedition --project X.prj "
                    "--output ./board_bottom.png --side bottom --scale 30 --replace --compact",
                ],
                params=[
                    _param("project", "path", True),
                    _param("output", "path", True),
                    _param("side", "string"),
                    _param("scale", "number"),
                    _param("replace", "boolean"),
                ],
                blast_radius="one PNG at --output (replaced only with --replace)",
            ),
            _command(
                "pcb show",
                "Bring Xpedition Layout's board window to the front under a display "
                "scheme that shows the parts (default `Loc: All On`; the stock templates "
                "open with `Loc: Assembly Bottom`, which hides top-side parts). "
                "--top-view makes and picks `Loc: Top View`: plane copper filled, inner "
                "layers off, assembly texts and the drill drawing off — the picture "
                "pcb render draws, on Layout's own screen",
                "pcb_show",
                [
                    "xpedition-cli pcb show --backend native_xpedition --project X.prj --compact",
                    "xpedition-cli pcb show --backend native_xpedition --project X.prj "
                    "--output ./board.png --compact",
                ],
                params=[
                    _param("project", "path", True),
                    _param("scheme", "string"),
                    _param("top_view", "boolean"),
                    _param("output", "path"),
                ],
                blast_radius=(
                    "the Layout window comes to the front and its display scheme changes; "
                    "--top-view writes Config/Top View.dcs once and reopens the board; "
                    "one new PNG at --output"
                ),
            ),
            _command(
                "pcb arrange",
                "A first placement of the board's unplaced parts that follows the "
                "connections: one cluster per IC with its parts around it (decoupling "
                "nearest), clusters in rows by sheet, connectors on the side edges, test "
                "points along the bottom, a silkscreen zone label per sheet from the design "
                "file's `zone`; the dry run measures every footprint through Layout and "
                "binds the token to the plan",
                "pcb_arrange",
                [
                    "xpedition-cli pcb arrange --backend native_xpedition --project X.prj "
                    "--design design.json --dry-run --compact",
                    "xpedition-cli pcb arrange --backend native_xpedition --project X.prj "
                    "--design design.json --confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("design", "path"),
                    _param("all", "boolean"),
                    _param("pace", "number"),
                ],
                blast_radius=(
                    "every unplaced part is placed, zone labels are written on the top "
                    "silkscreen and the board is saved"
                ),
                dry_run_schema="pcb_arrange_preview",
            ),
            _command(
                "pcb drc",
                "Run Layout's Batch DRC (menu command 32769, its dialog answered) and list "
                "every hazard on the board with type, description, position, objects and "
                "clearances; --no-run only reads, --online adds the online DRC's hazards",
                "pcb_drc",
                [
                    "xpedition-cli pcb drc --backend native_xpedition --project X.prj --compact",
                    "xpedition-cli pcb drc --backend native_xpedition --project X.prj "
                    "--no-run --compact",
                ],
                params=[
                    _param("project", "path", True),
                    _param("no-run", "boolean"),
                    _param("online", "boolean"),
                ],
                blast_radius="Layout runs its checks and records hazards on the board",
            ),
            _command(
                "pcb route",
                "Run Layout's autorouter passes over every net (Document.NewRoutePass: "
                "Route with an effort range, Via Min, Smooth; also fanout, novia, spread, "
                "expand, removehangers) and save; the dry run reports the routed state",
                "pcb_route",
                [
                    "xpedition-cli pcb route --backend native_xpedition --project X.prj "
                    "--dry-run --compact",
                    "xpedition-cli pcb route --backend native_xpedition --project X.prj "
                    '--passes "route:1-5,viamin,smooth" --layers 1,4 --confirm <confirm_token> '
                    "--compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("passes", "string"),
                    _param("layers", "string"),
                    _param("unroute", "boolean"),
                ],
                blast_radius="traces and vias on every net the passes touch; the board is saved",
                dry_run_schema="pcb_route_preview",
            ),
            _command(
                "pcb outline",
                "Replace the board outline by a width × height millimetre rectangle from "
                "the origin (Document.PutBoardOutline)",
                "pcb_outline",
                [
                    "xpedition-cli pcb outline --backend native_xpedition --project X.prj "
                    "--width 45 --height 30 --dry-run --compact",
                    "xpedition-cli pcb outline --backend native_xpedition --project X.prj "
                    "--width 60 --height 45 --radius 3 --confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("width", "number", True),
                    _param("height", "number", True),
                    _param("radius", "number"),
                ],
                blast_radius="the board outline is replaced and the board is saved",
                dry_run_schema="pcb_outline_preview",
            ),
            _command(
                "pcb rules",
                "A net class with its trace widths on every layer, through Constraint "
                "Manager's automation (Layout's own automation reads rules only); Layout "
                "re-reads them through SynchCES",
                "pcb_rules",
                [
                    "xpedition-cli pcb rules --backend native_xpedition --project X.prj "
                    "--class POWER --nets VBAT,+3V3 --width 0.5 --dry-run --compact",
                    "xpedition-cli pcb rules --backend native_xpedition --project X.prj "
                    "--class POWER --nets VBAT,+3V3 --width 0.5 --min 0.4 --expansion 0.6 "
                    "--confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("class", "string", True),
                    _param("nets", "string"),
                    _param("width", "number", True),
                    _param("min", "number"),
                    _param("expansion", "number"),
                ],
                blast_radius=(
                    "one net class and its trace widths change in the constraint database; "
                    "Layout re-reads its constraints"
                ),
                dry_run_schema="pcb_rules_preview",
            ),
            _command(
                "pcb export",
                "The fabrication package: Layout's ODB++, Gerber (RS-274X) and NC drill "
                "outputs through their Output dialogs, gathered into a folder with a "
                "manifest, a README for the board house, a centroid file and a BOM",
                "pcb_export",
                [
                    "xpedition-cli pcb export --backend native_xpedition --project X.prj "
                    "--dry-run --compact",
                    "xpedition-cli pcb export --backend native_xpedition --project X.prj "
                    "--formats odb,gerber,ncdrill --output ./fab --confirm <confirm_token> "
                    "--compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("formats", "string"),
                    _param("output", "path"),
                ],
                blast_radius=(
                    "Layout's output setups are patched (board closed and reopened), its "
                    "Output folders are written and the package folder is created"
                ),
                dry_run_schema="pcb_export_preview",
            ),
            _command(
                "pcb holes",
                "A non-plated mounting hole in each corner of the board outline "
                "(Document.PutMountingHoleEx with the library's MH-C<diameter>-NONPLATED "
                "padstack); 2.2 mm, 3.5 mm from both edges, by default",
                "pcb_holes",
                [
                    "xpedition-cli pcb holes --backend native_xpedition --project X.prj "
                    "--dry-run --compact",
                    "xpedition-cli pcb holes --backend native_xpedition --project X.prj "
                    "--diameter 2.2 --inset 3.5 --confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("diameter", "number"),
                    _param("inset", "number"),
                    _param("replace", "boolean"),
                ],
                blast_radius="mounting holes are added at the corners and the board is saved",
                dry_run_schema="pcb_holes_preview",
            ),
            _command(
                "pcb pour",
                "A plane shape (copper pour) for a net on a layer, inset from the outline "
                "(Document.PutPlaneShape); GND on layer 2 by default",
                "pcb_pour",
                [
                    "xpedition-cli pcb pour --backend native_xpedition --project X.prj "
                    "--net GND --layer 2 --dry-run --compact",
                    "xpedition-cli pcb pour --backend native_xpedition --project X.prj "
                    "--net GND --layer 2 --confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("net", "string"),
                    _param("layer", "integer"),
                    _param("margin", "number"),
                    _param("replace", "boolean"),
                ],
                blast_radius="one plane shape is added and the board is saved",
                dry_run_schema="pcb_pour_preview",
            ),
            _command(
                "pcb annotate",
                "Forward-annotate the packaged schematic into the board through Layout's "
                "Project Integration (packager, Database Load, netload), then save the board",
                "pcb_annotate",
                [
                    "xpedition-cli pcb annotate --backend native_xpedition --project X.prj "
                    "--dry-run --compact",
                    "xpedition-cli pcb annotate --backend native_xpedition --project X.prj "
                    "--confirm <confirm_token> --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("design", "string"),
                    _param("unroute", "boolean"),
                ],
                blast_radius=(
                    "the board's components and nets follow the packaged schematic, with "
                    "--unroute every trace and via goes first; Layout saves the board"
                ),
                dry_run_schema="pcb_annotate_preview",
            ),
            *[
                _command(
                    f"pcb {verb}",
                    f"List PCB {verb} objects",
                    "list_result",
                    [_mock_example(f"pcb {verb}", "--query U1" if verb == "query" else "")],
                    params=[
                        _param("project", "path"),
                        _param("limit", "integer"),
                        _param("offset", "integer"),
                        _param("query", "string", verb == "query"),
                    ],
                )
                for verb in (
                    "components",
                    "footprints",
                    "nets",
                    "layers",
                    "stackup",
                    "tracks",
                    "vias",
                    "zones",
                    "keepouts",
                    "query",
                )
            ],
            _command(
                "constraints list",
                "List design constraints",
                "list_result",
                [_mock_example("constraints list")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "constraints query",
                "Search design constraints",
                "list_result",
                [_mock_example("constraints query", "--query impedance")],
                params=[_param("query", "string", True), _param("project", "path")],
            ),
            _command(
                "constraints validate",
                "Validate constraint records",
                "constraints_validation",
                [_mock_example("constraints validate")],
                params=[_param("project", "path")],
            ),
            _command(
                "constraints export",
                "Export normalized constraint records",
                "list_result",
                [_mock_example("constraints export")],
                params=[_param("project", "path")],
            ),
            _command(
                "analysis results",
                "List stored ERC, DRC, DFM and analysis results",
                "analysis_results",
                [_mock_example("analysis results")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "analysis run",
                "Run deterministic MockBackend ERC, DRC or DFM checks",
                "analysis_run",
                [_mock_example("analysis run", "--kind all")],
                params=[
                    _param("kind", "enum"),
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            *[
                _command(
                    f"analysis {kind}",
                    f"List stored {kind.upper()} findings",
                    "analysis_results",
                    [_mock_example(f"analysis {kind}")],
                    params=[
                        _param("project", "path"),
                        _param("limit", "integer"),
                        _param("offset", "integer"),
                    ],
                )
                for kind in ("erc", "drc", "dfm")
            ],
            _command(
                "manufacturing artifacts",
                "List manufacturing artifacts",
                "list_result",
                [_mock_example("manufacturing artifacts")],
                params=[
                    _param("project", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "manufacturing verify",
                "Validate manufacturing artifact metadata",
                "manufacturing_verify",
                [_mock_example("manufacturing verify")],
                params=[_param("project", "path")],
            ),
            _command(
                "manufacturing bom",
                "Export manufacturing BOM rows",
                "bom",
                [_mock_example("manufacturing bom")],
                params=[_param("project", "path")],
            ),
            _command(
                "library build",
                "Generate placeholder padstacks, cells and parts for a design and import "
                "them into the project's central library through the stock HKP "
                "converters; --package then runs the packager",
                "library_build",
                [
                    "xpedition-cli library build --backend native_xpedition --project X.prj "
                    "--design design.json --dry-run --compact",
                    "xpedition-cli library build --backend native_xpedition --project X.prj "
                    "--design design.json --confirm <confirm_token> --package --compact",
                ],
                permission="write",
                params=[
                    _param("project", "path", True),
                    _param("design", "path", True),
                    _param("partition", "string"),
                    _param("package", "boolean"),
                ],
                blast_radius="the project's central library and its .prj parts-database list",
                dry_run_schema="library_build_preview",
            ),
            _command(
                "library search",
                "Search normalized library records",
                "library_search",
                [_mock_example("library search", "--query RES")],
                params=[_param("query", "string", True), _param("project", "path")],
            ),
            *[
                _command(
                    f"library {kind}",
                    f"List normalized library {kind}",
                    "list_result",
                    [_mock_example(f"library {kind}")],
                    params=[
                        _param("project", "path"),
                        _param("limit", "integer"),
                        _param("offset", "integer"),
                    ],
                )
                for kind in ("parts", "symbols", "footprints", "padstacks", "models")
            ],
            _command(
                "library validate",
                "Validate normalized library records",
                "library_validation",
                [_mock_example("library validate")],
                params=[_param("project", "path")],
            ),
            _command(
                "agent snapshot",
                "Expose a normalized snapshot to an Agent integration",
                "project_snapshot",
                [_mock_example("agent snapshot")],
                params=[_param("project", "path")],
            ),
            _command(
                "agent query",
                "Search normalized design data for an Agent integration",
                "list_result",
                [_mock_example("agent query", "--query 3V3")],
                params=[_param("query", "string", True), _param("project", "path")],
            ),
            _command(
                "agent review",
                "Run design review for an Agent integration",
                "review_report",
                [_mock_example("agent review")],
                params=[
                    _param("project", "path"),
                    _param("rules", "path"),
                    _param("limit", "integer"),
                    _param("offset", "integer"),
                ],
            ),
            _command(
                "agent capabilities",
                "Expose backend capabilities to an Agent integration",
                "capabilities",
                ["xpedition-cli agent capabilities --compact"],
            ),
            _command(
                "agent serve",
                "Serve newline-delimited JSON requests over stdio",
                "agent_stream",
                ["xpedition-cli agent serve --transport stdio"],
            ),
        ]
    )
    result.extend(placement_contract.commands())
    return result + pin_commands() + [api_command()]


def release_readiness() -> dict[str, Any]:
    return {
        "level": "beta",
        "fcc_required": True,
        "fcc_status": "verified",
        "mock_upstream_required": True,
        "mock_upstream_status": "verified",
        "live_smoke_required_for_stable": True,
        "live_smoke_status": "missing",
        "reason": (
            "The original 107-command release recorded command-level and native evidence; "
            "neither the added offline pin workflows nor the metadata inventory extend that "
            "native evidence or validate target Xpedition behavior. The contract "
            "tests cover success, validation, usage, confirmation, conflict, not-found, "
            "backend-unavailable and timeout paths, empty results, paging, the output "
            "envelope, exit codes and the stdout/stderr boundary; and docs/E2E.md "
            "records the whole chain on a licensed installation — a schematic drawn and "
            "read back pin by pin, a board created, annotated, placed, routed by hand "
            "and by the router, poured, checked (0 DRC errors) and packaged for "
            "fabrication. Context for that evidence: it comes from one Windows "
            "installation of XPED2604, with footprints converted from an open-source "
            "library and placeholder part numbers, and no second machine has repeated "
            "it. Selected placement tasks have command-level and simulated native-object "
            "tests, but the new native placement path has no licensed smoke record; the "
            "retained docs/E2E.md evidence does not validate these additions. Run "
            "disposable-board top/bottom, protected-part, refusal, stale-preview, "
            "save/close/reopen and DRC checks before marking it stable."
        ),
        "required_evidence": [
            "functional_contract_coverage_100",
            "mock_upstream_contract_tests",
            "recorded_live_smoke_for_stable",
        ],
    }


def _full_reference() -> dict[str, Any]:
    candidates = [
        Path(__file__).resolve().parent.parent / "contract" / "contract.json",
        Path(getattr(sys, "_MEIPASS", "")) / "contract" / "contract.json",
        Path.cwd() / "contract" / "contract.json",
    ]
    contract_path = next((path for path in candidates if path.exists()), candidates[0])
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    return {
        "tool": "xpedition-cli",
        "version": __version__,
        "risk_tier": "T1",
        "release_readiness": release_readiness(),
        "global_flags": [
            {
                "name": "format",
                "type": "enum",
                "choices": ["json", "text", "raw"],
                "default": "json",
            },
            {"name": "json", "type": "boolean", "alias_for": "format=json"},
            {"name": "compact", "type": "boolean", "default": False},
            {
                "name": "fields",
                "type": "string",
                "description": (
                    "Comma-separated data paths, including items.refdes or items[].refdes; "
                    "pagination and _untrusted are retained."
                ),
            },
            {
                "name": "backend",
                "type": "enum",
                "choices": ["mock", "native_xpedition"],
                "default": "mock",
            },
            {"name": "project", "type": "path"},
            {"name": "changeset", "type": "path"},
            {
                "name": "input",
                "type": "path",
                "applies_to": [
                    "exchange inspect",
                    "exchange import",
                    "schematic pin-plan",
                    "schematic pin-check",
                    "system api-inventory",
                    "pcb placement-plan",
                ],
            },
            {
                "name": "name",
                "type": "string",
                "applies_to": ["project init", "system api-inventory"],
            },
            {"name": "query", "type": "string", "applies_to": ["* query"]},
            {
                "name": "kind",
                "type": "enum",
                "choices": ["all", "erc", "drc", "dfm"],
                "applies_to": ["analysis run"],
            },
            {"name": "limit", "type": "integer", "applies_to": ["list commands"]},
            {"name": "offset", "type": "integer", "applies_to": ["list commands"]},
            {"name": "since", "type": "semver", "applies_to": ["changelog"]},
            {
                "name": "rules",
                "type": "path",
                "applies_to": ["review run", "review findings", "review report", "agent review"],
            },
            {"name": "other-project", "type": "path", "applies_to": ["bom compare"]},
            {
                "name": "dry-run",
                "type": "boolean",
                "applies_to": [item["path"] for item in commands() if item["type"] == "write"],
            },
            {
                "name": "confirm",
                "type": "string",
                "applies_to": [item["path"] for item in commands() if item["type"] == "write"],
            },
            {
                "name": "backup",
                "type": "boolean",
                "default": True,
                "applies_to": ["change apply", "change rollback", "schematic apply"],
                "description": "kept for explicitness; existing files are always backed up",
            },
            {
                "name": "quiet",
                "type": "boolean",
                "description": "suppress non-error stderr progress",
            },
            {
                "name": "transport",
                "type": "enum",
                "choices": ["stdio", "mcp"],
                "applies_to": ["agent serve"],
            },
        ],
        "commands": commands(),
        "schemas": {
            **SCHEMAS,
            "pin_assignment": PIN_OUTPUT_SCHEMA,
            "api_inventory": API_OUTPUT_SCHEMA,
        },
        "exit_codes": contract["exit_codes"]["table"],
        "error_codes": {
            name: {"exit": spec["exit"], "retryable": spec["retryable"]}
            for name, spec in CODES.items()
        },
    }


def reference(
    *, command: str | None = None, domain: str | None = None, schema: str | None = None
) -> dict[str, Any]:
    return select_reference(_full_reference(), command=command, domain=domain, schema=schema)
