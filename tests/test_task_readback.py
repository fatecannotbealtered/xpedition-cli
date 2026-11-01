from __future__ import annotations

import copy
import hashlib

import pytest

from xpedition_cli.driver import _schematic_build_readback
from xpedition_cli.task_readback import verify_task


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "save_false",
        "close_none",
        "double_save",
        "missing_close_observation",
        "wrong_path",
        "wrong_pid",
        "different_actual_pid",
        "non_target",
        "old_copper",
        "incomplete_native",
        "different_before",
        "forged_digest",
        "missing_post_edit",
        "missing_reopen_comparison",
        "forged_reopen_comparison",
    ],
)
def test_batch_file_sdk_readback_requires_complete_independent_material_and_strict_lifecycle(
    fault, tmp_path, monkeypatch
):
    from test_batch_material_validation import batch_plan

    from xpedition_cli import native_process
    from xpedition_cli.placement import batch_reopen_comparison, expected_state

    bound = batch_plan()
    original = copy.deepcopy(bound["protection_before"])
    observed = expected_state(original, bound["results"])
    board = str(tmp_path / "own.pcb")
    synthetic_process = {
        "pid": 4141,
        "creation_time_100ns": "12345678901",
        "executable": "C:/synthetic/cad/expeditionpcb.exe",
    }
    monkeypatch.setattr(
        native_process, "process_identity", lambda *_: copy.deepcopy(synthetic_process)
    )
    lifecycle = {
        "save_count": 1,
        "close_count": 1,
        "open_count": 1,
        "save_acknowledged": True,
        "save_returned": True,
        "save_return_type": "bool",
        "save_reply_received": True,
        "close_acknowledged": True,
        "close_returned": True,
        "close_return_type": "bool",
        "close_reply_received": True,
        "closed_observed": True,
        "open_reply_received": True,
        "reopen_verified": True,
        "pid_before": 4141,
        "pid_after": 4141,
        "board": board,
        "reopened_path": board,
        "process_identity_before": copy.deepcopy(synthetic_process),
        "process_identity_after": copy.deepcopy(synthetic_process),
    }
    result = {
        "outcome": "complete",
        "saved": True,
        "persistence_verified": True,
        "verification": {"valid": True},
        "lifecycle": lifecycle,
        "geometry_after": copy.deepcopy(observed),
        "geometry_after_edit": copy.deepcopy(observed),
        "pcb": board,
    }
    # This fixture uses exact raw equality. Rebuilt Net0 references and the
    # independently observed OS generation are covered by test_net0_reopen_identity.
    result["reopen_comparison"] = batch_reopen_comparison(
        result["geometry_after_edit"], result["geometry_after"], lifecycle
    )
    if fault == "save_false":
        lifecycle["save_acknowledged"] = False
    elif fault == "close_none":
        lifecycle["close_acknowledged"] = None
    elif fault == "double_save":
        lifecycle["save_count"] = 2
    elif fault == "missing_close_observation":
        lifecycle.pop("closed_observed")
    elif fault == "wrong_path":
        lifecycle["reopened_path"] += ".wrong"
    elif fault == "wrong_pid":
        lifecycle["pid_after"] = 2323
    elif fault == "different_actual_pid":
        lifecycle["pid_before"] = lifecycle["pid_after"] = 2323
    elif fault == "non_target":
        observed["components"][-1]["footprint"] = "OTHER"
    elif fault == "old_copper":
        observed["model"]["traces"][0]["net"] = "OTHER"
    elif fault == "incomplete_native":
        observed["components"].pop()
    elif fault == "different_before":
        original["components"][-1]["footprint"] = "OTHER"
    elif fault == "forged_digest":
        bound["state_digest"] = "a" * 64
    elif fault == "missing_post_edit":
        result.pop("geometry_after_edit")
    elif fault == "missing_reopen_comparison":
        result.pop("reopen_comparison")
    elif fault == "forged_reopen_comparison":
        result["reopen_comparison"]["raw_after_sha256"] = "0" * 64

    def snapshot(state):
        return {
            "extensions": {
                "facts": {"cell_refresh": {"geometry": state}, "application": {"pid": 4141}}
            }
        }

    proof = verify_task(
        "pcb placement move",
        {"file": "task.json"},
        result,
        snapshot(original),
        snapshot(observed),
        {"extensions": {"legacy_preview": bound}},
    )
    assert proof["valid"] is (fault is None)


