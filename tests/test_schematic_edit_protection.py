"""Pure complete snapshots; no fixture is CAD editing or persistence evidence."""

from __future__ import annotations

import copy

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import schematic_edit_protection as P
from xpedition_cli.models import normalise_project
from xpedition_cli.schematic_attribute_display import DISPLAY_NAMES, freeze_save_metadata


def _part(ref, uid, pn, x, y=50, *, border=False):
    attributes = (
        {"@NAME": "Board1", "@Sheet": "1", "@SHEETTOTAL": "1", "@DATETIME": "27/10/2026:10:00"}
        if border
        else {"Part Number": pn, "Value": "10K", "Tolerance": "1%"}
    )
    displays = []
    for name in ("Part Number", "Value") if not border else ():
        displays.append(
            {
                "name": name,
                "value": attributes[name],
                "owner": {"refdes": ref, "uid": uid, "sheet": 1},
                "display": {
                    "units": "sheet_unit",
                    "x": x - 10,
                    "y": y - 20,
                    "visible": 3,
                    "size": 10,
                    "origin": 3,
                    "orientation": 0,
                },
                "getter_errors": {},
            }
        )
    return {
        "refdes": ref,
        "uid": uid,
        "name": pn or ref,
        "internal_part_no": pn,
        "value": "10K" if not border else "",
        "description": "",
        "package": "",
        "manufacturer": "",
        "mpn": "",
        "sheet": 1,
        "x": x,
        "y": y,
        "rotation": 0,
        "attributes": attributes,
        "attribute_display": displays,
        "identity_observation_coverage": {"complete": True, "errors": []},
        "pin_observation_coverage": {"complete": True, "errors": []},
        "attribute_display_coverage": {
            "names": list(DISPLAY_NAMES),
            "enumeration_complete": True,
            "attribute_count": len(attributes),
            "attribute_limit": 256,
            "truncated": False,
            "errors": [],
            "other_text_graphics_observed": False,
        },
        "pins": []
        if border
        else [
            {
                "number": number,
                "net": None,
                "net_unnamed": False,
                "no_connect": False,
                "x": x + delta,
                "y": y,
            }
            for number, delta in (("1", -10), ("2", 10))
        ],
    }


def _snapshot(*, empty=False):
    parts = [] if empty else [_part("R1", "u1", "R0603", 10), _part("C1", "u2", "C0805-10N-J", 80)]
    return {
        "project": "Board1",
        "revision": "native",
        "components": parts,
        "unnamed_components": [_part("", "border", "", 0, 0, border=True)],
        "sheets": [{"number": 1, "name": "Board1.1", "parts": len(parts)}],
        "nets": [],
        "connections": [],
        "metadata": {
            "backend": "native_xpedition",
            "source": "Viewdraw.Application",
            "domain": "schematic",
            "unnamed_symbols": 1,
        },
    }


def _connections(snapshot, names):
    for number, net in names.items():
        for part in snapshot["components"]:
            part["pins"][number - 1]["net"] = net
        snapshot["nets"].append({"name": net, "unnamed": False, "sheets": [1]})
        snapshot["connections"].append({"net": net, "pins": [f"R1.{number}", f"C1.{number}"]})


def _moved(snapshot, x=30, y=40):
    part = snapshot["components"][0]
    dx, dy = x - part["x"], y - part["y"]
    part.update(x=x, y=y)
    for pin in part["pins"]:
        pin["x"] += dx
        pin["y"] += dy
    for attribute in part["attribute_display"]:
        attribute["display"]["x"] += dx
        attribute["display"]["y"] += dy


def _value(snapshot, value="22K"):
    part = snapshot["components"][0]
    part["attributes"]["Value"] = value
    part["value"] = value
    next(a for a in part["attribute_display"] if a["name"] == "Value")["value"] = value


