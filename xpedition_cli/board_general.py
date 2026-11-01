"""Finite, selected FromTo General Route contract, separate from RoutePass.

Only supported 151.0 selection and ProcessCommand members are used. Configuration
GETs are observations, never a guarantee of router output or a rule resolver.
"""

from __future__ import annotations

import copy
import math
import time
from datetime import UTC, datetime
from pathlib import Path

from . import board_labels, win_dialogs
from . import board_routepass as R
from . import native_pad_mask as M
from .cell_refresh import active, digest

GUARD = "native-selected-general-route-material-v1"
COMMAND = 35367
UNIT_MM = 4
EXECUTION_BUDGET_SECONDS = 1800


def input_schema():
    uid = {"type": "string", "minLength": 1, "pattern": "^(?!0$).+$"}
    properties = {
        "version": {"type": "integer", "const": 1},
        "net": {"type": "string", "const": "GND"},
        "layer": {"type": "integer", "minimum": 1, "maximum": 128},
        "endpoint_pin_uids": {
            "type": "array",
            "items": uid,
            "minItems": 2,
            "maxItems": 2,
            "uniqueItems": True,
        },
        "allow_replace_trace_uids": {
            "type": "array",
            "items": uid,
            "maxItems": 4096,
            "uniqueItems": True,
        },
        "bounds_mm": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
        "expected_components_after": {"type": "integer", "enum": [1, 2]},
        "select_active_layer": {"type": "boolean", "default": False},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [key for key in properties if key != "select_active_layer"],
        "properties": properties,
        "description": "Finite General Route input; bounds are xmin,ymin,xmax,ymax mm. "
        "Ordered bounds, actual ownership, unique FromTo, material groups and native "
        "configuration are independently validated; this file contains no verified switch.",
    }


def _bool(value, label):
    if type(value) is not bool:
        raise ValueError(label + " did not return a complete BOOL")
    return value


def _int(value, label):
    if type(value) is not int:
        raise ValueError(label + " did not return a complete LONG")
    return value


def observe_configuration(doc, layer_count, app, a=None):
    """Read supported actual settings only; no setter, selection or command.

    Enumeration values come from installed MGCPCB151 EPcbEC* definitions. No
    enabled push/shove option is silently treated as authorization to edit its
    objects; all foreign and undeclared old copper is checked after dispatch.
    """
    if a is None:
        from . import native_com_adapter as a
    result = {
        "source": "MGCPCB151 supported EditorControl/Gui/Document GETs",
        "supported_fields_complete": False,
        "engine_configuration_complete": False,
        "output_guarantee": False,
        "not_checked": [
            {
                "interface": "IMGCPCBEditorControl",
                "member": "RouteOptions",
                "dispid": 1,
                "reason": "151.0 formal help marks this member Deprecated; not invoked",
            },
            {
                "interface": "IMGCPCBEditorControl",
                "member": "Routes",
                "dispid": 21,
                "reason": "151.0 formal help marks this member Deprecated; not invoked",
            },
            {
                "scope": "complete General Route internal/per-command solver configuration",
                "reason": "No supported complete getter or output guarantee established",
            },
        ],
        "read_log": [],
        "completed_fields": [],
        "via_rule_semantics": {
            "0": "Allow one more via per SMD pin",
            "1": "UsePartOutlineAsViaObstructs",
            "2": "SinglePinNetFanout",
            "3": "AutoTrimThroughVias",
        },
        "route_max_vias_semantics": (
            "Actual LONG observation; no per-command no-via guarantee established"
        ),
        "limitations": "Actual settings are frozen observations; entire output must verify",
    }

    def read(owner, interface, dispid, field, *arguments, kind=None):
        record = {
            "field": field,
            "interface": interface,
            "dispid": dispid,
            "arguments": list(arguments),
            "returned": False,
        }
        result["read_log"].append(record)
        try:
            value = a._propget(owner, dispid, *arguments)
            if kind is int:
                value = _int(value, field)
            elif kind is bool:
                value = _bool(value, field)
            record.update(returned=True, value=value if kind is not None else "dispatch_reference")
            result["completed_fields"].append(field)
            return value
        except Exception as error:
            record["error"] = {
                "cause": type(error).__name__,
                "message": str(error),
                "code": getattr(error, "code", None),
                "details": getattr(error, "details", None),
            }
            result["failed_field"] = copy.deepcopy(record)
            failure = ValueError(
                "Configuration read failed at "
                + field
                + " PROPERTYGET "
                + str(dispid)
                + " arguments="
                + str(list(arguments))
            )
            failure.code = getattr(error, "code", "E_NATIVE_FAILURE")
            failure.configuration_observation = copy.deepcopy(result)
            raise failure from error

    actual_count = read(doc, "IMGCPCBDocument", 17, "layer_count", kind=int)
    if not 1 <= actual_count <= 128 or (layer_count is not None and actual_count != layer_count):
        failure = ValueError(
            "Actual supported LayerCount does not equal the frozen complete stackup"
        )
        failure.configuration_observation = copy.deepcopy(result)
        raise failure
    result["layer_count"] = actual_count
    ec = read(doc, "IMGCPCBDocument", 108, "editor_control")
    gui = read(app, "IMGCPCBApplication", 12, "gui")
    result["active_route_layer"] = read(gui, "IMGCPCBGui", 16, "active_route_layer", kind=int)
    result["active_mode"] = read(gui, "IMGCPCBGui", 17, "active_mode", kind=int)
    result["route_max_vias"] = read(ec, "IMGCPCBEditorControl", 2, "route_max_vias", kind=int)
    result["route_layers"] = []
    for layer in range(1, actual_count + 1):
        row = {"layer": layer}
        result["route_layers"].append(row)
        row["editor_enabled"] = read(
            ec,
            "IMGCPCBEditorControl",
            5,
            "route_layers." + str(layer) + ".editor_enabled",
            layer,
            kind=bool,
        )
        row["document_enabled"] = read(
            doc,
            "IMGCPCBDocument",
            75,
            "route_layers." + str(layer) + ".document_enabled",
            layer,
            kind=bool,
        )
    for key, dispid, count in (
        ("push_and_shove", 4, 7),
        ("net_rules", 22, 4),
        ("via_rules", 23, 4),
    ):
        result[key] = []
        for value in range(count):
            result[key].append(
                {
                    "enum": value,
                    "enabled": read(
                        ec, "IMGCPCBEditorControl", dispid, key + "." + str(value), value, kind=bool
                    ),
                }
            )
    result["interactive_drc"] = read(ec, "IMGCPCBEditorControl", 53, "interactive_drc", kind=bool)
    result["supported_fields_complete"] = True
    return result


