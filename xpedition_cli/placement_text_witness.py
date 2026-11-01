"""Placement-only, UID-bound native text doubles alongside the full board guard."""

from __future__ import annotations

import copy
import math
from fractions import Fraction
from pathlib import Path

from .errors import CLIError

WITNESS_FIELD = "text_native_witness"
SEMANTIC_FIELD = "text_semantic_witness"
RAW_TOLERANCE_MM = 1e-9
MAX_TEXT_POINTS = 16384
MAX_TEXTS = 16384
MAX_RAW_POINTS_TOTAL = 65536


def contract():
    """Read-only preflight limits; no native activation, reads or phase cache."""
    from .placement import MAX_PROTECTION_BYTES

    return {
        "schema_version": "1.0",
        "limits": {
            "max_texts": MAX_TEXTS,
            "max_points_per_text": MAX_TEXT_POINTS,
            "max_raw_points_total": MAX_RAW_POINTS_TOTAL,
            "max_full_material_bytes_including_witness": MAX_PROTECTION_BYTES,
        },
        "text_count_scope": "entire native FabricationLayerTexts collection before enumeration",
        "raw_point_scope": "all protected on-board silk text witnesses combined",
        "target_raw_coordinate_arithmetic_tolerance_mm": RAW_TOLERANCE_MM,
        "non_target_raw_geometry": "exact",
        "uid_binding": "unique nonzero native UID; one-to-one with complete material texts",
        "projection_binding": (
            "each phase raw witness exactly reprojects to its own reader material"
        ),
        "semantic_binding": (
            "every protected text binds full native format, identity, position, "
            "owner, parent, display condition and lock/reservation state"
        ),
        "cross_phase_cache": "none; every phase captures fresh native material and raw witnesses",
        "incomplete_or_over_capacity": "refuse; no missing-witness fallback or lifecycle replay",
    }


def _raw_point_count(values, field):
    if (
        not isinstance(values, (tuple, list))
        or not 4 <= len(values) <= 1 + 3 * MAX_TEXT_POINTS
        or (len(values) - 1) % 3
    ):
        _refuse("native text raw points are malformed or exceed per-text capacity", field)
    return (len(values) - 1) // 3


def _valid_uid(uid):
    return type(uid) in (str, int) and bool(str(uid).strip()) and str(uid).strip() != "0"


def _refuse(message, field, difference=None):
    details = {"field": field, "_untrusted": ["field"]}
    if difference is not None:
        details["difference"] = difference
        details["_untrusted"].append("difference")
    raise CLIError("E_VERIFY_INCOMPLETE", message, details)


def _first_difference(expected, actual, path):
    if type(expected) is not type(actual) and not (
        type(expected) in (int, float) and type(actual) in (int, float)
    ):
        return {"path": path, "expected": expected, "actual": actual}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        for index, (wanted, got) in enumerate(zip(expected, actual, strict=True)):
            difference = _first_difference(wanted, got, f"{path}[{index}]")
            if difference is not None:
                return difference
    if isinstance(expected, dict) and isinstance(actual, dict) and expected.keys() == actual.keys():
        for key in expected:
            difference = _first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if expected == actual:
        return None
    return {"path": path, "expected": expected, "actual": actual}


SEMANTIC_KEYS = {
    "uid",
    "text",
    "type",
    "text_type",
    "side",
    "side_code",
    "side_name",
    "layer",
    "layer_name",
    "fabrication_identity",
    "stackup_layer_association",
    "owner",
    "parent",
    "position",
    "format",
    "fix_lock",
    "display_condition",
    "reserved",
    "reserved_by",
    "native_name",
    "object_class",
    "original_layer",
    "is_dimension_text",
    "is_flex_text_valid",
}
FORMAT_KEYS = {
    "Font",
    "Bold",
    "Italic",
    "Underline",
    "Mirrored",
    "HorizontalJust",
    "VerticalJust",
    "AspectRatio",
    "Attributes",
    "LineSpacing",
    "height",
    "pen",
    "orientation",
    "valid",
    "is_vector_font",
}


