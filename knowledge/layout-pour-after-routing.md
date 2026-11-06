---
id: layout-pour-after-routing
title: Pour outer-layer planes after routing
kind: guide
applies_to: [plane create, router run, pcb check, placement run]
tags: [layout, plane, routing, order]
---

The order that worked:

1. plane create on an inner layer, such as GND on layer 2.
2. router run.
3. plane create on the outer layers (1 and 4 on a 4-layer board), over the routed traces.
4. pcb check.

Why the outer layers come last:

- A plane shape that obstructs routing blocks the router on its layer. A board with pours on its outer layers routed nothing.
- With the obstruction cleared and the pours in place, the router counted the plane net as routed. Then the regenerated copper left 5 GND pins cut off, and a Fanout pass added no vias for them.
- In another run, parts moved on a poured board and routed again left GND open (5 PartialNets). Removing the outer pours, routing, and pouring again fixed it.

A plane shape laid over traces with routing obstruction on failed with "DRC 违反". Laid with it off, the plane copper flowed around the traces.

This release has no command that deletes a plane shape (CAP-088 is deferred). If outer pours were made before routing, tell the person: they must be removed in Layout before router run.

See also `layout-plane-draft-state`.
