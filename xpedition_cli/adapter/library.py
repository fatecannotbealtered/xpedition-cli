"""Handlers of the central-library commands: ``part list``, ``part get`` and ``part create``.

The databases of a central library (``PartsDBLibs/*.pdb``, ``CellDBLibs/*.cel``,
``Layout/PadstackDB.psk``) are binary. Xpedition's converters in ``SDD_HOME/common/win64/bin``
export them as HKP text (``PartsDB2HKP``, ``CellDB2HKP``, ``PadstackDB2HKP``, always with
``-u mm -a``) and import HKP text back (``HKP2PadstackDB``, ``HKP2CellDB``, ``HKP2PartsDB``).
Symbols need no converter: Designer keeps each one as a text file,
``SymbolLibs/<partition>/sym/<name>.<version>``, and takes the highest version. The rules the
converters follow are in the knowledge entry ``library-converter-rules``.

Exports are kept in a cache under the adapter store's folder; a database whose time and size
are those of its last export is not exported again. ``load_library`` is the helper the
schematic code uses to read the library the same way.

All three commands work on files only: the runtime refuses ``part create`` while a Designer
session has the project open.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from . import base, jobs
from .base import Context, Handler, change, facts, key, need_input, preview, target
from .plan import PlanError
from .plan import library_parts as P
from .plan import library_read as R
from .plan import project_file
from .platform import env

KINDS = ("parts", "cells", "padstacks")
EXPORT_TOOLS = {"parts": "PartsDB2HKP", "cells": "CellDB2HKP", "padstacks": "PadstackDB2HKP"}
EXPORT_WORKERS = 6  # converters run side by side
EXPORT_TIMEOUT_S = 120.0  # one export, when no call limit applies (a long job's worker)
READ_BUDGET_S = 24.0  # all exports of one read, preview or read-back call, which ends at 30 s
MIN_TOOL_S = 2.0  # an export is not started with less time than this left
IMPORT_TIMEOUT_S = 300.0  # past the apply limit on purpose: an importer ended early can leave a database half written
CACHE_FOLDER = "library-cache"
WORK_FOLDER = ("Work", "xpedition-cli")  # inside the library: the HKP texts and logs of an import
# How a central library (.lmc) names its partitions: ASCII, each NUL-terminated after a 4-byte
# length, such as "PartsDBLibs\PartQuest.pdb" (read from the stock TemplateLibrary.lmc of 2604).
PARTITION_ENTRIES = {"parts": "PartsDBLibs\\{}.pdb", "cells": "CellDBLibs\\{}.cel", "symbols": "SymbolLibs\\{}"}
_VARIABLE = re.compile(r"\$\{(\w+)\}|%(\w+)%")


# ------------------------------------------------------------------ errors


def plan_error(exc: PlanError, dispatch_state: str = "not_started") -> AdapterError:
    """A planner's refusal as the adapter's error; the codes the adapter may not use become E_VALIDATION."""
    code = exc.code if exc.code in ("E_VALIDATION", "E_NOT_FOUND", "E_UNSUPPORTED", "E_NATIVE_FAILURE") else "E_VALIDATION"
    return AdapterError(code, exc.message, dispatch_state=dispatch_state)


# ------------------------------------------------------------------ the project and its library


def project_path(ctx: Context) -> Path:
    """The document of a file-mode command: an existing project file."""
    path = Path(ctx.document_path())
    if path.suffix.lower() != ".prj":
        raise AdapterError("E_VALIDATION", f"{ctx.command} takes a project file (.prj) with --doc, not {path}.",
                           dispatch_state="not_started")
    if not path.is_file():
        raise AdapterError("E_NOT_FOUND", f"There is no project file {path}.", dispatch_state="not_started")
    return path


def _expand(value: str) -> str:
    """``${SDD_HOME}`` and ``%NAME%`` in a path the project names. A project made from the stock
    template names the installation's library through ``${SDD_HOME}``."""

    def one(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        found = os.environ.get(name)
        if found is None and name.upper() == "SDD_HOME":
            home = env.find_sdd_home()
            found = str(home) if home else None
        return found if found is not None else match.group(0)

    return _VARIABLE.sub(one, value)


def central_library(project: Path) -> Path | None:
    """The ``.lmc`` the project names (``KEY CentralLibrary``), on this machine; None when it names none."""
    try:
        text = project.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("KEY CentralLibrary "):
            value = line[len("KEY CentralLibrary "):].strip().strip('"')
            return project_file.local_path(project.parent, _expand(value)) if value else None
    return None


def library_root(project: Path) -> tuple[Path, Path]:
    """The project's ``.lmc`` and the library folder around it."""
    lmc = central_library(project)
    if lmc is None:
        raise AdapterError("E_NOT_FOUND", f"The project {project} names no central library (KEY CentralLibrary).",
                           dispatch_state="not_started")
    if not lmc.is_file():
        raise AdapterError("E_NOT_FOUND", f"The central library {lmc} that the project names does not exist.",
                           dispatch_state="not_started")
    return lmc, lmc.parent


def unregistered_partitions(lmc: Path, required: dict[str, list[str]]) -> list[str]:
    """The partitions ``required`` names (``{"parts": [...], "cells": [...], "symbols": [...]}``) that
    the ``.lmc`` does not register. Only Library Manager's Partition Editor registers a partition;
    a database file or a project list entry alone is not a registration."""
    data = lmc.read_bytes().lower()
    missing: list[str] = []
    for kind, names in sorted(required.items()):
        for name in names:
            entry = PARTITION_ENTRIES[kind].format(name).encode("ascii", "replace").lower() + b"\x00"
            start, found = 0, False
            while (at := data.find(entry, start)) >= 0:
                if at == 0 or not 0x20 <= data[at - 1] <= 0x7E:  # the whole name, not the end of a longer one
                    found = True
                    break
                start = at + 1
            if not found:
                missing.append(f"{kind[:-1]} partition {name}")
    return missing


def refuse_unregistered(lmc: Path, required: dict[str, list[str]]) -> None:
    missing = unregistered_partitions(lmc, required)
    if missing:
        raise AdapterError(
            "E_HUMAN_REQUIRED",
            f"The central library {lmc} has not registered: {', '.join(missing)}. Ask the person to add each in "
            "Library Manager (Edit > Partition Editor, on the tab of its kind), apply and close Library Manager; then run "
            "the command again. Nothing was written.",
            dispatch_state="not_started", action="other", knowledge=["library-converter-rules"])


# ------------------------------------------------------------------ the project file's library lists


class ProjectListError(ValueError):
    """The project file has no place for a library list entry."""


def _read_prj(project: Path) -> str:
    return project.read_text(encoding="utf-8", errors="surrogateescape")


def _write_prj(project: Path, text: str) -> None:
    project.write_text(text, encoding="utf-8", errors="surrogateescape")


def ensure_prj_list(project: Path, list_name: str, entry: str) -> bool:
    """``entry`` in the project's ``LIST <list_name>``; True when it had to be added. A project
    without that list gets it in the board design's section, where Designer keeps it."""
    text = _read_prj(project)
    if not re.search(rf"^LIST {re.escape(list_name)}[ \t]*\r?$", text, re.M):
        board = project_file.board_design(project_file.designs(text))
        section = re.search(rf"^SECTION {re.escape(board.name)}[ \t]*\r?\n(.*?)^ENDSECTION", text, re.M | re.S) if board else None
        if section is None:
            raise ProjectListError(f"the project {project} has no board design section for LIST {list_name}")
        end = project_file.newline(text)
        text = text[: section.end(1)] + f"LIST {list_name}{end}ENDLIST{end}" + text[section.end(1):]
    text, added = project_file.add_entry(text, list_name, entry)
    if added:
        _write_prj(project, text)
    return added


def _add_cell_entries(project: Path, entries: list[str]) -> list[str]:
    if not entries:
        return []
    text = _read_prj(project)
    try:
        text = project_file.ensure_list(text, project_file.CELL_LIST, "PDBs")
    except ValueError as exc:
        raise ProjectListError(f"the project {project} cannot list cell partitions: {exc}") from None
    added: list[str] = []
    for entry in entries:
        text, done = project_file.add_entry(text, project_file.CELL_LIST, entry)
        if done:
            added.append(entry)
    if added:
        _write_prj(project, text)
    return added


def sync_prj_cells(project: Path, root: Path) -> list[str]:
    """A ``LIST 2dCellLibraries`` entry for every parts partition of ``LIST PDBs`` whose cell
    database exists; the entries added. Without them Layout's Database Load finds no cell
    during forward annotation (knowledge ``prj-cell-library-list``)."""
    available = [item.stem for item in (root / "CellDBLibs").glob("*.cel")]
    wanted = project_file.cell_entries_for(project_file.list_entries(_read_prj(project), "PDBs"), available)
    return _add_cell_entries(project, wanted)


def register_cell_partitions(project: Path, root: Path, partitions: list[str]) -> list[str]:
    """``LIST 2dCellLibraries`` entries for cell partitions the new parts use from other partitions."""
    present = [p for p in partitions if (root / "CellDBLibs" / f"{p}.cel").is_file()]
    return _add_cell_entries(project, [f"CellDBLibs\\{p}.cel" for p in present])


# ------------------------------------------------------------------ exports and the cache


@dataclass
class _Source:
    kind: str
    partition: str
    path: Path


def _sources(root: Path, kinds: tuple[str, ...], partitions: list[str] | None) -> list[_Source]:
    found: list[_Source] = []
    if "parts" in kinds:
        found += [_Source("parts", f.stem, f) for f in sorted((root / "PartsDBLibs").glob("*.pdb"))]
    if "cells" in kinds:
        found += [_Source("cells", f.stem, f) for f in sorted((root / "CellDBLibs").glob("*.cel"))]
    if partitions is not None:
        chosen = {p.casefold() for p in partitions}
        found = [s for s in found if s.partition.casefold() in chosen]
    padstacks = root / "Layout" / "PadstackDB.psk"
    if "padstacks" in kinds and padstacks.is_file():
        found.append(_Source("padstacks", "", padstacks))
    return found


def _output(folder: Path, source: _Source) -> Path:
    # two partitions whose names differ only in characters a file name drops keep two files
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.partition) or source.kind
    tag = hashlib.sha1(source.partition.encode("utf-8")).hexdigest()[:8]
    return folder / f"{source.kind}-{stem}-{tag}.hkp"


