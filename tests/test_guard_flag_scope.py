"""`--dry-run` and `--confirm` belong to write commands, and say so elsewhere.

The reference declares which commands the confirmation gate applies to -- the
writes -- but nothing enforced it, so passing `--dry-run` to a command without a
gate was accepted and ignored. `schematic export` renders a PDF that way: the
dry run wrote the file, and running the same dry run again failed because the
output already existed. An agent that has learned it can probe a guarded command
safely has to be told when it is not talking to one.
"""

from __future__ import annotations

import json

import pytest

from xpedition_cli import main as cli


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("flag", ["--dry-run", "--confirm"])
def test_a_query_command_refuses_the_gate_flags(tmp_path, monkeypatch, capsys, flag) -> None:
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    argv = ["schematic", "export", "--project", str(tmp_path / "x.prj"), flag]
    if flag == "--confirm":
        argv.append("ct_whatever")
    code, result = run(capsys, *argv)
    assert code == 2 and not result["ok"]
    assert result["error"]["code"] == "E_USAGE"
    assert "query command" in result["error"]["message"]
    assert result["error"]["details"]["command"] == "schematic export"


def test_the_refusal_happens_before_anything_is_written(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    output = tmp_path / "out.pdf"
    code, _ = run(
        capsys,
        "schematic",
        "export",
        "--backend",
        "native_xpedition",
        "--project",
        str(tmp_path / "x.prj"),
        "--output",
        str(output),
        "--dry-run",
    )
    assert code == 2
    assert not output.exists(), "a refused dry run must not leave the file it was probing"


def test_a_write_command_still_takes_the_gate(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    code, result = run(
        capsys,
        "schematic",
        "draw",
        "--project",
        str(tmp_path / "x.prj"),
        "--design",
        str(tmp_path / "missing.json"),
        "--dry-run",
    )
    # It fails on the missing design, not on the flag.
    assert result["error"]["code"] != "E_USAGE" or "takes no" not in result["error"]["message"]


def test_an_unknown_command_is_left_to_its_own_error(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    code, result = run(capsys, "schematic", "nonesuch", "--dry-run")
    assert "takes no" not in result["error"]["message"]


def test_commands_without_the_flags_are_untouched(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    code, result = run(capsys, "reference", "--compact")
    assert code == 0 and result["ok"]
