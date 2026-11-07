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
VERIFIED: dict[str, list[str]] = {
    "forward-annotation run": [
        "live:docs/evidence/2026-11-06-051148-forward-annotation-run.json",
        "live:docs/evidence/2026-11-06-051325-forward-annotation-run.json",
        "live:docs/evidence/2026-11-06-051534-forward-annotation-run.json",
        "live:docs/evidence/2026-11-06-051604-forward-annotation-run.json",
        "live:docs/evidence/2026-11-06-051625-forward-annotation-run.json",
        "live:docs/evidence/2026-11-06-053156-forward-annotation-run.json",
        "live:docs/evidence/2026-11-06-053436-forward-annotation-run.json",
        "live:docs/evidence/2026-11-06-054153-forward-annotation-run.json",
        "live:docs/evidence/2026-11-06-060030-forward-annotation-run.json",
        "live:docs/evidence/2026-11-06-160346-forward-annotation-run.json",
    ],
    "packager run": [
        "live:docs/evidence/2026-11-06-044610-packager-run.json",
        "live:docs/evidence/2026-11-06-050946-packager-run.json",
        "live:docs/evidence/2026-11-06-051016-packager-run.json",
        "live:docs/evidence/2026-11-06-160006-packager-run.json",
    ],
    "part create": [
        "live:docs/evidence/2026-11-06-044609-part-create.json",
        "live:docs/evidence/2026-11-06-044855-part-create.json",
        "live:docs/evidence/2026-11-06-073321-part-create.json",
        "live:docs/evidence/2026-11-06-073325-part-create.json",
        "live:docs/evidence/2026-11-06-073331-part-create.json",
        "live:docs/evidence/2026-11-06-160011-part-create.json",
    ],
    "part get": [
        "live:docs/evidence/2026-11-06-044923-part-get.json",
        "live:docs/evidence/2026-11-06-045406-part-get.json",
        "live:docs/evidence/2026-11-06-073333-part-get.json",
    ],
    "part list": [
        "live:docs/evidence/2026-11-06-044837-part-list.json",
        "live:docs/evidence/2026-11-06-045404-part-list.json",
    ],
    "pcb check": [
        "live:docs/evidence/2026-11-06-051209-pcb-check.json",
        "live:docs/evidence/2026-11-06-051524-pcb-check.json",
        "live:docs/evidence/2026-11-06-053207-pcb-check.json",
        "live:docs/evidence/2026-11-06-054206-pcb-check.json",
        "live:docs/evidence/2026-11-06-070334-pcb-check.json",
        "live:docs/evidence/2026-11-06-072359-pcb-check.json",
    ],
    "pcb create": [
        "live:docs/evidence/2026-11-06-044612-pcb-create.json",
        "live:docs/evidence/2026-11-06-051032-pcb-create.json",
        "live:docs/evidence/2026-11-06-051044-pcb-create.json",
        "live:docs/evidence/2026-11-06-073338-pcb-create.json",
        "live:docs/evidence/2026-11-06-073339-pcb-create.json",
        "live:docs/evidence/2026-11-06-073351-pcb-create.json",
        "live:docs/evidence/2026-11-06-160015-pcb-create.json",
    ],
    "pcb export": [
        "live:docs/evidence/2026-11-06-051157-pcb-export.json",
        "live:docs/evidence/2026-11-06-051524-pcb-export.json",
        "live:docs/evidence/2026-11-06-052803-pcb-export.json",
        "live:docs/evidence/2026-11-06-052836-pcb-export.json",
        "live:docs/evidence/2026-11-06-053043-pcb-export.json",
        "live:docs/evidence/2026-11-06-053209-pcb-export.json",
        "live:docs/evidence/2026-11-06-054208-pcb-export.json",
        "live:docs/evidence/2026-11-06-110322-pcb-export.json",
        "live:docs/evidence/2026-11-06-160449-pcb-export.json",
    ],
    "pcb get": [
        "live:docs/evidence/2026-11-06-051200-pcb-get.json",
        "live:docs/evidence/2026-11-06-051524-pcb-get.json",
        "live:docs/evidence/2026-11-06-053205-pcb-get.json",
        "live:docs/evidence/2026-11-06-054204-pcb-get.json",
        "live:docs/evidence/2026-11-06-070324-pcb-get.json",
        "live:docs/evidence/2026-11-06-072404-pcb-get.json",
    ],
    "pcb update": [
        "live:docs/evidence/2026-11-06-051150-pcb-update.json",
        "live:docs/evidence/2026-11-06-051524-pcb-update.json",
        "live:docs/evidence/2026-11-06-051609-pcb-update.json",
        "live:docs/evidence/2026-11-06-051611-pcb-update.json",
        "live:docs/evidence/2026-11-06-052257-pcb-update.json",
        "live:docs/evidence/2026-11-06-052559-pcb-update.json",
        "live:docs/evidence/2026-11-06-053158-pcb-update.json",
        "live:docs/evidence/2026-11-06-054156-pcb-update.json",
        "live:docs/evidence/2026-11-06-072402-pcb-update.json",
        "live:docs/evidence/2026-11-06-160353-pcb-update.json",
    ],
    "placement run": [
        "live:docs/evidence/2026-11-06-051152-placement-run.json",
        "live:docs/evidence/2026-11-06-051523-placement-run.json",
        "live:docs/evidence/2026-11-06-051739-placement-run.json",
        "live:docs/evidence/2026-11-06-053159-placement-run.json",
        "live:docs/evidence/2026-11-06-054158-placement-run.json",
        "live:docs/evidence/2026-11-06-072244-placement-run.json",
        "live:docs/evidence/2026-11-06-072246-placement-run.json",
        "live:docs/evidence/2026-11-06-072249-placement-run.json",
        "live:docs/evidence/2026-11-06-072254-placement-run.json",
        "live:docs/evidence/2026-11-06-160359-placement-run.json",
    ],
    "plane create": [
        "live:docs/evidence/2026-11-06-051154-plane-create.json",
        "live:docs/evidence/2026-11-06-051524-plane-create.json",
        "live:docs/evidence/2026-11-06-051911-plane-create.json",
        "live:docs/evidence/2026-11-06-051913-plane-create.json",
        "live:docs/evidence/2026-11-06-051915-plane-create.json",
        "live:docs/evidence/2026-11-06-051950-plane-create.json",
        "live:docs/evidence/2026-11-06-052601-plane-create.json",
        "live:docs/evidence/2026-11-06-053201-plane-create.json",
        "live:docs/evidence/2026-11-06-054200-plane-create.json",
        "live:docs/evidence/2026-11-06-160408-plane-create.json",
    ],
    "project get": [
        "live:docs/evidence/2026-11-06-044832-project-get.json",
        "live:docs/evidence/2026-11-06-045403-project-get.json",
    ],
    "router run": [
        "live:docs/evidence/2026-11-06-051156-router-run.json",
        "live:docs/evidence/2026-11-06-051524-router-run.json",
        "live:docs/evidence/2026-11-06-053203-router-run.json",
        "live:docs/evidence/2026-11-06-054202-router-run.json",
        "live:docs/evidence/2026-11-06-072314-router-run.json",
        "live:docs/evidence/2026-11-06-072317-router-run.json",
        "live:docs/evidence/2026-11-06-072321-router-run.json",
        "live:docs/evidence/2026-11-06-072323-router-run.json",
        "live:docs/evidence/2026-11-06-072336-router-run.json",
        "live:docs/evidence/2026-11-06-160412-router-run.json",
    ],
    "schematic check": [
        "live:docs/evidence/2026-11-06-045235-schematic-check.json",
        "live:docs/evidence/2026-11-06-045502-schematic-check.json",
        "live:docs/evidence/2026-11-06-053150-schematic-check.json",
        "live:docs/evidence/2026-11-06-054816-schematic-check.json",
        "live:docs/evidence/2026-11-06-073131-schematic-check.json",
        "live:docs/evidence/2026-11-06-073214-schematic-check.json",
    ],
    "schematic create": [
        "live:docs/evidence/2026-11-06-045252-schematic-create.json",
        "live:docs/evidence/2026-11-06-045341-schematic-create.json",
        "live:docs/evidence/2026-11-06-045504-schematic-create.json",
        "live:docs/evidence/2026-11-06-045719-schematic-create.json",
        "live:docs/evidence/2026-11-06-053152-schematic-create.json",
        "live:docs/evidence/2026-11-06-053318-schematic-create.json",
        "live:docs/evidence/2026-11-06-054818-schematic-create.json",
        "live:docs/evidence/2026-11-06-073209-schematic-create.json",
        "live:docs/evidence/2026-11-06-160146-schematic-create.json",
    ],
    "schematic export": [
        "live:docs/evidence/2026-11-06-045254-schematic-export.json",
        "live:docs/evidence/2026-11-06-045412-schematic-export.json",
        "live:docs/evidence/2026-11-06-045650-schematic-export.json",
        "live:docs/evidence/2026-11-06-050648-schematic-export.json",
        "live:docs/evidence/2026-11-06-050746-schematic-export.json",
        "live:docs/evidence/2026-11-06-053154-schematic-export.json",
        "live:docs/evidence/2026-11-06-054820-schematic-export.json",
        "live:docs/evidence/2026-11-06-073136-schematic-export.json",
        "live:docs/evidence/2026-11-06-160150-schematic-export.json",
    ],
    "schematic get": [
        "live:docs/evidence/2026-11-06-045231-schematic-get.json",
        "live:docs/evidence/2026-11-06-045459-schematic-get.json",
        "live:docs/evidence/2026-11-06-050547-schematic-get.json",
        "live:docs/evidence/2026-11-06-050641-schematic-get.json",
        "live:docs/evidence/2026-11-06-053148-schematic-get.json",
        "live:docs/evidence/2026-11-06-054815-schematic-get.json",
    ],
}

