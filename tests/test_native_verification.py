from __future__ import annotations

import copy

import pytest

from xpedition_cli.native_verification import verify_native_changes


def project():
    return {
        "components": [
            {
                "refdes": "R1",
                "internal_part_no": "RES-10K",
                "x": 10,
                "y": 20,
                "attributes": {"Value": "10k"},
                "pins": [{"number": "1", "net": "VCC"}, {"number": "2", "net": None}],
            }
        ],
        "nets": [{"name": "VCC"}],
        "connections": [{"net": "VCC", "pins": ["R1.1", "C1.1"]}],
        "pcb": {"components": [{"refdes": "U1", "x": 10, "y": 20}]},
    }


@pytest.mark.parametrize("kind,refdes", [("move_component", "R1"), ("move_pcb_component", "U1")])
def test_move_verifies_requested_coordinates(kind, refdes):
    expected = project()
    ops = [{"type": kind, "refdes": refdes, "x": 10, "y": 20}]
    assert verify_native_changes(expected, copy.deepcopy(expected), ops)["valid"]
    observed = project()
    container = observed["pcb"] if kind == "move_pcb_component" else observed
    container["components"][0]["x"] = 11
    result = verify_native_changes(expected, observed, ops)
    assert not result["valid"]
    assert result["issues"][0]["field"] == "x"


def test_repeated_moves_verify_final_state_not_intermediate_coordinates():
    ops = [
        {"type": "move_component", "refdes": "R1", "x": 0, "y": 0},
        {"type": "move_component", "refdes": "R1", "x": 10, "y": 20},
    ]
    assert verify_native_changes(project(), project(), ops)["valid"]


@pytest.mark.parametrize("wrong", [None, "10", True, float("nan"), float("inf"), 11])
def test_missing_or_invalid_coordinates_never_verify(wrong):
    observed = project()
    observed["components"][0]["x"] = wrong
    assert not verify_native_changes(
        project(), observed, [{"type": "move_component", "refdes": "R1"}]
    )["valid"]


def test_coordinate_tolerance_is_only_serialization_roundoff():
    observed = project()
    observed["components"][0]["x"] += 1e-8
    assert verify_native_changes(project(), observed, [{"type": "move_component", "refdes": "R1"}])[
        "valid"
    ]


def test_part_identity_and_properties_are_checked():
    observed = project()
    observed["components"][0]["internal_part_no"] = "WRONG"
    ops = [{"type": "place_component", "refdes": "R1", "part_number": "RES-10K", "x": 10, "y": 20}]
    assert not verify_native_changes(project(), observed, ops)["valid"]
    observed = project()
    observed["components"][0]["attributes"]["Value"] = "20k"
    assert not verify_native_changes(
        project(), observed, [{"type": "set_property", "refdes": "R1", "name": "Value"}]
    )["valid"]
    assert verify_native_changes(
        project(), project(), [{"type": "set_property", "refdes": "R1", "name": "Value"}]
    )["valid"]


def test_a_disconnected_pin_must_read_back_open():
    ops = [{"type": "disconnect", "pin": "R1.1"}]
    assert not verify_native_changes(project(), project(), ops)["valid"]
    observed = project()
    observed["components"][0]["pins"][0]["net"] = None
    assert verify_native_changes(project(), observed, ops)["valid"]


def test_a_renamed_net_must_be_gone_and_its_pins_on_the_new_name():
    ops = [{"type": "rename_net", "net": "VCC", "name": "VDD"}]
    expected = project()
    expected["nets"] = [{"name": "VDD"}]
    expected["connections"] = [{"net": "VDD", "pins": ["R1.1", "C1.1"]}]
    result = verify_native_changes(expected, project(), ops)
    assert {issue["kind"] for issue in result["issues"]} >= {"net_not_renamed"}
    assert verify_native_changes(expected, copy.deepcopy(expected), ops)["valid"]


def test_deleted_component_must_be_absent_and_disconnected():
    expected, observed = project(), project()
    expected["components"] = []
    ops = [{"type": "delete_component", "refdes": "R1"}]
    assert not verify_native_changes(expected, observed, ops)["valid"]
    observed["components"] = []
    assert not verify_native_changes(expected, observed, ops)["valid"]
    observed["connections"] = []
    assert verify_native_changes(expected, observed, ops)["valid"]


def test_connectivity_accepts_order_changes_but_not_missing_or_cross_net_pins():
    ops = [{"type": "connect", "net": "VCC", "pins": ["R1.1", "C1.1"]}]
    observed = project()
    observed["connections"][0]["pins"].reverse()
    assert verify_native_changes(project(), observed, ops)["valid"]
    observed["connections"].append({"net": "GND", "pins": ["R1.1"]})
    assert not verify_native_changes(project(), observed, ops)["valid"]
    observed = project()
    observed["connections"][0]["pins"].pop()
    assert not verify_native_changes(project(), observed, ops)["valid"]


def test_duplicate_targets_and_missing_nets_fail():
    observed = project()
    observed["components"].append(copy.deepcopy(observed["components"][0]))
    assert not verify_native_changes(
        project(), observed, [{"type": "move_component", "refdes": "R1"}]
    )["valid"]
    observed["nets"] = []
    assert not verify_native_changes(project(), observed, [{"type": "create_net", "name": "VCC"}])[
        "valid"
    ]


def test_unsupported_verification_is_not_success():
    result = verify_native_changes(project(), project(), [{"type": "create_track"}])
    assert not result["valid"]
    assert result["issues"][0]["kind"] == "unverified_operation"
