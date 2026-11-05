"""Read and edit the text of an Xpedition `.prj` project file.

A `.prj` is a flat text file of `SECTION name` … `ENDSECTION` blocks holding
`KEY name "value"` lines and `LIST name` … `ENDLIST` blocks of `VALUE "entry"`
lines. The `iCDB` section lists the project's designs; each design has a section
of its own whose `ConfigType` is `PCB` for a board design and whose
`PCBDesignPath` names the board file once one exists. Designer rewrites the file
when it closes a project, so callers edit it while the project is closed.
Everything here is pure text, so it can be tested without Xpedition.
"""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

DEFAULT_LAYOUT_TEMPLATE = "4 Layer Template"
CELL_LIST = "2dCellLibraries"


def relocate_library_references(text: str, source: Path, target: Path) -> tuple[str, list[dict]]:
    """Relocate owned absolute library references in a new clone's PRJ only.

    Relative entries, external/shared references and unrelated text stay intact.
    These are the same documented library keys/lists read elsewhere in this module.
    """
    lines, rewritten = [], []
    active_list = None
    for line in text.splitlines(keepends=True):
        start = re.match(r"^\s*LIST\s+(\S+)", line)
        if start:
            active_list = start[1]
        elif re.match(r"^\s*(ENDLIST|SECTION|ENDSECTION)\b", line):
            active_list = None
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        key = re.match(r'^([ \t]*KEY[ \t]+(CentralLibrary|DBCFile)[ \t]+)"([^"]*)"(.*)$', body)
        item = re.match(r'^([ \t]*VALUE[ \t]+)"([^"]*)"(.*)$', body)
        if key:
            prefix, field, value, tail = key.groups()
        elif item and active_list in {"Symbols", "PDBs", CELL_LIST}:
            prefix, value, tail = item.groups()
            field = f"LIST {active_list}"
        else:
            lines.append(line)
            continue
        path = Path(value)
        if path.is_absolute():
            try:
                relative = path.resolve().relative_to(source.resolve())
            except ValueError:
                pass
            else:
                value = str(target.resolve() / relative)
                line = f'{prefix}"{value}"{tail}{newline}'
                rewritten.append({"key": field, "value": value})
        lines.append(line)
    return "".join(lines), rewritten


def relative_owned_library_keys(
    text: str, target: Path, *, require_relative: bool = False
) -> tuple[str, list[dict]]:
    """Make only a new native target's two owned library keys root-independent.

    Call before the first explicit Open, after the existing complete source
    checks and copy. This does not repair an existing project or relocate any
    library/list/database bytes. Every reference must still resolve to a plain
    regular file inside this actual target; external paths cannot be disguised.
    """

    def plain(path):
        try:
            for ancestor in reversed((path, *path.parents)):
                info = ancestor.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("target_library_reparse_refused")
            return path.resolve(strict=True)
        except OSError as error:
            raise ValueError("target_library_reference_unreadable") from error

    root = Path(target)
    if not root.is_absolute() or str(root).startswith(("\\\\", "//")):
        raise ValueError("plain_new_target_directory_required")
    root = plain(root)
    if not root.is_dir():
        raise ValueError("plain_new_target_directory_required")
    lines, rewritten, seen = [], [], set()
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        match = re.match(r'^([ \t]*KEY[ \t]+(CentralLibrary|DBCFile)[ \t]+)"([^"]*)"(.*)$', body)
        if match is None:
            lines.append(line)
            continue
        prefix, field, value, tail = match.groups()
        windows = PureWindowsPath(value)
        if field in seen:
            raise ValueError("duplicate_target_library_key")
        seen.add(field)
        if (
            not value
            or value.startswith(("\\\\", "//"))
            or ".." in windows.parts
            or any(ord(char) < 32 for char in value)
            or any(":" in part for part in windows.parts if part != windows.anchor)
        ):
            raise ValueError("target_library_reference_not_self_contained")
        path = Path(value)
        if windows.anchor and not path.is_absolute():
            raise ValueError("target_library_reference_not_self_contained")
        if require_relative and path.is_absolute():
            raise ValueError("absolute_target_library_reference_after_reopen")
        if not path.is_absolute():
            path = root.joinpath(*windows.parts)
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError("target_library_reference_not_self_contained") from error
        path = plain(path)
        if not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError("target_library_file_required")
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError("target_library_reference_not_self_contained") from error
        if value != relative:
            line = f'{prefix}"{relative}"{tail}{line[len(body) :]}'
            rewritten.append({"key": field, "value": relative})
        lines.append(line)
    if "CentralLibrary" not in seen:
        raise ValueError("target_central_library_key_required")
    return "".join(lines), rewritten


