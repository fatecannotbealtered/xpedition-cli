---
id: xpedition-start-through-launcher
title: Start Designer and Layout through the launcher
kind: troubleshooting
applies_to: [session open, session list, session get, doctor, packager run]
tags: [startup, com, launcher]
---

## Symptom

Starting Xpedition through COM fails with `CO_E_SERVER_EXEC_FAILURE` (0x80080005). xpedition-cli 1.0.3 reported this as `E_BACKEND_UNAVAILABLE`, with a hint to use the launcher.

Running a program binary directly from its own folder under `SDD_HOME`, such as `wg/win64/bin`, failed too: with `CO_E_SERVER_EXEC_FAILURE`, with `STATUS_DLL_NOT_FOUND`, or, for `package.exe`, with the message "no Qt platform plugin could be initialized".

## Cause

The COM registration of each Xpedition program points at the real binary under `SDD_HOME/<product>/win64/bin`. That binary needs the release environment that the small launcher in `SDD_HOME/common/win64/bin` sets up. COM activation skips the launcher. This holds for Designer, Layout and Library Manager alike.

An instance that COM activation did start opened the board in a hidden window and never registered for automation. Nothing could attach to it.

## What to do

- Start the programs with session open. It starts them through the launcher (`viewdraw.exe` and `ExpeditionPCB.exe` in `SDD_HOME/common/win64/bin`).
- Do not start Xpedition programs any other way for the tool.
- Wait until session get shows the session ready. A Layout started through the launcher became reachable after about 50 seconds to a minute.
- The launcher's process ID is not the ID of the program's process. Use the session IDs that session list reports.
- If doctor reports that the Xpedition COM classes are not registered, the product's own post-install registration has not run (`registrator.exe` in `SDD_HOME/common/win64/_bin`). Ask the person or an administrator to run it. Do not register classes yourself.
