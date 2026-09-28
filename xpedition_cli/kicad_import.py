"""`library import`: KiCad footprint libraries as cell partitions of a central library.

Every `.pretty` folder becomes one cell partition named after it; the conversion runs
in the native adapter through the stock HKP converters (`_kicad_import`). This module
is the command's pure half: the dry run's plan -- which libraries, how many
footprints, which partitions the central library already has -- read from the files
alone, and the per-library result the confirmed run reports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import kicad_footprints
from .errors import CLIError

WRITE_TIMEOUT_SECONDS = 6 * 3600.0  # all 155 KiCad libraries take about fifteen minutes


def central_library(project: Path) -> Path:
    """The `.lmc` the project names (`KEY CentralLibrary`)."""
    try:
        text = project.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CLIError("E_IO", f"cannot read the project file: {exc}") from exc
    for line in text.splitlines():
        if line.startswith("KEY CentralLibrary "):
            lmc = Path(line[len("KEY CentralLibrary ") :].strip().strip('"'))
            return lmc if lmc.is_absolute() else project.parent / lmc
    raise CLIError(
        "E_NOT_FOUND",
        "the project names no CentralLibrary",
        {"project": str(project), "_untrusted": ["project"]},
    )


def plan(project: Path, libraries: list[str], root: str | None, limit: int) -> dict[str, Any]:
    """What a confirmed import would convert; nothing is written."""
    if not project.is_file():
        raise CLIError(
            "E_NOT_FOUND",
            "the project file does not exist",
            {"path": str(project), "_untrusted": ["path"]},
        )
    folder = Path(root).expanduser() if root else kicad_footprints.default_root()
    if folder is None or not folder.is_dir():
        raise CLIError(
            "E_NOT_FOUND",
            "the KiCad footprint folder was not found",
            {"hint": "pass --root, or set XPEDITION_KICAD_FOOTPRINTS"},
        )
    pretties = kicad_footprints.libraries(folder)
    if libraries:
        stems = {(w[:-7] if w.lower().endswith(".pretty") else w).lower() for w in libraries}
        pretties = [p for p in pretties if p.name[:-7].lower() in stems]
        missing = sorted(stems - {p.name[:-7].lower() for p in pretties})
        if missing:
            raise CLIError(
                "E_NOT_FOUND",
                "KiCad libraries were not found",
                {"libraries": missing, "_untrusted": ["libraries"]},
            )
    if limit > 0:
        pretties = pretties[:limit]
    if not pretties:
        raise CLIError("E_VALIDATION", "no KiCad library to import", {"root": str(folder)})
    lmc = central_library(project)
    rows = []
    for pretty in pretties:
        partition = kicad_footprints.partition_name(pretty.name)
        rows.append(
            {
                "library": pretty.name[:-7],
                "partition": partition,
                "footprints": sum(1 for _ in pretty.glob("*.kicad_mod")),
                "exists": (lmc.parent / "CellDBLibs" / f"{partition}.cel").is_file(),
            }
        )
    return {
        "project": str(project),
        "library": str(lmc),
        "root": str(folder),
        "libraries": rows,
        "total": len(rows),
        "footprints": sum(row["footprints"] for row in rows),
    }


def results(adapter_result: dict[str, Any]) -> dict[str, Any]:
    """The adapter's run with CLI-SPEC §15.5's per-library items and summary."""
    items = []
    for record in adapter_result.get("partitions") or []:
        item = {
            "target": record.get("library"),
            "ok": bool(record.get("ok")),
            "partition": record.get("partition"),
            "cells": record.get("cells"),
            "padstacks": record.get("padstacks"),
            "issues": record.get("issues"),
        }
        dropped = [*(record.get("rejected") or []), *(record.get("crashed") or [])]
        if dropped:
            # imported, but without these: a design naming one of them gets no cell
            item["dropped"] = len(dropped)
            item["dropped_samples"] = dropped[:20]
        if record.get("skipped"):
            item["skipped"] = record["skipped"]
        if not item["ok"]:
            item["error"] = {"code": "E_IO", "retryable": False, "steps": record.get("steps")}
        items.append(item)
    succeeded = sum(1 for item in items if item["ok"])
    return {
        **adapter_result,
        "items": items,
        # attempted items only; with --continue-on-error false the rest are in `skipped`
        "summary": {
            "total": len(items),
            "succeeded": succeeded,
            "failed": len(items) - succeeded,
            "dropped_footprints": sum(int(item.get("dropped") or 0) for item in items),
        },
        "skipped": list(adapter_result.get("skipped") or []),
        "_untrusted": sorted({*adapter_result.get("_untrusted", []), "items"}),
    }
