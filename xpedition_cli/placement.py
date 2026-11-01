"""Pure origin-based planning and driver-mediated placement task execution.

All geometry is expressed in mm in board coordinates (including bottom parts).
Plans describe intent, not electrical correctness or automatic routing repair.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Protocol

from .errors import CLIError

MAX_SELECTION = 200
MAX_STEPS = 32
MAX_INPUT_BYTES = 1_048_576
COORDINATE_LIMIT_MM = 100_000.0
POSITION_TOLERANCE_MM = 1e-6
ANGLE_TOLERANCE_DEG = 1e-6
MAX_BOARD_COMPONENTS = 2048
MAX_BOARD_PINS = 16384
MAX_COMPONENT_PINS = 4096
MAX_BOARD_COPPER_OBJECTS = 16384
MAX_PROTECTION_BYTES = 64 * 1024 * 1024
BATCH_GUARD = "placement-full-board-material-v5"
# These definitions drive validation AND the published input schema.
STEP_FIELDS = {
    "translate": {"dx": "number", "dy": "number"},
    "rotate": {"angle": "number", "origin": "point"},
    "align": {"axis": "axis", "anchor": "refdes"},
    "distribute": {"axis": "axis", "start": "number", "end": "number"},
}


def fail(message: str, field: str, code: str = "E_VALIDATION") -> None:
    raise CLIError(code, message, {"field": field, "_untrusted": ["field"]})


def number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail("expected a finite JSON number", field)
    try:
        result = float(value)
    except (ValueError, OverflowError):
        fail("number is outside the supported range", field)
    if not math.isfinite(result) or abs(result) > COORDINATE_LIMIT_MM:
        fail("number is nonfinite or outside the supported range", field)
    return result


def _keys(value: Any, required: set[str], field: str) -> None:
    if not isinstance(value, dict) or set(value) != required:
        fail("object must contain exactly the declared fields", field)


def _name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        fail("reference designator must be a nonempty string of at most 128 characters", field)
    if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        fail("reference designator contains whitespace padding or control characters", field)
    return value


def validate_request(value: Any) -> dict[str, Any]:
    _keys(value, {"schema_version", "unit", "selection", "steps"}, "request")
    if value["schema_version"] != "1.0" or value["unit"] != "mm":
        fail("placement requests require schema_version 1.0 and unit mm", "request")
    selection = value["selection"]
    if not isinstance(selection, list) or not 1 <= len(selection) <= MAX_SELECTION:
        fail("selection must be an explicit bounded nonempty array", "selection")
    names = [_name(item, f"selection[{i}]") for i, item in enumerate(selection)]
    if len(set(names)) != len(names):
        fail("selection contains duplicate reference designators", "selection")
    steps = value["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        fail("steps must be a bounded nonempty array", "steps")
    normalized = []
    for i, step in enumerate(steps):
        where = f"steps[{i}]"
        op = step.get("op") if isinstance(step, dict) else None
        if not isinstance(op, str) or op not in STEP_FIELDS:
            fail("unsupported placement operation", where)
        fields = STEP_FIELDS[op]
        _keys(step, {"op", *fields}, where)
        result: dict[str, Any] = {"op": op}
        for key, kind in fields.items():
            entry = step[key]
            field = f"{where}.{key}"
            if kind == "number":
                result[key] = number(entry, field)
            elif kind == "point":
                if not isinstance(entry, list) or len(entry) != 2:
                    fail("origin requires [x, y]", field)
                result[key] = [number(x, field) for x in entry]
            elif kind == "axis":
                if entry not in ("x", "y"):
                    fail("axis must be x or y", field)
                result[key] = entry
            else:
                result[key] = _name(entry, field)
                if entry not in names:
                    fail("alignment anchor must belong to the explicit selection", field)
        if op == "distribute" and len(names) < 2:
            fail("distribution needs at least two selected origins", where)
        normalized.append(result)
    return {"schema_version": "1.0", "unit": "mm", "selection": names, "steps": normalized}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            fail("duplicate JSON object key", key)
        value[key] = item
    return value


def read_json(path: str | Path) -> Any:
    try:
        with Path(path).expanduser().open("rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
    except FileNotFoundError as error:
        raise CLIError("E_NOT_FOUND", "placement input file was not found") from error
    except OSError as error:
        raise CLIError("E_IO", "cannot read placement input file") from error
    if len(raw) > MAX_INPUT_BYTES:
        fail("placement input exceeds the size limit", "file")
    try:
        # UTF-8 BOM accepted for files, never produced on stdout.
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise CLIError("E_VALIDATION", "placement input must be valid UTF-8 JSON") from error


def normalize_state(rows: Any, selection: list[str]) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        fail("observation requires a components array", "components")
    selected = set(selection)
    by_name: dict[str, dict[str, Any]] = {}
    identities: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("refdes"), str):
            fail("component observations need a reference designator", "components")
        refdes = row["refdes"]
        if refdes not in selected:
            continue
        if refdes in by_name:
            fail("selected reference designator is ambiguous", refdes, "E_CONFLICT")
        if row.get("unit") != "mm" or row.get("placed") is not True:
            fail(
                "only placed components with explicit millimetre observations are supported", refdes
            )
        if row.get("side") not in ("top", "bottom"):
            fail("component side is missing or unknown", refdes)
        # Protection must be observed, never guessed false. Native binding uses
        # this same strict contract; unsupported observations fail before writes.
        if type(row.get("anchor")) is not int or not 0 <= row["anchor"] <= 0xFFFFFFFF:
            fail("component anchor state must be explicitly observed", refdes)
        if type(row.get("fix_lock")) is not int or not 0 <= row["fix_lock"] <= 0xFFFFFFFF:
            fail("component fix_lock state must be explicitly observed", refdes)
        object_id = _name(row.get("object_id"), refdes + ".object_id")
        if object_id in identities:
            fail("component object identity is ambiguous", refdes, "E_CONFLICT")
        identities.add(object_id)
        by_name[refdes] = {
            "refdes": refdes,
            "x": number(row.get("x"), refdes + ".x"),
            "y": number(row.get("y"), refdes + ".y"),
            "rotation": number(row.get("rotation"), refdes + ".rotation") % 360,
            "side": row["side"],
            "placed": True,
            "unit": "mm",
            "anchor": row["anchor"],
            "fix_lock": row["fix_lock"],
            "object_id": object_id,
        }
    if selected != set(by_name):
        fail("selected components were not all observed", "selection", "E_NOT_FOUND")
    return [by_name[name] for name in selection]


def same_position(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    try:
        for key in ("refdes", "side", "placed", "unit", "anchor", "fix_lock", "object_id"):
            if type(expected[key]) is not type(observed[key]) or expected[key] != observed[key]:
                return False
        for key in ("x", "y"):
            if not math.isclose(
                number(expected[key], key),
                number(observed[key], key),
                rel_tol=0,
                abs_tol=POSITION_TOLERANCE_MM,
            ):
                return False
        delta = (
            number(expected["rotation"], "rotation")
            - number(observed["rotation"], "rotation")
            + 180
        ) % 360 - 180
        return abs(delta) <= ANGLE_TOLERANCE_DEG
    except (KeyError, CLIError):
        return False


def digest(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def plan_placement(
    request: Any, observations: Any, *, protection_state: Any = None
) -> dict[str, Any]:
    request = validate_request(request)
    before = normalize_state(observations, request["selection"])
    after = copy.deepcopy(before)
    for step in request["steps"]:
        op = step["op"]
        if op == "translate":
            for row in after:
                row["x"] += step["dx"]
                row["y"] += step["dy"]
        elif op == "rotate":
            angle = step["angle"] % 360
            sine, cosine = math.sin(math.radians(angle)), math.cos(math.radians(angle))
            ox, oy = step["origin"]
            for row in after:
                dx, dy = row["x"] - ox, row["y"] - oy
                row["x"], row["y"] = ox + dx * cosine - dy * sine, oy + dx * sine + dy * cosine
                row["rotation"] = (row["rotation"] + angle) % 360
        elif op == "align":
            axis = step["axis"]
            coordinate = next(row[axis] for row in after if row["refdes"] == step["anchor"])
            for row in after:
                row[axis] = coordinate
        else:
            spacing = (step["end"] - step["start"]) / (len(after) - 1)
            for i, row in enumerate(after):
                row[step["axis"]] = step["start"] + i * spacing
        for row in after:
            # Guard arithmetic overflow even when each individual input was valid.
            for key in ("x", "y", "rotation"):
                row[key] = round(number(row[key], row["refdes"] + "." + key), 9)
    results = []
    for old, new in zip(before, after, strict=True):
        changed = not same_position(old, new)
        if changed and (old["anchor"] != 0 or old["fix_lock"] != 0):
            fail("the task would move a locked or fixed component", old["refdes"], "E_CONFLICT")
        results.append({"id": old["refdes"], "before": old, "target": new, "changed": changed})
    result = {
        "schema_version": "1.0",
        "unit": "mm",
        "request": request,
        "state_digest": digest(sorted(before, key=lambda row: row["refdes"])),
        "results": results,
        "summary": {
            "selected_count": len(results),
            "changed_count": sum(r["changed"] for r in results),
        },
        "validation": {
            "input": "validated",
            "geometry": "origin_math_only",
            "drc": "not_run",
            "native_smoke": "missing",
        },
        "_untrusted": ["request", "results"],
    }
    if protection_state is not None:
        protected = canonical_state(protection_state, require_semantics=True)
        index = component_index(protected)
        for row in before:
            native = index.get(row["refdes"])
            if native is None or str(native["uid"]) != row["object_id"]:
                fail("placement and material identity differ", row["refdes"], "E_CONFLICT")
            if any(native.get(k) != row[k] for k in ("side", "placed")) or any(
                not math.isclose(native[k], row[k], rel_tol=0, abs_tol=POSITION_TOLERANCE_MM)
                for k in ("x", "y", "rotation")
            ):
                fail("placement and material pose differ", row["refdes"], "E_CONFLICT")
        # Compute the complete expected state before any write; unsupported material
        # transformations are a preview refusal, never a post-write discovery.
        expected_state(protected, result["results"])
        result.update(
            state_guard=BATCH_GUARD,
            protection_before=protected,
            state_digest=digest(
                {"placements": sorted(before, key=lambda r: r["refdes"]), "protection": protected}
            ),
        )
        result["_untrusted"].append("protection_before")
    return result


def canonical_state(state: Any, *, require_semantics: bool = False) -> dict[str, Any]:
    """Validate complete identities and retain every captured physical field."""
    from .cell_refresh import _canonical
    from .placement_text_witness import SEMANTIC_FIELD, WITNESS_FIELD, validate_semantics
    from .placement_text_witness import validate as validate_text_witness

    required = {
        "components",
        "model",
        "stackup",
        WITNESS_FIELD,
    }
    if not isinstance(state, dict) or set(state) not in (required, required | {SEMANTIC_FIELD}):
        fail("complete full-board material state is required", "protection")
    if require_semantics and SEMANTIC_FIELD not in state:
        fail("complete native text semantic witnesses are required", SEMANTIC_FIELD)
    if not isinstance(state["model"], dict) or not isinstance(state["stackup"], list):
        fail("complete model and stackup are required", "protection")
    model = state["model"]
    if type(model.get("layers")) is not int or model["layers"] < 1:
        fail("observed conductor count is required", "model.layers")
    for key in (
        "outline",
        "pads",
        "traces",
        "vias",
        "planes",
        "holes",
        "via_holes",
        "slots",
        "silk",
        "texts",
    ):
        if not isinstance(model.get(key), list):
            fail("complete physical board inventories are required", "model." + key)
    copper_ids = set()
    for key in ("traces", "vias", "planes"):
        if len(model[key]) > MAX_BOARD_COPPER_OBJECTS:
            fail("board copper inventory exceeds the bounded capacity", "model." + key)
        for item in model[key]:
            uid = item.get("uid") if isinstance(item, dict) else None
            if type(uid) not in (int, str) or not str(uid).strip():
                fail(
                    "stable native copper identity is unsupported", "model." + key, "E_UNSUPPORTED"
                )
            if str(uid) in copper_ids:
                fail(
                    "complete unique native copper identities are required",
                    "model." + key,
                    "E_CONFLICT",
                )
            copper_ids.add(str(uid))
    if (
        not isinstance(state["components"], list)
        or not 1 <= len(state["components"]) <= MAX_BOARD_COMPONENTS
    ):
        fail("full-board component observation exceeds the bounded capacity", "components")
    component_index(state)
    validate_text_witness(state)
    if SEMANTIC_FIELD in state:
        validate_semantics(state)
    try:
        raw = json.dumps(state, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise CLIError("E_VALIDATION", "material state must contain finite JSON values") from error
    if len(raw) > MAX_PROTECTION_BYTES:
        fail("full-board material observation exceeds the byte budget", "protection")
    # A second rounding of raw native doubles would recreate the text prediction
    # error. All existing material retains its original canonicalization.
    result = _canonical(
        {key: value for key, value in state.items() if key not in {WITNESS_FIELD, SEMANTIC_FIELD}}
    )
    result[WITNESS_FIELD] = sorted(copy.deepcopy(state[WITNESS_FIELD]), key=lambda row: row["uid"])
    if SEMANTIC_FIELD in state:
        result[SEMANTIC_FIELD] = sorted(
            copy.deepcopy(state[SEMANTIC_FIELD]), key=lambda row: row["uid"]
        )
    return result


def component_index(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed, ids, pin_count = {}, set(), 0
    for row in state["components"]:
        if not isinstance(row, dict):
            fail("invalid component material observation", "components")
        name = _name(row.get("refdes"), "refdes")
        uid = row.get("uid")
        if type(uid) not in (str, int) or not str(uid).strip():
            fail("component material identity is missing", name)
        if name in indexed or str(uid) in ids:
            fail("full-board component identity is ambiguous", name, "E_CONFLICT")
        indexed[name] = row
        ids.add(str(uid))
        for key in ("part_number", "footprint"):
            _name(row.get(key), name + "." + key)
        for key in ("x", "y", "rotation"):
            number(row.get(key), name + "." + key)
        if (
            row.get("unit") != "mm"
            or type(row.get("placed")) is not bool
            or row.get("side") not in ("top", "bottom")
        ):
            fail("full-board component pose is incomplete", name)
        for key in ("pins", "geometry", "placement_outlines", "insertion_outlines"):
            if not isinstance(row.get(key), list):
                fail("full-board component material observation is incomplete", name + "." + key)
        pins = row["pins"]
        if len(pins) > MAX_COMPONENT_PINS:
            fail("component pin observation exceeds bounded capacity", name)
        names = set()
        for pin in pins:
            if not isinstance(pin, dict):
                fail("invalid pin observation", name)
            pin_name = _name(pin.get("number"), name + ".pin")
            if (
                pin_name in names
                or "net" not in pin
                or (pin["net"] is not None and not isinstance(pin["net"], str))
            ):
                fail("pin identity or network observation is ambiguous", name, "E_CONFLICT")
            names.add(pin_name)
            if row["placed"]:
                number(pin.get("x"), name + ".pin.x")
                number(pin.get("y"), name + ".pin.y")
        geometry_names = [pin.get("number") for pin in row["geometry"] if isinstance(pin, dict)]
        if (
            len(geometry_names) != len(row["geometry"])
            or len(set(geometry_names)) != len(geometry_names)
            or set(geometry_names) != names
        ):
            fail("complete one-to-one pin material observation is required", name)
        for material in row["geometry"]:
            pin = next(pin for pin in pins if pin["number"] == material["number"])
            if "net" not in material or material["net"] != pin["net"]:
                fail("pin and physical material networks differ", name, "E_CONFLICT")
            if row["placed"] and (
                not isinstance(material.get("center_mm"), list)
                or len(material["center_mm"]) != 2
                or any(
                    not math.isclose(
                        number(value, name + ".pin_material"),
                        pin[axis],
                        rel_tol=0,
                        abs_tol=POSITION_TOLERANCE_MM,
                    )
                    for value, axis in zip(material["center_mm"], ("x", "y"), strict=True)
                )
            ):
                fail("pin and physical material coordinates differ", name, "E_CONFLICT")
            for key in ("copper", "fabrication", "holes"):
                if not isinstance(material.get(key), list):
                    fail("complete pin copper/fabrication/hole inventories are required", name)
        pin_count += len(pins)
        if pin_count > MAX_BOARD_PINS:
            fail("full-board pin observation exceeds bounded capacity", "pins")
    return indexed


def _transform_material(value: Any, old: dict, target: dict) -> Any:
    """Rigidly transform only declared coordinate fields; all other data survives."""
    angle = math.radians((target["rotation"] - old["rotation"]) % 360)
    cosine, sine = math.cos(angle), math.sin(angle)

    def point(xy, scale=1.0):
        x, y = (
            number(xy[0] * scale, "material.x") - old["x"],
            number(xy[1] * scale, "material.y") - old["y"],
        )
        return [
            (target["x"] + x * cosine - y * sine) / scale,
            (target["y"] + x * sine + y * cosine) / scale,
        ]

    def bounds(box):
        points = [point([x, y]) for x in (box[0], box[2]) for y in (box[1], box[3])]
        return [
            min(p[0] for p in points),
            min(p[1] for p in points),
            max(p[0] for p in points),
            max(p[1] for p in points),
        ]

    if isinstance(value, list):
        return [_transform_material(item, old, target) for item in value]
    if not isinstance(value, dict):
        return value
    transformed = {key: _transform_material(item, old, target) for key, item in value.items()}
    if "x" in value and "y" in value:
        transformed["x"], transformed["y"] = point([value["x"], value["y"]])
    for key in ("center_mm",):
        if key in value:
            transformed[key] = point(value[key])
    for key in ("circle", "circle_mm"):
        if value.get(key) is not None:
            transformed[key] = [*point(value[key]), value[key][2]]
    if "path" in value:
        transformed["path"] = [[*point(p), p[2]] for p in value["path"]]
    if "cutouts" in value:
        transformed["cutouts"] = [[[*point(p), p[2]] for p in path] for path in value["cutouts"]]
    if "points_mil" in value:
        xs, ys, rs = value["points_mil"]
        points = [point(p, 0.0254) for p in zip(xs, ys, strict=True)]
        transformed["points_mil"] = [[p[0] for p in points], [p[1] for p in points], list(rs)]
    if "strokes" in value:
        transformed["strokes"] = [[point(p) for p in stroke] for stroke in value["strokes"]]
    for key in ("bounds_mm", "extents"):
        if key in value:
            transformed[key] = bounds(value[key])
    if "orientation" in value:
        transformed["orientation"] = (
            value["orientation"] + target["rotation"] - old["rotation"]
        ) % 360
    return transformed


def expected_state(before: dict, results: list[dict]) -> dict:
    """Every old trace/via/plane and all non-target material stay protected."""
    state = copy.deepcopy(before)
    from .placement_text_witness import (
        SEMANTIC_FIELD,
        WITNESS_FIELD,
        projection,
        transform,
        transform_semantics,
    )

    witnesses = {row["uid"]: row for row in state[WITNESS_FIELD]}
    semantics = {row["uid"]: row for row in state.get(SEMANTIC_FIELD, [])}
    index = component_index(state)
    for result in results:
        if not result["changed"]:
            continue
        old, target = result["before"], result["target"]
        row = index[result["id"]]
        if row["side"] != target["side"]:
            fail("batch component flips are unsupported", result["id"])
        turns = (target["rotation"] - old["rotation"]) / 90
        if not math.isclose(turns, round(turns), rel_tol=0, abs_tol=1e-8):
            fail(
                "complete native text/material proof currently requires orthogonal batch rotation",
                result["id"],
                "E_UNSUPPORTED",
            )
        selected_holes = [hole for pin in row["geometry"] for hole in pin.get("holes", [])]
        if selected_holes and state["model"].get("slots"):
            fail("selected slotted-hole ownership cannot be proved", result["id"])
        for hole in selected_holes:
            original = [*hole["center_mm"], hole["diameter_mm"]]
            matches = [
                i
                for i, candidate in enumerate(state["model"].get("holes", []))
                if candidate == original
            ]
            if len(matches) != 1:
                fail("selected hole ownership is ambiguous", result["id"])
            moved = _transform_material(hole, old, target)
            state["model"]["holes"][matches[0]] = [*moved["center_mm"], moved["diameter_mm"]]
        moved_row = _transform_material(row, old, target)
        moved_row.update(x=target["x"], y=target["y"], rotation=target["rotation"])
        state["components"][state["components"].index(row)] = moved_row
        index[result["id"]] = moved_row
        for key in ("pads", "silk", "texts"):
            if key in state["model"]:
                state["model"][key] = [
                    _transform_material(item, old, target)
                    if item.get("refdes") == result["id"]
                    else item
                    for item in state["model"][key]
                ]
        for text in state["model"]["texts"]:
            if text.get("refdes") == result["id"]:
                raw = transform(witnesses[text["uid"]], old, target)
                witnesses[text["uid"]] = raw
                text.update(projection(raw))
                if SEMANTIC_FIELD in state:
                    semantics[text["uid"]] = transform_semantics(
                        semantics[text["uid"]], old, target
                    )
    state[WITNESS_FIELD] = list(witnesses.values())
    if SEMANTIC_FIELD in state:
        state[SEMANTIC_FIELD] = list(semantics.values())
    return canonical_state(state)


def _material_equal(expected: Any, actual: Any, key: str | None = None) -> bool:
    # Only positional getter roundoff is tolerated. Widths, radii, layer numbers,
    # identities and every non-coordinate material scalar remain exact.
    if type(expected) is not type(actual) and not (
        type(expected) in (int, float) and type(actual) in (int, float)
    ):
        return False
    if isinstance(expected, dict):
        return expected.keys() == actual.keys() and all(
            _material_equal(v, actual[k], k) for k, v in expected.items()
        )
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return False
        if key in {"center_mm", "bounds_mm", "extents"}:
            return all(_material_equal(x, y, "x") for x, y in zip(expected, actual, strict=True))
        if key in {"circle", "circle_mm"}:
            return len(expected) == 3 and all(
                _material_equal(x, y, "x" if i < 2 else None)
                for i, (x, y) in enumerate(zip(expected, actual, strict=True))
            )
        if key == "points_mil":
            return len(expected) == 3 and all(
                len(x) == len(y)
                and all(
                    _material_equal(xx * 0.0254, yy * 0.0254, "x") if i < 2 else xx == yy
                    for xx, yy in zip(x, y, strict=True)
                )
                for i, (x, y) in enumerate(zip(expected, actual, strict=True))
            )
        if key in {"path", "strokes", "cutouts"}:
            point_rows = key == "path"
            if point_rows:
                return all(
                    len(x) == len(y) == 3
                    and all(
                        _material_equal(xx, yy, "x" if i < 2 else None)
                        for i, (xx, yy) in enumerate(zip(x, y, strict=True))
                    )
                    for x, y in zip(expected, actual, strict=True)
                )
            if key == "strokes":
                return all(
                    len(x) == len(y)
                    and all(
                        len(xx) == len(yy) == 2
                        and all(_material_equal(vx, vy, "x") for vx, vy in zip(xx, yy, strict=True))
                        for xx, yy in zip(x, y, strict=True)
                    )
                    for x, y in zip(expected, actual, strict=True)
                )
            return all(_material_equal(x, y, "path") for x, y in zip(expected, actual, strict=True))
        return all(_material_equal(x, y) for x, y in zip(expected, actual, strict=True))
    if (
        key in {"x", "y", "rotation", "orientation"}
        and type(expected) in (int, float)
        and type(actual) in (int, float)
    ):
        return math.isclose(expected, actual, rel_tol=0, abs_tol=POSITION_TOLERANCE_MM)
    return expected == actual


def _holes_preserved(before: dict, after: dict, expected: dict, selected: set[str]) -> bool:
    old_remaining = copy.deepcopy(before["model"]["holes"])
    new_remaining = copy.deepcopy(after["model"]["holes"])
    old_index, expected_index = component_index(before), component_index(expected)
    for refdes in sorted(selected):
        old_holes = [h for p in old_index[refdes]["geometry"] for h in p["holes"]]
        expected_holes = [h for p in expected_index[refdes]["geometry"] for h in p["holes"]]
        for old, wanted in zip(old_holes, expected_holes, strict=True):
            original = [*old["center_mm"], old["diameter_mm"]]
            matches = [i for i, hole in enumerate(old_remaining) if hole == original]
            wanted_geometry = [*wanted["center_mm"], wanted["diameter_mm"]]
            new_matches = [
                i
                for i, hole in enumerate(new_remaining)
                if _material_equal({"circle": wanted_geometry}, {"circle": hole})
            ]
            if len(matches) != 1 or len(new_matches) != 1:
                return False
            old_remaining.pop(matches[0])
            new_remaining.pop(new_matches[0])
    return old_remaining == new_remaining


def _batch_net_references(state: dict) -> tuple[list, list]:
    """Bind both native pin projections before any comparison-only substitution."""
    connectivity = state["model"]["connectivity"]
    if connectivity.get("complete") is not True or not isinstance(connectivity.get("nets"), list):
        raise ValueError("Complete named network census is required for Net0 reopening")
    headers = []
    for component in state["components"]:
        by_number = {pin["number"]: pin for pin in component["pins"]}
        for material in component["geometry"]:
            pin = by_number[material["number"]]
            if any(material[key] != pin[key] for key in ("uid", "net_uid", "net")):
                raise ValueError("Native pin and geometry reference mirrors differ")
        for pin in component["pins"]:
            headers.append(
                {
                    "component_uid": str(component["uid"]),
                    "refdes": component["refdes"],
                    "pin_uid": pin["uid"],
                    "pin": pin["number"],
                    "net": pin["net"],
                    "net_uid": pin["net_uid"],
                }
            )
    return connectivity["nets"], headers


def _default_reference_uids(state: dict) -> set:
    from .design_capabilities import UNASSIGNED_NET_NAME

    return {
        pin.get("net_uid")
        for component in state["components"]
        for pin in component["pins"]
        if pin.get("net") == UNASSIGNED_NET_NAME
    }


def batch_reopen_lifecycle(lifecycle: dict) -> dict:
    """Translate real native facts; never synthesize a successful lifecycle."""
    from .net0_reopen_identity import validate_lifecycle

    for key in (
        "save_acknowledged",
        "save_reply_received",
        "close_acknowledged",
        "close_reply_received",
        "open_reply_received",
        "reopen_verified",
    ):
        if lifecycle.get(key) is not True:
            raise ValueError("Complete native lifecycle replies are required")
    if lifecycle.get("save_return_type") != "bool" or lifecycle.get("close_return_type") != "bool":
        raise ValueError("Native Save and Close must return Boolean true")
    pid = lifecycle.get("pid_before")
    if (
        type(pid) is not int
        or pid <= 0
        or lifecycle.get("pid_after") != pid
        or not isinstance(lifecycle.get("process_identity_before"), dict)
        or not isinstance(lifecycle.get("process_identity_after"), dict)
        or lifecycle["process_identity_before"].get("pid") != pid
        or lifecycle["process_identity_after"].get("pid") != pid
    ):
        raise ValueError("Native process generation is not bound to the reopened application")
    return validate_lifecycle(
        {
            **{key: lifecycle[key] for key in ("save_count", "close_count", "open_count")},
            "save_returned": lifecycle.get("save_returned"),
            "close_returned": lifecycle.get("close_returned"),
            "closed_observed": lifecycle.get("closed_observed"),
            "open_returned_document": lifecycle.get("reopen_verified"),
            "expected_path": lifecycle.get("board"),
            "reopened_path": lifecycle.get("reopened_path"),
            "process_before": lifecycle.get("process_identity_before"),
            "process_after": lifecycle.get("process_identity_after"),
        }
    )


def _batch_reference_projection(state: dict, proof: dict, side: str) -> dict:
    from .net0_reopen_identity import pin_reference_projector

    result = copy.deepcopy(state)
    # Requiring both complete mirrors prevents an arbitrary default-ID search and
    # replace from touching unrelated object identities or hiding malformed input.
    _batch_net_references(state)
    project = pin_reference_projector(proof, side)
    for component in result["components"]:
        for collection in ("pins", "geometry"):
            for pin in component[collection]:
                header = {
                    "component_uid": str(component["uid"]),
                    "refdes": component["refdes"],
                    "pin_uid": pin["uid"],
                    "pin": pin["number"],
                    "net": pin["net"],
                    "net_uid": pin["net_uid"],
                }
                pin["net_uid"] = project(header)["net_uid"]
    return result


def batch_reopen_comparison(before: Any, after: Any, lifecycle: dict) -> dict:
    """Compare complete post-edit/reopened raw frames under a narrow Net0 proof."""
    from .net0_reopen_identity import prove_net0_reopen

    old, new = canonical_state(before), canonical_state(after)
    view, proof = new, None
    if _default_reference_uids(old) != _default_reference_uids(new):
        old_nets, old_pins = _batch_net_references(old)
        new_nets, new_pins = _batch_net_references(new)
        proof = prove_net0_reopen(
            old_nets,
            old_pins,
            new_nets,
            new_pins,
            lifecycle=batch_reopen_lifecycle(lifecycle),
        )
        _batch_reference_projection(old, proof, "before")
        view = _batch_reference_projection(new, proof, "after")
    return {
        "valid": old == view,
        "reference_equivalence": proof,
        "raw_before_sha256": digest(old),
        "raw_after_sha256": digest(new),
        "comparison_after_sha256": digest(view),
        "raw_observations_unchanged": True,
    }


def verify_batch_state(plan: dict, observed: Any, *, reopen_proof: dict | None = None) -> dict:
    from .placement_silk_equivalence import DRAFT_GUARD
    from .placement_silk_equivalence import compare as compare_silk_draft
    from .placement_text_witness import (
        SEMANTIC_FIELD,
        WITNESS_FIELD,
        projection,
        semantic_difference,
    )
    from .placement_text_witness import difference as text_difference

    try:
        draft = plan.get("state_guard") == DRAFT_GUARD
        require_semantics = plan.get("state_guard") in {BATCH_GUARD, DRAFT_GUARD}
        before = canonical_state(plan["protection_before"], require_semantics=require_semantics)
        after = canonical_state(observed, require_semantics=require_semantics)
        if reopen_proof is not None:
            from .net0_reopen_identity import prove_net0_reopen

            if plan["summary"]["changed_count"] == 0:
                raise ValueError("An unchanged batch cannot claim a reopened-reference exception")
            old_nets, old_pins = _batch_net_references(before)
            new_nets, new_pins = _batch_net_references(after)
            recomputed = prove_net0_reopen(
                old_nets, old_pins, new_nets, new_pins, lifecycle=reopen_proof["lifecycle"]
            )
            if recomputed != reopen_proof:
                raise ValueError("Reopened Net0 reference proof does not recompute")
            after = _batch_reference_projection(after, recomputed, "after")
        expected = expected_state(before, plan["results"])
        old_index, new_index, expected_index = (
            component_index(before),
            component_index(after),
            component_index(expected),
        )
        selected = {row["id"] for row in plan["results"] if row["changed"]}
        engineering = None
        raw_difference = text_difference(expected[WITNESS_FIELD], after[WITNESS_FIELD], selected)
        semantic_diff = None
        if SEMANTIC_FIELD in before:
            if SEMANTIC_FIELD not in after:
                raise ValueError("Complete native text semantics are unavailable after placement")
            semantic_diff = semantic_difference(
                expected[SEMANTIC_FIELD],
                after[SEMANTIC_FIELD],
                before[SEMANTIC_FIELD],
                plan["results"],
            )
        elif SEMANTIC_FIELD in after:
            raise ValueError("Native text semantic inventories differ across placement")
        if draft:
            # Review-only: normal plans never emit DRAFT_GUARD and the native
            # apply path accepts only BATCH_GUARD. The existing v5 path stays strict.
            engineering = compare_silk_draft(before, expected, after, plan["results"])
            raw_difference = None if engineering["valid"] else engineering.get("difference")
        comparison_expected = copy.deepcopy(expected)
        if raw_difference is None:
            # A native double differing only by arithmetic noise can land on the
            # other side of a round4 half-point. Both raw inventories have already
            # been bound exactly to their own reader projections. Only after the
            # raw rigid proof succeeds may its observed coordinate projection be
            # used here; pen, codes, format and all non-target fields stay guarded.
            observed_raw = {row["uid"]: row for row in after[WITNESS_FIELD]}
            for text in comparison_expected["model"]["texts"]:
                if text.get("refdes") in selected:
                    projected = projection(observed_raw[text["uid"]])
                    text.update(extents=projected["extents"], strokes=projected["strokes"])
            comparison_expected[WITNESS_FIELD] = copy.deepcopy(after[WITNESS_FIELD])
        identity = {ref: str(row["uid"]) for ref, row in old_index.items()} == {
            ref: str(row["uid"]) for ref, row in new_index.items()
        }
        non_target = identity and all(
            new_index[ref] == row for ref, row in old_index.items() if ref not in selected
        )
        target = identity and all(
            _material_equal(expected_index[ref], new_index[ref]) for ref in selected
        )
        old_model, new_model, expected_model = (
            before["model"],
            after["model"],
            comparison_expected["model"],
        )
        board = old_model.keys() == new_model.keys()
        for key in old_model:
            if key in {"pads", "silk", "texts"}:
                untouched_old = [r for r in old_model[key] if r.get("refdes") not in selected]
                untouched_new = [
                    r for r in new_model.get(key, []) if r.get("refdes") not in selected
                ]
                board = board and untouched_old == untouched_new
                board = board and _material_equal(expected_model[key], new_model.get(key))
            elif key == "holes":
                board = board and _holes_preserved(before, after, expected, selected)
            else:
                board = board and old_model[key] == new_model.get(key)
        checks = {
            "unique_complete_identity": identity,
            "non_target_exact": non_target,
            "selected_rigid_material_and_pose": target,
            "old_copper_and_board": board,
            "stackup_exact": before["stackup"] == after["stackup"],
        }
        if draft:
            checks["native_text_engineering_geometry"] = raw_difference is None
        else:
            checks["native_text_raw_geometry"] = raw_difference is None
        if SEMANTIC_FIELD in before:
            checks["native_text_semantics"] = semantic_diff is None
        report = {
            "valid": all(checks.values()),
            "checks": [{"name": k, "passed": bool(v)} for k, v in checks.items()],
            "scope": (
                "full-board identity, pin/net/material and rigid target poses; "
                "all old copper and non-target fields"
            ),
        }
        if engineering is not None:
            report["silk_engineering_equivalence"] = engineering
            report["qualification"] = copy.deepcopy(engineering["qualification"])
            report["not_checked"] = ["silkscreen clearance and applicable protection-zone rules"]
            report["scope"] += "; draft silk geometry only, not clearance or manufacturing"
        if reopen_proof is not None:
            report["reopen_reference_equivalence"] = copy.deepcopy(reopen_proof)
        if not report["valid"]:
            report["difference"] = (
                raw_difference or semantic_diff or _material_difference(comparison_expected, after)
            )
        return report
    except (CLIError, KeyError, TypeError, ValueError, OverflowError) as error:
        report = {
            "valid": False,
            "scope": "complete bounded full-board placement material and native text witnesses",
            "checks": [{"name": "complete bounded material evidence", "passed": False}],
            "issue": _exception_issue(error),
        }
        difference = (getattr(error, "details", {}) or {}).get("difference")
        if isinstance(difference, dict):
            report["difference"] = copy.deepcopy(difference)
        return report


def _material_difference(
    expected: Any, actual: Any, path: str = "protection", key: str | None = None
) -> dict | None:
    """Retain the first concrete failing leaf instead of a bare protection boolean."""
    if _material_equal(expected, actual, key):
        return None
    if isinstance(expected, dict) and isinstance(actual, dict) and expected.keys() == actual.keys():
        for key in expected:
            result = _material_difference(expected[key], actual[key], path + "." + key, key)
            if result is not None:
                return result
    elif isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        for index, (wanted, got) in enumerate(zip(expected, actual, strict=True)):
            child_key = (
                "x"
                if key in {"center_mm", "bounds_mm", "extents", "_stroke_point"}
                or key in {"circle", "circle_mm", "_path_point"}
                and index < 2
                else "_stroke"
                if key == "strokes"
                else "_stroke_point"
                if key == "_stroke"
                else "_path_point"
                if key == "path"
                else "path"
                if key == "cutouts"
                else None
            )
            label = (
                f"uid={wanted['uid']}"
                if isinstance(wanted, dict) and "uid" in wanted
                else str(index)
            )
            result = _material_difference(wanted, got, f"{path}[{label}]", child_key)
            if result is not None:
                return result
    return {"path": path, "expected": expected, "actual": actual}


def _exception_issue(error: Exception, stage: str | None = None) -> dict:
    issue = {"code": getattr(error, "code", "E_UNKNOWN"), "exception_type": type(error).__name__}
    if stage is not None:
        issue["stage"] = stage
    if (
        isinstance(getattr(error, "code", None), str)
        and error.code.startswith("E_")
        and isinstance(getattr(error, "message", None), str)
        and isinstance(getattr(error, "details", None), dict)
    ):
        issue.update(message=error.message, details=copy.deepcopy(error.details))
        native_cause = error.__cause__
        if (
            native_cause is not None
            and not hasattr(native_cause, "code")
            and not hasattr(native_cause, "hresult")
            and not hasattr(native_cause, "excepinfo")
        ):
            private_text = str(native_cause)

            def redact_source(value):
                if isinstance(value, str) and private_text:
                    return value.replace(private_text, "<native exception text withheld>")
                if isinstance(value, dict):
                    return {key: redact_source(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [redact_source(item) for item in value]
                return value

            issue["message"] = redact_source(issue["message"])
            issue["details"] = redact_source(issue["details"])
            issue["native_exception_type"] = type(native_cause).__name__
    return issue


def validate_batch_preview(preview: dict, request: dict) -> dict:
    rows = [row["before"] for row in preview["results"]]
    reconstructed = plan_placement(request, rows, protection_state=preview["protection_before"])
    for key in (
        "request",
        "results",
        "summary",
        "state_digest",
        "state_guard",
        "protection_before",
    ):
        if preview[key] != reconstructed[key]:
            fail(
                "placement preview is inconsistent with full-board observations", key, "E_CONFLICT"
            )
    return reconstructed


class PlacementDriver(Protocol):
    def observe(self, selection: list[str]) -> list[dict[str, Any]]: ...
    def enable_drc(self) -> Any: ...
    def restore_drc(self, previous: Any) -> None: ...
    def move(self, target: dict[str, Any]) -> None: ...
    def capture_state(self) -> dict[str, Any]: ...
    def save_reopen(self, lifecycle: dict[str, Any]) -> None: ...


def execute_placement(
    request: Any, expected_digest: str, driver: PlacementDriver
) -> dict[str, Any]:
    """Execute serially under caller's lock; stop at first uncertainty, never undo blindly.

    No all-or-none claim: a failed UnPlace/Place can leave this part unplaced.
    Previously verified parts remain changed. Partial/failed batches are not saved.
    """
    try:
        request = validate_request(request)
        plan = plan_placement(
            request, driver.observe(request["selection"]), protection_state=driver.capture_state()
        )
    except Exception as error:
        raise CLIError(
            getattr(error, "code", "E_VERIFY_INCOMPLETE"),
            "complete placement precondition could not be observed; "
            "no component edit was dispatched",
            {
                **(getattr(error, "details", {}) or {}),
                "stage": "precondition",
                "write_attempted": False,
                "cause": type(error).__name__,
                "cause_error": _exception_issue(error),
            },
        ) from error
    if plan["state_digest"] != expected_digest:
        raise CLIError(
            "E_CONFLICT",
            "full-board material or placements changed since preview",
            {"stage": "precondition", "write_attempted": False},
        )
    rows = [
        {
            **row,
            "ok": not row["changed"],
            "status": "not_attempted" if row["changed"] else "unchanged",
        }
        for row in plan["results"]
    ]
    report: dict[str, Any] = {
        "results": rows,
        "outcome": "complete",
        "saved": False,
        "persistence_verified": False,
        "persistence_state": "unchanged",
        "no_change": plan["summary"]["changed_count"] == 0,
        "write_attempted": False,
        "state_digest_before": expected_digest,
        "drc_restored": None,
        "state_guard": BATCH_GUARD,
        "protection_before": plan["protection_before"],
        "verification": {"valid": False, "scope": "full_board_material", "drc": "not_run"},
        "lifecycle": {"save_count": 0, "close_count": 0, "open_count": 0},
        "issues": [],
        "_untrusted": ["results", "issues", "protection_before", "verification"],
    }
    current_stage = "drc_enable"
    previous = None
    enabled = False
    try:
        if any(row["changed"] for row in rows):
            previous = driver.enable_drc()
            enabled = True
        for row in rows:
            if not row["changed"]:
                continue
            current_stage = "before_item"
            observed = normalize_state(driver.observe([row["id"]]), [row["id"]])[0]
            if not same_position(row["before"], observed):
                raise CLIError("E_CONFLICT", "component changed before its turn")
            current_stage = "place"
            row["status"] = "outcome_unknown"
            report["write_attempted"] = True
            report["persistence_state"] = "unknown"
            try:
                driver.move(row["target"])
            except Exception:
                # Read-only evidence only: never retry Place, save a partial batch,
                # or conceal the possible unplaced state with a rollback.
                try:
                    observer = getattr(driver, "observe_failure", None)
                    raw = (
                        [observer(row["id"])]
                        if observer is not None
                        else driver.observe([row["id"]])
                    )
                    row["observed_on_failure"] = raw
                    row["possible_unplaced"] = any(r.get("placed") is not True for r in raw)
                except Exception:
                    row["possible_unplaced"] = True
                raise
            current_stage = "read_back"
            observed = normalize_state(driver.observe([row["id"]]), [row["id"]])[0]
            row["observed"] = observed
            if not same_position(row["target"], observed):
                raise CLIError("E_PROJECT_INVALID", "requested placement did not read back")
            row.update(ok=True, status="verified")
        current_stage = "final_read_back"
        final = normalize_state(driver.observe(request["selection"]), request["selection"])
        for row, observed in zip(rows, final, strict=True):
            row["observed"] = observed
            if not same_position(row["target"], observed):
                row.update(ok=False, status="verification_failed")
        if not all(row["ok"] for row in rows):
            raise CLIError("E_PROJECT_INVALID", "final placement comparison failed")
        current_stage = "full_board_protection"
        current = driver.capture_state()
        report["geometry_after_edit"] = current
        report["_untrusted"].append("geometry_after_edit")
        report["verification"] = {**verify_batch_state(plan, current), "drc": "not_run"}
        if report["verification"]["valid"] is not True:
            raise CLIError(
                "E_VERIFY_INCOMPLETE",
                "full-board placement protection failed",
                {"verification": report["verification"]},
            )
    except Exception as error:
        report["outcome"] = "partial_failure" if report["write_attempted"] else "failed"
        report["issues"].append(_exception_issue(error, current_stage))
    finally:
        if enabled:
            try:
                driver.restore_drc(previous)
                report["drc_restored"] = True
            except Exception as error:
                report["drc_restored"] = False
                report["outcome"] = "partial_failure" if report["write_attempted"] else "failed"
                report["issues"].append(_exception_issue(error, "drc_restore"))
    if report["outcome"] == "complete" and report["write_attempted"]:
        try:
            current_stage = "save_close_reopen"
            driver.save_reopen(report["lifecycle"])
            report["saved"] = report["lifecycle"].get("save_acknowledged") is True
            current_stage = "reopened_full_board_protection"
            reopened = driver.capture_state()
            report["geometry_after"] = reopened
            report["_untrusted"].append("geometry_after")
            transition = batch_reopen_comparison(current, reopened, report["lifecycle"])
            report["reopen_comparison"] = transition
            comparison = verify_batch_state(
                plan, reopened, reopen_proof=transition["reference_equivalence"]
            )
            # The saved state must reopen exactly; tolerance applies only to the
            # explicitly authorized rigid transform, never persistence drift.
            report["persistence_verified"] = comparison["valid"] and transition["valid"]
            report["verification"] = {
                **comparison,
                "valid": report["persistence_verified"],
                "drc": "not_run",
            }
            if not report["persistence_verified"]:
                raise CLIError("E_VERIFY_INCOMPLETE", "saved/reopened placement material differs")
            report["persistence_state"] = "saved"
            for row, observed in zip(
                rows,
                normalize_state(driver.observe(request["selection"]), request["selection"]),
                strict=True,
            ):
                row["observed"] = observed
                row["status"] = "saved_reopened_verified" if row["changed"] else "unchanged"
        except Exception as error:
            report["outcome"] = "persistence_incomplete"
            report["saved"] = True if report["lifecycle"].get("save_acknowledged") is True else None
            report["verification"]["valid"] = False
            report["issues"].append(_exception_issue(error, current_stage))
    elif report["outcome"] == "complete":
        report["geometry_after"] = report["geometry_after_edit"]
        report["_untrusted"].append("geometry_after")
    report["summary"] = {
        "ok_count": sum(row["ok"] for row in rows),
        "error_count": sum(not row["ok"] for row in rows),
        "applied": [r["id"] for r in rows if r["changed"] and r["ok"]],
        "uncertain": [
            r["id"] for r in rows if r["status"] in {"outcome_unknown", "verification_failed"}
        ],
        "not_attempted": [r["id"] for r in rows if r["status"] == "not_attempted"],
    }
    if report["outcome"] != "complete":
        report["verification"]["valid"] = False
    return report


def input_schema() -> dict[str, Any]:
    numeric = {"type": "number", "minimum": -COORDINATE_LIMIT_MM, "maximum": COORDINATE_LIMIT_MM}
    name = {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^[^\s\x00-\x1f\x7f](?:[^\x00-\x1f\x7f]*[^\s\x00-\x1f\x7f])?$",
    }
    types = {
        "number": numeric,
        "refdes": name,
        "axis": {"enum": ["x", "y"]},
        "point": {"type": "array", "items": numeric, "minItems": 2, "maxItems": 2},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "unit", "selection", "steps"],
        "properties": {
            "schema_version": {"const": "1.0"},
            "unit": {"const": "mm"},
            "selection": {
                "type": "array",
                "items": name,
                "minItems": 1,
                "maxItems": MAX_SELECTION,
                "uniqueItems": True,
            },
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_STEPS,
                "items": {
                    "oneOf": [
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["op", *fields],
                            "properties": {
                                "op": {"const": op},
                                **{k: types[v] for k, v in fields.items()},
                            },
                        }
                        for op, fields in STEP_FIELDS.items()
                    ]
                },
            },
        },
        "description": (
            "Origin-based tasks. Selection order defines distribution order; "
            "align anchor must be selected. No unplacing during preview, "
            "no route repair, flips or DRC simulation."
        ),
    }
