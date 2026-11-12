---
id: layout-solder-mask-openings
title: Soldermask and coverlay openings, and baring one net's copper
kind: guide
applies_to: [solder-mask create, solder-mask delete, pcb get, pcb check]
tags: [layout, soldermask, coverlay, flex, grounding]
---

solder-mask create opens the soldermask of one side over a rectangle (`--x1`, `--y1`, `--x2`, `--y2`) or a polygon (`--polygon '{"points_mm": [[x, y], ...]}'`), which leaves the copper under it bare. `--side top` is layer 1 and `--side bottom` the last layer, so on a 3-layer board "layer 3" is the bottom. The preview names the nets whose copper the opening bares there, and refuses an opening over no copper: pour or route the copper first. solder-mask delete removes the opening that holds a point; `pcb get` lists the board's own openings under `openings`.

A board bares ground on purpose where something in the product touches it: a flex board's ears, the tabs of its outline that hold a screw or meet a spring or conductive foam, and the place behind a steel stiffener whose conductive glue grounds it. Such an opening must bare ground alone, or the spring, foam or glue shorts the other net to ground.

- `--net <ground net>` names the net the opening is for, as `pcb get` or `net list` names it. An opening that bares another net's copper or comes within `--clearance` (0.1 mm by default) of it is refused with E_CONFLICT, naming the net and the place, and so is one that bares none of the named net's copper; nothing is drawn.
- `--fit` with `--net` takes the rectangle or polygon as the region to search and opens the largest rectangle in it, at least 0.3 mm a side, that lies on the net's copper and keeps the clearance from all other copper. Where none fits, the preview says so; give a larger region or pour the net there. Run it again with another region for a second spot.
- An opening in a bend zone is refused: bare copper cracks where the board folds.
- The copper is the plane copper Layout poured, with its clearances: pour the planes first.
- `pcb check` lists `opening_short` for each opening of the board's own that bares the copper of two nets or comes within 0.1 mm of a second net's copper, such as after copper was rerouted under it.

A flex board's outer layers may carry coverlay instead of soldermask: `pcb get` shows each insulation layer's kind under `stackup[].insulation`. Where the outer insulation of that side reads `cover_layer`, open it with `--cover-layer`; where it reads `dielectric`, open the soldermask, and the preview refuses `--cover-layer`.

What to do:

- Ears: for each ear in `pcb get` `ears`, run `solder-mask create --side <side> --net <ground net> --polygon '{"points_mm": <the ear's points_mm>}'`.
- Stiffeners: `pcb get` lists them under `keepout_kinds.height_limits`, each with the side it is laminated on. For its glue, run `solder-mask create --side <that side> --net <ground net> --fit` with its `extents_mm` (x1, y1, x2, y2) as the rectangle, once for each stiffener.
- When an opening is refused for another net's copper, move it, search a region with `--fit`, or reroute that copper. Keep `--clearance` at least the board house's mask registration, which their rules give.
