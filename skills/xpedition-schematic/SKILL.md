---
name: xpedition-schematic
version: "2.0.0-dev.1"
description: "Guides authorized Xpedition Designer schematic and BOM inspection, planning, edits and export through the discovered CLI. Use for schematic tasks; use xpedition-pcb for board tasks."
license: MIT
user-invocable: true
metadata: {"requires":{"bins":["xpedition-cli"],"skills":["xpedition-cli"],"min_version":"2.0.0-dev.1"}}
---

# Xpedition schematic

**Limited prerelease.** Load `../xpedition-cli/SKILL.md` first. If the
entry Skill or matching CLI is unavailable, stop and obtain the matching family;
do not improvise its authorization or recovery procedure. The current candidate
reports `unpublishable`; earlier Designer results belong to their own builds.

## Inspect and plan

Identify the actual authorized Designer project, owned process and library
dependencies. Discover the relevant schematic, library, BOM and export leaves
with `reference`, and inspect their current scope, schemas and limitations.
Read actual parts, pins, nets, sheets and source definitions before selecting
edits; placeholders, previews and counts do not establish library or electrical
correctness. Use the task's real design size and keep unread fields unknown.

For a design task, use `project inspect --preflight` with actual task
requirements and review the blockers before planning work.

## Act and verify

STOP CHECKPOINT: before an authorized edit, materialization, packaging, check
or export, review the actual leaf's dry-run target, effects, dependencies,
outputs, protections and budget; confirm the same arguments once with its fresh
token when the live contract requires it. Do not redraw or replace libraries
merely because a preflight or readback failed.

Read back the changed and protected objects, identities and connectivity within
the actual operation's observation coverage. Distinguish native execution, Save,
closing/reopening, independent readback and rendered PDF or image inspection.
A page image or acknowledged Save is not full schematic, ERC, library or
manufacturing acceptance. If any write is partial or unknown, follow the entry
Skill's stop rule and preserve its original record without replay.

## Eval scenarios

- Inspect a user's schematic without changing the project.
- Preview one authorized edit and confirm only if its actual protection gates pass.
- Stop after an unknown native result without redrawing or reopening the project.
