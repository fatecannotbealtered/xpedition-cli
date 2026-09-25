# PCB layout conventions for Xpedition Layout

Defaults an agent applies when it creates or edits a board. Nothing here is
enforced yet: Layout attaches and licenses, but no board exists in the smoke
project and a board is created by packaging the schematic in Designer, so every
rule is **default** (industry practice) unless marked **verified**. Company
rules supersede defaults when they arrive (**TBD** marks where a company
decision is expected). `reference --compact` is the source of truth for
commands.

Contents

1. Scope and units
2. Stackup
3. Net classes: width and clearance
4. Vias
5. Placement
6. Routing
7. Copper and planes
8. Silkscreen, assembly and test
9. Manufacturing outputs
10. Checklist
11. Sources

## 1. Scope and units

- Work in mm. The adapter passes coordinates in the board's current unit
  (`epcbUnitCurrent`), so read the unit before placing (TBD: expose it in
  `pcb info`).
- Layout today: attach, licence and health are verified; `place_pcb_component`
  and `move_pcb_component` act on an open board; there is no COM call that
  creates a board — that is Designer's packaging step (`package`), which needs
  every part in the PDB.
- The review pipeline reads ODB++; produce it alongside Gerber.

## 2. Stackup

| Layers | Order (top to bottom) | Use |
|---|---|---|
| 2 | SIG + GND pour, SIG + GND pour | simple boards without controlled impedance |
| 4 | SIG, GND, PWR, SIG | default for MCU + USB + charger class boards |
| 6 | SIG, GND, SIG, PWR, GND, SIG | when 4 layers cannot route or two reference planes are needed |

- Every signal layer adjacent to a solid reference plane.
- 1 oz (35 µm) outer copper by default; 2 oz for rails above 3 A.
- Total thickness 1.0 or 1.6 mm unless mechanics say otherwise (TBD).

## 3. Net classes: width and clearance

Defaults for 1 oz copper; the fab's capability sheet sets the floor (TBD).

| Class | Width | Clearance | Notes |
|---|---|---|---|
| SIGNAL (default) | 0.15 mm | 0.15 mm | 6/6 mil; 0.1/0.1 mm only for BGA escape |
| POWER | by current: ≥ 0.3 mm per A on outer layers, ≥ 0.6 mm per A on inner layers, 10 °C rise | 0.2 mm | IPC-2221 sizing; recompute for the real copper weight; pours preferred above 1 A |
| USB2_DIFF | 90 Ω differential per stackup | pair gap per stackup | coupled and length-matched (§6) |
| DIFF_100 | 100 Ω differential per stackup | pair gap per stackup | |
| RF_50 | 50 Ω single-ended per stackup | | keep-out both sides |
| HIGH_VOLTAGE | class width | per the IPC-2221 voltage table | battery packs and mains-adjacent nets |

- Copper to board edge ≥ 0.3 mm; components to board edge ≥ 1 mm.

## 4. Vias

| Type | Drill / pad | Use |
|---|---|---|
| Standard | 0.3 / 0.6 mm | everything |
| Minimum | 0.2 / 0.45 mm | dense escape only |
| Power | at least 2 vias per ampere per layer change | rails |

- No via in an SMT pad unless filled and capped; via to pad ≥ 0.2 mm.
- A ground via next to every signal-layer change; a via fence every ≤ 5 mm
  along RF sections and the board edge of RF boards.

## 5. Placement

- Mechanics first: connectors, switches, LEDs and mounting holes where the
  enclosure needs them; keep-out ring ≥ 1 mm around mounting holes (TBD washer
  size).
- Decoupling: each supply pin has its capacitor ≤ 2 mm away on the same side
  with the shortest path to a ground via; the smallest value closest to the pin.
  This is the rule library's P01.
- Crystal ≤ 10 mm from its IC, nothing routed underneath, ground fill around it.
- Switching converters (charger, boost): input capacitor, switch, inductor and
  output capacitor in the smallest loop; the switch node is a small island.
