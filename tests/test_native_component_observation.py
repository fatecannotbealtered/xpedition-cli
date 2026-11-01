"""Fresh adapter/mask component inventory, separate from routing completion.

Synthetic native replies exercise the actual observer chain. They are not a
licensed geometry or real routing proof; opaque contour rows and cell sets keep
that distinction explicit, as in test_native_pad_mask.
"""

import copy
from types import SimpleNamespace as NS

import pytest
from test_native_pad_mask import add_graph_trace, array, cells, contour, fixture, material
from test_native_pad_mask_six import _replace_trace, six_pin_material_fixture

from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import digest


def component_fixture(*, connected=False):
    doc, state, request, adapter, engine = fixture(traces=False, foreign=True)
    members = [
        ("J1", "109", "87", 0, 0),
        ("J2", "116", "82", 20, 0),
        ("J3", "123", "77", 0, 30),
        ("J4", "130", "72", 20, 30),
        ("C1", "101", "97", 10, 10),
        ("C2", "105", "92", 10, 30),
    ]
    pins = []
    for refdes, uid, owner, x, y in members:
        raw = array(x - 1, y - 1, x + 2, y + 2)
        engine.filled[digest(raw)] = cells(x - 1, y - 1, x + 2, y + 2)
        pins.append(
            {
                "component_uid": owner,
                "pin_uid": uid,
                "refdes": refdes,
                "pin": "2",
                "center_mm": [x, y],
                "net": "GND",
                "net_uid": "n1",
                "material": material(contour(raw)),
            }
        )
    foreign = copy.deepcopy(state["all_pin_pad_material"][-1])
    foreign.update(component_uid="other", pin_uid="foreign", center_mm=[100, 100])
    outer = array(99, 99, 102, 102)
    foreign["material"] = material(contour(outer))
    engine.filled[digest(outer)] = cells(99, 99, 102, 102)
    state["all_pin_pad_material"] = [*pins, foreign]
    state["geometry"]["model"]["connectivity"]["nets"][0]["net"] = "GND"
    request["net"] = "GND"

    def trace(uid, points, occupied):
        raw = [[p[i] for p in points] for i in (0, 1)] + [[0] * len(points)]
        add_graph_trace(
            doc,
            state,
            engine,
            uid,
            raw,
            occupied,
            [min(raw[0]) - 0.127, min(raw[1]) - 0.127, max(raw[0]) + 0.127, max(raw[1]) + 0.127],
            net="GND",
        )
        doc.Traces[-1].Net.UniqueId = "n1"

    trace("1441", [(20, 30), (10, 30)], cells(10, 30, 21, 31))
    trace("1442", [(0, 0), (10, 0), (10, 10)], cells(0, 0, 11, 1) | cells(10, 0, 11, 11))
    trace("1443", [(10, 10), (20, 10), (20, 0)], cells(10, 10, 21, 11) | cells(20, 0, 21, 11))
    if connected:
        trace("new1", [(10, 10), (10, 30)], cells(10, 10, 11, 31))
        trace("new2", [(0, 30), (10, 30)], cells(0, 30, 11, 31))
    return doc, state, request, adapter, engine


def observe(value):
    doc, state, request, adapter, engine = value
    proof = M.evaluate(doc, state, request, adapter)
    state["native_mask_proof"] = proof
    state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
    return proof, M.observe_material_components(state, request)


def test_complete_three_groups_is_valid_observation_without_network_completion():
    value = component_fixture()
    _, state, request, _, engine = value
    proof, observed = observe(value)
    assert proof["complete"] and M.verify_evidence(state, request)["valid"], proof
    assert observed["valid"] and observed["observation_valid"], observed
    assert not observed["complete"] and not observed["completion_valid"]
    assert not observed["all_members_connected"]
    assert len(observed["nodes"]) == 9
    assert observed["trace_components"] == [["trace:1441"], ["trace:1442", "trace:1443"]]
    assert [row["trace_ids"] for row in observed["components"]] == [[], ["1441"], ["1442", "1443"]]
    assert [
        {row["refdes"] for row in group["pin_members"]} for group in observed["components"]
    ] == [{"J3"}, {"J4", "C2"}, {"J1", "J2", "C1"}]
    assert observed["components"][0]["pin_members"] == [
        {
            "component_uid": "77",
            "pin_uid": "123",
            "refdes": "J3",
            "pin": "2",
            "net_uid": "n1",
            "layer": 1,
        }
    ]
    assert observed["component_membership_sha256"] == digest(observed["components"])
    assert not M.verify_material_graph(state, request)["valid"]
    assert not engine.masks


