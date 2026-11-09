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
| [2026-11-09-125327-trace-create.json](evidence/2026-11-09-125327-trace-create.json) | 2026-11-09T12:53:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-125311-trace-create.json](evidence/2026-11-09-125311-trace-create.json) | 2026-11-09T12:53:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-125300-trace-create.json](evidence/2026-11-09-125300-trace-create.json) | 2026-11-09T12:53:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-125256-trace-create.json](evidence/2026-11-09-125256-trace-create.json) | 2026-11-09T12:52:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-125247-trace-create.json](evidence/2026-11-09-125247-trace-create.json) | 2026-11-09T12:52:47Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-093441-constraint-import.json](evidence/2026-11-09-093441-constraint-import.json) | 2026-11-09T09:34:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-040933-refdes-move.json](evidence/2026-11-09-040933-refdes-move.json) | 2026-11-09T04:09:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-040929-refdes-move.json](evidence/2026-11-09-040929-refdes-move.json) | 2026-11-09T04:09:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-040924-refdes-move.json](evidence/2026-11-09-040924-refdes-move.json) | 2026-11-09T04:09:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-040921-refdes-move.json](evidence/2026-11-09-040921-refdes-move.json) | 2026-11-09T04:09:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-040919-refdes-move.json](evidence/2026-11-09-040919-refdes-move.json) | 2026-11-09T04:09:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-040916-refdes-move.json](evidence/2026-11-09-040916-refdes-move.json) | 2026-11-09T04:09:16Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-040849-mounting-hole-create.json](evidence/2026-11-09-040849-mounting-hole-create.json) | 2026-11-09T04:08:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-040843-mounting-hole-create.json](evidence/2026-11-09-040843-mounting-hole-create.json) | 2026-11-09T04:08:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-040841-mounting-hole-create.json](evidence/2026-11-09-040841-mounting-hole-create.json) | 2026-11-09T04:08:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-040839-mounting-hole-create.json](evidence/2026-11-09-040839-mounting-hole-create.json) | 2026-11-09T04:08:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-040837-mounting-hole-create.json](evidence/2026-11-09-040837-mounting-hole-create.json) | 2026-11-09T04:08:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-032629-mounting-hole-create.json](evidence/2026-11-09-032629-mounting-hole-create.json) | 2026-11-09T03:26:29Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-032548-component-move.json](evidence/2026-11-09-032548-component-move.json) | 2026-11-09T03:25:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-032540-component-move.json](evidence/2026-11-09-032540-component-move.json) | 2026-11-09T03:25:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-032536-component-move.json](evidence/2026-11-09-032536-component-move.json) | 2026-11-09T03:25:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-032532-component-move.json](evidence/2026-11-09-032532-component-move.json) | 2026-11-09T03:25:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-032528-component-move.json](evidence/2026-11-09-032528-component-move.json) | 2026-11-09T03:25:28Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-08-225140-component-get.json](evidence/2026-11-08-225140-component-get.json) | 2026-11-08T22:51:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-225139-component-list.json](evidence/2026-11-08-225139-component-list.json) | 2026-11-08T22:51:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-225101-component-get.json](evidence/2026-11-08-225101-component-get.json) | 2026-11-08T22:51:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-225059-component-list.json](evidence/2026-11-08-225059-component-list.json) | 2026-11-08T22:50:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224740-via-update.json](evidence/2026-11-08-224740-via-update.json) | 2026-11-08T22:47:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224657-via-update.json](evidence/2026-11-08-224657-via-update.json) | 2026-11-08T22:46:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224647-via-update.json](evidence/2026-11-08-224647-via-update.json) | 2026-11-08T22:46:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224641-via-update.json](evidence/2026-11-08-224641-via-update.json) | 2026-11-08T22:46:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224637-via-update.json](evidence/2026-11-08-224637-via-update.json) | 2026-11-08T22:46:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224633-via-update.json](evidence/2026-11-08-224633-via-update.json) | 2026-11-08T22:46:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224621-via-delete.json](evidence/2026-11-08-224621-via-delete.json) | 2026-11-08T22:46:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224608-via-delete.json](evidence/2026-11-08-224608-via-delete.json) | 2026-11-08T22:46:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224558-via-delete.json](evidence/2026-11-08-224558-via-delete.json) | 2026-11-08T22:45:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224555-via-delete.json](evidence/2026-11-08-224555-via-delete.json) | 2026-11-08T22:45:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224545-trace-delete.json](evidence/2026-11-08-224545-trace-delete.json) | 2026-11-08T22:45:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224532-trace-delete.json](evidence/2026-11-08-224532-trace-delete.json) | 2026-11-08T22:45:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224521-trace-delete.json](evidence/2026-11-08-224521-trace-delete.json) | 2026-11-08T22:45:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224518-trace-delete.json](evidence/2026-11-08-224518-trace-delete.json) | 2026-11-08T22:45:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224419-via-delete.json](evidence/2026-11-08-224419-via-delete.json) | 2026-11-08T22:44:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224417-trace-delete.json](evidence/2026-11-08-224417-trace-delete.json) | 2026-11-08T22:44:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224416-trace-create.json](evidence/2026-11-08-224416-trace-create.json) | 2026-11-08T22:44:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224413-constraint-import.json](evidence/2026-11-08-224413-constraint-import.json) | 2026-11-08T22:44:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224321-via-update.json](evidence/2026-11-08-224321-via-update.json) | 2026-11-08T22:43:21Z | Xpedition 2604 | windows | 3 次运行中 2 次通过 |
| [2026-11-08-224252-via-delete.json](evidence/2026-11-08-224252-via-delete.json) | 2026-11-08T22:42:52Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-224223-trace-delete.json](evidence/2026-11-08-224223-trace-delete.json) | 2026-11-08T22:42:23Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-224120-constraint-import.json](evidence/2026-11-08-224120-constraint-import.json) | 2026-11-08T22:41:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-224110-constraint-import.json](evidence/2026-11-08-224110-constraint-import.json) | 2026-11-08T22:41:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223835-constraint-import.json](evidence/2026-11-08-223835-constraint-import.json) | 2026-11-08T22:38:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223821-constraint-import.json](evidence/2026-11-08-223821-constraint-import.json) | 2026-11-08T22:38:21Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-223617-mounting-hole-delete.json](evidence/2026-11-08-223617-mounting-hole-delete.json) | 2026-11-08T22:36:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223611-mounting-hole-delete.json](evidence/2026-11-08-223611-mounting-hole-delete.json) | 2026-11-08T22:36:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223608-mounting-hole-delete.json](evidence/2026-11-08-223608-mounting-hole-delete.json) | 2026-11-08T22:36:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223606-mounting-hole-delete.json](evidence/2026-11-08-223606-mounting-hole-delete.json) | 2026-11-08T22:36:06Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-223603-mounting-hole-delete.json](evidence/2026-11-08-223603-mounting-hole-delete.json) | 2026-11-08T22:36:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223552-mounting-hole-list.json](evidence/2026-11-08-223552-mounting-hole-list.json) | 2026-11-08T22:35:52Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-08-223543-mounting-hole-delete.json](evidence/2026-11-08-223543-mounting-hole-delete.json) | 2026-11-08T22:35:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223540-mounting-hole-list.json](evidence/2026-11-08-223540-mounting-hole-list.json) | 2026-11-08T22:35:40Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-223400-session-close.json](evidence/2026-11-08-223400-session-close.json) | 2026-11-08T22:34:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223358-doc-list.json](evidence/2026-11-08-223358-doc-list.json) | 2026-11-08T22:33:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223357-component-move.json](evidence/2026-11-08-223357-component-move.json) | 2026-11-08T22:33:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223355-via-update.json](evidence/2026-11-08-223355-via-update.json) | 2026-11-08T22:33:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223353-via-delete.json](evidence/2026-11-08-223353-via-delete.json) | 2026-11-08T22:33:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223352-via-create.json](evidence/2026-11-08-223352-via-create.json) | 2026-11-08T22:33:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223350-trace-delete.json](evidence/2026-11-08-223350-trace-delete.json) | 2026-11-08T22:33:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223348-trace-create.json](evidence/2026-11-08-223348-trace-create.json) | 2026-11-08T22:33:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223346-solder-mask-delete.json](evidence/2026-11-08-223346-solder-mask-delete.json) | 2026-11-08T22:33:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223344-solder-mask-create.json](evidence/2026-11-08-223344-solder-mask-create.json) | 2026-11-08T22:33:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223342-router-run.json](evidence/2026-11-08-223342-router-run.json) | 2026-11-08T22:33:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223341-refdes-move.json](evidence/2026-11-08-223341-refdes-move.json) | 2026-11-08T22:33:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223339-plane-delete.json](evidence/2026-11-08-223339-plane-delete.json) | 2026-11-08T22:33:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223337-plane-create.json](evidence/2026-11-08-223337-plane-create.json) | 2026-11-08T22:33:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223336-placement-run.json](evidence/2026-11-08-223336-placement-run.json) | 2026-11-08T22:33:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223334-pcb-update.json](evidence/2026-11-08-223334-pcb-update.json) | 2026-11-08T22:33:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223332-pcb-render.json](evidence/2026-11-08-223332-pcb-render.json) | 2026-11-08T22:33:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223330-pcb-get.json](evidence/2026-11-08-223330-pcb-get.json) | 2026-11-08T22:33:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223329-pcb-export.json](evidence/2026-11-08-223329-pcb-export.json) | 2026-11-08T22:33:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223327-pcb-check.json](evidence/2026-11-08-223327-pcb-check.json) | 2026-11-08T22:33:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223325-mounting-hole-delete.json](evidence/2026-11-08-223325-mounting-hole-delete.json) | 2026-11-08T22:33:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223324-mounting-hole-create.json](evidence/2026-11-08-223324-mounting-hole-create.json) | 2026-11-08T22:33:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223322-mounting-hole-list.json](evidence/2026-11-08-223322-mounting-hole-list.json) | 2026-11-08T22:33:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223320-forward-annotation-run.json](evidence/2026-11-08-223320-forward-annotation-run.json) | 2026-11-08T22:33:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223319-doc-save.json](evidence/2026-11-08-223319-doc-save.json) | 2026-11-08T22:33:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223317-doc-open.json](evidence/2026-11-08-223317-doc-open.json) | 2026-11-08T22:33:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223315-doc-close.json](evidence/2026-11-08-223315-doc-close.json) | 2026-11-08T22:33:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223313-constraint-import.json](evidence/2026-11-08-223313-constraint-import.json) | 2026-11-08T22:33:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223311-constraint-get.json](evidence/2026-11-08-223311-constraint-get.json) | 2026-11-08T22:33:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223310-session-close.json](evidence/2026-11-08-223310-session-close.json) | 2026-11-08T22:33:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223308-session-get.json](evidence/2026-11-08-223308-session-get.json) | 2026-11-08T22:33:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223306-schematic-get.json](evidence/2026-11-08-223306-schematic-get.json) | 2026-11-08T22:33:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223304-schematic-export.json](evidence/2026-11-08-223304-schematic-export.json) | 2026-11-08T22:33:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223302-schematic-create.json](evidence/2026-11-08-223302-schematic-create.json) | 2026-11-08T22:33:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223301-schematic-check.json](evidence/2026-11-08-223301-schematic-check.json) | 2026-11-08T22:33:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223259-net-list.json](evidence/2026-11-08-223259-net-list.json) | 2026-11-08T22:32:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223257-net-get.json](evidence/2026-11-08-223257-net-get.json) | 2026-11-08T22:32:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223255-component-list.json](evidence/2026-11-08-223255-component-list.json) | 2026-11-08T22:32:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223254-component-get.json](evidence/2026-11-08-223254-component-get.json) | 2026-11-08T22:32:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223252-bom-export.json](evidence/2026-11-08-223252-bom-export.json) | 2026-11-08T22:32:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223250-bom-check.json](evidence/2026-11-08-223250-bom-check.json) | 2026-11-08T22:32:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223144-refdes-move.json](evidence/2026-11-08-223144-refdes-move.json) | 2026-11-08T22:31:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223141-mounting-hole-delete.json](evidence/2026-11-08-223141-mounting-hole-delete.json) | 2026-11-08T22:31:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223138-mounting-hole-create.json](evidence/2026-11-08-223138-mounting-hole-create.json) | 2026-11-08T22:31:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223120-mounting-hole-list.json](evidence/2026-11-08-223120-mounting-hole-list.json) | 2026-11-08T22:31:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223117-component-move.json](evidence/2026-11-08-223117-component-move.json) | 2026-11-08T22:31:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223115-doc-save.json](evidence/2026-11-08-223115-doc-save.json) | 2026-11-08T22:31:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223112-doc-open.json](evidence/2026-11-08-223112-doc-open.json) | 2026-11-08T22:31:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223056-doc-close.json](evidence/2026-11-08-223056-doc-close.json) | 2026-11-08T22:30:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223053-solder-mask-delete.json](evidence/2026-11-08-223053-solder-mask-delete.json) | 2026-11-08T22:30:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223050-solder-mask-create.json](evidence/2026-11-08-223050-solder-mask-create.json) | 2026-11-08T22:30:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223048-via-update.json](evidence/2026-11-08-223048-via-update.json) | 2026-11-08T22:30:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223030-via-delete.json](evidence/2026-11-08-223030-via-delete.json) | 2026-11-08T22:30:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223027-via-create.json](evidence/2026-11-08-223027-via-create.json) | 2026-11-08T22:30:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223025-trace-delete.json](evidence/2026-11-08-223025-trace-delete.json) | 2026-11-08T22:30:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223023-trace-create.json](evidence/2026-11-08-223023-trace-create.json) | 2026-11-08T22:30:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223006-constraint-import.json](evidence/2026-11-08-223006-constraint-import.json) | 2026-11-08T22:30:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223003-constraint-get.json](evidence/2026-11-08-223003-constraint-get.json) | 2026-11-08T22:30:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-223000-pcb-render.json](evidence/2026-11-08-223000-pcb-render.json) | 2026-11-08T22:30:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222957-pcb-export.json](evidence/2026-11-08-222957-pcb-export.json) | 2026-11-08T22:29:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222941-pcb-check.json](evidence/2026-11-08-222941-pcb-check.json) | 2026-11-08T22:29:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222938-pcb-get.json](evidence/2026-11-08-222938-pcb-get.json) | 2026-11-08T22:29:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222935-router-run.json](evidence/2026-11-08-222935-router-run.json) | 2026-11-08T22:29:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222933-plane-delete.json](evidence/2026-11-08-222933-plane-delete.json) | 2026-11-08T22:29:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222916-plane-create.json](evidence/2026-11-08-222916-plane-create.json) | 2026-11-08T22:29:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222913-placement-run.json](evidence/2026-11-08-222913-placement-run.json) | 2026-11-08T22:29:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222911-pcb-update.json](evidence/2026-11-08-222911-pcb-update.json) | 2026-11-08T22:29:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222908-forward-annotation-run.json](evidence/2026-11-08-222908-forward-annotation-run.json) | 2026-11-08T22:29:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222350-refdes-move.json](evidence/2026-11-08-222350-refdes-move.json) | 2026-11-08T22:23:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222349-mounting-hole-delete.json](evidence/2026-11-08-222349-mounting-hole-delete.json) | 2026-11-08T22:23:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222348-mounting-hole-create.json](evidence/2026-11-08-222348-mounting-hole-create.json) | 2026-11-08T22:23:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222346-mounting-hole-list.json](evidence/2026-11-08-222346-mounting-hole-list.json) | 2026-11-08T22:23:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222345-component-move.json](evidence/2026-11-08-222345-component-move.json) | 2026-11-08T22:23:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222344-doc-save.json](evidence/2026-11-08-222344-doc-save.json) | 2026-11-08T22:23:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222342-doc-open.json](evidence/2026-11-08-222342-doc-open.json) | 2026-11-08T22:23:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222341-doc-close.json](evidence/2026-11-08-222341-doc-close.json) | 2026-11-08T22:23:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222339-solder-mask-delete.json](evidence/2026-11-08-222339-solder-mask-delete.json) | 2026-11-08T22:23:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222338-solder-mask-create.json](evidence/2026-11-08-222338-solder-mask-create.json) | 2026-11-08T22:23:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222336-via-update.json](evidence/2026-11-08-222336-via-update.json) | 2026-11-08T22:23:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222335-via-delete.json](evidence/2026-11-08-222335-via-delete.json) | 2026-11-08T22:23:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222334-via-create.json](evidence/2026-11-08-222334-via-create.json) | 2026-11-08T22:23:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222332-trace-delete.json](evidence/2026-11-08-222332-trace-delete.json) | 2026-11-08T22:23:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222331-trace-create.json](evidence/2026-11-08-222331-trace-create.json) | 2026-11-08T22:23:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222329-constraint-import.json](evidence/2026-11-08-222329-constraint-import.json) | 2026-11-08T22:23:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222328-constraint-get.json](evidence/2026-11-08-222328-constraint-get.json) | 2026-11-08T22:23:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222326-pcb-render.json](evidence/2026-11-08-222326-pcb-render.json) | 2026-11-08T22:23:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222325-pcb-export.json](evidence/2026-11-08-222325-pcb-export.json) | 2026-11-08T22:23:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222324-pcb-check.json](evidence/2026-11-08-222324-pcb-check.json) | 2026-11-08T22:23:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222322-pcb-get.json](evidence/2026-11-08-222322-pcb-get.json) | 2026-11-08T22:23:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222321-router-run.json](evidence/2026-11-08-222321-router-run.json) | 2026-11-08T22:23:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222319-plane-delete.json](evidence/2026-11-08-222319-plane-delete.json) | 2026-11-08T22:23:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222318-plane-create.json](evidence/2026-11-08-222318-plane-create.json) | 2026-11-08T22:23:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222316-placement-run.json](evidence/2026-11-08-222316-placement-run.json) | 2026-11-08T22:23:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222315-pcb-update.json](evidence/2026-11-08-222315-pcb-update.json) | 2026-11-08T22:23:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222314-forward-annotation-run.json](evidence/2026-11-08-222314-forward-annotation-run.json) | 2026-11-08T22:23:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222250-plane-delete.json](evidence/2026-11-08-222250-plane-delete.json) | 2026-11-08T22:22:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222227-pcb-update.json](evidence/2026-11-08-222227-pcb-update.json) | 2026-11-08T22:22:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222151-via-create.json](evidence/2026-11-08-222151-via-create.json) | 2026-11-08T22:21:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222144-solder-mask-delete.json](evidence/2026-11-08-222144-solder-mask-delete.json) | 2026-11-08T22:21:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222139-solder-mask-create.json](evidence/2026-11-08-222139-solder-mask-create.json) | 2026-11-08T22:21:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222131-plane-delete.json](evidence/2026-11-08-222131-plane-delete.json) | 2026-11-08T22:21:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222129-plane-create.json](evidence/2026-11-08-222129-plane-create.json) | 2026-11-08T22:21:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222107-pcb-export.json](evidence/2026-11-08-222107-pcb-export.json) | 2026-11-08T22:21:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222053-pcb-export.json](evidence/2026-11-08-222053-pcb-export.json) | 2026-11-08T22:20:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222038-pcb-export.json](evidence/2026-11-08-222038-pcb-export.json) | 2026-11-08T22:20:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-222015-pcb-export.json](evidence/2026-11-08-222015-pcb-export.json) | 2026-11-08T22:20:15Z | Xpedition 2604 | windows | 5 次运行中 4 次通过 |
| [2026-11-08-221916-pcb-render.json](evidence/2026-11-08-221916-pcb-render.json) | 2026-11-08T22:19:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221850-pcb-render.json](evidence/2026-11-08-221850-pcb-render.json) | 2026-11-08T22:18:50Z | Xpedition 2604 | windows | 6 次运行中 6 次通过 |
| [2026-11-08-221715-solder-mask-create.json](evidence/2026-11-08-221715-solder-mask-create.json) | 2026-11-08T22:17:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221650-via-create.json](evidence/2026-11-08-221650-via-create.json) | 2026-11-08T22:16:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221622-via-create.json](evidence/2026-11-08-221622-via-create.json) | 2026-11-08T22:16:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221611-via-create.json](evidence/2026-11-08-221611-via-create.json) | 2026-11-08T22:16:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221540-via-create.json](evidence/2026-11-08-221540-via-create.json) | 2026-11-08T22:15:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221529-via-create.json](evidence/2026-11-08-221529-via-create.json) | 2026-11-08T22:15:29Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-221525-solder-mask-delete.json](evidence/2026-11-08-221525-solder-mask-delete.json) | 2026-11-08T22:15:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221523-solder-mask-delete.json](evidence/2026-11-08-221523-solder-mask-delete.json) | 2026-11-08T22:15:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221516-solder-mask-delete.json](evidence/2026-11-08-221516-solder-mask-delete.json) | 2026-11-08T22:15:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221515-solder-mask-delete.json](evidence/2026-11-08-221515-solder-mask-delete.json) | 2026-11-08T22:15:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221513-solder-mask-delete.json](evidence/2026-11-08-221513-solder-mask-delete.json) | 2026-11-08T22:15:13Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-221508-solder-mask-create.json](evidence/2026-11-08-221508-solder-mask-create.json) | 2026-11-08T22:15:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221505-solder-mask-create.json](evidence/2026-11-08-221505-solder-mask-create.json) | 2026-11-08T22:15:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221502-solder-mask-create.json](evidence/2026-11-08-221502-solder-mask-create.json) | 2026-11-08T22:15:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221459-solder-mask-create.json](evidence/2026-11-08-221459-solder-mask-create.json) | 2026-11-08T22:14:59Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-221454-plane-delete.json](evidence/2026-11-08-221454-plane-delete.json) | 2026-11-08T22:14:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221452-plane-delete.json](evidence/2026-11-08-221452-plane-delete.json) | 2026-11-08T22:14:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221446-plane-delete.json](evidence/2026-11-08-221446-plane-delete.json) | 2026-11-08T22:14:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221445-plane-delete.json](evidence/2026-11-08-221445-plane-delete.json) | 2026-11-08T22:14:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221443-plane-delete.json](evidence/2026-11-08-221443-plane-delete.json) | 2026-11-08T22:14:43Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-08-221400-constraint-get.json](evidence/2026-11-08-221400-constraint-get.json) | 2026-11-08T22:14:00Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-08-221356-pcb-check.json](evidence/2026-11-08-221356-pcb-check.json) | 2026-11-08T22:13:56Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-08-221345-pcb-get.json](evidence/2026-11-08-221345-pcb-get.json) | 2026-11-08T22:13:45Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-08-221343-router-run.json](evidence/2026-11-08-221343-router-run.json) | 2026-11-08T22:13:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221340-router-run.json](evidence/2026-11-08-221340-router-run.json) | 2026-11-08T22:13:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221334-router-run.json](evidence/2026-11-08-221334-router-run.json) | 2026-11-08T22:13:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221330-router-run.json](evidence/2026-11-08-221330-router-run.json) | 2026-11-08T22:13:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221321-router-run.json](evidence/2026-11-08-221321-router-run.json) | 2026-11-08T22:13:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221317-router-run.json](evidence/2026-11-08-221317-router-run.json) | 2026-11-08T22:13:17Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-221315-plane-create.json](evidence/2026-11-08-221315-plane-create.json) | 2026-11-08T22:13:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221310-plane-create.json](evidence/2026-11-08-221310-plane-create.json) | 2026-11-08T22:13:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221308-plane-create.json](evidence/2026-11-08-221308-plane-create.json) | 2026-11-08T22:13:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221306-plane-create.json](evidence/2026-11-08-221306-plane-create.json) | 2026-11-08T22:13:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221304-plane-create.json](evidence/2026-11-08-221304-plane-create.json) | 2026-11-08T22:13:04Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-221301-placement-run.json](evidence/2026-11-08-221301-placement-run.json) | 2026-11-08T22:13:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221230-doc-save.json](evidence/2026-11-08-221230-doc-save.json) | 2026-11-08T22:12:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221137-placement-run.json](evidence/2026-11-08-221137-placement-run.json) | 2026-11-08T22:11:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221131-placement-run.json](evidence/2026-11-08-221131-placement-run.json) | 2026-11-08T22:11:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221128-placement-run.json](evidence/2026-11-08-221128-placement-run.json) | 2026-11-08T22:11:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221116-placement-run.json](evidence/2026-11-08-221116-placement-run.json) | 2026-11-08T22:11:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221112-doc-close.json](evidence/2026-11-08-221112-doc-close.json) | 2026-11-08T22:11:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221111-doc-open.json](evidence/2026-11-08-221111-doc-open.json) | 2026-11-08T22:11:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221107-placement-run.json](evidence/2026-11-08-221107-placement-run.json) | 2026-11-08T22:11:07Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-221104-doc-close.json](evidence/2026-11-08-221104-doc-close.json) | 2026-11-08T22:11:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221041-doc-open.json](evidence/2026-11-08-221041-doc-open.json) | 2026-11-08T22:10:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221038-doc-open.json](evidence/2026-11-08-221038-doc-open.json) | 2026-11-08T22:10:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221021-doc-open.json](evidence/2026-11-08-221021-doc-open.json) | 2026-11-08T22:10:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-221000-doc-open.json](evidence/2026-11-08-221000-doc-open.json) | 2026-11-08T22:10:00Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-220956-doc-close.json](evidence/2026-11-08-220956-doc-close.json) | 2026-11-08T22:09:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220952-doc-close.json](evidence/2026-11-08-220952-doc-close.json) | 2026-11-08T22:09:52Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-220827-forward-annotation-run.json](evidence/2026-11-08-220827-forward-annotation-run.json) | 2026-11-08T22:08:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220604-doc-save.json](evidence/2026-11-08-220604-doc-save.json) | 2026-11-08T22:06:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220536-doc-save.json](evidence/2026-11-08-220536-doc-save.json) | 2026-11-08T22:05:36Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-08-220514-doc-save.json](evidence/2026-11-08-220514-doc-save.json) | 2026-11-08T22:05:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220451-pcb-update.json](evidence/2026-11-08-220451-pcb-update.json) | 2026-11-08T22:04:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220445-pcb-update.json](evidence/2026-11-08-220445-pcb-update.json) | 2026-11-08T22:04:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220443-pcb-update.json](evidence/2026-11-08-220443-pcb-update.json) | 2026-11-08T22:04:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220442-pcb-update.json](evidence/2026-11-08-220442-pcb-update.json) | 2026-11-08T22:04:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220440-pcb-update.json](evidence/2026-11-08-220440-pcb-update.json) | 2026-11-08T22:04:40Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-220354-doc-save.json](evidence/2026-11-08-220354-doc-save.json) | 2026-11-08T22:03:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220352-doc-open.json](evidence/2026-11-08-220352-doc-open.json) | 2026-11-08T22:03:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220351-doc-close.json](evidence/2026-11-08-220351-doc-close.json) | 2026-11-08T22:03:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220350-forward-annotation-run.json](evidence/2026-11-08-220350-forward-annotation-run.json) | 2026-11-08T22:03:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-220118-forward-annotation-run.json](evidence/2026-11-08-220118-forward-annotation-run.json) | 2026-11-08T22:01:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-215646-forward-annotation-run.json](evidence/2026-11-08-215646-forward-annotation-run.json) | 2026-11-08T21:56:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-215643-forward-annotation-run.json](evidence/2026-11-08-215643-forward-annotation-run.json) | 2026-11-08T21:56:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-215324-forward-annotation-run.json](evidence/2026-11-08-215324-forward-annotation-run.json) | 2026-11-08T21:53:24Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-215321-constraint-get.json](evidence/2026-11-08-215321-constraint-get.json) | 2026-11-08T21:53:21Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-215318-pcb-check.json](evidence/2026-11-08-215318-pcb-check.json) | 2026-11-08T21:53:18Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-215312-pcb-get.json](evidence/2026-11-08-215312-pcb-get.json) | 2026-11-08T21:53:12Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-215049-pcb-create.json](evidence/2026-11-08-215049-pcb-create.json) | 2026-11-08T21:50:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214826-pcb-create.json](evidence/2026-11-08-214826-pcb-create.json) | 2026-11-08T21:48:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214824-pcb-create.json](evidence/2026-11-08-214824-pcb-create.json) | 2026-11-08T21:48:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214820-pcb-create.json](evidence/2026-11-08-214820-pcb-create.json) | 2026-11-08T21:48:20Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-214815-packager-run.json](evidence/2026-11-08-214815-packager-run.json) | 2026-11-08T21:48:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214812-packager-run.json](evidence/2026-11-08-214812-packager-run.json) | 2026-11-08T21:48:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214807-packager-run.json](evidence/2026-11-08-214807-packager-run.json) | 2026-11-08T21:48:07Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-214557-schematic-export.json](evidence/2026-11-08-214557-schematic-export.json) | 2026-11-08T21:45:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214552-schematic-create.json](evidence/2026-11-08-214552-schematic-create.json) | 2026-11-08T21:45:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214526-session-close.json](evidence/2026-11-08-214526-session-close.json) | 2026-11-08T21:45:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214524-bom-export.json](evidence/2026-11-08-214524-bom-export.json) | 2026-11-08T21:45:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214522-bom-check.json](evidence/2026-11-08-214522-bom-check.json) | 2026-11-08T21:45:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214521-net-get.json](evidence/2026-11-08-214521-net-get.json) | 2026-11-08T21:45:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214519-net-list.json](evidence/2026-11-08-214519-net-list.json) | 2026-11-08T21:45:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214517-component-get.json](evidence/2026-11-08-214517-component-get.json) | 2026-11-08T21:45:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214515-component-list.json](evidence/2026-11-08-214515-component-list.json) | 2026-11-08T21:45:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214514-doc-create.json](evidence/2026-11-08-214514-doc-create.json) | 2026-11-08T21:45:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214512-session-list.json](evidence/2026-11-08-214512-session-list.json) | 2026-11-08T21:45:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214510-session-get.json](evidence/2026-11-08-214510-session-get.json) | 2026-11-08T21:45:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214509-doc-list.json](evidence/2026-11-08-214509-doc-list.json) | 2026-11-08T21:45:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214507-doc-get.json](evidence/2026-11-08-214507-doc-get.json) | 2026-11-08T21:45:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214506-schematic-create.json](evidence/2026-11-08-214506-schematic-create.json) | 2026-11-08T21:45:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214504-schematic-export.json](evidence/2026-11-08-214504-schematic-export.json) | 2026-11-08T21:45:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214502-schematic-check.json](evidence/2026-11-08-214502-schematic-check.json) | 2026-11-08T21:45:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214501-schematic-get.json](evidence/2026-11-08-214501-schematic-get.json) | 2026-11-08T21:45:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214408-bom-export.json](evidence/2026-11-08-214408-bom-export.json) | 2026-11-08T21:44:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214404-bom-export.json](evidence/2026-11-08-214404-bom-export.json) | 2026-11-08T21:44:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214400-bom-export.json](evidence/2026-11-08-214400-bom-export.json) | 2026-11-08T21:44:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214357-bom-check.json](evidence/2026-11-08-214357-bom-check.json) | 2026-11-08T21:43:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214355-net-get.json](evidence/2026-11-08-214355-net-get.json) | 2026-11-08T21:43:55Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-214352-net-list.json](evidence/2026-11-08-214352-net-list.json) | 2026-11-08T21:43:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214350-component-get.json](evidence/2026-11-08-214350-component-get.json) | 2026-11-08T21:43:50Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-214346-schematic-export.json](evidence/2026-11-08-214346-schematic-export.json) | 2026-11-08T21:43:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214340-schematic-export.json](evidence/2026-11-08-214340-schematic-export.json) | 2026-11-08T21:43:40Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-08-214336-schematic-check.json](evidence/2026-11-08-214336-schematic-check.json) | 2026-11-08T21:43:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214332-schematic-get.json](evidence/2026-11-08-214332-schematic-get.json) | 2026-11-08T21:43:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214219-session-close.json](evidence/2026-11-08-214219-session-close.json) | 2026-11-08T21:42:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214207-doc-create.json](evidence/2026-11-08-214207-doc-create.json) | 2026-11-08T21:42:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214156-bom-export.json](evidence/2026-11-08-214156-bom-export.json) | 2026-11-08T21:41:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214144-bom-check.json](evidence/2026-11-08-214144-bom-check.json) | 2026-11-08T21:41:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214132-net-get.json](evidence/2026-11-08-214132-net-get.json) | 2026-11-08T21:41:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214121-net-list.json](evidence/2026-11-08-214121-net-list.json) | 2026-11-08T21:41:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214109-component-get.json](evidence/2026-11-08-214109-component-get.json) | 2026-11-08T21:41:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214058-component-list.json](evidence/2026-11-08-214058-component-list.json) | 2026-11-08T21:40:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214046-schematic-export.json](evidence/2026-11-08-214046-schematic-export.json) | 2026-11-08T21:40:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214034-schematic-check.json](evidence/2026-11-08-214034-schematic-check.json) | 2026-11-08T21:40:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-214023-schematic-get.json](evidence/2026-11-08-214023-schematic-get.json) | 2026-11-08T21:40:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-213141-doc-list.json](evidence/2026-11-08-213141-doc-list.json) | 2026-11-08T21:31:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-213140-session-list.json](evidence/2026-11-08-213140-session-list.json) | 2026-11-08T21:31:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-213106-schematic-create.json](evidence/2026-11-08-213106-schematic-create.json) | 2026-11-08T21:31:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-213104-doc-get.json](evidence/2026-11-08-213104-doc-get.json) | 2026-11-08T21:31:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-213104-session-get.json](evidence/2026-11-08-213104-session-get.json) | 2026-11-08T21:31:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-213020-schematic-create.json](evidence/2026-11-08-213020-schematic-create.json) | 2026-11-08T21:30:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-213005-session-close.json](evidence/2026-11-08-213005-session-close.json) | 2026-11-08T21:30:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-213003-schematic-export.json](evidence/2026-11-08-213003-schematic-export.json) | 2026-11-08T21:30:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212959-padstack-list.json](evidence/2026-11-08-212959-padstack-list.json) | 2026-11-08T21:29:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212958-part-get.json](evidence/2026-11-08-212958-part-get.json) | 2026-11-08T21:29:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212956-part-list.json](evidence/2026-11-08-212956-part-list.json) | 2026-11-08T21:29:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212954-project-get.json](evidence/2026-11-08-212954-project-get.json) | 2026-11-08T21:29:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212951-bom-export.json](evidence/2026-11-08-212951-bom-export.json) | 2026-11-08T21:29:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212948-bom-check.json](evidence/2026-11-08-212948-bom-check.json) | 2026-11-08T21:29:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212946-net-get.json](evidence/2026-11-08-212946-net-get.json) | 2026-11-08T21:29:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212944-net-list.json](evidence/2026-11-08-212944-net-list.json) | 2026-11-08T21:29:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212942-component-get.json](evidence/2026-11-08-212942-component-get.json) | 2026-11-08T21:29:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212940-component-list.json](evidence/2026-11-08-212940-component-list.json) | 2026-11-08T21:29:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212938-doc-list.json](evidence/2026-11-08-212938-doc-list.json) | 2026-11-08T21:29:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212937-doc-get.json](evidence/2026-11-08-212937-doc-get.json) | 2026-11-08T21:29:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212935-schematic-check.json](evidence/2026-11-08-212935-schematic-check.json) | 2026-11-08T21:29:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212931-schematic-get.json](evidence/2026-11-08-212931-schematic-get.json) | 2026-11-08T21:29:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212929-schematic-create.json](evidence/2026-11-08-212929-schematic-create.json) | 2026-11-08T21:29:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212917-schematic-create.json](evidence/2026-11-08-212917-schematic-create.json) | 2026-11-08T21:29:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212855-schematic-create.json](evidence/2026-11-08-212855-schematic-create.json) | 2026-11-08T21:28:55Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212852-pcb-create.json](evidence/2026-11-08-212852-pcb-create.json) | 2026-11-08T21:28:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212851-packager-run.json](evidence/2026-11-08-212851-packager-run.json) | 2026-11-08T21:28:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212849-padstack-create.json](evidence/2026-11-08-212849-padstack-create.json) | 2026-11-08T21:28:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212848-part-create.json](evidence/2026-11-08-212848-part-create.json) | 2026-11-08T21:28:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212846-doc-list.json](evidence/2026-11-08-212846-doc-list.json) | 2026-11-08T21:28:46Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212843-session-get.json](evidence/2026-11-08-212843-session-get.json) | 2026-11-08T21:28:43Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212840-session-open.json](evidence/2026-11-08-212840-session-open.json) | 2026-11-08T21:28:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212801-session-open.json](evidence/2026-11-08-212801-session-open.json) | 2026-11-08T21:28:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212719-session-open.json](evidence/2026-11-08-212719-session-open.json) | 2026-11-08T21:27:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212632-session-open.json](evidence/2026-11-08-212632-session-open.json) | 2026-11-08T21:26:32Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212525-padstack-create.json](evidence/2026-11-08-212525-padstack-create.json) | 2026-11-08T21:25:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212518-padstack-create.json](evidence/2026-11-08-212518-padstack-create.json) | 2026-11-08T21:25:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212516-padstack-create.json](evidence/2026-11-08-212516-padstack-create.json) | 2026-11-08T21:25:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212513-padstack-create.json](evidence/2026-11-08-212513-padstack-create.json) | 2026-11-08T21:25:13Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212512-padstack-list.json](evidence/2026-11-08-212512-padstack-list.json) | 2026-11-08T21:25:12Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-08-212510-part-get.json](evidence/2026-11-08-212510-part-get.json) | 2026-11-08T21:25:10Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212507-part-list.json](evidence/2026-11-08-212507-part-list.json) | 2026-11-08T21:25:07Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-08-212504-part-create.json](evidence/2026-11-08-212504-part-create.json) | 2026-11-08T21:25:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212457-part-create.json](evidence/2026-11-08-212457-part-create.json) | 2026-11-08T21:24:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212453-part-create.json](evidence/2026-11-08-212453-part-create.json) | 2026-11-08T21:24:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212446-part-create.json](evidence/2026-11-08-212446-part-create.json) | 2026-11-08T21:24:46Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212436-project-get.json](evidence/2026-11-08-212436-project-get.json) | 2026-11-08T21:24:36Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212411-session-close.json](evidence/2026-11-08-212411-session-close.json) | 2026-11-08T21:24:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212355-session-close.json](evidence/2026-11-08-212355-session-close.json) | 2026-11-08T21:23:55Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-08-212353-doc-create.json](evidence/2026-11-08-212353-doc-create.json) | 2026-11-08T21:23:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212217-session-list.json](evidence/2026-11-08-212217-session-list.json) | 2026-11-08T21:22:17Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212214-doc-get.json](evidence/2026-11-08-212214-doc-get.json) | 2026-11-08T21:22:14Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212211-bom-export.json](evidence/2026-11-08-212211-bom-export.json) | 2026-11-08T21:22:11Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-08-212207-bom-check.json](evidence/2026-11-08-212207-bom-check.json) | 2026-11-08T21:22:07Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212200-net-list.json](evidence/2026-11-08-212200-net-list.json) | 2026-11-08T21:22:00Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212157-component-list.json](evidence/2026-11-08-212157-component-list.json) | 2026-11-08T21:21:57Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212154-schematic-check.json](evidence/2026-11-08-212154-schematic-check.json) | 2026-11-08T21:21:54Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212150-schematic-get.json](evidence/2026-11-08-212150-schematic-get.json) | 2026-11-08T21:21:50Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-08-212147-doc-create.json](evidence/2026-11-08-212147-doc-create.json) | 2026-11-08T21:21:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212145-doc-create.json](evidence/2026-11-08-212145-doc-create.json) | 2026-11-08T21:21:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212142-doc-create.json](evidence/2026-11-08-212142-doc-create.json) | 2026-11-08T21:21:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-08-212019-doc-create.json](evidence/2026-11-08-212019-doc-create.json) | 2026-11-08T21:20:19Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
<!-- driver:evidence:end -->
