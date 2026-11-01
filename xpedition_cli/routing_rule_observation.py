"""Supported151.0 location-scheme observations, never path-rule eligibility."""

from __future__ import annotations

import math

MAX_POINTS = 256


def observe(doc, item):
    """Read PickScheme at declared vertices with explicit mm; do not interpolate rules."""
    result = {
        "source": "Document.PickScheme; supported MGCPCB151.0 DISPID223",
        "signature": "PickScheme(x:double,y:double,Layer:int,ToLayer:int=-1,Unit:EPcbUnit=0)->BSTR",
        "unit": "mm",
        "native_unit_argument": 4,
        "to_layer_argument": -1,
        "layer": item.get("layer"),
        "point_limit": MAX_POINTS,
        "points": [],
        "unreviewed": [],
        "observation_complete": False,
        "path_rule_applicability_proven": False,
        "influence_region_rule_applicability_proven": False,
        "scope": (
            "declared path vertices only; not segments, trace width or clearance influence region"
        ),
        "missing_dependencies": [
            "same-engineering snapshot binding between Layout and complete constraint design",
            "exhaustive local scheme/child-object constraint sources and resolution precedence",
            "effective width/clearance applicability over the whole finite conductor "
            "influence region",
        ],
        "boundary": (
            "A returned location scheme is an observation. Identical sampled schemes, an empty "
            "RuleAreas result or a single observed net class cannot establish absence of local "
            "overrides or effective rules over the path. No write eligibility follows."
        ),
    }
    points = item.get("points")
    layer = item.get("layer")
    if (
        type(layer) is not int
        or layer < 1
        or not isinstance(points, list)
        or not 2 <= len(points) <= MAX_POINTS
    ):
        result["unreviewed"].append({"reason": "invalid_or_unbounded_path_or_layer"})
        return result
    for index, point in enumerate(points):
        if (
            not isinstance(point, (list, tuple))
            or len(point) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)
        ):
            result["unreviewed"].append({"point_index": index, "reason": "invalid_mm_point"})
            continue
        row = {"point_index": index, "point_mm": list(point), "scheme": None}
        try:
            scheme = doc.PickScheme(float(point[0]), float(point[1]), layer, -1, 4)
            if not isinstance(scheme, str) or not scheme.strip():
                raise ValueError("Empty or non-string location scheme")
            row.update(scheme=scheme, status="observed")
        except Exception as error:
            row.update(status="unreadable", reason=type(error).__name__)
            result["unreviewed"].append(
                {"point_index": index, "reason": "PickScheme:" + type(error).__name__}
            )
        result["points"].append(row)
    result["observation_complete"] = not result["unreviewed"]
    return result


def observe_named_widths(doc, item, native):
    """Bind point schemes to one observed net and its named-class width getters.

    These finite samples do not resolve child overrides or prove the rules over
    a segment or its clearance influence region. No sampling result authorizes
    a write by itself.
    """
    result = observe(doc, item)
    widths = {
        "source": "Net.NetClass.{Min,Typical,Expansion}TraceWidth(layer,scheme,unit4)",
        "unit": "mm",
        "net": None,
        "class_name": None,
        "points": [],
        "observation_complete": False,
        "unreviewed": [],
        "path_applicability_proven": False,
        "clearance_applicability_proven": False,
    }
    result["named_widths"] = widths
    try:
        name, expected_uid = item.get("net"), item.get("net_uid")
        if not isinstance(name, str) or not name or type(expected_uid) not in (str, int):
            raise ValueError("Exact net name and snapshot UID are required")
        if not str(expected_uid).strip() or str(expected_uid) == "0":
            raise ValueError("A nonzero snapshot net UID is required")
        if native.NET_CLASS_WIDTHS != {"minimum": 7, "typical": 8, "expansion": 9}:
            raise ValueError("The supported native width getter mapping is unavailable")
        layer_count = doc.LayerCount
        layer = item.get("layer")
        if type(layer_count) is not int or type(layer) is not int or not 1 <= layer <= layer_count:
            raise ValueError("The requested conductor layer is not observed")
        collection = native._com_member(doc, "Nets")
        count = collection.Count
        if type(count) is not int or not 0 <= count <= 4096:
            raise ValueError("The native net inventory is unavailable or unbounded")
        nets = list(native._items(collection, strict=True))
        if len(nets) != count or collection.Count != count:
            raise ValueError("The native net inventory changed during enumeration")
        matches, seen_names, seen_uids = [], set(), set()
        for net in nets:
            net_name, net_uid = net.Name, net.UniqueId
            if (
                not isinstance(net_name, str)
                or not net_name.strip()
                or type(net_uid) not in (str, int)
                or not str(net_uid).strip()
                or str(net_uid) == "0"
                or net_name in seen_names
                or str(net_uid) in seen_uids
            ):
                raise ValueError("A native net identity is incomplete")
            seen_names.add(net_name)
            seen_uids.add(str(net_uid))
            if net_name == name:
                matches.append(net)
        if len(matches) != 1 or str(matches[0].UniqueId) != str(expected_uid):
            raise ValueError("The current native net does not uniquely match the snapshot")
        net_class = matches[0].NetClass
        class_name = net_class.Name
        if not isinstance(class_name, str) or not class_name.strip():
            raise ValueError("The target native net class is unavailable")
        widths["net"] = {"name": name, "uid": str(expected_uid)}
        widths["class_name"] = class_name
    except Exception as error:
        widths["unreviewed"].append({"reason": "net_or_class_identity:" + type(error).__name__})
        return result

    for point in result["points"]:
        row = {"point_index": point["point_index"], "scheme": point.get("scheme"), "values_mm": {}}
        if point.get("status") != "observed":
            widths["unreviewed"].append(
                {"point_index": point["point_index"], "reason": "location_scheme_unreadable"}
            )
        else:
            for kind, dispid in native.NET_CLASS_WIDTHS.items():
                try:
                    value = native._propget(net_class, dispid, item["layer"], point["scheme"], 4)
                    if type(value) not in (int, float):
                        raise ValueError("Width getter did not return a native number")
                    value = float(value)
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("Width must be finite and nonnegative")
                    row["values_mm"][kind] = value
                except Exception as error:
                    widths["unreviewed"].append(
                        {
                            "point_index": point["point_index"],
                            "field": kind,
                            "reason": type(error).__name__,
                        }
                    )
        widths["points"].append(row)
    widths["observation_complete"] = (
        result["observation_complete"] and not widths["unreviewed"] and bool(widths["points"])
    )
    return result
