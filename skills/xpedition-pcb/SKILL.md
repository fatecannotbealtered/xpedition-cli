---
name: xpedition-pcb
version: "1.0.0"
description: "Handles board work in Xpedition Layout through the xpedition-cli tool: builds the board from a drawn schematic, forward-annotates it and brings it up to date after the schematic or a footprint changes, then the outline, mounting holes, placement, copper pours, net classes and trace widths, autorouting or planned hand routing, DRC, board renders and the fabrication package (Gerber, NC drill, ODB++, centroid). Use when the user asks to lay out, place or move parts on, route, DRC-check, render or export the PCB of an Xpedition project, even without the word Xpedition. Not for drawing, reviewing or exporting the schematic, the BOM, pin planning, footprint mapping or creating a project: those are in the xpedition-cli Skill, which is loaded before this one."
license: MIT
user-invocable: true
metadata: {"requires":{"bins":["xpedition-cli"],"skills":["xpedition-cli"],"min_version":"1.0.0"}}
---

# xpedition-pcb

Read `../xpedition-cli/SKILL.md` before running any command. It carries what
every xpedition-cli task needs and this Skill does not repeat: the install, the
first step (`context`, `doctor`, `reference`), native sessions, the dry-run →
confirm recipe, the error decision tree, the security boundary and the
`_untrusted` rule. If that file is missing, STOP CHECKPOINT: tell the user the
xpedition-cli entry Skill is not installed and, once they agree, install the
family with `npx skills add fatecannotbealtered/xpedition-cli -y -g`.

This Skill covers the board in Xpedition Layout, from packaging a drawn
schematic to the fabrication package.

## When to use

Use this Skill for:

- turning a drawn schematic into a board: package the parts, create the board,
  forward-annotate;
- the first layout: outline, mounting holes, placement, pours, net classes,
  autorouting and DRC;
- routing by hand, moving parts and small local placement adjustments;
- looking at a board and reading it back;
- the fabrication package.

Do not use it for drawing, reviewing or exporting the schematic, the BOM, pin
planning, footprint mapping or creating a project; xpedition-cli covers those.
Never present an autorouted board or a generated placement as signed off: both
are starting points for a person.

## Before a board task

`pcb *` commands run in Xpedition Layout. Check `doctor`'s `native_session` for
what is attached and start Layout explicitly with `session start --backend
native_xpedition --kind pcb`. `--backend` defaults to `mock`, so every native
command names `--backend native_xpedition --project X.prj`; the short forms in
the prose leave both out. Read `reference/pcb-conventions.md` before touching a
board: the rules in this Skill are its non-negotiable subset. `reference
--compact` stays the source of truth for commands and parameters.

Every write is shown as its dry run: inspect the preview, then run the same
command with `--confirm <confirm_token>` in place of `--dry-run`. The token is
bound to the arguments, so change nothing else.

While a board opens, Layout may ask about a stale lock, database recovery or
forward annotation; the adapter answers them and lists what it pressed under
`prompts`. If a result carries a prompt you did not expect, read it before
going on.

A MockBackend read needs no Layout:

```bash
xpedition-cli pcb info --backend mock --project ./demo-project.json --compact
```

## From the schematic to a board

