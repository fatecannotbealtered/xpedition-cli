# The parts file of `part create`

`part create --doc <project.prj> --file <parts.json>` adds the parts of a parts file to the project's central library. Each part gets a symbol, a footprint cell with its padstacks, and a pin map. A part identical to one the library holds is skipped; a part number the library holds with other content is refused, and nothing is written.

## Shape

```json
{
  "partition": "PartQuest",
  "parts": [
    {
      "number": "DEMO-RES-1K-0603",
      "description": "Resistor 1k 0603",
      "prefix": "R",
      "value": "1k",
      "symbol": {"kind": "RES"},
      "footprint": {"family": "chip", "size": "0603"}
    },
    {
      "number": "DEMO-CONN-2",
      "description": "Header 1x2, 2.54 mm",
      "prefix": "J",
      "symbol": {"left": [["1", "PIN1"], ["2", "PIN2"]]},
      "footprint": {"family": "through", "pins": 2, "pitch": 2.54, "lead": {"square": 0.64}, "height": 8.5}
    }
  ]
}
```

## Fields

- `partition`: the central-library partition, a plain identifier. It must be registered in the library; the stock template registers `PartQuest`. An unregistered partition stops the command with a request for the person to register it in Library Manager's Partition Editor.
- `parts`: 1 to 200 parts, each with a unique `number`.
- `number`: the part number, 1 to 100 characters.
- `prefix`: the reference designator prefix, such as `R`, `C`, `U`, `J`.
- `description`, `properties` (an object of name and text): optional.
- `value`: optional; a number with an SI multiplier only, such as `10k`, `4.7k`, `1u`. Other text is refused, because the library would store it as 0 (knowledge `library-part-value-is-numeric`).
- `symbol`: a built-in kind, `{"kind": "RES"}` (RES, CAP, CAPP, IND, DIODE, LED, SW, BAT, NTC, NMOS, PMOS, TP), or a box with pins on its sides: `left`, `right`, `top`, `bottom`, each a list of `[number, name]`, and optional `pintypes` (`{"1": "POWER"}`: IN, OUT, BI, TRI, OCL, OEM, ANALOG, POWER, GROUND).
- `footprint`, one of:
  - an IPC-7351B family: `family` (chip, molded, gullwing, jlead, nolead, through) with `size` for chips (0201 to 2512), or `pins`, `pitch`, `span`, `terminal`, `lead_width`, `body`, `height` and `density` (M, N, L) from the datasheet, in mm; a through-hole part gives its `lead`: a number for a round lead's diameter, or `{"square": 0.64}` for a square lead;
  - lands one by one: `pads`, a list of `{pin, x, y, width, height, shape, drill, plated}` in mm;
  - a placeholder package: `{"package": "SOIC8"}`;
  - a cell the library holds: `{"cell": "NAME"}`.
- `pinmap`: optional `{symbol pin: cell pin}` when the numbers differ. The symbol's pins and the cell's pins must match one to one (knowledge `cell-pin-count-must-match`).

Before writing a parts file, read `xpedition-cli knowledge list --applies-to "part create"`.
