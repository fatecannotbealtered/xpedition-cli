"""Generated component tasks with synthetic mask receipts, never native CAD.

The existing fake mask engine returns explicitly registered cell sets. Those
sets exercise the captured raw-input verifier and pure contracts; they are not
licensed geometry, routing, rules or persistence evidence.
"""

import copy
from types import SimpleNamespace as NS

import pytest
from test_native_pad_mask import add_graph_trace, array, cells, contour, fixture, material

from xpedition_cli import board_components as C
from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import digest

BOARD = "D:/synthetic-only/generated-layout.pcb"


def task(
    *,
    count=4,
    name="BUS_RETURN",
    net_uid="actual-net-47",
    offset=(0, 0),
    pure=False,
    same_pin=False,
):
    doc, state, _, adapter, engine = fixture(traces=False, foreign=True)
    state["all_pin_pad_material"] = [
        row for row in state["all_pin_pad_material"] if row["net"] == "B"
    ]
    x0, y0 = offset
    if not pure:
        for index in range(count):
            x, y = x0 + index * 6, y0
            raw = array(x - 0.75, y - 0.75, x + 0.75, y + 0.75)
            engine.filled[digest(raw)] = cells(x, y, x + 1, y + 1)
            state["all_pin_pad_material"].append(
                {
                    "component_uid": "owner-" + str(index),
                    "pin_uid": "pad-owner-" + str(index),
                    "refdes": "P" + str(index + 17),
                    "pin": str(index + 2),
                    "center_mm": [x, y],
                    "net": name,
                    "net_uid": net_uid,
                    "material": material(contour(raw)),
                }
            )
    if same_pin:
        first, second = state["all_pin_pad_material"][1:3]
        first["material"]["pads"].extend(copy.deepcopy(second["material"]["pads"]))
        state["all_pin_pad_material"].remove(second)
    nets = [
        {"net": name, "uid": net_uid, "opens": count - 1, "routed": False},
        {"net": "B", "uid": "n2", "opens": 0, "routed": True},
    ]
    model = state["geometry"]["model"]
    model.update(
        layers=2,
        traces=[],
        vias=[],
        planes=[],
        holes=[],
        outline=[[x0 - 3, y0 - 3], [x0 + 60, y0 + 60]],
        unrouted=[copy.deepcopy(nets[0])],
        connectivity={"complete": True, "nets": nets},
    )
    state["geometry"]["stackup"] = [
        {"kind": "conductor", "layer": layer, "usage": "signal"} for layer in (1, 2)
    ]
    state["geometry"]["components"] = [
        {
            "uid": row["component_uid"],
            "refdes": row["refdes"],
            "geometry": [{"number": row["pin"], "net": row["net"], "center_mm": row["center_mm"]}],
        }
        for row in state["all_pin_pad_material"]
    ]
    fields = ("component_uid", "pin_uid", "refdes", "pin", "net", "net_uid")
    state["pin_identities"] = [
        {key: row[key] for key in fields} for row in state["all_pin_pad_material"]
    ]
    state["plane_inventory"] = {"complete": True, "shapes": [], "generated": [], "assignments": []}
    state["rules"] = {"scheme": "(Master)", "local_applicability_verified": False}
    state["texts"] = [{"uid": "protected-label", "text": "fixed-label", "position": [70, 70]}]
    request = {"net": name, "net_uid": net_uid, "layer": 1}

    def trace(uid, points, *, net=name, owner=net_uid, width=0.254):
        raw = [[point[0] for point in points], [point[1] for point in points], [0.0] * len(points)]
        min_x, max_x = min(raw[0]), max(raw[0])
        min_y, max_y = min(raw[1]), max(raw[1])
        occupied = cells(min_x, min_y, max_x + 1, max_y + 1)
        add_graph_trace(
            doc,
            state,
            engine,
            uid,
            raw,
            occupied,
            [min_x - width / 2, min_y - width / 2, max_x + width / 2, max_y + width / 2],
            net=net,
        )
        doc.Traces[-1].Net = NS(Name=net, UniqueId=owner)
        if width != 0.254:
            engine.paths[(digest(raw), width)] = occupied
            doc.Traces[-1].Geometry.values[5] = width
            state["routing_objects"][-1]["geometry"]["width"] = width

    trace("protected-other-net", [[50, 40], [55, 40]], net="B", owner="n2")
    if pure:
        for index in range(count):
            trace("island-" + str(index), [[x0 + index * 10, y0], [x0 + index * 10 + 4, y0]])

    def observe(mutate=None):
        if mutate is not None:
            mutate(doc, engine)
        model["traces"] = [copy.deepcopy(row["geometry"]) for row in state["routing_objects"]]
        state["native_mask_proof"] = M.evaluate_components(doc, state, request, adapter)
        state["native_trace_material"] = copy.deepcopy(
            state["native_mask_proof"]["raw_trace_inventory"]
        )
        observed = M.observe_components(state, request)
        assert observed["valid"], observed
        return copy.deepcopy(state), observed

    return state, trace, observe, request


