<h1 align="center">xpedition-cli</h1>

<p align="center"><strong>Agent-native Xpedition design control with JSON-first reads and dry-run guarded ChangeSets</strong></p>

<p align="center"><a href="README.md">English</a> · <a href="README_zh.md">中文</a></p>

<p align="center">
  <a href="https://github.com/fatecannotbealtered/xpedition-cli/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/fatecannotbealtered/xpedition-cli/ci.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=CI"></a>
  <a href="https://www.npmjs.com/package/@fateforge/xpedition-cli"><img alt="npm" src="https://img.shields.io/npm/v/@fateforge/xpedition-cli?style=for-the-badge&logo=npm&logoColor=white&label=npm&color=CB3837"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-7C3AED?style=for-the-badge"></a>
</p>

`xpedition-cli` is the command layer around Siemens Xpedition data. MockBackend
works offline; NativeBackend drives a licensed Xpedition installation through an
optional Windows COM adapter, and has taken a project from an empty schematic to a
routed board and a fabrication package (`docs/E2E.md`). That evidence comes from one
Windows installation, with converted open-source footprints and placeholder part
numbers. The CLI never edits Xpedition's private databases directly, and every
write command is gated by `--dry-run` then `--confirm <token>`.

## Agent Install

```bash
python -m pip install "xpedition-cli[native] @ git+https://github.com/fatecannotbealtered/xpedition-cli"
npx skills add fatecannotbealtered/xpedition-cli -y -g

xpedition-cli context --compact
xpedition-cli doctor --compact
xpedition-cli reference --compact
```

The first line installs the CLI and its Windows adapter (`[native]`) from this
repository's default branch; append a release tag to pin one, e.g.
`...xpedition-cli@v1.0.0`. From a checkout, `python -m pip install -e ".[native]"`
does the same. Each tagged release also publishes a standalone binary to npm as
`@fateforge/xpedition-cli`. It has no Windows adapter, so it serves MockBackend,
offline planning and file commands only; driving Xpedition needs the pip install
on Windows. No CLI login is required for MockBackend. Native Xpedition
credentials and licensing remain inside the verified Xpedition environment; see
[Native adapter protocol](docs/NATIVE_ADAPTER.md).

## What It Does

The CLI normalizes project snapshots, BOM rows, connectivity and deterministic
review findings. A ChangeSet describes controlled operations such as placing a
component, creating a net, connecting pins, moving or deleting a component, and
setting a property. Applying a ChangeSet requires a preview token, writes a
backup when replacing an existing file, saves atomically, and verifies the project after the write.

Risk tier: **T2**. Against MockBackend the blast radius is the explicitly named
local JSON file. Against NativeBackend it is the named Xpedition project: a
confirmed write can draw a schematic, place parts, and add or delete routing.
The writes that destroy work also need `--dangerous` next to the token:
`schematic draw`, `pcb unroute`, `pcb create --replace` (which first archives the
layout folder to a zip beside the project), `pcb arrange` on a routed board,
`pcb route --unroute`, `pcb annotate --unroute`, and `library kicad-import` into a
partition that exists. `pcb annotate`, `pcb export` when it changes the output
setups, and the first `pcb show --top-view` on a board close and reopen the board
without saving it, so save hand edits in Layout first. See [SECURITY.md](SECURITY.md).

## Capabilities

