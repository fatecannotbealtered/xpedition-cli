"""File/menu/click acknowledgement is not effective display state."""

from types import SimpleNamespace as NS

import pytest

from xpedition_cli import native_com_adapter as A
from xpedition_cli import win_dialogs as W


def test_existing_top_view_must_match_bound_source(tmp_path):
    (tmp_path / "All On.dcs").write_text('"Option.Planes.Data.Fill" "0"\n')
    target = tmp_path / "Top View.dcs"
    target.write_text("wrong user scheme, do not overwrite")
    with pytest.raises(A.AdapterError):
        A._ensure_top_view_scheme(tmp_path, 4)
    assert target.read_text() == "wrong user scheme, do not overwrite"


@pytest.mark.parametrize("blocked", [None, "wrong_window", "locked"])
def test_clicked_combo_does_not_establish_effective_scheme(tmp_path, monkeypatch, blocked):
    board = tmp_path / "a.pcb"
    board.write_bytes(b"synthetic")
    config = tmp_path / "Config"
    config.mkdir()
    (config / "All On.dcs").write_text("fixture")
    doc = NS(
        FullName=str(board),
        CurrentUnit=2,
        LayerCount=4,
        BoardOutline=object(),
        DisplaySchemes=["Loc: All On"],
        ActiveView=NS(DisplayControl=NS(Name="Loc: Assembly")),
    )
    app = NS()
    monkeypatch.setattr(A, "_application", lambda *a, **k: app)
    monkeypatch.setattr(A, "_active_object", lambda *a: app)
    monkeypatch.setattr(A, "_licensed_document", lambda *a: doc)
    monkeypatch.setattr(A, "_open_layout_document", lambda *a: (doc, []))
    monkeypatch.setattr(A, "_board_counts", lambda *a: {"components": 2})
    monkeypatch.setattr(A, "_board_model", lambda *a, **k: {})
    monkeypatch.setattr(A, "_load_display_scheme", lambda *a: False)
    monkeypatch.setattr(A, "_fit_board_view", lambda *a: True)
    monkeypatch.setattr(A, "_extrema_mm", lambda *a: (0, 0, 10, 10))
    monkeypatch.setattr(
        A, "application_identity", lambda *a: {"pid": 42, "pid_source": {"hwnd": 12}}
    )
    monkeypatch.setattr(A.time, "sleep", lambda *a: None)
    monkeypatch.setattr(
        W,
        "main_window",
        lambda *a: {"hwnd": 12, "title": "PCB", "left": 0, "top": 0, "width": 10, "height": 10},
    )
    monkeypatch.setattr(W, "bring_to_front", lambda *a: True)
    monkeypatch.setattr(W, "_foreground_is_bound", lambda: True)
    monkeypatch.setattr(W, "same_root_window", lambda *a: blocked != "wrong_window")
    monkeypatch.setattr(W, "foreground_matches", lambda *a: True)
    monkeypatch.setattr(W, "desktop_locked", lambda: blocked == "locked")
    monkeypatch.setattr(
        W,
        "choose_combo_item",
        lambda *a, **k: {"item": "Loc: All On", "clicked": True, "selected": None},
    )
    monkeypatch.setenv("XPEDITION_EXPECTED_PID", "42")
    if blocked:
        monkeypatch.setattr(
            A,
            "_load_display_scheme",
            lambda *a: pytest.fail("No native view mutation before identity/unlock"),
        )
        with pytest.raises(A.AdapterError) as caught:
            A._show_board({"project": str(board), "scheme": "Loc: All On"}, None)
        assert caught.value.details["write_attempted"] is False
        return
    result = A._show_board({"project": str(board), "scheme": "Loc: All On"}, None)
    assert result["scheme_applied"] is False


