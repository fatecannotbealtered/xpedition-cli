"""Actual adapter → registered native masks → shared region graph controls.

Cell sets are synthetic native replies, not a licensed geometry implementation.
These cases specifically distinguish a continuous conductive pad from one pin
whose two separate owned shapes happen to share a persistent identity.
"""

import copy

import pytest
from test_native_pad_mask import add_graph_trace, array, cells, contour, evaluate_graph, material
from test_native_pad_mask import fixture as base_fixture

from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import digest


def bridging_fixture():
    doc, state, request, adapter, engine = base_fixture(foreign=True)
    raw = [[0, 4], [0, 0], [0, 0]]
    doc.Traces[0].Geometry.values[1] = raw
    doc.Traces[0].values[7].values = {1: -0.127, 2: -0.127, 3: 4.127, 4: 0.127}
    engine.paths[(digest(raw), 0.254)] = cells(0, 0, 5, 1)
    state["routing_objects"][0]["geometry"]["path"] = [[0, 0, 0], [4, 0, 0]]
    add_graph_trace(
        doc,
        state,
        engine,
        "t2",
        [[6, 10], [0, 0], [0, 0]],
        cells(6, 0, 11, 1),
        [5.873, -0.127, 10.127, 0.127],
    )
    outer = array(3, -2, 8, 3)
    engine.filled[digest(outer)] = cells(3, -2, 8, 3)
    state["all_pin_pad_material"].append(
        {
            "component_uid": "c4",
            "pin_uid": "p4",
            "refdes": "C1",
            "pin": "1",
            "center_mm": [5, 0],
            "net": "A",
            "net_uid": "n1",
            "material": material(contour(outer)),
        }
    )
    return doc, state, request, adapter, engine


@pytest.mark.parametrize("void", [None, "internal_cutout", "internal_drill"])
def test_continuous_pad_bridges_two_native_trace_components_in_both_verifiers(void):
    value = bridging_fixture()
    _, state, request, _, engine = value
    middle_material = state["all_pin_pad_material"][-1]["material"]
    if void == "internal_cutout":
        raw = array(5, -1, 6, 1)
        engine.filled[digest(raw)] = cells(5, -1, 6, 1)
        middle_material["pads"][0]["geometry"][0]["cutouts"] = [contour(raw)]
    elif void == "internal_drill":
        middle_material["holes"] = [{"center_mm": [5, 0], "diameter_mm": 1, "plated": True}]
    proof, final = evaluate_graph(value)
    assert proof["complete"] and final["valid"], (proof, final)
    observed = M.observe_material_components(state, request)
    assert observed["observation_valid"] and observed["completion_valid"], observed
    assert len(final["trace_components"]) == len(observed["trace_components"]) == 2
    assert len(final["components"]) == len(observed["components"]) == 1
    middle = next(row for row in proof["target_pad_regions"] if row["pin_uid"] == "p4")
    assert len(middle["regions"]) == 1
    assert all(row["area_mm2"] > 0 for row in middle["contacts"])
    assert not engine.masks


def test_same_pin_uid_does_not_short_two_separate_owned_conductive_regions():
    value = bridging_fixture()
    _, state, request, _, engine = value
    pin = state["all_pin_pad_material"][-1]
    left, right = array(3, -2, 5, 3), array(6, -2, 8, 3)
    for raw in (left, right):
        engine.filled[digest(raw)] = cells(min(raw[0]), min(raw[1]), max(raw[0]), max(raw[1]))
    pin["material"] = material(contour(left))
    pin["material"]["pads"][0]["bounds_mm"] = [3, -2, 8, 3]
    pin["material"]["pads"][0]["geometry"].append(
        {"filled": True, "outer": contour(right), "cutouts": []}
    )
    proof, final = evaluate_graph(value)
    observed = M.observe_material_components(state, request)
    assert proof["complete"] and observed["observation_valid"]
    assert not observed["complete"] and not final["valid"]
    assert len(observed["components"]) == len(final["components"]) == 2
    # Both groups may truthfully contain a region owned by the same physical pin.
    assert all("p4" in row["pin_ids"] for row in observed["components"])


