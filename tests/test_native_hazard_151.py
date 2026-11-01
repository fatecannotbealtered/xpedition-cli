"""151 hazard PROPERTYGET adaptation; no CAD or rule changes."""

from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as A


@pytest.mark.parametrize("count", [-1, None, True, 10001])
def test_unread_hazard_count_never_becomes_an_observed_empty_collection(count):
    collection = SimpleNamespace(Count=count)
    doc = SimpleNamespace(GetHazards=lambda kind: collection)
    with pytest.raises(A.AdapterError) as caught:
        A._hazards(doc, SimpleNamespace(), 3)
    assert caught.value.code == "E_NATIVE_FAILURE"
    assert caught.value.details["hazards_read"] is False
    assert caught.value.details["count"] is None
    assert caught.value.details["write_attempted"] is False


class Collection(list):
    @property
    def Count(self):
        return len(self)

    def Item(self, index):
        return self[index - 1]


def typed(name, **kwargs):
    info = SimpleNamespace(GetDocumentation=lambda n: (name, None, 0, None))
    return SimpleNamespace(_oleobj_=SimpleNamespace(GetTypeInfo=lambda: info), **kwargs)


def native_pair(monkeypatch, fault=None):
    """Actual adapter-to-material read chain with native-shaped failures."""
    component = SimpleNamespace(values={86: "J1", 87: 104})
    extrema = SimpleNamespace(IsValid=lambda: True, values={1: 4.23, 2: 29.63, 3: 5.93, 4: 31.33})
    geometry = SimpleNamespace(
        IsValid=lambda: True,
        IsCircle=lambda: False,
        IsClosed=lambda: True,
        IsArced=lambda: False,
        values={
            1: [[4.23, 5.93, 5.93, 4.23, 4.23], [29.63, 29.63, 31.33, 31.33, 29.63], [0] * 5],
            21: True,
            22: Collection(),
        },
    )
    pad = SimpleNamespace(
        Name="RECT1.7X1.7",
        Layer=1,
        IsNegative=lambda: False,
        IsNoConnect=lambda: False,
        values={4: Collection([geometry]), 7: extrema},
    )
    hole = SimpleNamespace(
        IsValid=lambda: True, ShapeType=1, Plated=True, values={1: 1.0, 4: 5.08, 5: 30.48}
    )
    pin = typed(
        "IMGCPCBPin",
        IsSMD=lambda: False,
        values={
            0: "1",
            2: 5.08,
            3: 30.48,
            8: SimpleNamespace(Name="A_IN"),
            16: component,
            20: Collection([pad]),
            25: Collection([hole]),
            64: 108,
        },
    )
    placement_geometry = SimpleNamespace(
        values={
            1: [[3.87, 6.29, 6.29, 3.87, 3.87], [26.84, 26.84, 32.3, 32.3, 26.84], [0] * 5],
            22: Collection(),
        }
    )
    placement_extrema = SimpleNamespace(
        IsValid=lambda: True, values={1: 3.87, 2: 26.84, 3: 6.29, 4: 32.3}
    )
    outline = typed(
        "IMGCPCBPlacementOutline",
        values={5: component, 7: placement_extrema, 8: placement_geometry, 31: 1, 42: 105},
    )
    hazard = SimpleNamespace(
        Type=67,
        Description="not used for geometry",
        Accepted=False,
        Objects=Collection([pin, outline]),
        values={9: 4.23, 10: 29.63, 11: 5.08, 12: 30.48, 13: 3.87, 14: 26.84, 16: 0.0, 17: 0.0},
    )
    if fault == "pad_get_failure":
        pin.values[20] = RuntimeError("Pads getter failed")
    elif fault == "negative_pad":
        pad.IsNegative = lambda: True
    elif fault == "wrong_pad_layer":
        pad.Layer = 2
    elif fault == "outline_arc":
        placement_geometry.values[1][2][0] = 0.2
    elif fault == "outline_cutout":
        placement_geometry.values[22].append(SimpleNamespace())
    elif fault == "outline_invalid_extrema":
        placement_extrema.IsValid = lambda: False
    elif fault == "outline_wrong_owner":
        outline.values[5] = SimpleNamespace(values={86: "J2", 87: 999})
    elif fault == "outline_missing_uid":
        outline.values.pop(42)
    calls = []

    def propget(owner, dispid, *args):
        calls.append((owner, dispid, args))
        if owner is pin and dispid == 20:
            assert args == (1, True)
        value = owner.values[dispid]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(A, "_propget", propget)
    return hazard, pin, outline, calls


