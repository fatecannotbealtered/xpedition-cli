from __future__ import annotations

import pytest

from xpedition_cli import project_file

PRJ = (
    "SECTION DesignInfo\r\n"
    'KEY CentralLibrary "D:\\lib\\TemplateLibrary.lmc"\r\n'
    "ENDSECTION\r\n"
    "SECTION iCDB\r\n"
    "LIST Designs\r\n"
    'VALUE "Schematic1"\r\n'
    'VALUE "Board1"\r\n'
    'VALUE "PCB"\r\n'
    "ENDLIST\r\n"
    'KEY iCDBDir ".\\database"\r\n'
    "ENDSECTION\r\n"
    "SECTION Schematic1\r\n"
    'KEY ConfigType "Board1"\r\n'
    "ENDSECTION\r\n"
    "SECTION Board1\r\n"
    "LIST Symbols\r\n"
    'VALUE "SymbolLibs\\Resistors"\r\n'
    "ENDLIST\r\n"
    "LIST PDBs\r\n"
    'VALUE "PartsDBLibs\\Resistors.pdb"\r\n'
    'VALUE "PartsDBLibs\\PartQuest.pdb"\r\n'
    "ENDLIST\r\n"
    'KEY ConfigType "PCB"\r\n'
    'KEY RootBlock "Schematic1"\r\n'
    'KEY PCBDesignPath ""\r\n'
    "ENDSECTION\r\n"
    "SECTION PCB\r\n"
    'KEY ConfigType "Board1"\r\n'
    "ENDSECTION\r\n"
)


def test_designs_reads_the_icdb_list_and_each_section() -> None:
    listed = project_file.designs(PRJ)
    assert [item.name for item in listed] == ["Schematic1", "Board1", "PCB"]
    board = project_file.board_design(listed)
    assert board is not None and board.name == "Board1" and board.is_board()
    assert board.pcb_path == "" and board.layout_template == ""
    assert project_file.board_design(listed, "PCB").config_type == "Board1"
    assert project_file.board_design(listed, "Nope") is None
    assert project_file.designs("KEY DesignName Board1\n") == []


def test_move_design_first_reorders_only_the_designs_list() -> None:
    moved = project_file.move_design_first(PRJ, "Board1")
    assert project_file.list_entries(moved, "Designs") == ["Board1", "Schematic1", "PCB"]
    assert project_file.list_entries(moved, "PDBs") == project_file.list_entries(PRJ, "PDBs")
    assert "\r\n" in moved and moved.count("ENDLIST") == PRJ.count("ENDLIST")
    assert project_file.move_design_first(moved, "Board1") == moved
    assert project_file.move_design_first(PRJ, "Nope") == PRJ


def test_ensure_list_and_add_entry_keep_the_file_style() -> None:
    text = project_file.ensure_list(PRJ, project_file.CELL_LIST, "PDBs")
    assert project_file.list_entries(text, project_file.CELL_LIST) == []
    assert text.index("LIST 2dCellLibraries") > text.index("LIST PDBs")
    assert text.index("LIST 2dCellLibraries") < text.index('KEY ConfigType "PCB"')
    assert project_file.ensure_list(text, project_file.CELL_LIST, "PDBs") == text
    text, added = project_file.add_entry(text, project_file.CELL_LIST, "CellDBLibs\\PartQuest.cel")
    assert added and project_file.list_entries(text, project_file.CELL_LIST) == [
        "CellDBLibs\\PartQuest.cel"
    ]
    text, again = project_file.add_entry(text, project_file.CELL_LIST, "celldblibs\\partquest.cel")
    assert not again
    assert "\n\n" not in text.replace("\r\n", "\n")
    with pytest.raises(ValueError):
        project_file.ensure_list(PRJ, "X", "Nope")
    with pytest.raises(ValueError):
        project_file.add_entry(PRJ, "Nope", "x")


def test_cell_entries_follow_the_parts_partitions_the_library_has() -> None:
    pdbs = ["PartsDBLibs\\Resistors.pdb", "PartsDBLibs\\PartQuest.pdb", "PartsDBLibs\\Gone.pdb"]
    assert project_file.cell_entries_for(pdbs, ["Resistors", "partquest", "Capacitors"]) == [
        "CellDBLibs\\Resistors.cel",
        "CellDBLibs\\PartQuest.cel",
    ]
    assert project_file.cell_entries_for([], ["Resistors"]) == []
