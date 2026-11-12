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

<!-- driver:spec-procedure:start -->
在真实的 Xpedition 上运行行为矩阵：

1. 准备目标，比如在列出的文件夹里专为测试复制的项目、为这次运行启动的实例，或为测试准备好的设备。清单之外的目标，先按「授权规则」列进「授权目标」。
2. 把 `XPEDITION_CLI_STATE_DIR` 设为专供真实软件运行使用的目录。
3. 运行 `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>`，矩阵用到的每个占位符各给一个 `--set`，占位符名不带花括号，如 `--set doc=<file>`：`{session}` 是为这次运行启动的实例，`{doc}` 是授权的文档或文件，`{device}` 是授权的设备，`{dir}` 是授权文件夹里新建的空目录。`{job}` 是作业 ID；不给时，运行器先运行 `--job-from` 指定的长命令（只有一个长命令时就是它）的 `normal` 行，用它启动的作业。`{inputs}` 不用 `--set`：它就是 `tests/inputs/`，里面的文件命令只读取，所以不需要授权。
4. 运行器执行适用的行，把真实软件记录写进 `docs/evidence/`，并填上每一行的实际结果和 `live:` 证据引用。实际结果与预期不符的行算作失败。
5. 在真实的 Xpedition 上无法按需制造的行，如 `disconnect`，可以标为 `unrunnable` 并写明原因；`normal`、`dialog` 和 `read_back_mismatch` 这三行不可以。`read_back_mismatch` 用 `--row read_back_mismatch` 运行：工具会在读回之前等一会儿，在这段时间里，到 Xpedition 里改掉它刚写的内容，或删掉它生成的文件。
6. 运行 `python scripts/spec.py generate` 更新支持矩阵和下面的索引，再把记录和矩阵一起提交。

要在真实的 Xpedition 上运行评测题，用列出的文件夹里的一个文件夹运行 `python scripts/spec.py eval --workspace-root <folder>`。运行器为每道题写好授权范围文件，并把评测报告写进 `docs/evidence/`。

真实软件记录里的运行在命令登记或这次运行用到的适配层代码改动之前一直算作证据；只是版本号变了，什么都不影响。记录里从不出现机密，带机密的参数显示为 `***`。
<!-- driver:spec-procedure:end -->

## 证据记录

`docs/evidence/` 里的真实软件记录，由 `python scripts/spec.py generate` 生成：

