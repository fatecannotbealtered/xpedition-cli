# The routing plan of `trace create`

`trace create --file <plan.json>` draws traces and places vias that an Agent plans itself, such as a differential pair between two connectors or a route the autorouter will not take. The plan is checked against the board before anything is drawn.

## Shape

```json
{
  "traces": [
    {"net": "USB_DP", "layer": 1, "width_mm": 0.1, "points_mm": [[6.1, 12.25], [11.0, 12.25], [13.5, 9.75], [83.9, 9.75]]},
    {"net": "USB_DN", "layer": 1, "width_mm": 0.1, "points_mm": [[6.1, 11.75], [7.5, 11.75], [8.0, 11.25]]},
    {"net": "USB_DN", "layer": 4, "width_mm": 0.1, "points_mm": [[8.0, 11.25], [9.0, 10.25], [80.0, 10.25]]}
  ],
  "vias": [{"net": "USB_DN", "at_mm": [8.0, 11.25], "padstack": "VIA_D0.2_P0.4"}]
}
```

## Fields

- Coordinates are the board's, in mm: `component get --refdes <part>` on the board gives the position of each pin to join, and `pcb get --output` writes every pad and trace, the keepouts (a placement keepout with a height marks a stiffener), the cutouts and the plane shapes with their fill.
- `traces`: the `net`, the conductor `layer` (1 at the top), `width_mm`, and at least two `points_mm`. A point between the ends may carry a third value, the radius of an arc that rounds the corner there, tangent to both segments: `[45.0, 9.0, 2.0]`. A lane that follows an arc of the outline turns at the point where its two straight runs meet, with the outline arc's radius plus or minus the lane's distance from that edge. Each end must lie on copper of its own net on its layer: a pad, a trace, a via, or a trace or via of the same plan. Layout connects a trace to a pin only at the pin's origin and to a via only at its centre, so an end on a pad is joined to the pin's origin and an end on a via to its centre; an end that meets another trace connects where it lies on that trace's centre line.
- `vias`: the `net`, the centre `at_mm` and a via `padstack` of the central library (`padstack list --type via`). A via joins every layer, so a trace that changes layers ends on one.
- `lanes`: traces that follow an edge of the board outline at a distance, worked out by the tool. Each has the `net`, `layer` and `width_mm` of a trace; `from_mm`, the points from its first end to the point where it leaves for the lane, such as a pin or a via and a point beside it; `to_mm`, the points from where it leaves the lane to its last end; `edge_mm`, two points near the outline, at or beyond where the lane joins and leaves it, that name the edge it follows between them, the shorter way round; and `offset_mm`, the distance of its centre line from that edge. A straight edge gives a parallel run and an arc of the outline an arc about the same centre, so lanes at even offsets stay evenly spaced through every turn; it runs straight past a part of the outline that sticks out over a base of 5 mm or less, such as a tab, and around a notch. The turns onto and off the lane are rounded with `radius_mm`, 0.5 mm unless given, less where the segments beside them are short. A point of `from_mm` or `to_mm` between the ends may carry a corner radius like a trace's.

```json
{"lanes": [{"net": "USB_DP", "layer": 1, "width_mm": 0.1, "from_mm": [[6.1, 12.25], [7.0, 12.25, 0.3]],
            "to_mm": [[83.0, 12.25, 0.3], [83.9, 12.25]], "edge_mm": [[2.0, 18.0], [88.0, 18.0]], "offset_mm": 2.5}]}
```

## The check

The plan is refused, with every reason, when it names a net or layer the board lacks, a width outside the limits of its net's class on that layer, an end that touches no copper of its net, or a trace or via closer to another net's copper than the clearance rule for the two nets' classes. A via also keeps its clearance from pads of its own net. Set the rules first (`constraint import`), so the check uses the widths and clearances the design asks for.

The preview, `trace create --dry-run`, also names what the plan's own copper shows before it is drawn: each pair's two lengths, the pairs recognised by name such as `USB_DP` and `USB_DN`, and what the board's bend zones forbid of it, such as a turn or a width change inside one.

## Reading the result

`result.nets` says for each net of the plan whether it is routed now and how many connections stay open. Run `pcb check` after: the plan checks clearances on its own copper only, while DRC checks the whole board.