@pytest.mark.parametrize("fault", ["source_drift", "target_race", "unknown", "wrong_existing"])
def test_scheme_binding_rejects_before_write(tmp_path, fault):
    from xpedition_cli.board_view import ensure_scheme, scheme_plan

    config = tmp_path / "Config"
    config.mkdir()
    source = config / "All On.dcs"
    source.write_text('"Option.Planes.Data.Fill" "0"\n')
    plan = scheme_plan(tmp_path / "a.pcb", 4, {"top_view": True}, A)
    assert plan["new_files"] == [
        str(config / "Top View.dcs"),
        str(config / "Top View.dcs.xpedition.json"),
    ]
    if fault == "source_drift":
        source.write_text("changed")
    if fault in {"target_race", "wrong_existing"}:
        (config / "Top View.dcs").write_text("user content")
    with pytest.raises(A.AdapterError):
        if fault == "unknown":
            scheme_plan(tmp_path / "a.pcb", 4, {"scheme": "Loc: Missing"}, A)
        else:
            ensure_scheme(plan, A)
    if fault == "source_drift":
        assert not (config / "Top View.dcs").exists()


def test_correct_scheme_has_explicit_source_target_and_no_overwrite(tmp_path):
    from xpedition_cli.board_view import ensure_scheme, register_new_scheme, scheme_plan

    config = tmp_path / "Config"
    config.mkdir()
    (config / "All On.dcs").write_text('"Option.Planes.Data.Fill" "0"\n')
    plan = scheme_plan(tmp_path / "a.pcb", 4, {"top_view": True}, A)
    assert ensure_scheme(plan, A) is True
    calls = []

    class Control:
        Name = "Loc: All On"

        def LoadScheme(self, name):
            calls.append("LoadScheme")
            return True

        def SaveScheme(self, name):
            calls.append("SaveScheme")
            self.Name = name
            return True

    doc = NS(ActiveView=NS(DisplayControl=Control()))
    assert register_new_scheme(plan, doc, A) is True
    assert calls == ["LoadScheme", "SaveScheme", "LoadScheme"]
    second = scheme_plan(tmp_path / "a.pcb", 4, {"top_view": True}, A)
    assert second["new_files"] == []
    assert ensure_scheme(second, A) is False


def test_combo_does_not_touch_locked_or_unbound_desktop(monkeypatch):
    monkeypatch.setattr(W, "desktop_locked", lambda: True)
    monkeypatch.setattr(W, "main_window", lambda *a: pytest.fail("No GUI access while locked"))
    assert W.choose_combo_item("expeditionpcb.exe", "CMD_DISPLAY_SCHEMES", "Loc: Top View") is None
    monkeypatch.setattr(W, "desktop_locked", lambda: False)
    monkeypatch.setattr(W, "_bound_pid", lambda: None)
    assert W.choose_combo_item("expeditionpcb.exe", "CMD_DISPLAY_SCHEMES", "Loc: Top View") is None


@pytest.mark.parametrize("fault", ["save_rejected", "input_changed", "wrong_name"])
def test_native_registration_failure_retains_artifact_and_never_retries(tmp_path, fault):
    from xpedition_cli.board_view import ensure_scheme, register_new_scheme, scheme_plan

    config = tmp_path / "Config"
    config.mkdir()
    (config / "All On.dcs").write_text("synthetic source")
    plan = scheme_plan(tmp_path / "a.pcb", 4, {"top_view": True}, A)
    ensure_scheme(plan, A)
    target = config / "Top View.dcs"
    calls = []

    class Control:
        Name = "wrong"

        def LoadScheme(self, name):
            calls.append("load")
            return True

        def SaveScheme(self, name):
            calls.append("save")
            return fault != "save_rejected"

    if fault == "input_changed":
        target.write_text("drift")
    with pytest.raises(A.AdapterError):
        register_new_scheme(plan, NS(ActiveView=NS(DisplayControl=Control())), A)
    assert calls.count("save") <= 1
    assert target.is_file()
    assert not (config / "Top View.dcs.xpedition.json").exists()
    with pytest.raises(A.AdapterError):
        scheme_plan(tmp_path / "a.pcb", 4, {"top_view": True}, A)
