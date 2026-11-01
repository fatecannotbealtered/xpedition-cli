"""schematic plan preview | draw | edit | check | components | nets | sheets | show | export."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..errors import CLIError
from .common import (
    check_gate,
    confirmed,
    input_file,
    integer,
    native,
    number,
    output_file,
    pace,
    page,
    previewed,
    project_file,
    read_design,
    reader,
    require_dangerous,
    sheets_option,
    text,
)

# -- planning and drawing --------------------------------------------------------


def annotate_operations(ops: list[Any]) -> list[dict[str, Any]]:
    """The planned operations, each carrying the index and sheet it lands on.

    A draw failure reports an operation index, and an index is only locatable
    against the plan it came from.
    """
    annotated: list[dict[str, Any]] = []
    sheet = 0
    for index, op in enumerate(ops):
        entry = dict(op) if isinstance(op, dict) else {"op": op}
        if entry.get("op") == "open_sheet":
            try:
                sheet = int(entry.get("number", sheet))
            except (TypeError, ValueError):
                pass
        annotated.append({"index": index, "sheet": sheet, **entry})
    return annotated


def ops_for_sheets(ops: list[dict[str, Any]], chosen: list[int]) -> list[dict[str, Any]]:
    """The operations of the chosen sheets: each sheet's run from its `open_sheet`."""
    kept: list[dict[str, Any]] = []
    sheet: int | None = None
    for op in ops:
        if op.get("op") == "open_sheet":
            sheet = int(op["number"])
        if sheet in chosen:
            kept.append(op)
    return kept


def render(options: dict[str, Any]) -> dict[str, Any]:
    """A picture of every planned sheet, drawn from the plan before anything is drawn."""
    from .. import schematic_layout, schematic_render, schematic_review
    from .library import design_library

    design_file, design = read_design(options, "schematic plan preview")
    output = output_file(options, "schematic plan preview", ".png", required=True)
    assert output is not None
    project = project_file(options, "schematic plan preview") if options.get("project") else None
    parts = design_library(options, project, design, "schematic plan preview")
    try:
        plan = schematic_layout.plan(design, parts)
    except schematic_layout.DesignError as exc:
        raise CLIError(
            "E_VALIDATION",
            f"design cannot be drawn: {exc}",
            {"problem": "invalid_value", "parameter": "--design", "design": str(design_file)},
        ) from exc
    planned = [int(sheet["number"]) for sheet in plan.sheets]
    chosen = sheets_option(options, planned)
    drawing_review = schematic_review.planned(plan, chosen)
    scale = number(options, "scale", schematic_render.SCALE)
    assert scale is not None
    if not 0.5 <= scale <= 6:
        raise CLIError(
            "E_VALIDATION",
            "--scale must be between 0.5 and 6",
            {"problem": "invalid_value", "parameter": "--scale"},
        )
    if options.get("dry_run"):
        sheets = set(chosen or planned)
        return {
            "design": str(design_file),
            "sheets": sorted(sheets),
            "issues": [issue for issue in plan.issues if issue.get("sheet", 0) in sheets],
            "drawing_review": drawing_review,
            "_untrusted": ["design", "issues", "drawing_review"],
        }
    try:
        import PIL  # noqa: F401
    except ImportError as exc:
        raise CLIError(
            "E_CONFIG",
            "schematic plan preview draws with Pillow, which is not installed",
            {"hint": 'python -m pip install "pillow>=12.3"'},
        ) from exc
    size = str(design.get("sheet_size", "B")).upper()
    try:
        pictures = schematic_render.render(
            plan, size, output, chosen, scale, replace=bool(options.get("replace"))
        )
    except FileExistsError as exc:
        raise CLIError(
            "E_CONFLICT", "output file already exists", {"path": str(exc), "hint": "--replace"}
        ) from exc
    sheets = set(chosen or planned)
    issues = [issue for issue in plan.issues if issue.get("sheet", 0) in sheets]
    return {
        "design": str(design_file),
        "pictures": pictures,
        "issues": issues,
        "drawing_review": drawing_review,
        "summary": {"sheets": len(pictures), "issues": len(issues)},
        "_untrusted": ["design", "pictures", "issues", "drawing_review"],
    }


