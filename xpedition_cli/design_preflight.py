"""Design-before-write capability query. Declared counts never authorize native work."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import __version__
from .design_capabilities import contract as material_contract
from .errors import CLIError
from .operation_budget import budget_catalog, profile_for

MAX_REQUIREMENTS_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 8
MAX_JSON_VALUES = 256
MAX_JSON_STRING = 128
MAX_DECLARED_COUNT = 10**7
COUNT_KEYS = {
    "components",
    "pins",
    "named_nets",
    "traces",
    "vias",
    "planes",
    "layers",
    "target_net_pins",
    "candidate_path_points",
    "selected_components",
    "placement_steps",
    "raw_points",
    "contours",
    "round_holes",
    "mask_points",
    "comparisons",
    "temporary_masks",
    "max_component_pins",
    "protection_bytes",
    "schematic_sheets",
    "pdf_pages",
    "pdf_bytes",
    "pdf_objects",
    "decoded_pdf_bytes",
    "max_pdf_stream_bytes",
    "outline_points",
    "setup_source_files",
    "setup_source_bytes",
    "schematic_operations",
    "max_component_attributes",
    "schematic_intermediate_points",
    "schematic_connections",
    "schematic_nets",
    "schematic_json_values",
    "schematic_string_bytes",
}
OPERATIONS = {
    "project_create",
    "schematic_edit",
    "schematic_read",
    "pcb_read",
    "placement_batch",
    "component_connect",
    "pcb_render",
    "pdf_export",
    "session_close",
    "outline_set",
}
OUTLINES = {"straight_polygon", "rounded_rectangle_13", "unknown", "unsupported"}
BYTE_COUNTS = {
    "protection_bytes",
    "pdf_bytes",
    "decoded_pdf_bytes",
    "max_pdf_stream_bytes",
    "setup_source_bytes",
    "schematic_string_bytes",
}
COMMANDS = {
    "project_create": "project create",
    "schematic_edit": "schematic edit",
    "schematic_read": "schematic parts",
    "pcb_read": "pcb inspect",
    "placement_batch": "pcb placement move",
    "component_connect": "pcb routing trace",
    "pcb_render": "pcb render",
    "pdf_export": "schematic export",
    "session_close": "session close",
    "outline_set": "pcb outline set",
}


def _invalid(reason, field="requirements", **details):
    raise CLIError(
        "E_VALIDATION",
        "Requirements do not satisfy design-preflight-v1.",
        {"field": field, "reason": reason, "write_attempted": False, **details},
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_non_integer(_value):
    raise ValueError("non_integer_or_nonfinite_number")


def _integer(value):
    if len(value.lstrip("-")) > 16:
        raise ValueError("integer_token_too_long")
    return int(value)


def _bounded_json(text):
    depth, quoted, escaped = 0, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("json_depth_limit")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("invalid_json_container_balance")
    value = json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_non_integer,
        parse_float=_reject_non_integer,
        parse_int=_integer,
    )
    pending, visited = [value], 0
    while pending:
        item = pending.pop()
        visited += 1
        if visited > MAX_JSON_VALUES:
            raise ValueError("json_value_limit")
        if isinstance(item, dict):
            if any(len(key) > MAX_JSON_STRING for key in item):
                raise ValueError("json_key_length_limit")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str) and len(item) > MAX_JSON_STRING:
            raise ValueError("json_string_length_limit")
    return value


def _validate(value):
    if not isinstance(value, dict) or set(value) - {
        "schema_version",
        "counts",
        "outline_family",
        "operations",
    }:
        _invalid("root_object_or_keys")
    if value.get("schema_version") != "1.0":
        _invalid("unsupported_schema_version", "schema_version")
    counts = value.get("counts")
    if not isinstance(counts, dict) or set(counts) - COUNT_KEYS:
        _invalid("counts_object_or_keys", "counts")
    for key, count in counts.items():
        maximum = 2**40 if key in BYTE_COUNTS else MAX_DECLARED_COUNT
        if type(count) is not int or not 0 <= count <= maximum:
            _invalid("count_must_be_bounded_nonnegative_integer", "counts." + key, maximum=maximum)
    family = value.get("outline_family", "unknown")
    if not isinstance(family, str) or family not in OUTLINES:
        _invalid("unsupported_outline_family", "outline_family", allowed=sorted(OUTLINES))
    operations = value.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= len(OPERATIONS):
        _invalid("operations_must_be_bounded_nonempty_array", "operations", maximum=len(OPERATIONS))
    if any(not isinstance(item, str) or item not in OPERATIONS for item in operations):
        _invalid("unsupported_operation", "operations", allowed=sorted(OPERATIONS))
    if len(set(operations)) != len(operations):
        _invalid("duplicate_operation", "operations")
    return value


def requirements(path):
    try:
        file = Path(path).expanduser().resolve()
        before = file.stat()
        if not file.is_file():
            _invalid("requirements_must_be_a_regular_file")
        if before.st_size > MAX_REQUIREMENTS_BYTES:
            _invalid("file_byte_limit", maximum=MAX_REQUIREMENTS_BYTES)
        with file.open("rb") as stream:
            data = stream.read(MAX_REQUIREMENTS_BYTES + 1)
            opened = os.fstat(stream.fileno())
        after = file.stat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(
            getattr(before, key) != getattr(after, key)
            or getattr(opened, key) != getattr(after, key)
            for key in fields
        ):
            raise CLIError(
                "E_CONFLICT", "Requirements changed while being read.", {"write_attempted": False}
            )
    except FileNotFoundError as error:
        raise CLIError("E_NOT_FOUND", "Requirements file was not found.") from error
    except ValueError as error:
        _invalid("invalid_requirements_path", cause_type=type(error).__name__)
    except (OSError, RuntimeError, TypeError) as error:
        raise CLIError(
            "E_IO",
            "Requirements file could not be read.",
            {"cause_type": type(error).__name__, "write_attempted": False},
        ) from error
    if len(data) > MAX_REQUIREMENTS_BYTES:
        _invalid("file_byte_limit", maximum=MAX_REQUIREMENTS_BYTES)
    try:
        value = _validate(_bounded_json(data.decode("utf-8-sig")))
    except (ValueError, UnicodeError, RecursionError) as error:
        _invalid("invalid_or_unbounded_json", cause_type=type(error).__name__)
    return value, {
        "path": str(file),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def inspect(path=None):
    """Pure query: report finite limits and declared blockers without connecting CAD."""
    from .designer_native_owner import capacity_contract

    intent, source = (
        requirements(path)
        if path
        else ({"schema_version": "1.0", "counts": {}, "operations": sorted(OPERATIONS)}, None)
    )
    limits = material_contract()
    counts = intent["counts"]
    steps = []
    environment = [
        "Windows AMD64 product runtime",
        "legally installed Xpedition with registered COM automation and a valid license",
        "matching EXE, public launcher, Skill and packaged dependency bindings",
        "current user authorization and an owned exact-project application instance",
    ]
    for operation in intent["operations"]:
        blockers = []
        checks = []
        declared_cost = None

        def bounded(key, maximum, *, minimum=0, reserve=0, _checks=checks, _blockers=blockers):
            actual = counts.get(key)
            prospective = actual + reserve if actual is not None else None
            blocked = prospective is not None and not minimum <= prospective <= maximum
            check = {
                "resource": key,
                "declared": actual,
                "minimum": minimum,
                "limit": maximum,
                "reserved_additional": reserve,
                "observed_or_reserved": prospective,
                "status": "unknown" if actual is None else "blocked" if blocked else "within_limit",
                "basis": "declared counts only; complete native observations remain required",
            }
            _checks.append(check)
            if blocked:
                _blockers.append(
                    {
                        "code": "resource_limit",
                        **{key: value for key, value in check.items() if key != "status"},
                    }
                )

        conditions = [
            "current user authorization and exact installed candidate binding",
            "fresh native identities and complete readback; "
            "declared counts are not native evidence",
        ]
        objects = []
        step_environment = list(environment)
        if operation == "project_create":
            from .designer_source_binding import MAX_BYTES, MAX_FILES
            from .project_create_budget import HARD_CAP_SECONDS, MODEL, required_seconds

            bounded("setup_source_files", MAX_FILES, minimum=1)
            bounded("setup_source_bytes", MAX_BYTES, minimum=1)
            files, size = counts.get("setup_source_files"), counts.get("setup_source_bytes")
            seconds = (
                required_seconds(files, size)
                if files is not None and size is not None and not blockers
                else None
            )
            declared_cost = {
                "basis": "declared counts only; not a source observation or signed creation plan",
                "required_seconds": seconds,
                "native_hard_cap_seconds": HARD_CAP_SECONDS,
                "status": "unknown"
                if seconds is None
                else ("blocked" if seconds > HARD_CAP_SECONDS else "within_limit"),
                "model": dict(MODEL),
            }
            if seconds is not None and seconds > HARD_CAP_SECONDS:
                blockers.append(
                    {
                        "code": "project_create_budget_exceeded",
                        "required_seconds": seconds,
                        "limit_seconds": HARD_CAP_SECONDS,
                    }
                )
            objects = [
                "official uninitialized project template and complete "
                "central-library source closure",
                "new target project, native database, design and sheet",
            ]
            conditions += [
                "CLI-owned dedicated empty Designer session; "
                "arbitrary existing instance takeover is unsupported",
                "new unused target and official uninitialized setup input; "
                "copying a completed design is not creation",
                "complete actual source closure, source hashes and signed resource cost "
                "must be rechecked by preview and confirmation",
                "NativeNewProject return, exact document/database identity "
                "and save-close-reopen must all be verified",
            ]
        elif operation == "schematic_edit":
            from .schematic_edit_protection import LIMITS as edit_limits

            for key, maximum, minimum in (
                ("schematic_operations", edit_limits["operations"], 1),
                ("components", edit_limits["components"], 0),
                ("max_component_pins", edit_limits["pins_per_component"], 0),
                ("max_component_attributes", edit_limits["attributes"], 0),
                ("schematic_intermediate_points", edit_limits["connect_intermediate_points"], 0),
                ("schematic_sheets", edit_limits["sheets"], 1),
                ("schematic_nets", edit_limits["collection_items"], 0),
                ("schematic_connections", edit_limits["collection_items"], 0),
                ("schematic_json_values", edit_limits["json_values"], 0),
                ("schematic_string_bytes", edit_limits["string_bytes"], 0),
            ):
                bounded(key, maximum, minimum=minimum)
            objects = [
                "complete named and unnamed component, net, connection and sheet observations",
                "stable component UIDs, all pins, existing attributes and display records",
                "explicit edit batch with all unrequested target fields "
                "and non-target objects protected",
            ]
            conditions += [
                "counts include proposed resulting objects, including unnamed symbols; "
                "no target-only observation",
                "supported ordinary edits: place real library part, move, set existing property, "
                "create net and connect explicit pins",
                "existing Value/Part Label display edits "
                "are a separate homogeneous operation batch",
                "unique native identities and complete observations "
                "remain required within the declared numeric limits",
                "normal Save/Close/exact Open and independent full protection checks "
                "after each changed batch; not an atomic multi-object transaction",
                "free graphics, general wire geometry, ERC "
                "and hardware correctness remain unverified",
            ]
        elif operation == "component_connect":
            for key in (
                "pins",
                "target_net_pins",
                "raw_points",
                "contours",
                "round_holes",
                "mask_points",
                "comparisons",
                "temporary_masks",
            ):
                bounded(key, limits["limits"][key])
            # No smaller independent per-device cap: every component contributes
            # to the same complete whole-board physical pin inventory.
            bounded("max_component_pins", limits["limits"]["pins"])
            bounded("traces", limits["limits"]["traces"], reserve=1)
            bounded("candidate_path_points", limits["limits"]["candidate_path_points"], minimum=2)
            bounded("layers", 128, minimum=1)
            for key in ("vias", "planes"):
                bounded(key, 0)
            family = intent.get("outline_family", "unknown")
            if family == "unsupported":
                blockers.append({"code": "unsupported_outline_family"})
            objects = [
                "all physical pin identities/material including foreign and Net0 pins",
                "all old copper UIDs and raw paths",
                "one candidate path and actual net/layer",
                "complete outline, masks, text, rule and plane inventories",
            ]
            conditions += [
                "one actual net, one signal layer and explicit simple path "
                "on the four-decimal mm grid",
                "all board pin/foreign/old-copper material, actual netUID "
                "and masks must be complete",
                "prospective native comparison/mask/point budgets must pass before transaction",
                "rounded rectangle13 requires exact native bidirectional-equality proof",
                "public registered closed-copy origin and same-Close receipt/ZIP continuation",
                "actual minimum width, native transaction DRC "
                "and full save-close-reopen protection",
            ]
        elif operation == "placement_batch":
            from . import placement

            for key, maximum in (
                ("selected_components", placement.MAX_SELECTION),
                ("placement_steps", placement.MAX_STEPS),
            ):
                bounded(key, maximum, minimum=1)
            for key, maximum in (
                ("components", placement.MAX_BOARD_COMPONENTS),
                ("pins", placement.MAX_BOARD_PINS),
                ("max_component_pins", placement.MAX_COMPONENT_PINS),
                ("protection_bytes", placement.MAX_PROTECTION_BYTES),
            ):
                bounded(key, maximum)
            for key in ("traces", "vias", "planes"):
                bounded(key, placement.MAX_BOARD_COPPER_OBJECTS)
            objects = [
                "explicit selected placed instances",
                "all board components, pin material and protection locks",
                "full outline and existing copper protection snapshot",
            ]
            conditions += [
                "existing pcb placement move --file; selected instances already placed",
                "full board stable identities, material, locks and non-target protection",
                "translate/align/distribute or orthogonal rotation; traces are not rerouted",
                "one verified Save/Close/exact Open per changed successful batch; "
                "partial execution is not atomic",
            ]
        elif operation in {"pcb_render", "pcb_read"}:
            objects = [
                "existing board component/pad/copper/outline/stackup geometry",
                "PNG projection and provenance"
                if operation == "pcb_render"
                else "board summary and optional full live geometry",
            ]
            conditions += [
                "already-open matching owned board; "
                "complete geometry must fit the public operation budget",
                "CurrentUnit=mils for full render/snapshot projection; no implicit unit changes",
                "artifact decode/source/view binding and separate visual review",
            ]
        elif operation == "pdf_export":
            from .schematic_pdf_artifacts import LIMITS as pdf_limits

            for key, maximum in (
                ("pdf_pages", pdf_limits["pages"]),
                ("schematic_sheets", pdf_limits["pages"]),
                ("pdf_bytes", pdf_limits["file_bytes"]),
                ("pdf_objects", pdf_limits["objects"]),
                ("decoded_pdf_bytes", pdf_limits["decoded_total_bytes"]),
                ("max_pdf_stream_bytes", pdf_limits["stream_bytes"]),
            ):
                bounded(key, maximum)
            objects = [
                "complete same-project native part/pin/sheet census",
                "pinned sch2pdf executable and invocation",
                "fresh classic-xref PDF 1.0--1.4 and bounded object/content syntax",
            ]
            conditions += [
                "owned matching Designer project already open "
                "with complete native identities/pins/sheets",
                "licensed installation includes sch2pdf and bundled bounded PDF parser",
                "producer, artifact structure, captured native protection "
                "and visual checks are separate",
                "no encrypted, incremental or object-stream PDF fallback; "
                "pixels, fonts and design correctness are not accepted",
            ]
            step_environment += ["installed sch2pdf.exe and packaged pypdf runtime"]
        elif operation == "session_close":
            objects = [
                "owned OS process PID/executable/creation time",
                "empty document or exact explicitly saved project",
            ]
            conditions += [
                "owned PID, executable and creation identity; no foreign instances",
                "empty document or explicit --save-project "
                "with persist scope and strict save/close",
                "Quit once; COM unregistering does not establish OS process exit",
            ]
        elif operation == "outline_set":
            from . import outline_readback

            bounded("outline_points", outline_readback.MAX_CONTOUR_POINTS)
            bounded("named_nets", outline_readback.MAX_NETS)
            bounded("layers", outline_readback.MAX_STACK_LAYERS, minimum=1)
            objects = [
                "BoardOutline",
                "RouteBorder",
                "ManufacturingOutline",
                "full native non-target board state",
            ]
            conditions += [
                "existing explicit rectangle/rounded-rectangle dimensions only",
                "all three native contour getters complete; no unsupported inner contours",
                "strict save/normal close/exact reopen "
                "and independent target/non-target comparison",
                "route border inset is the declared 0.3mm; no fabrication-rule acceptance",
            ]
        else:
            objects = ["complete native named and unnamed component/pin/net/sheet census"]
            conditions += [
                "owned matching Designer, complete component/pin/net census "
                "and bounded independent observation"
            ]
        steps.append(
            {
                "operation": operation,
                "status": "blocked" if blockers else "requires_native_observation",
                "declared_resources_fit": not blockers,
                "can_dispatch": False,
                "resource_checks": checks,
                "blockers": blockers,
                "conditions": conditions,
                "unreported_resources": [
                    item["resource"] for item in checks if item["declared"] is None
                ],
                "all_resources_declared": bool(checks)
                and all(item["declared"] is not None for item in checks),
                "objects": objects,
                "command": COMMANDS[operation],
                "operation_budget": profile_for(COMMANDS[operation]),
                "declared_creation_budget": declared_cost,
                "environment_requirements": step_environment,
                "native_acceptance": "not_run",
            }
        )
    return {
        "schema_version": "1.0",
        "kind": "design-preflight-v1",
        "version": __version__,
        "source": "declared_requirements" if source else "configured_capability_catalog",
        "requirements": source,
        "counts": counts,
        "steps": steps,
        "blocked": any(step["status"] == "blocked" for step in steps),
        "can_dispatch": False,
        "native_observation_performed": False,
        "material_capabilities": limits,
        "verification_resources": budget_catalog(),
        "designer_session_capacity": {
            **capacity_contract(),
            "live_usage": None,
            "live_usage_observation": "not_requested_by_declared_preflight",
        },
        "input_limits": {
            "requirements_bytes": MAX_REQUIREMENTS_BYTES,
            "json_depth": MAX_JSON_DEPTH,
            "json_values": MAX_JSON_VALUES,
            "json_string_characters": MAX_JSON_STRING,
            "maximum_declared_count": MAX_DECLARED_COUNT,
            "maximum_declared_byte_count": 2**40,
            "duplicate_keys": "rejected",
            "non_integer_numbers": "rejected",
        },
        "environment_requirements": [
            "Windows AMD64",
            "legally installed Xpedition and COM registration",
            "valid native license; its expiration is not inferred",
            "matching EXE/launcher/Skill/dependency bindings; doctor separately observes readiness",
        ],
        "not_accepted": [
            "autorouting",
            "vias/layer changes",
            "planes",
            "full effective-rule engine",
            "BatchDRC end-to-end",
            "hardware or manufacturing sign-off",
        ],
        "limitation": (
            "A resource limit is not a qualification. Native gates can still refuse "
            "a 467-terminal design whose declared counts fit."
        ),
        "_untrusted": ["requirements"],
    }