def validate(request):
    keys = {
        "version",
        "net",
        "layer",
        "endpoint_pin_uids",
        "allow_replace_trace_uids",
        "bounds_mm",
        "expected_components_after",
    }
    if (
        not isinstance(request, dict)
        or not keys <= set(request)
        or not set(request) <= keys | {"select_active_layer"}
    ):
        raise ValueError("General Route requires the exact bounded plan shape")
    bounds = request["bounds_mm"]
    pair, allowed = request["endpoint_pin_uids"], request["allow_replace_trace_uids"]
    for value, size in ((pair, 2), (allowed, None)):
        if (
            not isinstance(value, list)
            or (size is not None and len(value) != size)
            or len(value) > 4096
            or any(not isinstance(uid, str) or not uid or uid == "0" for uid in value)
            or len(set(value)) != len(value)
        ):
            raise ValueError("Actual distinct physical pin and trace UID strings are required")
    if (
        type(request["version"]) is not int
        or request["version"] != 1
        or request["net"] != "GND"
        or type(request["layer"]) is not int
        or not 1 <= request["layer"] <= 128
        or not isinstance(pair, list)
        or len(pair) != 2
        or len(set(pair)) != 2
        or not isinstance(allowed, list)
        or len(allowed) > 4096
        or len(set(allowed)) != len(allowed)
        or any(not isinstance(uid, str) or not uid or uid == "0" for uid in pair + allowed)
        or not isinstance(bounds, list)
        or len(bounds) != 4
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds)
        or bounds[0] >= bounds[2]
        or bounds[1] >= bounds[3]
        or type(request["expected_components_after"]) is not int
        or request["expected_components_after"] not in (1, 2)
        or type(request.get("select_active_layer", False)) is not bool
    ):
        raise ValueError(
            "Only finite six-pin GND, one layer, explicit pair/old copper/bounds are supported"
        )
    return copy.deepcopy(request)


def material_request(request):
    return {"net": request["net"], "layer": request["layer"]}


def _endpoint_object(obj, state, a, *, pins_only):
    info = obj._oleobj_.GetTypeInfo()
    name = info.GetDocumentation(-1)[0]
    guid = str(info.GetTypeAttr()[0]).upper()
    formal = {
        "IMGCPCBPin": ("{E972C9A3-D5F6-4B04-8FE7-EDEDEDEDED09}", "pin", 64),
        "IMGCPCBTrace": ("{E972C9A3-D5F6-4B04-8FE7-EDEDEDEDED0B}", "trace", 87),
    }
    if name not in formal or guid != formal[name][0] or (pins_only and name != "IMGCPCBPin"):
        error = ValueError(
            "Actual FromTo endpoint has unsupported or unknown formal type: "
            + str(name)
            + " "
            + guid
        )
        error.endpoint_type_observation = {"name": name, "guid": guid, "supported": False}
        raise error
    observed_type = {"name": name, "guid": guid, "source": "ITypeInfo actual name and GUID"}
    uid = R.board_trace._uid(a._propget(obj, formal[name][2]))
    pins = [r for r in state["pin_identities"] if r["pin_uid"] == uid]
    traces = [r for r in state["routing_objects"] if r["uid"] == uid]
    if (
        len(pins) + len(traces) != 1
        or (pins_only and len(pins) != 1)
        or (formal[name][1] == "pin" and len(pins) != 1)
        or (formal[name][1] == "trace" and len(traces) != 1)
    ):
        raise ValueError(
            "Actual FromTo endpoint does not uniquely bind a supported physical object"
        )
    if pins:
        row = pins[0]
        return {"kind": "pin", **copy.deepcopy(row), "type_info": observed_type}
    row = traces[0]
    if row["kind"] != "trace":
        raise ValueError("Only actual owned pin or trace endpoints are supported")
    return {
        "kind": "trace",
        "uid": uid,
        "net": row["net"],
        "net_uid": R._nets(state)[row["net"]]["uid"],
        "geometry": copy.deepcopy(row["geometry"]),
        "type_info": observed_type,
    }


def observe_fromtos(doc, state, a, *, selected=False):
    """Observe real and nearest-pin endpoints separately, never invent a FromTo UID."""
    completed = []
    try:
        return _observe_fromtos(doc, state, a, selected=selected, completed=completed)
    except Exception as error:
        error.fromto_observation = {
            "complete": False,
            "completed": copy.deepcopy(completed),
            "failed_observation_index": len(completed),
            "cause": type(error).__name__,
            "message": str(error),
            "collection_complete": False,
            "endpoint_type": getattr(error, "endpoint_type_observation", None),
        }
        raise


def _observe_fromtos(doc, state, a, *, selected, completed):
    collection = a._propget(doc, 29, 1 if selected else 0, 1)
    count = _int(collection.Count, "FromTos.Count")
    if not 0 <= count <= 4096:
        raise ValueError("FromTo inventory exceeds the finite budget")
    native = list(a._items(collection, strict=True))
    if len(native) != count:
        raise ValueError("FromTo inventory enumeration is incomplete")
    rows = []
    for index, item in enumerate(native):
        if item.IsValid() is not True:
            raise ValueError("Actual FromTo is invalid")
        coords = [a._propget(item, dispid, UNIT_MM) for dispid in (3, 4, 5, 6)]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in coords):
            raise ValueError("Actual FromTo coordinates are incomplete")
        row = {
            "net": str(a._propget(item, 9).Name),
            "net_uid": R.board_trace._uid(a._propget(item, 9).UniqueId),
            "from_object": _endpoint_object(a._propget(item, 11, False), state, a, pins_only=False),
            "to_object": _endpoint_object(a._propget(item, 12, False), state, a, pins_only=False),
            "nearest_from_pin": _endpoint_object(
                a._propget(item, 11, True), state, a, pins_only=True
            ),
            "nearest_to_pin": _endpoint_object(
                a._propget(item, 12, True), state, a, pins_only=True
            ),
            "from_mm": coords[:2],
            "to_mm": coords[2:],
            "unit_enum": UNIT_MM,
            "from_layer": _int(a._propget(item, 7), "FromTo.FromLayer"),
            "to_layer": _int(a._propget(item, 8), "FromTo.ToLayer"),
            "selected": _bool(a._propget(item, 1), "FromTo.Selected"),
        }
        row["identity_sha256"] = digest({k: v for k, v in row.items() if k != "selected"})
        rows.append((row, item, index))
        completed.append(row)
    if len({r[0]["identity_sha256"] for r in rows}) != len(rows):
        raise ValueError(
            "FromTo observed identities are ambiguous; collection index is not identity"
        )
    return rows


