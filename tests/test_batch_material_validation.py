"""Representative complete board fixtures; no CAD, real material comparison code."""

from __future__ import annotations

import copy
import random

import pytest
from test_placement import Driver, guarded_plan, material_state

from xpedition_cli.errors import CLIError
from xpedition_cli.placement import (
    canonical_state,
    execute_placement,
    expected_state,
    plan_placement,
    validate_batch_preview,
    verify_batch_state,
)


def representative_board(count=127):
    rows = [
        {
            "refdes": f"X{n + 431}",
            "object_id": f"native-{n}",
            "x": float(n % 10 * 4),
            "y": float(n // 10 * 6),
            "rotation": 0,
            "side": "top",
            "placed": True,
            "unit": "mm",
            "anchor": 0,
            "fix_lock": 0,
        }
        for n in range(count)
    ]
    state = material_state(rows)
    for n, row in enumerate(state["components"]):
        for pin in range(3 + (n < 86)):
            center = [row["x"] + pin * 0.5, row["y"] + 1]
            net = "(Net0)" if n < 50 and pin == 0 else "SIGNAL" + str(pin)
            row["pins"].append({"number": str(pin + 1), "net": net, "x": center[0], "y": center[1]})
            row["geometry"].append(
                {
                    "number": str(pin + 1),
                    "net": net,
                    "center_mm": center,
                    "copper": [
                        {
                            "layer": 1,
                            "geometry": [
                                {
                                    "shape": "ROUND",
                                    "circle_mm": [*center, 0.2],
                                    "bounds_mm": [
                                        center[0] - 0.2,
                                        center[1] - 0.2,
                                        center[0] + 0.2,
                                        center[1] + 0.2,
                                    ],
                                    "points_mil": [[], [], []],
                                }
                            ],
                        }
                    ],
                    "fabrication": [],
                    "holes": [],
                }
            )
            state["model"]["pads"].append(
                {
                    "kind": "pad",
                    "refdes": row["refdes"],
                    "pin": str(pin + 1),
                    "net": net,
                    "layer": 1,
                    "circle": [*center, 0.2],
                }
            )
    state["model"]["traces"] = [
        {
            "uid": "old-copper",
            "net": "FOREIGN",
            "layer": 1,
            "width": 0.254,
            "path": [[60, 1, 0], [60, 90, 0]],
        }
    ]
    return rows, state


def batch_plan(selection=None):
    rows, state = representative_board()
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": selection or [r["refdes"] for r in rows[:100]],
        "steps": [{"op": "translate", "dx": 0.25, "dy": -0.125}],
    }
    return plan_placement(request, rows, protection_state=state)


def test_full_127_component_467_pin_plan_accepts_reopened_com_order_without_mutating_evidence():
    plan = batch_plan()
    assert sum(len(c["pins"]) for c in plan["protection_before"]["components"]) == 467
    expected = expected_state(plan["protection_before"], plan["results"])
    reopened = copy.deepcopy(expected)
    random.Random(47).shuffle(reopened["components"])
    for row in reopened["components"]:
        row["pins"].reverse()
        row["geometry"].reverse()
    reopened["model"]["pads"].reverse()
    original = copy.deepcopy((plan, reopened))
    assert verify_batch_state(plan, reopened)["valid"] is True
    assert (plan, reopened) == original
    validate_batch_preview(plan, plan["request"])


