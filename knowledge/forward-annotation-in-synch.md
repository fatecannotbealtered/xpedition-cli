---
id: forward-annotation-in-synch
title: Forward annotation when the board is already in sync
kind: troubleshooting
applies_to: [forward-annotation run, packager run, doc save]
tags: [forward-annotation, layout]
---

## Symptom

Forward annotation fails with "正向标注失败" (forward annotation failed), error 1001, and writes no log.

## Cause

The board already holds the packaged schematic. Layout keeps a forward-annotation status: 1 required, 2 in sync, 3 no CES data. Asking for forward annotation while the status is 2 raises error 1001.

xpedition-cli 1.0.3 read the status first. When it was 2, the tool ran nothing and reported the outcome `in_synch`. A second forward annotation right after a successful one gave that outcome.

## What to do

- An `in_synch` outcome, or error 1001, means there is nothing to bring in. It is not a fault of the board.
- If you expected changes, check that they were saved in Designer with doc save. Then close the project in Designer with doc close, run packager run, and run forward-annotation run again.
- A changed cell is not a change that forward annotation brings in: see `forward-annotation-keeps-old-cells`.

## How a successful run looks

It took 30 to 70 seconds and showed progress windows (Packager, Database Load) that need no answer. It wrote `PCB/LogFiles/ForwardAnnotation.txt` with lines such as "N nets were found containing M pins", "N components were found" and "Forward-Annotation on the Layout Design has been successfully completed".
