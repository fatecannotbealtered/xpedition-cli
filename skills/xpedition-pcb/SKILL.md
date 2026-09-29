---
name: xpedition-pcb
version: "1.0.3"
description: "Handles board work in Xpedition Layout through the xpedition-cli tool: builds the board from a drawn schematic, forward-annotates it and brings it up to date after the schematic or a footprint changes, then the outline, mounting holes, net classes and trace widths, placement and moving parts, copper pours, autorouting (all nets or named ones) or planned hand routing, DRC plus board rules, placement metrics, the stackup and board data, renders and screenshots, and the fabrication package (Gerber, NC drill, ODB++, centroid). Use when the user asks to lay out, place or move parts on, route, check, measure, render or export the PCB of an Xpedition project, including a part move given in mm, even without the word Xpedition. Not for the schematic or the BOM (xpedition-schematic), or for install, sessions and Layout connection failures, projects, backups and the design library (the xpedition-cli Skill, loaded before this one)."
license: MIT
user-invocable: true
metadata: {"requires":{"bins":["xpedition-cli"],"skills":["xpedition-cli"],"min_version":"1.0.3"}}
---

# xpedition-pcb

Read `../xpedition-cli/SKILL.md` before running any command. It carries what
every xpedition-cli task needs and this Skill does not repeat: the install, the
first step (`context`, `doctor`, `reference`), sessions, backups, the design
library, the dry-run → confirm recipe, the error decision tree, the security
boundary and the `_untrusted` rule. If that file is missing, STOP CHECKPOINT:
tell the user the xpedition-cli entry Skill is not installed and, once they
agree, install the family with `npx skills add fatecannotbealtered/xpedition-cli -y -g`.

## When to use

- turning a drawn schematic into a board: create it, forward-annotate;
- the first layout: outline, holes, net classes, placement, pours, routing,
  checks;
- moving parts, routing by hand, measuring whether a change made it better;
- looking at the board and reading it back;
- the fabrication package.

Not for the schematic or the BOM (xpedition-schematic), or for projects, backups
and the library (xpedition-cli). Never present an autorouted board or a generated
placement as signed off: both are starting points for a person.

## Before a board task

`pcb *` commands run in Layout, except `pcb stitch` and `pcb metrics --geometry`,
which work from files, and `pcb create`, which needs Designer. Start Layout with
`session start --kind pcb`. Every command takes `--project X.prj` (or the board's
`.pcb`). Read `reference/pcb-conventions.md` before touching a board: the rules
here are its non-negotiable subset, and only a company rule changes them. When
`context` lists a knowledge-base document for board work, read it first.

While a board opens, Layout may ask about a stale lock, database recovery or
forward annotation; the adapter answers and lists what it pressed under
`prompts`. Read a prompt you did not expect before going on.

## From the schematic to a board

The schematic is drawn and packaged (`library build --package`, in
xpedition-schematic). Then, each a dry run and a confirm:

1. `pcb create`: the board from the library's `4 Layer Template` through
   JobWizard (`--template` for another).
2. `session start --kind pcb`, then `pcb annotate`: the packaged parts and nets
   arrive, unplaced. Done only on `outcome: annotated` (`annotated_on_open` and
   `in_synch` count too) with no `errors`, and counts matching the schematic.
3. `pcb info`: the board in numbers -- components, nets, the board's thickness
   (`thickness_mm`) and its stackup layer by layer, the net classes with their
   widths per layer.

## First layout

In this order; the writes are each a dry run and a confirm:

1. `pcb outline --width W --height H --radius 3`: the outline, a rectangle from
   the origin.
2. `pcb holes --diameter 3.2`: a mounting hole in each corner, before the
   arrangement so the corners stay clear. Only sizes the library's padstacks have
   are accepted; the refusal lists them.
3. `pcb rules --class POWER --nets VBAT,+3V3 --width 0.5`: a net class with its
   widths, through Constraint Manager; supplies 0.5 mm, signals keep the
   template's 0.254 mm. The result must say `seen_by_layout`.
4. `pcb arrange --design FILE`: one cluster per IC with its parts around it,
   decoupling nearest, connectors on the edges, a zone label per sheet. Its dry
   run sizes the board: `summary.outside` names parts the outline cannot hold,
   which means a bigger outline (steps 1–2 again), not a smaller gap.
5. `pcb labels`: every reference designator beside its part, clear of the others.
   A designator listed in `unplaced` found no free spot: its part is packed too
   tight; move it (or its neighbour) with `pcb move` and run `pcb labels` again.
6. `pcb pour --net GND --layer 2`: an inner ground plane.
7. `pcb route --layers 1,4`: the autorouter on the outer layers (Route at effort
   1–5, Via Min, Smooth); `--nets` routes only the nets named. The result says
   `complete` and lists `unrouted` nets.
8. `pcb pour --net GND --layer 1` and `--layer 4`: outer ground pours after
   routing, never before: a pour in place makes the router count ground as done.
   A request to route the board covers them; after routing only some nets they
   wait until the rest is routed.
9. `pcb check`: Layout's Batch DRC, every hazard with its kind, objects and
   position, plus the board rules DRC does not cover (parts unplaced, off the
   board or overlapping; decoupling capacitors far from their IC; connectors far
   from an edge; acute corners; nets still open).
10. `pcb render --output board.png`: look at it.

Report a board checked only when `pcb check` says `passes` (no DRC error), its
`board_rules` hold no high or medium finding, and you can name every warning kind
and why it is acceptable (`ViasUnderParts`, tented vias under a body, is;
overlapping pads or open nets are not). `clean` is stricter: no hazard at all. A
net that stays open beside a fine-pitch part is a rule problem (0.254 mm on the
stock templates), not a router problem.

