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

- Coordinates are the board's, in mm: `component get --refdes <part>` on the board gives the position of each pin to join, and `pcb get --output` writes every pad and trace.
- `traces`: the `net`, the conductor `layer` (1 at the top), `width_mm`, and at least two `points_mm`. Each end must lie on copper of its own net on its layer: a pad, a trace, a via, or a trace or via of the same plan.
- `vias`: the `net`, the centre `at_mm` and a via `padstack` of the central library (`padstack list --type via`). A via joins every layer, so a trace that changes layers ends on one.

## The check

The plan is refused, with every reason, when it names a net or layer the board lacks, a width outside the limits of its net's class on that layer, an end that touches no copper of its net, or a trace or via closer to another net's copper than the clearance rule for the two nets' classes. A via also keeps its clearance from pads of its own net. Set the rules first (`constraint import`), so the check uses the widths and clearances the design asks for.

## Reading the result

`result.nets` says for each net of the plan whether it is routed now and how many connections stay open. Run `pcb check` after: the plan checks clearances on its own copper only, while DRC checks the whole board.
