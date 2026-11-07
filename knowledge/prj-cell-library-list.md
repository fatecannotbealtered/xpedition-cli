---
id: prj-cell-library-list
title: The project file lists its cell partitions
kind: rule
applies_to: [project get, pcb create, forward-annotation run, part create, part get]
tags: [project, prj, cell, forward-annotation]
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

part create and pcb create register a cell partition for every parts partition the project lists, and pcb create reports the partitions it registered in `cells_registered`.

What to do:

- After pcb create and before forward-annotation run, run project get. Compare `cell_libraries` with the cell partitions that part get reports for the parts.
- Do not edit the `.prj` while Designer has the project open. Designer rewrites the file when it closes the project.
- If a partition is missing, tell the person.
