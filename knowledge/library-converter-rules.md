---
id: library-converter-rules
title: Rules of the central library converters
kind: guide
applies_to: [part create, part list, part get, padstack list, padstack create, packager run]
tags: [library, converter, hkp, cell]
---

part list, part get, part create, padstack list and padstack create read and write the central library through Xpedition's converter programs. `PartsDB2HKP`, `CellDB2HKP` and `PadstackDB2HKP` export a database as HKP text. `HKP2PadstackDB`, `HKP2CellDB` and `HKP2PartsDB` import it. Library Manager's own automation is read-only.

## How the converters behave

- They are GUI programs. A wrong argument or a licence problem shows a message box and returns no exit code.
- Their logs contain Chinese. Every log says "正在检查文件格式错误..." and "未找到文件格式错误。" (checking for file format errors… none found). That line is not an error. Real errors start with `错误` or `error`, or say `无法添加` (cannot add) or `遇到 N 个错误` (N errors met).
- A cell name has at most 64 characters. For a longer one, `HKP2CellDB` logs `无法添加单元 "…"。正在跳到下一个单元。` and then saves nothing of the whole file: `遇到 N 个错误。将不会保存单元数据库文件。`, exit code 1. Cell names with parentheses were refused too.
- `HKP2PartsDB` without `-r` keeps the part the database already holds under the same part number, and drops the new one without a word. With `-r` it replaces it. `HKP2CellDB` and `HKP2PadstackDB` with `-m` merge, and replace entries of the same name.
- A rounded-rectangle pad with radius 0 is refused. Such a pad must be written as a rectangle.
- A polyline written on one line of about 1500 characters crashed `HKP2CellDB` (0xC0000409). The points go one per line.
- Only one assembly outline is kept per cell. The placement outline must be one closed shape.
- The order matters: padstacks before the cells that use them, cells and symbols before the parts.
- Texts go to the converters in the system code page. A character that code page cannot hold cannot be stored.
- `LMCDB2HKP` hangs, so it is not used.
- The exports run while Designer has the project open.

## What this means for part create

part create skips a part identical to the library's. It refuses a part number the library holds with other content, instead of letting the converter drop the new part silently. To change a part, give it a new part number. Keep cell names short and free of parentheses. Check each new part with part get.
