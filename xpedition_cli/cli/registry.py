"""Every command, declared once.

Dispatch, `reference`, `--help` and the option checks all read this table, so a
command cannot exist in one of them and not in the others. A handler is named as
`module:function` inside `xpedition_cli.cli` and imported only when it runs.
"""

from __future__ import annotations

import functools
import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# What a command needs running (or installed) on the machine.
NEEDS = {
    "none": "nothing: it works from files or local configuration",
    "xpedition": "an Xpedition installation; no application has to be running",
    "designer": "Xpedition Designer running (session start --kind schematic)",
    "layout": "Xpedition Layout running (session start --kind pcb)",
}

# The design flow, in order; every command belongs to one stage.
STAGES = (
    "environment",
    "session",
    "project",
    "library",
    "schematic",
    "board",
    "placement",
    "routing",
    "inspection",
    "fabrication",
)


# Options every command takes: how the answer is written.
GLOBAL_OPTIONS: dict[str, str] = {
    "format": "value",
    "json": "switch",
    "compact": "switch",
    "fields": "value",
    "quiet": "switch",
    "help": "switch",
}
GLOBAL_DESCRIPTIONS = {
    "format": "json (the default), text or raw",
    "json": "the same as --format json",
    "compact": "one line of JSON",
    "fields": "only these data paths, comma-separated (items.refdes, items[].refdes); "
    "paging and _untrusted are kept",
    "quiet": "no progress lines on stderr",
    "help": "usage for people; agents read reference",
}


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    required: bool = False
    multiple: bool = False
    choices: tuple[str, ...] = ()
    description: str = ""

    def contract(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "multiple": self.multiple,
        }
        if self.choices:
            item["choices"] = list(self.choices)
        if self.description:
            item["description"] = self.description
        return item


@dataclass(frozen=True)
class Command:
    path: str
    handler: str
    description: str
    output_schema: str
    examples: tuple[str, ...]
    stage: str
    needs: str = "none"
    tier: str = "read"
    params: tuple[Param, ...] = ()
    blast_radius: str = "none"
    dry_run_schema: str | None = None
    dangerous_when: str | None = None
    sort: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def writes(self) -> bool:
        return self.tier in {"write", "dangerous"}

    def all_params(self) -> tuple[Param, ...]:
        """The declared params plus the write gates every write takes."""
        params = list(self.params)
        names = {param.name for param in params}
        if self.writes:
            for gate in (
                Param("dry-run", "boolean", description="preview and get a confirm token"),
                Param("confirm", "string", description="the token the dry run returned"),
            ):
                if gate.name not in names:
                    params.append(gate)
        if self.tier == "dangerous" and "dangerous" not in names:
            params.append(
                Param(
                    "dangerous",
                    "boolean",
                    description="the second gate; see dangerous_when for when it is needed",
                )
            )
        return tuple(params)

    def contract(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "path": self.path,
            "type": "write" if self.writes else "query",
            "description": self.description,
            "stage": self.stage,
            "needs": self.needs,
            "params": [param.contract() for param in self.all_params()],
            "output_schema": self.output_schema,
            "examples": list(self.examples),
            "permission_tier": self.tier,
            "blast_radius": self.blast_radius,
        }
        if self.dry_run_schema:
            item["dry_run_output_schema"] = self.dry_run_schema
        if self.tier == "dangerous":
            item["dangerous"] = True
            item["dangerous_when"] = self.dangerous_when or "always"
        if any(param.name == "limit" for param in self.params) and not self.writes:
            item["default_sort"] = self.sort or DEFAULT_SORT
        item.update(self.extra)
        return item

    def run(self, options: dict[str, Any]) -> dict[str, Any]:
        module_name, _, function_name = self.handler.partition(":")
        module = importlib.import_module(f"xpedition_cli.cli.{module_name}")
        handler: Callable[[dict[str, Any]], dict[str, Any]] = getattr(module, function_name)
        return handler(options)


DEFAULT_SORT = "the order the design stores them; stable between calls"


def _project(description: str = "the project's .prj") -> Param:
    return Param("project", "path", True, description=description)


def _board() -> Param:
    return Param("project", "path", True, description="the project's .prj, or the board's .pcb")


_TIMEOUT = Param(
    "timeout",
    "number",
    description="seconds the read may take, 1 to 3600 (120 by default); a read that runs "
    "out of time leaves the session stale, so give a large design room up front",
)
_LIMIT = Param("limit", "integer", description="at most this many items")
_OFFSET = Param("offset", "integer", description="skip this many items first")
_QUERY = Param("query", "string", description="keep items whose text contains this, any case")
_REPLACE = Param("replace", "boolean", description="replace the output file if it exists")
_PARTITIONS = Param(
    "partition",
    "string",
    multiple=True,
    description="only these central-library partitions",
)
_PACE = Param(
    "pace",
    "number",
    description="seconds between items, 0 to 10, so someone watching the screen can follow",
)


def _write_examples(base: str, dangerous: bool = False) -> tuple[str, str]:
    confirm = " --dangerous" if dangerous else ""
    return (
        f"xpedition-cli {base} --dry-run --compact",
        f"xpedition-cli {base}{confirm} --confirm <confirm_token> --compact",
    )


def _placement_schema() -> dict[str, Any]:
    from ..placement import input_schema

    return input_schema()


def _edit_schema() -> dict[str, Any]:
    from ..edit_operations import input_schema

    return input_schema()


def _parts_schema() -> dict[str, Any]:
    from ..library_parts import input_schema

    return input_schema()


