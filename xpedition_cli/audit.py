from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    override = os.environ.get("XPEDITION_CLI_CONFIG_DIR")
    root = Path(override).expanduser() if override else Path.home() / ".xpedition-cli"
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def audit_path() -> Path:
    return config_dir() / "audit.jsonl"


def record(command: str, exit_code: int, duration_ms: int, args: list[str]) -> None:
    """Write a minimal audit record without secrets or payload contents."""
    safe_args = []
    redacted = {"--confirm", "--token", "--password", "--secret"}
    skip_next = False
    for arg in args:
        if skip_next:
            safe_args.append("<redacted>")
            skip_next = False
            continue
        if arg in redacted:
            safe_args.append(arg)
            skip_next = True
        elif any(arg.startswith(prefix + "=") for prefix in redacted):
            safe_args.append(arg.split("=", 1)[0] + "=<redacted>")
        else:
            safe_args.append(arg)
    item: dict[str, Any] = {
        "command": command,
        "args": safe_args,
        "account": "local-user",
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "exit_code": exit_code,
        "duration_ms": duration_ms,
    }
    try:
        with audit_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    except OSError:
        # Auditing must not make an otherwise safe command fail.
        return