def validate_semantics(state):
    """Bind complete format and ownership observations to every material text."""
    from .placement import POSITION_TOLERANCE_MM

    rows = state.get(SEMANTIC_FIELD)
    texts = state["model"]["texts"]
    if not isinstance(rows, list) or len(rows) != len(texts) or len(rows) > MAX_TEXTS:
        _refuse("complete native text semantic witnesses are required", SEMANTIC_FIELD)
    model = {row["uid"]: row for row in texts}
    components = {row["refdes"]: str(row["uid"]) for row in state.get("components", [])}
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != SEMANTIC_KEYS:
            _refuse("native text semantic witness is incomplete", SEMANTIC_FIELD)
        uid = row["uid"]
        if not isinstance(uid, str) or not _valid_uid(uid) or uid in indexed or uid not in model:
            _refuse("native text semantic identity is missing or ambiguous", SEMANTIC_FIELD)
        field = f"{SEMANTIC_FIELD}[uid={uid}]"
        text = model[uid]
        owner, parent, fmt, position = row["owner"], row["parent"], row["format"], row["position"]
        if text.get("refdes") is None:
            valid_owner = owner is None
        else:
            valid_owner = (
                isinstance(owner, dict)
                and set(owner) == {"uid", "refdes", "placed"}
                and owner.get("refdes") == text.get("refdes")
                and owner.get("uid") == components.get(text.get("refdes"))
                and owner.get("placed") is True
            )
        if not valid_owner:
            _refuse("native text semantic owner differs", field + ".owner")
        if not isinstance(parent, dict) or type(parent.get("object_class")) is not int:
            _refuse("native text semantic parent is incomplete", field + ".parent")
        if set(parent) == {"object_class", "uid", "refdes"}:
            if (
                owner is None
                or parent["uid"] != owner["uid"]
                or parent["refdes"] != owner["refdes"]
            ):
                _refuse("native text semantic parent differs", field + ".parent")
        elif set(parent) == {"object_class", "document"}:
            if not isinstance(parent["document"], str) or not parent["document"]:
                _refuse("native text semantic document is incomplete", field + ".parent")
        else:
            _refuse("native text semantic parent is unsupported", field + ".parent")
        if (
            type(row["type"]) is not int
            or row["type"] != 2
            or type(row["text_type"]) is not int
            or row["side_code"] not in (1, 512)
            or type(row["side_code"]) is not int
            or row["side"] != {1: "top", 512: "bottom"}.get(row["side_code"])
            or row["side_name"] != {1: "Top", 512: "Bottom"}.get(row["side_code"])
            or type(row["layer"]) is not int
            or not isinstance(row["layer_name"], str)
            or not isinstance(row["text"], str)
            or row["text"] != text.get("text")
            or text.get("kind") != {1: "refdes", 4: "part_number"}.get(row["text_type"], "text")
            or row["side"] != text.get("side")
            or type(row["fix_lock"]) is not int
            or type(row["display_condition"]) is not int
            or type(row["reserved"]) is not bool
            or not isinstance(row["reserved_by"], str)
            or not isinstance(row["native_name"], str)
            or type(row["object_class"]) is not int
            or type(row["original_layer"]) is not int
            or type(row["is_dimension_text"]) is not bool
            or type(row["is_flex_text_valid"]) is not bool
            or not isinstance(row["fabrication_identity"], dict)
            or not isinstance(row["stackup_layer_association"], dict)
        ):
            _refuse("native text semantic fields differ from material", field)
        if (
            not isinstance(fmt, dict)
            or set(fmt) != FORMAT_KEYS
            or not isinstance(fmt["Font"], str)
            or not fmt["Font"].strip()
            or any(type(fmt[k]) is not bool for k in ("Bold", "Italic", "Underline", "Mirrored"))
            or any(
                type(fmt[k]) is not int for k in ("HorizontalJust", "VerticalJust", "Attributes")
            )
            or any(
                type(fmt[k]) not in (int, float) or not math.isfinite(fmt[k])
                for k in ("AspectRatio", "LineSpacing", "height", "pen", "orientation")
            )
            or fmt["height"] <= 0
            or fmt["pen"] <= 0
            or fmt["valid"] is not True
            or type(fmt["is_vector_font"]) is not bool
        ):
            _refuse("complete native text format is required", field + ".format")
        if fmt["Mirrored"] != text.get("mirrored"):
            path = field + ".format.Mirrored"
            _refuse(
                "native text format differs from material",
                path,
                {"path": path, "expected": text.get("mirrored"), "actual": fmt["Mirrored"]},
            )
        if (
            not isinstance(position, list)
            or len(position) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in position)
        ):
            _refuse("native text position is incomplete", field + ".position")
        for label, actual, material in (
            ("x", position[0], text.get("x")),
            ("y", position[1], text.get("y")),
            ("height", fmt["height"], text.get("height")),
            ("orientation", fmt["orientation"], text.get("orientation")),
        ):
            if (
                type(material) not in (int, float)
                or not math.isfinite(material)
                or not math.isclose(actual, material, rel_tol=0, abs_tol=POSITION_TOLERANCE_MM)
            ):
                _refuse("native text semantic value differs from material", field + "." + label)
        indexed[uid] = row
    if set(indexed) != set(model):
        _refuse("native text semantic inventory differs", SEMANTIC_FIELD)
    return indexed


