"""Offline review of the deliberately unwired derived-silk draft contract."""

from __future__ import annotations

import copy
import math
from fractions import Fraction

import pytest
from test_placement import material_state, observations
from test_placement_text_witness import semantics, text_board

from xpedition_cli import placement
from xpedition_cli import placement_silk_equivalence as draft
from xpedition_cli import placement_text_witness as witness
from xpedition_cli.errors import CLIError


def _add_text(state, component, *, uid, text, raw, anchor, height=0.8):
    raw = {"uid": uid, "refdes": component["refdes"] if component else None, **copy.deepcopy(raw)}
    material = {
        "uid": uid,
        "refdes": raw["refdes"],
        "text": text,
        "x": anchor[0],
        "y": anchor[1],
        "height": height,
        "orientation": 0.0,
        "mirrored": False,
        "side": component["side"] if component else "top",
        "kind": "refdes",
        **witness.projection(raw),
    }
    state["text_native_witness"].append(raw)
    state["model"]["texts"].append(material)
    semantic = semantics(material, component)
    semantic["fabrication_identity"].update(
        side_name=semantic["side_name"],
        basis="formal Type/Side/SideName; not a conductor-layer number",
    )
    state["text_semantic_witness"].append(semantic)


def _plan_and_after(rows, state, dx):
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": [rows[0]["refdes"]],
        "steps": [{"op": "translate", "dx": dx, "dy": 0.0}],
    }
    plan = placement.plan_placement(request, rows, protection_state=state)
    after = placement.expected_state(plan["protection_before"], plan["results"])
    return request, plan, after


def _draft(plan):
    value = copy.deepcopy(plan)
    value["state_guard"] = draft.DRAFT_GUARD
    return value


def _simple():
    rows = observations()
    state = material_state(rows)
    _add_text(
        state,
        state["components"][0],
        uid="fixture",
        text="R1",
        anchor=[0.0, 0.0],
        raw={
            "extents_mm": [-0.1, -0.1, 0.9, 0.1],
            "stroke_array_mm": [0.1, -0.1, 0.0, 0, 0.9, 0.0, 2],
        },
        height=1.0,
    )
    request, plan, after = _plan_and_after(rows, state, 0.1)
    assert after["text_native_witness"][0]["stroke_array_mm"][1] == 0.0
    return request, plan, after


def _refresh_projection(after, uid):
    raw = next(x for x in after["text_native_witness"] if x["uid"] == uid)
    next(x for x in after["model"]["texts"] if x["uid"] == uid).update(witness.projection(raw))


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_recorded_two_nm_sample_is_accepted_only_by_draft_and_retains_all_residuals():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_recorded_return_and_repeat_use_the_same_per_operation_contract():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.parametrize(
    "value,valid",
    [
        (math.nextafter(draft.VERTEX_BUDGET_MM, 0.0), True),
        # 1e-5's nearest binary64 value is slightly larger than exact 10 nm.
        (draft.VERTEX_BUDGET_MM, False),
        (math.nextafter(draft.VERTEX_BUDGET_MM, math.inf), False),
    ],
)
def test_single_vertex_exact_binary64_boundary(value, valid):
    _, plan, after = _simple()
    after["text_native_witness"][0]["stroke_array_mm"][1] = value
    _refresh_projection(after, "fixture")
    result = placement.verify_batch_state(_draft(plan), after)
    assert result["valid"] is valid
    proof = result["silk_engineering_equivalence"]
    assert len(proof["texts"][0]["point_residuals_mm_fraction"]) == 2


def test_exact_mathematical_ten_nm_boundary_is_inclusive():
    limit = Fraction(1, 100_000)
    assert draft._within_vertex_budget(limit, Fraction(0))
    assert draft._within_vertex_budget(limit - Fraction(1, 10**18), Fraction(0))
    assert not draft._within_vertex_budget(limit + Fraction(1, 10**18), Fraction(0))


def test_two_axis_changes_under_each_axis_budget_can_exceed_euclidean_budget():
    _, plan, after = _simple()
    raw = after["text_native_witness"][0]["stroke_array_mm"]
    raw[1] = raw[2] = draft.VERTEX_BUDGET_MM * 0.8
    _refresh_projection(after, "fixture")
    result = placement.verify_batch_state(_draft(plan), after)
    assert not result["valid"]
    assert "euclidean vertex budget exceeded" in result["difference"]["reason"]


