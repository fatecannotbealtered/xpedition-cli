"""Bounded GND continuation over real-shaped adapter observations.

The native mask replies below are registered synthetic cells; they prove the
acceptance chain and fault handling, not a licensed routing result.
"""

import copy
from types import SimpleNamespace as NS

import pytest
from test_native_pad_mask import Collection, add_graph_trace, array, cells, contour, material
from test_native_pad_mask_six import six_pin_material_fixture
from test_routepass_geometry import baseline
from test_routepass_guard import native_case
from test_routepass_sdk import setup_case

from xpedition_cli import board_routepass as R
from xpedition_cli import driver
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import _canonical, digest


def continuation_case(*, fault=None, baseline_fault=None):
    doc, raw, _, adapter, engine = six_pin_material_fixture()
    request = {"net": "GND", "layer": 1, "effort": 2, "continuation": R.CONTINUATION}
    targets = [row for row in raw["all_pin_pad_material"] if row["net"] == "GND"]
    for row, (x, y) in zip(
        targets, [(0, 0), (10, 0), (0, 20), (10, 20), (20, 0), (20, 20)], strict=True
    ):
        # Narrow the registered native pads as well as their raw bounds. The
        # old y19..22 pad really bridged the y21 zero-area trace/trace case;
        # after supporting real pad conduction that was a legitimate positive,
        # not evidence that zero-area copper contact had been accepted.
        outer = array(x - 0.75, y - 0.75, x + 0.75, y + 0.75)
        engine.filled[digest(outer)] = cells(x, y, x + 1, y + 1)
        row.update(center_mm=[float(x), float(y)], material=material(contour(outer)))
    state = baseline()
    state["all_pin_pad_material"] = copy.deepcopy(raw["all_pin_pad_material"])
    state["geometry"]["components"] = []
    state["pin_identities"], state["endpoint_pad_material"] = [], []
    for row in state["all_pin_pad_material"]:
        identity = {
            key: row[key] for key in ("component_uid", "refdes", "pin", "pin_uid", "net", "net_uid")
        }
        state["pin_identities"].append(identity)
        state["endpoint_pad_material"].append(
            {**identity, "material": {"complete": False, "pads": [], "holes": [], "unread": []}}
        )
        state["geometry"]["components"].append(
            {
                "uid": row["component_uid"],
                "refdes": row["refdes"],
                "geometry": [
                    {
                        "number": row["pin"],
                        "net": row["net"],
                        "center_mm": copy.deepcopy(row["center_mm"]),
                        "holes": copy.deepcopy(row["material"]["holes"]),
                        "copper": [
                            {
                                "layer": 1,
                                "geometry": [
                                    {"bounds_mm": row["material"]["pads"][0]["bounds_mm"]}
                                ],
                            }
                        ],
                    }
                ],
            }
        )
    model = state["geometry"]["model"]
    model["traces"], model["holes"] = [], []
    model["connectivity"]["nets"] = [
        {"net": "GND", "uid": "n1", "opens": 2, "routed": False},
        {"net": "B", "uid": "n2", "opens": 0, "routed": True},
    ]
    model["unrouted"] = [copy.deepcopy(model["connectivity"]["nets"][0])]
    doc.Traces, state["routing_objects"] = Collection(), []

    def trace(uid, x0, y0, x1, y1, *, net="GND", occupied=None):
        points = [[x0, x1], [y0, y1], [0, 0]]
        if occupied is None:
            occupied = cells(min(x0, x1), min(y0, y1), max(x0, x1) + 1, max(y0, y1) + 1)
        add_graph_trace(
            doc,
            state,
            engine,
            uid,
            points,
            occupied,
            [min(x0, x1) - 0.127, min(y0, y1) - 0.127, max(x0, x1) + 0.127, max(y0, y1) + 0.127],
            net=net,
        )
        doc.Traces[-1].Net.UniqueId = "n1" if net == "GND" else "n2"

    for index in range(8):
        trace(f"protected-{index}", 50, 50 + index * 3, 55, 50 + index * 3, net="B")
    trace("1441", 10, 20, 20, 20)
    if baseline_fault == "J3_old_copper":
        trace("1442", 0, 0, 20, 0)
        trace("1443", -5, 20, 0, 20)
    else:
        trace("1442", 0, 0, 10, 0)
        trace("1443", 10, 0, 20, 0)

    def snapshot():
        state["geometry"]["model"]["traces"] = [
            _canonical(row["geometry"]) for row in state["routing_objects"]
        ]
        state["native_mask_proof"] = M.evaluate(doc, state, request, adapter)
        state["native_mask_proof"]["request"] = copy.deepcopy(request)
        state["native_trace_material"] = copy.deepcopy(
            state["native_mask_proof"]["raw_trace_inventory"]
        )
        return copy.deepcopy(state)

    before = snapshot()
    plan = R.bind(before, request, [], A)
    trace("new-1", 20, 0, 20, 20)
    if fault != "missing_J3":
        trace("new-2", 0, 20, 20, 20)
    if fault == "isolated":
        trace("isolated", 70, 10, 80, 10)
    elif fault in {"wrong_net", "wrong_layer"}:
        if fault == "wrong_net":
            doc.Traces[-1].Net = NS(Name="B", UniqueId="n2")
            state["routing_objects"][-1]["net"] = "B"
            state["routing_objects"][-1]["geometry"]["net"] = "B"
        else:
            doc.Traces[-1].Layer = 2
            state["routing_objects"][-1]["geometry"]["layer"] = 2
    elif fault in {"microgap", "zero_area_touch"}:
        occupied = cells(0, 22 if fault == "microgap" else 21, 21, 23)
        engine.paths[(digest(doc.Traces[-1].Geometry.values[1]), 0.254)] = occupied
    elif fault == "hole_only":
        pin = next(row for row in state["all_pin_pad_material"] if row["refdes"] == "J3")
        raw_outer = pin["material"]["pads"][0]["geometry"][0]["outer"]["points_array_mm"]
        engine.filled[digest(raw_outer)] = {(0, 20), (-2, 18)}
        pin["material"]["holes"] = [{"center_mm": [0, 20], "diameter_mm": 1, "plated": True}]
        next(row for row in state["geometry"]["components"] if row["refdes"] == "J3")["geometry"][
            0
        ]["holes"] = copy.deepcopy(pin["material"]["holes"])
        model["holes"] = [[0, 20, 1]]
    elif fault == "unproved_pad_arc":
        # Exercise the real raw-mask -> region-witness failure path. The
        # synthetic engine can retain opaque native XYR material, but this
        # five-point arbitrary arc has no supported connected-region witness.
        pin = next(row for row in state["all_pin_pad_material"] if row["refdes"] == "J3")
        outer = pin["material"]["pads"][0]["geometry"][0]["outer"]
        occupied = copy.deepcopy(engine.filled[digest(outer["points_array_mm"])])
        outer["is_arced"] = True
        outer["points_array_mm"][2] = [0.1, 0, 0, 0, 0]
        engine.filled[digest(outer["points_array_mm"])] = occupied
    elif fault in {"old_deleted_native", "old_renamed_native", "old_width_native"}:
        # Coherent native output: all raw inventory and mask matrices are
        # regenerated after this old-object edit, so identity protection must
        # reject even when physical completion and evidence remain sound.
        index = next(
            index for index, row in enumerate(state["routing_objects"]) if row["uid"] == "1441"
        )
        native, row = doc.Traces[index], state["routing_objects"][index]
        if fault == "old_deleted_native":
            del doc.Traces[index]
            del state["routing_objects"][index]
        elif fault == "old_renamed_native":
            native.UniqueId = row["uid"] = row["geometry"]["uid"] = "replacement-1441"
        else:
            native.Geometry.values[5] = row["geometry"]["width"] = 0.3
            path = native.Geometry.values[1]
            engine.paths[(digest(path), 0.3)] = copy.deepcopy(engine.paths[(digest(path), 0.254)])
    elif fault not in (None, "missing_J3"):
        raise ValueError(fault)
    model["connectivity"]["nets"][0].update(opens=0, routed=True)
    model["unrouted"] = []
    return plan, snapshot()


