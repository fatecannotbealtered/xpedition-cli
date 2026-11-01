"""Bound explicit trace observations and fail-closed routing lifecycle.

The production 151.0 reader cannot establish local rule applicability or a
bounded generated-copper change region. Its preview therefore always declares
the relevant blockers and its confirmation cannot reach PutTrace/Save/Close/Open.
The guarded lifecycle is independently exercised with synthetic observations;
those tests are not a native eligibility source or a public override.
"""

from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path

from . import board_unroute
from .cell_refresh import active, digest
from .endpoint_material import observe_pin, physical_connection


def limitations():
    return {
        "supported_request": "one explicit trace, one named net and signal layer; mm path/width",
        "can_apply": False,
        "copper_impact": (
            "Existing draft shapes, generated copper or plane assignments are refused. "
            "A trace may legally change copper clearances; no bounded allowed region and "
            "outside-region protection proof has been established."
        ),
        "rules": (
            "Observed Master widths/clearances do not prove active or location-dependent "
            "rule applicability. Confirmation is blocked until that dependency is proven."
        ),
        "physical_scope": (
            "Endpoint pad ownership and native opens are distinct observations. "
            "No full thermal/tie material, clearance or DRC conclusion follows."
        ),
        "broader_routes": "autorouter, standalone via and mixed-plan apply are disabled",
    }


def refuse_broad(a, operation):
    raise a.AdapterError(
        "E_UNSUPPORTED",
        "This routing path has no bounded impact/protection proof; use the explicit trace preview",
        {"operation": operation, "write_attempted": False, "routing_boundary": limitations()},
    )


def _collection(owner, name, a):
    value = getattr(owner, name)
    if callable(value) and not hasattr(value, "Count"):
        value = value()
    if type(value.Count) is not int or not 0 <= value.Count <= 10000:
        raise ValueError("Native collection count is incomplete or unbounded: " + name)
    return list(a._items(value, strict=True))


def _uid(value):
    if type(value) not in (int, str) or not str(value).strip() or str(value) == "0":
        raise ValueError("Native identity is unavailable")
    return str(value)


def plane_inventory(doc, a):
    """No hidden GeneratedPlane UID and no state setters/regeneration."""
    result = {"shapes": [], "generated": [], "assignments": []}
    for key, name in (
        ("shapes", "PlaneShapes"),
        ("generated", "GeneratedPlanes"),
        ("assignments", "PlaneAssignments"),
    ):
        for obj in _collection(doc, name, a):
            row = {"net": str(obj.Net.Name), "net_uid": _uid(obj.Net.UniqueId)}
            if key == "assignments":
                row.update(layer=int(obj.LayerObject.ConductorLayer), state=int(obj.PlaneDataState))
            else:
                row["layer"] = int(obj.Layer)
                if key == "shapes":
                    row["uid"] = _uid(obj.UniqueId)
            if not row["net"] or row["layer"] < 1:
                raise ValueError("Plane ownership is incomplete")
            result[key].append(row)
    result["complete"] = True
    result["generated_identity_basis"] = "native net UID and layer; no hidden persistent UID"
    return result


