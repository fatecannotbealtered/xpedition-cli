"""Bound one new plane on an empty, simple board through the installed 151.0 API.

PlaneShape, assignment state and actual GeneratedPlanes are separate evidence.
No replacement, global regeneration, implicit unit changes or automatic rollback.
"""

from __future__ import annotations

import copy
import math

from .cell_refresh import _canonical, active, capture, digest

MM = 4
DYNAMIC = 1
TOLERANCE_MM = 0.001


def evidence_boundary():
    """Public evidence meanings, independent of success or lifecycle state."""
    return {
        "draft_rectangle": "PlaneShape.Geometry is a draft boundary, not generated copper.",
        "generated_body_and_cutouts": (
            "GeneratedPlane.Geometry exposes the observed body and polygon/circular cutouts "
            "in mm; its inclusion of every thermal/tie conductor is not independently proven."
        ),
        "full_thermal_tie_conductors": {
            "independently_proven": False,
            "missing_evidence": [
                "complete conductor shapes with layer and net ownership",
                "actual pad contact and clearance along every connection leg",
            ],
            "meaning": "Full thermal/tie conductor geometry lacks independent proof.",
        },
        "native_connectivity": (
            "Native opens/routed observations are separate evidence; opens=0, a point in "
            "copper or a visible image alone cannot prove complete physical connections."
        ),
        "drc": "Copper observation does not execute DRC or establish rule applicability.",
        "persistence": (
            "Current reads do not prove saving; retained pre-save and reopened observations "
            "are compared separately, preserving raw equality and bounded rectangle equivalence."
        ),
    }


def _items(owner, name, a):
    collection = getattr(owner, name)
    if callable(collection) and not hasattr(collection, "Count"):
        collection = collection()
    if type(collection.Count) is not int or not 0 <= collection.Count <= 10000:
        raise ValueError("Unbounded or missing native collection")
    return list(a._items(collection, strict=True))


def _bool(value):
    if type(value) not in (bool, int) or value not in (-1, 0, 1):
        raise ValueError("Native boolean is unavailable")
    return bool(value)


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Native finite numeric value is unavailable")
    return float(value)


def _integer(value):
    if type(value) is not int:
        raise ValueError("Native integer is unavailable")
    return value


def _net(obj):
    net = obj.Net
    if net is None or not isinstance(net.Name, str) or not net.Name.strip():
        raise ValueError("Plane network identity is unavailable")
    uid = net.UniqueId
    if type(uid) not in (str, int) or not str(uid).strip():
        raise ValueError("Plane network UID is unavailable")
    return {"net": net.Name, "net_uid": str(uid)}


def read_geometry(geometry, a, *, nested=False):
    """Complete native contour in explicit mm, including circular cutouts.

    PointsArray/CircleX/Y/R are parameterized properties, not methods. The
    geometry validity predicates are methods. No bounding-box equivalence.
    """
    if not _bool(geometry.IsValid()) or not _bool(geometry.IsClosed()):
        raise ValueError("Plane geometry is not valid and closed")
    if _bool(geometry.IsCircle()):
        circle = [_number(a._propget(geometry, d, MM)) for d in (10, 11, 12)]
        if circle[2] <= 0:
            raise ValueError("Plane circle is empty")
        result = {"circle": circle}
    else:
        rows = a._propget(geometry, 1, MM)
        if len(rows) != 3 or not 3 <= len(rows[0]) <= 10000 or len({len(r) for r in rows}) != 1:
            raise ValueError("Plane contour is incomplete")
        path = [[_number(v) for v in values] for values in zip(*rows, strict=True)]
        if len({tuple(p[:2]) for p in path}) < 3:
            raise ValueError("Plane contour is empty")
        result = {"path": path}
    cuts = _items(geometry, "Cutouts", a)
    if nested and cuts:
        raise ValueError("Nested plane cutouts are outside the supported scope")
    for cut in cuts:
        child = read_geometry(cut, a, nested=True)
        if "circle" in child:
            result.setdefault("circular_cutouts", []).append(child["circle"])
        else:
            result.setdefault("cutouts", []).append(child["path"])
    return _canonical(result)


