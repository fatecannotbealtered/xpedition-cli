from __future__ import annotations

import copy
import json
import random

import pytest

from xpedition_cli.errors import CLIError
from xpedition_cli.placement import (
    execute_placement, input_schema, plan_placement, read_json, same_position, validate_request,
)


def observations():
    return [
        {"refdes": name, "object_id": str(i), "x": x, "y": y, "rotation": 0,
         "side": "bottom" if i == 1 else "top", "placed": True, "unit": "mm", "anchor": 0, "fix_lock": 0}
        for i, (name, x, y) in enumerate((("R1", 0, 0), ("R2", 10, 5), ("R3", 30, 8)))
    ]


def task(*steps):
    return {"schema_version": "1.0", "unit": "mm", "selection": ["R1", "R2", "R3"],
            "steps": list(steps) or [{"op": "translate", "dx": 1, "dy": -2}]}


def test_translate_keeps_sides_identities_order_and_source():
    source = observations()
    before = copy.deepcopy(source)
    result = plan_placement(task(), source)
    assert [r["target"]["x"] for r in result["results"]] == [1, 11, 31]
    assert [r["target"]["y"] for r in result["results"]] == [-2, 3, 6]
    assert result["results"][1]["target"]["side"] == "bottom"
    assert result["summary"] == {"selected_count": 3, "changed_count": 3}
    assert source == before
    assert result["validation"]["drc"] == "not_run"


def test_rotation_is_about_explicit_origin_in_board_coordinates():
    plan = plan_placement(task({"op": "rotate", "angle": 90, "origin": [10, 5]}), observations())
    assert [(r["target"]["x"], r["target"]["y"]) for r in plan["results"]] == [(15, -5), (10, 5), (7, 25)]
    assert all(r["target"]["rotation"] == 90 for r in plan["results"])
    assert plan["results"][1]["target"]["side"] == "bottom"


def test_align_and_distribute_follow_explicit_selection_not_refdes_sort():
    value = task({"op": "align", "axis": "y", "anchor": "R2"},
                 {"op": "distribute", "axis": "x", "start": 25, "end": 5})
    value["selection"] = ["R3", "R1", "R2"]
    result = plan_placement(value, observations())
    assert [r["id"] for r in result["results"]] == value["selection"]
    assert [r["target"]["x"] for r in result["results"]] == [25, 15, 5]
    assert [r["target"]["y"] for r in result["results"]] == [5, 5, 5]


@pytest.mark.parametrize("state_key,state_value", [("anchor", 1), ("anchor", 2), ("anchor", 3), ("fix_lock", 4)])
def test_every_observed_protection_blocks_changed_parts(state_key, state_value):
    source = observations()
    source[1][state_key] = state_value
    with pytest.raises(CLIError) as error:
        plan_placement(task(), source)
    assert error.value.code == "E_CONFLICT"
    # An unchanged protected anchor is allowed; other parts align to its y.
    result = plan_placement(task({"op": "align", "axis": "y", "anchor": "R2"}), source)
    assert not result["results"][1]["changed"]


@pytest.mark.parametrize("bad", [True, "1", None, float("nan"), float("inf"), 10**1000])
def test_bad_numbers_are_rejected(bad):
    with pytest.raises(CLIError):
        validate_request(task({"op": "translate", "dx": bad, "dy": 0}))


@pytest.mark.parametrize("mutation", [
    lambda t: t.update(extra=True), lambda t: t.update(unit="mil"),
    lambda t: t.update(selection=["R1", "R1"]), lambda t: t.update(selection=[]),
    lambda t: t.update(selection=[" R1"]), lambda t: t.update(steps=[]),
    lambda t: t["steps"][0].update(extra=True), lambda t: t["steps"][0].update(op="delete"),
    lambda t: t.update(steps=[{"op": "align", "axis": "x", "anchor": "missing"}]),
    lambda t: t.update(selection=["R1"], steps=[{"op": "distribute", "axis": "x", "start": 0, "end": 1}]),
])
def test_invalid_requests_fail_without_a_plan(mutation):
    value = task()
    mutation(value)
    with pytest.raises(CLIError):
        plan_placement(value, observations())


@pytest.mark.parametrize("field", ["x", "y", "rotation", "unit", "side", "placed", "anchor", "fix_lock", "object_id"])
def test_missing_observation_is_not_defaulted(field):
    source = observations()
    del source[1][field]
    with pytest.raises(CLIError):
        plan_placement(task(), source)


def test_duplicate_or_missing_observations_are_errors():
    for source in (observations()[:2], observations() + [observations()[0]]):
        with pytest.raises(CLIError):
            plan_placement(task(), source)


