---
id: layout-placement-drc
title: Placement refused by Layout's online DRC
kind: troubleshooting
applies_to: [placement run, component move, pcb update, pcb get]
tags: [layout, placement, drc]
---

## Symptom

placement run stops part-way with "DRC 违规" (DRC violation, 10203). Some parts are placed and others are not.

component move is refused with `E_CONFLICT`: the spot overlaps another component.

## Cause

Layout's online DRC for parts refuses to place a part:

- on top of another part;
- on its own old position (its old footprint);
- where an identical footprint already sits.

A part placed outside the board outline is accepted. Switching `RespectComponentPlacementDRC` off does not turn this check off. So placement run lifts the parts first and then places them, and it parks the parts it measures in rows below the board.

A board can set this check to shove instead (Editor Control, online DRC for parts): then a part moved onto another pushes that one aside, and nobody asked for that change. component move sets the check to refuse while it moves the part and puts the board's setting back afterwards. So the part lands where it was sent, or the move is refused and nothing moves.

In one live run the board was too small. The plan listed 7 parts as `outside`, and applying it stopped at a placement DRC violation.

## What to do

- Run placement run with `--dry-run` first. If `outside` is not empty, make the board larger with pcb update, then plan again.
- After a stop part-way, read the board with pcb get first. Then run placement run with `--all`, which places the parts already placed again too.
- One confirmed placement once stopped part-way for a reason never found, and completed when repeated. Treat a partial result as possible: read the board, do not assume.
- When component move is refused, read where the other parts stand with component list, then send the part to a free spot. Its result says with `inside_board` whether the part still lies inside the outline.
