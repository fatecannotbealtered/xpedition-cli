# Selected-origin placement tasks (unreleased; native smoke missing)

## Scope and public references

This change turns familiar selection/translate/rotate/align/distribute workflows
into one deterministic task, not a new auto-placer or a UI click macro. Its baseline
includes confirmation concurrency PR #7 (`8e40dc910b09e5f0ca58b975739bc4bba7cef031`);
that PR is not merged or modified. PRs #4–#6 are not included.

The native binding cross-checks the published API surface in
[SiemensEDA_Python_Interface](https://github.com/EdgarMerger/SiemensEDA_Python_Interface/blob/main/xpedition_layout/layout_ifc.py),
blob `8d70329e7a838328affac2d62a53a3f46a34eb39`: `IMGCPCBComponent`,
`GetPositionX/Y`, `GetOrientation`, `Anchor`, `FixLock`, `UniqueId`, `Side`,
`UnPlace`, and `Place(x,y,orientation,bTop,eFixType,eUnit,eAngleUnit)`.
The repository's Layout directory declares MIT. No generated interface, external
implementation, dependency, or bundled proprietary manual was copied here.
The community type hints are not authoritative types; notably an integer COM
Anchor is annotated as a string. We validate the actual observations instead.

[XACT](https://github.com/RedHeadIvan/XpeditionAdvancedCapabilityToolkit)
(tree `a1945aac520cc3c7983c54a49bd6013950533ff5`, LabelAligner and AddCluster)
informed the task-level emphasis on explicit selections and local edits. Its
implementation was not copied. We did not adopt DRC-disabling batch patterns,
unqualified transactions, global selection, or automatic pop-up acceptance.
These references are evidence for API/task design, not proof of compatibility
with XPED2604 or any licensed installation.

## Two entry points

`pcb placement-plan --file TASK --input OBSERVATIONS` computes targets offline.
`pcb placement --file TASK --backend native_xpedition --project BOARD --dry-run`
reads selected native placements and previews; repeat with the returned `--confirm`
token to execute. The new native path requires an already-running Layout session.
Use the installed binary's `reference` to discover exact input JSON Schemas,
required fields, bounds, preconditions and examples. The two example files in
`examples/placement-{task,observations}.json` exercise only offline planning.

Each task selects explicit unique reference designators. Coordinates are component
CELL ORIGINS in board millimetres, on either board side, not bounding-box centres.
Rotation is counter-clockwise in board coordinates around an explicit origin;
orientation changes with it. Alignment fixes one coordinate to a selected anchor;
distribution uses explicit selection order and evenly spaced origins, not equal
body clearances. Steps calculate a final target; only one final move per changed
part is executed. A protected part may be an unchanged anchor, never a moved target.

`Anchor` values 1/2/3 and ANY nonzero `FixLock` are conservatively protected.
Unknown identity, side, units, placement or protection state fails closed. We do
not interpret every FixLock bit, override a lock, support embedded/flex-specific
placement layers, flip sides or place previously unplaced components.

## Execution and failure semantics

Preview never calls UnPlace, Place or Save. Confirm re-reads the preview and binds
the selected identities, positions, angles, sides, protection and semantic task
into the token. The adapter checks that digest again under a bounded sidecar lock
shared by this batch entry point in the same configuration directory. Other CLI
commands, other config directories, old versions, humans and external tools do
not cooperate. This is NOT a global engineering lock or a full-board revision.

The batch explicitly enables native placement DRC and requires read-back of that
setting before moving anything. Parts execute serially in selection order; after
each move and at the end, positions and identities are compared. The previous DRC
setting is restored and checked. Save is requested once only after all selected
targets verify. The CLI also checks the returned observed targets, not only a
`verification.valid` boolean. Numeric tolerance is 1e-6 mm/degrees, not a clearance.
Native quantization outside that tolerance correctly reports a mismatch.

On refusal or uncertainty, stop. Earlier moves can remain; the current part can
be unplaced. No blind rollback, all-or-none guarantee, or save of partial batches
is attempted. Results account for every selected item as unchanged, verified,
not_attempted, outcome_unknown or verification_failed. Execution/save failures
are non-retryable; transport failure after submit is explicitly outcome unknown.
Inspect the live board before another preview. There is not yet a durable operation
journal, restart recovery, or proof of save/close/reopen durability.

No routing deletion/repair or unselected placement is requested. This is not proof
that Xpedition made no incidental changes; routing topology and whole-board DRC
remain unchecked. Component swaps may fail because sequential placement sees the
other part still in the target position; DRC is not disabled to make them pass.

## Evidence and remaining native gate

Pure planning and fake COM-object tests cover transforms, strict input bounds,
protection, bottom-side placement, no preview mutation, one collection pass,
per-item verification, refusals, silent failure, DRC cleanup and save uncertainty.
CLI tests exercise preview/confirm, stale state, response mismatch, partial results,
request counts and machine discovery. CI executes no licensed Xpedition.

Before release, run on a disposable licensed board: top/bottom parts, all protection
states, stale preview, a refused move after a prior success, DRC-setting restoration,
save failure, and save/close/reopen read-back. Inspect unselected parts and routing
and run full DRC. Runtime readiness is beta until that new native evidence exists.