def _within(raw, bounds):
    observed = raw["bounds_mm"]
    return all(type(v) in (int, float) and math.isfinite(v) for v in observed) and (
        bounds[0] <= observed[0] <= observed[2] <= bounds[2]
        and bounds[1] <= observed[1] <= observed[3] <= bounds[3]
    )


def observe_selection(doc, state, a):
    """Complete finite formal selected-object collections, without changing selection."""
    collections = {
        "nets": (23, (1, True, "*")),
        "components": (22, (1, -1, 4, "*")),
        "pins": (26, (1,)),
        "traces": (27, (1, 0)),
        "vias": (28, (1,)),
    }
    result = {"complete": False, "source": "MGCPCB151 Document selected PROPERTYGET collections"}
    completed, field = [], None
    try:
        for name, (dispid, arguments) in collections.items():
            field = {"collection": name, "field": "PROPERTYGET", "dispid": dispid}
            collection = a._propget(doc, dispid, *arguments)
            field = {"collection": name, "field": "Count"}
            count = _int(collection.Count, name + ".Count")
            if not 0 <= count <= R.MAX_OBJECTS:
                raise ValueError("Actual selected " + name + " count exceeds the finite budget")
            rows = result[name] = []
            for index in range(1, count + 1):
                field = {"collection": name, "field": "Item", "observation_index": index - 1}
                item = collection.Item(index)
                field = {**field, "field": "UniqueId"}
                uid = R.board_trace._uid(item.UniqueId)
                row = {"uid": uid}
                rows.append(row)
                field = {**field, "field": "Selected"}
                row["selected"] = _bool(item.Selected, name + ".Selected")
                if name == "nets":
                    field = {**field, "field": "Name"}
                    row["name"] = str(item.Name)
            field = {"collection": name, "field": "unique_identity_validation"}
            if len({r["uid"] for r in rows}) != count:
                raise ValueError("Actual selected " + name + " identities are duplicated")
            result[name] = sorted(rows, key=lambda row: row["uid"])
            completed.append(name)
        field = {"collection": "fromtos", "field": "selected_inventory"}
        result["fromtos"] = [row[0] for row in observe_fromtos(doc, state, a, selected=True)]
        result["complete"] = True
        return result
    except Exception as error:
        error.selection_observation = {
            "complete": False,
            "failed_field": field,
            "completed_collections": completed,
            "observed": copy.deepcopy(result),
            "cause": type(error).__name__,
            "message": str(error),
        }
        raise


def _selection_allowed(observed, plan):
    try:
        rows = observed["fromtos"]
        nets = observed["nets"]
        return (
            observed["complete"] is True
            and len(rows) == 1
            and rows[0]["selected"] is True
            and rows[0]["identity_sha256"] == plan["selected_fromto"]["identity_sha256"]
            and len(nets) <= 1
            and all(
                row["uid"] == plan["target"]["net_uid"]
                and row["name"] == "GND"
                and type(row["selected"]) is bool
                for row in nets
            )
            and all(observed[key] == [] for key in ("components", "pins", "traces", "vias"))
        )
    except (KeyError, TypeError, ValueError):
        return False


def _components(state, request):
    observation = M.observe_material_components(state, material_request(request))
    if observation.get("valid") is not True:
        raise ValueError(
            "Complete actual material-component observation is required: " + str(observation)
        )
    ids = [r["pin_ids"] for r in observation["components"]]
    expected = {r["pin_uid"] for r in state["pin_identities"] if r["net"] == "GND"}
    if len(ids) not in (1, 2, 3) or set(uid for group in ids for uid in group) != expected:
        raise ValueError("All six actual GND pin materials must be observed")
    return observation


def _configuration_complete(config, count):
    try:
        rows = config["route_layers"]
        return (
            config["output_guarantee"] is False
            and config["supported_fields_complete"] is True
            and config["engine_configuration_complete"] is False
            and config["layer_count"] == count
            and {r.get("dispid") for r in config["not_checked"] if "dispid" in r} == {1, 21}
            and type(config["active_route_layer"]) is int
            and type(config["active_mode"]) is int
            and type(config["route_max_vias"]) is int
            and type(config["interactive_drc"]) is bool
            and len(rows) == count
            and [r["layer"] for r in rows] == list(range(1, count + 1))
            and all(
                type(r["editor_enabled"]) is bool and type(r["document_enabled"]) is bool
                for r in rows
            )
            and all(
                len(config[key]) == maximum
                and [r["enum"] for r in config[key]] == list(range(maximum))
                and all(type(r[value]) is kind for r in config[key])
                for key, maximum, value, kind in (
                    ("push_and_shove", 7, "enabled", bool),
                    ("net_rules", 4, "enabled", bool),
                    ("via_rules", 4, "enabled", bool),
                )
            )
        )
    except (KeyError, TypeError, ValueError):
        return False


