# Compatibility matrix

This document records the backends that have actually been verified. A
capability is not advertised as native support until a licensed Xpedition
environment passes the smoke loop described in [`E2E.md`](E2E.md).

| Backend | Version / environment | Status | Notes |
|---|---|---|---|
| MockBackend | xpedition-cli 1.0.x, Python 3.10–3.12 | verified | Offline JSON project model, schematic/PCB/constraint/analysis/manufacturing/library reads, ChangeSet validation/preview/apply, snapshot, BOM and review. |
| NativeBackend — Designer | Xpedition Standard XPED2604 on Windows 11, `Viewdraw.Application`, pywin32 bridge | reads and writes verified | Attach, schematic snapshot, `AddPartInstance` placement, net creation, labels and coordinate read-back all confirmed against a running Designer session. The R1/C1 smoke loop of [`E2E.md`](E2E.md) is recorded against a hand-built minimal library, not the stock one — see the blocker below. |
| NativeBackend — Layout | Xpedition Standard XPED2604, `MGCPCB.ExpeditionPCBApplication` | attach verified, reads unverified | COM registration and health probing work. No board document was open during verification, so `Components`/`Nets` reads have not been exercised. |
| ExchangeBackend | JSON / CSV / BOM / IPC-2581 XML | verified | Import and normalization use the normalized project model with dry-run/confirm writes. |
| ExchangeBackend | PDF / EDN / ODB++ | planned | Format-specific parsers are not enabled yet. |

## What blocks the E2E smoke loop

Not the CLI, and not COM: **the installation ships no component library**.
`SDD_HOME/standard/templates/dxdesigner/TemplateLibrary` is a deliberately empty
skeleton — every category directory (`Resistors`, `Capacitors`, …) exists, but
every `*.pdb` is a 3296-byte stub, every `*.cel` a 19492-byte stub, and the
device symbol directories hold zero files. Only borders, builtin symbols,
`Globals` (power/ground) and `Drawing.cel` carry content. Requesting a resistor
is answered by Designer itself:

```
6055  Symbol Resistors:R.1 not found, empty or a block.
```

`E2E.md` therefore cannot be satisfied on a stock installation; it needs a
populated central library. Placement against a symbol that *does* exist
(`builtin:espl1`) succeeds and reads back with correct coordinates, so the write
path is not what is missing.

## Driving the applications by command

The COM classes expose only a narrow slice of each product. The full command set
of every application is catalogued on disk:

```
SDD_HOME/standard/automation/Commands_XpeditionLayout.csv      1041 commands
SDD_HOME/standard/automation/Commands_Xpedition_Designer.csv
SDD_HOME/standard/automation/Commands_{Library_Manager,Symbol_Editor,CellEditor}.csv
```

Each row carries an id, an internal name, a display name and a description.
Designer runs them through `ExecuteCommandByID(id)`, `ExecuteCommandByName(name)`
or `ExecuteCommand(string)`; Layout has the equivalent on `Gui.ProcessCommand` /
`Gui.ProcessKeyin`. This is how a board gets created — Layout's own COM interface
has no such entry point, only `OpenDocument` and `OpenReference`.

Not every command is automation-friendly. `Package Design for Layout` (35085)
launches `packagerui.exe`, a separate GUI process that waits for a human, and the
dialog-suppression switches do not reach it.

## Forward annotation without a GUI

`package.exe` does the same work as the packager dialog and is a console program,
so the adapter's `package` method runs it headless and returns structured errors
read back from `<project>/Integration/PartPkg.log`.

It must be started through `common/win64/bin/package.exe`. Launching the real
binary under `wg/win64/bin` directly skips the release environment and the
program cannot initialise its Qt platform plugin — it puts up "no Qt platform
plugin could be initialized" and never runs. Same launcher rule as below.

## COM activation needs the launcher

Every Xpedition `LocalServer32` registration points at the real binary under
`SDD_HOME/<product>/win64/bin`, but those binaries depend on the release
environment that the small launcher in `SDD_HOME/common/win64/bin` sets up.
Direct COM activation skips the launcher, so `CoCreateInstance` fails with
`CO_E_SERVER_EXEC_FAILURE` (0x80080005). Start the product through its launcher,
then attach with `GetActiveObject`. This applies to `LibraryManager` and
`ExpeditionPCB` alike; the adapter reports the condition as
`E_BACKEND_UNAVAILABLE` with a launcher hint rather than a bare COM error.

## Library tooling present on a stock installation

Useful when a library has to be built rather than imported:

| Tool | Path under `SDD_HOME` | Automatable? |
|---|---|---|
| Library Manager | `common/win64/bin/LibraryManager.exe` | COM `LibraryManager.Application`, **read-only** — the object model exposes no Add/Create for symbols, cells or parts |
| Cell / Padstack editors | reached via `ActiveLibrary.CellEditor` / `.PadstackEditor` | yes — `OpenDatabase`, `NewPartition`, `NewCell(eCellType)`, `SaveActiveDatabase`, `SuppressTrivialDialogs` |
| HKP converters | `common/win64/bin/HKP2{PadstackDB,CellDB,PartsDB,LMCDB}.exe` and the `*DB2HKP` reverse | yes — GUI-subsystem binaries with a command line (`-i <hkp> -o <db> -c <lmc> -m -l <log>`; a wrong argument is a message box, not an exit code). `library build` and `kicad_import` run them; `CellDB2HKP -a` / `PadstackDB2HKP -a` exports are the grammar reference (see "KiCad footprints as cells" below) |
| PCB Footprint Expert 26 (Siemens) | `lm_fpe/` | IPC-7351B generator with populated `.fpx` libraries for SM/TH discretes, semiconductors, connectors and BGA |

Schematic symbols are plain ASCII (`SymbolLibs/<partition>/sym/<name>.<version>`),
so they can be generated as text; cells and padstacks go through the editor COM
objects above.

## Sheet units and symbol file versions

Measured by placing stock symbols through the adapter and reading the pin
coordinates back:

| Object | In the file | Read back on the sheet |
|---|---|---|
| `builtin:nc` connection point | `P 16 -254000 0 …`, header `V 54` | 10 units left of the origin |
| `builtin:Arrow_Blue` pin | `889000`, header `V 54` | 35 units |
| `builtin:Arrow_Green` pin | `35`, header `V 53` | 35 units |
| `asheet` border | `D 330200 0 27609800 21590000`, header `V 54` | 8.5 in tall, so 10 nm per file unit |

One sheet unit is therefore 10 mil (0.254 mm) and the 100 mil grid is 10 units.
`V 53` symbol files use sheet units directly; `V 54` files — every stock symbol
on XPED2604 — are in 10 nm, 254000 per grid step. The `Y` record is the
symbol type, not a scale: 1 part, 3 annotation, 4 power or ground, 5 border.
Hand-generated symbols use `V 53`. The xpedition-schematic Skill's
`reference/schematic-conventions.md` carries the grid, size and sheet-extent
rules that follow from this.

## Drawing a whole schematic

`schematic draw --design FILE` plans a readable schematic with
`xpedition_cli.schematic_layout` (IC blocks with a treatment per pin, vertical
ladders and horizontal chains of two-terminal parts between power, ground and
labelled nodes, titles, notes, overview boxes) and executes the plan through the
adapter method `draw`: `open_sheet`, `wipe_sheet`, `set_sheet`, `place_part`,
`place_symbol`, `wire`, `box`, `text`, `save`. Symbol files are written into the
project's central-library partition first, named by content. Afterwards the
project is reopened and every net is read back and compared with the plan;
unlabelled junctions are matched through Designer's own net `UID` (`$2N121`).
The recorded run is in [`E2E.md`](E2E.md).

`schematic show --sheet N` (adapter method `show`) activates a sheet, runs
`Fit All` (32775) and raises Designer's window: `SetForegroundWindow` is refused
to a background process, but making the window topmost and releasing it works.
`--output` captures the window through GDI into a PNG encoded in-process.

`project init --backend native_xpedition --template TPL.prj --project NEW.prj`
(adapter method `clone_project`) creates a project the only way Designer allows
without a GUI: copy a known-good project folder without `Templates`, `Work`,
`LogFiles`, `ProjectBackup`, `Thumbnail` and `*.bak`, rename the `.prj`, rewrite
the absolute `CentralLibrary` and `DBCFile` keys that pointed into the template
folder, and open the copy. The template is closed first if Designer has it
open, because the iCDB server holds its files. The destination must be an
ASCII path (see the facts below).

## Rendering a schematic to PDF

Designer's `Generate PDF` (command 34622) is a dialog, but the installation
ships `common/win64/bin/sch2pdf.exe`, a console program that renders a project
straight from its database and works while Designer still has the project
open. `schematic export --backend native_xpedition --project X.prj --output
X.pdf` runs it through the adapter method `export_pdf`. Facts that matter:

- It prints the area inside each sheet's border and nothing else; objects
  placed outside the border simply do not appear.
- `-c` selects colour: 0 black on white without black text, 1 colour on white
  (the default), 2 colour on black, 3 black on white, 4 colour on white with
  coloured text. `-schematic NAME` limits the run to one schematic.
- Every rendered sheet is reported as `Printed <schematic> Sheet <n>`; the
  adapter returns those as `sheets`.

## Designer drawing facts verified on XPED2604