@dataclass
class Design:
    name: str
    config_type: str = ""
    pcb_path: str = ""
    layout_template: str = ""

    def is_board(self) -> bool:
        return self.config_type.upper() == "PCB"


def local_path(folder: Path, value: str) -> Path:
    """A path the `.prj` names, on this machine.

    Designer writes Windows paths, relative ones to the project's folder
    (`PCB\\Board.pcb`, `..\\Lib\\Central.lmc`). A relative one is joined to `folder`
    part by part, so its backslashes separate folders on every system; a path with a
    drive (`D:\\Lib\\Central.lmc`) or a root stays as written, and on a system without
    drives it names no file (`foreign`).
    """
    windows = PureWindowsPath(value)
    if windows.anchor:
        return Path(value)
    return folder.joinpath(*windows.parts).resolve()


def central_library(project: Path) -> Path | None:
    """The `.lmc` a `.prj` names (`KEY CentralLibrary`), on this machine; None when the
    file names none or cannot be read."""
    try:
        text = project.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("KEY CentralLibrary "):
            value = line[len("KEY CentralLibrary ") :].strip().strip('"')
            return local_path(project.parent, value) if value else None
    return None


def template_state(project: Path) -> dict:
    """Distinguish a stock setup template from an initialized iCDB project."""
    text = project.read_text(encoding="utf-8", errors="surrogateescape")
    database = local_path(
        project.parent, _key(_section_body(text, "iCDB"), "iCDBDir") or "./database"
    )
    named = [design.name for design in designs(text)]
    initialized = (database / "icdb.dat").is_file()
    return {
        "database": str(database),
        "initialized": initialized,
        "designs": named,
        "creation_method": "template_clone" if initialized or named else "native_new_project",
    }


def foreign(value: str) -> bool:
    """A Windows drive or UNC path read where paths have no drives: no local file."""
    return bool(PureWindowsPath(value).drive) and not Path(value).is_absolute()


def newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _section_body(text: str, name: str) -> str:
    match = re.search(rf"^SECTION {re.escape(name)}[ \t]*\r?\n(.*?)^ENDSECTION", text, re.S | re.M)
    return match.group(1) if match else ""


def _key(body: str, key: str) -> str:
    match = re.search(rf'^KEY {re.escape(key)}[ \t]+"([^"]*)"', body, re.M)
    return match.group(1) if match else ""


def _list_match(text: str, list_name: str) -> re.Match[str] | None:
    return re.search(rf"^LIST {re.escape(list_name)}[ \t]*\r?\n(.*?)(^ENDLIST)", text, re.S | re.M)


def list_entries(text: str, list_name: str) -> list[str]:
    """The `VALUE` entries of the first `LIST list_name` in `text`."""
    match = _list_match(text, list_name)
    return re.findall(r'^VALUE[ \t]+"([^"]*)"', match.group(1), re.M) if match else []


def designs(text: str) -> list[Design]:
    """The designs the `iCDB` section lists, with what their own sections say."""
    result: list[Design] = []
    for name in list_entries(_section_body(text, "iCDB"), "Designs"):
        body = _section_body(text, name)
        result.append(
            Design(
                name,
                _key(body, "ConfigType"),
                _key(body, "PCBDesignPath"),
                _key(body, "LayoutTemplate"),
            )
        )
    return result


