"""The machine contract `reference` returns, assembled from the command registry."""

from __future__ import annotations

import json
from typing import Any

from . import __version__, resources
from .cli.registry import GLOBAL_DESCRIPTIONS, GLOBAL_OPTIONS, NEEDS, STAGES, WORKFLOW, commands
from .contract_gen import CODES
from .errors import CLIError
from .reference_query import select_reference


def _object(fields: list[str], untrusted: list[str], **shape: Any) -> dict[str, Any]:
    return {"shape": "object", "fields": fields, "untrusted_fields": untrusted, **shape}


SCHEMAS: dict[str, dict[str, Any]] = {
    "context": _object(
        [
            "version",
            "config",
            "xpedition",
            "project",
            "credentials",
            "knowledge_base",
            "notices",
            "_untrusted",
        ],
        ["config.directory", "xpedition", "project", "knowledge_base"],
    ),
    "doctor": _object(
        ["checks", "version", "_untrusted"], ["checks[].message", "checks[].details"]
    ),
    "reference": _object(
        [
            "tool",
            "version",
            "risk_tier",
            "release_readiness",
            "global_flags",
            "needs",
            "stages",
            "workflow",
            "commands",
            "schemas",
            "exit_codes",
            "error_codes",
            "selection",
        ],
        [],
        optional_fields=["selection"],
    ),
    "changelog": _object(["current_version", "since", "entries"], ["entries[].changes"]),
    "version": _object(["version"], []),
    "kb_list": _object(["documents", "count", "config", "_untrusted"], ["documents", "config"]),
    "kb_add": _object(
        ["name", "url", "about", "replaced", "config", "_untrusted"],
        ["url", "about", "replaced", "config"],
    ),
    "kb_remove": _object(["name", "removed", "url", "config", "_untrusted"], ["url", "config"]),
    "session_start": _object(
        [
            "domain",
            "application",
            "started",
            "attached",
            "pid",
            "project",
            "prompts",
            "_untrusted",
        ],
        ["project", "prompts"],
    ),
    "session_status": _object(
        ["xpedition", "designer", "layout", "recorded", "stale", "probe_error", "_untrusted"],
        ["xpedition.reason", "recorded"],
        optional_fields=["probe_error"],
    ),
    "session_stop": _object(["closed", "domain", "application"], []),
    "project_create": _object(
        [
            "project",
            "path",
            "template",
            "created",
            "files",
            "bytes",
            "rewritten",
            "parts_databases",
            "template_closed",
            "opened",
            "_untrusted",
        ],
        ["project", "path", "template", "rewritten", "parts_databases"],
    ),
    "project_info": _object(
        [
            "project",
            "path",
            "xpedition_version",
            "central_library",
            "designs",
            "board",
            "ascii_path",
            "_untrusted",
        ],
        ["project", "path", "central_library", "designs", "board"],
    ),
    "library_build": _object(
        [
            "project",
            "library",
            "partition",
            "steps",
            "failed",
            "pdb_registered",
            "cells_registered",
            "cells_missing",
            "ok",
            "hint",
            "package",
            "summary",
            "_untrusted",
        ],
        ["project", "library", "steps", "package", "summary"],
    ),
    "library_import": _object(
        [
            "project",
            "library",
            "root",
            "libraries",
            "cells",
            "dropped",
            "partitions",
            "failed",
            "seconds",
            "ok",
            "items",
            "summary",
            "skipped",
            "_untrusted",
        ],
        ["project", "library", "root", "partitions", "items", "skipped"],
        items_shape=[
            "target",
            "ok",
            "partition",
            "cells",
            "padstacks",
            "issues",
            "dropped",
            "dropped_samples",
            "error",
        ],
        summary_shape=["total", "succeeded", "failed", "dropped_footprints"],
    ),
    "schematic_render": _object(
        ["design", "pictures", "issues", "summary", "_untrusted"],
        ["design", "pictures", "issues"],
        items_shape=[
            "sheet",
            "path",
            "width",
            "height",
            "parts",
            "symbols",
            "wires",
            "labels",
            "texts",
            "issues",
            "issues_marked",
            "scale",
        ],
    ),
    "schematic_draw": _object(
        [
            "applied",
            "operations",
            "warnings",
            "netlist",
            "symbols_written",
            "sheets_drawn",
            "sheets_not_drawn",
            "sheets_kept",
            "project",
            "summary",
            "_untrusted",
        ],
        ["warnings", "netlist", "symbols_written", "project", "summary"],
    ),
    "schematic_edit": _object(
        ["project", "applied", "saved", "verification", "_untrusted"], ["project", "applied"]
    ),
    "schematic_check": _object(
        [
            "project",
            "findings",
            "count",
            "offset",
            "next_offset",
            "has_more",
            "summary",
            "designer_verification",
            "_untrusted",
        ],
        [
            "project",
            "findings[].finding",
            "findings[].evidence",
            "findings[].suggestion",
            "designer_verification.logs",
        ],
        items_shape=[
            "severity",
            "source",
            "refdes",
            "net",
            "finding",
            "evidence",
            "suggestion",
            "confidence",
        ],
    ),
    "list_result": _object(
        ["project", "items", "count", "offset", "next_offset", "has_more", "_untrusted"],
        ["project", "items"],
    ),
    "schematic_show": _object(
        ["sheet", "sheet_size", "foreground", "window", "screenshot", "_untrusted"],
        ["window", "screenshot"],
    ),
    "schematic_export": _object(
        [
            "exported",
            "format",
            "path",
            "size",
            "sheets",
            "pages",
            "warnings",
            "exit_code",
            "messages",
            "_untrusted",
        ],
        ["path", "sheets", "messages"],
    ),
    "pcb_create": _object(
        [
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
        ["project", "pcb", "log", "cells_registered", "replaced", "backup"],
    ),
    "pcb_annotate": _object(
        [
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
        ["pcb", "errors", "warnings", "prompts", "log"],
    ),
    "pcb_outline": _object(
        [
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
        ["pcb", "prompts"],
    ),
    "pcb_holes": _object(
        [
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
        ["pcb", "existing", "prompts"],
    ),
    "pcb_rules": _object(
        [
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
        ["pcb", "classes", "prompts"],
    ),
    "pcb_arrange": _object(
        [
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
        ["pcb", "plan", "labels", "clusters", "placed", "skipped_placed", "prompts"],
    ),
    "pcb_move": _object(
        [
            "pcb",
            "refdes",
            "before",
            "target",
            "routing",
            "after",
            "results",
            "outcome",
            "state_digest_before",
            "drc_restored",
            "verification",
            "issues",
            "summary",
            "write_attempted",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        ["pcb", "results", "issues", "prompts"],
        note="--refdes answers refdes/before/target/after; --file answers results, outcome, "
        "verification and summary for the whole selection",
    ),
    "pcb_labels": _object(
        [
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
        ["pcb", "labels", "prompts"],
    ),
    "pcb_route": _object(
        [
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
        ["pcb", "unrouted_before", "unrouted", "runs", "prompts"],
    ),
    "pcb_trace": _object(
        [
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
        ["pcb", "items", "warnings", "prompts"],
    ),
    "pcb_stitch": _object(
        ["net", "width", "vias", "items", "without_room", "path", "_untrusted"],
        ["items", "without_room"],
    ),
    "pcb_unroute": _object(
        [
            "pcb",
            "nets",
            "at",
            "layer",
            "to_delete",
            "targets",
            "deleted",
            "items",
            "summary",
            "skipped",
            "planes_regenerated",
            "routing",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        ["pcb", "prompts", "targets", "items", "skipped"],
        items_shape=["target", "ok", "traces", "vias", "deleted", "error"],
        summary_shape=["total", "succeeded", "failed"],
    ),
    "pcb_pour": _object(
        [
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
        ["pcb", "existing", "prompts"],
    ),
    "pcb_info": _object(
        [
            "project",
            "component_count",
            "placed_count",
            "footprint_count",
            "net_count",
            "track_count",
            "via_count",
            "layer_count",
            "_untrusted",
        ],
        ["project"],
    ),
    "pcb_geometry": _object(
        ["pcb", "layers", "counts", "path", "model", "prompts", "_untrusted"],
        ["pcb", "prompts", "model"],
    ),
    "pcb_render": _object(
        ["pcb", "side", "scale", "picture", "layers", "prompts", "_untrusted"],
        ["pcb", "picture", "prompts"],
    ),
    "pcb_show": _object(
        [
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
        ["pcb", "schemes", "prompts"],
    ),
    "pcb_metrics": _object(
        [
            "board",
            "parts",
            "ratsnest",
            "overlaps",
            "outside",
            "decoupling",
            "edge_parts",
            "routing",
            "summary",
            "delta",
            "path",
            "_untrusted",
        ],
        ["parts", "ratsnest", "overlaps", "outside", "decoupling", "edge_parts", "routing"],
        summary_shape=[
            "ratsnest_mm",
            "signal_ratsnest_mm",
            "crossings",
            "overlaps",
            "outside",
            "unplaced",
            "decoupling_max_mm",
            "edge_max_mm",
            "density",
            "trace_mm",
            "vias",
            "acute_corners",
        ],
    ),
    "pcb_check": _object(
        [
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
        ["pcb", "hazards", "online", "prompts"],
    ),
    "pcb_export": _object(
        [
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
            "skipped",
            "stale",
            "unplaced",
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
        ["pcb", "package", "prompts", "runs", "files", "gerber", "drill", "odb"],
    ),
    "bom_export": _object(
        [
            "project",
            "items",
            "count",
            "offset",
            "next_offset",
            "has_more",
            "grouped",
            "parts",
            "changes",
            "path",
            "_untrusted",
        ],
        ["project", "items", "changes"],
        optional_fields=["changes", "path"],
        items_shape=[
            "refdes",
            "internal_part_no",
            "manufacturer",
            "mpn",
            "description",
            "value",
            "package",
            "quantity",
            "variant",
            "dnp",
            "lifecycle",
            "datasheet",
        ],
    ),
    "bom_check": _object(
        ["project", "parts", "part_numbers", "valid", "issues", "_untrusted"], ["project", "issues"]
    ),
}


def _preview_schemas() -> dict[str, dict[str, Any]]:
    """Every dry run answers with a preview and a token; the draw adds its operations."""
    result: dict[str, dict[str, Any]] = {}
    for command in commands():
        if command.dry_run_schema:
            extra = ["operations"] if command.path == "schematic draw" else []
            fields = ["preview", *extra, "confirm_token", "expires_at", "_untrusted"]
            result[command.dry_run_schema] = _object(fields, ["preview", *extra])
    return result


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
            "Every command has command-level tests against a faked Xpedition adapter: "
            "success, usage, validation, confirmation, dangerous-gate, conflict, not-found, "
            "backend-unavailable and timeout paths, empty results, paging, the envelope, exit "
            "codes and the stdout/stderr boundary. docs/E2E.md records the whole chain on one "
            "licensed Windows installation of XPED2604: a project created from a template, a "
            "schematic drawn and read back, parts built and packaged, a board created, "
            "annotated, placed, routed, poured, checked and packaged for fabrication. No second "
            "installation has repeated it, which is what keeps this short of stable."
        ),
        "required_evidence": [
            "functional_contract_coverage_100",
            "mock_upstream_contract_tests",
            "recorded_live_smoke_for_stable",
        ],
    }


def _global_flags() -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    for name, kind in GLOBAL_OPTIONS.items():
        entry: dict[str, Any] = {
            "name": name,
            "type": "boolean" if kind == "switch" else "string",
            "description": GLOBAL_DESCRIPTIONS[name],
        }
        if name == "format":
            entry.update({"type": "enum", "choices": ["json", "text", "raw"], "default": "json"})
        flags.append(entry)
    return flags


def _full_reference() -> dict[str, Any]:
    contract_path = resources.locate("contract/contract.json")
    if contract_path is None:
        raise CLIError(
            "E_CONFIG",
            "this installation is missing its machine contract (contract/contract.json)",
            {"hint": "reinstall xpedition-cli"},
        )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    return {
        "tool": "xpedition-cli",
        "version": __version__,
        "risk_tier": "T2",
        "release_readiness": release_readiness(),
        "global_flags": _global_flags(),
        "needs": dict(NEEDS),
        "stages": list(STAGES),
        "workflow": [dict(step) for step in WORKFLOW],
        "commands": [command.contract() for command in commands()],
        "schemas": {**SCHEMAS, **_preview_schemas()},
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