# The evaluation report in which every question passed on Xpedition 2604 (DSKL-043).
EVALUATION = "eval:docs/evidence/eval-2026-11-06.json"

# Verified commands that an evaluation question lists and the Agent ran there (DSKL-043).
STABLE = {
    "forward-annotation run",
    "packager run",
    "part create",
    "part get",
    "part list",
    "pcb check",
    "pcb create",
    "pcb export",
    "pcb get",
    "pcb update",
    "placement run",
    "plane create",
    "project get",
    "router run",
    "schematic check",
    "schematic create",
    "schematic export",
    "schematic get",
}

# The standard commands whose behavior matrix passed in full on Xpedition 2604 (DVER-040).
STANDARD_MATURITY: dict[str, dict[str, Any]] = {
    "doc close": {"maturity": "stable", "evidence": [
        "live:docs/evidence/2026-11-06-051223-doc-close.json",
        "live:docs/evidence/2026-11-06-051326-doc-close.json",
        "live:docs/evidence/2026-11-06-051627-doc-close.json",
        "live:docs/evidence/2026-11-06-053143-doc-close.json",
        "live:docs/evidence/2026-11-06-054210-doc-close.json",
        "live:docs/evidence/2026-11-06-072116-doc-close.json",
        "live:docs/evidence/2026-11-06-100443-doc-close.json",
        "live:docs/evidence/2026-11-06-100729-doc-close.json",
        "live:docs/evidence/2026-11-06-160422-doc-close.json",
        EVALUATION,
    ]},
    "doc create": {"maturity": "stable", "evidence": [
        "live:docs/evidence/2026-11-06-072719-doc-create.json",
        "live:docs/evidence/2026-11-06-072721-doc-create.json",
        "live:docs/evidence/2026-11-06-072724-doc-create.json",
        "live:docs/evidence/2026-11-06-072743-doc-create.json",
        "live:docs/evidence/2026-11-06-072807-doc-create.json",
        "live:docs/evidence/2026-11-06-072853-doc-create.json",
        "live:docs/evidence/2026-11-06-073032-doc-create.json",
        "live:docs/evidence/2026-11-06-100710-doc-create.json",
        "live:docs/evidence/2026-11-06-160153-doc-create.json",
        EVALUATION,
    ]},
    "doc get": {"maturity": "stable", "evidence": [
        "live:docs/evidence/2026-11-06-044532-doc-get.json",
        "live:docs/evidence/2026-11-06-045401-doc-get.json",
        "live:docs/evidence/2026-11-06-045650-doc-get.json",
        "live:docs/evidence/2026-11-06-054838-doc-get.json",
        EVALUATION,
    ]},
    "doc list": {"maturity": "verified", "evidence": [
        "live:docs/evidence/2026-11-06-044539-doc-list.json",
        "live:docs/evidence/2026-11-06-045408-doc-list.json",
        "live:docs/evidence/2026-11-06-045522-doc-list.json",
        "live:docs/evidence/2026-11-06-053211-doc-list.json",
        "live:docs/evidence/2026-11-06-054824-doc-list.json",
    ]},
    "doc open": {"maturity": "stable", "evidence": [
        "live:docs/evidence/2026-11-06-051222-doc-open.json",
        "live:docs/evidence/2026-11-06-051328-doc-open.json",
        "live:docs/evidence/2026-11-06-051524-doc-open.json",
        "live:docs/evidence/2026-11-06-051630-doc-open.json",
        "live:docs/evidence/2026-11-06-053147-doc-open.json",
        "live:docs/evidence/2026-11-06-054212-doc-open.json",
        "live:docs/evidence/2026-11-06-072212-doc-open.json",
        "live:docs/evidence/2026-11-06-100812-doc-open.json",
        "live:docs/evidence/2026-11-06-160524-doc-open.json",
        EVALUATION,
    ]},
    "doc save": {"maturity": "stable", "evidence": [
        "live:docs/evidence/2026-11-06-051252-doc-save.json",
        "live:docs/evidence/2026-11-06-051330-doc-save.json",
        "live:docs/evidence/2026-11-06-051524-doc-save.json",
        "live:docs/evidence/2026-11-06-052347-doc-save.json",
        "live:docs/evidence/2026-11-06-052443-doc-save.json",
        "live:docs/evidence/2026-11-06-053145-doc-save.json",
        "live:docs/evidence/2026-11-06-054214-doc-save.json",
        "live:docs/evidence/2026-11-06-100337-doc-save.json",
        "live:docs/evidence/2026-11-06-160419-doc-save.json",
        EVALUATION,
    ]},
    "session close": {"maturity": "stable", "evidence": [
        "live:docs/evidence/2026-11-06-044700-session-close.json",
        "live:docs/evidence/2026-11-06-045414-session-close.json",
        "live:docs/evidence/2026-11-06-045650-session-close.json",
        "live:docs/evidence/2026-11-06-053140-session-close.json",
        "live:docs/evidence/2026-11-06-054217-session-close.json",
        "live:docs/evidence/2026-11-06-100837-session-close.json",
        "live:docs/evidence/2026-11-06-160205-session-close.json",
        EVALUATION,
    ]},
    "session get": {"maturity": "verified", "evidence": [
        "live:docs/evidence/2026-11-06-044507-session-get.json",
        "live:docs/evidence/2026-11-06-045456-session-get.json",
        "live:docs/evidence/2026-11-06-053138-session-get.json",
        "live:docs/evidence/2026-11-06-054219-session-get.json",
    ]},
    "session list": {"maturity": "stable", "evidence": [
        "live:docs/evidence/2026-11-06-044529-session-list.json",
        "live:docs/evidence/2026-11-06-045458-session-list.json",
        "live:docs/evidence/2026-11-06-054221-session-list.json",
        EVALUATION,
    ]},
    "session open": {"maturity": "stable", "evidence": [
        "live:docs/evidence/2026-11-06-044635-session-open.json",
        "live:docs/evidence/2026-11-06-045010-session-open.json",
        "live:docs/evidence/2026-11-06-073450-session-open.json",
        "live:docs/evidence/2026-11-06-160051-session-open.json",
        EVALUATION,
    ]},
}


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
        "plane create", "Pour a plane shape for one net on one layer, inside the board outline.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="plane",
        flags=[{"name": "--net", "type": "string", "required": True, "summary": "The net, such as GND."},
               {"name": "--layer", "type": "integer", "required": True, "summary": "The conductor layer number, 1 at the top."},
               {"name": "--margin", "type": "quantity", "quantity": "length", "summary": "The distance from the board outline; 1 mm by default."}],
        output=obj(net=STR, layer=INT, margin_mm=NUM, generated=BOOL),
        examples=["xpedition-cli plane create --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --net GND --layer 2"],
        knowledge=['layout-plane-draft-state', 'layout-pour-after-routing'],
    ),
    command(
        "router run", "Route every open net with Layout's autorouter passes; runs as a long job.",
        effects=["change"], modes=["session"], kinds=["board"], target_kind="net", long=True,
        flags=[{"name": "--layers", "type": "string", "summary": "The conductor layers to route on, such as 1,4; all by default."}],
        output=obj(nets=INT, routed=INT, open=arr(STR), traces=INT, vias=INT, passes=arr(STR), skipped_passes=arr(STR),
                   planes_regenerated=INT),
        untrusted=["open"],
        examples=["xpedition-cli router run --session layout-6312 --doc D:/work/demo/PCB/Demo.pcb --layers 1,4"],
        knowledge=['layout-fine-pitch-clearance', 'layout-via-near-pad', 'layout-trace-widths-through-cm'],
    ),
    command(
        "pcb get", "Read the open board: counts, layer stack, net classes and widths, geometry and layout metrics.",
        effects=["observe"], modes=["session"], kinds=["board"], target_kind="pcb",
        flags=[{"name": "--output", "type": "path", "output": True,
                "summary": "Write the whole result to this JSON file; the response keeps the summary."}],
        output=obj(path=STR, layers=INT, components=OBJ, nets=OBJ, traces=INT, vias=INT,
                   outline=obj(width_mm=NUM, height_mm=NUM, radius_mm=NUM, extents_mm=arr(NUM)), stackup=arr(OBJ),
                   net_classes=arr(obj(name=STR, nets=INT, widths=OBJ)), keepouts=INT,
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
        knowledge=['layout-trace-widths-through-cm'],
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
        knowledge=['layout-trace-widths-through-cm'],
    ),
]
