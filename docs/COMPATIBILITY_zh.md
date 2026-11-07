# 兼容性

[English](COMPATIBILITY.md) | 中文

xpedition-cli 在哪里能用，能配合哪些版本的 Xpedition。

## 支持矩阵

Xpedition 版本、操作系统和模式的每种组合占一行，由 `python scripts/spec.py generate` 根据 `docs/evidence/` 里的真实软件记录和 `package.json` 里的平台包生成，从不手改：✅ verified 表示已在真实软件上验证，⏳ untested 表示还没测试，⛔ unsupported 表示不支持。

<!-- driver:support-matrix:start -->
| 软件版本 | 操作系统 | 模式 | 状态 |
|---|---|---|---|
| 2604 | Windows | file | ⏳ untested |
| 2604 | Windows | session | ⏳ untested |
| 2604 | macOS | file | ⛔ unsupported |
| 2604 | macOS | session | ⛔ unsupported |
| 2604 | Linux | file | ⛔ unsupported |
| 2604 | Linux | session | ⛔ unsupported |
<!-- driver:support-matrix:end -->

## 已知限制

- 目前这个版本还没有真实软件运行记录。行为矩阵在真实软件上通过之前，每条命令都是 `draft`。
- 只支持 Windows x64 上的 Xpedition 2604；COM 只在 Windows 上有。
- 同时只驱动一个 Designer 和一个 Layout：COM 每种程序只连得上一个实例。
- Xpedition 不经 COM 报告未保存的修改或修改计数，所以 `dirty` 和 `revision` 来自工具自己的写操作，工具没有打开或保存过的文档，`dirty` 为 `null`；人在软件里的修改看不到（知识条目 `xpedition-external-edits-not-tracked`）。
- 增删页以后，Designer 要存盘、关闭再打开工程才能读对设计（知识条目 `designer-reopen-after-sheet-changes`）；`schematic create` 不重开就读回，这在 Xpedition Enterprise 2604 的真机运行中可行。
- 不提供底面放置、窗口截图和刷新板上的封装。
- `doc save` 要一到两分钟：存盘后它会关闭再打开文档，因为 Designer 的数据库服务只在工程关闭时才把工程写进文件；存盘的修改从重新打开的文档读回。只有看到文档所在文件夹里的文件变了，才报告 `saved`。
