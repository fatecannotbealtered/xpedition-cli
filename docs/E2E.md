# End-to-end verification

Native E2E requires a disposable project in a licensed Windows Xpedition
environment. The first smoke run must perform only this sequence:

1. Start Xpedition Designer with a temporary project.
2. Place `R1` and `C1`.
3. Create the `3V3` net and connect `R1.1` to `C1.1`.
4. Save, close, reopen, and read the snapshot through the native adapter.
5. Compare component, net, connection, coordinate and property data.

Never use a production design for this test.

## Recorded run — Designer, 2026-09-12

All five steps passed against Xpedition Standard `XPED2604` on Windows 11,
driven entirely through the COM adapter.

| Step | Evidence |
|---|---|
| 1 | Attached to a running `Viewdraw.Application`; project `RcSmoke.prj`, design `Board1`, sheet `Schematic1.1`. |
| 2 | `AddPartInstance` placed `Resistors:R` at (3000, 3000) as `R1` and `Capacitors:C` at (4000, 3000) as `C1`. |
| 3 | `apply_changeset` with a `connect` operation created the wire and labelled it `3V3`. |
| 4 | `ActiveDocument.Save()`, `CloseProject()`, `OpenProject()`, `Documents.Open(design)`, then `snapshot`. |
| 5 | Reread matched exactly. |

```json
{
  "components": {"R1": [3000, 3000], "C1": [4000, 3000]},
  "nets": ["3V3", "GND"],
  "connections": [{"net": "3V3", "pins": ["C1.1", "R1.1"]}]
}
```

R1 and C1 coordinates matched before and after the reopen, `3V3` survived, and
it still connected `C1.1` and `R1.1`.

Two things about this run are worth stating plainly:

- The `R` and `C` symbols were **authored as plain ASCII** into a writable copy
  of the stock library, because the installation ships no component library at
  all (see [`COMPATIBILITY.md`](COMPATIBILITY.md)). That is a valid exercise of
  the automation path — placement, connection, persistence and read-back all
  went through the product — but it is not a production library, and the cells
  behind those symbols are not real footprints.
- The run covers **Designer only**. At that point Layout reads and writes had not
  been exercised against a real board, so `release_readiness.level` was `beta`.
  The runs below closed that gap.

## Recorded run — a four-sheet example schematic, 2026-09-12

A full sheet drawn under the xpedition-schematic Skill's drawing conventions, on the same
installation and the same hand-built library:

| Item | Evidence |
|---|---|
| Sheet | `Schematic1.1` wiped with Select All + `DeleteSelected`; B border (1700 × 1100 units) kept |
| Parts | 26 placed through `AddPartInstance` with symbols from `xpedition_cli.symbols`; every part inside the border, no two bounding boxes closer than 30 units |
| Connections | 70 without failure: labelled stubs, four short wires, 20 stubs onto `Globals:gnd`, 16 stubs onto generated type-4 power symbols, 5 `No_Connect` marks |
| Read-back | 18 nets (`VBUS`, `VBAT`, `+5V`, `+3V3`, `GND` and 13 signals) matched the intended netlist pin for pin |
| PDF | `sch2pdf` rendered the sheet; every part, label, symbol and note visible |

The placement and wiring engine that produced it is still a probe script; the
per-pin stub-and-label, power-symbol and no-connect steps are not ChangeSet
operations yet.

## Recorded run — a four-sheet example through `schematic draw`, 2026-09-12

A four-sheet example design planned and drawn end to end on the same
installation:

| Item | Evidence |
|---|---|
| Plan | 4 A4 sheets (overview plus three function sheets), 26 parts, 15 named nets, 2 bare junctions, 223 operations, no DS-07/DS-08 issues |
| Draw | 223 operations applied in 128 s: sheets wiped, sized to A4, parts placed with horizontal value and refdes text, ladders and chains wired, labels boxed, no-connects marked, titles, notes and footers written |
| Read-back | after the automatic project reopen, all 15 nets matched pin for pin and both bare junctions were found on one net each |
| Appearance | pin names inside the IC bodies, pin numbers outside, values beside vertical parts — the layout of a design review draft |