def test_two_parts_two_nets_property_move_preserve_every_other_observed_field():
    before, after = _snapshot(), _snapshot()
    operations = [
        {"type": "create_net", "name": "A"},
        {"type": "connect", "net": "A", "pins": ["R1.1", "C1.1"]},
        {"type": "create_net", "name": "B"},
        {"type": "connect", "net": "B", "pins": ["R1.2", "C1.2"]},
        {"type": "set_property", "refdes": "R1", "name": "Value", "value": "22K"},
        {"type": "move_component", "refdes": "R1", "x": 30, "y": 40},
    ]
    original = copy.deepcopy(before)
    _connections(after, {1: "A", 2: "B"})
    _value(after)
    _moved(after)
    assert P.validate_supported(before, operations)["valid"] is True
    result = P.verify_schematic_edit_protection(before, after, operations)
    assert result["valid"] is result["complete"] is True
    assert result["persistence_verified"] is False
    assert "wire_segments_routes_labels_and_other_free_graphics" in result["not_checked"]
    assert before == original
    # Native and CLI-normalised sheet identifiers and census permutations share
    # semantics, while no UID/value/pin is dropped or merged.
    after["components"].reverse()
    after["nets"].reverse()
    after["connections"].reverse()
    for part in after["components"]:
        part["pins"].reverse()
        part["attribute_display"].reverse()
    assert P.verify_schematic_edit_protection(before, normalise_project(after), operations)["valid"]


def test_two_new_parts_have_exact_new_references_uids_and_unconnected_pins():
    before, after = _snapshot(empty=True), _snapshot()
    operations = [
        {
            "type": "place_component",
            "refdes": ref,
            "part_number": pn,
            "library": "Discrete",
            "symbol_name": symbol,
            "x": x,
            "y": 50,
            "sheet": 1,
        }
        for ref, pn, symbol, x in (("R1", "R0603", "res", 10), ("C1", "C0805-10N-J", "cap", 80))
    ]
    assert P.validate_supported(before, operations)["valid"]
    assert P.verify_schematic_edit_protection(before, after, operations)["valid"]
    after["components"][1]["internal_part_no"] = "OTHER"
    assert not P.verify_schematic_edit_protection(before, after, operations)["valid"]


@pytest.mark.parametrize(
    "change", ["non_target", "target_attr", "target_display", "target_pin", "rotation"]
)
def test_move_is_not_a_blanket_target_part_exception(change):
    before, after = _snapshot(), _snapshot()
    operation = [{"type": "move_component", "refdes": "R1", "x": 30, "y": 40}]
    _moved(after)
    if change == "non_target":
        after["components"][1]["attributes"]["Tolerance"] = "5%"
    elif change == "target_attr":
        after["components"][0]["attributes"]["Tolerance"] = "5%"
    elif change == "target_display":
        after["components"][0]["attribute_display"][0]["display"]["x"] += 1
    elif change == "target_pin":
        after["components"][0]["pins"][1]["x"] += 1
    else:
        after["components"][0]["rotation"] = 90
    result = P.verify_schematic_edit_protection(before, after, operation)
    assert result["complete"] is True and result["valid"] is False


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_uid",
        "missing_uid",
        "missing_collection",
        "pin_coverage",
        "attr_coverage",
        "pin_coordinate",
    ],
)
def test_incomplete_or_ambiguous_observation_refuses_before_writing(change):
    before = _snapshot()
    if change == "duplicate_uid":
        before["components"][1]["uid"] = "u1"
    elif change == "missing_uid":
        before["components"][0].pop("uid")
    elif change == "missing_collection":
        before.pop("unnamed_components")
    elif change == "pin_coverage":
        before["components"][0]["pin_observation_coverage"]["complete"] = False
    elif change == "attr_coverage":
        before["components"][0]["attribute_display_coverage"]["attribute_count"] = 1
    else:
        before["components"][0]["pins"][0]["x"] = None
    result = P.validate_supported(
        before, [{"type": "move_component", "refdes": "R1", "x": 30, "y": 40}]
    )
    assert result["valid"] is result["complete"] is False


@pytest.mark.parametrize("change", ["extra_net", "wrong_pin"])
def test_complete_graph_changes_outside_the_requested_two_pins_are_rejected(change):
    before, after = _snapshot(), _snapshot()
    operations = [
        {"type": "create_net", "name": "A"},
        {"type": "connect", "net": "A", "pins": ["R1.1", "C1.1"]},
    ]
    _connections(after, {1: "A"})
    if change == "extra_net":
        _connections(after, {2: "EXTRA"})
    else:
        after = _snapshot()
        _connections(after, {2: "A"})
    result = P.verify_schematic_edit_protection(before, after, operations)
    assert result["complete"] is True and result["valid"] is False


