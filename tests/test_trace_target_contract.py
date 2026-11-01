"""Owned native-shaped pad reads and independent two-pad trace acceptance.

All objects are synthetic. No fixture is a native eligibility, rule, copper,
thermal or save/reopen proof.
"""

import copy
from types import SimpleNamespace as NS

import pytest
from test_unroute_native import Collection

from xpedition_cli import board_trace as T
from xpedition_cli import endpoint_material as M
from xpedition_cli import native_com_adapter as A


def material_native_pin(center=(0, 0), *, circle=False, cutout=False, hole=False):
    calls = []

    def geometry(center, radius, *, circle):
        x, y = center
        rows = [
            [x - radius, x + radius, x + radius, x - radius, x - radius],
            [y - radius, y - radius, y + radius, y + radius, y - radius],
            [0] * 5,
        ]
        result = NS(
            IsValid=lambda: True,
            IsCircle=lambda: circle,
            IsClosed=lambda: True,
            IsArced=lambda: False,
        )
        result.values = {1: rows, 10: x, 11: y, 12: radius, 21: True, 22: Collection()}
        return result

    outer = geometry(center, 0.3, circle=circle)
    if cutout:
        outer.values[22].append(geometry(center, 0.12, circle=True))
    pad = NS(
        Name="owned-pad",
        Layer=1,
        IsNegative=lambda: False,
        IsNoConnect=lambda: False,
        values={4: Collection([outer])},
    )
    holes = Collection()
    if hole:
        holes.append(
            NS(
                IsValid=lambda: True,
                ShapeType=1,
                Plated=True,
                values={1: 0.2, 4: center[0], 5: center[1]},
            )
        )
    pin = NS(values={20: Collection([pad]), 25: holes})

    def propget(owner, dispid, *args):
        calls.append((owner, dispid, args))
        if owner is pin and dispid == 20:
            assert args == (1, True)
        elif dispid in {1, 4, 5, 10, 11, 12} and owner not in (pin, pad):
            assert args == (4,)
        else:
            assert args == ()
        value = owner.values[dispid]
        if isinstance(value, Exception):
            raise value
        return value

    adapter = NS(_propget=propget, _items=A._items)
    return pin, adapter, calls, outer, pad


def owned_pin_material(center=(0, 0), **kwargs):
    pin, adapter, *_ = material_native_pin(center, **kwargs)
    result = M.observe_pin(pin, 1, adapter)
    assert result["complete"] is True, result
    return result


def connected_state(state, *, opens=0, routed=True):
    from test_trace_safety import ITEM

    result = copy.deepcopy(state)
    shape = {
        "kind": "trace",
        "net": "A",
        "layer": 1,
        "width": ITEM["width"],
        "path": [[*point, 0.0] for point in ITEM["points"]],
    }
    result["routing_objects"].append({"kind": "trace", "uid": "111", "net": "A", "geometry": shape})
    model = result["geometry"]["model"]
    model["traces"].append(copy.deepcopy(shape))
    model["connectivity"]["nets"][0].update(opens=opens, routed=routed)
    model["unrouted"] = [copy.deepcopy(r) for r in model["connectivity"]["nets"] if r["opens"]]
    return result


def contract_case():
    from test_trace_safety import ITEM, trace_state

    before = trace_state(trusted_rules=True)
    plan = T.bind(before, ITEM, [], A)
    assert plan["can_apply"] is True
    return plan, connected_state(before)


@pytest.mark.parametrize(
    "circle,cutout,hole",
    [(False, False, False), (True, True, False), (True, False, True), (False, True, True)],
)
def test_native_material_reader_and_supported_contact(circle, cutout, hole):
    material = owned_pin_material(circle=circle, cutout=cutout, hole=hole)
    endpoint = {"pads": material["pads"], "holes": material["holes"]}
    witness = M._contact_witness(endpoint, [0, 0], [5, 0])
    assert witness is not None
    assert 0 < witness["centerline_point_mm"][0] < 0.3
    if cutout or hole:
        assert witness["centerline_point_mm"][0] > (0.12 if cutout else 0.1)


@pytest.mark.parametrize(
    "fault",
    [
        "unfilled",
        "cutout_missing",
        "invalid",
        "arced",
        "open",
        "negative",
        "no_connect",
        "wrong_layer",
        "hole_slot",
        "nested_cutout",
        "points_malformed",
        "points_over_budget",
    ],
)
def test_native_material_incomplete_is_explicit(fault):
    pin, adapter, _, geometry, pad = material_native_pin(hole=True)
    if fault == "unfilled":
        geometry.values[21] = False
    elif fault == "cutout_missing":
        geometry.values[22] = RuntimeError("cutout read failed")
    elif fault == "invalid":
        geometry.IsValid = lambda: False
    elif fault == "arced":
        geometry.IsArced = lambda: True
    elif fault == "open":
        geometry.IsClosed = lambda: False
    elif fault == "negative":
        pad.IsNegative = lambda: True
    elif fault == "no_connect":
        pad.IsNoConnect = lambda: True
    elif fault == "wrong_layer":
        pad.Layer = 2
    elif fault == "hole_slot":
        pin.values[25][0].ShapeType = 10
    elif fault == "nested_cutout":
        geometry.values[22].append(geometry)
    elif fault == "points_malformed":
        geometry.values[1] = [[0, 1, 2], [0], [0, 0, 0]]
    elif fault == "points_over_budget":
        geometry.values[1] = [[0] * 129] * 3
    read = M.observe_pin(pin, 1, adapter)
    assert read["complete"] is False and read["unread"]
    assert read["unread"][0]["affected_requirement"] == "independent endpoint material contact"


