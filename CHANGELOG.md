# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.1] - 2026-09-29

A new command tree. It breaks every script written against 1.0.0: the old names are
gone with no aliases, and so is the offline mock backend. Under Semantic Versioning
this is a major change; it is released as 1.0.1 by the maintainer's decision, while
nothing depends on 1.0.0 yet.

### Removed

- The mock backend and `--backend`: every command works on a real project through
  the Windows adapter, and the tests fake the adapter at its JSON boundary. With it
  went the commands that answered only for the mock project: `change history`,
  `change rollback`, `exchange inspect`, `exchange import`, `analysis run`,
  `analysis dfm`, `project diff`.
- `agent serve` (the NDJSON and MCP bridge, four read-only tools) and the rest of
  the `agent` group (`snapshot`, `query`, `review`, `capabilities`), copies of other
  commands.
- The `system` group: `capabilities`, `doctor`, `license` and `version` answer in
  `context`, `doctor` and `version`; `api-inventory` left the product.
- Aliases, copies and empty commands: `design snapshot`, `project snapshot`,
  `project tree`, `session logs` (nothing wrote its log), `schematic interfaces`
  (always empty), `schematic pin-plan` and `pin-check`, `bom normalize`,
  `bom variants`, `manufacturing bom`.

### Changed

- 118 commands became 54, each declared once in a registry that the parser,
  `--help` and `reference` all read. Each says its stage in the design flow and what
  must be running (`needs`: designer, layout, xpedition or none), and `reference`
  publishes the flow as a twelve-step `workflow`.
- Renamed or merged, old -> new:
  - `project init` -> `project create`; `project info` and `project tree` ->
    `project info` (read from the `.prj` alone);
  - `session attach`, `session open` -> `session start` (which now opens
    `--project`; it was ignored);
  - `library kicad-import` -> `library import`; `library search|parts|symbols|
    footprints|padstacks|models` -> `library list --kind`; `library validate` ->
    `library check`;
  - `review run|findings|report`, `schematic unconnected`, `analysis erc` ->
    `schematic check` (without `--rules`, which injected its file's entries as
    findings without checking anything);
  - `change validate|preview|apply`, `schematic apply` -> `schematic edit`;
  - `schematic components|pins|query` -> `schematic components`;
    `schematic nets|connectivity|power` -> `schematic nets`;
  - `pcb drc`, `analysis drc`, `analysis results` -> `pcb check`;
  - `pcb info|layers|stackup` -> `pcb info`; `pcb components|footprints|nets|
    tracks|vias|query|zones|keepouts` and `pcb geometry` -> `pcb geometry`;
  - `pcb placement`, `pcb placement-plan` -> `pcb move --file`;
  - `pcb rules`, `constraints list|query|validate|export` -> `pcb rules`;
  - `pcb export`, `manufacturing artifacts|verify` -> `pcb export`;
  - `bom export|group|compare` -> `bom export` (`--group`, `--baseline`);
    `bom validate|missing|duplicates` -> `bom check`.
- The parser is strict: an option a command does not declare, a missing required
  one, a stray positional argument and a malformed value are refused before anything
  runs, with the options the command takes.
- `schematic edit` activates the sheet each operation's part is on, draws a
  connection as a horizontal and a vertical wire between the pins with its label
  over the first segment (it was drawn from the sheet origin), and saves the sheets
  it touched.
- `library build` reads the library's parts first: it makes no placeholder for a
  part the design names from the library, and refuses a placeholder whose number is
  a real part (`-r` would have replaced it). Its dry run needs the adapter.
- `schematic export` takes `--replace`, like every other command that writes a
  file.

### Added

- The project's own design library:
  - `library list` pages its parts, cells, symbols or padstacks, read through the
    stock converters (`PartsDB2HKP`, `CellDB2HKP`, `PadstackDB2HKP`), six at a time,
    into a cache refreshed when a database changes;
  - `library show` gives one part whole (pin map, symbol pins, cell lands and their
    padstacks, what is wrong with it) or one cell and the parts using it;
  - `library check` checks the library: parts against their cells, symbols and pin
    maps, cells against the padstacks, repeated pin names, part numbers in two
    partitions;
  - `library add` adds parts from a parts file -- a symbol (a box with typed pins or
    a built-in kind), a footprint and the pin map -- compares everything with what
    the library holds (identical content is kept, other content under a name is a
    replacement that needs `--dangerous`), imports, and reads the library back;
  - footprints by IPC-7351B from a datasheet's dimensions: chip, molded, gull-wing
    (SOIC, SOP, SOT, QFP), J-lead, QFN/DFN/SON with an exposed pad (paste reduced,
    terminals pulled back to clear it) and through-hole; density levels, omitted
    lead positions, rounded lands, a silkscreen kept off the lands, a pin-1 mark and
    a courtyard. The SOIC-8 and TSSOP-20 lands match KiCad's. Lands may also be given
    one by one, several on one pin number;
  - `library render` draws a part's symbol beside its footprint, from the library or
    from a parts file before it is added.
- A design's symbols may name a library part, `{"LDO": {"part": "TPS7A2033PDBVR"}}`:
  the drawing places the library's own symbol with its part number, from its
  partition, which the draw lists in the `.prj`.
- `schematic edit` deletes a part (with the wires, power symbols and label boxes
  only it used), sets a property, disconnects a pin and renames a labelled net on
  every sheet (resizing its label boxes); each is verified by reading the design
  back.
- `project backup` zips the project folder beside it (closing the project in
  Designer for the moment its database is locked); `project restore` makes the
  folder what a backup holds, after zipping it as it is.
- `pcb info` reads the stackup (each layer's kind, name, thickness and dielectric
  constant), the board's thickness, the net classes with their widths per layer
  and the keepouts.
- `pcb geometry` carries the keepouts and the nets still open; `--refdes` and
  `--nets` narrow it.
- `pcb route --nets` routes only the nets named.
- `pcb check` adds the board rules DRC does not cover: parts unplaced, off the board
  or overlapping, decoupling capacitors far from their IC, connectors far from an
  edge, acute trace corners, open nets. `clean` needs none of the high or medium
  ones.
- `pcb metrics --project` measures the live board.
- `pcb pour --remove` takes a net's pour off a layer. A net routed again once the
  outer pours are in -- after moving its parts -- needs it: with a pour in place the
  router counts the net as connected, and the pads it did not route are open once the
  planes regenerate.


- Sheet sizes `E`, `A2`, `A1` and `A0`, on their landscape borders and pages
  (`VDSHEET_EL_SIZE` 15, `A2L_SIZE` 18 to `A0L_SIZE` 20); an ISO drawing could not grow
  past A3. Their title blocks are not measured yet, and the plan says so:
  `summary.usable_measured` is false for every size but A4 and A3, whose usable area
  runs into the title block.
- `schematic export` reads each page's size and orientation back from the PDF
  (`pages`) and warns about a portrait page, which is a landscape sheet printed
  clipped. The first sign of a wrong page used to be a person opening the file.
- `schematic render --design FILE --output preview.png` draws every planned sheet as
  a PNG before anything is drawn in Designer: the generated symbols, wires, net
  labels, power, ground and no-connect symbols, part attributes and text on the
  sheet frame, with the drawable area dashed and the plan's findings boxed in red.
  The only picture of a draw was a capture of Designer's window after the sheets had
  been wiped. On the recorded 41-part design it shows the collisions below.
- DS-17 measures readability: a text that collides with another text, has a wire or
  a symbol's graphics drawn through it, or lands on another net label's box is an
  issue of the plan, and DS-07 now covers net labels and part attributes that run
  past the drawable area. Nothing checked text: the recorded 41-part design, drawn
  with a clean plan, has a refdes crossed by a ground bar, a `+3V3` on a capacitor's
  refdes, two pin names written over each other and a label off the sheet.
- The planner places the refdes and part number of every part that is not
  two-terminal (IC and connector boxes, test points, holes, transistors), horizontal
  whatever the part's orientation, at the first spot beside the part where they
  collide with nothing. Designer's default put a box's part number under its
  lower-left corner, where the wire from a bottom-edge ground pin runs through any
  part number longer than a few characters. This uses the attribute placement the
  two-terminal parts already had, and the recorded draw of 2026-09-29 shows every
  attribute where the plan put it. The preview's ground and no-connect symbols follow
  the stock files: a `GND` under the ground triangle is text DS-17 now sees.
