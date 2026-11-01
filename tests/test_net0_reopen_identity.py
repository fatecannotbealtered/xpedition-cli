"""Whole-board Net0 references are equivalent only across a proven native reopen."""

from __future__ import annotations

import copy
import json

import pytest
from test_batch_driver import NativeBoundary
from test_batch_material_validation import RepresentativeDriver, representative_board
from test_driver_v2 import _scope

from xpedition_cli import native_process
from xpedition_cli.cli import common
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError
from xpedition_cli.net0_reopen_identity import pin_reference_projector, prove_net0_reopen
from xpedition_cli.placement import (
    BATCH_GUARD,
    _batch_net_references,
    batch_reopen_comparison,
    batch_reopen_lifecycle,
    expected_state,
    plan_placement,
    validate_batch_preview,
    verify_batch_state,
)

OLD = "31997248657162249"
NEW = "31997248657162252"


def identity(pid=4141):
    return {
        "pid": pid,
        "creation_time_100ns": "12345678901",
        "executable": "C:/cad/expeditionpcb.exe",
    }


def lifecycle(path="C:/trial/test.pcb"):
    return {
        "save_count": 1,
        "close_count": 1,
        "open_count": 1,
        **{
            key: True
            for key in (
                "save_acknowledged",
                "save_reply_received",
                "save_returned",
                "close_acknowledged",
                "close_reply_received",
                "close_returned",
                "closed_observed",
                "open_reply_received",
                "reopen_verified",
            )
        },
        "save_return_type": "bool",
        "close_return_type": "bool",
        "pid_before": 4141,
        "pid_after": 4141,
        "board": path,
        "reopened_path": path,
        "process_identity_before": identity(),
        "process_identity_after": identity(),
    }


def board():
    rows, state = representative_board()
    state["model"]["traces"] = []
    nets = [{"net": f"N{n}", "uid": f"network-{n}", "opens": 0} for n in range(84)]
    names = {}
    ordinal = 0
    for component in state["components"]:
        by_number = {row["number"]: row for row in component["geometry"]}
        for pin in component["pins"]:
            is_default = pin["net"] == "(Net0)"
            name = "(Net0)" if is_default else nets[ordinal % 84]["net"]
            uid = OLD if is_default else nets[ordinal % 84]["uid"]
            ordinal += not is_default
            pin.update(uid=f"physical-{len(names)}", net=name, net_uid=uid)
            by_number[pin["number"]].update(uid=pin["uid"], net=name, net_uid=uid)
            names[(component["refdes"], pin["number"])] = name
    for pad in state["model"]["pads"]:
        pad["net"] = names[(pad["refdes"], pad["pin"])]
    state["model"]["connectivity"] = {"complete": True, "nets": nets}
    return rows, state


def rebuilt_default(state):
    result = copy.deepcopy(state)
    for component in result["components"]:
        for key in ("pins", "geometry"):
            for pin in component[key]:
                if pin["net"] == "(Net0)":
                    pin["net_uid"] = NEW
    return result


def proof_pair():
    _, before = board()
    after = rebuilt_default(before)
    bn, bp = _batch_net_references(before)
    an, ap = _batch_net_references(after)
    return before, after, bn, bp, an, ap


def test_full_467_pin_50_default_84_named_reopen_projection_retains_raw_evidence():
    before, after, bn, bp, an, ap = proof_pair()
    unchanged = copy.deepcopy((before, after))
    proof = prove_net0_reopen(bn, bp, an, ap, lifecycle=batch_reopen_lifecycle(lifecycle()))
    assert (
        proof["physical_pin_count"],
        proof["default_member_count"],
        proof["named_net_count"],
    ) == (467, 50, 84)
    project = pin_reference_projector(proof, "after")
    assert [project(row) for row in ap] == bp
    assert before != after
    transition = batch_reopen_comparison(before, after, lifecycle())
    assert transition["valid"] and transition["reference_equivalence"] == proof
    assert transition["raw_before_sha256"] != transition["raw_after_sha256"]
    assert transition["raw_before_sha256"] == transition["comparison_after_sha256"]
    assert (before, after) == unchanged


