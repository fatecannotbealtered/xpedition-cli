---
id: designer-project-prompts
title: The questions Designer asks when the project changes or a call times out
kind: troubleshooting
applies_to: [doc open, doc close, doc save, session open, session close, session get, session list, schematic create, schematic get, schematic check]
tags: [dialog, designer, timeout]
---

## Changing the project

Opening another project while sheets of the current one are open makes Designer ask:

"更改当前项目需要关闭所有打开的文档。是否希望关闭它们并继续打开选定的项目?" (changing the current project requires closing all open documents; close them and open the selected project?), Yes or No.

The open call waits until the question is answered. doc open does not answer it, because the open documents may hold changes nobody saved: it reports the question to the person as `E_HUMAN_REQUIRED`. schematic create answers Yes, because it saved the sheets it opened.

What to do:

- Before doc open of another project, run doc save and then doc close for the project Designer has open. Then the question does not come.
- If the question is already on screen, tell the person. Yes closes the open documents, so they should be saved first.

## "close all open documents?" after a timeout

A read that ran out of time (a one-second timeout in the live run) left "close all open documents?" on screen in Designer. While it was up, Quit returned without quitting.

session close answers no question while quitting, and watches whether the Designer process goes. When a dialog holds Designer, it reports `E_HUMAN_REQUIRED` with the dialog's title; when Designer stays without one, it reports `E_NATIVE_FAILURE`.

What to do:

- After `E_TIMEOUT` from a Designer command, run session get before anything else. Look for a blocker.
- If a dialog is up, tell the person its title. Send no more commands to that session until it is gone.
- After session close, run session list and check that the Designer session is gone. If it is still there, a dialog may hold it.
