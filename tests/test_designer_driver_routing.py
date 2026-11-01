"""Intent selection and original SDK/registry checks do not grant via JSON."""

from types import SimpleNamespace as NS

import pytest

from xpedition_cli import designer_driver_routing as R
from xpedition_cli import designer_owner_client as C
from xpedition_cli import driver as D
from xpedition_cli import session, session_ownership
from xpedition_cli.errors import CLIError


def test_start_intent_does_not_enable_old_targets_or_running_instance_attach():
    D._require_dedicated_designer("session start", {"kind": "schematic"})
    target = {"extensions": {"command": "session start", "domain": "schematic"}}
    with pytest.raises(D.DriverError):
        D._require_dedicated_designer(target=target)
    target["extensions"]["designer_startup_route"] = D.DESIGNER_START_ROUTE
    with pytest.raises(D.DriverError):
        D._require_dedicated_designer(target=target)
    target["extensions"].update(designer_startup_generation="op-new", source_operation_id="op-new")
    D._require_dedicated_designer(target=target)
    for command, options in (
        ("session attach", {"kind": "schematic"}),
        ("session start", {"kind": "schematic", "project": "old.prj"}),
    ):
        with pytest.raises(D.DriverError):
            D._require_dedicated_designer(command, options)


def test_reference_still_needs_original_receipt_and_registry_each_time(monkeypatch):
    reference = {"opaque": "reference fixture"}
    actual = {"pid": 42, "creation_time_100ns": "99", "executable": "fixture"}
    state = {
        "owner_ref": reference,
        "process_identity": actual,
        "startup_operation_id": "op-original",
    }
    pointer = {"original": "record fixture"}
    scope = NS(granted=True, scope_id="scope-fixture")
    source_result = {"owner_ref": reference}
    adapter = NS(
        _ownership=lambda *a, **kw: ({"startup_operation_id": "op-original"}, pointer),
        ownership_record_get=lambda _id: {"receipt": {"extensions": {"result": source_result}}},
    )
    monkeypatch.setattr(session, "read_state", lambda: state)
    monkeypatch.setattr(session_ownership, "pointer_for", lambda *a: pointer)
    calls = []

    def resolved(ref, revalidate):
        calls.append(ref)
        revalidate()
        return "validated opaque locator fixture"

    monkeypatch.setattr(C, "resolve_owner", resolved)
    provider = R._provider(adapter, scope, None)
    assert provider("identity", {}) == "validated opaque locator fixture"
    source_result["owner_ref"] = {"opaque": "different source"}
    with pytest.raises(CLIError, match="differs from the original"):
        provider("identity", {})
    source_result["owner_ref"] = reference
    monkeypatch.setattr(session_ownership, "pointer_for", lambda *a: None)
    with pytest.raises(CLIError, match="registry is unavailable"):
        provider("identity", {})
    assert len(calls) == 3


def test_confirmed_source_binding_is_carried_without_mutating_plan(tmp_path):
    path = tmp_path / "grant.json"
    path.write_bytes(b"scope fixture bytes")
    scope = NS(granted=True, scope_id="scope-fixture", grant_file=str(path))
    source = {"closure_sha256": "a" * 64}
    plan = {"base_snapshot": {"extensions": {"facts": {"designer_setup_source": source}}}}
    result = R._binding(scope, "project create", "op-confirmed", plan)
    assert result["source_binding"] == source
    assert result["source_operation_id"] == "op-confirmed"
    assert plan["base_snapshot"]["extensions"]["facts"]["designer_setup_source"] == source
