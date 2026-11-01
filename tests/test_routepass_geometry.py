"""Synthetic native output only; not a licensed route-engine proof."""

import copy

import pytest
from test_trace_safety import trace_state

from xpedition_cli import board_routepass as R
from xpedition_cli import native_com_adapter as A


def baseline():
    state = trace_state()
    state["texts"] = [{"uid": "t1", "text": "J1", "position": [0, 1]}]
    state["all_pin_pad_material"] = copy.deepcopy(state["endpoint_pad_material"])
    return state


def plan_case():
    state = baseline()
    return R.bind(state, {"net": "A", "layer": 1}, [], A)


def routed_state(before, paths=None):
    after = copy.deepcopy(before)
    paths = paths or [([[0, 0, 0], [2, 0, 0]], 0.254), ([[2, 0, 0], [5, 0, 0]], 0.3)]
    for index, (path, width) in enumerate(paths):
        shape = {"kind": "trace", "net": "A", "layer": 1, "width": width, "path": path}
        after["routing_objects"].append(
            {"kind": "trace", "uid": str(100 + index), "net": "A", "geometry": copy.deepcopy(shape)}
        )
        after["geometry"]["model"]["traces"].append(shape)
    nets = after["geometry"]["model"]["connectivity"]["nets"]
    nets[0].update(opens=0, routed=True)
    after["geometry"]["model"]["unrouted"] = [
        copy.deepcopy(r) for r in nets if r["opens"] or not r["routed"]
    ]
    return after


def test_multiple_native_objects_and_different_real_widths_are_independently_verified():
    plan = plan_case()
    assert plan["can_apply"] and not plan["before"]["rules"]["local_applicability_verified"]
    proof = R.compare(plan, routed_state(plan["before"]))
    assert proof["valid"] and len(proof["created_objects"]) == 2
    assert proof["physical_connection"]["segment_count"] == 2
    assert proof["native_connectivity_verified"] and not proof["drc_executed"]


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_net",
        "wrong_layer",
        "unknown_layer",
        "zero_width",
        "nan_width",
        "arc",
        "duplicate_uid",
        "missing_object",
        "deleted_old",
        "changed_old",
        "wrong_opens",
        "negative_opens",
        "other_net",
        "rules",
        "component",
        "hole",
        "text",
        "plane",
        "pin_uid",
        "material",
        "incomplete_net",
    ],
)
def test_complete_native_result_rejects_wrong_and_unprotected_output(fault):
    plan = plan_case()
    after = routed_state(plan["before"])
    rows, model = after["routing_objects"], after["geometry"]["model"]
    new = rows[-1]
    if fault == "wrong_net":
        new["net"] = new["geometry"]["net"] = "B"
    elif fault == "wrong_layer":
        new["geometry"]["layer"] = 2
    elif fault == "unknown_layer":
        new["geometry"]["layer"] = None
    elif fault == "zero_width":
        new["geometry"]["width"] = 0
    elif fault == "nan_width":
        new["geometry"]["width"] = float("nan")
    elif fault == "arc":
        new["geometry"]["path"][0][2] = 1
    elif fault == "duplicate_uid":
        new["uid"] = rows[-2]["uid"]
    elif fault == "missing_object":
        rows.pop()
    elif fault == "deleted_old":
        rows.pop(0)
    elif fault == "changed_old":
        rows[0]["geometry"]["width"] = 0.5
    elif fault == "wrong_opens":
        model["connectivity"]["nets"][0].update(opens=1, routed=False)
    elif fault == "negative_opens":
        model["connectivity"]["nets"][0]["opens"] = -1
    elif fault == "other_net":
        model["connectivity"]["nets"][1]["opens"] = 1
    elif fault == "rules":
        after["rules"]["scheme"] = "changed"
    elif fault == "component":
        after["geometry"]["components"][0]["refdes"] = "WRONG"
    elif fault == "hole":
        model["holes"][0][2] += 1
    elif fault == "text":
        after["texts"][0]["position"][0] += 1
    elif fault == "plane":
        after["plane_inventory"]["assignments"].append({"layer": 1})
    elif fault == "pin_uid":
        after["pin_identities"][0]["pin_uid"] = "other"
    elif fault == "material":
        after["endpoint_pad_material"][0]["material"]["complete"] = False
    elif fault == "incomplete_net":
        model["connectivity"]["complete"] = False
    assert not R.compare(plan, after)["valid"]