def test_preexisting_other_net_cannot_be_implicitly_joined():
    before = _snapshot()
    _connections(before, {2: "OLD"})
    operations = [
        {"type": "create_net", "name": "NEW"},
        {"type": "connect", "net": "NEW", "pins": ["R1.2", "C1.1"]},
    ]
    result = P.validate_supported(before, operations)
    assert not result["valid"]
    assert result["issues"][0]["reason"] == "net_join_or_no_connect_removal_unsupported"


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "delete_component", "refdes": "R1"},
        {"type": "set_property", "refdes": "R1", "name": "Tolerance", "value": "5%"},
        {
            "type": "set_property",
            "refdes": "R1",
            "name": "Value",
            "value": "5K",
            "display": {"x": 1},
        },
        {"type": "move_component", "refdes": "R1", "x": True, "y": 0},
        {"type": "move_component", "refdes": "R1", "x": 1.5, "y": 0},
        {"type": "move_component", "refdes": "R1", "x": 0, "y": 0, "verified": True},
        {"type": "create_net", "name": "UNOBSERVABLE"},
        {"type": "connect", "net": "A", "pins": ["R1.1", "R1.1"]},
    ],
)
def test_unsupported_fields_types_or_unobservable_operations_refuse(operation):
    result = P.validate_supported(_snapshot(), [operation])
    assert result["valid"] is result["complete"] is False


def test_only_the_frozen_border_timestamp_can_change_and_none_is_strict():
    before, after = _snapshot(), _snapshot()
    operation = [{"type": "set_property", "refdes": "R1", "name": "Value", "value": "22K"}]
    _value(after)
    after["unnamed_components"][0]["attributes"]["@DATETIME"] = "27/10/2026:10:01"
    assert not P.verify_schematic_edit_protection(before, after, operation)["valid"]
    metadata = freeze_save_metadata(before)
    result = P.verify_schematic_edit_protection(before, after, operation, save_metadata=metadata)
    assert result["valid"] and len(result["metadata_changes"]) == 1
    after["unnamed_components"][0]["attributes"]["@NAME"] = "OTHER"
    assert not P.verify_schematic_edit_protection(before, after, operation, save_metadata=metadata)[
        "valid"
    ]
    metadata["owner"]["uid"] = "foreign"
    assert not P.validate_supported(before, operation, save_metadata=metadata)["valid"]


def test_bounded_plain_json_rejects_malicious_schema_and_operation_count():
    before = _snapshot()
    before["metadata"]["bad"] = object()
    operation = {"type": "move_component", "refdes": "R1", "x": 0, "y": 0}
    assert not P.validate_supported(before, [operation])["valid"]
    assert not P.validate_supported(_snapshot(), [operation] * 65)["valid"]
    cyclic = _snapshot()
    cyclic["metadata"]["cycle"] = cyclic
    assert not P.validate_supported(cyclic, [operation])["valid"]


@pytest.mark.parametrize("change", ["pin", "other_attribute", "display"])
def test_value_edit_protects_the_targets_other_pin_attribute_and_display_fields(change):
    before, after = _snapshot(), _snapshot()
    operations = [{"type": "set_property", "refdes": "R1", "name": "Value", "value": "22K"}]
    _value(after)
    target = after["components"][0]
    if change == "pin":
        target["pins"][1]["x"] += 1
    elif change == "other_attribute":
        target["attributes"]["Tolerance"] = "5%"
    else:
        target["attribute_display"][0]["display"]["visible"] = 0
    result = P.verify_schematic_edit_protection(before, after, operations)
    assert result["complete"] is True and result["valid"] is False


