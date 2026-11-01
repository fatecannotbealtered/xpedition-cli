"""Bounded native temporary-mask witnesses for observed pad and trace material.

Geometry.PointsArray's three rows are passed unchanged to the supported151.0
Mask Engine. This module does not interpret the native arc row, change database
geometry, activate an application, resolve rules, or perform persistence. Every
temporary mask created here is individually deleted; other clients' masks are
never enumerated, cleared, or removed.
"""

from __future__ import annotations

import copy
import math

from .cell_refresh import _canonical, digest
from .design_capabilities import LIMITS, UNASSIGNED_NET_NAME, named_net_ownership

UNIT_MM = 4
MAX_PINS = LIMITS["pins"]
MAX_TRACES = LIMITS["traces"]
MAX_CONTOURS = LIMITS["contours"]
MAX_POINTS = LIMITS["mask_points"]
MAX_COMPARISONS = LIMITS["comparisons"]
MAX_MASKS = LIMITS["temporary_masks"]
TARGET_PIN_COUNTS = (2, 3, 6)
LEGACY_PROFILE = "legacy-routepass-material-v1"
COMPONENT_PROFILE = "single-net-single-layer-material-components-v1"
MAX_COMPONENT_PINS = LIMITS["target_net_pins"]
TRACE_VOID_CONTRACT = (
    "all observed round pin holes subtracted; positive target hole intersections require "
    "strict terminal crops; pad cutouts remain local to pad material"
)

# Installed151.0 IMask.Touch: memid10, help60024, flags0,
# "Do these two masks touch?". The type description is retained in the
# R12 formal/mask-lifecycle-151-metadata.json; these controls test the actual
# engine's boundary semantics before any native routing pass is authorized.
TOUCH_FORMAL_SOURCE = {
    "interface": "IMask.Touch151",
    "memid": 10,
    "help_context": 60024,
    "type_library_sha256": "8c0b91981a7706f6680538f1a06459ac81a6ad2b0a3abdadf4ae41ff2ff7cca3",
    "nonconvex_control_construction": {
        "factory": "Utility.CreateRectXYR",
        "factory_memid": 2,
        "factory_help": 60053,
        "union": "Engine.BooleanOp",
        "union_memid": 1,
        "union_help": 60029,
        "operation": 2,
    },
}


def _touch_control_inputs():
    def polygon(points):
        return {
            "kind": "native_points_array",
            "points_array_mm": [[p[i] for p in points] for i in (0, 1)] + [[0] * len(points)],
            "closed": True,
            "is_arced": False,
        }

    def rectangle(x0, y0, x1, y1):
        return polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])

    base = rectangle(0, 0, 2, 2)
    return [
        {
            "name": "positive_overlap",
            "left": base,
            "right": rectangle(1, 0, 3, 2),
            "expected_touch": True,
            "expected_area_mm2": 2,
        },
        {
            "name": "edge_tangent",
            "left": base,
            "right": rectangle(2, 0, 4, 2),
            "expected_touch": True,
            "expected_area_mm2": 0,
        },
        {
            "name": "corner_tangent",
            "left": base,
            "right": rectangle(2, 2, 4, 4),
            "expected_touch": True,
            "expected_area_mm2": 0,
        },
        {
            "name": "positive_gap",
            "left": base,
            "right": rectangle(2.01, 0, 4, 2),
            "expected_touch": False,
            "expected_area_mm2": 0,
        },
        {
            "name": "bounds_overlap_material_separated",
            "left": {
                "kind": "native_rectangle_union",
                "rectangles_mm": [[0, 0, 3, 1], [0, 0, 1, 3]],
                "operation": 2,
                "expected_area_mm2": 5,
            },
            "right": {"kind": "native_rectangle_factory", "bounds_mm": [2, 2, 2.5, 2.5]},
            "left_contains_points": [
                {"point_mm": [0.5, 2], "expected": True},
                {"point_mm": [2, 0.5], "expected": True},
                {"point_mm": [0.5, 0.5], "expected": True},
                {"point_mm": [2.25, 2.25], "expected": False},
            ],
            "expected_touch": False,
            "expected_area_mm2": 0,
        },
    ]


def _touch_controls(owner, rows):
    for control in _touch_control_inputs():
        if control["left"].get("kind") == "native_rectangle_union":
            construction = {
                "source": copy.deepcopy(TOUCH_FORMAL_SOURCE["nonconvex_control_construction"]),
                "rectangles": [],
            }
            record = {**control, "native_construction": construction}
            rows.append(record)
            masks = []
            utility = owner.a._propget(owner.engine, 2)
            bounds_rows = control["left"]["rectangles_mm"] + [control["right"]["bounds_mm"]]
            for bounds in bounds_rows:
                raw = _array(utility.CreateRectXYR(*bounds), owner.points)
                observed = {"bounds_mm": bounds, "points_array_mm": raw, "raw_sha256": digest(raw)}
                construction["rectangles"].append(observed)
                mask = owner.contour(
                    {
                        "kind": "native_points_array",
                        "closed": True,
                        "is_arced": False,
                        "points_array_mm": raw,
                    }
                )
                observed.update(owner.area(mask))
                if observed["area_mm2"] != (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]):
                    raise ValueError("Native factory rectangle control area differs")
                masks.append(mask)
            left, right = owner.boolean(2, masks[0], masks[1]), masks[2]
            construction["left_union_area"] = owner.area(left)
            if construction["left_union_area"]["area_mm2"] != 5:
                raise ValueError("Native rectangle union control is not the expected L material")
            construction["left_contains_points"] = []
            for point in control["left_contains_points"]:
                inside = left.ContainsPoint(*point["point_mm"], UNIT_MM)
                construction["left_contains_points"].append({**point, "actual": inside})
                if type(inside) is not bool or inside is not point["expected"]:
                    raise ValueError("Native rectangle union control containment differs")
        else:
            left, right = owner.contour(control["left"]), owner.contour(control["right"])
            record = {**control}
            rows.append(record)
        touch = left.Touch(right)
        intersection = owner.area(owner.boolean(1, left, right))
        record.update(actual_touch=touch, **intersection)
        if type(touch) is not bool:
            raise ValueError("Formal native Mask.Touch control did not return BOOL")
        if (
            touch != control["expected_touch"]
            or intersection["area_mm2"] != control["expected_area_mm2"]
        ):
            raise ValueError("Formal native Mask.Touch boundary control failed: " + control["name"])


def _number(value, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Native material number is not finite")
    if positive and value <= 0:
        raise ValueError("Native material size must be positive")
    return value


def _items(value, a, maximum):
    count = value.Count
    if type(count) is not int or not 0 <= count <= maximum:
        raise ValueError("Native material collection Count is unavailable or over budget")
    rows = list(a._items(value, strict=True))
    if len(rows) != count:
        raise ValueError("Native material collection enumeration is incomplete")
    return rows


def _array(value, budget, *, minimum=3):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(not isinstance(row, (list, tuple)) for row in value)
        or len({len(row) for row in value}) != 1
        or not minimum <= len(value[0]) <= 128
    ):
        raise ValueError("Native x/y/arc rows are malformed or exceed128 points")
    for row in value:
        for number in row:
            _number(number)
    budget["points"] += len(value[0])
    if budget["points"] > budget["maximum_points"]:
        raise ValueError("Native point observation budget exceeded")
    # Preserve row order, actual coordinates and the vendor's arc representation.
    return [list(row) for row in value]


def _bounds(value):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("Complete actual material bounds are unavailable")
    for number in value:
        _number(number)
    if value[0] >= value[2] or value[1] >= value[3]:
        raise ValueError("Actual material bounds have no positive extent")
    return value


def _separated(left, right):
    return left[2] < right[0] or right[2] < left[0] or left[3] < right[1] or right[3] < left[1]


def _trace_bounds(trace):
    # Trace.Extrema is an actual native observation. Do not infer caps or joins
    # from the centerline and width, or fall back when this read is incomplete.
    return _bounds(trace["bounds_mm"])


def _round_holes(materials):
    """Physical round voids, deduplicated without dropping their source binding."""
    holes = {}
    for pin in materials:
        for hole in pin["material"]["holes"]:
            center = hole["center_mm"]
            if not isinstance(center, list) or len(center) != 2:
                raise ValueError("Complete observed round-hole centers are required")
            for coordinate in center:
                _number(coordinate)
            diameter = _number(hole["diameter_mm"], positive=True)
            row = {"center_mm": list(center), "diameter_mm": diameter}
            holes[digest(row)] = row
    if len(holes) > MAX_PINS:
        raise ValueError("Whole round-hole observation budget exceeded")
    return [holes[key] for key in sorted(holes)]


def _terminal_hole_cut(trace, hole):
    """A sufficient straight-path condition for a hole to crop only an end.

    This is not a general polygon-component solver. A physical hole through the
    middle of a trace is refused even if the vendor still calls it one Trace.
    The adjacent segment must extend completely beyond the enlarged circle,
    and every other segment must be strictly outside it. A centered circular
    end crop with radius >= half-width leaves one continuous remaining path.
    """
    points = list(zip(trace["points_array_mm"][0], trace["points_array_mm"][1], strict=True))
    center = tuple(hole["center_mm"])
    ends = [index for index in (0, len(points) - 1) if points[index] == center]
    if len(ends) != 1:
        raise ValueError("A target trace intersects a hole away from its exact single endpoint")
    half_width, radius = trace["width_mm"] / 2, hole["diameter_mm"] / 2
    if radius < half_width:
        raise ValueError("A target trace end hole is smaller than the full-width crop contract")
    guard = radius + half_width
    index = 0 if ends[0] == 0 else len(points) - 2
    adjacent_length = _number(math.dist(points[index], points[index + 1]), positive=True)
    if adjacent_length <= guard:
        raise ValueError("A target trace end segment does not fully leave its physical hole")
    distances = []
    for segment, (left, right) in enumerate(zip(points, points[1:], strict=False)):
        if segment == index:
            continue
        dx, dy = right[0] - left[0], right[1] - left[1]
        length2 = _number(dx * dx + dy * dy, positive=True)
        projection = ((center[0] - left[0]) * dx + (center[1] - left[1]) * dy) / length2
        projection = min(1, max(0, projection))
        distance = _number(
            math.dist(center, (left[0] + projection * dx, left[1] + projection * dy))
        )
        if distance <= guard:
            raise ValueError(
                "A target trace re-enters an end hole or its connectedness is unproven"
            )
        distances.append({"segment_index": segment, "distance_mm": distance})
    return {
        "kind": "strict_terminal_round_hole_crop",
        "end_index": 0 if ends[0] == 0 else 1,
        "adjacent_segment_index": index,
        "adjacent_length_mm": adjacent_length,
        "radius_plus_half_width_mm": guard,
        "other_segment_distances": distances,
    }


