---
id: cell-pin-count-must-match
title: A cell has exactly the pin numbers of its part
kind: rule
applies_to: [part create, part get, forward-annotation run, packager run]
tags: [library, cell, pins, forward-annotation]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/COMPATIBILITY.md (Creating a board and forward-annotating it; The library converters: HKP out and in) and docs/E2E.md (Recorded run, checked, 2026-09-14; run of 2026-09-29 and 30) of npm @fateforge/xpedition-cli@1.0.3
updated_on: 2026-11-05
severity: error
check: run part get for each part; the pin numbers of its pin map equal those of its cell, and findings is empty
---

The footprint cell of a part must have exactly as many pin numbers as the part. Otherwise Layout's Database Load stops during forward annotation, with a message such as:

```
Cell CLI_SOIC4 has 4 unique Alphanumeric Pin Numbers while Part Number <part> has 3
```

In the live run a 4-pin SOIC placeholder on a 3-pin regulator was refused, and forward annotation ended. Afterwards 3-pin parts got a SOT-23 cell, and odd pin counts were no longer padded.

What counts is the set of distinct pin numbers:

- Several lands may carry one pin number, such as the drain leads and the paddle of a MOSFET, or the shield legs of a connector. They are all pads of that pin, and forward annotation puts them all on its net.
- A mounting hole inside a cell (a padstack with a hole and no pad), such as a connector's locating peg, is not a pin. A part with such pegs annotated without trouble.

Check with part get before schematic create. Fixing a cell after forward annotation is costly: see `forward-annotation-keeps-old-cells`.
