<h1 align="center">xpedition-cli</h1>

<p align="center"><strong>面向 AI Agent 的 Xpedition 设计控制层：JSON 优先，ChangeSet 写入受 dry-run 保护</strong></p>

<p align="center"><a href="README.md">English</a> · <a href="README_zh.md">中文</a></p>

<p align="center">
  <a href="https://github.com/fatecannotbealtered/xpedition-cli/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/fatecannotbealtered/xpedition-cli/ci.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=CI"></a>
  <a href="https://www.npmjs.com/package/@fateforge/xpedition-cli"><img alt="npm" src="https://img.shields.io/npm/v/@fateforge/xpedition-cli?style=for-the-badge&logo=npm&logoColor=white&label=npm&color=CB3837"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-7C3AED?style=for-the-badge"></a>
</p>

`xpedition-cli` 是 Siemens Xpedition 数据的命令层。MockBackend 可离线使用；
NativeBackend 通过可选的 Windows COM 适配器驱动正版 Xpedition，已经把一个工程从空白原理图
一路做到布线完成的板子和整套打板资料（见 `docs/E2E.md`）。这些证据来自同一台 Windows 机器，
封装是转换来的开源库，料号是占位值。CLI 不直接修改 Xpedition 私有数据库，
所有写操作都要先 `--dry-run` 再 `--confirm <token>`。

## Agent 安装

```bash
npm install -g @fateforge/xpedition-cli
npx skills add fatecannotbealtered/xpedition-cli -y -g

xpedition-cli context --compact
xpedition-cli doctor --compact
xpedition-cli reference --compact
```

在代码检出目录中可使用 `python -m xpedition_cli.main ...`。MockBackend 不需要
CLI 登录；Native Xpedition 的凭据和许可证留在经过验证的 Xpedition 环境内。Windows
Native 适配器可通过 `python -m pip install -e ".[native]"` 安装，详见
[NativeBackend 适配器协议](docs/NATIVE_ADAPTER.md)。

## 它做什么

CLI 负责标准化工程快照、BOM、连通性和确定性的审查结果。ChangeSet 描述放置器件、
创建网络、连接引脚、移动或删除器件、设置属性等受控操作。应用 ChangeSet 必须先拿到
预览 token，替换已有文件时自动生成备份，随后原子保存并回读验证。

风险等级：**T1**。对 MockBackend，爆炸半径是明确指定的那个本地 JSON 文件。对 NativeBackend，
爆炸半径是指定的那个 Xpedition 工程：一次确认过的写入可以画原理图、摆器件、增删布线；
`pcb create --replace` 会先把已有布局目录打包成工程旁边的 zip，然后删除它。
参见 [SECURITY.md](SECURITY.md)。

## 能力

| 领域 | 命令 | 后端 |
|---|---|---|
| 工程数据 | `project init`、`project info`、`project tree`、`project snapshot`、`project diff`、`design snapshot` | MockBackend |
| 原理图 | 上述读取命令，以及用于 ChangeSet 的 `schematic apply` | MockBackend |
| PCB 读取 | `pcb info`、`components`、`footprints`、`nets`、`layers`、`stackup`、`tracks`、`vias`、`zones`、`keepouts`、`query` | MockBackend |
| PCB 设计 | `pcb create`、`annotate`、`outline`、`holes`、`arrange`、`move`、`rules`、`pour`、`route`、`trace`、`via`、`unroute`、`stitch`、`labels`、`geometry`、`render`、`show`、`drc`、`export` | NativeBackend（见下节） |
| 原理图绘制 | `schematic draw`、`schematic show`、`library build` | NativeBackend（见下节） |
| 约束与分析 | `constraints ...`、`analysis run|results|erc|drc|dfm` | MockBackend |
| 制造与库 | `manufacturing ...`、`library search|...|validate` | MockBackend |
| 变更控制 | `change validate`、`change preview`、`change apply`、`change history`、`change rollback` | MockBackend |
| 审查与 BOM | `review run`、`bom export|normalize|group|variants|missing|duplicates|validate|compare` | MockBackend |
| 环境 | `context`、`doctor`、`system capabilities`、`system license` | 本地探针 |
| 会话 | `session status`、`session logs` | MockBackend；原生 start/attach/stop 需要适配器 |
| Exchange 文件 | `exchange inspect`、`exchange import` | JSON/CSV/BOM/IPC-2581；PDF/EDN/ODB++ 仍不可用 |
| Agent 桥接 | `agent snapshot`、`agent query`、`agent review`、`agent capabilities`、`agent serve` | MockBackend；`serve` 支持自定义 NDJSON 和 MCP transport |
| 原生 Xpedition | `session ...`、工程/PCB 读取、受控器件移动与放置 | NativeBackend；需 COM 注册和授权 |
| 规划中 | 未支持的 Exchange 格式、完整原生 ChangeSet、R-C 闭环证据 | 在 `capabilities` 中显式标记 |

实时命令和 schema 以 `xpedition-cli reference --compact` 为准。

## Agent 工作流

