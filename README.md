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
numbers. The CLI never edits Xpedition private databases, and every write is gated
by `--dry-run` then `--confirm <token>`.

## Agent Install

```bash
npm install -g @fateforge/xpedition-cli
npx skills add fatecannotbealtered/xpedition-cli -y -g

xpedition-cli context --compact
xpedition-cli doctor --compact
xpedition-cli reference --compact
```

For a checkout, run `python -m xpedition_cli.main ...` from the repository root.
No CLI login is required for MockBackend. Native Xpedition credentials and
licensing remain inside the verified Xpedition environment. Install the Windows
adapter with `python -m pip install -e ".[native]"`; see
[Native adapter protocol](docs/NATIVE_ADAPTER.md).

## What It Does

The CLI normalizes project snapshots, BOM rows, connectivity and deterministic
review findings. A ChangeSet describes controlled operations such as placing a
component, creating a net, connecting pins, moving or deleting a component, and
setting a property. Applying a ChangeSet requires a preview token, writes a
backup when replacing an existing file, saves atomically, and verifies the project after the write.

Risk tier: **T1**. Against MockBackend the blast radius is the explicitly named
local JSON file. Against NativeBackend it is the named Xpedition project: a
confirmed write can draw a schematic, place parts, add or delete routing, and
`pcb create --replace` archives the existing layout folder to a zip beside the
project before deleting it. See [SECURITY.md](SECURITY.md).

## Capabilities

| Area | Commands | Backend |
|---|---|---|
| Project data | `project init`, `project info`, `project tree`, `project snapshot`, `project diff`, `design snapshot` | MockBackend |
| Schematic | reads above plus `schematic apply` for ChangeSets | MockBackend |
| PCB reads | `pcb info`, `components`, `footprints`, `nets`, `layers`, `stackup`, `tracks`, `vias`, `zones`, `keepouts`, `query` | MockBackend |
| PCB design | `pcb create`, `annotate`, `outline`, `holes`, `arrange`, `move`, `rules`, `pour`, `route`, `trace`, `via`, `unroute`, `stitch`, `labels`, `geometry`, `render`, `show`, `drc`, `export` | NativeBackend (see below) |
| Schematic drawing | `schematic draw`, `schematic show`, `library build` | NativeBackend (see below) |
| Constraints/analysis | `constraints ...`, `analysis run|results|erc|drc|dfm` | MockBackend |
| Manufacturing/library | `manufacturing ...`, `library search|...|validate` | MockBackend |
| Change control | `change validate`, `change preview`, `change apply`, `change history`, `change rollback` | MockBackend |
| Review and BOM | `review run`, `bom export|normalize|group|variants|missing|duplicates|validate|compare` | MockBackend |
| Environment | `context`, `doctor`, `system capabilities`, `system license` | local probe |
| Session | `session status`, `session logs` | MockBackend; native start/attach/stop require adapter |
| Exchange files | `exchange inspect`, `exchange import` | JSON/CSV/BOM/IPC-2581; PDF/EDN/ODB++ remain unavailable |
| Agent bridge | `agent snapshot`, `agent query`, `agent review`, `agent capabilities`, `agent serve` | MockBackend; `serve` supports custom NDJSON and MCP transports |
| Native Xpedition | `session ...`, project/PCB reads, controlled component move/place | NativeBackend; requires COM registration and licensing |
| Planned | unsupported Exchange formats, full native ChangeSets, recorded R-C evidence | explicit in `capabilities` |

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
review and capability methods over MockBackend.

## Native Xpedition: from the schematic to the fabrication package

Every step below has run on a licensed Xpedition (XPED2604) against the example
project; the runs are recorded in [docs/E2E.md](docs/E2E.md) and every fact learnt
about the automation in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). Write
commands are guarded: `--dry-run` returns a `confirm_token`, `--confirm <token>` acts.

| Stage | Commands |
|---|---|
| Project and schematic | `project init`, `schematic plan`, `schematic draw`, `schematic show`, `schematic export`, `review run` |
| Library | `library build` (parts from a design file; cells from KiCad footprints via `python -m xpedition_cli.kicad_import`) |
| Board | `pcb create`, `pcb annotate`, `pcb outline`, `pcb holes`, `pcb arrange`, `pcb pour`, `pcb rules`, `pcb route`, `pcb drc` |
| By hand | `pcb geometry`, `pcb trace`, `pcb via`, `pcb unroute`, `pcb move`, `pcb labels`, `pcb stitch` (offline plan checks in `xpedition_cli.routing_plan`) |
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

The CLI has no login flow in this phase. It stores only the local confirmation
secret, consumed-token ledger, and audit JSONL under `~/.xpedition-cli/`. Set
`XPEDITION_CLI_CONFIG_DIR` to isolate these files in tests or CI. Set
`XPEDITION_NATIVE_COMMAND` to select an adapter when needed; setting it does not
bypass COM registration or license checks.

## Project Structure

```text
xpedition-cli/
├── xpedition_cli/       # CLI boundary, models, ChangeSet, backends, contract
├── tests/               # command-level contract and FCC tests
├── skills/xpedition-cli/
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

`reference.release_readiness.level` is `stable`: every public command has a
command-level test, the contract tests cover the failure and boundary behaviour as
well as the happy path, and live runs against a licensed Xpedition are recorded in
[`docs/E2E.md`](docs/E2E.md). `stable` is a statement about that evidence, not a
promise that the Xpedition automation behaves identically on another installation.

## Links

- [Agent entry](AGENTS.md)
- [Skill](skills/xpedition-cli/SKILL.md)
- [CLI contract](.agent/CLI-SPEC.md)
- [Security policy](SECURITY.md)
- [Compatibility matrix](docs/COMPATIBILITY.md)
- [Native adapter protocol](docs/NATIVE_ADAPTER.md)
- [MCP transport](docs/MCP.md)
- [E2E notes](docs/E2E.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Third-party notice](NOTICE.md)
- [MIT license](LICENSE)
