# Xpedition Capability Survey

English | [中文](APPLICATION_zh.md)

This survey records what Xpedition can do and how it can be automated. It is filled in before commands are designed: every domain command of xpedition-cli names the capability IDs it implements, and every capability has a disposition.

Source markers:
- [measured] Records of xpedition-cli 1.0.3 measured on Xpedition Standard XPED2604 (Windows 11), 12 to 30 September 2026. See `docs/E2E.md`, `docs/COMPATIBILITY.md` and `docs/NATIVE_ADAPTER.md` in the npm package `@fateforge/xpedition-cli@1.0.3`.
- [old code] The old implementation on the main branch of this repository (commit 4a054a3) and its CHANGELOG. Only the close diagnostics were run once on a real installation. The rest is retold from code comments and private runs, so it is less reliable than [measured].
- [this machine] The registry, the install directory and the command catalogs `SDD_HOME/standard/automation/Commands_*.csv` of the Xpedition Enterprise 2604 installed on this machine (`XENTP2604`, `COM_VERSION 151`).
- The installation on this machine has no automation reference manual (`docs/` holds only `legal`), so no vendor manual is cited below.

## Application

- Name: Xpedition (Designer draws schematics, Layout draws boards, Library Manager manages the central library)
- Vendor: Siemens EDA (formerly Mentor Graphics)
- Versions surveyed: Xpedition Standard XPED2604 [measured], Xpedition Enterprise XENTP2604 [this machine]; xpedition-cli supports 2604

The operating systems and CPU architectures on which Xpedition runs, or in device mode the device driver or SDK; the platform packages of xpedition-cli follow from this table:

| Operating system | CPU architectures |
|---|---|
| Windows | x64 |

Source: [this machine] the registry key `HKLM\Software\Mentor Graphics\Releases\MGC_1` says `SDD_PLATFORM win64`, and the install directory has only `win64`. Automation goes through Windows COM (`Viewdraw.Application`, `MGCPCB.ExpeditionPCBApplication`), which exists only on Windows. The vendor has no Windows arm64 build. To be confirmed: whether Xpedition also has a Linux build. Even if it does, Linux has no such COM interface, so this tool could not use it there either.

## Automation Interfaces

Every interface Xpedition offers, and where code that calls it must run. An interface the CLI can call is used from the adapter layer in `xpedition_cli/adapter/`. An interface that works only inside the process of Xpedition or on its main thread needs a host component in `plugin/`.