@pytest.mark.parametrize(
    "fault",
    [
        "missing_life",
        "no_close",
        "no_empty",
        "no_document",
        "wrong_path",
        "pid_reuse",
        "missing_birth",
        "other_executable",
        "count_bool",
        "save_ack_only",
        "mixed_default",
        "named_uid",
        "named_census",
        "removed_pin",
        "replaced_pin",
        "reassigned_pin",
    ],
)
def test_ambiguous_identity_or_unproven_lifecycle_never_grants_net0_equivalence(fault):
    before, after, bn, bp, an, ap = proof_pair()
    life = batch_reopen_lifecycle(lifecycle())
    if fault == "missing_life":
        life = None
    elif fault == "no_close":
        life["close_count"] = 0
    elif fault == "no_empty":
        life["closed_observed"] = False
    elif fault == "no_document":
        life["open_returned_document"] = False
    elif fault == "wrong_path":
        life["reopened_path"] = "/other.pcb"
    elif fault == "pid_reuse":
        life["process_after"]["creation_time_100ns"] = "12345678902"
    elif fault == "missing_birth":
        life["process_before"].pop("creation_time_100ns")
    elif fault == "other_executable":
        life["process_after"]["executable"] = "/other/app.exe"
    elif fault == "count_bool":
        life["open_count"] = True
    elif fault == "save_ack_only":
        life["save_returned"] = None
    elif fault == "mixed_default":
        next(row for row in ap if row["net"] == "(Net0)")["net_uid"] = "different-default"
    elif fault == "named_uid":
        next(row for row in ap if row["net"] != "(Net0)")["net_uid"] = "different-network"
    elif fault == "named_census":
        an[0]["uid"] = "different-network"
    elif fault == "removed_pin":
        ap.pop()
    elif fault == "replaced_pin":
        ap[0]["pin_uid"] = "replacement"
    else:
        next(row for row in ap if row["net"] == "(Net0)")["net"] = an[0]["net"]
    with pytest.raises((KeyError, ValueError)):
        prove_net0_reopen(bn, bp, an, ap, lifecycle=life)


@pytest.mark.parametrize("fault", ["foreign_coordinate", "mirror_uid", "pad_size", "stackup"])
def test_reopen_exception_never_hides_other_native_material_differences(fault):
    _, before = board()
    after = rebuilt_default(before)
    if fault == "foreign_coordinate":
        after["components"][-1]["x"] += 1e-7
    elif fault == "mirror_uid":
        after["components"][0]["geometry"][0]["uid"] = "different"
    elif fault == "pad_size":
        after["model"]["pads"][0]["circle"][2] += 1e-7
    else:
        after["stackup"].append({"actual": "changed"})
    try:
        comparison = batch_reopen_comparison(before, after, lifecycle())
    except (KeyError, ValueError):
        return
    assert comparison["valid"] is False


def test_batch_preconfirm_stays_exact_and_old_plans_are_rejected():
    rows, before = board()
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": [rows[0]["refdes"]],
        "steps": [{"op": "translate", "dx": 0.2, "dy": 0}],
    }
    plan = plan_placement(request, rows, protection_state=before)
    after_edit = expected_state(before, plan["results"])
    reopened = rebuilt_default(after_edit)
    assert not verify_batch_state(plan, reopened)["valid"]
    transition = batch_reopen_comparison(after_edit, reopened, lifecycle())
    assert verify_batch_state(plan, reopened, reopen_proof=transition["reference_equivalence"])[
        "valid"
    ]
    for guard in ("placement-full-board-material-v3", "placement-full-board-material-v4"):
        old = copy.deepcopy(plan)
        old["state_guard"] = guard
        with pytest.raises(CLIError):
            validate_batch_preview(old, old["request"])
    assert plan["state_guard"] == BATCH_GUARD == "placement-full-board-material-v5"


def test_noop_or_unbound_application_cannot_reuse_another_reopen_proof():
    rows, before = board()
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": [rows[0]["refdes"]],
        "steps": [{"op": "translate", "dx": 0, "dy": 0}],
    }
    plan = plan_placement(request, rows, protection_state=before)
    reopened = rebuilt_default(before)
    transition = batch_reopen_comparison(before, reopened, lifecycle())
    assert not verify_batch_state(plan, reopened, reopen_proof=transition["reference_equivalence"])[
        "valid"
    ]
    unbound = lifecycle()
    unbound["pid_after"] = 4142
    with pytest.raises(ValueError):
        batch_reopen_comparison(before, reopened, unbound)


