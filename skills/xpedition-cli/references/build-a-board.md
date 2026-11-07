# Build a board

From an empty project to the fabrication package. Before `part create`, read [parts-file.md](parts-file.md) for the parts file; before `schematic create` or `placement run`, read [design-file.md](design-file.md) for the design file. Report each step's receipt; stop at the first failure and say which step failed.

1. `session open --kind project` starts Designer with no project. Then `doc create --session <id> --kind project --path <new.prj>` makes the project from Xpedition's stock setup template, with its own copy of the central library and a board design, and leaves it open. When the user gives a template project instead, `doc create --path <new.prj> --template <template.prj>` copies it in file mode.
2. `doc close`, then `part create --doc <new.prj> --file <parts.json>` for every part the design uses; file-mode writes are refused while Designer has the project open.
3. `doc open --doc <new.prj>`, `schematic create --design <design.json>`, then `doc save`, which also opens the project again, so `schematic check` and the reads see the new drawing.
4. `doc close` in Designer, then `packager run` and `pcb create --doc <new.prj>`.
5. `session open --doc <PCB/name.pcb>`. When it ends with the session `starting`, run `session get --session <id>` until it is `ready`, then `doc open --doc <file>` if the file is not open yet.
6. `forward-annotation run`, `pcb update --width --height --radius`, then `placement run --design <design.json>`.
7. Route the board as [route-a-board.md](route-a-board.md) says.
8. `doc save`, then `pcb export --output <folder>`.
