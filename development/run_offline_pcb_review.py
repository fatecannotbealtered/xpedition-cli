"""Run selected offline checks without accessing the restricted vendor inputs.

This is a development runner, never an alternate product CLI or build path.
Public SDK tests and builds still require normal authorization for those inputs.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        raise SystemExit("usage: run_offline_pcb_review.py BOUNDARY-REPORT pytest-arguments...")
    output = Path(args.pop(0)).absolute()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    denied = []

    def boundary(event, values):
        if event not in {"import", "open", "os.listdir", "os.scandir"}:
            return
        path_event = event in {"open", "os.listdir", "os.scandir"}
        value = values[0] if values else None
        path = (
            os.fsdecode(value).replace("\\", "/").lower() if isinstance(value, (str, bytes)) else ""
        )
        if (
            event == "import"
            and path.startswith("xpedition_cli._vendor")
            or path_event
            and "/xpedition_cli/_vendor" in path
        ):
            denied.append({"event": event, "reason": "restricted vendor dependency"})
            raise PermissionError("Offline review must not access restricted vendor dependencies")

    sys.addaudithook(boundary)
    status = None
    try:
        import pytest

        status = int(pytest.main(args))
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({"pytest_exit": status, "forbidden_access_attempts": denied}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    return status if not denied else 1


if __name__ == "__main__":
    raise SystemExit(main())