@pytest.mark.parametrize(
    "value,valid",
    [
        (math.nextafter(draft.VERTEX_BUDGET_MM, 0.0), True),
        (draft.VERTEX_BUDGET_MM, False),
        (math.nextafter(draft.VERTEX_BUDGET_MM, math.inf), False),
    ],
)
def test_extent_coordinate_has_its_own_fixed_absolute_bound(value, valid):
    _, plan, after = _simple()
    assert after["text_native_witness"][0]["extents_mm"][0] == 0.0
    after["text_native_witness"][0]["extents_mm"][0] = value
    _refresh_projection(after, "fixture")
    assert placement.verify_batch_state(_draft(plan), after)["valid"] is valid


def test_small_real_geometry_change_is_explicitly_indistinguishable_inside_budget():
    _, plan, after = _simple()
    after["text_native_witness"][0]["stroke_array_mm"][1] = 0.000005
    _refresh_projection(after, "fixture")
    result = placement.verify_batch_state(_draft(plan), after)
    assert result["valid"] is True
    assert result["silk_engineering_equivalence"]["clearance"]["status"] == "unverified"
    # Geometry alone cannot establish whether this sub-budget change was intended.


def test_two_operations_each_have_own_budget_not_a_cumulative_bound():
    request, first_plan, first_after = _simple()
    first_after["text_native_witness"][0]["stroke_array_mm"][1] = 0.000009
    _refresh_projection(first_after, "fixture")
    first = placement.verify_batch_state(_draft(first_plan), first_after)
    assert first["valid"]
    second_rows = observations()
    second_rows[0]["x"] = first_plan["results"][0]["target"]["x"]
    second_plan = placement.plan_placement(request, second_rows, protection_state=first_after)
    second_after = placement.expected_state(
        second_plan["protection_before"], second_plan["results"]
    )
    second_raw = second_after["text_native_witness"][0]["stroke_array_mm"]
    second_raw[1] += 0.000009
    _refresh_projection(second_after, "fixture")
    second = placement.verify_batch_state(_draft(second_plan), second_after)
    assert second["valid"]
    assert second_raw[1] - 0.1 > draft.VERTEX_BUDGET_MM
    assert "no cumulative drift guarantee" in second["silk_engineering_equivalence"]["budget_scope"]


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_real_semantic_changes_are_not_treated_as_derived_geometry():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_unverified_font_fails_even_when_observed_geometry_is_within_budget():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_fabrication_identity_must_positively_prove_actual_silkscreen_type():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_incomplete_silk_side_identity_is_not_a_valid_attestation():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_non_target_raw_one_ulp_is_still_rejected():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


def test_unchanged_selected_object_gets_no_engineering_allowance():
    request, plan, _ = _simple()
    request["steps"][0]["dx"] = 0.0
    rows = observations()
    unchanged_plan = placement.plan_placement(
        request, rows, protection_state=plan["protection_before"]
    )
    after = placement.expected_state(unchanged_plan["protection_before"], unchanged_plan["results"])
    after["text_native_witness"][0]["stroke_array_mm"][1] += 0.000002
    _refresh_projection(after, "fixture")
    assert not placement.verify_batch_state(_draft(unchanged_plan), after)["valid"]


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_unrelated_physical_material_keeps_original_protection():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_point_count_order_control_and_pen_changes_fail():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


def test_topology_rejects_new_crossing_closure_and_short_segments():
    before_cross = [0.1, 0.0, 0.0, 0, 1.0, 0.0, 2, 0.5, 0.000005, 0, 0.5, 1.0, 2]
    after_cross = copy.deepcopy(before_cross)
    after_cross[8] = -0.000005
    with pytest.raises(ValueError, match="intersection topology changed"):
        draft._topology(before_cross, after_cross)
    before_open = [0.1, 0.0, 0.0, 0, 1.0, 1.0, 1, 0.000005, 0.0, 2]
    after_closed = copy.deepcopy(before_open)
    after_closed[7] = 0.0
    with pytest.raises(ValueError, match="closure changed"):
        draft._topology(before_open, after_closed)
    too_short = [0.1, 0.0, 0.0, 0, 0.000015, 0.0, 2]
    with pytest.raises(ValueError, match="degenerate or budget-sized"):
        draft._topology(too_short, too_short)


