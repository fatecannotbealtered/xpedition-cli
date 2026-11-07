---
id: layout-via-near-pad
title: Vias keep the clearance from pads, even on their own net
kind: rule
applies_to: [router run, trace create, via create, pcb check, part create]
tags: [layout, via, clearance, test-point]
severity: error
check: run pcb check and look for clearance hazards at vias and Hangers hazards at test points
---

A via must keep the clearance (0.254 mm on the stock template) from every pad it does not sit inside. Layout refuses a via closer than that to a surface-mount pad, even when the via and the pad are on the same net. In the live run, vias 0.07 mm and 0.1 mm from a pad of their own net were refused. Layout's online DRC judges every trace and via as it is added. trace create and via create check the same clearance before they place anything, then place with online DRC off.

Test points: the test-point cells of the live run (`TestPoint_Pad_D1.0mm`) were surface-mount pads. An inner-layer trace that ends under one connects nothing and is a `Hangers` hazard. The connection that worked was a via beside the pad, with a short trace on the top layer.

What to do:

- When pcb check reports `Hangers` at a test point, the test point is not connected. Tell the person.
- When you create a test-point part with part create, remember that a surface-mount pad is reached only on its own layer.
- To connect a test point, place the via beside its pad with via create --x --y, or plan the via and the short trace in a routing plan for trace create.
