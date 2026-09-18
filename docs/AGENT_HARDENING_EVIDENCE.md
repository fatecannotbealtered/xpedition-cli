# Agent hardening: verification record

Recorded on 2026-09-17. This is evidence for the first hardening change, not a
claim that the entire AI-native roadmap or licensed-native validation is complete.

## Source and execution

- Baseline: `2a66f2dc52c95a7165c9cc675ed01b0c65d8e7b5` (1.0.0).
- Verified implementation: `f656b8de426e3175c472a4a01e42dd5b941dbd64`.
- [GitHub Actions run 35228596168](https://github.com/fatecannotbealtered/xpedition-cli/actions/runs/35228596168), job `105226443964`.
- [Raw regression and benchmark artifact](https://github.com/fatecannotbealtered/xpedition-cli/actions/runs/35228596168/artifacts/10500450492).
- Environment: Ubuntu 24.04 runner, x86-64, Python 3.12.14, Ruff 0.16.8.

The branch-only workflow first ran the new command-boundary and routing
regressions against the unmodified baseline. The result was **10 failed, 3
passed**: explicit native/invalid backend initialization, cross-backend confirm,
array projection, native verification reporting, write-flag discovery and the
old-old routing comparison guard reproduced the target defects or missing
contracts. This was an expected regression-proof step, not a passing baseline.

The workflow then applied a patch only after validating the original source blob
hashes. Ruff import sorting/formatting affected only the touched Python files.
The complete working-tree test suite and quality guards passed before the
implementation was committed. The temporary patch script and branch-only
workflow removed themselves from the final tree; the normal repository CI and
release workflows were not changed.

## Results

| Check | Observed result |
|---|---|
| `ruff check xpedition_cli tests` | Passed |
| `ruff format --check xpedition_cli tests` | Passed, 60 files formatted |
| `pytest -q` | 246 passed, 1 warning, 16.63 s |
| `node scripts/check-version.js` | Passed, derived versions remain 1.0.0 |
| `node scripts/check-spec.js --local-only` | Passed, generated contract matches canonical JSON |
| Randomized routing differential check | 200 seeded cases, identical findings and order |

The warning was an existing invalid escape sequence in a fixture in
`tests/test_native_com_adapter.py:305`. Forty test cases were added across
`test_agent_hardening.py`, `test_field_projection.py`,
`test_native_verification.py`, and `test_routing_incremental.py`; existing tests
were not weakened or removed. The local-only spec check is not a claim that the
network comparison ran; ordinary PR CI runs the full guard separately.

## Synthetic routing benchmark

The same function workload was executed five times before and after the change.
The fixture has one new straight segment, all segments on the same net and layer,
no pads and no vias. It measures avoidable pair-loop overhead, **not Xpedition
COM performance, complete DRC performance, or whole-board routing speed**.

| Existing segments | New segments | Baseline median | Patched median |
|---:|---:|---:|---:|
| 1,000 | 1 | 18.546 ms | 0.450 ms |
| 3,000 | 1 | 169.827 ms | 1.389 ms |
| 10,000 | 1 | 1,901.848 ms | 4.522 ms |

Reproduce from a full git checkout with `python scripts/benchmark-routing.py`.
The script loads the original checker from the pinned baseline commit and keeps
the raw samples in its JSON output. CI regression tests count comparisons rather
than imposing machine-dependent wall-clock thresholds. Existing-existing pairs
are skipped before iteration; new-existing/new-new checks and their geometric
predicates remain. Spatial indexing and repeated stitching-plan work remain
separate optimizations.

## Native verification scope and remaining gate

The command-boundary tests use a **fake NativeBackend**, including an adapter
that reports success while leaving coordinates unchanged, and a post-write
read-back timeout. They prove that the CLI checks observed postconditions and
returns a non-retryable, stage-labelled failure rather than assuming success or
inviting blind replay. They do not run Xpedition or exercise a production project.

`verification.valid` is scoped to `requested_postconditions`. `saved` is only the
adapter's explicit report (or `null` when absent), not a save/close/reopen proof.
The checker does not add support for operations missing from the native adapter.
It cannot establish ERC/DRC correctness, real-part compatibility or electrical
function. In particular, a genuine library part number that differs from an
input placeholder is a verification mismatch, not something to silently waive.

Before releasing the changed native path, record a disposable-project run on the
licensed installation: place/move/connect with actual library identifiers, verify
the reported fields, save/close/reopen and compare again. Confirm that partial
failure handling does not cause an automatic duplicate write. No new licensed
native smoke evidence is claimed by this change, and no production project was
opened or modified during these tests.

## Subsequent work, outside this change

Command-definition single-sourcing and targeted reference discovery; strict
per-command input validation; backend query pushdown or revision-bound snapshot
reuse; batch placement; long-running job status/result/recovery; and a persistent,
serialized native worker remain separate work. None is implied complete by these
regression results.
