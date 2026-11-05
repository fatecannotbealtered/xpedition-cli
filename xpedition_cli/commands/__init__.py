"""The domain commands of xpedition-cli, as registrations (DCLI-029).

Each command is one dict. The runtime checks every field when the tool starts and refuses a
registration that breaks a rule, so a mistake shows up at once. A command needs:

- ``path``: ``noun verb`` in lowercase words, the noun taken from Xpedition's object model
- ``summary``: one line
- ``effects``: what it changes (DCLI-051); the runtime derives the confirmation and risk tiers
- ``modes``, ``target_kind``, ``flags`` (with a unit for every physical quantity), ``untrusted_fields``
- ``adapter_capabilities``: the adapter methods it calls (DCLI-260)
- ``survey_ids``: the capability IDs of docs/APPLICATION.md it provides (DREPO-061)
- ``irreversible``, ``halt``, ``tolerances``, ``default_fields``, ``related_knowledge``
- ``output_schema``, ``examples``, ``maturity`` and ``evidence``

Every command also gets a behavior matrix in tests/matrix/; its ``normal`` row is the write
sample (DVER-002, DVER-030). Run ``python scripts/spec.py generate`` after a change.

The document of a file-mode command is the project file (``.prj``). In session mode the document
is the project Designer has open, or the board (``.pcb``) Layout has open.
"""
from __future__ import annotations

from typing import Any

READ = ["read"]
WRITE = ["preview", "apply", "read_back"]

LIMIT = {"name": "--limit", "type": "integer", "summary": "Items per page; 50 by default, at most 500."}
OFFSET = {"name": "--offset", "type": "integer", "summary": "Start position of the page."}

OBJ: dict[str, Any] = {"type": "object"}
STR: dict[str, Any] = {"type": "string"}
NUM: dict[str, Any] = {"type": "number"}
INT: dict[str, Any] = {"type": "integer"}
BOOL: dict[str, Any] = {"type": "boolean"}


def arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def obj(**props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props}


FILES = arr(obj(path=STR, bytes=INT, sha256=STR))
FINDING = obj(id=STR, severity=STR, rule=STR, message=STR, objects=arr(STR))


def command(path: str, summary: str, *, effects: list[str], modes: list[str], target_kind: str,
            flags: list[dict[str, Any]] | None = None, survey_ids: list[str], output: dict[str, Any],
            examples: list[str], untrusted: list[str] | None = None, irreversible: bool = False,
            tolerances: dict[str, float] | None = None, default_fields: list[str] | None = None,
            knowledge: list[str] | None = None) -> dict[str, Any]:
    writes = effects != ["observe"]
    return {
        "path": path,
        "summary": summary,
        "effects": effects,
        "modes": modes,
        "target_kind": target_kind,
        "flags": flags or [],
        "untrusted_fields": untrusted or [],
        "adapter_capabilities": WRITE if writes else READ,
        "survey_ids": survey_ids,
        "irreversible": irreversible,
        "halt": False,
        "tolerances": tolerances or {},
        "default_fields": default_fields or [],
        "related_knowledge": knowledge or [],
        "output_schema": output,
        "examples": examples,
        "maturity": "draft",
        "evidence": [],
    }