| Fact | Detail |
|---|---|
| `AddPartInstance(partition, part, symbol, x, y)` | the second argument is the part (shown as the instance's Part Number), the third the symbol name |
| `AddSymbolInstance(partition, symbol, x, y)` | places a refdes-less symbol — stock `Globals:gnd`, `builtin:No_Connect`, generated power symbols — with no refdes step and therefore no orphan |
| Power and ground nets | a wire ending on the symbol's origin joins the net named by its `NETNAME` attribute, provided the symbol file is type 4 (`Y 4`) like every stock Globals symbol; a type-1 (part) symbol with a `NETNAME` attribute names nothing |
| Symbol cache | Designer keeps the definition of any symbol it has placed in the open project; a regenerated file with the same name is ignored. Give a changed symbol a new name |
| `Orientation` on an instance | 0 / 1 / 2 / 3 = 0° / 90° / 180° / 270° counter-clockwise, 4–7 the mirrored set; pin coordinates read back rotated, so read them instead of predicting them |
| Wiping a sheet | `ExecuteCommandByID(57642)` (Select All) then `Block.DeleteSelected(False)` removes every object and keeps the border; there is no per-object delete |
| Text | `Block.AddText(text, x, y)`, then `.Size = n` in sheet units |
| Attributes on an instance | `instance.AddAttribute("VALUE=10k", x, y, 3)` adds a visible attribute; the stock `pwr_bar`'s `NETNAME` cannot be changed this way, its attribute collection is empty |
| Sheet size | `Block.SheetSize` is the `VDSHEET_*` enum (0 A, 1 B, 2 C, 3 D, 4 E, 5–9 A4–A0, 10 custom); `SchematicSheetDocuments.Add()` and `InsertSheet` add sheets |
| Drawn wires | free `AddNet` segments that meet at an endpoint, and a segment that starts on the middle of another, all join one net (verified: an L-shaped run plus a T drop read back as one three-pin net); labels go on any segment, once per net (`Net already labeled`, 6035) |
| More sheets | `ExecuteCommandByID(34165)` (New Sheet) adds and activates a sheet; `SchematicSheetDocuments.Open("Schematic1", "2")` switches, `DeleteSheet("Schematic1", 2)` removes. The COM `Add`/`InsertSheet` calls fail with error 670 |
| After sheet operations | `GetActiveDesign` reports the schematic (`Schematic1`) instead of the block (`Board1`) and `DesignComponents` fails with a type mismatch until the project is closed and reopened |
| Off-page connectors | `builtin:OFFPAGE_INPUT` places like any symbol; the net still takes its name from a label on the wire |
| Pin text on a symbol | pin attributes take absolute symbol coordinates: `A x y size 0 3 3 #=1` shows the pin number, `A x y size 0 3 3 NAME=VIN` the pin name inside the body. An `L` record after a `P` record is not displayed on a part symbol, which is why generated boxes showed a black blob at the origin: every pin number was drawn at the same point |
| Sheet size and border | `Block.SheetSize = 5` (VDSHEET_A4_SIZE) changes the page; `Block.ChangeBorder("a4sheet")` swaps the border symbol; set the size first, the border second. Either change can leave another sheet's window active, so re-activate the target sheet before drawing on |
| Sheets open vs sheets existing | `SchematicSheetDocuments` lists the sheet windows that are open; `GetAvailableSheets(schematic)` (an `IStringList`: `GetCount()`, 1-based `GetItem(i)`) lists the sheets that exist. `Open(schematic, n)` activates a sheet only the first time; afterwards call `document.Activate()` on the open document |
| Attribute text on an instance | `attribute.Orientation = 0` turns a rotated value or refdes horizontal and `attribute.SetLocation(x, y)` moves it; `X`/`Y` are read-only |
| Chinese text | `AddText` accepts Unicode and Designer draws Chinese titles and notes correctly on screen. The project stores them in the system code page (GBK here), and `sch2pdf` writes those bytes as Latin-1 glyphs, so a PDF shows mojibake; the text is recoverable by re-encoding Latin-1 to GBK, which is what a review pipeline has to do when it extracts notes |
| Verification | `RunDesignIntegrityChecks` is a database integrity test (22 layer/object tests). The schematic ERC is the `Full Verification` command (34155; `Quick Verification` 46461) with the rules of the project's `Verify.ini`; it writes `LogFiles/vdrc.log` (`SEVERITY` / `GROUP` headers, then `<rule> - [flatnet : X, component: R1($2I5), pin: $1P10] message`) and `LogFiles/grc.log` (graphical checks). The adapter method `verify` runs it and parses both |
| Reading a design back | `net.LogicalNetName` gives the net name, `net.UID` the `$<sheet>N<id>` id of an unlabelled net; a component's `UID` `$<sheet>I<id>` tells its sheet; `component.Attributes` holds the instance attributes (Part Number, VALUE …) but not the symbol's DEVICE/PART_NAME; pin names and types are not exposed on the connection's pin object; the symbol name of an instance is not exposed at all — a no-connect mark is recognised by geometry, a refdes-less symbol sitting on the pin end |
| Non-ASCII paths | a project whose folder path contains Chinese characters opens, reads back and shows its sheets, but `AddPartInstance` reports every symbol file written after the copy as `not found, empty or a block`, before and after a reopen; the same project on an ASCII path finds them at once. Keep projects and their central library on ASCII paths; `schematic draw` and `project init --template` refuse others with `E_VALIDATION` |
| Pins on wire corners | a symbol pin placed where a stub and a bar meet end-to-end does not connect: Designer merges the two segments into one polyline and the pin sits on a vertex. A pin on a free wire end or on a T junction connects. The planner therefore runs a shared ground bar one stub past the last pin and puts the ground symbol on the free end |
| Creating a project | there is no automation call; copy a project folder as described above. The iCDB `database` folder copies cleanly while the project is closed, and the copy opens under a new `.prj` name |
| Pin types and ERC | `drc-BI-POWER` / `drc-BI-GROUND` fire for every pin typed `BI` on a net that also carries a `POWER` or `GROUND` typed pin, except on resistors and capacitors, which the verification exempts. Pins typed `ANALOG` pass: the generated inductor, diode, LED, switch, battery, thermistor, MOSFET drain/source and test-point pins are `ANALOG`, and the demo design verifies with no warning |
| Symbols without pins | a `V 53` part symbol with shapes and no `P` record (a mounting hole) places through `AddPartInstance`, gets a refdes and appears in the BOM |

## Creating a board and forward-annotating it (verified on XPED2604)

Layout's `File > New` is the Job Management Wizard, a separate program
(`common/win64/bin/JobWizard.exe`) with a documented command line:

```
JobWizard -createnew [-f] -prj <project.prj> -newpcb <PCB/Name.pcb> -template "<Layout Template Name>" -lib <library.lmc> -l <log>
```

`-f` ignores create warnings, `-l` names the log (otherwise
`%LOCALAPPDATA%\Temp\JobWizard.log`). It copies `Templates/Layout/<name>` out of
the central library (58 files for the stock 4-layer template), writes
`PCBDesignPath` and `LayoutTemplate` into the `.prj`, and needs no Designer
session. `pcb create` runs it. Facts that cost time:

- The command line takes the **first** entry of the `.prj`'s `LIST Designs` and
  stops with "未能从通用数据库中获取设计信息" (failed to get design information
  from the common database) when that is a schematic node (`ConfigType
  "Board1"`) rather than the board design (`ConfigType "PCB"`); the GUI wizard
  picks the board design itself. `pcb create` lists the board design first.
- Template names are the folders under `<library>/Templates/Layout`. A project
  cloned without that folder (what `project init --template` does, 24 MB per
  template) offers none; `pcb create` copies the requested one from
  `SDD_HOME/standard/templates/dxdesigner/TemplateLibrary/Templates/Layout`.
- `-l` is the log file; it is not a mode flag. Wrong options show the usage in
  a message box titled `messageBox`.
- `ExpeditionPCB.exe -create -prj X.prj -design <template.pcb>` opens the
  template itself and asks for a library; `pcbui.exe -p X.prj Board1` is the
  old PADS-style "PCB Interface" netlister, not a board creator.

Forward annotation is `Document.ProjectIntegration`, an object without type
information (`GetTypeInfo` fails, so `EnsureDispatch` cannot wrap it); its
interface is in `common/win64/lib/ProjectIntegration.dll`: `ForwardAnnotate`,
`BackAnnotate`, `LoadCES`, `SynchCES`, `IsForwardAnnotationAllowed`,
`IsBackAnnotationAllowed`, `IsLoadCESAllowed`, `IsSynchCESAllowed`,
`ProjectFile`, `ForwardAnnotationStatus`, `BackAnnotationStatus`,
`SynchCESStatus`, `LoadCESStatus` (1 required, 2 in synch, 3 no CES). Through a
late-bound object every member runs on attribute access and returns a bool.
`pcb annotate` runs it. Facts:

- The board needs the cell partitions in the `.prj`: `LIST 2dCellLibraries`
  with `VALUE "CellDBLibs\<partition>.cel"` entries in the design section, next
  to `LIST PDBs` and `LIST Symbols`. Without it Database Load stops with "No cell
  library search paths found from X.prj — Unable to initialize CellDBUpdate".
  `library build` and `pcb create` register a cell partition for every parts
  partition listed.
- A cell must have exactly the part's pin count; a 4-pin SOIC placeholder on a
  3-pin regulator is "Cell CLI_SOIC4 has 4 unique Alphanumeric Pin Numbers
  while Part Number XC6206-3.3 has 3" and Database Load terminates.
- Asking for forward annotation when the status is already 2 (in synch) raises
  `正向标注失败` (1001) without writing the log; the adapter reports `in_synch`
  instead of running.
- A successful run writes `PCB/LogFiles/ForwardAnnotation.txt` with "N nets were
  found containing M pins", "N components were found" and "Forward-Annotation on
  the Layout Design has been successfully completed"; the run takes about 30 s
  and shows progress boxes (`Packager`, `Database Load`) that need no answer.
- After opening a board whose front end is newer, Layout asks "新更改已经可以进行
  正向标注。是否要立即进行正向标注?" (是/否); the adapter answers 否 so that the
  annotation stays an explicit command.

Opening a board and attaching to Layout:

- `OpenDocument` wants a `.pcb`; a `.prj` is "Cannot open document file due to
  invalid file extension" (10211). The board of a project is the design
  section's `PCBDesignPath`, relative to the `.prj`.
- A Layout that a killed session left behind asks "应用程序已尝试确定设计状态超过
  15 秒了…" (Open/Cancel) and then "数据库恢复" (load the autosave or the
  user-saved database, 确定/取消). Both are Qt windows, not `#32770` dialogs, so
  they are read and answered through UI Automation (`pywinauto`), from a helper
  thread, because the `OpenDocument` call does not return until they are
  answered.
