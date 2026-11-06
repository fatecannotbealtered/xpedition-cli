# 真实软件验收

[English](E2E.md) | 中文

真实软件运行用真实的适配层，在真实的 Xpedition 上执行 xpedition-cli 的行为矩阵。它会做出真实的修改，所以只使用人在下面授权过的目标。

## 授权目标

真实软件运行可以使用的 Xpedition 实例、文件、文件夹和设备，唯一的清单在英文版 `docs/E2E.md` 的「Authorized Targets」一节；检查脚本和矩阵运行器只读那一份，本节不另列目标。只有人能往清单里加目标，或者由人在自己发的消息里明确委托的编码 Agent 加入：它先把目标给人看，人同意后再加。每个目标占一个列表项，以反引号括起的目标开头，写法与授权范围文件里的目标相同：文件或文件夹的 `file://` URL，或者设备的 `device:<id>`；后面跟一个冒号和对目标的说明。Xpedition 的实例没有固定的 ID，所以真实软件运行只使用人为这次运行启动的实例，打开的文档也只来自这里列出的文件和文件夹。真实软件运行不使用任何其他目标，真实软件运行所用的授权范围文件也只授予这里列出的目标。

清单以 `docs/E2E.md` 为准。

## 步骤

真实软件运行使用这些电脑：

- 一台 Windows 11 x64 电脑，装 Xpedition Enterprise 2604，用 file 和 session 两种模式。练习工程和评测文件夹由人列进「授权目标」。

在真实的 Xpedition 上运行行为矩阵：

1. 由人准备目标，比如专为测试复制的项目、为这次运行启动的实例，或为测试准备好的设备，并把它们列进「授权目标」。
2. 由人写（或明确委托编码 Agent 写）一个只授予这些目标的授权范围文件，包含被测命令需要的效果，并让 `XPEDITION_CLI_SCOPE_FILE` 指向它。把 `XPEDITION_CLI_STATE_DIR` 设为专供真实软件运行使用的目录。
3. 运行 `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>`，矩阵用到的每个占位符各给一个 `--set`，占位符名不带花括号，如 `--set doc=<file>`：`{session}` 是授权的实例，`{doc}` 是授权的文档或文件，`{device}` 是授权的设备，`{dir}` 是授权文件夹里新建的空目录。`{job}` 是作业 ID；不给时，运行器先运行 `--job-from` 指定的长命令（只有一个长命令时就是它）的 `normal` 行，用它启动的作业。`{inputs}` 不用 `--set`：它就是 `tests/inputs/`，里面的文件命令只读取，所以不需要授权。
4. 运行器执行适用的行，把真实软件记录写进 `docs/evidence/`，并填上每一行的实际结果和 `live:` 证据引用。实际结果与预期不符的行算作失败。
5. 在真实的 Xpedition 上无法按需制造的行，如 `disconnect`，可以标为 `unrunnable` 并写明原因；`normal`、`dialog` 和 `read_back_mismatch` 这三行不可以。`read_back_mismatch` 用 `--row read_back_mismatch` 运行：工具会在读回之前等一会儿，在这段时间里，到 Xpedition 里改掉它刚写的内容，或删掉它生成的文件。
6. 运行 `python scripts/spec.py generate` 更新支持矩阵和下面的索引，再把记录和矩阵一起提交。

要在真实的 Xpedition 上运行评测题，由人在「授权目标」里列出一个放评测工作目录的文件夹，以及题目的 `SCOPE` 用到的其他目标，比如设备。然后运行 `python scripts/spec.py eval --workspace-root <folder>`。运行器自己为每道题写授权范围文件，只授予这道题的文档和它的 `SCOPE` 列出的目标，并把评测报告写进 `docs/evidence/`。

真实软件记录只在它记录的工具版本与 xpedition-cli 的主版本和次版本都相同时才算作证据。记录里从不出现机密，带机密的参数显示为 `***`。

## 证据记录

`docs/evidence/` 里的真实软件记录，由 `python scripts/spec.py generate` 生成：