def projection(row):
    """Use the existing reader's exact stroke parsing and four-decimal projection."""
    from .native_com_adapter import stroke_rows

    box, values = row["extents_mm"], row["stroke_array_mm"]
    if (
        not isinstance(box, list)
        or len(box) != 4
        or not isinstance(values, list)
        or not 4 <= len(values) <= 1 + 3 * MAX_TEXT_POINTS
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in [*box, *values])
        or box[0] > box[2]
        or box[1] > box[3]
    ):
        raise ValueError("native text witness is malformed or unbounded")
    pen, strokes = stroke_rows(values)
    if not strokes:
        raise ValueError("native text witness has no vector strokes")
    return {"extents": [round(v, 4) for v in box], "strokes": strokes, "pen": round(pen, 4)}


def validate(state):
    """Require exactly one raw witness for every protected text, including free texts."""
    texts, witnesses = state["model"]["texts"], state.get(WITNESS_FIELD)
    if (
        not isinstance(witnesses, list)
        or len(witnesses) != len(texts)
        or len(witnesses) > MAX_TEXTS
    ):
        _refuse("complete native text witnesses are required", WITNESS_FIELD)
    indexed, text_ids, raw_points = {}, set(), 0
    for row in witnesses:
        if (
            not isinstance(row, dict)
            or set(row) != {"uid", "refdes", "extents_mm", "stroke_array_mm"}
            or not isinstance(row.get("uid"), str)
            or not _valid_uid(row["uid"])
            or row["uid"] in indexed
        ):
            _refuse("native text witness identity is missing or ambiguous", WITNESS_FIELD)
        raw_points += _raw_point_count(row["stroke_array_mm"], WITNESS_FIELD)
        if raw_points > MAX_RAW_POINTS_TOTAL:
            _refuse("full-board native text raw points exceed capacity", WITNESS_FIELD)
        indexed[row["uid"]] = row
    for index, text in enumerate(texts):
        field = f"model.texts[{index}]"
        uid = text.get("uid") if isinstance(text, dict) else None
        if not isinstance(uid, str) or not _valid_uid(uid) or uid in text_ids or uid not in indexed:
            _refuse("complete unique text UID bindings are required", field + ".uid")
        text_ids.add(uid)
        field = f"model.texts[uid={uid}]"
        row = indexed[uid]
        if row["refdes"] != text.get("refdes"):
            _refuse("native text witness ownership differs", field + ".refdes")
        try:
            projected = projection(row)
        except Exception:
            _refuse("native text witness projection is unavailable", field + ".native_witness")
        for key, value in projected.items():
            if text.get(key) != value:
                _refuse(
                    "native text witness differs from protected material",
                    field + "." + key,
                    _first_difference(value, text.get(key), field + "." + key),
                )
    if set(indexed) != text_ids:
        _refuse("native text witness inventory differs", WITNESS_FIELD)
    return indexed


def _native_text_collection(doc):
    collection = doc.FabricationLayerTexts
    if callable(collection) and not hasattr(collection, "Count"):
        collection = collection()
    declared_count = collection.Count
    if type(declared_count) is not int or not 0 <= declared_count <= MAX_TEXTS:
        _refuse("native text collection count is invalid or exceeds capacity", WITNESS_FIELD)
    return collection, declared_count


def preflight_native_text_count(doc):
    """Bound native text work before even the phase's first complete material capture."""
    return _native_text_collection(doc)[1]


