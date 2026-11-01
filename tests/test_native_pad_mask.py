"""Synthetic adapter and temporary-mask protocol; no licensed geometry proof.

The fake native engine uses explicitly registered sets of test cells. It does
not implement or document Xpedition's XYR arc encoding or physical geometry.
"""

import copy
from types import SimpleNamespace as NS

import pytest
from test_trace_target_contract import material_native_pin

from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import _canonical, digest


class Collection(list):
    @property
    def Count(self):
        return len(self)


def array(x0, y0, x1, y1, *, arc=0):
    return [[x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], [arc] * 5]


def cells(x0, y0, x1, y1):
    return {(x, y) for x in range(x0, x1) for y in range(y0, y1)}


class FakeShapes:
    def __init__(self, mask):
        self.mask = mask

    @property
    def Count(self):
        return int(not self.mask.IsEmpty())

    def AddByPointsArray(self, count, rows, solid, unit):
        assert count == len(rows[0]) and solid is True and unit == 4
        self.mask.engine.calls.append(("AddByPointsArray", copy.deepcopy(rows), unit))
        key = digest(rows)
        if key in self.mask.engine.control_contours:
            self.mask.control_key = key
            self.mask.area_override = self.mask.engine.control_contours[key]
            return NS()
        self.mask.cells |= self.mask.engine.filled[digest(rows)]
        return NS()

    def AddByPath(self, count, rows, width, solid, oversize, unit):
        assert count == len(rows[0]) and solid is True and oversize == 0 and unit == 4
        self.mask.engine.calls.append(("AddByPath", copy.deepcopy(rows), width, unit))
        self.mask.cells |= self.mask.engine.paths[(digest(rows), width)]


class FakeMask:
    def __init__(self, engine, occupied=()):
        self.engine, self.cells = engine, set(occupied)
        self.shapes = FakeShapes(self)
        self.deleted = False
        self.control_key = None
        self.area_override = None

    def IsEmpty(self):
        return self.area_override == 0 if self.area_override is not None else not self.cells

    def Touch(self, other):
        self.engine.calls.append(("Touch",))
        if self.control_key is not None and other.control_key is not None:
            pair = frozenset((self.control_key, other.control_key))
            value = self.engine.control_pairs[pair][0]
            if self.engine.control_touch_fault:
                return not value
            return value
        if self.engine.touch_failure:
            raise RuntimeError("synthetic Mask.Touch failure")
        if self.engine.touch_result is not None:
            return self.engine.touch_result
        return any(
            abs(x - a) <= 1 and abs(y - b) <= 1 for x, y in self.cells for a, b in other.cells
        )

    def ContainsPoint(self, x, y, unit):
        assert unit == 4
        if self.control_key == self.engine.control_union_key:
            result = self.engine.control_union_points[(x, y)]
            return not result if self.engine.control_union_contains_fault else result
        return any(a <= x <= a + 1 and b <= y <= b + 1 for a, b in self.cells)

    def Delete(self):
        self.engine.calls.append(("Delete",))
        assert not self.deleted
        if self.engine.delete_failure:
            raise RuntimeError("synthetic individual Delete failed")
        self.deleted = True
        self.engine.masks.remove(self)


class FakeMasks(Collection):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def Add(self):
        mask = FakeMask(self.engine)
        self.append(mask)
        return mask


class FakeEngine:
    def __init__(self):
        self.calls, self.filled, self.paths = [], {}, {}
        self.masks = FakeMasks(self)
        self.delete_failure = False
        self.boolean_failure = False
        self.touch_failure = self.control_touch_fault = False
        self.touch_result = None
        self.control_factory_failure = self.control_factory_malformed = False
        self.control_union_area_fault = self.control_union_contains_fault = False
        self.control_union_key = "registered-native-rectangle-union-L"
        self.control_union_points = {}
        self.control_contours, self.control_pairs = {}, {}
        for row in M._touch_control_inputs():
            if row["left"]["kind"] == "native_rectangle_union":
                self.control_union_points = {
                    tuple(point["point_mm"]): point["expected"]
                    for point in row["left_contains_points"]
                }
                third = array(*row["right"]["bounds_mm"])
                self.control_pairs[frozenset((self.control_union_key, digest(third)))] = (
                    row["expected_touch"],
                    row["expected_area_mm2"],
                )
                continue
            for shape in (row["left"], row["right"]):
                raw = shape["points_array_mm"]
                xy = list(zip(raw[0], raw[1], strict=True))
                self.control_contours[digest(raw)] = (
                    abs(
                        sum(
                            left[0] * right[1] - right[0] * left[1]
                            for left, right in zip(xy, xy[1:], strict=False)
                        )
                    )
                    / 2
                )
            pair = frozenset(
                (digest(row["left"]["points_array_mm"]), digest(row["right"]["points_array_mm"]))
            )
            self.control_pairs[pair] = (row["expected_touch"], row["expected_area_mm2"])
        self.utility = NS(CreateCircleXYR=self.circle, CreateRectXYR=self.rectangle)

    def rectangle(self, x0, y0, x1, y1):
        self.calls.append(("CreateRectXYR", x0, y0, x1, y1))
        if self.control_factory_failure:
            raise RuntimeError("synthetic native rectangle factory failure")
        if self.control_factory_malformed:
            return [[x0], [y0]]
        raw = array(x0, y0, x1, y1)
        self.control_contours[digest(raw)] = (x1 - x0) * (y1 - y0)
        return raw

    def circle(self, x, y, radius):
        # Arbitrary opaque XYR test records, never parsed by production code.
        rows = [[x - radius, x, x + radius], [y, y + radius, y], [radius, -radius, radius]]
        self.calls.append(("CreateCircleXYR", x, y, radius))
        self.filled[digest(rows)] = {(int(x), int(y))}
        return rows

    def BooleanOp(self, operation, left, right):
        self.calls.append(("BooleanOp", operation))
        if self.boolean_failure:
            raise RuntimeError("synthetic BooleanOp failure")
        if left.control_key is not None and right.control_key is not None:
            mask = FakeMask(self)
            if operation == 2:
                assert {left.control_key, right.control_key} == {
                    digest(array(0, 0, 3, 1)),
                    digest(array(0, 0, 1, 3)),
                }
                mask.control_key = self.control_union_key
                mask.area_override = 5.125 if self.control_union_area_fault else 5
            else:
                assert operation == 1
                pair = frozenset((left.control_key, right.control_key))
                mask.area_override = self.control_pairs[pair][1]
            self.masks.append(mask)
            return mask
        occupied = (
            left.cells - right.cells
            if operation == 0
            else left.cells & right.cells
            if operation == 1
            else left.cells | right.cells
        )
        mask = FakeMask(self, occupied)
        self.masks.append(mask)
        return mask


