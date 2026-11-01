"""Bounded protection of the five observed Designer engineering collections.

This compares observations, not wire geometry, hidden CAD objects or hardware
correctness. No COM, filesystem or application entry point is used here. New
parts need a fresh observation before another operation can address their pins
or attributes. Every original record remains protected, including target fields
that the requested operation did not name.
"""

from __future__ import annotations

import copy
import math

from .errors import CLIError
from .native_snapshot_revision import canonical
from .schematic_attribute_display import (
    DISPLAY_NAMES,
    _native_border_datetime,
    canonical_name,
    freeze_save_metadata,
    same_save_metadata,
)

COLLECTIONS = ("components", "unnamed_components", "nets", "connections", "sheets")
LIMITS = {
    "operations": 64,
    "components": 4096,
    "pins_per_component": 512,
    "attributes": 256,
    "connect_intermediate_points": 64,
    "sheets": 512,
    "collection_items": 16384,
    "json_values": 200000,
    "string_bytes": 8 * 1024**2,
}
NOT_CHECKED = [
    "wire_segments_routes_labels_and_other_free_graphics",
    "unavailable_native_getter_values_and_unexposed_objects",
    "library_symbol_source_identity_not_exposed_by_snapshot",
    "erc_rules_hardware_correctness_and_manufacturing",
    "independent_save_close_reopen_persistence",
]
_FIELDS = {
    "place_component": {
        "type",
        "refdes",
        "part_number",
        "library",
        "device_name",
        "symbol_name",
        "x",
        "y",
        "sheet",
    },
    "move_component": {"type", "refdes", "x", "y", "sheet"},
    "set_property": {"type", "refdes", "name", "value", "sheet"},
    "create_net": {"type", "name", "sheet"},
    "connect": {"type", "net", "pins", "sheet", "points", "x", "y"},
}


class _Invalid(ValueError):
    def __init__(self, reason, collection=None):
        self.reason, self.collection = reason, collection


def _need(condition, reason, collection=None):
    if not condition:
        raise _Invalid(reason, collection)


def _json(value, *, depth=0, budget=None):
    """Reject non-JSON objects, cycles/depth, nonfinite values and oversized input."""
    budget = [0, 0] if budget is None else budget
    budget[0] += 1
    _need(depth <= 24 and budget[0] <= LIMITS["json_values"], "input_resource_limit")
    if type(value) is dict:
        _need(len(value) <= LIMITS["collection_items"], "input_resource_limit")
        for key, item in value.items():
            _need(type(key) is str and len(key) <= 256, "json_string_keys_required")
            budget[1] += len(key.encode("utf-8"))
            _json(item, depth=depth + 1, budget=budget)
    elif type(value) is list:
        _need(len(value) <= LIMITS["collection_items"], "input_resource_limit")
        for item in value:
            _json(item, depth=depth + 1, budget=budget)
    elif type(value) is str:
        _need(len(value) <= 8192, "input_resource_limit")
        budget[1] += len(value.encode("utf-8"))
    elif type(value) is int:
        _need(-(2**63) <= value < 2**63, "json_integer_range")
    elif type(value) is float:
        _need(math.isfinite(value), "finite_json_number_required")
    else:
        _need(value is None or type(value) is bool, "plain_json_required")
    _need(budget[1] <= LIMITS["string_bytes"], "input_resource_limit")


def _text(value):
    return type(value) is str and bool(value.strip()) and value == value.strip()


def _long(value):
    return type(value) is int and -(2**31) <= value < 2**31


def _sheet(value):
    if type(value) is int and value > 0:
        return str(value)
    _need(type(value) is str and value.isascii() and value.isdecimal(), "sheet_identity_required")
    _need(value == str(int(value)) and int(value) > 0, "sheet_identity_required")
    return value


