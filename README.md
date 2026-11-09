# xpedition-cli

Lets an AI Agent build Xpedition designs from the command line, from central-library parts and the schematic to the board and its fabrication package, with every change previewed, confirmed and read back.

<!-- driver:badges:start -->
![Agent-native](https://img.shields.io/badge/🤖-Agent--native-6f42c1)
[![CI](https://github.com/fatecannotbealtered/xpedition-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/fatecannotbealtered/xpedition-cli/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/@fateforge/xpedition-cli)](https://www.npmjs.com/package/@fateforge/xpedition-cli)
![drives](https://img.shields.io/badge/drives-Xpedition_2604-1f6feb)
![mode](https://img.shields.io/badge/mode-file_%C2%B7_session-0e8a16)
![platform](https://img.shields.io/badge/platform-Windows-555555)
![risk](https://img.shields.io/badge/risk-T1-d29922)
![maturity](https://img.shields.io/badge/maturity-verified-8957e5)
![spec](https://img.shields.io/badge/spec-tool--driver--cli--spec_1.1.0-0969da)
![license](https://img.shields.io/badge/license-Apache--2.0-blue)
<!-- driver:badges:end -->

English | [中文](README_zh.md)

<!-- driver:info-card:start -->
| Item | Value |
|---|---|
| Application | Xpedition 2604 |
| Modes | file, session |
| Systems and CPUs | Windows (x64) |
| Risk tier | T1 |
| Maturity | verified: 0 draft, 1 verified, 41 stable |
| Specification | tool-driver-cli-spec 1.1.0 |
| Host component | no |
<!-- driver:info-card:end -->

## Install

1. Install xpedition-cli from npm:

   ```bash
   npm install -g @fateforge/xpedition-cli
   ```

2. Run `update` once. The installed version is already current, so `update` only points the command entry at the native program, so that no call has to start Node.js first:

   ```bash
   xpedition-cli update
   ```

3. Install the Skills from the installed package; they never come from the network. Copy them into your Agent's Skill folder. With bash:

   ```bash
   mkdir -p ~/.agents/skills
   cp -r "$(npm root -g)/@fateforge/xpedition-cli/skills/"* ~/.agents/skills/
   ```

   With PowerShell:

   ```powershell
   New-Item -ItemType Directory -Force "$HOME\.agents\skills" | Out-Null
   Copy-Item -Recurse -Force "$(npm root -g)\@fateforge/xpedition-cli\skills\*" "$HOME\.agents\skills\"
   ```

   Other Agent Skill folders work the same way. Later runs of `xpedition-cli update` keep the copies in step with the tool in these folders: `~/.agents/skills/`, `~/.claude/skills/`, `~/.codex/skills/`, `~/.cursor/skills/` and `~/.gemini/skills/`. A copy anywhere else is not updated.

## Capabilities

Every command of xpedition-cli, generated from its command registration; `(dangerous)` marks a command in the dangerous tier, and `xpedition-cli reference --command "<path>"` describes one in full.

<!-- driver:command-map:start -->
- `reference`
- `context`
- `doctor`
- `changelog`
- `operation`: get, list, resolve
- `update`
- `session`: list, get, open, close
- `doc`: list, get, create, open, save, save-as, close, undo
- `job`: list, get, cancel
- `knowledge`: list, get, search, create, delete
- `project`: get
- `part`: list, get, create
- `padstack`: list, create
- `packager`: run
- `schematic`: get, create (dangerous), check, export
- `component`: list, get, move
- `net`: list, get
- `bom`: check, export
- `pcb`: create, update, get, check, export, render
- `forward-annotation`: run
- `placement`: run
- `mounting-hole`: list, create, delete
- `refdes`: move
- `plane`: create, delete
- `router`: run
- `trace`: create, delete
- `via`: delete, create, update
- `solder-mask`: create, delete
- `constraint`: get, import
<!-- driver:command-map:end -->

## How It Works

xpedition-cli prints one JSON envelope for every call. `xpedition-cli reference` lists every command, and `xpedition-cli reference --command "<path>"` describes one, with its flags, its effects and its confirmation tier in each mode.

**Instances and documents.** xpedition-cli drives running instances of Xpedition. `xpedition-cli session list` shows each instance with its session ID, its state and its open documents, and `xpedition-cli doc list` shows the documents. Name the instance with `--session <id>` and the document with `--doc <path-or-id>`. When several instances run and a command names none, it returns `E_AMBIGUOUS` with the candidates: xpedition-cli never picks an instance by window focus or by recent use. A `--doc` that exactly one instance has open selects that instance. A file that an instance has open is changed only through that instance; a direct write to the file is refused with `E_CONFLICT`.

**Files.** xpedition-cli reads and writes Xpedition files directly, without a running instance. `--doc <path>` names the file.

**Confirmation tiers.** Every write checks the scope, builds a preview, checks the state again just before dispatch, locks its target, reads the change back and returns a receipt. Only the confirmation depends on the tier, which xpedition-cli derives from the effects the command declares:

| Tier | Applies to | How to call |
|---|---|---|
| direct | Changes held in memory until they are saved; starting instances; creating and opening documents; new files that overwrite nothing; stopping work | One call runs it |
| two-step | Saving and overwriting files; changes in file and device mode; settings; host components | The first call changes nothing and returns `E_CONFIRMATION_REQUIRED` with a preview and a confirm token; the same command with `--confirm <token>` runs it |
| dangerous | Physical actions; handing content to outside people or systems; irreversible changes; discarding unsaved changes; closing an instance xpedition-cli did not start | As two-step, and the confirming call also carries `--dangerous`; the scope must allow dangerous operations |

`--dry-run` shows the preview of any write and changes nothing. A confirm token works once, within 10 minutes, and only for the change it previewed. When the scope file sets `"confirm": "always"`, every write except stopping work is two-step, so that a person can approve each preview.

**Saving in session mode.** A change is not a save. After a change, the receipt reports `persistence: unchanged` and the document stays dirty; only `xpedition-cli doc save` writes the document's file. Its preview lists every change since the last save, including changes a person made in Xpedition (`origin: external`) and writes whose outcome is still unknown. A command that Xpedition itself saves at once is two-step and reports `persistence: saved`.

**Saving in file mode.** Writing the file is the save: the receipt reports `persistence: saved`, and xpedition-cli reads the file again to verify the change.

**Receipts.** Every write returns a receipt that reports separately whether the change ran, took effect, was delivered, was saved and was verified. When the reply to a write is lost, xpedition-cli returns `E_OUTCOME_UNKNOWN` with the command that settles the outcome, and the write is never sent twice.

## Authorization

xpedition-cli reads without asking, but it writes only what a person has granted in a scope file. The person writes this JSON file by hand; no command writes it, and an Agent never creates or changes it. xpedition-cli reads it from the path in `XPEDITION_CLI_SCOPE_FILE`, or from `scope.json` in the state directory when the variable is not set, and from nowhere else.

```json
{
  "scope_id": "demo-rework",
  "effects": ["change", "persist"],
  "dangerous": false,
  "targets": ["file:///D:/projects/demo/"],
  "expires_at": "2027-01-31T18:00:00Z"
}
```

| Field | Meaning |
|---|---|
| `scope_id` | Names the grant; operation records carry it |
| `effects` | The effects writes may have; reads need no scope |
| `dangerous` | Whether operations in the dangerous tier are allowed |
| `targets` | Target-key prefixes of the objects writes may touch: `file:///D:/projects/demo/` covers every file under that folder, `untitled:` covers new documents and `device:<id>` covers one device |
| `expires_at` | When the grant ends, in UTC |
| `confirm` | Optional; `always` makes every write except stopping work wait until a person approves its preview |

A write the scope does not cover changes nothing and returns `E_FORBIDDEN`, with what is missing and a snippet the person can paste into the file. `xpedition-cli context` shows the scope in force, and `SECURITY.md` describes the scope in full.

Commands and the effects a scope must grant for them:

| Effect | Commands |
|---|---|
| `artifact` | `schematic create` (writes symbol files), `schematic export`, `pcb create` (writes the board file), `pcb export` |
| `change` | `part create`, `packager run`, `pcb create`, `schematic create`, `forward-annotation run`, `pcb update`, `placement run`, `plane create`, `router run` |
| `persist` | `schematic create` (writes symbol files), `pcb create` (writes the project file), `pcb export` (rewrites the output setup files), `doc save` |
| `lifecycle` | `session open`, `session close`, `doc open`, `doc close`, `job cancel` |

`schematic create` is dangerous because it replaces the sheets it draws; it runs only with `"dangerous": true` in the scope. Building a board in one project folder needs `lifecycle`, `change`, `artifact` and `persist` on that folder.

## About This Tool

Xpedition is Siemens' printed-circuit-board design suite. Designer draws the schematic, Layout draws the board, and the project's central library holds the parts, symbols, footprints (cells) and padstacks. xpedition-cli drives a licensed Xpedition 2604 on the same Windows computer: Designer and Layout through their COM interfaces, and the central library, packaging, board creation and PDF export through Xpedition's own command-line programs.

The first release covers the path from an empty project to a fabrication package:

1. `doc create --kind project` makes a project: in a Designer session from Xpedition's stock setup template, or in file mode as a copy of a template project; `part create` adds parts to its central library from a parts file.
2. `session open --doc Demo.prj` starts Designer; `schematic create` draws the schematic from a design file; `schematic check` runs Designer's verification; `doc save` saves it; `schematic export` writes a PDF.
3. After `doc close` in Designer, `packager run` packages the schematic and `pcb create` creates the board.
4. `session open --doc PCB/Demo.pcb` starts Layout; `forward-annotation run` brings the schematic in; `pcb update` sets the outline; `placement run`, `plane create` and `router run` place, pour and route; `pcb check` runs the design rule check; `doc save` saves; `pcb export` writes ODB++, Gerber, NC drill, a centroid file and a BOM.

Every change is previewed, confirmed when its tier asks for it, and read back from Xpedition. Work that takes Xpedition longer than one call runs as a long job that `job get` follows: starting and quitting Designer and Layout, creating, opening, saving and closing documents, `part create`, `packager run`, `pcb create`, `schematic create`, `schematic check`, `forward-annotation run`, `placement run`, `router run`, `pcb check` and `pcb export`.

Upgrading from version 1.0.3 or earlier: those versions have no `update` command. Reinstall with `npm install -g @fateforge/xpedition-cli`, then delete their Skill folders `xpedition-schematic` and `xpedition-pcb` from your Agent's Skill folders; this version has a single Skill, `xpedition-cli`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `XPEDITION_CLI_STATE_DIR` | `~/.xpedition-cli` | The state directory: operation records, locks, the update cache and team knowledge registrations |
| `XPEDITION_CLI_SCOPE_FILE` | `scope.json` in the state directory | The scope file (see Authorization) |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | not set | Proxies for `update` and `doctor`, the only commands that contact the npm registry |
| `XPEDITION_CLI_FAKE_HOST` | not set | For tests only: sends every call to the fake host instead of Xpedition and marks every result as simulated |

### Finding Xpedition

- **Install:** xpedition-cli looks for the Xpedition install (`SDD_HOME`) in this order: the variables `XPEDITION_SDD_HOME` and `SDD_HOME` (from the process, then the user's and the machine's Windows environment settings), the release entries under `HKEY_LOCAL_MACHINE\Software\Mentor Graphics\Releases`, and a release folder beside the licence file. The folder must hold `common/win64/bin/ExpeditionPCB.exe`. Set `XPEDITION_SDD_HOME` to choose one install when several are present.
- **Version:** read from the release folder name, such as `XPED2604` or `XENTP2604`. `doctor` reports it.
- **Starting programs:** always through the launchers in `SDD_HOME/common/win64/bin`, which set up the release environment; licence settings missing from the process are taken from the Windows environment settings and never printed.
- **Running instances:** found from the process list (`viewdraw.exe` for Designer, `ExpeditionPCB.exe` for Layout) and reached through COM. COM reaches only one instance of each program, so xpedition-cli drives one Designer and one Layout at a time; `session list` shows the others as blocked.

## Uninstall

Remove the installed Skills while the package still lists them. With bash:

```bash
for skill in "$(npm root -g)/@fateforge/xpedition-cli/skills/"*; do rm -rf ~/.agents/skills/"$(basename "$skill")"; done
```

With PowerShell:

```powershell
Get-ChildItem "$(npm root -g)\@fateforge/xpedition-cli\skills" | ForEach-Object { Remove-Item -Recurse -Force "$HOME\.agents\skills\$($_.Name)" -ErrorAction SilentlyContinue }
```

Do the same in every other Agent Skill folder you copied them to, such as `~/.claude/skills/`. Then remove the npm package:

```bash
npm uninstall -g @fateforge/xpedition-cli
```

On Windows, also delete the folder `.xpedition-cli-trash` in npm's global `node_modules` folder (`npm root -g`) if it exists; `update` keeps earlier program files there until its next run when the state directory is on another disk.

Last, delete the state directory: `~/.xpedition-cli`, or the folder that `XPEDITION_CLI_STATE_DIR` names. It holds the operation records, the locks, the default scope file and the team knowledge registrations, so keep it if you want the records.

```bash
rm -rf ~/.xpedition-cli
```

```powershell
Remove-Item -Recurse -Force "$HOME\.xpedition-cli"
```

## Development

xpedition-cli is written in Python on the driver runtime in `xpedition_cli/_driver/`, a pinned copy that is never edited. Domain commands live in `xpedition_cli/commands/` and the adapter layer in `xpedition_cli/adapter/`.

```bash
python -m pip install --require-hashes --no-deps -r requirements-dev.txt
python scripts/spec.py check
python -m unittest discover -s tests
python scripts/spec.py suites
```

The tests and the conformance suites use the fake host and never need Xpedition. `CONTRIBUTING.md` describes the workflow, the behavior matrices and live runs.

## Links

- Specification: [tool-driver-cli-spec 1.0.3](.agent/), pinned in `.agent/`
- [CHANGELOG.md](CHANGELOG.md): the changes in each version
- [SECURITY.md](SECURITY.md): the security model and how to report a vulnerability
- [CONTRIBUTING.md](CONTRIBUTING.md): how to contribute
- [docs/APPLICATION.md](docs/APPLICATION.md): the capability survey of Xpedition
- [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md): where xpedition-cli works
- [docs/E2E.md](docs/E2E.md): live acceptance and its records
- Issues: <https://github.com/fatecannotbealtered/xpedition-cli/issues>
