"""Plan previously ungated session/artifact/check effects before invoking a handler.

This boundary is an interim canonical-runtime gate, not a substitute for the
tool-driver SDK's durable operation journal and task-scope authorization.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .common import check_gate, confirmed, previewed
from .registry import Command


def invoke(command: Command, handler: Callable, options: dict[str, Any]) -> dict[str, Any]:
    check_gate(options, command.path)
    if command.path == "session attach" and options.get("project_source_operation_id"):
        # This narrow dedicated-owner path implements its own bound source
        # preview/confirm scope. The outer SDK gate remains unchanged.
        return handler(options)
    params = {
        key: value
        for key, value in options.items()
        if not key.startswith("_")
        and key
        not in {"dry_run", "confirm", "help", "format", "compact", "fields", "quiet", "json"}
    }
    dependencies = {}
    drc = None
    view = None
    drawing_review = None
    release = None
    if command.path == "project backup":
        from ..errors import CLIError
        from .project import release_preflight

        release = release_preflight(Path(str(params["project"])).expanduser().resolve())
        if (
            release.get("read_only") is not True
            or type(release.get("save_count")) is not int
            or release["save_count"] != 0
            or type(release.get("close_count")) is not int
            or release["close_count"] != 0
            or not isinstance(release.get("release_preflight"), dict)
            or set(release["release_preflight"]) != {"library", "schematic", "pcb"}
        ):
            raise CLIError(
                "E_HUMAN_REQUIRED",
                "The complete read-only project-release preflight is unavailable",
                {"write_attempted": False, "release_preflight": release},
            )
        dependencies["release_preflight"] = release
    if command.path in {"schematic view", "schematic export"}:
        from .. import schematic_review
        from .common import reader

        if command.path == "schematic view":
            design, _ = reader(options).load(str(params["project"]), domain="schematic")
            drawing_review = schematic_review.native_drawing(design, int(params.get("sheet") or 1))
            dependencies["drawing_review_source_sha256"] = hashlib.sha256(
                json.dumps(design, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
        else:
            drawing_review = schematic_review.unobserved_drawing(
                "sch2pdf_export_has_no_bound_Designer_drawing_model"
            )
    if command.path == "pcb view":
        from .common import comma_list, reader

        view = reader(options).invoke(
            "board_view_state",
            {
                "project": params["project"],
                "scheme": params.get("scheme"),
                "top_view": params.get("top_view"),
                "refdes": comma_list(options, "refdes"),
            },
            timeout_seconds=60.0,
        )
        dependencies["display_scheme"] = view["scheme_plan"]
    if command.path == "pcb checks run":
        from .. import drc_engine, native_com_adapter
        from ..errors import CLIError

        try:
            board = native_com_adapter._layout_board_path(params)
            config = drc_engine.configurations(params, board)
        except (ValueError, OSError, native_com_adapter.AdapterError) as error:
            raise CLIError("E_VALIDATION", str(error), {"write_attempted": False}) from error
        dependencies.update(config)
        drc = {
            "configurations": config,
            "native_outputs": [str(p) for p in drc_engine.outputs(board).values()],
            "run_work_directory": str(board.parent / "Work" / "xpedition-cli" / "native-drc"),
            "declared_side_effects": drc_engine.planned_side_effects(board),
            "side_effect_boundary": (
                "Known report, configuration, staging and lifecycle log paths; "
                "other native side effects remain unclassified."
            ),
            "lifecycle": [
                "save",
                "normal close",
                "stage byte-identical copies of selected configurations in owned run directory",
                "copy and hash prior effective configurations before Go; keep original paths",
                "native engine Go once",
                "consume only fresh automatic Config outputs produced after this Go",
                "reopen and read hazards",
            ],
            "rule_modification": False,
        }
    for key in ("project", "file", "design", "geometry", "baseline", "backup", "template"):
        if not params.get(key):
            continue
        path = Path(str(params[key])).expanduser().resolve()
        if path.is_file():
            dependencies[key] = {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    digest = hashlib.sha256(
        json.dumps(
            {"params": params, "dependencies": dependencies}, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    scope = {"operation": command.path, "digest": digest}
    if options.get("dry_run"):
        design_preview = handler(options) if command.path == "schematic plan preview" else None
        return previewed(
            {
                "command": command.path,
                "level": "plan",
                "effects": command.extra.get("effects", []),
                "params": params,
                "input_dependencies": dependencies,
                "changes": [{"action": "execute", "command": command.path}],
                **({"design_preview": design_preview} if design_preview is not None else {}),
                **({"native_check_plan": drc} if drc is not None else {}),
                **({"view_plan": {k: v for k, v in view.items() if k != "model"}} if view else {}),
                **({"drawing_review": drawing_review} if drawing_review is not None else {}),
                **({"release_plan": release} if release is not None else {}),
                "native_observed": release is not None,
                "native_completion": "not_run",
                "limitations": [
                    "This planning step does not execute the native checker or create the output."
                ],
            },
            scope,
        )
    confirmed(options, scope)
    if view:
        options = {**options, "_view_plan": view["scheme_plan"]}
    if release is not None:
        options = {**options, "_expected_release_preflight": release}
    result = handler(options)
    if drawing_review is not None:
        result["drawing_review"] = drawing_review
        result["_untrusted"] = [*(result.get("_untrusted") or []), "drawing_review"]
    return result