def test_unproven_near_separation_cannot_be_silently_accepted():
    first = [0.1, 0.0, 0.0, 0, 1.0, 0.0, 2, 0.5, 0.000005, 0, 0.5, 1.0, 2]
    second = copy.deepcopy(first)
    second[8] = 0.000006
    with pytest.raises(ValueError, match="separation is within the uncertainty budget"):
        draft._topology(first, second)


def test_adjacent_backtrack_overlap_cannot_disappear_under_budget():
    before = [0.1, 0.0, 0.0, 0, 1.0, 0.0, 1, 0.5, 0.0, 1, 0.5, 1.0, 2]
    after = copy.deepcopy(before)
    after[8] = 0.000005
    with pytest.raises(ValueError, match="adjacent native stroke overlap topology changed"):
        draft._topology(before, after)


def test_new_crossing_inside_vertex_budget_is_rejected_by_full_draft_guard():
    rows = observations()
    state = material_state(rows)
    _add_text(
        state,
        state["components"][0],
        uid="crossing",
        text="R1",
        anchor=[0.0, 0.0],
        raw={
            "extents_mm": [-0.1, 0.0, 0.9, 1.0],
            "stroke_array_mm": [0.1, -0.1, 0.0, 0, 0.9, 0.0, 2, 0.4, 0.000004, 0, 0.4, 1.0, 2],
        },
        height=1.0,
    )
    _, plan, after = _plan_and_after(rows, state, 0.1)
    raw = after["text_native_witness"][0]
    raw["stroke_array_mm"][8] = -0.000004
    raw["extents_mm"][1] = -0.000004
    _refresh_projection(after, "crossing")
    result = placement.verify_batch_state(_draft(plan), after)
    assert not result["valid"]
    assert "intersection topology changed" in result["difference"]["reason"]


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_topology_resource_limit_refuses_instead_of_skipping():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_total_topology_resource_limit_refuses_instead_of_skipping():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.parametrize("failed_texts", [0, 1, 2, 3])
def test_total_pair_reservation_includes_texts_failing_on_the_last_pair(monkeypatch, failed_texts):
    rows = observations()
    state = material_state(rows)
    for index in range(3):
        _add_text(
            state,
            state["components"][0],
            uid=f"bounded-{index}",
            text="R1",
            anchor=[0.0, 0.0],
            height=1.0,
            raw={
                "extents_mm": [-10.0, -10.0, 1.0, 1.0],
                "stroke_array_mm": [
                    0.1,
                    -10.0,
                    -10.0,
                    0,
                    -9.0,
                    -10.0,
                    2,
                    0.0,
                    0.0,
                    0,
                    1.0,
                    0.0,
                    2,
                    0.5,
                    0.000004,
                    0,
                    0.5,
                    1.0,
                    2,
                ],
            },
        )
    _, plan, after = _plan_and_after(rows, state, 0.1)
    for index in range(failed_texts):
        after["text_native_witness"][index]["stroke_array_mm"][14] = -0.000004
        _refresh_projection(after, f"bounded-{index}")
    monkeypatch.setattr(draft, "MAX_TOPOLOGY_PAIRS_PER_TEXT", 3)
    monkeypatch.setattr(draft, "MAX_TOPOLOGY_PAIRS_TOTAL", 6)
    relations = []
    relation = draft._relation

    def observed_relation(*args):
        relations.append(args)
        return relation(*args)

    monkeypatch.setattr(draft, "_relation", observed_relation)
    result = placement.verify_batch_state(_draft(plan), after)
    assert not result["valid"]
    proof = result["silk_engineering_equivalence"]
    assert proof["topology_pairs"] == 6
    assert [t["topology_pairs_reserved"] for t in proof["texts"]] == [3, 3, 0]
    assert sum(
        "intersection topology changed" in issue["reason"] for issue in proof["issues"]
    ) == min(failed_texts, 2)
    assert (
        proof["issues"][-1]["reason"]
        == "native stroke topology exceeds the total comparison budget"
    )
    assert len(relations) == 2 * min(failed_texts, 2), "third text must not start pair work"


