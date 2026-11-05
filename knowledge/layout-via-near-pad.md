---
id: layout-via-near-pad
title: Vias keep the clearance from pads, even on their own net
kind: rule
applies_to: [router run, pcb check, part create]
tags: [layout, via, clearance, test-point]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/COMPATIBILITY.md (Hand routing through the automation, verified 2026-09-15) and docs/E2E.md (Recorded run: the board routed by hand, 2026-09-15) of npm @fateforge/xpedition-cli@1.0.3; capability survey docs/APPLICATION.md (CAP-074, CAP-075)
updated_on: 2026-11-05
severity: error
check: run pcb check and look for clearance hazards at vias and Hangers hazards at test points
---

A via must keep the clearance (0.254 mm on the stock template) from every pad it does not sit inside. Layout refuses a via closer than that to a surface-mount pad, even when the via and the pad are on the same net. In the live run, vias 0.07 mm and 0.1 mm from a pad of their own net were refused. Layout's online DRC judges every trace and via as it is added.

Test points: the test-point cells of the live run (`TestPoint_Pad_D1.0mm`) were surface-mount pads. An inner-layer trace that ends under one connects nothing and is a `Hangers` hazard. The connection that worked was a via beside the pad, with a short trace on the top layer.

What to do:

- When pcb check reports `Hangers` at a test point, the test point is not connected. Tell the person.
- When you create a test-point part with part create, remember that a surface-mount pad is reached only on its own layer.
- This release has no command that adds single vias or traces (CAP-074 and CAP-075 are deferred). router run places them.
