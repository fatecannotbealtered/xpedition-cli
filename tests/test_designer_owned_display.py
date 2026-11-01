"""Owned display integration uses the held object and owner persistence, never source strings."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from test_schematic_attribute_display_native import (
    _all_writes,
    _apply,
    _harness,
    _operation,
    _parts,
)

from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A


@pytest.fixture
def owned(monkeypatch):
    parts = _parts()
    app, events, snapshots = _harness(monkeypatch, parts)
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    token = D._CURRENT.set((object(), "apply_changeset"))

    def identity(selected, domain):
        assert selected is app and domain == "schematic"
        value = A._legacy_application_identity(app, domain)
        value["document"]["source"] = "dedicated_startup_host_Application_and_authenticated_ncalrpc"
        return value

    identities = []

    def checked_identity(selected, domain):
        value = identity(selected, domain)
        identities.append(copy.deepcopy(value))
        return value

    def require(*, app):
        assert app is selected_app
        events.append("current_owner_checked")

    selected_app = app
    monkeypatch.setattr(D, "require_current_document_owner", require)
    monkeypatch.setattr(D, "application_identity", checked_identity)
    monkeypatch.setattr(
        A,
        "application_identity",
        lambda *args: pytest.fail("owned display cannot use legacy identity"),
    )
    monkeypatch.setattr(
        A,
        "_close_if_open",
        lambda *args, **kwargs: pytest.fail("owned close must use owner lifecycle"),
    )
    monkeypatch.setattr(
        A,
        "_ensure_project",
        lambda *args, **kwargs: pytest.fail("owned reopen must use owner lifecycle"),
    )
    persisted = []

    def persist(selected, project):
        assert selected is app and project == str(app.project_path.resolve())
        persisted.append(project)
        before = identity(app, "schematic")
        app.save_all()
        assert app.CloseProject() is True
        assert app.opened is False
        assert app.OpenProject(project) is True
        after = identity(app, "schematic")
        return {
            "source": "designer_save_close_reopen",
            "save_count": 1,
            "close_count": 1,
            "reopen_count": 1,
            "save_acknowledged": True,
            "closed_observed": True,
            "close_returned": True,
            "before_identity": before["document"],
            "before_pid": before["pid"],
            "reopened_identity": after["document"],
            "reopened_pid": after["pid"],
        }

    monkeypatch.setattr(D, "persist_changeset_project", persist)
    yield SimpleNamespace(
        app=app,
        parts=parts,
        events=events,
        snapshots=snapshots,
        identities=identities,
        persisted=persisted,
        identity=identity,
    )
    D._CURRENT.reset(token)


def test_owned_display_reads_trusted_identity_and_one_owner_persistence(owned):
    result = _apply(owned.app, [_operation(size=15)])
    assert result["persistence_verified"] is True
    assert result["verification_before_save"]["valid"] is True
    assert result["verification"]["valid"] is True
    assert len(owned.persisted) == 1 and len(owned.snapshots) == 3
    assert [owned.events.count(name) for name in ("SaveAll", "CloseProject", "OpenProject")] == [
        1,
        1,
        1,
    ]
    assert owned.events.count("current_owner_checked") == 1
    assert len(owned.identities) == 2
    assert result["lifecycle"]["before_identity"] == result["lifecycle"]["reopened_identity"]
    assert owned.parts[2].Attributes[1].GetLocation().X == 233


def test_wrong_held_application_is_rejected_before_any_snapshot_or_setter(owned, monkeypatch):
    monkeypatch.setattr(A, "_owner_application", lambda: object())
    with pytest.raises(A.AdapterError) as caught:
        _apply(owned.app, [_operation()])
    assert caught.value.code == "E_CONFLICT"
    assert caught.value.details["display_failure"]["stage"] == "display_owner_preflight"
    assert caught.value.details["write_attempted"] is False
    assert not owned.snapshots and not _all_writes(owned.parts) and not owned.persisted


@pytest.mark.parametrize("fake_context", [False, True])
def test_forged_source_without_a_trusted_current_owner_cannot_enter_display(
    monkeypatch, fake_context
):
    parts = _parts()
    app, _events, snapshots = _harness(monkeypatch, parts)
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    forged = {
        "pid": 4242,
        "document": {
            "open": True,
            "path": str(app.project_path.resolve()),
            "designs": ["Board1"],
            "status": "observed",
            "source": "dedicated_startup_host_Application_and_authenticated_ncalrpc",
        },
    }
    monkeypatch.setattr(A, "application_identity", lambda *args: forged)
    token = D._CURRENT.set((object(), "apply_changeset") if fake_context else None)
    try:
        with pytest.raises(A.AdapterError) as caught:
            _apply(app, [_operation()])
        assert caught.value.code == "E_CONFLICT"
        assert caught.value.details["display_failure"]["stage"] == "display_owner_preflight"
        assert not snapshots and not _all_writes(parts)
    finally:
        D._CURRENT.reset(token)


def test_forged_dedicated_source_does_not_activate_the_legacy_helper(monkeypatch):
    parts = _parts()
    app, _events, snapshots = _harness(monkeypatch, parts)
    monkeypatch.setattr(A, "_owner_application", lambda: None)
    identity = A._legacy_application_identity(app, "schematic")
    identity["document"]["source"] = "dedicated_startup_host_Application_and_authenticated_ncalrpc"
    monkeypatch.setattr(A, "application_identity", lambda *args: identity)
    with pytest.raises(A.AdapterError) as caught:
        _apply(app, [_operation()])
    assert caught.value.code == "E_VALIDATION"
    assert caught.value.details["display_failure"]["stage"] == "display_identity_preflight"
    assert len(snapshots) == 1 and not _all_writes(parts)


@pytest.mark.parametrize("method", ["identity", "clone_project", "open"])
def test_owned_display_requires_current_changeset_context(owned, method):
    token = D._CURRENT.set((object(), method))
    try:
        with pytest.raises(A.AdapterError) as caught:
            _apply(owned.app, [_operation()])
        assert caught.value.code == "E_CONFLICT"
        assert not owned.snapshots and not _all_writes(owned.parts) and not owned.persisted
    finally:
        D._CURRENT.reset(token)


@pytest.mark.parametrize("fault", ["pid", "path", "status", "open", "designs"])
def test_owned_identity_getter_loss_cannot_be_replaced_by_source_string(owned, monkeypatch, fault):
    def identity(*args):
        value = owned.identity(*args)
        if fault == "pid":
            value["pid"] = True
        else:
            value["document"][fault] = {
                "path": str(owned.app.project_path.with_name("foreign.prj")),
                "status": "unobserved",
                "open": False,
                "designs": [],
            }[fault]
        return value

    monkeypatch.setattr(D, "application_identity", identity)
    with pytest.raises(A.AdapterError) as caught:
        _apply(owned.app, [_operation()])
    assert caught.value.details["display_failure"]["stage"] == "display_identity_preflight"
    assert not _all_writes(owned.parts) and not owned.persisted


@pytest.mark.parametrize("phase", ["save_unknown", "close", "open", "missing_evidence"])
def test_owned_persistence_failure_uses_actual_nullable_lifecycle(owned, monkeypatch, phase):
    known = {
        "save_count": 1,
        "close_count": 0,
        "reopen_count": 0,
        "save_acknowledged": None,
        "closed_observed": False,
        "close_returned": None,
    }
    if phase in {"close", "open"}:
        known.update(save_acknowledged=True, close_count=1)
    if phase == "open":
        known.update(reopen_count=1, closed_observed=True, close_returned=True)

    def refuse(*args):
        error = A.AdapterError("E_SERVER", "synthetic persistence interruption")
        if phase != "missing_evidence":
            error.lifecycle = copy.deepcopy(known)
        raise error

    monkeypatch.setattr(D, "persist_changeset_project", refuse)
    with pytest.raises(A.AdapterError) as caught:
        _apply(owned.app, [_operation()])
    details = caught.value.details
    assert details["persistence_verified"] is False and details["write_attempted"] is True
    assert details["display_failure"]["stage"] == "display_save_close_reopen"
    assert len(owned.snapshots) == 2 and "read_back_after_reopen" not in details
    assert details["saved"] is (True if phase in {"close", "open"} else None)
    if phase == "missing_evidence":
        assert all(
            details["lifecycle"][key] is None
            for key in (
                "save_count",
                "close_count",
                "reopen_count",
                "save_acknowledged",
                "closed_observed",
            )
        )
    else:
        for key, value in known.items():
            assert details["lifecycle"][key] == value


def test_owned_partial_setter_never_saves_and_retains_attempted_operation(owned, monkeypatch):
    attribute = owned.parts[2].Attributes[1]

    def partial(x, y):
        attribute.point = SimpleNamespace(X=x, Y=y)
        raise RuntimeError("synthetic setter returned no terminal result")

    monkeypatch.setattr(attribute, "SetLocation", partial)
    with pytest.raises(A.AdapterError) as caught:
        _apply(owned.app, [_operation()])
    details = caught.value.details
    assert attribute.point.X == 233
    assert details["applied"] == [] and details["saved"] is False
    assert details["write_attempted"] is True and not owned.persisted
    assert details["display_failure"] == {
        "stage": "display_apply",
        "reason": "display_apply_or_verification_failed",
        "operations_requested": 1,
        "operations_returned": 0,
        "operation_index": 0,
        "operation_call_attempted": True,
    }


def test_second_sheet_failure_does_not_claim_the_second_setter_was_entered(owned, monkeypatch):
    calls = []

    def ensure(*args):
        calls.append(args)
        if len(calls) == 2:
            raise A.AdapterError("E_CONFLICT", "synthetic second sheet unavailable")

    monkeypatch.setattr(A, "_ensure_sheet", ensure)
    second = {**_operation(), "refdes": "R2"}
    with pytest.raises(A.AdapterError) as caught:
        _apply(owned.app, [_operation(), second])
    details = caught.value.details
    assert len(details["applied"]) == 1 and not owned.persisted
    assert details["display_failure"] == {
        "stage": "display_apply",
        "reason": "display_apply_or_verification_failed",
        "operations_requested": 2,
        "operations_returned": 1,
        "operation_index": 1,
        "operation_call_attempted": False,
    }
    public = O._adapter_failure(caught.value)["details"]
    assert public["display_setter_fields_attempted"] is None
    assert public["display_setter_fields_observed"] is False
    assert public["display_setter_last_attempted_callpoint"] is None


@pytest.mark.parametrize("when", ["before_save", "after_reopen"])
def test_owned_non_target_changes_still_fail_complete_protection(owned, monkeypatch, when):
    snapshot = A._designer_snapshot

    def changed(*args):
        value = snapshot(*args)
        required = 2 if when == "before_save" else 3
        if len(owned.snapshots) == required:
            value["components"][6]["attributes"]["Part Number"] = "UNREQUESTED"
        return value

    monkeypatch.setattr(A, "_designer_snapshot", changed)
    with pytest.raises(A.AdapterError) as caught:
        _apply(owned.app, [_operation()])
    assert caught.value.code == "E_PROJECT_INVALID"
    assert caught.value.details["persistence_verified"] is False
    assert len(owned.persisted) == (0 if when == "before_save" else 1)


def _failure():
    return {
        "stage": "display_identity_preflight",
        "reason": "display_preflight_refused",
        "operations_requested": 2,
        "operations_returned": 0,
        "operation_index": None,
        "operation_call_attempted": False,
    }


def test_owner_failure_preserves_fixed_display_diagnostics_without_raw_identity():
    error = A.AdapterError(
        "E_VALIDATION",
        "private message and capsule",
        {
            "display_failure": _failure(),
            "stage": "private_stage_canary",
            "reason": "private_reason_canary",
            "identity": {"secret": "private_identity_canary"},
            "write_attempted": False,
        },
    )
    public = O._adapter_failure(error)
    text = json.dumps(public)
    assert "private_" not in text and "capsule" not in text
    details = public["details"]
    assert details["display_failure"] == _failure()
    assert details["stage"] == "display_identity_preflight"
    assert details["reason"] == "display_preflight_refused"
    assert details["native_display_failure_details_complete"] is True
    assert details["write_attempted"] is False
    # Preflight control-flow facts do not erase possibly dispatched native reads.
    assert details["native_request_dispatched"] is None and details["native_call_unknown"] is True


@pytest.mark.parametrize(
    "fault",
    ["extra", "stage", "stage_list", "reason", "bool_count", "bad_index", "contradictory_attempt"],
)
def test_invalid_display_diagnostics_do_not_leak_or_become_observed(fault):
    value = _failure()
    if fault == "extra":
        value["capsule"] = "private_capsule_canary"
    elif fault == "stage":
        value["stage"] = "private_stage_canary"
    elif fault == "stage_list":
        value["stage"] = ["private_stage_canary"]
    elif fault == "reason":
        value["reason"] = "private_reason_canary"
    elif fault == "bool_count":
        value["operations_requested"] = True
    elif fault == "bad_index":
        value["operation_index"] = 4
    else:
        value["operation_call_attempted"] = True
    error = A.AdapterError(
        "E_VALIDATION",
        "private_message_canary",
        {
            "display_failure": value,
            "stage": "private_stage_canary",
            "reason": "private_reason_canary",
        },
    )
    details = O._adapter_failure(error)["details"]
    assert details["native_display_failure_details_complete"] is False
    assert "display_failure" not in details and "stage" not in details
    assert "private_" not in json.dumps(details)
    assert details["native_call_unknown"] is True


def test_fixed_setter_attempt_prefix_is_observed_without_claiming_a_return():
    failure = {
        **_failure(),
        "stage": "display_apply",
        "reason": "display_apply_or_verification_failed",
        "operation_index": 0,
        "operation_call_attempted": True,
    }
    error = A.AdapterError(
        "E_SERVER",
        "synthetic setter failure",
        {"display_failure": failure, "attempted_fields": ["Value", "SetLocation"]},
    )
    details = O._adapter_failure(error)["details"]
    assert details["display_setter_fields_attempted"] == ["Value", "SetLocation"]
    assert details["display_setter_fields_observed"] is True
    assert details["display_setter_last_attempted_callpoint"] == "SetLocation"
    assert details["native_call_returned"] is None and details["native_call_unknown"] is True


def test_missing_setter_observation_is_null_for_preflight_or_sheet_failure():
    details = O._adapter_failure(
        A.AdapterError(
            "E_VALIDATION", "synthetic preflight refusal", {"display_failure": _failure()}
        )
    )["details"]
    assert details["display_setter_fields_attempted"] is None
    assert details["display_setter_fields_observed"] is False
    assert details["display_setter_last_attempted_callpoint"] is None


@pytest.mark.parametrize(
    "fields",
    [
        ["Value"],
        ["Value", "private_field_canary"],
        ["SetLocation"],
        ["Value", "SetLocation", "Visible", "Size", "private_field_canary"],
        [],
        "Value",
    ],
)
def test_bad_setter_attempt_fields_are_unobserved_and_never_echoed(fields):
    error = A.AdapterError(
        "E_SERVER", "synthetic failure", {"display_failure": _failure(), "attempted_fields": fields}
    )
    details = O._adapter_failure(error)["details"]
    assert details["display_setter_fields_attempted"] is None
    assert details["display_setter_fields_observed"] is False
    assert details["display_setter_last_attempted_callpoint"] is None
    assert "private_field_canary" not in json.dumps(details)
