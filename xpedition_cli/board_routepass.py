"""One native no-via routing pass, bounded by independently verified output.

This mode delegates rule solving to the native engine. It does not establish a
complete effective-rule parser. Configuration has no supported getter: the exact
ordered dispatch transcript and independently captured actual result are separate
evidence. No PutTrace, selection globals, cleanup, plane regeneration or Sync.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from . import board_labels, board_trace, board_unroute
from .cell_refresh import _canonical, active, digest
from .endpoint_material import _contact_witness

GUARD = "native-routepass-two-three-or-six-pad-no-via-material-graph-v8"
MAX_OBJECTS = 4096
SIX_GND_MEMBERS = frozenset((refdes, "2") for refdes in ("J1", "J2", "J3", "J4", "C1", "C2"))
CONTINUATION = "existing-components"
CONTINUATION_PIN_GROUPS = frozenset(
    (
        frozenset((refdes, "2") for refdes in ("J1", "J2", "C1")),
        frozenset((refdes, "2") for refdes in ("J4", "C2")),
        frozenset({("J3", "2")}),
    )
)
CONTINUATION_GROUP_TRACE_COUNTS = {
    frozenset((refdes, "2") for refdes in ("J1", "J2", "C1")): 2,
    frozenset((refdes, "2") for refdes in ("J4", "C2")): 1,
    frozenset({("J3", "2")}): 0,
}


def limitations():
    return {
        "mode": (
            "native engine solves; exact selected two- or three-pad net, or the limited six-pad "
            "GND member set, and one signal layer"
        ),
        "rules": "Named observations are frozen; complete path-effective rule parsing is unproven",
        "configuration": "No supported configuration getter; ordered calls are intent evidence",
        "atomicity": (
            "Observable inputs are compared exactly in one retained owner; no atomic database "
            "freeze, exclusive lock or complete opaque constraint-cache version is proved. "
            "Checkpoint Before/At/After values remain observations only."
        ),
        "derived_geometry_evidence": (
            "Temporary native-mask allocation/delete receipts are recomputed evidence, not design "
            "identity. Actual raw pad/trace inputs remain exactly protected."
        ),
        "physical": (
            "Two-pad straight chain, or three-/six-pad complete native full-width material graph; "
            "positive owned-pad contact excludes drilled and cutout voids"
        ),
        "six_pad_scope": {
            "net": "GND",
            "physical_members": [list(member) for member in sorted(SIX_GND_MEMBERS)],
            "trace_material_condition": (
                "Every target trace and pin belongs to one positive-material region graph; "
                "pad-mediated contact requires independently proven continuous pad material"
            ),
        },
        "existing_component_scope": {
            "explicit_request": CONTINUATION,
            "net": "GND",
            "old_target_traces": 3,
            "all_old_traces": 11,
            "observed_pin_components": 3,
            "native_opens_before": 2,
            "old_copper_protection": "every old UID, full raw path, width, layer and net exactly",
            "pad_bridging": (
                "only independently proven continuous pad regions after hole/cutout subtraction; "
                "pin identity cannot merge disconnected islands"
            ),
        },
        "pins": (
            "Every physical pin must have an observed owning net; NC/unassigned pads are refused"
        ),
        "not_checked": [
            "complete effective-rule resolution",
            "DRC",
            "manufacturing",
            "thermal/tie",
        ],
        "no_actions": ["cleanup", "fanout", "unroute", "plane regeneration", "SynchCES"],
    }


def validate(request):
    if (
        not isinstance(request, dict)
        or not {"net", "layer"} <= set(request) <= {"net", "layer", "effort", "continuation"}
        or not isinstance(request["net"], str)
        or not request["net"].strip()
        or type(request["layer"]) is not int
        or not 1 <= request["layer"] <= 128
        or type(request.get("effort", 1)) is not int
        or request.get("effort", 1) not in (1, 2)
        or (
            "continuation" in request
            and (request["continuation"] != CONTINUATION or request["net"] != "GND")
        )
    ):
        raise ValueError(
            "Exactly one nonempty net, one positive conductor layer and native effort 1 or 2 "
            "are required"
        )
    return {
        "net": request["net"],
        "layer": request["layer"],
        **({"effort": 2} if request.get("effort", 1) == 2 else {}),
        **({"continuation": CONTINUATION} if "continuation" in request else {}),
    }


def _item(request):
    # Net/layer are observer keys, not an invented input path or requested width.
    return {
        "kind": "trace",
        "net": request["net"],
        "layer": request["layer"],
        "width": None,
        "points": [],
    }


def observe(doc, a, request, *, _capture_stage=None):
    return _observe(doc, a, request, _capture_stage=_capture_stage)


def observe_for_save(doc, a, request, verified_state):
    return _observe(doc, a, request, reuse_state=verified_state)


def observe_components(doc, a, request, *, _capture_stage=None, _identity_reader=None):
    """One actual net/layer, independent of legacy route task member counts."""
    if not isinstance(request, dict) or set(request) != {"net", "layer"}:
        raise ValueError("Component observation requires exactly one net and layer")
    return _observe(
        doc,
        a,
        request,
        component_scope=True,
        _capture_stage=_capture_stage,
        _identity_reader=_identity_reader,
    )


def observe_components_for_save(doc, a, request, verified_state, *, _identity_reader=None):
    if not isinstance(request, dict) or set(request) != {"net", "layer"}:
        raise ValueError("Component observation requires exactly one net and layer")
    return _observe(
        doc,
        a,
        request,
        reuse_state=verified_state,
        component_scope=True,
        _identity_reader=_identity_reader,
    )


def _observe(
    doc,
    a,
    request,
    reuse_state=None,
    *,
    component_scope=False,
    _capture_stage=None,
    _identity_reader=None,
):
    from . import native_pad_mask

    request = validate(request)
    native_unit = doc.CurrentUnit
    if type(native_unit) is not int or native_unit != 2:
        raise ValueError("Actual native CurrentUnit must be the observed mil enum2")
    if _identity_reader is not None:
        if _capture_stage is not None or not callable(_identity_reader):
            raise ValueError("One trusted native identity callback or issued stage is required")
        from .native_observation_stage import full_capture, material

        # Each lifecycle observation issues its own complete native getter.
        # Only sub-observers in this lexical read may consume the result.
        with full_capture(doc, a, identity_reader=_identity_reader, include_texts=True) as stage:
            state = _observe(
                doc,
                a,
                request,
                reuse_state,
                component_scope=component_scope,
                _capture_stage=stage,
            )
            material(stage, doc, a, kind="complete_capture")
            return state
    shared = {"_capture_stage": _capture_stage} if _capture_stage is not None else {}
    state = board_trace.observe(doc, a, _item(request), location_scheme=False, **shared)
    if component_scope:
        from .board_components import validate_signal_layer

        validate_signal_layer(state, request["layer"])
    texts = board_labels.observe(doc, Path(str(doc.FullName)), a, **shared)
    if texts["geometry"] != state["geometry"]:
        raise ValueError("Geometry changed during independent complete text observation")
    state["texts"] = texts["texts"]
    if _capture_stage is not None:
        from .native_observation_stage import text_inventory

        independently_read_texts = text_inventory(_capture_stage, doc, a)
        if independently_read_texts is not None and independently_read_texts != texts["texts"]:
            raise ValueError("Complete native text fields changed within the local read stage")
    materials = []
    for component in board_trace._collection(doc, "Components", a):
        for pin in board_trace._collection(component, "Pins", a):
            if pin.Net is None:
                raise ValueError(
                    "Unassigned physical pins cannot be omitted from whole-board material"
                )
            if pin.Net is not None:
                materials.append(
                    {
                        "component_uid": board_trace._uid(component.UniqueId),
                        "refdes": str(component.RefDes),
                        "pin": str(pin.Name),
                        "pin_uid": board_trace._uid(pin.UniqueId),
                        "net": str(pin.Net.Name),
                        "net_uid": board_trace._uid(pin.Net.UniqueId),
                        "center_mm": [float(pin.GetPositionX(4)), float(pin.GetPositionY(4))],
                        "material": native_pad_mask.observe_pin(pin, request["layer"], a),
                    }
                )
    state["all_pin_pad_material"] = sorted(
        materials, key=lambda r: (r["component_uid"], r["pin_uid"])
    )
    if _capture_stage is not None:
        from .cell_refresh import _canonical
        from .native_observation_stage import material

        geometry = material(_capture_stage, doc, a, kind="complete_capture")
        expected_pins = [
            [
                str(component["uid"]),
                component["refdes"],
                pin["number"],
                pin["net"],
                pin["center_mm"],
                pin["uid"],
                pin["net_uid"],
            ]
            for component in geometry["components"]
            for pin in component["geometry"]
        ]
        observed_pins = [
            [
                row["component_uid"],
                row["refdes"],
                row["pin"],
                row["net"],
                row["center_mm"],
                row["pin_uid"],
                row["net_uid"],
            ]
            for row in materials
        ]
        if sorted(_canonical(expected_pins)) != sorted(_canonical(observed_pins)):
            raise ValueError("Whole-board pin material differs from the shared complete capture")
    material_request = request
    if component_scope:
        nets = _nets(state)
        if request["net"] not in nets:
            raise ValueError("The requested actual net is absent from complete connectivity")
        material_request = {
            "net": request["net"],
            "net_uid": nets[request["net"]]["uid"],
            "layer": request["layer"],
        }
    if reuse_state is None:
        state["native_mask_proof"] = (
            native_pad_mask.evaluate_components(doc, state, material_request, a)
            if component_scope
            else native_pad_mask.evaluate(doc, state, request, a)
        )
        state["native_mask_proof"]["request"] = copy.deepcopy(material_request)
        state["native_trace_material"] = state["native_mask_proof"].get("raw_trace_inventory")
    else:
        state["native_trace_material"] = native_pad_mask._trace_inventory(doc, state, a)
        if (
            design_state(state) != design_state(reuse_state)
            or reuse_state["native_mask_proof"].get("request") != material_request
            or (
                native_pad_mask.verify_component_evidence(reuse_state, material_request)
                if component_scope
                else native_pad_mask.verify_evidence(reuse_state, request)
            ).get("valid")
            is not True
        ):
            raise ValueError("Fresh pre-Save raw design/material inputs differ or are incomplete")
        state["native_mask_proof"] = copy.deepcopy(reuse_state["native_mask_proof"])
        verified = (
            native_pad_mask.verify_component_evidence(state, material_request)
            if component_scope
            else native_pad_mask.verify_evidence(state, request)
        )
        if verified.get("valid") is not True:
            raise ValueError("Exact-input pre-Save material evidence reuse did not verify")
    return state


def _inventory(rows):
    if not isinstance(rows, list) or len(rows) > MAX_OBJECTS:
        raise ValueError("Routing inventory exceeds the supported budget")
    result = {}
    for row in rows:
        key = board_unroute.key(row)
        if (
            key in result
            or key[0] not in {"trace", "via"}
            or not isinstance(key[1], str)
            or not key[1].strip()
            or key[1] == "0"
        ):
            raise ValueError("Routing identity is absent or duplicated")
        result[key] = row
    return result


def _nets(state):
    connectivity = state["geometry"]["model"]["connectivity"]
    rows = connectivity["nets"]
    if (
        connectivity.get("complete") is not True
        or not isinstance(rows, list)
        or len(rows) > 10000
        or any(
            not isinstance(row.get("net"), str)
            or not row["net"]
            or not isinstance(row.get("uid"), str)
            or not row["uid"]
            or type(row.get("opens")) is not int
            or row["opens"] < 0
            or type(row.get("routed")) is not bool
            for row in rows
        )
        or len({r["net"] for r in rows}) != len(rows)
        or len({r["uid"] for r in rows}) != len(rows)
    ):
        raise ValueError("Complete unique native net identities/connectivity are required")
    return {r["net"]: r for r in rows}


def configuration(state, request):
    request = validate(request)
    layers = [r for r in state["geometry"]["stackup"] if r.get("kind") == "conductor"]
    count = state["geometry"]["model"]["layers"]
    if (
        type(count) is not int
        or not 1 <= count <= 128
        or len(layers) != count
        or {r.get("layer") for r in layers} != set(range(1, count + 1))
        or len([r for r in layers if r["layer"] == request["layer"] and r.get("usage") == "signal"])
        != 1
    ):
        raise ValueError("The actual complete stackup and target signal layer are required")
    return {
        "pass_type": [6, request.get("effort", 1), request.get("effort", 1), False, False],
        "config": [False, False],
        "items": 5,
        "order": [0, 0],
        "net": request["net"],
        "remove_layers": [r["layer"] for r in layers if r["layer"] != request["layer"]],
        "add_layer": request["layer"],
        "go_count": 1,
        "configuration_readback": "unavailable; exact ordered dispatch transcript only",
    }


def _connection_contract(count):
    if type(count) is not int or count not in (2, 3, 6):
        raise ValueError("Only two, three or the limited six complete target pins are supported")
    return {
        "member_count": count,
        "native_opens_before": count - 1,
        "native_opens_after": 0,
        "expected_material_components_after": 1,
        "all_target_pins_and_all_new_traces_required": True,
        "physical_basis": "complete-native-material-graph" if count in (3, 6) else "two-pad-chain",
    }


def _six_member_contract(net, members):
    """A bounded six-member exception, never an arbitrary higher-count net."""
    if len(members) == 6 and (net != "GND" or set(members) != SIX_GND_MEMBERS):
        raise ValueError("Six-pin mode requires exactly GND: J1.2, J2.2, J3.2, J4.2, C1.2 and C2.2")


def _bound_connection_contract(state, request, endpoints):
    """Recompute the same finite initial-state contract at every acceptance gate."""
    contract = _connection_contract(len(endpoints))
    if request.get("continuation") != CONTINUATION:
        return contract
    from . import native_pad_mask

    if len(endpoints) != 6:
        raise ValueError("Existing-component continuation requires all six GND members")
    _six_member_contract(request["net"], [(e["refdes"], e["pin"]) for e in endpoints])
    objects = _inventory(state["routing_objects"])
    target_rows = [row for row in objects.values() if row["net"] == request["net"]]
    if (
        len(objects) != 11
        or len(target_rows) != 3
        or any(row["kind"] != "trace" for row in objects.values())
        or any(row["geometry"]["layer"] != request["layer"] for row in target_rows)
    ):
        raise ValueError(
            "Continuation requires exactly eleven old traces, including three target traces"
        )
    target_net = _nets(state)[request["net"]]
    if target_net["opens"] != 2 or target_net["routed"] is not False:
        raise ValueError("Continuation requires observed GND two opens and unrouted status")
    raw = _raw_trace_rows(state)
    raw_target = [row for row in raw if row["net"] == request["net"]]
    observation = native_pad_mask.observe_material_components(state, request)
    if observation.get("valid") is not True:
        raise ValueError(
            "Complete initial material-component observation is required: " + str(observation)
        )
    components = observation["components"]
    groups = frozenset(
        frozenset((row["refdes"], row["pin"]) for row in component["pin_members"])
        for component in components
    )
    if (
        len(components) != 3
        or groups != CONTINUATION_PIN_GROUPS
        or any(
            len(component["trace_ids"])
            != CONTINUATION_GROUP_TRACE_COUNTS.get(
                frozenset((row["refdes"], row["pin"]) for row in component["pin_members"])
            )
            for component in components
        )
        or observation.get("all_members_connected") is not False
        or sorted(uid for component in components for uid in component["trace_ids"])
        != sorted(row["uid"] for row in target_rows)
        or sorted(uid for component in components for uid in component["pin_ids"])
        != sorted(row["pin_uid"] for row in endpoints)
    ):
        raise ValueError("Continuation requires the exact observed three GND pin/material groups")
    return {
        **contract,
        "mode": CONTINUATION,
        "native_opens_before": 2,
        "expected_material_components_before": 3,
        "all_target_pins_and_all_existing_and_new_traces_required": True,
        "old_target_trace_count": 3,
        "old_board_trace_count": 11,
        "old_target_trace_uids": sorted(row["uid"] for row in target_rows),
        "old_target_routing_sha256": digest(sorted(target_rows, key=lambda row: row["uid"])),
        "old_target_raw_sha256": digest(raw_target),
        "components_before": copy.deepcopy(components),
        "component_membership_sha256": observation["component_membership_sha256"],
    }


def _all_endpoints(state, request, pins, a):
    """Capture every actual owned member, without treating the middle pin as a waypoint."""
    count = len(pins)
    _connection_contract(count)
    if len({_point([*pin["center_mm"], 0]) for pin in pins}) != count:
        raise ValueError("Distinct actual physical pin centers are required")
    endpoints = []
    for pin in pins[1:]:
        pair = board_trace._endpoints(
            state,
            {**_item(request), "points": [pins[0]["center_mm"], pin["center_mm"]]},
            a,
        )
        if not endpoints:
            endpoints.append(pair[0])
        elif endpoints[0] != pair[0]:
            raise ValueError("Repeated anchor pin ownership observation differs")
        endpoints.append(pair[1])
    return endpoints


def _round_hole_inventory(state):
    """The strict model's full board drill inventory must equal observed owned holes."""
    observed = state["geometry"]["model"]["holes"]
    slots = state["geometry"]["model"].get("slots", [])
    if not isinstance(observed, list) or len(observed) > MAX_OBJECTS or slots:
        raise ValueError("Only complete bounded pin-owned round board holes are supported")
    owned = []
    for pin in state["all_pin_pad_material"]:
        for hole in pin["material"]["holes"]:
            center, diameter = hole["center_mm"], hole["diameter_mm"]
            if (
                not isinstance(center, list)
                or len(center) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in center)
                or type(diameter) not in (int, float)
                or not math.isfinite(diameter)
                or diameter <= 0
                or type(hole["plated"]) is not bool
            ):
                raise ValueError("Pin-owned round hole position/diameter/plating is incomplete")
            owned.append([*center, diameter])
    for row in observed:
        if (
            not isinstance(row, list)
            or len(row) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in row)
            or row[2] <= 0
        ):
            raise ValueError("Complete native board round-hole inventory is unreadable")
    if Counter(tuple(_canonical(row)) for row in observed) != Counter(
        tuple(_canonical(row)) for row in owned
    ):
        raise ValueError("All board holes must exactly bind observed pin-owned round holes")


