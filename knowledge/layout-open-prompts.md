---
id: layout-open-prompts
title: The questions Layout asks when it opens a board
kind: troubleshooting
applies_to: [session open, session get, doc open, doc close, doc save, forward-annotation run, pcb export, session close]
tags: [dialog, layout, open]
---

When Layout opens a board, it can ask up to three questions. The open call does not return until each one is answered, so the command hangs or the session shows as blocked. These are Qt windows, not standard Windows dialogs. Their texts below come from a Chinese installation.

| Question | When it comes | What xpedition-cli answers |
|---|---|---|
| "应用程序已尝试确定设计状态超过 15 秒了…" (determining the design status took more than 15 seconds), Open or Cancel | A killed Layout session left a stale lock | Open |
| "数据库恢复" (database recovery): load the autosave or the database the user saved, OK (确定) or Cancel | After a killed session | The database the user saved, then OK |
| "新更改已经可以进行正向标注。是否要立即进行正向标注?" (new changes are ready for forward annotation; annotate now?), Yes or No | The schematic is newer than the board | No (否), so that forward annotation stays an explicit command; during forward-annotation run, Yes |

Two more dialogs hold Layout in the same way:

- Save All. Opening another board while the current board has unsaved changes shows a Save All dialog.
- A question that the design and the local library have changed, with four buttons: save all, save library only, don't save, cancel. It came when the board was opened again while its outline was in a bad state. Pressing Cancel (取消(C)) recovered.

What to do:

- If session get shows one of these as the blocker, tell the person which question it is and what xpedition-cli answers to it. Let the person answer it. Only the person knows whether the autosave or the saved database should be loaded.
- Answer "annotate now?" with No, then run forward-annotation run.
- Before doc open of another board, run doc save or doc close for the board that is open.
- Ending a Layout process brings the first two questions at the next open. End one only when nothing else works.
