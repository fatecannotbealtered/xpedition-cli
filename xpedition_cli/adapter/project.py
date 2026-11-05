"""Handlers of the file-mode project commands: ``project get``, ``project create``, ``packager run``
and ``pcb create``.

They work on files and on Xpedition's command-line programs only, never through Designer or
Layout: ``package.exe`` packages the schematic for Layout and ``JobWizard.exe -createnew`` creates
the board. Both are started through their launchers in ``SDD_HOME/common/win64/bin``
(``env.run_tool``). The runtime refuses a file-mode write while a Designer session has the
project open, so no handler here closes Designer: Designer locks ``database/icdb.dat`` and
rewrites the ``.prj`` when it closes a project (knowledge ``xpedition-basics``).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from pathlib import Path, PureWindowsPath
from typing import Any

from .._driver import AdapterError
from . import library as lib
from .base import Context, Handler, change, facts, key, need_input, preview, target
from .plan import logs, project_file
from .platform import env

# What a template's folder holds that a new project must not: the library's layout templates and
# work files, logs, backups, thumbnails and the template's packager report.
CLONE_IGNORE = ("Templates", "Work", "LogFiles", "ProjectBackup", "Thumbnail", "*.bak", "PartPkg.log")
# Designer ships these symbol partitions; they carry no parts database of their own.
STOCK_SYMBOL_PARTITIONS = frozenset({"globals", "builtin", "borders"})
PACKAGER_TIMEOUT_S = 900.0
JOBWIZARD_TIMEOUT_S = 240.0
LAYOUT_TEMPLATES = ("standard", "templates", "dxdesigner", "TemplateLibrary", "Templates", "Layout")
TEMPLATE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _().-]*$")
BOARD_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
LOG_CLOCK_SLACK_S = 2.0  # a file time can be this much older than the clock that started the run


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="surrogateescape")


def _section(text: str, name: str) -> str:
    match = re.search(rf"^SECTION {re.escape(name)}[ \t]*\r?\n(.*?)^ENDSECTION", text, re.S | re.M)
    return match.group(1) if match else ""


def _key_in(text: str, section: str, name: str) -> str:
    match = re.search(rf'^KEY {re.escape(name)}[ \t]+"([^"]*)"', _section(text, section), re.M)
    return match.group(1) if match else ""


# ------------------------------------------------------------------ project get


def _design_rows(project: Path, text: str) -> list[dict[str, Any]]:
    listed = project_file.designs(text)
    roots = {_key_in(text, d.name, "RootBlock") for d in listed if d.is_board()}
    rows = []
    for design in listed:
        board = project_file.local_path(project.parent, design.pcb_path) if design.pcb_path else None
        kind = "board" if design.is_board() else "schematic" if design.name in roots else "other"
        rows.append({"name": design.name, "kind": kind, "board": str(board) if board else "",
                     "board_exists": bool(board and board.is_file()), "layout_template": design.layout_template})
    return rows


class ProjectGet(Handler):
    command = "project get"

    def read(self, ctx: Context) -> dict[str, Any]:
        project = lib.project_path(ctx)
        text = _text(project)
        library = lib.central_library(project)
        designs = _design_rows(project, text)
        board = next((d for d in designs if d["kind"] == "board"), None)
        # the library lists live in the board design's section; without one, the first lists of the file
        body = _section(text, board["name"]) if board else text
        return {
            "path": str(project),
            "xpedition_version": _key_in(text, "DesignInfo", "DxD_Version"),
            "central_library": {"path": str(library) if library else "", "exists": bool(library and library.is_file())},
            "designs": designs,
            "board": board["board"] if board else "",
            "symbol_libraries": project_file.list_entries(body, "Symbols"),
            "parts_databases": project_file.list_entries(body, "PDBs"),
            "cell_libraries": project_file.list_entries(body, project_file.CELL_LIST),
            "ascii_path": str(project).isascii(),
        }


# ------------------------------------------------------------------ project create


def _new_project(ctx: Context) -> tuple[Path, Path]:
    """The template project and the new project's file, checked before anything is copied."""
    template = lib.project_path(ctx)
    target_path = Path(os.path.abspath(str(need_input(ctx, "output"))))
    source, folder = template.parent, target_path.parent

    def refuse(message: str, *, code: str = "E_VALIDATION", knowledge: list[str] | None = None) -> AdapterError:
        return AdapterError(code, message, dispatch_state="not_started", **({"knowledge": knowledge} if knowledge else {}))

    if target_path.suffix.lower() != ".prj":
        raise refuse(f"The new project's file must end in .prj, not {target_path.name}.")
    if not str(target_path).isascii():
        raise refuse(f"The new project's path must be ASCII ({target_path}): Designer loads no new symbol from a folder whose path "
                     "has other characters.", knowledge=["xpedition-ascii-paths-only"])
    if folder == source or source in folder.parents:
        raise refuse(f"The new project cannot live inside the template's folder {source}.")
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise refuse(f"The new project's folder {folder} exists and is not empty; name a new or empty folder.")
    state = project_file.template_state(template)
    if state["designs"] and not state["initialized"]:
        raise refuse(f"The template {template} lists designs but has no project database ({state['database']}); it cannot be "
                     "opened as a project.")
    if state["creation_method"] != "template_clone":
        raise refuse(f"The template {template} is a setup template without designs or a project database. This release copies "
                     "only a project Designer has created; make one with it and name that project with --doc.", code="E_UNSUPPORTED")
    return template, target_path


