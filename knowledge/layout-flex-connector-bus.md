---
id: layout-flex-connector-bus
title: Routing a flex board between two board-to-board connectors
kind: guide
applies_to: [trace create, via create, plane create, constraint import, pcb check]
tags: [layout, flex, connector, routing, differential-pair, power]
---

A flex board that joins two boards carries one bus from a two-row board-to-board connector at one end to a matching one at the other. What follows is common practice for such a board; a team's own rules come first.

Connector fan-out:

- The row nearer the board's body can leave on the connector's pad layer straight away. The other row is blocked by it, so each of its pins changes layer through one via beside the pin, inside the stiffener, and changes back the same way at the far end. Traces of one layer keep the order the pins have, so none crosses another.
- Ground pins between signal pins reach the ground plane through a via beside the pin, inside the stiffener; copper poured between two signal lanes is an island unless a via ties it to the plane.

Lanes:

- Run every net as a lane at a fixed distance from one board edge, so the lanes stay parallel and evenly spaced through every turn of the outline. Concentric turns that bend one way and then back give the lanes of a pair equal lengths. Keep the outline's clearance to the outermost lanes.
- Round the corners with arcs or several obtuse bends, never acute or right angles.
- Keep a differential pair's spacing constant over its whole length, and keep the pairs apart from each other and from other nets as the interface asks; a ground lane or poured ground between pairs shields them.

Power:

- Size a power conductor for its current with the copper's thickness, not one width for every net. When one layer has no room, carry the current on two layers in parallel and join them with copper areas and vias at both ends; count the vias from the current each via may carry. `plane create --polygon` pours such an area, and `via create` places its vias inside it.

What to do:

- Read the board with `pcb get` and the pins with `component get` first, then write the routing plan for `trace create`, one entry of `lanes` per net with its distance from the edge it follows, and the layer changes at the connectors only.
- Pour the planes after the traces, then stitch ground with `via create` outside the bend zones (knowledge `layout-flex-bend-and-stiffener`).
- Check with `pcb check`, and compare each pair's lengths in the plan.