To route a net again once the outer pours are in -- after moving its parts -- take
the pours off first: `pcb unroute --nets NET` (dangerous: ask first), `pcb pour
--net GND --layer 1 --remove` (and 4), `pcb route --nets NET`, then pour again.
With a pour in place the router counts its net as connected, and after the planes
regenerate the pads it did not route are open (`pcb check` names them
`PartialNets`). Measure before and after (below) to show what the move gained.

```bash
xpedition-cli pcb outline --project X.prj --width 60 --height 45 --radius 3 --dry-run --compact
xpedition-cli pcb holes --project X.prj --dry-run --compact
xpedition-cli pcb rules --project X.prj --class POWER --nets VBAT,+3V3 --width 0.5 --dry-run --compact
xpedition-cli pcb arrange --project X.prj --design design.json --dry-run --compact
xpedition-cli pcb route --project X.prj --layers 1,4 --dry-run --compact
xpedition-cli pcb check --project X.prj --compact
xpedition-cli pcb render --project X.prj --output board.png --compact
```

## Moving parts

One part: read where it is (`pcb geometry --refdes R1`), then `pcb move --refdes
R1 --to x,y --rotate 90` (millimetres). A set, aligned or distributed: `pcb move
--file task.json`; read `reference/placement-tasks.md`. Traces stay where they
were: `pcb check` afterwards; routing the moved parts' nets again is a write of
its own, asked for like any other. Never use `pcb arrange` for a small edit.

## Measuring a layout

DRC says whether a board breaks a rule, not whether its placement is good.
`pcb metrics` measures it: the ratsnest per net and in total, crossings, parts
overlapping, outside or unplaced, each decoupling capacitor's distance to its IC,
connectors' distance to an edge, density, and the routing's length, vias and
sharp corners. Measure before a change, keep the result, measure after with
`--baseline`: `delta` gives each value before and after, lower is better for all
but `density`. Say a layout got better only from those numbers.

```bash
xpedition-cli pcb metrics --project X.prj --output before.json --compact
xpedition-cli pcb metrics --project X.prj --baseline before.json --compact
```

`pcb geometry` is the board as data (outline, parts, pads, traces, vias, planes,
keepouts, open nets); `--refdes` and `--nets` narrow it, `--output` keeps it for
`pcb metrics --geometry`, `pcb stitch` and hand routing.

## Looking at the board

`pcb render --output board.png` draws the board from its data and needs no
screen. `pcb show --output board.png` brings Layout's window to the front for the
person and captures it; it captures black while the desktop is locked. A board
that "looks empty" after annotation is usually not broken: its parts stay
unplaced until the arrangement, and the stock templates open under a display
scheme that hides top-side parts. `pcb info` counts the parts and `pcb show`
switches to a scheme that shows them; `--top-view` shows pours filled and one
layer at a time for someone at Layout's screen.

## After a footprint changed

Annotating again keeps a part's old cell: Layout never swaps the cell of an
existing component. Recreate the board: `project backup`, then `pcb create
--replace`, `pcb annotate`, and the first layout again -- a few minutes, all
through the CLI.

STOP CHECKPOINT: `pcb create --replace` archives the layout folder to a zip
beside the project and deletes it; confirm only with the user's go-ahead for that
board (the confirm needs `--dangerous`), and report the archive's path.

## Handing over

Placeholder cells (`CLI_*`) are placeholders: right pin count, rough size,
nothing a factory can use; say so, and add the real parts to the library as the
follow-up. For the fabrication package read `reference/fabrication.md`: the board
checked first (as above), then `pcb export --output DIR` (dry run, confirm). The
package is ready only when the confirmed export's `checks.ok` is true; its README
leaves thickness, finish and mask colour to the board house: say so.

STOP CHECKPOINT: ask before confirming a board write the user has not asked for,
and before any that discards work: `pcb arrange` on a routed board (it deletes
every trace and via first, and `--all` also moves placed parts; for a few parts
offer `pcb move`, which keeps the routing), `pcb route --unroute`, `pcb unroute`,
`pcb annotate --unroute`, `pcb pour --replace` or `pcb holes --replace` on work
from before this task, or any confirm whose preview lists something it deletes.
Those whose preview says `dangerous` also need `--dangerous`, added only after the
user agreed to that loss; back the project up first (`project backup`).

## References

| Task | Read |
| --- | --- |
| Any board edit | `reference/pcb-conventions.md` |
| Routing a net or a board by hand | `reference/hand-routing.md` |
| Moving a set of parts | `reference/placement-tasks.md` |
| Sending the board out | `reference/fabrication.md` |

## Eval Scenarios

- Entry first: read `../xpedition-cli/SKILL.md` before any command; with it
  missing, stop and ask before installing the family.
- Schematic to board: create and annotate with a dry run each; done only on
  `outcome: annotated` with matching counts.
- First layout: the outline grown until the arrange dry run leaves nothing
  outside, holes before the arrangement, outer pours only after routing, and
  "checked" only when `pcb check` passes with no high or medium board rule and
  every warning explained.
- Work that goes: an arrange on a routed board and `pcb create --replace` stop for
  the user, with a backup first; `--dangerous` only after the user agreed.
- One part: `pcb move`, never `pcb arrange`, and the routing checked afterwards.
- Better or worse: `pcb metrics` saved before the change, compared with
  `--baseline` after it; the answer quotes the deltas.
- Moving parts on a routed, poured board: unroute their nets, remove the outer
  pours, move, route those nets, pour again, check.
- Boundary: a schematic or BOM request is not this Skill's.