def test_explicit_existing_component_contract_preserves_all_old_copper_and_connects_all_six():
    plan, after = continuation_case()
    contract = plan["connection_contract"]
    assert contract["mode"] == R.CONTINUATION
    assert contract["native_opens_before"] == 2
    assert contract["old_target_trace_uids"] == ["1441", "1442", "1443"]
    assert len(contract["components_before"]) == 3
    assert len(plan["before"]["routing_objects"]) == 11
    proof = R.compare(plan, after)
    assert proof["valid"], proof
    assert proof["physical_connection"]["trace_object_count"] == 2
    assert proof["physical_connection"]["all_target_trace_object_count"] == 5
    assert proof["physical_connection"]["existing_target_trace_object_count"] == 3


def test_complete_component_observation_is_valid_while_original_graph_is_incomplete():
    plan, _ = continuation_case()
    observed = M.observe_material_components(plan["before"], plan["request"])
    assert observed["valid"] and not observed["all_members_connected"]
    assert len(observed["components"]) == 3
    assert not M.verify_material_graph(plan["before"], plan["request"])["valid"]
    compared = R.compare(plan, plan["before"])
    assert compared["protection_valid"] and not compared["completion_valid"]
    assert compared["physical_connection"]["material_component_observation"]["valid"]


def test_same_pin_groups_do_not_allow_old_copper_at_the_required_isolated_J3():
    with pytest.raises(A.AdapterError, match="exact observed three GND"):
        continuation_case(baseline_fault="J3_old_copper")


