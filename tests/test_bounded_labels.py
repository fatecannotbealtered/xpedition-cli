"""Native-shaped formal text getters, bounded lifecycle and public discovery."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from xpedition_cli import board_labels as labels
from xpedition_cli import native_com_adapter as a
from xpedition_cli.cell_refresh import physical_model


class Collection:
    def __init__(self, rows):
        self.rows = rows

    @property
    def Count(self):
        return len(self.rows)

    def Item(self, index):
        return self.rows[index - 1]


class Format:
    Font = "NativeVector"
    Bold = Italic = Underline = Mirrored = False
    HorizontalJust = VerticalJust = Attributes = 0
    AspectRatio = LineSpacing = 1.0
    Orientation = 0.0

    def IsValid(self):
        return True

    def IsVectorFont(self):
        return True

    def GetHeight(self, unit):
        assert unit == a.UNIT_MM
        return 0.8

    def GetPenWidth(self, unit):
        assert unit == a.UNIT_MM
        return 0.1


class Text:
    Type = 2
    TextType = 1
    Side = 1
    SideName = "Top"
    Layer = -99
    LayerName = ""
    FixLock = DisplayCondition = 0
    Reserved = False
    ReservedBy = ""
    ObjectClass = 2048
    OriginalLayer = -99

    def __init__(self, uid, owner, doc):
        self.UniqueId, self.TextString = uid, owner.RefDes
        self.Component = self.Parent = owner
        self.Document = doc
        self.Format = Format()
        self.x, self.y = 10.0, 10.0
        self.calls = []
        self.failure = None

    @property
    def Name(self):
        return self.TextString

    def IsDimensionText(self):
        return False

    def IsFlexTextValid(self):
        return True

    @property
    def PositionX(self):
        return self.x / a.TH_TO_MM

    @property
    def PositionY(self):
        return self.y / a.TH_TO_MM

    @property
    def Extrema(self):
        if self.failure == "missing_extrema":
            raise RuntimeError("native unavailable")
        return SimpleNamespace(
            GetMinX=lambda unit: self.x - 1,
            GetMaxX=lambda unit: self.x + 1,
            GetMinY=lambda unit: self.y - 0.4,
            GetMaxY=lambda unit: self.y + 0.4,
        )

    def IsValid(self):
        return True

    def GetPositionX(self, unit):
        assert unit == a.UNIT_MM
        return self.x

    def GetPositionY(self, unit):
        assert unit == a.UNIT_MM
        return self.y

    def StrokeText(self, unit):
        assert unit == a.UNIT_MM
        return [0.1, self.x - 0.5, self.y, 0, self.x + 0.5, self.y, 2]

    def Move(self, x, y, unit):
        assert unit == a.UNIT_MM
        self.calls.append((x, y))
        if self.failure == "lost_reply":
            self.x, self.y = x, y
            raise TimeoutError("reply lost")
        if self.failure != "ignored":
            self.x, self.y = x, y
        if self.failure == "protected_change":
            self.Document.protected = "changed"
        if self.failure == "format_change":
            self.Format.Font = "unexpected"
        if self.failure == "readback_missing":
            self.LayerName = None


@pytest.fixture
def native(tmp_path, monkeypatch):
    board = tmp_path / "board.pcb"
    board.write_bytes(b"native source")
    owner = SimpleNamespace(UniqueId="component1", RefDes="J1", Placed=True, ObjectClass=1)

    class Doc:
        ObjectClass = 2
        FullName = str(board)
        protected = "original"
        saves = closes = opens = 0
        failure = None

        def Save(self):
            self.saves += 1
            if self.failure == "save":
                return False
            if self.failure == "save_none":
                return None
            if self.failure == "save_integer":
                return 1
            if self.failure == "save_exception":
                raise TimeoutError("Save reply lost")
            return True

        def Close(self, saving):
            assert saving is True
            self.closes += 1
            if self.failure == "close":
                return False
            app.ActiveDocument = None
            if self.failure == "close_none":
                return None
            if self.failure == "close_integer":
                return 1
            if self.failure == "close_exception":
                raise TimeoutError("Close reply lost after closing")
            return True

    doc = Doc()
    text = Text("text1", owner, doc)
    owner.FabricationLayerTexts = Collection([text])
    doc.Components = Collection([owner])
    doc.FabricationLayerTexts = Collection([text])
    app = SimpleNamespace(ActiveDocument=doc)

    def model(doc, **kwargs):
        row = a._text_record(text, owner.RefDes)
        return {
            "texts": [row],
            "pads": [],
            "silk": [],
            "traces": [],
            "planes": [],
            "holes": [],
            "components": [],
            "protected": doc.protected,
            "layout_observation": {"trace_envelopes": []},
        }

    def capture(doc, adapter, **kwargs):
        return labels._canonical(
            {
                "model": physical_model(model(doc)),
                "components": [{"uid": owner.UniqueId, "refdes": owner.RefDes, "pose": [0, 0]}],
                "stackup": [],
            }
        )

    def open_doc(app, path):
        assert path == board
        doc.opens += 1
        if doc.failure == "open":
            raise RuntimeError("cannot reopen")
        app.ActiveDocument = doc
        if doc.failure == "reopen_mismatch":
            doc.protected = "changed on disk"
        return doc, []

    monkeypatch.setattr(labels, "active", lambda *args, **kwargs: (board, board, app, doc))
    monkeypatch.setattr(labels, "capture", capture)
    monkeypatch.setattr(a, "_board_model", model)
    monkeypatch.setattr(a, "_licensed_document", lambda app: app.ActiveDocument)
    monkeypatch.setattr(a, "_open_layout_document", open_doc)
    monkeypatch.setattr(
        labels, "_identity", lambda *args: {"pid": 1, "birth": "same", "board": str(board)}
    )
    request = {
        "unit": "mm",
        "moves": [
            {
                "refdes": "J1",
                "text_uid": "text1",
                "text": "J1",
                "type": 2,
                "text_type": 1,
                "side": "top",
                "side_name": "Top",
                "layer": -99,
                "layer_name": "",
                "from": [10.0, 10.0],
                "to": [10.0, 12.0],
            }
        ],
    }
    params = {"project": str(board), "label_moves": request}
    return doc, text, params


def preview_and_apply(params):
    plan = a._tidy_labels({**params, "apply": False}, None)
    return a._tidy_labels(
        {
            **params,
            "apply": True,
            "state_guard": plan["state_guard"],
            "state_digest": plan["state_digest"],
        },
        None,
    )


def test_actual_native_getter_chain_binds_before_move_and_normal_save_reopen(native):
    doc, text, params = native
    plan = a._tidy_labels({**params, "apply": False}, None)
    assert plan["label_moves"]["before"]["texts"][0]["uid"] == "text1"
    assert plan["reminders_projected"]["layout_reminder_coverage"]["status"] == "partial"
    assert text.calls == [] and (doc.saves, doc.closes, doc.opens) == (0, 0, 0)
    done = preview_and_apply(params)
    assert done["persistence_verified"] and done["saved"] and done["verification"]["valid"]
    assert text.calls == [(10, 12)] and (doc.saves, doc.closes, doc.opens) == (1, 1, 1)
    assert text.Format.GetHeight(a.UNIT_MM) == 0.8
    assert [r["return_kind"] for r in done["native_calls_returned"]] == [
        "none",
        "true",
        "true",
        "document_and_prompts",
    ]
    assert labels.persistence_evidence(done, done["label_moves"], done["state_reopened"])["valid"]


def test_placement_capture_uses_real_text_getter_chain_and_preserves_exact_format(native):
    from xpedition_cli.placement_text_witness import capture

    doc, text, _ = native
    text.Format.AspectRatio = 1.0000000001
    text.Format.Height = 0.8 / a.TH_TO_MM
    state = {
        "components": [{"uid": text.Component.UniqueId, "refdes": text.Component.RefDes}],
        "model": {"texts": [labels._canonical(a._text_record(text, text.Component.RefDes))]},
    }
    result = capture(doc, a, state)
    semantic = result["text_semantic_witness"][0]
    assert semantic["format"]["AspectRatio"] == 1.0000000001
    assert semantic["format"]["Font"] == "NativeVector"
    assert semantic["format"]["valid"] is True and semantic["format"]["is_vector_font"] is True
    assert semantic["native_name"] == "J1" and semantic["object_class"] == 2048
    assert semantic["original_layer"] == -99 and semantic["is_dimension_text"] is False
    assert semantic["is_flex_text_valid"] is True
    assert semantic["owner"]["uid"] == text.Component.UniqueId
    assert semantic["parent"]["refdes"] == text.Component.RefDes
    assert semantic["position"] == [10.0, 10.0]
    assert result["text_native_witness"][0]["stroke_array_mm"] == text.StrokeText(a.UNIT_MM)
    assert doc.saves == doc.closes == doc.opens == 0


def test_placement_capture_refuses_geometry_that_changes_between_two_reads(native):
    from xpedition_cli.errors import CLIError
    from xpedition_cli.placement_text_witness import capture

    doc, text, _ = native
    text.Format.Height = 0.8 / a.TH_TO_MM
    state = {
        "components": [{"uid": text.Component.UniqueId, "refdes": text.Component.RefDes}],
        "model": {"texts": [labels._canonical(a._text_record(text, text.Component.RefDes))]},
    }
    original = text.StrokeText
    calls = []

    def changing(unit):
        result = original(unit)
        calls.append(1)
        if len(calls) == 2:
            result[1] += 1e-7
        return result

    text.StrokeText = changing
    with pytest.raises(CLIError, match="changed during semantic capture"):
        capture(doc, a, state)
    assert len(calls) == 2
    assert doc.saves == doc.closes == doc.opens == 0


def test_placement_capture_refuses_an_unavailable_full_format_getter(native):
    from xpedition_cli.placement_text_witness import capture

    doc, text, _ = native
    text.Format.Height = 0.8 / a.TH_TO_MM
    text.Format.IsVectorFont = lambda: None
    state = {
        "components": [{"uid": text.Component.UniqueId, "refdes": text.Component.RefDes}],
        "model": {"texts": [labels._canonical(a._text_record(text, text.Component.RefDes))]},
    }
    with pytest.raises(ValueError, match="identity or format getters are unavailable"):
        capture(doc, a, state)
    assert doc.saves == doc.closes == doc.opens == 0


def test_actual_rc_fabrication_shape_keeps_negative_raw_association_without_layer_inference(native):
    from pathlib import Path

    doc, text, params = native
    text.Format.Font = "vf_std"
    assembly = Text("assembly-text", text.Component, doc)
    assembly.Type = 1
    assembly.Format.Font = "vf_std"
    doc.FabricationLayerTexts.rows.append(assembly)
    text.Component.FabricationLayerTexts.rows.append(assembly)
    observed = labels.inventory(doc, Path(params["project"]), a)
    assert observed["complete"] and observed["observed_count"] == observed["enumerated_count"] == 2
    assert [r["type"] for r in observed["texts"]] == [2, 1]
    for row in observed["texts"]:
        assert row["side_code"] == 1 and row["side"] == "top" and row["side_name"] == "Top"
        assert row["layer"] == -99 and row["layer_name"] == ""
        assert row["fabrication_identity"]["type"] in (1, 2)
        association = row["stackup_layer_association"]
        assert association["applicability"] == "not_applicable_to_assembly_or_silkscreen"
        assert association["negative_value_meaning"] == "unverified"
        assert association["used_for_silk_identity"] is False
    assert not text.calls and doc.saves == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("SideName", None),
        ("SideName", ""),
        ("SideName", "Bottom"),
        ("SideName", "unproven-layer-name"),
        ("Side", None),
        ("Side", 0),
        ("Side", 513),
        ("Type", None),
        ("Type", 2.5),
    ],
)
def test_fabrication_identity_requires_actual_consistent_type_side_and_name(native, field, value):
    from pathlib import Path

    doc, text, params = native
    setattr(text, field, value)
    observed = labels.inventory(doc, Path(params["project"]), a)
    assert not observed["complete"] and observed["unread_objects"]
    with pytest.raises(a.AdapterError) as caught:
        preview_and_apply(params)
    assert caught.value.details["write_attempted"] is False
    assert not text.calls and (doc.saves, doc.closes, doc.opens) == (0, 0, 0)


@pytest.mark.parametrize("field", ["SideName", "Side", "Type", "Layer", "LayerName"])
def test_nonapplicable_stackup_fields_still_must_be_actually_read_no_getter_failure_skip(
    native, field
):
    from pathlib import Path

    doc, text, params = native

    def fail_getter(self):
        raise RuntimeError("native property unavailable")

    text.__class__ = type("FailingFabricationText", (Text,), {field: property(fail_getter)})
    observed = labels.inventory(doc, Path(params["project"]), a)
    assert not observed["complete"] and "Text." + field in observed["unread_objects"][0]["reason"]
    with pytest.raises(a.AdapterError) as caught:
        preview_and_apply(params)
    assert caught.value.details["write_attempted"] is False
    assert not text.calls and (doc.saves, doc.closes, doc.opens) == (0, 0, 0)


def test_silk_raw_association_remains_frozen_in_file_and_drift_guard(native):
    doc, text, params = native
    plan = a._tidy_labels({**params, "apply": False}, None)
    text.Layer = -98  # Unknown numeric meaning is never normalized to the observed-99.
    with pytest.raises(a.AdapterError) as caught:
        a._tidy_labels(
            {
                **params,
                "apply": True,
                "state_guard": plan["state_guard"],
                "state_digest": plan["state_digest"],
            },
            None,
        )
    assert caught.value.details["write_attempted"] is False
    assert not text.calls and doc.saves == 0


@pytest.mark.parametrize(
    "failure", ["ignored", "lost_reply", "protected_change", "format_change", "readback_missing"]
)
def test_any_post_move_incomplete_or_protected_change_stops_save_close_open(native, failure):
    doc, text, params = native
    text.failure = failure
    with pytest.raises(a.AdapterError) as caught:
        preview_and_apply(params)
    actual = caught.value.details["native_result"]
    assert actual["write_attempted"] is True
    assert actual["saved"] is False and actual["persistence_verified"] is False
    assert len(text.calls) == 1 and (doc.saves, doc.closes, doc.opens) == (0, 0, 0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("UniqueId", "0"),
        ("LayerName", None),
        ("Side", 0),
        ("Layer", None),
        ("Type", 3),
        ("TextType", 4),
    ],
)
def test_missing_or_wrong_real_native_identity_refuses_before_move(native, field, value):
    doc, text, params = native
    setattr(text, field, value)
    with pytest.raises(a.AdapterError):
        preview_and_apply(params)
    assert text.calls == [] and (doc.saves, doc.closes, doc.opens) == (0, 0, 0)


def test_missing_actual_envelope_and_duplicate_uid_are_publicly_unread(native):
    doc, text, params = native
    text.failure = "missing_extrema"
    report = labels.inventory(doc, __import__("pathlib").Path(params["project"]), a)
    assert not report["complete"] and report["observed_count"] == 0 and report["unread_objects"]
    with pytest.raises(a.AdapterError):
        preview_and_apply(params)
    text.failure = None
    doc.FabricationLayerTexts.rows.append(text)
    report = labels.inventory(doc, __import__("pathlib").Path(params["project"]), a)
    assert not report["complete"] and report["observed_count"] == 1
    assert text.calls == [] and doc.saves == 0


def test_inventory_preserves_second_identity_when_first_item_getter_fails(native):
    from pathlib import Path

    doc, text, params = native

    class PartialCollection:
        Count = 2

        def Item(self, index):
            if index == 1:
                raise RuntimeError("unreadable first native text")
            return text

    doc.FabricationLayerTexts = PartialCollection()
    observed = labels.inventory(doc, Path(params["project"]), a)
    assert observed["complete"] is False
    assert observed["unread_objects"][0]["observation_index"] == 0
    assert observed["texts"][0]["uid"] == "text1"
    assert observed["texts"][0]["observation_index"] == 1
    assert observed["texts"][0]["position"] == [10, 10]


@pytest.mark.parametrize("change", ["position", "input", "geometry", "generation"])
def test_frozen_state_drift_refuses_no_move(native, monkeypatch, change):
    doc, text, params = native
    plan = a._tidy_labels({**params, "apply": False}, None)
    if change == "position":
        text.x += 1
    elif change == "input":
        params["label_moves"]["moves"][0]["to"][0] += 1
    elif change == "geometry":
        doc.protected = "changed"
    else:
        monkeypatch.setattr(labels, "_identity", lambda *args: {"birth": "different"})
    with pytest.raises(a.AdapterError) as caught:
        a._tidy_labels(
            {
                **params,
                "apply": True,
                "state_guard": plan["state_guard"],
                "state_digest": plan["state_digest"],
            },
            None,
        )
    assert caught.value.details["write_attempted"] is False
    assert text.calls == [] and (doc.saves, doc.closes, doc.opens) == (0, 0, 0)


@pytest.mark.parametrize(
    "failure,counts,saved",
    [
        ("save", (1, 0, 0), False),
        ("close", (1, 1, 0), True),
        ("open", (1, 1, 1), True),
        ("reopen_mismatch", (1, 1, 1), True),
    ],
)
def test_persistence_stage_failures_preserve_actual_earlier_facts(native, failure, counts, saved):
    doc, text, params = native
    doc.failure = failure
    with pytest.raises(a.AdapterError) as caught:
        preview_and_apply(params)
    actual = caught.value.details["native_result"]
    assert actual["saved"] is saved and not actual["persistence_verified"]
    assert (doc.saves, doc.closes, doc.opens) == counts and len(text.calls) == 1


@pytest.mark.parametrize("failure", ["save_none", "save_integer", "save", "save_exception"])
def test_formal_bool_save_requires_true_and_preserves_move_without_close_or_open(native, failure):
    doc, text, params = native
    doc.failure = failure
    with pytest.raises(a.AdapterError) as caught:
        preview_and_apply(params)
    actual = caught.value.details["native_result"]
    assert actual["applied"] is True and actual["change_status"] == "applied"
    assert actual["state_after"] and actual["verification"]["valid"]
    assert actual["saved"] is False and actual["persistence_verified"] is False
    assert actual["stages"]["save"] == "started"
    assert actual["stages"]["close"] == actual["stages"]["reopen"] == "not_started"
    assert text.calls == [(10, 12)] and (doc.saves, doc.closes, doc.opens) == (1, 0, 0)
    assert [r["call"] for r in actual["native_calls_returned"]] == ["Move", "Save"]
    save = actual["native_calls_returned"][-1]
    assert save["returned"] is (failure != "save_exception")
    if failure != "save_exception":
        assert (
            save["return_kind"]
            == {"save_none": "none", "save_integer": "int", "save": "false"}[failure]
        )


@pytest.mark.parametrize("failure", ["close_none", "close_integer", "close", "close_exception"])
def test_formal_bool_close_requires_true_without_open_or_close_replay(native, failure):
    doc, text, params = native
    doc.failure = failure
    with pytest.raises(a.AdapterError) as caught:
        preview_and_apply(params)
    actual = caught.value.details["native_result"]
    assert actual["applied"] and actual["saved"] and not actual["persistence_verified"]
    assert actual["state_after"] and actual["verification"]["valid"]
    assert actual["stages"]["save"] == "acknowledged"
    assert actual["stages"]["close"] == "started"
    assert actual["stages"]["reopen"] == "not_started"
    assert text.calls == [(10, 12)] and (doc.saves, doc.closes, doc.opens) == (1, 1, 0)
    assert [r["call"] for r in actual["native_calls_returned"]] == ["Move", "Save", "Close"]
    close = actual["native_calls_returned"][-1]
    assert close["returned"] is (failure != "close_exception")
    if failure in ("close_none", "close_integer"):
        # Observed closure cannot replace the declared BOOL acknowledgement.
        assert close["active_document_none"] is True
        assert close["return_kind"] == {"close_none": "none", "close_integer": "int"}[failure]


def test_legacy_direct_apply_is_disabled_before_application_lookup(monkeypatch):
    monkeypatch.setattr(a, "_application", lambda *args, **kwargs: pytest.fail("no COM attach"))
    with pytest.raises(a.AdapterError) as caught:
        a._tidy_labels({"apply": True, "height": 0.8}, None)
    assert caught.value.code == "E_UNSUPPORTED" and caught.value.details["write_attempted"] is False


def test_direct_bounded_mixed_resize_rejects_before_native_attach(native, monkeypatch):
    doc, text, params = native
    monkeypatch.setattr(labels, "active", lambda *args, **kwargs: pytest.fail("no attach"))
    with pytest.raises(a.AdapterError) as caught:
        a._tidy_labels({**params, "height": 0.8, "apply": True}, None)
    assert caught.value.code == "E_USAGE" and not caught.value.details["write_attempted"]
    assert not text.calls and doc.saves == 0


def test_parent_same_class_wrong_component_refuses(native):
    doc, text, params = native
    text.Parent = SimpleNamespace(ObjectClass=1, UniqueId="other", RefDes="J2")
    with pytest.raises(a.AdapterError):
        preview_and_apply(params)
    assert not text.calls and doc.saves == 0


@pytest.mark.parametrize(
    "failure", [None, "missing_inventory", "format_only", "material", "forged_native_result"]
)
def test_receipt_uses_independent_full_text_and_geometry_not_native_success_flag(native, failure):
    from pathlib import Path

    from xpedition_cli.task_readback import verify_task

    doc, text, params = native
    before = labels.observe(doc, Path(params["project"]), a)
    done = preview_and_apply(params)
    after = copy.deepcopy(done["state_reopened"])

    def snapshot(state):
        return {
            "extensions": {
                "facts": {"cell_refresh": {"geometry": state["geometry"], "label_state": state}}
            }
        }

    observed = snapshot(after)
    if failure == "missing_inventory":
        del observed["extensions"]["facts"]["cell_refresh"]["label_state"]
    elif failure == "format_only":
        after["texts"][0]["format"]["Font"] = "different native font"
    elif failure == "material":
        after["geometry"]["model"]["protected"] = "changed"
    elif failure == "forged_native_result":
        done["state_reopened"]["texts"][0]["format"]["Font"] = "unobserved claim"
    proof = verify_task(
        "pcb silkscreen labels",
        {"file": "moves.json"},
        done,
        snapshot(before),
        observed,
        {"extensions": {"legacy_preview": {"label_moves": done["label_moves"]}}},
    )
    assert proof["valid"] is (failure is None)


def test_independent_observer_label_mode_is_opt_in_and_keeps_native_failures(native, monkeypatch):
    from pathlib import Path

    from xpedition_cli import cell_refresh

    doc, text, params = native
    board = Path(params["project"])
    monkeypatch.setattr(cell_refresh, "active", lambda *args, **kwargs: (board, board, None, doc))
    monkeypatch.setattr(cell_refresh, "capture", labels.capture)
    ordinary = cell_refresh.observe(params, None, a)
    assert "label_state" not in ordinary
    observed = cell_refresh.observe({**params, "label_objects": True}, None, a)
    assert observed["label_state"]["texts"][0]["uid"] == "text1"
    text.LayerName = None
    with pytest.raises(a.AdapterError) as caught:
        cell_refresh.observe({**params, "label_objects": True}, None, a)
    assert caught.value.code == "E_VERIFY_INCOMPLETE"
    assert caught.value.details["label_inventory"]["complete"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("Type", 2.5),
        ("TextType", 1.5),
        ("FixLock", 0.5),
        ("DisplayCondition", None),
        ("TextString", None),
        ("Side", True),
        ("Layer", 1.5),
    ],
)
def test_real_native_text_scalars_are_never_coerced(native, field, value):
    doc, text, params = native
    setattr(text, field, value)
    with pytest.raises(a.AdapterError) as caught:
        preview_and_apply(params)
    assert caught.value.details["write_attempted"] is False
    assert not text.calls and (doc.saves, doc.closes, doc.opens) == (0, 0, 0)


@pytest.mark.parametrize("owner_field,value", [("RefDes", None), ("ObjectClass", 1.5)])
def test_real_native_owner_scalars_are_not_folded_into_valid_identity(native, owner_field, value):
    doc, text, params = native
    setattr(text.Component, owner_field, value)
    with pytest.raises(a.AdapterError):
        preview_and_apply(params)
    assert not text.calls and doc.saves == 0


def test_noninteger_mm_translation_uses_observed_target_geometry_precision(native):
    doc, text, params = native
    params["label_moves"]["moves"][0]["to"] = [10.0123, 12.4567]
    result = preview_and_apply(params)
    assert result["persistence_verified"] is True
    assert text.calls == [(10.0123, 12.4567)]
    assert (doc.saves, doc.closes, doc.opens) == (1, 1, 1)


def test_unrepresentable_delta_refuses_before_move_without_rewriting_raw_input(native):
    doc, text, params = native
    params["label_moves"]["moves"][0]["to"] = [10.00001, 12]
    original = copy.deepcopy(params)
    with pytest.raises(a.AdapterError) as caught:
        preview_and_apply(params)
    assert caught.value.code == "E_UNSUPPORTED" and not caught.value.details["write_attempted"]
    assert params == original and not text.calls and doc.saves == 0


def test_raw_native_rounding_tie_predicts_actual_stroke_not_rounded_before_plus_delta(native):
    doc, text, params = native
    text.x = 10.62345  # The first native stroke x is exactly the review's10.12345.
    move = params["label_moves"]["moves"][0]
    move["from"] = [10.62345, 10]
    move["to"] = [10.62355, 10]
    preview = a._tidy_labels({**params, "apply": False}, None)
    row = preview["label_moves"]["before"]["texts"][0]
    assert row["native_geometry_witness"]["stroke_array_mm"][1] == repr(10.12345)
    assert round(round(10.12345, 4) + 0.0001, 4) != round(10.12345 + 0.0001, 4)
    result = preview_and_apply(params)
    assert result["persistence_verified"] is True
    assert result["state_after"]["geometry"]["model"]["texts"][0]["strokes"][0][0][0] == round(
        10.12345 + 0.0001, 4
    )
    assert (doc.saves, doc.closes, doc.opens) == (1, 1, 1)


@pytest.mark.parametrize(
    "failure",
    [
        "stages",
        "missing_save",
        "extra_open",
        "false_save",
        "none_save",
        "none_close",
        "false_close",
        "save_reply_missing",
        "close_reply_missing",
        "close_not_observed",
        "missing_reopen_identity",
        "wrong_reopen_identity",
        "wrong_state_digest",
    ],
)
def test_receipt_rejects_persistence_flags_without_actual_lifecycle_evidence(native, failure):
    from pathlib import Path

    from xpedition_cli.task_readback import verify_task

    doc, text, params = native
    before = labels.observe(doc, Path(params["project"]), a)
    result = preview_and_apply(params)
    if failure == "stages":
        result["stages"] = dict.fromkeys(result["stages"], "not_started")
    elif failure == "missing_save":
        result["native_calls_returned"] = [
            r for r in result["native_calls_returned"] if r["call"] != "Save"
        ]
    elif failure == "extra_open":
        result["native_calls_returned"].append(copy.deepcopy(result["native_calls_returned"][-1]))
    elif failure == "false_save":
        result["native_calls_returned"][-3]["return_kind"] = "false"
    elif failure in ("none_save", "none_close", "false_close"):
        index = -3 if failure == "none_save" else -2
        result["native_calls_returned"][index]["return_kind"] = (
            "false" if failure == "false_close" else "none"
        )
    elif failure in ("save_reply_missing", "close_reply_missing"):
        index = -3 if failure == "save_reply_missing" else -2
        result["native_calls_returned"][index]["returned"] = False
    elif failure == "close_not_observed":
        result["native_calls_returned"][-2]["active_document_none"] = False
    elif failure == "missing_reopen_identity":
        del result["reopen_evidence"]
    elif failure == "wrong_reopen_identity":
        result["reopen_evidence"]["identity"] = {"birth": "different"}
    else:
        result["reopen_evidence"]["state_sha256"] = "0" * 64

    def snap(state):
        return {
            "extensions": {
                "facts": {"cell_refresh": {"geometry": state["geometry"], "label_state": state}}
            }
        }

    proof = verify_task(
        "pcb silkscreen labels",
        {"file": "moves.json"},
        result,
        snap(before),
        snap(result["state_reopened"]),
        {"extensions": {"legacy_preview": {"label_moves": result["label_moves"]}}},
    )
    assert result["saved"] and result["persistence_verified"] and not proof["valid"]


@pytest.fixture
def strict_application(tmp_path, monkeypatch):
    """Exercise application_identity's formal type/getter chain, not _identity mocking."""
    import ctypes

    from xpedition_cli import native_identity, native_process, native_release_preflight

    executable = tmp_path / "XP/wg/win64/bin/ExpeditionPCB.exe"
    board = tmp_path / "actual.pcb"
    data = {
        "Name": "Xpedition Layout",
        "FullName": str(executable),
        "Version": "2604",
        "InstanceGuid": "{BBC5A95D-A8EF-44D3-A3F0-B347F8D21DF4}",
        "HWND": 12345,
        "ProcessID": 77,
        "ActiveDocument": SimpleNamespace(FullName=str(board)),
    }
    lib_values = [native_release_preflight.TYPE_LIBRARIES["pcb"][0], 0, 3, 151, 0, 8]

    class TypeInfo:
        def GetContainingTypeLib(self):
            return SimpleNamespace(
                GetLibAttr=lambda: tuple(lib_values), GetDocumentation=lambda i: ("MGCPCB",)
            ), 0

        def GetTypeAttr(self):
            return SimpleNamespace(cFuncs=len(data))

        def GetFuncDesc(self, index):
            return (index, None, None, None, 2)

        def GetNames(self, index):
            return (list(data)[index],)

    class App:
        _oleobj_ = SimpleNamespace(GetTypeInfo=lambda: TypeInfo())

        def __getattr__(self, name):
            value = data[name]
            if isinstance(value, BaseException):
                raise value
            return value

    app = App()

    def map_window(hwnd, pid_pointer):
        assert hwnd == data["HWND"]
        ctypes.cast(pid_pointer, ctypes.POINTER(ctypes.c_ulong))[0] = 77

    monkeypatch.setattr(
        ctypes.windll, "user32", SimpleNamespace(GetWindowThreadProcessId=map_window)
    )
    generation = {
        "pid": 77,
        "alive": True,
        "birth_filetime_100ns": "133700000000000000",
        "executable": str(executable),
    }
    monkeypatch.setattr(
        native_release_preflight, "process_generation", lambda pid: dict(generation)
    )
    monkeypatch.setattr(
        native_process,
        "processes",
        lambda domain: [
            {"pid": 77, "parent_pid": 1, "executable": str(executable)},
            {"pid": 99, "parent_pid": 1, "executable": "unrelated protected session"},
        ],
    )
    return app, board, data, lib_values, generation, native_identity


