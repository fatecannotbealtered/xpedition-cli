# 真实软件验收

[English](E2E.md) | 中文

真实软件运行用真实的适配层，在真实的 Xpedition 上执行 xpedition-cli 的行为矩阵。它会做出真实的修改，所以只使用人在下面授权过的目标。

## 授权目标

真实软件运行可以使用的 Xpedition 实例、文件、文件夹和设备，唯一的清单在英文版 `docs/E2E.md` 的「Authorized Targets」一节；检查脚本和矩阵运行器只读那一份，本节不另列目标。只有人能往清单里加目标，编程 Agent 从不添加。每个目标占一个列表项，以反引号括起的目标开头，写法与授权范围文件里的目标相同：文件或文件夹的 `file://` URL，或者设备的 `device:<id>`；后面跟一个冒号和对目标的说明。Xpedition 的实例没有固定的 ID，所以真实软件运行只使用人为这次运行启动的实例，打开的文档也只来自这里列出的文件和文件夹。真实软件运行不使用任何其他目标，真实软件运行所用的授权范围文件也只授予这里列出的目标。

清单以 `docs/E2E.md` 为准。

## 步骤

真实软件运行使用这些电脑：

- 一台 Windows 11 x64 电脑，装 Xpedition Enterprise 2604，用 file 和 session 两种模式。练习工程和评测文件夹由人列进「授权目标」。

在真实的 Xpedition 上运行行为矩阵：

1. 由人准备目标，比如专为测试复制的项目、为这次运行启动的实例，或为测试准备好的设备，并把它们列进「授权目标」。
2. 由人写一个只授予这些目标的授权范围文件，包含被测命令需要的效果，并让 `XPEDITION_CLI_SCOPE_FILE` 指向它。把 `XPEDITION_CLI_STATE_DIR` 设为专供真实软件运行使用的目录。
3. 运行 `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>`，矩阵用到的每个占位符各给一个 `--set`，占位符名不带花括号，如 `--set doc=<file>`：`{session}` 是授权的实例，`{doc}` 是授权的文档或文件，`{device}` 是授权的设备，`{dir}` 是授权文件夹里新建的空目录。`{job}` 是作业 ID；不给时，运行器先运行 `--job-from` 指定的长命令（只有一个长命令时就是它）的 `normal` 行，用它启动的作业。`{inputs}` 不用 `--set`：它就是 `tests/inputs/`，里面的文件命令只读取，所以不需要授权。
4. 运行器执行适用的行，把真实软件记录写进 `docs/evidence/`，并填上每一行的实际结果和 `live:` 证据引用。实际结果与预期不符的行算作失败。
5. 在真实的 Xpedition 上无法按需制造的行，如 `disconnect`，可以标为 `unrunnable` 并写明原因；`normal`、`dialog` 和 `read_back_mismatch` 这三行不可以。`read_back_mismatch` 用 `--row read_back_mismatch` 运行：工具会在读回之前等一会儿，在这段时间里，到 Xpedition 里改掉它刚写的内容，或删掉它生成的文件。
6. 运行 `python scripts/spec.py generate` 更新支持矩阵和下面的索引，再把记录和矩阵一起提交。

要在真实的 Xpedition 上运行评测题，由人在「授权目标」里列出一个放评测工作目录的文件夹，以及题目的 `SCOPE` 用到的其他目标，比如设备。然后运行 `python scripts/spec.py eval --workspace-root <folder>`。运行器自己为每道题写授权范围文件，只授予这道题的文档和它的 `SCOPE` 列出的目标，并把评测报告写进 `docs/evidence/`。

真实软件记录只在它记录的工具版本与 xpedition-cli 的主版本和次版本都相同时才算作证据。记录里从不出现机密，带机密的参数显示为 `***`。

## 证据记录

`docs/evidence/` 里的真实软件记录，由 `python scripts/spec.py generate` 生成：

<!-- driver:evidence:start -->
还没有真实软件运行记录。
<!-- driver:evidence:end -->