def material(outer, *, cutouts=(), hole=False):
    raw = outer["points_array_mm"]
    result = {
        "source": "native_pin_owned_pad_material_mask151",
        "complete": True,
        "unit": "mm",
        "layer": 1,
        "unread": [],
        "pads": [
            {
                "name": "name-is-not-geometry",
                "layer": 1,
                "bounds_mm": [min(raw[0]), min(raw[1]), max(raw[0]), max(raw[1])],
                "geometry": [{"filled": True, "outer": outer, "cutouts": list(cutouts)}],
            }
        ],
        "holes": [],
    }
    if hole:
        result["holes"].append({"center_mm": [0, 0], "diameter_mm": 1, "plated": True})
    return result


def contour(rows, *, arced=False):
    return {
        "kind": "native_points_array",
        "closed": True,
        "is_arced": arced,
        "points_array_mm": rows,
    }


def fixture(*, traces=True, foreign=False, hole=False, cutout=False, opaque_arc=False):
    engine = FakeEngine()
    first, second, other = (
        array(-2, -2, 3, 3, arc=0.7 if opaque_arc else 0),
        array(8, -2, 13, 3),
        array(30, 30, 32, 32),
    )
    void = array(-1, -1, 1, 1, arc=-0.3)
    for rows, occupied in [
        (first, cells(-2, -2, 3, 3)),
        (second, cells(8, -2, 13, 3)),
        (other, cells(30, 30, 32, 32)),
        (void, {(0, 0)}),
    ]:
        engine.filled[digest(rows)] = occupied
    materials = [
        {
            "component_uid": "c1",
            "pin_uid": "p1",
            "refdes": "J1",
            "pin": "1",
            "center_mm": [0, 0],
            "net": "A",
            "net_uid": "n1",
            "material": material(
                contour(first, arced=opaque_arc),
                cutouts=[contour(void, arced=True)] if cutout else [],
                hole=hole,
            ),
        },
        {
            "component_uid": "c2",
            "pin_uid": "p2",
            "refdes": "R1",
            "pin": "1",
            "center_mm": [10, 0],
            "net": "A",
            "net_uid": "n1",
            "material": material(contour(second)),
        },
    ]
    if foreign:
        materials.append(
            {
                "component_uid": "c3",
                "pin_uid": "p3",
                "refdes": "R2",
                "pin": "1",
                "center_mm": [31, 31],
                "net": "B",
                "net_uid": "n2",
                "material": material(contour(other)),
            }
        )
    raw = [[0, 10], [0, 0], [0, 0]]
    engine.paths[(digest(raw), 0.254)] = cells(0, 0, 11, 1)
    geometry = NS(IsValid=lambda: True, IsArced=lambda: False, values={1: raw, 5: 0.254})
    extrema = NS(IsValid=lambda: True, values={1: -0.127, 2: -0.127, 3: 10.127, 4: 0.127})
    trace = NS(
        UniqueId="t1",
        Net=NS(Name="A", UniqueId="n1"),
        Layer=1,
        Geometry=geometry,
        values={7: extrema},
    )
    shape = {
        "uid": "t1",
        "kind": "trace",
        "net": "A",
        "layer": 1,
        "width": 0.254,
        "path": [[0, 0, 0], [10, 0, 0]],
    }
    state = {
        "all_pin_pad_material": materials,
        "geometry": {
            "model": {
                "connectivity": {"nets": [{"net": "A", "uid": "n1"}, {"net": "B", "uid": "n2"}]}
            }
        },
        "routing_objects": [
            {"kind": "trace", "uid": "t1", "net": "A", "geometry": _canonical(shape)}
        ]
        if traces
        else [],
    }
    doc = NS(
        Traces=Collection([trace] if traces else []),
        engine=engine,
        Save=lambda: pytest.fail("Mask observer must not Save"),
        Close=lambda *_: pytest.fail("Mask observer must not Close"),
    )

    def propget(owner, dispid, *args):
        if owner is doc:
            assert dispid == 326 and args == ()
            return engine
        if owner is engine:
            assert not args
            return {0: engine.masks, 2: engine.utility}[dispid]
        if isinstance(owner, FakeMask):
            if dispid == 1:
                assert not args
                return owner.shapes
            if dispid == 2:
                assert args == (4,)
                return (
                    owner.area_override
                    if owner.area_override is not None
                    else len(owner.cells) * 0.01
                )
        if isinstance(owner, NS) and hasattr(owner, "values"):
            if dispid == 7:
                assert not args
                return owner.values[dispid]
            assert args == (4,)
            return owner.values[dispid]
        raise AssertionError((owner, dispid, args))

    adapter = NS(_propget=propget, _items=lambda collection, strict: iter(collection))
    return doc, state, {"net": "A", "layer": 1}, adapter, engine


