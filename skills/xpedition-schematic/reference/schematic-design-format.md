# Schematic design description for `schematic draw`

`xpedition-cli schematic draw --design FILE` plans a readable schematic from a
compact JSON description and draws it through Xpedition Designer. The planner
is pure Python (`xpedition_cli.schematic_layout`), so `--dry-run` shows the
plan, the netlist it will produce and any convention issues before anything
touches the product. In [the repository](https://github.com/fatecannotbealtered/xpedition-cli),
`examples/demo-sensor-board.json` is a complete design and
`examples/demo-sensor-board-kicad.json` the same design with KiCad footprints.

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

- `sheet_size`: `A`, `B`, `C`, `D`, `E`, `A4`, `A3`, `A2`, `A1` or `A0` (default
  `B`), always landscape. Every sheet gets that border and a landscape page of
  the same size; `A4` suits a review draft. Only A4 and A3 have a measured title
  block: on the other sizes `summary.usable_measured` is false and the usable
  area runs into the title block, so keep the lower-right corner clear.
- `partition` (optional, default `PartQuest`, the partition a stock central
  library registers for symbols, cells and parts; one it does not register
  cannot be packaged): the library partition the generated symbols go to. It
  names a folder and a `.prj` entry, so it is a plain identifier: a letter, then
  letters, digits and `_`. Symbol names (§2) are plain names too: letters,
  digits and `_ . + -`.
- `packages` (optional, read by `library build`): the footprint of each part,
  keyed by symbol kind or reference designator (a refdes key wins), e.g.
  `{"RES": "0603", "U302": "TSSOP20", "CMP": "kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"}`.
  Stock keys are `0402`, `0603`, `0805`, `SOT23`, `TP`, `HOLE`, `HDR<n>`,
  `SOIC<n>` and `TSSOP<n>`; a `kicad:Library:Footprint` key takes the cell from a
  KiCad library imported with `library import`. Without an entry a part
  gets a placeholder package for its kind and pin count.
- `kicad_footprints` (optional): the KiCad footprint folder that `kicad:` keys
  are read from to check their pads against the symbol's pins. Without it the
  planner uses `XPEDITION_KICAD_FOOTPRINTS`, `KICAD9_FOOTPRINT_DIR` or
  `KICAD8_FOOTPRINT_DIR`, then a standard KiCad install under Program Files.
- `status`, `title`, `revision`, `date` form the footer of every sheet.
- Titles, descriptions, block titles and notes may be Chinese: Designer shows
  them correctly on screen. Net names, reference designators and values stay
  ASCII. A PDF made with `schematic export` shows Chinese as mojibake (GBK
  bytes drawn as Latin-1); extract it by re-encoding Latin-1 to GBK, or review
  on screen.
- Each sheet: `number` (1-based, the sheet Designer will show), `title` (the
  strip across the top, `02 POWER`), `description`
  (one line under it), `blocks`, `notes` (`Note 2-1: …`, lower left), and an
  optional `zone`: the silkscreen label `pcb arrange` writes over that sheet's
  parts on the board (its sheet number otherwise).

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
  capacitors excepted, so `RES`, `CAP` and `CAPP` keep `BI` pins). The other
  built-in kinds -- `IND`, `DIODE`, `LED`, `SW`, `BAT`, `NTC`, the MOSFET drain
  and source, and `TP` -- are `ANALOG` for that reason (the MOSFET gate is `IN`),
  and a correct design verifies clean.

Symbol files are generated from these definitions and named by content, so a
changed definition is always a new symbol to Designer. They are written into
the project's central library, which must live on an ASCII path: Designer does
not find new symbol files under a path with other characters.

### Parts of the central library

A symbol may name a part the project's central library holds -- one added with
`library add`, with its real footprint -- instead of defining a box:

```json
"LDO": {"part": "TPS7A2033PDBVR"}
```

Blocks place it like any symbol, by its pin numbers; the drawing uses the
library's own symbol and carries the library's part number, whatever the block's
`value` says, and `library build` makes no placeholder for it. `schematic render`,
`schematic draw` and `library build` read those parts from the library, so they
need `--project` (render only then). A part the library lacks stops the plan with
its number: add it first. Two parts with one value are one part number, so they
must share a symbol and a package; the planner refuses a value drawn with two
different symbols, and `library build` one that needs two different cells.

## 3. Blocks: IC and connector

```json
{"kind": "ic", "refdes": "U101", "symbol": "LDO", "value": "LDO-3V3-SOT23-5", "x": 600, "y": 560,
 "pins": {"1": "power:+5V", "2": "gnd", "3": "label:LDO_EN", "4": "nc",
          "5": "power:+3V3"}}
```

- `x`, `y` is the symbol origin (centre of the body) in sheet units; `kind`
  `connector` is the same block with a different word. `orientation` (optional)
  turns the symbol: `0`, `1`, `2`, `3` for 0°, 90°, 180°, 270° counter-clockwise.
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
- DS-15: no label box, power or ground symbol lands on the free end of another
  net's wire (Designer would refuse the draw, or merge the two nets).
- DS-16: pin names on a top or bottom edge fit the pin pitch; wider ones overlap
  into one unreadable row.
- DS-17: no text collides with another text, has a line (a wire, a symbol's
  graphics) drawn through it, or lands on another net label's box; a block's
  frame may surround text but not cut through it. DS-07 also covers net labels
  and part attributes that run past the drawable area.
- Grid: every position and wire point is a multiple of 10.

Every part that is not a two-terminal one (IC and connector boxes, test points,
holes, transistors) gets its refdes and part number placed by the planner,
horizontal whatever the part's orientation, at the first spot beside it where
they collide with nothing (the refdes above or left first, the part number right
or below first). A box with pin names on its top or bottom edge is a row taller,
so those names stay clear of the side pins' names. `schematic render --design
FILE --output preview.png` shows the result, sheet by sheet, before a draw.

Two kinds of result. DS-06, the grid, a wire that is not orthogonal, a reference
designator used twice anywhere in the design and a treatment for a pin the
symbol does not have make the design undrawable: the dry run refuses it with
`E_VALIDATION` and issues no token. DS-07, DS-08, DS-15 and DS-16 are reported in
`summary.issues` and the draw still runs, so read them. Numbering the refdes per
sheet (R1xx on sheet 1, R2xx on sheet 2) keeps them from colliding.
After drawing, the adapter reopens the project, reads every net back and
compares it with the plan: `differences` and `links_broken` for planned nets
that read back wrong, `extra_nets` for nets the plan never named that hold a
planned pin, `unplanned_components` for parts the design does not place, and
`no_connects_joined` for no-connect pins that share a net. `netlist.matches` is
true only when all five are empty.

## 8. Geometry the planner uses

Sheet units, 10 per grid step. Passives are 40 units pin to pin; IC boxes grow
with their longest pin names; stubs are 20 units; labels sit at the stub end
with a 6-unit-per-character box. A4 is 1169 × 827 units with the title strip at
the top left and the notes and footer at the bottom left; keep parts inside
x 60–1110 and y 150–700 and clear of the title block in the lower right.