def test_pair_budget_exact_boundary_and_failure_reservation_are_not_refunded():
    budget = draft._TopologyBudget()
    before = [0.1, 0.0, 0.0, 0, 1.0, 0.0, 2, 0.5, 0.000005, 0, 0.5, 1.0, 2]
    after = copy.deepcopy(before)
    after[8] = -0.000005
    with pytest.raises(ValueError, match="intersection topology changed"):
        draft._topology(before, after, budget=budget)
    assert budget.reserved_pairs == 1
    budget.reserve(draft.MAX_TOPOLOGY_PAIRS_PER_TEXT)
    budget.reserve(draft.MAX_TOPOLOGY_PAIRS_PER_TEXT)
    budget.reserve(draft.MAX_TOPOLOGY_PAIRS_TOTAL - budget.reserved_pairs)
    assert budget.reserved_pairs == draft.MAX_TOPOLOGY_PAIRS_TOTAL
    with pytest.raises(ValueError, match="total comparison budget"):
        draft._topology(before, after, budget=budget)
    assert budget.reserved_pairs == draft.MAX_TOPOLOGY_PAIRS_TOTAL


def test_multiple_moved_texts_report_every_residual_and_global_maximum():
    rows, state, request = text_board()
    for sem in state["text_semantic_witness"]:
        sem["fabrication_identity"].update(
            side_name=sem["side_name"],
            basis="formal Type/Side/SideName; not a conductor-layer number",
        )
    plan = placement.plan_placement(request, rows, protection_state=state)
    after = placement.expected_state(plan["protection_before"], plan["results"])
    for uid, offset in (("1000", 0.000002), ("593", 0.000003)):
        raw = next(x for x in after["text_native_witness"] if x["uid"] == uid)
        raw["stroke_array_mm"][1] += offset
        _refresh_projection(after, uid)
    result = placement.verify_batch_state(_draft(plan), after)
    assert result["valid"]
    proof = result["silk_engineering_equivalence"]
    assert len(proof["texts"]) == 6
    assert all(len(row["point_residuals_mm_fraction"]) == 30 for row in proof["texts"])
    assert proof["max_vertex_distance_mm_approx"] == pytest.approx(0.000003, abs=1e-14)


@pytest.mark.parametrize(
    "distance,moved_a,moved_b,status",
    [
        (0.200009, True, False, "insufficient_or_uncertain"),
        (0.200011, True, False, "margin_satisfied"),
        (0.200019, True, True, "insufficient_or_uncertain"),
        (0.200021, True, True, "margin_satisfied"),
    ],
)
def test_stroke_clearance_uses_both_pen_widths_and_each_moved_side(
    distance, moved_a, moved_b, status
):
    result = draft.assess_observed_stroke_clearance(
        distance, 0.1, 0.1, 0.1, moved_a=moved_a, moved_b=moved_b
    )
    assert result["status"] == status
    assert result["budget_sides"] == int(moved_a) + int(moved_b)


def test_missing_clearance_is_explicitly_unverified():
    result = draft.assess_observed_stroke_clearance(
        None, 0.1, 0.1, 0.1, moved_a=True, moved_b=False
    )
    assert result["status"] == "unverified"


def test_clearance_equal_to_required_gap_is_not_a_margin_proof():
    result = draft.assess_observed_stroke_clearance(
        0.25, 0.125, 0.125, 0.125, moved_a=False, moved_b=False
    )
    assert result["guaranteed_gap_mm_fraction"] == result["required_gap_mm_fraction"]
    assert result["status"] == "insufficient_or_uncertain"


def test_draft_cannot_be_substituted_for_a_public_preview_or_native_apply():
    from xpedition_cli.native_placement import run

    request, plan, _ = _simple()
    assert plan["state_guard"] == placement.BATCH_GUARD != draft.DRAFT_GUARD
    with pytest.raises(CLIError):
        placement.validate_batch_preview(_draft(plan), request)
    with pytest.raises(CLIError) as error:
        run(
            {
                "request": request,
                "apply": True,
                "state_digest": "0" * 64,
                "state_guard": draft.DRAFT_GUARD,
            },
            None,
        )
    assert error.value.code == "E_CONFLICT" and error.value.details["write_attempted"] is False
