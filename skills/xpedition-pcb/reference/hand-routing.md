# Routing by hand

For the person's layout, when the autorouter's is not good enough. Native
commands name `--backend native_xpedition --project X.prj`; every write is a
dry run first, then the same command with `--confirm <confirm_token>`.

`pcb geometry --output board.json` gives every pad, pin, trace, via and plane;
plan the traces yourself (a `PlanBuilder` from `xpedition_cli.routing_plan`
resolves pin names and checks angles and clearances; `pcb stitch --geometry
board.json --net GND` plans the ground vias without Layout, a plan to draw with
`pcb trace --file`), then `pcb trace --file plan.json --geometry board.json`
(`--dry-run` shows the nearest pin of every trace end and the offline check;
`--confirm` draws; Layout's online DRC refuses an item that violates a rule and
the result names it), read `opens_after` per net and `pcb render` to look. Fix a
wrong piece with `pcb unroute --at x,y --layer N` and draw it again; move a part
with `pcb move --refdes R1 --to x,y --rotate 90`; when someone is watching
Layout's screen, `--pace 0.15` on `pcb trace` / `pcb via` / `pcb arrange` makes
the items land one at a time instead of in a burst; tidy the designators with
`pcb labels`.

Widths: 0.254 mm signals, 0.5 mm supply (the POWER class), 0.3 mm ground stubs
(raise the default class's expansion width first). Keep 0.254 mm from any pad a
via does not sit inside; a test point is a surface-mount pad, so an inner-layer
run to it needs a via beside it; pour the outer layers after the traces are in,
then stitch every ground pad with a via. A 39-part board took two rounds this
way: 129 traces, 41 vias, DRC clean.

```bash
xpedition-cli pcb geometry --backend native_xpedition --project X.prj --output board.json --compact
xpedition-cli pcb stitch --geometry board.json --net GND --output stitch.json --compact
xpedition-cli pcb trace --backend native_xpedition --project X.prj --file plan.json --geometry board.json --dry-run --compact
```