def _terminal_crop_pairs(trace, holes, intersections):
    active = [row["hole_index"] for row in intersections if row["area_mm2"] > 0]
    pairs = []
    for index, left in enumerate(active):
        for right in active[index + 1 :]:
            first, second = holes[left], holes[right]
            if first["center_mm"] == second["center_mm"]:
                # Concentric end crops have the same effect as their largest
                # radius; they do not create two independent void neighborhoods.
                continue
            distance = _number(math.dist(first["center_mm"], second["center_mm"]))
            required = (first["diameter_mm"] + second["diameter_mm"]) / 2 + trace["width_mm"]
            if distance <= required:
                raise ValueError(
                    "Target trace terminal-hole neighborhoods are not strictly separated"
                )
            pairs.append(
                {
                    "left_hole_index": left,
                    "right_hole_index": right,
                    "center_distance_mm": distance,
                    "strict_guard_distance_mm": required,
                }
            )
    return pairs


def _contour(geometry, a, budget):
    budget["contours"] += 1
    if budget["contours"] > budget["maximum_contours"]:
        raise ValueError("Native contour observation budget exceeded")
    if geometry.IsValid() is not True:
        raise ValueError("Native pad contour is invalid or unreadable")
    circle = geometry.IsCircle()
    if type(circle) is not bool:
        raise ValueError("Native circle classification is unreadable")
    if circle:
        x, y = (_number(a._propget(geometry, key, UNIT_MM)) for key in (10, 11))
        radius = _number(a._propget(geometry, 12, UNIT_MM), positive=True)
        budget["points"] += 1
        if budget["points"] > budget["maximum_points"]:
            raise ValueError("Native point observation budget exceeded")
        return {"kind": "circle", "center_mm": [x, y], "radius_mm": radius}
    arced = geometry.IsArced()
    if geometry.IsClosed() is not True or type(arced) is not bool:
        raise ValueError("Native pad contour closure or arc observation is unavailable")
    return {
        "kind": "native_points_array",
        "points_array_mm": _array(a._propget(geometry, 1, UNIT_MM), budget),
        "closed": True,
        "is_arced": arced,
    }


def observe_pin(pin, layer, a):
    """Observe actual owned filled pad contours, cutouts and round drill holes.

    All getters use explicit EPcbUnitMM4 where supported. Pad names are retained
    as observations and never used to construct material. ``complete`` describes
    these raw native observations, not a Python interpretation of native arcs.
    The caller binds the actual persistent component/pin/net ownership identities.
    """
    result = {
        "source": "native_pin_owned_pad_material_mask151",
        "unit": "mm",
        "layer": layer,
        "pads": [],
        "holes": [],
        "unread": [],
        "complete": False,
    }
    budget = {"contours": 0, "points": 0, "maximum_contours": 128, "maximum_points": 4096}
    try:
        if type(layer) is not int or not 1 <= layer <= 128:
            raise ValueError("Actual conductor layer identity is required")
        pads = _items(a._propget(pin, 20, layer, True), a, 32)
        if not pads:
            raise ValueError("No effective native layer pad was observed")
        for index, pad in enumerate(pads):
            if (
                type(pad.Layer) is not int
                or pad.Layer != layer
                or pad.IsNegative() is not False
                or pad.IsNoConnect() is not False
            ):
                raise ValueError("Negative, no-connect or incorrectly layered pad is unsupported")
            name = pad.Name
            if not isinstance(name, str) or not name:
                raise ValueError("Observed pad name is unavailable")
            extrema = a._propget(pad, 7)
            if extrema.IsValid() is not True:
                raise ValueError("Actual native owned-pad extrema are invalid or unavailable")
            bounds = _bounds([_number(a._propget(extrema, key, UNIT_MM)) for key in (1, 2, 3, 4)])
            record = {
                "observation_index": index,
                "name": name,
                "layer": layer,
                "bounds_mm": bounds,
                "geometry": [],
            }
            result["pads"].append(record)
            geometries = _items(a._propget(pad, 4), a, 32)
            if not geometries:
                raise ValueError("Native owned pad geometries are empty")
            for shape_index, geometry in enumerate(geometries):
                if a._propget(geometry, 21) is not True:
                    raise ValueError("Native pad is not an observed filled area")
                outer = _contour(geometry, a, budget)
                cutouts = []
                for cut in _items(a._propget(geometry, 22), a, 32):
                    cutouts.append(_contour(cut, a, budget))
                    if _items(a._propget(cut, 22), a, 32):
                        raise ValueError("Nested native pad cutouts are unsupported")
                record["geometry"].append(
                    {
                        "observation_index": shape_index,
                        "filled": True,
                        "outer": outer,
                        "cutouts": cutouts,
                    }
                )
        for index, hole in enumerate(_items(a._propget(pin, 25), a, 32)):
            if hole.IsValid() is not True or type(hole.ShapeType) is not int or hole.ShapeType != 1:
                raise ValueError("Only valid observed round owned holes are supported")
            plated = hole.Plated
            if type(plated) is not bool:
                raise ValueError("Native owned hole plating is unreadable")
            result["holes"].append(
                {
                    "observation_index": index,
                    "center_mm": [_number(a._propget(hole, key, UNIT_MM)) for key in (4, 5)],
                    "diameter_mm": _number(a._propget(hole, 1, UNIT_MM), positive=True),
                    "plated": plated,
                }
            )
        result["complete"] = True
    except Exception as error:
        result["unread"].append(
            {
                "reason": str(error),
                "cause": type(error).__name__,
                "affected_requirement": "complete native owned pad material masks",
            }
        )
    result["geometry_budget"] = budget
    return result


class _Masks:
    """Own only masks created by this invocation; cleanup is not design rollback."""

    def __init__(self, engine, a):
        self.engine, self.a = engine, a
        self.collection = a._propget(engine, 0)
        self.initial_count = self.collection.Count
        if type(self.initial_count) is not int or not 0 <= self.initial_count <= MAX_MASKS:
            raise ValueError("Native existing Mask Count is unavailable or over budget")
        self.owned = []
        self.cleanup = []
        self.points = {"points": 0, "maximum_points": MAX_POINTS}
        self.final_count = None
        self.region_comparisons = 0

    def own(self, mask):
        if mask is None:
            raise ValueError("Native temporary Mask was not returned")
        self.owned.append(mask)
        if len(self.owned) > MAX_MASKS:
            raise ValueError("Native temporary Mask allocation budget exceeded")
        return mask

    def new(self):
        return self.own(self.collection.Add())

    def boolean(self, operation, left, right):
        # Engine.BooleanOp returns a NEW mask. Mask.BooleanOp is intentionally
        # not used: its documented in-place mutation is a different contract.
        return self.own(self.engine.BooleanOp(operation, left, right))

    def contour(self, contour):
        if not isinstance(contour, dict):
            raise ValueError("Observed native contour record is unavailable")
        kind = contour.get("kind")
        if kind == "circle":
            center = contour.get("center_mm")
            if not isinstance(center, list) or len(center) != 2:
                raise ValueError("Observed native circle center is incomplete")
            x, y = [_number(v) for v in center]
            radius = _number(contour.get("radius_mm"), positive=True)
            utility = self.a._propget(self.engine, 2)
            raw = utility.CreateCircleXYR(x, y, radius)
        elif (
            kind == "native_points_array"
            and contour.get("closed") is True
            and type(contour.get("is_arced")) is bool
        ):
            raw = contour.get("points_array_mm")
        else:
            raise ValueError("Observed native contour kind is unsupported")
        rows = _array(raw, self.points, minimum=1 if kind == "circle" else 3)
        mask = self.new()
        shapes = self.a._propget(mask, 1)
        shape = shapes.AddByPointsArray(len(rows[0]), rows, True, UNIT_MM)
        if shape is None:
            raise ValueError("Native filled Shape was not returned")
        return mask

    def path(self, trace):
        rows = _array(trace["points_array_mm"], self.points, minimum=2)
        width = _number(trace["width_mm"], positive=True)
        mask = self.new()
        shapes = self.a._propget(mask, 1)
        returned = shapes.AddByPath(len(rows[0]), rows, width, True, 0, UNIT_MM)
        if returned is not None:
            raise ValueError("Formal void Shapes.AddByPath returned an unexpected value")
        return mask

    def area(self, mask):
        area = _number(self.a._propget(mask, 2, UNIT_MM))
        empty = mask.IsEmpty()
        count = self.a._propget(mask, 1).Count
        if (
            area < 0
            or type(empty) is not bool
            or empty != (area == 0)
            or type(count) is not int
            or not 0 <= count <= MAX_CONTOURS
            or (area > 0 and count == 0)
        ):
            raise ValueError("Native Mask area and emptiness are inconsistent")
        return {"area_mm2": area, "is_empty": empty, "shape_count": count}

    def delete_owned(self):
        # Individual Delete is supported. Never enumerate, clear, or delete the
        # engine-wide Masks collection, and never retry a failed deletion.
        for index in range(len(self.owned) - 1, -1, -1):
            row = {"owned_allocation_index": index, "delete_returned": False}
            self.cleanup.append(row)
            try:
                returned = self.owned[index].Delete()
                if returned is not None:
                    raise ValueError("Formal void Mask.Delete returned an unexpected value")
                row["delete_returned"] = True
            except Exception as error:
                row.update(error=str(error), cause=type(error).__name__)
        self.owned.clear()
        try:
            self.final_count = self.collection.Count
        except Exception:
            self.final_count = None


