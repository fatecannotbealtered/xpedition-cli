from __future__ import annotations

import json

import pytest
from test_cli_contract import payload, run_cli

from xpedition_cli import main as cli
from xpedition_cli.models import normalise_project


@pytest.mark.parametrize("kind", ["all", "erc", "drc", "dfm"])
def test_native_analysis_run_fails_before_project_or_backend_access(
    tmp_path, monkeypatch, capsys, kind
):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    def unexpected(*args, **kwargs):
        raise AssertionError("unsupported analysis must be rejected before reading a project")
    monkeypatch.setattr(cli, "_backend", unexpected)
    monkeypatch.setattr(cli, "_run_mock_analysis", unexpected)
    path = tmp_path / "do-not-create.prj"
    code = cli.main(["analysis", "run", "--backend", "native_xpedition", "--kind", kind,
                     "--project", str(path)])
    streams = capsys.readouterr()
    result = json.loads(streams.out)
    assert code == 4 and result["ok"] is False
    assert result["error"]["code"] == "E_BACKEND_UNAVAILABLE"
    assert result["error"]["retryable"] is False
    assert result["error"]["details"]["supported_backends"] == ["mock"]
    assert "data" not in result
    assert not path.exists()


@pytest.mark.parametrize("backend", ["mock", "native_xpedition"])
def test_agent_capabilities_does_not_load_or_require_a_project_backend(
    tmp_path, monkeypatch, capsys, backend
):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    def unexpected(*args, **kwargs):
        raise AssertionError("capability discovery must not load a design")
    monkeypatch.setattr(cli, "_backend", unexpected)
    code = cli.main(["agent", "capabilities", "--backend", backend,
                     "--project", str(tmp_path / "missing.prj")])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["ok"]
    assert "capabilities" in result["data"]
    # Both transports serialize tuples as arrays. Compare the actual wire shape,
    # not a decoded JSON list with the registry's internal Python tuple.
    expected = json.loads(json.dumps(cli._agent_request({"method": "capabilities"}, {})))
    assert result["data"] == expected


def test_capability_discovery_ignores_invalid_design_contents(tmp_path):
    path = tmp_path / "not-a-project.json"
    path.write_text("not valid JSON", encoding="utf-8")
    result = run_cli("agent", "capabilities", "--project", str(path),
                     config_dir=tmp_path / "config")
    assert result.returncode == 0 and payload(result)["ok"]
    assert path.read_text(encoding="utf-8") == "not valid JSON"


@pytest.mark.parametrize("kind", ["results", "erc", "drc", "dfm"])
def test_native_stored_analysis_reads_are_not_disabled(monkeypatch, kind):
    observed = normalise_project({
        "project": "observed", "analysis": {
            key: [{"message": "stored fixture", "severity": "warning"}]
            for key in ("erc", "drc", "dfm")
        },
    })
    calls = []
    class FakeNative:
        name = "native_xpedition"
        def load(self, path):
            calls.append(path)
            return observed, None
    monkeypatch.setattr(cli, "_backend", lambda options: FakeNative())
    result = cli.dispatch(["analysis", kind], {"backend": "native_xpedition", "project": "fixture"})
    assert calls == ["fixture"]
    assert result["items"] and result["project"] == "observed"


def test_mock_analysis_remains_explicitly_labelled(tmp_path):
    result = run_cli("analysis", "run", "--backend", "mock", "--kind", "all",
                     config_dir=tmp_path / "config")
    assert result.returncode == 0
    assert payload(result)["data"]["engine"] == "mock"


def test_invalid_analysis_backend_remains_a_validation_error(tmp_path):
    result = run_cli("analysis", "run", "--backend", "typo", config_dir=tmp_path / "config")
    assert result.returncode == 2
    assert payload(result)["error"]["code"] == "E_VALIDATION"