def board_design(items: list[Design], requested: str | None = None) -> Design | None:
    """The design named `requested`, or else the first board (`ConfigType "PCB"`) design."""
    if requested:
        return next((item for item in items if item.name == requested), None)
    return next((item for item in items if item.is_board()), None)


def pdb_path_repairs(project: Path) -> list[dict[str, str]]:
    """Packager paths resolve from the project; library partitions can live elsewhere."""
    library = central_library(project)
    if library is None:
        return []
    text = project.read_text(encoding="utf-8", errors="surrogateescape")
    repairs = []
    for entry in list_entries(text, "PDBs"):
        relative = PureWindowsPath(entry)
        if relative.is_absolute() or local_path(project.parent, entry).is_file():
            continue
        candidate = local_path(library.parent, entry)
        if candidate.is_file():
            repairs.append({"from": entry, "to": str(candidate.resolve())})
    return repairs


def pdb_registration_repairs(project: Path) -> list[dict[str, str]]:
    """Detect absent partition records; actual registration uses the native converter."""
    library = central_library(project)
    if library is None or not library.is_file():
        return []
    data = library.read_bytes()
    text = project.read_text(encoding="utf-8", errors="surrogateescape")
    repairs = []
    for entry in list_entries(text, "PDBs"):
        name = PureWindowsPath(entry).name
        marker = f"PartsDBLibs\\{name}".encode("ascii", errors="strict")
        path = library.parent / "PartsDBLibs" / name
        if path.is_file() and marker not in data:
            repairs.append({"partition": path.stem, "parts_database": str(path.resolve())})
    return repairs


def move_design_first(text: str, name: str) -> str:
    """`LIST Designs` with `name` first.

    JobWizard's command line creates the board for the first design listed, and
    reports "failed to get design information" when that is a schematic node.
    """
    match = _list_match(text, "Designs")
    if match is None:
        return text
    entries = re.findall(r'^VALUE[ \t]+"([^"]*)"', match.group(1), re.M)
    if name not in entries or entries[0] == name:
        return text
    ordered = [name, *[entry for entry in entries if entry != name]]
    end = newline(text)
    body = "".join(f'VALUE "{entry}"{end}' for entry in ordered)
    return text[: match.start(1)] + body + text[match.end(1) :]


def ensure_list(text: str, list_name: str, after_list: str) -> str:
    """`text` with an empty `LIST list_name` after `LIST after_list` when it has none."""
    if re.search(rf"^LIST {re.escape(list_name)}[ \t]*\r?$", text, re.M):
        return text
    match = re.search(
        rf"^LIST {re.escape(after_list)}[ \t]*\r?\n.*?^ENDLIST[ \t]*\r?\n", text, re.S | re.M
    )
    if match is None:
        raise ValueError(f"the project has no LIST {after_list}")
    end = newline(text)
    return text[: match.end()] + f"LIST {list_name}{end}ENDLIST{end}" + text[match.end() :]


def add_entry(text: str, list_name: str, entry: str) -> tuple[str, bool]:
    """`entry` at the end of the first `LIST list_name`; whether it had to be added."""
    match = _list_match(text, list_name)
    if match is None:
        raise ValueError(f"the project has no LIST {list_name}")
    present = {
        value.lower() for value in re.findall(r'^VALUE[ \t]+"([^"]*)"', match.group(1), re.M)
    }
    if entry.lower() in present:
        return text, False
    end = newline(text)
    return text[: match.start(2)] + f'VALUE "{entry}"{end}' + text[match.start(2) :], True


def cell_entries_for(pdb_entries: list[str], available_cells: list[str]) -> list[str]:
    """A `CellDBLibs\\<name>.cel` entry for every `PartsDBLibs\\<name>.pdb` whose cell
    file exists. These proposed references do not establish native Layout usability."""
    available = {name.lower() for name in available_cells}
    result: list[str] = []
    for entry in pdb_entries:
        stem = entry.replace("\\", "/").rsplit("/", 1)[-1]
        if stem.lower().endswith(".pdb"):
            stem = stem[:-4]
        if stem.lower() in available:
            result.append(f"CellDBLibs\\{stem}.cel")
    return result
