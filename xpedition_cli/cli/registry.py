"""Every command, declared once.

Dispatch, `reference`, `--help` and the option checks all read this table, so a
command cannot exist in one of them and not in the others. A handler is named as
`module:function` inside `xpedition_cli.cli` and imported only when it runs
(`_handler_module`).
"""

from __future__ import annotations

import functools
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
        module = _handler_module(module_name)
        handler: Callable[[dict[str, Any]], dict[str, Any]] = getattr(module, function_name)
        options = {
            **options,
            "_command": self.path,
            "_check_domain": self.extra.get("check_domain"),
        }
        if self.extra.get("generic_gate") and not (
            self.extra.get("artifact_when") and not options.get(self.extra["artifact_when"])
        ):
            from .effect_gate import invoke

            return invoke(self, handler, options)
        return handler(options)


def _handler_module(name: str) -> Any:
    """The module of `xpedition_cli.cli` a handler lives in.

    Imported here and not at the top, because the handler modules reach this
    registry through `reference_data`. The imports are statements, never a name
    computed for `importlib`: a frozen binary bundles only what PyInstaller sees
    imported, and 1.0.1's binaries, which looked the modules up by name, held none
    of them and could run no command.
    """
    from xpedition_cli.cli import (
        bom,
        checks,
        diagnostics,
        environment,
        kb,
        library,
        manufacturing,
        pcb,
        project,
        schematic,
        sessions,
    )

    modules = {
        "bom": bom,
        "checks": checks,
        "diagnostics": diagnostics,
        "environment": environment,
        "kb": kb,
        "library": library,
        "manufacturing": manufacturing,
        "pcb": pcb,
        "project": project,
        "schematic": schematic,
        "sessions": sessions,
    }
    return modules[name]


DEFAULT_SORT = "the order the design stores them; stable between calls"


def _project(description: str = "the project's .prj") -> Param:
    return Param("project", "path", True, description=description)


def _board() -> Param:
    return Param("project", "path", True, description="the project's .prj, or the board's .pcb")


