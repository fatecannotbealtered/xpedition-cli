"""Pure fixture model: no COM, process query or connection-runtime files."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import threading

import pytest

from xpedition_cli import designer_connection as C


def _anchor(**changes):
    values = {
        "launch_intent_id": "private-fixture-intent",
        "session_epoch": "private-fixture-epoch",
        "expected_peer": C.ProcessGeneration(123, 456, r"C:\licensed\viewdraw.exe"),
        "project_scope_id": "private-fixture-scope",
        "template_sha256": "a" * 64,
    }
    values.update(changes)
    return C.LaunchAnchor(**values)


def _ready(*, anchor=None, application=None):
    anchor = anchor or _anchor()
    application = application if application is not None else object()
    verifier = C._FixtureTransportVerifier(anchor, application)
    session = C.ConnectionSession(anchor, verifier=verifier)
    message = verifier.issue_capture()
    session.capture_host_application(message)
    session.mark_startup_returned(verifier.issue_startup_return(message))
    handle = session.materialize()
    return session, handle, verifier, application


def _initial_hash(observation):
    return hashlib.sha256(
        json.dumps(observation["operation"]["initial_observation"], sort_keys=True).encode()
    ).hexdigest()


def test_default_verifier_cannot_be_enabled_by_labels_or_claimed_pid():
    session = C.ConnectionSession(_anchor())
    assert C.native_connection_enabled() is False
    for evidence in (
        True,
        {"qualified": True, "pid": 123, "birth_100ns": 456, "project": "same"},
        "self-reported-secret-random-string",
        object(),
    ):
        with pytest.raises(C.ConnectionError, match="native_transport_unqualified"):
            session.capture_host_application(evidence)
    assert session.status()["state"] == "awaiting_host"
    assert session.status()["native_admitted"] is False
    assert session.status()["startup_returned"] is None
    assert session.status()["startup_return_observation"] == "unobserved"
    with pytest.raises(C.ConnectionError, match="unsupported_transport_verifier"):
        C.ConnectionSession(_anchor(), verifier=type("CallerTrusted", (), {"qualified": True})())


@pytest.mark.parametrize(
    "changed",
    [
        {"peer": C.ProcessGeneration(124, 456, r"C:\licensed\viewdraw.exe")},
        {"peer": C.ProcessGeneration(123, 457, r"C:\licensed\viewdraw.exe")},
        {"peer": C.ProcessGeneration(123, 456, r"C:\foreign\viewdraw.exe")},
        {"anchor": _anchor(session_epoch="restarted-epoch")},
        {"anchor": _anchor(launch_intent_id="other-launch")},
        {"anchor": _anchor(project_scope_id="other-scope")},
        {"anchor": _anchor(template_sha256="b" * 64)},
        {"source": "external_GetActiveObject"},
        {"application": object()},
    ],
)
def test_peer_epoch_scope_template_and_host_object_are_all_bound(changed):
    anchor = _anchor()
    application = object()
    verifier = C._FixtureTransportVerifier(anchor, application)
    session = C.ConnectionSession(anchor, verifier=verifier)
    with pytest.raises(C.ConnectionError):
        session.capture_host_application(verifier.issue_capture(**changed))
    assert session.status()["state"] == "awaiting_host"
    assert session.status()["owner_materialized"] is False
    assert session.status()["cleanup_verified"] is None


def test_same_project_and_type_do_not_accept_foreign_application():
    class Application:
        project = "identical-project"

    anchor = _anchor()
    host, foreign = Application(), Application()
    verifier = C._FixtureTransportVerifier(anchor, host)
    session = C.ConnectionSession(anchor, verifier=verifier)
    with pytest.raises(C.ConnectionError, match="host_application_provenance_mismatch"):
        session.capture_host_application(verifier.issue_capture(application=foreign))


def test_callback_capture_has_no_application_getters_and_needs_separate_barriers():
    class NoGetters:
        def __getattribute__(self, name):
            raise AssertionError("callback must not inspect an application")

    anchor, application = _anchor(), NoGetters()
    verifier = C._FixtureTransportVerifier(anchor, application)
    session = C.ConnectionSession(anchor, verifier=verifier)
    message = verifier.issue_capture()
    callback_result = []
    callback = threading.Thread(
        target=lambda: callback_result.append(session.capture_host_application(message))
    )
    callback.start()
    callback.join(1)
    assert not callback.is_alive()
    assert callback_result[0]["state"] == "host_captured"
    assert callback_result[0]["startup_returned"] is None
    assert callback_result[0]["startup_return_observation"] == "unobserved"
    assert callback_result[0]["owner_materialized"] is False
    with pytest.raises(C.ConnectionError, match="startup_return_barrier_required"):
        session.materialize()
    with pytest.raises(C.ConnectionError, match="startup_return_proof_missing"):
        session.mark_startup_returned(True)
    session.mark_startup_returned(verifier.issue_startup_return(message))
    assert session.status()["state"] == "host_captured"
    assert session.status()["startup_returned"] is True
    assert session.status()["startup_return_observation"] == "fixture_only"
    handle = session.materialize()
    assert type(handle) is C.SessionHandle
    assert session.status()["state"] == "offline_ready"
    assert session.status()["native_admitted"] is False


def test_barrier_for_another_capture_is_rejected():
    anchor = _anchor()
    verifier = C._FixtureTransportVerifier(anchor, object())
    session = C.ConnectionSession(anchor, verifier=verifier)
    session.capture_host_application(verifier.issue_capture())
    unrelated_message = verifier.issue_capture()
    with pytest.raises(C.ConnectionError, match="startup_return_proof_mismatch"):
        session.mark_startup_returned(verifier.issue_startup_return(unrelated_message))
    assert session.status()["startup_returned"] is None
    assert session.status()["startup_return_observation"] == "unobserved"


def test_owner_thread_and_exact_same_retained_object_gate_commands():
    session, handle, verifier, application = _ready()
    received = []
    command = verifier.issue_command(
        lambda bound: received.append((bound, threading.current_thread()))
    )
    attempts = []

    def other_thread():
        try:
            session.execute(handle, "fixture-operation-wrong-thread", command)
        except C.ConnectionError as error:
            attempts.append(str(error))

    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join(1)
    assert attempts == ["owner_thread_required"]
    assert received == []
    session.execute(handle, "fixture-operation-correct", command)
    assert received == [(application, threading.current_thread())]
    with pytest.raises(C.ConnectionError, match="offline_fixture_command_required"):
        session.execute(handle, "fixture-operation-arbitrary", lambda app: None)


def test_thread_object_identity_is_required_even_if_ident_matches():
    session, handle, verifier, _ = _ready()
    session._owner_thread = object()  # Simulate a recycled numeric thread identity.
    with pytest.raises(C.ConnectionError, match="owner_thread_required"):
        session.execute(
            handle, "fixture-operation-recycled-thread", verifier.issue_command(lambda app: None)
        )


def test_forged_handle_cannot_dispatch_and_operation_is_not_replayed():
    session, handle, verifier, _ = _ready()
    calls = []
    command = verifier.issue_command(lambda app: calls.append("called"))
    with pytest.raises(C.ConnectionError, match="connection_handle_expired_or_unrecognized"):
        session.execute(C.SessionHandle(object()), "fixture-operation-forged", command)
    first = session.execute(handle, "fixture-operation-once", command)
    with pytest.raises(C.ConnectionError, match="operation_already_received_no_replay"):
        session.execute(handle, "fixture-operation-once", command)
    assert calls == ["called"]
    assert first["operation"]["received"] is True
    assert first["operation"]["execution_completed"] is True
    assert first["operation"]["initial_observation"]["outcome"] == "unknown"
    assert first["cleanup_verified"] is None
    assert first["owner_exited"] is None


def test_observer_timeout_does_not_cancel_replay_cleanup_or_revise_unknown():
    shared = {}
    entered, release, done = threading.Event(), threading.Event(), threading.Event()

    def owner():
        session, handle, verifier, application = _ready()
        shared.update(session=session, application=application)

        def blocked(app):
            assert app is application
            shared["calls"] = shared.get("calls", 0) + 1
            entered.set()
            assert release.wait(2), "fixture teardown must release the owner"

        command = verifier.issue_command(blocked, requires_save=True)
        try:
            session.execute(handle, "fixture-operation-blocked", command)
        finally:
            session.cleanup()
            session.mark_owner_exited()
            done.set()

    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert entered.wait(1)
        session = shared["session"]
        first = session.observe("fixture-operation-blocked", timeout_seconds=0.01)
        original_hash = _initial_hash(first)
        assert first["operation"]["execution_completed"] is None
        assert first["operation"]["save_completed"] is None
        assert first["cleanup_verified"] is None
        assert first["owner_exited"] is None
        assert shared["calls"] == 1
        session.disconnect()
        assert session.status()["inflight_commands"] == 1
        assert session.status()["cleanup_verified"] is None
    finally:
        release.set()  # Test-only release, not a product stop/cancellation feature.
        thread.join(2)
    assert done.is_set() and not thread.is_alive()
    later = session.observe("fixture-operation-blocked")
    assert _initial_hash(later) == original_hash
    assert later["operation"]["execution_completed"] is True
    assert later["operation"]["connection_valid_at_return"] is False
    assert later["operation"]["save_completed"] is None
    assert later["operation"]["failure_stage"] == "connection_invalidated_during_execution"
    assert later["state"] == "owner_exited"
    assert later["native_admitted"] is False
    assert shared["calls"] == 1


def test_save_is_separate_from_return_and_partial_save_remains_failed():
    session, handle, verifier, _ = _ready()
    command = verifier.issue_command(lambda app: None, requires_save=True)
    returned = session.execute(handle, "fixture-operation-partial", command)
    assert returned["operation"]["execution_completed"] is True
    assert returned["operation"]["save_completed"] is None
    session.record_save_result(handle, "fixture-operation-partial", saved=False)
    failed = session.observe("fixture-operation-partial")
    assert failed["operation"]["save_completed"] is False
    assert failed["operation"]["failure_stage"] == "save"
    with pytest.raises(C.ConnectionError, match="save_result_already_recorded"):
        session.record_save_result(handle, "fixture-operation-partial", saved=True)
    assert _initial_hash(returned) == _initial_hash(failed)
    assert session.cleanup()["cleanup_verified"] is True
    assert session.status()["owner_exited"] is None
    assert session.mark_owner_exited()["owner_exited"] is True
    assert session.observe("fixture-operation-partial")["operation"]["save_completed"] is False


def test_successful_save_does_not_imply_cleanup_or_exit():
    session, handle, verifier, _ = _ready()
    session.execute(
        handle,
        "fixture-operation-saved",
        verifier.issue_command(lambda app: None, requires_save=True),
    )
    session.record_save_result(handle, "fixture-operation-saved", saved=True)
    result = session.observe("fixture-operation-saved")
    assert result["operation"]["save_completed"] is True
    assert result["cleanup_verified"] is None
    assert result["owner_exited"] is None
    with pytest.raises(C.ConnectionError, match="successful_cleanup_required_before_model_exit"):
        session.mark_owner_exited()


def test_normal_return_does_not_interpret_business_result_or_save():
    session, handle, verifier, _ = _ready()
    result = session.execute(
        handle,
        "fixture-operation-return-false",
        verifier.issue_command(lambda app: False, requires_save=True),
    )
    assert result["operation"]["execution_completed"] is True
    assert result["operation"]["execution_completion_basis"] == "fixture_callable_return_only"
    assert result["operation"]["save_completed"] is None
    assert result["native_admitted"] is False


def test_reentrant_owner_command_is_rejected_before_second_dispatch(monkeypatch):
    session, handle, verifier, _ = _ready()
    calls, resolved, initial = [], [], []
    nested_command = verifier.issue_command(lambda app: calls.append("nested"))
    original_resolver = verifier.command

    def resolve(command):
        resolved.append(command)
        return original_resolver(command)

    monkeypatch.setattr(verifier, "command", resolve)

    def outer(app):
        initial.append(_initial_hash(session.observe("fixture-operation-outer")))
        with pytest.raises(C.ConnectionError, match="owner_command_already_inflight"):
            session.execute(
                handle,
                "fixture-operation-reentrant",
                nested_command,
            )
        calls.append("outer")

    outer_command = verifier.issue_command(outer)
    result = session.execute(handle, "fixture-operation-outer", outer_command)
    assert calls == ["outer"]
    assert resolved == [outer_command]
    assert result["inflight_commands"] == 0
    assert result["operation"]["execution_completed"] is True
    assert result["operation"]["initial_observation"]["outcome"] == "unknown"
    assert initial == [_initial_hash(result)]
    with pytest.raises(C.ConnectionError, match="operation_not_owned"):
        session.observe("fixture-operation-reentrant")


def test_model_cleanup_drops_own_reference_graph_without_native_release_claim():
    session, _, verifier, _ = _ready()
    result = session.cleanup()
    assert result["cleanup_verified"] is True
    assert result["cleanup_scope"] == "offline_connection_slots_and_fixture_release_only"
    assert result["fixture_release_completed"] is None
    assert result["native_reference_release"] is None
    assert session._application is None and session._capture is None
    assert verifier._host_application is None
    assert verifier._messages == verifier._barriers == verifier._commands == {}


def test_cleanup_failure_cannot_be_relabelled_as_success():
    session, handle, verifier, application = _ready()
    result = session.execute(
        handle, "fixture-operation-cleanup", verifier.issue_command(lambda app: None)
    )

    def failed_release(bound):
        assert bound is application
        raise RuntimeError("private endpoint and external exception text must not escape")

    failed = session.cleanup(failed_release)
    assert failed["cleanup_verified"] is False
    assert failed["fixture_release_completed"] is False
    assert failed["native_reference_release"] is None
    assert failed["owner_exited"] is None
    assert failed["state"] == "cleanup_failed"
    assert "private endpoint" not in json.dumps(failed)
    with pytest.raises(C.ConnectionError, match="cleanup_already_attempted"):
        session.cleanup()
    with pytest.raises(C.ConnectionError, match="successful_cleanup_required_before_model_exit"):
        session.mark_owner_exited()
    assert _initial_hash(result) == _initial_hash(session.observe("fixture-operation-cleanup"))


def test_slow_finalizer_never_holds_the_observers_connection_lock():
    entered, release, finalizer_done = threading.Event(), threading.Event(), threading.Event()
    owner_done, observer_done = threading.Event(), threading.Event()
    shared, observed, errors = {}, [], []

    class SlowFinalizer:
        def __del__(self):
            entered.set()
            release.wait()  # Only test teardown releases this fixture finalizer.
            finalizer_done.set()

    def owner():
        try:
            session, handle, verifier, application = _ready(application=SlowFinalizer())
            result = session.execute(
                handle,
                "fixture-operation-finalizer",
                verifier.issue_command(lambda bound: None),
            )
            shared.update(session=session, original_hash=_initial_hash(result))
            del application
            shared["cleanup"] = session.cleanup()
        except BaseException as error:
            errors.append(type(error).__name__)
        finally:
            owner_done.set()

    def observer():
        try:
            session = shared["session"]
            observed.append(session.status())
            observed.append(session.observe("fixture-operation-finalizer", timeout_seconds=0.01))
            observed.append(session.disconnect())
        except BaseException as error:
            errors.append(type(error).__name__)
        finally:
            observer_done.set()

    owner_thread = threading.Thread(target=owner)
    observer_thread = None
    owner_thread.start()
    try:
        assert entered.wait(1), "owner must reach its last opaque reference release"
        observer_thread = threading.Thread(target=observer)
        observer_thread.start()
        assert observer_done.wait(1), "slow finalizer must not block status or observation"
        assert finalizer_done.is_set() is False
        assert owner_done.is_set() is False
        assert observed[0]["state"] == observed[1]["state"] == "cleanup_pending"
        assert observed[0]["cleanup_verified"] is observed[1]["cleanup_verified"] is None
        assert observed[2]["state"] == "disconnected"
        assert observed[2]["cleanup_verified"] is None
        assert _initial_hash(observed[1]) == shared["original_hash"]
    finally:
        release.set()
        owner_thread.join(2)
        if observer_thread is not None:
            observer_thread.join(2)
    assert not owner_thread.is_alive()
    assert observer_thread is not None and not observer_thread.is_alive()
    assert errors == []
    assert finalizer_done.is_set() and owner_done.is_set()
    assert shared["cleanup"]["state"] == "closed"
    assert shared["cleanup"]["cleanup_verified"] is True
    assert shared["session"].status()["state"] != "offline_ready"


def test_restart_needs_new_session_and_rejects_old_handle_and_old_proof():
    old, old_handle, old_verifier, _ = _ready()
    old_command = old_verifier.issue_command(lambda app: pytest.fail("must not replay"))
    old.disconnect()
    with pytest.raises(C.ConnectionError, match="connection_handle_expired_or_unrecognized"):
        old.execute(old_handle, "fixture-operation-old", old_command)
    with pytest.raises(C.ConnectionError, match="connection_not_accepting_host"):
        old.capture_host_application(old_verifier.issue_capture())
    new, new_handle, verifier, _ = _ready(
        anchor=_anchor(
            session_epoch="restarted",
            expected_peer=C.ProcessGeneration(123, 999, r"C:\licensed\viewdraw.exe"),
        )
    )
    with pytest.raises(C.ConnectionError, match="connection_handle_expired_or_unrecognized"):
        new.execute(
            old_handle, "fixture-operation-old-handle", verifier.issue_command(lambda app: None)
        )
    with pytest.raises(C.ConnectionError, match="operation_not_owned"):
        new.observe("fixture-operation-old")
    assert new_handle is not old_handle
    with pytest.raises(C.ConnectionError, match="host_origin_proof_reused"):
        other = C.ConnectionSession(old._anchor, verifier=old_verifier)
        used_message = next(iter(old_verifier._used))
        other.capture_host_application(used_message)


def test_command_failure_and_operation_limit_do_not_dispatch_again(monkeypatch):
    monkeypatch.setattr(C, "MAX_OPERATIONS", 1)
    session, handle, verifier, _ = _ready()
    calls = []

    def fail(app):
        calls.append("first")
        raise RuntimeError("private-error")

    with pytest.raises(C.ConnectionError, match="fixture_command_failed"):
        session.execute(handle, "fixture-operation-failed", verifier.issue_command(fail))
    observed = session.observe("fixture-operation-failed")
    assert observed["operation"]["execution_completed"] is False
    assert observed["operation"]["save_completed"] is None
    assert "private-error" not in json.dumps(observed)
    with pytest.raises(C.ConnectionError, match="offline_operation_limit"):
        session.execute(
            handle,
            "fixture-operation-over-limit",
            verifier.issue_command(lambda app: calls.append("second")),
        )
    assert calls == ["first"]


@pytest.mark.parametrize("budget", [-1, 1.01, float("inf"), float("nan"), True, "1"])
def test_observation_budget_is_bounded_and_pure(budget):
    session, handle, verifier, _ = _ready()
    session.execute(handle, "fixture-operation-observe", verifier.issue_command(lambda app: None))
    with pytest.raises(C.ConnectionError, match="bounded_observation_budget_required"):
        session.observe("fixture-operation-observe", timeout_seconds=budget)


def test_safe_snapshots_contain_no_identity_capabilities_or_application():
    anchor = _anchor()
    session, handle, verifier, application = _ready(anchor=anchor)
    result = session.execute(
        handle, "fixture-operation-safe", verifier.issue_command(lambda app: None)
    )
    rendered = json.dumps(result)
    for private in (
        anchor.launch_intent_id,
        anchor.session_epoch,
        anchor.project_scope_id,
        anchor.expected_peer.image,
    ):
        assert private not in rendered
    assert str(id(application)) not in rendered
    assert str(id(handle._token)) not in rendered
    result["operation"]["initial_observation"]["outcome"] = "forged-success"
    assert (
        session.observe("fixture-operation-safe")["operation"]["initial_observation"]["outcome"]
        == "unknown"
    )
    assert dataclasses.is_dataclass(anchor)
