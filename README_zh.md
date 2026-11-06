# xpedition-cli

让 AI Agent 用命令行完成 Xpedition 设计：从中心库元件、原理图，到板和生产文件，每次修改都先预览、确认，再读回核对。

<!-- driver:badges:start -->
![Agent-native](https://img.shields.io/badge/🤖-Agent--native-6f42c1)
[![CI](https://github.com/fatecannotbealtered/xpedition-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/fatecannotbealtered/xpedition-cli/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/@fateforge/xpedition-cli)](https://www.npmjs.com/package/@fateforge/xpedition-cli)
![drives](https://img.shields.io/badge/drives-Xpedition_2604-1f6feb)
![mode](https://img.shields.io/badge/mode-file_%C2%B7_session-0e8a16)
![platform](https://img.shields.io/badge/platform-Windows-555555)
![risk](https://img.shields.io/badge/risk-T1-d29922)
![maturity](https://img.shields.io/badge/maturity-draft-8957e5)
![spec](https://img.shields.io/badge/spec-tool--driver--cli--spec_1.0.3-0969da)
![license](https://img.shields.io/badge/license-Apache--2.0-blue)
<!-- driver:badges:end -->

[English](README.md) | 中文

<!-- driver:info-card:start -->
| 项目 | 内容 |
|---|---|
| 软件 | Xpedition 2604 |
| 模式 | file, session |
| 系统与 CPU | Windows (x64) |
| 风险等级 | T1 |
| 成熟度 | draft：draft 19 个，verified 0 个，stable 0 个 |
| 规范 | tool-driver-cli-spec 1.0.3 |
| 宿主组件 | 不需要 |
<!-- driver:info-card:end -->

## 安装

1. 从 npm 安装 xpedition-cli：

   ```bash
   npm install -g @fateforge/xpedition-cli
   ```

2. 运行一次 `update`。刚装好的已经是最新版本，所以 `update` 只把命令入口指向原生程序；这样每次调用都不用先启动 Node.js：

   ```bash
   xpedition-cli update
   ```

3. 从已安装的包里安装 Skill，Skill 从不从网络获取。把它们复制到 Agent 的 Skill 目录。用 bash：

   ```bash
   mkdir -p ~/.agents/skills
   cp -r "$(npm root -g)/@fateforge/xpedition-cli/skills/"* ~/.agents/skills/
   ```

   用 PowerShell：

   ```powershell
   New-Item -ItemType Directory -Force "$HOME\.agents\skills" | Out-Null
   Copy-Item -Recurse -Force "$(npm root -g)\@fateforge/xpedition-cli\skills\*" "$HOME\.agents\skills\"
   ```

   其他 Agent 的 Skill 目录做法相同。以后运行 `xpedition-cli update` 时，这几个目录里的副本会跟着工具一起更新：`~/.agents/skills/`、`~/.claude/skills/`、`~/.codex/skills/`、`~/.cursor/skills/`、`~/.gemini/skills/`。放在别处的副本不会更新。

## 能力

下面是 xpedition-cli 的全部命令，由命令登记生成；`(dangerous)` 表示该命令属于危险级，`xpedition-cli reference --command "<path>"` 给出单个命令的完整说明。

<!-- driver:command-map:start -->
- `reference`
- `context`
- `doctor`
- `changelog`
- `operation`: get, list, resolve
- `update`
- `session`: list, get, open, close
- `doc`: list, get, create, open, save, save-as, close, undo
- `job`: list, get, cancel
- `knowledge`: list, get, search, create, delete
- `project`: get, create
- `part`: list, get, create
- `packager`: run
- `schematic`: get, create (dangerous), check, export
- `pcb`: create, update, get, check, export
- `forward-annotation`: run
- `placement`: run
- `plane`: create
- `router`: run
<!-- driver:command-map:end -->

## 工作方式

xpedition-cli 每次调用都输出一个 JSON 信封。`xpedition-cli reference` 列出全部命令，`xpedition-cli reference --command "<path>"` 说明单个命令，包括它的参数、效果和在每种模式下的确认级别。

**实例与文档。** xpedition-cli 驱动正在运行的 Xpedition 实例。`xpedition-cli session list` 列出每个实例的会话 ID、状态和打开的文档，`xpedition-cli doc list` 列出文档。用 `--session <id>` 指定实例，用 `--doc <path-or-id>` 指定文档。有多个实例在运行而命令没有指定时，命令返回 `E_AMBIGUOUS` 和候选列表：xpedition-cli 从不按窗口焦点或最近使用来挑实例。`--doc` 指定的文档只有一个实例打开时，就选中这个实例。被某个实例打开的文件只经由这个实例修改，直接写这个文件会被 `E_CONFLICT` 拒绝。

**文件。** xpedition-cli 直接读写 Xpedition 的文件，不需要运行中的实例。`--doc <path>` 指定文件。

**确认级别。** 每次写操作都会检查授权范围、生成预览、在派发前再检查一次状态、锁定目标、读回修改并返回回执。只有确认方式随级别不同，级别由 xpedition-cli 根据命令声明的效果得出：

| 级别 | 适用 | 调用方式 |
|---|---|---|
| 直接执行 | 保存之前只在内存里的修改；启动实例；新建和打开文档；不覆盖已有文件的新文件；停止工作 | 一次调用就执行 |
| 两步确认 | 保存和覆盖文件；文件模式和设备模式下的修改；设置；宿主组件 | 第一次调用什么都不改，返回 `E_CONFIRMATION_REQUIRED`，带预览和确认令牌；再带 `--confirm <token>` 运行同一条命令才执行 |
| 危险 | 物理动作；把内容交给外部的人或系统；不可撤回的修改；放弃未保存的修改；关闭不是 xpedition-cli 启动的实例 | 与两步确认相同，确认的那次调用还要加 `--dangerous`；授权范围必须允许危险操作 |

`--dry-run` 显示任何写操作的预览，什么都不改。确认令牌只能用一次，10 分钟内有效，而且只对它预览过的那次修改有效。授权范围文件设了 `"confirm": "always"` 时，除停止工作外的所有写操作都改为两步确认，人可以逐个批准预览。

**会话模式下的保存。** 修改不等于保存。修改之后，回执报告 `persistence: unchanged`，文档仍有未保存的修改；只有 `xpedition-cli doc save` 才写入文档的文件。保存的预览列出上次保存以来的所有修改，包括人在 Xpedition 里做的修改（`origin: external`），以及结果仍然未知的写操作。

**文件模式下的保存。** 写文件就是保存：回执报告 `persistence: saved`，xpedition-cli 会重新读取文件来验证修改。

**回执。** 每次写操作都返回回执，分别报告修改是否执行、是否生效、是否送达、是否保存、是否验证。写操作的回复丢失时，xpedition-cli 返回 `E_OUTCOME_UNKNOWN` 和查明结果的命令，绝不会把写操作再发一遍。

## 授权

xpedition-cli 读取不需要许可，但只写入人在授权范围文件里允许的内容。这个 JSON 文件由人亲手写；没有命令会写它，Agent 也从不创建或修改它。xpedition-cli 从 `XPEDITION_CLI_SCOPE_FILE` 指定的路径读取它；没有设置这个变量时，读取状态目录里的 `scope.json`，不从其他任何地方读取。

```json
{
  "scope_id": "demo-rework",
  "effects": ["change", "persist"],
  "dangerous": false,
  "targets": ["file:///D:/projects/demo/"],
  "expires_at": "2027-01-31T18:00:00Z"
}
```

| 字段 | 含义 |
|---|---|
| `scope_id` | 这份授权的名称；操作记录会带上它 |
| `effects` | 写操作可以有的效果；读取不需要授权范围 |
| `dangerous` | 是否允许危险级的操作 |
| `targets` | 写操作可以触及的对象的目标键前缀：`file:///D:/projects/demo/` 覆盖这个文件夹下的所有文件，`untitled:` 覆盖新建的文档，`device:<id>` 覆盖一台设备 |
| `expires_at` | 授权到期的时间，用 UTC |
| `confirm` | 可选；设为 `always` 时，除停止工作外的每个写操作都要等人批准预览后才执行 |

授权范围不覆盖的写操作什么都不改，返回 `E_FORBIDDEN`，说明缺什么，并给出一段人可以直接粘贴进文件的片段。`xpedition-cli context` 显示当前生效的授权范围，`SECURITY_zh.md` 完整说明了授权范围。

各命令需要授权范围准许的效果：

| 效果 | 命令 |
|---|---|
| `artifact` | `project create`、`schematic export`、`pcb export` |
| `change` | `part create`、`packager run`、`pcb create`、`schematic create`、`forward-annotation run`、`pcb update`、`placement run`、`plane create`、`router run` |
| `persist` | `schematic create`（写符号文件）、`pcb create`（写工程文件）、`pcb export`（改输出设置文件）、`doc save` |
| `lifecycle` | `session open`、`session close`、`doc open`、`doc close`、`job cancel` |

`schematic create` 会替换它画的各页，属于危险档，授权范围里要有 `"dangerous": true` 才能运行。在一个工程文件夹里做完一块板，需要对这个文件夹准许 `lifecycle`、`change`、`artifact` 和 `persist`。

## 关于本工具

Xpedition 是 Siemens 的印制电路板设计软件。Designer 画原理图，Layout 画板，工程的中心库存放元件、符号、封装（cell）和焊盘栈。xpedition-cli 驱动同一台 Windows 电脑上装好并有许可证的 Xpedition 2604：Designer 和 Layout 经它们的 COM 接口驱动；中心库、打包、建板和导出 PDF 用 Xpedition 自带的命令行程序。

第一版覆盖从空工程到生产文件的整条流程：

1. `project create` 从模板复制工程；`part create` 按元件描述文件往中心库加元件。
2. `session open --doc Demo.prj` 启动 Designer；`schematic create` 按设计描述文件画原理图；`schematic check` 运行 Designer 的校验；`doc save` 存盘；`schematic export` 导出 PDF。
3. 在 Designer 里 `doc close` 以后，`packager run` 打包原理图，`pcb create` 建板。
4. `session open --doc PCB/Demo.pcb` 启动 Layout；`forward-annotation run` 把原理图带进板；`pcb update` 设板框；`placement run`、`plane create`、`router run` 布局、铺铜、布线；`pcb check` 跑 DRC；`doc save` 存盘；`pcb export` 出 ODB++、Gerber、NC 钻孔、坐标文件和 BOM。

每次修改都先预览，按确认档确认，执行后从 Xpedition 读回核对。一次调用做不完的工作是长作业，用 `job get` 跟进：启动和退出 Designer、Layout，打开、保存和关闭文档，以及 `project create`、`packager run`、`schematic create`、`forward-annotation run` 和 `pcb export`。

从 1.0.3 或更早的版本升级：这些版本没有 `update` 命令。请用 `npm install -g @fateforge/xpedition-cli` 重装，再从 Agent 的 Skill 文件夹里删掉旧的 `xpedition-schematic` 和 `xpedition-pcb` 两个文件夹；本版本只有一个 Skill：`xpedition-cli`。

## 配置

| 变量 | 默认值 | 含义 |
|---|---|---|
| `XPEDITION_CLI_STATE_DIR` | `~/.xpedition-cli` | 状态目录：操作记录、锁、更新检查的缓存和团队知识的登记 |
| `XPEDITION_CLI_SCOPE_FILE` | 状态目录里的 `scope.json` | 授权范围文件（见「授权」） |
| `HTTPS_PROXY`、`HTTP_PROXY`、`NO_PROXY` | 未设置 | `update` 和 `doctor` 用的代理，只有这两个命令访问 npm 仓库 |
| `XPEDITION_CLI_FAKE_HOST` | 未设置 | 只用于测试：把每次调用发给模拟宿主而不是 Xpedition，并把每个结果标为模拟结果 |

### 怎样找到 Xpedition

- **安装：**xpedition-cli 按这个顺序找 Xpedition 的安装（`SDD_HOME`）：变量 `XPEDITION_SDD_HOME` 和 `SDD_HOME`（先看进程，再看用户和本机的 Windows 环境设置），`HKEY_LOCAL_MACHINE\Software\Mentor Graphics\Releases` 下的版本条目，以及许可证文件旁边的版本文件夹。这个文件夹里必须有 `common/win64/bin/ExpeditionPCB.exe`。装了几套时，设 `XPEDITION_SDD_HOME` 指定一套。
- **版本：**从版本文件夹的名字读出，例如 `XPED2604` 或 `XENTP2604`。`doctor` 会报告。
- **启动程序：**一律经 `SDD_HOME/common/win64/bin` 下的启动器，它会设好这个版本的环境；进程里缺的许可证设置从 Windows 环境设置里取，从不打印。
- **运行中的实例：**从进程列表找（Designer 是 `viewdraw.exe`，Layout 是 `ExpeditionPCB.exe`），经 COM 连接。COM 每种程序只连得上一个实例，所以 xpedition-cli 同时只驱动一个 Designer 和一个 Layout；`session list` 把其余的显示为 blocked。

## 卸载

趁 npm 包还在、能列出 Skill 名称时，删除已安装的 Skill。用 bash：

```bash
for skill in "$(npm root -g)/@fateforge/xpedition-cli/skills/"*; do rm -rf ~/.agents/skills/"$(basename "$skill")"; done
```

用 PowerShell：

```powershell
Get-ChildItem "$(npm root -g)\@fateforge/xpedition-cli\skills" | ForEach-Object { Remove-Item -Recurse -Force "$HOME\.agents\skills\$($_.Name)" -ErrorAction SilentlyContinue }
```

复制过 Skill 的其他 Agent Skill 目录，如 `~/.claude/skills/`，也照此删除。然后卸载 npm 包：

```bash
npm uninstall -g @fateforge/xpedition-cli
```

在 Windows 上，如果 npm 全局 `node_modules` 文件夹（`npm root -g`）里有 `.xpedition-cli-trash`，也把它删掉；状态目录在另一块磁盘上时，`update` 会把旧的程序文件放在那里，等下次运行再删。

最后删除状态目录：`~/.xpedition-cli`，或 `XPEDITION_CLI_STATE_DIR` 指定的文件夹。里面有操作记录、锁、默认位置的授权范围文件和团队知识的登记；想保留记录就留着它。

```bash
rm -rf ~/.xpedition-cli
```

```powershell
Remove-Item -Recurse -Force "$HOME\.xpedition-cli"
```

## 开发

xpedition-cli 用 Python 写成，建立在 `xpedition_cli/_driver/` 里的驱动运行时之上；这是固定副本，从不修改。领域命令在 `xpedition_cli/commands/`，适配层在 `xpedition_cli/adapter/`。

```bash
python -m pip install --require-hashes --no-deps -r requirements-dev.txt
python scripts/spec.py check
python -m unittest discover -s tests
python scripts/spec.py suites
```

测试和一致性测试都用模拟宿主，从不需要 Xpedition。工作流程、行为矩阵和真实软件运行见 `CONTRIBUTING_zh.md`。

## 链接

- 规范：[tool-driver-cli-spec 1.0.0](.agent/)，固定副本在 `.agent/`
- [CHANGELOG.md](CHANGELOG.md)：每个版本的改动（英文）
- [SECURITY_zh.md](SECURITY_zh.md)：安全模型，以及怎样报告漏洞
- [CONTRIBUTING_zh.md](CONTRIBUTING_zh.md)：怎样参与贡献
- [docs/APPLICATION_zh.md](docs/APPLICATION_zh.md)：Xpedition 的能力调查
- [docs/COMPATIBILITY_zh.md](docs/COMPATIBILITY_zh.md)：xpedition-cli 在哪里能用
- [docs/E2E_zh.md](docs/E2E_zh.md)：真实软件验收及其记录
- 问题跟踪：<https://github.com/fatecannotbealtered/xpedition-cli/issues>