def bind(state, request, inputs, config, fromtos, identity, a):
    request = validate(request)
    target = R._nets(state)["GND"]
    pins = [r for r in state["pin_identities"] if r["net"] == "GND"]
    R._six_member_contract("GND", [(r["refdes"], r["pin"]) for r in pins])
    if len(pins) != 6 or any(r["net_uid"] != target["uid"] for r in pins):
        raise ValueError("All six actual GND identities and owners are required")
    objects, raw = R._inventory(state["routing_objects"]), R._raw_trace_rows(state)
    planes = state["plane_inventory"]
    if (
        any(r["kind"] != "trace" for r in objects.values())
        or planes.get("complete") is not True
        or any(planes[k] for k in ("shapes", "generated", "assignments"))
        or state["geometry"]["model"]["planes"]
        or M.verify_evidence(state, material_request(request)).get("valid") is not True
    ):
        raise ValueError("Complete no-via/no-plane actual material state is required")
    R._round_hole_inventory(state)
    layers = [r for r in state["geometry"]["stackup"] if r.get("kind") == "conductor"]
    if (
        not _configuration_complete(config, state["geometry"]["model"]["layers"])
        or len(layers) != state["geometry"]["model"]["layers"]
        or len(
            [r for r in layers if r.get("layer") == request["layer"] and r.get("usage") == "signal"]
        )
        != 1
        or (
            not request.get("select_active_layer", False)
            and config["active_route_layer"] != request["layer"]
        )
        or len(config["route_layers"]) != len(layers)
        or not any(
            r["layer"] == request["layer"]
            and r["editor_enabled"] is True
            and r["document_enabled"] is True
            for r in config["route_layers"]
        )
    ):
        raise ValueError(
            "Actual target signal layer and complete active configuration are required"
        )
    if config["active_mode"] != 1:
        raise ValueError(
            "In the actual Layout GUI, select Route working mode before a new preview; "
            "this finite General Route path requires observed IMGCPCBGui.ActiveMode=1 "
            "(EPcbMode Route), not a guarantee of router output"
        )
    components = _components(state, request)
    before_count = len(components["components"])
    if (
        before_count not in (2, 3)
        or target["opens"] != before_count - 1
        or target["routed"] is not False
    ):
        raise ValueError("Known two/three actual GND groups and corresponding opens are required")
    if request["expected_components_after"] >= before_count:
        raise ValueError(
            "One declared task must merge the selected groups or the complete target net"
        )
    pair = set(request["endpoint_pin_uids"])
    if any(sum(uid in c["pin_ids"] for c in components["components"]) != 1 for uid in pair):
        raise ValueError("Each requested pin must identify exactly one actual material group")
    selected_groups = [c for c in components["components"] if pair & set(c["pin_ids"])]
    if len(selected_groups) != 2 or not pair <= {p["pin_uid"] for p in pins}:
        raise ValueError(
            "Selected actual physical pins must belong to two different GND material groups"
        )
    candidates = [
        r
        for r in fromtos
        if r["net_uid"] == target["uid"]
        and r["net"] == "GND"
        and {r["nearest_from_pin"]["pin_uid"], r["nearest_to_pin"]["pin_uid"]} == pair
    ]
    if len(candidates) != 1:
        raise ValueError("Exactly one real unconnected FromTo must match the selected pin pair")
    selected = candidates[0]
    if selected["from_layer"] != request["layer"] or selected["to_layer"] != request["layer"]:
        raise ValueError("Actual FromTo endpoint layers do not match the declared signal layer")
    actual_groups = []
    for key, nearest in (("from_object", "nearest_from_pin"), ("to_object", "nearest_to_pin")):
        endpoint = selected[key]
        matching = [
            c
            for c in components["components"]
            if (
                endpoint.get("kind") == "pin"
                and endpoint.get("pin_uid") in c["pin_ids"]
                or endpoint.get("kind") == "trace"
                and endpoint.get("uid") in c["trace_ids"]
            )
        ]
        if (
            endpoint.get("net") != "GND"
            or endpoint.get("net_uid") != target["uid"]
            or len(matching) != 1
            or selected[nearest]["pin_uid"] not in matching[0]["pin_ids"]
            or (endpoint["kind"] == "trace" and endpoint["geometry"]["layer"] != request["layer"])
        ):
            raise ValueError(
                "Actual False FromTo endpoint does not bind exactly one target material group"
            )
        actual_groups.append(sorted(matching[0]["pin_ids"]))
    if sorted(actual_groups) != sorted(sorted(c["pin_ids"]) for c in selected_groups):
        raise ValueError(
            "Nearest-pin request and actual False FromTo endpoints refer to different "
            "material groups"
        )
    allowed = set(request["allow_replace_trace_uids"])
    allowed_raw = [r for r in raw if r["uid"] in allowed]
    if len(allowed_raw) != len(allowed) or any(
        r["net"] != "GND" or r["layer"] != request["layer"] or not _within(r, request["bounds_mm"])
        for r in allowed_raw
    ):
        raise ValueError(
            "Every allowed old target trace must bind its full material inside the declared bounds"
        )
    groups = [set(c["pin_ids"]) for c in components["components"]]
    merged = set().union(*(set(c["pin_ids"]) for c in selected_groups))
    expected_groups = (
        [sorted(set().union(*groups))]
        if request["expected_components_after"] == 1
        else [sorted(g) for g in groups if not g & pair] + [sorted(merged)]
    )
    result = {
        "guard": GUARD,
        "request": request,
        "before": copy.deepcopy(state),
        "target": {"net": "GND", "net_uid": target["uid"], "layer": request["layer"]},
        "components_before": components,
        "expected_pin_groups": sorted(expected_groups),
        "selected_fromto": copy.deepcopy(selected),
        "fromto_inventory": copy.deepcopy(fromtos),
        "fromto_inventory_sha256": digest(fromtos),
        "configuration": copy.deepcopy(config),
        "ui_layer_selection": {
            "requested": request.get("select_active_layer", False),
            "before": config["active_route_layer"],
            "target": request["layer"],
            "formal_property": "IMGCPCBGui.ActiveRouteLayer PUT16 flags0",
            "put_count": 1 if request.get("select_active_layer", False) else 0,
            "ui_preference_persistence": "not established; may persist",
        },
        "allowed_old_target_copper": copy.deepcopy(allowed_raw),
        "input_files": copy.deepcopy(inputs),
        "identity": copy.deepcopy(identity),
        "allowed_output_files": R._allowed_output_files(identity, inputs),
        "can_apply": True,
        "unit": "mm",
        "command": COMMAND,
        "execution_budget_seconds": EXECUTION_BUDGET_SECONDS,
        "limitations": [
            "Configuration getters do not guarantee General Route output",
            "Observed ActiveMode=1 is this finite path's measured precondition; "
            "it does not prove a vendor-wide requirement or successful routing",
            "General Route may consider the selected net; all actual changes are "
            "independently checked",
            "No complete effective-rule resolution, DRC or manufacturing claim",
        ],
    }
    result["fingerprint"] = _fingerprint(result)
    return result


def _fingerprint(plan):
    basis = copy.deepcopy(plan)
    basis.pop("fingerprint", None)
    basis["before"] = R.design_state(plan["before"])
    basis["input_files"] = R._dependency_identity(plan["input_files"])
    return digest(basis)


def _plans_equal(left, right):
    """Only recomputed before-mask receipts are derived, never raw design inputs."""
    if not R._states_equal(left["before"], right["before"], material_request(right["request"])):
        return False
    first, second = copy.deepcopy(left), copy.deepcopy(right)
    first["before"], second["before"] = (
        R.design_state(first["before"]),
        R.design_state(second["before"]),
    )
    return first == second


def _execution_configuration(plan):
    expected = copy.deepcopy(plan["configuration"])
    if plan["request"].get("select_active_layer", False):
        expected["active_route_layer"] = plan["request"]["layer"]
        for record in expected.get("read_log", []):
            if record["field"] == "active_route_layer":
                record["value"] = plan["request"]["layer"]
    return expected


