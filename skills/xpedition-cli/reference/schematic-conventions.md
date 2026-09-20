# Schematic drawing conventions for Xpedition Designer

How an agent lays out a schematic that a hardware engineer can read and that
Designer, the packager and Layout accept. `reference --compact` stays the
source of truth for command names and parameters; this file covers judgement.

Status of each rule:

- **verified** — measured on Xpedition Standard XPED2604 through the native adapter.
- **default** — industry practice (see Sources); apply unless the company
  specification says otherwise.
- **TBD** — a company decision is pending; the default applies meanwhile.

Contents

1. Units, grid and sheet sizes
2. Symbol construction
3. Placement and signal flow
4. Wiring: stubs, labels, power symbols
5. Naming: reference designators and nets
6. Annotation: values, no-connects, notes
7. Sheets and title block
8. Checklist before reporting done
9. Tool status
10. Sources
11. Observed practice in a company review schematic

## 1. Units, grid and sheet sizes

| Fact | Value | Status |
|---|---|---|
| Sheet coordinate unit | 1 unit = 10 mil = 0.254 mm | verified |
| Grid | 100 mil = 10 units; every pin end, wire end and label anchor sits on a multiple of 10 | unit verified, grid default |
| Axes | x grows to the right, y grows upward (ground symbols hang below their pin, power bars sit above it); the border's lower-left corner is (0, 0) | verified |
| Symbol files with header `V 53` | same 10-mil units as the sheet: a pin at `35` reads back 35 units from the origin | verified |
| Symbol files with header `V 54` | 10 nm per unit: `254000` = 100 mil = 10 sheet units. Every stock Globals, builtin and Borders symbol is V 54 | verified |
| The `Y` record | the symbol type, not a scale: 1 part, 3 annotation, 4 power or ground, 5 border. A power symbol saved as type 1 names no net | verified |

Sheet sizes in sheet units, width × height. The drawable area is inside the
border lines: keep 20 units clear of them and clear of the title block.

| Border symbol | Size | Units |
|---|---|---|
| `asheet` | ANSI A, 11 × 8.5 in | 1100 × 850 |
| `bsheet` | ANSI B, 17 × 11 in | 1700 × 1100 |
| `csheet` | ANSI C, 22 × 17 in | 2200 × 1700 |
| `dsheet` | ANSI D, 34 × 22 in | 3400 × 2200 |
| `a4sheet` | A4, 297 × 210 mm | 1169 × 827 |
| `a3sheet` | A3, 420 × 297 mm | 1654 × 1169 |
| `a2sheet` | A2, 594 × 420 mm | 2339 × 1654 |
| `a1sheet` | A1, 841 × 594 mm | 3311 × 2339 |
| `a0sheet` | A0, 1189 × 841 mm | 4681 × 3311 |

`_p` variants are portrait. A coordinate such as (20000, 20000) is 200 inches
off every sheet: Designer accepts it and the netlist is still right, but a
human opening the sheet sees an empty border and `Fit All` shrinks the drawing
to a dot. Read the border in use before placing; when it is unknown, assume B
and stay inside 1700 × 1100.

## 2. Symbol construction

Geometry, all default unless marked:

| Item | Rule |
|---|---|
| Pin pitch | 100 mil (10 units) between adjacent pins, never less |
| Pin length | 20 units on ICs and connectors, 10 units on two-terminal passives; the connection end is the first point of the `P` record and must be on grid |
| Body | rectangle for ICs and connectors, wide enough for the longest left/right pin-name pair plus 20 units (6 units per character) |
| Text | pin number 5 units, pin name 6 units, REFDES and value 8–10 units, horizontal |
| Attributes | `DEVICE=` (must equal the PDB device name), `PART_NAME=`, `REFDES=` (empty, filled at placement), `PINTYPE=` on every pin; power symbols carry `NETNAME=` and no refdes — verified against the stock files |
| Pin types | `IN`, `OUT`, `BI`, `TRI`, `OCL`, `OEM`, `ANALOG`, `POWER`, `GROUND`: the set used by the stock builtin and Globals symbols; ERC reads them |

