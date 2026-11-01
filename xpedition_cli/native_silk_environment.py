"""Bounded read-only151.0 silk surroundings; never selects the draft for editing.

Raw observations are deliberately distinct from supported geometry and effective
rule applicability. A numeric clearance response alone cannot prove that151.0
supports a fabrication-text object pair.
"""

from __future__ import annotations

import math
from pathlib import Path

from . import board_labels

MAX_OBJECTS = 4096
MAX_POINTS = 65536
MAX_TARGETS = 8
MAX_NATIVE_OBJECTS_TOTAL = 20000


def _reserve(budget, key, amount, limit):
    if budget["exhausted"] or budget[key] + amount > limit:
        budget["exhausted"] = True
        raise ValueError("Native environment " + key + " budget exceeded")
    budget[key] += amount


def _failure(error):
    # COM wrapper reprs include process addresses. They are not material and
    # would make two identical failed observations appear geometrically changed.
    return {
        "exception_type": type(error).__name__,
        "hresult": getattr(error, "hresult", None),
        "reason": str(error) if isinstance(error, ValueError) else "native getter unavailable",
        "native_exception_info": getattr(error, "excepinfo", None),
    }


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Native coordinate/width is not finite")
    return value


def _uid(obj):
    value = obj.UniqueId
    if type(value) not in (int, str) or not str(value).strip() or str(value) == "0":
        raise ValueError("Native UID is unavailable")
    return str(value)


def _geometry(obj, a, budget, *, nested=False):
    if budget["exhausted"]:
        raise ValueError("Native environment budget already exhausted")
    if obj.IsValid() is not True:
        raise ValueError("Native geometry is invalid")
    row = {
        "unit": "mm",
        "source": "IMGCPCBGeometry151 parameterized properties with EPcbUnitMM4",
        "closed": bool(obj.IsClosed()),
        "filled": bool(obj.Filled),
        "pen_mm": _number(a._propget(obj, 5, 4)),
    }
    if row["pen_mm"] < 0:
        raise ValueError("Negative native geometry pen")
    if obj.IsCircle():
        row["circle_mm"] = [_number(a._propget(obj, key, 4)) for key in (10, 11, 12)]
        if row["circle_mm"][2] <= 0:
            raise ValueError("Empty native circle")
    else:
        array = a._propget(obj, 1, 4)
        if (
            len(array) != 3
            or len({len(v) for v in array}) != 1
            or not 2 <= len(array[0]) <= MAX_POINTS
        ):
            raise ValueError("Native geometry point array is incomplete or unbounded")
        _reserve(budget, "points", len(array[0]), MAX_POINTS)
        row["path_mm"] = [[_number(v) for v in point] for point in zip(*array, strict=True)]
    cuts = _collection(obj.Cutouts, budget)
    if nested and cuts:
        raise ValueError("Nested cutout geometry is outside the bounded reader")
    row["cutouts"] = [_geometry(cut, a, budget, nested=True) for cut in cuts]
    return row


def _collection(value, budget):
    if callable(value) and not hasattr(value, "Count"):
        value = value()
    count = value.Count
    if type(count) is not int or not 0 <= count <= MAX_OBJECTS:
        raise ValueError("Native collection Count is unavailable or over capacity")
    _reserve(budget, "objects", count, MAX_NATIVE_OBJECTS_TOTAL)
    rows = [value.Item(index) for index in range(1, count + 1)]
    if value.Count != count:
        raise ValueError("Native collection changed while enumerating")
    return rows


