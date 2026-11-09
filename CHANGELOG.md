# Changelog

All notable changes to xpedition-cli are recorded here, in the Keep a Changelog format; versions follow Semantic Versioning.

## [Unreleased]

### Added

- `plane create` cuts its shape to a rectangle with `--from-x`, `--from-y`, `--to-x` and `--to-y`, a side left out running to the board's edge, and pours it beside the net's other shapes on the layer: a hatched band over a flex board's bend on a layer that is solid elsewhere. A region that reaches into a shape of the net on that layer is refused, and the read-back finds the shape by its region.
- `pcb get` reports the keepouts by kind, with the placement keepouts that limit height, such as a flex board's stiffeners; the cutouts inside the outline; the plane shapes by net, layer and fill, with how many shapes and the extents they cover; and the via padstacks of the design's own library with how many vias use each. `--output` adds each keepout's, cutout's and plane shape's geometry.
- `trace create` rounds a corner of the routing plan with an arc when the point carries a third value, the arc's radius, tangent to both segments; the check measures along the arc, and a radius too large for the segments beside it is refused.
- The Skill gains a playbook for routing a flex board between two connectors: read the stiffeners, bend zones and pins, set the rules, plan one lane per net, draw, pour with each bend zone hatched, stitch outside the bends and check. Two knowledge entries back it: `layout-flex-bend-and-stiffener` says how a board marks where it bends and where it is stiffened and what each place allows, and `layout-flex-connector-bus` gives common practice for the fan-out, the lanes and the power of such a board.

### Fixed

- `pcb check` keeps the values of a hazard's description when Layout runs in a language other than English, which hands the labels over COM as question marks: `CC1; Pad Part; J1; Unrouted Pin` instead of `???? : CC1 ...`.
- `trace create`, `trace delete` and `via delete` count a board's traces and vias in one pass instead of one pass per net and layer, so the preview and the read-back of a plan on a board of a few hundred traces finish within the call's budget; the read-back ran out of time there and reported the change as not verified.
- `trace create` reads its traces back by their geometry: each net and layer of the plan reads back `plan_drawn`, true when every point along its planned traces lies on a trace of that net and layer. Layout keeps one planned trace as several objects, split at its arcs and elsewhere, so the count it read back before failed a plan that was drawn.
- `schematic export` and `bom export` try replacing the file again for two seconds when Windows refuses it, as a virus scanner or the search indexer holding a file it has just seen written does for a moment; such a run failed.

## [1.2.1] - 2026-11-09

### Changed

- Built on tool-driver-cli-spec 1.0.10. A long job that ends with `ok: false`, a write or a read, carries the error the call would have returned, in `code`, `message` and `details`; a command it names to run again is the write's first call, never a spent token. A final job's `action`, `resume`, `title` and `knowledge` are in `details`; only a `blocked` job keeps `action` and `knowledge` at the top. `doc save` compares each field with the tolerance of the command whose write left the value, and a `doc undo` record keeps the undone command's tolerances. The Skill handles a failed job as the error it carries.

### Fixed

- `trace create` joins a trace end that lies on a pad to the pin's origin, and one that lies on a via to the via's centre, before it draws: Layout connects a trace to a pin or a via only at that exact point, and the coordinates the tool reports are rounded, so a plan that ended inside a pad drew copper that stayed unconnected, with every net reported open.
- `via create`, `via update` and `trace create` take a via padstack from the design's own padstack library when no via of the board uses it and the central library cannot be read, as on a copy of a project whose central library is on another machine's drive; they refused such a via as one whose size is not known.
- `doc create` in file mode refuses a template whose folder holds a file another program keeps from being read, such as a project database Designer, Layout, the packager or the iCDB server is working on, with `E_CONFLICT` naming the files, before anything is copied. A copy that still fails part way names the new project file as asked and lists the files it could not copy; it kept the template's file name before.

## [1.2.0] - 2026-11-08

### Added