def test_unknown_timestamp_is_never_normalised_to_zero_or_an_empty_record():
    before = _snapshot()
    metadata = freeze_save_metadata(before)
    before["unnamed_components"][0]["attributes"]["@DATETIME"] = "unknown"
    operations = [{"type": "move_component", "refdes": "R1", "x": 30, "y": 40}]
    result = P.validate_supported(before, operations, save_metadata=metadata)
    assert not result["valid"] and not result["complete"]
    assert before["unnamed_components"][0]["attributes"]["@DATETIME"] == "unknown"
    assert result["checked_operations"] == 0 and result["verification_performed"] is False


def test_new_reference_and_fresh_observation_boundaries_are_explicit():
    before = _snapshot()
    placement = {
        "type": "place_component",
        "refdes": "R1",
        "part_number": "R0603",
        "library": "Discrete",
        "symbol_name": "res",
        "x": 10,
        "y": 50,
        "sheet": 1,
    }
    assert not P.validate_supported(before, [placement])["valid"]
    placement["refdes"] = "R2"
    assert P.validate_supported(before, [placement])["valid"]
    result = P.validate_supported(
        before, [placement, {"type": "move_component", "refdes": "R2", "x": 20, "y": 50}]
    )
    assert not result["valid"]
    assert result["issues"][0]["reason"] == "target_requires_prior_complete_observation"


def test_a_complete_unrequested_unnamed_object_is_not_hidden_by_a_value_edit():
    before, after = _snapshot(), _snapshot()
    operations = [{"type": "set_property", "refdes": "R1", "name": "Value", "value": "22K"}]
    _value(after)
    after["unnamed_components"].append(_part("", "orphan", "", 20, 20, border=True))
    after["metadata"]["unnamed_symbols"] = 2
    result = P.verify_schematic_edit_protection(before, after, operations)
    assert result["complete"] is True and result["valid"] is False


def _public_connect_operations():
    return [
        {"type": "create_net", "name": "RC_HIGH", "sheet": 1},
        {
            "type": "connect",
            "net": "RC_HIGH",
            "pins": ["R1.1", "C1.1"],
            "points": [[300, 480], [600, 480]],
            "sheet": 1,
            "x": 450,
            "y": 490,
        },
        {"type": "create_net", "name": "RETURN", "sheet": 1},
        {
            "type": "connect",
            "net": "RETURN",
            "pins": ["R1.2", "C1.2"],
            "points": [[300, 300], [600, 300]],
            "sheet": 1,
            "x": 450,
            "y": 280,
        },
    ]


def test_actual_public_connect_parameter_shape_remains_electrically_protected():
    before, after = _snapshot(), _snapshot()
    operations = _public_connect_operations()
    original = copy.deepcopy(operations)
    assert P.validate_supported(before, operations)["valid"] is True
    _connections(after, {1: "RC_HIGH", 2: "RETURN"})
    result = P.verify_schematic_edit_protection(before, after, operations)
    assert result["valid"] is result["complete"] is True
    assert "wire_segments_routes_labels_and_other_free_graphics" in result["not_checked"]
    assert operations == original
    after["components"][1]["attributes"]["Tolerance"] = "5%"
    result = P.verify_schematic_edit_protection(before, after, operations)
    assert result["complete"] is True and result["valid"] is False


@pytest.mark.parametrize("points", [[], [[1, 2]], [[1, 2]] * 64])
def test_declared_connection_point_count_is_bounded_without_claiming_route_geometry(points):
    operations = _public_connect_operations()
    operations[1]["points"] = points
    assert P.validate_supported(_snapshot(), operations)["valid"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("x", True),
        ("y", 1.5),
        ("x", 2**31),
        ("y", -(2**31) - 1),
        ("x", float("nan")),
        ("points", None),
        ("points", [[1, 2]] * 65),
        ("points", [[True, 1]]),
        ("points", [[1, float("nan")]]),
        ("points", [[1, 2**31]]),
        ("points", [[1]]),
        ("points", [[1, 2, 3]]),
        ("verified", True),
    ],
)
def test_public_connect_geometry_rejects_bad_types_ranges_sizes_or_extra_fields(field, value):
    operations = _public_connect_operations()
    operations[1][field] = value
    result = P.validate_supported(_snapshot(), operations)
    assert result["valid"] is result["complete"] is False
    assert result["verification_performed"] is False