def test_actual_application_getter_chain_pins_only_retained_target(strict_application):
    app, board, *_ = strict_application
    pin = labels._identity(app, board)
    assert pin["application"]["type_library"]["major"] == 151
    assert pin["application"]["pid_source"]["api"] == "GetWindowThreadProcessId"
    assert pin["generation"]["birth_filetime_100ns"] == "133700000000000000"


def test_closed_application_pin_does_not_claim_a_current_document(strict_application):
    app, board, data, *_ = strict_application
    before = labels._identity(app, board)
    data["ActiveDocument"] = None
    closed = labels._application_identity(app)
    assert closed == {key: before[key] for key in ("application", "generation")}
    assert "board" not in closed
    with pytest.raises((AttributeError, ValueError)):
        labels._identity(app, board)


@pytest.mark.parametrize(
    "failure",
    [
        "type_error",
        "wrong_version",
        "zero_guid",
        "bad_guid",
        "guid_getter",
        "bad_hwnd",
        "image_mismatch",
        "birth_missing",
        "wrong_document",
    ],
)
def test_actual_application_getter_identity_failures_refuse(strict_application, failure):
    app, board, data, lib_values, generation, _ = strict_application
    if failure == "type_error":
        app._oleobj_ = SimpleNamespace(
            GetTypeInfo=lambda: (_ for _ in ()).throw(RuntimeError("type getter failed"))
        )
    elif failure == "wrong_version":
        lib_values[3] = 150
    elif failure == "zero_guid":
        data["InstanceGuid"] = "00000000-0000-0000-0000-000000000000"
    elif failure == "bad_guid":
        data["InstanceGuid"] = "nonempty-but-invalid"
    elif failure == "guid_getter":
        data["InstanceGuid"] = RuntimeError("getter failed")
    elif failure == "bad_hwnd":
        data["HWND"] = True
    elif failure == "image_mismatch":
        data["FullName"] = str(board.parent / "other.exe")
    elif failure == "birth_missing":
        del generation["birth_filetime_100ns"]
    else:
        data["ActiveDocument"] = SimpleNamespace(FullName=str(board.parent / "wrong.pcb"))
    with pytest.raises((a.AdapterError, ValueError)):
        labels._identity(app, board)


