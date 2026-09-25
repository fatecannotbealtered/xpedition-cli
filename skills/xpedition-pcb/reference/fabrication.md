# Fabrication package

To send the board out: `pcb export --output DIR` (`--dry-run` then `--confirm`)
writes Layout's ODB++, Gerber (RS-274X) and NC drill outputs and gathers them
into `DIR`: `gerber/*.gbr` (empty and duplicate files left out), `drill/*.drl`,
the ODB++ job as a zip, `centroid.csv`, `bom.csv`, `README.md` for the board
house and `manifest.json` with `checks`. Report the package as ready only when
`checks.ok` is true; `problems` names what is missing (a silkscreen, an outline,
a drill layer). The first run on a board closes and reopens it in Layout to
patch the output setups; later runs do not. The README's board thickness,
finish and mask colour are the board house's defaults unless the person says
otherwise.

`checks` covers what the package contains, not the board's design rules, so run
`pcb drc` first: a board that does not pass is not ready to send.

`manufacturing artifacts` and `manufacturing verify` read the artifact records a
project stores, and `manufacturing bom` lists BOM rows built from its
components; none of them produces files.

```bash
xpedition-cli pcb drc --backend native_xpedition --project X.prj --compact
xpedition-cli pcb export --backend native_xpedition --project X.prj --output ./fab --dry-run --compact
```

Confirm with the same arguments and the returned token.