_CLASS_SCALARS = (
    "UsePadstackThermalDefinition",
    "EnableTieBridging",
    "DefaultViaTieLegType",
    "DefaultViaTieLegRotation",
    "DefaultThroughHoleTieLegType",
    "DefaultThroughHoleTieLegRotation",
    "DefaultSMDTieLegType",
    "DefaultSMDTieLegRotation",
    "DiscardAllUntiedAreas",
    "DiscardAreasTiedToSinglePad",
    "DiscardAreasLessThanDefinedArea",
    "HatchPattern",
    "FillNonSquareHatchAreas",
    "RestrictPlaneToOrthogonalPatterns",
    "SnapToHatchGrid",
    "HasOverrides",
    "RemoveScallops",
    "ClearPadIfOriginOutsideShape",
)
_CLASS_MM = (5, 6, 9, 10, 13, 14, 17, 18, 22, 23, 24, 25)


def _class_state(value, a):
    result = {"name": str(value.Name), "parent": str(value.Parent.Name) if value.Parent else None}
    for name in _CLASS_SCALARS:
        actual = getattr(value, name)
        if type(actual) not in (bool, int, float) or not math.isfinite(actual):
            raise ValueError("Plane class setting is unavailable")
        result[name] = actual
    result["lengths_mm_by_dispid"] = {str(d): _number(a._propget(value, d, MM)) for d in _CLASS_MM}
    # HatchAngle uses EPcbAngleUnit, not EPcbUnit. Zero is explicitly degrees.
    result["hatch_angle_degrees"] = _number(a._propget(value, 33, 0))
    return result


def _assignment(value):
    result = {
        **_net(value),
        "layer": _integer(value.LayerObject.ConductorLayer),
        "plane_class": str(value.PlaneClass.Name),
        "state": _integer(value.PlaneDataState),
        "route_border_as_shape": _bool(value.UseRouteBorderAsPlaneShape),
    }
    if result["layer"] < 1 or result["state"] not in (1, 2, 3, 4):
        raise ValueError("Unsupported plane assignment state")
    return result


def _assignment_key(row):
    return row["net_uid"], row["layer"]


def _witnesses(item, geometry, model, a):
    """Point witnesses supplement full contours; never a clearance/DRC proof."""
    samples = []
    for component in model["components"]:
        for pin in component["geometry"]:
            if any(p["layer"] == item["layer"] for p in pin["copper"]):
                samples.append(
                    {
                        "kind": "pin",
                        "refdes": component["refdes"],
                        "pin": pin["number"],
                        "net": pin["net"],
                        "xy_mm": pin["center_mm"],
                    }
                )
    samples += [
        {"kind": "drill_center", "xy_mm": h[:2], "drill_mm": h[2]} for h in model["model"]["holes"]
    ]
    cuts = _items(geometry, "Cutouts", a)
    for sample in samples:
        x, y = sample["xy_mm"]
        sample["outer_contains"] = _bool(geometry.IsPointInside(x, y, MM))
        sample["cutout_contains"] = [_bool(c.IsPointInside(x, y, MM)) for c in cuts]
        sample["copper_at_point"] = sample["outer_contains"] and not any(sample["cutout_contains"])
    return samples


