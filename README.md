# xpedition-cli — limited preview

An experimental Windows CLI and three Agent Skills for a separately licensed
Siemens Xpedition installation. This project is independent of Siemens. It does
not include Xpedition, a commercial CAD SDK, license credentials, or private
engineering projects and run evidence.

The CLI reports version `2.0.0-dev.1`; Python distribution metadata normalizes
that version to `2.0.0.dev1`. This [prerelease](https://github.com/fatecannotbealtered/xpedition-cli/releases/tag/v2.0.0-dev.1)
is a limited preview, not a claim of full product qualification. The runtime
`reference` continues to report `unpublishable` and missing functional contract
coverage. PCB save/reopen, general editing, complete rules, DRC and manufacturing
deliverables have not been qualified for this build. Silkscreen editing remains
disabled. The [limited native smoke](docs/E2E.md) covers only the stated owned
startup, close diagnostic and exit, with engineering persistence still unknown.

## Install and discover

Download the Windows x64 onedir ZIP from the prerelease and verify its published
SHA-256. Keep the entire `app/` directory together. Windows, a licensed Xpedition
installation and its automation prerequisites are separate. Run the exact
candidate's `--version`, `reference --index --compact`, and leaf-specific
`reference --command` before choosing operations. Configure a user-authorized
scope and task-local `XPEDITION_CLI_CONFIG_DIR`; do not reuse old journals to
bypass a prior unknown outcome.

The three matching Skills are available from the same release and [tagged source](https://github.com/fatecannotbealtered/xpedition-cli/tree/v2.0.0-dev.1/skills).
They may be installed together with:

```powershell
npx skills add https://github.com/fatecannotbealtered/xpedition-cli/tree/v2.0.0-dev.1 -y -g
```

The CLI and Skills are separate. Match the exact executable, payload and Skill
versions to their release checksums; historical trials cannot qualify a new build.
For an authorized effect, inspect the live contract, scope, targets, protections
and preview before confirmation. Keep execution, change, persistence and
verification separate; do not automatically replay an unknown result.

## Source and tests

Python 3.11+ is required to build from source. Private historical evidence and
recorded engineering fixtures are excluded. The 49 historical test functions
that required those private fixtures are replaced with explicit skip stubs;
their original checks and native-recorded byte coverage are not reproduced.
Run tests only with a pre-import isolated home, configuration and output roots,
and without access to a live CAD session or old state.

Some development registries retain sanitized historical evidence metadata and
hashes for context. Example paths do not identify evidence available in this
snapshot, and a historical generated `errors=[]` is not a current check result.
The excluded evidence cannot be independently revalidated from this snapshot.

Third-party terms are in `THIRD-PARTY-LICENSES` and `license-index-v4.json`.
