<h1 align="center">xpedition-cli</h1>

<p align="center"><strong>面向 Agent 的 Siemens Xpedition 控制工具：从设计描述到布好线的板子和制造文件，每次写入先预览、后核验</strong></p>

<p align="center"><a href="README.md">English</a> · <a href="README_zh.md">中文</a></p>

<p align="center">
  <a href="https://github.com/fatecannotbealtered/xpedition-cli/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/fatecannotbealtered/xpedition-cli/ci.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=CI"></a>
  <a href="https://www.npmjs.com/package/@fateforge/xpedition-cli"><img alt="npm" src="https://img.shields.io/npm/v/@fateforge/xpedition-cli?style=for-the-badge&logo=npm&logoColor=white&label=npm&color=CB3837"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-7C3AED?style=for-the-badge"></a>
</p>

`xpedition-cli` 在 Windows 上通过自动化接口驱动已授权的 Xpedition：原理图在
Designer 里，板子在 Layout 里。Agent 写好原理图的描述，把缺的器件加进工程自己的
库，画图、打包、建板、布局、布线、检查、导出，一共 54 条命令，每条都只回一个 JSON
信封。整条链路在一台装有 XPED2604 的 Windows 上跑通，记录在
[docs/E2E.md](docs/E2E.md)。CLI 从不直接改 Xpedition 的私有数据库，所有写命令都要
先 `--dry-run` 再 `--confirm <token>`。

## Agent 安装

```bash
python -m pip install "xpedition-cli[native] @ git+https://github.com/fatecannotbealtered/xpedition-cli"
npx skills add fatecannotbealtered/xpedition-cli -y -g

xpedition-cli context --compact
xpedition-cli doctor --compact
xpedition-cli reference --compact
```

第一行从本仓库默认分支安装 CLI 和它的 Windows 适配器（`[native]`）；要固定版本就在
末尾加发布 tag，例如 `...xpedition-cli@v1.0.2`。在源码目录里
`python -m pip install -e ".[native]"` 效果相同。每次发布还会在 npm 上发布独立二进制
`@fateforge/xpedition-cli`；它不带 Windows 适配器，只能跑不需要 Xpedition 的命令
（`reference` 里 `needs: none` 的：预览、规划、按文件计算指标、工程备份）。CLI 没有
登录流程，Xpedition 自己的许可留在用户的安装里（见[原生适配器协议](docs/NATIVE_ADAPTER.md)）。

## 能做什么

十二步，每步几条命令（`reference` 里的 `workflow` 列着）：

| 步骤 | 命令 |
|---|---|
| 检查机器，启动 Designer 或 Layout | `doctor`、`session start` |
| 从模板建工程 | `project create` |
| 把设计要用的器件放进库 | `library list`、`library add`、`library render`、`library import` |
| 先预览设计，再画原理图 | `schematic render`、`schematic draw` |
| 生成占位器件并打包 | `library build`、`library check` |
| 检查原理图和 BOM | `schematic check`、`bom check`、`schematic export` |
| 备份、建板、把原理图同步进板子 | `project backup`、`pcb create`、`pcb annotate` |
| 板框、安装孔、网络类 | `pcb outline`、`pcb holes`、`pcb rules` |
| 布局和丝印位号 | `pcb arrange`、`pcb move`、`pcb labels` |
| 布线和铺铜 | `pcb route`、`pcb trace`、`pcb via`、`pcb stitch`、`pcb pour` |
| 检查、度量、看图 | `pcb check`、`pcb geometry`、`pcb metrics`、`pcb render` |
| 制造文件 | `pcb export`、`bom export` |

另外还有：`schematic edit` 在已画好的图上定点修改（放置、移动、删除元件，改属性，
连接或断开引脚，改网络名）；读取类命令（`schematic components|nets|sheets`、
`pcb info`、`library show`）；把窗口带到前台（`schematic show`、`pcb show`）；
`project restore`；会话（`session status|stop`）；知识库（`kb list|add|remove`）；
自描述（`context`、`doctor`、`reference`、`changelog`、`version`）。

**设计库。** 器件有两条路进入工程的中心库。`library import` 从已有的 Xpedition 库
导入：别的工程的库，或公司库的一份拷贝（`--from LIB.lmc`），器件连同它用到的符号、
封装、焊盘栈、焊盘和孔一起进来，各自保留源库里的分区；源库只读不写，导入前可以先用
`library list|show|check --library LIB.lmc` 看看里面有什么。`library add` 按器件文件
自己创建：每个器件一个符号（带命名、带类型引脚的方框，或内置种类）、一个封装和引脚
对应。封装可以按 IPC-7351B 从数据手册尺寸生成（chip、molded、鸥翼、J 形引脚、带散热
焊盘的 QFN/DFN、通孔），可以逐个给焊盘（多个焊盘可以共用一个引脚号；支持槽孔、
非金属化孔和定位孔），也可以用库里已有的封装。两种方式的预览都会说清楚每一项是新增、
保留（内容相同就不动）还是会覆盖；写入后读回库再逐个检查。设计里直接写器件编号就能用：
`"symbols": {"LDO": {"part": "TPS7A2033PDBVR"}}`。

