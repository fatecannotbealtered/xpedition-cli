"""The real labels binding, with controlled COM objects and no native application."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as bridge


class Collection:
    def __init__(self, items):
        self.items = items
        self.Count = len(items)

    def Item(self, index):
        return self.items[index - 1]


class Format:
    def __init__(self, failure):
        self.Height = 0.6 / bridge.TH_TO_MM
        self.PenWidth = 0.12 / bridge.TH_TO_MM
        self.Orientation = 0
        self.failure = failure
        self.calls = []

    def SetHeight(self, unit, value):
        assert unit == bridge.UNIT_MM
        self.calls.append(("height", value))
        if self.failure == "raise_height":
            raise RuntimeError("private native diagnostic")
        if self.failure != "ignore_height":
            self.Height = value / bridge.TH_TO_MM

    def SetPenWidth(self, unit, value):
        assert unit == bridge.UNIT_MM
        self.calls.append(("pen", value))
        self.PenWidth = value / bridge.TH_TO_MM


class Text:
    def __init__(self, failure):
        self.TextString, self.Type = "C1", bridge.FAB_GFX_SILKSCREEN
        self.Format = Format(failure)
        self.PositionX = self.PositionY = 10 / bridge.TH_TO_MM
        self.failure, self.calls = failure, []

    def Move(self, x, y, unit):
        assert unit == bridge.UNIT_MM
        self.calls.append((x, y))
        if self.failure == "raise_move":
            raise RuntimeError("private native diagnostic")
        if self.failure != "ignore_move":
            self.PositionX, self.PositionY = x / bridge.TH_TO_MM, y / bridge.TH_TO_MM


@pytest.fixture
def labels_doc(tmp_path, monkeypatch):
    def make(failure=None):
        text = Text(failure)
        part = SimpleNamespace(
            RefDes="C1", Placed=True, Pins=Collection([]), FabricationLayerTexts=Collection([text])
        )

        class Document:
            Components = Collection([part])
            FabricationLayerTexts = Collection([])
            saves = 0

            def Save(self):
                self.saves += 1
                if failure == "save":
                    raise RuntimeError("private native diagnostic")
                if failure == "read_back":
                    del text.Format.Height

        doc = Document()
        monkeypatch.setattr(bridge, "_layout_board_path", lambda params: tmp_path / "scratch.pcb")
        monkeypatch.setattr(bridge, "_application", lambda *args, **kwargs: object())
        monkeypatch.setattr(bridge, "_open_layout_document", lambda *args: (doc, []))
        monkeypatch.setattr(bridge, "_licensed_document", lambda app: doc)
        monkeypatch.setattr(bridge, "_board_rect", lambda doc: (0, 0, 40, 30))
        monkeypatch.setattr(bridge, "_hole_records", lambda doc: [])
        monkeypatch.setattr(bridge, "_extrema_mm", lambda obj: (9, 9, 11, 11))
        return doc, text

    return make


def test_labels_preview_never_sets_moves_or_saves(labels_doc):
    doc, text = labels_doc()
    result = bridge._tidy_labels({"height": 0.8, "apply": False}, None)
    assert result["resized"] == 1 and result["moved"] == 1
    assert not result["applied"] and not result["saved"]
    assert not text.calls and not text.Format.calls and not doc.saves


@pytest.mark.parametrize(
    "failure",
    [None, "ignore_height", "ignore_move", "raise_height", "raise_move", "save", "read_back"],
)
def test_legacy_automatic_apply_is_refused_before_any_setter_or_save(labels_doc, failure):
    doc, text = labels_doc(failure)
    plan = bridge._tidy_labels({"height": 0.8, "apply": False}, None)
    with pytest.raises(bridge.AdapterError) as caught:
        bridge._tidy_labels(
            {
                "height": 0.8,
                "apply": True,
                "state_guard": plan["state_guard"],
                "state_digest": plan["state_digest"],
            },
            None,
        )
    assert caught.value.code == "E_UNSUPPORTED"
    assert caught.value.details["write_attempted"] is False
    assert not text.calls and not text.Format.calls and doc.saves == 0


def test_selected_label_keeps_other_label_fixed_and_as_obstacle(labels_doc, monkeypatch):
    from xpedition_cli import board_layout

    doc, target = labels_doc()
    fixed = Text(None)
    fixed.TextString = "R1"
    fixed.PositionX = 20 / bridge.TH_TO_MM
    doc.Components.items.append(
        SimpleNamespace(
            RefDes="R1", Placed=True, Pins=Collection([]), FabricationLayerTexts=Collection([fixed])
        )
    )
    doc.Components.Count += 1
    original = board_layout.place_labels

    def plan(labels, obstacles, board, gap):
        assert [v["refdes"] for v in labels] == ["C1"]
        assert (9, 9, 11, 11) in obstacles  # measured fixed-label extents
        return original(labels, obstacles, board, gap)

    monkeypatch.setattr(board_layout, "place_labels", plan)
    result = bridge._tidy_labels({"refdes": ["C1"], "height": 0.8, "apply": False}, None)
    assert result["protected_refdes"] == ["R1"]
    assert not target.calls and not fixed.calls and not fixed.Format.calls and doc.saves == 0


def test_missing_selected_designator_refuses_before_save(labels_doc):
    doc, text = labels_doc()
    with pytest.raises(bridge.AdapterError) as caught:
        bridge._tidy_labels({"refdes": ["ABSENT"], "apply": False}, None)
    assert caught.value.code == "E_NOT_FOUND"
    assert not doc.saves and not text.calls