def snapshot(state, observation, request):
    value = C.build_snapshot(
        state, observation, board_id=BOARD, net_id=request["net_uid"], layer=request["layer"]
    )
    assert value["valid"], value
    return value


def connect_task(*, count=4, pure=False, same_pin=False, name="BUS_RETURN", offset=(0, 0)):
    state, trace, observe, request = task(
        count=count, pure=pure, same_pin=same_pin, name=name, offset=offset
    )
    before_state, observed = observe()
    before = snapshot(before_state, observed, request)
    if pure:
        first, second = "trace:island-0", "trace:island-1"
        points = [[offset[0] + 4, offset[1]], [offset[0] + 10, offset[1]]]
    else:
        nodes = sorted(
            (row for row in before["nodes"] if row["kind"] == "pin"),
            key=lambda row: (row["component_uid"], row["region_index"]),
        )
        first, second = nodes[0]["node_id"], nodes[1]["node_id"]
        points = [[offset[0], offset[1]], [offset[0] + 6, offset[1]]]
    plan = C.plan_connection(before, first=first, second=second, points=points, width_mm=0.254)
    assert plan["structural_valid"] and not plan["no_op"], plan
    trace("new-connection", points)
    target = state["geometry"]["model"]["connectivity"]["nets"][0]
    target.update(opens=max(0, target["opens"] - 1), routed=count == 2)
    state["geometry"]["model"]["unrouted"] = [copy.deepcopy(target)] if target["opens"] else []
    after_state, after_observed = observe()
    return before, plan, after_state, after_observed, state, trace, observe


@pytest.mark.parametrize(
    "count,name,offset",
    [
        (2, "DATA_3", (0, 0)),
        (4, "RETURN", (-20, 10)),
        (5, "shield", (7, -9)),
        (7, "任意网络", (4, 5)),
        (9, "RX_PAIR_N", (-6, -4)),
        (4, "0", (3, 9)),
    ],
)
def test_generated_arbitrary_pin_tasks_accept_only_one_component_merge(count, name, offset):
    before, plan, after, observed, *_ = connect_task(count=count, name=name, offset=offset)
    proof = C.verify_connection(before, after, observed, plan=plan, board_id=BOARD)
    assert proof["valid"], proof
    assert proof["components_before"] == count
    assert proof["components_after"] == count - 1
    assert proof["all_members_connected"] is (count == 2)
    assert proof["native_consistent"] and proof["requested_geometry_matches"]
    assert proof["created_trace_uids"] == ["new-connection"]
    assert not proof["drc_executed"] and not proof["persistence_verified"]
    assert "can_apply" not in plan
    assert plan["blockers"][0]["code"] == "E_EFFECTIVE_PATH_RULES_NOT_ASSESSED"


@pytest.mark.parametrize("count", [2, 4, 7])
def test_pure_copper_islands_are_nodes_and_need_no_pin_count(count):
    before, plan, after, observed, *_ = connect_task(count=count, pure=True)
    assert all(row["kind"] == "trace" for row in before["nodes"])
    proof = C.verify_connection(before, after, observed, plan=plan, board_id=BOARD)
    assert proof["valid"] and proof["components_after"] == count - 1, proof


def test_copper_pad_endpoint_and_trace_middle_material_contacts_preserve_old_uids():
    state, trace, observe, request = task(count=4)
    trace("old-pad-stub", [[0, 0], [3, 0]])
    raw, observation = observe()
    before = snapshot(raw, observation, request)
    pad = next(row for row in before["nodes"] if row.get("pin_uid") == "pad-owner-1")
    plan = C.plan_connection(
        before,
        first="trace:old-pad-stub",
        second=pad["node_id"],
        points=[[2, 0], [6, 0]],
        width_mm=0.254,
    )
    trace("new-connection", [[2, 0], [6, 0]])
    state["geometry"]["model"]["connectivity"]["nets"][0]["opens"] -= 1
    after, observation = observe()
    proof = C.verify_connection(before, after, observation, plan=plan, board_id=BOARD)
    assert proof["valid"] and proof["requested_geometry_matches"], proof
    assert proof["components_before"] == 4 and proof["components_after"] == 3


