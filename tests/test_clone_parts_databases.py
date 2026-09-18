"""A cloned project must not carry a symbol partition with no parts database.

Designer walks a design's symbol partitions when it places a part, and one whose
`.pdb` is missing raises a modal dialog that holds the draw open until a human
clicks. A template clone listed three such partitions. An empty parts database is
a stock artefact, so a missing one is filled from an unused stock database in the
project's own library -- but only when "empty" can be checked rather than guessed.
"""

from __future__ import annotations

from pathlib import Path

from xpedition_cli.native_com_adapter import _repair_missing_parts_databases

EMPTY = b"stock-empty-parts-database"


def project(tmp_path: Path, symbols: list[str], pdbs: list[str], stock: dict[str, bytes]) -> Path:
    library = tmp_path / "RcLib"
    (library / "PartsDBLibs").mkdir(parents=True)
    for name, content in stock.items():
        (library / "PartsDBLibs" / name).write_bytes(content)
    (library / "lib.lmc").write_text("central library", encoding="utf-8")
    path = tmp_path / "Design.prj"
    lines = [f'KEY CentralLibrary "{library / "lib.lmc"}"', "SECTION Board1", "LIST Symbols"]
    lines += [f'VALUE "SymbolLibs\\{name}"' for name in symbols]
    lines += ["ENDLIST", "LIST PDBs"]
    lines += [f'VALUE "PartsDBLibs\\{name}"' for name in pdbs]
    lines += ["ENDLIST", "ENDSECTION", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_a_user_partition_without_a_database_gets_an_empty_one(tmp_path) -> None:
    path = project(
        tmp_path,
        symbols=["Case", "Resistors", "Globals", "builtin", "Borders"],
        pdbs=["Resistors.pdb"],
        stock={"Resistors.pdb": b"used", "Spare1.pdb": EMPTY, "Spare2.pdb": EMPTY},
    )
    report = _repair_missing_parts_databases(path)
    created = tmp_path / "RcLib" / "PartsDBLibs" / "Case.pdb"
    assert created.is_file() and created.read_bytes() == EMPTY
    assert report["missing"] == []
    assert "Case.pdb" in report["registered"]
    # Registering it is what makes the packager search the partition.
    assert 'VALUE "PartsDBLibs\\Case.pdb"' in path.read_text(encoding="utf-8")


def test_stock_partitions_are_left_alone(tmp_path) -> None:
    path = project(
        tmp_path,
        symbols=["Globals", "builtin", "Borders"],
        pdbs=[],
        stock={"Spare1.pdb": EMPTY, "Spare2.pdb": EMPTY},
    )
    report = _repair_missing_parts_databases(path)
    assert report == {"created": [], "registered": [], "missing": []}
    assert not (tmp_path / "RcLib" / "PartsDBLibs" / "Globals.pdb").exists()


def test_without_two_matching_stock_databases_nothing_is_invented(tmp_path) -> None:
    # One unused database could be a populated one; copying it would give the new
    # partition someone else's parts.
    path = project(
        tmp_path,
        symbols=["Case"],
        pdbs=[],
        stock={"Only.pdb": b"could be anything"},
    )
    report = _repair_missing_parts_databases(path)
    assert report["missing"] == ["Case"]
    assert report["created"] == []
    assert not (tmp_path / "RcLib" / "PartsDBLibs" / "Case.pdb").exists()


def test_a_database_that_exists_but_is_unlisted_is_registered(tmp_path) -> None:
    path = project(
        tmp_path,
        symbols=["Case"],
        pdbs=[],
        stock={"Case.pdb": EMPTY, "Spare1.pdb": EMPTY, "Spare2.pdb": EMPTY},
    )
    report = _repair_missing_parts_databases(path)
    assert report["created"] == [] and report["registered"] == ["Case.pdb"]


def test_an_already_complete_project_is_not_rewritten(tmp_path) -> None:
    path = project(
        tmp_path,
        symbols=["Case"],
        pdbs=["Case.pdb"],
        stock={"Case.pdb": EMPTY, "Spare1.pdb": EMPTY, "Spare2.pdb": EMPTY},
    )
    before = path.read_text(encoding="utf-8")
    report = _repair_missing_parts_databases(path)
    assert report == {"created": [], "registered": [], "missing": []}
    assert path.read_text(encoding="utf-8") == before


def test_a_project_without_a_central_library_is_not_a_failure(tmp_path) -> None:
    path = tmp_path / "Design.prj"
    path.write_text("SECTION Board1\nENDSECTION\n", encoding="utf-8")
    assert _repair_missing_parts_databases(path) == {
        "created": [],
        "registered": [],
        "missing": [],
    }