def describe_run(run: env.ToolRun, log_errors: list[str] | None = None) -> str:
    """What went wrong in a converter run. A converter reports a wrong argument or a refused
    record in a message box, not by its exit code, so a dialog counts as a failure."""
    reasons = []
    if run.exit_code != 0:
        reasons.append(f"exit code {run.exit_code}")
    if run.dialogs:
        shown = "; ".join(f"{d.get('title', '')}: {d.get('text', '')}" for d in run.dialogs[:3])
        reasons.append(f"it showed {shown}")
    if log_errors:
        reasons.append(f"its log says {'; '.join(log_errors[:5])}")
    return ", ".join(reasons) or "it wrote no output"


def cache_root() -> Path:
    """Where the library exports are kept between calls: this user's cache folder, outside the
    state directory, which only the runtime writes (DSEC-031). Losing it costs one export."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "xpedition-cli" / CACHE_FOLDER


def cache_for(ctx: Context) -> Path:
    return cache_root()


def default_cache() -> Path:
    """The cache ``load_library`` uses: the same folder as ``cache_for``."""
    return cache_root()


def _budget() -> float | None:
    """The time all exports of one call may take: none in a long job's worker, else the read limit."""
    return None if jobs.in_worker() else READ_BUDGET_S


def export(lmc: Path, root: Path, cache: Path, *, kinds: tuple[str, ...] = KINDS, partitions: list[str] | None = None,
           fresh: bool = False, budget_s: float | None = READ_BUDGET_S) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Export the library's databases as HKP text into the cache: ``(files, failed)``.

    Each file is ``{kind, partition, path, cached}``; each failure ``{kind, partition, error}``.
    Nothing in the library is written. Up to six converters run at once. With ``budget_s``,
    an export is not started once the time is spent; what was exported is kept, so the same
    call made again continues from there (E_TIMEOUT, retryable)."""
    sources = _sources(root, kinds, partitions)
    if not sources:
        return [], []
    env.configure_environment()
    for kind in sorted({s.kind for s in sources}):
        env.launcher(EXPORT_TOOLS[kind])  # a missing converter is reported once, before any run
    folder = cache / hashlib.sha1(os.path.normcase(str(lmc)).encode("utf-8")).hexdigest()[:16]
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AdapterError("E_CONFIG", f"The library cache {folder} cannot be created: {exc.strerror}.",
                           dispatch_state="not_started") from None
    index_path = folder / "index.json"
    index = jobs.read(index_path) or {}
    deadline = time.monotonic() + budget_s if budget_s else None

    def one(source: _Source) -> dict[str, Any]:
        output = _output(folder, source)
        record: dict[str, Any] = {"kind": source.kind, "partition": source.partition, "path": str(output)}
        try:
            info = source.path.stat()
        except OSError as exc:
            return {**record, "error": f"{source.path.name} cannot be read: {exc.strerror}"}
        stamp = [int(info.st_mtime_ns), int(info.st_size)]
        if not fresh and index.get(str(source.path)) == stamp and output.is_file():
            return {**record, "stamp": stamp, "cached": True}
        timeout = EXPORT_TIMEOUT_S
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout < MIN_TOOL_S:
                return {**record, "late": True, "error": "not exported within the time of one call"}
        log = output.with_suffix(".log") if source.kind != "parts" else None  # as 1.0.3 ran it: PartsDB2HKP without a log
        arguments = ["-i", str(source.path), "-o", str(output), "-u", "mm", "-a"] + (["-l", str(log)] if log else [])
        try:
            output.unlink(missing_ok=True)  # a run that fails must not leave an older export behind
            if log is not None:
                log.unlink(missing_ok=True)
            run = env.run_tool(EXPORT_TOOLS[source.kind], arguments, timeout_s=timeout, log=log)
        except (AdapterError, OSError) as exc:
            return {**record, "error": str(exc)}
        if run.blocked_by_license:
            title = next((d.get("title", "") for d in run.dialogs if not d.get("dismissed")), "")
            return {**record, "licence": True, "title": title, "error": "a licence window waits for the person"}
        if run.timed_out:
            return {**record, "late": deadline is not None, "error": f"{EXPORT_TOOLS[source.kind]} did not finish in time"}
        if run.exit_code != 0 or run.dialogs or not output.is_file():
            why = describe_run(run, env.tool_log_errors(run.log_text or ""))
            return {**record, "error": f"{EXPORT_TOOLS[source.kind]} failed: {why}"}
        return {**record, "stamp": stamp, "cached": False}

    with ThreadPoolExecutor(EXPORT_WORKERS) as pool:
        results = list(pool.map(one, sources))
    files: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for source, result in zip(sources, results, strict=True):
        if "error" in result:
            index.pop(str(source.path), None)
            failed.append(result)
        else:
            index[str(source.path)] = result.pop("stamp")
            files.append(result)
    try:
        jobs.write(index_path, index)
    except OSError:
        pass  # the cache only saves time
    licence = [r for r in failed if r.get("licence")]
    if licence:
        raise AdapterError("E_HUMAN_REQUIRED", "A licence window of the library converters waits for the person; nothing was changed.",
                           dispatch_state="not_started", action="activate_license", title=licence[0].get("title") or "",
                           knowledge=["xpedition-license-sign-in"])
    late = [r for r in failed if r.get("late")]
    if late:
        raise AdapterError(
            "E_TIMEOUT",
            f"The central library has {len(sources)} databases to export; {len(files)} are exported and kept, "
            f"{len(late)} did not fit in the time of one call. Run the same command again: it continues from the kept exports.",
            dispatch_state="not_started")
    return files, [{"kind": r["kind"], "partition": r["partition"], "error": r["error"]} for r in failed]


def read_library(project: Path, cache: Path, *, kinds: tuple[str, ...] = KINDS, partitions: list[str] | None = None,
                 symbols: bool = True, fresh: bool = False) -> R.Library:
    """The project's central library as ``library_read`` records: the exports of ``kinds`` and,
    with ``symbols``, the current version of every symbol file. ``failed`` lists the databases
    the converters could not export; ``texts`` keeps each export's text by (kind, partition)."""
    lmc, root = library_root(project)
    files, failed = export(lmc, root, cache, kinds=kinds, partitions=partitions, fresh=fresh, budget_s=_budget())
    library = R.Library(root=str(root))
    for item in files:
        try:
            content = R.decode_text(Path(item["path"]).read_bytes())
        except OSError as exc:
            failed.append({"kind": item["kind"], "partition": item["partition"], "error": f"the export cannot be read: {exc.strerror}"})
            continue
        library.texts[(item["kind"], item["partition"])] = content
        if item["kind"] == "parts":
            library.parts += R.parse_parts(content, item["partition"])
        elif item["kind"] == "cells":
            library.cells += R.parse_cells(content, item["partition"])
        else:
            library.padstacks = R.parse_padstacks(content)
    library.failed += failed
    if symbols:
        for partition, name, path in R.symbol_files(root / "SymbolLibs", partitions):
            try:
                content = R.decode_text(path.read_bytes())
            except OSError:
                continue
            symbol = R.parse_symbol(content, partition, name)
            symbol["version"] = int(path.suffix[1:])
            symbol["path"] = str(path)
            library.symbols.append(symbol)
    return library