def _snapshot(value):
    _json(value)
    _need(type(value) is dict, "snapshot_object_required")
    _need(
        value.get("revision") == "native"
        and type(value.get("metadata")) is dict
        and value["metadata"].get("backend") == "native_xpedition"
        and value["metadata"].get("source") == "Viewdraw.Application",
        "native_designer_observation_required",
    )
    _need(all(type(value.get(k)) is list for k in COLLECTIONS), "five_collections_required")
    _need(_text(value.get("project")), "native_project_identity_required")
    result = copy.deepcopy(value)
    _need(
        len(result["components"]) + len(result["unnamed_components"]) <= LIMITS["components"],
        "component_resource_limit",
    )
    sheets = {}
    for row in result["sheets"]:
        _need(
            type(row) is dict and set(row) == {"number", "name", "parts"},
            "complete_sheet_record_required",
            "sheets",
        )
        row["number"] = number = _sheet(row["number"])
        _need(
            number not in sheets
            and _text(row["name"])
            and type(row["parts"]) is int
            and row["parts"] >= 0,
            "unique_sheet_identity_required",
            "sheets",
        )
        sheets[number] = row
    _need(1 <= len(sheets) <= LIMITS["sheets"], "sheet_census_required", "sheets")
    refs, uids, counts, pinmap = {}, set(), {key: 0 for key in sheets}, {}
    display_names = {canonical_name(name) for name in DISPLAY_NAMES}
    for collection in ("components", "unnamed_components"):
        for row in result[collection]:
            _need(type(row) is dict, "component_record_required", collection)
            _need(
                {
                    "refdes",
                    "uid",
                    "name",
                    "internal_part_no",
                    "value",
                    "description",
                    "package",
                    "manufacturer",
                    "mpn",
                    "attributes",
                    "attribute_display",
                    "attribute_display_coverage",
                    "pin_observation_coverage",
                    "identity_observation_coverage",
                    "sheet",
                    "x",
                    "y",
                    "rotation",
                    "pins",
                }
                <= set(row),
                "complete_native_component_record_required",
                collection,
            )
            _need(
                all(
                    type(row[k]) is str
                    for k in (
                        "name",
                        "internal_part_no",
                        "value",
                        "description",
                        "package",
                        "manufacturer",
                        "mpn",
                    )
                ),
                "native_component_summary_fields_required",
                collection,
            )
            uid, ref = row.get("uid"), row.get("refdes")
            _need(
                _text(uid)
                and uid.casefold() not in {"0", "unknown", "none", "null"}
                and uid not in uids,
                "unique_component_uid_required",
                collection,
            )
            _need(
                type(ref) is str
                and (
                    ref == ""
                    if collection == "unnamed_components"
                    else _text(ref) and ref.casefold() not in refs
                ),
                "unique_component_reference_required",
                collection,
            )
            uids.add(uid)
            if ref:
                refs[ref.casefold()] = row
            sheet = _sheet(row.get("sheet"))
            _need(sheet in sheets, "component_sheet_missing", collection)
            if ref:
                counts[sheet] += 1
            _need(
                all(_long(row.get(k)) for k in ("x", "y")),
                "component_coordinates_unobserved",
                collection,
            )
            for key in ("identity_observation_coverage", "pin_observation_coverage"):
                coverage = row.get(key)
                _need(
                    type(coverage) is dict
                    and coverage.get("complete") is True
                    and coverage.get("errors") == [],
                    "complete_component_coverage_required",
                    collection,
                )
            attributes, displays = row.get("attributes"), row.get("attribute_display")
            _need(
                type(attributes) is dict
                and len(attributes) <= LIMITS["attributes"]
                and all(_text(k) and type(v) is str for k, v in attributes.items())
                and type(displays) is list
                and len(displays) <= LIMITS["attributes"],
                "complete_attribute_observations_required",
                collection,
            )
            coverage = row.get("attribute_display_coverage")
            _need(
                type(coverage) is dict
                and coverage.get("enumeration_complete") is True
                and coverage.get("errors") == []
                and coverage.get("truncated") is False
                and type(coverage.get("attribute_count")) is int
                and coverage["attribute_count"] == len(attributes)
                and type(coverage.get("attribute_limit")) is int
                and len(attributes) <= coverage["attribute_limit"] <= LIMITS["attributes"]
                and coverage.get("names") == list(DISPLAY_NAMES),
                "complete_attribute_coverage_required",
                collection,
            )
            display_index = {}
            for attribute in displays:
                _need(type(attribute) is dict, "attribute_record_required", collection)
                name = attribute.get("name")
                _need(
                    _text(name)
                    and name not in display_index
                    and name in attributes
                    and attribute.get("value") == attributes[name]
                    and attribute.get("getter_errors") == {}
                    and attribute.get("owner")
                    == {"refdes": ref, "uid": uid, "sheet": row["sheet"]},
                    "unique_complete_attribute_record_required",
                    collection,
                )
                geometry = attribute.get("display")
                _need(
                    type(geometry) is dict
                    and geometry.get("units") == "sheet_unit"
                    and all(
                        _long(geometry.get(k))
                        for k in ("x", "y", "visible", "size", "origin", "orientation")
                    )
                    and geometry["size"] > 0,
                    "attribute_geometry_unobserved",
                    collection,
                )
                display_index[name] = attribute
            _need(
                set(display_index) == {k for k in attributes if canonical_name(k) in display_names},
                "attribute_display_census_incomplete",
                collection,
            )
            row["attribute_display"] = sorted(displays, key=lambda a: a["name"])
            pins = row.get("pins")
            _need(
                type(pins) is list and len(pins) <= LIMITS["pins_per_component"],
                "complete_pin_census_required",
                collection,
            )
            numbers = set()
            for pin in pins:
                _need(
                    type(pin) is dict
                    and {"number", "net", "net_unnamed", "x", "y", "no_connect"} <= set(pin),
                    "complete_pin_record_required",
                    collection,
                )
                number, net = pin["number"], pin["net"]
                _need(
                    type(number) is str
                    and (bool(number) or (not ref and len(pins) == 1))
                    and number not in numbers,
                    "unique_pin_identity_required",
                    collection,
                )
                numbers.add(number)
                _need(
                    (net is None or _text(net))
                    and type(pin["net_unnamed"]) is bool
                    and pin["net_unnamed"] is bool(net and net.startswith("$"))
                    and type(pin["no_connect"]) is bool
                    and not (net is not None and pin["no_connect"])
                    and all(_long(pin.get(k)) for k in ("x", "y")),
                    "complete_pin_state_required",
                    collection,
                )
                if ref:
                    identity = f"{ref}.{number}"
                    _need(identity not in pinmap, "ambiguous_pin_identity", collection)
                    pinmap[identity] = (row, pin)
            row["pins"] = sorted(pins, key=lambda p: p["number"])
    _need(
        all(row["parts"] == counts[number] for number, row in sheets.items()),
        "sheet_component_census_mismatch",
        "sheets",
    )
    for key in ("unnamed_symbols",):
        for owner in (result, result["metadata"]):
            if key in owner:
                _need(
                    type(owner[key]) is int and owner[key] == len(result["unnamed_components"]),
                    "unnamed_component_census_mismatch",
                    "unnamed_components",
                )
    nets = {}
    for row in result["nets"]:
        _need(
            type(row) is dict
            and set(row) == {"name", "unnamed", "sheets"}
            and _text(row["name"])
            and row["name"] not in nets
            and type(row["unnamed"]) is bool
            and row["unnamed"] is row["name"].startswith("$")
            and type(row["sheets"]) is list,
            "unique_complete_net_record_required",
            "nets",
        )
        row["sheets"] = [_sheet(n) for n in row["sheets"]]
        _need(len(set(row["sheets"])) == len(row["sheets"]), "duplicate_net_sheet", "nets")
        nets[row["name"]] = row
    members, net_sheets = _members(pinmap)
    _need(set(nets) == set(members), "net_pin_census_mismatch", "nets")
    _need(
        all(set(nets[n]["sheets"]) == net_sheets[n] for n in nets),
        "net_sheet_census_mismatch",
        "nets",
    )
    connections = {}
    for row in result["connections"]:
        _need(
            type(row) is dict
            and set(row) == {"net", "pins"}
            and _text(row["net"])
            and row["net"] not in connections
            and type(row["pins"]) is list
            and all(_text(p) for p in row["pins"])
            and len(row["pins"]) == len(set(row["pins"])),
            "unique_complete_connection_required",
            "connections",
        )
        connections[row["net"]] = set(row["pins"])
    _need(
        connections == {n: pins for n, pins in members.items() if len(pins) >= 2},
        "connection_pin_census_mismatch",
        "connections",
    )
    # normalise_project adds this explicit empty, unobserved Layout projection.
    # Nonempty or unfamiliar values stay present and remain protected.
    if result.get("pcb") == {k: [] for k in ("components", "footprints", "nets", "tracks", "vias")}:
        result.pop("pcb")
    return canonical(result)


