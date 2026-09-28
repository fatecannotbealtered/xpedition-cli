# Parts files for `library add`

Contents: the file · symbols · footprints (IPC-7351B families, lands one by one,
other sources) · the pin map · what the dry run says · checking afterwards.

`reference --command "library add"` carries the file's JSON schema
(`file_json_schema`); this page says how to fill it from a datasheet.

## The file

```json
{
  "partition": "PartQuest",
  "parts": [
    {
      "number": "TPS7A2033PDBVR",
      "description": "LDO regulator, 3.3 V, 300 mA, SOT-23-5",
      "prefix": "U",
      "properties": {"Manufacturer Name": "Texas Instruments"},
      "symbol": {
        "left": [["1", "IN"], ["3", "EN"]],
        "right": [["5", "OUT"], ["4", "NC"]],
        "bottom": [["2", "GND"]],
        "pintypes": {"1": "POWER", "2": "GROUND", "3": "IN", "5": "POWER", "4": "ANALOG"}
      },
      "footprint": {
        "family": "gullwing", "pins": 5, "positions": 6, "omit": [5],
        "pitch": 0.95, "span": [2.6, 3.0], "terminal": [0.3, 0.6],
        "lead_width": [0.3, 0.5], "body": [1.6, 2.9], "height": 1.45
      }
    }
  ]
}
```

- `number` is the part number the schematic and the BOM carry; one number, one
  part, in one partition.
- `prefix` is the reference designator's letters (`U`, `R`, `C`, `Q`, `J`).
- `value` (optional) is the library's numeric Value: `10k`, `4.7k`, `100n`, `1u`.
  Leave it out for an IC; text goes in `description` or a property.
- `properties` are free text; this library's own names are `Manufacturer Name`
  and `Manufacturer Part Number`.
- `partition` defaults to `PartQuest`, the stock library's partition for this
  tool's parts.

## Symbols

A box, as a design's symbols are written: `left` and `right` list pins from the
top, `top` and `bottom` from the left, each `[number, name]`; `["", ""]` leaves a
gap between pin groups. Give every pin a distinct name (Designer names a net after
the pin a wire meets, so repeated names collide) and an honest type in `pintypes`
(`POWER`, `GROUND`, `IN`, `OUT`, `BI`, `OCL`, `ANALOG`): Designer's check warns
for every `BI` pin that meets a supply. Put supplies on top, grounds at the
bottom, inputs left, outputs right.

A built-in kind instead of a box: `{"kind": "RES"}` -- `RES`, `CAP`, `CAPP`,
`IND`, `DIODE` (1 cathode, 2 anode), `LED`, `SW`, `BAT`, `NTC`, `NMOS` and `PMOS`
(1 gate, 2 source, 3 drain), `TP`. It is the same symbol file a design's `RES`
draws.

## Footprints

### IPC-7351B families, from the datasheet's package drawing

Give each dimension as the datasheet does: a number, `[min, max]`, or
`{"nominal": n, "tolerance": t}`; millimetres. `height` is the seated height.
`density` is `N` (nominal, the default), `M` (most) or `L` (least). The cell is
named the IPC way (`SOIC127P600X175-8N`) unless `name` says otherwise.

| Family | Packages | What to give |
| --- | --- | --- |
| `chip` | resistors, capacitors, inductors 0201–2512 | `size` (EIA code) or `body_length`, `body_width`, `terminal`; `height` |
| `molded` | tantalum capacitors, SMA/SMB/SOD-123F diodes | `body_length`, `body_width`, `terminal`, `lead_width`, `height`; `polarized` |
| `gullwing` | SOIC, SSOP, TSSOP, MSOP, SOT-23, QFP | `pins`, `pitch`, `span` (toe to toe), `terminal` (foot length), `lead_width`, `body` [width, length], `height`; `rows` 4 for a QFP |
| `jlead` | SOJ, PLCC | as gull-wing |
| `nolead` | QFN (`rows` 4), DFN and SON (`rows` 2) | `pins`, `pitch`, `body` [width, length], `terminal`, `lead_width`, `height`; `exposed_pad` |
| `through` | headers, DIPs | `pins`, `pitch`, `lead` (diameter, or `{"square": w}`), `height`; `rows` 2 with `row_pitch` and `numbering` (`around` for a DIP, `zigzag` for a header) |

Pins are numbered as the packages are: down the left row from the top, then up
the right; a quad package goes on counter-clockwise. A package with a missing
lead position (SOT-23, SOT-23-5) gives `positions` and the 1-based positions it
leaves out in `omit`; the rest are numbered in order. An `exposed_pad` is
`{"size": [w, h], "pin": "9", "paste": 0.7}`: its pin number defaults to one past
the last, the terminals are pulled back to clear it by 0.2 mm, and its paste
covers 70 % unless `paste` says otherwise. `extra_pads` adds lands to a family
(a SOT-223 tab); give it the pin number of the lead it belongs to.

Lands have rounded corners (a quarter of the short side, at most 0.25 mm);
`"corners": "square"` for plain rectangles. The silkscreen stays 0.2 mm off every
land, pin 1 is marked with a small triangle, and the placement outline encloses
body, lands and silkscreen with the density's courtyard excess.

### Lands one by one

For anything else, give the lands: `"pads": [{"pin": "1", "x": -0.95, "y": 0,
"width": 0.5, "height": 0.35, "shape": "rect"}, ...]` with `shape` `rect`,
`round` or `oblong`, and `drill` for a plated through hole; plus `height`, and
`body` for the outlines. Several lands may carry one pin number -- a MOSFET's
drain leads and its paddle -- and all of them are on that pin's net. `pin_one`
marks pin 1. Name the cell with `name`.

### Other sources

- `{"cell": "NAME"}`: a cell the library holds already (`library list --kind cells`).
- `{"kicad": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"}`: a KiCad footprint imported
  with `library import` first.
- `{"package": "SOIC8"}`: this tool's placeholder shapes (`0402`, `0603`, `0805`,
  `SOT23`, `SOICn`, `TSSOPn`, `HDRn`, `TP`); right pin count, nothing a factory
  can use.

## The pin map

Each symbol pin goes to the cell pin of the same number. When the package numbers
its pins otherwise, `"pinmap": {"1": "G", "2": "S", "3": "D"}` maps symbol pin
numbers to cell pin numbers. Symbol and cell must carry the same pin numbers:
Layout refuses a part whose cell has a pin its symbol lacks. Give an unused
package pin a symbol pin (`NC`) rather than leaving it out.

## What the dry run says

`preview.actions` sorts every part, symbol, cell and padstack: `add`, `keep` (the
library holds exactly this; left alone), `use` (a cell referenced, not written)
or `replace`. `preview.replaces` lists what the import would overwrite; with any,
the confirm needs `--dangerous`, and every part using a replaced cell or padstack
changes too. `preview.parts` gives each part's lands, pin numbers and the pins
that share a number. A part number another partition holds is refused: the
packager would find two parts of one number.

## Checking afterwards

The confirmed run reads the library back and checks each part (`verification`).
`library render --project X.prj --part NUMBER --output p.png` draws it as the
library holds it; `library check` checks the whole library. A design uses the part
by naming it in its symbols: `"symbols": {"LDO": {"part": "TPS7A2033PDBVR"}}`.