def load_library(prj_path: str, *, fresh: bool = False) -> R.Library:
    """The central library of the project ``prj_path``: parts, cells, padstacks and symbols.

    The exports are kept in the adapter store's cache; ``fresh`` exports every database again.
    Raises ``AdapterError``: E_NOT_FOUND when the project names no existing central library,
    E_HUMAN_REQUIRED when a licence window holds the converters, E_TIMEOUT when, outside a long
    job's worker, the exports did not fit in one call (the call made again continues). A
    database the converters could not export is listed in ``failed``; check it before relying
    on what is absent."""
    return read_library(Path(prj_path).resolve(), default_cache(), fresh=fresh)


def refuse_failed(library: R.Library, what: str) -> None:
    if library.failed:
        names = ", ".join(f"{f['kind']} {f['partition'] or 'padstacks'}" for f in library.failed[:8])
        raise AdapterError("E_NATIVE_FAILURE", f"The converters could not export {names} of the central library, so {what}.",
                           dispatch_state="not_started", native_message="; ".join(f["error"] for f in library.failed[:8]),
                           knowledge=["library-converter-rules"])


# ------------------------------------------------------------------ part list, part get


class PartList(Handler):
    command = "part list"

    def read(self, ctx: Context) -> dict[str, Any]:
        library = read_library(project_path(ctx), cache_for(ctx), kinds=("parts",), symbols=False)
        refuse_failed(library, "the list would be incomplete")
        rows = []
        for part in sorted(library.parts, key=lambda p: (p["partition"], p["number"])):
            row = R.part_row(part)
            rows.append({"id": row.pop("number"), **row})
        return {"items": rows}