def _put_active_layer(app, layer, a):
    # The installed151.0 IMGCPCBGui property16 is a supported PROPERTYPUT.
    # No constraints, EditorControl settings or global routing options are written.
    import pythoncom

    gui = a._propget(app, 12)
    return gui._oleobj_.Invoke(16, 0, pythoncom.DISPATCH_PROPERTYPUT, 0, layer)


def _protected_state(state, allowed):
    result = R.design_state(state)
    result["routing_objects"] = [r for r in result["routing_objects"] if r["uid"] not in allowed]
    result["native_trace_material"] = [
        r for r in result["native_trace_material"] if r["uid"] not in allowed
    ]
    geometries = [r["geometry"] for r in state["routing_objects"] if r["uid"] in allowed]
    for geometry in geometries:
        result["geometry"]["model"]["traces"].remove(geometry)
    for key in ("connectivity", "unrouted"):
        result["geometry"]["model"].pop(key)
    return result


def compare(plan, after):
    """Accept only the declared material-group merge and exact protected state."""
    checks = []

    def check(name, passed, category="protection"):
        checks.append({"name": name, "passed": bool(passed), "category": category})

    try:
        request, before = plan["request"], plan["before"]
        validate(request)
        rebound = bind(
            before,
            request,
            plan["input_files"],
            plan["configuration"],
            plan["fromto_inventory"],
            plan["identity"],
            None,
        )
        check(
            "frozen target material, selection, configuration and permission contract recomputes",
            rebound == plan,
        )
        old, new = R._inventory(before["routing_objects"]), R._inventory(after["routing_objects"])
        raw_old, raw_new = R._raw_trace_rows(before), R._raw_trace_rows(after)
        permitted = set(request["allow_replace_trace_uids"])
        old_uids = {r["uid"] for r in old.values()}
        created = [r for r in new.values() if r["uid"] not in old_uids]
        new_uids = {r["uid"] for r in created}
        changed = [r for k, r in new.items() if k in old and r != old[k]]
        deleted = [r for k, r in old.items() if k not in new]
        check(
            "all unpermitted old routing identities and full geometry remain exact",
            all(new.get(k) == row for k, row in old.items() if row["uid"] not in permitted),
        )
        check(
            "only declared old GND copper may change or disappear",
            all(r["uid"] in permitted for r in deleted + changed),
        )
        affected = [r for r in raw_new if r["uid"] in permitted | new_uids]
        check(
            "all affected copper is target GND on the declared layer and within material bounds",
            all(
                r["kind"] == "trace"
                and r["net"] == "GND"
                and r["geometry"]["net"] == "GND"
                and r["geometry"]["layer"] == request["layer"]
                for r in created + changed
            )
            and all(
                r["net"] == "GND"
                and r["layer"] == request["layer"]
                and r["net_uid"] == plan["target"]["net_uid"]
                and _within(r, request["bounds_mm"])
                for r in affected
            ),
        )
        check(
            "all unpermitted raw paths widths units layers and net identities remain exact",
            all(
                next((v for v in raw_new if v["uid"] == r["uid"]), None) == r
                for r in raw_old
                if r["uid"] not in permitted
            ),
        )
        check(
            "all non-target geometry pins pads drills text rules and other state remain exact",
            _protected_state(before, permitted) == _protected_state(after, permitted | new_uids),
        )
        old_nets, new_nets = R._nets(before), R._nets(after)
        check(
            "all net identities and every other network remain exact",
            {k: r["uid"] for k, r in old_nets.items()} == {k: r["uid"] for k, r in new_nets.items()}
            and {k: r for k, r in old_nets.items() if k != "GND"}
            == {k: r for k, r in new_nets.items() if k != "GND"},
        )
        evidence = M.verify_evidence(after, material_request(request))
        check(
            "all fresh native masks and foreign material separation are complete",
            evidence.get("valid") is True,
        )
        observed = _components(after, request)
        groups = sorted(sorted(c["pin_ids"]) for c in observed["components"])
        target_traces = {r["uid"] for r in raw_new if r["net"] == "GND"}
        check(
            "every target copper object and all six actual pins participate in the material graph",
            {uid for c in observed["components"] for uid in c["trace_ids"]} == target_traces,
            "completion",
        )
        check(
            "only the planned two actual groups merge with no omitted pin or extra component",
            groups == plan["expected_pin_groups"]
            and len(groups) == request["expected_components_after"],
            "completion",
        )
        expected = {**old_nets["GND"], "opens": len(groups) - 1, "routed": len(groups) == 1}
        check(
            "native opens/routed are independent crosschecks of the complete material groups",
            new_nets["GND"] == expected,
            "completion",
        )
        check(
            "complete native unrouted projection remains accurate",
            after["geometry"]["model"]["unrouted"]
            == [r for r in new_nets.values() if not r["routed"] or r["opens"] != 0],
        )
        for state in (before, after):
            for kind in ("trace", "via"):
                check(
                    "complete model/native " + kind + " inventory",
                    sorted(
                        digest(r["geometry"]) for r in state["routing_objects"] if r["kind"] == kind
                    )
                    == sorted(digest(r) for r in state["geometry"]["model"][kind + "s"]),
                )
        protection = all(r["passed"] for r in checks if r["category"] == "protection")
        completion = all(r["passed"] for r in checks if r["category"] == "completion")
        return {
            "valid": protection and completion,
            "protection_valid": protection,
            "completion_valid": completion,
            "whole_net_complete": len(groups) == 1,
            "checks": checks,
            "material_components": observed,
            "created_objects": created,
            "deleted_objects": deleted,
            "changed_old_objects": changed,
            "raw_affected_copper": affected,
            "native_mask_evidence": evidence,
            "native_connectivity": new_nets["GND"],
            "drc_executed": False,
        }
    except (KeyError, TypeError, ValueError, IndexError) as error:
        return {
            "valid": False,
            "protection_valid": False,
            "completion_valid": False,
            "checks": checks,
            "reason": str(error),
            "whole_net_complete": False,
        }


