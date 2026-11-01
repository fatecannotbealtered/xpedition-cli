"""Bounded surroundings and final gates; no CAD, SDK or restricted vendor access."""

import copy
from fractions import Fraction as F
from types import SimpleNamespace

import pytest

from xpedition_cli import native_silk_environment as native
from xpedition_cli import silk_qualification as q


@pytest.mark.parametrize(
    "gap,moved_b,expected",
    [
        (0.200015, False, "margin_satisfied"),
        (0.200015, True, "insufficient_or_uncertain"),
        (0.200005, False, "insufficient_or_uncertain"),
        (0.200025, True, "margin_satisfied"),
    ],
)
def test_inter_text_distance_flows_through_pen_and_each_moving_side(gap, moved_b, expected):
    shape = {"edges": [((F(0), F(gap)), (F(1), F(gap)))], "pen": 0.1}
    result = q.compare_stroke_obstacle(
        [((F(0), F(0)), (F(1), F(0)))],
        0.1,
        shape,
        moved_a=True,
        moved_b=moved_b,
        required_gap_mm=0.1,
        budget={"pairs": 0},
    )
    assert result["gap_assessment"]["status"] == expected


@pytest.mark.parametrize(
    "edge,relation",
    [
        (((1, 0), (2, 1)), "touch"),
        (((0.5, -1), (0.5, 1)), "cross"),
        (((0.5, 0), (2, 0)), "overlap"),
    ],
)
def test_between_object_endpoint_crossing_and_overlap_never_hide_in_geometry_valid(edge, relation):
    shape = {"edges": [tuple((F(x), F(y)) for x, y in edge)], "pen": 0.1}
    result = q.compare_stroke_obstacle(
        [((F(0), F(0)), (F(1), F(0)))],
        0.1,
        shape,
        moved_a=True,
        moved_b=False,
        required_gap_mm=0,
        budget={"pairs": 0},
    )
    assert relation in result["contacts"]
    assert result["stroke_bodies_contact_or_uncertain"]
    assert result["gap_assessment"]["status"] == "insufficient_or_uncertain"


def environment():
    inventories = {
        name: {"complete": True, "rows": [], "count": 0} for name in q.REQUIRED_INVENTORIES
    }
    inventories["board_outline"] = {
        "complete": True,
        "geometry": {
            "unit": "mm",
            "closed": True,
            "filled": False,
            "pen_mm": 0.0,
            "cutouts": [],
            "path_mm": [[-2, -2, 0], [3, -2, 0], [3, 3, 0], [-2, 3, 0], [-2, -2, 0]],
        },
    }
    for uid, y in (("1", 0.0), ("2", 1.0)):
        inventories["texts"]["rows"].append(
            {
                "id": "text:" + uid,
                "semantic": {"uid": uid, "type": 2, "side": "top", "side_code": 1},
                "raw": {"stroke_array_mm": [0.1, 0.0, y, 0, 1.0, y, 2], "extents_mm": [0, y, 1, y]},
            }
        )
    inventories["texts"]["count"] = 2
    frame = {
        "inventories": inventories,
        "layer_count": 4,
        "rule_observations": [
            {"status": "observed_only", "raw_value_mm": 0.0, "applicability_proven": True}
        ],
    }
    return {"before": copy.deepcopy(frame), "after": frame, "repeat_exact": True}


def test_real_numeric_zero_or_json_applicability_flag_does_not_resolve_unknown_rule():
    result = q.evaluate(environment(), ["1"])
    assert result["native_input_coverage"]["complete"]
    assert result["rule_coverage"]["complete"] is False
    assert all(
        p["gap_assessment"]["status"] == "unverified"
        for p in result["clearance_protection"]["pairs"]
    )
    assert not result["write_eligibility"]["eligible"]


def test_repeat_exact_boolean_cannot_mask_a_changed_foreign_text():
    value = environment()
    value["after"]["inventories"]["texts"]["rows"][1]["raw"]["stroke_array_mm"][1] += 0.001
    result = q.evaluate(value, ["1"])
    assert "native_environment_not_repeat_exact" in result["native_input_coverage"]["issues"]


def test_four_prerequisites_remain_independent_and_activation_never_follows_from_valid():
    values = [{"valid": True}, {"complete": True}, {"complete": True}, {"status": "satisfied"}]
    good = q.decision(*values)
    assert good["conditions_complete"] and not good["eligible"]
    assert good["blockers"] == ["public_draft_inactive"]
    for index in range(4):
        bad = copy.deepcopy(values)
        bad[index] = {}
        report = q.decision(*bad)
        assert not report["conditions_complete"] and not report["eligible"]
        assert sum(not passed for passed in report["gates"].values()) == 1


def test_failed_native_geometry_attempt_cannot_restart_work_after_point_budget(monkeypatch):
    monkeypatch.setattr(native, "MAX_POINTS", 2)
    calls = []
    obj = SimpleNamespace(
        IsValid=lambda: True, IsClosed=lambda: True, Filled=False, IsCircle=lambda: False
    )

    def prop(obj, dispid, unit):
        calls.append(dispid)
        return 0.1 if dispid == 5 else [[0, 1], [0, 0], [0, 0]]

    bridge = SimpleNamespace(_propget=prop)
    budget = {"points": 1, "objects": 0, "exhausted": False}
    with pytest.raises(ValueError, match="points budget"):
        native._geometry(obj, bridge, budget)
    before = len(calls)
    with pytest.raises(ValueError, match="already exhausted"):
        native._geometry(obj, bridge, budget)
    assert len(calls) == before
