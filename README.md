<h1 align="center">xpedition-cli</h1>

<p align="center"><strong>Agent-native control of Siemens Xpedition: from a design description to a routed board and its fabrication package, every write previewed and verified</strong></p>

<p align="center"><a href="README.md">English</a> · <a href="README_zh.md">中文</a></p>

<p align="center">
  <a href="https://github.com/fatecannotbealtered/xpedition-cli/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/fatecannotbealtered/xpedition-cli/ci.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=CI"></a>
  <a href="https://www.npmjs.com/package/@fateforge/xpedition-cli"><img alt="npm" src="https://img.shields.io/npm/v/@fateforge/xpedition-cli?style=for-the-badge&logo=npm&logoColor=white&label=npm&color=CB3837"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-7C3AED?style=for-the-badge"></a>
</p>

`xpedition-cli` drives a licensed Xpedition installation -- Designer for the
schematic, Layout for the board -- through their automation interfaces, on
Windows. An agent describes a schematic, adds the parts it needs to the project's
own library, draws it, packages it, creates the board, places, routes, checks and
exports it, with 54 commands that each answer in one JSON envelope. The whole
chain is recorded in [docs/E2E.md](docs/E2E.md) from one Windows installation of
XPED2604. The CLI never edits Xpedition's private databases directly, and every
write is gated by `--dry-run` then `--confirm <token>`.

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
`...xpedition-cli@v1.0.3`. From a checkout, `python -m pip install -e ".[native]"`
does the same. Each release also publishes a standalone binary to npm as
`@fateforge/xpedition-cli`; it has no Windows adapter, so it runs only the
commands that need no Xpedition (`reference`'s `needs: none`: previews, plans,
metrics from a file, project backups). There is no CLI login: Xpedition's own
licensing stays in the user's installation (see [Native adapter
protocol](docs/NATIVE_ADAPTER.md)).

## What It Does

Twelve steps, each a handful of commands (`reference` lists them as `workflow`):

| Step | Commands |
|---|---|
| Check the machine, start Designer or Layout | `doctor`, `session start` |
| Create the project from a template | `project create` |
| Put the parts the design needs in the library | `library list`, `library add`, `library render`, `library import` |
| Preview the design, then draw the schematic | `schematic render`, `schematic draw` |
| Build the placeholder parts, package the design | `library build`, `library check` |
| Check the schematic and its BOM | `schematic check`, `bom check`, `schematic export` |
| Back up, create the board, bring the schematic in | `project backup`, `pcb create`, `pcb annotate` |
| Shape the board | `pcb outline`, `pcb holes`, `pcb rules` |
| Place the parts and their labels | `pcb arrange`, `pcb move`, `pcb labels` |
| Route and pour | `pcb route`, `pcb trace`, `pcb via`, `pcb stitch`, `pcb pour` |
| Check, measure, look | `pcb check`, `pcb geometry`, `pcb metrics`, `pcb render` |
| Fabrication outputs | `pcb export`, `bom export` |

Around them: `schematic edit` changes a drawn sheet in place (place, move or
delete a part, set a property, connect or disconnect a pin, rename a net), the
reads (`schematic components|nets|sheets`, `pcb info`, `library show`), the
windows (`schematic show`, `pcb show`), `project restore`, sessions
(`session status|stop`), the knowledge base (`kb list|add|remove`) and the
self-description (`context`, `doctor`, `reference`, `changelog`, `version`).

**The design library.** A part comes into the project's central library one of
two ways. `library import` takes it from an existing Xpedition library --
another project's, or a copy of the company's (`--from LIB.lmc`) -- with the
symbols, cells, padstacks, pads and holes it uses, each in its source partition;
the source is only read, and `library list|show|check --library LIB.lmc` looks
into it first. `library add` creates it from a parts file: per part a symbol (a
box with named, typed pins, or a built-in kind), a footprint and the pin map.
Footprints come from the datasheet's dimensions by IPC-7351B (chip, molded,
gull-wing, J-lead, QFN/DFN with an exposed pad, through-hole), from lands given
one by one (several may share a pin; slotted and unplated holes, locating pegs)
or from a cell the library holds. Either dry run says what it adds, keeps
(identical content is left alone) or would replace; the write is read back and
checked. A design then names the part: `"symbols": {"LDO": {"part":
"TPS7A2033PDBVR"}}`.