<!-- driver:evidence:start -->
| 记录 | 运行时间 | 软件 | 平台 | 结果 |
|---|---|---|---|---|
| [2026-11-06-073450-session-open.json](evidence/2026-11-06-073450-session-open.json) | 2026-11-06T07:34:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073351-pcb-create.json](evidence/2026-11-06-073351-pcb-create.json) | 2026-11-06T07:33:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073339-pcb-create.json](evidence/2026-11-06-073339-pcb-create.json) | 2026-11-06T07:33:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073338-pcb-create.json](evidence/2026-11-06-073338-pcb-create.json) | 2026-11-06T07:33:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073333-part-get.json](evidence/2026-11-06-073333-part-get.json) | 2026-11-06T07:33:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073331-part-create.json](evidence/2026-11-06-073331-part-create.json) | 2026-11-06T07:33:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073325-part-create.json](evidence/2026-11-06-073325-part-create.json) | 2026-11-06T07:33:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073321-part-create.json](evidence/2026-11-06-073321-part-create.json) | 2026-11-06T07:33:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073214-schematic-check.json](evidence/2026-11-06-073214-schematic-check.json) | 2026-11-06T07:32:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073209-schematic-create.json](evidence/2026-11-06-073209-schematic-create.json) | 2026-11-06T07:32:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073136-schematic-export.json](evidence/2026-11-06-073136-schematic-export.json) | 2026-11-06T07:31:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073131-schematic-check.json](evidence/2026-11-06-073131-schematic-check.json) | 2026-11-06T07:31:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-073032-doc-create.json](evidence/2026-11-06-073032-doc-create.json) | 2026-11-06T07:30:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072853-doc-create.json](evidence/2026-11-06-072853-doc-create.json) | 2026-11-06T07:28:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072807-doc-create.json](evidence/2026-11-06-072807-doc-create.json) | 2026-11-06T07:28:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072743-doc-create.json](evidence/2026-11-06-072743-doc-create.json) | 2026-11-06T07:27:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072724-doc-create.json](evidence/2026-11-06-072724-doc-create.json) | 2026-11-06T07:27:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072721-doc-create.json](evidence/2026-11-06-072721-doc-create.json) | 2026-11-06T07:27:21Z | Xpedition 2604 | windows | 2 次运行中 1 次通过 |
| [2026-11-06-072719-doc-create.json](evidence/2026-11-06-072719-doc-create.json) | 2026-11-06T07:27:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072717-doc-create.json](evidence/2026-11-06-072717-doc-create.json) | 2026-11-06T07:27:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072404-pcb-get.json](evidence/2026-11-06-072404-pcb-get.json) | 2026-11-06T07:24:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072402-pcb-update.json](evidence/2026-11-06-072402-pcb-update.json) | 2026-11-06T07:24:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072359-pcb-check.json](evidence/2026-11-06-072359-pcb-check.json) | 2026-11-06T07:23:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072336-router-run.json](evidence/2026-11-06-072336-router-run.json) | 2026-11-06T07:23:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072323-router-run.json](evidence/2026-11-06-072323-router-run.json) | 2026-11-06T07:23:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072321-router-run.json](evidence/2026-11-06-072321-router-run.json) | 2026-11-06T07:23:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072317-router-run.json](evidence/2026-11-06-072317-router-run.json) | 2026-11-06T07:23:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072314-router-run.json](evidence/2026-11-06-072314-router-run.json) | 2026-11-06T07:23:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072254-placement-run.json](evidence/2026-11-06-072254-placement-run.json) | 2026-11-06T07:22:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072249-placement-run.json](evidence/2026-11-06-072249-placement-run.json) | 2026-11-06T07:22:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072246-placement-run.json](evidence/2026-11-06-072246-placement-run.json) | 2026-11-06T07:22:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072244-placement-run.json](evidence/2026-11-06-072244-placement-run.json) | 2026-11-06T07:22:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072212-doc-open.json](evidence/2026-11-06-072212-doc-open.json) | 2026-11-06T07:22:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-072116-doc-close.json](evidence/2026-11-06-072116-doc-close.json) | 2026-11-06T07:21:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-070334-pcb-check.json](evidence/2026-11-06-070334-pcb-check.json) | 2026-11-06T07:03:34Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-070324-pcb-get.json](evidence/2026-11-06-070324-pcb-get.json) | 2026-11-06T07:03:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-060030-forward-annotation-run.json](evidence/2026-11-06-060030-forward-annotation-run.json) | 2026-11-06T06:00:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054838-doc-get.json](evidence/2026-11-06-054838-doc-get.json) | 2026-11-06T05:48:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054824-doc-list.json](evidence/2026-11-06-054824-doc-list.json) | 2026-11-06T05:48:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054820-schematic-export.json](evidence/2026-11-06-054820-schematic-export.json) | 2026-11-06T05:48:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054818-schematic-create.json](evidence/2026-11-06-054818-schematic-create.json) | 2026-11-06T05:48:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054816-schematic-check.json](evidence/2026-11-06-054816-schematic-check.json) | 2026-11-06T05:48:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054815-schematic-get.json](evidence/2026-11-06-054815-schematic-get.json) | 2026-11-06T05:48:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054221-session-list.json](evidence/2026-11-06-054221-session-list.json) | 2026-11-06T05:42:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054219-session-get.json](evidence/2026-11-06-054219-session-get.json) | 2026-11-06T05:42:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054217-session-close.json](evidence/2026-11-06-054217-session-close.json) | 2026-11-06T05:42:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054214-doc-save.json](evidence/2026-11-06-054214-doc-save.json) | 2026-11-06T05:42:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054212-doc-open.json](evidence/2026-11-06-054212-doc-open.json) | 2026-11-06T05:42:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054210-doc-close.json](evidence/2026-11-06-054210-doc-close.json) | 2026-11-06T05:42:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054208-pcb-export.json](evidence/2026-11-06-054208-pcb-export.json) | 2026-11-06T05:42:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054206-pcb-check.json](evidence/2026-11-06-054206-pcb-check.json) | 2026-11-06T05:42:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054204-pcb-get.json](evidence/2026-11-06-054204-pcb-get.json) | 2026-11-06T05:42:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054202-router-run.json](evidence/2026-11-06-054202-router-run.json) | 2026-11-06T05:42:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054200-plane-create.json](evidence/2026-11-06-054200-plane-create.json) | 2026-11-06T05:42:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054158-placement-run.json](evidence/2026-11-06-054158-placement-run.json) | 2026-11-06T05:41:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054156-pcb-update.json](evidence/2026-11-06-054156-pcb-update.json) | 2026-11-06T05:41:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-054153-forward-annotation-run.json](evidence/2026-11-06-054153-forward-annotation-run.json) | 2026-11-06T05:41:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053436-forward-annotation-run.json](evidence/2026-11-06-053436-forward-annotation-run.json) | 2026-11-06T05:34:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053318-schematic-create.json](evidence/2026-11-06-053318-schematic-create.json) | 2026-11-06T05:33:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053211-doc-list.json](evidence/2026-11-06-053211-doc-list.json) | 2026-11-06T05:32:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053209-pcb-export.json](evidence/2026-11-06-053209-pcb-export.json) | 2026-11-06T05:32:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053207-pcb-check.json](evidence/2026-11-06-053207-pcb-check.json) | 2026-11-06T05:32:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053205-pcb-get.json](evidence/2026-11-06-053205-pcb-get.json) | 2026-11-06T05:32:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053203-router-run.json](evidence/2026-11-06-053203-router-run.json) | 2026-11-06T05:32:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053201-plane-create.json](evidence/2026-11-06-053201-plane-create.json) | 2026-11-06T05:32:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053159-placement-run.json](evidence/2026-11-06-053159-placement-run.json) | 2026-11-06T05:31:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053158-pcb-update.json](evidence/2026-11-06-053158-pcb-update.json) | 2026-11-06T05:31:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053156-forward-annotation-run.json](evidence/2026-11-06-053156-forward-annotation-run.json) | 2026-11-06T05:31:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053154-schematic-export.json](evidence/2026-11-06-053154-schematic-export.json) | 2026-11-06T05:31:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053152-schematic-create.json](evidence/2026-11-06-053152-schematic-create.json) | 2026-11-06T05:31:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053150-schematic-check.json](evidence/2026-11-06-053150-schematic-check.json) | 2026-11-06T05:31:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053148-schematic-get.json](evidence/2026-11-06-053148-schematic-get.json) | 2026-11-06T05:31:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053147-doc-open.json](evidence/2026-11-06-053147-doc-open.json) | 2026-11-06T05:31:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053145-doc-save.json](evidence/2026-11-06-053145-doc-save.json) | 2026-11-06T05:31:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053143-doc-close.json](evidence/2026-11-06-053143-doc-close.json) | 2026-11-06T05:31:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053140-session-close.json](evidence/2026-11-06-053140-session-close.json) | 2026-11-06T05:31:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053138-session-get.json](evidence/2026-11-06-053138-session-get.json) | 2026-11-06T05:31:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053043-pcb-export.json](evidence/2026-11-06-053043-pcb-export.json) | 2026-11-06T05:30:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-053019-pcb-export.json](evidence/2026-11-06-053019-pcb-export.json) | 2026-11-06T05:30:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-052836-pcb-export.json](evidence/2026-11-06-052836-pcb-export.json) | 2026-11-06T05:28:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-052803-pcb-export.json](evidence/2026-11-06-052803-pcb-export.json) | 2026-11-06T05:28:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-052601-plane-create.json](evidence/2026-11-06-052601-plane-create.json) | 2026-11-06T05:26:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-052559-pcb-update.json](evidence/2026-11-06-052559-pcb-update.json) | 2026-11-06T05:25:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-052443-doc-save.json](evidence/2026-11-06-052443-doc-save.json) | 2026-11-06T05:24:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-052347-doc-save.json](evidence/2026-11-06-052347-doc-save.json) | 2026-11-06T05:23:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-052257-pcb-update.json](evidence/2026-11-06-052257-pcb-update.json) | 2026-11-06T05:22:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051950-plane-create.json](evidence/2026-11-06-051950-plane-create.json) | 2026-11-06T05:19:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051915-plane-create.json](evidence/2026-11-06-051915-plane-create.json) | 2026-11-06T05:19:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051913-plane-create.json](evidence/2026-11-06-051913-plane-create.json) | 2026-11-06T05:19:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051911-plane-create.json](evidence/2026-11-06-051911-plane-create.json) | 2026-11-06T05:19:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051739-placement-run.json](evidence/2026-11-06-051739-placement-run.json) | 2026-11-06T05:17:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051717-doc-save.json](evidence/2026-11-06-051717-doc-save.json) | 2026-11-06T05:17:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051630-doc-open.json](evidence/2026-11-06-051630-doc-open.json) | 2026-11-06T05:16:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051627-doc-close.json](evidence/2026-11-06-051627-doc-close.json) | 2026-11-06T05:16:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051625-forward-annotation-run.json](evidence/2026-11-06-051625-forward-annotation-run.json) | 2026-11-06T05:16:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051611-pcb-update.json](evidence/2026-11-06-051611-pcb-update.json) | 2026-11-06T05:16:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051609-pcb-update.json](evidence/2026-11-06-051609-pcb-update.json) | 2026-11-06T05:16:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051604-forward-annotation-run.json](evidence/2026-11-06-051604-forward-annotation-run.json) | 2026-11-06T05:16:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051534-forward-annotation-run.json](evidence/2026-11-06-051534-forward-annotation-run.json) | 2026-11-06T05:15:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051524-doc-close.json](evidence/2026-11-06-051524-doc-close.json) | 2026-11-06T05:15:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051524-doc-open.json](evidence/2026-11-06-051524-doc-open.json) | 2026-11-06T05:15:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051524-doc-save.json](evidence/2026-11-06-051524-doc-save.json) | 2026-11-06T05:15:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051524-pcb-check.json](evidence/2026-11-06-051524-pcb-check.json) | 2026-11-06T05:15:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051524-pcb-export.json](evidence/2026-11-06-051524-pcb-export.json) | 2026-11-06T05:15:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051524-pcb-get.json](evidence/2026-11-06-051524-pcb-get.json) | 2026-11-06T05:15:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051524-pcb-update.json](evidence/2026-11-06-051524-pcb-update.json) | 2026-11-06T05:15:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051524-plane-create.json](evidence/2026-11-06-051524-plane-create.json) | 2026-11-06T05:15:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051524-router-run.json](evidence/2026-11-06-051524-router-run.json) | 2026-11-06T05:15:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051523-placement-run.json](evidence/2026-11-06-051523-placement-run.json) | 2026-11-06T05:15:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051456-doc-open.json](evidence/2026-11-06-051456-doc-open.json) | 2026-11-06T05:14:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051438-doc-close.json](evidence/2026-11-06-051438-doc-close.json) | 2026-11-06T05:14:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051330-doc-save.json](evidence/2026-11-06-051330-doc-save.json) | 2026-11-06T05:13:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051328-doc-open.json](evidence/2026-11-06-051328-doc-open.json) | 2026-11-06T05:13:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051326-doc-close.json](evidence/2026-11-06-051326-doc-close.json) | 2026-11-06T05:13:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051325-forward-annotation-run.json](evidence/2026-11-06-051325-forward-annotation-run.json) | 2026-11-06T05:13:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051252-doc-save.json](evidence/2026-11-06-051252-doc-save.json) | 2026-11-06T05:12:52Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-051223-doc-close.json](evidence/2026-11-06-051223-doc-close.json) | 2026-11-06T05:12:23Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-06-051222-doc-open.json](evidence/2026-11-06-051222-doc-open.json) | 2026-11-06T05:12:22Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-051209-pcb-check.json](evidence/2026-11-06-051209-pcb-check.json) | 2026-11-06T05:12:09Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-051200-pcb-get.json](evidence/2026-11-06-051200-pcb-get.json) | 2026-11-06T05:12:00Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-051157-pcb-export.json](evidence/2026-11-06-051157-pcb-export.json) | 2026-11-06T05:11:57Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-06-051156-router-run.json](evidence/2026-11-06-051156-router-run.json) | 2026-11-06T05:11:56Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-06-051154-plane-create.json](evidence/2026-11-06-051154-plane-create.json) | 2026-11-06T05:11:54Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-06-051152-placement-run.json](evidence/2026-11-06-051152-placement-run.json) | 2026-11-06T05:11:52Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-06-051150-pcb-update.json](evidence/2026-11-06-051150-pcb-update.json) | 2026-11-06T05:11:50Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-06-051148-forward-annotation-run.json](evidence/2026-11-06-051148-forward-annotation-run.json) | 2026-11-06T05:11:48Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-06-051044-pcb-create.json](evidence/2026-11-06-051044-pcb-create.json) | 2026-11-06T05:10:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-051032-pcb-create.json](evidence/2026-11-06-051032-pcb-create.json) | 2026-11-06T05:10:32Z | Xpedition 2604 | windows | 3 次运行中 2 次通过 |
| [2026-11-06-051016-packager-run.json](evidence/2026-11-06-051016-packager-run.json) | 2026-11-06T05:10:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-050946-packager-run.json](evidence/2026-11-06-050946-packager-run.json) | 2026-11-06T05:09:46Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-050942-session-close.json](evidence/2026-11-06-050942-session-close.json) | 2026-11-06T05:09:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-050746-schematic-export.json](evidence/2026-11-06-050746-schematic-export.json) | 2026-11-06T05:07:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-050648-schematic-export.json](evidence/2026-11-06-050648-schematic-export.json) | 2026-11-06T05:06:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-050641-schematic-get.json](evidence/2026-11-06-050641-schematic-get.json) | 2026-11-06T05:06:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-050547-schematic-get.json](evidence/2026-11-06-050547-schematic-get.json) | 2026-11-06T05:05:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045719-schematic-create.json](evidence/2026-11-06-045719-schematic-create.json) | 2026-11-06T04:57:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045650-doc-get.json](evidence/2026-11-06-045650-doc-get.json) | 2026-11-06T04:56:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045650-schematic-export.json](evidence/2026-11-06-045650-schematic-export.json) | 2026-11-06T04:56:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045650-session-close.json](evidence/2026-11-06-045650-session-close.json) | 2026-11-06T04:56:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045522-doc-list.json](evidence/2026-11-06-045522-doc-list.json) | 2026-11-06T04:55:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045504-schematic-create.json](evidence/2026-11-06-045504-schematic-create.json) | 2026-11-06T04:55:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045502-schematic-check.json](evidence/2026-11-06-045502-schematic-check.json) | 2026-11-06T04:55:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045459-schematic-get.json](evidence/2026-11-06-045459-schematic-get.json) | 2026-11-06T04:54:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045458-session-list.json](evidence/2026-11-06-045458-session-list.json) | 2026-11-06T04:54:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045456-session-get.json](evidence/2026-11-06-045456-session-get.json) | 2026-11-06T04:54:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045414-session-close.json](evidence/2026-11-06-045414-session-close.json) | 2026-11-06T04:54:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045412-schematic-export.json](evidence/2026-11-06-045412-schematic-export.json) | 2026-11-06T04:54:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045408-doc-list.json](evidence/2026-11-06-045408-doc-list.json) | 2026-11-06T04:54:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045406-part-get.json](evidence/2026-11-06-045406-part-get.json) | 2026-11-06T04:54:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045404-part-list.json](evidence/2026-11-06-045404-part-list.json) | 2026-11-06T04:54:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045403-project-get.json](evidence/2026-11-06-045403-project-get.json) | 2026-11-06T04:54:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045401-doc-get.json](evidence/2026-11-06-045401-doc-get.json) | 2026-11-06T04:54:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045341-schematic-create.json](evidence/2026-11-06-045341-schematic-create.json) | 2026-11-06T04:53:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-045254-schematic-export.json](evidence/2026-11-06-045254-schematic-export.json) | 2026-11-06T04:52:54Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-06-045252-schematic-create.json](evidence/2026-11-06-045252-schematic-create.json) | 2026-11-06T04:52:52Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-06-045235-schematic-check.json](evidence/2026-11-06-045235-schematic-check.json) | 2026-11-06T04:52:35Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-045231-schematic-get.json](evidence/2026-11-06-045231-schematic-get.json) | 2026-11-06T04:52:31Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-045010-session-open.json](evidence/2026-11-06-045010-session-open.json) | 2026-11-06T04:50:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-044923-part-get.json](evidence/2026-11-06-044923-part-get.json) | 2026-11-06T04:49:23Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-044855-part-create.json](evidence/2026-11-06-044855-part-create.json) | 2026-11-06T04:48:55Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-044837-part-list.json](evidence/2026-11-06-044837-part-list.json) | 2026-11-06T04:48:37Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-06-044832-project-get.json](evidence/2026-11-06-044832-project-get.json) | 2026-11-06T04:48:32Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-044700-session-close.json](evidence/2026-11-06-044700-session-close.json) | 2026-11-06T04:47:00Z | Xpedition 2604 | windows | 3 次运行中 2 次通过 |
| [2026-11-06-044635-session-open.json](evidence/2026-11-06-044635-session-open.json) | 2026-11-06T04:46:35Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-044612-pcb-create.json](evidence/2026-11-06-044612-pcb-create.json) | 2026-11-06T04:46:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-044610-packager-run.json](evidence/2026-11-06-044610-packager-run.json) | 2026-11-06T04:46:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-044609-part-create.json](evidence/2026-11-06-044609-part-create.json) | 2026-11-06T04:46:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-06-044539-doc-list.json](evidence/2026-11-06-044539-doc-list.json) | 2026-11-06T04:45:39Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-044532-doc-get.json](evidence/2026-11-06-044532-doc-get.json) | 2026-11-06T04:45:32Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-044529-session-list.json](evidence/2026-11-06-044529-session-list.json) | 2026-11-06T04:45:29Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-06-044507-session-get.json](evidence/2026-11-06-044507-session-get.json) | 2026-11-06T04:45:07Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
<!-- driver:evidence:end -->