def repair_missing_parts_databases(project: Path) -> dict[str, list[str]]:
    """Give every user symbol partition of the new project a parts database, or report the ones without.

    Designer walks a design's symbol partitions when it places a part, and a partition whose
    parts database is missing raises a modal dialog that holds the drawing until a person
    answers; a template can list partitions whose ``.pdb`` was never copied. An empty parts
    database is a stock artefact, so a missing one is filled with an unused database of the
    project's own library, but only when at least two unused databases are byte-identical:
    that makes "this file is an empty database" checkable rather than guessed. Only a library
    inside the new project's folder is written; a shared one is reported and left alone.
    """
    report: dict[str, list[str]] = {"created": [], "registered": [], "missing": []}
    library = lib.central_library(project)
    if library is None or not library.is_file():
        return report
    parts_dir = library.parent / "PartsDBLibs"
    owned = project.parent.resolve() in parts_dir.resolve().parents
    text = _text(project)
    # the project file spells these with backslashes whatever the host is
    declared = {PureWindowsPath(e).name.lower() for e in project_file.list_entries(text, "PDBs")}
    wanted = [n for n in (PureWindowsPath(e).name for e in project_file.list_entries(text, "Symbols"))
              if n and n.lower() not in STOCK_SYMBOL_PARTITIONS]

    def empty_database() -> bytes | None:
        seen: dict[bytes, int] = {}
        for path in sorted(parts_dir.glob("*.pdb")) if parts_dir.is_dir() else []:
            if path.name.lower() not in declared:
                try:
                    content = path.read_bytes()
                except OSError:
                    continue
                seen[content] = seen.get(content, 0) + 1
        return next((content for content, count in seen.items() if count >= 2), None)

    donor: bytes | None = None
    changed = False
    for name in wanted:
        database = parts_dir / f"{name}.pdb"
        if not database.is_file():
            donor = donor if donor is not None else (empty_database() if owned else None)
            if donor is None:
                report["missing"].append(name)
                continue
            try:
                parts_dir.mkdir(parents=True, exist_ok=True)
                database.write_bytes(donor)
            except OSError:
                report["missing"].append(name)
                continue
            report["created"].append(str(database))
        if database.name.lower() not in declared:
            try:
                text, added = project_file.add_entry(text, "PDBs", f"PartsDBLibs\\{database.name}")
            except ValueError:  # the project has no LIST PDBs to register it in
                report["missing"].append(name)
                continue
            if added:
                declared.add(database.name.lower())
                report["registered"].append(database.name)
                changed = True
    if changed:
        project.write_text(text, encoding="utf-8", errors="surrogateescape")
    return report


def _count(folder: Path) -> tuple[int, int]:
    files = size = 0
    for path in folder.rglob("*"):
        if path.is_file():
            files += 1
            size += path.stat().st_size
    return files, size


