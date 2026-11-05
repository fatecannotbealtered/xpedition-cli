---
id: xpedition-external-edits-not-tracked
title: The tool does not see edits a person makes in Xpedition
kind: guide
applies_to: [doc save, doc get, doc list, doc close, session get, session close]
tags: [save, concurrency, sessions]
lang: en
based_on: capability survey docs/APPLICATION.md (Save and Undo; Instances and Concurrency; Risks); this release's adapter, xpedition_cli/adapter/sessions.py
updated_on: 2026-11-05
---

Xpedition does not tell automation that a document has changed. The survey found no change counter in either program. Whether either program exposes an "unsaved changes" flag through COM is not known, and the tool does not read one.

So xpedition-cli knows only its own changes:

- The `dirty` and `revision` values of a session document come from this tool's writes only.
- The doc save preview lists the changes this tool made since the last save. It lists nothing a person changed in Designer or Layout; its list of external changes is always empty.
- doc save saves the whole document, so a person's edits are saved along with the tool's changes, unseen.
- In file mode, a document's revision follows the file's time and size.

What to do:

- Before doc save, doc close or session close on a session a person also works in, ask the person whether they changed anything, and whether it may be saved or discarded.
- Do not present a save preview as the complete list of changes when a person may have worked in the session.
- `doc close --discard` and `session close --discard` throw away the person's edits too.
