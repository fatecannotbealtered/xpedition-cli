"""Public reference must describe the dedicated path that the source actually implements."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from xpedition_cli.backends import NativeBackend
from xpedition_cli.cli.registry import by_path
from xpedition_cli.driver import runtime

ROOT = Path(__file__).resolve().parents[1]


def test_public_designer_start_reference_matches_source_without_native_calls(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Reference must not query or start CAD")

    monkeypatch.setattr(NativeBackend, "invoke", forbidden)
    monkeypatch.setattr(NativeBackend, "status", forbidden)
    with runtime(state_dir=tmp_path / "state") as cli:
        output = cli.run(["reference", "--command", "session start", "--quiet"])
    assert output.envelope["ok"] and output.exit_code == 0
    rows = []

    def visit(value):
        if isinstance(value, dict):
            if value.get("path") == "session start":
                rows.append(value)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(output.envelope["data"])
    assert len(rows) == 1
    assert rows[0]["description"] == by_path()["session start"].description
    assert "Designer start is disabled" not in rows[0]["description"]
    assert "authenticated startup host" in rows[0]["description"]
    assert any("schematic --dry-run" in example for example in rows[0]["examples"])


@pytest.mark.parametrize(
    "relative",
    [
        "registry.json",
        "development-registry.json",
        "development-current-registry.json",
        "development/current-command-baseline.json",
    ],
)
def test_data_catalogs_do_not_reintroduce_the_retired_disabled_description(relative):
    catalog = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    command = next(row for row in catalog["commands"] if row["path"] == "session start")
    assert command["description"] == by_path()["session start"].description
    assert command["examples"] == list(by_path()["session start"].examples)


@pytest.mark.parametrize("example", by_path()["reference"].examples)
def test_advertised_reference_examples_use_the_real_public_builtin(tmp_path, monkeypatch, example):
    monkeypatch.setattr(
        NativeBackend, "invoke", lambda *args, **kwargs: pytest.fail("Reference invoked CAD")
    )
    with runtime(state_dir=tmp_path / "state") as cli:
        result = cli.run([*shlex.split(example)[1:], "--quiet"])
    assert result.envelope["ok"] and result.exit_code == 0
