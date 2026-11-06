# Changelog

All notable changes to xpedition-cli are recorded here, in the Keep a Changelog format; versions follow Semantic Versioning.

## [Unreleased]

### Added

- The xpedition-cli project, built on tool-driver-cli-spec 1.0.4. This version starts over: commands, flags, output, confirmation, saving and exit codes differ from 1.0.3 and earlier, which had no `update` command; reinstall with `npm install -g @fateforge/xpedition-cli`. The Skills `xpedition-schematic` and `xpedition-pcb` of 1.0.3 are gone; the entry Skill `xpedition-cli` covers their work.
- `doc create --kind project`: in a Designer session, a new project from Xpedition's stock setup template with its own copy of the central library and a board design; in file mode, a copy of a template project.
- File-mode commands on a project: `project get`, `part list`, `part get`, `part create`, `packager run`, `pcb create`, and `schematic export` (also in a Designer session).
- Designer session commands: `schematic get`, `schematic create`, `schematic check`.
- Layout session commands: `forward-annotation run`, `pcb update`, `placement run`, `plane create`, `router run`, `pcb get`, `pcb check`, `pcb export`.
- Long jobs, run by the runtime in its own worker: `part create`, `packager run`, `pcb create`, `schematic create`, `schematic check`, `forward-annotation run`, `placement run`, `router run`, `pcb check` and `pcb export`, and the standard `session open`, `session close`, `doc create`, `doc open`, `doc save` and `doc close`, which take Designer and Layout about a minute.
- Sessions and documents for Designer and Layout through the standard `session`, `doc` and `job` commands: `session open --kind project` or `--kind board` starts Designer or Layout; an instance COM cannot reach is `unreachable`; a document the tool did not open or save reports `dirty: null`; `doc save` closes and opens the document again, reads the saved changes back from it, and checks that the save wrote the files; `session close` on Layout closes the board without saving, as `doc close` does, and ends a Layout that quitting leaves on its start page.
- Every domain command says which document it works on: `doc_kinds` is `project` for the file-mode, Designer and library commands, and `board` for the Layout commands.
- What Xpedition's state stands in the way of is a conflict, not bad input: a second Designer or Layout, another project or board already open, a part number the library holds with other content, a board without an outline, a plane that already exists, a project Designer holds during forward annotation, or a design Designer can read only after a reopen. Writes refuse it at the preview (`E_CONFLICT` `blocked`, with the reason in `blocked_by`); `session open`, `doc open` and reads refuse it with `E_CONFLICT` `application_state`.
- A session held by a dialog names the knowledge entry for it in its `blocker`, and a long job on that session shows as `blocked` with the same `action` and entry.
- Session and document reads answer within the runtime's time limits while Designer or Layout is busy: a session's documents ask that one instance only, and a dialog is looked for with Windows calls before UI Automation, which gets a short time. While a long job of xpedition-cli drives an instance, the dialogs that job answers itself leave the session `busy`; another dialog blocks it.
- Every length in the output is in mm with the unit in its key, such as `x_mm`, symbol coordinates included, and `reference` states the coordinate system of every command that returns coordinates. Board geometry is read in whatever unit the board counts in. `placement run` returns where each part now stands; `pcb update` changes only what it is given and keeps the corner radius without `--radius`.
- The central library's exports are cached in the folder `xpedition-cli` of this user's local application data, outside the state directory.
- Builtin knowledge for the known Xpedition limits and dialogs; one entry Skill, `xpedition-cli`.
- Maturity on Xpedition 2604, with live records and an evaluation report in `docs/evidence/`: `stable` for `project get`, `part list`, `part get`, `schematic get`, `schematic check`, `pcb get`, `pcb check`, `session list` and `doc get`; `verified` for `session get` and `doc list`.
