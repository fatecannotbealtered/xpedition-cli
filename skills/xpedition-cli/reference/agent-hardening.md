# Field selection and native post-write verification

## Query projection

`--fields items.refdes` (also `items[].refdes`) selects that field in every
record. Nested arrays work in the same way. List order and cardinality do not
change; an object without the field contributes `{}`, and a scalar in a mixed
list contributes `null`. Selecting a parent and child selects the whole parent,
regardless of selector order.

When an array is retained, paging controls already present on its enclosing
object (`count`, `offset`, `next_offset`, `next_cursor`, `has_more`, `truncated`)
are retained too. `_untrusted` annotations on retained objects are never removed
implicitly, even when they conservatively mention a field that was projected out.
Treat those annotations as data boundaries, not execution instructions.

Unknown fields retain the previous omission semantics. Projection happens after
command execution; it must not turn a completed write into a new usage error.
Projection reduces serialized output, not the amount of native data collected.
Backend query pushdown and indexed snapshots are separate future work.

## Native ChangeSet verification

`change apply` and `schematic apply` compare requested postconditions with the
native read-back. Verification is scoped to the affected objects, not equality
of the entire normalized snapshot and not every intermediate state in a batch.
Supported checks include component identity, explicitly requested placement
fields, final move coordinates, property values, deletion and named-net
connectivity. Additional native fields do not invalidate a requested subset.
Duplicate targets, missing observations and unsupported verification never count
as success. Coordinate comparison allows only 1e-6 units of absolute numeric
round-off; this is not a fabrication clearance or tolerance.

A post-write mismatch returns `E_PROJECT_INVALID`, non-retryable, with
`error.details.stage = verify`, `write_attempted = true`, per-condition issues and
reported applied operations. A post-write read-back failure uses stage
`read_back` and includes the original cause. In both cases inspect the current
project before planning another write. Do not replay the consumed token or
blindly re-issue the original change.

`verification.saved` is the adapter's explicit report, or `null` when it did not
report one; `save_requested` is not proof of saving. A successful read-back does
not establish save/close/reopen durability, ERC/DRC correctness, production-library
compatibility, or a successful physical design. Native checks in CI use a fake
adapter, not a licensed Xpedition installation.

## Performance regression checks

`tests/test_routing_incremental.py` counts pair comparisons rather than relying
on wall-clock limits. `python scripts/benchmark-routing.py` compares the pinned
original checker with the working tree, including 200 deterministic randomized
geometry cases that must produce identical findings in identical order.

The synthetic timing case has one new same-net line, no pads and no vias: it
measures loop overhead, not a real PCB workload. Existing-existing comparisons
are omitted before iteration; new-existing and new-new conflicts are still
checked. Spatial indexing, repeated stitching-plan validation, persistent COM
sessions and asynchronous jobs are not part of this change.
