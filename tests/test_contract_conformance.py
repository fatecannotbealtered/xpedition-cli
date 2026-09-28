"""The envelope, the error triple and the exit table match the fleet's canonical contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((ROOT / "contract" / "contract.json").read_text(encoding="utf-8"))
EXTENSION = json.loads((ROOT / "contract" / "contract-ext.json").read_text(encoding="utf-8"))


def run(*args: str, config: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "XPEDITION_CLI_CONFIG_DIR": str(config),
        # never the developer's real adapter: a native command fails as unavailable
        "XPEDITION_NATIVE_COMMAND": str(ROOT / "tests" / "no-such-native-adapter"),
    }
    return subprocess.run(
        [sys.executable, "-m", "xpedition_cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        env=env,
        check=False,
    )


def assert_envelope(result, ok: bool) -> dict:
    body = json.loads(result.stdout)
    envelope = CONTRACT["envelope"]
    expected = set(envelope["success_keys"] if ok else envelope["error_keys"])
    assert set(body) == expected
    assert body["schema_version"] == envelope["schema_version_value"]
    assert set(body["meta"]) <= set(envelope["meta_required_keys"] + envelope["meta_optional_keys"])
    assert set(body["meta"]) >= set(envelope["meta_required_keys"])
    if ok:
        assert body["ok"] is True
    else:
        assert body["ok"] is False
        assert set(body["error"]) == set(envelope["error_object_keys"])
    return body


def test_success_envelopes_match_canonical_contract(tmp_path: Path) -> None:
    for args in (("context",), ("doctor",), ("reference",), ("changelog",), ("version",)):
        result = run(*args, "--compact", config=tmp_path / "config")
        assert result.returncode == 0, result.stdout
        assert_envelope(result, True)
        assert result.stderr == ""


def test_error_triple_matches_canonical_contract(tmp_path: Path) -> None:
    result = run("unknown-command", config=tmp_path / "config")
    body = assert_envelope(result, False)
    spec = CONTRACT["error_codes"]["core"][body["error"]["code"]]
    assert result.returncode == spec["exit"]
    assert body["error"]["retryable"] is spec["retryable"]


def test_a_native_command_without_an_adapter_is_backend_unavailable(tmp_path: Path) -> None:
    project = tmp_path / "Board.prj"
    project.write_text("", encoding="utf-8")
    result = run("schematic", "sheets", "--project", str(project), config=tmp_path / "config")
    body = assert_envelope(result, False)
    assert body["error"]["code"] == "E_BACKEND_UNAVAILABLE" and result.returncode == 4
    assert body["error"]["details"]["hint"]


def test_reference_uses_canonical_exit_table(tmp_path: Path) -> None:
    result = run("reference", "--compact", config=tmp_path / "config")
    body = assert_envelope(result, True)
    assert body["data"]["exit_codes"] == CONTRACT["exit_codes"]["table"]
    for code, spec in CONTRACT["error_codes"]["core"].items():
        assert body["data"]["error_codes"][code] == spec
    for code, spec in EXTENSION["error_codes"].items():
        assert body["data"]["error_codes"][code] == spec


def test_sensitive_design_attributes_are_redacted(cli, adapter, project) -> None:
    from fakes import schematic_snapshot

    design = schematic_snapshot()
    design["components"][0]["attributes"] = {"api_token": "never-print-this", "Value": "MCU"}
    adapter.on("snapshot", lambda params: design)
    code, envelope = cli("schematic", "components", "--project", str(project))
    text = json.dumps(envelope)
    assert code == 0 and "never-print-this" not in text and "<redacted>" in text
