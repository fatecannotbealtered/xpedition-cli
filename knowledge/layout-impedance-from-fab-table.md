---
id: layout-impedance-from-fab-table
title: Impedance widths come from the fabricator's table; Layout's own figures are far off
kind: guide
applies_to: [constraint get, constraint import, router run, trace create]
tags: [layout, impedance, stackup, flex, differential-pair]
---

xpedition-cli does not compute impedance. Layout's automation offers the single-ended impedance of a width on a layer (`LayerProperties.Z0ForWidth`), but on both boards measured it gave several hundred ohms for any width. On a flex board with 25 µm dielectric it gave about 560 ohm for a 0.1 mm trace and 260 ohm for a 100 mm one, where the real figure for 0.1 mm is a few tens of ohms. Neither board marked any layer as a plane in its stackup, which Layout may need as the reference, and only Layout's Stackup Editor changes that.

The impedance of a differential pair, such as USB at 90 ohm or MIPI at 100 ohm, depends on the width of its traces, the spacing between them and the stackup. The fabricator's impedance table gives the width and spacing for their stackup.

What to do:

- Before routing impedance lines, ask the person for the fabricator's impedance table and the stackup it assumes. Do not choose widths from Layout's impedance figures.
- Put each layer's width, and for pairs the differential spacing, into the net classes of a rules file, name the pairs under `differential_pairs`, and import it with constraint import. The router then routes each pair coupled at that spacing.
- A layer that needs another width than the rest gets its own entry in the class's `layers` instead of `all`.