def observe(doc, a, geometry=None):
    if type(doc.CurrentUnit) is not int or doc.CurrentUnit != 2:
        raise a.AdapterError("E_UNSUPPORTED", "Protected copper observation requires native mils")
    geometry = capture(doc, a, strict=True) if geometry is None else geometry
    shapes = []
    for item in _items(doc, "PlaneShapes", a):
        uid = item.UniqueId  # Public persistent identifier on PlaneShape in 151.0.
        if type(uid) not in (str, int) or not str(uid).strip():
            raise ValueError("PlaneShape identity is unavailable")
        shapes.append(
            {
                **_net(item),
                "uid": str(uid),
                "layer": _integer(item.Layer),
                "route_obstructed": _bool(item.RouteObstructed),
                "geometry": read_geometry(item.Geometry, a),
            }
        )
    generated = []
    for item in _items(doc, "GeneratedPlanes", a):
        if not _bool(item.IsValid()):
            raise ValueError("GeneratedPlane is invalid")
        row = {
            **_net(item),
            "layer": _integer(item.Layer),
            "geometry": read_geometry(item.Geometry, a),
            "bounds_mm": [_number(v) for v in a._extrema_mm(item)],
            "filled": _bool(item.Geometry.Filled),
            "hatch_pattern": _integer(item.HatchPattern),
            "hatch_width_mm": _number(a._propget(item, 51, MM)),
            "hatch_distance_mm": _number(a._propget(item, 52, MM)),
            "hatch_angle_degrees": _number(a._propget(item, 53, 0)),
            "fill_non_square_hatch_areas": _bool(item.FillNonSquareHatchAreas),
        }
        row["point_witnesses"] = _witnesses(row, item.Geometry, geometry, a)
        # GeneratedPlane.UniqueId is hidden in 151.0; do not require/use it.
        row["identity_basis"] = "native net UID/layer and complete geometry content"
        generated.append(row)
    assignments = [_assignment(item) for item in _items(doc, "PlaneAssignments", a)]
    if len({_assignment_key(r) for r in assignments}) != len(assignments):
        raise ValueError("Duplicate plane assignments")
    classes = [_class_state(c, a) for c in _items(doc, "PlaneClasses", a)]
    return _canonical(
        {
            "geometry": geometry,
            "plane_state": {
                "shapes": shapes,
                "assignments": assignments,
                "generated": generated,
                "classes": classes,
            },
        }
    )


def _rectangle(shape):
    points = list(shape.get("path") or [])
    if points and points[-1] == points[0]:
        points.pop()
    if len(points) != 4 or shape.get("cutouts") or shape.get("circular_cutouts"):
        raise ValueError("Only a simple rectangular boundary is supported")
    if any(len(p) != 3 or p[2] != 0 or not all(math.isfinite(v) for v in p) for p in points):
        raise ValueError("Arced or incomplete boundary is unsupported")
    x1, y1 = min(p[0] for p in points), min(p[1] for p in points)
    x2, y2 = max(p[0] for p in points), max(p[1] for p in points)
    if (
        x1 >= x2
        or y1 >= y2
        or {tuple(p[:2]) for p in points} != {(x1, y1), (x1, y2), (x2, y1), (x2, y2)}
    ):
        raise ValueError("Boundary is not an axis-aligned rectangle")
    if any(
        p[0] != q[0] and p[1] != q[1] for p, q in zip(points, points[1:] + points[:1], strict=True)
    ):
        raise ValueError("Boundary has diagonal/crossed edges")
    return [x1, y1, x2, y2]