class ProjectCreate(Handler):
    command = "project create"

    def preview(self, ctx: Context) -> dict[str, Any]:
        template, new = _new_project(ctx)
        return preview(f"Copy the project folder {template.parent} to {new.parent}, name the project {new.name} and point its "
                       "library references inside the copied folder at the copy.",
                       [target(str(new), "project", label=str(new))], [change(key(str(new)), "exists", False, True)])

    def apply(self, ctx: Context) -> dict[str, Any]:
        template, new = _new_project(ctx)
        source, folder = template.parent, new.parent
        try:
            shutil.copytree(source, folder, ignore=shutil.ignore_patterns(*CLONE_IGNORE), dirs_exist_ok=True)
            copied = folder / template.name
            if copied != new:
                copied.rename(new)
            text, rewritten = project_file.relocate_library_references(_text(new), source, folder)
            new.write_text(text, encoding="utf-8", errors="surrogateescape")
            # a partition without a parts database stops Designer's drawing on a dialog: fix it now
            databases = repair_missing_parts_databases(new)
            files, size = _count(folder)
        except (OSError, shutil.Error) as exc:
            if folder.is_dir() and any(folder.iterdir()):
                return facts("partial", "partial", native_message=f"The copy stopped part way ({exc}); {folder} holds part of it. "
                             "Remove that folder before running project create again.")
            raise AdapterError("E_NATIVE_FAILURE", f"The template folder could not be copied: {exc}.", dispatch_state="dispatched") from None
        note = None
        if databases["missing"]:
            note = (f"Symbol partitions without a parts database: {', '.join(databases['missing'])}; Designer shows a dialog when it "
                    "places a part from one of them.")
        result = {"path": str(new), "template": str(template), "files": files, "bytes": size, "rewritten": rewritten,
                  "parts_databases": {"created": databases["created"], "registered": databases["registered"]}}
        return facts("not_applicable", "saved", result=result, files=[{"path": str(new)}], native_message=note)

    def read_back(self, ctx: Context) -> dict[str, Any]:
        new = Path(os.path.abspath(str(need_input(ctx, "output"))))
        return {"observed": {key(str(new)): {"exists": new.is_file()}}, "unreadable": []}


# ------------------------------------------------------------------ packager run


def package_design(project: Path) -> str:
    """The design package.exe packages: the iCDB design that holds the schematic, the one whose
    ``ConfigType`` is ``PCB`` (``Board1`` in the stock template, which 1.0.3 always packaged)."""
    listed = project_file.designs(_text(project))
    design = project_file.board_design(listed) or (listed[0] if listed else None)
    if design is None:
        raise AdapterError("E_VALIDATION", f"The project {project} lists no design to package; draw the schematic first.",
                           dispatch_state="not_started")
    return design.name


def _refuse_unregistered_databases(project: Path) -> None:
    """The packager reads every parts database of ``LIST PDBs``; one the central library has not
    registered is refused here. 1.0.3 registered it by exporting the database and importing it
    again with ``HKP2PartsDB -r``, which rewrites it; this release leaves that to the person."""
    missing = project_file.pdb_registration_repairs(project)
    if missing:
        names = ", ".join(item["partition"] for item in missing)
        raise AdapterError(
            "E_HUMAN_REQUIRED",
            f"The project lists parts databases the central library has not registered: {names}. Ask the person to add each in "
            "Library Manager (Edit > Partition Editor, Parts tab), apply and close Library Manager; then run the command again.",
            dispatch_state="not_started", action="other", knowledge=["library-converter-rules"])


def _path_hint(project: Path) -> str:
    """A note for a failed packaging: parts databases the project names by a path that resolves
    from the library's folder only. 1.0.3 rewrote such entries to absolute paths before packaging
    (the packager runs in the project's folder); this release leaves the project file as it is."""
    found = project_file.pdb_path_repairs(project)
    if not found:
        return ""
    names = ", ".join(item["from"] for item in found[:5])
    return (f" Note: LIST PDBs names {names} by a path that resolves from the central library's folder only; when the errors "
            "are about missing parts, that may be why.")


def _packager_log(project: Path) -> Path:
    return project.parent / "Integration" / "PartPkg.log"


def _run_key(project: Path) -> str:
    return "packager-" + hashlib.sha256(os.path.normcase(str(project)).encode("utf-8")).hexdigest()[:16]


def _warnings(log: Path) -> int:
    try:
        text = env.tool_text(log.read_bytes())
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("WARNING:"))


