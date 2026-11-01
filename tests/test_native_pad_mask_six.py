"""Six-pin adapter→temporary-mask→receipt material graph tests.

As in test_native_pad_mask, cell sets are explicit synthetic native replies.
They exercise the actual observation chain without claiming licensed geometry
precision or proving a real six-terminal native routing result.
"""

import copy
from types import SimpleNamespace as NS

import pytest
from test_native_pad_mask import (
    add_graph_trace,
    array,
    cells,
    contour,
    evaluate_graph,
    fixture,
    material,
)

from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import digest


def _replace_trace(doc, state, engine, index, raw, occupied):
    trace = doc.Traces[index]
    trace.Geometry.values[1] = raw
    trace.values[7].values = {
        1: min(raw[0]) - 0.127,
        2: min(raw[1]) - 0.127,
        3: max(raw[0]) + 0.127,
        4: max(raw[1]) + 0.127,
    }
    state["routing_objects"][index]["geometry"]["path"] = [
        list(point) for point in zip(*raw, strict=True)
    ]
    engine.paths[(digest(raw), 0.254)] = occupied


def six_pin_material_fixture(*, fault=None):
    """Return doc, state, request, adapter, engine with six actual-shaped owners.

    Five registered target traces form a trunk and four middle T branches. The
    public routepass layer, separately, restricts this count to the exact GND
    membership; the mask layer checks every owned member and complete matrices.
    """
    doc, state, request, adapter, engine = fixture(foreign=True)
    members = [("J1", "2"), ("J2", "2"), ("J3", "2"), ("J4", "2"), ("C1", "2"), ("C2", "2")]
    targets = [row for row in state["all_pin_pad_material"] if row["net"] == "A"]
    # The second endpoint must not also overlap the separate x=8 T branch.
    endpoint = array(9, -2, 12, 3)
    engine.filled[digest(endpoint)] = cells(9, -2, 12, 3)
    targets[1]["material"] = material(contour(endpoint))
    for index, x in enumerate((2, 4, 6, 8), start=3):
        # Keep separate pins' actual copper disjoint. The old width-three
        # synthetic pads overlapped their neighbors and really bridged the
        # deliberately broken branch cases once pad material was modeled.
        raw = array(x - 0.75, 9, x + 0.75, 12)
        engine.filled[digest(raw)] = cells(x, 9, x + 1, 12)
        pin = {
            "component_uid": "c" + str(index + 1),
            "pin_uid": "p" + str(index + 1),
            "refdes": members[index - 1][0],
            "pin": "2",
            "center_mm": [x, 10],
            "net": "A",
            "net_uid": "n1",
            "material": material(contour(raw)),
        }
        state["all_pin_pad_material"].append(pin)
        targets.append(pin)
        add_graph_trace(
            doc,
            state,
            engine,
            "t" + str(index - 1),
            [[x, x], [0, 10], [0, 0]],
            cells(x, 0, x + 1, 11),
            [x - 0.127, -0.127, x + 0.127, 10.127],
        )
    for pin, (refdes, number) in zip(targets, members, strict=True):
        pin.update(refdes=refdes, pin=number, net="GND")
    for trace in doc.Traces:
        trace.Net.Name = "GND"
    for row in state["routing_objects"]:
        row["net"] = row["geometry"]["net"] = "GND"
    state["geometry"]["model"]["connectivity"]["nets"][0]["net"] = "GND"
    request["net"] = "GND"

    if fault == "missing_pin_contact":
        _replace_trace(doc, state, engine, 4, [[8, 8], [0, 7], [0, 0]], cells(8, 0, 9, 8))
    elif fault == "two_components":
        _replace_trace(doc, state, engine, 0, [[0, 4], [0, 0], [0, 0]], cells(0, 0, 5, 1))
        add_graph_trace(
            doc,
            state,
            engine,
            "t6",
            [[6, 10], [0, 0], [0, 0]],
            cells(6, 0, 11, 1),
            [5.873, -0.127, 10.127, 0.127],
            net="GND",
        )
        doc.Traces[-1].Net.UniqueId = "n1"
    elif fault == "isolated_copper":
        add_graph_trace(
            doc,
            state,
            engine,
            "t-isolated",
            [[20, 25], [20, 20], [0, 0]],
            cells(20, 20, 26, 21),
            [19.873, 19.873, 25.127, 20.127],
            net="GND",
        )
        doc.Traces[-1].Net.UniqueId = "n1"
    elif fault in {"zero_area_tangent", "microgap"}:
        y = 0.254 if fault == "zero_area_tangent" else 0.255
        _replace_trace(doc, state, engine, 4, [[8, 8], [y, 10], [0, 0]], cells(8, 1, 9, 11))
    elif fault == "hole_only":
        pin = targets[0]["material"]
        outer = pin["pads"][0]["geometry"][0]["outer"]["points_array_mm"]
        engine.filled[digest(outer)] = {(0, 0), (-2, -2)}
        pin["holes"] = [{"center_mm": [0, 0], "diameter_mm": 1, "plated": True}]
    elif fault == "wrong_net":
        doc.Traces[-1].Net = NS(Name="B", UniqueId="n2")
        state["routing_objects"][-1]["net"] = "B"
        state["routing_objects"][-1]["geometry"]["net"] = "B"
    elif fault == "wrong_layer":
        doc.Traces[-1].Layer = 2
        state["routing_objects"][-1]["geometry"]["layer"] = 2
    elif fault == "unknown_layer":
        doc.Traces[-1].Layer = None
    elif fault == "unsupported_curve":
        doc.Traces[-1].Geometry.IsArced = lambda: True
    elif fault is not None:
        raise ValueError("Unknown synthetic six-pin fault: " + fault)
    return doc, state, request, adapter, engine