def _persistence_comparison(before, after, plan, created_uid):
    """Retain raw equality; permit only the bound draft rectangle's vertex order.

    Layout 151.0 can reverse that rectangle when saving/reopening. Validate
    all four corners and straight, noncrossing edges before comparing regions.
    Generated copper, cutouts, point witnesses and every other field remain exact.
    Neither input is changed, and no tolerance or general polygon equivalence is used.
    """
    equal = before == after
    result = {
        "equal": equal,
        "before_sha256": digest(before),
        "after_sha256": digest(after),
        "geometry_equal": before["geometry"] == after["geometry"],
        "plane_state_equal": before["plane_state"] == after["plane_state"],
        "persistence_equivalent": equal,
        "equivalence_basis": "strict" if equal else "different_or_unsupported",
    }
    if equal:
        return result
    try:
        normalized, rectangles = [], []
        for state in (before, after):
            shapes = state["plane_state"]["shapes"]
            if len(shapes) != 1 or shapes[0]["uid"] != created_uid:
                return result
            if any(shapes[0][k] != plan["target"][k] for k in ("net", "net_uid", "layer")):
                return result
            rectangle = _rectangle(shapes[0]["geometry"])
            rectangles.append(rectangle)
            x1, y1, x2, y2 = rectangle
            value = copy.deepcopy(state)
            value["plane_state"]["shapes"][0]["geometry"]["path"] = [
                [x1, y1, 0.0],
                [x2, y1, 0.0],
                [x2, y2, 0.0],
                [x1, y2, 0.0],
                [x1, y1, 0.0],
            ]
            normalized.append(value)
        if rectangles[0] == rectangles[1] and normalized[0] == normalized[1]:
            result.update(
                persistence_equivalent=True,
                equivalence_basis="target_rectangle_vertex_order",
            )
    except (KeyError, TypeError, ValueError, IndexError):
        # Missing/unsupported observations do not establish persistence.
        pass
    return result


def bind(state, net, layer, margin, a):
    if (
        type(layer) is not int
        or layer < 1
        or type(margin) not in (int, float)
        or not math.isfinite(margin)
        or margin < 0
    ):
        raise a.AdapterError(
            "E_VALIDATION", "A positive layer and finite nonnegative margin are required"
        )
    geo, planes = state["geometry"], state["plane_state"]
    connectivity = geo["model"]["connectivity"]
    if connectivity.get("complete") is not True or any(
        not r.get("net")
        or not r.get("uid")
        or type(r.get("opens")) is not int
        or r["opens"] < 0
        or type(r.get("routed")) is not bool
        for r in connectivity.get("nets", [])
    ):
        raise a.AdapterError("E_NATIVE_FAILURE", "Complete native connectivity is required")
    matches = [r for r in connectivity["nets"] if r["net"] == net]
    if len(matches) != 1 or not matches[0].get("uid"):
        raise a.AdapterError("E_NOT_FOUND", "Target net identity is unavailable")
    conductors = [r for r in geo["stackup"] if r["kind"] == "conductor"]
    selected_layer = [r for r in conductors if r.get("layer") == layer]
    if (
        len(conductors) != geo["model"]["layers"]
        or len(selected_layer) != 1
        or any(type(r.get("layer")) is not int for r in conductors)
        or {r.get("layer") for r in conductors} != set(range(1, geo["model"]["layers"] + 1))
        or any(r.get("usage") != "signal" for r in conductors)
    ):
        raise a.AdapterError(
            "E_UNSUPPORTED", "This copper subset requires observed signal conductor layers"
        )
    if not planes["classes"] or len({r["name"] for r in planes["classes"]}) != len(
        planes["classes"]
    ):
        raise a.AdapterError("E_UNSUPPORTED", "Existing unique plane class settings are required")
    if planes["shapes"] or planes["generated"] or geo["model"]["planes"]:
        raise a.AdapterError(
            "E_CONFLICT", "Existing plane objects conflict; replacement is not supported"
        )
    if geo["model"].get("vias") or geo["model"].get("slots"):
        raise a.AdapterError(
            "E_UNSUPPORTED", "Initial copper subset excludes vias and slotted holes"
        )
    if any(r["route_border_as_shape"] for r in planes["assignments"]):
        raise a.AdapterError(
            "E_UNSUPPORTED", "Implicit route-border plane shapes are outside the scope"
        )
    if len(geo["model"]["outline"]) != 1:
        raise a.AdapterError("E_UNSUPPORTED", "Exactly one rectangular board outline is required")
    try:
        x1, y1, x2, y2 = _rectangle(geo["model"]["outline"][0])
    except ValueError as error:
        raise a.AdapterError("E_UNSUPPORTED", str(error)) from error
    rect = [x1 + margin, y1 + margin, x2 - margin, y2 - margin]
    if rect[0] >= rect[2] or rect[1] >= rect[3]:
        raise a.AdapterError("E_VALIDATION", "Margin leaves no copper region")
    target = {"net": net, "net_uid": matches[0]["uid"], "layer": layer, "layer_usage": "signal"}
    plan = {
        "before": state,
        "target": target,
        "rectangle": rect,
        "expected_assignment_changes": [{**target, "state": DYNAMIC}],
        "generated_scope": "one GeneratedPlane; observed body/cutout contours and point witnesses",
        "evidence_boundary": evidence_boundary(),
        "not_checked": [
            "complete thermal/tie conductor geometry, pad contact and leg clearance",
            "full clearance/DRC",
            "arbitrary outlines or plane stacks",
            "hidden GeneratedPlane UID",
        ],
    }
    plan["fingerprint"] = digest(plan)
    return plan


