---
id: xpedition-gen-py-cache-broken
title: A broken win32com cache hides a running Designer
kind: troubleshooting
applies_to: [session list, session get, session open, doc open, doctor]
tags: [com, win32com, cache, startup]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/E2E.md (Recorded run: resume, read time and multi-land pads) of npm @fateforge/xpedition-cli@1.0.3, 2026-09-26; old implementation (commit 4a054a3), native_com_adapter.py (_heal_gen_py_cache) and CHANGELOG.md
updated_on: 2026-11-05
---

## Symptom

A Designer that is running is reported as not running, or connecting to it fails with:

```
has no attribute 'CLSIDToClassMap'
```

A second symptom came from the same cause in the same run. With the cache gone, Designer was bound late, and opening a design failed on `Documents.Open` with "parameter not optional".

## Cause

pywin32 (win32com) caches a generated wrapper for each type library in its `gen_py` folder, in the user's temp folder (`%TEMP%`). A temp cleaner deleted the `.py` files of the entries but left their `__pycache__` folders. Each entry then became an empty package, and win32com failed every attach to that type library. In the recorded run all four entries had lost their files.

## What to do

- The entries are only a cache. win32com builds an entry again when it is missing.
- xpedition-cli 1.0.3 removed the broken entries itself before it attached. A broken entry is a folder under `gen_py` that has a `__pycache__` folder but no `__init__.py`. An entry being generated has its `.py` files and no `__pycache__` yet, so it was left alone.
- If the error still appears, tell the person the cause. Removing the broken entry folders, by the rule above, fixes it. Ask before you delete files outside the project.
- Then run session list again.