<!-- driver:evidence:start -->
| 记录 | 运行时间 | 软件 | 平台 | 结果 |
|---|---|---|---|---|
| [2026-11-12-185930-doc-list.json](evidence/2026-11-12-185930-doc-list.json) | 2026-11-12T18:59:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185844-session-list.json](evidence/2026-11-12-185844-session-list.json) | 2026-11-12T18:58:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185446-component-get.json](evidence/2026-11-12-185446-component-get.json) | 2026-11-12T18:54:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185442-component-list.json](evidence/2026-11-12-185442-component-list.json) | 2026-11-12T18:54:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185357-component-get.json](evidence/2026-11-12-185357-component-get.json) | 2026-11-12T18:53:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185353-component-list.json](evidence/2026-11-12-185353-component-list.json) | 2026-11-12T18:53:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185220-via-update.json](evidence/2026-11-12-185220-via-update.json) | 2026-11-12T18:52:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185211-via-update.json](evidence/2026-11-12-185211-via-update.json) | 2026-11-12T18:52:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185204-via-update.json](evidence/2026-11-12-185204-via-update.json) | 2026-11-12T18:52:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185200-via-update.json](evidence/2026-11-12-185200-via-update.json) | 2026-11-12T18:52:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185130-via-delete.json](evidence/2026-11-12-185130-via-delete.json) | 2026-11-12T18:51:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185105-via-delete.json](evidence/2026-11-12-185105-via-delete.json) | 2026-11-12T18:51:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185043-via-delete.json](evidence/2026-11-12-185043-via-delete.json) | 2026-11-12T18:50:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185038-via-delete.json](evidence/2026-11-12-185038-via-delete.json) | 2026-11-12T18:50:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185016-trace-delete.json](evidence/2026-11-12-185016-trace-delete.json) | 2026-11-12T18:50:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-185001-trace-delete.json](evidence/2026-11-12-185001-trace-delete.json) | 2026-11-12T18:50:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-184949-trace-delete.json](evidence/2026-11-12-184949-trace-delete.json) | 2026-11-12T18:49:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-184945-trace-delete.json](evidence/2026-11-12-184945-trace-delete.json) | 2026-11-12T18:49:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-184651-via-update.json](evidence/2026-11-12-184651-via-update.json) | 2026-11-12T18:46:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-184557-via-update.json](evidence/2026-11-12-184557-via-update.json) | 2026-11-12T18:45:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-184433-via-delete.json](evidence/2026-11-12-184433-via-delete.json) | 2026-11-12T18:44:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-184429-trace-delete.json](evidence/2026-11-12-184429-trace-delete.json) | 2026-11-12T18:44:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-184425-trace-create.json](evidence/2026-11-12-184425-trace-create.json) | 2026-11-12T18:44:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-184420-constraint-import.json](evidence/2026-11-12-184420-constraint-import.json) | 2026-11-12T18:44:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-184304-via-update.json](evidence/2026-11-12-184304-via-update.json) | 2026-11-12T18:43:04Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-184139-via-delete.json](evidence/2026-11-12-184139-via-delete.json) | 2026-11-12T18:41:39Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-184035-trace-delete.json](evidence/2026-11-12-184035-trace-delete.json) | 2026-11-12T18:40:35Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-184021-trace-create.json](evidence/2026-11-12-184021-trace-create.json) | 2026-11-12T18:40:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-184004-trace-check.json](evidence/2026-11-12-184004-trace-check.json) | 2026-11-12T18:40:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183959-trace-create.json](evidence/2026-11-12-183959-trace-create.json) | 2026-11-12T18:39:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183945-trace-create.json](evidence/2026-11-12-183945-trace-create.json) | 2026-11-12T18:39:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183940-trace-check.json](evidence/2026-11-12-183940-trace-check.json) | 2026-11-12T18:39:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183935-trace-create.json](evidence/2026-11-12-183935-trace-create.json) | 2026-11-12T18:39:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183924-trace-check.json](evidence/2026-11-12-183924-trace-check.json) | 2026-11-12T18:39:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183919-trace-check.json](evidence/2026-11-12-183919-trace-check.json) | 2026-11-12T18:39:19Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-12-183913-trace-create.json](evidence/2026-11-12-183913-trace-create.json) | 2026-11-12T18:39:13Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-183900-constraint-import.json](evidence/2026-11-12-183900-constraint-import.json) | 2026-11-12T18:39:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183843-constraint-import.json](evidence/2026-11-12-183843-constraint-import.json) | 2026-11-12T18:38:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183819-constraint-import.json](evidence/2026-11-12-183819-constraint-import.json) | 2026-11-12T18:38:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183808-constraint-import.json](evidence/2026-11-12-183808-constraint-import.json) | 2026-11-12T18:38:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183758-constraint-import.json](evidence/2026-11-12-183758-constraint-import.json) | 2026-11-12T18:37:58Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-183714-refdes-move.json](evidence/2026-11-12-183714-refdes-move.json) | 2026-11-12T18:37:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183708-refdes-move.json](evidence/2026-11-12-183708-refdes-move.json) | 2026-11-12T18:37:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183701-refdes-move.json](evidence/2026-11-12-183701-refdes-move.json) | 2026-11-12T18:37:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183656-refdes-move.json](evidence/2026-11-12-183656-refdes-move.json) | 2026-11-12T18:36:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183651-refdes-move.json](evidence/2026-11-12-183651-refdes-move.json) | 2026-11-12T18:36:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183646-refdes-move.json](evidence/2026-11-12-183646-refdes-move.json) | 2026-11-12T18:36:46Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-183609-mounting-hole-delete.json](evidence/2026-11-12-183609-mounting-hole-delete.json) | 2026-11-12T18:36:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183601-mounting-hole-delete.json](evidence/2026-11-12-183601-mounting-hole-delete.json) | 2026-11-12T18:36:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183555-mounting-hole-delete.json](evidence/2026-11-12-183555-mounting-hole-delete.json) | 2026-11-12T18:35:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183550-mounting-hole-delete.json](evidence/2026-11-12-183550-mounting-hole-delete.json) | 2026-11-12T18:35:50Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-183540-mounting-hole-delete.json](evidence/2026-11-12-183540-mounting-hole-delete.json) | 2026-11-12T18:35:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183536-mounting-hole-create.json](evidence/2026-11-12-183536-mounting-hole-create.json) | 2026-11-12T18:35:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183528-mounting-hole-create.json](evidence/2026-11-12-183528-mounting-hole-create.json) | 2026-11-12T18:35:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183523-mounting-hole-create.json](evidence/2026-11-12-183523-mounting-hole-create.json) | 2026-11-12T18:35:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183519-mounting-hole-list.json](evidence/2026-11-12-183519-mounting-hole-list.json) | 2026-11-12T18:35:19Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-12-183514-mounting-hole-create.json](evidence/2026-11-12-183514-mounting-hole-create.json) | 2026-11-12T18:35:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183509-mounting-hole-create.json](evidence/2026-11-12-183509-mounting-hole-create.json) | 2026-11-12T18:35:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183505-mounting-hole-create.json](evidence/2026-11-12-183505-mounting-hole-create.json) | 2026-11-12T18:35:05Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-183455-mounting-hole-delete.json](evidence/2026-11-12-183455-mounting-hole-delete.json) | 2026-11-12T18:34:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183451-mounting-hole-list.json](evidence/2026-11-12-183451-mounting-hole-list.json) | 2026-11-12T18:34:51Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-183417-component-move.json](evidence/2026-11-12-183417-component-move.json) | 2026-11-12T18:34:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183409-component-move.json](evidence/2026-11-12-183409-component-move.json) | 2026-11-12T18:34:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183404-component-move.json](evidence/2026-11-12-183404-component-move.json) | 2026-11-12T18:34:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183400-component-move.json](evidence/2026-11-12-183400-component-move.json) | 2026-11-12T18:34:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183356-component-move.json](evidence/2026-11-12-183356-component-move.json) | 2026-11-12T18:33:56Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-183254-refdes-move.json](evidence/2026-11-12-183254-refdes-move.json) | 2026-11-12T18:32:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183250-mounting-hole-delete.json](evidence/2026-11-12-183250-mounting-hole-delete.json) | 2026-11-12T18:32:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183246-mounting-hole-create.json](evidence/2026-11-12-183246-mounting-hole-create.json) | 2026-11-12T18:32:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183242-mounting-hole-list.json](evidence/2026-11-12-183242-mounting-hole-list.json) | 2026-11-12T18:32:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183238-component-move.json](evidence/2026-11-12-183238-component-move.json) | 2026-11-12T18:32:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183235-doc-save.json](evidence/2026-11-12-183235-doc-save.json) | 2026-11-12T18:32:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183231-doc-open.json](evidence/2026-11-12-183231-doc-open.json) | 2026-11-12T18:32:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183226-doc-close.json](evidence/2026-11-12-183226-doc-close.json) | 2026-11-12T18:32:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183222-keepout-delete.json](evidence/2026-11-12-183222-keepout-delete.json) | 2026-11-12T18:32:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183218-keepout-create.json](evidence/2026-11-12-183218-keepout-create.json) | 2026-11-12T18:32:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183214-silkscreen-text-delete.json](evidence/2026-11-12-183214-silkscreen-text-delete.json) | 2026-11-12T18:32:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183210-silkscreen-text-create.json](evidence/2026-11-12-183210-silkscreen-text-create.json) | 2026-11-12T18:32:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183206-solder-mask-delete.json](evidence/2026-11-12-183206-solder-mask-delete.json) | 2026-11-12T18:32:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183202-solder-mask-create.json](evidence/2026-11-12-183202-solder-mask-create.json) | 2026-11-12T18:32:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183158-via-update.json](evidence/2026-11-12-183158-via-update.json) | 2026-11-12T18:31:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183154-via-delete.json](evidence/2026-11-12-183154-via-delete.json) | 2026-11-12T18:31:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183150-via-create.json](evidence/2026-11-12-183150-via-create.json) | 2026-11-12T18:31:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183146-trace-delete.json](evidence/2026-11-12-183146-trace-delete.json) | 2026-11-12T18:31:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183142-trace-check.json](evidence/2026-11-12-183142-trace-check.json) | 2026-11-12T18:31:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183139-trace-create.json](evidence/2026-11-12-183139-trace-create.json) | 2026-11-12T18:31:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183135-constraint-import.json](evidence/2026-11-12-183135-constraint-import.json) | 2026-11-12T18:31:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183131-constraint-get.json](evidence/2026-11-12-183131-constraint-get.json) | 2026-11-12T18:31:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183127-pcb-render.json](evidence/2026-11-12-183127-pcb-render.json) | 2026-11-12T18:31:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183123-pcb-export.json](evidence/2026-11-12-183123-pcb-export.json) | 2026-11-12T18:31:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183119-pcb-check.json](evidence/2026-11-12-183119-pcb-check.json) | 2026-11-12T18:31:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183116-pcb-get.json](evidence/2026-11-12-183116-pcb-get.json) | 2026-11-12T18:31:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183112-router-run.json](evidence/2026-11-12-183112-router-run.json) | 2026-11-12T18:31:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183108-plane-delete.json](evidence/2026-11-12-183108-plane-delete.json) | 2026-11-12T18:31:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183104-plane-create.json](evidence/2026-11-12-183104-plane-create.json) | 2026-11-12T18:31:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183100-placement-run.json](evidence/2026-11-12-183100-placement-run.json) | 2026-11-12T18:31:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183056-pcb-update.json](evidence/2026-11-12-183056-pcb-update.json) | 2026-11-12T18:30:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183052-forward-annotation-run.json](evidence/2026-11-12-183052-forward-annotation-run.json) | 2026-11-12T18:30:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183031-refdes-move.json](evidence/2026-11-12-183031-refdes-move.json) | 2026-11-12T18:30:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183024-mounting-hole-delete.json](evidence/2026-11-12-183024-mounting-hole-delete.json) | 2026-11-12T18:30:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183018-mounting-hole-create.json](evidence/2026-11-12-183018-mounting-hole-create.json) | 2026-11-12T18:30:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-183001-mounting-hole-list.json](evidence/2026-11-12-183001-mounting-hole-list.json) | 2026-11-12T18:30:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182955-component-move.json](evidence/2026-11-12-182955-component-move.json) | 2026-11-12T18:29:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182948-doc-save.json](evidence/2026-11-12-182948-doc-save.json) | 2026-11-12T18:29:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182942-doc-open.json](evidence/2026-11-12-182942-doc-open.json) | 2026-11-12T18:29:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182925-doc-close.json](evidence/2026-11-12-182925-doc-close.json) | 2026-11-12T18:29:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182918-keepout-delete.json](evidence/2026-11-12-182918-keepout-delete.json) | 2026-11-12T18:29:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182912-keepout-create.json](evidence/2026-11-12-182912-keepout-create.json) | 2026-11-12T18:29:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182906-silkscreen-text-delete.json](evidence/2026-11-12-182906-silkscreen-text-delete.json) | 2026-11-12T18:29:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182849-silkscreen-text-create.json](evidence/2026-11-12-182849-silkscreen-text-create.json) | 2026-11-12T18:28:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182842-solder-mask-delete.json](evidence/2026-11-12-182842-solder-mask-delete.json) | 2026-11-12T18:28:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182835-solder-mask-create.json](evidence/2026-11-12-182835-solder-mask-create.json) | 2026-11-12T18:28:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182828-via-update.json](evidence/2026-11-12-182828-via-update.json) | 2026-11-12T18:28:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182756-via-delete.json](evidence/2026-11-12-182756-via-delete.json) | 2026-11-12T18:27:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182734-via-create.json](evidence/2026-11-12-182734-via-create.json) | 2026-11-12T18:27:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182728-trace-delete.json](evidence/2026-11-12-182728-trace-delete.json) | 2026-11-12T18:27:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182722-trace-check.json](evidence/2026-11-12-182722-trace-check.json) | 2026-11-12T18:27:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182716-trace-create.json](evidence/2026-11-12-182716-trace-create.json) | 2026-11-12T18:27:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182700-constraint-import.json](evidence/2026-11-12-182700-constraint-import.json) | 2026-11-12T18:27:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182653-constraint-get.json](evidence/2026-11-12-182653-constraint-get.json) | 2026-11-12T18:26:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182647-pcb-render.json](evidence/2026-11-12-182647-pcb-render.json) | 2026-11-12T18:26:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182641-pcb-export.json](evidence/2026-11-12-182641-pcb-export.json) | 2026-11-12T18:26:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182623-pcb-check.json](evidence/2026-11-12-182623-pcb-check.json) | 2026-11-12T18:26:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182615-pcb-get.json](evidence/2026-11-12-182615-pcb-get.json) | 2026-11-12T18:26:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182610-router-run.json](evidence/2026-11-12-182610-router-run.json) | 2026-11-12T18:26:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182604-plane-delete.json](evidence/2026-11-12-182604-plane-delete.json) | 2026-11-12T18:26:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182547-plane-create.json](evidence/2026-11-12-182547-plane-create.json) | 2026-11-12T18:25:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182540-placement-run.json](evidence/2026-11-12-182540-placement-run.json) | 2026-11-12T18:25:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182534-pcb-update.json](evidence/2026-11-12-182534-pcb-update.json) | 2026-11-12T18:25:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182526-forward-annotation-run.json](evidence/2026-11-12-182526-forward-annotation-run.json) | 2026-11-12T18:25:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182514-session-close.json](evidence/2026-11-12-182514-session-close.json) | 2026-11-12T18:25:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182510-doc-list.json](evidence/2026-11-12-182510-doc-list.json) | 2026-11-12T18:25:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182506-component-move.json](evidence/2026-11-12-182506-component-move.json) | 2026-11-12T18:25:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182501-via-update.json](evidence/2026-11-12-182501-via-update.json) | 2026-11-12T18:25:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182457-via-delete.json](evidence/2026-11-12-182457-via-delete.json) | 2026-11-12T18:24:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182453-via-create.json](evidence/2026-11-12-182453-via-create.json) | 2026-11-12T18:24:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182449-trace-delete.json](evidence/2026-11-12-182449-trace-delete.json) | 2026-11-12T18:24:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182445-trace-check.json](evidence/2026-11-12-182445-trace-check.json) | 2026-11-12T18:24:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182441-trace-create.json](evidence/2026-11-12-182441-trace-create.json) | 2026-11-12T18:24:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182438-keepout-delete.json](evidence/2026-11-12-182438-keepout-delete.json) | 2026-11-12T18:24:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182434-keepout-create.json](evidence/2026-11-12-182434-keepout-create.json) | 2026-11-12T18:24:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182429-silkscreen-text-delete.json](evidence/2026-11-12-182429-silkscreen-text-delete.json) | 2026-11-12T18:24:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182425-silkscreen-text-create.json](evidence/2026-11-12-182425-silkscreen-text-create.json) | 2026-11-12T18:24:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182421-solder-mask-delete.json](evidence/2026-11-12-182421-solder-mask-delete.json) | 2026-11-12T18:24:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182417-solder-mask-create.json](evidence/2026-11-12-182417-solder-mask-create.json) | 2026-11-12T18:24:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182413-router-run.json](evidence/2026-11-12-182413-router-run.json) | 2026-11-12T18:24:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182409-refdes-move.json](evidence/2026-11-12-182409-refdes-move.json) | 2026-11-12T18:24:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182406-plane-delete.json](evidence/2026-11-12-182406-plane-delete.json) | 2026-11-12T18:24:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182402-plane-create.json](evidence/2026-11-12-182402-plane-create.json) | 2026-11-12T18:24:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182358-placement-run.json](evidence/2026-11-12-182358-placement-run.json) | 2026-11-12T18:23:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182353-pcb-update.json](evidence/2026-11-12-182353-pcb-update.json) | 2026-11-12T18:23:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182349-pcb-render.json](evidence/2026-11-12-182349-pcb-render.json) | 2026-11-12T18:23:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182345-pcb-get.json](evidence/2026-11-12-182345-pcb-get.json) | 2026-11-12T18:23:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182342-pcb-export.json](evidence/2026-11-12-182342-pcb-export.json) | 2026-11-12T18:23:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182338-pcb-check.json](evidence/2026-11-12-182338-pcb-check.json) | 2026-11-12T18:23:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182334-mounting-hole-delete.json](evidence/2026-11-12-182334-mounting-hole-delete.json) | 2026-11-12T18:23:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182330-mounting-hole-create.json](evidence/2026-11-12-182330-mounting-hole-create.json) | 2026-11-12T18:23:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182326-mounting-hole-list.json](evidence/2026-11-12-182326-mounting-hole-list.json) | 2026-11-12T18:23:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182323-forward-annotation-run.json](evidence/2026-11-12-182323-forward-annotation-run.json) | 2026-11-12T18:23:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182319-doc-save.json](evidence/2026-11-12-182319-doc-save.json) | 2026-11-12T18:23:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182315-doc-open.json](evidence/2026-11-12-182315-doc-open.json) | 2026-11-12T18:23:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182311-doc-close.json](evidence/2026-11-12-182311-doc-close.json) | 2026-11-12T18:23:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182307-constraint-import.json](evidence/2026-11-12-182307-constraint-import.json) | 2026-11-12T18:23:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182303-constraint-get.json](evidence/2026-11-12-182303-constraint-get.json) | 2026-11-12T18:23:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182300-session-close.json](evidence/2026-11-12-182300-session-close.json) | 2026-11-12T18:23:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182255-session-get.json](evidence/2026-11-12-182255-session-get.json) | 2026-11-12T18:22:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182251-schematic-get.json](evidence/2026-11-12-182251-schematic-get.json) | 2026-11-12T18:22:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182248-schematic-export.json](evidence/2026-11-12-182248-schematic-export.json) | 2026-11-12T18:22:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182244-schematic-create.json](evidence/2026-11-12-182244-schematic-create.json) | 2026-11-12T18:22:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182240-schematic-check.json](evidence/2026-11-12-182240-schematic-check.json) | 2026-11-12T18:22:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182236-net-list.json](evidence/2026-11-12-182236-net-list.json) | 2026-11-12T18:22:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182233-net-get.json](evidence/2026-11-12-182233-net-get.json) | 2026-11-12T18:22:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182230-component-list.json](evidence/2026-11-12-182230-component-list.json) | 2026-11-12T18:22:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182226-component-get.json](evidence/2026-11-12-182226-component-get.json) | 2026-11-12T18:22:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182222-bom-export.json](evidence/2026-11-12-182222-bom-export.json) | 2026-11-12T18:22:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182218-bom-check.json](evidence/2026-11-12-182218-bom-check.json) | 2026-11-12T18:22:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182132-plane-delete.json](evidence/2026-11-12-182132-plane-delete.json) | 2026-11-12T18:21:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182123-pcb-update.json](evidence/2026-11-12-182123-pcb-update.json) | 2026-11-12T18:21:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182119-via-create.json](evidence/2026-11-12-182119-via-create.json) | 2026-11-12T18:21:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182112-keepout-delete.json](evidence/2026-11-12-182112-keepout-delete.json) | 2026-11-12T18:21:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182108-keepout-create.json](evidence/2026-11-12-182108-keepout-create.json) | 2026-11-12T18:21:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182104-silkscreen-text-delete.json](evidence/2026-11-12-182104-silkscreen-text-delete.json) | 2026-11-12T18:21:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182100-silkscreen-text-create.json](evidence/2026-11-12-182100-silkscreen-text-create.json) | 2026-11-12T18:21:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182056-solder-mask-delete.json](evidence/2026-11-12-182056-solder-mask-delete.json) | 2026-11-12T18:20:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182052-solder-mask-create.json](evidence/2026-11-12-182052-solder-mask-create.json) | 2026-11-12T18:20:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182047-plane-delete.json](evidence/2026-11-12-182047-plane-delete.json) | 2026-11-12T18:20:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182043-plane-create.json](evidence/2026-11-12-182043-plane-create.json) | 2026-11-12T18:20:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-182006-pcb-export.json](evidence/2026-11-12-182006-pcb-export.json) | 2026-11-12T18:20:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181958-pcb-export.json](evidence/2026-11-12-181958-pcb-export.json) | 2026-11-12T18:19:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181939-pcb-export.json](evidence/2026-11-12-181939-pcb-export.json) | 2026-11-12T18:19:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181910-pcb-export.json](evidence/2026-11-12-181910-pcb-export.json) | 2026-11-12T18:19:10Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-181749-pcb-render.json](evidence/2026-11-12-181749-pcb-render.json) | 2026-11-12T18:17:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181732-pcb-render.json](evidence/2026-11-12-181732-pcb-render.json) | 2026-11-12T18:17:32Z | Xpedition 2604 | windows | 6 次运行中 6 次通过 |
| [2026-11-12-181636-keepout-delete.json](evidence/2026-11-12-181636-keepout-delete.json) | 2026-11-12T18:16:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181627-keepout-delete.json](evidence/2026-11-12-181627-keepout-delete.json) | 2026-11-12T18:16:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181622-keepout-delete.json](evidence/2026-11-12-181622-keepout-delete.json) | 2026-11-12T18:16:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181618-keepout-delete.json](evidence/2026-11-12-181618-keepout-delete.json) | 2026-11-12T18:16:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181614-keepout-delete.json](evidence/2026-11-12-181614-keepout-delete.json) | 2026-11-12T18:16:14Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-181605-keepout-create.json](evidence/2026-11-12-181605-keepout-create.json) | 2026-11-12T18:16:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181557-keepout-create.json](evidence/2026-11-12-181557-keepout-create.json) | 2026-11-12T18:15:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181552-keepout-create.json](evidence/2026-11-12-181552-keepout-create.json) | 2026-11-12T18:15:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181548-keepout-create.json](evidence/2026-11-12-181548-keepout-create.json) | 2026-11-12T18:15:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181544-keepout-create.json](evidence/2026-11-12-181544-keepout-create.json) | 2026-11-12T18:15:44Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-181528-silkscreen-text-delete.json](evidence/2026-11-12-181528-silkscreen-text-delete.json) | 2026-11-12T18:15:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181516-silkscreen-text-delete.json](evidence/2026-11-12-181516-silkscreen-text-delete.json) | 2026-11-12T18:15:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181512-silkscreen-text-delete.json](evidence/2026-11-12-181512-silkscreen-text-delete.json) | 2026-11-12T18:15:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181508-silkscreen-text-delete.json](evidence/2026-11-12-181508-silkscreen-text-delete.json) | 2026-11-12T18:15:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181503-silkscreen-text-delete.json](evidence/2026-11-12-181503-silkscreen-text-delete.json) | 2026-11-12T18:15:03Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-181452-silkscreen-text-create.json](evidence/2026-11-12-181452-silkscreen-text-create.json) | 2026-11-12T18:14:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181442-silkscreen-text-create.json](evidence/2026-11-12-181442-silkscreen-text-create.json) | 2026-11-12T18:14:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181436-silkscreen-text-create.json](evidence/2026-11-12-181436-silkscreen-text-create.json) | 2026-11-12T18:14:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181432-silkscreen-text-create.json](evidence/2026-11-12-181432-silkscreen-text-create.json) | 2026-11-12T18:14:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181425-silkscreen-text-create.json](evidence/2026-11-12-181425-silkscreen-text-create.json) | 2026-11-12T18:14:25Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-181415-via-create.json](evidence/2026-11-12-181415-via-create.json) | 2026-11-12T18:14:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181357-via-create.json](evidence/2026-11-12-181357-via-create.json) | 2026-11-12T18:13:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181346-via-create.json](evidence/2026-11-12-181346-via-create.json) | 2026-11-12T18:13:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181322-via-create.json](evidence/2026-11-12-181322-via-create.json) | 2026-11-12T18:13:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181312-via-create.json](evidence/2026-11-12-181312-via-create.json) | 2026-11-12T18:13:12Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-181300-solder-mask-delete.json](evidence/2026-11-12-181300-solder-mask-delete.json) | 2026-11-12T18:13:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181256-solder-mask-delete.json](evidence/2026-11-12-181256-solder-mask-delete.json) | 2026-11-12T18:12:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181246-solder-mask-delete.json](evidence/2026-11-12-181246-solder-mask-delete.json) | 2026-11-12T18:12:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181242-solder-mask-delete.json](evidence/2026-11-12-181242-solder-mask-delete.json) | 2026-11-12T18:12:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181237-solder-mask-delete.json](evidence/2026-11-12-181237-solder-mask-delete.json) | 2026-11-12T18:12:37Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-181228-solder-mask-create.json](evidence/2026-11-12-181228-solder-mask-create.json) | 2026-11-12T18:12:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181220-solder-mask-create.json](evidence/2026-11-12-181220-solder-mask-create.json) | 2026-11-12T18:12:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181215-solder-mask-create.json](evidence/2026-11-12-181215-solder-mask-create.json) | 2026-11-12T18:12:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181210-solder-mask-create.json](evidence/2026-11-12-181210-solder-mask-create.json) | 2026-11-12T18:12:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181205-solder-mask-create.json](evidence/2026-11-12-181205-solder-mask-create.json) | 2026-11-12T18:12:05Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-181149-plane-delete.json](evidence/2026-11-12-181149-plane-delete.json) | 2026-11-12T18:11:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181145-plane-delete.json](evidence/2026-11-12-181145-plane-delete.json) | 2026-11-12T18:11:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181135-plane-delete.json](evidence/2026-11-12-181135-plane-delete.json) | 2026-11-12T18:11:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181131-plane-delete.json](evidence/2026-11-12-181131-plane-delete.json) | 2026-11-12T18:11:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181127-plane-delete.json](evidence/2026-11-12-181127-plane-delete.json) | 2026-11-12T18:11:27Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-12-181120-constraint-get.json](evidence/2026-11-12-181120-constraint-get.json) | 2026-11-12T18:11:20Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-12-181112-pcb-check.json](evidence/2026-11-12-181112-pcb-check.json) | 2026-11-12T18:11:12Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-12-181057-pcb-get.json](evidence/2026-11-12-181057-pcb-get.json) | 2026-11-12T18:10:57Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-12-181050-router-run.json](evidence/2026-11-12-181050-router-run.json) | 2026-11-12T18:10:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181044-router-run.json](evidence/2026-11-12-181044-router-run.json) | 2026-11-12T18:10:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181035-router-run.json](evidence/2026-11-12-181035-router-run.json) | 2026-11-12T18:10:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181027-router-run.json](evidence/2026-11-12-181027-router-run.json) | 2026-11-12T18:10:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181014-router-run.json](evidence/2026-11-12-181014-router-run.json) | 2026-11-12T18:10:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-181008-router-run.json](evidence/2026-11-12-181008-router-run.json) | 2026-11-12T18:10:08Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-180959-plane-create.json](evidence/2026-11-12-180959-plane-create.json) | 2026-11-12T18:09:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180951-plane-create.json](evidence/2026-11-12-180951-plane-create.json) | 2026-11-12T18:09:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180946-plane-create.json](evidence/2026-11-12-180946-plane-create.json) | 2026-11-12T18:09:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180942-plane-create.json](evidence/2026-11-12-180942-plane-create.json) | 2026-11-12T18:09:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180938-plane-create.json](evidence/2026-11-12-180938-plane-create.json) | 2026-11-12T18:09:38Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-180928-placement-run.json](evidence/2026-11-12-180928-placement-run.json) | 2026-11-12T18:09:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180921-doc-save.json](evidence/2026-11-12-180921-doc-save.json) | 2026-11-12T18:09:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180814-placement-run.json](evidence/2026-11-12-180814-placement-run.json) | 2026-11-12T18:08:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180806-placement-run.json](evidence/2026-11-12-180806-placement-run.json) | 2026-11-12T18:08:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180800-placement-run.json](evidence/2026-11-12-180800-placement-run.json) | 2026-11-12T18:08:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180743-placement-run.json](evidence/2026-11-12-180743-placement-run.json) | 2026-11-12T18:07:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180737-doc-close.json](evidence/2026-11-12-180737-doc-close.json) | 2026-11-12T18:07:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180733-doc-open.json](evidence/2026-11-12-180733-doc-open.json) | 2026-11-12T18:07:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180726-placement-run.json](evidence/2026-11-12-180726-placement-run.json) | 2026-11-12T18:07:26Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-180647-doc-close.json](evidence/2026-11-12-180647-doc-close.json) | 2026-11-12T18:06:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180618-doc-open.json](evidence/2026-11-12-180618-doc-open.json) | 2026-11-12T18:06:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180613-doc-open.json](evidence/2026-11-12-180613-doc-open.json) | 2026-11-12T18:06:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180550-doc-open.json](evidence/2026-11-12-180550-doc-open.json) | 2026-11-12T18:05:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180523-doc-open.json](evidence/2026-11-12-180523-doc-open.json) | 2026-11-12T18:05:23Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-180512-doc-close.json](evidence/2026-11-12-180512-doc-close.json) | 2026-11-12T18:05:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180503-doc-close.json](evidence/2026-11-12-180503-doc-close.json) | 2026-11-12T18:05:03Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-180424-forward-annotation-run.json](evidence/2026-11-12-180424-forward-annotation-run.json) | 2026-11-12T18:04:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180122-doc-save.json](evidence/2026-11-12-180122-doc-save.json) | 2026-11-12T18:01:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-180044-doc-save.json](evidence/2026-11-12-180044-doc-save.json) | 2026-11-12T18:00:44Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-180007-doc-save.json](evidence/2026-11-12-180007-doc-save.json) | 2026-11-12T18:00:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175932-pcb-update.json](evidence/2026-11-12-175932-pcb-update.json) | 2026-11-12T17:59:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175925-pcb-update.json](evidence/2026-11-12-175925-pcb-update.json) | 2026-11-12T17:59:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175920-pcb-update.json](evidence/2026-11-12-175920-pcb-update.json) | 2026-11-12T17:59:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175916-pcb-update.json](evidence/2026-11-12-175916-pcb-update.json) | 2026-11-12T17:59:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175911-pcb-update.json](evidence/2026-11-12-175911-pcb-update.json) | 2026-11-12T17:59:11Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-175855-doc-save.json](evidence/2026-11-12-175855-doc-save.json) | 2026-11-12T17:58:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175851-doc-open.json](evidence/2026-11-12-175851-doc-open.json) | 2026-11-12T17:58:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175847-doc-close.json](evidence/2026-11-12-175847-doc-close.json) | 2026-11-12T17:58:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175844-forward-annotation-run.json](evidence/2026-11-12-175844-forward-annotation-run.json) | 2026-11-12T17:58:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175826-forward-annotation-run.json](evidence/2026-11-12-175826-forward-annotation-run.json) | 2026-11-12T17:58:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175322-forward-annotation-run.json](evidence/2026-11-12-175322-forward-annotation-run.json) | 2026-11-12T17:53:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175315-forward-annotation-run.json](evidence/2026-11-12-175315-forward-annotation-run.json) | 2026-11-12T17:53:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175249-forward-annotation-run.json](evidence/2026-11-12-175249-forward-annotation-run.json) | 2026-11-12T17:52:49Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-175238-constraint-get.json](evidence/2026-11-12-175238-constraint-get.json) | 2026-11-12T17:52:38Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-175226-pcb-check.json](evidence/2026-11-12-175226-pcb-check.json) | 2026-11-12T17:52:26Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-175208-pcb-get.json](evidence/2026-11-12-175208-pcb-get.json) | 2026-11-12T17:52:08Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-175118-pcb-create.json](evidence/2026-11-12-175118-pcb-create.json) | 2026-11-12T17:51:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175114-pcb-create.json](evidence/2026-11-12-175114-pcb-create.json) | 2026-11-12T17:51:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-175102-pcb-create.json](evidence/2026-11-12-175102-pcb-create.json) | 2026-11-12T17:51:02Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-175050-pcb-create.json](evidence/2026-11-12-175050-pcb-create.json) | 2026-11-12T17:50:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174836-packager-run.json](evidence/2026-11-12-174836-packager-run.json) | 2026-11-12T17:48:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174825-packager-run.json](evidence/2026-11-12-174825-packager-run.json) | 2026-11-12T17:48:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174814-packager-run.json](evidence/2026-11-12-174814-packager-run.json) | 2026-11-12T17:48:14Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-174641-schematic-export.json](evidence/2026-11-12-174641-schematic-export.json) | 2026-11-12T17:46:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174632-schematic-create.json](evidence/2026-11-12-174632-schematic-create.json) | 2026-11-12T17:46:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174615-session-close.json](evidence/2026-11-12-174615-session-close.json) | 2026-11-12T17:46:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174611-bom-export.json](evidence/2026-11-12-174611-bom-export.json) | 2026-11-12T17:46:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174606-bom-check.json](evidence/2026-11-12-174606-bom-check.json) | 2026-11-12T17:46:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174601-net-get.json](evidence/2026-11-12-174601-net-get.json) | 2026-11-12T17:46:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174557-net-list.json](evidence/2026-11-12-174557-net-list.json) | 2026-11-12T17:45:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174552-component-get.json](evidence/2026-11-12-174552-component-get.json) | 2026-11-12T17:45:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174548-component-list.json](evidence/2026-11-12-174548-component-list.json) | 2026-11-12T17:45:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174544-doc-create.json](evidence/2026-11-12-174544-doc-create.json) | 2026-11-12T17:45:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174540-session-list.json](evidence/2026-11-12-174540-session-list.json) | 2026-11-12T17:45:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174536-session-get.json](evidence/2026-11-12-174536-session-get.json) | 2026-11-12T17:45:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174532-doc-list.json](evidence/2026-11-12-174532-doc-list.json) | 2026-11-12T17:45:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174528-doc-get.json](evidence/2026-11-12-174528-doc-get.json) | 2026-11-12T17:45:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174525-schematic-create.json](evidence/2026-11-12-174525-schematic-create.json) | 2026-11-12T17:45:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174520-schematic-export.json](evidence/2026-11-12-174520-schematic-export.json) | 2026-11-12T17:45:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174514-schematic-check.json](evidence/2026-11-12-174514-schematic-check.json) | 2026-11-12T17:45:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174511-schematic-get.json](evidence/2026-11-12-174511-schematic-get.json) | 2026-11-12T17:45:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174432-bom-export.json](evidence/2026-11-12-174432-bom-export.json) | 2026-11-12T17:44:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174426-bom-export.json](evidence/2026-11-12-174426-bom-export.json) | 2026-11-12T17:44:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174419-bom-export.json](evidence/2026-11-12-174419-bom-export.json) | 2026-11-12T17:44:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174413-bom-check.json](evidence/2026-11-12-174413-bom-check.json) | 2026-11-12T17:44:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174408-net-get.json](evidence/2026-11-12-174408-net-get.json) | 2026-11-12T17:44:08Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-174358-net-list.json](evidence/2026-11-12-174358-net-list.json) | 2026-11-12T17:43:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174354-component-get.json](evidence/2026-11-12-174354-component-get.json) | 2026-11-12T17:43:54Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-174341-schematic-export.json](evidence/2026-11-12-174341-schematic-export.json) | 2026-11-12T17:43:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174333-schematic-export.json](evidence/2026-11-12-174333-schematic-export.json) | 2026-11-12T17:43:33Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-174319-schematic-check.json](evidence/2026-11-12-174319-schematic-check.json) | 2026-11-12T17:43:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174313-schematic-get.json](evidence/2026-11-12-174313-schematic-get.json) | 2026-11-12T17:43:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174216-session-close.json](evidence/2026-11-12-174216-session-close.json) | 2026-11-12T17:42:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174203-bom-export.json](evidence/2026-11-12-174203-bom-export.json) | 2026-11-12T17:42:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174149-bom-check.json](evidence/2026-11-12-174149-bom-check.json) | 2026-11-12T17:41:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174136-net-get.json](evidence/2026-11-12-174136-net-get.json) | 2026-11-12T17:41:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174122-net-list.json](evidence/2026-11-12-174122-net-list.json) | 2026-11-12T17:41:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174109-component-get.json](evidence/2026-11-12-174109-component-get.json) | 2026-11-12T17:41:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174056-component-list.json](evidence/2026-11-12-174056-component-list.json) | 2026-11-12T17:40:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174043-doc-create.json](evidence/2026-11-12-174043-doc-create.json) | 2026-11-12T17:40:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174028-session-get.json](evidence/2026-11-12-174028-session-get.json) | 2026-11-12T17:40:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174015-doc-get.json](evidence/2026-11-12-174015-doc-get.json) | 2026-11-12T17:40:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-174002-schematic-create.json](evidence/2026-11-12-174002-schematic-create.json) | 2026-11-12T17:40:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173948-schematic-export.json](evidence/2026-11-12-173948-schematic-export.json) | 2026-11-12T17:39:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173934-schematic-check.json](evidence/2026-11-12-173934-schematic-check.json) | 2026-11-12T17:39:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173921-schematic-get.json](evidence/2026-11-12-173921-schematic-get.json) | 2026-11-12T17:39:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173711-schematic-create.json](evidence/2026-11-12-173711-schematic-create.json) | 2026-11-12T17:37:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173656-session-close.json](evidence/2026-11-12-173656-session-close.json) | 2026-11-12T17:36:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173653-schematic-export.json](evidence/2026-11-12-173653-schematic-export.json) | 2026-11-12T17:36:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173647-padstack-list.json](evidence/2026-11-12-173647-padstack-list.json) | 2026-11-12T17:36:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173644-part-get.json](evidence/2026-11-12-173644-part-get.json) | 2026-11-12T17:36:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173640-part-list.json](evidence/2026-11-12-173640-part-list.json) | 2026-11-12T17:36:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173637-project-get.json](evidence/2026-11-12-173637-project-get.json) | 2026-11-12T17:36:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173634-bom-export.json](evidence/2026-11-12-173634-bom-export.json) | 2026-11-12T17:36:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173629-bom-check.json](evidence/2026-11-12-173629-bom-check.json) | 2026-11-12T17:36:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173625-net-get.json](evidence/2026-11-12-173625-net-get.json) | 2026-11-12T17:36:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173621-net-list.json](evidence/2026-11-12-173621-net-list.json) | 2026-11-12T17:36:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173617-component-get.json](evidence/2026-11-12-173617-component-get.json) | 2026-11-12T17:36:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173614-component-list.json](evidence/2026-11-12-173614-component-list.json) | 2026-11-12T17:36:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173610-doc-list.json](evidence/2026-11-12-173610-doc-list.json) | 2026-11-12T17:36:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173606-doc-get.json](evidence/2026-11-12-173606-doc-get.json) | 2026-11-12T17:36:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173603-schematic-check.json](evidence/2026-11-12-173603-schematic-check.json) | 2026-11-12T17:36:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173558-schematic-get.json](evidence/2026-11-12-173558-schematic-get.json) | 2026-11-12T17:35:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173554-schematic-create.json](evidence/2026-11-12-173554-schematic-create.json) | 2026-11-12T17:35:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173539-schematic-create.json](evidence/2026-11-12-173539-schematic-create.json) | 2026-11-12T17:35:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173521-schematic-create.json](evidence/2026-11-12-173521-schematic-create.json) | 2026-11-12T17:35:21Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-173512-pcb-create.json](evidence/2026-11-12-173512-pcb-create.json) | 2026-11-12T17:35:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173508-packager-run.json](evidence/2026-11-12-173508-packager-run.json) | 2026-11-12T17:35:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173505-padstack-create.json](evidence/2026-11-12-173505-padstack-create.json) | 2026-11-12T17:35:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173501-part-create.json](evidence/2026-11-12-173501-part-create.json) | 2026-11-12T17:35:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173457-doc-list.json](evidence/2026-11-12-173457-doc-list.json) | 2026-11-12T17:34:57Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-173450-session-get.json](evidence/2026-11-12-173450-session-get.json) | 2026-11-12T17:34:50Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-173442-session-open.json](evidence/2026-11-12-173442-session-open.json) | 2026-11-12T17:34:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173437-session-open.json](evidence/2026-11-12-173437-session-open.json) | 2026-11-12T17:34:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173350-session-open.json](evidence/2026-11-12-173350-session-open.json) | 2026-11-12T17:33:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173307-session-open.json](evidence/2026-11-12-173307-session-open.json) | 2026-11-12T17:33:07Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-173257-padstack-create.json](evidence/2026-11-12-173257-padstack-create.json) | 2026-11-12T17:32:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173245-padstack-create.json](evidence/2026-11-12-173245-padstack-create.json) | 2026-11-12T17:32:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173239-padstack-create.json](evidence/2026-11-12-173239-padstack-create.json) | 2026-11-12T17:32:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173233-padstack-create.json](evidence/2026-11-12-173233-padstack-create.json) | 2026-11-12T17:32:33Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-173225-padstack-list.json](evidence/2026-11-12-173225-padstack-list.json) | 2026-11-12T17:32:25Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-173216-part-get.json](evidence/2026-11-12-173216-part-get.json) | 2026-11-12T17:32:16Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-173208-part-list.json](evidence/2026-11-12-173208-part-list.json) | 2026-11-12T17:32:08Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-173200-part-create.json](evidence/2026-11-12-173200-part-create.json) | 2026-11-12T17:32:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173150-part-create.json](evidence/2026-11-12-173150-part-create.json) | 2026-11-12T17:31:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173142-part-create.json](evidence/2026-11-12-173142-part-create.json) | 2026-11-12T17:31:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173132-part-create.json](evidence/2026-11-12-173132-part-create.json) | 2026-11-12T17:31:32Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-173123-project-get.json](evidence/2026-11-12-173123-project-get.json) | 2026-11-12T17:31:23Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-173116-session-close.json](evidence/2026-11-12-173116-session-close.json) | 2026-11-12T17:31:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-173101-session-close.json](evidence/2026-11-12-173101-session-close.json) | 2026-11-12T17:31:01Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-12-173055-doc-create.json](evidence/2026-11-12-173055-doc-create.json) | 2026-11-12T17:30:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-172914-session-list.json](evidence/2026-11-12-172914-session-list.json) | 2026-11-12T17:29:14Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-172901-doc-get.json](evidence/2026-11-12-172901-doc-get.json) | 2026-11-12T17:29:01Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-172854-bom-export.json](evidence/2026-11-12-172854-bom-export.json) | 2026-11-12T17:28:54Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-172841-bom-check.json](evidence/2026-11-12-172841-bom-check.json) | 2026-11-12T17:28:41Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-172833-net-list.json](evidence/2026-11-12-172833-net-list.json) | 2026-11-12T17:28:33Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-172826-component-list.json](evidence/2026-11-12-172826-component-list.json) | 2026-11-12T17:28:26Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-172819-schematic-check.json](evidence/2026-11-12-172819-schematic-check.json) | 2026-11-12T17:28:19Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-172809-schematic-get.json](evidence/2026-11-12-172809-schematic-get.json) | 2026-11-12T17:28:09Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-172802-doc-create.json](evidence/2026-11-12-172802-doc-create.json) | 2026-11-12T17:28:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-172758-doc-create.json](evidence/2026-11-12-172758-doc-create.json) | 2026-11-12T17:27:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-172751-doc-create.json](evidence/2026-11-12-172751-doc-create.json) | 2026-11-12T17:27:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-172630-doc-create.json](evidence/2026-11-12-172630-doc-create.json) | 2026-11-12T17:26:30Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
<!-- driver:evidence:end -->
