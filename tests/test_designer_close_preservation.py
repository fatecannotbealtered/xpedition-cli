"""Designer release must preserve documents and observe a successful close."""

from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as adapter


def application(path, *, save_fails=False, close_result=True, remains_open=False):
    calls = []
    state = {"open": True}

    def save():
        calls.append("save_all")
        if save_fails:
            raise RuntimeError("save failed")

    def close():
        calls.append("close")
        if close_result is True and not remains_open:
            state["open"] = False
        return close_result

    app = SimpleNamespace(
        IsProjectOpened=lambda: state["open"],
        GetProjectData=lambda: SimpleNamespace(GetProjectFilePath=lambda: str(path)),
        Documents=SimpleNamespace(SaveAll=save),
        CloseProject=close,
    )
    return app, calls


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(adapter, "_settle", lambda *args: None)


def test_matching_designer_project_saves_all_and_observes_closure(tmp_path):
    path = tmp_path / "QA.prj"
    app, calls = application(path)
    assert adapter._close_if_open(app, path) is True
    assert calls == ["save_all", "close"]
    assert app.IsProjectOpened() is False


def test_designer_save_failure_never_closes(tmp_path):
    path = tmp_path / "QA.prj"
    app, calls = application(path, save_fails=True)
    with pytest.raises(adapter.AdapterError):
        adapter._close_if_open(app, path)
    assert calls == ["save_all"]


@pytest.mark.parametrize("result,remains", [(False, True), (None, True), (True, True)])
def test_designer_close_rejection_or_open_state_stops(tmp_path, result, remains):
    path = tmp_path / "QA.prj"
    app, calls = application(path, close_result=result, remains_open=remains)
    with pytest.raises(adapter.AdapterError):
        adapter._close_if_open(app, path)
    assert calls == ["save_all", "close"]


def test_unknown_open_state_does_not_dispatch_save_or_close(tmp_path):
    app, calls = application(tmp_path / "QA.prj")
    app.IsProjectOpened = lambda: None
    with pytest.raises(adapter.AdapterError):
        adapter._close_if_open(app, tmp_path / "QA.prj")
    assert calls == []


def test_different_project_is_untouched(tmp_path):
    app, calls = application(tmp_path / "USER.prj")
    assert adapter._close_if_open(app, tmp_path / "QA.prj") is False
    assert calls == []


def test_release_propagates_close_failure(tmp_path, monkeypatch):
    app, _ = application(tmp_path / "QA.prj", save_fails=True)
    monkeypatch.setattr(adapter, "_viewdraw_application", lambda *a, **k: app)
    with pytest.raises(adapter.AdapterError):
        adapter._release_project(None, tmp_path / "QA.prj")


def test_unreachable_running_designer_is_not_released(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise adapter.AdapterError("E_BACKEND_UNAVAILABLE", "unreachable")

    monkeypatch.setattr(adapter, "_viewdraw_application", unavailable)
    monkeypatch.setattr(adapter, "processes", lambda domain: [{"pid": 10}])
    with pytest.raises(adapter.AdapterError):
        adapter._release_project(None, tmp_path / "QA.prj")


@pytest.mark.parametrize("state", ["owned", "invalid", "unavailable"])
def test_archive_stops_before_designer_close_for_unreleased_library(tmp_path, monkeypatch, state):
    from xpedition_cli import native_library_manager

    project = tmp_path / "QA.prj"
    monkeypatch.setattr(adapter, "processes", lambda domain: [{"pid": 10}])

    class InvalidLibrary:
        @property
        def FullName(self):
            raise RuntimeError("Invalid object")

    def active(client):
        if state == "unavailable":
            raise RuntimeError("unreachable")
        library = (
            InvalidLibrary()
            if state == "invalid"
            else SimpleNamespace(FullName=str(tmp_path / "Library/QA.lmc"))
        )
        return SimpleNamespace(ActiveLibrary=library), "fixture"

    monkeypatch.setattr(native_library_manager, "active", active)
    monkeypatch.setattr(adapter, "_release_project", lambda *a: pytest.fail("Designer closed"))
    with pytest.raises(adapter.AdapterError) as failure:
        adapter._let_go_of_project({"project": str(project)}, None)
    assert failure.value.code == "E_HUMAN_REQUIRED"
    assert failure.value.details["backup_complete"] is False


@pytest.mark.parametrize("active_path", [None, "outside"])
def test_archive_accepts_observed_library_outside_its_scope(tmp_path, monkeypatch, active_path):
    from xpedition_cli import native_library_manager

    monkeypatch.setattr(adapter, "processes", lambda domain: [{"pid": 10}])
    library = (
        None if active_path is None else SimpleNamespace(FullName=str(tmp_path / "other/Lib.lmc"))
    )
    app = SimpleNamespace(ActiveLibrary=library)
    monkeypatch.setattr(native_library_manager, "active", lambda client: (app, "fixture"))
    observed = adapter._archive_library_state(None, tmp_path / "owned/QA.prj")
    assert observed["status"] == ("no_active_library" if active_path is None else "outside_project")
