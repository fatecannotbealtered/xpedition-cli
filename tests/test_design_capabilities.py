"""Bounded whole-board capacity, not synthetic evidence of actual CAD success."""

import copy

import pytest
from test_components_public_observation import native_component_case
from test_native_component_candidate import candidate_case
from test_native_pad_mask import array, cells, contour, material
from test_native_pad_mask_components import component_fixture

from xpedition_cli import board_components as C
from xpedition_cli import board_routepass as R
from xpedition_cli import design_capabilities as D
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_component_candidate as Q
from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import digest


def representative_case(pin_count, *, traces=1):
    doc, state, target, adapter, engine = component_fixture(2, trace_count=traces)
    for index in range(2, pin_count):
        x, y = index * 4, 100
        raw = array(x - 1, y - 1, x + 1, y + 1)
        engine.filled[digest(raw)] = cells(x - 1, y - 1, x + 1, y + 1)
        state["all_pin_pad_material"].append(
            {
                "component_uid": "foreign-owner-" + str(index),
                "pin_uid": "foreign-pin-" + str(index),
                "refdes": "OTHER_" + str(index),
                "pin": "1",
                "center_mm": [x, y],
                "net": "(Net0)",
                "net_uid": "actual-net0-71",
                "material": material(contour(raw)),
            }
        )
    state["pin_identities"] = [
        {key: row[key] for key in ("component_uid", "pin_uid", "refdes", "pin", "net", "net_uid")}
        for row in state["all_pin_pad_material"]
    ]
    return doc, state, target, adapter, engine


def observed_case(pin_count):
    value = representative_case(pin_count)
    doc, state, target, adapter, _ = value
    state["native_mask_proof"] = M.evaluate_components(doc, state, target, adapter)
    state["native_trace_material"] = copy.deepcopy(
        state["native_mask_proof"]["raw_trace_inventory"]
    )
    return value


@pytest.mark.parametrize("pin_count", [467, 512])
def test_full_board_material_including_every_foreign_net0_pin_is_observed(pin_count):
    _, state, target, _, engine = observed_case(pin_count)
    proof = state["native_mask_proof"]
    assert proof["complete"], proof
    assert proof["budget"]["pins"] == pin_count
    assert len(proof["pin_materials"]) == pin_count
    assert len(proof["foreign_intersections"]) == pin_count - 2
    assert M.verify_component_evidence(state, target)["valid"]
    assert M.observe_components(state, target)["valid"]
    budget = D.prospective_budget(state, target)
    assert budget["valid"], budget
    assert budget["counts"]["pins"] == pin_count
    assert budget["counts"]["foreign_pad_objects"] == pin_count - 2
    assert budget["counts"]["traces"] == 2
    assert not engine.masks


def test_513_physical_pins_refused_before_any_native_mask_allocation():
    doc, state, target, adapter, engine = representative_case(513)
    result = M.evaluate_components(doc, state, target, adapter)
    assert not result["complete"] and result["errors"]
    assert engine.calls == []
    state["native_trace_material"] = []
    state["routing_objects"] = []
    budget = D.prospective_budget(state, target)
    assert not budget["valid"]
    assert any(row.get("resource") == "pins" for row in budget["errors"])


def test_257_old_traces_are_refused_before_any_native_mask_allocation():
    doc, state, target, adapter, engine = representative_case(467, traces=257)
    result = M.evaluate_components(doc, state, target, adapter)
    assert not result["complete"] and result["errors"]
    assert engine.calls == []


def test_unassigned_physical_pin_is_reported_not_omitted(tmp_path, monkeypatch):
    case = native_component_case(tmp_path, monkeypatch, pins=4, traces=0)
    case.doc.Components[-1].Pins[0].Net = None
    with pytest.raises(ValueError, match="Unassigned physical pins cannot be omitted"):
        R.observe_components(case.doc, A, case.selector)
    assert case.engine.calls == []


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "missing_material",
        "missing_identity",
        "missing_owner",
        "net_uid",
        "owner_alias",
        "ref_alias",
    ],
)
def test_complete_identity_requirement_cannot_hide_or_alias_foreign_material(fault):
    _, state, target, _, _ = observed_case(467)
    if fault == "duplicate":
        state["all_pin_pad_material"].append(copy.deepcopy(state["all_pin_pad_material"][-1]))
    elif fault == "missing_material":
        state["all_pin_pad_material"].pop()
    elif fault == "missing_identity":
        state["pin_identities"].pop()
    elif fault == "missing_owner":
        state["all_pin_pad_material"][-1]["component_uid"] = ""
    elif fault == "net_uid":
        state["all_pin_pad_material"][-1]["net_uid"] = target["net_uid"]
    else:
        key = "component_uid" if fault == "owner_alias" else "refdes"
        for inventory in (state["pin_identities"], state["all_pin_pad_material"]):
            inventory[-1][key] = inventory[-2][key]
    result = D.prospective_budget(state, target)
    assert not result["valid"] and not result["write_attempted"], result


