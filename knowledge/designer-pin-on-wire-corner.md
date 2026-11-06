---
id: designer-pin-on-wire-corner
title: A pin on a wire corner does not connect
kind: guide
applies_to: [schematic create, schematic check]
tags: [designer, wires, connectivity]
---

When two wire segments meet end to end, Designer merges them into one polyline. A symbol pin placed where they meet sits on a vertex of that polyline, and it does not connect.

A pin on a free wire end connects. A pin on a T junction connects. Segments that meet at an end point, and a segment that starts in the middle of another, all join one net.

In a live run, two ground pins on the bottom edge of a part shared one bar. The ground symbol sat on the corner where a stub met the bar, and Designer left the symbol unconnected. The fix was in the drawing: the bar ran one stub past the last pin, and the ground symbol went on its free end.

What to do:

- schematic create compares the netlist it reads back with the design file. If it reports a pin, or a power or ground symbol, as unconnected although the design file connects it, this is a likely cause.
- It is a fault of the drawing, not of the design file. Report it to the person with the pin, such as U1.4, and the net.