@pytest.mark.parametrize(
    "paths",
    [
        [([[0, 0, 0], [2, 0, 0]], 0.254), ([[3, 0, 0], [5, 0, 0]], 0.254)],
        [([[0, 0, 0], [2, 0, 0], [5, 0, 0]], 0.254), ([[2, 0, 0], [2, 1, 0]], 0.254)],
        [([[0, 0, 0], [5, 0, 0]], 0.254), ([[1, 1, 0], [2, 1, 0], [2, 2, 0], [1, 1, 0]], 0.254)],
        [([[0, 0, 0], [2, 0, 0], [0, 0, 0], [5, 0, 0]], 0.254)],
        [([[0, 0, 0], [2, 2, 0], [2, -2, 0], [1, 1, 0], [5, 0, 0]], 0.254)],
        [([[1, 0, 0], [4, 0, 0]], 0.254)],
    ],
)
def test_false_native_opens_zero_does_not_prove_material_chain(paths):
    plan = plan_case()
    proof = R.compare(plan, routed_state(plan["before"], paths))
    assert proof["native_connectivity_verified"]
    assert not proof["valid"] and not proof["physical_connection"]["verified"]


def test_only_drill_void_contact_fails_even_when_native_opens_zero():
    plan = plan_case()
    from test_trace_target_contract import owned_pin_material

    for endpoint in plan["endpoints"]:
        material = owned_pin_material(endpoint["center_mm"], circle=True, hole=True)
        endpoint["pads"], endpoint["holes"] = material["pads"], material["holes"]
    # End inside the drill and leave each pad vertically outside the copper
    # cannot be a two-terminal chain; a tiny path in one drill never suffices.
    proof = R.physical_chain(
        plan,
        [
            {
                "kind": "trace",
                "uid": "x",
                "net": "A",
                "geometry": {
                    "kind": "trace",
                    "net": "A",
                    "layer": 1,
                    "width": 0.254,
                    "path": [[0, 0, 0], [0.05, 0, 0]],
                },
            }
        ],
    )
    assert not proof["verified"]


def test_full_width_foreign_pad_contact_is_not_masked_by_correct_target_opens():
    plan = plan_case()
    # Centerline remains clear of B's pad at y=3, but this actual trace body's
    # radius reaches it. Native target opens=0 is deliberately unchanged.
    after = routed_state(plan["before"], [([[0, 0, 0], [5, 0, 0]], 6)])
    proof = R.compare(plan, after)
    assert proof["native_connectivity_verified"]
    assert proof["physical_connection"]["verified"]
    assert not proof["foreign_material_separation"]["verified"]
    assert not proof["valid"]


def test_foreign_old_trace_contact_rejects_even_if_pad_material_is_clear():
    plan = plan_case()
    after = routed_state(plan["before"], [([[0, 0, 0], [2.5, 3, 0], [5, 0, 0]], 0.254)])
    proof = R.compare(plan, after)
    assert not proof["valid"]
    assert "existing trace" in proof["foreign_material_separation"]["reason"]


@pytest.mark.parametrize(
    "fault",
    ["planes", "three_pins", "already_routed", "existing_trace", "stackup", "missing_material"],
)
def test_bind_limited_eligibility_never_relaxes_the_existing_contract(fault):
    state = baseline()
    if fault == "planes":
        state["plane_inventory"]["assignments"].append({"layer": 1})
    elif fault == "three_pins":
        state["geometry"]["components"][0]["geometry"].append(
            copy.deepcopy(state["geometry"]["components"][0]["geometry"][0])
        )
    elif fault == "already_routed":
        state["geometry"]["model"]["connectivity"]["nets"][0].update(opens=0, routed=True)
    elif fault == "existing_trace":
        state["routing_objects"][0]["net"] = "A"
    elif fault == "stackup":
        state["geometry"]["stackup"].pop()
    elif fault == "missing_material":
        state["endpoint_pad_material"][0]["material"]["complete"] = False
    with pytest.raises(A.AdapterError):
        R.bind(state, {"net": "A", "layer": 1}, [], A)


