"""Offline native-adapter integration of the independent component read profile."""

import copy
from types import SimpleNamespace as NS

import pytest
from test_native_pad_mask import Collection, add_graph_trace, cells
from test_native_pad_mask_components import component_fixture

from xpedition_cli import (
    board_components,
    board_labels,
    board_routepass,
    board_trace,
    cell_refresh,
    copper_ties,
    native_identity,
    native_pad_mask,
)
from xpedition_cli import native_com_adapter as A


def native_component_case(tmp_path, monkeypatch, *, pins=4, traces=1, fault=None):
    doc, state, request, adapter, engine = component_fixture(
        pins, net="RENAMED_SIGNAL_93", trace_count=traces
    )
    board = tmp_path / "isolated-observation.pcb"
    board.write_bytes(b"synthetic-only board marker")
    doc.FullName, doc.CurrentUnit = str(board), 2
    if fault == "foreign_trace":
        add_graph_trace(
            doc,
            state,
            engine,
            "foreign-old-copper",
            [[50, 55], [20, 20], [0, 0]],
            cells(50, 20, 56, 21),
            [49.873, 19.873, 55.127, 20.127],
            net="B",
        )
        state["geometry"]["model"]["connectivity"]["nets"].append({"net": "B", "uid": "n2"})
    for row in state["geometry"]["model"]["connectivity"]["nets"]:
        row.update(opens=max(0, pins - 1) if row["net"] == request["net"] else 0, routed=pins < 2)
    model = state["geometry"]["model"]
    model["connectivity"]["complete"] = True
    model.update(
        layers=2,
        traces=[copy.deepcopy(row["geometry"]) for row in state["routing_objects"]],
        vias=[],
        planes=[],
        holes=[],
        unrouted=[
            row for row in model["connectivity"]["nets"] if row["opens"] or not row["routed"]
        ],
    )
    state["geometry"]["stackup"] = [
        {"kind": "conductor", "layer": layer, "usage": "signal"} for layer in (1, 2)
    ]
    state["geometry"]["components"] = [
        {
            "uid": row["component_uid"],
            "refdes": row["refdes"],
            "geometry": [
                {
                    "number": row["pin"],
                    "net": row["net"],
                    "uid": row["pin_uid"],
                    "net_uid": row["net_uid"],
                    "center_mm": copy.deepcopy(row["center_mm"]),
                }
            ],
        }
        for row in state["all_pin_pad_material"]
    ]
    state["pin_identities"] = [
        {key: row[key] for key in ("component_uid", "refdes", "pin", "pin_uid", "net", "net_uid")}
        for row in state["all_pin_pad_material"]
    ]
    state["plane_inventory"] = {"complete": True, "shapes": [], "generated": [], "assignments": []}
    state["rules"] = {"scheme": "(Master)", "local_applicability_verified": False}
    state["texts"] = []
    doc.Components = Collection()
    for row in state["all_pin_pad_material"]:
        pin = NS(
            UniqueId=row["pin_uid"],
            Name=row["pin"],
            Net=NS(Name=row["net"], UniqueId=row["net_uid"]),
            GetPositionX=lambda unit, x=row["center_mm"][0]: x,
            GetPositionY=lambda unit, y=row["center_mm"][1]: y,
            material=copy.deepcopy(row["material"]),
        )
        doc.Components.append(
            NS(UniqueId=row["component_uid"], RefDes=row["refdes"], Pins=Collection([pin]))
        )
    if fault == "raw_material":
        doc.Components[0].Pins[0].material["complete"] = False
    if fault == "pin_identity":
        state["pin_identities"][0]["pin_uid"] = "different-actual-pin"
    if fault == "model_inventory":
        model["traces"] = []
    if fault == "plane_layer":
        state["geometry"]["stackup"][0]["usage"] = "plane"
    events, identity_reads = [], []
    app = NS(ActiveDocument=doc)

    def identity(_app, actual_board):
        assert actual_board == board
        identity_reads.append(1)
        return {
            "board": str(board),
            "generation": {
                "pid": 42,
                "birth": 2 if fault == "identity" and len(identity_reads) > 1 else 1,
            },
        }

    def capture(*_args, **_kwargs):
        events.append("capture")
        return copy.deepcopy(state["geometry"])

    def base_observation(actual_doc, _a, item, *, location_scheme, _capture_stage=None):
        assert actual_doc is doc and location_scheme is False
        assert item["net"] == request["net"] and item["layer"] == request["layer"]
        events.append("complete_material_read")
        return copy.deepcopy(state)

    def application(_client, **kwargs):
        assert kwargs == {"attach_only": True, "quiet": False}
        events.append("attach")
        return app

    monkeypatch.setattr(A, "_application", application)
    monkeypatch.setattr(A, "_licensed_document", lambda _app: doc)
    monkeypatch.setattr(A, "_propget", adapter._propget)
    monkeypatch.setattr(A, "_items", adapter._items)
    monkeypatch.setattr(A, "_open_layout_document", lambda *_: pytest.fail("read cannot Open"))
    monkeypatch.setattr(native_identity, "application_identity", lambda *_: {"pid": 42})
    monkeypatch.setattr(board_labels, "_identity", identity)
    monkeypatch.setattr(cell_refresh, "capture", capture)
    monkeypatch.setattr(copper_ties, "observe", lambda *_: {"complete": True, "items": []})
    monkeypatch.setattr(board_trace, "observe", base_observation)
    monkeypatch.setattr(
        board_labels,
        "observe",
        lambda *_, **_kwargs: {"geometry": copy.deepcopy(state["geometry"]), "texts": []},
    )
    monkeypatch.setattr(
        native_pad_mask, "observe_pin", lambda pin, _layer, _a: copy.deepcopy(pin.material)
    )
    monkeypatch.setattr(board_routepass, "execute", lambda *_: pytest.fail("read cannot Go"))
    return NS(
        board=board,
        doc=doc,
        state=state,
        request=request,
        selector={"net": request["net"], "layer": request["layer"]},
        events=events,
        engine=engine,
    )