## Recorded run — the same design through the CLI front door, 2026-09-12

`xpedition-cli schematic draw --dry-run` planned the design (26 parts, 4 sheets,
no issues) and issued a confirm token; `--confirm` drew it through the adapter
subprocess in 130 s with all 15 nets and both junctions matching; `schematic
show --sheet 2 --output sheet2.png` activated the sheet, fitted it, brought the
Designer window to the front and captured it. Nothing in that loop runs outside
the package.

## Recorded run — a demo project end to end, 2026-09-12

`project init --backend native_xpedition --template RcSmoke.prj --project
<projects>/demo-board/DemoBoard.prj`
copied the smoke project (266 files, 3.3 MB, without backups, logs and layout
templates), renamed the `.prj`, rewrote `CentralLibrary` and `DBCFile` into the
copy and opened it. The same clone under a Chinese-named folder opened and read
back fine but could not place any new symbol (`Symbol Case:NTC_… not found,
empty or a block`), before and after a reopen; the ASCII copy placed it at once.
`schematic draw` of a four-sheet demo design (4 A4 sheets, 29
parts, 83 pins, 264 operations, NTC and MOSFET symbols) took 138 s and read back
all 15 nets and the one junction as planned, after one fix: two ground pins on a
sensor's bottom edge shared a bar whose ground symbol sat on the corner where
stub and bar met, and Designer left that symbol unconnected. `review run`
reported 9 findings, all Designer ERC `drc-BI-GROUND/POWER` on battery,
thermistor, diode and MOSFET pins, and no CLI rule findings; `bom export` 29
rows; `schematic unconnected` none. A blank design of four empty sheets then
wiped the project in 63 s, ready for the recorded demo.

After a comparison with a one-page reference board the design
gained pull-ups on both open-drain lines, 33 Ω series resistors towards the
host connector, six test points, two mounting holes (pinless symbols, placed
and listed in the BOM) and thermal and interface notes: 41 parts, 309
operations, 165 s, all 17 nets and the junction matching. With the passive,
MOSFET and test-point pins typed `ANALOG`, `review run` reported nothing at all,
from Designer's ERC or from the CLI rules.

## Recorded run — from the schematic to a board, 2026-09-14

The demo project went on from the drawn schematic to a board
without a production central library, all through the CLI:

| Step | Evidence |
|---|---|
| `library build --confirm --package` | 9 padstacks merged into `Layout/PadstackDB.psk`, 11 cells into `CellDBLibs/PartQuest.cel`, 29 parts into `PartsDBLibs/PartQuest.pdb` (the part number is the value string), `.prj` gains the parts and cell partitions; packager: "Packaging has been successfully tested with no errors or warnings" |
| `pcb create --confirm` | `JobWizard -createnew` copied 58 files of the 4-layer template into `PCB/DemoBoard.pcb` in 4 s; `.prj` carries `PCBDesignPath` and `LayoutTemplate` |
| `pcb annotate --confirm` | Layout's Project Integration ran packager, Database Load and netload in 30 s: "18 nets were found containing 95 pins", "39 components were found", "Forward-Annotation … successfully completed" |
| `pcb info --project X.prj` | 39 components, 9 footprints, 18 nets; `session open --kind layout --project X.prj` opens the board from the `.prj` |
| A second `pcb annotate` | `outcome: in_synch`, nothing run |
| `pcb arrange --dry-run` | 39 parts measured by placing and unplacing each (2.7 s): 3 sheet groups (7, 17, 15 parts), all rows inside the 50.8 mm outline, digest bound to the token |
| `pcb arrange --confirm` | plan recomputed with the same digest, 39 `Component.Place` calls and a save in 7.6 s; every `read_back` position equals the plan; `pcb components` reports them placed on the top side in millimetres |
| `pcb show` | the board window to the front under `Loc: All On`, three other schemes selected by name through the toolbar combo, a PNG capture showing the three bands, pads and ratsnest |