@pytest.mark.parametrize("count", [2, 3, 6])
def test_no_flag_two_three_six_empty_contracts_still_refuse_any_old_target_copper(count):
    if count == 2:
        from test_routepass_geometry import plan_case, routed_state

        plan = plan_case()
        after = routed_state(plan["before"])
    elif count == 3:
        from test_routepass_three_pad import three_pad_case

        plan, after = three_pad_case()
    else:
        from test_routepass_six_pad import six_pad_case

        plan, after = six_pad_case()
    with pytest.raises(A.AdapterError, match="Target must have no existing trace or via"):
        R.bind(after, plan["request"], [], A)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_J3",
        "isolated",
        "wrong_net",
        "wrong_layer",
        "microgap",
        "zero_area_touch",
        "hole_only",
    ],
)
def test_completion_checks_every_old_and_new_copper_and_all_six_pins(fault):
    plan, after = continuation_case(fault=fault)
    proof = R.compare(plan, after)
    assert not proof["valid"], proof
    assert not proof["physical_connection"]["verified"], proof


@pytest.mark.parametrize(
    "fault",
    ["modified", "deleted", "replaced_uid", "geometry_uid", "raw_path", "foreign", "only_new"],
)
def test_old_target_copper_identity_and_raw_geometry_are_never_relaxed(fault):
    plan, after = continuation_case()
    if fault in {"deleted", "only_new"}:
        after["routing_objects"] = [
            row
            for row in after["routing_objects"]
            if row["uid"] not in ({"1441"} if fault == "deleted" else {"1441", "1442", "1443"})
        ]
    elif fault == "raw_path":
        next(row for row in after["native_trace_material"] if row["uid"] == "1441")[
            "points_array_mm"
        ][0][0] += 0.000001
    elif fault == "replaced_uid":
        next(row for row in after["routing_objects"] if row["uid"] == "1441")["uid"] = "replacement"
    elif fault == "geometry_uid":
        next(row for row in after["routing_objects"] if row["uid"] == "1441")["geometry"]["uid"] = (
            "replacement"
        )
    elif fault == "foreign":
        after["texts"][0]["position"][0] += 0.000001
    else:
        next(row for row in after["routing_objects"] if row["uid"] == "1441")["geometry"][
            "width"
        ] += 0.000001
    assert not R.compare(plan, after)["valid"]