| Area | Commands | Backend |
|---|---|---|
| Project data | `project init`, `project info`, `project tree`, `project snapshot`, `project diff`, `design snapshot` | both; natively `project init --template` copies a template project, and `project diff` compares a MockBackend file with its backup |
| Schematic reads | `schematic sheets`, `components`, `pins`, `nets`, `connectivity`, `unconnected`, `power`, `interfaces`, `query` | both |
| Schematic drawing | `schematic draw`, `schematic show`, `schematic export`, `library build`, `library kicad-import` | NativeBackend (see below) |
| Pin planning | `schematic pin-plan`, `schematic pin-check` | offline, against a supplied snapshot |
| PCB reads | `pcb info`, `components`, `footprints`, `nets`, `tracks`, `vias`, `layers`, `stackup`, `zones`, `keepouts`, `query` | both; natively `layers`, `stackup`, `zones` and `keepouts` are refused (not read yet) and `pcb info` reports their counts as null |
| PCB design | `pcb create`, `annotate`, `outline`, `holes`, `arrange`, `placement`, `move`, `rules`, `pour`, `route`, `trace`, `via`, `unroute`, `labels`, `geometry`, `render`, `show`, `drc`, `export` | NativeBackend (see below) |
| PCB planning | `pcb stitch`, `pcb placement-plan`, `pcb metrics` | offline, from files; `pcb metrics` measures a placement and compares it with an earlier one |
| Constraints/analysis | `constraints ...`, `analysis run|results|erc|drc|dfm` | MockBackend; refused on the native backend (use `review run` and `pcb drc`) |
| Manufacturing/library reads | `manufacturing ...`, `library search|parts|symbols|footprints|padstacks|models|validate` | MockBackend; refused on the native backend, except `manufacturing bom`, which reads the components |
| Change control | `change validate`, `change preview`, `change apply`, `change history`, `change rollback`, `schematic apply` | MockBackend; natively `change apply` places and moves parts, and `schematic apply` also creates nets and connects pins |
| Review and BOM | `review run`, `bom export|normalize|group|variants|missing|duplicates|validate|compare` | both; natively `review run` adds Designer's own verification |
| Environment | `context`, `doctor`, `reference`, `changelog`, `system capabilities`, `system license`, `system api-inventory` | local probe; `api-inventory` reads COM type libraries on Windows |
| Knowledge base | `kb list`, `kb add`, `kb remove` | local links to company rules; the agent reads them |
| Session | `session status`, `session logs`, `session start`, `session attach`, `session open`, `session stop` | start/attach/open/stop drive Xpedition; `session logs` reads a log this version never writes |
| Exchange files | `exchange inspect`, `exchange import` | JSON/CSV/BOM/IPC-2581; PDF/EDN/ODB++ remain unavailable |
| Agent bridge | `agent snapshot`, `agent query`, `agent review`, `agent capabilities`, `agent serve` | MockBackend unless the native backend is selected; `serve` supports custom NDJSON and MCP transports |
| Planned | native constraints, analysis, manufacturing and library reads; PDF/EDN/ODB++ imports; the rest of the native ChangeSet operations | listed in `system capabilities` |

The live command and schema source is `xpedition-cli reference --compact`.

## Agent Workflow

1. Run `context`, `doctor`, and `reference`; check the backend and release readiness.
2. Keep `--backend mock` and `--project PATH` explicit for offline work.
3. Use `--compact` and `--fields` when passing JSON between agent steps.
4. To create a new MockBackend project, run `project init --dry-run`, inspect
   the preview, then confirm with the same arguments.
5. Validate and preview a ChangeSet before applying it:

   ```bash
   xpedition-cli change validate --changeset ./changeset.json --compact
   xpedition-cli change apply --backend mock --project ./demo-project.json --changeset ./changeset.json --dry-run --compact
   xpedition-cli change apply --backend mock --project ./demo-project.json --changeset ./changeset.json --confirm <confirm_token> --backup --compact
   ```

6. After applying, inspect `verification`; re-read `project snapshot` before any next write.
7. Use `change history` to inspect local operations. Rollback also requires a
   dry-run preview and a one-time confirmation token.

Agent integrations can use `xpedition-cli agent serve --transport stdio` for a
newline-delimited JSON request/response stream. It exposes snapshot, query,
review and capability methods, on MockBackend unless a request selects the
native backend.

## Native Xpedition: from the schematic to the fabrication package

The stages below have run on a licensed Xpedition (XPED2604) against the example
project -- apart from `pcb via` on its own and the guarded `library kicad-import`,
whose converter ran through an earlier entry point. The runs are recorded in
[docs/E2E.md](docs/E2E.md) and every fact learnt
about the automation in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). Write
commands are guarded: `--dry-run` returns a `confirm_token`, `--confirm <token>` acts.

