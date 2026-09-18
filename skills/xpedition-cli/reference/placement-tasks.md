# Local placement tasks

Only use this workflow when the installed binary's reference advertises the new
placement commands. Obtain their input schemas and preconditions from reference;
older released binaries do not have these commands. Unreleased implementation is
not a reason to assume native compatibility or bypass engineer authorization.

Prefer an explicit selected set and local transforms when adjusting an existing
layout. Do not use whole-board arrangement as a substitute for a small edit: it
has different effects on placement and routing. Determine the intended order,
anchor and coordinate origin before planning; origin spacing is not body clearance.

Preview, inspect every before/target and the native evidence status, then confirm
only within the user's authorization. Do not override fixed/locked states. Check
per-item results as well as the outer envelope. A failed or missing response can
leave earlier changes or an unplaced part; do not replay or silently reconstruct
the original write. Read the observed state and plan the remaining work explicitly.

Native placement DRC is not full-board DRC. Moving a component does not repair its
traces. Inspect routing, render the board, run the appropriate native checks, and
record remaining warnings before handing off. Successful coordinate read-back
is not a save/close/reopen durability test or a hardware sign-off.