- `pcb metrics` measures how good a placement and its routing are, from `pcb
  geometry`'s file and without Layout: per net the ratsnest (the shortest tree
  through its pins, as if unrouted) and how often signal airwires cross; parts whose
  extents overlap, leave the board or are not placed; each decoupling capacitor's
  distance to the nearest IC pin on its rail; connectors' distance to the board
  edge; density; trace length per layer, vias and corners sharper than 90 degrees.
  `--output` saves a result and `--baseline` compares with one: every summary value
  comes back before, after and changed. DRC could say a board broke no rule; nothing
  said whether a move made the layout better.
- `library import` names the footprints the converter refused or crashed on:
  `dropped` and `dropped_samples` on each library and `dropped_footprints` in the
  summary. A library that imported without them read as plain success, and a design
  naming one of them got no cell.

### Fixed

- `pcb route --layers` never did what it said: `RoutePass.LayerSelect` takes a
  select type and a layer, and was called with a layer and a flag, so layer 2 read as
  "add", layer 3 as "remove" and the outer layers were refused. Each excluded layer is
  removed now; a net routed with `--layers 1,4` lies on layers 1 and 4 only.
- The parts database stores `Value` as a number with an SI multiplier and anything
  else -- `10k 1%`, `100nF/16V` -- as 0, with no more than a warning: every passive
  `library build` generated had Value 0. Placeholders carry the number their value
  begins with, or no Value; `library add` refuses a value the library cannot keep,
  and converter warnings are reported.
- Two parts sharing a value but drawn with different symbols (or needing different
  cells) made `library build` rename the second part while the drawing kept the
  value, so the packager found no such part and packaged nothing. A value is the part
  number and names one part: the planner and the build refuse the clash.
- `library build --package` reported `ok` when packaging failed.
- `pcb rules` could not find GND or +5V: Constraint Manager's net list leaves out the
  power nets unless asked for them.
- `pcb holes` previewed a diameter the library has no padstack for and failed at the
  first hole; the preview checks the library and names the sizes it has.
- A generated placeholder cell had no reference designator, so the board had none and
  `pcb labels` nothing to move.
- `pcb arrange` labelled a sheet's parts with the wrong zone when the design numbers
  its parts after another sheet.
- `schematic sheets` listed only the sheets open in a window.
- `doctor` failed the Xpedition check whenever the recorded session process was gone.
- A COM failure's message was the raw error tuple; it is the application's own text.


- `schematic draw`'s netlist check walked only the planned nets, so a drawing with a
  template's leftover part wired to a no-connect pin still matched. It now reports
  `extra_nets` (a net the plan never named that holds a planned pin),
  `unplanned_components` (a part the design does not place) and
  `no_connects_joined` (a no-connect pin that shares a net), and `netlist.matches`
  is false when any of them is not empty. A junction the plan asks for with a
  `none` node is not an extra net.
- The planner refuses a reference designator used on two sheets, as it already did
  within one. The expected netlist had put one pin on two nets.
- `pcb export`'s `checks` passed a package that was not what was asked for. A
  requested format that produced nothing is now a problem (an export that wrote
  no file at all passed), as are an output run that did not finish, a copper layer
  the board's layer count needs, and parts the BOM lists but the board has not
  placed (`unplaced`). The copper is checked for the board's layer count: a
  two-layer board's bottom is `EtchLayer2Bottom`, not a missing `EtchLayer4Bottom`,
  its package no longer carries the bottom copper twice, and every inner layer
  needs a file. A negative plane with no draws is a solid plane and is packaged.
  Files an earlier export left in Layout's output folders stay out of the package
  and are listed in `stale`. The README no longer states a 0.254 mm minimum it did
  not read, and says the centroid file gives each part's cell origin.
- `library build` reported `ok` while `cells_missing` named a cell partition the
  library does not have, and then packaged a design whose parts had no footprint.
  It is not `ok` now, `--package` does not run, and a hint names
  `library import`.
- The adapter's default partition is the planner's `PartQuest`, not `Case`.
- An ANSI sheet was drawn on a portrait page. The planner set the page with the plain
  `VdSheetSize` codes, whose ANSI members are portrait pages, under the landscape
  border symbols: a C sheet printed as 1224 x 1584 pt with its right 500 units, the
  title block among them, cut off, and nothing reported it. A, B (the default), C and
  D now use the landscape codes (`VDSHEET_AL_SIZE` 11 to `DL_SIZE` 14), as `borders.ini`
  pairs `csheet.1` with CSHEETL. A4 and A3 keep their plain codes, recorded printing
  landscape. Recorded 2026-09-29: C exports 1584 x 1224 pt and B 1224 x 792 pt, whole
  border and title block on the page. Designer reads a landscape code back as its plain
  size (13 set, 2 read), which the draw had reported as "sheet size did not take" on
  every sheet; that read-back now counts.
- A generated power symbol wrote its net name through its own bar: the name was
  anchored by its upper centre above the bar, so it hung across it, in Designer as
  in the preview. It is anchored by its lower centre now, as the stock `pwr_bar` is.
