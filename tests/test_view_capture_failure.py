"""A screenshot failure must retain already observed native display facts."""

from types import SimpleNamespace as NS

from xpedition_cli import board_view as V
from xpedition_cli import native_com_adapter as A
from xpedition_cli import win_dialogs as W


def test_capture_failure_keeps_observed_scheme_without_retry(tmp_path, monkeypatch):
    board = tmp_path / "a.pcb"
    board.write_bytes(b"synthetic")
    config = tmp_path / "Config"
    config.mkdir()
    (config / "All On.dcs").write_text("synthetic")
    doc = NS(FullName=str(board), LayerCount=4, DisplaySchemes=["Loc: All On"], BoardOutline=1)
    monkeypatch.setattr(V, "active", lambda *args: (board, object(), doc))
    monkeypatch.setattr(
        A, "application_identity", lambda *args: {"pid": 42, "pid_source": {"hwnd": 12}}
    )
    monkeypatch.setattr(A, "_board_model", lambda *args, **kwargs: {})
    monkeypatch.setattr(A, "_extrema_mm", lambda *args: (0, 0, 10, 10))
    monkeypatch.setattr(A, "_fit_board_view", lambda *args: True)
    monkeypatch.setattr(A, "_load_display_scheme", lambda *args: True)
    monkeypatch.setattr(A.time, "sleep", lambda *args: None)
    monkeypatch.setattr(W, "desktop_locked", lambda: False)
    monkeypatch.setattr(W, "main_window", lambda *args: {"hwnd": 12})
    monkeypatch.setattr(W, "same_root_window", lambda *args: True)
    monkeypatch.setattr(W, "bring_to_front", lambda *args: True)
    monkeypatch.setattr(W, "foreground_matches", lambda *args: True)
    monkeypatch.setattr(
        V,
        "display_state",
        lambda *args: {
            "scheme": "Loc: All On",
            "complete": True,
            "viewport_mm": [-1, -1, 11, 11],
            "visible_objects": [],
        },
    )
    monkeypatch.setenv("XPEDITION_EXPECTED_PID", "42")
    calls = []

    def capture(*args):
        calls.append(True)
        raise OSError("synthetic physical frame cannot be established")

    monkeypatch.setattr(W, "capture_window", capture)
    result = V.show(
        {"project": str(board), "scheme": "Loc: All On", "output": str(tmp_path / "a.png")}, None, A
    )
    assert calls == [True]
    assert result["scheme_applied"] is True
    assert result["native_load_acknowledged"] is True
    assert result["screenshot"]["complete"] is False
    assert result["screenshot"]["error"]["code"] == "E_IO"
    assert not (tmp_path / "a.png").exists()