def capture(doc, bridge, state):
    """Independently enumerate all on-board silk texts and bind to strict material."""
    from .board_labels import _text

    collection, declared_count = _native_text_collection(doc)
    rows, semantics, raw_points, observed_count = [], [], 0, 0
    for text in bridge._items(collection, strict=True):
        observed_count += 1
        if observed_count > declared_count:
            _refuse("complete native text inventory is unavailable", WITNESS_FIELD)
        if int(text.Type) != bridge.FAB_GFX_SILKSCREEN:
            continue
        owner, on_board = bridge._fabrication_owner(text, strict=True)
        if not on_board:
            if not bridge._fabrication_placement_observed(text):
                _refuse("native text owner placement is unavailable", WITNESS_FIELD)
            continue
        uid = text.UniqueId
        if not _valid_uid(uid):
            _refuse("native text UID is unavailable", WITNESS_FIELD)
        values = text.StrokeText(bridge.UNIT_MM)
        raw_points += _raw_point_count(values, WITNESS_FIELD)
        if raw_points > MAX_RAW_POINTS_TOTAL:
            _refuse("full-board native text raw points exceed capacity", WITNESS_FIELD)
        raw = {
            "uid": str(uid),
            "refdes": owner,
            "extents_mm": list(bridge._extrema_mm(text)),
            "stroke_array_mm": list(values),
        }
        # The labels reader authenticates document/parent/owner and all formal
        # formatting and state getters. It also independently reads the geometry;
        # require that both observations describe the very same native frame.
        observed = _text(text, Path(str(doc.FullName)), bridge, canonical=False)
        geometry = observed.pop("native_geometry_witness")
        for key in ("extents", "strokes", "stroke_pen"):
            observed.pop(key)
        if (
            observed["uid"] != raw["uid"]
            or geometry["extents_mm"] != [repr(v) for v in raw["extents_mm"]]
            or geometry["stroke_array_mm"] != [repr(v) for v in raw["stroke_array_mm"]]
        ):
            _refuse("native text changed during semantic capture", SEMANTIC_FIELD)
        rows.append(raw)
        semantics.append(observed)
    final_count = collection.Count
    if (
        type(final_count) is not int
        or final_count != declared_count
        or observed_count != declared_count
    ):
        _refuse("complete native text inventory is unavailable", WITNESS_FIELD)
    state = {
        **state,
        WITNESS_FIELD: sorted(rows, key=lambda row: row["uid"]),
        SEMANTIC_FIELD: sorted(semantics, key=lambda row: row["uid"]),
    }
    validate(state)
    validate_semantics(state)
    return state


def observe(params, client, bridge):
    """SDK-only fresh strict observation; no cached handler state or second board read."""
    from . import cell_refresh

    if params.get("strict") is not True or params.get("observe_only") is not True:
        _refuse("placement text observation requires strict read-only capture", WITNESS_FIELD)
    _, board, app, doc = cell_refresh.active(
        params, client, bridge, require_project=False, quiet=False
    )
    preflight_native_text_count(doc)
    state = capture(doc, bridge, cell_refresh.capture(doc, bridge, strict=True))
    cell_refresh._readonly_identity(params, app, board, bridge)
    return {"board": str(board), "geometry": state}


def transform(row, old, target):
    """Transform raw doubles first; preserve raw pen and stroke control codes exactly."""
    row = copy.deepcopy(row)
    turns = round((target["rotation"] - old["rotation"]) / 90) % 4
    cosine, sine = ((1, 0), (0, 1), (-1, 0), (0, -1))[turns]

    def point(x, y):
        dx, dy = x - old["x"], y - old["y"]
        return [target["x"] + dx * cosine - dy * sine, target["y"] + dx * sine + dy * cosine]

    box = row["extents_mm"]
    corners = [point(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])]
    row["extents_mm"] = [
        min(p[0] for p in corners),
        min(p[1] for p in corners),
        max(p[0] for p in corners),
        max(p[1] for p in corners),
    ]
    values = row["stroke_array_mm"]
    for index in range(1, len(values), 3):
        values[index : index + 2] = point(values[index], values[index + 1])
    return row


def transform_semantics(row, old, target):
    """Move only the anchor and orientation; all other text semantics are fixed."""
    row = copy.deepcopy(row)
    turns = round((target["rotation"] - old["rotation"]) / 90) % 4
    cosine, sine = ((1, 0), (0, 1), (-1, 0), (0, -1))[turns]
    x, y = row["position"]
    dx, dy = x - old["x"], y - old["y"]
    row["position"] = [
        target["x"] + dx * cosine - dy * sine,
        target["y"] + dx * sine + dy * cosine,
    ]
    row["format"]["orientation"] = (
        row["format"]["orientation"] + target["rotation"] - old["rotation"]
    ) % 360
    return row