def _material_masks(owner, material):
    if (
        not isinstance(material, dict)
        or material.get("source") != "native_pin_owned_pad_material_mask151"
        or material.get("complete") is not True
        or material.get("unit") != "mm"
        or material.get("unread") != []
        or not isinstance(material.get("pads"), list)
        or not 1 <= len(material["pads"]) <= 32
        or not isinstance(material.get("holes"), list)
        or len(material["holes"]) > 32
    ):
        raise ValueError("Complete raw native owned-pad material observations are required")
    outer_union, material_union = None, None
    pad_materials = []
    contour_count = 0
    for pad in material["pads"]:
        _bounds(pad.get("bounds_mm"))
        if pad.get("layer") != material["layer"]:
            raise ValueError("Observed pad layer differs from its material identity")
        geometries = pad.get("geometry")
        if not isinstance(geometries, list) or not 1 <= len(geometries) <= 32:
            raise ValueError("Observed pad geometry collection is incomplete")
        pad_material = None
        for shape in geometries:
            if shape.get("filled") is not True:
                raise ValueError("Observed pad shape is not filled material")
            cutouts = shape.get("cutouts")
            if not isinstance(cutouts, list) or len(cutouts) > 32:
                raise ValueError("Observed pad cutout collection is incomplete")
            contour_count += 1 + len(cutouts)
            if contour_count > 128:
                raise ValueError("Per-pin contour budget exceeded")
            outer = owner.contour(shape["outer"])
            outer_union = outer if outer_union is None else owner.boolean(2, outer_union, outer)
            shaped = outer
            for cutout in cutouts:
                shaped = owner.boolean(0, shaped, owner.contour(cutout))
            pad_material = (
                shaped if pad_material is None else owner.boolean(2, pad_material, shaped)
            )
        pad_materials.append(pad_material)
    for hole in material["holes"]:
        center = hole.get("center_mm")
        if type(hole.get("plated")) is not bool:
            raise ValueError("Observed round owned hole plating is incomplete")
        diameter = _number(hole.get("diameter_mm"), positive=True)
        cut = owner.contour({"kind": "circle", "center_mm": center, "radius_mm": diameter / 2})
        pad_materials = [owner.boolean(0, pad_mask, cut) for pad_mask in pad_materials]
    for pad_mask in pad_materials:
        material_union = (
            pad_mask if material_union is None else owner.boolean(2, material_union, pad_mask)
        )
    if outer_union is None or material_union is None:
        raise ValueError("Owned pad mask is empty or unavailable")
    for mask in (outer_union, material_union):
        if owner.area(mask)["area_mm2"] <= 0:
            raise ValueError("Observed owned pad has no positive native material area")
    return outer_union, material_union, contour_count, pad_materials


def _cross(a, b, c):
    return _number((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))


def _segments_meet(a, b, c, d):
    def on(a, b, p):
        return (
            _cross(a, b, p) == 0
            and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
        )

    return (
        (_cross(a, b, c) * _cross(a, b, d) < 0 and _cross(c, d, a) * _cross(c, d, b) < 0)
        or on(a, b, c)
        or on(a, b, d)
        or on(c, d, a)
        or on(c, d, b)
    )


def _simple_polygon(contour):
    if contour.get("kind") != "native_points_array" or contour.get("is_arced") is not False:
        raise ValueError("Connected polygon witness requires observed straight edges")
    raw = _array(contour["points_array_mm"], {"points": 0, "maximum_points": 128})
    if contour.get("closed") is not True or any(raw[2]):
        raise ValueError("Connected polygon witness requires complete closed straight edges")
    vertices = [tuple(row) for row in zip(raw[0], raw[1], strict=True)]
    if vertices[0] == vertices[-1]:
        vertices.pop()
    if len(vertices) < 3 or len(set(vertices)) != len(vertices):
        raise ValueError("Repeated or insufficient polygon vertices are unsupported")
    for index, b in enumerate(vertices):
        a, c = vertices[index - 1], vertices[(index + 1) % len(vertices)]
        if _cross(a, b, c) == 0 and (
            (a[0] - b[0]) * (c[0] - b[0]) + (a[1] - b[1]) * (c[1] - b[1]) > 0
        ):
            raise ValueError("Backtracking polygon boundary is unsupported")
    edges = list(zip(vertices, vertices[1:] + vertices[:1], strict=True))
    for index, (a, b) in enumerate(edges):
        for other, (c, d) in enumerate(edges[index + 1 :], start=index + 1):
            if other == index + 1 or (index == 0 and other == len(edges) - 1):
                # Adjacent collinear backtracking also invalidates a Jordan boundary.
                if (
                    other == index + 1
                    and _cross(a, b, d) == 0
                    and ((a[0] - b[0]) * (d[0] - b[0]) + (a[1] - b[1]) * (d[1] - b[1]) > 0)
                ):
                    raise ValueError("Backtracking polygon boundary is unsupported")
                continue
            if _segments_meet(a, b, c, d):
                raise ValueError("Self-intersecting or touching polygon boundary is unsupported")
    if _number(sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)) == 0:
        raise ValueError("Polygon has no positive interior")
    return vertices


def _strict_inside(point, polygon):
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if _segments_meet(a, b, point, point):
            return False
        if (a[1] > point[1]) != (b[1] > point[1]):
            if point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]:
                inside = not inside
    return inside


def _contour_bounds(record):
    if record.get("kind") == "circle":
        x, y = record["center_mm"]
        radius = _number(record["radius_mm"], positive=True)
        return [x - radius, y - radius, x + radius, y + radius]
    raw = record["points_array_mm"]
    return [min(raw[0]), min(raw[1]), max(raw[0]), max(raw[1])]


def _connected_shape_plan(outer, voids):
    """Prove a bounded disk with strictly interior, mutually separated voids.

    No native XYR arc is interpreted. The rounded-rectangle family is merely a
    candidate; evaluate must prove its actual equality with native BooleanOp.
    A crossing cutout, tangency, arbitrary arc or self-touch stays unsupported.
    """
    if outer.get("kind") == "circle":
        center = outer["center_mm"]
        radius = _number(outer["radius_mm"], positive=True)
        polygon, kind = None, "circle"
    elif outer.get("is_arced") is False:
        polygon, kind = _simple_polygon(outer), "simple_polygon"
    else:
        raw = _array(outer["points_array_mm"], {"points": 0, "maximum_points": 128})
        radii = {abs(value) for value in raw[2] if value != 0}
        bounds = _contour_bounds(outer)
        if (
            len(radii) != 1
            or len(raw[0]) != 13
            or sum(value != 0 for value in raw[2]) != 4
            or voids
            or outer.get("closed") is not True
            or outer.get("is_arced") is not True
        ):
            raise ValueError("Arced pad topology is outside the witnessed rounded rectangle family")
        radius = next(iter(radii))
        if 2 * radius >= min(bounds[2] - bounds[0], bounds[3] - bounds[1]):
            raise ValueError("Rounded rectangle candidate has no positive overlapping core")
        return {
            "kind": "native_equivalent_rounded_rectangle",
            "bounds_mm": bounds,
            "radius_mm": radius,
        }
    for index, void in enumerate(voids):
        if void.get("kind") == "circle":
            point = void["center_mm"]
            r = _number(void["radius_mm"], positive=True)
            if polygon is None:
                contained = math.dist(point, center) + r < radius
            else:
                contained = _strict_inside(point, polygon)
                for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True):
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    t = max(
                        0,
                        min(
                            1,
                            ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy),
                        ),
                    )
                    contained = contained and math.dist(point, [a[0] + t * dx, a[1] + t * dy]) > r
        else:
            inner = _simple_polygon(void)
            if polygon is None:
                contained = all(math.dist(point, center) < radius for point in inner)
            else:
                contained = all(_strict_inside(point, polygon) for point in inner) and not any(
                    _segments_meet(a, b, c, d)
                    for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True)
                    for c, d in zip(inner, inner[1:] + inner[:1], strict=True)
                )
        if not contained:
            raise ValueError("Pad void reaches its outer boundary; material may split into islands")
        if any(
            not _separated(_contour_bounds(void), _contour_bounds(other)) for other in voids[:index]
        ):
            raise ValueError("Pad voids touch or overlap; connected residual material is unproved")
    return {"kind": kind, "strictly_interior_separated_void_count": len(voids)}


def _rounded_witness(owner, plan, actual):
    x0, y0, x1, y1 = plan["bounds_mm"]
    radius = plan["radius_mm"]
    shapes = []
    for bounds in ([x0, y0 + radius, x1, y1 - radius], [x0 + radius, y0, x1 - radius, y1]):
        raw = owner.a._propget(owner.engine, 2).CreateRectXYR(*bounds)
        shapes.append(
            owner.contour(
                {
                    "kind": "native_points_array",
                    "closed": True,
                    "is_arced": False,
                    "points_array_mm": raw,
                }
            )
        )
    for x in (x0 + radius, x1 - radius):
        for y in (y0 + radius, y1 - radius):
            shapes.append(
                owner.contour({"kind": "circle", "center_mm": [x, y], "radius_mm": radius})
            )
    candidate = shapes[0]
    for shape in shapes[1:]:
        candidate = owner.boolean(2, candidate, shape)
    forward = owner.area(owner.boolean(0, actual, candidate))
    reverse = owner.area(owner.boolean(0, candidate, actual))
    if forward["area_mm2"] != 0 or reverse["area_mm2"] != 0:
        raise ValueError("Actual arced material is not the connected rounded rectangle candidate")
    return {"actual_minus_candidate": forward, "candidate_minus_actual": reverse}