def _members(pinmap):
    members, sheets = {}, {}
    for identity, (part, pin) in pinmap.items():
        if pin["net"] is not None:
            members.setdefault(pin["net"], set()).add(identity)
            sheets.setdefault(pin["net"], set()).add(_sheet(part["sheet"]))
    return members, sheets


def _part_map(snapshot):
    return {row["refdes"]: row for row in snapshot["components"]}


def _pin_map(parts):
    return {f"{ref}.{p['number']}": (part, p) for ref, part in parts.items() for p in part["pins"]}


def _compile(before, operations, save_metadata):
    expected = _snapshot(before)
    _json(operations)
    _need(
        type(operations) is list and 1 <= len(operations) <= LIMITS["operations"],
        "bounded_nonempty_operations_required",
    )
    if save_metadata is not None:
        _json(save_metadata)
        _need(
            same_save_metadata(freeze_save_metadata(expected), save_metadata),
            "frozen_save_metadata_mismatch",
        )
    parts, new_parts = _part_map(expected), {}
    nets = {row["name"]: row for row in expected["nets"]}
    new_nets, connected = set(), set()
    sheets = {row["number"]: row for row in expected["sheets"]}
    for op in operations:
        _need(type(op) is dict and op.get("type") in _FIELDS, "unsupported_operation")
        kind = op["type"]
        _need(set(op) <= _FIELDS[kind], "unsupported_operation_fields")
        sheet = _sheet(op["sheet"]) if "sheet" in op else None
        if sheet is not None:
            _need(sheet in sheets, "new_or_unobserved_sheet_unsupported")
        if kind in {"place_component", "move_component", "set_property"}:
            ref = op.get("refdes")
            _need(_text(ref) and "." not in ref, "simple_reference_required")
        if kind == "place_component":
            _need(
                len(parts) + len(new_parts) + len(expected["unnamed_components"])
                < LIMITS["components"],
                "resulting_component_resource_limit",
            )
            _need(
                ref not in parts
                and ref.casefold() not in {r.casefold() for r in (*parts, *new_parts)},
                "placement_requires_new_reference",
            )
            _need(
                sheet is not None
                and all(_long(op.get(k)) for k in ("x", "y"))
                and all(_text(op.get(k)) for k in ("part_number", "library", "symbol_name"))
                and ("device_name" not in op or _text(op["device_name"])),
                "explicit_complete_placement_required",
            )
            new_parts[ref] = copy.deepcopy(op)
            sheets[sheet]["parts"] += 1
        elif kind in {"move_component", "set_property"}:
            _need(ref in parts, "target_requires_prior_complete_observation")
            part = parts[ref]
            _need(sheet is None or sheet == _sheet(part["sheet"]), "cross_sheet_edit_unsupported")
            if kind == "move_component":
                _need(
                    all(_long(op.get(k)) for k in ("x", "y")), "integer_move_coordinates_required"
                )
                dx, dy = op["x"] - part["x"], op["y"] - part["y"]
                part.update(x=op["x"], y=op["y"])
                for record in [*part["pins"], *(a["display"] for a in part["attribute_display"])]:
                    record["x"], record["y"] = record["x"] + dx, record["y"] + dy
                    _need(
                        _long(record["x"]) and _long(record["y"]), "translated_coordinates_overflow"
                    )
            else:
                _need(
                    canonical_name(op.get("name")) == "value" and type(op.get("value")) is str,
                    "existing_value_attribute_only",
                )
                names = [n for n in part["attributes"] if canonical_name(n) == "value"]
                _need(
                    len(names) == 1 and names[0] in {"VALUE", "Value"},
                    "unique_existing_value_attribute_required",
                )
                name = names[0]
                part["attributes"][name] = op["value"]
                for attribute in part["attribute_display"]:
                    if attribute["name"] == name:
                        attribute["value"] = op["value"]
                part["value"] = (
                    part["attributes"].get("VALUE")
                    or part["attributes"].get("Value")
                    or part.get("internal_part_no", "")
                )
        elif kind == "create_net":
            name = op.get("name")
            _need(len(nets) < LIMITS["collection_items"], "resulting_net_resource_limit")
            _need(
                _text(name) and not name.startswith("$") and name not in nets,
                "new_explicit_net_name_required",
            )
            nets[name] = {"name": name, "unnamed": False, "sheets": []}
            new_nets.add(name)
        else:
            name, pins = op.get("net"), op.get("pins")
            _need(
                all(_long(op[key]) for key in ("x", "y") if key in op),
                "integer_connection_label_coordinates_required",
            )
            if "points" in op:
                points = op["points"]
                _need(
                    type(points) is list
                    and len(points) <= LIMITS["connect_intermediate_points"]
                    and all(
                        type(point) is list
                        and len(point) == 2
                        and all(_long(coordinate) for coordinate in point)
                        for point in points
                    ),
                    "bounded_integer_connection_points_required",
                )
            _need(
                _text(name) and name in nets and not name.startswith("$"),
                "declared_named_net_required",
            )
            _need(
                type(pins) is list
                and len(pins) == 2
                and all(_text(p) for p in pins)
                and len(set(pins)) == 2,
                "exact_two_distinct_pins_required",
            )
            pinmap = _pin_map(parts)
            _need(
                all(pin in pinmap and "." not in pin.rsplit(".", 1)[0] for pin in pins),
                "pins_require_prior_complete_observation",
            )
            _need(
                len({_sheet(pinmap[p][0]["sheet"]) for p in pins}) == 1
                and (sheet is None or _sheet(pinmap[pins[0]][0]["sheet"]) == sheet),
                "same_sheet_connection_required",
            )
            _need(
                all(
                    pinmap[p][1]["net"] in {None, name} and pinmap[p][1]["no_connect"] is False
                    for p in pins
                ),
                "net_join_or_no_connect_removal_unsupported",
            )
            _need(any(pinmap[p][1]["net"] != name for p in pins), "connection_already_observed")
            for pin in pins:
                pinmap[pin][1].update(net=name, net_unnamed=False)
            connected.add(name)
    _need(new_nets <= connected, "deferred_net_requires_observable_connection")
    members, net_sheets = _members(_pin_map(parts))
    for name, row in nets.items():
        row["sheets"] = sorted(net_sheets.get(name, set()))
    expected["nets"] = sorted(nets.values(), key=lambda r: r["name"])
    expected["connections"] = [
        {"net": n, "pins": sorted(pins)} for n, pins in sorted(members.items()) if len(pins) >= 2
    ]
    return expected, new_parts


