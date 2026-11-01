"""Unassigned native pins are retained obstacles, never a conducting network.

All identifiers and material observations here are synthetic; this is not CAD
acceptance and does not contain the phone project's private observation state.
"""

from __future__ import annotations

import copy

import pytest
from test_design_capabilities import representative_case
from test_native_pad_mask import add_graph_trace, array, cells, contour, material

from xpedition_cli import board_components as C
from xpedition_cli import board_routepass as R
from xpedition_cli import design_capabilities as D
from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import digest


def representative_with_50_nc():
    doc, state, target, adapter, engine = representative_case(467)
    named = state["geometry"]["model"]["connectivity"]["nets"]
    named.append({"net": "FOREIGN_SUPPLY", "uid": "foreign-named-74"})
    for index, pin in enumerate(state["all_pin_pad_material"]):
        if index >= 417:
            pin.update(net="(Net0)", net_uid="observed-unassigned-73")
        elif index >= 2:
            pin.update(net="FOREIGN_SUPPLY", net_uid="foreign-named-74")
    sync_identities(state)
    return doc, state, target, adapter, engine


def sync_identities(state):
    state["pin_identities"] = [
        {key: row[key] for key in ("component_uid", "pin_uid", "refdes", "pin", "net", "net_uid")}
        for row in state["all_pin_pad_material"]
    ]


def evaluated():
    doc, state, target, adapter, engine = representative_with_50_nc()
    proof = M.evaluate_components(doc, state, target, adapter)
    state["native_mask_proof"] = proof
    state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
    return doc, state, target, adapter, engine


def test_467_pins_including_50_unassigned_pins_remain_complete_foreign_material():
    _, state, target, _, engine = evaluated()
    proof = state["native_mask_proof"]
    assert proof["complete"], proof["errors"]
    assert len(proof["pin_materials"]) == proof["budget"]["pins"] == 467
    pins = state["all_pin_pad_material"]
    nc = {(row["component_uid"], row["pin_uid"]) for row in pins if row["net"] == "(Net0)"}
    assert len(nc) == 50
    compared = {
        (row["foreign_component_uid"], row["foreign_pin_uid"])
        for row in proof["foreign_intersections"]
        if row["foreign_kind"] == "pad"
    }
    assert nc <= compared and len(compared) == 465
    assert len(pins) == 467 and all(
        row["net"] != "(Net0)" for row in state["geometry"]["model"]["connectivity"]["nets"]
    )
    assert M.verify_component_evidence(state, target)["valid"]
    assert M.observe_components(state, target)["valid"]
    budget = D.prospective_budget(state, target)
    assert budget["valid"] and budget["counts"]["pins"] == 467
    assert budget["counts"]["foreign_pad_objects"] == 465
    assert not engine.masks


@pytest.mark.parametrize(
    "fault",
    [
        "named_missing_uid",
        "named_wrong_uid",
        "named_same_uid",
        "nc_named_uid_collision",
        "nc_same_name_different_uid",
        "nc_zero_uid",
        "duplicate_physical_pin_uid",
        "duplicate_physical_pin_name",
        "nc_inserted_as_named",
        "unknown_named_pin",
    ],
)
def test_ambiguous_or_real_named_identity_difference_refused_before_native_masks(fault):
    doc, state, target, adapter, engine = representative_with_50_nc()
    named = state["geometry"]["model"]["connectivity"]["nets"]
    pins = state["all_pin_pad_material"]
    if fault == "named_missing_uid":
        named[-1].pop("uid")
    elif fault == "named_wrong_uid":
        named[-1]["uid"] = "replacement-real-net"
    elif fault == "named_same_uid":
        named[-1]["uid"] = named[0]["uid"]
    elif fault == "nc_named_uid_collision":
        for row in pins[417:]:
            row["net_uid"] = named[0]["uid"]
    elif fault == "nc_same_name_different_uid":
        pins[-1]["net_uid"] = "different-unassigned-uid"
    elif fault == "nc_zero_uid":
        for row in pins[417:]:
            row["net_uid"] = "0"
    elif fault == "duplicate_physical_pin_uid":
        pins[-1]["pin_uid"] = pins[-2]["pin_uid"]
    elif fault == "duplicate_physical_pin_name":
        for key in ("component_uid", "refdes", "pin"):
            pins[-1][key] = pins[-2][key]
    elif fault == "nc_inserted_as_named":
        named.append({"net": "(Net0)", "uid": "observed-unassigned-73"})
    else:
        pins[3]["net"] = "MISSING_REAL_NET"
    sync_identities(state)
    proof = M.evaluate_components(doc, state, target, adapter)
    assert not proof["complete"] and proof["errors"]
    assert engine.calls == []
    state["native_trace_material"] = []
    assert not D.prospective_budget(state, target)["valid"]