Three guarded steps, each `--dry-run` then `--confirm`, then a read-back:
`library build --design FILE --package` (padstacks, cells and parts for every
part — placeholder cells unless the design names KiCad footprints — imported into
the project's central library, then packaged), `pcb create` (the board from the
library's `4 Layer Template` through JobWizard; `--template` for another), `pcb
annotate` (Layout's forward annotation: the packaged parts and nets arrive
unplaced), then `pcb info` and `pcb components` to read the board back. Report
done only when `pcb annotate` says `outcome: annotated` (`annotated_on_open` and
`in_synch` count too) with no `errors`, and the counts match the schematic.

`FILE` is the design file the schematic was drawn from. Its `packages` choose
the footprints; see the schematic drawing conventions in `../xpedition-cli/SKILL.md`.

```bash
xpedition-cli session start --backend native_xpedition --kind pcb --compact
xpedition-cli library build --backend native_xpedition --project X.prj --design design.json --package --dry-run --compact
xpedition-cli pcb create --backend native_xpedition --project X.prj --dry-run --compact
xpedition-cli pcb annotate --backend native_xpedition --project X.prj --dry-run --compact
xpedition-cli pcb info --backend native_xpedition --project X.prj --compact
xpedition-cli pcb components --backend native_xpedition --project X.prj --compact
```

## First layout

Forward-annotated parts are invisible until placed. The first layout is a
handful of commands in this order; 1–7 are guarded writes, each `--dry-run` then
`--confirm`, and 8–9 are reads to run directly:

1. `pcb outline --width W --height H --radius 3`: a rectangle from the origin
   with rounded corners, replacing the board's outline.
2. `pcb holes`: a mounting hole in each corner; do it before the arrangement so
   the planner keeps the corners clear (`--replace` once the outline has grown).
3. `pcb arrange --design FILE`: one cluster per IC with its parts around it,
   decoupling nearest, rows and columns on one pitch, centres on a 0.5 mm grid,
   room above every part for its designator, clusters in rows by sheet,
   connectors on the side edges with their designators inward, test points
   along the bottom, a zone label per sheet from the design file's `zone`;
   `--all` to move parts that are already placed; each part of a cluster stands
   beside the IC pin it connects to, its own pad facing that pin. Its dry run
   sizes the board: `summary.outside` names parts the outline could not hold,
   which means a bigger outline (steps 1 and 2 again), not a smaller gap — 70 ×
   48 mm holds the 39-part `examples/demo-sensor-board.json` board on real
   footprints with room for every designator. A confirmed arrange deletes every trace and via on the board first and
   reports what it removed.
4. `pcb pour --net GND --layer 2`: a copper plane inset from the outline,
   rounded like it; `--replace` after the outline changed.
5. `pcb rules --class POWER --nets VBAT,+3V3 --width 0.5`: a net class with its
   trace widths on every layer, written through Constraint Manager; supply nets
   get 0.5 mm, signals keep the template's 0.254 mm; the result must say
   `seen_by_layout`.
6. `pcb route --layers 1,4`: Layout's autorouter on the outer layers only:
   Route at effort 1–5, Via Min, Smooth; `--unroute` to start over.
7. `pcb pour --net GND --layer 1` and `--layer 4`, the outer-layer ground
   pours: after routing, never before: a pour in place makes the router count
   the ground net as done while the copper leaves pins cut off.
8. `pcb drc`: Layout's Batch DRC, every hazard listed with its kind, objects
   and position; `errors` and `warnings` apart, `passes` when there are no
   errors.
9. `pcb render --output board.png`: the board drawn from its geometry in KiCad's
   colours, `--side bottom` for the other side.

The result of `pcb route` says `complete` and lists `unrouted` nets with their
open count; a net that stays open next to a fine-pitch part is a rule problem
(0.254 mm traces and clearances on the stock templates), not a router problem.
Report a board as checked only when `pcb drc` says `passes` and you can name
each warning kind and why it is acceptable (`ViasUnderParts` — vias under
surface-mount bodies, tented — is; overlapping pads or partial nets are errors
and are not).

The placement is a starting point for a person, not a layout. `pcb arrange`
lists each part's `x`/`y` in millimetres and `read_back` proves the placement.

```bash
xpedition-cli pcb outline --backend native_xpedition --project X.prj --width W --height H --radius 3 --dry-run --compact
xpedition-cli pcb holes --backend native_xpedition --project X.prj --dry-run --compact
# confirm the arrange only once its dry run leaves nothing in summary.outside
xpedition-cli pcb arrange --backend native_xpedition --project X.prj --design design.json --dry-run --compact
xpedition-cli pcb pour --backend native_xpedition --project X.prj --net GND --layer 2 --dry-run --compact
xpedition-cli pcb rules --backend native_xpedition --project X.prj --class POWER --nets VBAT,+3V3 --width 0.5 --dry-run --compact
xpedition-cli pcb route --backend native_xpedition --project X.prj --layers 1,4 --dry-run --compact
xpedition-cli pcb pour --backend native_xpedition --project X.prj --net GND --layer 1 --dry-run --compact
xpedition-cli pcb pour --backend native_xpedition --project X.prj --net GND --layer 4 --dry-run --compact
xpedition-cli pcb drc --backend native_xpedition --project X.prj --compact
xpedition-cli pcb render --backend native_xpedition --project X.prj --output board.png --compact
```

## Moving parts

One part: `pcb move --refdes R1 --to x,y --rotate 90`. Its traces stay where
they were, so check the routing afterwards (`pcb drc`, `pcb render`) and route
again where it broke; Layout refuses a position that touches another part.
Several parts, aligned or distributed: use the selected-placement workflow only
when it is advertised by the installed binary's reference; read
`reference/placement-tasks.md`. Its native smoke status and partial-execution
boundaries remain explicit. Never use `pcb arrange` for a small edit.

```bash
xpedition-cli pcb components --backend native_xpedition --project X.prj --compact
xpedition-cli pcb move --backend native_xpedition --project X.prj --refdes R12 --to 34,35.5 --dry-run --compact
```

## Looking at the board

`pcb show --output board.png` puts the board in front of the person; look at it
yourself too. Layout opens the stock templates under the `Loc: Assembly Bottom`
display scheme, which hides top-side parts, so a board that "looks empty" after
annotation or placement usually needs this, not a fix; `pcb show` switches to
`Loc: All On` (or `--scheme`). When the person looks at Layout's own screen,
`pcb show --top-view`: the stock schemes draw the pours as outlines and every
layer at once, so a poured, routed board looks bare and crowded until that
scheme is picked.

`pcb render --output board.png` needs no screen, unlike `pcb show --output`,
which captures a black PNG while the desktop is locked. A render does not
overwrite an existing file without `--replace`.

## After a cell changed

After `library build` changed a cell that is already on the board, annotate
again and the part keeps its old cell: Layout never swaps the cell of an
existing component. Recreate the board instead: `pcb create --replace`, then
`pcb annotate`, `pcb outline`, `pcb holes`, `pcb arrange`, `pcb pour`, `pcb
rules`, `pcb route`, the outer pours — about three minutes, all through the CLI.

STOP CHECKPOINT: `pcb create --replace` archives the existing layout folder to a
zip beside the project and deletes it. Confirm only with the user's go-ahead for
that board, and report the archive path.

## Handing over

Placeholder cells are placeholders: right pin count and rough size, nothing a
factory can use. Say so when handing over, and keep real cells from the
company's library as the follow-up.

STOP CHECKPOINT: ask the user before confirming a board write they have not
asked for, and before any that discards work: `pcb arrange` on a routed board (a
confirmed arrange deletes every trace and via first; `--all` also moves the parts
already placed), `pcb route --unroute`, `pcb unroute`, `pcb annotate --unroute`,
`pcb pour --replace`, `pcb holes --replace`, or any confirm whose preview lists
something it deletes or removes.

## References

| Task | Read |
| --- | --- |
| Any board edit | `reference/pcb-conventions.md` |
| Routing a net or a board by hand | `reference/hand-routing.md` |
| Sending the board out | `reference/fabrication.md` |
| Aligning or distributing a set of parts | `reference/placement-tasks.md` |

## Eval Scenarios

- Entry first: read `../xpedition-cli/SKILL.md` before any command; with it
  missing, stop and ask before installing the family.
- Schematic to board: package, create and annotate with a dry run each; done
  only on `outcome: annotated` (`annotated_on_open`, `in_synch`) with matching
  counts.
- First layout: the outline grown until the arrange dry run leaves nothing
  outside, holes before the arrangement, the outer ground pours only after
  routing, and "checked" only when `pcb drc` passes with every warning kind
  explained.
- Work that goes: a confirmed arrange deletes all routing and `pcb create
  --replace` archives the layout; both stop for the user.
- One part: `pcb move`, never `pcb arrange`, and the routing checked afterwards.
- Boundary: a schematic drawing or BOM request is not this Skill's.
