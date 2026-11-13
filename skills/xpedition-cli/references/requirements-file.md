# The requirements file

A requirements file turns a reviewer's list, such as "ground on both sides of the pairs" or "a ground via every 2 mm", into checks the tool measures. `trace check --file <plan> --requirements <file>` predicts them from a routing plan before anything is drawn; `pcb check --requirements <file>` measures them on the board. Both give the same fields, so the two can be compared. Keep the file beside the project and run it after every change: the table it returns is how far the board meets the review.

The file is JSON: `{"requirements": [...]}`. Each requirement has a `kind`, the values of that kind and, if wanted, a `label`, the reviewer's own words, which its result repeats.

| kind | values | measures |
|---|---|---|
| `guard` | `nets`, each a net or a pair or group guarded as one joined with `+`, such as `"TX_P+TX_N"`; `min_both_pct`; `gap_mm` (0.3 unless given) | for each net and layer, the share of its length with ground within `gap_mm` of its copper on both sides: a ground trace, or a ground plane shape that holds copper where no other net's copper leaves the pour without room; the nets of a group cover each other's inner side where they run within 1 mm of each other, however far the pair's spacing sets them apart; a stretch shorter than 2 mm, such as a stub at a pin, is left out |
| `ring` | `net`, `layers`, `offset_mm`, `min_pct`; `tolerance_mm` (0.05 unless given) | the share of the way round the board, `offset_mm` inside the outline, that a trace of `net` covers on each layer, a trace counting within `tolerance_mm` of the line |
| `via_padstack` | `padstack`; `nets` (every via unless given) | the vias that use another padstack |
| `via_pitch` | `net`, `max_mm`; `min_length_mm` (2 unless given) | along every trace of `net` at least `min_length_mm` long, the gaps between its vias, its ends counting as vias; a gap longer than `max_mm` where a via of the net's most common size fits with 0.04 mm to spare, its pad off the ends of the net's traces, fails, with a spot where one fits; a gap where none fits, such as between two signals or across a bend zone, passes and is counted under `no_room`: how many gaps, their length and share of the lines measured, and the lines with no via at all (`without_via`, `without_via_mm`) |
| `plane` | `net`, `layers`; `hatched_in_bends` (false unless given) | whether `net` is poured on each layer with copper made in every shape, such as a band hatched over a bend, and, with `hatched_in_bends`, hatched over each bend zone with no solid copper there; `trace check` leaves it unmeasured, since the pours come after the plan |
| `opening` | `net`, `at` (`ears` or `stiffeners`), `side`; `clearance_mm` (0.1 unless given) | for each of the outline's ears, or each stiffener laminated on `side`, whether an opening of the mask on `side` lies over it that bares `net`'s copper and comes no nearer than `clearance_mm` to any other net's copper; `trace check` leaves it unmeasured |
| `text` | `text`; `side` (top unless given) or `user_layer` | whether the board's own text with those words lies on that side's silkscreen or on the user draft layer; `trace check` leaves it unmeasured |

Each result has its `index` and `kind`, the `label`, `ok` (true, false, or none when it cannot be measured yet, with `reason`), what was measured against what is needed (`measured_pct` and `needed_pct`, or `measured_mm` and `needed_mm`), `count` and `failed`, and `worst`, the worst places with their positions. A requirement the board cannot be measured for, such as a ring on a board without an outline, has `ok: false` and the `reason`.

A `via_pitch` that passes with a large `no_room` share passes only by its "where there is room": the lines there are grounded at their ends alone, such as guards between signals 0.3 mm apart with signals of the other layer beneath them. Report that share and `without_via` to the person with the result; giving the vias room takes lanes spaced for them on every layer the via passes.

The guards and rings of a routing plan take their vias within the pitch wherever one fits, measured as `via_pitch` measures, so the requirement on their own lines passes as planned. A failing gap, such as along a trace of the net drawn by hand, lists in `vias_fit_at_mm` every spot it needs, each as far along as the pitch allows from the last where a via fits, their pads clear of the ends of the plan's traces and of each other: add a via of the net at each to the routing plan's `vias` and run `trace check` again; then draw. A gap whose list stops short has no room further on. The prediction works on the plan's geometry and the measurement on the board's, so after drawing `pcb check` may still find a spot the plan did not show; add it the same way.

Width rules, vias and turns in bend zones, dangling ends, loops and islands are not requirements: the rules file and `constraint import` set the widths, and `pcb check` reports the rest on every run.

An example for a flex board reviewed for ground beside its pairs and clocks, a ring, one via padstack, 2 mm stitching, pours on every layer, bare ground at its ears and behind its stiffeners, and its name on the silkscreen:

```json
{"requirements": [
  {"kind": "guard", "label": "GND on both sides of the speaker, sense and 90 ohm pairs",
   "nets": ["SPK_P+SPK_N", "VSP+VSN", "SS_TX_P+SS_TX_N", "SS_RX_P+SS_RX_N", "HS_DP+HS_DM"], "min_both_pct": 90, "gap_mm": 0.3},
  {"kind": "guard", "label": "GND on both sides of the microphone clock and data, as one group",
   "nets": ["MIC_CLK+MIC_DATA"], "min_both_pct": 90},
  {"kind": "ring", "label": "a ground trace round the outline", "net": "GND", "layers": [1, 3], "offset_mm": 0.32, "min_pct": 80},
  {"kind": "via_padstack", "label": "every via one padstack", "padstack": "VIA_D0.2_P0.4"},
  {"kind": "via_pitch", "label": "a GND via every 2 mm where there is room", "net": "GND", "max_mm": 2.0},
  {"kind": "plane", "label": "GND on every layer, hatched over the bends", "net": "GND", "layers": [1, 2, 3], "hatched_in_bends": true},
  {"kind": "opening", "label": "bare GND at the ears on layer 3, no shorts", "net": "GND", "at": "ears", "side": "bottom"},
  {"kind": "opening", "label": "bare GND behind the stiffeners on layer 1, no shorts", "net": "GND", "at": "stiffeners", "side": "top"},
  {"kind": "text", "label": "the board's name on the silk layer", "text": "FLEX_A_REV1", "user_layer": "silk"}
]}
```