def _report(issues, *, complete, count, metadata_changes=None, compared=False):
    checked = ["bounded_plain_json_input"]
    if complete:
        checked += [
            "five_observed_collections",
            "unique_uid_reference_pin_attribute_identity",
            "supported_explicit_operation_masks",
        ]
    if complete and compared:
        checked += ["all_observed_non_target_fields", "requested_observable_target_fields"]
    return {
        "valid": complete and not issues,
        "complete": complete,
        "issues": issues,
        "requested_operations": count,
        "checked_operations": count if complete else 0,
        "checked": checked,
        "verification_performed": compared,
        "metadata_changes": metadata_changes or [],
        "persistence_verified": False,
        "not_checked": list(NOT_CHECKED),
        "limits": dict(LIMITS),
        "_untrusted": ["issues", "metadata_changes"],
    }


def _incomplete(error, operations):
    issue = {"kind": "unsupported_or_incomplete_observation", "reason": error.reason}
    if error.collection:
        issue["collection"] = error.collection
    return _report(
        [issue], complete=False, count=len(operations) if type(operations) is list else 0
    )


def validate_supported(before, operations, *, save_metadata=None):
    """Pure pre-write eligibility; a report is not an authorization capability."""
    try:
        _compile(before, operations, save_metadata)
    except _Invalid as error:
        return _incomplete(error, operations)
    except (CLIError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return _incomplete(_Invalid("malformed_native_observation_or_operation"), operations)
    return _report([], complete=True, count=len(operations))


def verify_schematic_edit_protection(before, after, operations, *, save_metadata=None):
    """Compare complete observations with only the explicitly projected edits."""
    try:
        expected, placed = _compile(before, operations, save_metadata)
        actual = _snapshot(after)
        parts = _part_map(actual)
        old_refs = {r["refdes"] for r in before["components"]}
        issues = []
        if set(parts) != old_refs | set(placed):
            issues.append(
                {"kind": "unrequested_component_addition_or_removal", "collection": "components"}
            )
        old_uids = {r["uid"] for k in ("components", "unnamed_components") for r in before[k]}
        for ref, operation in placed.items():
            if ref not in parts:
                continue
            row = parts[ref]
            matches = (
                row["uid"] not in old_uids
                and row.get("internal_part_no") == operation["part_number"]
                and row["attributes"].get("Part Number") == operation["part_number"]
                and all(row[k] == operation[k] for k in ("x", "y"))
                and _sheet(row["sheet"]) == _sheet(operation["sheet"])
                and all(p["net"] is None and p["no_connect"] is False for p in row["pins"])
            )
            if not matches:
                issues.append(
                    {
                        "kind": "new_component_postconditions_or_identity_mismatch",
                        "collection": "components",
                    }
                )
        actual["components"] = [r for r in actual["components"] if r["refdes"] not in placed]
        metadata_changes = []
        if save_metadata is not None:
            owner = save_metadata["owner"]
            borders = [r for r in actual["unnamed_components"] if r["uid"] == owner["uid"]]
            _need(len(borders) == 1, "bound_save_metadata_owner_missing", "unnamed_components")
            value = borders[0]["attributes"].get("@DATETIME")
            _need(
                _native_border_datetime(value),
                "bound_save_metadata_value_invalid",
                "unnamed_components",
            )
            wanted = next(r for r in expected["unnamed_components"] if r["uid"] == owner["uid"])
            if value != save_metadata["before"]:
                wanted["attributes"]["@DATETIME"] = value
                metadata_changes.append(
                    {
                        "owner": copy.deepcopy(owner),
                        "field": "@DATETIME",
                        "before": save_metadata["before"],
                        "after": value,
                        "source": "native_save_metadata",
                    }
                )
        for key in sorted(set(expected) | set(actual)):
            if expected.get(key, object()) != actual.get(key, object()):
                issues.append(
                    {"kind": "protected_record_or_requested_field_mismatch", "collection": key}
                )
        return _report(
            issues,
            complete=True,
            count=len(operations),
            metadata_changes=metadata_changes,
            compared=True,
        )
    except _Invalid as error:
        return _incomplete(error, operations)
    except (CLIError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return _incomplete(_Invalid("malformed_native_observation_or_operation"), operations)
