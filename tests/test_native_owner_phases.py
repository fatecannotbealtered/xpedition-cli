"""Callback-level no-value lifecycle controls; no COM, CAD or journal access."""

from __future__ import annotations

from collections import Counter

import pytest

from scripts.native_acceptance.owner_phases import NoeditState, run_noedit


class Owner:
    def __init__(self, failures=None, values=None):
        self.calls = []
        self.failures = failures or {}
        self.values = values or {}
        self.native_loads = 0
        self.native_unloads = 0

    def call(self, phase, default):
        self.calls.append(phase)
        if phase in self.failures:
            raise self.failures[phase]
        return self.values.get(phase, default)

    def baseline_read(self):
        return self.call("baseline_read", {"project": "owned-F", "Expansion": 12})

    def verify_identity(self):
        self.calls.append("verify_identity")
        if self.calls.count("verify_identity") == 2 and self.values.get("identity_lost"):
            raise ReferenceError("authenticated Document reference expired")
        return True

    def load(self):
        self.native_loads += 1
        return self.call("load", None)

    def read_loaded(self):
        return self.call("read_loaded", {"Expansion": 12, "linear_unit": "th"})

    def sample_logs(self):
        return self.call("sample_logs", {"complete": False, "sample": "live source"})

    def unload(self):
        self.native_unloads += 1
        return self.call("unload", None)

    def release_constraints(self):
        return self.call("release_constraints", True)

    def after_read(self):
        return self.call("after_read", {"project": "owned-F", "Expansion": 12})

    def compare_readback(self, before, after):
        return self.call("compare_readback", before == after)


def assert_no_replay(owner, original):
    calls = list(owner.calls)
    refused = run_noedit(owner)
    assert refused["primary_error"]["phase"] == "replay_refused"
    assert set(refused["callback_attempts"].values()) == {0}
    assert owner.calls == calls
    assert original["callback_attempts"] == {
        name: Counter(calls)[name] for name in original["callback_attempts"]
    }


def test_normal_sequence_releases_local_references_and_refuses_replay():
    owner = Owner()
    state = NoeditState()
    result = run_noedit(owner, state)
    assert owner.calls == [
        "baseline_read",
        "verify_identity",
        "load",
        "read_loaded",
        "sample_logs",
        "verify_identity",
        "unload",
        "release_constraints",
        "after_read",
        "compare_readback",
    ]
    assert result["control_complete"] is True
    assert result["cleanup"]["complete"] is True
    assert result["cleanup"]["service_lock_released_proven"] is False
    assert result["facts"]["design_load_state"] == "unloaded_by_void_return"
    assert (owner.native_loads, owner.native_unloads) == (1, 1)
    assert_no_replay(owner, result)
    replaced = run_noedit(owner, NoeditState())
    assert replaced["primary_error"]["phase"] == "preflight"
    assert set(replaced["callback_attempts"].values()) == {0}


def test_missing_dependency_fails_before_any_native_dispatch():
    owner = Owner()
    owner.after_read = None
    result = run_noedit(owner)
    assert result["primary_error"]["phase"] == "preflight"
    assert owner.calls == []
    assert (owner.native_loads, owner.native_unloads) == (0, 0)
    assert result["facts"]["design_load_state"] == "not_dispatched"


def test_baseline_exception_stops_before_load_with_precise_known_stage():
    owner = Owner(failures={"baseline_read": PermissionError("baseline file unreadable")})
    result = run_noedit(owner)
    assert owner.calls == ["baseline_read"]
    assert result["primary_error"]["type"] == "PermissionError"
    assert result["facts"]["design_load_state"] == "not_dispatched"
    assert result["cleanup"]["known_loaded"] is False
    assert_no_replay(owner, result)


def test_load_exception_does_not_guess_unload_or_release():
    owner = Owner(failures={"load": TimeoutError("Load reply missing")})
    result = run_noedit(owner)
    assert result["primary_error"]["phase"] == "load"
    assert result["facts"]["design_load_state"] == "return_unknown"
    assert (owner.native_loads, owner.native_unloads) == (1, 0)
    assert "release_constraints" not in owner.calls
    assert_no_replay(owner, result)


