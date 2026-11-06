---
id: designer-reopen-after-sheet-changes
title: Reopen the project after sheet changes before reading the design
kind: guide
applies_to: [schematic create, schematic get, schematic check, doc save, doc close, doc open]
tags: [designer, read-back, sheets]
---

After sheet operations, Designer reads the design wrongly until the project is closed and opened again. Sheet operations include adding and deleting sheets, and changing their size or border.

After them, Designer reports the schematic (`Schematic1`) as the active design instead of the block (`Board1`). Reading the components then fails with a type mismatch (`DesignComponents`).

schematic create replaces the sheets but does not save or reopen the project: in a session, only doc save saves. It reads the netlist back from the open project without reopening; when Designer cannot give it then, its receipt says the read-back is incomplete. doc save closes and opens the project again after saving, so after it Designer reads the design right.

What to do:

- After schematic create, run doc save before schematic get or schematic check: it saves and reopens the project.
- If schematic get or schematic check fails with a type mismatch, or reports counts that do not match the drawing, run doc save, or close and open the project (doc close, doc open) when nothing is unsaved. Then read again.
- Symbols without a reference designator (power, ground and no-connect marks) do not expose their symbol name through COM. They can be recognized only by where they sit.
