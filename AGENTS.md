# AGENTS.md

English | [中文](AGENTS_zh.md)

This file is for coding Agents that work on the xpedition-cli project. Read it before changing anything.

## Bare minimum to obey

1. Never edit a pinned copy: `.agent/`, `contract/driver-contract.json`, `xpedition_cli/_driver/` and `licenses/`. They come unchanged from one tool-driver-cli-spec release. To move to another release, run `python scripts/spec.py sync <version>`, which replaces them all together.
2. Never edit generated content by hand: `contract/commands.json`, the command maps in the README and the Skills, the badge row, the info card, the risk block, the support matrix, the evidence index and `knowledge/index.json`. Change their source, then run `python scripts/spec.py generate`.
3. Register every domain command in `xpedition_cli/commands/` with all its effects, its modes, the unit quantity of every physical parameter, its target type and the capability IDs from `docs/APPLICATION.md` that it implements (`survey_ids`). Mark a change the application cannot take back as `irreversible`. Never lower a confirmation tier.
4. Give every domain command a behavior matrix in `tests/matrix/`. The `normal` row of a write command is its sample.
5. Before every commit, run `python -m ruff check .`, `python scripts/spec.py check`, `python -m unittest discover -s tests` and `python scripts/spec.py suites`. All four must pass. Never skip, weaken or edit a conformance test.
6. Run live tests on the real application only on the targets listed under Authorized Targets in `docs/E2E.md`. Never add a target: only a person does. Never create or change a scope file; the evaluation runner writes the one each question needs.
7. Tests set `XPEDITION_CLI_STATE_DIR` to a new temporary directory and never touch the real state directory.
8. Never commit secrets, license files or keys, or customer and project files you are not allowed to share, not even inside evidence.
9. Scripts and tests that start subprocesses on Windows pass `CREATE_NO_WINDOW`.
10. Add an entry under `[Unreleased]` in `CHANGELOG.md` for every change.
11. Write code, code comments, commit messages, command output and error messages in English.

## This project

xpedition-cli drives Xpedition, versions 2604, in these modes:

- `file`: reads and writes Xpedition files directly.
- `session`: drives running instances of Xpedition.

Where the code lives:

- Domain commands: `xpedition_cli/commands/`
- Adapter layer: `xpedition_cli/adapter/`
- Driver runtime, a pinned copy: `xpedition_cli/_driver/`

How the adapter reaches Xpedition:

- Designer: out-of-process COM `Viewdraw.Application` (pywin32), reached with `GetActiveObject`. Its object cannot tell its process ID, so only one Designer can be driven.
- Layout: out-of-process COM `MGCPCB.ExpeditionPCBApplication`; every opened document needs the licence token step (`Validate` → `MGCPCBAutomationLicensing.Application.GetToken` → `Validate`). Batch DRC and the fabrication outputs are menu commands (`Gui.ProcessCommand`) that raise dialogs.
- The central library, packaging, board creation and PDF export use Xpedition's command-line programs (`HKP2*DB`, `*DB2HKP`, `package.exe`, `JobWizard.exe`, `sch2pdf.exe`), always started through the launchers in `SDD_HOME/common/win64/bin`.
- A modal dialog blocks every COM call. `adapter/platform/dialogs.py` answers only the known prompts in its rule tables, never presses a default button of an unknown dialog, and never touches licence or sign-in windows.
- Work longer than 60 s runs in a worker process (`adapter/jobs.py`, `adapter/worker.py`).
- Unit tests fake COM and the programs. Never start Xpedition or run its programs from tests; live runs follow `docs/E2E.md`.

The common commands are listed in the Commands section of `CONTRIBUTING.md`. Live tests use only the targets under Authorized Targets in `docs/E2E.md`; this file lists none.
