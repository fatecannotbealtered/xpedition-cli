"""Every sheet the planner draws is landscape, so every page it asks for must be too."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from xpedition_cli import native_com_adapter as adapter
from xpedition_cli import pdf_pages
from xpedition_cli import schematic_layout as L

LANDSCAPE_FAMILY = range(11, 21)  # VDSHEET_AL_SIZE ... VDSHEET_A0L_SIZE
# plain codes recorded printing a landscape page: A4 842 x 595 pt, A3 1190 x 842 pt
RECORDED_LANDSCAPE = {5, 6}


def _one_part(size: str) -> dict:
    return {
        "sheet_size": size,
        "sheets": [
            {
                "number": 1,
                "title": "ONE",
                "blocks": [
                    {
                        "kind": "ladder",
                        "x": 300,
                        "y": 500,
                        "path": [
                            "power:+3V3",
                            {"refdes": "R1", "symbol": "RES", "value": "10k"},
                            "gnd",
                        ],
                    }
                ],
            }
        ],
    }


def test_every_sheet_size_asks_for_a_landscape_page() -> None:
    assert set(L.SHEET_SIZES) == set(L.SHEET_BORDERS)
    for size, (width, height) in L.SHEET_SIZES.items():
        border, code = L.SHEET_BORDERS[size]
        assert width > height, size
        # the border symbols without a suffix are the landscape ones
        assert border == f"{size.lower()}sheet", size
        assert code in LANDSCAPE_FAMILY or code in RECORDED_LANDSCAPE, size


def test_the_ansi_sizes_leave_the_portrait_family() -> None:
    # VDSHEET_CSIZE (2) printed a landscape C border on a 17 x 22 in page
    assert {size: L.SHEET_BORDERS[size][1] for size in "ABCDE"} == {
        "A": 11,
        "B": 12,
        "C": 13,
        "D": 14,
        "E": 15,
    }


@pytest.mark.parametrize(
    ("size", "border", "code", "measured"),
    [
        ("C", "csheet", 13, False),
        ("A4", "a4sheet", 5, True),
        ("A2", "a2sheet", 18, False),
        ("A0", "a0sheet", 20, False),
    ],
)
def test_a_design_s_sheet_size_reaches_the_draw(size, border, code, measured) -> None:
    params = L.plan_to_params(_one_part(size), "X.prj")
    [set_sheet] = [op for op in params["ops"] if op["op"] == "set_sheet"]
    assert (set_sheet["border"], set_sheet["size"]) == (border, code)
    width, height = L.SHEET_SIZES[size]
    x1, y1, x2, y2 = params["summary"]["usable"]["1"]
    assert 0 < x1 < x2 < width and 0 < y1 < y2 < height
    # a size without a measured title block says its usable area runs into it
    assert params["summary"]["usable_measured"] is measured


def test_page_sizes_are_read_back_from_the_pdf() -> None:
    data = (
        b"%PDF-1.4\n1 0 obj << /Type /Page /MediaBox [0 0 1224 1584] >> endobj\n"
        b"2 0 obj << /Type /Page /MediaBox [ 0 0 1190.55 841.89 ] >> endobj\n"
    )
    found = pdf_pages.pages(data)
    assert [(p["orientation"], p["width_mm"], p["height_mm"]) for p in found] == [
        ("portrait", 431.8, 558.8),
        ("landscape", 420.0, 297.0),
    ]
    [warning] = pdf_pages.warnings(found)
    assert warning.startswith("page 1 is portrait (1224 x 1584 pt)")


def test_an_export_reports_a_page_its_border_does_not_fit(tmp_path, monkeypatch) -> None:
    project = tmp_path / "Board.prj"
    project.write_text("", encoding="utf-8")
    home = tmp_path / "SDD_HOME"
    binary = home / "common" / "win64" / "bin" / "sch2pdf.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"")
    output = tmp_path / "out.pdf"

    def fake_sch2pdf(arguments, **kwargs):
        # a C sheet on the portrait C page: 17 x 22 in
        output.write_bytes(b"%PDF-1.4\n<< /Type /Page /MediaBox [0 0 1224 1584] >>\n")
        return subprocess.CompletedProcess(arguments, 0, b"Printing Schematic1 Sheet 1\n", b"")

    monkeypatch.setattr(adapter, "_configure_environment", lambda: home)
    monkeypatch.setattr(adapter.subprocess, "run", fake_sch2pdf)
    result = adapter._export_pdf({"project": str(project), "output": str(output)})
    assert result["exported"] is True
    assert result["pages"][0]["orientation"] == "portrait"
    assert "clipped" in result["warnings"][0]


def test_the_example_designs_still_plan_on_their_recorded_a4_code() -> None:
    example = Path(__file__).resolve().parent.parent / "examples" / "demo-sensor-board.json"
    params = L.plan_to_params(json.loads(example.read_text(encoding="utf-8")), "X.prj")
    sizes = {op["size"] for op in params["ops"] if op["op"] == "set_sheet"}
    assert sizes == {5}


def test_a_landscape_page_code_reads_back_as_its_plain_size() -> None:
    # CL_SIZE (13) set, CSIZE (2) read, and the page exported landscape: a set that took
    taken = adapter.sheet_size_taken
    assert taken(2, 13) and taken(1, 12) and taken(9, 20) and taken(2, 23)
    assert taken(5, 5) and taken(13, 13)
    assert not taken(3, 13) and not taken(2, 5)
