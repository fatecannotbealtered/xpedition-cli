"""Bounded independent contact witnesses for observed owned pad material.

No rules/DRC/thermal inference and no public eligibility override. Native readers
must supply filled material, complete contours/cutouts and actual owned holes.
"""

from __future__ import annotations

import math


def _native_number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("native material coordinate or size is not a finite number")
    return float(value)


def _native_items(value, a, maximum):
    count = value.Count
    if type(count) is not int or not 0 <= count <= maximum:
        raise ValueError("native material collection count is unavailable or over budget")
    rows = list(a._items(value, strict=True))
    if len(rows) != count:
        raise ValueError("native material collection enumeration is incomplete")
    return rows


def _native_contour(geometry, a, budget):
    budget["contours"] += 1
    if budget["contours"] > 128:
        raise ValueError("native pad contour inventory exceeds bounded 128-contour budget")
    if geometry.IsValid() is not True:
        raise ValueError("native pad contour is invalid or validity is unreadable")
    is_circle = geometry.IsCircle()
    if type(is_circle) is not bool:
        raise ValueError("native contour circle classification is unreadable")
    if is_circle:
        budget["points"] += 1
        if budget["points"] > 4096:
            raise ValueError("native pad point inventory exceeds bounded 4096-point budget")
        return {"circle": [_native_number(a._propget(geometry, key, 4)) for key in (10, 11, 12)]}
    if geometry.IsClosed() is not True or geometry.IsArced() is not False:
        raise ValueError("native pad needs a closed contour without arcs")
    rows = a._propget(geometry, 1, 4)
    if (
        not isinstance(rows, (list, tuple))
        or len(rows) != 3
        or any(not isinstance(row, (list, tuple)) for row in rows)
        or not 3 <= len(rows[0]) <= 128
        or len({len(row) for row in rows}) != 1
    ):
        raise ValueError("native pad point rows are malformed or over budget")
    budget["points"] += len(rows[0])
    if budget["points"] > 4096:
        raise ValueError("native pad geometry exceeds bounded 4096-point budget")
    return {"path": [list(p) for p in zip(*rows, strict=True)], "closed": True}


def observe_pin(pin, layer, a):
    """Read supported 151.0 pad material; partial data never becomes eligibility.

    Pin.Pads20(layer, includeNegative=True), Pad.IsNegative2/IsNoConnect9,
    Pad.Geometries4; Geometry uses explicit EPcbUnitMM4 and supported fill,
    closure, validity and Cutouts22. No supported Pad/Hole persistent UID is
    available in the inspected151.0 interfaces. Actual Pin.UniqueId supplies
    the owning persistent identity.
    """
    result = {
        "source": "native_pin_owned_pad_material",
        "unit": "mm",
        "layer": layer,
        "pads": [],
        "holes": [],
        "unread": [],
        "complete": False,
    }
    budget = {"contours": 0, "points": 0}
    try:
        pads = _native_items(a._propget(pin, 20, layer, True), a, 32)
        if not pads:
            raise ValueError("no native effective layer pad was returned")
        for pad in pads:
            if (
                type(pad.Layer) is not int
                or pad.Layer != layer
                or pad.IsNegative() is not False
                or pad.IsNoConnect() is not False
            ):
                raise ValueError("negative, no-connect or incorrectly layered pad is unsupported")
            name = str(pad.Name)
            if not name:
                raise ValueError("native owned pad name is unavailable")
            shapes = []
            geometries = _native_items(a._propget(pad, 4), a, 32)
            if not geometries:
                raise ValueError("native owned pad geometries are empty")
            for geometry in geometries:
                filled = a._propget(geometry, 21)
                if filled is not True:
                    raise ValueError("native owned pad is not an observed filled area")
                outer = _native_contour(geometry, a, budget)
                native_cuts = _native_items(a._propget(geometry, 22), a, 32)
                cutouts = [_native_contour(cut, a, budget) for cut in native_cuts]
                # Do not flatten nested material topology into ordinary holes.
                for cut in native_cuts:
                    if _native_items(a._propget(cut, 22), a, 32):
                        raise ValueError("nested native pad cutouts are unsupported")
                shape = {
                    "material": {
                        "source": "native_pad_geometry",
                        "unit": "mm",
                        "filled": filled,
                        "complete": True,
                        "outer": outer,
                        "cutouts": cutouts,
                    }
                }
                _material(shape)  # Independent parser, including topology/budget checks.
                shapes.append(shape)
            result["pads"].append({"name": name, "layer": layer, "geometry": shapes})
        for hole in _native_items(a._propget(pin, 25), a, 32):
            if hole.IsValid() is not True or type(hole.ShapeType) is not int or hole.ShapeType != 1:
                raise ValueError("only observed valid round owned holes are supported")
            plated = hole.Plated
            if type(plated) is not bool:
                raise ValueError("native hole plating is unreadable")
            result["holes"].append(
                {
                    "center_mm": [_native_number(a._propget(hole, key, 4)) for key in (4, 5)],
                    "diameter_mm": _native_number(a._propget(hole, 1, 4)),
                    "plated": plated,
                }
            )
        result["complete"] = True
    except Exception as error:
        result["unread"].append(
            {
                "reason": str(error),
                "cause": type(error).__name__,
                "affected_requirement": "independent endpoint material contact",
            }
        )
    result["geometry_budget"] = {**budget, "maximum_contours": 128, "maximum_points": 4096}
    return result


