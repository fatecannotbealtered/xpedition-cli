"""Exercise the release smoke gate with a controlled child, without freezing."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import build
from xpedition_cli import __version__


@pytest.mark.parametrize("status", [1, 2, 7])
def test_smoke_rejects_nonzero_child_even_when_json_claims_success(monkeypatch, status):
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, status, json.dumps({"ok": True, "data": {"version": __version__}}), ""
        )

    monkeypatch.setattr(build.subprocess, "run", run)
    with pytest.raises(SystemExit, match=f"exited with status {status}"):
        build.smoke(Path("controlled-binary"))
    assert len(seen) == 1
    assert seen[0][1]["creationflags"] == (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def test_smoke_passes_only_after_all_offline_commands_succeed(monkeypatch):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        if argv[1] == "version":
            data = {"version": __version__}
        elif argv[1] == "reference":
            contract = json.loads(
                (build.ROOT / "contract/driver-v2/product_contract.json").read_text(
                    encoding="utf-8"
                )
            )
            data = {"commands": contract["reference"]["commands"]}
        elif "--dry-run" in argv:
            data = {"confirm_token": "offline-fixture-token"}
        else:
            output = Path(argv[argv.index("--output") + 1])
            output.write_bytes(b"controlled renderer result")
            data = {"receipt": {"extensions": {"result": {"pictures": [{"path": str(output)}]}}}}
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": data}), "")

    monkeypatch.setattr(build.subprocess, "run", run)
    build.smoke(Path("controlled-binary"))
    assert [argv[1] for argv in seen] == ["version", "reference", "schematic", "schematic"]
