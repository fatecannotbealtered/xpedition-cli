# Public-reference adoption and evidence boundaries

This work advances engineer-level task coverage without claiming an untested
Xpedition version behaves like someone else's installation. No third-party source
code, manuals, binaries or library assets are included by this change. New code
and fixtures are independently authored; links document task/API investigation,
not a blanket license to copy upstream code. Review date: 2026-09-18.

## Evidence vocabulary

`public_example`: an upstream source demonstrates a task or API name.
`offline_tested`: our deterministic code is tested against saved/synthetic data.
`adapter_tested`: our adapter path is exercised with a simulated native object.
`licensed_run`: an identified target installation and disposable project were
actually used. These labels must never be silently promoted into each other.
Historical licensed evidence elsewhere in this repo does not validate new paths.

## Adoption register

| Engineer task | Public reference | Our implementation / next gate |
|---|---|---|
| Assign pin nets from a table | XACT NetAssigner [1] | Offline plan and check implemented here; native mutation deliberately unavailable. |
| Select the right component and pin | XACT uses active selection and integer pin comparison [1] | Explicit refdes + exact string pin IDs; ambiguous/missing observations block. |
| Review effects on shared nets | XACT changes labels near pins [1] | Reassign is distinguished from connect/noop; bounded peer sample and total require isolation review. No promise of safe global rename. |
| Inspect available Designer APIs | SiemensEDA interface generator [2] | Candidate for local read-only type-library inventory; no new COM execution claimed here. |
| Batch native changes / transactions | EETB_2412 Constraint/SetPadEntry.vbs [3] | Investigate transaction and LockServer semantics on target installation; not an engineering write lock or proven rollback. |
| React to forward-annotation completion | XACT faReporter.js [4] | Candidate event/report source. Complete warnings and result provenance required; not a substitute for fresh state validation. |

[1] [XACT NetAssigner at a1945aa](https://github.com/RedHeadIvan/XpeditionAdvancedCapabilityToolkit/blob/a1945aac520cc3c7983c54a49bd6013950533ff5/NetAssigner/net_assigner.js)
([blob 674e32f](https://api.github.com/repos/RedHeadIvan/XpeditionAdvancedCapabilityToolkit/git/blobs/674e32f4fb51c688ed66259a0cdc7cd51088a879)).
Its workflow motivates table-based assignment; no JavaScript, CSV library or UI code
was copied. Its active-selection, skipped-row, integer pin and success-reporting
behavior is not our safety contract. License clearance is required before any code
reuse; this change does not rely on such reuse.

[2] [SiemensEDA interface project at 54b3c3f](https://github.com/EdgarMerger/SiemensEDA_Python_Interface/tree/54b3c3f85d9fbcf10259705e5a3a11399794c12f).
The generator is a discovery reference, not authoritative typing for our installed
version. A type-library member does not establish a safe or supported CLI operation.

[3] [EETB batch/constraint example](https://github.com/Aragornian/EETB_2412/blob/main/Constraint/SetPadEntry.vbs).
Prior investigation only; this mutable link is NOT a pinned implementation input.
No transaction calls, DRC disabling or error suppression from it were adopted.

[4] [XACT report workflow at a1945aa](https://github.com/RedHeadIvan/XpeditionAdvancedCapabilityToolkit/blob/a1945aac520cc3c7983c54a49bd6013950533ff5/faReporter.js).
A future event implementation needs its own installation-specific tests.

## Native integration gate

Before implementing/exposing pin mutations: verify actual typed pin identity,
component hierarchy, wire/label ownership and disconnection semantics; bind plans
to a freshly read target; serialize engineering writes; record partial/unknown
outcomes; compare requested postconditions and unaffected peers; save/close/reopen.
Do not map `reassign` to existing additive `connect` and assume the old net vanished.

## Tests and review

Helper tests cover exact IDs, unknown and contradictory observations, duplicate
targets, stale preconditions, post-edit checks, paging and bounded peers, malformed
CSV/JSON and immutability. CLI tests reject native/write/output arguments before
file/backend access, preserve JSON/trust controls and exercise both new leaves.
The generated validation record records actual CI results. No native design is
opened by this work. PRs #4--#7 remain independent; combined-tree behavior must be
validated before release. Preserve all Unreleased notes when combining branches.