@pytest.mark.parametrize(
    "fault",
    [
        "same_uid",
        "missing_uid",
        "missing_component",
        "duplicate_ref",
        "missing_pin",
        "duplicate_pin",
        "geometry_pin_missing",
        "geometry_pin_duplicate",
        "part",
        "footprint",
        "net",
        "non_target_pose",
        "old_trace",
        "pad_radius",
        "pad_material",
        "stackup",
        "missing_model",
    ],
)
def test_full_board_comparison_rejects_identity_real_material_and_non_target_changes(fault):
    plan = batch_plan()
    observed = expected_state(plan["protection_before"], plan["results"])
    selected = next(
        c for c in observed["components"] if c["refdes"] == plan["request"]["selection"][0]
    )
    non_target = next(
        c for c in observed["components"] if c["refdes"] not in plan["request"]["selection"]
    )
    if fault == "same_uid":
        non_target["uid"] = selected["uid"]
    elif fault == "missing_uid":
        non_target.pop("uid")
    elif fault == "missing_component":
        observed["components"].remove(non_target)
    elif fault == "duplicate_ref":
        non_target["refdes"] = selected["refdes"]
    elif fault == "missing_pin":
        selected["pins"].pop()
    elif fault == "duplicate_pin":
        selected["pins"].append(copy.deepcopy(selected["pins"][0]))
    elif fault == "geometry_pin_missing":
        selected["geometry"].pop()
    elif fault == "geometry_pin_duplicate":
        selected["geometry"].append(copy.deepcopy(selected["geometry"][0]))
    elif fault == "part":
        selected["part_number"] = "OTHER"
    elif fault == "footprint":
        selected["footprint"] = "OTHER"
    elif fault == "net":
        selected["pins"][0]["net"] = "OTHER"
    elif fault == "non_target_pose":
        non_target["x"] += 0.00000001
    elif fault == "old_trace":
        observed["model"]["traces"][0]["width"] += 0.000001
    elif fault == "pad_radius":
        selected["geometry"][0]["copper"][0]["geometry"][0]["circle_mm"][2] += 0.00000001
    elif fault == "pad_material":
        selected["geometry"][0]["fabrication"].append({"layer": 1, "geometry": []})
    elif fault == "stackup":
        observed["stackup"].append({"layer": 1})
    elif fault == "missing_model":
        observed["model"].pop("pads")
    assert verify_batch_state(plan, observed)["valid"] is False


def test_confirmation_digest_covers_unselected_component_and_all_old_copper():
    rows, state = representative_board()
    request = batch_plan()["request"]
    bound = plan_placement(request, rows, protection_state=state)
    state["model"]["traces"][0]["net"] = "REAL_CHANGE"
    changed = plan_placement(request, rows, protection_state=state)
    assert bound["state_digest"] != changed["state_digest"]
    state["components"][-1]["part_number"] = "REPLACEMENT"
    assert (
        changed["state_digest"]
        != plan_placement(request, rows, protection_state=state)["state_digest"]
    )


def test_rotation_proves_pin_center_pad_radius_and_pose():
    rows, state = representative_board()
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": [rows[0]["refdes"]],
        "steps": [{"op": "rotate", "angle": 90, "origin": [0, 0]}],
    }
    plan = plan_placement(request, rows, protection_state=state)
    expected = expected_state(state, plan["results"])
    row = next(c for c in expected["components"] if c["refdes"] == rows[0]["refdes"])
    pin = next(p for p in row["pins"] if p["number"] == "1")
    assert pin["x"] == -1 and abs(pin["y"]) < 1e-7
    assert verify_batch_state(plan, expected)["valid"]


def test_native_pose_roundoff_does_not_conflict_with_eight_decimal_material_canonicalization():
    rows, state = representative_board()
    rows[0]["x"] = state["components"][0]["x"] = 40.199999999999996
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": [rows[0]["refdes"]],
        "steps": [{"op": "translate", "dx": 0.1, "dy": 0}],
    }
    assert plan_placement(request, rows, protection_state=state)["summary"]["changed_count"] == 1


def test_native_material_preview_refuses_unproven_nonorthogonal_text_rotation_before_write():
    rows, state = representative_board()
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": [rows[0]["refdes"]],
        "steps": [{"op": "rotate", "angle": 37, "origin": [0, 0]}],
    }
    assert plan_placement(request, rows)["summary"]["changed_count"] == 1
    with pytest.raises(CLIError, match="orthogonal") as error:
        plan_placement(request, rows, protection_state=state)
    assert error.value.code == "E_UNSUPPORTED"


