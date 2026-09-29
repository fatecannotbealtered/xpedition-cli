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
    input_file,
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
    held, _meta = load_library(options, project, kinds=("parts",))
    parts = design_parts(held, design, "library build")
    try:
        plan, texts = library_hkp.library_texts(design, options.get("partition"), parts)
    except (schematic_layout.DesignError, ValueError) as exc:
        raise CLIError(
            "E_VALIDATION", f"design cannot be packaged: {exc}", {"design": str(design_file)}
        ) from exc
    _refuse_overwriting_real_parts(plan, held)
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
            "library_parts": sorted(parts or {}),
            "placeholders_replaced": sorted(
                number for number in plan.parts if held.find_parts(number)
            ),
            "risk": {
                "tier": "T1",
                "blast_radius": (
                    "the project's central library gains padstacks, a cell partition and a "
                    "parts partition; Designer's project is closed and reopened meanwhile"
                ),
            },
        }
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
        # the parts are in the library either way; the build asked for a packaged design
        result["ok"] = bool(result["package"].get("packaged"))
    result["summary"] = summary
    result["_untrusted"] = [*(result.get("_untrusted") or []), "summary", "package"]
    return result


def _library_path(options: dict[str, Any], key: str, command: str) -> Path:
    """A central library named on the command line: its `.lmc`, or for `--from` also a
    `.prj` whose central library it is."""
    from .. import project_file as prj

    raw = text(options, key)
    path = Path(raw).expanduser()
    if not path.is_file():
        raise CLIError(
            "E_NOT_FOUND",
            f"{command}: --{key} was not found",
            {"path": str(path), "_untrusted": ["path"]},
        )
    suffix = path.suffix.lower()
    if suffix == ".prj" and key == "from":
        lmc = prj.central_library(path)
        if lmc is None:
            raise CLIError(
                "E_VALIDATION",
                f"{command}: the project names no central library",
                {"path": str(path), "_untrusted": ["path"]},
            )
    elif suffix == ".lmc":
        lmc = path
    else:
        allowed = "a central library (.lmc) or a project (.prj)" if key == "from" else "a .lmc"
        raise CLIError(
            "E_VALIDATION",
            f"{command}: --{key} is {allowed}",
            {"path": str(path), "_untrusted": ["path"]},
        )
    lmc = lmc.resolve()
    if not lmc.is_file():
        raise CLIError(
            "E_NOT_FOUND",
            f"{command}: the central library was not found",
            {"path": str(lmc), "_untrusted": ["path"]},
        )
    return lmc


def library_target(
    options: dict[str, Any], command: str, required: bool = True
) -> tuple[Path | None, Path | None]:
    """`(project, lmc)`: the library a reading command reads -- a project's central
    library (--project) or one named by its `.lmc` (--library), never both."""
    if text(options, "project") and text(options, "library"):
        raise CLIError("E_USAGE", f"{command} reads --project or --library, not both")
    if text(options, "library"):
        return None, _library_path(options, "library", command)
    if text(options, "project"):
        return project_file(options, command), None
    if required:
        raise CLIError(
            "E_USAGE",
            f"{command} reads a central library: --project X.prj, or --library LIB.lmc for "
            "one outside a project",
        )
    return None, None