def test_raw_native_reader_retains_arcs_without_interpreting_them():
    pin, adapter, _, geometry, pad = material_native_pin(cutout=True, hole=True)
    add_extrema(adapter, pad)
    geometry.IsArced = lambda: True
    geometry.values[1][2] = [0.1, -0.2, 0.3, -0.4, 0.5]
    read = M.observe_pin(pin, 1, adapter)
    assert read["complete"], read
    outer = read["pads"][0]["geometry"][0]["outer"]
    assert outer["is_arced"] is True
    assert outer["points_array_mm"] == geometry.values[1]
    assert read["holes"][0]["diameter_mm"] == 0.2
    assert read["pads"][0]["geometry"][0]["cutouts"][0]["kind"] == "circle"


@pytest.mark.parametrize(
    "fault",
    [
        "count",
        "missing_points",
        "ragged",
        "nan",
        "budget",
        "negative",
        "cutout",
        "nested_cutout",
        "slot",
        "layer",
        "fill",
        "invalid",
    ],
)
def test_raw_native_adapter_failures_preserve_incomplete_reason(fault):
    pin, adapter, _, geometry, pad = material_native_pin(hole=True)
    add_extrema(adapter, pad)
    if fault == "count":
        pin.values[20] = NS(Count=-1)
    elif fault == "missing_points":
        geometry.values[1] = RuntimeError("native PointsArray failed")
    elif fault == "ragged":
        geometry.values[1][1] = [0]
    elif fault == "nan":
        geometry.values[1][2][0] = float("nan")
    elif fault == "budget":
        geometry.values[1] = [[0] * 129] * 3
    elif fault == "negative":
        pad.IsNegative = lambda: True
    elif fault == "cutout":
        geometry.values[22] = RuntimeError("native Cutouts failed")
    elif fault == "nested_cutout":
        geometry.values[22].append(geometry)
    elif fault == "slot":
        pin.values[25][0].ShapeType = 10
    elif fault == "layer":
        pad.Layer = None
    elif fault == "fill":
        geometry.values[21] = None
    elif fault == "invalid":
        geometry.IsValid = lambda: False
    read = M.observe_pin(pin, 1, adapter)
    assert read["complete"] is False and read["unread"]


def add_extrema(adapter, pad):
    original = adapter._propget
    extrema = NS(IsValid=lambda: True)

    def propget(owner, dispid, *args):
        if owner is pad and dispid == 7:
            assert not args
            return extrema
        if owner is extrema:
            assert args == (4,)
            return {1: -0.3, 2: -0.3, 3: 0.3, 4: 0.3}[dispid]
        return original(owner, dispid, *args)

    adapter._propget = propget
    return extrema


def test_no_trace_preflight_uses_native_raw_material_and_deletes_only_owned_masks():
    doc, state, request, adapter, engine = fixture(
        traces=False, hole=True, cutout=True, opaque_arc=True
    )
    preexisting = engine.masks.Add()
    proof = M.evaluate(doc, state, request, adapter)
    assert proof["complete"], proof
    assert engine.masks == [preexisting] and not preexisting.deleted
    assert proof["target_contacts"] == []
    assert any(call[0] == "CreateCircleXYR" and call[3] == 0.5 for call in engine.calls)
    assert [call[1] for call in engine.calls if call[0] == "BooleanOp"].count(0) == 2
    assert any(call[0] == "AddByPointsArray" and call[1][2] == [0.7] * 5 for call in engine.calls)
    assert all(row["delete_returned"] for row in proof["cleanup"])


@pytest.mark.parametrize(
    "fault", ["delete", "boolean", "trace_points", "trace_width", "trace_count"]
)
def test_native_mask_failure_is_explicit_and_never_attempts_persistence(fault):
    doc, state, request, adapter, engine = fixture(hole=True)
    if fault == "delete":
        engine.delete_failure = True
    elif fault == "boolean":
        engine.boolean_failure = True
    elif fault == "trace_points":
        doc.Traces[0].Geometry.values[1][2][0] = 1
    elif fault == "trace_width":
        doc.Traces[0].Geometry.values[5] = 0
    elif fault == "trace_count":
        doc.Traces.append(doc.Traces[0])
    proof = M.evaluate(doc, state, request, adapter)
    assert proof["complete"] is False and proof["errors"]
    assert all(call[0] not in {"Save", "Close", "Open"} for call in engine.calls)


def evaluated(*, traces=True, foreign=True, hole=True, cutout=True):
    doc, state, request, adapter, engine = fixture(
        traces=traces, foreign=foreign, hole=hole, cutout=cutout
    )
    proof = M.evaluate(doc, state, request, adapter)
    state["native_mask_proof"] = proof
    state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
    return doc, state, request, adapter, engine


@pytest.mark.parametrize("traces", [False, True])
def test_positive_mask_protocol_and_independent_receipt_matrix(traces):
    _, state, request, _, engine = evaluated(traces=traces)
    proof = state["native_mask_proof"]
    assert proof["complete"], proof
    verified = M.verify_evidence(state, request)
    assert verified["valid"], verified
    assert not engine.masks
    if traces:
        assert len(proof["target_contacts"]) == 2
        assert all(row["positive_intersection_area_mm2"] > 0 for row in proof["target_contacts"])
        assert proof["target_contacts"][0]["trace_ends"][0]["inside_owned_outer"] is True
        assert len(proof["foreign_intersections"]) == 1
        assert proof["foreign_intersections"][0]["strict_bounds_separated"] is True