def test_unassigned_pin_network_is_never_a_target_even_with_forged_named_census():
    doc, state, _, adapter, engine = representative_with_50_nc()
    target = {"net": "(Net0)", "net_uid": "observed-unassigned-73", "layer": 1}
    state["geometry"]["model"]["connectivity"]["nets"].append(
        {"net": "(Net0)", "uid": target["net_uid"]}
    )
    proof = M.evaluate_components(doc, state, target, adapter)
    assert not proof["complete"] and proof["errors"]
    assert engine.calls == []
    state["native_trace_material"] = []
    assert not D.prospective_budget(state, target)["valid"]


def test_trace_cannot_use_unassigned_uid_or_net_name():
    doc, state, target, adapter, engine = representative_with_50_nc()
    trace = doc.Traces[0]
    trace.Net.Name, trace.Net.UniqueId = "(Net0)", "observed-unassigned-73"
    state["routing_objects"][0]["net"] = "(Net0)"
    state["routing_objects"][0]["geometry"]["net"] = "(Net0)"
    proof = M.evaluate_components(doc, state, target, adapter)
    assert not proof["complete"] and proof["errors"]
    assert engine.calls == []


@pytest.mark.parametrize("boundary_only", [False, True])
def test_foreign_nc_positive_contact_or_boundary_touch_is_not_accepted(boundary_only):
    doc, state, target, adapter, engine = representative_with_50_nc()
    nc = state["all_pin_pad_material"][-1]
    raw = array(10.127 if boundary_only else 5, 0, 12 if boundary_only else 7, 2)
    engine.filled[digest(raw)] = cells(11 if boundary_only else 5, 0, 12 if boundary_only else 7, 2)
    nc["material"] = material(contour(raw))
    nc["center_mm"] = [11 if boundary_only else 6, 1]
    proof = M.evaluate_components(doc, state, target, adapter)
    state["native_mask_proof"] = proof
    state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
    assert proof["complete"], proof["errors"]
    assert len(proof["pin_materials"]) == 467
    assert not proof["foreign_separation_verified"]
    contact = next(
        row for row in proof["foreign_intersections"] if row.get("foreign_pin_uid") == nc["pin_uid"]
    )
    if boundary_only:
        assert contact["intersection_area_mm2"] == 0 and contact["boundary_touch"] is True
    else:
        assert contact["intersection_area_mm2"] > 0
    assert not M.verify_component_evidence(state, target)["valid"]
    assert not engine.masks


def test_independent_evidence_rejects_same_name_nc_uid_replacement_and_named_drift():
    _, state, target, _, _ = evaluated()
    assert M.verify_component_evidence(state, target)["valid"]
    altered = copy.deepcopy(state)
    altered["all_pin_pad_material"][-1]["net_uid"] = "foreign-uid-replaced"
    altered["native_mask_proof"]["material_sha256"] = digest(altered["all_pin_pad_material"])
    assert not M.verify_component_evidence(altered, target)["valid"]
    altered = copy.deepcopy(state)
    altered["geometry"]["model"]["connectivity"]["nets"][-1]["uid"] = "named-replaced"
    assert not M.verify_component_evidence(altered, target)["valid"]


def test_first_new_trace_then_second_connection_retains_old_uid_shape_and_all_50_nc():
    doc, state, target, adapter, engine = representative_with_50_nc()
    third = state["all_pin_pad_material"][2]
    third.update(net=target["net"], net_uid=target["net_uid"])
    sync_identities(state)
    doc.Traces.clear()
    state["routing_objects"] = []

    def observe():
        proof = M.evaluate_components(doc, state, target, adapter)
        assert proof["complete"], proof["errors"]
        state["native_mask_proof"] = proof
        state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
        assert M.verify_component_evidence(state, target)["valid"]
        assert len(proof["pin_materials"]) == 467
        assert sum(pin["net"] == "(Net0)" for pin in state["all_pin_pad_material"]) == 50
        return M.observe_components(state, target)

    assert len(observe()["components"]) == 3
    add_graph_trace(
        doc,
        state,
        engine,
        "first-new-copper-91",
        [[0, 10], [0, 0], [0, 0]],
        cells(0, 0, 11, 1),
        [-0.127, -0.127, 10.127, 0.127],
        net=target["net"],
    )
    doc.Traces[-1].Net.UniqueId = target["net_uid"]
    assert len(observe()["components"]) == 2
    old_raw = copy.deepcopy(state["native_trace_material"][0])
    old_geometry = copy.deepcopy(state["routing_objects"][0])
    add_graph_trace(
        doc,
        state,
        engine,
        "second-new-copper-92",
        [[10, 8, 8], [0, 0, 100], [0, 0, 0]],
        cells(8, 0, 11, 1) | cells(8, 0, 9, 101),
        [7.873, -0.127, 10.127, 100.127],
        net=target["net"],
    )
    doc.Traces[-1].Net.UniqueId = target["net_uid"]
    assert len(observe()["components"]) == 1
    assert state["native_trace_material"][0] == old_raw
    assert state["routing_objects"][0] == old_geometry
    assert not engine.masks


