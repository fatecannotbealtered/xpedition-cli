from __future__ import annotations

import copy
import json

import pytest

from xpedition_cli import main as cli
from xpedition_cli.changeset import apply_operations
from xpedition_cli.errors import CLIError
from xpedition_cli.models import normalise_project


def run(capsys, *args):
    code = cli.main(list(args))
    streams = capsys.readouterr()
    return code, json.loads(streams.out)


@pytest.mark.parametrize("backend,expected", [("native_xpedition", "E_USAGE"), ("invalid", "E_VALIDATION")])
def test_project_init_does_not_fall_back_to_mock(tmp_path, monkeypatch, capsys, backend, expected):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    target = tmp_path / "new" / "board.prj"
    code, result = run(capsys, "project", "init", "--backend", backend, "--project", str(target), "--dry-run")
    assert code == 2
    assert result["error"]["code"] == expected
    assert not target.exists()
    assert not target.parent.exists()
    assert not (tmp_path / "config" / "confirm.secret").exists()


def test_native_init_cannot_consume_a_mock_preview(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    target = tmp_path / "project.json"
    _, preview = run(capsys, "project", "init", "--project", str(target), "--dry-run")
    token = preview["data"]["confirm_token"]
    code, result = run(capsys, "project", "init", "--backend", "native_xpedition",
                       "--project", str(target), "--confirm", token)
    assert code == 2 and result["error"]["code"] == "E_USAGE"
    assert not target.exists()
    code, result = run(capsys, "project", "init", "--project", str(target), "--confirm", token)
    assert code == 0 and result["data"]["created"]


def test_projection_through_cli_keeps_paging_and_trust(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "project.json"
    path.write_text(json.dumps({"project": "demo", "components": [
        {"refdes": "R1", "value": "10k"}, {"refdes": "C1", "value": "100n"}]}), encoding="utf-8")
    code, result = run(capsys, "schematic", "components", "--backend", "mock",
                       "--project", str(path), "--limit", "1", "--fields", "items.refdes", "--compact")
    assert code == 0
    assert result["data"]["items"] == [{"refdes": "R1"}]
    assert result["data"]["has_more"] and result["data"]["next_offset"] == 1
    assert "items" in result["data"]["_untrusted"]


@pytest.mark.parametrize("mode", ["matched", "unchanged", "readback_timeout", "save_unreported"])
def test_native_apply_reports_observed_postconditions(tmp_path, monkeypatch, capsys, mode):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "board.prj"
    path.write_text("test fixture, not an Xpedition project", encoding="utf-8")
    before = normalise_project({"project": "demo", "components": [{"refdes": "R1", "x": 0, "y": 0}]})
    operations = [{"type": "move_component", "refdes": "R1", "x": 10, "y": 20}]
    after, _ = apply_operations(before, operations)

    class FakeNative:
        name = "native_xpedition"
        invoked = False

        def load(self, project_path, domain=None):
            if self.invoked and mode == "readback_timeout":
                raise CLIError("E_TIMEOUT", "simulated read-back timeout")
            value = after if self.invoked and mode != "unchanged" else before
            return copy.deepcopy(value), path

        def invoke(self, method, params, **kwargs):
            assert method == "apply_changeset"
            self.invoked = True
            result = {"applied": copy.deepcopy(operations)}
            if mode != "save_unreported":
                result["saved"] = True
            return result

    fake = FakeNative()
    monkeypatch.setattr(cli, "_backend", lambda options: fake)
    changeset = tmp_path / "changeset.json"
    changeset.write_text(json.dumps({"project": "demo", "operations": operations}), encoding="utf-8")
    args = ("schematic", "apply", "--backend", "native_xpedition", "--project", str(path),
            "--changeset", str(changeset))
    code, result = run(capsys, *args, "--dry-run")
    assert code == 0 and not fake.invoked
    token = result["data"]["confirm_token"]
    code, result = run(capsys, *args, "--confirm", token)
    assert fake.invoked
    if mode in {"matched", "save_unreported"}:
        assert code == 0 and result["data"]["verification"]["valid"]
        assert result["data"]["verification"]["scope"] == "requested_postconditions"
        assert result["data"]["verification"]["saved"] is (None if mode == "save_unreported" else True)
    else:
        assert code == 2 and result["error"]["code"] == "E_PROJECT_INVALID"
        assert result["error"]["retryable"] is False
        assert result["error"]["details"]["write_attempted"] is True
        assert result["error"]["details"]["stage"] == ("read_back" if mode == "readback_timeout" else "verify")


def test_reference_confirm_flags_cover_all_declared_writes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    code, result = run(capsys, "reference", "--compact")
    assert code == 0
    data = result["data"]
    writes = {item["path"] for item in data["commands"] if item["type"] == "write"}
    for name in ("dry-run", "confirm"):
        declared = next(item for item in data["global_flags"] if item["name"] == name)
        assert set(declared["applies_to"]) == writes