@pytest.mark.parametrize(
    "fault",
    [
        "claimed_complete",
        "raw_material_drift",
        "raw_trace_drift",
        "target_geometry_digest",
        "missing_pin_mask",
        "negative_pin_area",
        "contact_missing",
        "contact_duplicate",
        "contact_wrong_pin",
        "contact_empty_conflict",
        "contact_wrong_width_digest",
        "contact_wrong_end",
        "foreign_missing",
        "foreign_duplicate",
        "foreign_wrong_identity",
        "foreign_bound_drift",
        "foreign_contact",
        "foreign_boundary",
        "delete_failed",
        "delete_missing",
        "delete_duplicate",
        "collection_count",
        "over_budget",
        "error_with_complete",
    ],
)
def test_complete_marker_cannot_override_missing_or_contradictory_evidence(fault):
    _, state, request, _, _ = evaluated()
    proof = state["native_mask_proof"]
    assert M.verify_evidence(state, request)["valid"]
    if fault == "claimed_complete":
        proof["complete"] = False
    elif fault == "raw_material_drift":
        state["all_pin_pad_material"][0]["material"]["pads"][0]["name"] = "different"
    elif fault == "raw_trace_drift":
        state["native_trace_material"][0]["width_mm"] = 0.3
    elif fault == "target_geometry_digest":
        proof["trace_geometry_sha256"] = "wrong"
    elif fault == "missing_pin_mask":
        proof["pin_materials"].pop()
    elif fault == "negative_pin_area":
        proof["pin_materials"][0]["material_area_mm2"] = -1
    elif fault == "contact_missing":
        proof["target_contacts"].pop()
    elif fault == "contact_duplicate":
        proof["target_contacts"].append(copy.deepcopy(proof["target_contacts"][0]))
    elif fault == "contact_wrong_pin":
        proof["target_contacts"][0]["pin_uid"] = "wrong"
    elif fault == "contact_empty_conflict":
        proof["target_contacts"][0]["intersection_is_empty"] = True
    elif fault == "contact_wrong_width_digest":
        proof["target_contacts"][0]["raw_trace_sha256"] = "wrong"
    elif fault == "contact_wrong_end":
        proof["target_contacts"][0]["trace_ends"][0]["point_mm"] = [5, 5]
    elif fault == "foreign_missing":
        proof["foreign_intersections"].pop()
    elif fault == "foreign_duplicate":
        proof["foreign_intersections"].append(copy.deepcopy(proof["foreign_intersections"][0]))
    elif fault == "foreign_wrong_identity":
        proof["foreign_intersections"][0]["foreign_pin_uid"] = "wrong"
    elif fault == "foreign_bound_drift":
        proof["foreign_intersections"][0]["foreign_bounds_mm"][0] += 1
    elif fault == "foreign_contact":
        proof["foreign_intersections"][0]["intersection_area_mm2"] = 0.1
    elif fault == "foreign_boundary":
        proof["foreign_intersections"][0]["strict_bounds_separated"] = False
    elif fault == "delete_failed":
        proof["cleanup"][0]["delete_returned"] = False
    elif fault == "delete_missing":
        proof["cleanup"].pop()
    elif fault == "delete_duplicate":
        proof["cleanup"][0]["owned_allocation_index"] = proof["cleanup"][1][
            "owned_allocation_index"
        ]
    elif fault == "collection_count":
        proof["temporary_masks_count"]["after"] += 1
    elif fault == "over_budget":
        proof["budget"]["mask_points"] = M.MAX_POINTS + 1
    elif fault == "error_with_complete":
        proof["errors"].append({"reason": "unread native geometry"})
    assert M.verify_evidence(state, request)["valid"] is False


def test_drill_only_material_area_is_zero_even_with_outer_anchor():
    doc, state, request, adapter, engine = fixture(hole=True)
    raw = doc.Traces[0].Geometry.values[1]
    engine.paths[(digest(raw), 0.254)] = {(0, 0)}
    proof = M.evaluate(doc, state, request, adapter)
    assert proof["complete"], proof
    first = proof["target_contacts"][0]
    assert first["trace_ends"][0]["inside_owned_outer"]
    assert first["positive_intersection_area_mm2"] == 0
    assert first["intersection_is_empty"] is True
    # The graph verifier must additionally require positive material contact;
    # an accurate empty-intersection observation is not incomplete evidence.


def test_foreign_bounds_touching_is_unproven_even_if_native_and_is_empty():
    doc, state, request, adapter, engine = fixture(foreign=True)
    foreign = state["all_pin_pad_material"][-1]
    foreign["material"]["pads"][0]["bounds_mm"] = [10.127, -1, 12, 1]
    engine.touch_result = True
    proof = M.evaluate(doc, state, request, adapter)
    assert proof["complete"], proof
    assert proof["foreign_intersections"][0]["intersection_is_empty"]
    assert not proof["foreign_intersections"][0]["strict_bounds_separated"]
    state["native_mask_proof"] = proof
    state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
    assert M.verify_evidence(state, request)["valid"] is False


