"""Three actual members through the raw native-mask adaptation and lifecycle.

These are registered synthetic mask cells, not native CAD fault injection.
"""

import copy
from types import SimpleNamespace as NS

import pytest
from test_native_pad_mask import Collection, array, cells, contour, fixture, material
from test_routepass_geometry import baseline
from test_routepass_guard import native_case
from test_routepass_sdk import setup_case

from xpedition_cli import board_routepass as R
from xpedition_cli import driver
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import _canonical, digest


def three_pad_case(*, fault=None, round_hole=False):
    """Actual observer-shaped input with middle T contact inside the first trace."""
    doc, raw, request, adapter, engine = fixture(traces=False, foreign=True, hole=round_hole)
    third = array(3, 8, 8, 13)
    engine.filled[digest(third)] = cells(3, 8, 8, 13)
    raw["all_pin_pad_material"].append(
        {
            "component_uid": "c4",
            "pin_uid": "p4",
            "refdes": "C1",
            "pin": "1",
            "center_mm": [5.0, 10.0],
            "net": "A",
            "net_uid": "n1",
            "material": material(contour(third)),
        }
    )
    before = baseline()
    before["all_pin_pad_material"] = copy.deepcopy(raw["all_pin_pad_material"])
    before["geometry"]["components"] = []
    before["pin_identities"] = []
    before["endpoint_pad_material"] = []
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
                        "holes": [],
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
    before["routing_objects"] = []
    before["geometry"]["model"]["holes"] = [[0.0, 0.0, 1.0]] if round_hole else []
    before["geometry"]["model"]["traces"] = []
    nets = [
        {"net": "A", "uid": "n1", "opens": 2, "routed": False},
        {"net": "B", "uid": "n2", "opens": 0, "routed": True},
    ]
    before["geometry"]["model"]["connectivity"]["nets"] = nets
    before["geometry"]["model"]["unrouted"] = [copy.deepcopy(nets[0])]
    before["native_mask_proof"] = M.evaluate(doc, before, request, adapter)
    before["native_mask_proof"]["request"] = copy.deepcopy(request)
    before["native_trace_material"] = before["native_mask_proof"]["raw_trace_inventory"]
    assert M.verify_evidence(before, request)["valid"], before["native_mask_proof"]
    plan = R.bind(before, request, [], A)
    after = copy.deepcopy(before)
    paths = [
        ([[0, 10], [0, 0], [0, 0]], cells(0, 0, 11, 1)),
        ([[5, 5], [0, 10], [0, 0]], cells(5, 0, 6, 11)),
    ]
    if fault == "gap":
        paths[1] = ([[5, 5], [2, 10], [0, 0]], cells(5, 2, 6, 11))
    elif fault == "missing_third":
        paths.pop()
    elif fault == "isolated":
        paths.append(([[20, 21], [20, 20], [0, 0]], cells(20, 20, 22, 21)))
    traces = []
    for index, (path, occupied) in enumerate(paths):
        width = 0.254
        engine.paths[(digest(path), width)] = occupied
        uid = f"t{index + 1}"
        shape = {
            "uid": uid,
            "kind": "trace",
            "net": "A",
            "layer": 1,
            "width": width,
            "path": [list(point) for point in zip(*path, strict=True)],
        }
        extrema = NS(
            IsValid=lambda: True,
            values={
                1: min(path[0]) - width / 2,
                2: min(path[1]) - width / 2,
                3: max(path[0]) + width / 2,
                4: max(path[1]) + width / 2,
            },
        )
        traces.append(
            NS(
                UniqueId=uid,
                Net=NS(Name="A", UniqueId="n1"),
                Layer=1,
                Geometry=NS(
                    IsValid=lambda: True, IsArced=lambda: False, values={1: path, 5: width}
                ),
                values={7: extrema},
            )
        )
        after["routing_objects"].append(
            {"kind": "trace", "uid": uid, "net": "A", "geometry": _canonical(shape)}
        )
        after["geometry"]["model"]["traces"].append(_canonical(shape))
    doc.Traces = Collection(traces)
    after["geometry"]["model"]["connectivity"]["nets"][0].update(opens=0, routed=True)
    after["geometry"]["model"]["unrouted"] = []
    after["native_mask_proof"] = M.evaluate(doc, after, request, adapter)
    after["native_mask_proof"]["request"] = copy.deepcopy(request)
    after["native_trace_material"] = after["native_mask_proof"]["raw_trace_inventory"]
    assert M.verify_evidence(after, request)["valid"], after["native_mask_proof"]
    return plan, after