class PackagerRun(Handler):
    command = "packager run"

    def preview(self, ctx: Context) -> dict[str, Any]:
        project = lib.project_path(ctx)
        design = package_design(project)
        _refuse_unregistered_databases(project)
        return preview(f"Package design {design} of {project} for Layout with package.exe (-Replace) and read its report "
                       "Integration/PartPkg.log.",
                       [target(str(project), "project", "/package", label=design)],
                       [change(key(str(project), "/package"), "packaged", None, True)])

    def apply(self, ctx: Context) -> dict[str, Any]:
        project = lib.project_path(ctx)
        design = package_design(project)
        _refuse_unregistered_databases(project)
        home = env.sdd_home()
        env.launcher("package")  # missing: refused before the old report is removed
        # package.exe is a Qt program: without its platform plugin folder it shows a message box
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(home / "common" / "win64" / "lib" / "plugins" / "platforms"))
        log = _packager_log(project)
        try:
            log.unlink(missing_ok=True)  # a copied project can carry its template's report; only this run's counts
        except OSError:
            pass
        ctx.store.set(_run_key(project), {"started": time.time(), "design": design})
        # -Replace refreshes the packager's copy of every part from the project's parts databases;
        # -Add, its default, keeps what an earlier run cached in Integration/LocalPartsDB.pdb
        run = env.run_tool("package", [f"-j{project}", f"-n{design}", "-Replace"], cwd=project.parent,
                           timeout_s=PACKAGER_TIMEOUT_S)
        if run.blocked_by_license:
            title = next((d.get("title", "") for d in run.dialogs if not d.get("dismissed")), "")
            raise AdapterError("E_HUMAN_REQUIRED", "A licence window holds package.exe.", dispatch_state="dispatched",
                               action="activate_license", title=title, knowledge=["xpedition-license-sign-in"])
        if run.timed_out:
            raise AdapterError("E_TIMEOUT", f"package.exe did not finish within {int(PACKAGER_TIMEOUT_S)} s and was ended.",
                               dispatch_state="dispatched")
        if not log.exists() and run.stdout:
            try:  # the packager echoes its whole report to its output too
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(run.stdout, encoding="utf-8")
            except OSError:
                pass
        errors, verdict = logs.packager_messages(log)
        result = {"packaged": run.exit_code == 0 and log.is_file() and not errors and "NOT been packaged" not in verdict,
                  "design": design, "errors": errors, "warnings": _warnings(log), "verdict": verdict, "log": str(log)}
        if result["packaged"]:
            return facts("applied", "saved", result=result)
        hint = _path_hint(project)
        if errors:
            raise AdapterError("E_VALIDATION", f"The packager did not package design {design}: {verdict or 'it reported errors'}. "
                               f"First errors: {' | '.join(errors[:5])}.{hint}", dispatch_state="dispatched",
                               native_message="\n".join(errors[:20]))
        shown = "; ".join(f"{d.get('title', '')}: {d.get('text', '')}" for d in run.dialogs[:3])
        raise AdapterError("E_NATIVE_FAILURE", f"package.exe ended with exit code {run.exit_code} and no report of errors"
                           f"{'; it showed ' + shown if shown else ''}.{hint}", dispatch_state="dispatched",
                           native_message=(run.stderr or run.stdout)[-2000:] or None)

    def read_back(self, ctx: Context) -> dict[str, Any]:
        project = lib.project_path(ctx)
        log = _packager_log(project)
        started = float((ctx.store.get(_run_key(project)) or {}).get("started") or 0.0)
        try:
            fresh = log.stat().st_mtime >= started - LOG_CLOCK_SLACK_S
        except OSError:
            fresh = False
        errors, verdict = logs.packager_messages(log)
        packaged = fresh and not errors and "NOT been packaged" not in verdict
        return {"observed": {key(str(project), "/package"): {"packaged": packaged}}, "unreadable": []}


# ------------------------------------------------------------------ pcb create


class _Board:
    """What one pcb create works with, checked before anything is written."""

    def __init__(self, ctx: Context) -> None:
        self.project = lib.project_path(ctx)
        self.template = str(ctx.inputs.get("template") or project_file.DEFAULT_LAYOUT_TEMPLATE)
        if not TEMPLATE_NAME.fullmatch(self.template):
            raise AdapterError("E_VALIDATION", f"{self.template!r} is not the name of a layout template.", dispatch_state="not_started")
        self.name = str(ctx.inputs.get("name") or self.project.stem)
        if not BOARD_NAME.fullmatch(self.name):
            raise AdapterError("E_VALIDATION", f"The board's name {self.name!r} may hold only letters, digits, - and _; give one with --name.",
                               dispatch_state="not_started")
        listed = project_file.designs(_text(self.project))
        design = project_file.board_design(listed)
        if design is None:
            raise AdapterError("E_NOT_FOUND", f"The project {self.project} has no board design (ConfigType PCB); its designs: "
                               f"{', '.join(d.name for d in listed) or 'none'}.", dispatch_state="not_started")
        self.design = design.name
        self.pcb = self.project.parent / "PCB" / f"{self.name}.pcb"
        if design.pcb_path or self.pcb.exists():
            raise AdapterError("E_VALIDATION", f"The project already has a board ({design.pcb_path or self.pcb}); this release does not "
                               "replace a board.", dispatch_state="not_started")
        self.lmc, self.root = lib.library_root(self.project)
        self.template_dir = self.root / "Templates" / "Layout" / self.template
        self.stock: Path | None = None
        if not (self.template_dir / "template.pcb").is_file():
            self.stock = self._stock_template()

    def _stock_template(self) -> Path:
        """The installation's copy of the template, for a library that lacks it (project create skips
        the library's Templates folder)."""
        home = env.find_sdd_home()
        stock = home.joinpath(*LAYOUT_TEMPLATES) if home else None
        if stock is not None and (stock / self.template / "template.pcb").is_file():
            return stock / self.template
        available = sorted(p.name for p in stock.iterdir() if p.is_dir()) if stock is not None and stock.is_dir() else []
        raise AdapterError("E_NOT_FOUND", f"The layout template {self.template!r} is neither in the central library nor in the "
                           f"installation; available: {', '.join(available) or 'none found'}.", dispatch_state="not_started")