Shape by device class, IEC 60617 / GB/T 4728 form (TBD: the company may use
the ANSI forms — zigzag resistor, looped inductor):

| Class | Shape | Pins |
|---|---|---|
| Resistor R | rectangle 20 × 8 units, pins on the long axis, connection ends 40 units apart | 1, 2 |
| Capacitor C | two plates 12 units long, 4 units apart; polarised: one curved plate and a `+` | 1 = +, 2 = − |
| Inductor L | four half-circle humps | 1, 2 |
| Diode D | triangle and bar 12 units long; LED adds two outward arrows; Zener bends the bar | 1 = K, 2 = A is the common library convention (TBD against the central library) |
| Transistor Q | IEC form; pins named E/B/C or S/G/D | per datasheet |
| Switch SW | contact with a gap | 1, 2 |
| Battery BT | long plate for +, short plate for − | 1 = +, 2 = − |
| Crystal Y | rectangle between two plates | 1, 2 |
| Connector J / P | rectangle, one pin per row, numbers outside, names inside; shell pins as `SH`, mounting as `MH` | per datasheet |
| IC U | rectangle; power pins on the top edge pointing up, ground on the bottom edge pointing down, inputs left, outputs and bidirectional right, grouped by function with a blank row between groups, NC pins last | per datasheet |
| Power, ground | stock `Globals` symbols `gnd`, `gnd_analog`, `gnd_digital`, `gnd_earth`, `pwr_bar`, `vcc_bar`, `vcc_circle`, `power_triangle`; the `NETNAME` attribute is the net | 1 |
| No-connect | stock `builtin:No_Connect` on the pin end | — |
| Off-page | stock `builtin:OFFPAGE_INPUT`, `OFFPAGE_OUTPUT`, `OFFPAGE_BIDIR` | — |

Never draw a two-terminal passive, diode, transistor or switch as a bare
rectangle: readers recognise the class from the shape before they read the
value.

A generated symbol is a `V 53` text file at
`SymbolLibs/<partition>/sym/<name>.1`; this resistor loads and places on
XPED2604 (verified). Sizes are in sheet units.

```
V 53
K 1196353901 R.1
F Case
D -30 5 30 -5                        bounding box x1 y1 x2 y2
Y 1
Z 10
i 11
U -20 12 10 0 4 0 DEVICE=R            attribute: x y size … NAME=value
U -20 -16 10 0 5 0 PART_NAME=R
U -30 12 10 0 8 3 REFDES=
l 5 -20 -5 20 -5 20 5 -20 5 -20 -5    polyline: point count, then x y pairs
l 2 -30 0 -20 0                       pin leg
P 10 -30 0 -20 0 0 3 0                pin: id, connection end x y, body end x y
L -34 4 4 0 5 0 0 0 1                 pin number text: x y size …
A 0 1 10 0 6 3 #=1                    pin number attribute
A 0 10 10 0 3 0 PINTYPE=BI
l 2 20 0 30 0
P 11 30 0 20 0 0 3 0
L 31 4 4 0 5 0 0 0 2
A 0 1 10 0 6 3 #=2
A 0 10 10 0 3 0 PINTYPE=BI
E
```

## 3. Placement and signal flow

- Signal flows left to right and top to bottom: input connectors and sources on
  the left, processing in the middle, outputs and output connectors on the
  right.
- Power rails run along the top of a block, ground along the bottom; power
  symbols point up, ground symbols point down.
- One function per area, titled: power input, charger, boost, MCU, interfaces,
  indicators. Designs that fill a sheet get one function per sheet (§7).
- Passives sit next to the pin they serve: decoupling capacitors within 20
  units of the supply pin, pull-ups and series resistors on the signal they
  belong to, feedback dividers next to the regulator.
