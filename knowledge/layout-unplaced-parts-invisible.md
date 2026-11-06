---
id: layout-unplaced-parts-invisible
title: After forward annotation the parts are unplaced and not drawn
kind: guide
applies_to: [forward-annotation run, placement run, router run, pcb get]
tags: [layout, placement, display]
---

Forward annotation brings the parts in unplaced: not placed, at position 0, 0. Layout does not draw an unplaced part at all. So the board looks empty after forward annotation, although Layout's component navigator counts the parts. Tell the person this if they look at the board before placement run.

Unplaced parts are missing from several reads:

- Once a part has been unplaced, a net's pin list shows only the pins of placed parts. A plan made then saw 16 of 22 ground pins.
- The pin count of the document read 0 even with 95 pins on the board.
- A route pass on a board whose parts are unplaced reports every net routed, with no opens.

So run placement run before router run. Do not trust a "complete" routing result on a board with unplaced parts.

Placed parts can still be invisible. The stock templates open under the display scheme `Loc: Assembly Bottom`, which shows nothing of a top-side part. The schemes `Loc: Placement` and `Loc: All On` show the parts. `Loc: All On` draws plane copper as outlines only. Layout keeps the scheme picked last and opens the board with it next time.