def _record_call(result, a, name, function, *, kind, arguments=None):
    locked = win_dialogs.desktop_locked()
    result.setdefault("desktop_lock_checks", []).append({"before_call": name, "locked": locked})
    if locked is not False:
        raise a.AdapterError(
            "E_HUMAN_REQUIRED",
            "Desktop is locked; dependent native action stopped",
            {"stage": name, "write_attempted": result.get("write_attempted", False)},
        )
    if name == "Gui.ActiveRouteLayer":
        result["ui_write_attempted"] = True
    elif name in {"UnSelectAll", "FromTo.Selected"}:
        result["selection_write_attempted"] = True
    elif name == "ProcessCommand":
        result.update(
            write_attempted=True,
            engineering_write_dispatched=True,
            change_status="unknown",
            persistence_state="unknown",
        )
    row = {
        "call": name,
        "sequence": len(result["native_calls_returned"]) + 1,
        "dispatched": True,
        "returned": False,
        "arguments": arguments,
        "started_at": datetime.now(UTC).isoformat(),
    }
    result["native_calls_returned"].append(row)
    started = time.monotonic()
    try:
        value = a._native_call("general_route." + name, function)
    finally:
        row["elapsed_seconds"] = time.monotonic() - started
    row.update(
        returned=True,
        return_kind="none"
        if value is None
        else "true"
        if value is True
        else "false"
        if value is False
        else type(value).__name__,
    )
    if (kind == "void" and value is not None) or (kind == "bool" and value is not True):
        raise ValueError(name + " did not satisfy formal " + kind + " return contract")
    return value, row


def _fresh(doc, app, board, a, request, *, verified_state=None):
    authenticated = a._licensed_document(app)
    if Path(str(authenticated.FullName)).resolve() != board.resolve():
        raise ValueError("Fresh authenticated actual document path differs")
    if verified_state is None:
        return R.observe(authenticated, a, material_request(request))
    return R.observe_for_save(authenticated, a, material_request(request), verified_state)


def _material_phase(timings, name, function, *, reuse_state=None):
    row = {
        "phase": name,
        "started_at": datetime.now(UTC).isoformat(),
        "complete": False,
        "basis": "full_fresh_native_material"
        if reuse_state is None
        else "fresh_all_raw_exact_verified_mask_reuse",
    }
    if reuse_state is not None:
        row["verified_raw_state_sha256"] = digest(R.design_state(reuse_state))
    timings.append(row)
    started = time.monotonic()
    try:
        observed = function()
        row["complete"] = True
        return observed
    finally:
        row["elapsed_seconds"] = time.monotonic() - started


