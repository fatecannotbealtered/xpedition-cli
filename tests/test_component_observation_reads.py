"""Real strict shape/text readers over native-shaped objects; no CAD acceptance.

Only plane/rule discovery and mask evaluation are stubbed. The complete native
model, strict capture, trace UID/material reader and formal text reader run for
real, so getter counts cannot be hidden by a fake preassembled snapshot.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from test_layout_routing_integration import native_document

from xpedition_cli import (
    board_labels,
    board_routepass,
    board_trace,
    board_unroute,
    cell_refresh,
    native_pad_mask,
)
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_observation_stage as S


def observed_case(tmp_path, monkeypatch):
    doc, _, _ = native_document(monkeypatch)
    doc.FullName = str(tmp_path / "independent-stage.pcb")
    doc.ObjectClass = 1
    text = doc.FabricationLayerTexts[0]
    text.Document = text.Parent = doc
    text.Layer, text.LayerName, text.Side = -99, "", 1
    text.FixLock = text.DisplayCondition = 0
    text.Reserved, text.ReservedBy = False, ""
    text.IsValid = lambda: True
    text.GetPositionX = lambda unit: text.PositionX * A.TH_TO_MM
    text.GetPositionY = lambda unit: text.PositionY * A.TH_TO_MM
    fmt = text.Format
    fmt.Font = "NativeVector"
    fmt.Bold = fmt.Italic = fmt.Underline = False
    fmt.HorizontalJust = fmt.VerticalJust = fmt.Attributes = 0
    fmt.AspectRatio = fmt.LineSpacing = 1.0
    fmt.GetHeight = lambda unit: fmt.Height * A.TH_TO_MM
    fmt.GetPenWidth = lambda unit: fmt.PenWidth * A.TH_TO_MM
    identity = {"pid": 42, "generation": "first", "board": doc.FullName}
    counts = {"capture": 0, "model": 0, "texts": 0}
    issued = []
    real_capture, real_model = cell_refresh.capture, A._board_model
    real_labels, real_trace = board_labels.observe, board_trace.observe

    def capture(*args, **kwargs):
        counts["capture"] += 1
        return real_capture(*args, **kwargs)

    def model(*args, **kwargs):
        counts["model"] += 1
        return real_model(*args, **kwargs)

    def labels(*args, **kwargs):
        counts["texts"] += 1
        return real_labels(*args, **kwargs)

    def traces(*args, **kwargs):
        if kwargs.get("_capture_stage") is not None:
            issued.append(kwargs["_capture_stage"])
        return real_trace(*args, **kwargs)

    monkeypatch.setattr(cell_refresh, "capture", capture)
    monkeypatch.setattr(board_unroute, "capture", capture)
    monkeypatch.setattr(board_labels, "capture", capture)
    monkeypatch.setattr(A, "_board_model", model)
    monkeypatch.setattr(board_labels, "observe", labels)
    monkeypatch.setattr(board_trace, "observe", traces)
    monkeypatch.setattr(
        A,
        "_board_stackup",
        lambda *_: [{"kind": "conductor", "layer": number, "usage": "signal"} for number in (1, 2)],
    )
    monkeypatch.setattr(
        board_trace,
        "plane_inventory",
        lambda *_: {"complete": True, "shapes": [], "generated": [], "assignments": []},
    )
    monkeypatch.setattr(A, "_board_net_classes", lambda *_, **__: [])
    monkeypatch.setattr(
        native_pad_mask,
        "evaluate_components",
        lambda *args: {
            "request": args[2],
            "raw_trace_inventory": [],
            "complete": False,
            "reason": "Cost fixture deliberately does not establish native mask acceptance",
        },
    )
    return NS(
        doc=doc,
        identity=identity,
        counts=counts,
        issued=issued,
        request={"net": "A", "layer": 1},
        text=text,
    )


def test_owned_lifecycle_observation_reads_complete_geometry_once_and_texts_twice(
    tmp_path, monkeypatch
):
    case = observed_case(tmp_path, monkeypatch)
    state = board_routepass.observe_components(
        case.doc, A, case.request, _identity_reader=lambda: case.identity
    )
    assert case.counts == {"capture": 1, "model": 1, "texts": 2}
    assert state["geometry"]["model"]["traces"][0]["uid"] == "318"
    assert state["texts"][0]["text"] == "R1"
    with pytest.raises(ValueError, match="expired"):
        S.material(case.issued[0], case.doc, A, kind="complete_capture")


def test_no_identity_callback_retains_original_independent_capture_path(tmp_path, monkeypatch):
    case = observed_case(tmp_path, monkeypatch)
    board_routepass.observe_components(case.doc, A, case.request)
    assert case.counts == {"capture": 2, "model": 3, "texts": 1}
    assert case.issued == []


def test_six_lifecycle_observations_issue_six_fresh_native_reads_not_one_cache(
    tmp_path, monkeypatch
):
    case = observed_case(tmp_path, monkeypatch)
    for index in range(6):
        case.text.TextString = "fresh-text-" + str(index)
        state = board_routepass.observe_components(
            case.doc, A, case.request, _identity_reader=lambda: case.identity
        )
        assert state["texts"][0]["text"] == "fresh-text-" + str(index)
    assert case.counts == {"capture": 6, "model": 6, "texts": 12}
    assert len({id(stage) for stage in case.issued}) == 6
    for stage in case.issued:
        with pytest.raises(ValueError, match="expired"):
            S.material(stage, case.doc, A, kind="complete_capture")


@pytest.mark.parametrize("fault", ["pid", "document", "expired"])
def test_local_frame_is_rechecked_after_material_reads_before_return(tmp_path, monkeypatch, fault):
    case = observed_case(tmp_path, monkeypatch)
    now = [100.0]
    monkeypatch.setattr(S.time, "monotonic", lambda: now[0])

    def masks(*args):
        if fault == "pid":
            case.identity["pid"] += 1
        elif fault == "document":
            case.doc.FullName += ".foreign"
        else:
            now[0] += 601
        return {"raw_trace_inventory": []}

    monkeypatch.setattr(native_pad_mask, "evaluate_components", masks)
    with pytest.raises(ValueError, match="expired"):
        board_routepass.observe_components(
            case.doc, A, case.request, _identity_reader=lambda: case.identity
        )
    assert case.counts["capture"] == 1
    assert all(stage._active is False for stage in case.issued)


@pytest.mark.parametrize("field", ["text", "owner_field", "format", "raw_strokes"])
def test_complete_current_text_fields_are_compared_to_independent_stage_sidecar(
    tmp_path, monkeypatch, field
):
    case = observed_case(tmp_path, monkeypatch)
    observe = board_labels.observe
    calls = []

    def labels(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            if field == "text":
                case.text.TextString = "changed-foreign-text"
            elif field == "owner_field":
                case.text.FixLock += 1
            elif field == "format":
                case.text.Format.Bold = True
            else:
                case.text.StrokeText = lambda unit: [0.1, 20.1, 14.9, 0, 21.8, 15.7, 2]
        return observe(*args, **kwargs)

    monkeypatch.setattr(board_labels, "observe", labels)
    with pytest.raises(ValueError, match="Complete native text fields changed"):
        board_routepass.observe_components(
            case.doc, A, case.request, _identity_reader=lambda: case.identity
        )
    assert case.counts["capture"] == 1


@pytest.mark.parametrize("injection", [{"pid": 42}, "cached-native-state", []])
def test_caller_json_cannot_inject_an_identity_reader(tmp_path, monkeypatch, injection):
    case = observed_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="trusted native identity callback"):
        board_routepass.observe_components(case.doc, A, case.request, _identity_reader=injection)
    assert case.counts["capture"] == case.counts["model"] == 0


def large_case(tmp_path, monkeypatch):
    from test_native_observation_stage import foreign_case

    case = foreign_case(tmp_path, monkeypatch)
    case.state["geometry"]["model"]["connectivity"]["nets"][0].update(opens=1, routed=False)
    case.state["geometry"]["model"]["connectivity"]["nets"].append(
        {"net": "REAL_FOREIGN", "uid": "real-foreign-76", "opens": 0, "routed": True}
    )
    for index in range(2, 417):
        case.state["all_pin_pad_material"][index].update(
            net="REAL_FOREIGN", net_uid="real-foreign-76"
        )
        case.state["pin_identities"][index].update(net="REAL_FOREIGN", net_uid="real-foreign-76")
        case.state["geometry"]["components"][index]["geometry"][0].update(
            net="REAL_FOREIGN", net_uid="real-foreign-76"
        )
        case.doc.Components[index].Pins[0].Net.Name = "REAL_FOREIGN"
        case.doc.Components[index].Pins[0].Net.UniqueId = "real-foreign-76"
    return case


def test_local_stage_keeps_all_467_pins_and_50_nc_in_one_capture(tmp_path, monkeypatch):
    case = large_case(tmp_path, monkeypatch)
    state = board_routepass.observe_components(
        case.doc, A, case.selector, _identity_reader=lambda: {"pid": 42, "board": str(case.board)}
    )
    assert case.events.count("capture") == 1
    assert len(state["all_pin_pad_material"]) == 467
    assert sum(row["net"] == "(Net0)" for row in state["all_pin_pad_material"]) == 50
    assert native_pad_mask.verify_component_evidence(state, state["native_mask_proof"]["request"])[
        "valid"
    ]
    assert not case.engine.masks


def test_reopened_component_order_preserves_all_467_stable_pin_identities(tmp_path, monkeypatch):
    case = large_case(tmp_path, monkeypatch)

    def reader():
        return {"pid": 42, "board": str(case.board)}

    before = board_routepass.observe_components(case.doc, A, case.selector, _identity_reader=reader)
    case.doc.Components.reverse()
    for component in case.doc.Components:
        component.Pins.reverse()
    reopened = board_routepass.observe_components(
        case.doc, A, case.selector, _identity_reader=reader
    )
    assert board_routepass.design_state(before) == board_routepass.design_state(reopened)
    assert len(reopened["pin_identities"]) == 467
    assert len(reopened["all_pin_pad_material"]) == 467
    assert case.events.count("capture") == 2


def test_pre_save_raw_observation_uses_a_new_capture_even_when_old_mask_proof_is_equal(
    tmp_path, monkeypatch
):
    case = large_case(tmp_path, monkeypatch)

    def reader():
        return {"pid": 42, "board": str(case.board)}

    before = board_routepass.observe_components(case.doc, A, case.selector, _identity_reader=reader)
    current = board_routepass.observe_components_for_save(
        case.doc, A, case.selector, before, _identity_reader=reader
    )
    assert case.events.count("capture") == 2
    assert current == before and current is not before
    assert current["geometry"] is not before["geometry"]
    assert len(current["all_pin_pad_material"]) == 467


@pytest.mark.parametrize("field", ["pin_uid", "net_uid", "center"])
def test_local_frame_rejects_current_foreign_pin_identity_or_center_drift_before_masks(
    tmp_path, monkeypatch, field
):
    case = large_case(tmp_path, monkeypatch)
    observe = board_trace.observe

    def changed(*args, **kwargs):
        pin = case.doc.Components[-1].Pins[0]
        if field == "pin_uid":
            pin.UniqueId = "changed-foreign-pin-77"
        elif field == "net_uid":
            pin.Net.UniqueId = "changed-unassigned-uid-77"
        else:
            pin.GetPositionX = lambda unit: -123.0
        return observe(*args, **kwargs)

    monkeypatch.setattr(board_trace, "observe", changed)
    with pytest.raises(ValueError, match="Whole-board pin material differs"):
        board_routepass.observe_components(
            case.doc,
            A,
            case.selector,
            _identity_reader=lambda: {"pid": 42, "board": str(case.board)},
        )
    assert case.events.count("capture") == 1
    assert case.engine.calls == []
