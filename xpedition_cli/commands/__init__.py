"""The domain commands of xpedition-cli, as registrations (DCLI-029).

Each command is one dict. The runtime checks every field when the tool starts and refuses a
registration that breaks a rule, so a mistake shows up at once. A command needs:

- ``path``: ``noun verb`` in lowercase words, the noun taken from Xpedition's object model
- ``summary``: one line
- ``effects``: what it changes (DCLI-051); the runtime derives the confirmation and risk tiers
- ``modes``, ``target_kind``, ``flags`` (with a unit for every physical quantity), ``untrusted_fields``
- ``doc_kinds``: the kind of document it works on, ``project`` or ``board`` (DCLI-210)
- ``long``: the runtime runs it as a long job in its own worker (DCLI-065)
- ``irreversible``, ``halt``, ``tolerances``, ``default_fields``, ``related_knowledge``
- ``output_schema`` and ``examples``; ``maturity`` and ``evidence`` come from ``VERIFIED`` and ``STABLE``

Every command also gets a behavior matrix in tests/matrix/; its ``normal`` row is the write
sample (DVER-002, DVER-030). Run ``python scripts/spec.py generate`` after a change.

The document of a file-mode command is the project file (``.prj``, kind ``project``). In session
mode the document is the project Designer has open, or the board (``.pcb``, kind ``board``)
Layout has open.
"""
from __future__ import annotations

from typing import Any

OBJ: dict[str, Any] = {"type": "object"}
STR: dict[str, Any] = {"type": "string"}
NUM: dict[str, Any] = {"type": "number"}
INT: dict[str, Any] = {"type": "integer"}
BOOL: dict[str, Any] = {"type": "boolean"}


def arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def obj(**props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props}


FINDING = obj(id=STR, severity=STR, rule=STR, message=STR, objects=arr(STR))
# the length fields of a rules file (constraint get and constraint import), compared in mm
RULE_WIDTH_FIELDS = ("width_mm", "min_width_mm", "max_width_mm", "differential_spacing_mm")
RULE_CLEARANCE_FIELDS = ("trace_to_trace_mm", "trace_to_pad_mm", "trace_to_via_mm", "trace_to_plane_mm", "pad_to_pad_mm",
                         "pad_to_via_mm", "via_to_via_mm", "via_to_plane_mm")
BOARD_COORDINATES = ("Board coordinates in mm: the origin is the board origin, the lower left corner of the outline pcb update "
                     "draws; x to the right, y up, seen from the top side.")

# The commands whose behavior matrix passed in full on Xpedition 2604, and the live records of
# those runs (DVER-040, DVER-041).
VERIFIED: dict[str, list[str]] = {}

# The evaluation report in which every question passed on Xpedition 2604 (DSKL-043).
EVALUATION = "eval:docs/evidence/eval-2026-11-06.json"

# Verified commands that an evaluation question lists and the Agent ran there (DSKL-043).
STABLE: set[str] = set()

# The standard commands whose behavior matrix passed in full on Xpedition 2604 (DVER-040).
STANDARD_MATURITY: dict[str, dict[str, Any]] = {}


def command(path: str, summary: str, *, effects: list[str], modes: list[str], target_kind: str,
            flags: list[dict[str, Any]] | None = None, output: dict[str, Any],
            examples: list[str], untrusted: list[str] | None = None, irreversible: bool = False,
            kinds: list[str], long: bool = False, tolerances: dict[str, float] | None = None,
            default_fields: list[str] | None = None, knowledge: list[str] | None = None,
            coordinates: str | None = None) -> dict[str, Any]:
    reg: dict[str, Any] = {
        "path": path,
        "summary": summary,
        "effects": effects,
        "modes": modes,
        "doc_kinds": kinds,
        "target_kind": target_kind,
        "flags": flags or [],
        "untrusted_fields": untrusted or [],
        "irreversible": irreversible,
        "halt": False,
        "tolerances": tolerances or {},
        "default_fields": default_fields or [],
        "related_knowledge": knowledge or [],
        "output_schema": output,
        "examples": examples,
        "maturity": "stable" if path in STABLE else "verified" if path in VERIFIED else "draft",
        "evidence": [*VERIFIED.get(path, []), *([EVALUATION] if path in STABLE else [])],
    }
    if long:
        reg["long"] = True
    if coordinates:
        reg["output_coordinate_system"] = coordinates
    return reg


