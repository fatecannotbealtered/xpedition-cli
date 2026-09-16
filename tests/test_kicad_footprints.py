from pathlib import Path

import pytest

from xpedition_cli import kicad_footprints as K
from xpedition_cli import library_hkp as H

SOIC = """(footprint "SOIC-8_Test"
  (version 20240108) (generator "test") (layer "F.Cu")
  (descr "SOIC, 8 Pin (JEDEC MS-012AA, https://example.org/r_8.pdf)")
  (tags "SOIC SO")
  (property "Reference" "REF**" (at 0 -3.4 0) (layer "F.SilkS")
    (effects (font (size 1 1) (thickness 0.15))))
  (property "Value" "SOIC-8" (at 0 3.4 0) (layer "F.Fab")
    (effects (font (size 1 1) (thickness 0.15))))
  (fp_text user "${REFERENCE}" (at 0 0.5 90) (layer "F.Fab")
    (effects (font (size 0.8 0.8) (thickness 0.12))))
  (attr smd)
  (fp_line (start -2.06 -2.56) (end 2.06 -2.56) (stroke (width 0.12) (type solid))
    (layer "F.SilkS"))
  (fp_line (start -1.95 -1.45) (end 1.95 -2.45) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
  (fp_line (start 1.95 -2.45) (end 1.95 2.45) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
  (fp_line (start 1.95 2.45) (end -1.95 2.45) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
  (fp_line (start -1.95 2.45) (end -1.95 -1.45) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
  (fp_poly (pts (xy -3.2 -1.0) (xy -2.8 -1.4) (xy -2.8 -0.6)) (stroke (width 0.1) (type solid))
    (fill yes) (layer "F.SilkS"))
  (fp_arc (start -1 0) (mid 0 1) (end 1 0) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
  (fp_rect (start -3.7 -2.7) (end 3.7 2.7) (stroke (width 0.05) (type solid)) (fill no)
    (layer "F.CrtYd"))
  (pad "1" smd roundrect (at -2.475 -1.905) (size 1.95 0.6) (layers "F.Cu" "F.Paste" "F.Mask")
    (roundrect_rratio 0.25))
  (pad "2" smd roundrect (at -2.475 -0.635) (size 1.95 0.6) (layers "F.Cu" "F.Paste" "F.Mask")
    (roundrect_rratio 0.25))
  (pad "3" smd rect (at -2.475 0.635 90) (size 1.95 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "4" smd oval (at -2.475 1.905) (size 1.95 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "4" smd rect (at -2.475 1.905) (size 0.5 0.5) (layers "F.Paste"))
  (pad "5" smd roundrect (at 2.475 1.905) (size 0.6 0.6) (layers "F.Cu" "F.Paste" "F.Mask")
    (roundrect_rratio 0))
  (pad "6" smd custom (at 2.475 0.635) (size 0.1 0.1) (layers "F.Cu" "F.Mask")
    (options (clearance outline) (anchor circle))
    (primitives (gr_poly (pts (xy -0.9 -0.3) (xy 0.9 -0.3) (xy 0.9 0.3) (xy -0.9 0.3))
      (width 0) (fill yes))))
  (pad "7" smd rect (at 2.475 -0.635) (size 1.95 0.6) (layers "B.Cu" "B.Mask"))
  (pad "8" smd rect (at 2.475 -1.905) (size 1.95 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "8" smd rect (at 3.475 -1.905) (size 0.4 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))
)
"""

JST = """(footprint "JST_Test"
  (layer "F.Cu") (descr "JST PH series connector, B4B-PH-K, top entry")
  (property "Reference" "REF**" (at 3 -2.9 0) (layer "F.SilkS")
    (effects (font (size 1 1) (thickness 0.15))))
  (attr through_hole)
  (fp_line (start -2.45 -3.3) (end 8.45 -3.3) (stroke (width 0.05) (type solid))
    (layer "F.CrtYd"))
  (fp_line (start 8.45 -3.3) (end 8.45 2.2) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start 8.45 2.2) (end -2.45 2.2) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start -2.45 2.2) (end -2.45 -3.3) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_circle (center 3 -0.5) (end 3.5 -0.5) (stroke (width 0.12) (type solid)) (fill no)
    (layer "F.SilkS"))
  (pad "1" thru_hole roundrect (at 0 0) (size 1.2 1.75) (drill 0.75) (layers "*.Cu" "*.Mask")
    (roundrect_rratio 0.208333))
  (pad "2" thru_hole oval (at 2 0) (size 1.2 1.75) (drill 0.75) (layers "*.Cu" "*.Mask"))
  (pad "3" thru_hole oval (at 4 0 90) (size 1.2 1.75) (drill oval 0.7 1.0)
    (layers "*.Cu" "*.Mask"))
  (pad "" np_thru_hole circle (at 6 -1.5) (size 1.2 1.2) (drill 1.2) (layers "*.Cu" "*.Mask"))
  (pad "" thru_hole circle (at 6 1.5) (size 1.6 1.6) (drill 1.0) (layers "*.Cu" "*.Mask"))
)
"""

