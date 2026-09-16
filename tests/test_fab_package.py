from pathlib import Path

from xpedition_cli import fab_package as F

ODB_SETUP = """.FILETYPE ODBSETUP
.LAYERS_TO_EXPORT
 ..NAME "d_1_4"
  ...INCLUDE NO
 ..NAME "smt"
  ...INCLUDE YES
.ROUND_CORNERS YES
.BOARD_OUTLINE NO
.OutputJobName "Designodb"
"""

GERBER_SETUP = """.FILETYPE GerberPlotSetupFile
.GerberOutputDir "Output\\\\Gerber\\\\"

.GerberOutputFile "SoldermaskTop.gdo"
 ..ProcessFile Yes
 ..FlashPads Yes
 ..GerberOutputPath "Output\\\\Gerber\\\\SoldermaskTop.gdo"
 ..BoardItem SoldermaskTop
"""


def test_odb_setup_gains_drill_spans_and_the_outline_once() -> None:
    patched, changes = F.patch_odb_setup(ODB_SETUP)
    assert changes == ["drill spans included", "board outline included"]
    assert '..NAME "d_1_4"\n  ...INCLUDE YES' in patched and ".BOARD_OUTLINE YES" in patched
    again, changes = F.patch_odb_setup(patched)
    assert again == patched and changes == []


def test_gerber_setup_gains_cell_silkscreen_and_outline_files_once() -> None:
    patched, changes = F.patch_gerber_setup(GERBER_SETUP)
    assert changes == ["SilkscreenTop.gdo", "SilkscreenBottom.gdo", "BoardOutline.gdo"]
    assert '.GerberOutputFile "SilkscreenTop.gdo"' in patched
    assert " ..CellType ICSOIC\n" in patched and " ..CellItemsSide Bottom\n" in patched
    assert "   ...CellItemsLayer 1\n" in patched and "   ...CellItemsLayer 0\n" in patched
    assert "   ...CellItem SilkscreenReferenceDesignator" in patched
    assert ' ..GerberOutputPath "Output\\\\Gerber\\\\BoardOutline.gdo"' in patched
    assert " ..BoardItem BoardOutline" in patched
    again, changes = F.patch_gerber_setup(patched)
    assert again == patched and changes == []
    reordered = patched.replace(" ..FlashPads Yes\n", "")  # the dialog reorders lines
    assert F.patch_gerber_setup(reordered)[1] == []
    wrong = patched.replace("   ...CellItemsLayer 0\n", "   ...CellItemsLayer 1\n")
    assert F.patch_gerber_setup(wrong)[1] == ["SilkscreenBottom.gdo"]
    # a silkscreen file defined as a board item draws nothing and is replaced
    stale = GERBER_SETUP + (
        '\n.GerberOutputFile "SilkscreenTop.gdo"\n ..ProcessFile Yes\n ..BoardItem SilkscreenTop\n'
    )
    fixed, changes = F.patch_gerber_setup(stale)
    assert "SilkscreenTop.gdo" in changes
    assert fixed.count('.GerberOutputFile "SilkscreenTop.gdo"') == 1
    assert " ..BoardItem SilkscreenTop\n" not in fixed and " ..CellItemsSide Top\n" in fixed


def _outputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    gerber = tmp_path / "Gerber"
    gerber.mkdir()
    (gerber / "EtchLayer1Top.gdo").write_text(
        "G04 top*\nD10*\nX1Y1D02*\nX2Y2D01*\nX3Y3D03*\nM02*\n"
    )
    (gerber / "SilkscreenTop.gdo").write_text("G04 empty*\nM02*\n")
    drill = tmp_path / "NCDrill"
    drill.mkdir()
    (drill / "ThruHolePlated.ncd").write_text(
        "M48\nT01C0.026\n%\nT01\nX1000Y2000\nX1500\nY2500\nM30\n"
    )
    odb = tmp_path / "ODBpp" / "designodb"
    (odb / "matrix").mkdir(parents=True)
    (odb / "matrix" / "matrix").write_text(
        "LAYER {\n ROW=1\n CONTEXT=BOARD\n TYPE=SIGNAL\n NAME=signal_1\n}\n"
        "LAYER {\n ROW=2\n CONTEXT=BOARD\n TYPE=DRILL\n NAME=d_1_4\n}\n"
    )
    step = odb / "steps" / "board"
    (step / "layers" / "signal_1").mkdir(parents=True)
    (step / "layers" / "signal_1" / "features").write_text("#\nL 0 0 1 1 0 P 0\nP 1 1 0 P 0\n")
    (step / "profile").write_text("OB 0 0 I\nOS 1 0\nOE\n")
    return gerber, drill, odb