def execute(params, client, a):
    """One formal selected-netline command, fresh verification, conditional persistence."""
    try:
        request = validate(params["general_request"])
    except (KeyError, TypeError, ValueError) as error:
        raise a.AdapterError("E_VALIDATION", str(error), {"write_attempted": False}) from error
    state, inputs, config, native, identity = None, None, None, None, None
    timings = []
    try:
        _, board, app, doc = active(params, client, a, require_project=False, quiet=False)
        identity = board_labels._identity(app, board)
        config = observe_configuration(doc, None, app, a)
        state = _material_phase(
            timings, "initial_material", lambda: R.observe(doc, a, material_request(request))
        )
        inputs = R._files(params, board)
        if config["layer_count"] != state["geometry"]["model"]["layers"]:
            raise ValueError(
                "Initial configuration LayerCount differs from complete actual stackup"
            )
        native = observe_fromtos(doc, state, a)
        plan = bind(state, request, inputs, config, [r[0] for r in native], identity, a)
        if board_labels._identity(app, board) != identity:
            raise a.AdapterError("E_CONFLICT", "General Route identity changed during preflight")
    except Exception as error:
        raise a.AdapterError(
            getattr(
                error,
                "code",
                "E_UNSUPPORTED" if isinstance(error, ValueError) else "E_NATIVE_FAILURE",
            ),
            "General Route preflight incomplete; no selection or command dispatched",
            {
                "write_attempted": False,
                "cause": str(error),
                "preflight_observation": {
                    "state": state,
                    "input_files": inputs,
                    "identity": identity,
                    "configuration": getattr(error, "configuration_observation", config),
                    "fromto_inventory": [r[0] for r in native] if native is not None else None,
                    "fromto_read_error": getattr(error, "fromto_observation", None),
                    "phase_timings": timings,
                },
            },
        ) from error
    result = {
        "pcb": str(board),
        "mode": "bounded-general",
        "general_route": plan,
        "state_guard": GUARD,
        "state_digest": plan["fingerprint"],
        "write_attempted": False,
        "engineering_write_dispatched": False,
        "ui_write_attempted": False,
        "ui_write_acknowledged": False,
        "selection_write_attempted": False,
        "applied": False,
        "saved": False,
        "persistence_verified": False,
        "change_status": "not_applied",
        "persistence_state": "unchanged",
        "native_calls_returned": [],
        "phase_timings": timings,
        "execution_budget_seconds": EXECUTION_BUDGET_SECONDS,
        "observations": [],
        "stages": dict.fromkeys(
            ("selection", "route", "readback", "protection", "save", "close", "reopen"),
            "not_started",
        ),
        "reminders_before": R._layout_advisories(state),
    }
    if not params.get("apply"):
        return result
    if params.get("expected") != plan["fingerprint"]:
        raise a.AdapterError(
            "E_CONFLICT",
            "General Route frozen inputs or selection drifted",
            {"write_attempted": False},
        )
    try:
        # Selection is explicitly dispatched only on confirmation, never preview.
        current = _material_phase(
            timings,
            "before_selection_raw",
            lambda: _fresh(doc, app, board, a, request, verified_state=state),
            reuse_state=state,
        )
        if (
            not R._states_equal(current, state, material_request(request))
            or board_labels._identity(app, board) != identity
        ):
            raise ValueError("Actual complete state or identity drifted before selection")
        if R._dependency_identity(R._files(params, board)) != R._dependency_identity(inputs):
            raise ValueError("Actual source/dependencies drifted before selection")
        execution_config = _execution_configuration(plan)
        if request.get("select_active_layer", False):
            before_ui = observe_configuration(doc, state["geometry"]["model"]["layers"], app, a)
            if before_ui != config or board_labels._identity(app, board) != identity:
                raise ValueError("Actual UI/configuration identity drifted before layer selection")
            _record_call(
                result,
                a,
                "Gui.ActiveRouteLayer",
                lambda: _put_active_layer(app, request["layer"], a),
                kind="void",
                arguments=request["layer"],
            )
            result["ui_write_acknowledged"] = True
            result["configuration_after_ui"] = observe_configuration(
                doc, state["geometry"]["model"]["layers"], app, a
            )
            if result["configuration_after_ui"] != execution_config:
                raise ValueError(
                    "Active layer selection did not read back with all other settings exact"
                )
        result["stages"]["selection"] = "started"
        _record_call(result, a, "UnSelectAll", doc.UnSelectAll, kind="void")
        selected = [
            r
            for r in observe_fromtos(doc, state, a)
            if r[0]["identity_sha256"] == plan["selected_fromto"]["identity_sha256"]
        ]
        if len(selected) != 1:
            raise ValueError("Fresh actual FromTo is no longer unique")
        _record_call(
            result,
            a,
            "FromTo.Selected",
            lambda: setattr(selected[0][1], "Selected", True),
            kind="void",
            arguments=True,
        )
        actual = observe_fromtos(doc, state, a, selected=True)
        if (
            len(actual) != 1
            or actual[0][0]["identity_sha256"] != plan["selected_fromto"]["identity_sha256"]
        ):
            raise ValueError("Complete selected FromTo inventory differs")
        result["selected_fromto_evidence"] = [r[0] for r in actual]
        full_fromtos = [r[0] for r in observe_fromtos(doc, state, a)]
        if sorted(r["identity_sha256"] for r in full_fromtos) != sorted(
            r["identity_sha256"] for r in plan["fromto_inventory"]
        ):
            raise ValueError("Actual whole FromTo inventory drifted before command")
        result["fromto_inventory_before_command"] = full_fromtos
        current = _material_phase(
            timings,
            "before_command_raw",
            lambda: _fresh(doc, app, board, a, request, verified_state=state),
            reuse_state=state,
        )
        fresh_cfg = observe_configuration(doc, state["geometry"]["model"]["layers"], app, a)
        if (
            not R._states_equal(current, state, material_request(request))
            or fresh_cfg != execution_config
            or board_labels._identity(app, board) != identity
            or R._dependency_identity(R._files(params, board)) != R._dependency_identity(inputs)
        ):
            raise ValueError(
                "Actual design/configuration/identity/dependencies drifted before command"
            )
        result["state_before_command"] = current
        result["configuration_before_command"] = fresh_cfg
        # This is deliberately last: the full material read can take minutes.
        # UnSelectAll returning void is not evidence that all other inputs cleared.
        result["selection_before_command"] = observe_selection(doc, current, a)
        if not _selection_allowed(result["selection_before_command"], plan):
            raise ValueError(
                "Actual last selected-object inventory is not the single bound GND FromTo"
            )
        result["stages"]["selection"], result["stages"]["route"] = "verified", "started"
        _record_call(
            result,
            a,
            "ProcessCommand",
            lambda: app.Gui.ProcessCommand(COMMAND, True),
            kind="bool",
            arguments=[COMMAND, True],
        )
        result["stages"]["route"] = "returned"
        result["stages"]["readback"] = "started"
        current = _material_phase(
            timings, "post_command_material", lambda: _fresh(doc, app, board, a, request)
        )
        result.update(
            state_after=current,
            applied=R.design_state(current) != R.design_state(state),
            change_status="partial"
            if R.design_state(current) != R.design_state(state)
            else "not_applied",
            reminders_after=R._layout_advisories(current),
        )
        after_inputs = R._files(params, board)
        result["post_route_input_observation"] = after_inputs
        result["dependency_change_evidence"] = R.dependency_changes(
            inputs, after_inputs, plan["allowed_output_files"]
        )
        result["configuration_after_command"] = observe_configuration(
            doc, state["geometry"]["model"]["layers"], app, a
        )
        result["stages"]["readback"] = "complete"
        proof = compare(plan, current)
        result["verification"] = proof
        if (
            not proof["valid"]
            or not result["dependency_change_evidence"]["valid"]
            or result["configuration_after_command"] != execution_config
            or board_labels._identity(app, board) != identity
        ):
            result["stages"]["protection"] = "failed"
            raise ValueError("General Route output/protection incomplete; Save/Close/Open stopped")
        result["stages"]["protection"] = "passed"
        before_save = _material_phase(
            timings,
            "before_save_raw",
            lambda: _fresh(doc, app, board, a, request, verified_state=current),
            reuse_state=current,
        )
        before_save_inputs = R._files(params, board)
        result["state_before_save"] = before_save
        result["pre_save_input_observation"] = before_save_inputs
        if (
            not R._states_equal(before_save, current, material_request(request))
            or R._dependency_identity(before_save_inputs) != R._dependency_identity(after_inputs)
            or observe_configuration(doc, state["geometry"]["model"]["layers"], app, a)
            != execution_config
            or board_labels._identity(app, board) != identity
        ):
            raise ValueError("Fresh complete inputs changed before Save")
        result["stages"]["save"] = "started"
        _record_call(result, a, "Save", doc.Save, kind="bool")
        result["saved"], result["stages"]["save"] = True, "acknowledged"
        result["stages"]["close"] = "started"
        _, event = _record_call(
            result, a, "Close", lambda: doc.Close(True), kind="bool", arguments=True
        )
        event["active_document_none"] = app.ActiveDocument is None
        if event["active_document_none"] is not True:
            raise ValueError("Actual ActiveDocument is not empty after acknowledged Close")
        result["stages"]["close"] = "confirmed"
        closed_identity = board_labels._application_identity(app)
        result["post_close_identity"] = {
            **closed_identity,
            "active_document_none": app.ActiveDocument is None,
            "closed_board": str(board.resolve()),
        }
        if (
            closed_identity != {key: plan["identity"][key] for key in ("application", "generation")}
            or result["post_close_identity"]["active_document_none"] is not True
        ):
            raise ValueError("Post-Close actual application identity changed; Open stopped")
        result["stages"]["reopen"] = "started"
        opened, event = _record_call(
            result, a, "Open", lambda: a._open_layout_document(app, board), kind="open"
        )
        if not isinstance(opened, tuple) or len(opened) != 2 or opened[0] is None:
            raise ValueError("Open did not return document and prompts")
        event["return_kind"] = "document_and_prompts"
        reopened = _material_phase(
            timings, "reopened_material", lambda: _fresh(None, app, board, a, request)
        )
        result["state_reopened"] = reopened
        result["reopen_evidence"] = {
            "authenticated": True,
            "board": str(board.resolve()),
            "identity": board_labels._identity(app, board),
            "state_sha256": digest(reopened),
            "observed_after_open_sequence": event["sequence"],
        }
        result["persistence_verified"] = (
            R._states_equal(reopened, current, material_request(request))
            and compare(plan, reopened)["valid"]
            and result["reopen_evidence"]["identity"] == identity
        )
        result["stages"]["reopen"] = "verified" if result["persistence_verified"] else "mismatch"
        if (
            not result["persistence_verified"]
            or not verify_receipt(result, plan, reopened)["valid"]
        ):
            raise ValueError("General Route persistence evidence is incomplete")
        result.update(change_status="applied", persistence_state="saved")
        return result
    except Exception as error:
        result["post_write_error"] = {
            "cause": type(error).__name__,
            "message": str(error),
            "code": getattr(error, "code", "E_NATIVE_FAILURE"),
        }
        raise a.AdapterError(
            "E_HUMAN_REQUIRED"
            if getattr(error, "code", None) == "E_HUMAN_REQUIRED"
            else "E_VERIFY_INCOMPLETE"
            if result["write_attempted"]
            else "E_CONFLICT",
            "Selected General Route lifecycle incomplete; retain facts and do not replay",
            {"write_attempted": result["write_attempted"], "native_result": result},
        ) from error


