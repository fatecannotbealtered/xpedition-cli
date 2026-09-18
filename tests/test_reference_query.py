from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_cli_contract import payload, run_cli

from xpedition_cli import main as cli
from xpedition_cli.errors import CLIError
from xpedition_cli.reference_data import reference
from xpedition_cli.reference_query import SELECTOR_FLAGS, select_reference


def test_unfiltered_reference_remains_complete():
    full = reference()
    assert select_reference(full) is full
    assert "selection" not in full
    assert len(full["commands"]) > 100


@pytest.mark.parametrize(
    "path", ["pcb trace", "pcb move", "schematic draw", "change apply", "context"]
)
def test_command_keeps_success_and_preview_schemas(path):
    full = reference()
    original = copy.deepcopy(full)
    selected = select_reference(full, command=path)
    declared = next(item for item in full["commands"] if item["path"] == path)
    assert selected["commands"] == [declared]
    names = {declared[key] for key in ("output_schema", "dry_run_output_schema") if key in declared}
    assert set(selected["schemas"]) == names
    assert selected["selection"]["command_count"] == 1
    for key in (
        "tool",
        "version",
        "release_readiness",
        "global_flags",
        "error_codes",
        "exit_codes",
    ):
        assert selected[key] == full[key]
    selected["commands"][0]["description"] = "test mutation"
    assert full == original


def test_domain_is_a_word_boundary_not_a_substring():
    full = reference()
    selected = select_reference(full, domain="pcb")
    assert selected["commands"]
    assert all(item["path"].split()[0] == "pcb" for item in selected["commands"])
    with pytest.raises(CLIError) as error:
        select_reference(full, domain="pc")
    assert error.value.code == "E_NOT_FOUND"


def test_schema_only_does_not_return_commands_with_dangling_schemas():
    selected = reference(schema="context")
    assert selected["commands"] == []
    assert set(selected["schemas"]) == {"context"}


def test_unknown_and_broken_schema_are_distinguished():
    with pytest.raises(CLIError) as error:
        select_reference(reference(), schema="not-a-schema")
    assert error.value.code == "E_NOT_FOUND"
    full = reference()
    target = next(item for item in full["commands"] if item["path"] == "context")
    target["output_schema"] = "missing"
    with pytest.raises(CLIError) as error:
        select_reference(full, command="context")
    assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize(
    "args",
    [
        ("--command", "pcb trace"),
        ("--command=pcb trace",),
        ("--domain", "schematic"),
        ("--schema", "context"),
        ("--command", "  pcb   move  "),
    ],
)
def test_reference_selectors_through_cli(tmp_path, args):
    result = run_cli("reference", *args, "--compact", config_dir=tmp_path / "config")
    assert result.returncode == 0, result.stdout
    data = payload(result)["data"]
    assert data["selection"]["kind"] in {"command", "domain", "schema"}
    assert len(data["commands"]) < len(reference()["commands"])
    assert result.stderr == ""
    contract = json.loads(
        (Path(__file__).resolve().parent.parent / "contract/contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(payload(result)) == set(contract["envelope"]["success_keys"])


@pytest.mark.parametrize(
    "args,code",
    [
        (("--command", "missing command"), "E_NOT_FOUND"),
        (("--domain", "missing"), "E_NOT_FOUND"),
        (("--schema", "missing"), "E_NOT_FOUND"),
        (("--command", ""), "E_VALIDATION"),
        (("--domain", "pcb move"), "E_VALIDATION"),
        (("--command", "context", "--schema", "context"), "E_USAGE"),
        (("--command", "context", "--command", "version"), "E_USAGE"),
        (("--command",), "E_USAGE"),
        (("--domain", "--compact"), "E_USAGE"),
    ],
)
def test_bad_selectors_are_structured_errors(tmp_path, args, code):
    result = run_cli("reference", *args, config_dir=tmp_path / "config")
    error = payload(result)["error"]
    assert error["code"] == code and error["retryable"] is False
    assert result.returncode == (3 if code == "E_NOT_FOUND" else 2)


@pytest.mark.parametrize(
    "command",
    [
        ["project", "init"],
        ["pcb", "move"],
        ["agent", "serve"],
        ["version"],
    ],
)
def test_reference_flags_cannot_be_ignored_by_a_different_command(tmp_path, command):
    result = run_cli(
        *command,
        "--command",
        "context",
        "--dry-run",
        "--project",
        str(tmp_path / "must-not-exist.json"),
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 2 and payload(result)["error"]["code"] == "E_USAGE"
    assert not (tmp_path / "must-not-exist.json").exists()


def test_discovery_does_not_probe_native_or_load_project(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("reference must not access the native backend")

    monkeypatch.setattr(cli, "_backend", unexpected)
    monkeypatch.setattr(cli, "NativeBackend", unexpected)
    data = cli.dispatch(["reference"], {"command": "pcb trace", "backend": "native_xpedition"})
    assert data["commands"][0]["path"] == "pcb trace"


def test_selector_metadata_is_single_sourced():
    entry = next(item for item in reference()["commands"] if item["path"] == "reference")
    assert {"--" + item["name"] for item in entry["params"]} == SELECTOR_FLAGS
    assert SELECTOR_FLAGS <= cli.VALUE_FLAGS
    assert all(len(item["mutually_exclusive_with"]) == 2 for item in entry["params"])


def test_targeted_reference_has_bounded_relative_output_cost():
    full = reference()
    selected = reference(command="pcb move")

    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())

    assert size(selected) < size(full) * 0.25