HOLE = """(footprint "MountingHole_Test" (layer "F.Cu")
  (descr "Mounting Hole 2.2mm, M2, no annular")
  (attr exclude_from_pos_files exclude_from_bom)
  (property "Reference" "REF**" (at 0 -3.15 0) (layer "F.SilkS")
    (effects (font (size 1 1) (thickness 0.15))))
  (fp_circle (center 0 0) (end 2.45 0) (stroke (width 0.05) (type solid)) (fill no)
    (layer "F.CrtYd"))
  (pad "" np_thru_hole circle (at 0 0) (size 2.2 2.2) (drill 2.2) (layers "*.Cu" "*.Mask"))
)
"""


def _cell(text: str, library: str = "Package_SO"):
    plan = H.LibraryPlan(partition="KiCad")
    footprint = K.read_footprint(text)
    cell, issues = K.to_cell(footprint, H._Stock(plan), library)
    return plan, cell, issues


def test_parser_handles_quotes_and_nesting() -> None:
    tree = K.parse('(a "b c" (d 1.5 (e "q\\"x")) f)')
    assert tree == [["a", "b c", ["d", "1.5", ["e", 'q"x']], "f"]]
    with pytest.raises(ValueError):
        K.parse("(a (b)")


def test_soic_pads_flip_y_keep_rotation_and_drop_pads_without_front_copper() -> None:
    plan, cell, issues = _cell(SOIC)
    assert cell.name == "SOIC-8_Test"
    assert cell.group == "IC_SOIC" and cell.mount == "SURFACE"
    numbers = [pin.number for pin in cell.pins]
    assert numbers == ["1", "2", "3", "4", "5", "6", "8"]  # 7 is back-side only
    pin1 = cell.pins[0]
    assert (pin1.x, pin1.y) == (-2.475, 1.905)  # KiCad y down, Xpedition y up
    assert pin1.padstack == "SMD-RRECT1.95X0.6R0.15"
    assert cell.pins[2].rotation == 90
    assert cell.pins[3].padstack == "SMD-OBL1.95X0.6"
    assert cell.pins[4].padstack == "SMD-RECT0.6X0.6"  # a zero-ratio roundrect is a rectangle
    assert cell.pins[5].padstack == "SMD-RECT1.8X0.6"  # custom pad: box around its primitives
    assert cell.pins[6].x == 2.475  # the first pad "8" stays, the second is dropped
    assert issues == ["SOIC-8_Test: pads dropped (back_side 1, duplicate_number 1, no_copper 1)"]
    assert plan.pads["RRECT1.95X0.6R0.15"].radius == 0.15
    assert plan.pads["RRECT2.05X0.7R0.2"].shape == "RADIUS_CORNER_RECTANGLE"  # the mask