def compare(plan, observed, created_uid, *, generated_required=True):
    before, target = plan["before"], plan["target"]
    old, new = before["plane_state"], observed["plane_state"]
    checks = []

    def check(name, passed, *, completion=False):
        checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "category": "completion" if completion else "protection",
            }
        )

    def target_matches(row):
        return all(row[k] == target[k] for k in ("net", "net_uid", "layer"))

    check(
        "only bound new PlaneShape",
        len(new["shapes"]) == 1
        and created_uid is not None
        and new["shapes"][0]["uid"] == created_uid
        and target_matches(new["shapes"][0])
        and new["shapes"][0]["route_obstructed"] is False,
    )
    try:
        rect = _rectangle(new["shapes"][0]["geometry"])
        shape_matches = all(
            abs(x - y) <= TOLERANCE_MM for x, y in zip(rect, plan["rectangle"], strict=True)
        )
    except (IndexError, KeyError, ValueError):
        shape_matches = False
    check("actual draft contour equals requested rectangle", shape_matches)
    check("plane classes unchanged", old["classes"] == new["classes"])
    key = (target["net_uid"], target["layer"])
    old_assign = {_assignment_key(r): r for r in old["assignments"]}
    new_assign = {_assignment_key(r): r for r in new["assignments"]}
    check(
        "non-target assignments unchanged",
        {k: v for k, v in old_assign.items() if k != key}
        == {k: v for k, v in new_assign.items() if k != key},
    )
    assignment = new_assign.get(key)
    check(
        "target assignment identity",
        assignment and target_matches(assignment) and not assignment["route_border_as_shape"],
    )
    if key in old_assign and assignment:
        check(
            "target assignment properties preserved",
            {k: v for k, v in old_assign[key].items() if k != "state"}
            == {k: v for k, v in assignment.items() if k != "state"},
        )
    left, right = copy.deepcopy(before["geometry"]), copy.deepcopy(observed["geometry"])
    old_conn = left["model"].pop("connectivity")
    new_conn = right["model"].pop("connectivity")
    for value in (left, right):
        value["model"].pop("planes")
        value["model"].pop("unrouted")
    check("all protected board/component/pin/drill/routing geometry unchanged", left == right)
    valid_conn = new_conn.get("complete") is True and len(new_conn["nets"]) == len(old_conn["nets"])
    valid_conn = valid_conn and all(
        type(r.get("opens")) is int and r["opens"] >= 0 and type(r.get("routed")) is bool
        for r in new_conn["nets"]
    )
    check("complete connectivity", valid_conn)
    check(
        "unrouted list agrees with complete connectivity",
        observed["geometry"]["model"]["unrouted"]
        == [r for r in new_conn["nets"] if r.get("opens") or r.get("routed") is False],
    )
    check(
        "all net identities preserved",
        {r["net"]: r["uid"] for r in old_conn["nets"]}
        == {r["net"]: r["uid"] for r in new_conn["nets"]},
    )
    check(
        "non-target connectivity unchanged",
        [r for r in old_conn["nets"] if r["uid"] != target["net_uid"]]
        == [r for r in new_conn["nets"] if r["uid"] != target["net_uid"]],
    )
    actual = new["generated"]
    check("generated objects only on bound net/layer", all(target_matches(r) for r in actual))
    for row in actual:
        bounds = row["bounds_mm"]
        x1, y1, x2, y2 = plan["rectangle"]
        check(
            "generated copper lies inside bound rectangle",
            len(bounds) == 4
            and x1 - TOLERANCE_MM <= bounds[0] < bounds[2] <= x2 + TOLERANCE_MM
            and y1 - TOLERANCE_MM <= bounds[1] < bounds[3] <= y2 + TOLERANCE_MM,
        )
        check(
            "foreign pin centers avoided",
            all(
                not p["copper_at_point"]
                for p in row["point_witnesses"]
                if p["kind"] == "pin" and p["net"] != target["net"]
            ),
        )
    check(
        "one actual filled generated copper region",
        len(actual) == 1 and actual[0]["filled"] is True,
        completion=True,
    )
    check(
        "target assignment dynamic", assignment and assignment["state"] == DYNAMIC, completion=True
    )
    protection = all(r["passed"] for r in checks if r["category"] == "protection")
    complete = all(r["passed"] for r in checks)
    return {
        "valid": complete if generated_required else protection,
        "protection_valid": protection,
        "generation_complete": complete,
        "checks": checks,
        "engineering_complete": False,
    }