def _input_files(params, board):
    paths = sorted({Path(str(params["project"])).resolve(), board.resolve()})
    return [
        {"path": str(p), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for p in paths
        for data in [p.read_bytes()]
    ]


def observe(doc, a, item, *, location_scheme=True, _capture_stage=None):
    """Strict core geometry and native routing identities, without opening a target."""
    geometry = None
    if _capture_stage is not None:
        from .native_observation_stage import material

        geometry = material(_capture_stage, doc, a, kind="complete_capture")
    state = board_unroute.observe(doc, a, geometry)
    inventory = plane_inventory(doc, a)
    names = [r["net"] for r in state["geometry"]["model"]["connectivity"]["nets"]]
    pin_identities, materials = [], []
    for comp in _collection(doc, "Components", a):
        for pin in _collection(comp, "Pins", a):
            if pin.Net is not None:
                pin_identities.append(
                    {
                        "component_uid": _uid(comp.UniqueId),
                        "refdes": str(comp.RefDes),
                        "pin": str(pin.Name),
                        "pin_uid": _uid(pin.UniqueId),
                        "net": str(pin.Net.Name),
                        "net_uid": _uid(pin.Net.UniqueId),
                    }
                )
                if str(pin.Net.Name) == item["net"]:
                    materials.append(
                        {**pin_identities[-1], "material": observe_pin(pin, item["layer"], a)}
                    )
    net_class_observation = {}
    net_classes = a._board_net_classes(doc, int(doc.LayerCount), observation=net_class_observation)
    rules = {
        "scheme": "(Master)",
        "active_scheme_verified": False,
        "local_applicability_verified": False,
        "net_classes": net_classes,
        "net_class_observation": net_class_observation,
        "clearances": None,
        "not_checked": [
            "active and location-dependent schemes",
            "complete constraint database/dependency identities",
            "via/plane and general-object clearance rules",
        ],
    }
    if 2 <= len(names) <= 8:
        rules["clearances"] = a._selected_clearances(doc, names, [item["layer"]])
    elif len(names) > 8:
        rules["not_checked"].append("clearance matrix exceeds bounded 8-net observation")
    from .routing_rule_observation import observe as observe_location_scheme

    rules["location_scheme_observation"] = (
        observe_location_scheme(doc, item)
        if location_scheme
        else {
            "status": "not_requested",
            "reason": "The native engine has not generated a path; "
            "a sampled scheme cannot prove whole-path applicability",
        }
    )
    state.update(
        plane_inventory=inventory,
        rules=rules,
        pin_identities=sorted(
            pin_identities, key=lambda row: (row["component_uid"], row["pin_uid"])
        ),
        endpoint_pad_material=sorted(
            materials, key=lambda row: (row["component_uid"], row["pin_uid"])
        ),
    )
    return state


def _endpoints(state, item, a):
    geometry = state["geometry"]
    targets = []
    for point in (item["points"][0], item["points"][-1]):
        matches = []
        for comp in geometry["components"]:
            for pin in comp.get("geometry", []):
                if pin.get("net") != item["net"] or list(point) != pin.get("center_mm"):
                    continue
                pads = [p for p in pin.get("copper", []) if p.get("layer") == item["layer"]]
                if not pads or any(not p.get("geometry") for p in pads):
                    continue
                identities = [
                    r
                    for r in state["pin_identities"]
                    if r["component_uid"] == str(comp["uid"])
                    and r["refdes"] == comp["refdes"]
                    and r["pin"] == pin["number"]
                    and r["net"] == pin["net"]
                ]
                nets = [
                    r for r in geometry["model"]["connectivity"]["nets"] if r["net"] == pin["net"]
                ]
                if (
                    len(identities) != 1
                    or len(nets) != 1
                    or identities[0]["net_uid"] != nets[0]["uid"]
                ):
                    continue
                endpoint = {
                    "component_uid": _uid(comp["uid"]),
                    "refdes": comp["refdes"],
                    "pin": pin["number"],
                    "net": pin["net"],
                    "net_uid": identities[0]["net_uid"],
                    "pin_uid": identities[0].get("pin_uid"),
                    "center_mm": list(pin["center_mm"]),
                    "pads": copy.deepcopy(pads),
                    "holes": copy.deepcopy(pin.get("holes")),
                    "identity_basis": "native component UID/pin name and owned pad contours",
                    "physical_contact_verified": False,
                }
                materials = [
                    r["material"]
                    for r in state.get("endpoint_pad_material", [])
                    if r["component_uid"] == str(comp["uid"])
                    and r["pin"] == pin["number"]
                    and r["net_uid"] == identities[0]["net_uid"]
                    and r.get("pin_uid") == identities[0].get("pin_uid")
                ]
                if len(materials) == 1:
                    endpoint["material_observation"] = copy.deepcopy(materials[0])
                    if materials[0].get("complete") is True:
                        endpoint["captured_pads"] = copy.deepcopy(endpoint["pads"])
                        endpoint["pads"] = copy.deepcopy(materials[0]["pads"])
                        endpoint["holes"] = copy.deepcopy(materials[0]["holes"])
                        endpoint["material_source"] = materials[0]["source"]
                matches.append(endpoint)
        if len(matches) != 1:
            raise a.AdapterError(
                "E_UNSUPPORTED",
                "Each trace endpoint must match one physical pin center with observed layer pads",
                {"point_mm": list(point), "matches": len(matches), "write_attempted": False},
            )
        targets.append(matches[0])
    if targets[0] == targets[1]:
        raise a.AdapterError("E_VALIDATION", "Trace endpoints must be different physical pins")
    return targets


def _positive_finite_width(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _named_width_comparison(rules, item, net_uid):
    """Compare an observed named minimum, without resolving any effective local rule."""
    result = {
        "status": "unknown",
        "source_member": "NetClass.MinTraceWidth",
        "source_dispid": 7,
        "source_invoke_kind": "PROPERTYGET",
        "source_signature": "(nLayer:int,sScheme:BSTR,eUnit:EPcbUnit) -> double",
        "scheme": rules.get("scheme") if isinstance(rules.get("scheme"), str) else None,
        "net": item["net"],
        "net_uid": net_uid,
        "class_name": None,
        "class_observation_indices": [],
        "membership_complete": False,
        "membership_observation": rules.get("net_class_observation"),
        "class_identity_basis": "observed class name and net membership; no persistent class UID",
        "layer": item["layer"],
        "unit": "mm",
        "native_unit_argument": 4,
        "requested_width_mm": item["width"] if _positive_finite_width(item["width"]) else None,
        "observed_minimum_width_mm": None,
        "delta_mm": None,
        "local_applicability_verified": False,
        "scope": (
            "named scheme/class/layer observation only; no current local compliance conclusion"
        ),
        "reasons": [],
    }
    if result["requested_width_mm"] is None:
        result["reasons"].append("the requested width is invalid or non-finite")
    if result["scheme"] != "(Master)":
        result["reasons"].append("the reader's named Master scheme is missing or ambiguous")
    classes = rules.get("net_classes")
    observation = rules.get("net_class_observation")
    membership_complete = isinstance(observation, dict) and all(
        observation.get(key) is True
        for key in ("complete", "enumeration_complete", "membership_complete")
    )
    if not membership_complete:
        result["reasons"].append("net-class collection or membership reading is incomplete")
    if not isinstance(classes, list):
        result["reasons"].append("net-class observations are missing")
        return result
    matches = []
    for index, row in enumerate(classes):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("nets"), list)
            or any(not isinstance(name, str) or not name for name in row["nets"])
            or not isinstance(row.get("membership_observation"), dict)
            or row["membership_observation"].get("complete") is not True
        ):
            membership_complete = False
            result["reasons"].append(
                f"net-class membership at observation index {index} is incomplete"
            )
            continue
        if item["net"] in row["nets"]:
            matches.append(row)
            result["class_observation_indices"].append(index)
            if row["nets"].count(item["net"]) != 1:
                result["reasons"].append("target net membership is repeated within its class")
    result["membership_complete"] = membership_complete
    if len(matches) != 1:
        result["reasons"].append("the target net has no unique observed named class")
        return result
    row = matches[0]
    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        result["reasons"].append("the observed class name is missing")
    else:
        result["class_name"] = name
    widths = row.get("widths")
    layer = widths.get(str(item["layer"])) if isinstance(widths, dict) else None
    minimum = layer.get("minimum") if isinstance(layer, dict) else None
    if not _positive_finite_width(minimum):
        result["reasons"].append("the named layer minimum width is missing, invalid or non-finite")
    else:
        result["observed_minimum_width_mm"] = minimum
    if result["reasons"]:
        return result
    requested = item["width"]
    result["delta_mm"] = requested - minimum
    relation = "below" if requested < minimum else "above" if requested > minimum else "equal"
    result["status"] = relation + "_observed_named_minimum"
    return result


