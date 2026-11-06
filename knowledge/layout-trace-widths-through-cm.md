---
id: layout-trace-widths-through-cm
title: Layout's own automation cannot change trace widths or clearances
kind: guide
applies_to: [pcb get, router run, pcb check]
tags: [layout, rules, trace-width, constraint-manager]
---

Layout's automation reads routing rules but cannot set them. The net class widths (minimum, typical and expansion) and the clearance rules are read-only there. pcb get reads the net classes and their widths.

On the stock 4-layer template these rules read 0.254 mm everywhere, and its via padstack is `026VIA`. Its `(Default)` net class allows exactly one width: minimum, typical and expansion are all 10 th (0.254 mm). A wider trace in that class is a `TraceWidths` hazard in pcb check.

Trace widths can be set through Constraint Manager's automation (`ConstraintsAuto`). Its values are in th, thousandths of an inch (0.5 mm = 19.685). Layout used a new width only after `ProjectIntegration.SynchCES`. Loading the constraints again, or reopening the board, did not do it. In a live run the supply traces then came out 0.5 mm wide and the rest 0.254 mm.

This release has no command that sets trace widths or clearances. Net classes and widths are deferred (CAP-067), and clearance rules are marked unknown (CAP-083).

What to do:

- Expect router run to route every net at the template's width: 0.254 mm on the stock template.
- If the design needs wider power traces or other clearances, tell the person. A person can change them in Constraint Manager.
- Choose footprints that the stock clearance can route: see `layout-fine-pitch-clearance`.
