"""A long draw has to be locatable while it runs and after it fails.

A 356-operation draw runs for minutes. It used to print nothing until it
finished and, on failure, report only an operation index -- which could be
resolved to an actual operation solely by importing the planner and counting
`open_sheet` records. The plan is now published with its indices, and the
adapter's progress reaches the caller's stderr as it happens.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from xpedition_cli import main as cli
from xpedition_cli.backends import native_xpedition


def test_operations_carry_their_index_and_sheet() -> None:
    ops = [
        {"op": "open_sheet", "number": 1},
        {"op": "place_symbol", "refdes": "R1"},
        {"op": "open_sheet", "number": 3},
        {"op": "wire", "label": "VCC"},
    ]
    annotated = cli._annotate_operations(ops)
    assert [entry["index"] for entry in annotated] == [0, 1, 2, 3]
    # The sheet an operation lands on is the last one opened before it.
    assert [entry["sheet"] for entry in annotated] == [1, 1, 3, 3]
    # The original fields survive, so the plan stays inspectable.
    assert annotated[3]["label"] == "VCC"


def test_a_malformed_sheet_number_does_not_break_the_annotation() -> None:
    annotated = cli._annotate_operations([{"op": "open_sheet", "number": "two"}, {"op": "wire"}])
    assert [entry["sheet"] for entry in annotated] == [0, 0]


def test_dry_run_publishes_the_planned_operations(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    design = tmp_path / "design.json"
    design.write_text(
        json.dumps(
            {
                "sheets": [
                    {
                        "number": 1,
                        "title": "One",
                        "parts": [
                            {"refdes": "R1", "device": "RES", "value": "1k", "x": 100, "y": 100}
                        ],
                        "nets": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    code = cli.main(
        [
            "schematic",
            "draw",
            "--project",
            str(tmp_path / "p.prj"),
            "--design",
            str(design),
            "--dry-run",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["ok"], result
    operations = result["data"]["operations"]
    assert operations, "the plan's operations must be published, not only its summary"
    assert all("index" in entry and "sheet" in entry for entry in operations)
    assert [entry["index"] for entry in operations] == list(range(len(operations)))


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    binary = tmp_path / "native-adapter.exe"
    binary.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(binary))
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, '{"ok":true,"data":{"ready":true}}', "")

    monkeypatch.setattr(native_xpedition.subprocess, "run", fake_run)
    return seen


def test_adapter_progress_streams_to_our_stderr_by_default(adapter) -> None:
    native_xpedition.suppress_progress(False)
    native_xpedition.NativeBackend().invoke("health", {})
    # Inheriting stderr is what makes progress live; capturing it would hold
    # every line until the call returned.
    assert adapter["stderr"] is None
    assert adapter["stdout"] is subprocess.PIPE


def test_quiet_captures_the_adapter_progress_instead(adapter) -> None:
    native_xpedition.suppress_progress(True)
    try:
        native_xpedition.NativeBackend().invoke("health", {})
        assert adapter["stderr"] is subprocess.PIPE
    finally:
        native_xpedition.suppress_progress(False)


def test_the_quiet_flag_reaches_the_backend(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    try:
        cli.main(["version", "--quiet"])
        assert native_xpedition.progress_suppressed() is True
        cli.main(["version"])
        assert native_xpedition.progress_suppressed() is False
    finally:
        native_xpedition.suppress_progress(False)
        capsys.readouterr()