def bind(state, item, inputs, a):
    geometry, inventory = state["geometry"], state["plane_inventory"]
    connectivity = geometry["model"]["connectivity"]
    rows = connectivity.get("nets", [])
    if (
        connectivity.get("complete") is not True
        or any(
            not r.get("uid")
            or type(r.get("opens")) is not int
            or r["opens"] < 0
            or type(r.get("routed")) is not bool
            for r in rows
        )
        or len({r["net"] for r in rows}) != len(rows)
        or len({r["uid"] for r in rows}) != len(rows)
    ):
        raise a.AdapterError("E_VERIFY_INCOMPLETE", "Complete unique net identities are required")
    matches = [r for r in rows if r["net"] == item["net"]]
    if len(matches) != 1:
        raise a.AdapterError("E_NOT_FOUND", "The target net has no unique observed identity")
    target_pins = [
        (str(comp["uid"]), pin["number"])
        for comp in geometry["components"]
        for pin in comp.get("geometry", [])
        if pin.get("net") == item["net"]
    ]
    if (
        len(target_pins) != 2
        or len(set(target_pins)) != 2
        or matches[0]["opens"] != 1
        or matches[0]["routed"] is not False
    ):
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "Trace connection requires exactly two physical pins and observed opens=1/routed=false",
            {"write_attempted": False, "target_connectivity": matches[0]},
        )
    layers = [
        r
        for r in geometry["stackup"]
        if r.get("kind") == "conductor" and r.get("layer") == item["layer"]
    ]
    if len(layers) != 1 or layers[0].get("usage") != "signal":
        raise a.AdapterError("E_UNSUPPORTED", "Explicit routing requires an observed signal layer")
    has_planes = any(inventory[k] for k in ("shapes", "generated", "assignments")) or bool(
        geometry["model"].get("planes")
    )
    blockers = []
    if has_planes:
        blockers.append(
            {
                "code": "generated_copper_impact_unproven",
                "required_evidence": [
                    "allowed changed copper objects, region and clearance criteria",
                    "unchanged generated copper outside that region and every other assignment",
                ],
            }
        )
    if not state["rules"].get("local_applicability_verified"):
        blockers.append(
            {
                "code": "local_rule_applicability_unproven",
                "required_evidence": [
                    "actual local width/clearance rules along the complete input path",
                    "complete required constraint dependencies and identities",
                ],
            }
        )
    result = {
        "before": copy.deepcopy(state),
        "target": {"net": item["net"], "net_uid": matches[0]["uid"], "layer": item["layer"]},
        "request": copy.deepcopy(item),
        "unit": "mm",
        "native_current_unit": 2,
        "endpoints": _endpoints(state, item, a),
        "target_contract": {
            "task": "connect two specified actual pin-owned layer pads with one explicit trace",
            "before_connectivity": copy.deepcopy(matches[0]),
            "expected_connectivity": {**matches[0], "opens": 0, "routed": True},
            "physical_scope": "supported owned pad material contact and exact new trace geometry",
            "pad_identity_basis": (
                "native pin UID and owned pad name/layer/geometry; "
                "no supported Pad UID available in inspected151.0 interface"
            ),
        },
        "input_files": inputs,
        "plane_inventory": inventory,
        "possible_affected_copper": copy.deepcopy(inventory),
        "authorized_regeneration": [],
        "allowed_copper_changes": [],
        "copper_impact_proven": not has_planes,
        "can_apply": not blockers,
        "blockers": blockers,
        "rules": state["rules"],
        "named_width_comparison": _named_width_comparison(state["rules"], item, matches[0]["uid"]),
        "protection": (
            "exact complete captured geometry, native routing and net identities, observed rules"
        ),
        "evidence_boundary": limitations(),
    }
    result["endpoint_contact_preflight"] = physical_connection(result)
    if not result["endpoint_contact_preflight"]["verified"]:
        blockers.append(
            {
                "code": "endpoint_material_contact_unproven",
                "required_evidence": [
                    result["endpoint_contact_preflight"].get("reason")
                    or "supported positive material contact at both actual endpoint pads"
                ],
            }
        )
    result["can_apply"] = not blockers
    result["fingerprint"] = digest(result)
    return result