每次写入都会读回结果：画图对照计划核对网表，编辑逐条核验，`library add` 和
`library import` 逐个检查器件，`pcb` 写命令报告它读回的状态。风险等级 **T2**。会毁掉没有归档的成果的写入，
除了 token 还要加 `--dangerous`：`schematic draw`、`pcb unroute`、
`pcb create --replace`（它会先把版图目录压缩到工程旁边）、在已布线的板上
`pcb arrange`、`pcb route --unroute`、`pcb annotate --unroute`、会覆盖库里内容的
`library import` 和 `library add`，以及 `project restore`。做这些之前
可以先用 `project backup` 把整个工程打包。见 [SECURITY.md](SECURITY.md)。

实时的命令和 schema 以 `xpedition-cli reference --compact` 为准。

## 机器契约

- 默认输出 JSON，stdout 里只有一个信封。
- 成功和失败都带 `ok`、`schema_version` 和 `meta.duration_ms`。
- 错误码 `E_*`、退出码和 `retryable` 的对应关系见
  [`contract/contract.json`](contract/contract.json)。
- 日志和诊断走 stderr。`--json` 是 `--format json` 的兼容别名；`--format text` 给人看，
  `raw` 只返回数据本身。
- 来自设计、库或文件的值都列在 `_untrusted` 里。
- ID 是字符串，时间是 ISO 8601 UTC。
- 未知选项、缺少必填项、格式不对的值，在执行任何动作之前就会被拒绝。

## 配置

CLI 的本地状态放在 `~/.xpedition-cli/`：确认密钥、已用 token 账本和它的锁、审计
JSONL、知识库链接、会话记录（`session.json`）和库导出缓存。设
`XPEDITION_CLI_CONFIG_DIR` 可以把这些文件隔离开。设 `XPEDITION_NATIVE_COMMAND` 可以
指定适配器，但它绕不过 COM 注册和许可；找不到安装位置时设 `XPEDITION_SDD_HOME`。

公司自己的规则（布局规则、画图规范、评审清单）留在公司的知识库里。
`kb add --name 名字 --url 链接 --about 说明`（写命令：先预览再确认）记录哪篇文档适用，
`context` 把它们列给 agent，agent 用自己的工具去读。CLI 从不抓取文档内容。

## 目录结构

```text
xpedition-cli/
├── xpedition_cli/               # CLI 边界、命令注册表、规划器、设计库、适配器
│   └── cli/                     # 每个命令域一个模块；registry.py 列出全部命令
├── tests/                       # 命令级契约测试和 FCC 测试（伪造的适配器）
├── skills/xpedition-cli/        # 入口 Skill：安装、会话、工程、设计库、安全
├── skills/xpedition-schematic/  # 原理图 Skill：画图、编辑、检查、BOM
├── skills/xpedition-pcb/        # 版图 Skill：Layout、布局、布线、检查、制造文件
├── contract/                    # 规范的机器契约副本
├── examples/                    # 设计文件和一个布局任务
├── scripts/                     # 规范、版本和 npm 包装脚本
├── docs/                        # 兼容性、E2E、原生适配器、评测
└── .agent/                      # 固定版本的 AI 原生 CLI 规范
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

测试在 CLI 唯一跨越的进程边界上伪造适配器，驱动的是真实的 CLI，不需要 Xpedition。
`reference.release_readiness.level` 是 `beta`：每条公开命令都有命令级测试，对授权
Xpedition 的实测记录在 [`docs/E2E.md`](docs/E2E.md)；`reference` 写明了离 `stable`
还差什么。这个等级说的是这些证据，不保证自动化接口在另一台安装上表现完全一样。

## 链接

- [Agent 入口](AGENTS_zh.md)
- [Skills](skills/xpedition-cli/SKILL.md)：入口 Skill，以及 [xpedition-schematic](skills/xpedition-schematic/SKILL.md) 和 [xpedition-pcb](skills/xpedition-pcb/SKILL.md)
- [CLI 契约](.agent/CLI-SPEC_zh.md)
- [安全策略](SECURITY.md)
- [兼容性矩阵](docs/COMPATIBILITY.md)
- [原生适配器协议](docs/NATIVE_ADAPTER.md)
- [E2E 记录](docs/E2E.md)
- [Skill 跨模型评测](docs/EVALS.md)
- [更新日志](CHANGELOG.md)
- [贡献指南](CONTRIBUTING.md)
- [第三方声明](NOTICE.md)
- [MIT 许可证](LICENSE)
