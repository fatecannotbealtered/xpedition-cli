"""pcb create | annotate | outline | holes | rules | arrange | move | labels | route |
trace | via | stitch | unroute | pour | info | geometry | render | show | metrics |
check | export."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..errors import CLIError
from .common import (
    board_file,
    check_gate,
    comma_list,
    confirmed,
    continue_on_error,
    input_file,
    integer,
    native,
    number,
    output_file,
    pace,
    previewed,
    project_file,
    read_design,
    read_json_file,
    reader,
    require_dangerous,
    text,
    write_json_output,
)


def _risk(tier: str, blast_radius: str) -> dict[str, str]:
    return {"tier": tier, "blast_radius": blast_radius}


# -- the board -------------------------------------------------------------------


def create(options: dict[str, Any]) -> dict[str, Any]:
    """The project's board from a layout template, through JobWizard.

    The preview reads the .prj (pure text); the confirmed run copies the template into
    the central library when it is missing, lists the board design first, runs
    `JobWizard -createnew` and registers the cell partitions forward annotation needs.
    """
    from .. import project_file as prj

    check_gate(options, "pcb create")
    path = project_file(options, "pcb create")
    canonical = str(path)
    listed = prj.designs(path.read_text(encoding="utf-8", errors="replace"))
    design = prj.board_design(listed, text(options, "design") or None)
    if design is None:
        raise CLIError(
            "E_NOT_FOUND",
            "the project lists no PCB design",
            {"project": canonical, "designs": [item.name for item in listed]},
        )
    replace = bool(options.get("replace"))
    if design.pcb_path and not replace:
        raise CLIError(
            "E_CONFLICT",
            "the design already names a board",
            {"design": design.name, "pcb": design.pcb_path, "hint": "pass --replace"},
        )
    template = text(options, "template") or prj.DEFAULT_LAYOUT_TEMPLATE
    name = text(options, "name") or path.stem
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
    if options.get("dry_run"):
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
                {"action": "register_cells_in_project", "list": prj.CELL_LIST},
            ],
            "dangerous": replace,
            "risk": _risk(
                "T2" if replace else "T1",
                (
                    "the design's whole existing layout -- placement, routing, pours, output "
                    "setups -- is zipped to PCB-backup-<timestamp>.zip beside the project and the "
                    "PCB folder then deleted; a Layout process still holding a file in it is "
                    "ended; then "
                    if replace
                    else ""
                )
                + "a new PCB folder beside the .prj; the .prj gains the board path, the "
                "template name and a cell-library list; Designer's project is closed and "
                "reopened meanwhile",
            ),
        }
        return previewed(preview, scope)
    if replace:
        require_dangerous(
            options, "pcb create --replace removes the board's layout and ends Layout processes"
        )
    backend = native()
    confirmed(options, scope)
    return backend.invoke(
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


def annotate(options: dict[str, Any]) -> dict[str, Any]:
    """Forward-annotate the packaged schematic into the board.

    Layout runs the packager, Database Load and the netload itself through its
    Project Integration object; the CLI opens the board (answering Layout's own
    prompts), runs it, reads `ForwardAnnotation.txt` back and saves the board.
    """
    from .. import project_file as prj

    check_gate(options, "pcb annotate")
    canonical = board_file(options, "pcb annotate")
    path = Path(canonical)
    pcb = canonical if path.suffix.lower() == ".pcb" else None
    if pcb is None:
        listed = prj.designs(path.read_text(encoding="utf-8", errors="replace"))
        design = prj.board_design(listed, text(options, "design") or None)
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
    unroute = bool(options.get("unroute"))
    scope = {"operation": "pcb_annotate", "project": canonical, "unroute": unroute}
    if options.get("dry_run"):
        preview = {
            "project": canonical,
            "pcb": pcb,
            "changes": [
                {"action": "open_board", "pcb": pcb},
                *([{"action": "delete_traces_and_vias"}] if unroute else []),
                {"action": "forward_annotate"},
                {"action": "save_board"},
            ],
            "dangerous": unroute,
            "risk": _risk(
                "T2" if unroute else "T1",
                ("every trace and via is deleted first, not archived; " if unroute else "")
                + "the board's components and nets are replaced by the packaged schematic's "
                "and Layout saves the board",
            ),
        }
        return previewed(preview, scope)
    if unroute:
        require_dangerous(options, "pcb annotate --unroute deletes the board's routing first")
    backend = native()
    confirmed(options, scope)
    return backend.invoke(
        "forward_annotate",
        {"project": canonical, "start": True, "unroute": unroute},
        timeout_seconds=900.0,
    )


def _planned_write(
    options: dict[str, Any],
    command: str,
    method: str,
    request: dict[str, Any],
    scope: dict[str, Any],
    preview: Any,
    *,
    plan_timeout: float = 420.0,
    apply_timeout: float = 420.0,
) -> dict[str, Any]:
    """The common shape of a Layout write: the adapter plans it against the board as it
    is (apply false), the preview is built from that plan, and the confirmed run asks
    the adapter to apply it."""
    check_gate(options, command)
    backend = native()
    if options.get("dry_run"):
        current = backend.invoke(method, {**request, "apply": False}, timeout_seconds=plan_timeout)
        return previewed(preview(current), scope)
    confirmed(options, scope)
    return backend.invoke(method, {**request, "apply": True}, timeout_seconds=apply_timeout)


def outline(options: dict[str, Any]) -> dict[str, Any]:
    canonical = board_file(options, "pcb outline")
    width = number(options, "width") or 0.0
    height = number(options, "height") or 0.0
    if width <= 0 or height <= 0:
        raise CLIError("E_VALIDATION", "--width and --height must be positive millimetres")
    radius = number(options, "radius", 0.0) or 0.0
    if radius < 0 or radius >= min(width, height) / 2:
        raise CLIError("E_VALIDATION", "--radius must be below half of the shorter side")
    request = {
        "project": canonical,
        "width": width,
        "height": height,
        "radius": radius,
        "start": True,
    }
    scope = {
        "operation": "pcb_outline",
        **{k: request[k] for k in ("project", "width", "height", "radius")},
    }
    return _planned_write(
        options,
        "pcb outline",
        "board_outline",
        request,
        scope,
        lambda current: {
            "project": canonical,
            "pcb": current.get("pcb"),
            "before": current.get("before"),
            "requested": {"width": width, "height": height, "radius": radius},
            "changes": [
                {"action": "put_board_outline", "width": width, "height": height, "radius": radius},
                {"action": "save_board"},
            ],
            "risk": _risk(
                "T1",
                "the board outline becomes the requested rectangle from the origin; parts "
                "outside it stay where they are",
            ),
        },
    )


def holes(options: dict[str, Any]) -> dict[str, Any]:
    canonical = board_file(options, "pcb holes")
    diameter = number(options, "diameter", 2.2) or 0.0
    inset = number(options, "inset", 3.5) or 0.0
    if diameter <= 0 or inset <= 0:
        raise CLIError("E_VALIDATION", "--diameter and --inset must be positive millimetres")
    replace = bool(options.get("replace"))
    request = {
        "project": canonical,
        "diameter": diameter,
        "inset": inset,
        "replace": replace,
        "start": True,
    }
    scope = {
        "operation": "pcb_holes",
        **{k: request[k] for k in ("project", "diameter", "inset", "replace")},
    }
    return _planned_write(
        options,
        "pcb holes",
        "mounting_holes",
        request,
        scope,
        lambda current: {
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
            "risk": _risk(
                "T1",
                "non-plated mounting holes are added at the corners of the outline and the "
                "board is saved; parts already there are not moved",
            ),
        },
    )


def rules(options: dict[str, Any]) -> dict[str, Any]:
    canonical = board_file(options, "pcb rules")
    class_name = text(options, "class")
    nets = comma_list(options, "nets")
    width = number(options, "width")
    minimum = number(options, "min")
    expansion = number(options, "expansion")
    if width is None or width <= 0:
        raise CLIError("E_VALIDATION", "--width must be positive millimetres")
    if (minimum is not None and minimum <= 0) or (expansion is not None and expansion <= 0):
        raise CLIError("E_VALIDATION", "--min and --expansion must be positive millimetres")
    request = {
        "project": canonical,
        "class": class_name,
        "nets": nets,
        "width": width,
        "minimum": minimum,
        "expansion": expansion,
        "start": True,
    }
    scope = {
        "operation": "pcb_rules",
        "project": canonical,
        "class": class_name,
        "nets": ",".join(nets),
        "width": width,
        "min": minimum,
        "expansion": expansion,
    }
    return _planned_write(
        options,
        "pcb rules",
        "net_rules",
        request,
        scope,
        lambda current: {
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
            "risk": _risk(
                "T1",
                "the constraint database gains or changes one net class and its trace widths "
                "on every layer; Layout re-reads its constraints",
            ),
        },
        plan_timeout=300.0,
        apply_timeout=600.0,
    )


# -- placement -----------------------------------------------------------------------


def _design_zones(options: dict[str, Any]) -> dict[str, str]:
    """Zone labels for the arrange's groups, from the design file's `sheets[].zone`.

    The arrange groups parts by the sheet their reference designator encodes (C201 is
    group 2), which is not always the sheet they are drawn on: the demo numbers its
    first sheet of parts 1xx and draws it on sheet 2. So each sheet's label goes to the
    group most of its parts fall in, not to the group with the sheet's own number.
    """
    from collections import Counter

    from .. import schematic_layout
    from ..board_layout import group_of

    if not options.get("design"):
        return {}
    _design_file, design = read_design(options, "pcb arrange")
    labels: dict[int, str] = {}
    for index, sheet in enumerate(design.get("sheets") or [], 1):
        if isinstance(sheet, dict) and str(sheet.get("zone") or "").strip():
            try:
                labels[int(sheet.get("number") or index)] = str(sheet["zone"]).strip()
            except (TypeError, ValueError):
                continue
    try:
        parts = schematic_layout.plan(design).parts
    except schematic_layout.DesignError:
        parts = []
    by_sheet: dict[int, list[str]] = {}
    for part in parts:
        by_sheet.setdefault(int(part.get("sheet") or 0), []).append(str(part["refdes"]))
    zones: dict[str, str] = {}
    for sheet_number, label in sorted(labels.items()):
        refs = by_sheet.get(sheet_number)
        if refs:
            key = Counter(group_of(ref) for ref in refs).most_common(1)[0][0]
        else:
            continue  # a sheet with no parts has no group on the board
        zones.setdefault(key, label)
    return zones


def arrange(options: dict[str, Any]) -> dict[str, Any]:
    """A first placement of the unplaced parts, in clusters by sheet.

    Both runs need Layout: the plan depends on each footprint's measured size and on
    the outline. The token binds the plan's digest; the confirmed run plans again, so
    a board that changed in between no longer matches the token.
    """
    check_gate(options, "pcb arrange")
    canonical = board_file(options, "pcb arrange")
    include_all = bool(options.get("all"))
    zones = _design_zones(options)
    backend = native()
    request = {"project": canonical, "all": include_all, "zones": zones, "start": True}
    result = backend.invoke(
        "arrange_components", {**request, "apply": False}, timeout_seconds=900.0
    )
    digest = str(result.get("digest") or "")
    routing = result.get("routing") or {}
    # a confirmed arrange deletes every trace and via first
    routed = bool(int(routing.get("traces") or 0) + int(routing.get("vias") or 0))
    scope = {"operation": "pcb_arrange", "project": canonical, "all": include_all, "digest": digest}
    if options.get("dry_run"):
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
            "routing": routing,
            "dangerous": routed,
            "changes": [
                {"action": "delete_traces_and_vias", **routing},
                {"action": "place_components", "count": len(result.get("plan") or [])},
                {"action": "write_zone_labels", "count": len(result.get("labels") or [])},
                {"action": "save_board"},
            ],
            "risk": _risk(
                "T2" if routed else "T1",
                ("every trace and via on the board is deleted, not archived; " if routed else "")
                + "every listed part is placed on the top side at the planned position and "
                "the board is saved; parts already placed are left alone unless --all",
            ),
        }
        return previewed(preview, scope)
    if routed:
        require_dangerous(options, "pcb arrange deletes the board's traces and vias first")
    confirmed(options, scope)
    return backend.invoke(
        "arrange_components",
        {**request, "apply": True, "digest": digest, "pace": pace(options, "pcb arrange")},
        timeout_seconds=900.0,
    )


def _move_one(options: dict[str, Any], canonical: str) -> dict[str, Any]:
    from ..native_com_adapter import AdapterError, parse_points

    refdes = text(options, "refdes")
    if not refdes or options.get("to") is None:
        raise CLIError(
            "E_USAGE", "pcb move requires --refdes U1 --to x,y [--rotate DEG], or --file"
        )
    try:
        x, y = parse_points(options.get("to"), 1)[0]
    except AdapterError as exc:
        raise CLIError("E_VALIDATION", "--to takes x,y in millimetres") from exc
    rotation = number(options, "rotate")
    request = {
        "project": canonical,
        "refdes": refdes,
        "x": x,
        "y": y,
        "rotation": rotation,
        "start": True,
    }
    scope = {
        "operation": "pcb_move",
        "project": canonical,
        "refdes": refdes,
        "to": f"{x},{y}",
        "rotate": rotation,
    }
    return _planned_write(
        options,
        "pcb move",
        "move_component",
        request,
        scope,
        lambda plan: {
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
            "risk": _risk(
                "T1",
                "one part moves (its traces stay where they were and may need rerouting); "
                "Layout refuses a position that touches another part; the board is saved",
            ),
        },
        plan_timeout=600.0,
        apply_timeout=600.0,
    )


def _move_task(options: dict[str, Any], canonical: str) -> dict[str, Any]:
    """A selection moved by translate, rotate, align and distribute steps, read back."""
    from ..placement import plan_placement, read_json, same_position, validate_request

    request = validate_request(
        read_json(str(input_file(options, "file", "pcb move", "the task file")))
    )
    backend = native()
    params = {"project": canonical, "request": request}
    preview = backend.invoke("placement_batch", {**params, "apply": False}, timeout_seconds=120.0)
    try:
        # Do not sign an arbitrary adapter-generated digest or a different task.
        rows = [row["before"] for row in preview["results"]]
        reconstructed = plan_placement(request, rows)
        if preview["request"] != reconstructed["request"]:
            raise ValueError("different request")
        for key in ("state_digest", "results", "summary"):
            if preview[key] != reconstructed[key]:
                raise ValueError("inconsistent preview")
        if not isinstance(preview["pcb"], str) or not preview["pcb"]:
            raise ValueError("missing board")
    except (CLIError, KeyError, TypeError, ValueError) as error:
        raise CLIError("E_SERVER", "the placement preview is incomplete or inconsistent") from error
    scope = {
        "operation": "pcb_move_task",
        "project": canonical,
        "pcb": preview["pcb"],
        "request": request,
        "state_digest": preview["state_digest"],
    }
    if options.get("dry_run"):
        preview["risk"] = {
            "tier": "T1",
            "blast_radius": (
                "the selected parts move and the board is saved; traces are not rerouted"
            ),
            "partial_failure": (
                "parts moved before a failure stay moved and the current part may be unplaced"
            ),
        }
        return previewed(preview, scope)
    # A fresh preview binds identities, sides, protection and positions; the adapter
    # checks the digest again under its batch lock right before editing.
    confirmed(options, scope)
    try:
        result = backend.invoke(
            "placement_batch",
            {**params, "apply": True, "state_digest": preview["state_digest"]},
            timeout_seconds=600.0,
        )
    except CLIError as error:
        if error.code == "E_CONFLICT" and (error.details or {}).get("write_attempted") is False:
            raise
        raise CLIError(
            "E_PROJECT_INVALID",
            "the placement outcome is unknown; inspect the board before another write",
            {
                "stage": "submit",
                "write_attempted": True,
                "outcome": "unknown",
                "cause_code": error.code,
                "retry_safe": False,
            },
        ) from error
    if (
        not isinstance(result, dict)
        or result.get("outcome") != "complete"
        or not isinstance(result.get("verification"), dict)
        or result["verification"].get("valid") is not True
    ):
        raise CLIError(
            "E_PROJECT_INVALID",
            "the placement did not complete; inspect the per-item outcomes",
            {"stage": "execution", "report": result, "retry_safe": False, "_untrusted": ["report"]},
        )
    results = result.get("results")
    if (
        not isinstance(results, list)
        or len(results) != len(request["selection"])
        or any(
            not isinstance(row, dict) or row.get("id") != name or row.get("ok") is not True
            for row, name in zip(results, request["selection"], strict=True)
        )
    ):
        raise CLIError(
            "E_PROJECT_INVALID",
            "the placement response did not account for every selected part",
            {"stage": "response", "write_attempted": True, "outcome": "unknown"},
        )
    for planned, returned in zip(preview["results"], results, strict=True):
        observed = returned.get("observed")
        if not isinstance(observed, dict) or not same_position(planned["target"], observed):
            raise CLIError(
                "E_PROJECT_INVALID",
                "the placement response did not verify the requested target",
                {"stage": "response", "write_attempted": True},
            )
    if preview["summary"]["changed_count"] and (
        result.get("write_attempted") is not True or result.get("drc_restored") is not True
    ):
        raise CLIError(
            "E_PROJECT_INVALID",
            "the placement ran but its DRC restoration is unconfirmed",
            {"stage": "response", "write_attempted": True},
        )
    if result.get("write_attempted") is True and result.get("saved") is not True:
        raise CLIError(
            "E_PROJECT_INVALID",
            "the placement save is unconfirmed",
            {"stage": "save", "write_attempted": True},
        )
    return result


def move(options: dict[str, Any]) -> dict[str, Any]:
    canonical = board_file(options, "pcb move")
    if options.get("file") is not None:
        if any(options.get(name) is not None for name in ("refdes", "to", "rotate")):
            raise CLIError("E_USAGE", "pcb move takes --file or --refdes/--to/--rotate, not both")
        check_gate(options, "pcb move")
        return _move_task(options, canonical)
    return _move_one(options, canonical)


def labels(options: dict[str, Any]) -> dict[str, Any]:
    canonical = board_file(options, "pcb labels")
    gap = number(options, "gap")
    if gap is not None and gap < 0:
        raise CLIError("E_VALIDATION", "--gap must not be negative")
    request: dict[str, Any] = {"project": canonical, "start": True}
    if gap is not None:
        request["gap"] = gap
    scope = {"operation": "pcb_labels", "project": canonical, "gap": gap}
    return _planned_write(
        options,
        "pcb labels",
        "tidy_labels",
        request,
        scope,
        lambda plan: {
            "project": canonical,
            "pcb": plan.get("pcb"),
            "labels": plan.get("labels"),
            "moved": plan.get("moved"),
            "unplaced": plan.get("unplaced"),
            "changes": [
                {"action": "move_silkscreen_designators", "count": plan.get("moved")},
                {"action": "save"},
            ],
            "risk": _risk(
                "T1",
                "the silkscreen designators move; nothing electrical changes; the board is saved",
            ),
        },
        plan_timeout=600.0,
        apply_timeout=600.0,
    )


# -- routing --------------------------------------------------------------------------


def route(options: dict[str, Any]) -> dict[str, Any]:
    """Layout's autorouter passes over the board, then save; `--passes` names the passes
    and their effort, route:1-5,viamin:1-3,smooth:1-3 by default."""
    check_gate(options, "pcb route")
    canonical = board_file(options, "pcb route")
    passes = text(options, "passes") or "route:1-5,viamin:1-3,smooth:1-3"
    layers = text(options, "layers")
    unroute = bool(options.get("unroute"))
    request = {
        "project": canonical,
        "passes": passes,
        "layers": layers,
        "unroute": unroute,
        "start": True,
    }
    scope = {
        "operation": "pcb_route",
        "project": canonical,
        "passes": passes,
        "layers": layers,
        "unroute": unroute,
    }
    backend = native()
    if options.get("dry_run"):
        current = backend.invoke("route_board", {**request, "apply": False}, timeout_seconds=420.0)
        preview = {
            "project": canonical,
            "pcb": current.get("pcb"),
            "passes": current.get("passes"),
            "before": current.get("before"),
            "unrouted_before": current.get("unrouted_before"),
            "changes": [
                *([{"action": "delete_traces_and_vias"}] if unroute else []),
                {"action": "run_route_passes", "count": len(current.get("passes") or [])},
                {"action": "save_board"},
            ],
            "dangerous": unroute,
            "risk": _risk(
                "T2" if unroute else "T1",
                ("every trace and via is deleted first, not archived; " if unroute else "")
                + "traces and vias are added or changed on every net the passes touch; the "
                "board is saved",
            ),
        }
        return previewed(preview, scope)
    if unroute:
        require_dangerous(options, "pcb route --unroute deletes the board's routing first")
    confirmed(options, scope)
    return backend.invoke("route_board", {**request, "apply": True}, timeout_seconds=1800.0)


def _geometry_model(
    options: dict[str, Any], command: str, *, required: bool
) -> dict[str, Any] | None:
    """The board geometry file named by --geometry."""
    if not options.get("geometry"):
        if required:
            raise CLIError(
                "E_USAGE", f"{command} requires --geometry board.json (from pcb geometry)"
            )
        return None
    path = input_file(options, "geometry", command, "the geometry file")
    model = read_json_file(path, "the geometry file")
    if isinstance(model, dict) and isinstance(model.get("data"), dict):
        model = model["data"].get("model", model["data"])
    if not isinstance(model, dict) or "pads" not in model:
        raise CLIError("E_VALIDATION", f"{command}: --geometry is the file pcb geometry writes")
    return model


def _routing_items(options: dict[str, Any], command: str) -> list[dict[str, Any]]:
    """The trace/via items: one from the flags, or a plan file's."""
    from ..native_com_adapter import AdapterError, plan_items

    try:
        if options.get("file") is not None:
            data = read_json_file(
                input_file(options, "file", command, "the plan file"), "the plan file"
            )
            return plan_items(data)
        if command == "pcb via":
            item: dict[str, Any] = {
                "kind": "via",
                "net": options.get("net"),
                "at": options.get("at"),
                "padstack": options.get("padstack"),
            }
        else:
            if not text(options, "net") or options.get("points") is None:
                raise CLIError(
                    "E_USAGE", "pcb trace requires --net and --points, or --file PLAN.json"
                )
            item = {
                "kind": "trace",
                "net": options.get("net"),
                "layer": integer(options, "layer", 1),
                "width": number(options, "width", 0.254),
                "points": options.get("points"),
            }
        return plan_items({"items": [item]})
    except AdapterError as exc:
        raise CLIError(exc.code, exc.message, exc.details) from exc


