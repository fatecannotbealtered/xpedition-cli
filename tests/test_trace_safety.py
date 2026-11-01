"""Owned synthetic routing objects; no CAD, vendor database or native acceptance.

Production previews have no eligible write. The future-only guarded lifecycle
tests explicitly inject a synthetic trusted rule observation; no public input
can create that observation or bypass the production refusal.
"""

import copy
from types import SimpleNamespace as NS

import pytest
from test_trace_target_contract import owned_pin_material
from test_unroute_native import Collection
from test_unroute_public import fixture_state, remove

from xpedition_cli import board_trace as T
from xpedition_cli import native_com_adapter as A

ITEM = {"kind": "trace", "net": "A", "layer": 1, "width": 0.254, "points": [[0, 0], [5, 0]]}


def trace_state(*, copper=False, trusted_rules=False):
    state = fixture_state()
    remove(state, {"A"})
    for comp in state["geometry"]["components"]:
        comp["geometry"] = [
            {
                "number": p["number"],
                "net": p["net"],
                "center_mm": [p["x"], p["y"]],
                "holes": [],
                "copper": [
                    {
                        "layer": 1,
                        "geometry": [
                            {"bounds_mm": [p["x"] - 0.3, p["y"] - 0.3, p["x"] + 0.3, p["y"] + 0.3]}
                        ],
                    }
                ],
            }
            for p in comp["pins"]
        ]
    state["pin_identities"] = [
        {
            "component_uid": c["uid"],
            "refdes": c["refdes"],
            "pin": p["number"],
            "pin_uid": c["uid"] + ":" + p["number"],
            "net": p["net"],
            "net_uid": "11" if p["net"] == "A" else "12",
        }
        for c in state["geometry"]["components"]
        for p in c["geometry"]
    ]
    state["plane_inventory"] = {
        "complete": True,
        "shapes": [],
        "generated": [],
        "assignments": [],
        "generated_identity_basis": "native net UID and layer; no hidden persistent UID",
    }
    if copper:
        state["plane_inventory"]["shapes"] = [
            {"uid": "756", "net": "B", "net_uid": "12", "layer": 1}
        ]
        state["plane_inventory"]["generated"] = [{"net": "B", "net_uid": "12", "layer": 1}]
        state["plane_inventory"]["assignments"] = [
            {"net": "B", "net_uid": "12", "layer": 1, "state": 1}
        ]
    state["rules"] = {
        "local_applicability_verified": trusted_rules,
        "scheme": "synthetic trusted fixture" if trusted_rules else "(Master)",
        "net_classes": [],
    }
    state["endpoint_pad_material"] = [
        {**identity, "material": owned_pin_material(pin["center_mm"])}
        for comp in state["geometry"]["components"]
        for pin in comp["geometry"]
        for identity in state["pin_identities"]
        if identity["component_uid"] == comp["uid"] and identity["pin"] == pin["number"]
    ]
    return state


def result_for(board, plan):
    return {
        "pcb": str(board),
        "trace": plan,
        "applied": False,
        "saved": False,
        "persistence_verified": False,
        "change_status": "not_applied",
        "persistence_state": "unchanged",
        "stages": dict.fromkeys(
            (
                "route",
                "regeneration",
                "readback",
                "protection",
                "connection",
                "save",
                "close",
                "reopen",
            ),
            "not_started",
        ),
        "native_calls_returned": [],
    }