The board that looked empty to the user after forward annotation now shows every
part in rows by sheet: unplaced components are not drawn, and Layout's own
"Placed" count on the component navigator was the only sign of them.

## Recorded run — a first layout, 2026-09-14

The same board taken from rows to something a reviewer recognises, through the
CLI, after the user compared it with a finished KiCad board:

| Step | Evidence |
|---|---|
| `pcb outline --width 45 --height 30` | outline, route border (0.3 mm inside) and manufacturing outline replaced; `pcb show` fits the view to it |
| `pcb arrange --design <example>.json` | 4 clusters (regulator with its capacitors, MCU with its 8 resistors and filter capacitors, temperature sensor with its capacitor, charger with the MOSFETs and dividers), battery and host connectors turned by 90° on the side edges, 6 test points along the bottom, zone labels `MCU / TEMP SENSOR` and `POWER / TEST` on the top silkscreen; 39 parts placed in 14 s, no overlaps, nothing outside |
| `pcb pour --net GND --layer 2` | one plane shape, inset 1 mm, on layer 2 |
| Two dry runs | the same digest, after the planner was made independent of the order Layout lists components in |

## Recorded run — routed, 2026-09-14

The whole board again from the template, because the MCU's placeholder cell had
to change (a 0.65 mm pitch is unroutable under the stock 0.254 mm rules) and
forward annotation never swaps the cell of an existing part:

| Step | Evidence |
|---|---|
| `library build --confirm --package` | `CLI_SOIC10` for the 10-pin MCU, packaged clean |
| `pcb create --replace` | board closed in Layout, `PCB` folder removed, keys cleared, 58 template files copied again |
| `pcb annotate` | 39 components, 18 nets after the retry the first call needs on a fresh board |
| `pcb outline`, `pcb arrange --design`, `pcb pour` | 45 × 30 mm, 4 clusters, GND plane on layer 2 — as before |
| `pcb route` | Route 1–5: 18 of 18 nets, 0 opens, 128 traces, 57 vias in 0.6 s; Via Min: 41 vias; Smooth: 114 traces; `complete: true` |
| `pcb info` | 39 components, 18 nets, 114 tracks, 41 vias |

Before the cell change the same passes left 10 nets open around the MCU: the
router cannot reach 0.4 mm pads 0.25 mm apart with a 0.254 mm clearance rule,
and the rules are read-only through automation.

## Recorded run — checked, 2026-09-14

| Step | Evidence |
|---|---|
| `pcb drc` (first run) | 59 batch hazards: 18 Proximity (dual-row placeholder pads overlapping, required 0.254 mm, actual 0), 16 PartialNets and 12 Dangling (GND vias with no generated plane), 13 ViasUnderParts |
| library fix, `pcb create --replace`, `pcb annotate` | pads turned across the row; annotation at the first attempt, 42 s, once the answerer thread stopped ending the packager's progress box |
| `pcb outline`, `pcb arrange --design`, `pcb pour`, `pcb route` | as before; the pour now generates the plane (`generated_planes` 1); Route 1–5: 18 of 18 nets, 121 traces, 39 vias after Via Min and Smooth |
| `pcb drc` after regenerating the plane | 14 hazards, all ViasUnderParts (vias under the IC bodies, a design choice); no proximity, no partial net, nothing dangling |

The Batch DRC is Layout's own, reached through the menu command
`Gui.ProcessCommand(32769)` with its dialog answered from the helper thread;
its driver refuses a command line.

Three Layout facts cost a run each: an unplaced part has no pins in `Net.Pins`
(the first plan saw 16 of 22 ground pins and clustered a connector with the
regulator), a part placed onto another one or onto its own old footprint is a
DRC violation even with `RespectComponentPlacementDRC` false (so the parts are
lifted first), and two identical footprints on one spot are refused while a
part outside the outline is accepted (so the survey parks unplaced parts in a
grid below the board).