def test_three_groups_to_one_requires_every_old_and_new_trace_and_all_six_pins():
    value = component_fixture(connected=True)
    _, state, request, _, engine = value
    proof, observed = observe(value)
    assert proof["complete"] and observed["valid"], (proof, observed)
    assert observed["complete"] and observed["completion_valid"]
    assert len(observed["components"]) == len(observed["trace_components"]) == 1
    assert observed["components"][0]["trace_ids"] == ["1441", "1442", "1443", "new1", "new2"]
    assert len(observed["components"][0]["pin_members"]) == 6
    assert M.verify_material_graph(state, request)["valid"]
    assert len(proof["target_contacts"]) == 30
    assert len(proof["target_trace_intersections"]) == 10
    assert not engine.masks


def test_no_copper_is_complete_component_observation_with_six_isolated_members():
    value = component_fixture()
    doc, state, _, _, engine = value
    doc.Traces.clear()
    state["routing_objects"].clear()
    proof, observed = observe(value)
    assert proof["complete"] and observed["valid"], (proof, observed)
    assert len(observed["components"]) == 6 and observed["trace_components"] == []
    assert not observed["complete"] and not engine.masks


@pytest.mark.parametrize(
    "field",
    ["target_contacts", "target_trace_intersections", "trace_materials", "foreign_intersections"],
)
@pytest.mark.parametrize("change", ["missing", "duplicate"])
def test_incomplete_native_matrices_never_become_valid_component_observation(field, change):
    value = component_fixture(connected=True)
    _, state, request, _, _ = value
    proof, observed = observe(value)
    assert observed["valid"]
    if change == "missing":
        proof[field].pop()
    else:
        proof[field].append(copy.deepcopy(proof[field][0]))
    result = M.observe_material_components(state, request)
    assert not result["valid"] and not result["complete"]
    assert not result["component_membership_sha256"]


@pytest.mark.parametrize(
    "fault",
    [
        "missing_isolated_pin",
        "duplicate_pin_uid",
        "duplicate_member_name",
        "missing_name",
        "duplicate_trace_uid",
        "unread_layer",
        "wrong_layer",
        "wrong_net",
        "unsupported_curve",
    ],
)
def test_actual_adapter_failures_do_not_hide_members_or_old_copper(fault):
    value = component_fixture()
    doc, state, _, _, _ = value
    if fault == "missing_isolated_pin":
        state["all_pin_pad_material"].pop(2)
    elif fault == "duplicate_pin_uid":
        state["all_pin_pad_material"][2]["pin_uid"] = "109"
    elif fault == "duplicate_member_name":
        state["all_pin_pad_material"][2]["refdes"] = "J1"
    elif fault == "missing_name":
        state["all_pin_pad_material"][2]["refdes"] = ""
    elif fault == "duplicate_trace_uid":
        doc.Traces[-1].UniqueId = doc.Traces[0].UniqueId
        state["routing_objects"][-1]["uid"] = state["routing_objects"][0]["uid"]
    elif fault == "unread_layer":
        doc.Traces[-1].Layer = None
    elif fault == "wrong_layer":
        doc.Traces[-1].Layer = 2
        state["routing_objects"][-1]["geometry"]["layer"] = 2
    elif fault == "wrong_net":
        doc.Traces[-1].Net = NS(Name="B", UniqueId="n2")
        state["routing_objects"][-1]["net"] = "B"
        state["routing_objects"][-1]["geometry"]["net"] = "B"
    elif fault == "unsupported_curve":
        doc.Traces[-1].Geometry.IsArced = lambda: True
    proof, observed = observe(value)
    assert not observed["valid"] and not observed["complete"], (fault, proof, observed)


def test_receipt_cannot_exclude_old_trace_while_fresh_inventory_still_contains_it():
    value = component_fixture(connected=True)
    _, state, request, _, _ = value
    proof, observed = observe(value)
    assert observed["complete"]
    proof["raw_trace_inventory"].pop(0)
    result = M.observe_material_components(state, request)
    assert not result["valid"] and not result["complete"]