@pytest.mark.parametrize("field", ["uid", "width", "path"])
def test_coherent_new_receipts_do_not_hide_old_uid_or_subcanonical_raw_drift(field):
    before, plan, _, _, state, _, observe = connect_task(pure=True)

    def edit_native(doc, engine):
        obj = next(row for row in doc.Traces if row.UniqueId == "island-0")
        routing = next(row for row in state["routing_objects"] if row["uid"] == "island-0")
        raw, width = obj.Geometry.values[1], obj.Geometry.values[5]
        occupied = copy.deepcopy(engine.paths[(digest(raw), width)])
        if field == "uid":
            obj.UniqueId = routing["uid"] = "replacement-island"
            routing["geometry"]["uid"] = obj.UniqueId
        elif field == "width":
            obj.Geometry.values[5] = width + 1e-10
            engine.paths[(digest(raw), width + 1e-10)] = occupied
        else:
            raw = copy.deepcopy(raw)
            raw[0][0] += 1e-10
            obj.Geometry.values[1] = raw
            engine.paths[(digest(raw), width)] = occupied
        # The complete canonical observation is unchanged at eight decimals for
        # width/path faults. The fresh raw mm observation must still protect it.

    after, observation = observe(edit_native)
    assert observation["valid"]
    proof = C.verify_connection(before, after, observation, plan=plan, board_id=BOARD)
    assert not proof["valid"] and "all old raw trace" in proof["reason"], proof


def test_same_pin_disjoint_regions_remain_distinct_and_can_be_explicitly_selected():
    before, plan, after, observed, *_ = connect_task(count=2, same_pin=True)
    assert len({row["pin_uid"] for row in before["nodes"]}) == 1
    assert len(before["components"]) == 2
    # Native pin accounting need not count two disconnected regions separately.
    before["native_connectivity"]["opens"] = 0
    before["snapshot_data_sha256"] = digest(
        {key: value for key, value in before.items() if key != "snapshot_data_sha256"}
    )
    plan = C.plan_connection(
        before,
        first=plan["first"],
        second=plan["second"],
        points=plan["points"],
        width_mm=plan["width_mm"],
    )
    proof = C.verify_connection(before, after, observed, plan=plan, board_id=BOARD)
    assert proof["valid"] and not proof["native_consistent"], proof


def test_same_component_selection_is_no_op_without_path_or_rule_authorization():
    before, _, after, observed, *_ = connect_task(count=2)
    connected = C.build_snapshot(
        after, observed, board_id=BOARD, net_id=before["target"]["net_uid"], layer=1
    )
    plan = C.plan_connection(
        connected,
        first=connected["components"][0]["component_id"],
        second=connected["nodes"][0]["node_id"],
        points=None,
        width_mm=None,
    )
    assert plan["no_op"] and plan["structural_valid"] and not plan["blockers"]
    proof = C.verify_connection(connected, after, observed, plan=plan, board_id=BOARD)
    assert proof["valid"] and not proof["created_trace_uids"], proof


@pytest.mark.parametrize("fault", ["extra_merge", "detached_new"])
def test_component_partition_rejects_unselected_merge_or_detached_copper(fault):
    before, plan, _, _, state, trace, observe = connect_task(count=4)
    if fault == "extra_merge":
        trace("unselected-bridge", [[6, 0], [12, 0]])
    elif fault == "detached_new":
        trace("detached", [[70, 0], [74, 0]])
    after, observed = observe()
    proof = C.verify_connection(before, after, observed, plan=plan, board_id=BOARD)
    assert not proof["valid"] and "only the selected two" in proof["reason"], proof


def test_exact_node_selection_does_not_mean_any_node_in_the_same_component():
    state, trace, observe, request = task(count=3, pure=True)
    trace("left-extension", [[-4, 0], [0, 0]])
    raw, observation = observe()
    before = snapshot(raw, observation, request)
    plan = C.plan_connection(
        before,
        first="trace:left-extension",
        second="trace:island-1",
        points=[[4, 0], [10, 0]],
        width_mm=0.254,
    )
    trace("new-connection", [[4, 0], [10, 0]])
    state["geometry"]["model"]["connectivity"]["nets"][0]["opens"] -= 1
    after, observation = observe()
    proof = C.verify_connection(before, after, observation, plan=plan, board_id=BOARD)
    assert not proof["valid"] and "selected exact material node" in proof["reason"], proof


@pytest.mark.parametrize("field", ["uid", "width_mm", "points_array_mm", "layer", "net_uid"])
def test_any_old_raw_trace_drift_is_rejected_even_below_canonical_rounding(field):
    before, plan, after, observed, *_ = connect_task(pure=True)
    raw = next(row for row in after["native_trace_material"] if row["uid"] == "island-0")
    if field == "uid":
        raw[field] = "replacement"
    elif field == "points_array_mm":
        raw[field][0][0] += 1e-10
    elif field == "width_mm":
        raw[field] += 1e-10
    else:
        raw[field] = 2 if field == "layer" else "another-net"
    proof = C.verify_connection(before, after, observed, plan=plan, board_id=BOARD)
    assert not proof["valid"], proof


