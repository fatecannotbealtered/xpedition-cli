"""Preserve the matching document, and stop on save/close uncertainty."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as adapter


def setup_board(monkeypatch, path, *, save_error=False, refuse_close=False, remain_open=False):
    calls = []
    app = SimpleNamespace()

    class Document:
        FullName = str(path)

        def Save(self):
            calls.append("save")
            if save_error:
                raise RuntimeError("save rejected")

        def Close(self, save_changes):
            calls.append(("close", save_changes))
            if refuse_close:
                return False
            if not remain_open:
                app.ActiveDocument = None
            return True

    doc = Document()
    app.ActiveDocument = doc
    monkeypatch.setattr(adapter, "_application", lambda *a, **k: app)
    monkeypatch.setattr(adapter, "_licensed_document", lambda current: current.ActiveDocument)
    monkeypatch.setattr(adapter, "_settle", lambda *a: None)
    return app, calls


def test_matching_board_is_saved_and_closed_without_discard(monkeypatch, tmp_path):
    path = tmp_path / "QA.pcb"
    app, calls = setup_board(monkeypatch, path)
    assert adapter._close_board_if_open(None, path) is True
    assert calls == ["save", ("close", True)]
    assert app.ActiveDocument is None


def test_save_failure_never_closes_the_board(monkeypatch, tmp_path):
    path = tmp_path / "QA.pcb"
    app, calls = setup_board(monkeypatch, path, save_error=True)
    with pytest.raises(adapter.AdapterError):
        adapter._close_board_if_open(None, path)
    assert calls == ["save"]
    assert app.ActiveDocument is not None


@pytest.mark.parametrize("option", ["refuse_close", "remain_open"])
def test_unconfirmed_close_stops_without_reopening(monkeypatch, tmp_path, option):
    path = tmp_path / "QA.pcb"
    app, calls = setup_board(monkeypatch, path, **{option: True})
    with pytest.raises(adapter.AdapterError):
        adapter._close_board_if_open(None, path)
    assert calls == ["save", ("close", True)]
    assert app.ActiveDocument is not None


def test_other_document_is_never_saved_or_closed(monkeypatch, tmp_path):
    _, calls = setup_board(monkeypatch, tmp_path / "USER.pcb")
    assert adapter._close_board_if_open(None, tmp_path / "QA.pcb") is False
    assert calls == []


def test_unreadable_active_document_state_stops(monkeypatch, tmp_path):
    class Application:
        @property
        def ActiveDocument(self):
            raise RuntimeError("state unavailable")

    monkeypatch.setattr(adapter, "_application", lambda *a, **k: Application())
    with pytest.raises(adapter.AdapterError):
        adapter._close_board_if_open(None, Path(tmp_path / "QA.pcb"))


def test_unreachable_running_layout_does_not_mean_closed(monkeypatch, tmp_path):
    def unavailable(*args, **kwargs):
        raise adapter.AdapterError("E_BACKEND_UNAVAILABLE", "not reachable")

    monkeypatch.setattr(adapter, "_application", unavailable)
    monkeypatch.setattr(adapter, "processes", lambda domain: [{"pid": 123}])
    with pytest.raises(adapter.AdapterError):
        adapter._close_board_if_open(None, tmp_path / "QA.pcb")