def _build() -> list[Command]:
    return [
        # -- environment ---------------------------------------------------------
        Command(
            "context",
            "environment:context",
            "Runtime, configuration, the Xpedition installation and the bound "
            "knowledge-base documents",
            "context",
            ("xpedition-cli context --compact",),
            "environment",
            params=(Param("project", "path", description="a .prj to report on"),),
        ),
        Command(
            "doctor",
            "environment:doctor",
            "Check the machine: the Xpedition adapter, which applications are running, "
            "the project, and release readiness; each problem comes with its fix",
            "doctor",
            ("xpedition-cli doctor --compact",),
            "environment",
            params=(Param("project", "path", description="also check this .prj"),),
        ),
        Command(
            "reference",
            "environment:reference",
            "The live machine-readable contract: commands, params, output schemas, "
            "error codes and the design workflow",
            "reference",
            (
                "xpedition-cli reference --compact",
                'xpedition-cli reference --command "pcb route" --compact',
                "xpedition-cli reference --domain pcb --compact",
                "xpedition-cli reference --schema pcb_check --compact",
            ),
            "environment",
            params=(
                Param("command", "string", description="one command path, as pcb route"),
                Param("domain", "string", description="one top-level domain, as schematic"),
                Param("schema", "string", description="one output schema's name"),
            ),
            extra={"mutually_exclusive": [["command", "domain", "schema"]]},
        ),
        Command(
            "changelog",
            "environment:changelog",
            "What changed in each release",
            "changelog",
            (
                "xpedition-cli changelog --compact",
                "xpedition-cli changelog --since 1.0.0 --compact",
            ),
            "environment",
            params=(Param("since", "semver", description="only releases newer than this"),),
        ),
        Command(
            "version",
            "environment:version",
            "The CLI's version",
            "version",
            ("xpedition-cli version --compact",),
            "environment",
        ),
        Command(
            "kb list",
            "kb:listing",
            "The knowledge-base documents bound on this machine: a name, a link and what "
            "each covers. The agent reads them with its own tools",
            "kb_list",
            ("xpedition-cli kb list --compact",),
            "environment",
        ),
        Command(
            "kb add",
            "kb:add",
            "Bind a knowledge-base document's link under a name, with what it covers; "
            "binding a name again re-points it",
            "kb_add",
            _write_examples(
                'kb add --name pcb --url https://example.com/wiki/pcb-rules --about "PCB '
                'layout rules"'
            ),
            "environment",
            tier="write",
            params=(
                Param("name", "string", True, description="a short name for the document"),
                Param("url", "string", True, description="its link"),
                Param("about", "string", description="what it covers"),
            ),
            blast_radius="one entry in knowledge-base.json in the tool's config directory",
            dry_run_schema="kb_change_preview",
        ),
        Command(
            "kb remove",
            "kb:remove",
            "Unbind a knowledge-base document",
            "kb_remove",
            _write_examples("kb remove --name pcb"),
            "environment",
            tier="write",
            params=(Param("name", "string", True, description="the name it was bound under"),),
            blast_radius="one entry in knowledge-base.json in the tool's config directory",
            dry_run_schema="kb_change_preview",
        ),
        # -- session ---------------------------------------------------------------
        Command(
            "session start",
            "sessions:start",
            "Start Xpedition Designer or Layout and wait until it can be automated; one "
            "already running is attached instead. With --project the project (Designer) "
            "or its board (Layout) is opened, Layout's start-up questions answered",
            "session_start",
            (
                "xpedition-cli session start --kind schematic --project X.prj --compact",
                "xpedition-cli session start --kind pcb --project X.prj --compact",
            ),
            "session",
            needs="xpedition",
            params=(
                Param(
                    "kind",
                    "enum",
                    True,
                    choices=("schematic", "pcb"),
                    description="schematic starts Designer, pcb starts Layout",
                ),
                Param("project", "path", description="a .prj (or .pcb for Layout) to open"),
            ),
        ),
        Command(
            "session status",
            "sessions:status",
            "Whether Designer and Layout are running, and the session this tool recorded "
            "(stale after a timed-out call)",
            "session_status",
            ("xpedition-cli session status --compact",),
            "session",
            needs="xpedition",
        ),
        Command(
            "session stop",
            "sessions:stop",
            "Quit Designer or Layout; unsaved work in it is lost",
            "session_stop",
            _write_examples("session stop --kind pcb"),
            "session",
            needs="xpedition",
            tier="write",
            params=(
                Param(
                    "kind",
                    "enum",
                    choices=("schematic", "pcb"),
                    description="which application; not needed when only one is running",
                ),
            ),
            blast_radius="the application quits; unsaved design work in it is lost",
            dry_run_schema="session_stop_preview",
        ),
        # -- project ---------------------------------------------------------------
        Command(
            "project create",
            "project:create",
            "Create a project by copying a template project: the folder is copied, the "
            ".prj renamed, its library keys pointed into the copy, and it is opened in "
            "Designer",
            "project_create",
            _write_examples(
                "project create --template D:/tpl/Tpl.prj --project D:/work/new/New.prj"
            ),
            "project",
            needs="designer",
            tier="write",
            params=(
                Param(
                    "project",
                    "path",
                    True,
                    description="the new .prj; ASCII path, its folder absent or empty",
                ),
                Param("template", "path", True, description="an existing .prj to copy"),
            ),
            blast_radius=(
                "a new project folder; if Designer has the template open it is closed while "
                "the folder is copied"
            ),
            dry_run_schema="project_create_preview",
        ),
        Command(
            "project info",
            "project:info",
            "What a .prj says, read from the file itself: its designs, board, central "
            "library and the libraries each design lists",
            "project_info",
            ("xpedition-cli project info --project X.prj --compact",),
            "project",
            params=(_project(),),
        ),
        Command(
            "project backup",
            "project:backup",
            "Zip the project folder -- the .prj, the schematic database, the board and the "
            "central library when it lives there -- beside the folder, with a manifest",
            "project_backup",
            (
                "xpedition-cli project backup --project X.prj --compact",
                "xpedition-cli project backup --project X.prj --output D:/backups/x.zip --compact",
            ),
            "project",
            params=(
                _project(),
                Param(
                    "output",
                    "path",
                    description="the .zip; <folder>-backups/<project>-<time>.zip beside the "
                    "project folder by default",
                ),
            ),
        ),
        Command(
            "project restore",
            "project:restore",
            "Make the project folder what a backup holds: the dry run lists the files it "
            "adds, replaces and removes; the confirmed run closes the project, zips the "
            "folder as it is first, restores and opens the project again",
            "project_restore",
            (
                "xpedition-cli project restore --project X.prj --backup x.zip --dry-run --compact",
                "xpedition-cli project restore --project X.prj --backup x.zip --dangerous "
                "--confirm <confirm_token> --compact",
            ),
            "project",
            tier="dangerous",
            params=(
                _project(),
                Param("backup", "path", True, description="a .zip project backup made"),
            ),
            blast_radius=(
                "every file of the project folder the backup differs from -- schematic, "
                "board, library -- is replaced or removed; the folder is zipped first"
            ),
            dry_run_schema="project_restore_preview",
            dangerous_when="always: it replaces and removes the project's files",
        ),
        # -- library ---------------------------------------------------------------
        Command(
            "library build",
            "library:build",
            "Generate the padstacks, cells and parts a design needs and import them into "
            "the project's central library; --package runs the packager afterwards. The "
            "dry run shows every part-to-cell mapping",
            "library_build",
            _write_examples("library build --project X.prj --design design.json --package"),
            "library",
            needs="designer",
            tier="write",
            params=(
                _project(),
                Param("design", "path", True, description="the design description JSON"),
                Param(
                    "partition",
                    "string",
                    description="the central-library partition for the parts (PartQuest)",
                ),
                Param("package", "boolean", description="run the packager afterwards"),
            ),
            blast_radius=(
                "the project's central library gains padstacks, a cell partition and a parts "
                "partition, and the .prj its parts-database list; Designer's project is "
                "closed and reopened meanwhile"
            ),
            dry_run_schema="library_build_preview",
        ),
        Command(
            "library import",
            "library:import_libraries",
            "Convert KiCad footprint libraries (.pretty folders) into cell partitions of "
            "the project's central library, their padstacks into its padstack database",
            "library_import",
            (
                "xpedition-cli library import --project X.prj --libraries "
                "Package_SO,Resistor_SMD --dry-run --compact",
                "xpedition-cli library import --project X.prj --libraries "
                "Package_SO,Resistor_SMD --dangerous --confirm <confirm_token> --compact",
            ),
            "library",
            needs="designer",
            tier="dangerous",
            params=(
                _project(),
                Param(
                    "libraries",
                    "string",
                    multiple=True,
                    description="library names, each a <name>.pretty folder under --root",
                ),
                Param(
                    "root",
                    "path",
                    description="folder of the .pretty libraries; the KiCad installation's "
                    "footprints by default",
                ),
                Param("limit", "integer", description="at most this many footprints a library"),
                Param(
                    "continue-on-error",
                    "enum",
                    choices=("true", "false"),
                    description="go on past a library that fails (true by default)",
                ),
            ),
            blast_radius=(
                "one cell partition per library in the project's central library (an existing "
                "one is merged, its same-named cells overwritten) and the shared padstack "
                "database; Designer's project is closed while it runs"
            ),
            dry_run_schema="library_import_preview",
            dangerous_when="a library's partition exists already: the dry run marks it",
        ),
        Command(
            "library list",
            "library:list_items",
            "What the project's central library holds, one kind at a time: parts, cells, "
            "symbols or padstacks, read through the stock converters (cached until a "
            "database changes)",
            "library_list",
            (
                "xpedition-cli library list --project X.prj --compact",
                "xpedition-cli library list --project X.prj --kind cells --query SOIC --compact",
            ),
            "library",
            needs="xpedition",
            params=(
                _project(),
                Param(
                    "kind",
                    "enum",
                    choices=("parts", "cells", "symbols", "padstacks"),
                    description="what to list (parts)",
                ),
                _PARTITIONS,
                _QUERY,
                _LIMIT,
                _OFFSET,
                _TIMEOUT,
            ),
            sort="by partition, then name or part number",
        ),
        Command(
            "library show",
            "library:show",
            "One part in full -- pin map, symbol pins, cell lands and their padstacks, and "
            "what is wrong with it -- or one cell and the parts that use it",
            "library_show",
            (
                "xpedition-cli library show --project X.prj --part TPS7A2033PDBVR --compact",
                "xpedition-cli library show --project X.prj --cell SOIC127P600X175-8N --compact",
            ),
            "library",
            needs="xpedition",
            params=(
                _project(),
                Param("part", "string", description="a part number"),
                Param("cell", "string", description="a cell name"),
                _TIMEOUT,
            ),
            extra={"mutually_exclusive": [["part", "cell"]]},
        ),
        Command(
            "library check",
            "library:check",
            "Check the central library: parts against their cells, symbols and pin maps, "
            "cells against the padstacks, symbols with repeated pin names, part numbers in "
            "two partitions; most severe first",
            "library_check",
            ("xpedition-cli library check --project X.prj --compact",),
            "library",
            needs="xpedition",
            params=(_project(), _PARTITIONS, _QUERY, _LIMIT, _OFFSET, _TIMEOUT),
            sort="high, medium, low; then by rule and item",
        ),
        Command(
            "library add",
            "library:add",
            "Add parts to the central library from a parts file: each part's symbol (a box "
            "or a built-in kind), footprint (an IPC-7351B family from datasheet dimensions, "
            "lands given one by one -- several may share a pin --, a placeholder package, a "
            "cell the library holds or an imported KiCad footprint) and pin map. What the "
            "library holds already is left alone when identical",
            "library_add",
            (
                *_write_examples("library add --project X.prj --file parts.json"),
                "xpedition-cli library add --project X.prj --file parts.json --dangerous "
                "--confirm <confirm_token> --compact",
            ),
            "library",
            needs="xpedition",
            tier="dangerous",
            params=(
                _project(),
                Param("file", "path", True, description="the parts file (JSON)"),
                Param(
                    "partition",
                    "string",
                    description="the partition for the parts, over the file's (PartQuest)",
                ),
                Param(
                    "kicad-root",
                    "path",
                    description="folder of the KiCad .pretty libraries a kicad footprint "
                    "names; the KiCad installation's by default",
                ),
            ),
            blast_radius=(
                "the project's central library gains the parts with their symbols, cells and "
                "padstacks, and the .prj its symbol, parts and cell lists; a part, cell, "
                "padstack or pad of the same name and other content is replaced, and every "
                "part that uses it changes with it; Designer's project is closed and "
                "reopened meanwhile"
            ),
            dry_run_schema="library_add_preview",
            dangerous_when="the file replaces something the library holds: the dry run "
            "lists it under replaces",
            extra={"file_json_schema": _parts_schema()},
        ),
        Command(
            "library render",
            "library:render",
            "A PNG per part, its symbol beside its footprint (lands numbered, silkscreen, "
            "assembly and placement outlines, a 1 mm bar): from the library, or from a "
            "parts file before it is added",
            "library_render",
            (
                "xpedition-cli library render --file parts.json --output parts.png --compact",
                "xpedition-cli library render --project X.prj --part TPS7A2033PDBVR "
                "--output part.png --compact",
            ),
            "library",
            needs="xpedition",
            params=(
                Param(
                    "project",
                    "path",
                    description="the project's .prj: needed for --part, and for a parts file "
                    "that names cells the library holds",
                ),
                Param("part", "string", description="a part number in the library"),
                Param("file", "path", description="a parts file, not added yet"),
                Param(
                    "output",
                    "path",
                    True,
                    description="the PNG; with several parts, <stem>-<part>.png beside it",
                ),
                Param(
                    "kicad-root",
                    "path",
                    description="folder of the KiCad .pretty libraries a kicad footprint names",
                ),
                _REPLACE,
                _TIMEOUT,
            ),
            extra={"mutually_exclusive": [["part", "file"]]},
        ),
        # -- schematic -------------------------------------------------------------
        Command(
            "schematic render",
            "schematic:render",
            "A PNG of every planned sheet of a design, drawn from the plan before anything "
            "is drawn in Designer, the plan's findings boxed in red",
            "schematic_render",
            ("xpedition-cli schematic render --design design.json --output preview.png --compact",),
            "schematic",
            params=(
                Param("design", "path", True, description="the design description JSON"),
                Param("output", "path", True, description="a .png; one file a sheet if several"),
                Param(
                    "project",
                    "path",
                    description="the project's .prj, whose central library holds the parts "
                    'the design\'s symbols name ({"part": NUMBER}); needed only then',
                ),
                Param("sheets", "integer", multiple=True, description="only these sheets"),
                Param("scale", "number", description="pixels per sheet unit, 0.5 to 6"),
                _REPLACE,
            ),
        ),
        Command(
            "schematic draw",
            "schematic:draw",
            "Plan a schematic from a design description and draw it in Designer, then read "
            "the netlist back and compare it with the plan. Each sheet drawn is wiped first; "
            "--sheets draws only those (to resume a draw that stopped)",
            "schematic_draw",
            _write_examples("schematic draw --project X.prj --design design.json", dangerous=True),
            "schematic",
            needs="designer",
            tier="dangerous",
            params=(
                _project(),
                Param("design", "path", True, description="the design description JSON"),
                Param("sheets", "integer", multiple=True, description="draw only these sheets"),
                _PACE,
            ),
            blast_radius=(
                "every sheet drawn is wiped and redrawn, hand edits included; symbol files are "
                "written into the project's central-library partition"
            ),
            dry_run_schema="schematic_draw_preview",
            dangerous_when="always: each sheet it draws is wiped first, hand edits included",
        ),
        Command(
            "schematic edit",
            "schematic:edit",
            "Change a drawn schematic in place from an operations file: place, move or "
            "delete a part, set a part's property, create a net, connect or disconnect a "
            "pin, rename a labelled net. The dry run checks every operation against the "
            "design; the confirmed run reads the design back and verifies it",
            "schematic_edit",
            _write_examples("schematic edit --project X.prj --file changes.json"),
            "schematic",
            needs="designer",
            tier="write",
            params=(
                _project(),
                Param("file", "path", True, description="the operations JSON: {operations: [...]}"),
            ),
            blast_radius="the parts, nets and wires the operations name; the design is saved",
            dry_run_schema="schematic_edit_preview",
            extra={"file_json_schema": _edit_schema()},
        ),
        Command(
            "schematic check",
            "schematic:check",
            "Check the schematic: this tool's netlist rules (open pins, single-pin nets, "
            "missing part numbers, decoupling, I2C pull-ups, net names, duplicates) and "
            "Designer's own verification, in one list by severity",
            "schematic_check",
            ("xpedition-cli schematic check --project X.prj --compact",),
            "schematic",
            needs="designer",
            params=(_project(), _LIMIT, _OFFSET, _TIMEOUT),
            sort="severity, then source, reference designator and net",
        ),
        Command(
            "schematic components",
            "schematic:components",
            "The schematic's parts: reference designator, part number, value, sheet, "
            "attributes and every pin with its net",
            "list_result",
            ("xpedition-cli schematic components --project X.prj --query U --compact",),
            "schematic",
            needs="designer",
            params=(_project(), _QUERY, _LIMIT, _OFFSET, _TIMEOUT),
        ),
        Command(
            "schematic nets",
            "schematic:nets",
            "The schematic's nets: name, kind (power, ground or signal), sheets and the "
            "pins each joins",
            "list_result",
            ("xpedition-cli schematic nets --project X.prj --query I2C --compact",),
            "schematic",
            needs="designer",
            params=(_project(), _QUERY, _LIMIT, _OFFSET, _TIMEOUT),
        ),
        Command(
            "schematic sheets",
            "schematic:sheets",
            "The schematic's sheets",
            "list_result",
            ("xpedition-cli schematic sheets --project X.prj --compact",),
            "schematic",
            needs="designer",
            params=(_project(), _TIMEOUT),
        ),
        Command(
            "schematic show",
            "schematic:show",
            "Activate a sheet, fit it and bring Designer's window to the front; --output "
            "captures the window to a new PNG",
            "schematic_show",
            ("xpedition-cli schematic show --project X.prj --sheet 2 --compact",),
            "schematic",
            needs="designer",
            params=(
                _project(),
                Param("sheet", "integer", description="the sheet number (1 by default)"),
                Param("output", "path", description="capture the window to this new .png"),
            ),
            blast_radius="the Designer window comes to the front; one new PNG with --output",
        ),
        Command(
            "schematic export",
            "schematic:export",
            "Render the schematic to a new PDF through Xpedition's sch2pdf and report each "
            "page's size and orientation",
            "schematic_export",
            ("xpedition-cli schematic export --project X.prj --output board.pdf --compact",),
            "schematic",
            needs="xpedition",
            params=(
                _project(),
                Param("output", "path", description="the new .pdf (beside the .prj by default)"),
                Param("color", "integer", description="sch2pdf colour mode, 0 to 4"),
                Param("schematic", "string", description="which schematic; the first by default"),
                _REPLACE,
            ),
            blast_radius="one new PDF; an existing file is never replaced",
        ),
        # -- board -------------------------------------------------------------------
        Command(
            "pcb create",
            "pcb:create",
            "Create the project's board from a layout template of its central library "
            "through JobWizard",
            "pcb_create",
            (
                *_write_examples('pcb create --project X.prj --template "4 Layer Template"'),
                "xpedition-cli pcb create --project X.prj --replace --dangerous --confirm "
                "<confirm_token> --compact",
            ),
            "board",
            needs="designer",
            tier="dangerous",
            params=(
                _project(),
                Param(
                    "template",
                    "string",
                    description="the layout template's name (4 Layer Template by default)",
                ),
                Param("name", "string", description="the board's name (the project's by default)"),
                Param("design", "string", description="the board design in the .prj (the first)"),
                Param("replace", "boolean", description="replace an existing board"),
            ),
            blast_radius=(
                "a new PCB folder beside the .prj; the .prj gains the board path, the template "
                "name and a cell-library list; with --replace the existing layout data is zipped "
                "beside the project and removed first, and a Layout process holding it is ended"
            ),
            dry_run_schema="pcb_create_preview",
            dangerous_when="with --replace",
        ),
        Command(
            "pcb annotate",
            "pcb:annotate",
            "Forward-annotate the packaged schematic into the board: its parts and nets "
            "replace the board's, and the board is saved",
            "pcb_annotate",
            (
                *_write_examples("pcb annotate --project X.prj"),
                "xpedition-cli pcb annotate --project X.prj --unroute --dangerous --confirm "
                "<confirm_token> --compact",
            ),
            "board",
            needs="layout",
            tier="dangerous",
            params=(
                _board(),
                Param("design", "string", description="the board design in the .prj (the first)"),
                Param("unroute", "boolean", description="delete every trace and via first"),
            ),
            blast_radius=(
                "the board's components and nets follow the packaged schematic, with --unroute "
                "every trace and via goes first; Layout saves the board"
            ),
            dry_run_schema="pcb_annotate_preview",
            dangerous_when="with --unroute",
        ),
        Command(
            "pcb outline",
            "pcb:outline",
            "Replace the board outline by a width x height millimetre rectangle from the "
            "origin, optionally with rounded corners",
            "pcb_outline",
            _write_examples("pcb outline --project X.prj --width 60 --height 45 --radius 3"),
            "board",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("width", "number", True, description="millimetres"),
                Param("height", "number", True, description="millimetres"),
                Param("radius", "number", description="corner radius in millimetres"),
            ),
            blast_radius="the board outline is replaced and the board saved",
            dry_run_schema="pcb_outline_preview",
        ),
        Command(
            "pcb holes",
            "pcb:holes",
            "A non-plated mounting hole in each corner of the board outline",
            "pcb_holes",
            _write_examples("pcb holes --project X.prj --diameter 3.2 --inset 4"),
            "board",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("diameter", "number", description="millimetres (2.2 by default)"),
                Param("inset", "number", description="from each edge, millimetres (3.5)"),
                Param("replace", "boolean", description="remove the existing holes first"),
            ),
            blast_radius="mounting holes are added at the corners and the board saved",
            dry_run_schema="pcb_holes_preview",
        ),
        Command(
            "pcb rules",
            "pcb:rules",
            "A net class with its trace widths on every layer, through Constraint "
            "Manager; the dry run shows the classes and widths as they are now",
            "pcb_rules",
            _write_examples(
                "pcb rules --project X.prj --class POWER --nets VBAT,+3V3 --width 0.5 "
                "--min 0.4 --expansion 0.6"
            ),
            "board",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("class", "string", True, description="the net class"),
                Param("nets", "string", multiple=True, description="nets to put in the class"),
                Param("width", "number", True, description="typical trace width, millimetres"),
                Param("min", "number", description="minimum width, millimetres"),
                Param("expansion", "number", description="expansion width, millimetres"),
            ),
            blast_radius=(
                "one net class and its trace widths change in the constraint database; Layout "
                "re-reads its constraints"
            ),
            dry_run_schema="pcb_rules_preview",
        ),
        # -- placement ---------------------------------------------------------------
        Command(
            "pcb arrange",
            "pcb:arrange",
            "A first placement of the board's unplaced parts that follows the connections: "
            "one cluster per sheet, zone labels on the top silkscreen. With --all the placed "
            "parts move too",
            "pcb_arrange",
            _write_examples("pcb arrange --project X.prj --design design.json", dangerous=True),
            "placement",
            needs="layout",
            tier="dangerous",
            params=(
                _board(),
                Param("design", "path", description="the design description, for zone labels"),
                Param("all", "boolean", description="move the placed parts too"),
                _PACE,
            ),
            blast_radius=(
                "every trace and via on the board is deleted first; every unplaced part is "
                "placed (with --all the placed ones move too), zone labels are written on the "
                "top silkscreen and the board is saved"
            ),
            dry_run_schema="pcb_arrange_preview",
            dangerous_when="when the board has traces or vias: the dry run's routing counts them",
        ),
        Command(
            "pcb move",
            "pcb:move",
            "Move parts: one to a position and rotation (--refdes, --to, --rotate), or a "
            "selection by translate, rotate, align and distribute steps (--file); every "
            "move is read back",
            "pcb_move",
            (
                "xpedition-cli pcb move --project X.prj --refdes U3 --to 34,35.5 --rotate 90 "
                "--dry-run --compact",
                "xpedition-cli pcb move --project X.prj --file task.json --dry-run --compact",
                "xpedition-cli pcb move --project X.prj --file task.json --confirm "
                "<confirm_token> --compact",
            ),
            "placement",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("refdes", "string", description="the part to move"),
                Param("to", "point", description="its new origin, x,y in millimetres"),
                Param("rotate", "number", description="its new rotation in degrees"),
                Param(
                    "file",
                    "path",
                    description="a placement task: {schema_version, unit, selection, steps}",
                ),
            ),
            blast_radius=(
                "the named parts move (their traces stay where they were); Layout refuses a "
                "position that touches another part; the board is saved"
            ),
            dry_run_schema="pcb_move_preview",
            extra={
                "mutually_exclusive": [["refdes", "file"], ["to", "file"], ["rotate", "file"]],
                "file_json_schema": _placement_schema(),
            },
        ),
        Command(
            "pcb labels",
            "pcb:labels",
            "Move every part's silkscreen reference designator to a free spot beside the "
            "part (above, below, left or right)",
            "pcb_labels",
            _write_examples("pcb labels --project X.prj --gap 0.3"),
            "placement",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("gap", "number", description="from the part's body, millimetres"),
            ),
            blast_radius="the silkscreen designators move and the board is saved",
            dry_run_schema="pcb_labels_preview",
        ),
        # -- routing -----------------------------------------------------------------
        Command(
            "pcb route",
            "pcb:route",
            "Run Layout's autorouter passes over the board, or only the nets named, on the "
            "layers named, then save; the dry run reports how much is routed now",
            "pcb_route",
            (
                *_write_examples('pcb route --project X.prj --passes "route:1-5,viamin,smooth"'),
                "xpedition-cli pcb route --project X.prj --unroute --dangerous --confirm "
                "<confirm_token> --compact",
            ),
            "routing",
            needs="layout",
            tier="dangerous",
            params=(
                _board(),
                Param(
                    "passes",
                    "string",
                    description="passes and their effort (route:1-5,viamin:1-3,smooth:1-3)",
                ),
                Param("layers", "string", description="layer numbers to route on, as 1,4"),
                Param("nets", "string", multiple=True, description="route only these nets"),
                Param("unroute", "boolean", description="delete every trace and via first"),
            ),
            blast_radius=(
                "traces and vias on every net the passes touch; with --unroute every trace and "
                "via is deleted first; the board is saved"
            ),
            dry_run_schema="pcb_route_preview",
            dangerous_when="with --unroute",
        ),
        Command(
            "pcb trace",
            "pcb:trace",
            "Traces drawn where the caller says: a net, a layer, a width and points in "
            "millimetres, or a plan file of traces and vias (pcb stitch writes one)",
            "pcb_trace",
            _write_examples(
                "pcb trace --project X.prj --net I2C_SCL --layer 1 --width 0.254 --points "
                '"34.2,36.0 36.5,36.0 36.5,40.5"'
            ),
            "routing",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("net", "string", description="the net"),
                Param("layer", "integer", description="layer number (1 by default)"),
                Param("width", "number", description="millimetres (0.254 by default)"),
                Param("points", "points", description='"x,y x,y ..." in millimetres'),
                Param("file", "path", description="a plan of traces and vias instead"),
                Param(
                    "geometry",
                    "path",
                    description="check the plan against this pcb geometry file first",
                ),
                _PACE,
                Param(
                    "dangerous",
                    "boolean",
                    description="confirm a plan the --geometry clearance check failed",
                ),
            ),
            blast_radius="the board gains the listed traces and vias and is saved",
            dry_run_schema="pcb_trace_preview",
        ),
        Command(
            "pcb via",
            "pcb:via",
            "A via placed where the caller says: a net and a point in millimetres",
            "pcb_trace",
            _write_examples("pcb via --project X.prj --net GND --at 36.5,40.5"),
            "routing",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("net", "string", True, description="the net"),
                Param("at", "point", True, description="x,y in millimetres"),
                Param("padstack", "string", description="the via padstack (the board's default)"),
                _PACE,
            ),
            blast_radius="the board gains one via and is saved",
            dry_run_schema="pcb_trace_preview",
        ),
        Command(
            "pcb stitch",
            "pcb:stitch",
            "The plan for ground stitching: a via beside every surface-mount pad of a net "
            "with a short stub, where the clearance check against a geometry file finds "
            "room. Works from pcb geometry's file; feed the plan to pcb trace --file",
            "pcb_stitch",
            (
                "xpedition-cli pcb stitch --geometry board.json --net GND --output stitch.json "
                "--compact",
            ),
            "routing",
            params=(
                Param("geometry", "path", True, description="the file pcb geometry wrote"),
                Param("net", "string", description="the net (GND by default)"),
                Param("width", "number", description="stub width in millimetres (0.3)"),
                Param("output", "path", description="write the plan to this .json"),
                _REPLACE,
            ),
        ),
        Command(
            "pcb unroute",
            "pcb:unroute",
            "Delete the traces and vias of the named nets (--nets), of every net (--all), or "
            "of one object at a point (--at)",
            "pcb_unroute",
            _write_examples("pcb unroute --project X.prj --nets I2C_SCL,I2C_SDA", dangerous=True),
            "routing",
            needs="layout",
            tier="dangerous",
            params=(
                _board(),
                Param("nets", "string", multiple=True, description="the nets to unroute"),
                Param("all", "boolean", description="every net"),
                Param("at", "point", description="the trace or via at x,y (millimetres)"),
                Param("layer", "integer", description="with --at: only on this layer"),
                Param(
                    "continue-on-error",
                    "enum",
                    choices=("true", "false"),
                    description="go on past a net that fails (true by default)",
                ),
            ),
            blast_radius=(
                "the routing of the named nets (or of all) is deleted, not archived, and the "
                "board saved"
            ),
            dry_run_schema="pcb_unroute_preview",
            dangerous_when="always: the deleted routing is not archived",
        ),
        Command(
            "pcb pour",
            "pcb:pour",
            "A plane shape (copper pour) for a net on a layer, inset from the outline",
            "pcb_pour",
            _write_examples("pcb pour --project X.prj --net GND --layer 2"),
            "routing",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("net", "string", description="the net (GND by default)"),
                Param("layer", "integer", description="the layer (2 by default)"),
                Param("margin", "number", description="inset from the outline, millimetres (1)"),
                Param("replace", "boolean", description="replace the net's shapes on the layer"),
            ),
            blast_radius="one plane shape is added and the board saved",
            dry_run_schema="pcb_pour_preview",
        ),
        # -- inspection ----------------------------------------------------------------
        Command(
            "pcb info",
            "pcb:info",
            "The board in numbers, with its stackup (each layer's kind, name, thickness and "
            "dielectric constant), its net classes (nets and trace widths per layer) and how "
            "many keepouts it has",
            "pcb_info",
            ("xpedition-cli pcb info --project X.prj --compact",),
            "inspection",
            needs="layout",
            params=(_board(), _TIMEOUT),
        ),
        Command(
            "pcb geometry",
            "pcb:geometry",
            "The board as data: outline, parts with their origin, extents and pins, pads "
            "with net and layer, traces with width, vias, planes, holes, keepouts, open "
            "nets, silkscreen and texts; --refdes and --nets keep only what they name; to a "
            "JSON file with --output",
            "pcb_geometry",
            (
                "xpedition-cli pcb geometry --project X.prj --output board.json --compact",
                "xpedition-cli pcb geometry --project X.prj --refdes U1 --nets GND --compact",
            ),
            "inspection",
            needs="layout",
            params=(
                _board(),
                Param("output", "path", description="write the model to this .json"),
                Param("refdes", "string", multiple=True, description="only these parts"),
                Param("nets", "string", multiple=True, description="only these nets"),
                _REPLACE,
            ),
        ),
        Command(
            "pcb render",
            "pcb:render",
            "A PNG of the board drawn from its geometry (outline, pads, traces, vias, planes, "
            "holes, silkscreen); needs no screen, so it works with the desktop locked",
            "pcb_render",
            ("xpedition-cli pcb render --project X.prj --output board.png --compact",),
            "inspection",
            needs="layout",
            params=(
                _board(),
                Param("output", "path", True, description="the .png"),
                Param("side", "enum", choices=("top", "bottom"), description="top by default"),
                Param("scale", "number", description="pixels per millimetre, 1 to 200 (20)"),
                _REPLACE,
            ),
        ),
        Command(
            "pcb show",
            "pcb:show",
            "Bring Layout's board window to the front under a display scheme that shows the "
            "parts; --output captures the window to a new PNG",
            "pcb_show",
            ("xpedition-cli pcb show --project X.prj --top-view --compact",),
            "inspection",
            needs="layout",
            params=(
                _board(),
                Param("scheme", "string", description="a display scheme of the board"),
                Param(
                    "top-view",
                    "boolean",
                    description="write a top-view scheme once (Config/Top View.dcs) and use it",
                ),
                Param("output", "path", description="capture the window to this new .png"),
            ),
            blast_radius=(
                "the Layout window comes to the front and its display scheme changes; one new "
                "PNG with --output"
            ),
        ),
        Command(
            "pcb metrics",
            "pcb:metrics",
            "How good the placement and routing are, as numbers to compare two states by: "
            "ratsnest length and crossings, overlaps, parts off the board, decoupling "
            "distances, connector-to-edge distances, density, trace length, vias and acute "
            "corners; --baseline compares with an earlier result. From a geometry file, or "
            "the live board with --project",
            "pcb_metrics",
            (
                "xpedition-cli pcb metrics --project X.prj --output metrics-before.json --compact",
                "xpedition-cli pcb metrics --geometry board.json --baseline metrics-before.json "
                "--compact",
            ),
            "inspection",
            params=(
                Param("geometry", "path", description="the file pcb geometry wrote"),
                Param("project", "path", description="measure the live board instead (Layout)"),
                Param("baseline", "path", description="an earlier pcb metrics --output"),
                Param("output", "path", description="write the result to this .json"),
                _REPLACE,
            ),
        ),
        Command(
            "pcb check",
            "pcb:check",
            "Run Layout's Batch DRC and list every hazard with its type, position, objects "
            "and clearance, and the board rules DRC does not cover: parts unplaced, off the "
            "board or overlapping, decoupling far from its IC, connectors far from an edge, "
            "acute corners, nets still open; --no-run only reads the hazards already there",
            "pcb_check",
            ("xpedition-cli pcb check --project X.prj --compact",),
            "inspection",
            needs="layout",
            params=(
                _board(),
                Param("no-run", "boolean", description="read the hazards already there"),
                Param("online", "boolean", description="add the online DRC's hazards"),
            ),
            blast_radius="Layout runs its checks and records hazards on the board",
        ),
        # -- fabrication -----------------------------------------------------------------
        Command(
            "pcb export",
            "pcb:export",
            "The fabrication package: Layout's ODB++, Gerber (RS-274X) and NC drill outputs, "
            "copied into one folder with a centroid file, a BOM and a manifest of checks",
            "pcb_export",
            _write_examples("pcb export --project X.prj --formats odb,gerber,ncdrill --output fab"),
            "fabrication",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param(
                    "formats",
                    "string",
                    multiple=True,
                    choices=("odb", "gerber", "ncdrill"),
                    description="odb,gerber,ncdrill by default",
                ),
                Param("output", "path", description="the package folder"),
            ),
            blast_radius=(
                "Layout's output setups are patched (the board closed and reopened when needed), "
                "its Output folders are written and the package folder is created"
            ),
            dry_run_schema="pcb_export_preview",
        ),
        Command(
            "bom export",
            "bom:export",
            "The bill of materials from the schematic: one row per part, or per part number "
            "with --group; --baseline lists what changed since an earlier export",
            "bom_export",
            (
                "xpedition-cli bom export --project X.prj --group --output bom.json --compact",
                "xpedition-cli bom export --project X.prj --baseline bom.json --compact",
            ),
            "fabrication",
            needs="designer",
            params=(
                _project(),
                Param("group", "boolean", description="one row per part number, with quantity"),
                Param("baseline", "path", description="an earlier bom export --output"),
                Param("output", "path", description="also write the rows to this .json"),
                _REPLACE,
                _LIMIT,
                _OFFSET,
                _TIMEOUT,
            ),
        ),
        Command(
            "bom check",
            "bom:check",
            "Problems in the bill of materials: parts without a part number, repeated "
            "reference designators, and one part number used with different values or "
            "packages",
            "bom_check",
            ("xpedition-cli bom check --project X.prj --compact",),
            "fabrication",
            needs="designer",
            params=(_project(), _TIMEOUT),
        ),
    ]