COMMANDS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------ project
    command(
        "project get", "Read a project file: its designs, board, central library and library lists.",
        effects=["observe"], modes=["file"], kinds=["project"], target_kind="project",
        output=obj(path=STR, xpedition_version=STR, central_library=obj(path=STR, exists=BOOL),
                   designs=arr(obj(name=STR, kind=STR, board=STR, board_exists=BOOL, layout_template=STR)),
                   board=STR, symbol_libraries=arr(STR), parts_databases=arr(STR), cell_libraries=arr(STR),
                   ascii_path=BOOL),
        untrusted=["designs", "xpedition_version", "central_library", "board", "symbol_libraries", "parts_databases",
                   "cell_libraries"],
        examples=["xpedition-cli project get --doc D:/work/demo/Demo.prj"],
    ),
    # ------------------------------------------------------------------ central library
    command(
        "part list", "List the parts of the project's central library.",
        effects=["observe"], modes=["file"], kinds=["project"], target_kind="part",
        output=obj(items=arr(obj(id=STR, partition=STR, description=STR, prefix=STR, type=STR, cell=STR,
                                 symbols=arr(STR), pins=INT, value=STR)), total=INT),
        untrusted=["id", "partition", "description", "prefix", "type", "cell", "symbols", "value"],
        default_fields=["id", "partition", "description", "cell", "pins"],
        examples=["xpedition-cli part list --doc D:/work/demo/Demo.prj --limit 20"],
        knowledge=['library-converter-rules'],
    ),
    command(
        "part get", "Read one part: its pin map, symbols, footprint cell, padstacks and the problems found.",
        effects=["observe"], modes=["file"], kinds=["project"], target_kind="part",
        flags=[{"name": "--id", "type": "string", "required": True, "summary": "The part number."}],
        output=obj(id=STR, partition=STR, description=STR, prefix=STR, value=STR, properties=OBJ,
                   pins=arr(obj(symbol=STR, name=STR, number=STR)),
                   symbols=arr(obj(reference=STR, partition=STR, name=STR, kind=STR, device=STR, version=INT,
                                   pins=arr(obj(number=STR, name=STR, type=STR, x_mm=NUM, y_mm=NUM, body_x_mm=NUM,
                                                body_y_mm=NUM, side=STR)),
                                   bbox_mm=arr(NUM))),
                   cell=obj(name=STR, partition=STR, kind=STR, group=STR, mount=STR, layers=INT, description=STR,
                            height_mm=NUM, pins=arr(obj(number=STR, x_mm=NUM, y_mm=NUM, padstack=STR, rotation_deg=NUM)),
                            holes=arr(OBJ), placement=arr(obj(kind=STR, points_mm=arr(arr(NUM)), width_mm=NUM))),
                   padstacks=OBJ, also_in=arr(STR), findings=arr(FINDING)),
        untrusted=["id", "partition", "description", "prefix", "value", "properties", "pins", "symbols", "cell", "padstacks",
                   "also_in", "findings"],
        coordinates=("Symbol pins and box: mm from the symbol origin, x to the right, y up. Cell pins, holes and placement "
                     "outline: mm from the cell origin, x to the right, y up, seen from the top side. Padstack sizes are "
                     "lengths, not coordinates."),
        examples=["xpedition-cli part get --doc D:/work/demo/Demo.prj --id RES-10K-0402"],
        knowledge=['library-converter-rules', 'cell-pin-count-must-match'],
    ),
    command(
        "part create", "Add parts to the central library from a parts file: symbol, IPC-7351B footprint, padstacks and pin map; "
        "runs as a long job.",
        effects=["change"], modes=["file"], kinds=["project"], target_kind="part", long=True,
        flags=[{"name": "--file", "type": "path", "required": True,
                "summary": "The parts file (JSON). A part identical to the library's is skipped; a part number the library holds with other content is refused."}],
        output=obj(partition=STR, created=arr(STR), skipped=arr(STR), symbols=arr(STR), cells=arr(STR), padstacks=arr(STR)),
        examples=["xpedition-cli part create --doc D:/work/demo/Demo.prj --file D:/work/demo/parts.json"],
        knowledge=['library-converter-rules', 'library-part-value-is-numeric', 'cell-pin-count-must-match', 'xpedition-ascii-paths-only'],
    ),
    command(
        "padstack list", "List the padstacks of the project's central library, with their type, pad and hole sizes.",
        effects=["observe"], modes=["file"], kinds=["project"], target_kind="padstack",
        flags=[{"name": "--type", "type": "string", "summary": "Only padstacks of this type: via, smd, through or mounting_hole."}],
        output=obj(items=arr(obj(id=STR, type=STR, pad_shape=STR, pad_mm=NUM, pad_height_mm=NUM, drill_mm=NUM, plated=BOOL,
                                 mask_mm=NUM)), total=INT),
        untrusted=["id", "type", "pad_shape"],
        default_fields=["id", "type", "pad_mm", "drill_mm", "mask_mm"],
        examples=["xpedition-cli padstack list --doc D:/work/demo/Demo.prj --type via"],
        knowledge=['library-converter-rules'],
    ),
    command(
        "padstack create", "Add a via padstack to the central library: a plated hole, a round pad on every conductor layer, and "
        "a soldermask opening or none (tented); runs as a long job.",
        effects=["change"], modes=["file"], kinds=["project"], target_kind="padstack", long=True,
        flags=[{"name": "--drill", "type": "quantity", "quantity": "length", "required": True, "summary": "The finished hole diameter."},
               {"name": "--pad", "type": "quantity", "quantity": "length", "required": True,
                "summary": "The pad diameter on every conductor layer; wider than the hole."},
               {"name": "--mask", "type": "quantity", "quantity": "length",
                "summary": "The soldermask opening's diameter; without it the via is tented, covered by soldermask or coverlay."},
               {"name": "--name", "type": "string",
                "summary": "The padstack's name; VIA_D<drill>_P<pad> in mm by default, such as VIA_D0.2_P0.4."}],
        output=obj(name=STR, type=STR, pad_mm=NUM, drill_mm=NUM, plated=BOOL, mask_mm=NUM, created=BOOL),
        tolerances={"pad_mm": 0.0005, "drill_mm": 0.0005, "mask_mm": 0.0005},
        examples=["xpedition-cli padstack create --doc D:/work/demo/Demo.prj --drill 0.2mm --pad 0.4mm"],
        knowledge=['library-converter-rules'],
    ),
    command(
        "packager run", "Package the schematic for Layout with Xpedition's packager and report its errors and warnings; runs as a long job.",
        effects=["change"], modes=["file"], kinds=["project"], target_kind="project", long=True,
        output=obj(packaged=BOOL, design=STR, errors=arr(STR), warnings=INT, verdict=STR, log=STR),
        untrusted=["errors", "verdict"],
        examples=["xpedition-cli packager run --doc D:/work/demo/Demo.prj"],
        knowledge=['prj-cell-library-list', 'cell-pin-count-must-match'],
    ),
    # ------------------------------------------------------------------ schematic (Designer)
    command(
        "schematic get", "Read the open schematic: its sheets and the numbers of components and nets.",
        effects=["observe"], modes=["session"], kinds=["project"], target_kind="schematic",
        output=obj(design=STR, sheets=arr(obj(number=INT, name=STR, size=STR)), components=INT, nets=INT),
        untrusted=["design", "sheets"],
        examples=["xpedition-cli schematic get --session designer-4120 --doc D:/work/demo/Demo.prj"],
        knowledge=['designer-reopen-after-sheet-changes'],
    ),
    command(
        "schematic create", "Draw the whole schematic from a design file, replacing every sheet it names; runs as a long job.",
        effects=["change", "persist", "artifact"], modes=["session"], kinds=["project"], target_kind="schematic", long=True,
        flags=[{"name": "--design", "type": "path", "required": True,
                "summary": "The design file (JSON): sheets, parts by central-library part number, and the pin of every connection."}],
        output=obj(sheets=arr(INT), parts=INT, nets=INT, operations=INT, symbols_written=arr(STR), warnings=arr(STR)),
        untrusted=["warnings"], irreversible=True,
        examples=["xpedition-cli schematic create --session designer-4120 --doc D:/work/demo/Demo.prj --design D:/work/demo/design.json"],
        knowledge=['designer-power-symbols', 'designer-pin-on-wire-corner', 'designer-symbol-cache', 'designer-reopen-after-sheet-changes', 'designer-one-instance-only'],
    ),
    command(
        "schematic check", "Run Designer's verification and this tool's netlist rules, and list the problems found; runs as a "
        "long job.",
        effects=["observe"], modes=["session"], kinds=["project"], target_kind="schematic", long=True,
        output=obj(findings=arr(FINDING), errors=INT, warnings=INT, complete=BOOL, logs=arr(STR)),
        untrusted=["findings"],
        examples=["xpedition-cli schematic check --session designer-4120 --doc D:/work/demo/Demo.prj"],
        knowledge=['designer-pin-types-and-erc', 'designer-reopen-after-sheet-changes'],
    ),
    command(
        "schematic export", "Export the schematic to a PDF with Xpedition's sch2pdf.",
        effects=["artifact"], modes=["file", "session"], kinds=["project"], target_kind="schematic",
        flags=[{"name": "--output", "type": "path", "required": True, "output": True, "summary": "The PDF file to write."}],
        output=obj(path=STR, pages=arr(obj(sheet=STR, width_mm=NUM, height_mm=NUM)), warnings=arr(STR)),
        untrusted=["pages", "warnings"],
        examples=["xpedition-cli schematic export --doc D:/work/demo/Demo.prj --output D:/work/demo/out/Demo.pdf"],
        knowledge=['designer-chinese-text-in-pdf'],
    ),
    # ------------------------------------------------------------------ board (Layout)
    command(
        "pcb create", "Create the project's board from a layout template with Xpedition's JobWizard; runs as a long job.",
        effects=["change", "persist", "artifact"], modes=["file"], kinds=["project"], target_kind="pcb", long=True,
        flags=[{"name": "--template", "type": "string", "summary": "The layout template's name; 4 Layer Template by default."},
               {"name": "--name", "type": "string", "summary": "The board's name: letters, digits, - and _; the project's name by default."}],
        output=obj(path=STR, template=STR, files=INT, cells_registered=arr(STR)),
        untrusted=["cells_registered"],
        examples=["xpedition-cli pcb create --doc D:/work/demo/Demo.prj"],
        knowledge=['prj-cell-library-list'],
    ),
    command(
        "forward-annotation run", "Bring the packaged schematic into the open board; runs as a long job.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="pcb", long=True,
        output=obj(outcome=STR, components=INT, nets=INT, pins=INT, errors=arr(STR), warnings=arr(STR)),
        untrusted=["errors", "warnings"],
        examples=["xpedition-cli forward-annotation run --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb"],
        knowledge=['forward-annotation-in-synch', 'forward-annotation-keeps-old-cells', 'layout-open-prompts'],
    ),
    command(
        "pcb update", "Replace the board outline by a rectangle from the origin; the route border and manufacturing outline follow.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="pcb",
        flags=[{"name": "--width", "type": "quantity", "quantity": "length", "required": True, "summary": "The board's width."},
               {"name": "--height", "type": "quantity", "quantity": "length", "required": True, "summary": "The board's height."},
               {"name": "--radius", "type": "quantity", "quantity": "length",
                "summary": "The corner radius, 0 for square corners; the board's current radius by default."}],
        output=obj(width_mm=NUM, height_mm=NUM, radius_mm=NUM, route_border_inset_mm=NUM),
        tolerances={"width_mm": 0.001, "height_mm": 0.001, "radius_mm": 0.001},
        examples=["xpedition-cli pcb update --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --width 70mm --height 48mm --radius 3mm"],
    ),
    command(
        "placement run", "Place every component of the board from its connections, grouped by schematic sheet; runs as a long job.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="component", long=True,
        flags=[{"name": "--design", "type": "path", "summary": "The design file the schematic was drawn from; its sheets and zones group the parts."},
               {"name": "--all", "type": "boolean", "summary": "Place the parts already placed again too."}],
        output=obj(placed=INT, clusters=arr(obj(name=STR, parts=arr(STR))), outside=arr(STR), labels=arr(STR),
                   routing_removed=OBJ,
                   components=arr(obj(id=STR, x_mm=NUM, y_mm=NUM, rotation_deg=NUM)), components_total=INT),
        untrusted=["clusters", "outside", "labels", "components"],
        tolerances={"x_mm": 0.001, "y_mm": 0.001, "rotation_deg": 0.01},
        coordinates=BOARD_COORDINATES + " A component's position is its cell origin.",
        examples=["xpedition-cli placement run --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --design D:/work/demo/design.json"],
        knowledge=['layout-placement-drc', 'layout-unplaced-parts-invisible'],
    ),
    command(
        "plane create", "Pour a plane shape for one net on one layer, inside the board outline: solid, or hatched.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="plane",
        flags=[{"name": "--net", "type": "string", "required": True, "summary": "The net, such as GND."},
               {"name": "--layer", "type": "integer", "required": True, "summary": "The conductor layer number, 1 at the top."},
               {"name": "--margin", "type": "quantity", "quantity": "length", "summary": "The distance from a rectangular board outline; 1 mm by default. On any other outline the plane follows the route border."},
               {"name": "--hatch", "type": "string",
                "summary": "solid (the default), orthogonal or diagonal: a hatched plane, common on flex, is a grid of lines."},
               {"name": "--hatch-width", "type": "quantity", "quantity": "length", "summary": "A hatched plane's line width."},
               {"name": "--hatch-distance", "type": "quantity", "quantity": "length",
                "summary": "A hatched plane's distance from line to line, larger than the width."}],
        output=obj(net=STR, layer=INT, within=STR, hatch=STR, generated=BOOL),
        examples=["xpedition-cli plane create --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --net GND --layer 2",
                  "xpedition-cli plane create --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --net GND --layer 2 --hatch orthogonal "
                  "--hatch-width 0.15mm --hatch-distance 0.5mm"],
        knowledge=['layout-plane-draft-state', 'layout-pour-after-routing', 'layout-plane-hatch'],
    ),
    command(
        "router run", "Route the open nets with Layout's autorouter passes: every net, or only the nets and net classes named; "
        "runs as a long job.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="net", long=True,
        flags=[{"name": "--layers", "type": "string", "summary": "The conductor layers to route on, such as 1,4; all by default."},
               {"name": "--nets", "type": "string", "summary": "Route only these nets, such as SS_TX1_P,SS_TX1_N."},
               {"name": "--net-classes", "type": "string", "summary": "Route only the nets of these net classes, such as USB_SS."},
               {"name": "--passes", "type": "string",
                "summary": "The autorouter passes in order, each with its effort 1 to 5, such as fanout:1,route:1-5,smooth; "
                           "route:1-5,viamin:1-3,smooth:1-3 by default. Passes: fanout, route, novia, viamin, smooth, spread, "
                           "expand, removehangers."}],
        output=obj(nets=INT, routed=INT, open=arr(STR), traces=INT, vias=INT, passes=arr(STR), skipped_passes=arr(STR),
                   planes_regenerated=INT, selected=obj(nets=INT, routed=INT, open=arr(STR))),
        untrusted=["open", "selected"],
        examples=["xpedition-cli router run --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --layers 1,4",
                  "xpedition-cli router run --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --net-classes USB_SS --passes fanout:1,route:1-5"],
        knowledge=['layout-fine-pitch-clearance', 'layout-via-near-pad', 'layout-trace-widths-through-cm'],
    ),
    command(
        "pcb get", "Read the open board: counts, layer stack, net classes with their widths and vias, geometry and layout metrics.",
        effects=["observe"], modes=["session"], kinds=["board"], target_kind="pcb",
        flags=[{"name": "--output", "type": "path", "output": True,
                "summary": "Write the whole result to this JSON file; the response keeps the summary."}],
        output=obj(path=STR, layers=INT, components=OBJ, nets=OBJ, traces=INT, vias=INT,
                   outline=obj(width_mm=NUM, height_mm=NUM, radius_mm=NUM, extents_mm=arr(NUM)), stackup=arr(OBJ),
                   net_classes=arr(obj(name=STR, nets=INT, widths=OBJ, vias=OBJ)), keepouts=INT,
                   geometry=obj(available=BOOL, reason=STR, outline_mm=arr(arr(NUM)), counts=OBJ), metrics=OBJ, output=STR),
        untrusted=["stackup", "net_classes", "metrics"],
        coordinates=BOARD_COORDINATES + " The net classes' widths are per conductor layer: minimum_mm, typical_mm and expansion_mm.",
        examples=["xpedition-cli pcb get --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --fields layers,nets,metrics"],
        knowledge=['layout-trace-widths-through-cm'],
    ),
    command(
        "pcb check", "Run Layout's Batch DRC and the board rules it does not cover, and list every hazard; runs as a long job.",
        effects=["observe"], modes=["session"], kinds=["board"], target_kind="pcb", long=True,
        flags=[{"name": "--output", "type": "path", "output": True,
                "summary": "Write the whole result to this JSON file; the response keeps the summary."}],
        output=obj(hazards=arr(obj(type=STR, severity=STR, description=STR, x_mm=NUM, y_mm=NUM, objects=arr(STR),
                                   actual_mm=NUM, required_mm=NUM)),
                   rules=arr(FINDING), errors=INT, warnings=INT, hazard_count=INT, rule_count=INT, drc=OBJ, output=STR),
        untrusted=["hazards", "rules"],
        coordinates=BOARD_COORDINATES,
        examples=["xpedition-cli pcb check --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb"],
        knowledge=['layout-fine-pitch-clearance', 'layout-via-near-pad'],
    ),
    command(
        "pcb export", "Write the fabrication package: ODB++, Gerber, NC drill, a centroid file, a BOM and a manifest; runs as a long job.",
        effects=["artifact", "persist"], modes=["session"], kinds=["board"], target_kind="pcb", long=True,
        flags=[{"name": "--output", "type": "path", "required": True, "output": True,
                "summary": "The folder for the package; it must not exist or must be empty."}],
        output=obj(path=STR, file_count=INT, manifest=STR, board=obj(width_mm=NUM, height_mm=NUM, layers=INT), problems=arr(STR),
                   setups_patched=arr(STR), setups_rewritten=arr(STR)),
        untrusted=["problems"],
        examples=["xpedition-cli pcb export --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --output D:/work/demo/fab"],
        knowledge=['layout-output-setups', 'layout-drillprefs-held'],
    ),
    command(
        "trace create", "Draw the traces and place the vias of a routing plan, after checking it against the board's copper, the "
        "net classes' widths and the clearance rules; runs as a long job.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="trace", long=True,
        flags=[{"name": "--file", "type": "path", "required": True,
                "summary": "The routing plan (JSON): traces with net, layer, width_mm and points_mm, and vias with net, at_mm and padstack, "
                           "in board coordinates."}],
        output=obj(traces=INT, vias=INT, nets=arr(obj(net=STR, routed=BOOL, opens=INT))),
        coordinates=BOARD_COORDINATES,
        examples=["xpedition-cli trace create --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --file D:/work/demo/route.json"],
        knowledge=['layout-via-near-pad', 'layout-trace-widths-through-cm'],
    ),
    command(
        "trace delete", "Delete the traces of the nets named, on every layer or on the layers named; their vias stay.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="trace",
        flags=[{"name": "--nets", "type": "string", "required": True, "summary": "The nets whose traces go, such as SS_TX1_P,SS_TX1_N."},
               {"name": "--layers", "type": "string", "summary": "Only on these conductor layers, such as 1,4; every layer by default."}],
        output=obj(deleted=INT),
        examples=["xpedition-cli trace delete --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --nets SS_TX1_P,SS_TX1_N"],
    ),
    command(
        "via delete", "Delete the vias of the nets named: routing vias and stitching vias alike.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="via",
        flags=[{"name": "--nets", "type": "string", "required": True, "summary": "The nets whose vias go, such as GND."}],
        output=obj(deleted=INT),
        examples=["xpedition-cli via delete --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --nets GND"],
    ),
    command(
        "plane delete", "Delete the plane shapes of one net on one layer, and with them its copper there.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="plane",
        flags=[{"name": "--net", "type": "string", "required": True, "summary": "The net, such as GND."},
               {"name": "--layer", "type": "integer", "required": True, "summary": "The conductor layer number, 1 at the top."}],
        output=obj(net=STR, layer=INT, deleted=INT),
        examples=["xpedition-cli plane delete --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --net GND --layer 2"],
    ),
    command(
        "via create", "Place vias of one net: one at --x, --y, or a stitching grid at --pitch inside the net's copper on two "
        "layers, clear of other copper by the default clearance rule; runs as a long job.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="via", long=True,
        flags=[{"name": "--net", "type": "string", "required": True, "summary": "The net, such as GND."},
               {"name": "--x", "type": "quantity", "quantity": "length", "coordinate_system": BOARD_COORDINATES,
                "summary": "One via's X position."},
               {"name": "--y", "type": "quantity", "quantity": "length", "coordinate_system": BOARD_COORDINATES,
                "summary": "One via's Y position."},
               {"name": "--pitch", "type": "quantity", "quantity": "length", "summary": "The stitching grid's pitch, such as 2mm."},
               {"name": "--margin", "type": "quantity", "quantity": "length",
                "summary": "The grid's distance from the board outline; 0.5 mm by default."},
               {"name": "--padstack", "type": "string",
                "summary": "The via padstack; the via the net's class is assigned by default (pcb get lists it)."}],
        output=obj(net=STR, padstack=STR, placed=INT, points_mm=arr(arr(NUM)), dropped=OBJ),
        coordinates=BOARD_COORDINATES,
        examples=["xpedition-cli via create --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --net GND --pitch 2mm",
                  "xpedition-cli via create --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --net GND --x 12.5mm --y 8mm"],
        knowledge=['layout-via-near-pad', 'layout-router-via-from-net-class'],
    ),
    command(
        "via update", "Change the vias of the nets named, or of the nets of the net classes named, to another via padstack, "
        "pulled from the central library when the board lacks it.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="via",
        flags=[{"name": "--nets", "type": "string", "summary": "The nets whose vias change, such as SS_TX1_P,SS_TX1_N."},
               {"name": "--net-classes", "type": "string", "summary": "The net classes whose nets' vias change, such as USB_SS."},
               {"name": "--padstack", "type": "string", "required": True,
                "summary": "The via padstack, such as VIA_D0.2_P0.4; padstack list --type via lists the central library's."}],
        output=obj(padstack=STR, changed=INT, pulled=BOOL),
        examples=["xpedition-cli via update --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --net-classes USB_SS --padstack VIA_D0.2_P0.4"],
        knowledge=['layout-router-via-from-net-class'],
    ),
    command(
        "solder-mask create", "Open the soldermask, or the coverlay of a flex board, over a rectangle on one side, so the copper "
        "under it is bare.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="opening",
        flags=[{"name": "--x1", "type": "quantity", "quantity": "length", "required": True, "coordinate_system": BOARD_COORDINATES,
                "summary": "One corner's X."},
               {"name": "--y1", "type": "quantity", "quantity": "length", "required": True, "coordinate_system": BOARD_COORDINATES,
                "summary": "One corner's Y."},
               {"name": "--x2", "type": "quantity", "quantity": "length", "required": True, "coordinate_system": BOARD_COORDINATES,
                "summary": "The opposite corner's X."},
               {"name": "--y2", "type": "quantity", "quantity": "length", "required": True, "coordinate_system": BOARD_COORDINATES,
                "summary": "The opposite corner's Y."},
               {"name": "--side", "type": "string", "summary": "top (the default) or bottom."},
               {"name": "--cover-layer", "type": "boolean", "summary": "Open the coverlay of a flex board instead of the soldermask."}],
        output=obj(kind=STR, side=STR, created=BOOL, rect_mm=arr(NUM), nets=arr(STR)),
        untrusted=["nets"],
        coordinates=BOARD_COORDINATES,
        examples=["xpedition-cli solder-mask create --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --x1 40mm --y1 2mm --x2 50mm "
                  "--y2 6mm --cover-layer"],
        knowledge=['layout-solder-mask-openings'],
    ),
    command(
        "solder-mask delete", "Delete the soldermask or coverlay opening that contains a point on one side.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="opening",
        flags=[{"name": "--x", "type": "quantity", "quantity": "length", "required": True, "coordinate_system": BOARD_COORDINATES,
                "summary": "A point inside the opening: its X."},
               {"name": "--y", "type": "quantity", "quantity": "length", "required": True, "coordinate_system": BOARD_COORDINATES,
                "summary": "Its Y."},
               {"name": "--side", "type": "string", "summary": "top (the default) or bottom."},
               {"name": "--cover-layer", "type": "boolean", "summary": "The opening is in the coverlay, not the soldermask."}],
        output=obj(kind=STR, side=STR, rect_mm=arr(NUM)),
        coordinates=BOARD_COORDINATES,
        examples=["xpedition-cli solder-mask delete --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --x 45mm --y 4mm --cover-layer"],
    ),
    command(
        "pcb render", "Draw the board as a PNG from its geometry: outline, pads, traces, vias, plane shapes, holes and soldermask "
        "openings, seen from the top or the bottom; runs as a long job.",
        effects=["artifact"], modes=["session"], kinds=["board"], target_kind="file", long=True,
        flags=[{"name": "--output", "type": "path", "required": True, "output": True, "summary": "The .png file to write."},
               {"name": "--side", "type": "string", "summary": "top (the default) or bottom, which is drawn mirrored left to right."},
               {"name": "--layers", "type": "string", "summary": "The conductor layers to draw, such as 1,2; every layer by default."},
               {"name": "--resolution", "type": "number", "summary": "Pixels per mm, 1 to 200; 20 by default, lowered to keep the picture "
                "within 4000 pixels."}],
        output=obj(path=STR, side=STR, layers=arr(INT), width_px=INT, height_px=INT, pads=INT, traces=INT, vias=INT, planes=INT,
                   openings=INT),
        examples=["xpedition-cli pcb render --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --output D:/work/demo/board.png"],
    ),
    # ------------------------------------------------------------------ constraints (Layout)
    command(
        "constraint get", "Read the board's constraints: net classes and their nets, trace widths and differential spacing per "
        "layer, differential pairs, clearance rules and which class pairs use them.",
        effects=["observe"], modes=["session"], kinds=["board"], target_kind="constraint",
        flags=[{"name": "--output", "type": "path", "output": True,
                "summary": "Write every rule to this file, in the rules file format constraint import reads; the response keeps the summary."}],
        output=obj(layers=INT, nets=INT, net_class_count=INT,
                   net_classes=arr(obj(name=STR, nets=INT, layers=OBJ)), differential_pairs=arr(STR), clearance_rules=arr(STR),
                   class_clearances=arr(obj(**{"from": STR, "to": STR, "rule": STR})), loaded=BOOL, output=STR),
        untrusted=["net_classes", "differential_pairs", "clearance_rules", "class_clearances"],
        examples=["xpedition-cli constraint get --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --output D:/work/demo/rules.json"],
        knowledge=['layout-trace-widths-through-cm', 'layout-impedance-from-fab-table'],
    ),
    command(
        "constraint import", "Create or change the net classes, trace widths, differential spacing, differential pairs and "
        "clearance rules a rules file names, then load them into Layout; rules the file does not name stay as they are.",
        effects=["change", "persist"], modes=["session"], kinds=["board"], target_kind="constraint", long=True,
        flags=[{"name": "--file", "type": "path", "required": True,
                "summary": "The rules file (JSON), in the format constraint get writes; it names only what to create or change."}],
        output=obj(changes=INT, net_classes_created=arr(STR), nets_moved=INT, differential_pairs_created=arr(STR),
                   clearance_rules_created=arr(STR), loaded=BOOL),
        untrusted=["net_classes_created", "differential_pairs_created", "clearance_rules_created"],
        tolerances={name: 0.0005 for name in (*RULE_WIDTH_FIELDS, *RULE_CLEARANCE_FIELDS)},
        examples=["xpedition-cli constraint import --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --file D:/work/demo/rules.json"],
        knowledge=['layout-trace-widths-through-cm', 'layout-impedance-from-fab-table'],
    ),
]
