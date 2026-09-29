# Native Xpedition adapter

The repository includes an opt-in adapter for the public Xpedition PCB
automation interface. It uses the `MGCPCB.ExpeditionPCBApplication` and
`MGCPCBAutomationLicensing.Application` COM classes documented by the
installation's own automation examples. The adapter never edits Xpedition
private databases and does not bypass licensing.

## Install and configure

Install the optional Windows dependency from the checkout:

```powershell
python -m pip install -e ".[native]"
```

`xpedition-native-adapter` is installed as a console entry point. The CLI
discovers it automatically when it is on `PATH`; `XPEDITION_NATIVE_COMMAND`
can be set to an explicit adapter executable when several installations are
present. Set `XPEDITION_SDD_HOME` when the installation cannot be found from
`SDD_HOME` or the `MGLS_LICENSE_FILE` location.

The Xpedition automation classes must be registered by the product's official
post-install step. If `xpedition-cli doctor` reports that COM automation is
not registered, first try the current-user helper (no elevation required). The
`scripts/` helpers ship in neither the wheel nor the npm package, so run them from
a clone of this repository:

```powershell
# Substitute the SDD_HOME of the installed release, for example
# D:\Xpedition\home\XPED2604\SDD_HOME
$env:XPEDITION_SDD_HOME = "<install>\home\<release>\SDD_HOME"
powershell -ExecutionPolicy Bypass -File .\scripts\register-xpedition-user.ps1
```

If the current-user registration is rejected by the local policy, run
`scripts/register-xpedition.ps1` from the clone in an elevated PowerShell. The
helper then invokes the installation's own `registrator.exe`:

```powershell
$env:SDD_HOME = "<install>\home\<release>\SDD_HOME"
$env:SDD_PLATFORM = "win64"
$env:SDD_VERSION = "<release>"
Set-Location "$env:SDD_HOME\..\win64"
& "$env:SDD_HOME\common\win64\_bin\registrator.exe" "-version=$env:SDD_VERSION"
```

Use the paths for the installed release. A successful registration is visible
without opening a project, in the `xpedition` check of:

```powershell
xpedition-cli doctor --compact
```

The CLI keeps the native status unavailable until both the adapter and the
COM registration are present. It reports license failures as `E_AUTH` and
does not claim that a license is valid merely because a license file path is
configured.

## Adapter protocol

The adapter receives one JSON object on stdin and returns one CLI envelope on
stdout. Diagnostics belong on stderr:

```json
{"method":"health","params":{}}
```

```json
{"ok":true,"schema_version":"1.0","data":{"ready":true},"meta":{"duration_ms":0}}
```

The bridge implements `health`, `start`, `attach`, `open`, `save`, `close`,
`snapshot` (the schematic or the board as the CLI's project model), and the
methods behind the commands:

| Area | Methods |
|---|---|
| Projects | `clone_project` (`project create`), `release_project` and `reopen_project` (Designer and Layout let go of a project for `project backup` and `project restore`) |
| Library | `library_export` (a project's central library, or one named by its `.lmc`: the parts, cell and padstack databases as HKP text through `*DB2HKP -a -u mm`, cached until a database changes), `library_import` (`library build`, `library add` and `library import`: the padstack text, then each partition's symbol files -- a replaced one as its next version --, cells and parts merged through `HKP2*DB`, `-r` only when a part is to be replaced, the `.prj` lists updated), `package` |
| Schematic | `draw` (every partition a placed part comes from listed in the `.prj`), `apply_changeset` (`schematic edit`: place, move and delete a part with the wires only it used, set a property, create a net, connect and disconnect a pin, rename a labelled net and its label boxes), `verify` (Designer's verification), `show`, `export_pdf` |
| Board | `pcb_create` (JobWizard's command line), `forward_annotate` (Project Integration), `board_outline`, `mounting_holes` (only padstacks the library has), `net_rules` (`ConstraintsAuto`, then `SynchCES`), `arrange_components`, `placement_batch` and `move_component` (with the placement DRC on, each part read back), `tidy_labels`, `plane_pour`, `route_board` (`RoutePass` per pass; `LayerSelect(3, n)` removes the layers a pass may not use; `Items(8)` routes the selected nets), `hand_route`, `unroute_nets`, `board_info` (stackup, net classes, keepouts), `board_geometry` (optionally narrowed to parts or nets), `render_board`, `show_board`, `batch_drc`, `manufacturing_output` |

It supports the PCB `MGCPCB.ExpeditionPCBApplication` and schematic
`Viewdraw.Application` COM classes. The bridge attaches to an already running
Xpedition process when possible; otherwise `start` or `open` creates one
through the launcher. Layout's own questions while a board opens (a stale lock,
database recovery, the offer to forward-annotate) are answered from a helper
thread through UI Automation, which needs the optional `pywinauto` package (the
`native` extra, installed from a checkout as above or with
`python -m pip install "xpedition-cli[native] @ git+https://github.com/fatecannotbealtered/xpedition-cli"`);
without it those prompts wait for a person.

Prefer starting through the product launcher rather than through COM. Every
Xpedition `LocalServer32` entry points at the real binary under
`SDD_HOME/<product>/win64/bin`, and those binaries need the release environment
that the small launcher in `SDD_HOME/common/win64/bin` establishes. COM
activation bypasses the launcher, so `CoCreateInstance` fails with
`CO_E_SERVER_EXEC_FAILURE` (0x80080005); `start` therefore launches the launcher
binary and then attaches. The adapter maps that COM status to
`E_BACKEND_UNAVAILABLE` with a launcher hint instead of a bare server error.

All document reads request an automation license token using the same
`Validate(0)` → `GetToken` → `Validate(token)` sequence as the vendor
examples. A failed token exchange is returned as a structured error.

## Placement is not transactional

`AddPartInstance` puts the symbol on the sheet before the reference designator
is assigned. When the refdes assignment fails — a power or ground symbol rejects
one outright — the instance is on the sheet without a name. The adapter reports
that case with `orphan_placed`, the library/device/symbol it came from and the
coordinates; it never reports the operation as applied. An orphan is removed by
selecting it and `Block.DeleteSelected`, which is how `schematic edit` deletes a
part.

## End-to-end evidence

Native E2E still requires a disposable project in a licensed Windows
environment. The first smoke run places `R1` and `C1`, creates the `3V3` net,
connects `R1.1` to `C1.1`, saves, closes, reopens and compares the reread
snapshot; it is recorded in [`E2E.md`](E2E.md), together with the runs that took
a project from an empty schematic to a fabrication package.

Attach, snapshot, placement and coordinate read-back have each been verified
against a running Designer session. The smoke loop itself passed on 2026-09-12
against a hand-built library: the installation ships no component library, so the
`R` and `C` symbols were authored into a writable copy of its empty template
library — see [`COMPATIBILITY.md`](COMPATIBILITY.md).