def test_real_active_application_chain_does_not_quiet_gui(native, monkeypatch):
    from xpedition_cli import cell_refresh

    doc, text, params = native
    real_application = a._application
    monkeypatch.setattr(labels, "active", cell_refresh.active)
    monkeypatch.setattr(a, "_application", real_application)
    monkeypatch.setattr(
        a, "_layout_board_path", lambda p: __import__("pathlib").Path(params["project"])
    )
    monkeypatch.setattr(a, "_configure_environment", lambda: None)
    monkeypatch.setattr(a, "_active_object", lambda client: SimpleNamespace(ActiveDocument=doc))
    monkeypatch.setattr(a, "_typed", lambda client, app: app)
    monkeypatch.setattr(
        a, "_quiet_gui", lambda *args: pytest.fail("labels must not write GUI options")
    )
    monkeypatch.delenv("XPEDITION_OBSERVE_ONLY", raising=False)
    monkeypatch.delenv("XPEDITION_REQUIRE_EXISTING", raising=False)
    preview = a._tidy_labels({**params, "apply": False}, None)
    assert preview["write_attempted"] is False and not text.calls and doc.saves == 0
    observation = cell_refresh.observe({**params, "label_objects": True}, None, a)
    assert observation["label_state"]["texts"][0]["uid"] == "text1"


