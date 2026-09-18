from __future__ import annotations

import json

import pytest
from test_api_inventory import Com, fixture_file

from xpedition_cli import api_inventory as inventory
from xpedition_cli import main as cli
from xpedition_cli.api_inventory_contract import OUTPUT_SCHEMA


def invoke(capsys, *args):
    code = cli.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def test_api_inventory_cli_uses_only_type_metadata(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    source = fixture_file(tmp_path)
    com = Com()
    monkeypatch.setattr(inventory, "_pythoncom", lambda: com)

    def fail(*a, **kw):
        pytest.fail("inventory accessed design backend/confirmation")

    monkeypatch.setattr(cli, "_backend", fail)
    monkeypatch.setattr(cli.NativeBackend, "invoke", fail)
    monkeypatch.setattr(cli, "issue", fail)
    monkeypatch.setattr(cli, "consume", fail)
    code, result = invoke(capsys, "system", "api-inventory", "--input", str(source), "--compact")
    assert code == 0 and result["ok"]
    assert set(result["data"]) == set(OUTPUT_SCHEMA["fields"])
    assert all(v is False for v in result["data"]["execution"].values())
    assert not (tmp_path / "config" / "confirm.secret").exists()
    assert com.calls[-1] == "uninitialize"


@pytest.mark.parametrize(
    "args",
    [
        ["--project", "x.prj"],
        ["--backend", "native_xpedition"],
        ["--confirm", "ct_secret"],
        ["--dry-run"],
        ["--output", "x"],
        ["--input", "--name", "X"],
        ["--name=X", "--name=Y"],
        ["extra"],
    ],
)
def test_inventory_rejects_unsafe_or_ambiguous_flags_before_loading(
    tmp_path, monkeypatch, capsys, args
):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(inventory, "_pythoncom", lambda: pytest.fail("loader accessed"))
    code, result = invoke(capsys, "system", "api-inventory", *args)
    assert code == 2 and result["error"]["code"] == "E_USAGE"


def test_inventory_projection_retains_limitations(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    source = fixture_file(tmp_path)
    monkeypatch.setattr(inventory, "_pythoncom", lambda: Com())
    code, result = invoke(
        capsys,
        "system",
        "api-inventory",
        "--input",
        str(source),
        "--name",
        "IFixture",
        "--limit",
        "1",
        "--fields",
        "items",
    )
    assert code == 0
    value = result["data"]
    assert value["has_more"] and value["count"] == 1
    assert value["semantic_validation"] == "not_performed"
    assert value["execution"]["capabilities_granted"] is False
    assert "items" in value["_untrusted"]


def test_reference_exposes_read_only_inventory_contract(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    code, result = invoke(capsys, "reference", "--compact")
    assert code == 0
    entry = next(c for c in result["data"]["commands"] if c["path"] == "system api-inventory")
    assert entry["type"] == "query" and entry["target_xpedition_validation"] == "not_performed"
    assert result["data"]["schemas"][entry["output_schema"]] == OUTPUT_SCHEMA
