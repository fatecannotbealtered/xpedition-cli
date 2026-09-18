# API inventory review handoff

Implementation verified on Windows: `323dcd4ca908edbf73af8302624ea5c6373409ba`.
This document changes no execution behavior. The PR remains draft/unmerged.

## Executed evidence

[Windows validation run 35298644634](https://github.com/fatecannotbealtered/xpedition-cli/actions/runs/35298644634)
passed the complete **257-test suite**, Ruff lint/format, version consistency and
the local canonical-contract generation guard, then executed the public inventory
CLI against the real standard OLE metadata installed on the Windows runner.

The CLI observed 42 type headers, 22 members on `IFont`, and four variable
descriptors on `GUID`. The smoke asserted complete-in-scope results, no issues,
unchanged input hashes, and the command's explicit non-execution markers. The
real metadata loader was pywin32; no Xpedition installation or license was used.
Machine-readable results and normalized source hash are in
`API_INVENTORY_WINDOWS_VALIDATION.json`. Raw JUnit and JSON outputs are attached
to the run. Only JSON was uploaded, not the Windows library or extracted binary.

## Corrections found before handoff

The initial Linux fake-provider tests did not catch an assumed `LoadTypeLibEx`
entry that pywin32 does not expose. The actual binding is `LoadTypeLib`; the
implementation enforces the absolute-path input that Microsoft documents as
non-registering. It does not fall back to a bare filename or application factory.
`GetNames` was also aligned to its one-argument Python binding in production and
in the strict fake provider. The real Windows smoke now passes with these calls.

The system `stdole2.tlb` on this runner was actually a PE resource container. The
smoke harness extracted its sole TYPELIB through resource-only, non-executable
mapping into a temporary standalone fixture. The public CLI still rejects PE
containers; its input restrictions were not relaxed to satisfy a test.

Historical validation records remain scoped to what they actually tested:
`API_INVENTORY_VALIDATION.json` is the first 256-test fake-provider run;
`API_INVENTORY_BINDING_VALIDATION.json` is the later 257-test loader correction;
`API_INVENTORY_WINDOWS_VALIDATION.json` verifies the corrected member calls and
real standard-OLE smoke. None is evidence of target Xpedition API semantics.

## Review boundaries

Normal PR CI and the permanent metadata smoke independently validate the committed
tree after this handoff commit. Consult their actual results; the preceding
Windows run does not by itself establish the later workflow status.

No discovered member is callable through this command. Inventory does not prove
units, preconditions, native support, licensing, transaction/rollback semantics,
engineer-level task completion, or save/close/reopen durability. Using a product
API still needs its own implementation, authorization and target-version evidence.

This branch does not include the other draft PRs. In particular #8 provides the
separate saved-observation pin-plan/pin-check task workflow; #4--#7 harden different
boundaries. Main and those branches remain unchanged. Combined-tree tests and
preserving all Unreleased notes/Skill guidance are required before a release.
