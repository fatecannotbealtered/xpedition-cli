"""Selected envelope observation precision; synthetic getters, no licensed CAD.

The before/after extrema reproduce R15/034 C2's retained native readings. The
1nm inventory difference did not change the existing four-decimal model. This
fixture proves the revised comparison, not a new native Move or persistence.
"""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_bounded_labels import Collection, Text
from test_bounded_labels import native as native

from xpedition_cli import board_labels as L
from xpedition_cli import native_com_adapter as A


@pytest.fixture
def observed_case(native, monkeypatch):
    doc, target, params = native
    target.UniqueId, target.TextString = "93", "C2"
    target.Component.UniqueId, target.Component.RefDes = "92", "C2"
    target.x, target.y = 31.68, 8.89
    target.Format.Orientation = 270.0
    target.Format.Font = "vf_std"
    original_extrema = Text.Extrema.fget

    def extrema(text):
        if text.UniqueId != "93":
            return original_extrema(text)
        # Exact retained native Extrema values before and after the-3mm move.
        values = (
            [31.22, 8.023939, 32.14, 9.756061]
            if text.y == 8.89
            else [31.22, 5.023939, 32.14, 6.756060]
        )
        return SimpleNamespace(
            GetMinX=lambda unit: values[0],
            GetMinY=lambda unit: values[1],
            GetMaxX=lambda unit: values[2],
            GetMaxY=lambda unit: values[3],
        )

    monkeypatch.setattr(Text, "Extrema", property(extrema))
    other_owner = SimpleNamespace(UniqueId="97", RefDes="C1", Placed=True, ObjectClass=1)
    other = Text("98", other_owner, doc)
    other.x, other.y = 20.0, 20.0
    other_owner.FabricationLayerTexts = Collection([other])
    doc.Components = Collection([target.Component, other_owner])
    doc.FabricationLayerTexts = Collection([target, other])
    move = params["label_moves"]["moves"][0]
    move.update(refdes="C2", text_uid="93", text="C2", from_=[31.68, 8.89], to=[31.68, 5.89])
    move["from"] = move.pop("from_")
    board = Path(params["project"])
    before = L.observe(doc, board, A)
    bound = L.bind(before, params["label_moves"], [], {"board": str(board)}, A)
    target.Move(31.68, 5.89, A.UNIT_MM)
    after = L.observe(doc, board, A)
    assert (doc.saves, doc.closes, doc.opens) == (0, 0, 0)
    return bound, after


def row(state, uid="93"):
    return next(item for item in state["texts"] if item["uid"] == uid)


def set_extent(state, index, value, *, uid="93"):
    target = row(state, uid)
    target["extents"][index] = value
    target["native_geometry_witness"]["extents_mm"][index] = repr(value)


def test_retained_native_shaped_1nm_counterexample_fails_old_inventory_and_passes_v2(observed_case):
    bound, actual = observed_case
    original = copy.deepcopy((bound, actual))
    old_prediction = L.translate(row(bound["before"]), 0, -3)
    old_observation = copy.deepcopy(row(actual))
    for item in (old_prediction, old_observation):
        item.pop("native_geometry_witness")
    assert old_prediction["extents"][3] == 6.756061
    assert old_observation["extents"][3] == 6.756060
    assert old_prediction != old_observation  # The original exact-inventory failure.
    assert (
        L.translated_geometry(
            bound["before"]["geometry"], bound["request"]["moves"], bound["before"]["texts"]
        )
        == actual["geometry"]
    )
    proof = L.compare(bound, actual)
    assert proof["valid"] and all(item["passed"] for item in proof["checks"])
    assert L.GUARD == "explicit_silk_translation_v2"
    assert (bound, actual) == original  # Both full native witnesses remain intact.
    assert L._witness_valid(row(actual))


@pytest.mark.parametrize("index,value", [(3, 6.756049), (1, 5.023951)])
def test_crossing_either_envelope_quantization_boundary_still_refuses(observed_case, index, value):
    bound, actual = observed_case
    set_extent(actual, index, value)
    assert L._witness_valid(row(actual))
    proof = L.compare(bound, actual)
    assert not proof["valid"]
    assert proof["checks"][1]["passed"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "size",
        "non_target_1nm",
        "non_target_raw_only",
        "position",
        "stroke",
        "format",
        "copper",
        "witness",
    ],
)
def test_quantization_does_not_ignore_other_fields_or_real_changes(observed_case, fault):
    bound, actual = observed_case
    if fault == "size":
        set_extent(actual, 2, row(actual)["extents"][2] + 0.01)
    elif fault == "non_target_1nm":
        set_extent(actual, 3, row(actual, "98")["extents"][3] + 0.000001, uid="98")
    elif fault == "non_target_raw_only":
        row(actual, "98")["native_geometry_witness"]["extents_mm"][0] = "19.000001"
    elif fault == "position":
        row(actual)["position"][0] += 0.000001
    elif fault == "stroke":
        target = row(actual)
        value = float(target["native_geometry_witness"]["stroke_array_mm"][1]) + 0.0001
        target["native_geometry_witness"]["stroke_array_mm"][1] = repr(value)
        _, target["strokes"] = A.stroke_rows(
            [float(value) for value in target["native_geometry_witness"]["stroke_array_mm"]]
        )
        assert L._witness_valid(target)
    elif fault == "format":
        row(actual)["format"]["orientation"] += 0.000001
    elif fault == "copper":
        actual["geometry"]["model"]["traces"].append({"uid": "unexpected-copper"})
    else:
        row(actual)["native_geometry_witness"]["extents_mm"][3] = "6.756061"
        assert not L._witness_valid(row(actual))
    assert not L.compare(bound, actual)["valid"]


def test_old_guard_refuses_before_move_or_persistence(native):
    doc, text, params = native
    preview = A._tidy_labels({**params, "apply": False}, None)
    with pytest.raises(A.AdapterError) as caught:
        A._tidy_labels(
            {
                **params,
                "apply": True,
                "state_guard": "explicit_silk_translation_v1",
                "state_digest": preview["state_digest"],
            },
            None,
        )
    assert caught.value.code == "E_CONFLICT" and caught.value.details["write_attempted"] is False
    assert text.calls == [] and (doc.saves, doc.closes, doc.opens) == (0, 0, 0)
