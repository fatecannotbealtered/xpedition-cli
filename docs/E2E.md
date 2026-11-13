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
| [2026-11-13-171859-component-get.json](evidence/2026-11-13-171859-component-get.json) | 2026-11-13T17:18:59Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171856-component-list.json](evidence/2026-11-13-171856-component-list.json) | 2026-11-13T17:18:56Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171811-component-get.json](evidence/2026-11-13-171811-component-get.json) | 2026-11-13T17:18:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171808-component-list.json](evidence/2026-11-13-171808-component-list.json) | 2026-11-13T17:18:08Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171727-via-update.json](evidence/2026-11-13-171727-via-update.json) | 2026-11-13T17:17:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171640-via-update.json](evidence/2026-11-13-171640-via-update.json) | 2026-11-13T17:16:40Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171632-via-update.json](evidence/2026-11-13-171632-via-update.json) | 2026-11-13T17:16:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171626-via-update.json](evidence/2026-11-13-171626-via-update.json) | 2026-11-13T17:16:26Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171622-via-update.json](evidence/2026-11-13-171622-via-update.json) | 2026-11-13T17:16:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171618-via-update.json](evidence/2026-11-13-171618-via-update.json) | 2026-11-13T17:16:18Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171550-via-delete.json](evidence/2026-11-13-171550-via-delete.json) | 2026-11-13T17:15:50Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171527-via-delete.json](evidence/2026-11-13-171527-via-delete.json) | 2026-11-13T17:15:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171506-via-delete.json](evidence/2026-11-13-171506-via-delete.json) | 2026-11-13T17:15:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171502-via-delete.json](evidence/2026-11-13-171502-via-delete.json) | 2026-11-13T17:15:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171442-trace-delete.json](evidence/2026-11-13-171442-trace-delete.json) | 2026-11-13T17:14:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171428-trace-delete.json](evidence/2026-11-13-171428-trace-delete.json) | 2026-11-13T17:14:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171417-trace-delete.json](evidence/2026-11-13-171417-trace-delete.json) | 2026-11-13T17:14:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171413-trace-delete.json](evidence/2026-11-13-171413-trace-delete.json) | 2026-11-13T17:14:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171348-via-delete.json](evidence/2026-11-13-171348-via-delete.json) | 2026-11-13T17:13:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171345-trace-delete.json](evidence/2026-11-13-171345-trace-delete.json) | 2026-11-13T17:13:45Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171342-trace-create.json](evidence/2026-11-13-171342-trace-create.json) | 2026-11-13T17:13:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171338-constraint-import.json](evidence/2026-11-13-171338-constraint-import.json) | 2026-11-13T17:13:38Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-171229-via-update.json](evidence/2026-11-13-171229-via-update.json) | 2026-11-13T17:12:29Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-171114-via-delete.json](evidence/2026-11-13-171114-via-delete.json) | 2026-11-13T17:11:14Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-171016-trace-delete.json](evidence/2026-11-13-171016-trace-delete.json) | 2026-11-13T17:10:16Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-171003-trace-create.json](evidence/2026-11-13-171003-trace-create.json) | 2026-11-13T17:10:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170949-trace-check.json](evidence/2026-11-13-170949-trace-check.json) | 2026-11-13T17:09:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170945-trace-create.json](evidence/2026-11-13-170945-trace-create.json) | 2026-11-13T17:09:45Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170933-trace-create.json](evidence/2026-11-13-170933-trace-create.json) | 2026-11-13T17:09:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170929-trace-check.json](evidence/2026-11-13-170929-trace-check.json) | 2026-11-13T17:09:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170925-trace-create.json](evidence/2026-11-13-170925-trace-create.json) | 2026-11-13T17:09:25Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170916-trace-check.json](evidence/2026-11-13-170916-trace-check.json) | 2026-11-13T17:09:16Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170912-trace-check.json](evidence/2026-11-13-170912-trace-check.json) | 2026-11-13T17:09:12Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-13-170907-trace-create.json](evidence/2026-11-13-170907-trace-create.json) | 2026-11-13T17:09:07Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-170857-constraint-import.json](evidence/2026-11-13-170857-constraint-import.json) | 2026-11-13T17:08:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170843-constraint-import.json](evidence/2026-11-13-170843-constraint-import.json) | 2026-11-13T17:08:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170821-constraint-import.json](evidence/2026-11-13-170821-constraint-import.json) | 2026-11-13T17:08:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170811-constraint-import.json](evidence/2026-11-13-170811-constraint-import.json) | 2026-11-13T17:08:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170802-constraint-import.json](evidence/2026-11-13-170802-constraint-import.json) | 2026-11-13T17:08:02Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-170722-refdes-move.json](evidence/2026-11-13-170722-refdes-move.json) | 2026-11-13T17:07:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170717-refdes-move.json](evidence/2026-11-13-170717-refdes-move.json) | 2026-11-13T17:07:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170710-refdes-move.json](evidence/2026-11-13-170710-refdes-move.json) | 2026-11-13T17:07:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170706-refdes-move.json](evidence/2026-11-13-170706-refdes-move.json) | 2026-11-13T17:07:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170703-refdes-move.json](evidence/2026-11-13-170703-refdes-move.json) | 2026-11-13T17:07:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170659-refdes-move.json](evidence/2026-11-13-170659-refdes-move.json) | 2026-11-13T17:06:59Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-170625-mounting-hole-delete.json](evidence/2026-11-13-170625-mounting-hole-delete.json) | 2026-11-13T17:06:25Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170618-mounting-hole-delete.json](evidence/2026-11-13-170618-mounting-hole-delete.json) | 2026-11-13T17:06:18Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170613-mounting-hole-delete.json](evidence/2026-11-13-170613-mounting-hole-delete.json) | 2026-11-13T17:06:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170609-mounting-hole-delete.json](evidence/2026-11-13-170609-mounting-hole-delete.json) | 2026-11-13T17:06:09Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-170601-mounting-hole-delete.json](evidence/2026-11-13-170601-mounting-hole-delete.json) | 2026-11-13T17:06:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170557-mounting-hole-create.json](evidence/2026-11-13-170557-mounting-hole-create.json) | 2026-11-13T17:05:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170550-mounting-hole-create.json](evidence/2026-11-13-170550-mounting-hole-create.json) | 2026-11-13T17:05:50Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170547-mounting-hole-create.json](evidence/2026-11-13-170547-mounting-hole-create.json) | 2026-11-13T17:05:47Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170543-mounting-hole-list.json](evidence/2026-11-13-170543-mounting-hole-list.json) | 2026-11-13T17:05:43Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-13-170539-mounting-hole-create.json](evidence/2026-11-13-170539-mounting-hole-create.json) | 2026-11-13T17:05:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170535-mounting-hole-create.json](evidence/2026-11-13-170535-mounting-hole-create.json) | 2026-11-13T17:05:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170531-mounting-hole-create.json](evidence/2026-11-13-170531-mounting-hole-create.json) | 2026-11-13T17:05:31Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-170522-mounting-hole-delete.json](evidence/2026-11-13-170522-mounting-hole-delete.json) | 2026-11-13T17:05:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170518-mounting-hole-list.json](evidence/2026-11-13-170518-mounting-hole-list.json) | 2026-11-13T17:05:18Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-170444-doc-undo.json](evidence/2026-11-13-170444-doc-undo.json) | 2026-11-13T17:04:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170434-doc-undo.json](evidence/2026-11-13-170434-doc-undo.json) | 2026-11-13T17:04:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170428-doc-undo.json](evidence/2026-11-13-170428-doc-undo.json) | 2026-11-13T17:04:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170422-doc-undo.json](evidence/2026-11-13-170422-doc-undo.json) | 2026-11-13T17:04:22Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-170414-component-move.json](evidence/2026-11-13-170414-component-move.json) | 2026-11-13T17:04:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170408-component-move.json](evidence/2026-11-13-170408-component-move.json) | 2026-11-13T17:04:08Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170404-component-move.json](evidence/2026-11-13-170404-component-move.json) | 2026-11-13T17:04:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170400-component-move.json](evidence/2026-11-13-170400-component-move.json) | 2026-11-13T17:04:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170357-component-move.json](evidence/2026-11-13-170357-component-move.json) | 2026-11-13T17:03:57Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-13-170300-refdes-move.json](evidence/2026-11-13-170300-refdes-move.json) | 2026-11-13T17:03:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170256-mounting-hole-delete.json](evidence/2026-11-13-170256-mounting-hole-delete.json) | 2026-11-13T17:02:56Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170253-mounting-hole-create.json](evidence/2026-11-13-170253-mounting-hole-create.json) | 2026-11-13T17:02:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170249-mounting-hole-list.json](evidence/2026-11-13-170249-mounting-hole-list.json) | 2026-11-13T17:02:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170247-doc-undo.json](evidence/2026-11-13-170247-doc-undo.json) | 2026-11-13T17:02:47Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170243-component-move.json](evidence/2026-11-13-170243-component-move.json) | 2026-11-13T17:02:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170240-doc-save.json](evidence/2026-11-13-170240-doc-save.json) | 2026-11-13T17:02:40Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170236-doc-open.json](evidence/2026-11-13-170236-doc-open.json) | 2026-11-13T17:02:36Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170233-doc-close.json](evidence/2026-11-13-170233-doc-close.json) | 2026-11-13T17:02:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170230-keepout-delete.json](evidence/2026-11-13-170230-keepout-delete.json) | 2026-11-13T17:02:30Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170226-keepout-create.json](evidence/2026-11-13-170226-keepout-create.json) | 2026-11-13T17:02:26Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170223-silkscreen-text-delete.json](evidence/2026-11-13-170223-silkscreen-text-delete.json) | 2026-11-13T17:02:23Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170219-silkscreen-text-create.json](evidence/2026-11-13-170219-silkscreen-text-create.json) | 2026-11-13T17:02:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170216-solder-mask-delete.json](evidence/2026-11-13-170216-solder-mask-delete.json) | 2026-11-13T17:02:16Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170213-solder-mask-create.json](evidence/2026-11-13-170213-solder-mask-create.json) | 2026-11-13T17:02:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170209-via-update.json](evidence/2026-11-13-170209-via-update.json) | 2026-11-13T17:02:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170206-via-delete.json](evidence/2026-11-13-170206-via-delete.json) | 2026-11-13T17:02:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170202-via-create.json](evidence/2026-11-13-170202-via-create.json) | 2026-11-13T17:02:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170159-trace-delete.json](evidence/2026-11-13-170159-trace-delete.json) | 2026-11-13T17:01:59Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170155-trace-check.json](evidence/2026-11-13-170155-trace-check.json) | 2026-11-13T17:01:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170153-trace-create.json](evidence/2026-11-13-170153-trace-create.json) | 2026-11-13T17:01:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170149-constraint-import.json](evidence/2026-11-13-170149-constraint-import.json) | 2026-11-13T17:01:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170146-constraint-get.json](evidence/2026-11-13-170146-constraint-get.json) | 2026-11-13T17:01:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170143-pcb-render.json](evidence/2026-11-13-170143-pcb-render.json) | 2026-11-13T17:01:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170140-pcb-export.json](evidence/2026-11-13-170140-pcb-export.json) | 2026-11-13T17:01:40Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170137-pcb-check.json](evidence/2026-11-13-170137-pcb-check.json) | 2026-11-13T17:01:37Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170134-pcb-get.json](evidence/2026-11-13-170134-pcb-get.json) | 2026-11-13T17:01:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170131-router-run.json](evidence/2026-11-13-170131-router-run.json) | 2026-11-13T17:01:31Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170128-plane-delete.json](evidence/2026-11-13-170128-plane-delete.json) | 2026-11-13T17:01:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170124-plane-create.json](evidence/2026-11-13-170124-plane-create.json) | 2026-11-13T17:01:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170121-placement-run.json](evidence/2026-11-13-170121-placement-run.json) | 2026-11-13T17:01:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170117-pcb-update.json](evidence/2026-11-13-170117-pcb-update.json) | 2026-11-13T17:01:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170114-forward-annotation-run.json](evidence/2026-11-13-170114-forward-annotation-run.json) | 2026-11-13T17:01:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170046-silkscreen-text-create.json](evidence/2026-11-13-170046-silkscreen-text-create.json) | 2026-11-13T17:00:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170032-doc-undo.json](evidence/2026-11-13-170032-doc-undo.json) | 2026-11-13T17:00:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170026-refdes-move.json](evidence/2026-11-13-170026-refdes-move.json) | 2026-11-13T17:00:26Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170021-mounting-hole-delete.json](evidence/2026-11-13-170021-mounting-hole-delete.json) | 2026-11-13T17:00:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170015-mounting-hole-create.json](evidence/2026-11-13-170015-mounting-hole-create.json) | 2026-11-13T17:00:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-170001-mounting-hole-list.json](evidence/2026-11-13-170001-mounting-hole-list.json) | 2026-11-13T17:00:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165955-component-move.json](evidence/2026-11-13-165955-component-move.json) | 2026-11-13T16:59:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165950-doc-save.json](evidence/2026-11-13-165950-doc-save.json) | 2026-11-13T16:59:50Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165945-doc-open.json](evidence/2026-11-13-165945-doc-open.json) | 2026-11-13T16:59:45Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165929-doc-close.json](evidence/2026-11-13-165929-doc-close.json) | 2026-11-13T16:59:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165924-keepout-delete.json](evidence/2026-11-13-165924-keepout-delete.json) | 2026-11-13T16:59:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165919-keepout-create.json](evidence/2026-11-13-165919-keepout-create.json) | 2026-11-13T16:59:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165913-silkscreen-text-delete.json](evidence/2026-11-13-165913-silkscreen-text-delete.json) | 2026-11-13T16:59:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165855-solder-mask-delete.json](evidence/2026-11-13-165855-solder-mask-delete.json) | 2026-11-13T16:58:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165849-solder-mask-create.json](evidence/2026-11-13-165849-solder-mask-create.json) | 2026-11-13T16:58:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165844-via-update.json](evidence/2026-11-13-165844-via-update.json) | 2026-11-13T16:58:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165824-doc-list.json](evidence/2026-11-13-165824-doc-list.json) | 2026-11-13T16:58:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165819-session-list.json](evidence/2026-11-13-165819-session-list.json) | 2026-11-13T16:58:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165815-via-delete.json](evidence/2026-11-13-165815-via-delete.json) | 2026-11-13T16:58:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165800-via-create.json](evidence/2026-11-13-165800-via-create.json) | 2026-11-13T16:58:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165754-trace-delete.json](evidence/2026-11-13-165754-trace-delete.json) | 2026-11-13T16:57:54Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165749-trace-check.json](evidence/2026-11-13-165749-trace-check.json) | 2026-11-13T16:57:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165744-trace-create.json](evidence/2026-11-13-165744-trace-create.json) | 2026-11-13T16:57:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165730-constraint-import.json](evidence/2026-11-13-165730-constraint-import.json) | 2026-11-13T16:57:30Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165724-constraint-get.json](evidence/2026-11-13-165724-constraint-get.json) | 2026-11-13T16:57:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165719-pcb-render.json](evidence/2026-11-13-165719-pcb-render.json) | 2026-11-13T16:57:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165714-pcb-export.json](evidence/2026-11-13-165714-pcb-export.json) | 2026-11-13T16:57:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165658-pcb-check.json](evidence/2026-11-13-165658-pcb-check.json) | 2026-11-13T16:56:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165654-pcb-get.json](evidence/2026-11-13-165654-pcb-get.json) | 2026-11-13T16:56:54Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165649-router-run.json](evidence/2026-11-13-165649-router-run.json) | 2026-11-13T16:56:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165643-plane-delete.json](evidence/2026-11-13-165643-plane-delete.json) | 2026-11-13T16:56:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165629-plane-create.json](evidence/2026-11-13-165629-plane-create.json) | 2026-11-13T16:56:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165624-placement-run.json](evidence/2026-11-13-165624-placement-run.json) | 2026-11-13T16:56:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165619-pcb-update.json](evidence/2026-11-13-165619-pcb-update.json) | 2026-11-13T16:56:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165614-forward-annotation-run.json](evidence/2026-11-13-165614-forward-annotation-run.json) | 2026-11-13T16:56:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165602-session-close.json](evidence/2026-11-13-165602-session-close.json) | 2026-11-13T16:56:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165558-doc-list.json](evidence/2026-11-13-165558-doc-list.json) | 2026-11-13T16:55:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165555-component-move.json](evidence/2026-11-13-165555-component-move.json) | 2026-11-13T16:55:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165552-via-update.json](evidence/2026-11-13-165552-via-update.json) | 2026-11-13T16:55:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165548-via-delete.json](evidence/2026-11-13-165548-via-delete.json) | 2026-11-13T16:55:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165544-via-create.json](evidence/2026-11-13-165544-via-create.json) | 2026-11-13T16:55:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165541-trace-delete.json](evidence/2026-11-13-165541-trace-delete.json) | 2026-11-13T16:55:41Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165538-trace-check.json](evidence/2026-11-13-165538-trace-check.json) | 2026-11-13T16:55:38Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165535-trace-create.json](evidence/2026-11-13-165535-trace-create.json) | 2026-11-13T16:55:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165532-keepout-delete.json](evidence/2026-11-13-165532-keepout-delete.json) | 2026-11-13T16:55:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165528-keepout-create.json](evidence/2026-11-13-165528-keepout-create.json) | 2026-11-13T16:55:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165525-silkscreen-text-delete.json](evidence/2026-11-13-165525-silkscreen-text-delete.json) | 2026-11-13T16:55:25Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165521-silkscreen-text-create.json](evidence/2026-11-13-165521-silkscreen-text-create.json) | 2026-11-13T16:55:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165518-solder-mask-delete.json](evidence/2026-11-13-165518-solder-mask-delete.json) | 2026-11-13T16:55:18Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165514-solder-mask-create.json](evidence/2026-11-13-165514-solder-mask-create.json) | 2026-11-13T16:55:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165511-router-run.json](evidence/2026-11-13-165511-router-run.json) | 2026-11-13T16:55:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165507-refdes-move.json](evidence/2026-11-13-165507-refdes-move.json) | 2026-11-13T16:55:07Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165503-plane-delete.json](evidence/2026-11-13-165503-plane-delete.json) | 2026-11-13T16:55:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165459-plane-create.json](evidence/2026-11-13-165459-plane-create.json) | 2026-11-13T16:54:59Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165456-placement-run.json](evidence/2026-11-13-165456-placement-run.json) | 2026-11-13T16:54:56Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165452-pcb-update.json](evidence/2026-11-13-165452-pcb-update.json) | 2026-11-13T16:54:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165449-pcb-render.json](evidence/2026-11-13-165449-pcb-render.json) | 2026-11-13T16:54:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165445-pcb-get.json](evidence/2026-11-13-165445-pcb-get.json) | 2026-11-13T16:54:45Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165443-pcb-export.json](evidence/2026-11-13-165443-pcb-export.json) | 2026-11-13T16:54:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165439-pcb-check.json](evidence/2026-11-13-165439-pcb-check.json) | 2026-11-13T16:54:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165436-mounting-hole-delete.json](evidence/2026-11-13-165436-mounting-hole-delete.json) | 2026-11-13T16:54:36Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165433-mounting-hole-create.json](evidence/2026-11-13-165433-mounting-hole-create.json) | 2026-11-13T16:54:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165430-mounting-hole-list.json](evidence/2026-11-13-165430-mounting-hole-list.json) | 2026-11-13T16:54:30Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165427-forward-annotation-run.json](evidence/2026-11-13-165427-forward-annotation-run.json) | 2026-11-13T16:54:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165423-doc-undo.json](evidence/2026-11-13-165423-doc-undo.json) | 2026-11-13T16:54:23Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165420-doc-save.json](evidence/2026-11-13-165420-doc-save.json) | 2026-11-13T16:54:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165416-doc-open.json](evidence/2026-11-13-165416-doc-open.json) | 2026-11-13T16:54:16Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165413-doc-close.json](evidence/2026-11-13-165413-doc-close.json) | 2026-11-13T16:54:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165409-constraint-import.json](evidence/2026-11-13-165409-constraint-import.json) | 2026-11-13T16:54:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165406-constraint-get.json](evidence/2026-11-13-165406-constraint-get.json) | 2026-11-13T16:54:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165403-session-close.json](evidence/2026-11-13-165403-session-close.json) | 2026-11-13T16:54:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165400-session-get.json](evidence/2026-11-13-165400-session-get.json) | 2026-11-13T16:54:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165357-schematic-get.json](evidence/2026-11-13-165357-schematic-get.json) | 2026-11-13T16:53:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165354-schematic-export.json](evidence/2026-11-13-165354-schematic-export.json) | 2026-11-13T16:53:54Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165351-schematic-create.json](evidence/2026-11-13-165351-schematic-create.json) | 2026-11-13T16:53:51Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165347-schematic-check.json](evidence/2026-11-13-165347-schematic-check.json) | 2026-11-13T16:53:47Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165344-net-list.json](evidence/2026-11-13-165344-net-list.json) | 2026-11-13T16:53:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165341-net-get.json](evidence/2026-11-13-165341-net-get.json) | 2026-11-13T16:53:41Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165339-component-list.json](evidence/2026-11-13-165339-component-list.json) | 2026-11-13T16:53:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165336-component-get.json](evidence/2026-11-13-165336-component-get.json) | 2026-11-13T16:53:36Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165333-bom-export.json](evidence/2026-11-13-165333-bom-export.json) | 2026-11-13T16:53:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165330-bom-check.json](evidence/2026-11-13-165330-bom-check.json) | 2026-11-13T16:53:30Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165252-plane-delete.json](evidence/2026-11-13-165252-plane-delete.json) | 2026-11-13T16:52:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165244-pcb-update.json](evidence/2026-11-13-165244-pcb-update.json) | 2026-11-13T16:52:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165240-via-create.json](evidence/2026-11-13-165240-via-create.json) | 2026-11-13T16:52:40Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165221-keepout-delete.json](evidence/2026-11-13-165221-keepout-delete.json) | 2026-11-13T16:52:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165218-keepout-create.json](evidence/2026-11-13-165218-keepout-create.json) | 2026-11-13T16:52:18Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165214-silkscreen-text-delete.json](evidence/2026-11-13-165214-silkscreen-text-delete.json) | 2026-11-13T16:52:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165211-silkscreen-text-create.json](evidence/2026-11-13-165211-silkscreen-text-create.json) | 2026-11-13T16:52:11Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165208-solder-mask-delete.json](evidence/2026-11-13-165208-solder-mask-delete.json) | 2026-11-13T16:52:08Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165204-solder-mask-create.json](evidence/2026-11-13-165204-solder-mask-create.json) | 2026-11-13T16:52:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165200-plane-delete.json](evidence/2026-11-13-165200-plane-delete.json) | 2026-11-13T16:52:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165157-plane-create.json](evidence/2026-11-13-165157-plane-create.json) | 2026-11-13T16:51:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165121-pcb-export.json](evidence/2026-11-13-165121-pcb-export.json) | 2026-11-13T16:51:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165114-pcb-export.json](evidence/2026-11-13-165114-pcb-export.json) | 2026-11-13T16:51:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165055-pcb-export.json](evidence/2026-11-13-165055-pcb-export.json) | 2026-11-13T16:50:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-165030-pcb-export.json](evidence/2026-11-13-165030-pcb-export.json) | 2026-11-13T16:50:30Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-13-164926-pcb-render.json](evidence/2026-11-13-164926-pcb-render.json) | 2026-11-13T16:49:26Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164913-pcb-render.json](evidence/2026-11-13-164913-pcb-render.json) | 2026-11-13T16:49:13Z | Xpedition 2604 | windows | 6 of 6 runs passed |
| [2026-11-13-164829-keepout-delete.json](evidence/2026-11-13-164829-keepout-delete.json) | 2026-11-13T16:48:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164821-keepout-delete.json](evidence/2026-11-13-164821-keepout-delete.json) | 2026-11-13T16:48:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164817-keepout-delete.json](evidence/2026-11-13-164817-keepout-delete.json) | 2026-11-13T16:48:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164814-keepout-delete.json](evidence/2026-11-13-164814-keepout-delete.json) | 2026-11-13T16:48:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164810-keepout-delete.json](evidence/2026-11-13-164810-keepout-delete.json) | 2026-11-13T16:48:10Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-164802-keepout-create.json](evidence/2026-11-13-164802-keepout-create.json) | 2026-11-13T16:48:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164755-keepout-create.json](evidence/2026-11-13-164755-keepout-create.json) | 2026-11-13T16:47:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164752-keepout-create.json](evidence/2026-11-13-164752-keepout-create.json) | 2026-11-13T16:47:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164748-keepout-create.json](evidence/2026-11-13-164748-keepout-create.json) | 2026-11-13T16:47:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164744-keepout-create.json](evidence/2026-11-13-164744-keepout-create.json) | 2026-11-13T16:47:44Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-164733-silkscreen-text-delete.json](evidence/2026-11-13-164733-silkscreen-text-delete.json) | 2026-11-13T16:47:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164724-silkscreen-text-delete.json](evidence/2026-11-13-164724-silkscreen-text-delete.json) | 2026-11-13T16:47:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164720-silkscreen-text-delete.json](evidence/2026-11-13-164720-silkscreen-text-delete.json) | 2026-11-13T16:47:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164717-silkscreen-text-delete.json](evidence/2026-11-13-164717-silkscreen-text-delete.json) | 2026-11-13T16:47:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164713-silkscreen-text-delete.json](evidence/2026-11-13-164713-silkscreen-text-delete.json) | 2026-11-13T16:47:13Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-164705-silkscreen-text-create.json](evidence/2026-11-13-164705-silkscreen-text-create.json) | 2026-11-13T16:47:05Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164657-silkscreen-text-create.json](evidence/2026-11-13-164657-silkscreen-text-create.json) | 2026-11-13T16:46:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164652-silkscreen-text-create.json](evidence/2026-11-13-164652-silkscreen-text-create.json) | 2026-11-13T16:46:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164649-silkscreen-text-create.json](evidence/2026-11-13-164649-silkscreen-text-create.json) | 2026-11-13T16:46:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164643-silkscreen-text-create.json](evidence/2026-11-13-164643-silkscreen-text-create.json) | 2026-11-13T16:46:43Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-164635-via-create.json](evidence/2026-11-13-164635-via-create.json) | 2026-11-13T16:46:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164620-via-create.json](evidence/2026-11-13-164620-via-create.json) | 2026-11-13T16:46:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164610-via-create.json](evidence/2026-11-13-164610-via-create.json) | 2026-11-13T16:46:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164552-via-create.json](evidence/2026-11-13-164552-via-create.json) | 2026-11-13T16:45:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164542-via-create.json](evidence/2026-11-13-164542-via-create.json) | 2026-11-13T16:45:42Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-164533-solder-mask-delete.json](evidence/2026-11-13-164533-solder-mask-delete.json) | 2026-11-13T16:45:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164529-solder-mask-delete.json](evidence/2026-11-13-164529-solder-mask-delete.json) | 2026-11-13T16:45:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164520-solder-mask-delete.json](evidence/2026-11-13-164520-solder-mask-delete.json) | 2026-11-13T16:45:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164517-solder-mask-delete.json](evidence/2026-11-13-164517-solder-mask-delete.json) | 2026-11-13T16:45:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164513-solder-mask-delete.json](evidence/2026-11-13-164513-solder-mask-delete.json) | 2026-11-13T16:45:13Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-164505-solder-mask-create.json](evidence/2026-11-13-164505-solder-mask-create.json) | 2026-11-13T16:45:05Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164458-solder-mask-create.json](evidence/2026-11-13-164458-solder-mask-create.json) | 2026-11-13T16:44:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164453-solder-mask-create.json](evidence/2026-11-13-164453-solder-mask-create.json) | 2026-11-13T16:44:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164450-solder-mask-create.json](evidence/2026-11-13-164450-solder-mask-create.json) | 2026-11-13T16:44:50Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164446-solder-mask-create.json](evidence/2026-11-13-164446-solder-mask-create.json) | 2026-11-13T16:44:46Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-164433-plane-delete.json](evidence/2026-11-13-164433-plane-delete.json) | 2026-11-13T16:44:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164429-plane-delete.json](evidence/2026-11-13-164429-plane-delete.json) | 2026-11-13T16:44:29Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164421-plane-delete.json](evidence/2026-11-13-164421-plane-delete.json) | 2026-11-13T16:44:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164418-plane-delete.json](evidence/2026-11-13-164418-plane-delete.json) | 2026-11-13T16:44:18Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164414-plane-delete.json](evidence/2026-11-13-164414-plane-delete.json) | 2026-11-13T16:44:14Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-13-164408-constraint-get.json](evidence/2026-11-13-164408-constraint-get.json) | 2026-11-13T16:44:08Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-13-164402-pcb-check.json](evidence/2026-11-13-164402-pcb-check.json) | 2026-11-13T16:44:02Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-13-164346-pcb-get.json](evidence/2026-11-13-164346-pcb-get.json) | 2026-11-13T16:43:46Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-13-164341-router-run.json](evidence/2026-11-13-164341-router-run.json) | 2026-11-13T16:43:41Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164336-router-run.json](evidence/2026-11-13-164336-router-run.json) | 2026-11-13T16:43:36Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164327-router-run.json](evidence/2026-11-13-164327-router-run.json) | 2026-11-13T16:43:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164320-router-run.json](evidence/2026-11-13-164320-router-run.json) | 2026-11-13T16:43:20Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164307-router-run.json](evidence/2026-11-13-164307-router-run.json) | 2026-11-13T16:43:07Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164302-router-run.json](evidence/2026-11-13-164302-router-run.json) | 2026-11-13T16:43:02Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-164255-plane-create.json](evidence/2026-11-13-164255-plane-create.json) | 2026-11-13T16:42:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164248-plane-create.json](evidence/2026-11-13-164248-plane-create.json) | 2026-11-13T16:42:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164244-plane-create.json](evidence/2026-11-13-164244-plane-create.json) | 2026-11-13T16:42:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164240-plane-create.json](evidence/2026-11-13-164240-plane-create.json) | 2026-11-13T16:42:40Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164237-plane-create.json](evidence/2026-11-13-164237-plane-create.json) | 2026-11-13T16:42:37Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-164228-placement-run.json](evidence/2026-11-13-164228-placement-run.json) | 2026-11-13T16:42:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164222-doc-save.json](evidence/2026-11-13-164222-doc-save.json) | 2026-11-13T16:42:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164123-placement-run.json](evidence/2026-11-13-164123-placement-run.json) | 2026-11-13T16:41:23Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164115-placement-run.json](evidence/2026-11-13-164115-placement-run.json) | 2026-11-13T16:41:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164109-placement-run.json](evidence/2026-11-13-164109-placement-run.json) | 2026-11-13T16:41:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164055-placement-run.json](evidence/2026-11-13-164055-placement-run.json) | 2026-11-13T16:40:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164049-doc-close.json](evidence/2026-11-13-164049-doc-close.json) | 2026-11-13T16:40:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164046-doc-open.json](evidence/2026-11-13-164046-doc-open.json) | 2026-11-13T16:40:46Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-164040-placement-run.json](evidence/2026-11-13-164040-placement-run.json) | 2026-11-13T16:40:40Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-164003-doc-close.json](evidence/2026-11-13-164003-doc-close.json) | 2026-11-13T16:40:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163939-doc-open.json](evidence/2026-11-13-163939-doc-open.json) | 2026-11-13T16:39:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163934-doc-open.json](evidence/2026-11-13-163934-doc-open.json) | 2026-11-13T16:39:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163914-doc-open.json](evidence/2026-11-13-163914-doc-open.json) | 2026-11-13T16:39:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163850-doc-open.json](evidence/2026-11-13-163850-doc-open.json) | 2026-11-13T16:38:50Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-163839-doc-close.json](evidence/2026-11-13-163839-doc-close.json) | 2026-11-13T16:38:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163831-doc-close.json](evidence/2026-11-13-163831-doc-close.json) | 2026-11-13T16:38:31Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-163756-forward-annotation-run.json](evidence/2026-11-13-163756-forward-annotation-run.json) | 2026-11-13T16:37:56Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163517-doc-save.json](evidence/2026-11-13-163517-doc-save.json) | 2026-11-13T16:35:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163442-doc-save.json](evidence/2026-11-13-163442-doc-save.json) | 2026-11-13T16:34:42Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-13-163408-doc-save.json](evidence/2026-11-13-163408-doc-save.json) | 2026-11-13T16:34:08Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163338-pcb-update.json](evidence/2026-11-13-163338-pcb-update.json) | 2026-11-13T16:33:38Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163332-pcb-update.json](evidence/2026-11-13-163332-pcb-update.json) | 2026-11-13T16:33:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163328-pcb-update.json](evidence/2026-11-13-163328-pcb-update.json) | 2026-11-13T16:33:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163324-pcb-update.json](evidence/2026-11-13-163324-pcb-update.json) | 2026-11-13T16:33:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163320-pcb-update.json](evidence/2026-11-13-163320-pcb-update.json) | 2026-11-13T16:33:20Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-163306-doc-undo.json](evidence/2026-11-13-163306-doc-undo.json) | 2026-11-13T16:33:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163302-doc-save.json](evidence/2026-11-13-163302-doc-save.json) | 2026-11-13T16:33:02Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163258-doc-open.json](evidence/2026-11-13-163258-doc-open.json) | 2026-11-13T16:32:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163255-doc-close.json](evidence/2026-11-13-163255-doc-close.json) | 2026-11-13T16:32:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163252-forward-annotation-run.json](evidence/2026-11-13-163252-forward-annotation-run.json) | 2026-11-13T16:32:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-163236-forward-annotation-run.json](evidence/2026-11-13-163236-forward-annotation-run.json) | 2026-11-13T16:32:36Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162828-forward-annotation-run.json](evidence/2026-11-13-162828-forward-annotation-run.json) | 2026-11-13T16:28:28Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162822-forward-annotation-run.json](evidence/2026-11-13-162822-forward-annotation-run.json) | 2026-11-13T16:28:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162806-forward-annotation-run.json](evidence/2026-11-13-162806-forward-annotation-run.json) | 2026-11-13T16:28:06Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-162758-constraint-get.json](evidence/2026-11-13-162758-constraint-get.json) | 2026-11-13T16:27:58Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-162752-pcb-check.json](evidence/2026-11-13-162752-pcb-check.json) | 2026-11-13T16:27:52Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-162739-pcb-get.json](evidence/2026-11-13-162739-pcb-get.json) | 2026-11-13T16:27:39Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-162706-pcb-create.json](evidence/2026-11-13-162706-pcb-create.json) | 2026-11-13T16:27:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162703-pcb-create.json](evidence/2026-11-13-162703-pcb-create.json) | 2026-11-13T16:27:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162654-pcb-create.json](evidence/2026-11-13-162654-pcb-create.json) | 2026-11-13T16:26:54Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-162647-pcb-create.json](evidence/2026-11-13-162647-pcb-create.json) | 2026-11-13T16:26:47Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162455-packager-run.json](evidence/2026-11-13-162455-packager-run.json) | 2026-11-13T16:24:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162447-packager-run.json](evidence/2026-11-13-162447-packager-run.json) | 2026-11-13T16:24:47Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162436-packager-run.json](evidence/2026-11-13-162436-packager-run.json) | 2026-11-13T16:24:36Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-162317-schematic-export.json](evidence/2026-11-13-162317-schematic-export.json) | 2026-11-13T16:23:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162309-schematic-create.json](evidence/2026-11-13-162309-schematic-create.json) | 2026-11-13T16:23:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162255-session-close.json](evidence/2026-11-13-162255-session-close.json) | 2026-11-13T16:22:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162252-bom-export.json](evidence/2026-11-13-162252-bom-export.json) | 2026-11-13T16:22:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162248-bom-check.json](evidence/2026-11-13-162248-bom-check.json) | 2026-11-13T16:22:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162245-net-get.json](evidence/2026-11-13-162245-net-get.json) | 2026-11-13T16:22:45Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162242-net-list.json](evidence/2026-11-13-162242-net-list.json) | 2026-11-13T16:22:42Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162239-component-get.json](evidence/2026-11-13-162239-component-get.json) | 2026-11-13T16:22:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162236-component-list.json](evidence/2026-11-13-162236-component-list.json) | 2026-11-13T16:22:36Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162234-doc-create.json](evidence/2026-11-13-162234-doc-create.json) | 2026-11-13T16:22:34Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162230-session-list.json](evidence/2026-11-13-162230-session-list.json) | 2026-11-13T16:22:30Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162227-session-get.json](evidence/2026-11-13-162227-session-get.json) | 2026-11-13T16:22:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162224-doc-list.json](evidence/2026-11-13-162224-doc-list.json) | 2026-11-13T16:22:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162221-doc-get.json](evidence/2026-11-13-162221-doc-get.json) | 2026-11-13T16:22:21Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162219-schematic-create.json](evidence/2026-11-13-162219-schematic-create.json) | 2026-11-13T16:22:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162216-schematic-export.json](evidence/2026-11-13-162216-schematic-export.json) | 2026-11-13T16:22:16Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162213-schematic-check.json](evidence/2026-11-13-162213-schematic-check.json) | 2026-11-13T16:22:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162210-schematic-get.json](evidence/2026-11-13-162210-schematic-get.json) | 2026-11-13T16:22:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162131-bom-export.json](evidence/2026-11-13-162131-bom-export.json) | 2026-11-13T16:21:31Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162125-bom-export.json](evidence/2026-11-13-162125-bom-export.json) | 2026-11-13T16:21:25Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162119-bom-export.json](evidence/2026-11-13-162119-bom-export.json) | 2026-11-13T16:21:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162115-bom-check.json](evidence/2026-11-13-162115-bom-check.json) | 2026-11-13T16:21:15Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162112-net-get.json](evidence/2026-11-13-162112-net-get.json) | 2026-11-13T16:21:12Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-162106-net-list.json](evidence/2026-11-13-162106-net-list.json) | 2026-11-13T16:21:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162102-component-get.json](evidence/2026-11-13-162102-component-get.json) | 2026-11-13T16:21:02Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-162051-schematic-export.json](evidence/2026-11-13-162051-schematic-export.json) | 2026-11-13T16:20:51Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162044-schematic-export.json](evidence/2026-11-13-162044-schematic-export.json) | 2026-11-13T16:20:44Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-13-162032-schematic-check.json](evidence/2026-11-13-162032-schematic-check.json) | 2026-11-13T16:20:32Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-162027-schematic-get.json](evidence/2026-11-13-162027-schematic-get.json) | 2026-11-13T16:20:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161935-session-close.json](evidence/2026-11-13-161935-session-close.json) | 2026-11-13T16:19:35Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161922-bom-export.json](evidence/2026-11-13-161922-bom-export.json) | 2026-11-13T16:19:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161909-bom-check.json](evidence/2026-11-13-161909-bom-check.json) | 2026-11-13T16:19:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161856-net-get.json](evidence/2026-11-13-161856-net-get.json) | 2026-11-13T16:18:56Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161843-net-list.json](evidence/2026-11-13-161843-net-list.json) | 2026-11-13T16:18:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161831-component-get.json](evidence/2026-11-13-161831-component-get.json) | 2026-11-13T16:18:31Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161818-component-list.json](evidence/2026-11-13-161818-component-list.json) | 2026-11-13T16:18:18Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161806-doc-create.json](evidence/2026-11-13-161806-doc-create.json) | 2026-11-13T16:18:06Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161752-session-get.json](evidence/2026-11-13-161752-session-get.json) | 2026-11-13T16:17:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161739-doc-get.json](evidence/2026-11-13-161739-doc-get.json) | 2026-11-13T16:17:39Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161727-schematic-create.json](evidence/2026-11-13-161727-schematic-create.json) | 2026-11-13T16:17:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161714-schematic-export.json](evidence/2026-11-13-161714-schematic-export.json) | 2026-11-13T16:17:14Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161701-schematic-check.json](evidence/2026-11-13-161701-schematic-check.json) | 2026-11-13T16:17:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161649-schematic-get.json](evidence/2026-11-13-161649-schematic-get.json) | 2026-11-13T16:16:49Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161451-schematic-create.json](evidence/2026-11-13-161451-schematic-create.json) | 2026-11-13T16:14:51Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161437-session-close.json](evidence/2026-11-13-161437-session-close.json) | 2026-11-13T16:14:37Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161433-schematic-export.json](evidence/2026-11-13-161433-schematic-export.json) | 2026-11-13T16:14:33Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161427-padstack-list.json](evidence/2026-11-13-161427-padstack-list.json) | 2026-11-13T16:14:27Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161425-part-get.json](evidence/2026-11-13-161425-part-get.json) | 2026-11-13T16:14:25Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161422-part-list.json](evidence/2026-11-13-161422-part-list.json) | 2026-11-13T16:14:22Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161419-project-get.json](evidence/2026-11-13-161419-project-get.json) | 2026-11-13T16:14:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161417-bom-export.json](evidence/2026-11-13-161417-bom-export.json) | 2026-11-13T16:14:17Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161413-bom-check.json](evidence/2026-11-13-161413-bom-check.json) | 2026-11-13T16:14:13Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161410-net-get.json](evidence/2026-11-13-161410-net-get.json) | 2026-11-13T16:14:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161407-net-list.json](evidence/2026-11-13-161407-net-list.json) | 2026-11-13T16:14:07Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161404-component-get.json](evidence/2026-11-13-161404-component-get.json) | 2026-11-13T16:14:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161400-component-list.json](evidence/2026-11-13-161400-component-list.json) | 2026-11-13T16:14:00Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161357-doc-list.json](evidence/2026-11-13-161357-doc-list.json) | 2026-11-13T16:13:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161355-doc-get.json](evidence/2026-11-13-161355-doc-get.json) | 2026-11-13T16:13:55Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161352-schematic-check.json](evidence/2026-11-13-161352-schematic-check.json) | 2026-11-13T16:13:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161348-schematic-get.json](evidence/2026-11-13-161348-schematic-get.json) | 2026-11-13T16:13:48Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161344-schematic-create.json](evidence/2026-11-13-161344-schematic-create.json) | 2026-11-13T16:13:44Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161331-schematic-create.json](evidence/2026-11-13-161331-schematic-create.json) | 2026-11-13T16:13:31Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161315-schematic-create.json](evidence/2026-11-13-161315-schematic-create.json) | 2026-11-13T16:13:15Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-161307-pcb-create.json](evidence/2026-11-13-161307-pcb-create.json) | 2026-11-13T16:13:07Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161304-packager-run.json](evidence/2026-11-13-161304-packager-run.json) | 2026-11-13T16:13:04Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161301-padstack-create.json](evidence/2026-11-13-161301-padstack-create.json) | 2026-11-13T16:13:01Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161257-part-create.json](evidence/2026-11-13-161257-part-create.json) | 2026-11-13T16:12:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161254-doc-list.json](evidence/2026-11-13-161254-doc-list.json) | 2026-11-13T16:12:54Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-161248-session-get.json](evidence/2026-11-13-161248-session-get.json) | 2026-11-13T16:12:48Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-161243-session-open.json](evidence/2026-11-13-161243-session-open.json) | 2026-11-13T16:12:43Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161238-session-open.json](evidence/2026-11-13-161238-session-open.json) | 2026-11-13T16:12:38Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161157-session-open.json](evidence/2026-11-13-161157-session-open.json) | 2026-11-13T16:11:57Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161118-session-open.json](evidence/2026-11-13-161118-session-open.json) | 2026-11-13T16:11:18Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-161109-padstack-create.json](evidence/2026-11-13-161109-padstack-create.json) | 2026-11-13T16:11:09Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161058-padstack-create.json](evidence/2026-11-13-161058-padstack-create.json) | 2026-11-13T16:10:58Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161053-padstack-create.json](evidence/2026-11-13-161053-padstack-create.json) | 2026-11-13T16:10:53Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161046-padstack-create.json](evidence/2026-11-13-161046-padstack-create.json) | 2026-11-13T16:10:46Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-161039-padstack-list.json](evidence/2026-11-13-161039-padstack-list.json) | 2026-11-13T16:10:39Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-13-161032-part-get.json](evidence/2026-11-13-161032-part-get.json) | 2026-11-13T16:10:32Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-161026-part-list.json](evidence/2026-11-13-161026-part-list.json) | 2026-11-13T16:10:26Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-13-161019-part-create.json](evidence/2026-11-13-161019-part-create.json) | 2026-11-13T16:10:19Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161010-part-create.json](evidence/2026-11-13-161010-part-create.json) | 2026-11-13T16:10:10Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-161003-part-create.json](evidence/2026-11-13-161003-part-create.json) | 2026-11-13T16:10:03Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-160955-part-create.json](evidence/2026-11-13-160955-part-create.json) | 2026-11-13T16:09:55Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-160946-project-get.json](evidence/2026-11-13-160946-project-get.json) | 2026-11-13T16:09:46Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-160941-session-close.json](evidence/2026-11-13-160941-session-close.json) | 2026-11-13T16:09:41Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-160928-session-close.json](evidence/2026-11-13-160928-session-close.json) | 2026-11-13T16:09:28Z | Xpedition 2604 | windows | 2 of 2 runs passed |
| [2026-11-13-160924-doc-create.json](evidence/2026-11-13-160924-doc-create.json) | 2026-11-13T16:09:24Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-160752-session-list.json](evidence/2026-11-13-160752-session-list.json) | 2026-11-13T16:07:52Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-160741-doc-get.json](evidence/2026-11-13-160741-doc-get.json) | 2026-11-13T16:07:41Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-160735-bom-export.json](evidence/2026-11-13-160735-bom-export.json) | 2026-11-13T16:07:35Z | Xpedition 2604 | windows | 4 of 4 runs passed |
| [2026-11-13-160724-bom-check.json](evidence/2026-11-13-160724-bom-check.json) | 2026-11-13T16:07:24Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-160718-net-list.json](evidence/2026-11-13-160718-net-list.json) | 2026-11-13T16:07:18Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-160712-component-list.json](evidence/2026-11-13-160712-component-list.json) | 2026-11-13T16:07:12Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-160706-schematic-check.json](evidence/2026-11-13-160706-schematic-check.json) | 2026-11-13T16:07:06Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-160658-schematic-get.json](evidence/2026-11-13-160658-schematic-get.json) | 2026-11-13T16:06:58Z | Xpedition 2604 | windows | 3 of 3 runs passed |
| [2026-11-13-160652-doc-create.json](evidence/2026-11-13-160652-doc-create.json) | 2026-11-13T16:06:52Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-160650-doc-create.json](evidence/2026-11-13-160650-doc-create.json) | 2026-11-13T16:06:50Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-160645-doc-create.json](evidence/2026-11-13-160645-doc-create.json) | 2026-11-13T16:06:45Z | Xpedition 2604 | windows | 1 of 1 runs passed |
| [2026-11-13-160532-doc-create.json](evidence/2026-11-13-160532-doc-create.json) | 2026-11-13T16:05:32Z | Xpedition 2604 | windows | 3 of 3 runs passed |
<!-- driver:evidence:end -->