- Passives stand in vertical ladders, never lie flat with a label on one end
  and a ground on the other: a power symbol on top, the parts in series
  below it, the signal label at the junction, a ground symbol at the bottom.
  Series elements in a signal path (fuse, diode, inductor, coupling
  capacitor) sit on one horizontal drawn wire from left to right. An IC's
  pins end in labels; its passives are drawn as ladders beside it. A sheet
  where nothing but the labels connects is a netlist, not a schematic.
- Spacing comes from real extents: at least 30 units between symbol bounding
  boxes, at least 10 units between a wire and an unrelated symbol, and room for
  the labels a pin will carry (6 units per character). A fixed column and row
  grid that ignores symbol size produces either overlaps or a sparse sheet
  nobody can read.
- Rotate only in 90° steps; mirror only to keep pin sides sensible; text stays
  horizontal and upright.

## 4. Wiring: stubs, labels, power symbols

- Every connection is one of: a short drawn wire, a labelled stub, a power
  symbol, or an off-page connector. Nothing else.
- Drawn wires only inside a function block, only straight or with 90° bends,
  and never crossing anything; a junction dot on every T. Never four wires into
  one point: make two Ts.
- Everything else connects by label: from the pin end draw a 10–20 unit stub
  outward (left pins to the left, right pins to the right, power up, ground
  down) and put the net label at its free end. Identical labels are one net.
  This is what made the example netlist match on the first read-back,
  after drawn wires had shorted three nets by crossing (verified).
- One pin, one net. A pin listed under two nets is a design error, not a tool
  error.
- Power and ground use power symbols, not bare labels and not wires running
  across the sheet: the stock `Globals:gnd` for ground and one generated
  type-4 symbol per power net (§9); the symbol's `NETNAME` is the net. On IC
  pins a label carrying the rail's name is acceptable where a bar would
  collide with neighbouring pins.
- A label that appears once and is not on a power symbol or an off-page
  connector is a dangling net: fix it or mark the pin no-connect.
- Buses as `NAME[7:0]` (TBD: verify Designer's bus syntax through the adapter
  before relying on it).

## 5. Naming

Reference designators — default letters per IEEE 315 / ASME Y14.44, the scheme
most EDA libraries use. GB/T 5094 assigns different letters (`V` for
semiconductors, `X` for connectors); the company has to pick one (TBD).

| Prefix | Class | Prefix | Class |
|---|---|---|---|
| R, RN | resistor, resistor network | J, P | connector receptacle, plug or cable side |
| C | capacitor | SW | switch |
| L | inductor | BT | battery |
| FB | ferrite bead | Y | crystal, oscillator |
| D | diode, LED, Zener, TVS | F | fuse |
| Q | transistor, MOSFET | K | relay |
| U | integrated circuit | T | transformer |
| TP | test point | ANT | antenna |
| M | motor | LS, MK | speaker or buzzer, microphone |
| MH | mounting hole | FID | fiducial |

Number from 1 without leading zeros, in reading order (left to right, top to
bottom) or per function block; a deleted part's number stays unused until the
design is re-annotated.

Nets: ASCII only, `UPPER_SNAKE_CASE`, descriptive (`I2C_SDA`, `LID_DET`,
`CHG_STAT_N`), never `NET1`. Power nets by voltage: `+3V3`, `+5V`, `+1V8`,
`VBAT`, `VBUS`, `VSYS`. One `GND`; `AGND` and `PGND` only with a deliberate
single join. Active-low: suffix `_N`. Differential pairs: `_P` / `_N`; when
that could be read as active-low, use `_DP` / `_DN`. Designer's automatic
`$…` names are acceptable only on local two-pin nets nobody will probe.

## 6. Annotation

- Every placed part carries a REFDES, a value or `PART_NAME`, and a part number
  that exists in the PDB; the packager rejects the design otherwise
  (`no Part Number: R in Parts DataBase`, verified).
- Values: `10k`, `4.7k`, `100nF`, `10uF/10V`, `2.2uH`; tolerance, power and
  voltage rating only when they matter to the circuit.
- REFDES above or left of the body, value below or right; text never crosses a
  wire, a pin or other text.
