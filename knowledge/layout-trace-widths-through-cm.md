---
id: layout-trace-widths-through-cm
title: Trace widths and clearances change only through Constraint Manager
kind: guide
applies_to: [constraint get, constraint import, pcb get, router run, trace create, pcb check]
tags: [layout, rules, trace-width, clearance, constraint-manager]
---

Layout's automation reads routing rules but cannot set them: net class widths (minimum, typical and expansion), differential spacing and clearance rules are read-only there. pcb get reads the net classes and their widths. constraint get and constraint import read and change them through Constraint Manager's automation instead.

On the stock 4-layer template these rules read 0.254 mm everywhere, and its via padstack is `026VIA`. Its `(Default)` net class allows exactly one width: minimum, typical and expansion are all 10 th (0.254 mm). A wider trace in that class is a `TraceWidths` hazard in pcb check.

The project keeps two sides of its constraints: the schematic's and the board's. constraint import changes the board's side, the one Layout routes with, and loads it into Layout at once. It does not touch the schematic's side. Live runs on the practice board showed:

- The rules are written into the project database the moment the import ends, before any doc save, so the receipt says persistence saved.
- After the import, the router routed a class at its new width, and a differential pair as a coupled pair at the class's differential spacing.
- A later sync of the schematic's side kept the board's own values and classes; it carried over only what had changed on the schematic's side.
- Changing the rules and loading them took about a second. While Layout is still busy with earlier work, such as a constraint sync that was cut off, every call waits, for minutes.

What to do:

- Read the rules with constraint get --output, change the values you need in that file, and import it with constraint import. A rules file names only what it changes.
- After an import, route with router run, or draw planned routes with trace create: both use the new widths, spacing and clearances.
- If constraint import reports that the board's side holds changes Layout has not loaded, ask the person to load them in Layout (Setup > Project Integration) first.
- If calls start to wait for minutes, run session get and wait until the session is ready before anything else.