@pytest.mark.parametrize("fault", ["old_deleted_native", "old_renamed_native", "old_width_native"])
def test_coherent_complete_native_output_still_cannot_replace_delete_or_edit_old_copper(fault):
    plan, after = continuation_case(fault=fault)
    assert M.verify_evidence(after, plan["request"])["valid"]
    proof = R.compare(plan, after)
    assert proof["completion_valid"] and not proof["protection_valid"] and not proof["valid"]
    assert not next(
        row
        for row in proof["checks"]
        if row["name"] == "existing routing identities and exact geometry remain"
    )["passed"]


@pytest.mark.parametrize("fault", ["default", "opens", "group", "old_count", "contract", "member"])
def test_continuation_is_explicit_frozen_and_cannot_widen_the_empty_net_contract(fault):
    plan, after = continuation_case()
    state, request = copy.deepcopy(plan["before"]), copy.deepcopy(plan["request"])
    if fault == "default":
        request.pop("continuation")
    elif fault == "opens":
        state["geometry"]["model"]["connectivity"]["nets"][0]["opens"] = 5
    elif fault == "old_count":
        state["routing_objects"].pop()
    elif fault == "group":
        # Real observer must reject a missing/contact-altered complete matrix.
        next(
            row
            for row in state["native_mask_proof"]["target_contacts"]
            if row["positive_intersection_area_mm2"] > 0
        )["positive_intersection_area_mm2"] = 0
    elif fault == "member":
        state["geometry"]["components"][0]["refdes"] = "J9"
    else:
        plan["connection_contract"]["old_target_trace_uids"][0] = "forged"
        assert not R.compare(plan, after)["valid"]
        return
    with pytest.raises(A.AdapterError):
        R.bind(state, request, [], A)


def native_continuation_case(tmp_path, monkeypatch, *, fault=None, material_fault=None):
    plan, after = continuation_case(fault=material_fault)
    app, params, events, identity, net = native_case(tmp_path, monkeypatch, fault=fault)
    app.ActiveDocument.state = copy.deepcopy(plan["before"])
    app.route_state = lambda before: copy.deepcopy(after)
    net.Name, net.UniqueId = "GND", plan["target"]["net_uid"]
    params["routepass_request"] = copy.deepcopy(plan["request"])
    return app, params, events, identity, net


@pytest.mark.parametrize(
    "fault,material_fault,expected",
    [
        (None, None, (1, 1, 1, 1)),
        (None, "missing_J3", (1, 0, 0, 0)),
        ("protected", None, (1, 0, 0, 0)),
        ("save_none", None, (1, 1, 0, 0)),
        ("save_false", None, (1, 1, 0, 0)),
        ("save_exception", None, (1, 1, 0, 0)),
        ("close_none", None, (1, 1, 1, 0)),
        ("close_false", None, (1, 1, 1, 0)),
        ("go_reply_loss", None, (1, 0, 0, 0)),
        ("reopen_drift", None, (1, 1, 1, 1)),
    ],
)
def test_existing_component_lifecycle_has_one_go_and_strict_conditional_persistence(
    tmp_path, monkeypatch, fault, material_fault, expected
):
    app, params, events, *_ = native_continuation_case(
        tmp_path, monkeypatch, fault=fault, material_fault=material_fault
    )
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    if fault is None and material_fault is None:
        result = R.execute(params, object(), A)
        assert result["persistence_verified"]
        assert R.verify_receipt(result, preview["routepass"], app.ActiveDocument.state)["valid"]
    else:
        with pytest.raises(A.AdapterError) as caught:
            R.execute(params, object(), A)
        assert not caught.value.details["native_result"]["persistence_verified"]
    assert (
        tuple(sum(event[0] == name for event in events) for name in ("Go", "Save", "Close", "Open"))
        == expected
    )


