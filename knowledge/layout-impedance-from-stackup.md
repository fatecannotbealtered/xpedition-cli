---
id: layout-impedance-from-stackup
title: Impedance comes from Layout's stackup; widths come from the fabricator's table
kind: guide
applies_to: [constraint get, constraint import]
tags: [layout, impedance, stackup, constraints, flex]
---

Layout computes the single-ended impedance of a trace width on a layer from the board's stackup: each layer's thickness and dielectric constant. constraint get reports it as `z0_ohm` next to each net class's width on each signal layer. A net class in a rules file may carry an impedance target, `"impedance": {"ohm": 50, "tolerance_ohm": 5}`. constraint import then computes the impedance of the class's width on every signal layer, after the import, and refuses the import when a layer misses the target. The target is a check only: Constraint Manager keeps no copy of it, and constraint get writes none into a file.

The figures are only as good as the stackup. A stackup that lacks the dielectric constant of any layer, the conductor layers included, gives figures several times too high. So both commands refuse to compute impedance on such a stackup and name the layers. The stock board's stackup is like this: its conductor layers have no dielectric constant, and its soldermask has a dielectric constant of 1. Only Layout's Stackup Editor sets the stackup; no automation writes it.

Layout computes single-ended impedance only. The differential impedance of a pair, such as USB at 90 ohm or MIPI at 100 ohm, depends on the pair's width and spacing, and the fabricator's impedance table gives both for their stackup.

What to do:

- Before routing impedance lines, ask for the fabricator's impedance table and the stackup it assumes. Put each layer's width, and for pairs the differential spacing, into the net classes of a rules file.
- Give single-ended lines an impedance target in the rules file, so the import checks the widths against Layout's figures.
- If an import or constraint get reports a stackup without dielectric constants, ask the person to complete the stackup in Layout's Stackup Editor (Setup > Stackup Editor) from the fabricator's table.
- A layer that misses its target needs its own width: give that layer a width in the class's `layers` instead of `all`.