def _pad_region_witness(owner, pin, traces, trace_masks):
    """Retain every proved connected owned region; never union by pin UID."""
    result = {
        "component_uid": pin["component_uid"],
        "pin_uid": pin["pin_uid"],
        "material_sha256": digest(pin["material"]),
        "complete": False,
        "regions": [],
        "region_intersections": [],
        "contacts": [],
    }
    masks = []
    try:
        holes = [
            {"kind": "circle", "center_mm": hole["center_mm"], "radius_mm": hole["diameter_mm"] / 2}
            for hole in pin["material"]["holes"]
        ]
        for pad_index, pad in enumerate(pin["material"]["pads"]):
            for shape_index, shape in enumerate(pad["geometry"]):
                voids = shape["cutouts"] + holes
                plan = _connected_shape_plan(shape["outer"], voids)
                mask = owner.contour(shape["outer"])
                equality = (
                    _rounded_witness(owner, plan, mask)
                    if plan["kind"] == "native_equivalent_rounded_rectangle"
                    else None
                )
                for void in voids:
                    mask = owner.boolean(0, mask, owner.contour(void))
                area = owner.area(mask)["area_mm2"]
                if area <= 0:
                    raise ValueError("Connected owned pad region has no positive material")
                index = len(masks)
                masks.append(mask)
                result["regions"].append(
                    {
                        "region_index": index,
                        "pad_index": pad_index,
                        "shape_index": shape_index,
                        "source_sha256": digest(shape),
                        "topology": plan,
                        "native_equality": equality,
                        "material_area_mm2": area,
                    }
                )
        for index, mask in enumerate(masks):
            for other in range(index + 1, len(masks)):
                area = _region_area(owner, mask, masks[other])
                result["region_intersections"].append(
                    {"left_region_index": index, "right_region_index": other, **area}
                )
            for trace in traces:
                area = _region_area(owner, mask, trace_masks[trace["uid"]])
                result["contacts"].append(
                    {
                        "region_index": index,
                        "trace_uid": trace["uid"],
                        "raw_trace_sha256": digest(trace),
                        **area,
                    }
                )
        result["complete"] = True
    except (ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        result["reason"] = str(error)
    return result, masks


def _region_area(owner, left, right):
    owner.region_comparisons += 1
    if owner.region_comparisons > MAX_COMPARISONS:
        raise ValueError("Owned-region material comparison budget exceeded")
    return owner.area(owner.boolean(1, left, right))


def _pair_key(
    trace_uid,
    foreign_kind,
    *,
    component_uid=None,
    pin_uid=None,
    pad_index=None,
    foreign_trace_uid=None,
):
    return (trace_uid, foreign_kind, component_uid, pin_uid, pad_index, foreign_trace_uid)


def _foreign_pairs(materials, traces, target_net_uid, layer):
    foreign = []
    for pin in materials:
        if pin["net_uid"] == target_net_uid:
            continue
        for index, pad in enumerate(pin["material"]["pads"]):
            foreign.append(
                {
                    "foreign_kind": "pad",
                    "foreign_component_uid": pin["component_uid"],
                    "foreign_pin_uid": pin["pin_uid"],
                    "foreign_pad_index": index,
                    "foreign_bounds_mm": _bounds(pad.get("bounds_mm")),
                }
            )
    for trace in traces:
        if trace["net_uid"] != target_net_uid and trace["layer"] == layer:
            foreign.append(
                {
                    "foreign_kind": "trace",
                    "foreign_trace_uid": trace["uid"],
                    "foreign_bounds_mm": _trace_bounds(trace),
                }
            )
    return foreign


def _trace_inventory(doc, state, a):
    collection = doc.Traces
    if callable(collection) and not hasattr(collection, "Count"):
        collection = collection()
    rows, seen = [], set()
    budget = {"points": 0, "maximum_points": MAX_POINTS}
    expected = {(r["kind"], r["uid"]): r for r in state["routing_objects"]}
    net_rows = state["geometry"]["model"]["connectivity"]["nets"]
    net_ids = named_net_ownership(net_rows, state["all_pin_pad_material"])
    if len(expected) != len(state["routing_objects"]) or any(k[0] != "trace" for k in expected):
        raise ValueError("Native mask mode does not accept missing identities or via material")
    for trace in _items(collection, a, MAX_TRACES):
        uid, net, net_uid = str(trace.UniqueId), str(trace.Net.Name), str(trace.Net.UniqueId)
        if not uid or uid == "0" or uid in seen or not net or not net_uid or net_uid == "0":
            raise ValueError("Native trace identity is absent or duplicated")
        if net_ids.get(net) != net_uid:
            raise ValueError(
                "Actual raw trace owning net differs from complete native connectivity"
            )
        seen.add(uid)
        layer = trace.Layer
        if type(layer) is not int or not 1 <= layer <= 128:
            raise ValueError("Native trace layer is unavailable")
        geometry = trace.Geometry
        if geometry.IsValid() is not True or geometry.IsArced() is not False:
            raise ValueError("Only valid observed straight native trace paths are supported")
        raw = _array(a._propget(geometry, 1, UNIT_MM), budget, minimum=2)
        if any(v != 0 for v in raw[2]):
            raise ValueError("Native trace is not an observed straight path")
        width = _number(a._propget(geometry, 5, UNIT_MM), positive=True)
        extrema = a._propget(trace, 7)
        if extrema.IsValid() is not True:
            raise ValueError("Actual native Trace.Extrema is invalid or unavailable")
        bounds = _bounds([_number(a._propget(extrema, key, UNIT_MM)) for key in (1, 2, 3, 4)])
        if any(
            not bounds[0] <= x <= bounds[2] or not bounds[1] <= y <= bounds[3]
            for x, y in zip(raw[0], raw[1], strict=True)
        ):
            raise ValueError("Actual native Trace.Extrema does not contain its observed centerline")
        model = {
            "uid": uid,
            "kind": "trace",
            "net": net,
            "layer": layer,
            "width": width,
            "path": [list(point) for point in zip(*raw, strict=True)],
        }
        bound = expected.get(("trace", uid))
        if bound is None or bound["net"] != net or bound["geometry"] != _canonical(model):
            raise ValueError("Raw native trace and complete independently captured model differ")
        rows.append(
            {
                "uid": uid,
                "net": net,
                "net_uid": net_uid,
                "layer": layer,
                "width_mm": width,
                "points_array_mm": raw,
                "bounds_mm": bounds,
                "bounds_source": "Trace.Extrema.MinX/MinY/MaxX/MaxY(EPcbUnitMM4)",
                "geometry_source": "Geometry.PointsArray(EPcbUnitMM4),LineWidth(EPcbUnitMM4)",
            }
        )
    if seen != {key[1] for key in expected}:
        raise ValueError("Raw native trace collection is incomplete")
    return sorted(rows, key=lambda row: row["uid"])


def _profile_target_net_uid(state, request, materials, profile):
    """Bind the generic profile to an observed net, including a net with no pins."""
    if profile not in (LEGACY_PROFILE, COMPONENT_PROFILE):
        raise ValueError("Unknown native material observation profile")
    if isinstance(request, dict) and request.get("net") == UNASSIGNED_NET_NAME:
        raise ValueError("Unassigned (Net0) pins are foreign material, never a target network")
    if profile == COMPONENT_PROFILE and (
        not isinstance(request, dict)
        or set(request) != {"net", "net_uid", "layer"}
        or not isinstance(request["net"], str)
        or not request["net"]
        or type(request["layer"]) is not int
        or not 1 <= request["layer"] <= 128
    ):
        raise ValueError("Component observation requires exactly one actual net UID/name and layer")
    target_pins = [row for row in materials if row["net"] == request["net"]]
    if profile == LEGACY_PROFILE:
        if (
            len(target_pins) not in TARGET_PIN_COUNTS
            or len({row["net_uid"] for row in target_pins}) != 1
        ):
            raise ValueError(
                "Two, three or six native owned target pins and one net UID are required"
            )
        uid = target_pins[0]["net_uid"]
    else:
        uid = request["net_uid"]
        nets = state["geometry"]["model"]["connectivity"]["nets"]
        if (
            len(target_pins) > MAX_COMPONENT_PINS
            or not isinstance(uid, str)
            or not uid
            or uid == "0"
            or len([row for row in nets if row["net"] == request["net"]]) != 1
            or len([row for row in nets if row["uid"] == uid]) != 1
            or not any(row["net"] == request["net"] and row["uid"] == uid for row in nets)
            or any(row["net_uid"] != uid for row in target_pins)
        ):
            raise ValueError("Bounded target pins and actual unique requested net UID are required")
    if not isinstance(uid, str) or not uid or uid == "0":
        raise ValueError("Native target net UID is unavailable")
    return uid


def evaluate(doc, state, request, a, *, profile=LEGACY_PROFILE):
    """Recompute native material witnesses on the retained authenticated document.

    The enclosing observer binds document/process identity and checkpoint. Each
    invocation obtains and deletes its own temporary Masks. A producer's prior
    boolean proof is never accepted as a substitute for fresh native operations.
    """
    result = {
        "source": "native_mask_engine151",
        "unit": "mm",
        "complete": False,
        "target_contacts": [],
        "target_trace_intersections": [],
        "round_holes": [],
        "trace_materials": [],
        "mask_touch_controls": [],
        "mask_touch_formal_source": copy.deepcopy(TOUCH_FORMAL_SOURCE),
        "pin_materials": [],
        "target_pad_regions": [],
        "target_pad_intersections": [],
        "foreign_intersections": [],
        "raw_trace_inventory": [],
        "errors": [],
        "cleanup": [],
        "not_checked": ["spacing rules", "DRC", "manufacturing", "thermal/tie"],
        "trace_void_contract": TRACE_VOID_CONTRACT,
    }
    owner = None
    try:
        if profile == COMPONENT_PROFILE:
            result["profile"] = profile
            result["request"] = copy.deepcopy(request)
        layer, net = request["layer"], request["net"]
        if type(layer) is not int or not 1 <= layer <= 128 or not isinstance(net, str) or not net:
            raise ValueError("One actual target net and conductor layer are required")
        materials = state["all_pin_pad_material"]
        minimum = 0 if profile == COMPONENT_PROFILE else 2
        if not isinstance(materials, list) or not minimum <= len(materials) <= MAX_PINS:
            raise ValueError("Complete bounded native pin material inventory is required")
        keys = [(row["component_uid"], row["pin_uid"]) for row in materials]
        if len(set(keys)) != len(keys) or any(not all(k) for k in keys):
            raise ValueError("Native material ownership identities are absent or duplicated")
        target_pins = [row for row in materials if row["net"] == net]
        target_net_uid = _profile_target_net_uid(state, request, materials, profile)
        raw_traces = _trace_inventory(doc, state, a)
        result["raw_trace_inventory"] = raw_traces
        target_traces = [row for row in raw_traces if row["net"] == net]
        if any(row["net_uid"] != target_net_uid or row["layer"] != layer for row in target_traces):
            raise ValueError("Actual native target trace ownership or layer differs")
        target_rows = [row for row in state["routing_objects"] if row["net"] == net]
        result.update(
            target_net_uid=target_net_uid,
            target_layer=layer,
            target_trace_uids=[row["uid"] for row in target_traces],
            trace_geometry_sha256=digest(target_rows),
            raw_trace_sha256=digest(raw_traces),
            material_sha256=digest(materials),
        )
        engine = a._propget(doc, 326)
        if engine is None:
            raise ValueError("Actual Document.MaskEngine was not returned")
        owner = _Masks(engine, a)
        _touch_controls(owner, result["mask_touch_controls"])
        pin_masks, total_contours = {}, 0
        for row in materials:
            if row["material"].get("layer") != layer:
                raise ValueError("Native pad material layer differs from the target layer")
            outer, material, count, pads = _material_masks(owner, row["material"])
            total_contours += count
            if total_contours > MAX_CONTOURS:
                raise ValueError("Whole observation contour budget exceeded")
            pin_masks[(row["component_uid"], row["pin_uid"])] = (outer, material, pads)
            result["pin_materials"].append(
                {
                    "component_uid": row["component_uid"],
                    "pin_uid": row["pin_uid"],
                    "material_sha256": digest(row["material"]),
                    "outer_area_mm2": owner.area(outer)["area_mm2"],
                    "material_area_mm2": owner.area(material)["area_mm2"],
                }
            )
        holes = _round_holes(materials)
        result["round_holes"] = holes
        result["round_holes_sha256"] = digest(holes)
        hole_masks = [
            owner.contour(
                {
                    "kind": "circle",
                    "center_mm": row["center_mm"],
                    "radius_mm": row["diameter_mm"] / 2,
                }
            )
            for row in holes
        ]
        layer_traces = [row for row in raw_traces if row["layer"] == layer]
        foreign = _foreign_pairs(materials, raw_traces, target_net_uid, layer)
        comparisons = len(target_traces) * (len(foreign) + len(target_pins))
        comparisons += len(target_traces) * (len(target_traces) - 1) // 2
        comparisons += len(layer_traces) * len(holes)
        if comparisons > MAX_COMPARISONS:
            raise ValueError("Native full-material comparison budget exceeded")
        trace_masks = {}
        for trace in layer_traces:
            raw_mask = owner.path(trace)
            mask = raw_mask
            row = {
                "trace_uid": trace["uid"],
                "raw_trace_sha256": digest(trace),
                "round_holes_sha256": digest(holes),
                "raw_area_mm2": owner.area(raw_mask)["area_mm2"],
                "hole_intersections": [],
            }
            if row["raw_area_mm2"] <= 0:
                raise ValueError("Actual trace path has no positive raw native material area")
            result["trace_materials"].append(row)
            for index, hole_mask in enumerate(hole_masks):
                intersection = owner.area(owner.boolean(1, raw_mask, hole_mask))
                hole_row = {"hole_index": index, **intersection, "terminal_crop": None}
                row["hole_intersections"].append(hole_row)
                if intersection["area_mm2"] > 0 and trace["net_uid"] == target_net_uid:
                    hole_row["terminal_crop"] = _terminal_hole_cut(trace, holes[index])
                mask = owner.boolean(0, mask, hole_mask)
            row["terminal_crop_pairs"] = (
                _terminal_crop_pairs(trace, holes, row["hole_intersections"])
                if trace["net_uid"] == target_net_uid
                else None
            )
            area = owner.area(mask)["area_mm2"]
            row["material_area_mm2"] = area
            trace_masks[trace["uid"]] = mask
        for trace in target_traces:
            mask = trace_masks[trace["uid"]]
            for pin in target_pins:
                outer, material, _ = pin_masks[(pin["component_uid"], pin["pin_uid"])]
                intersection = owner.area(owner.boolean(1, mask, material))
                ends = []
                for index in (0, -1):
                    point = [trace["points_array_mm"][i][index] for i in (0, 1)]
                    inside = outer.ContainsPoint(*point, UNIT_MM)
                    if type(inside) is not bool:
                        raise ValueError("Native owned outer-mask containment is unreadable")
                    ends.append(
                        {
                            "end_index": 0 if index == 0 else 1,
                            "point_mm": point,
                            "inside_owned_outer": inside,
                        }
                    )
                result["target_contacts"].append(
                    {
                        "component_uid": pin["component_uid"],
                        "pin_uid": pin["pin_uid"],
                        "trace_uid": trace["uid"],
                        "positive_intersection_area_mm2": intersection["area_mm2"],
                        "intersection_is_empty": intersection["is_empty"],
                        "material_area_mm2": owner.area(material)["area_mm2"],
                        "trace_ends": ends,
                        "raw_trace_sha256": digest(trace),
                    }
                )
        # Whole full-width paths, rather than endpoint coincidence, establish
        # material edges. This includes a branch ending in another trace's
        # middle, without manufacturing a centerline tolerance or persistent ID.
        for index, left in enumerate(target_traces):
            for right in target_traces[index + 1 :]:
                intersection = owner.area(
                    owner.boolean(1, trace_masks[left["uid"]], trace_masks[right["uid"]])
                )
                result["target_trace_intersections"].append(
                    {
                        "left_trace_uid": left["uid"],
                        "right_trace_uid": right["uid"],
                        "left_raw_trace_sha256": digest(left),
                        "right_raw_trace_sha256": digest(right),
                        "intersection_area_mm2": intersection["area_mm2"],
                        "intersection_is_empty": intersection["is_empty"],
                    }
                )
        # Connectivity of a pin's material is a separate observation. An
        # unsupported topology does not erase the complete raw masks above,
        # but neither observer nor final graph may use it as a bridge.
        pin_regions = []
        for pin in target_pins:
            witness, masks = _pad_region_witness(owner, pin, target_traces, trace_masks)
            result["target_pad_regions"].append(witness)
            pin_regions.append((pin, masks))
        for index, (left, left_masks) in enumerate(pin_regions):
            for right, right_masks in pin_regions[index + 1 :]:
                for left_index, left_mask in enumerate(left_masks):
                    for right_index, right_mask in enumerate(right_masks):
                        if len(result["target_pad_intersections"]) >= MAX_COMPARISONS:
                            raise ValueError("Owned-region pair comparison budget exceeded")
                        result["target_pad_intersections"].append(
                            {
                                "left_component_uid": left["component_uid"],
                                "left_pin_uid": left["pin_uid"],
                                "left_region_index": left_index,
                                "right_component_uid": right["component_uid"],
                                "right_pin_uid": right["pin_uid"],
                                "right_region_index": right_index,
                                **_region_area(owner, left_mask, right_mask),
                            }
                        )
        for trace in target_traces:
            bounds = _trace_bounds(trace)
            for other in foreign:
                # Actual pad Extrema includes its real arcs without interpreting
                # XYR. Strictly separated full-width trace bounds are a sufficient
                # condition. AND area0 alone cannot exclude boundary contact.
                separated = _separated(bounds, other["foreign_bounds_mm"])
                if other["foreign_kind"] == "pad":
                    other_mask = pin_masks[
                        (other["foreign_component_uid"], other["foreign_pin_uid"])
                    ][2][other["foreign_pad_index"]]
                else:
                    other_mask = trace_masks[other["foreign_trace_uid"]]
                intersection = owner.area(owner.boolean(1, trace_masks[trace["uid"]], other_mask))
                row = {
                    "trace_uid": trace["uid"],
                    **other,
                    "bounds_mm": bounds,
                    "strict_bounds_separated": separated,
                    "intersection_area_mm2": intersection["area_mm2"],
                    "intersection_is_empty": intersection["is_empty"],
                }
                if not separated:
                    # Formal151 IMask.Touch (memid10/help60024) asks whether
                    # two masks touch. AND area0 alone leaves tangency unknown.
                    # The actual full material is still independently intersected;
                    # neither a false/non-BOOL reply nor an exception is ignored.
                    touching = trace_masks[trace["uid"]].Touch(other_mask)
                    if type(touching) is not bool:
                        raise ValueError("Formal native Mask.Touch did not return BOOL")
                    row.update(boundary_touch=touching, boundary_touch_source="IMask.Touch151")
                result["foreign_intersections"].append(row)
        result["budget"] = {
            "pins": len(materials),
            "traces": len(raw_traces),
            "contours": total_contours,
            "mask_points": owner.points["points"],
            "temporary_masks": len(owner.owned),
            "comparisons": comparisons,
            "region_comparisons": owner.region_comparisons,
            "mask_control_comparisons": len(result["mask_touch_controls"]),
            "maximum_points": MAX_POINTS,
            "maximum_comparisons": MAX_COMPARISONS,
        }
        result["complete"] = True
        result["foreign_separation_verified"] = all(
            (row["strict_bounds_separated"] or row.get("boundary_touch") is False)
            and row["intersection_is_empty"] is True
            and row["intersection_area_mm2"] == 0
            for row in result["foreign_intersections"]
        )
    except Exception as error:
        result["errors"].append({"cause": type(error).__name__, "reason": str(error)})
    finally:
        if owner is not None:
            owner.delete_owned()
            result["cleanup"] = owner.cleanup
            result["temporary_masks_count"] = {
                "before": owner.initial_count,
                "after": owner.final_count,
            }
            if (
                not all(row["delete_returned"] for row in owner.cleanup)
                or type(owner.final_count) is not int
                or owner.final_count != owner.initial_count
            ):
                result["complete"] = False
                result["errors"].append(
                    {"cause": "TemporaryMaskReleaseIncomplete", "reason": "Own Mask.Delete failed"}
                )
    return result


def _record_key(row):
    return _pair_key(
        row["trace_uid"],
        row["foreign_kind"],
        component_uid=row.get("foreign_component_uid"),
        pin_uid=row.get("foreign_pin_uid"),
        pad_index=row.get("foreign_pad_index"),
        foreign_trace_uid=row.get("foreign_trace_uid"),
    )


def _validate_material_records(materials, layer):
    """Check record structure/budgets without interpreting the opaque arc row."""
    budget = {"points": 0, "maximum_points": MAX_POINTS}
    total_contours = 0

    def contour(record):
        nonlocal total_contours
        total_contours += 1
        if total_contours > MAX_CONTOURS:
            raise ValueError("Whole raw contour record budget exceeded")
        if record.get("kind") == "circle":
            center = record["center_mm"]
            if not isinstance(center, list) or len(center) != 2:
                raise ValueError("Native circle record center is malformed")
            for value in center:
                _number(value)
            _number(record["radius_mm"], positive=True)
            budget["points"] += 1
        elif (
            record.get("kind") == "native_points_array"
            and record.get("closed") is True
            and type(record.get("is_arced")) is bool
        ):
            _array(record["points_array_mm"], budget)
        else:
            raise ValueError("Unsupported raw native contour record")
        if budget["points"] > MAX_POINTS:
            raise ValueError("Whole raw point record budget exceeded")

    for pin in materials:
        value = pin["material"]
        if (
            value.get("source") != "native_pin_owned_pad_material_mask151"
            or value.get("complete") is not True
            or value.get("unread") != []
            or value.get("unit") != "mm"
            or value.get("layer") != layer
            or not isinstance(value.get("pads"), list)
            or not 1 <= len(value["pads"]) <= 32
            or not isinstance(value.get("holes"), list)
            or len(value["holes"]) > 32
        ):
            raise ValueError("Incomplete raw owned pad observation")
        before = total_contours
        for pad in value["pads"]:
            _bounds(pad["bounds_mm"])
            if pad.get("layer") != layer:
                raise ValueError("Raw pad layer differs from actual target layer")
            geometries = pad["geometry"]
            if not isinstance(geometries, list) or not 1 <= len(geometries) <= 32:
                raise ValueError("Raw pad geometry records are incomplete")
            for shape in geometries:
                cuts = shape["cutouts"]
                if shape.get("filled") is not True or not isinstance(cuts, list) or len(cuts) > 32:
                    raise ValueError("Raw pad fill or cutout records are incomplete")
                contour(shape["outer"])
                for cut in cuts:
                    contour(cut)
        if total_contours - before > 128:
            raise ValueError("Per-pin raw contour record budget exceeded")
        for hole in value["holes"]:
            center = hole["center_mm"]
            if not isinstance(center, list) or len(center) != 2 or type(hole["plated"]) is not bool:
                raise ValueError("Actual round owned hole record is incomplete")
            for coordinate in center:
                _number(coordinate)
            _number(hole["diameter_mm"], positive=True)
    return total_contours


def verify_evidence(state, request, *, profile=LEGACY_PROFILE):
    """Validate fresh mask evidence shape, complete matrices and bound inputs.

    This is a pure receipt consistency check. The SDK's fresh native observation
    must independently run :func:`evaluate`; this function never turns a supplied
    producer boolean into native measurement. Actual chain-end ownership and
    positive contact are additionally checked by the routing graph verifier.
    """
    checks = []

    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        if not value:
            raise ValueError(name)

    try:
        proof = state["native_mask_proof"]
        check(
            "native material observation profile matches acceptance policy",
            profile in (LEGACY_PROFILE, COMPONENT_PROFILE)
            and proof.get("profile", LEGACY_PROFILE) == profile
            and (profile != COMPONENT_PROFILE or proof.get("request") == request),
        )
        check(
            "complete fresh native mask observation",
            proof["complete"] is True
            and proof["source"] == "native_mask_engine151"
            and proof["unit"] == "mm"
            and proof["errors"] == [],
        )
        controls = proof["mask_touch_controls"]
        expected_controls = _touch_control_inputs()
        check(
            "formal151 Mask.Touch source and complete boundary controls",
            proof["mask_touch_formal_source"] == TOUCH_FORMAL_SOURCE
            and isinstance(controls, list)
            and len(controls) == len(expected_controls),
        )
        for actual, expected_control in zip(controls, expected_controls, strict=True):
            check(
                "actual Mask.Touch boundary control inputs and result",
                all(actual[key] == value for key, value in expected_control.items())
                and type(actual["actual_touch"]) is bool
                and actual["actual_touch"] is expected_control["expected_touch"]
                and _number(actual["area_mm2"]) == expected_control["expected_area_mm2"]
                and type(actual["is_empty"]) is bool
                and actual["is_empty"] is (expected_control["expected_area_mm2"] == 0)
                and type(actual["shape_count"]) is int
                and 0 <= actual["shape_count"] <= MAX_CONTOURS
                and (actual["area_mm2"] == 0 or actual["shape_count"] > 0),
            )
            if expected_control["left"].get("kind") == "native_rectangle_union":
                construction = actual["native_construction"]
                check(
                    "formal factory and OR2 source bound for nonconvex control",
                    construction["source"] == TOUCH_FORMAL_SOURCE["nonconvex_control_construction"],
                )
                bounds_rows = expected_control["left"]["rectangles_mm"] + [
                    expected_control["right"]["bounds_mm"]
                ]
                rectangles = construction["rectangles"]
                check("all three native factory rectangles retained", len(rectangles) == 3)
                factory_budget = {"points": 0, "maximum_points": MAX_POINTS}
                for rectangle, bounds in zip(rectangles, bounds_rows, strict=True):
                    raw = _array(rectangle["points_array_mm"], factory_budget)
                    check(
                        "native rectangle inputs, raw result and area bound",
                        rectangle["bounds_mm"] == bounds
                        and rectangle["raw_sha256"] == digest(raw)
                        and _number(rectangle["area_mm2"])
                        == (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
                        and rectangle["is_empty"] is False
                        and type(rectangle["shape_count"]) is int
                        and 0 < rectangle["shape_count"] <= MAX_CONTOURS,
                    )
                union = construction["left_union_area"]
                check(
                    "actual native L union has expected five square millimeters",
                    _number(union["area_mm2"]) == 5
                    and union["is_empty"] is False
                    and type(union["shape_count"]) is int
                    and 0 < union["shape_count"] <= MAX_CONTOURS,
                )
                observed_points = construction["left_contains_points"]
                expected_points = expected_control["left_contains_points"]
                check(
                    "all native L containment observations retained",
                    len(observed_points) == len(expected_points),
                )
                for observed, expected_point in zip(observed_points, expected_points, strict=True):
                    check(
                        "native L arms, shared region and missing quadrant distinguished",
                        all(observed[key] == value for key, value in expected_point.items())
                        and type(observed["actual"]) is bool
                        and observed["actual"] is expected_point["expected"],
                    )
        materials, traces = state["all_pin_pad_material"], proof["raw_trace_inventory"]
        check(
            "bounded complete raw material inputs",
            isinstance(materials, list)
            and (0 if profile == COMPONENT_PROFILE else 2) <= len(materials) <= MAX_PINS
            and isinstance(traces, list)
            and len(traces) <= MAX_TRACES,
        )
        total_contours = _validate_material_records(materials, request["layer"])
        check(
            "raw trace observation independently retained", state["native_trace_material"] == traces
        )
        check(
            "actual full material and raw trace digests",
            proof["material_sha256"] == digest(materials)
            and proof["raw_trace_sha256"] == digest(traces),
        )
        pins = {(row["component_uid"], row["pin_uid"]): row for row in materials}
        check(
            "unique observed persistent pin owners",
            len(pins) == len(materials) and all(all(key) for key in pins),
        )
        for pin in materials:
            material = pin["material"]
            check(
                "complete raw owned filled material",
                material.get("complete") is True
                and material.get("source") == "native_pin_owned_pad_material_mask151"
                and material.get("unit") == "mm"
                and material.get("layer") == request["layer"]
                and material.get("unread") == [],
            )
            for pad in material["pads"]:
                _bounds(pad["bounds_mm"])
        observed_materials = {
            (row["component_uid"], row["pin_uid"]): row for row in proof["pin_materials"]
        }
        check(
            "all actual pin masks evaluated exactly once",
            len(observed_materials) == len(proof["pin_materials"]) == len(pins)
            and set(observed_materials) == set(pins),
        )
        for key, row in observed_materials.items():
            check(
                "positive actual owned material areas and input digest",
                _number(row["outer_area_mm2"], positive=True) > 0
                and _number(row["material_area_mm2"], positive=True) > 0
                and row["material_sha256"] == digest(pins[key]["material"]),
            )
        target_pins = [row for row in materials if row["net"] == request["net"]]
        net_uid = proof["target_net_uid"]
        expected_net_uid = _profile_target_net_uid(state, request, materials, profile)
        check(
            "bounded target pins with actual net ownership"
            if profile == COMPONENT_PROFILE
            else "two, three or six distinct target pins with actual net ownership",
            net_uid == expected_net_uid
            and net_uid
            and net_uid != "0"
            and all(row["net_uid"] == net_uid for row in target_pins)
            and proof["target_layer"] == request["layer"],
        )
        route_rows = {(row["kind"], row["uid"]): row for row in state["routing_objects"]}
        check(
            "complete unique trace identities",
            len(route_rows) == len(state["routing_objects"]) == len(traces)
            and len({row["uid"] for row in traces}) == len(traces),
        )
        point_budget = {"points": 0, "maximum_points": MAX_POINTS}
        net_rows = state["geometry"]["model"]["connectivity"]["nets"]
        net_ids = named_net_ownership(net_rows, materials)
        check(
            "complete unique net ownership mapping",
            len(net_ids) == len(net_rows),
        )
        for trace in traces:
            raw = _array(trace["points_array_mm"], point_budget, minimum=2)
            width = _number(trace["width_mm"], positive=True)
            bounds = _trace_bounds(trace)
            check(
                "actual trace extrema present and contain observed centerline",
                trace["bounds_source"] == "Trace.Extrema.MinX/MinY/MaxX/MaxY(EPcbUnitMM4)"
                and all(
                    bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]
                    for x, y in zip(raw[0], raw[1], strict=True)
                ),
            )
            check("raw trace path has no unparsed arc", all(value == 0 for value in raw[2]))
            model = {
                "uid": trace["uid"],
                "kind": "trace",
                "net": trace["net"],
                "layer": trace["layer"],
                "width": width,
                "path": [list(p) for p in zip(*raw, strict=True)],
            }
            bound = route_rows[("trace", trace["uid"])]
            check(
                "actual raw trace is bound to independently captured geometry",
                bound["geometry"] == _canonical(model)
                and bound["net"] == trace["net"]
                and trace["net_uid"] == net_ids.get(trace["net"]),
            )
        target = [row for row in traces if row["net"] == request["net"]]
        target_rows = [row for row in state["routing_objects"] if row["net"] == request["net"]]
        check(
            "complete target trace identity and geometry binding",
            proof["target_trace_uids"] == [row["uid"] for row in target]
            and proof["trace_geometry_sha256"] == digest(target_rows)
            and all(
                row["layer"] == request["layer"] and row["net_uid"] == net_uid for row in target
            ),
        )
        holes = _round_holes(materials)
        check(
            "complete physical round-hole inventory and limited trace crop contract",
            proof["round_holes"] == holes
            and proof["round_holes_sha256"] == digest(holes)
            and proof["trace_void_contract"] == TRACE_VOID_CONTRACT,
        )
        layer_traces = {row["uid"]: row for row in traces if row["layer"] == request["layer"]}
        clipped = {row["trace_uid"]: row for row in proof["trace_materials"]}
        check(
            "every target-layer trace has full physical-void material evidence",
            len(clipped) == len(proof["trace_materials"]) == len(layer_traces)
            and set(clipped) == set(layer_traces),
        )
        for uid, row in clipped.items():
            trace = layer_traces[uid]
            raw_area = _number(row["raw_area_mm2"], positive=True)
            material_area = _number(row["material_area_mm2"])
            check(
                "trace material bound to actual raw path and all physical holes",
                row["raw_trace_sha256"] == digest(trace)
                and row["round_holes_sha256"] == digest(holes)
                and 0 <= material_area <= raw_area,
            )
            intersections = row["hole_intersections"]
            check(
                "complete ordered trace by physical-hole matrix",
                isinstance(intersections, list)
                and len(intersections) == len(holes)
                and [entry["hole_index"] for entry in intersections] == list(range(len(holes))),
            )
            for index, entry in enumerate(intersections):
                area = _number(entry["area_mm2"])
                check(
                    "physical-hole intersection area and emptiness consistent",
                    area >= 0
                    and type(entry["is_empty"]) is bool
                    and entry["is_empty"] == (area == 0)
                    and type(entry["shape_count"]) is int
                    and 0 <= entry["shape_count"] <= MAX_CONTOURS
                    and (area == 0 or entry["shape_count"] > 0),
                )
                crop = (
                    _terminal_hole_cut(trace, holes[index])
                    if area > 0 and trace["net_uid"] == net_uid
                    else None
                )
                check(
                    "positive target hole contact is only a proven terminal crop",
                    entry["terminal_crop"] == crop,
                )
            expected_pairs = (
                _terminal_crop_pairs(trace, holes, intersections)
                if trace["net_uid"] == net_uid
                else None
            )
            check(
                "target terminal void neighborhoods cannot split their remaining trace",
                row["terminal_crop_pairs"] == expected_pairs,
            )
        contacts = proof["target_contacts"]
        contact_map = {
            (row["component_uid"], row["pin_uid"], row["trace_uid"]): row for row in contacts
        }
        expected = {
            (pin["component_uid"], pin["pin_uid"], trace["uid"]): (pin, trace)
            for pin in target_pins
            for trace in target
        }
        check(
            "complete unique actual target pin by trace matrix",
            len(contact_map) == len(contacts) == len(expected)
            and set(contact_map) == set(expected),
        )
        for key, row in contact_map.items():
            pin, trace = expected[key]
            area = _number(row["positive_intersection_area_mm2"])
            check(
                "actual contact area and emptiness consistent",
                area >= 0
                and type(row["intersection_is_empty"]) is bool
                and row["intersection_is_empty"] == (area == 0)
                and row["raw_trace_sha256"] == digest(trace)
                and row["material_area_mm2"]
                == observed_materials[(pin["component_uid"], pin["pin_uid"])]["material_area_mm2"],
            )
            ends = row["trace_ends"]
            check(
                "actual terminal path points and owned-outer observations",
                isinstance(ends, list)
                and len(ends) == 2
                and [end["end_index"] for end in ends] == [0, 1]
                and all(
                    type(end["inside_owned_outer"]) is bool
                    and end["point_mm"] == [trace["points_array_mm"][j][i] for j in (0, 1)]
                    for end, i in zip(ends, (0, -1), strict=True)
                ),
            )
        expected_trace_pairs = {
            (left["uid"], right["uid"]): (left, right)
            for index, left in enumerate(target)
            for right in target[index + 1 :]
        }
        trace_contacts = proof.get("target_trace_intersections", [])
        trace_contact_map = {
            (row["left_trace_uid"], row["right_trace_uid"]): row for row in trace_contacts
        }
        check(
            "complete unique actual target trace by trace matrix",
            len(trace_contact_map) == len(trace_contacts) == len(expected_trace_pairs)
            and set(trace_contact_map) == set(expected_trace_pairs),
        )
        for key, row in trace_contact_map.items():
            left, right = expected_trace_pairs[key]
            area = _number(row["intersection_area_mm2"])
            check(
                "target trace material area and bound inputs consistent",
                area >= 0
                and type(row["intersection_is_empty"]) is bool
                and row["intersection_is_empty"] == (area == 0)
                and row["left_raw_trace_sha256"] == digest(left)
                and row["right_raw_trace_sha256"] == digest(right),
            )
        foreign = _foreign_pairs(materials, traces, net_uid, request["layer"])
        expected_foreign = {}
        for trace in target:
            for other in foreign:
                pair = {"trace_uid": trace["uid"], **other}
                expected_foreign[_record_key(pair)] = (trace, other)
        foreign_map = {_record_key(row): row for row in proof["foreign_intersections"]}
        check(
            "complete foreign pad and old-trace comparison matrix",
            len(foreign_map) == len(proof["foreign_intersections"]) == len(expected_foreign)
            and set(foreign_map) == set(expected_foreign),
        )
        for key, row in foreign_map.items():
            trace, other = expected_foreign[key]
            bounds, foreign_bounds = _trace_bounds(trace), other["foreign_bounds_mm"]
            check(
                "actual full-width material separation excludes boundary contact",
                row["bounds_mm"] == bounds
                and row["foreign_bounds_mm"] == foreign_bounds
                and type(row["strict_bounds_separated"]) is bool
                and row["strict_bounds_separated"] == _separated(bounds, foreign_bounds)
                and (
                    row["strict_bounds_separated"] is True
                    or (
                        row.get("boundary_touch") is False
                        and row.get("boundary_touch_source") == "IMask.Touch151"
                    )
                )
                and type(row["intersection_is_empty"]) is bool
                and row["intersection_is_empty"] is True
                and _number(row["intersection_area_mm2"]) == 0,
            )
        budget = proof["budget"]
        comparisons = len(target) * (len(foreign) + len(target_pins))
        comparisons += len(expected_trace_pairs)
        comparisons += len(layer_traces) * len(holes)
        allocated = budget["temporary_masks"]
        check(
            "bounded actual observation counts",
            budget["pins"] == len(materials)
            and budget["traces"] == len(traces)
            and budget["contours"] == total_contours
            and budget["comparisons"] == comparisons
            and budget["mask_control_comparisons"] == len(expected_controls)
            and comparisons <= MAX_COMPARISONS
            and type(allocated) is int
            and 0 < allocated <= MAX_MASKS
            and type(budget["mask_points"]) is int
            and 0 < budget["mask_points"] <= MAX_POINTS,
        )
        cleanup = proof["cleanup"]
        check(
            "only all invocation-owned temporary masks released once",
            isinstance(cleanup, list)
            and len(cleanup) == allocated
            and [row["owned_allocation_index"] for row in cleanup]
            == list(range(allocated - 1, -1, -1))
            and all(row["delete_returned"] is True and "error" not in row for row in cleanup),
        )
        counts = proof["temporary_masks_count"]
        check(
            "shared Mask collection count restored",
            type(counts["before"]) is int
            and type(counts["after"]) is int
            and 0 <= counts["before"] == counts["after"],
        )
        return {"valid": True, "checks": checks}
    except (ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        return {"valid": False, "checks": checks, "reason": str(error)}


def _groups(adjacency):
    unseen, rows = set(adjacency), []
    while unseen:
        stack, group = [min(unseen)], set()
        while stack:
            node = stack.pop()
            if node in group:
                continue
            group.add(node)
            stack.extend(adjacency[node] - group)
        unseen -= group
        rows.append(sorted(group))
    return sorted(rows)


def _material_region_graph(state, request, *, profile=LEGACY_PROFILE):
    """Use proved conductive regions, not pin identity, as electrical nodes.

    Each region is a Jordan disk minus strictly internal, separated voids, or
    an independently native-equal connected rounded rectangle. Raw arcs and
    cutouts crossing the boundary remain incomplete rather than guessed.
    """
    evidence = verify_evidence(state, request, profile=profile)
    result = {
        "valid": False,
        "observation_valid": False,
        "complete": False,
        "completion_valid": False,
        "all_members_connected": False,
        "source": "fresh_native_full_material_components151",
        "checks": list(evidence["checks"]),
        "nodes": [],
        "edges": [],
        "components": [],
        "trace_components": [],
        "component_membership_sha256": None,
        "evidence": evidence,
        "not_checked": [
            "arbitrary arced pad topology and boundary-crossing or touching pad voids",
            "spacing rules",
            "DRC",
            "manufacturing",
            "thermal/tie",
        ],
    }
    if profile == COMPONENT_PROFILE:
        result["profile"] = profile
        result["node_identity_scope"] = (
            "observation snapshot; region indices are not persistent IDs"
        )

    def check(name, value):
        result["checks"].append({"name": name, "passed": bool(value)})
        if not value:
            raise ValueError(name)

    def area_row(row):
        area = _number(row["area_mm2"])
        check(
            "region intersection area and emptiness consistent",
            area >= 0
            and type(row["is_empty"]) is bool
            and row["is_empty"] == (area == 0)
            and type(row["shape_count"]) is int
            and 0 <= row["shape_count"] <= MAX_CONTOURS
            and (area == 0 or row["shape_count"] > 0),
        )
        return area

    try:
        check("fresh complete native material matrices independently bound", evidence["valid"])
        proof = state["native_mask_proof"]
        pins = [row for row in state["all_pin_pad_material"] if row["net"] == request["net"]]
        traces = [row for row in proof["raw_trace_inventory"] if row["net"] == request["net"]]
        check(
            "all target pin names and persistent identities present and unique",
            (
                len(pins) <= MAX_COMPONENT_PINS
                if profile == COMPONENT_PROFILE
                else len(pins) in TARGET_PIN_COUNTS
            )
            and len({row["pin_uid"] for row in pins}) == len(pins)
            and len({(row["refdes"], row["pin"]) for row in pins}) == len(pins)
            and all(
                isinstance(row[key], str) and row[key] and row[key] != "0"
                for row in pins
                for key in ("component_uid", "pin_uid", "refdes", "pin", "net_uid")
            ),
        )
        observed = {row["trace_uid"]: row for row in proof["trace_materials"]}
        check(
            "every target trace has positive material outside all physical holes",
            all(observed[row["uid"]]["material_area_mm2"] > 0 for row in traces),
        )
        result["pin_pair_separation"] = []
        for index, left in enumerate(pins):
            for right in pins[index + 1 :]:
                separated = all(
                    _separated(_bounds(a["bounds_mm"]), _bounds(b["bounds_mm"]))
                    for a in left["material"]["pads"]
                    for b in right["material"]["pads"]
                )
                result["pin_pair_separation"].append(
                    {
                        "left_pin_uid": left["pin_uid"],
                        "right_pin_uid": right["pin_uid"],
                        "strict_bounds_separated": separated,
                    }
                )
        witnesses = proof["target_pad_regions"]
        bound = {(row["component_uid"], row["pin_uid"]): row for row in witnesses}
        check(
            "every actual target pin has one region witness",
            len(bound) == len(witnesses) == len(pins)
            and set(bound) == {(row["component_uid"], row["pin_uid"]) for row in pins},
        )
        trace_nodes = {row["uid"]: "trace:" + row["uid"] for row in traces}
        for row in traces:
            result["nodes"].append(
                {
                    "node_id": trace_nodes[row["uid"]],
                    "kind": "trace",
                    "trace_uid": row["uid"],
                    "net_uid": row["net_uid"],
                    "layer": row["layer"],
                }
            )
        pin_nodes, region_maps = {}, {}
        for pin in pins:
            key = (pin["component_uid"], pin["pin_uid"])
            witness = bound[key]
            check(
                "complete connected owned-region observation for " + pin["pin_uid"],
                witness["complete"] is True
                and witness["material_sha256"] == digest(pin["material"]),
            )
            shapes = [
                (i, j, shape)
                for i, pad in enumerate(pin["material"]["pads"])
                for j, shape in enumerate(pad["geometry"])
            ]
            regions = witness["regions"]
            check(
                "all original owned pad shapes retained as separate material nodes",
                len(regions) == len(shapes) and len(regions) > 0,
            )
            holes = [
                {
                    "kind": "circle",
                    "center_mm": hole["center_mm"],
                    "radius_mm": hole["diameter_mm"] / 2,
                }
                for hole in pin["material"]["holes"]
            ]
            region_nodes = {}
            for index, (row, (pad_index, shape_index, shape)) in enumerate(
                zip(regions, shapes, strict=True)
            ):
                plan = _connected_shape_plan(shape["outer"], shape["cutouts"] + holes)
                check(
                    "owned region identity, native source and connected topology bound",
                    row["region_index"] == index
                    and row["pad_index"] == pad_index
                    and row["shape_index"] == shape_index
                    and row["source_sha256"] == digest(shape)
                    and row["topology"] == plan
                    and _number(row["material_area_mm2"], positive=True) > 0,
                )
                if plan["kind"] == "native_equivalent_rounded_rectangle":
                    check(
                        "actual native rounded rectangle equality in both directions",
                        area_row(row["native_equality"]["actual_minus_candidate"]) == 0
                        and area_row(row["native_equality"]["candidate_minus_actual"]) == 0,
                    )
                else:
                    check(
                        "no unbound native topology equality claim", row["native_equality"] is None
                    )
                node = "pin:" + digest([*key, index])
                region_nodes[index] = node
                result["nodes"].append(
                    {
                        "node_id": node,
                        "kind": "pin",
                        "component_uid": pin["component_uid"],
                        "pin_uid": pin["pin_uid"],
                        "refdes": pin["refdes"],
                        "pin": pin["pin"],
                        "region_index": index,
                        "net_uid": pin["net_uid"],
                        "layer": request["layer"],
                    }
                )
            pin_nodes[key], region_maps[key] = set(region_nodes.values()), region_nodes
        adjacency = {row["node_id"]: set() for row in result["nodes"]}
        trace_adjacency = {node: set() for node in trace_nodes.values()}

        def edge(source, target, area, kind):
            if area > 0:
                adjacency[source].add(target)
                adjacency[target].add(source)
                result["edges"].append(
                    {
                        "source": source,
                        "target": target,
                        "kind": kind,
                        "positive_intersection_area_mm2": area,
                    }
                )
                if kind == "trace_trace":
                    trace_adjacency[source].add(target)
                    trace_adjacency[target].add(source)

        for row in proof["target_trace_intersections"]:
            edge(
                trace_nodes[row["left_trace_uid"]],
                trace_nodes[row["right_trace_uid"]],
                row["intersection_area_mm2"],
                "trace_trace",
            )
        result["trace_components"] = _groups(trace_adjacency)
        for pin in pins:
            key = (pin["component_uid"], pin["pin_uid"])
            witness, nodes = bound[key], region_maps[key]
            intersections = witness["region_intersections"]
            expected = {(i, j) for i in nodes for j in nodes if i < j}
            check(
                "complete unique region by region contact matrix",
                len(intersections) == len(expected)
                and {(row["left_region_index"], row["right_region_index"]) for row in intersections}
                == expected,
            )
            for row in intersections:
                edge(
                    nodes[row["left_region_index"]],
                    nodes[row["right_region_index"]],
                    area_row(row),
                    "pad_region_pad_region",
                )
            contacts = witness["contacts"]
            expected = {(i, trace["uid"]) for i in nodes for trace in traces}
            check(
                "complete unique region by trace contact matrix",
                len(contacts) == len(expected)
                and {(row["region_index"], row["trace_uid"]) for row in contacts} == expected,
            )
            by_uid = {row["uid"]: row for row in traces}
            for row in contacts:
                check(
                    "region contact bound to actual full trace",
                    row["raw_trace_sha256"] == digest(by_uid[row["trace_uid"]]),
                )
                edge(
                    nodes[row["region_index"]],
                    trace_nodes[row["trace_uid"]],
                    area_row(row),
                    "pin_trace",
                )
        pairs = proof["target_pad_intersections"]
        expected = {
            (left["component_uid"], left["pin_uid"], i, right["component_uid"], right["pin_uid"], j)
            for index, left in enumerate(pins)
            for right in pins[index + 1 :]
            for i in region_maps[(left["component_uid"], left["pin_uid"])]
            for j in region_maps[(right["component_uid"], right["pin_uid"])]
        }

        def pair_key(row):
            return tuple(
                row[key]
                for key in (
                    "left_component_uid",
                    "left_pin_uid",
                    "left_region_index",
                    "right_component_uid",
                    "right_pin_uid",
                    "right_region_index",
                )
            )

        check(
            "complete unique actual owned-region cross-pin matrix",
            len(pairs) == len(expected) and {pair_key(row) for row in pairs} == expected,
        )
        count = len(pairs) + sum(
            len(row["contacts"]) + len(row["region_intersections"]) for row in witnesses
        )
        check(
            "bounded complete owned-region material comparison count",
            type(proof["budget"]["region_comparisons"]) is int
            and proof["budget"]["region_comparisons"] == count <= MAX_COMPARISONS,
        )
        for row in pairs:
            left = region_maps[(row["left_component_uid"], row["left_pin_uid"])][
                row["left_region_index"]
            ]
            right = region_maps[(row["right_component_uid"], row["right_pin_uid"])][
                row["right_region_index"]
            ]
            edge(left, right, area_row(row), "pad_region_pad_region")
        node_rows = {row["node_id"]: row for row in result["nodes"]}
        for component in _groups(adjacency):
            members = [node_rows[node] for node in component]
            pin_members = {
                (row["component_uid"], row["pin_uid"]): {
                    key: value
                    for key, value in row.items()
                    if key not in {"node_id", "kind", "region_index"}
                }
                for row in members
                if row["kind"] == "pin"
            }
            result["components"].append(
                {
                    "node_ids": component,
                    "trace_ids": sorted(
                        row["trace_uid"] for row in members if row["kind"] == "trace"
                    ),
                    "pin_ids": sorted({row["pin_uid"] for row in members if row["kind"] == "pin"}),
                    "pin_members": sorted(
                        pin_members.values(), key=lambda row: (row["component_uid"], row["pin_uid"])
                    ),
                }
            )
        result["components"].sort(key=lambda row: (row["trace_ids"], row["pin_ids"]))
        result["component_membership_sha256"] = digest(result["components"])
        # Completion includes every owned material region and all copper, so an
        # uncontacted island is retained and cannot disappear behind a pin UID.
        connected = (
            (bool(result["nodes"]) if profile == COMPONENT_PROFILE else bool(traces))
            and len(result["components"]) == 1
            and (
                profile == COMPONENT_PROFILE
                or all(any(adjacency[node] for node in nodes) for nodes in pin_nodes.values())
            )
        )
        result["complete"] = result["completion_valid"] = result["all_members_connected"] = (
            connected
        )
        result["observation_valid"] = result["valid"] = True
        if profile == COMPONENT_PROFILE:
            result["complete"] = True
    except (ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        result["reason"] = str(error)
    return result


def verify_material_graph(state, request, *, profile=LEGACY_PROFILE):
    """Final acceptance and component observation use the same material graph."""
    observed = _material_region_graph(state, request, profile=profile)
    result = copy.deepcopy(observed)
    result["source"] = "fresh_native_full_material_graph151"
    result["valid"] = bool(observed["observation_valid"] and observed["completion_valid"])
    result["components"] = [row["node_ids"] for row in observed["components"]]
    if observed["observation_valid"] and not observed["completion_valid"]:
        result["reason"] = (
            "Every target trace and every conductive pad region must form one "
            "positive-material component"
        )
    return result


def observe_material_components(state, request, *, profile=LEGACY_PROFILE):
    """Return complete physical groups even when final completion is false."""
    result = _material_region_graph(state, request, profile=profile)
    completion = copy.deepcopy(result)
    completion["source"] = "fresh_native_full_material_graph151"
    completion["valid"] = bool(result["observation_valid"] and result["completion_valid"])
    completion["components"] = [row["node_ids"] for row in result["components"]]
    result["completion"] = completion
    return result


def evaluate_components(doc, state, request, a):
    """Observe one actual net/layer under the independent bounded component policy.

    ``request.net_uid`` must come from the adapter's observed actual net. No pin
    count, name, old-copper count or whole-net completion is assumed. Region
    indices and graph node IDs identify this snapshot only, never persistent pads.
    """
    return evaluate(doc, state, request, a, profile=COMPONENT_PROFILE)


def verify_component_evidence(state, request):
    return verify_evidence(state, request, profile=COMPONENT_PROFILE)


def observe_components(state, request):
    """Return complete component observations independently of full connectivity."""
    return observe_material_components(state, request, profile=COMPONENT_PROFILE)


def verify_components_graph(state, request):
    """Require all material to be connected under the independent component policy."""
    return verify_material_graph(state, request, profile=COMPONENT_PROFILE)
