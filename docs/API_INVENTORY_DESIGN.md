# Read-only API inventory: source references and review record

Independent implementation based on main d42b226. Existing draft PRs remain
unchanged. This is the metadata-investigation part of the public-reference adoption
work, separate from offline pin assignment and native write hardening.

## References and adoption

- [SiemensEDA Python Interface, pinned 54b3c3f](https://github.com/EdgarMerger/SiemensEDA_Python_Interface/tree/54b3c3f85d9fbcf10259705e5a3a11399794c12f):
  motivates inspecting actual installed types rather than guessing API names or
  adopting another version's heuristic annotations. No code or library is copied.
- [Microsoft LoadTypeLib](https://learn.microsoft.com/en-us/windows/win32/api/oleauto/nf-oleauto-loadtypelib):
  supplying a path disables the legacy automatic-registration behavior. pywin32
  exposes LoadTypeLib, not LoadTypeLibEx; the code explicitly requires the resolved
  absolute path. No filename-only lookup, ProgID or activation fallback is used.
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


## Real-binding correction

The first Windows smoke revealed that the system stdole2.tlb is a PE resource
container. The smoke harness now extracts its sole TYPELIB as a data/image resource
without executable initialization; CLI input restrictions remain unchanged.
The next run reached a binding error. Upstream PythonCOM.cpp confirms pywin32
exports LoadTypeLib, not the initially assumed LoadTypeLibEx. The implementation
and fake provider now use the actual one-argument binding with an absolute-path
guard. Microsoft explicitly documents non-registration for a supplied path.
No permissive fallback or ctypes COM-pointer wrapper was introduced.

The initial 256-test validation record is historical and did not establish real
binding compatibility. API_INVENTORY_BINDING_VALIDATION.json records the corrected
suite; the independent Windows smoke must still pass before claiming that binding
is tested. System OLE metadata remains distinct from target Xpedition evidence.


The remaining metadata calls were checked against the published pywin32 binding.
GetNames accepts the member ID, not the native C++ output-buffer length argument.
Both production calls and the strict fake provider now use the one-argument API.
Reference: https://mhammond.github.io/pywin32/PyITypeInfo__GetNames_meth.html .
The Windows correction run validates the full suite and the actual standard OLE
fixture before recording API_INVENTORY_WINDOWS_VALIDATION.json. That is still not
an Xpedition installation or a call to any discovered application method.
