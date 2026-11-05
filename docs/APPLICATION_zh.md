# Xpedition 能力调查

[English](APPLICATION.md) | 中文

本调查记录 Xpedition 能做什么、能怎样自动化。它在设计命令之前填写：xpedition-cli 的每个领域命令都写明它实现的能力编号，每项能力都有处置。

出处缩写：
- 〔实测〕xpedition-cli 1.0.3 在 Xpedition Standard XPED2604（Windows 11）上的实测记录，2026年9月12日至30日，见 npm 包 `@fateforge/xpedition-cli@1.0.3` 里的 `docs/E2E.md`、`docs/COMPATIBILITY.md` 和 `docs/NATIVE_ADAPTER.md`。
- 〔旧码〕本仓库 main 分支上的旧实现（提交 4a054a3）及其 CHANGELOG。其中只有关闭诊断做过一次真机运行，其余是代码注释和私有运行的转述，可信度低于〔实测〕。
- 〔本机〕本机安装的 Xpedition Enterprise 2604（`XENTP2604`，`COM_VERSION 151`）的注册表、安装目录和 `SDD_HOME/standard/automation/Commands_*.csv` 命令目录。
- 本机安装没有附带自动化参考手册（`docs/` 下只有 `legal`），所以下面没有引用厂商手册。

## 软件

- 名称：Xpedition（Designer 画原理图，Layout 画板，Library Manager 管中心库）
- 厂商：Siemens EDA（原 Mentor Graphics）
- 调查过的版本：Xpedition Standard XPED2604〔实测〕、Xpedition Enterprise XENTP2604〔本机〕；xpedition-cli 支持 2604

| 操作系统 | CPU 架构 |
|---|---|
| Windows | x64 |

出处：〔本机〕注册表 `HKLM\Software\Mentor Graphics\Releases\MGC_1` 写着 `SDD_PLATFORM win64`，安装目录只有 `win64`。自动化走 Windows COM（`Viewdraw.Application`、`MGCPCB.ExpeditionPCBApplication`），只在 Windows 上有。厂商没有 Windows arm64 版。待确认：Xpedition 是否还有 Linux 版。就算有，Linux 上也没有这套 COM 接口，本工具一样用不了。

## 自动化接口

| 接口 | 从哪里调用 | 说明 |
|---|---|---|
| Designer COM `Viewdraw.Application`（另有带版本号的 `.151`） | CLI（进程外 COM，pywin32） | 只能用 `GetActiveObject` 连接已经运行的实例。Application 对象没有 PID 和窗口句柄，ROT 里无法挑选实例〔旧码〕。一部分成员只能按 dispid 调用〔实测〕〔旧码〕 |
| Layout COM `MGCPCB.ExpeditionPCBApplication` / `MGCPCB.Application`（`.151`、`.60`） | CLI（进程外 COM） | 每个文档都要先换自动化许可令牌：`Validate(0)` → `MGCPCBAutomationLicensing.Application.GetToken` → `Validate(token)`〔实测〕。带参数的属性要用 dispid 加 `DISPATCH_PROPERTYGET` 读〔实测〕 |
| 启动器 `SDD_HOME/common/win64/bin/{viewdraw,ExpeditionPCB,LibraryManager}.exe` | CLI（启动进程） | 直接 COM 激活或直接运行 `wg/wv/win64/bin` 下的真程序都会失败（`CO_E_SERVER_EXEC_FAILURE`、`STATUS_DLL_NOT_FOUND`）。启动器 PID 不是工作进程 PID〔实测〕〔旧码〕 |
| Constraint Manager COM `ConstraintsAuto`（`.151`） | CLI | 只能晚绑定。要先设好 `SDD_HOME` 和 Layout 的 PATH。改完要在 Layout 里调 `ProjectIntegration.SynchCES` 才生效〔实测〕 |
| Library Manager COM `LibraryManager.Application` | CLI | 只读，没有新建符号、封装、元件的方法〔实测〕 |
| 库转换程序 `PartsDB2HKP`、`CellDB2HKP`、`PadstackDB2HKP`（导出）和 `HKP2PartsDB`、`HKP2CellDB`、`HKP2PadstackDB`（导入） | CLI（命令行，不需要运行中的实例） | 是 GUI 程序：参数错或许可问题会弹消息框，不返回退出码。日志里有中文。`LMCDB2HKP` 会卡住，不用〔实测〕 |
| `sch2pdf.exe` | CLI（命令行） | 直接从工程数据库出 PDF，Designer 开着工程也能用〔实测〕 |
| `package.exe` | CLI（命令行） | 无界面打包，结果读 `Integration/PartPkg.log`。必须经 `common/win64/bin` 启动〔实测〕 |
| `JobWizard.exe -createnew` | CLI（命令行） | 从版图模板建板，不需要 Designer〔实测〕 |
| 菜单命令 `Gui.ProcessCommand(id 或名称)`（Layout）、`ExecuteCommandByID`（Designer） | CLI（经 COM） | 命令目录在 `Commands_XpeditionLayout.csv`（1041 条）、`Commands_Xpedition_Designer.csv`〔本机〕。Batch DRC、Gerber、ODB++、NC 钻孔都只能这样走，而且会弹对话框〔实测〕 |
| 文件格式：`.prj`、`SymbolLibs/*/sym/<名>.<版本>` 符号文本、HKP 文本、`Config/*.ocf/.gpf/.dcs` 设置文件 | CLI（读写文件） | 符号文件 `V 53` 用图纸单位，`V 54` 用 10 nm〔实测〕 |
| `LayoutDB2HKP`/`HKP2LayoutDB`、`NetClassDB2HKP`/`HKP2NetClassDB` | CLI（命令行） | 本机有〔本机〕，从未试过 |
| Designer 启动脚本（`scripts.ini` 里的 VBS，在 Designer 进程内运行） | 只能在进程内 | 旧码用它把 Designer 自己的 Application 对象交出来，以确认实例身份〔旧码〕。COM 能用不依赖它 |