@pytest.mark.parametrize(
    "fault", ["text", "rule", "foreign_connectivity", "foreign_pad", "old_deleted", "via", "plane"]
)
def test_non_target_and_old_copper_protection_is_strict(fault):
    before, plan, after, observed, *_ = connect_task()
    if fault == "text":
        after["texts"][0]["position"][0] += 1
    elif fault == "rule":
        after["rules"]["scheme"] = "changed"
    elif fault == "foreign_connectivity":
        after["geometry"]["model"]["connectivity"]["nets"][1]["opens"] = 1
    elif fault == "foreign_pad":
        after["all_pin_pad_material"][0]["material"]["pads"][0]["bounds_mm"][0] -= 1e-10
    elif fault == "old_deleted":
        after["routing_objects"] = [
            row for row in after["routing_objects"] if row["uid"] != "protected-other-net"
        ]
    elif fault == "via":
        after["routing_objects"].append(
            {"kind": "via", "uid": "new-via", "net": before["target"]["net"], "geometry": {}}
        )
    else:
        after["plane_inventory"]["generated"].append({"net": before["target"]["net"], "layer": 1})
    proof = C.verify_connection(before, after, observed, plan=plan, board_id=BOARD)
    assert not proof["valid"], proof


@pytest.mark.parametrize(
    "points,width",
    [
        ([[0, 0], [0, 0]], 0.254),
        ([[0, 0], [6, 0], [2, 0]], 0.254),
        ([[0, 0], [6, 6], [0, 6], [6, 0]], 0.254),
        ([[0, 0], [6, 0]], 0),
        ([[0, 0], [float("nan"), 0]], 0.254),
        ([[0, 0, 0], [6, 0, 0]], 0.254),
    ],
)
def test_invalid_explicit_paths_do_not_make_structurally_valid_proposals(points, width):
    state, _, observe, request = task()
    state, material_observation = observe()
    before = snapshot(state, material_observation, request)
    proposal = C.plan_connection(
        before,
        first=before["components"][0]["component_id"],
        second=before["components"][1]["component_id"],
        points=points,
        width_mm=width,
    )
    assert not proposal["structural_valid"] and proposal["blockers"], proposal


def test_material_ids_ignore_temporary_lifecycle_receipts_but_keep_raw_precision():
    state, _, observe, request = task()
    state, observation = observe()
    before = snapshot(state, observation, request)
    altered = copy.deepcopy(state)
    altered["native_mask_proof"]["observed_at"] = "another diagnostic time"
    # This field is not a geometry/source matrix or a lifecycle integrity input.
    later_observation = M.observe_components(altered, request)
    later = snapshot(altered, later_observation, request)
    assert before["snapshot_id"] == later["snapshot_id"]
    assert before["components"] == later["components"]
    assert before["source_state_sha256"] != later["source_state_sha256"]


def test_forged_partition_stale_ids_and_board_identity_are_rejected():
    state, _, observe, request = task()
    state, observation = observe()
    before = snapshot(state, observation, request)
    forged = copy.deepcopy(observation)
    forged["components"][0]["node_ids"].extend(forged["components"].pop()["node_ids"])
    result = C.build_snapshot(state, forged, board_id=BOARD, net_id=request["net_uid"], layer=1)
    assert not result["valid"]
    proposal = C.plan_connection(
        before,
        first="component:stale",
        second=before["nodes"][0]["node_id"],
        points=[[0, 0], [6, 0]],
        width_mm=0.254,
    )
    assert not proposal["structural_valid"]
    before, plan, after, observed, *_ = connect_task()
    proof = C.verify_connection(
        before, after, observed, plan=plan, board_id="D:/synthetic-only/other-layout.pcb"
    )
    assert not proof["valid"]


def test_geometry_and_native_accounting_are_separate_from_material_partition_acceptance():
    before, plan, after, observed, *_ = connect_task()
    other_plan = C.plan_connection(
        before,
        first=plan["first"],
        second=plan["second"],
        points=[[0, 0], [3, 2], [6, 0]],
        width_mm=0.3,
    )
    proof = C.verify_connection(before, after, observed, plan=other_plan, board_id=BOARD)
    assert proof["valid"] and not proof["requested_geometry_matches"], proof
    assert proof["scope"] == "physical_partition_and_protection_only"


def test_client_style_rule_booleans_cannot_supply_effective_path_evidence():
    state, _, observe, request = task()
    state, observation = observe()
    state["rules"]["local_applicability_verified"] = True
    before = snapshot(state, observation, request)
    plan = C.plan_connection(
        before,
        first=before["nodes"][0]["node_id"],
        second=before["nodes"][1]["node_id"],
        points=[[0, 0], [6, 0]],
        width_mm=0.254,
    )
    assert plan["blockers"][0]["code"] == "E_EFFECTIVE_PATH_RULES_NOT_ASSESSED"
