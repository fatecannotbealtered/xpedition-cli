"""A draw says when the parts database it needs does not exist yet.

Designer draws a part instance's value itself when the library has no part for
it -- once rotated beside the body and once horizontally, the horizontal copy
landing on the reference designator -- and its own "Text alignment" graphical
check then fires on every such part. Eight of fourteen capacitors on one sheet
were flagged, and they were the only thing between a generated schematic and a
clean review. Building the library clears both, so the draw now says so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xpedition_cli import native_com_adapter as adapter


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    library = tmp_path / "RcLib"
    (library / "PartsDBLibs").mkdir(parents=True)
    (library / "SymbolLibs").mkdir()
    path = tmp_path / "Design.prj"
    path.write_text(f'KEY CentralLibrary "{library / "lib.lmc"}"\n', encoding="utf-8")
    monkeypatch.setattr(adapter, "_symbol_library_root", lambda _p: library / "SymbolLibs")
    return path


def warn_for(project: Path, partition: str, ops: list[dict]) -> list[dict]:
    warnings: list[dict] = []
    adapter._warn_when_the_library_is_missing(warnings, project, partition, ops)
    return warnings


PLACE = [{"op": "place_part", "refdes": "C1"}]


def test_a_missing_parts_database_is_reported(project) -> None:
    found = warn_for(project, "PartQuest", PLACE)
    assert len(found) == 1
    assert found[0]["check"] == "library_not_built"
    assert "library build --package" in found[0]["message"]
    assert found[0]["partition"] == "PartQuest"


def test_an_existing_parts_database_says_nothing(project) -> None:
    (project.parent / "RcLib" / "PartsDBLibs" / "PartQuest.pdb").write_bytes(b"built")
    assert warn_for(project, "PartQuest", PLACE) == []


def test_a_draw_that_places_nothing_says_nothing(project) -> None:
    # Wiping sheets needs no parts database.
    assert warn_for(project, "PartQuest", [{"op": "wipe_sheet"}, {"op": "open_sheet"}]) == []


def test_an_unreadable_project_is_not_a_failure(tmp_path, monkeypatch) -> None:
    def explode(_path):
        raise adapter.AdapterError("E_IO", "cannot read the project file")

    monkeypatch.setattr(adapter, "_symbol_library_root", explode)
    assert warn_for(tmp_path / "gone.prj", "PartQuest", PLACE) == []
