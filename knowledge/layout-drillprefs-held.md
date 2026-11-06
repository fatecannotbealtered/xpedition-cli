---
id: layout-drillprefs-held
title: Layout holds DrillPrefs.txt after NC drill
kind: troubleshooting
applies_to: [pcb export, doc close, session close, session list, pcb create]
tags: [layout, files, drill, lock]
---

## Symptom

After pcb export, the board folder (`PCB`) cannot be deleted or moved, because a file in it is in use.

## Cause

The NC drill output leaves `PCB/LogFiles/DrillPrefs.txt` open in the Layout process after the board closes. Quitting Layout through automation can leave it on its start page ("[Home Page]" or "[首页]") with the file still held; it then no longer answers automation, and session list reports it `starting`.

## What to do

- Close the session with session close. It closes the board without saving, quits Layout, and ends a Layout that stays on its start page: by then it has no document open.
- If session list still shows a Layout session in state `starting` with no document long after, its process still holds the file. Ask the person to end that Layout process. Do not end a Layout that has a document open.
- This matters when a person rebuilds the board: the old `PCB` folder must go first. See `forward-annotation-keeps-old-cells`.
