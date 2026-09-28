from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _environment(config):
    return {**os.environ, "XPEDITION_CLI_CONFIG_DIR": str(config)}


def _run(args, config):
    return subprocess.run(
        [sys.executable, "-m", "xpedition_cli.main", *args],
        cwd=ROOT,
        env=_environment(config),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


LINK = "https://example.com/wiki/pcb-rules"


def _preview(config):
    """A local write (a knowledge-base binding) previewed; its args and token."""
    args = ["kb", "add", "--name", "pcb", "--url", LINK, "--about", "layout rules", "--compact"]
    result = _run([*args, "--dry-run"], config)
    assert result.returncode == 0, result.stdout
    return args, json.loads(result.stdout)["data"]["confirm_token"]


def _bound(config) -> dict:
    path = config / "knowledge-base.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def test_competing_cli_confirms_apply_the_write_once(tmp_path):
    config = tmp_path / "config"
    args, token = _preview(config)
    processes = []
    try:
        for _ in range(4):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-m", "xpedition_cli.main", *args, "--confirm", token],
                    cwd=ROOT,
                    env=_environment(config),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                )
            )
        results = []
        for process in processes:
            out, err = process.communicate(timeout=30)
            result = json.loads(out)
            results.append(result)
            assert token not in out + err
            if not result["ok"]:
                assert process.returncode == 6
                assert result["error"]["code"] == "E_CONFLICT"
                assert result["error"]["retryable"] is False
        assert sum(result["ok"] for result in results) == 1
        assert LINK in json.dumps(_bound(config))
        recorded = json.loads((config / "confirm-consumed.json").read_text())
        assert hashlib.sha256(token.encode()).hexdigest() in recorded
        assert token not in (config / "audit.jsonl").read_text()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


def test_an_alternate_spelling_of_the_token_fails_before_the_write(tmp_path):
    config = tmp_path / "config"
    args, token = _preview(config)
    body, signature = token.rsplit(".", 1)
    result = _run([*args, "--confirm", body + "====." + signature], config)
    assert result.returncode == 6
    assert json.loads(result.stdout)["error"]["code"] == "E_CONFLICT"
    assert LINK not in json.dumps(_bound(config))
    # Rejecting the alternate representation did not consume the original.
    assert _run([*args, "--confirm", token], config).returncode == 0


def test_storage_degradation_preserves_cli_json_and_never_prints_exception_contents(tmp_path):
    config = tmp_path / "config"
    args, token = _preview(config)
    script = """
import errno
import sys
from xpedition_cli import confirmation_store as store
from xpedition_cli.main import main
original = store.atomic_write
def fail_ledger(path, data):
    if path.name == 'confirm-consumed.json':
        raise OSError(errno.ENOSPC, 'private-fault-injection-message')
    return original(path, data)
store.atomic_write = fail_ledger
raise SystemExit(main(sys.argv[1:]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, *args, "--confirm", token],
        cwd=ROOT,
        env=_environment(config),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0 and json.loads(result.stdout)["ok"]
    assert "ledger_write_failed" in result.stderr
    assert "private-fault-injection-message" not in result.stderr + result.stdout
    assert token not in result.stderr + result.stdout
    assert LINK in json.dumps(_bound(config))
    assert not (config / "confirm-consumed.json").exists()
