# Schematic design description for `schematic draw`

`xpedition-cli schematic draw --design FILE` plans a readable schematic from a
compact JSON description and draws it through Xpedition Designer. The planner
is pure Python (`xpedition_cli.schematic_layout`), so `--dry-run` shows the
plan, the netlist it will produce and any convention issues before anything
touches the product. See `examples/demo-sensor-board.json` for a complete design.

Contents

1. Top level
2. Symbols
3. Blocks: IC and connector
4. Blocks: ladder and chain
5. Blocks: text and box
6. Nodes
7. What the planner checks
8. Geometry the planner uses

## 1. Top level

```json
{
  "title": "XPEDITION-CLI DEMO BOARD",
  "revision": "R1",
  "date": "2026-09-17",
  "status": "EXAMPLE DESIGN - NOT A PRODUCT",
  "sheet_size": "A4",
  "boxed_labels": true,
  "symbols": {"...": "see §2"},
  "sheets": [{"number": 1, "title": "01 ...", "description": "...", "blocks": [], "notes": []}]
}
```

- `sheet_size`: `A`, `B`, `C`, `D`, `A4` or `A3`. Every sheet gets that border
  and page size; `A4` suits a review draft.
- `status`, `title`, `revision`, `date` form the footer of every sheet.
- Titles, descriptions, block titles and notes may be Chinese: Designer shows
  them correctly on screen. Net names, reference designators and values stay
  ASCII. A PDF made with `schematic export` shows Chinese as mojibake (GBK
  bytes drawn as Latin-1); extract it by re-encoding Latin-1 to GBK, or review
  on screen.
- Each sheet: `number` (1-based, the sheet Designer will show), `title` (the
  strip across the top, `02 POWER`), `description`
  (one line under it), `blocks`, `notes` (`Note 2-1: …`, lower left).

## 2. Symbols

Two-terminal kinds are built in and need no definition: `RES`, `CAP`, `CAPP`
(polarised), `IND`, `DIODE`, `LED`, `SW`, `BAT`, `NTC` (thermistor). Pin `1`
is the left pin, `2` the right; diodes have `2` = anode on the left, `1` =
cathode on the right.

Three-terminal kinds `NMOS` and `PMOS` are built in too. They go in `ic`
blocks (§3) with a treatment per pin: `1` gate on the left, `2` source, `3`
drain (SOT-23 order). An N-channel part has its drain on top, a P-channel part
its source on top, so a high-side switch reads
`"pins": {"1": "label:GATE", "2": "power:VBAT", "3": "label:OUT"}`.

Marks are `ic` blocks as well: `TP` is a test point with one pin (`1`) pointing
down, `{"kind": "ic", "refdes": "TP401", "symbol": "TP", "value": "TP",
"x": 120, "y": 360, "pins": {"1": "label:+3V3"}}`; `HOLE` is a mounting hole
with no pins, so its block carries no `pins` at all. Give both a `value`, or
the review reports them as parts without a part number.

Boxes for ICs and connectors are defined once per design:

```json
"MCU": {
  "kind": "box",
  "top":    [["1", "VDD"]],
  "left":   [["3", "LID"], ["4", "RST"]],
  "right":  [["5", "LED1"], ["6", "BOOST_EN"], ["", ""], ["7", "SDA"], ["8", "SCL"]],
  "bottom": [["2", "GND"]],
  "pintypes": {"1": "POWER", "2": "GROUND"}
}
```

- Pins are `[number, name]` pairs in top-to-bottom / left-to-right order;
  `["", ""]` leaves a gap row between groups.
- Put power pins on `top`, ground on `bottom`, inputs `left`, outputs and
  bidirectional `right`, as the conventions ask.
- `pintypes` values: `IN`, `OUT`, `BI`, `TRI`, `OCL`, `OEM`, `ANALOG`,
  `POWER`, `GROUND`; default `BI`. Type supply pins `POWER` / `GROUND`, inputs
  `IN`, open-drain outputs `OCL`: Designer's verification warns for every `BI`
  pin on a net that also has a `POWER` or `GROUND` pin (resistors and
  capacitors excepted). The built-in passives, MOSFET drain and source and test
  points are `ANALOG` for that reason, and a correct design verifies clean.