def native_mask_case(*, noncenter=False):
    """Registered fake mask cells test integration; no real arc interpretation."""
    from test_native_pad_mask import cells, fixture

    from xpedition_cli import native_pad_mask as M
    from xpedition_cli.cell_refresh import _canonical, digest

    doc, raw, request, adapter, engine = fixture(traces=False, foreign=True)
    before = baseline()
    before["all_pin_pad_material"] = raw["all_pin_pad_material"]
    before["geometry"]["components"] = []
    before["pin_identities"] = []
    before["endpoint_pad_material"] = []
    for row in before["all_pin_pad_material"]:
        row["center_mm"] = [float(v) for v in row["center_mm"]]
        bounds = row["material"]["pads"][0]["bounds_mm"]
        before["geometry"]["components"].append(
            {
                "uid": row["component_uid"],
                "refdes": row["refdes"],
                "geometry": [
                    {
                        "number": row["pin"],
                        "net": row["net"],
                        "center_mm": row["center_mm"],
                        "holes": [],
                        "copper": [{"layer": 1, "geometry": [{"bounds_mm": bounds}]}],
                    }
                ],
            }
        )
        before["pin_identities"].append(
            {k: row[k] for k in ("component_uid", "refdes", "pin", "pin_uid", "net", "net_uid")}
        )
        before["endpoint_pad_material"].append(
            {
                **before["pin_identities"][-1],
                "material": {
                    "complete": False,
                    "pads": [],
                    "holes": [],
                    "unread": [{"reason": "legacy Python arc parser unsupported"}],
                },
            }
        )
    before["routing_objects"] = []
    model = before["geometry"]["model"]
    model["holes"] = []  # This registered native fixture has no drill holes.
    model["traces"] = []
    model["connectivity"]["nets"] = [
        {"net": "A", "uid": "n1", "opens": 1, "routed": False},
        {"net": "B", "uid": "n2", "opens": 0, "routed": True},
    ]
    model["unrouted"] = [copy.deepcopy(model["connectivity"]["nets"][0])]
    before["native_mask_proof"] = M.evaluate(doc, before, request, adapter)
    before["native_mask_proof"]["request"] = copy.deepcopy(request)
    before["native_trace_material"] = before["native_mask_proof"]["raw_trace_inventory"]
    assert M.verify_evidence(before, request)["valid"]
    plan = R.bind(before, request, [], A)
    after = copy.deepcopy(before)
    routed_doc, routed_raw, _, routed_adapter, routed_engine = fixture(traces=True, foreign=True)
    target = routed_raw["routing_objects"]
    if noncenter:
        path = [[0.5, 9.5], [0, 0], [0, 0]]
        routed_doc.Traces[0].Geometry.values[1] = path
        routed_engine.paths[(digest(path), 0.254)] = cells(0, 0, 11, 1)
        target[0]["geometry"]["path"] = [[0.5, 0, 0], [9.5, 0, 0]]
    after["routing_objects"] = copy.deepcopy(target)
    after["geometry"]["model"]["traces"] = [_canonical(copy.deepcopy(target[0]["geometry"]))]
    after["geometry"]["model"]["connectivity"]["nets"][0].update(opens=0, routed=True)
    after["geometry"]["model"]["unrouted"] = []
    after["native_mask_proof"] = M.evaluate(routed_doc, after, request, routed_adapter)
    after["native_mask_proof"]["request"] = copy.deepcopy(request)
    after["native_trace_material"] = after["native_mask_proof"]["raw_trace_inventory"]
    assert M.verify_evidence(after, request)["valid"]
    return plan, after


@pytest.mark.parametrize("noncenter", [False, True])
def test_actual_raw_native_mask_chain_accepts_owned_pad_contacts_without_center_requirement(
    noncenter,
):
    plan, after = native_mask_case(noncenter=noncenter)
    assert plan["material_verification_mode"] == "native-mask"
    assert plan["before"]["endpoint_pad_material"][0]["material"]["complete"] is False
    proof = R.compare(plan, after)
    assert proof["valid"], proof
    assert proof["physical_connection"]["verified"]
    assert proof["foreign_material_separation"]["verified"]
    assert (
        proof["physical_connection"]["endpoint_witnesses"][0]["native_mask_intersection"][
            "positive_intersection_area_mm2"
        ]
        > 0
    )


