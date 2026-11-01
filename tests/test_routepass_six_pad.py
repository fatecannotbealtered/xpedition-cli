"""Six owned GND members through native-shaped inputs and the public lifecycle.

Registered synthetic mask cells are offline fault controls, not licensed native
T-junction, rule or manufacturing evidence.
"""

import copy
from types import SimpleNamespace as NS

import pytest
from test_native_pad_mask import Collection, cells
from test_native_pad_mask_six import six_pin_material_fixture
from test_routepass_geometry import baseline
from test_routepass_guard import native_case
from test_routepass_sdk import setup_case

from xpedition_cli import board_routepass as R
from xpedition_cli import driver
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import _canonical, digest


def six_pad_case(*, fault=None):
    """Keep eight old foreign traces while observing all actual new mask inputs."""
    doc, raw, request, adapter, engine = six_pin_material_fixture(fault=fault)
    target_native = list(doc.Traces)
    target_rows = copy.deepcopy(raw["routing_objects"])
    foreign = next(row for row in raw["all_pin_pad_material"] if row["net"] != "GND")
    old_native, old_rows = [], []
    for index in range(8):
        width, y = 0.254, 50 + index * 3
        path = [[50, 55], [y, y], [0, 0]]
        engine.paths[(digest(path), width)] = cells(50, y, 56, y + 1)
        extrema = NS(
            IsValid=lambda: True,
            values={1: 50 - width / 2, 2: y - width / 2, 3: 55 + width / 2, 4: y + width / 2},
        )
        uid = f"protected-{index}"
        old_native.append(
            NS(
                UniqueId=uid,
                Net=NS(Name=foreign["net"], UniqueId=foreign["net_uid"]),
                Layer=1,
                Geometry=NS(
                    IsValid=lambda: True, IsArced=lambda: False, values={1: path, 5: width}
                ),
                values={7: extrema},
            )
        )
        shape = {
            "uid": uid,
            "kind": "trace",
            "net": foreign["net"],
            "layer": 1,
            "width": width,
            "path": [list(point) for point in zip(*path, strict=True)],
        }
        old_rows.append({"kind": "trace", "uid": uid, "net": foreign["net"], "geometry": shape})

    before = baseline()
    before["all_pin_pad_material"] = copy.deepcopy(raw["all_pin_pad_material"])
    before["geometry"]["components"] = []
    before["pin_identities"], before["endpoint_pad_material"] = [], []
    for row in before["all_pin_pad_material"]:
        row["center_mm"] = [float(value) for value in row["center_mm"]]
        before["geometry"]["components"].append(
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
        identity = {
            key: row[key] for key in ("component_uid", "refdes", "pin", "pin_uid", "net", "net_uid")
        }
        before["pin_identities"].append(identity)
        before["endpoint_pad_material"].append(
            {**identity, "material": {"complete": False, "pads": [], "holes": [], "unread": []}}
        )
    before["routing_objects"] = copy.deepcopy(old_rows)
    model = before["geometry"]["model"]
    model["traces"] = [_canonical(row["geometry"]) for row in old_rows]
    model["holes"] = [
        [*hole["center_mm"], hole["diameter_mm"]]
        for row in before["all_pin_pad_material"]
        for hole in row["material"]["holes"]
    ]
    nets = copy.deepcopy(raw["geometry"]["model"]["connectivity"]["nets"])
    for row in nets:
        row.update(opens=5 if row["net"] == "GND" else 0, routed=row["net"] != "GND")
    model["connectivity"]["nets"] = nets
    model["unrouted"] = [copy.deepcopy(row) for row in nets if row["opens"]]
    doc.Traces = Collection(old_native)
    before["native_mask_proof"] = M.evaluate(doc, before, request, adapter)
    before["native_mask_proof"]["request"] = copy.deepcopy(request)
    before["native_trace_material"] = before["native_mask_proof"]["raw_trace_inventory"]
    assert M.verify_evidence(before, request)["valid"], before["native_mask_proof"]
    plan = R.bind(before, request, [], A)
    after = copy.deepcopy(before)
    after["routing_objects"] += target_rows
    after["geometry"]["model"]["traces"] += [row["geometry"] for row in target_rows]
    for row in after["geometry"]["model"]["connectivity"]["nets"]:
        if row["net"] == "GND":
            row.update(opens=0, routed=True)
    after["geometry"]["model"]["unrouted"] = []
    doc.Traces = Collection([*old_native, *target_native])
    after["native_mask_proof"] = M.evaluate(doc, after, request, adapter)
    after["native_mask_proof"]["request"] = copy.deepcopy(request)
    after["native_trace_material"] = after["native_mask_proof"]["raw_trace_inventory"]
    return plan, after


def test_six_ground_members_use_complete_native_material_graph_and_preserve_eight_old_traces():
    plan, after = six_pad_case()
    contract = plan["connection_contract"]
    assert contract["member_count"] == 6
    assert contract["native_opens_before"] == 5 and contract["native_opens_after"] == 0
    assert contract["physical_basis"] == "complete-native-material-graph"
    assert {(row["refdes"], row["pin"]) for row in plan["endpoints"]} == R.SIX_GND_MEMBERS
    assert len(plan["before"]["routing_objects"]) == 8
    proof = R.compare(plan, after)
    assert proof["valid"], proof
    graph = proof["physical_connection"]["material_graph"]
    assert graph["valid"] and graph["all_members_connected"]
    assert sum(row["kind"] == "pin" for row in graph["nodes"]) == 6
    assert proof["physical_connection"]["trace_object_count"] == len(proof["created_objects"])
    assert proof["native_connectivity_verified"] and not proof["drc_executed"]


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
        "unsupported_curve",
    ],
)
def test_false_opens_zero_never_substitutes_for_complete_six_member_material(fault):
    plan, after = six_pad_case(fault=fault)
    proof = R.compare(plan, after)
    assert not proof["valid"], proof
    assert not proof["physical_connection"]["verified"], proof