def _finite_row(value, size):
    return (
        isinstance(value, (list, tuple))
        and len(value) == size
        and all(type(v) in (int, float) and math.isfinite(v) for v in value)
    )


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(p, a, b):
    return (
        _cross(a, b, p) == 0
        and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    )


def _segments_meet(a, b, c, d):
    sides = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    return (sides[0] * sides[1] < 0 and sides[2] * sides[3] < 0) or any(
        side == 0 and _on_segment(p, x, y)
        for side, p, x, y in (
            (sides[0], c, a, b),
            (sides[1], d, a, b),
            (sides[2], a, c, d),
            (sides[3], b, c, d),
        )
    )


def _contour(value):
    """Strict circles or simple closed straight polygons, with no inferred material."""
    if not isinstance(value, dict):
        raise ValueError("pad contour is missing")
    if "circle" in value:
        if not _finite_row(value["circle"], 3) or value["circle"][2] <= 0:
            raise ValueError("pad circle is invalid")
        return {"circle": list(value["circle"])}
    path = value.get("path")
    if (
        value.get("closed") is not True
        or not isinstance(path, list)
        or not 3 <= len(path) <= 128
        or any(not _finite_row(p, 3) or p[2] != 0 for p in path)
    ):
        raise ValueError("pad polygon needs observed closure and bounded straight edges")
    points = [p[:2] for p in path]
    if points[0] == points[-1]:
        points.pop()
    if len(points) < 3 or len(set(map(tuple, points))) != len(points):
        raise ValueError("pad polygon has repeated or missing vertices")
    edges = list(zip(points, points[1:] + points[:1], strict=True))
    if sum(a[0] * b[1] - a[1] * b[0] for a, b in edges) == 0:
        raise ValueError("pad polygon has no area")
    if any(
        _segments_meet(a, b, c, d)
        for i, (a, b) in enumerate(edges)
        for j, (c, d) in enumerate(edges)
        if i < j and j != i + 1 and (i, j) != (0, len(edges) - 1)
    ):
        raise ValueError("self-intersecting pad polygon is unsupported")
    return {"polygon": points}