@pytest.mark.parametrize(
    "fault",
    ["missing_consumption", "changed_old_uid", "forged_components", "save_none", "close_none"],
)
def test_complete_receipt_flags_cannot_replace_recomputed_continuation_evidence(
    tmp_path, monkeypatch, fault
):
    app, params, *_ = native_continuation_case(tmp_path, monkeypatch)
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    result = R.execute(params, object(), A)
    fresh = copy.deepcopy(app.ActiveDocument.state)
    if fault == "missing_consumption":
        fresh = copy.deepcopy(preview["routepass"]["before"])
        result["state_reopened"] = fresh
        result["reopen_evidence"]["state_sha256"] = digest(fresh)
    elif fault == "changed_old_uid":
        next(row for row in fresh["routing_objects"] if row["uid"] == "1441")["uid"] = "replacement"
        result["state_reopened"] = fresh
        result["reopen_evidence"]["state_sha256"] = digest(fresh)
    elif fault == "forged_components":
        preview["routepass"]["connection_contract"]["components_before"].pop()
    else:
        name = "Save" if fault == "save_none" else "Close"
        next(row for row in result["native_calls_returned"] if row["call"] == name)[
            "return_kind"
        ] = "none"
    assert result["persistence_verified"] and result["saved"]
    assert not R.verify_receipt(result, preview["routepass"], fresh)["valid"]


def sdk_continuation_case(tmp_path, monkeypatch, *, fault=None):
    plan, after = continuation_case()
    case = setup_case(tmp_path, monkeypatch, fault=fault)
    case.app.ActiveDocument.state = copy.deepcopy(plan["before"])
    case.app.route_state = lambda before: copy.deepcopy(after)
    case.net.Name, case.net.UniqueId = "GND", plan["target"]["net_uid"]
    case.args[case.args.index("--nets") + 1] = "GND"
    case.args += ["--native-effort", "2", "--continue-existing"]
    return case


@pytest.mark.parametrize("lost_reply", [False, True])
def test_public_continuation_confirm_or_unknown_has_zero_confirmation_replay(
    tmp_path, monkeypatch, lost_reply
):
    case = sdk_continuation_case(
        tmp_path, monkeypatch, fault="go_reply_loss" if lost_reply else None
    )
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        token = planned.envelope["data"]["confirm_token"]
        done = cli.run([*case.args, "--confirm", token])
        assert (done.exit_code != 0) is lost_reply, done.envelope
        events = copy.deepcopy(case.events)
        assert cli.run([*case.args, "--confirm", token]).exit_code != 0
        assert case.events == events and sum(event[0] == "Go" for event in events) == 1
        if lost_reply:
            assert cli.run([*case.args, "--dry-run"]).exit_code != 0
            assert case.events == events


@pytest.mark.parametrize("fault", ["source", "components", "old_trace", "pin_uid", "rules"])
def test_public_continuation_drift_stops_before_any_dispatch(tmp_path, monkeypatch, fault):
    case = sdk_continuation_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        state = case.app.ActiveDocument.state
        if fault == "source":
            from pathlib import Path

            Path(case.params["source_backup"]).write_bytes(b"source drift")
        elif fault == "components":
            next(
                row
                for row in state["native_mask_proof"]["target_trace_intersections"]
                if row["intersection_area_mm2"] > 0
            )["intersection_area_mm2"] = 0
        elif fault == "old_trace":
            state["routing_objects"][-1]["geometry"]["width"] += 0.000001
        elif fault == "pin_uid":
            state["pin_identities"][0]["pin_uid"] = "drift"
        else:
            state["rules"]["scheme"] = "drift"
        assert (
            cli.run([*case.args, "--confirm", planned.envelope["data"]["confirm_token"]]).exit_code
            != 0
        )
        assert not any(event[0] in {"Go", "Save", "Close", "Open"} for event in case.events)
