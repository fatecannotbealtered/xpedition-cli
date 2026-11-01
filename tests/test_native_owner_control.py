"""External owner control: synthetic children and private temporary SDK stores.

No CAD, COM, real operation store or product EXE is used by these tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.native_acceptance import owner_control as control


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return control.file_pin(path)


@pytest.fixture
def contract(tmp_path, monkeypatch):
    method = tmp_path / "method"
    method.mkdir()
    monkeypatch.setattr(control, "__file__", str(method / "owner_control.py"))
    native = method / "owner_lifecycle.py"
    native.write_text("# Synthetic compile-only entry. Never run.\n", encoding="utf-8")
    home, work = tmp_path / "SDD_HOME", tmp_path / "wdir"
    home.mkdir()
    work.mkdir()
    owned, protected = tmp_path / "owned", tmp_path / "protected"
    owned.mkdir()
    protected.mkdir()
    (owned / "board.pcb").write_bytes(b"board input")
    (owned / "test.prj").write_bytes(b"project input")
    (protected / "keep.txt").write_bytes(b"keep")
    output = tmp_path / "evidence"
    scope = write(
        tmp_path / "scope.json",
        dict(
            scope_id="synthetic-owned-control",
            tool="xpedition-cli",
            granted_by="test owner",
            granted_at="2026-01-01T00:00:00Z",
            expires_at=None,
            effects=control.EFFECTS,
            dangerous=False,
            roots=[str(tmp_path)],
            outputs=[str(tmp_path)],
            limits={"max_operations": 5},
        ),
    )
    declared = dict(
        XPEDITION_CLI_CONFIG_DIR=str(tmp_path / "state"),
        XPEDITION_CLI_SCOPE_FILE=scope["path"],
        XPEDITION_SDD_HOME=str(home),
        XPEDITION_MGC_WD=str(work),
        TEMP=str(tmp_path),
        TMP=str(tmp_path),
    )
    for key, value in declared.items():
        monkeypatch.setenv(key, value)
    # Avoid inherited workstation-specific derived variables in this isolated fixture.
    for key in (
        "SDD_HOME",
        "SDD_PLATFORM",
        "SDD_VERSION",
        "SDD_WG",
        "SDD_WV",
        "SDD_DX",
        "VCO",
        "TARGET",
        "MENTOR_ROOT",
        "MGC_WD",
        "WDIR",
        "MGC_TMPDIR",
        "MGC_HOME",
        "SDD_MGC_HOME",
    ):
        monkeypatch.delenv(key, raising=False)
    env = write(tmp_path / "environment.json", declared)
    inventory = write(
        tmp_path / "protected-inventory.json",
        dict(
            root=str(protected),
            complete=True,
            files=[control.file_pin(protected / "keep.txt")],
            errors=[],
        ),
    )
    closed_inventory = write(
        tmp_path / "closed-input-inventory.json",
        dict(
            root=str(owned),
            complete=True,
            files=[
                dict(control.file_pin(owned / name), path=name)
                for name in ("test.prj", "board.pcb")
            ],
            errors=[],
        ),
    )
    contract = dict(
        project=str(owned / "test.prj"),
        board=str(owned / "board.pcb"),
        board_name="Board1",
        output=str(output),
        journal=str(tmp_path / "state/driver-v2"),
        environment=env,
        scope=scope,
        native_entry=control.file_pin(native),
        source_pins=[control.file_pin(native)],
        file_pins=[control.file_pin(owned / name) for name in ("test.prj", "board.pcb")],
        protected_inventories=[inventory],
        closed_input_inventory=closed_inventory,
        limits=control.LIMITS,
        max_wait_seconds=1,
    )
    path = tmp_path / "contract.json"
    write(path, contract)
    return path, contract


def row(contract, operation):
    store = control.StateStore(contract["journal"])
    try:
        return store.get(operation)
    finally:
        store.close()


def planned(contract_path):
    code, value = control.plan(contract_path)
    assert code == 0
    return value["data"]["confirm_token"], value["data"]["preview"]["operation_id"]


def fake_child(
    monkeypatch, *, mode="complete", mutate=None, evidence_missing=False, disk_mode="changed"
):
    calls = []

    class Child:
        pid = 424242
        returncode = 0

        def poll(self):
            return None if mode == "timeout" else self.returncode

    def start(argv, **kwargs):
        calls.append((argv, kwargs))
        if mode == "start_failure":
            raise OSError("Synthetic executable creation failure")
        assert "--request" in argv and kwargs["stdin"] == control.subprocess.DEVNULL
        request_path = Path(argv[argv.index("--request") + 1])
        request = control.read_json(request_path)
        assert request["conflict_exclude"] == []
        assert request["effective_journal"] == request["contract"]["journal"]
        output = Path(request["contract"]["output"])
        if mode in {"reply_loss", "timeout"}:
            return Child()
        if mode == "early":
            marker = dict(
                schema="owned-control-early-failure/v1",
                operation_id=request["operation_id"],
                request_sha256=control.file_pin(request_path)["sha256"],
                native_dispatch_state="not_started",
                counts={name: 0 for name in control.LIMITS},
                error={"type": "ValueError", "message": "Synthetic pre-CAD validation failure"},
            )
            if mutate:
                mutate(marker, request)
            kwargs["stderr"].write(b"synthetic prior diagnostic\n")
            kwargs["stderr"].write(json.dumps(marker).encode("utf-8") + b"\n")
            return Child()
        evidence = write(
            output / "native/observations.json", {"synthetic": True, "actual_native_calls": False}
        )
        counts = {name: 0 for name in control.LIMITS}
        if mode != "zero":
            counts.update(
                {name: 1 for name in ("Activation", "Open", "Load", "UnLoad", "Close", "Quit")}
            )
        result = dict(
            schema="owned-native-control-result/v1",
            operation_id=request["operation_id"],
            request_sha256=control.file_pin(request_path)["sha256"],
            complete=True,
            evidence_complete=True,
            control_completed=mode == "complete",
            stage="control_completed" if mode == "complete" else "dependency_preflight",
            counts=counts,
            known_effects=[
                dict(
                    id="session",
                    change="Synthetic bounded session control",
                    evidence_ref=evidence["path"],
                )
            ],
            required_evidence=[] if evidence_missing else [evidence],
            missing_required_evidence=[],
            verification={"status": "passed"},
            rules_saved=False,
        )
        if mode != "zero" and disk_mode != "missing":
            before_pin = request["contract"]["closed_input_inventory"]
            before = control.read_json(before_pin["path"])
            after = json.loads(json.dumps(before))
            if disk_mode == "changed":
                aux = Path(before["root"]) / "native-auxiliary.dat"
                aux.write_bytes(b"synthetic native auxiliary effect")
                after["files"].append(dict(control.file_pin(aux), path=aux.name))
            elif disk_mode == "incomplete":
                after["files"] = [row for row in after["files"] if row["path"] != "board.pcb"]
                after.update(
                    complete=False,
                    errors=[dict(path="board.pcb", type="PermissionError", errno=13)],
                )
            after_pin = write(output / "native/F-after-control-files.json", after)
            changed = disk_mode == "changed"
            delta_pin = write(
                output / "native/F-disk-delta.json",
                dict(
                    added=["native-auxiliary.dat"] if changed else [],
                    removed=[],
                    changed=[],
                    unobserved=["board.pcb"] if disk_mode == "incomplete" else [],
                    complete=disk_mode != "incomplete",
                    manual_Save_called=False,
                    rule_persistence_proven=False,
                ),
            )
            result["disk_observation"] = dict(
                before=before_pin,
                after=after_pin,
                delta=delta_pin,
                changes_observed=changed,
                coverage_complete=disk_mode != "incomplete",
            )
            result["required_evidence"].extend([after_pin, delta_pin])
        if evidence_missing:
            result["required_evidence"] = []
        if mutate:
            mutate(result, request)
        write(output / "native/result.json", result)
        return Child()

    monkeypatch.setattr(control.subprocess, "Popen", start)
    return calls


def test_dryrun_uses_sdk_plan_without_child_or_native_import(contract, monkeypatch):
    path, _ = contract
    fake = fake_child(monkeypatch)
    code, value = control.plan(path)
    assert code == 0 and value["data"]["preview"]["can_apply"]
    control.ContractValidator(control.SPEC).validate("response", value)
    assert fake == []
    assert not (Path(contract[1]["output"]) / "control").exists()


def test_missing_dependency_fails_before_plan_record(contract, monkeypatch):
    path, data = contract
    monkeypatch.setattr(control.tokens, "issue", None)
    with pytest.raises(control.OwnerPreflightError, match="runtime callable"):
        control.plan(path)
    assert not Path(data["journal"]).exists()


def test_missing_child_syntax_dependency_rejected(contract):
    path, data = contract
    Path(data["native_entry"]["path"]).unlink()
    with pytest.raises(control.OwnerPreflightError, match="Critical input unreadable"):
        control.plan(path)
    assert not Path(data["journal"]).exists()


def test_known_complete_control_yields_valid_partial_receipt(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    calls = fake_child(monkeypatch)
    code, value = control.confirm(path, token)
    assert code == 1 and value["error"]["code"] == "E_PARTIAL"
    receipt = value["error"]["details"]["receipt"]
    control.ContractValidator(control.SPEC).validate("receipt", receipt)
    assert receipt["outcome"]["execution"] == "completed"
    assert receipt["outcome"]["persistence"] == "partial"
    assert receipt["outcome"]["delivery"] == "not_applicable"
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert len(calls) == 1
    record = row(data, operation)
    assert record["status"] == "partial" and record["receipt"] == receipt
    assert record["resolution"] is None


@pytest.mark.parametrize(
    ("disk_mode", "expected_persistence", "expected_code"),
    [("changed", "partial", 1), ("unchanged", "unchanged", 0), ("incomplete", "unknown", 6)],
)
def test_persistence_is_derived_from_actual_disk_observation(
    contract, monkeypatch, disk_mode, expected_persistence, expected_code
):
    path, data = contract
    token, operation = planned(path)
    fake_child(monkeypatch, disk_mode=disk_mode)
    code, value = control.confirm(path, token)
    assert code == expected_code
    receipt = row(data, operation)["receipt"]
    assert receipt["outcome"]["persistence"] == expected_persistence
    assert receipt["extensions"]["rules_saved"] is False
    control.ContractValidator(control.SPEC).validate("receipt", receipt)
    if expected_code:
        assert value["error"]["code"] == (
            "E_PARTIAL" if expected_persistence == "partial" else "E_OUTCOME_UNKNOWN"
        )


def test_completed_native_without_disk_observation_cannot_claim_partial(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    fake_child(monkeypatch, disk_mode="missing")
    code, value = control.confirm(path, token)
    assert code == 6 and value["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert row(data, operation)["receipt"] is None


@pytest.mark.parametrize("use_field_flags", [False, True])
def test_field_disk_delta_flags_require_explicit_profile_arguments(contract, use_field_flags):
    _, data = contract
    before_pin = data["closed_input_inventory"]
    before = control.read_json(before_pin["path"])
    output = Path(data["output"])
    auxiliary = Path(before["root"]) / "field-auxiliary.dat"
    auxiliary.write_bytes(b"synthetic field persistence")
    after = json.loads(json.dumps(before))
    after["files"].append(dict(control.file_pin(auxiliary), path=auxiliary.name))
    after_pin = write(output / "native/F-after-field-files.json", after)
    delta_pin = write(
        output / "native/F-field-delta.json",
        dict(
            added=[auxiliary.name],
            removed=[],
            changed=[],
            unobserved=[],
            complete=True,
            manual_Save_called=True,
            rule_persistence_proven=True,
        ),
    )
    result = dict(
        disk_observation=dict(
            before=before_pin,
            after=after_pin,
            delta=delta_pin,
            changes_observed=True,
            coverage_complete=True,
        )
    )
    record = {"plan": {"extensions": {"closed_input_inventory": before_pin}}}
    arguments = {"manual_save": True, "rule_persistence": True} if use_field_flags else {}
    actual = control._disk_persistence(record, result, output, [after_pin, delta_pin], **arguments)
    assert actual == ("partial" if use_field_flags else None)


def test_start_failure_is_failed_not_started_token_stays_used(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    calls = fake_child(monkeypatch, mode="start_failure")
    code, value = control.confirm(path, token)
    assert code == 1 and value["error"]["code"] == "E_IO"
    record = row(data, operation)
    assert record["status"] == "failed" and record["receipt"] is None
    assert record["extra"]["dispatch_state"] == "not_started"
    assert record["extra"]["token_remains_consumed"]
    with pytest.raises(control.ControlError, match="already consumed"):
        control.confirm(path, token)
    assert len(calls) == 1


def test_child_known_zero_native_failure_is_not_unknown(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    fake_child(monkeypatch, mode="zero")
    code, value = control.confirm(path, token)
    assert code == 1 and value["error"]["code"] == "E_NATIVE_FAILURE"
    record = row(data, operation)
    assert record["status"] == "failed" and record["receipt"] is None
    assert record["extra"]["dispatch_state"] == "not_started"
    assert all(v == 0 for v in record["extra"]["native_counts"].values())


def test_reply_loss_unknown_and_duplicate_confirmation_never_replays(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    calls = fake_child(monkeypatch, mode="reply_loss")
    code, value = control.confirm(path, token)
    assert code == 6 and value["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert row(data, operation)["status"] == "unknown"
    with pytest.raises(control.ControlError, match="already consumed"):
        control.confirm(path, token)
    assert len(calls) == 1


def test_bound_early_failure_marker_is_known_not_started(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    calls = fake_child(monkeypatch, mode="early")
    code, value = control.confirm(path, token)
    assert code == 1 and value["error"]["code"] == "E_NATIVE_FAILURE"
    record = row(data, operation)
    assert record["status"] == "failed" and record["receipt"] is None
    assert record["extra"]["dispatch_state"] == "not_started"
    assert record["extra"]["no_native_call"] is True
    assert record["extra"]["early_failure"]["stderr"]["sha256"]
    assert record["extra"]["token_remains_consumed"] is True
    with pytest.raises(control.ControlError, match="already consumed"):
        control.confirm(path, token)
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["wrong_op", "wrong_sha", "unknown", "source_drift"])
def test_incomplete_or_unbound_early_marker_remains_unknown(contract, monkeypatch, case):
    path, data = contract
    token, operation = planned(path)

    def mutate(marker, request):
        if case == "wrong_op":
            marker["operation_id"] = "another-operation"
        elif case == "wrong_sha":
            marker["request_sha256"] = "0" * 64
        elif case == "unknown":
            marker["native_dispatch_state"] = "unknown"
        else:
            Path(request["contract"]["native_entry"]["path"]).write_bytes(b"# changed\n")

    fake_child(monkeypatch, mode="early", mutate=mutate)
    code, value = control.confirm(path, token)
    assert code == 6 and value["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert row(data, operation)["status"] == "unknown"
    assert row(data, operation)["receipt"] is None


def test_timeout_keeps_child_and_marks_unknown(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    calls = fake_child(monkeypatch, mode="timeout")
    code, value = control.confirm(path, token)
    assert code == 6 and value["error"]["code"] == "E_OUTCOME_UNKNOWN"
    record = row(data, operation)
    assert record["status"] == "unknown" and record["extra"]["child_exited"] is False
    assert record["extra"]["no_kill"] and len(calls) == 1


@pytest.mark.parametrize(
    "case", ["changed_project", "changed_source", "new_protected_file", "changed_protected_file"]
)
def test_input_drift_prevents_dispatch(contract, monkeypatch, case):
    path, data = contract
    token, operation = planned(path)
    if case == "changed_project":
        Path(data["project"]).write_bytes(b"drift")
    elif case == "changed_source":
        Path(data["native_entry"]["path"]).write_bytes(b"# drift\n")
    else:
        protected = Path(control.read_json(data["protected_inventories"][0]["path"])["root"])
        (protected / ("new.txt" if case == "new_protected_file" else "keep.txt")).write_bytes(
            b"drift"
        )
    calls = fake_child(monkeypatch)
    with pytest.raises(control.OwnerPreflightError):
        control.confirm(path, token)
    assert calls == [] and row(data, operation)["status"] == "planned"


def test_missing_effect_evidence_cannot_be_partial(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    fake_child(monkeypatch, evidence_missing=True)
    code, value = control.confirm(path, token)
    assert code == 6 and value["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert row(data, operation)["receipt"] is None


@pytest.mark.parametrize("case", ["nonempty", "invalid_type", "absent"])
def test_missing_required_evidence_cannot_claim_complete(contract, monkeypatch, case):
    path, data = contract
    token, operation = planned(path)

    def mutate(result, _):
        if case == "nonempty":
            result["missing_required_evidence"] = [
                dict(name="post_close_identity.json", error_type="OSError")
            ]
        elif case == "invalid_type":
            result["missing_required_evidence"] = False
        else:
            result.pop("missing_required_evidence")

    fake_child(monkeypatch, mutate=mutate)
    code, value = control.confirm(path, token)
    assert code == 6 and value["error"]["code"] == "E_OUTCOME_UNKNOWN"
    record = row(data, operation)
    assert record["receipt"] is None
    assert record["extra"]["reported_native_counts"]["Close"] == 1
    assert record["extra"]["reported_control_completed"] is True
    assert record["extra"]["reported_verification"]["status"] == "passed"
    raw = control.read_json(record["extra"]["result"]["path"])
    assert raw["counts"]["Close"] == 1 and raw["complete"] is True


def test_after_close_incomplete_verification_never_becomes_passed(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    fake_child(
        monkeypatch,
        mutate=lambda result, _: result.update(
            verification=dict(status="incomplete", reason="Final post-Close identity not verified")
        ),
    )
    _, value = control.confirm(path, token)
    assert value["error"]["code"] == "E_PARTIAL"
    receipt = row(data, operation)["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["extensions"]["native_counts"]["Close"] == 1
    assert receipt["extensions"]["control_completed"] is False


def test_required_evidence_hashes_are_checked_after_close(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)

    def mutate(result, _):
        Path(result["required_evidence"][0]["path"]).write_bytes(b"changed evidence")

    fake_child(monkeypatch, mutate=mutate)
    code, value = control.confirm(path, token)
    assert code == 6 and value["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert row(data, operation)["receipt"] is None


def test_protected_mutation_kept_as_failed_verification(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)

    def mutate(result, request):
        inventory = control.read_json(request["contract"]["protected_inventories"][0]["path"])
        Path(inventory["files"][0]["path"]).write_bytes(b"illicit mutation")

    fake_child(monkeypatch, mutate=mutate)
    _, value = control.confirm(path, token)
    assert value["error"]["code"] == "E_PARTIAL"
    receipt = row(data, operation)["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "failed"
    assert receipt["extensions"]["protected_ok"] is False


def test_wrong_result_binding_unknown(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    fake_child(monkeypatch, mutate=lambda result, _: result.update(operation_id="wrong-operation"))
    _, value = control.confirm(path, token)
    assert value["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert row(data, operation)["receipt"] is None


def test_real_global_key_is_checked_without_exclusions(contract, monkeypatch):
    path, data = contract
    token, _ = planned(path)
    store = control.StateStore(data["journal"])
    other = "op_global_unresolved"
    try:
        store.add_plan(
            dict(
                operation_id=other,
                plan_id="plan_global",
                capability_id=control.CAPABILITY,
                command=control.COMMAND,
                target_key=control.target_keys(data)[-1],
                request_digest="other",
                scope_id="synthetic-owned-control",
                effects=control.EFFECTS,
                plan={},
                plan_expires_at=9999999999,
            )
        )
        store.finish(other, "unknown")
    finally:
        store.close()
    calls = fake_child(monkeypatch)
    with pytest.raises(control.ControlError, match="unresolved") as exc:
        control.confirm(path, token)
    assert exc.value.details["active_operation"] == other and calls == []


def test_scope_budget_refusal_is_forbidden_without_native_start(contract, monkeypatch):
    path, data = contract
    token, operation = planned(path)
    store = control.StateStore(data["journal"])
    try:
        store.connection().execute(
            "INSERT INTO scope_usage(scope_id,used) VALUES (?,?)",
            ("synthetic-owned-control", 5),
        )
    finally:
        store.close()
    calls = fake_child(monkeypatch)
    with pytest.raises(Exception) as exc:
        control.confirm(path, token)
    assert exc.value.code == "E_FORBIDDEN"
    assert "limit" in exc.value.details["scope"]["missing"]
    assert calls == [] and row(data, operation)["status"] == "planned"


def test_partial_inventory_only_accepts_same_explicit_unreadable_set(contract, monkeypatch):
    path, data = contract
    inventory_pin = data["protected_inventories"][0]
    inventory = control.read_json(inventory_pin["path"])
    locked = Path(inventory["root"]) / "locked"
    locked.write_bytes(b"synthetic protected lock")
    inventory.update(
        complete=False, errors=[dict(path=str(locked), type="PermissionError", errno=13)]
    )
    data["protected_inventories"] = [write(Path(inventory_pin["path"]), inventory)]
    write(path, data)
    original_read = Path.read_bytes

    def unreadable(self):
        if self == locked:
            raise PermissionError(13, "synthetic sharing violation")
        return original_read(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    observed = control.check_inventory(data["protected_inventories"][0])
    assert observed["complete"] is False and observed["known_unreadable"] == 1
    control.plan(path)
    monkeypatch.setattr(Path, "read_bytes", original_read)
    with pytest.raises(control.OwnerPreflightError, match="Previously unreadable"):
        control.check_inventory(data["protected_inventories"][0])


def test_child_environment_keeps_existing_license_without_logging(contract, monkeypatch):
    path, _ = contract
    monkeypatch.setenv("MGLS_LICENSE_FILE", "synthetic-license-marker")
    _, _, environment, _, _, _ = control.preflight(path)
    assert environment["MGLS_LICENSE_FILE"] == "synthetic-license-marker"
    assert os.environ["MGLS_LICENSE_FILE"] == "synthetic-license-marker"


def test_method_source_has_no_rot_or_process_kill_action():
    source = Path(control.__file__).read_text(encoding="utf-8")
    for forbidden in ("GetActiveObject(", ".kill(", ".terminate(", "undispatch(", "store.release("):
        assert forbidden not in source


def field_contract(contract, *, builder):
    path, data = contract
    native = Path(data["native_entry"]["path"]).with_name("owner_field.py")
    native.write_text("# Synthetic field entry, never executed.\n", encoding="utf-8")
    limits = dict(control.LIMITS, Open=2, Close=2, ValuePUT=1, Save=1)
    profile = control.ControlProfile(
        kind="single_field_value_put",
        capability="external.owner.synthetic_single_field",
        command="external synthetic single field",
        limits=limits,
        native_filename="owner_field.py",
        request_schema="owned-native-field-request/v1",
        result_schema="owned-native-field-result/v1",
        receipt_builder=builder,
    )
    data.update(
        profile_kind=profile.kind,
        limits=limits,
        native_entry=control.file_pin(native),
        source_pins=[control.file_pin(native)],
    )
    write(path, data)
    return path, data, profile


def test_compiled_field_profile_plans_explicit_scope_without_changing_noedit(contract):
    path, data, profile = field_contract(contract, builder=lambda *_: None)
    code, value = control.plan(path, profile=profile)
    preview = value["data"]["preview"]
    assert code == 0 and preview["capability_id"] == profile.capability
    assert "ValuePUT1/Save1" in preview["changes"][0]["summary"]
    assert preview["extensions"]["limits"] == data["limits"]
    assert control.LIMITS["ValuePUT"] == 0 and control.LIMITS["Open"] == 1
    with pytest.raises(TypeError):
        profile.limits["Save"] = 50
    with pytest.raises(control.OwnerPreflightError, match="different native method"):
        control.preflight(path)


@pytest.mark.parametrize("case", ["legacy_contract", "wrong_entry"])
def test_field_profile_rejects_legacy_contract_or_entry_before_native(contract, monkeypatch, case):
    path, data, profile = field_contract(contract, builder=lambda *_: None)
    if case == "legacy_contract":
        data.pop("profile_kind")
    else:
        data["native_entry"] = control.file_pin(
            Path(data["native_entry"]["path"]).with_name("owner_lifecycle.py")
        )
    write(path, data)
    calls = fake_child(monkeypatch)
    with pytest.raises(control.OwnerPreflightError):
        control.plan(path, profile=profile)
    assert calls == []


@pytest.mark.parametrize("case", ["old_schema", "missing_evidence", "valid"])
def test_field_profile_core_truth_gate_precedes_fixed_builder(contract, monkeypatch, case):
    invoked = []

    def builder(record, result, result_pin, protected_ok, validator):
        invoked.append((record, result, result_pin, protected_ok, validator))
        # This synthetic builder deliberately returns no receipt. It does not
        # implement or claim the new field's native acceptance conditions.
        return None

    path, data, profile = field_contract(contract, builder=builder)
    _, planned_value = control.plan(path, profile=profile)
    operation = planned_value["data"]["preview"]["operation_id"]
    token = planned_value["data"]["confirm_token"]

    def mutate(result, request):
        assert request["schema"] == profile.request_schema
        assert request["contract"]["limits"]["ValuePUT"] == 1
        if case != "old_schema":
            result["schema"] = profile.result_schema
        if case == "missing_evidence":
            result["missing_required_evidence"] = [{"name": "post_close"}]

    calls = fake_child(monkeypatch, mutate=mutate)
    code, value = control.confirm(path, token, profile=profile)
    assert code == 6 and value["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert len(calls) == 1 and len(invoked) == (1 if case == "valid" else 0)
    assert row(data, operation)["receipt"] is None


def test_compiled_builder_cannot_upgrade_native_incomplete_verification(contract, monkeypatch):
    def builder(record, result, result_pin, protected_ok, validator):
        changed_claim = dict(result, verification={"status": "passed"})
        receipt = control._receipt(record, changed_claim, result_pin, protected_ok, validator)
        receipt["capability_id"] = record["capability_id"]
        return receipt

    path, data, profile = field_contract(contract, builder=builder)
    _, planned_value = control.plan(path, profile=profile)
    operation = planned_value["data"]["preview"]["operation_id"]

    def mutate(result, _):
        result["schema"] = profile.result_schema
        result["verification"] = dict(status="incomplete", reason="Post-Close evidence absent")

    fake_child(monkeypatch, mutate=mutate)
    code, value = control.confirm(path, planned_value["data"]["confirm_token"], profile=profile)
    assert code == 6 and value["error"]["code"] == "E_OUTCOME_UNKNOWN"
    record = row(data, operation)
    assert record["receipt"] is None and record["extra"]["reported_native_counts"]["Close"] == 1
    assert record["extra"]["receipt_rejection"]["type"] == "ValueError"