def native_case(tmp_path, monkeypatch, fault=None, *, copper=False, trusted_rules=False):
    state = trace_state(copper=copper, trusted_rules=trusted_rules)
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic owned board")
    events, stored = [], None
    app = NS(ActiveDocument=None)

    class Doc:
        FullName, CurrentUnit = str(board), 2

        def __init__(self, state):
            self.state = state

        def PutTrace(self, layer, net, width, count, rows, p1, p2, unit):
            assert (layer, net, width, count, unit) == (1, "A", 0.254, 2, 4)
            events.append("put")
            geom = {
                "kind": "trace",
                "net": "A",
                "layer": 1,
                "width": width,
                "path": [[x, y, z] for x, y, z in zip(*rows, strict=True)],
            }
            self.state["routing_objects"].append(
                {"kind": "trace", "uid": "111", "net": "A", "geometry": geom}
            )
            model = self.state["geometry"]["model"]
            model["traces"].append(copy.deepcopy(geom))
            model["connectivity"]["nets"][0].update(opens=0, routed=True)
            model["unrouted"] = []
            if fault == "protected":
                model["holes"][0][2] = 7
            if fault == "foreign_net":
                model["connectivity"]["nets"][1]["opens"] = 1
            if fault == "unauthorized_generation":
                self.state["plane_inventory"]["generated"].append({"net": "B", "layer": 1})
            if fault == "target_negative_opens":
                model["connectivity"]["nets"][0]["opens"] = -1
            if fault == "target_degraded":
                model["connectivity"]["nets"][0].update(opens=2, routed=False)
            if fault == "target_unrouted":
                model["connectivity"]["nets"][0].update(opens=1, routed=False)
            if fault == "target_material_missing":
                self.state["endpoint_pad_material"][0]["material"]["pads"][0]["geometry"][0].pop(
                    "material"
                )
            if fault == "target_wrong_pin":
                self.state["pin_identities"][0]["pin_uid"] = "wrong-pin"
            if fault == "partial_missing_reply":
                raise RuntimeError("synthetic interruption/reply loss after actual mutation")
            return NS(UniqueId=0 if fault == "missing_uid" else "111")

        def Save(self):
            nonlocal stored
            events.append("save")
            if fault == "save_fail":
                raise RuntimeError("synthetic Save failed")
            stored = copy.deepcopy(self.state)
            if fault == "save_reply_loss":
                raise RuntimeError("synthetic Save reply lost")
            return fault != "save_false"

        def Close(self, save):
            assert save is True
            events.append("close")
            if fault == "close_fail":
                return False
            app.ActiveDocument = None
            return True

    app.ActiveDocument = Doc(state)

    def observe(doc, a, item):
        if events and fault == "missing_readback":
            raise RuntimeError("synthetic readback unavailable")
        result = copy.deepcopy(doc.state)
        if events and fault == "missing_connectivity":
            result["geometry"]["model"]["connectivity"]["complete"] = False
        return result

    def open_doc(application, path):
        assert application is app and path == board and app.ActiveDocument is None
        events.append("open")
        if fault == "open_fail":
            raise RuntimeError("synthetic reopen failure")
        app.ActiveDocument = Doc(copy.deepcopy(stored))
        if fault == "reopen_drift":
            app.ActiveDocument.state["geometry"]["model"]["holes"][0][2] = 8
        return app.ActiveDocument, []

    def application(client, attach_only):
        assert attach_only is True
        return app

    monkeypatch.setattr(T, "observe", observe)
    monkeypatch.setattr(A, "_application", application)
    monkeypatch.setattr(A, "_licensed_document", lambda app: app.ActiveDocument)
    monkeypatch.setattr(A, "_find_net", lambda doc, name: name)
    monkeypatch.setattr(A, "_open_layout_document", open_doc)
    return app, board, {"project": str(board), "items": [copy.deepcopy(ITEM)]}, events