- An instance started by COM activation (`Dispatch` of the progid) opens the
  board in a hidden window and never registers in the running object table:
  `GetActiveObject` keeps failing and nothing can attach to it. An instance
  started through the launcher registers after start-up (about 50 s on this
  machine). The adapter starts Layout through the launcher and waits.
- `GetActiveObject` returns a late-bound object without the type library's
  enumerations; `gencache.EnsureDispatch` generates the library (`epcbSelectAll`
  and friends appear in `win32com.client.constants`) but its generated classes
  expose `Document.Components` and `Document.Nets` as parameterised properties
  that reject arguments (`__call__() takes from 1 to 2 positional arguments`),
  and the late-bound object rejects them too ("无效的参数数目"). Read both bare:
  every parameter is optional and defaults to everything.
- Components arrive unplaced (`Placed` false, position 0/0); `Pins.Count` on the
  document reads 0 even with 95 pins on the board.
- Designer has a question of the same kind: `OpenProject` for another project
  while sheets are open asks "更改当前项目需要关闭所有打开的文档。是否希望关闭它们
  并继续打开选定的项目?" (Yes/No) and waits. The adapter answers Yes from the
  same helper thread.

Placing components (verified on XPED2604, `pcb arrange`):

- `Component.Place(dX, dY, dOrientation, bTop, eFixType, eUnit, eAngleUnit)`:
  the fourth argument is "top side" (True), the fifth the fix type (0 none), the
  sixth the unit of the coordinates (`EPcbUnit`: 0 current, 2 mils, 3 inch, 4 mm,
  5 µm), the seventh the angle unit (0 degrees). `Component.Move(dX, dY, eUnit)`,
  `Component.UnPlace()`. `GetPositionX(eUnit)` / `GetPositionY(eUnit)` read the
  position in any unit; the bare `PositionX` is in the document's current unit,
  which is mils on the stock templates (`Document.CurrentUnit` 2).
- `Component.Side` is 1 on the top and 512 on the bottom (`epcbSideTop`,
  `epcbSideBottom`); `Layer` is 1 for a top-side part as well, so a layer test
  cannot tell the sides apart.
- `Component.Extrema` (a property) is the footprint's bounding box:
  `GetMinX(eUnit)` … `GetMaxY(eUnit)`. An unplaced component has no extents and
  reading them fails, so a part is measured by placing it, reading and
  unplacing it again. The cell object itself (`Component.Cell`) has no size.
- `Document.BoardOutline.Geometry` gives the outline: `GetRectMinX(eUnit)` …
  `GetRectMaxY(eUnit)` for the bounding rectangle and `GetPointsArray(eUnit)`
  for the vertices; the stock 4-layer template is a 50.8 mm (2000 mil) square
  with its origin at the bottom-left corner.
- An unplaced component is not drawn on the board at all; after forward
  annotation the board looks empty although the component navigator counts
  the parts. Placing 39 parts through `Place` and saving takes about 5 s.
- `Net.Pins` lists only the pins of placed components once a part has been
  unplaced (right after forward annotation the unplaced parts still appear);
  a placement planner that reads connectivity must have every part on the
  board first. `Net.IsPower` is a method and is False for `+3V3` on this board,
  so a supply is better recognised by its member count.
- Layout's online DRC rejects `Place` with "DRC 违规" (10203) when the part
  would land on another part or on its own old footprint, and when two
  identical footprints share one spot; a part placed outside the board outline
  is accepted. `Document.RespectComponentPlacementDRC` reads False here and
  does not switch that check off. Lift the parts first, then place them.
- `PutBoardOutline(nPnts, points, width, eUnit)` replaces the outline; the
  route border (`PutRouteBorder`) and manufacturing outline
  (`PutManufacturingOutline`) are separate objects that keep their old size
  until replaced too. Points go as three rows (x, y, radius) with the first
  point repeated to close the polygon.
- `PutPlaneShape(layer, nPnts, points, net, routeObstruct, hatch, hatchWidth,
  hatchDistance, component, eUnit)` and `PutFabricationLayerText(text, x, y,
  epcbFabSilkscreen=2, epcbSideTop=1, height, rotation, penWidth, font, attr,
  horiz, vert, component, eUnit, angleUnit)` take an optional component object;
  pass `None`, because win32com turns the typelib's default `0` into "The
  Python instance can not be converted to a COM object". Text is anchored at
  its centre: a 1.2 mm string measures about 1.18 mm per character.
