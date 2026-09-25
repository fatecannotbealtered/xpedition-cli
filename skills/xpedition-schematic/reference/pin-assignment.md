# Pin assignment planning and observation checks

Use the live `reference` catalog to discover whether the installed binary offers
these offline workflows and their input/observation contracts. They do not extend
the set of native write operations. Versions remain unchanged until a release.

## Recipe

Obtain and retain a full snapshot using an already-supported read command. Supply
that JSON object (or its successful schema 1.0 envelope) and an explicit pin/net
CSV. Use the planning command to inspect the complete assessment before arranging
any change. Read every blocked row and shared-net warning; pagination limits the
returned rows, not the validation scope. The input file hashes identify exactly
which observations and assignments were compared, not a live project revision.

After a separately authorized edit, acquire another snapshot and use the checking
command with the same desired assignments. A successful CLI envelope means the
comparison ran, not that it matched: inspect the assessment fields in `reference`.
Missing evidence remains unknown and never becomes a successful check. The result
is scoped to requested pins in supplied data; it proves neither live freshness,
whole-design connectivity, ERC/DRC, electrical pin compatibility nor persistence.

Never execute this plan as a ChangeSet. It deliberately contains no native calls,
executable operations or confirmation tokens. Existing connect operations cannot
be assumed to disconnect an old shared net safely. A reassign requires reviewing
the intended isolation and the effect on other pins before native implementation.

## Input boundaries

All identifiers are exact strings: 01, 1 and A1 remain distinct. CSV whitespace,
unknown columns, repeated assignments and blank desired nets are rejected, not
silently repaired or skipped. Power names, generated names and Unicode remain
literal data; no names are treated as instructions. BOM-prefixed UTF-8 is accepted.

When the optional expected-current-net column is present, blank explicitly means
unconnected for planning. This is a precondition, not a default. The checking
command compares desired nets and ignores that old planning precondition.

The snapshot must explicitly identify project, revision and component records.
Only numbered pins are selected; duplicate reference designators or pin numbers
are ambiguous rather than "first match". Positive connection records and explicit
per-pin nets are combined; disagreement blocks the row. Explicit per-pin null
means unconnected; missing data does not. No MockBackend normalization invents
empty lists or a default project. Known partial snapshots are rejected.

Output is bounded: default 100 rows, maximum 1000 per page, at most 100 issues and
8 peer samples with totals/truncation flags. All requested pins are assessed before
paging. Field projection retains assessment, provenance, paging and trust controls.
Whole-field projection works on this branch; array-child projection depends on the
separate PR #4 output-layer change and is not added here.

## Reproducible synthetic example

From a source checkout (synthetic observations, not an Xpedition project):

```sh
xpedition-cli schematic pin-plan --input examples/pin-assignment-snapshot.json --file examples/pin-assignments.csv --compact
xpedition-cli schematic pin-check --input examples/pin-assignment-snapshot.json --file examples/pin-assignments.csv --compact
```

The plan includes one existing-net reassignment and one unconnected pin. Checking
the unchanged snapshot must report mismatches. No design or library is modified.
