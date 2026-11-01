"""First-open admission provenance; real SDK schemas, explicit fake native facts.

Closed-copy materialization/file guards are covered by test_designer_first_open.
These tests never create a CAD object, scan a process or use an old task state.
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import designer_first_open as F
from xpedition_cli import designer_project_admission as J
from xpedition_cli import driver as D
from xpedition_cli import native_process as P
from xpedition_cli import session
from xpedition_cli._vendor.tool_driver_sdk.scope import ScopeResolver
from xpedition_cli._vendor.tool_driver_sdk.store import target_key
from xpedition_cli._vendor.tool_driver_sdk.validation import ContractValidator
from xpedition_cli.backends.native_xpedition import NativeBackend
from xpedition_cli.cli import common, sessions
from xpedition_cli.errors import CLIError

START_ID = "op-" + "1" * 32
COPY_ID = "op-" + "2" * 32
OPEN_ID = "op-" + "3" * 32
LATER_ID = "op-" + "4" * 32


@pytest.fixture(autouse=True)
def no_native(monkeypatch, tmp_path):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Admission tests must not access real native APIs or a transport")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    for name in ("ProcessHandle", "processes", "process_identity"):
        monkeypatch.setattr(P, name, forbidden)
    monkeypatch.setattr(NativeBackend, "invoke", forbidden)
    monkeypatch.setattr(NativeBackend, "_command_path", forbidden)
    monkeypatch.setattr(common, "native", forbidden)
    monkeypatch.setattr(sessions, "native", forbidden)
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "fresh-config"))
    token = J._CURRENT.set(None)
    yield
    J._CURRENT.reset(token)
    assert hits == []


@pytest.fixture(scope="module")
def validator():
    return ContractValidator(Path(J.__file__).parent / "_vendor/spec")


@pytest.fixture
def admitted(tmp_path, monkeypatch, validator):
    project = str((tmp_path / "registered-copy/Board.prj").resolve())
    grant = tmp_path / "scope.json"
    grant.write_text(
        json.dumps(
            {
                "scope_id": "scope-admission-fixture",
                "tool": "xpedition-cli",
                "granted_by": "offline test fixture",
                "granted_at": "2026-10-01T00:00:00Z",
                "expires_at": None,
                "effects": ["observe", "session", "persist", "change"],
                "dangerous": False,
                "roots": [str(tmp_path)],
                "outputs": [str(tmp_path)],
                "destinations": [],
            }
        ),
        encoding="utf-8",
    )
    scope = ScopeResolver(
        "xpedition-cli",
        validator=validator,
        environ={"XPEDITION_CLI_SCOPE_FILE": str(grant)},
        home=tmp_path,
    ).resolve()
    assert scope.granted
    actual = {
        "pid": 103,
        "executable": "C:/fixture/SDD_HOME/wv/win64/bin/viewdraw.exe",
        "creation_time_100ns": "134375464979013265",
    }
    reference = {
        "protocol": "designer-owned-session-v1",
        "owner_id": "a" * 32,
        "owner_epoch_sha256": "b" * 64,
        "source_bundle_sha256": "c" * 64,
    }
    capsule = {
        "protocol": F.PROTOCOL,
        "source_kind": "closed_copy",
        "observation_budget": F.observation_budget_for("closed_copy", 1, 1),
        "project": project,
        "copy_operation_id": COPY_ID,
        "open_operation_id": OPEN_ID,
        "scope_id": scope.scope_id,
        "scope_sha256": hashlib.sha256(grant.read_bytes()).hexdigest(),
        "owner_epoch_sha256": reference["owner_epoch_sha256"],
        "copy_record_sha256": "1" * 64,
        "copy_receipt_sha256": "2" * 64,
        "registration_sha256": "3" * 64,
        "genesis_sha256": "4" * 64,
        "library_closure_sha256": "5" * 64,
        "target_tree_sha256": "6" * 64,
        "target_directory_identity": [1, 2],
        "target_project_identity": [1, 3],
    }
    assert set(capsule) == F._FIELDS
    options = {
        "kind": "schematic",
        "pid": actual["pid"],
        "project": project,
        "project_source_operation_id": COPY_ID,
    }
    state = {
        "domain": "schematic",
        "state": "started",
        "process_identity": copy.deepcopy(actual),
        "startup_operation_id": START_ID,
        "owner_ref": copy.deepcopy(reference),
        "active_bound_project": project,
        "project_source_operation_id": COPY_ID,
        "project_admission_operation_id": OPEN_ID,
        "project_admission_binding": copy.deepcopy(capsule),
    }
    target = {
        "mode": "session",
        "engine_id": "xpedition-schematic",
        "instance_id": str(actual["pid"]),
        "object_id": project,
        "locator": Path(project).as_uri(),
        "extensions": {
            "command": "session attach",
            "domain": "schematic",
            "project": project,
            "source_operation_id": OPEN_ID,
            "process_identity": copy.deepcopy(actual),
            "designer_project_open_route": J.ROUTE,
            "designer_project_source": copy.deepcopy(capsule),
        },
    }

    def snapshot(identifier, opened):
        return {
            "id": identifier,
            "source": "live",
            "revision": "offline fixture revision",
            "observed_at": "2026-10-27T00:00:00Z",
            "dependencies": [],
            "extensions": {
                "facts": {
                    "os_process_identity": copy.deepcopy(actual),
                    "native": {"fixture": True, "project_open": opened},
                    "application": {
                        "pid": actual["pid"],
                        "document": {
                            "status": "observed",
                            "open": opened,
                            "path": project if opened else None,
                            "designs": ["Board"] if opened else [],
                        },
                    },
                }
            },
        }

    before, after = snapshot("snapshot-before", False), snapshot("snapshot-after", True)
    plan = {
        "operation_id": OPEN_ID,
        "plan_id": "plan-admission-fixture",
        "capability_id": "session-attach",
        "authorization_scope_id": scope.scope_id,
        "effects": ["session", "persist"],
        "target": copy.deepcopy(target),
        "base_snapshot": before,
        "level": "plan",
        "changes": [{"action": "open", "resource": project, "summary": "Pure fixture first open"}],
        "outputs": [],
        "unknowns": [],
        "constraints": [],
        "trial_snapshot": None,
        "can_apply": True,
        "expires_at": "2030-01-01T00:00:00Z",
    }
    receipt = {
        "operation_id": OPEN_ID,
        "plan_id": plan["plan_id"],
        "capability_id": plan["capability_id"],
        "target": copy.deepcopy(target),
        "authorization_scope_id": scope.scope_id,
        "effects": ["session", "persist"],
        "snapshot_before": copy.deepcopy(before),
        "snapshot_after": after,
        "outcome": {
            "execution": "completed",
            "change": "applied",
            "persistence": "saved",
            "delivery": "not_applicable",
            "verification": {
                "status": "passed",
                "scope": "Pure fixture same-owner project and metadata readback",
                "snapshot_id": after["id"],
                "coverage": "complete",
                "checks": [
                    {
                        "id": "first-open-fixture",
                        "status": "passed",
                        "required": True,
                        "evidence_ref": "fixture:same-owner-readback",
                    }
                ],
                "not_checked": [],
                "evidence_refs": ["fixture:same-owner-readback"],
            },
        },
        "failed_stage": None,
        "changed_items": [],
        "artifacts": [],
        "deliveries": [],
        "recovery": {"action": "none", "reason": "Pure verified fixture", "supported": False},
        "extensions": {
            "result": {
                "domain": "schematic",
                "pid": actual["pid"],
                "process_identity": copy.deepcopy(actual),
                "started": False,
                "attached": True,
                "opened": True,
                "created": False,
                "path": project,
                "owner_ref": copy.deepcopy(reference),
                "startup_operation_id": START_ID,
                "project_source_operation_id": COPY_ID,
                "first_open_binding_sha256": J._digest(capsule),
                "first_open": {"opened": True, "fixture": True},
            }
        },
    }
    validator.validate("plan", plan)
    validator.validate("receipt", receipt)
    assert validator.receipt_rule(receipt)["ok"] is True
    record = {
        "operation_id": OPEN_ID,
        "plan_id": plan["plan_id"],
        "capability_id": plan["capability_id"],
        "command": "session attach",
        "kind": "single",
        "status": "succeeded",
        "resolution": None,
        "scope_id": scope.scope_id,
        "effects": ["session", "persist"],
        "target_key": target_key(target),
        "plan": plan,
        "receipt": receipt,
    }
    reads = []

    def get_record(operation_id):
        reads.append(operation_id)
        return copy.deepcopy(record) if operation_id == OPEN_ID else None

    adapter = NS(ownership_record_get=get_record, ownership_validator=validator)
    monkeypatch.setattr(session, "read_state", lambda: copy.deepcopy(state))
    return NS(
        scope=scope,
        actual=actual,
        reference=reference,
        capsule=capsule,
        options=options,
        state=state,
        target=target,
        plan=plan,
        receipt=receipt,
        record=record,
        adapter=adapter,
        reads=reads,
        validator=validator,
    )


def _require(case, *, command="schematic edit", operation_id=LATER_ID):
    return J.require_admission_receipt(case.adapter, case.scope, case.state, operation_id, command)


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "pcb"},
        {"project": "relative.prj"},
        {"pid": 0},
        {"pid": True},
        {"project_source_operation_id": "../foreign"},
        {"project_source_operation_id": {"verified": True}},
        {"startup_operation_id": START_ID},
        {"host_bootstrap": "identity"},
    ],
)
def test_invalid_source_selectors_fail_before_any_native(admitted, change):
    with pytest.raises(CLIError) as failure:
        J.validate_intent({**admitted.options, **change})
    assert failure.value.code == "E_USAGE"
    assert failure.value.details["native_request_dispatched"] is False


def test_legacy_attach_and_old_signed_target_remain_disabled(admitted):
    with pytest.raises(D.DriverError):
        D._require_dedicated_designer("session attach", {"kind": "schematic", "pid": 103})
    old = copy.deepcopy(admitted.target)
    old["extensions"].pop("designer_project_open_route")
    with pytest.raises(CLIError):
        D._require_dedicated_designer(target=old)


@pytest.mark.parametrize("field", ["copy_operation_id", "project", "open_operation_id"])
def test_signed_source_plan_mismatch_rejects_before_native(admitted, field):
    target = copy.deepcopy(admitted.target)
    target["extensions"]["designer_project_source"][field] = (
        str(Path(admitted.options["project"]).with_name("Foreign.prj"))
        if field == "project"
        else "op-foreign"
    )
    with pytest.raises(CLIError):
        J.validate_intent(admitted.options, target)


def test_valid_intent_does_not_supply_a_typed_sdk_context(admitted):
    J.validate_intent(admitted.options, admitted.target)
    with pytest.raises(CLIError) as failure:
        sessions.attach({**admitted.options, "verified": True, "native_qualified": True})
    assert failure.value.details["reason"] == "original_scoped_sdk_operation_required"


@pytest.mark.parametrize("impostor", [{"verified": True}, NS(verified=True)])
def test_json_or_namespace_cannot_impersonate_sdk_context(admitted, impostor):
    token = J._CURRENT.set(impostor)
    try:
        with pytest.raises(CLIError) as failure:
            J.build_target_binding(admitted.actual)
    finally:
        J._CURRENT.reset(token)
    assert failure.value.details["reason"] == "original_scoped_sdk_operation_required"


@pytest.mark.parametrize("changes", [{"pid": 104}, {"creation_time_100ns": "134375464979013266"}])
def test_foreign_original_generation_fails_before_source_or_handle_probe(admitted, changes):
    with (
        J.bind_sdk_admission(admitted.adapter, admitted.scope, OPEN_ID, admitted.options),
        pytest.raises(CLIError) as failure,
    ):
        J.build_target_binding({**admitted.actual, **changes})
    assert failure.value.details["reason"] == "current_original_dedicated_generation_required"


def test_confirmed_source_binding_must_be_rebuilt_equal(admitted, monkeypatch):
    calls = []

    def source(**kwargs):
        calls.append(kwargs)
        return copy.deepcopy(admitted.capsule)

    monkeypatch.setattr(F, "build_closed_copy_open_binding", source)
    monkeypatch.setattr(J, "_generation_reader", lambda *a: lambda: [])
    changed = {**admitted.capsule, "copy_receipt_sha256": "9" * 64}
    with (
        J.bind_sdk_admission(admitted.adapter, admitted.scope, OPEN_ID, admitted.options, changed),
        pytest.raises(CLIError) as failure,
    ):
        J.build_target_binding(admitted.actual)
    assert failure.value.details["reason"] == "confirmed_first_open_source_changed"
    assert calls[0]["source_operation_id"] == COPY_ID


def test_matching_successful_schema_valid_sdk_receipt_allows_later_write(admitted):
    assert _require(admitted) is None
    assert admitted.reads == [OPEN_ID]


def test_session_only_open_unchanged_receipt_is_not_mislabeled_as_saved(admitted):
    for value in (admitted.record, admitted.plan, admitted.receipt):
        value["effects"] = ["session"]
    admitted.receipt["outcome"].update(change="not_applied", persistence="unchanged")
    admitted.validator.validate("plan", admitted.plan)
    admitted.validator.validate("receipt", admitted.receipt)
    assert admitted.validator.receipt_rule(admitted.receipt)["ok"] is True
    before = copy.deepcopy(admitted.receipt)
    assert _require(admitted) is None
    assert admitted.receipt == before


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "different_command",
        "foreign_scope",
        "missing_receipt",
        "foreign_owner",
        "foreign_path",
        "foreign_copy_source",
        "foreign_binding",
        "foreign_pid",
        "foreign_birth",
        "foreign_receipt_operation",
        "foreign_plan",
        "foreign_target",
        "foreign_engine",
        "foreign_mode",
        "foreign_record_target_key",
        "incomplete_binding_metadata",
    ],
)
def test_success_markers_cannot_replace_original_provenance(admitted, change):
    result = admitted.receipt["extensions"]["result"]
    if change == "unknown":
        admitted.record["status"] = "unknown"
    elif change == "different_command":
        admitted.record["command"] = "project create"
    elif change == "foreign_scope":
        admitted.record["scope_id"] = "scope-foreign"
    elif change == "missing_receipt":
        admitted.record["receipt"] = None
    elif change == "foreign_owner":
        result["owner_ref"]["owner_id"] = "f" * 32
    elif change == "foreign_path":
        result["path"] = str(Path(result["path"]).with_name("Foreign.prj"))
    elif change == "foreign_copy_source":
        result["project_source_operation_id"] = "op-foreign"
    elif change == "foreign_binding":
        result["first_open_binding_sha256"] = "f" * 64
    elif change == "foreign_pid":
        result["process_identity"]["pid"] = 104
    elif change == "foreign_birth":
        result["process_identity"]["creation_time_100ns"] = "134375464979013266"
    elif change == "foreign_receipt_operation":
        admitted.receipt["operation_id"] = "op-foreign"
    elif change == "foreign_plan":
        admitted.record["plan"]["operation_id"] = "op-foreign"
    elif change == "foreign_target":
        admitted.receipt["target"]["instance_id"] = "104"
    elif change in {"foreign_engine", "foreign_mode"}:
        key, value = (
            ("engine_id", "xpedition-pcb") if change == "foreign_engine" else ("mode", "file")
        )
        admitted.plan["target"][key] = admitted.receipt["target"][key] = value
        admitted.record["target_key"] = target_key(admitted.plan["target"])
    elif change == "foreign_record_target_key":
        admitted.record["target_key"] = "session|xpedition-schematic|104|foreign-project"
    else:
        admitted.state["project_admission_operation_id"] = None
    before = copy.deepcopy(admitted.record)
    with pytest.raises(CLIError) as failure:
        _require(admitted)
    assert failure.value.details["native_request_dispatched"] is False
    assert admitted.record == before


def test_unknown_original_id_readback_does_not_rewrite_receipt(admitted):
    admitted.record["status"] = "unknown"
    admitted.receipt["outcome"].update(execution="unknown", change="unknown", persistence="unknown")
    admitted.receipt["outcome"]["verification"].update(
        status="incomplete",
        coverage="partial",
        checks=[],
        reason="Original first-open completion has not been independently verified",
    )
    admitted.validator.validate("receipt", admitted.receipt)
    assert admitted.validator.receipt_rule(admitted.receipt)["ok"] is False
    before = json.dumps(admitted.record, sort_keys=True)
    assert _require(admitted, command="session inspect", operation_id=OPEN_ID) is None
    assert json.dumps(admitted.record, sort_keys=True) == before
    with pytest.raises(CLIError):
        _require(admitted, operation_id=LATER_ID)
    assert json.dumps(admitted.record, sort_keys=True) == before


def test_typed_same_open_context_allows_its_own_sdk_readback(admitted):
    admitted.record["status"] = "unknown"
    with J.bind_sdk_admission(
        admitted.adapter, admitted.scope, OPEN_ID, admitted.options, admitted.capsule
    ):
        assert _require(admitted, command="session attach", operation_id=OPEN_ID) is None
    assert admitted.record["status"] == "unknown"
    assert admitted.reads == []


def test_same_id_context_cannot_authorize_engineering_write(admitted):
    admitted.record["status"] = "unknown"
    with (
        J.bind_sdk_admission(
            admitted.adapter, admitted.scope, OPEN_ID, admitted.options, admitted.capsule
        ),
        pytest.raises(CLIError),
    ):
        _require(admitted, operation_id=OPEN_ID)


@pytest.mark.parametrize("change", ["scope", "capsule"])
def test_same_open_context_cannot_bypass_foreign_binding(admitted, change):
    admitted.record["status"] = "unknown"
    scope = admitted.scope
    capsule = copy.deepcopy(admitted.capsule)
    if change == "scope":
        scope = copy.copy(scope)
        scope.summary = {**scope.summary, "scope_id": "scope-foreign"}
    else:
        capsule["copy_receipt_sha256"] = "f" * 64
    before = copy.deepcopy(admitted.record)
    with (
        J.bind_sdk_admission(admitted.adapter, scope, OPEN_ID, admitted.options, capsule),
        pytest.raises(CLIError),
    ):
        _require(admitted, command="session attach", operation_id=OPEN_ID)
    assert admitted.record == before


def test_effect_preview_and_direct_confirm_share_the_real_handler_token(admitted, monkeypatch):
    from xpedition_cli.cli.effect_gate import invoke
    from xpedition_cli.cli.registry import by_path

    command = by_path()["session attach"]
    calls = []
    opened = False

    def application():
        return {
            "pid": admitted.actual["pid"],
            "document": {
                "status": "observed",
                "open": opened,
                "path": admitted.options["project"] if opened else None,
                "designs": ["Board"] if opened else [],
            },
        }

    class Backend:
        def invoke(self, method, params, **kwargs):
            nonlocal opened
            calls.append((method, copy.deepcopy(params)))
            if method == "identity":
                return {"application": application()}
            assert method == "open"
            assert params == {
                "domain": "schematic",
                "project": admitted.options["project"],
                "pid": admitted.actual["pid"],
                "start": False,
            }
            opened = True
            return {"opened": True, "fixture": True}

    class Pin:
        def __init__(self, domain, pid, actual):
            assert domain == "schematic" and pid == admitted.actual["pid"]
            assert P.same_identity(actual, admitted.actual)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def store(value):
        admitted.state.clear()
        admitted.state.update(copy.deepcopy(value))

    # The physical copy/OS validators are explicit fake boundaries here, not a
    # second copy/fileguard suite. Confirmation issue/consume and both handlers
    # are real, using only this test's fresh configuration directory.
    monkeypatch.setattr(
        F, "build_closed_copy_open_binding", lambda **kw: copy.deepcopy(admitted.capsule)
    )
    monkeypatch.setattr(J, "_generation_reader", lambda *args: lambda: [])
    monkeypatch.setattr(sessions, "_require_no_pending_close", lambda: None)
    monkeypatch.setattr(P, "ProcessHandle", Pin)
    monkeypatch.setattr(common, "native", lambda: Backend())
    monkeypatch.setattr(session, "_write_state", store)
    for key in (
        "active_bound_project",
        "project_source_operation_id",
        "project_admission_operation_id",
        "project_admission_binding",
    ):
        admitted.state.pop(key)
    with pytest.raises(CLIError):
        invoke(command, sessions.attach, {**admitted.options, "dry_run": True})
    assert calls == []
    assert not (J.config_dir() / "confirm.secret").exists()

    with J.bind_sdk_admission(
        admitted.adapter, admitted.scope, OPEN_ID, admitted.options, admitted.capsule
    ):
        preview = invoke(command, sessions.attach, {**admitted.options, "dry_run": True})
        assert preview["preview"]["operation"] == "designer_registered_project_first_open"
        assert preview["preview"]["binding_sha256"] == J._digest(admitted.capsule)
        assert calls == []
        token = preview["confirm_token"]
        assert token.startswith("ct_")
        # Adapter.apply calls the handler directly; it does not issue/consume a
        # second generic scope. The original preview token must work there.
        complete = sessions.attach({**admitted.options, "confirm": token})
        assert complete["opened"] is True and complete["created"] is False
        assert [method for method, params in calls] == ["identity", "open", "identity"]
        assert admitted.state["project_admission_operation_id"] == OPEN_ID
        assert admitted.state["startup_operation_id"] == START_ID
        with pytest.raises(CLIError) as replay:
            sessions.attach({**admitted.options, "confirm": token})
        assert replay.value.details["native_request_dispatched"] is False
        assert len(calls) == 3


@pytest.mark.parametrize("path", ["session attach", "session start"])
def test_layout_and_other_generic_tokens_keep_the_existing_flow(admitted, path):
    from xpedition_cli.cli.effect_gate import invoke
    from xpedition_cli.cli.registry import by_path

    command = by_path()[path]
    calls = []

    def handler(options):
        calls.append(copy.deepcopy(options))
        return {"generic_handler_called": True}

    options = {"kind": "pcb", **({"pid": 103} if path == "session attach" else {})}
    preview = invoke(command, handler, {**options, "dry_run": True})
    assert calls == []
    assert preview["preview"]["command"] == path
    assert "operation" not in preview["preview"]
    assert invoke(command, handler, {**options, "confirm": preview["confirm_token"]}) == {
        "generic_handler_called": True
    }
    assert len(calls) == 1
