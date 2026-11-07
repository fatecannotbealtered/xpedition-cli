---
id: layout-plane-hatch
title: A plane's hatch comes from its plane class; a shape's own override sets it
kind: guide
applies_to: [plane create]
tags: [layout, plane, hatch, flex]
---

Layout fills a plane shape the way its plane class says. The stock board has one plane class, `(Default)`, which draws solid copper as horizontal hatch lines 6 mil wide and 6 mil apart. The hatch arguments of `PutPlaneShape` are ignored, the `(Default)` class itself cannot be changed through automation, and no other plane class can be created.

What can be changed is a shape's own override of its plane class. plane create --hatch orthogonal or diagonal, with --hatch-width and --hatch-distance, sets that override on the new shape only. In a live run on the practice board a GND plane on layer 3 came out as an orthogonal hatch of 0.15 mm lines 0.5 mm apart, while the solid GND plane on layer 2 kept its solid fill.

What to do:

- Pour hatched planes with plane create --hatch, one shape at a time; every other plane keeps its fill.
- To change a hatch, delete the shape with plane delete and pour it again.