def test_every_old_trace_and_individual_foreign_pad_is_in_full_matrix():
    doc, state, request, adapter, engine = fixture(foreign=True)
    raw = [[30, 40], [0, 0], [0, 0]]
    geometry = NS(IsValid=lambda: True, IsArced=lambda: False, values={1: raw, 5: 0.4})
    extrema = NS(IsValid=lambda: True, values={1: 29.8, 2: -0.2, 3: 40.2, 4: 0.2})
    doc.Traces.append(
        NS(
            UniqueId="t-old",
            Net=NS(Name="B", UniqueId="n2"),
            Layer=1,
            Geometry=geometry,
            values={7: extrema},
        )
    )
    engine.paths[(digest(raw), 0.4)] = cells(30, 0, 41, 1)
    state["routing_objects"].append(
        {
            "kind": "trace",
            "uid": "t-old",
            "net": "B",
            "geometry": {
                "uid": "t-old",
                "kind": "trace",
                "net": "B",
                "layer": 1,
                "width": 0.4,
                "path": [[30, 0, 0], [40, 0, 0]],
            },
        }
    )
    # Two distinct effective pads owned by the same foreign physical pin.
    original = state["all_pin_pad_material"][-1]["material"]["pads"][0]
    state["all_pin_pad_material"][-1]["material"]["pads"].append(copy.deepcopy(original))
    proof = M.evaluate(doc, state, request, adapter)
    state["native_mask_proof"] = proof
    state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
    assert proof["complete"], proof
    assert len(proof["foreign_intersections"]) == 3
    assert {row.get("foreign_pad_index") for row in proof["foreign_intersections"]} == {0, 1, None}
    assert M.verify_evidence(state, request)["valid"]
    proof["foreign_intersections"].pop()
    assert not M.verify_evidence(state, request)["valid"]


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "invalid",
        "false_validity",
        "nan",
        "reversed",
        "zero_extent",
        "wrong_centerline",
        "nonnumeric",
    ],
)
def test_actual_trace_extrema_failure_stops_before_any_mask_or_persistence(fault):
    doc, state, request, adapter, engine = fixture(foreign=True)
    trace = doc.Traces[0]
    extrema = trace.values[7]
    if fault == "missing":
        trace.values.clear()
    elif fault == "invalid":
        trace.values[7] = None
    elif fault == "false_validity":
        extrema.IsValid = lambda: False
    elif fault == "nan":
        extrema.values[1] = float("nan")
    elif fault == "reversed":
        extrema.values[1] = 15
    elif fault == "zero_extent":
        extrema.values[4] = extrema.values[2]
    elif fault == "wrong_centerline":
        extrema.values[3] = 5
    elif fault == "nonnumeric":
        extrema.values[1] = "-0.127"
    proof = M.evaluate(doc, state, request, adapter)
    assert not proof["complete"] and proof["errors"]
    assert not engine.calls and not engine.masks


def test_trace_bounds_uses_actual_extrema_without_cap_or_join_inference():
    doc, state, request, adapter, _ = fixture(foreign=True)
    # A deliberately wider native observation must survive unchanged rather than
    # being replaced by the centerline +/- half width.
    doc.Traces[0].values[7].values = {1: -1, 2: -2, 3: 12, 4: 3}
    proof = M.evaluate(doc, state, request, adapter)
    assert proof["complete"], proof
    raw = proof["raw_trace_inventory"][0]
    assert raw["bounds_mm"] == [-1, -2, 12, 3]
    assert raw["bounds_source"] == "Trace.Extrema.MinX/MinY/MaxX/MaxY(EPcbUnitMM4)"
    assert M._trace_bounds(raw) == raw["bounds_mm"]
    raw.pop("bounds_mm")
    with pytest.raises(KeyError):
        M._trace_bounds(raw)


def test_diagonal_trace_actual_extrema_boundary_contact_is_not_separation():
    doc, state, request, adapter, engine = fixture(foreign=True)
    raw = [[0, 10], [0, 10], [0, 0]]
    doc.Traces[0].Geometry.values[1] = raw
    doc.Traces[0].values[7].values = {1: -0.2, 2: -0.2, 3: 10.2, 4: 10.2}
    state["routing_objects"][0]["geometry"]["path"] = [[0, 0, 0], [10, 10, 0]]
    engine.paths[(digest(raw), 0.254)] = {(x, x) for x in range(11)}
    state["all_pin_pad_material"][-1]["material"]["pads"][0]["bounds_mm"] = [10.2, 10, 12, 12]
    engine.touch_result = True
    proof = M.evaluate(doc, state, request, adapter)
    assert proof["complete"], proof
    pair = proof["foreign_intersections"][0]
    assert pair["intersection_area_mm2"] == 0 and pair["intersection_is_empty"]
    assert not pair["strict_bounds_separated"]
    state["native_mask_proof"] = proof
    state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
    assert not M.verify_evidence(state, request)["valid"]


def test_circle_foreign_pad_actual_bounds_tangent_and_zero_area_is_rejected():
    doc, state, request, adapter, engine = fixture(foreign=True)
    pad = state["all_pin_pad_material"][-1]["material"]["pads"][0]
    pad["geometry"][0]["outer"] = {"kind": "circle", "center_mm": [11.127, 0], "radius_mm": 1}
    pad["bounds_mm"] = [10.127, -1, 12.127, 1]
    engine.touch_result = True
    proof = M.evaluate(doc, state, request, adapter)
    assert proof["complete"], proof
    pair = proof["foreign_intersections"][0]
    assert pair["intersection_area_mm2"] == 0 and pair["intersection_is_empty"]
    assert not pair["strict_bounds_separated"]
    state["native_mask_proof"] = proof
    state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
    assert not M.verify_evidence(state, request)["valid"]


def three_pin_material_fixture():
    """Actual adapter chain with explicitly registered synthetic native material.

    The registered cell sets are test witnesses, not a replacement geometry
    implementation or evidence of the vendor's physical precision.
    """
    doc, state, request, adapter, engine = fixture(foreign=True)
    third = array(3, 8, 8, 13)
    engine.filled[digest(third)] = cells(3, 8, 8, 13)
    state["all_pin_pad_material"].append(
        {
            "component_uid": "c4",
            "pin_uid": "p4",
            "refdes": "C1",
            "pin": "1",
            "center_mm": [5, 10],
            "net": "A",
            "net_uid": "n1",
            "material": material(contour(third)),
        }
    )
    add_graph_trace(
        doc,
        state,
        engine,
        "t2",
        [[5, 5], [0, 10], [0, 0]],
        cells(5, 0, 6, 11),
        [4.873, -0.127, 5.127, 10.127],
    )
    return doc, state, request, adapter, engine