def test_six_pin_full_adapter_chain_covers_all_pins_and_all_new_copper():
    fixture_value = six_pin_material_fixture()
    _, state, request, _, engine = fixture_value
    proof, graph = evaluate_graph(fixture_value)
    assert proof["complete"] and M.verify_evidence(state, request)["valid"], proof
    assert graph["valid"] and graph["all_members_connected"], graph
    assert len(graph["nodes"]) == 11
    assert len(proof["target_contacts"]) == 6 * 5
    assert len(proof["target_trace_intersections"]) == 5 * 4 // 2
    assert len(proof["trace_materials"]) == 5
    assert len(proof["foreign_intersections"]) == 5
    assert len(graph["trace_components"]) == len(graph["components"]) == 1
    assert len([row for row in graph["edges"] if row["kind"] == "trace_trace"]) == 4
    assert not engine.masks


def test_six_pin_zero_target_copper_is_valid_observation_but_never_a_connected_graph():
    fixture_value = six_pin_material_fixture()
    doc, state, request, _, engine = fixture_value
    doc.Traces.clear()
    state["routing_objects"] = []
    proof, graph = evaluate_graph(fixture_value)
    assert proof["complete"] and M.verify_evidence(state, request)["valid"], proof
    assert proof["target_trace_uids"] == proof["target_contacts"] == []
    assert proof["target_trace_intersections"] == proof["foreign_intersections"] == []
    assert not graph["valid"] and not graph["all_members_connected"]
    assert not engine.masks


@pytest.mark.parametrize(
    "fault",
    [
        "missing_pin_contact",
        "two_components",
        "isolated_copper",
        "zero_area_tangent",
        "microgap",
        "hole_only",
        "wrong_net",
        "wrong_layer",
        "unknown_layer",
        "unsupported_curve",
    ],
)
def test_six_pin_actual_adapter_material_failures_never_claim_connection(fault):
    fixture_value = six_pin_material_fixture(fault=fault)
    _, _, _, _, engine = fixture_value
    proof, graph = evaluate_graph(fixture_value)
    assert not graph["valid"] and not graph["all_members_connected"], (fault, proof, graph)
    assert not engine.masks
    if fault in {"two_components", "isolated_copper", "zero_area_tangent", "microgap"}:
        assert proof["complete"], proof
        assert len(graph["trace_components"]) == 2, graph


@pytest.mark.parametrize(
    "field",
    ["target_contacts", "target_trace_intersections", "trace_materials", "foreign_intersections"],
)
@pytest.mark.parametrize("change", ["missing", "duplicate"])
def test_six_pin_complete_marker_cannot_hide_missing_or_duplicate_object_matrix(field, change):
    fixture_value = six_pin_material_fixture()
    _, state, request, _, _ = fixture_value
    proof, graph = evaluate_graph(fixture_value)
    assert graph["valid"]
    if change == "missing":
        proof[field].pop()
    else:
        proof[field].append(copy.deepcopy(proof[field][0]))
    assert proof["complete"]
    assert not M.verify_evidence(state, request)["valid"]
    assert not M.verify_material_graph(state, request)["valid"]


