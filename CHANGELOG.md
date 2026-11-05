# Changelog

All notable changes to xpedition-cli are recorded here, in the Keep a Changelog format; versions follow Semantic Versioning.

## [Unreleased]

### Added

- The xpedition-cli project, generated from tool-driver-cli-spec 1.0.0. This version starts over: commands, flags, output, confirmation, saving and exit codes differ from 1.0.3 and earlier, which had no `update` command; reinstall with `npm install -g @fateforge/xpedition-cli`.
- File-mode commands on a project: `project get`, `project create`, `part list`, `part get`, `part create`, `packager run`, `pcb create`, and `schematic export` (also in a Designer session).
- Designer session commands: `schematic get`, `schematic create` (a long job), `schematic check`.
- Layout session commands: `forward-annotation run` (a long job), `pcb update`, `placement run`, `plane create`, `router run`, `pcb get`, `pcb check`, `pcb export` (a long job).
- Sessions and documents for Designer and Layout through the standard `session`, `doc` and `job` commands; builtin knowledge for the known Xpedition limits and dialogs; one entry Skill, `xpedition-cli`.