- Unused pins get `No_Connect`; an open pin without it fails review.
- Sheet titles, descriptions, block titles and notes are written in Chinese
  when the reviewers read Chinese, as the company review drafts are; net
  names, reference designators and values stay ASCII.
- Design intent goes on the sheet as text notes in the form
  `Note <sheet>-<n>: …`; the review pipeline extracts notes in exactly that form
  into rules.

## 7. Sheets and title block

- Sheet 1 holds the block diagram, the sheet index and the revision table; then
  one function per sheet (power, core, interfaces, connectors, indicators).
- Title block filled on every sheet: project, sheet title, sheet N of M,
  revision, date, author (TBD: the company template and its fields).
- Nets crossing sheets use off-page connectors; power symbols are global through
  `NETNAME` and need none.
- Leftovers are defects: no orphan symbols, test placements or unused labels on
  a delivered sheet. Start on a clean sheet: Select All plus `DeleteSelected`
  wipes a sheet (§9); there is no per-object delete.

## 8. Checklist before reporting done

Read the design back with `schematic connectivity` and `project snapshot`,
then check. Classes follow the review rule library: L2 is decidable from the
netlist, L2b from geometry, manual needs an engineer.

Run `library build --package` after a redraw, before reading the design back or
reviewing it. A schematic that has changed since it was last packaged cannot be
read: `review run`, `bom export` and `schematic components` all stop on the same
COM type mismatch until it is re-packaged. Being unpackaged is a normal state to
be in halfway through a design, not a failure — the error now names the cause and
the one command that clears it.

| ID | Check | Class | How |
|---|---|---|---|
| DS-01 | every component has a refdes | L2 | `review run`: components without a refdes are counted in `metadata.unnamed_symbols` |
| DS-02 | every net has a name | L2 | `review run` rule `cli/unnamed-net` (nets of three or more pins) |
| DS-03 | no single-pin net | L2 | `review run` rule `cli/single-pin-net` |
| DS-04 | no pin in two intended nets | L2 | `schematic draw` plan (a pin in two nets is rejected by the planner) |
| DS-05 | read-back netlist equals intent | L2 | `schematic draw` result `netlist.matches` |
| DS-06 | unused pins marked no-connect | L2 | `review run` rule `cli/open-pin` (no-connect marks are recognised) |
| DS-07 | all coordinates inside the border | L2b | `schematic draw --dry-run` issue `DS-07` |
| DS-08 | no overlapping symbol boxes | L2b | `schematic draw --dry-run` issue `DS-08` |
| DS-09 | refdes prefix matches the device class | L2 | `review run` rule `cli/refdes-prefix` |
| DS-10 | every part number exists in the PDB | L2 | `review run` rule `cli/missing-part-number`; `package` verdict |
| DS-11 | signal flow, grouping, power up and ground down | manual | engineer review |
| DS-12 | no leftover objects on the sheet | manual | engineer review |
| DS-13 | Designer's own verification passes | L2 | `review run` on the live design: findings tagged `xpedition/verify:*` and `xpedition/grc:*` |
| DS-14 | every IC supply net has a capacitor to ground; every I2C line a pull-up | L2 | `review run` rules `cli/decoupling`, `cli/i2c-pullup` |

## 9. Tool status

What the native adapter covers and where the conventions are still applied by
hand; `reference --compact` is authoritative for the live command list.

