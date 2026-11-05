---
id: designer-reopen-after-sheet-changes
title: Reopen the project after sheet changes before reading the design
kind: guide
applies_to: [schematic create, schematic get, schematic check, doc save, doc close, doc open]
tags: [designer, read-back, sheets]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/COMPATIBILITY.md (Designer drawing facts verified on XPED2604; Drawing a whole schematic) of npm @fateforge/xpedition-cli@1.0.3, measured on XPED2604 in September 2026; capability survey docs/APPLICATION.md (Read-back; CAP-034)
updated_on: 2026-11-05
---

After sheet operations, Designer reads the design wrongly until the project is closed and opened again. Sheet operations include adding and deleting sheets, and changing their size or border.

After them, Designer reports the schematic (`Schematic1`) as the active design instead of the block (`Board1`). Reading the components then fails with a type mismatch (`DesignComponents`).

schematic create replaces the sheets, then saves, reopens the project and compares the netlist it reads back with the design file. That is why it ends with a reopen.

What to do:

- If schematic get or schematic check fails with a type mismatch, or reports counts that do not match the drawing, reopen the project: doc save, doc close, then doc open in the same Designer session. Then read again.
- Symbols without a reference designator (power, ground and no-connect marks) do not expose their symbol name through COM. They can be recognized only by where they sit.
