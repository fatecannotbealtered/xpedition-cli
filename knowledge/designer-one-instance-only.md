---
id: designer-one-instance-only
title: Run one Designer and one Layout at a time
kind: guide
applies_to: [session list, session open, session get, session close, doc open, schematic create, schematic get, forward-annotation run]
tags: [sessions, instances, designer, layout]
lang: en
based_on: capability survey docs/APPLICATION.md (Automation Interfaces; Instances and Concurrency), facts marked from the old implementation (commit 4a054a3); this release's standard commands, xpedition_cli/_driver/standard.py (session close tiers)
updated_on: 2026-11-05
---

Designer's automation object has no process ID and no window handle. The tool reaches a running Designer through COM, but it cannot choose which one: the running object table gives no way to pick an instance. The process ID can be read from a view only while a sheet window is open. Otherwise the tool can only assume that the Designer it reached is the Designer process on the machine, and that holds only when exactly one runs. With two Designers open, the intended one cannot be driven reliably.

Layout is similar. Its process ID comes from its main window. When several Layouts run, COM returns only one of them, and the tool cannot choose which.

What to do:

- Run session list before session open. If a Designer already runs, use it: open the project in it with doc open instead of starting a second Designer. Do the same for Layout. (Switching Designer to another project asks a question: see `designer-project-prompts`.)
- If session list shows two Designer sessions or two Layout sessions, stop. Ask the person to close the one they do not need.
- Closing a session this tool did not start is a dangerous operation (`close_unowned_session`). Do not do it unasked.
- Name the session with `--session` in every command.
- A person working in the same Designer at the same time is a separate problem: see `xpedition-external-edits-not-tracked`.