class RepresentativeDriver(Driver):
    def __init__(self, *, failure_at=None, foreign_drift=False, reopened_drift=False):
        super().__init__()
        self.rows, self.material = representative_board()
        self.failure_at, self.foreign_drift, self.reopened_drift = (
            failure_at,
            foreign_drift,
            reopened_drift,
        )

    def capture_state(self):
        return copy.deepcopy(self.material)

    def move(self, target):
        old = copy.deepcopy(next(r for r in self.rows if r["refdes"] == target["refdes"]))
        self.calls.append(target["refdes"])
        if target["refdes"] == self.failure_at:
            next(r for r in self.rows if r["refdes"] == target["refdes"])["placed"] = False
            raise RuntimeError("not exposed")
        self.material = expected_state(
            self.material,
            [{"id": target["refdes"], "changed": True, "before": old, "target": target}],
        )
        self.rows[
            self.rows.index(next(r for r in self.rows if r["refdes"] == target["refdes"]))
        ] = copy.deepcopy(target)
        if self.foreign_drift:
            self.material["components"][-1]["part_number"] = "DRIFT"

    def save_reopen(self, lifecycle):
        super().save_reopen(lifecycle)
        self.material["components"].reverse()
        if self.reopened_drift:
            self.material["model"]["traces"][0]["width"] = 1


def test_representative_batch_executes_100_of_127_parts_one_lifecycle_and_all_467_pins():
    driver = RepresentativeDriver()
    request = batch_plan()["request"]
    plan = guarded_plan(request, driver)
    report = execute_placement(request, plan["state_digest"], driver)
    assert report["outcome"] == "complete" and report["persistence_verified"]
    assert (
        report["lifecycle"]["save_count"]
        == report["lifecycle"]["close_count"]
        == report["lifecycle"]["open_count"]
        == 1
    )
    assert len(report["summary"]["applied"]) == 100
    assert driver.calls.count("save") == 1
    assert sum(len(c["pins"]) for c in report["geometry_after"]["components"]) == 467


def test_partial_batch_reports_applied_uncertain_and_not_attempted_without_saving():
    driver = RepresentativeDriver(failure_at="X433")
    request = batch_plan()["request"]
    report = execute_placement(request, guarded_plan(request, driver)["state_digest"], driver)
    assert report["outcome"] == "partial_failure"
    assert report["summary"]["applied"] == ["X431", "X432"]
    assert report["summary"]["uncertain"] == ["X433"]
    assert len(report["summary"]["not_attempted"]) == 97
    assert report["results"][2]["possible_unplaced"] is True
    assert "save" not in driver.calls and "X434" not in driver.calls


@pytest.mark.parametrize("stage", ["foreign_drift", "reopened_drift"])
def test_material_drift_blocks_batch_completion_at_its_actual_stage(stage):
    driver = RepresentativeDriver(**{stage: True})
    request = batch_plan()["request"]
    report = execute_placement(request, guarded_plan(request, driver)["state_digest"], driver)
    assert report["verification"]["valid"] is False
    assert report["persistence_verified"] is False
    if stage == "foreign_drift":
        assert "save" not in driver.calls
        assert report["issues"][0]["stage"] == "full_board_protection"
    else:
        assert driver.calls.count("save") == 1
        assert report["issues"][0]["stage"] == "reopened_full_board_protection"


def test_board_capacity_is_refused_before_a_write():
    rows, state = representative_board(2049)
    with pytest.raises(CLIError, match="capacity"):
        plan_placement(
            {
                "schema_version": "1.0",
                "unit": "mm",
                "selection": [rows[0]["refdes"]],
                "steps": [{"op": "translate", "dx": 1, "dy": 0}],
            },
            rows,
            protection_state=state,
        )
    with pytest.raises(CLIError):
        canonical_state({"components": [], "model": {}, "stackup": []})
