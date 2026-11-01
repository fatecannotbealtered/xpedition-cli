"""Current Skill recipes invoke the real public file-only capability query."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_driver_v2 import _scope

from xpedition_cli.driver import Adapter, runtime

ROOT = Path(__file__).resolve().parents[1]


class NoNative:
    def invoke(self, *args, **kwargs):
        pytest.fail("design-before-write preflight must not connect CAD")


@pytest.mark.parametrize("pins,blocked", [(467, False), (513, True)])
def test_delivered_requirements_example_runs_public_preflight_without_native_calls(
    tmp_path, monkeypatch, pins, blocked
):
    _scope(tmp_path, monkeypatch)
    intent = json.loads(
        (ROOT / "examples/representative-design-preflight.json").read_text(encoding="utf-8")
    )
    intent["counts"]["pins"] = pins
    file = tmp_path / "declared-intent.json"
    file.write_text(json.dumps(intent), encoding="utf-8")
    with runtime(Adapter(NoNative()), state_dir=tmp_path / "state") as cli:
        result = cli.run(
            ["project", "inspect", "--preflight", "--requirements", str(file), "--compact"]
        )
    assert result.envelope["ok"], result.envelope
    observed = result.envelope["data"]["preflight"]
    assert observed["counts"]["pins"] == pins
    assert observed["blocked"] is blocked
    assert observed["can_dispatch"] is False
    assert observed["native_observation_performed"] is False
    assert all(step["can_dispatch"] is False for step in observed["steps"])


@pytest.mark.parametrize("name", ["xpedition-cli", "xpedition-schematic", "xpedition-pcb"])
def test_public_skill_family_preserves_authorization_and_has_evaluation_scenarios(name):
    text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    assert "STOP CHECKPOINT" in text
    assert "reference" in text
    assert "project inspect --preflight" in text
    cases = json.loads((ROOT / "skills" / name / "test-prompts.json").read_text(encoding="utf-8"))
    assert len(cases["scenarios"]) >= 3
    assert all(case["prompt"] and case["expected"] for case in cases["scenarios"])


@pytest.mark.parametrize("ok", [False, True])
def test_powershell_reader_preserves_real_outcome_dimensions_without_tokens_or_scope(tmp_path, ok):
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell is a declared Windows delivery prerequisite")
    fixture = {
        "ok": ok,
        "schema_version": "1.0",
        "meta": {},
        "data": {
            "confirm_token": "private-fixture-token",
            "authorization_scope": "private-fixture-scope",
        },
    }
    receipt = {
        "operation_id": "op-fixture",
        "outcome": {
            "execution": "completed",
            "verification": {"status": "passed" if ok else "incomplete"},
        },
        "failed_stage": None if ok else "verify",
        "extensions": {"persistence_independently_reopened": ok, "private_state": "do-not-print"},
    }
    if ok:
        fixture["data"]["receipt"] = receipt
    else:
        fixture["error"] = {
            "code": "E_VERIFY_INCOMPLETE",
            "details": {"receipt": receipt, "private": "do-not-print"},
        }
    file = tmp_path / "result.json"
    file.write_text(json.dumps(fixture), encoding="utf-8-sig")
    process = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-File",
            str(ROOT / "examples/read-operation-result.ps1"),
            "-Path",
            str(file),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["ok"] is ok
    assert result["operation_id"] == "op-fixture"
    assert result["outcome"] == receipt["outcome"]
    assert "private-fixture" not in process.stdout and "do-not-print" not in process.stdout
