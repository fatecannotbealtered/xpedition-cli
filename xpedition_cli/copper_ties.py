"""Bounded supported 151.0 document tie-leg observations, never material proof.

This optional inventory is separate from plane_state and persistence operands.
No hidden/deprecated plane ownership or ConnectedObjects interface is used.
"""

from __future__ import annotations

import math

from .cell_refresh import digest

MM = 4
MAX_ITEMS = 10000
MAX_POINTS = 100000
MAX_GEOMETRIES = 10000
MAX_INDICES = 20000
MAX_DEPTH = 4


def boundary():
    return {
        "scope": "returned document TieLegs collection only",
        "layer_domain": "native unresolved; not assumed conductor or board-layer numbering",
        "complete_material_proven": False,
        "pad_contact_proven": False,
        "clearance_proven": False,
        "parent_ownership": "not inferred",
        "empty_meaning": "An empty collection does not prove absence of thermal/tie material.",
        "path_meaning": "Points/arcs plus width do not define complete material without "
        "supported arc, stroke cap and join semantics; Filled/IsClosed do not close that gap.",
        "consistency_meaning": "Matching complete reads establish repeatable current collection "
        "readback only; not completeness, physical contact, DRC or persistence.",
    }


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Missing finite native number")
    return float(value)


def _integer(value):
    if type(value) is not int:
        raise ValueError("Missing native integer")
    return value


def _boolean(value):
    if type(value) not in (bool, int) or value not in (-1, 0, 1):
        raise ValueError("Missing native Boolean")
    return bool(value)


def _string(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing native string")
    return value


def _uid(value):
    if type(value) not in (str, int) or not str(value).strip():
        raise ValueError("Missing native UID")
    return str(value)


def _read(row, field, getter):
    try:
        row[field] = getter()
        if row[field] is None:
            raise ValueError("Missing native value")
    except Exception as error:
        # Exception text can contain external/private native data. Keep a typed
        # failure at its exact field; never turn failed getters into empty data.
        row[field] = None
        row.setdefault("errors", []).append({"field": field, "cause": type(error).__name__})
    return row[field]


def _collection(collection, reader, budget):
    row = {"count": None, "items": [], "errors": [], "truncated": False}
    count = _read(row, "count", lambda: _integer(collection.Count))
    if count is None:
        return row
    if count < 0:
        row["errors"].append({"field": "count", "cause": "negative_count"})
        return row
    limit = min(count, MAX_ITEMS, budget["indices"])
    budget["indices"] -= limit
    if count > limit:
        row["truncated"] = True
        row["errors"].append({"field": "items", "cause": "collection_limit_exceeded"})
    for index in range(1, limit + 1):
        item = {"index": index, "errors": []}
        native = _read(item, "value", lambda i=index: collection.Item(i))
        del item["value"]  # Never serialize a native object.
        if native is not None:
            item.update(reader(native))
        row["items"].append(item)
    return row


def _points(geometry, a, budget):
    rows = a._propget(geometry, 1, MM)
    if (
        not isinstance(rows, (list, tuple))
        or len(rows) != 3
        or any(not isinstance(r, (list, tuple)) for r in rows)
        or len({len(r) for r in rows}) != 1
    ):
        raise ValueError("Incomplete native x/y/r array")
    count = len(rows[0])
    if count > budget["points"]:
        raise ValueError("Tie geometry point budget exceeded")
    budget["points"] -= count
    return [[_number(v) for v in p] for p in zip(*rows, strict=True)]


def _geometry(geometry, a, budget, depth=0):
    row = {"errors": []}
    if depth > MAX_DEPTH or budget["geometries"] == 0:
        row["errors"].append({"field": "geometry", "cause": "geometry_budget_exceeded"})
        return row
    budget["geometries"] -= 1
    for field, method in (
        ("is_valid", "IsValid"),
        ("is_path", "IsPath"),
        ("is_closed", "IsClosed"),
        ("is_arced", "IsArced"),
        ("is_circle", "IsCircle"),
    ):
        _read(row, field, lambda m=method: _boolean(getattr(geometry, m)()))
    _read(row, "filled", lambda: _boolean(geometry.Filled))
    _read(row, "line_width_mm", lambda: _number(a._propget(geometry, 5, MM)))
    if row["is_circle"] is True:
        _read(
            row,
            "circle_mm",
            lambda: [_number(a._propget(geometry, d, MM)) for d in (10, 11, 12)],
        )
    elif row["is_circle"] is False:
        _read(row, "points_xyr_mm", lambda: _points(geometry, a, budget))
    cutouts = _read(row, "cutouts", lambda: geometry.Cutouts)
    if cutouts is not None:
        row["cutouts"] = _collection(cutouts, lambda g: _geometry(g, a, budget, depth + 1), budget)
    return row


def _leg(item, a, budget):
    row = {"errors": []}
    _read(row, "uid", lambda: _uid(item.UniqueId))
    _read(row, "native_layer", lambda: _integer(item.Layer))
    _read(row, "type2", lambda: _integer(item.Type2))
    _read(row, "is_valid", lambda: _boolean(item.IsValid()))
    net = _read(row, "net", lambda: item.Net)
    if net is not None:
        _read(row, "net", lambda: _string(net.Name))
        _read(row, "net_uid", lambda: _uid(net.UniqueId))
    _read(
        row,
        "extrema_mm",
        lambda: [_number(a._propget(item.Extrema, d, MM)) for d in (1, 2, 3, 4)],
    )
    geometry = _read(row, "geometry", lambda: item.Geometry)
    if geometry is not None:
        row["geometry"] = _geometry(geometry, a, budget)
    return row


def _complete(value):
    if isinstance(value, dict):
        return not value.get("errors") and all(_complete(v) for v in value.values())
    if isinstance(value, list):
        return all(_complete(v) for v in value)
    return True


def observe(doc, a):
    """Read the explicit supported collection, retaining every bounded index/failure."""
    row = {
        "source": "IMGCPCBDocument.TieLegs DISPID154 PROPERTYGET(0,0)",
        "units": "mm",
        "limits": {
            "collection_items": MAX_ITEMS,
            "total_points": MAX_POINTS,
            "total_geometries": MAX_GEOMETRIES,
            "total_collection_indices": MAX_INDICES,
            "cutout_depth": MAX_DEPTH,
        },
        "errors": [],
    }
    collection = _read(row, "collection", lambda: a._propget(doc, 154, 0, 0))
    if collection is not None:
        budget = {"points": MAX_POINTS, "geometries": MAX_GEOMETRIES, "indices": MAX_INDICES}
        row["collection"] = _collection(collection, lambda item: _leg(item, a, budget), budget)
        uids = [v.get("uid") for v in row["collection"]["items"] if v.get("uid") is not None]
        if len(set(uids)) != len(uids):
            row["errors"].append({"field": "collection.items.uid", "cause": "duplicate_uid"})
    row["complete"] = _complete(row)
    return row


def compare(before, after):
    equal = before == after
    complete = before["complete"] and after["complete"]
    result = {
        **after,
        "evidence_boundary": boundary(),
        "consistency": "two_matching_complete_reads" if complete and equal else "unverified",
        "comparison": {
            "equal": equal,
            "before_complete": before["complete"],
            "after_complete": after["complete"],
            "before_sha256": digest(before),
            "after_sha256": digest(after),
        },
    }
    if not complete or not equal:
        result["before"] = before
    return result