def test_nonvoid_load_is_not_a_known_loaded_design():
    owner = Owner(values={"load": True})
    result = run_noedit(owner)
    assert result["primary_error"]["phase"] == "load_nonvoid"
    assert result["facts"]["design_load_state"] == "return_unknown"
    assert (owner.native_loads, owner.native_unloads) == (1, 0)
    assert_no_replay(owner, result)


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("live log grew during capture"),
        FileNotFoundError("log rotated before copy"),
        PermissionError("temporary log sharing denial"),
    ],
)
def test_noncritical_log_callback_exception_never_interrupts_known_unload(failure):
    owner = Owner(failures={"sample_logs": failure})
    result = run_noedit(owner)
    assert (owner.native_loads, owner.native_unloads) == (1, 1)
    assert result["cleanup"]["complete"] is True
    assert result["control_complete"] is True
    assert result["primary_error"] is None
    assert result["errors"] == [
        {
            "phase": "sample_logs",
            "critical": False,
            "type": type(failure).__name__,
            "message": str(failure),
        }
    ]
    assert_no_replay(owner, result)


def test_critical_loaded_read_exception_is_preserved_while_known_unload_completes():
    owner = Owner(failures={"read_loaded": ValueError("required named field missing")})
    result = run_noedit(owner)
    assert (owner.native_loads, owner.native_unloads) == (1, 1)
    assert result["cleanup"]["complete"] is True
    assert result["primary_error"]["phase"] == "read_loaded"
    assert result["facts"]["loaded_read_completed"] is False
    assert result["control_complete"] is False
    assert result["facts"]["readback_equal"] is True
    assert_no_replay(owner, result)


def test_identity_loss_after_load_preserves_scene_without_cleanup_writes():
    owner = Owner(values={"identity_lost": True})
    result = run_noedit(owner)
    assert (owner.native_loads, owner.native_unloads) == (1, 0)
    assert result["primary_error"]["type"] == "ReferenceError"
    assert result["cleanup"]["known_loaded"] is True
    assert result["cleanup"]["complete"] is False
    assert "release_constraints" not in owner.calls
    assert "after_read" not in owner.calls
    assert_no_replay(owner, result)


def test_unload_exception_is_not_swallowed_or_retried():
    owner = Owner(failures={"unload": RuntimeError("formal UnLoad failed")})
    result = run_noedit(owner)
    assert (owner.native_loads, owner.native_unloads) == (1, 1)
    assert result["primary_error"]["phase"] == "unload"
    assert result["cleanup"]["unload_attempted"] is True
    assert result["cleanup"]["unload_returned_void"] is False
    assert "release_constraints" not in owner.calls
    assert_no_replay(owner, result)


def test_nonvoid_unload_stops_before_local_reference_release():
    owner = Owner(values={"unload": False})
    result = run_noedit(owner)
    assert result["primary_error"]["phase"] == "unload_nonvoid"
    assert result["cleanup"]["complete"] is False
    assert "release_constraints" not in owner.calls
    assert_no_replay(owner, result)


def test_local_release_failure_preserves_unload_fact_but_no_service_lock_claim():
    owner = Owner(failures={"release_constraints": RuntimeError("local release incomplete")})
    result = run_noedit(owner)
    assert result["cleanup"]["unload_returned_void"] is True
    assert result["cleanup"]["complete"] is False
    assert result["cleanup"]["service_lock_released_proven"] is False
    assert result["primary_error"]["phase"] == "release_constraints"
    assert "after_read" not in owner.calls


def test_post_release_readback_drift_is_control_failure_with_cleanup_fact_retained():
    owner = Owner(values={"after_read": {"project": "owned-F", "Expansion": 14}})
    result = run_noedit(owner)
    assert result["primary_error"]["phase"] == "readback_not_equal"
    assert result["cleanup"]["complete"] is True
    assert result["control_complete"] is False
    assert result["observations"]["before"]["Expansion"] == 12
    assert result["observations"]["after"]["Expansion"] == 14
    assert_no_replay(owner, result)
