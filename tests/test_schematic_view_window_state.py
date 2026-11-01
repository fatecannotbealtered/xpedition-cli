"""Native-shaped GUI doubles for the dual-RC full-page view regression; CAD=0.

Real failure: example-project/runs/032-r5-schematic-view-confirm restored a
maximized Designer to 1230x589 logical / 1538x736 physical pixels before capture.
The original schematic-initial.png and its incomplete receipt remain evidence.
"""

from __future__ import annotations

import copy
import ctypes
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import native_com_adapter as A
from xpedition_cli import win_dialogs as W


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


@pytest.mark.parametrize("state", ["maximized", "normal", "minimized"])
def test_preserve_window_state_restores_only_when_minimized(monkeypatch, state):
    events = []
    user = NS(
        IsIconic=Function(lambda hwnd: state == "minimized"),
        ShowWindow=Function(lambda *args: events.append(("ShowWindow", *args))),
        SetForegroundWindow=Function(lambda *args: events.append(("foreground", *args))),
        SwitchToThisWindow=Function(lambda *args: events.append(("switch", *args))),
        SetWindowPos=Function(lambda *args: events.append(("position", *args))),
        GetForegroundWindow=Function(lambda: 10),
    )
    monkeypatch.setattr(W, "_api", lambda: (ctypes, user, object()))
    monkeypatch.setattr(W, "_window_is_bound", lambda *args: True)
    assert W.bring_to_front(10, preserve_state=True) is True
    assert [e for e in events if e[0] == "ShowWindow"] == (
        [("ShowWindow", 10, 9)] if state == "minimized" else []
    )


def test_old_bring_to_front_callers_keep_restore_behavior(monkeypatch):
    events = []
    user = NS(
        ShowWindow=Function(lambda *args: events.append(args)),
        SetForegroundWindow=Function(lambda *args: None),
        SwitchToThisWindow=Function(lambda *args: None),
        SetWindowPos=Function(lambda *args: None),
        GetForegroundWindow=Function(lambda: 10),
    )
    monkeypatch.setattr(W, "_api", lambda: (ctypes, user, object()))
    monkeypatch.setattr(W, "_window_is_bound", lambda *args: True)
    assert W.bring_to_front(10) is True
    assert events == [(10, 9)]


def test_raise_rejects_foreign_bound_window_before_show(monkeypatch):
    monkeypatch.setattr(W, "_api", lambda: (object(), object(), object()))
    monkeypatch.setattr(W, "_window_is_bound", lambda *args: False)
    assert W.bring_to_front(10, preserve_state=True) is False


@pytest.fixture
def view(tmp_path, monkeypatch):
    project = tmp_path / "EXAMPLE_BOARD.prj"
    project.write_text("synthetic project")
    events = []
    state = NS(
        locked=False,
        raised=False,
        foreground=True,
        window_present=True,
        root_matches=True,
        width=1920,
        height=1080,
        sheet=1,
        activate=True,
        during_fit=None,
        during_capture=None,
    )
    identity = {
        "pid": 42,
        "document": {"open": True, "path": str(project)},
        "public_fields": {"ActiveViewHandle": 11, "FullName": "C:/Vendor/viewdraw.exe"},
        "pid_source": {"hwnd": 11, "property": "ActiveViewHandle"},
    }

    def fit(command):
        assert command == A._FIT_ALL_COMMAND
        events.append("fit")
        if state.during_fit:
            state.during_fit()

    app = NS(ExecuteCommandByID=fit, ActiveView=NS(Block=NS(SheetSize=5)))

    def application(*args, **kwargs):
        assert kwargs == {"attach_only": True}
        events.append("bind")
        return app

    def observe_identity(*args):
        events.append("identity")
        return copy.deepcopy(identity)

    def window(*args):
        events.append("geometry:large" if state.raised else "geometry:small")
        if not state.window_present:
            return None
        return {
            "hwnd": 10,
            "title": "[EXAMPLE_BOARD.Board1.1]",
            "left": 0,
            "top": 0,
            "width": state.width if state.raised else 1230,
            "height": state.height if state.raised else 589,
        }

    def raise_window(hwnd, **kwargs):
        assert hwnd == 10 and kwargs == {"preserve_state": True}
        events.append("raise")
        state.raised = True
        return state.foreground

    def activate(native, sheet):
        assert native is app
        events.append("activate")
        if state.activate:
            state.sheet = sheet

    def capture(hwnd, path):
        assert hwnd == 10
        events.append("capture")
        from pathlib import Path

        Path(path).write_bytes(b"synthetic physical capture, not native evidence")
        if state.during_capture:
            state.during_capture()
        return {
            "path": path,
            "window_hwnd": 10,
            "window_pid": 42,
            "width": 2400,
            "height": 1350,
            "bounds_px": [0, 0, 2400, 1350],
            "bounds_verified": True,
            "coordinate_space": "physical_pixels",
        }

    monkeypatch.setenv("XPEDITION_EXPECTED_PID", "42")
    monkeypatch.setattr(A, "_viewdraw_application", application)
    monkeypatch.setattr(A, "_ensure_project", lambda *args: events.append("ensure"))
    monkeypatch.setattr(A, "_sheet_numbers", lambda *args: {1, 2})
    monkeypatch.setattr(A, "application_identity", observe_identity)
    monkeypatch.setattr(A, "_activate_sheet", activate)
    monkeypatch.setattr(A, "_active_sheet", lambda *args: state.sheet)
    monkeypatch.setattr(A.time, "sleep", lambda *args: None)
    monkeypatch.setattr(W, "desktop_locked", lambda: state.locked)
    monkeypatch.setattr(W, "main_window", window)
    monkeypatch.setattr(W, "same_root_window", lambda *args: state.root_matches)
    monkeypatch.setattr(W, "bring_to_front", raise_window)
    monkeypatch.setattr(W, "foreground_matches", lambda *args: state.foreground)
    monkeypatch.setattr(W, "capture_window", capture)
    return NS(
        project=project,
        output=tmp_path / "view.png",
        events=events,
        state=state,
        identity=identity,
        params={"project": str(project), "sheet": 1, "output": str(tmp_path / "view.png")},
    )