def compare(plan, state, uid):
    """Allow exactly one returned trace; no geometry tolerance or copper regeneration."""
    try:
        return _compare_complete(plan, state, uid)
    except (KeyError, TypeError, ValueError, IndexError) as error:
        return {
            "valid": False,
            "protection_valid": False,
            "completion_valid": False,
            "completion_status": "incomplete",
            "checks": [
                {
                    "name": "complete trace readback",
                    "passed": False,
                    "category": "protection",
                    "reason": str(error),
                }
            ],
            "physical_contact_verified": False,
            "physical_connection": {
                "status": "incomplete",
                "verified": False,
                "reason": "complete bound/readback state is unavailable",
            },
            "native_connectivity_verified": False,
            "drc_executed": False,
        }


def _endpoint_contract(plan):
    endpoints = plan.get("endpoints")
    contract = plan.get("target_contract")
    if not isinstance(endpoints, list) or len(endpoints) != 2 or not isinstance(contract, dict):
        return False
    before, target = plan["before"], plan["target"]
    pins = [
        (str(c["uid"]), p["number"])
        for c in before["geometry"]["components"]
        for p in c.get("geometry", [])
        if p.get("net") == target["net"]
    ]
    if len(pins) != 2 or len(set(pins)) != 2:
        return False
    if {(e.get("component_uid"), e.get("pin")) for e in endpoints} != set(pins):
        return False
    for endpoint, point in zip(
        endpoints, (plan["request"]["points"][0], plan["request"]["points"][-1]), strict=True
    ):
        components = [
            c
            for c in before["geometry"]["components"]
            if str(c["uid"]) == endpoint.get("component_uid")
            and c["refdes"] == endpoint.get("refdes")
        ]
        if len(components) != 1:
            return False
        owned = [
            p
            for p in components[0].get("geometry", [])
            if p["number"] == endpoint.get("pin") and p["net"] == target["net"]
        ]
        identities = [
            p
            for p in before["pin_identities"]
            if p["component_uid"] == endpoint.get("component_uid")
            and p["pin"] == endpoint.get("pin")
            and p["net"] == target["net"]
            and p["net_uid"] == target.get("net_uid")
            and p.get("pin_uid") == endpoint.get("pin_uid")
        ]
        if (
            len(owned) != 1
            or len(identities) != 1
            or not endpoint.get("pin_uid")
            or owned[0]["center_mm"] != endpoint.get("center_mm")
            or list(point) != endpoint.get("center_mm")
            or endpoint.get("net") != target["net"]
            or endpoint.get("net_uid") != target.get("net_uid")
            or endpoint.get("captured_pads", endpoint.get("pads"))
            != [p for p in owned[0]["copper"] if p["layer"] == target["layer"]]
        ):
            return False
        material_rows = [
            row["material"]
            for row in before.get("endpoint_pad_material", [])
            if row["component_uid"] == endpoint["component_uid"]
            and row.get("pin_uid") == endpoint["pin_uid"]
            and row["net_uid"] == target["net_uid"]
        ]
        if (
            len(material_rows) != 1
            or material_rows[0].get("complete") is not True
            or material_rows[0]["pads"] != endpoint["pads"]
            or material_rows[0]["holes"] != endpoint["holes"]
        ):
            return False
    old = contract.get("before_connectivity", {})
    return (
        old.get("net") == target["net"]
        and old.get("uid") == target.get("net_uid")
        and type(old.get("opens")) is int
        and old["opens"] == 1
        and old.get("routed") is False
        and contract.get("expected_connectivity") == {**old, "opens": 0, "routed": True}
    )


