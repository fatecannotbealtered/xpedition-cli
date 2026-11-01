"""Board-library names and instance presence are distinct native observations."""

from types import SimpleNamespace

from xpedition_cli import native_com_adapter as adapter


def test_native_cell_names_property_is_not_called_as_a_function():
    document = SimpleNamespace(
        CellNames=("KNOWN_R0805", "QA_R0805"),
        CellEditor=SimpleNamespace(ActiveDatabase=SimpleNamespace(FullName="D:/qa/QA.pcb")),
    )
    observed = adapter._cell_search_evidence(document)
    assert observed["status"] == "observed"
    assert observed["names"] == ["KNOWN_R0805", "QA_R0805"]
    assert observed["database"] == "D:/qa/QA.pcb"
    assert observed["board_instances_verified"] is False
    assert observed["_untrusted"] == ["names", "database"]


def test_empty_native_library_is_an_observed_empty_set():
    document = SimpleNamespace(
        CellNames=(),
        CellEditor=SimpleNamespace(ActiveDatabase=SimpleNamespace(FullName="D:/qa/QA.pcb")),
    )
    observed = adapter._cell_search_evidence(document)
    assert observed["status"] == "observed" and observed["names"] == []
    assert observed["cell_geometry_verified"] is False


def test_unavailable_getter_is_not_an_empty_native_library():
    class Document:
        @property
        def CellNames(self):
            raise RuntimeError("native getter failed")

    observed = adapter._cell_search_evidence(Document())
    assert observed["status"] == "not_observed"
    assert observed["names"] is None
    assert observed["diagnostics"][0]["action"] == "Document.CellNames"