@pytest.mark.parametrize("fault", ["member", "net", "count", "old_opens", "missing_mask"])
def test_six_pin_scope_cannot_expand_to_an_arbitrary_six_pin_net(fault):
    plan, _ = six_pad_case()
    state, request = copy.deepcopy(plan["before"]), copy.deepcopy(plan["request"])
    if fault == "member":
        state["geometry"]["components"][0]["refdes"] = "J9"
    elif fault == "net":
        request["net"] = "OTHER"
        for key in ("routing_objects", "pin_identities", "all_pin_pad_material"):
            for row in state[key]:
                if row["net"] == "GND":
                    row["net"] = "OTHER"
        for component in state["geometry"]["components"]:
            for pin in component["geometry"]:
                if pin["net"] == "GND":
                    pin["net"] = "OTHER"
        state["geometry"]["model"]["connectivity"]["nets"][0]["net"] = "OTHER"
    elif fault == "count":
        state["geometry"]["components"].pop(0)
    elif fault == "old_opens":
        next(
            row for row in state["geometry"]["model"]["connectivity"]["nets"] if row["net"] == "GND"
        )["opens"] = 4
    else:
        state.pop("native_mask_proof")
    with pytest.raises(A.AdapterError):
        R.bind(state, request, [], A)


@pytest.mark.parametrize("fault", ["missing_member", "member_uid", "member_name", "count", "mode"])
def test_six_member_receipt_recomputes_the_frozen_complete_contract(fault):
    plan, after = six_pad_case()
    if fault == "missing_member":
        plan["endpoints"].pop()
    elif fault == "member_uid":
        plan["endpoints"][-1]["pin_uid"] = "wrong"
    elif fault == "member_name":
        plan["endpoints"][-1]["refdes"] = "C9"
    elif fault == "count":
        plan["connection_contract"]["member_count"] = 3
    else:
        plan["material_verification_mode"] = "supported-straight-contours"
    assert not R.compare(plan, after)["valid"]


@pytest.mark.parametrize(
    "fault", ["old_trace", "old_trace_removed", "text", "component", "rule", "net"]
)
def test_six_target_success_does_not_allow_any_protected_object_change(fault):
    plan, after = six_pad_case()
    if fault == "old_trace":
        after["routing_objects"][7]["geometry"]["width"] += 0.000001
    elif fault == "old_trace_removed":
        after["routing_objects"].pop(7)
    elif fault == "text":
        after["texts"][0]["position"][0] += 0.000001
    elif fault == "component":
        after["geometry"]["components"][0]["refdes"] = "WRONG"
    elif fault == "rule":
        after["rules"]["scheme"] = "different"
    else:
        after["geometry"]["model"]["connectivity"]["nets"][1]["opens"] = 1
    assert not R.compare(plan, after)["valid"]


def native_six_case(tmp_path, monkeypatch, *, fault=None, material_fault=None):
    plan, after = six_pad_case(fault=material_fault)
    app, params, events, identity, net = native_case(tmp_path, monkeypatch, fault=fault)
    app.ActiveDocument.state = copy.deepcopy(plan["before"])
    app.route_state = lambda before: copy.deepcopy(after)
    net.Name, net.UniqueId = "GND", plan["target"]["net_uid"]
    params["routepass_request"] = {"net": "GND", "layer": 1, "effort": 2}
    return app, params, events, identity, net


@pytest.mark.parametrize(
    "fault,material_fault,expected",
    [
        (None, None, (1, 1, 1, 1)),
        ("protected", None, (1, 0, 0, 0)),
        (None, "missing_pin_contact", (1, 0, 0, 0)),
        (None, "two_components", (1, 0, 0, 0)),
        (None, "isolated_copper", (1, 0, 0, 0)),
        ("save_none", None, (1, 1, 0, 0)),
        ("save_false", None, (1, 1, 0, 0)),
        ("save_exception", None, (1, 1, 0, 0)),
        ("close_none", None, (1, 1, 1, 0)),
        ("close_false", None, (1, 1, 1, 0)),
        ("close_exception", None, (1, 1, 1, 0)),
        ("go_reply_loss", None, (1, 0, 0, 0)),
        ("reopen_drift", None, (1, 1, 1, 1)),
    ],
)
def test_six_pin_complete_chain_conditional_save_and_reopen_is_not_the_two_pin_chain(
    tmp_path, monkeypatch, fault, material_fault, expected
):
    app, params, events, *_ = native_six_case(
        tmp_path, monkeypatch, fault=fault, material_fault=material_fault
    )
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    if fault is None and material_fault is None:
        result = R.execute(params, object(), A)
        assert result["persistence_verified"]
        assert R.verify_receipt(result, preview["routepass"], app.ActiveDocument.state)["valid"]
        assert result["verification"]["physical_connection"]["member_count"] == 6
    else:
        with pytest.raises(A.AdapterError) as caught:
            R.execute(params, object(), A)
        result = caught.value.details["native_result"]
        assert not result["persistence_verified"]
    counts = tuple(
        sum(event[0] == name for event in events) for name in ("Go", "Save", "Close", "Open")
    )
    assert counts == expected