Two library facts came out of the first failed annotation: the project file
needs `LIST 2dCellLibraries` (Database Load found no cells without it) and a
placeholder cell must carry exactly the part's pin count (a 4-pin SOIC on the
3-pin regulator was refused; 3-pin parts now default to SOT-23 and odd counts
are no longer padded).

Opening the board twice through a killed Layout exercised the prompts the
adapter now answers by itself: the stale design-status question, the database
recovery box and the offer to forward-annotate.

## Recorded run: the board on KiCad footprints (2026-09-14, evening)

Same project and schematic; the placeholder cells replaced by KiCad's footprints.

1. `python -m xpedition_cli.kicad_import --project DemoBoard.prj` converted all 155
   `.pretty` libraries (15 450 files) into 152 cell partitions holding 15 113 cells:
   about a quarter of an hour of converter runs, 5–8 s per library (three libraries
   hold no front-side footprint). The run produced the converter's rules: cell names
   over 64 characters (685 footprints) or with parentheses (37) are refused, a
   `roundrect` pad with ratio 0 has to be a rectangle (radius 0 is refused), and a
   polyline written on one line of about 1 500 characters crashes `HKP2CellDB`
   (0xC0000409), so points go one per line, as the library's own exports write them.
2. `library build --design <example>-kicad.json --package`: 26 parts on
   13 KiCad cells from 9 partitions, no cells of its own; the 9 partitions were
   registered in `LIST 2dCellLibraries` (14 s).
3. `pcb create --replace` (13 s) and `pcb annotate` (44 s): `annotated`, 39 parts,
   18 nets.
4. `pcb outline --width 55 --height 40`: the JST connectors and SOIC bodies do not
   fit the 45 × 30 board of the placeholder run; the first plan listed 7 parts
   `outside`, and applying it stopped at a placement DRC violation.
5. `pcb arrange --design … --all` (`--all` because the failed apply had left half of
   the parts placed): 39 parts in four clusters, none outside (12 s).
6. `pcb pour --net GND --layer 2` and `pcb route`: 18 of 18 nets, 111 traces, 33 vias
   (11 s).
7. `pcb drc`: 10 hazards, all vias under the SOIC bodies. The first pass had reported
   11 partial GND connections and 7 dangling vias: the GND plane was back in Draft
   after the pour had been saved, and `pcb route` only regenerated Dynamic planes.
   It now generates a Draft one too.
8. `pcb show`: black while the desktop is locked. The capture taken before the last
   rebuild showed the footprints' own 1 mm reference designators, silkscreen outlines
   and pin-1 marks where the placeholder rectangles and the oversized default
   designators had been.

## Recorded run: the board made presentable (2026-09-14, night)

Same board on KiCad footprints, taken from "routed" to "reads like a hand layout",
every step through the CLI (`--dry-run`, then `--confirm`):

1. `pcb outline --width 70 --height 48 --radius 3`: rounded corners; the dry run of
   `pcb arrange` sized the board (60 × 45 held the parts, not the room their
   designators and one-pitch rows need; 65 × 50 still put the last cluster into a third
   row; 70 × 48 takes the sheet-3 clusters in one row).
2. `pcb holes --replace`: four non-plated 2.2 mm holes 3.5 mm from the corners
   (`MH-C2.2-NONPLATED` from the central library), the previous ones removed because
   the outline had grown.
3. `pcb arrange --design … --all`: 39 parts, four clusters, none outside, rows and
   columns on one 0.5 mm-grid pitch, room above every part for its designator,
   connectors turned inward on the side edges, 7 mm corners kept clear. The first
   attempt stopped with a placement DRC violation on a connector: a placed part is
   measured as it stands, so a connector at 90° came back 5.5 × 10.9 mm and the plan
   turned it again — the survey now unturns what it measures.