## 对象模型

- 工程 Project（`.prj`）：一个工程有若干 Design（原理图块 `Board1`，板设计 `ConfigType "PCB"`）、一个中心库 Central Library（`.lmc`），以及分区列表 `LIST Symbols`、`LIST PDBs`、`LIST 2dCellLibraries`。
- 中心库：
  - Part（元件，Parts DB `.pdb`）：料号、描述、Value、属性、符号、管脚映射。
  - Symbol（符号）：文本文件。
  - Cell（封装，Cell DB `.cel`）。
  - Padstack（焊盘栈，`PadstackDB.psk`）、Pad、Hole。
  - 以上都按 Partition（分区）存放。
- 原理图（Designer）：Schematic → Sheet（页）→ Component（元件实例，以位号如 `R1` 标识；放置用 `AddPartInstance`，在页上画成 symbol）、Net（网络，有名字或 UID `$2N121`）、Pin（管脚，如 `R1.1`）、Attribute（属性）、Label（网络标签）、Wire（导线）。
- 板（Layout，`PCB/<名>.pcb`）：
  - Board Outline（板框）、Route Border（布线边界）、Manufacturing Outline（制造边框）。
  - Component（元件，位号）及其 Pin、Pad。
  - Net、Net Class（网络类）、Trace（走线）、Via（过孔）、Plane Shape（铜皮）、Mounting Hole（安装孔）、Obstruct（禁布区）。
  - Fabrication Layer Text（丝印/装配层文字，位号）、Hazard（DRC 结果）、Display Scheme（显示方案）、Layer Stack（叠层）。
- 生产输出：ODB++ 作业、Gerber（RS-274X）、NC Drill（Excellon），放在板文件夹的 `Output/` 下。

## 单位与坐标

- 原理图：图纸单位 = 10 mil = 0.254 mm，100 mil 栅格 = 10 单位。`V 54` 符号文件里一格是 254000（10 nm）。原点在图框左下角，y 向上。元件朝向 0–3 = 逆时针 0°/90°/180°/270°，4–7 是镜像〔实测〕。
- 板：COM 方法大多带单位参数 `EPcbUnit`（0 当前、2 mil、3 inch、4 mm、5 µm），本工具一律传 4（mm）。不带单位的成员（`PointsArray`、`LineWidth`、文字尺寸）按文档当前单位返回，默认模板是 mil（`CurrentUnit 2`）。走线宽 `Geometry.LineWidth` 是 th（千分之一英寸）。默认 4 层模板的原点在板左下角，x 向右，y 向上。角度用度〔实测〕〔旧码〕。
- Constraint Manager 的线宽值是 th（0.5 mm = 19.685）〔实测〕。
- HKP：封装、焊盘栈用 `.UNITS MM`，元件用 `th`（元件没有几何）〔旧码〕。
- 弧：`PointsArray` 每行 (x, y, r)，r ≠ 0 的行是弧心，正值顺时针，负值逆时针〔实测〕。