@pytest.mark.parametrize("copper", [False, True])
def test_production_shape_preview_and_confirm_refuse_before_all_mutation(
    tmp_path, monkeypatch, copper
):
    app, board, params, events = native_case(tmp_path, monkeypatch, copper=copper)
    preview = A._hand_route(params, None)
    plan = preview["trace"]
    assert plan["can_apply"] is False
    assert plan["unit"] == "mm" and plan["native_current_unit"] == 2
    assert plan["endpoints"][0]["component_uid"] == "67"
    assert plan["endpoints"][0]["net_uid"] == "11"
    assert plan["endpoints"][0]["physical_contact_verified"] is False
    assert plan["input_files"][0]["path"] == str(board)
    codes = {r["code"] for r in plan["blockers"]}
    assert "local_rule_applicability_unproven" in codes
    assert ("generated_copper_impact_unproven" in codes) is copper
    assert plan["authorized_regeneration"] == plan["allowed_copper_changes"] == []
    for _ in range(2):
        with pytest.raises(A.AdapterError) as error:
            A._hand_route(
                {**params, "apply": True, "expected": plan["fingerprint"], "dangerous": True}, None
            )
        assert error.value.code == "E_UNSUPPORTED"
        assert error.value.details["write_attempted"] is False
    assert events == []
    assert app.ActiveDocument is not None


@pytest.mark.parametrize("drift", ["file", "net_uid", "pad", "rules"])
def test_frozen_trace_confirmation_rejects_inputs_and_identity_drift(tmp_path, monkeypatch, drift):
    app, board, params, events = native_case(tmp_path, monkeypatch)
    preview = A._hand_route(params, None)
    if drift == "file":
        board.write_bytes(b"drift")
    elif drift == "net_uid":
        app.ActiveDocument.state["geometry"]["model"]["connectivity"]["nets"][0]["uid"] = "999"
    elif drift == "pad":
        app.ActiveDocument.state["geometry"]["components"][0]["geometry"][0]["copper"][0][
            "geometry"
        ][0]["bounds_mm"][0] -= 1
    else:
        app.ActiveDocument.state["rules"]["changed"] = True
    with pytest.raises(A.AdapterError) as error:
        A._hand_route({**params, "apply": True, "expected": preview["trace"]["fingerprint"]}, None)
    assert error.value.code in {"E_CONFLICT", "E_UNSUPPORTED"}
    assert error.value.details["write_attempted"] is False
    assert events == []


def test_wrong_target_is_rejected_without_opening_it(tmp_path, monkeypatch):
    app, board, params, events = native_case(tmp_path, monkeypatch)
    app.ActiveDocument.FullName = str(tmp_path / "foreign.pcb")
    with pytest.raises(A.AdapterError) as error:
        A._hand_route(params, None)
    assert error.value.code == "E_CONFLICT" and error.value.details["write_attempted"] is False
    assert events == []


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "protected",
        "foreign_net",
        "unauthorized_generation",
        "missing_readback",
        "missing_connectivity",
        "partial_missing_reply",
        "missing_uid",
        "target_negative_opens",
        "target_degraded",
        "target_unrouted",
        "target_material_missing",
        "target_wrong_pin",
        "save_fail",
        "save_reply_loss",
        "save_false",
        "close_fail",
        "open_fail",
        "reopen_drift",
    ],
)
def test_future_only_synthetic_guard_stops_lifecycle_at_each_failure(tmp_path, monkeypatch, fault):
    app, board, params, events = native_case(tmp_path, monkeypatch, fault, trusted_rules=True)
    plan = A._hand_route(params, None)["trace"]
    assert plan["can_apply"] is True  # only this explicitly synthetic observer permits it
    try:
        result = T._execute_guarded(
            plan, result_for(board, plan), app, board, app.ActiveDocument, A
        )
    except A.AdapterError as error:
        result = error.details["native_result"]
        assert fault is not None
        assert result["persistence_state"] == "unknown"
    else:
        assert fault is None and result["persistence_verified"] is True
        assert result["protection"]["valid"] is True
        assert result["protection"]["physical_contact_verified"] is True
    if fault in {
        "protected",
        "foreign_net",
        "unauthorized_generation",
        "missing_readback",
        "missing_connectivity",
        "partial_missing_reply",
        "missing_uid",
        "target_negative_opens",
        "target_degraded",
        "target_unrouted",
        "target_material_missing",
        "target_wrong_pin",
    }:
        assert events == ["put"]
        assert (
            result["stages"]["save"]
            == result["stages"]["close"]
            == result["stages"]["reopen"]
            == "not_started"
        )
    elif fault in {"save_fail", "save_reply_loss", "save_false"}:
        assert events == ["put", "save"] and result["saved"] is False
    elif fault == "close_fail":
        assert events == ["put", "save", "close"] and result["saved"] is True
    else:
        assert events == ["put", "save", "close", "open"]
    if fault == "missing_uid":
        assert result["native_calls_returned"] == [
            {"call": "PutTrace", "returned": True, "uid": None}
        ]