class PcbCreate(Handler):
    command = "pcb create"

    def preview(self, ctx: Context) -> dict[str, Any]:
        board = _Board(ctx)
        targets = [target(str(board.pcb), "pcb", label=str(board.pcb)), target(str(board.project), "project", label=board.design)]
        action = (f"Create board {board.pcb} for design {board.design} from layout template {board.template!r} with JobWizard; "
                  "the project file gains the board's path, the template's name and the cell partitions of its parts partitions")
        if board.stock is not None:
            targets.append(target(str(board.lmc), "library", label=str(board.lmc)))
            action += f"; the template is first copied from the installation into {board.template_dir}"
        return preview(action + ".", targets, [change(key(str(board.pcb)), "exists", False, True)])

    def apply(self, ctx: Context) -> dict[str, Any]:
        board = _Board(ctx)
        env.configure_environment()
        env.launcher("JobWizard")  # missing: refused before anything is written
        log = board.project.parent / "LogFiles" / "jobwizard.log"
        try:
            if board.stock is not None:
                shutil.copytree(board.stock, board.template_dir, dirs_exist_ok=True)
            # JobWizard's command line creates the board of the first design listed, and reports
            # "failed to get design information" when that is a schematic
            text = _text(board.project)
            moved = project_file.move_design_first(text, board.design)
            if moved != text:
                board.project.write_text(moved, encoding="utf-8", errors="surrogateescape")
            log.parent.mkdir(parents=True, exist_ok=True)
            log.unlink(missing_ok=True)
        except (OSError, shutil.Error) as exc:
            raise AdapterError("E_NATIVE_FAILURE", f"pcb create could not prepare the project: {exc}.", dispatch_state="dispatched") from None
        run = env.run_tool("JobWizard", ["-createnew", "-f", "-prj", str(board.project), "-newpcb", str(board.pcb),
                                         "-template", board.template, "-lib", str(board.lmc), "-l", str(log)],
                           timeout_s=JOBWIZARD_TIMEOUT_S, log=log)
        if run.blocked_by_license:
            title = next((d.get("title", "") for d in run.dialogs if not d.get("dismissed")), "")
            raise AdapterError("E_HUMAN_REQUIRED", "A licence window holds JobWizard.", dispatch_state="dispatched",
                               action="activate_license", title=title, knowledge=["xpedition-license-sign-in"])
        if run.timed_out:
            raise AdapterError("E_TIMEOUT", f"JobWizard did not finish within {int(JOBWIZARD_TIMEOUT_S)} s and was ended.",
                               dispatch_state="dispatched")
        errors, copied = logs.jobwizard_messages(log)
        problems = errors + [f"{d.get('title', '')}: {d.get('text', '')}" for d in run.dialogs]
        if run.exit_code != 0:
            problems.append(f"exit code {run.exit_code}")
        if not board.pcb.is_file():
            raise AdapterError("E_NATIVE_FAILURE", f"JobWizard did not create {board.pcb}: {' | '.join(problems[:5]) or 'no reason given'}.",
                               dispatch_state="dispatched", native_message="\n".join(problems[:20]) or None)
        try:
            cells = lib.sync_prj_cells(board.project, board.root)
        except (lib.ProjectListError, OSError) as exc:
            problems.append(f"the cell partitions could not be listed in the project: {exc}")
            cells = []
        result = {"path": str(board.pcb), "template": board.template, "files": copied or 0, "cells_registered": cells}
        files = [{"path": str(board.pcb)}]
        if problems:
            return facts("partial", "partial", result=result, files=files, native_message="; ".join(problems[:10]))
        return facts("not_applicable", "saved", result=result, files=files)

    def read_back(self, ctx: Context) -> dict[str, Any]:
        project = lib.project_path(ctx)
        name = str(ctx.inputs.get("name") or project.stem)
        pcb = project.parent / "PCB" / f"{name}.pcb"
        return {"observed": {key(str(pcb)): {"exists": pcb.is_file()}}, "unreadable": []}


HANDLERS: list[Handler] = [ProjectGet(), ProjectCreate(), PackagerRun(), PcbCreate()]
