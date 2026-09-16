# 为 xpedition-cli 贡献代码

*[English](CONTRIBUTING.md) | 中文*

修改行为前先读 [AGENTS.md](AGENTS.md) 和固定版本的 `.agent/` 规范。本 CLI
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

## 新增命令或后端

1. 先读 `.agent/CLI-SPEC.md` 和 `.agent/SEC-SPEC.md` 的相关章节。
2. 在 `xpedition_cli/` 下增加领域模型或后端操作。
3. 在 `xpedition_cli/reference_data.py` 注册命令、schema、examples、权限等级和爆炸半径。
4. 外部值必须在 `_untrusted` 中标记；所有写操作都走 ChangeSet 确认流程。
5. 为成功、非法输入、错误、envelope、退出码及 stdout/stderr 边界增加命令级测试，保持 FCC guard 通过。
6. 同步修改两个 README、Skill 和 `CHANGELOG.md`。

没有经过授权环境的记录，不要宣称 NativeBackend 或新的 Xpedition 版本可用。`.agent/*`、
`contract/contract.json` 和生成代码只能通过 `scripts/sync-spec.js` 保持同步，不要手改规范副本或生成文件。

## Pull Request

- 使用聚焦分支和 Conventional Commit。
- 每个可观察行为变化都要有测试和文档。
- 本地执行 lint、格式检查、测试、版本和 spec guard。
- 不要提交凭据、机密工程数据、构建产物或真实 token。