def _compare_complete(plan, state, uid):
    before = plan["before"]
    old = {board_unroute.key(r): r for r in before["routing_objects"]}
    new = {board_unroute.key(r): r for r in state["routing_objects"]}
    created = [r for k, r in new.items() if k not in old]
    expected = {
        "kind": "trace",
        "net": plan["target"]["net"],
        "layer": plan["target"]["layer"],
        "width": plan["request"]["width"],
        "path": [[*p, 0.0] for p in plan["request"]["points"]],
    }
    # Current strict captures promote the independently bound native UID into
    # physical trace material. Earlier immutable receipts used the outer UID.
    # Match that explicit representation only; old and non-target shapes remain
    # protected exactly, and native observe requires UID/material one-to-one.
    if len(created) == 1 and "uid" in created[0]["geometry"]:
        expected["uid"] = uid
    checks = [
        (
            "one returned trace identity and exact requested geometry",
            len(created) == 1
            and created[0]
            == {"kind": "trace", "uid": uid, "net": expected["net"], "geometry": expected},
        ),
        (
            "existing routing identities and geometry unchanged",
            all(new.get(k) == r for k, r in old.items()),
        ),
        (
            "unique routing identity inventory",
            len(old) == len(before["routing_objects"])
            and len(new) == len(state["routing_objects"]),
        ),
        (
            "plane inventory unchanged and no authorized regeneration",
            state["plane_inventory"] == before["plane_inventory"]
            and not plan["authorized_regeneration"],
        ),
        ("observed rule state unchanged", state["rules"] == before["rules"]),
        (
            "pin and owning net identities unchanged",
            state["pin_identities"] == before["pin_identities"],
        ),
        (
            "owned endpoint material inventory unchanged",
            state.get("endpoint_pad_material") == before.get("endpoint_pad_material"),
        ),
    ]
    protected_before, protected_after = (
        copy.deepcopy(before["geometry"]),
        copy.deepcopy(state["geometry"]),
    )
    if len(created) == 1 and created[0]["geometry"] in protected_after["model"]["traces"]:
        protected_after["model"]["traces"].remove(created[0]["geometry"])
    old_conn = protected_before["model"].pop("connectivity")
    new_conn = protected_after["model"].pop("connectivity")
    protected_before["model"].pop("unrouted")
    protected_after["model"].pop("unrouted")
    checks.append(
        (
            "protected board components pads holes and all other captured geometry",
            protected_before == protected_after,
        )
    )
    old_nets, new_nets = (
        {r["net"]: r for r in old_conn["nets"]},
        {r["net"]: r for r in new_conn["nets"]},
    )
    checks += [
        (
            "complete native connectivity",
            new_conn.get("complete") is True
            and len(new_nets) == len(new_conn["nets"])
            and all(
                type(r.get("opens")) is int and r["opens"] >= 0 and type(r.get("routed")) is bool
                for r in new_nets.values()
            ),
        ),
        (
            "all net identities unchanged",
            {n: r["uid"] for n, r in old_nets.items()}
            == {n: r["uid"] for n, r in new_nets.items()},
        ),
        (
            "other-net connectivity unchanged",
            {n: r for n, r in old_nets.items() if n != expected["net"]}
            == {n: r for n, r in new_nets.items() if n != expected["net"]},
        ),
    ]
    protection_checks = [
        {"name": n, "passed": bool(p), "category": "protection"} for n, p in checks
    ]
    contract = plan.get("target_contract", {})
    endpoint_identity_valid = _endpoint_contract(plan)
    physical = physical_connection(plan)
    actual_trace_matches = checks[0][1]
    endpoint_observation_matches = (
        state["geometry"]["components"] == before["geometry"]["components"]
        and state["pin_identities"] == before["pin_identities"]
        and state.get("endpoint_pad_material") == before.get("endpoint_pad_material")
    )
    physical["verified"] = (
        physical["verified"]
        and endpoint_identity_valid
        and actual_trace_matches
        and endpoint_observation_matches
    )
    if not endpoint_identity_valid:
        physical.update(
            status="incomplete", reason="bound actual endpoint identities are incomplete"
        )
    elif not actual_trace_matches or not endpoint_observation_matches:
        physical.update(status="failed", reason="actual trace or owned endpoint material differs")
    expected_net = contract.get("expected_connectivity")
    native_ok = (
        bool(expected_net)
        and new_nets.get(expected["net"]) == expected_net
        and type(new_nets[expected["net"]].get("opens")) is int
        and type(new_nets[expected["net"]].get("routed")) is bool
        and new_conn.get("complete") is True
    )
    unroute_ok = state["geometry"]["model"]["unrouted"] == [
        row for row in new_conn["nets"] if not row["routed"] or row["opens"] != 0
    ]
    completion_checks = [
        ("two specified actual pin-owned layer pad identities bound", endpoint_identity_valid),
        ("target native connectivity exactly meets opens=0/routed=true", native_ok),
        ("unrouted projection matches complete native connectivity", unroute_ok),
        ("independent material contact at both specified pads", physical["verified"]),
    ]
    protection_valid = all(p["passed"] for p in protection_checks)
    completion_valid = all(p for _, p in completion_checks)
    return {
        "valid": protection_valid and completion_valid,
        "protection_valid": protection_valid,
        "completion_valid": completion_valid,
        "completion_status": "verified"
        if completion_valid
        else "incomplete"
        if physical["status"] == "incomplete"
        else "failed",
        "checks": protection_checks
        + [{"name": n, "passed": bool(p), "category": "completion"} for n, p in completion_checks],
        "physical_contact_verified": physical["verified"],
        "physical_connection": physical,
        "native_connectivity_verified": native_ok,
        "drc_executed": False,
    }