def _capture(doc, board, a, target_uids):
    inventories, issues, objects = {}, [], {}
    budget = {"points": 0, "objects": 0, "exhausted": False}

    def inventory(name, value, read):
        result = {"source": name, "complete": False, "rows": [], "errors": []}
        inventories[name] = result
        try:
            if budget["exhausted"]:
                raise ValueError("Native environment budget already exhausted")
            values = _collection(value(), budget)
            result["count"] = len(values)
            seen = set()
            for index, obj in enumerate(values):
                try:
                    if budget["exhausted"]:
                        raise ValueError("Native environment budget already exhausted")
                    row = read(obj, index)
                    identity = row["id"]
                    if identity in seen:
                        raise ValueError("Duplicate native object identity")
                    seen.add(identity)
                    result["rows"].append(row)
                    objects[identity] = obj
                except Exception as error:
                    result["errors"].append({"index": index, **_failure(error)})
                    if budget["exhausted"]:
                        break
            if len(_collection(value(), budget)) != len(values):
                raise ValueError("Native inventory count changed after capture")
            result["complete"] = not result["errors"] and len(result["rows"]) == len(values)
        except Exception as error:
            result["errors"].append(_failure(error))
        if not result["complete"]:
            issues.append(name + ":incomplete")

    def text(obj, index):
        semantic = board_labels._text(obj, board, a, canonical=False)
        strokes = list(obj.StrokeText(4))
        _reserve(budget, "points", max(0, (len(strokes) - 1) // 3), MAX_POINTS)
        return {
            "id": "text:" + _uid(obj),
            "semantic": semantic,
            "raw": {
                "stroke_array_mm": strokes,
                "extents_mm": list(a._extrema_mm(obj)),
            },
        }

    def graphic(obj, index):
        owner = obj.Component
        return {
            "id": "graphic:" + _uid(obj),
            "type": int(obj.Type),
            "side": int(obj.Side),
            "side_name": str(obj.SideName),
            "owner": _uid(owner) if owner is not None else None,
            "geometry": _geometry(obj.Geometry, a, budget),
        }

    def area(obj, index):
        return {
            "id": "area:" + _uid(obj),
            "layer": int(obj.Layer),
            "name": str(obj.Name),
            "geometry": _geometry(obj.Geometry, a, budget),
        }

    def conductor(obj, index):
        owner = obj.Component
        return {
            "id": f"conductor-observation:{index}",
            "identity_kind": "observation_index_only",
            "persistent_identity_available": False,
            "owner": _uid(owner) if owner is not None else None,
            "type": int(obj.Type2),
            "layer": int(obj.Layer),
            "name": str(obj.Name),
            "geometry": _geometry(obj.Geometry, a, budget),
        }

    # Explicit defaults include board AND component fabrication objects.
    inventory("texts", lambda: a._propget(doc, 72, -1, 0, 0, True), text)
    inventory("fabrication_graphics", lambda: a._propget(doc, 69, -1, 0, 0, True), graphic)
    inventory("placement_obstructs", lambda: a._propget(doc, 40, 0, 0), area)
    inventory("obstructs", lambda: a._propget(doc, 41, -1, 0, 0), area)
    inventory("rule_areas", lambda: a._propget(doc, 43, 0, 0), area)
    for name in (
        "Traces",
        "Vias",
        "GeneratedPlanes",
        "PlaneShapes",
        "MountingHoles",
        "ToolingHoles",
        "ShearingHoles",
        "ConductorLayerGfxs2",
        "UserLayerGfxs",
    ):
        inventory(
            name,
            lambda name=name: getattr(doc, name),
            conductor
            if name == "ConductorLayerGfxs2"
            else lambda obj, i, name=name: {
                "id": name + ":" + _uid(obj),
                "geometry": _geometry(obj.Geometry, a, budget),
            },
        )
    try:
        inventories["board_outline"] = {
            "complete": True,
            "geometry": _geometry(doc.BoardOutline.Geometry, a, budget),
        }
    except Exception as error:
        inventories["board_outline"] = {"complete": False, "reason": str(error)}
        issues.append("board_outline:incomplete")

    def pin(obj, index):
        owner = obj.Component
        row = {
            "id": "pin:" + _uid(obj),
            "refdes": str(owner.RefDes),
            "name": str(obj.Name),
            "copper": [],
            "fabrication": [],
            "holes": [],
            "position_mm": [_number(obj.GetPositionX(4)), _number(obj.GetPositionY(4))],
        }
        for hole in _collection(obj.Holes, budget):
            row["holes"].append(
                {
                    "drill_mm": _number(hole.GetDrillSize(4)),
                    "geometries": [
                        _geometry(g, a, budget) for g in _collection(hole.Geometries, budget)
                    ],
                }
            )
        for collection_name, field in (("Pads", "copper"), ("FabricationPads", "fabrication")):
            for pad_index, pad in enumerate(_collection(getattr(obj, collection_name), budget)):
                geometries = [_geometry(g, a, budget) for g in _collection(pad.Geometries, budget)]
                item = {"index": pad_index, "layer": int(pad.Layer), "geometries": geometries}
                if field == "fabrication":
                    item.update(
                        type=int(pad.Type),
                        side=int(pad.Side),
                        name=str(pad.Name),
                        layer_name=str(pad.LayerName),
                    )
                row[field].append(item)
        return row

    inventory("pins", lambda: doc.Pins, pin)
    targets = {"text:" + str(uid) for uid in target_uids}
    missing = targets - objects.keys()
    if missing:
        issues.append("target_text_identity_missing")
    rules = []
    # Only direct formal object-pair observations. No fake general-clearance
    # enum for silk (the151.0 enum has no fabrication-text entry).
    for target in sorted(targets - missing):
        target_row = next(row for row in inventories["texts"]["rows"] if row["id"] == target)
        anchor = target_row["semantic"]["position"]
        same_side = [
            row
            for row in inventories["texts"]["rows"]
            if row["id"] != target
            and row["semantic"]["type"] == 2
            and row["semantic"]["side"] == target_row["semantic"]["side"]
        ]
        same_side.sort(
            key=lambda r: sum(
                (a - b) ** 2 for a, b in zip(r["semantic"]["position"], anchor, strict=True)
            )
        )
        pins = sorted(
            inventories["pins"]["rows"],
            key=lambda r: sum((a - b) ** 2 for a, b in zip(r["position_mm"], anchor, strict=True)),
        )
        candidates = [row["id"] for row in same_side[:2]]
        candidates += [row["id"] for row in pins[:2]]
        candidates += [
            row["id"]
            for row in inventories["fabrication_graphics"]["rows"]
            if row["type"] == 2 and row["side"] == target_row["semantic"]["side_code"]
        ][:2]
        if inventories["board_outline"]["complete"]:
            objects["board_outline"] = doc.BoardOutline
            candidates.append("board_outline")
        for candidate in candidates[:8]:
            row = {
                "target": target,
                "obstacle": candidate,
                "unit": "mm",
                "source": "IMGCPCBClearance151.GetClearanceRule(first,second,-1,-1,4)",
                "applicability_proven": False,
            }
            try:
                row["raw_value_mm"] = _number(
                    doc.Clearance.GetClearanceRule(objects[target], objects[candidate], -1, -1, 4)
                )
                row["status"] = "observed_only"
            except Exception as error:
                row.update(status="unavailable", reason=type(error).__name__ + ": " + str(error))
            rules.append(row)
    return {
        "inventories": inventories,
        "issues": issues,
        "rule_observations": rules,
        "budget": budget,
        "layer_count": int(doc.LayerCount),
        "geometry_inventory_complete": not issues,
        "effective_silk_rule_coverage": "unverified",
        "soldermask_identity": "formal FabricationPad.Type=2 and Side; negative Layer retained raw",
    }


def observe(params, client, a):
    """Two independent native frames on the already open, exact bound board."""
    from .cell_refresh import active
    from .native_process import process_identity

    targets = params.get("text_uids")
    if (
        not isinstance(targets, list)
        or not 1 <= len(targets) <= MAX_TARGETS
        or len(set(map(str, targets))) != len(targets)
    ):
        raise ValueError("One to eight unique explicit text UIDs are required")
    _, board, app, doc = active(params, client, a, require_project=False, quiet=False)
    identity = a.application_identity(app, "pcb")
    generation = process_identity("pcb", identity["pid"])
    before = _capture(doc, board, a, targets)
    after = _capture(doc, board, a, targets)
    fresh = a.application_identity(app, "pcb")
    if (
        identity != fresh
        or generation != process_identity("pcb", fresh["pid"])
        or Path(str(a._licensed_document(app).FullName)).resolve() != board.resolve()
    ):
        raise ValueError("Application generation or active board changed during read")
    return {
        "contract": "silk-environment-observation-v1-draft",
        "board": str(board),
        "application": identity,
        "process_identity": generation,
        "text_uids": targets,
        "before": before,
        "after": after,
        "repeat_exact": before == after,
        "source_scope": "complete bounded raw inventories; effective rule coverage is separate",
        "cad_mutations_dispatched": 0,
        "public_draft_apply_enabled": False,
        "final_write_eligibility": {
            "eligible": False,
            "reason": "draft inactive; rule applicability unverified",
        },
        "_untrusted": ["before", "after", "board"],
    }