def layout_reminders(plan):
    """Warn about projected text under the proposed region without editing it."""
    from .board_view import reminders

    model = plan["before"]["geometry"]["model"]
    result = reminders(model)
    x1, y1, x2, y2 = plan["rectangle"]
    for text in model.get("texts", []):
        box = text.get("extents")
        if (
            box
            and len(box) == 4
            and box[0] <= x2
            and box[2] >= x1
            and box[1] <= y2
            and box[3] >= y1
        ):
            result.append(
                {
                    "kind": "projected_text_planned_plane_overlap",
                    "text_kind": text.get("kind"),
                    "refdes": text.get("refdes"),
                    "text_side": text.get("side"),
                    "plane_layer": plan["target"]["layer"],
                    "net": plan["target"]["net"],
                    "meaning": (
                        "Text bounds overlap the proposed plane in projection; not a DRC result. "
                        "Review actual layers/voids; no protected object will be moved/hidden."
                    ),
                }
            )
    return result


def execute(params, client, a):
    if params.get("replace") or params.get("remove"):
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "Copper replacement/removal is outside the verified subset",
            {"write_attempted": False},
        )
    try:
        _, board, app, doc = active(params, client, a, require_project=False)
        state = a._native_call("copper.observe_before", lambda: observe(doc, a))
        plan = bind(state, str(params["net"]), int(params["layer"]), float(params["margin"]), a)
    except Exception as error:
        raise a.AdapterError(
            getattr(error, "code", "E_NATIVE_FAILURE"),
            "Copper preflight failed",
            {
                **(getattr(error, "details", {}) or {}),
                "write_attempted": False,
                "stage": "copper.preflight",
                "reason": str(error),
            },
        ) from error
    result = {
        "pcb": str(board),
        "net": params["net"],
        "layer": params["layer"],
        "copper": plan,
        "applied": False,
        "change_status": "not_applied",
        "saved": False,
        "persistence_state": "unchanged",
        "persistence_verified": False,
        "created_shape_uid": None,
        "stages": {
            k: "not_started"
            for k in (
                "create",
                "observe_created",
                "generate",
                "observe_generated",
                "protection",
                "save",
                "close",
                "reopen",
            )
        },
        "_untrusted": [
            "pcb",
            "net",
            "copper",
            "state_before_save",
            "state_after",
            "geometry_after",
            "reopen_comparison",
            "post_write_error",
        ],
    }
    if not params.get("apply"):
        return result
    if params.get("expected") != plan["fingerprint"]:
        raise a.AdapterError(
            "E_CONFLICT", "Copper input changed after preview", {"write_attempted": False}
        )
    try:
        net = a._find_net(doc, plan["target"]["net"])
        if str(net.UniqueId) != plan["target"]["net_uid"]:
            raise a.AdapterError("E_CONFLICT", "Native target net identity changed")
    except Exception as error:
        raise a.AdapterError(
            getattr(error, "code", "E_NATIVE_FAILURE"),
            "Copper target could not be bound before creation",
            {"write_attempted": False, "stage": "copper.bind_net"},
        ) from error
    phase = "create"
    try:
        result.update(change_status="unknown", persistence_state="unknown")
        result["stages"][phase] = "started"
        x1, y1, x2, y2 = plan["rectangle"]
        points = ((x1, x2, x2, x1, x1), (y1, y1, y2, y2, y1), (0.0,) * 5)
        shape = a._native_call(
            "copper.PutPlaneShape",
            lambda: doc.PutPlaneShape(
                plan["target"]["layer"], 5, points, net, False, 0, 0.0, 0.0, None, MM
            ),
        )
        result["created_shape_uid"] = str(shape.UniqueId)
        result.update(applied=True, change_status="partial")
        result["stages"][phase] = "returned"
        phase = "observe_created"
        result["stages"][phase] = "started"
        observed = a._native_call("copper.observe_created", lambda: observe(doc, a))
        result.update(state_after=observed, geometry_after=observed["geometry"])
        result["stages"][phase] = "complete"
        proof = compare(plan, observed, result["created_shape_uid"], generated_required=False)
        result["protection"] = proof
        if not proof["protection_valid"]:
            raise a.AdapterError("E_PARTIAL", "Protection failed after PlaneShape creation")
        phase = "generate"
        result["stages"][phase] = "started"
        assignment = a._native_call(
            "copper.FindPlaneAssignment",
            lambda: doc.FindPlaneAssignment(plan["target"]["layer"], net),
        )
        if (
            assignment is None
            or _assignment(assignment) not in observed["plane_state"]["assignments"]
        ):
            raise a.AdapterError(
                "E_NATIVE_FAILURE", "Exact target assignment could not be observed"
            )
        actual = _assignment(assignment)
        if _assignment_key(actual) != (plan["target"]["net_uid"], plan["target"]["layer"]):
            raise a.AdapterError("E_CONFLICT", "FindPlaneAssignment returned a different target")
        if actual["state"] != DYNAMIC:
            a._native_call(
                "copper.PlaneDataState", lambda: setattr(assignment, "PlaneDataState", DYNAMIC)
            )
        result["stages"][phase] = "returned"
        phase = "observe_generated"
        result["stages"][phase] = "started"
        observed = a._native_call("copper.observe_generated", lambda: observe(doc, a))
        result.update(state_after=observed, geometry_after=observed["geometry"])
        result["stages"][phase] = "complete"
        proof = compare(plan, observed, result["created_shape_uid"])
        result["protection"] = proof
        result["stages"]["protection"] = "passed" if proof["valid"] else "failed"
        if not proof["valid"]:
            raise a.AdapterError(
                "E_PARTIAL", "Generated copper or protection could not be verified"
            )
        result["change_status"] = "applied"
        # Retain the complete generated observation before the native lifecycle;
        # later readback replaces state_after, even when persistence mismatches.
        result["state_before_save"] = copy.deepcopy(observed)
        phase = "save"
        result["stages"][phase] = "started"
        try:
            a._save_close_board(app, board)
        except Exception as error:
            if (getattr(error, "details", {}) or {}).get("save_acknowledged") is True:
                result["saved"] = True
                result["stages"].update(save="acknowledged", close="unconfirmed")
            raise
        result.update(saved=True)
        result["stages"].update(save="acknowledged", close="confirmed", reopen="started")
        phase = "reopen"
        a._native_call("copper.reopen", lambda: a._open_layout_document(app, board))
        result["stages"][phase] = "returned"
        _, _, _, reopened = active(params, client, a, require_project=False)
        after = a._native_call("copper.observe_reopened", lambda: observe(reopened, a))
        comparison = _persistence_comparison(
            result["state_before_save"], after, plan, result["created_shape_uid"]
        )
        result.update(
            state_after=after,
            geometry_after=after["geometry"],
            persistence_verified=comparison["persistence_equivalent"],
            reopen_comparison=comparison,
        )
        result["stages"][phase] = "verified" if result["persistence_verified"] else "mismatch"
        result["persistence_state"] = "saved" if result["persistence_verified"] else "unknown"
        if not result["persistence_verified"]:
            raise a.AdapterError("E_VERIFY_INCOMPLETE", "Saved/reopened copper differs")
        return result
    except Exception as error:
        # Observation can establish a partial change after a failed native call;
        # it never repeats that call, saves, closes or rolls back the document.
        if phase in {"create", "observe_created", "generate", "observe_generated"}:
            try:
                observed = a._native_call("copper.observe_failure", lambda: observe(doc, a))
                result.update(
                    state_after=observed,
                    geometry_after=observed["geometry"],
                    applied=observed != state,
                    change_status="partial" if observed != state else "not_applied",
                )
            except Exception as observation_error:
                result["failure_observation_error"] = type(observation_error).__name__
        result["post_write_error"] = {
            "stage": phase,
            "code": getattr(error, "code", "E_NATIVE_FAILURE"),
            "cause": type(error).__name__,
            "reason": str(error),
        }
        raise a.AdapterError(
            getattr(error, "code", "E_NATIVE_FAILURE"),
            "Copper operation did not complete",
            {"write_attempted": True, "native_result": result},
        ) from error