def query(case, **extra):
    return A._board_observation(
        {"project": str(case.board), "pid": 42, "component_request": case.selector, **extra},
        object(),
    )


@pytest.mark.parametrize("pins,traces", [(0, 3), (1, 0), (4, 0), (4, 1), (5, 4), (7, 1)])
def test_real_generic_observer_adapter_chain_preserves_actual_uid_and_complete_raw(
    tmp_path, monkeypatch, pins, traces
):
    case = native_component_case(tmp_path, monkeypatch, pins=pins, traces=traces)
    result = query(case)
    observed = result["observation"]
    state = observed["native_routepass_state"]
    snapshot = observed["connectivity_components"]
    assert observed["native_routepass_request"] == case.selector
    assert state["native_mask_proof"]["request"] == case.request
    assert state["native_mask_proof"]["target_net_uid"] == case.request["net_uid"]
    assert observed["native_routepass_evidence"]["valid"]
    assert snapshot["valid"] and snapshot["observation_complete"], snapshot
    assert snapshot["target"] == case.request
    assert len(snapshot["raw_traces"]) == len(state["native_trace_material"]) == traces
    assert len(snapshot["nodes"]) == pins + traces
    if pins > 1 and traces == 0:
        assert observed["native_routepass_components"]["complete"]
        assert not snapshot["all_members_connected"]
        assert len(snapshot["components"]) == pins
    assert len(case.engine.masks) == 0
    assert case.events == ["attach", "capture", "complete_material_read", "capture"]
    assert observed["engineering_write_dispatched"] is False
    assert observed["persistence"] == "unknown"


def test_complete_raw_inventory_keeps_foreign_copper_in_snapshot(tmp_path, monkeypatch):
    case = native_component_case(tmp_path, monkeypatch, fault="foreign_trace")
    observation = query(case)["observation"]
    assert len(observation["native_routepass_state"]["native_trace_material"]) == 2
    raw = observation["connectivity_components"]["raw_traces"]
    assert {row["uid"] for row in raw} == {"actual-trace-0", "foreign-old-copper"}