@pytest.mark.parametrize(
    "fault",
    ["missing_uid", "same_shape_changed_uid", "duplicate_uid", "non_target_path", "foreign_uid"],
)
def test_every_current_bound_trace_uid_and_full_shape_must_match_native_before_masks(fault):
    doc, state, target, adapter, engine = representative_with_50_nc()
    add_graph_trace(
        doc,
        state,
        engine,
        "foreign-old-95",
        [[20, 30], [30, 30], [0, 0]],
        cells(20, 30, 31, 31),
        [19.873, 29.873, 30.127, 30.127],
        net="FOREIGN_SUPPLY",
    )
    doc.Traces[-1].Net.UniqueId = "foreign-named-74"
    if fault == "missing_uid":
        state["routing_objects"][0]["geometry"].pop("uid")
    elif fault == "same_shape_changed_uid":
        doc.Traces[0].UniqueId = "same-shape-replacement-96"
    elif fault == "duplicate_uid":
        state["routing_objects"][-1]["geometry"]["uid"] = state["routing_objects"][0]["uid"]
    elif fault == "non_target_path":
        state["routing_objects"][-1]["geometry"]["path"][0][0] += 0.1
    else:
        doc.Traces[-1].UniqueId = "foreign-same-shape-replacement-96"
    proof = M.evaluate_components(doc, state, target, adapter)
    assert not proof["complete"] and proof["errors"]
    assert engine.calls == []


@pytest.mark.parametrize("fault", ["missing_uid", "changed_uid", "path", "extra_field"])
def test_independent_trace_verifier_rejects_missing_replaced_or_nonexact_material(fault):
    _, state, target, _, _ = evaluated()
    assert M.verify_component_evidence(state, target)["valid"]
    geometry = state["routing_objects"][0]["geometry"]
    if fault == "missing_uid":
        geometry.pop("uid")
    elif fault == "changed_uid":
        geometry["uid"] = "same-shape-replacement-96"
    elif fault == "path":
        geometry["path"][0][0] += 0.1
    else:
        geometry["unrecognized_native_material"] = "must remain protected"
    proof = state["native_mask_proof"]
    proof["trace_geometry_sha256"] = digest(state["routing_objects"])
    assert not M.verify_component_evidence(state, target)["valid"]


@pytest.mark.parametrize(
    "fault", [None, "missing_uid", "same_shape_uid", "duplicate_uid", "foreign_path", "extra_field"]
)
def test_snapshot_and_candidate_raw_trace_binding_keep_uid_and_complete_material(fault):
    doc, state, target, adapter, engine = representative_with_50_nc()
    add_graph_trace(
        doc,
        state,
        engine,
        "foreign-old-95",
        [[20, 30], [30, 30], [0, 0]],
        cells(20, 30, 31, 31),
        [19.873, 29.873, 30.127, 30.127],
        net="FOREIGN_SUPPLY",
    )
    doc.Traces[-1].Net.UniqueId = "foreign-named-74"
    proof = M.evaluate_components(doc, state, target, adapter)
    assert proof["complete"], proof["errors"]
    state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
    model = state["geometry"]["model"]
    model["connectivity"]["complete"] = True
    for net in model["connectivity"]["nets"]:
        net.update(opens=0, routed=True)
    model["vias"] = []
    geometry = state["routing_objects"][-1]["geometry"]
    if fault == "missing_uid":
        geometry.pop("uid")
    elif fault == "same_shape_uid":
        geometry["uid"] = "foreign-replacement-96"
    elif fault == "duplicate_uid":
        geometry["uid"] = state["routing_objects"][0]["uid"]
    elif fault == "foreign_path":
        geometry["path"][0][0] += 0.1
    elif fault == "extra_field":
        geometry["native_field_not_to_drop"] = True
    model["traces"] = [copy.deepcopy(row["geometry"]) for row in state["routing_objects"]]
    if fault:
        with pytest.raises(ValueError):
            C._routing(state, C._nets(state))
        with pytest.raises(ValueError):
            R._raw_trace_rows(state)
    else:
        routing, traces = C._routing(state, C._nets(state))
        assert len(routing) == len(traces) == 2
        assert R._raw_trace_rows(state)[1]["routing_row"]["geometry"] == geometry
