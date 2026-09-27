"""A command that pages declares it, and a command that declares paging pages.

`reference` is what an agent plans with: a list it can page without knowing is
fetched whole, and a `--limit` it was promised but that is ignored hands back
more than it asked for. This runs every query command against a project with
three of everything and checks both directions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xpedition_cli import main as cli

# what a command needs besides the project before it answers
EXTRA_ARGS = {
    "schematic query": ["--query", "R"],
    "pcb query": ["--query", "R"],
    "constraints query": ["--query", "N"],
    "library search": ["--query", "P"],
    "agent query": ["--query", "R"],
}
# commands that start or read Xpedition, or read files other than a project
OUT_OF_SCOPE = ("session", "kb", "system api-inventory", "pcb placement", "schematic pin-")


def _three(make):
    return [make(index) for index in range(1, 4)]


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(tmp_path / "no-such-adapter"))
    pins = [{"number": str(n), "name": f"P{n}", "net": f"N{n}"} for n in (1, 2)]
    data = {
        "project": "paging",
        "revision": "R01",
        "sheets": _three(lambda i: {"id": str(i), "name": f"S{i}"}),
        "components": _three(
            lambda i: {"refdes": f"R{i}", "part_number": "RES-10K", "value": "10k", "pins": pins}
        ),
        "nets": _three(lambda i: {"name": f"N{i}", "pins": [f"R{i}.1"]}),
        "connections": _three(lambda i: {"net": f"N{i}", "refdes": f"R{i}", "pin": "1"}),
        "interfaces": _three(lambda i: {"name": f"I{i}"}),
        "bom": _three(lambda i: {"refdes": f"R{i}", "part_number": "RES-10K", "quantity": 1}),
        "constraints": _three(lambda i: {"id": f"C{i}", "net": f"N{i}", "type": "width"}),
        "pcb": {
            key: _three(lambda i, key=key: {"name": f"{key}{i}", "net": f"N{i}", "refdes": f"R{i}"})
            for key in (
                "components",
                "footprints",
                "nets",
                "layers",
                "stackup",
                "tracks",
                "vias",
                "zones",
                "keepouts",
            )
        },
        "library": {
            key: _three(lambda i, key=key: {"name": f"{key}{i}", "part_number": f"P{i}"})
            for key in ("parts", "symbols", "footprints", "padstacks", "models")
        },
        "analysis": {
            key: _three(lambda i, key=key: {"id": f"{key}{i}"})
            for key in ("erc", "drc", "dfm", "results")
        },
        "manufacturing": {"artifacts": _three(lambda i: {"name": f"out{i}.gbr", "kind": "gerber"})},
    }
    path = tmp_path / "paging.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_paging_is_declared_where_it_happens_and_honoured_where_declared(capsys, project) -> None:
    _, reference = _run(capsys, "reference", "--compact")
    checked, problems = [], []
    for command in reference["data"]["commands"]:
        path = command["path"]
        if command["type"] == "write" or path.startswith(OUT_OF_SCOPE):
            continue
        declared = {param["name"] for param in command.get("params", [])}
        argv = [*path.split(), "--backend", "mock", "--project", str(project)]
        code, result = _run(capsys, *argv, *EXTRA_ARGS.get(path, []), "--limit", "1")
        if code != 0:
            continue
        data = result["data"]
        if isinstance(data, dict) and "has_more" in data:
            checked.append(path)
            if not {"limit", "offset"} <= declared:
                problems.append(f"{path}: pages but reference does not declare --limit/--offset")
            if not command.get("default_sort"):
                problems.append(f"{path}: pages but declares no default_sort")
            listed = data["items"] if "items" in data else data["findings"]
            if len(listed) > 1:
                problems.append(f"{path}: ignored --limit 1")
        elif "limit" in declared:
            problems.append(f"{path}: declares --limit but its result does not page")
    assert not problems, "; ".join(problems)
    # a vacuous pass would hide a broken fixture
    assert len(checked) >= 40, checked
    assert "manufacturing bom" in checked and "constraints export" in checked