@functools.lru_cache(maxsize=1)
def _table() -> tuple[Command, ...]:
    return tuple(_build())


def commands() -> list[Command]:
    """Every command, in the order of the design flow."""
    return list(_table())


def by_path() -> dict[str, Command]:
    return {command.path: command for command in commands()}


def domains() -> list[str]:
    return sorted({command.path.split()[0] for command in commands()})


def find(positionals: list[str]) -> tuple[Command | None, int]:
    """The command the leading positionals name, and how many of them it used."""
    table = by_path()
    for length in (2, 1):
        command = table.get(" ".join(positionals[:length]))
        if command is not None:
            return command, length
    return None, 0


# The whole chain, in order. Each step names the commands that do it.
WORKFLOW = [
    {
        "step": 1,
        "do": "check the machine and start Designer",
        "commands": ["doctor", "session start"],
    },
    {"step": 2, "do": "create the project from a template", "commands": ["project create"]},
    {
        "step": 3,
        "do": "put the parts the design needs into the library: look, add, look at them",
        "commands": ["library list", "library add", "library render", "library import"],
    },
    {
        "step": 4,
        "do": "preview the design, then draw the schematic",
        "commands": ["schematic render", "schematic draw"],
    },
    {
        "step": 5,
        "do": "build the placeholder parts and package the design",
        "commands": ["library build", "library check"],
    },
    {
        "step": 6,
        "do": "check the schematic and its bill of materials",
        "commands": ["schematic check", "bom check", "schematic export"],
    },
    {
        "step": 7,
        "do": "back up, then create the board, start Layout, bring the schematic in",
        "commands": ["project backup", "pcb create", "session start", "pcb annotate"],
    },
    {
        "step": 8,
        "do": "shape the board: outline, holes, net classes",
        "commands": ["pcb outline", "pcb holes", "pcb rules"],
    },
    {
        "step": 9,
        "do": "place the parts and their labels",
        "commands": ["pcb arrange", "pcb move", "pcb labels"],
    },
    {
        "step": 10,
        "do": "route and pour",
        "commands": ["pcb route", "pcb trace", "pcb via", "pcb stitch", "pcb pour"],
    },
    {
        "step": 11,
        "do": "check and measure; look at it",
        "commands": ["pcb check", "pcb geometry", "pcb metrics", "pcb render"],
    },
    {"step": 12, "do": "fabrication outputs", "commands": ["pcb export", "bom export"]},
]
