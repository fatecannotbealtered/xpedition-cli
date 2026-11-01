"""Independent component profile, using registered synthetic native mask replies."""

import copy

import pytest
from test_native_pad_mask import add_graph_trace, array, cells, contour, fixture, material

from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import digest


def component_fixture(pin_count, *, net="SUPPLY_AUX", trace_count=1):
    doc, state, _, adapter, engine = fixture(traces=False)
    request = {"net": net, "net_uid": "actual-net-937", "layer": 1}
    state["geometry"]["model"]["connectivity"]["nets"] = [{"net": net, "uid": request["net_uid"]}]
    state["all_pin_pad_material"] = []
    for index in range(pin_count):
        x = index * 10
        raw = array(x - 1, -1, x + 2, 2)
        engine.filled[digest(raw)] = cells(x - 1, -1, x + 2, 2)
        state["all_pin_pad_material"].append(
            {
                "component_uid": "actual-owner-" + str(index),
                "pin_uid": "actual-pin-" + str(index),
                "refdes": "RENAMED" + str(index),
                "pin": "A" + str(index),
                "center_mm": [x, 0],
                "net": net,
                "net_uid": request["net_uid"],
                "material": material(contour(raw)),
            }
        )
    for index in range(trace_count):
        end = max(1, (pin_count - 1) * 10)
        add_graph_trace(
            doc,
            state,
            engine,
            "actual-trace-" + str(index),
            [[0, end], [0, 0], [0, 0]],
            cells(0, 0, end + 1, 1),
            [-0.127, -0.127, end + 0.127, 0.127],
            net=net,
        )
        doc.Traces[-1].Net.UniqueId = request["net_uid"]
    return doc, state, request, adapter, engine


def observe(value):
    doc, state, request, adapter, _ = value
    state["native_mask_proof"] = M.evaluate_components(doc, state, request, adapter)
    state["native_trace_material"] = copy.deepcopy(
        state["native_mask_proof"]["raw_trace_inventory"]
    )
    return M.observe_components(state, request)


@pytest.mark.parametrize("pin_count", [1, 4, 5, 7])
@pytest.mark.parametrize("net", ["SUPPLY_AUX", "DATA_renamed_93"])
@pytest.mark.parametrize("trace_count", [0, 1, 4])
def test_generic_counts_and_names_are_independent_of_legacy_samples(pin_count, net, trace_count):
    value = component_fixture(pin_count, net=net, trace_count=trace_count)
    _, state, request, _, engine = value
    result = observe(value)
    assert result["valid"] and result["observation_valid"] and result["complete"], result
    assert state["native_mask_proof"]["request"] == request
    assert state["native_mask_proof"]["profile"] == M.COMPONENT_PROFILE
    assert M.verify_component_evidence(state, request)["valid"]
    assert len(result["nodes"]) == pin_count + trace_count
    assert result["all_members_connected"] is (trace_count > 0 or pin_count == 1)
    assert M.verify_components_graph(state, request)["valid"] is result["all_members_connected"]
    assert engine.masks == []
    assert not M.verify_evidence(state, request)["valid"]


def test_zero_pin_copper_islands_bind_actual_network_and_retain_every_trace():
    value = component_fixture(0, trace_count=1)
    doc, state, request, _, engine = value
    add_graph_trace(
        doc,
        state,
        engine,
        "isolated-old-copper",
        [[30, 40], [20, 20], [0, 0]],
        cells(30, 20, 41, 21),
        [29.873, 19.873, 40.127, 20.127],
        net=request["net"],
    )
    doc.Traces[-1].Net.UniqueId = request["net_uid"]
    result = observe(value)
    assert result["valid"] and result["complete"], result
    assert len(result["components"]) == 2
    assert not result["all_members_connected"] and not result["completion"]["valid"]
    assert {uid for row in result["components"] for uid in row["trace_ids"]} == {
        "actual-trace-0",
        "isolated-old-copper",
    }
    assert all(row["pin_ids"] == [] for row in result["components"])
    proof = state["native_mask_proof"]
    proof["target_trace_intersections"] = []
    assert not M.observe_components(state, request)["valid"]


def test_empty_material_network_is_observed_but_does_not_claim_connected():
    value = component_fixture(0, trace_count=0)
    result = observe(value)
    assert result["valid"] and result["complete"], result
    assert result["nodes"] == result["components"] == []
    assert not result["completion_valid"] and not result["all_members_connected"]