@pytest.mark.parametrize("fault", ["save_none", "close_none", "missing_sixth", "split_graph"])
def test_six_receipt_never_accepts_success_booleans_over_contradictory_actual_evidence(
    tmp_path, monkeypatch, fault
):
    app, params, *_ = native_six_case(tmp_path, monkeypatch)
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    result = R.execute(params, object(), A)
    fresh = copy.deepcopy(app.ActiveDocument.state)
    assert result["persistence_verified"]
    if fault in {"save_none", "close_none"}:
        name = "Save" if fault == "save_none" else "Close"
        next(row for row in result["native_calls_returned"] if row["call"] == name)[
            "return_kind"
        ] = "none"
    else:
        _, fresh = six_pad_case(
            fault="missing_pin_contact" if fault == "missing_sixth" else "two_components"
        )
        result["state_reopened"] = fresh
        result["reopen_evidence"]["state_sha256"] = digest(fresh)
    # All producer success flags are still true. The receipt independently
    # recomputes exact calls, both physical comparisons and all six members.
    assert result["persistence_verified"] and result["saved"]
    assert not R.verify_receipt(result, preview["routepass"], fresh)["valid"]


def sdk_six_case(tmp_path, monkeypatch, *, fault=None):
    plan, after = six_pad_case()
    case = setup_case(tmp_path, monkeypatch, fault=fault)
    case.app.ActiveDocument.state = copy.deepcopy(plan["before"])
    case.app.route_state = lambda before: copy.deepcopy(after)
    case.net.Name, case.net.UniqueId = "GND", plan["target"]["net_uid"]
    case.args[case.args.index("--nets") + 1] = "GND"
    case.args += ["--native-effort", "2"]
    return case


@pytest.mark.parametrize("lost_reply", [False, True])
def test_public_six_member_lifecycle_or_unknown_keeps_one_go_and_zero_confirmation_replay(
    tmp_path, monkeypatch, lost_reply
):
    case = sdk_six_case(tmp_path, monkeypatch, fault="go_reply_loss" if lost_reply else None)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        raw = planned.envelope["data"]["preview"]["extensions"]["legacy_preview"]
        assert raw["routepass"]["connection_contract"]["member_count"] == 6
        token = planned.envelope["data"]["confirm_token"]
        done = cli.run([*case.args, "--confirm", token])
        assert (done.exit_code != 0) is lost_reply, done.envelope
        if not lost_reply:
            result = done.envelope["data"]["receipt"]["extensions"]["result"]
            assert result["persistence_verified"]
            assert result["verification"]["physical_connection"]["member_count"] == 6
        events = copy.deepcopy(case.events)
        replay = cli.run([*case.args, "--confirm", token])
        assert replay.exit_code != 0 and case.events == events
        assert sum(event[0] == "Go" for event in events) == 1
        if lost_reply:
            operation_id = planned.envelope["data"]["preview"]["operation_id"]
            status = cli.run(["operation", "show", "--id", operation_id])
            assert status.envelope["data"]["operation_status"]["status"] == "unknown"
            blocked = cli.run([*case.args, "--dry-run"])
            assert blocked.exit_code != 0 and case.events == events
            assert not any(event[0] in {"Save", "Close", "Open"} for event in events)


@pytest.mark.parametrize("fault", ["pin_uid", "material", "old_trace", "rules", "source"])
def test_public_six_member_input_drift_refuses_before_any_dispatch(tmp_path, monkeypatch, fault):
    case = sdk_six_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        state = case.app.ActiveDocument.state
        if fault == "pin_uid":
            state["pin_identities"][0]["pin_uid"] = "drift"
        elif fault == "material":
            state["all_pin_pad_material"][0]["material"]["pads"][0]["bounds_mm"][0] += 0.000001
        elif fault == "old_trace":
            state["routing_objects"][0]["geometry"]["width"] += 0.000001
        elif fault == "rules":
            state["rules"]["scheme"] = "drift"
        else:
            from pathlib import Path

            Path(case.params["source_backup"]).write_bytes(b"input drift")
        refused = cli.run([*case.args, "--confirm", planned.envelope["data"]["confirm_token"]])
        assert refused.exit_code != 0, refused.envelope
        assert not any(event[0] in {"Go", "Save", "Close", "Open"} for event in case.events)
