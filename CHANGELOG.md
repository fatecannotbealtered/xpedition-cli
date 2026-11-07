# Changelog

All notable changes to xpedition-cli are recorded here, in the Keep a Changelog format; versions follow Semantic Versioning.

## [Unreleased]

### Added

- `constraint get` reads the board's constraints: net classes and their nets, trace widths and differential spacing per layer, differential pairs, clearance rules and the class pairs that use them; `--output` writes them all in the rules file format.
- `constraint import` creates or changes the net classes, widths, differential spacing, differential pairs and clearance rules a rules file names, writes them into the project database and loads them into Layout; what the file does not name stays. It changes the board's side of the constraints only, is refused while that side holds changes Layout has not loaded, and its receipt says persistence saved, so the board does not count it among its unsaved changes. A width outside its class's minimum or maximum moves the limit the file leaves out to that width.
- `padstack list` lists the padstacks of the central library with their type and their pad, hole and soldermask sizes; `--type via` lists the vias.
- `padstack create` adds a via padstack to the central library: a plated hole, a round pad on every conductor layer, and a soldermask opening with `--mask` or none (tented). A via the library holds as it is is skipped; a name it holds with other content is refused.
- `trace delete` deletes the traces of the nets named, on every layer or on the layers named; `via delete` deletes their vias; `plane delete` deletes the plane shapes of one net on one layer. They take back a routing attempt before a save.
- `via update` changes the vias of the nets or net classes named to another via padstack, pulled from the central library when the board lacks it. The autorouter places the via each net class is assigned in Constraint Manager, which no command can change, so a router run followed by via update gives the via size the design asks for.
- `via create` places vias of one net: one at `--x`, `--y`, or a stitching grid at `--pitch` inside the net's copper on two layers, clear of every pad, the traces of other nets, vias and holes by the board's default clearance rule. The via is the net class's own unless `--padstack` names another.
- `solder-mask create` opens the soldermask over a rectangle on one side, or the coverlay of a flex board with `--cover-layer`, naming the nets whose copper it bares; an opening over no copper, or a coverlay opening on a stackup without a cover layer, is refused. `solder-mask delete` deletes the opening that holds a point.

### Changed

- Built on tool-driver-cli-spec 1.0.6.
- `router run` routes only the nets `--nets` names and the nets of the classes `--net-classes` names, with the passes `--passes` lists in order, such as `fanout:1,route:1-5`; its result then adds `selected`, the routing of those nets. Its result reports connections only: run pcb check to judge the routing.
- `pcb get` shows, for each net class, the via it is assigned for each via span: the via the autorouter places.
- `plane create --hatch orthogonal` or `diagonal`, with `--hatch-width` and `--hatch-distance`, pours a hatched plane, as flex boards often use; only that shape is hatched.
- `pcb get` names the kind of each insulation layer of the stackup, such as `cover_layer` on a flex board.

### Fixed

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