def import_parts(options: dict[str, Any]) -> dict[str, Any]:
    """Parts from another Xpedition central library, with the symbols, cells, padstacks,
    pads and holes they use, into the project's central library.

    The source is only read. The dry run meets every item with what the library holds:
    add, keep (identical, left alone) or replace; a replacement needs --dangerous. The
    token is bound to everything the confirm would send.
    """
    from .. import library_import as I
    from .. import library_read as R

    check_gate(options, "library import")
    project = project_file(options, "library import")
    source_lmc = _library_path(options, "from", "library import")
    try:
        wanted = I.numbers(comma_list(options, "parts"))
    except I.LibraryImportError as exc:
        raise CLIError(exc.code, str(exc), exc.details) from exc
    target, meta = load_library(options, project)
    own = meta.get("library")
    if own and Path(str(own)).resolve() == source_lmc:
        raise CLIError(
            "E_VALIDATION",
            "library import: --from is the project's own central library",
            {"library": str(source_lmc), "_untrusted": ["library"]},
        )
    source, source_meta = load_library(options, None, lmc=source_lmc, keep_texts=True)
    try:
        plan = I.plan(source, target, wanted)
    except I.LibraryImportError as exc:
        details = {**exc.details, "source": str(source_lmc)}
        if source_meta["failed"]:
            details["source_unreadable"] = source_meta["failed"]
        details["_untrusted"] = [key for key in details if key != "hint"]
        raise CLIError(exc.code, f"library import: {exc}", details) from exc
    digest = hashlib.sha256(plan.material().encode("utf-8")).hexdigest()
    scope = {
        "operation": "library_import",
        "project": str(project),
        "source": str(source_lmc),
        "parts": wanted,
        "digest": digest[:16],
        "replaces": sorted(plan.replaces),
    }
    summary = {
        kind: {
            action: sum(1 for item in items if item["action"] == action)
            for action in ("add", "keep", "replace")
        }
        for kind, items in (
            ("parts", plan.parts),
            ("symbols", plan.symbols),
            ("cells", plan.cells),
            ("padstacks", plan.padstacks),
        )
    }
    listing = {
        "source": str(source_lmc),
        "library": own,
        "parts": plan.parts,
        "symbols": plan.symbols,
        "cells": plan.cells,
        "padstacks": plan.padstacks,
        "summary": summary,
    }
    if options.get("dry_run"):
        changes: list[dict[str, Any]] = []
        if plan.padstack_text:
            changes.append({"action": "merge_padstacks", "into": "Layout/PadstackDB.psk"})
        for unit in plan.units:
            if unit["symbols"]:
                changes.append(
                    {
                        "action": "write_symbols",
                        "folder": f"SymbolLibs/{unit['partition']}/sym",
                        "symbols": sorted(unit["symbols"]),
                    }
                )
            if unit["cells"]:
                changes.append(
                    {"action": "merge_cells", "file": f"CellDBLibs/{unit['partition']}.cel"}
                )
            if unit["parts"]:
                changes.append(
                    {"action": "merge_parts", "file": f"PartsDBLibs/{unit['partition']}.pdb"}
                )
        if not plan.empty():
            changes.append(
                {
                    "action": "register_in_project",
                    "lists": ["Symbols", "PDBs", "2dCellLibraries"],
                    "cell_partitions": plan.cell_partitions,
                }
            )
        preview = {
            **listing,
            "dangerous": bool(plan.replaces),
            "replaces": sorted(plan.replaces),
            "changes": changes,
            "risk": {
                "tier": "T2" if plan.replaces else "T1",
                "blast_radius": (
                    "the project's central library gains the parts and everything they use, "
                    "each in its source partition; what it holds under a name with other "
                    "content is replaced, and every part using a replaced cell or padstack "
                    "changes with it; the source library is only read"
                ),
            },
            "_untrusted": ["source", "library", "parts", "symbols", "cells", "padstacks"],
        }
        if plan.empty():
            preview["note"] = "the library holds every item identically: a confirm changes nothing"
        return previewed(preview, scope)
    if plan.replaces:
        named = ", ".join(sorted(plan.replaces)[:5])
        require_dangerous(options, f"library import replaces what the library holds ({named})")
    backend = None if plan.empty() else native()
    confirmed(options, scope)
    if backend is None:
        result: dict[str, Any] = {"ok": True, "imported": False, "steps": [], "failed": []}
    else:
        result = backend.invoke(
            "library_import",
            {
                "project": str(project),
                "units": plan.units,
                "padstacks": plan.padstack_text,
                "cell_partitions": plan.cell_partitions,
            },
            timeout_seconds=900.0,
        )
        result["imported"] = True
    verification: dict[str, Any] = {"parts": [part["number"] for part in plan.parts]}
    if result.get("ok"):
        after, _meta = load_library(options, project)
        missing = [
            part["number"]
            for part in plan.parts
            if not any(
                row["partition"] == part["partition"] for row in after.find_parts(part["number"])
            )
        ]
        findings = [
            finding
            for part in plan.parts
            for row in after.find_parts(part["number"])[:1]
            for finding in R.check_part(after, row)
        ]
        lacking = [
            s["reference"] for s in plan.symbols if after.find_symbol(s["reference"]) is None
        ]
        lacking += [f"cell {c['name']}" for c in plan.cells if after.find_cell(c["name"]) is None]
        # met again with the source, every item must now be one the library holds as
        # the source does: a text the converters changed on the way reads otherwise
        try:
            again = I.plan(source, after, wanted)
            changed = [
                f"{kind[:-1]} {item.get('number') or item.get('reference') or item.get('name')}"
                for kind in ("parts", "symbols", "cells", "padstacks")
                for item in getattr(again, kind)
                if item["action"] != "keep"
            ]
        except I.LibraryImportError as exc:
            changed = [str(exc)]
        verification.update(
            {
                "missing": missing,
                "missing_items": lacking,
                "changed": changed,
                "findings": findings,
                "verified": not missing
                and not lacking
                and not changed
                and not any(f["severity"] == "high" for f in findings),
            }
        )
    else:
        verification["verified"] = False
    result.update(listing)
    result["verification"] = verification
    result["ok"] = bool(result.get("ok")) and verification.get("verified", False)
    result["_untrusted"] = [
        *(result.get("_untrusted") or []),
        "source",
        "parts",
        "symbols",
        "cells",
        "padstacks",
        "verification",
    ]
    return result