- `component list`, `component get`, `net list` and `net get` read the schematic Designer has open: each component's part number, value, sheet and attributes, each pin with its name and net, and each net with the component pins it joins and the sheets it reaches. An unnamed net goes by the name Designer gives it, such as `$1N54`; a reference designator two components share is refused as ambiguous, with both listed. On a board Layout has open, `component list` and `component get` give each component's part number, cell, position, rotation, side and anchor, and `component get` each pin's net and position; a name that matches two components only in case is refused as ambiguous.
- `component move` moves one component on the open board to a position, rotation and side, or places one not yet placed; a part sent to the bottom is placed there mirrored. While it moves the part, Layout's placement check is set to refuse a spot another part takes instead of shoving that part aside, so no other part moves: such a spot is refused with `E_CONFLICT`. A fixed or locked part is refused at the preview, a semi-fixed part stays semi-fixed when it changes side, and a part whose side Layout does not give moves only with `--side`. Its result says whether the part lies inside the board outline.
- `mounting-hole create` places mounting holes of a mounting-hole padstack of the central library at one spot, or one in each corner of the board outline with `--corners` and `--inset`, taking the padstack from the library when the board lacks it; a hole whose drill or pad would reach past the outline, a rounded corner's arc included, is refused, and so is an inset that would make the corner holes meet, and a spot that holds a hole of another padstack blocks the call. `mounting-hole list` lists the board's own mounting holes, and `mounting-hole delete` deletes the one that holds a point, or all of them; a fixed or locked hole is refused, and the holes of a part's cell stay.
- `refdes move` moves a component's reference designator on the silkscreen to a position and rotation, or with `--auto` to the first spot beside the part, above, below, right or left, clear of the parts, the other reference designators and the mounting holes, inside the outline. A fixed or locked text is refused at the preview. Its result lists what the text now overlaps, its own part included.
- `bom check` checks the schematic's bill of materials: components without a part number, reference designators used twice, part numbers the central library does not hold, and one part number with several values. When the central library cannot be read, such as one on a drive this machine does not have, it checks the rest and says why the library was left out.
- `bom export` writes the schematic's bill of materials to a CSV file Excel reads, one row per part number and value with the central library's description, or the component's own when the library has none or cannot be read, or one row per component with `--per-component`. With `--baseline` it compares the BOM with an earlier one, from this tool or another, its reference designators separated by commas, full-width commas, ideographic enumeration commas or spaces and ranges written `R1-R4`, `R1~R4` or `R1 - R4`, and lists the components added, removed and changed in part number or value. A file another program holds open, as Excel holds a CSV it shows, is refused before anything is written.
- Evaluation questions for these commands and for `trace create`: read a schematic's connections, values and part numbers; check the BOM and compare it with an earlier one; tidy a placed board by moving a part to the bottom, putting a mounting hole in each corner, taking one back and giving each reference designator a free spot; and draw one trace between two named pins, as wide as its class allows, around the other nets' pads.

### Changed

- The Skill gains a playbook for changing a design that exists: read it first, then move one named part, reference designator or mounting hole at a time, and compare the BOM with an earlier one; its reference says how. Its description names the new commands, and its playbooks take one line each.
- The routing plan reference says `component get` on a board gives the position of each pin to join.
- Reading a schematic takes a fraction of the time: each kind of Designer object is described once, not every object anew, and a pin's location is read only when it has no net. A schematic with 136 pins that took 12 s reads in under a second, which speeds `schematic get`, `schematic check` and the read-back of `schematic create` too; a lookup a busy Designer refuses is asked again on the next object instead of being taken for a missing member.
- The knowledge entry `layout-placement-drc` says a board can set Layout's placement check to shove parts aside, and how component move keeps it from doing so.
- `docs/E2E.md` lists the copy of a real FPC project also for accepting 1.2 by reading its schematic and its bill of materials.
- Every domain command's summary is one short line, so the index `reference` returns fits one response; the details stay in each command's flags, as `reference --command` shows them.
- The live records and the capability survey name the commits of the repository's current history.
- The evaluation questions count a report that says the tool read a change back and the check passed, or that each delete checked itself by reading the board back, as saying it was verified, and one that says a step was saved straight to disk as saying it was saved; a read-back that failed or did not pass still counts as not verified.
- Maturity on Xpedition 2604, with live records and an evaluation report in `docs/evidence/`: `stable` for every domain command, and for `session open`, `session close`, `session list`, `doc create`, `doc open`, `doc get`, `doc list`, `doc save` and `doc close`; `verified` for `session get`. Every behavior matrix passed live again on 1.2.

## [1.1.1] - 2026-11-08

### Changed

- Built on tool-driver-cli-spec 1.0.9. `--kind` of `session open` and `doc create` takes only `project` or `board`, and another kind is refused with `E_USAGE` before anything starts. `operation resolve` settles a standard write whose outcome is unknown, such as `session open`, `doc open` or `doc save`, by reading back the instances, documents or files it changes; it refused them with `E_UNSUPPORTED` before.
- The evaluation questions count a report in Chinese that says each change read back consistently, or was checked, as saying it was verified.

## [1.1.0] - 2026-11-08

### Added