def bind(state, request, inputs, a, identity=None):
    try:
        request = validate(request)
        nets, objects = _nets(state), _inventory(state["routing_objects"])
        target = nets[request["net"]]
        identities = state["pin_identities"]
        if len({r["pin_uid"] for r in identities}) != len(identities) or any(
            not r["pin_uid"] or r["net"] not in nets or r["net_uid"] != nets[r["net"]]["uid"]
            for r in identities
        ):
            raise ValueError(
                "Every physical pin must have a unique UID and consistent actual owning net"
            )
        if request.get("continuation") != CONTINUATION and any(
            r["net"] == request["net"] for r in objects.values()
        ):
            raise ValueError("Target must have no existing trace or via")
        if any(r["kind"] == "via" for r in objects.values()):
            raise ValueError("Existing via material separation is outside this no-via contract")
        inventory = state["plane_inventory"]
        if (
            inventory.get("complete") is not True
            or any(inventory[k] for k in ("shapes", "generated", "assignments"))
            or state["geometry"]["model"]["planes"]
        ):
            raise ValueError("Any plane shape, generated plane or assignment is unsupported")
        pins = [
            p
            for c in state["geometry"]["components"]
            for p in c["geometry"]
            if p["net"] == request["net"]
        ]
        if any(
            p.get("net") is None for c in state["geometry"]["components"] for p in c["geometry"]
        ):
            raise ValueError(
                "NC or unassigned physical pad protection is outside this limited mode"
            )
        _connection_contract(len(pins))
        _six_member_contract(
            request["net"],
            [
                (c["refdes"], p["number"])
                for c in state["geometry"]["components"]
                for p in c["geometry"]
                if p["net"] == request["net"]
            ],
        )
        endpoints = _all_endpoints(state, request, pins, a)
        contract = _bound_connection_contract(state, request, endpoints)
        if target["opens"] != contract["native_opens_before"] or target["routed"] is not False:
            raise ValueError(
                "Unrouted target opens must equal the complete frozen connection contract"
            )
        native_mask_mode = isinstance(state.get("native_mask_proof"), dict)
        if len(pins) in (3, 6) and not native_mask_mode:
            raise ValueError(
                "Three or six physical pins require complete native material-graph evidence"
            )
        target_ids = [r for r in identities if r["net"] == request["net"]]
        if len(target_ids) != len(endpoints) or {r["pin_uid"] for r in target_ids} != {
            e["pin_uid"] for e in endpoints
        }:
            raise ValueError("Complete target pin identities do not equal captured owned members")
        if native_mask_mode:
            from . import native_pad_mask

            _round_hole_inventory(state)
            evidence = native_pad_mask.verify_evidence(state, request)
            if evidence.get("valid") is not True:
                raise ValueError("Native mask raw input evidence is incomplete: " + str(evidence))
            for endpoint in endpoints:
                rows = [
                    r
                    for r in state["all_pin_pad_material"]
                    if r["component_uid"] == endpoint["component_uid"]
                    and r["pin_uid"] == endpoint["pin_uid"]
                    and r["net_uid"] == endpoint["net_uid"]
                ]
                if len(rows) != 1 or rows[0]["material"].get("complete") is not True:
                    raise ValueError("Actual pin-owned native mask material input is incomplete")
                endpoint["native_material_observation"] = copy.deepcopy(rows[0]["material"])
                endpoint["native_center_mm"] = copy.deepcopy(rows[0]["center_mm"])
        elif any(
            e.get("material_observation", {}).get("complete") is not True or not e.get("pin_uid")
            for e in endpoints
        ):
            raise ValueError("Both actual pad material and pin UID observations are required")
        all_material = state["all_pin_pad_material"]
        if (
            len(all_material) != len(state["pin_identities"])
            or len({(r["component_uid"], r["pin_uid"]) for r in all_material}) != len(all_material)
            or {(r["component_uid"], r["pin_uid"], r["net_uid"]) for r in all_material}
            != {(r["component_uid"], r["pin_uid"], r["net_uid"]) for r in state["pin_identities"]}
            or any(r["material"].get("complete") is not True for r in all_material)
        ):
            raise ValueError(
                "Complete all-pin owned pad material is required to reject wrong-net contact"
            )
        config = configuration(state, request)
    except (KeyError, ValueError, TypeError, IndexError) as error:
        raise a.AdapterError(
            "E_UNSUPPORTED", str(error), {"write_attempted": False, "limits": limitations()}
        ) from error
    result = {
        "guard": GUARD,
        "before": copy.deepcopy(state),
        "request": request,
        "target": {"net": target["net"], "net_uid": target["uid"], "layer": request["layer"]},
        "endpoints": endpoints,
        "connection_contract": contract,
        "material_verification_mode": "native-mask"
        if native_mask_mode
        else "supported-straight-contours",
        "configuration": config,
        "input_files": copy.deepcopy(inputs),
        "identity": copy.deepcopy(identity),
        "allowed_output_files": _allowed_output_files(identity, inputs),
        "expected_connectivity": {**target, "opens": 0, "routed": True},
        "unit": "mm",
        "native_current_unit": 2,
        "can_apply": True,
        "allowed_changes": ["new target-net straight trace objects on the one bound layer"],
        "forbidden_changes": [
            "new vias",
            "existing copper",
            "other nets",
            "rules",
            "all non-copper",
        ],
        "limits": limitations(),
    }
    fingerprint_basis = copy.deepcopy(result)
    fingerprint_basis["before"] = _state_identity(result["before"])
    if isinstance(inputs, dict):
        fingerprint_basis["input_files"] = _dependency_identity(inputs)
    result["fingerprint"] = digest(fingerprint_basis)
    return result


