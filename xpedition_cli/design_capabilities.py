"""Pure, bounded resource accounting; counts never replace native observations.

The whole board is counted, including foreign/Net0 material and existing copper.
An accepted estimate authorizes neither dispatch nor a geometry/rule assertion.
"""

from __future__ import annotations

import copy

from .cell_refresh import _canonical

PROFILE_ID = "whole-board-material-512-v1"
# Four polygon pairs use three masks each; the L control uses three factory
# rectangles, one union and one intersection. Factory array lengths remain an
# explicit reservation; the actual native point guard independently applies.
FACTORY_POINT_RESERVATION = 128
CONTROL_MASK_RESERVATION = 4 * 3 + 5
CONTROL_POINT_RESERVATION = 4 * 2 * 5 + 2 * 3 * FACTORY_POINT_RESERVATION
LIMITS = {
    "pins": 512,
    "traces": 256,
    "target_net_pins": 64,
    "contours": 4096,
    "raw_points": 32768,
    "mask_points": 32768,
    "comparisons": 8192,
    "temporary_masks": 20000,
    "candidate_path_points": 128,
    "round_holes": 512,
}
UNASSIGNED_NET_NAME = "(Net0)"


def contract():
    """Publish resource bounds without implying that a design passed them."""
    return {
        "profile_id": PROFILE_ID,
        "limits": copy.deepcopy(LIMITS),
        "component_pin_boundary": {
            "scope": "single-net whole-board material observation/connection profile",
            "independent_limit": None,
            "effective_limit": LIMITS["pins"],
            "basis": "Derived from the complete whole-board physical pin limit; "
            "there is no smaller independent per-component pin gate",
            "target_net_pins_scope": "All pins on one selected actual net across component "
            "owners; limits.target_net_pins is not a per-component pin limit",
            "requires_complete_native_observation": True,
            "qualification": "Count feasibility only; geometry, complete pad material, "
            "rules and persistence remain separately unproved",
        },
        "supported_outline_families": [
            "closed simple straight polygon without inner contours",
            "closed 13-record rounded rectangle; negative quarter arcs; native equality required",
        ],
        "observation_boundary": (
            "Complete physical pin/owner/net identities, all pad material including Net0, "
            "all old trace UIDs and raw paths; no target-only or sampled substitute"
        ),
        "unassigned_pin_policy": (
            "Exact (Net0) pins retain unique physical identities and one observed nonzero "
            "UID disjoint from named nets; all remain foreign material, never a target "
            "or an inferred conducting network"
        ),
        "supported_write": (
            "one explicit simple path, one actual net, one signal layer; no via/plane"
        ),
        "not_established": ["effective rules", "native geometry equality", "persistence", "DRC"],
        "reservation_basis": {
            "factory_array_points": FACTORY_POINT_RESERVATION,
            "factory_array_length": (
                "reservation only; actual arrays are read during native observation"
            ),
            "native_dynamic_limits_still_apply": True,
            "control_masks": CONTROL_MASK_RESERVATION,
            "control_points": CONTROL_POINT_RESERVATION,
        },
    }


def _identity(row):
    keys = ("component_uid", "pin_uid", "refdes", "pin", "net", "net_uid")
    values = tuple(row[key] for key in keys)
    if any(not isinstance(value, str) or not value for value in values) or any(
        values[index] == "0" for index in (0, 1, 5)
    ):
        raise ValueError("Complete actual pin, owner and net identity is required")
    return values


def named_net_ownership(net_rows, materials):
    """Bind real nets while retaining unassigned pins solely as foreign material.

    Layout's shared (Net0) object does not establish connectivity between its
    physical pins. Its identity is observed and protected, never inserted into
    the named-net census or used to qualify a routing target/trace.
    """
    if not isinstance(net_rows, list) or not isinstance(materials, list):
        raise ValueError("Complete native net and physical pin inventories are required")
    named, net_uids = {}, set()
    for row in net_rows:
        name, uid = row["net"], row["uid"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(uid, str)
            or not uid.strip()
            or uid == "0"
            or name == UNASSIGNED_NET_NAME
            or name in named
            or uid in net_uids
        ):
            raise ValueError("Complete unique named native net ownership is required")
        named[name] = uid
        net_uids.add(uid)
    pin_uids, owner_pins, owners, refs = set(), set(), {}, {}
    unassigned_uid = None
    for row in materials:
        owner, pin_uid, refdes, pin, name, uid = _identity(row)
        if (
            pin_uid in pin_uids
            or (owner, pin) in owner_pins
            or owners.setdefault(owner, refdes) != refdes
            or refs.setdefault(refdes, owner) != owner
        ):
            raise ValueError("Complete unique physical pin/component ownership is required")
        pin_uids.add(pin_uid)
        owner_pins.add((owner, pin))
        if name == UNASSIGNED_NET_NAME:
            if uid in net_uids or (unassigned_uid is not None and uid != unassigned_uid):
                raise ValueError(
                    "Unassigned native pin identity is ambiguous or aliases a named net"
                )
            unassigned_uid = uid
        elif named.get(name) != uid:
            raise ValueError(
                "Actual named pin owning net differs from complete native connectivity"
            )
    return named


