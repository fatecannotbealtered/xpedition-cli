"""Real field receipt builder with synthetic files and real Schema semantics.

No COM, CAD, product EXE or operation store is used. Disk-delta calculation is
tested in test_native_owner_control; this file isolates the field facts contract.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_native_owner_field_rules import baseline as layout_baseline  # noqa: F401

from scripts.native_acceptance import owner_control as core
from scripts.native_acceptance import owner_field_control as field
from scripts.native_acceptance import owner_field_rules as rules


@pytest.fixture
def case(tmp_path, monkeypatch, request):
    fingerprint = request.getfixturevalue("layout_baseline")
    validator = core.ContractValidator(core.SPEC)
    directory = tmp_path / "evidence/native"
    directory.mkdir(parents=True)
    ledger = directory / "actual-stage-ledger.json"
    core.write_new(ledger, {"synthetic": True, "not_native_evidence": True})
    project = tmp_path / "owned/QA_ENTERPRISE.prj"
    board = tmp_path / "owned/PCB/QA_ENTERPRISE.pcb"
    contract = dict(
        project=str(project),
        board=str(board),
        field=dict(
            scheme="(Master)",
            net_class="(Default)",
            layer="SIGNAL_1",
            metallic_index=1,
            constraint_type=183,
            target_th=14,
            unit_enum=1,
        ),
    )
    contract_path = tmp_path / "contract.json"
    core.write_new(contract_path, contract)
    record = dict(
        operation_id="op_synthetic_field_receipt",
        plan_id="plan_synthetic_field_receipt",
        capability_id=field.CAPABILITY,
        scope_id="synthetic-field-scope",
        plan=dict(
            target=core.target(tmp_path / "owned/QA_ENTERPRISE.prj"),
            base_snapshot=core.snapshot("1" * 64),
            extensions={"contract": core.file_pin(contract_path)},
        ),
    )
    facts = {
        "Load-return": {"returned_void": True},
        "PUT-return": {"returned_void": True, "raw_target": 14.0, "unit": "th"},
        "same-object-read": {"value": 14, "is_valid": True, "proves_Layout_consumption": False},
        "UnLoad-return": {"returned_void": True},
        "consumption": {
            "consumed": True,
            "master_mm": "0.3556",
            "master_th": "14",
            "minimum_mm": "0.3048",
            "protected_named_widths": 22,
            "full_remaining_fingerprint_equal": True,
        },
        "Save-return": {"actual_return": True, "proves_reopen": False},
        "Close-after-save": {"actual_return": True, "save_argument": False},
        "persistence": {"reopened_independent_read_passed": True},
        "Close-final": {"actual_return": True, "save_argument": False},
        "Quit-return": {"returned_void": True},
    }
    consumed = deepcopy(fingerprint)
    consumed["widths"]["classes"][0]["named_master_widths"][0]["ExpansionTraceWidth"] = 0.3556
    facts["consumption"] = rules.compare_consumed(fingerprint, consumed)
    facts["after-reopen-protection"] = rules.compare_persisted(consumed, consumed)
    facts["reopen"] = {"returned_path": str(board), "authenticated_path": str(board)}
    evidence = [core.file_pin(ledger)]
    for name, data in {
        "baseline-b-independent-layout.json": fingerprint,
        "after-unload-independent-layout.json": consumed,
        "after-unload-fresh-independent-layout.json": consumed,
        "after-reopen-independent-layout.json": consumed,
        "after-reopen-fresh-independent-layout.json": consumed,
        "after-unload-physical-document-comparison.json": {
            "fingerprints_equal": True,
            "fresh_path": str(board),
        },
        "after-reopen-physical-document-comparison.json": {
            "fingerprints_equal": True,
            "fresh_path": str(board),
        },
    }.items():
        path = directory / name
        core.write_new(path, data)
        evidence.append(core.file_pin(path))
    result = dict(
        schema=field.FIELD_PROFILE.result_schema,
        operation_id=record["operation_id"],
        complete=True,
        evidence_complete=True,
        control_completed=True,
        counts=dict(field.LIMITS),
        facts=facts,
        verification={"status": "passed"},
        rules_saved=True,
        stage="field_persistence_completed",
        missing_required_evidence=[],
        required_evidence=evidence,
        known_effects=[
            dict(id="owned-field", change="Synthetic stage observations", evidence_ref=str(ledger))
        ],
    )
    disk_calls = []

    def disk(*args, **kwargs):
        disk_calls.append((args, kwargs))
        return "partial"

    monkeypatch.setattr(core, "_disk_persistence", disk)

    def build(*, protected_ok=True, sync_facts=True):
        if sync_facts:
            pins = {ref["path"]: ref for ref in result["required_evidence"]}
            for name, data in result["facts"].items():
                path = directory / ("fact-" + name + ".json")
                path.write_text(json.dumps(data), encoding="utf-8")
                ref = core.file_pin(path)
                pins[ref["path"]] = ref
            result["required_evidence"] = list(pins.values())
        path = directory / f"result-{len(list(directory.glob('result-*')))}.json"
        core.write_new(path, result)
        value = field.receipt(record, result, core.file_pin(path), protected_ok, validator)
        if isinstance(value, dict):
            validator.validate("receipt", value)
        return value

    return result, build, validator, disk_calls


def test_complete_named_field_receipt_is_saved_and_schema_semantic_valid(case):
    result, build, validator, disk_calls = case
    value = build()
    assert value["outcome"]["execution"] == "completed"
    assert value["outcome"]["change"] == "applied"
    assert value["outcome"]["persistence"] == "saved"
    assert value["outcome"]["verification"]["status"] == "passed"
    assert value["extensions"]["native_counts"] == field.LIMITS
    assert value["extensions"]["facts"] == result["facts"]
    assert value["extensions"]["product_can_apply"] is False
    assert validator.receipt_rule(value)["ok"] is True
    assert disk_calls[0][1] == {"manual_save": True, "rule_persistence": True}


def test_put_reply_loss_retains_unknown_without_inventing_zero_writes(case):
    result, build, validator, _ = case
    result.update(
        control_completed=False,
        rules_saved=False,
        stage="ValuePUT",
        verification=dict(status="incomplete", reason="Setter reply lost"),
        counts=dict.fromkeys(field.LIMITS, 0),
        facts={"Load-return": {"returned_void": True}},
    )
    result["counts"].update(Activation=1, Open=1, Load=1, ValuePUT=1)
    value = build()
    assert value["outcome"]["execution"] == "unknown"
    assert value["outcome"]["change"] == "unknown"
    assert value["extensions"]["native_counts"]["ValuePUT"] == 1
    assert value["extensions"]["native_counts"]["Save"] == 0
    assert value["extensions"]["native_stage"] == "ValuePUT"
    assert validator.receipt_rule(value)["codes"] == ["E_OUTCOME_UNKNOWN"]
    assert value["recovery"]["action"] != "retry"


@pytest.mark.parametrize(
    ("stage", "save", "close", "opened", "stopped_fact"),
    [
        ("after-unload", 0, 0, 1, {"consumed": False, "master_mm": "0.3048"}),
        ("Save", 1, 0, 1, {"actual_return": False}),
        ("Close-after-save", 1, 1, 1, {"actual_return": False, "save_argument": False}),
        ("reopen", 1, 1, 2, {"returned": False, "error_type": "SyntheticOpenError"}),
    ],
)
def test_stopped_release_save_close_reopen_preserves_exact_facts(
    case, stage, save, close, opened, stopped_fact
):
    result, build, validator, _ = case
    result.update(
        control_completed=False,
        rules_saved=False,
        stage=stage,
        verification=dict(status="incomplete", reason="Exact stopped stage"),
        counts=dict.fromkeys(field.LIMITS, 0),
    )
    result["counts"].update(
        Activation=1, Open=opened, Load=1, UnLoad=1, ValuePUT=1, Save=save, Close=close
    )
    result["facts"] = {
        "PUT-return": {"returned_void": True},
        "UnLoad-return": {"returned_void": True},
        stage: deepcopy(stopped_fact),
    }
    value = build()
    assert value["outcome"]["verification"]["status"] == "incomplete"
    assert value["outcome"]["persistence"] not in {"saved", "unsaved"}
    assert value["extensions"]["native_stage"] == stage
    assert value["extensions"]["native_counts"] == result["counts"]
    assert value["extensions"]["facts"][stage] == stopped_fact
    assert value["extensions"]["field_lifecycle_completed"] is False
    assert validator.receipt_rule(value)["codes"] == ["E_PARTIAL"]


@pytest.mark.parametrize("stage", ["Close-final", "Quit"])
def test_final_release_failure_preserves_already_proved_reopen(case, stage):
    result, build, _, _ = case
    result.update(
        control_completed=False,
        stage=stage,
        verification=dict(status="incomplete", reason="Release after persisted readback failed"),
    )
    result["counts"]["Quit"] = 0 if stage == "Close-final" else 1
    if stage == "Close-final":
        result["facts"]["Close-final"]["actual_return"] = False
    else:
        result["facts"].pop("Quit-return")
    value = build()
    assert value["outcome"]["persistence"] == "saved"
    assert value["outcome"]["verification"]["status"] == "incomplete"
    assert value["extensions"]["field_lifecycle_completed"] is False
    assert value["extensions"]["native_stage"] == stage
    assert value["extensions"]["facts"]["persistence"]["reopened_independent_read_passed"]


def test_native_incomplete_verification_and_protection_failure_never_pass(case):
    result, build, _, _ = case
    result["verification"] = dict(status="incomplete", reason="Final evidence incomplete")
    value = build()
    assert value["outcome"]["verification"]["status"] == "incomplete"
    assert value["extensions"]["field_lifecycle_completed"] is False
    result["verification"] = {"status": "passed"}
    protected_failure = build(protected_ok=False)
    assert protected_failure["outcome"]["verification"]["status"] == "failed"
    assert protected_failure["extensions"]["field_lifecycle_completed"] is False


@pytest.mark.parametrize(
    "name", ["PUT-return", "UnLoad-return", "Save-return", "Close-after-save", "Close-final"]
)
def test_complete_claim_rejects_missing_essential_actual_return(case, name):
    result, build, _, _ = case
    result["facts"].pop(name)
    assert build() is None


@pytest.mark.parametrize("name", ["Save-return", "Close-after-save", "Close-final"])
def test_complete_claim_rejects_explicit_native_false_return(case, name):
    result, build, _, _ = case
    result["facts"][name]["actual_return"] = False
    assert build() is None


def test_complete_claim_rejects_incomplete_count_and_no_op_is_not_started(case):
    result, build, _, _ = case
    result["counts"]["Close"] = 1
    assert build() is None
    result.update(control_completed=False, counts=dict.fromkeys(field.LIMITS, 0), facts={})
    assert build() == "not_started"


def _stopped_after_reopen(result):
    result.update(
        control_completed=False,
        stage="Close-final",
        verification=dict(status="incomplete", reason="Final close reply failed after readback"),
    )
    result["counts"]["Quit"] = 0
    result["facts"]["Close-final"]["actual_return"] = False


@pytest.mark.parametrize(
    "contradiction",
    [
        "Open1",
        "Save0",
        "PUT0",
        "UnLoad0",
        "missing_consumption",
        "wrong_target",
        "missing_reopen_protection",
        "not_equal_reopen_protection",
        "missing_reopen",
        "wrong_reopen_path",
    ],
)
def test_persisted_claim_is_checked_even_when_final_control_incomplete(case, contradiction):
    result, build, _, _ = case
    _stopped_after_reopen(result)
    if contradiction in {"Open1", "Save0", "PUT0", "UnLoad0"}:
        name = {"Open1": "Open", "Save0": "Save", "PUT0": "ValuePUT", "UnLoad0": "UnLoad"}[
            contradiction
        ]
        result["counts"][name] = 1 if contradiction == "Open1" else 0
    elif contradiction == "missing_consumption":
        result["facts"].pop("consumption")
    elif contradiction == "wrong_target":
        result["facts"]["consumption"]["master_mm"] = "0.3048"
    elif contradiction == "missing_reopen_protection":
        result["facts"].pop("after-reopen-protection")
    elif contradiction == "not_equal_reopen_protection":
        result["facts"]["after-reopen-protection"]["full_fingerprint_compared"] = False
    elif contradiction == "missing_reopen":
        result["facts"].pop("reopen")
    else:
        result["facts"]["reopen"]["authenticated_path"] += ".wrong-board"
    assert build() is None


@pytest.mark.parametrize(
    "fault",
    ["missing_pin", "changed_bytes", "forged_consumption", "forged_reopen", "incomplete_reopen"],
)
def test_persisted_receipt_recomputes_pinned_physical_evidence(case, fault):
    result, build, _, _ = case
    _stopped_after_reopen(result)
    # Establish honest fact artifacts first; subsequent changes deliberately
    # bypass that synthetic writer and exercise actual files and content pins.
    assert build() is not None
    name = (
        "after-unload-independent-layout.json"
        if fault == "forged_consumption"
        else "after-reopen-independent-layout.json"
    )
    ref = next(pin for pin in result["required_evidence"] if Path(pin["path"]).name == name)
    if fault == "missing_pin":
        result["required_evidence"].remove(ref)
    elif fault == "changed_bytes":
        Path(ref["path"]).write_bytes(b'{"changed":true}')
    else:
        observed = core.read_json(ref["path"])
        if fault == "incomplete_reopen":
            observed["widths"]["complete"] = False
        else:
            observed["widths"]["classes"][0]["named_master_widths"][0]["ExpansionTraceWidth"] = (
                0.3048
            )
        Path(ref["path"]).write_text(json.dumps(observed), encoding="utf-8")
        # A fresh valid pin cannot turn a contradictory physical observation
        # into the claimed 14th/unchanged protected fingerprint.
        ref.update(core.file_pin(ref["path"]))
    assert build(sync_facts=False) is None