def test_batch_noop_is_verified_without_fabricating_save_or_reopen(tmp_path):
    from test_batch_material_validation import representative_board

    from xpedition_cli.placement import plan_placement

    rows, state = representative_board()
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": [rows[0]["refdes"]],
        "steps": [{"op": "translate", "dx": 0, "dy": 0}],
    }
    bound = plan_placement(request, rows, protection_state=state)
    snapshot = {"extensions": {"facts": {"cell_refresh": {"geometry": state}}}}
    result = {
        "outcome": "complete",
        "saved": False,
        "write_attempted": False,
        "persistence_verified": False,
        "no_change": True,
        "verification": {"valid": True},
        "lifecycle": {"save_count": 0, "close_count": 0, "open_count": 0},
        "geometry_after": state,
    }
    proof = verify_task(
        "pcb placement move",
        {"file": "task.json"},
        result,
        snapshot,
        snapshot,
        {"extensions": {"legacy_preview": bound}},
    )
    assert proof["valid"] is True and proof["no_change"] is True
    assert proof["persistence_verified"] is False


@pytest.mark.parametrize(
    "fault", [None, "empty_board", "missing_part", "wrong_pin_net", "wrong_part", "no_reopen"]
)
def test_two_qa_update_requires_actual_parts_pins_and_reopen(fault):
    source = {
        "components": [
            {
                "refdes": ref,
                "uid": str(index),
                "internal_part_no": number,
                "pins": [{"number": "1", "net": "QA_SIGNAL"}, {"number": "2", "net": "QA_RETURN"}],
            }
            for index, (ref, number) in enumerate(
                (("J1", "QA_J1X02_P254"), ("R1", "QA_R0805_10K")), start=1
            )
        ]
    }
    actual = copy.deepcopy(source)
    for row in actual["components"]:
        row["part_number"] = row.pop("internal_part_no")
    result = {"outcome": "annotated", "persistence_verified": True, "errors": []}
    if fault == "empty_board":
        actual["components"] = []
    elif fault == "missing_part":
        actual["components"].pop()
    elif fault == "wrong_pin_net":
        actual["components"][0]["pins"][1]["net"] = "QA_SIGNAL"
    elif fault == "wrong_part":
        actual["components"][0]["part_number"] = "OTHER"
    elif fault == "no_reopen":
        result["persistence_verified"] = False
    proof = verify_task(
        "pcb update",
        {},
        result,
        {},
        {"extensions": {"facts": {"native": actual}}},
        {"extensions": {"legacy_preview": {"verification_input": source}}},
    )
    assert proof["valid"] is (fault is None)


def _phone_update_snapshots():
    """R18r10 pattern: 127 parts, 467 pins, 27 NCs and 23 unnamed singletons."""
    source, actual = {"components": []}, {"components": []}
    for index in range(127):
        pins = [
            {"number": str(n + 1), "net": "SIGNAL", "net_unnamed": False, "no_connect": False}
            for n in range(3 + (index < 86))
        ]
        if index < 27:
            pins[0].update(net=None, no_connect=True)
        elif index < 50:
            pins[0].update(net=f"$1N{index}", net_unnamed=True)
        row = {
            "refdes": f"U{index + 101}",
            "uid": f"$1I{index + 1}",
            "internal_part_no": f"PN{index}",
            "pins": pins,
            "identity_observation_coverage": {"complete": True, "errors": []},
            "pin_observation_coverage": {"complete": True, "errors": []},
        }
        source["components"].append(row)
        actual["components"].append(
            {
                "refdes": row["refdes"],
                "uid": index + 1,  # Layout's native UniqueId can be an integer.
                "part_number": row["internal_part_no"],
                "pins": [
                    {
                        "number": pin["number"],
                        "net": "(Net0)" if n == 0 and index < 50 else pin["net"],
                        "x": None,
                        "y": None,
                    }
                    for n, pin in enumerate(pins)
                ],
            }
        )
    return source, actual