def add_graph_trace(doc, state, engine, uid, raw, occupied, bounds, *, net="A", layer=1):
    geometry = NS(IsValid=lambda: True, IsArced=lambda: False, values={1: raw, 5: 0.254})
    extrema = NS(IsValid=lambda: True, values=dict(zip((1, 2, 3, 4), bounds, strict=True)))
    doc.Traces.append(
        NS(
            UniqueId=uid,
            Net=NS(Name=net, UniqueId="n1" if net == "A" else "n2"),
            Layer=layer,
            Geometry=geometry,
            values={7: extrema},
        )
    )
    engine.paths[(digest(raw), 0.254)] = occupied
    model = {
        "uid": uid,
        "kind": "trace",
        "net": net,
        "layer": layer,
        "width": 0.254,
        "path": [list(row) for row in zip(*raw, strict=True)],
    }
    state["routing_objects"].append(
        {"kind": "trace", "uid": uid, "net": net, "geometry": _canonical(model)}
    )


def evaluate_graph(fixture_value):
    doc, state, request, adapter, engine = fixture_value
    proof = M.evaluate(doc, state, request, adapter)
    state["native_mask_proof"] = proof
    state["native_trace_material"] = copy.deepcopy(proof["raw_trace_inventory"])
    return proof, M.verify_material_graph(state, request)


def test_three_pin_graph_accepts_true_full_material_middle_T_junction():
    fixture_value = three_pin_material_fixture()
    doc, state, request, _, _ = fixture_value
    # No endpoint of the horizontal trunk is the branch's (5,0) junction.
    assert all([5, 0] != [row[i] for row in doc.Traces[0].Geometry.values[1][:2]] for i in (0, -1))
    proof, graph = evaluate_graph(fixture_value)
    assert proof["complete"], proof
    assert M.verify_evidence(state, request)["valid"]
    assert graph["valid"] and graph["all_members_connected"], graph
    assert len(graph["nodes"]) == 5 and len(graph["trace_components"]) == 1
    assert len(proof["target_trace_intersections"]) == 1
    assert {row["pin_uid"] for row in graph["nodes"] if row["kind"] == "pin"} == {"p1", "p2", "p4"}
    assert any(row["kind"] == "trace_trace" for row in graph["edges"])


@pytest.mark.parametrize(
    "fault",
    [
        "material_gap",
        "missing_third_contact",
        "wrong_layer",
        "wrong_net",
        "isolated_copper",
        "unsupported_curve",
        "hole_only",
        "cutout_only",
    ],
)
def test_three_pin_graph_rejects_actual_adapter_material_failures(fault):
    doc, state, request, adapter, engine = fixture_value = three_pin_material_fixture()
    branch = doc.Traces[1]
    raw = branch.Geometry.values[1]
    if fault == "material_gap":
        # Full-width lower cap is .128mm while trunk ends at .127mm:
        # the .001mm material gap is not repaired by centerline heuristics.
        raw[1][0] = 0.255
        branch.values[7].values[2] = 0.128
        state["routing_objects"][1]["geometry"]["path"][0][1] = 0.255
        engine.paths[(digest(raw), 0.254)] = cells(5, 1, 6, 11)
    elif fault == "missing_third_contact":
        raw[1][1] = 7
        branch.values[7].values[4] = 7.127
        state["routing_objects"][1]["geometry"]["path"][1][1] = 7
        engine.paths[(digest(raw), 0.254)] = cells(5, 0, 6, 8)
    elif fault == "wrong_layer":
        branch.Layer = 2
        state["routing_objects"][1]["geometry"]["layer"] = 2
    elif fault == "wrong_net":
        branch.Net = NS(Name="B", UniqueId="n2")
        state["routing_objects"][1].update(net="B")
        state["routing_objects"][1]["geometry"]["net"] = "B"
    elif fault == "isolated_copper":
        add_graph_trace(
            doc,
            state,
            engine,
            "t-isolated",
            [[20, 25], [20, 20], [0, 0]],
            cells(20, 20, 26, 21),
            [19.873, 19.873, 25.127, 20.127],
        )
    elif fault == "unsupported_curve":
        branch.Geometry.IsArced = lambda: True
    else:
        pin = state["all_pin_pad_material"][0]["material"]
        outer = pin["pads"][0]["geometry"][0]["outer"]
        engine.filled[digest(outer["points_array_mm"])] = {(0, 0), (-2, -2)}
        if fault == "hole_only":
            pin["holes"] = [{"center_mm": [0, 0], "diameter_mm": 1, "plated": True}]
        else:
            cut = array(-1, -1, 1, 1, arc=0.3)
            engine.filled[digest(cut)] = {(0, 0)}
            pin["pads"][0]["geometry"][0]["cutouts"] = [contour(cut, arced=True)]
    proof, graph = evaluate_graph(fixture_value)
    assert not graph["valid"], (fault, proof, graph)
    assert not graph["all_members_connected"]
    assert not engine.masks


@pytest.mark.parametrize(
    "fault",
    [
        "pair_missing",
        "pair_duplicate",
        "pair_reverse",
        "pair_digest",
        "pair_false_empty",
        "pair_nan",
        "missing_contact",
        "wrong_pin",
    ],
)
def test_three_pin_graph_receipt_requires_complete_bound_matrices(fault):
    fixture_value = three_pin_material_fixture()
    _, state, request, _, _ = fixture_value
    proof, graph = evaluate_graph(fixture_value)
    assert graph["valid"], graph
    row = proof["target_trace_intersections"][0]
    if fault == "pair_missing":
        proof["target_trace_intersections"] = []
    elif fault == "pair_duplicate":
        proof["target_trace_intersections"].append(copy.deepcopy(row))
    elif fault == "pair_reverse":
        row["left_trace_uid"], row["right_trace_uid"] = (
            row["right_trace_uid"],
            row["left_trace_uid"],
        )
    elif fault == "pair_digest":
        row["left_raw_trace_sha256"] = "changed"
    elif fault == "pair_false_empty":
        row["intersection_is_empty"] = True
    elif fault == "pair_nan":
        row["intersection_area_mm2"] = float("nan")
    elif fault == "missing_contact":
        proof["target_contacts"].pop()
    elif fault == "wrong_pin":
        proof["target_contacts"][0]["pin_uid"] = "wrong"
    assert not M.verify_material_graph(state, request)["valid"]