| Interface | Called from | Notes |
|---|---|---|
| Designer COM `Viewdraw.Application` (also a versioned `.151`) | CLI (out-of-process COM, pywin32) | Can only connect to an instance that is already running, with `GetActiveObject`. The Application object has no PID and no window handle, so an instance cannot be picked from the ROT [old code]. Some members can only be called by dispid [measured] [old code] |
| Layout COM `MGCPCB.ExpeditionPCBApplication` / `MGCPCB.Application` (`.151`, `.60`) | CLI (out-of-process COM) | Each document must first exchange an automation license token: `Validate(0)` → `MGCPCBAutomationLicensing.Application.GetToken` → `Validate(token)` [measured]. Properties with parameters must be read by dispid with `DISPATCH_PROPERTYGET` [measured] |
| Launchers `SDD_HOME/common/win64/bin/{viewdraw,ExpeditionPCB,LibraryManager}.exe` | CLI (starts a process) | Direct COM activation fails, and so does running the real programs under `wg/wv/win64/bin` directly (`CO_E_SERVER_EXEC_FAILURE`, `STATUS_DLL_NOT_FOUND`). The launcher PID is not the PID of the worker process [measured] [old code] |
| Constraint Manager COM `ConstraintsAuto` (`.151`) | CLI | Late binding only. `SDD_HOME` and the PATH of Layout must be set first. Changes take effect only after calling `ProjectIntegration.SynchCES` in Layout [measured] |
| Library Manager COM `LibraryManager.Application` | CLI | Read-only. It has no methods to create symbols, cells or parts [measured] |
| Library converters `PartsDB2HKP`, `CellDB2HKP`, `PadstackDB2HKP` (export) and `HKP2PartsDB`, `HKP2CellDB`, `HKP2PadstackDB` (import) | CLI (command line, no running instance needed) | They are GUI programs: a wrong argument or a license problem pops up a message box, and no exit code is returned. The logs contain Chinese. `LMCDB2HKP` hangs, so it is not used [measured] |
| `sch2pdf.exe` | CLI (command line) | Makes a PDF directly from the project database. It works even while Designer has the project open [measured] |
| `package.exe` | CLI (command line) | Packages without a user interface; the result is read from `Integration/PartPkg.log`. It must be started through `common/win64/bin` [measured] |
| `JobWizard.exe -createnew` | CLI (command line) | Creates a board from a layout template. Designer is not needed [measured] |
| Menu commands `Gui.ProcessCommand(id or name)` (Layout), `ExecuteCommandByID` (Designer) | CLI (through COM) | The command catalogs are `Commands_XpeditionLayout.csv` (1041 entries) and `Commands_Xpedition_Designer.csv` [this machine]. Batch DRC, Gerber, ODB++ and NC drill can only go this way, and they open dialogs [measured] |
| File formats: `.prj`, symbol text `SymbolLibs/*/sym/<name>.<version>`, HKP text, setup files `Config/*.ocf/.gpf/.dcs` | CLI (reads and writes files) | Symbol files of `V 53` use sheet units; `V 54` uses 10 nm [measured] |
| `LayoutDB2HKP`/`HKP2LayoutDB`, `NetClassDB2HKP`/`HKP2NetClassDB` | CLI (command line) | Present on this machine [this machine]; never tried |
| Designer startup script (VBS in `scripts.ini`, runs inside the Designer process) | Only inside the process | The old code used it to hand over Designer's own Application object, to confirm which instance it was [old code]. COM works without it |

## Object Model

- Project (`.prj`): a project has several Designs (the schematic block `Board1`, the board design `ConfigType "PCB"`), one Central Library (`.lmc`), and the partition lists `LIST Symbols`, `LIST PDBs` and `LIST 2dCellLibraries`.
- Central library:
  - Part (Parts DB `.pdb`): part number, description, Value, attributes, symbol, pin map.
  - Symbol: a text file.
  - Cell (footprint, Cell DB `.cel`).
  - Padstack (`PadstackDB.psk`), Pad, Hole.
  - All of these are stored by Partition.
- Schematic (Designer): Schematic → Sheet → Component (a part instance, identified by a reference designator such as `R1`; placed with `AddPartInstance` and drawn on the sheet as a symbol), Net (a name or a UID such as `$2N121`), Pin (such as `R1.1`), Attribute, Label (net label), Wire.
- Board (Layout, `PCB/<name>.pcb`):
  - Board Outline, Route Border, Manufacturing Outline.
  - Component (reference designator) with its Pins and Pads.
  - Net, Net Class, Trace, Via, Plane Shape (copper pour), Mounting Hole, Obstruct (keep-out area).
  - Fabrication Layer Text (silkscreen and assembly layer text, such as reference designators), Hazard (DRC result), Display Scheme, Layer Stack.
- Fabrication outputs: ODB++ job, Gerber (RS-274X), NC Drill (Excellon), placed under `Output/` in the board folder.

## Units and Coordinates

- Schematic: one sheet unit = 10 mil = 0.254 mm, so a 100 mil grid = 10 units. In a `V 54` symbol file one grid step is 254000 (10 nm). The origin is at the lower-left corner of the sheet border, and y points up. Component orientations 0–3 = counterclockwise 0°/90°/180°/270°; 4–7 are mirrored [measured].
- Board: most COM methods take a unit parameter `EPcbUnit` (0 current, 2 mil, 3 inch, 4 mm, 5 µm). This tool always passes 4 (mm). Members without a unit parameter (`PointsArray`, `LineWidth`, text sizes) return values in the current unit of the document; the default template uses mil (`CurrentUnit 2`). Trace width `Geometry.LineWidth` is in th (thousandths of an inch). In the default 4-layer template the origin is at the lower-left corner of the board, x points right and y points up. Angles are in degrees [measured] [old code].
- Constraint Manager line-width values are in th (0.5 mm = 19.685) [measured].
- HKP: cells and padstacks use `.UNITS MM`; parts use `th` (parts have no geometry) [old code].
- Arcs: each row of `PointsArray` is (x, y, r). A row with r ≠ 0 is the arc center; a positive r means clockwise, a negative r counterclockwise [measured].

