"""`schematic render` and DS-17: a planned sheet as a picture, and the texts it overlaps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xpedition_cli import main as cli
from xpedition_cli import schematic_layout as L
from xpedition_cli import schematic_render as R
from xpedition_cli import symbols as S

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "demo-sensor-board.json"


def _design() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _block(design: dict, refdes: str) -> dict:
    for sheet in design["sheets"]:
        for block in sheet["blocks"]:
            if refdes in json.dumps(block):
                return block
    raise KeyError(refdes)


def _texts(symbol: S.Symbol) -> list[R.Text]:
    art = R.parse_symbol(symbol.render())
    return [
        R.Text(x, y, value, size, anchor, "symbol", symbol.name)
        for x, y, size, anchor, _name, value, visible in art.texts
        if visible and value
    ]


# -- the symbols the planner generates ------------------------------------------------


def test_a_power_symbol_writes_its_net_above_its_bar() -> None:
    # anchored by its upper centre the name hung through the bar, in Designer too
    art = R.parse_symbol(S.power_symbol("+3V3").render())
    [netname] = [t for t in art.texts if t[4] == "NETNAME"]
    x, y, size, anchor = netname[0], netname[1], netname[2], netname[3]
    bar = max(point[1] for line in art.lines for point in line)
    box = R.Text(x, y, "+3V3", size, anchor, "symbol", "power").box()
    assert anchor == 6 and box[1] > bar


def test_a_box_keeps_its_side_rows_clear_of_top_and_bottom_pin_names() -> None:
    # VDD on the top edge was written over the first left pin's name
    for rows in (2, 3, 4):
        left = [(str(i), f"IN{i}") for i in range(1, rows + 1)]
        right = [(str(i + 10), f"OUT{i}") for i in range(1, rows + 1)]
        symbol = S.box("CHIP", left, right, top=[("20", "VDD")], bottom=[("21", "GND")])
        boxes = [text.box() for text in _texts(symbol)]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                assert R._area(a, b) == 0, (rows, a, b)


# -- DS-17 -----------------------------------------------------------------------------


def test_texts_that_collide_are_reported() -> None:
    layout = R.SheetLayout(1, 1169, 827, [60, 150, 1110, 700])
    layout.texts = [
        R.Text(100, 300, "ABC", 8, 3, "text", "first"),
        R.Text(104, 302, "DEF", 8, 3, "text", "second"),
        R.Text(300, 300, "CLEAR", 8, 3, "text", "third"),
    ]
    layout.lines = [R.Line([(90, 304), (130, 304)], "wire", "wire U1.1")]
    layout.boxes = [
        R.Box((96, 296, 140, 312), "label", "label NET"),
        R.Box((290, 290, 400, 350), "frame", "frame"),  # CLEAR sits inside: fine
        R.Box((280, 302, 400, 400), "frame", "frame"),  # this edge runs through CLEAR
    ]
    messages = {issue["message"] for issue in R.text_overlaps(layout)}
    assert "first: text 'ABC' overlaps text of second 'DEF'" in messages
    assert "first: text 'ABC' is crossed by wire U1.1 (wire)" in messages
    assert "first: text 'ABC' overlaps the box of label NET" in messages
    assert "third: text 'CLEAR' is crossed by a frame's edge" in messages
    assert all(
        issue["check"] == "DS-17" and issue["sheet"] == 1 for issue in R.text_overlaps(layout)
    )
    # a label's own text sits in its own box
    layout.texts = [R.Text(100, 300, "NET", 8, 3, "label", "label NET")]
    layout.lines = []
    assert R.text_overlaps(layout) == []


def test_the_example_plans_clean_and_its_old_layout_did_not() -> None:
    assert L.plan(_design()).issues == []
    design = _design()
    # the pull-up where it was: its label ran into the MCU's label of the same net
    _block(design, "R201")["x"] = 240
    issues = L.plan(design).issues
    assert [(i["check"], i["object"], i["text"]) for i in issues] == [
        ("DS-17", "label MCU_RST_N", "MCU_RST_N")
    ]


def test_a_label_past_the_drawable_area_is_reported() -> None:
    design = _design()
    _block(design, "U202")["x"] = 960  # where it was: its alert label ran off the sheet
    issues = [i for i in L.plan(design).issues if i["check"] == "DS-07"]
    assert [(i["object"], i["text"]) for i in issues] == [
        ("label SENSOR_ALERT_N", "SENSOR_ALERT_N")
    ]


def test_a_part_s_refdes_moves_off_its_own_power_symbol() -> None:
    plan = L.plan(_design())
    [j201] = [op for op in plan.ops if op["op"] == "place_part" and op["refdes"] == "J201"]
    attributes = {a["name"]: a for a in j201["attributes"]}
    # the first choice, above the header's upper-left, is where the +3V3 symbol of its
    # first pin stands; the refdes went to the next free spot, horizontally
    assert attributes["Ref Designator"]["origin"] != 8
    assert attributes["Ref Designator"]["orientation"] == 0
    assert attributes["Part Number"]["orientation"] == 0
    assert not [i for i in plan.issues if i.get("object") == "J201"]


# -- the picture ------------------------------------------------------------------------


def test_render_writes_one_picture_per_sheet_and_never_overwrites(tmp_path: Path) -> None:
    plan = L.plan(_design())
    pictures = R.render(plan, "A4", tmp_path / "plan.png")
    assert [p["sheet"] for p in pictures] == [1, 2, 3, 4]
    for picture in pictures:
        data = Path(picture["path"]).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and picture["width"] == 2339
    assert pictures[2]["parts"] == 8 and pictures[2]["labels"] == 13
    with pytest.raises(FileExistsError):
        R.render(plan, "A4", tmp_path / "plan.png")
    [single] = R.render(plan, "A4", tmp_path / "three.png", sheets=[3], scale=1)
    assert single["path"] == str(tmp_path / "three.png") and single["width"] == 1170


# -- the command -------------------------------------------------------------------------


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main([*argv, "--compact"])
    return code, json.loads(capsys.readouterr().out)


def test_schematic_render_pictures_a_design_before_it_is_drawn(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    output = tmp_path / "preview.png"
    code, result = _run(
        capsys, "schematic", "render", "--design", str(EXAMPLE), "--output", str(output)
    )
    assert code == 0 and result["data"]["summary"] == {"sheets": 4, "issues": 0}
    assert (tmp_path / "preview-sheet2.png").is_file()
    code, result = _run(
        capsys, "schematic", "render", "--design", str(EXAMPLE), "--output", str(output)
    )
    assert code == 6 and result["error"]["code"] == "E_CONFLICT"
    code, result = _run(
        capsys,
        "schematic",
        "render",
        "--design",
        str(EXAMPLE),
        "--sheets",
        "3",
        "--output",
        str(output),
        "--replace",
    )
    assert code == 0 and [p["sheet"] for p in result["data"]["pictures"]] == [3]


def test_schematic_render_refuses_what_it_cannot_picture(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    code, result = _run(capsys, "schematic", "render", "--design", str(EXAMPLE))
    assert code == 2 and result["error"]["code"] == "E_USAGE"
    png = str(tmp_path / "x.png")
    code, result = _run(
        capsys, "schematic", "render", "--design", str(EXAMPLE), "--output", str(tmp_path / "x.jpg")
    )
    assert code == 2 and result["error"]["code"] == "E_VALIDATION"
    code, result = _run(
        capsys, "schematic", "render", "--design", str(EXAMPLE), "--sheets", "9", "--output", png
    )
    assert code == 2 and result["error"]["details"]["sheets"] == [9]
    broken = _design()
    _block(broken, "R301")["path"][1]["refdes"] = "R201"  # the same refdes on two sheets
    design = tmp_path / "broken.json"
    design.write_text(json.dumps(broken), encoding="utf-8")
    code, result = _run(capsys, "schematic", "render", "--design", str(design), "--output", png)
    assert code == 2 and "R201" in result["error"]["message"]
