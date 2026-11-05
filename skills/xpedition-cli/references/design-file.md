# The design file of `schematic create` and `placement run`

`schematic create --design <design.json>` draws the whole schematic from a design file and replaces every sheet it names. `placement run --design <design.json>` uses the same file to group the board's parts by sheet. Every part the design places must already be in the central library (`part create`); the design names it by part number.

## Shape

```json
{
  "title": "DEMO INDICATOR BOARD",
  "revision": "R1",
  "sheet_size": "A4",
  "partition": "PartQuest",
  "symbols": {
    "J_DEMO": {"part": "DEMO-CONN-2"},
    "R_DEMO": {"part": "DEMO-RES-1K-0603"},
    "D_DEMO": {"part": "DEMO-LED-0603"}
  },
  "sheets": [
    {
      "number": 1,
      "title": "DEMO",
      "zone": "DEMO",
      "blocks": [
        {"kind": "connector", "refdes": "J1", "symbol": "J_DEMO", "x": 140, "y": 480,
         "pins": {"1": "power:5V", "2": "gnd"}},
        {"kind": "ladder", "x": 740, "y": 560,
         "path": ["power:5V", {"refdes": "R1", "symbol": "R_DEMO"}, "none", {"refdes": "D1", "symbol": "D_DEMO"}, "gnd"]},
        {"kind": "text", "text": "INDICATOR", "x": 700, "y": 620, "size": 9}
      ]
    }
  ]
}
```

## Fields

- Positions are sheet units of 10 mil, on a grid of 10; the origin is the sheet's lower left corner.
- `title`, `revision`, `date`, `status`: the title block. `sheet_size`: A, B, C, D, E, A4, A3, A2, A1 or A0 (landscape).
- `partition`: the library partition of the parts and of the power symbols the drawing generates; the project file must already list it (`part create` lists it).
- `symbols`: names used by the blocks, each `{"part": "<part number>"}` for a central-library part.
- `sheets`: each with `number`, `title`, optional `description`, `notes` and `zone` (the group name `placement run` uses), and `blocks`.
- Blocks:
  - `ic` or `connector`: one symbol with `refdes`, `symbol`, optional `value`, `x`, `y`, `orientation`, and `pins`: every pin gets a treatment — `power:<net>`, `gnd`, `label:<net>` or `nc`.
  - `ladder` (vertical) or `chain` (horizontal): two-terminal parts strung along `path`, which alternates nodes and parts; a node is `power:<net>`, `gnd`, `label:<net>` or `none` (a plain link between two parts); optional `pitch`.
  - `text`: `text`, `x`, `y`, `size`. `box`: `x1`, `y1`, `x2`, `y2`.
- The preview lists every net with its pins (`REFDES.PIN`) and every component with its part number; a design the planner cannot draw cleanly is blocked, with the reason.

Before drawing, read `xpedition-cli knowledge list --applies-to "schematic create"`.