def test_same_pin_disconnected_material_regions_are_separate_components():
    value = component_fixture(1, trace_count=0)
    _, state, request, _, engine = value
    pin = state["all_pin_pad_material"][0]
    raw = array(20, -1, 23, 2)
    engine.filled[digest(raw)] = cells(20, -1, 23, 2)
    pad = pin["material"]["pads"][0]
    pad["bounds_mm"] = [-1, -1, 23, 2]
    pad["geometry"].append({"filled": True, "outer": contour(raw), "cutouts": []})
    result = observe(value)
    assert result["valid"] and result["complete"], result
    assert len(result["components"]) == 2 and not result["all_members_connected"]
    assert [row["pin_ids"] for row in result["components"]] == [[pin["pin_uid"]]] * 2
    assert {row["region_index"] for row in result["nodes"]} == {0, 1}
    assert "not persistent IDs" in result["node_identity_scope"]
    assert not M.verify_components_graph(state, request)["valid"]
    state["native_mask_proof"]["target_pad_regions"][0]["regions"].pop()
    assert not M.observe_components(state, request)["valid"]


@pytest.mark.parametrize("uid", [None, "0", "invented", ""])
def test_generic_profile_requires_adapter_observed_actual_net_uid(uid):
    doc, state, request, adapter, engine = component_fixture(0)
    request["net_uid"] = uid
    proof = M.evaluate_components(doc, state, request, adapter)
    assert not proof["complete"] and proof["errors"]
    assert engine.calls == []


def test_generic_profile_rejects_ambiguous_native_net_identity():
    doc, state, request, adapter, engine = component_fixture(0)
    state["geometry"]["model"]["connectivity"]["nets"].append(
        {"net": "other-name", "uid": request["net_uid"]}
    )
    proof = M.evaluate_components(doc, state, request, adapter)
    assert not proof["complete"] and engine.calls == []


def test_generic_target_pin_budget_is_an_upper_bound_not_an_allowed_count_list():
    doc, state, request, adapter, engine = component_fixture(
        M.MAX_COMPONENT_PINS + 1, trace_count=0
    )
    proof = M.evaluate_components(doc, state, request, adapter)
    assert not proof["complete"] and proof["errors"] and engine.calls == []


def test_generic_target_pin_budget_includes_upper_bound():
    value = component_fixture(M.MAX_COMPONENT_PINS, trace_count=0)
    result = observe(value)
    assert result["valid"] and len(result["components"]) == M.MAX_COMPONENT_PINS, result


def test_pure_copper_material_graph_accepts_real_middle_t_contact():
    value = component_fixture(0)
    doc, state, request, _, engine = value
    doc.Traces[0].Geometry.values[1] = [[0, 10], [0, 0], [0, 0]]
    doc.Traces[0].values[7].values = {1: -0.127, 2: -0.127, 3: 10.127, 4: 0.127}
    state["routing_objects"][0]["geometry"]["path"] = [[0, 0, 0], [10, 0, 0]]
    engine.paths[(digest(doc.Traces[0].Geometry.values[1]), 0.254)] = cells(0, 0, 11, 1)
    add_graph_trace(
        doc,
        state,
        engine,
        "middle-t-branch",
        [[5, 5], [0, 10], [0, 0]],
        cells(5, 0, 6, 11),
        [4.873, -0.127, 5.127, 10.127],
        net=request["net"],
    )
    doc.Traces[-1].Net.UniqueId = request["net_uid"]
    result = observe(value)
    assert result["valid"] and result["all_members_connected"], result
    assert len(result["components"]) == 1
    assert [row["kind"] for row in result["edges"]] == ["trace_trace"]


@pytest.mark.parametrize("layer", [True, 0, 129, "1"])
def test_generic_receipt_rejects_invalid_layer_even_without_pads(layer):
    value = component_fixture(0)
    _, state, request, _, _ = value
    assert observe(value)["valid"]
    request["layer"] = layer
    state["native_mask_proof"]["request"] = copy.deepcopy(request)
    assert not M.verify_component_evidence(state, request)["valid"]


def test_legacy_default_retains_two_three_six_pin_limit():
    doc, state, request, adapter, engine = component_fixture(4)
    proof = M.evaluate(doc, state, request, adapter)
    assert not proof["complete"] and proof["errors"] and engine.calls == []


def test_generic_receipt_cannot_change_bound_request_or_be_accepted_as_legacy():
    value = component_fixture(4)
    _, state, request, _, _ = value
    assert observe(value)["valid"]
    assert not M.verify_component_evidence(state, {**request, "layer": 2})["valid"]
    state["native_mask_proof"].pop("profile")
    assert not M.verify_component_evidence(state, request)["valid"]
