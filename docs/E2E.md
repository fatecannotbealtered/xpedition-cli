# Live Acceptance

English | [中文](E2E_zh.md)

Live runs execute the behavior matrices of xpedition-cli on the real Xpedition, with the real adapter. They make real changes, so they use only the targets authorized below.

## Authorization Rules

Authorized Targets in this file is the only list of the files, folders and devices that live runs, and evaluations on the real Xpedition, may use. A target is added only by a person, or by a coding Agent when the person's own message grants it; the Agent then tells the person the line it added, and keeps the descriptions up to date itself. Each target is one list item that starts with the target in backticks, written the way the scope file writes targets: a `file://` URL of a file or folder, or `device:<id>` for a device, followed by a colon and a description of the target. A listed folder is a work folder: inside it, the coding Agent makes practice copies, output folders and evaluation workspaces without asking again. An instance of Xpedition has no lasting ID, so live runs use only instances started for the run, by a person or by the coding Agent, with documents from the listed files and folders; the coding Agent closes only the instances it started. Live runs use no other target.

Scope files for live runs grant only listed targets and the new documents of the instances started for the run. The matrix and evaluation runners write the scope files of their runs; for other live work, such as trying a command or installing the host component, the coding Agent writes its own without asking. A target that an evaluation question's `SCOPE` names is listed too.

## Authorized Targets

- `file:///D:/driver-work/xpedition-cli/`: the practice folder of the maintainer's machine: practice projects the live runs create from Xpedition's stock templates, their copied central libraries, and the evaluation workspaces. No real project is placed here except the copies listed below.
- `file:///D:/driver-work/xpedition-cli/V00test/`: a copy of a real FPC project, used only to try routing, reading and checking on a real flex board; never committed and never used as evidence.
- `file:///D:/driver-work/xpedition-cli/LCDtest/`: a copy of a second real FPC project, given as a test job: place the parts at their mechanical positions, then route; never committed and never used as evidence.

## Procedure

Live runs are made on these machines:

- One Windows 11 x64 computer with Xpedition Enterprise 2604, in file and session mode. The person lists the practice projects and the evaluation folder under Authorized Targets.

<!-- driver:spec-procedure:start -->
To run a behavior matrix on the real Xpedition:

1. Prepare the targets, such as a copy of a project made for testing in a listed folder, an instance started for the run or a device set up for testing. A target outside the listed ones is added under Authorized Targets first, as Authorization Rules says.
2. Set `XPEDITION_CLI_STATE_DIR` to a separate directory for live runs.
3. Run `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>`, with one `--set` for each placeholder the matrix uses, named without braces, such as `--set doc=<file>`: `{session}` takes an instance started for the run, `{doc}` an authorized document or file, `{device}` an authorized device, and `{dir}` a new empty directory inside an authorized folder. `{job}` takes a job ID; without it, the runner first runs the `normal` row of the long command that `--job-from` names, or of the only long command, and uses the job it starts. `{inputs}` needs no `--set`: it is `tests/inputs/`, whose files commands only read, so they need no authorization.
4. The runner runs the rows that apply, writes a live record to `docs/evidence/` and fills in the actual result and the `live:` evidence reference of each row. A row whose actual result differs from the expected one has failed.
5. A row that cannot be caused on demand on the real Xpedition, such as `disconnect`, may be marked `unrunnable` with the reason; the `normal`, `dialog` and `read_back_mismatch` rows never may. For `read_back_mismatch`, run the matrix with `--row read_back_mismatch`: the tool waits before its read-back, and in that time change what it just wrote in Xpedition, or delete the file it made.
6. Run `python scripts/spec.py generate` to update the support matrix and the index below, then commit the records with the matrices.

To run the evaluation questions on the real Xpedition, run `python scripts/spec.py eval --workspace-root <folder>` with a folder inside a listed folder. The runner writes each question's scope file and writes the evaluation report to `docs/evidence/`.

A run in a live record counts as evidence until the command's registration or the adapter code that run used changes; a new version number alone changes nothing. Records never hold secrets, and arguments that carry secrets show as `***`.
<!-- driver:spec-procedure:end -->

## Evidence Records

The live records in `docs/evidence/`, generated by `python scripts/spec.py generate`:

<!-- driver:evidence:start -->
| Record | Recorded at | Application | Platform | Result |
|---|---|---|---|---|
| [2026-11-14-064822-component-get.json](evidence/2026-11-14-064822-component-get.json) | 2026-11-14T06:48:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064819-component-list.json](evidence/2026-11-14-064819-component-list.json) | 2026-11-14T06:48:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064732-component-get.json](evidence/2026-11-14-064732-component-get.json) | 2026-11-14T06:47:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064728-component-list.json](evidence/2026-11-14-064728-component-list.json) | 2026-11-14T06:47:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064646-via-update.json](evidence/2026-11-14-064646-via-update.json) | 2026-11-14T06:46:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064558-via-update.json](evidence/2026-11-14-064558-via-update.json) | 2026-11-14T06:45:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064549-via-update.json](evidence/2026-11-14-064549-via-update.json) | 2026-11-14T06:45:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064543-via-update.json](evidence/2026-11-14-064543-via-update.json) | 2026-11-14T06:45:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064538-via-update.json](evidence/2026-11-14-064538-via-update.json) | 2026-11-14T06:45:38Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064534-via-update.json](evidence/2026-11-14-064534-via-update.json) | 2026-11-14T06:45:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064503-via-delete.json](evidence/2026-11-14-064503-via-delete.json) | 2026-11-14T06:45:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064436-via-delete.json](evidence/2026-11-14-064436-via-delete.json) | 2026-11-14T06:44:36Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064413-via-delete.json](evidence/2026-11-14-064413-via-delete.json) | 2026-11-14T06:44:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064409-via-delete.json](evidence/2026-11-14-064409-via-delete.json) | 2026-11-14T06:44:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064346-trace-delete.json](evidence/2026-11-14-064346-trace-delete.json) | 2026-11-14T06:43:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064331-trace-delete.json](evidence/2026-11-14-064331-trace-delete.json) | 2026-11-14T06:43:31Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064318-trace-delete.json](evidence/2026-11-14-064318-trace-delete.json) | 2026-11-14T06:43:18Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064314-trace-delete.json](evidence/2026-11-14-064314-trace-delete.json) | 2026-11-14T06:43:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064246-via-delete.json](evidence/2026-11-14-064246-via-delete.json) | 2026-11-14T06:42:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064242-trace-delete.json](evidence/2026-11-14-064242-trace-delete.json) | 2026-11-14T06:42:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064239-trace-create.json](evidence/2026-11-14-064239-trace-create.json) | 2026-11-14T06:42:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064235-constraint-import.json](evidence/2026-11-14-064235-constraint-import.json) | 2026-11-14T06:42:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-064123-via-update.json](evidence/2026-11-14-064123-via-update.json) | 2026-11-14T06:41:23Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-064001-via-delete.json](evidence/2026-11-14-064001-via-delete.json) | 2026-11-14T06:40:01Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-063900-trace-delete.json](evidence/2026-11-14-063900-trace-delete.json) | 2026-11-14T06:39:00Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-063848-trace-create.json](evidence/2026-11-14-063848-trace-create.json) | 2026-11-14T06:38:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063833-trace-check.json](evidence/2026-11-14-063833-trace-check.json) | 2026-11-14T06:38:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063829-trace-create.json](evidence/2026-11-14-063829-trace-create.json) | 2026-11-14T06:38:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063816-trace-create.json](evidence/2026-11-14-063816-trace-create.json) | 2026-11-14T06:38:16Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063811-trace-check.json](evidence/2026-11-14-063811-trace-check.json) | 2026-11-14T06:38:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063806-trace-create.json](evidence/2026-11-14-063806-trace-create.json) | 2026-11-14T06:38:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063757-trace-check.json](evidence/2026-11-14-063757-trace-check.json) | 2026-11-14T06:37:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063753-trace-check.json](evidence/2026-11-14-063753-trace-check.json) | 2026-11-14T06:37:53Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-14-063749-trace-create.json](evidence/2026-11-14-063749-trace-create.json) | 2026-11-14T06:37:49Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-063740-constraint-import.json](evidence/2026-11-14-063740-constraint-import.json) | 2026-11-14T06:37:40Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063724-constraint-import.json](evidence/2026-11-14-063724-constraint-import.json) | 2026-11-14T06:37:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063701-constraint-import.json](evidence/2026-11-14-063701-constraint-import.json) | 2026-11-14T06:37:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063650-constraint-import.json](evidence/2026-11-14-063650-constraint-import.json) | 2026-11-14T06:36:50Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063639-constraint-import.json](evidence/2026-11-14-063639-constraint-import.json) | 2026-11-14T06:36:39Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-063558-refdes-move.json](evidence/2026-11-14-063558-refdes-move.json) | 2026-11-14T06:35:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063553-refdes-move.json](evidence/2026-11-14-063553-refdes-move.json) | 2026-11-14T06:35:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063546-refdes-move.json](evidence/2026-11-14-063546-refdes-move.json) | 2026-11-14T06:35:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063542-refdes-move.json](evidence/2026-11-14-063542-refdes-move.json) | 2026-11-14T06:35:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063538-refdes-move.json](evidence/2026-11-14-063538-refdes-move.json) | 2026-11-14T06:35:38Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063534-refdes-move.json](evidence/2026-11-14-063534-refdes-move.json) | 2026-11-14T06:35:34Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-063500-mounting-hole-delete.json](evidence/2026-11-14-063500-mounting-hole-delete.json) | 2026-11-14T06:35:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063453-mounting-hole-delete.json](evidence/2026-11-14-063453-mounting-hole-delete.json) | 2026-11-14T06:34:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063446-mounting-hole-delete.json](evidence/2026-11-14-063446-mounting-hole-delete.json) | 2026-11-14T06:34:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063442-mounting-hole-delete.json](evidence/2026-11-14-063442-mounting-hole-delete.json) | 2026-11-14T06:34:42Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-063436-mounting-hole-delete.json](evidence/2026-11-14-063436-mounting-hole-delete.json) | 2026-11-14T06:34:36Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063432-mounting-hole-create.json](evidence/2026-11-14-063432-mounting-hole-create.json) | 2026-11-14T06:34:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063425-mounting-hole-create.json](evidence/2026-11-14-063425-mounting-hole-create.json) | 2026-11-14T06:34:25Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063420-mounting-hole-create.json](evidence/2026-11-14-063420-mounting-hole-create.json) | 2026-11-14T06:34:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063417-mounting-hole-list.json](evidence/2026-11-14-063417-mounting-hole-list.json) | 2026-11-14T06:34:17Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-14-063413-mounting-hole-create.json](evidence/2026-11-14-063413-mounting-hole-create.json) | 2026-11-14T06:34:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063409-mounting-hole-create.json](evidence/2026-11-14-063409-mounting-hole-create.json) | 2026-11-14T06:34:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063405-mounting-hole-create.json](evidence/2026-11-14-063405-mounting-hole-create.json) | 2026-11-14T06:34:05Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-063358-mounting-hole-delete.json](evidence/2026-11-14-063358-mounting-hole-delete.json) | 2026-11-14T06:33:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063355-mounting-hole-list.json](evidence/2026-11-14-063355-mounting-hole-list.json) | 2026-11-14T06:33:55Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-063322-doc-undo.json](evidence/2026-11-14-063322-doc-undo.json) | 2026-11-14T06:33:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063312-doc-undo.json](evidence/2026-11-14-063312-doc-undo.json) | 2026-11-14T06:33:12Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063304-doc-undo.json](evidence/2026-11-14-063304-doc-undo.json) | 2026-11-14T06:33:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063257-doc-undo.json](evidence/2026-11-14-063257-doc-undo.json) | 2026-11-14T06:32:57Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-063251-component-move.json](evidence/2026-11-14-063251-component-move.json) | 2026-11-14T06:32:51Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063244-component-move.json](evidence/2026-11-14-063244-component-move.json) | 2026-11-14T06:32:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063240-component-move.json](evidence/2026-11-14-063240-component-move.json) | 2026-11-14T06:32:40Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063236-component-move.json](evidence/2026-11-14-063236-component-move.json) | 2026-11-14T06:32:36Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063232-component-move.json](evidence/2026-11-14-063232-component-move.json) | 2026-11-14T06:32:32Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-14-063133-refdes-move.json](evidence/2026-11-14-063133-refdes-move.json) | 2026-11-14T06:31:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063129-mounting-hole-delete.json](evidence/2026-11-14-063129-mounting-hole-delete.json) | 2026-11-14T06:31:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063125-mounting-hole-create.json](evidence/2026-11-14-063125-mounting-hole-create.json) | 2026-11-14T06:31:25Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063122-mounting-hole-list.json](evidence/2026-11-14-063122-mounting-hole-list.json) | 2026-11-14T06:31:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063119-doc-undo.json](evidence/2026-11-14-063119-doc-undo.json) | 2026-11-14T06:31:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063115-component-move.json](evidence/2026-11-14-063115-component-move.json) | 2026-11-14T06:31:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063112-doc-save.json](evidence/2026-11-14-063112-doc-save.json) | 2026-11-14T06:31:12Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063108-doc-open.json](evidence/2026-11-14-063108-doc-open.json) | 2026-11-14T06:31:08Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063104-doc-close.json](evidence/2026-11-14-063104-doc-close.json) | 2026-11-14T06:31:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063100-keepout-delete.json](evidence/2026-11-14-063100-keepout-delete.json) | 2026-11-14T06:31:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063057-keepout-create.json](evidence/2026-11-14-063057-keepout-create.json) | 2026-11-14T06:30:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063053-silkscreen-text-delete.json](evidence/2026-11-14-063053-silkscreen-text-delete.json) | 2026-11-14T06:30:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063049-silkscreen-text-create.json](evidence/2026-11-14-063049-silkscreen-text-create.json) | 2026-11-14T06:30:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063045-solder-mask-delete.json](evidence/2026-11-14-063045-solder-mask-delete.json) | 2026-11-14T06:30:45Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063042-solder-mask-create.json](evidence/2026-11-14-063042-solder-mask-create.json) | 2026-11-14T06:30:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063038-via-update.json](evidence/2026-11-14-063038-via-update.json) | 2026-11-14T06:30:38Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063034-via-delete.json](evidence/2026-11-14-063034-via-delete.json) | 2026-11-14T06:30:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063030-via-create.json](evidence/2026-11-14-063030-via-create.json) | 2026-11-14T06:30:30Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063026-trace-delete.json](evidence/2026-11-14-063026-trace-delete.json) | 2026-11-14T06:30:26Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063023-trace-check.json](evidence/2026-11-14-063023-trace-check.json) | 2026-11-14T06:30:23Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063020-trace-create.json](evidence/2026-11-14-063020-trace-create.json) | 2026-11-14T06:30:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063016-constraint-import.json](evidence/2026-11-14-063016-constraint-import.json) | 2026-11-14T06:30:16Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063013-constraint-get.json](evidence/2026-11-14-063013-constraint-get.json) | 2026-11-14T06:30:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063010-pcb-render.json](evidence/2026-11-14-063010-pcb-render.json) | 2026-11-14T06:30:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063006-pcb-export.json](evidence/2026-11-14-063006-pcb-export.json) | 2026-11-14T06:30:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063002-pcb-check.json](evidence/2026-11-14-063002-pcb-check.json) | 2026-11-14T06:30:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-063000-pcb-get.json](evidence/2026-11-14-063000-pcb-get.json) | 2026-11-14T06:30:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062957-router-run.json](evidence/2026-11-14-062957-router-run.json) | 2026-11-14T06:29:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062953-plane-delete.json](evidence/2026-11-14-062953-plane-delete.json) | 2026-11-14T06:29:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062949-plane-create.json](evidence/2026-11-14-062949-plane-create.json) | 2026-11-14T06:29:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062946-placement-run.json](evidence/2026-11-14-062946-placement-run.json) | 2026-11-14T06:29:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062940-pcb-update.json](evidence/2026-11-14-062940-pcb-update.json) | 2026-11-14T06:29:40Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062934-forward-annotation-run.json](evidence/2026-11-14-062934-forward-annotation-run.json) | 2026-11-14T06:29:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062904-silkscreen-text-create.json](evidence/2026-11-14-062904-silkscreen-text-create.json) | 2026-11-14T06:29:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062855-doc-undo.json](evidence/2026-11-14-062855-doc-undo.json) | 2026-11-14T06:28:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062826-refdes-move.json](evidence/2026-11-14-062826-refdes-move.json) | 2026-11-14T06:28:26Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062820-mounting-hole-delete.json](evidence/2026-11-14-062820-mounting-hole-delete.json) | 2026-11-14T06:28:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062811-mounting-hole-create.json](evidence/2026-11-14-062811-mounting-hole-create.json) | 2026-11-14T06:28:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062742-mounting-hole-list.json](evidence/2026-11-14-062742-mounting-hole-list.json) | 2026-11-14T06:27:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062737-component-move.json](evidence/2026-11-14-062737-component-move.json) | 2026-11-14T06:27:37Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062728-doc-save.json](evidence/2026-11-14-062728-doc-save.json) | 2026-11-14T06:27:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062701-doc-open.json](evidence/2026-11-14-062701-doc-open.json) | 2026-11-14T06:27:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062655-doc-close.json](evidence/2026-11-14-062655-doc-close.json) | 2026-11-14T06:26:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062646-keepout-delete.json](evidence/2026-11-14-062646-keepout-delete.json) | 2026-11-14T06:26:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062619-keepout-create.json](evidence/2026-11-14-062619-keepout-create.json) | 2026-11-14T06:26:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062612-silkscreen-text-delete.json](evidence/2026-11-14-062612-silkscreen-text-delete.json) | 2026-11-14T06:26:12Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062603-solder-mask-delete.json](evidence/2026-11-14-062603-solder-mask-delete.json) | 2026-11-14T06:26:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062535-solder-mask-create.json](evidence/2026-11-14-062535-solder-mask-create.json) | 2026-11-14T06:25:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062529-via-update.json](evidence/2026-11-14-062529-via-update.json) | 2026-11-14T06:25:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062519-doc-list.json](evidence/2026-11-14-062519-doc-list.json) | 2026-11-14T06:25:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062453-session-list.json](evidence/2026-11-14-062453-session-list.json) | 2026-11-14T06:24:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062449-via-delete.json](evidence/2026-11-14-062449-via-delete.json) | 2026-11-14T06:24:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062439-via-create.json](evidence/2026-11-14-062439-via-create.json) | 2026-11-14T06:24:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062410-trace-delete.json](evidence/2026-11-14-062410-trace-delete.json) | 2026-11-14T06:24:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062404-trace-check.json](evidence/2026-11-14-062404-trace-check.json) | 2026-11-14T06:24:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062356-trace-create.json](evidence/2026-11-14-062356-trace-create.json) | 2026-11-14T06:23:56Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062329-constraint-import.json](evidence/2026-11-14-062329-constraint-import.json) | 2026-11-14T06:23:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062323-constraint-get.json](evidence/2026-11-14-062323-constraint-get.json) | 2026-11-14T06:23:23Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062314-pcb-render.json](evidence/2026-11-14-062314-pcb-render.json) | 2026-11-14T06:23:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062244-pcb-export.json](evidence/2026-11-14-062244-pcb-export.json) | 2026-11-14T06:22:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062239-pcb-check.json](evidence/2026-11-14-062239-pcb-check.json) | 2026-11-14T06:22:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062230-pcb-get.json](evidence/2026-11-14-062230-pcb-get.json) | 2026-11-14T06:22:30Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062205-router-run.json](evidence/2026-11-14-062205-router-run.json) | 2026-11-14T06:22:05Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062158-plane-delete.json](evidence/2026-11-14-062158-plane-delete.json) | 2026-11-14T06:21:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062149-plane-create.json](evidence/2026-11-14-062149-plane-create.json) | 2026-11-14T06:21:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062122-placement-run.json](evidence/2026-11-14-062122-placement-run.json) | 2026-11-14T06:21:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062116-pcb-update.json](evidence/2026-11-14-062116-pcb-update.json) | 2026-11-14T06:21:16Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062109-forward-annotation-run.json](evidence/2026-11-14-062109-forward-annotation-run.json) | 2026-11-14T06:21:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062056-session-close.json](evidence/2026-11-14-062056-session-close.json) | 2026-11-14T06:20:56Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062052-doc-list.json](evidence/2026-11-14-062052-doc-list.json) | 2026-11-14T06:20:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062050-component-move.json](evidence/2026-11-14-062050-component-move.json) | 2026-11-14T06:20:50Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062046-via-update.json](evidence/2026-11-14-062046-via-update.json) | 2026-11-14T06:20:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062042-via-delete.json](evidence/2026-11-14-062042-via-delete.json) | 2026-11-14T06:20:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062039-via-create.json](evidence/2026-11-14-062039-via-create.json) | 2026-11-14T06:20:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062035-trace-delete.json](evidence/2026-11-14-062035-trace-delete.json) | 2026-11-14T06:20:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062031-trace-check.json](evidence/2026-11-14-062031-trace-check.json) | 2026-11-14T06:20:31Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062028-trace-create.json](evidence/2026-11-14-062028-trace-create.json) | 2026-11-14T06:20:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062025-keepout-delete.json](evidence/2026-11-14-062025-keepout-delete.json) | 2026-11-14T06:20:25Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062021-keepout-create.json](evidence/2026-11-14-062021-keepout-create.json) | 2026-11-14T06:20:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062017-silkscreen-text-delete.json](evidence/2026-11-14-062017-silkscreen-text-delete.json) | 2026-11-14T06:20:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062013-silkscreen-text-create.json](evidence/2026-11-14-062013-silkscreen-text-create.json) | 2026-11-14T06:20:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062010-solder-mask-delete.json](evidence/2026-11-14-062010-solder-mask-delete.json) | 2026-11-14T06:20:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062006-solder-mask-create.json](evidence/2026-11-14-062006-solder-mask-create.json) | 2026-11-14T06:20:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-062002-router-run.json](evidence/2026-11-14-062002-router-run.json) | 2026-11-14T06:20:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061959-refdes-move.json](evidence/2026-11-14-061959-refdes-move.json) | 2026-11-14T06:19:59Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061955-plane-delete.json](evidence/2026-11-14-061955-plane-delete.json) | 2026-11-14T06:19:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061951-plane-create.json](evidence/2026-11-14-061951-plane-create.json) | 2026-11-14T06:19:51Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061948-placement-run.json](evidence/2026-11-14-061948-placement-run.json) | 2026-11-14T06:19:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061944-pcb-update.json](evidence/2026-11-14-061944-pcb-update.json) | 2026-11-14T06:19:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061940-pcb-render.json](evidence/2026-11-14-061940-pcb-render.json) | 2026-11-14T06:19:40Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061937-pcb-get.json](evidence/2026-11-14-061937-pcb-get.json) | 2026-11-14T06:19:37Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061934-pcb-export.json](evidence/2026-11-14-061934-pcb-export.json) | 2026-11-14T06:19:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061930-pcb-check.json](evidence/2026-11-14-061930-pcb-check.json) | 2026-11-14T06:19:30Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061927-mounting-hole-delete.json](evidence/2026-11-14-061927-mounting-hole-delete.json) | 2026-11-14T06:19:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061924-mounting-hole-create.json](evidence/2026-11-14-061924-mounting-hole-create.json) | 2026-11-14T06:19:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061920-mounting-hole-list.json](evidence/2026-11-14-061920-mounting-hole-list.json) | 2026-11-14T06:19:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061917-forward-annotation-run.json](evidence/2026-11-14-061917-forward-annotation-run.json) | 2026-11-14T06:19:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061913-doc-undo.json](evidence/2026-11-14-061913-doc-undo.json) | 2026-11-14T06:19:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061910-doc-save.json](evidence/2026-11-14-061910-doc-save.json) | 2026-11-14T06:19:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061906-doc-open.json](evidence/2026-11-14-061906-doc-open.json) | 2026-11-14T06:19:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061902-doc-close.json](evidence/2026-11-14-061902-doc-close.json) | 2026-11-14T06:19:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061858-constraint-import.json](evidence/2026-11-14-061858-constraint-import.json) | 2026-11-14T06:18:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061855-constraint-get.json](evidence/2026-11-14-061855-constraint-get.json) | 2026-11-14T06:18:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061852-session-close.json](evidence/2026-11-14-061852-session-close.json) | 2026-11-14T06:18:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061848-session-get.json](evidence/2026-11-14-061848-session-get.json) | 2026-11-14T06:18:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061846-schematic-get.json](evidence/2026-11-14-061846-schematic-get.json) | 2026-11-14T06:18:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061843-schematic-export.json](evidence/2026-11-14-061843-schematic-export.json) | 2026-11-14T06:18:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061839-schematic-create.json](evidence/2026-11-14-061839-schematic-create.json) | 2026-11-14T06:18:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061835-schematic-check.json](evidence/2026-11-14-061835-schematic-check.json) | 2026-11-14T06:18:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061832-net-list.json](evidence/2026-11-14-061832-net-list.json) | 2026-11-14T06:18:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061829-net-get.json](evidence/2026-11-14-061829-net-get.json) | 2026-11-14T06:18:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061827-component-list.json](evidence/2026-11-14-061827-component-list.json) | 2026-11-14T06:18:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061824-component-get.json](evidence/2026-11-14-061824-component-get.json) | 2026-11-14T06:18:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061821-bom-export.json](evidence/2026-11-14-061821-bom-export.json) | 2026-11-14T06:18:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061817-bom-check.json](evidence/2026-11-14-061817-bom-check.json) | 2026-11-14T06:18:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061738-plane-delete.json](evidence/2026-11-14-061738-plane-delete.json) | 2026-11-14T06:17:38Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061728-pcb-update.json](evidence/2026-11-14-061728-pcb-update.json) | 2026-11-14T06:17:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061724-via-create.json](evidence/2026-11-14-061724-via-create.json) | 2026-11-14T06:17:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061704-keepout-delete.json](evidence/2026-11-14-061704-keepout-delete.json) | 2026-11-14T06:17:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061700-keepout-create.json](evidence/2026-11-14-061700-keepout-create.json) | 2026-11-14T06:17:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061657-silkscreen-text-delete.json](evidence/2026-11-14-061657-silkscreen-text-delete.json) | 2026-11-14T06:16:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061653-silkscreen-text-create.json](evidence/2026-11-14-061653-silkscreen-text-create.json) | 2026-11-14T06:16:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061649-solder-mask-delete.json](evidence/2026-11-14-061649-solder-mask-delete.json) | 2026-11-14T06:16:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061646-solder-mask-create.json](evidence/2026-11-14-061646-solder-mask-create.json) | 2026-11-14T06:16:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061641-plane-delete.json](evidence/2026-11-14-061641-plane-delete.json) | 2026-11-14T06:16:41Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061638-plane-create.json](evidence/2026-11-14-061638-plane-create.json) | 2026-11-14T06:16:38Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061600-pcb-export.json](evidence/2026-11-14-061600-pcb-export.json) | 2026-11-14T06:16:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061552-pcb-export.json](evidence/2026-11-14-061552-pcb-export.json) | 2026-11-14T06:15:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061527-pcb-export.json](evidence/2026-11-14-061527-pcb-export.json) | 2026-11-14T06:15:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061450-pcb-export.json](evidence/2026-11-14-061450-pcb-export.json) | 2026-11-14T06:14:50Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-14-061322-pcb-render.json](evidence/2026-11-14-061322-pcb-render.json) | 2026-11-14T06:13:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061307-pcb-render.json](evidence/2026-11-14-061307-pcb-render.json) | 2026-11-14T06:13:07Z | Xpedition 2604 | windows | 6 of 6 runs passed |
| [2026-11-14-061224-keepout-delete.json](evidence/2026-11-14-061224-keepout-delete.json) | 2026-11-14T06:12:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061215-keepout-delete.json](evidence/2026-11-14-061215-keepout-delete.json) | 2026-11-14T06:12:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061211-keepout-delete.json](evidence/2026-11-14-061211-keepout-delete.json) | 2026-11-14T06:12:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061207-keepout-delete.json](evidence/2026-11-14-061207-keepout-delete.json) | 2026-11-14T06:12:07Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061203-keepout-delete.json](evidence/2026-11-14-061203-keepout-delete.json) | 2026-11-14T06:12:03Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-061157-keepout-create.json](evidence/2026-11-14-061157-keepout-create.json) | 2026-11-14T06:11:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061149-keepout-create.json](evidence/2026-11-14-061149-keepout-create.json) | 2026-11-14T06:11:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061146-keepout-create.json](evidence/2026-11-14-061146-keepout-create.json) | 2026-11-14T06:11:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061142-keepout-create.json](evidence/2026-11-14-061142-keepout-create.json) | 2026-11-14T06:11:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061138-keepout-create.json](evidence/2026-11-14-061138-keepout-create.json) | 2026-11-14T06:11:38Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-061129-silkscreen-text-delete.json](evidence/2026-11-14-061129-silkscreen-text-delete.json) | 2026-11-14T06:11:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061118-silkscreen-text-delete.json](evidence/2026-11-14-061118-silkscreen-text-delete.json) | 2026-11-14T06:11:18Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061115-silkscreen-text-delete.json](evidence/2026-11-14-061115-silkscreen-text-delete.json) | 2026-11-14T06:11:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061111-silkscreen-text-delete.json](evidence/2026-11-14-061111-silkscreen-text-delete.json) | 2026-11-14T06:11:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061107-silkscreen-text-delete.json](evidence/2026-11-14-061107-silkscreen-text-delete.json) | 2026-11-14T06:11:07Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-061100-silkscreen-text-create.json](evidence/2026-11-14-061100-silkscreen-text-create.json) | 2026-11-14T06:11:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061052-silkscreen-text-create.json](evidence/2026-11-14-061052-silkscreen-text-create.json) | 2026-11-14T06:10:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061047-silkscreen-text-create.json](evidence/2026-11-14-061047-silkscreen-text-create.json) | 2026-11-14T06:10:47Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061043-silkscreen-text-create.json](evidence/2026-11-14-061043-silkscreen-text-create.json) | 2026-11-14T06:10:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061037-silkscreen-text-create.json](evidence/2026-11-14-061037-silkscreen-text-create.json) | 2026-11-14T06:10:37Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-061030-via-create.json](evidence/2026-11-14-061030-via-create.json) | 2026-11-14T06:10:30Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061013-via-create.json](evidence/2026-11-14-061013-via-create.json) | 2026-11-14T06:10:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-061003-via-create.json](evidence/2026-11-14-061003-via-create.json) | 2026-11-14T06:10:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060945-via-create.json](evidence/2026-11-14-060945-via-create.json) | 2026-11-14T06:09:45Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060935-via-create.json](evidence/2026-11-14-060935-via-create.json) | 2026-11-14T06:09:35Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-060927-solder-mask-delete.json](evidence/2026-11-14-060927-solder-mask-delete.json) | 2026-11-14T06:09:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060924-solder-mask-delete.json](evidence/2026-11-14-060924-solder-mask-delete.json) | 2026-11-14T06:09:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060915-solder-mask-delete.json](evidence/2026-11-14-060915-solder-mask-delete.json) | 2026-11-14T06:09:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060911-solder-mask-delete.json](evidence/2026-11-14-060911-solder-mask-delete.json) | 2026-11-14T06:09:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060907-solder-mask-delete.json](evidence/2026-11-14-060907-solder-mask-delete.json) | 2026-11-14T06:09:07Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-060901-solder-mask-create.json](evidence/2026-11-14-060901-solder-mask-create.json) | 2026-11-14T06:09:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060854-solder-mask-create.json](evidence/2026-11-14-060854-solder-mask-create.json) | 2026-11-14T06:08:54Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060849-solder-mask-create.json](evidence/2026-11-14-060849-solder-mask-create.json) | 2026-11-14T06:08:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060843-solder-mask-create.json](evidence/2026-11-14-060843-solder-mask-create.json) | 2026-11-14T06:08:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060838-solder-mask-create.json](evidence/2026-11-14-060838-solder-mask-create.json) | 2026-11-14T06:08:38Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-060826-plane-delete.json](evidence/2026-11-14-060826-plane-delete.json) | 2026-11-14T06:08:26Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060822-plane-delete.json](evidence/2026-11-14-060822-plane-delete.json) | 2026-11-14T06:08:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060812-plane-delete.json](evidence/2026-11-14-060812-plane-delete.json) | 2026-11-14T06:08:12Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060807-plane-delete.json](evidence/2026-11-14-060807-plane-delete.json) | 2026-11-14T06:08:07Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060802-plane-delete.json](evidence/2026-11-14-060802-plane-delete.json) | 2026-11-14T06:08:02Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-14-060755-constraint-get.json](evidence/2026-11-14-060755-constraint-get.json) | 2026-11-14T06:07:55Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-14-060749-pcb-check.json](evidence/2026-11-14-060749-pcb-check.json) | 2026-11-14T06:07:49Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-14-060730-pcb-get.json](evidence/2026-11-14-060730-pcb-get.json) | 2026-11-14T06:07:30Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-14-060724-router-run.json](evidence/2026-11-14-060724-router-run.json) | 2026-11-14T06:07:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060719-router-run.json](evidence/2026-11-14-060719-router-run.json) | 2026-11-14T06:07:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060709-router-run.json](evidence/2026-11-14-060709-router-run.json) | 2026-11-14T06:07:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060701-router-run.json](evidence/2026-11-14-060701-router-run.json) | 2026-11-14T06:07:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060647-router-run.json](evidence/2026-11-14-060647-router-run.json) | 2026-11-14T06:06:47Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060642-router-run.json](evidence/2026-11-14-060642-router-run.json) | 2026-11-14T06:06:42Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-060636-plane-create.json](evidence/2026-11-14-060636-plane-create.json) | 2026-11-14T06:06:36Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060628-plane-create.json](evidence/2026-11-14-060628-plane-create.json) | 2026-11-14T06:06:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060624-plane-create.json](evidence/2026-11-14-060624-plane-create.json) | 2026-11-14T06:06:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060620-plane-create.json](evidence/2026-11-14-060620-plane-create.json) | 2026-11-14T06:06:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060616-plane-create.json](evidence/2026-11-14-060616-plane-create.json) | 2026-11-14T06:06:16Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-060609-placement-run.json](evidence/2026-11-14-060609-placement-run.json) | 2026-11-14T06:06:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060602-doc-save.json](evidence/2026-11-14-060602-doc-save.json) | 2026-11-14T06:06:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060458-placement-run.json](evidence/2026-11-14-060458-placement-run.json) | 2026-11-14T06:04:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060449-placement-run.json](evidence/2026-11-14-060449-placement-run.json) | 2026-11-14T06:04:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060443-placement-run.json](evidence/2026-11-14-060443-placement-run.json) | 2026-11-14T06:04:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060428-placement-run.json](evidence/2026-11-14-060428-placement-run.json) | 2026-11-14T06:04:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060421-doc-close.json](evidence/2026-11-14-060421-doc-close.json) | 2026-11-14T06:04:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060417-doc-open.json](evidence/2026-11-14-060417-doc-open.json) | 2026-11-14T06:04:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060410-placement-run.json](evidence/2026-11-14-060410-placement-run.json) | 2026-11-14T06:04:10Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-060335-doc-close.json](evidence/2026-11-14-060335-doc-close.json) | 2026-11-14T06:03:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060308-doc-open.json](evidence/2026-11-14-060308-doc-open.json) | 2026-11-14T06:03:08Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060302-doc-open.json](evidence/2026-11-14-060302-doc-open.json) | 2026-11-14T06:03:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060241-doc-open.json](evidence/2026-11-14-060241-doc-open.json) | 2026-11-14T06:02:41Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060216-doc-open.json](evidence/2026-11-14-060216-doc-open.json) | 2026-11-14T06:02:16Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-060207-doc-close.json](evidence/2026-11-14-060207-doc-close.json) | 2026-11-14T06:02:07Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-060158-doc-close.json](evidence/2026-11-14-060158-doc-close.json) | 2026-11-14T06:01:58Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-060122-forward-annotation-run.json](evidence/2026-11-14-060122-forward-annotation-run.json) | 2026-11-14T06:01:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055835-doc-save.json](evidence/2026-11-14-055835-doc-save.json) | 2026-11-14T05:58:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055758-doc-save.json](evidence/2026-11-14-055758-doc-save.json) | 2026-11-14T05:57:58Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-14-055725-doc-save.json](evidence/2026-11-14-055725-doc-save.json) | 2026-11-14T05:57:25Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055653-pcb-update.json](evidence/2026-11-14-055653-pcb-update.json) | 2026-11-14T05:56:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055646-pcb-update.json](evidence/2026-11-14-055646-pcb-update.json) | 2026-11-14T05:56:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055642-pcb-update.json](evidence/2026-11-14-055642-pcb-update.json) | 2026-11-14T05:56:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055639-pcb-update.json](evidence/2026-11-14-055639-pcb-update.json) | 2026-11-14T05:56:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055635-pcb-update.json](evidence/2026-11-14-055635-pcb-update.json) | 2026-11-14T05:56:35Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-055619-doc-undo.json](evidence/2026-11-14-055619-doc-undo.json) | 2026-11-14T05:56:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055615-doc-save.json](evidence/2026-11-14-055615-doc-save.json) | 2026-11-14T05:56:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055611-doc-open.json](evidence/2026-11-14-055611-doc-open.json) | 2026-11-14T05:56:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055607-doc-close.json](evidence/2026-11-14-055607-doc-close.json) | 2026-11-14T05:56:07Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055604-forward-annotation-run.json](evidence/2026-11-14-055604-forward-annotation-run.json) | 2026-11-14T05:56:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055548-forward-annotation-run.json](evidence/2026-11-14-055548-forward-annotation-run.json) | 2026-11-14T05:55:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055123-forward-annotation-run.json](evidence/2026-11-14-055123-forward-annotation-run.json) | 2026-11-14T05:51:23Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055116-forward-annotation-run.json](evidence/2026-11-14-055116-forward-annotation-run.json) | 2026-11-14T05:51:16Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055059-forward-annotation-run.json](evidence/2026-11-14-055059-forward-annotation-run.json) | 2026-11-14T05:50:59Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-055053-constraint-get.json](evidence/2026-11-14-055053-constraint-get.json) | 2026-11-14T05:50:53Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-055048-pcb-check.json](evidence/2026-11-14-055048-pcb-check.json) | 2026-11-14T05:50:48Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-055037-pcb-get.json](evidence/2026-11-14-055037-pcb-get.json) | 2026-11-14T05:50:37Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-055005-pcb-create.json](evidence/2026-11-14-055005-pcb-create.json) | 2026-11-14T05:50:05Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-055001-pcb-create.json](evidence/2026-11-14-055001-pcb-create.json) | 2026-11-14T05:50:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054950-pcb-create.json](evidence/2026-11-14-054950-pcb-create.json) | 2026-11-14T05:49:50Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-054942-pcb-create.json](evidence/2026-11-14-054942-pcb-create.json) | 2026-11-14T05:49:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054742-packager-run.json](evidence/2026-11-14-054742-packager-run.json) | 2026-11-14T05:47:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054732-packager-run.json](evidence/2026-11-14-054732-packager-run.json) | 2026-11-14T05:47:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054720-packager-run.json](evidence/2026-11-14-054720-packager-run.json) | 2026-11-14T05:47:20Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-054556-schematic-export.json](evidence/2026-11-14-054556-schematic-export.json) | 2026-11-14T05:45:56Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054548-schematic-create.json](evidence/2026-11-14-054548-schematic-create.json) | 2026-11-14T05:45:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054533-session-close.json](evidence/2026-11-14-054533-session-close.json) | 2026-11-14T05:45:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054529-bom-export.json](evidence/2026-11-14-054529-bom-export.json) | 2026-11-14T05:45:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054525-bom-check.json](evidence/2026-11-14-054525-bom-check.json) | 2026-11-14T05:45:25Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054522-net-get.json](evidence/2026-11-14-054522-net-get.json) | 2026-11-14T05:45:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054519-net-list.json](evidence/2026-11-14-054519-net-list.json) | 2026-11-14T05:45:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054516-component-get.json](evidence/2026-11-14-054516-component-get.json) | 2026-11-14T05:45:16Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054513-component-list.json](evidence/2026-11-14-054513-component-list.json) | 2026-11-14T05:45:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054510-doc-create.json](evidence/2026-11-14-054510-doc-create.json) | 2026-11-14T05:45:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054506-session-list.json](evidence/2026-11-14-054506-session-list.json) | 2026-11-14T05:45:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054503-session-get.json](evidence/2026-11-14-054503-session-get.json) | 2026-11-14T05:45:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054501-doc-list.json](evidence/2026-11-14-054501-doc-list.json) | 2026-11-14T05:45:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054457-doc-get.json](evidence/2026-11-14-054457-doc-get.json) | 2026-11-14T05:44:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054454-schematic-create.json](evidence/2026-11-14-054454-schematic-create.json) | 2026-11-14T05:44:54Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054451-schematic-export.json](evidence/2026-11-14-054451-schematic-export.json) | 2026-11-14T05:44:51Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054447-schematic-check.json](evidence/2026-11-14-054447-schematic-check.json) | 2026-11-14T05:44:47Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054445-schematic-get.json](evidence/2026-11-14-054445-schematic-get.json) | 2026-11-14T05:44:45Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054407-bom-export.json](evidence/2026-11-14-054407-bom-export.json) | 2026-11-14T05:44:07Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054400-bom-export.json](evidence/2026-11-14-054400-bom-export.json) | 2026-11-14T05:44:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054354-bom-export.json](evidence/2026-11-14-054354-bom-export.json) | 2026-11-14T05:43:54Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054349-bom-check.json](evidence/2026-11-14-054349-bom-check.json) | 2026-11-14T05:43:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054346-net-get.json](evidence/2026-11-14-054346-net-get.json) | 2026-11-14T05:43:46Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-054341-net-list.json](evidence/2026-11-14-054341-net-list.json) | 2026-11-14T05:43:41Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054338-component-get.json](evidence/2026-11-14-054338-component-get.json) | 2026-11-14T05:43:38Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-054330-schematic-export.json](evidence/2026-11-14-054330-schematic-export.json) | 2026-11-14T05:43:30Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054323-schematic-export.json](evidence/2026-11-14-054323-schematic-export.json) | 2026-11-14T05:43:23Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-14-054313-schematic-check.json](evidence/2026-11-14-054313-schematic-check.json) | 2026-11-14T05:43:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054308-schematic-get.json](evidence/2026-11-14-054308-schematic-get.json) | 2026-11-14T05:43:08Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054215-session-close.json](evidence/2026-11-14-054215-session-close.json) | 2026-11-14T05:42:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054200-bom-export.json](evidence/2026-11-14-054200-bom-export.json) | 2026-11-14T05:42:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054145-bom-check.json](evidence/2026-11-14-054145-bom-check.json) | 2026-11-14T05:41:45Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054131-net-get.json](evidence/2026-11-14-054131-net-get.json) | 2026-11-14T05:41:31Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054115-net-list.json](evidence/2026-11-14-054115-net-list.json) | 2026-11-14T05:41:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054102-component-get.json](evidence/2026-11-14-054102-component-get.json) | 2026-11-14T05:41:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054047-component-list.json](evidence/2026-11-14-054047-component-list.json) | 2026-11-14T05:40:47Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054034-doc-create.json](evidence/2026-11-14-054034-doc-create.json) | 2026-11-14T05:40:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054018-session-get.json](evidence/2026-11-14-054018-session-get.json) | 2026-11-14T05:40:18Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-054004-doc-get.json](evidence/2026-11-14-054004-doc-get.json) | 2026-11-14T05:40:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053952-schematic-create.json](evidence/2026-11-14-053952-schematic-create.json) | 2026-11-14T05:39:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053935-schematic-export.json](evidence/2026-11-14-053935-schematic-export.json) | 2026-11-14T05:39:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053920-schematic-check.json](evidence/2026-11-14-053920-schematic-check.json) | 2026-11-14T05:39:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053906-schematic-get.json](evidence/2026-11-14-053906-schematic-get.json) | 2026-11-14T05:39:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053658-schematic-create.json](evidence/2026-11-14-053658-schematic-create.json) | 2026-11-14T05:36:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053643-session-close.json](evidence/2026-11-14-053643-session-close.json) | 2026-11-14T05:36:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053640-schematic-export.json](evidence/2026-11-14-053640-schematic-export.json) | 2026-11-14T05:36:40Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053634-padstack-list.json](evidence/2026-11-14-053634-padstack-list.json) | 2026-11-14T05:36:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053632-part-get.json](evidence/2026-11-14-053632-part-get.json) | 2026-11-14T05:36:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053629-part-list.json](evidence/2026-11-14-053629-part-list.json) | 2026-11-14T05:36:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053626-project-get.json](evidence/2026-11-14-053626-project-get.json) | 2026-11-14T05:36:26Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053623-bom-export.json](evidence/2026-11-14-053623-bom-export.json) | 2026-11-14T05:36:23Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053617-bom-check.json](evidence/2026-11-14-053617-bom-check.json) | 2026-11-14T05:36:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053614-net-get.json](evidence/2026-11-14-053614-net-get.json) | 2026-11-14T05:36:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053611-net-list.json](evidence/2026-11-14-053611-net-list.json) | 2026-11-14T05:36:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053608-component-get.json](evidence/2026-11-14-053608-component-get.json) | 2026-11-14T05:36:08Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053604-component-list.json](evidence/2026-11-14-053604-component-list.json) | 2026-11-14T05:36:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053601-doc-list.json](evidence/2026-11-14-053601-doc-list.json) | 2026-11-14T05:36:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053558-doc-get.json](evidence/2026-11-14-053558-doc-get.json) | 2026-11-14T05:35:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053556-schematic-check.json](evidence/2026-11-14-053556-schematic-check.json) | 2026-11-14T05:35:56Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053551-schematic-get.json](evidence/2026-11-14-053551-schematic-get.json) | 2026-11-14T05:35:51Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053548-schematic-create.json](evidence/2026-11-14-053548-schematic-create.json) | 2026-11-14T05:35:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053534-schematic-create.json](evidence/2026-11-14-053534-schematic-create.json) | 2026-11-14T05:35:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053517-schematic-create.json](evidence/2026-11-14-053517-schematic-create.json) | 2026-11-14T05:35:17Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-053510-pcb-create.json](evidence/2026-11-14-053510-pcb-create.json) | 2026-11-14T05:35:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053506-packager-run.json](evidence/2026-11-14-053506-packager-run.json) | 2026-11-14T05:35:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053503-padstack-create.json](evidence/2026-11-14-053503-padstack-create.json) | 2026-11-14T05:35:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053459-part-create.json](evidence/2026-11-14-053459-part-create.json) | 2026-11-14T05:34:59Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053456-doc-list.json](evidence/2026-11-14-053456-doc-list.json) | 2026-11-14T05:34:56Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-053452-session-get.json](evidence/2026-11-14-053452-session-get.json) | 2026-11-14T05:34:52Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-053448-session-open.json](evidence/2026-11-14-053448-session-open.json) | 2026-11-14T05:34:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053443-session-open.json](evidence/2026-11-14-053443-session-open.json) | 2026-11-14T05:34:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053402-session-open.json](evidence/2026-11-14-053402-session-open.json) | 2026-11-14T05:34:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053323-session-open.json](evidence/2026-11-14-053323-session-open.json) | 2026-11-14T05:33:23Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-053315-padstack-create.json](evidence/2026-11-14-053315-padstack-create.json) | 2026-11-14T05:33:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053302-padstack-create.json](evidence/2026-11-14-053302-padstack-create.json) | 2026-11-14T05:33:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053254-padstack-create.json](evidence/2026-11-14-053254-padstack-create.json) | 2026-11-14T05:32:54Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053245-padstack-create.json](evidence/2026-11-14-053245-padstack-create.json) | 2026-11-14T05:32:45Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-053237-padstack-list.json](evidence/2026-11-14-053237-padstack-list.json) | 2026-11-14T05:32:37Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-14-053230-part-get.json](evidence/2026-11-14-053230-part-get.json) | 2026-11-14T05:32:30Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-053223-part-list.json](evidence/2026-11-14-053223-part-list.json) | 2026-11-14T05:32:23Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-14-053218-part-create.json](evidence/2026-11-14-053218-part-create.json) | 2026-11-14T05:32:18Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053206-part-create.json](evidence/2026-11-14-053206-part-create.json) | 2026-11-14T05:32:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053157-part-create.json](evidence/2026-11-14-053157-part-create.json) | 2026-11-14T05:31:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053146-part-create.json](evidence/2026-11-14-053146-part-create.json) | 2026-11-14T05:31:46Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-053137-project-get.json](evidence/2026-11-14-053137-project-get.json) | 2026-11-14T05:31:37Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-053132-session-close.json](evidence/2026-11-14-053132-session-close.json) | 2026-11-14T05:31:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-053116-session-close.json](evidence/2026-11-14-053116-session-close.json) | 2026-11-14T05:31:16Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-14-053113-doc-create.json](evidence/2026-11-14-053113-doc-create.json) | 2026-11-14T05:31:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-052941-session-list.json](evidence/2026-11-14-052941-session-list.json) | 2026-11-14T05:29:41Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-052934-doc-get.json](evidence/2026-11-14-052934-doc-get.json) | 2026-11-14T05:29:34Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-052930-bom-export.json](evidence/2026-11-14-052930-bom-export.json) | 2026-11-14T05:29:30Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-14-052922-bom-check.json](evidence/2026-11-14-052922-bom-check.json) | 2026-11-14T05:29:22Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-052918-net-list.json](evidence/2026-11-14-052918-net-list.json) | 2026-11-14T05:29:18Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-052914-component-list.json](evidence/2026-11-14-052914-component-list.json) | 2026-11-14T05:29:14Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-052910-schematic-check.json](evidence/2026-11-14-052910-schematic-check.json) | 2026-11-14T05:29:10Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-052905-schematic-get.json](evidence/2026-11-14-052905-schematic-get.json) | 2026-11-14T05:29:05Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-14-052901-doc-create.json](evidence/2026-11-14-052901-doc-create.json) | 2026-11-14T05:29:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-052858-doc-create.json](evidence/2026-11-14-052858-doc-create.json) | 2026-11-14T05:28:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-052853-doc-create.json](evidence/2026-11-14-052853-doc-create.json) | 2026-11-14T05:28:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-14-052737-doc-create.json](evidence/2026-11-14-052737-doc-create.json) | 2026-11-14T05:27:37Z | Xpedition 2604 | windows | 3 of 3 runs passed |
<!-- driver:evidence:end -->
