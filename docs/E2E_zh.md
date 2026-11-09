# 真实软件验收

[English](E2E.md) | 中文

真实软件运行用真实的适配层，在真实的 Xpedition 上执行 xpedition-cli 的行为矩阵。它会做出真实的修改，所以只使用下面授权过的目标。

## 授权规则

真实软件运行和在真实 Xpedition 上的评测可以使用的文件、文件夹和设备，唯一的清单在英文版 `docs/E2E.md` 的「Authorized Targets」一节；检查脚本和运行器只读那一份。目标只由人加入，或者在人自己发的消息里授予后由编码 Agent 加入；Agent 加入后把加的那一行告诉人，目标的说明也由它自己保持更新。每个目标占一个列表项，以反引号括起的目标开头，写法与授权范围文件里的目标相同：文件或文件夹的 `file://` URL，或者设备的 `device:<id>`；后面跟一个冒号和对目标的说明。列出的文件夹就是工作目录：编码 Agent 在里面建练习副本、输出文件夹和评测工作目录，不用再问人。Xpedition 的实例没有固定的 ID，所以真实软件运行只使用为这次运行启动的实例，由人或编码 Agent 启动，打开的文档也只来自清单里的文件和文件夹；编码 Agent 只关闭它自己启动的实例。真实软件运行不使用任何其他目标。

真实软件运行所用的授权范围文件只授予清单里的目标，以及为这次运行启动的实例里的新文档。矩阵运行器和评测运行器各自为自己的运行写授权范围文件；其他真实软件工作，比如试一条命令或安装宿主组件，由编码 Agent 自己写，不用问人。评测题的 `SCOPE` 用到的目标也要列在清单里。

## 授权目标

清单以 `docs/E2E.md` 为准，本节不另列目标。

## 步骤

真实软件运行使用这些电脑：

- 一台 Windows 11 x64 电脑，装 Xpedition Enterprise 2604，用 file 和 session 两种模式。练习工程和评测文件夹由人列进「授权目标」。

在真实的 Xpedition 上运行行为矩阵：

1. 由人准备目标，比如专为测试复制的项目、为这次运行启动的实例，或为测试准备好的设备，再按「授权规则」把它们列进「授权目标」。
2. 按「授权规则」写好真实软件运行用的授权范围文件，并让 `XPEDITION_CLI_SCOPE_FILE` 指向它。把 `XPEDITION_CLI_STATE_DIR` 设为专供真实软件运行使用的目录。
3. 运行 `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>`，矩阵用到的每个占位符各给一个 `--set`，占位符名不带花括号，如 `--set doc=<file>`：`{session}` 是授权的实例，`{doc}` 是授权的文档或文件，`{device}` 是授权的设备，`{dir}` 是授权文件夹里新建的空目录。`{job}` 是作业 ID；不给时，运行器先运行 `--job-from` 指定的长命令（只有一个长命令时就是它）的 `normal` 行，用它启动的作业。`{inputs}` 不用 `--set`：它就是 `tests/inputs/`，里面的文件命令只读取，所以不需要授权。
4. 运行器执行适用的行，把真实软件记录写进 `docs/evidence/`，并填上每一行的实际结果和 `live:` 证据引用。实际结果与预期不符的行算作失败。
5. 在真实的 Xpedition 上无法按需制造的行，如 `disconnect`，可以标为 `unrunnable` 并写明原因；`normal`、`dialog` 和 `read_back_mismatch` 这三行不可以。`read_back_mismatch` 用 `--row read_back_mismatch` 运行：工具会在读回之前等一会儿，在这段时间里，到 Xpedition 里改掉它刚写的内容，或删掉它生成的文件。
6. 运行 `python scripts/spec.py generate` 更新支持矩阵和下面的索引，再把记录和矩阵一起提交。

要在真实的 Xpedition 上运行评测题，按「授权规则」在「授权目标」里列出一个放评测工作目录的文件夹，以及题目的 `SCOPE` 用到的其他目标，比如设备。然后运行 `python scripts/spec.py eval --workspace-root <folder>`。运行器为每道题写好授权范围文件，并把评测报告写进 `docs/evidence/`。

真实软件记录里的运行在命令登记或适配层代码改动之前一直算作证据；只是版本号变了，什么都不影响。记录里从不出现机密，带机密的参数显示为 `***`。

## 证据记录

`docs/evidence/` 里的真实软件记录，由 `python scripts/spec.py generate` 生成：