def _finding(index: int, found: dict[str, Any]) -> dict[str, Any]:
    objects = [found["item"]]
    if found.get("cell"):
        objects.append(f"cell {found['cell']}")
    if found.get("symbol"):
        objects.append(f"symbol {found['symbol']}")
    if found.get("pin"):
        objects.append(f"pin {found['pin']}")
    objects += [f"pin {p}" for p in (found.get("pins") or [])[:10]]
    return {"id": f"{found['rule']}-{index}", "severity": found["severity"], "rule": found["rule"], "message": found["message"],
            "objects": objects}


SHEET_UNIT_MM = 0.254  # a symbol's coordinates are in sheet units of 10 mil
MM_KEYS = {"x": "x_mm", "y": "y_mm", "width": "width_mm", "height": "height_mm", "radius": "radius_mm",
           "diameter": "diameter_mm", "points": "points_mm", "rotation": "rotation_deg"}


def with_units(value: Any) -> Any:
    """A cell or padstack in millimetres, with the unit in every key of a quantity (DCLI-081)."""
    if isinstance(value, dict):
        return {MM_KEYS.get(k, k): with_units(v) for k, v in value.items()}
    if isinstance(value, list):
        return [with_units(v) for v in value]
    return value


def _sheet_mm(value: float) -> float:
    return round(float(value) * SHEET_UNIT_MM, 4)