def test_touch_fallback_accepts_actual_separation_despite_overlapping_bounds():
    fixture_value = fixture(foreign=True)
    _, state, request, _, engine = fixture_value
    state["all_pin_pad_material"][-1]["material"]["pads"][0]["bounds_mm"] = [-1, -1, 32, 32]
    engine.touch_result = False
    proof, graph = evaluate_graph(fixture_value)
    assert proof["complete"] and graph["valid"], (proof, graph)
    foreign = proof["foreign_intersections"][0]
    assert foreign["strict_bounds_separated"] is False
    assert foreign["boundary_touch"] is False
    assert len(proof["mask_touch_controls"]) == 5
    assert M.verify_evidence(state, request)["valid"]


@pytest.mark.parametrize(
    "fault", ["control_touch", "foreign_touch", "foreign_non_bool", "foreign_exception"]
)
def test_touch_controls_or_indeterminate_foreign_boundary_fail_closed(fault):
    fixture_value = fixture(foreign=True)
    _, state, _, _, engine = fixture_value
    state["all_pin_pad_material"][-1]["material"]["pads"][0]["bounds_mm"] = [-1, -1, 32, 32]
    if fault == "control_touch":
        engine.control_touch_fault = True
    elif fault == "foreign_touch":
        engine.touch_result = True
    elif fault == "foreign_non_bool":
        engine.touch_result = 0
    else:
        engine.touch_failure = True
    proof, graph = evaluate_graph(fixture_value)
    assert not graph["valid"], (proof, graph)
    assert not engine.masks


@pytest.mark.parametrize(
    "fault",
    ["missing", "duplicate", "order", "source", "raw", "touch", "area", "empty", "non_bool"],
)
def test_touch_control_receipt_cannot_trust_producer_complete_marker(fault):
    fixture_value = fixture(foreign=True)
    _, state, request, _, _ = fixture_value
    proof, graph = evaluate_graph(fixture_value)
    assert graph["valid"]
    rows = proof["mask_touch_controls"]
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif fault == "order":
        rows.reverse()
    elif fault == "source":
        proof["mask_touch_formal_source"]["type_library_sha256"] = "wrong"
    elif fault == "raw":
        rows[1]["right"]["points_array_mm"][0][0] += 1
    elif fault == "touch":
        rows[1]["actual_touch"] = False
    elif fault == "area":
        rows[1]["area_mm2"] = 0.01
    elif fault == "empty":
        rows[1]["is_empty"] = False
    else:
        rows[1]["actual_touch"] = 1
    assert not M.verify_evidence(state, request)["valid"]


def test_three_pin_graph_clips_actual_terminal_hole_before_all_material_edges():
    fixture_value = three_pin_material_fixture()
    _, state, request, _, _ = fixture_value
    state["all_pin_pad_material"][0]["material"]["holes"] = [
        {"center_mm": [0, 0], "diameter_mm": 1, "plated": True}
    ]
    proof, graph = evaluate_graph(fixture_value)
    assert graph["valid"], (proof, graph)
    clipped = {row["trace_uid"]: row for row in proof["trace_materials"]}
    assert clipped["t1"]["raw_area_mm2"] > clipped["t1"]["material_area_mm2"]
    row = clipped["t1"]["hole_intersections"][0]
    assert row["terminal_crop"]["kind"] == "strict_terminal_round_hole_crop"
    assert row["terminal_crop"]["end_index"] == 0
    assert clipped["t2"]["hole_intersections"][0]["area_mm2"] == 0
    assert M.verify_evidence(state, request)["valid"]


def test_middle_T_meeting_only_in_drill_void_is_not_a_material_connection():
    doc, state, request, adapter, engine = fixture_value = three_pin_material_fixture()
    state["all_pin_pad_material"][0]["material"]["holes"] = [
        {"center_mm": [0, 0], "diameter_mm": 1, "plated": True}
    ]
    paths = [
        ([[-10, 10], [0, 0], [0, 0]], cells(-10, 0, 11, 1)),
        ([[0, 0], [0, 10], [0, 0]], cells(0, 0, 1, 11)),
    ]
    for index, (raw, occupied) in enumerate(paths):
        doc.Traces[index].Geometry.values[1] = raw
        doc.Traces[index].values[7].values = {
            1: min(raw[0]) - 0.127,
            2: min(raw[1]) - 0.127,
            3: max(raw[0]) + 0.127,
            4: max(raw[1]) + 0.127,
        }
        state["routing_objects"][index]["geometry"]["path"] = [
            list(row) for row in zip(*raw, strict=True)
        ]
        engine.paths[(digest(raw), 0.254)] = occupied
    proof, graph = evaluate_graph(fixture_value)
    assert not graph["valid"] and not proof["complete"]
    assert "away from its exact single endpoint" in proof["errors"][0]["reason"]
    # The complete full-width paths overlap, but the overlap witness lies in the
    # actual drill cell and must never turn into a trace/trace material edge.
    assert (paths[0][1] & paths[1][1]) == {(0, 0)}
    assert proof["trace_materials"][0]["hole_intersections"][0]["area_mm2"] > 0
    assert not engine.masks