def verify(result, before, after, plan):
    try:
        bound = plan["extensions"]["legacy_preview"]["copper"]
        new = after["extensions"]["facts"]["cell_refresh"]
        new = {k: new[k] for k in ("geometry", "plane_state")}
        old = before["extensions"]["facts"]["cell_refresh"]
        old = {k: old[k] for k in ("geometry", "plane_state")}
        proof = compare(bound, new, result.get("created_shape_uid"))
        checks = proof["checks"]
        for name, value in (
            ("preview matches independent before", old == bound["before"]),
            ("independent after matches native result", new == result.get("state_after")),
            (
                "saved and independently reopened",
                result.get("saved") is True and result.get("persistence_verified") is True,
            ),
        ):
            checks.append({"name": name, "passed": bool(value)})
        return {
            **proof,
            "valid": all(r["passed"] for r in checks),
            "persistence_valid": result.get("persistence_verified") is True
            and new == result.get("state_after"),
            "scope": "one generated copper region, protected geometry/settings and save/reopen",
            "source": "independent native contours/assignment; persistence separately evidenced",
            "evidence_boundary": evidence_boundary(),
            "not_checked": [
                "complete thermal/tie conductor geometry, pad contact and leg clearance",
                "native DRC or complete clearance",
                "plane-group applicability",
                "hidden generated UID",
            ],
        }
    except (KeyError, ValueError, TypeError) as error:
        return {
            "valid": False,
            "engineering_complete": False,
            "checks": [],
            "scope": "bounded copper observation",
            "source": "incomplete native evidence",
            "read_error": type(error).__name__,
            "evidence_boundary": evidence_boundary(),
            "not_checked": [
                "complete thermal/tie conductor geometry, pad contact and leg clearance",
                "missing native evidence",
                "native DRC",
            ],
        }
