---
id: layout-solder-mask-openings
title: Soldermask and coverlay openings
kind: guide
applies_to: [solder-mask create, solder-mask delete, pcb get]
tags: [layout, soldermask, coverlay, flex]
---

solder-mask create draws a filled rectangle on the soldermask of one side, which leaves the copper under it bare. In a live run on the practice board an opening over a top GND plane was drawn and read back, and solder-mask delete removed it.

The opening bares only copper that is there: the preview names the nets whose pads, traces or planes lie inside the rectangle on that side's outer layer, and refuses an opening over no copper. Pour or route the copper first.

A flex board's outer layers are covered by coverlay instead of soldermask. With --cover-layer the opening goes into the coverlay, which needs a cover layer in the stackup: pcb get shows each insulation layer's kind under `stackup[].insulation`, such as `cover_layer`. On a board without one, such as the stock rigid 4-layer template, whose outer insulation layers read `dielectric`, the preview refuses a coverlay opening.

What to do:

- Check the stackup with pcb get before a coverlay opening.
- Give the opening's two opposite corners in board coordinates; solder-mask delete takes any point inside it.