## 线程与阻塞

- 进程外 COM 调用会排进程序主线程。弹出模态框时调用不返回，所以旧码用辅助线程靠 UI Automation（pywinauto）读框、按钮〔实测〕。
- 已知弹框，取自〔实测〕〔旧码〕；中文界面下标题是中文：
  - Layout 打开板时：确定设计状态超过 15 秒（打开/取消）、数据库恢复（确定/取消）、"新更改已经可以进行正向标注"（是/否）。打开另一块板而当前板有改动时会弹 Save All 模态框。
  - Designer 换工程时：更改当前项目需要关闭所有打开的文档（是/否）。超时后 Designer 会弹"close all open documents?"。
  - 输出与检查：批量 DRC、NC 钻孔生成、ODB++ 设计输出、Gerber 输出对话框，以及 Processing… 进度窗。
  - Library Services（库服务）窗口；保存提示（四个按钮：全部保存、只存库、不保存、取消）。
  - 许可与登录：Siemens 许可登录框，只能由人完成。
- 不能对进程里每个 `#32770` 都按默认按钮：那样会把打包进度框关掉，导致正向标注失败〔实测〕。
- 桌面锁屏时窗口截图全黑；UI Automation 仍能用〔实测〕。
- 忙的表现：COM 调用被拒（`RPC_E_CALL_REJECTED` 一类）、出现 Processing 窗口。程序是否另有"忙"的标志：待确认。

## 读回

- 原理图：元件位置、属性、网络成员都能经 COM 读回，按图纸单位精确比较。整张画完后要关闭再打开工程，才能读到正确的设计和网表：页操作后 `GetActiveDesign` 会返回原理图而不是块，`DesignComponents` 会报类型不符〔实测〕。没有位号的符号（电源、接地、无连接标记）读不到符号名，只能按几何判断〔实测〕。
- 板：元件位置用 `GetPositionX/Y(4)` 按 mm 读回，与计划完全相等〔实测〕。板框、安装孔、铜皮、走线、过孔、位号文字的位置都能读回。槽孔没有钻孔尺寸，按长度读〔实测〕。线宽规则在 Layout 里只读（`NetClass.*TraceWidth`）〔实测〕。
- 中心库：重新用转换程序导出后按内容比较（忽略时间戳和交换组名）。Value 按数值比（`10k` 存为 `10K`）。文字按系统代码页比〔实测〕。
- 生产文件：读文件本身（Gerber 头、Excellon、ODB++ 目录），记录大小和 SHA-256〔实测〕。
- PDF：读 `sch2pdf` 输出的 `Printed … Sheet n` 和页面尺寸〔实测〕。

## 保存与撤销

- Designer：`Documents.SaveAll`（`ActiveDocument.Save` 只存当前页），`CloseProject()` 要返回 True〔实测〕〔旧码〕。Designer 的 iCDB 服务开着工程时锁住 `database/icdb.dat`，备份前要先关工程〔实测〕。另存为：有 Save As 命令（57604），从未用过。
- Layout：`Document.Save()` 返回 True 才算存了，`Close(True)` 后 `ActiveDocument` 应为 None〔旧码〕。另存：只有 `FILE_SAVE_COPY`，会打开 JobWizard 的复制对话框〔本机〕。
- 未保存状态：两边的 COM 是否暴露"有未保存修改"：待确认，旧码从没读过。Designer 的 Exit 命令说明写着"有未决修改时提示保存"，说明程序内部知道〔本机〕。
- 撤销：两边都有菜单命令 Undo（57643），Layout 说明写着可撤销到上次保存为止〔本机〕。对自动化做的修改是否有效：待确认。Layout 有 `TransactionStart`/`TransactionEnd`，`TransactionEnd(False)` 能否回滚：待确认〔旧码〕。
- 中心库、`.prj`、`sch2pdf`、`package.exe`、JobWizard 都直接写文件，写了就是存了。

