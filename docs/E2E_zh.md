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

真实软件记录里的运行在命令登记或适配层代码改动之前一直算作证据；只是版本号变了，什么都不影响。记录里从不出现机密，带机密的参数显示为 `***`。
<!-- driver:spec-procedure:end -->

## 证据记录

`docs/evidence/` 里的真实软件记录，由 `python scripts/spec.py generate` 生成：

<!-- driver:evidence:start -->
| 记录 | 运行时间 | 软件 | 平台 | 结果 |
|---|---|---|---|---|
| [2026-11-11-214325-doc-list.json](evidence/2026-11-11-214325-doc-list.json) | 2026-11-11T21:43:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-214311-session-list.json](evidence/2026-11-11-214311-session-list.json) | 2026-11-11T21:43:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-214147-component-get.json](evidence/2026-11-11-214147-component-get.json) | 2026-11-11T21:41:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-214145-component-list.json](evidence/2026-11-11-214145-component-list.json) | 2026-11-11T21:41:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-214036-via-delete.json](evidence/2026-11-11-214036-via-delete.json) | 2026-11-11T21:40:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-214032-via-update.json](evidence/2026-11-11-214032-via-update.json) | 2026-11-11T21:40:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-214017-via-update.json](evidence/2026-11-11-214017-via-update.json) | 2026-11-11T21:40:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-213937-via-delete.json](evidence/2026-11-11-213937-via-delete.json) | 2026-11-11T21:39:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-213630-constraint-import.json](evidence/2026-11-11-213630-constraint-import.json) | 2026-11-11T21:36:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-213607-trace-create.json](evidence/2026-11-11-213607-trace-create.json) | 2026-11-11T21:36:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-213546-trace-create.json](evidence/2026-11-11-213546-trace-create.json) | 2026-11-11T21:35:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-213529-trace-create.json](evidence/2026-11-11-213529-trace-create.json) | 2026-11-11T21:35:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-213046-component-get.json](evidence/2026-11-11-213046-component-get.json) | 2026-11-11T21:30:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-213044-component-list.json](evidence/2026-11-11-213044-component-list.json) | 2026-11-11T21:30:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-213001-via-update.json](evidence/2026-11-11-213001-via-update.json) | 2026-11-11T21:30:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212809-via-update.json](evidence/2026-11-11-212809-via-update.json) | 2026-11-11T21:28:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212804-via-update.json](evidence/2026-11-11-212804-via-update.json) | 2026-11-11T21:28:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212758-via-update.json](evidence/2026-11-11-212758-via-update.json) | 2026-11-11T21:27:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212733-via-delete.json](evidence/2026-11-11-212733-via-delete.json) | 2026-11-11T21:27:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212721-via-delete.json](evidence/2026-11-11-212721-via-delete.json) | 2026-11-11T21:27:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212708-trace-delete.json](evidence/2026-11-11-212708-trace-delete.json) | 2026-11-11T21:27:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212653-trace-delete.json](evidence/2026-11-11-212653-trace-delete.json) | 2026-11-11T21:26:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212641-trace-delete.json](evidence/2026-11-11-212641-trace-delete.json) | 2026-11-11T21:26:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212638-trace-delete.json](evidence/2026-11-11-212638-trace-delete.json) | 2026-11-11T21:26:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212619-via-delete.json](evidence/2026-11-11-212619-via-delete.json) | 2026-11-11T21:26:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212616-trace-delete.json](evidence/2026-11-11-212616-trace-delete.json) | 2026-11-11T21:26:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212614-trace-create.json](evidence/2026-11-11-212614-trace-create.json) | 2026-11-11T21:26:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212610-constraint-import.json](evidence/2026-11-11-212610-constraint-import.json) | 2026-11-11T21:26:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212406-via-update.json](evidence/2026-11-11-212406-via-update.json) | 2026-11-11T21:24:06Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-212324-via-delete.json](evidence/2026-11-11-212324-via-delete.json) | 2026-11-11T21:23:24Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-212239-trace-delete.json](evidence/2026-11-11-212239-trace-delete.json) | 2026-11-11T21:22:39Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-212221-trace-check.json](evidence/2026-11-11-212221-trace-check.json) | 2026-11-11T21:22:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212209-trace-create.json](evidence/2026-11-11-212209-trace-create.json) | 2026-11-11T21:22:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212205-trace-check.json](evidence/2026-11-11-212205-trace-check.json) | 2026-11-11T21:22:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212156-trace-check.json](evidence/2026-11-11-212156-trace-check.json) | 2026-11-11T21:21:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-212151-trace-check.json](evidence/2026-11-11-212151-trace-check.json) | 2026-11-11T21:21:51Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-11-212148-trace-create.json](evidence/2026-11-11-212148-trace-create.json) | 2026-11-11T21:21:48Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-212141-constraint-import.json](evidence/2026-11-11-212141-constraint-import.json) | 2026-11-11T21:21:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211949-constraint-import.json](evidence/2026-11-11-211949-constraint-import.json) | 2026-11-11T21:19:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211941-constraint-import.json](evidence/2026-11-11-211941-constraint-import.json) | 2026-11-11T21:19:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211930-constraint-import.json](evidence/2026-11-11-211930-constraint-import.json) | 2026-11-11T21:19:30Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-211853-refdes-move.json](evidence/2026-11-11-211853-refdes-move.json) | 2026-11-11T21:18:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211849-refdes-move.json](evidence/2026-11-11-211849-refdes-move.json) | 2026-11-11T21:18:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211843-refdes-move.json](evidence/2026-11-11-211843-refdes-move.json) | 2026-11-11T21:18:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211840-refdes-move.json](evidence/2026-11-11-211840-refdes-move.json) | 2026-11-11T21:18:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211837-refdes-move.json](evidence/2026-11-11-211837-refdes-move.json) | 2026-11-11T21:18:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211834-refdes-move.json](evidence/2026-11-11-211834-refdes-move.json) | 2026-11-11T21:18:34Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-211803-mounting-hole-delete.json](evidence/2026-11-11-211803-mounting-hole-delete.json) | 2026-11-11T21:18:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211757-mounting-hole-delete.json](evidence/2026-11-11-211757-mounting-hole-delete.json) | 2026-11-11T21:17:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211753-mounting-hole-delete.json](evidence/2026-11-11-211753-mounting-hole-delete.json) | 2026-11-11T21:17:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211750-mounting-hole-delete.json](evidence/2026-11-11-211750-mounting-hole-delete.json) | 2026-11-11T21:17:50Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-211746-mounting-hole-delete.json](evidence/2026-11-11-211746-mounting-hole-delete.json) | 2026-11-11T21:17:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211743-mounting-hole-create.json](evidence/2026-11-11-211743-mounting-hole-create.json) | 2026-11-11T21:17:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211737-mounting-hole-create.json](evidence/2026-11-11-211737-mounting-hole-create.json) | 2026-11-11T21:17:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211734-mounting-hole-create.json](evidence/2026-11-11-211734-mounting-hole-create.json) | 2026-11-11T21:17:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211732-mounting-hole-list.json](evidence/2026-11-11-211732-mounting-hole-list.json) | 2026-11-11T21:17:32Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-11-211730-mounting-hole-create.json](evidence/2026-11-11-211730-mounting-hole-create.json) | 2026-11-11T21:17:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211727-mounting-hole-create.json](evidence/2026-11-11-211727-mounting-hole-create.json) | 2026-11-11T21:17:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211724-mounting-hole-create.json](evidence/2026-11-11-211724-mounting-hole-create.json) | 2026-11-11T21:17:24Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-211720-mounting-hole-delete.json](evidence/2026-11-11-211720-mounting-hole-delete.json) | 2026-11-11T21:17:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211717-mounting-hole-list.json](evidence/2026-11-11-211717-mounting-hole-list.json) | 2026-11-11T21:17:17Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-211650-component-move.json](evidence/2026-11-11-211650-component-move.json) | 2026-11-11T21:16:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211644-component-move.json](evidence/2026-11-11-211644-component-move.json) | 2026-11-11T21:16:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211642-component-move.json](evidence/2026-11-11-211642-component-move.json) | 2026-11-11T21:16:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211639-component-move.json](evidence/2026-11-11-211639-component-move.json) | 2026-11-11T21:16:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211636-component-move.json](evidence/2026-11-11-211636-component-move.json) | 2026-11-11T21:16:36Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-11-211546-refdes-move.json](evidence/2026-11-11-211546-refdes-move.json) | 2026-11-11T21:15:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211544-mounting-hole-delete.json](evidence/2026-11-11-211544-mounting-hole-delete.json) | 2026-11-11T21:15:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211541-mounting-hole-create.json](evidence/2026-11-11-211541-mounting-hole-create.json) | 2026-11-11T21:15:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211539-mounting-hole-list.json](evidence/2026-11-11-211539-mounting-hole-list.json) | 2026-11-11T21:15:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211538-component-move.json](evidence/2026-11-11-211538-component-move.json) | 2026-11-11T21:15:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211535-doc-save.json](evidence/2026-11-11-211535-doc-save.json) | 2026-11-11T21:15:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211533-doc-open.json](evidence/2026-11-11-211533-doc-open.json) | 2026-11-11T21:15:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211531-doc-close.json](evidence/2026-11-11-211531-doc-close.json) | 2026-11-11T21:15:31Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211529-solder-mask-delete.json](evidence/2026-11-11-211529-solder-mask-delete.json) | 2026-11-11T21:15:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211526-solder-mask-create.json](evidence/2026-11-11-211526-solder-mask-create.json) | 2026-11-11T21:15:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211524-via-update.json](evidence/2026-11-11-211524-via-update.json) | 2026-11-11T21:15:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211522-via-delete.json](evidence/2026-11-11-211522-via-delete.json) | 2026-11-11T21:15:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211519-via-create.json](evidence/2026-11-11-211519-via-create.json) | 2026-11-11T21:15:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211517-trace-delete.json](evidence/2026-11-11-211517-trace-delete.json) | 2026-11-11T21:15:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211515-trace-check.json](evidence/2026-11-11-211515-trace-check.json) | 2026-11-11T21:15:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211513-trace-create.json](evidence/2026-11-11-211513-trace-create.json) | 2026-11-11T21:15:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211511-constraint-import.json](evidence/2026-11-11-211511-constraint-import.json) | 2026-11-11T21:15:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211508-constraint-get.json](evidence/2026-11-11-211508-constraint-get.json) | 2026-11-11T21:15:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211507-pcb-render.json](evidence/2026-11-11-211507-pcb-render.json) | 2026-11-11T21:15:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211504-pcb-export.json](evidence/2026-11-11-211504-pcb-export.json) | 2026-11-11T21:15:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211502-pcb-check.json](evidence/2026-11-11-211502-pcb-check.json) | 2026-11-11T21:15:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211500-pcb-get.json](evidence/2026-11-11-211500-pcb-get.json) | 2026-11-11T21:15:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211458-router-run.json](evidence/2026-11-11-211458-router-run.json) | 2026-11-11T21:14:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211455-plane-delete.json](evidence/2026-11-11-211455-plane-delete.json) | 2026-11-11T21:14:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211453-plane-create.json](evidence/2026-11-11-211453-plane-create.json) | 2026-11-11T21:14:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211450-placement-run.json](evidence/2026-11-11-211450-placement-run.json) | 2026-11-11T21:14:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211448-pcb-update.json](evidence/2026-11-11-211448-pcb-update.json) | 2026-11-11T21:14:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211446-forward-annotation-run.json](evidence/2026-11-11-211446-forward-annotation-run.json) | 2026-11-11T21:14:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211423-refdes-move.json](evidence/2026-11-11-211423-refdes-move.json) | 2026-11-11T21:14:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211420-mounting-hole-delete.json](evidence/2026-11-11-211420-mounting-hole-delete.json) | 2026-11-11T21:14:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211415-mounting-hole-create.json](evidence/2026-11-11-211415-mounting-hole-create.json) | 2026-11-11T21:14:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211357-mounting-hole-list.json](evidence/2026-11-11-211357-mounting-hole-list.json) | 2026-11-11T21:13:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211353-component-move.json](evidence/2026-11-11-211353-component-move.json) | 2026-11-11T21:13:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211349-doc-save.json](evidence/2026-11-11-211349-doc-save.json) | 2026-11-11T21:13:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211345-doc-open.json](evidence/2026-11-11-211345-doc-open.json) | 2026-11-11T21:13:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211327-doc-close.json](evidence/2026-11-11-211327-doc-close.json) | 2026-11-11T21:13:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211322-solder-mask-delete.json](evidence/2026-11-11-211322-solder-mask-delete.json) | 2026-11-11T21:13:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211319-solder-mask-create.json](evidence/2026-11-11-211319-solder-mask-create.json) | 2026-11-11T21:13:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211314-via-update.json](evidence/2026-11-11-211314-via-update.json) | 2026-11-11T21:13:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211257-via-delete.json](evidence/2026-11-11-211257-via-delete.json) | 2026-11-11T21:12:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211253-via-create.json](evidence/2026-11-11-211253-via-create.json) | 2026-11-11T21:12:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211249-trace-delete.json](evidence/2026-11-11-211249-trace-delete.json) | 2026-11-11T21:12:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211245-trace-check.json](evidence/2026-11-11-211245-trace-check.json) | 2026-11-11T21:12:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211241-trace-create.json](evidence/2026-11-11-211241-trace-create.json) | 2026-11-11T21:12:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211222-constraint-import.json](evidence/2026-11-11-211222-constraint-import.json) | 2026-11-11T21:12:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211218-constraint-get.json](evidence/2026-11-11-211218-constraint-get.json) | 2026-11-11T21:12:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211215-pcb-render.json](evidence/2026-11-11-211215-pcb-render.json) | 2026-11-11T21:12:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211210-pcb-export.json](evidence/2026-11-11-211210-pcb-export.json) | 2026-11-11T21:12:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211149-pcb-check.json](evidence/2026-11-11-211149-pcb-check.json) | 2026-11-11T21:11:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211146-pcb-get.json](evidence/2026-11-11-211146-pcb-get.json) | 2026-11-11T21:11:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211143-router-run.json](evidence/2026-11-11-211143-router-run.json) | 2026-11-11T21:11:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211138-plane-delete.json](evidence/2026-11-11-211138-plane-delete.json) | 2026-11-11T21:11:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211118-plane-create.json](evidence/2026-11-11-211118-plane-create.json) | 2026-11-11T21:11:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211114-placement-run.json](evidence/2026-11-11-211114-placement-run.json) | 2026-11-11T21:11:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211110-pcb-update.json](evidence/2026-11-11-211110-pcb-update.json) | 2026-11-11T21:11:10Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211105-forward-annotation-run.json](evidence/2026-11-11-211105-forward-annotation-run.json) | 2026-11-11T21:11:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211056-session-close.json](evidence/2026-11-11-211056-session-close.json) | 2026-11-11T21:10:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211054-doc-list.json](evidence/2026-11-11-211054-doc-list.json) | 2026-11-11T21:10:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211052-component-move.json](evidence/2026-11-11-211052-component-move.json) | 2026-11-11T21:10:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211050-via-update.json](evidence/2026-11-11-211050-via-update.json) | 2026-11-11T21:10:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211047-via-delete.json](evidence/2026-11-11-211047-via-delete.json) | 2026-11-11T21:10:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211045-via-create.json](evidence/2026-11-11-211045-via-create.json) | 2026-11-11T21:10:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211042-trace-delete.json](evidence/2026-11-11-211042-trace-delete.json) | 2026-11-11T21:10:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211040-trace-check.json](evidence/2026-11-11-211040-trace-check.json) | 2026-11-11T21:10:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211038-trace-create.json](evidence/2026-11-11-211038-trace-create.json) | 2026-11-11T21:10:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211036-solder-mask-delete.json](evidence/2026-11-11-211036-solder-mask-delete.json) | 2026-11-11T21:10:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211034-solder-mask-create.json](evidence/2026-11-11-211034-solder-mask-create.json) | 2026-11-11T21:10:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211032-router-run.json](evidence/2026-11-11-211032-router-run.json) | 2026-11-11T21:10:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211029-refdes-move.json](evidence/2026-11-11-211029-refdes-move.json) | 2026-11-11T21:10:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211027-plane-delete.json](evidence/2026-11-11-211027-plane-delete.json) | 2026-11-11T21:10:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211025-plane-create.json](evidence/2026-11-11-211025-plane-create.json) | 2026-11-11T21:10:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211022-placement-run.json](evidence/2026-11-11-211022-placement-run.json) | 2026-11-11T21:10:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211020-pcb-update.json](evidence/2026-11-11-211020-pcb-update.json) | 2026-11-11T21:10:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211017-pcb-render.json](evidence/2026-11-11-211017-pcb-render.json) | 2026-11-11T21:10:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211015-pcb-get.json](evidence/2026-11-11-211015-pcb-get.json) | 2026-11-11T21:10:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211013-pcb-export.json](evidence/2026-11-11-211013-pcb-export.json) | 2026-11-11T21:10:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211011-pcb-check.json](evidence/2026-11-11-211011-pcb-check.json) | 2026-11-11T21:10:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211009-mounting-hole-delete.json](evidence/2026-11-11-211009-mounting-hole-delete.json) | 2026-11-11T21:10:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211007-mounting-hole-create.json](evidence/2026-11-11-211007-mounting-hole-create.json) | 2026-11-11T21:10:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211005-mounting-hole-list.json](evidence/2026-11-11-211005-mounting-hole-list.json) | 2026-11-11T21:10:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211003-forward-annotation-run.json](evidence/2026-11-11-211003-forward-annotation-run.json) | 2026-11-11T21:10:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-211001-doc-save.json](evidence/2026-11-11-211001-doc-save.json) | 2026-11-11T21:10:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210958-doc-open.json](evidence/2026-11-11-210958-doc-open.json) | 2026-11-11T21:09:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210956-doc-close.json](evidence/2026-11-11-210956-doc-close.json) | 2026-11-11T21:09:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210953-constraint-import.json](evidence/2026-11-11-210953-constraint-import.json) | 2026-11-11T21:09:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210951-constraint-get.json](evidence/2026-11-11-210951-constraint-get.json) | 2026-11-11T21:09:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210949-session-close.json](evidence/2026-11-11-210949-session-close.json) | 2026-11-11T21:09:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210947-session-get.json](evidence/2026-11-11-210947-session-get.json) | 2026-11-11T21:09:47Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210946-schematic-get.json](evidence/2026-11-11-210946-schematic-get.json) | 2026-11-11T21:09:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210944-schematic-export.json](evidence/2026-11-11-210944-schematic-export.json) | 2026-11-11T21:09:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210942-schematic-create.json](evidence/2026-11-11-210942-schematic-create.json) | 2026-11-11T21:09:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210939-schematic-check.json](evidence/2026-11-11-210939-schematic-check.json) | 2026-11-11T21:09:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210937-net-list.json](evidence/2026-11-11-210937-net-list.json) | 2026-11-11T21:09:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210935-net-get.json](evidence/2026-11-11-210935-net-get.json) | 2026-11-11T21:09:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210933-component-list.json](evidence/2026-11-11-210933-component-list.json) | 2026-11-11T21:09:33Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210932-component-get.json](evidence/2026-11-11-210932-component-get.json) | 2026-11-11T21:09:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210930-bom-export.json](evidence/2026-11-11-210930-bom-export.json) | 2026-11-11T21:09:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210927-bom-check.json](evidence/2026-11-11-210927-bom-check.json) | 2026-11-11T21:09:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210849-plane-delete.json](evidence/2026-11-11-210849-plane-delete.json) | 2026-11-11T21:08:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210842-pcb-update.json](evidence/2026-11-11-210842-pcb-update.json) | 2026-11-11T21:08:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210837-via-create.json](evidence/2026-11-11-210837-via-create.json) | 2026-11-11T21:08:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210827-solder-mask-delete.json](evidence/2026-11-11-210827-solder-mask-delete.json) | 2026-11-11T21:08:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210821-solder-mask-create.json](evidence/2026-11-11-210821-solder-mask-create.json) | 2026-11-11T21:08:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210813-plane-delete.json](evidence/2026-11-11-210813-plane-delete.json) | 2026-11-11T21:08:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210811-plane-create.json](evidence/2026-11-11-210811-plane-create.json) | 2026-11-11T21:08:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210735-pcb-export.json](evidence/2026-11-11-210735-pcb-export.json) | 2026-11-11T21:07:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210730-pcb-export.json](evidence/2026-11-11-210730-pcb-export.json) | 2026-11-11T21:07:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210713-pcb-export.json](evidence/2026-11-11-210713-pcb-export.json) | 2026-11-11T21:07:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210647-pcb-export.json](evidence/2026-11-11-210647-pcb-export.json) | 2026-11-11T21:06:47Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-11-210542-pcb-render.json](evidence/2026-11-11-210542-pcb-render.json) | 2026-11-11T21:05:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210514-pcb-render.json](evidence/2026-11-11-210514-pcb-render.json) | 2026-11-11T21:05:14Z | Xpedition 2604 | windows | 6 次运行中 6 次通过 |
| [2026-11-11-210349-via-create.json](evidence/2026-11-11-210349-via-create.json) | 2026-11-11T21:03:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210315-via-create.json](evidence/2026-11-11-210315-via-create.json) | 2026-11-11T21:03:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210304-via-create.json](evidence/2026-11-11-210304-via-create.json) | 2026-11-11T21:03:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210237-via-create.json](evidence/2026-11-11-210237-via-create.json) | 2026-11-11T21:02:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210225-via-create.json](evidence/2026-11-11-210225-via-create.json) | 2026-11-11T21:02:25Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-210220-solder-mask-delete.json](evidence/2026-11-11-210220-solder-mask-delete.json) | 2026-11-11T21:02:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210217-solder-mask-delete.json](evidence/2026-11-11-210217-solder-mask-delete.json) | 2026-11-11T21:02:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210208-solder-mask-delete.json](evidence/2026-11-11-210208-solder-mask-delete.json) | 2026-11-11T21:02:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210206-solder-mask-delete.json](evidence/2026-11-11-210206-solder-mask-delete.json) | 2026-11-11T21:02:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210204-solder-mask-delete.json](evidence/2026-11-11-210204-solder-mask-delete.json) | 2026-11-11T21:02:04Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-210200-solder-mask-create.json](evidence/2026-11-11-210200-solder-mask-create.json) | 2026-11-11T21:02:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210152-solder-mask-create.json](evidence/2026-11-11-210152-solder-mask-create.json) | 2026-11-11T21:01:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210149-solder-mask-create.json](evidence/2026-11-11-210149-solder-mask-create.json) | 2026-11-11T21:01:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210145-solder-mask-create.json](evidence/2026-11-11-210145-solder-mask-create.json) | 2026-11-11T21:01:45Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210142-solder-mask-create.json](evidence/2026-11-11-210142-solder-mask-create.json) | 2026-11-11T21:01:42Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-210132-plane-delete.json](evidence/2026-11-11-210132-plane-delete.json) | 2026-11-11T21:01:32Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210130-plane-delete.json](evidence/2026-11-11-210130-plane-delete.json) | 2026-11-11T21:01:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210121-plane-delete.json](evidence/2026-11-11-210121-plane-delete.json) | 2026-11-11T21:01:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210119-plane-delete.json](evidence/2026-11-11-210119-plane-delete.json) | 2026-11-11T21:01:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210116-plane-delete.json](evidence/2026-11-11-210116-plane-delete.json) | 2026-11-11T21:01:16Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-11-210113-constraint-get.json](evidence/2026-11-11-210113-constraint-get.json) | 2026-11-11T21:01:13Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-11-210108-pcb-check.json](evidence/2026-11-11-210108-pcb-check.json) | 2026-11-11T21:01:08Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-11-210057-pcb-get.json](evidence/2026-11-11-210057-pcb-get.json) | 2026-11-11T21:00:57Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-11-210054-router-run.json](evidence/2026-11-11-210054-router-run.json) | 2026-11-11T21:00:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210049-router-run.json](evidence/2026-11-11-210049-router-run.json) | 2026-11-11T21:00:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210041-router-run.json](evidence/2026-11-11-210041-router-run.json) | 2026-11-11T21:00:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210035-router-run.json](evidence/2026-11-11-210035-router-run.json) | 2026-11-11T21:00:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210024-router-run.json](evidence/2026-11-11-210024-router-run.json) | 2026-11-11T21:00:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210019-router-run.json](evidence/2026-11-11-210019-router-run.json) | 2026-11-11T21:00:19Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-210016-plane-create.json](evidence/2026-11-11-210016-plane-create.json) | 2026-11-11T21:00:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210009-plane-create.json](evidence/2026-11-11-210009-plane-create.json) | 2026-11-11T21:00:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210006-plane-create.json](evidence/2026-11-11-210006-plane-create.json) | 2026-11-11T21:00:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210004-plane-create.json](evidence/2026-11-11-210004-plane-create.json) | 2026-11-11T21:00:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-210001-plane-create.json](evidence/2026-11-11-210001-plane-create.json) | 2026-11-11T21:00:01Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-205956-placement-run.json](evidence/2026-11-11-205956-placement-run.json) | 2026-11-11T20:59:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205951-doc-save.json](evidence/2026-11-11-205951-doc-save.json) | 2026-11-11T20:59:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205850-placement-run.json](evidence/2026-11-11-205850-placement-run.json) | 2026-11-11T20:58:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205842-placement-run.json](evidence/2026-11-11-205842-placement-run.json) | 2026-11-11T20:58:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205838-placement-run.json](evidence/2026-11-11-205838-placement-run.json) | 2026-11-11T20:58:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205824-placement-run.json](evidence/2026-11-11-205824-placement-run.json) | 2026-11-11T20:58:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205819-doc-close.json](evidence/2026-11-11-205819-doc-close.json) | 2026-11-11T20:58:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205817-doc-open.json](evidence/2026-11-11-205817-doc-open.json) | 2026-11-11T20:58:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205812-placement-run.json](evidence/2026-11-11-205812-placement-run.json) | 2026-11-11T20:58:12Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-205741-doc-close.json](evidence/2026-11-11-205741-doc-close.json) | 2026-11-11T20:57:41Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205717-doc-open.json](evidence/2026-11-11-205717-doc-open.json) | 2026-11-11T20:57:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205713-doc-open.json](evidence/2026-11-11-205713-doc-open.json) | 2026-11-11T20:57:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205652-doc-open.json](evidence/2026-11-11-205652-doc-open.json) | 2026-11-11T20:56:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205630-doc-open.json](evidence/2026-11-11-205630-doc-open.json) | 2026-11-11T20:56:30Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-205623-doc-close.json](evidence/2026-11-11-205623-doc-close.json) | 2026-11-11T20:56:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205617-doc-close.json](evidence/2026-11-11-205617-doc-close.json) | 2026-11-11T20:56:17Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-205544-forward-annotation-run.json](evidence/2026-11-11-205544-forward-annotation-run.json) | 2026-11-11T20:55:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205257-doc-save.json](evidence/2026-11-11-205257-doc-save.json) | 2026-11-11T20:52:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205222-doc-save.json](evidence/2026-11-11-205222-doc-save.json) | 2026-11-11T20:52:22Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-11-205153-doc-save.json](evidence/2026-11-11-205153-doc-save.json) | 2026-11-11T20:51:53Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205125-pcb-update.json](evidence/2026-11-11-205125-pcb-update.json) | 2026-11-11T20:51:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205119-pcb-update.json](evidence/2026-11-11-205119-pcb-update.json) | 2026-11-11T20:51:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205116-pcb-update.json](evidence/2026-11-11-205116-pcb-update.json) | 2026-11-11T20:51:16Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205113-pcb-update.json](evidence/2026-11-11-205113-pcb-update.json) | 2026-11-11T20:51:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205110-pcb-update.json](evidence/2026-11-11-205110-pcb-update.json) | 2026-11-11T20:51:10Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-205059-doc-save.json](evidence/2026-11-11-205059-doc-save.json) | 2026-11-11T20:50:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205056-doc-open.json](evidence/2026-11-11-205056-doc-open.json) | 2026-11-11T20:50:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205054-doc-close.json](evidence/2026-11-11-205054-doc-close.json) | 2026-11-11T20:50:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205052-forward-annotation-run.json](evidence/2026-11-11-205052-forward-annotation-run.json) | 2026-11-11T20:50:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-205036-forward-annotation-run.json](evidence/2026-11-11-205036-forward-annotation-run.json) | 2026-11-11T20:50:36Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204614-forward-annotation-run.json](evidence/2026-11-11-204614-forward-annotation-run.json) | 2026-11-11T20:46:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204609-forward-annotation-run.json](evidence/2026-11-11-204609-forward-annotation-run.json) | 2026-11-11T20:46:09Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204552-forward-annotation-run.json](evidence/2026-11-11-204552-forward-annotation-run.json) | 2026-11-11T20:45:52Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-204548-constraint-get.json](evidence/2026-11-11-204548-constraint-get.json) | 2026-11-11T20:45:48Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-204545-pcb-check.json](evidence/2026-11-11-204545-pcb-check.json) | 2026-11-11T20:45:45Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-204538-pcb-get.json](evidence/2026-11-11-204538-pcb-get.json) | 2026-11-11T20:45:38Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-204507-pcb-create.json](evidence/2026-11-11-204507-pcb-create.json) | 2026-11-11T20:45:07Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204505-pcb-create.json](evidence/2026-11-11-204505-pcb-create.json) | 2026-11-11T20:45:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204459-pcb-create.json](evidence/2026-11-11-204459-pcb-create.json) | 2026-11-11T20:44:59Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-204455-pcb-create.json](evidence/2026-11-11-204455-pcb-create.json) | 2026-11-11T20:44:55Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204303-packager-run.json](evidence/2026-11-11-204303-packager-run.json) | 2026-11-11T20:43:03Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204258-packager-run.json](evidence/2026-11-11-204258-packager-run.json) | 2026-11-11T20:42:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204250-packager-run.json](evidence/2026-11-11-204250-packager-run.json) | 2026-11-11T20:42:50Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-204134-schematic-export.json](evidence/2026-11-11-204134-schematic-export.json) | 2026-11-11T20:41:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204128-schematic-create.json](evidence/2026-11-11-204128-schematic-create.json) | 2026-11-11T20:41:28Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204113-session-close.json](evidence/2026-11-11-204113-session-close.json) | 2026-11-11T20:41:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204111-bom-export.json](evidence/2026-11-11-204111-bom-export.json) | 2026-11-11T20:41:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204108-bom-check.json](evidence/2026-11-11-204108-bom-check.json) | 2026-11-11T20:41:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204106-net-get.json](evidence/2026-11-11-204106-net-get.json) | 2026-11-11T20:41:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204104-net-list.json](evidence/2026-11-11-204104-net-list.json) | 2026-11-11T20:41:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204102-component-get.json](evidence/2026-11-11-204102-component-get.json) | 2026-11-11T20:41:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204100-component-list.json](evidence/2026-11-11-204100-component-list.json) | 2026-11-11T20:41:00Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204058-doc-create.json](evidence/2026-11-11-204058-doc-create.json) | 2026-11-11T20:40:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204056-session-list.json](evidence/2026-11-11-204056-session-list.json) | 2026-11-11T20:40:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204054-session-get.json](evidence/2026-11-11-204054-session-get.json) | 2026-11-11T20:40:54Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204052-doc-list.json](evidence/2026-11-11-204052-doc-list.json) | 2026-11-11T20:40:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204050-doc-get.json](evidence/2026-11-11-204050-doc-get.json) | 2026-11-11T20:40:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204048-schematic-create.json](evidence/2026-11-11-204048-schematic-create.json) | 2026-11-11T20:40:48Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204046-schematic-export.json](evidence/2026-11-11-204046-schematic-export.json) | 2026-11-11T20:40:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204044-schematic-check.json](evidence/2026-11-11-204044-schematic-check.json) | 2026-11-11T20:40:44Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204042-schematic-get.json](evidence/2026-11-11-204042-schematic-get.json) | 2026-11-11T20:40:42Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-204002-bom-export.json](evidence/2026-11-11-204002-bom-export.json) | 2026-11-11T20:40:02Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203957-bom-export.json](evidence/2026-11-11-203957-bom-export.json) | 2026-11-11T20:39:57Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203952-bom-export.json](evidence/2026-11-11-203952-bom-export.json) | 2026-11-11T20:39:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203949-bom-check.json](evidence/2026-11-11-203949-bom-check.json) | 2026-11-11T20:39:49Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203947-net-get.json](evidence/2026-11-11-203947-net-get.json) | 2026-11-11T20:39:47Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-203943-net-list.json](evidence/2026-11-11-203943-net-list.json) | 2026-11-11T20:39:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203941-component-get.json](evidence/2026-11-11-203941-component-get.json) | 2026-11-11T20:39:41Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-203935-schematic-export.json](evidence/2026-11-11-203935-schematic-export.json) | 2026-11-11T20:39:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203929-schematic-export.json](evidence/2026-11-11-203929-schematic-export.json) | 2026-11-11T20:39:29Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-11-203922-schematic-check.json](evidence/2026-11-11-203922-schematic-check.json) | 2026-11-11T20:39:22Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203918-schematic-get.json](evidence/2026-11-11-203918-schematic-get.json) | 2026-11-11T20:39:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203825-session-close.json](evidence/2026-11-11-203825-session-close.json) | 2026-11-11T20:38:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203812-bom-export.json](evidence/2026-11-11-203812-bom-export.json) | 2026-11-11T20:38:12Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203759-bom-check.json](evidence/2026-11-11-203759-bom-check.json) | 2026-11-11T20:37:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203746-net-get.json](evidence/2026-11-11-203746-net-get.json) | 2026-11-11T20:37:46Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203734-net-list.json](evidence/2026-11-11-203734-net-list.json) | 2026-11-11T20:37:34Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203721-component-get.json](evidence/2026-11-11-203721-component-get.json) | 2026-11-11T20:37:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203708-component-list.json](evidence/2026-11-11-203708-component-list.json) | 2026-11-11T20:37:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203656-doc-create.json](evidence/2026-11-11-203656-doc-create.json) | 2026-11-11T20:36:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203643-session-get.json](evidence/2026-11-11-203643-session-get.json) | 2026-11-11T20:36:43Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203630-doc-get.json](evidence/2026-11-11-203630-doc-get.json) | 2026-11-11T20:36:30Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203618-schematic-create.json](evidence/2026-11-11-203618-schematic-create.json) | 2026-11-11T20:36:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203604-schematic-export.json](evidence/2026-11-11-203604-schematic-export.json) | 2026-11-11T20:36:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203551-schematic-check.json](evidence/2026-11-11-203551-schematic-check.json) | 2026-11-11T20:35:51Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203539-schematic-get.json](evidence/2026-11-11-203539-schematic-get.json) | 2026-11-11T20:35:39Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203340-schematic-create.json](evidence/2026-11-11-203340-schematic-create.json) | 2026-11-11T20:33:40Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203326-session-close.json](evidence/2026-11-11-203326-session-close.json) | 2026-11-11T20:33:26Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203324-schematic-export.json](evidence/2026-11-11-203324-schematic-export.json) | 2026-11-11T20:33:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203320-padstack-list.json](evidence/2026-11-11-203320-padstack-list.json) | 2026-11-11T20:33:20Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203319-part-get.json](evidence/2026-11-11-203319-part-get.json) | 2026-11-11T20:33:19Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203317-part-list.json](evidence/2026-11-11-203317-part-list.json) | 2026-11-11T20:33:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203315-project-get.json](evidence/2026-11-11-203315-project-get.json) | 2026-11-11T20:33:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203314-bom-export.json](evidence/2026-11-11-203314-bom-export.json) | 2026-11-11T20:33:14Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203311-bom-check.json](evidence/2026-11-11-203311-bom-check.json) | 2026-11-11T20:33:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203308-net-get.json](evidence/2026-11-11-203308-net-get.json) | 2026-11-11T20:33:08Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203306-net-list.json](evidence/2026-11-11-203306-net-list.json) | 2026-11-11T20:33:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203304-component-get.json](evidence/2026-11-11-203304-component-get.json) | 2026-11-11T20:33:04Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203301-component-list.json](evidence/2026-11-11-203301-component-list.json) | 2026-11-11T20:33:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203259-doc-list.json](evidence/2026-11-11-203259-doc-list.json) | 2026-11-11T20:32:59Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203258-doc-get.json](evidence/2026-11-11-203258-doc-get.json) | 2026-11-11T20:32:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203256-schematic-check.json](evidence/2026-11-11-203256-schematic-check.json) | 2026-11-11T20:32:56Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203252-schematic-get.json](evidence/2026-11-11-203252-schematic-get.json) | 2026-11-11T20:32:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203250-schematic-create.json](evidence/2026-11-11-203250-schematic-create.json) | 2026-11-11T20:32:50Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203237-schematic-create.json](evidence/2026-11-11-203237-schematic-create.json) | 2026-11-11T20:32:37Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203221-schematic-create.json](evidence/2026-11-11-203221-schematic-create.json) | 2026-11-11T20:32:21Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-203217-pcb-create.json](evidence/2026-11-11-203217-pcb-create.json) | 2026-11-11T20:32:17Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203215-packager-run.json](evidence/2026-11-11-203215-packager-run.json) | 2026-11-11T20:32:15Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203213-padstack-create.json](evidence/2026-11-11-203213-padstack-create.json) | 2026-11-11T20:32:13Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203211-part-create.json](evidence/2026-11-11-203211-part-create.json) | 2026-11-11T20:32:11Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203209-doc-list.json](evidence/2026-11-11-203209-doc-list.json) | 2026-11-11T20:32:09Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-203207-session-get.json](evidence/2026-11-11-203207-session-get.json) | 2026-11-11T20:32:07Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-203205-session-open.json](evidence/2026-11-11-203205-session-open.json) | 2026-11-11T20:32:05Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203201-session-open.json](evidence/2026-11-11-203201-session-open.json) | 2026-11-11T20:32:01Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203118-session-open.json](evidence/2026-11-11-203118-session-open.json) | 2026-11-11T20:31:18Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203039-session-open.json](evidence/2026-11-11-203039-session-open.json) | 2026-11-11T20:30:39Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-203035-padstack-create.json](evidence/2026-11-11-203035-padstack-create.json) | 2026-11-11T20:30:35Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203025-padstack-create.json](evidence/2026-11-11-203025-padstack-create.json) | 2026-11-11T20:30:25Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203021-padstack-create.json](evidence/2026-11-11-203021-padstack-create.json) | 2026-11-11T20:30:21Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-203016-padstack-create.json](evidence/2026-11-11-203016-padstack-create.json) | 2026-11-11T20:30:16Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-203013-padstack-list.json](evidence/2026-11-11-203013-padstack-list.json) | 2026-11-11T20:30:13Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-11-203011-part-get.json](evidence/2026-11-11-203011-part-get.json) | 2026-11-11T20:30:11Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-203008-part-list.json](evidence/2026-11-11-203008-part-list.json) | 2026-11-11T20:30:08Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-11-203006-part-create.json](evidence/2026-11-11-203006-part-create.json) | 2026-11-11T20:30:06Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-202958-part-create.json](evidence/2026-11-11-202958-part-create.json) | 2026-11-11T20:29:58Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-202952-part-create.json](evidence/2026-11-11-202952-part-create.json) | 2026-11-11T20:29:52Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-202945-part-create.json](evidence/2026-11-11-202945-part-create.json) | 2026-11-11T20:29:45Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-202940-project-get.json](evidence/2026-11-11-202940-project-get.json) | 2026-11-11T20:29:40Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-202938-session-close.json](evidence/2026-11-11-202938-session-close.json) | 2026-11-11T20:29:38Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-202925-session-close.json](evidence/2026-11-11-202925-session-close.json) | 2026-11-11T20:29:25Z | Xpedition 2604 | windows | 2 次运行中 2 次通过 |
| [2026-11-11-202923-doc-create.json](evidence/2026-11-11-202923-doc-create.json) | 2026-11-11T20:29:23Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-202755-session-list.json](evidence/2026-11-11-202755-session-list.json) | 2026-11-11T20:27:55Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-202751-doc-get.json](evidence/2026-11-11-202751-doc-get.json) | 2026-11-11T20:27:51Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-202749-bom-export.json](evidence/2026-11-11-202749-bom-export.json) | 2026-11-11T20:27:49Z | Xpedition 2604 | windows | 4 次运行中 4 次通过 |
| [2026-11-11-202743-bom-check.json](evidence/2026-11-11-202743-bom-check.json) | 2026-11-11T20:27:43Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-202741-net-list.json](evidence/2026-11-11-202741-net-list.json) | 2026-11-11T20:27:41Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-202738-component-list.json](evidence/2026-11-11-202738-component-list.json) | 2026-11-11T20:27:38Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-202736-schematic-check.json](evidence/2026-11-11-202736-schematic-check.json) | 2026-11-11T20:27:36Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-202732-schematic-get.json](evidence/2026-11-11-202732-schematic-get.json) | 2026-11-11T20:27:32Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
| [2026-11-11-202729-doc-create.json](evidence/2026-11-11-202729-doc-create.json) | 2026-11-11T20:27:29Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-202727-doc-create.json](evidence/2026-11-11-202727-doc-create.json) | 2026-11-11T20:27:27Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-202724-doc-create.json](evidence/2026-11-11-202724-doc-create.json) | 2026-11-11T20:27:24Z | Xpedition 2604 | windows | 1 次运行中 1 次通过 |
| [2026-11-11-202607-doc-create.json](evidence/2026-11-11-202607-doc-create.json) | 2026-11-11T20:26:07Z | Xpedition 2604 | windows | 3 次运行中 3 次通过 |
<!-- driver:evidence:end -->