def _execute_guarded(plan, result, app, board, doc, a):
    """Internal lifecycle; production plans cannot pass the eligibility guard."""
    if not plan["can_apply"] or plan["blockers"] or plan["authorized_regeneration"]:
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "Trace impact/dependencies are not proven; no native write was started",
            {"write_attempted": False, "trace": plan, "native_result": result},
        )
    item, stages = plan["request"], result["stages"]
    if (
        Path(str(doc.FullName)).resolve() != board.resolve()
        or a._licensed_document(app) is not doc
        or observe(doc, a, item) != plan["before"]
    ):
        raise a.AdapterError(
            "E_CONFLICT",
            "Trace frozen identity/state changed before routing",
            {"write_attempted": False, "native_result": result},
        )
    try:
        stages["route"] = "started"
        result.update(change_status="unknown", persistence_state="unknown")
        trace = doc.PutTrace(
            item["layer"],
            a._find_net(doc, item["net"]),
            item["width"],
            len(item["points"]),
            a._points_rows(item["points"]),
            None,
            0,
            a.UNIT_MM,
        )
        returned = {"call": "PutTrace", "returned": True, "uid": None}
        result["native_calls_returned"].append(returned)
        uid = _uid(trace.UniqueId)
        returned["uid"] = uid
        stages["route"], stages["regeneration"] = "returned", "not_required"
        stages["readback"] = "started"
        state = observe(doc, a, item)
        result.update(state_after=state, applied=True, change_status="partial")
        stages["readback"], stages["protection"] = "complete", "started"
        stages["connection"] = "started"
        proof = compare(plan, state, uid)
        result["protection"] = proof
        stages["protection"] = "passed" if proof["protection_valid"] else "failed"
        stages["connection"] = proof["completion_status"]
        if not proof["valid"]:
            raise a.AdapterError(
                "E_VERIFY_INCOMPLETE"
                if proof["completion_status"] == "incomplete"
                else "E_PARTIAL",
                "Trace connection/protection did not verify; Save/Close/Open stopped",
            )
        result["change_status"] = "applied"
        stages["save"] = "started"
        saved = a._native_call("trace.Save", doc.Save)
        if saved is False:
            raise a.AdapterError("E_NATIVE_FAILURE", "Trace Save was not acknowledged")
        result["saved"] = True
        stages["save"], stages["close"] = "acknowledged", "started"
        closed = a._native_call("trace.Close", lambda: doc.Close(True))
        if closed is False or app.ActiveDocument is not None:
            raise a.AdapterError(
                "E_HUMAN_REQUIRED", "Normal trace document closure was not confirmed"
            )
        stages["close"], stages["reopen"] = "confirmed", "started"
        a._native_call("trace.Open", lambda: a._open_layout_document(app, board))
        reopened = a._licensed_document(app)
        if Path(str(reopened.FullName)).resolve() != board.resolve():
            raise a.AdapterError("E_CONFLICT", "Reopened trace board identity differs")
        after = observe(reopened, a, item)
        result["state_reopened"] = after
        result["persistence_verified"] = state == after
        stages["reopen"] = "verified" if state == after else "mismatch"
        if state != after:
            raise a.AdapterError(
                "E_CONFLICT", "Trace reopened state differs from protected pre-save state"
            )
        result["persistence_state"] = "saved"
        return result
    except Exception as error:
        result["post_write_error"] = {
            "code": getattr(error, "code", "E_NATIVE_FAILURE"),
            "cause": type(error).__name__,
        }
        raise a.AdapterError(
            getattr(error, "code", "E_NATIVE_FAILURE"),
            "Trace lifecycle did not complete; retain the original observation and receipt",
            {"write_attempted": True, "native_result": result},
        ) from error