def test_raise_and_fresh_geometry_precede_fit_and_physical_capture(view):
    result = A._show(view.params, None)
    events = view.events
    assert events.index("raise") < events.index("geometry:large") < events.index("fit")
    assert events.index("fit") < events.index("capture")
    assert events.count("raise") == events.count("fit") == events.count("capture") == 1
    assert events.index("capture") < len(events) - 1, "fresh identity must follow capture"
    assert result["window"]["width"] == 1920
    assert result["screenshot"]["width"] == 2400
    assert result["capture_context"] == {
        "project": str(view.project),
        "sheet": 1,
        "pid": 42,
        "executable": "C:/Vendor/viewdraw.exe",
        "application_hwnd": 11,
        "active_view_hwnd": 11,
        "window_hwnd": 10,
        "same_root_verified": True,
        "foreground_verified": True,
    }


def test_locked_desktop_never_binds_activates_fits_or_captures(view):
    view.state.locked = True
    with pytest.raises(A.AdapterError) as caught:
        A._show(view.params, None)
    assert caught.value.code == "E_HUMAN_REQUIRED"
    assert caught.value.details["write_attempted"] is False
    assert view.events == []
    assert not view.output.exists()


@pytest.mark.parametrize(
    "fault", ["pid", "project", "unknown_project", "closed", "hwnd", "window", "root"]
)
def test_wrong_or_missing_target_never_raises_fits_or_captures(view, fault):
    if fault == "pid":
        view.identity["pid"] = 99
    elif fault == "project":
        view.identity["document"]["path"] = str(view.project.with_name("foreign.prj"))
    elif fault == "unknown_project":
        view.identity["document"]["path"] = None
    elif fault == "closed":
        view.identity["document"]["open"] = False
    elif fault == "hwnd":
        view.identity["pid_source"] = {}
    elif fault == "window":
        view.state.window_present = False
    elif fault == "root":
        view.state.root_matches = False
    with pytest.raises(A.AdapterError):
        A._show(view.params, None)
    assert not {"ensure", "raise", "activate", "fit", "capture"} & set(view.events)
    assert not view.output.exists()


@pytest.mark.parametrize("fault", ["window", "root", "locked", "geometry", "foreground"])
def test_missing_or_changed_window_after_raise_cannot_fallback_to_old_geometry(
    view, monkeypatch, fault
):
    def raise_window(*args, **kwargs):
        view.events.append("raise")
        view.state.raised = True
        if fault == "window":
            view.state.window_present = False
        elif fault == "root":
            view.state.root_matches = False
        elif fault == "locked":
            view.state.locked = True
        elif fault == "geometry":
            view.state.width = 0
        elif fault == "foreground":
            view.state.foreground = False
        return True

    monkeypatch.setattr(W, "bring_to_front", raise_window)
    with pytest.raises(A.AdapterError):
        A._show(view.params, None)
    assert "raise" in view.events
    assert not {"activate", "fit", "capture"} & set(view.events)
    assert not view.output.exists()


def test_wrong_sheet_activation_never_fits_or_captures(view):
    view.state.activate = False
    view.params["sheet"] = 2
    with pytest.raises(A.AdapterError, match="could not be activated"):
        A._show(view.params, None)
    assert "activate" in view.events
    assert not {"fit", "capture"} & set(view.events)


def test_lock_after_fit_stops_capture_with_prior_view_fact(view):
    view.state.during_fit = lambda: setattr(view.state, "locked", True)
    with pytest.raises(A.AdapterError) as caught:
        A._show(view.params, None)
    assert caught.value.details["write_attempted"] is True
    assert "fit" in view.events and "capture" not in view.events
    assert not view.output.exists()


@pytest.mark.parametrize("fault", ["project", "pid", "sheet", "active_view"])
def test_changed_capture_context_preserves_image_fact_but_never_claims_success(view, fault):
    def drift():
        if fault == "project":
            view.identity["document"]["path"] = str(view.project.with_name("foreign.prj"))
        elif fault == "pid":
            view.identity["pid"] = 99
        elif fault == "sheet":
            view.state.sheet = 2
        else:
            view.identity["public_fields"]["ActiveViewHandle"] = 999

    view.state.during_capture = drift
    with pytest.raises(A.AdapterError):
        A._show(view.params, None)
    assert view.events.count("capture") == 1
    assert view.output.exists(), "retain the already-created partial artifact; do not replay"