def _symbol_view(symbol: dict[str, Any]) -> dict[str, Any]:
    """A symbol as part get shows it: its pins and box in mm from the symbol origin, without the
    drawing and the file path."""
    out = {k: v for k, v in symbol.items() if k not in ("lines", "attributes", "path", "pins", "bbox")}
    out["reference"] = f"{symbol['partition']}:{symbol['name']}"
    out["pins"] = [{"number": p.get("number", ""), "name": p.get("name", ""), "type": p.get("type", ""),
                    "x_mm": _sheet_mm(p["x"]), "y_mm": _sheet_mm(p["y"]),
                    "body_x_mm": _sheet_mm(p["bx"]), "body_y_mm": _sheet_mm(p["by"]), "side": p.get("side", "")}
                   for p in symbol.get("pins", [])]
    out["bbox_mm"] = [_sheet_mm(v) for v in symbol.get("bbox", [])]
    return out


def _cell_view(cell: dict[str, Any]) -> dict[str, Any]:
    """A cell as part get shows it: its pins, holes and placement outline in mm from the cell
    origin, without the silkscreen, assembly drawing and texts."""
    return with_units({k: v for k, v in cell.items() if k not in ("silkscreen", "assembly", "texts")})


class PartGet(Handler):
    command = "part get"

    def read(self, ctx: Context) -> dict[str, Any]:
        number = str(need_input(ctx, "id"))
        library = read_library(project_path(ctx), cache_for(ctx))
        found = library.find_parts(number)
        if not found:
            refuse_failed(library, f"part {number} may be in a database that was not read")
            raise AdapterError("E_NOT_FOUND", f"The central library has no part {number!r}; part list lists the part numbers.")
        part = found[0]
        cell = library.find_cell(part["cell"]) if part["cell"] else None
        if (part["cell"] and cell is None) or any(f["kind"] == "padstacks" for f in library.failed):
            refuse_failed(library, f"the cell or the padstacks of part {number} may be in a database that was not read")
        try:
            symbols = [library.find_symbol(reference) for reference in part["symbols"]]
            findings = R.check_part(library, part)
        except PlanError as exc:
            raise plan_error(exc) from None
        stacks = sorted({pin["padstack"] for pin in (cell["pins"] + cell["holes"] if cell else [])})
        return {
            "id": part["number"],
            "partition": part["partition"],
            "description": part["description"],
            "prefix": part["prefix"],
            "value": part["properties"].get("Value", ""),
            "properties": dict(part["properties"]),
            "pins": [{"symbol": p["symbol"], "name": p["name"], "number": p["number"]} for p in part["pins"]],
            "symbols": [_symbol_view(s) for s in symbols if s is not None],
            "cell": _cell_view(cell) if cell else {},
            "padstacks": {name: with_units(R.padstack_geometry(library.padstacks, name)) for name in stacks},
            "also_in": [p["partition"] for p in found[1:]],
            "findings": [_finding(i, f) for i, f in enumerate(findings, start=1)],
        }


