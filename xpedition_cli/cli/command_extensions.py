"""Canonical task boundaries. Internal adapter method names remain stable."""

from __future__ import annotations

import re
from dataclasses import replace

from .registry import Command, Param, _project, _write_examples


def _without(command: Command, *names: str) -> Command:
    return replace(command, params=tuple(p for p in command.params if p.name not in names))


def canonical_commands(base: list[Command]) -> list[Command]:
    table = {command.path: command for command in base}
    inspect_session = table["session inspect"]
    table["session inspect"] = replace(
        inspect_session,
        params=(
            *inspect_session.params,
            Param(
                "kind",
                "string",
                choices=("schematic",),
                description="only with --designer-identity-probe; explicitly select Designer",
            ),
            Param(
                "designer-identity-probe",
                "string",
                choices=("application-proxy",),
                description="debugging-only, non-authorizing held-application proxy diagnostic; "
                "requires --kind schematic and skips legacy window/NativeOM scans",
            ),
        ),
        examples=(
            *inspect_session.examples,
            "xpedition-cli session inspect --kind schematic "
            "--designer-identity-probe application-proxy --compact",
        ),
    )
    create = table["project create"]
    table["project create"] = replace(
        create,
        description=create.description + ". Explicit --closed-copy instead copies every verified "
        "closed-backup member of an initialized source into a new disjoint ASCII directory, "
        "relocates only public PRJ library keys and independently verifies all copied bytes. "
        "It never calls native applications, initializes a library/PCB or opens the result; "
        "complete byte copying is not native openability or production acceptance.",
        examples=(
            *create.examples,
            *_write_examples(
                "project create --template D:/source/Board.prj --project D:/isolated/Board.prj "
                "--closed-copy --source-backup D:/backups/source.zip"
            ),
        ),
    )
    inspect = table["pcb inspect"]
    if not any(param.name == "components" for param in inspect.params):
        table["pcb inspect"] = replace(
            inspect,
            params=(
                *inspect.params,
                Param(
                    "components",
                    "boolean",
                    description="read actual net material components with --live-geometry, "
                    "--pid, one --nets and one --layers; snapshot-scoped identities, no writes",
                ),
            ),
        )
    for path in ("project backup", "schematic export", "manufacturing export"):
        command = table[path]
        table[path] = replace(
            command,
            params=tuple(
                replace(p, required=True) if p.name == "output" else p for p in command.params
            ),
        )
    command = table["session close"]
    table["session close"] = replace(
        command,
        params=(*command.params, Param("pid", "integer", True)),
        extra={"effects": ["session", "persist"]},
    )
    for path in ("kb bind", "kb unbind"):
        command = table[path]
        table[path] = replace(
            command,
            tier="dangerous",
            dangerous_when="always: persistent tool configuration changes",
            extra={"effects": ["configure", "persist"]},
        )
    table["library materialize"] = replace(
        _without(table["library materialize"], "package"),
        description=(
            "Materialize the missing placeholder padstacks, cells and parts "
            "required by a design. This does not run schematic packaging; use "
            "schematic package as a separate operation."
        ),
        examples=_write_examples("library materialize --project X.prj --design design.json"),
    )
    table["pcb routing trace"] = replace(
        _without(table["pcb routing trace"], "file"),
        description=(
            "Preview one explicitly named net, signal layer, mm path and width on the exact "
            "already-open board. Default trace mode requires exactly two physical "
            "pins, one open and no existing routing. Bind endpoint/pad ownership, geometry, "
            "inputs, rules and "
            "possible copper impact before mutation. Named-width diagnostics retain class "
            "and member read completeness and located getter errors; failed reads are not "
            "observed empty membership. Current native rule applicability and "
            "bounded copper-change proof are unavailable: can_apply=false and confirmation "
            "refuses before routing, regeneration or Save/Close/Open. No dangerous override. "
            "Explicit component-connect mode instead binds an exact --component-query snapshot, "
            "two component/node choices and an isolated-origin record to one native DRC "
            "transaction. "
            "Complete actual material and old-object protection precede conditional persistence. "
            "Same-component tasks are no-ops without apply or Save. Full effective-rule parsing "
            "and manufacturing acceptance remain unproved."
        ),
        params=tuple(
            replace(
                p,
                required=True,
                description={
                    "net": "exact existing native net name; required",
                    "layer": "explicit actual signal conductor layer; required",
                    "width": "explicit trace width in millimetres; required",
                    "points": "explicit ordered x,y path in millimetres; required",
                }[p.name],
            )
            if p.name in {"net", "layer", "width", "points"}
            else replace(p, description="does not override missing impact/rule/protection proof")
            if p.name == "dangerous"
            else p
            for p in _without(table["pcb routing trace"], "file").params
        ),
        examples=(
            *_write_examples(
                "pcb routing trace --project X.prj --net QA_SIGNAL --layer 1 --width "
                '0.25 --points "1,1;2,2"'
            ),
            *_write_examples(
                "pcb routing trace --project isolated.prj --mode component-connect "
                "--component-query components.json --snapshot-id <actual-snapshot-id> "
                "--from-component <actual-source-component-id> "
                "--to-component <actual-target-component-id> --isolated-origin origin.json "
                "--recovery-backup new-closed-stage.zip "
                '--net RETURN --layer 1 --width 0.254 --points "1,1 2,1"'
            ),
        ),
        blast_radius="Default trace mode remains ineligible. Explicit component-connect may add "
        "one trace only to the bound isolated copy, then conditionally save/close/reopen after "
        "native DRC transaction and complete material/protection checks; no automatic replay",
        extra={
            **table["pcb routing trace"].extra,
            "mutually_exclusive": [],
            "effects": ["session", "change", "persist", "artifact"],
            "component_connect_required": [
                "component-query",
                "snapshot-id",
                "from-component",
                "to-component",
                "isolated-origin",
                "net",
                "layer",
                "width",
                "points",
            ],
        },
    )
    table["pcb constraints widths"] = replace(
        table["pcb constraints widths"],
        needs="none",
        description=(
            "Preview the requested net-class creation/selection, net assignment, all three "
            "trace-width fields on every layer and Layout synchronization. This full command "
            "has no verified native write contract: can_apply=false; offline preview observes "
            "no native state and confirmation refuses before COM attachment or writes. A "
            "single IConstraint.Value PUT cannot validate the whole command. See "
            "docs/CURRENT-CANDIDATE.md for current evidence."
        ),
        blast_radius=(
            "No native write is eligible; the original class, assignment, three-field "
            "all-layer and synchronization scope remains unverified"
        ),
    )
    table["pcb copper pour"] = _without(table["pcb copper pour"], "remove")
    table["pcb copper pour"] = replace(
        table["pcb copper pour"],
        description=(
            "Create one new bounded plane on an empty rectangular signal-layer board, "
            "generate only its net/layer assignment, verify actual copper/voids and protected "
            "objects before saving, then normally close/reopen and compare. Explicit net/layer "
            "required; existing planes, vias/slots, replacement and removal are unsupported. "
            "Full thermal/tie conductor geometry lacks independent proof; native connectivity, "
            "DRC execution and persistence are separate evidence."
        ),
        params=tuple(
            replace(
                p,
                required=True,
                description=(
                    "exact existing net name (required)"
                    if p.name == "net"
                    else "actual conductor layer number (required)"
                ),
            )
            if p.name in {"net", "layer"}
            else replace(p, description="unsupported before native writes")
            if p.name == "replace"
            else p
            for p in table["pcb copper pour"].params
        ),
        blast_radius=(
            "one new plane and its exact net/layer assignment; preservation "
            "and actual generation gate Save/Close/Open"
        ),
        examples=_write_examples("pcb copper pour --project X.prj --net GND --layer 2"),
        extra={"effects": ["session", "change", "persist"]},
    )
    table["pcb routing unroute"] = replace(
        table["pcb routing unroute"],
        extra={**table["pcb routing unroute"].extra, "effects": ["session", "change", "persist"]},
    )
    table["pcb checks run"] = replace(
        _without(table["pcb checks run"], "no-run"),
        handler="checks:pcb_run",
        tier="write",
        params=(
            *_without(table["pcb checks run"], "no-run").params,
            Param(
                "rules",
                "path",
                description="Existing native .drc settings; default PCB/Config/BatchDrc.dat "
                "then (Final DRC).drc",
            ),
            Param(
                "elements",
                "path",
                description="Existing native element-clearance .e2e settings; "
                "defaults beside the board",
            ),
        ),
        description="Run a new bound Layout Batch DRC and derived board-rules check. "
        "Saves/closes the board, runs the registered engine once, writes native "
        "configurations/reports, then reopens. Preview names the known report, setup, "
        "staging and lifecycle-log paths; other native side effects remain unclassified. "
        "A completed report can contain violations; "
        "ignored rules are retained. Unproven native completion remains incomplete; "
        "read its retained report with pcb checks result.",
        extra={
            **table["pcb checks run"].extra,
            "effects": ["change", "persist", "artifact"],
            "generic_gate": True,
        },
    )
    table["schematic checks run"] = replace(
        table["schematic checks run"],
        handler="checks:schematic_run",
        tier="write",
        description=(
            "Run Designer verification and derived netlist rules, retaining a "
            "report bound to this run. Native completion and rule coverage are "
            "reported separately."
        ),
        extra={
            **table["schematic checks run"].extra,
            "effects": ["change", "artifact"],
            "generic_gate": True,
        },
    )
    table["bom inspect"] = replace(
        _without(table["bom inspect"], "output", "replace"),
        handler="bom:inspect",
        description=(
            "Read the schematic bill of materials; optionally group rows or "
            "compare a baseline. Use bom export to create a file."
        ),
        examples=("xpedition-cli bom inspect --project X.prj --group --compact",),
    )
    table["session start"] = replace(
        table["session start"],
        examples=(
            *_write_examples("session start --kind schematic"),
            *_write_examples("session start --kind pcb --project X.prj"),
        ),
    )
    for path, effects in {
        "session start": ["session"],
        "project backup": ["session", "persist", "artifact"],
        "library preview": ["artifact"],
        "schematic plan preview": ["artifact"],
        "schematic view": ["session", "artifact"],
        "schematic export": ["artifact"],
        "pcb render": ["artifact"],
        "pcb view": ["session", "artifact"],
    }.items():
        command = table[path]
        table[path] = replace(
            command, tier="write", extra={**command.extra, "effects": effects, "generic_gate": True}
        )
    for path in (
        "project create",
        "project restore",
        "library materialize",
        "library import",
        "library create",
        "pcb create",
        "manufacturing export",
    ):
        table[path] = replace(
            table[path], extra={**table[path].extra, "effects": ["session", "change", "persist"]}
        )
    table["manufacturing export"] = replace(
        table["manufacturing export"],
        extra={
            **table["manufacturing export"].extra,
            "effects": ["session", "change", "persist", "artifact"],
        },
    )
    table["project backup"] = replace(
        table["project backup"],
        description=(
            "Preview complete read-only library/Designer/Layout release identities; "
            "unknown writers refuse before confirmation. Recheck frozen identities before "
            "saving/closing matching owned documents, then archive every project file "
            "with hashes. Outside projects remain untouched; no forced release."
        ),
        examples=_write_examples("project backup --project X.prj --output backup.zip"),
    )
    table["schematic export"] = replace(
        table["schematic export"],
        examples=_write_examples("schematic export --project X.prj --output schematic.pdf"),
    )
    table["kb bind"] = replace(
        table["kb bind"], examples=_write_examples("kb bind --name qa --url https://example.com/qa")
    )
    table["kb unbind"] = replace(
        table["kb unbind"], examples=_write_examples("kb unbind --name qa")
    )
    # These commands can be pure observations or create an optional output file.
    for path in ("pcb snapshot", "pcb metrics", "pcb routing stitch-plan"):
        command = table[path]
        table[path] = replace(
            command,
            tier="write",
            extra={
                **command.extra,
                "effects": ["observe", "artifact"],
                "generic_gate": True,
                "artifact_when": "output",
            },
        )
    result = [table[command.path] for command in base]
    result.extend(
        [
            Command(
                "session attach",
                "sessions:attach",
                "Attach to an explicitly selected running Layout process; never launch an "
                "application. For Designer, explicitly open a registered closed copy through "
                "the current CLI-owned empty session with --project-source-operation-id; "
                "arbitrary running-instance takeover is unsupported. "
                "Recover Layout ownership only from its original successful start operation. "
                "Ownership storage is bounded to 64 exact generations and 256 KiB. "
                "The retained runtime host-bootstrap experiment remains disabled.",
                "session_start",
                (
                    *_write_examples(
                        "session attach --kind pcb --pid 1234 "
                        "--startup-operation-id <original_start_id>"
                    ),
                ),
                "session",
                needs="xpedition",
                tier="write",
                params=(
                    Param("kind", "string", True, choices=("schematic", "pcb")),
                    Param(
                        "pid",
                        "integer",
                        True,
                        description=(
                            "explicit native process ID; normal attachment requires native "
                            "identity; the disabled host-bootstrap mode does not inspect this PID"
                        ),
                    ),
                    Param(
                        "project",
                        "path",
                        description="optionally open this owned project after attachment; "
                        "Layout must have no board open or already have this board open",
                    ),
                    Param(
                        "startup-operation-id",
                        "string",
                        description="Layout only: original successful start ID in this same state; "
                        "revalidate and recover this exact process generation's ownership, "
                        "without opening or activating a project; excludes --project",
                    ),
                    Param(
                        "project-source-operation-id",
                        "string",
                        description="Designer only: successful project create --closed-copy or "
                        "project backup --leave-closed ID in this same state and scope. "
                        "Revalidate the complete source/genesis or closed checkpoint "
                        "and first-open it in the current CLI-owned empty process; requires "
                        "--project, excludes --startup-operation-id and arbitrary takeover",
                    ),
                    Param(
                        "host-bootstrap",
                        "string",
                        choices=("identity",),
                        description="disabled native experiment: returns E_HUMAN_REQUIRED before "
                        "state/OS/COM or token creation; runtime Run is historical, not a "
                        "prerequisite or fallback for dedicated Designer startup",
                    ),
                ),
                extra={"effects": ["session", "persist"], "generic_gate": True},
            ),
            Command(
                "schematic package",
                "schematic:package",
                (
                    "Package an existing schematic for PCB integration; library "
                    "materialization is a separate operation."
                ),
                "schematic_package",
                _write_examples("schematic package --project X.prj"),
                "schematic",
                needs="designer",
                tier="write",
                params=(_project(),),
            ),
            Command(
                "schematic checks result",
                "checks:result",
                (
                    "Read one retained schematic check run; no native checker is executed. "
                    "Stale and incomplete reports remain labelled."
                ),
                "check_result",
                ("xpedition-cli schematic checks result --id <check_run_id> --compact",),
                "inspection",
                params=(Param("id", "string", True),),
                extra={"check_domain": "schematic"},
            ),
            Command(
                "pcb checks result",
                "checks:result",
                (
                    "Read one retained PCB check run, or hash-verify and parse historical native "
                    "files with --native-files. No native checker is executed. Historical parsing "
                    "does not establish native completion, freshness or hazard matching."
                ),
                "check_result",
                (
                    "xpedition-cli pcb checks result --id <check_run_id> --compact",
                    "xpedition-cli pcb checks result --native-files retained.json --compact",
                ),
                "inspection",
                params=(
                    Param(
                        "id",
                        "string",
                        description="Retained check ID; mutually exclusive with --native-files",
                    ),
                    Param(
                        "native-files",
                        "path",
                        description="Historical manifest binding requested rules/elements and "
                        "four output copies by group/role/copy/bytes/sha256; exclusive with --id",
                    ),
                ),
                extra={"check_domain": "pcb"},
            ),
            Command(
                "pcb routing apply",
                "pcb:apply_routing",
                "Apply a file containing a mixed, explicit trace and via plan.",
                table["pcb routing trace"].output_schema,
                _write_examples("pcb routing apply --project X.prj --file routing.json"),
                "routing",
                needs="layout",
                tier="write",
                params=tuple(
                    replace(p, required=True) if p.name == "file" else p
                    for p in base[[c.path for c in base].index("pcb routing trace")].params
                    if p.name in {"project", "file", "geometry", "pace", "dangerous"}
                ),
            ),
            replace(
                _without(table["pcb copper pour"], "margin", "replace"),
                path="pcb copper remove",
                handler="pcb:remove_copper",
                description=(
                    "Reserved existing leaf: copper removal is currently unsupported "
                    "and refused before native writes."
                ),
                examples=_write_examples("pcb copper remove --project X.prj --net GND --layer 2"),
            ),
            Command(
                "bom export",
                "bom:export_file",
                "Write the schematic bill of materials to an explicit JSON file.",
                "bom_export",
                _write_examples("bom export --project X.prj --output bom.json"),
                "fabrication",
                needs="designer",
                tier="write",
                params=(
                    *table["bom inspect"].params,
                    Param("output", "path", True),
                    Param("replace", "boolean"),
                ),
                extra={"effects": ["artifact"], "generic_gate": True},
            ),
            Command(
                "manufacturing verify",
                "manufacturing:verify",
                "Independently verify manifest paths, file sizes and SHA-256 values. "
                "This integrity suite does not provide CAM geometry, DFM "
                "or manufacturing sign-off.",
                "manufacturing_verification",
                (
                    (
                        "xpedition-cli manufacturing verify --package fabrication --suite "
                        "integrity --compact"
                    ),
                ),
                "fabrication",
                params=(
                    Param("package", "path", True),
                    Param("suite", "string", choices=("integrity", "cam", "dfm")),
                ),
                extra={"coverage": ["package-integrity"], "unsupported_suites": ["cam", "dfm"]},
            ),
        ]
    )
    normalized = []
    for command in result:
        if command.path in {"pcb routing via", "pcb routing apply"}:
            command = replace(
                command,
                needs="none",
                description=(
                    "Unsupported in this candidate: the legacy routing path lacks bounded "
                    "copper impact and pre-save protection proof. Refuses before native route/"
                    "regeneration/save calls; use pcb routing trace preview for explicit blockers."
                ),
                blast_radius="Disabled before native routing or persistence; "
                "no blanket unroute or plane-state switching",
            )
        if command.writes:
            examples = tuple(
                example
                if "--dry-run" in example or "--confirm" in example
                else example + " --dry-run"
                for example in command.examples
            )
            if not any("--confirm" in example for example in examples):
                confirm = examples[0].replace("--dry-run", "--confirm <confirm_token>")
                if command.tier == "dangerous" and "--dangerous" not in confirm:
                    confirm += " --dangerous"
                examples = (*examples, confirm)
            if command.path == "session close":
                examples = _write_examples("session close --kind schematic --pid 1234")
            if command.tier == "dangerous":
                examples = tuple(
                    example + " --dangerous" if "--dangerous" not in example else example
                    for example in examples
                )
            normalized.append(
                replace(
                    command,
                    examples=examples,
                    dry_run_schema=command.dry_run_schema
                    or re.sub(r"[^a-z0-9]+", "_", command.path) + "_preview",
                    blast_radius=command.blast_radius
                    if command.blast_radius != "none"
                    else "the explicitly selected application session, output or native checker",
                    extra={"effects": ["change", "persist"], **command.extra},
                )
            )
        else:
            normalized.append(replace(command, extra={"effects": ["observe"], **command.extra}))
    return normalized
