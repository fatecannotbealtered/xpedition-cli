---
id: layout-flex-bend-and-stiffener
title: Where a flex board bends and where it is stiffened, and what each place allows
kind: guide
applies_to: [pcb get, trace create, via create, plane create, router run, pcb check, keepout create, pcb update]
tags: [layout, flex, bend, stiffener]
---

A flex board folds in its bend zones and stays rigid where a stiffener is laminated behind it, usually under each connector. The design rarely names them in words; read them from what the board holds. `pcb get` reports both: the stiffeners under `keepout_kinds.height_limits`, and the bend zones under `bend_zones`, each widened to the board's edges along its length, with the axis it runs along (`along`; traces cross it along the other) and what marks it. On a board without stiffeners, a placement keepout that runs from edge to edge across a neck of the board marks a bend zone too; such a zone, which may lie at a slant, also carries its own shape in `polygon_mm`, and the checks judge traces against that shape and the edge they cross. A hatched patch that does not span the board, such as one under an antenna, is no bend zone.

- Stiffener: a placement keepout with a height, the stiffener's thickness, such as 0.15 mm, on the side the stiffener is laminated on, usually the side opposite the parts. `pcb get` lists these under `keepout_kinds.height_limits`, each with that side.
- Bend zone: a band across the board where the reference plane is hatched instead of solid, often beside each stiffener; `pcb get` lists each plane shape group with its fill under `planes`. Placement keepouts of no height over the same band often mark it too.

What each place allows:

- In a stiffener area: vias, connector fan-out, layer changes and copper areas for power.
- In a bend zone: no vias; conductors cross the bend line at a right angle, without turns or width changes, evenly spaced; on two-sided flex the conductors of the two layers do not lie straight above each other; the reference plane is hatched. Fan-out, width changes and turns go before or after the zone.
- Between the zones: vias are allowed, such as ground stitching along the board's edges.

What to do:

- Read `pcb get` before routing a flex board and note each stiffener's and bend zone's extents. When neither is marked, ask the person where the board bends before placing any via between the connectors.
- Mark what the person tells you, so that every later command reads it: a stiffener with `keepout create --side <its side> --height <its thickness>` over its area, a bend zone with `keepout create` across the board's width with no height. A flex board's outline from its mechanical drawing, ears included, goes in with `pcb update --outline`, its corners in order, a rounded one as `[x, y, r]`.
- Pour a layer of a flex board with `plane create --bends --hatch diagonal --hatch-width --hatch-distance`: solid between the bend zones, hatched over each, a band that touches nothing of its net tied across with traces of the net so that Layout makes its copper. A bend zone at a slant, one with `polygon_mm`, is hatched by its own shape, reaching past the board's edges, and tied square to the sides it shares with the solid copper, or along the board's edges where nothing fits so. Where those two sides lie at an angle, as over the turn of an arm, each tie leaves one side square to it and turns in one arc of at least 1 mm radius to cross the other square to it. Its piece holds copper only where a tie or other copper of the net reaches it: where the traces fill the zone, it may stay empty, and `pcb check` says so. One band alone takes `--from-y` and `--to-y` (or `--from-x` and `--to-x`). Read `planes` in pcb get afterwards: `without_copper` counts the shapes Layout made no copper for.
- `via create` places no via in a bend zone, a stitching grid's or a single one, and the vias along a routing plan's guards and rings stay out of them too. A stitching grid and the guards' and rings' vias keep 1 mm from a bend zone, and stand under no part either, where Layout's DRC reports a via under a part.
- Read the team's flex rules in team knowledge, which may set the hatch, the clearances and the via limits.
- When `bend_zones` is empty, or a zone it lists is not where the person says the board bends, ask the person before you route across it.
- Check with `pcb check`. On a board with a bend zone it uses the zones of `bend_zones` and lists `bend_via`, `bend_via_near` (a via within 1 mm of a zone, where folding strains the board most), `bend_turn` (a corner, or an arc narrower than 1 mm; a wider arc met along its tangents is a curve), `bend_angle` (more than 5 degrees off square), `bend_width` and `bend_stacked` (traces of two layers above each other) in each zone, and, where the board has stiffeners, `layer_change_outside_stiffener` for a via of a net without plane shapes outside them.
