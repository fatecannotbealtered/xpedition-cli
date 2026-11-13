---
id: layout-output-setups
title: Fabrication output setups and the dialogs that rewrite them
kind: guide
applies_to: [pcb export, doc close, doc save]
tags: [layout, fabrication, odb, gerber, drill]
---

Layout has no automation call for ODB++, Gerber or NC drill. They run through Layout's Output menu commands and their dialogs (`ODB++ 设计输出`, `Gerber 输出`, `NC 钻孔生成`). pcb export drives them.

## The setup files

The settings are text files in the board's `Config` folder:

- ODB++: `Config/ODBSetup.ocf` (and `.eocf`)
- Gerber: `Config/PlotSetup.gpf` (and `GerberPlot.egpf`) and `Config/GerberMachineFile1.gmf`
- NC drill: the scheme `Sys: DrillEnglish.dff`

The dialogs hold their settings in memory while the board is open. They write them back to the files when OK is pressed. So a setup file edited while the board is open is overwritten. Edit setups only with the board closed, then open it again. A dialog opened earlier in the same Layout session even wrote its old settings over a patched file after the board had been closed and reopened.

## Stock defaults that matter

- The stock ODB++ setup leaves out the drill span (`d_1_4` with `INCLUDE NO`) and the board outline (`.BOARD_OUTLINE NO`). With both on, the ODB++ job has a drill layer and a profile.
- The stock Gerber silkscreen item writes a file with a header only. The cells' silkscreen outlines and reference designators must be named as cell items.
- The stock 4-layer setup writes the outer copper twice (`EtchLayer1Top` and `EtchLayerTop`) and negative plane files for the inner layers.

## How pcb export handles it

pcb export patches the setups that need it, each backed up once, with the board left open. It runs NC drill, ODB++ and Gerber through their dialogs, then reads the setups again. When a dialog has written its old settings back, it patches them and runs the three once more. `setups_rewritten` in its result names a setup that a dialog still overwrote. In one live run the ODB++ dialog wrote `d_1_4 INCLUDE NO` back, and the job came out without a drill layer; the second round fixed it.

## What to do

- Do not edit setup files while the board is open. Do not open the output dialogs by hand in the Layout session before pcb export.
- An output dialog left open, such as by a pcb export that was stopped part way, blocks the session. Ask the person to press its Cancel button: it closes without writing the setup files, and the session is ready again. Its OK button would run the output and write the setup back.
- After pcb export, check the package. The ODB++ job (under `Output/ODBpp/`) has a drill layer and a profile. The Gerber set (`Output/Gerber/*.gdo`) has a board outline and a top silkscreen with draws. NC drill wrote `Output/NCDrill/ThruHolePlated.ncd` and `ThruHoleNonPlated.ncd`.
- In one live run seven files stayed empty or duplicate: the bottom silkscreen, the bottom paste, the layer-3 plane negative and the generator's silkscreens were empty, and `EtchLayerTop` and `EtchLayerBottom` duplicated other files.
- After pcb export, Layout holds a file in the board folder: see `layout-drillprefs-held`.