@pytest.mark.parametrize("fault", ["delete", "duplicate_uid", "path", "width", "net"])
def test_every_old_copper_identity_and_full_geometry_is_required(fault):
    _, state, target, _, _ = observed_case(467)
    traces = state["native_trace_material"]
    if fault == "delete":
        traces.pop()
    elif fault == "duplicate_uid":
        traces.append(copy.deepcopy(traces[0]))
    elif fault == "path":
        traces[0]["points_array_mm"][0][-1] += 1
    elif fault == "width":
        traces[0]["width_mm"] += 0.1
    else:
        traces[0]["net"] = "(Net0)"
        traces[0]["net_uid"] = "actual-net0-71"
    assert not D.prospective_budget(state, target)["valid"]


def test_prospective_next_trace_capacity_is_reserved_before_write():
    _, state, target, _, _ = observed_case(467)
    template_raw = state["native_trace_material"][0]
    template_route = state["routing_objects"][0]
    # Seventeen old target traces fit 8192; the next eighteenth would need 8424.
    for index in range(1, 17):
        uid = "retained-old-trace-" + str(index)
        raw, route = copy.deepcopy(template_raw), copy.deepcopy(template_route)
        raw["uid"] = route["uid"] = uid
        route["geometry"]["uid"] = uid
        state["native_trace_material"].append(raw)
        state["routing_objects"].append(route)
    assert D.prospective_budget(state, target, added_trace=0)["valid"]
    result = D.prospective_budget(state, target)
    assert not result["valid"] and result["complete"]
    assert any(row.get("resource") == "comparisons" for row in result["errors"])
    assert result["counts"]["pins"] == 467 and result["counts"]["existing_trace_objects"] == 17


def test_contract_is_a_copy_and_bounds_are_not_removed():
    contract = D.contract()
    assert contract["limits"] == D.LIMITS
    assert contract["limits"]["pins"] == 512
    assert contract["limits"]["target_net_pins"] == 64
    contract["limits"]["pins"] = 999999
    assert D.contract()["limits"]["pins"] == 512


def rounded_outline():
    path = [
        [66, 0, 0],
        [66, 4, -4],
        [70, 4, 0],
        [70, 146, 0],
        [66, 146, -4],
        [66, 150, 0],
        [4, 150, 0],
        [4, 146, -4],
        [0, 146, 0],
        [0, 4, 0],
        [4, 4, -4],
        [4, 0, 0],
        [66, 0, 0],
    ]
    return contour([[point[axis] for point in path] for axis in (0, 1, 2)], arced=True)


def test_real_phone_outline_family_is_recognized_without_replacing_raw_boundary():
    outline = rounded_outline()
    original = copy.deepcopy(outline)
    assert Q._outline_plan(outline) == {
        "kind": "native_equivalent_rounded_rectangle",
        "bounds_mm": [0, 0, 70, 150],
        "radius_mm": 4,
    }
    assert outline == original


@pytest.mark.parametrize(
    "fault",
    [
        "positive_arcs",
        "one_sign",
        "not_closed",
        "arc_center",
        "radius",
        "extra_record",
        "arbitrary_shape",
    ],
)
def test_wrong_arc_sign_or_malformed_family_is_refused(fault):
    outline = rounded_outline()
    raw = outline["points_array_mm"]
    if fault == "positive_arcs":
        raw[2] = [-r for r in raw[2]]
    elif fault == "one_sign":
        raw[2][1] *= -1
    elif fault == "not_closed":
        raw[0][-1] -= 1
    elif fault == "arc_center":
        raw[0][1] -= 1
    elif fault == "radius":
        raw[2][1] = -3
    elif fault == "extra_record":
        for row in raw:
            row.insert(2, row[2])
    else:
        raw[0][3], raw[1][3] = 30, 80
    with pytest.raises(ValueError):
        Q._outline_plan(outline)


def test_native_rounded_equality_must_be_recorded_in_both_directions(tmp_path, monkeypatch):
    case, state, snapshot, connection = candidate_case(tmp_path, monkeypatch)
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert result["valid"], result
    # A straight native boundary cannot borrow a fabricated rounded equality claim.
    result["board_containment"]["outline_native_equality"] = {
        "actual_minus_candidate": {"area_mm2": 0, "is_empty": True, "shape_count": 0},
        "candidate_minus_actual": {"area_mm2": 0, "is_empty": True, "shape_count": 0},
    }
    assert not Q.verify(state, snapshot, connection, result)["valid"]


