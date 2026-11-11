---
id: layout-trace-end-on-a-via
title: A trace end on a via or a pad is drawn on to its centre, at the trace's width
kind: rule
applies_to: [trace create, trace check, pcb check]
tags: [layout, via, trace, clearance, routing-plan]
severity: error
check: run trace check before trace create; a reason that says an end runs on to the centre of a pin or via names the place
---

Layout connects a trace to a via only at the via's centre, and to a pin only at the pin's origin; a trace that merely ends somewhere on the via's pad is not connected. trace create therefore draws an end that lies on a via or a pad (within its extents) on to the via's centre or the pin's origin, with a piece of the trace's own width. When the trace is wider than the pad, or the via stands off the trace's line, that piece reaches past the pad. In a live run, a ground guard 0.53 mm wide ended 0.15 mm from a ground via set beside it; the piece drawn on to the via's centre came 0.06 mm from a signal and Batch DRC reported it.

trace create and trace check check every trace as it will be drawn, with these pieces; a reason that comes from such a piece says where the end runs on to. The vias that trace create places along guards and rings keep their pads off the ends of the plan's traces, and a guard or ring end that falls on a via is drawn on to it only when the piece clears; otherwise the end is moved off the via.

What to do:

- End a trace of the plan at the via's centre or the pin's origin, or keep it clear of the pad.
- Place a via of the plan, such as one a pcb check result suggests (`via_fits_at_mm`), so that its pad does not cover the end of a trace of the plan; trace check names any that does.
