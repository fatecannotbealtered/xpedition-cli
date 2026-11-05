---
id: layout-drillprefs-held
title: Layout holds DrillPrefs.txt after NC drill
kind: troubleshooting
applies_to: [pcb export, doc close, session close, session list, pcb create]
tags: [layout, files, drill, lock]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/COMPATIBILITY.md (Manufacturing outputs, ODB++, Gerber, NC drill) and docs/E2E.md (run of 2026-09-15) of npm @fateforge/xpedition-cli@1.0.3; old implementation (commit 4a054a3), CHANGELOG.md and native_com_adapter.py (_remove_board)
updated_on: 2026-11-05
---

## Symptom

After pcb export, the board folder (`PCB`) cannot be deleted or moved, because a file in it is in use.

## Cause

The NC drill output leaves `PCB/LogFiles/DrillPrefs.txt` open in the Layout process after the board closes. Quitting Layout through automation put it on its start page ("[首页]"), with the file still held.

## What to do

- Close the board with doc close, then close the session with session close.
- Run session list. If the Layout session is still there with no document, its process still holds the file.
- Then ask the person to end that Layout process. xpedition-cli 1.0.3 ended a Layout with no document by its process when the folder would not go. Do not end a Layout that has a document open.
- This matters when a person rebuilds the board: the old `PCB` folder must go first. See `forward-annotation-keeps-old-cells`.
