# 为 xpedition-cli 贡献代码

*[English](CONTRIBUTING.md) | 中文*

修改行为前先读 [AGENTS_zh.md](AGENTS_zh.md) 和固定版本的 `.agent/` 规范。本 CLI
优先面向 Agent：stdout 只输出一个 JSON envelope，错误遵守统一的 code/exit/retryable
映射，写操作使用 `--dry-run` 再配合单次 `--confirm` token。

## 开发环境

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check xpedition_cli tests
ruff format --check xpedition_cli tests
node scripts/check-version.js
node scripts/check-spec.js --local-only
python -m xpedition_cli.main --help
```

测试时使用 `XPEDITION_CLI_CONFIG_DIR` 隔离确认 secret 和审计目录。不要使用生产工程文件；
正版 E2E 只能执行 [`docs/E2E.md`](docs/E2E.md) 中的临时工程流程。

## 新增命令

1. 先读 `.agent/CLI-SPEC.md` 和 `.agent/SEC-SPEC.md` 的相关章节。
2. 在 `xpedition_cli/cli/registry.py` 里声明一次：路径、处理函数（`xpedition_cli/cli/`
   下的 `模块:函数`）、描述、输出 schema、examples、阶段、需要运行什么、权限等级、参数，
   写命令还要写爆炸半径和 dry-run schema。`reference`、`--help` 和参数解析都读这一条。
3. 在 `xpedition_cli/reference_data.py` 加输出 schema，功能代码放在 `xpedition_cli/` 下；
   凡是和 Xpedition 打交道的，都走 `xpedition_cli/native_com_adapter.py` 里的适配器方法。
4. 外部值必须在 `_untrusted` 中标记；所有写操作都先 `--dry-run`，再用单次 `--confirm <token>` 执行，
   会毁掉成果的还要加 `--dangerous`；写完要读回结果。
5. 为成功、非法输入、错误、envelope、退出码及 stdout/stderr 边界增加命令级测试（用
   `tests/fakes.py` 伪造适配器），保持 FCC guard 通过。
6. 同步修改两个 README、受影响的 Skill 和 `CHANGELOG.md`。

没有在授权安装上跑过并记录（`docs/E2E.md`），不要宣称某项能力或新的 Xpedition 版本可用。`.agent/*`、
`contract/contract.json` 和生成代码只能通过 `scripts/sync-spec.js` 保持同步，不要手改规范副本或生成文件。

## Pull Request

- 使用聚焦分支和 Conventional Commit。
- 每个可观察行为变化都要有测试和文档。
- 本地执行 lint、格式检查、测试、版本和 spec guard。
- 不要提交凭据、机密工程数据、构建产物或真实 token。

