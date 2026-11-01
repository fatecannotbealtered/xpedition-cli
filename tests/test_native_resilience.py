"""The adapter survives the environment it runs in.

Three things found while testing issues #28-#30 on a machine that had been
rebooted: a temp cleaner had emptied win32com's generated-wrapper cache, which
made a running Designer look absent; with the cache gone, Designer came back
late-bound and `Documents.Open` failed ("parameter not optional"); and a
question a timed-out call left on screen held `Quit`, while `session close`
reported the application closed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as adapter


@pytest.mark.parametrize("bind", [adapter._designer_bound, adapter._typed])
def test_binds_live_typelib_even_when_old_wrapper_has_same_interface_id(bind):
    calls = []
    live_module = "win32com.gen_py.fixture151"
    old = type("Old", (), {"__module__": "win32com.gen_py.fixture155"})
    new = type(
        "New",
        (),
        {"__module__": live_module, "__init__": lambda self, raw: setattr(self, "_oleobj_", raw)},
    )
    classes = {"app-iid": old, "text-iid": old}
    lib = SimpleNamespace(GetLibAttr=lambda: ("lib-guid", 0, 3, 151, 0, 8))
    info = SimpleNamespace(GetContainingTypeLib=lambda: (lib, 0), GetTypeAttr=lambda: ("app-iid",))
    raw = SimpleNamespace(GetTypeInfo=lambda: info)
    app = old()
    app._oleobj_ = raw
    module = SimpleNamespace(
        __name__=live_module,
        CLSIDToClassMap={},
        CLSIDToPackageMap={"app-iid": "App", "text-iid": "Text"},
    )
    cache = SimpleNamespace(
        EnsureDispatch=lambda value: value,
        EnsureModule=lambda *args, **kwargs: calls.append((args, kwargs)) or module,
        AddModuleToCache=lambda *args, **kwargs: calls.append((args, kwargs)),
        GetModuleForCLSID=lambda iid: SimpleNamespace(CLSIDToClassMap={iid: new}),
        GetClassForCLSID=lambda iid: classes[iid],
    )
    registry = SimpleNamespace(
        HasClass=lambda iid: iid in classes,
        GetClass=lambda iid: classes[iid],
        RegisterCLSIDsFromDict=lambda mapping: classes.update(mapping),
    )
    bound = bind(SimpleNamespace(gencache=cache, CLSIDToClass=registry), app)
    assert type(bound) is new and bound._oleobj_ is raw
    assert classes["text-iid"] is new
    assert calls[0][0] == ("lib-guid", 0, 151, 0)
    assert calls[1][1] == {"bFlushNow": False}


# ---- the gen_py cache ----


def make_entry(root: Path, name: str, *, init: bool, pycache: bool) -> Path:
    entry = root / name
    entry.mkdir()
    if init:
        (entry / "__init__.py").write_text("# generated\n", encoding="utf-8")
    if pycache:
        (entry / "__pycache__").mkdir()
    return entry


def test_an_entry_that_lost_its_module_is_removed(tmp_path) -> None:
    broken = make_entry(tmp_path, "4ADEF4E1x0x155x0", init=False, pycache=True)
    assert adapter._heal_gen_py_cache(tmp_path) == ["4ADEF4E1x0x155x0"]
    assert not broken.exists()


def test_healthy_and_half_written_entries_are_left_alone(tmp_path) -> None:
    healthy = make_entry(tmp_path, "54FE0C76x0x155x0", init=True, pycache=True)
    # being generated right now: its .py files are written before any __pycache__
    fresh = make_entry(tmp_path, "91EF0189x0x155x0", init=False, pycache=False)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "dicts.dat").write_bytes(b"cache index")
    assert adapter._heal_gen_py_cache(tmp_path) == []
    assert healthy.exists() and fresh.exists()
    assert (tmp_path / "__pycache__").exists() and (tmp_path / "dicts.dat").exists()


def test_a_missing_cache_root_is_not_an_error(tmp_path) -> None:
    assert adapter._heal_gen_py_cache(tmp_path / "gone") == []


# ---- binding Designer through its wrapper ----


def test_designer_is_bound_through_its_generated_wrapper() -> None:
    wrapped = object()
    client = SimpleNamespace(gencache=SimpleNamespace(EnsureDispatch=lambda app: wrapped))
    assert adapter._designer_bound(client, object()) is wrapped


def test_without_a_wrapper_designer_stays_as_it_came() -> None:
    app = object()

    def refuse(_app):
        raise TypeError("This COM object can not automate the makepy process")

    assert adapter._designer_bound(SimpleNamespace(), app) is app
    assert (
        adapter._designer_bound(
            SimpleNamespace(gencache=SimpleNamespace(EnsureDispatch=refuse)), app
        )
        is app
    )


def test_an_attach_failure_says_what_failed() -> None:
    def broken_cache(progid):
        raise AttributeError(
            "module 'win32com.gen_py.4ADEF4E1x0x155x0' has no attribute 'CLSIDToClassMap'"
        )

    with pytest.raises(adapter.AdapterError) as caught:
        adapter._viewdraw_active(SimpleNamespace(GetActiveObject=broken_cache))
    assert caught.value.code == "E_NATIVE_FAILURE"
    assert caught.value.details["cause_type"] == "AttributeError"
    assert caught.value.details["write_attempted"] is False


# ---- quitting ----


class QuietPrompts:
    def __init__(self, *args, **kwargs) -> None:
        self.answered: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def blocking_dialogs(self) -> list:
        return [{"title": "", "text": "close all open documents?"}]


@pytest.fixture
def no_waiting(monkeypatch):
    monkeypatch.setattr(adapter, "_PromptAnswerer", QuietPrompts)
    monkeypatch.setattr(adapter.time, "sleep", lambda _s: None)


def test_a_quit_that_went_through_is_quiet(no_waiting) -> None:
    state = {"running": True}
    app = SimpleNamespace(Quit=lambda: state.update(running=False))

    def attach(progid):
        if not state["running"]:
            raise OSError("operation unavailable")
        return app

    adapter._quit_and_confirm(SimpleNamespace(GetActiveObject=attach), app, "schematic", wait=0)


def test_a_quit_held_by_a_dialog_is_reported(no_waiting) -> None:
    app = SimpleNamespace(Quit=lambda: None)  # returns, and Designer stays
    client = SimpleNamespace(GetActiveObject=lambda progid: app)
    with pytest.raises(adapter.AdapterError) as caught:
        adapter._quit_and_confirm(client, app, "schematic", wait=0)
    assert caught.value.code == "E_CONFLICT"
    assert caught.value.details["blocking_dialogs"] == [
        {"title": "", "text": "close all open documents?"}
    ]
    assert "session close again" in caught.value.details["hint"]