def verify_receipt(result, plan, state):
    try:
        expected = [
            ("UnSelectAll", "none"),
            ("FromTo.Selected", "none"),
            ("ProcessCommand", "true"),
            ("Save", "true"),
            ("Close", "true"),
            ("Open", "document_and_prompts"),
        ]
        select_layer = plan["request"].get("select_active_layer", False)
        if select_layer:
            expected.insert(0, ("Gui.ActiveRouteLayer", "none"))
        calls = result["native_calls_returned"]
        sequence = len(calls) == len(expected) and all(
            row["call"] == name
            and row["return_kind"] == kind
            and row["dispatched"] is True
            and row["returned"] is True
            and type(row["sequence"]) is int
            and row["sequence"] == index + 1
            for index, (row, (name, kind)) in enumerate(zip(calls, expected, strict=True))
        )
        by_name = {row["call"]: row for row in calls}
        command_call, close_call = by_name["ProcessCommand"], by_name["Close"]
        actual_config = _execution_configuration(plan)
        dependencies = R.dependency_changes(
            plan["input_files"],
            result["post_route_input_observation"],
            plan["allowed_output_files"],
        )
        phases = [
            "initial_material",
            "before_selection_raw",
            "before_command_raw",
            "post_command_material",
            "before_save_raw",
            "reopened_material",
        ]
        expected_basis = [
            "full_fresh_native_material",
            "fresh_all_raw_exact_verified_mask_reuse",
            "fresh_all_raw_exact_verified_mask_reuse",
            "full_fresh_native_material",
            "fresh_all_raw_exact_verified_mask_reuse",
            "full_fresh_native_material",
        ]
        timings = result["phase_timings"]
        timing_ok = (
            result["execution_budget_seconds"]
            == plan["execution_budget_seconds"]
            == EXECUTION_BUDGET_SECONDS
            and [r["phase"] for r in timings] == phases
            and [r["basis"] for r in timings] == expected_basis
            and all(
                r["complete"] is True
                and type(r["elapsed_seconds"]) in (int, float)
                and math.isfinite(r["elapsed_seconds"])
                and r["elapsed_seconds"] >= 0
                for r in timings
            )
            and sum(r["elapsed_seconds"] for r in timings) <= EXECUTION_BUDGET_SECONDS
            and timings[1]["verified_raw_state_sha256"]
            == timings[2]["verified_raw_state_sha256"]
            == digest(R.design_state(plan["before"]))
            and timings[4]["verified_raw_state_sha256"]
            == digest(R.design_state(result["state_after"]))
        )
        checks = [
            timing_ok,
            result["post_close_identity"]
            == {
                "application": plan["identity"]["application"],
                "generation": plan["identity"]["generation"],
                "active_document_none": True,
                "closed_board": result["pcb"],
            },
            sequence
            and by_name["FromTo.Selected"]["arguments"] is True
            and command_call["arguments"] == [COMMAND, True]
            and command_call["arguments"][1] is True
            and close_call["arguments"] is True
            and close_call["active_document_none"] is True,
            [row["before_call"] for row in result["desktop_lock_checks"]]
            == [name for name, _ in expected]
            and all(row["locked"] is False for row in result["desktop_lock_checks"]),
            result["engineering_write_dispatched"] is True
            and result["selection_write_attempted"] is True
            and result["ui_write_attempted"] is select_layer
            and result["ui_write_acknowledged"] is select_layer
            and (
                not select_layer
                or by_name["Gui.ActiveRouteLayer"]["arguments"] == plan["request"]["layer"]
                and result["configuration_after_ui"] == actual_config
            ),
            result["state_guard"] == GUARD
            and result["state_digest"] == plan["fingerprint"]
            and result["write_attempted"] is True
            and result["applied"] is True
            and _plans_equal(result["general_route"], plan)
            and _fingerprint(plan) == plan["fingerprint"],
            R._states_equal(
                result["state_before_command"], plan["before"], material_request(plan["request"])
            ),
            result["configuration_before_command"]
            == result["configuration_after_command"]
            == actual_config,
            result["selected_fromto_evidence"][0]["identity_sha256"]
            == plan["selected_fromto"]["identity_sha256"]
            and len(result["selected_fromto_evidence"]) == 1,
            _selection_allowed(result["selection_before_command"], plan),
            sorted(r["identity_sha256"] for r in result["fromto_inventory_before_command"])
            == sorted(r["identity_sha256"] for r in plan["fromto_inventory"]),
            dependencies["valid"]
            and R._dependency_identity(result["post_route_input_observation"])
            == R._dependency_identity(result["pre_save_input_observation"]),
            all(
                compare(plan, item)["valid"]
                for item in (state, result["state_after"], result["state_before_save"])
            ),
            R._states_equal(
                result["state_after"],
                result["state_before_save"],
                material_request(plan["request"]),
            )
            and R._states_equal(result["state_after"], state, material_request(plan["request"])),
            R._states_equal(result["state_reopened"], state, material_request(plan["request"])),
            result["reopen_evidence"]
            == {
                "authenticated": True,
                "board": result["pcb"],
                "identity": plan["identity"],
                "state_sha256": digest(result["state_reopened"]),
                "observed_after_open_sequence": len(expected),
            },
            result["saved"] is True and result["persistence_verified"] is True,
            result["stages"]
            == {
                "selection": "verified",
                "route": "returned",
                "readback": "complete",
                "protection": "passed",
                "save": "acknowledged",
                "close": "confirmed",
                "reopen": "verified",
            },
        ]
        return {
            "valid": all(checks),
            "checks": checks,
            "whole_net_complete": compare(plan, state).get("whole_net_complete", False),
        }
    except (KeyError, ValueError, TypeError, IndexError) as error:
        return {"valid": False, "reason": str(error)}
