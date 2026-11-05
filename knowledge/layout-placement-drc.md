---
id: layout-placement-drc
title: Placement refused by Layout's online DRC
kind: troubleshooting
applies_to: [placement run, pcb update, pcb get]
tags: [layout, placement, drc]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/COMPATIBILITY.md (Creating a board and forward-annotating it; Hand routing through the automation) and docs/E2E.md (runs of 2026-09-14 and 2026-09-19) of npm @fateforge/xpedition-cli@1.0.3; this release's command registry (placement run)
updated_on: 2026-11-05
---

## Symptom

placement run stops part-way with "DRC 违规" (DRC violation, 10203). Some parts are placed and others are not.

## Cause

Layout's online DRC refuses to place a part:

- on top of another part;
- on its own old position (its old footprint);
- where an identical footprint already sits.

A part placed outside the board outline is accepted. Switching `RespectComponentPlacementDRC` off does not turn this check off. xpedition-cli 1.0.3 therefore lifted the parts first and then placed them, and it parked parts it was measuring in a grid below the board.

In one live run the board was too small. The plan listed 7 parts as `outside`, and applying it stopped at a placement DRC violation.

## What to do

- Run placement run with `--dry-run` first. If `outside` is not empty, make the board larger with pcb update, then plan again.
- After a stop part-way, read the board with pcb get first. Then run placement run with `--all`, which places the parts already placed again too.
- One confirmed placement once stopped part-way for a reason never found, and completed when repeated. Treat a partial result as possible: read the board, do not assume.