def _update_proof(source, actual):
    return verify_task(
        "pcb update",
        {},
        {"outcome": "annotated", "persistence_verified": True, "errors": []},
        {},
        {"extensions": {"facts": {"native": actual}}},
        {"extensions": {"legacy_preview": {"verification_input": source}}},
    )


def test_phone_update_accepts_only_bounded_native_nc_equivalence_without_rewriting_evidence():
    source, actual = _phone_update_snapshots()
    originals = copy.deepcopy((source, actual))
    proof = _update_proof(source, actual)
    assert proof["valid"] is True
    assert len(proof["expected"]["parts"]) == 127
    assert len(proof["expected"]["pins"]) == 467
    assert sum(net is None for net in proof["expected"]["pins"].values()) == 27
    assert sum(net == "(Net0)" for net in proof["observed"]["pins"].values()) == 50
    assert proof["expected"]["pins"]["U128.1"] == "$1N27"
    assert (source, actual) == originals


@pytest.mark.parametrize(
    "fault",
    [
        "changed_named_net",
        "connected_to_net0",
        "multi_pin_anonymous",
        "multi_pin_anonymous_unflagged_member",
        "nc_flag_missing",
        "nc_flag_false",
        "unnamed_flag_missing",
        "unnamed_flag_false",
        "unnamed_nc_conflict",
        "missing_net",
        "empty_net",
        "unknown_actual_net",
        "net0_alias",
        "missing_part_number",
        "partial_pins",
        "partial_identity",
    ],
)
def test_phone_update_nc_equivalence_rejects_connected_or_unknown_source_evidence(fault):
    source, actual = _phone_update_snapshots()
    nc = source["components"][0]["pins"][0]
    unnamed = source["components"][27]["pins"][0]
    if fault == "changed_named_net":
        actual["components"][50]["pins"][1]["net"] = "OTHER"
    elif fault == "connected_to_net0":
        actual["components"][50]["pins"][1]["net"] = "(Net0)"
    elif fault.startswith("multi_pin_anonymous"):
        member = source["components"][28]["pins"][0]
        member["net"] = unnamed["net"]
        if fault == "multi_pin_anonymous_unflagged_member":
            member["net_unnamed"] = False
    elif fault == "nc_flag_missing":
        nc.pop("no_connect")
    elif fault == "nc_flag_false":
        nc["no_connect"] = False
    elif fault == "unnamed_flag_missing":
        unnamed.pop("net_unnamed")
    elif fault == "unnamed_flag_false":
        unnamed["net_unnamed"] = False
    elif fault == "unnamed_nc_conflict":
        unnamed["no_connect"] = True
    elif fault == "missing_net":
        nc.pop("net")
    elif fault == "empty_net":
        nc["net"] = ""
    elif fault == "unknown_actual_net":
        actual["components"][27]["pins"][0]["net"] = None
    elif fault == "net0_alias":
        actual["components"][0]["pins"][0]["net"] = "net0"
    elif fault == "missing_part_number":
        source["components"][0].pop("internal_part_no")
        actual["components"][0].pop("part_number")
    elif fault == "partial_pins":
        source["components"][0]["pin_observation_coverage"]["complete"] = False
    elif fault == "partial_identity":
        source["components"][0]["identity_observation_coverage"]["errors"] = ["getter failed"]
    assert _update_proof(source, actual)["valid"] is False


@pytest.mark.parametrize("snapshot", ["source", "actual"])
@pytest.mark.parametrize(
    "fault",
    [
        "duplicate_refdes",
        "missing_refdes",
        "duplicate_uid",
        "missing_uid",
        "empty_uid",
        "bool_uid",
        "duplicate_pin",
        "missing_pin_number",
        "missing_pins",
        "missing_component",
        "missing_pin",
    ],
)
def test_phone_update_requires_complete_unique_component_and_pin_identities(snapshot, fault):
    source, actual = _phone_update_snapshots()
    rows = (source if snapshot == "source" else actual)["components"]
    if fault == "duplicate_refdes":
        rows[1]["refdes"] = rows[0]["refdes"]
    elif fault == "missing_refdes":
        rows[0].pop("refdes")
    elif fault == "duplicate_uid":
        rows[1]["uid"] = str(rows[0]["uid"])
    elif fault == "missing_uid":
        rows[0].pop("uid")
    elif fault == "empty_uid":
        rows[0]["uid"] = ""
    elif fault == "bool_uid":
        rows[0]["uid"] = True
    elif fault == "duplicate_pin":
        rows[0]["pins"].append(copy.deepcopy(rows[0]["pins"][0]))
    elif fault == "missing_pin_number":
        rows[0]["pins"][0].pop("number")
    elif fault == "missing_pins":
        rows[0].pop("pins")
    elif fault == "missing_component":
        rows.pop()
    elif fault == "missing_pin":
        rows[0]["pins"].pop()
    assert _update_proof(source, actual)["valid"] is False