def test_roundtrip_transform_for_seeded_coordinates():
    rng = random.Random(2186)
    for _ in range(100):
        source = observations()
        for row in source:
            row.update(x=rng.uniform(-100, 100), y=rng.uniform(-100, 100), rotation=rng.uniform(0, 360))
        angle = rng.uniform(-720, 720)
        request = task({"op": "rotate", "angle": angle, "origin": [2, -8]},
                       {"op": "rotate", "angle": -angle, "origin": [2, -8]})
        result = plan_placement(request, source)
        assert all(same_position(old, row["target"]) for old, row in zip(source, result["results"], strict=True))
        assert result["summary"]["changed_count"] == 0


def test_schema_is_generated_for_all_operations():
    choices = input_schema()["properties"]["steps"]["items"]["oneOf"]
    assert {choice["properties"]["op"]["const"] for choice in choices} == {"translate", "rotate", "align", "distribute"}
    assert all(choice["additionalProperties"] is False for choice in choices)


@pytest.mark.parametrize("content", [b'{"x":1,"x":2}', b"\xff", b"[", b" " * 1048577])
def test_input_file_errors_are_structured(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_bytes(content)
    with pytest.raises(CLIError):
        read_json(path)


def test_bom_utf8_file_is_accepted(tmp_path):
    path = tmp_path / "task.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(task()).encode())
    assert validate_request(read_json(path)) == validate_request(task())


class Driver:
    def __init__(self, mode="normal"):
        self.rows = observations()
        self.mode = mode
        self.calls = []
        self.drc = False

    def observe(self, selection):
        return copy.deepcopy([row for row in self.rows if row["refdes"] in selection])

    def enable_drc(self):
        self.calls.append("enable")
        self.drc = True
        return False

    def restore_drc(self, previous):
        self.calls.append("restore")
        if self.mode == "restore_failure":
            raise RuntimeError("private error")
        self.drc = previous

    def move(self, target):
        assert self.drc
        self.calls.append(target["refdes"])
        if self.mode == "partial_failure" and target["refdes"] == "R2":
            self.rows[1]["placed"] = False
            raise RuntimeError("private error")
        if self.mode != "swallowed":
            self.rows[self.rows.index(next(r for r in self.rows if r["refdes"] == target["refdes"]))] = copy.deepcopy(target)

    def save(self):
        self.calls.append("save")
        if self.mode == "save_failure":
            raise RuntimeError("private error")


def test_execute_verifies_every_target_and_saves_once():
    driver = Driver()
    plan = plan_placement(task(), driver.rows)
    result = execute_placement(task(), plan["state_digest"], driver)
    assert result["outcome"] == "complete" and result["saved"]
    assert result["verification"]["valid"] and result["summary"]["ok_count"] == 3
    assert driver.calls == ["enable", "R1", "R2", "R3", "restore", "save"]
    assert not driver.drc


@pytest.mark.parametrize("mode,outcome", [("partial_failure", "partial_failure"), ("swallowed", "partial_failure"),
                                           ("restore_failure", "partial_failure"), ("save_failure", "save_unknown")])
def test_failures_never_fake_complete_or_blindly_rollback(mode, outcome):
    driver = Driver(mode)
    result = execute_placement(task(), plan_placement(task(), driver.rows)["state_digest"], driver)
    assert result["outcome"] == outcome
    assert result["saved"] is not True
    assert "private error" not in json.dumps(result)
    if mode == "partial_failure":
        assert result["results"][0]["status"] == "verified"
        assert result["results"][1]["status"] == "outcome_unknown"
        assert result["results"][2]["status"] == "not_attempted"
        assert "R3" not in driver.calls and "save" not in driver.calls
    if mode == "swallowed":
        assert not result["verification"]["valid"] and "R2" not in driver.calls


def test_stale_identity_fails_before_any_drc_or_move():
    driver = Driver()
    plan = plan_placement(task(), driver.rows)
    driver.rows[1]["object_id"] = "replacement"
    with pytest.raises(CLIError) as error:
        execute_placement(task(), plan["state_digest"], driver)
    assert error.value.code == "E_CONFLICT"
    assert error.value.details["write_attempted"] is False
    assert not driver.calls


def test_noop_does_not_change_drc_or_save():
    driver = Driver()
    value = task({"op": "translate", "dx": 0, "dy": 0})
    result = execute_placement(value, plan_placement(value, driver.rows)["state_digest"], driver)
    assert result["outcome"] == "complete" and not result["write_attempted"]
    assert not driver.calls and result["verification"]["valid"]