@pytest.mark.parametrize("islands", ["separate_contours", "cutout"])
def test_six_pin_pad_islands_cannot_bridge_two_disconnected_trace_components(islands):
    fixture_value = six_pin_material_fixture(fault="two_components")
    _, state, _, _, engine = fixture_value
    # One pin has two disconnected copper islands, one in each trace component.
    # The graph must not collapse them into a trustworthy conducting pad node.
    pin = state["all_pin_pad_material"][0]
    if islands == "separate_contours":
        left, right = array(-2, -2, 3, 3), array(8, -2, 13, 3)
        engine.filled[digest(left)] = cells(-2, -2, 3, 3)
        engine.filled[digest(right)] = cells(8, -2, 13, 3)
        pin["material"] = material(contour(left))
        pad = pin["material"]["pads"][0]
        pad["bounds_mm"] = [-2, -2, 13, 3]
        pad["geometry"].append({"filled": True, "outer": contour(right), "cutouts": []})
    else:
        raw, cut = array(-2, -2, 13, 3), array(3, -2, 8, 3)
        engine.filled[digest(raw)] = cells(-2, -2, 13, 3)
        engine.filled[digest(cut)] = cells(3, -2, 8, 3)
        pin["material"] = material(contour(raw), cutouts=[contour(cut)])
    proof, graph = evaluate_graph(fixture_value)
    assert proof["complete"], proof
    assert not graph["valid"], graph
    if islands == "separate_contours":
        assert len(graph["trace_components"]) == len(graph["components"]) == 2
        assert graph["observation_valid"]
    else:
        # This finite solver explicitly declines a boundary-crossing void;
        # it never turns the two residual islands into one pin node.
        assert not graph["observation_valid"]
        assert "connected owned-region" in graph["reason"]


def test_six_pin_graph_crops_all_four_connector_holes_before_material_connectivity():
    fixture_value = six_pin_material_fixture()
    _, state, request, _, engine = fixture_value
    targets = [row for row in state["all_pin_pad_material"] if row["net"] == "GND"]
    for pin in targets[:4]:
        pin["material"]["holes"] = [
            {"center_mm": pin["center_mm"], "diameter_mm": 1, "plated": True}
        ]
    proof, graph = evaluate_graph(fixture_value)
    assert proof["complete"] and graph["valid"], (proof, graph)
    assert M.verify_evidence(state, request)["valid"]
    assert len(proof["round_holes"]) == 4
    assert all(len(row["hole_intersections"]) == 4 for row in proof["trace_materials"])
    assert (
        sum(
            entry["terminal_crop"] is not None
            for row in proof["trace_materials"]
            for entry in row["hole_intersections"]
        )
        == 4
    )
    assert all(row["material_area_mm2"] > 0 for row in proof["trace_materials"])
    assert not engine.masks


def test_six_pin_foreign_boundary_touch_cannot_be_cleared_by_zero_intersection_area():
    fixture_value = six_pin_material_fixture()
    _, state, request, _, engine = fixture_value
    foreign = next(row for row in state["all_pin_pad_material"] if row["net"] == "B")
    foreign["material"]["pads"][0]["bounds_mm"] = [-1, -1, 32, 32]
    engine.touch_result = True
    proof, graph = evaluate_graph(fixture_value)
    assert proof["complete"], proof
    assert all(row["intersection_area_mm2"] == 0 for row in proof["foreign_intersections"])
    assert any(row.get("boundary_touch") is True for row in proof["foreign_intersections"])
    assert not M.verify_evidence(state, request)["valid"] and not graph["valid"]


def test_six_pin_overlapping_bounds_accept_only_proved_native_material_separation():
    fixture_value = six_pin_material_fixture()
    _, state, request, _, engine = fixture_value
    foreign = next(row for row in state["all_pin_pad_material"] if row["net"] == "B")
    foreign["material"]["pads"][0]["bounds_mm"] = [-1, -1, 32, 32]
    engine.touch_result = False
    proof, graph = evaluate_graph(fixture_value)
    assert proof["complete"] and graph["valid"], (proof, graph)
    assert M.verify_evidence(state, request)["valid"]
    assert all(row.get("boundary_touch") is False for row in proof["foreign_intersections"])


@pytest.mark.parametrize("count", [4, 5, 7])
def test_material_adapter_keeps_finite_supported_target_counts(count):
    fixture_value = six_pin_material_fixture()
    _, state, _, _, engine = fixture_value
    materials = state["all_pin_pad_material"]
    target = [row for row in materials if row["net"] == "GND"]
    if count < 6:
        for row in target[count:]:
            row.update(net="B", net_uid="n2")
    else:
        extra = copy.deepcopy(target[-1])
        extra.update(component_uid="c-extra", pin_uid="p-extra")
        materials.append(extra)
    proof, graph = evaluate_graph(fixture_value)
    assert not proof["complete"] and not graph["valid"], (proof, graph)
    assert "Two, three or six" in proof["errors"][0]["reason"]
    assert not engine.masks
