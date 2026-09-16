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
from dataclasses import dataclass

DEFAULT_LAYOUT_TEMPLATE = "4 Layer Template"
CELL_LIST = "2dCellLibraries"


@dataclass
class Design:
    name: str
    config_type: str = ""
    pcb_path: str = ""
    layout_template: str = ""

    def is_board(self) -> bool:
        return self.config_type.upper() == "PCB"


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
    partition exists, so Layout's Database Load can find cells for the parts it packages."""
    available = {name.lower() for name in available_cells}
    result: list[str] = []
    for entry in pdb_entries:
        stem = entry.replace("\\", "/").rsplit("/", 1)[-1]
        if stem.lower().endswith(".pdb"):
            stem = stem[:-4]
        if stem.lower() in available:
            result.append(f"CellDBLibs\\{stem}.cel")
    return result