- Routing rules are read-only through automation: `NetClass.MinTraceWidth(layer,
  scheme, unit)`, `TypicalTraceWidth`, `ExpansionTraceWidth` and
  `Document.GetClearanceRule(a, b, layerA, layerB, unit)` read them (0.254 mm
  everywhere on the stock 4-layer template, via padstack `026VIA`) and nothing
  sets them. Placeholder cells therefore need pads at least 0.254 mm apart: a
  0.65 mm pitch with 0.4 mm pads (0.25 mm gap) leaves its nets open.

Autorouting (verified on XPED2604, `pcb route`):

- `Document.NewRoutePass()` returns a pass object: `PassType(ePassType,
  effortStart, effortEnd, viaGrid, routeGrid)` with the `epcbAR…Pass` values
  (Expand 1, Fanout 2, NoVia 6, RemoveHangers 7, Route 8, Smooth 10, Spread 11,
  ViaMin 14), `Items(epcbARAllNetsItem=0, None)`, `Order(epcbARAutoOrder=0, 0)`,
  `Config(routeDuringFanout, allowCleanup)`, then `Go()`, which returns when the
  pass is done (under a second for 18 nets on this board). `LayerSelect(1, 0)`
  is "参数无效"; without it the pass uses every layer enabled for routing
  (`Document.RouteLayerEnabled(n)`).
- `Net.IsRouted` and `Net.NumberOfOpens` (both methods) give the state per net;
  `Document.Traces` and `Document.Vias` count the result. A pass on a board
  whose parts are unplaced reports every net routed with no opens.
- Route passes leave the fine-pitch pads alone and never report why; the
  clearance rule above is the reason.

Batch DRC and hazards (verified on XPED2604, `pcb drc`):