@pytest.mark.parametrize(
    "fault",
    [
        "raw_width",
        "raw_points",
        "mask_flag",
        "mask_digest",
        "matrix_missing",
        "raw_material",
        "cleanup_missing",
    ],
)
def test_native_mask_result_is_not_a_boolean_override(fault):
    plan, after = native_mask_case()
    if fault == "raw_width":
        after["native_trace_material"][0]["width_mm"] += 0.001
    elif fault == "raw_points":
        after["native_trace_material"][0]["points_array_mm"][0][0] += 0.000000001
    elif fault == "mask_flag":
        after["native_mask_proof"]["complete"] = False
    elif fault == "mask_digest":
        after["native_mask_proof"]["material_sha256"] = "0" * 64
    elif fault == "matrix_missing":
        after["native_mask_proof"]["target_contacts"].pop()
    elif fault == "cleanup_missing":
        after["native_mask_proof"]["cleanup"].pop()
    else:
        after["all_pin_pad_material"][0]["material"]["pads"][0]["bounds_mm"][0] += 0.001
    assert not R.compare(plan, after)["valid"]


def test_design_identity_excludes_only_recomputed_mask_receipt():
    plan, after = native_mask_case()
    same = copy.deepcopy(after)
    same["native_mask_proof"]["temporary_note"] = "another observation's allocation receipt"
    assert R.design_state(after) == R.design_state(same)
    assert R._states_equal(after, same, plan["request"])
    same["native_mask_proof"]["material_sha256"] = "0" * 64
    assert R.design_state(after) == R.design_state(same)
    assert not R._states_equal(after, same, plan["request"])
    same = copy.deepcopy(after)
    same["native_trace_material"][0]["width_mm"] += 0.000000001
    assert R.design_state(after) != R.design_state(same)


@pytest.mark.parametrize(
    "fault", [None, "raw_width", "raw_pad", "position", "request", "read_failed"]
)
def test_fresh_before_save_reader_reuses_only_exact_native_mask_inputs(monkeypatch, fault):
    from types import SimpleNamespace as NS

    from test_unroute_native import Collection

    from xpedition_cli import native_pad_mask as M

    plan, state = native_mask_case()
    raw_traces = copy.deepcopy(state["native_trace_material"])
    components = []
    for row in state["all_pin_pad_material"]:
        x, y = row["center_mm"]
        pin = NS(
            Name=row["pin"],
            UniqueId=row["pin_uid"],
            Net=NS(Name=row["net"], UniqueId=row["net_uid"]),
            GetPositionX=lambda unit, x=x: x,
            GetPositionY=lambda unit, y=y: y,
            material=copy.deepcopy(row["material"]),
        )
        components.append(
            NS(UniqueId=row["component_uid"], RefDes=row["refdes"], Pins=Collection([pin]))
        )
    doc = NS(CurrentUnit=2, FullName="C:/owned/board.pcb", Components=Collection(components))
    monkeypatch.setattr(R.board_trace, "observe", lambda *args, **kwargs: copy.deepcopy(state))
    monkeypatch.setattr(
        R.board_labels,
        "observe",
        lambda *args: {
            "geometry": copy.deepcopy(state["geometry"]),
            "texts": copy.deepcopy(state["texts"]),
        },
    )
    monkeypatch.setattr(M, "observe_pin", lambda pin, layer, adapter: copy.deepcopy(pin.material))
    monkeypatch.setattr(
        M, "evaluate", lambda *args: pytest.fail("Exact raw input reuse must not rerun Mask Engine")
    )

    def raw_reader(*args):
        if fault == "read_failed":
            raise RuntimeError("actual fresh native trace read failed")
        return copy.deepcopy(raw_traces)

    monkeypatch.setattr(M, "_trace_inventory", raw_reader)
    request = copy.deepcopy(plan["request"])
    if fault == "raw_width":
        raw_traces[0]["width_mm"] += 0.000000001
    elif fault == "raw_pad":
        components[0].Pins[0].material["pads"][0]["bounds_mm"][0] += 0.000000001
    elif fault == "position":
        components[0].Pins[0].GetPositionX = lambda unit: 0.000000001
    elif fault == "request":
        request["net"] = "B"
    if fault is None:
        actual = R.observe_for_save(doc, A, request, state)
        assert R.design_state(actual) == R.design_state(state)
        assert actual["native_mask_proof"] == state["native_mask_proof"]
    else:
        with pytest.raises((ValueError, RuntimeError)):
            R.observe_for_save(doc, A, request, state)