## Threading and Blocking

- Out-of-process COM calls are queued onto the main thread of the program. While a modal dialog is open, the call does not return. So the old code used a helper thread to read dialogs and buttons through UI Automation (pywinauto) [measured].
- Known dialogs, from [measured] [old code]; in a Chinese user interface the titles are in Chinese:
  - When Layout opens a board: determining the design status took more than 15 seconds (Open/Cancel), database recovery (OK/Cancel), "新更改已经可以进行正向标注" (new changes are ready for forward annotation) (Yes/No). Opening another board while the current board has changes shows a Save All modal dialog.
  - When Designer changes projects: changing the current project requires closing all open documents (Yes/No). After a timeout Designer shows "close all open documents?".
  - Outputs and checks: the Batch DRC, NC Drill Generation, ODB++ design output and Gerber output dialogs, and the Processing… progress window.
  - The Library Services window; the save prompt (four buttons: save all, save library only, don't save, cancel).
  - License and sign-in: the Siemens license sign-in dialog, which only a person can complete.
- Do not press the default button of every `#32770` in the process: that closes the packaging progress dialog, and forward annotation then fails [measured].
- While the desktop is locked, window screenshots are all black; UI Automation still works [measured].
- Signs of being busy: COM calls are rejected (`RPC_E_CALL_REJECTED` and similar), and a Processing window appears. Whether the program has another "busy" flag: to be confirmed.

## Read-back

- Schematic: component positions, attributes and net members can all be read back through COM and compared exactly in sheet units. After the whole schematic is drawn, the project must be closed and reopened before the correct design and netlist can be read: after sheet operations `GetActiveDesign` returns the schematic instead of the block, and `DesignComponents` reports a type mismatch [measured]. For symbols without a reference designator (power, ground, no-connect markers) the symbol name cannot be read; they can only be judged by geometry [measured].
- Board: component positions are read back in mm with `GetPositionX/Y(4)` and equal the plan exactly [measured]. The positions of the board outline, mounting holes, plane shapes, traces, vias and reference designator text can all be read back. Slots have no drill size and are read by length [measured]. Line-width rules are read-only in Layout (`NetClass.*TraceWidth`) [measured].
- Central library: export again with the converters and compare by content (ignoring timestamps and swap group names). Value is compared as a number (`10k` is stored as `10K`). Text is compared in the system code page [measured].
- Fabrication files: read the files themselves (Gerber header, Excellon, the ODB++ directory) and record their size and SHA-256 [measured].
- PDF: read `Printed … Sheet n` and the page size from the `sch2pdf` output [measured].

## Save and Undo

- Designer: `Documents.SaveAll` (`ActiveDocument.Save` saves only the current sheet); `CloseProject()` must return True [measured] [old code]. While the Designer iCDB service has the project open, it locks `database/icdb.dat`, so the project must be closed before a backup [measured]. Save as: there is a Save As command (57604); it has never been used.
- Layout: a save counts only when `Document.Save()` returns True. After `Close(True)`, `ActiveDocument` should be None [old code]. Save as: there is only `FILE_SAVE_COPY`, which opens the JobWizard copy dialog [this machine].
- Unsaved state: whether the COM of either side exposes "has unsaved changes": to be confirmed; the old code never read it. The description of the Designer Exit command says "prompts to save when there are pending changes", so the program knows internally [this machine].
- Undo: both sides have the menu command Undo (57643). The Layout description says it can undo back to the last save [this machine]. Whether it works on changes made by automation: to be confirmed. Layout has `TransactionStart`/`TransactionEnd`; whether `TransactionEnd(False)` can roll back: to be confirmed [old code].
- The central library, `.prj`, `sch2pdf`, `package.exe` and JobWizard write files directly. Once written, the change is saved.

## Instances and Concurrency

- Layout: get the PID with `Gui.HWND` → `GetWindowThreadProcessId`, and the board file with `ActiveDocument.FullName` [old code]. When several Layouts are open at once, `GetActiveObject` returns only one of them, and which one cannot be chosen [old code].
- Designer: the Application has no PID and no window. The PID can be taken from a view only while a sheet window is open. Otherwise the tool can only assume that the connected instance is the Designer worker process, and only when there is exactly one on the machine [old code]. So when two Designers are open at once, the intended one cannot be driven reliably.
- A person working in the software at the same time: the program has no change counter, so the tool cannot see what the person changed. It can only infer this from file times and its own change records (see the risks below).
- Designer and Layout sharing one project: if Designer closes the project while Layout has the board open, the database session of Layout breaks ("iCDB error getting UID manager") [measured].

## Licensing

- License sources: the environment variables `MGLS_LICENSE_FILE`, `SALT_LICENSE_SERVER` and `LM_LICENSE_FILE` (inherited from HKCU or HKLM when the process does not have them) [old code].
- Layout automation must exchange a token for each document (see above). When this step is missed, the EXCEPINFO of the error has product code 10279 and scode `0x8004022D`. The message is localized Chinese, so matching on the text alone is not reliable [old code].
- When Designer starts, it may stop at the Siemens license sign-in dialog, which only a person can complete [measured].
- Which license feature automation needs: to be confirmed; ask the user.
- License information (server addresses, license files) does not go into the repository and does not appear in output.

## Capabilities

Each capability has one row with an ID `CAP-nnn`, unique in this survey, and one disposition: `provided` (a domain command offers it), `deferred` (worth offering, left for a later version), `excluded` (will not be offered, for example because it is unsafe or cannot be automated reliably) or `unknown` (not yet known whether Xpedition allows it). Every row names the interface that reaches the capability; a row other than `provided` gives the reason in one line.

| ID | Capability | Interface | Disposition | Reason |
|---|---|---|---|---|
| CAP-001 | Read the project file: designs, board path, central library, partitions | `.prj` text | provided | - |
| CAP-002 | Copy a new project from a template project and rewrite the library paths | Files | provided | - |
| CAP-003 | Initialize a project from a template shipped with the installation, with Designer `NewProject` | Designer COM | unknown | Written only in the old code; it never passed on a real installation |
| CAP-004 | Back up the project folder as a ZIP | Files | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-005 | Restore the project folder from a ZIP backup | Files | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-010 | List the parts in the central library | `PartsDB2HKP` | provided | - |
| CAP-011 | Read everything about one part: pin map, symbol and its pins, cell pads and padstacks, problems | Converters + symbol files | provided | - |
| CAP-012 | List the cells in the central library | `CellDB2HKP` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-013 | Read one cell and the parts that use it | `CellDB2HKP`, `PadstackDB2HKP` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-014 | Check the consistency of the central library (13 rules) | Converters + symbol files | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-015 | Create a part from a part file: symbol, IPC-7351B footprint, padstacks, pin map | `HKP2*DB` + symbol files + `.prj` | provided | - |
| CAP-016 | Generate placeholder parts for missing parts from a design description | Same as above | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-017 | Import parts from another Xpedition central library (with their symbols, cells and padstacks) | Converters | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-018 | Draw a part preview image (symbol on the left, cell on the right) | Pure Python | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-019 | Create or change library objects with Library Manager COM | Library Manager COM | excluded | Its object model is read-only |
| CAP-020 | Import KiCad footprint libraries | Converters | excluded | Removed in 1.0.3; the library does not depend on KiCad |
| CAP-021 | Create a partition and register it in `.lmc` | Converters / Partition Editor | unknown | The old code contradicts itself; there is no evidence from a real installation |
| CAP-022 | Generate cells with PCB Footprint Expert | `lm_fpe` | excluded | A GUI tool with no automation interface |
| CAP-030 | Read the schematic: sheets, component count, net count | Designer COM | provided | - |
| CAP-031 | Read schematic components: reference designator, part number, value, sheet, attributes, each pin and its net | Designer COM `DesignComponents` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-032 | Read schematic nets and their pins | Designer COM | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-033 | Plan a schematic from a design description and draw a preview PNG (without Designer) | Pure Python | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-034 | Draw a whole schematic from a design description: clear each sheet, place components, draw wires, labels and titles, save, reopen and compare the netlist read back | Designer COM | provided | - |
| CAP-035 | Place a component | `AddPartInstance` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-036 | Move a component | `SetLocation` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-037 | Delete a component, together with the wires that belong only to it | `DeleteSelected(True)` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-038 | Set component attributes and how they are displayed | `FindAttribute`, `AddAttribute` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-039 | Connect and disconnect pins, create nets | `AddNet`, labels | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-040 | Rename a net (all its labels on every sheet change together) | Label `TextString` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-041 | Run Designer verification (Full/Quick Verification) and parse `vdrc.log`, `grc.log` | `ExecuteCommandByID(34155/46461)` | provided | - |
| CAP-042 | Export the schematic to PDF | `sch2pdf.exe` | provided | - |
| CAP-043 | Package the schematic for Layout | `package.exe` | provided | - |
| CAP-044 | BOM check: missing part numbers, duplicate reference designators, one part number with different values | Schematic reads | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-045 | Export a BOM (optionally grouped, optionally compared with a baseline) | Schematic reads | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-046 | Activate a sheet, fit the window, capture an image of the Designer window | Designer COM + GDI | deferred | Only for people to look at; screenshots are all black while the screen is locked; the PDF and preview images already show the drawing. To be reconsidered later |
| CAP-047 | Back annotation | `ProjectIntegration.BackAnnotate` | unknown | Never tried |
| CAP-060 | Create a board from a layout template with JobWizard | `JobWizard.exe` | provided | - |
| CAP-061 | Delete and rebuild a board, after first packing the board folder into a backup | Files + JobWizard | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-062 | Forward annotation: bring the packaged schematic into the board | `ProjectIntegration.ForwardAnnotate` | provided | - |
| CAP-063 | Read the board: counts, layer stack, net classes and line widths, obstructs, whether each net is fully routed | Layout COM | provided | - |
| CAP-064 | Read the board geometry: component outlines, pins, pads, traces, vias, plane shapes, holes, silkscreen | Layout COM | provided | - |
| CAP-065 | Set a rectangular board outline (rounded corners optional), replacing the route border and the manufacturing outline at the same time | `PutBoardOutline` and others | provided | - |
| CAP-066 | Place non-plated mounting holes at the four corners; delete mounting holes | `PutMountingHoleEx` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-067 | Create net classes, assign nets, set line widths | `ConstraintsAuto` + `SynchCES` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-068 | Automatic placement by connectivity (clusters per sheet, area labels) | `Component.Place` + planner | provided | - |
| CAP-069 | Move a component to a given position and angle (top side) | `Component.Place/Move` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-070 | Place a component on the bottom side | `Place(bTop=False)` | unknown | Never tried; `Side` is read-only |
| CAP-071 | Move a silkscreen reference designator to a free spot, or to a given position | `FabricationLayerText.Move` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-072 | Copper pour (plane shapes) | `PutPlaneShape`, `PlaneAssignment` | provided | - |
| CAP-073 | Autoroute: chosen nets, layers and route passes | `NewRoutePass` | provided | - |
| CAP-074 | Draw traces from a plan | `PutTrace` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-075 | Place vias, including stitching vias for ground nets | `PutVia`, `PutPadstack` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-076 | Delete traces and vias (a whole net, or those at one point) | `Delete()` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-077 | Batch DRC, and read out the hazards | `Gui.ProcessCommand(32769)` + dialog + `GetHazards` | provided | - |
| CAP-078 | Placement and routing metrics, and board-level rules (ratsnest length, crossings, distance from decoupling capacitors to power pins) | Pure Python | provided | - |
| CAP-079 | Draw a board image PNG from the geometry (top side, bottom side) | Pure Python | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-080 | Switch the display scheme, fit the board, capture an image of the Layout window | `DisplayControl.LoadScheme` + GDI | deferred | Only for people to look at; screenshots are all black while the screen is locked; `pcb render` already shows the board. To be reconsidered later |
| CAP-081 | Fabrication files: ODB++, Gerber, NC drill, plus a coordinate file, BOM, notes and a manifest | `Gui.ProcessCommand(33016–33018)` + setup files + dialogs | provided | - |
| CAP-082 | Refresh the cells on the board (Library Services local library update) | `SETUP_LIBRARYSERVICES`, `ECO_UPDATELOCALLIBRARYDATA` | unknown | Written only in the old code; needs an interactive desktop |
| CAP-083 | Set clearance rules | `ConstraintsAuto` | unknown | Never tried |
| CAP-084 | 3D view and STEP export | `EXP3D_EXPORT` 53325 | unknown | Never tried |
| CAP-085 | IPC-D-356 netlist export | `FILE_EXPORT_IPCD356B` 32938 | unknown | Never tried |
| CAP-086 | Generate silkscreen with Silkscreen Generator | `OUTPUT_SILKSCREEN` 33019 | excluded | The layer check boxes of each package group are not exposed to UI Automation, so they could only be clicked blind |
| CAP-087 | Convert the board database to and from text (read the board without opening Layout) | `LayoutDB2HKP`/`HKP2LayoutDB` | unknown | Present on this machine; never tried |
| CAP-088 | Delete plane shapes | `Delete()` | deferred | The first version includes only the steps needed to make a board; a later version will provide it |
| CAP-100 | List running Designer and Layout instances, and the projects and boards they have open | Process enumeration + COM | provided | Provided by the standard commands `session` and `doc`, not by a domain command (the spec defines provided only for domain commands; see the SPEC issue log) |
| CAP-101 | Start Designer or Layout through the launcher | Launcher | provided | Provided by the standard commands `session` and `doc`, not by a domain command (the spec defines provided only for domain commands; see the SPEC issue log) |
| CAP-102 | Close an instance | `Quit` | provided | Provided by the standard commands `session` and `doc`, not by a domain command (the spec defines provided only for domain commands; see the SPEC issue log) |
| CAP-103 | Open a project (Designer) or a board (Layout) | `OpenProject`, `OpenDocument` | provided | Provided by the standard commands `session` and `doc`, not by a domain command (the spec defines provided only for domain commands; see the SPEC issue log) |
| CAP-104 | Save | `Documents.SaveAll`, `Document.Save` | provided | Provided by the standard commands `session` and `doc`, not by a domain command (the spec defines provided only for domain commands; see the SPEC issue log) |
| CAP-105 | Close a project or a board | `CloseProject`, `Document.Close` | provided | Provided by the standard commands `session` and `doc`, not by a domain command (the spec defines provided only for domain commands; see the SPEC issue log) |
| CAP-106 | Undo | Undo command 57643 | unknown | Unknown whether it works on changes made by automation |
| CAP-107 | Save as | Designer Save As; Layout has only the JobWizard copy | unknown | Never tried |

## Risks

Each risk or limit found in the survey becomes an entry in `knowledge/`, or gives here the reason it is left out.

- The installation comes without a part library: the template library is an empty skeleton, and placing `Resistors:R` reports "not found, empty or a block". → `xpedition-template-library-is-empty`
- COM activation must go through the launcher; direct activation reports `CO_E_SERVER_EXEC_FAILURE`. → `xpedition-start-through-launcher`
- After a cleanup tool damages the win32com cache, connecting reports `CLSIDToClassMap`. → `xpedition-gen-py-cache-broken`
- When the project or the central library is under a non-ASCII path, newly written symbols cannot be placed. → `xpedition-ascii-paths-only`
- The license sign-in dialog, and license token failures with product code 10279. → `xpedition-license-sign-in`
- The three questions when Layout opens a board (design status, database recovery, whether to forward annotate), and the Save All modal dialog. → `layout-open-prompts`
- The question when Designer changes projects, and "close all open documents?" after a timeout. → `designer-project-prompts`
- Do not press the default button of every dialog in the process, or forward annotation fails in the packaging step. → Written into the adapter code, not made an entry: it is an implementation rule that agents have no use for.
- When two Designers are open at once, there is no way to tell which one is connected; Designer has no PID. → `designer-one-instance-only`
- After sheet operations, the project must be closed and reopened before the design reads correctly. → `designer-reopen-after-sheet-changes`
- Symbol cache: after a symbol is changed under the same name, Designer does not pick up the change; rename it or raise its version. → `designer-symbol-cache`
- Power and ground symbols must be type 4 (`Y 4`), or `NETNAME` has no effect. → `designer-power-symbols`
- A pin that lands on a wire corner does not connect. → `designer-pin-on-wire-corner`
- Chinese text written into the project is stored in the system code page, and it comes out garbled in the PDF made by `sch2pdf`. → `designer-chinese-text-in-pdf`
- The `drc-BI-POWER/GROUND` verification rules, and how to use the pin type `ANALOG`. → `designer-pin-types-and-erc`
- The pin count of a cell must equal the pin count of the part, or Database Load aborts. → `cell-pin-count-must-match`
- When `.prj` lacks `LIST 2dCellLibraries`, forward annotation cannot find cells. → `prj-cell-library-list`
- Forward annotation does not replace the cells of components already on the board; the only way is to rebuild the board. → `forward-annotation-keeps-old-cells`
- Asking for forward annotation when the design is already in sync reports 1001. → `forward-annotation-in-synch`
- Routing rules are read-only through Layout's own interface. Line widths must go through Constraint Manager, followed by `SynchCES`. → `layout-trace-widths-through-cm`
- With the default clearance of 0.254 mm, fine-pitch cells with a 0.65 mm pitch cannot be routed, and the router does not say why. → `layout-fine-pitch-clearance`
- Outer-layer plane shapes must be poured after routing, or the ground pins get cut off. → `layout-pour-after-routing`
- A newly built plane shape is in the Draft state; it must be switched to Dynamic before copper is generated. → `layout-plane-draft-state`
- Online DRC refuses to place a component on another component or on its own old position; the component must be lifted first. → `layout-placement-drc`
- Unplaced components have no pins and are not shown; the default display scheme does not show top-side components. → `layout-unplaced-parts-invisible`
- A via closer to a pad than the clearance is refused, even on the same net; test points are surface-mount pads. → `layout-via-near-pad`
- The fabrication output dialogs write the settings in memory back to the files, so settings must be changed while the board is closed. By default ODB++ has no drill layers and no board outline. → `layout-output-setups`
- After NC drill has run, Layout keeps holding `DrillPrefs.txt`, so the board folder cannot be deleted. → `layout-drillprefs-held`
- After opening a board, Enterprise 2604 keeps a temporary hold on PartsDB; opening and closing Library Services once releases it. → `layout-library-services-reservation` (to be written after it is confirmed on a real installation)
- Bottom-side placement has never been tried. → No entry: the capability table says unknown, and no command is offered.
- Central library converters: when a cell name is longer than 64 characters, the whole file is not saved; without `-r`, `HKP2PartsDB` silently drops parts with the same part number; the log message "未找到文件格式错误" (no file format errors found) is not an error. → `library-converter-rules`
- The Value of a part accepts only a number with an SI multiplier; any other text is stored as 0. → `library-part-value-is-numeric`
- Partition names can use only ASCII letters, digits, spaces and `_ . -`. → Merged into `xpedition-ascii-paths-only`
- A person editing in the software at the same time: the tool cannot see the internal change counter of the program, so "external changes" in the save preview can only be inferred from file times. → `xpedition-external-edits-not-tracked`
- Measured on only one machine and one version (2604); other versions may differ. → No entry: it is written into the support matrix in `docs/COMPATIBILITY.md`.