Every write reads its result back: a draw compares the netlist with the plan, an
edit verifies each operation, `library add` and `library import` check each part,
`pcb` writes report what they observed. Risk tier: **T2**. The writes that destroy
work that is not archived also need `--dangerous` next to the token: `schematic
draw`, `pcb unroute`, `pcb create --replace` (which first zips the layout folder
beside the project), `pcb arrange` on a routed board, `pcb route --unroute`, `pcb
annotate --unroute`, a `library import` or `library add` that replaces what the
library holds, and `project restore`. `project backup` zips the whole project
before any of them. See [SECURITY.md](SECURITY.md).

The live command and schema source is `xpedition-cli reference --compact`.

## Machine Contract

- JSON is the default and stdout contains exactly one envelope.
- Success and failure both include `ok`, `schema_version`, and `meta.duration_ms`.
- Errors use the canonical `E_*` to exit-code and `retryable` mapping in
  [`contract/contract.json`](contract/contract.json).
- Logs and diagnostics go to stderr. `--json` is a compatibility alias for
  `--format json`; `--format text` is for humans and `raw` returns the payload.
- Values that come from the design, the library or files are listed in `_untrusted`.
- IDs are strings and timestamps are ISO 8601 UTC.
- Unknown options, missing required ones and malformed values are refused before
  anything runs.

## Configuration

The CLI keeps its local state under `~/.xpedition-cli/`: the confirmation secret,
the consumed-token ledger and its lock, the audit JSONL, knowledge-base links, the
session record (`session.json`) and the library export cache. Set
`XPEDITION_CLI_CONFIG_DIR` to isolate these files. Set `XPEDITION_NATIVE_COMMAND`
to select an adapter; it does not bypass COM registration or licensing, and
`XPEDITION_SDD_HOME` when the installation cannot be discovered.

Company rules -- layout rules, drawing conventions, review checklists -- stay in
the company's knowledge base. `kb add --name NAME --url URL --about TEXT` (a
write: dry run, then confirm) records which document applies, and `context` lists
them for the agent, which reads each with its own tools. The CLI never fetches a
document.

## Project Structure

```text
xpedition-cli/
├── xpedition_cli/               # CLI boundary, registry, planners, library, adapter
│   └── cli/                     # one module per command domain; registry.py lists all
├── tests/                       # command-level contract and FCC tests (a faked adapter)
├── skills/xpedition-cli/        # entry Skill: install, sessions, projects, library, safety
├── skills/xpedition-schematic/  # schematic Skill: drawing, editing, checking, BOM
├── skills/xpedition-pcb/        # board Skill: Layout, placement, routing, checks, fabrication
├── contract/                    # canonical vendored machine contract
├── examples/                    # design files and a placement task
├── scripts/                     # spec, version, and npm wrapper tooling
├── docs/                        # compatibility, E2E, native adapter, evaluations
└── .agent/                      # pinned AI-native CLI specifications
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

The tests drive the real CLI against a faked adapter at the one process boundary
the CLI crosses; no Xpedition is needed. `reference.release_readiness.level` is
`beta`: every public command has a command-level test, and live runs against a
licensed Xpedition are recorded in [`docs/E2E.md`](docs/E2E.md); `reference` names
what keeps it from `stable`. The level is a statement about that evidence, not a
promise that the automation behaves identically on another installation.

## Links

- [Agent entry](AGENTS.md)
- [Skills](skills/xpedition-cli/SKILL.md): the entry Skill, with [xpedition-schematic](skills/xpedition-schematic/SKILL.md) and [xpedition-pcb](skills/xpedition-pcb/SKILL.md)
- [CLI contract](.agent/CLI-SPEC.md)
- [Security policy](SECURITY.md)
- [Compatibility matrix](docs/COMPATIBILITY.md)
- [Native adapter protocol](docs/NATIVE_ADAPTER.md)
- [E2E notes](docs/E2E.md)
- [Skill evaluations across models](docs/EVALS.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Third-party notice](NOTICE.md)
- [MIT license](LICENSE)