def _anchor_coordinate_candidates(target, anchor, origin, sign):
    """The finite binary64 results of the equivalent three-term rigid sum.

    The three parenthesizations perform two rounded additions. The fourth value
    rounds the exact sum of the same observed binary64 inputs once. No distance
    tolerance or physical-grid assumption is involved.
    """
    from .placement import COORDINATE_LIMIT_MM, POSITION_TOLERANCE_MM

    inputs = (target, anchor, origin)
    if sign not in (-1, 1) or any(
        type(value) not in (int, float)
        or not math.isfinite(value)
        or abs(value) > COORDINATE_LIMIT_MM + POSITION_TOLERANCE_MM
        for value in inputs
    ):
        raise ValueError("Rigid anchor inputs exceed the bounded coordinate contract")
    a, b, c = float(target), sign * float(anchor), -sign * float(origin)
    exact = Fraction.from_float(a) + Fraction.from_float(b) + Fraction.from_float(c)
    return {(a + b) + c, (a + c) + b, (b + c) + a, float(exact)}


def _rigid_anchor_candidates(position, old, target):
    turns = round((target["rotation"] - old["rotation"]) / 90) % 4
    # x/y each use exactly one original axis under an orthogonal rotation.
    source_axes = ((0, 1), (1, 0), (0, 1), (1, 0))[turns]
    signs = ((1, 1), (-1, 1), (-1, -1), (1, -1))[turns]
    return [
        _anchor_coordinate_candidates(target[axis], position[source], old[("x", "y")[source]], sign)
        for axis, source, sign in zip(("x", "y"), source_axes, signs, strict=True)
    ]


def semantic_difference(expected, actual, before, results):
    """Protect all semantics; target anchors allow only rigid binary64 arithmetic forms."""
    by_uid = {row["uid"]: row for row in actual}
    if len(by_uid) != len(actual) or set(by_uid) != {row["uid"] for row in expected}:
        return {
            "path": SEMANTIC_FIELD + ".uid",
            "expected": "same unique UIDs",
            "actual": "different",
        }
    before_by_uid = {row["uid"]: row for row in before}
    if len(before_by_uid) != len(before) or set(before_by_uid) != set(by_uid):
        return {
            "path": SEMANTIC_FIELD + ".uid",
            "expected": "same unique UIDs",
            "actual": "different",
        }
    moved = {row["id"]: row for row in results if row["changed"]}
    if len(moved) != sum(bool(row["changed"]) for row in results):
        raise ValueError("Duplicate moved-component identity")
    for row in expected:
        observed = by_uid[row["uid"]]
        prefix = f"{SEMANTIC_FIELD}[uid={row['uid']}]"
        owner = row["owner"]
        refdes = owner["refdes"] if owner else None
        if refdes not in moved:
            diff = _first_difference(row, observed, prefix)
            if diff is not None:
                return diff
            continue
        diff = _first_difference(
            {key: value for key, value in row.items() if key != "position"},
            {key: value for key, value in observed.items() if key != "position"},
            prefix,
        )
        if diff is not None:
            return diff
        movement = moved[refdes]
        candidates = _rigid_anchor_candidates(
            before_by_uid[row["uid"]]["position"], movement["before"], movement["target"]
        )
        for axis, (wanted, got) in enumerate(
            zip(row["position"], observed["position"], strict=True)
        ):
            if got not in candidates[axis]:
                return {"path": f"{prefix}.position[{axis}]", "expected": wanted, "actual": got}
    return None


def difference(expected, actual, selected):
    """Non-target raw doubles stay exact; target doubles retain only arithmetic noise."""
    by_uid = {row["uid"]: row for row in actual}
    if len(by_uid) != len(actual) or set(by_uid) != {row["uid"] for row in expected}:
        return {
            "path": WITNESS_FIELD + ".uid",
            "expected": "same unique UIDs",
            "actual": "different",
        }
    for row in expected:
        observed = by_uid[row["uid"]]
        prefix = f"{WITNESS_FIELD}[uid={row['uid']}]"
        for key in ("uid", "refdes", "extents_mm", "stroke_array_mm"):
            wanted, got = row[key], observed.get(key)
            if key not in {"extents_mm", "stroke_array_mm"} or row["refdes"] not in selected:
                if wanted != got:
                    return {"path": prefix + "." + key, "expected": wanted, "actual": got}
                continue
            if not isinstance(got, list) or len(wanted) != len(got):
                return {"path": prefix + "." + key, "expected": wanted, "actual": got}
            for index, (x, y) in enumerate(zip(wanted, got, strict=True)):
                coordinate = key == "extents_mm" or index > 0 and (index - 1) % 3 != 2
                equal = (
                    math.isclose(x, y, rel_tol=0, abs_tol=RAW_TOLERANCE_MM)
                    if coordinate
                    else x == y
                )
                if not equal:
                    return {"path": f"{prefix}.{key}[{index}]", "expected": x, "actual": y}
    return None
