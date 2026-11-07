---
id: xpedition-basics
title: Key operating points of Xpedition
kind: guide
applies_to: [context, session open, session list, session get, doc open, doc save, doc close, job get, part create, schematic create, packager run, pcb create, forward-annotation run, placement run, plane create, router run, pcb check, pcb export]
tags: [basics, sessions, save, board-flow]
---

## Two programs, two kinds of session

Xpedition Designer draws the schematic. Its document is the project file (`.prj`). Xpedition Layout draws the board. Its document is the board file, `PCB/<name>.pcb` in the project folder.

A session is one running program. Session IDs start with `designer-` or `layout-`. `session open --doc D:/work/demo/Demo.prj` starts Designer. `session open --doc D:/work/demo/PCB/Demo.pcb` starts Layout. Name the session with `--session` and the document with `--doc` in every session-mode command.

## Start through the launcher, one instance of each

session open starts the program through the Xpedition launcher. Do not start the program binaries any other way. Layout took about 50 seconds to a minute before automation could reach it. Wait until session get shows the session ready. See `xpedition-start-through-launcher`.

Keep at most one Designer and one Layout running. The tool cannot tell two Designers apart. See `designer-one-instance-only`.

At start-up Designer may stop at the Siemens licence sign-in. Only a person can complete it. See `xpedition-license-sign-in`.

## File mode and session mode

File-mode commands work on the `.prj` without a session: project get, doc create (a copy of a template project), part list, part get, part create, packager run, pcb create and schematic export. Designer commands: schematic get, schematic create and schematic check. Layout commands: forward-annotation run, pcb update, placement run, plane create, router run, pcb get, pcb check and pcb export.

## File-mode writes need the project closed in Designer

While Designer has a project open, its database server locks `database/icdb.dat`. Designer also rewrites the `.prj` when it closes a project, so a change made to the file while the project is open can be lost. Before part create, packager run and pcb create, run doc save, then doc close for the project in Designer. doc create in file mode needs its template project closed. part list, part get and schematic export only read the project; they worked with the project open.

Do not close the project in Designer while Layout is opening the board of that project. Layout's database session broke with "iCDB error getting UID manager". Close it before Layout opens the board, or after the board is open.

## Saving

A session-mode write changes the open document only. It is not on disk until doc save. Commands whose effects include `persist`, such as schematic create and pcb export, save by themselves. `reference --command "<path>"` shows the effects of each command. The doc save preview lists only this tool's changes: see `xpedition-external-edits-not-tracked`.

## The board flow

This order made a board from an empty project in the live runs, written with xpedition-cli's commands:

1. part create: add every part the design uses. The installation ships no parts (`xpedition-template-library-is-empty`). The project is closed in Designer.
2. session open or doc open on the `.prj`, then schematic create. It replaces every sheet the design file names and cannot be undone.
3. doc save, then doc close of the project in Designer.
4. packager run.
5. pcb create: the board from a layout template, 4 Layer Template by default.
6. session open on the `.pcb`, then forward-annotation run. The parts arrive unplaced and are not drawn (`layout-unplaced-parts-invisible`).
7. pcb update: the board outline.
8. placement run. Check `outside` first: a part that does not fit needs a bigger outline (`layout-placement-drc`).
9. constraint import: the widths, differential spacing and clearances the design asks for, before any copper (`layout-trace-widths-through-cm`).
10. plane create on an inner layer, such as GND on layer 2.
11. router run. Pour outer-layer planes only after routing (`layout-pour-after-routing`).
12. doc save, then pcb check.
13. pcb export.

Get every cell right before step 6. Forward annotation never changes the cell of a part already on the board (`forward-annotation-keeps-old-cells`).

## Long jobs

schematic create, forward-annotation run and pcb export run as long jobs. Follow them with `job get --job <id> --wait 60s`. In the live runs a four-sheet drawing took 85 to 183 seconds, forward annotation 30 to 70 seconds, and the fabrication outputs 72 seconds in one run.

## Other limits

- Keep projects and libraries on ASCII paths (`xpedition-ascii-paths-only`).
- The dialogs and errors met most often, and what to do: `xpedition-troubleshooting`.