- `constraint get` reads the board's constraints: net classes and their nets, trace widths and differential spacing per layer, differential pairs, clearance rules and the class pairs that use them; `--output` writes them all in the rules file format.
- `constraint import` creates or changes the net classes, widths, differential spacing, differential pairs and clearance rules a rules file names, writes them into the project database and loads them into Layout; what the file does not name stays. It changes the board's side of the constraints only, is refused while that side holds changes Layout has not loaded, and its receipt says persistence saved, so the board does not count it among its unsaved changes. A width outside its class's minimum or maximum moves the limit the file leaves out to that width.
- The knowledge entry `layout-impedance-from-fab-table` says impedance widths and spacing come from the fabricator's table: Layout's own impedance figures were several hundred ohms for any width on both boards measured.
- `padstack list` lists the padstacks of the central library with their type and their pad, hole and soldermask sizes; `--type via` lists the vias.
- `padstack create` adds a via padstack to the central library: a plated hole, a round pad on every conductor layer, and a soldermask opening with `--mask` or none (tented). A via the library holds as it is is skipped; a name it holds with other content is refused.
- `trace create` draws the traces and places the vias of a routing plan, a JSON file in board coordinates. It first checks the plan offline against the board's copper, the width limits of each net's class and the clearance rules for each pair of classes, and is refused with every reason it finds; its result says which of the plan's nets are routed now. A via the board already uses takes its size from the board's own vias, so a plan works on a project whose central library is on another machine's drive.
- `trace delete` deletes the traces of the nets named, on every layer or on the layers named; `via delete` deletes their vias; `plane delete` deletes the plane shapes of one net on one layer. They take back a routing attempt before a save, and the save's read-back expects neither the padstack of the deleted vias nor the fill of the deleted plane.
- `via update` changes the vias of the nets or net classes named to another via padstack, pulled from the central library when the board lacks it. The autorouter places the via each net class is assigned in Constraint Manager, which no command can change, so a router run followed by via update gives the via size the design asks for.
- `via create` places vias of one net: one at `--x`, `--y`, or a stitching grid at `--pitch` inside the net's copper on two layers, clear of every pad, the traces of other nets, vias and holes by the board's default clearance rule, and 0.254 mm from the board's cutouts, the distance Layout's DRC asks. The via is the net class's own unless `--padstack` names another; a via the board already uses takes its size from the board's own vias.
- `solder-mask create` opens the soldermask over a rectangle on one side, or the coverlay of a flex board with `--cover-layer`, naming the nets whose copper it bares; an opening over no copper, or a coverlay opening on a stackup without a cover layer, is refused. `solder-mask delete` deletes the opening that holds a point.
- `pcb render` draws the board as a PNG from its geometry: outline, pads, traces, vias, plane shapes, holes, cutouts such as slots for screws, and soldermask openings, from the top or the bottom, for chosen layers. Its result counts the pads, traces and plane shapes on the layers drawn and the openings on the side drawn. It needs no screen, so it works with the desktop locked, and runs as a long job, since reading a real board's geometry takes tens of seconds.
- Evaluation questions for the routing commands, on the sample board: put the power nets in a net class of their own width, route only that class and give its vias the size asked for; pour a second GND plane, stitch the two, bare a patch of copper and draw the board; take back the traces, vias, a plane and a soldermask opening; read a net class's width and the library's vias.

### Changed

- The Skill gains a playbook for routing a board, with the rules file and the routing plan described in its references; the board-building steps move into a reference of their own.
- The knowledge entries describe what xpedition-cli does now, without version labels; the licence entry says a missing licence call is reported as `E_HUMAN_REQUIRED` with the action `activate_license`, and the Designer prompts entry says doc open leaves the question about closing open documents to the person.
- The knowledge on vias near pads, trace widths, pouring after routing and the board flow names the new commands: trace create and via create place single traces and vias, plane delete takes back a pour, and constraint import sets the rules before any copper.
- Built on tool-driver-cli-spec 1.0.8. A long write's final receipt names the document as the job left it, so a `doc save` after it no longer lists the job's own change as made outside the tool. A long write that a dialog stops after it was sent ends `unknown` with `E_HUMAN_REQUIRED`, the dialog's title and the command that resumes it. The Skill saves only when the person asks for it, since a grant to change a document is not a request to save it, and writes a scope file itself only for what the person's own message grants. A person grants live-run targets once, in `docs/E2E.md`; inside them the coding Agent works without asking again.
- `router run` routes only the nets `--nets` names and the nets of the classes `--net-classes` names, with the passes `--passes` lists in order, such as `fanout:1,route:1-5`; its result then adds `selected`, the routing of those nets. Its result reports connections only: run pcb check to judge the routing.
- `pcb get` shows, for each net class, the via it is assigned for each via span: the via the autorouter places.
- `plane create --hatch orthogonal` or `diagonal`, with `--hatch-width` and `--hatch-distance`, pours a hatched plane, as flex boards often use; only that shape is hatched.
- `pcb get` names the kind of each insulation layer of the stackup, such as `cover_layer` on a flex board.
- Reading a board's geometry takes about half the time: net names are read without wrapping each net object, and the readers of copper alone skip the silkscreen.
- `docs/E2E.md` lists a copy of a real FPC project among the authorized targets, used only to accept 1.1 by routing it.
- Maturity on Xpedition 2604, with live records and an evaluation report in `docs/evidence/`: `stable` for every domain command but `trace create`, and for `session open`, `session close`, `session list`, `doc create`, `doc open`, `doc get`, `doc save` and `doc close`; `verified` for `trace create`, `session get` and `doc list`.

