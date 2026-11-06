---
id: forward-annotation-keeps-old-cells
title: Forward annotation never replaces the cell of a part already on the board
kind: guide
applies_to: [forward-annotation run, part create, part get, pcb create]
tags: [forward-annotation, cell, library, rebuild]
---

Forward annotation never changes the cell (footprint) of a component that already exists on the board. This held whether the part was placed or not, after `ResetCell` and `ResetCellEx`, with the `.prj` flag `FwdAnnoRebuildFlag`, and after deleting the component: Layout refused that delete without a word for a schematic part. The central library and the board's local parts cache both held the new cell, but the board kept the old one. `ReplaceCell` takes only cells from the board's local library, which holds only cells a part has used.

The only way that worked was to create the board again. xpedition-cli 1.0.3 did it like this: it zipped the board folder beside the project, closed the board in Layout, deleted the `PCB` folder, and cleared `PCBDesignPath` and `LayoutTemplate` in the `.prj`. Then it created the board with JobWizard and ran forward annotation again. The outline, placement, planes and routing had to be done again too. JobWizard's own `-deletePCB` failed with "failed to get design information" on these projects.

This release has no command that deletes or rebuilds a board: the survey defers it (CAP-061). Refreshing cells through Layout's Library Services is marked unknown (CAP-082).

What to do:

- Get every cell right before the first forward-annotation run. Check each part with part get: its pin numbers (`cell-pin-count-must-match`) and its pad gaps (`layout-fine-pitch-clearance`).
- If a cell must change after forward annotation, tell the person. A rebuild needs the steps above, done by the person, with a backup first. The board folder may be held by Layout: see `layout-drillprefs-held`.
- If the pins of a part change on a board that already has traces, Layout refuses to break the traces back in its preventive DRC mode ("不能在预防模式下打断导线"). 1.0.3 deleted the traces and vias first. This release has no command that deletes traces (CAP-076).
