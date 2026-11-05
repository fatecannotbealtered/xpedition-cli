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

{{fill: 已知的缺口和故障，每项写明受影响的工具版本和软件版本；没有时写「目前没有已知限制。」}}