_TIMEOUT = Param(
    "timeout",
    "number",
    description="upper bound requested for one native read, 1 to 3600 seconds; ordinary "
    "NativeBackend reads default to 300 seconds, subject to command-specific limits and "
    "the published total/stage budgets. On timeout inspect the session and original "
    "operation; never replay an uncertain write",
)
_LIMIT = Param("limit", "integer", description="at most this many items")
_OFFSET = Param("offset", "integer", description="skip this many items first")
_QUERY = Param("query", "string", description="keep items whose text contains this, any case")
_REPLACE = Param("replace", "boolean", description="replace the output file if it exists")
_LIBRARY_PROJECT = Param(
    "project",
    "path",
    description="the project's .prj, whose central library is read; or --library",
)
_LIBRARY = Param(
    "library",
    "path",
    description="a central library (.lmc) to read instead of a project's: another "
    "project's, or a copy of a company library",
)
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
                'xpedition-cli reference --command "pcb routing auto" --compact',
                'xpedition-cli reference --command "pcb inspect" --compact',
            ),
            "environment",
            params=(
                Param(
                    "command",
                    "string",
                    description="one command path, as pcb routing auto",
                ),
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
            "kb bind",
            "kb:add",
            "Bind a knowledge-base document's link under a name, with what it covers; "
            "binding a name again re-points it",
            "kb_add",
            _write_examples(
                'kb bind --name pcb --url https://example.com/wiki/pcb-rules --about "PCB '
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
            "kb unbind",
            "kb:remove",
            "Unbind a knowledge-base document",
            "kb_remove",
            _write_examples("kb unbind --name pcb"),
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
            "Start a CLI-owned empty Designer through its authenticated startup host, "
            "or start Layout. Designer refuses existing-instance takeover and --project; "
            "create or open an authorized project through the same dedicated connection. "
            "Layout accepts --project for its board",
            "session_start",
            (
                "xpedition-cli session start --kind schematic --dry-run --compact",
                "xpedition-cli session start --kind schematic --confirm <confirm_token> --compact",
                "xpedition-cli session start --kind pcb --project X.prj --dry-run --compact",
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
                Param(
                    "project",
                    "path",
                    description="a .prj or .pcb to open in Layout only",
                ),
            ),
        ),
        Command(
            "session inspect",
            "sessions:status",
            "Whether Designer and Layout are running, and the session this tool recorded "
            "(stale after a timed-out call)",
            "session_status",
            ("xpedition-cli session inspect --compact",),
            "session",
            needs="xpedition",
        ),
        Command(
            "session close",
            "sessions:stop",
            "Quit an owned Designer or Layout generation after observing an empty document "
            "or explicit --save-project; verify bounded OS exit, retaining pending evidence",
            "session_stop",
            _write_examples("session close --kind pcb"),
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
                Param(
                    "save-project",
                    "path",
                    description="explicit owned Layout project to save and close before Quit; "
                    "adds persist effect",
                ),
                Param(
                    "diagnostic-exit-token",
                    "string",
                    description="signed qualification returned by the original diagnostic; "
                    "required with --diagnostic-operation-id; not a confirm token",
                ),
                Param(
                    "diagnostic-operation-id",
                    "string",
                    description="same-journal completed close diagnostic for one owned empty PCB "
                    "exit; requires --kind pcb and excludes --save-project",
                ),
            ),
            blast_radius="only the bound owned process generation quits; save-project "
            "explicitly saves and closes that Layout document",
            dry_run_schema="session_stop_preview",
        ),
        # -- project ---------------------------------------------------------------
        Command(
            "project create",
            "project:create",
            "Create an owned project: clone an initialized template or initialize a "
            "stock setup template through native NewProject, then reopen and inspect it",
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
                Param(
                    "closed-copy",
                    "boolean",
                    description="file-only complete initialized backup copy; requires "
                    "--source-backup, preserves the source .prj filename, "
                    "no native calls or opening",
                ),
                Param(
                    "source-backup",
                    "path",
                    description="complete closed project-backup ZIP whose entire member set/"
                    "CRC/SHA and readable initialized source tree match; only with --closed-copy",
                ),
                Param(
                    "design",
                    "string",
                    description="initial design for a stock setup template; default Board1; "
                    "initialized clones retain their designs",
                ),
            ),
            blast_radius=(
                "a new project folder; if Designer has the template open it is closed while "
                "the folder is copied"
            ),
            dry_run_schema="project_create_preview",
        ),
        Command(
            "project inspect",
            "project:info",
            "What a .prj says, read from the file itself: its designs, board, central "
            "library and the libraries each design lists. --preflight instead queries finite "
            "design/verification/environment capabilities before design, with optional declared "
            "--requirements JSON; this does not observe CAD or authorize dispatch",
            "project_info",
            ("xpedition-cli project inspect --project X.prj --compact",),
            "project",
            params=(
                Param(
                    "project",
                    "path",
                    description="the project's .prj; optional for --preflight",
                ),
                Param(
                    "preflight",
                    "boolean",
                    description="query design-before-write capability limits without CAD",
                ),
                Param(
                    "requirements",
                    "path",
                    description="UTF-8 design-preflight-v1 declared counts JSON; "
                    "requires --preflight",
                ),
            ),
        ),
        Command(
            "project backup",
            "project:backup",
            "Save and observe normal document release, then archive every project file with "
            "hashes; refuse active or unobservable library writers and retain incomplete attempts",
            "project_backup",
            (
                "xpedition-cli project backup --project X.prj --compact",
                "xpedition-cli project backup --project X.prj --output D:/backups/x.zip --compact",
            ),
            "project",
            params=(
                _project(),
                Param(
                    "leave-closed",
                    "boolean",
                    description="keep normally released documents closed after backup; "
                    "do not reopen Designer",
                ),
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
            "library materialize",
            "library:build",
            "Generate the padstacks, cells and parts a design needs and import them into "
            "the project's central library. The dry run shows every part-to-cell mapping; "
            "an all-bound design has materialization_not_needed=true, no changes and cannot "
            "dispatch an import. Run schematic package separately",
            "library_build",
            _write_examples("library materialize --project X.prj --design design.json"),
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
                Param(
                    "package",
                    "boolean",
                    description="unsupported: use schematic package",
                ),
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
            "library:import_parts",
            "Import parts from another Xpedition central library -- another project's, or a "
            "copy of a company library -- with the symbols, cells, padstacks, pads and holes "
            "they use, each in its source partition. The source is only read; what the "
            "library holds identically is left alone. Native writes require registered "
            "partitions for the full resolved dependency closure, including kept items",
            "library_import",
            (
                *_write_examples(
                    "library import --project X.prj --from D:/libraries/Company.lmc "
                    "--parts TPS7A2033PDBVR,RC0603FR-0710KL"
                ),
                "xpedition-cli library import --project X.prj --from D:/projects/other/Other.prj "
                "--parts TPS7A2033PDBVR --dangerous --confirm <confirm_token> --compact",
            ),
            "library",
            needs="xpedition",
            tier="dangerous",
            params=(
                _project(),
                Param(
                    "from",
                    "path",
                    True,
                    description="the source: a central library (.lmc), or a project (.prj) "
                    "whose central library it is; only read",
                ),
                Param(
                    "parts",
                    "string",
                    True,
                    multiple=True,
                    description="the part numbers to import, up to 200",
                ),
            ),
            blast_radius=(
                "the project's central library gains the parts with their symbols, cells, "
                "padstacks, pads and holes, each in its source partition, and the .prj its "
                "symbol, parts and cell lists; an item of the same name and other content is "
                "replaced, and every part that uses it changes with it; Designer's project is "
                "closed and reopened meanwhile"
            ),
            dry_run_schema="library_import_preview",
            dangerous_when="the source replaces something the library holds: the dry run "
            "lists it under replaces",
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
                "xpedition-cli library list --library D:/libraries/Company.lmc --query TPS7A "
                "--compact",
            ),
            "library",
            needs="xpedition",
            params=(
                _LIBRARY_PROJECT,
                _LIBRARY,
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
            extra={"mutually_exclusive": [["project", "library"]]},
        ),
        Command(
            "library inspect",
            "library:show",
            "One part in full -- pin map, symbol pins, cell lands and their padstacks, and "
            "what is wrong with it -- or one cell and the parts that use it",
            "library_show",
            (
                "xpedition-cli library inspect --project X.prj --part TPS7A2033PDBVR --compact",
                "xpedition-cli library inspect --project X.prj --cell SOIC127P600X175-8N --compact",
            ),
            "library",
            needs="xpedition",
            params=(
                _LIBRARY_PROJECT,
                _LIBRARY,
                Param("part", "string", description="a part number"),
                Param("cell", "string", description="a cell name"),
                _TIMEOUT,
            ),
            extra={"mutually_exclusive": [["part", "cell"], ["project", "library"]]},
        ),
        Command(
            "library check",
            "library:check",
            "Check the central library: parts against their cells, symbols and pin maps, "
            "cells against the padstacks and for silkscreen over their lands, symbols with "
            "repeated pin names, part numbers in two partitions; most severe first. "
            "LMC registration is observed separately from an active matching LibraryManager; "
            "an unavailable observation does not prove registration or packager usability",
            "library_check",
            ("xpedition-cli library check --project X.prj --compact",),
            "library",
            needs="xpedition",
            params=(
                _LIBRARY_PROJECT,
                _LIBRARY,
                _PARTITIONS,
                _QUERY,
                _LIMIT,
                _OFFSET,
                _TIMEOUT,
            ),
            sort="high, medium, low; then by rule and item",
            extra={"mutually_exclusive": [["project", "library"]]},
        ),
        Command(
            "library create",
            "library:add",
            "Add parts to the central library from a parts file: each part's symbol (a box "
            "or a built-in kind), footprint (an IPC-7351B family from datasheet dimensions, "
            "lands given one by one -- several may share a pin, holes may be slots or "
            "unplated --, a placeholder package or a cell the library holds) and pin map. "
            "What the library holds already is left alone when identical. Native writes "
            "preflight registered partitions for kept and changed dependencies",
            "library_add",
            (
                *_write_examples("library create --project X.prj --file parts.json"),
                "xpedition-cli library create --project X.prj --file parts.json --dangerous "
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
            "library preview",
            "library:render",
            "A PNG per part, its symbol beside its footprint (lands numbered, silkscreen, "
            "assembly and placement outlines, a 1 mm bar): from the library, or from a "
            "parts file before it is added",
            "library_render",
            (
                "xpedition-cli library preview --file parts.json --output parts.png --compact",
                "xpedition-cli library preview --project X.prj --part TPS7A2033PDBVR "
                "--output part.png --compact",
            ),
            "library",
            needs="xpedition",
            params=(
                Param(
                    "project",
                    "path",
                    description="the project's .prj: for --part (or --library), and for a "
                    "parts file that names cells the library holds",
                ),
                _LIBRARY,
                Param("part", "string", description="a part number in the library"),
                Param("file", "path", description="a parts file, not added yet"),
                Param(
                    "output",
                    "path",
                    True,
                    description="the PNG; with several parts, <stem>-<part>.png beside it",
                ),
                _REPLACE,
                _TIMEOUT,
            ),
            extra={"mutually_exclusive": [["part", "file"], ["project", "library"]]},
        ),
        # -- schematic -------------------------------------------------------------
        Command(
            "schematic plan preview",
            "schematic:render",
            "Validate preview inputs and report planned layout findings before confirmation; "
            "a PNG of every planned sheet of a design, drawn from the plan before anything "
            "is drawn in Designer, the plan's findings boxed in red. drawing_review exposes "
            "estimated text envelopes, unmodelled symbols and unchecked native coverage; "
            "empty findings do not establish readable layout",
            "schematic_render",
            (
                (
                    "xpedition-cli schematic plan preview --design design.json --output "
                    "preview.png --compact"
                ),
            ),
            "schematic",
            params=(
                Param(
                    "design",
                    "path",
                    True,
                    description="the design description JSON; chain/ladder blocks accept "
                    "pitch in sheet units (integer60..4680, multiple20; default60); read "
                    "skills/xpedition-schematic/reference/schematic-design-format.md",
                ),
                Param(
                    "output",
                    "path",
                    True,
                    description="a .png; one file a sheet if several",
                ),
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
            "schematic build",
            "schematic:draw",
            "Plan a schematic from a design description and draw it in Designer, then read "
            "the netlist back and compare it with the plan. Each sheet drawn is wiped first; "
            "--sheets draws only those (to resume a draw that stopped). Read drawing_review "
            "for planned projection checks and missing native drawing coverage",
            "schematic_draw",
            _write_examples("schematic build --project X.prj --design design.json", dangerous=True),
            "schematic",
            needs="designer",
            tier="dangerous",
            params=(
                _project(),
                Param(
                    "design",
                    "path",
                    True,
                    description="the design description JSON; chain/ladder blocks accept "
                    "pitch in sheet units (integer60..4680, multiple20; default60); read "
                    "skills/xpedition-schematic/reference/schematic-design-format.md",
                ),
                Param(
                    "sheets",
                    "integer",
                    multiple=True,
                    description="draw only these sheets",
                ),
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
            "design. Optional set_property.display moves one existing Value or Part Label "
            "attribute (integer sheet-unit x/y, visible:true=VDVALUEVISIBLE3, optional "
            "integer Size5..30); horizontal actual getters and unique existing attributes "
            "are required. Display-only batches verify protected records before one SaveAll, "
            "normally close/reopen this project, then read identity and records again. The "
            "confirmed run reads the design back and verifies it. drawing_review "
            "lists affected objects and visual rules not checked because native text "
            "envelopes, graphics, wire paths and sheet bounds are not observed",
            "schematic_edit",
            _write_examples("schematic edit --project X.prj --file changes.json"),
            "schematic",
            needs="designer",
            tier="write",
            params=(
                _project(),
                Param(
                    "file",
                    "path",
                    True,
                    description="the operations JSON: {operations: [...]}",
                ),
            ),
            blast_radius="the parts, nets and wires the operations name; the design is saved; "
            "display-only batches normally close/reopen the bound Designer project",
            dry_run_schema="schematic_edit_preview",
            extra={
                "file_json_schema": _edit_schema(),
                "effects": ["session", "change", "persist"],
            },
        ),
        Command(
            "schematic checks run",
            "schematic:check",
            "Check the schematic: this tool's netlist rules (open pins, single-pin nets, "
            "missing part numbers, decoupling, I2C pull-ups, net names, duplicates) and "
            "Designer's own verification, in one list by severity",
            "schematic_check",
            ("xpedition-cli schematic checks run --project X.prj --compact",),
            "schematic",
            needs="designer",
            params=(_project(), _LIMIT, _OFFSET, _TIMEOUT),
            sort="severity, then source, reference designator and net",
        ),
        Command(
            "schematic parts",
            "schematic:components",
            "The schematic's parts: reference designator, part number, value, sheet, "
            "attributes and every pin with its net; limited named attribute_display getter "
            "records retain position, Visible, Size, Orientation, Origin and read failures "
            "in sheet units, with incomplete free-text/font-envelope coverage",
            "list_result",
            ("xpedition-cli schematic parts --project X.prj --query U --compact",),
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
            "schematic view",
            "schematic:show",
            "Activate a sheet, fit it and bring Designer's window to the front; --output "
            "captures the window to a new PNG. Before confirmation, drawing_review lists "
            "actual snapshot identities and unenumerated visual geometry; empty findings "
            "do not establish a clear sheet. Use --output for independently reopened PNG "
            "bytes and target project/sheet/process/window binding; without --output, "
            "independent acceptance remains incomplete. Capture verification does not "
            "establish font correctness, readability, schematic intent or ERC",
            "schematic_show",
            (
                "xpedition-cli schematic view --project X.prj --sheet 2 "
                "--output schematic-sheet2.png --compact",
            ),
            "schematic",
            needs="designer",
            params=(
                _project(),
                Param("sheet", "integer", description="the sheet number (1 by default)"),
                Param(
                    "output",
                    "path",
                    description="recommended: capture to this new .png for independent "
                    "image delivery and target-window verification; without it, "
                    "independent acceptance remains incomplete",
                ),
            ),
            blast_radius="the Designer window comes to the front; one new PNG with --output",
        ),
        Command(
            "schematic export",
            "schematic:export",
            "Render the schematic to a new PDF through Xpedition's sch2pdf and report each "
            "page's size and orientation. Before confirmation, drawing_review discloses "
            "that the Designer drawing model and visual-check inputs were not read",
            "schematic_export",
            ("xpedition-cli schematic export --project X.prj --output board.pdf --compact",),
            "schematic",
            needs="designer",
            params=(
                _project(),
                Param(
                    "output",
                    "path",
                    description="the new .pdf (beside the .prj by default)",
                ),
                Param("color", "integer", description="sch2pdf colour mode, 0 to 4"),
                Param(
                    "schematic",
                    "string",
                    description="which schematic; the first by default",
                ),
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
                Param(
                    "name",
                    "string",
                    description="the board's name (the project's by default)",
                ),
                Param(
                    "design",
                    "string",
                    description="the board design in the .prj (the first)",
                ),
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
            "pcb update",
            "pcb:annotate",
            "Forward-annotate the packaged schematic into the board: its parts and nets "
            "replace the board's, and the board is saved. Verification covers part/pin/net "
            "correspondence. With --refresh-cells, refresh central/local cell definitions "
            "and existing instance geometry instead; logical in_synch alone is insufficient",
            "pcb_annotate",
            (
                *_write_examples("pcb update --project X.prj --dangerous"),
                *_write_examples("pcb update --project X.prj --refresh-cells --dangerous"),
                "xpedition-cli pcb update --project X.prj --unroute --dangerous --confirm "
                "<confirm_token> --compact",
            ),
            "board",
            needs="layout",
            tier="dangerous",
            params=(
                _board(),
                Param(
                    "design",
                    "string",
                    description="the board design in the .prj (the first)",
                ),
                Param("unroute", "boolean", description="delete every trace and via first"),
                Param(
                    "refresh-cells",
                    "boolean",
                    description="Refresh used central cells/padstacks into local definitions "
                    "and existing instances; separate from logical annotation; requires an "
                    "already synchronized .prj, top orthogonal SMD geometry and interactive "
                    "desktop. Normally opens/closes a new PID/LMC-matched Library Services "
                    "window (may save its LMC container); refuses existing dialogs and "
                    "definition drift, never force-releases reservations",
                ),
            ),
            blast_radius=(
                "the board's components and nets follow the packaged schematic, with --unroute "
                "every trace and via goes first; Layout saves the board. With --refresh-cells, "
                "normal Library Services closure may save the LMC container; used local "
                "definitions and existing instance geometry are refreshed and saved"
            ),
            dry_run_schema="pcb_annotate_preview",
            dangerous_when="with --unroute",
        ),
        Command(
            "pcb outline set",
            "pcb:outline",
            "Replace the board outline by a width x height millimetre rectangle from the "
            "origin, optionally with rounded corners",
            "pcb_outline",
            _write_examples("pcb outline set --project X.prj --width 60 --height 45 --radius 3"),
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
            "pcb holes add-corners",
            "pcb:holes",
            "A non-plated mounting hole in each corner of the board outline",
            "pcb_holes",
            _write_examples("pcb holes add-corners --project X.prj --diameter 3.2 --inset 4"),
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
            "pcb constraints widths",
            "pcb:rules",
            "A net class with its trace widths on every layer, through Constraint "
            "Manager; the dry run shows the classes and widths as they are now",
            "pcb_rules",
            _write_examples(
                "pcb constraints widths --project X.prj --class POWER --nets VBAT,+3V3 --width 0.5 "
                "--min 0.4 --expansion 0.6"
            ),
            "board",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("class", "string", True, description="the net class"),
                Param(
                    "nets",
                    "string",
                    multiple=True,
                    description="nets to put in the class",
                ),
                Param(
                    "width",
                    "number",
                    True,
                    description="typical trace width, millimetres",
                ),
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
            "pcb placement auto",
            "pcb:arrange",
            "A first placement of the board's unplaced parts that follows the connections: "
            "one cluster per sheet, zone labels on the top silkscreen. With --all the placed "
            "parts move too",
            "pcb_arrange",
            _write_examples(
                "pcb placement auto --project X.prj --design design.json",
                dangerous=True,
            ),
            "placement",
            needs="layout",
            tier="dangerous",
            params=(
                _board(),
                Param(
                    "design",
                    "path",
                    description="the design description, for zone labels",
                ),
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
            "pcb placement move",
            "pcb:move",
            "Move parts: one to a position and rotation (--refdes, --to, --rotate), or a "
            "selection by translate, rotate, align and distribute steps (--file); every "
            "move is read back",
            "pcb_move",
            (
                (
                    "xpedition-cli pcb placement move --project X.prj --refdes U3 --to "
                    "34,35.5 --rotate 90 --dry-run --compact"
                ),
                (
                    "xpedition-cli pcb placement move --project X.prj --file task.json "
                    "--dry-run --compact"
                ),
                "xpedition-cli pcb placement move --project X.prj --file task.json --confirm "
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
                "mutually_exclusive": [
                    ["refdes", "file"],
                    ["to", "file"],
                    ["rotate", "file"],
                ],
                "file_json_schema": _placement_schema(),
            },
        ),
        Command(
            "pcb silkscreen labels",
            "pcb:labels",
            "Move uniquely identified existing silk reference texts to explicit mm positions "
            "with --file; bind actual owner/UID/fabrication Type/Side/SideName/envelope "
            "and raw soldermask stackup association; protect every other "
            "captured object before conditional save/close/reopen. Legacy automatic/size "
            "preview remains available but confirmation is disabled",
            "pcb_labels",
            _write_examples("pcb silkscreen labels --project X.prj --file silk-moves.json"),
            "placement",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param(
                    "file",
                    "path",
                    description="JSON unit=mm and moves with exact text UID/owner/"
                    "fabrication side/name, raw layer association and from/to; no resizing",
                ),
                Param(
                    "refdes",
                    "string",
                    description="comma-separated designators to move; others stay fixed obstacles",
                ),
                Param("gap", "number", description="from the part's body, millimetres"),
                Param(
                    "height",
                    "number",
                    description="every designator's text height, millimetres (0.3 to 5)",
                ),
                Param(
                    "min-height",
                    "number",
                    description="raise the designators lower than this, millimetres; the "
                    "others keep their size",
                ),
                Param(
                    "pen",
                    "number",
                    description="every designator's stroke width, millimetres (0.05 to 1)",
                ),
            ),
            blast_radius=(
                "explicit --file moves only identified silk text positions, then conditionally "
                "saves/reopens after protected readback; legacy auto/resize apply is disabled"
            ),
            extra={
                "effects": ["session", "change", "persist"],
                "mutually_exclusive": [
                    ["height", "min-height"],
                    *[["file", k] for k in ("refdes", "gap", "height", "min-height", "pen")],
                ],
                "file_json_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["unit", "moves"],
                    "properties": {
                        "unit": {"const": "mm"},
                        "moves": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 32,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "refdes",
                                    "text_uid",
                                    "text",
                                    "type",
                                    "text_type",
                                    "side",
                                    "side_name",
                                    "layer",
                                    "layer_name",
                                    "from",
                                    "to",
                                ],
                                "properties": {
                                    **{
                                        k: {"type": "string", "minLength": 1}
                                        for k in (
                                            "refdes",
                                            "text_uid",
                                            "text",
                                            "side_name",
                                        )
                                    },
                                    "type": {"const": 2},
                                    "text_type": {"const": 1},
                                    "side": {"enum": ["top", "bottom"]},
                                    "layer": {
                                        "type": "integer",
                                        "description": "Raw soldermask stackup-association getter; "
                                        "not silk identity and negative meaning remains unverified",
                                    },
                                    "layer_name": {
                                        "type": "string",
                                        "description": "Raw associated stackup-name getter; may "
                                        "be observed empty for Type2 silk",
                                    },
                                    **{
                                        k: {
                                            "type": "array",
                                            "minItems": 2,
                                            "maxItems": 2,
                                            "items": {"type": "number"},
                                        }
                                        for k in ("from", "to")
                                    },
                                },
                            },
                        },
                    },
                },
            },
            dry_run_schema="pcb_labels_preview",
        ),
        # -- routing -----------------------------------------------------------------
        Command(
            "pcb routing auto",
            "pcb:route",
            "Use --mode bounded-native for one exactly selected, unrouted "
            "two-/three-pin net or the exact six-pin GND member set on "
            "one signal layer through the native NoViaPass engine. Bounded-general instead "
            "requires --file and freezes a unique unconnected FromTo and all actual native "
            "settings for one formal General Route35367 candidate, permitting only predeclared "
            "target-GND old-copper replacements in a finite rectangle. "
            "Explicit --continue-existing "
            "instead binds only the six-pin GND with three old target traces, three material "
            "groups and two opens; every old trace identity and raw path must remain exact. "
            "Preview binds the real "
            "board, members, readable rule observations and configuration intent; opaque "
            "database/cache versions and atomic freeze are unproved. Independently verify "
            "all new trace segments, pad-material contact and protected objects before strict "
            "Save/Close/Open. The default legacy auto mode remains unsupported. Native "
            "solver acceptance does not establish complete effective-rule parsing or DRC.",
            "pcb_route",
            (
                *_write_examples(
                    "pcb routing auto --project X.prj --mode bounded-native --nets A_IN "
                    "--layers 1 --source-backup closed-source.zip"
                ),
                "xpedition-cli pcb routing auto --project X.prj --mode bounded-native "
                "--nets A_IN --layers 1 --source-backup closed-source.zip --dangerous "
                "--confirm <confirm_token> --compact",
            ),
            "routing",
            needs="layout",
            tier="dangerous",
            params=(
                _board(),
                Param(
                    "mode",
                    "string",
                    choices=("auto", "bounded-native", "bounded-general"),
                    description="auto (default) refuses legacy unprotected routing; "
                    "bounded-native requests one NoViaPass6; bounded-general uses an explicit "
                    "FromTo/material plan for one General Route35367 candidate, with strict "
                    "bounded target replacement and full output/persistence checks",
                ),
                Param(
                    "file",
                    "path",
                    description="bounded-general only: strict version1 GND plan "
                    "with layer, endpoint_pin_uids, allow_replace_trace_uids, bounds_mm and "
                    "expected_components_after; not a manual verification flag",
                ),
                Param(
                    "native-effort",
                    "integer",
                    description="bounded-native only: 1 (default) or 2, bound to both formal "
                    "NoViaPass effort arguments; one Go and all protection/persistence "
                    "gates remain",
                ),
                Param(
                    "continue-existing",
                    "boolean",
                    description="explicit bounded-native continuation only: exact six-pin GND, "
                    "three existing target traces, three observed material groups, two opens "
                    "and eleven existing board traces; all old copper must be preserved by "
                    "identity and full geometry. Without this flag old target copper refuses",
                ),
                Param(
                    "source-backup",
                    "path",
                    description="both bounded native modes require the authorized complete "
                    "closed source recovery ZIP; source hash is separate from readable live "
                    "dependencies and fresh native state, never a manual rule-verification flag",
                ),
                Param(
                    "passes",
                    "string",
                    description="legacy pass list; forbidden in both bounded native modes",
                ),
                Param(
                    "layers",
                    "string",
                    description="bounded-native requires exactly one "
                    "actual signal conductor layer number",
                ),
                Param(
                    "nets",
                    "string",
                    multiple=True,
                    description="bounded-native requires "
                    "exactly one actual two-/three-pin net or limited six-pin GND; "
                    "native identity bound",
                ),
                Param(
                    "unroute",
                    "boolean",
                    description="legacy removal request; forbidden "
                    "in bounded-native mode and never dispatched by the bounded path",
                ),
            ),
            blast_radius=(
                "Bounded-native allows only new trace material of the frozen target net on "
                "one layer. Bounded-general additionally permits only explicitly listed old "
                "GND trace changes inside plan bounds; all other copper and nonrouting state "
                "are exact protected. Actual native settings are observations, not an output "
                "guarantee; any via or wrong-layer output refuses persistence. "
                "No cleanup, fanout, unroute, new vias or plane regeneration. "
                "Complete independent target/protection checks gate Save/Close/Open. "
                "A failed or unknown native call is not replayed. Legacy auto remains refused."
            ),
            dry_run_schema="pcb_route_preview",
            dangerous_when="with --unroute",
        ),
        Command(
            "pcb routing trace",
            "pcb:trace",
            "Traces drawn where the caller says: a net, a layer, a width and points in "
            "millimetres, or a plan file of traces and vias (pcb routing stitch-plan writes one)",
            "pcb_trace",
            _write_examples(
                "pcb routing trace --project X.prj --net I2C_SCL --layer 1 --width 0.254 --points "
                '"34.2,36.0 36.5,36.0 36.5,40.5"'
            ),
            "routing",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param(
                    "mode",
                    "enum",
                    choices=("trace", "component-connect"),
                    description="trace keeps the default refusal; component-connect binds "
                    "one isolated material-component task",
                ),
                Param(
                    "component-query",
                    "path",
                    description="successful pcb inspect --components JSON envelope or direct "
                    "connectivity_components snapshot; required for component-connect",
                ),
                Param(
                    "snapshot-id",
                    "string",
                    description="exact snapshot_id from the selected component query; "
                    "required for component-connect",
                ),
                Param(
                    "from-component",
                    "string",
                    description="source component_id in that exact snapshot; "
                    "required for component-connect",
                ),
                Param(
                    "to-component",
                    "string",
                    description="target component_id in that exact snapshot; "
                    "same component is a no-op",
                ),
                Param(
                    "from-node",
                    "string",
                    description="optional actual source material node_id within --from-component",
                ),
                Param(
                    "to-node",
                    "string",
                    description="optional actual target material node_id within --to-component",
                ),
                Param(
                    "isolated-origin",
                    "path",
                    description="bounded JSON origin record for the owned isolated copy; "
                    "evidence is checked natively, no caller verified override",
                ),
                Param(
                    "source-backup",
                    "path",
                    description="optional current closed recovery ZIP; otherwise use the "
                    "isolated-origin record source",
                ),
                Param(
                    "recovery-backup",
                    "path",
                    description="new ZIP outside the project, written and verified in the "
                    "same confirmed Close window before reopen; required for a non-no-op "
                    "component connection",
                ),
                Param("net", "string", description="the net"),
                Param("layer", "integer", description="layer number (1 by default)"),
                Param("width", "number", description="millimetres (0.254 by default)"),
                Param("points", "points", description='"x,y x,y ..." in millimetres'),
                Param("file", "path", description="a plan of traces and vias instead"),
                Param(
                    "geometry",
                    "path",
                    description="check the plan against this pcb snapshot file first",
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
            "pcb routing via",
            "pcb:via",
            "A via placed where the caller says: a net and a point in millimetres",
            "pcb_trace",
            _write_examples("pcb routing via --project X.prj --net GND --at 36.5,40.5"),
            "routing",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("net", "string", True, description="the net"),
                Param("at", "point", True, description="x,y in millimetres"),
                Param(
                    "padstack",
                    "string",
                    description="the via padstack (the board's default)",
                ),
                _PACE,
            ),
            blast_radius="the board gains one via and is saved",
            dry_run_schema="pcb_trace_preview",
        ),
        Command(
            "pcb routing stitch-plan",
            "pcb:stitch",
            "The plan for ground stitching: a via beside every surface-mount pad of a net "
            "with a short stub, where the clearance check against a geometry file finds "
            "room. Works from pcb snapshot's file; feed the plan to pcb routing apply --file",
            "pcb_stitch",
            (
                (
                    "xpedition-cli pcb routing stitch-plan --geometry board.json --net GND "
                    "--output stitch.json --compact"
                ),
            ),
            "routing",
            params=(
                Param("geometry", "path", True, description="the file pcb snapshot wrote"),
                Param("net", "string", description="the net (GND by default)"),
                Param("width", "number", description="stub width in millimetres (0.3)"),
                Param("output", "path", description="write the plan to this .json"),
                _REPLACE,
            ),
        ),
        Command(
            "pcb routing unroute",
            "pcb:unroute",
            "Remove the bound native traces/vias of explicit whole nets, preserve other objects, "
            "then save/close/reopen and independently compare. Verified scope: two separated "
            "physical pins per net without planes; --all/--at/--layer are currently refused. "
            "Expected disconnects are operation success, not engineering routing completion",
            "pcb_unroute",
            _write_examples(
                "pcb routing unroute --project X.prj --nets I2C_SCL,I2C_SDA",
                dangerous=True,
            ),
            "routing",
            needs="layout",
            tier="dangerous",
            params=(
                _board(),
                Param("nets", "string", multiple=True, description="the nets to unroute"),
                Param(
                    "all",
                    "boolean",
                    description="not in the current verified subset; use explicit --nets",
                ),
                Param(
                    "at",
                    "point",
                    description="point selection is currently unsupported before native writes",
                ),
                Param(
                    "layer",
                    "integer",
                    description="partial layer selection is currently unsupported",
                ),
                Param(
                    "continue-on-error",
                    "enum",
                    choices=("true", "false"),
                    description="go on past a net that fails (true by default)",
                ),
            ),
            blast_radius=(
                "only bound routing of explicitly named nets is deleted; save/close/reopen "
                "is verified, no automatic rollback; zero selected objects performs no save"
            ),
            dry_run_schema="pcb_unroute_preview",
            dangerous_when="always: the deleted routing is not archived",
        ),
        Command(
            "pcb copper pour",
            "pcb:pour",
            "A plane shape (copper pour) for a net on a layer, inset from the outline; "
            "--remove takes the net's pour off the layer, as a net routed again after the "
            "pours needs",
            "pcb_pour",
            (
                *_write_examples("pcb copper pour --project X.prj --net GND --layer 2"),
                (
                    "xpedition-cli pcb copper pour --project X.prj --net GND --layer 1 "
                    "--remove --dry-run --compact"
                ),
            ),
            "routing",
            needs="layout",
            tier="write",
            params=(
                _board(),
                Param("net", "string", description="the net (GND by default)"),
                Param("layer", "integer", description="the layer (2 by default)"),
                Param(
                    "margin",
                    "number",
                    description="inset from the outline, millimetres (1)",
                ),
                Param(
                    "replace",
                    "boolean",
                    description="replace the net's shapes on the layer",
                ),
                Param(
                    "remove",
                    "boolean",
                    description="delete the net's shapes on the layer and pour nothing",
                ),
            ),
            blast_radius="one plane shape is added, or the net's shapes on the layer deleted, "
            "and the board saved",
            dry_run_schema="pcb_pour_preview",
            extra={"mutually_exclusive": [["replace", "remove"]]},
        ),
        # -- inspection ----------------------------------------------------------------
        Command(
            "pcb inspect",
            "pcb:info",
            "The board in numbers, with its stackup (each layer's kind, name, thickness and "
            "dielectric constant), its net classes (nets and trace widths per layer, separate "
            "class/member read completeness and located getter errors) and how "
            "many keepouts it has; native library cell names and the hosted active database "
            "object are reported separately. Failed reads are not observed empty membership. "
            "--live-geometry with explicit --pid observes the "
            "already-open matching board's full measured geometry without save/open/switch "
            "or operation reconciliation; historical saving remains unknown. With exactly "
            "one --nets name and one --layers number it also reads complete native "
            "routepass state/material evidence on that same document, without routing. "
            "--components queries arbitrary observed net material components with snapshot-scoped "
            "selection IDs; complete observation is distinct from whole-net connection. "
            "--continue-existing observes the limited GND components separately from "
            "whole-net completion; complete observation can report incomplete connectivity",
            "pcb_info",
            ("xpedition-cli pcb inspect --project X.prj --compact",),
            "inspection",
            needs="layout",
            params=(
                _board(),
                _TIMEOUT,
                Param(
                    "components",
                    "boolean",
                    description="query actual conductive components of one --nets and one "
                    "--layers with --live-geometry and --pid; no name/member-count/layout "
                    "whitelist, no writes, snapshot-scoped component/node identities",
                ),
                Param(
                    "native-general-state",
                    "boolean",
                    description="read-only General Route discovery; requires --live-geometry, "
                    "--pid, --nets GND and one --layers conductor. Returns full raw material "
                    "state, groups, actual/nearest FromTo endpoints and supported configuration "
                    "GETs; no selection clearing, routing or persistence",
                ),
                Param(
                    "continue-existing",
                    "boolean",
                    description="read-only existing-components observation; requires "
                    "--live-geometry, --pid, --nets GND and exactly one --layers conductor. "
                    "Observation completeness and whole-net completion are separate; no Go "
                    "or persistence is dispatched",
                ),
                Param(
                    "nets",
                    "string",
                    description="normal read: 2..8 exact names with --layers for Master "
                    "clearances; --live-geometry: exactly one name plus one layer adds "
                    "complete read-only native routepass state",
                ),
                Param(
                    "layers",
                    "string",
                    description="normal Master query: 1..8 actual layers; --live-geometry "
                    "routepass observation requires exactly one actual layer with one net; "
                    "active/location schemes are not inferred",
                ),
                Param(
                    "live-geometry",
                    "boolean",
                    description="bounded live geometry observation, including while unresolved; "
                    "requires --pid and scoped project; all live selectors use the published "
                    "complete-board-query profile: at most 1200 seconds per native board-info "
                    "request, 1260 seconds for the query stage and 1500 seconds total query "
                    "budget; see project inspect --preflight for current resource and budget "
                    "limits",
                ),
                Param(
                    "drc-hazards",
                    "boolean",
                    description="with --live-geometry and --pid, read existing batch hazards "
                    "and supported151.0 object evidence; never runs DRC or Save/Close/Open",
                ),
                Param(
                    "mask-inventory",
                    "boolean",
                    description="with --live-geometry and --pid, read only the current "
                    "MaskEngine collection count on that exact board; no geometry capture, "
                    "allocation, deletion, cleanup or recovery conclusion",
                ),
                Param(
                    "silk-text-uids",
                    "string",
                    description="with --live-geometry and --pid only: comma-separated 1..8 "
                    "observed native silk text UIDs; complete bounded surroundings reads and "
                    "separate rule/clearance/eligibility report; no engineering edit "
                    "or draft activation",
                ),
                Param(
                    "pid",
                    "integer",
                    description="explicit existing Layout process for --live-geometry; "
                    "never starts or switches a document",
                ),
            ),
        ),
        Command(
            "pcb snapshot",
            "pcb:geometry",
            "The board as data: outline, parts with their origin, extents and pins, pads "
            "with net, layer and pin, traces with width, vias, planes, holes, keepouts, open "
            "nets, silkscreen, and its texts with their part, box and strokes; --refdes and "
            "--nets keep only what they name, the parts' silkscreen with them; to a JSON "
            "file with --output",
            "pcb_geometry",
            (
                "xpedition-cli pcb snapshot --project X.prj --output board.json --compact",
                "xpedition-cli pcb snapshot --project X.prj --refdes U1 --nets GND --compact",
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
            "holes, silkscreen and its designators as Layout strokes them); --refdes draws "
            "only those parts and what is around them, larger; needs no screen, so it works "
            "with the desktop locked",
            "pcb_render",
            (
                "xpedition-cli pcb render --project X.prj --output board.png --compact",
                "xpedition-cli pcb render --project X.prj --refdes J1 --output j1.png --compact",
            ),
            "inspection",
            needs="layout",
            params=(
                _board(),
                Param("output", "path", True, description="the .png"),
                Param(
                    "side",
                    "enum",
                    choices=("top", "bottom"),
                    description="top by default",
                ),
                Param(
                    "scale",
                    "number",
                    description="pixels per millimetre, 1 to 200 (20; with --refdes, what "
                    "makes the picture 1600 px across)",
                ),
                Param(
                    "refdes",
                    "string",
                    multiple=True,
                    description="draw only these parts and what is around them",
                ),
                _REPLACE,
            ),
        ),
        Command(
            "pcb view",
            "pcb:show",
            "Observe and apply a file-bound local display scheme, fit the board or selected "
            "parts, and optionally capture a new PNG. Preview binds Config sources and "
            "lists layout reminders; verification separates effective scheme, viewport, "
            "visibility, foreground and image decoding from visual review.",
            "pcb_show",
            (
                "xpedition-cli pcb view --project X.prj --compact",
                "xpedition-cli pcb view --project X.prj --refdes J1 --output j1.png --compact",
            ),
            "inspection",
            needs="layout",
            params=(
                _board(),
                Param(
                    "scheme",
                    "string",
                    description="a display scheme of the board, e.g. 'Loc: All On' (every "
                    "layer and all three designators of each part)",
                ),
                Param(
                    "top-view",
                    "boolean",
                    description="the top view, as without --scheme (Config/Top View.dcs, "
                    "written the first time)",
                ),
                Param(
                    "refdes",
                    "string",
                    multiple=True,
                    description="zoom onto these parts and their designators",
                ),
                Param("output", "path", description="capture the window to this new .png"),
            ),
            blast_radius=(
                "the Layout window comes to the front and its display scheme and zoom change; "
                "the top view's scheme file is written into the board's Config folder the "
                "first time; one new PNG with --output"
            ),
            extra={"mutually_exclusive": [["scheme", "top-view"]]},
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
                Param("geometry", "path", description="the file pcb snapshot wrote"),
                Param(
                    "project",
                    "path",
                    description="measure the live board instead (Layout)",
                ),
                Param("baseline", "path", description="an earlier pcb metrics --output"),
                Param("output", "path", description="write the result to this .json"),
                _REPLACE,
            ),
        ),
        Command(
            "pcb checks run",
            "pcb:check",
            "Run Layout's Batch DRC and list every hazard with its type, position, objects "
            "and clearance, and the board rules DRC does not cover: parts unplaced, off the "
            "board or overlapping, decoupling far from its IC, connectors far from an edge, "
            "acute corners, nets still open, silkscreen designators over pads or holes, on "
            "each other, across silkscreen lines, under other parts, past the edge or too "
            "small, and silkscreen lines over pads; --no-run only reads the hazards already "
            "there. passes/clean require completed, fresh native evidence; unconfirmed "
            "results are incomplete, and --no-run cannot establish a current pass",
            "pcb_check",
            ("xpedition-cli pcb checks run --project X.prj --compact",),
            "inspection",
            needs="layout",
            params=(
                _board(),
                Param("no-run", "boolean", description="read the hazards already there"),
                Param("online", "boolean", description="add the online DRC's hazards"),
                Param(
                    "min-text-height",
                    "number",
                    description="the lowest silkscreen text that passes, millimetres (0.6)",
                ),
            ),
            blast_radius="Layout runs its checks and records hazards on the board",
        ),
        Command(
            "pcb diagnostics close",
            "diagnostics:close",
            "Review-only diagnostic on a fresh authenticated closed-backup copy and owned "
            "Layout. One bounded Save and Close(True); no engine, Go, Open or retry. "
            "Unknown terminal stops without CAD queries. Only returned terminal permits "
            "two bound identity/file frames; never qualifies save/reopen or PCB acceptance.",
            "close_diagnostic",
            _write_examples(
                "pcb diagnostics close --project NEW.prj --pid 1234 "
                "--progid MGCPCB.Application.151 --isolated-origin origin.json"
            ),
            "inspection",
            needs="layout",
            tier="write",
            params=(
                Param(
                    "post-open-token",
                    "string",
                    description="final successful first-startup certificate for the bounded "
                    "post-Open project/board baseline; required for newly prepared copies",
                ),
                _board(),
                Param("pid", "integer", True, description="original owned Layout process"),
                Param(
                    "progid",
                    "string",
                    True,
                    description="one explicit versioned Layout ProgID",
                ),
                Param(
                    "isolated-origin",
                    "path",
                    True,
                    description="registration plus original successful closed-copy operation ID",
                ),
            ),
            blast_radius="Save once and Close(True) once on the new isolated board only; "
            "the owned Layout remains running. No reopen or automatic recovery.",
            extra={"effects": ["session", "persist", "artifact"]},
        ),
        # -- fabrication -----------------------------------------------------------------
        Command(
            "manufacturing export",
            "pcb:export",
            "The fabrication package: Layout's ODB++, Gerber (RS-274X) and NC drill outputs, "
            "copied into one folder with a centroid file, a BOM and a manifest of checks",
            "pcb_export",
            _write_examples(
                "manufacturing export --project X.prj --formats odb,gerber,ncdrill --output fab"
            ),
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
            "bom inspect",
            "bom:export",
            "The bill of materials from the schematic: one row per part, or per part number "
            "with --group; --baseline lists what changed since an earlier export",
            "bom_export",
            (
                "xpedition-cli bom inspect --project X.prj --group --output bom.json --compact",
                "xpedition-cli bom inspect --project X.prj --baseline bom.json --compact",
            ),
            "fabrication",
            needs="designer",
            params=(
                _project(),
                Param(
                    "group",
                    "boolean",
                    description="one row per part number, with quantity",
                ),
                Param("baseline", "path", description="an earlier bom inspect --output"),
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
    from .command_extensions import canonical_commands

    result = tuple(canonical_commands(_build()))
    paths = [command.path for command in result]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate command path")
    if any(other.startswith(path + " ") for path in paths for other in paths):
        raise ValueError("a command leaf cannot also be a parent group")
    return result


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
    for length in range(len(positionals), 0, -1):
        command = table.get(" ".join(positionals[:length]))
        if command is not None:
            return command, length
    return None, 0


# The whole chain, in order. Each step names the commands that do it.
WORKFLOW = [
    {
        "step": 1,
        "do": "check the machine and start Designer",
        "commands": ["doctor", "session inspect", "session start", "session attach"],
    },
    {
        "step": 2,
        "do": "create the project from a template",
        "commands": ["project create"],
    },
    {
        "step": 3,
        "do": "put the parts the design needs into the library: look, add or import, look at them",
        "commands": [
            "library list",
            "library create",
            "library import",
            "library preview",
        ],
    },
    {
        "step": 4,
        "do": "preview the design, then draw the schematic",
        "commands": ["schematic plan preview", "schematic build"],
    },
    {
        "step": 5,
        "do": "build the placeholder parts and package the design",
        "commands": ["library materialize", "library check", "schematic package"],
    },
    {
        "step": 6,
        "do": "check the schematic and its bill of materials",
        "commands": [
            "schematic checks run",
            "schematic checks result",
            "bom check",
            "schematic export",
        ],
    },
    {
        "step": 7,
        "do": "back up, then create the board, start Layout, bring the schematic in",
        "commands": ["project backup", "pcb create", "session start", "pcb update"],
    },
    {
        "step": 8,
        "do": "shape the board: outline, holes, net classes",
        "commands": [
            "pcb outline set",
            "pcb holes add-corners",
            "pcb constraints widths",
        ],
    },
    {
        "step": 9,
        "do": "place the parts and their labels",
        "commands": [
            "pcb placement auto",
            "pcb placement move",
            "pcb silkscreen labels",
        ],
    },
    {
        "step": 10,
        "do": "route and pour",
        "commands": [
            "pcb routing auto",
            "pcb routing trace",
            "pcb routing via",
            "pcb routing stitch-plan",
            "pcb copper pour",
        ],
    },
    {
        "step": 11,
        "do": "check and measure; look at it",
        "commands": [
            "pcb checks run",
            "pcb checks result",
            "pcb snapshot",
            "pcb metrics",
            "pcb render",
        ],
    },
    {
        "step": 12,
        "do": "fabrication outputs and independent byte integrity",
        "commands": [
            "manufacturing export",
            "manufacturing verify",
            "bom inspect",
            "bom export",
        ],
    },
]