| Need | Today |
|---|---|
| Place a part | `place_component` with an explicit library partition; keep the returned object, because `DesignComponents` lags the placement. `AddPartInstance(partition, part, symbol, x, y)`: the part name is the visible Part Number |
| Wire two pins with a label | `connect` draws one wire between two pins and labels it; the per-pin stub-and-label form runs through the adapter's `AddNet` + `AddLabel` and is not yet a ChangeSet operation |
| Power symbols, ground, no-connects | `AddSymbolInstance(partition, symbol, x, y)` places refdes-less symbols without an orphan: stock `Globals:gnd`, `builtin:No_Connect` (set `Orientation` 0 / 2 / 3 / 1 for left / right / top / bottom pins) and one generated type-4 power symbol per net from `xpedition_cli.symbols.power_symbol`. A stub ending on the symbol origin joins the net |
| Symbol generation | `xpedition_cli.symbols` writes `V 53` files per §2; not yet a CLI command. Designer keeps the definition of a symbol it has placed, so a changed symbol needs a new name |
| Clean sheet | `ExecuteCommandByID(57642)` (Select All) then `Block.DeleteSelected(False)`; keeps the border. There is no per-object delete |
| Titles and notes | `Block.AddText(text, x, y)`, then `.Size` |
| Whole sheets | `schematic draw --design FILE` (format: `reference/schematic-design-format.md`) plans IC blocks, ladders, chains, labels, power and ground symbols, no-connects, titles and notes, draws them, reopens the project and diffs the netlist read back |
| Show the result | `schematic show --sheet N [--output sheet.png]`: activates the sheet, fits it, raises Designer's window and optionally captures it as PNG — the way to look at Chinese text, which a PDF garbles |
| More sheets | the `New Sheet` command (34165) adds one; close and reopen the project before reading the design back afterwards |
| PDF | `schematic export --backend native_xpedition --project X.prj --output X.pdf`; only what is inside the border is printed |
| DS-01 … DS-10 | by hand from snapshot and connectivity output |

## 10. Sources

Versions unverified; cite the standard by name, not by clause, until the
company's copy is checked. The company's review rule library already registers
the GB/T and IPC items.

- IEC 60617 / GB/T 4728 — graphical symbols for diagrams (symbol shapes).
- IEC 61082 / GB/T 6988 — preparation of documents used in electrotechnology
  (sheet layout, flow, title block).
- IEEE 315 / ASME Y14.44 — reference designations; GB/T 5094 (IEC 81346) is the
  alternative letter scheme.
- IPC-2612 — sectional requirements for electronic diagramming symbol generation.
- Company schematic design specification and internal checklist — pending from
  the hardware team; it supersedes every `default` above when it arrives.

## 11. Observed practice in a company review schematic

Read from `DA30_R2_音响原理图.pdf` (a hardware design review draft, KiCad 10,
A4 landscape, 8 sheets, dated 2026-09-10). These are observations, not yet
confirmed company rules; where they differ from the defaults above, follow
them until the company specification arrives.

- Sheet 1 is an overview: product name and design targets, one titled box per
  following sheet with a one-line signal-flow summary, design and verification
  boundaries, component conventions (tolerance, package, dielectric) and the
  vendors used.
- One function per sheet with a numbered title strip across the top
  (`02 直流输入与电源保护`), a one-line description under it, free-text notes
  in the lower left, and the title block in the lower right carrying the
  status (`硬件设计评审稿 | 待 PCB 与样机验证`), the key ratings, sheet path,
  file, title, size, date, revision and `Id n/N`.
- Reference designators are numbered per sheet: `J201`, `U201`, `R205` on
  sheet 2; `U401`, `C404` on sheet 4.
- IC pins end in boxed global labels; every unused pin carries an X; ground
  pins run to one vertical bus that ends in a single ground symbol.
- Passives form vertical ladders between a power symbol and a ground symbol
  (dividers, pull-ups, decoupling); the input chain (jack, fuse, diode) is one
  horizontal drawn wire; decoupling capacitors hang from a short horizontal
  rail wire.
- Values carry the rating that matters: `10k 1%`, `100n/50V`, `4.7uH / 5A+`,
  `4A / 125V`; part numbers appear next to ICs and protection parts.
- Net names: `VRAW`, `VSYS`, `+3V3`, `+3V3A`, `DC_FUSED`, `EF_UVLO`,
  `PWR_FAULT_N`, `I2S_BCLK_DRV`, `BOOT_N`, `KEY_N` — ASCII, upper snake case,
  `_N` for active-low, rails named by role or voltage.
- Text sizes on A4: pin numbers smallest, values and labels the body size,
  reference designators a step larger, sheet titles about twice the body.
