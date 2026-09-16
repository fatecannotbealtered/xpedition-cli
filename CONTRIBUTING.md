# Contributing to xpedition-cli

*English | [中文](CONTRIBUTING_zh.md)*

Read [AGENTS.md](AGENTS.md) and the pinned `.agent/` specifications before
changing behavior. The CLI is agent-first: stdout is a single JSON envelope,
errors use the canonical code/exit/retryable mapping, and writes use
`--dry-run` followed by a single-use `--confirm` token.

## Development setup

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check xpedition_cli tests
ruff format --check xpedition_cli tests
node scripts/check-version.js
node scripts/check-spec.js --local-only
python -m xpedition_cli.main --help
```

Use `XPEDITION_CLI_CONFIG_DIR` for an isolated confirmation secret and audit
directory during tests. Do not use production design files; native E2E is
limited to the disposable sequence in [`docs/E2E.md`](docs/E2E.md).

## Adding a command or backend

1. Read the relevant sections of `.agent/CLI-SPEC.md` and `.agent/SEC-SPEC.md`.
2. Add the domain model/backend operation under `xpedition_cli/`.
3. Register the command, schema, examples, permission tier and blast radius in
   `xpedition_cli/reference_data.py`.
4. Keep external values marked in `_untrusted` and route every mutating action
   through the ChangeSet confirmation flow.
5. Add command-level tests for success, invalid input, errors, envelope shape,
   exit code and stdout/stderr behavior. The FCC guard must remain green.
6. Update both READMEs, the Skill and `CHANGELOG.md`.

Do not claim NativeBackend or a new Xpedition version until a recorded licensed
smoke test proves it. Keep `.agent/*`, `contract/contract.json`, and generated
contract code synchronized through `scripts/sync-spec.js`; never hand-edit a
vendored spec or generated module.

## Pull requests

- Use a focused branch and a Conventional Commit message.
- Include tests and documentation for every observable behavior change.
- Run lint, format, tests, version and spec guards locally.
- Do not commit credentials, confidential design data, build artifacts or real tokens.

