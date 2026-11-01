"""Closed setup source observations never initialize CAD or grant scope."""

import os

import pytest

from xpedition_cli import designer_source_binding as S

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows source path contract")


def source(tmp_path):
    root = tmp_path / "setup"
    root.mkdir()
    library = root / "Library"
    library.mkdir()
    (library / "Central.lmc").write_bytes(b"local example library index")
    (library / "part.pdb").write_bytes(b"original part bytes")
    project = root / "Setup.prj"
    project.write_text('SECTION DesignInfo\nKEY CentralLibrary "Library/Central.lmc"\nENDSECTION\n')
    return project, library


def test_unchanged_template_cannot_hide_changed_library_or_unrelated_source(tmp_path):
    project, library = source(tmp_path)
    first = S.setup_source_binding(project)
    (library / "part.pdb").write_bytes(b"other real part bytes")
    second = S.setup_source_binding(project)
    assert first["template_sha256"] == second["template_sha256"]
    assert first["closure_sha256"] != second["closure_sha256"]
    (project.parent / "unrelated.ini").write_bytes(b"also observed")
    third = S.setup_source_binding(project)
    assert third["file_count"] == first["file_count"] + 1
    (library / "empty_partition").mkdir()
    assert S.setup_source_binding(project)["closure_sha256"] != third["closure_sha256"]


def test_source_change_between_passes_refuses_instead_of_binding_partial_tree(
    tmp_path, monkeypatch
):
    project, library = source(tmp_path)
    original = S._tree
    calls = 0

    def observed(root):
        nonlocal calls
        result = original(root)
        calls += 1
        if calls == 1:
            (library / "part.pdb").write_bytes(b"changed while queued")
        return result

    monkeypatch.setattr(S, "_tree", observed)
    with pytest.raises(S.SourceBindingError, match="changed_or_resource_limit"):
        S.setup_source_binding(project)


def test_capacity_and_initialized_source_refuse(tmp_path, monkeypatch):
    project, _library = source(tmp_path)
    monkeypatch.setattr(S, "MAX_FILES", 1)
    with pytest.raises(S.SourceBindingError, match="resource_limit"):
        S.setup_source_binding(project)
    monkeypatch.setattr(S, "MAX_FILES", 8192)
    (project.parent / "database").mkdir()
    (project.parent / "database/icdb.dat").write_bytes(b"already initialized")
    with pytest.raises(S.SourceBindingError, match="requires_setup_source"):
        S.setup_source_binding(project)