def test_public_file_process_preview_confirm_and_duplicate_no_replay(cli, adapter, tmp_path):
    board = tmp_path / "board.pcb"
    board.touch()
    request = {
        "unit": "mm",
        "moves": [
            {
                "refdes": "J1",
                "text_uid": "t",
                "text": "J1",
                "type": 2,
                "text_type": 1,
                "side": "top",
                "side_name": "Top",
                "layer": -99,
                "layer_name": "",
                "from": [1, 1],
                "to": [1, 2],
            }
        ],
    }
    source = tmp_path / "文字 调整.json"
    source.write_text(json.dumps(request))
    answer = {
        "pcb": str(board),
        "state_guard": labels.GUARD,
        "state_digest": "a" * 64,
        "labels": request["moves"],
        "reminders_projected": {"warnings": [], "coverage": "partial"},
    }
    adapter.on(
        "tidy_labels",
        lambda p: {
            **answer,
            "applied": p["apply"],
            "saved": p["apply"],
            "persistence_verified": p["apply"],
            "verification": {"valid": p["apply"]},
        },
    )
    argv = ["pcb", "silkscreen", "labels", "--project", str(board), "--file", str(source)]
    code, planned = cli(*argv, "--dry-run")
    assert code == 0 and planned["data"]["preview"]["can_apply"] is True
    token = planned["data"]["confirm_token"]
    code, done = cli(*argv, "--confirm", token)
    assert code == 0, done
    assert sum(p["apply"] for p in adapter.params("tidy_labels")) == 1
    code, done = cli(*argv, "--confirm", token)
    assert code == 6 and sum(p["apply"] for p in adapter.params("tidy_labels")) == 1
    code, found = cli("reference", "--command", "pcb silkscreen labels")
    assert code == 0 and "file_json_schema" in str(found)