@pytest.mark.parametrize("islands", ["separate_contours", "cutout"])
def test_observation_distinguishes_proven_separate_pad_regions_from_unproved_cutout(islands):
    value = component_fixture()
    doc, state, request, _, engine = value
    # Two separate trace components reach two isolated pieces of J3's observed
    # pad in an otherwise empty rectangle, well clear of every other pin.
    _replace_trace(
        doc,
        state,
        engine,
        1,
        [[0, 10, 10, 10], [0, 0, 16, 10], [0, 0, 0, 0]],
        cells(0, 0, 11, 1) | cells(10, 0, 11, 17),
    )
    _replace_trace(
        doc,
        state,
        engine,
        0,
        [[20, 15, 15, 15, 10], [30, 30, 25, 30, 30], [0, 0, 0, 0, 0]],
        cells(10, 30, 21, 31) | cells(15, 25, 16, 31),
    )
    pin = state["all_pin_pad_material"][2]
    raw, cut = array(9, 14, 17, 27), array(9, 17, 17, 24)
    engine.filled[digest(raw)] = cells(9, 14, 17, 27)
    engine.filled[digest(cut)] = cells(9, 17, 17, 24)
    pin["material"] = material(contour(raw), cutouts=[contour(cut)] if islands == "cutout" else [])
    if islands == "separate_contours":
        first, second = array(9, 14, 12, 17), array(14, 24, 17, 27)
        engine.filled[digest(first)] = cells(9, 14, 12, 17)
        engine.filled[digest(second)] = cells(14, 24, 17, 27)
        pin["material"]["pads"][0]["geometry"] = [
            {"filled": True, "outer": contour(first), "cutouts": []},
            {"filled": True, "outer": contour(second), "cutouts": []},
        ]
    proof, observed = observe(value)
    assert proof["complete"], proof
    assert not observed["complete"]
    assert not M.verify_material_graph(state, request)["valid"]
    if islands == "separate_contours":
        # Separate owned shapes now have independent material nodes. Knowing
        # that both are owned by J3 must not electrically merge their groups.
        assert observed["valid"] and observed["observation_valid"], observed
        assert not observed["completion_valid"]
        assert len(observed["trace_components"]) == len(observed["components"]) == 2
        regions = [row for row in observed["nodes"] if row.get("pin_uid") == "123"]
        assert len(regions) == 2
        assert {row["region_index"] for row in regions} == {0, 1}
        assert all("123" in group["pin_ids"] for group in observed["components"])
    else:
        # This cut spans the complete outer width, so this finite topology
        # witness cannot assert continuity. Preserve the complete raw masks,
        # and explicitly refuse a final graph rather than merging by pin UID.
        assert not observed["valid"] and not observed["observation_valid"]
        assert observed["reason"] == "complete connected owned-region observation for 123"
        witness = next(row for row in proof["target_pad_regions"] if row["pin_uid"] == "123")
        assert witness["complete"] is False
        assert "outer boundary" in witness["reason"]


def test_overlapping_pad_bounds_use_observed_material_pair_matrix_not_box_inference():
    value = component_fixture(connected=True)
    _, state, _, _, _ = value
    state["all_pin_pad_material"][0]["material"]["pads"][0]["bounds_mm"] = [-1, -1, 22, 2]
    proof, observed = observe(value)
    assert proof["complete"] and observed["valid"] and observed["complete"], (proof, observed)
    assert any(not row["strict_bounds_separated"] for row in observed["pin_pair_separation"])
    pairs = proof["target_pad_intersections"]
    assert len(pairs) == 6 * 5 // 2
    assert all(row["area_mm2"] == 0 and row["is_empty"] for row in pairs)
    # The matrix is actual native evidence, not implied by an enlarged box.
    # Missing one pair invalidates both observation and final acceptance.
    proof["target_pad_intersections"].pop()
    assert not M.observe_material_components(value[1], value[2])["valid"]
    assert not M.verify_material_graph(value[1], value[2])["valid"]


@pytest.mark.parametrize(
    "fault", ["missing_pin_contact", "zero_area_tangent", "microgap", "hole_only"]
)
def test_positive_material_failures_cannot_be_upgraded_from_observation_to_completion(fault):
    # Raw readability and final positive-material completion are separate:
    # known gaps are valid observations while missing material stays incomplete.
    value = six_pin_material_fixture(fault=fault)
    _, state, request, _, _ = value
    proof, observed = observe(value)
    assert not observed["complete"] and not M.verify_material_graph(state, request)["valid"], proof


def test_zero_area_contact_stays_disconnected_in_complete_component_observation():
    value = component_fixture(connected=True)
    doc, state, request, _, engine = value
    _replace_trace(
        doc, state, engine, len(doc.Traces) - 1, [[0, 10], [32, 32], [0, 0]], cells(0, 32, 10, 33)
    )
    proof, observed = observe(value)
    assert proof["complete"] and observed["valid"], (proof, observed)
    assert not observed["complete"] and not M.verify_material_graph(state, request)["valid"]
    assert len(observed["trace_components"]) == 2
    assert set(sum((group["trace_ids"] for group in observed["components"]), [])) == {
        "1441",
        "1442",
        "1443",
        "new1",
        "new2",
    }


def test_isolated_additional_copper_is_retained_in_observed_components():
    value = component_fixture(connected=True)
    doc, state, request, _, engine = value
    raw = [[60, 65], [60, 60], [0, 0]]
    add_graph_trace(
        doc,
        state,
        engine,
        "isolated",
        raw,
        cells(60, 60, 66, 61),
        [59.873, 59.873, 65.127, 60.127],
        net="GND",
    )
    doc.Traces[-1].Net.UniqueId = "n1"
    proof, observed = observe(value)
    assert proof["complete"] and observed["valid"] and not observed["complete"], (proof, observed)
    group = next(row for row in observed["components"] if row["trace_ids"] == ["isolated"])
    assert group["pin_members"] == []
    assert not M.verify_material_graph(state, request)["valid"]