- No automation call runs Batch DRC. Its engine, `common/win64/bin/DrcDriver.exe`
  (`-p PcbFilename [-q]`), refuses to start from a command line ("无法从命令行运行
  可执行文件"). The menu command does run: `Gui.CommandBars` lists `Document
  Menu Bar > 分析 > 批量 DRC...` with id 32769, and `Gui.ProcessCommand(32769)`
  opens the `批量 DRC` dialog (scheme combo, 确定/取消); `ProcessCommand` takes
  such MFC command ids, not toolbar names. The command-bar control objects have
  no `Execute`. After 确定 a `Processing...` window shows until the checks are
  done (about 10 s here).
- `Document.GetHazards(eType)` returns the hazards: 0 all, 1 online, 2 batch.
  Each has `Type` (an `epcbHazardType…` value: 67 Proximity, 74 PartialNets,
  75 Dangling, 77 ViasUnderParts, 24 PlacementGrid, 31/34/46/47 length and
  delay summaries…), `Description` (a few lines of Chinese text), `Objects`,
  `GetPositionX/Y(unit)` and, for clearance hazards, `GetRequiredClearance` /
  `GetActualClearance`; the other kinds raise "Property or method not valid
  for selected hazard type" on those.
- A plane shape made with `PutPlaneShape` is in the Draft state and connects
  nothing (`GeneratedPlanes` 0): every via to it is dangling and its net partial.
  `PlaneAssignment.PlaneDataState = 1` (Dynamic) generates the plane at once;
  vias the router adds afterwards only tie in after the state is toggled to
  Draft (3) and back to Dynamic. `Document.PlaneAssignments` has one entry per
  assigned net and layer.
- The first Batch DRC on this board reported 18 pad-to-pad proximity
  violations (required 0.254 mm, actual 0) on every dual-row placeholder: the
  pads' long side had been laid along the pitch. Placeholder geometry needs a
  DRC run as much as a real one.

Forward annotation after a library change (verified the hard way):

- Forward annotation never changes the cell of a component that already exists
  on the board — placed, unplaced, with `ResetCell`, `ResetCellEx`, the `.prj`
  `FwdAnnoRebuildFlag`, or after `Component.Delete` (which is refused silently
  for a schematic part). The local parts cache and the central partition both
  carry the new cell; the board keeps the old one. `ReplaceCell` wants a cell
  object from `Document.Cells`, the board's local library, which only holds
  cells a part has used. The way through is to create the board again
  (`pcb create --replace`); JobWizard's own `-deletePCB` fails with "failed to
  get design information" on these projects.
- `ForwardAnnotate` failed "in its packaging phase" ("正向标注的封装阶段出现错误",
  while the packager's own log said it finished) on every run from the adapter
  and succeeded from a plain script in the same state. The difference was the
  adapter's prompt-answering thread, which also pressed the default button of
  every `#32770` dialog of the process once a second: that ends the packager's
  progress box and the annotation with it. Only rule-based answers are pressed
  now, and the annotation succeeds at the first attempt on a fresh board. Two
  earlier observations were symptoms of the same thing, not rules: "the second
  call succeeds" and "it works with Designer's project closed". What is real:
  closing Designer's project while Layout is opening the board breaks Layout's
  database session ("iCDB error getting UID manager"), so the adapter reopens
  the board first and only then lets Designer go of the project for the run.
- With traces on the board, a part whose pins changed needs traces broken
  back, which Layout refuses in its preventive DRC mode ("不能在预防模式下打断导
  线"); `pcb annotate --unroute` deletes traces and vias first.
- Placed parts can still be invisible: the stock templates open under the
  `Loc: Assembly Bottom` display scheme, which shows nothing of a top-side
  part. `Document.DisplaySchemes` is a tuple of the scheme names (`Loc: …` from
  the design's `Config/*.dcs`, `Sys: …` from the installation) and
  `Document.ActiveView` is the `View` object (`Name` is the view name). The scheme
  setter is `ActiveView.DisplayControl.LoadScheme("Loc: Top View")` (returns True;
  `DisplayControl.Name` reads the current scheme back; `SaveScheme`,
  `LoadUserScheme` beside it), and Fit Board is `ActiveView.SetExtentsToBoard()`
  (`SetExtentsToAll`, `SetExtents(...)` too). `Application.Gui.ProcessCommand(name)`
  runs any command of `SDD_HOME/standard/automation/Commands_XpeditionLayout.csv`
  by its internal name (`VIEW_FITBOARD` → True; a name with arguments is "未找到命令").
  `pcb show` uses these; driving the toolbar combo `CMD_DISPLAY_SCHEMES` through UI
  Automation is only its fallback — on a Layout started by `pcb annotate`,
  pywinauto's `Desktop().window(handle=…).descendants()` came back empty while
  `Application(backend="uia").connect(process=pid).top_window()` saw the tree.
  The scheme picked last is kept in `Config/<user>/Graphics Settings.hkp`
  (`"[Virtual].iDC.SchemeSelectorName"`), which is what the board opens with.
  `Loc: All On` shows outlines, pads, reference designators and the ratsnest.

## Layout facts from the placement and routing round

| Fact | Detail |
|---|---|
| `Component.Extrema` | the placement outline only; the reference designator text above it is not included, so a planner that packs parts by their extents puts the labels of neighbours on top of each other |
| `RoutePass.LayerSelect(n, bool)` | accepts the **inner** layers only (2 and 3 on a four-layer board); 1 and 4 are "invalid parameters" — the outer layers are always the router's. `pcb route --layers 1,4` therefore disables 2 and 3. A handful of traces still landed on layer 3 in the first run; a second run after `--unroute` is the check |
| `TraceSegment` | has `Point1X/Y`, `Point2X/Y`, `Geometry`, no `Width`; a `Trace`'s width is `Geometry.LineWidth` (thousandths of an inch). Trace widths cannot be set through Layout's automation (`NetClass.MinTraceWidth` and friends are read-only): see the Constraint Manager section |
| `PutPlaneShape(..., bRouteObstruct, ...)` | with `bRouteObstruct` true (the earlier default) the shape blocks the autorouter on its layer — a board with pours on the outer layers routed nothing — and laid over traces already routed the call fails with "DRC 违反"; false lets traces through and the plane data flows around them. `PlaneShape.RouteObstructed` can be cleared afterwards too |
| Outer pours and the router | with the pours in place the router counts a plane net as routed while the regenerated copper leaves pins cut off (5 GND pins here) and a Fanout pass does not add vias for them; pour the outer layers after routing instead |
| `PointsArray` | three rows (x, y, r) in thousandths of an inch; a row with `r ≠ 0` is the centre of an arc from the previous row to the next, **positive r clockwise, negative counter-clockwise** (a pad's rounded corners come back positive on a clockwise outline; the board outline's corners negative on a counter-clockwise one). `Geometry.IsCircle()` first: a circle has `CircleX/Y/R` and `PointsArray` raises "the geometry for this call is incorrect" |
| Geometry for a picture | `Pin.Pads` / `Via.Pads` (one per layer: `Layer`, `Name`, `ShapeType`, `Geometries`), `Pin.Holes` / `Via.Holes` / `MountingHole.Holes` (`GetDrillSize(unit)`, `Plated`), `Trace.Geometry`, `PlaneShape.GeneratedPlanes[].Geometry` with `Cutouts`, `FabricationLayerGfxs` (`Type` 1 assembly, 2 silkscreen; `Side`, `Geometry.LineWidth`), `FabricationLayerTexts` (`TextString`, `Format.Height/Orientation/Mirrored`; `StrokeText()` for the vector fonts only), `Component.PlacementOutlines`, `Pin.GetPositionX/Y(unit)` |
| Window capture | `pcb show --output` captures a black PNG while the desktop is locked (the result says `blank` with that hint); UI Automation still works. `pcb render` draws the board from its geometry instead |
| Rounded outline | `PutBoardOutline` points array with arcs: per corner the rows `(start, 0)`, `(centre, −r)`, `(end, 0)`; a positive radius on the centre row draws the 270° arc the other way, a radius on the start or end row is an "invalid point array". `Geometry.GetRectMinX` etc. then fail with "the geometry for this call is incorrect": they only serve rectangles; read `Extrema` |
| Save prompt | while the outline was in that state the adapter's open logic tried to open the board again and Layout asked "your design and local library have changed… save all / library only / don't save / cancel" (four buttons, so the generic answerer leaves it); pressing `取消(C)` through UI Automation recovers |
| `PutMountingHoleEx(x, y, padstackName, bFromCentralLib, nDepth, bMirrored, pNet, pComponent, eFixed, eUnit)` | places a mounting hole by padstack name; `None` for the two object arguments, `True` to take the padstack from the central library. `MountingHoles` lists them with `GetPositionX/Y(unit)` |
| Display schemes | `Loc: Placement` shows pads, bodies and designators on a dark background without traces or planes — the picture to judge a placement by; `Loc: All On` shows everything, **but draws plane copper as outlines only** (`"Option.Planes.Data.Fill" "0"` in the scheme) and every layer and text layer at once. `pcb show --scheme` picks either; `--top-view` writes and picks `Loc: Top View` |
| Scheme files | `PCB/Config/<name>.dcs`, HKP-style text (`.Global_Constants`, `..File_Type Graphics_Scheme`). The `..Trace_Layer_On ( T T … )` arrays and `..Assy_Ref_Des_On True` toggles are the legacy section and this Layout ignores them; what counts is the key-value section: `"LayerControl.N" "d:1" "e:1" …` per layer, item entries such as `"Fabrication.Assembly.Part.Text.RefDes.Top"`, `"Fabrication.Silkscreen.Part.Text.RefDes.Top"`, `"Place.Part.Text.RefDes.Top"` (the designator drawn at the cell, a third copy of the name), `"Part.Cell.Origin.Top"` (the origin marker), `"Part.PlaceOutline.Top"`, `"Fabrication.DrillDrawingThrough"` — an item is drawn only with **both** `d:1` and `e:1` — and `"Option.*"` values (`Planes.Data.Fill`, `Pin.Number.Top`, `Pin.Type.Top`, `Pin.NetName.Top`, `Fabrication.AssemblyItems.Top`). A file added there appears in `Document.DisplaySchemes` at once but in the toolbar combo only after the board reopens |
| Three names per part | every part carries its designator three times on screen: the silkscreen text (printed on the board), the assembly-layer text (the assembly drawing; 1 mm on KiCad test points, 0.4 mm on the others) and Layout's placement-level designator at the cell. Normal; only the silkscreen one matters for the board. `Loc: Top View` shows that one alone |

## Hand routing through the automation (verified 2026-09-15)

| Fact | Detail |
|---|---|
| `PutTrace(nLayer, pNet, dWidth, nPnts, points, pComponent=None, eFixType=0, eUnit)` | adds a trace; the points array is the three-row `(x, y, r)` form, width and points in the unit given (`UNIT_MM`); returns the trace (`Geometry.LineWidth` in th). A trace that violates a clearance rule fails with "DRC 违反" — Layout's online DRC judges every call |
| `PutVia(dX, dY, pPadstack, pNet, pComponent=None, eFixed=0, eUnit)` | places a via; `pPadstack` is an object, not a name. `Document.Padstacks` lists only the padstacks in use, so a board whose vias were all deleted has no via padstack: `PutPadstack(1, nLayers, "026VIA", False, True)` pulls one from the central library and returns it; `GetPadstackNames(2, -1, "*", True)` lists the library's via padstacks (`026VIA`, `VC…`). `DefaultViaPadstack` takes other arguments ("无效的参数数目") |
| Via next to a pad | a via pad within the clearance of a surface-mount pad is refused **even for the same net** (SCL via 0.07 mm from U303.4's pad, VBAT via 0.1 mm from U301.1's); keep 0.254 mm to any pad the via does not sit inside |
| Test points | `TestPoint_Pad_D1.0mm` cells are surface-mount pads (`SMD-RND1`), so an inner-layer trace ending under one connects nothing and is a "Hangers" hazard; a via beside the pad with a top stub is the connection |
| `Net.NumberOfOpens`, `Pin.IsConnectedToPathOrAreaOnLayer(n)` | the open count per net and, per pin, whether copper on layer n touches it — enough to tell "pin not reached" from "two islands" |
| Unconnected pins | Layout shows them as net `(Net0)`; there is no such net in `Nets` (`PutTrace` for it is "the board has no such net") |
| `RoutePass` type 7 (remove hangers) | `PassType(7, 1, 3, False, False)` is "参数无效"; deleting the hanging trace (`pcb unroute --at`) and drawing it to the via instead is the fix |
| `Component.FabricationLayerTexts` | the cell's texts; `Type` 2 silkscreen, `TextType` 1 the designator; `Move(x, y, eUnit)` moves one, `Format.Orientation` turns with the part (a designator of a part at 90° stands upright), `Extrema` is its box |
| `RespectComponentPlacementDRC` | true makes `Component.Place` refuse a spot that touches another part — what `pcb move` wants; `pcb arrange` turns it off while it lifts and re-places everything |
| Trace-width hazards | the stock `(Default)` class allows exactly one width (min = typical = expansion = 10 th); a 0.3 mm stub is a `TraceWidths` hazard until the expansion width is raised (`pcb rules --class "(Default)" --expansion 0.5`) |

## Constraint Manager automation (net classes and trace widths)

| Fact | Detail |
|---|---|
| Server | `ConstraintsAuto` (late-bound `Dispatch`; `gencache.EnsureDispatch` fails with "can not automate the makepy process"); needs `SDD_HOME` and Layout's `PATH` set first, else "Cannot locate Mentor environment" |
| Loading | `auto.Design.CreateDesignParams()` → `ProjectFile`, `Board` (the design name, `Board1`), `DesignContext = 1` (layout; 0 answers "设计上下文丢失"), then `design.Load(params)` / `UnLoad()` |
| Objects | `design.NetClasses` (`Add(name)`, `Item(name)`, `NetClass.AssignNet(net)`), `design.Nets` and `design.PowerNets` (GND lives in the second; each net has `NetClass.Name`), `design.PhysicalRules.Schemes.Item(1)` = `(Master)` → `.NetClasses.Item(name).Layers` (`SIGNAL_1…4`) → `GetConstraints(7)` → constraints with `Name`, `Value`, `ConstraintType` (283 trace width minimum, 439 typical, 183 expansion). Values are thousandths of an inch (0.5 mm = 19.685) and settable; `nc.Constraints` on a net class answers "对象不可约束" |
| Layout sees it only after | `doc.ProjectIntegration.SynchCES` (returns true); `LoadCES` and reopening the board did not; afterwards `NetClass.TypicalTraceWidth(1, "(Master)", unit)` in Layout reports the new width and the router uses it |

## Manufacturing outputs (ODB++, Gerber, NC drill)

| Fact | Detail |
|---|---|
| No automation call | `IMGCPCBDocument` has `ExportAscii`, `ExportComponentData`, `GenerateEDMOutputs` and nothing for Gerber/ODB++/drill; the Output menu commands are `OUTPUT_ODBPP` 33017, `OUTPUT_GERBER` 33016, `OUTPUT_NCDRILL` 33018, `OUTPUT_SILKSCREEN` 33019 (Silkscreen Generator), `OUTPUT_NEUTRALFILE` 33773, `FILE_EXPORT_IPCD356B` 32938; `Gui.ProcessCommand` opens their dialogs (`ODB++ 设计输出`, `Gerber 输出`, `NC 钻孔生成`), OK is `确定(O)` / `确定` |
| Settings files | `Config/ODBSetup.ocf` (+ `.eocf`), `Config/PlotSetup.gpf` (+ `GerberPlot.egpf`), `Config/GerberMachineFile1.gmf`, NC drill scheme `Sys: DrillEnglish.dff`: HKP-style text. **The dialogs hold their settings in memory while the board is open and rewrite the files on OK**, so a file edited while the board is open is overwritten; edit it with the board closed and reopen |
| ODB++ | the stock setup leaves the drill span out (`..NAME "d_1_4"` / `...INCLUDE NO`) and the outline off (`.BOARD_OUTLINE NO`); with both on, the job has a `d_1_4` `TYPE=DRILL` layer with tools and hits and a `profile`. Job folder `Output/ODBpp/<OutputJobName lower-cased>/`, silkscreen `sst` complete, units inch |
| Gerber silkscreen | `..BoardItem SilkscreenTop` is accepted and writes a header-only file; the installation's sample setups draw the cells: `..CellType <each type>`, `..CellItemsSide Top`, `...CellItemsLayer 1` (bottom: `0`, which the dialog rewrites as the last layer number), `...CellItem SilkscreenOutline`, `...CellItem SilkscreenReferenceDesignator`. `GeneratedSilkscreen*` files need the Silkscreen Generator, whose per-package-group layer checklist is not exposed to UI Automation (clicks toggle blindly) |
| Gerber output | RS-274X, `Output/Gerber/*.gdo`, coordinates modal (a line may carry only X or Y); the stock 4-layer setup writes the outer copper twice (`EtchLayer1Top` and `EtchLayerTop`) and negative plane files for the inner layers |
| NC drill | `Output/NCDrill/ThruHolePlated.ncd` and `ThruHoleNonPlated.ncd`, Excellon 2.4 inch trailing-zero, modal coordinates |
| Files held after the run | NC drill leaves `PCB/LogFiles/DrillPrefs.txt` open in the Layout process after the document closes; `Application.Quit` puts Layout on its start page (`[首页]`) with the file still held, so `pcb create --replace` ends a document-less Layout by its process (`tasklist` / `taskkill`) when the folder will not go |
| Setups written back | a dialog that was opened earlier in the Layout session writes its in-memory settings over the patched file on OK even after the board was closed and reopened (the ODB++ job came out without `d_1_4` once); `pcb export` reads the setups again after the run and repeats the patch and the dialogs once (`rounds`) |
| 3D | `WINDOW_3D_VIEW` 33155 opens a `[3D View : <board>]` tab; view commands 53330–53338; `EXP3D_EXPORT` 53325 exports STEP/PDF/PNG; cells without a 3D model are drawn as their placement outline at the cell's height (0 for converted KiCad cells, so flat); `CONDUCTORVIEWRMB_VIEW_PHOTOREALISTIC` 53361 exists |

## KiCad footprints as cells

KiCad ships its footprint library as text (`share/kicad/footprints/<library>.pretty/*.kicad_mod`,
155 libraries and 15 450 footprints in KiCad 9 here) under CC-BY-SA 4.0 with the KiCad library
exception. `xpedition_cli.kicad_footprints` turns each `.pretty` folder into one cell partition
and `kicad_import` (`python -m xpedition_cli.kicad_import --project X.prj`) feeds them through
`HKP2PadstackDB` / `HKP2CellDB`; a design then names a footprint as its package
(`"packages": {"RES": "kicad:Resistor_SMD:R_0603_1608Metric"}`) and `library build` writes
parts that reference the cell and registers its partition in `LIST 2dCellLibraries`. The whole
library takes about a quarter of an hour. Facts that cost time:

| Fact | Detail |
|---|---|
| Grammar reference | `CellDB2HKP -i X.cel -o X.hkp -a` and `PadstackDB2HKP -i PadstackDB.psk -o X.hkp -a` export what the importers read; the stock `Drawing` and `Starpoints and Tiebars` partitions show text, arcs, circles, rectangles and filled shapes |
| Pad shapes | `..ROUND ...DIAMETER`, `..SQUARE ...WIDTH`, `..RECTANGLE`/`..OBLONG ...WIDTH ...HEIGHT`, `..RADIUS_CORNER_RECTANGLE ...WIDTH ...HEIGHT ...RADIUS`; holes `..ROUND ...DIAMETER` or `..SLOT ...WIDTH ...HEIGHT`. KiCad `roundrect` maps to the radius-corner rectangle, `oval` to oblong, `custom` to the rectangle around its primitives |
| Graphics | one path per outline block: `..SILKSCREEN_OUTLINE`/`..ASSEMBLY_OUTLINE`/`..PLACEMENT_OUTLINE` with `...SIDE MNT_SIDE` and `...POLYLINE_PATH` (`....WIDTH`, `....XY (x, y) …`), `...RECT_PATH` (two corners), `...CIRCLE_PATH` (`....XY`, `....RADIUS`) or `...POLYLINE_SHAPE` + `....SHAPE_OPTIONS FILLED`. Any number of silkscreen blocks survive; **only one assembly outline is kept per cell**, so the converter emits the largest closed loop of the fabrication drawing; the placement outline must be one closed shape |
| Reference designator | `..TEXT "Ref Des"` / `...TEXT_TYPE REF_DES` / `...DISPLAY_ATTR` with `....XY`, `....TEXT_LYR SILKSCREEN_MNT_LYR`, `....HEIGHT`, `....WIDTH`, `....STROKE_WIDTH`, `....ROTATION`, `....FONT "vf_std"` places the refdes at the cell's own size; a cell without it gets Layout's default text, which is what made the placeholder boards look like a sea of "C"s |
| Holes in package cells | `..MOUNTING_HOLE ...PADSTACK ...XY ...ROTATION` is accepted inside `.PACKAGE_CELL` (a `MOUNTING_HOLE` padstack: clearance pad, mask, hole, no pad); KiCad `np_thru_hole` pads and unnumbered plated pads become these |
| Mount type | `..MOUNT_TYPE MIXED` is accepted with any package group; the converter derives it from the pads that became pins |
| Cell names | at most **64 characters**: `HKP2CellDB` logs `无法添加单元 "…"。正在跳到下一个单元。` for longer ones and then **saves nothing** for the whole file (`遇到 N 个错误。将不会保存单元数据库文件。`, exit code 1). 685 KiCad names are longer; they are cut to 56 characters plus `~` and seven hex digits of a SHA-1 of the full name (`kicad_footprints.cell_name`), and `kicad_import` retries a partition once without any cell the log refused |
| Log noise | every converter log contains `正在检查文件格式错误...` and `未找到文件格式错误。` ("checking for file format errors… none found"); a log check that matches the word "error" alone reports success as failure. `_tool_log` matches a leading `错误`/`error`, `错误:`/`error:`, `无法添加`, `遇到 N 个错误` |
| Coordinates | KiCad's Y points down, Xpedition's up: every Y is negated; rotations are counter-clockwise on screen in both, so angles stay |
| Same pad number twice | a thermal pad with its paste windows and vias: the largest copper pad is the pin, the rest is dropped (a cell's pin count must equal its part's); paste-only, back-side and `connect` pads are dropped too |
| Merge | `HKP2PadstackDB … -m` and `HKP2CellDB … -m` add to what exists, replacing same-named entries and registering the partition in the `.lmc`; Layout and Designer may stay open with the project (Designer's project is closed and reopened by the adapter as for `library build`) |
