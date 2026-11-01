"""Temporary native-mask qualification of one frozen component connection.

Only invocation-owned scratch Masks are created and deleted. These observations
do not authorize dispatch, resolve spacing rules or establish persistence.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

from . import board_components as C
from . import board_routepass as R
from . import native_pad_mask as M
from .cell_refresh import _canonical, digest
from .design_capabilities import prospective_budget

SOURCE = "native-component-candidate-terminal-anchor-mask151-v2"


def _anchors(state, snapshot, connection):
    values = [connection["width_mm"]] + [value for point in connection["points"] for value in point]
    if any(
        Decimal(str(value)) * 10000 != (Decimal(str(value)) * 10000).to_integral_value()
        for value in values
    ):
        raise ValueError("Candidate inputs must be exact four-decimal-mm grid values; no rounding")
    nodes = {row["node_id"]: row for row in snapshot["nodes"]}
    groups = {row["component_id"]: row["node_ids"] for row in snapshot["components"]}
    pins = {(row["component_uid"], row["pin_uid"]): row for row in state["all_pin_pad_material"]}
    traces = {row["uid"]: row for row in state["native_trace_material"]}
    anchors = []
    for key, point in (("first", connection["points"][0]), ("second", connection["points"][-1])):
        selected = connection[key]
        candidates = (
            [selected["node_id"]]
            if selected.get("node_id") is not None
            else groups[selected["component_id"]]
        )
        matches = []
        for node_id in candidates:
            node = nodes[node_id]
            if node["kind"] == "pin":
                pin = pins[(node["component_uid"], node["pin_uid"])]
                if point == pin["center_mm"]:
                    matches.append(
                        {
                            "kind": "pin",
                            "node_id": node_id,
                            "component_uid": pin["component_uid"],
                            "pin_uid": pin["pin_uid"],
                            "net_uid": pin["net_uid"],
                            "point_mm": list(point),
                        }
                    )
            elif node["kind"] == "trace":
                trace = traces[node["trace_uid"]]
                raw = trace["points_array_mm"]
                ends = [i for i in (0, len(raw[0]) - 1) if point == [raw[0][i], raw[1][i]]]
                if len(ends) == 1:
                    matches.append(
                        {
                            "kind": "trace",
                            "node_id": node_id,
                            "trace_uid": trace["uid"],
                            "vertex_index": ends[0],
                            "net_uid": trace["net_uid"],
                            "point_mm": list(point),
                        }
                    )
        if not matches:
            raise ValueError(
                "Each candidate anchor must be an actual selected pin center "
                "or trace first/last vertex"
            )
        matches.sort(key=lambda row: (row["kind"] != "pin", row["node_id"]))
        anchors.append({"endpoint": key, "component_id": selected["component_id"], **matches[0]})
    return anchors


def _binding(state, snapshot, connection):
    C._snapshot(snapshot)
    target = snapshot["target"]
    budget = prospective_budget(state, target)
    if not budget["valid"]:
        raise ValueError(
            "Prospective whole-board resource budget refused before dispatch: " + str(budget)
        )
    observed = M.observe_components(state, target)
    current = C.build_snapshot(
        state,
        observed,
        board_id=snapshot["board_id"],
        net_id=target["net_uid"],
        layer=target["layer"],
    )
    if not current["valid"] or current != snapshot:
        raise ValueError("Candidate must bind the complete current material snapshot")
    rebuilt = C.plan_connection(
        snapshot,
        first=connection["first"],
        second=connection["second"],
        points=connection["points"],
        width_mm=connection["width_mm"],
    )
    if rebuilt != connection or not rebuilt["structural_valid"] or rebuilt["no_op"]:
        raise ValueError("Candidate qualification requires one frozen non-no-op connection")
    R._round_hole_inventory(state)
    points = connection["points"]
    raw = {
        "net": target["net"],
        "net_uid": target["net_uid"],
        "layer": target["layer"],
        "width_mm": connection["width_mm"],
        "points_array_mm": [[p[i] for p in points] for i in (0, 1)] + [[0.0] * len(points)],
    }
    return {
        "state_inputs_sha256": digest(R.design_state(state)),
        "snapshot_id": snapshot["snapshot_id"],
        "connection_sha256": digest(connection),
        "material_sha256": digest(state["all_pin_pad_material"]),
        "raw_trace_sha256": digest(state["native_trace_material"]),
        "candidate": raw,
        "candidate_sha256": digest(raw),
        "anchors": _anchors(state, snapshot, connection),
        "prospective_budget": budget,
    }


def _raw_pins(doc, a, layer):
    rows = []
    for component in R.board_trace._collection(doc, "Components", a):
        for pin in R.board_trace._collection(component, "Pins", a):
            if pin.Net is None:
                raise ValueError(
                    "Unassigned native pin material cannot be omitted from qualification"
                )
            rows.append(
                {
                    "component_uid": R.board_trace._uid(component.UniqueId),
                    "refdes": str(component.RefDes),
                    "pin": str(pin.Name),
                    "pin_uid": R.board_trace._uid(pin.UniqueId),
                    "net": str(pin.Net.Name),
                    "net_uid": R.board_trace._uid(pin.Net.UniqueId),
                    "center_mm": [float(pin.GetPositionX(4)), float(pin.GetPositionY(4))],
                    "material": M.observe_pin(pin, layer, a),
                }
            )
    return sorted(rows, key=lambda row: (row["component_uid"], row["pin_uid"]))


def _outline_plan(contour):
    """Recognize one bounded rounded family; native equality is still mandatory."""
    plan = M._connected_shape_plan(contour, [])
    if plan["kind"] == "simple_polygon":
        return plan
    if plan["kind"] != "native_equivalent_rounded_rectangle":
        raise ValueError("Board outline requires a simple polygon or witnessed rounded rectangle")
    x0, y0, x1, y1 = plan["bounds_mm"]
    r = plan["radius_mm"]
    expected = [
        [x1 - r, y0, 0],
        [x1 - r, y0 + r, -r],
        [x1, y0 + r, 0],
        [x1, y1 - r, 0],
        [x1 - r, y1 - r, -r],
        [x1 - r, y1, 0],
        [x0 + r, y1, 0],
        [x0 + r, y1 - r, -r],
        [x0, y1 - r, 0],
        [x0, y0 + r, 0],
        [x0 + r, y0 + r, -r],
        [x0 + r, y0, 0],
    ]
    observed = [list(p) for p in zip(*contour["points_array_mm"], strict=True)]
    if observed[-1] != observed[0] or not any(
        _canonical(observed[:-1]) == _canonical(expected[index:] + expected[:index])
        for index in range(len(expected))
    ):
        raise ValueError(
            "Arced board outline differs from the strict negative-quarter-arc 13-record family"
        )
    return plan


def _outline(doc, a, state):
    geometry = doc.BoardOutline.Geometry
    cutouts = a._propget(geometry, 22)
    if type(cutouts.Count) is not int or cutouts.Count != 0:
        raise ValueError("Candidate supports only a board outline with no inner contours")
    raw = M._contour(
        geometry,
        a,
        {
            "contours": 0,
            "maximum_contours": 1,
            "points": 0,
            "maximum_points": 128,
        },
    )
    _outline_plan(raw)
    outline = state["geometry"]["model"]["outline"]
    expected = {
        "kind": "outline",
        "path": [list(p) for p in zip(*raw["points_array_mm"], strict=True)],
    }
    if len(outline) != 1 or _canonical(outline[0]) != _canonical(expected):
        raise ValueError("Fresh complete native board outline differs from captured geometry")
    return {
        "contour": raw,
        "inner_contour_count": 0,
        "source": "BoardOutline.Geometry; explicit mm",
    }


def _fresh(doc, a, state, snapshot):
    if (
        type(doc.CurrentUnit) is not int
        or doc.CurrentUnit != 2
        or not isinstance(snapshot["board_id"], str)
        or Path(str(doc.FullName)).resolve() != Path(snapshot["board_id"]).resolve()
    ):
        raise ValueError("Candidate actual document identity or current units differ")
    pins = _raw_pins(doc, a, snapshot["target"]["layer"])
    traces = M._trace_inventory(doc, state, a)
    if pins != state["all_pin_pad_material"] or traces != state["native_trace_material"]:
        raise ValueError("Fresh complete native material inputs differ from the frozen candidate")
    return {
        "pin_material_sha256": digest(pins),
        "raw_trace_sha256": digest(traces),
        "outline": _outline(doc, a, state),
    }


def _area(row):
    area = M._number(row["area_mm2"])
    if (
        area < 0
        or type(row["is_empty"]) is not bool
        or row["is_empty"] != (area == 0)
        or type(row["shape_count"]) is not int
        or not 0 <= row["shape_count"] <= M.MAX_CONTOURS
        or (area > 0 and row["shape_count"] == 0)
    ):
        raise ValueError("Native candidate intersection area/emptiness is inconsistent")
    return area


def _intersection(owner, candidate, other):
    row = M._region_area(owner, candidate, other)
    touch = candidate.Touch(other)
    if type(touch) is not bool:
        raise ValueError("Formal native candidate Mask.Touch did not return BOOL")
    return {**row, "boundary_touch": touch, "boundary_touch_source": "IMask.Touch151"}


def _separate(row):
    return (
        _area(row) == 0
        and row["boundary_touch"] is False
        and row["boundary_touch_source"] == "IMask.Touch151"
    )


def _voids(state):
    rows = []
    for index, hole in enumerate(M._round_holes(state["all_pin_pad_material"])):
        rows.append(
            {
                "void_id": "drill:" + str(index),
                "contour": {
                    "kind": "circle",
                    "center_mm": hole["center_mm"],
                    "radius_mm": hole["diameter_mm"] / 2,
                },
                "owners": [
                    {
                        "component_uid": pin["component_uid"],
                        "pin_uid": pin["pin_uid"],
                        "net_uid": pin["net_uid"],
                    }
                    for pin in state["all_pin_pad_material"]
                    if any(
                        other["center_mm"] == hole["center_mm"]
                        and other["diameter_mm"] == hole["diameter_mm"]
                        for other in pin["material"]["holes"]
                    )
                ],
            }
        )
    for pin in state["all_pin_pad_material"]:
        for pad_index, pad in enumerate(pin["material"]["pads"]):
            for shape_index, shape in enumerate(pad["geometry"]):
                for index, cutout in enumerate(shape["cutouts"]):
                    rows.append(
                        {
                            "void_id": "cutout:"
                            + digest(
                                [
                                    pin["component_uid"],
                                    pin["pin_uid"],
                                    pad_index,
                                    shape_index,
                                    index,
                                ]
                            ),
                            "contour": cutout,
                        }
                    )
    return rows


def _terminal_crop(raw, row, anchors):
    if not row["void_id"].startswith("drill:") or len(row["owners"]) != 1:
        raise ValueError("Only one selected pin-owned terminal round drill may crop a candidate")
    owner, contour = row["owners"][0], row["contour"]
    if not any(
        anchor["kind"] == "pin"
        and all(anchor[key] == owner[key] for key in ("component_uid", "pin_uid", "net_uid"))
        and anchor["point_mm"] == contour["center_mm"]
        for anchor in anchors
    ):
        raise ValueError("A candidate drill intersection is not owned by its selected terminal pin")
    return M._terminal_hole_cut(
        raw, {"center_mm": contour["center_mm"], "diameter_mm": contour["radius_mm"] * 2}
    )


def _expected_foreign(state, target):
    rows = []
    for pin in state["all_pin_pad_material"]:
        if pin["net_uid"] != target["net_uid"]:
            for index, _ in enumerate(pin["material"]["pads"]):
                rows.append(
                    {
                        "object_id": "pad:" + digest([pin["component_uid"], pin["pin_uid"], index]),
                        "raw_sha256": digest(pin),
                        "kind": "pad",
                    }
                )
    for trace in state["native_trace_material"]:
        if trace["net_uid"] != target["net_uid"] and trace["layer"] == target["layer"]:
            rows.append(
                {"object_id": "trace:" + trace["uid"], "raw_sha256": digest(trace), "kind": "trace"}
            )
    return rows


def _envelope(outline):
    bounds = M._contour_bounds(outline)
    x0, y0, x1, y1 = [bounds[0] - 1, bounds[1] - 1, bounds[2] + 1, bounds[3] + 1]
    return {
        "kind": "native_points_array",
        "closed": True,
        "is_arced": False,
        "points_array_mm": [[x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], [0] * 5],
    }


def evaluate(doc, a, state, snapshot, connection):
    """Fresh scratch-mask measurements; never PutTrace, transaction or persistence."""
    result = {
        "source": SOURCE,
        "unit": "mm",
        "valid": False,
        "complete": False,
        "binding": None,
        "before_inputs": None,
        "after_cleanup_inputs": None,
        "candidate_area": None,
        "candidate_raw_area": None,
        "terminal_crop_pairs": None,
        "target_intersections": [],
        "foreign_intersections": [],
        "void_intersections": [],
        "pad_region_witnesses": [],
        "board_containment": None,
        "mask_touch_controls": [],
        "mask_touch_formal_source": copy.deepcopy(M.TOUCH_FORMAL_SOURCE),
        "cleanup": [],
        "temporary_masks_count": None,
        "errors": [],
        "not_checked": ["effective spacing rules", "DRC", "persistence", "manufacturing"],
    }
    owner = None
    try:
        result["binding"] = _binding(state, snapshot, connection)
        result["before_inputs"] = _fresh(doc, a, state, snapshot)
        owner = M._Masks(a._propget(doc, 326), a)
        M._touch_controls(owner, result["mask_touch_controls"])
        raw_candidate = owner.path(result["binding"]["candidate"])
        result["candidate_raw_area"] = owner.area(raw_candidate)
        if result["candidate_raw_area"]["area_mm2"] <= 0:
            raise ValueError("Candidate has no positive full-width native material")
        void_rows = _voids(state)
        hole_masks, void_masks, hole_intersections = [], [], []
        candidate = raw_candidate
        for row in void_rows:
            mask = owner.contour(row["contour"])
            void_masks.append(mask)
            intersection = _intersection(owner, raw_candidate, mask)
            observed_void = {**row, "raw_intersection": intersection, "terminal_crop": None}
            result["void_intersections"].append(observed_void)
            if row["void_id"].startswith("drill:"):
                hole_intersections.append({"hole_index": len(hole_masks), **intersection})
                if intersection["area_mm2"] > 0:
                    observed_void["terminal_crop"] = _terminal_crop(
                        result["binding"]["candidate"], row, result["binding"]["anchors"]
                    )
                hole_masks.append(mask)
                candidate = owner.boolean(0, candidate, mask)
        result["terminal_crop_pairs"] = M._terminal_crop_pairs(
            result["binding"]["candidate"],
            M._round_holes(state["all_pin_pad_material"]),
            hole_intersections,
        )
        result["candidate_area"] = owner.area(candidate)
        if not 0 < result["candidate_area"]["area_mm2"] <= result["candidate_raw_area"]["area_mm2"]:
            raise ValueError(
                "Candidate must retain positive physical material after all drill subtraction"
            )
        if (
            any(row["terminal_crop"] is not None for row in result["void_intersections"])
            and result["candidate_area"]["shape_count"] != 1
        ):
            raise ValueError(
                "Native terminal-cropped candidate must retain one witnessed material shape"
            )
        for row, mask in zip(result["void_intersections"], void_masks, strict=True):
            row.update(_intersection(owner, candidate, mask))
        trace_masks = {}
        for trace in state["native_trace_material"]:
            if trace["layer"] != snapshot["target"]["layer"]:
                continue
            mask = owner.path(trace)
            for hole in hole_masks:
                mask = owner.boolean(0, mask, hole)
            trace_masks[trace["uid"]] = mask
        target_masks = {}
        for pin in state["all_pin_pad_material"]:
            if pin["net_uid"] != snapshot["target"]["net_uid"]:
                _, _, _, masks = M._material_masks(owner, pin["material"])
                for index, mask in enumerate(masks):
                    result["foreign_intersections"].append(
                        {
                            "object_id": "pad:"
                            + digest([pin["component_uid"], pin["pin_uid"], index]),
                            "raw_sha256": digest(pin),
                            "kind": "pad",
                            **_intersection(owner, candidate, mask),
                        }
                    )
                continue
            witness, masks = M._pad_region_witness(owner, pin, [], {})
            result["pad_region_witnesses"].append(witness)
            if witness["complete"] is not True:
                raise ValueError("Candidate cannot bind continuous owned pad regions")
            for index, mask in enumerate(masks):
                target_masks[(pin["component_uid"], pin["pin_uid"], index)] = mask
        traces = {row["uid"]: row for row in state["native_trace_material"]}
        for node in snapshot["nodes"]:
            mask = (
                trace_masks[node["trace_uid"]]
                if node["kind"] == "trace"
                else target_masks[(node["component_uid"], node["pin_uid"], node["region_index"])]
            )
            result["target_intersections"].append(
                {
                    "node_id": node["node_id"],
                    "physical_material_sha256": node["physical_material_sha256"],
                    **_intersection(owner, candidate, mask),
                }
            )
        for uid, mask in trace_masks.items():
            trace = traces[uid]
            if trace["net_uid"] != snapshot["target"]["net_uid"]:
                result["foreign_intersections"].append(
                    {
                        "object_id": "trace:" + uid,
                        "raw_sha256": digest(trace),
                        "kind": "trace",
                        **_intersection(owner, candidate, mask),
                    }
                )
        outline = result["before_inputs"]["outline"]["contour"]
        board_mask = owner.contour(outline)
        outline_plan = _outline_plan(outline)
        outline_equality = (
            M._rounded_witness(owner, outline_plan, board_mask)
            if outline_plan["kind"] == "native_equivalent_rounded_rectangle"
            else None
        )
        envelope = _envelope(outline)
        outside = owner.boolean(0, owner.contour(envelope), board_mask)
        result["board_containment"] = {
            "candidate_minus_board": owner.area(owner.boolean(0, raw_candidate, board_mask)),
            "candidate_vs_exterior": _intersection(owner, raw_candidate, outside),
            "outline_sha256": digest(outline),
            "exterior_envelope": envelope,
            "outline_topology": outline_plan,
            "outline_native_equality": outline_equality,
        }
        result["budget"] = {
            "temporary_masks": len(owner.owned),
            "mask_points": owner.points["points"],
            "comparisons": owner.region_comparisons,
        }
        result["complete"] = True
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
        try:
            if result["before_inputs"] is not None:
                result["after_cleanup_inputs"] = _fresh(doc, a, state, snapshot)
        except Exception as error:
            result["errors"].append({"cause": type(error).__name__, "reason": str(error)})
    proof = verify(state, snapshot, connection, result)
    result["valid"] = proof["valid"]
    if not result["valid"]:
        result["reason"] = proof["reason"]
    return result


def verify(state, snapshot, connection, candidate):
    """Pure completeness/binding verifier; caller flags cannot replace measurements."""
    try:
        if (
            candidate["source"] != SOURCE
            or candidate["unit"] != "mm"
            or candidate["complete"] is not True
            or candidate["errors"] != []
            or candidate["binding"] != _binding(state, snapshot, connection)
            or candidate["mask_touch_formal_source"] != M.TOUCH_FORMAL_SOURCE
            or candidate["mask_touch_controls"] != state["native_mask_proof"]["mask_touch_controls"]
        ):
            raise ValueError(
                "Complete fresh candidate sources, controls and raw inputs are required"
            )
        inputs = candidate["before_inputs"]
        if (
            inputs != candidate["after_cleanup_inputs"]
            or inputs["pin_material_sha256"] != digest(state["all_pin_pad_material"])
            or inputs["raw_trace_sha256"] != digest(state["native_trace_material"])
        ):
            raise ValueError(
                "Raw material inputs changed during candidate qualification or cleanup"
            )
        outline = inputs["outline"]["contour"]
        outline_plan = _outline_plan(outline)
        expected_outline = {
            "kind": "outline",
            "path": [list(p) for p in zip(*outline["points_array_mm"], strict=True)],
        }
        if (
            type(inputs["outline"]["inner_contour_count"]) is not int
            or inputs["outline"]["inner_contour_count"] != 0
            or _canonical(state["geometry"]["model"]["outline"]) != _canonical([expected_outline])
        ):
            raise ValueError("Candidate board outline does not bind the complete captured boundary")
        if not 0 < _area(candidate["candidate_area"]) <= _area(candidate["candidate_raw_area"]):
            raise ValueError("Candidate material must have positive native area")
        rows = candidate["target_intersections"]
        nodes = {row["node_id"]: row for row in snapshot["nodes"]}
        if len(rows) != len(nodes) or {row["node_id"] for row in rows} != set(nodes):
            raise ValueError("Candidate target-node matrix is incomplete or duplicated")
        by_node = {row["node_id"]: row for row in rows}
        selected = {connection[key]["component_id"] for key in ("first", "second")}
        touched = set()
        for group in snapshot["components"]:
            for node_id in group["node_ids"]:
                row = by_node[node_id]
                if row["physical_material_sha256"] != nodes[node_id]["physical_material_sha256"]:
                    raise ValueError("Candidate target intersection has different physical inputs")
                if (
                    type(row["boundary_touch"]) is not bool
                    or row["boundary_touch_source"] != "IMask.Touch151"
                ):
                    raise ValueError(
                        "Every candidate target pair requires an actual native Touch reply"
                    )
                area = _area(row)
                if area > 0:
                    touched.add(group["component_id"])
                elif group["component_id"] not in selected and not _separate(row):
                    raise ValueError("Candidate touches an unselected target component boundary")
        if touched != selected:
            raise ValueError(
                "Candidate must positively contact exactly the selected two components"
            )
        for key in ("first", "second"):
            node_id = connection[key].get("node_id")
            if node_id is not None and _area(by_node[node_id]) <= 0:
                raise ValueError("Candidate must positively contact each explicitly selected node")
        expected = _expected_foreign(state, snapshot["target"])
        rows = candidate["foreign_intersections"]

        def keys(row):
            return row["object_id"], row["raw_sha256"], row["kind"]

        if (
            len(rows) != len(expected)
            or {keys(row) for row in rows} != {keys(row) for row in expected}
            or not all(_separate(row) for row in rows)
        ):
            raise ValueError(
                "Complete candidate foreign-material separation including Touch is required"
            )
        expected = _voids(state)
        rows = candidate["void_intersections"]
        if (
            len(rows) != len(expected)
            or [
                {key: row[key] for key in actual}
                for row, actual in zip(rows, expected, strict=True)
            ]
            != expected
        ):
            raise ValueError("Candidate requires every physical drill and pad cutout observation")
        holes = M._round_holes(state["all_pin_pad_material"])
        intersections = []
        for row in rows:
            raw = row["raw_intersection"]
            raw_area = _area(raw)
            if any(
                type(value["boundary_touch"]) is not bool
                or value["boundary_touch_source"] != "IMask.Touch151"
                for value in (raw, row)
            ):
                raise ValueError(
                    "Raw and physical drill/cutout pairs require actual native Touch observations"
                )
            if row["void_id"].startswith("drill:"):
                intersections.append({"hole_index": len(intersections), **raw})
            if raw_area > 0:
                crop = _terminal_crop(
                    candidate["binding"]["candidate"], row, candidate["binding"]["anchors"]
                )
                if row["terminal_crop"] != crop or _area(row) != 0:
                    raise ValueError(
                        "Actual terminal drill crop must be bound and fully subtracted"
                    )
            elif row["terminal_crop"] is not None or not _separate(raw) or not _separate(row):
                raise ValueError("Foreign/internal drill and all cutout contacts remain forbidden")
        if candidate["terminal_crop_pairs"] != M._terminal_crop_pairs(
            candidate["binding"]["candidate"], holes, intersections
        ):
            raise ValueError("Candidate terminal crops do not prove continuous residual material")
        if (
            any(row["terminal_crop"] is not None for row in rows)
            and candidate["candidate_area"]["shape_count"] != 1
        ):
            raise ValueError(
                "Native terminal-cropped candidate does not retain one witnessed material shape"
            )
        witnesses = candidate["pad_region_witnesses"]
        baseline = state["native_mask_proof"]["target_pad_regions"]

        def strip_contacts(row):
            return {key: value for key, value in row.items() if key != "contacts"}

        if [strip_contacts(row) for row in witnesses] != [strip_contacts(row) for row in baseline]:
            raise ValueError(
                "Candidate owned pad regions differ from the complete witnessed topology"
            )
        board = candidate["board_containment"]
        if board["outline_topology"] != outline_plan:
            raise ValueError("Actual board outline topology is not bound")
        equality = board["outline_native_equality"]
        if outline_plan["kind"] == "native_equivalent_rounded_rectangle":
            if (
                _area(equality["actual_minus_candidate"]) != 0
                or _area(equality["candidate_minus_actual"]) != 0
            ):
                raise ValueError(
                    "Actual native rounded board equality must hold in both directions"
                )
        elif equality is not None:
            raise ValueError("Straight outline cannot claim an unrelated native rounded witness")
        envelope = board["exterior_envelope"]
        M._simple_polygon(envelope)
        if (
            board["outline_sha256"] != digest(outline)
            or envelope != _envelope(outline)
            or _area(board["candidate_minus_board"]) != 0
            or not _separate(board["candidate_vs_exterior"])
        ):
            raise ValueError(
                "Candidate full-width material is not strictly inside the simple board"
            )
        budget, cleanup, counts = (
            candidate["budget"],
            candidate["cleanup"],
            candidate["temporary_masks_count"],
        )
        allocated = budget["temporary_masks"]
        comparisons = (
            len(candidate["target_intersections"])
            + len(candidate["foreign_intersections"])
            + 2 * len(candidate["void_intersections"])
            + 1
        )
        comparisons += sum(len(row["region_intersections"]) for row in witnesses)
        if (
            type(allocated) is not int
            or not 0 < allocated <= M.MAX_MASKS
            or type(budget["mask_points"]) is not int
            or not 0 < budget["mask_points"] <= M.MAX_POINTS
            or type(budget["comparisons"]) is not int
            or not 0 < budget["comparisons"] == comparisons <= M.MAX_COMPARISONS
            or len(cleanup) != allocated
            or [row["owned_allocation_index"] for row in cleanup]
            != list(range(allocated - 1, -1, -1))
            or not all(row["delete_returned"] is True and "error" not in row for row in cleanup)
            or type(counts["before"]) is not int
            or counts["before"] < 0
            or type(counts["after"]) is not int
            or counts["before"] != counts["after"]
        ):
            raise ValueError("Candidate scratch-mask budgets or individual cleanup are incomplete")
        return {"valid": True, "source": SOURCE}
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as error:
        return {"valid": False, "reason": str(error)}


def fingerprint_basis(candidate):
    """Keep physical measurements/raw bindings; exclude only temporary allocation identity."""
    return {
        key: copy.deepcopy(value)
        for key, value in candidate.items()
        if key not in {"cleanup", "temporary_masks_count"}
    }