@pytest.mark.parametrize("fault", [None, "missing_material", "extra_material"])
def test_native_rounded_witness_compares_actual_material_both_ways(fault):
    """Explicit synthetic masks test the native-difference protocol, not arc decoding."""
    from types import SimpleNamespace as NS

    class Owner:
        def __init__(self):
            self.engine = object()
            utility = NS(CreateRectXYR=lambda *bounds: array(*bounds))
            self.a = NS(_propget=lambda engine, dispid: utility)

        def contour(self, record):
            if record["kind"] == "circle":
                x, y = record["center_mm"]
                r = record["radius_mm"]
                return {
                    (a, b)
                    for a in range(x - r, x + r + 1)
                    for b in range(y - r, y + r + 1)
                    if (a - x) ** 2 + (b - y) ** 2 <= r * r
                }
            raw = record["points_array_mm"]
            return cells(min(raw[0]), min(raw[1]), max(raw[0]), max(raw[1]))

        def boolean(self, operation, left, right):
            return left | right if operation == 2 else left - right

        def area(self, mask):
            return {"area_mm2": len(mask), "is_empty": not mask, "shape_count": int(bool(mask))}

    owner = Owner()
    actual = cells(0, 2, 10, 10) | cells(2, 0, 8, 12)
    for x in (2, 8):
        for y in (2, 10):
            actual |= owner.contour({"kind": "circle", "center_mm": [x, y], "radius_mm": 2})
    if fault == "missing_material":
        actual.remove((5, 5))
    elif fault == "extra_material":
        actual.add((100, 100))
    plan = {"bounds_mm": [0, 0, 10, 12], "radius_mm": 2}
    if fault:
        with pytest.raises(ValueError, match="not the connected rounded rectangle"):
            M._rounded_witness(owner, plan, actual)
    else:
        result = M._rounded_witness(owner, plan, actual)
        assert result["actual_minus_candidate"]["area_mm2"] == 0
        assert result["candidate_minus_actual"]["area_mm2"] == 0


@pytest.mark.parametrize(
    "fault", [None, "actual_extra", "actual_missing", "receipt_forward", "receipt_reverse"]
)
def test_candidate_binds_rounded_outline_and_native_equality(tmp_path, monkeypatch, fault):
    """Registered synthetic native replies exercise the real candidate path."""
    case, state, _, old_connection = candidate_case(tmp_path, monkeypatch)
    outline = rounded_outline()
    raw = outline["points_array_mm"]
    raw[0] = [value - 5 for value in raw[0]]
    raw[1] = [value - 5 for value in raw[1]]
    case.doc.BoardOutline.Geometry.values[1] = raw
    case.doc.BoardOutline.Geometry.IsArced = lambda: True
    state["geometry"]["model"]["outline"] = [
        {"kind": "outline", "path": [list(point) for point in zip(*raw, strict=True)]}
    ]
    actual = cells(-5, -1, 65, 141) | cells(-1, -5, 61, 145)

    def disk(x, y, radius):
        x, y, radius = int(x), int(y), int(radius)
        return {
            (a, b)
            for a in range(x - radius, x + radius + 1)
            for b in range(y - radius, y + radius + 1)
            if (a - x) ** 2 + (b - y) ** 2 <= radius * radius
        }

    for x in (-1, 61):
        for y in (-1, 141):
            actual |= disk(x, y, 4)
    if fault == "actual_extra":
        actual.add((-5, -5))
    elif fault == "actual_missing":
        actual.remove((50, 50))
    case.engine.filled[digest(raw)] = actual
    case.engine.filled[digest(array(-6, -6, 66, 146))] = cells(-6, -6, 66, 146)
    original_rectangle, original_circle = case.engine.rectangle, case.engine.circle

    def rectangle(*bounds):
        if bounds in ((0, 0, 3, 1), (0, 0, 1, 3)):
            return original_rectangle(*bounds)
        rows = array(*bounds)
        case.engine.filled[digest(rows)] = cells(*(int(value) for value in bounds))
        return rows

    def circle(x, y, radius):
        rows = original_circle(x, y, radius)
        case.engine.filled[digest(rows)] = disk(x, y, radius)
        return rows

    case.engine.utility.CreateRectXYR = rectangle
    case.engine.utility.CreateCircleXYR = circle
    snapshot = C.build_snapshot(
        state,
        M.observe_components(state, case.request),
        board_id=str(case.board),
        net_id=case.request["net_uid"],
        layer=1,
    )
    connection = C.plan_connection(
        snapshot,
        first=old_connection["first"],
        second=old_connection["second"],
        points=old_connection["points"],
        width_mm=old_connection["width_mm"],
    )
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    if fault in {"actual_extra", "actual_missing"}:
        assert not result["valid"] and result["errors"], result
    else:
        assert result["valid"] and Q.verify(state, snapshot, connection, result)["valid"], result[
            "errors"
        ]
        equality = result["board_containment"]["outline_native_equality"]
        assert equality["actual_minus_candidate"]["area_mm2"] == 0
        assert equality["candidate_minus_actual"]["area_mm2"] == 0
        if fault:
            key = (
                "actual_minus_candidate" if fault == "receipt_forward" else "candidate_minus_actual"
            )
            equality[key] = {"area_mm2": 1, "is_empty": False, "shape_count": 1}
            assert not Q.verify(state, snapshot, connection, result)["valid"]
    assert not case.engine.masks