@pytest.mark.parametrize("fault", [None, "save", "missing", "joined", "part_number", "orphan"])
def test_schematic_readback_requires_reopen_and_exact_instance_connectivity(fault):
    expected = {
        "parts": ["J1", "R1"],
        "part_numbers": {"J1": "QA_J", "R1": "QA_R"},
        "nets": {"SIGNAL": ["J1.1", "R1.1"]},
        "links": [],
        "no_connect": [],
    }
    actual = {
        "components": [
            {"refdes": ref, "internal_part_no": part, "pins": [{"number": "1", "net": "SIGNAL"}]}
            for ref, part in expected["part_numbers"].items()
        ],
        "unnamed_components": [],
    }
    result = {
        "persistence_verified": True,
        "verification_source": "native_getters_after_sheet_save_close_open",
    }
    if fault == "save":
        result["persistence_verified"] = False
    elif fault == "missing":
        actual["components"].pop()
    elif fault == "joined":
        actual["components"][0]["pins"][0]["net"] = "OTHER"
    elif fault == "part_number":
        actual["components"][0]["internal_part_no"] = "OTHER"
    elif fault == "orphan":
        actual["unnamed_components"].append({"internal_part_no": "QA_J"})
    proof = _schematic_build_readback(
        result,
        {"extensions": {"legacy_preview": {"verification_input": expected}}},
        {"extensions": {"facts": {"native": actual}}},
    )
    assert proof["valid"] is (fault is None)


