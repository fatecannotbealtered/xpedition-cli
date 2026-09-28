"""The command surface itself: one registry, a strict parser, a truthful reference."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from xpedition_cli import __version__
from xpedition_cli import main as entry
from xpedition_cli.cli.registry import NEEDS, STAGES, WORKFLOW, commands
from xpedition_cli.errors import CLIError
from xpedition_cli.reference_data import reference

ROOT = Path(__file__).resolve().parents[1]


# -- the registry ------------------------------------------------------------------


def test_every_command_is_declared_once_with_a_handler_that_exists() -> None:
    import importlib

    paths = [command.path for command in commands()]
    assert len(paths) == len(set(paths)) == 52
    for command in commands():
        module_name, _, function = command.handler.partition(":")
        module = importlib.import_module(f"xpedition_cli.cli.{module_name}")
        assert callable(getattr(module, function)), command.path


def test_every_command_says_where_it_runs_and_which_stage_it_belongs_to() -> None:
    for command in commands():
        assert command.needs in NEEDS, command.path
        assert command.stage in STAGES, command.path
        assert command.tier in {"read", "write", "dangerous"}, command.path
        if command.tier == "dangerous":
            assert command.dangerous_when, command.path
        if command.writes:
            assert command.dry_run_schema, command.path
            assert command.blast_radius != "none", command.path


def test_a_flag_is_a_switch_everywhere_or_takes_a_value_everywhere() -> None:
    kinds: dict[str, set[str]] = {}
    for command in commands():
        for param in command.all_params():
            kinds.setdefault(param.name, set()).add(
                "switch" if param.type == "boolean" else "value"
            )
    mixed = {name: kind for name, kind in kinds.items() if len(kind) > 1}
    assert mixed == {}


def test_the_workflow_names_only_commands_that_exist_and_covers_the_chain() -> None:
    paths = {command.path for command in commands()}
    named = {name for step in WORKFLOW for name in step["commands"]}
    assert named <= paths
    for essential in (
        "project create",
        "schematic draw",
        "library build",
        "pcb create",
        "pcb annotate",
        "pcb route",
        "pcb check",
        "pcb export",
    ):
        assert essential in named
    assert [step["step"] for step in WORKFLOW] == list(range(1, len(WORKFLOW) + 1))


# -- reference ----------------------------------------------------------------------


def test_reference_declares_every_schema_it_names() -> None:
    catalog = reference()
    for item in catalog["commands"]:
        assert item["output_schema"] in catalog["schemas"], item["path"]
        if "dry_run_output_schema" in item:
            assert item["dry_run_output_schema"] in catalog["schemas"], item["path"]
    assert catalog["version"] == __version__
    assert set(catalog["needs"]) == set(NEEDS)
    assert catalog["stages"] == list(STAGES)
    assert catalog["workflow"] == WORKFLOW


def test_no_command_or_example_mentions_a_backend() -> None:
    catalog = reference()
    catalog.pop("release_readiness")  # its mock_upstream_* keys are the spec's names
    text = json.dumps(catalog)
    assert "--backend" not in text
    assert "mock" not in text.lower()


@pytest.mark.parametrize("command", commands(), ids=lambda command: command.path)
def test_every_example_parses_as_the_command_it_documents(command) -> None:
    assert command.examples, command.path
    for example in command.examples:
        argv = shlex.split(example.replace("<confirm_token>", "TOKEN"))
        assert argv[0] == "xpedition-cli"
        parsed, options = entry.parse_argv(argv[1:])
        assert parsed is command, example
        if command.writes:
            assert options.get("dry_run") or options.get("confirm"), example
        if (
            command.tier == "dangerous"
            and options.get("confirm")
            and command.dangerous_when.startswith("always")
        ):
            assert options.get("dangerous"), example


def test_every_write_shows_the_dry_run_then_the_confirm() -> None:
    for command in commands():
        if not command.writes:
            continue
        joined = " ".join(command.examples)
        assert "--dry-run" in joined and "--confirm <confirm_token>" in joined, command.path


# -- the parser -------------------------------------------------------------------------


def _error(argv: list[str]) -> CLIError:
    with pytest.raises(CLIError) as caught:
        entry.parse_argv(argv)
    return caught.value


def test_an_option_the_command_does_not_take_is_refused_with_what_it_does_take() -> None:
    error = _error(["pcb", "outline", "--project", "X.prj", "--wdith", "3"])
    assert error.code == "E_USAGE"
    assert error.details["option"] == "--wdith"
    assert "--width" in error.details["accepted"]


def test_a_missing_required_option_is_named() -> None:
    error = _error(["pcb", "outline", "--project", "X.prj", "--width", "3"])
    assert error.code == "E_USAGE" and error.details["missing"] == ["--height"]


def test_positional_arguments_after_a_command_are_refused() -> None:
    error = _error(["project", "info", "X.prj"])
    assert error.code == "E_USAGE" and error.details["arguments"] == ["X.prj"]


def test_an_unknown_verb_lists_the_domain_s_commands() -> None:
    error = _error(["pcb", "frobnicate"])
    assert error.code == "E_USAGE" and "pcb route" in error.details["commands"]
    error = _error(["pcb"])
    assert error.message == "pcb needs a verb"


def test_an_unknown_command_lists_the_domains() -> None:
    error = _error(["exchange", "inspect"])
    assert error.code == "E_USAGE" and "schematic" in error.details["domains"]
    error = _error([])
    assert error.code == "E_USAGE"


def test_a_switch_takes_no_value_and_a_value_option_needs_one() -> None:
    assert _error(["pcb", "check", "--project", "X.prj", "--online=yes"]).code == "E_USAGE"
    assert _error(["pcb", "check", "--project"]).code == "E_USAGE"


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (
            ["pcb", "outline", "--project", "X.prj", "--width", "wide", "--height", "3"],
            "E_VALIDATION",
        ),
        (["pcb", "check", "--project", "X.prj", "--limit", "2"], "E_USAGE"),
        (["schematic", "sheets", "--project", "X.prj", "--timeout", "soon"], "E_VALIDATION"),
        (["session", "start", "--kind", "designer"], "E_VALIDATION"),
        (["changelog", "--since", "one"], "E_VALIDATION"),
        (["bom", "export", "--project", "X.prj", "--limit", "-1"], "E_VALIDATION"),
        (["schematic", "components", "--project", "X.prj", "--format", "yaml"], "E_VALIDATION"),
    ],
)
def test_values_are_checked_before_anything_runs(argv, code) -> None:
    assert _error(argv).code == code


def test_a_repeated_option_must_agree_unless_it_is_a_list() -> None:
    assert _error(["pcb", "outline", "--project", "A.prj", "--project", "B.prj"]).code == "E_USAGE"
    _, options = entry.parse_argv(
        ["pcb", "unroute", "--project", "A.prj", "--nets", "A", "--nets", "B,C"]
    )
    assert options["nets"] == "A,B,C"


def test_mutually_exclusive_options_are_refused_together() -> None:
    error = _error(["pcb", "move", "--project", "X.prj", "--refdes", "U1", "--file", "t.json"])
    assert error.code == "E_USAGE"
    error = _error(["reference", "--command", "pcb route", "--domain", "pcb"])
    assert error.code == "E_USAGE"


def test_write_gates_are_only_taken_by_writes() -> None:
    assert _error(["pcb", "check", "--project", "X.prj", "--dry-run"]).code == "E_USAGE"
    assert _error(["kb", "list", "--confirm", "t"]).code == "E_USAGE"
    assert (
        _error(
            ["pcb", "outline", "--project", "X", "--width", "1", "--height", "1", "--dangerous"]
        ).code
        == "E_USAGE"
    )


# -- the process boundary --------------------------------------------------------------


def test_help_is_for_people_and_exits_zero(capsys) -> None:
    assert entry.main(["--help"]) == 0
    text = capsys.readouterr().out
    assert "Placement:" in text and "pcb route" in text and "reference" in text
    assert entry.main(["pcb", "--help"]) == 0
    text = capsys.readouterr().out
    assert "pcb route" in text and "schematic draw" not in text
    assert entry.main(["schematic", "draw", "--help"]) == 0
    text = capsys.readouterr().out
    assert "--dangerous" in text and "--design <path> (required)" in text


def test_version_flag_and_command(cli, capsys) -> None:
    assert entry.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"xpedition-cli {__version__}"
    code, payload = cli("version")
    assert code == 0 and payload["data"] == {"version": __version__}


def test_an_error_is_one_envelope_with_its_exit_code_even_before_parsing(capsys) -> None:
    code = entry.main(["pcb", "frobnicate", "--compact"])
    out = capsys.readouterr().out
    assert code == 2 and out.count("\n") == 1
    payload = json.loads(out)
    assert payload["ok"] is False and payload["error"]["code"] == "E_USAGE"
    assert payload["error"]["retryable"] is False


def test_text_and_raw_formats(cli, capsys) -> None:
    assert entry.main(["version", "--format", "text"]) == 0
    assert capsys.readouterr().out.strip() == f"version: {__version__}"
    assert entry.main(["version", "--format", "raw", "--compact"]) == 0
    assert json.loads(capsys.readouterr().out) == {"version": __version__}
    assert entry.main(["pcb", "frob", "--format", "text"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "E_USAGE" in captured.err


def test_fields_trims_the_data(cli) -> None:
    code, payload = cli("reference", "--fields", "tool,version", "--compact")
    assert code == 0 and set(payload["data"]) == {"tool", "version"}


def test_the_installed_entry_point_runs_as_a_process() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "xpedition_cli", "version", "--compact"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=False,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["data"]["version"] == __version__
    assert completed.stderr == ""