| Stage | Commands |
|---|---|
| Project and schematic | `project init`, `schematic draw`, `schematic show`, `schematic export`, `review run` |
| Library | `library build` (parts from a design file), `library kicad-import` (cells from KiCad footprint libraries) |
| Board | `pcb create`, `pcb annotate`, `pcb outline`, `pcb holes`, `pcb arrange`, `pcb placement`, `pcb pour`, `pcb rules`, `pcb route`, `pcb drc` |
| By hand | `pcb geometry`, `pcb trace`, `pcb via`, `pcb unroute`, `pcb move`, `pcb labels`, `pcb stitch` (plans offline; the plan checks are in `xpedition_cli.routing_plan`) |
| Pictures and output | `pcb render`, `pcb show [--top-view]`, `pcb export` (ODB++, Gerber, NC drill, centroid, BOM, manifest) |

## Machine Contract

- JSON is the default and stdout contains exactly one envelope.
- Success and failure both include `ok`, `schema_version`, and `meta.duration_ms`.
- Errors use the canonical `E_*` to exit-code and `retryable` mapping in
  [`contract/contract.json`](contract/contract.json).
- Logs and diagnostics go to stderr. `--json` is a compatibility alias for
  `--format json`; `--format text` is for humans and `raw` returns the payload.
- Project and review values that can originate in files are marked in `_untrusted`.
- IDs are strings and timestamps are ISO 8601 UTC.

## Configuration

The CLI has no login flow in this phase. It keeps its local state under
`~/.xpedition-cli/`: the confirmation secret, the consumed-token ledger and its
lock, the audit JSONL, knowledge-base links, the native session record
(`session.json`) and placement locks. Set `XPEDITION_CLI_CONFIG_DIR` to isolate these files in
tests or CI. Set `XPEDITION_NATIVE_COMMAND` to select an adapter when needed;
setting it does not bypass COM registration or license checks.

Company rules -- layout rules, drawing conventions, review checklists -- stay in
the company's knowledge base. `kb add --name NAME --url URL --about TEXT` (a
write: dry run, then confirm) records which document applies, in
`knowledge-base.json`, and `context` lists them for the agent, which reads each
with its own tools (for a Feishu wiki, lark-cli). The CLI never fetches a
document.

## Project Structure

```text
xpedition-cli/
├── xpedition_cli/       # CLI boundary, models, ChangeSet, backends, contract
├── tests/               # command-level contract and FCC tests
├── skills/xpedition-cli/        # entry Skill: install, sessions, projects, ChangeSets
├── skills/xpedition-schematic/  # schematic Skill: Designer drawing, review, pins
├── skills/xpedition-pcb/        # board Skill: Layout, routing, DRC, fabrication
├── contract/            # canonical vendored machine contract
├── scripts/             # spec, version, and npm wrapper tooling
├── docs/                # compatibility, E2E, and open-source checklist
└── .agent/              # pinned AI-native CLI specifications
```

## Development

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check xpedition_cli tests
ruff format --check xpedition_cli tests
node scripts/check-version.js
node scripts/check-spec.js --local-only
```

`reference.release_readiness.level` is `beta`: every public command has a
command-level test and the contract tests cover the failure and boundary behaviour
as well as the happy path, and live runs against a licensed Xpedition are recorded
in [`docs/E2E.md`](docs/E2E.md); `reference` names what still keeps it from
`stable`. The level is a statement about that evidence, not a promise that the
Xpedition automation behaves identically on another installation.

## Links

- [Agent entry](AGENTS.md)
- [Skills](skills/xpedition-cli/SKILL.md): the entry Skill, with [xpedition-schematic](skills/xpedition-schematic/SKILL.md) and [xpedition-pcb](skills/xpedition-pcb/SKILL.md)
- [CLI contract](.agent/CLI-SPEC.md)
- [Security policy](SECURITY.md)
- [Compatibility matrix](docs/COMPATIBILITY.md)
- [Native adapter protocol](docs/NATIVE_ADAPTER.md)
- [MCP transport](docs/MCP.md)
- [E2E notes](docs/E2E.md)
- [Skill evaluations across models](docs/EVALS.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Third-party notice](NOTICE.md)
- [MIT license](LICENSE)
