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
not registered, first try the current-user helper (no elevation required):

```powershell
# Substitute the SDD_HOME of the installed release, for example
# D:\Xpedition\home\XPED2604\SDD_HOME
$env:XPEDITION_SDD_HOME = "<install>\home\<release>\SDD_HOME"
powershell -ExecutionPolicy Bypass -File .\scripts\register-xpedition-user.ps1
```

If the current-user registration is rejected by the local policy, run
`scripts/register-xpedition.ps1` from an elevated PowerShell. The helper then
invokes the installation's own `registrator.exe`:

```powershell
$env:SDD_HOME = "<install>\home\<release>\SDD_HOME"
$env:SDD_PLATFORM = "win64"
$env:SDD_VERSION = "<release>"
Set-Location "$env:SDD_HOME\..\win64"
& "$env:SDD_HOME\common\win64\_bin\registrator.exe" "-version=$env:SDD_VERSION"
```

Use the paths for the installed release. A successful registration is visible
without opening a project:

```powershell
xpedition-cli doctor --compact
xpedition-cli system license --compact
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

The current bridge implements `health`, `start`, `attach`, `open`,
`snapshot`, `save`, `close`, controlled component placement/move and
schematic net operations used by `apply_changeset`, and the project-level
methods behind the guarded commands: `clone_project`, `draw`, `show`,
`verify`, `export_pdf`, `package`, `library_import`, `kicad_import` (every KiCad
`.pretty` footprint library as a cell partition, through the stock HKP converters;
`python -m xpedition_cli.kicad_import`), `pcb_create` (JobWizard's command line),
`forward_annotate` (Layout's Project Integration), `board_outline` (rounded corners
through the points array), `mounting_holes` (`PutMountingHoleEx`), `arrange_components`,
`plane_pour` (`bRouteObstruct` false, so the copper flows around traces), `route_board`
(`LayerSelect` for the inner layers; a second round after the planes regenerate),
`net_rules` (a net class and its trace widths through the `ConstraintsAuto` server, then
`ProjectIntegration.SynchCES`), `render_board` (the board's geometry drawn to a PNG by
`xpedition_cli.board_render`), `board_geometry` (the same data as JSON), `hand_route`
(`PutTrace` / `PutVia` where the person says), `unroute_nets` (by net, all, or one item
at a point), `move_component` (with the placement DRC on), `tidy_labels` (silkscreen
designators moved beside their parts), `batch_drc` and `manufacturing_output` (the ODB++ /
Gerber / NC drill dialogs with their setups patched while the board is closed, gathered
into a package folder). It
supports the PCB `MGCPCB.ExpeditionPCBApplication` and schematic
`Viewdraw.Application` COM classes. The bridge attaches to an already running
Xpedition process when possible; otherwise `start` or `open` creates one
through the launcher. Layout's own questions while a board opens (a stale lock,
database recovery, the offer to forward-annotate) are answered from a helper
thread through UI Automation, which needs the optional `pywinauto` package
(`pip install "xpedition-cli[native]"`); without it those prompts wait for a
person.

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
is assigned, and neither the component nor the block exposes a delete entry
point in this API. When the refdes assignment fails — a power or ground symbol
rejects one outright — the instance cannot be rolled back. The adapter reports
that case with `orphan_placed`, the library/device/symbol it came from and the
coordinates, so the caller can remove it in Designer; it never reports the
operation as applied.

## End-to-end evidence

Native E2E still requires a disposable project in a licensed Windows
environment. The first smoke run places `R1` and `C1`, creates the `3V3` net,
connects `R1.1` to `C1.1`, saves, closes, reopens and compares the reread
snapshot; it is recorded in [`E2E.md`](E2E.md), together with the runs that took
a project from an empty schematic to a fabrication package.

Attach, snapshot, placement and coordinate read-back have each been verified
against a running Designer session. The smoke loop itself is blocked by the
empty stock component library rather than by the adapter — see
[`COMPATIBILITY.md`](COMPATIBILITY.md).
