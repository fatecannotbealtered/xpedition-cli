"""The machine contract `reference` returns, assembled from the command registry."""

from __future__ import annotations

import json
from typing import Any

from . import __version__, resources
from .board_copper import evidence_boundary
from .board_general import input_schema as general_route_input_schema
from .cli.registry import GLOBAL_DESCRIPTIONS, GLOBAL_OPTIONS, NEEDS, STAGES, WORKFLOW, commands
from .contract_gen import CODES
from .copper_ties import boundary as tie_boundary
from .errors import CLIError
from .reference_query import select_reference


def _object(fields: list[str], untrusted: list[str], **shape: Any) -> dict[str, Any]:
    return {"shape": "object", "fields": fields, "untrusted_fields": untrusted, **shape}


_COMPONENT_FILE_PIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "bytes", "sha256"],
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "bytes": {"type": "integer", "minimum": 1},
        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    },
}
_COMPONENT_SELECTOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["component_id"],
    "properties": {
        "component_id": {"type": "string", "minLength": 1},
        "node_id": {"type": "string", "minLength": 1},
    },
}
_COMPONENT_ORIGIN_PIN_SCHEMA = {
    **_COMPONENT_FILE_PIN_SCHEMA,
    "required": ["path", "sha256"],
}
_COMPONENT_ORIGIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "kind",
        "origin_project",
        "origin_board",
        "source_backup",
        "target_project",
        "target_board",
        "genesis",
        "provenance",
        "copy_operation_id",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "kind": {"const": "isolated-component-connect"},
        "copy_operation_id": {"type": "string", "pattern": "^op[-_][a-zA-Z0-9]{8,80}$"},
        "origin_project": _COMPONENT_ORIGIN_PIN_SCHEMA,
        "origin_board": _COMPONENT_ORIGIN_PIN_SCHEMA,
        "source_backup": _COMPONENT_ORIGIN_PIN_SCHEMA,
        "continuation_receipt": _COMPONENT_ORIGIN_PIN_SCHEMA,
        "target_project": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
            },
        },
        "target_board": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
            },
        },
        "genesis": {
            "type": "object",
            "additionalProperties": False,
            "required": ["file_manifest", "project_sha256", "board_sha256"],
            "properties": {
                "file_manifest": {
                    "type": "object",
                    "minProperties": 1,
                    "additionalProperties": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                },
                "project_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "board_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
        },
        "provenance": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "project_library_relocation"],
            "properties": {
                "kind": {"const": "closed-backup-copy"},
                "project_library_relocation": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["encoding", "from_root", "to_root", "rewritten"],
                    "properties": {
                        "encoding": {"enum": ["utf-8", "utf-16"]},
                        "from_root": {"type": "string", "minLength": 1},
                        "to_root": {"type": "string", "minLength": 1},
                        "rewritten": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["key", "value"],
                                "additionalProperties": False,
                                "properties": {
                                    "key": {"type": "string"},
                                    "value": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    },
    "description": "Registration derived from successful public project create --closed-copy. "
    "copy_operation_id identifies its original continuous-journal record, not a verified flag. "
    "The original public receipt and independent complete file readback must authenticate it; "
    "actual source/ZIP/genesis derivation is recomputed. External prep records are ineligible.",
}
_COMPONENT_CONNECT_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "snapshot_id",
        "net",
        "net_uid",
        "layer",
        "first",
        "second",
        "width_mm",
        "points",
        "query",
        "query_file",
        "isolated_origin",
        "isolated_origin_file",
        "recovery_backup",
    ],
    "properties": {
        "snapshot_id": {"type": "string", "minLength": 1},
        "net": {"type": "string", "minLength": 1},
        "net_uid": {"type": "string", "minLength": 1},
        "layer": {"type": "integer", "minimum": 1, "maximum": 128},
        "first": _COMPONENT_SELECTOR_SCHEMA,
        "second": _COMPONENT_SELECTOR_SCHEMA,
        "width_mm": {"type": "number", "exclusiveMinimum": 0},
        "points": {
            "type": "array",
            "minItems": 2,
            "maxItems": 128,
            "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}},
        },
        "query": {"type": "object"},
        "query_file": _COMPONENT_FILE_PIN_SCHEMA,
        "isolated_origin": {"type": "object"},
        "isolated_origin_file": _COMPONENT_FILE_PIN_SCHEMA,
        "recovery_backup": {
            "anyOf": [{"type": "null"}, {"type": "string", "minLength": 1}],
            "description": "New exact recovery ZIP output outside the project. Non-no-op "
            "connections require it; the same verified Close window supplies all members, "
            "including actual icdb bytes, before Open. Existing outputs cannot be replaced.",
        },
    },
    "description": "Finite component request extracted from original pinned query/origin bytes. "
    "Snapshot identity, material, provenance and numeric geometry require independent runtime "
    "verification; this schema contains no caller verification or eligibility override.",
}


_SCHEMATIC_DRAWING_REVIEW_SCHEMA = {
    "drawing_review_shape": [
        "status",
        "basis",
        "units",
        "findings",
        "checked_rules",
        "object_counts",
        "unreviewed_objects",
        "unreviewed_collections",
        "empty_findings_mean_layout_clear",
        "manual_full_sheet_review_required",
        "native_erc_or_manufacturing_check",
        "limitations",
        "attribute_display_observations",
        "native_anchor_check",
    ],
    "drawing_unreviewed_object_shape": ["object", "reasons", "affected_rules"],
    "native_anchor_check_shape": [
        "rule",
        "fully_observed_attributes",
        "unreviewed_attributes",
        "exact_font_envelopes_observed",
    ],
    "native_anchor_finding_shape": [
        "rule",
        "severity",
        "sheet",
        "object",
        "anchor",
        "attributes",
        "message",
        "basis",
        "exact_collision_verified",
    ],
    "drawing_evidence_boundary": "Planned projections estimate font boxes and model only "
    "declared graphics. Native structural snapshots have no actual text envelopes, visible "
    "symbol graphics, wire paths or usable sheet bounds. Missing collections are explicitly "
    "not enumerated. One bounded advisory compares complete named native attribute getters: "
    "different non-empty values, visible3 and exactly matching owner/sheet/x/y/orientation/"
    "origin form an anchor-coincidence risk. Identical values are excluded; missing getters "
    "remain unreviewed. This does not measure fonts or verify exact collisions. Empty findings "
    "never establish clear actual layout or an ERC pass.",
}

_SCHEMATIC_SPACING_SCHEMA = {
    "design_file_spacing": {
        "path": "sheets[].blocks[].pitch",
        "block_kinds": ["chain", "ladder"],
        "type": "integer",
        "boolean_allowed": False,
        "units": "sheet_units_10_mil",
        "default": 60,
        "minimum": 60,
        "maximum": 4680,
        "multiple_of": 20,
        "scope": "Planned node spacing and wire lengths only; part_count*pitch must fit "
        "the sheet long edge. DS-07 still reports actual usable-area bounds. No net, "
        "physical pin mapping or already-drawn object changes are inferred.",
        "format_reference": "skills/xpedition-schematic/reference/schematic-design-format.md",
    }
}


_LAYOUT_PROJECTION_SCHEMA = {
    "layout_reminder_coverage_shape": [
        "status",
        "units",
        "advisory_proximity_mm",
        "distance_basis",
        "object_counts",
        "checked_rules",
        "unreviewed_objects",
        "unreviewed_collections",
        "warning_count",
        "empty_warnings_mean_layout_clear",
        "manual_visual_review_required",
        "limitations",
        "budgets",
        "collection_coverage",
        "layer_count",
        "board_edge_coverage",
    ],
    "layout_checked_rule_shape": [
        "rule",
        "pairs_checked",
        "pairs_excluded",
        "pairs_unchecked",
        "warnings",
    ],
    "layout_warning_shape": [
        "kind",
        "objects",
        "envelopes_mm",
        "envelope_sources",
        "projected_distance_mm",
        "advisory_proximity_mm",
        "meaning",
    ],
    "layout_object_identity_shape": [
        "kind",
        "observation_index",
        "model_index",
        "uid",
        "refdes",
        "pin",
        "layer",
        "side",
        "orientation_deg",
        "text_kind",
    ],
    "layout_unreviewed_object_shape": ["object", "reasons", "affected_rules"],
    "layout_observation_shape": [
        "units",
        "collections",
        "component_envelopes",
        "unreadable_objects",
        "trace_envelopes",
        "layer_count",
        "board_outline",
    ],
    "layout_text_observation_metadata": [
        "uid",
        "owner_observed",
        "observed_side",
        "fabrication_layer",
        "rotation",
        "mirrored",
        "height_source",
        "envelope_source",
        "stroke_source",
        "extents",
        "strokes",
    ],
    "layout_trace_envelope_shape": [
        "observation_index",
        "model_index",
        "uid",
        "geometry_sha256",
        "width_source",
    ],
    "layout_collection_coverage_shape": [
        "complete",
        "enumeration_complete",
        "observed_count",
        "geometry_complete",
        "geometry_observed_count",
        "geometry_unreadable_count",
        "geometry_scope",
        "reminder_checkable_count",
        "reminder_uncheckable_count",
        "reminder_checkable_complete",
    ],
    "layout_layer_count_shape": [
        "observed",
        "value",
        "source",
        "reason",
        "observation_error",
        "renderer_value",
        "renderer_source",
    ],
    "layout_board_edge_coverage_shape": [
        "outer_boundary_supported",
        "outer_boundary_checked",
        "inner_inventory_observed",
        "inner_contour_count",
        "inner_boundaries_checked",
        "complete",
    ],
    "physical_capture_boundary": "Only the explicitly named layout_observation analysis sidecar "
    "is outside mutation-protection geometry. Native raw paths, widths, identities and every "
    "other physical or unknown field stay exact. The sidecar remains in artifact/view models "
    "and warning reports; this does not relax any persistence-equivalence algorithm.",
    "layout_budget_shape": [
        "object_limit",
        "objects_examined",
        "objects_omitted",
        "pair_limit",
        "pairs_examined",
        "pair_budget_exhausted",
        "warning_limit",
        "warnings_omitted",
        "unreviewed_limit",
        "unreviewed_omitted",
        "path_point_limit_per_object",
    ],
    "layout_boundary": (
        "Read-only world-mm projection advisories from actual native extrema/vector strokes "
        "or explicitly supported geometry. No string-length font-envelope estimate. The "
        "declared0.3mm proximity threshold is a visual-review heuristic, not an electrical or "
        "silkscreen rule. Missing envelope/layer/side/direction/provenance is explicit incomplete "
        "coverage; warnings=[] never establishes a clean layout. Object indices identify only "
        "this observation, not persistent UIDs. No protected object is moved, hidden or deleted. "
        "Collection enumeration, geometric conversion and reminder checkability are separate. "
        "An enumerated object with empty/unreadable geometry stays explicitly unreviewed. "
        "Display layer/count fallbacks never establish observed conductor layers or justify "
        "opposite-side exclusions. Trace provenance binds both original enumeration index and "
        "compressed model index to exact geometry/UID. Rectangular outer-edge checks do not "
        "cover internal cutouts; unknown or nonempty inner inventory remains incomplete. "
        "Full image review, physical geometry, native DRC and persistence are separate."
    ),
}


