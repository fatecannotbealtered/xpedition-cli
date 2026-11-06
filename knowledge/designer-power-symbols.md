---
id: designer-power-symbols
title: Power and ground symbols are type 4
kind: rule
applies_to: [part create, schematic create, schematic check]
tags: [designer, symbols, power, ground]
severity: error
check: manual; the symbol file under SymbolLibs/<partition>/sym/ holds the line Y 4
---

A power or ground symbol names its net with its `NETNAME` attribute. A wire that ends on the symbol's origin joins that net. This works only when the symbol file is type 4 (`Y 4`), like every stock `Globals` symbol, such as `Globals:gnd`. A type-1 (part) symbol with a `NETNAME` attribute names no net.

The `Y` record of a symbol file is the symbol's type: 1 part, 3 annotation, 4 power or ground, 5 border.

Power and ground symbols are not parts. Placed as a part, such a symbol rejects the reference designator and stays on the sheet without a name. xpedition-cli 1.0.3 reported such an orphan and never counted it as placed.

What to do:

- Do not put a power or ground symbol into a parts file for part create.
- When a net that should be power or ground reads back without its name, check the type of the symbol that should name it.
- The stock `pwr_bar` symbol's `NETNAME` cannot be changed by adding an attribute: its attribute list is empty.
