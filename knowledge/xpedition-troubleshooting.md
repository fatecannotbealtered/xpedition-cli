---
id: xpedition-troubleshooting
title: Common Xpedition dialogs and errors
kind: troubleshooting
applies_to: [doctor, session list, session get, session open, session close, doc open, doc close, part create, packager run, pcb create, schematic create, schematic check, schematic export, forward-annotation run, placement run, router run, pcb check, pcb export]
tags: [dialog, error, index]
lang: en
based_on: capability survey docs/APPLICATION.md (Threading and Blocking, Licensing); xpedition-cli 1.0.3 live records docs/E2E.md, docs/COMPATIBILITY.md and docs/NATIVE_ADAPTER.md of npm @fateforge/xpedition-cli@1.0.3, 2026-09-12 to 2026-09-30; old implementation (commit 4a054a3), native_com_adapter.py and win_dialogs.py
updated_on: 2026-11-05
---

The measured installation had a Chinese user interface, so most texts below are Chinese. Each row names the entry with the details. Read it with `knowledge get --id <id>`.

## General rules

- A COM call into Designer or Layout does not return while a modal dialog is open. A command that hangs, or a session that session get shows as blocked, usually waits on a dialog. Its title is in `blocker`.
- Do not answer a dialog you do not know. Tell the person its title and let them answer it.
- Licence and sign-in windows are for a person only. Never read or answer them.

## Start-up and connection

| What you see | Entry |
|---|---|
| The Siemens licence sign-in window when Designer starts | `xpedition-license-sign-in` |
| `E_AUTH`; product code 10279, scode 0x8004022D; "自动化代码不包含身份验证所需的许可调用。" | `xpedition-license-sign-in` |
| `CO_E_SERVER_EXEC_FAILURE` (0x80080005); "no Qt platform plugin could be initialized" | `xpedition-start-through-launcher` |
| A running Designer reported absent; "has no attribute 'CLSIDToClassMap'" | `xpedition-gen-py-cache-broken` |
| Two Designer or two Layout sessions in session list | `designer-one-instance-only` |

## Dialogs while documents open and close

| What you see | Entry |
|---|---|
| "应用程序已尝试确定设计状态超过 15 秒了…", "数据库恢复" or "新更改已经可以进行正向标注…" when Layout opens a board | `layout-open-prompts` |
| A Save All dialog, or a four-button question about saving the design and the local library, in Layout | `layout-open-prompts` |
| "更改当前项目需要关闭所有打开的文档…" in Designer | `designer-project-prompts` |
| "close all open documents?" left in Designer after a timeout; Designer does not quit | `designer-project-prompts` |

## Schematic and library

| What you see | Entry |
|---|---|
| "Symbol <partition>:<name> not found, empty or a block" (6055) | `xpedition-template-library-is-empty`, `xpedition-ascii-paths-only`, `designer-symbol-cache` |
| A type mismatch, or wrong counts, when reading a schematic just drawn | `designer-reopen-after-sheet-changes` |
| A power or ground symbol that names no net | `designer-power-symbols` |
| A pin or ground symbol left unconnected on a wire corner | `designer-pin-on-wire-corner` |
| `drc-BI-POWER` or `drc-BI-GROUND` from schematic check | `designer-pin-types-and-erc` |
| Garbled Chinese text in the PDF of schematic export | `designer-chinese-text-in-pdf` |
| A part's Value read back as 0 | `library-part-value-is-numeric` |
| "未找到文件格式错误。" in a converter log; a cell file not saved; a part not replaced | `library-converter-rules` |

## Board

| What you see | Entry |
|---|---|
| "No cell library search paths found from <project>.prj" | `prj-cell-library-list` |
| "Cell <name> has 4 unique Alphanumeric Pin Numbers while Part Number <part> has 3" | `cell-pin-count-must-match` |
| "正向标注失败" (forward annotation failed, 1001) | `forward-annotation-in-synch` |
| A changed cell does not reach the board | `forward-annotation-keeps-old-cells` |
| The board looks empty after forward annotation | `layout-unplaced-parts-invisible` |
| "DRC 违规" (10203) during placement run | `layout-placement-drc` |
| Nets left open around a fine-pitch part, with no reason given | `layout-fine-pitch-clearance` |
| Trace widths that cannot be changed; `TraceWidths` hazards | `layout-trace-widths-through-cm` |
| `PartialNets` and `Dangling` hazards on a plane net | `layout-plane-draft-state`, `layout-pour-after-routing` |
| A via refused near a pad; `Hangers` at a test point | `layout-via-near-pad` |
| An ODB++ job without a drill layer or board outline | `layout-output-setups` |
| The board folder cannot be deleted after pcb export | `layout-drillprefs-held` |
| A person's edits missing from the doc save preview | `xpedition-external-edits-not-tracked` |

## Errors without their own entry

- "未能从通用数据库中获取设计信息" (failed to get design information from the common database) from JobWizard during pcb create. JobWizard takes the first entry of `LIST Designs` in the `.prj`. That entry must be the board design (`ConfigType "PCB"`), not the schematic block. Read the designs with project get.
- "iCDB error getting UID manager" in Layout. Designer closed the project while Layout was opening the board, and Layout's database session broke. The records do not say how to recover. Closing the board and opening it again with doc open is the first thing to try.
- Product code 64185, "Xpedition supports only scripts without GUI". Designer refused an operation it thought already done, most often opening the project it already has open. It came after a timed-out call had left the tool's view of Designer stale. Run session get and doc list to see what Designer really has open.
- `E_TIMEOUT`. The program may still be busy, or the call may have left a dialog open. Run session get before the next command.
