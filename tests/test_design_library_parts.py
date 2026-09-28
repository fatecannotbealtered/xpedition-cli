"""A design's symbols may name parts of the central library: the drawing places the
library's own symbol with the library's part number, and the build makes nothing up
for them -- nor lets a placeholder replace them."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fakes import LIBRARY_PARTS, FakeLibrary

from xpedition_cli import library_hkp as H
from xpedition_cli import library_parts as P
from xpedition_cli import native_com_adapter as adapter
from xpedition_cli import schematic_layout as L

MCU_PINS = {
    "1": "power:+3V3",
    "2": "label:PA0",
    "3": "label:PA1",
    "4": "gnd",
    "5": "label:SWCLK",
    "6": "label:SWDIO",
    "7": "nc",
    "8": "nc",
}


def _design() -> dict:
    return {
        "sheet_size": "A4",
        "symbols": {"MCU": {"part": "MCU-8"}},
        "sheets": [
            {
                "number": 1,
                "blocks": [
                    {
                        "kind": "ic",
                        "refdes": "U1",
                        "symbol": "MCU",
                        "value": "ignored for a library part",
                        "x": 400,
                        "y": 400,
                        "pins": MCU_PINS,
                    },
                    {
                        "kind": "ladder",
                        "x": 700,
                        "y": 500,
                        "path": [
                            "power:+3V3",
                            {"refdes": "R1", "symbol": "RES", "value": "10k"},
                            "label:PA0",
                        ],
                    },
                ],
            }
        ],
    }


def _parts() -> dict:
    plan = P.plan(LIBRARY_PARTS)
    name = next(n for n in plan.symbols if n.startswith("MCU-8"))
    return {
        "MCU-8": {
            "partition": "PartQuest",
            "symbol": name,
            "text": plan.symbols[name],
            "cell": "SOIC127P600X175-8N",
        }
    }


def test_the_names_a_design_gives_are_found() -> None:
    assert L.library_part_numbers(_design()) == ["MCU-8"]
    assert L.library_part_numbers({"symbols": {"A": {"kind": "box"}}}) == []
    assert L.refdes_by_sheet(_design()) == {"U1": 1, "R1": 1}


def test_a_library_part_is_placed_with_its_own_symbol_and_number() -> None:
    parts = _parts()
    plan = L.plan(_design(), parts)
    place = next(op for op in plan.ops if op.get("op") == "place_part" and op["refdes"] == "U1")
    assert place["library"] == "PartQuest" and place["part"] == "MCU-8"
    assert place["symbol"] == parts["MCU-8"]["symbol"]
    # its file is the library's: drawn in the preview, never written again
    assert place["symbol"] not in plan.symbols and place["symbol"] in plan.library_symbols
    u1 = next(p for p in plan.parts if p["refdes"] == "U1")
    assert u1["part"] == "MCU-8" and u1["library"] == "PartQuest"
    assert {"U1.1", "R1.1"} <= plan.nets["+3V3"]
    assert "U1.7" in plan.no_connects


def test_a_design_naming_a_part_it_was_not_given_is_refused() -> None:
    with pytest.raises(L.DesignError, match="was not read from the library"):
        L.plan(_design())
    parts = _parts()
    lines = parts["MCU-8"]["text"].splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("P 10 "))
    fields = lines[index].split()
    fields[2] = str(int(fields[2]) + 5)  # the first pin's end, half a grid step off
    lines[index] = " ".join(fields)
    parts["MCU-8"]["text"] = "\n".join(lines)
    with pytest.raises(L.DesignError, match="off the 10-unit grid"):
        L.plan(_design(), parts)


def test_the_build_makes_up_nothing_for_a_library_part() -> None:
    library = H.plan_library(_design(), _parts())
    assert "MCU-8" not in library.parts and "10k" in library.parts
    mapped = next(row for row in library.mapping if row["refdes"] == "U1")
    assert mapped["library_part"] is True and mapped["cell"] == "SOIC127P600X175-8N"


def test_the_preview_draws_the_library_symbol(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from xpedition_cli import schematic_render

    plan = L.plan(_design(), _parts())
    layout = schematic_render.layout_sheet(plan, 1, "A4")
    assert "U1" in layout.parts and any(t.text == "VDD" for t in layout.texts)


@pytest.fixture
def library(adapter, tmp_path) -> FakeLibrary:
    fake = FakeLibrary(tmp_path / "Lib")
    adapter.on("library_export", fake.export)
    adapter.on("library_import", fake.absorb)
    return fake


def _design_file(tmp_path: Path, design: dict) -> Path:
    path = tmp_path / "design.json"
    path.write_text(json.dumps(design), encoding="utf-8")
    return path


def test_build_reads_the_named_parts_and_refuses_to_overwrite_real_ones(
    cli, adapter, library, project, tmp_path
) -> None:
    design = _design_file(tmp_path, _design())
    args = ["library", "build", "--project", str(project), "--design", str(design)]
    code, payload = cli(*args, "--dry-run")
    preview = payload["data"]["preview"]
    assert code == 0 and preview["library_parts"] == ["MCU-8"]
    assert "MCU-8" not in preview["summary"]["parts"]
    clash = copy.deepcopy(_design())
    clash["sheets"][0]["blocks"][1]["path"][1]["value"] = "RES-10K"
    code, payload = cli(
        "library",
        "build",
        "--project",
        str(project),
        "--design",
        str(_design_file(tmp_path, clash)),
        "--dry-run",
    )
    assert code == 6 and payload["error"]["code"] == "E_CONFLICT"
    assert payload["error"]["details"]["parts"] == ["RES-10K"]
    assert '"part"' in payload["error"]["details"]["hint"]


def test_render_and_draw_read_the_named_parts(cli, adapter, library, project, tmp_path) -> None:
    pytest.importorskip("PIL")
    design = _design_file(tmp_path, _design())
    output = tmp_path / "sheet.png"
    code, payload = cli("schematic", "render", "--design", str(design), "--output", str(output))
    assert code == 2 and payload["error"]["code"] == "E_USAGE"
    assert "--project" in payload["error"]["message"]
    code, payload = cli(
        "schematic",
        "render",
        "--design",
        str(design),
        "--output",
        str(output),
        "--project",
        str(project),
    )
    assert code == 0 and output.is_file()
    code, payload = cli(
        "schematic", "draw", "--project", str(project), "--design", str(design), "--dry-run"
    )
    assert code == 0
    missing = copy.deepcopy(_design())
    missing["symbols"]["MCU"]["part"] = "NOT-THERE"
    code, payload = cli(
        "schematic",
        "draw",
        "--project",
        str(project),
        "--design",
        str(_design_file(tmp_path, missing)),
        "--dry-run",
    )
    assert payload["error"]["code"] == "E_VALIDATION"
    assert payload["error"]["details"]["parts"] == ["NOT-THERE"]


def test_the_draw_lists_every_partition_its_parts_come_from(tmp_path, monkeypatch) -> None:
    project = tmp_path / "Board.prj"
    project.write_text("", encoding="utf-8")
    root = tmp_path / "Lib"
    (root / "PartsDBLibs").mkdir(parents=True)
    (root / "PartsDBLibs" / "Vendor.pdb").write_bytes(b"db")
    listed: list[tuple[str, str]] = []
    monkeypatch.setattr(adapter, "_symbol_library_root", lambda path: root / "SymbolLibs")
    monkeypatch.setattr(adapter, "_prj_lists", lambda path, entry: "PartQuest" in entry)
    monkeypatch.setattr(
        adapter, "_ensure_prj_list", lambda path, name, entry: listed.append((name, entry))
    )
    monkeypatch.setattr(adapter, "_close_if_open", lambda app, path: False)

    class Stop(Exception):
        pass

    def stop(app, path):
        raise Stop

    monkeypatch.setattr(adapter, "_viewdraw_application", lambda client, **kw: object())
    monkeypatch.setattr(adapter, "_ensure_project", stop)
    ops = [
        {"op": "open_sheet", "number": 1},
        {"op": "place_part", "refdes": "U1", "library": "Vendor", "symbol": "X", "part": "N"},
    ]
    with pytest.raises(Stop):
        adapter._draw({"project": str(project), "ops": ops, "library": "PartQuest"}, None)
    assert listed == [("Symbols", "SymbolLibs\\Vendor"), ("PDBs", "PartsDBLibs\\Vendor.pdb")]
