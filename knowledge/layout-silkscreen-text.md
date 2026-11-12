---
id: layout-silkscreen-text
title: Text on the board's silkscreen
kind: guide
applies_to: [silkscreen-text create, silkscreen-text delete, pcb get, pcb check]
tags: [layout, silkscreen, flex]
---

silkscreen-text create places one line of text on the board's own silkscreen, not a part's, centred on a point. Some teams plot a user draft layer as their silkscreen, such as one named silk: `--user-layer <name>` places the text there, and `--side` then says which side that layer prints on. `pcb get` lists the board's user draft layers under `user_layers`, and its own texts on the silkscreen and on those layers under `silkscreen_texts`, each with the box Layout reports. One place holds the same words once: placing them again at another point is refused until silkscreen-text delete removes them, by their words and place.

The tool works out the text's box from its characters, height and rotation, a little larger than the box Layout reports once it is placed, and refuses a place where the text would not print whole: past the board outline or across a cutout, over a pad, a bare hole or a soldermask or coverlay opening of its side (the board house clips ink off bare copper), on another text, under a part, or in a flex board's bend zone, where ink cracks. A text lower than 0.6 mm or with strokes thinner than 0.1 mm prints hard to read; the preview says so.

What to do:

- Ask, or read in the team's rules, where their silkscreen lies: the board's silkscreen or a user draft layer of `user_layers`.
- A line of n characters 1 mm high is about n mm long and 1.8 mm tall. On a long, narrow board, such as a flex tail, run it along the board: `--rotation 90deg` where the board runs along y (its bend zones then read `along: x`).
- Pick a spot between the bend zones (`pcb get` `bend_zones`), away from the ears, stiffeners and openings, and check it with `--dry-run`. Where it does not fit, lower `--height`, down to 0.6 mm.
- Give the words as they read. On the bottom, Layout mirrors them, as they will read from that side.