@pytest.mark.parametrize(
    "opens,routed", [(-1, True), (2, False), (1, False), (0, False), (False, True), (0, None)]
)
def test_target_connectivity_illegal_or_degraded_never_passes(opens, routed):
    plan, _ = contract_case()
    proof = T.compare(plan, connected_state(plan["before"], opens=opens, routed=routed), "111")
    assert not proof["valid"] and not proof["completion_valid"]
    assert not proof["native_connectivity_verified"]


def test_exact_trace_contact_and_native_connectivity_are_separate():
    plan, after = contract_case()
    proof = T.compare(plan, after, "111")
    assert proof["valid"] and proof["completion_valid"] and proof["protection_valid"]
    assert proof["physical_connection"]["status"] == "verified"
    assert proof["physical_contact_verified"] and proof["native_connectivity_verified"]
    assert not proof["drc_executed"]


@pytest.mark.parametrize(
    "fault",
    [
        "missing_material",
        "bbox_only",
        "wrong_endpoint",
        "wrong_pin_uid",
        "wrong_net",
        "width",
        "path",
        "uid",
        "unrouted_missing",
        "unrouted_stale",
        "missing_geometry",
        "missing_target",
        "material_drift",
    ],
)
def test_target_identity_shape_and_readback_failures(fault):
    plan, after = contract_case()
    if fault in {"missing_material", "bbox_only"}:
        plan["endpoints"][0]["pads"][0]["geometry"][0].pop("material")
        if fault == "bbox_only":
            plan["endpoints"][0]["pads"][0]["geometry"][0]["bounds_mm"] = [-0.3, -0.3, 0.3, 0.3]
    elif fault == "wrong_endpoint":
        plan["endpoints"][0]["pin"] = "2"
    elif fault == "wrong_pin_uid":
        plan["endpoints"][0]["pin_uid"] = "foreign-pin"
    elif fault == "wrong_net":
        after["routing_objects"][-1]["net"] = "B"
    elif fault == "width":
        after["routing_objects"][-1]["geometry"]["width"] *= 2
    elif fault == "path":
        after["routing_objects"][-1]["geometry"]["path"][0][0] = 0.2
    elif fault == "uid":
        after["routing_objects"][-1]["uid"] = "wrong"
    elif fault == "unrouted_missing":
        after["geometry"]["model"].pop("unrouted")
    elif fault == "unrouted_stale":
        after["geometry"]["model"]["unrouted"] = plan["before"]["geometry"]["model"]["unrouted"]
    elif fault == "missing_geometry":
        after.pop("geometry")
    elif fault == "missing_target":
        after["geometry"]["model"]["connectivity"]["nets"].pop(0)
    elif fault == "material_drift":
        after["endpoint_pad_material"][0]["material"]["holes"].append({})
    proof = T.compare(plan, after, "111")
    assert proof["valid"] is False
    if fault in {"missing_material", "bbox_only", "wrong_endpoint", "wrong_pin_uid"}:
        assert proof["protection_valid"] and not proof["completion_valid"]
    if fault in {"wrong_net", "width", "path", "uid", "material_drift"}:
        assert not proof["physical_contact_verified"]


@pytest.mark.parametrize(
    "opens,routed", [(-1, False), (0, True), (2, False), (True, False), (1, None)]
)
def test_initial_target_contract_rejects_invalid_or_unintended_connectivity(opens, routed):
    from test_trace_safety import ITEM, trace_state

    before = trace_state(trusted_rules=True)
    before["geometry"]["model"]["connectivity"]["nets"][0].update(opens=opens, routed=routed)
    with pytest.raises(A.AdapterError):
        T.bind(before, ITEM, [], A)


@pytest.mark.parametrize("fault", ["void", "tangent", "arc", "self_intersection", "unit", "filled"])
def test_bounds_and_native_opens_cannot_substitute_for_real_material(fault):
    material = owned_pin_material(circle=True)
    shape = material["pads"][0]["geometry"][0]["material"]
    start, end = [0, 0], [5, 0]
    if fault == "void":
        shape["cutouts"] = [{"circle": [0, 0, 1]}]
    elif fault == "tangent":
        start, end = [-1, 0.3], [1, 0.3]
    elif fault == "arc":
        shape["outer"] = {"closed": True, "path": [[0, 0, 0], [1, 0, 0.2], [1, 1, 0], [0, 1, 0]]}
    elif fault == "self_intersection":
        shape["outer"] = {"closed": True, "path": [[0, 0, 0], [1, 1, 0], [0, 1, 0], [1, 0, 0]]}
    elif fault == "unit":
        shape["unit"] = "mils"
    elif fault == "filled":
        shape["filled"] = False
    endpoint = {"pads": material["pads"], "holes": []}
    if fault in {"void", "tangent"}:
        assert M._contact_witness(endpoint, start, end) is None
    else:
        with pytest.raises(ValueError):
            M._contact_witness(endpoint, start, end)


def test_global_material_contour_budget_is_not_reset_for_each_shape():
    pin, adapter, _, geometry, pad = material_native_pin(circle=True)
    # 5x32 individually bounded contours must exceed the per-pin total budget.
    pad.values[4] = Collection([geometry] * 32)
    pin.values[20] = Collection([pad] * 5)
    read = M.observe_pin(pin, 1, adapter)
    assert not read["complete"] and read["geometry_budget"]["contours"] == 129
    assert "128-contour budget" in read["unread"][0]["reason"]