def test_soic_graphics_become_outline_blocks_and_a_placed_reference() -> None:
    _, cell, _ = _cell(SOIC)
    blocks = [(g.block, g.path) for g in cell.graphics]
    assert blocks.count(("SILKSCREEN_OUTLINE", "POLYLINE_PATH")) == 2  # the line and the arc
    assert ("SILKSCREEN_OUTLINE", "POLYLINE_SHAPE") in blocks  # the filled pin-1 triangle
    assert blocks.count(("ASSEMBLY_OUTLINE", "POLYLINE_PATH")) == 1  # one body loop
    assert ("PLACEMENT_OUTLINE", "RECT_PATH") in blocks
    arc = next(g for g in cell.graphics if g.path == "POLYLINE_PATH" and len(g.points) > 2)
    assert arc.points[0] == (-1.0, 0.0) and arc.points[-1] == (1.0, 0.0)
    assert len(arc.points) >= 10 and abs(arc.points[len(arc.points) // 2][1] + 1.0) < 0.01
    body = next(g for g in cell.graphics if g.block == "ASSEMBLY_OUTLINE")
    assert body.points[0] == body.points[-1] and len(body.points) == 5
    assert cell.body == (-3.7, -2.7, 3.7, 2.7)
    assert cell.refdes == H.RefDesText(0.0, 3.4, 1.0, 0.15, 0.0)
    assert cell.refdes_assembly == H.RefDesText(0.0, -0.5, 0.8, 0.12, 90.0)
    assert cell.description.startswith("SOIC, 8 Pin")


def test_through_hole_connector_gets_holes_slots_and_a_chained_courtyard() -> None:
    plan, cell, issues = _cell(JST, "Connector_JST")
    assert cell.group == "CONNECTOR" and cell.mount == "THROUGH"
    assert [pin.number for pin in cell.pins] == ["1", "2", "3"]
    assert cell.pins[0].padstack == "TH-RRECT1.2X1.75R0.25-C0.75-PLATED"
    assert cell.pins[1].padstack == "TH-OBL1.2X1.75-C0.75-PLATED"
    assert cell.pins[2].padstack == "TH-OBL1.2X1.75-S0.7X1-PLATED"
    assert plan.holes["S0.7X1-PLATED"].slot == (0.7, 1.0)
    assert [hole.padstack for hole in cell.holes] == ["MH-C1.2-NONPLATED", "MH-C1-PLATED"]
    assert cell.holes[1].y == -1.5
    assert issues == []
    placement = next(g for g in cell.graphics if g.block == "PLACEMENT_OUTLINE")
    assert placement.path == "POLYLINE_PATH" and len(placement.points) == 5
    assert cell.body == (-2.45, -2.2, 8.45, 3.3)
    circle = next(g for g in cell.graphics if g.path == "CIRCLE_PATH")
    assert circle.points == ((3.0, 0.5),) and circle.radius == 0.5


def test_mounting_hole_is_a_mechanical_cell_with_a_round_courtyard() -> None:
    _, cell, _ = _cell(HOLE, "MountingHole")
    assert cell.mechanical and cell.pins == [] and len(cell.holes) == 1
    placement = next(g for g in cell.graphics if g.block == "PLACEMENT_OUTLINE")
    assert len(placement.points) == 25  # a 24-gon around the circle, closed


def test_rendered_cell_text_carries_graphics_holes_and_reference_designator() -> None:
    plan = H.LibraryPlan(partition="KiCad")
    stock = H._Stock(plan)
    for text, library in ((SOIC, "Package_SO"), (JST, "Connector_JST"), (HOLE, "MountingHole")):
        cell, _ = K.to_cell(K.read_footprint(text), stock, library)
        plan.cells[cell.name] = cell
    cells = H.render_cells(plan)
    assert '.PACKAGE_CELL "SOIC-8_Test"' in cells
    assert '..TEXT "Ref Des"\n...TEXT_TYPE REF_DES\n...DISPLAY_ATTR\n....XY (0, 3.4)' in cells
    assert "....HEIGHT 1\n....WIDTH 3\n....STROKE_WIDTH 0.15" in cells
    assert "....XY (0, -0.5)\n....TEXT_LYR ASSEMBLY_MNT_LYR" in cells  # a second DISPLAY_ATTR
    jst = cells[cells.index('.PACKAGE_CELL "JST_Test"') :]
    assert "....TEXT_LYR ASSEMBLY_MNT_LYR" in jst  # centred in the body when KiCad has none
    triangle = (
        "....XY (-3.2, 1)\n      (-2.8, 1.4)\n      (-2.8, 0.6)\n      (-3.2, 1)"
        "\n....SHAPE_OPTIONS FILLED"
    )
    assert "...POLYLINE_SHAPE\n" + triangle in cells
    assert "...CIRCLE_PATH\n....WIDTH 0.12\n....XY (3, 0.5)\n....RADIUS 0.5" in cells
    assert '..MOUNTING_HOLE\n...PADSTACK "MH-C1.2-NONPLATED"\n...XY (6, 1.5)' in cells
    assert '.MECHANICAL_CELL "MountingHole_Test"' in cells
    assert "..MOUNT_TYPE THROUGH" in cells and "..PACKAGE_GROUP CONNECTOR" in cells
    padstacks = H.render_padstacks(plan)
    rounded = "..RADIUS_CORNER_RECTANGLE\n...WIDTH 1.95\n...HEIGHT 0.6\n...RADIUS 0.15"
    assert '.PAD "RRECT1.95X0.6R0.15"\n' + rounded in padstacks
    assert '.PAD "OBL1.95X0.6"\n..OBLONG\n...WIDTH 1.95\n...HEIGHT 0.6' in padstacks
    assert '.HOLE "S0.7X1-PLATED"\n..SLOT\n...WIDTH 0.7\n...HEIGHT 1' in padstacks
    assert '.PADSTACK "MH-C1-PLATED"\n..PADSTACK_TYPE MOUNTING_HOLE' in padstacks


def test_thermal_pad_keeps_its_largest_copper_piece_not_its_first_via() -> None:
    text = SOIC.replace(
        '  (pad "8" smd rect (at 2.475 -1.905) (size 1.95 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))',
        '  (pad "8" thru_hole circle (at 0 0) (size 0.55 0.55) (drill 0.25) (layers "*.Cu"))\n'
        '  (pad "8" smd rect (at 0 0) (size 1.65 2.85) (layers "F.Cu" "F.Mask"))\n'
        '  (pad "8" smd rect (at 0 0.5) (size 0.7 0.7) (layers "F.Paste"))',
    )
    _, cell, issues = _cell(text)
    exposed = next(pin for pin in cell.pins if pin.number == "8")
    assert exposed.padstack == "SMD-RECT1.65X2.85"  # the pad, not the via drilled first
    assert cell.mount == "SURFACE"
    assert issues == ["SOIC-8_Test: pads dropped (back_side 1, duplicate_number 2, no_copper 2)"]


def test_long_footprint_names_are_cut_to_the_library_limit_reproducibly() -> None:
    short = "SOIC-8_3.9x4.9mm_P1.27mm"
    assert K.cell_name(short) == short
    long = "HTSSOP-24-1EP_4.4x7.8mm_P0.65mm_EP3.4x7.8mm_Mask2.44x3.42mm_ThermalVias"
    cut = K.cell_name(long)
    assert len(cut) == 64 and cut.startswith(long[:56] + "~")
    assert cut == K.cell_name(long)
    assert cut != K.cell_name(long + "_2")
    assert (
        K.cell_name("Hirose_DF40C(2.0)-20DS-0.4V_2x10_P0.4mm")
        == "Hirose_DF40C_2.0_-20DS-0.4V_2x10_P0.4mm"
    )
    _, cell, _ = _cell(SOIC.replace("SOIC-8_Test", long))
    assert cell.name == cut


def test_partition_names_are_identifiers() -> None:
    assert K.partition_name("Package_SO.pretty") == "Package_SO"
    assert K.partition_name("Connector_JST.pretty") == "Connector_JST"
    assert K.partition_name("Display_7Segment.pretty") == "Display_7Segment"
    assert K.partition_name("4UCON_Case.pretty") == "K_4UCON_Case"
    assert K.partition_name("RF-Module.pretty") == "RF_Module"


def _library(tmp_path: Path) -> Path:
    root = tmp_path / "footprints"
    for library, name, text in (
        ("Package_SO", "SOIC-8_Test", SOIC),
        ("Connector_JST", "JST_Test", JST),
        ("MountingHole", "MountingHole_Test", HOLE),
        ("Package_SO", "Other_Test", SOIC.replace("SOIC-8_Test", "Other_Test")),
    ):
        folder = root / f"{library}.pretty"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.kicad_mod").write_text(text, encoding="utf-8")
    return root


def test_locate_accepts_library_prefixes_and_searches_bare_names(tmp_path: Path) -> None:
    root = _library(tmp_path)
    assert K.locate(root, "Package_SO:SOIC-8_Test").name == "SOIC-8_Test.kicad_mod"
    assert K.locate(root, "Package_SO/SOIC-8_Test").name == "SOIC-8_Test.kicad_mod"
    assert K.locate(root, "JST_Test").parent.name == "Connector_JST.pretty"
    with pytest.raises(FileNotFoundError):
        K.locate(root, "Package_SO:Missing")
    with pytest.raises(FileNotFoundError):
        K.locate(root, "Missing")


def test_convert_library_makes_one_partition_per_folder(tmp_path: Path) -> None:
    root = _library(tmp_path)
    plan, issues = K.convert_library(root / "Package_SO.pretty")
    assert plan.partition == "Package_SO"
    assert sorted(plan.cells) == ["Other_Test", "SOIC-8_Test"]
    assert len(issues) == 2 and all("pads dropped" in issue for issue in issues)
    assert "SMD-RRECT1.95X0.6R0.15" in plan.padstacks
    plan, issues = K.convert_library(root / "MountingHole.pretty")
    assert plan.cells["MountingHole_Test"].mechanical and issues == []
    assert K.libraries(root) == sorted(root.glob("*.pretty"))


def _design(root: Path | str, packages: dict[str, str]) -> dict:
    ic = {
        "kind": "box",
        "top": [["1", "VDD"]],
        "left": [["2", "IN"], ["3", "REF"]],
        "right": [["4", "OUT"], ["5", "NC"], ["6", "SD"]],
        "bottom": [["8", "GND"]],
    }
    connector = {"kind": "box", "left": [["1", "A"], ["2", "B"], ["3", "C"]]}
    return {
        "sheet_size": "A4",
        "kicad_footprints": str(root),
        "packages": packages,
        "symbols": {"AMP": ic, "APCON": connector},
        "sheets": [
            {
                "number": 1,
                "blocks": [
                    {
                        "kind": "ic",
                        "refdes": "U1",
                        "symbol": "AMP",
                        "value": "LM393",
                        "x": 500,
                        "y": 400,
                        "pins": {
                            "1": "power:+3V3",
                            "2": "label:IN",
                            "3": "label:REF",
                            "4": "label:OUT",
                            "5": "nc",
                            "6": "label:SD",
                            "8": "gnd",
                        },
                    },
                    {
                        "kind": "ic",
                        "refdes": "J1",
                        "symbol": "APCON",
                        "value": "AP I/F 3P",
                        "x": 900,
                        "y": 400,
                        "pins": {"1": "label:A", "2": "label:B", "3": "label:C"},
                    },
                    {
                        "kind": "ladder",
                        "x": 800,
                        "y": 700,
                        "path": [
                            "power:+3V3",
                            {"refdes": "R1", "symbol": "RES", "value": "10k"},
                            "gnd",
                        ],
                    },
                ],
            }
        ],
    }


def test_design_packages_may_reference_kicad_footprints(tmp_path: Path) -> None:
    root = _library(tmp_path)
    packages = {"U1": "kicad:Package_SO:SOIC-8_Test", "J1": "kicad:JST_Test"}
    plan, texts = H.library_texts(_design(root, packages))
    rows = {row["refdes"]: row for row in plan.mapping}
    assert rows["U1"]["cell"] == "SOIC-8_Test" and rows["U1"]["partition"] == "Package_SO"
    assert rows["J1"]["cell"] == "JST_Test" and rows["J1"]["partition"] == "Connector_JST"
    assert rows["R1"]["cell"] == "CLI_0402" and rows["R1"]["partition"] == plan.partition
    summary = plan.summary()
    assert summary["cell_partitions"] == ["Connector_JST", "Package_SO"]
    assert summary["referenced_cells"] == ["JST_Test", "SOIC-8_Test"]
    assert summary["cells"] == ["CLI_0402"]
    assert '.PACKAGE_CELL "SOIC-8_Test"' not in texts["cells"]
    assert '..TopCell "SOIC-8_Test"' in texts["parts"]
    assert any("pads dropped" in issue for issue in plan.issues)


def test_missing_footprint_folder_is_an_issue_not_a_crash() -> None:
    plan = H.plan_library(_design("Z:/nowhere", {"RES": "kicad:Resistor_SMD:R_0603_1608Metric"}))
    assert [row["refdes"] for row in plan.mapping] == ["U1", "J1"]
    assert any("R1: no KiCad footprint folder" in issue for issue in plan.issues)