Symbol files are generated from these definitions and named by content, so a
changed definition is always a new symbol to Designer. They are written into
the project's central library, which must live on an ASCII path: Designer does
not find new symbol files under a path with other characters.

## 3. Blocks: IC and connector

```json
{"kind": "ic", "refdes": "U101", "symbol": "LDO", "value": "LDO-3V3-SOT23-5", "x": 600, "y": 560,
 "pins": {"1": "power:+5V", "2": "gnd", "3": "label:LDO_EN", "4": "nc",
          "5": "power:+3V3"}}
```

- `x`, `y` is the symbol origin (centre of the body) in sheet units; `kind`
  `connector` is the same block with a different word.
- `pins` must name every pin of the symbol (DS-06); each gets a treatment from
  §6. Several `gnd` pins on the bottom edge share one bar that runs one stub
  past the last pin, with the single ground symbol on its free end: a pin on
  the corner where a stub and the bar meet would not connect in Designer.
- `value` is shown as the part's Part Number text until a central library
  assigns real part numbers.

## 4. Blocks: ladder and chain

```json
{"kind": "ladder", "x": 660, "y": 640,
 "path": ["power:+5V", {"refdes": "R301", "symbol": "RES", "value": "100k 1%"},
          "label:FB", {"refdes": "R302", "symbol": "RES", "value": "18k 1%"}, "gnd"]}
```

- A ladder is vertical: `x`, `y` is its top node; nodes follow every 60 units
  downwards, parts sit between them with pin `1` up. A `chain` is the same
  thing horizontally, `x`, `y` its left node, parts with pin `1` on the left.
- `path` alternates node, part, node, … and ends on a node. `"flip": true` on a
  part turns it round (cathode up, for instance).
- **A `path` holds as many parts as the run has.** Draw a series run as one
  ladder, not as several one-part ladders joined by matching labels: things that
  are connected should look connected, and a sheet where nothing but the labels
  connects is a netlist rather than a schematic.

  ```json
  {"kind": "ladder", "x": 160, "y": 530,
   "path": ["power:VBAT",
            {"refdes": "R206", "symbol": "RES", "value": "20mR 1%"},
            "label:VSNS_B",
            {"refdes": "R207", "symbol": "RES", "value": "5.1R 0402"},
            "label:SNS2B"]}
  ```

  The intermediate node carries the label, so a third part joining the run there
  connects to a named net rather than to a coincidence of two labels.
- `gnd` may only end a ladder; `power:` normally starts one.
- Two-terminal parts only; ICs go in `ic` blocks and connect by labels.

## 5. Blocks: text and box

```json
{"kind": "text", "text": "5 V INPUT", "x": 90, "y": 690, "size": 9}
{"kind": "box", "x1": 60, "y1": 500, "x2": 560, "y2": 610}
```

Block titles, overview boxes and free notes. Sizes are sheet units: 14 for a
sheet title, 9 for block titles, 8 for body text.

## 6. Nodes

| Node | Meaning |
|---|---|
| `power:+3V3` | a power symbol carrying that net |
| `gnd` | a ground symbol (`Globals:gnd`) |
| `label:NAME` | a boxed net label; identical labels are one net across all sheets |
| `none` | a bare junction between two parts, left unnamed |
| `nc` | (IC pins only) a no-connect mark |

Net names: ASCII `UPPER_SNAKE_CASE`; rails by voltage (`+5V`, `+3V3`) or role
(`VBUS`, `VBAT`); active-low `_N`.

## 7. What the planner checks

- DS-06: every IC pin has a treatment.
- DS-07: every part and symbol lies inside the border margin.
- DS-08: parts that are not wired to each other inside one ladder keep at
  least 30 units apart.
- Grid: every position and wire point is a multiple of 10.

Issues are reported in `summary.issues`; the draw still runs, so read them.
After drawing, the adapter reopens the project, reads every net back and
compares it with the plan (`netlist.matches`, `differences`, `links_broken`).

## 8. Geometry the planner uses

Sheet units, 10 per grid step. Passives are 40 units pin to pin; IC boxes grow
with their longest pin names; stubs are 20 units; labels sit at the stub end
with a 6-unit-per-character box. A4 is 1169 × 827 units with the title strip at
the top left and the notes and footer at the bottom left; keep parts inside
x 60–1110 and y 150–700 and clear of the title block in the lower right.
