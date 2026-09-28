# Moving a set of parts

`pcb move --file task.json` moves a selection of parts in one task: translate,
rotate, align and distribute, applied in order to the parts' current positions,
which it reads from the board. The file's schema is in `reference --command "pcb
move"` (`file_json_schema`); an example:

```json
{
  "schema_version": "1.0",
  "unit": "mm",
  "selection": ["R1", "R2", "R3"],
  "steps": [
    {"op": "align", "axis": "y", "anchor": "R2"},
    {"op": "distribute", "axis": "x", "start": 10, "end": 30}
  ]
}
```

Name the selection explicitly and decide the order, the anchor and the origin
before writing the steps: the distance between origins is not the clearance
between bodies. Never use `pcb arrange` for a small edit: it places the whole
board again and deletes the routing.

The dry run lists each part's position before and after; confirm only within the
user's authorization. A part fixed or locked in Layout is refused, not moved. The
result reports every part: moved, refused, or `unknown` when the response did not
say. After a failure some parts may have moved already: read the board back
(`pcb geometry --refdes ...`) and plan what is left; never replay the token or
rebuild the original task blindly.

Moving a part does not move its traces. Look at the board (`pcb render`), run
`pcb check`, and route again where it broke. The moves have been verified on the
top side of a board only.
