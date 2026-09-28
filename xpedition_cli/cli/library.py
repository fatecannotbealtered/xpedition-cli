"""library build | import | list | show | check | add | render."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..errors import CLIError
from .common import (
    check_gate,
    comma_list,
    confirmed,
    continue_on_error,
    input_file,
    integer,
    native,
    output_file,
    previewed,
    project_file,
    read_design,
    reader,
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


# -- reading the library -------------------------------------------------------------

LIBRARY_KINDS = ("parts", "cells", "symbols", "padstacks")
EXPORT_TIMEOUT = 600.0


def cache_root() -> Path:
    from ..audit import config_dir

    return config_dir() / "cache" / "library"


def load_library(
    options: dict[str, Any],
    project: Path,
    partitions: list[str] | None = None,
    kinds: tuple[str, ...] = ("parts", "cells", "padstacks"),
) -> tuple[Any, dict[str, Any]]:
    """The project's central library as `library_read` records, and where it came from.

    The adapter exports what `kinds` names through the stock converters into a cache
    (a database unchanged since its last export is not exported again); the symbols
    are read from their files.
    """
    from .. import library_read as R

    backend = reader(options)
    exported = backend.invoke(
        "library_export",
        {
            "project": str(project),
            "cache": str(cache_root()),
            "kinds": [kind for kind in kinds if kind != "symbols"],
            "partitions": partitions or None,
        },
        timeout_seconds=max(backend.read_timeout_seconds, EXPORT_TIMEOUT),
    )
    library = R.Library(root=str(exported.get("root") or ""))
    for item in exported.get("files") or []:
        path = Path(str(item["path"]))
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            library.failed.append(
                {"kind": item["kind"], "partition": item["partition"], "error": str(exc)}
            )
            continue
        if item["kind"] == "parts":
            library.parts += R.parse_parts(content, str(item["partition"]))
        elif item["kind"] == "cells":
            library.cells += R.parse_cells(content, str(item["partition"]))
        elif item["kind"] == "padstacks":
            library.padstacks = R.parse_padstacks(content)
    library.failed += list(exported.get("failed") or [])
    if "symbols" in kinds or "parts" in kinds:
        root = Path(str(exported.get("symbols") or ""))
        for partition, name, path in R.symbol_files(root, partitions):
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            symbol = R.parse_symbol(content, partition, name)
            symbol["version"] = int(path.suffix[1:])
            library.symbols.append(symbol)
    meta = {
        "library": exported.get("library"),
        "exported": exported.get("exported", 0),
        "failed": library.failed,
    }
    return library, meta


def _partitions(options: dict[str, Any]) -> list[str] | None:
    raw = options.get("partition")
    if raw is None:
        return None
    return comma_list(options, "partition")


def list_items(options: dict[str, Any]) -> dict[str, Any]:
    """One kind of thing the central library holds, a page at a time."""
    from .. import library_read as R
    from ..query_page import query_page

    project = project_file(options, "library list")
    kind = text(options, "kind") or "parts"
    partitions = _partitions(options)
    wanted = (kind,) if kind != "symbols" else ("symbols",)
    library, meta = load_library(options, project, partitions, wanted)
    if kind == "parts":
        rows = [
            R.part_row(p)
            for p in sorted(library.parts, key=lambda p: (p["partition"], p["number"]))
        ]
    elif kind == "cells":
        rows = [
            R.cell_row(c) for c in sorted(library.cells, key=lambda c: (c["partition"], c["name"]))
        ]
    elif kind == "symbols":
        rows = [
            R.symbol_row(s)
            for s in sorted(library.symbols, key=lambda s: (s["partition"], s["name"]))
        ]
    else:
        rows = R.padstack_rows(library)
    result = query_page(
        rows,
        query=options.get("query"),
        limit=options.get("limit"),
        offset=int(options.get("offset") or 0),
    )
    partitions_seen = sorted(
        {str(row.get("partition", "")) for row in rows if row.get("partition")}
    )
    return {
        "project": str(project),
        "library": meta["library"],
        "kind": kind,
        "partitions": partitions_seen,
        "total": len(rows),
        **result,
        "failed": meta["failed"],
        "_untrusted": ["project", "library", "items", "partitions", "failed"],
    }


def _geometry(library: Any, cell: dict[str, Any]) -> dict[str, Any]:
    from .. import library_read as R

    names = sorted({pin["padstack"] for pin in cell["pins"] + cell["holes"]})
    return {name: R.padstack_geometry(library.padstacks, name) for name in names}


def show(options: dict[str, Any]) -> dict[str, Any]:
    """One part in full -- its pin map, symbol, cell and padstacks, and what is wrong with
    it -- or one cell and the parts that use it."""
    from .. import library_read as R

    project = project_file(options, "library show")
    number = text(options, "part")
    name = text(options, "cell")
    if bool(number) == bool(name):
        raise CLIError("E_USAGE", "library show takes --part NUMBER or --cell NAME")
    library, meta = load_library(options, project)
    if number:
        found = library.find_parts(number)
        if not found:
            raise CLIError(
                "E_NOT_FOUND",
                f"the library has no part {number!r}",
                {"part": number, "hint": "library list --query <text> finds part numbers"},
            )
        part = found[0]
        symbols = [library.find_symbol(ref) for ref in part["symbols"]]
        cell = library.find_cell(part["cell"])
        return {
            "project": str(project),
            "library": meta["library"],
            "part": part,
            "also_in": [p["partition"] for p in found[1:]],
            "symbols": [s for s in symbols if s is not None],
            "cell": cell,
            "padstacks": _geometry(library, cell) if cell else {},
            "findings": R.check_part(library, part),
            "_untrusted": [
                "project",
                "library",
                "part",
                "symbols",
                "cell",
                "padstacks",
                "findings",
            ],
        }
    cell = library.find_cell(name)
    if cell is None:
        raise CLIError(
            "E_NOT_FOUND",
            f"the library has no cell {name!r}",
            {"cell": name, "hint": "library list --kind cells --query <text> finds cells"},
        )
    return {
        "project": str(project),
        "library": meta["library"],
        "cell": cell,
        "padstacks": _geometry(library, cell),
        "used_by": sorted(p["number"] for p in library.parts if p["cell"] == name),
        "_untrusted": ["project", "library", "cell", "padstacks", "used_by"],
    }


def check(options: dict[str, Any]) -> dict[str, Any]:
    """What is wrong across the library: parts against their cells, symbols and pin maps,
    cells against the padstacks, symbols with repeated pin names, part numbers in
    two partitions."""
    from .. import library_read as R
    from ..query_page import query_page

    project = project_file(options, "library check")
    partitions = _partitions(options)
    library, meta = load_library(options, project)
    findings = R.check(library, partitions)
    counts = {
        level: sum(1 for f in findings if f["severity"] == level)
        for level in ("high", "medium", "low")
    }
    result = query_page(
        findings,
        query=options.get("query"),
        limit=options.get("limit"),
        offset=int(options.get("offset") or 0),
    )
    scope = [
        p
        for p in library.parts
        if not partitions or p["partition"].casefold() in {x.casefold() for x in partitions}
    ]
    return {
        "project": str(project),
        "library": meta["library"],
        "parts": len(scope),
        "cells": len(library.cells),
        "symbols": len(library.symbols),
        "counts": counts,
        "clean": counts["high"] == 0 and counts["medium"] == 0 and not meta["failed"],
        **result,
        "failed": meta["failed"],
        "_untrusted": ["project", "library", "items", "failed"],
    }


def _parts_plan(options: dict[str, Any], command: str, existing: Any) -> tuple[Path, Any]:
    from .. import library_parts as P

    path = input_file(options, "file", command, "the parts file")
    try:
        spec = P.load(path)
        if text(options, "partition"):
            spec = {**spec, "partition": text(options, "partition")}
        root = text(options, "kicad-root") or None
        return path, P.plan(spec, existing, root)
    except P.PartsFileError as exc:
        raise CLIError("E_VALIDATION", str(exc), {"file": str(path)}) from exc


def add(options: dict[str, Any]) -> dict[str, Any]:
    """Parts into the central library from a parts file: symbol, footprint and pin map.

    The dry run reads the library and says, item by item, what is added, what is
    already there (and left alone) and what would be replaced. Replacing a part,
    cell, padstack or pad that exists with other content needs --dangerous.
    """
    check_gate(options, "library add")
    project = project_file(options, "library add")
    library, meta = load_library(options, project)
    path, plan = _parts_plan(options, "library add", library)
    texts = plan.texts()
    digest = hashlib.sha256(
        "".join(texts[k] for k in sorted(texts)).encode("utf-8")
        + "".join(f"{k}\n{v}" for k, v in sorted(plan.symbols.items())).encode("utf-8")
    ).hexdigest()
    scope = {
        "operation": "library_add",
        "project": str(project),
        "partition": plan.partition,
        "digest": digest[:16],
        "replaces": sorted(plan.replaces),
    }
    if options.get("dry_run"):
        preview = {
            "file": str(path),
            "partition": plan.partition,
            "dangerous": bool(plan.replaces),
            "replaces": sorted(plan.replaces),
            "actions": plan.actions,
            "parts": plan.parts,
            "changes": [
                *(
                    [
                        {
                            "action": "write_symbols",
                            "folder": f"SymbolLibs/{plan.partition}/sym",
                            "symbols": sorted(plan.symbols),
                        }
                    ]
                    if plan.symbols
                    else []
                ),
                *(
                    [
                        {
                            "action": "merge_padstacks",
                            "into": "Layout/PadstackDB.psk",
                            "padstacks": sorted(plan.library.padstacks),
                        }
                    ]
                    if plan.library.padstacks
                    else []
                ),
                *(
                    [{"action": "merge_cells", "file": f"CellDBLibs/{plan.partition}.cel"}]
                    if texts["cells"]
                    else []
                ),
                {"action": "merge_parts", "file": f"PartsDBLibs/{plan.partition}.pdb"},
                {"action": "register_in_project", "lists": ["Symbols", "PDBs", "2dCellLibraries"]},
            ],
            "risk": {
                "tier": "T2" if plan.replaces else "T1",
                "blast_radius": (
                    "the project's central library gains the parts, their symbols, cells and "
                    "padstacks; what exists already with other content is replaced, and every "
                    "part using a replaced cell or padstack changes with it"
                ),
            },
            "hint": "library render --file <parts file> --output parts.png draws each part first",
        }
        preview["library"] = meta["library"]
        return previewed(preview, scope)
    if plan.replaces:
        require_dangerous(
            options,
            f"library add replaces what the library holds ({', '.join(sorted(plan.replaces)[:5])})",
        )
    backend = native()
    confirmed(options, scope)
    request = {
        "project": str(project),
        "partition": plan.partition,
        "cell_partitions": sorted(plan.library.cell_partitions),
        "symbols": plan.symbols,
        "replace": any(item["action"].startswith("replace") for item in plan.actions["parts"]),
        **texts,
    }
    result = backend.invoke("library_import", request, timeout_seconds=900.0)
    added = [part["number"] for part in plan.parts]
    verification: dict[str, Any] = {"parts": added}
    if result.get("ok"):
        after, _meta = load_library(options, project)
        from .. import library_read as R

        found = {number: after.find_parts(number) for number in added}
        missing = sorted(n for n, rows in found.items() if not rows)
        findings = [f for n, rows in found.items() if rows for f in R.check_part(after, rows[0])]
        verification.update(
            {
                "missing": missing,
                "findings": findings,
                "verified": not missing and not any(f["severity"] == "high" for f in findings),
            }
        )
    else:
        verification["verified"] = False
    result["verification"] = verification
    result["ok"] = bool(result.get("ok")) and verification.get("verified", False)
    result["_untrusted"] = [*(result.get("_untrusted") or []), "verification"]
    return result


def render(options: dict[str, Any]) -> dict[str, Any]:
    """A PNG per part: the symbol beside the footprint, from the library (--part) or
    from a parts file not added yet (--file), to look at before and after adding."""
    from .. import library_parts as P
    from .. import library_render as V

    number = text(options, "part")
    has_file = bool(text(options, "file"))
    if bool(number) == has_file:
        raise CLIError("E_USAGE", "library render takes --part NUMBER or --file PARTS.json")
    output = output_file(options, "library render", ".png", required=True)
    assert output is not None
    replace = bool(options.get("replace"))
    library = None
    if number or text(options, "project"):
        project = project_file(options, "library render")
        library, _meta = load_library(options, project)
    if number:
        assert library is not None
        found = library.find_parts(number)
        if not found:
            raise CLIError("E_NOT_FOUND", f"the library has no part {number!r}", {"part": number})
        part = found[0]
        views = [
            {
                "number": part["number"],
                "description": part["description"],
                "symbol": library.find_symbol(part["symbols"][0]) if part["symbols"] else None,
                "cell": library.find_cell(part["cell"]),
                "padstacks": library.padstacks,
            }
        ]
    else:
        _path, plan = _parts_plan(options, "library render", library)
        views = P.views(plan, library)
    targets = {
        view["number"]: output
        if len(views) == 1
        else output.with_name(f"{output.stem}-{_file_safe(view['number'])}{output.suffix}")
        for view in views
    }
    if not replace:
        existing = [str(p) for p in targets.values() if p.exists()]
        if existing:
            raise CLIError(
                "E_CONFLICT",
                "output file already exists",
                {"path": existing[0], "hint": "--replace"},
            )
    pictures = [
        dict(V.render(view, targets[view["number"]], replace=True), part=view["number"])
        for view in views
    ]
    return {"pictures": pictures, "count": len(pictures), "_untrusted": ["pictures"]}


def _file_safe(value: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", value)[:60]
