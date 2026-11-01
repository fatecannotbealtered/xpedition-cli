# xpedition-cli — 限定预览版

这是面向已合法安装 Siemens Xpedition 的 Windows CLI 和三个 Agent Skill。项目独立于
Siemens，不包含 Xpedition、商业 CAD SDK、许可证凭据、私人工程或私人运行证据。

CLI 显示版本为 `2.0.0-dev.1`，Python 分发版本规范化为 `2.0.0.dev1`。请从
[预发布页面](https://github.com/fatecannotbealtered/xpedition-cli/releases/tag/v2.0.0-dev.1)
下载 Windows x64 完整目录包并核对 SHA-256；保留整个 `app/` 目录。安装 Xpedition
及其许可证是独立前提。

本版仅为限定预览。运行时 `reference` 仍报告 `unpublishable`、功能合同覆盖缺失；
本次没有确认 PCB 保存重开、通用编辑、完整规则、DRC 或制造交付。丝印编辑仍禁用。
[限定实机 smoke](docs/E2E.md) 只覆盖自有启动、关闭诊断和退出，工程持久性仍为 unknown。

运行准确候选的 `--version`、`reference --index --compact` 和目标命令的 reference，
使用用户授权的 scope 与独立 `XPEDITION_CLI_CONFIG_DIR`。确认写入前检查实际预览、
目标和保护门禁；未知结果不得通过换 journal 自动重放。

三个匹配 Skill 可从同一 Release 或[固定标签源码](https://github.com/fatecannotbealtered/xpedition-cli/tree/v2.0.0-dev.1/skills)获取：

```powershell
npx skills add https://github.com/fatecannotbealtered/xpedition-cli/tree/v2.0.0-dev.1 -y -g
```

历史私有证据及其真实工程夹具不随快照分发；依赖这些夹具的 49 个历史测试函数
已替换为明确跳过的占位，原始断言与原生记录字节覆盖未在此复现。测试必须先隔离
配置、用户目录和输出，且不得连接已有 CAD 会话。
部分开发登记仍保留脱敏的历史证据元数据和哈希：其中示例路径不指向快照内的
真实证据，历史生成结果的 `errors=[]` 也不是本版检查通过。这些被排除的证据
无法仅凭本快照独立复验。
第三方许可见 `THIRD-PARTY-LICENSES` 和 `license-index-v4.json`。
