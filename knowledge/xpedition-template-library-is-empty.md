---
id: xpedition-template-library-is-empty
title: The installation ships no parts; create them with part create
kind: guide
applies_to: [part list, part get, part create, schematic create, project create]
tags: [library, parts, template]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/COMPATIBILITY.md (A stock installation ships no component library) and docs/NATIVE_ADAPTER.md of npm @fateforge/xpedition-cli@1.0.3, measured on XPED2604, 2026-09-12; docs/E2E.md of the same package (What the recorded runs do not cover)
updated_on: 2026-11-05
---

Xpedition 2604 comes without a component library. The template library under `SDD_HOME/standard/templates/dxdesigner/TemplateLibrary` is an empty skeleton. Every category folder exists (`Resistors`, `Capacitors` and so on). But every parts database (`*.pdb`) and every cell database (`*.cel`) is an empty stub, and the device symbol folders hold no files. Only the borders, the built-in symbols, `Globals` (power and ground) and `Drawing.cel` have content.

A project copied from that template therefore has no resistor, capacitor or IC. Asking Designer to place one fails with Designer's own message:

```
6055  Symbol Resistors:R.1 not found, empty or a block.
```

This is not a fault of the tool or of COM. Placing a symbol that does exist, such as `builtin:espl1`, works and reads back with the right coordinates.

What to do:

1. Run part list on the project. Expect it to be empty, or to hold only parts made earlier.
2. Create every part the design uses with part create, before schematic create. Each part needs a symbol, a footprint cell and a pin map.
3. Check each new part with part get.
4. If the person has a company central library, ask them about it. Do not assume one exists.

The live runs used parts made this way: placeholders and IPC-7351B footprints from datasheet dimensions, not a production library. Most of their part numbers were placeholders.
