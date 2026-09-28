"""schematic render | draw | edit | check | components | nets | sheets | show | export."""

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
    from .. import schematic_layout, schematic_render
    from .library import design_library

    design_file, design = read_design(options, "schematic render")
    output = output_file(options, "schematic render", ".png", required=True)
    assert output is not None
    project = project_file(options, "schematic render") if options.get("project") else None
    parts = design_library(options, project, design, "schematic render")
    try:
        plan = schematic_layout.plan(design, parts)
    except schematic_layout.DesignError as exc:
        raise CLIError(
            "E_VALIDATION", f"design cannot be drawn: {exc}", {"design": str(design_file)}
        ) from exc
    planned = [int(sheet["number"]) for sheet in plan.sheets]
    chosen = sheets_option(options, planned)
    scale = number(options, "scale", schematic_render.SCALE)
    assert scale is not None
    if not 0.5 <= scale <= 6:
        raise CLIError("E_VALIDATION", "--scale must be between 0.5 and 6")
    try:
        import PIL  # noqa: F401
    except ImportError as exc:
        raise CLIError(
            "E_CONFIG",
            "schematic render draws with Pillow, which is not installed",
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
        "summary": {"sheets": len(pictures), "issues": len(issues)},
        "_untrusted": ["design", "pictures", "issues"],
    }


def draw(options: dict[str, Any]) -> dict[str, Any]:
    """Plan a schematic from a design description, then draw it.

    The plan is pure Python, so the dry run shows the sheets, parts, netlist and
    convention issues without Designer. The confirmed run wipes and redraws every
    listed sheet, reopens the project and reports whether the netlist read back
    matches the plan.
    """
    from .. import schematic_layout
    from .library import design_library

    check_gate(options, "schematic draw")
    project = project_file(options, "schematic draw")
    design_file, design = read_design(options, "schematic draw")
    parts = design_library(options, project, design, "schematic draw")
    try:
        params = schematic_layout.plan_to_params(design, str(project), parts)
    except schematic_layout.DesignError as exc:
        raise CLIError(
            "E_VALIDATION", f"design cannot be drawn: {exc}", {"design": str(design_file)}
        ) from exc
    summary = params["summary"]
    planned = [int(sheet["number"]) for sheet in summary["sheets"]]
    chosen = sheets_option(options, planned)
    if chosen is not None:
        # Every sheet ends in a save, so the sheets a failed draw completed are on
        # disk: redrawing only the rest resumes it. The netlist read back at the end
        # still covers the whole design.
        params["ops"] = ops_for_sheets(params["ops"], chosen)
    drawing = chosen if chosen is not None else planned
    params["design_sheets"] = planned
    seconds = pace(options, "schematic draw")
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
    require_dangerous(options, "schematic draw wipes every sheet it draws, hand edits included")
    backend = native()
    confirmed(options, scope)
    request = {key: value for key, value in params.items() if key != "summary"}
    result = backend.invoke("draw", request, timeout_seconds=1800.0)
    result["summary"] = summary
    result["_untrusted"] = [*(result.get("_untrusted") or []), "summary"]
    return result


# -- editing ----------------------------------------------------------------------


def _snapshot_hash(project: dict[str, Any]) -> str:
    canonical = json.dumps(project, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        result.append(item)
    return result


def edit(options: dict[str, Any]) -> dict[str, Any]:
    """Change a drawn schematic in place, then read it back and verify every change."""
    from .. import edit_operations
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
    steps = native_operations(design, operations)
    scope = {
        "operation": "schematic_edit",
        "project": str(project),
        "design_hash": _snapshot_hash(design),
        "operations": operations,
    }
    if options.get("dry_run"):
        preview = {
            "project": str(project),
            "operation_count": len(operations),
            "changes": changes,
            "steps": steps,
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "the parts, nets and wires the operations name; the design is saved"
                ),
            },
        }
        return previewed(preview, scope)
    confirmed(options, scope)
    written = backend.invoke(
        "apply_changeset",
        {"project": str(project), "domain": "schematic", "operations": steps, "save": True},
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
                "_untrusted": ["cause.details", "applied"],
            },
        ) from error
    verification = verify_native_changes(projected, observed, operations)
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
                "_untrusted": ["verification.issues", "applied"],
            },
        )
    return {
        "project": str(project),
        "applied": written.get("applied", []),
        "saved": written.get("saved"),
        "verification": verification,
        "_untrusted": ["project", "applied"],
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
    _, design = _read(options, "schematic components")
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

    project, design = _read(options, "schematic check")
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


def show(options: dict[str, Any]) -> dict[str, Any]:
    """Activate a sheet, fit it and raise Designer's window (the design is unchanged)."""
    project = project_file(options, "schematic show")
    sheet = integer(options, "sheet", 1)
    if sheet is None or sheet < 1:
        raise CLIError("E_VALIDATION", "--sheet must be a positive integer", {"sheet": sheet})
    output = output_file(options, "schematic show", ".png")
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
    return native().invoke("export_pdf", params, timeout_seconds=600.0)
