"""Executable, fail-closed surroundings assessment for the inactive silk draft.

Consumes raw native observations, computes between-object distances, and keeps
rule applicability separate. This module accepts no user-provided clearance or
authentication flags. The151.0 text-pair rule semantics are not yet established.
"""

from __future__ import annotations

import math
from fractions import Fraction

from . import placement_silk_equivalence as silk

MAX_PAIR_COMPARISONS = 250000
MEASUREMENT_RADIUS_MM = 2  # Work window only, never an assumed rule value.
REQUIRED_INVENTORIES = {
    "texts",
    "fabrication_graphics",
    "placement_obstructs",
    "obstructs",
    "rule_areas",
    "Traces",
    "Vias",
    "GeneratedPlanes",
    "PlaneShapes",
    "MountingHoles",
    "ToolingHoles",
    "ShearingHoles",
    "ConductorLayerGfxs2",
    "UserLayerGfxs",
    "board_outline",
    "pins",
}


def _fraction(value):
    return [value.numerator, value.denominator]


def _segments(raw):
    return [
        (a, b) for stroke in silk._strokes(raw) for a, b in zip(stroke, stroke[1:], strict=False)
    ]


def _shape(geometry, budget=None):
    """Straight contours or a conservative filled disk; preserve unsupported arcs."""
    if geometry.get("unit") != "mm":
        raise ValueError("Explicit native mm geometry is required")
    pen = geometry["pen_mm"]
    if geometry.get("cutouts"):
        raise ValueError("Cutout material requires a separate bounded proof")
    if "circle_mm" in geometry:
        x, y, radius = map(silk._number, geometry["circle_mm"])
        if radius <= 0:
            raise ValueError("Invalid circle")
        return {"disk": (x, y, radius), "pen": pen}
    points = geometry["path_mm"]
    if len(points) > 64 or geometry["filled"] and not geometry["closed"]:
        raise ValueError("Contour is not a bounded valid filled/linear shape")
    if any(len(p) != 3 or p[2] != 0 for p in points):
        raise ValueError("Arced contour is outside straight-contour qualification")
    vertices = [silk._point(p[0], p[1]) for p in points]
    if geometry["closed"] and vertices[0] != vertices[-1]:
        vertices.append(vertices[0])
    edges = list(zip(vertices, vertices[1:], strict=False))
    if not edges or any(a == b for a, b in edges):
        raise ValueError("Degenerate contour")
    # Occupy closed interiors conservatively, even when only the native display
    # outline is drawn. A protection area is not merely its displayed border.
    filled = geometry["closed"] or geometry["filled"]
    if filled:
        if budget is None:
            budget = {"pairs": 0}
        for i, (a, b) in enumerate(edges):
            for j in range(i + 1, len(edges)):
                if j == i + 1 or i == 0 and j == len(edges) - 1:
                    continue
                if budget["pairs"] >= MAX_PAIR_COMPARISONS:
                    raise ValueError("Environment comparison budget exhausted")
                budget["pairs"] += 1
                if silk._relation(a, b, *edges[j]) != "disjoint":
                    raise ValueError("Filled contour is not a verified simple polygon")
        if sum(a[0] * b[1] - b[0] * a[1] for a, b in edges) == 0:
            raise ValueError("Filled contour has no area")
    return {"edges": edges, "filled": filled, "vertices": vertices, "pen": pen}


