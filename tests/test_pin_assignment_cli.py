from __future__ import annotations

import json

import pytest

from xpedition_cli import main as cli
from xpedition_cli.pin_assignment_contract import OUTPUT_SCHEMA


def files(tmp_path):
    source = tmp_path / "snapshot.json"
    pins = tmp_path / "pins.csv"
    source.write_text(
        json.dumps(
            {
                "project": "fixture",
                "revision": "R1",
                "components": [
                    {
                        "refdes": "J1",
                        "pins": [{"number": "01", "net": "OLD"}, {"number": "1", "net": None}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pins.write_text("refdes,pin,net\nJ1,01,NEW\nJ1,1,+3V3\n", encoding="utf-8")
    return source, pins


def invoke(capsys, args):
    status = cli.main(args)
    streams = capsys.readouterr()
    return status, json.loads(streams.out)


@pytest.mark.parametrize("verb", ["pin-plan", "pin-check"])
def test_pin_commands_are_offline_queries(tmp_path, monkeypatch, capsys, verb):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    source, pins = files(tmp_path)
    before = source.read_bytes(), pins.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("offline pin workflow reached native/backend/confirmation")

    monkeypatch.setattr(cli, "_backend", forbidden)
    monkeypatch.setattr(cli.NativeBackend, "invoke", forbidden)
    monkeypatch.setattr(cli, "issue", forbidden)
    monkeypatch.setattr(cli, "consume", forbidden)
    args = ["schematic", "pin-plan"] if verb == "pin-plan" else ["schematic", "pin-check"]
    code, result = invoke(capsys, [*args, "--input", str(source), "--file", str(pins), "--compact"])
    assert code == 0 and result["ok"] is True
    assert set(result["data"]) == set(OUTPUT_SCHEMA["fields"])
    assert result["data"]["execution"] == {
        "supported": False,
        "performed": False,
        "reason": "observation_only; not an executable ChangeSet or native verification",
    }
    assert (source.read_bytes(), pins.read_bytes()) == before
    assert not (tmp_path / "config" / "confirm.secret").exists()


@pytest.mark.parametrize(
    "args",
    [
        ["--backend", "native_xpedition"],
        ["--dry-run"],
        ["--confirm", "ct_do_not_use"],
        ["--project", "real.prj"],
        ["--input", "--file", "x"],
        ["--input=x", "--input=y"],
        ["--output", "real.prj"],
        ["extra"],
        ["--limit", "0"],
        ["--limit", "1001"],
    ],
)
def test_invalid_pin_options_fail_before_reading_inputs(tmp_path, monkeypatch, capsys, args):
    import xpedition_cli.pin_assignment as module

    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(module, "_bytes", lambda *a: pytest.fail("unexpected file read"))
    code, result = invoke(capsys, ["schematic", "pin-plan", *args])
    assert code == 2 and result["error"]["code"] in {"E_USAGE", "E_VALIDATION"}


def test_field_selection_keeps_assessment_paging_and_trust(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    source, pins = files(tmp_path)
    code, result = invoke(
        capsys,
        [
            "--compact",
            "schematic",
            "pin-plan",
            "--input",
            str(source),
            "--file",
            str(pins),
            "--limit",
            "1",
            "--fields",
            "items",
        ],
    )
    assert code == 0
    data = result["data"]
    assert data["count"] == 1 and data["next_offset"] == 1
    assert data["summary"]["requested"] == 2
    assert "items" in data["_untrusted"] and data["execution"]["performed"] is False
    assert data["source"]["freshness"] == "not_checked"


def test_reference_exposes_formats_and_no_native_claim(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    code, result = invoke(capsys, ["reference", "--compact"])
    assert code == 0
    data = result["data"]
    for name in ("schematic pin-plan", "schematic pin-check"):
        command = next(c for c in data["commands"] if c["path"] == name)
        assert command["type"] == "query" and command["native_execution"] is False
        assert command["output_schema"] in data["schemas"]
        contract = next(p for p in command["params"] if p["name"] == "file")["input_contract"]
        assert contract["unique_by"] == ["refdes", "pin"]


def test_unknown_observation_never_reports_matches_true(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    source, pins = files(tmp_path)
    source.write_text(
        json.dumps(
            {
                "project": "fixture",
                "revision": "R1",
                "components": [{"refdes": "J1", "pins": [{"number": "01"}, {"number": "1"}]}],
            }
        ),
        encoding="utf-8",
    )
    code, result = invoke(
        capsys, ["schematic", "pin-check", "--input", str(source), "--file", str(pins)]
    )
    assert code == 0 and result["ok"]
    assert result["data"]["matches"] is None and result["data"]["valid"] is False
