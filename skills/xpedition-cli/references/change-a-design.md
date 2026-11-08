# Change a design

A project that exists already, such as a person's own board, is read before anything changes, and changed one named object at a time.

## Read it first

In a Designer session with the project open:

- `component list` gives every component with its part number, value and sheet; `component get --refdes U1` gives one component's attributes and each pin's name and net.
- `net list` gives every net with how many pins it joins; `net get --name VBUS` gives the pins of one net. An unnamed net goes by the name Designer gives it, such as `$1N54`.
- `bom check` lists components without a part number, reference designators used twice, part numbers the central library does not hold, and one part number with several values. When it says `library_checked: false`, the central library could not be read, often because it lies on a drive this machine does not have; the other checks still hold.

In a Layout session with the board open:

- `component list` and `component get` give where each part stands: position, rotation, side, and whether Layout fixes or locks it. `component get` also gives each pin's net and position.
- `mounting-hole list` gives the board's own mounting holes. `pcb get` gives the stackup, the net classes and the outline.

## Change the board

- `component move --refdes U1 --x 20mm --y 15mm --rotation 90deg` puts one part there; `--side bottom` puts it on the bottom, mirrored. A spot another part takes is refused with `E_CONFLICT` and nothing moves: read `component list`, then choose a free spot. A fixed or locked part is refused; only a person releases it in Layout. Check `inside_board` in the result.
- `refdes move --refdes U1 --auto` puts the part's reference designator on the silkscreen beside it, clear of the other parts and texts; `--x`, `--y` and `--rotation` put it exactly. The result's `overlaps` lists what the text covers; it should be empty.
- `mounting-hole create --padstack <name> --corners --inset 3mm` puts a hole in each corner; `padstack list --type mounting_hole` lists the padstacks the central library holds. `mounting-hole delete` takes one back by a point inside it.
- After the changes, run `pcb check` and report its hazards.

## Compare the bill of materials

`bom export --output <file.csv>` writes the BOM, one row per part number and value. With `--baseline <earlier.csv>`, from this tool or another, its result lists the components added, removed and changed since. Report those lists; they are what changed in the design.

Save with `doc save` only when the person asks for it, and say in the report what is still unsaved.
