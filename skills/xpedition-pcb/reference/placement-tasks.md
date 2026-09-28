# Local placement tasks

Obtain the input schemas and preconditions of `pcb placement-plan` and
`pcb placement` from `reference`. The native path has been smoke-tested on one
disposable board, top side only (`reference`'s `release_readiness` says what that
covers); that is
not a reason to assume native compatibility or bypass engineer authorization.

Prefer an explicit selected set and local transforms when adjusting an existing
layout. Do not use whole-board arrangement as a substitute for a small edit: it
has different effects on placement and routing. Determine the intended order,
anchor and coordinate origin before planning; origin spacing is not body clearance.

The native path reads a running Xpedition Layout session; `placement-plan` is
offline and needs none. Check `doctor`'s `native_session` for what is attached
before previewing, and start Layout explicitly rather than letting the command
activate it.

Preview, inspect every before/target and the native evidence status, then confirm
only within the user's authorization. Do not override fixed/locked states. Check
per-item results as well as the outer envelope. A failed or missing response can
leave earlier changes or an unplaced part; do not replay or silently reconstruct
the original write. Read the observed state and plan the remaining work explicitly.

Native placement DRC is not full-board DRC. Moving a component does not repair its
traces. Inspect routing, render the board, run the appropriate native checks, and
record remaining warnings before handing off. Successful coordinate read-back
is not a save/close/reopen durability test or a hardware sign-off.
