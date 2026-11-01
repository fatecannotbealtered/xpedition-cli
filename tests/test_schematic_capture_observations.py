"""Delayed foreground and physical-coverage evidence; native calls=0."""

import pytest

from tests.test_schematic_view_window_state import view as view
from tests.test_win_capture import CaptureAPI
from xpedition_cli import native_com_adapter as A
from xpedition_cli import win_dialogs as W


def test_delayed_foreground_observation_can_succeed_after_request_returned_false(view, monkeypatch):
    def request(*args, **kwargs):
        view.events.append("raise")
        view.state.raised = True
        return False

    monkeypatch.setattr(W, "bring_to_front", request)
    result = A._show(view.params, None)
    assert result["foreground_request_return"] is False
    assert result["foreground"] is True
    assert view.events.count("raise") == view.events.count("capture") == 1


def test_request_true_never_substitutes_for_fresh_foreground(view, monkeypatch):
    monkeypatch.setattr(W, "bring_to_front", lambda *args, **kwargs: True)
    view.state.foreground = False
    with pytest.raises(A.AdapterError) as caught:
        A._show(view.params, None)
    assert caught.value.code == "E_HUMAN_REQUIRED"
    assert not {"fit", "capture"} & set(view.events)
    assert not view.output.exists()


def test_foreground_loss_after_fit_still_stops_capture(view):
    view.state.during_fit = lambda: setattr(view.state, "foreground", False)
    with pytest.raises(A.AdapterError):
        A._show(view.params, None)
    assert "fit" in view.events and "capture" not in view.events
    assert not view.output.exists()


def test_physical_monitor_gap_is_rejected_with_exact_coverage_evidence(tmp_path, monkeypatch):
    api = CaptureAPI()
    api.monitors = [(0, 0, 100, 240), (120, 0, 320, 240)]
    api.install(monkeypatch)
    output = tmp_path / "gap.png"
    with pytest.raises(OSError) as caught:
        W.capture_window(api.hwnd, str(output))
    assert caught.value.details["coordinate_space"] == "physical_pixels"
    assert caught.value.details["frame_bounds"] == list(api.physical)
    assert caught.value.details["monitor_bounds"] == [list(m) for m in api.monitors]
    assert caught.value.details["uncovered_rectangles"] == [[100, 30, 120, 150]]
    assert caught.value.details["output_created"] is False
    assert not api.copied and not output.exists()
    assert api.context == -2


def test_show_retains_physical_coverage_error_details(view, monkeypatch):
    def no_capture(*args):
        error = OSError("physical frame uncovered")
        error.details = {"coordinate_space": "physical_pixels", "frame_bounds": [0, 0, 3, 4]}
        raise error

    monkeypatch.setattr(W, "capture_window", no_capture)
    with pytest.raises(A.AdapterError) as caught:
        A._show(view.params, None)
    assert caught.value.code == "E_IO"
    assert caught.value.details["frame_bounds"] == [0, 0, 3, 4]
    assert not view.output.exists()
