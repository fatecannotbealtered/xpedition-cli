"""Bounded, read-only drawing reminders from observed world-coordinate envelopes.

These are deliberately projection advisories. Neither a bounding-box intersection
nor the explicit proximity distance is a native silkscreen/electrical rule.
"""

from __future__ import annotations

import hashlib
import json
import math

PROXIMITY_MM = 0.3
MAX_OBJECTS = 4096
MAX_PAIRS = 100000
MAX_WARNINGS = 1000
MAX_UNREVIEWED = 1000
MAX_PATH_POINTS = 4096
RULES = (
    "text_text",
    "text_outer_pad",
    "text_component_graphic",
    "text_component_envelope",
    "text_board_edge",
    "text_trace",
)


def trace_geometry_binding(row):
    """Bind advisory provenance to this exact observed physical trace, without mutation."""
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode("utf-8")).hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _box(value):
    if (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(_number(v) for v in value)
        and value[0] <= value[2]
        and value[1] <= value[3]
    ):
        return list(value)
    return None


def _geometry_box(row):
    """Exact circle/straight-path envelopes; never approximate unsupported arcs."""
    if _box(row.get("extents")):
        return _box(row["extents"]), "native_extrema"
    circle = row.get("circle")
    width = row.get("width", 0)
    if not _number(width) or width < 0:
        return None, "invalid_stroke_width"
    grow = width / 2
    if (
        isinstance(circle, (list, tuple))
        and len(circle) == 3
        and all(_number(v) for v in circle)
        and circle[2] >= 0
    ):
        x, y, radius = circle
        radius += grow
        return [x - radius, y - radius, x + radius, y + radius], "observed_circle"
    points = row.get("path")
    if not isinstance(points, (list, tuple)) or not points:
        return None, "missing_geometry_envelope"
    if len(points) > MAX_PATH_POINTS:
        return None, "geometry_point_budget_exceeded"
    if any(
        not isinstance(p, (list, tuple)) or len(p) < 3 or not all(_number(v) for v in p[:3])
        for p in points
    ):
        return None, "invalid_geometry_path"
    if any(p[2] != 0 for p in points):
        return None, "arc_envelope_unobserved"
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [
        min(xs) - grow,
        min(ys) - grow,
        max(xs) + grow,
        max(ys) + grow,
    ], "observed_straight_path"


def _identity(row, kind, index):
    return {
        "kind": kind,
        "observation_index": index,
        "uid": row.get("uid"),
        "refdes": row.get("refdes"),
        "pin": row.get("pin"),
        "net": row.get("net"),
        "layer": row.get("fabrication_layer", row.get("layer")),
        "side": row.get("observed_side", row.get("side")),
        "orientation_deg": row.get("orientation"),
    }


def _distance(a, b):
    return math.hypot(max(a[0] - b[2], b[0] - a[2], 0), max(a[1] - b[3], b[1] - a[3], 0))


def _rectangle(row):
    if not isinstance(row, dict):
        return None
    points = row.get("path") or []
    if len(points) != 5 or points[0] != points[-1]:
        return None
    if any(len(p) != 3 or not all(_number(v) for v in p) or p[2] != 0 for p in points):
        return None
    if any(
        (first[0] == second[0]) == (first[1] == second[1])
        for first, second in zip(points, points[1:], strict=False)
    ):
        return None
    bounds, _ = _geometry_box(row)
    if not bounds or bounds[0] == bounds[2] or bounds[1] == bounds[3]:
        return None
    corners = {
        (bounds[0], bounds[1]),
        (bounds[0], bounds[3]),
        (bounds[2], bounds[1]),
        (bounds[2], bounds[3]),
    }
    return bounds if {(p[0], p[1]) for p in points[:-1]} == corners else None