# ------------------------------------------------------------------ part create


def content_digest(part: dict[str, Any] | None) -> str | None:
    """What a part says, as the library reads it, as one SHA-256: ``library_read.part_content``
    (Value by the number it stands for, without the partition, lists in one order)."""
    content = R.part_content(part)
    if content is None:
        return None
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def hkp_bytes(text: str) -> bytes:
    """An HKP text as the converters read it: the system code page. Written as ASCII with
    replacements, a Chinese description reached the library as question marks and nothing said
    so; a character the code page cannot hold is refused instead."""
    try:
        return text.encode("ascii")
    except UnicodeEncodeError:
        pass
    try:
        return text.encode("mbcs")
    except LookupError:  # no code page off Windows
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        bad = exc.object[exc.start: exc.end]
        raise AdapterError("E_VALIDATION", f"The parts file holds characters the system code page cannot write ({bad!r}); "
                           "the library converters read only that code page. Nothing was written.",
                           dispatch_state="not_started", knowledge=["library-converter-rules"]) from None


def next_symbol_version(folder: Path, name: str) -> int:
    """One past the highest version of symbol ``name`` in a ``sym`` folder, so the file written is
    the one Designer takes; 1 when there is none."""
    highest = 0
    if folder.is_dir():
        for path in folder.iterdir():
            stem, _, version = path.name.rpartition(".")
            if stem == name and version.isdigit():
                highest = max(highest, int(version))
    return highest + 1


def _refuse_installation_library(root: Path) -> None:
    home = env.find_sdd_home()
    if home is not None and root.resolve().is_relative_to(home.resolve()):
        raise AdapterError("E_VALIDATION", f"The project's central library {root} is the installation's own library; part create "
                           "does not write into the installation. Use a project with its own copy of the library.",
                           dispatch_state="not_started")


def _refuse_replacements(plan: P.AddPlan) -> None:
    """This release only adds: whatever the library holds under the same name with other content is refused."""
    if not plan.replaces:
        return
    parts = sorted(r[5:] for r in plan.replaces if r.startswith("part "))
    others = sorted(r for r in plan.replaces if not r.startswith("part "))
    reasons = []
    if parts:
        reasons.append(f"the library holds part number(s) {', '.join(parts)} with other content; give a changed part a new part "
                       "number, or leave it out")
    if others:
        reasons.append(f"the library holds {', '.join(others)} with other content; give the footprint or pad another name")
    raise base.blocked(f"part create only adds to the library: {'; '.join(reasons)}. Nothing was written.",
                       knowledge=["library-converter-rules"])


@dataclass
class _Create:
    """Everything one part create would write, worked out from the parts file and the library."""

    project: Path
    lmc: Path
    root: Path
    plan: P.AddPlan
    texts: dict[str, str]
    new: list[str]
    skipped: list[str]
    digests: dict[str, str | None] = field(default_factory=dict)
    encoded: dict[str, bytes] = field(default_factory=dict)

    @property
    def symbol_folder(self) -> Path:
        return self.root / "SymbolLibs" / self.plan.partition / "sym"

    def added(self, kind: str) -> list[str]:
        return sorted(a["name"] for a in self.plan.actions.get(kind, []) if a["action"] == "add")