1. 运行 `context`、`doctor`、`reference`，确认后端和发布就绪等级。
2. 离线工作时显式使用 `--backend mock` 和 `--project PATH`。
3. 在 Agent 步骤之间传递 JSON 时使用 `--compact` 和 `--fields`。
4. 创建新的 MockBackend 工程时先运行 `project init --dry-run`，检查预览后使用相同参数确认。
5. 应用 ChangeSet 前先校验和预览：

   ```bash
   xpedition-cli change validate --changeset ./changeset.json --compact
   xpedition-cli change apply --backend mock --project ./demo-project.json --changeset ./changeset.json --dry-run --compact
   xpedition-cli change apply --backend mock --project ./demo-project.json --changeset ./changeset.json --confirm <confirm_token> --backup --compact
   ```

6. 应用后检查 `verification`，并在下一次写入前重新读取 `project snapshot`。
7. 使用 `change history` 查看本地操作记录。回滚同样必须先预览，再使用一次性确认 token。

Agent 集成可使用 `xpedition-cli agent serve --transport stdio`，通过 NDJSON
请求/响应流访问 MockBackend 的 snapshot、query、review 和 capability 方法。

## 原生 Xpedition：从原理图到打板资料

下面每一步都在有许可的 Xpedition（XPED2604）上对示例工程跑通过；过程记录在
[docs/E2E.md](docs/E2E.md)，自动化接口的每条事实在 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)。
写命令都有门禁：`--dry-run` 返回 `confirm_token`，`--confirm <token>` 才执行。

| 阶段 | 命令 |
|---|---|
| 工程与原理图 | `project init`、`schematic plan`、`schematic draw`、`schematic show`、`schematic export`、`review run` |
| 库 | `library build`（零件来自设计文件；封装由 KiCad 库转换，`python -m xpedition_cli.kicad_import`） |
| 板子 | `pcb create`、`pcb annotate`、`pcb outline`、`pcb holes`、`pcb arrange`、`pcb pour`、`pcb rules`、`pcb route`、`pcb drc` |
| 手工布线 | `pcb geometry`、`pcb trace`、`pcb via`、`pcb unroute`、`pcb move`、`pcb labels`、`pcb stitch`（离线检查在 `xpedition_cli.routing_plan`） |
| 出图与出资料 | `pcb render`、`pcb show [--top-view]`、`pcb export`（ODB++、Gerber、钻孔、坐标、BOM、清单） |

## 机器契约

- 默认输出 JSON，stdout 只包含一个 envelope。
- 成功和失败都包含 `ok`、`schema_version` 和 `meta.duration_ms`。
- 错误使用 [`contract/contract.json`](contract/contract.json) 中统一的 `E_*`、退出码和 `retryable` 映射。
- 日志和诊断走 stderr；`--json` 是 `--format json` 的兼容别名，`text` 面向人，`raw` 返回 payload。
- 来自工程文件的项目和审查字段通过 `_untrusted` 标记。
- ID 使用字符串，时间使用 ISO 8601 UTC。

## 配置

本阶段没有登录流程。CLI 只在 `~/.xpedition-cli/` 下保存本地确认 secret、已消费 token
记录和审计 JSONL。测试或 CI 可设置 `XPEDITION_CLI_CONFIG_DIR` 隔离这些文件。Native
适配器可通过 `XPEDITION_NATIVE_COMMAND` 指定；设置该变量不会绕过 COM 注册或许可证检查。

## 项目结构

```text
xpedition-cli/
├── xpedition_cli/       # CLI 边界、模型、ChangeSet、后端、契约
├── tests/               # 命令级契约和 FCC 测试
├── skills/xpedition-cli/  # 入口 Skill：安装、会话、原理图、库
├── skills/xpedition-pcb/  # 板级 Skill：Layout、布线、DRC、制造输出
├── contract/            # vendored 机器契约真源
├── scripts/             # 规范、版本和 npm 壳工具
├── docs/                # 兼容性、E2E 和开源清单
└── .agent/              # 固定版本的 AI 原生 CLI 规范
```

## 开发

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check xpedition_cli tests
ruff format --check xpedition_cli tests
node scripts/check-version.js
node scripts/check-spec.js --local-only
```

当前 `reference.release_readiness.level` 为 `stable`：每条公开命令都有命令级测试，
契约测试覆盖失败路径和边界行为而不只是正常路径，正版 Xpedition 上的真实运行记录在
[`docs/E2E.md`](docs/E2E.md)。`stable` 说的是这些证据，不等于承诺换一台机器上的
Xpedition 自动化行为完全一致。

## 链接

- [Agent 入口](AGENTS.md)
- [Skill](skills/xpedition-cli/SKILL.md) 及板级的 [xpedition-pcb](skills/xpedition-pcb/SKILL.md)
- [CLI 契约](.agent/CLI-SPEC.md)
- [安全策略](SECURITY.md)
- [兼容性矩阵](docs/COMPATIBILITY.md)
- [NativeBackend 适配器协议](docs/NATIVE_ADAPTER.md)
- [MCP transport](docs/MCP.md)
- [E2E 说明](docs/E2E.md)
- [变更记录](CHANGELOG.md)
- [贡献说明](CONTRIBUTING.md)
- [第三方声明](NOTICE.md)
- [MIT 许可证](LICENSE)