def _point(value):
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
        or value[2] != 0
    ):
        raise ValueError("Only finite native straight trace vertices are supported")
    return tuple(value[:2])


def design_state(state):
    """Exact design identity, excluding only recomputed temporary-mask receipts."""
    result = copy.deepcopy(state)
    result.pop("native_mask_proof", None)
    return result


def _state_identity(state):
    return design_state(state)


def _states_equal(left, right, request):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if _state_identity(left) != _state_identity(right):
        return False
    if "native_mask_proof" in left or "native_mask_proof" in right:
        from . import native_pad_mask

        return all(
            native_pad_mask.verify_evidence(state, request).get("valid") is True
            for state in (left, right)
        )
    return True


def _checkpoint(doc):
    try:
        return doc.Checkpoint, None
    except Exception as error:
        return None, {"cause": type(error).__name__, "code": getattr(error, "code", None)}


def _observe_checkpoint(result, key, doc, checkpoint, error, phase, identity):
    row = {
        "phase": phase,
        "identity": copy.deepcopy(identity),
        "atomic_freeze_proven": False,
        "not_proven": "atomic database or opaque cache freeze",
    }
    try:
        if checkpoint is None:
            row.update(
                status="reference_unavailable", error=error or {"cause": "CheckpointUnavailable"}
            )
        else:
            valid, location = checkpoint.IsValid(), doc.LocateCheckpoint(checkpoint)
            row.update(
                status="observed",
                valid=valid,
                location=location,
                location_name={1: "Before", 2: "At", 3: "After", 4: "NotOnBranch"}.get(
                    location, "Unknown"
                )
                if type(location) is int
                else "Unknown",
            )
    except Exception as failure:
        row.update(
            status="read_failed",
            error={"cause": type(failure).__name__, "code": getattr(failure, "code", None)},
        )
    result.setdefault(key, []).append(row)


def _record_observation(result, phase, state_field, state, files, identity, *, material_basis):
    result["observable_state_guard_evidence"]["observations"].append(
        {
            "phase": phase,
            "state_field": state_field,
            "design_state_sha256": digest(design_state(state)),
            "dependency_identity_sha256": digest(_dependency_identity(files)),
            "identity": copy.deepcopy(identity),
            "material_basis": material_basis,
        }
    )


def _checkpoint_records(rows, phases, identity):
    if not isinstance(rows, list) or [r.get("phase") for r in rows] != phases:
        return False
    for row in rows:
        if row.get("identity") != identity or row.get("atomic_freeze_proven") is not False:
            return False
        if row.get("status") == "observed":
            if (
                type(row.get("valid")) is not bool
                or type(row.get("location")) is not int
                or row.get("location_name")
                != {1: "Before", 2: "At", 3: "After", 4: "NotOnBranch"}.get(
                    row["location"], "Unknown"
                )
            ):
                return False
        elif row.get("status") not in {"reference_unavailable", "read_failed"} or not isinstance(
            row.get("error"), dict
        ):
            return False
    return True


def _observable_evidence(result, bound):
    evidence = result["observable_state_guard_evidence"]
    if (
        bound.get("guard") != GUARD
        or result.get("state_guard") != GUARD
        or evidence.get("guard") != GUARD
        or evidence.get("request") != bound["request"]
        or any(
            evidence.get(k) is not False
            for k in (
                "atomic_database_freeze",
                "exclusive_database_lock",
                "opaque_constraint_cache_version_proven",
            )
        )
    ):
        return False
    sources = [
        (
            "initial_bind",
            "routepass.before",
            bound["before"],
            bound["input_files"],
            "full_native_observation",
        ),
        (
            "immediately_before_go",
            "state_before_go",
            result["state_before_go"],
            result["pre_go_input_observation"],
            "full_native_observation",
        ),
        (
            "post_go_readback",
            "state_after",
            result["state_after"],
            result["post_route_input_observation"],
            "full_native_observation",
        ),
        (
            "immediately_before_save",
            "state_before_save",
            result["state_before_save"],
            result["pre_save_input_observation"],
            "fresh_native_inputs_exact_mask_input_reuse",
        ),
    ]
    rows = evidence["observations"]
    if not isinstance(rows, list) or len(rows) != len(sources):
        return False
    for row, (phase, field, state, files, basis) in zip(rows, sources, strict=True):
        if (
            row.get("phase") != phase
            or row.get("state_field") != field
            or row.get("identity") != bound["identity"]
            or row.get("material_basis") != basis
            or row.get("design_state_sha256") != digest(design_state(state))
            or row.get("dependency_identity_sha256") != digest(_dependency_identity(files))
        ):
            return False
    selected = result["selected_net_evidence"]
    return (
        _states_equal(bound["before"], result["state_before_go"], bound["request"])
        and _dependency_identity(bound["input_files"])
        == _dependency_identity(result["pre_go_input_observation"])
        and _states_equal(result["state_after"], result["state_before_save"], bound["request"])
        and type(selected.get("count")) is int
        and selected["count"] == 1
        and selected.get("net") == bound["target"]["net"]
        and selected.get("net_uid") == bound["target"]["net_uid"]
    )


