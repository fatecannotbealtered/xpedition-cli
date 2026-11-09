---
id: layout-flex-bend-and-stiffener
title: Where a flex board bends and where it is stiffened, and what each place allows
kind: guide
applies_to: [pcb get, trace create, via create, plane create, router run, pcb check]
tags: [layout, flex, bend, stiffener]
---

A flex board folds in its bend zones and stays rigid where a stiffener is laminated behind it, usually under each connector. The design rarely names them in words; read them from what the board holds. `pcb get` reports both: the stiffeners under `keepout_kinds.height_limits`, and the bend zones under `bend_zones`, each widened to the board's edges along its length, with what marks it.

- Stiffener: a placement keepout with a height, the stiffener's thickness, such as 0.15 mm, on the side opposite the parts. `pcb get` lists these under `keepout_kinds.height_limits`.
- Bend zone: a band across the board where the reference plane is hatched instead of solid, often beside each stiffener; `pcb get` lists each plane shape group with its fill under `planes`. Placement keepouts of no height over the same band often mark it too. A text such as BENDING, when the design has one, sits on a user or fabrication layer.

What each place allows:

- In a stiffener area: vias, connector fan-out, layer changes and copper areas for power.
- In a bend zone: no vias; conductors cross the bend line at a right angle, without turns or width changes, evenly spaced; on two-sided flex the conductors of the two layers do not lie straight above each other; the reference plane is hatched. Fan-out, width changes and turns go before or after the zone.
- Between the zones: vias are allowed, such as ground stitching along the board's edges.

What to do:

- Read `pcb get` before routing a flex board and note each stiffener's and bend zone's extents. When neither is marked, ask the person where the board bends before placing any via between the connectors.
- Pour a bend zone's hatched band with `plane create --hatch diagonal --from-y --to-y` beside the solid plane of that layer, and the solid parts with the region flags for the rest.
- Read the team's flex rules in team knowledge, which may set the hatch, the clearances and the via limits.
- When `bend_zones` is empty, or a zone it lists is not where the person says the board bends, ask the person before you route across it.
- Check with `pcb check`. On a board with a bend zone it uses the zones of `bend_zones` and lists `bend_via`, `bend_turn`, `bend_angle` (more than 5 degrees off square), `bend_width` and `bend_stacked` (traces of two layers above each other) in each zone, and, where the board has stiffeners, `layer_change_outside_stiffener` for a via of a net without plane shapes outside them.