def _inside(point, vertices):
    """Exact winding parity, including boundary contact."""
    result = False
    for a, b in zip(vertices, vertices[1:], strict=False):
        if silk._on_segment(a, b, point):
            return True
        if (a[1] > point[1]) != (b[1] > point[1]):
            x = a[0] + (point[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if point[0] < x:
                result = not result
    return result


def _bounds(edges, pen, disk=None):
    margin = silk._number(pen) / 2
    if disk is not None:
        x, y, radius = disk
        return (x - radius - margin, y - radius - margin, x + radius + margin, y + radius + margin)
    points = [p for edge in edges for p in edge]
    return (
        min(p[0] for p in points) - margin,
        min(p[1] for p in points) - margin,
        max(p[0] for p in points) + margin,
        max(p[1] for p in points) + margin,
    )


def _box_distance_squared(a, b):
    return sum(max(Fraction(0), a[i] - b[i + 2], b[i] - a[i + 2]) ** 2 for i in (0, 1))


def _lower_distance_squared_to_distance(squared):
    """A checked lower bound; never enlarge the10nm engineering budget."""
    value = math.sqrt(float(squared))
    for _ in range(4):
        if silk._number(value) ** 2 <= squared:
            return value
        value = math.nextafter(value, 0.0)
    raise ValueError("A conservative distance lower bound could not be established")


def _upper(value):
    result = float(value)
    if silk._number(result) < value:
        result = math.nextafter(result, math.inf)
    if not math.isfinite(result) or silk._number(result) < value:
        raise ValueError("A conservative pen/radius upper bound is unavailable")
    return result


def compare_stroke_obstacle(
    strokes, pen_mm, obstacle, *, moved_a, moved_b, required_gap_mm, budget
):
    """Conservative inter-object measurement, independent of rule authentication."""
    best = None
    contacts = set()
    obstacle_pen = obstacle["pen"]
    for a, b in strokes:
        if "disk" in obstacle:
            if budget["pairs"] >= MAX_PAIR_COMPARISONS:
                raise ValueError("Environment comparison budget exhausted")
            budget["pairs"] += 1
            x, y, radius = obstacle["disk"]
            distance = silk._point_segment_distance_squared((x, y), a, b)
            best = distance if best is None else min(best, distance)
            obstacle_pen = _upper(silk._number(obstacle["pen"]) + 2 * radius)
            if distance <= radius * radius:
                contacts.add("disk_contact_or_inside")
            continue
        for c, d in obstacle["edges"]:
            if budget["pairs"] >= MAX_PAIR_COMPARISONS:
                raise ValueError("Environment comparison budget exhausted")
            budget["pairs"] += 1  # Charge actual attempts even if a later pair fails.
            relation = silk._relation(a, b, c, d)
            if relation != "disjoint":
                contacts.add(relation)
            distance = silk._segment_distance_squared(a, b, c, d)
            best = distance if best is None else min(best, distance)
        if obstacle.get("filled") and (
            _inside(a, obstacle["vertices"]) or _inside(b, obstacle["vertices"])
        ):
            best = Fraction(0)
            contacts.add("inside_filled_region")
    if best is None:
        raise ValueError("No complete segment distance")
    lower = _lower_distance_squared_to_distance(best)
    result = silk.assess_observed_stroke_clearance(
        lower, pen_mm, obstacle_pen, required_gap_mm, moved_a=moved_a, moved_b=moved_b
    )
    physical_limit = (silk._number(pen_mm) + silk._number(obstacle_pen)) / 2
    uncertainty = (int(moved_a) + int(moved_b)) * Fraction(1, 100000)
    return {
        "centerline_distance_squared_mm2_fraction": _fraction(best),
        "centerline_distance_mm_lower_bound": lower,
        "contacts": sorted(contacts),
        "gap_assessment": result,
        "stroke_bodies_contact_or_uncertain": best <= (physical_limit + uncertainty) ** 2,
        "distance_scope": "vector strokes / straight contours; circles overapproximated as disks",
    }


def decision(geometry, native_coverage, rule_coverage, clearance):
    """Independent prerequisite gates; activation remains a separate review step."""
    gates = {
        "geometry_comparison": geometry.get("valid") is True,
        "native_input_coverage": native_coverage.get("complete") is True,
        "effective_rule_coverage": rule_coverage.get("complete") is True,
        "clearance_and_protection": clearance.get("status") == "satisfied",
    }
    return {
        "eligible": False,
        "conditions_complete": all(gates.values()),
        "gates": gates,
        "blockers": [name for name, passed in gates.items() if not passed]
        + ["public_draft_inactive"],
    }


def evaluate(environment, target_uids, *, plan=None, observed=None):
    """Recompute every gate; a geometry valid flag alone can never authorize apply."""
    from . import placement

    geometry = {"status": "not_run", "valid": False}
    if plan is not None and observed is not None:
        geometry = placement.verify_batch_state(plan, observed)
    before, frame = environment["before"], environment["after"]
    inventories = frame["inventories"]
    issues, pairs, unsupported = [], [], []
    if before != frame:
        issues.append("native_environment_not_repeat_exact")
    if set(inventories) != REQUIRED_INVENTORIES:
        issues.append("native_inventory_roles_missing_or_unrecognized")
    for name in sorted(REQUIRED_INVENTORIES):
        value = inventories.get(name, {})
        if value.get("complete") is not True:
            issues.append(name + ":incomplete")
        if "rows" in value and (
            value.get("count") != len(value["rows"])
            or len({r["id"] for r in value["rows"]}) != len(value["rows"])
        ):
            issues.append(name + ":identity_or_count_mismatch")
        if any(r.get("persistent_identity_available") is False for r in value.get("rows", [])):
            issues.append(name + ":persistent_identity_unavailable")
    texts = {row["id"]: row for row in inventories.get("texts", {}).get("rows", [])}
    if any("holes" not in pin for pin in inventories.get("pins", {}).get("rows", [])):
        issues.append("pin_hole_inventory_missing")
    if observed is not None:
        actual_texts = {"text:" + row["uid"]: row for row in observed["text_native_witness"]}
        native_texts = {key: row for key, row in texts.items() if row["semantic"]["type"] == 2}
        if set(actual_texts) != set(native_texts) or any(
            native_texts[key]["raw"][field] != row[field]
            for key, row in actual_texts.items()
            if key in native_texts
            for field in ("stroke_array_mm", "extents_mm")
        ):
            issues.append("environment_does_not_bind_geometry_frame_raw_texts")
    targets = {"text:" + str(uid) for uid in target_uids}
    if not targets or not targets <= texts.keys():
        issues.append("selected_native_text_identity_missing")
    # Collector row provenance and all raw values are retained. Neither a bare
    # numeric GetClearanceRule response nor a JSON valid flag authenticates the
    # supported object types, scope or priority for a fabrication-text rule.
    rule_coverage = {
        "complete": False,
        "reason": "151.0 fabrication-text rule applicability/priority unproven",
        "observations": frame.get("rule_observations", []),
        "missing": [
            "silk-text",
            "silk-graphics",
            "copper/mask-openings",
            "board-edge",
            "protection-areas",
        ],
    }
    budget = {"pairs": 0}
    native_issues = list(issues)
    for target_id in sorted(targets & texts.keys()):
        target = texts[target_id]
        semantic = target["semantic"]
        if semantic["type"] != 2 or semantic["side"] not in ("top", "bottom"):
            issues.append(target_id + ":not_verified_silk_side")
            continue
        try:
            strokes = _segments(target["raw"]["stroke_array_mm"])
            pen = target["raw"]["stroke_array_mm"][0]
        except (ValueError, KeyError) as error:
            unsupported.append({"id": target_id, "reason": str(error)})
            continue
        obstacles = []
        for other_id, other in texts.items():
            if other_id == target_id or other["semantic"]["type"] != 2:
                continue
            if other["semantic"]["side"] != semantic["side"]:
                continue
            try:
                obstacles.append(
                    (
                        other_id,
                        {
                            "edges": _segments(other["raw"]["stroke_array_mm"]),
                            "pen": other["raw"]["stroke_array_mm"][0],
                        },
                        other_id in targets,
                    )
                )
            except ValueError as error:
                unsupported.append({"id": other_id, "reason": str(error)})
        for name in REQUIRED_INVENTORIES - {"texts", "pins", "board_outline"}:
            for row in inventories.get(name, {}).get("rows", []):
                if name == "fabrication_graphics" and (
                    row["type"] not in (2, 4, 16) or row["side"] != semantic["side_code"]
                ):
                    continue
                try:
                    obstacles.append((row["id"], _shape(row["geometry"], budget), False))
                except (ValueError, KeyError) as error:
                    unsupported.append({"id": row["id"], "reason": str(error)})
        for pin in inventories.get("pins", {}).get("rows", []):
            for index, hole in enumerate(pin.get("holes", [])):
                if not hole["geometries"]:
                    unsupported.append(
                        {"id": pin["id"] + ":hole", "reason": "No complete native hole contour"}
                    )
                for number, g in enumerate(hole["geometries"]):
                    oid = f"{pin['id']}:hole:{index}:{number}"
                    try:
                        obstacles.append((oid, _shape(g, budget), False))
                    except (ValueError, KeyError) as error:
                        unsupported.append({"id": oid, "reason": str(error)})
            for kind in ("copper", "fabrication"):
                for pad in pin[kind]:
                    if kind == "copper" and pad["layer"] != (
                        1 if semantic["side"] == "top" else frame.get("layer_count")
                    ):
                        continue
                    if kind == "fabrication" and (
                        pad.get("type") != 2 or pad.get("side") != semantic["side_code"]
                    ):
                        continue
                    for index, g in enumerate(pad["geometries"]):
                        oid = f"{pin['id']}:{kind}:{pad['layer']}:{pad['index']}:{index}"
                        try:
                            obstacles.append((oid, _shape(g, budget), False))
                        except (ValueError, KeyError) as error:
                            unsupported.append({"id": oid, "reason": str(error)})
        for oid, shape, moved in obstacles:
            try:
                bound = _box_distance_squared(
                    _bounds(strokes, pen),
                    _bounds(shape.get("edges", []), shape["pen"], shape.get("disk")),
                )
                if bound > MEASUREMENT_RADIUS_MM**2:
                    pairs.append(
                        {
                            "target": target_id,
                            "obstacle": oid,
                            "distance_lower_bound_squared_mm2_fraction": _fraction(bound),
                            "measurement": "outside_bounded_measurement_window",
                            "gap_assessment": {
                                "status": "unverified",
                                "reason": "applicable rule unavailable",
                            },
                            "stroke_bodies_contact_or_uncertain": False,
                        }
                    )
                    continue
                measurement = compare_stroke_obstacle(
                    strokes,
                    pen,
                    shape,
                    moved_a=True,
                    moved_b=moved,
                    required_gap_mm=None,
                    budget=budget,
                )
                pairs.append({"target": target_id, "obstacle": oid, **measurement})
            except ValueError as error:
                unsupported.append({"id": oid, "reason": str(error)})
                if budget["pairs"] >= MAX_PAIR_COMPARISONS:
                    break
        # The outer boundary must enclose the whole stroke, not be treated as
        # a filled obstacle. Crossing/touching an edge or a cutout always refuses.
        try:
            board = _shape(inventories["board_outline"]["geometry"], budget)
            if "edges" not in board or not inventories["board_outline"]["geometry"]["closed"]:
                raise ValueError("Only a complete straight closed board boundary is supported")
            inside = all(_inside(p, board["vertices"]) for segment in strokes for p in segment)
            board["filled"] = False
            measured = compare_stroke_obstacle(
                strokes,
                pen,
                board,
                moved_a=True,
                moved_b=False,
                required_gap_mm=None,
                budget=budget,
            )
            pairs.append(
                {"target": target_id, "obstacle": "board_outline", "inside": inside, **measured}
            )
            if not inside:
                issues.append(target_id + ":outside_board")
        except (KeyError, ValueError) as error:
            unsupported.append({"id": "board_outline", "reason": str(error)})
    if unsupported:
        issues.append("unsupported_or_unmeasured_obstacle_material")
    coverage = {"complete": not native_issues, "issues": sorted(set(native_issues))}
    clearance = {
        "status": "satisfied"
        if pairs
        and not issues
        and not unsupported
        and all(
            p["gap_assessment"]["status"] == "margin_satisfied"
            and not p["stroke_bodies_contact_or_uncertain"]
            for p in pairs
        )
        else "incomplete",
        "pairs": pairs,
        "unsupported": unsupported,
        "issues": sorted(set(issues)),
        "budget": budget,
        "contact_policy": "bounded strict separation; all contacts or uncertain contacts refuse",
    }
    return {
        "contract": "silk-environment-qualification-v1-draft",
        "geometry_comparison": geometry,
        "native_input_coverage": coverage,
        "rule_coverage": rule_coverage,
        "clearance_protection": clearance,
        "measurement_scope": {
            "nearby_radius_mm": MEASUREMENT_RADIUS_MM,
            "window_is_not_a_clearance_rule": True,
            "far_objects": "exact conservative bounds retained; unknown rules never pass",
        },
        "write_eligibility": decision(geometry, coverage, rule_coverage, clearance),
        "_untrusted": ["clearance_protection", "rule_coverage", "native_input_coverage"],
    }
