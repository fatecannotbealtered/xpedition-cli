"""Review-only contract for bounded differences in moved component silk text.

Nothing in the public placement path selects this draft.  This module compares
fresh native witnesses; it does not normalize or overwrite either raw frame.
It is not a clearance rule, native DRC, manufacturing check, or release claim.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Any

from . import placement_text_witness as witness

DRAFT_GUARD = "placement-full-board-material-v6-silk-draft"
CONTRACT = "component-derived-silk-geometry-v1-draft"
VERTEX_BUDGET_MM = 0.00001
MAX_TOPOLOGY_PAIRS_PER_TEXT = 100_000
MAX_TOPOLOGY_PAIRS_TOTAL = 250_000
# Exactly 10 nm, not the slightly larger binary64 value nearest to 0.00001 mm.
_BUDGET = Fraction(1, 100_000)
_BUDGET_SQUARED = _BUDGET * _BUDGET
_FABRICATION_BASIS = "formal Type/Side/SideName; not a conductor-layer number"


def _number(value: Any) -> Fraction:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("finite native coordinate required")
    return Fraction(value) if type(value) is int else Fraction.from_float(value)


def _fraction(value: Fraction) -> list[int]:
    return [value.numerator, value.denominator]


def _point(x: Any, y: Any) -> tuple[Fraction, Fraction]:
    return _number(x), _number(y)


def _distance_squared(a: tuple[Fraction, Fraction], b: tuple[Fraction, Fraction]) -> Fraction:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _within_vertex_budget(dx: Fraction, dy: Fraction) -> bool:
    return dx * dx + dy * dy <= _BUDGET_SQUARED


def _orientation(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, p):
    return (
        _orientation(a, b, p) == 0
        and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    )


def _relation(a, b, c, d):
    """Exact centerline relation, including endpoint contacts and overlap."""
    o1, o2, o3, o4 = (
        _orientation(a, b, c),
        _orientation(a, b, d),
        _orientation(c, d, a),
        _orientation(c, d, b),
    )
    if o1 * o2 < 0 and o3 * o4 < 0:
        return "cross"
    touches = any(
        (_on_segment(a, b, c), _on_segment(a, b, d), _on_segment(c, d, a), _on_segment(c, d, b))
    )
    if not touches:
        return "disjoint"
    if o1 == o2 == o3 == o4 == 0:
        axis = 0 if a[0] != b[0] else 1
        lo = max(min(a[axis], b[axis]), min(c[axis], d[axis]))
        hi = min(max(a[axis], b[axis]), max(c[axis], d[axis]))
        return "overlap" if lo < hi else "touch"
    return "touch"


def _contact_signature(a, b, c, d):
    """Preserve which endpoints form each observed centerline contact."""
    return (_on_segment(a, b, c), _on_segment(a, b, d), _on_segment(c, d, a), _on_segment(c, d, b))


def _point_segment_distance_squared(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        raise ValueError("degenerate line segment")
    projection = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_squared
    if projection <= 0:
        return _distance_squared(p, a)
    if projection >= 1:
        return _distance_squared(p, b)
    q = (a[0] + projection * dx, a[1] + projection * dy)
    return _distance_squared(p, q)


def _segment_distance_squared(a, b, c, d):
    if _relation(a, b, c, d) != "disjoint":
        return Fraction(0)
    return min(
        _point_segment_distance_squared(a, c, d),
        _point_segment_distance_squared(b, c, d),
        _point_segment_distance_squared(c, a, b),
        _point_segment_distance_squared(d, a, b),
    )


def _boxes_separated_beyond_budget(a, b, c, d):
    margin = 2 * _BUDGET
    return any(
        max(a[axis], b[axis]) + margin < min(c[axis], d[axis])
        or max(c[axis], d[axis]) + margin < min(a[axis], b[axis])
        for axis in (0, 1)
    )


def _strokes(values):
    """Reject unsupported stroke starts/ends and short/degenerate segments."""
    if not isinstance(values, list) or len(values) < 7 or (len(values) - 1) % 3:
        raise ValueError("native stroke array is incomplete")
    strokes, current = [], None
    for index in range(1, len(values), 3):
        x, y, code = values[index : index + 3]
        if type(code) not in (int, float) or code not in (0, 1, 2):
            raise ValueError("native stroke control code is invalid")
        point = _point(x, y)
        if code == 0:
            if current is not None:
                raise ValueError("native stroke has an unterminated start")
            current = [point]
        elif current is None:
            raise ValueError("native stroke has no start")
        else:
            if _distance_squared(current[-1], point) <= 4 * _BUDGET_SQUARED:
                raise ValueError("native stroke has a degenerate or budget-sized segment")
            current.append(point)
            if code == 2:
                strokes.append(current)
                current = None
    if current is not None or not strokes:
        raise ValueError("native stroke has no complete ending")
    return strokes


class _TopologyBudget:
    """Reserve worst-case pair work before comparison, including failed texts."""

    def __init__(self):
        self.reserved_pairs = 0

    def reserve(self, pairs):
        if pairs > MAX_TOPOLOGY_PAIRS_PER_TEXT:
            raise ValueError("native stroke topology exceeds the bounded comparison budget")
        if self.reserved_pairs + pairs > MAX_TOPOLOGY_PAIRS_TOTAL:
            raise ValueError("native stroke topology exceeds the total comparison budget")
        self.reserved_pairs += pairs


def _topology(expected_values, actual_values, *, budget=None):
    expected, actual = _strokes(expected_values), _strokes(actual_values)
    if len(expected) != len(actual) or any(
        len(a) != len(b) for a, b in zip(expected, actual, strict=True)
    ):
        raise ValueError("native stroke topology differs")
    old_segments, new_segments = [], []
    for stroke_index, (old, new) in enumerate(zip(expected, actual, strict=True)):
        old_closed, new_closed = old[0] == old[-1], new[0] == new[-1]
        if old_closed != new_closed:
            raise ValueError("native stroke closure changed")
        if not old_closed and (
            _distance_squared(old[0], old[-1]) <= 4 * _BUDGET_SQUARED
            or _distance_squared(new[0], new[-1]) <= 4 * _BUDGET_SQUARED
        ):
            raise ValueError("native stroke closure is within the uncertainty budget")
        for index in range(len(old) - 1):
            old_segments.append(
                (stroke_index, index, len(old) - 1, old_closed, old[index], old[index + 1])
            )
            new_segments.append(
                (stroke_index, index, len(new) - 1, new_closed, new[index], new[index + 1])
            )
    count = len(old_segments)
    pairs = count * (count - 1) // 2
    # A text may fail on its last pair. Never refund that work or charge it only
    # after success. Cheap parsing/closure failures above perform no pair work.
    if budget is None:
        budget = _TopologyBudget()
    budget.reserve(pairs)
    contact_pairs = 0
    checked_pairs = 0
    for i, old_a in enumerate(old_segments):
        for j in range(i + 1, count):
            old_b, new_a, new_b = old_segments[j], new_segments[i], new_segments[j]
            same_stroke = old_a[0] == old_b[0]
            adjacent = same_stroke and (
                old_b[1] == old_a[1] + 1 or old_a[3] and old_a[1] == 0 and old_b[1] == old_a[2] - 1
            )
            if adjacent:
                if _relation(old_a[4], old_a[5], old_b[4], old_b[5]) != _relation(
                    new_a[4], new_a[5], new_b[4], new_b[5]
                ):
                    raise ValueError("adjacent native stroke overlap topology changed")
                continue
            unchanged = old_a[4:] == new_a[4:] and old_b[4:] == new_b[4:]
            if unchanged:
                continue
            if _boxes_separated_beyond_budget(
                old_a[4], old_a[5], old_b[4], old_b[5]
            ) and _boxes_separated_beyond_budget(new_a[4], new_a[5], new_b[4], new_b[5]):
                continue
            checked_pairs += 1
            old_relation = _relation(old_a[4], old_a[5], old_b[4], old_b[5])
            new_relation = _relation(new_a[4], new_a[5], new_b[4], new_b[5])
            if old_relation != new_relation:
                raise ValueError("native stroke intersection topology changed")
            if old_relation != "disjoint":
                # Both raw frames are observed. Preserve the exact endpoint
                # incidence for a touch/overlap and the proper-crossing relation;
                # a broken, added or reassigned contact must never be hidden by
                # the engineering displacement allowance.
                if old_relation in {"touch", "overlap"} and _contact_signature(
                    old_a[4], old_a[5], old_b[4], old_b[5]
                ) != _contact_signature(new_a[4], new_a[5], new_b[4], new_b[5]):
                    raise ValueError("native stroke contact incidence changed")
                if (
                    old_relation == "cross"
                    and min(
                        _point_segment_distance_squared(old_a[4], old_b[4], old_b[5]),
                        _point_segment_distance_squared(old_a[5], old_b[4], old_b[5]),
                        _point_segment_distance_squared(old_b[4], old_a[4], old_a[5]),
                        _point_segment_distance_squared(old_b[5], old_a[4], old_a[5]),
                        _point_segment_distance_squared(new_a[4], new_b[4], new_b[5]),
                        _point_segment_distance_squared(new_a[5], new_b[4], new_b[5]),
                        _point_segment_distance_squared(new_b[4], new_a[4], new_a[5]),
                        _point_segment_distance_squared(new_b[5], new_a[4], new_a[5]),
                    )
                    <= 4 * _BUDGET_SQUARED
                ):
                    raise ValueError("native stroke crossing is within the uncertainty budget")
                contact_pairs += 1
                continue
            if (
                _segment_distance_squared(old_a[4], old_a[5], old_b[4], old_b[5])
                <= 4 * _BUDGET_SQUARED
                or _segment_distance_squared(new_a[4], new_a[5], new_b[4], new_b[5])
                <= 4 * _BUDGET_SQUARED
            ):
                raise ValueError("native stroke separation is within the uncertainty budget")
    return {
        "status": "observed_relations_preserved",
        "stroke_count": len(expected),
        "segment_count": count,
        "pairs": pairs,
        "checked_pairs": checked_pairs,
        "observed_contact_pairs_with_changed_coordinates": contact_pairs,
        "contact_robustness_to_unobserved_changes": "unverified"
        if contact_pairs
        else "not_applicable",
    }


def assess_observed_stroke_clearance(
    centerline_distance_mm: Any,
    pen_a_mm: Any,
    pen_b_mm: Any,
    required_gap_mm: Any,
    *,
    moved_a: bool,
    moved_b: bool,
) -> dict[str, Any]:
    """Conservative comparison for a *separately authenticated* observed constraint.

    The current placement snapshot does not contain an authenticated complete silk
    rule/keepout inventory.  This helper is therefore not wired to public inputs.
    """
    if type(moved_a) is not bool or type(moved_b) is not bool:
        raise ValueError("clearance budget sides must be observed booleans")
    if any(
        value is None for value in (centerline_distance_mm, pen_a_mm, pen_b_mm, required_gap_mm)
    ):
        return {"status": "unverified", "reason": "required stroke clearance evidence is missing"}
    distance, a, b, required = map(
        _number, (centerline_distance_mm, pen_a_mm, pen_b_mm, required_gap_mm)
    )
    if min(distance, a, b, required) < 0:
        raise ValueError("clearance evidence must be nonnegative")
    sides = int(moved_a) + int(moved_b)
    guaranteed_gap = distance - (a + b) / 2 - sides * _BUDGET
    return {
        "status": "margin_satisfied" if guaranteed_gap > required else "insufficient_or_uncertain",
        "budget_sides": sides,
        "guaranteed_gap_mm_fraction": _fraction(guaranteed_gap),
        "required_gap_mm_fraction": _fraction(required),
        "basis": "centerline distance less half of both pen widths and each moved-side budget",
        "provenance": "caller must authenticate the measured distance and applicable rule",
    }


def _eligible(semantic, result):
    owner = semantic.get("owner")
    fmt = semantic.get("format")
    fabrication = semantic.get("fabrication_identity")
    old, target = result.get("before", {}), result.get("target", {})
    return bool(
        type(semantic.get("type")) is int
        and semantic["type"] == 2
        and type(semantic.get("text_type")) is int
        and semantic["text_type"] in (1, 4)
        and isinstance(fabrication, dict)
        and type(fabrication.get("type")) is int
        and fabrication["type"] == 2
        and type(fabrication.get("side")) is int
        and fabrication["side"] == semantic.get("side_code")
        and semantic.get("side_code") in (1, 512)
        and fabrication.get("side_name") == semantic.get("side_name")
        and fabrication.get("basis") == _FABRICATION_BASIS
        and set(fabrication) == {"type", "side", "side_name", "basis"}
        and isinstance(owner, dict)
        and owner.get("refdes") == result.get("id")
        and owner.get("placed") is True
        and owner.get("uid") == str(old.get("object_id"))
        and old.get("placed") is True
        and target.get("placed") is True
        and old.get("object_id") == target.get("object_id")
        and semantic.get("side") == old.get("side") == target.get("side")
        and isinstance(fmt, dict)
        and isinstance(fmt.get("Font"), str)
        and bool(fmt["Font"].strip())
        and fmt.get("valid") is True
        and fmt.get("is_vector_font") is True
    )


def compare(before, expected, actual, results):
    """Compare complete, already validated states and preserve every residual.

    The caller must first run complete material and semantic validation. This
    function rechecks semantic equality and eligibility before any allowance.
    """
    before_sem = before[witness.SEMANTIC_FIELD]
    expected_sem = expected[witness.SEMANTIC_FIELD]
    actual_sem = actual[witness.SEMANTIC_FIELD]
    semantic_diff = witness.semantic_difference(expected_sem, actual_sem, before_sem, results)
    report = {
        "contract": CONTRACT,
        "budget_mm": VERTEX_BUDGET_MM,
        "budget_mm_fraction": _fraction(_BUDGET),
        "vertex_metric": "euclidean",
        "budget_scope": "single operation; no cumulative drift guarantee",
        "topology_scope": "per-text centerlines only; inter-text and obstacle clearance unverified",
        "max_topology_pairs_per_text": MAX_TOPOLOGY_PAIRS_PER_TEXT,
        "max_topology_pairs_total": MAX_TOPOLOGY_PAIRS_TOTAL,
        "coordinate_basis": "exact rational value of each observed binary64 number",
        "raw_observations_preserved": True,
        "qualification": {
            "status": "incomplete",
            "can_authorize_write": False,
            "blockers": [
                "independent review and explicit activation approval pending",
                "authenticated applicable silk clearance and protection-zone proof absent",
                "contact robustness to unobserved changes unverified",
            ],
        },
        "clearance": {
            "status": "unverified",
            "reason": (
                "complete authenticated silk clearance and applicable keepout rules are absent"
            ),
            "not_a_drc_or_manufacturing_check": True,
        },
        "texts": [],
        "issues": [],
        "_untrusted": ["texts", "issues", "difference"],
        "valid": False,
    }
    if semantic_diff is not None:
        report["difference"] = semantic_diff
        return report
    moves = {row["id"]: row for row in results if row["changed"]}
    if len(moves) != sum(bool(row["changed"]) for row in results):
        report["difference"] = {"path": "results", "reason": "duplicate moved component"}
        return report
    semantics = {row["uid"]: row for row in before_sem}
    actual_by_uid = {row["uid"]: row for row in actual[witness.WITNESS_FIELD]}
    expected_rows = expected[witness.WITNESS_FIELD]
    if len(actual_by_uid) != len(actual[witness.WITNESS_FIELD]) or set(actual_by_uid) != {
        r["uid"] for r in expected_rows
    }:
        report["difference"] = {
            "path": witness.WITNESS_FIELD + ".uid",
            "reason": "different or duplicate identities",
        }
        return report
    global_maximum_squared = Fraction(0)
    topology_budget = _TopologyBudget()
    for row in expected_rows:
        uid, refdes = row["uid"], row["refdes"]
        got = actual_by_uid[uid]
        if refdes not in moves:
            diff = witness._first_difference(row, got, f"{witness.WITNESS_FIELD}[uid={uid}]")
            if diff is not None:
                report["issues"].append(diff)
            continue
        semantic = semantics.get(uid)
        if not isinstance(semantic, dict) or not _eligible(semantic, moves[refdes]):
            report["issues"].append(
                {
                    "path": f"{witness.SEMANTIC_FIELD}[uid={uid}]",
                    "reason": "target silk, vector font or placed owner not positively verified",
                }
            )
            continue
        if row["uid"] != got["uid"] or row["refdes"] != got["refdes"]:
            report["issues"].append(
                {"path": f"{witness.WITNESS_FIELD}[uid={uid}]", "reason": "target identity changed"}
            )
            continue
        want, have = row["stroke_array_mm"], got.get("stroke_array_mm")
        if not isinstance(have, list) or len(want) != len(have):
            report["issues"].append(
                {
                    "path": f"{witness.WITNESS_FIELD}[uid={uid}].stroke_array_mm",
                    "reason": "point count changed",
                }
            )
            continue
        if want[0] != have[0] or any(want[i] != have[i] for i in range(3, len(want), 3)):
            report["issues"].append(
                {
                    "path": f"{witness.WITNESS_FIELD}[uid={uid}].stroke_array_mm",
                    "reason": "pen or control code changed",
                }
            )
            continue
        extents = got.get("extents_mm")
        if not isinstance(extents, list) or len(extents) != 4:
            report["issues"].append(
                {
                    "path": f"{witness.WITNESS_FIELD}[uid={uid}].extents_mm",
                    "reason": "extents incomplete",
                }
            )
            continue
        extent_residuals = [
            _number(b) - _number(a) for a, b in zip(row["extents_mm"], extents, strict=True)
        ]
        point_residuals, maximum_squared, first_budget_error = [], Fraction(0), None
        for i in range(1, len(want), 3):
            dx, dy = (
                _number(have[i]) - _number(want[i]),
                _number(have[i + 1]) - _number(want[i + 1]),
            )
            squared = dx * dx + dy * dy
            maximum_squared = max(maximum_squared, squared)
            point_residuals.append([_fraction(dx), _fraction(dy)])
            if not _within_vertex_budget(dx, dy) and first_budget_error is None:
                first_budget_error = {
                    "path": f"{witness.WITNESS_FIELD}[uid={uid}].stroke_array_mm[{i}]",
                    "reason": "euclidean vertex budget exceeded",
                }
        text_report = {
            "uid": uid,
            "refdes": refdes,
            "point_residuals_mm_fraction": point_residuals,
            "extent_residuals_mm_fraction": [_fraction(v) for v in extent_residuals],
            "max_vertex_distance_squared_mm2_fraction": _fraction(maximum_squared),
            "max_vertex_distance_mm_approx": math.sqrt(float(maximum_squared)),
            "max_extent_residual_mm_approx": float(max(map(abs, extent_residuals), default=0)),
        }
        report["texts"].append(text_report)
        global_maximum_squared = max(global_maximum_squared, maximum_squared)
        if first_budget_error is not None:
            report["issues"].append(first_budget_error)
        if any(abs(value) > _BUDGET for value in extent_residuals):
            report["issues"].append(
                {
                    "path": f"{witness.WITNESS_FIELD}[uid={uid}].extents_mm",
                    "reason": "extent budget exceeded",
                }
            )
        reserved_before = topology_budget.reserved_pairs
        try:
            text_report["topology"] = _topology(want, have, budget=topology_budget)
        except ValueError as error:
            report["issues"].append(
                {
                    "path": f"{witness.WITNESS_FIELD}[uid={uid}].stroke_array_mm",
                    "reason": str(error),
                }
            )
        finally:
            text_report["topology_pairs_reserved"] = (
                topology_budget.reserved_pairs - reserved_before
            )
    report["max_vertex_distance_squared_mm2_fraction"] = _fraction(global_maximum_squared)
    report["max_vertex_distance_mm_approx"] = math.sqrt(float(global_maximum_squared))
    report["topology_pairs"] = topology_budget.reserved_pairs
    report["topology_accounting"] = "worst-case pairs reserved before comparison; no failure refund"
    if report["issues"]:
        report["difference"] = report["issues"][0]
    else:
        report["valid"] = True
    return report