def _parts_file(ctx: Context) -> Path:
    path = Path(str(need_input(ctx, "file")))
    if not path.is_file():
        raise AdapterError("E_NOT_FOUND", f"There is no parts file {path}.", dispatch_state="not_started")
    return path


def _work_out(ctx: Context) -> _Create:
    project = project_path(ctx)
    try:
        spec = P.load(_parts_file(ctx))
    except PlanError as exc:
        raise plan_error(exc) from None
    lmc, root = library_root(project)
    _refuse_installation_library(root)
    library = read_library(project, cache_for(ctx))
    refuse_failed(library, "the parts cannot be compared with what it holds")
    try:
        plan = P.plan(spec, library)
    except PlanError as exc:
        raise plan_error(exc) from None
    _refuse_replacements(plan)
    texts = plan.texts()
    new = sorted(n for n in plan.library.parts if n not in plan.kept)
    work = _Create(project, lmc, root, plan, texts, new, sorted(plan.kept))
    if not new:
        return work
    ours = {p["number"]: p for p in R.parse_parts(texts["parts"], plan.partition)}
    work.digests = {n: content_digest(ours.get(n)) for n in new}
    refuse_unregistered(lmc, plan.required_partitions)
    if not str(work.symbol_folder).isascii():
        raise AdapterError("E_VALIDATION", f"Designer loads no new symbol from a folder whose path is not ASCII ({work.symbol_folder}); "
                           "keep the project and its library on an ASCII path. Nothing was written.",
                           dispatch_state="not_started", knowledge=["xpedition-ascii-paths-only"])
    work.encoded = {kind: hkp_bytes(text) for kind, text in texts.items() if text}
    return work


def _result(work: _Create, symbols: list[str]) -> dict[str, Any]:
    return {"partition": work.plan.partition, "created": list(work.new), "skipped": list(work.skipped), "symbols": symbols,
            "cells": work.added("cells") if work.new else [], "padstacks": work.added("padstacks") if work.new else []}


class _StepFailed(Exception):
    pass


class _Import:
    """One part create's writes, in the order the converters need: padstacks, symbols, cells, parts,
    then the project's lists. ``wrote`` says whether anything reached the library yet."""

    def __init__(self, work: _Create) -> None:
        self.work = work
        self.folder = work.root.joinpath(*WORK_FOLDER)
        self.wrote = False
        self.symbols: list[str] = []
        self.warnings: list[str] = []

    def convert(self, step: str, tool: str, arguments: list[str]) -> None:
        source = self.folder / f"{step}.hkp"
        log = self.folder / f"{step}.log"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            source.write_bytes(self.work.encoded[step])
            log.unlink(missing_ok=True)
        except OSError as exc:
            raise _StepFailed(f"the {step} text cannot be written to {source}: {exc.strerror}") from None
        run = env.run_tool(tool, ["-i", str(source), *arguments, "-l", str(log)], timeout_s=IMPORT_TIMEOUT_S, log=log)
        state = "dispatched"
        if run.blocked_by_license:
            title = next((d.get("title", "") for d in run.dialogs if not d.get("dismissed")), "")
            raise AdapterError("E_HUMAN_REQUIRED", f"A licence window holds {tool}; the library may be partly written.",
                               dispatch_state=state, action="activate_license", title=title, knowledge=["xpedition-license-sign-in"])
        if run.timed_out:
            raise AdapterError("E_TIMEOUT", f"{tool} did not finish within {int(IMPORT_TIMEOUT_S)} s and was ended; the library may be "
                               "partly written.", dispatch_state=state)
        text = run.log_text or ""
        self.warnings += [f"{tool}: {line}" for line in env.tool_log_warnings(text)[:10]]
        errors = env.tool_log_errors(text)
        if run.exit_code != 0 or run.dialogs or errors:
            raise _StepFailed(f"{tool} failed: {describe_run(run, errors)}")
        self.wrote = True

    def write_symbols(self) -> None:
        folder = self.work.symbol_folder
        try:
            folder.mkdir(parents=True, exist_ok=True)
            plain = folder.resolve()
            for name, text in sorted(self.work.plan.symbols.items()):
                path = (folder / f"{name}.{next_symbol_version(folder, name)}").resolve()
                if path.parent != plain:
                    raise _StepFailed(f"symbol name {name!r} is not a plain file name")
                path.write_text(text, encoding="utf-8")
                self.wrote = True
                self.symbols.append(name)
        except OSError as exc:
            raise _StepFailed(f"the symbol files cannot be written to {folder}: {exc.strerror}") from None

    def run(self) -> None:
        work, plan = self.work, self.work.plan
        lmc, root, partition = str(work.lmc), work.root, plan.partition
        env.configure_environment()
        for tool in ("HKP2PadstackDB", "HKP2CellDB", "HKP2PartsDB"):
            env.launcher(tool)  # a missing converter stops the import before anything is written
        padstack_db = str(root / "Layout" / "PadstackDB.psk")
        if "padstacks" in work.encoded:
            self.convert("padstacks", "HKP2PadstackDB", ["-o", padstack_db, "-c", lmc, "-m"])
        self.write_symbols()
        if "cells" in work.encoded:
            cell_db = str(root / "CellDBLibs" / f"{partition}.cel")
            self.convert("cells", "HKP2CellDB", ["-o", cell_db, "-psk", padstack_db, "-c", lmc, "-m"])
        # never -r: without it a part number the database holds keeps its part, and every
        # number written here is new (knowledge library-converter-rules)
        self.convert("parts", "HKP2PartsDB", ["-c", lmc, "-o", str(root / "PartsDBLibs" / f"{partition}.pdb")])
        try:
            # the packager searches the parts partitions of LIST PDBs, Designer the symbol
            # partitions of LIST Symbols, Layout the cell partitions of LIST 2dCellLibraries
            ensure_prj_list(work.project, "PDBs", f"PartsDBLibs\\{partition}.pdb")
            ensure_prj_list(work.project, "Symbols", f"SymbolLibs\\{partition}")
            sync_prj_cells(work.project, root)
            register_cell_partitions(work.project, root, sorted(plan.library.cell_partitions))
        except ProjectListError as exc:
            raise _StepFailed(str(exc)) from None
        except OSError as exc:
            raise _StepFailed(f"the project file cannot be written: {exc.strerror}") from None


