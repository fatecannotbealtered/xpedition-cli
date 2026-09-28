"""A value is the part number a drawn part carries, and a part number names one part."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from xpedition_cli import library_hkp as H
from xpedition_cli import schematic_layout as L

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "demo-sensor-board.json"


def _demo() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _connectors(design: dict) -> list[dict]:
    return [
        block
        for sheet in design["sheets"]
        for block in sheet.get("blocks", [])
        if block.get("kind") == "connector"
    ]


def test_one_value_drawn_with_two_symbols_is_refused_when_planned() -> None:
    design = _demo()
    first, second = _connectors(design)[:2]
    assert first["symbol"] != second["symbol"]
    second["value"] = first["value"]
    with pytest.raises(L.DesignError, match="share the value"):
        L.plan(design)


def test_one_value_on_two_cells_is_refused_when_built() -> None:
    design = _demo()
    resistors = [
        item
        for sheet in design["sheets"]
        for block in sheet.get("blocks", [])
        for item in (block.get("path") or [])[1::2]
        if isinstance(item, dict) and item.get("symbol") == "RES"
    ]
    first, second = resistors[:2]
    second["value"] = first["value"]
    H.plan_library(design)  # the same value on the same cell is one part: fine
    design = copy.deepcopy(design)
    design["packages"] = {second["refdes"]: "0603"}
    with pytest.raises(ValueError, match="need different parts"):
        H.plan_library(design)


def test_a_build_whose_packaging_fails_is_not_ok(cli, adapter, project, tmp_path) -> None:
    from fakes import FakeLibrary

    library = FakeLibrary(tmp_path / "Lib")
    adapter.on("library_export", library.export)
    adapter.on("library_import", library.absorb)
    adapter.on("package", {"packaged": False, "errors": ["ERROR: ..."], "exit_code": 5})
    design = tmp_path / "design.json"
    design.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    args = ["library", "build", "--project", str(project), "--design", str(design), "--package"]
    _, payload = cli(*args, "--dry-run")
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert payload["data"]["ok"] is False and payload["data"]["package"]["packaged"] is False
