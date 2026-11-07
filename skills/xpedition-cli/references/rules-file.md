# The rules file of `constraint get` and `constraint import`

`constraint get --output <rules.json>` writes the board's rules in this shape, and `constraint import --file <rules.json>` creates or changes only what a file names. Read the board's rules first, keep only the classes, pairs and rules to create or change, edit their values, and import the file.

## Shape

```json
{
  "net_classes": [
    {"name": "USB_90", "nets": ["USB_DP", "USB_DN"],
     "layers": {"all": {"width_mm": 0.1, "min_width_mm": 0.08, "differential_spacing_mm": 0.12}}},
    {"name": "POWER", "nets": ["VBUS", "VSYS"], "layers": {"all": {"width_mm": 0.4, "max_width_mm": 1.0}}}
  ],
  "differential_pairs": [{"nets": ["USB_DP", "USB_DN"]}],
  "clearances": [
    {"rule": "FINE", "layers": {"all": {"trace_to_trace_mm": 0.1, "trace_to_pad_mm": 0.1}},
     "classes": [["USB_90", "USB_90"], ["USB_90", "(All)"]]}
  ]
}
```

## Fields

- Lengths are millimetres. A layer is a conductor layer number as a string, `"1"` at the top, or `"all"`; a named layer overrides `all`.
- `net_classes`: `name`, the `nets` that move into the class (its other nets stay in it), and per layer `width_mm` (the width the router draws), `min_width_mm`, `max_width_mm` and `differential_spacing_mm`. A width outside the class's minimum or maximum moves the limit the file leaves out to that width.
- Widths and spacing for impedance lines come from the fabricator's impedance table; read the knowledge entry `layout-impedance-from-fab-table`.
- `differential_pairs`: two nets each. A net already in another pair is refused.
- `clearances`: a clearance `rule` with per-layer values `trace_to_trace_mm`, `trace_to_pad_mm`, `trace_to_via_mm`, `trace_to_plane_mm`, `pad_to_pad_mm`, `pad_to_via_mm`, `via_to_via_mm` and `via_to_plane_mm`, and the class pairs that use it; `(All)` stands for every class.

## Reading the result

The preview lists every change as a target and field with its value before and after. The receipt says `persistence: saved`: Constraint Manager writes the rules into the project at once, so they are not among the board's unsaved changes.
