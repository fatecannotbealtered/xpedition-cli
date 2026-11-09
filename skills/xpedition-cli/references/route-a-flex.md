# Route a flex board between two connectors

A flex board that joins two boards carries one bus between two board-to-board connectors. Read the knowledge entries `layout-flex-bend-and-stiffener` and `layout-flex-connector-bus` first, and the team's flex and routing rules that `xpedition-cli knowledge list --source team --applies-to trace create` returns: they decide the fan-out, the clearances, the via limits and where vias may go.

1. Read the board. `pcb get` gives the outline, the stiffeners (`keepout_kinds.height_limits`), the hatched plane bands that mark bend zones (`planes`), the cutouts, the net classes and the via padstacks; `pcb get --output <file>` adds every pad, keepout and cutout shape. `component get --refdes <connector>` gives each pin's net and position, which shows the two rows and the pins that match from one end to the other. When the board marks no bend zone, ask the person where it bends.
2. Set the rules. Give the power and speaker nets classes as wide as their current needs, define the differential pairs and the spacing between the two nets of a pair, then `constraint import --file <rules.json>` ([rules-file.md](rules-file.md)).
3. Plan the lanes. Write one routing plan for `trace create` ([routing-plan.md](routing-plan.md)):
   - Give every net a lane at a fixed distance from one board edge and follow the outline with it: a straight edge gives a parallel straight lane, an arc of the outline gives an arc about the same centre, its radius larger or smaller by the lane's distance. Lanes laid that way stay evenly spaced, and a pair's two lanes come out the same length.
   - At each connector, fan the traces out from the pins to their lanes inside the stiffener: the row nearer the board's body on the pad layer, the other row through one via beside each pin. Finish every turn and width change before a bend zone, and cross the zone straight.
   - Give each ground pin a via beside it inside the stiffener.
   - Compare the lengths of each pair's two nets from the plan's points before you draw.
4. Draw. `trace create --file <plan.json>` refuses a plan that breaks a width or a clearance and lists every place; change the plan and run it again. Its result says for each net whether it is routed.
5. Pour. `plane create --net GND --layer <n>` for each layer; for a bend zone, pour the solid part and the hatched band as separate shapes with `--from-y` and `--to-y`, the band with `--hatch diagonal --hatch-width --hatch-distance`.
6. Stitch. `via create --net GND --x --y` places each ground stitching via, along the board's edges between the bend zones.
7. Check. `pcb check` lists the hazards, and `pcb get` must report no open net. Report each pair's length difference, each power net's width and via count against its current, and any rule you could not meet.