@pytest.mark.parametrize(
    "fault",
    [
        "crossing_cutout",
        "arbitrary_arc",
        "missing_material",
        "wrong_layer",
        "wrong_net",
        "drill_only",
        "edge_only",
    ],
)
def test_region_graph_never_invents_a_bridge_for_incomplete_or_nonmaterial_contact(fault):
    value = bridging_fixture()
    doc, state, request, _, engine = value
    pin = state["all_pin_pad_material"][-1]
    if fault == "crossing_cutout":
        cut = array(4, -2, 7, 3)
        engine.filled[digest(cut)] = cells(4, -2, 7, 3)
        pin["material"]["pads"][0]["geometry"][0]["cutouts"] = [contour(cut)]
    elif fault == "arbitrary_arc":
        outer = pin["material"]["pads"][0]["geometry"][0]["outer"]
        old = digest(outer["points_array_mm"])
        outer["is_arced"] = True
        outer["points_array_mm"][2] = [0.7] * 5
        engine.filled[digest(outer["points_array_mm"])] = engine.filled[old]
    elif fault == "missing_material":
        pin["material"]["complete"] = False
        pin["material"]["unread"] = [{"reason": "native pad geometry unavailable"}]
    elif fault == "wrong_layer":
        pin["material"]["layer"] = 2
    elif fault == "wrong_net":
        doc.Traces[1].Net.UniqueId = "n2"
    else:
        raw = pin["material"]["pads"][0]["geometry"][0]["outer"]["points_array_mm"]
        # Positive outer-bound containment cannot override an empty material AND.
        engine.filled[digest(raw)] = {(5, 0)}
        if fault == "drill_only":
            pin["material"]["holes"] = [{"center_mm": [5, 0], "diameter_mm": 1, "plated": True}]
    _, final = evaluate_graph(value)
    observed = M.observe_material_components(state, request)
    assert not final["valid"] and not observed["completion_valid"], (fault, final, observed)
    assert not engine.masks


@pytest.mark.parametrize(
    "fault",
    [
        "missing_pin",
        "missing_region",
        "duplicate_region",
        "missing_contact",
        "duplicate_contact",
        "bad_empty",
        "bad_identity",
        "missing_cross_pin",
        "comparison_budget",
    ],
)
def test_region_receipts_cannot_promote_complete_with_missing_or_contradictory_evidence(fault):
    value = bridging_fixture()
    _, state, request, _, _ = value
    _, final = evaluate_graph(value)
    assert final["valid"]
    proof = state["native_mask_proof"]
    row = proof["target_pad_regions"][-1]
    if fault == "missing_pin":
        proof["target_pad_regions"].pop()
    elif fault == "missing_region":
        row["regions"].pop()
    elif fault == "duplicate_region":
        row["regions"].append(copy.deepcopy(row["regions"][0]))
    elif fault == "missing_contact":
        row["contacts"].pop()
    elif fault == "duplicate_contact":
        row["contacts"].append(copy.deepcopy(row["contacts"][0]))
    elif fault == "bad_empty":
        row["contacts"][0]["is_empty"] = True
    elif fault == "bad_identity":
        row["regions"][0]["source_sha256"] = "0" * 64
    elif fault == "missing_cross_pin":
        proof["target_pad_intersections"].pop()
    else:
        proof["budget"]["region_comparisons"] += 1
    assert not M.verify_material_graph(state, request)["valid"]
    assert not M.observe_material_components(state, request)["valid"]


def test_holes_are_strictly_internal_before_treating_annular_pad_as_connected():
    circle = {"kind": "circle", "center_mm": [0, 0], "radius_mm": 2}
    hole = {"kind": "circle", "center_mm": [0, 0], "radius_mm": 1}
    assert M._connected_shape_plan(circle, [hole])["kind"] == "circle"
    for center in ([1, 0], [1.00001, 0], [3, 0]):
        with pytest.raises(ValueError, match="outer boundary"):
            M._connected_shape_plan(circle, [{**hole, "center_mm": center}])


def test_simple_polygon_rejects_crossings_repeated_vertices_and_zero_area():
    for xy in (
        [(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)],
        [(0, 0), (2, 0), (2, 2), (2, 0), (0, 0)],
        [(0, 0), (1, 0), (2, 0), (0, 0)],
    ):
        raw = [[point[axis] for point in xy] for axis in (0, 1)] + [[0] * len(xy)]
        with pytest.raises(ValueError):
            M._connected_shape_plan(contour(raw), [])
