---
id: layout-router-via-from-net-class
title: The autorouter places the via each net class is assigned in Constraint Manager
kind: rule
applies_to: [router run, via update, pcb get, padstack create]
tags: [layout, routing, via, constraint-manager]
severity: warning
check: pcb get shows each net class's via per via span under net_classes[].vias; after router run, compare it with the via the design asks for
---

When router run changes layers, it places the via padstack that the net's class is assigned for that via span in Constraint Manager. pcb get shows it under `net_classes[].vias`, such as `{"1-4": "026VIA"}`. On the stock template every class is assigned `026VIA`, a 0.48 mm hole with a 0.66 mm pad.

The default via of a via span in Layout's setup does not change this. In a live run on the practice board, the span's default was set to a 0.2 mm via, and the router still placed `026VIA`. Constraint Manager's automation has no via assignment to change, so no xpedition-cli command sets it.

What to do:

- Create the via the design asks for with padstack create, if the central library lacks it; padstack list --type via shows the library's vias.
- Route with router run, then change the vias of the routed nets with via update, such as `--net-classes USB_SS --padstack VIA_D0.2_P0.4`. In the live run all 8 vias of the class changed and read back.
- A larger via than the router placed can break a clearance: run pcb check after via update.
- If every via of a class must be that size from the start, tell the person: they can assign it to the class in Constraint Manager.
