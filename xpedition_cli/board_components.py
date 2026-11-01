"""Pure contracts for one observed net's conductive material components.

These functions consume captured data only. They neither call the CAD adapter
nor authorize a write. Component and region IDs describe one exact snapshot;
they are not persistent database identifiers. Effective path rules, dispatch,
DRC and persistence belong to the caller's separate contracts.
"""

from __future__ import annotations

import copy
import math
from collections import Counter

from .cell_refresh import _canonical, digest

CONTRACT = "single-net-component-connection-v1"
MAX_OBJECTS = 1024
MAX_NODES = 4096
MAX_PATH_POINTS = 128
MAX_RAW_POINTS = 32768


def _uid(value):
    if not isinstance(value, str) or not value.strip() or value == "0":
        raise ValueError("An actual nonzero native identity is required")
    return value


def _number(value, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("A finite explicit-mm number is required")
    if positive and value <= 0:
        raise ValueError("An explicit-mm width must be positive")
    return value


def _board_id(value):
    if not isinstance(value, (str, dict)) or not value:
        raise ValueError("The caller must bind the actual native board identity")
    if isinstance(value, str):
        _uid(value)
    # digest also rejects data that cannot be retained as JSON observations.
    digest(value)
    return copy.deepcopy(value)


def _nets(state):
    connectivity = state["geometry"]["model"]["connectivity"]
    rows = connectivity["nets"]
    if connectivity.get("complete") is not True or not isinstance(rows, list):
        raise ValueError("The complete native net inventory is required")
    if len(rows) > MAX_OBJECTS:
        raise ValueError("Native net inventory exceeds the observation budget")
    result, uids = {}, set()
    for row in rows:
        name, uid = row["net"], _uid(row["uid"])
        if (
            not isinstance(name, str)
            or not name.strip()
            or name in result
            or uid in uids
            or type(row["opens"]) is not int
            or row["opens"] < 0
            or type(row["routed"]) is not bool
        ):
            raise ValueError("Native net identity or connectivity is incomplete or duplicated")
        result[name], uids = copy.deepcopy(row), uids | {uid}
    return result


def _routing(state, nets):
    rows, raw = state["routing_objects"], state["native_trace_material"]
    if not isinstance(rows, list) or not isinstance(raw, list):
        raise ValueError("Complete native and raw routing inventories are required")
    if len(rows) > MAX_OBJECTS or len(raw) > MAX_OBJECTS:
        raise ValueError("Routing inventory exceeds the observation budget")
    routing = {}
    for row in rows:
        key = row["kind"], _uid(row["uid"])
        if key in routing or row["kind"] not in ("trace", "via") or row["net"] not in nets:
            raise ValueError("Routing identities or owning nets are unavailable or duplicated")
        routing[key] = copy.deepcopy(row)
    traces, point_count = {}, 0
    for row in raw:
        uid, points = _uid(row["uid"]), row["points_array_mm"]
        if (
            uid in traces
            or row["net"] not in nets
            or row["net_uid"] != nets[row["net"]]["uid"]
            or type(row["layer"]) is not int
            or row["layer"] < 1
            or not isinstance(points, (list, tuple))
            or len(points) != 3
            or any(not isinstance(axis, (list, tuple)) for axis in points)
            or len({len(axis) for axis in points}) != 1
            or not 2 <= len(points[0]) <= MAX_RAW_POINTS
        ):
            raise ValueError("Raw trace identity, units, path or ownership is incomplete")
        _number(row["width_mm"], positive=True)
        for axis in points:
            for value in axis:
                _number(value)
        point_count += len(points[0])
        if point_count > MAX_RAW_POINTS:
            raise ValueError("Raw trace point inventory exceeds the observation budget")
        shape = {
            "uid": uid,
            "kind": "trace",
            "net": row["net"],
            "layer": row["layer"],
            "width": row["width_mm"],
            "path": [list(point) for point in zip(*points, strict=True)],
        }
        observed = routing.get(("trace", uid))
        if (
            observed is None
            or observed["net"] != row["net"]
            or _canonical(observed["geometry"]) != _canonical(shape)
        ):
            raise ValueError("Raw trace and captured native geometry disagree")
        traces[uid] = copy.deepcopy(row)
    if set(traces) != {uid for kind, uid in routing if kind == "trace"}:
        raise ValueError("Raw trace inventory does not cover every actual native trace UID")
    model = state["geometry"]["model"]
    for kind in ("trace", "via"):
        if Counter(digest(row["geometry"]) for key, row in routing.items() if key[0] == kind) != (
            Counter(digest(row) for row in model[kind + "s"])
        ):
            raise ValueError("Model and native routing inventories disagree")
    return routing, traces


def _protection(state, net):
    """Only new traces and the target's native completion counters may change."""
    result = copy.deepcopy(state)
    result.pop("native_mask_proof", None)
    result.pop("native_trace_material")
    result["routing_objects"] = sorted(
        (row for row in result["routing_objects"] if row["kind"] != "trace"),
        key=lambda row: (row["kind"], row["uid"]),
    )
    model = result["geometry"]["model"]
    model.pop("traces")
    for row in model["connectivity"]["nets"]:
        if row["net"] == net:
            row.pop("opens")
            row.pop("routed")
    model["connectivity"]["nets"].sort(key=lambda row: row["uid"])
    model["unrouted"] = [row for row in model["unrouted"] if row["net"] != net]
    return digest(result)


def _node_material(node, traces, pins):
    if node["kind"] == "trace":
        raw = traces[node["trace_uid"]]
        return {"node": node, "raw_trace": raw}
    if node["kind"] != "pin" or type(node["region_index"]) is not int:
        raise ValueError("Unsupported or incomplete physical material node")
    pin = pins[(node["component_uid"], node["pin_uid"])]
    shapes = [shape for pad in pin["material"]["pads"] for shape in pad["geometry"]]
    index = node["region_index"]
    if not 0 <= index < len(shapes):
        raise ValueError("A pad region index is outside its actual owned material")
    return {"node": node, "owned_pin_material": pin, "region_shape": shapes[index]}


def _partition(nodes, edges):
    adjacency = {node: set() for node in nodes}
    for edge in edges:
        source, target = edge["source"], edge["target"]
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
            adjacency[target].add(source)
    groups, unseen = set(), set(nodes)
    while unseen:
        pending, group = [min(unseen)], set()
        while pending:
            node = pending.pop()
            if node not in group:
                group.add(node)
                pending.extend(adjacency[node] - group)
        unseen -= group
        groups.add(frozenset(group))
    return groups


def _failure(result, code, reason):
    result["blockers"].append({"code": code, "message": str(reason)})
    result["reason"] = str(reason)
    return result


def validate_signal_layer(native_state, layer):
    """Resolve one actual signal layer from a complete, uniquely numbered stackup."""
    count = native_state["geometry"]["model"]["layers"]
    stackup = native_state["geometry"]["stackup"]
    if (
        type(count) is not int
        or not 1 <= count <= 128
        or type(layer) is not int
        or not 1 <= layer <= count
        or not isinstance(stackup, list)
        or any(not isinstance(row, dict) for row in stackup)
    ):
        raise ValueError("The complete actual signal-conductor stackup is required")
    conductors = [row for row in stackup if row.get("kind") == "conductor"]
    if (
        len(conductors) != count
        or any(type(row.get("layer")) is not int for row in conductors)
        or {row["layer"] for row in conductors} != set(range(1, count + 1))
    ):
        raise ValueError("Actual conductor layer numbers must be complete and unique")
    selected = [row for row in conductors if row["layer"] == layer]
    if len(selected) != 1 or selected[0].get("usage") != "signal":
        raise ValueError("The requested layer is not an observed signal conductor")
    return selected[0]


def build_snapshot(native_state, material_observation, *, board_id, net_id, layer):
    """Bind complete raw material, a physical partition and actual board/net/layer.

    ``material_observation`` is M.observe_components' output. Recomputing that
    pure graph from the captured matrices rejects substituted partition data.
    ``board_id`` must be the caller's freshly read native document identity.
    """
    from . import native_pad_mask

    result = {
        "contract": CONTRACT,
        "valid": False,
        "observation_complete": False,
        "snapshot_id": None,
        "id_scope": "exact_snapshot_only; region indices are not persistent identities",
        "nodes": [],
        "components": [],
        "blockers": [],
        "not_checked": ["effective path rules", "DRC", "persistence", "manufacturing"],
    }
    try:
        board_id, net_id = _board_id(board_id), _uid(net_id)
        if "board_id" in native_state and native_state["board_id"] != board_id:
            raise ValueError("The observed board identity differs from the requested board")
        nets = _nets(native_state)
        selected = [row for row in nets.values() if row["uid"] == net_id]
        if len(selected) != 1:
            raise ValueError("The actual native target net UID is absent")
        target = selected[0]
        validate_signal_layer(native_state, layer)
        routing, traces = _routing(native_state, nets)
        planes = native_state["plane_inventory"]
        if (
            planes.get("complete") is not True
            or any(planes[key] for key in ("shapes", "generated", "assignments"))
            or native_state["geometry"]["model"]["planes"]
            or any(kind == "via" for kind, _ in routing)
        ):
            raise ValueError("Via/plane material is outside the captured single-layer boundary")
        request = {"net": target["net"], "net_uid": net_id, "layer": layer}
        expected = native_pad_mask.observe_components(native_state, request)
        if expected.get("observation_valid") is not True or expected.get("complete") is not True:
            raise ValueError(
                "Complete native physical components are unavailable: "
                + str(expected.get("reason"))
            )
        if material_observation != expected:
            raise ValueError(
                "Supplied material partition differs from the captured complete matrices"
            )
        nodes = expected["nodes"]
        if len(nodes) > MAX_NODES or len({row["node_id"] for row in nodes}) != len(nodes):
            raise ValueError("Material node inventory is duplicated or exceeds its budget")
        materials = native_state["all_pin_pad_material"]
        pins = {(row["component_uid"], row["pin_uid"]): row for row in materials}
        identities = native_state["pin_identities"]
        identity_fields = ("component_uid", "pin_uid", "net", "net_uid", "refdes", "pin")
        if (
            len(pins) != len(materials)
            or len(identities) != len(materials)
            or Counter(tuple(row[key] for key in identity_fields) for row in identities)
            != Counter(tuple(row[key] for key in identity_fields) for row in materials)
        ):
            raise ValueError("Complete native pin identities do not bind all owned material")
        for row in nodes:
            if row["net_uid"] != net_id or row["layer"] != layer:
                raise ValueError("A material node has another net or layer")
            result["nodes"].append(
                {
                    **copy.deepcopy(row),
                    "physical_material_sha256": digest(_node_material(row, traces, pins)),
                }
            )
        result["nodes"].sort(key=lambda row: row["node_id"])
        node_ids = {row["node_id"] for row in nodes}
        groups = [sorted(row["node_ids"]) for row in expected["components"]]
        flattened = [node for group in groups for node in group]
        if len(flattened) != len(set(flattened)) or set(flattened) != node_ids:
            raise ValueError("Physical components are not a complete unique node partition")
        result.update(
            board_id=board_id,
            target={"net": target["net"], "net_uid": net_id, "layer": layer},
            native_connectivity=copy.deepcopy(target),
            raw_traces=sorted(traces.values(), key=lambda row: row["uid"]),
            routing_objects=sorted(routing.values(), key=lambda row: (row["kind"], row["uid"])),
            edges=copy.deepcopy(expected["edges"]),
            protection_sha256=_protection(native_state, target["net"]),
            source_state_sha256=digest(native_state),
            rule_observation_sha256=digest(native_state.get("rules")),
            all_members_connected=expected["all_members_connected"],
        )
        signatures = {row["node_id"]: row["physical_material_sha256"] for row in result["nodes"]}
        result["snapshot_id"] = "snapshot:" + digest(
            {
                "contract": CONTRACT,
                "board_id": board_id,
                "target": result["target"],
                "nodes": sorted(signatures.items()),
                "partition": sorted(groups),
            }
        )
        for group in sorted(groups):
            basis = {
                "snapshot_id": result["snapshot_id"],
                "nodes": [(node, signatures[node]) for node in group],
            }
            result["components"].append(
                {"component_id": "component:" + digest(basis), "node_ids": group}
            )
        result["valid"] = result["observation_complete"] = True
        result["snapshot_data_sha256"] = digest(result)
        return result
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as error:
        return _failure(result, "E_COMPONENT_OBSERVATION_INCOMPLETE", error)


def _snapshot(snapshot):
    expected = digest(
        {key: value for key, value in snapshot.items() if key != "snapshot_data_sha256"}
    )
    if (
        snapshot.get("contract") != CONTRACT
        or snapshot.get("valid") is not True
        or snapshot.get("observation_complete") is not True
        or snapshot.get("snapshot_data_sha256") != expected
    ):
        raise ValueError("The component snapshot is incomplete or its bound data changed")


def _select(snapshot, selector):
    if isinstance(selector, str):
        selector = {"component_id" if selector.startswith("component:") else "node_id": selector}
    if (
        not isinstance(selector, dict)
        or not selector
        or set(selector) - {"component_id", "node_id"}
    ):
        raise ValueError("Select one snapshot component_id or node_id")
    if not any(selector.get(key) for key in ("component_id", "node_id")):
        raise ValueError("A component or node identity cannot be empty")
    for key in ("component_id", "node_id"):
        if selector.get(key) is not None:
            _uid(selector[key])
    matches = [
        row
        for row in snapshot["components"]
        if ("component_id" not in selector or row["component_id"] == selector["component_id"])
        and (selector.get("node_id") is None or selector["node_id"] in row["node_ids"])
    ]
    if len(matches) != 1:
        raise ValueError("The selected endpoint is absent from this exact snapshot")
    return {"component_id": matches[0]["component_id"], "node_id": selector.get("node_id")}


def _orientation(a, b, c):
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if not math.isfinite(cross):
        raise ValueError("Path arithmetic is outside the finite numeric geometry boundary")
    return (cross > 0) - (cross < 0)


def _on_segment(a, b, p):
    return _orientation(a, b, p) == 0 and all(
        min(a[i], b[i]) <= p[i] <= max(a[i], b[i]) for i in (0, 1)
    )


def _intersects(a, b, c, d):
    o1, o2, o3, o4 = (
        _orientation(a, b, c),
        _orientation(a, b, d),
        _orientation(c, d, a),
        _orientation(c, d, b),
    )
    return (o1 * o2 < 0 and o3 * o4 < 0) or any(
        (o == 0 and _on_segment(x, y, p))
        for o, x, y, p in ((o1, a, b, c), (o2, a, b, d), (o3, c, d, a), (o4, c, d, b))
    )


def _path(points, width_mm):
    _number(width_mm, positive=True)
    if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= MAX_PATH_POINTS:
        raise ValueError("An explicit simple path of 2..128 points is required")
    result = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Each explicit path point must contain x and y in mm")
        result.append([_number(coordinate) for coordinate in point])
    if len({tuple(point) for point in result}) != len(result):
        raise ValueError("A simple path cannot repeat a vertex or close on itself")
    segments = list(zip(result, result[1:], strict=False))
    for index, (a, b) in enumerate(segments):
        for other, (c, d) in enumerate(segments[index + 1 :], index + 1):
            if other == index + 1:
                if _on_segment(a, b, d) or _on_segment(c, d, a):
                    raise ValueError("Adjacent path segments cannot overlap or reverse")
            elif _intersects(a, b, c, d):
                raise ValueError("A simple path cannot intersect or touch itself")
    return result


def plan_connection(snapshot, *, first, second, points, width_mm):
    """Freeze two component/node choices and an explicit simple proposed path.

    Structural validity is independent of rule applicability. This result never
    declares can_apply, accepts a client rule override, or synthesizes evidence.
    """
    result = {"contract": CONTRACT, "structural_valid": False, "no_op": False, "blockers": []}
    try:
        _snapshot(snapshot)
        first, second = _select(snapshot, first), _select(snapshot, second)
        result.update(
            snapshot_id=snapshot["snapshot_id"],
            board_id=copy.deepcopy(snapshot["board_id"]),
            target=copy.deepcopy(snapshot["target"]),
            first=first,
            second=second,
        )
        if first["component_id"] == second["component_id"]:
            result.update(
                structural_valid=True, no_op=True, status="no_op", points=[], width_mm=None
            )
        else:
            result.update(
                structural_valid=True,
                status="requires_independent_write_contract",
                points=_path(points, width_mm),
                width_mm=width_mm,
            )
            result["blockers"].append(
                {
                    "code": "E_EFFECTIVE_PATH_RULES_NOT_ASSESSED",
                    "message": (
                        "The component contract does not establish effective width/clearance "
                        "rules over the proposed conductor and its influence area"
                    ),
                }
            )
            result["required_evidence"] = [
                "native endpoint material contact",
                "effective path rules",
                "bounded native dispatch",
                "post-write physical partition and protection",
                "DRC and persistence",
            ]
        result["plan_id"] = "connection:" + digest(result)
        return result
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as error:
        return _failure(result, "E_COMPONENT_REQUEST_INVALID", error)


def verify_connection(
    before_snapshot,
    after_native_state,
    after_material_observation,
    *,
    plan,
    board_id,
    reopen_comparison=None,
):
    """Accept only the selected old partition merge and same-layer new traces.

    A valid result is a physical/protection comparison, not rule, DRC, save or
    manufacturing approval. Whole-net completion is reported independently.
    """
    result = {
        "contract": CONTRACT,
        "valid": False,
        "structural_valid": False,
        "blockers": [],
        "checks": [],
        "scope": "physical_partition_and_protection_only",
    }

    def check(name, passed):
        result["checks"].append({"name": name, "passed": bool(passed)})
        if not passed:
            raise ValueError(name)

    try:
        _snapshot(before_snapshot)
        rebuilt = plan_connection(
            before_snapshot,
            first=plan["first"],
            second=plan["second"],
            points=plan["points"],
            width_mm=plan["width_mm"],
        )
        check(
            "connection proposal exactly bound to its original snapshot",
            rebuilt == plan and plan["structural_valid"] is True,
        )
        after = build_snapshot(
            after_native_state,
            after_material_observation,
            board_id=board_id,
            net_id=before_snapshot["target"]["net_uid"],
            layer=before_snapshot["target"]["layer"],
        )
        check("fresh complete component observation", after["valid"] is True)
        check(
            "same actual board/net/layer identities",
            after["board_id"] == before_snapshot["board_id"]
            and after["target"] == before_snapshot["target"],
        )
        protection_sha256 = after["protection_sha256"]
        if reopen_comparison is not None:
            from . import component_net0_reopen, native_pad_mask

            check("no-op cannot claim a reopened default-reference change", not plan["no_op"])
            reference = reopen_comparison["reference_state"]
            request = {
                "net": before_snapshot["target"]["net"],
                "net_uid": before_snapshot["target"]["net_uid"],
                "layer": before_snapshot["target"]["layer"],
            }
            reference_proof = verify_connection(
                before_snapshot,
                reference,
                native_pad_mask.observe_components(reference, request),
                plan=plan,
                board_id=board_id,
            )
            check(
                "accepted raw after-edit reference still verifies without an alias",
                reference_proof["valid"] is True,
            )
            # Both actual raw frames have already passed their unmodified mask
            # validation. This view is used only for the protection comparison.
            projection = component_net0_reopen.protection_projection(
                after_native_state, request, reopen_comparison
            )
            protection_sha256 = _protection(projection, before_snapshot["target"]["net"])
        check(
            "all non-target and non-new material, rules and geometry protected",
            protection_sha256 == before_snapshot["protection_sha256"],
        )
        old_raw = {row["uid"]: row for row in before_snapshot["raw_traces"]}
        new_raw = {row["uid"]: row for row in after["raw_traces"]}
        old_routing = {(row["kind"], row["uid"]): row for row in before_snapshot["routing_objects"]}
        new_routing = {(row["kind"], row["uid"]): row for row in after["routing_objects"]}
        check(
            "all old raw trace UIDs, widths, paths, layers and owning nets unchanged",
            all(new_raw.get(uid) == row for uid, row in old_raw.items()),
        )
        check(
            "all old native routing records unchanged",
            all(new_routing.get(key) == row for key, row in old_routing.items()),
        )
        created = [row for uid, row in new_raw.items() if uid not in old_raw]
        added_routing = set(new_routing) - set(old_routing)
        target = before_snapshot["target"]
        check(
            "only actual same-net same-layer trace objects added and zero via",
            added_routing == {("trace", row["uid"]) for row in created}
            and all(
                row["net"] == target["net"]
                and row["net_uid"] == target["net_uid"]
                and row["layer"] == target["layer"]
                for row in created
            ),
        )
        old_nodes = {row["node_id"]: row for row in before_snapshot["nodes"]}
        new_nodes = {row["node_id"]: row for row in after["nodes"]}
        check(
            "every old physical node and its unrounded material signature remains",
            all(new_nodes.get(node) == row for node, row in old_nodes.items()),
        )
        added_nodes = set(new_nodes) - set(old_nodes)
        check(
            "every new material node binds one actual new trace",
            added_nodes == {"trace:" + row["uid"] for row in created},
        )
        old_groups = {
            row["component_id"]: set(row["node_ids"]) for row in before_snapshot["components"]
        }
        check(
            "the induced physical partition of old nodes remains unchanged",
            _partition(old_nodes, after["edges"])
            == {frozenset(group) for group in old_groups.values()},
        )
        after_groups = [set(row["node_ids"]) for row in after["components"]]
        selected = {plan["first"]["component_id"], plan["second"]["component_id"]}
        if plan["no_op"]:
            check(
                "same-component request remains a no-op with no new copper",
                not created and after["snapshot_id"] == before_snapshot["snapshot_id"],
            )
        else:
            check("connection created at least one actual trace", bool(created))
            merged_old = set().union(*(old_groups[component] for component in selected))
            expected_groups = [
                group for component, group in old_groups.items() if component not in selected
            ] + [merged_old | added_nodes]
            check(
                "only the selected two old components merge and every new copper joins them",
                {frozenset(group) for group in after_groups}
                == {frozenset(group) for group in expected_groups},
            )
            for choice in (plan["first"], plan["second"]):
                node = choice["node_id"]
                if node is not None:
                    check(
                        "selected exact material node directly contacts new copper: " + node,
                        any(
                            (edge["source"] == node and edge["target"] in added_nodes)
                            or (edge["target"] == node and edge["source"] in added_nodes)
                            for edge in after["edges"]
                        ),
                    )
        requested_rows = [
            [point[0] for point in plan["points"]],
            [point[1] for point in plan["points"]],
            [0.0] * len(plan["points"]),
        ]
        requested_geometry_matches = plan["no_op"] or (
            len(created) == 1
            and created[0]["width_mm"] == plan["width_mm"]
            and created[0]["points_array_mm"] == requested_rows
        )
        before_opens = before_snapshot["native_connectivity"]["opens"]
        after_opens = after["native_connectivity"]["opens"]
        result.update(
            valid=True,
            structural_valid=True,
            no_op=plan["no_op"],
            before_snapshot_id=before_snapshot["snapshot_id"],
            after_snapshot_id=after["snapshot_id"],
            created_trace_uids=sorted(row["uid"] for row in created),
            components_before=len(old_groups),
            components_after=len(after_groups),
            all_members_connected=after["all_members_connected"],
            native_connectivity_before=before_snapshot["native_connectivity"],
            native_connectivity_after=after["native_connectivity"],
            native_consistent=(
                after_opens == before_opens if plan["no_op"] else after_opens == before_opens - 1
            ),
            native_consistency_basis=(
                "opens delta only; native-to-material baseline accounting remains independent"
            ),
            requested_geometry_matches=requested_geometry_matches,
            drc_executed=False,
            persistence_verified=False,
        )
        if reopen_comparison is not None:
            result["reopen_reference_identity"] = copy.deepcopy(
                {key: value for key, value in reopen_comparison.items() if key != "reference_state"}
            )
        return result
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as error:
        return _failure(result, "E_COMPONENT_DELTA_REJECTED", error)