def draw(options: dict[str, Any]) -> dict[str, Any]:
    """Plan a schematic from a design description, then draw it.

    The plan is pure Python, so the dry run shows the sheets, parts, netlist and
    convention issues without Designer. The confirmed run wipes and redraws every
    listed sheet, reopens the project and reports whether the netlist read back
    matches the plan.
    """
    from .. import schematic_layout, schematic_review
    from .library import design_library

    check_gate(options, "schematic build")
    project = project_file(options, "schematic build")
    design_file, design = read_design(options, "schematic build")
    parts = design_library(options, project, design, "schematic build")
    try:
        params = schematic_layout.plan_to_params(design, str(project), parts)
    except schematic_layout.DesignError as exc:
        raise CLIError(
            "E_VALIDATION", f"design cannot be drawn: {exc}", {"design": str(design_file)}
        ) from exc
    summary = params["summary"]
    params["verify"]["part_numbers"] = {
        op["refdes"]: op["part"]
        for op in params["ops"]
        if op.get("op") == "place_part" and op.get("part")
    }
    planned = [int(sheet["number"]) for sheet in summary["sheets"]]
    chosen = sheets_option(options, planned)
    if chosen is not None:
        # Every sheet ends in a save, so the sheets a failed draw completed are on
        # disk: redrawing only the rest resumes it. The netlist read back at the end
        # still covers the whole design.
        params["ops"] = ops_for_sheets(params["ops"], chosen)
    drawing = chosen if chosen is not None else planned
    drawing_review = schematic_review.planned_params(params, design, parts or {}, drawing)
    params["design_sheets"] = planned
    seconds = pace(options, "schematic build")
    if seconds:
        params["pace"] = seconds
    digest = hashlib.sha256(json.dumps(params["ops"], sort_keys=True).encode("utf-8")).hexdigest()
    scope = {
        "operation": "schematic_draw",
        "project": str(project),
        "plan": digest[:16],
        "sheets": drawing,
    }
    if options.get("dry_run"):
        preview = {
            "changes": [
                {
                    "action": "wipe_and_draw_sheet",
                    "sheet": sheet["number"],
                    "title": sheet["title"],
                    "parts": sheet["parts"],
                }
                for sheet in summary["sheets"]
                if int(sheet["number"]) in drawing
            ],
            "sheets_kept": [item for item in planned if item not in drawing],
            "summary": summary,
            "dangerous": True,
            "requires": "--dangerous with --confirm",
            "verification_input": params["verify"],
            "drawing_review": drawing_review,
            "risk": {
                "tier": "T2",
                "blast_radius": (
                    "every sheet drawn is wiped and redrawn, hand edits included; symbol files "
                    "are written into the project's central-library partition"
                ),
            },
        }
        # The operations themselves, so a reported index can be looked up without
        # the planner. Trim with --fields when the plan is large.
        return previewed(preview, scope, operations=annotate_operations(params["ops"]))
    require_dangerous(options, "schematic build wipes every sheet it draws, hand edits included")
    backend = native()
    confirmed(options, scope)
    request = {key: value for key, value in params.items() if key != "summary"}
    result = backend.invoke("draw", request, timeout_seconds=1800.0)
    result["summary"] = summary
    result["drawing_review"] = drawing_review
    result["_untrusted"] = [*(result.get("_untrusted") or []), "summary", "drawing_review"]
    return result


# -- editing ----------------------------------------------------------------------


