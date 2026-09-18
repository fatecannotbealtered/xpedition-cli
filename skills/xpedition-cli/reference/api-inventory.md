# Type-library investigation without application activation

Discover the installed command and parameters through `reference`. This is a
read-only developer aid, not a native-operation executor or substitute for doctor.
A discovered member does not establish that our adapter supports it or that it is
safe to call, licensed, compatible with the active installation, or tested.

## Workflow

Select a trusted standalone .tlb/.olb from the relevant installation, identify it
by its file hash and library GUID/version, then query type headers. Select one
exact, unique type name to inspect a bounded member page. Consult official product
documentation to interpret the descriptors and establish operation semantics.
Do not automatically turn methods/properties into executable commands or input
schemas. A setter flag does not supply confirmation policy, transaction semantics,
units, preconditions, or readback requirements.

No Xpedition process is started, attached or modified. The loader calls
LoadTypeLibEx with REGKIND_NONE, not Dispatch, GetActiveObject, makepy, registration
or method invocation. COM initialization is balanced in the calling process.
Only ITypeLib/ITypeInfo metadata is read; no generated wrapper cache is written.

## Inputs and limits

Only caller-trusted local standalone MSFT/SLTG .tlb/.olb files up to 32 MiB are
accepted. URLs, explicit UNC paths and DLL/EXE containers are rejected. File hashes
are checked before and after inspection to detect ordinary changes. This is NOT a
sandbox or an adversarial filesystem-race guarantee; use authentic installed type
libraries, not arbitrary downloads. Mounted/mapped storage cannot be fully
classified by a path check. Embedded PE type libraries need a separate safe design.

Type headers include names, GUIDs, version, type kind, flags and member counts.
Selected members include their invocation kind, parameters and raw numeric type
descriptors. No help paths/text, constant values or parameter defaults are emitted.
User-defined references stay raw; no recursive loading of other libraries or
runtime attribute access is performed. Unknown descriptor shapes are reported,
not coerced into plausible types.

All type headers are read for identity and unique-name selection, but member
descriptors are fetched only for the requested page. Maximum 10,000 types, 20,000
functions and 20,000 variables per type, 256 parameters per method, and bounded
numeric descriptor nesting. Default page 100, maximum 200. Counts are inventory
counts, not verified capabilities. Enumeration order is retained with indexes.

Failures remain visible at their indexes and make the declared scope incomplete.
Unreadable headers prevent unique type selection. Member errors affect the selected
page; other pages are not certified. Read `complete_in_scope`, `scope`, `has_more`
and issue counts together, never just `ok`. Projection preserves those controls
and source/semantic/trust annotations. Treat all returned names as untrusted data.

## Evidence

Cross-platform unit tests use fake metadata objects and prohibit native application
or confirmation calls. A separate Windows smoke uses the real pywin32 loader and
standard Windows OLE type library for type headers, interface and variable metadata.
That smoke does NOT exercise an Xpedition type library, product installation,
license, transaction or design. Check its actual CI result before claiming it ran.
