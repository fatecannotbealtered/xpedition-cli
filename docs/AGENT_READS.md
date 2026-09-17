# Scoped discovery and bounded local reads

This change is independent of the native write hardening in PR #4. It does not
merge that PR, modify native automation, create a project, or publish a release.
The source baseline is `main@d42b226a5b1812b2937ded096bf618b8692c4f9a`.

## Discover only the contract needed for a task

The default `reference` still returns the complete catalog. Its own parameter
metadata now declares three mutually exclusive selectors:

```sh
xpedition-cli reference --command "pcb trace" --compact
xpedition-cli reference --domain schematic --compact
xpedition-cli reference --schema context --compact
```

Command lookup uses an exact command path (whitespace is normalized); domain
lookup uses one whole top-level word, never a substring. Schema lookup refers
to the existing output-schema catalog, NOT a new schema for design input files.
Unknown names return `E_NOT_FOUND`; malformed or combined selectors return a
usage/validation error. Selectors on a different command are rejected before
backend access, including streaming and write commands.

A command/domain response retains every referenced success and dry-run schema,
error tables, global flags, permission metadata and release-readiness caveats.
A schema-only response has an empty command list, so it cannot contain a dangling
command schema reference. The optional `selection` block identifies the subset.
Selecting does not mutate the catalog or certify native support that it did not
already declare. The catalog is still constructed in process; this change mainly
reduces serialization and agent context, not CLI startup time. It does not finish
the broader command-registry/argument-validation refactor.

## Bound local work to the requested page

Unfiltered sequences are sliced directly. Filtered or iterator-backed reads stop
after finding the requested page plus one matching lookahead record. Only that
lookahead is needed for `has_more`; `count` remains the page size, not a total.
Agent, schematic, PCB and library query record assembly is lazy; agent/library
results are not serialized again by a second identical query filter.

Order, JSON substring matching, Unicode case folding, offset clamping, empty
results, and unbounded requests are preserved. `limit=0` preserves legacy empty
page behavior, including a non-advancing next offset when more results exist;
clients should use a positive limit when traversing pages. A missing match or a
large offset can still require a full scan. A source is consumed once, through
the lookahead, not advertised as a reusable cursor.

NativeBackend still obtains the same snapshot before local paging. This is NOT
native query pushdown, revision-bound caching, persistent COM workers, or a
whole-project validation shortcut. Native state, write confirmations, canonical
envelopes and error mappings are unchanged.

## Verification

`tests/test_reference_query.py` covers CLI selectors, schema completeness,
permissions/error metadata retention, invalid input, no native access, catalog
immutability and bounded relative output size.

`tests/test_query_page.py` compares 800 seeded cases with the previous algorithm
for both list and iterator inputs (1,600 comparisons). It also counts record
serializations through the CLI and protects against consuming an unneeded tail.
These deterministic guards do not rely on machine-dependent timing thresholds.

`python scripts/benchmark-agent-reads.py` loads the original query functions from
the pinned commit. It checks result equality before taking five timing samples
per workload. It also reports compact reference byte sizes, not guessed token
counts. Raw results and tested-source hashes are in `AGENT_READS_METRICS.json`;
execution status is in `AGENT_READS_VALIDATION.json`. These generated records are
added only after the full suite and local version/contract checks succeed.
