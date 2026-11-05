---
id: layout-fine-pitch-clearance
title: Pad gaps must be at least the clearance, or the router leaves nets open
kind: rule
applies_to: [part create, part get, router run, pcb check]
tags: [layout, routing, clearance, footprint]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/E2E.md (Recorded run, routed, and Recorded run, checked, 2026-09-14) and docs/COMPATIBILITY.md (Creating a board and forward-annotating it; Autorouting; Batch DRC and hazards) of npm @fateforge/xpedition-cli@1.0.3
updated_on: 2026-11-05
severity: error
check: run part get and compare the gap between neighbouring pads with the clearance (0.254 mm on the stock template); after router run, look for open nets gathered on one part
---

With the stock rules (0.254 mm clearance), the pads of a cell must be at least 0.254 mm apart.

A 0.65 mm pitch with 0.4 mm pads leaves a 0.25 mm gap. In the live run the router left 10 nets open around such an MCU. Route passes leave fine-pitch pads alone and never say why; the clearance rule was the reason. Layout's own automation cannot change that rule: see `layout-trace-widths-through-cm`.

Pads closer than the clearance also show up in pcb check. A first check found 18 pad-to-pad Proximity hazards (required 0.254 mm, actual 0) on dual-row placeholder cells, whose pads had been laid with their long side along the pitch. Placeholder footprints need a DRC as much as real ones.

What to do:

- Before forward-annotation run, check the pad gaps of every fine-pitch part with part get.
- Choose a package that fits the rule. The live run gave the MCU an SOIC cell instead, and the router then routed all 18 nets.
- Changing a cell after forward annotation means rebuilding the board: see `forward-annotation-keeps-old-cells`.
- If the package cannot change, tell the person that the clearance rule must change.