@pytest.mark.parametrize("fault", [None, "stale", "wrong_bytes", "no_report"])
def test_packager_success_requires_its_own_fresh_report_bytes(tmp_path, fault):
    project = tmp_path / "QA.prj"
    path = tmp_path / "Integration/PartPkg.log"
    path.parent.mkdir()
    path.write_bytes(b"fixture completed packager report")
    result = {
        "packaged": True,
        "exit_code": 0,
        "errors": [],
        "started_ns": path.stat().st_mtime_ns,
        "log_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    if fault == "stale":
        result["started_ns"] += 1
    elif fault == "wrong_bytes":
        result["log_sha256"] = "a" * 64
    elif fault == "no_report":
        path.unlink()
    assert verify_task("schematic package", {"project": str(project)}, result, {}, {})["valid"] is (
        fault is None
    )


@pytest.mark.parametrize(
    "fault", [None, "wrong_position", "changed_neighbor", "replacement", "no_save"]
)
def test_placement_readback_requires_persistence_and_preserves_other_instances(fault):
    original = [
        {"refdes": "J1", "uid": "1", "x": 0, "y": 0, "rotation": 0, "placed": True},
        {"refdes": "R1", "uid": "2", "x": 3, "y": 4, "rotation": 0, "placed": True},
    ]
    actual = copy.deepcopy(original)
    actual[0].update(x=20, y=15, rotation=90)
    result = {"persistence_verified": True}
    if fault == "wrong_position":
        actual[0]["x"] = 21
    elif fault == "changed_neighbor":
        actual[1]["y"] = 5
    elif fault == "replacement":
        actual[0]["uid"] = "3"
    elif fault == "no_save":
        result["persistence_verified"] = False

    def snap(rows):
        return {"extensions": {"facts": {"native": {"components": rows}}}}

    assert verify_task(
        "pcb placement move",
        {"refdes": "J1", "to": "20,15", "rotate": "90"},
        result,
        snap(original),
        snap(actual),
    )["valid"] is (fault is None)


def _phone_placement_snapshots():
    original = [
        {
            "refdes": "U501" if index == 0 else f"R{index}",
            "uid": 177 if index == 0 else index + 1000,
            "part_number": "ESP32_S3_N8R8" if index == 0 else "R_10K",
            "footprint": "DEMO_ESP41" if index == 0 else "DEMO_R0402",
            "x": 0.0,
            "y": 0.0,
            "unit": "mm",
            "rotation": 0.0,
            "side": "top",
            "placed": False,
            "pins": [{"number": "1", "net": "GND", "x": None, "y": None}],
        }
        for index in range(127)
    ]
    actual = copy.deepcopy(original)
    actual[0].update(x=35.0, y=133.0, placed=True)
    actual[0]["pins"][0].update(x=26.0, y=141.0)
    # The frozen receipt changed 54 outer enumeration positions after reopening.
    actual[1:55] = reversed(actual[1:55])
    return original, actual


def _placement_proof(original, actual):
    def snap(rows):
        return {"extensions": {"facts": {"native": {"components": rows}}}}

    return verify_task(
        "pcb placement move",
        {"refdes": "U501", "to": "35,133", "rotate": "0"},
        {"persistence_verified": True},
        snap(original),
        snap(actual),
    )


def test_phone_placement_accepts_outer_enumeration_shuffle_and_keeps_original_records():
    original, actual = _phone_placement_snapshots()
    unchanged = copy.deepcopy((original, actual))
    assert (
        sum(a["refdes"] != b["refdes"] for a, b in zip(original[1:], actual[1:], strict=True)) == 54
    )
    proof = _placement_proof(original, actual)
    assert proof["valid"] is True
    assert proof["observed"] == actual[0]
    assert (original, actual) == unchanged


@pytest.mark.parametrize("snapshot", ["before", "after", "both"])
@pytest.mark.parametrize(
    "fault", ["duplicate_refdes", "missing_refdes", "duplicate_uid", "missing_uid", "empty_uid"]
)
def test_placement_requires_unique_complete_identities_in_both_snapshots(snapshot, fault):
    original, actual = _phone_placement_snapshots()
    states = (
        [original, actual] if snapshot == "both" else [original if snapshot == "before" else actual]
    )
    for rows in states:
        if fault == "duplicate_refdes":
            rows.append(copy.deepcopy(rows[1]))
            rows[-1]["uid"] = 9000
        elif fault == "missing_refdes":
            rows[1].pop("refdes")
        elif fault == "duplicate_uid":
            rows[1]["uid"] = str(rows[0]["uid"])
        elif fault == "missing_uid":
            rows[1].pop("uid")
        elif fault == "empty_uid":
            rows[1]["uid"] = ""
    assert _placement_proof(original, actual)["valid"] is False


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize(
    "field,value",
    [
        ("part_number", "OTHER"),
        ("footprint", "REPLACEMENT_CELL"),
        ("side", "bottom"),
        ("uid", 9000),
        ("pin_net", "OTHER"),
        ("pin_number", "2"),
        ("missing_pin", None),
    ],
)
def test_placement_shuffle_cannot_hide_material_or_pin_identity_changes(selected, field, value):
    original, actual = _phone_placement_snapshots()
    row = actual[0] if selected else actual[1]
    if field == "pin_net":
        row["pins"][0]["net"] = value
    elif field == "pin_number":
        row["pins"][0]["number"] = value
    elif field == "missing_pin":
        row["pins"].pop()
    else:
        row[field] = value
    assert _placement_proof(original, actual)["valid"] is False


@pytest.mark.parametrize(
    "field,value",
    [("x", 1.0), ("y", 1.0), ("rotation", 90.0), ("placed", True), ("pin_x", 1.0)],
)
def test_placement_shuffle_cannot_hide_an_unselected_move(field, value):
    original, actual = _phone_placement_snapshots()
    if field == "pin_x":
        actual[1]["pins"][0]["x"] = value
    else:
        actual[1][field] = value
    assert _placement_proof(original, actual)["valid"] is False


def test_placement_shuffle_cannot_hide_a_missing_unselected_component():
    original, actual = _phone_placement_snapshots()
    actual.pop()
    assert _placement_proof(original, actual)["valid"] is False