@pytest.mark.parametrize(
    "selector",
    [
        None,
        {},
        {"net": "x"},
        {"net": "", "layer": 1},
        {"net": "x", "layer": True},
        {"net": "x", "layer": 0},
        {"net": "x", "layer": 129},
        {"net": "x", "layer": 1, "net_uid": "user-invented"},
    ],
)
def test_invalid_adapter_component_selector_stops_before_attach(tmp_path, monkeypatch, selector):
    case = native_component_case(tmp_path, monkeypatch)
    case.selector = selector
    with pytest.raises(A.AdapterError) as raised:
        query(case)
    assert raised.value.code == "E_VALIDATION"
    assert case.events == []


@pytest.mark.parametrize("legacy", ["routepass_request", "native_general_state"])
def test_generic_selector_cannot_mix_legacy_profile(tmp_path, monkeypatch, legacy):
    case = native_component_case(tmp_path, monkeypatch)
    with pytest.raises(A.AdapterError) as raised:
        query(case, **{legacy: True})
    assert raised.value.code == "E_VALIDATION" and case.events == []


@pytest.mark.parametrize(
    "fault", ["identity", "raw_material", "pin_identity", "model_inventory", "plane_layer"]
)
def test_actual_identity_material_or_snapshot_failure_never_returns_success(
    tmp_path, monkeypatch, fault
):
    case = native_component_case(tmp_path, monkeypatch, fault=fault)
    with pytest.raises(A.AdapterError) as raised:
        query(case)
    assert raised.value.code in {"E_CONFLICT", "E_NATIVE_FAILURE", "E_UNSUPPORTED"}
    assert raised.value.details.get("write_attempted") is False
    assert len(case.engine.masks) == 0


def test_pre_save_component_observer_reuses_only_exact_raw_inputs(tmp_path, monkeypatch):
    case = native_component_case(tmp_path, monkeypatch)
    state = board_routepass.observe_components(case.doc, A, case.selector)
    before_calls = len(case.engine.calls)
    same = board_routepass.observe_components_for_save(case.doc, A, case.selector, state)
    assert same == state
    assert len(case.engine.calls) == before_calls
    case.doc.Traces[0].Geometry.values[5] += 0.01
    with pytest.raises(ValueError):
        board_routepass.observe_components_for_save(case.doc, A, case.selector, state)


@pytest.mark.parametrize(
    "fault", ["duplicate", "missing", "wrong_count", "boolean_layer", "boolean_count", "plane"]
)
def test_component_snapshot_and_native_observer_reject_incomplete_or_wrong_layers(
    tmp_path, monkeypatch, fault
):
    case = native_component_case(tmp_path, monkeypatch)
    observed = query(case)["observation"]
    native = observed["native_routepass_state"]

    def corrupt(state):
        rows = state["geometry"]["stackup"]
        if fault == "duplicate":
            rows[1]["layer"] = 1
        elif fault == "missing":
            rows.pop()
        elif fault == "wrong_count":
            state["geometry"]["model"]["layers"] = 3
        elif fault == "boolean_layer":
            rows[0]["layer"] = True
        elif fault == "boolean_count":
            state["geometry"]["model"]["layers"] = True
        else:
            rows[0]["usage"] = "plane"

    corrupt(native)
    snapshot = board_components.build_snapshot(
        native,
        observed["native_routepass_components"],
        board_id=str(case.board),
        net_id=case.request["net_uid"],
        layer=case.request["layer"],
    )
    assert snapshot["valid"] is False
    corrupt(case.state)
    mask_calls = len(case.engine.calls)
    with pytest.raises(ValueError):
        board_routepass.observe_components(case.doc, A, case.selector)
    assert len(case.engine.calls) == mask_calls


def test_actual_layer_numbers_are_resolved_independently_of_stackup_list_order(
    tmp_path, monkeypatch
):
    case = native_component_case(tmp_path, monkeypatch)
    case.state["geometry"]["stackup"].reverse()
    case.state["geometry"]["stackup"][0]["usage"] = "plane"
    snapshot = query(case)["observation"]["connectivity_components"]
    assert snapshot["valid"] and snapshot["target"]["layer"] == 1