def test_unauthorized_regeneration_guard_never_calls_any_stage(tmp_path, monkeypatch):
    app, board, params, events = native_case(tmp_path, monkeypatch, trusted_rules=True)
    plan = A._hand_route(params, None)["trace"]
    plan["authorized_regeneration"] = [{"net": "B", "layer": 1}]
    with pytest.raises(A.AdapterError) as error:
        T._execute_guarded(plan, result_for(board, plan), app, board, app.ActiveDocument, A)
    assert error.value.details["write_attempted"] is False
    assert events == []


@pytest.mark.parametrize(
    "name,params",
    [
        ("_regenerate_planes", {}),
        ("_route_board", {}),
        ("_hand_route", {"items": [{"kind": "via", "net": "A", "at": [0, 0]}]}),
    ],
)
def test_broader_native_paths_refuse_before_native_attachment(monkeypatch, name, params):
    monkeypatch.setattr(A, "_application", lambda *a, **k: pytest.fail("no native attachment"))
    with pytest.raises(A.AdapterError) as error:
        if name == "_regenerate_planes":
            getattr(A, name)(NS())
        else:
            getattr(A, name)(params, None)
    assert error.value.code == "E_UNSUPPORTED" and error.value.details["write_attempted"] is False


def test_native_reader_never_invents_rule_eligibility(monkeypatch):
    state = trace_state()
    monkeypatch.setattr(T.board_unroute, "observe", lambda *a: copy.deepcopy(state))
    monkeypatch.setattr(A, "_board_net_classes", lambda *a, **k: [{"name": "Default"}])
    monkeypatch.setattr(A, "_selected_clearances", lambda *a: {"active_scheme_verified": False})
    components = Collection(
        [
            NS(
                UniqueId=c["uid"],
                RefDes=c["refdes"],
                Pins=Collection(
                    NS(
                        Name=p["number"],
                        UniqueId=c["uid"] + ":" + p["number"],
                        Net=NS(Name=p["net"], UniqueId="11" if p["net"] == "A" else "12"),
                    )
                    for p in c["geometry"]
                ),
            )
            for c in state["geometry"]["components"]
        ]
    )
    doc = NS(
        LayerCount=2,
        PlaneShapes=Collection(),
        GeneratedPlanes=Collection(),
        PlaneAssignments=Collection(),
        Components=components,
    )
    read = T.observe(doc, A, ITEM)
    assert read["rules"]["local_applicability_verified"] is False
    assert read["rules"]["active_scheme_verified"] is False
    assert read["plane_inventory"]["complete"] is True


@pytest.mark.parametrize("fault", ["negative", "missing", "huge"])
def test_plane_inventory_rejects_unbounded_or_missing_counts(fault):
    collection = NS(Count={"negative": -1, "missing": None, "huge": 10001}[fault])
    with pytest.raises(ValueError):
        T.plane_inventory(NS(PlaneShapes=collection), A)


def test_endpoint_identity_is_exact_not_nearest_pin():
    state = trace_state()
    item = copy.deepcopy(ITEM)
    item["points"][0][0] = 0.001
    with pytest.raises(A.AdapterError, match="physical pin center"):
        T.bind(state, item, [], A)