def test_native_shaped_THT_and_outline_read_chain_is_reachable_without_fake_getters(monkeypatch):
    from test_drc_proximity_records import PROXIMITY

    from xpedition_cli import drc_engine as D
    from xpedition_cli import library_read as R

    hazard, pin, outline, calls = native_pair(monkeypatch)
    result = A._hazard_record(hazard, {67: "Proximity"})
    p, o = result["object_details"]
    assert p["position_mm"] == [5.08, 30.48] and p["layer"] is None
    assert p["layer_material_observation"]["complete"] and not p["is_smd"]
    assert p["layer_material_observation"]["pads"][0]["layer"] == 1
    assert o["position_mm"] is None and o["placement_geometry_observation"]["complete"]
    assert o["placement_geometry_observation"]["rectangle_min_corner_mm"] == [3.87, 26.84]
    assert not any(owner is pin and dispid == 48 for owner, dispid, _ in calls)
    assert {dispid for owner, dispid, _ in calls if owner is outline} == {5, 7, 8, 31, 42}
    assert D.compare_hazards([D.violation_record(R.parse_records(PROXIMITY)[0])], [result])["valid"]


@pytest.mark.parametrize(
    "fault",
    [
        "pad_get_failure",
        "negative_pad",
        "wrong_pad_layer",
        "outline_arc",
        "outline_cutout",
        "outline_invalid_extrema",
        "outline_wrong_owner",
        "outline_missing_uid",
    ],
)
def test_actual_adapter_failure_paths_preserve_unknown_and_refuse_matching(monkeypatch, fault):
    from test_drc_proximity_records import PROXIMITY

    from xpedition_cli import drc_engine as D
    from xpedition_cli import library_read as R

    hazard, _, _, _ = native_pair(monkeypatch, fault)
    observed = A._hazard_record(hazard, {67: "Proximity"})
    result = D.compare_hazards([D.violation_record(R.parse_records(PROXIMITY)[0])], [observed])
    assert not result["valid"] and not result["matches"]


@pytest.mark.parametrize("fault", [None, "missing", "negative", "nan", "bool"])
def test_formal_proximity_properties_preserve_units_and_missing_evidence(monkeypatch, fault):
    values = {9: 0.1, 10: 0.2, 11: 1.25, 12: 2.5, 13: 3.75, 14: 4.5, 16: 0.01234567, 17: 0.127}
    calls = []

    def propget(obj, dispid, unit):
        calls.append((dispid, unit))
        assert obj is hazard and unit == 4
        if dispid == 16:
            if fault == "missing":
                raise RuntimeError("synthetic unavailable")
            if fault == "negative":
                return -1
            if fault == "nan":
                return float("nan")
            if fault == "bool":
                return True
        return values[dispid]

    hazard = SimpleNamespace(Type=67, Description="retained", Accepted=False, Objects=[])
    monkeypatch.setattr(A, "_propget", propget)
    record = A._hazard_record(hazard, {67: "Proximity"})
    proof = record["proximity_observation"]
    assert calls == [(n, 4) for n in (9, 10, 11, 12, 13, 14, 16, 17)]
    assert proof["source"] == "IMGCPCBHazard151.propertyget" and proof["unit"] == "mm"
    assert proof["first_object_position_mm"] == [1.25, 2.5]
    assert proof["second_object_position_mm"] == [3.75, 4.5]
    assert proof["complete"] is (fault is None)
    assert bool(proof["errors"]) is (fault is not None)
    if fault is None:
        assert record["actual"] == 0.01234567 and record["required"] == 0.127
    elif fault == "negative":
        assert record["actual"] == -1
    else:
        assert record["actual"] is None


def test_report_or_description_never_fills_missing_formal_position(monkeypatch):
    def propget(obj, dispid, unit):
        raise RuntimeError("synthetic absent getter")

    monkeypatch.setattr(A, "_propget", propget)
    hazard = SimpleNamespace(Type=67, Description="(1,2) CLEARANCE0", Objects=[], Accepted=False)
    record = A._hazard_record(hazard, {67: "Proximity"})
    assert record["proximity_observation"]["first_object_position_mm"] == [None, None]
    assert len(record["proximity_observation"]["errors"]) == 6
    assert record["proximity_observation"]["complete"] is False