SCHEMAS: dict[str, dict[str, Any]] = {
    "schematic_package": _object(
        ["packaged", "project", "returncode", "stdout", "stderr", "_untrusted"],
        ["project", "stdout", "stderr"],
    ),
    "check_result": _object(
        [
            "check_run_id",
            "domain",
            "execution",
            "native_completion",
            "report",
            "binding",
            "stale",
            "freshness",
            "record_sha256",
            "source",
            "native_executed_by_result",
            "requested_configuration_records",
            "hazard_content_verification",
            "configuration_applicability",
            "limitations",
            "_untrusted",
        ],
        ["report", "binding", "requested_configuration_records"],
        historical_native_files={
            "command": "pcb checks result --native-files retained-manifest.json",
            "exclusive_with": "--id",
            "manifest_file_fields": ["group", "role", "copy", "bytes", "sha256"],
            "required_roles": {
                "input_configurations": ["rules", "elements"],
                "outputs_after_go": [
                    "report",
                    "violations",
                    "effective_rules",
                    "effective_elements",
                ],
            },
            "path_basis": "copy is absolute or relative to the manifest; every listed file "
            "is verified before and after parsing",
            "content_binding": "Only verified immutable byte captures are parsed. Output "
            "and requested-configuration hashes/lengths describe those same consumed bytes; "
            "a transient path change cannot substitute content. Multi-file captures are "
            "not an atomic database snapshot. Live timestamp/prior-file gates remain separate.",
            "source": "historical-native-files",
            "freshness": "historical_not_checked",
            "native_completion": "not_established_by_historical_parse",
            "time_basis": "Original DRCFILE DATE/TIME values are retained; "
            "no execution time is invented",
            "record_fields": [
                "kind",
                "position_mm",
                "objects",
                "source_line",
                "raw_record",
                "record_tree",
            ],
            "supported_record_types": ["PROXIMITY", "UNROUTED_NET", "PARTIAL_NET"],
            "partial_scope": "Observed151.0 DRCFILE2002 single PAD PART with complete "
            "refdes/net/name/class/layer and positions. PARTIAL_NET stays distinct from "
            "UNROUTED_NET; record counts do not measure opens.",
            "boundary": "Offline content parsing does not execute DRC, establish native "
            "completion, match live hazards or prove configuration applicability. PARTIAL_NET "
            "has no independent native hazard mapping. Original unknown receipts and live "
            "read_reports freshness gates are unchanged.",
        },
    ),
    "manufacturing_verification": _object(
        [
            "suite",
            "status",
            "valid",
            "items",
            "coverage",
            "uncovered",
            "manufacturing_sign_off",
            "_untrusted",
        ],
        ["items"],
    ),
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
            "owner_ref",
            "startup_operation_id",
            "opened",
            "created",
            "path",
            "project_source_operation_id",
            "first_open_binding_sha256",
            "first_open",
            "extensions",
            "_untrusted",
        ],
        ["project", "prompts"],
        optional_fields=[
            "owner_ref",
            "startup_operation_id",
            "opened",
            "created",
            "path",
            "project_source_operation_id",
            "first_open_binding_sha256",
            "first_open",
            "extensions",
        ],
        closed_copy_startup={
            "path": "extensions.closed_copy_startup",
            "fields": ["token", "proof"],
            "boundary": "Present only after successful final startup publication. New-protocol "
            "preflight requires this token for the close diagnostic even if Open leaves bytes "
            "unchanged. Proof covers only pre-/post-Open .prj/.pcb observations, not open/locked "
            "icdb or complete-project integrity. Original genesis remains provenance; no "
            "persistence or Save/Close unknown outcome is promoted.",
        },
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
            "creation_method",
            "persistence_verified",
            "verification",
            "_untrusted",
        ],
        ["project", "path", "template", "rewritten", "parts_databases"],
        optional_fields=["creation_method", "persistence_verified", "verification"],
        closed_copy_fields=[
            "closed_copy",
            "file_copy_verified",
            "native_calls_dispatched",
            "native_open_verified",
        ],
        closed_copy_shape=[
            "plan",
            "genesis",
            "registration",
            "source_pins",
            "written_files",
            "exact_differences",
            "actual_target_manifest",
            "target_verification",
        ],
        closed_copy_verification_shape=[
            "valid",
            "complete",
            "source",
            "genesis",
            "target",
            "actual_target_manifest",
            "actual_target_files",
            "native_open_verified",
        ],
        closed_copy_boundary="Explicit --closed-copy requires a complete --source-backup ZIP "
        "and matching readable initialized template tree, including icdb and source-local "
        "public library references. Every member name, CRC/SHA and full source set is checked; "
        "stock setup templates, external library dependencies and nonempty/non-ASCII/overlapping "
        "targets refuse. Copy every member including Work/logs/backups into the new directory; "
        "only existing project_file.relocate_library_references may rewrite public PRJ library "
        "keys/lists. Preserve the source project filename and all other bytes. Preview binds "
        "source/archive pins, complete expected genesis and exact allowed file changes. "
        "Confirmation rechecks before exclusive creation, and independent verification reads "
        "the entire target file set/hashes. No Designer/Layout/Library/Open/Save/Close is called. "
        "Complete byte-copy persistence is not native openability or engineering acceptance; "
        "partial files are retained without cleanup/replay. "
        "Native default creation stays separate.",
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
            "preflight",
            "_untrusted",
        ],
        ["project", "path", "central_library", "designs", "board", "preflight.requirements"],
    ),
    "library_build": _object(
        [
            "project",
            "library",
            "partition",
            "steps",
            "failed",
            "pdb_registered",
            "symbols_written",
            "symbols_registered",
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
            "source",
            "imported",
            "parts",
            "symbols",
            "cells",
            "padstacks",
            "summary",
            "partition",
            "units",
            "steps",
            "failed",
            "pdb_registered",
            "symbols_written",
            "symbols_registered",
            "cells_registered",
            "cells_missing",
            "verification",
            "ok",
            "hint",
            "_untrusted",
        ],
        [
            "project",
            "library",
            "source",
            "parts",
            "symbols",
            "cells",
            "padstacks",
            "units",
            "steps",
            "verification",
        ],
        summary_shape=["parts", "symbols", "cells", "padstacks"],
    ),
    "project_backup": _object(
        [
            "project",
            "backup",
            "files",
            "bytes",
            "compressed",
            "skipped",
            "complete",
            "closed_and_reopened",
            "central_library",
            "release",
            "note",
            "_untrusted",
        ],
        ["project", "backup", "skipped", "central_library"],
        optional_fields=["note", "release"],
        release_preview_boundary=(
            "The public preview performs a read-only whole-project release preflight. "
            "Library/Designer/Layout process generations, formal application identity and "
            "document ownership are bound in the SDK snapshot and handler dependencies. "
            "Unknown native writers refuse before confirmation; changed identities refuse "
            "before saving/closing. Apply revalidates the retained reference chain. "
            "Identified outside documents are never saved, closed or quieted. Save/Close "
            "facts survive later release failure; byte hashes are not an atomic snapshot."
        ),
        release_preflight_shape=[
            "project",
            "board",
            "library_state",
            "release_preflight",
            "read_only",
            "save_count",
            "close_count",
        ],
    ),
    "project_restore": _object(
        [
            "project",
            "backup",
            "safety_backup",
            "written",
            "removed",
            "failed",
            "complete",
            "released",
            "reopened",
            "ok",
            "_untrusted",
        ],
        ["project", "backup", "safety_backup", "failed", "released"],
    ),
    "library_list": _object(
        [
            "project",
            "library",
            "kind",
            "partitions",
            "total",
            "items",
            "count",
            "offset",
            "next_offset",
            "has_more",
            "failed",
            "_untrusted",
        ],
        ["project", "library", "items", "partitions", "failed"],
        parts_shape=[
            "number",
            "partition",
            "description",
            "prefix",
            "type",
            "cell",
            "symbols",
            "pins",
            "value",
        ],
        cells_shape=[
            "name",
            "partition",
            "kind",
            "group",
            "mount",
            "description",
            "pins",
            "pads",
            "pin_numbers",
            "holes",
            "height",
        ],
        symbols_shape=["name", "partition", "reference", "kind", "version", "pins"],
        padstacks_shape=["name", "type", "shape", "width", "height", "drill", "plated"],
    ),
    "library_show": _object(
        [
            "project",
            "library",
            "part",
            "also_in",
            "symbols",
            "cell",
            "padstacks",
            "findings",
            "used_by",
            "_untrusted",
        ],
        ["project", "library", "part", "symbols", "cell", "padstacks", "findings", "used_by"],
        optional_fields=["part", "also_in", "symbols", "findings", "used_by"],
        part_shape=[
            "number",
            "partition",
            "name",
            "label",
            "description",
            "prefix",
            "cell",
            "type",
            "properties",
            "symbols",
            "slots",
            "pins",
        ],
        cell_shape=[
            "name",
            "partition",
            "kind",
            "group",
            "mount",
            "layers",
            "description",
            "height",
            "pins",
            "holes",
            "texts",
            "placement",
            "assembly",
            "silkscreen",
        ],
    ),
    "library_check": _object(
        [
            "project",
            "library",
            "parts",
            "cells",
            "symbols",
            "counts",
            "clean",
            "registration",
            "validation_scope",
            "items",
            "count",
            "offset",
            "next_offset",
            "has_more",
            "failed",
            "_untrusted",
        ],
        ["project", "library", "items", "failed"],
        items_shape=["severity", "rule", "item", "message"],
    ),
    "library_add": _object(
        [
            "project",
            "library",
            "partition",
            "steps",
            "failed",
            "pdb_registered",
            "symbols_written",
            "symbols_registered",
            "cells_registered",
            "cells_missing",
            "ok",
            "hint",
            "verification",
            "_untrusted",
        ],
        ["project", "library", "steps", "verification"],
        verification_shape=["parts", "missing", "findings", "verified"],
    ),
    "library_render": _object(
        ["pictures", "count", "_untrusted"],
        ["pictures"],
        pictures_shape=[
            "part",
            "path",
            "width",
            "height",
            "symbol_pins",
            "lands",
            "pin_numbers",
            "holes",
            "extent_mm",
        ],
    ),
    "schematic_render": _object(
        ["design", "pictures", "issues", "drawing_review", "summary", "_untrusted"],
        ["design", "pictures", "issues", "drawing_review"],
        drawing_review="Existing DS checks use planned projections and estimated character "
        "widths, not native font extents. Read drawing_review's checked_rules, per-object "
        "unmodelled symbol records and unreviewed scope before generating an image; empty "
        "findings do not mean the actual drawing is clear.",
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
        **_SCHEMATIC_DRAWING_REVIEW_SCHEMA,
        **_SCHEMATIC_SPACING_SCHEMA,
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
            "drawing_review",
            "_untrusted",
        ],
        ["warnings", "netlist", "symbols_written", "project", "summary", "drawing_review"],
        drawing_review="The same planned projection coverage is returned before and after "
        "drawing; it does not establish actual native readability, ERC or persistence.",
        **_SCHEMATIC_DRAWING_REVIEW_SCHEMA,
        **_SCHEMATIC_SPACING_SCHEMA,
    ),
    "schematic_edit": _object(
        [
            "project",
            "applied",
            "saved",
            "save_acknowledged",
            "lifecycle",
            "read_back_after_reopen",
            "save_metadata",
            "metadata_changes",
            "verification",
            "observed",
            "read_back",
            "persistence_verified",
            "drawing_review",
            "_untrusted",
        ],
        [
            "project",
            "applied",
            "observed",
            "read_back.source",
            "drawing_review",
            "lifecycle",
            "read_back_after_reopen",
            "save_metadata",
            "metadata_changes",
        ],
        observation="observed is the post-apply native snapshot, not the projected design or "
        "the apply report; read_back binds its SHA-256 and retains available source identity",
        verification_scope="requested postconditions; display-only set_property additionally "
        "protects all observed engineering records before save and on fresh readback. "
        "Display-only batches then normally close/reopen once; persistence requires actual "
        "lifecycle counts, fresh matching public identity and independent post-reopen "
        "display/protection comparison. SaveAll acknowledgement alone cannot prove persistence; "
        "unknown application/adapter versions remain null",
        display_save_metadata="The preview explicitly binds at most one uniquely observed "
        "pinless unnamed native border owner by UID/sheet, @NAME/@Sheet/@SHEETTOTAL markers "
        "and the original @DATETIME value. The evidence observed an edit-plus-save bundle; "
        "the individual phase was not isolated. The bound owner's valid DD/MM/YYYY:HH:MM "
        "value may change during edit/save/reopen, reported separately "
        "as metadata_changes with raw before/after values. Other fields, owners, identities "
        "and geometry remain protected; missing/ambiguous roles refuse before writes. "
        "Without any observed @DATETIME the policy is empty and comparison stays strict.",
        display_save_metadata_shape=[
            "owner",
            "field",
            "before",
            "role_markers",
            "phase_scope",
            "phase_evidence",
        ],
        display_metadata_changes_shape=["owner", "field", "before", "after", "source"],
        display_lifecycle_shape=[
            "source",
            "save_count",
            "close_count",
            "reopen_count",
            "save_acknowledged",
            "closed_observed",
            "close_returned",
            "before_identity",
            "before_pid",
            "reopened_identity",
            "reopened_pid",
        ],
        drawing_review="Native edit previews report affected objects and unchecked rules. "
        "Limited named attribute_display records retain actual getter results and failures; "
        "they do not include exact text envelopes, font family, symbol graphics, wire paths or "
        "actual usable boundaries. No visual collision or orientation pass is inferred.",
        **_SCHEMATIC_DRAWING_REVIEW_SCHEMA,
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
            "findings",
            "designer_verification",
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
        schematic_parts_item_extensions=[
            "attribute_display",
            "attribute_display_coverage",
            "pin_observation_coverage",
            "identity_observation_coverage",
        ],
        attribute_display_shape=["name", "value", "owner", "display", "getter_errors"],
        attribute_display_owner_shape=["refdes", "uid", "sheet"],
        attribute_display_getters=["x", "y", "visible", "size", "orientation", "origin", "units"],
        attribute_display_coverage="At most 256 instance attributes enumerated; display getters "
        "are limited to Name, Value, Part Label, Part Name, Part Number and Ref Designator "
        "(case/spacing aliases). GetLocation, Visible, Size, Orientation and Origin are actual "
        "native getters; missing/error fields are omitted and getter_errors retained. "
        "Coordinates/Size use sheet_unit (10 mil). Other free text and exact font envelopes "
        "are not observed. Enumeration completeness does not imply getter completeness.",
    ),
    "schematic_show": _object(
        [
            "sheet",
            "sheet_size",
            "foreground",
            "foreground_request_return",
            "window",
            "screenshot",
            "capture_context",
            "drawing_review",
            "_untrusted",
        ],
        ["window", "screenshot", "capture_context", "drawing_review"],
        capture_context_shape=[
            "project",
            "sheet",
            "pid",
            "executable",
            "application_hwnd",
            "active_view_hwnd",
            "window_hwnd",
            "same_root_verified",
            "foreground_verified",
        ],
        screenshot_shape=[
            "path",
            "width",
            "height",
            "bytes",
            "window_hwnd",
            "window_pid",
            "bounds_px",
            "coordinate_space",
            "bounds_verified",
        ],
        capture_verification_scope="With --output, the receipt independently reopens the "
        "declared PNG, checks its hash/decoding/dimensions and binds the requested project, "
        "sheet, process, executable, ActiveView and actual window-root relationship. "
        "capture_context is native source evidence, not by itself independent acceptance.",
        without_output="The view may activate and fit a sheet, but without --output it has "
        "no independently accepted PNG delivery; verification remains incomplete.",
        foreground_observation="foreground_request_return is the immediate request result; "
        "settled fresh foreground/root/PID/project/sheet observations decide capture acceptance.",
        viewport_failure="A full physical frame must fit active monitors. Failure details "
        "retain physical bounds, monitor rectangles and uncovered areas (at most 64), in "
        "physical_pixels. Adjust the window through the authorized GUI; no crop or automatic "
        "window resize weakens this requirement. No particular native failure cause is inferred.",
        capture_not_checked=[
            "font correctness and visual readability",
            "schematic intent and connectivity",
            "native ERC",
            "complete usable drawing boundary or manufacturing acceptance",
        ],
        **_SCHEMATIC_DRAWING_REVIEW_SCHEMA,
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
            "drawing_review",
            "_untrusted",
        ],
        ["path", "sheets", "messages", "drawing_review"],
        **_SCHEMATIC_DRAWING_REVIEW_SCHEMA,
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
            "log_status",
            "stages",
            "native_log",
            "plan_fingerprint",
            "definitions_after",
            "after_command",
            "applied",
            "no_change",
            "change_status",
            "persistence_state",
            "persistence_verified",
            "verification",
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
            "state_guard",
            "state_digest",
            "labels",
            "selected_refdes",
            "protected_refdes",
            "persistence_verified",
            "geometry_after",
            "post_save_error",
            "moved",
            "resized",
            "unplaced",
            "failures",
            "applied",
            "saved",
            "write_attempted",
            "read_back",
            "verification",
            "prompts",
            "_untrusted",
            "label_moves",
            "stages",
            "state_after",
            "state_reopened",
            "post_write_error",
            "reminders_before",
            "reminders_projected",
            "not_checked",
            "native_calls_returned",
            "reopen_evidence",
            "change_status",
            "persistence_state",
        ],
        ["pcb", "labels", "failures", "read_back", "verification", "prompts"],
        verification_shape=["valid", "scope", "persistence_verified", "issues"],
        bounded_native_call_shape=[
            "call",
            "sequence",
            "dispatched",
            "returned",
            "return_kind",
            "text_uid?",
            "target_mm?",
            "unit?",
            "active_document_none?",
        ],
        bounded_reopen_evidence_shape=[
            "identity",
            "board",
            "authenticated",
            "observed_after_open_sequence",
            "state_sha256",
        ],
        note="Explicit --file translations use explicit_silk_translation_v2 and require exact "
        "native text UID, owner, Type=2, TextType=1, actual Side/SideName, raw Layer/LayerName, "
        "mm from/to and "
        "native envelopes/vector strokes. Position-only Move is followed by full protected "
        "readback before any Save/Close/Open; missing reads stop persistence. Fresh reopened "
        "geometry is independently checked. Legacy automatic/resize plans cannot apply. "
        "Receipt separately requires one returned Move per target, then one Save/Close/Open, "
        "actual empty-document closure and fresh authenticated reopened identity/state. "
        "Formal151.0 Layout Save and Close require BOOL=True; None, False or an exception "
        "stops dependent Close/Open. Later matching geometry cannot replace missing call "
        "acknowledgement; completed movement, save and readback facts remain separate. "
        "Target deltas require 0.0001 mm representability. Only selected text inventory "
        "envelopes compare by independently rounding predicted and observed extrema to "
        "four decimal millimetres, matching the existing model precision. This is an "
        "explicit quantized observation, not an epsilon or global geometric tolerance. "
        "Raw before/after extrema and stroke-array witnesses remain in receipts and must "
        "independently reproduce their observed envelopes/strokes. Positions, formats, "
        "identity, strokes at existing reader precision, all non-target fields including "
        "raw witnesses and complete physical geometry remain exact. "
        "Projection reminders retain unsupported pin1/mask/font checks; they are not DRC. "
        "Known saved facts survive later failure.",
        selected_envelope_comparison={
            "scope": "selected text inventory extents only",
            "unit": "mm",
            "decimal_places": 4,
            "method": "round predicted and observed extrema independently, then exact equality",
            "raw_before_after_witnesses_retained": True,
            "actual_witness_self_consistency_required": True,
            "epsilon": None,
            "non_target_fields_quantized": False,
            "full_geometry_comparison_changed": False,
        },
        fabrication_identity_boundary="Formal151.0 FabricationLayerText.Layer48/help14548 "
        "and LayerName49/help14549 describe soldermask's associated stackup layer. "
        "Silk2/assembly1 identity instead uses Type32, actual Side30 and SideName31. "
        "The observed RC values Layer=-99 and LayerName='' remain raw successful reads, "
        "not a conductor-layer number or an established negative-sentinel meaning. "
        "File side_name binds the actual Top/Bottom name; missing/failed getters still refuse.",
    ),
    "pcb_route": _object(
        [
            "pcb",
            "passes",
            "nets",
            "layers",
            "unroute",
            "before",
            "unrouted_before",
            "runs",
            "planes_regenerated",
            "after",
            "unrouted",
            "complete",
            "persistence_verified",
            "geometry_after",
            "post_save_error",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
            "mode",
            "routepass",
            "general_route",
            "selected_fromto_evidence",
            "state_before_command",
            "configuration_before_command",
            "configuration_after_command",
            "fromto_inventory_before_command",
            "selection_before_command",
            "execution_budget_seconds",
            "phase_timings",
            "post_close_identity",
            "ui_write_attempted",
            "ui_write_acknowledged",
            "selection_write_attempted",
            "engineering_write_dispatched",
            "desktop_lock_checks",
            "configuration_after_ui",
            "checkpoint_evidence",
            "observable_state_guard_evidence",
            "selected_net_evidence",
            "state_before_go",
            "pre_go_input_observation",
            "state_before_save",
            "save_gate_checkpoint_evidence",
            "post_route_input_observation",
            "post_route_input_observation_error",
            "dependency_change_evidence",
            "pre_save_input_observation",
            "state_guard",
            "state_digest",
            "write_attempted",
            "change_status",
            "persistence_state",
            "stages",
            "native_calls_returned",
            "verification",
            "state_after",
            "state_reopened",
            "reopen_evidence",
            "post_write_error",
            "limits",
            "reminders_before",
            "reminders_after",
            "route_projection_boundary",
        ],
        [
            "pcb",
            "unrouted_before",
            "unrouted",
            "runs",
            "prompts",
            "routepass",
            "verification",
            "state_after",
            "state_reopened",
            "reopen_evidence",
            "checkpoint_evidence",
            "observable_state_guard_evidence",
            "selected_net_evidence",
            "state_before_go",
            "pre_go_input_observation",
            "state_before_save",
            "save_gate_checkpoint_evidence",
            "post_route_input_observation",
            "post_route_input_observation_error",
            "dependency_change_evidence",
            "pre_save_input_observation",
            "native_calls_returned",
            "post_write_error",
            "reminders_before",
            "reminders_after",
            "route_projection_boundary",
        ],
        required=["pcb"],
        native_routepass_required_fields=[
            "pcb",
            "mode",
            "routepass",
            "state_guard",
            "state_digest",
            "write_attempted",
            "applied",
            "saved",
            "persistence_verified",
            "change_status",
            "persistence_state",
            "stages",
            "native_calls_returned",
            "verification",
            "state_after",
            "state_reopened",
            "reopen_evidence",
            "checkpoint_evidence",
            "observable_state_guard_evidence",
            "selected_net_evidence",
            "state_before_go",
            "pre_go_input_observation",
            "state_before_save",
            "save_gate_checkpoint_evidence",
            "post_route_input_observation",
            "dependency_change_evidence",
            "pre_save_input_observation",
            "reminders_before",
            "route_projection_boundary",
            "limits",
        ],
        native_routepass_required_field_scope="Successful bounded-native apply results only; "
        "previews and retained partial/error native_result have their actual completed-stage "
        "fields. Legacy auto is disabled and never fabricates runs/unrouted fields. "
        "The separate receipt verifier independently checks required evidence and contents.",
        native_routepass_shape=[
            "guard",
            "before",
            "request",
            "target",
            "endpoints",
            "connection_contract",
            "material_verification_mode",
            "configuration",
            "input_files",
            "identity",
            "allowed_output_files",
            "expected_connectivity",
            "unit",
            "native_current_unit",
            "can_apply",
            "allowed_changes",
            "forbidden_changes",
            "limits",
            "fingerprint",
        ],
        native_routepass_request_shape=["net", "layer", "effort?", "continuation?"],
        bounded_general_guard="native-selected-general-route-material-v1",
        bounded_general_native_dispatch_supported=False,
        bounded_general_selector_boundary="The formal151.0 ProcessCommand signature does not "
        "establish vCommand=35367 as a supported selector. Two retained calls returned False. "
        "Public preview is material-scope diagnosis only, can_apply=false; confirmation and "
        "direct adapter apply refuse before attach/selection/command/persistence. The nested "
        "material binding can_apply is scope eligibility only and cannot authorize dispatch. "
        "Do not guess another numeric, symbolic-name or menu-path selector. A separately "
        "registered normal-GUI experiment is not CLI autorouting acceptance.",
        bounded_general_plan_schema=general_route_input_schema(),
        bounded_general_required_result_fields=[
            "pcb",
            "mode",
            "general_route",
            "state_guard",
            "state_digest",
            "write_attempted",
            "applied",
            "saved",
            "persistence_verified",
            "change_status",
            "persistence_state",
            "native_calls_returned",
            "stages",
            "state_before_command",
            "phase_timings",
            "execution_budget_seconds",
            "post_close_identity",
            "configuration_before_command",
            "configuration_after_command",
            "selected_fromto_evidence",
            "selection_before_command",
            "fromto_inventory_before_command",
            "state_after",
            "post_route_input_observation",
            "dependency_change_evidence",
            "verification",
            "state_before_save",
            "pre_save_input_observation",
            "state_reopened",
            "reopen_evidence",
        ],
        bounded_general_required_result_scope="Successful bounded-general apply only; failed "
        "phases retain completed actual facts without inventing later reads or persistence.",
        bounded_general_execution_budget_seconds=1800,
        bounded_general_material_read_budget_boundary="Only bounded-general apply uses an "
        "explicit1800-second native subprocess lifecycle bound. Preview and independent "
        "attached GND observations remain600seconds. Initial, post-command and reopened "
        "material are independently fully recomputed. The two pre-command and one "
        "pre-Save gates reread all actual raw inputs and reuse an existing verified mask "
        "matrix only when every raw design/material input matches exactly; missing/drifted "
        "inputs reject, with no permissive fallback. Each phase and native call retains "
        "actual elapsed seconds; timing is not material or completion proof.",
        bounded_general_verification_shape=[
            "valid",
            "protection_valid",
            "completion_valid",
            "whole_net_complete",
            "checks",
            "material_components",
            "created_objects",
            "deleted_objects",
            "changed_old_objects",
            "raw_affected_copper",
            "native_mask_evidence",
            "native_connectivity",
            "drc_executed",
        ],
        bounded_general_plan_result_shape=[
            "guard",
            "request",
            "before",
            "target",
            "components_before",
            "expected_pin_groups",
            "selected_fromto",
            "fromto_inventory",
            "fromto_inventory_sha256",
            "configuration",
            "allowed_old_target_copper",
            "input_files",
            "identity",
            "allowed_output_files",
            "can_apply",
            "unit",
            "command",
            "execution_budget_seconds",
            "limitations",
            "fingerprint",
        ],
        bounded_general_fromto_shape=[
            "net",
            "net_uid",
            "from_object",
            "to_object",
            "nearest_from_pin",
            "nearest_to_pin",
            "from_mm",
            "to_mm",
            "unit_enum",
            "from_layer",
            "to_layer",
            "selected",
            "identity_sha256",
        ],
        bounded_general_stages=[
            "selection",
            "route",
            "readback",
            "protection",
            "save",
            "close",
            "reopen",
        ],
        bounded_general_plan_shape=[
            "version",
            "net",
            "layer",
            "endpoint_pin_uids",
            "allow_replace_trace_uids",
            "bounds_mm",
            "expected_components_after",
            "select_active_layer?",
        ],
        bounded_general_plan_scope="Explicit --mode bounded-general --file PLAN, version1, "
        "exact six-pin GND only. Freeze one actual unconnected FromTo between two different "
        "material groups; an explicit after=2 merges only the selected pair from three "
        "groups, while explicit after=1 requires the full six-pin target net in one group. "
        "Selected endpoint pin "
        "UIDs only identify the requested candidate; raw false FromTo endpoints, nearest-pin "
        "true observations, coordinates MM4, layers, net and complete inventory are bound "
        "independently. No persistent FromTo UID is invented.",
        bounded_general_dispatch=[
            "Document.UnSelectAll void",
            "one actual FromTo.Selected BOOL PUT",
            "complete selected FromTo inventory",
            "last complete selected Nets/Components/Pins/Traces/Vias inventory",
            "Gui.ProcessCommand(35367,True) BOOL=True",
        ],
        bounded_general_configuration_boundary="Supported actual EditorControl/Gui "
        "GET observations are frozen; no EditorControl or constraint settings PUT. Actual "
        "Gui.ActiveMode PROPERTYGET17 must be Route1 for this finite tested path; missing, "
        "non-Route or changed mode refuses before routing. Select the normal Route working "
        "mode in the actual Layout GUI first; this path does not set it. Route1 is a tested "
        "prerequisite, not a guarantee that General can start or solve the task. Active "
        "layer, enabled layer lists, "
        "RouteMaxVias, net/via rules, push/shove and interactive DRC are "
        "observations, not a solver guarantee. AllowAdditionalVia concerns one extra via per "
        "SMD pin, not a no-via certificate. Any actual via, wrong layer, out-of-bounds copper, "
        "foreign or unpermitted old-object change stops Save/Close/Open. Official151.0 "
        "Deprecated RouteOptions1 and Routes21 are not invoked and remain explicit "
        "not_checked; engine_configuration_complete=false. Each stable read_log record "
        "contains field/interface/DISPID/arguments/returned/value; failed_field and "
        "completed_fields preserve partial reads. Initial configuration is sampled before "
        "the expensive material masks; failure reports state=null for this attempt.",
        bounded_general_ui_layer_selection_boundary="Optional select_active_layer is a strict "
        "boolean, defaultfalse. Without it the actual active route layer must already match. "
        "With true the preview binds current to target Gui.ActiveRouteLayer and one formal "
        "supported PROPERTYPUT16 on the retained authenticated instance; all other readable "
        "configuration stays exact, followed by independent GET confirmation. This is normal "
        "UI layer selection, not Constraints.Value PUT; UI preferences may persist and are "
        "not automatically restored. UI/selection/engineering dispatch facts are separate. "
        "desktop_locked is checked immediately before every UI, command, Save, Close and "
        "Open; a locked desktop stops dependent actions without bypass or replay.",
        bounded_general_selection_boundary="Document selected-only Nets23(1,True,'*'), "
        "Components22(1,-1,4,'*'), Pins26(1), Traces27(1,0), Vias28(1) and FromTos29(1,1) "
        "are read through formal PROPERTYGET DISPIDs after the potentially long material "
        "read and immediately before the command. One exact selected FromTo and no selected "
        "component/pin/trace/via are required; selected-net observations may contain only "
        "the same actual GND UID. Count/item/identity failures or other selection reject "
        "before ProcessCommand. Highlight is not used as Selected evidence.",
        bounded_general_target_change_boundary="Only explicitly listed old GND trace UIDs "
        "may be replaced/deleted/changed within the finite full-material rectangle. All "
        "unlisted old copper, other networks and all nonrouting state stay exactly protected. "
        "All six pins and all old/new remaining GND copper enter the actual material graph. "
        "Each task accepts exactly the planned material-group merge, with native opens only "
        "a crosscheck. A two-group task is saved partial progress, not whole-net completion. "
        "Independent fresh material/input checks and strict BOOL Save/Close plus exact "
        "reopen gate persistence; no NoViaPass.Go, cleanup, fanout, global setting change, "
        "constraint PUT or plane regeneration.",
        native_routepass_effort_values=[1, 2],
        native_routepass_effort_boundary="Default1 preserves the original request shape. Explicit2 "
        "binds PassType(6,2,2,false,false) to a new preview and confirmation; no automatic "
        "retry or pending-operation waiver is allowed.",
        native_routepass_guard="native-routepass-two-three-or-six-pad-no-via-material-graph-v8",
        native_routepass_supported_member_counts=[2, 3, 6],
        native_routepass_six_pin_scope={
            "net": "GND",
            "physical_members": ["J1.2", "J2.2", "J3.2", "J4.2", "C1.2", "C2.2"],
            "old_target_copper": False,
            "initial_opens": 5,
            "pad_bridging": "allowed only through the same independently proven continuous "
            "post-drill/post-cutout pad material region; pin UID is not a bridge",
        },
        native_routepass_continuation_scope={
            "public_flag": "--continue-existing",
            "request_value": "existing-components",
            "net": "GND",
            "physical_members": ["J1.2", "J2.2", "J3.2", "J4.2", "C1.2", "C2.2"],
            "old_target_trace_count": 3,
            "old_board_trace_count": 11,
            "material_components_before": 3,
            "initial_opens": 2,
            "single_signal_layer": True,
            "via_and_plane_allowed": False,
            "old_trace_identity_and_full_raw_material_preserved": True,
            "default_old_target_copper_refused": True,
            "boundary": "The native solver has no proven old-copper preservation guarantee. "
            "After one Go all old and new target copper and all six owned pads must form the "
            "verified material component; every old board trace UID, full path, width, layer "
            "and net must remain exact. Splitting, replacing or moving old copper refuses "
            "conditional persistence even if native opens is zero. No pad bridging of "
            "separate trace components without independent pad-material connectedness proof.",
        },
        native_routepass_connection_contract_shape=[
            "member_count",
            "native_opens_before",
            "native_opens_after",
            "expected_material_components_after",
            "all_target_pins_and_all_new_traces_required",
            "physical_basis",
            "mode?",
            "expected_material_components_before?",
            "all_target_pins_and_all_existing_and_new_traces_required?",
            "old_target_trace_count?",
            "old_board_trace_count?",
            "old_target_trace_uids?",
            "old_target_routing_sha256?",
            "old_target_raw_sha256?",
            "components_before?",
            "component_membership_sha256?",
        ],
        native_routepass_input_shape=[
            "source_backup",
            "live_files",
            "live_unavailable",
            "non_rule_observed_files",
            "live_file_count",
            "non_rule_exclusion_policy",
            "limitations",
        ],
        native_routepass_source_backup_shape=[
            "path",
            "bytes",
            "sha256",
            "files",
            "all_files",
            "skipped",
            "crc_and_members_verified",
            "icdb_source_sha256",
            "basis",
        ],
        native_routepass_live_unavailable_shape=[
            "path",
            "live_read",
            "source_only_sha256",
        ],
        native_routepass_checkpoint_shape=[
            "phase",
            "identity",
            "status",
            "atomic_freeze_proven",
            "not_proven",
            "valid?",
            "location?",
            "location_name?",
            "error?",
        ],
        native_routepass_checkpoint_observation_phases=[
            "before_configuration",
            "immediately_before_go",
            "after_go_before_readback",
            "immediately_before_save",
        ],
        native_routepass_observable_state_guard_shape=[
            "guard",
            "atomic_database_freeze",
            "exclusive_database_lock",
            "opaque_constraint_cache_version_proven",
            "request",
            "observations",
            "operator_assumption",
        ],
        native_routepass_observable_observation_shape=[
            "phase",
            "state_field",
            "design_state_sha256",
            "dependency_identity_sha256",
            "identity",
            "material_basis",
        ],
        native_routepass_observable_material_basis=[
            "full_native_observation",
            "fresh_native_inputs_exact_mask_input_reuse",
        ],
        native_routepass_observable_phases=[
            "initial_bind",
            "immediately_before_go",
            "post_go_readback",
            "immediately_before_save",
        ],
        native_routepass_selected_net_shape=["count", "net", "net_uid"],
        native_routepass_checkpoint_observation_boundary="Actual Checkpoint getter/IsValid/"
        "Locate observations or errors are retained without using them as a database freeze. "
        "Observed Before1 is not At2. An observed value, including At2, is not complete opaque "
        "database/cache identity or an exclusive lock. The observed-v3 guard independently "
        "checks the supported state, retained owner and readable dependencies instead.",
        native_routepass_dependency_change_shape=[
            "valid",
            "changes",
            "allowed_output_files",
            "opaque_boundary",
            "reason?",
        ],
        native_routepass_input_observation_error_shape=["cause", "code", "message"],
        native_routepass_dependency_change_row_shape=[
            "path",
            "before",
            "after",
            "allowed",
            "reason",
        ],
        native_routepass_allowed_output_shape=[
            "path",
            "role",
            "reason",
            "may_create",
            "existing_report_indices?",
            "max_bytes?",
        ],
        native_routepass_live_file_shape=["path", "bytes", "sha256", "text_observation?"],
        native_routepass_text_observation_shape=["encoding", "text", "_untrusted"],
        native_routepass_allowed_output_boundary="Only exact preview-bound paths are allowed: "
        "the existing primary board, the next absent AutoRouteReportNN.txt determined from "
        "the complete existing 01..99 report-index inventory, and an already existing exact "
        "PCB/LogFiles/MVO_rules_log.txt. Roles are primary_board/native_autoroute_report/"
        "native_mvo_warning_log. The next report may be created once; MVO may only be "
        "overwritten. Report indices, prior bytes/hash and the 1MiB native-text limit are "
        "bound; binary/oversize text or absent Xpedition Layout report signature refuses. "
        "Text is untrusted observation, not executable instructions or independent copper "
        "acceptance. This is never a whole LogFiles-directory exclusion. Unexpected file "
        "changes still retain post-Go native facts/protection readback before refusing Save.",
        native_routepass_material_state_fields=[
            "all_pin_pad_material",
            "native_mask_proof",
            "native_trace_material",
        ],
        native_routepass_raw_trace_material_shape=[
            "uid",
            "net",
            "net_uid",
            "layer",
            "width_mm",
            "points_array_mm",
            "bounds_mm",
            "bounds_source",
            "geometry_source",
        ],
        native_routepass_mask_proof_shape=[
            "source",
            "unit",
            "complete",
            "target_contacts",
            "pin_materials",
            "target_pad_regions",
            "target_pad_intersections",
            "foreign_intersections",
            "raw_trace_inventory",
            "errors",
            "cleanup",
            "not_checked",
            "target_net_uid",
            "target_layer",
            "target_trace_uids",
            "trace_geometry_sha256",
            "raw_trace_sha256",
            "material_sha256",
            "budget",
            "foreign_separation_verified",
            "temporary_masks_count",
            "target_trace_intersections",
            "mask_touch_controls",
            "mask_touch_formal_source",
            "round_holes",
            "round_holes_sha256",
            "trace_materials",
        ],
        native_routepass_pad_region_shape=[
            "component_uid",
            "pin_uid",
            "material_sha256",
            "complete",
            "regions",
            "region_intersections",
            "contacts",
        ],
        native_routepass_pad_continuity_boundary=(
            "Each region index is local to this observation and source SHA, not a persistent UID. "
            "Circles/simple straight polygons require strictly contained separated "
            "supported voids; "
            "rounded rectangles additionally require supported native factory construction and "
            "bidirectional zero-area material differences. "
            "Unsupported topology remains incomplete. "
            "All region/region and region/trace comparisons are complete and budgeted separately."
        ),
        native_routepass_mask_contact_shape=[
            "component_uid",
            "pin_uid",
            "trace_uid",
            "positive_intersection_area_mm2",
            "intersection_is_empty",
            "material_area_mm2",
            "trace_ends",
            "raw_trace_sha256",
        ],
        native_routepass_material_graph_boundary="Three-/six-pin completion requires every frozen "
        "physical pin and every target trace in the one verified material "
        "graph. In explicit existing-components mode this includes all old and new target "
        "copper; the default empty-net mode is unchanged. Actual independently proven "
        "continuous post-drill/post-cutout pad regions can connect target trace branches. "
        "Disconnected pad islands remain distinct graph nodes; a shared pin UID/name or "
        "overlapping boxes cannot join them. Unsupported or unread material is incomplete. "
        "Full-width positive material intersections support mid-segment T junctions; "
        "drill/cutout space, zero-area touching, centerlines and opens=0 are insufficient. "
        "Unreadable or curved target trace material refuses. Foreign material requires "
        "strict native bounds separation or zero-area intersection plus formal151 Mask.Touch "
        "False after fixed native scratch-mask boundary controls pass. This is not a proof "
        "of applicable clearance-rule distance. Trace material also excludes every actual "
        "round drill hole. Only proven terminal round-hole crops preserve the trace-node "
        "connectedness contract; internal drilling, slots or unmatched mechanical holes refuse.",
        native_routepass_reminder_boundary="reminders_before and reminders_after contain "
        "projection warnings plus explicit coverage. Strict physical models omit the advisory "
        "collection/conversion sidecar, so that coverage remains partial. Native path is "
        "unknown before Go: no candidate trace/text projection is claimed at preview. "
        "No protected text or component is moved to satisfy reminders.",
        native_routepass_mask_cleanup_shape=[
            "owned_allocation_index",
            "delete_returned",
            "error?",
            "cause?",
        ],
        native_routepass_mask_count_shape=["before", "after"],
        native_routepass_configuration_shape=[
            "pass_type",
            "config",
            "items",
            "order",
            "net",
            "remove_layers",
            "add_layer",
            "go_count",
            "configuration_readback",
        ],
        native_routepass_verification_shape=[
            "valid",
            "protection_valid",
            "completion_valid",
            "checks",
            "created_objects",
            "physical_connection",
            "foreign_material_separation",
            "native_connectivity_verified",
            "drc_executed",
        ],
        native_routepass_physical_shape=[
            "verified",
            "status",
            "segment_count",
            "trace_object_count",
            "endpoint_witnesses",
            "basis",
            "not_checked",
            "reason?",
            "material_graph?",
            "all_target_trace_object_count?",
            "existing_target_trace_object_count?",
            "member_count?",
            "material_component_observation?",
        ],
        native_routepass_foreign_material_shape=[
            "verified",
            "basis",
            "native_mask_evidence?",
            "comparisons?",
            "not_checked?",
            "reason?",
        ],
        native_routepass_call_shape=[
            "call",
            "sequence",
            "dispatched",
            "returned",
            "return_kind",
            "arguments?",
            "item_type?",
            "net_uid?",
            "active_document_none?",
        ],
        native_routepass_stages=[
            "configuration",
            "route",
            "readback",
            "protection",
            "save",
            "close",
            "reopen",
        ],
        native_routepass_boundary=(
            "Only explicit --mode bounded-native selects the new contract. The default "
            "legacy auto mode remains refused. Without --continue-existing, exactly one "
            "entirely unrouted two-/three-pin net "
            "or limited six-pin GND, one actual signal layer and respectively one, two or five "
            "initial opens are required, with no planes "
            "or assignments. Explicit --continue-existing binds only six-pin GND with three "
            "old target traces, eleven board traces, three observed material groups and "
            "two opens; actual old trace identities, complete raw geometry and component "
            "membership are frozen. All old plus new target copper must pass the same full "
            "material graph and old objects must remain exact; native preservation is a "
            "checked postcondition, not an engine guarantee. --passes and --unroute are forbidden. "
            "The explicitly authorized complete --source-backup closed ZIP, readable live "
            "dependencies and complete supported native observations are separately bound. "
            "A locked "
            "live icdb has no current content hash and the source ZIP cannot substitute for "
            "one; complete native rule-cache versioning is not claimed. "
            "The finite material-graph-v8 guard repeats complete supported native state, "
            "raw material, "
            "identity and readable dependency hashes on the same owner immediately before Go. "
            "Actual doc.Nets enumeration and net UID checks bind the supplied native collection; "
            "no FindNetByID/FindNet double lookup is claimed. Checkpoint is observation only: "
            "observed Before1 is not formal At2 and provides no atomic freeze. Neither "
            "exclusive editing nor opaque database/cache equality is established. Post-Go "
            "full protection/material acceptance and fresh raw native pre-Save readback "
            "plus dependency checks gate saving. Explicit non-rule service logs are retained "
            "separately from protected rule/design inputs. Readable dependency changes may "
            "affect only the explicitly bound primary .pcb and exact declared native report/"
            "MVO text outputs; source, library and rule-file changes stop Save. Post-Go "
            "state/protection facts are captured even when output-file protection fails. "
            "Native NoViaPass6 is "
            "configured after PassType resets parameters with frozen native effort1 or2: "
            "Config(false,false), explicit "
            "NetsItem5 actual net collection, layer removal/addition and one Go. Go is formal "
            "void, so a returned None is expected; Save and Close require formal BOOL=True. "
            "No supported pass configuration getter exists: request/configuration and ordered "
            "returned calls record intent/dispatch, while created_objects and independently "
            "read native geometry record actual output. A complete multi-object straight "
            "trace chain must contact both real owned pad materials on the one layer for two pins; "
            "three-/six-pin networks instead require the complete "
            "all-pin/all-trace material graph, "
            "including positive-area mid-segment T junctions and no isolated new copper. "
            "Native-mask mode retains unquantized width/xyr, Pad.Extrema and Trace.Extrema "
            "observations. "
            "Original pad xyr rows go unchanged to the same-document formal MaskEngine; "
            "Python does not interpret native arcs. Actual round holes and cutouts use "
            "SUB0; terminal trace/pad AND1 must have positive material area and the chain "
            "end must anchor inside that owned outer. Foreign material additionally needs "
            "strict separation of full real bounds, or formal Mask.Touch=False plus empty "
            "zero-area intersection after native boundary controls pass. Each owned "
            "scratch Mask is individually "
            "deleted once and collection Count must return to its initial value. Derived "
            "mask allocation/proof receipts are recomputed evidence, not persistent design "
            "identity; original raw inputs remain exactly bound. This does not upgrade "
            "the historical layout-reminder eight arc-envelope omissions or establish "
            "applicable spacing-rule verification. "
            "All old routing, other networks, text, components, pins, holes, planes and "
            "frozen rule observations remain protected. Missing reads, extra changes, "
            "ambiguous replies or failed protection stop dependent persistence without "
            "replaying Go. Engineering facts and strict Save/Close/Open/fresh readback "
            "evidence remain separate. Native solver acceptance is not complete path-rule "
            "parsing, clearance verification, DRC, manufacturing or thermal/tie proof. "
            "Run pcb checks run separately on accepted geometry; that checker saves/closes "
            "before execution and must never be described as pre-save DRC."
        ),
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
            "trace",
            "change_status",
            "persistence_state",
            "persistence_verified",
            "stages",
            "native_calls_returned",
            "no_op",
            "transaction_drc_acknowledged",
            "transaction_state_unknown",
        ],
        ["pcb", "items", "warnings", "prompts"],
        trace_plan_shape=[
            "fingerprint",
            "before",
            "target",
            "request",
            "unit",
            "native_current_unit",
            "endpoints",
            "target_contract",
            "endpoint_contact_preflight",
            "input_files",
            "plane_inventory",
            "possible_affected_copper",
            "authorized_regeneration",
            "allowed_copper_changes",
            "copper_impact_proven",
            "can_apply",
            "blockers",
            "rules",
            "named_width_comparison",
            "protection",
            "evidence_boundary",
        ],
        endpoint_shape=[
            "component_uid",
            "pin_uid",
            "refdes",
            "pin",
            "net",
            "net_uid",
            "center_mm",
            "pads",
            "identity_basis",
            "physical_contact_verified",
            "holes",
            "captured_pads",
            "material_source",
        ],
        endpoint_material_shape=[
            "source",
            "unit",
            "filled",
            "complete",
            "outer",
            "cutouts",
        ],
        target_contract_shape=[
            "task",
            "before_connectivity",
            "expected_connectivity",
            "physical_scope",
            "pad_identity_basis",
        ],
        trace_comparison_shape=[
            "valid",
            "protection_valid",
            "completion_valid",
            "completion_status",
            "checks",
            "physical_contact_verified",
            "physical_connection",
            "native_connectivity_verified",
            "drc_executed",
        ],
        target_acceptance_boundary="Default trace mode's production whole-net contract requires "
        "exactly two "
        "physical pins, one open, routed=false and no existing trace. Three/six-terminal "
        "net component merging is unavailable in that default contract; relaxing counts does "
        "not enable it. Explicit component-connect mode has a separate isolated contract. "
        "This task connects two specified native pin-owned pads, "
        "not merely an arbitrary trace on a named net. Target identity, exact added trace "
        "path/layer/net/width and complete native opens=0/routed=true are checked independently "
        "from material contact. Supported contact uses observed filled circle/closed straight "
        "polygon material, complete cutouts and round drilled holes; bbox-only, negative pads, "
        "unsupported contours and missing readback remain incomplete. Native connectivity "
        "never replaces physical contact, and neither is DRC or thermal proof. Protection or "
        "completion failure stops automatic Save/Close/Open. Current production still requires "
        "the independently missing local-rule and bounded copper-impact eligibility proof.",
        stage_shape=[
            "route",
            "regeneration",
            "readback",
            "protection",
            "connection",
            "save",
            "close",
            "reopen",
        ],
        native_routing_boundary="Default production trace previews require exact already-open "
        "document/native "
        "units/net/pin-owned pads and explicit one-layer mm path/width. Current observer cannot "
        "prove actual local-rule applicability or a bounded legal generated-copper change "
        "region. can_apply=false and confirmation is refused before any native route, plane "
        "state switch or Save/Close/Open. No dangerous override or public eligibility bypass. "
        "Internal synthetic guarded-lifecycle tests do not establish native route acceptance.",
        component_connect_request_schema=_COMPONENT_CONNECT_REQUEST_SCHEMA,
        component_connect_origin_schema=_COMPONENT_ORIGIN_SCHEMA,
        component_connect_plan_shape=[
            "guard",
            "board",
            "project",
            "before",
            "request",
            "snapshot",
            "connection",
            "target",
            "identity",
            "input_files",
            "origin",
            "query_file",
            "can_apply",
            "no_op",
            "blockers",
            "native_transaction",
            "named_minimum_observations",
            "location_rule_observation?",
            "candidate",
            "allowed_output_files",
            "closed_output_files",
            "recovery_plan",
            "save_policy",
            "closed_snapshot_sampling",
            "limits",
            "fingerprint",
        ],
        component_connect_origin_shape=[
            "schema_version=1",
            "kind=isolated-component-connect",
            "origin_project",
            "origin_board",
            "source_backup",
            "target_project",
            "target_board",
            "genesis",
            "provenance",
            "continuation_receipt?",
            "copy_operation_id",
        ],
        component_connect_genesis_shape=["file_manifest", "project_sha256", "board_sha256"],
        component_connect_provenance_shape=[
            "kind=closed-backup-copy",
            "project_library_relocation",
        ],
        component_connect_rebase_shape=[
            "encoding=utf-8|utf-16",
            "from_root",
            "to_root",
            "rewritten",
        ],
        component_connect_boundary="Explicit --mode component-connect uses the existing trace "
        "leaf, one actual net, one observed signal layer, no board vias/planes and an explicit "
        "simple mm path between two exact-query conductive material groups. "
        "Anchors are actual selected pin centers or old trace first/last vertices; interior "
        "taps and intermediate vertices are unsupported. Input coordinates and width must "
        "already lie on the four-decimal-mm grid; nothing is rounded. Selected pin-owned "
        "round terminal holes require native subtraction and positive continuous annulus "
        "contact; foreign holes, cutouts and hole-only contact refuse. Query and origin "
        "files retain original bytes/path/SHA; snapshot ID, actual net UID/layer, component/node "
        "membership and complete current protection must rebind before dispatch. Missing or "
        "unsupported candidate endpoint contact, foreign-material separation or readable named "
        "minimum-width diagnostics refuse before insertion; a known below-minimum width is "
        "not made eligible by native post-checking. Native plans also sample each declared "
        "vertex's location scheme and its target net class's named minimum, typical and "
        "expansion widths in explicit mm; an unreadable point sample or an observed "
        "lower-than-minimum request refuses. Segments, clearance influence areas and child-rule "
        "precedence remain "
        "unverified, and sampling does not grant eligibility. Master observations never become "
        "full effective-rule applicability. Source genesis derives from a complete public "
        "closed-copy operation, authenticated in the original continuous journal and "
        "independently fully read back; only declared public PRJ library references may relocate. "
        "An external prep "
        "script or supplied receipt cannot grant writing. Later connections require "
        "a complete verifiable prior saved receipt from the original SDK and its exact "
        "same-Close recovery ZIP. "
        "Non-no-op requests require a new --recovery-backup outside all project/input trees. "
        "After Close=True and empty ActiveDocument, read every closed member including actual "
        "icdb bytes, exclusively create and verify that ZIP, then Open and verify material. "
        "The frozen closed_snapshot_sampling contract permits at most 17 complete "
        "read-only two-pass closed-tree observations, with 500ms only between attempts "
        "(at most 8 seconds of inter-attempt waiting; file hashing is additional). "
        "Every attempt rechecks the same application generation and empty ActiveDocument; "
        "identity drift stops immediately. Retain each exact error, elapsed time and final "
        "tree fingerprint. This does not guarantee native file-handle release. "
        "This bound applies to stabilization; archive sealing and verification retain "
        "their additional complete checks. "
        "No Save, Close, Open or copper insertion is replayed during file stabilization. "
        "A later backup that saves again is not an interchangeable continuation source. "
        "Same-component choices are a no-op without mutation eligibility or apply/Save. "
        "Native TransactionStart(1), one PutTrace(...,None,0,4), TransactionEnd(True) use formal "
        "151.0 modified-data DRC; strict BOOL=True is supplementary to exact new-trace material, "
        "two-group merge, native topology accounting and all old/foreign-object protection. "
        "The PutTrace returned UID is a pre-End observation, not a persistent identity promise. "
        "The final new UID comes from full independent differences; every old raw UID, "
        "path, width, layer and net must remain exact. "
        "Only the existing primary descriptor and the two exact, existing and pinned "
        "Layout/LayoutDB.lyt and Work/Layout_Temp/LayoutDB.lyt stores may be declared "
        "transaction copper-storage outputs; no directory wildcard or new output file. "
        "Save/Close have separately bound exact-existing Layout/Temp storage, current-user "
        "UserSettings, strictly delimited HKP view fields and existing global "
        "Global_Constants.WindowPlacement numeric fields, bounded BMP thumbnail/save log and "
        "the unique icdb closed-visibility transition. Five exact session-marker paths "
        "require authenticated absence in the current closed-source ZIP: Save preserves "
        "them, Close may naturally remove them, and Open may recreate bounded markers "
        "only from closed absence; two temporary reports must remain empty. An existing "
        "nonempty ForwardAnnotation.txt authenticated in that closed source is instead "
        "listed in protected_source_reports: a strict protected input in every phase, "
        "never an allowed output or removable marker. Its live bytes must equal source; "
        "rewrite, deletion, substitution or missing provenance refuses. This exception "
        "does not grant other paths any marker permission. The native "
        "auto-save log retains historical source bytes separately: initial live bytes "
        "must equal source or be exactly empty; Save/continuation preserve actual live "
        "bytes, Close only appends validated actual-board blocks, and Open may retain "
        "the closed log or reset it to exactly empty. Other rewrites refuse. No lock "
        "or marker is deleted by the tool. At Open only, six existing backups may copy "
        "their exact declared closed-source bytes: main Graphics Settings.bak from main "
        "HKP, UserSettings.bak from actual current-user UPF, three Work/BackupDatabase "
        "stores from their matching Layout stores, and Work/JobPrefsDB.bak from Layout "
        "JobPrefsDB.jpf. Every source must remain unchanged after Open. PCBLogFile may "
        "remain closed-unchanged or restore its exact initial frozen live text; retain "
        "the full closed Save log. These are byte-copy/reset proofs, not private DB "
        "semantics; other Work/Config changes refuse. Every other file remains protected. "
        "server journals remain separately inventoried under the disclosed exclusion policy. "
        "Locked live icdb bytes remain unknown/source-only until the distinct closed read. "
        "Opaque storage changes do not establish complete private rule/cache semantics. "
        "Undeclared working or rule-file changes stop persistence; names do not make them "
        "harmless. Save/Close require actual BOOL=True, observed empty "
        "document and exact authenticated reopen with complete material/protection equality. "
        "Failure retains stages, reply/UID facts and unknown outcomes without automatic "
        "rollback, extra Save, cleanup or native write replay. The contract and synthetic "
        "controls do not establish a passed native write, full effective-rule parser, "
        "production routing or manufacturing.",
        component_connect_full_effective_rule_parser_proven=False,
        component_connect_persistence_shape=[
            "saved",
            "persistence_verified",
            "stages",
            "native_calls_returned",
            "state_before_save",
            "after_save_inputs",
            "closed_tree",
            "closed_snapshot_sampling",
            "closed_inputs",
            "closed_dependency_changes",
            "recovery_archive",
            "save_to_closed_descriptor",
            "closed_to_reopen_descriptor",
            "state_reopened",
            "reopened_inputs",
            "full_file_diffs",
        ],
        component_connect_persistence_field_scope="Successful isolated component persistence "
        "only. Retained partial/error results have only their actual completed-stage evidence. "
        "saved=True is a returned Save fact; closed bytes, recovery ZIP and independent reopen "
        "are separately verified and cannot be inferred from it.",
        component_connect_transaction_shape=[
            "component_transaction_entered",
            "component_transaction_dispatched",
            "counters",
            "stages",
            "native_calls_returned",
            "net_uid",
            "trace_uid",
            "put_trace_returned_uid_before_end",
            "actual_created_trace_uids",
            "uid_known",
            "native_trace_returned",
            "transaction_state",
            "transaction_state_unknown",
            "transaction_drc_acknowledged",
            "transaction_source",
            "transaction_failure?",
        ],
        blocker_shape=["code", "required_evidence"],
        rule_observation_shape=[
            "scheme",
            "active_scheme_verified",
            "local_applicability_verified",
            "net_classes",
            "net_class_observation",
            "clearances",
            "not_checked",
            "location_scheme_observation",
        ],
        named_width_comparison_shape=[
            "status",
            "source_member",
            "source_dispid",
            "source_invoke_kind",
            "source_signature",
            "scheme",
            "net",
            "net_uid",
            "class_name",
            "class_observation_indices",
            "membership_complete",
            "membership_observation",
            "class_identity_basis",
            "layer",
            "unit",
            "native_unit_argument",
            "requested_width_mm",
            "observed_minimum_width_mm",
            "delta_mm",
            "local_applicability_verified",
            "scope",
            "reasons",
        ],
        named_width_comparison_boundary="The preview compares the requested mm width with the "
        "uniquely observed named Master class/layer minimum and preserves its source and "
        "signed delta. Below/equal/above_observed_named_minimum refer only to that observation; "
        "class/member enumeration and names require complete actual reads; failed Nets, "
        "Count, Item or Name getters remain located failures, never observed empty sets. "
        "Only a completely read Count=0 establishes observed empty membership. "
        "Missing, incomplete, ambiguous, invalid or non-finite data produce unknown with reasons. "
        "None establishes current effective local compliance or enables can_apply.",
        net_class_observation_shape=[
            "complete",
            "enumeration_complete",
            "membership_complete",
            "collection",
            "failures",
            "index_basis",
            "member_budget",
            "member_attempted_count",
            "member_budget_remaining",
        ],
        net_class_collection_shape=[
            "complete",
            "count_before",
            "count_after",
            "observed_count",
            "attempted_count",
            "index_base",
            "limit",
            "failures",
        ],
        net_class_membership_shape=[
            "complete",
            "collection",
            "named_member_count",
            "named_members",
            "failures",
        ],
        net_class_read_failure_shape=[
            "location",
            "type",
            "message",
            "code?",
            "details?",
            "hresult?",
            "native?",
        ],
        location_scheme_observation_shape=[
            "source",
            "signature",
            "unit",
            "native_unit_argument",
            "to_layer_argument",
            "layer",
            "point_limit",
            "points",
            "unreviewed",
            "observation_complete",
            "path_rule_applicability_proven",
            "influence_region_rule_applicability_proven",
            "scope",
            "missing_dependencies",
            "boundary",
        ],
        location_scheme_point_shape=["point_index", "point_mm", "scheme", "status", "reason"],
    ),
    "pcb_stitch": _object(
        ["net", "width", "vias", "items", "without_room", "path", "_untrusted"],
        ["items", "without_room"],
    ),
    "pcb_unroute": _object(
        [
            "protection",
            "remaining_objects",
            "unroute",
            "state_after",
            "geometry_after",
            "removed_objects",
            "no_change",
            "change_status",
            "persistence_state",
            "persistence_verified",
            "stages",
            "delete_attempts",
            "deletion_errors",
            "post_write_error",
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
        [
            "pcb",
            "prompts",
            "targets",
            "items",
            "skipped",
            "unroute",
            "state_after",
            "geometry_after",
        ],
        items_shape=["target", "ok", "traces", "vias", "deleted", "error"],
        summary_shape=["total", "succeeded", "failed"],
    ),
    "pcb_pour": _object(
        [
            "layout_reminders",
            "copper",
            "created_shape_uid",
            "state_before_save",
            "state_after",
            "geometry_after",
            "reopen_comparison",
            "protection",
            "change_status",
            "persistence_state",
            "persistence_verified",
            "stages",
            "failure_observation_error",
            "post_write_error",
            "pcb",
            "net",
            "layer",
            "layers",
            "margin",
            "rectangle",
            "existing",
            "replace",
            "remove",
            "removed",
            "shapes",
            "generated",
            "route_unobstructed",
            "applied",
            "saved",
            "prompts",
            "_untrusted",
        ],
        [
            "pcb",
            "existing",
            "prompts",
            "net",
            "copper",
            "state_before_save",
            "state_after",
            "geometry_after",
            "reopen_comparison",
            "post_write_error",
        ],
        optional_fields=["state_before_save", "reopen_comparison"],
        copper_plan_shape=[
            "before",
            "target",
            "rectangle",
            "expected_assignment_changes",
            "generated_scope",
            "evidence_boundary",
            "not_checked",
            "fingerprint",
        ],
        copper_evidence_boundary=evidence_boundary(),
        verification_boundary="Receipt extensions.file_readback exposes evidence_boundary and "
        "not_checked even when bounded copper verification passes. Full thermal/tie conductor "
        "geometry, pad contact and connection-leg clearance are not independently proven. "
        "This limitation does not invalidate separately passed body/cutout, protection or "
        "save/reopen comparisons.",
        state_before_save_shape=["geometry", "plane_state"],
        reopen_comparison_shape=[
            "equal",
            "before_sha256",
            "after_sha256",
            "geometry_equal",
            "plane_state_equal",
            "persistence_equivalent",
            "equivalence_basis",
        ],
        persistence_witness_boundary="state_before_save is a deep copy of the complete generated "
        "observation after protection passes and before Save. state_after is the latest complete "
        "observation; it remains the pre-save observation if reopened readback fails. "
        "reopen_comparison appears only after complete reopened readback and contains SHA-256 "
        "summaries plus strict raw full-state and per-domain equality. persistence_equivalent "
        "also accepts a changed starting vertex or winding of the single bound PlaneShape's "
        "simple axis-aligned rectangle, with the same UID/net/layer and exact corners and edges. "
        "equivalence_basis is strict, target_rectangle_vertex_order, or different_or_unsupported. "
        "Both complete raw observations remain unchanged. Generated copper contours/cutouts, "
        "point witnesses, all settings and protected geometry must still match exactly. No "
        "general polygon, bounding-box-only or tolerance equivalence is applied. Missing or "
        "other different reopened evidence leaves persistence unknown. stages.reopen=returned only "
        "records that the open helper returned; active-target readback is still required.",
    ),
    "pcb_info": _object(
        [
            "pcb",
            "component_count",
            "placed_count",
            "net_count",
            "track_count",
            "via_count",
            "layer_count",
            "thickness_mm",
            "stackup",
            "net_classes",
            "net_class_observation",
            "keepout_count",
            "cell_search",
            "display",
            "clearances",
            "label_inventory",
            "silk_environment",
            "qualification",
            "can_apply",
            "observation",
            "prompts",
            "_untrusted",
        ],
        ["pcb", "stackup", "net_classes", "net_class_observation", "prompts", "observation"],
        temporary_mask_inventory_boundary="With --mask-inventory, --live-geometry and an "
        "explicit PID, read only the stable current MaskEngine collection count and exact "
        "process/board identity. No geometry capture, mask allocation/deletion, owner "
        "identification or interrupted-operation reconciliation is performed.",
        silk_environment_boundary="--silk-text-uids selects1..8 actual text UIDs with "
        "--live-geometry and an explicit --pid. It reads two complete bounded surroundings "
        "frames, raw mm contours and native rule observations without saving or editing. "
        "Geometry comparison, native input coverage, effective rule coverage, clearance/"
        "protection and final write eligibility are separate. Missing persistent identity, "
        "unsupported arcs/cutouts or an unknown applicable rule refuse qualification. "
        "The2mm detailed-measurement window is not a clearance rule; distant objects retain "
        "conservative bounds without passing unknown rules. "
        "The public silk draft remains inactive.",
        stackup_shape=[
            "board_layer",
            "kind",
            "layer",
            "name",
            "description",
            "thickness_mm",
            "usage",
            "dielectric_constant",
        ],
        net_classes_shape=["name", "observation_index", "nets", "membership_observation", "widths"],
        label_inventory_shape=[
            "unit",
            "source",
            "complete",
            "enumerated_count",
            "count_after",
            "observed_count",
            "object_limit",
            "stroke_point_limit_per_text",
            "total_stroke_point_limit",
            "texts",
            "unread_objects",
            "limitations",
        ],
        label_inventory_text_shape=[
            "observation_index",
            "uid",
            "text",
            "type",
            "text_type",
            "side",
            "side_code",
            "side_name",
            "layer",
            "layer_name",
            "fabrication_identity",
            "stackup_layer_association",
            "owner",
            "parent",
            "position",
            "format",
            "extents",
            "strokes",
            "stroke_pen",
            "native_geometry_witness",
            "fix_lock",
            "display_condition",
            "reserved",
            "reserved_by",
        ],
        label_inventory_boundary="Normal pcb inspect discovers native text UIDs and actual "
        "owner/fabrication Type/Side/SideName/position/format/envelopes for bounded labels --file. "
        "Raw Layer/LayerName are retained soldermask-stackup associations, not silk identity; "
        "negative meaning is unverified. Failed getters, duplicate UID, missing actual "
        "side/name or stroke budget are explicit unread_objects; observation "
        "indices are not persistent IDs. This does not establish pin1 semantic identity, "
        "exact-font collision or DRC.",
        net_class_observation_shape=[
            "complete",
            "enumeration_complete",
            "membership_complete",
            "collection",
            "failures",
            "index_basis",
            "member_budget",
            "member_attempted_count",
            "member_budget_remaining",
        ],
        net_class_collection_shape=[
            "complete",
            "count_before",
            "count_after",
            "observed_count",
            "attempted_count",
            "index_base",
            "limit",
            "failures",
        ],
        net_class_membership_shape=[
            "complete",
            "collection",
            "named_member_count",
            "named_members",
            "failures",
        ],
        net_class_read_failure_shape=[
            "location",
            "type",
            "message",
            "code?",
            "details?",
            "hresult?",
            "native?",
        ],
        net_class_observation_boundary="Net-class enumeration and member completeness are "
        "reported separately with before/after counts, one-based observation indices and "
        "located raw getter failures. Failed Item attempts consume the total member budget. "
        "Only a completely read Count=0 is an observed empty "
        "collection; absent or failed reads do not establish nonmembership. Named widths "
        "remain observations, not current effective local-rule proof.",
        clearances_shape=[
            "scheme",
            "active_scheme_verified",
            "unit",
            "source",
            "rules",
            "not_checked",
        ],
        clearances_boundary="Optional bounded --nets/--layers Master trace/trace, trace/pad "
        "and trace/SMD-pad reads. No inferred active or location-dependent scheme; "
        "no fallback zero.",
        observation_shape=[
            "requested_project",
            "application",
            "adapter",
            "started_at",
            "observed_at",
            "native_unit",
            "output_units",
            "geometry",
            "geometry_sha256",
            "plane_state",
            "tie_inventory",
            "consistency",
            "persistence",
            "engineering_write_dispatched",
            "copper_evidence_boundary",
            "limitations",
            "native_routepass_state",
            "native_routepass_request",
            "native_routepass_state_sha256",
            "native_routepass_evidence",
            "native_routepass_components",
            "native_routepass_identity",
            "native_routepass_consistency",
            "drc_hazards_observation",
            "native_general_state",
            "connectivity_components",
        ],
        observation_geometry_shape=["components", "model", "stackup"],
        observation_optional_fields=[
            "plane_state",
            "tie_inventory",
            "native_routepass_state",
            "native_routepass_request",
            "native_routepass_state_sha256",
            "native_routepass_evidence",
            "native_routepass_components",
            "native_routepass_identity",
            "native_routepass_consistency",
            "drc_hazards_observation",
            "native_general_state",
            "connectivity_components",
        ],
        connectivity_components_shape=[
            "contract",
            "valid",
            "observation_complete",
            "snapshot_id",
            "id_scope",
            "board_id",
            "target",
            "native_connectivity",
            "nodes",
            "edges",
            "components",
            "raw_traces",
            "routing_objects",
            "protection_sha256",
            "source_state_sha256",
            "rule_observation_sha256",
            "snapshot_data_sha256",
            "all_members_connected",
            "blockers",
            "not_checked",
        ],
        connectivity_component_shape=["component_id", "node_ids"],
        connectivity_component_node_shape=[
            "node_id",
            "kind",
            "net_uid",
            "layer",
            "physical_material_sha256",
            "trace_uid?",
            "component_uid?",
            "pin_uid?",
            "region_index?",
        ],
        connectivity_component_boundary="Explicit --components requires --live-geometry, a "
        "fresh matching --pid, one actual --nets and one signal --layers conductor. The same "
        "native material graph covers arbitrary observed net names/member counts, existing "
        "traces and separate post-hole/cutout pad regions. Component/node IDs belong only to "
        "that exact snapshot. Via/plane material is outside this finite query boundary. "
        "Same pin identity does not join disconnected regions; point/edge "
        "contact and drill voids are not accepted conductive edges. Native opens/routed and "
        "material partition are separate observations. Missing raw geometry, unsupported shapes, "
        "ownership or positive-area evidence remain explicit blockers. This query never opens, "
        "selects, routes, saves, closes or runs DRC; it does not authorize a later write or prove "
        "persistence, complete effective rules or thermal/tie/manufacturing acceptance.",
        observation_native_general_shape=[
            "state",
            "state_sha256",
            "identity",
            "components",
            "configuration",
            "configuration_after",
            "fromto_inventory",
            "fromto_inventory_after",
            "selection",
            "selection_after",
            "selection_consistency",
            "engineering_write_dispatched",
            "selection_write_dispatched",
            "persistence",
        ],
        observation_native_general_boundary="--native-general-state requires --live-geometry, "
        "a fresh explicit --pid, --nets GND and one --layers conductor. Discover the actual "
        "pin identities, conductive material groups and complete unconnected FromTo "
        "inventory before creating a General Route plan. False actual endpoint objects and "
        "True nearest-pin observations remain distinct; collection indices are not "
        "persistent UIDs. Configuration and FromTo inventories are independently repeated. "
        "Complete selected Nets/Components/Pins/Traces/Vias/FromTos are sampled twice after "
        "the material read; equal readings do not establish an earlier time window or atomic "
        "freeze. Foreign selections remain visible. Failed sample/collection/field/index and "
        "completed rows remain in selection_read_error; "
        "partial reads preserve available evidence and cannot authorize routing. No "
        "selection clearing, Selected PUT, ProcessCommand or Save/Close/Open is dispatched.",
        observation_drc_hazards_boundary="--drc-hazards requires --live-geometry plus a fresh "
        "positive --pid and exactly matching already-open board. It reads existing151.0 "
        "Document.GetHazards only, never Go, Save/Close/Open or a new checker run. "
        "Collection completeness, raw getter errors, actual object material/geometry and "
        "existing freshness are separate; no passes/clean or historical run binding is "
        "inferred. pcb checks run --no-run remains unsupported under the canonical write "
        "contract; this attached query does not weaken that write gate.",
        observation_drc_hazards_shape=[
            "method",
            "freshness",
            "engine_executed",
            "save_close_open_dispatched",
            "collection_complete",
            "count",
            "hazards",
            "type_library_major",
            "passes",
            "not_checked",
        ],
        observation_native_routepass_request_shape=["net", "layer", "continuation?"],
        observation_native_routepass_evidence_shape=["valid", "checks", "reason?"],
        observation_native_routepass_components_shape=[
            "valid",
            "observation_valid",
            "complete",
            "completion_valid",
            "all_members_connected",
            "source",
            "checks",
            "nodes",
            "edges",
            "components",
            "trace_components",
            "component_membership_sha256",
            "completion?",
            "evidence",
            "pin_pair_separation?",
            "not_checked",
            "reason?",
        ],
        observation_native_routepass_component_shape=[
            "node_ids",
            "trace_ids",
            "pin_ids",
            "pin_members",
        ],
        observation_native_routepass_components_boundary="With explicit --continue-existing, "
        "native_routepass_components reports the complete actual-material component inventory "
        "for the limited six-pin GND. observation_valid/valid means the component observation "
        "is complete and trustworthy; complete/completion_valid/all_members_connected states "
        "whole-net completion. A valid observation can explicitly report false completion. "
        "Every old and new target trace and every actual owned pin is included. Unproved "
        "pad bridging, missing material or an incomplete contact matrix refuses observation. "
        "This derived result does not change raw state hashes or resolve historical unknowns.",
        observation_native_routepass_identity_shape=["application", "generation", "board"],
        observation_native_routepass_boundary="Existing pcb inspect --live-geometry with a "
        "positive --pid and exactly one --nets name plus one --layers conductor observes "
        "complete native routepass state, raw pin/trace material and native mask evidence. "
        "The same document's complete before/after core captures must equal that state's "
        "geometry. Missing required state, drift or invalid material evidence returns an "
        "explicit incomplete/error with available partial observations; no empty success. "
        "Actual application, OS process generation and board identities must remain exact "
        "before/after; native_routepass_consistency states the one material read bracketed "
        "by two matching independent geometry captures, without an atomic freeze. "
        "This is observe-only: no RoutePass configuration/Go, Save/Close/Open or historical "
        "operation reconciliation. State/hash and mask-evidence consistency do not prove "
        "that a prior pass saved, completed protection, passed DRC or resolved an unknown. "
        "Without net/layer selectors the existing general live observation remains separate; "
        "ordinary non-live Master queries still require 2..8 nets and 1..8 layers. "
        "Live reads use the published bounded operation profile from project inspect "
        "--preflight: at most1200seconds per native board-info request, with finite total "
        "and stage budgets, unchanged completeness and geometry "
        "budgets and actual PID/project identity. --continue-existing additionally reports "
        "the frozen GND material components independently of whole-net completion; complete "
        "observation does not mean all members are connected. "
        "This grants no routing or persistence.",
        copper_evidence_boundary=evidence_boundary(),
        observation_tie_inventory_shape=[
            "source",
            "units",
            "limits",
            "collection",
            "errors",
            "complete",
            "consistency",
            "comparison",
            "evidence_boundary",
            "before",
        ],
        observation_tie_inventory_collection_shape=["count", "items", "errors", "truncated"],
        observation_tie_inventory_item_shape=[
            "index",
            "uid",
            "net",
            "net_uid",
            "native_layer",
            "type2",
            "is_valid",
            "extrema_mm",
            "geometry",
            "errors",
        ],
        observation_tie_inventory_geometry_shape=[
            "is_valid",
            "is_path",
            "is_closed",
            "is_arced",
            "is_circle",
            "filled",
            "line_width_mm",
            "points_xyr_mm",
            "circle_mm",
            "cutouts",
            "errors",
        ],
        observation_tie_inventory_comparison_shape=[
            "equal",
            "before_complete",
            "after_complete",
            "before_sha256",
            "after_sha256",
        ],
        observation_tie_inventory_boundary=tie_boundary(),
        observation_tie_inventory_reading="Optional --live-geometry document-level supported "
        "TieLegs(0,0) inventory, beside plane_state with separate exact two-read comparison. "
        "Native layer domain remains unresolved. Explicit mm for PointsArray, LineWidth, "
        "CircleX/Y/R and extrema; original x/y/r values retained. Geometry flags are native "
        "methods, Filled is a property. Every bounded collection index or typed getter "
        "failure is retained; a failed getter is null plus errors, never an empty success. "
        "Limits disclose truncation, total indices/points/geometries and cutout depth. "
        "An incomplete or drifting optional inventory does not discard complete core "
        "geometry/plane_state. Before is retained if incomplete or different. Empty never "
        "proves absence of material. No pin/parent ownership is inferred; complete material, "
        "pad contact and clearance remain unproved. Does not change persistence operands.",
        observation_plane_state_shape={
            "shapes": ["uid", "net", "net_uid", "layer", "route_obstructed", "geometry"],
            "generated": [
                "net",
                "net_uid",
                "layer",
                "geometry",
                "bounds_mm",
                "filled",
                "hatch_pattern",
                "hatch_width_mm",
                "hatch_distance_mm",
                "hatch_angle_degrees",
                "fill_non_square_hatch_areas",
                "point_witnesses",
                "identity_basis",
            ],
            "assignments": [
                "net",
                "net_uid",
                "layer",
                "plane_class",
                "state",
                "route_border_as_shape",
            ],
            "classes": ["name", "parent", "lengths_mm_by_dispid", "hatch_angle_degrees"],
        },
        observation_plane_state_boundary="Optional when native planes exist. Separates "
        "PlaneShape drafts, actual GeneratedPlanes, assignments and observed class settings. "
        "Geometry contains path/circle and polygon/circular cutouts in mm. Classes also "
        "retain the returned public scalar settings. Generated identity uses net UID/layer "
        "and complete content, not a hidden object UID. Point witnesses only test sampled "
        "centers; they do not prove full clearances or DRC. Full thermal/tie conductor "
        "geometry, pad contact and connection-leg clearance lack independent proof. Native "
        "opens, a point witness or image alone cannot fill that gap. No persistence claim.",
        observation_boundary="Two matching live reads; persistence is always unknown. "
        "Requires scoped paths, explicit PID and native CurrentUnit=mils; output is mm. "
        "Never saves, opens, switches a board or changes units.",
    ),
    "pcb_geometry": _object(
        [
            "pcb",
            "layers",
            "counts",
            "path",
            "model",
            "artifact",
            "prompts",
            "_untrusted",
            "layout_reminders",
            "layout_reminder_coverage",
        ],
        ["pcb", "prompts", "model", "layout_reminders", "layout_reminder_coverage"],
        artifact_scope="Generation/project/board identity and byte hash; model includes "
        "physical_instances with actual copper/fabrication contours and holes, stackup, "
        "and placement outlines. Artifact verification is separate from engineering acceptance. "
        "Before generation, public previews expose projection reminders plus incomplete coverage; "
        "an empty warning list is not a layout pass.",
        **_LAYOUT_PROJECTION_SCHEMA,
    ),
    "pcb_render": _object(
        [
            "pcb",
            "side",
            "scale",
            "picture",
            "artifact",
            "layers",
            "refdes",
            "window",
            "prompts",
            "_untrusted",
            "layout_reminders",
            "layout_reminder_coverage",
        ],
        ["pcb", "picture", "prompts", "layout_reminders", "layout_reminder_coverage"],
        optional_fields=["refdes", "window"],
        artifact_scope="PNG xpedition metadata binds generation, project, board, current "
        "geometry and view; independent decode is not visual acceptance.",
        **_LAYOUT_PROJECTION_SCHEMA,
    ),
    "pcb_show": _object(
        [
            "pcb",
            "scheme",
            "scheme_applied",
            "scheme_written",
            "fitted",
            "zoomed",
            "view",
            "schemes",
            "components",
            "prompts",
            "foreground",
            "window",
            "locked",
            "hint",
            "screenshot",
            "scheme_plan",
            "display_observed",
            "native_load_acknowledged",
            "gui_selection",
            "identity_verified",
            "window_hwnd",
            "viewport_requested",
            "layout_reminders",
            "layout_reminder_coverage",
            "geometry_sha256",
            "artifact",
            "_untrusted",
        ],
        ["pcb", "schemes", "prompts", "layout_reminders", "layout_reminder_coverage"],
        optional_fields=["zoomed", "view", "foreground", "window", "locked", "hint", "screenshot"],
        verification_scope=(
            "A click is diagnostic only. Require native effective scheme, viewport and visible "
            "objects, protected geometry, and independently decoded fresh PNG when requested. "
            "PNG pixels must match observed physical window bounds under per-monitor DPI. "
            "Visual review and DRC remain separate. Preview binds source, target and native "
            "profile copies under Config; no existing scheme is overwritten."
        ),
        **_LAYOUT_PROJECTION_SCHEMA,
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
    "close_diagnostic": _object(
        [
            "contract",
            "binding_sha256",
            "board_lifecycle",
            "terminal_state",
            "persistence_verified",
            "reopen_attempted",
        ],
        ["board_lifecycle"],
    ),
    "pcb_check": _object(
        [
            "pcb",
            "ran",
            "completion",
            "completed",
            "native_reports",
            "engine_messages",
            "input_configurations",
            "reopened_hazards_match",
            "check_run_id",
            "check_record",
            "source",
            "verification",
            "hazards_read",
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
            "board_rules",
            "board_rule_count",
            "_untrusted",
        ],
        ["pcb", "hazards", "online", "prompts", "board_rules", "source.input"],
        evidence_scope="passes/clean require a completed current run, native source, actual "
        "application/checker versions, target/input snapshot and rule/exclusion coverage; "
        "missing evidence is incomplete; retained checks are read with checks result",
        native_report_record_types=["PROXIMITY", "UNROUTED_NET", "PARTIAL_NET"],
        native_report_partial_boundary="PARTIAL_NET has an observed finite single-PAD parser; "
        "its complete raw record and field tree are retained. This does not add a native "
        "hazard mapping, count opens, verify native completion or establish rule applicability. "
        "Use checks result --native-files for explicitly historical hash-bound parsing; "
        "a successful historical parse never updates an older check or operation receipt.",
        native_limit="Registered MGCPCBEngines.DRC consumes bound native profiles and emits "
        "fresh report/violation/effective-setup files. Rule coverage is limited to those "
        "configurations, including ignored values; no rule-property getters or complete "
        "application coverage are claimed.",
        native_output_planning="The preview declares known report, effective configuration, "
        "staging and lifecycle-log paths. All ten declared paths, including the five "
        "lifecycle logs, are observed for byte changes; other native side effects remain "
        "unclassified and a declared path does not prove a fresh or complete report.",
        native_file_integrity="Live native and SDK readback bind all four output roles to "
        "the requested board paths, reject reparse points, and consume at most 32 MiB per "
        "regular file. Hashes describe the captured bytes and device/inode file identity. "
        "All roles are recaptured after parsing; a changed output refuses. This is not an "
        "atomic multi-file database snapshot.",
        native_annular_unit_binding="source.coverage.annular_unit_binding retains selected "
        "and effective raw fields separately from explicitly labelled151.0 Chinese report "
        "distances. File-to-display units remain unverified and cannot authorize a rule "
        "write. A configuration refusal retains unqualified_report_observation with its "
        "consumed file hashes; it does not qualify execution or update a previous receipt.",
        rule_execution_scope="source.coverage.rule_execution_verification correlates the "
        "observed 151.0 Chinese PARTCOVERLAYER/VIACOVERLAYER report markers with their settings. "
        "Missing, ambiguous or conflicting markers remove that rule from checked/excluded "
        "claims. An explicitly selected unverified rule makes evidence incomplete; "
        "an engine-added YES switch needs supported report correlation to be claimed. "
        "Unverified native settings remain disclosed without rewriting them.",
        unread_counts="count/errors/warnings are null when hazards_read is false; "
        "an absent result is not a zero-finding result",
        native_hazard_proximity_shape=[
            "source",
            "unit",
            "first_object_position_mm",
            "second_object_position_mm",
            "actual_clearance_mm",
            "required_clearance_mm",
            "complete",
            "errors",
        ],
        native_hazard_proximity_boundary="Formal151.0 IMGCPCBHazard PROPERTYGET uses explicit "
        "millimetres and preserves getter errors/undefined negative clearance. Its complete "
        "flag covers these getters only, not complete object identity or DRC verification. "
        "The finite Layer1 pin-to-own-Top-placement matcher additionally needs independent "
        "object UIDs/types, real Layer1 pad material and native placement-outline geometry. "
        "PlacementOutline has no Position getter: its observed rectangle minimum is a "
        "geometry-derived correspondence anchor, never a fabricated Position observation. "
        "THT Pin.Layer is not valid; actual Pin.Pads establish the observed material layer. "
        "Report CLEARANCE is not assumed to equal either "
        "native actual or required clearance. No annular124460/4.9 normalization is enabled; "
        "field-specific storage semantics are still unproven, and missing/duplicate unit "
        "or value headers refuse configuration binding.",
        board_rules_shape=[
            "rule",
            "severity",
            "finding",
            "refdes",
            "net",
            "text",
            "other",
            "pins",
            "at",
            "layer",
        ],
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
            extra = ["operations"] if command.path == "schematic build" else []
            fields = ["preview", *extra, "confirm_token", "expires_at", "_untrusted"]
            result[command.dry_run_schema] = _object(fields, ["preview", *extra])
            if command.path == "pcb routing auto":
                result[command.dry_run_schema].update(
                    {
                        "native_routepass_preview_shape": [
                            "mode",
                            "routepass",
                            "state_guard",
                            "state_digest",
                            "limits",
                        ],
                        "native_routepass_plan_shape": SCHEMAS["pcb_route"][
                            "native_routepass_shape"
                        ],
                        "native_routepass_boundary": SCHEMAS["pcb_route"][
                            "native_routepass_boundary"
                        ],
                        "preview_is_route_execution": False,
                    }
                )
            if command.path == "schematic edit":
                result[command.dry_run_schema].update(
                    {
                        "display_preview_shape": [
                            "changes",
                            "steps[].display_before",
                            "drawing_review",
                            "lifecycle",
                            "save_metadata",
                        ],
                        "display_lifecycle": "Display-only set_property freezes actual attribute "
                        "display state and requested x/y/VDVALUEVISIBLE3/optional Size; protected "
                        "getters must match before the one SaveAll, normal CloseProject and "
                        "OpenProject. Fresh public identity and all observed engineering records "
                        "must match after reopening. Exact fonts/envelopes and other text remain "
                        "unreviewed. Failed reads stop the sequence without an automatic retry.",
                    }
                )
            if command.path == "library materialize":
                result[command.dry_run_schema].update(
                    {
                        "materialization_preview_shape": [
                            "partition",
                            "padstacks",
                            "cells",
                            "parts",
                            "can_apply",
                            "materialization_not_needed",
                            "changes",
                            "summary.mapping",
                            "library_parts",
                            "next_action",
                        ],
                        "empty_plan_contract": "Object counts come from the resolved library "
                        "plan, not nonempty HKP headers. An all-bound design has "
                        "materialization_not_needed=true, can_apply=false and changes=[]. "
                        "Its mappings remain visible. Confirmations, including legacy tokens, "
                        "cannot dispatch library_import. Direct empty/header-only adapter "
                        "requests stop before converters, project Save/Close/Open or native "
                        "library registration. This refusal neither verifies an earlier "
                        "unknown operation nor proves nonempty import merge/persistence. "
                        "Run schematic package separately after independent library checks.",
                    }
                )
    return result


def release_readiness() -> dict[str, Any]:
    return {
        "level": "unpublishable",
        "fcc_required": True,
        "fcc_status": "missing",
        "mock_upstream_required": True,
        "mock_upstream_status": "missing",
        "live_smoke_required_for_stable": True,
        "live_smoke_status": "missing",
        "reason": (
            "The breaking 2.0 candidate uses the published v1.0.2 driver SDK. Public "
            "command FCC, agent evaluations and current native task acceptance remain "
            "incomplete. Internal handler fixtures, historical E2E documentation and "
            "registered type libraries do not establish a current licensed real task "
            "or independent save/reopen. No whole-application coverage is claimed."
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
