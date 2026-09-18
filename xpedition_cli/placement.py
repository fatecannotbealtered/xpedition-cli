"""Deterministic, origin-based placement tasks. No COM, I/O writes or DRC here.

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
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("refdes"), str):
            fail("component observations need a reference designator", "components")
        refdes = row["refdes"]
        if refdes not in selected:
            continue
        if refdes in by_name:
            fail("selected reference designator is ambiguous", refdes, "E_CONFLICT")
        if row.get("unit") != "mm" or row.get("placed") is not True:
            fail("only placed components with explicit millimetre observations are supported", refdes)
        if row.get("side") not in ("top", "bottom"):
            fail("component side is missing or unknown", refdes)
        # Protection must be observed, never guessed false. Native binding uses
        # this same strict contract; unsupported observations fail before writes.
        if type(row.get("anchor")) is not int or row["anchor"] not in (0, 1, 2, 3):
            fail("component anchor state must be explicitly observed", refdes)
        if type(row.get("fix_lock")) is not int or not 0 <= row["fix_lock"] <= 2147483647:
            fail("component fix_lock state must be explicitly observed", refdes)
        object_id = _name(row.get("object_id"), refdes + ".object_id")
        by_name[refdes] = {
            "refdes": refdes, "x": number(row.get("x"), refdes + ".x"),
            "y": number(row.get("y"), refdes + ".y"),
            "rotation": number(row.get("rotation"), refdes + ".rotation") % 360,
            "side": row["side"], "placed": True, "unit": "mm",
            "anchor": row["anchor"], "fix_lock": row["fix_lock"], "object_id": object_id,
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
            if not math.isclose(number(expected[key], key), number(observed[key], key),
                                rel_tol=0, abs_tol=POSITION_TOLERANCE_MM):
                return False
        delta = (number(expected["rotation"], "rotation") -
                 number(observed["rotation"], "rotation") + 180) % 360 - 180
        return abs(delta) <= ANGLE_TOLERANCE_DEG
    except (KeyError, CLIError):
        return False


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def plan_placement(request: Any, observations: Any) -> dict[str, Any]:
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
    return {
        "schema_version": "1.0", "unit": "mm", "request": request,
        "state_digest": digest(sorted(before, key=lambda row: row["refdes"])),
        "results": results,
        "summary": {"selected_count": len(results), "changed_count": sum(r["changed"] for r in results)},
        "validation": {"input": "validated", "geometry": "origin_math_only", "drc": "not_run",
                       "native_smoke": "missing"},
        "_untrusted": ["request", "results"],
    }


class PlacementDriver(Protocol):
    def observe(self, selection: list[str]) -> list[dict[str, Any]]: ...
    def enable_drc(self) -> Any: ...
    def restore_drc(self, previous: Any) -> None: ...
    def move(self, target: dict[str, Any]) -> None: ...
    def save(self) -> None: ...


def execute_placement(request: Any, expected_digest: str, driver: PlacementDriver) -> dict[str, Any]:
    """Execute serially under caller's lock; stop at first uncertainty, never undo blindly.

    No all-or-none claim: a failed UnPlace/Place can leave this part unplaced.
    Previously verified parts remain changed. Partial/failed batches are not saved.
    """
    request = validate_request(request)
    plan = plan_placement(request, driver.observe(request["selection"]))
    if plan["state_digest"] != expected_digest:
        raise CLIError("E_CONFLICT", "selected placements changed since preview",
                       {"stage": "precondition", "write_attempted": False})
    rows = [{**row, "ok": not row["changed"], "status": "not_attempted" if row["changed"] else "unchanged"}
            for row in plan["results"]]
    report: dict[str, Any] = {
        "results": rows, "outcome": "complete", "saved": False, "write_attempted": False,
        "state_digest_before": expected_digest, "drc_restored": None,
        "verification": {"valid": False, "scope": "selected_placements", "drc": "not_run"},
        "issues": [], "_untrusted": ["results", "issues"],
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
            driver.move(row["target"])
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
        report["verification"]["valid"] = True
    except Exception as error:
        report["outcome"] = "partial_failure" if report["write_attempted"] else "failed"
        report["issues"].append({"stage": current_stage, "code": getattr(error, "code", "E_UNKNOWN"),
                                 "exception_type": type(error).__name__})
    finally:
        if enabled:
            try:
                driver.restore_drc(previous)
                report["drc_restored"] = True
            except Exception as error:
                report["drc_restored"] = False
                report["outcome"] = "partial_failure" if report["write_attempted"] else "failed"
                report["issues"].append({"stage": "drc_restore", "exception_type": type(error).__name__})
    if report["outcome"] == "complete" and report["write_attempted"]:
        try:
            driver.save()
            report["saved"] = True
        except Exception as error:
            report["outcome"] = "save_unknown"
            report["saved"] = None
            report["issues"].append({"stage": "save", "exception_type": type(error).__name__})
    report["summary"] = {"ok_count": sum(row["ok"] for row in rows),
                         "error_count": sum(not row["ok"] for row in rows)}
    return report


def input_schema() -> dict[str, Any]:
    numeric = {"type": "number", "minimum": -COORDINATE_LIMIT_MM, "maximum": COORDINATE_LIMIT_MM}
    name = {"type": "string", "minLength": 1, "maxLength": 128,
            "pattern": r"^[^\s\x00-\x1f\x7f](?:[^\x00-\x1f\x7f]*[^\s\x00-\x1f\x7f])?$"}
    types = {"number": numeric, "refdes": name, "axis": {"enum": ["x", "y"]},
             "point": {"type": "array", "items": numeric, "minItems": 2, "maxItems": 2}}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "unit", "selection", "steps"],
        "properties": {
            "schema_version": {"const": "1.0"}, "unit": {"const": "mm"},
            "selection": {"type": "array", "items": name, "minItems": 1,
                          "maxItems": MAX_SELECTION, "uniqueItems": True},
            "steps": {"type": "array", "minItems": 1, "maxItems": MAX_STEPS,
                      "items": {"oneOf": [
                          {"type": "object", "additionalProperties": False,
                           "required": ["op", *fields],
                           "properties": {"op": {"const": op}, **{k: types[v] for k, v in fields.items()}}}
                          for op, fields in STEP_FIELDS.items()]}},
        },
        "description": "Origin-based tasks. Selection order defines distribution order; align anchor must be selected. No unplacing during preview, no route repair, flips or DRC simulation.",
    }
