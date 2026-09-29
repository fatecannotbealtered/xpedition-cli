# Fabrication package

To send the board out: `pcb export --output DIR` (`--dry-run` then `--confirm`)
writes Layout's ODB++, Gerber (RS-274X) and NC drill outputs and gathers them
into `DIR`: `gerber/*.gbr` (empty and duplicate files left out), `drill/*.drl`,
the ODB++ job as a zip, `centroid.csv`, `bom.csv`, `README.md` for the board
house and `manifest.json` with `checks`. Report the package as ready only when
`checks.ok` is true; `problems` names what is missing: a format that was asked
for and produced nothing, an output that did not finish, a copper layer the
board's layer count needs (a two-layer board's bottom is `EtchLayer2Bottom`),
the masks, a silkscreen, the outline, a drill layer, and parts the BOM lists but
the board has not placed (`unplaced`). Files an earlier export left in Layout's
output folders are kept out of the package and listed under `stale`. The
centroid file gives each part's cell origin, which is not always its centre.
The first run on a board closes and reopens it in Layout to
patch the output setups; later runs do not. The README leaves board thickness,
finish and mask colour to the board house (`pcb export` takes no such options);
pass on any the person names separately.

`checks` covers what the package contains, not the board's design rules, so run
`pcb check` first: send only a board that passes (no DRC error), with no high or
medium board rule and every warning kind explained, as the Skill's first layout
says. The BOM for purchasing comes from the schematic (`bom export --group`, in
xpedition-schematic); the package's `bom.csv` lists what the board places.

```bash
xpedition-cli pcb check --project X.prj --compact
xpedition-cli pcb export --project X.prj --output ./fab --dry-run --compact
```

Confirm with the same arguments and the returned token.