def execute(params, client, a):
    items = a.plan_items({"items": params.get("items") or []})
    if len(items) != 1 or items[0]["kind"] != "trace":
        refuse_broad(a, "mixed/via routing")
    item = items[0]
    if (
        not math.isfinite(item["width"])
        or any(not all(math.isfinite(v) for v in p) for p in item["points"])
        or len(item["points"]) > 10000
    ):
        raise a.AdapterError(
            "E_VALIDATION",
            "Trace path/width must be finite and bounded",
            {"write_attempted": False},
        )
    try:
        _, board, app, doc = active(params, client, a, require_project=False)
        state = observe(doc, a, item)
        plan = bind(state, item, _input_files(params, board), a)
    except Exception as error:
        raise a.AdapterError(
            getattr(error, "code", "E_NATIVE_FAILURE"),
            "Trace preflight could not bind the exact open board, geometry and dependencies",
            {
                **(getattr(error, "details", {}) or {}),
                "write_attempted": False,
                "stage": "trace.preflight",
            },
        ) from error
    result = {
        "pcb": str(board),
        "trace": plan,
        "items": items,
        "nets": [item["net"]],
        "applied": False,
        "saved": False,
        "persistence_verified": False,
        "change_status": "not_applied",
        "persistence_state": "unchanged",
        "stages": dict.fromkeys(
            (
                "route",
                "regeneration",
                "readback",
                "protection",
                "connection",
                "save",
                "close",
                "reopen",
            ),
            "not_started",
        ),
        "native_calls_returned": [],
        "_untrusted": ["pcb", "trace", "items", "state_after", "state_reopened"],
    }
    if not params.get("apply"):
        return result
    if params.get("expected") != plan["fingerprint"]:
        raise a.AdapterError(
            "E_CONFLICT",
            "Trace target/inputs/dependencies changed since preview",
            {"write_attempted": False},
        )
    return _execute_guarded(plan, result, app, board, doc, a)