4. `pcb pour --net GND --layer 2 --replace`: the plane follows the rounded outline
   (radius 2 after the 1 mm margin); the old rectangle had stuck out past the corners.
5. `pcb route --layers 1,4 --unroute`: 18 of 18 nets, 104 traces, 29 vias; 91 traces on
   the top layer, 11 on the bottom, 2 still on layer 3 (`LayerSelect` takes the inner
   layers only, and the router kept two short pieces there).
6. `pcb drc`: **no hazards**.
7. `pcb show --output board_kicad.png` and `--scheme "Loc: Placement"` for the clean
   placement view: rounded board, holes in the corners, connectors on the edges,
   resistor rows aligned, labels readable, zone names above their rows.

Facts that cost a run each are in `COMPATIBILITY.md` ("Layout facts from the
placement and routing round"): `LayerSelect` on inner layers only, no trace width
through the automation, the arc format of the points array, `GetRect*` for
rectangles only, the save prompt when the outline was invalid.

## Recorded run: the fabrication package (2026-09-14, night)

`pcb export --output 07_PCB/fab` on the 70 × 48 board: the first run closed and reopened
the board to patch the setups (drill spans and outline into the ODB++ job, cell
silkscreen and outline into the Gerber set), then NC drill (12 s), ODB++ (13 s) and
Gerber (11 s) ran through their dialogs. Package: 11 Gerber files (top/bottom copper,
two inner layers with the plane negative, masks, top paste, top silkscreen with 417
draws, board outline with four arcs, drill drawing), 2 drill files (4 non-plated
holes, 41 plated), the ODB++ job zipped with its `d_1_4` drill layer and profile, a
39-row centroid file, a 26-line BOM, the README and the manifest; `checks.ok` true.
Seven files stayed behind as empty (bottom silkscreen, bottom paste, layer-3 plane
negative, the generator's silkscreens) or duplicate (`EtchLayerTop/Bottom`). A second
dry run reports no setup changes.

## Recorded run: trace widths, pin-aware placement, pours and a drawn picture (2026-09-15, night)

The whole recipe again on the board on KiCad footprints, in the new order and with
the desktop locked (so `pcb show` could not see the window): `pcb create --replace`
(33 s; Layout had to be ended by its process because it held `DrillPrefs.txt`),
`pcb annotate` (70 s, 39 parts, 18 nets), `pcb outline 70 × 48 --radius 3`,
`pcb holes --replace`, `pcb arrange --all` (11 s; four clusters, nothing outside; every
satellite beside the IC pin it connects to — the I2C pull-ups at the SOIC's corner
pins on the left and right columns, the decoupling capacitors at the supply pins),
`pcb pour --net GND --layer 2 --replace`, `pcb rules --class POWER --nets VBAT,+3V3
--width 0.5 --min 0.4 --expansion 0.6` (9 s; 12 constraints written, `SynchCES` true,
Layout reports the class at 0.4 / 0.5 mm), `pcb route --layers 1,4 --unroute` (11 s;
complete, 117 traces, 36 vias, the supply traces 19.685 th = 0.5 mm wide, the rest
10 th), `pcb pour --layer 1` and `--layer 4` (6 s each, laid over the traces without a
DRC refusal now that the shape does not obstruct routing), `pcb drc` (15 s; 0 errors,
11 `ViasUnderParts` warnings, `passes` true), `pcb render` top and bottom (16 s each;
133 pads, 36 vias, 117 traces, 8 generated plane pieces, 52 holes, 199 silkscreen
lines, 41 texts). Pictures: `07_PCB/board_render_top.png`, `board_render_bottom.png`.

Then `pcb export --output 07_PCB/fab`: the first run closed and reopened the board
to patch the setups, ran the three dialogs and gathered 16 Gerber files, 2 drill
files, the ODB++ job, the centroid file and the BOM, but `checks.ok` was false — "the
ODB++ job has no drill layer": the ODB++ dialog had written `d_1_4 INCLUDE NO` back
over the patched setup. A second run patched again (board closed) and came out with
`checks.ok` true. `pcb export` now repeats that itself (`rounds`).

Earlier the same night, with the pours laid before routing: the router routed
nothing while the plane shapes obstructed routing; with `RouteObstructed` cleared it
routed every net but the regenerated ground copper left 5 GND pins cut off, and a
Fanout pass did not add vias for them. Pouring the outer layers after routing is
the recipe.

## Recorded run: the board routed by hand (2026-09-15, morning)

The person's layout through the CLI, on the board of the previous run (its
autorouting deleted with `pcb unroute --all`, the outer pours removed): eleven parts
moved with `pcb move` (the MCU's pull-ups and filters to the pins they serve, the
sensor's capacitor above it, two resistors turned upright), `pcb geometry` for the
pins, a plan of 73 traces and 22 vias written pin by pin and checked offline (three
mistakes caught: a diagonal through a transistor pad, a via 0.12 mm from a connector
pad, a trace across two host lines), then `pcb trace --file`. Round 1: all 73 traces
accepted, every via refused for want of a via padstack (the board had none left);
round 1b after the padstack fix: 20 vias placed, 2 refused as DRC violations — both
within 0.1 mm of a pad of their own net — and 15 opens left (three test points are
surface-mount pads that inner-layer runs cannot reach, a via stub hanging off a
diagonal, the bottom VBAT group never tied to the battery connector). Round 2 (16
items) closed every signal net; `pcb pour` on layers 1 and 4, then `pcb stitch`'s 16
ground vias (two pads without room) closed GND. Batch DRC: 16 `TraceWidths` (0.3 mm
stubs against a class that allows exactly 0.254) — `pcb rules --class "(Default)"
--expansion 0.5`; 3 `Hangers` (the runs under the test points) — `pcb unroute --at`
and the runs drawn to their vias; then 0 errors, 2 `ViasUnderParts` warnings.
`pcb labels` moved 41 designators (R202, R305 found no room). Result: 129 traces,
41 vias (16 of them ground stitching), 18/18 nets, `pcb export` `checks.ok` on the
first run. Pictures `07_PCB/board_render_top.png`, `board_render_bottom.png`; the
plans in `06_脚本与工具/handroute/`.

## Recorded run: the hand layout replayed for a screen recording (2026-09-15, afternoon)

Twice from `pcb create --replace` + `pcb annotate` (a fresh board, 39 parts unplaced;
14–45 s and 45–70 s): `pcb outline`, `pcb holes`, `pcb show --top-view`, `pcb arrange
--all`, sixteen `pcb move` calls (the morning's hand placement, recovered by comparing
the planner's dry-run placement with the board and ordered so no part lands on one not
yet moved; 2.5 s each), the inner pour, two `pcb rules`, `pcb geometry`, one `pcb trace
--file` of the three morning rounds merged (83 traces and 26 vias: 109 items, none
refused, every signal net closed), the outer pours, `pcb stitch` + `pcb trace` (32
items, GND closed), `pcb labels` (37 moved) and `pcb drc` (0 errors, 2 `ViasUnderParts`).
185 s at full speed; 214 s with `--pace 0.15`, under which the traces grow visibly over
18 s instead of appearing in a burst. Both runs ended with the same board as the
morning's (129 traces, 41 vias, 18/18 nets).

Found on the way: on the Layout started by `pcb annotate`, the UI Automation `pcb show`
used for the scheme combo and the Fit Board button came back empty (pywinauto's
`Desktop().window(handle=…).descendants()` gave 0 elements, `Application(backend="uia")
.connect(process=pid)` did see the tree). The scheme is now loaded through
`ActiveView.DisplayControl.LoadScheme` (`DisplayControl.Name` reads it back) and the fit
through `ActiveView.SetExtentsToBoard`; `Application.Gui.ProcessCommand("VIEW_FITBOARD")`
runs Layout's named commands as well.

## Recorded run: the destructive path and the timeout code (2026-09-17)

Two gaps between the recorded evidence and the release gate, closed on the same
installation:

- `pcb create --replace` had gained an archive step that no live run had
  exercised. A run against the finished board wrote `PCB-backup-20260917-153700.zip`
  beside the project — 1.9 MB, 128 entries, the `.pcb` file included, `LogFiles`
  and `*.bak` left out — before deleting the folder; `pcb annotate` then rebuilt
  the board (39 parts, 18 nets) and `flow_demo.py` redrew it in full.
- `E_TIMEOUT` is a declared error code that no test reached. It now has one at the
  backend (`subprocess.TimeoutExpired` becomes `E_TIMEOUT`, retryable) and one at
  the CLI boundary (an adapter that never answers exits 8).

With those in place the release gate reads: functional contract coverage 107 of
107 commands, contract tests across success, validation, usage, confirmation,
conflict, not-found, backend-unavailable and timeout paths, empty results, paging,
the output envelope, exit codes and the stdout/stderr boundary, and the live runs
recorded above. `release_readiness.level` is `stable`.

## Recorded run: selected placement on a disposable board, 2026-09-19

`pcb placement`'s native path had command-level and simulated-object tests but no
licensed smoke record. A board was built for one from a template clone, all
through the CLI: `project init --template` (266 files; the clone's `Case`
partition had no parts database and one was created and registered),
`schematic draw` (8 parts, 6 nets), `library build --package`, `pcb create`,
`pcb annotate` (8 components, 6 nets, 16 pins) and `pcb arrange` to place them.

| Check | Evidence |
|---|---|
| Preview reads real state | `R1` at 6.0, 45.5 with `object_id` 67, `anchor` 0, `fix_lock` 0, matching an independent `pcb components` read |
| Top-side placement | align `y` to `R2`, distribute `x` 10→30 applied and read back: `R1` 10.0, `R2` 20.0, `R3` 30.0, each `status: verified` |
| Bottom-side placement | **not run.** `Side` is read-only on `IMGCPCBComponent`, and this tool does not flip sides, so no bottom-side part could be produced from automation |
| Protected part | `FixLock = 2` set on `R3`; a task naming it as a moved target is refused `E_CONFLICT` "the task would move a locked or fixed component", `details.field: R3` |
| Refusal | a task naming components the board does not have is refused `E_NOT_FOUND`, non-retryable, before any write |
| Stale preview | a token taken before a *selected* component moved is refused `E_CONFLICT` "confirmation token does not match this operation". A token stays valid when an unselected component moves: the digest binds the selection, not the board |
| Save / close / reopen | Layout stopped (0 processes) and reopened; `R1` 12.0, `R2` 22.5, `R3` 32.0 unchanged |
| DRC | 6.4 s, 16 hazards, all `PartialNets` "Unrouted Pin" on an unrouted board; no clearance or overlap hazard from the placement |

One thing is unexplained. The first confirmed placement on the freshly annotated
board returned `E_PROJECT_INVALID` "placement did not complete" after applying
part of the task — `R1` and `R2` moved and verified, `R3` did not. Repeating the
task completed it, and four later runs (including the same task shape at other
coordinates) all completed with every item verified. The failing run's per-item
report was overwritten before it was read, so what refused `R3` is not known.
Treat a partial apply as possible and re-read the board rather than replaying.

## Not claimed by `stable`

- Every recorded run comes from one Windows installation of XPED2604. A second
  machine has not repeated them, and CI has never run.
- Symbols and cells are generated or converted from an open-source library rather
  than taken from a production central library; part numbers are placeholders.
- The Xpedition automation surface is what this installation exposes; another
  version may differ. `docs/COMPATIBILITY.md` is the version matrix.
