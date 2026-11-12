---
id: layout-placement-drc
title: Placement refused by Layout's online DRC
kind: troubleshooting
applies_to: [placement run, component move, pcb update, pcb get]
tags: [layout, placement, drc]
---

## Symptom

placement run stops part-way with "DRC 违规" (DRC violation, 10203). Some parts are placed and others are not.

component move is refused with `E_CONFLICT`, naming what lies under the place the part would take.

## Cause

Layout's online DRC for parts refuses to place a part:

- on top of another part;
- on its own old position (its old footprint);
- where an identical footprint already sits;
- on the other side, when the part is placed: `Place` refuses a placed part that changes side, wherever it is sent, even with nothing in the way. Lifted off the board first, the part takes the same place.

A part placed outside the board outline is accepted. Switching `RespectComponentPlacementDRC` off does not turn this check off. So placement run lifts the parts first and then places them, and it parks the parts it measures in rows below the board.

A board can set this check to shove instead (Editor Control, online DRC for parts): then a part moved onto another pushes that one aside, and nobody asked for that change. component move sets the check to refuse while it moves the part and puts the board's setting back afterwards. So the part lands where it was sent, or the move is refused and nothing moves.

In one live run the board was too small. The plan listed 7 parts as `outside`, and applying it stopped at a placement DRC violation.

## What to do

- Run placement run with `--dry-run` first. If `outside` is not empty, make the board larger with pcb update, then plan again.
- After a stop part-way, read the board with pcb get first. Then run placement run with `--all`, which places the parts already placed again too.
- One confirmed placement once stopped part-way for a reason never found, and completed when repeated. Treat a partial result as possible: read the board, do not assume.
- component move to the other side lifts the part first when Layout refuses it so; when the new place is refused even then, the part goes back where it stood.
- When component move is refused, its message names what lies under the place: another part, vias or traces of nets the part has no pin of on that side, or a placement keepout. Move that, or send the part to a free spot; component list shows where the parts stand. Its result says with `inside_board` whether the part still lies inside the outline.