## 实例与并发

- Layout：用 `Gui.HWND` → `GetWindowThreadProcessId` 拿到 PID，用 `ActiveDocument.FullName` 拿到板文件〔旧码〕。同时开多个 Layout 时，`GetActiveObject` 只返回其中一个，不能指定〔旧码〕。
- Designer：Application 没有 PID 和窗口。只有页面窗口打开时，能从视图拿到 PID；否则只能在本机恰好一个 Designer 工作进程时，认定连上的就是它〔旧码〕。所以两个 Designer 同时开时无法可靠驱动指定的那个。
- 人同时在软件里操作：程序没有修改计数，工具看不到人改了什么，只能靠文件时间和工具自己的修改记录推断（风险见下）。
- Designer 和 Layout 共用一个工程：Layout 打开板时如果 Designer 关闭工程，会弄坏 Layout 的数据库会话（"iCDB error getting UID manager"）〔实测〕。

## 许可

- 许可来源：`MGLS_LICENSE_FILE`、`SALT_LICENSE_SERVER`、`LM_LICENSE_FILE` 环境变量（进程里没有时从 HKCU、HKLM 继承）〔旧码〕。
- Layout 自动化每个文档都要换令牌（见上）。漏掉这一步时，错误的 EXCEPINFO 是产品码 10279、scode `0x8004022D`，消息是本地化中文，只靠文字匹配不可靠〔旧码〕。
- Designer 启动时可能停在 Siemens 许可登录框，只能由人完成〔实测〕。
- 自动化需要哪种许可特性（feature）：待确认，问用户。
- 许可信息（服务器地址、许可文件）不进仓库，也不出现在输出里。

## 能力

每项能力占一行，带一个在本调查里唯一的编号 `CAP-nnn` 和一个处置：`provided`（有领域命令提供）、`deferred`（值得提供，留到以后的版本）、`excluded`（不会提供，比如不安全或无法可靠地自动化）或 `unknown`（还不知道 Xpedition 是否允许）。每行写明能用到这项能力的接口；处置不是 `provided` 的行，用一句话写明原因。

