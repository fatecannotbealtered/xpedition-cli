# Read-only API inventory: source references and review record

Independent implementation based on main d42b226. Existing draft PRs remain
unchanged. This is the metadata-investigation part of the public-reference adoption
work, separate from offline pin assignment and native write hardening.

## References and adoption

- [SiemensEDA Python Interface, pinned 54b3c3f](https://github.com/EdgarMerger/SiemensEDA_Python_Interface/tree/54b3c3f85d9fbcf10259705e5a3a11399794c12f):
  motivates inspecting actual installed types rather than guessing API names or
  adopting another version's heuristic annotations. No code or library is copied.
- [Microsoft LoadTypeLibEx](https://learn.microsoft.com/en-us/windows/win32/api/oleauto/nf-oleauto-loadtypelibex):
  REGKIND_NONE disables the registration process. The loader is supplied an explicit
  standalone local type library; no ProgID or application activation fallback.
- pywin32 published metadata definitions:
  [TYPEATTR](https://mhammond.github.io/pywin32/TYPEATTR.html),
  [FUNCDESC](https://mhammond.github.io/pywin32/FUNCDESC.html),
  [VARDESC](https://mhammond.github.io/pywin32/VARDESC.html),
  [ELEMDESC](https://mhammond.github.io/pywin32/ELEMDESC.html).
  Member metadata stays structural, not a guessed Python or JSON input schema.
  Documentation was checked 2026-09-18; actual Windows binding smoke is separate.

No third-party source, manuals, binaries or proprietary metadata is distributed by
this change. Installation metadata is read only when explicitly requested. Reuse
of upstream implementation code or redistribution of product metadata needs its
own license review; linked public examples are not blanket permission.

## Interpretation

This closes a discovery gap, not an execution gap. A function or property name in
a TLB does not prove callable behavior, units, state/permission preconditions, safe
rollback or compatibility with our adapter. All results explicitly deny application
activation, member invocation, registration request and capability grants.

Inventory is constrained and paged; it is not a complete COM browser. It does not
resolve aliases, inherited interfaces, referenced libraries or events into a full
object graph. Metadata errors are visible; missing entries are not removed silently.
It uses local file hashes rather than a mutable published API listing. Hashing
before/after is ordinary change detection, not a hostile-input sandbox.

## Verification and native gate

Unit tests cover discovery without member reads, exact selection, ambiguous and
unreadable headers, getter/setter classification, omission of defaults/constants,
failed member reporting, bounded descriptor shapes/counts, file change detection,
COM lifecycle, non-Windows handling, unsupported inputs/flags and output controls.
CLI tests run against fake metadata providers on the normal matrix. The separate
Windows smoke loads stdole2.tlb without application activation and checks the real
binding, paging data and variable/function descriptors. No Xpedition is involved.

The bootstrap writes API_INVENTORY_VALIDATION.json only after the full suite and
existing quality gates pass. That record covers fake-provider testing; the actual
Windows smoke publishes its own run-bound JSON evidence. Keep the distinction.
Before using a discovered product API in a native command, verify it on the target
installation with an explicit disposable project and operation-specific safety,
state binding, partial-failure and readback tests. Do not promote inventory findings
automatically into supported/native-verified command metadata.
