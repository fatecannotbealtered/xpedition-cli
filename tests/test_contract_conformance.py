from __future__ import annotations

import json
from pathlib import Path

from test_cli_contract import run_cli

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((ROOT / "contract" / "contract.json").read_text(encoding="utf-8"))
EXTENSION = json.loads((ROOT / "contract" / "contract-ext.json").read_text(encoding="utf-8"))


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
    for args in (
        ("context", "--compact"),
        ("doctor", "--compact"),
        ("reference", "--compact"),
        ("changelog", "--compact"),
        ("project", "snapshot", "--backend", "mock", "--compact"),
    ):
        result = run_cli(*args, config_dir=tmp_path / "config")
        assert result.returncode == 0, result.stderr
        assert_envelope(result, True)


def test_error_triple_matches_canonical_contract(tmp_path: Path) -> None:
    result = run_cli("unknown-command", config_dir=tmp_path / "config")
    body = assert_envelope(result, False)
    code = body["error"]["code"]
    spec = CONTRACT["error_codes"]["core"][code]
    assert result.returncode == spec["exit"]
    assert body["error"]["retryable"] is spec["retryable"]


def test_reference_uses_canonical_exit_table(tmp_path: Path) -> None:
    result = run_cli("reference", "--compact", config_dir=tmp_path / "config")
    body = assert_envelope(result, True)
    assert body["data"]["exit_codes"] == CONTRACT["exit_codes"]["table"]
    for code, spec in CONTRACT["error_codes"]["core"].items():
        assert body["data"]["error_codes"][code] == spec
    for code, spec in EXTENSION["error_codes"].items():
        assert body["data"]["error_codes"][code] == spec


def test_sensitive_project_properties_are_redacted(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "project": "secret-fixture",
                "components": [
                    {
                        "refdes": "U1",
                        "properties": {"api_token": "never-print-this"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = run_cli(
        "project", "snapshot", "--project", str(project), config_dir=tmp_path / "config"
    )
    assert result.returncode == 0
    assert "never-print-this" not in result.stdout
    assert "<redacted>" in result.stdout