| 编号 | 能力 | 接口 | 处置 | 原因 |
|---|---|---|---|---|
| CAP-001 | 读工程文件：设计、板路径、中心库、分区 | `.prj` 文本 | provided | - |
| CAP-002 | 从模板工程复制新工程，改写库路径 | 文件 | provided | - |
| CAP-003 | 用 Designer `NewProject` 从安装自带模板初始化工程 | Designer COM | unknown | 只在旧码里写过，从未真机通过 |
| CAP-004 | 把工程文件夹打包成 ZIP 备份 | 文件 | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-005 | 从 ZIP 备份恢复工程文件夹 | 文件 | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-010 | 列出中心库里的元件 | `PartsDB2HKP` | provided | - |
| CAP-011 | 读一个元件的全部：管脚映射、符号及其管脚、封装焊盘及焊盘栈、问题 | 转换程序 + 符号文件 | provided | - |
| CAP-012 | 列出中心库里的封装 | `CellDB2HKP` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-013 | 读一个封装及使用它的元件 | `CellDB2HKP`、`PadstackDB2HKP` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-014 | 检查中心库一致性（13 条规则） | 转换程序 + 符号文件 | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-015 | 从元件文件新建元件：符号、IPC-7351B 封装、焊盘栈、管脚映射 | `HKP2*DB` + 符号文件 + `.prj` | provided | - |
| CAP-016 | 按设计描述给缺的元件生成占位元件 | 同上 | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-017 | 从另一个 Xpedition 中心库导入元件（连同符号、封装、焊盘栈） | 转换程序 | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-018 | 画元件预览图（符号在左，封装在右） | 纯 Python | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-019 | 用 Library Manager COM 新建或修改库对象 | Library Manager COM | excluded | 它的对象模型只读 |
| CAP-020 | 导入 KiCad 封装库 | 转换程序 | excluded | 1.0.3 已移除，库不依赖 KiCad |
| CAP-021 | 新建分区并登记到 `.lmc` | 转换程序 / Partition Editor | unknown | 旧码前后矛盾，没有真机证据 |
| CAP-022 | 用 PCB Footprint Expert 生成封装 | `lm_fpe` | excluded | GUI 工具，没有自动化接口 |
| CAP-030 | 读原理图：页、元件数、网络数 | Designer COM | provided | - |
| CAP-031 | 读原理图元件：位号、料号、值、页、属性、每个管脚及其网络 | Designer COM `DesignComponents` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-032 | 读原理图网络及其管脚 | Designer COM | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-033 | 按设计描述规划原理图，画预览 PNG（不用 Designer） | 纯 Python | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-034 | 按设计描述画整个原理图：清空各页、放元件、连线、标签、标题，保存后重开读回网表比对 | Designer COM | provided | - |
| CAP-035 | 放置元件 | `AddPartInstance` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-036 | 移动元件 | `SetLocation` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-037 | 删除元件，连同只属于它的导线 | `DeleteSelected(True)` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-038 | 设置元件属性及其显示 | `FindAttribute`、`AddAttribute` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-039 | 连接、断开管脚，新建网络 | `AddNet`、标签 | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-040 | 重命名网络（各页所有标签一起改） | 标签 `TextString` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-041 | 运行 Designer 校验（Full/Quick Verification），解析 `vdrc.log`、`grc.log` | `ExecuteCommandByID(34155/46461)` | provided | - |
| CAP-042 | 原理图导出 PDF | `sch2pdf.exe` | provided | - |
| CAP-043 | 打包原理图供 Layout 使用 | `package.exe` | provided | - |
| CAP-044 | BOM 检查：缺料号、重复位号、同料号不同值 | 原理图读数 | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-045 | 导出 BOM（可分组、可与基线比较） | 原理图读数 | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-046 | 激活某页、适配窗口、截 Designer 窗口图 | Designer COM + GDI | deferred | 只给人看；锁屏时截图全黑；PDF 和预览图已能看图。以后再考虑 |
| CAP-047 | 反标（Back Annotation） | `ProjectIntegration.BackAnnotate` | unknown | 从未试过 |
| CAP-060 | 用 JobWizard 从版图模板建板 | `JobWizard.exe` | provided | - |
| CAP-061 | 删除重建板：先把板文件夹打包备份 | 文件 + JobWizard | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-062 | 正向标注：把打包后的原理图带进板 | `ProjectIntegration.ForwardAnnotate` | provided | - |
| CAP-063 | 读板：计数、叠层、网络类及线宽、禁布区、各网络是否布通 | Layout COM | provided | - |
| CAP-064 | 读板几何：元件外形、管脚、焊盘、走线、过孔、铜皮、孔、丝印 | Layout COM | provided | - |
| CAP-065 | 设置矩形板框（可圆角），同时换布线边界和制造边框 | `PutBoardOutline` 等 | provided | - |
| CAP-066 | 在四角放非金属化安装孔，删除安装孔 | `PutMountingHoleEx` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-067 | 新建网络类、分配网络、设置线宽 | `ConstraintsAuto` + `SynchCES` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-068 | 按连接关系自动布局（按页分簇、区域标签） | `Component.Place` + 规划器 | provided | - |
| CAP-069 | 把元件移到指定位置和角度（顶面） | `Component.Place/Move` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-070 | 把元件放到底面 | `Place(bTop=False)` | unknown | 从未试过；`Side` 只读 |
| CAP-071 | 把丝印位号挪到空位，或挪到指定位置 | `FabricationLayerText.Move` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-072 | 铺铜（平面形状） | `PutPlaneShape`、`PlaneAssignment` | provided | - |
| CAP-073 | 自动布线：指定网络、层和布线遍 | `NewRoutePass` | provided | - |
| CAP-074 | 按计划画走线 | `PutTrace` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-075 | 放过孔，包括给地网络打缝合过孔 | `PutVia`、`PutPadstack` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-076 | 删除走线和过孔（整网，或某一点上的） | `Delete()` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-077 | Batch DRC，读出 hazards | `Gui.ProcessCommand(32769)` + 对话框 + `GetHazards` | provided | - |
| CAP-078 | 布局布线指标与板级规则（飞线长、交叉、去耦电容到电源脚距离） | 纯 Python | provided | - |
| CAP-079 | 按几何画板图 PNG（顶面、底面） | 纯 Python | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-080 | 切显示方案、适配板、截 Layout 窗口图 | `DisplayControl.LoadScheme` + GDI | deferred | 只给人看；锁屏时截图全黑；`pcb render` 已能看图。以后再考虑 |
| CAP-081 | 生产文件：ODB++、Gerber、NC 钻孔，加坐标文件、BOM、说明和清单 | `Gui.ProcessCommand(33016–33018)` + 设置文件 + 对话框 | provided | - |
| CAP-082 | 刷新板上的封装（Library Services 本地库更新） | `SETUP_LIBRARYSERVICES`、`ECO_UPDATELOCALLIBRARYDATA` | unknown | 只在旧码里写过；需要可交互桌面 |
| CAP-083 | 设置间距规则 | `ConstraintsAuto` | unknown | 从未试过 |
| CAP-084 | 3D 视图与 STEP 导出 | `EXP3D_EXPORT` 53325 | unknown | 从未试过 |
| CAP-085 | IPC-D-356 网表导出 | `FILE_EXPORT_IPCD356B` 32938 | unknown | 从未试过 |
| CAP-086 | 用 Silkscreen Generator 生成丝印 | `OUTPUT_SILKSCREEN` 33019 | excluded | 每个封装组的层勾选框不暴露给 UI Automation，只能盲点 |
| CAP-087 | 板数据库与文本互转（不开 Layout 读板） | `LayoutDB2HKP`/`HKP2LayoutDB` | unknown | 本机有，从未试过 |
| CAP-088 | 删除铜皮 | `Delete()` | deferred | 第一版只收做板必经的步骤，以后的版本提供 |
| CAP-100 | 列出运行中的 Designer、Layout 实例及其打开的工程、板 | 进程枚举 + COM | provided | 由标准命令 `session`、`doc` 提供，没有领域命令（规范只为领域命令定义了 provided，见 SPEC 问题记录） |
| CAP-101 | 经启动器启动 Designer 或 Layout | 启动器 | provided | 由标准命令 `session`、`doc` 提供，没有领域命令（规范只为领域命令定义了 provided，见 SPEC 问题记录） |
| CAP-102 | 关闭实例 | `Quit` | provided | 由标准命令 `session`、`doc` 提供，没有领域命令（规范只为领域命令定义了 provided，见 SPEC 问题记录） |
| CAP-103 | 打开工程（Designer）或板（Layout） | `OpenProject`、`OpenDocument` | provided | 由标准命令 `session`、`doc` 提供，没有领域命令（规范只为领域命令定义了 provided，见 SPEC 问题记录） |
| CAP-104 | 保存 | `Documents.SaveAll`、`Document.Save` | provided | 由标准命令 `session`、`doc` 提供，没有领域命令（规范只为领域命令定义了 provided，见 SPEC 问题记录） |
| CAP-105 | 关闭工程或板 | `CloseProject`、`Document.Close` | provided | 由标准命令 `session`、`doc` 提供，没有领域命令（规范只为领域命令定义了 provided，见 SPEC 问题记录） |
| CAP-106 | 撤销 | Undo 命令 57643 | unknown | 对自动化修改是否有效未知 |
| CAP-107 | 另存为 | Designer Save As；Layout 只有 JobWizard 复制 | unknown | 从未试过 |