@pytest.mark.parametrize("fault", ["reenter", "too_short", "too_wide"])
def test_terminal_round_hole_crop_requires_proven_single_remaining_path(fault):
    doc, state, request, adapter, engine = fixture_value = fixture(hole=True, foreign=True)
    trace = doc.Traces[0]
    if fault == "reenter":
        raw = [[0, 2, 0], [0, 0, 0.2], [0, 0, 0]]
        occupied = cells(0, 0, 3, 1)
    elif fault == "too_short":
        raw = [[0, 0.4], [0, 0], [0, 0]]
        occupied = {(0, 0)}
    else:
        raw = trace.Geometry.values[1]
        occupied = cells(0, 0, 11, 1)
        trace.Geometry.values[5] = state["routing_objects"][0]["geometry"]["width"] = 2
    trace.Geometry.values[1] = raw
    trace.values[7].values = {1: -1, 2: -1, 3: 12, 4: 3}
    state["routing_objects"][0]["geometry"]["path"] = [list(row) for row in zip(*raw, strict=True)]
    engine.paths[(digest(raw), trace.Geometry.values[5])] = occupied
    proof, graph = evaluate_graph(fixture_value)
    assert not graph["valid"] and not proof["complete"], (proof, graph)
    assert not engine.masks


def test_two_terminal_crops_cannot_leave_unproven_disconnected_trace_slivers():
    fixture_value = fixture(hole=True, foreign=True)
    _, state, _, _, engine = fixture_value
    state["all_pin_pad_material"][0]["material"]["holes"][0]["diameter_mm"] = 10
    state["all_pin_pad_material"][1]["material"]["holes"] = [
        {"center_mm": [10, 0], "diameter_mm": 10, "plated": True}
    ]
    proof, graph = evaluate_graph(fixture_value)
    assert not graph["valid"] and not proof["complete"], (proof, graph)
    assert "terminal-hole neighborhoods" in proof["errors"][0]["reason"]
    assert not engine.masks


@pytest.mark.parametrize(
    "fault",
    [
        "inventory",
        "digest",
        "missing_trace",
        "missing_hole",
        "duplicate_hole",
        "crop",
        "area",
        "raw_binding",
    ],
)
def test_physical_void_receipt_requires_bound_inventory_and_complete_cut_matrix(fault):
    fixture_value = fixture(hole=True, foreign=True)
    _, state, request, _, _ = fixture_value
    proof, graph = evaluate_graph(fixture_value)
    assert graph["valid"], (proof, graph)
    row = proof["trace_materials"][0]
    if fault == "inventory":
        proof["round_holes"][0]["diameter_mm"] = 2
    elif fault == "digest":
        row["round_holes_sha256"] = "wrong"
    elif fault == "missing_trace":
        proof["trace_materials"] = []
    elif fault == "missing_hole":
        row["hole_intersections"] = []
    elif fault == "duplicate_hole":
        row["hole_intersections"].append(copy.deepcopy(row["hole_intersections"][0]))
    elif fault == "crop":
        row["hole_intersections"][0]["terminal_crop"]["end_index"] = 1
    elif fault == "area":
        row["material_area_mm2"] = row["raw_area_mm2"] + 1
    else:
        row["raw_trace_sha256"] = "wrong"
    assert not M.verify_evidence(state, request)["valid"]


def test_nonconvex_touch_control_uses_native_rectangle_factory_and_union():
    fixture_value = fixture(foreign=True)
    _, state, request, _, engine = fixture_value
    proof, graph = evaluate_graph(fixture_value)
    assert graph["valid"], (proof, graph)
    control = proof["mask_touch_controls"][4]
    construction = control["native_construction"]
    assert control["left"]["kind"] == "native_rectangle_union"
    assert [row["bounds_mm"] for row in construction["rectangles"]] == [
        [0, 0, 3, 1],
        [0, 0, 1, 3],
        [2, 2, 2.5, 2.5],
    ]
    assert [row["area_mm2"] for row in construction["rectangles"]] == [3, 3, 0.25]
    assert construction["left_union_area"]["area_mm2"] == 5
    assert [row["actual"] for row in construction["left_contains_points"]] == [
        True,
        True,
        True,
        False,
    ]
    assert [call for call in engine.calls if call[0] == "CreateRectXYR"] == [
        ("CreateRectXYR", 0, 0, 3, 1),
        ("CreateRectXYR", 0, 0, 1, 3),
        ("CreateRectXYR", 2, 2, 2.5, 2.5),
    ]
    assert control["actual_touch"] is False and control["area_mm2"] == 0
    assert M.verify_evidence(state, request)["valid"]


@pytest.mark.parametrize(
    "fault", ["factory_exception", "factory_malformed", "union_area", "union_point"]
)
def test_native_rectangle_union_control_failure_preserves_observation_and_cleanup(fault):
    fixture_value = fixture(foreign=True)
    _, _, _, _, engine = fixture_value
    if fault == "factory_exception":
        engine.control_factory_failure = True
    elif fault == "factory_malformed":
        engine.control_factory_malformed = True
    elif fault == "union_area":
        engine.control_union_area_fault = True
    else:
        engine.control_union_contains_fault = True
    proof, graph = evaluate_graph(fixture_value)
    assert not graph["valid"] and not proof["complete"]
    assert len(proof["mask_touch_controls"]) == 5
    assert "native_construction" in proof["mask_touch_controls"][4]
    assert not engine.masks


@pytest.mark.parametrize("fault", ["source", "factory_raw", "factory_area", "union_contains"])
def test_native_rectangle_union_receipt_rejects_construction_or_result_drift(fault):
    fixture_value = fixture(foreign=True)
    _, state, request, _, _ = fixture_value
    proof, graph = evaluate_graph(fixture_value)
    assert graph["valid"]
    construction = proof["mask_touch_controls"][4]["native_construction"]
    if fault == "source":
        construction["source"]["operation"] = 1
    elif fault == "factory_raw":
        construction["rectangles"][0]["points_array_mm"][0][0] += 0.1
    elif fault == "factory_area":
        construction["rectangles"][0]["area_mm2"] = 2.9
    else:
        construction["left_contains_points"][3]["actual"] = True
    assert not M.verify_evidence(state, request)["valid"]
