"""pcb create | annotate | outline | holes | rules | arrange | move | labels | route |
trace | via | stitch | unroute | pour | info | geometry | render | show | metrics |
check | export."""

from __future__ import annotations

import hashlib
import json
import math
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
                    "setups -- is preserved in a complete verified project archive beside the "
                    "project and the "
                    "PCB folder then deleted; a folder still held by a running Layout "
                    "requires separately authorized normal session closure; then "
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
            "verify": True,
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

    check_gate(options, "pcb update")
    canonical = board_file(options, "pcb update")
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
        pcb = str(prj.local_path(path.parent, design.pcb_path))
    unroute = bool(options.get("unroute"))
    if options.get("refresh_cells"):
        from ..audit import config_dir

        if unroute or path.suffix.lower() != ".prj":
            raise CLIError(
                "E_USAGE", "--refresh-cells requires a .prj and cannot be combined with --unroute"
            )
        backend = native()
        request = {"project": canonical, "cache": str(config_dir() / "cache" / "cell-refresh")}
        current = backend.invoke("refresh_cells", {**request, "apply": False}, timeout_seconds=240)
        scope = {
            "operation": "pcb_refresh_cells",
            "project": canonical,
            "fingerprint": current["fingerprint"],
        }
        if options.get("dry_run"):
            return previewed(
                {
                    "project": canonical,
                    "pcb": pcb,
                    "cell_refresh": current,
                    "changes": [
                        {
                            "action": "refresh_used_cells_and_padstacks",
                            "instances": current["affected_instances"],
                        },
                        {"action": "save_close_reopen_and_compare"},
                    ],
                    "risk": _risk(
                        "T2",
                        "used local cell definitions and their placed instances; "
                        "preserves connectivity, poses and protected geometry",
                    ),
                },
                scope,
            )
        confirmed(options, scope)
        return backend.invoke(
            "refresh_cells",
            {**request, "apply": True, "expected": current["fingerprint"]},
            timeout_seconds=300,
        )
    scope = {"operation": "pcb_annotate", "project": canonical, "unroute": unroute}
    if options.get("dry_run"):
        source = None
        if path.suffix.lower() == ".prj":
            source = native().invoke(
                "snapshot",
                {"domain": "schematic", "project": canonical, "observe_only": True},
                timeout_seconds=120.0,
            )
        preview = {
            "project": canonical,
            "pcb": pcb,
            "verification_input": source,
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
        require_dangerous(options, "pcb update --unroute deletes the board's routing first")
    backend = native()
    confirmed(options, scope)
    return backend.invoke(
        "forward_annotate",
        {"project": canonical, "start": True, "unroute": unroute, "verify": True},
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
    canonical = board_file(options, "pcb outline set")
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
    check_gate(options, "pcb outline set")
    backend = native()
    current = backend.invoke("board_outline", {**request, "apply": False}, timeout_seconds=600.0)
    scope.update(state_guard=current.get("state_guard"), state_digest=current.get("state_digest"))
    if options.get("dry_run"):
        return previewed(
            {
                "project": canonical,
                "pcb": current.get("pcb"),
                "before": current.get("before"),
                "requested": {"width": width, "height": height, "radius": radius},
                "changes": [
                    {
                        "action": "put_board_outline",
                        "width": width,
                        "height": height,
                        "radius": radius,
                    },
                    {"action": "verify_all_three_native_outlines_and_full_board_material"},
                    {"action": "save_close_exact_reopen_and_compare"},
                ],
                "outline_plan": current.get("outline_plan"),
                "state_guard": current.get("state_guard"),
                "state_digest": current.get("state_digest"),
                "risk": _risk(
                    "T1",
                    "the board outline becomes the requested rectangle from the origin; parts "
                    "outside it stay where they are",
                ),
            },
            scope,
        )
    confirmed(options, scope)
    return backend.invoke(
        "board_outline",
        {
            **request,
            "apply": True,
            "state_guard": current.get("state_guard"),
            "state_digest": current.get("state_digest"),
        },
        timeout_seconds=1200.0,
    )


def holes(options: dict[str, Any]) -> dict[str, Any]:
    canonical = board_file(options, "pcb holes add-corners")
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
        "pcb holes add-corners",
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
    canonical = board_file(options, "pcb constraints widths")
    class_name = text(options, "class")
    nets = comma_list(options, "nets")
    width = number(options, "width")
    minimum = number(options, "min")
    expansion = number(options, "expansion")
    if width is None or width <= 0:
        raise CLIError("E_VALIDATION", "--width must be positive millimetres")
    if (minimum is not None and minimum <= 0) or (expansion is not None and expansion <= 0):
        raise CLIError("E_VALIDATION", "--min and --expansion must be positive millimetres")
    widths = {
        "typical": width,
        "minimum": minimum if minimum is not None else width * 0.8,
        "expansion": expansion if expansion is not None else width * 1.2,
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
    reason = (
        "The complete widths command remains unverified: ensure/create the net class, "
        "assign nets, set all three width fields on every layer, and synchronize Layout. "
        "A single IConstraint.Value PUT does not establish this full command contract."
    )
    blockers = [{"code": "widths_full_contract_unverified", "reason": reason}]
    unknowns = [
        "Native class existence and net membership were not observed.",
        "Native units, exact design/context and all-layer three-field writes are unverified.",
        "Layout synchronization and independent native width readback are unverified.",
        "Save/reopen persistence of the complete widths command is unverified.",
    ]
    if not options.get("dry_run"):
        raise CLIError(
            "E_UNSUPPORTED",
            reason,
            {
                "write_attempted": False,
                "can_apply": False,
                "native_state": "not_observed",
                "blockers": blockers,
                "unknowns": unknowns,
            },
        )
    check_gate(options, "pcb constraints widths")
    return previewed(
        {
            "project": canonical,
            "pcb": None,
            "class": class_name,
            "nets": nets,
            "widths": widths,
            "classes": None,
            "missing_nets": None,
            "native_state": "not_observed",
            "can_apply": False,
            "write_attempted": False,
            "eligibility": {"eligible": False, "status": "unverified", "reason": reason},
            "blockers": blockers,
            "unknowns": unknowns,
            "constraints": [
                reason,
                "Do not retry or override this refusal with manual flags. A can_apply=false "
                "confirmation refusal does not establish changed native geometry or rules.",
            ],
            "changes": [
                {"action": "ensure_net_class", "class": class_name},
                {"action": "assign_nets", "nets": nets},
                {
                    "action": "set_trace_widths",
                    "widths": widths,
                    "fields": ["minimum", "typical", "expansion"],
                    "layers": "all",
                },
                {"action": "synch_ces"},
            ],
            "risk": _risk(
                "T1",
                "No native write is eligible for this unverified full command scope",
            ),
        },
        scope,
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
    _design_file, design = read_design(options, "pcb placement auto")
    labels: dict[int, str] = {}
    for index, sheet in enumerate(design.get("sheets") or [], 1):
        if isinstance(sheet, dict) and str(sheet.get("zone") or "").strip():
            try:
                labels[int(sheet.get("number") or index)] = str(sheet["zone"]).strip()
            except (TypeError, ValueError):
                continue
    by_sheet: dict[int, list[str]] = {}
    for refdes, sheet_number in schematic_layout.refdes_by_sheet(design).items():
        by_sheet.setdefault(sheet_number, []).append(refdes)
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
    check_gate(options, "pcb placement auto")
    canonical = board_file(options, "pcb placement auto")
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
        require_dangerous(options, "pcb placement auto deletes the board's traces and vias first")
    confirmed(options, scope)
    return backend.invoke(
        "arrange_components",
        {**request, "apply": True, "digest": digest, "pace": pace(options, "pcb placement auto")},
        timeout_seconds=900.0,
    )


def _move_one(options: dict[str, Any], canonical: str) -> dict[str, Any]:
    from ..native_com_adapter import AdapterError, parse_points

    refdes = text(options, "refdes")
    if not refdes or options.get("to") is None:
        raise CLIError(
            "E_USAGE", "pcb placement move requires --refdes U1 --to x,y [--rotate DEG], or --file"
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
        "verify": True,
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
        "pcb placement move",
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
    from ..placement import read_json, same_position, validate_batch_preview, validate_request

    request = validate_request(
        read_json(str(input_file(options, "file", "pcb placement move", "the task file")))
    )
    backend = native()
    params = {"project": canonical, "request": request}
    preview = backend.invoke("placement_batch", {**params, "apply": False}, timeout_seconds=600.0)
    try:
        # Do not sign an arbitrary adapter-generated digest or a different task.
        validate_batch_preview(preview, request)
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
        "state_guard": preview["state_guard"],
    }
    if options.get("dry_run"):
        preview["risk"] = {
            "tier": "T1",
            "blast_radius": (
                "the selected parts move; complete target and non-target material is checked, "
                "then one strict save/close/exact-reopen verifies persistence; "
                "traces are not rerouted"
            ),
            "partial_failure": (
                "parts moved before a failure stay moved and the current part may be unplaced"
            ),
        }
        return previewed(preview, scope)
    # A fresh preview binds identities, sides, protection and positions; the adapter
    # checks the digest again under its batch lock right before editing.
    confirmed(options, scope)

    def retained_result(report: Any, stage: str) -> dict[str, Any]:
        known = isinstance(report, dict) and report.get("outcome") in {
            "complete",
            "partial_failure",
            "persistence_incomplete",
            "failed",
        }
        return {
            "stage": stage,
            "write_attempted": report.get("write_attempted", True) if known else True,
            "outcome": report["outcome"] if known else "unknown",
            "report": report,
            "retry_safe": False,
            "_untrusted": ["report"],
        }

    try:
        result = backend.invoke(
            "placement_batch",
            {
                **params,
                "apply": True,
                "state_digest": preview["state_digest"],
                "state_guard": preview["state_guard"],
            },
            timeout_seconds=1500.0,
        )
    except CLIError as error:
        if (error.details or {}).get("write_attempted") is False:
            raise
        details = dict(error.details or {})
        known_report = details.get("report", details)
        retained = retained_result(known_report, "submit")
        retained.update(cause_code=error.code, cause_details=details)
        retained["_untrusted"].append("cause_details")
        if retained["outcome"] != "unknown":
            retained["cause_message"] = error.message
            retained["_untrusted"].append("cause_message")
        raise CLIError(
            "E_PROJECT_INVALID",
            "the placement did not complete; inspect the retained result before another write"
            if retained["outcome"] != "unknown"
            else "the placement outcome is unknown; inspect the board before another write",
            retained,
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
            retained_result(result, "execution"),
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
            retained_result(result, "response"),
        )
    for planned, returned in zip(preview["results"], results, strict=True):
        observed = returned.get("observed")
        if not isinstance(observed, dict) or not same_position(planned["target"], observed):
            raise CLIError(
                "E_PROJECT_INVALID",
                "the placement response did not verify the requested target",
                retained_result(result, "response"),
            )
    if preview["summary"]["changed_count"] and (
        result.get("write_attempted") is not True or result.get("drc_restored") is not True
    ):
        raise CLIError(
            "E_PROJECT_INVALID",
            "the placement ran but its DRC restoration is unconfirmed",
            retained_result(result, "response"),
        )
    if result.get("write_attempted") is True and result.get("saved") is not True:
        raise CLIError(
            "E_PROJECT_INVALID",
            "the placement save is unconfirmed",
            retained_result(result, "save"),
        )
    return result


def move(options: dict[str, Any]) -> dict[str, Any]:
    canonical = board_file(options, "pcb placement move")
    if options.get("file") is not None:
        if any(options.get(name) is not None for name in ("refdes", "to", "rotate")):
            raise CLIError(
                "E_USAGE", "pcb placement move takes --file or --refdes/--to/--rotate, not both"
            )
        check_gate(options, "pcb placement move")
        return _move_task(options, canonical)
    return _move_one(options, canonical)


def _bounded_labels(options: dict[str, Any], canonical: str) -> dict[str, Any]:
    from ..board_labels import GUARD, validate

    if any(options.get(k) is not None for k in ("refdes", "gap", "height", "min_height", "pen")):
        raise CLIError(
            "E_USAGE", "labels --file cannot combine automatic placement or size options"
        )
    path = input_file(options, "file", "pcb silkscreen labels", "the explicit label moves file")
    moves = validate(read_json_file(path, "the explicit label moves file"))
    check_gate(options, "pcb silkscreen labels")
    request = {"project": canonical, "start": False, "label_moves": moves}
    backend = native()
    plan = backend.invoke("tidy_labels", {**request, "apply": False}, timeout_seconds=600.0)
    fingerprint = plan.get("state_digest")
    if (
        plan.get("state_guard") != GUARD
        or not isinstance(fingerprint, str)
        or not re.fullmatch("[a-f0-9]{64}", fingerprint)
    ):
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "Adapter cannot bind explicit silk text translation",
            {"write_attempted": False},
        )
    scope = {
        "operation": "pcb_labels",
        "project": canonical,
        "file": str(path),
        "label_moves": moves,
        "state_guard": GUARD,
        "state_digest": fingerprint,
    }
    if options.get("dry_run"):
        return previewed(
            {
                **plan,
                "can_apply": True,
                "changes": [{"action": "Move", **m} for m in moves["moves"]]
                + [{"action": "conditional_save_close_reopen_after_complete_protection"}],
                "risk": _risk(
                    "T1",
                    "only identified silk text positions; pin1 graphics, formats and all other "
                    "captured geometry protected",
                ),
            },
            scope,
        )
    confirmed(options, scope)
    try:
        result = backend.invoke(
            "tidy_labels",
            {**request, "apply": True, "state_guard": GUARD, "state_digest": fingerprint},
            timeout_seconds=600.0,
        )
    except CLIError as error:
        if (error.details or {}).get("write_attempted") is False:
            raise
        raise CLIError(
            "E_PROJECT_INVALID",
            "Explicit silk outcome requires observation; never replay",
            {
                "stage": "execution",
                "retry_safe": False,
                "outcome": "unknown",
                "write_attempted": True,
                "cause_code": error.code,
                "native_details": error.details,
            },
        ) from error
    if (
        not all(result.get(k) is True for k in ("applied", "saved", "persistence_verified"))
        or not isinstance(result.get("verification"), dict)
        or result["verification"].get("valid") is not True
        or result.get("failures")
        or result.get("state_guard") != GUARD
        or result.get("state_digest") != fingerprint
    ):
        raise CLIError(
            "E_PROJECT_INVALID",
            "Explicit silk saved/reopened proof is incomplete",
            {"native_result": result, "retry_safe": False},
        )
    return result


def labels(options: dict[str, Any]) -> dict[str, Any]:
    """Every silkscreen designator beside its part; --height and --pen give them all one
    size first, --min-height raises only the lower ones, and the spots are found for the
    new sizes."""
    canonical = board_file(options, "pcb silkscreen labels")
    if options.get("file") is not None:
        return _bounded_labels(options, canonical)
    gap = number(options, "gap")
    if gap is not None and gap < 0:
        raise CLIError("E_VALIDATION", "--gap must not be negative")
    height = number(options, "height")
    if height is not None and not 0.3 <= height <= 5.0:
        raise CLIError("E_VALIDATION", "--height is millimetres, from 0.3 to 5")
    minimum = number(options, "min_height")
    if minimum is not None and not 0.3 <= minimum <= 5.0:
        raise CLIError("E_VALIDATION", "--min-height is millimetres, from 0.3 to 5")
    pen = number(options, "pen")
    if pen is not None and not 0.05 <= pen <= 1.0:
        raise CLIError("E_VALIDATION", "--pen is millimetres, from 0.05 to 1")
    smallest = height or minimum
    if smallest is not None and pen is not None and pen > smallest / 3:
        raise CLIError(
            "E_VALIDATION",
            "--pen wider than a third of the text height fills the letters in",
            {"height": smallest, "pen": pen},
        )
    refdes = comma_list(options, "refdes")
    request: dict[str, Any] = {"project": canonical, "start": True, "refdes": refdes}
    sizes = (("gap", gap), ("height", height), ("min_height", minimum), ("pen", pen))
    for key, value in sizes:
        if value is not None:
            request[key] = value
    scope = {"operation": "pcb_labels", "project": canonical, "refdes": refdes, **dict(sizes)}

    def preview(plan: dict[str, Any]) -> dict[str, Any]:
        changes: list[dict[str, Any]] = []
        if plan.get("resized"):
            resize = {
                "count": plan.get("resized"),
                "height": height,
                "min_height": minimum,
                "pen": pen,
            }
            changes.append(
                {
                    "action": "resize_silkscreen_designators",
                    **{key: value for key, value in resize.items() if value is not None},
                }
            )
        changes += [
            {"action": "move_silkscreen_designators", "count": plan.get("moved")},
            {"action": "save"},
        ]
        return {
            "can_apply": False,
            "limitations": [
                "Automatic relocation/resizing lacks pre-save protected-object verification; "
                "confirmation is disabled. Use identity-bound --file position moves."
            ],
            "project": canonical,
            "pcb": plan.get("pcb"),
            "labels": plan.get("labels"),
            "selected_refdes": plan.get("selected_refdes"),
            "protected_refdes": plan.get("protected_refdes"),
            "moved": plan.get("moved"),
            "resized": plan.get("resized"),
            "unplaced": plan.get("unplaced"),
            "state_guard": plan.get("state_guard"),
            "state_digest": plan.get("state_digest"),
            "changes": changes,
            "risk": _risk(
                "T1",
                "the silkscreen designators move"
                + (" and change size" if plan.get("resized") else "")
                + "; nothing electrical changes; the board is saved",
            ),
        }

    check_gate(options, "pcb silkscreen labels")
    backend = native()
    plan = backend.invoke("tidy_labels", {**request, "apply": False}, timeout_seconds=600.0)
    state_digest = plan.get("state_digest")
    if (
        plan.get("state_guard") != "labels_input_sha256_v1"
        or not isinstance(state_digest, str)
        or len(state_digest) != 64
        or any(char not in "0123456789abcdef" for char in state_digest)
    ):
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "the adapter does not support guarded designator writes; update it and dry-run again",
            {"stage": "plan", "write_attempted": False},
        )
    scope.update({"state_guard": plan["state_guard"], "state_digest": state_digest})
    if options.get("dry_run"):
        return previewed(preview(plan), scope)
    confirmed(options, scope)
    raise CLIError(
        "E_UNSUPPORTED",
        "Automatic label relocation/resizing is disabled before any native mutation; "
        "use explicit --file positions",
        {"write_attempted": False, "can_apply": False},
    )


# -- routing --------------------------------------------------------------------------


def route(options: dict[str, Any]) -> dict[str, Any]:
    check_gate(options, "pcb routing auto")
    if options.get("mode") == "bounded-general":
        return _bounded_general_route(options)
    if options.get("mode") == "bounded-native":
        return _bounded_native_route(options)
    raise CLIError(
        "E_UNSUPPORTED",
        "Autorouter impact/protection is unproven; use pcb routing trace for a bound preview",
        {"write_attempted": False, "unsupported_operation": "autorouter"},
    )


def _bounded_general_route(options: dict[str, Any]) -> dict[str, Any]:
    from ..board_general import EXECUTION_BUDGET_SECONDS, GUARD, validate

    if (
        any(options.get(k) is not None for k in ("nets", "layers", "passes", "native_effort"))
        or options.get("unroute")
        or options.get("continue_existing")
    ):
        raise CLIError(
            "E_USAGE",
            "bounded-general uses one explicit --file plan; legacy/pass flags are forbidden",
            {"write_attempted": False},
        )
    plan_file = input_file(options, "file", "pcb routing auto", "the bounded General Route plan")
    try:
        general_request = validate(read_json_file(plan_file, "the bounded General Route plan"))
    except ValueError as error:
        raise CLIError("E_VALIDATION", str(error), {"write_attempted": False}) from error
    source = input_file(
        options, "source_backup", "pcb routing auto", "the complete closed source recovery ZIP"
    )
    request = {
        "project": board_file(options, "pcb routing auto"),
        "mode": "bounded-general",
        "start": False,
        "source_backup": str(source),
        "general_request": general_request,
    }
    backend = native()
    observed = backend.invoke("route_board", {**request, "apply": False}, timeout_seconds=600.0)
    bound = observed.get("general_route")
    dispatch_supported = observed.get("native_dispatch_supported")
    if (
        not isinstance(bound, dict)
        or bound.get("guard") != GUARD
        or not isinstance(bound.get("fingerprint"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", bound["fingerprint"])
        or type(bound.get("can_apply")) is not bool
        or type(dispatch_supported) is not bool
    ):
        raise CLIError(
            "E_VERIFY_INCOMPLETE",
            "General Route preview lacks the full target/material/selection/configuration binding",
            {"write_attempted": False, "native_result": observed},
        )
    scope = {
        "operation": "pcb_route",
        "mode": "bounded-general",
        "project": request["project"],
        "request": general_request,
        "source_backup": str(source),
        "fingerprint": bound["fingerprint"],
    }
    if options.get("dry_run"):
        return previewed(
            {
                **observed,
                "can_apply": bound["can_apply"] and dispatch_supported,
                "native_dispatch_limitation": observed.get("native_dispatch_limitation"),
                "changes": [
                    *(
                        [
                            {
                                "action": "select_active_route_layer_ui_once",
                                **bound["ui_layer_selection"],
                            }
                        ]
                        if general_request.get("select_active_layer", False)
                        else []
                    ),
                    {
                        "action": "select_one_actual_unconnected_FromTo",
                        "target": bound["selected_fromto"],
                    },
                    {"action": "one_Gui_ProcessCommand", "command": 35367, "synchronous": True},
                    {"action": "verify_entire_material_graph_and_declared_target_replacements"},
                    {"action": "conditional_BOOL_save_close_reopen"},
                ]
                if dispatch_supported
                else [
                    {"action": "observe_bound_material_scope_only"},
                    {
                        "action": "no_native_dispatch_selector_contract_unproven",
                        "write_attempted": False,
                    },
                ],
                "risk": _risk(
                    "T1",
                    "One native-engine candidate; settings do not guarantee output. "
                    "Strict whole-output material, bounded target-change and foreign protection "
                    "gate persistence."
                    if dispatch_supported
                    else "Material-scope observation only; the General Route API selector "
                    "contract is unproven. No selection, command or persistence is authorized.",
                ),
            },
            scope,
        )
    if dispatch_supported is not True:
        raise CLIError(
            "E_UNSUPPORTED",
            "General Route API selector contract is unproven; no command dispatched",
            {"write_attempted": False, "limitation": observed.get("native_dispatch_limitation")},
        )
    confirmed(options, scope)
    if bound["can_apply"] is not True:
        raise CLIError(
            "E_UNSUPPORTED",
            "Selected General Route preconditions incomplete",
            {"write_attempted": False},
        )
    result = backend.invoke(
        "route_board",
        {**request, "apply": True, "expected": bound["fingerprint"]},
        timeout_seconds=float(EXECUTION_BUDGET_SECONDS),
    )
    if not all(result.get(k) is True for k in ("saved", "persistence_verified")):
        raise CLIError(
            "E_VERIFY_INCOMPLETE",
            "General Route did not establish protected save/reopen; do not replay",
            {"native_result": result, "retry_safe": False},
        )
    return {**result, "mode": "bounded-general"}


def _bounded_native_route(options: dict[str, Any]) -> dict[str, Any]:
    from ..board_routepass import GUARD

    route_request = native_route_options(options)
    canonical = board_file(options, "pcb routing auto")
    source_backup = input_file(
        options, "source_backup", "pcb routing auto", "the complete closed source recovery ZIP"
    )
    request = {
        "project": canonical,
        "mode": "bounded-native",
        "start": False,
        "source_backup": str(source_backup),
        "routepass_request": route_request,
    }
    backend = native()
    observed = backend.invoke("route_board", {**request, "apply": False}, timeout_seconds=600.0)
    bound = observed.get("routepass")
    if (
        not isinstance(bound, dict)
        or bound.get("guard") != GUARD
        or not isinstance(bound.get("fingerprint"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", bound["fingerprint"])
        or type(bound.get("can_apply")) is not bool
    ):
        raise CLIError(
            "E_VERIFY_INCOMPLETE",
            "Native pass preview lacks a bound target, complete state and ordered configuration",
            {"write_attempted": False, "native_result": observed},
        )
    scope = {
        "operation": "pcb_route",
        "mode": "bounded-native",
        "project": canonical,
        "request": request["routepass_request"],
        "source_backup": str(source_backup),
        "fingerprint": bound["fingerprint"],
    }
    if options.get("dry_run"):
        return previewed(
            {
                **observed,
                "mode": "bounded-native",
                "can_apply": bound["can_apply"],
                "changes": [
                    {"action": "one_native_NoViaPass", "target": bound["target"]},
                    {"action": "verify_entire_new_copper_set_and_protected_objects"},
                    {"action": "conditional_BOOL_save_close_reopen"},
                ],
                "risk": _risk(
                    "T1",
                    "native-engine candidate on one bound net/layer; complete copper/contact/"
                    "protection acceptance precedes conditional persistence; "
                    "no complete effective-rule parser is claimed",
                ),
            },
            scope,
        )
    confirmed(options, scope)
    if bound["can_apply"] is not True:
        raise CLIError(
            "E_UNSUPPORTED", "Native pass preconditions are incomplete", {"write_attempted": False}
        )
    result = backend.invoke(
        "route_board",
        {**request, "apply": True, "expected": bound["fingerprint"]},
        timeout_seconds=900.0,
    )
    if not all(result.get(k) is True for k in ("saved", "persistence_verified")):
        raise CLIError(
            "E_VERIFY_INCOMPLETE",
            "Native pass did not establish protected save/reopen; do not replay",
            {"native_result": result, "retry_safe": False},
        )
    return {**result, "mode": "bounded-native"}


def native_route_options(options: dict[str, Any]) -> dict[str, Any]:
    """Validate the finite mode before SDK attachment or native observation."""
    if options.get("mode") != "bounded-native":
        raise CLIError(
            "E_UNSUPPORTED",
            "Legacy unrestricted autorouting remains disabled",
            {"write_attempted": False, "unsupported_operation": "autorouter"},
        )
    nets, layers = comma_list(options, "nets"), comma_list(options, "layers")
    if (
        len(nets) != 1
        or len(layers) != 1
        or not re.fullmatch(r"[1-9][0-9]{0,2}", layers[0])
        or int(layers[0]) > 128
        or options.get("passes") is not None
        or options.get("unroute")
    ):
        raise CLIError(
            "E_USAGE",
            "bounded-native requires exactly one --nets name and one --layers number, "
            "without --passes or --unroute",
            {"write_attempted": False},
        )
    if not options.get("source_backup"):
        raise CLIError(
            "E_USAGE",
            "bounded-native requires --source-backup for the complete closed source recovery",
            {"write_attempted": False},
        )
    effort = options.get("native_effort", 1)
    if not (
        (type(effort) is int and effort in (1, 2)) or (type(effort) is str and effort in ("1", "2"))
    ):
        raise CLIError(
            "E_USAGE",
            "bounded-native --native-effort accepts only integer 1 (default) or 2",
            {"write_attempted": False},
        )
    if options.get("continue_existing") and nets[0] != "GND":
        raise CLIError(
            "E_USAGE",
            "--continue-existing is limited to the six-pin GND existing-components contract",
            {"write_attempted": False},
        )
    return {
        "net": nets[0],
        "layer": int(layers[0]),
        **({"effort": 2} if int(effort) == 2 else {}),
        **({"continuation": "existing-components"} if options.get("continue_existing") else {}),
    }


def _geometry_model(
    options: dict[str, Any], command: str, *, required: bool
) -> dict[str, Any] | None:
    """The board geometry file named by --geometry."""
    if not options.get("geometry"):
        if required:
            raise CLIError(
                "E_USAGE", f"{command} requires --geometry board.json (from pcb snapshot)"
            )
        return None
    path = input_file(options, "geometry", command, "the geometry file")
    model = read_json_file(path, "the geometry file")
    if isinstance(model, dict) and isinstance(model.get("data"), dict):
        model = model["data"].get("model", model["data"])
    if not isinstance(model, dict) or "pads" not in model:
        raise CLIError("E_VALIDATION", f"{command}: --geometry is the file pcb snapshot writes")
    if model.get("filter"):
        raise CLIError(
            "E_VALIDATION",
            f"{command} needs the whole board; this geometry was filtered",
            {"filter": model["filter"], "hint": "pcb snapshot without --refdes or --nets"},
        )
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
        if command == "pcb routing via":
            item: dict[str, Any] = {
                "kind": "via",
                "net": options.get("net"),
                "at": options.get("at"),
                "padstack": options.get("padstack"),
            }
        else:
            if not text(options, "net") or options.get("points") is None:
                raise CLIError(
                    "E_USAGE", "pcb routing trace requires --net and --points, or --file PLAN.json"
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


def _component_connect_file(options, name, *, max_bytes):
    path = input_file(options, name, "pcb routing trace", "the " + name.replace("_", " ") + " file")
    try:
        data = path.read_bytes()
        if not data or len(data) > max_bytes:
            raise ValueError("The component input file is empty or exceeds its byte budget")

        def invalid_constant(value):
            raise ValueError("Nonfinite JSON constant: " + value)

        def finite_float(value):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("JSON number exceeds the finite numeric boundary")
            return number

        record = json.loads(
            data.decode("utf-8-sig"), parse_constant=invalid_constant, parse_float=finite_float
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise CLIError(
            "E_VALIDATION",
            "The component input must be bounded UTF-8 JSON",
            {"path": str(path), "write_attempted": False},
        ) from error
    if not isinstance(record, dict):
        raise CLIError(
            "E_VALIDATION",
            "The component input must contain a JSON object",
            {"write_attempted": False},
        )
    return record, {
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def component_connect_options(options: dict[str, Any], canonical: str) -> dict[str, Any]:
    """Capture exact input bytes and validate snapshot-scoped choices without CAD."""
    try:
        return _component_connect_options(options, canonical)
    except CLIError as error:
        error.details = {**(error.details or {}), "write_attempted": False}
        raise


def _component_connect_options(options: dict[str, Any], canonical: str) -> dict[str, Any]:
    from .. import board_components as components

    forbidden = [name for name in ("file", "geometry", "dangerous") if options.get(name)]
    if forbidden:
        raise CLIError(
            "E_USAGE",
            "Component connection cannot combine legacy trace override inputs",
            {"options": forbidden, "write_attempted": False},
        )
    required = (
        "component_query",
        "snapshot_id",
        "from_component",
        "to_component",
        "isolated_origin",
        "net",
        "layer",
        "width",
        "points",
    )
    missing = ["--" + name.replace("_", "-") for name in required if options.get(name) is None]
    if missing:
        raise CLIError(
            "E_USAGE",
            "Component connection requires " + ", ".join(missing),
            {"write_attempted": False},
        )
    content, query_file = _component_connect_file(
        options, "component_query", max_bytes=64 * 1024 * 1024
    )
    if "ok" in content:
        if content["ok"] is not True or not isinstance(content.get("data"), dict):
            raise CLIError(
                "E_VALIDATION",
                "The component query envelope is not successful",
                {"write_attempted": False},
            )
        content = content["data"]
    if isinstance(content.get("observation"), dict):
        content = content["observation"]
    query = content.get("connectivity_components", content)
    if not isinstance(query, dict):
        raise CLIError(
            "E_VALIDATION", "The query lacks connectivity_components", {"write_attempted": False}
        )
    target = query.get("target")
    layer = integer(options, "layer")
    if (
        not isinstance(target, dict)
        or query.get("snapshot_id") != text(options, "snapshot_id")
        or target.get("net") != text(options, "net")
        or target.get("layer") != layer
        or not isinstance(target.get("net_uid"), str)
        or not target["net_uid"].strip()
    ):
        raise CLIError(
            "E_CONFLICT",
            "Snapshot ID, net and layer must match the exact component query",
            {"write_attempted": False},
        )
    items = _routing_items(options, "pcb routing trace")
    item = items[0]
    if (
        not math.isfinite(item["width"])
        or item["width"] <= 0
        or not 2 <= len(item["points"]) <= components.MAX_PATH_POINTS
        or any(not all(math.isfinite(v) for v in point) for point in item["points"])
    ):
        raise CLIError(
            "E_VALIDATION",
            "Component path and width must be finite and bounded in mm",
            {"write_attempted": False},
        )
    first, second = (
        {"component_id": text(options, "from_component")},
        {"component_id": text(options, "to_component")},
    )
    for selector, flag in ((first, "from_node"), (second, "to_node")):
        if options.get(flag) is not None:
            selector["node_id"] = text(options, flag)
    pure = components.plan_connection(
        query, first=first, second=second, points=item["points"], width_mm=item["width"]
    )
    if pure.get("structural_valid") is not True:
        raise CLIError(
            "E_VALIDATION",
            "Component selectors or path do not match the exact snapshot",
            {"component_plan": pure, "write_attempted": False},
        )
    origin, origin_file = _component_connect_file(options, "isolated_origin", max_bytes=1024 * 1024)
    if any(
        name in origin for name in ("verified", "rules_verified", "local_applicability_verified")
    ):
        raise CLIError(
            "E_VALIDATION",
            "Origin records cannot supply caller verification overrides",
            {"write_attempted": False},
        )
    request = {
        "snapshot_id": query["snapshot_id"],
        "net": target["net"],
        "net_uid": target["net_uid"],
        "layer": layer,
        "first": first,
        "second": second,
        "width_mm": item["width"],
        "points": item["points"],
        "query": query,
        "query_file": query_file,
        "isolated_origin": origin,
        "isolated_origin_file": origin_file,
        "recovery_backup": str(Path(text(options, "recovery_backup")).expanduser().resolve())
        if options.get("recovery_backup") is not None
        else None,
    }
    result = {
        "project": canonical,
        "start": False,
        "mode": "component-connect",
        "component_connect_request": request,
    }
    if options.get("source_backup") is not None:
        result["source_backup"] = str(
            input_file(options, "source_backup", "pcb routing trace", "the source recovery ZIP")
        )
    return result


def _component_connect_trace(options, canonical):
    from ..cell_refresh import digest
    from ..component_execution_budget import ENGINE_SECONDS, blocker, validate_report

    request = component_connect_options(options, canonical)
    backend = native()
    observed = backend.invoke("hand_route", {**request, "apply": False}, timeout_seconds=1200.0)
    plan = observed.get("trace")
    try:
        execution_resources = validate_report(observed.get("execution_resources"))
    except (TypeError, ValueError) as error:
        raise CLIError(
            "E_NATIVE_FAILURE",
            "Component preview lacks its measured native execution budget.",
            {"write_attempted": False, "native_query_only": True},
        ) from error
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("fingerprint"), str)
        or re.fullmatch(r"[0-9a-f]{64}", plan["fingerprint"]) is None
        or type(plan.get("can_apply")) is not bool
        or not isinstance(plan.get("blockers"), list)
        or type(plan.get("no_op")) is not bool
        or plan["no_op"]
        != (
            request["component_connect_request"]["first"]["component_id"]
            == request["component_connect_request"]["second"]["component_id"]
        )
    ):
        raise CLIError(
            "E_NATIVE_FAILURE",
            "Component preview lacks a complete native plan",
            {"report": observed, "write_attempted": False, "native_query_only": True},
        )
    if (
        execution_resources["phase"] != "preview"
        or execution_resources["no_op"] != plan["no_op"]
        or execution_resources["remaining_reads"] != (0 if plan["no_op"] else 6)
    ):
        raise CLIError(
            "E_NATIVE_FAILURE",
            "Component preview execution measurement has a wrong phase or read count.",
            {"write_attempted": False, "native_query_only": True},
        )
    # The receiver independently rebinds the query to actual material. A no-op
    # still needs that fresh preview; it never dispatches apply or persistence.
    scope = {
        "operation": "pcb_component_connect",
        "project": canonical,
        "fingerprint": plan["fingerprint"],
        "request_sha256": digest(request),
    }
    if options.get("dry_run"):
        return previewed(
            {
                "project": canonical,
                "pcb": observed.get("pcb"),
                "mode": "component-connect",
                "trace": plan,
                "can_apply": plan["can_apply"] and not execution_resources["blocked"],
                "blockers": [
                    *plan["blockers"],
                    *([blocker(execution_resources)] if execution_resources["blocked"] else []),
                ],
                "execution_resources": execution_resources,
                "no_op": plan["no_op"],
                "changes": [{"action": "no_change"}]
                if plan["no_op"]
                else [
                    {"action": "one_isolated_component_connection", "traces": 1, "vias": 0},
                    {"action": "native_drc_transaction_and_complete_protection"},
                    {"action": "conditional_save_close_reopen"},
                ]
                if plan["can_apply"] and not execution_resources["blocked"]
                else [{"action": "write_refused", "traces": 0, "vias": 0}],
                "risk": _risk(
                    "T1",
                    "One isolated native DRC transaction; full effective-rule parsing remains "
                    "unproved. Material and protection must pass before persistence; "
                    "unknown outcomes are never replayed.",
                ),
            },
            scope,
        )
    if execution_resources["blocked"]:
        raise CLIError(
            "E_UNSUPPORTED",
            "Native observation costs exceed the fixed component apply budget.",
            {"write_attempted": False, "resource_budget": execution_resources},
        )
    if not plan["can_apply"] and not (plan["no_op"] and not plan["blockers"]):
        raise CLIError(
            "E_UNSUPPORTED",
            "Component connection is blocked before native apply",
            {"trace": plan, "write_attempted": False},
        )
    confirmed(options, scope)
    if plan["no_op"]:
        return {
            **observed,
            "no_op": True,
            "applied": False,
            "saved": False,
            "engineering_write_dispatched": False,
            "no_change": True,
            "change_status": "not_applied",
            "persistence_state": "unchanged",
        }
    from ..operation_budget import current_budget

    budget = current_budget()
    if budget is not None and budget.remaining_seconds() < ENGINE_SECONDS:
        raise CLIError(
            "E_TIMEOUT",
            "The shared dispatch budget cannot fit the full fixed native apply allowance.",
            {
                "write_attempted": False,
                "native_query_only": True,
                "required_seconds": ENGINE_SECONDS,
                "available_seconds": budget.remaining_seconds(),
            },
        )
    result = backend.invoke(
        "hand_route",
        {**request, "apply": True, "expected": plan["fingerprint"]},
        timeout_seconds=ENGINE_SECONDS,
    )
    if (
        result.get("persistence_verified") is not True
        or result.get("protection", {}).get("valid") is not True
    ):
        raise CLIError(
            "E_VERIFY_INCOMPLETE",
            "Component connection did not verify protection and persistence",
            {"native_result": result, "retry_safe": False},
        )
    return result


def _hand_route(options: dict[str, Any], command: str) -> dict[str, Any]:
    check_gate(options, command)
    canonical = board_file(options, command)
    mode = text(options, "mode").lower() or "trace"
    if command == "pcb routing trace" and mode == "component-connect":
        return _component_connect_trace(options, canonical)
    if mode != "trace":
        raise CLIError(
            "E_VALIDATION", "Trace mode is trace or component-connect", {"write_attempted": False}
        )
    component_flags = (
        "component_query",
        "snapshot_id",
        "from_component",
        "to_component",
        "from_node",
        "to_node",
        "isolated_origin",
        "source_backup",
    )
    if any(options.get(name) is not None for name in component_flags):
        raise CLIError(
            "E_USAGE",
            "Component connection inputs require --mode component-connect",
            {"write_attempted": False},
        )
    if command != "pcb routing trace" or options.get("file"):
        raise CLIError(
            "E_UNSUPPORTED",
            "This candidate supports only the explicit single trace preview",
            {"write_attempted": False},
        )
    if options.get("layer") is None or options.get("width") is None:
        raise CLIError(
            "E_USAGE",
            "pcb routing trace requires explicit --layer and --width",
            {"write_attempted": False},
        )
    items = _routing_items(options, command)
    model = _geometry_model(options, command, required=False)
    problems = []
    if model is not None:
        from .. import routing_plan

        problems = routing_plan.check_plan(items, routing_plan.Board(model))
        if problems and not options.get("dangerous"):
            raise CLIError(
                "E_VALIDATION",
                "The supplemental geometry projection check found risks",
                {"problems": problems[:40], "count": len(problems), "write_attempted": False},
            )
    backend = native()
    request = {"project": canonical, "items": items, "start": False}
    observed = backend.invoke("hand_route", {**request, "apply": False}, timeout_seconds=120.0)
    plan = observed.get("trace")
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("fingerprint"), str)
        or len(plan["fingerprint"]) != 64
        or type(plan.get("can_apply")) is not bool
        or not isinstance(plan.get("blockers"), list)
    ):
        raise CLIError(
            "E_VERIFY_INCOMPLETE",
            "Trace preview lacks a complete bound native impact/dependency plan",
            {"write_attempted": False, "report": observed},
        )
    scope = {"operation": "pcb_trace", "project": canonical, "fingerprint": plan["fingerprint"]}
    if options.get("dry_run"):
        return previewed(
            {
                "project": canonical,
                "pcb": observed.get("pcb"),
                "items": items,
                "nets": observed.get("nets"),
                "trace": plan,
                "can_apply": plan["can_apply"],
                "blockers": plan["blockers"],
                "checked_against_geometry": model is not None,
                "problems": problems,
                "changes": [
                    {"action": "put_one_trace", "traces": 1, "vias": 0},
                    {"action": "protect_before_save_close_reopen"},
                ]
                if plan["can_apply"]
                else [{"action": "write_refused", "traces": 0, "vias": 0}],
                "risk": _risk(
                    "T1",
                    "No write is eligible until complete local rule/dependency and copper-impact "
                    "proof exists; --dangerous does not override these blockers",
                ),
            },
            scope,
        )
    confirmed(options, scope)
    result = backend.invoke(
        "hand_route",
        {**request, "apply": True, "expected": plan["fingerprint"]},
        timeout_seconds=600.0,
    )
    if (
        result.get("persistence_verified") is not True
        or result.get("protection", {}).get("valid") is not True
    ):
        raise CLIError(
            "E_VERIFY_INCOMPLETE",
            "Trace did not establish complete protection and persistence",
            {"native_result": result, "retry_safe": False},
        )
    return result


def trace(options: dict[str, Any]) -> dict[str, Any]:
    return _hand_route(options, "pcb routing trace")


def via(options: dict[str, Any]) -> dict[str, Any]:
    return _hand_route(options, "pcb routing via")


def apply_routing(options: dict[str, Any]) -> dict[str, Any]:
    return _hand_route(options, "pcb routing apply")


def stitch(options: dict[str, Any]) -> dict[str, Any]:
    """The plan for a via beside every surface-mount pad of a net, from the geometry file."""
    from .. import routing_plan

    model = _geometry_model(options, "pcb routing stitch-plan", required=True)
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
    path = write_json_output(options, "pcb routing stitch-plan", {"items": plan})
    if path:
        result["path"] = path
    return result


def unroute(options: dict[str, Any]) -> dict[str, Any]:
    """Delete the traces and vias of some nets, of all, or of one object at a point."""
    check_gate(options, "pcb routing unroute")
    canonical = board_file(options, "pcb routing unroute")
    # input order, each net once: the result's items[] zip back to what was asked
    nets = comma_list(options, "nets")
    everything = bool(options.get("all"))
    at = text(options, "at")
    layer = integer(options, "layer")
    if not nets and not everything and not at:
        raise CLIError(
            "E_USAGE", "pcb routing unroute requires --nets A,B, --all, or --at x,y [--layer N]"
        )
    if everything or at or layer is not None:
        raise CLIError(
            "E_UNSUPPORTED",
            "Verified unroute currently supports explicit whole selected nets",
            {"write_attempted": False, "unsupported_options": ["all", "at", "layer"]},
        )
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
    plan = backend.invoke("unroute_nets", {**request, "apply": False}, timeout_seconds=120.0)
    scope["fingerprint"] = plan["unroute"]["fingerprint"]
    if options.get("dry_run"):
        preview = {
            "project": canonical,
            "pcb": plan.get("pcb"),
            "nets": plan.get("nets"),
            "at": plan.get("at"),
            "layer": plan.get("layer"),
            "to_delete": plan.get("to_delete"),
            "targets": plan.get("targets"),
            "unroute": plan["unroute"],
            "total": len(plan.get("targets") or []),
            "changes": [
                {"action": "delete_traces_and_vias", **(plan.get("to_delete") or {})},
                {
                    "action": "save_close_reopen_and_compare",
                    "only_if": "all protection checks pass",
                },
            ]
            if plan["unroute"]["selected_objects"]
            else [{"action": "no_change"}],
            "dangerous": True,
            "requires": "--dangerous with --confirm",
            "risk": _risk(
                "T2",
                "selected routing is deleted; saving requires complete protected-object readback. "
                "A protection failure stops Save/Close/Open and leaves persistence unknown. "
                "No automatic rollback or recovery deletion is performed",
            ),
        }
        return previewed(preview, scope)
    require_dangerous(options, "pcb routing unroute deletes routing that is not archived")
    try:
        confirmed(options, scope)
    except CLIError as error:
        error.details = {**(error.details or {}), "write_attempted": False}
        raise
    return backend.invoke(
        "unroute_nets",
        {**request, "apply": True, "expected": scope["fingerprint"]},
        timeout_seconds=600.0,
    )


def pour(options: dict[str, Any]) -> dict[str, Any]:
    check_gate(options, "pcb copper pour")
    canonical = board_file(options, "pcb copper pour")
    net, layer, margin = (
        text(options, "net"),
        integer(options, "layer"),
        number(options, "margin", 1.0),
    )
    if not net or layer is None or layer < 1 or margin is None or margin < 0:
        raise CLIError(
            "E_VALIDATION", "Explicit --net/--layer and a nonnegative --margin are required"
        )
    if options.get("replace") or options.get("remove"):
        raise CLIError(
            "E_UNSUPPORTED",
            "Copper replacement/removal is outside the verified subset",
            {"write_attempted": False},
        )
    request = {"project": canonical, "net": net, "layer": layer, "margin": margin, "start": False}
    backend = native()
    result = backend.invoke("plane_pour", {**request, "apply": False}, timeout_seconds=120.0)
    plan = result["copper"]
    scope = {"operation": "pcb_pour", **request, "fingerprint": plan["fingerprint"]}
    if options.get("dry_run"):
        from ..board_copper import layout_reminders

        return previewed(
            {
                "project": canonical,
                "pcb": result["pcb"],
                "net": net,
                "layer": layer,
                "copper": plan,
                "rectangle": plan["rectangle"],
                "layout_reminders": layout_reminders(plan),
                "changes": [
                    {
                        "action": "create_one_plane_shape",
                        **plan["target"],
                        "rectangle": plan["rectangle"],
                    },
                    {
                        "action": "generate_exact_assignment",
                        "targets": plan["expected_assignment_changes"],
                    },
                    {
                        "action": "save_close_reopen_and_compare",
                        "only_if": "generation and all protections pass",
                    },
                ],
                "risk": _risk(
                    "T1",
                    "One new plane on an empty rectangular signal-layer board; generation "
                    "and complete protection required before saving; no automatic rollback",
                ),
            },
            scope,
        )
    try:
        confirmed(options, scope)
    except CLIError as error:
        error.details = {**(error.details or {}), "write_attempted": False}
        raise
    return backend.invoke(
        "plane_pour",
        {**request, "apply": True, "expected": plan["fingerprint"]},
        timeout_seconds=600.0,
    )


def remove_copper(options: dict[str, Any]) -> dict[str, Any]:
    raise CLIError(
        "E_UNSUPPORTED",
        "Copper removal is outside the current verified subset",
        {"write_attempted": False},
    )


# -- inspection ------------------------------------------------------------------------


def info(options: dict[str, Any]) -> dict[str, Any]:
    """The board in numbers, with its stackup, net classes and keepouts."""
    canonical = board_file(options, "pcb inspect")
    from ..native_com_adapter import AdapterError, parse_route_layers

    if options.get("silk_text_uids") is not None:
        uids = comma_list(options, "silk_text_uids")
        pid = integer(options, "pid")
        if (
            not options.get("live_geometry")
            or pid is None
            or pid <= 0
            or not 1 <= len(uids) <= 8
            or len(set(uids)) != len(uids)
            or any(not uid.strip() or uid == "0" for uid in uids)
            or any(
                options.get(key)
                for key in (
                    "nets",
                    "layers",
                    "mask_inventory",
                    "drc_hazards",
                    "components",
                    "native_general_state",
                    "continue_existing",
                )
            )
        ):
            raise CLIError(
                "E_VALIDATION",
                "--silk-text-uids requires only --live-geometry, "
                "positive --pid and 1..8 unique nonzero native text UIDs",
            )
        backend = reader(options)
        if backend.read_timeout_seconds > 1200:
            raise CLIError("E_VALIDATION", "Silk surroundings observation has a1200-second maximum")
        return backend.invoke(
            "silk_environment_state",
            {
                "domain": "pcb",
                "project": canonical,
                "pid": pid,
                "start": False,
                "observe_only": True,
                "text_uids": uids,
            },
            timeout_seconds=backend.read_timeout_seconds,
        )
    if options.get("drc_hazards") and not options.get("live_geometry"):
        raise CLIError(
            "E_VALIDATION", "--drc-hazards requires --live-geometry and an explicit --pid"
        )
    nets = comma_list(options, "nets")
    try:
        layers = parse_route_layers(options.get("layers"))
    except AdapterError as error:
        raise CLIError(error.code, error.message, error.details) from error
    if options.get("mask_inventory") and (
        not options.get("live_geometry")
        or nets
        or layers
        or any(
            options.get(key)
            for key in ("components", "native_general_state", "continue_existing", "drc_hazards")
        )
    ):
        raise CLIError(
            "E_VALIDATION",
            "--mask-inventory requires only --live-geometry and --pid; "
            "it cannot be combined with material or hazard selectors",
        )
    routepass_request = None
    component_scope = bool(options.get("components"))
    if component_scope and (
        not options.get("live_geometry")
        or len(nets) != 1
        or len(layers) != 1
        or options.get("native_general_state")
        or options.get("continue_existing")
    ):
        raise CLIError(
            "E_VALIDATION",
            "--components requires --live-geometry, --pid, one actual --nets and one "
            "--layers; legacy route selectors cannot be combined",
        )
    if options.get("native_general_state") and (
        not options.get("live_geometry") or nets != ["GND"] or len(layers) != 1
    ):
        raise CLIError(
            "E_VALIDATION",
            "--native-general-state requires --live-geometry, --pid, --nets GND "
            "and one --layers conductor",
        )
    if options.get("continue_existing") and (
        not options.get("live_geometry") or nets != ["GND"] or len(layers) != 1
    ):
        raise CLIError(
            "E_VALIDATION",
            "--continue-existing observation requires --live-geometry, --nets GND "
            "and exactly one --layers conductor",
        )
    if nets or layers:
        if options.get("live_geometry"):
            if len(nets) != 1 or len(layers) != 1:
                raise CLIError(
                    "E_VALIDATION",
                    "Native RoutePass material observation requires exactly one --nets "
                    "and one --layers with --live-geometry",
                )
            routepass_request = {
                "net": nets[0],
                "layer": layers[0],
                **(
                    {"continuation": "existing-components"}
                    if options.get("continue_existing")
                    else {}
                ),
            }
        elif not 2 <= len(set(nets)) <= 8 or not 1 <= len(layers) <= 8:
            raise CLIError(
                "E_VALIDATION",
                "Selected Master clearance queries require 2..8 --nets "
                "and 1..8 --layers; separate from --live-geometry",
            )
    pid = integer(options, "pid")
    if options.get("live_geometry") and (pid is None or pid <= 0):
        raise CLIError(
            "E_VALIDATION", "pcb inspect --live-geometry requires an explicit positive --pid"
        )
    if pid is not None and not options.get("live_geometry"):
        raise CLIError("E_VALIDATION", "--pid applies to pcb inspect --live-geometry")
    backend = reader(options)
    # Whole-board reads share the published bounded query budget. Component
    # material queries reserve more time for all native masks/comparisons.
    live_budget = 1200
    if options.get("live_geometry") and backend.read_timeout_seconds > live_budget:
        raise CLIError(
            "E_VALIDATION",
            f"Live geometry observation has a maximum {live_budget}-second budget",
        )
    return backend.invoke(
        "board_info",
        {
            "project": canonical,
            "start": False,
            "geometry": True,
            "pid": pid,
            **({"mask_inventory_only": True} if options.get("mask_inventory") else {}),
            **({"drc_hazards": True} if options.get("drc_hazards") else {}),
            **(
                {"component_request" if component_scope else "routepass_request": routepass_request}
                if routepass_request
                else {}
            ),
            **({"native_general_state": True} if options.get("native_general_state") else {}),
        }
        if options.get("live_geometry")
        else {
            "project": canonical,
            "start": True,
            **({"nets": nets, "layers": layers} if nets else {}),
        },
        timeout_seconds=backend.read_timeout_seconds,
    )


def geometry(options: dict[str, Any]) -> dict[str, Any]:
    """The board as data: outline, pads, traces, vias, planes, holes, silkscreen, parts."""
    canonical = board_file(options, "pcb snapshot")
    output = output_file(options, "pcb snapshot", ".json")
    params: dict[str, Any] = {
        "project": canonical,
        "replace": bool(options.get("replace")),
        "refdes": comma_list(options, "refdes"),
        "nets": comma_list(options, "nets"),
        "start": True,
        "artifact_generation": options.get("_artifact_generation"),
    }
    if output is not None:
        params["output"] = str(output)
    return native().invoke("board_geometry", params, timeout_seconds=600.0)


def render(options: dict[str, Any]) -> dict[str, Any]:
    """A PNG of the board drawn from its geometry (no screen needed); with --refdes,
    the parts named and what is around them, larger."""
    canonical = board_file(options, "pcb render")
    output = output_file(options, "pcb render", ".png", required=True)
    side = (text(options, "side") or "top").lower()
    if side not in {"top", "bottom"}:
        raise CLIError("E_VALIDATION", "--side is top or bottom", {"side": side})
    scale = number(options, "scale")
    if scale is not None and not 1.0 <= scale <= 200.0:
        raise CLIError("E_VALIDATION", "--scale must be between 1 and 200")
    params: dict[str, Any] = {
        "project": canonical,
        "output": str(output),
        "side": side,
        "replace": bool(options.get("replace")),
        "start": True,
        "artifact_generation": options.get("_artifact_generation"),
    }
    if scale is not None:
        params["scale"] = scale
    if options.get("refdes") is not None:
        params["refdes"] = comma_list(options, "refdes")
    return native().invoke("render_board", params, timeout_seconds=600.0)


def show(options: dict[str, Any]) -> dict[str, Any]:
    """Bring the board to the front under the top view (or the scheme named), fitted,
    or zoomed onto the parts --refdes names."""
    canonical = board_file(options, "pcb view")
    output = output_file(options, "pcb view", ".png")
    params: dict[str, Any] = {"project": canonical, "start": True}
    if options.get("_view_plan"):
        params["scheme_plan"] = options["_view_plan"]
    if options.get("_artifact_generation"):
        params["artifact_generation"] = options["_artifact_generation"]
    if text(options, "scheme"):
        params["scheme"] = text(options, "scheme")
    if options.get("top_view"):
        params["top_view"] = True
    if options.get("refdes") is not None:
        params["refdes"] = comma_list(options, "refdes")
    if output is not None:
        params["output"] = str(output)
    return native().invoke("show_board", params, timeout_seconds=120.0)


def metrics(options: dict[str, Any]) -> dict[str, Any]:
    """How good the placement and routing in a geometry file are, as numbers."""
    from .. import board_metrics

    if options.get("project") is not None:
        if options.get("geometry") is not None:
            raise CLIError("E_USAGE", "pcb metrics takes --geometry or --project, not both")
        read = native().invoke(
            "board_geometry",
            {"project": board_file(options, "pcb metrics"), "start": True},
            timeout_seconds=600.0,
        )
        model = read.get("model")
        if not isinstance(model, dict):
            raise CLIError("E_SERVER", "the adapter returned no board model")
    else:
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
    """Layout's Batch DRC, then every hazard it left on the board, and the board rules
    DRC does not cover: parts unplaced, off the board or overlapping, decoupling far
    from its IC, connectors far from an edge, acute corners, nets still open,
    silkscreen texts that will not read and silkscreen lines over pads."""
    from .. import board_metrics
    from ..native_verification import verify_batch_drc_report

    canonical = board_file(options, "pcb checks run")
    minimum = number(options, "min_text_height", board_metrics.MIN_TEXT_HEIGHT_MM)
    if minimum is None or not 0.1 <= minimum <= 5.0:
        raise CLIError(
            "E_VALIDATION",
            "--min-text-height is millimetres, from 0.1 to 5",
            {"min_text_height": options.get("min_text_height")},
        )
    backend = native()
    params = {
        "project": canonical,
        "start": True,
        "run": not bool(options.get("no_run")),
        "online": bool(options.get("online")),
        "rules": options.get("rules"),
        "elements": options.get("elements"),
    }
    try:
        result = backend.invoke("batch_drc", params, timeout_seconds=180.0)
    except CLIError as error:
        if error.code != "E_TIMEOUT":
            raise
        raise CLIError(
            "E_PROJECT_INVALID",
            "Batch DRC completion is unknown after a timeout; inspect the owned session "
            "before another check",
            {
                "stage": "batch_drc",
                "problem": "verification_incomplete",
                "outcome": "unknown",
                "verification": {"valid": False, "status": "incomplete"},
                "cause": {"code": error.code, "details": error.details or {}},
                "next_action": "inspect the current owned check; do not blindly rerun or "
                "restart an attached user session",
                "_untrusted": ["cause.details"],
            },
        ) from error
    if result.get("native_reports"):
        try:
            fresh = backend.invoke("batch_drc", {**params, "run": False}, timeout_seconds=60.0)
            result["hazards"] = fresh["hazards"]
            result["online"] = fresh["online"]
            from ..drc_engine import compare_hazards

            result["hazard_content_verification"] = compare_hazards(
                result["native_reports"].get("violations", []), fresh["hazards"]
            )
            result["reopened_hazards_match"] = (
                result["hazard_content_verification"]["valid"]
                and len(fresh["hazards"]) == result["count"]
            )
            if not result["reopened_hazards_match"]:
                result["source"]["complete"] = False
                result["passes"] = result["clean"] = False
        except CLIError as error:
            error.details = {
                **(error.details or {}),
                "native_result": result,
                "write_attempted": True,
            }
            raise
    try:
        read = backend.invoke(
            "board_geometry", {"project": canonical, "start": True}, timeout_seconds=60.0
        )
    except CLIError as error:
        if result.get("native_reports"):
            error.details = {
                **(error.details or {}),
                "native_result": result,
                "write_attempted": True,
            }
        raise
    if result.get("native_reports"):
        from ..cell_refresh import _canonical, digest, physical_model

        model = read.get("model") or {}
        observed = _canonical(
            {
                "components": model.get("physical_instances"),
                "stackup": model.get("stackup"),
                "model": physical_model(
                    {
                        k: v
                        for k, v in model.items()
                        if k not in {"components", "physical_instances", "stackup"}
                    }
                ),
            }
        )
        matched = (
            observed == result.get("geometry_before")
            and result.get("reopened_hazards_match") is True
        )
        result["save_reopen_verification"] = {
            "valid": bool(matched),
            "snapshot_sha256": digest(observed),
            "source": "separate native worker geometry after saved board reopen",
        }
        if matched:
            result.update(persistence_state="saved", persistence_verified=True)
        else:
            result["source"]["complete"] = False
    verification = verify_batch_drc_report(result, project=canonical, run_requested=params["run"])
    result["verification"] = verification
    result["passes"] = verification["valid"] and result.get("passes") is True
    rules = board_metrics.board_rules(read.get("model") or {}, min_text_height=minimum)
    result["board_rules"] = rules
    result["board_rule_count"] = len(rules)
    result["clean"] = (
        verification["valid"]
        and result.get("clean") is True
        and not any(rule["severity"] in {"high", "medium"} for rule in rules)
    )
    result["_untrusted"] = [*(result.get("_untrusted") or []), "board_rules"]
    return result


# -- fabrication -------------------------------------------------------------------------


def export(options: dict[str, Any]) -> dict[str, Any]:
    """The fabrication package: ODB++, Gerber, NC drill, centroid, BOM and a manifest."""
    canonical = board_file(options, "manufacturing export")
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
        "manufacturing export",
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