def _hand_route(options: dict[str, Any], command: str) -> dict[str, Any]:
    check_gate(options, command)
    canonical = board_file(options, command)
    items = _routing_items(options, command)
    model = _geometry_model(options, command, required=False)
    problems: list[str] = []
    if model is not None:
        from .. import routing_plan

        problems = routing_plan.check_plan(items, routing_plan.Board(model))
        if problems and not options.get("dangerous"):
            raise CLIError(
                "E_VALIDATION",
                f"{command}: the plan fails the clearance check against the geometry; fix it "
                "or confirm with --dangerous",
                {"problems": problems[:40], "count": len(problems)},
            )
    seconds = pace(options, command)
    digest = hashlib.sha256(
        json.dumps(items, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    scope = {"operation": "pcb_trace", "project": canonical, "items": digest[:16]}
    request: dict[str, Any] = {"project": canonical, "items": items, "start": True}
    if seconds:
        request["pace"] = seconds
    backend = native()
    if options.get("dry_run"):
        plan = backend.invoke("hand_route", {**request, "apply": False}, timeout_seconds=600.0)
        preview = {
            "project": canonical,
            "pcb": plan.get("pcb"),
            "items": plan.get("items"),
            "nets": plan.get("nets"),
            "opens_before": plan.get("opens_before"),
            "warnings": plan.get("warnings"),
            "checked_against_geometry": model is not None,
            "problems": problems,
            "changes": [
                {
                    "action": "put_traces_and_vias",
                    "traces": sum(1 for item in items if item["kind"] == "trace"),
                    "vias": sum(1 for item in items if item["kind"] == "via"),
                },
                {"action": "regenerate_planes"},
                {"action": "save"},
            ],
            "risk": _risk(
                "T1",
                "the board gains the traces and vias listed; Layout refuses any that violate a "
                "clearance; the board is saved",
            ),
        }
        return previewed(preview, scope)
    confirmed(options, scope)
    return backend.invoke("hand_route", {**request, "apply": True}, timeout_seconds=1200.0)


def trace(options: dict[str, Any]) -> dict[str, Any]:
    return _hand_route(options, "pcb trace")


def via(options: dict[str, Any]) -> dict[str, Any]:
    return _hand_route(options, "pcb via")


def stitch(options: dict[str, Any]) -> dict[str, Any]:
    """The plan for a via beside every surface-mount pad of a net, from the geometry file."""
    from .. import routing_plan

    model = _geometry_model(options, "pcb stitch", required=True)
    assert model is not None
    net = text(options, "net") or "GND"
    width = number(options, "width", 0.3) or 0.0
    if width <= 0:
        raise CLIError("E_VALIDATION", "--width must be positive millimetres")
    items, left = routing_plan.stitch_vias(routing_plan.Board(model), net, width)
    plan = [
        {**item, "at": list(item["at"])}
        if item["kind"] == "via"
        else {**item, "points": [list(point) for point in item["points"]]}
        for item in items
    ]
    result: dict[str, Any] = {
        "net": net,
        "width": width,
        "vias": sum(1 for item in items if item["kind"] == "via"),
        "items": plan,
        "without_room": left,
        "_untrusted": ["items", "without_room"],
    }
    path = write_json_output(options, "pcb stitch", {"items": plan})
    if path:
        result["path"] = path
    return result


def unroute(options: dict[str, Any]) -> dict[str, Any]:
    """Delete the traces and vias of some nets, of all, or of one object at a point."""
    check_gate(options, "pcb unroute")
    canonical = board_file(options, "pcb unroute")
    # input order, each net once: the result's items[] zip back to what was asked
    nets = comma_list(options, "nets")
    everything = bool(options.get("all"))
    at = text(options, "at")
    layer = integer(options, "layer")
    if not nets and not everything and not at:
        raise CLIError("E_USAGE", "pcb unroute requires --nets A,B, --all, or --at x,y [--layer N]")
    keep_going = continue_on_error(options)
    scope = {
        "operation": "pcb_unroute",
        "project": canonical,
        "nets": "all" if everything else ",".join(sorted(nets)),
        "at": at,
        "layer": str(layer or ""),
        "continue_on_error": keep_going,
    }
    request: dict[str, Any] = {
        "project": canonical,
        "nets": nets,
        "all": everything,
        "start": True,
        "continue_on_error": keep_going,
    }
    if at:
        request["at"] = at
        if layer is not None:
            request["layer"] = layer
    backend = native()
    if options.get("dry_run"):
        plan = backend.invoke("unroute_nets", {**request, "apply": False}, timeout_seconds=600.0)
        preview = {
            "project": canonical,
            "pcb": plan.get("pcb"),
            "nets": plan.get("nets"),
            "at": plan.get("at"),
            "layer": plan.get("layer"),
            "to_delete": plan.get("to_delete"),
            "targets": plan.get("targets"),
            "total": len(plan.get("targets") or []),
            "changes": [
                {"action": "delete_traces_and_vias", **(plan.get("to_delete") or {})},
                {"action": "regenerate_planes"},
                {"action": "save"},
            ],
            "dangerous": True,
            "requires": "--dangerous with --confirm",
            "risk": _risk(
                "T2",
                "the routing of the named nets is deleted and the board saved; it is not "
                "archived: re-routing is the way back",
            ),
        }
        return previewed(preview, scope)
    require_dangerous(options, "pcb unroute deletes routing that is not archived")
    confirmed(options, scope)
    return backend.invoke("unroute_nets", {**request, "apply": True}, timeout_seconds=600.0)


def pour(options: dict[str, Any]) -> dict[str, Any]:
    canonical = board_file(options, "pcb pour")
    net = text(options, "net") or "GND"
    layer = integer(options, "layer", 2) or 0
    margin = number(options, "margin", 1.0)
    if layer < 1 or margin is None or margin < 0:
        raise CLIError("E_VALIDATION", "--layer starts at 1 and --margin is not negative")
    replace = bool(options.get("replace"))
    request = {
        "project": canonical,
        "net": net,
        "layer": layer,
        "margin": margin,
        "replace": replace,
        "start": True,
    }
    scope = {
        "operation": "pcb_pour",
        **{k: request[k] for k in ("project", "net", "layer", "margin", "replace")},
    }
    return _planned_write(
        options,
        "pcb pour",
        "plane_pour",
        request,
        scope,
        lambda current: {
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
            "risk": _risk(
                "T1", "one plane shape on the layer, assigned to the net; the board is saved"
            ),
        },
    )


# -- inspection ------------------------------------------------------------------------


def info(options: dict[str, Any]) -> dict[str, Any]:
    canonical = board_file(options, "pcb info")
    board, _ = reader(options).load(canonical, domain="pcb")
    pcb = board.get("pcb", {})
    return {
        "project": board["project"],
        "component_count": len(pcb.get("components", [])),
        "placed_count": sum(1 for item in pcb.get("components", []) if item.get("placed")),
        "footprint_count": len(pcb.get("footprints", [])),
        "net_count": len(pcb.get("nets", [])),
        "track_count": len(pcb.get("tracks", [])),
        "via_count": len(pcb.get("vias", [])),
        "layer_count": board.get("metadata", {}).get("layer_count"),
        "_untrusted": ["project"],
    }


def geometry(options: dict[str, Any]) -> dict[str, Any]:
    """The board as data: outline, pads, traces, vias, planes, holes, silkscreen, parts."""
    canonical = board_file(options, "pcb geometry")
    output = output_file(options, "pcb geometry", ".json")
    params: dict[str, Any] = {
        "project": canonical,
        "replace": bool(options.get("replace")),
        "start": True,
    }
    if output is not None:
        params["output"] = str(output)
    return native().invoke("board_geometry", params, timeout_seconds=600.0)


def render(options: dict[str, Any]) -> dict[str, Any]:
    """A PNG of the board drawn from its geometry (no screen needed)."""
    canonical = board_file(options, "pcb render")
    output = output_file(options, "pcb render", ".png", required=True)
    side = (text(options, "side") or "top").lower()
    if side not in {"top", "bottom"}:
        raise CLIError("E_VALIDATION", "--side is top or bottom", {"side": side})
    scale = number(options, "scale", 20.0) or 0.0
    if not 1.0 <= scale <= 200.0:
        raise CLIError("E_VALIDATION", "--scale must be between 1 and 200")
    params = {
        "project": canonical,
        "output": str(output),
        "side": side,
        "scale": scale,
        "replace": bool(options.get("replace")),
        "start": True,
    }
    return native().invoke("render_board", params, timeout_seconds=600.0)


def show(options: dict[str, Any]) -> dict[str, Any]:
    """Bring the board to the front under a display scheme that shows the parts."""
    canonical = board_file(options, "pcb show")
    output = output_file(options, "pcb show", ".png")
    params: dict[str, Any] = {"project": canonical, "start": True}
    if text(options, "scheme"):
        params["scheme"] = text(options, "scheme")
    if options.get("top_view"):
        params["top_view"] = True
    if output is not None:
        params["output"] = str(output)
    return native().invoke("show_board", params, timeout_seconds=420.0)


def metrics(options: dict[str, Any]) -> dict[str, Any]:
    """How good the placement and routing in a geometry file are, as numbers."""
    from .. import board_metrics

    model = _geometry_model(options, "pcb metrics", required=True)
    assert model is not None
    result: dict[str, Any] = board_metrics.measure(model)
    if options.get("baseline") is not None:
        baseline = read_json_file(
            input_file(options, "baseline", "pcb metrics", "the baseline file"), "the baseline file"
        )
        # an earlier result, saved with --output or as the whole envelope
        if isinstance(baseline, dict) and isinstance(baseline.get("data"), dict):
            baseline = baseline["data"]
        if not isinstance(baseline, dict) or not isinstance(baseline.get("summary"), dict):
            raise CLIError(
                "E_VALIDATION", "--baseline is a file an earlier pcb metrics --output wrote"
            )
        result["delta"] = board_metrics.compare(result, baseline)
    result["_untrusted"] = [
        "parts",
        "ratsnest",
        "overlaps",
        "outside",
        "decoupling",
        "edge_parts",
        "routing",
    ]
    saved = {key: value for key, value in result.items() if key not in {"delta", "path"}}
    path = write_json_output(options, "pcb metrics", saved)
    if path:
        result["path"] = path
    return result


def check(options: dict[str, Any]) -> dict[str, Any]:
    """Layout's Batch DRC, then every hazard it left on the board (no design change)."""
    canonical = board_file(options, "pcb check")
    params = {
        "project": canonical,
        "start": True,
        "run": not bool(options.get("no_run")),
        "online": bool(options.get("online")),
    }
    return native().invoke("batch_drc", params, timeout_seconds=900.0)


# -- fabrication -------------------------------------------------------------------------


def export(options: dict[str, Any]) -> dict[str, Any]:
    """The fabrication package: ODB++, Gerber, NC drill, centroid, BOM and a manifest."""
    canonical = board_file(options, "pcb export")
    formats = (
        comma_list(options, "formats")
        if options.get("formats") is not None
        else ["odb", "gerber", "ncdrill"]
    )
    unknown = [item for item in formats if item.lower() not in {"odb", "gerber", "ncdrill"}]
    if unknown:
        raise CLIError(
            "E_VALIDATION", "--formats takes odb, gerber and ncdrill", {"unknown": unknown}
        )
    formats_text = ",".join(item.lower() for item in formats)
    output = text(options, "output")
    folder = str(Path(output).expanduser().resolve()) if output else ""
    request: dict[str, Any] = {"project": canonical, "formats": formats_text, "start": True}
    if folder:
        request["output"] = folder
    scope = {
        "operation": "pcb_export",
        "project": canonical,
        "formats": formats_text,
        "output": folder,
    }
    return _planned_write(
        options,
        "pcb export",
        "manufacturing_output",
        request,
        scope,
        lambda plan: {
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
            "risk": _risk(
                "T1",
                "the board is closed and reopened in Layout when its output setups need "
                "patching; Layout writes its Output folders; the package folder is written",
            ),
        },
        plan_timeout=120.0,
        apply_timeout=1800.0,
    )