def test_three_owned_pins_freeze_complete_members_and_native_middle_t_material():
    plan, after = three_pad_case()
    assert plan["connection_contract"]["member_count"] == 3
    assert plan["connection_contract"]["native_opens_before"] == 2
    assert {row["pin_uid"] for row in plan["endpoints"]} == {"p1", "p2", "p4"}
    # The third branch starts at (5,0), inside t1 rather than either t1 end.
    proof = R.compare(plan, after)
    assert proof["valid"], proof
    assert proof["physical_connection"]["material_graph"]["valid"]
    assert proof["physical_connection"]["trace_object_count"] == 2
    assert proof["native_connectivity_verified"]
    assert not proof["drc_executed"]


def test_complete_owned_round_hole_inventory_is_bound_and_trace_material_is_clipped():
    plan, after = three_pad_case(round_hole=True)
    proof = R.compare(plan, after)
    assert proof["valid"], proof
    assert plan["before"]["geometry"]["model"]["holes"] == [[0.0, 0.0, 1.0]]


@pytest.mark.parametrize(
    "fault",
    [
        "extra_board_hole",
        "missing_board_hole",
        "wrong_diameter",
        "missing_owned_hole",
        "slot",
        "nonfinite",
    ],
)
def test_preflight_board_holes_must_equal_complete_owned_round_hole_inventory(fault):
    plan, _ = three_pad_case(round_hole=True)
    state = copy.deepcopy(plan["before"])
    holes = state["geometry"]["model"]["holes"]
    if fault == "extra_board_hole":
        holes.append([20, 20, 1])
    elif fault == "missing_board_hole":
        holes.clear()
    elif fault == "wrong_diameter":
        holes[0][2] = 1.000001
    elif fault == "missing_owned_hole":
        state["all_pin_pad_material"][0]["material"]["holes"].clear()
    elif fault == "slot":
        state["geometry"]["model"]["slots"] = [{"path": [[0, 0], [1, 0]]}]
    else:
        holes[0][0] = float("nan")
    with pytest.raises(A.AdapterError) as caught:
        R.bind(state, plan["request"], [], A)
    assert "hole" in str(caught.value).lower()


@pytest.mark.parametrize("fault", ["gap", "missing_third", "isolated"])
def test_opens_zero_cannot_substitute_for_all_member_and_all_copper_material_graph(fault):
    plan, after = three_pad_case(fault=fault)
    proof = R.compare(plan, after)
    assert proof["native_connectivity_verified"]
    assert not proof["physical_connection"]["verified"]
    assert not proof["valid"]


@pytest.mark.parametrize("fault", ["missing_pin", "member_uid", "member_count", "old_opens"])
def test_bound_complete_member_contract_is_recomputed_independently(fault):
    plan, after = three_pad_case()
    if fault == "missing_pin":
        plan["endpoints"].pop()
    elif fault == "member_uid":
        plan["endpoints"][-1]["pin_uid"] = "wrong"
    elif fault == "member_count":
        plan["connection_contract"]["member_count"] = 2
    else:
        plan["before"]["geometry"]["model"]["connectivity"]["nets"][0]["opens"] = 1
    assert not R.compare(plan, after)["valid"]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "protected",
        "physical_missing_third",
        "physical_gap",
        "save_none",
        "save_false",
        "close_none",
        "close_false",
        "go_reply_loss",
    ],
)
def test_three_pin_lifecycle_obeys_same_one_go_and_strict_conditional_save(
    tmp_path, monkeypatch, fault
):
    material_fault = {"physical_missing_third": "missing_third", "physical_gap": "gap"}.get(fault)
    plan, after = three_pad_case(fault=material_fault)
    app, params, events, _, net = native_case(tmp_path, monkeypatch, fault=fault)
    app.ActiveDocument.state = copy.deepcopy(plan["before"])
    app.route_state = lambda before: copy.deepcopy(after)
    net.UniqueId = "n1"
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    if fault is None:
        result = R.execute(params, object(), A)
        assert result["persistence_verified"]
        assert R.verify_receipt(result, preview["routepass"], app.ActiveDocument.state)["valid"]
    else:
        with pytest.raises(A.AdapterError) as caught:
            R.execute(params, object(), A)
        result = caught.value.details["native_result"]
        assert not result["persistence_verified"]
    counts = {
        name: sum(event[0] == name for event in events) for name in ("Go", "Save", "Close", "Open")
    }
    expected = {
        None: (1, 1, 1, 1),
        "protected": (1, 0, 0, 0),
        "physical_missing_third": (1, 0, 0, 0),
        "physical_gap": (1, 0, 0, 0),
        "save_none": (1, 1, 0, 0),
        "save_false": (1, 1, 0, 0),
        "close_none": (1, 1, 1, 0),
        "close_false": (1, 1, 1, 0),
        "go_reply_loss": (1, 0, 0, 0),
    }[fault]
    assert tuple(counts.values()) == expected


