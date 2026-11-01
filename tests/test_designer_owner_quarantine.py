"""Known False replies allow only observation and empty Quit, never another write."""

from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest
from test_designer_native_owner import _public_submit_context
from test_designer_owner_dispatch import FACTORY
from test_designer_owner_dispatch import scene as _scene_fixture

from xpedition_cli import designer_host_bootstrap as B
from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_client as C
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A

scene = _scene_fixture


def _quarantine(scene, tmp_path):
    return D.quarantine_rejected_creation(
        scene.grant,
        _rejected(scene, tmp_path),
        sdk_operation_id="op-failed-create",
        command_id="owner-command-" + "b" * 32,
    )


def _rejected(scene, tmp_path):
    scene.owner.project = str(tmp_path / "failed.prj")
    scene.owner.project_origin = {"kind": "new_project", "sdk_operation_id": "op-failed-create"}
    return A.AdapterError("E_PROJECT_INVALID", "negative reply", {"acknowledgement": False})


def test_known_false_retains_bridge_target_and_original_failure_but_blocks_engineering_writes(
    scene, monkeypatch, tmp_path
):  # noqa: F811
    error = _rejected(scene, tmp_path)
    prior = copy.deepcopy(error.details)
    initial = tmp_path / "initial.json"
    initial.write_bytes(b"immutable original unknown")
    q = D.quarantine_rejected_creation(
        scene.grant,
        error,
        sdk_operation_id="op-failed-create",
        command_id="owner-command-" + "b" * 32,
    )
    assert q["engineering_writes_enabled"] is False
    assert scene.owner.readonly_quarantine == q and scene.owner.project.endswith("failed.prj")
    calls = []
    monkeypatch.setattr(
        A, "dispatch", lambda method, params: calls.append(method) or {"method": method}
    )
    for method, params in (
        ("identity", {}),
        ("read_project", {}),
        ("snapshot", {"observe_only": True}),
    ):
        D.dispatch(scene.grant, method, params)
    for method in D.WRITE_METHODS - {"close"}:
        with pytest.raises(D.DispatchError, match="readonly_quarantine_blocks") as failed:
            D.dispatch(scene.grant, method, {})
        assert failed.value.dispatch_started is False
    assert calls == ["identity", "read_project", "snapshot"]
    assert error.details == prior and initial.read_bytes() == b"immutable original unknown"


@pytest.mark.parametrize("fault", ["positive_ack", "wrong_code", "lost_generation"])
def test_quarantine_is_not_a_json_flag_and_requires_exact_negative_reply_and_live_generation(
    scene, monkeypatch, tmp_path, fault
):  # noqa: F811
    error = _rejected(scene, tmp_path)
    if fault == "positive_ack":
        error.details["acknowledgement"] = True
    if fault == "wrong_code":
        error.code = "E_COM"
    if fault == "lost_generation":
        scene.alive = False
    if fault == "lost_generation":
        with pytest.raises(D.DispatchError):
            D.quarantine_rejected_creation(
                scene.grant,
                error,
                sdk_operation_id="op-failed-create",
                command_id="owner-command-" + "b" * 32,
            )
    else:
        assert (
            D.quarantine_rejected_creation(
                scene.grant,
                error,
                sdk_operation_id="op-failed-create",
                command_id="owner-command-" + "b" * 32,
            )
            is None
        )
    assert scene.owner.readonly_quarantine is None


def test_partial_open_project_stays_readonly_and_cannot_quit_as_empty(scene, tmp_path):  # noqa: F811
    error = _rejected(scene, tmp_path)
    scene.document = {"open": True, "path": B.W._path(scene.owner.project)}
    D.quarantine_rejected_creation(
        scene.grant,
        error,
        sdk_operation_id="op-failed-create",
        command_id="owner-command-" + "b" * 32,
    )
    with pytest.raises(O.OwnerError, match="fresh_empty_project_required"):
        scene.owner.quit_empty()
    assert scene.owner.quit_attempted is False


def test_quarantine_queue_rejects_new_writes_before_creating_command_directory(
    tmp_path, monkeypatch
):
    ticket, root = _public_submit_context(tmp_path, monkeypatch)
    monkeypatch.setattr(
        O.L,
        "_events",
        lambda *a: [{"phase": "public_dispatch_ready"}, {"phase": "public_readonly_quarantine"}],
    )
    with pytest.raises(O.OwnerError, match="readonly_quarantine_blocks"):
        O.submit_owner_adapter_command(
            ticket, "apply_changeset", {}, sdk_operation_id="op-write", authorization_binding={}
        )
    assert list((root / "commands").iterdir()) == []
    answer = O.submit_owner_adapter_command(
        ticket, "identity", {}, sdk_operation_id="query-read", authorization_binding={}
    )
    assert answer["status"] == "unknown" and len(list((root / "commands").iterdir())) == 1