def prospective_budget(state, target, added_trace=1):
    """Check complete retained inputs and reserve the next supported path.

    Reservation is deliberately conservative and separately reports observation
    and candidate peaks. Dynamic native allocation/point guards still apply to
    every actual read before a write. Missing inputs fail, never count as zero.
    """
    result = {
        "profile_id": PROFILE_ID,
        "valid": False,
        "complete": False,
        "write_attempted": False,
        "added_trace_reservation": added_trace,
        "counts": {},
        "limits": copy.deepcopy(LIMITS),
        "errors": [],
        "boundary": "resource feasibility only; complete fresh native safety gates remain required",
    }
    try:
        if type(added_trace) is not int or added_trace not in (0, 1):
            raise ValueError("Reserve zero or one supported new trace, never an unbounded batch")
        materials = state["all_pin_pad_material"]
        identities = state["pin_identities"]
        traces = state["native_trace_material"]
        routes = state["routing_objects"]
        if not all(isinstance(value, list) for value in (materials, identities, traces, routes)):
            raise ValueError("Whole-board inventories must be complete lists")
        material_ids, pin_ids = (
            [_identity(row) for row in materials],
            [_identity(row) for row in identities],
        )
        for inventory in (material_ids, pin_ids):
            if len(set(inventory)) != len(inventory) or len({row[1] for row in inventory}) != len(
                inventory
            ):
                raise ValueError("Repeated physical pin identities cannot be budgeted")
            owners, refs = {}, {}
            for owner, _, refdes, *_ in inventory:
                if (
                    owners.setdefault(owner, refdes) != refdes
                    or refs.setdefault(refdes, owner) != owner
                ):
                    raise ValueError("Component UID/refdes ownership is ambiguous")
        if sorted(material_ids) != sorted(pin_ids):
            raise ValueError(
                "Every physical pin, including foreign/Net0, needs exact owned material"
            )
        net_rows = state["geometry"]["model"]["connectivity"]["nets"]
        net_ids = named_net_ownership(net_rows, materials)
        if (
            target["net"] == UNASSIGNED_NET_NAME
            or net_ids.get(target["net"]) != target["net_uid"]
            or type(target["layer"]) is not int
            or not 1 <= target["layer"] <= 128
        ):
            raise ValueError("Complete unique actual net ownership and selected layer are required")
        trace_ids = [row["uid"] for row in traces]
        route_ids = [row["uid"] for row in routes]
        if (
            len(set(trace_ids)) != len(trace_ids)
            or len(set(route_ids)) != len(route_ids)
            or sorted(trace_ids) != sorted(route_ids)
            or any(row["kind"] != "trace" for row in routes)
            or any(not isinstance(uid, str) or not uid or uid == "0" for uid in trace_ids)
            or any(net_ids.get(row["net"]) != row["net_uid"] for row in traces)
        ):
            raise ValueError("Complete unique old trace inventory is required; vias unsupported")
        trace_by_uid = {row["uid"]: row for row in traces}
        for row in routes:
            trace = trace_by_uid[row["uid"]]
            raw = trace["points_array_mm"]
            shape = {
                "uid": row["uid"],
                "kind": "trace",
                "net": trace["net"],
                "layer": trace["layer"],
                "width": trace["width_mm"],
                "path": [list(point) for point in zip(*raw, strict=True)],
            }
            if row["net"] != trace["net"] or _canonical(row["geometry"]) != _canonical(shape):
                raise ValueError("Every old copper UID must bind its complete raw geometry")

        contours = raw_points = material_masks = material_points = 0
        target_regions = target_region_masks = target_region_points = 0
        target_pins = foreign_pads = cutouts = 0
        holes = set()

        def contour_points(record):
            if record["kind"] == "circle":
                return FACTORY_POINT_RESERVATION
            raw = record["points_array_mm"]
            if len(raw) != 3 or len({len(axis) for axis in raw}) != 1 or not raw[0]:
                raise ValueError("Complete raw contour arrays are required")
            return len(raw[0])

        for pin in materials:
            value = pin["material"]
            if value.get("complete") is not True or value.get("unread") != []:
                raise ValueError("Incomplete owned pad material cannot be budgeted")
            pads, local_holes = value["pads"], value["holes"]
            if (
                value["layer"] != target["layer"]
                or not isinstance(pads, list)
                or not 1 <= len(pads) <= 32
                or not isinstance(local_holes, list)
                or len(local_holes) > 32
            ):
                raise ValueError("All owned material must bind the selected layer")
            if any(
                not isinstance(pad["geometry"], list) or not 1 <= len(pad["geometry"]) <= 32
                for pad in pads
            ):
                raise ValueError("Per-pad shape inventory exceeds the bounded observation")
            shapes = [shape for pad in pads for shape in pad["geometry"]]
            if any(
                not isinstance(shape["cutouts"], list) or len(shape["cutouts"]) > 32
                for shape in shapes
            ):
                raise ValueError("Per-shape void inventory exceeds the bounded observation")
            local_cuts = sum(len(shape["cutouts"]) for shape in shapes)
            local_points = sum(
                contour_points(record)
                for shape in shapes
                for record in [shape["outer"], *shape["cutouts"]]
            )
            if not shapes:
                raise ValueError("Empty owned material cannot be budgeted")
            if len(shapes) + local_cuts > 128 or local_points > 4096:
                raise ValueError(
                    "Per-pin contour/point reservation exceeds the supported observation"
                )
            contours += len(shapes) + local_cuts
            raw_points += local_points
            cutouts += local_cuts
            material_masks += (
                3 * len(shapes) - 2 + 2 * local_cuts + len(local_holes) * (1 + len(pads))
            )
            material_points += local_points + FACTORY_POINT_RESERVATION * len(local_holes)
            for hole in local_holes:
                holes.add((*hole["center_mm"], hole["diameter_mm"]))
            if pin["net_uid"] == target["net_uid"]:
                target_pins += 1
                target_regions += len(shapes)
                target_region_masks += len(shapes) + 2 * (
                    local_cuts + len(local_holes) * len(shapes)
                )
                target_region_points += local_points + FACTORY_POINT_RESERVATION * len(
                    local_holes
                ) * len(shapes)
                # Bounded rounded witnesses add six factory contours and seven booleans.
                for shape in shapes:
                    if shape["outer"].get("is_arced") is True:
                        target_region_masks += 13
                        target_region_points += 6 * FACTORY_POINT_RESERVATION
            else:
                foreign_pads += len(pads)
        layer_traces = [row for row in traces if row["layer"] == target["layer"]]
        target_traces = [row for row in traces if row["net_uid"] == target["net_uid"]]
        if any(row["layer"] != target["layer"] for row in target_traces):
            raise ValueError("All selected-net copper must belong to the one selected layer")
        foreign_traces = len(layer_traces) - len(target_traces)
        next_target = len(target_traces) + added_trace
        next_layer = len(layer_traces) + added_trace
        trace_points = sum(len(row["points_array_mm"][0]) for row in traces)
        next_trace_points = trace_points + added_trace * LIMITS["candidate_path_points"]
        pair_count = next_target * (foreign_pads + foreign_traces + target_pins)
        pair_count += next_target * (next_target - 1) // 2 + next_layer * len(holes)
        region_count = target_regions * (target_regions - 1) // 2 + target_regions * next_target
        candidate_pairs = target_regions + len(target_traces) + foreign_pads + foreign_traces
        candidate_pairs += 2 * (len(holes) + cutouts) + 1
        candidate_pairs += target_regions * (target_regions - 1) // 2
        observation_masks = (
            CONTROL_MASK_RESERVATION
            + material_masks
            + len(holes)
            + next_layer * (1 + 2 * len(holes))
        )
        observation_masks += pair_count + target_region_masks + region_count
        candidate_masks = CONTROL_MASK_RESERVATION + material_masks + target_region_masks
        candidate_masks += len(holes) * 2 + cutouts + len(layer_traces) * (1 + len(holes))
        candidate_masks += candidate_pairs + 32
        observation_points = (
            CONTROL_POINT_RESERVATION + material_points + FACTORY_POINT_RESERVATION * len(holes)
        )
        observation_points += next_trace_points + target_region_points
        candidate_points = CONTROL_POINT_RESERVATION + material_points + target_region_points
        # Candidate + outline each allow128 records; envelope5; a rounded board
        # witness additionally reserves six128-record native factory contours.
        candidate_points += trace_points + FACTORY_POINT_RESERVATION * (len(holes) + cutouts)
        candidate_points += 2 * LIMITS["candidate_path_points"] + 5 + 6 * FACTORY_POINT_RESERVATION
        counts = {
            "pins": len(materials),
            "traces": len(traces) + added_trace,
            "target_net_pins": target_pins,
            "contours": contours,
            "raw_points": raw_points + next_trace_points,
            "mask_points": max(observation_points, candidate_points),
            "comparisons": max(pair_count, region_count, candidate_pairs),
            "temporary_masks": max(observation_masks, candidate_masks),
            "candidate_path_points": LIMITS["candidate_path_points"] if added_trace else 0,
            "round_holes": len(holes),
            "foreign_pad_objects": foreign_pads,
            "foreign_trace_objects": foreign_traces,
            "existing_trace_objects": len(traces),
            "target_regions": target_regions,
            "observation_comparisons": pair_count,
            "region_comparisons": region_count,
            "candidate_comparisons": candidate_pairs,
            "observation_mask_points_reservation": observation_points,
            "candidate_mask_points_reservation": candidate_points,
        }
        result["counts"] = counts
        result["complete"] = True
        for name, limit in LIMITS.items():
            if counts[name] > limit:
                result["errors"].append(
                    {"resource": name, "observed_or_reserved": counts[name], "maximum": limit}
                )
        result["valid"] = not result["errors"]
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as error:
        result["errors"].append({"cause": type(error).__name__, "reason": str(error)})
    return result
