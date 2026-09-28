"""library build | import."""

from __future__ import annotations

import hashlib
from typing import Any

from ..errors import CLIError
from .common import (
    check_gate,
    comma_list,
    confirmed,
    continue_on_error,
    integer,
    native,
    previewed,
    project_file,
    read_design,
    require_dangerous,
    text,
)


def build(options: dict[str, Any]) -> dict[str, Any]:
    """The padstacks, cells and parts a design needs, imported into the central library.

    The plan is pure Python (`xpedition_cli.library_hkp`), so the dry run shows every
    part-to-cell mapping without Xpedition. The confirmed run imports the three HKP
    texts through the stock converters and, with --package, runs the packager.
    """
    from .. import library_hkp, schematic_layout

    check_gate(options, "library build")
    project = project_file(options, "library build")
    design_file, design = read_design(options, "library build")
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
    ).hexdigest()
    scope = {
        "operation": "library_build",
        "project": str(project),
        "partition": partition,
        "digest": digest[:16],
        "package": bool(options.get("package")),
    }
    if options.get("dry_run"):
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
                *([{"action": "package_design"}] if options.get("package") else []),
            ],
            "summary": summary,
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "the project's central library gains padstacks, a cell partition and a "
                    "parts partition; Designer's project is closed and reopened meanwhile"
                ),
            },
        }
        if plan.cell_partitions:
            preview["changes"].append(
                {
                    "action": "register_cell_partitions_in_project",
                    "partitions": sorted(plan.cell_partitions),
                    "note": "cells of KiCad footprints imported earlier; the parts reference them",
                }
            )
        return previewed(preview, scope)
    backend = native()
    confirmed(options, scope)
    request = {
        "project": str(project),
        "partition": partition,
        "cell_partitions": sorted(plan.cell_partitions),
        **texts,
    }
    result = backend.invoke("library_import", request, timeout_seconds=900.0)
    if options.get("package") and result.get("ok"):
        result["package"] = backend.invoke(
            "package", {"project": str(project)}, timeout_seconds=900.0
        )
    result["summary"] = summary
    result["_untrusted"] = [*(result.get("_untrusted") or []), "summary", "package"]
    return result


def import_libraries(options: dict[str, Any]) -> dict[str, Any]:
    """KiCad footprint libraries as cell partitions of the central library.

    The dry run reads only files -- the libraries, their footprint counts and which
    partitions the central library has already -- and binds the token to that list,
    so a library that appeared or a partition imported in between refuses the
    confirmation.
    """
    from .. import kicad_import

    check_gate(options, "library import")
    project = project_file(options, "library import")
    libraries = comma_list(options, "libraries")
    root = text(options, "root") or None
    limit = integer(options, "limit", 0) or 0
    if limit < 0:
        raise CLIError("E_VALIDATION", "--limit must not be negative")
    plan = kicad_import.plan(project, libraries, root, limit)
    keep_going = continue_on_error(options)
    scope = {
        "operation": "library_import",
        "project": plan["project"],
        "root": plan["root"],
        "libraries": [(row["library"], row["exists"]) for row in plan["libraries"]],
        "continue_on_error": keep_going,
    }
    merging = [row["partition"] for row in plan["libraries"] if row["exists"]]
    if options.get("dry_run"):
        preview = {
            **plan,
            "dangerous": bool(merging),
            "changes": [
                {"action": "close_designer_project", "reopened_after": True},
                *(
                    {
                        "action": "merge_partition" if row["exists"] else "create_partition",
                        "partition": row["partition"],
                        "footprints": row["footprints"],
                    }
                    for row in plan["libraries"]
                ),
            ],
            "risk": {
                "tier": "T2" if merging else "T1",
                "blast_radius": (
                    "one cell partition per library in the project's central library -- an "
                    "existing one is merged, its same-named cells overwritten -- and the "
                    "library's shared padstack database"
                ),
            },
        }
        return previewed(preview, scope)
    if merging:
        require_dangerous(
            options, "library import overwrites same-named cells in partitions that exist"
        )
    backend = native()
    confirmed(options, scope)
    result = backend.invoke(
        "kicad_import",
        {
            "project": plan["project"],
            "root": plan["root"],
            "libraries": [row["library"] for row in plan["libraries"]],
            "continue_on_error": keep_going,
        },
        timeout_seconds=kicad_import.WRITE_TIMEOUT_SECONDS,
    )
    return kicad_import.results(result)
