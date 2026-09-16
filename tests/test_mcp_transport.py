from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_mcp_json_rpc_stdio_round_trip(tmp_path: Path) -> None:
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "xpedition_snapshot", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "missing_tool", "arguments": {}},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    env = os.environ.copy()
    env["XPEDITION_CLI_CONFIG_DIR"] = str(tmp_path / "config")
    result = subprocess.run(
        [sys.executable, "-m", "xpedition_cli.main", "agent", "serve", "--transport", "mcp"],
        cwd=ROOT,
        input="\n".join(json.dumps(request) for request in requests) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2, 3, 4]
    assert responses[0]["result"]["capabilities"]["tools"]["listChanged"] is False
    assert len(responses[1]["result"]["tools"]) == 4
    assert all("outputSchema" in tool for tool in responses[1]["result"]["tools"])
    assert responses[2]["result"]["isError"] is False
    assert responses[2]["result"]["structuredContent"]["project"] == "mock-project"
    assert responses[3]["error"]["code"] == -32602