- A box with pin names on its top or bottom edge wrote them over the names of the
  side pins in the row beside that edge (`VDD` over the first left pin's name). Such
  a box is a row taller now, which moves its side pins; a design that packed parts
  closely may see DS-07 or DS-08 where it did not.
- The example designs plan clean again under DS-17: the pull-up of sheet 3, the
  sensor and its capacitor, and the test points of sheet 4 moved, and the rail's test
  point is turned pin-up under its power symbol.

## [1.0.0] - 2026-09-28

### Added

- `library kicad-import` converts KiCad footprint libraries into cell partitions of a
  project's central library as a guarded command. Its dry run reads only files -- the
  libraries, their footprint counts, the partitions that exist already -- and binds the
  token to that list; the result reports each library in `items[]` with a `summary`. It
  replaces the earlier module entry point, which wrote with no dry run and is gone.
- `--dangerous`, the second gate of CLI-SPEC §15.4, on the writes that destroy work:
  `pcb unroute`, `pcb create --replace` (which archives the layout folder first),
  `pcb route --unroute`,
  `pcb annotate --unroute`, and `pcb arrange` on a board with routing (its dry run
  counts it). Without it the confirmed run is refused with `E_CONFIRMATION_REQUIRED`
  and the token stays unspent. `reference` marks them `dangerous` with `dangerous_when`
  in the `dangerous` permission tier, and the tool is now T2. `library kicad-import`
  joins them when it merges into a partition that exists, and `schematic draw` always: every
  sheet it draws is wiped first, hand edits included. SECURITY.md says why `session stop`
  stays outside the tier.
- `--continue-on-error false` stops `pcb unroute` and `library kicad-import` at the first
  failed item; the rest are listed in `skipped` and `summary` counts the attempted ones. The
  token binds the choice, and other commands refuse the flag.
- `kb add --name NAME --url URL --about TEXT`, `kb list` and `kb remove --name NAME`
  bind a company's knowledge-base documents -- layout rules, drawing conventions, review
  checklists -- on this machine, and `context` lists them. Binding and unbinding are
  writes, behind the usual dry run and token. The agent reads each document with its own
  tools (for a Feishu wiki, lark-cli), every time, and the Skills say when: before work in
  its area. A company rule replaces a bundled default and settles a TBD; verified facts,
  the write safety rules and STOP CHECKPOINTs stand. The CLI stores http(s) links only;
  it never fetches a document, so no document system is special-cased and no credential
  is held.
- A failed `schematic draw` can be resumed (#30). Every sheet ends in a save, so the
  failure now names the sheets it completed (`sheets_drawn`) and the rest
  (`sheets_remaining`), with a hint, and `--sheets 3,4` draws only those; the netlist
  check still covers the whole design, and the sheets left alone come back as
  `sheets_kept`. The token binds the chosen sheets. `--pace` slows a draw for someone
  watching Designer, as it does `pcb trace`. On a 4-sheet design interrupted on
  sheet 3, the resume drew 132 of 207 operations and the netlist matched.
- `--timeout SECONDS` gives a native read -- the snapshot behind review, bom,
  schematic, pcb, library and project reads -- the time a large design needs (#29).
  The default is 120 s, up from a fixed 30 s that a 41-part design with an 8 MB
  central library outran; a timeout names the time it had and the flag to raise.
- `schematic draw` notes when the partition's parts database does not exist. Designer
  draws a part instance's value itself when the library has no part for it -- twice, the
  horizontal copy landing on the reference designator -- and its own "Text alignment"
  check then fires on every such part, which was the only thing between a generated
  schematic and a clean review. `library build --package` clears both, so the draw says
  so instead of leaving the findings to be chased.

- DS-16 reports a top or bottom edge whose pin names are wider than the pin pitch. Those
  names are drawn horizontally inside the body at the pitch, and at `CHAR_WIDTH` per
  character over 10 units about 1.6 characters fit, so a four-ground bottom edge rendered
  as `AGNBPGNBCNE2AD` -- four names written over each other. Shortening them does not
  help. The check names the pins and says where they can go instead; drawing them
  readably needs a rotated name row, which is not done here.

- DS-15 checks, when the plan is built, that nothing belonging to one net lands on the
  free end of another net's wire: a ground symbol reaches 40 units past its own end, four
  slots at the 10-unit pin pitch, and a boxed label runs `CHAR_WIDTH` per character
  sideways from a top-edge pin across its neighbours. Designer refuses those draws with
  6031 and 6035, and a box that lands exactly on an end is worse -- it draws, merging two
  nets, with nothing in the output to say so. DS-07 and DS-08 check symbol extents and
  spacing; none of this was checked, so three draws failed on plans reported clean.
- A failed draw operation reports the sheet it fell on, the net or symbol it touched,
  and how many operations had already been applied. An index alone was not locatable:
  resolving "operation 269" meant importing the planner, rebuilding the plan and counting
  `open_sheet` records, once per attempt, at several minutes per attempt.
- `schematic draw --dry-run` publishes the planned `operations`, each carrying its index
  and the sheet it lands on, so a reported index can be looked up and a plan inspected
  without importing the planner. Trim it with `--fields` on a large plan.
- The native adapter streams draw progress to stderr as it runs, and `--quiet` now does
  what CLI-SPEC §4 says it does and suppresses it. The adapter's stderr was captured, so
  a draw of several hundred operations printed nothing at all until it returned.
- `doctor` reports a `native_session` check naming which Xpedition application is
  attached right now. `native_xpedition` passes as soon as an adapter is configured,
  which says nothing about what is running, and Layout and Designer are separate
  products with separate COM classes: `pcb *` reaches Layout while `schematic *` and
  `agent snapshot` reach Designer. Nothing surfaced that split, so the first sign of
  it was a task command failing. The probe is skipped when the native backend is not
  ready, and a probe that fails cannot fail `doctor`.
- Failing to attach to a session now names the application, the commands it serves,
  and the `session start --kind pcb|schematic` that starts it, instead of reporting
  only the ProgIDs that were tried.
- Selected-origin placement tasks: offline `pcb placement-plan` and guarded native
  `pcb placement` for explicit translate, rotation about an origin, alignment to
  an anchor, and equal-origin-spacing distribution. Input JSON Schemas are exposed
  by reference; native identity, units, side and protection are read, not guessed.
- One preview/confirmation for a serial batch, per-item and final target read-back,
  native placement DRC enable/restore checks, stop-on-uncertainty results and one
  save after verified completion. No routing deletion or unselected placement is
  requested. This is not route repair, all-or-none rollback or a global write lock.
- Scoped reference discovery by exact command, top-level domain or existing
  output-schema name. A command slice retains its success and dry-run schemas,
  error tables and permission metadata; the full catalog remains the default.
  Selectors are declared from one source and rejected on unrelated commands.
- Read-only `schematic pin-plan` and `schematic pin-check` compare exact CSV
  pin/net assignments with supplied snapshots. Plans identify noop/connect/reassign
  and shared-net review needs. Missing or conflicting observations never count as
  verified. No native calls, automatic ChangeSets, write tokens or modifications.
- Bounded pages and peer samples, full-input assessment before paging, file hashes,
  strict CSV/JSON validation and machine-readable input/observation contracts.
  Source freshness and live electrical correctness are explicitly unverified.
- `system api-inventory` reads trusted standalone COM type-library metadata on
  Windows without application activation, method invocation or registration.
  Type headers and selected member pages report names, identities and raw type
  descriptors; they do not become callable CLI capabilities or inferred schemas.
- Exact type selection, bounded pages and descriptor depth, incomplete-read
  reporting, source hashes and safe projection. DLL/EXE/URL/UNC input, backend
  selection and write flags are rejected. Xpedition semantics remain untested.
- A Windows smoke workflow exercises the real pywin32 loader against Windows'
  standard OLE type library, without Xpedition. It is not a licensed-native test.
- Hand routing through the CLI — the person's own layout, not the autorouter's:
  `pcb geometry` (the board as data: outline, pads with nets, traces with widths, vias,
  planes, holes, silkscreen, and every part with its origin, extents and pins),
  `pcb trace` (a trace from `--net`, `--layer`, `--width`, `--points "x,y x,y …"`, or a
  whole plan file of traces and vias with `--file`; `Document.PutTrace`), `pcb via`
  (`Document.PutVia`, the board's via padstack or the central library's), `pcb unroute`
  (the routing of `--nets`, of `--all`, or one trace / via at `--at x,y [--layer N]`),
  `pcb move` (one part to a position and rotation with the placement DRC on) and
  `pcb labels` (every silkscreen designator to a free spot beside its part). The
  preview of `pcb trace` names the nearest pin of each trace end; the result gives the
  open count of every touched net before and after, and Layout's online DRC refuses an
  item that violates a clearance while the rest go on.
- `pcb show --top-view`: a display scheme (`Loc: Top View`, written once into the
  board's `Config` from `All On.dcs`) that shows Layout's own screen the way
  `pcb render` draws the board — plane copper filled, the inner layers off, the
  assembly texts and the drill drawing off. The stock `Loc: All On` draws the
  pours as outlines only and every layer and text at once, which is why a board
  with pours looked bare and crowded on screen. Layout lists a new scheme only
  after the board reopens, so the first use closes and reopens it.
- `xpedition_cli.routing_plan`: the offline checker for a plan against `pcb geometry`'s
  file (angles, clearances to pads, traces and vias of other nets, a via pad touching
  any pad — Layout refuses those, its own net included), `PlanBuilder` for writing a
  plan pin by pin, and `pcb stitch`, the plan for a ground via beside every
  surface-mount ground pad where the checker finds room. `pcb trace --geometry` runs
  the checker before Layout is touched and refuses a failing plan (`--dangerous` to let
  Layout judge).

- Added `pcb rules` (adapter method `net_rules`): a net class with its trace widths on
  every layer, through Constraint Manager's automation server (`ConstraintsAuto`, the
  board loaded in its layout context), which Layout's own automation cannot write.
  `--class POWER --nets VBAT,+3V3 --width 0.5` sets the typical width, `--min` and
  `--expansion` the other two (80 % and 120 % of it by default); Layout re-reads the
  constraints through `ProjectIntegration.SynchCES` and the result says
  `seen_by_layout`. The next `pcb route --unroute` routes the class at that width.
- Added `pcb render` (adapter method `render_board`, drawing in
  `xpedition_cli.board_render`): a PNG of the board from its geometry — outline with its
  arcs, pads, traces at their widths, vias, generated planes with their cutouts, drills,
  silkscreen graphics and texts — in KiCad's editor colours, top or bottom side. It
  needs no screen, so it works with the desktop locked, where `pcb show --output`
  captures a black window.
- `pcb arrange` places the parts of a cluster beside the IC pin they connect to: the
  survey now reads every pin's offset, a satellite goes to the side of the IC where its
  pin stands, level with it, turned so its own connecting pad faces the pin (a series
  resistor at its signal pin, a decoupling capacitor at the supply pin, not the ground
  one); a side may overhang the IC by about one designator and continues in a further
  column or row when it fills up, and a row above or below the IC steps past a column
  that reaches the corner.
- Added `pcb export`: the fabrication package (adapter method `manufacturing_output`,
  pure parts in `xpedition_cli.fab_package`). Layout has no automation call for ODB++,
  Gerber or NC drill, so their Output-menu dialogs (33017, 33016, 33018) are run with
  the helper thread pressing OK. The dialogs keep their settings in memory while the
  board is open and rewrite `Config/ODBSetup.ocf` and `Config/PlotSetup.gpf` on OK, so
  the setups are patched with the board closed and it is opened again: the ODB++ job
  gains its drill spans and board outline (`d_1_4 INCLUDE YES`, `BOARD_OUTLINE YES`),
  the Gerber set gains the cells' silkscreen (`..CellType` / `..CellItemsSide` /
  `...CellItem SilkscreenOutline` + `SilkscreenReferenceDesignator`; a `..BoardItem
  SilkscreenTop` writes an empty file) and the board outline. The outputs are read back
  (draw counts, drill hits, ODB++ layer matrix) and copied into a folder with fab-friendly
  names — empty files and Layout's duplicate outer-copper files stay behind — plus the
  ODB++ job as a zip, a centroid file, a BOM grouped by part number and footprint, a
  README for the board house in Chinese and a manifest with the checks. The dry run is
  offline (it reads the setups only).

- `pcb arrange` lays parts out the way a hand layout reads: every part keeps room above
  it for its reference designator (Layout's extents stop at the placement outline, and
  a 1 mm `R302` is wider than an 0603), the parts of one column or row share one pitch
  (the largest of them plus the gap) with the first parts nearest the anchor, centres
  snap to a 0.5 mm grid without ever moving outward past a margin, connectors on the
  left edge turn by 270° and on the right by 90° so their designators face inward, a
  new row keeps room for its sheet label, and once the corners hold mounting holes a
  7 mm square in each corner stays empty (`corner_keepout`). A plan with parts past the
  outline is refused with `E_VALIDATION` before anything moves (placing them lands on
  the parked parts and Layout stops halfway with a DRC violation).
- `pcb route --layers 1,4` keeps the traces on the outer layers: `RoutePass.LayerSelect`
  takes the inner layers only (the outer two are always the router's and count as
  invalid parameters), so the list decides which inner layers join. The result reports
  `traces_by_layer`. `--unroute` deletes every trace and via first, because the passes
  only work on open connections.
- `pcb outline --radius R` rounds the corners: Layout's points array takes each corner
  as the arc's start, its centre with a *negative* radius and its end (a positive
  radius draws the other 270°); the route border is rounded by `R − 0.3` and the
  manufacturing outline like the board. `Geometry.GetRect*` only answers for a plain
  rectangle and calls a rounded outline incorrect geometry, so the board rectangle is
  now read from the outline's extents.
- `pcb pour` follows the outline: the shape is inset from a rounded outline with the
  corner radius less the margin (the radius is read back from the outline's points
  array, which comes in thousandths of an inch), and `--replace` removes the shape the
  board already has for that net and layer, because after `pcb outline` changed the
  board the old rectangle stuck out past the rounded corners.
- Added `pcb holes`: a non-plated mounting hole in each corner of the outline (2.2 mm,
  3.5 mm from both edges by default) through `Document.PutMountingHoleEx` with the
  central library's `MH-C<diameter>-NONPLATED` padstack, which the library build and
  the KiCad import both create (adapter method `mounting_holes`). Corners that already
  hold a hole are left alone; `--replace` removes every existing hole first, for a
  board whose outline grew.
- Added KiCad's footprint library as a source of real cells. `xpedition_cli.kicad_footprints`
  reads `.kicad_mod` files (pads of every shape, drills and slots, silkscreen / fabrication
  / courtyard graphics, the reference-designator text) into the library model, and the
  adapter method `kicad_import` (run by `library kicad-import`) turns every `.pretty`
  folder into one cell partition of the project's
  central library through the stock converters — 155 libraries, about fifteen thousand
  cells, in a quarter of an hour. A design names a footprint as its package with a
  `kicad:` key (`"packages": {"RES": "kicad:Resistor_SMD:R_0603_1608Metric"}`), and
  `library build` then writes parts that reference the cell and registers its partition in
  the project's `LIST 2dCellLibraries` (`cell_partitions`). The library model gained
  radius-corner rectangles, oblongs, slotted holes, drawn outlines (`Graphic`), mounting
  holes inside package cells and the placed reference designator (`RefDesText`, on the
  silkscreen and on the assembly layer), which is what keeps the refdes at the
  footprint's 1 mm instead of Layout's default. Cell names are
  limited to 64 characters by `HKP2CellDB`, so longer KiCad names are cut and tagged with a
  hash; a partition the converter refuses a cell of is imported again without it.
  `examples/demo-sensor-board-kicad.json` is the example design on KiCad footprints.

- Added `pcb create`: the project's board from a layout template through `JobWizard
  -createnew` (adapter method `pcb_create`). The wizard's command line takes the first
  design in the `.prj`'s `LIST Designs` and fails with "failed to get design information"
  when that is a schematic node, so the board design is listed first; the template is
  copied from the installation's stock set into the central library when the library
  lacks it; and `LIST 2dCellLibraries` gains the cell partition of every parts partition
  the project lists, without which Layout's Database Load finds no cells. `library build`
  registers the same list.
- Added `pcb annotate`: forward annotation through Layout's `Document.ProjectIntegration`
  (adapter method `forward_annotate`), which has no type information, so `ForwardAnnotate`
  runs on attribute access; the counts and verdict come from `PCB/LogFiles/
  ForwardAnnotation.txt`, a board already in synch is reported without running (Layout
  calls that a failure), and the board is saved afterwards.
- `session open --kind layout` and `pcb info` accept the `.prj`: the board comes from the
  design's `PCBDesignPath`. While a board opens, a helper thread answers Layout's own
  questions through UI Automation (`win_dialogs.answer_prompts`, needs `pywinauto`): a
  stale lock from a killed session is opened, the recovery box loads the database the
  user last saved, the offer to forward-annotate is declined, and every answer is
  reported as `prompts`. A missing Layout is started through its launcher and waited
  for, because an instance that COM activation starts never registers its automation
  object. The adapter loads Layout's type library for its enumerations but keeps the
  objects late-bound, and reads `Components` and `Nets` without filters: both wrappers
  reject the filter arguments.
- `xpedition_cli.project_file`: pure-text reading and editing of a `.prj` (designs and
  their sections, list entries, list creation, design order).
- Designer's `OpenProject` is wrapped the same way: switching projects while sheets are
  open asks whether to close them, and the call waits for the answer; the adapter now
  answers Yes.
- Added `pcb arrange`: a first placement of the board's parts that follows the
  connections (adapter method `arrange_components`, planner `xpedition_cli.board_layout`):
  one cluster per IC with the parts that connect to it around it, filled from the centre
  outward so decoupling capacitors sit nearest, clusters in rows by sheet, connectors
  turned by 90° on the side edge next to their cluster, test points along the bottom
  edge, and a silkscreen zone label per sheet from the design file's `zone`
  (`--design`). Layout has no extents for an unplaced part and drops its pins from
  `Net.Pins`, so the dry run measures each part alone at the board's centre, parks the
  unplaced ones in a grid below the outline, reads the nets, unplaces them again and
  binds the token to the plan's digest; the confirmed run recomputes the plan, refuses a
  changed board, lifts every planned part (Layout calls a part placed onto another one,
  or its own old footprint, a DRC violation), places through `Component.Place`, replaces
  earlier zone labels and saves.
- Added `pcb outline`: the board outline as a width × height millimetre rectangle from
  the origin (`PutBoardOutline`), with the route border 0.3 mm inside it and the
  manufacturing outline on it, because the template's copies keep their old size
  otherwise (adapter method `board_outline`).
- Added `pcb pour`: a plane shape for a net on a layer, inset from the outline
  (`PutPlaneShape`; GND on layer 2 by default), refusing a second shape for the same net
  and layer (adapter method `plane_pour`). The optional component argument of these
  calls must be passed as `None`: win32com cannot turn the type library's default `0`
  into an object pointer, and the call fails with "The Python instance can not be
  converted to a COM object".
- `pcb show` also fits the board outline into the view (`ActiveView.SetExtentsToBoard`,
  once the toolbar's `VIEW_FITBOARD` button), so a resized outline fills the view.
- Added `pcb route`: Layout's batch autorouter through `Document.NewRoutePass`
  (adapter method `route_board`). `--passes` names the passes and their effort range,
  `route:1-5,viamin:1-3,smooth:1-3` by default (also `fanout`, `novia`, `spread`,
  `expand`, `removehangers`); each pass runs over every net with `Go` and the result
  reports the routed nets, open connections, traces and vias after each one and the
  nets left open. The example board routes completely in three seconds.
- Added `pcb create --replace`: closes the board in Layout, removes the `PCB` folder,
  clears the board keys in the `.prj` and creates the board again. That is the way to
  pick up a cell that changed in the library: forward annotation never changes the cell
  of a component that already exists on the board, placed or not, and JobWizard's own
  `-deletePCB` fails on these projects.
- `pcb annotate` is now robust: the board is closed and opened again first (a Layout
  session older than Designer's current project session fails the packaging phase),
  Layout's own "annotate now?" prompt is answered Yes, the explicit call runs with
  Designer's project closed (it fails with it open) and is retried once (the first call
  on a freshly opened board fails, the second succeeds), Designer's project is reopened
  afterwards, and `--unroute` deletes traces and vias first (Layout in its preventive
  DRC mode cannot break traces back).
- `pcb arrange` deletes the board's traces and vias before re-placing (a part placed
  onto a trace is a DRC violation, and a placement change makes every trace wrong) and
  reports what it removed.
- Placeholder IC packages are routable under the stock rules: `SOIC` (1.27 mm pitch)
  up to 16 pins and a 0.8 mm pitch `TSSOP` beyond, because a 0.65 mm pitch cannot be
  reached with 0.254 mm traces and clearances (the template's read-only defaults).
- Board snapshots include traces and vias (net and layer), so `pcb info` counts them.
- Added `pcb drc`: Layout's Batch DRC (adapter method `batch_drc`). There is no
  automation call and its driver refuses a command line, but the menu command runs
  through `Gui.ProcessCommand(32769)`; its dialog is answered from the helper thread, the
  run is waited out, and every hazard comes back from `Document.GetHazards` with its
  type name, description, position, the objects involved and, for clearance checks, the
  required and actual distance. `--no-run` only reads, `--online` adds the online DRC's
  hazards. The first run found the dual-row placeholder pads overlapping (pad long side
  along the pitch), fixed in `library_hkp`, and the ground vias dangling, fixed below.
- `pcb pour` switches the net's plane assignment from Draft to Dynamic so Layout
  generates the copper (`PlaneAssignment.PlaneDataState`), and `pcb route` regenerates
  every plane after its passes: vias the router adds only tie in after a regeneration.
  The battery board then passes Batch DRC except for vias under the IC bodies.
- The prompt answerer thread no longer presses the default button of every `#32770`
  dialog: doing so ended the packager's progress box during forward annotation, which
  was the whole reason `pcb annotate` kept failing "in the packaging phase" while the
  same call from a plain script succeeded. Annotation now succeeds at the first attempt
  on a fresh board; the retry stays, with short waits.
- Window captures that are all black (a locked desktop) are reported with `blank`.
- Added `pcb show` (adapter method `show_board`): the board window to the front under a
  display scheme that shows the parts, default `Loc: All On`, with an optional PNG
  capture like `schematic show`. The stock templates open with `Loc: Assembly Bottom`,
  under which a board of top-side parts looks empty. Layout lists the scheme names
  (`Document.DisplaySchemes`); `pcb show` switches them through the view's display
  control (below), with the toolbar combo driven through UI Automation as the fallback,
  whose popup exposes only the rows on screen and is paged with the keyboard.
- Board component records carry `x`/`y` in millimetres with `unit`, whatever the
  document's current unit (the stock template counts in mils), and `side` from `Side`
  (1 top, 512 bottom) instead of the wrong layer test.
- Added `library build`: placeholder padstacks, cells and parts for a design, generated as
  HKP text (`xpedition_cli.library_hkp`) and imported into the project's central library
  through the stock `HKP2PadstackDB`, `HKP2CellDB` and `HKP2PartsDB` converters (adapter
  method `library_import`); `--package` then runs the packager, which now replaces its cached
  parts (`-Replace`) so a rebuilt library is seen. Symbols carry pin-label records and default
  to the stock `PartQuest` partition, the one every template library registers.
- Added `project init --template` on the NativeBackend: a new Xpedition project copied from a
  template project (adapter method `clone_project`), with an ASCII-path guard because Designer
  cannot load new symbol files from a folder whose path has other characters.
- Added built-in `NTC`, `NMOS`, `PMOS`, `TP` (test point) and `HOLE` (mounting hole) symbol
  kinds to the schematic planner and a four-sheet example design (a
  battery-compartment temperature monitor with pull-ups, series resistors, test points and
  thermal and interface notes).
- The I2C pull-up review rule now accepts a pull-up one series resistor away, so a host
  connector behind 33 Ω series resistors is not reported.
- Generated passive, MOSFET and test-point symbols carry `ANALOG` pins, so Designer's
  BI-to-power and BI-to-ground checks stay quiet on a correct design.
- Added `project tree`, embedded runtime changelog data, and the ExchangeBackend boundary.
- Added MockBackend read commands for schematic, PCB, constraints, analysis, manufacturing, and library data.
- Added schematic power-net, interface, and project-model support.
- Added read-only Agent integration commands and an NDJSON stdio server.
- Added ChangeSet history and backup rollback with the same dry-run/confirm gate.
- Added project diff against the latest automatic backup.
- Added MockBackend session status/log inspection with explicit native lifecycle boundaries.
- Added JSON/CSV/BOM ExchangeBackend inspection and guarded import.
- Added IPC-2581 XML component, net and pin-connection inspection/import.
- Added library validation and review findings/report read aliases.
- Added `schematic apply` as the unified guarded ChangeSet write entry point.
- Added guarded MockBackend project initialization.
- Added MCP JSON-RPC transport for read-only Agent tools.
- Added MCP protocol version negotiation and tool output schemas.
- Updated MCP negotiation to prefer protocol version 2025-11-25 while accepting older clients.
- Added deterministic MockBackend ERC/DRC/DFM analysis runs.
- Added BOM normalization, grouping, variant/missing/duplicate checks, validation and comparison.
- Added the optional Windows COM adapter for the public Xpedition automation API, including
  installation discovery, COM-registration probing, structured health errors, session lifecycle,
  PCB and Designer snapshots, and guarded component/net move/place operations.

- Added a `package` adapter method that runs forward annotation headless. Designer's
  `Package Design for Layout` launches `packagerui.exe`, a separate GUI process that waits
  for a human; `package.exe` does the same work as a console program. It has to be started
  through the launcher in `common/win64/bin`, because the real binary under `wg/win64/bin`
  cannot initialise its Qt platform plugin on its own and puts up a message box instead.
  Errors and the verdict are read back from `<project>/Integration/PartPkg.log`.
- Added the Skill's drawing conventions: `reference/schematic-conventions.md` and
  `reference/pcb-conventions.md`, with the non-negotiable subset in `SKILL.md` and a matching
  eval prompt. The sheet unit was measured rather than assumed: one sheet unit is 10 mil, the
  100 mil grid is 10 units, `V 53` symbol files share the sheet unit and `V 54` files (every
  stock symbol) are in 10 nm. Each rule is marked verified, industry default or pending a
  company decision; the PCB rules are defaults only.
- Added `schematic export`: renders a Designer project to a new PDF through the stock
  `sch2pdf` console program (adapter method `export_pdf`) and reports the sheets it printed.
  Designer's own `Generate PDF` is a dialog; `sch2pdf` runs headless while the project is
  open and prints only what lies inside each sheet's border. An existing output file is never
  replaced.
- Added `xpedition_cli.symbols`, a generator for `V 53` Designer symbols: IEC two-terminal
  shapes, pin-grouped boxes and one power symbol per net, all on the 10-unit grid. Power
  symbols are type 4 (`Y 4`) like the stock Globals symbols — a type-1 symbol carrying a
  `NETNAME` attribute names no net — and Designer keeps the definition of a symbol it has
  placed, so a changed symbol needs a new name.
- Added `schematic draw`: plans a whole schematic from a compact design description
  (`xpedition_cli.schematic_layout`: IC blocks with a treatment per pin, vertical ladders and
  horizontal chains of two-terminal parts between power, ground and labelled nodes, titles,
  notes, overview boxes) and draws it through the adapter method `draw` after the usual
  dry-run/confirm gate. The dry run is pure Python and reports the sheets, parts, netlist and
  convention issues; the confirmed run wipes and redraws the listed sheets, sets them to the
  requested size, writes content-named symbol files, reopens the project and compares every
  net read back with the plan. `examples/demo-sensor-board.json` is the published example.
- Added `xpedition_cli.win_dialogs`, the modal-dialog dismisser the probes used, so the
  adapter can settle after project and sheet operations without a human.
- Added `schematic show`: activates a sheet, fits it and brings Designer's window to the
  front so a person can look at what was drawn, with an optional PNG capture of the window
  for the agent's own check (no external imaging library; the PNG is encoded in-process).
  `schematic draw` and `schematic show` were then exercised end to end through the CLI on
  a four-sheet example design: plan, confirm token, 223 operations, netlist match, window
  in front.
- Added schematic review on live designs. The native snapshot now carries every instance
  attribute (part number, value, package), the sheet a part sits on (from its UID), each
  pin's net through `LogicalNetName` with Designer's `$<sheet>N<id>` id for unlabelled
  nets, and a `no_connect` flag for pins under a no-connect mark. Nets are merged across
  sheets, so multi-sheet designs no longer report every rail as a duplicate, `bom export`
  returns part numbers and values, and `schematic unconnected` no longer lists marked pins
  or bare junctions.
- Added netlist rules to `review run` (`cli/open-pin`, `single-pin-net`, `unnamed-net`,
  `missing-part-number`, `decoupling`, `i2c-pullup`, `net-name`, `refdes-prefix`) and, on the
  NativeBackend, Designer's own verification: the adapter method `verify` runs `Full
  Verification` (34155) and parses `LogFiles/vdrc.log` and `grc.log` into findings tagged
  `xpedition/verify:<rule>` and `xpedition/grc:<check>`. `RunDesignIntegrityChecks` is only
  a database integrity test and is not used for this.
- MockBackend project model and normalized design snapshots.
- Capability registry with explicit NativeBackend and ExchangeBackend boundaries.
- ChangeSet validation, dry-run previews, operation-bound confirmation tokens, atomic apply, backup, and post-write verification.
- Deterministic review and normalized BOM commands.
- JSON-first CLI contract, Skill, FCC guard, CI and npm/PyInstaller packaging seed.

### Changed

- Plural flags -- `--nets`, `--sheets`, `--layers`, `--formats`, `--libraries`,
  `--fields` -- repeat as well as take a comma list, and `reference` marks them
  `multiple`. A singular flag given twice with different values is a usage error; it
  used to keep the last one silently.
- `pcb unroute` reports each net (or the point) in `items[]` with a `summary`, a net
  named twice counts once, and an empty `--nets` is a validation error.
- A read whose `--project` does not exist is `E_NOT_FOUND`, not an empty design.
  ChangeSet writes and imports still create their target.
- `doctor` and `session status` only look at a running Layout: they do not switch on its
  dialog suppression and single-threaded mode, which the commands that drive Layout set
  when they attach.
- `live_smoke_status` uses the spec's values: `missing`, with `reason` saying what the
  recorded runs cover.
- The release binary's dependencies are locked -- `requirements.txt` (Pillow 12.3.0,
  which fixes the 25 advisories against 12.2.0) and `requirements-build.txt`
  (PyInstaller) -- and CI and the release workflow both audit them; the release also
  runs `npm audit` and the full spec check, as CI does.
- The install instructions start from the repository with pip, which works before any
  npm release.
- The bundled conventions keep to general defaults; a company's own practice belongs in
  a bound knowledge-base document. Numbering reference designators per sheet in
  hundreds, and the text-size ladder, are now defaults.
- The Skill is now three, on spec v1.6.3's rules for several Skills (SKILL-SPEC §7).
  `xpedition-cli` stays the entry Skill: install, sessions, projects and ChangeSets, the
  write recipe, errors and security. `xpedition-schematic` carries the schematic in
  Designer: drawing conventions, design format, packaging after a draw, review, export,
  pin assignment and footprint mapping. `xpedition-pcb` carries the board in Layout,
  from creating it to the fabrication package, with its conventions, placement,
  hand-routing and fabrication references. Both read the entry Skill first. Three writes
  that discard work or cannot be previewed are now STOP CHECKPOINTs: a confirmed
  `pcb arrange` deletes all routing, `schematic draw` wipes the sheets it redraws, and
  `library kicad-import` overwrites the cells of a partition that exists. The same
  `npx skills add fatecannotbealtered/xpedition-cli -y -g` installs all three.
- The vendored spec is v1.6.3.
- Runtime readiness is beta. `reference`'s `release_readiness.reason` says what the
  recorded runs cover, the 2026-09-19 placement smoke included, and what keeps the
  level short of stable.
- Routing-plan checks skip existing-existing segment and via pairs before
  iteration and avoid repeated tail-list copies. The geometric predicates and
  finding order are unchanged. Incremental edits no longer pay a quadratic
  existing-board pair scan; new-new comparisons still require further indexing.
- Bounded local query pages stop after one matching lookahead record rather than
  filtering every record. Unfiltered sequence reads slice directly; agent and
  library queries no longer re-filter a materialized result. Matching and paging
  semantics remain compatible, including zero limits and clamped offsets.
  This is not native query pushdown or a COM-session optimization.
- `agent capabilities` no longer loads a project or requires the selected native
  backend to be operational. Capability discovery now matches its streaming
  counterpart and remains available when a design path is absent or malformed.
- `analysis run --backend native_xpedition` explicitly rejects the unsupported
  combination before native access instead of running Mock checks on a native
  snapshot. Native stored-result reads still run, but the native snapshot holds no
  analysis results, so they return an empty list. This intentionally
  tightens backend selection; it does not implement a native analysis engine.
- Confirmation consumption now serializes ledger check/update across cooperating
  processes and threads, atomically replaces the ledger and rechecks expiry after
  locking. Concurrent first-use secret creation no longer races; corrupt secrets
  are rejected rather than silently reused or rotated.
- Reject alternate base64 token representations that could evade a consumed-token
  fingerprint, non-ASCII signatures and nonfinite or malformed signed expiry data.
- Preserve the pinned spec's storage-failure degradation, with explicit secret-free
  stderr warnings. Failed replacement preserves the old ledger; unavailable lock
  storage never triggers an unlocked ledger rewrite. Lock contention is a conflict,
  not degradation. This is not a project write lock or exactly-once native execution.
- The published example design is `examples/demo-sensor-board.json` (and its
  `-kicad` variant with a KiCad footprint map): a generic 5 V / 3V3 sensor board that
  exercises every block kind the planner supports. It replaces the two product-shaped
  designs that were here before.
- `pcb create --replace` archives the design's existing layout folder to
  `PCB-backup-<timestamp>.zip` beside the project before deleting it, and returns the
  archive's path in `backup`. Deleting a board was the one irreversible operation in
  the tool: the folder holds the placement, the routing, the pours and the output
  setups, and none of that lives anywhere else. Its dry-run preview now says so —
  the old `blast_radius` text described only the folder being created, while the
  `changes` list beside it already said `remove_layout_data`.
- `pcb trace`, `pcb via` and `pcb arrange` take `--pace SECONDS`: a wait between placed
  items (0 to 10 s) so a person at Layout's screen watches the parts land and the
  routing grow one item at a time; 0, the default, is as fast as Layout takes it.
- `pcb show` switches the display scheme through Layout's automation
  (`ActiveView.DisplayControl.LoadScheme`, read back from `DisplayControl.Name`) and fits
  the board with `ActiveView.SetExtentsToBoard`; the toolbar combo and button through UI
  Automation remain the fallback, which had started to come back empty on a Layout
  started by `pcb annotate`.
- `pcb rules` also sets the widths of a class that exists, `(Default)` included: the
  stock class allows exactly one width (minimum = typical = expansion = 0.254 mm), so a
  0.3 mm ground stub was a TraceWidths hazard until its expansion width was raised.
- A board whose vias were all deleted lists no via padstack; `pcb via` then pulls the
  central library's first via padstack (`Document.PutPadstack`, `026VIA` here).
- `pcb pour` lays the plane shape with `bRouteObstruct` false: an obstructing shape
  blocks the autorouter on that layer entirely and, laid over traces that are already
  routed, is refused as a DRC violation. The copper flows around the traces when the
  plane data is generated. The board recipe pours the outer layers after routing.
- `pcb route` routes a second round after regenerating the planes when a net is still
  open: a pin can be cut off once the copper flows around the new traces.
- `pcb drc` separates `errors` from `warnings` (`ViasUnderParts` — a via under a
  surface-mount body, normal with tented vias) and says `passes` when there are no
  errors; `clean` still means no hazards at all.
- The placement planner packs parts 0.6 mm apart (courtyards carry their own margin),
  starts a new row of clusters for every sheet so the sheet labels sit over their own
  clusters, and moves a connector to the other edge when one edge cannot hold its
  column.
- The converter logs are read more carefully: every `HKP2*` log says "checking for file
  format errors… none found", so only a leading or labelled error word, "cannot add" and
  "N errors" count as failures (the old check on the bare word flagged nothing in the
  Chinese logs and would have flagged everything in English ones).
- `session start`, `attach`, `open` and `stop` are declared by `reference` and pick their
  target application with `--kind pcb|layout|schematic|designer`. Three of them were already
  implemented but undeclared, so no agent reading `reference` could find them, and every one
  of them silently targeted Layout — Designer was unreachable. `session stop` quits the
  application, so it now goes through the dry-run/confirm gate; `native_session_lifecycle` is
  no longer reported as a planned domain.
- The adapter suppresses Layout's modal dialogs on every attach (`SuppressTrivialDialogs`,
  `SuppressNotepadDialogs`, `SuppressVariantDataOutOfDateDialog`, `SingleThreaded`). An unattended agent cannot answer a
  message box, so a dialog raised mid-operation turns a COM call into a hang. Dialogs raised
  while the application is still starting up happen before automation exists and are out of
  reach — attach to an already-running instance to avoid them.
- Hardened output redaction, operation-bound confirmation scopes, pagination metadata, and native capability reporting.
- NativeBackend now discovers the installed adapter, reports the actual COM-registration state,
  and routes verified reads and ChangeSet writes through the adapter.

### Fixed

- The plan-time checks for a covered wire end and for unreadable edge pin names reported
  as DS-09 and DS-10, ids the checklist gives to the refdes-prefix and part-number
  checks, so a reviewer looking an issue up found a different rule. They are DS-15 and
  DS-16 now, with a row each in the checklist, and a test holds every id the planner
  emits to a checklist row that names that dry-run issue.
- `changelog` returned only part of a release's entries: a release that repeats a heading
  kept only its first section, and an entry lost its continuation lines.
- `manufacturing bom` ignored `--limit`, `agent review` ignored `--limit` and
  `--offset`, and six query commands paged without declaring it in `reference`.
- Design text holding a double quote or a line break could write records of its own
  into the generated HKP library files; it is refused now. The same holds for symbol
  files, and a KiCad footprint whose text cannot be quoted is skipped with an issue.
- A symbol name or a symbol partition in a design file named a file path:
  `../../../ESCAPE` would have been written outside the library's `sym` folder, and the
  partition also into the `.prj`. Both are plain names now, and the adapter writes a symbol
  file only inside its folder.
- `--limit` and `--offset` on a command that does not page were ignored -- `bom validate
  --limit 1` returned every issue -- and are refused now. Review pages carry `count`, and
  every paged command declares its `default_sort`.
- `doctor` suggested COM registration when the configured adapter file was missing; it
  now names the fix for the reason the backend gives.
- `pcb trace --dangerous` and `pcb via --dangerous`, which skip the offline clearance
  check, were refused by a global check and could not be used.
- A pip install that is not editable -- the one the README now leads with -- answered
  `reference` with `E_UNKNOWN` and `changelog` with nothing: the wheel held neither
  contract/contract.json nor CHANGELOG.md. `setup.py` now carries both into the package,
  and neither command falls back to a file in the working directory, which would be
  another project's.
- The tests run against their own configuration directory; some had been writing audit
  records into the developer's `~/.xpedition-cli`.
- README said the release level was `stable` (it is `beta`) and listed a
  `schematic plan` command that does not exist.
- A KiCad footprint that gives one number to several lands keeps them all (#28): each
  becomes a pad of that pin, and forward annotation puts them on its net -- measured
  with a 2x2 mm TDFN MOSFET whose drain owns four leads and the paddle. Only a pad
  inside a larger one of its number (a thermal via) is dropped, now reported as
  `inside_same_number`; two overlapping rectangles make an L-shaped land.
- A running Designer read as absent after a temp cleaner emptied win32com's
  generated-wrapper cache (`has no attribute 'CLSIDToClassMap'`). The adapter
  removes such entries before it attaches, and an attach failure now says what
  failed instead of only "not found". Designer is bound through its generated
  wrapper, created if missing: late-bound, `project init` failed on
  `Documents.Open` ("parameter not optional"), which a fresh machine would hit too.
- `session stop` reported the application closed while a dialog a timed-out call had
  left up held its `Quit`. Known questions are answered while it quits, and an
  application still running afterwards is an `E_CONFLICT` naming the dialog.
- A draw that failed inside the adapter itself -- a sheet it could not make active --
  reported no sheet or index; every draw failure now carries them.
- The `schematic_draw` output schema declares `sheets_drawn`, `sheets_not_drawn` and
  `sheets_kept`, and the dry run's declares `operations`.
- Every confirm example in `reference` repeats the arguments of a dry-run example. The
  token is bound to them, so the `pcb route`, `outline`, `rules`, `export`, `holes`,
  `labels`, `create`, `trace`, `unroute` and `library build` examples failed with
  `E_CONFLICT` when copied; a test now holds every command to it. `pcb arrange` declares
  in its blast radius that it deletes every trace and via on the board first.
- `library build --dry-run` reports a cell pad that no symbol pin reaches. The pin check
  ran one way only -- every symbol pin needed a cell pin -- so a cell with *more* pads
  than the symbol has pins passed clean. A power MOSFET in DFN or PowerPAK carries
  several pads per electrode and a drain paddle, so its converted cell had seven pads
  against a three-pin symbol and the preview reported `issues: []`: a library that looks
  built and is not. The unmapped pads and both counts are named.

- DS-07 checks the area a design may draw in rather than only the border. The sheet
  title strip and the notes band are placed by the planner itself, so those are computed
  from `MARGIN` and the note count; the border symbol's own frame and title block are
  measured per sheet size (A4 and A3 recorded, others fall back to the computed area).
  A sheet used to pass with `issues: []` while a whole stage was drawn through the notes
  and two capacitors sat on the title block. The plan publishes the rectangle per sheet,
  so a caller can lay out against it instead of measuring a rendered export.

- `--dry-run` and `--confirm` are refused on a command that is not a guarded write,
  instead of being accepted and ignored. `schematic export` renders a PDF and has no
  gate, so a dry run wrote the file and the next one failed because it already existed.
  The reference already declared which commands the gate applies to; now it is enforced,
  and probing a command that has no gate says so rather than performing the write.

- A design collection that cannot be read names the state that caused it. Two ordinary
  mid-design states both surfaced as a bare `DesignComponents` type mismatch, stopping
  `review run`, `bom export` and `schematic components` at once: a schematic changed
  since the last `library build --package`, and a sheet added or removed, which needs the
  project reopened instead. `GetActiveDesign` reporting the schematic rather than the
  block separates them, so the error now carries the likely cause and the one command
  that clears it. Being unpackaged is a normal state to be in, not a failure.

- A part's reference designator and value start where the plan puts them. Designer's
  attributes carry a `VdOrigin` saying which corner of the text sits at the location, and
  a symbol leaves its refdes at `VDALIGN_MR` -- middle *right* -- so setting a location
  put the text's right edge there and the string ran back its own width. A 17-unit refdes
  against the planner's +12 offset landed on the part's own centreline, on every part of
  every drawn sheet. The planner now states the left-anchored origin it means.
- `project init --template` gives every user symbol partition a parts database instead of
  leaving Designer to raise a modal dialog at draw time, which blocks the draw until a
  human clicks. A missing one is filled by copying an unused stock database out of the
  project's own library, and only when at least two unused databases are byte-identical --
  enough to tell an empty database from a populated one rather than guess. A library where
  that does not hold is reported in `parts_databases.missing` and left alone.
- `schematic draw` reports `sheets_drawn` and `sheets_not_drawn`. A draw wipes and redraws
  the sheets its design names; any other sheet keeps what was on it, which for a cloned
  project is the template's content and used to ship unremarked.
- A timed-out native call marks the session stale, and the next task command reports that
  instead of failing somewhere unrelated. After a read timed out, `IsProjectOpened()`
  reported false with the project still open, so the next command asked Designer to open a
  project it already had and was refused with a message about scripts and GUIs. Recovery
  commands (`session status`, `start`, `stop`) still run against a stale session.
- `_ensure_project` reads the open project's path whatever `IsProjectOpened()` claims, so
  a project that is already open is not reopened, and product code 64185 maps to a
  conflict naming the state that refused the request rather than a retryable server fault.
- A symbol that displays the same pin name twice is refused when the plan is built,
  naming the repeated names and the pins that share them. Designer names a net after
  the pin a wire meets, so repeated pin names put two wires on one auto-named net and
  the second explicit label made the draw stop with 6035 "Net already labeled" minutes
  in and partway through. Renaming automatically is not available: the pin's `L` record
  is what the parts database maps to cell pin numbers. Give each pin a distinct name and
  keep the shared net on the wire's label, which is what the netlist already uses.
- `session stop` quits the application that is attached rather than defaulting to pcb.
  Quitting discards unsaved design work, and with only Designer running the old default
  reported success against an application that was not there while leaving the one the
  caller meant still running. The domain is resolved from the live session; both attached
  is a `E_USAGE` asking for `--kind`, an explicitly named application that is not running
  is `E_NOT_FOUND`, and the preview's blast radius now names the application instead of
  saying "the running Xpedition application". Machines with no adapter to probe keep the
  previous behaviour.
- `session start --kind schematic` starts Designer through `common\win64\bin\viewdraw.exe`
  rather than the product under `wv`. The common launcher sets up the release environment
  before starting the product; naming the product directly skipped that and the process
  exited immediately with STATUS_DLL_NOT_FOUND (0xC0000135), so every native schematic
  command was unusable unless someone had already started Designer by hand. The pcb domain
  always went through the launcher and the comment above the code already stated the rule.
- A missing Xpedition automation-licensing call is classified from its EXCEPINFO
  numbers (product code 10279, scode `0x8004022D`) instead of by searching the COM
  description for "license". On a localised installation the description carries
  neither that word nor "token" — a Chinese session reports 自动化代码不包含身份验证
  所需的许可调用 — so the fault was reported as a retryable `E_SERVER` and an agent
  would retry it indefinitely. It is now the non-retryable `E_AUTH` it always was.
  Observed on XPED2604; ordinary faults such as `DISP_E_TYPEMISMATCH` arrive without
  this EXCEPINFO and keep their existing classification.
- The reference-query test reads `contract/contract.json` as UTF-8 rather than with
  the locale codec, so the suite no longer fails on a GBK Windows machine. The
  hosted runners default to UTF-8, so CI could not observe this.
- Explicit NativeBackend project initialization without a template fails before
  preview, token consumption or file creation; it never creates a mock project.
- Native ChangeSet results verify the requested final coordinates, properties,
  part identities and connectivity against read-back instead of hardcoding
  verification success. Missing observations or unsupported verification fail
  closed; post-write read-back failures are non-retryable and report the stage.
  Read-back is not a durability or electrical-correctness claim.
- `--fields` projects records inside arrays without changing their order or
  cardinality. Paging controls and `_untrusted` annotations survive projection;
  parent/child selectors are order-independent. Missing fields retain legacy
  omission semantics; no post-write selector error is introduced.
- `reference` derives confirm/dry-run applicability from write-command metadata.
- Three things the first CI run found, none of which a Windows workstation could
  show: `_layout_board_path` could not resolve a board off Windows, because a `.prj`
  stores its relative board path with backslashes and those are ordinary filename
  characters elsewhere; `pcb render`'s tests were skipped-by-crash wherever Pillow
  was absent, since Pillow sat only in the `native` extra while the renderer is pure
  Python (it is now in `dev` too); and a test encoded its fixture with `mbcs`, which
  does not exist off Windows and cannot represent Chinese on a Windows runner whose
  code page differs.
- The contract tests no longer depend on what is installed on the machine that runs
  them. NativeBackend finds its adapter through `XPEDITION_NATIVE_COMMAND` or through
  `xpedition-native-adapter` on PATH, so on a workstation with the native extra the
  backend really is available and the write-gate test saw a successful snapshot where
  it asserts `E_BACKEND_UNAVAILABLE`. The suite now points the variable at a path that
  does not exist.
- `session start|attach|open|stop --backend native_xpedition --kind bogus` reports
  `E_VALIDATION` again instead of `E_BACKEND_UNAVAILABLE`: the kind is validated before
  the adapter is asked for, because a malformed flag is a usage error whether or not an
  adapter is installed.
- Grid snapping rounded half-way values to the even side (Python's `round`), so a
  column on a 2.5 mm step landed alternately high and low; they now always go up.
- `pcb create --replace` could not remove the old layout folder after an NC drill run:
  Layout keeps `LogFiles/DrillPrefs.txt` open after the document closes, and
  `Application.Quit` leaves it on its start page still holding the file. A Layout
  with no document open is now ended by its process when the folder stays locked,
  and the next step starts it again.
- `pcb export` runs the output dialogs a second time when one of them wrote its own
  settings back over the patched setup file (the ODB++ job came out without its
  drill layer once); the result says `rounds`.
- Fixed shared ground bars for several bottom-edge ground pins: the ground symbol now sits on
  the bar's free end, where Designer connects it, instead of on the corner where it did not.
- The board ChangeSet operations called `Component.Place` and `Component.Move` with the
  wrong arguments: `Place(x, y, orientation, bTop, eFixType, eUnit, eAngleUnit)` takes
  "top side" where a mirror flag was passed, and `Move(x, y, eUnit)` was given no `y`.
  Both now take an optional `unit` (`mm`, `mils`, `inch`, `um`; the document's unit by
  default).
- `pcb arrange` measures a placed part unturned: Layout reports the extents of a part as
  it stands, so a connector standing at 90° from the previous arrangement came back
  5.5 mm wide and 10.9 mm tall and the next plan turned it again, onto its neighbour.
- `pcb route` regenerates a plane it finds in Draft instead of skipping it: the GND
  plane of the board on KiCad footprints was back in Draft after its pour had been
  saved, and Batch DRC reported the ground net partial with its vias dangling.
- The CLI and the native adapter now exchange UTF-8 on their pipes regardless of the console
  code page. On a Chinese Windows the parent decoded the adapter's output as GBK and crashed in
  its reader thread, so every native error surfaced as `E_UNKNOWN`/`TypeError` instead of the
  adapter's structured error.
- Session liveness no longer probes with `os.kill(pid, 0)` on Windows. `signal.CTRL_C_EVENT`
  is 0 there, so CPython routed signal 0 to `GenerateConsoleCtrlEvent`, which takes a process
  *group* id: it reported every running Xpedition process as dead and made `doctor` warn
  "recorded native process is no longer running" against a live session. The probe now uses
  `OpenProcess` plus `WaitForSingleObject`.
- Native Designer snapshots return component coordinates again. `GetLocation` is a property
  on current Xpedition Standard, not a method, so calling it yielded the point's default
  member and every `x`/`y` came back as null.
- A native schematic placement whose reference-designator assignment fails now reports the
  symbol it left on the sheet — `orphan_placed` with library, device, symbol and coordinates —
  instead of failing silently with the instance still present. The API exposes no delete
  entry point, so the orphan has to be removed in Designer.
- `doctor` reads `release_readiness` from its single source instead of restating it. The
  hardcoded copy went stale the moment the smoke evidence changed, and `doctor` is what an
  agent checks first.
- Native schematic reads resolve net names and connections. A Designer net carries no
  `Name`; the name lives on the label attached to one of its segments
  (`net.GetLabel(segment).TextString`), so reading `Name` reported every net on a live
  sheet as unnamed and left `connections` empty.
- Native schematic placement no longer defaults the library to `MISC`. No such library
  exists in a stock installation, so a missing parameter surfaced only as Designer error
  2005 in the message window; the adapter now names the missing parameter instead.
- Native Designer reads return component pins again, and a schematic net can be labelled.
  `GetConnections` and `GetSegments` are properties on current Xpedition Standard, not
  methods; calling them raised DISP_E_PARAMNOTOPTIONAL inside a bare `except`, so every
  snapshot reported `pins: []` and `connect` failed to find a pin by number. A shared
  `_com_member` helper now reads either form, and a net that cannot carry its label fails
  loudly instead of silently.
- `CO_E_SERVER_EXEC_FAILURE` from COM activation is reported as `E_BACKEND_UNAVAILABLE` with
  a launcher hint. Xpedition `LocalServer32` entries point at binaries that need the release
  environment established by the launcher in `common/win64/bin`, which COM activation skips.
- Native schematic reads no longer fail on every real design. `normalise_project` required a
  reference designator on every component and a name on every net, which is right for an
  authored project file but wrong for observed data: a live schematic always carries ground,
  power, port and border symbols that have none, and a single one of them made `design
  snapshot` and all nine `schematic *` commands return `E_PROJECT_INVALID`. Observed data now
  separates those out and records a count under `metadata.unnamed`; authored files stay
  strict.

[Unreleased]: https://github.com/fatecannotbealtered/xpedition-cli/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/fatecannotbealtered/xpedition-cli/releases/tag/v1.0.0