def _snapshot_hash(project: dict[str, Any]) -> str:
    from ..native_snapshot_revision import canonical

    material = json.dumps(
        canonical(project), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _edit_scope_summary(scope):
    """Current fixed fields only; never values, tokens, binding hashes or expected guesses."""
    fields = {}
    for key in ("operation", "project", "design_hash", "operations", "save_metadata"):
        value = scope[key]
        item = {"type": "null" if value is None else type(value).__name__}
        if isinstance(value, (str, list, dict)):
            item["length"] = len(value)
        fields[key] = item
    return {"current_fields": fields, "expected_fields": "unobserved"}


_ON_A_PART = {"move_component", "delete_component", "set_property", "disconnect"}


def native_operations(
    design: dict[str, Any], operations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The operations as Designer carries them out: each on the sheet its part is drawn
    on (unless it names one), and a net renamed on every sheet that draws it."""
    part_sheets = {
        str(component.get("refdes")): component.get("sheet")
        for component in design.get("components", [])
    }
    net_sheets = {str(net.get("name")): net.get("sheets") or [] for net in design.get("nets", [])}
    result: list[dict[str, Any]] = []
    for operation in operations:
        item = dict(operation)
        kind = item["type"]
        if kind == "rename_net" and "sheet" not in item:
            sheets = sorted(int(s) for s in net_sheets.get(str(item["net"]), []) if s is not None)
            result += [{**item, "sheet": sheet} for sheet in sheets] or [item]
            continue
        if kind in _ON_A_PART and "sheet" not in item:
            refdes = str(item.get("refdes") or str(item.get("pin", "")).partition(".")[0])
            if part_sheets.get(refdes) is not None:
                item["sheet"] = int(part_sheets[refdes])
        if "display" in item:
            import copy

            from ..schematic_attribute_display import target_record

            component = next(
                c for c in design["components"] if str(c.get("refdes")) == str(item["refdes"])
            )
            item["display_before"] = copy.deepcopy(target_record(component, str(item["name"])))
        result.append(item)
    return result


def _ordinary_edit_persistence(
    written, observed, projected, operations, project, *, before, save_metadata
):
    """Corroborate actual Save/Close/Open with an independent fresh snapshot.

    Requested engineering postconditions and the declared operation's protected
    fields are checked against the CLI's original snapshot. Separate complete
    record comparisons cover before-save/reopen/fresh observations. Neither
    this function nor those models claim unobserved wire or free-text geometry.
    """
    from ..models import normalise_project
    from ..native_verification import verify_native_changes

    lifecycle = written.get("lifecycle")
    present = any(
        key in written for key in ("lifecycle", "read_back_before_save", "read_back_after_reopen")
    )
    result = {
        "valid": False,
        "complete": False,
        "evidence_present": present,
        "lifecycle_verified": False,
        "protection_verified": False,
        "first_failure": None,
        "scope": "independent requested postconditions and operation-aware protected records; "
        "all observed records across before-save/reopen/fresh snapshots",
    }
    if not present:
        result["first_failure"] = "save_close_reopen_evidence_unavailable"
        return result
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}

    def document(value):
        if not isinstance(value, dict):
            return None
        designs = value.get("designs")
        if (
            value.get("open") is not True
            or value.get("status") != "observed"
            or value.get("source")
            not in {
                "IsProjectOpened/GetProjectData public getters",
                "dedicated_startup_host_Application_and_authenticated_ncalrpc",
            }
            or not isinstance(value.get("path"), str)
            or not value["path"]
            or not isinstance(designs, list)
            or not designs
            or any(not isinstance(name, str) or not name for name in designs)
            or len(set(designs)) != len(designs)
        ):
            return None
        try:
            return {
                "path": str(Path(value["path"]).resolve()),
                "designs": sorted(designs),
                "source": value["source"],
            }
        except (OSError, ValueError, RuntimeError):
            return None

    before_identity = document(lifecycle.get("before_identity"))
    reopened_identity = document(lifecycle.get("reopened_identity"))
    identity_valid = bool(
        before_identity is not None
        and before_identity == reopened_identity
        and Path(before_identity["path"]) == project.resolve()
        and type(lifecycle.get("before_pid")) is int
        and lifecycle["before_pid"] > 0
        and type(lifecycle.get("reopened_pid")) is int
        and lifecycle["before_pid"] == lifecycle["reopened_pid"]
    )
    lifecycle_valid = bool(
        identity_valid
        and written.get("domain") == "schematic"
        and written.get("saved") is True
        and written.get("save_acknowledged") is True
        and written.get("verification_source") == "native_getters_after_edit_save_close_open"
        and lifecycle.get("source") == "designer_save_close_reopen"
        and all(
            type(lifecycle.get(key)) is int and lifecycle[key] == 1
            for key in ("save_count", "close_count", "reopen_count")
        )
        and all(
            lifecycle.get(key) is True
            for key in ("save_acknowledged", "closed_observed", "close_returned")
        )
        and not written.get("error")
        and not written.get("failure")
    )
    result["lifecycle_verified"] = lifecycle_valid
    if not lifecycle_valid:
        result["first_failure"] = "save_close_reopen_lifecycle_incomplete"
        return result
    before_save, reopened = (
        written.get("read_back_before_save"),
        written.get("read_back_after_reopen"),
    )
    if not isinstance(before_save, dict) or not isinstance(reopened, dict):
        result["first_failure"] = "save_close_reopen_snapshots_unavailable"
        return result
    from ..native_com_adapter import _verify_designer_reopen_records
    from ..schematic_attribute_display import same_save_metadata
    from ..schematic_edit_protection import verify_schematic_edit_protection

    try:
        if not same_save_metadata(save_metadata, written.get("save_metadata")):
            result["first_failure"] = "preview_save_metadata_binding_changed"
            return result
        result["before_save_engineering"] = verify_schematic_edit_protection(
            before, before_save, operations, save_metadata=save_metadata
        )
        result["fresh_engineering"] = verify_schematic_edit_protection(
            before, observed, operations, save_metadata=save_metadata
        )
        result["before_save_postconditions"] = verify_native_changes(
            projected, normalise_project(before_save, observed=True), operations
        )
        result["reopened_postconditions"] = verify_native_changes(
            projected, normalise_project(reopened, observed=True), operations
        )
        result["reopened_records"] = _verify_designer_reopen_records(
            before_save, reopened, save_metadata=written.get("save_metadata")
        )
        result["fresh_records"] = _verify_designer_reopen_records(reopened, observed)
    except (CLIError, ValueError, KeyError, TypeError):
        result["first_failure"] = "required_durability_observation_unavailable"
        return result
    comparisons = (
        "before_save_engineering",
        "fresh_engineering",
        "before_save_postconditions",
        "reopened_postconditions",
        "reopened_records",
        "fresh_records",
    )
    result["complete"] = all(
        result[key].get("complete") is True
        for key in (
            "before_save_engineering",
            "fresh_engineering",
            "reopened_records",
            "fresh_records",
        )
    )
    result["protection_verified"] = all(
        result[key].get("protection_verified") is True
        for key in ("reopened_records", "fresh_records")
    ) and all(
        result[key].get("valid") is True for key in ("before_save_engineering", "fresh_engineering")
    )
    result["first_failure"] = next(
        (key for key in comparisons if result[key].get("valid") is not True), None
    )
    result["valid"] = bool(
        result["complete"] and result["protection_verified"] and result["first_failure"] is None
    )
    return result


def verify_ordinary_edit_durability(
    project, before, after, operations, written, *, save_metadata=None
):
    """Pure evidence algorithm; callers supply their own independent snapshots."""
    from ..changeset import apply_operations
    from ..native_verification import verify_native_changes

    try:
        projected, _ = apply_operations(before, operations)
        requested = verify_native_changes(projected, after, operations)
    except (CLIError, ValueError, KeyError, TypeError):
        return {
            "valid": False,
            "complete": False,
            "first_failure": "requested_postconditions_unavailable",
            "issues": [{"kind": "requested_postconditions_unavailable"}],
            "scope": "independent operation-aware edit and reopen durability",
            "evidence_present": bool(written.get("lifecycle")),
            "protection_verified": False,
        }
    result = _ordinary_edit_persistence(
        written,
        after,
        projected,
        operations,
        Path(project),
        before=before,
        save_metadata=save_metadata,
    )
    result["requested_postconditions"] = requested
    result["issues"] = list(requested["issues"])
    if not requested["valid"]:
        result["first_failure"] = "requested_postconditions"
    elif result["first_failure"]:
        result["issues"].append(
            {"kind": "edit_durability_unverified", "stage": result["first_failure"]}
        )
    result["valid"] = bool(result["valid"] and requested["valid"])
    return result


def edit(options: dict[str, Any]) -> dict[str, Any]:
    """Change a drawn schematic in place, then read it back and verify every change."""
    from .. import edit_operations, schematic_review
    from ..changeset import apply_operations
    from ..native_verification import verify_native_changes

    check_gate(options, "schematic edit")
    project = project_file(options, "schematic edit")
    operations = edit_operations.load(
        input_file(options, "file", "schematic edit", "the operations file")
    )
    backend = reader(options)
    design, _ = backend.load(str(project), domain="schematic")
    # every operation is projected onto the design as read: a missing part, pin or
    # net, or a part that exists already, is refused here, before anything is written
    projected, changes = apply_operations(design, operations)
    from ..schematic_attribute_display import (
        freeze_save_metadata,
        is_display_only_operations,
        verify_display_changes,
    )

    display_only = is_display_only_operations(operations)
    save_metadata = freeze_save_metadata(design)
    steps = native_operations(design, operations)
    protection_preflight = None
    if not display_only:
        from ..schematic_edit_protection import validate_supported

        protection_preflight = validate_supported(design, steps, save_metadata=save_metadata)
        if any(protection_preflight.get(key) is not True for key in ("valid", "complete")):
            raise CLIError(
                "E_VALIDATION",
                "the ordinary edit is unsupported or its native protection "
                "observations are incomplete",
                {
                    "stage": "ordinary_edit_preflight",
                    "validation": protection_preflight,
                    "write_attempted": False,
                    "native_request_dispatched": False,
                    "state_write_attempted": False,
                    "_untrusted": ["validation.issues"],
                },
            )
    drawing_review = schematic_review.native_edit(design, steps)
    scope = {
        "operation": "schematic_edit",
        "project": str(project),
        "design_hash": _snapshot_hash(design),
        "operations": operations,
        "save_metadata": save_metadata,
    }
    if options.get("dry_run"):
        preview = {
            "project": str(project),
            "operation_count": len(operations),
            "changes": changes,
            "steps": steps,
            "drawing_review": drawing_review,
            "save_metadata": save_metadata,
            **({"protection_preflight": protection_preflight} if not display_only else {}),
            "lifecycle": {
                "sequence": [
                    "protected_getter_readback",
                    "Documents.SaveAll",
                    "CloseProject",
                    "OpenProject",
                    "fresh_identity_and_protected_getters",
                ],
                "save_count": 1,
                "close_count": 1,
                "reopen_count": 1,
                "protected_records": "all observed components/UIDs/attributes/pins/nets/"
                "connections/unnamed records; the bound border time is separately reported",
                **({"operation_field_model_required": True} if not display_only else {}),
            },
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "the parts, nets and wires the operations name; the design is saved, "
                    "normally closed and reopened"
                ),
            },
        }
        return previewed(preview, scope)
    try:
        confirmed(options, scope)
    except CLIError as error:
        if (
            error.code == "E_CONFLICT"
            and error.message == "confirmation token does not match this operation"
        ):
            raise CLIError(
                error.code,
                error.message,
                {
                    **(error.details or {}),
                    "confirmation_layer": "schematic_handler",
                    "scope_diagnostic": _edit_scope_summary(scope),
                    "engineering_write_dispatched": False,
                },
            ) from error
        raise
    written = backend.invoke(
        "apply_changeset",
        {
            "project": str(project),
            "domain": "schematic",
            "operations": steps,
            "save": True,
            "save_metadata": save_metadata,
        },
        timeout_seconds=600.0,
    )
    # A returned call does not prove the change happened: read the design back.
    try:
        observed, _ = backend.load(str(project), domain="schematic")
    except CLIError as error:
        raise CLIError(
            "E_PROJECT_INVALID",
            "the edit was written, but reading the design back failed; inspect it before "
            "another write",
            {
                "stage": "read_back",
                "write_attempted": True,
                "cause": {"code": error.code, "details": error.details or {}},
                "applied": written.get("applied", []),
                "saved": written.get("saved"),
                "save_acknowledged": written.get("save_acknowledged"),
                "lifecycle": written.get("lifecycle"),
                "read_back_before_save": written.get("read_back_before_save"),
                "read_back_after_reopen": written.get("read_back_after_reopen"),
                "verification_source": written.get("verification_source"),
                "persistence_verified": False,
                "drawing_review": drawing_review,
                "_untrusted": [
                    "cause.details",
                    "applied",
                    "drawing_review",
                    "lifecycle",
                    "read_back_before_save",
                    "read_back_after_reopen",
                ],
            },
        ) from error
    verification = (
        verify_display_changes(design, observed, operations, save_metadata=save_metadata)
        if display_only
        else verify_native_changes(projected, observed, operations)
    )
    if display_only and verification["valid"] and written.get("persistence_verified") is True:
        verification["persistence_verified"] = True
        verification["not_checked"] = [
            item for item in verification["not_checked"] if item != "independent_reopen_persistence"
        ]
    durability = None
    if not display_only:
        durability = verify_ordinary_edit_durability(
            project,
            design,
            observed,
            operations,
            written,
            save_metadata=save_metadata,
        )
        verification["persistence_verified"] = bool(verification["valid"] and durability["valid"])
        verification["persistence_readback"] = durability
        if durability["evidence_present"] and not durability["valid"]:
            verification["valid"] = False
            verification["status"] = "failed" if durability["complete"] else "incomplete"
            verification["issues"].append(
                {"kind": "edit_durability_unverified", "stage": durability["first_failure"]}
            )
    metadata = observed.get("metadata") or {}
    read_back = {
        "method": "snapshot_after_apply",
        "snapshot_sha256": _snapshot_hash(observed),
        "scope": verification["scope"],
        "source": {
            "backend": backend.name,
            "application": metadata.get("source"),
            "application_version": metadata.get("application_version"),
            "adapter_version": metadata.get("adapter_version"),
        },
    }
    if not verification["valid"]:
        raise CLIError(
            "E_PROJECT_INVALID",
            "the design read back does not show every requested change; inspect it before "
            "another write",
            {
                "stage": "verify",
                "write_attempted": True,
                "verification": verification,
                "applied": written.get("applied", []),
                "saved": written.get("saved"),
                "save_acknowledged": written.get("save_acknowledged"),
                "lifecycle": written.get("lifecycle"),
                "read_back_before_save": written.get("read_back_before_save"),
                "read_back_after_reopen": written.get("read_back_after_reopen"),
                "verification_source": written.get("verification_source"),
                **({"first_failure": durability["first_failure"]} if durability else {}),
                "observed": observed,
                "read_back": read_back,
                "persistence_verified": False,
                "drawing_review": drawing_review,
                "_untrusted": [
                    "verification.issues",
                    "applied",
                    "observed",
                    "read_back.source",
                    "drawing_review",
                    "lifecycle",
                    "read_back_before_save",
                    "read_back_after_reopen",
                ],
            },
        )
    return {
        "project": str(project),
        "domain": written.get("domain"),
        "applied": written.get("applied", []),
        "saved": written.get("saved"),
        "save_acknowledged": written.get("save_acknowledged"),
        "lifecycle": written.get("lifecycle"),
        "read_back_before_save": written.get("read_back_before_save"),
        "read_back_after_reopen": written.get("read_back_after_reopen"),
        "verification_source": written.get("verification_source"),
        "save_metadata": save_metadata,
        "verification": verification,
        **(
            {"metadata_changes": verification["metadata_changes"], "save_metadata": save_metadata}
            if display_only
            else {}
        ),
        "observed": observed,
        "read_back": read_back,
        "persistence_verified": bool(
            written.get("persistence_verified") is True if display_only else durability["valid"]
        ),
        "drawing_review": drawing_review,
        "_untrusted": [
            "project",
            "applied",
            "observed",
            "read_back.source",
            "drawing_review",
            "lifecycle",
            "read_back_before_save",
            "read_back_after_reopen",
            *(["save_metadata", "metadata_changes"] if display_only else []),
            "verification_source",
            "save_metadata",
        ],
    }


# -- reading and checking -----------------------------------------------------------


def net_kind(name: str) -> str:
    from ..review_engine import is_ground_net, is_power_net

    if is_ground_net(name):
        return "ground"
    if is_power_net(name):
        return "power"
    return "signal"


def _read(options: dict[str, Any], command: str) -> tuple[Path, dict[str, Any]]:
    project = project_file(options, command)
    design, _ = reader(options).load(str(project), domain="schematic")
    return project, design


def components(options: dict[str, Any]) -> dict[str, Any]:
    _, design = _read(options, "schematic parts")
    return page(design, design["components"], options)


def nets(options: dict[str, Any]) -> dict[str, Any]:
    _, design = _read(options, "schematic nets")
    pins: dict[str, list[str]] = {}
    for connection in design.get("connections", []):
        pins.setdefault(str(connection.get("net")), []).extend(
            str(pin) for pin in connection.get("pins", [])
        )
    for component in design["components"]:
        for pin in component.get("pins") or []:
            if isinstance(pin, dict) and pin.get("net") and pin.get("number") is not None:
                reference = f"{component['refdes']}.{pin['number']}"
                members = pins.setdefault(str(pin["net"]), [])
                if reference not in members:
                    members.append(reference)
    rows = [
        {
            "name": net["name"],
            "kind": net_kind(str(net["name"])),
            "unnamed": bool(net.get("unnamed")),
            "sheets": net.get("sheets", []),
            "pins": sorted(set(pins.get(str(net["name"]), []))),
        }
        for net in design["nets"]
    ]
    return page(design, rows, options)


def sheets(options: dict[str, Any]) -> dict[str, Any]:
    _, design = _read(options, "schematic sheets")
    return page(design, design.get("sheets", []), options)


def check(options: dict[str, Any]) -> dict[str, Any]:
    """Our netlist rules and Designer's own verification, merged by severity."""
    from ..review_engine import run_review

    project, design = _read(options, "schematic checks run")
    verification = native().invoke("verify", {"project": str(project)}, timeout_seconds=300.0)
    report = run_review(design, list(verification.get("findings") or []))
    findings = report["findings"]
    offset = min(len(findings), int(options.get("offset") or 0))
    limit = options.get("limit")
    end = len(findings) if limit is None else min(len(findings), offset + int(limit))
    report["findings"] = findings[offset:end]
    report["count"] = len(report["findings"])
    report["offset"] = offset
    report["next_offset"] = end if end < len(findings) else None
    report["has_more"] = end < len(findings)
    report["designer_verification"] = {
        "scheme": verification.get("scheme"),
        "findings": len(verification.get("findings") or []),
        "logs": verification.get("logs"),
    }
    report["_untrusted"] = [*report.get("_untrusted", []), "designer_verification.logs"]
    return report


# -- showing and exporting -------------------------------------------------------------


def package(options: dict[str, Any]) -> dict[str, Any]:
    from ..project_file import pdb_path_repairs, pdb_registration_repairs

    check_gate(options, "schematic package")
    project = project_file(options, "schematic package")
    repairs = pdb_path_repairs(project)
    registrations = pdb_registration_repairs(project)
    scope = {
        "operation": "schematic_package",
        "project": str(project),
        "project_sha256": hashlib.sha256(project.read_bytes()).hexdigest(),
        "registration_repairs": repairs,
        "partition_registrations": registrations,
    }
    if options.get("dry_run"):
        return previewed(
            {
                "project": str(project),
                "level": "plan",
                "native_observed": False,
                "changes": [
                    {"action": "register_existing_parts_partition", **item}
                    for item in registrations
                ]
                + [{"action": "repair_parts_database_path", **item} for item in repairs]
                + [{"action": "package_existing_schematic"}],
                "limitations": [
                    (
                        "Library authoring is separate. Packaging completion requires native "
                        "outputs/logs."
                    )
                ],
            },
            scope,
        )
    confirmed(options, scope)
    return native().invoke(
        "package",
        {
            "project": str(project),
            "registration_repairs": repairs,
            "partition_registrations": registrations,
        },
        timeout_seconds=900.0,
    )


def show(options: dict[str, Any]) -> dict[str, Any]:
    """Activate a sheet, fit it and raise Designer's window (the design is unchanged)."""
    project = project_file(options, "schematic view")
    sheet = integer(options, "sheet", 1)
    if sheet is None or sheet < 1:
        raise CLIError("E_VALIDATION", "--sheet must be a positive integer", {"sheet": sheet})
    output = output_file(options, "schematic view", ".png")
    params: dict[str, Any] = {"project": str(project), "sheet": sheet}
    if output is not None:
        params["output"] = str(output)
    return native().invoke("show", params, timeout_seconds=180.0)


def export(options: dict[str, Any]) -> dict[str, Any]:
    """The schematic as a new PDF through sch2pdf (a report, not a design change)."""
    project = project_file(options, "schematic export")
    output = output_file(options, "schematic export", ".pdf")
    params: dict[str, Any] = {"project": str(project)}
    if output is not None:
        params["output"] = str(output)
    color = integer(options, "color")
    if color is not None:
        if not 0 <= color <= 4:
            raise CLIError("E_VALIDATION", "--color is 0 to 4", {"color": color})
        params["color"] = color
    if text(options, "schematic"):
        params["schematic"] = text(options, "schematic")
    if options.get("replace"):
        params["replace"] = True
    if options.get("_artifact_generation"):
        params["artifact_generation"] = options["_artifact_generation"]
    if isinstance(options.get("_pdf_expectation"), dict):
        params["_pdf_expectation"] = options["_pdf_expectation"]
    return native().invoke("export_pdf", params, timeout_seconds=600.0)