def _inside(point, contour):
    """Strict interior: boundary-only contact never supplies a material witness."""
    if "circle" in contour:
        x, y, radius = contour["circle"]
        return (point[0] - x) ** 2 + (point[1] - y) ** 2 < radius**2
    points = contour["polygon"]
    result = False
    for a, b in zip(points, points[1:] + points[:1], strict=True):
        if _on_segment(point, a, b):
            return False
        if (a[1] > point[1]) != (b[1] > point[1]) and point[0] < (
            a[0] + (point[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
        ):
            result = not result
    return result


def _crossings(start, end, contour):
    dx, dy = end[0] - start[0], end[1] - start[1]
    if "circle" in contour:
        x, y, radius = contour["circle"]
        px, py = start[0] - x, start[1] - y
        aa, bb = dx * dx + dy * dy, 2 * (px * dx + py * dy)
        cc = px * px + py * py - radius * radius
        discriminant = bb * bb - 4 * aa * cc
        return (
            []
            if discriminant < 0
            else [(-bb + sign * math.sqrt(discriminant)) / (2 * aa) for sign in (-1, 1)]
        )
    result = []
    points = contour["polygon"]
    for a, b in zip(points, points[1:] + points[:1], strict=True):
        ex, ey = b[0] - a[0], b[1] - a[1]
        determinant = dx * ey - dy * ex
        if determinant:
            ax, ay = a[0] - start[0], a[1] - start[1]
            t, u = (ax * ey - ay * ex) / determinant, (ax * dy - ay * dx) / determinant
            if 0 <= u <= 1:
                result.append(t)
    return result


def _material(shape):
    material = shape.get("material")
    if (
        not isinstance(material, dict)
        or material.get("source") != "native_pad_geometry"
        or material.get("unit") != "mm"
        or material.get("complete") is not True
        or material.get("filled") is not True
        or not isinstance(material.get("cutouts"), list)
        or len(material["cutouts"]) > 32
    ):
        raise ValueError("filled pad material and complete native cutout observation are missing")
    outer = _contour(material.get("outer"))
    cuts = [_contour(c) for c in material["cutouts"]]
    return outer, cuts


def _contact_witness(endpoint, start, end):
    """Prove a positive centerline interval in real pad material, not in its bounds.

    A positive-width trace contains its centerline. An interval strictly inside
    filled pad material is therefore a sufficient contact witness. This supports
    straight segments through an observed annulus but does not infer cap/join,
    arbitrary arcs, thermal/tie material, clearance or manufacturability.
    """
    holes = endpoint.get("holes")
    if not isinstance(holes, list) or len(holes) > 32:
        raise ValueError("complete owned pad hole observation is missing")
    hole_contours = []
    for hole in holes:
        diameter, center = hole.get("diameter_mm"), hole.get("center_mm")
        if (
            not _finite_row(center, 2)
            or type(diameter) not in (int, float)
            or not math.isfinite(diameter)
            or diameter <= 0
            or type(hole.get("plated")) is not bool
        ):
            raise ValueError("owned pad hole shape is incomplete")
        hole_contours.append({"circle": [*center, diameter / 2]})
    pads = endpoint.get("pads")
    if not isinstance(pads, list) or not pads or len(pads) > 32:
        raise ValueError("owned layer pad observation is incomplete")
    candidates = []
    for pad_index, pad in enumerate(pads):
        shapes = pad.get("geometry")
        if not isinstance(shapes, list) or not shapes or len(shapes) > 32:
            raise ValueError("owned pad geometry observation is incomplete")
        for shape_index, shape in enumerate(shapes):
            outer, cutouts = _material(shape)
            cuts = [*cutouts, *hole_contours]
            parameters = sorted(
                {0.0, 1.0}
                | {
                    t
                    for contour in [outer, *cuts]
                    for t in _crossings(start, end, contour)
                    if math.isfinite(t) and 0 < t < 1
                }
            )
            for low, high in zip(parameters, parameters[1:], strict=False):
                middle = (low + high) / 2
                point = [start[i] + middle * (end[i] - start[i]) for i in (0, 1)]
                if _inside(point, outer) and not any(_inside(point, cut) for cut in cuts):
                    # Exclude cutout boundaries as well; midpoint contact with
                    # an edge is not positive-area material evidence.
                    boundary = any(
                        not _inside(point, cut)
                        and (
                            (point[0] - cut["circle"][0]) ** 2 + (point[1] - cut["circle"][1]) ** 2
                            == cut["circle"][2] ** 2
                            if "circle" in cut
                            else any(
                                _on_segment(point, a, b)
                                for a, b in zip(
                                    cut["polygon"],
                                    cut["polygon"][1:] + cut["polygon"][:1],
                                    strict=True,
                                )
                            )
                        )
                        for cut in cuts
                    )
                    if not boundary:
                        candidates.append(
                            {
                                "pad_observation_index": pad_index,
                                "shape_observation_index": shape_index,
                                "centerline_point_mm": point,
                                "segment_parameter_interval": [low, high],
                            }
                        )
    return candidates[0] if candidates else None


def physical_connection(plan):
    try:
        endpoints = plan["endpoints"]
        points, width = plan["request"]["points"], plan["request"]["width"]
        if (
            not isinstance(endpoints, list)
            or len(endpoints) != 2
            or not isinstance(points, list)
            or not 2 <= len(points) <= 10000
            or any(not _finite_row(p, 2) for p in points)
            or any(a == b for a, b in zip(points, points[1:], strict=False))
            or type(width) not in (float, int)
            or not math.isfinite(width)
            or width <= 0
        ):
            raise ValueError("two endpoints and a finite positive-width straight path are required")
        witnesses = [
            _contact_witness(endpoint, start, end)
            for endpoint, start, end in (
                (endpoints[0], points[0], points[1]),
                (endpoints[1], points[-1], points[-2]),
            )
        ]
        return {
            "status": "verified" if all(witnesses) else "failed",
            "verified": all(witnesses),
            "basis": "positive centerline intervals inside supported filled owned pad material",
            "endpoint_witnesses": witnesses,
            "not_checked": ["clearance", "DRC", "thermal/tie material", "manufacturing"],
        }
    except (KeyError, TypeError, ValueError, IndexError) as error:
        return {
            "status": "incomplete",
            "verified": False,
            "reason": str(error),
            "endpoint_witnesses": [],
        }