def sdk_three_case(tmp_path, monkeypatch, fault=None):
    plan, after = three_pad_case()
    case = setup_case(tmp_path, monkeypatch, fault=fault)
    case.app.ActiveDocument.state = copy.deepcopy(plan["before"])
    case.app.route_state = lambda before: copy.deepcopy(after)
    case.net.UniqueId = "n1"
    return case


def test_public_sdk_three_pin_t_graph_and_repeated_confirmation_has_zero_new_writes(
    tmp_path, monkeypatch
):
    case = sdk_three_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        preview = planned.envelope["data"]["preview"]["extensions"]["legacy_preview"]
        assert preview["reminders_before"]["layout_reminder_coverage"]["status"] == "partial"
        assert preview["route_projection_boundary"]["new_trace_text_projection_checked"] is False
        token = planned.envelope["data"]["confirm_token"]
        done = cli.run([*case.args, "--confirm", token])
        assert done.exit_code == 0, done.envelope
        result = done.envelope["data"]["receipt"]["extensions"]["result"]
        assert result["verification"]["physical_connection"]["member_count"] == 3
        assert result["persistence_verified"]
        assert result["reminders_after"]["layout_reminder_coverage"]["object_counts"]["traces"] == 2
        assert (
            result["reminders_after"]["layout_reminder_coverage"][
                "empty_warnings_mean_layout_clear"
            ]
            is False
        )
        events = copy.deepcopy(case.events)
        repeated = cli.run([*case.args, "--confirm", token])
        assert repeated.exit_code == 6, repeated.envelope
        assert case.events == events
        assert sum(event[0] == "Go" for event in events) == 1


@pytest.mark.parametrize("fault", ["member_uid", "member_net", "member_material", "rules"])
def test_public_sdk_three_pin_input_drift_refuses_before_go(tmp_path, monkeypatch, fault):
    case = sdk_three_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        state = case.app.ActiveDocument.state
        if fault == "member_uid":
            state["pin_identities"][-1]["pin_uid"] = "wrong-member"
        elif fault == "member_net":
            state["all_pin_pad_material"][-1]["net_uid"] = "n2"
        elif fault == "member_material":
            state["all_pin_pad_material"][-1]["material"]["pads"][0]["bounds_mm"][0] += 0.000001
        else:
            state["rules"]["scheme"] = "different"
        rejected = cli.run([*case.args, "--confirm", planned.envelope["data"]["confirm_token"]])
        assert rejected.exit_code != 0, rejected.envelope
        assert not any(event[0] in {"Go", "Save", "Close", "Open"} for event in case.events)


def test_public_sdk_three_pin_lost_go_reply_remains_unresolved_and_cannot_dispatch_again(
    tmp_path, monkeypatch
):
    case = sdk_three_case(tmp_path, monkeypatch, fault="go_reply_loss")
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        operation_id = planned.envelope["data"]["preview"]["operation_id"]
        failed = cli.run([*case.args, "--confirm", planned.envelope["data"]["confirm_token"]])
        assert failed.exit_code != 0, failed.envelope
        events = copy.deepcopy(case.events)
        shown = cli.run(["operation", "show", "--id", operation_id])
        assert shown.envelope["data"]["operation_status"]["status"] == "unknown"
        blocked = cli.run([*case.args, "--dry-run"])
        # The real retained observation already contains target copper: its
        # preflight can refuse before the journal conflict check. Preserve both
        # that new refusal and the unresolved dispatched operation above.
        assert blocked.exit_code != 0, blocked.envelope
        repeated = cli.run([*case.args, "--confirm", planned.envelope["data"]["confirm_token"]])
        assert repeated.exit_code != 0, repeated.envelope
        assert case.events == events
        assert [event[0] for event in events if event[0] in {"Go", "Save", "Close", "Open"}] == [
            "Go"
        ]
