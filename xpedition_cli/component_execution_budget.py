"""Cost feasibility only; no native state, caller override, or authorization."""

from __future__ import annotations

import math

GUARD = "component-native-apply-resources-v2"
ENGINE_SECONDS = 3600.0
MINIMUM_FULL_READS = 6
REMAINING_PHASE_SECONDS = 120.0


def contract():
    return {
        "guard": GUARD,
        "native_apply_seconds": ENGINE_SECONDS,
        "minimum_full_reads": MINIMUM_FULL_READS,
        "prewrite_checks_after_reads": [1, 3],
        "remaining_reads_at_checks": [5, 3],
        "remaining_phase_allowance_seconds": REMAINING_PHASE_SECONDS,
        "engine_formula": "6 complete getter planning units * 450 seconds "
        "+ 900 material/rules/transaction/dependency/Save/closed ZIP/Open seconds = 3600",
        "engine_planning_overhead_seconds": 900,
        "remaining_reads_unit": "complete native observation phase",
        "sample_unit": "whole local observation including current text and material/masks",
        "sample_cost_boundary": "material already inside the sample is never added again; "
        "120 seconds is a non-read estimate, not a completion guarantee",
        "clock": "local monotonic; measured complete reads in this native invocation",
        "caller_override": False,
        "engineering_fingerprint_contains_timing": False,
        "qualification": "runtime cost estimate only; neither safety nor throughput acceptance",
    }


def estimate(
    sampled_seconds,
    remaining_reads,
    available_seconds,
    *,
    phase,
    no_op=False,
    spent_non_read_seconds=0.0,
):
    if (
        type(sampled_seconds) not in (int, float)
        or not math.isfinite(sampled_seconds)
        or sampled_seconds < 0
        or type(remaining_reads) is not int
        or not 0 <= remaining_reads <= MINIMUM_FULL_READS
        or type(available_seconds) not in (int, float)
        or not math.isfinite(available_seconds)
        or not 0 <= available_seconds <= ENGINE_SECONDS
        or phase not in {"preview", "after_initial_observation", "before_transaction"}
        or type(no_op) is not bool
        or type(spent_non_read_seconds) not in (int, float)
        or not math.isfinite(spent_non_read_seconds)
        or spent_non_read_seconds < 0
    ):
        raise ValueError("Complete local cost measurements and a fixed native limit are required")
    sampled_seconds = round(float(sampled_seconds), 6)
    available_seconds = round(float(available_seconds), 6)
    reads = 0 if no_op else remaining_reads
    spent_non_read_seconds = round(float(spent_non_read_seconds), 6)
    allowance = 0.0 if no_op else max(0.0, REMAINING_PHASE_SECONDS - spent_non_read_seconds)
    estimated_seconds = round(sampled_seconds * reads + allowance, 6)
    blocked = estimated_seconds > available_seconds
    return {
        **contract(),
        "phase": phase,
        "sampled_seconds": sampled_seconds,
        "remaining_reads": reads,
        "estimated_seconds": estimated_seconds,
        "available_seconds": available_seconds,
        "phase_allowance_seconds": allowance,
        "spent_non_read_seconds": spent_non_read_seconds,
        "no_op": no_op,
        "blocked": blocked,
        "status": "not_required_for_noop"
        if no_op
        else "blocked"
        if blocked
        else "within_estimated_budget",
        "write_attempted": False,
    }


def validate_report(value):
    if not isinstance(value, dict):
        raise ValueError("A locally measured execution resource report is required")
    expected = estimate(
        value.get("sampled_seconds"),
        value.get("remaining_reads"),
        value.get("available_seconds"),
        phase=value.get("phase"),
        no_op=value.get("no_op"),
        spent_non_read_seconds=value.get("spent_non_read_seconds"),
    )
    if value != expected:
        raise ValueError("Execution resource report is malformed or inconsistent")
    return value


def blocker(report):
    return {
        "code": "E_COMPONENT_EXECUTION_BUDGET",
        "message": "Complete remaining native observations exceed the fixed native apply budget.",
        "resource": "native_apply_seconds",
        "sampled_seconds": report["sampled_seconds"],
        "remaining_reads": report["remaining_reads"],
        "estimated_seconds": report["estimated_seconds"],
        "available_seconds": report["available_seconds"],
        "write_attempted": False,
    }