def test_outputs_are_read_back_with_counts_and_emptiness(tmp_path: Path) -> None:
    gerber, drill, odb = _outputs(tmp_path)
    files = {item.name: item for item in F.gerber_files(gerber)}
    assert files["EtchLayer1Top"].draws == 3 and not files["EtchLayer1Top"].empty
    assert files["SilkscreenTop"].empty and files["SilkscreenTop"].note.startswith("顶层丝印")
    holes = F.drill_files(drill)
    assert holes[0].draws == 3 and "1 tools" in holes[0].note and "金属化" in holes[0].note
    job = F.odb_job(odb)
    assert job["drill"] and job["profile"] and job["step"] == "board"
    assert [layer["features"] for layer in job["layers"]] == [2, None]
    verdict = F.checks(list(files.values()), holes, job)
    assert not verdict["ok"]
    assert "Gerber EtchLayer4Bottom is missing or empty" in verdict["problems"]
    assert "Gerber has no top silkscreen" in verdict["problems"]


def test_package_folder_holds_renamed_copies_manifest_readme_centroid_and_bom(
    tmp_path: Path,
) -> None:
    gerber, drill, odb = _outputs(tmp_path)
    components = [
        {
            "refdes": "R2",
            "x": 10.0,
            "y": 5.0,
            "rotation": 90,
            "side": "top",
            "footprint": "R_0603",
            "part_number": "10k",
            "placed": True,
        },
        {
            "refdes": "R1",
            "x": 12.0,
            "y": 5.0,
            "rotation": 0,
            "side": "top",
            "footprint": "R_0603",
            "part_number": "10k",
            "placed": True,
        },
        {
            "refdes": "U1",
            "x": 20.0,
            "y": 9.0,
            "rotation": 0,
            "side": "top",
            "footprint": "SOIC-8",
            "part_number": "LM393",
            "placed": True,
        },
        {
            "refdes": "H1",
            "x": 0.0,
            "y": 0.0,
            "rotation": 0,
            "side": "top",
            "footprint": "HOLE",
            "part_number": "",
            "placed": False,
        },
    ]
    target = tmp_path / "fab"
    manifest = F.write_package(
        target, "Demo", {"width": 70, "height": 48, "unit": "mm"}, 4, gerber, drill, odb, components
    )
    assert (target / "gerber" / "EtchLayer1Top.gbr").is_file()
    assert not (target / "gerber" / "SilkscreenTop.gbr").exists()  # empty files stay behind
    assert (target / "drill" / "ThruHolePlated.drl").is_file()
    assert (target / "designodb.zip").is_file()
    centroid = (target / "centroid.csv").read_text(encoding="utf-8").splitlines()
    assert centroid[0].startswith("refdes,x_mm,y_mm,rotation,side,footprint")
    assert [line.split(",")[0] for line in centroid[1:]] == [
        "R1",
        "R2",
        "U1",
    ]  # sorted, unplaced dropped
    bom = (target / "bom.csv").read_text(encoding="utf-8").splitlines()
    assert "10k,R_0603,2,R1 R2" in bom and "LM393,SOIC-8,1,U1" in bom
    readme = (target / "README.md").read_text(encoding="utf-8")
    assert "# Demo 打板资料" in readme and "70 × 48 mm" in readme and "EtchLayer1Top.gbr" in readme
    assert (
        manifest["checks"]["ok"] is False
        and manifest["centroid_rows"] == 3
        and manifest["bom_rows"] == 3
    )
    assert str(target / "README.md") in manifest["files"]
    assert (target / "manifest.json").is_file()
