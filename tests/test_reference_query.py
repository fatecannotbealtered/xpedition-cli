"""`reference --command | --domain | --schema`: a self-contained slice, never a second catalog."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from xpedition_cli.backends import native_xpedition
from xpedition_cli.errors import CLIError
from xpedition_cli.reference_data import reference
from xpedition_cli.reference_query import SELECTOR_FLAGS, select_reference

CONTRACT = Path(__file__).resolve().parent.parent / "contract" / "contract.json"


def test_unfiltered_reference_remains_complete():
    full = reference()
    assert select_reference(full) is full
    assert "selection" not in full
    assert len(full["commands"]) == 54


@pytest.mark.parametrize(
    "path", ["pcb trace", "pcb move", "schematic draw", "schematic edit", "context"]
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
        "workflow",
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
def test_reference_selectors_through_cli(cli, capsys, args):
    code, envelope = cli("reference", *args, "--compact")
    assert code == 0, envelope
    data = envelope["data"]
    assert data["selection"]["kind"] in {"command", "domain", "schema"}
    assert len(data["commands"]) < len(reference()["commands"])
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert set(envelope) == set(contract["envelope"]["success_keys"])


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
def test_bad_selectors_are_structured_errors(cli, args, code):
    exit_code, envelope = cli("reference", *args)
    error = envelope["error"]
    assert error["code"] == code and error["retryable"] is False
    assert exit_code == (3 if code == "E_NOT_FOUND" else 2)


@pytest.mark.parametrize("command", [["project", "create"], ["pcb", "move"], ["version"]])
def test_reference_flags_cannot_be_ignored_by_a_different_command(cli, tmp_path, command):
    code, envelope = cli(
        *command,
        "--command",
        "context",
        "--dry-run",
        "--project",
        str(tmp_path / "must-not-exist.prj"),
    )
    assert code == 2 and envelope["error"]["code"] == "E_USAGE"
    assert not (tmp_path / "must-not-exist.prj").exists()


def test_discovery_does_not_probe_native_or_load_a_project(cli, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("reference must not reach the adapter")

    monkeypatch.setattr(native_xpedition.subprocess, "run", unexpected)
    code, envelope = cli("reference", "--command", "pcb trace")
    assert code == 0 and envelope["data"]["commands"][0]["path"] == "pcb trace"


def test_selector_metadata_is_single_sourced():
    entry = next(item for item in reference()["commands"] if item["path"] == "reference")
    assert {"--" + item["name"] for item in entry["params"]} == SELECTOR_FLAGS
    assert entry["mutually_exclusive"] == [["command", "domain", "schema"]]


def test_targeted_reference_has_bounded_relative_output_cost():
    full = reference()
    selected = reference(command="pcb move")

    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())

    assert size(selected) < size(full) * 0.25