def test_quarantine_client_blocks_write_without_transport_and_keeps_observation_service(
    monkeypatch,
):
    assert (
        C._service_unavailable(
            {"phase": "public_readonly_quarantine", "owner_state": "verified_alive"},
            require_ready=True,
        )
        is False
    )
    monkeypatch.setattr(C, "_ticket", lambda locator: (object(), object(), None, None))
    monkeypatch.setattr(C, "_authorization", lambda locator: ("op-write", {}))
    monkeypatch.setattr(
        C,
        "_service_observation",
        lambda *a: {"phase": "public_readonly_quarantine", "owner_state": "verified_alive"},
    )
    with pytest.raises(Exception) as rejected:
        C.call_owner(NS(), "apply_changeset", {}, 1)
    assert rejected.value.code == "E_CONFLICT"
    assert rejected.value.details["native_request_dispatched"] is False


def test_real_observe_only_snapshot_rejection_never_opens_and_preserves_quarantine(
    scene, monkeypatch, tmp_path
):  # noqa: F811
    quarantine = _quarantine(scene, tmp_path)
    before = copy.deepcopy(quarantine)
    native_writes = []

    def forbidden(*a, **k):
        native_writes.append(True)
        pytest.fail("A quarantined read cannot open, activate, save or close a project")

    monkeypatch.setattr(A, "_import_com", lambda: (scene.com, NS(GetActiveObject=forbidden)))
    monkeypatch.setattr(A, "_viewdraw_application", FACTORY)
    monkeypatch.setattr(A, "_ensure_project", forbidden)
    monkeypatch.setattr(A, "_open_project_answering", forbidden)
    scene.app.OpenProject = scene.app.Activate = scene.app.CloseProject = forbidden
    with pytest.raises(A.AdapterError) as failed:
        D.dispatch(
            scene.grant,
            "snapshot",
            {"domain": "schematic", "project": scene.owner.project, "observe_only": True},
        )
    error = failed.value
    assert error.code == "E_CONFLICT"
    details = copy.deepcopy(error.details)
    before_checks = len(scene.calls)
    assert D.continue_quarantined_observation_rejection(scene.grant, error) is True
    assert len(scene.calls) > before_checks
    assert D.continue_quarantined_observation_rejection(scene.grant, error) is False
    assert error.code == "E_CONFLICT" and error.details == details
    assert scene.owner.readonly_quarantine == before
    assert scene.owner.project.endswith("failed.prj") and not scene.grant.closed
    assert scene.grant.busy is False and D.current_application() is None
    assert native_writes == []
    assert O._adapter_failure(error)["details"]["native_call_unknown"] is True


@pytest.mark.parametrize("method", ["identity", "read_project", "snapshot"])
def test_quarantined_read_rejection_requires_current_native_binding_after_failure(
    scene, monkeypatch, tmp_path, method
):  # noqa: F811
    _quarantine(scene, tmp_path)
    error = A.AdapterError("E_NOT_FOUND", "synthetic missing observation")

    def rejected(*a, **k):
        raise error

    monkeypatch.setattr(A, "dispatch", rejected)
    with pytest.raises(A.AdapterError):
        D.dispatch(scene.grant, method, {"observe_only": True})
    scene.alive = False
    with pytest.raises(D.DispatchError, match="original_launch_handle_generation_changed"):
        D.continue_quarantined_observation_rejection(scene.grant, error)
    assert scene.owner.readonly_quarantine is not None
    assert scene.grant.closed is False


@pytest.mark.parametrize(
    "fault", ["forged", "not_quarantined", "rpc_failure", "write", "not_observe_only"]
)
def test_method_name_or_json_details_do_not_allow_failure_continuation(
    scene, monkeypatch, tmp_path, fault
):  # noqa: F811
    if fault != "not_quarantined":
        _quarantine(scene, tmp_path)
    error = A.AdapterError(
        "E_COM" if fault == "rpc_failure" else "E_CONFLICT",
        "synthetic rejection",
        {"observe_only": True, "quarantine_service_preserved": True},
    )

    def rejected(*a, **k):
        raise error

    monkeypatch.setattr(A, "dispatch", rejected)
    method = "save" if fault == "write" else "snapshot"
    params = {} if fault == "not_observe_only" else {"observe_only": True}
    if fault != "forged":
        with pytest.raises((A.AdapterError, D.DispatchError)):
            D.dispatch(scene.grant, method, params)
    assert D.continue_quarantined_observation_rejection(scene.grant, error) is False


def test_canonical_drift_after_quarantined_read_rejection_cannot_preserve_service(
    scene, monkeypatch, tmp_path
):  # noqa: F811
    _quarantine(scene, tmp_path)
    error = A.AdapterError("E_CONFLICT", "synthetic pure-read rejection")

    def rejected(*a, **k):
        raise error

    monkeypatch.setattr(A, "dispatch", rejected)
    with pytest.raises(A.AdapterError):
        D.dispatch(scene.grant, "snapshot", {"observe_only": True})
    scene.unknown = object()
    with pytest.raises(D.DispatchError, match="retained_application_canonical_changed"):
        D.continue_quarantined_observation_rejection(scene.grant, error)
