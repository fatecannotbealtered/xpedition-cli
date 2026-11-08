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

真实软件记录只在它记录的工具版本与 xpedition-cli 的主版本和次版本都相同时才算作证据。记录里从不出现机密，带机密的参数显示为 `***`。

## 证据记录

`docs/evidence/` 里的真实软件记录，由 `python scripts/spec.py generate` 生成：

<!-- driver:evidence:start -->
| 记录 | 运行时间 | 软件 | 平台 | 结果 |
|---|---|---|---|---|
| [2026-11-08-100002-pcb-render.json](evidence/2026-11-08-100002-pcb-render.json) | 2026-11-08T10:00:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-095948-pcb-render.json](evidence/2026-11-08-095948-pcb-render.json) | 2026-11-08T09:59:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-095935-pcb-render.json](evidence/2026-11-08-095935-pcb-render.json) | 2026-11-08T09:59:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-201608-pcb-check.json](evidence/2026-11-07-201608-pcb-check.json) | 2026-11-07T20:16:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-201558-pcb-get.json](evidence/2026-11-07-201558-pcb-get.json) | 2026-11-07T20:15:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-201318-plane-delete.json](evidence/2026-11-07-201318-plane-delete.json) | 2026-11-07T20:13:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-201310-plane-delete.json](evidence/2026-11-07-201310-plane-delete.json) | 2026-11-07T20:13:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-201307-plane-delete.json](evidence/2026-11-07-201307-plane-delete.json) | 2026-11-07T20:13:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-201304-plane-delete.json](evidence/2026-11-07-201304-plane-delete.json) | 2026-11-07T20:13:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-201243-via-delete.json](evidence/2026-11-07-201243-via-delete.json) | 2026-11-07T20:12:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-201234-via-delete.json](evidence/2026-11-07-201234-via-delete.json) | 2026-11-07T20:12:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-201230-via-delete.json](evidence/2026-11-07-201230-via-delete.json) | 2026-11-07T20:12:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-201226-via-delete.json](evidence/2026-11-07-201226-via-delete.json) | 2026-11-07T20:12:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200943-via-update.json](evidence/2026-11-07-200943-via-update.json) | 2026-11-07T20:09:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200941-via-delete.json](evidence/2026-11-07-200941-via-delete.json) | 2026-11-07T20:09:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200938-via-create.json](evidence/2026-11-07-200938-via-create.json) | 2026-11-07T20:09:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200932-trace-delete.json](evidence/2026-11-07-200932-trace-delete.json) | 2026-11-07T20:09:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200929-trace-create.json](evidence/2026-11-07-200929-trace-create.json) | 2026-11-07T20:09:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200922-solder-mask-delete.json](evidence/2026-11-07-200922-solder-mask-delete.json) | 2026-11-07T20:09:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200920-solder-mask-create.json](evidence/2026-11-07-200920-solder-mask-create.json) | 2026-11-07T20:09:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200914-constraint-import.json](evidence/2026-11-07-200914-constraint-import.json) | 2026-11-07T20:09:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200820-via-update.json](evidence/2026-11-07-200820-via-update.json) | 2026-11-07T20:08:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200817-via-delete.json](evidence/2026-11-07-200817-via-delete.json) | 2026-11-07T20:08:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200815-via-create.json](evidence/2026-11-07-200815-via-create.json) | 2026-11-07T20:08:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200809-trace-delete.json](evidence/2026-11-07-200809-trace-delete.json) | 2026-11-07T20:08:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200806-solder-mask-delete.json](evidence/2026-11-07-200806-solder-mask-delete.json) | 2026-11-07T20:08:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200803-plane-delete.json](evidence/2026-11-07-200803-plane-delete.json) | 2026-11-07T20:08:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200800-plane-create.json](evidence/2026-11-07-200800-plane-create.json) | 2026-11-07T20:08:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200745-trace-create.json](evidence/2026-11-07-200745-trace-create.json) | 2026-11-07T20:07:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200723-trace-create.json](evidence/2026-11-07-200723-trace-create.json) | 2026-11-07T20:07:23Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-07-200715-trace-create.json](evidence/2026-11-07-200715-trace-create.json) | 2026-11-07T20:07:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200658-trace-create.json](evidence/2026-11-07-200658-trace-create.json) | 2026-11-07T20:06:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200534-pcb-render.json](evidence/2026-11-07-200534-pcb-render.json) | 2026-11-07T20:05:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200512-pcb-render.json](evidence/2026-11-07-200512-pcb-render.json) | 2026-11-07T20:05:12Z | Xpedition 2604 | windows | 5 次运行中 5 次通过 |
| [2026-11-07-200359-solder-mask-delete.json](evidence/2026-11-07-200359-solder-mask-delete.json) | 2026-11-07T20:03:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200343-solder-mask-delete.json](evidence/2026-11-07-200343-solder-mask-delete.json) | 2026-11-07T20:03:43Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-200339-solder-mask-delete.json](evidence/2026-11-07-200339-solder-mask-delete.json) | 2026-11-07T20:03:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200336-solder-mask-delete.json](evidence/2026-11-07-200336-solder-mask-delete.json) | 2026-11-07T20:03:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200320-solder-mask-create.json](evidence/2026-11-07-200320-solder-mask-create.json) | 2026-11-07T20:03:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200303-solder-mask-create.json](evidence/2026-11-07-200303-solder-mask-create.json) | 2026-11-07T20:03:03Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-07-200240-solder-mask-create.json](evidence/2026-11-07-200240-solder-mask-create.json) | 2026-11-07T20:02:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200146-via-create.json](evidence/2026-11-07-200146-via-create.json) | 2026-11-07T20:01:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200128-via-create.json](evidence/2026-11-07-200128-via-create.json) | 2026-11-07T20:01:28Z | Xpedition 2604 | windows | 3 次运行中 2 次通过 |
| [2026-11-07-200105-via-create.json](evidence/2026-11-07-200105-via-create.json) | 2026-11-07T20:01:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200042-via-create.json](evidence/2026-11-07-200042-via-create.json) | 2026-11-07T20:00:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-200025-via-create.json](evidence/2026-11-07-200025-via-create.json) | 2026-11-07T20:00:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195940-trace-delete.json](evidence/2026-11-07-195940-trace-delete.json) | 2026-11-07T19:59:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195921-via-delete.json](evidence/2026-11-07-195921-via-delete.json) | 2026-11-07T19:59:21Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-195905-trace-delete.json](evidence/2026-11-07-195905-trace-delete.json) | 2026-11-07T19:59:05Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-195859-trace-delete.json](evidence/2026-11-07-195859-trace-delete.json) | 2026-11-07T19:58:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195855-trace-delete.json](evidence/2026-11-07-195855-trace-delete.json) | 2026-11-07T19:58:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195830-via-update.json](evidence/2026-11-07-195830-via-update.json) | 2026-11-07T19:58:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195823-via-update.json](evidence/2026-11-07-195823-via-update.json) | 2026-11-07T19:58:23Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-195817-via-update.json](evidence/2026-11-07-195817-via-update.json) | 2026-11-07T19:58:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195812-via-update.json](evidence/2026-11-07-195812-via-update.json) | 2026-11-07T19:58:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195720-constraint-import.json](evidence/2026-11-07-195720-constraint-import.json) | 2026-11-07T19:57:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195649-constraint-import.json](evidence/2026-11-07-195649-constraint-import.json) | 2026-11-07T19:56:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195634-constraint-get.json](evidence/2026-11-07-195634-constraint-get.json) | 2026-11-07T19:56:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195618-trace-create.json](evidence/2026-11-07-195618-trace-create.json) | 2026-11-07T19:56:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195615-constraint-import.json](evidence/2026-11-07-195615-constraint-import.json) | 2026-11-07T19:56:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195549-constraint-import.json](evidence/2026-11-07-195549-constraint-import.json) | 2026-11-07T19:55:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195541-constraint-import.json](evidence/2026-11-07-195541-constraint-import.json) | 2026-11-07T19:55:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-195530-constraint-import.json](evidence/2026-11-07-195530-constraint-import.json) | 2026-11-07T19:55:30Z | Xpedition 2604 | windows | 2 次运行中 1 次通过 |
| [2026-11-07-195527-constraint-get.json](evidence/2026-11-07-195527-constraint-get.json) | 2026-11-07T19:55:27Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-07-194908-plane-delete.json](evidence/2026-11-07-194908-plane-delete.json) | 2026-11-07T19:49:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194906-plane-create.json](evidence/2026-11-07-194906-plane-create.json) | 2026-11-07T19:49:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194903-pcb-update.json](evidence/2026-11-07-194903-pcb-update.json) | 2026-11-07T19:49:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194606-pcb-export.json](evidence/2026-11-07-194606-pcb-export.json) | 2026-11-07T19:46:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194528-pcb-export.json](evidence/2026-11-07-194528-pcb-export.json) | 2026-11-07T19:45:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194458-pcb-export.json](evidence/2026-11-07-194458-pcb-export.json) | 2026-11-07T19:44:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194419-pcb-export.json](evidence/2026-11-07-194419-pcb-export.json) | 2026-11-07T19:44:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194335-constraint-get.json](evidence/2026-11-07-194335-constraint-get.json) | 2026-11-07T19:43:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194335-constraint-import.json](evidence/2026-11-07-194335-constraint-import.json) | 2026-11-07T19:43:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194335-doc-save.json](evidence/2026-11-07-194335-doc-save.json) | 2026-11-07T19:43:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194325-pcb-export.json](evidence/2026-11-07-194325-pcb-export.json) | 2026-11-07T19:43:25Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-07-194310-pcb-get.json](evidence/2026-11-07-194310-pcb-get.json) | 2026-11-07T19:43:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194307-pcb-check.json](evidence/2026-11-07-194307-pcb-check.json) | 2026-11-07T19:43:07Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-07-194253-router-run.json](evidence/2026-11-07-194253-router-run.json) | 2026-11-07T19:42:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194244-router-run.json](evidence/2026-11-07-194244-router-run.json) | 2026-11-07T19:42:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194225-router-run.json](evidence/2026-11-07-194225-router-run.json) | 2026-11-07T19:42:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194149-router-run.json](evidence/2026-11-07-194149-router-run.json) | 2026-11-07T19:41:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194101-router-run.json](evidence/2026-11-07-194101-router-run.json) | 2026-11-07T19:41:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-194056-router-run.json](evidence/2026-11-07-194056-router-run.json) | 2026-11-07T19:40:56Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-07-193619-plane-delete.json](evidence/2026-11-07-193619-plane-delete.json) | 2026-11-07T19:36:19Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-193532-plane-create.json](evidence/2026-11-07-193532-plane-create.json) | 2026-11-07T19:35:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-193525-plane-create.json](evidence/2026-11-07-193525-plane-create.json) | 2026-11-07T19:35:25Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-07-193519-plane-create.json](evidence/2026-11-07-193519-plane-create.json) | 2026-11-07T19:35:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-193515-pcb-check.json](evidence/2026-11-07-193515-pcb-check.json) | 2026-11-07T19:35:15Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-07-193512-pcb-get.json](evidence/2026-11-07-193512-pcb-get.json) | 2026-11-07T19:35:12Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-193414-doc-close.json](evidence/2026-11-07-193414-doc-close.json) | 2026-11-07T19:34:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-193251-doc-close.json](evidence/2026-11-07-193251-doc-close.json) | 2026-11-07T19:32:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-193212-doc-save.json](evidence/2026-11-07-193212-doc-save.json) | 2026-11-07T19:32:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-193058-doc-save.json](evidence/2026-11-07-193058-doc-save.json) | 2026-11-07T19:30:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-193008-doc-save.json](evidence/2026-11-07-193008-doc-save.json) | 2026-11-07T19:30:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192918-doc-save.json](evidence/2026-11-07-192918-doc-save.json) | 2026-11-07T19:29:18Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-192758-placement-run.json](evidence/2026-11-07-192758-placement-run.json) | 2026-11-07T19:27:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192728-placement-run.json](evidence/2026-11-07-192728-placement-run.json) | 2026-11-07T19:27:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192719-placement-run.json](evidence/2026-11-07-192719-placement-run.json) | 2026-11-07T19:27:19Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-192714-placement-run.json](evidence/2026-11-07-192714-placement-run.json) | 2026-11-07T19:27:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192709-forward-annotation-run.json](evidence/2026-11-07-192709-forward-annotation-run.json) | 2026-11-07T19:27:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192640-doc-open.json](evidence/2026-11-07-192640-doc-open.json) | 2026-11-07T19:26:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192620-doc-open.json](evidence/2026-11-07-192620-doc-open.json) | 2026-11-07T19:26:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192616-doc-open.json](evidence/2026-11-07-192616-doc-open.json) | 2026-11-07T19:26:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192553-doc-open.json](evidence/2026-11-07-192553-doc-open.json) | 2026-11-07T19:25:53Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-07-192547-doc-open.json](evidence/2026-11-07-192547-doc-open.json) | 2026-11-07T19:25:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192449-doc-close.json](evidence/2026-11-07-192449-doc-close.json) | 2026-11-07T19:24:49Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-192445-placement-run.json](evidence/2026-11-07-192445-placement-run.json) | 2026-11-07T19:24:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192431-pcb-update.json](evidence/2026-11-07-192431-pcb-update.json) | 2026-11-07T19:24:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192427-pcb-update.json](evidence/2026-11-07-192427-pcb-update.json) | 2026-11-07T19:24:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192421-pcb-update.json](evidence/2026-11-07-192421-pcb-update.json) | 2026-11-07T19:24:21Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-192416-pcb-update.json](evidence/2026-11-07-192416-pcb-update.json) | 2026-11-07T19:24:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192232-doc-open.json](evidence/2026-11-07-192232-doc-open.json) | 2026-11-07T19:22:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192230-doc-close.json](evidence/2026-11-07-192230-doc-close.json) | 2026-11-07T19:22:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192227-doc-save.json](evidence/2026-11-07-192227-doc-save.json) | 2026-11-07T19:22:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-192225-forward-annotation-run.json](evidence/2026-11-07-192225-forward-annotation-run.json) | 2026-11-07T19:22:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191959-forward-annotation-run.json](evidence/2026-11-07-191959-forward-annotation-run.json) | 2026-11-07T19:19:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191722-forward-annotation-run.json](evidence/2026-11-07-191722-forward-annotation-run.json) | 2026-11-07T19:17:22Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-191558-forward-annotation-run.json](evidence/2026-11-07-191558-forward-annotation-run.json) | 2026-11-07T19:15:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191555-via-delete.json](evidence/2026-11-07-191555-via-delete.json) | 2026-11-07T19:15:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191555-via-update.json](evidence/2026-11-07-191555-via-update.json) | 2026-11-07T19:15:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191554-trace-delete.json](evidence/2026-11-07-191554-trace-delete.json) | 2026-11-07T19:15:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191554-via-create.json](evidence/2026-11-07-191554-via-create.json) | 2026-11-07T19:15:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191553-trace-create.json](evidence/2026-11-07-191553-trace-create.json) | 2026-11-07T19:15:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191552-solder-mask-create.json](evidence/2026-11-07-191552-solder-mask-create.json) | 2026-11-07T19:15:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191552-solder-mask-delete.json](evidence/2026-11-07-191552-solder-mask-delete.json) | 2026-11-07T19:15:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191551-pcb-export.json](evidence/2026-11-07-191551-pcb-export.json) | 2026-11-07T19:15:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191547-router-run.json](evidence/2026-11-07-191547-router-run.json) | 2026-11-07T19:15:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191546-plane-delete.json](evidence/2026-11-07-191546-plane-delete.json) | 2026-11-07T19:15:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191545-placement-run.json](evidence/2026-11-07-191545-placement-run.json) | 2026-11-07T19:15:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191545-plane-create.json](evidence/2026-11-07-191545-plane-create.json) | 2026-11-07T19:15:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191544-pcb-render.json](evidence/2026-11-07-191544-pcb-render.json) | 2026-11-07T19:15:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191544-pcb-update.json](evidence/2026-11-07-191544-pcb-update.json) | 2026-11-07T19:15:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191543-pcb-get.json](evidence/2026-11-07-191543-pcb-get.json) | 2026-11-07T19:15:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191535-pcb-check.json](evidence/2026-11-07-191535-pcb-check.json) | 2026-11-07T19:15:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191534-doc-open.json](evidence/2026-11-07-191534-doc-open.json) | 2026-11-07T19:15:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191533-doc-close.json](evidence/2026-11-07-191533-doc-close.json) | 2026-11-07T19:15:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-191155-forward-annotation-run.json](evidence/2026-11-07-191155-forward-annotation-run.json) | 2026-11-07T19:11:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184906-trace-create.json](evidence/2026-11-07-184906-trace-create.json) | 2026-11-07T18:49:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184901-solder-mask-create.json](evidence/2026-11-07-184901-solder-mask-create.json) | 2026-11-07T18:49:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184859-router-run.json](evidence/2026-11-07-184859-router-run.json) | 2026-11-07T18:48:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184852-placement-run.json](evidence/2026-11-07-184852-placement-run.json) | 2026-11-07T18:48:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184849-pcb-update.json](evidence/2026-11-07-184849-pcb-update.json) | 2026-11-07T18:48:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184847-pcb-render.json](evidence/2026-11-07-184847-pcb-render.json) | 2026-11-07T18:48:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184845-pcb-export.json](evidence/2026-11-07-184845-pcb-export.json) | 2026-11-07T18:48:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184842-forward-annotation-run.json](evidence/2026-11-07-184842-forward-annotation-run.json) | 2026-11-07T18:48:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184840-doc-save.json](evidence/2026-11-07-184840-doc-save.json) | 2026-11-07T18:48:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184837-doc-open.json](evidence/2026-11-07-184837-doc-open.json) | 2026-11-07T18:48:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184835-doc-close.json](evidence/2026-11-07-184835-doc-close.json) | 2026-11-07T18:48:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184832-constraint-import.json](evidence/2026-11-07-184832-constraint-import.json) | 2026-11-07T18:48:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184816-via-update.json](evidence/2026-11-07-184816-via-update.json) | 2026-11-07T18:48:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184814-via-delete.json](evidence/2026-11-07-184814-via-delete.json) | 2026-11-07T18:48:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184813-via-create.json](evidence/2026-11-07-184813-via-create.json) | 2026-11-07T18:48:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184811-trace-delete.json](evidence/2026-11-07-184811-trace-delete.json) | 2026-11-07T18:48:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184809-trace-create.json](evidence/2026-11-07-184809-trace-create.json) | 2026-11-07T18:48:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184807-solder-mask-delete.json](evidence/2026-11-07-184807-solder-mask-delete.json) | 2026-11-07T18:48:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184805-solder-mask-create.json](evidence/2026-11-07-184805-solder-mask-create.json) | 2026-11-07T18:48:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184803-router-run.json](evidence/2026-11-07-184803-router-run.json) | 2026-11-07T18:48:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184801-plane-delete.json](evidence/2026-11-07-184801-plane-delete.json) | 2026-11-07T18:48:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184758-plane-create.json](evidence/2026-11-07-184758-plane-create.json) | 2026-11-07T18:47:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184756-placement-run.json](evidence/2026-11-07-184756-placement-run.json) | 2026-11-07T18:47:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184753-pcb-update.json](evidence/2026-11-07-184753-pcb-update.json) | 2026-11-07T18:47:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184751-pcb-render.json](evidence/2026-11-07-184751-pcb-render.json) | 2026-11-07T18:47:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184748-pcb-get.json](evidence/2026-11-07-184748-pcb-get.json) | 2026-11-07T18:47:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184746-pcb-export.json](evidence/2026-11-07-184746-pcb-export.json) | 2026-11-07T18:47:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184743-pcb-check.json](evidence/2026-11-07-184743-pcb-check.json) | 2026-11-07T18:47:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184740-forward-annotation-run.json](evidence/2026-11-07-184740-forward-annotation-run.json) | 2026-11-07T18:47:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184738-doc-save.json](evidence/2026-11-07-184738-doc-save.json) | 2026-11-07T18:47:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184736-doc-open.json](evidence/2026-11-07-184736-doc-open.json) | 2026-11-07T18:47:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184733-doc-close.json](evidence/2026-11-07-184733-doc-close.json) | 2026-11-07T18:47:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184731-constraint-import.json](evidence/2026-11-07-184731-constraint-import.json) | 2026-11-07T18:47:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184728-constraint-get.json](evidence/2026-11-07-184728-constraint-get.json) | 2026-11-07T18:47:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184557-via-update.json](evidence/2026-11-07-184557-via-update.json) | 2026-11-07T18:45:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184556-via-delete.json](evidence/2026-11-07-184556-via-delete.json) | 2026-11-07T18:45:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184554-via-create.json](evidence/2026-11-07-184554-via-create.json) | 2026-11-07T18:45:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184551-trace-delete.json](evidence/2026-11-07-184551-trace-delete.json) | 2026-11-07T18:45:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184549-trace-create.json](evidence/2026-11-07-184549-trace-create.json) | 2026-11-07T18:45:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184547-solder-mask-delete.json](evidence/2026-11-07-184547-solder-mask-delete.json) | 2026-11-07T18:45:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184545-solder-mask-create.json](evidence/2026-11-07-184545-solder-mask-create.json) | 2026-11-07T18:45:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184543-session-get.json](evidence/2026-11-07-184543-session-get.json) | 2026-11-07T18:45:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184540-session-close.json](evidence/2026-11-07-184540-session-close.json) | 2026-11-07T18:45:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184537-schematic-get.json](evidence/2026-11-07-184537-schematic-get.json) | 2026-11-07T18:45:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184535-schematic-export.json](evidence/2026-11-07-184535-schematic-export.json) | 2026-11-07T18:45:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184533-schematic-create.json](evidence/2026-11-07-184533-schematic-create.json) | 2026-11-07T18:45:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184531-schematic-check.json](evidence/2026-11-07-184531-schematic-check.json) | 2026-11-07T18:45:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184529-router-run.json](evidence/2026-11-07-184529-router-run.json) | 2026-11-07T18:45:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184527-plane-delete.json](evidence/2026-11-07-184527-plane-delete.json) | 2026-11-07T18:45:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184525-plane-create.json](evidence/2026-11-07-184525-plane-create.json) | 2026-11-07T18:45:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184523-placement-run.json](evidence/2026-11-07-184523-placement-run.json) | 2026-11-07T18:45:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184521-pcb-update.json](evidence/2026-11-07-184521-pcb-update.json) | 2026-11-07T18:45:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184519-pcb-render.json](evidence/2026-11-07-184519-pcb-render.json) | 2026-11-07T18:45:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184517-pcb-export.json](evidence/2026-11-07-184517-pcb-export.json) | 2026-11-07T18:45:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184515-pcb-check.json](evidence/2026-11-07-184515-pcb-check.json) | 2026-11-07T18:45:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184513-forward-annotation-run.json](evidence/2026-11-07-184513-forward-annotation-run.json) | 2026-11-07T18:45:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184511-doc-save.json](evidence/2026-11-07-184511-doc-save.json) | 2026-11-07T18:45:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184509-doc-open.json](evidence/2026-11-07-184509-doc-open.json) | 2026-11-07T18:45:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184507-doc-close.json](evidence/2026-11-07-184507-doc-close.json) | 2026-11-07T18:45:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184505-constraint-import.json](evidence/2026-11-07-184505-constraint-import.json) | 2026-11-07T18:45:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184503-constraint-get.json](evidence/2026-11-07-184503-constraint-get.json) | 2026-11-07T18:45:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184453-pcb-get.json](evidence/2026-11-07-184453-pcb-get.json) | 2026-11-07T18:44:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184300-session-close.json](evidence/2026-11-07-184300-session-close.json) | 2026-11-07T18:43:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-184259-session-open.json](evidence/2026-11-07-184259-session-open.json) | 2026-11-07T18:42:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-183338-pcb-create.json](evidence/2026-11-07-183338-pcb-create.json) | 2026-11-07T18:33:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-183337-pcb-create.json](evidence/2026-11-07-183337-pcb-create.json) | 2026-11-07T18:33:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-183249-pcb-create.json](evidence/2026-11-07-183249-pcb-create.json) | 2026-11-07T18:32:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-183241-pcb-create.json](evidence/2026-11-07-183241-pcb-create.json) | 2026-11-07T18:32:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-183239-pcb-create.json](evidence/2026-11-07-183239-pcb-create.json) | 2026-11-07T18:32:39Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-07-183229-packager-run.json](evidence/2026-11-07-183229-packager-run.json) | 2026-11-07T18:32:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-183219-packager-run.json](evidence/2026-11-07-183219-packager-run.json) | 2026-11-07T18:32:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-183216-packager-run.json](evidence/2026-11-07-183216-packager-run.json) | 2026-11-07T18:32:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-183205-packager-run.json](evidence/2026-11-07-183205-packager-run.json) | 2026-11-07T18:32:05Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-07-183200-pcb-create.json](evidence/2026-11-07-183200-pcb-create.json) | 2026-11-07T18:32:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-183159-packager-run.json](evidence/2026-11-07-183159-packager-run.json) | 2026-11-07T18:31:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-183128-schematic-export.json](evidence/2026-11-07-183128-schematic-export.json) | 2026-11-07T18:31:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182844-schematic-create.json](evidence/2026-11-07-182844-schematic-create.json) | 2026-11-07T18:28:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182632-session-close.json](evidence/2026-11-07-182632-session-close.json) | 2026-11-07T18:26:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182630-session-list.json](evidence/2026-11-07-182630-session-list.json) | 2026-11-07T18:26:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182628-session-get.json](evidence/2026-11-07-182628-session-get.json) | 2026-11-07T18:26:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182626-doc-list.json](evidence/2026-11-07-182626-doc-list.json) | 2026-11-07T18:26:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182624-doc-get.json](evidence/2026-11-07-182624-doc-get.json) | 2026-11-07T18:26:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182622-doc-create.json](evidence/2026-11-07-182622-doc-create.json) | 2026-11-07T18:26:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182620-schematic-export.json](evidence/2026-11-07-182620-schematic-export.json) | 2026-11-07T18:26:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182618-schematic-check.json](evidence/2026-11-07-182618-schematic-check.json) | 2026-11-07T18:26:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182616-schematic-get.json](evidence/2026-11-07-182616-schematic-get.json) | 2026-11-07T18:26:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182615-schematic-create.json](evidence/2026-11-07-182615-schematic-create.json) | 2026-11-07T18:26:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182340-schematic-export.json](evidence/2026-11-07-182340-schematic-export.json) | 2026-11-07T18:23:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182333-schematic-create.json](evidence/2026-11-07-182333-schematic-create.json) | 2026-11-07T18:23:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182331-schematic-export.json](evidence/2026-11-07-182331-schematic-export.json) | 2026-11-07T18:23:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182329-schematic-export.json](evidence/2026-11-07-182329-schematic-export.json) | 2026-11-07T18:23:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182326-schematic-check.json](evidence/2026-11-07-182326-schematic-check.json) | 2026-11-07T18:23:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182321-schematic-get.json](evidence/2026-11-07-182321-schematic-get.json) | 2026-11-07T18:23:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182212-schematic-create.json](evidence/2026-11-07-182212-schematic-create.json) | 2026-11-07T18:22:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182207-doc-create.json](evidence/2026-11-07-182207-doc-create.json) | 2026-11-07T18:22:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182205-session-close.json](evidence/2026-11-07-182205-session-close.json) | 2026-11-07T18:22:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182147-session-open.json](evidence/2026-11-07-182147-session-open.json) | 2026-11-07T18:21:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182106-session-open.json](evidence/2026-11-07-182106-session-open.json) | 2026-11-07T18:21:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182018-session-close.json](evidence/2026-11-07-182018-session-close.json) | 2026-11-07T18:20:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-182007-session-open.json](evidence/2026-11-07-182007-session-open.json) | 2026-11-07T18:20:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181906-session-open.json](evidence/2026-11-07-181906-session-open.json) | 2026-11-07T18:19:06Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-07-181824-session-list.json](evidence/2026-11-07-181824-session-list.json) | 2026-11-07T18:18:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181823-session-get.json](evidence/2026-11-07-181823-session-get.json) | 2026-11-07T18:18:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181821-doc-list.json](evidence/2026-11-07-181821-doc-list.json) | 2026-11-07T18:18:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181819-doc-get.json](evidence/2026-11-07-181819-doc-get.json) | 2026-11-07T18:18:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181817-schematic-export.json](evidence/2026-11-07-181817-schematic-export.json) | 2026-11-07T18:18:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181815-schematic-check.json](evidence/2026-11-07-181815-schematic-check.json) | 2026-11-07T18:18:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181813-schematic-get.json](evidence/2026-11-07-181813-schematic-get.json) | 2026-11-07T18:18:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181812-schematic-create.json](evidence/2026-11-07-181812-schematic-create.json) | 2026-11-07T18:18:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181755-schematic-create.json](evidence/2026-11-07-181755-schematic-create.json) | 2026-11-07T18:17:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181743-session-close.json](evidence/2026-11-07-181743-session-close.json) | 2026-11-07T18:17:43Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-181723-doc-list.json](evidence/2026-11-07-181723-doc-list.json) | 2026-11-07T18:17:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181722-doc-get.json](evidence/2026-11-07-181722-doc-get.json) | 2026-11-07T18:17:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181713-padstack-list.json](evidence/2026-11-07-181713-padstack-list.json) | 2026-11-07T18:17:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181711-part-get.json](evidence/2026-11-07-181711-part-get.json) | 2026-11-07T18:17:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181709-part-list.json](evidence/2026-11-07-181709-part-list.json) | 2026-11-07T18:17:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181708-project-get.json](evidence/2026-11-07-181708-project-get.json) | 2026-11-07T18:17:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181705-schematic-export.json](evidence/2026-11-07-181705-schematic-export.json) | 2026-11-07T18:17:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181701-schematic-check.json](evidence/2026-11-07-181701-schematic-check.json) | 2026-11-07T18:17:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181657-schematic-get.json](evidence/2026-11-07-181657-schematic-get.json) | 2026-11-07T18:16:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181646-schematic-create.json](evidence/2026-11-07-181646-schematic-create.json) | 2026-11-07T18:16:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181623-schematic-export.json](evidence/2026-11-07-181623-schematic-export.json) | 2026-11-07T18:16:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181621-schematic-create.json](evidence/2026-11-07-181621-schematic-create.json) | 2026-11-07T18:16:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181620-schematic-check.json](evidence/2026-11-07-181620-schematic-check.json) | 2026-11-07T18:16:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181616-schematic-get.json](evidence/2026-11-07-181616-schematic-get.json) | 2026-11-07T18:16:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181554-schematic-export.json](evidence/2026-11-07-181554-schematic-export.json) | 2026-11-07T18:15:54Z | Xpedition 2604 | windows | 2 次运行中 1 次通过 |
| [2026-11-07-181552-schematic-create.json](evidence/2026-11-07-181552-schematic-create.json) | 2026-11-07T18:15:52Z | Xpedition 2604 | windows | 2 次运行中 1 次通过 |
| [2026-11-07-181550-schematic-check.json](evidence/2026-11-07-181550-schematic-check.json) | 2026-11-07T18:15:50Z | Xpedition 2604 | windows | 3 次运行中 2 次通过 |
| [2026-11-07-181548-schematic-get.json](evidence/2026-11-07-181548-schematic-get.json) | 2026-11-07T18:15:48Z | Xpedition 2604 | windows | 3 次运行中 2 次通过 |
| [2026-11-07-181509-padstack-create.json](evidence/2026-11-07-181509-padstack-create.json) | 2026-11-07T18:15:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181502-padstack-create.json](evidence/2026-11-07-181502-padstack-create.json) | 2026-11-07T18:15:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181500-padstack-list.json](evidence/2026-11-07-181500-padstack-list.json) | 2026-11-07T18:15:00Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-07-181458-padstack-create.json](evidence/2026-11-07-181458-padstack-create.json) | 2026-11-07T18:14:58Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-181455-padstack-create.json](evidence/2026-11-07-181455-padstack-create.json) | 2026-11-07T18:14:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181443-part-create.json](evidence/2026-11-07-181443-part-create.json) | 2026-11-07T18:14:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181437-part-create.json](evidence/2026-11-07-181437-part-create.json) | 2026-11-07T18:14:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181435-part-get.json](evidence/2026-11-07-181435-part-get.json) | 2026-11-07T18:14:35Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-181432-part-create.json](evidence/2026-11-07-181432-part-create.json) | 2026-11-07T18:14:32Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-181427-part-create.json](evidence/2026-11-07-181427-part-create.json) | 2026-11-07T18:14:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181415-part-list.json](evidence/2026-11-07-181415-part-list.json) | 2026-11-07T18:14:15Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-07-181410-project-get.json](evidence/2026-11-07-181410-project-get.json) | 2026-11-07T18:14:10Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-181331-padstack-create.json](evidence/2026-11-07-181331-padstack-create.json) | 2026-11-07T18:13:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181330-part-create.json](evidence/2026-11-07-181330-part-create.json) | 2026-11-07T18:13:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181250-session-list.json](evidence/2026-11-07-181250-session-list.json) | 2026-11-07T18:12:50Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-181248-session-get.json](evidence/2026-11-07-181248-session-get.json) | 2026-11-07T18:12:48Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-181246-doc-list.json](evidence/2026-11-07-181246-doc-list.json) | 2026-11-07T18:12:46Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-07-181243-doc-get.json](evidence/2026-11-07-181243-doc-get.json) | 2026-11-07T18:12:43Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-181226-doc-create.json](evidence/2026-11-07-181226-doc-create.json) | 2026-11-07T18:12:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181224-doc-create.json](evidence/2026-11-07-181224-doc-create.json) | 2026-11-07T18:12:24Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-07-181214-doc-create.json](evidence/2026-11-07-181214-doc-create.json) | 2026-11-07T18:12:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181212-doc-create.json](evidence/2026-11-07-181212-doc-create.json) | 2026-11-07T18:12:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-07-181033-doc-create.json](evidence/2026-11-07-181033-doc-create.json) | 2026-11-07T18:10:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
<!-- driver:evidence:end -->
