"""Bounded, complete observation of the existing rounded-rectangle outline edit.

Document.BoardOutline, RouteBorder and ManufacturingOutline and their Geometry
getters are declared by the installed 151.0 type library. Observation never
opens/switches a document. Missing getters are unsupported evidence, never an
empty successful contour. This module does not validate fabrication rules.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from pathlib import Path

GUARD = "complete-native-outline-v1"
OUTLINE_MEMBERS = {
    "board_outline": "BoardOutline",
    "route_border": "RouteBorder",
    "manufacturing_outline": "ManufacturingOutline",
}
MAX_CONTOUR_POINTS = 128
MAX_NETS = 8192
MAX_STACK_LAYERS = 256
UNIT_SCALE_MM = 0.0254
INSET_MM = 0.3
TOLERANCE_MM = 0.000001


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("An outline coordinate is not finite")
    return float(value)


def _array(value):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(not isinstance(row, (list, tuple)) for row in value)
        or len({len(row) for row in value}) != 1
        or not 4 <= len(value[0]) <= MAX_CONTOUR_POINTS
    ):
        raise ValueError("Contour x/y/signed-radius rows are malformed or exceed128 points")
    return [[_number(v) for v in row] for row in value]


def _same_points(left, right):
    try:
        one, two = _array(left), _array(right)
    except (ValueError, TypeError):
        return False
    return len(one[0]) == len(two[0]) and all(
        abs(a - b) <= TOLERANCE_MM
        for row_a, row_b in zip(one, two, strict=True)
        for a, b in zip(row_a, row_b, strict=True)
    )


def _closed(points):
    return all(abs(row[0] - row[-1]) <= TOLERANCE_MM for row in points)


def _shape(record):
    """Material/shape acceptance does not depend on replacement-object UID."""
    return {k: copy.deepcopy(record[k]) for k in ("points_mm", "cutout_count", "closed")}


def _same_shape(left, right):
    return (
        isinstance(left, dict)
        and isinstance(right, dict)
        and left.get("closed") is True
        and right.get("closed") is True
        and left.get("cutout_count") == right.get("cutout_count") == 0
        and _same_points(left.get("points_mm"), right.get("points_mm"))
    )


def _valid_outlines(value):
    if (
        not isinstance(value, dict)
        or value.get("complete") is not True
        or value.get("errors") != []
        or not isinstance(value.get("objects"), dict)
        or set(value["objects"]) != set(OUTLINE_MEMBERS)
    ):
        return False
    for record in value["objects"].values():
        if not isinstance(record, dict):
            return False
        try:
            raw = _array(record.get("points_mil"))
            points = _array(record.get("points_mm"))
        except (ValueError, TypeError):
            return False
        if (
            record.get("cutout_count") != 0
            or type(record.get("cutout_count")) is not int
            or record.get("closed") is not True
            or not _closed(points)
            or not _same_points(points, [[v * UNIT_SCALE_MM for v in row] for row in raw])
        ):
            return False
    return True


def _rounded(x0, y0, x1, y1, radius):
    if radius == 0:
        return [[x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], [0.0] * 5]
    points = [
        [x1 - radius, y0, 0],
        [x1 - radius, y0 + radius, -radius],
        [x1, y0 + radius, 0],
        [x1, y1 - radius, 0],
        [x1 - radius, y1 - radius, -radius],
        [x1 - radius, y1, 0],
        [x0 + radius, y1, 0],
        [x0 + radius, y1 - radius, -radius],
        [x0, y1 - radius, 0],
        [x0, y0 + radius, 0],
        [x0 + radius, y0 + radius, -radius],
        [x0 + radius, y0, 0],
        [x1 - radius, y0, 0],
    ]
    return [list(row) for row in zip(*points, strict=True)]


def expectation(options, before_outlines):
    """Return the exact supported target or reject incomplete pre-write evidence.

    No-op means all THREE fully observed contours already equal the requested
    closed signed-arc representation. It dispatches no setters or lifecycle
    calls, and claims unchanged persistence, rather than a new save/reopen.
    """
    if not _valid_outlines(before_outlines):
        raise ValueError("All three complete contour getters are required before outline writing")
    width = _number(options.get("width"))
    height = _number(options.get("height"))
    radius = _number(0.0 if options.get("radius") is None else options["radius"])
    if (
        width <= 2 * INSET_MM
        or height <= 2 * INSET_MM
        or radius < 0
        or radius >= min(width, height) / 2
    ):
        raise ValueError("Outline and inset companion rectangles must have positive legal extents")
    main = _rounded(0.0, 0.0, width, height, radius)
    target = {
        "board_outline": {"points_mm": main, "cutout_count": 0, "closed": True},
        "route_border": {
            "points_mm": _rounded(
                INSET_MM,
                INSET_MM,
                width - INSET_MM,
                height - INSET_MM,
                max(radius - INSET_MM, 0.0),
            ),
            "cutout_count": 0,
            "closed": True,
        },
        "manufacturing_outline": {
            "points_mm": copy.deepcopy(main),
            "cutout_count": 0,
            "closed": True,
        },
    }
    return {
        "guard": GUARD,
        "requested": {"width": width, "height": height, "radius": radius},
        "before_outlines_sha256": digest(before_outlines),
        "target": target,
        "no_op": all(
            _same_shape(before_outlines["objects"][key], target[key]) for key in OUTLINE_MEMBERS
        ),
    }


def _collection(value, a, maximum):
    count = value.Count
    if type(count) is not int or not 0 <= count <= maximum:
        raise ValueError("Native collection Count is unavailable or over the observation bound")
    rows = list(a._items(value, strict=True))
    if len(rows) != count:
        raise ValueError("Native collection enumeration differs from Count")
    return rows


def outlines(doc):
    objects, errors = {}, []
    for key, member in OUTLINE_MEMBERS.items():
        record = {"source": member + ".Geometry.PointsArray/Cutouts.Count"}
        try:
            item = getattr(doc, member)
            if item is None:
                raise ValueError("The native outline object is absent")
            geometry = item.Geometry
            raw_getter = geometry.PointsArray
            record["points_mil"] = copy.deepcopy(raw_getter)
            raw = _array(raw_getter)
            points = [[v * UNIT_SCALE_MM for v in row] for row in raw]
            record.update(points_mil=raw, points_mm=points, closed=_closed(points))
            count = geometry.Cutouts.Count
            record["cutout_count"] = count
            if type(count) is not int or count != 0:
                raise ValueError("Only fully observed contours without cutouts are supported")
            if not _closed(points):
                raise ValueError("The observed contour is not closed")
            objects[key] = record
        except Exception as error:
            if "points_mil" in record:
                objects[key] = record
            errors.append(
                {"object": key, "getter": member, "observation_error": type(error).__name__}
            )
    return {"complete": not errors and len(objects) == 3, "objects": objects, "errors": errors}


def _strict_stackup(doc, a):
    rows = []
    layers = _collection(a._propget(doc, a.LAYOUT_LAYER_STACK, True), a, MAX_STACK_LAYERS)
    if not layers:
        raise ValueError("Native layer stack is empty")
    for layer in layers:
        kind, board_layer, properties = layer.Type, layer.BoardLayer, layer.LayerProperties
        if type(kind) is not int or kind not in a.LAYER_KINDS or type(board_layer) is not int:
            raise ValueError("Layer type/identity is unobserved")
        if properties is None:
            raise ValueError("Native layer properties are unobserved")
        row = {
            "type": kind,
            "board_layer": board_layer,
            "name": properties.StackupLayerName,
            "description": properties.Description,
            "thickness_mm": _number(
                a._propget(properties, a.LAYER_PROPERTIES_THICKNESS, a.UNIT_MM)
            ),
        }
        if not isinstance(row["name"], str) or not isinstance(row["description"], str):
            raise ValueError("Layer textual properties are unobserved")
        if kind == 0:
            row.update(layer=layer.ConductorLayer, usage=properties.LayerUsage)
            if type(row["layer"]) is not int or type(row["usage"]) is not int:
                raise ValueError("Native conductor properties are unobserved")
        if kind == 3:
            row["dielectric_constant"] = _number(properties.DielectricConstant)
        rows.append(row)
    return rows


def _protected(state):
    """Remove only the authorized main-outline contour from a full snapshot."""
    value = copy.deepcopy(state)
    model = value.get("model")
    if not isinstance(model, dict) or not isinstance(model.get("outline"), list):
        raise ValueError("Complete physical model is absent")
    model.pop("outline")
    from .cell_refresh import _canonical

    return _canonical(value)


def _material_complete(value):
    if not isinstance(value, dict):
        return False
    components, model = value.get("components"), value.get("model")
    if not isinstance(components, list) or not isinstance(model, dict):
        return False
    refs, uids = set(), set()
    for comp in components:
        if not isinstance(comp, dict):
            return False
        ref, uid, pins = comp.get("refdes"), comp.get("uid"), comp.get("pins")
        if (
            not isinstance(ref, str)
            or not ref.strip()
            or ref in refs
            or type(uid) not in (str, int)
            or not str(uid).strip()
            or str(uid) in uids
            or not isinstance(pins, list)
            or any(
                not isinstance(comp.get(key), str) or not comp[key]
                for key in ("part_number", "footprint")
            )
            or type(comp.get("placed")) is not bool
            or not isinstance(comp.get("side"), str)
            or comp["side"] not in {"top", "bottom"}
            or any(
                type(comp.get(key)) is not int or comp[key] < 0 for key in ("anchor", "fix_lock")
            )
            or any(
                type(comp.get(key)) not in (int, float) or not math.isfinite(comp[key])
                for key in ("x", "y", "rotation")
            )
            or any(
                not isinstance(comp.get(key), list)
                for key in ("geometry", "placement_outlines", "insertion_outlines")
            )
        ):
            return False
        refs.add(ref)
        uids.add(str(uid))
        numbers = set()
        for pin in pins:
            if not isinstance(pin, dict):
                return False
            number, net = pin.get("number"), pin.get("net")
            if (
                not isinstance(number, str)
                or not number.strip()
                or number in numbers
                or "net" not in pin
                or (net is not None and not isinstance(net, str))
                or any(
                    type(pin.get(key)) not in (int, float) or not math.isfinite(pin[key])
                    for key in ("x", "y")
                )
            ):
                return False
            numbers.add(number)
        geometry = comp["geometry"]
        if len(geometry) != len(pins):
            return False
        physical = {}
        for pin in geometry:
            if (
                not isinstance(pin, dict)
                or not isinstance(pin.get("number"), str)
                or pin["number"] not in numbers
                or pin["number"] in physical
                or "net" not in pin
                or any(
                    not isinstance(pin.get(key), list) for key in ("copper", "fabrication", "holes")
                )
                or not isinstance(pin.get("center_mm"), list)
                or len(pin["center_mm"]) != 2
                or any(
                    type(v) not in (int, float) or not math.isfinite(v) for v in pin["center_mm"]
                )
            ):
                return False
            physical[pin["number"]] = pin
        if any(
            physical[pin["number"]]["net"] != pin["net"]
            or physical[pin["number"]]["center_mm"] != [pin["x"], pin["y"]]
            for pin in pins
        ):
            return False
    required = {
        "layers",
        "outline",
        "pads",
        "vias",
        "traces",
        "planes",
        "holes",
        "via_holes",
        "slots",
        "silk",
        "texts",
    }
    if (
        not required.issubset(model)
        or type(model.get("layers")) is not int
        or model["layers"] <= 0
        or any(not isinstance(model[key], list) for key in required - {"layers"})
    ):
        return False
    traces = model["traces"]
    if any(
        not isinstance(row, dict)
        or type(row.get("uid")) not in (str, int)
        or not str(row["uid"]).strip()
        for row in traces
    ):
        return False
    trace_ids = [str(row["uid"]) for row in traces]
    return (
        len(trace_ids) == len(traces)
        and all(trace_ids)
        and len(trace_ids) == len(set(trace_ids))
        and isinstance(value.get("stackup"), list)
        and bool(value["stackup"])
    )


def _nets_complete(rows):
    if not isinstance(rows, list):
        return False
    names, ids = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            return False
        name, uid = row.get("name"), row.get("uid")
        if (
            not isinstance(name, str)
            or not name.strip()
            or name in names
            or type(uid) not in (str, int)
            or not str(uid).strip()
            or str(uid) in ids
        ):
            return False
        names.add(name)
        ids.add(str(uid))
    return True


def capture(doc, a):
    from .cell_refresh import capture as material_capture

    contour = outlines(doc)
    if not _valid_outlines(contour):
        # No empty substitution and no expensive full material scan after a
        # known missing mandatory getter. Raw successful contours stay visible.
        return {"outlines": contour, "protection": None, "nets": None, "stackup": None}
    material = material_capture(doc, a, strict=True)
    if not _material_complete(material):
        raise ValueError("Complete unique component/pin/trace material is required")
    model_outline = material["model"]["outline"]
    expected_path = [
        list(row) for row in zip(*contour["objects"]["board_outline"]["points_mm"], strict=True)
    ]
    if (
        len(model_outline) != 1
        or model_outline[0].get("kind") != "outline"
        or not _same_points(
            [list(row) for row in zip(*model_outline[0].get("path", []), strict=True)],
            [list(row) for row in zip(*expected_path, strict=True)],
        )
        or outlines(doc) != contour
    ):
        raise ValueError("Independent main-contour getters differ during material observation")
    nets = []
    for net in _collection(doc.Nets, a, MAX_NETS):
        name, uid = net.Name, net.UniqueId
        if not isinstance(name, str) or not name or type(uid) not in (str, int) or not str(uid):
            raise ValueError("Native net identity is incomplete")
        nets.append({"name": name, "uid": str(uid)})
    if len({row["name"] for row in nets}) != len(nets) or len({row["uid"] for row in nets}) != len(
        nets
    ):
        raise ValueError("Native net identity is ambiguous")
    return {
        "outlines": contour,
        "protection": material,
        "nets": sorted(nets, key=lambda row: (row["name"], row["uid"])),
        "stackup": _strict_stackup(doc, a),
    }


def _identity(app, board):
    from .native_identity import application_identity

    pid = application_identity(app, "pcb").get("pid")
    if type(pid) is not int or pid <= 0:
        raise ValueError("Outline application PID is unobserved")
    return {"pid": pid, "pcb": str(board.resolve())}


def observe(params, client, a):
    from .cell_refresh import active

    if (
        params.get("observe_only") is not True
        or params.get("start") is not False
        or type(params.get("pid")) is not int
        or params["pid"] <= 0
    ):
        raise a.AdapterError(
            "E_VALIDATION",
            "Outline observation requires observe_only, start=false and bound PID",
            {"write_attempted": False},
        )
    _, board, app, doc = active(params, client, a, require_project=False, quiet=False)
    before = _identity(app, board)
    if before["pid"] != params["pid"]:
        raise a.AdapterError(
            "E_CONFLICT", "Outline observer belongs to another PID", {"write_attempted": False}
        )
    try:
        state = a._native_call("outline.observe.readonly", lambda: capture(doc, a))
    except Exception as error:
        raise a.AdapterError(
            "E_VERIFY_INCOMPLETE",
            "Full native outline/material observation is incomplete",
            {"write_attempted": False, "cause": type(error).__name__},
        ) from error
    if (
        _identity(app, board) != before
        or Path(str(a._licensed_document(app).FullName)).resolve() != board
    ):
        raise a.AdapterError(
            "E_CONFLICT", "Outline observer identity changed", {"write_attempted": False}
        )
    return {
        "pcb": str(board),
        "identity": before,
        **state,
        "state_guard": GUARD,
        "state_digest": digest({"identity": before, **state}),
        "write_attempted": False,
        "complete": _valid_outlines(state["outlines"]),
        "_untrusted": ["pcb", "identity", "outlines", "protection", "nets", "stackup"],
    }


def _facts(value):
    if isinstance(value, dict) and isinstance(value.get("outline_state"), dict):
        return value["outline_state"]
    return value if isinstance(value, dict) else {}


def verify(beforefacts, afterfacts, result, expected):
    """Separate exact native shape, non-target protection, and lifecycle evidence."""
    before, after = _facts(beforefacts), _facts(afterfacts)
    result = result if isinstance(result, dict) else {}
    expected = expected if isinstance(expected, dict) else {}
    checks = []

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})

    old, new = before.get("outlines"), after.get("outlines")
    complete = _valid_outlines(old) and _valid_outlines(new)
    check("complete raw main and both companion contour getters", complete)
    try:
        requested = expectation(expected.get("requested", {}), old)
        bound = expected == requested
    except (ValueError, TypeError, AttributeError):
        bound = False
    check("exact requested outline expectation bound to before contours", bound)
    target = expected.get("target", {}) if isinstance(expected, dict) else {}
    for key in OUTLINE_MEMBERS:
        check(
            key + " complete closed coordinates and signed quarter arcs",
            complete and _same_shape(new["objects"][key], target.get(key)),
        )
    identity = before.get("identity")
    check(
        "same positive owned process and exact board",
        isinstance(identity, dict)
        and type(identity.get("pid")) is int
        and identity["pid"] > 0
        and isinstance(identity.get("pcb"), str)
        and bool(identity["pcb"])
        and identity == after.get("identity"),
    )
    try:
        material_ok = (
            _material_complete(before.get("protection"))
            and _material_complete(after.get("protection"))
            and digest(_protected(before["protection"])) == digest(_protected(after["protection"]))
            and _nets_complete(before.get("nets"))
            and _nets_complete(after.get("nets"))
            and before["nets"] == after.get("nets")
            and isinstance(before.get("stackup"), list)
            and bool(before["stackup"])
            and before["stackup"] == after.get("stackup")
        )
    except (ValueError, TypeError, KeyError):
        material_ok = False
    check("all components pins nets old copper and strict stackup protected", material_ok)
    lifecycle = result.get("lifecycle", {}) if isinstance(result, dict) else {}
    if bound and expected["no_op"]:
        check(
            "unchanged no-op with zero native writes and zero lifecycle dispatch",
            result.get("write_attempted") is False
            and result.get("applied") is False
            and result.get("saved") is False
            and result.get("change_status") == "not_applied"
            and result.get("persistence_state") == "unchanged"
            and result.get("native_calls_returned") == []
            and lifecycle == {},
        )
        persistence = False
    else:
        names = [row.get("call") for row in result.get("native_calls_returned", [])]
        check(
            "exactly one returned setter per outline in order",
            names == ["PutBoardOutline", "PutRouteBorder", "PutManufacturingOutline"]
            and all(row.get("returned") is True for row in result.get("native_calls_returned", [])),
        )
        persistence = (
            isinstance(lifecycle, dict)
            and all(
                type(lifecycle.get(key)) is int
                for key in ("save_count", "close_count", "open_count")
            )
            and lifecycle.get("save_count")
            == lifecycle.get("close_count")
            == lifecycle.get("open_count")
            == 1
            and lifecycle.get("save_reply_received") is True
            and lifecycle.get("save_returned") is True
            and lifecycle.get("save_return_type") == "bool"
            and lifecycle.get("save_acknowledged") is True
            and lifecycle.get("close_reply_received") is True
            and lifecycle.get("close_returned") is True
            and lifecycle.get("close_return_type") == "bool"
            and lifecycle.get("close_acknowledged") is True
            and lifecycle.get("closed_observed") is True
            and lifecycle.get("open_reply_received") is True
            and lifecycle.get("reopen_verified") is True
            and identity is not None
            and lifecycle.get("pid_before") == lifecycle.get("pid_after") == identity.get("pid")
            and lifecycle.get("board") == lifecycle.get("reopened_path") == identity.get("pcb")
        )
        check("strict BOOL Save Close empty-document same-PID exact reopen", persistence)
    valid = all(row["passed"] for row in checks)
    return {
        "valid": valid,
        "persistence_verified": bool(valid and persistence),
        "checks": checks,
        "source": "fresh complete native outline/material getters; lifecycle separately checked",
        "not_checked": ["Full rules and BatchDRC", "Manufacturing qualification"],
    }


def execute(params, client, a):
    """One existing outline transaction, no retries, no repair and no GUI writes."""
    from .cell_refresh import active

    started = time.monotonic()
    apply = params.get("apply", False)
    if type(apply) is not bool:
        raise a.AdapterError("E_VALIDATION", "apply must be a boolean", {"write_attempted": False})
    try:
        _, board, app, doc = active(params, client, a, require_project=False, quiet=False)
        identity = _identity(app, board)
        before = a._native_call("outline.observe.before", lambda: capture(doc, a))
        expected = expectation(params, before["outlines"])
        state_digest = digest({"identity": identity, **before})
    except Exception as error:
        raise a.AdapterError(
            getattr(error, "code", "E_UNSUPPORTED"),
            "Complete native outline preflight is unavailable; no write dispatched",
            {
                "stage": "outline.preflight",
                "write_attempted": False,
                "cause": type(error).__name__,
                **(getattr(error, "details", {}) or {}),
            },
        ) from error
    result = {
        "pcb": str(board),
        "unit": "mm",
        "before": before,
        "identity": identity,
        "requested": expected["requested"],
        "outline_plan": expected,
        "state_guard": GUARD,
        "state_digest": state_digest,
        "applied": False,
        "saved": False,
        "write_attempted": False,
        "change_status": "not_applied",
        "persistence_state": "unchanged",
        "persistence_verified": False,
        "native_calls_returned": [],
        "lifecycle": {},
        "prompts": [],
        "timing_seconds": {"preflight": time.monotonic() - started},
        "_untrusted": ["pcb", "before", "identity", "requested", "outline_plan", "prompts"],
    }
    if not apply:
        return result
    if params.get("state_guard") != GUARD or params.get("state_digest") != state_digest:
        raise a.AdapterError(
            "E_CONFLICT",
            "Full outline/material state changed after preview",
            {"stage": "outline.precondition", "write_attempted": False},
        )
    if expected["no_op"]:
        result["no_change"] = True
        result["after"] = copy.deepcopy(before)
        result["verification"] = verify(
            {"identity": identity, **before}, {"identity": identity, **before}, result, expected
        )
        return result
    stage = "outline.put"
    try:
        write_started = time.monotonic()
        for key, member in (
            ("board_outline", "PutBoardOutline"),
            ("route_border", "PutRouteBorder"),
            ("manufacturing_outline", "PutManufacturingOutline"),
        ):
            rows = expected["target"][key]["points_mm"]
            event = {"call": member, "dispatched": True, "returned": False}
            result["native_calls_returned"].append(event)
            result.update(
                write_attempted=True, change_status="unknown", persistence_state="unknown"
            )
            returned = a._native_call(
                "outline." + member,
                lambda member=member, rows=rows: (
                    getattr(doc, member)(len(rows[0]), rows, a.UNIT_MM)
                    if member == "PutManufacturingOutline"
                    else getattr(doc, member)(len(rows[0]), rows, 0.15, a.UNIT_MM)
                ),
            )
            event.update(returned=True, return_type=type(returned).__name__)
        result["timing_seconds"]["setters"] = time.monotonic() - write_started
        stage = "outline.in_memory_verification"
        # Do not save known wrong contours or silently accept partial companions.
        current = a._native_call("outline.observe.before_save", lambda: capture(doc, a))
        result["state_after_setters"] = current
        if (
            not _valid_outlines(current["outlines"])
            or any(
                not _same_shape(current["outlines"]["objects"][key], expected["target"][key])
                for key in OUTLINE_MEMBERS
            )
            or (
                digest(_protected(current["protection"]))
                != digest(_protected(before["protection"]))
                or current["nets"] != before["nets"]
                or current["stackup"] != before["stackup"]
                or _identity(app, board) != identity
                or Path(str(a._licensed_document(app).FullName)).resolve() != board
            )
        ):
            raise ValueError("Native outline or companion setters did not produce the full target")
        stage = "outline.save_close_reopen"
        lifecycle_started = time.monotonic()
        lifecycle = result["lifecycle"] = {
            "save_count": 0,
            "close_count": 0,
            "open_count": 0,
            "pid_before": identity["pid"],
            "board": str(board),
            "reopen_verified": False,
        }
        a._save_close_board(app, board, lifecycle=lifecycle)
        lifecycle["open_count"] += 1
        lifecycle["open_reply_received"] = False
        reopened = a._open_layout_document(app, board)
        lifecycle["open_reply_received"] = True
        if not isinstance(reopened, tuple) or len(reopened) != 2 or reopened[0] is None:
            raise ValueError("Normal reopen did not return a document")
        doc = a._licensed_document(app)
        after_identity = _identity(app, board)
        lifecycle.update(
            pid_after=after_identity["pid"], reopened_path=str(Path(str(doc.FullName)).resolve())
        )
        if after_identity != identity or lifecycle["reopened_path"] != str(board):
            raise ValueError("Reopened board/PID differs from the bound outline target")
        lifecycle["reopen_verified"] = True
        result["prompts"] = reopened[1]
        result["timing_seconds"]["save_close_reopen"] = time.monotonic() - lifecycle_started
        stage = "outline.reopened_observation"
        read_started = time.monotonic()
        after = a._native_call("outline.observe.reopened", lambda: capture(doc, a))
        result["after"] = after
        result["verification"] = verify(
            {"identity": identity, **before},
            {"identity": after_identity, **after},
            result,
            expected,
        )
        result["timing_seconds"]["reopened_observation"] = time.monotonic() - read_started
        if not result["verification"]["valid"]:
            raise ValueError("Fresh reopened native outline/material acceptance is incomplete")
        result.update(
            applied=True,
            saved=True,
            change_status="applied",
            persistence_state="saved",
            persistence_verified=True,
        )
        result["timing_seconds"]["total"] = time.monotonic() - started
        return result
    except Exception as error:
        result["timing_seconds"]["total"] = time.monotonic() - started
        # Preserve returned lifecycle evidence without a retry/extra save/undo.
        raise a.AdapterError(
            getattr(error, "code", "E_VERIFY_INCOMPLETE"),
            "Outline dispatch/verification stopped; no setter or lifecycle command was replayed",
            {
                "stage": stage,
                "write_attempted": result["write_attempted"],
                "result": result,
                "cause": type(error).__name__,
                "next_action": "Query the original ID; supplement with read-only evidence",
            },
        ) from error