## 风险

调查中发现的每项风险或限制，都写成 `knowledge/` 里的条目，或在这里写明不收的原因。

- 安装不带元件库：模板库是空骨架，放 `Resistors:R` 会报"not found, empty or a block"。→ `xpedition-template-library-is-empty`
- COM 激活必须经启动器，直接激活报 `CO_E_SERVER_EXEC_FAILURE`。→ `xpedition-start-through-launcher`
- win32com 缓存被清理工具删坏后，连接报 `CLSIDToClassMap`。→ `xpedition-gen-py-cache-broken`
- 工程或中心库放在非 ASCII 路径下，新写的符号放不上去。→ `xpedition-ascii-paths-only`
- 许可登录框、许可令牌失败，产品码 10279。→ `xpedition-license-sign-in`
- Layout 打开板时的三个提问（设计状态、数据库恢复、是否正向标注），以及 Save All 模态框。→ `layout-open-prompts`
- Designer 换工程时的提问，以及超时后"close all open documents?"。→ `designer-project-prompts`
- 不能按下进程里所有对话框的默认按钮，否则正向标注在打包阶段失败。→ 写进适配层代码，不做条目：这是实现规则，Agent 用不到。
- 两个 Designer 同时开时无法确定连的是哪个；Designer 没有 PID。→ `designer-one-instance-only`
- 页操作后要关闭再打开工程才能读对设计。→ `designer-reopen-after-sheet-changes`
- 符号缓存：同名符号改过后 Designer 不认，要换名字或升版本。→ `designer-symbol-cache`
- 电源、接地符号必须是类型 4（`Y 4`），否则 `NETNAME` 不起作用。→ `designer-power-symbols`
- 管脚落在导线拐角上不会连上。→ `designer-pin-on-wire-corner`
- 中文写进工程按系统代码页存，`sch2pdf` 出的 PDF 里是乱码。→ `designer-chinese-text-in-pdf`
- `drc-BI-POWER/GROUND` 校验规则，以及管脚类型 `ANALOG` 的用法。→ `designer-pin-types-and-erc`
- 封装管脚数必须等于元件管脚数，否则 Database Load 终止。→ `cell-pin-count-must-match`
- `.prj` 缺 `LIST 2dCellLibraries`，正向标注找不到封装。→ `prj-cell-library-list`
- 正向标注不会换掉板上已有元件的封装，只能重建板。→ `forward-annotation-keeps-old-cells`
- 已同步时再要求正向标注会报 1001。→ `forward-annotation-in-synch`
- Layout 的布线规则在 Layout 自身接口里只读，线宽要走 Constraint Manager，之后还要 `SynchCES`。→ `layout-trace-widths-through-cm`
- 默认 0.254 mm 间距下，0.65 mm 间距的细脚封装布不通，而且布线器不报原因。→ `layout-fine-pitch-clearance`
- 外层铜皮要在布线后再铺，否则地脚被割断。→ `layout-pour-after-routing`
- 铜皮建好后处于 Draft 状态，要切到 Dynamic 才生成铜。→ `layout-plane-draft-state`
- 在线 DRC 拒绝把元件放到别的元件上或它自己的旧位置上，要先抬起。→ `layout-placement-drc`
- 未放置的元件没有管脚、不显示；默认显示方案看不到顶面元件。→ `layout-unplaced-parts-invisible`
- 过孔离焊盘近于间距会被拒，同网也一样；测试点是表贴焊盘。→ `layout-via-near-pad`
- 生产输出对话框会把内存里的设置写回文件，改设置要在板关闭时改；ODB++ 默认不含钻孔层和板框。→ `layout-output-setups`
- NC 钻孔跑完后 Layout 一直占着 `DrillPrefs.txt`，板文件夹删不掉。→ `layout-drillprefs-held`
- Enterprise 2604 打开板后会留着 PartsDB 临时占用，开关一次 Library Services 才释放。→ `layout-library-services-reservation`（待真机确认后再写）
- 底面放置从未试过。→ 不做条目：能力表里写 unknown，不提供命令。
- 中心库转换程序：封装名超过 64 字符时整个文件不保存；`HKP2PartsDB` 不加 `-r` 时同料号会被悄悄丢弃；日志里的"未找到文件格式错误"不是错误。→ `library-converter-rules`
- 元件 Value 只收数值加 SI 倍率，其他文字存成 0。→ `library-part-value-is-numeric`
- 分区名只能用 ASCII 字母、数字、空格和 `_ . -`。→ 合并进 `xpedition-ascii-paths-only`
- 人同时在软件里改：工具看不到程序内部的修改计数，保存预览里的"外部修改"只能按文件时间推断。→ `xpedition-external-edits-not-tracked`
- 只在一台机器、一个版本（2604）上实测过，换版本可能不同。→ 不做条目：写进 `docs/COMPATIBILITY.md` 的支持矩阵。