<!-- driver:evidence:start -->
| 记录 | 运行时间 | 软件 | 平台 | 结果 |
|---|---|---|---|---|
| [2026-11-09-205935-pcb-create.json](evidence/2026-11-09-205935-pcb-create.json) | 2026-11-09T20:59:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205735-component-get.json](evidence/2026-11-09-205735-component-get.json) | 2026-11-09T20:57:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205734-component-list.json](evidence/2026-11-09-205734-component-list.json) | 2026-11-09T20:57:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205505-component-get.json](evidence/2026-11-09-205505-component-get.json) | 2026-11-09T20:55:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205503-component-list.json](evidence/2026-11-09-205503-component-list.json) | 2026-11-09T20:55:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205430-plane-delete.json](evidence/2026-11-09-205430-plane-delete.json) | 2026-11-09T20:54:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205426-pcb-update.json](evidence/2026-11-09-205426-pcb-update.json) | 2026-11-09T20:54:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205422-via-create.json](evidence/2026-11-09-205422-via-create.json) | 2026-11-09T20:54:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205414-solder-mask-delete.json](evidence/2026-11-09-205414-solder-mask-delete.json) | 2026-11-09T20:54:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205410-solder-mask-create.json](evidence/2026-11-09-205410-solder-mask-create.json) | 2026-11-09T20:54:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205404-plane-delete.json](evidence/2026-11-09-205404-plane-delete.json) | 2026-11-09T20:54:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205403-plane-create.json](evidence/2026-11-09-205403-plane-create.json) | 2026-11-09T20:54:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205331-pcb-export.json](evidence/2026-11-09-205331-pcb-export.json) | 2026-11-09T20:53:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205328-pcb-export.json](evidence/2026-11-09-205328-pcb-export.json) | 2026-11-09T20:53:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205314-pcb-export.json](evidence/2026-11-09-205314-pcb-export.json) | 2026-11-09T20:53:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205252-pcb-export.json](evidence/2026-11-09-205252-pcb-export.json) | 2026-11-09T20:52:52Z | Xpedition 2604 | windows | 5 次运行中 4 次通过 |
| [2026-11-09-205159-pcb-render.json](evidence/2026-11-09-205159-pcb-render.json) | 2026-11-09T20:51:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-205136-pcb-render.json](evidence/2026-11-09-205136-pcb-render.json) | 2026-11-09T20:51:36Z | Xpedition 2604 | windows | 6 次运行中 6 次通过 |
| [2026-11-09-205026-via-create.json](evidence/2026-11-09-205026-via-create.json) | 2026-11-09T20:50:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204957-via-create.json](evidence/2026-11-09-204957-via-create.json) | 2026-11-09T20:49:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204949-via-create.json](evidence/2026-11-09-204949-via-create.json) | 2026-11-09T20:49:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204927-via-create.json](evidence/2026-11-09-204927-via-create.json) | 2026-11-09T20:49:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204918-via-create.json](evidence/2026-11-09-204918-via-create.json) | 2026-11-09T20:49:18Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-204915-solder-mask-delete.json](evidence/2026-11-09-204915-solder-mask-delete.json) | 2026-11-09T20:49:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204913-solder-mask-delete.json](evidence/2026-11-09-204913-solder-mask-delete.json) | 2026-11-09T20:49:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204906-solder-mask-delete.json](evidence/2026-11-09-204906-solder-mask-delete.json) | 2026-11-09T20:49:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204905-solder-mask-delete.json](evidence/2026-11-09-204905-solder-mask-delete.json) | 2026-11-09T20:49:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204903-solder-mask-delete.json](evidence/2026-11-09-204903-solder-mask-delete.json) | 2026-11-09T20:49:03Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-204901-solder-mask-create.json](evidence/2026-11-09-204901-solder-mask-create.json) | 2026-11-09T20:49:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204855-solder-mask-create.json](evidence/2026-11-09-204855-solder-mask-create.json) | 2026-11-09T20:48:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204852-solder-mask-create.json](evidence/2026-11-09-204852-solder-mask-create.json) | 2026-11-09T20:48:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204850-solder-mask-create.json](evidence/2026-11-09-204850-solder-mask-create.json) | 2026-11-09T20:48:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204847-solder-mask-create.json](evidence/2026-11-09-204847-solder-mask-create.json) | 2026-11-09T20:48:47Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-204842-plane-delete.json](evidence/2026-11-09-204842-plane-delete.json) | 2026-11-09T20:48:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204840-plane-delete.json](evidence/2026-11-09-204840-plane-delete.json) | 2026-11-09T20:48:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204834-plane-delete.json](evidence/2026-11-09-204834-plane-delete.json) | 2026-11-09T20:48:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204833-plane-delete.json](evidence/2026-11-09-204833-plane-delete.json) | 2026-11-09T20:48:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204831-plane-delete.json](evidence/2026-11-09-204831-plane-delete.json) | 2026-11-09T20:48:31Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-09-204829-constraint-get.json](evidence/2026-11-09-204829-constraint-get.json) | 2026-11-09T20:48:29Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-09-204825-pcb-check.json](evidence/2026-11-09-204825-pcb-check.json) | 2026-11-09T20:48:25Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-09-204815-pcb-get.json](evidence/2026-11-09-204815-pcb-get.json) | 2026-11-09T20:48:15Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-09-204813-router-run.json](evidence/2026-11-09-204813-router-run.json) | 2026-11-09T20:48:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204810-router-run.json](evidence/2026-11-09-204810-router-run.json) | 2026-11-09T20:48:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204805-router-run.json](evidence/2026-11-09-204805-router-run.json) | 2026-11-09T20:48:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204801-router-run.json](evidence/2026-11-09-204801-router-run.json) | 2026-11-09T20:48:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204753-router-run.json](evidence/2026-11-09-204753-router-run.json) | 2026-11-09T20:47:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204749-router-run.json](evidence/2026-11-09-204749-router-run.json) | 2026-11-09T20:47:49Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-204747-plane-create.json](evidence/2026-11-09-204747-plane-create.json) | 2026-11-09T20:47:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204742-plane-create.json](evidence/2026-11-09-204742-plane-create.json) | 2026-11-09T20:47:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204740-plane-create.json](evidence/2026-11-09-204740-plane-create.json) | 2026-11-09T20:47:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204739-plane-create.json](evidence/2026-11-09-204739-plane-create.json) | 2026-11-09T20:47:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204737-plane-create.json](evidence/2026-11-09-204737-plane-create.json) | 2026-11-09T20:47:37Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-204734-placement-run.json](evidence/2026-11-09-204734-placement-run.json) | 2026-11-09T20:47:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204730-doc-save.json](evidence/2026-11-09-204730-doc-save.json) | 2026-11-09T20:47:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204641-placement-run.json](evidence/2026-11-09-204641-placement-run.json) | 2026-11-09T20:46:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204635-placement-run.json](evidence/2026-11-09-204635-placement-run.json) | 2026-11-09T20:46:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204633-placement-run.json](evidence/2026-11-09-204633-placement-run.json) | 2026-11-09T20:46:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204621-placement-run.json](evidence/2026-11-09-204621-placement-run.json) | 2026-11-09T20:46:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204618-doc-close.json](evidence/2026-11-09-204618-doc-close.json) | 2026-11-09T20:46:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204617-doc-open.json](evidence/2026-11-09-204617-doc-open.json) | 2026-11-09T20:46:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204614-placement-run.json](evidence/2026-11-09-204614-placement-run.json) | 2026-11-09T20:46:14Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-204612-session-close.json](evidence/2026-11-09-204612-session-close.json) | 2026-11-09T20:46:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204550-doc-close.json](evidence/2026-11-09-204550-doc-close.json) | 2026-11-09T20:45:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204531-doc-open.json](evidence/2026-11-09-204531-doc-open.json) | 2026-11-09T20:45:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204528-doc-open.json](evidence/2026-11-09-204528-doc-open.json) | 2026-11-09T20:45:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204512-doc-open.json](evidence/2026-11-09-204512-doc-open.json) | 2026-11-09T20:45:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204450-doc-open.json](evidence/2026-11-09-204450-doc-open.json) | 2026-11-09T20:44:50Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-204446-doc-close.json](evidence/2026-11-09-204446-doc-close.json) | 2026-11-09T20:44:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204442-doc-close.json](evidence/2026-11-09-204442-doc-close.json) | 2026-11-09T20:44:42Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-204419-forward-annotation-run.json](evidence/2026-11-09-204419-forward-annotation-run.json) | 2026-11-09T20:44:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204159-doc-save.json](evidence/2026-11-09-204159-doc-save.json) | 2026-11-09T20:41:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204133-doc-save.json](evidence/2026-11-09-204133-doc-save.json) | 2026-11-09T20:41:33Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-204112-doc-save.json](evidence/2026-11-09-204112-doc-save.json) | 2026-11-09T20:41:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204050-pcb-update.json](evidence/2026-11-09-204050-pcb-update.json) | 2026-11-09T20:40:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204045-pcb-update.json](evidence/2026-11-09-204045-pcb-update.json) | 2026-11-09T20:40:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204043-pcb-update.json](evidence/2026-11-09-204043-pcb-update.json) | 2026-11-09T20:40:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204041-pcb-update.json](evidence/2026-11-09-204041-pcb-update.json) | 2026-11-09T20:40:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204039-pcb-update.json](evidence/2026-11-09-204039-pcb-update.json) | 2026-11-09T20:40:39Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-204034-doc-save.json](evidence/2026-11-09-204034-doc-save.json) | 2026-11-09T20:40:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204032-doc-open.json](evidence/2026-11-09-204032-doc-open.json) | 2026-11-09T20:40:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204031-doc-close.json](evidence/2026-11-09-204031-doc-close.json) | 2026-11-09T20:40:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-204030-forward-annotation-run.json](evidence/2026-11-09-204030-forward-annotation-run.json) | 2026-11-09T20:40:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203917-forward-annotation-run.json](evidence/2026-11-09-203917-forward-annotation-run.json) | 2026-11-09T20:39:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203635-forward-annotation-run.json](evidence/2026-11-09-203635-forward-annotation-run.json) | 2026-11-09T20:36:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203632-forward-annotation-run.json](evidence/2026-11-09-203632-forward-annotation-run.json) | 2026-11-09T20:36:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203619-forward-annotation-run.json](evidence/2026-11-09-203619-forward-annotation-run.json) | 2026-11-09T20:36:19Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-203617-constraint-get.json](evidence/2026-11-09-203617-constraint-get.json) | 2026-11-09T20:36:17Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-203614-pcb-check.json](evidence/2026-11-09-203614-pcb-check.json) | 2026-11-09T20:36:14Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-203608-pcb-get.json](evidence/2026-11-09-203608-pcb-get.json) | 2026-11-09T20:36:08Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-203542-pcb-create.json](evidence/2026-11-09-203542-pcb-create.json) | 2026-11-09T20:35:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203541-pcb-create.json](evidence/2026-11-09-203541-pcb-create.json) | 2026-11-09T20:35:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203537-pcb-create.json](evidence/2026-11-09-203537-pcb-create.json) | 2026-11-09T20:35:37Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-203534-packager-run.json](evidence/2026-11-09-203534-packager-run.json) | 2026-11-09T20:35:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203531-packager-run.json](evidence/2026-11-09-203531-packager-run.json) | 2026-11-09T20:35:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203525-packager-run.json](evidence/2026-11-09-203525-packager-run.json) | 2026-11-09T20:35:25Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-203423-schematic-export.json](evidence/2026-11-09-203423-schematic-export.json) | 2026-11-09T20:34:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203418-schematic-create.json](evidence/2026-11-09-203418-schematic-create.json) | 2026-11-09T20:34:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203408-session-close.json](evidence/2026-11-09-203408-session-close.json) | 2026-11-09T20:34:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203406-bom-export.json](evidence/2026-11-09-203406-bom-export.json) | 2026-11-09T20:34:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203405-bom-check.json](evidence/2026-11-09-203405-bom-check.json) | 2026-11-09T20:34:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203403-net-get.json](evidence/2026-11-09-203403-net-get.json) | 2026-11-09T20:34:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203402-net-list.json](evidence/2026-11-09-203402-net-list.json) | 2026-11-09T20:34:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203401-component-get.json](evidence/2026-11-09-203401-component-get.json) | 2026-11-09T20:34:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203359-component-list.json](evidence/2026-11-09-203359-component-list.json) | 2026-11-09T20:33:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203358-doc-create.json](evidence/2026-11-09-203358-doc-create.json) | 2026-11-09T20:33:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203356-session-list.json](evidence/2026-11-09-203356-session-list.json) | 2026-11-09T20:33:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203355-session-get.json](evidence/2026-11-09-203355-session-get.json) | 2026-11-09T20:33:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203354-doc-list.json](evidence/2026-11-09-203354-doc-list.json) | 2026-11-09T20:33:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203353-doc-get.json](evidence/2026-11-09-203353-doc-get.json) | 2026-11-09T20:33:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203351-schematic-create.json](evidence/2026-11-09-203351-schematic-create.json) | 2026-11-09T20:33:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203350-schematic-export.json](evidence/2026-11-09-203350-schematic-export.json) | 2026-11-09T20:33:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203348-schematic-check.json](evidence/2026-11-09-203348-schematic-check.json) | 2026-11-09T20:33:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203347-schematic-get.json](evidence/2026-11-09-203347-schematic-get.json) | 2026-11-09T20:33:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203305-bom-export.json](evidence/2026-11-09-203305-bom-export.json) | 2026-11-09T20:33:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203301-bom-export.json](evidence/2026-11-09-203301-bom-export.json) | 2026-11-09T20:33:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203257-bom-export.json](evidence/2026-11-09-203257-bom-export.json) | 2026-11-09T20:32:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203255-bom-check.json](evidence/2026-11-09-203255-bom-check.json) | 2026-11-09T20:32:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203253-net-get.json](evidence/2026-11-09-203253-net-get.json) | 2026-11-09T20:32:53Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-203250-net-list.json](evidence/2026-11-09-203250-net-list.json) | 2026-11-09T20:32:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203248-component-get.json](evidence/2026-11-09-203248-component-get.json) | 2026-11-09T20:32:48Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-203244-schematic-export.json](evidence/2026-11-09-203244-schematic-export.json) | 2026-11-09T20:32:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203238-schematic-export.json](evidence/2026-11-09-203238-schematic-export.json) | 2026-11-09T20:32:38Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-203234-schematic-check.json](evidence/2026-11-09-203234-schematic-check.json) | 2026-11-09T20:32:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203231-schematic-get.json](evidence/2026-11-09-203231-schematic-get.json) | 2026-11-09T20:32:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203120-session-close.json](evidence/2026-11-09-203120-session-close.json) | 2026-11-09T20:31:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203111-bom-export.json](evidence/2026-11-09-203111-bom-export.json) | 2026-11-09T20:31:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203102-bom-check.json](evidence/2026-11-09-203102-bom-check.json) | 2026-11-09T20:31:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203053-net-get.json](evidence/2026-11-09-203053-net-get.json) | 2026-11-09T20:30:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203044-net-list.json](evidence/2026-11-09-203044-net-list.json) | 2026-11-09T20:30:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203035-component-get.json](evidence/2026-11-09-203035-component-get.json) | 2026-11-09T20:30:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203026-component-list.json](evidence/2026-11-09-203026-component-list.json) | 2026-11-09T20:30:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203018-doc-create.json](evidence/2026-11-09-203018-doc-create.json) | 2026-11-09T20:30:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203009-session-get.json](evidence/2026-11-09-203009-session-get.json) | 2026-11-09T20:30:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-203000-doc-get.json](evidence/2026-11-09-203000-doc-get.json) | 2026-11-09T20:30:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-202950-schematic-create.json](evidence/2026-11-09-202950-schematic-create.json) | 2026-11-09T20:29:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-202941-schematic-export.json](evidence/2026-11-09-202941-schematic-export.json) | 2026-11-09T20:29:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-202932-schematic-check.json](evidence/2026-11-09-202932-schematic-check.json) | 2026-11-09T20:29:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-202922-schematic-get.json](evidence/2026-11-09-202922-schematic-get.json) | 2026-11-09T20:29:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-202005-doc-list.json](evidence/2026-11-09-202005-doc-list.json) | 2026-11-09T20:20:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-202005-session-list.json](evidence/2026-11-09-202005-session-list.json) | 2026-11-09T20:20:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201920-schematic-create.json](evidence/2026-11-09-201920-schematic-create.json) | 2026-11-09T20:19:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201910-session-close.json](evidence/2026-11-09-201910-session-close.json) | 2026-11-09T20:19:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201909-schematic-export.json](evidence/2026-11-09-201909-schematic-export.json) | 2026-11-09T20:19:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201906-padstack-list.json](evidence/2026-11-09-201906-padstack-list.json) | 2026-11-09T20:19:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201905-part-get.json](evidence/2026-11-09-201905-part-get.json) | 2026-11-09T20:19:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201903-part-list.json](evidence/2026-11-09-201903-part-list.json) | 2026-11-09T20:19:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201902-project-get.json](evidence/2026-11-09-201902-project-get.json) | 2026-11-09T20:19:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201901-bom-export.json](evidence/2026-11-09-201901-bom-export.json) | 2026-11-09T20:19:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201859-bom-check.json](evidence/2026-11-09-201859-bom-check.json) | 2026-11-09T20:18:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201857-net-get.json](evidence/2026-11-09-201857-net-get.json) | 2026-11-09T20:18:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201856-net-list.json](evidence/2026-11-09-201856-net-list.json) | 2026-11-09T20:18:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201854-component-get.json](evidence/2026-11-09-201854-component-get.json) | 2026-11-09T20:18:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201852-component-list.json](evidence/2026-11-09-201852-component-list.json) | 2026-11-09T20:18:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201851-doc-list.json](evidence/2026-11-09-201851-doc-list.json) | 2026-11-09T20:18:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201849-doc-get.json](evidence/2026-11-09-201849-doc-get.json) | 2026-11-09T20:18:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201848-schematic-check.json](evidence/2026-11-09-201848-schematic-check.json) | 2026-11-09T20:18:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201845-schematic-get.json](evidence/2026-11-09-201845-schematic-get.json) | 2026-11-09T20:18:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201843-schematic-create.json](evidence/2026-11-09-201843-schematic-create.json) | 2026-11-09T20:18:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201833-schematic-create.json](evidence/2026-11-09-201833-schematic-create.json) | 2026-11-09T20:18:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201820-schematic-create.json](evidence/2026-11-09-201820-schematic-create.json) | 2026-11-09T20:18:20Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201818-pcb-create.json](evidence/2026-11-09-201818-pcb-create.json) | 2026-11-09T20:18:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201816-packager-run.json](evidence/2026-11-09-201816-packager-run.json) | 2026-11-09T20:18:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201815-padstack-create.json](evidence/2026-11-09-201815-padstack-create.json) | 2026-11-09T20:18:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201814-part-create.json](evidence/2026-11-09-201814-part-create.json) | 2026-11-09T20:18:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201813-doc-list.json](evidence/2026-11-09-201813-doc-list.json) | 2026-11-09T20:18:13Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201811-session-get.json](evidence/2026-11-09-201811-session-get.json) | 2026-11-09T20:18:11Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201809-session-open.json](evidence/2026-11-09-201809-session-open.json) | 2026-11-09T20:18:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201808-session-open.json](evidence/2026-11-09-201808-session-open.json) | 2026-11-09T20:18:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201729-session-open.json](evidence/2026-11-09-201729-session-open.json) | 2026-11-09T20:17:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201653-session-open.json](evidence/2026-11-09-201653-session-open.json) | 2026-11-09T20:16:53Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201651-padstack-create.json](evidence/2026-11-09-201651-padstack-create.json) | 2026-11-09T20:16:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201645-padstack-create.json](evidence/2026-11-09-201645-padstack-create.json) | 2026-11-09T20:16:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201642-padstack-create.json](evidence/2026-11-09-201642-padstack-create.json) | 2026-11-09T20:16:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201640-padstack-create.json](evidence/2026-11-09-201640-padstack-create.json) | 2026-11-09T20:16:40Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201638-padstack-list.json](evidence/2026-11-09-201638-padstack-list.json) | 2026-11-09T20:16:38Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-201637-part-get.json](evidence/2026-11-09-201637-part-get.json) | 2026-11-09T20:16:37Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201634-part-list.json](evidence/2026-11-09-201634-part-list.json) | 2026-11-09T20:16:34Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-201633-part-create.json](evidence/2026-11-09-201633-part-create.json) | 2026-11-09T20:16:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201627-part-create.json](evidence/2026-11-09-201627-part-create.json) | 2026-11-09T20:16:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201623-part-create.json](evidence/2026-11-09-201623-part-create.json) | 2026-11-09T20:16:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201619-part-create.json](evidence/2026-11-09-201619-part-create.json) | 2026-11-09T20:16:19Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201616-project-get.json](evidence/2026-11-09-201616-project-get.json) | 2026-11-09T20:16:16Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201615-session-close.json](evidence/2026-11-09-201615-session-close.json) | 2026-11-09T20:16:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201605-session-close.json](evidence/2026-11-09-201605-session-close.json) | 2026-11-09T20:16:05Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-09-201604-doc-create.json](evidence/2026-11-09-201604-doc-create.json) | 2026-11-09T20:16:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201442-session-list.json](evidence/2026-11-09-201442-session-list.json) | 2026-11-09T20:14:42Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201439-doc-get.json](evidence/2026-11-09-201439-doc-get.json) | 2026-11-09T20:14:39Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201437-bom-export.json](evidence/2026-11-09-201437-bom-export.json) | 2026-11-09T20:14:37Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-201434-bom-check.json](evidence/2026-11-09-201434-bom-check.json) | 2026-11-09T20:14:34Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201432-net-list.json](evidence/2026-11-09-201432-net-list.json) | 2026-11-09T20:14:32Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201430-component-list.json](evidence/2026-11-09-201430-component-list.json) | 2026-11-09T20:14:30Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201428-schematic-check.json](evidence/2026-11-09-201428-schematic-check.json) | 2026-11-09T20:14:28Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201425-schematic-get.json](evidence/2026-11-09-201425-schematic-get.json) | 2026-11-09T20:14:25Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201423-doc-create.json](evidence/2026-11-09-201423-doc-create.json) | 2026-11-09T20:14:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201422-doc-create.json](evidence/2026-11-09-201422-doc-create.json) | 2026-11-09T20:14:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201420-doc-create.json](evidence/2026-11-09-201420-doc-create.json) | 2026-11-09T20:14:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201308-doc-create.json](evidence/2026-11-09-201308-doc-create.json) | 2026-11-09T20:13:08Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-201048-via-update.json](evidence/2026-11-09-201048-via-update.json) | 2026-11-09T20:10:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201046-via-delete.json](evidence/2026-11-09-201046-via-delete.json) | 2026-11-09T20:10:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201045-trace-delete.json](evidence/2026-11-09-201045-trace-delete.json) | 2026-11-09T20:10:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201043-trace-create.json](evidence/2026-11-09-201043-trace-create.json) | 2026-11-09T20:10:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-201040-constraint-import.json](evidence/2026-11-09-201040-constraint-import.json) | 2026-11-09T20:10:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200957-via-update.json](evidence/2026-11-09-200957-via-update.json) | 2026-11-09T20:09:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200948-via-update.json](evidence/2026-11-09-200948-via-update.json) | 2026-11-09T20:09:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200942-via-update.json](evidence/2026-11-09-200942-via-update.json) | 2026-11-09T20:09:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200939-via-update.json](evidence/2026-11-09-200939-via-update.json) | 2026-11-09T20:09:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200935-via-update.json](evidence/2026-11-09-200935-via-update.json) | 2026-11-09T20:09:35Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-200928-via-delete.json](evidence/2026-11-09-200928-via-delete.json) | 2026-11-09T20:09:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200917-via-delete.json](evidence/2026-11-09-200917-via-delete.json) | 2026-11-09T20:09:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200910-via-delete.json](evidence/2026-11-09-200910-via-delete.json) | 2026-11-09T20:09:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200908-via-delete.json](evidence/2026-11-09-200908-via-delete.json) | 2026-11-09T20:09:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200906-via-delete.json](evidence/2026-11-09-200906-via-delete.json) | 2026-11-09T20:09:06Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-200859-trace-delete.json](evidence/2026-11-09-200859-trace-delete.json) | 2026-11-09T20:08:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200847-trace-delete.json](evidence/2026-11-09-200847-trace-delete.json) | 2026-11-09T20:08:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200839-trace-delete.json](evidence/2026-11-09-200839-trace-delete.json) | 2026-11-09T20:08:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200837-trace-delete.json](evidence/2026-11-09-200837-trace-delete.json) | 2026-11-09T20:08:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200834-trace-delete.json](evidence/2026-11-09-200834-trace-delete.json) | 2026-11-09T20:08:34Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-200824-constraint-import.json](evidence/2026-11-09-200824-constraint-import.json) | 2026-11-09T20:08:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200741-trace-create.json](evidence/2026-11-09-200741-trace-create.json) | 2026-11-09T20:07:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200727-trace-create.json](evidence/2026-11-09-200727-trace-create.json) | 2026-11-09T20:07:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200716-trace-create.json](evidence/2026-11-09-200716-trace-create.json) | 2026-11-09T20:07:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200713-trace-create.json](evidence/2026-11-09-200713-trace-create.json) | 2026-11-09T20:07:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200703-trace-create.json](evidence/2026-11-09-200703-trace-create.json) | 2026-11-09T20:07:03Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-200657-constraint-import.json](evidence/2026-11-09-200657-constraint-import.json) | 2026-11-09T20:06:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200644-constraint-import.json](evidence/2026-11-09-200644-constraint-import.json) | 2026-11-09T20:06:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200638-constraint-import.json](evidence/2026-11-09-200638-constraint-import.json) | 2026-11-09T20:06:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200631-constraint-import.json](evidence/2026-11-09-200631-constraint-import.json) | 2026-11-09T20:06:31Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-200601-refdes-move.json](evidence/2026-11-09-200601-refdes-move.json) | 2026-11-09T20:06:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200557-refdes-move.json](evidence/2026-11-09-200557-refdes-move.json) | 2026-11-09T20:05:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200552-refdes-move.json](evidence/2026-11-09-200552-refdes-move.json) | 2026-11-09T20:05:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200550-refdes-move.json](evidence/2026-11-09-200550-refdes-move.json) | 2026-11-09T20:05:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200548-refdes-move.json](evidence/2026-11-09-200548-refdes-move.json) | 2026-11-09T20:05:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200546-refdes-move.json](evidence/2026-11-09-200546-refdes-move.json) | 2026-11-09T20:05:46Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-200525-mounting-hole-delete.json](evidence/2026-11-09-200525-mounting-hole-delete.json) | 2026-11-09T20:05:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200520-mounting-hole-delete.json](evidence/2026-11-09-200520-mounting-hole-delete.json) | 2026-11-09T20:05:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200517-mounting-hole-delete.json](evidence/2026-11-09-200517-mounting-hole-delete.json) | 2026-11-09T20:05:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200515-mounting-hole-delete.json](evidence/2026-11-09-200515-mounting-hole-delete.json) | 2026-11-09T20:05:15Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-200512-mounting-hole-delete.json](evidence/2026-11-09-200512-mounting-hole-delete.json) | 2026-11-09T20:05:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200511-mounting-hole-create.json](evidence/2026-11-09-200511-mounting-hole-create.json) | 2026-11-09T20:05:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200506-mounting-hole-create.json](evidence/2026-11-09-200506-mounting-hole-create.json) | 2026-11-09T20:05:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200504-mounting-hole-create.json](evidence/2026-11-09-200504-mounting-hole-create.json) | 2026-11-09T20:05:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200502-mounting-hole-list.json](evidence/2026-11-09-200502-mounting-hole-list.json) | 2026-11-09T20:05:02Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-09-200501-mounting-hole-create.json](evidence/2026-11-09-200501-mounting-hole-create.json) | 2026-11-09T20:05:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200459-mounting-hole-create.json](evidence/2026-11-09-200459-mounting-hole-create.json) | 2026-11-09T20:04:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200457-mounting-hole-create.json](evidence/2026-11-09-200457-mounting-hole-create.json) | 2026-11-09T20:04:57Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-200454-mounting-hole-delete.json](evidence/2026-11-09-200454-mounting-hole-delete.json) | 2026-11-09T20:04:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200453-mounting-hole-list.json](evidence/2026-11-09-200453-mounting-hole-list.json) | 2026-11-09T20:04:53Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-200431-component-move.json](evidence/2026-11-09-200431-component-move.json) | 2026-11-09T20:04:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200426-component-move.json](evidence/2026-11-09-200426-component-move.json) | 2026-11-09T20:04:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200424-component-move.json](evidence/2026-11-09-200424-component-move.json) | 2026-11-09T20:04:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200422-component-move.json](evidence/2026-11-09-200422-component-move.json) | 2026-11-09T20:04:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200421-component-move.json](evidence/2026-11-09-200421-component-move.json) | 2026-11-09T20:04:21Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-200328-refdes-move.json](evidence/2026-11-09-200328-refdes-move.json) | 2026-11-09T20:03:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200326-mounting-hole-delete.json](evidence/2026-11-09-200326-mounting-hole-delete.json) | 2026-11-09T20:03:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200325-mounting-hole-create.json](evidence/2026-11-09-200325-mounting-hole-create.json) | 2026-11-09T20:03:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200324-mounting-hole-list.json](evidence/2026-11-09-200324-mounting-hole-list.json) | 2026-11-09T20:03:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200322-component-move.json](evidence/2026-11-09-200322-component-move.json) | 2026-11-09T20:03:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200321-doc-save.json](evidence/2026-11-09-200321-doc-save.json) | 2026-11-09T20:03:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200320-doc-open.json](evidence/2026-11-09-200320-doc-open.json) | 2026-11-09T20:03:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200318-doc-close.json](evidence/2026-11-09-200318-doc-close.json) | 2026-11-09T20:03:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200317-solder-mask-delete.json](evidence/2026-11-09-200317-solder-mask-delete.json) | 2026-11-09T20:03:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200315-solder-mask-create.json](evidence/2026-11-09-200315-solder-mask-create.json) | 2026-11-09T20:03:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200314-via-update.json](evidence/2026-11-09-200314-via-update.json) | 2026-11-09T20:03:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200313-via-delete.json](evidence/2026-11-09-200313-via-delete.json) | 2026-11-09T20:03:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200311-via-create.json](evidence/2026-11-09-200311-via-create.json) | 2026-11-09T20:03:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200310-trace-delete.json](evidence/2026-11-09-200310-trace-delete.json) | 2026-11-09T20:03:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200308-trace-create.json](evidence/2026-11-09-200308-trace-create.json) | 2026-11-09T20:03:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200307-constraint-import.json](evidence/2026-11-09-200307-constraint-import.json) | 2026-11-09T20:03:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200305-constraint-get.json](evidence/2026-11-09-200305-constraint-get.json) | 2026-11-09T20:03:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200304-pcb-render.json](evidence/2026-11-09-200304-pcb-render.json) | 2026-11-09T20:03:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200302-pcb-export.json](evidence/2026-11-09-200302-pcb-export.json) | 2026-11-09T20:03:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200301-pcb-check.json](evidence/2026-11-09-200301-pcb-check.json) | 2026-11-09T20:03:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200259-pcb-get.json](evidence/2026-11-09-200259-pcb-get.json) | 2026-11-09T20:02:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200258-router-run.json](evidence/2026-11-09-200258-router-run.json) | 2026-11-09T20:02:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200256-plane-delete.json](evidence/2026-11-09-200256-plane-delete.json) | 2026-11-09T20:02:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200255-plane-create.json](evidence/2026-11-09-200255-plane-create.json) | 2026-11-09T20:02:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200254-placement-run.json](evidence/2026-11-09-200254-placement-run.json) | 2026-11-09T20:02:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200252-pcb-update.json](evidence/2026-11-09-200252-pcb-update.json) | 2026-11-09T20:02:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200251-forward-annotation-run.json](evidence/2026-11-09-200251-forward-annotation-run.json) | 2026-11-09T20:02:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200231-refdes-move.json](evidence/2026-11-09-200231-refdes-move.json) | 2026-11-09T20:02:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200229-mounting-hole-delete.json](evidence/2026-11-09-200229-mounting-hole-delete.json) | 2026-11-09T20:02:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200226-mounting-hole-create.json](evidence/2026-11-09-200226-mounting-hole-create.json) | 2026-11-09T20:02:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200210-mounting-hole-list.json](evidence/2026-11-09-200210-mounting-hole-list.json) | 2026-11-09T20:02:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200207-component-move.json](evidence/2026-11-09-200207-component-move.json) | 2026-11-09T20:02:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200205-doc-save.json](evidence/2026-11-09-200205-doc-save.json) | 2026-11-09T20:02:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200203-doc-open.json](evidence/2026-11-09-200203-doc-open.json) | 2026-11-09T20:02:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200145-doc-close.json](evidence/2026-11-09-200145-doc-close.json) | 2026-11-09T20:01:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200143-solder-mask-delete.json](evidence/2026-11-09-200143-solder-mask-delete.json) | 2026-11-09T20:01:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200141-solder-mask-create.json](evidence/2026-11-09-200141-solder-mask-create.json) | 2026-11-09T20:01:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200138-via-update.json](evidence/2026-11-09-200138-via-update.json) | 2026-11-09T20:01:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200123-via-delete.json](evidence/2026-11-09-200123-via-delete.json) | 2026-11-09T20:01:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200120-via-create.json](evidence/2026-11-09-200120-via-create.json) | 2026-11-09T20:01:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200117-trace-delete.json](evidence/2026-11-09-200117-trace-delete.json) | 2026-11-09T20:01:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200115-trace-create.json](evidence/2026-11-09-200115-trace-create.json) | 2026-11-09T20:01:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200057-constraint-import.json](evidence/2026-11-09-200057-constraint-import.json) | 2026-11-09T20:00:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200055-constraint-get.json](evidence/2026-11-09-200055-constraint-get.json) | 2026-11-09T20:00:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200052-pcb-render.json](evidence/2026-11-09-200052-pcb-render.json) | 2026-11-09T20:00:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200050-pcb-export.json](evidence/2026-11-09-200050-pcb-export.json) | 2026-11-09T20:00:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200034-pcb-check.json](evidence/2026-11-09-200034-pcb-check.json) | 2026-11-09T20:00:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200031-pcb-get.json](evidence/2026-11-09-200031-pcb-get.json) | 2026-11-09T20:00:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200029-router-run.json](evidence/2026-11-09-200029-router-run.json) | 2026-11-09T20:00:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200026-plane-delete.json](evidence/2026-11-09-200026-plane-delete.json) | 2026-11-09T20:00:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200009-plane-create.json](evidence/2026-11-09-200009-plane-create.json) | 2026-11-09T20:00:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200007-placement-run.json](evidence/2026-11-09-200007-placement-run.json) | 2026-11-09T20:00:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200005-pcb-update.json](evidence/2026-11-09-200005-pcb-update.json) | 2026-11-09T20:00:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-200003-forward-annotation-run.json](evidence/2026-11-09-200003-forward-annotation-run.json) | 2026-11-09T20:00:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195953-doc-list.json](evidence/2026-11-09-195953-doc-list.json) | 2026-11-09T19:59:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195952-component-move.json](evidence/2026-11-09-195952-component-move.json) | 2026-11-09T19:59:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195950-via-update.json](evidence/2026-11-09-195950-via-update.json) | 2026-11-09T19:59:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195949-via-delete.json](evidence/2026-11-09-195949-via-delete.json) | 2026-11-09T19:59:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195947-via-create.json](evidence/2026-11-09-195947-via-create.json) | 2026-11-09T19:59:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195946-trace-delete.json](evidence/2026-11-09-195946-trace-delete.json) | 2026-11-09T19:59:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195944-trace-create.json](evidence/2026-11-09-195944-trace-create.json) | 2026-11-09T19:59:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195943-solder-mask-delete.json](evidence/2026-11-09-195943-solder-mask-delete.json) | 2026-11-09T19:59:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195942-solder-mask-create.json](evidence/2026-11-09-195942-solder-mask-create.json) | 2026-11-09T19:59:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195940-router-run.json](evidence/2026-11-09-195940-router-run.json) | 2026-11-09T19:59:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195939-refdes-move.json](evidence/2026-11-09-195939-refdes-move.json) | 2026-11-09T19:59:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195937-plane-delete.json](evidence/2026-11-09-195937-plane-delete.json) | 2026-11-09T19:59:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195936-plane-create.json](evidence/2026-11-09-195936-plane-create.json) | 2026-11-09T19:59:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195934-placement-run.json](evidence/2026-11-09-195934-placement-run.json) | 2026-11-09T19:59:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195933-pcb-update.json](evidence/2026-11-09-195933-pcb-update.json) | 2026-11-09T19:59:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195931-pcb-render.json](evidence/2026-11-09-195931-pcb-render.json) | 2026-11-09T19:59:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195930-pcb-get.json](evidence/2026-11-09-195930-pcb-get.json) | 2026-11-09T19:59:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195929-pcb-export.json](evidence/2026-11-09-195929-pcb-export.json) | 2026-11-09T19:59:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195927-pcb-check.json](evidence/2026-11-09-195927-pcb-check.json) | 2026-11-09T19:59:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195926-mounting-hole-delete.json](evidence/2026-11-09-195926-mounting-hole-delete.json) | 2026-11-09T19:59:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195924-mounting-hole-create.json](evidence/2026-11-09-195924-mounting-hole-create.json) | 2026-11-09T19:59:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195923-mounting-hole-list.json](evidence/2026-11-09-195923-mounting-hole-list.json) | 2026-11-09T19:59:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195922-forward-annotation-run.json](evidence/2026-11-09-195922-forward-annotation-run.json) | 2026-11-09T19:59:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195920-doc-save.json](evidence/2026-11-09-195920-doc-save.json) | 2026-11-09T19:59:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195919-doc-open.json](evidence/2026-11-09-195919-doc-open.json) | 2026-11-09T19:59:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195917-doc-close.json](evidence/2026-11-09-195917-doc-close.json) | 2026-11-09T19:59:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195916-constraint-import.json](evidence/2026-11-09-195916-constraint-import.json) | 2026-11-09T19:59:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195914-constraint-get.json](evidence/2026-11-09-195914-constraint-get.json) | 2026-11-09T19:59:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195913-session-close.json](evidence/2026-11-09-195913-session-close.json) | 2026-11-09T19:59:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195911-session-get.json](evidence/2026-11-09-195911-session-get.json) | 2026-11-09T19:59:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195909-schematic-get.json](evidence/2026-11-09-195909-schematic-get.json) | 2026-11-09T19:59:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195907-schematic-export.json](evidence/2026-11-09-195907-schematic-export.json) | 2026-11-09T19:59:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195906-schematic-create.json](evidence/2026-11-09-195906-schematic-create.json) | 2026-11-09T19:59:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195904-schematic-check.json](evidence/2026-11-09-195904-schematic-check.json) | 2026-11-09T19:59:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195903-net-list.json](evidence/2026-11-09-195903-net-list.json) | 2026-11-09T19:59:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195902-net-get.json](evidence/2026-11-09-195902-net-get.json) | 2026-11-09T19:59:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195900-component-list.json](evidence/2026-11-09-195900-component-list.json) | 2026-11-09T19:59:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195859-component-get.json](evidence/2026-11-09-195859-component-get.json) | 2026-11-09T19:58:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195857-bom-export.json](evidence/2026-11-09-195857-bom-export.json) | 2026-11-09T19:58:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-195856-bom-check.json](evidence/2026-11-09-195856-bom-check.json) | 2026-11-09T19:58:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
<!-- driver:evidence:end -->