def test_public_input_changed_after_preview_rejects_before_apply(cli, adapter, tmp_path):
    board = tmp_path / "board.pcb"
    board.touch()
    source = tmp_path / "moves.json"
    request = {
        "unit": "mm",
        "moves": [
            {
                "refdes": "J1",
                "text_uid": "t",
                "text": "J1",
                "type": 2,
                "text_type": 1,
                "side": "top",
                "side_name": "Top",
                "layer": -99,
                "layer_name": "",
                "from": [1, 1],
                "to": [1, 2],
            }
        ],
    }
    source.write_text(json.dumps(request))
    adapter.on("tidy_labels", {"state_guard": labels.GUARD, "state_digest": "a" * 64})
    argv = ["pcb", "silkscreen", "labels", "--project", str(board), "--file", str(source)]
    code, planned = cli(*argv, "--dry-run")
    assert code == 0
    request["moves"][0]["to"][1] = 3
    source.write_text(json.dumps(request))
    code, failed = cli(*argv, "--confirm", planned["data"]["confirm_token"])
    assert code == 6 and not any(p["apply"] for p in adapter.params("tidy_labels"))


@pytest.mark.parametrize("label_objects,expected_budget", [(True, 180.0), (False, 60.0)])
def test_sdk_complete_label_observation_has_bounded_material_read_budget(
    tmp_path, monkeypatch, label_objects, expected_budget
):
    from xpedition_cli.driver import Adapter

    calls = []

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            calls.append((method, params, kwargs))
            return {}

    instance = Adapter(NativeFixture())
    monkeypatch.setattr(instance, "_identity", lambda domain: {"application": {"pid": 42}})
    target = {
        "mode": "session",
        "instance_id": "42",
        "extensions": {
            "domain": "pcb",
            "project": str(tmp_path / "RC.prj"),
            "command": "pcb silkscreen labels",
            "label_objects": label_objects,
        },
    }
    instance._observe(target)
    captured = [row for row in calls if row[0] == "cell_refresh_state"]
    assert len(captured) == 1
    assert captured[0][2]["timeout_seconds"] == expected_budget
    assert captured[0][1].get("label_objects", False) is label_objects
