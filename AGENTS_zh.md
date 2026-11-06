# AGENTS.md

[English](AGENTS.md) | 中文

本文件写给在 xpedition-cli 项目上干活的编程 Agent。改动任何东西之前先读它。

## 必须遵守的底线

1. 禁止修改固定副本：`.agent/`、`contract/driver-contract.json`、`xpedition_cli/_driver/`、`licenses/`，以及 `scripts/` 里的 `spec.py`、`build.py`、`pack.py`、`platforms.py` 和 `release_gate.py`。它们原样来自 tool-driver-cli-spec 的某一个发布。要换到另一个发布，运行 `python scripts/spec.py sync <version>`，它会把这些副本一起替换。
2. 禁止手改生成的内容：`contract/commands.json`、README 和 Skill 里的命令地图、徽章行、信息卡、风险块、支持矩阵和证据索引。改它们的来源，再运行 `python scripts/spec.py generate`。
3. 每个领域命令都在 `xpedition_cli/commands/` 里登记，写明它的全部效果、模式、每个物理参数的单位量和目标类型。软件无法撤回的修改要标为 `irreversible`。禁止降低确认级别。
4. 每个领域命令都要在 `tests/matrix/` 里有行为矩阵。写命令的矩阵中，`normal` 行就是它的样例。
5. 每次提交前运行 `python -m ruff check .`、`python scripts/spec.py check`、`python -m unittest discover -s tests` 和 `python scripts/spec.py suites`，四项都必须通过。禁止跳过、削弱或修改一致性测试。
6. 真实软件测试只能用 `docs/E2E.md` 里「授权目标」一节列出的目标。只有人在自己发的消息里明确要你代写时，才添加目标或写真实软件测试的授权范围文件，而且要先把内容给人看、人同意后再写；否则只在对话里给出建议，由人自己写。每道评测题需要的授权范围文件由评测运行器来写。
7. 测试把 `XPEDITION_CLI_STATE_DIR` 设为新建的临时目录，从不碰真实的状态目录。
8. 禁止提交机密、许可证文件或密钥，以及无权分享的客户文件和项目文件，证据里也不行。
9. 脚本和测试在 Windows 上启动子进程时，传入 `CREATE_NO_WINDOW`。
10. 每次改动都在 `CHANGELOG.md` 的 `[Unreleased]` 下加一条记录。
11. 代码、代码注释、提交信息、命令输出和错误信息都用英文。

## 本项目

xpedition-cli 驱动 Xpedition，支持的版本是 2604，模式如下：

- `file`：直接读写 Xpedition 的文件。
- `session`：驱动正在运行的 Xpedition 实例。

代码的位置：

- 领域命令：`xpedition_cli/commands/`
- 适配层：`xpedition_cli/adapter/`
- 驱动运行时，固定副本：`xpedition_cli/_driver/`

适配层怎样连到 Xpedition：

- Designer：进程外 COM `Viewdraw.Application`（pywin32），用 `GetActiveObject` 连接。它的对象说不出自己的进程号，所以只能驱动一个 Designer。
- Layout：进程外 COM `MGCPCB.ExpeditionPCBApplication`；每打开一个文档都要换许可证 token（`Validate` → `MGCPCBAutomationLicensing.Application.GetToken` → `Validate`）。Batch DRC 和生产文件输出是菜单命令（`Gui.ProcessCommand`），会弹对话框。
- 中心库、打包、建板和导出 PDF 用 Xpedition 自带的命令行程序（`HKP2*DB`、`*DB2HKP`、`package.exe`、`JobWizard.exe`、`sch2pdf.exe`），一律经 `SDD_HOME/common/win64/bin` 下的启动器启动。
- 模态对话框会挡住所有 COM 调用。`adapter/platform/dialogs.py` 只回答规则表里已知的提示，从不按未知对话框的默认按钮，也从不碰许可证和登录窗口。
- 超过 60 秒的工作，例如打开工程、打包，登记为 `long`（标准命令列进 `long_commands`），由运行时在它自己的工作进程里运行（适配层这边的代码在 `adapter/jobs.py`）。
- 单元测试模拟 COM 和这些程序。测试里绝不启动 Xpedition 或运行它的程序；真实软件运行按 `docs/E2E.md` 做。

常用命令见 `CONTRIBUTING_zh.md` 的「常用命令」一节。真实软件测试只用 `docs/E2E.md` 里「Authorized Targets」一节的目标；本文件不列任何目标。
