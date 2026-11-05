---
id: xpedition-ascii-paths-only
title: Keep projects, libraries and partition names ASCII
kind: rule
applies_to: [project create, project get, part create, schematic create]
tags: [paths, ascii, library, partition]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/E2E.md (Recorded run, a demo project end to end, 2026-09-12) and docs/COMPATIBILITY.md (Designer drawing facts verified on XPED2604; The library converters: HKP out and in) of npm @fateforge/xpedition-cli@1.0.3; this release's command registry (project create, project get)
updated_on: 2026-11-05
severity: error
check: run project get and read ascii_path; check each partition name in the parts file by eye
---

Keep the project folder and its central library on a path of ASCII characters only. Partition names use only ASCII letters, digits, spaces and `_ . -`.

Why: a project copied into a folder whose path held Chinese characters opened, read back and showed its sheets. But Designer could not place any symbol written after the copy. Every one failed with "Symbol <partition>:<name> not found, empty or a block", before and after a reopen. The same project on an ASCII path placed the symbol at once.

A partition names a parts database, a cell database and a `SymbolLibs` folder. Designer loaded no new symbol from a folder whose path had other characters.

How to keep the rule:

- Give project create an ASCII `--output` path, such as `D:/work/demo/Demo.prj`. It refuses other paths.
- Before part create or schematic create on an existing project, run project get. If `ascii_path` is false, stop and tell the person. Copying the project to an ASCII path is the fix.
- Keep partition names in a parts file within the characters above. Spaces are fine: the stock template partitions have them, such as `Crystals Resonators`.