### Fixed

- `plane create` on a board whose outline is not a rectangle, such as a flex board, pours inside the route border; it poured a rounded rectangle over the outline's extents, partly outside the board. `--margin` applies to a rectangular outline only and is refused on any other.
- router run no longer fails while regenerating the planes when a plane shape was deleted: an assignment without a shape is left alone.
- `session list`, `session get`, `doc list` and `doc get` answer in time when Designer or Layout hangs: an instance's probe and its look for a dialog take at most about 2 s together, inside the 2.5 s the runtime gives the adapter's part of the session list.

## [1.0.4] - 2026-11-06

### Added

- The xpedition-cli project, built on tool-driver-cli-spec 1.0.5. This version starts over: commands, flags, output, confirmation, saving and exit codes differ from 1.0.3 and earlier, which had no `update` command; reinstall with `npm install -g @fateforge/xpedition-cli`. The Skills `xpedition-schematic` and `xpedition-pcb` of 1.0.3 are gone; the entry Skill `xpedition-cli` covers their work.
- `doc create --kind project`: in a Designer session, a new project from Xpedition's stock setup template with its own copy of the central library and a board design; in file mode, a copy of a template project.
- File-mode commands on a project: `project get`, `part list`, `part get`, `part create`, `packager run`, `pcb create`, and `schematic export` (also in a Designer session).
- Designer session commands: `schematic get`, `schematic create`, `schematic check`.
- Layout session commands: `forward-annotation run`, `pcb update`, `placement run`, `plane create`, `router run`, `pcb get`, `pcb check`, `pcb export`.
- Long jobs, run by the runtime in its own worker: `part create`, `packager run`, `pcb create`, `schematic create`, `schematic check`, `forward-annotation run`, `placement run`, `router run`, `pcb check` and `pcb export`, and the standard `session open`, `session close`, `doc create`, `doc open`, `doc save` and `doc close`, which take Designer and Layout about a minute.
- Sessions and documents for Designer and Layout through the standard `session`, `doc` and `job` commands: `session open --kind project` or `--kind board` starts Designer or Layout; an instance COM cannot reach is `unreachable`; a document the tool did not open or save reports `dirty: null`; `doc save` closes and opens the document again, reads the last value of each changed field back from it, and checks that the save wrote the files; `session close` on Layout closes the board without saving, as `doc close` does, and ends a Layout that quitting leaves on its start page.
- A path written with short (8.3) folder names, as a temporary folder can be, names the same project, board or library as its long form.
- Every domain command says which document it works on: `doc_kinds` is `project` for the file-mode, Designer and library commands, and `board` for the Layout commands.
- What Xpedition's state stands in the way of is a conflict, not bad input: a second Designer or Layout, another project or board already open, a part number the library holds with other content, a board without an outline, an outline whose route border would leave placed parts or routing outside it (Layout refuses that border only after writing the outline), a plane that already exists, a project Designer holds during forward annotation, or a design Designer can read only after a reopen. Writes refuse it at the preview (`E_CONFLICT` `blocked`, with the reason in `blocked_by`); `session open`, `doc open` and reads refuse it with `E_CONFLICT` `application_state`.
- A session held by a dialog names the knowledge entry for it in its `blocker`, and a long job on that session shows as `blocked` with the same `action` and entry.
- Session and document reads answer within the runtime's time limits while Designer or Layout is busy: a session's documents ask that one instance only, and a dialog is looked for with Windows calls before UI Automation, which gets a short time. While a long job of xpedition-cli drives an instance, from its first step, the dialogs that job answers itself leave the session `busy`; a dialog it does not answer, including one without the button its rule presses, blocks it, and a second long job on that instance is refused with `E_BUSY`.
- Every length in the output is in mm with the unit in its key, such as `x_mm`, symbol coordinates included, and `reference` states the coordinate system of every command that returns coordinates. Board geometry is read in whatever unit the board counts in. `placement run` returns where each part now stands; `pcb update` changes only what it is given and keeps the corner radius without `--radius`.
- The central library's exports are cached in the folder `xpedition-cli` of this user's local application data, outside the state directory.
- Builtin knowledge for the known Xpedition limits and dialogs; one entry Skill, `xpedition-cli`.
- Maturity on Xpedition 2604, with live records and an evaluation report in `docs/evidence/`: `stable` for every domain command and for `session open`, `session close`, `session list`, `doc create`, `doc open`, `doc get`, `doc save` and `doc close`; `verified` for `session get` and `doc list`.
