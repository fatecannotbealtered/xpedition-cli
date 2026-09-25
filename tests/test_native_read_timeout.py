"""A native read can be given the time a large design needs.

On a 41-part, 5-sheet design with an 8 MB central library, `library
footprints`, `review run` and `bom export` each timed out at the fixed 30 s,
and every timeout left the session stale: stop, start, wait, sometimes
re-package -- three to four minutes each. The snapshot behind every native read
now defaults to 120 s, and `--timeout` sets it per command.
"""

from __future__ import annotations

import copy
import json
import subprocess

import pytest

from xpedition_cli import main as cli
from xpedition_cli.backends import native_xpedition
from xpedition_cli.errors import CLIError
from xpedition_cli.models import EMPTY_PROJECT


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    # a timeout records a stale session; keep it out of the real config
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    """A configured NativeBackend whose snapshot call is recorded, not run."""
    binary = tmp_path / "native-adapter.exe"
    binary.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(binary))
    monkeypatch.setattr(native_xpedition.NativeBackend, "require_implemented", lambda self: None)
    seen: list[dict] = []

    def fake_run(argv, **kwargs):
        seen.append({"method": json.loads(kwargs["input"])["method"], "timeout": kwargs["timeout"]})
        data = copy.deepcopy(EMPTY_PROJECT)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": data}), "")

    monkeypatch.setattr(native_xpedition.subprocess, "run", fake_run)
    return seen


@pytest.mark.parametrize(("raw", "seconds"), [(None, None), ("180", 180.0), ("1", 1.0)])
def test_the_timeout_is_read_in_seconds(raw, seconds) -> None:
    assert cli._timeout_option({"timeout": raw}) == seconds


@pytest.mark.parametrize("raw", ["0", "0.5", "3601", "slow"])
def test_a_timeout_outside_1_to_3600_seconds_is_refused(raw) -> None:
    with pytest.raises(CLIError) as caught:
        cli._timeout_option({"timeout": raw})
    assert caught.value.code == "E_VALIDATION"


def test_a_native_read_waits_120_seconds_by_default(tmp_path, adapter) -> None:
    native_xpedition.NativeBackend().load(str(tmp_path / "Board.prj"))
    assert adapter == [{"method": "snapshot", "timeout": 120.0}]


def test_the_timeout_reaches_the_snapshot_call(tmp_path, adapter) -> None:
    backend = cli._backend({"backend": "native_xpedition", "timeout": "300"})
    backend.load(str(tmp_path / "Board.prj"))
    assert adapter == [{"method": "snapshot", "timeout": 300.0}]


def test_a_command_passes_its_timeout_through(tmp_path, adapter, capsys) -> None:
    code = cli.main(
        ["bom", "export", "--backend", "native_xpedition", "--project", str(tmp_path / "Board.prj"),
         "--timeout", "240"]
    )  # fmt: skip
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["ok"], result
    assert adapter[0] == {"method": "snapshot", "timeout": 240.0}


def test_a_bad_timeout_fails_before_anything_runs(tmp_path, adapter, capsys) -> None:
    code = cli.main(
        ["review", "run", "--backend", "native_xpedition", "--project", str(tmp_path / "Board.prj"),
         "--timeout", "0"]
    )  # fmt: skip
    result = json.loads(capsys.readouterr().out)
    assert code == 2 and result["error"]["code"] == "E_VALIDATION"
    assert adapter == []


def test_a_timed_out_read_says_how_long_it_had_and_how_to_give_it_more(
    tmp_path, monkeypatch
) -> None:
    binary = tmp_path / "native-adapter.exe"
    binary.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(binary))
    monkeypatch.setattr(native_xpedition.NativeBackend, "require_implemented", lambda self: None)

    def too_slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(native_xpedition.subprocess, "run", too_slow)
    with pytest.raises(CLIError) as caught:
        native_xpedition.NativeBackend().load(str(tmp_path / "Board.prj"))
    assert caught.value.code == "E_TIMEOUT"
    assert caught.value.details["seconds"] == 120.0
    assert "--timeout above 120" in caught.value.details["hint"]
    assert caught.value.details["hint"].startswith("session stop, then session start")


def test_the_timeout_is_declared_as_a_global_flag(capsys) -> None:
    cli.main(["reference", "--compact"])
    declared = json.loads(capsys.readouterr().out)["data"]["global_flags"]
    flags = {flag["name"]: flag for flag in declared}
    assert flags["timeout"]["type"] == "number"
    assert flags["timeout"]["default"] == 120
