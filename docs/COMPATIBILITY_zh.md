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

## 客户端与宿主

xpedition-cli 没有宿主组件。它只从 CLI 进程里连到 Xpedition，xpedition-cli 和 Xpedition 始终在同一台电脑上运行。

## 宿主组件版本

xpedition-cli 没有宿主组件，所以没有宿主组件版本。

## 已知限制

- 目前这个版本还没有真实软件运行记录。行为矩阵在真实软件上通过之前，每条命令都是 `draft`。
- 只支持 Windows x64 上的 Xpedition 2604；COM 只在 Windows 上有。
- 同时只驱动一个 Designer 和一个 Layout：COM 每种程序只连得上一个实例。
- Xpedition 不经 COM 报告未保存的修改或修改计数，所以 `dirty` 和 `revision` 来自工具自己的写操作；人在软件里的修改看不到（知识条目 `xpedition-external-edits-not-tracked`）。
- 增删页以后，Designer 要存盘、关闭再打开工程才能读对设计（知识条目 `designer-reopen-after-sheet-changes`）；`schematic create` 不重开就读回，这一点还没有证实。
- 不提供底面放置、窗口截图、Designer 的 NewProject 和刷新板上的封装。