def test_net0_change_between_public_preview_and_confirm_never_dispatches(tmp_path, monkeypatch):
    _scope(tmp_path, monkeypatch)
    target = tmp_path / "same-open-drift.pcb"
    target.write_bytes(b"synthetic native receiver")
    native = ReopenBoundary(target, "success")
    monkeypatch.setattr(common, "NativeBackend", lambda: native)
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": [native.driver.rows[0]["refdes"]],
        "steps": [{"op": "translate", "dx": 0.2, "dy": 0}],
    }
    source = tmp_path / "batch.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    args = ["pcb", "placement", "move", "--project", str(target), "--file", str(source)]
    with runtime(Adapter(native), state_dir=tmp_path / "operations") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"]
        native.driver.material = rebuilt_default(native.driver.material)
        confirmed = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not confirmed.envelope["ok"]
    assert not any(method == "placement_batch" and p["apply"] for method, p in native.calls)
    assert not native.driver.calls


class RebuiltDefaultDriver(RepresentativeDriver):
    def __init__(self, path="C:/trial/test.pcb", forged=False):
        super().__init__()
        self.rows, self.material = board()
        self.path, self.forged = path, forged

    def save_reopen(self, observed):
        super().save_reopen(observed)
        observed.update(lifecycle(self.path))
        self.material = rebuilt_default(self.material)
        if self.forged:
            observed["process_identity_after"]["creation_time_100ns"] = "999"


class ReopenBoundary(NativeBoundary):
    def __init__(self, board, mode):
        super().__init__(board, "success")
        self.driver = RebuiltDefaultDriver(str(board), forged=mode == "bad_birth")
        self.mode = mode


@pytest.mark.parametrize("mode", ["success", "bad_birth", "sdk_drift", "forged_proof", "bad_os"])
def test_real_sdk_recomputes_reopen_exception_and_never_replays(tmp_path, monkeypatch, mode):
    _scope(tmp_path, monkeypatch)
    target = tmp_path / "holdout-rebuilt-default.pcb"
    target.write_bytes(b"synthetic native receiver")
    native = ReopenBoundary(target, mode)
    original = native.invoke

    def invoke(method, params, **kwargs):
        result = original(method, params, **kwargs)
        if method == "cell_refresh_state" and "save" in native.driver.calls and mode == "sdk_drift":
            result["geometry"]["components"][-1]["part_number"] = "REAL_FOREIGN_CHANGE"
        if method == "placement_batch" and params["apply"] and mode == "forged_proof":
            result["reopen_comparison"]["reference_equivalence"]["old_uid"] = "FORGED_REFERENCE"
        return result

    monkeypatch.setattr(native, "invoke", invoke)
    monkeypatch.setattr(common, "NativeBackend", lambda: native)
    actual_identity = identity()
    if mode == "bad_os":
        actual_identity["creation_time_100ns"] = "999"
    monkeypatch.setattr(
        native_process, "process_identity", lambda *_: copy.deepcopy(actual_identity)
    )
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": [r["refdes"] for r in native.driver.rows[:6]],
        "steps": [{"op": "translate", "dx": 0.2, "dy": 0}],
    }
    source = tmp_path / "batch.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    args = ["pcb", "placement", "move", "--project", str(target), "--file", str(source)]
    with runtime(Adapter(native), state_dir=tmp_path / "operations") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        token = preview.envelope["data"]["confirm_token"]
        applied = cli.run([*args, "--confirm", token])
        replay = cli.run([*args, "--confirm", token])
    assert not replay.envelope["ok"]
    assert sum(method == "placement_batch" and p["apply"] for method, p in native.calls) == 1
    assert applied.envelope["ok"] is (mode == "success"), applied.envelope
    if mode == "success":
        result = applied.envelope["data"]["receipt"]
        assert result["outcome"]["persistence"] == "saved"
        assert result["extensions"]["persistence_independently_reopened"] is True
