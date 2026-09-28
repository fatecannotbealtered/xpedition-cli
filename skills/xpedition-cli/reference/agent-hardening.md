# Field selection and what a verified write proves

## Field selection

`--fields items.refdes` (also `items[].refdes`) selects that field in every
record. Nested arrays work the same way. List order and length do not change; an
object without the field contributes `{}`, and a scalar in a mixed list
contributes `null`. Selecting a parent and a child selects the whole parent.

When an array is kept, the paging fields beside it (`count`, `offset`,
`next_offset`, `has_more`) are kept too. `_untrusted` annotations on kept objects
are never removed, even when they name a field that was projected out: treat them
as data boundaries. Unknown fields are left out silently. Projection happens after
the command ran; it shortens the output, not the work Xpedition did, and it never
turns a completed write into a usage error.

## Paging

Commands whose `reference` params include `limit` and `offset` page their list:
pass a small `--limit` when exploring and follow `next_offset` only when more is
needed. `count` describes the page; `total`, where given, the whole list. Any
other command refuses `--limit`.

## Verified writes

A confirmed write reads its result back and compares it with what was asked:

- `schematic edit` checks each operation's own postconditions -- a part placed,
  moved, deleted (and gone from every net), a property set, a pin connected or
  disconnected, a net renamed with all its pins on the new name -- not the
  equality of the whole design, and not intermediate states. Coordinates compare
  within 1e-6 units of numeric round-off, not a clearance.
- `schematic draw` compares the netlist read back with the planned one, including
  nets and parts the plan never asked for.
- `library add` reads the library back and checks each part against its symbol,
  cell and padstacks.
- `pcb` writes read the board back (`read_back`, `after`) and say what they
  observed.

A mismatch after a write is `E_PROJECT_INVALID`, not retryable, with
`details.stage` (`verify` or `read_back`), `write_attempted: true`, the issues and
the operations reported applied. Inspect the project before planning another
write; never replay the token or resend the change blindly.

A verified result proves the requested postconditions only: not that the design
is electrically right (that is `schematic check` and `pcb check`), not that a
library part matches its datasheet, and not that the board can be built.