def physical_chain(plan, created, *, mask_proof=None):
    """Exact endpoint graph, no hidden geometric tolerance or opens inference."""
    try:
        if not created or len(created) > MAX_OBJECTS:
            raise ValueError("A bounded nonempty new trace collection is required")
        adjacent, edges = {}, set()
        segments = []
        for row in created:
            shape = row["geometry"]
            width = shape["width"]
            path = shape["path"]
            if (
                row["kind"] != "trace"
                or row["net"] != plan["target"]["net"]
                or shape.get("kind") != "trace"
                or shape.get("net") != plan["target"]["net"]
                or shape.get("layer") != plan["target"]["layer"]
                or type(shape.get("layer")) is not int
                or type(width) not in (int, float)
                or not math.isfinite(width)
                or width <= 0
                or not isinstance(path, list)
                or not 2 <= len(path) <= MAX_OBJECTS
            ):
                raise ValueError(
                    "Each new trace needs positive width, target net and exact signal layer"
                )
            points = [_point(p) for p in path]
            for p, q in zip(points, points[1:], strict=False):
                edge = tuple(sorted((p, q)))
                if p == q or edge in edges:
                    raise ValueError("Zero-length or duplicated copper segment")
                edges.add(edge)
                adjacent.setdefault(p, []).append(q)
                adjacent.setdefault(q, []).append(p)
                segments.append((p, q))
                if len(edges) > MAX_OBJECTS:
                    raise ValueError("Total new trace segment budget exceeded")
        ends = [p for p, neighbors in adjacent.items() if len(neighbors) == 1]
        if len(ends) != 2 or any(len(v) not in (1, 2) for v in adjacent.values()):
            raise ValueError("New traces must be one unbranched open chain")
        visited, pending = set(), [ends[0]]
        while pending:
            point = pending.pop()
            if point in visited:
                continue
            visited.add(point)
            pending.extend(adjacent[point])
        if visited != set(adjacent):
            raise ValueError("New traces include disconnected material or a detached loop")
        # Non-neighbor intersections/overlaps create an additional physical
        # junction not represented by the endpoint graph; conservatively reject.
        from .endpoint_material import _segments_meet

        for i, (p, q) in enumerate(segments):
            for r, s in segments[i + 1 :]:
                common = {p, q} & {r, s}
                if not common and _segments_meet(p, q, r, s):
                    raise ValueError("Non-neighbor trace intersection is unsupported")
                if len(common) == 1:
                    shared = next(iter(common))
                    left = q if p == shared else p
                    right = s if r == shared else r
                    u = [left[i] - shared[i] for i in (0, 1)]
                    v = [right[i] - shared[i] for i in (0, 1)]
                    if (
                        u[0] * v[1] == u[1] * v[0]
                        and sum(a * b for a, b in zip(u, v, strict=True)) > 0
                    ):
                        raise ValueError("Adjacent trace segments overlap beyond their endpoint")
        witnesses = []
        if mask_proof is not None:
            contacts = mask_proof["target_contacts"]
            for endpoint in plan["endpoints"]:
                matches = []
                for end in ends:
                    terminal_uids = {
                        row["uid"]
                        for row in created
                        if tuple(row["geometry"]["path"][0][:2]) == end
                        or tuple(row["geometry"]["path"][-1][:2]) == end
                    }
                    rows = [
                        row
                        for row in contacts
                        if row.get("component_uid") == endpoint["component_uid"]
                        and row.get("pin_uid") == endpoint["pin_uid"]
                        and row.get("trace_uid") in terminal_uids
                        and type(row.get("positive_intersection_area_mm2")) in (int, float)
                        and math.isfinite(row["positive_intersection_area_mm2"])
                        and row["positive_intersection_area_mm2"] > 0
                        and any(
                            e.get("point_mm") == list(end) and e.get("inside_owned_outer") is True
                            for e in row.get("trace_ends", [])
                        )
                    ]
                    matches.extend((end, row) for row in rows)
                if len(matches) != 1:
                    raise ValueError(
                        "Actual terminal trace lacks unique positive filled owned-pad overlap"
                    )
                end, row = matches[0]
                witnesses.append({"chain_end_mm": list(end), "native_mask_intersection": row})
            if witnesses[0]["chain_end_mm"] == witnesses[1]["chain_end_mm"]:
                raise ValueError("Both actual owned pads matched the same chain terminal")
            return {
                "verified": True,
                "status": "verified",
                "segment_count": len(edges),
                "trace_object_count": len(created),
                "endpoint_witnesses": witnesses,
                "basis": (
                    "exact raw native chain and positive-area terminal trace/pad "
                    "material intersection"
                ),
                "not_checked": ["spacing rules", "DRC", "manufacturing", "thermal/tie"],
            }
        from .endpoint_material import _inside, _material

        for endpoint in plan["endpoints"]:
            hits = []
            for end in ends:
                anchored = any(
                    _inside(end, _material(shape)[0])
                    for pad in endpoint["pads"]
                    for shape in pad["geometry"]
                )
                if not anchored:
                    continue
                witness = _contact_witness(endpoint, list(end), list(adjacent[end][0]))
                if witness is not None:
                    hits.append({"chain_end_mm": list(end), **witness})
            if len(hits) != 1:
                raise ValueError(
                    "Each actual owned pad must contact one unique chain end in material"
                )
            witnesses.append(hits[0])
        if witnesses[0]["chain_end_mm"] == witnesses[1]["chain_end_mm"]:
            raise ValueError("Both actual pads matched the same chain end")
        return {
            "verified": True,
            "status": "verified",
            "segment_count": len(edges),
            "trace_object_count": len(created),
            "endpoint_witnesses": witnesses,
            "basis": (
                "one exact connected chain and positive centerline intervals in owned pad material"
            ),
            "not_checked": ["clearance", "DRC", "manufacturing", "thermal/tie"],
        }
    except (ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        return {"verified": False, "status": "incomplete", "reason": str(error)}


def compare(plan, observed):
    try:
        return _compare(plan, observed)
    except (KeyError, ValueError, TypeError, IndexError, OverflowError) as error:
        return {
            "valid": False,
            "protection_valid": False,
            "completion_valid": False,
            "checks": [{"name": "complete independent native readback", "passed": False}],
            "physical_connection": {"verified": False, "reason": str(error)},
            "native_connectivity_verified": False,
            "drc_executed": False,
        }


def _point_segment_distance(point, start, end):
    vector = [end[i] - start[i] for i in (0, 1)]
    length2 = sum(v * v for v in vector)
    if not length2:
        return math.dist(point, start)
    t = max(0.0, min(1.0, sum((point[i] - start[i]) * vector[i] for i in (0, 1)) / length2))
    return math.dist(point, [start[i] + t * vector[i] for i in (0, 1)])


def _segment_distance(a, b, c, d):
    from .endpoint_material import _segments_meet

    if _segments_meet(a, b, c, d):
        return 0.0
    return min(
        _point_segment_distance(a, c, d),
        _point_segment_distance(b, c, d),
        _point_segment_distance(c, a, b),
        _point_segment_distance(d, a, b),
    )


def foreign_material_separation(plan, created):
    """Conservative full-width separation from every other-net pad/old trace.

    Pad outer fill is considered occupied even inside its cutouts and drill.
    This may reject a valid route but never uses an unobserved void as clearance.
    No spacing-rule compliance is inferred from absence of copper contact.
    """
    try:
        from .endpoint_material import _inside, _material

        pads = []
        for row in plan["before"]["all_pin_pad_material"]:
            if row["net_uid"] == plan["target"]["net_uid"]:
                continue
            material = row["material"]
            if material.get("complete") is not True:
                raise ValueError("A foreign pad material observation is incomplete")
            for pad in material["pads"]:
                for shape in pad["geometry"]:
                    outer, _ = _material(shape)
                    pads.append(outer)
        old_segments = []
        for row in plan["before"]["routing_objects"]:
            if row["net"] == plan["target"]["net"]:
                raise ValueError("An existing target routing object is outside the contract")
            shape = row["geometry"]
            if shape.get("layer") != plan["target"]["layer"]:
                continue
            width = shape["width"]
            if (
                row["kind"] != "trace"
                or type(width) not in (int, float)
                or not math.isfinite(width)
                or width <= 0
            ):
                raise ValueError(
                    "Foreign existing copper is not an observed straight positive-width trace"
                )
            points = [_point(p) for p in shape["path"]]
            old_segments.extend((a, b, width / 2) for a, b in zip(points, points[1:], strict=False))
        checked = 0
        for row in created:
            shape = row["geometry"]
            points, radius = [_point(p) for p in shape["path"]], shape["width"] / 2
            if not math.isfinite(radius) or radius <= 0:
                raise ValueError("Actual new trace width is invalid")
            for a, b in zip(points, points[1:], strict=False):
                for outer in pads:
                    if "circle" in outer:
                        x, y, r = outer["circle"]
                        separated = _point_segment_distance([x, y], a, b) > r + radius
                    else:
                        polygon = outer["polygon"]
                        separated = (
                            not _inside(a, outer)
                            and not _inside(b, outer)
                            and all(
                                _segment_distance(a, b, c, d) > radius
                                for c, d in zip(polygon, polygon[1:] + polygon[:1], strict=True)
                            )
                        )
                    if not separated:
                        raise ValueError(
                            "New full-width copper may contact another net's owned pad"
                        )
                    checked += 1
                for c, d, old_radius in old_segments:
                    if _segment_distance(a, b, c, d) <= radius + old_radius:
                        raise ValueError(
                            "New full-width copper may contact another net's existing trace"
                        )
                    checked += 1
        return {
            "verified": True,
            "comparisons": checked,
            "basis": (
                "strict full-width separation from observed foreign pad outers and trace capsules"
            ),
            "not_checked": ["spacing rules", "DRC", "manufacturing"],
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as error:
        return {"verified": False, "reason": str(error)}


def _raw_trace_rows(state):
    """Bind raw explicit-mm observations to the existing canonical snapshot.

    Canonical8 is only the reader-to-reader identity check. Physical graph and
    material masks continue to use the unrounded native values below.
    """
    raw = state["native_trace_material"]
    observed = _inventory(state["routing_objects"])
    if not isinstance(raw, list) or len(raw) > MAX_OBJECTS:
        raise ValueError("Raw native trace inventory is missing or unbounded")
    raw_uids = [r["uid"] for r in raw]
    expected_uids = [r["uid"] for r in observed.values() if r["kind"] == "trace"]
    if len(set(raw_uids)) != len(raw_uids) or set(raw_uids) != set(expected_uids):
        raise ValueError("Raw trace identities do not equal the complete captured inventory")
    nets, result, budget = _nets(state), [], 0
    for row in raw:
        native = observed[("trace", row["uid"])]
        points, width = row["points_array_mm"], row["width_mm"]
        if (
            type(width) not in (int, float)
            or not math.isfinite(width)
            or width <= 0
            or not isinstance(points, (list, tuple))
            or len(points) != 3
            or any(not isinstance(p, (list, tuple)) for p in points)
            or len({len(p) for p in points}) != 1
            or not 2 <= len(points[0]) <= MAX_OBJECTS
            or any(type(v) not in (int, float) or not math.isfinite(v) for p in points for v in p)
            or row["net"] not in nets
            or row["net_uid"] != nets[row["net"]]["uid"]
            or type(row["layer"]) is not int
            or row["layer"] < 1
        ):
            raise ValueError("Raw native trace width, unit, points or ownership are incomplete")
        budget += len(points[0])
        if budget > 16384:
            raise ValueError("Total raw native trace point budget exceeded")
        shape = {
            "uid": row["uid"],
            "kind": "trace",
            "net": row["net"],
            "layer": row["layer"],
            "width": width,
            "path": [list(p) for p in zip(*points, strict=True)],
        }
        if _canonical(shape) != native["geometry"] or row["net"] != native["net"]:
            raise ValueError("Raw native trace and canonical geometry observations disagree")
        result.append(
            {
                **copy.deepcopy(row),
                "routing_row": {
                    "kind": "trace",
                    "uid": row["uid"],
                    "net": row["net"],
                    "geometry": shape,
                },
            }
        )
    return sorted(result, key=lambda r: r["uid"])


def _native_endpoint_contract(plan):
    try:
        state, target = plan["before"], plan["target"]
        _round_hole_inventory(state)
        endpoints = plan["endpoints"]
        if any(row["kind"] == "via" for row in state["routing_objects"]) or (
            plan["request"].get("continuation") != CONTINUATION
            and any(row["net"] == target["net"] for row in state["routing_objects"])
        ):
            return False
        planes = state["plane_inventory"]
        if (
            planes.get("complete") is not True
            or any(planes[key] for key in ("shapes", "generated", "assignments"))
            or state["geometry"]["model"]["planes"]
        ):
            return False
        pins = [
            (str(c["uid"]), p["number"])
            for c in state["geometry"]["components"]
            for p in c["geometry"]
            if p["net"] == target["net"]
        ]
        count = len(endpoints)
        _six_member_contract(
            target["net"], [(endpoint["refdes"], endpoint["pin"]) for endpoint in endpoints]
        )
        if (
            count not in (2, 3, 6)
            or target["net"] != plan["request"]["net"]
            or target["layer"] != plan["request"]["layer"]
            or plan.get("connection_contract")
            != _bound_connection_contract(state, plan["request"], endpoints)
            or len(pins) != count
            or len(set(pins)) != count
            or set(pins) != {(e["component_uid"], e["pin"]) for e in endpoints}
        ):
            return False
        target_identities = [row for row in state["pin_identities"] if row["net"] == target["net"]]
        if (
            len(target_identities) != count
            or len({e["pin_uid"] for e in endpoints}) != count
            or {r["pin_uid"] for r in target_identities} != {e["pin_uid"] for e in endpoints}
        ):
            return False
        old = _nets(state)[target["net"]]
        if (
            old["uid"] != target["net_uid"]
            or old["opens"] != plan["connection_contract"]["native_opens_before"]
            or old["routed"] is not False
            or plan["expected_connectivity"] != {**old, "opens": 0, "routed": True}
        ):
            return False
        for endpoint in endpoints:
            if endpoint["net"] != target["net"] or endpoint["net_uid"] != target["net_uid"]:
                return False
            components = [
                c
                for c in state["geometry"]["components"]
                if str(c["uid"]) == endpoint["component_uid"] and c["refdes"] == endpoint["refdes"]
            ]
            if len(components) != 1:
                return False
            owned = [
                p
                for p in components[0]["geometry"]
                if p["number"] == endpoint["pin"] and p["net"] == target["net"]
            ]
            identities = [
                r
                for r in state["pin_identities"]
                if r["component_uid"] == endpoint["component_uid"]
                and r["refdes"] == endpoint["refdes"]
                and r["pin_uid"] == endpoint["pin_uid"]
                and r["pin"] == endpoint["pin"]
                and r["net"] == target["net"]
                and r["net_uid"] == target["net_uid"]
            ]
            materials = [
                r
                for r in state["all_pin_pad_material"]
                if r["component_uid"] == endpoint["component_uid"]
                and r["refdes"] == endpoint["refdes"]
                and r["pin_uid"] == endpoint["pin_uid"]
                and r["pin"] == endpoint["pin"]
                and r["net"] == target["net"]
                and r["net_uid"] == target["net_uid"]
            ]
            if (
                len(owned) != 1
                or len(identities) != 1
                or len(materials) != 1
                or materials[0]["material"].get("complete") is not True
                or materials[0]["center_mm"] != endpoint["native_center_mm"]
                or materials[0]["material"] != endpoint["native_material_observation"]
                or owned[0]["center_mm"] != endpoint["center_mm"]
                or endpoint.get("captured_pads", endpoint.get("pads"))
                != [p for p in owned[0]["copper"] if p["layer"] == target["layer"]]
            ):
                return False
        return True
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def _compare(plan, observed):
    before = plan["before"]
    old, new = _inventory(before["routing_objects"]), _inventory(observed["routing_objects"])
    created = [r for k, r in new.items() if k not in old]
    old_nets, new_nets = _nets(before), _nets(observed)
    checks = []

    def check(name, value, category="protection"):
        checks.append({"name": name, "passed": bool(value), "category": category})

    target_net = _nets(before)[plan["target"]["net"]]
    endpoint_contract = {
        "before": before,
        "target": plan["target"],
        "endpoints": plan["endpoints"],
        "request": {"points": [e["center_mm"] for e in plan["endpoints"]]},
        "target_contract": {
            "before_connectivity": target_net,
            "expected_connectivity": plan["expected_connectivity"],
        },
    }
    native_mask_mode = plan.get("material_verification_mode") == "native-mask"
    check(
        "all exact target physical pad members and owning-net identities bound",
        _native_endpoint_contract(plan)
        if native_mask_mode
        else board_trace._endpoint_contract(endpoint_contract),
    )
    check(
        "frozen native configuration equals the bounded supported contract",
        digest(plan["configuration"]) == digest(configuration(before, plan["request"])),
    )
    check(
        "complete bounded member and expected connectivity contract",
        plan.get("connection_contract")
        == _bound_connection_contract(before, plan["request"], plan["endpoints"]),
    )

    check(
        "existing routing identities and exact geometry remain",
        all(new.get(k) == r for k, r in old.items()),
    )
    check(
        "only target-net same-layer traces added and no via",
        all(
            r["kind"] == "trace"
            and r["net"] == plan["target"]["net"]
            and r["geometry"].get("net") == plan["target"]["net"]
            and r["geometry"].get("layer") == plan["target"]["layer"]
            for r in created
        ),
    )
    for state in (before, observed):
        for kind in ("trace", "via"):
            check(
                "complete model/native " + kind + " inventory",
                Counter(
                    digest(r["geometry"]) for r in state["routing_objects"] if r["kind"] == kind
                )
                == Counter(digest(r) for r in state["geometry"]["model"][kind + "s"]),
            )
    protected_before, protected_after = copy.deepcopy(before), copy.deepcopy(observed)
    raw_created = None
    native_evidence = None
    if native_mask_mode:
        from . import native_pad_mask

        raw_before, raw_after = _raw_trace_rows(before), _raw_trace_rows(observed)
        raw_created = [r for r in raw_after if r["uid"] not in {v["uid"] for v in raw_before}]
        check("all actual raw trace identities/units bind the captured complete routing", True)
        check(
            "all old raw trace paths and widths remain exactly",
            all(
                next((v for v in raw_after if v["uid"] == row["uid"]), None) == row
                for row in raw_before
            ),
        )
        native_evidence = native_pad_mask.verify_evidence(observed, plan["request"])
        check(
            "frozen native mask input evidence is independently complete",
            native_pad_mask.verify_evidence(before, plan["request"]).get("valid") is True,
        )
        check(
            "complete fresh native mask input and temporary-object evidence",
            native_evidence.get("valid") is True,
        )
        for state in (protected_before, protected_after):
            state.pop("native_mask_proof")
        new_uids = {r["uid"] for r in raw_created}
        protected_after["native_trace_material"] = [
            row for row in protected_after["native_trace_material"] if row["uid"] not in new_uids
        ]
    protected_before.pop("routing_objects")
    protected_after.pop("routing_objects")
    for row in created:
        if row["kind"] == "trace":
            protected_after["geometry"]["model"]["traces"].remove(row["geometry"])
    for state in (protected_before, protected_after):
        state["geometry"]["model"].pop("connectivity")
        state["geometry"]["model"].pop("unrouted")
    check(
        "all non-new geometry, text, rules, pins and planes remain exactly",
        protected_before == protected_after,
    )
    check(
        "all net identities remain",
        {n: r["uid"] for n, r in old_nets.items()} == {n: r["uid"] for n, r in new_nets.items()},
    )
    target = plan["target"]["net"]
    check(
        "other-network connectivity remains",
        {n: r for n, r in old_nets.items() if n != target}
        == {n: r for n, r in new_nets.items() if n != target},
    )
    check(
        "complete unrouted projection",
        observed["geometry"]["model"]["unrouted"]
        == [r for r in new_nets.values() if not r["routed"] or r["opens"] != 0],
    )
    if native_mask_mode:
        if len(plan["endpoints"]) in (3, 6):
            graph = native_pad_mask.verify_material_graph(observed, plan["request"])
            physical = {
                "verified": graph.get("valid") is True,
                "status": "verified" if graph.get("valid") is True else "incomplete",
                "trace_object_count": len(raw_created),
                "all_target_trace_object_count": sum(
                    row["net"] == plan["target"]["net"] for row in raw_after
                ),
                "existing_target_trace_object_count": sum(
                    row["net"] == plan["target"]["net"] for row in raw_before
                ),
                "member_count": len(plan["endpoints"]),
                "material_graph": graph,
                "basis": (
                    "all actual owned pins and all full-width target copper, old and new, "
                    "in one native material graph"
                ),
                "not_checked": ["spacing rules", "DRC", "manufacturing", "thermal/tie"],
            }
            if plan["request"].get("continuation") == CONTINUATION:
                # A complete but disconnected observation remains useful data;
                # only the unchanged completion verifier authorizes Save.
                physical["material_component_observation"] = (
                    native_pad_mask.observe_material_components(observed, plan["request"])
                )
        else:
            physical = physical_chain(
                plan,
                [r["routing_row"] for r in raw_created],
                mask_proof=observed["native_mask_proof"],
            )
        foreign = {
            "verified": native_evidence.get("valid") is True,
            "native_mask_evidence": native_evidence,
            "basis": (
                "complete native filled-mask matrix with strict native-extrema separation "
                "or validated native Touch exclusion; not a spacing-rule proof"
            ),
        }
    else:
        physical = physical_chain(plan, created)
        foreign = foreign_material_separation(plan, created)
    check(
        "actual full-width new copper remains separated from all foreign pad/trace material",
        foreign["verified"],
    )
    native = new_nets.get(target) == plan["expected_connectivity"]
    check("actual target connectivity meets frozen initial to zero opens", native, "completion")
    check(
        "independent new copper contacts all actual owned target pads in the required graph",
        physical["verified"],
        "completion",
    )
    protection = all(c["passed"] for c in checks if c["category"] == "protection")
    completion = all(c["passed"] for c in checks if c["category"] == "completion")
    return {
        "valid": protection and completion,
        "protection_valid": protection,
        "completion_valid": completion,
        "checks": checks,
        "created_objects": created,
        "physical_connection": physical,
        "foreign_material_separation": foreign,
        "native_connectivity_verified": native,
        "drc_executed": False,
    }


def _files(params, board):
    if not params.get("source_backup"):
        raise ValueError("A complete closed source_backup is required for isolated diagnostic mode")
    return input_files(params["project"], board, params["source_backup"])


def _source_backup(path, project, board):
    archive = Path(str(path)).resolve()
    payload = archive.read_bytes()
    if len(payload) > 1024 * 1024 * 1024:
        raise ValueError("Source backup exceeds bounded 1GiB archive size")
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if (
            not names
            or len(names) > 10001
            or len(set(n.casefold() for n in names)) != len(names)
            or any(
                n.startswith(("/", "\\")) or "\\" in n or ":" in n or ".." in PurePosixPath(n).parts
                for n in names
            )
            or sum(r.file_size for r in source.infolist()) > 2 * 1024 * 1024 * 1024
        ):
            raise ValueError("Source backup member identity/count/size is unsafe")
        if source.testzip() is not None:
            raise ValueError("Source backup CRC verification failed")
        meta = json.loads(source.read("xpedition-cli-backup.json"))
        members = [n for n in names if n != "xpedition-cli-backup.json"]
        if (
            meta.get("all_files") is not True
            or meta.get("skipped") != []
            or type(meta.get("files")) is not int
            or meta["files"] != len(members)
            or set(meta.get("sha256", {})) != set(members)
            or meta.get("project") != project.name
            or "database/icdb.dat" not in members
            or board.relative_to(project.parent).as_posix() not in members
        ):
            raise ValueError("Source backup is not a complete project/board/icdb recovery source")
        byte_count = 0
        for name in members:
            data = source.read(name)
            byte_count += len(data)
            if hashlib.sha256(data).hexdigest() != meta["sha256"][name]:
                raise ValueError("Source backup member hash differs: " + name)
        if type(meta.get("bytes")) is not int or meta["bytes"] != byte_count:
            raise ValueError("Source backup total uncompressed bytes differ")
    return {
        "path": str(archive),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "files": len(members),
        "all_files": True,
        "skipped": [],
        "crc_and_members_verified": True,
        "icdb_source_sha256": meta["sha256"]["database/icdb.dat"],
        "basis": "closed source archive only; not the live opaque database/cache version",
    }


def _non_rule_file(relative):
    lower = relative.lower()
    return any(
        lower.startswith("database/cdbsvr/" + folder + "/")
        for folder in ("log", "plog", "pimlog", "dbcheck", "dbupgrade")
    ) or lower in {"database/cdbsvr/events/events.dat", "database/cdbsvr/events/events.idx"}


def _dependency_identity(dependencies):
    required = {
        "source_backup",
        "live_files",
        "live_unavailable",
        "non_rule_observed_files",
        "live_file_count",
        "non_rule_exclusion_policy",
        "limitations",
    }
    if not isinstance(dependencies, dict) or not required <= dependencies.keys():
        raise ValueError("Complete raw dependency observations are required")
    files, logs, unread = (
        dependencies[k] for k in ("live_files", "non_rule_observed_files", "live_unavailable")
    )
    if (
        any(not isinstance(rows, list) for rows in (files, logs, unread))
        or type(dependencies["live_file_count"]) is not int
        or dependencies["live_file_count"] != len(files) + len(logs) + len(unread)
    ):
        raise ValueError("Raw dependency observation counts are incomplete")
    paths = [row["path"] for row in [*files, *logs, *unread]]
    if len(set(paths)) != len(paths) or any(not Path(path).is_absolute() for path in paths):
        raise ValueError("Raw dependency file identities are missing or duplicated")
    for row in [*files, *logs]:
        sha = row["sha256"]
        if (
            type(row["bytes"]) is not int
            or row["bytes"] < 0
            or not isinstance(sha, str)
            or len(sha) != 64
            or any(c not in "0123456789abcdef" for c in sha)
        ):
            raise ValueError("Raw dependency content pin is incomplete")
    result = copy.deepcopy(dependencies)
    result.pop("non_rule_observed_files", None)
    result.pop("live_file_count", None)
    return result


def _allowed_output_files(identity, inputs):
    if not isinstance(identity, dict) or not identity.get("board"):
        return []
    board = Path(identity["board"]).resolve()
    directory = board.parent / "LogFiles"
    files = {Path(row["path"]): row for row in inputs["live_files"]}
    indices = []
    for path in files:
        if path.parent != directory or not path.name.lower().startswith("autoroutereport"):
            continue
        matched = re.fullmatch(r"AutoRouteReport([0-9]{2})\.txt", path.name)
        if matched is None or not 1 <= int(matched[1]) <= 99:
            raise ValueError("Native autoroute report sequence/name is unknown")
        indices.append(int(matched[1]))
    indices.sort()
    if indices != list(range(1, len(indices) + 1)) or len(indices) >= 99:
        raise ValueError("Native autoroute report sequence has gaps or is exhausted")
    outputs = [
        {
            "path": str(board),
            "role": "primary_board",
            "reason": (
                "only the existing primary Layout board may acquire native route-output bytes; "
                "actual copper and all protected native objects must independently verify"
            ),
            "may_create": False,
        },
        {
            "path": str(directory / f"AutoRouteReport{len(indices) + 1:02}.txt"),
            "role": "native_autoroute_report",
            "may_create": True,
            "max_bytes": 1048576,
            "existing_report_indices": indices,
            "reason": (
                "only this predicted next native report may be created; report text is "
                "evidence, not physical or complete-rule proof"
            ),
        },
    ]
    mvo = directory / "MVO_rules_log.txt"
    if mvo in files:
        outputs.append(
            {
                "path": str(mvo),
                "role": "native_mvo_warning_log",
                "may_create": False,
                "max_bytes": 1048576,
                "reason": (
                    "existing native MVO reader diagnostic may be overwritten; warnings "
                    "and unsupported-rule semantics remain unresolved"
                ),
            }
        )
    return outputs


def _native_text(data):
    if len(data) > 1048576:
        raise ValueError("Native route diagnostic text exceeds the declared 1MiB bound")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
        text = data.decode(encoding)
    else:
        try:
            encoding, text = "utf-8-sig", data.decode("utf-8-sig")
        except UnicodeDecodeError:
            encoding = "mbcs"
            text = data.decode(encoding)
    if any(ord(c) < 32 and c not in "\t\n\r\f" for c in text):
        raise ValueError("Native route diagnostic is not bounded plain text")
    return {"encoding": encoding, "text": text, "_untrusted": ["text"]}


def _output_text_valid(row, contract):
    if contract["role"] == "primary_board":
        return True
    observation = row.get("text_observation")
    return (
        type(row.get("bytes")) is int
        and row["bytes"] <= contract["max_bytes"]
        and isinstance(observation, dict)
        and isinstance(observation.get("text"), str)
        and observation.get("_untrusted") == ["text"]
        and not any(ord(c) < 32 and c not in "\t\n\r\f" for c in observation["text"])
        and (
            contract["role"] != "native_autoroute_report"
            or "Xpedition Layout" in observation["text"]
        )
    )


def dependency_changes(before, after, allowed_output_files):
    """Allow only declared existing route output; never rebaseline library/rules."""
    try:
        old, new = _dependency_identity(before), _dependency_identity(after)
        old_rows, new_rows = old.pop("live_files"), new.pop("live_files")
        old_files = {r["path"]: r for r in old_rows}
        new_files = {r["path"]: r for r in new_rows}
        if len(old_files) != len(old_rows) or len(new_files) != len(new_rows) or old != new:
            return {"valid": False, "reason": "source, lock boundary or non-log file set changed"}
        allowed = {r["path"]: r for r in allowed_output_files}
        changes = []
        for path in sorted(old_files.keys() | new_files.keys()):
            prior, current = old_files.get(path), new_files.get(path)
            if prior != current:
                contract = allowed.get(path)
                permitted = (
                    current is not None
                    and contract is not None
                    and (prior is not None or contract["may_create"] is True)
                    and _output_text_valid(current, contract)
                )
                changes.append(
                    {
                        "path": path,
                        "before": prior,
                        "after": current,
                        "allowed": permitted,
                        "reason": allowed.get(path, {}).get("reason", "protected input"),
                    }
                )
        return {
            "valid": all(r["allowed"] for r in changes),
            "changes": changes,
            "allowed_output_files": copy.deepcopy(allowed_output_files),
            "opaque_boundary": copy.deepcopy(before.get("live_unavailable", [])),
        }
    except (KeyError, TypeError, ValueError) as error:
        return {"valid": False, "reason": str(error)}


def input_files(project, board, source_backup):
    """Audited closed provenance, live inputs, and the explicitly unknown lock.

    Only actual project icdb.dat may be unavailable live. Its source-only hash
    remains distinct. Server journals are individually inventoried, not frozen
    rule inputs. Every other file/new path is bound; other unreadability stops.
    """
    project, board = Path(str(project)).resolve(), Path(str(board)).resolve()
    root = project.parent
    if not project.is_file() or not board.is_file() or root not in board.parents:
        raise ValueError("Actual project and board must be files in the declared project tree")
    source = _source_backup(source_backup, project, board)
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    if not paths or len(paths) > 10000:
        raise ValueError("Whole-project dependency file budget is empty or exceeded")
    if root / "database" / "icdb.dat" not in paths:
        raise ValueError("The current project's actual database/icdb.dat is missing")
    if any(path.is_symlink() or root not in path.resolve().parents for path in paths):
        raise ValueError("Project dependency resolves outside the actual project tree")
    rows, logs, unread, unavailable = [], [], [], []
    for path in paths:
        try:
            data = path.read_bytes()
            row = {
                "path": str(path),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            if path.parent == board.parent / "LogFiles" and (
                re.fullmatch(r"AutoRouteReport[0-9]{2}\.txt", path.name)
                or path.name == "MVO_rules_log.txt"
            ):
                row["text_observation"] = _native_text(data)
            (logs if _non_rule_file(path.relative_to(root).as_posix()) else rows).append(row)
        except OSError as error:
            row = {"path": str(path), "error": type(error).__name__, "message": str(error)}
            if path == root / "database" / "icdb.dat":
                unavailable.append(
                    {
                        "path": str(path),
                        "live_read": "unavailable",
                        "source_only_sha256": source["icdb_source_sha256"],
                    }
                )
            else:
                unread.append(row)
    if unread:
        raise ValueError("Whole-project live input files unreadable: " + repr(unread))
    return {
        "source_backup": source,
        "live_files": rows,
        "live_unavailable": unavailable,
        "non_rule_observed_files": logs,
        "live_file_count": len(paths),
        "non_rule_exclusion_policy": (
            "explicit cdbsvr log/plog/PIMlog/dbcheck/dbupgrade/events journals"
        ),
        "limitations": [
            "Live icdb may be locked and its current bytes are then unknown",
            "Native constraint-cache complete version is unproven",
            "Checkpoint binds the same retained owner, not all hidden caches",
        ],
    }


def _call(result, a, name, function, **details):
    event = {
        "call": name,
        "sequence": len(result["native_calls_returned"]) + 1,
        "dispatched": True,
        "returned": False,
        **details,
    }
    result["native_calls_returned"].append(event)
    value = a._native_call("routepass." + name, function)
    event.update(
        returned=True,
        return_kind="none"
        if value is None
        else "true"
        if value is True
        else "false"
        if value is False
        else type(value).__name__,
    )
    return value, event


def _void(result, a, name, function, **details):
    value, event = _call(result, a, name, function, **details)
    if value is not None:
        raise a.AdapterError(
            "E_VERIFY_INCOMPLETE",
            "Formal void RoutePass call returned an unexpected value",
            {"call": name},
        )
    return event


def observe_request(params, client, a):
    _, board, app, doc = active(params, client, a, require_project=False)
    identity = board_labels._identity(app, board)
    request = validate(params["routepass_request"])
    state = observe(doc, a, request)
    if board_labels._identity(app, board) != identity:
        raise a.AdapterError("E_CONFLICT", "Native observation identity changed")
    result = {"board": str(board), "state": state, "identity": identity}
    if request.get("continuation") == CONTINUATION:
        from . import native_pad_mask

        result["native_routepass_components"] = native_pad_mask.observe_material_components(
            state, request
        )
    return result


def execute(params, client, a):
    state = None
    try:
        request = validate(params["routepass_request"])
        _, board, app, doc = active(params, client, a, require_project=False)
        identity = board_labels._identity(app, board)
        state = observe(doc, a, request)
        files = _files(params, board)
        plan = bind(state, request, files, a, identity)
        if board_labels._identity(app, board) != identity:
            raise ValueError("Target identity changed during preflight")
    except Exception as error:
        raise a.AdapterError(
            getattr(error, "code", "E_VERIFY_INCOMPLETE"),
            "Native RoutePass preflight is incomplete; no pass dispatched",
            {
                "write_attempted": False,
                "cause": str(error),
                "preflight_observation": state,
                **(getattr(error, "details", {}) or {}),
            },
        ) from error
    result = {
        "pcb": str(board),
        "routepass": plan,
        "state_guard": GUARD,
        "state_digest": plan["fingerprint"],
        "applied": False,
        "saved": False,
        "persistence_verified": False,
        "write_attempted": False,
        "change_status": "not_applied",
        "persistence_state": "unchanged",
        "stages": dict.fromkeys(
            ("configuration", "route", "readback", "protection", "save", "close", "reopen"),
            "not_started",
        ),
        "native_calls_returned": [],
        "limits": limitations(),
        "reminders_before": _layout_advisories(state),
        "route_projection_boundary": {
            "native_candidate_path_observed": False,
            "new_trace_text_projection_checked": False,
            "reason": (
                "Before native RoutePass.Go there is no generated candidate path; new trace "
                "versus text projections are unreviewed. Baseline advisories cover only the "
                "frozen available model and do not establish a clear layout."
            ),
        },
        "observable_state_guard_evidence": {
            "guard": GUARD,
            "atomic_database_freeze": False,
            "exclusive_database_lock": False,
            "opaque_constraint_cache_version_proven": False,
            "request": copy.deepcopy(request),
            "observations": [],
            "operator_assumption": "one authorised CAD operator; external edits are not locked",
        },
    }
    _record_observation(
        result,
        "initial_bind",
        "routepass.before",
        state,
        files,
        identity,
        material_basis="full_native_observation",
    )
    if not params.get("apply"):
        return result
    if params.get("expected") != plan["fingerprint"]:
        raise a.AdapterError(
            "E_CONFLICT",
            "RoutePass frozen target, inputs or state drifted",
            {"write_attempted": False},
        )
    return _execute_guarded(params, plan, result, app, board, doc, a)


def _layout_advisories(state):
    """Derived reminders are outside the design identity and never authorize persistence."""
    from .layout_reminders import report

    model = state["geometry"]["model"]
    try:
        result = report(model)
    except Exception as error:
        return {
            "layout_reminders": [],
            "layout_reminder_coverage": {
                "status": "partial",
                "source": "frozen_routepass_state.geometry.model",
                "report_error": {"cause": type(error).__name__, "message": str(error)},
                "unreviewed_collections": [
                    {"collection": name, "complete": False, "reason": "advisory_report_failed"}
                    for name in (
                        "texts",
                        "pads",
                        "silk",
                        "traces",
                        "outline",
                        "component_envelopes",
                    )
                ],
                "empty_warnings_mean_layout_clear": False,
                "manual_visual_review_required": True,
            },
        }
    coverage = result["layout_reminder_coverage"]
    coverage["source"] = "frozen_routepass_state.geometry.model"
    coverage["layout_observation_present"] = isinstance(model.get("layout_observation"), dict)
    if not coverage["layout_observation_present"]:
        coverage["status"] = "partial"
        coverage["routepass_observation_limit"] = (
            "The strict physical model does not retain layout_observation provenance; collection "
            "enumeration, unread conversion records, actual layer and complete text-envelope "
            "coverage cannot be established from this model. Read the explicit unchecked ranges "
            "and perform full visual review; no omitted object is declared clear."
        )
    return result


def _execute_guarded(params, plan, result, app, board, doc, a):
    request, config, stages = plan["request"], plan["configuration"], result["stages"]
    if (
        plan.get("guard") != GUARD
        or not plan.get("can_apply")
        or digest(config) != digest(configuration(plan["before"], request))
        or board_labels._identity(app, board) != plan["identity"]
        or _dependency_identity(_files(params, board)) != _dependency_identity(plan["input_files"])
    ):
        raise a.AdapterError(
            "E_CONFLICT",
            "Native RoutePass identity/configuration/input drift before dispatch",
            {"write_attempted": False},
        )
    try:
        checkpoint, checkpoint_error = _checkpoint(doc)
        result["checkpoint_evidence"] = []

        def check_checkpoint(phase):
            _observe_checkpoint(
                result,
                "checkpoint_evidence",
                doc,
                checkpoint,
                checkpoint_error,
                phase,
                plan["identity"],
            )

        check_checkpoint("before_configuration")
        # The collection is explicitly supplied to NetsItem. No selected-net
        # global or SelectedPins observation is treated as route ownership.
        net = a._find_net(doc, request["net"])
        if str(net.Name) != request["net"] or str(net.UniqueId) != plan["target"]["net_uid"]:
            raise ValueError("Actual selected native net identity differs")
        selected = app.Utility.NewNets()
        if type(selected.Count) is not int or selected.Count != 0:
            raise ValueError("Native explicit net collection is not empty")
        selected.Add(net)
        selected_rows = list(a._items(selected, strict=True))
        if (
            selected.Count != 1
            or len(selected_rows) != 1
            or str(selected_rows[0].Name) != request["net"]
            or str(selected_rows[0].UniqueId) != plan["target"]["net_uid"]
        ):
            raise ValueError("Actual explicit native net collection differs")
        stages["configuration"] = "started"
        route = doc.NewRoutePass()
        _void(
            result,
            a,
            "PassType",
            lambda: route.PassType(*config["pass_type"]),
            arguments=config["pass_type"],
        )
        _void(
            result, a, "Config", lambda: route.Config(*config["config"]), arguments=config["config"]
        )
        _void(
            result,
            a,
            "Items",
            lambda: route.Items(config["items"], selected),
            item_type=config["items"],
            net_uid=plan["target"]["net_uid"],
        )
        _void(result, a, "Order", lambda: route.Order(*config["order"]), arguments=config["order"])
        for layer in config["remove_layers"]:
            _void(
                result,
                a,
                "LayerSelect",
                lambda layer=layer: route.LayerSelect(3, layer),
                arguments=[3, layer],
            )
        _void(
            result,
            a,
            "LayerSelect",
            lambda: route.LayerSelect(2, config["add_layer"]),
            arguments=[2, config["add_layer"]],
        )
        before_go_identity = board_labels._identity(app, board)
        before_go_state = observe(a._licensed_document(app), a, request)
        before_go_files = _files(params, board)
        result["state_before_go"] = before_go_state
        result["pre_go_input_observation"] = before_go_files
        if (
            before_go_identity != plan["identity"]
            or not _states_equal(before_go_state, plan["before"], request)
            or _dependency_identity(before_go_files) != _dependency_identity(plan["input_files"])
        ):
            raise ValueError("Target/configuration inputs changed immediately before Go")
        rows = list(a._items(selected, strict=True))
        if (
            selected.Count != 1
            or len(rows) != 1
            or str(rows[0].Name) != request["net"]
            or str(rows[0].UniqueId) != plan["target"]["net_uid"]
        ):
            raise ValueError("Explicit bound native net collection changed before Go")
        result["selected_net_evidence"] = {
            "count": 1,
            "net": str(rows[0].Name),
            "net_uid": str(rows[0].UniqueId),
        }
        _record_observation(
            result,
            "immediately_before_go",
            "state_before_go",
            before_go_state,
            before_go_files,
            before_go_identity,
            material_basis="full_native_observation",
        )
        check_checkpoint("immediately_before_go")
        stages["configuration"], stages["route"] = "dispatched", "started"
        result.update(write_attempted=True, change_status="unknown", persistence_state="unknown")
        _void(result, a, "Go", route.Go)
        stages["route"], stages["readback"] = "returned", "started"
        save_checkpoint, save_checkpoint_error = _checkpoint(doc)
        result["save_gate_checkpoint_evidence"] = []

        def check_save_checkpoint(phase):
            _observe_checkpoint(
                result,
                "save_gate_checkpoint_evidence",
                doc,
                save_checkpoint,
                save_checkpoint_error,
                phase,
                plan["identity"],
            )

        check_save_checkpoint("after_go_before_readback")
        save_inputs = None
        try:
            save_inputs = _files(params, board)
            result["post_route_input_observation"] = save_inputs
            delta = dependency_changes(
                plan["input_files"], save_inputs, plan["allowed_output_files"]
            )
        except Exception as error:
            result["post_route_input_observation_error"] = {
                "cause": type(error).__name__,
                "code": getattr(error, "code", None),
                "message": str(error),
            }
            delta = {"valid": False, "reason": "Post-Go dependency observation is incomplete"}
        result["dependency_change_evidence"] = delta
        current = observe(a._licensed_document(app), a, request)
        design_changed = design_state(current) != design_state(plan["before"])
        result.update(
            applied=design_changed,
            state_after=current,
            reminders_after=_layout_advisories(current),
            change_status="partial" if design_changed else "not_applied",
        )
        post_identity = board_labels._identity(app, board)
        if save_inputs is not None:
            _record_observation(
                result,
                "post_go_readback",
                "state_after",
                current,
                save_inputs,
                post_identity,
                material_basis="full_native_observation",
            )
        stages["readback"] = "complete"
        proof = compare(plan, current)
        result["verification"] = proof
        stages["protection"] = "passed" if proof["valid"] and delta["valid"] else "failed"
        if (
            not proof["valid"]
            or not delta["valid"]
            or board_labels._identity(app, board) != plan["identity"]
        ):
            raise a.AdapterError(
                "E_VERIFY_INCOMPLETE",
                "Native RoutePass output/protection incomplete; Save/Close/Open stopped",
            )
        fresh_before_save = observe_for_save(a._licensed_document(app), a, request, current)
        result["state_before_save"] = fresh_before_save
        pre_save_identity = board_labels._identity(app, board)
        if pre_save_identity != plan["identity"] or not _states_equal(
            current, fresh_before_save, request
        ):
            raise ValueError("Fresh native design/material inputs drifted before Save")
        pre_save_inputs = _files(params, board)
        result["pre_save_input_observation"] = pre_save_inputs
        if _dependency_identity(pre_save_inputs) != _dependency_identity(save_inputs):
            raise ValueError("Post-Go input dependencies drifted during verification; Save stopped")
        check_save_checkpoint("immediately_before_save")
        _record_observation(
            result,
            "immediately_before_save",
            "state_before_save",
            fresh_before_save,
            pre_save_inputs,
            pre_save_identity,
            material_basis="fresh_native_inputs_exact_mask_input_reuse",
        )
        result["change_status"] = "applied"
        stages["save"] = "started"
        saved, _ = _call(result, a, "Save", doc.Save)
        if saved is not True:
            raise a.AdapterError(
                "E_VERIFY_INCOMPLETE", "Save requires declared BOOL=True; Close/Open stopped"
            )
        result["saved"], stages["save"], stages["close"] = True, "acknowledged", "started"
        closed, event = _call(result, a, "Close", lambda: doc.Close(True))
        event["active_document_none"] = app.ActiveDocument is None
        if closed is not True or not event["active_document_none"]:
            raise a.AdapterError(
                "E_VERIFY_INCOMPLETE",
                "Close requires BOOL=True and empty ActiveDocument; Open stopped",
            )
        stages["close"], stages["reopen"] = "confirmed", "started"
        opened, event = _call(result, a, "Open", lambda: a._open_layout_document(app, board))
        if not isinstance(opened, tuple) or len(opened) != 2 or opened[0] is None:
            raise ValueError("Open did not return document and prompts")
        event["return_kind"] = "document_and_prompts"
        reopened = a._licensed_document(app)
        identity = board_labels._identity(app, board)
        if (
            Path(str(reopened.FullName)).resolve() != board.resolve()
            or identity != plan["identity"]
        ):
            raise ValueError("Reopened actual document/application identity differs")
        after = observe(reopened, a, request)
        result.update(state_reopened=after, geometry_after=after["geometry"])
        result["reopen_evidence"] = {
            "identity": identity,
            "authenticated": True,
            "board": str(board.resolve()),
            "state_sha256": digest(after),
            "observed_after_open_sequence": event["sequence"],
        }
        result["persistence_verified"] = (
            _states_equal(after, current, request) and compare(plan, after)["valid"]
        )
        stages["reopen"] = "verified" if result["persistence_verified"] else "mismatch"
        if not result["persistence_verified"] or not verify_receipt(result, plan, after)["valid"]:
            raise a.AdapterError(
                "E_VERIFY_INCOMPLETE", "RoutePass persistence evidence is incomplete"
            )
        result["persistence_state"] = "saved"
        return result
    except Exception as error:
        result["post_write_error"] = {
            "code": getattr(error, "code", "E_NATIVE_FAILURE"),
            "cause": type(error).__name__,
            "message": str(error),
        }
        raise a.AdapterError(
            getattr(error, "code", "E_NATIVE_FAILURE"),
            "Native RoutePass lifecycle incomplete; retain facts and do not replay",
            {"write_attempted": result["write_attempted"], "native_result": result},
        ) from error


def verify_receipt(result, bound, fresh_state):
    """Recompute completion and evidence; producer booleans are not authorities."""
    try:
        config = configuration(bound["before"], bound["request"])
        expected = [("PassType", "none"), ("Config", "none"), ("Items", "none"), ("Order", "none")]
        expected += [("LayerSelect", "none")] * (len(config["remove_layers"]) + 1)
        expected += [
            ("Go", "none"),
            ("Save", "true"),
            ("Close", "true"),
            ("Open", "document_and_prompts"),
        ]
        calls = result["native_calls_returned"]
        sequence_ok = len(calls) == len(expected) and all(
            row.get("call") == name
            and row.get("return_kind") == kind
            and row.get("sequence") == i + 1
            and type(row.get("sequence")) is int
            and row.get("dispatched") is True
            and row.get("returned") is True
            for i, (row, (name, kind)) in enumerate(zip(calls, expected, strict=True))
        )
        arguments_ok = (
            digest(calls[0].get("arguments")) == digest(config["pass_type"])
            and digest(calls[1].get("arguments")) == digest(config["config"])
            and calls[2].get("item_type") == 5
            and calls[2].get("net_uid") == bound["target"]["net_uid"]
            and digest(calls[3].get("arguments")) == digest(config["order"])
            and [r.get("arguments") for r in calls if r.get("call") == "LayerSelect"]
            == [[3, n] for n in config["remove_layers"]] + [[2, config["add_layer"]]]
        )
        identity = result["reopen_evidence"]
        reopen_ok = (
            identity.get("authenticated") is True
            and identity.get("identity") == bound["identity"]
            and identity.get("board") == result["pcb"]
            and identity.get("state_sha256") == digest(fresh_state)
            and identity.get("observed_after_open_sequence") == len(calls)
            and calls[-2].get("active_document_none") is True
        )
        stages = result["stages"]
        checkpoints = result.get("checkpoint_evidence")
        checkpoint_ok = _checkpoint_records(
            checkpoints, ["before_configuration", "immediately_before_go"], bound["identity"]
        )
        save_checkpoints = result.get("save_gate_checkpoint_evidence")
        save_checkpoint_ok = _checkpoint_records(
            save_checkpoints,
            ["after_go_before_readback", "immediately_before_save"],
            bound["identity"],
        )
        stage_ok = all(
            stages.get(k) == v
            for k, v in {
                "configuration": "dispatched",
                "route": "returned",
                "readback": "complete",
                "protection": "passed",
                "save": "acknowledged",
                "close": "confirmed",
                "reopen": "verified",
            }.items()
        )
        before_save = result["state_after"]
        output_contract_ok = bound.get("allowed_output_files") == _allowed_output_files(
            bound.get("identity"), bound["input_files"]
        )
        dependency_proof = dependency_changes(
            bound["input_files"],
            result["post_route_input_observation"],
            bound["allowed_output_files"],
        )
        pre_save_dependencies_stable = _dependency_identity(
            result["post_route_input_observation"]
        ) == _dependency_identity(result["pre_save_input_observation"])
        proof, prior_proof = compare(bound, fresh_state), compare(bound, before_save)
        actual_pre_save_proof = compare(bound, result["state_before_save"])
        checks = [
            {
                "name": "exact ordered native configuration, one Go and BOOL lifecycle",
                "passed": sequence_ok and arguments_ok,
            },
            {"name": "authenticated fresh actual reopened identity", "passed": reopen_ok},
            {"name": "all required lifecycle stages", "passed": stage_ok},
            {
                "name": "checkpoint observations retained without claiming an atomic freeze",
                "passed": checkpoint_ok,
            },
            {
                "name": "post-Go checkpoint observations retained as observations",
                "passed": save_checkpoint_ok,
            },
            {
                "name": "fresh observable native inputs and dependencies protect Go and Save",
                "passed": _observable_evidence(result, bound),
            },
            {
                "name": "only the explicitly bound primary board file may change before Save",
                "passed": output_contract_ok
                and dependency_proof["valid"]
                and dependency_proof == result.get("dependency_change_evidence"),
            },
            {
                "name": "raw post-Go and immediately pre-Save dependency observations match",
                "passed": pre_save_dependencies_stable,
            },
            {
                "name": "independent pre-save and reopened protection/contact",
                "passed": proof["valid"]
                and prior_proof["valid"]
                and actual_pre_save_proof["valid"],
            },
            {
                "name": "complete before-save and reopened state equal",
                "passed": _states_equal(before_save, fresh_state, bound["request"])
                and _states_equal(fresh_state, result.get("state_reopened"), bound["request"]),
            },
        ]
        return {"valid": all(c["passed"] for c in checks), "checks": checks, "comparison": proof}
    except (KeyError, TypeError, ValueError, IndexError) as error:
        return {
            "valid": False,
            "checks": [
                {
                    "name": "complete independent receipt evidence",
                    "passed": False,
                    "reason": str(error),
                }
            ],
        }