- Thermal: heat sources spaced apart and over copper; temperature sensors and
  thermistors away from them at the distance the design note specifies.
- Group by function following the schematic sheets; single-side SMT preferred,
  bottom side for passives only when the assembly process allows (TBD).
- Polarised parts oriented consistently.

## 6. Routing

- 45° bends or arcs; no acute angles, stubs or loops.
- Continuous return path: no trace crosses a plane split; every layer change
  has a nearby ground via.
- Differential pairs routed together at the class gap; USB 2.0 intra-pair
  mismatch ≤ 1.25 mm (common design-guide value; the platform guide wins).
- Sensitive analog nets (battery sense, thermistor, regulator feedback) short
  and away from switch nodes and inductors; the feedback divider next to the IC.
- No routing under crystals, switcher inductors or antenna keep-outs; antenna
  areas have no copper on any layer, per the module datasheet.

## 7. Copper and planes

- Solid ground plane unbroken under signal areas; power planes split only along
  rail boundaries and never under a differential pair.
- Ground pour on outer layers with stitching vias every ≤ 5 mm; no dead copper
  islands.
- Thermal reliefs on through-hole and hand-soldered pads; direct connection on
  SMT power pads.
- Pour clearance equals the class clearance; minimum pour width 0.2 mm.

## 8. Silkscreen, assembly and test

- A refdes on silk for every part, readable in at most two orientations, never
  on pads or vias; text ≥ 0.8 mm high, line ≥ 0.15 mm (TBD fab capability).
- Polarity and pin-1 marks for diodes, LEDs, electrolytic capacitors,
  connectors and ICs.
- Fiducials: 3 per SMT side, 1 mm copper dot with a 3 mm mask opening, ≥ 5 mm
  from the edge; local fiducials for fine-pitch BGA and QFN.
- Test points on rails, ground, UART, reset and battery sense: ≥ 1 mm pad,
  2.54 mm pitch preferred, one side.
- Solder-mask dam ≥ 0.1 mm between pads; mask expansion 0.05 mm unless the fab
  says otherwise.
- Panel rails, tooling holes and breakaway per the assembler (TBD).

## 9. Manufacturing outputs

ODB++ and Gerber X2, NC drill, IPC-D-356 netlist, pick-and-place centroid
file, assembly drawing, fabrication drawing with stackup and notes, and the
BOM — all from the same released revision.

## 10. Checklist

Classes follow the review rule library: L2 from the netlist, L2b from geometry,
manual needs an engineer.

| ID | Check | Class |
|---|---|---|
| DP-01 | every schematic part placed; forward annotation has zero unresolved items | L2 |
| DP-02 | DRC clean at the class rules | L2b |
| DP-03 | decoupling ≤ 2 mm from its pin (rule library P01) | L2b |
| DP-04 | no plane split under differential pairs | L2b |
| DP-05 | via count on power layer changes | L2b |
| DP-06 | antenna, mounting and edge keep-outs respected | L2b |
| DP-07 | silk clear of pads; polarity marks present | L2b |
| DP-08 | fiducials and test points present | L2 |
| DP-09 | thermal and sensor distances per design notes | L2b |
| DP-10 | board netlist equals schematic netlist | L2 |
| DP-11 | outputs generated from the released revision and reviewed | manual |

## 11. Sources

Versions unverified; cite by name until the company's copy is checked.

- IPC-2221 generic design, IPC-2222 rigid boards, IPC-2141 controlled impedance.
- IPC-7351 land patterns — the installation's Footprint Expert generates to it.
- IPC-6012 fabrication acceptance, IPC-A-610 assembly acceptance, IPC-D-356
  netlist.
- Interface specifications: USB 2.0, I2C (NXP UM10204) — registered in the
  review rule library.
- Company PCB design specification — pending; it supersedes every default.