COMMANDS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------ project
    command(
        "project get", "Read a project file: its designs, board, central library and library lists.",
        effects=["observe"], modes=["file"], target_kind="project", survey_ids=["CAP-001"],
        output=obj(path=STR, xpedition_version=STR, central_library=obj(path=STR, exists=BOOL),
                   designs=arr(obj(name=STR, kind=STR, board=STR, board_exists=BOOL, layout_template=STR)),
                   board=STR, symbol_libraries=arr(STR), parts_databases=arr(STR), cell_libraries=arr(STR),
                   ascii_path=BOOL),
        untrusted=["designs", "xpedition_version"],
        examples=["xpedition-cli project get --doc D:/work/demo/Demo.prj"],
    ),
    command(
        "project create", "Create a project by copying the template project given with --doc.",
        effects=["artifact"], modes=["file"], target_kind="project", survey_ids=["CAP-002"],
        flags=[{"name": "--output", "type": "path", "required": True, "output": True,
                "summary": "The new project's .prj file; its folder must not exist or must be empty, and its path must be ASCII."}],
        output=obj(path=STR, template=STR, files=INT, bytes=INT, rewritten=arr(obj(key=STR, value=STR)),
                   parts_databases=obj(created=arr(STR), registered=arr(STR))),
        examples=["xpedition-cli project create --doc D:/templates/Template/Template.prj --output D:/work/demo/Demo.prj"],
        knowledge=['xpedition-ascii-paths-only', 'xpedition-template-library-is-empty'],
    ),
    # ------------------------------------------------------------------ central library
    command(
        "part list", "List the parts of the project's central library.",
        effects=["observe"], modes=["file"], target_kind="part", survey_ids=["CAP-010"], flags=[LIMIT, OFFSET],
        output=obj(items=arr(obj(id=STR, partition=STR, description=STR, prefix=STR, type=STR, cell=STR,
                                 symbols=arr(STR), pins=INT, value=STR)), total=INT),
        untrusted=["description", "value"], default_fields=["id", "partition", "description", "cell", "pins"],
        examples=["xpedition-cli part list --doc D:/work/demo/Demo.prj --limit 20"],
        knowledge=['library-converter-rules'],
    ),
    command(
        "part get", "Read one part: its pin map, symbols, footprint cell, padstacks and the problems found.",
        effects=["observe"], modes=["file"], target_kind="part", survey_ids=["CAP-011"],
        flags=[{"name": "--id", "type": "string", "required": True, "summary": "The part number."}],
        output=obj(id=STR, partition=STR, description=STR, prefix=STR, value=STR, properties=OBJ,
                   pins=arr(obj(symbol=STR, name=STR, number=STR)), symbols=arr(OBJ), cell=OBJ, padstacks=OBJ,
                   also_in=arr(STR), findings=arr(FINDING)),
        untrusted=["description", "value", "properties"],
        examples=["xpedition-cli part get --doc D:/work/demo/Demo.prj --id RES-10K-0402"],
        knowledge=['library-converter-rules', 'cell-pin-count-must-match'],
    ),
    command(
        "part create", "Add parts to the central library from a parts file: symbol, IPC-7351B footprint, padstacks and pin map.",
        effects=["change"], modes=["file"], target_kind="part", survey_ids=["CAP-015"],
        flags=[{"name": "--file", "type": "path", "required": True,
                "summary": "The parts file (JSON). A part identical to the library's is skipped; a part number the library holds with other content is refused."}],
        output=obj(partition=STR, created=arr(STR), skipped=arr(STR), symbols=arr(STR), cells=arr(STR), padstacks=arr(STR)),
        examples=["xpedition-cli part create --doc D:/work/demo/Demo.prj --file D:/work/demo/parts.json"],
        knowledge=['library-converter-rules', 'library-part-value-is-numeric', 'cell-pin-count-must-match', 'xpedition-ascii-paths-only'],
    ),
    command(
        "packager run", "Package the schematic for Layout with Xpedition's packager and report its errors and warnings.",
        effects=["change"], modes=["file"], target_kind="project", survey_ids=["CAP-043"],
        output=obj(packaged=BOOL, design=STR, errors=arr(STR), warnings=INT, verdict=STR, log=STR),
        untrusted=["errors", "verdict"],
        examples=["xpedition-cli packager run --doc D:/work/demo/Demo.prj"],
        knowledge=['prj-cell-library-list', 'cell-pin-count-must-match'],
    ),
    # ------------------------------------------------------------------ schematic (Designer)
    command(
        "schematic get", "Read the open schematic: its sheets and the numbers of components and nets.",
        effects=["observe"], modes=["session"], target_kind="schematic", survey_ids=["CAP-030"],
        output=obj(design=STR, sheets=arr(obj(number=INT, name=STR, size=STR)), components=INT, nets=INT),
        untrusted=["design", "sheets"],
        examples=["xpedition-cli schematic get --session designer-4120 --doc D:/work/demo/Demo.prj"],
        knowledge=['designer-reopen-after-sheet-changes'],
    ),
    command(
        "schematic create", "Draw the whole schematic from a design file, replacing every sheet it names; runs as a long job.",
        effects=["change", "persist"], modes=["session"], target_kind="schematic", survey_ids=["CAP-034"],
        flags=[{"name": "--design", "type": "path", "required": True,
                "summary": "The design file (JSON): sheets, parts by central-library part number, and the pin of every connection."}],
        output=obj(sheets=arr(INT), parts=INT, nets=INT, operations=INT, symbols_written=arr(STR), warnings=arr(STR)),
        untrusted=["warnings"], irreversible=True,
        examples=["xpedition-cli schematic create --session designer-4120 --doc D:/work/demo/Demo.prj --design D:/work/demo/design.json"],
        knowledge=['designer-power-symbols', 'designer-pin-on-wire-corner', 'designer-symbol-cache', 'designer-reopen-after-sheet-changes', 'designer-one-instance-only'],
    ),
    command(
        "schematic check", "Run Designer's verification and this tool's netlist rules, and list the problems found.",
        effects=["observe"], modes=["session"], target_kind="schematic", survey_ids=["CAP-041"],
        output=obj(findings=arr(FINDING), errors=INT, warnings=INT, complete=BOOL, logs=OBJ),
        untrusted=["findings"],
        examples=["xpedition-cli schematic check --session designer-4120 --doc D:/work/demo/Demo.prj"],
        knowledge=['designer-pin-types-and-erc', 'designer-reopen-after-sheet-changes'],
    ),
    command(
        "schematic export", "Export the schematic to a PDF with Xpedition's sch2pdf.",
        effects=["artifact"], modes=["file", "session"], target_kind="schematic", survey_ids=["CAP-042"],
        flags=[{"name": "--output", "type": "path", "required": True, "output": True, "summary": "The PDF file to write."}],
        output=obj(path=STR, pages=arr(obj(sheet=STR, width_mm=NUM, height_mm=NUM)), warnings=arr(STR)),
        untrusted=["pages", "warnings"],
        examples=["xpedition-cli schematic export --doc D:/work/demo/Demo.prj --output D:/work/demo/out/Demo.pdf"],
        knowledge=['designer-chinese-text-in-pdf'],
    ),
    # ------------------------------------------------------------------ board (Layout)
    command(
        "pcb create", "Create the project's board from a layout template with Xpedition's JobWizard.",
        effects=["artifact", "persist"], modes=["file"], target_kind="pcb", survey_ids=["CAP-060"],
        flags=[{"name": "--template", "type": "string", "summary": "The layout template's name; 4 Layer Template by default."},
               {"name": "--name", "type": "string", "summary": "The board's name: letters, digits, - and _; the project's name by default."}],
        output=obj(path=STR, template=STR, files=INT, cells_registered=arr(STR)),
        examples=["xpedition-cli pcb create --doc D:/work/demo/Demo.prj"],
        knowledge=['prj-cell-library-list'],
    ),
    command(
        "forward-annotation run", "Bring the packaged schematic into the open board; runs as a long job.",
        effects=["change"], modes=["session"], target_kind="pcb", survey_ids=["CAP-062"],
        output=obj(outcome=STR, components=INT, nets=INT, pins=INT, errors=arr(STR), warnings=arr(STR)),
        untrusted=["errors", "warnings"],
        examples=["xpedition-cli forward-annotation run --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb"],
        knowledge=['forward-annotation-in-synch', 'forward-annotation-keeps-old-cells', 'layout-open-prompts'],
    ),
    command(
        "pcb update", "Replace the board outline by a rectangle from the origin; the route border and manufacturing outline follow.",
        effects=["change"], modes=["session"], target_kind="pcb", survey_ids=["CAP-065"],
        flags=[{"name": "--width", "type": "quantity", "quantity": "length", "summary": "The board's width."},
               {"name": "--height", "type": "quantity", "quantity": "length", "summary": "The board's height."},
               {"name": "--radius", "type": "quantity", "quantity": "length", "summary": "The corner radius; 0 for square corners."}],
        output=obj(width_mm=NUM, height_mm=NUM, radius_mm=NUM, route_border_inset_mm=NUM),
        tolerances={"width_mm": 0.001, "height_mm": 0.001, "radius_mm": 0.001},
        examples=["xpedition-cli pcb update --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --width 70mm --height 48mm --radius 3mm"],
    ),
    command(
        "placement run", "Place every component of the board from its connections, grouped by schematic sheet.",
        effects=["change"], modes=["session"], target_kind="component", survey_ids=["CAP-068"],
        flags=[{"name": "--design", "type": "path", "summary": "The design file the schematic was drawn from; its sheets and zones group the parts."},
               {"name": "--all", "type": "boolean", "summary": "Place the parts already placed again too."}],
        output=obj(placed=INT, clusters=arr(obj(name=STR, parts=arr(STR))), outside=arr(STR), labels=arr(STR),
                   routing_removed=OBJ),
        untrusted=["clusters", "outside", "labels"],
        tolerances={"x_mm": 0.001, "y_mm": 0.001, "rotation_deg": 0.01},
        examples=["xpedition-cli placement run --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --design D:/work/demo/design.json"],
        knowledge=['layout-placement-drc', 'layout-unplaced-parts-invisible'],
    ),
    command(
        "plane create", "Pour a plane shape for one net on one layer, inside the board outline.",
        effects=["change"], modes=["session"], target_kind="plane", survey_ids=["CAP-072"],
        flags=[{"name": "--net", "type": "string", "required": True, "summary": "The net, such as GND."},
               {"name": "--layer", "type": "integer", "required": True, "summary": "The conductor layer number, 1 at the top."},
               {"name": "--margin", "type": "quantity", "quantity": "length", "summary": "The distance from the board outline; 1 mm by default."}],
        output=obj(net=STR, layer=INT, margin_mm=NUM, generated=BOOL),
        examples=["xpedition-cli plane create --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --net GND --layer 2"],
        knowledge=['layout-plane-draft-state', 'layout-pour-after-routing'],
    ),
    command(
        "router run", "Route every open net with Layout's autorouter passes.",
        effects=["change"], modes=["session"], target_kind="net", survey_ids=["CAP-073"],
        flags=[{"name": "--layers", "type": "string", "summary": "The conductor layers to route on, such as 1,4; all by default."}],
        output=obj(nets=INT, routed=INT, open=arr(STR), traces=INT, vias=INT, passes=arr(STR), skipped_passes=arr(STR),
                   planes_regenerated=INT),
        untrusted=["open"],
        examples=["xpedition-cli router run --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --layers 1,4"],
        knowledge=['layout-fine-pitch-clearance', 'layout-via-near-pad', 'layout-trace-widths-through-cm'],
    ),
    command(
        "pcb get", "Read the open board: counts, layer stack, net classes and widths, geometry and layout metrics.",
        effects=["observe"], modes=["session"], target_kind="pcb", survey_ids=["CAP-063", "CAP-064", "CAP-078"],
        flags=[{"name": "--output", "type": "path", "output": True,
                "summary": "Write the whole result to this JSON file; the response keeps the summary."}],
        output=obj(path=STR, layers=INT, components=OBJ, nets=OBJ, traces=INT, vias=INT, outline=OBJ, stackup=arr(OBJ),
                   net_classes=arr(OBJ), keepouts=INT, geometry=OBJ, metrics=OBJ, output=STR),
        untrusted=["stackup", "net_classes", "metrics"],
        examples=["xpedition-cli pcb get --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --fields layers,nets,metrics"],
        knowledge=['layout-trace-widths-through-cm'],
    ),
    command(
        "pcb check", "Run Layout's Batch DRC and the board rules it does not cover, and list every hazard.",
        effects=["observe"], modes=["session"], target_kind="pcb", survey_ids=["CAP-077", "CAP-078"],
        flags=[{"name": "--output", "type": "path", "output": True,
                "summary": "Write the whole result to this JSON file; the response keeps the summary."}],
        output=obj(hazards=arr(obj(type=STR, severity=STR, description=STR, x_mm=NUM, y_mm=NUM, objects=arr(STR),
                                   actual_mm=NUM, required_mm=NUM)),
                   rules=arr(FINDING), errors=INT, warnings=INT, hazard_count=INT, rule_count=INT, drc=OBJ, output=STR),
        untrusted=["hazards", "rules"],
        examples=["xpedition-cli pcb check --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb"],
        knowledge=['layout-fine-pitch-clearance', 'layout-via-near-pad'],
    ),
    command(
        "pcb export", "Write the fabrication package: ODB++, Gerber, NC drill, a centroid file, a BOM and a manifest; runs as a long job.",
        effects=["artifact", "persist"], modes=["session"], target_kind="pcb", survey_ids=["CAP-081"],
        flags=[{"name": "--output", "type": "path", "required": True, "output": True,
                "summary": "The folder for the package; it must not exist or must be empty."}],
        output=obj(path=STR, files=FILES, board=obj(width_mm=NUM, height_mm=NUM, layers=INT), problems=arr(STR),
                   setups_patched=arr(STR), setups_rewritten=arr(STR)),
        untrusted=["problems"],
        examples=["xpedition-cli pcb export --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --output D:/work/demo/fab"],
        knowledge=['layout-output-setups', 'layout-drillprefs-held'],
    ),
]