def report(model):
    """Report evaluated projection pairs and explicit missing observation coverage.

    Local indices identify observations only; they never substitute for native UIDs.
    Native Extrema is already in board coordinates, so rotated text is not rotated a
    second time. Text length, fallback fonts and renderer metrics are never used.
    """
    warnings, unreviewed = [], []
    budgets = {
        "object_limit": MAX_OBJECTS,
        "objects_examined": 0,
        "objects_omitted": 0,
        "pair_limit": MAX_PAIRS,
        "pairs_examined": 0,
        "pair_budget_exhausted": False,
        "warning_limit": MAX_WARNINGS,
        "warnings_omitted": 0,
        "unreviewed_limit": MAX_UNREVIEWED,
        "unreviewed_omitted": 0,
        "path_point_limit_per_object": MAX_PATH_POINTS,
    }
    checks = {
        name: {
            "rule": name,
            "pairs_checked": 0,
            "pairs_excluded": 0,
            "pairs_unchecked": 0,
            "warnings": 0,
        }
        for name in RULES
    }
    observation = model.get("layout_observation") or {}
    units = observation.get("units")
    collections = observation.get("collections") or {}
    layer_count = observation.get("layer_count") or {
        "observed": False,
        "value": None,
        "reason": "native_layer_count_observation_missing",
        "renderer_value": model.get("layers"),
        "renderer_source": "legacy_model_display_only",
    }
    actual_layers = layer_count.get("value")
    layers_observed = (
        layer_count.get("observed") is True and type(actual_layers) is int and actual_layers >= 1
    )
    layer_count = {
        **layer_count,
        "observed": layers_observed,
        "value": actual_layers if layers_observed else None,
    }
    absent = [
        name
        for name in ("texts", "pads", "silk", "component_envelopes", "outline", "traces")
        if collections.get(name, {}).get("complete") is not True
    ]

    def missing(identity, reasons, rules):
        if reasons:
            if len(unreviewed) >= MAX_UNREVIEWED:
                budgets["unreviewed_omitted"] += 1
            else:
                unreviewed.append(
                    {"object": identity, "reasons": reasons, "affected_rules": list(rules)}
                )

    def bounded_rows(rows):
        remaining = max(0, MAX_OBJECTS - budgets["objects_examined"])
        selected = rows[:remaining]
        budgets["objects_examined"] += len(selected)
        budgets["objects_omitted"] += len(rows) - len(selected)
        return selected

    def allow_pair():
        if budgets["pairs_examined"] >= MAX_PAIRS:
            budgets["pair_budget_exhausted"] = True
            return False
        budgets["pairs_examined"] += 1
        return True

    def warn(row):
        if len(warnings) >= MAX_WARNINGS:
            budgets["warnings_omitted"] += 1
        else:
            warnings.append(row)

    for row in bounded_rows(observation.get("unreadable_objects") or []):
        missing(
            row,
            [row.get("reason", "native_object_read_failed")],
            row.get("affected_rules") or RULES,
        )
    if not layers_observed:
        missing(
            {"kind": "board_layer_count"},
            [layer_count.get("reason", "native_layer_count_unobserved_or_invalid")],
            ["text_outer_pad", "text_trace"],
        )
    if model.get("filter"):
        absent.append("filtered_model")

    def evaluate(rule, first, box, second, other, source):
        checks[rule]["pairs_checked"] += 1
        distance = _distance(box, other)
        if distance > PROXIMITY_MM:
            return
        overlap = distance == 0
        checks[rule]["warnings"] += 1
        warn(
            {
                "kind": "projected_" + rule + ("_overlap" if overlap else "_proximity"),
                "text_kind": first.get("text_kind"),
                "refdes": first.get("refdes"),
                "text_layer": first.get("layer"),
                "text_side": first.get("side"),
                **(
                    {"net": second.get("net"), "trace_layer": second.get("layer")}
                    if rule == "text_trace"
                    else {}
                ),
                "objects": [first, second],
                "envelopes_mm": [box, other],
                "envelope_sources": ["native_text_extrema_world", source],
                "projected_distance_mm": round(distance, 6),
                "advisory_proximity_mm": PROXIMITY_MM,
                "meaning": (
                    "Observed envelopes overlap/touch or are close in projection. "
                    "The 0.3 mm advisory distance is not a native rule; inspect actual "
                    "strokes/layers. No object is moved or hidden."
                ),
            }
        )

    texts = []
    for index, row in enumerate(bounded_rows(model.get("texts") or [])):
        identity = {**_identity(row, "text", index), "text_kind": row.get("kind")}
        reasons = []
        box = _box(row.get("extents"))
        if not box:
            reasons.append("native_text_envelope_missing_or_invalid")
        if row.get("envelope_source") != "native_extrema_world_mm":
            reasons.append("text_envelope_provenance_unobserved")
        if units != "mm":
            reasons.append("model_units_unobserved")
        if row.get("owner_observed") is not True:
            reasons.append("text_component_or_document_ownership_unobserved")
        if row.get("height_source") != "native_format_mils":
            reasons.append("text_height_observation_unavailable")
        if not _number(row.get("orientation")) or type(row.get("mirrored")) is not bool:
            reasons.append("text_orientation_or_mirroring_unobserved")
        if row.get("observed_side") not in {"top", "bottom"}:
            reasons.append("text_side_unobserved")
        if row.get("fabrication_layer") not in {"silkscreen", "assembly"}:
            reasons.append("text_fabrication_layer_unobserved_or_unsupported")
        if row.get("stroke_source") != "native_vector_strokes_world_mm" or not row.get("strokes"):
            reasons.append("font_strokes_unobserved_or_unsupported")
        missing(identity, reasons, RULES)
        texts.append((identity, box if not reasons else None))

    targets = {
        name: []
        for name in (
            "text_outer_pad",
            "text_component_graphic",
            "text_component_envelope",
            "text_trace",
        )
    }
    trace_metadata = {}
    for row in (observation.get("trace_envelopes") or [])[:MAX_OBJECTS]:
        index = row.get("model_index")
        if type(index) is not int or index < 0:
            continue
        if index in trace_metadata:
            trace_metadata[index] = None  # ambiguous provenance cannot establish ownership
        else:
            trace_metadata[index] = row
    for key, rule, kind in (
        ("pads", "text_outer_pad", "pad"),
        ("silk", "text_component_graphic", "component_graphic"),
        ("traces", "text_trace", "trace"),
    ):
        for index, row in enumerate(bounded_rows(model.get(key) or [])):
            if rule == "text_trace":
                metadata = trace_metadata.get(index)
                width_source, uid = None, None
                if metadata and metadata.get("geometry_sha256") == trace_geometry_binding(row):
                    width_source, uid = metadata.get("width_source"), metadata.get("uid")
                row = {**row, "width_source": width_source, "uid": uid}
            identity = _identity(row, kind, index)
            if rule == "text_trace":
                identity["model_index"] = index
                identity["observation_index"] = (
                    metadata.get("observation_index") if uid is not None else None
                )
            box, source = _geometry_box(row)
            reasons = [] if box else [source]
            if rule == "text_component_graphic":
                if source != "native_extrema" and row.get("width_source") != "native_geometry_mils":
                    reasons.append("graphic_stroke_width_unobserved")
                if row.get("owner_observed") is not True:
                    reasons.append("graphic_component_or_document_ownership_unobserved")
                if (
                    row.get("observed_side") not in {"top", "bottom"}
                    or row.get("fabrication_layer") != "silkscreen"
                ):
                    reasons.append("graphic_side_or_fabrication_layer_unobserved")
            elif (
                type(row.get("layer")) is not int
                or row["layer"] < 1
                or (layers_observed and row["layer"] > actual_layers)
            ):
                reasons.append("conductor_layer_unobserved_or_invalid")
            if rule in {"text_outer_pad", "text_trace"} and not layers_observed:
                reasons.append("actual_conductor_layer_count_unobserved")
            if (
                rule == "text_trace"
                and source != "native_extrema"
                and row.get("width_source") != "native_geometry_mils"
            ):
                reasons.append("trace_stroke_width_unobserved")
            missing(identity, reasons, [rule])
            targets[rule].append((identity, box if not reasons else None, source))
    for index, row in enumerate(bounded_rows(observation.get("component_envelopes") or [])):
        identity = _identity(row, "component_envelope", index)
        box = _box(row.get("extents"))
        reasons = []
        if not box or row.get("envelope_source") != "native_component_extrema_world_mm":
            reasons.append("native_component_envelope_unobserved")
        if row.get("observed_side") not in {"top", "bottom"}:
            reasons.append("component_side_unobserved")
        missing(identity, reasons, ["text_component_envelope"])
        targets["text_component_envelope"].append(
            (identity, box if not reasons else None, "native_component_extrema_world")
        )

    collection_coverage = {name: dict(row) for name, row in collections.items()}
    for name, rule in (("traces", "text_trace"), ("silk", "text_component_graphic")):
        collection = collection_coverage.setdefault(name, {"complete": False})
        native_unreadable = collection.get("geometry_unreadable_count", 0)
        checkable = sum(box is not None for _, box, _ in targets[rule])
        uncheckable = len(targets[rule]) - checkable + native_unreadable
        collection.update(
            reminder_rule=rule,
            reminder_checkable_count=checkable,
            reminder_uncheckable_count=uncheckable,
            reminder_checkable_complete=(
                collection.get("complete") is True
                and uncheckable == 0
                and budgets["objects_omitted"] == 0
            ),
        )

    outlines = model.get("outline") or []
    for index, row in enumerate(outlines):
        if not isinstance(row, dict):
            missing(
                {"kind": "board_outline", "observation_index": index},
                ["board_outline_geometry_representation_unsupported"],
                ["text_board_edge"],
            )
    board = _rectangle(outlines[0]) if len(outlines) == 1 else None
    outline_observation = observation.get("board_outline") or {}
    native_inner_count = outline_observation.get("inner_contour_count")
    inner_observed = (
        outline_observation.get("inner_inventory_observed") is True
        and type(native_inner_count) is int
        and native_inner_count >= 0
    )
    represented_inner_count = sum(
        len(row.get("cutouts") or []) + len(row.get("circular_cutouts") or [])
        for row in outlines
        if isinstance(row, dict)
    )
    inner_count = max(native_inner_count or 0, represented_inner_count) if inner_observed else None
    inner_reason = (
        "board_inner_contours_not_checked"
        if represented_inner_count or (inner_observed and inner_count)
        else "board_inner_contour_inventory_unobserved"
        if not inner_observed
        else None
    )
    if inner_reason:
        missing(
            {"kind": "board_inner_boundaries", "count": inner_count},
            [inner_reason],
            ["text_board_edge"],
        )

    for index, (identity, box) in enumerate(texts):
        for other, other_box in texts[index + 1 :]:
            if not allow_pair():
                break
            if not box or not other_box:
                checks["text_text"]["pairs_unchecked"] += 1
            elif (identity["side"], identity["layer"]) != (other["side"], other["layer"]):
                checks["text_text"]["pairs_excluded"] += 1
            else:
                evaluate("text_text", identity, box, other, other_box, "native_text_extrema_world")
        if budgets["pair_budget_exhausted"]:
            break
        for rule, rows in targets.items():
            for other, other_box, source in rows:
                if not allow_pair():
                    break
                if not box or not other_box:
                    checks[rule]["pairs_unchecked"] += 1
                    continue
                matches = (
                    other["side"] == identity["side"]
                    and (rule == "text_component_envelope" or other["layer"] == identity["layer"])
                    if rule in {"text_component_graphic", "text_component_envelope"}
                    else other["layer"] == (1 if identity["side"] == "top" else actual_layers)
                )
                if not matches:
                    checks[rule]["pairs_excluded"] += 1
                    continue
                evaluate(rule, identity, box, other, other_box, source)
            if budgets["pair_budget_exhausted"]:
                break
        if budgets["pair_budget_exhausted"]:
            break
        if inner_reason:
            if not allow_pair():
                break
            checks["text_board_edge"]["pairs_unchecked"] += 1
        if not box or not board:
            if not allow_pair():
                break
            checks["text_board_edge"]["pairs_unchecked"] += 1
            if not board:
                missing(
                    identity,
                    ["board_edge_requires_one_exact_axis_aligned_rectangle"],
                    ["text_board_edge"],
                )
            continue
        edges = (
            [board[0], board[1], board[0], board[3]],
            [board[2], board[1], board[2], board[3]],
            [board[0], board[1], board[2], board[1]],
            [board[0], board[3], board[2], board[3]],
        )
        for edge_index, edge in enumerate(edges):
            if not allow_pair():
                break
            evaluate(
                "text_board_edge",
                identity,
                box,
                {"kind": "board_edge", "observation_index": edge_index},
                edge,
                "observed_exact_rectangular_outline",
            )
        if box[0] < board[0] or box[1] < board[1] or box[2] > board[2] or box[3] > board[3]:
            warn(
                {
                    "kind": "projected_text_outside_board_envelope",
                    "objects": [identity],
                    "envelopes_mm": [box, board],
                    "meaning": (
                        "Observed text envelope extends outside the rectangular board "
                        "projection; inspect actual strokes. Not native DRC."
                    ),
                }
            )
            checks["text_board_edge"]["warnings"] += 1
    return {
        "layout_reminders": warnings,
        "layout_reminder_coverage": {
            "status": "partial"
            if absent
            or unreviewed
            or any(
                budgets[key]
                for key in (
                    "objects_omitted",
                    "pair_budget_exhausted",
                    "warnings_omitted",
                    "unreviewed_omitted",
                )
            )
            else "checked_projected_subset",
            "units": units,
            "layer_count": layer_count,
            "collection_coverage": collection_coverage,
            "board_edge_coverage": {
                "outer_boundary_supported": board is not None,
                "outer_boundary_checked": checks["text_board_edge"]["pairs_checked"] > 0,
                "inner_inventory_observed": inner_observed,
                "inner_contour_count": inner_count,
                "inner_boundaries_checked": inner_observed and inner_count == 0,
                "complete": board is not None
                and inner_reason is None
                and checks["text_board_edge"]["pairs_checked"] > 0
                and checks["text_board_edge"]["pairs_unchecked"] == 0
                and not budgets["pair_budget_exhausted"]
                and budgets["objects_omitted"] == 0,
            },
            "advisory_proximity_mm": PROXIMITY_MM,
            "distance_basis": "explicit drawing heuristic; not a native clearance rule",
            "object_counts": {
                key: len(model.get(key) or [])
                for key in ("texts", "pads", "silk", "traces", "outline")
            },
            "checked_rules": list(checks.values()),
            "unreviewed_objects": unreviewed,
            "unreviewed_collections": [
                {
                    "collection": name,
                    **collections.get(
                        name, {"complete": False, "reason": "collection_completeness_unobserved"}
                    ),
                }
                for name in absent
            ],
            "warning_count": len(warnings),
            "budgets": budgets,
            "empty_warnings_mean_layout_clear": False,
            "manual_visual_review_required": True,
            "limitations": [
                "Envelope projections and a declared proximity heuristic only; not silkscreen "
                "violations, electrical DRC, manufacturing clearance or layout acceptance.",
                "Native world-coordinate text extrema/vector strokes are required; no "
                "string-length, fallback font or angle estimate supplies missing geometry.",
                "Component extrema are an aggregate projection, not an independently isolated "
                "physical body or fabrication-layer outline.",
                "Only same observed side/layer pairs and outer-conductor pads/traces are checked; "
                "inner/opposite layers are excluded. Missing provenance remains unreviewed.",
                "Display fallbacks never establish conductor Layer/LayerCount; unknown layers "
                "remain unchecked rather than excluding another side. Collection enumeration "
                "completeness differs from eligible geometry-conversion completeness.",
                "Arc envelopes without native extrema, internal board contours and "
                "nonrectangular board edges remain "
                "unreviewed. Local indices are observation positions, never persistent UIDs.",
                "An empty warning list cannot establish a clean layout. Review the complete "
                "final image and all unreviewed objects.",
                "Object, pair, geometry-point and output budgets are explicit; exhausted budgets "
                "leave coverage partial and do not assert that omitted objects/pairs are clear.",
            ],
        },
    }
