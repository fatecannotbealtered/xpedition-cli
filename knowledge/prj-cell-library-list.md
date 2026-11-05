---
id: prj-cell-library-list
title: The project file lists its cell partitions
kind: rule
applies_to: [project get, pcb create, forward-annotation run, part create, part get]
tags: [project, prj, cell, forward-annotation]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/COMPATIBILITY.md (Creating a board and forward-annotating it) and docs/E2E.md (Recorded run, checked, 2026-09-14) of npm @fateforge/xpedition-cli@1.0.3; old implementation (commit 4a054a3), project_file.py; this release's command registry (project get, pcb create)
updated_on: 2026-11-05
severity: error
check: run project get; cell_libraries names the cell partition of every part the design uses
---

The design section of the `.prj` must list the cell partitions in `LIST 2dCellLibraries`, next to `LIST PDBs` and `LIST Symbols`. Each entry is a `VALUE` line:

```
LIST 2dCellLibraries
VALUE "CellDBLibs\<partition>.cel"
ENDLIST
```

Without it, Database Load finds no cell during forward annotation and stops:

```
No cell library search paths found from <project>.prj — Unable to initialize CellDBUpdate
```

xpedition-cli 1.0.3 registered a cell partition for every parts partition the project listed, both when it built the library and when it created the board. In this release, pcb create reports the partitions it registered in `cells_registered`.

What to do:

- After pcb create and before forward-annotation run, run project get. Compare `cell_libraries` with the cell partitions that part get reports for the parts.
- Do not edit the `.prj` while Designer has the project open. Designer rewrites the file when it closes the project.
- If a partition is missing, tell the person.