# -- reading the library -------------------------------------------------------------

LIBRARY_KINDS = ("parts", "cells", "symbols", "padstacks")
EXPORT_TIMEOUT = 600.0


def design_parts(library: Any, design: dict[str, Any], command: str) -> dict[str, Any] | None:
    """The central-library parts a design's symbols name, as the planner takes them:
    by number, each with its partition, symbol name, symbol file text and cell."""
    from .. import schematic_layout

    numbers = schematic_layout.library_part_numbers(design)
    if not numbers:
        return None
    found: dict[str, Any] = {}
    missing: list[str] = []
    for number in numbers:
        rows = library.find_parts(number)
        symbol = library.find_symbol(rows[0]["symbols"][0]) if rows and rows[0]["symbols"] else None
        if symbol is None or not symbol.get("path"):
            missing.append(number)
            continue
        try:
            text_ = Path(symbol["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            missing.append(number)
            continue
        found[number] = {
            "partition": symbol["partition"],
            "symbol": symbol["name"],
            "text": text_,
            "cell": rows[0]["cell"],
        }
    if missing:
        raise CLIError(
            "E_VALIDATION",
            f"{command}: the design names parts the library does not hold: {missing}",
            {
                "parts": missing,
                "hint": "library list --query <number>; library add --file parts.json",
            },
        )
    return found


def design_library(
    options: dict[str, Any], project: Path | None, design: dict[str, Any], command: str
) -> dict[str, Any] | None:
    """`design_parts` for a design that names library parts; None, and no read, for one
    that names none."""
    from .. import schematic_layout

    numbers = schematic_layout.library_part_numbers(design)
    if not numbers:
        return None
    if project is None:
        raise CLIError(
            "E_USAGE",
            f"{command}: the design names library parts {numbers[:5]}; give --project X.prj "
            "so their symbols are read from its central library",
        )
    library, _meta = load_library(options, project, kinds=("parts",))
    return design_parts(library, design, command)


def _refuse_overwriting_real_parts(plan: Any, held: Any) -> None:
    """A placeholder whose number is a part `library add` made would replace it."""
    from .. import library_parts

    real = sorted(
        number
        for number in plan.parts
        if any(not library_parts.placeholder(row) for row in held.find_parts(number))
    )
    if real:
        raise CLIError(
            "E_CONFLICT",
            f"library build would replace library parts {real[:5]} with placeholders: the "
            "design draws its own symbol for them",
            {
                "parts": real,
                "hint": 'name them in the design\'s symbols: {"LDO": {"part": "<number>"}}',
            },
        )


def cache_root() -> Path:
    from ..audit import config_dir

    return config_dir() / "cache" / "library"


def load_library(
    options: dict[str, Any],
    project: Path | None,
    partitions: list[str] | None = None,
    kinds: tuple[str, ...] = ("parts", "cells", "padstacks"),
    *,
    lmc: Path | None = None,
    keep_texts: bool = False,
) -> tuple[Any, dict[str, Any]]:
    """A central library as `library_read` records, and where it came from: the
    project's, or with `lmc` one named by its `.lmc` (another project's, a company
    library's copy).

    The adapter exports what `kinds` names through the stock converters into a cache
    (a database unchanged since its last export is not exported again); the symbols
    are read from their files. `keep_texts` keeps the exports as text too, for a reader
    that copies records.
    """
    from .. import library_read as R

    backend = reader(options)
    request: dict[str, Any] = {
        "cache": str(cache_root()),
        "kinds": [kind for kind in kinds if kind != "symbols"],
        "partitions": partitions or None,
    }
    if lmc is not None:
        request["library"] = str(lmc)
    else:
        request["project"] = str(project)
    exported = backend.invoke(
        "library_export",
        request,
        timeout_seconds=max(backend.read_timeout_seconds, EXPORT_TIMEOUT),
    )
    library = R.Library(root=str(exported.get("root") or ""))
    for item in exported.get("files") or []:
        path = Path(str(item["path"]))
        try:
            content = R.decode_text(path.read_bytes())
        except OSError as exc:
            library.failed.append(
                {"kind": item["kind"], "partition": item["partition"], "error": str(exc)}
            )
            continue
        if keep_texts:
            library.texts[(str(item["kind"]), str(item["partition"]))] = content
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
                content = R.decode_text(path.read_bytes())
            except OSError:
                continue
            symbol = R.parse_symbol(content, partition, name)
            symbol["version"] = int(path.suffix[1:])
            symbol["path"] = str(path)
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

    project, lmc = library_target(options, "library list")
    kind = text(options, "kind") or "parts"
    partitions = _partitions(options)
    wanted = (kind,) if kind != "symbols" else ("symbols",)
    library, meta = load_library(options, project, partitions, wanted, lmc=lmc)
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
        "project": str(project) if project else None,
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

    project, lmc = library_target(options, "library show")
    number = text(options, "part")
    name = text(options, "cell")
    if bool(number) == bool(name):
        raise CLIError("E_USAGE", "library show takes --part NUMBER or --cell NAME")
    library, meta = load_library(options, project, lmc=lmc)
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
            "project": str(project) if project else None,
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
        "project": str(project) if project else None,
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

    project, lmc = library_target(options, "library check")
    partitions = _partitions(options)
    library, meta = load_library(options, project, lmc=lmc)
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
        "project": str(project) if project else None,
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
        return path, P.plan(spec, existing)
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
        # the text as the library holds it is the text added: written in the wrong code
        # page, a Chinese description came back as question marks and passed
        written = R.parse_parts(texts["parts"], plan.partition) if texts["parts"] else []
        changed = sorted(
            part["number"]
            for part in written
            if found.get(part["number"])
            and R.part_content(part) != R.part_content(found[part["number"]][0])
        )
        verification.update(
            {
                "missing": missing,
                "changed": changed,
                "findings": findings,
                "verified": not missing
                and not changed
                and not any(f["severity"] == "high" for f in findings),
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
    project, lmc = library_target(options, "library render", required=bool(number))
    if project is not None or lmc is not None:
        library, _meta = load_library(options, project, lmc=lmc)
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