class PartCreate(Handler):
    """Runs as a long job (DCLI-065): the library converters may take minutes."""

    command = "part create"
    long_job = True

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        return self.apply(ctx)

    def preview(self, ctx: Context) -> dict[str, Any]:
        work = _work_out(ctx)
        prj = str(work.project)
        targets = [target(prj, "part", f"/parts/{n}", label=n) for n in work.new]
        changes = [change(key(prj, f"/parts/{n}"), "content", None, work.digests[n]) for n in work.new]
        if work.new:
            # the converters write the library's databases: the scope must grant the library too
            targets.append(target(str(work.lmc), "library", label=str(work.lmc)))
            action = (f"Add {len(work.new)} part(s) to partition {work.plan.partition} of the central library {work.lmc}: "
                      f"{', '.join(work.new[:10])}{' ...' if len(work.new) > 10 else ''}")
        else:
            action = f"Nothing to add: the central library {work.lmc} holds every part of the file as it is"
        if work.skipped:
            action += f"; skip {len(work.skipped)} identical part(s)"
        return preview(action + ".", targets, changes)

    def apply(self, ctx: Context) -> dict[str, Any]:
        work = _work_out(ctx)
        if not work.new:
            return facts("not_applicable", "unchanged", result=_result(work, []))
        job = _Import(work)
        try:
            job.run()
        except _StepFailed as exc:
            if not job.wrote:
                raise AdapterError("E_NATIVE_FAILURE", f"part create wrote nothing: {exc}.", dispatch_state="dispatched",
                                   knowledge=["library-converter-rules"]) from None
            return facts("partial", "partial", result=_result(work, job.symbols),
                         native_message=f"part create stopped part way: {exc}. " + " ".join(job.warnings[:5]))
        message = "; ".join(job.warnings[:10]) or None
        return facts("applied", "saved", result=_result(work, job.symbols), native_message=message)

    def read_back(self, ctx: Context) -> dict[str, Any]:
        project = project_path(ctx)
        try:
            partition = P.load(_parts_file(ctx))["partition"]
        except PlanError as exc:
            raise plan_error(exc) from None
        library = read_library(project, cache_for(ctx), kinds=("parts",), partitions=[partition], symbols=False, fresh=True)
        if library.failed:
            return {"observed": {}, "unreadable": ["content"]}
        prefix = key(str(project), "/parts/")
        observed: dict[str, Any] = {}
        for item in ctx.request.get("targets") or []:
            if item.startswith(prefix):
                found = [p for p in library.parts if p["number"] == item[len(prefix):] and p["partition"] == partition]
                observed[item] = {"content": content_digest(found[0]) if found else None}
        return {"observed": observed, "unreadable": []}


HANDLERS: list[Handler] = [PartList(), PartGet(), PartCreate()]

