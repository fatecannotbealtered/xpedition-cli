"""One bounded no-value lifecycle through an already protected owner's callbacks.

This module imports no native adapter and owns no COM references. Callback
attempts are not native method counts: the supplied owner records those facts.
The local state prevents reentry/replay in that owner; durable operation history
and authorization remain the caller's responsibility.
"""

from __future__ import annotations

CALLBACKS = (
    "baseline_read",
    "verify_identity",
    "load",
    "read_loaded",
    "sample_logs",
    "unload",
    "release_constraints",
    "after_read",
    "compare_readback",
)


class NoeditState:
    """Retain in the same bounded owner; not a cross-process journal substitute."""

    def __init__(self):
        self.started = False
        self.last_phase = "not_started"


def _result():
    return {
        "schema": "owned-no-value-lifecycle/v1",
        "control_complete": False,
        "primary_error": None,
        "errors": [],
        "callback_attempts": dict.fromkeys(CALLBACKS, 0),
        "observations": {},
        "facts": {
            "identity_before_load_verified": False,
            "load_returned_void": False,
            "loaded_read_completed": False,
            "identity_before_unload_verified": False,
            "after_read_completed": False,
            "readback_equal": False,
            "design_load_state": "not_dispatched",
        },
        "cleanup": {
            "known_loaded": False,
            "unload_attempted": False,
            "unload_returned_void": False,
            "local_references_released": False,
            "complete": False,
            "service_lock_released_proven": False,
        },
        "native_method_counts_source": "owner callback records, not callback_attempts",
    }


def _error(result, phase, message, exc=None, *, critical=True):
    row = {"phase": phase, "message": message, "critical": critical}
    if exc is not None:
        row.update(type=type(exc).__name__, message=str(exc))
        if isinstance(getattr(exc, "hresult", None), int):
            row["hresult"] = exc.hresult
    result["errors"].append(row)
    if critical and result["primary_error"] is None:
        result["primary_error"] = row


def run_noedit(api, state=None):
    """Run Load/read/UnLoad once, with no fallback ordering or implicit writes.

    Identity checks and comparison must return the actual boolean True. Native
    void Load/UnLoad callbacks must return None. ``release_constraints`` only
    releases local references, returns True after completion, and cannot prove a
    vendor service/configuration lock was released. A known void Load permits
    one authorized UnLoad only after a fresh successful identity check, even if
    an intervening observation failed. Nothing executes from a finally clause.
    """
    result = _result()
    try:
        for name in CALLBACKS:
            if not callable(getattr(api, name, None)):
                raise ValueError("Required owner callback unavailable: " + name)
        retained = getattr(api, "_owner_noedit_state", None)
        if retained is not None and state is not None and retained is not state:
            raise ValueError("Cannot replace this owner's retained no-value lifecycle state")
        if state is None:
            state = retained if retained is not None else NoeditState()
        if not isinstance(state, NoeditState):
            raise ValueError("Explicit NoeditState required")
        if retained is None:
            api._owner_noedit_state = state
            if getattr(api, "_owner_noedit_state", None) is not state:
                raise ValueError("Cannot retain the no-value lifecycle state in this owner")
        if state.started:
            _error(result, "replay_refused", "This owner already entered the no-value lifecycle")
            result["previous_last_phase"] = state.last_phase
            return result
        state.started = True
    except Exception as exc:
        _error(result, "preflight", "Owner preflight failed", exc)
        return result

    def invoke(name, *args, critical=True):
        state.last_phase = name
        result["callback_attempts"][name] += 1
        try:
            return True, getattr(api, name)(*args)
        except Exception as exc:
            _error(result, name, "Owner callback failed", exc, critical=critical)
            return False, None

    ok, baseline = invoke("baseline_read")
    if not ok:
        return result
    if baseline is None:
        _error(result, "baseline_missing", "Frozen baseline read did not return an observation")
        return result
    result["observations"]["before"] = baseline
    ok, identity = invoke("verify_identity")
    if not ok:
        return result
    if identity is not True:
        _error(result, "identity_before_load", "Identity was not explicitly verified")
        return result
    result["facts"]["identity_before_load_verified"] = True
    result["facts"]["design_load_state"] = "return_unknown"
    ok, loaded = invoke("load")
    if not ok:
        return result
    if loaded is not None:
        _error(result, "load_nonvoid", "Load did not return the expected formal void")
        return result
    result["facts"]["load_returned_void"] = True
    result["facts"]["design_load_state"] = "loaded_by_void_return"
    result["cleanup"]["known_loaded"] = True

    ok, observation = invoke("read_loaded")
    if ok and observation is not None:
        result["observations"]["loaded"] = observation
        result["facts"]["loaded_read_completed"] = True
    elif ok:
        _error(result, "loaded_read_missing", "Loaded read did not return an observation")
    # Log evidence cannot change the established Load fact or by itself prevent
    # the already authorized UnLoad. Incomplete samples remain in observations.
    ok, logs = invoke("sample_logs", critical=False)
    if ok:
        result["observations"]["logs"] = logs

    ok, identity = invoke("verify_identity")
    if not ok:
        return result
    if identity is not True:
        _error(
            result,
            "identity_before_unload",
            "Identity no longer explicitly verified; preserve owner",
        )
        return result
    result["facts"]["identity_before_unload_verified"] = True
    result["cleanup"]["unload_attempted"] = True
    ok, unloaded = invoke("unload")
    if not ok:
        return result
    if unloaded is not None:
        _error(result, "unload_nonvoid", "UnLoad did not return the expected formal void")
        return result
    result["cleanup"]["unload_returned_void"] = True
    result["facts"]["design_load_state"] = "unloaded_by_void_return"
    ok, released = invoke("release_constraints")
    if not ok:
        return result
    if released is not True:
        _error(result, "release_incomplete", "Local reference release was not explicitly completed")
        return result
    result["cleanup"]["local_references_released"] = True
    result["cleanup"]["complete"] = True
    ok, after = invoke("after_read")
    if not ok:
        return result
    if after is None:
        _error(result, "after_read_missing", "Independent readback did not return an observation")
        return result
    result["observations"]["after"] = after
    result["facts"]["after_read_completed"] = True
    ok, equal = invoke("compare_readback", baseline, after)
    if not ok:
        return result
    if equal is not True:
        _error(result, "readback_not_equal", "Frozen readback comparison did not explicitly pass")
        return result
    result["facts"]["readback_equal"] = True
    result["control_complete"] = result["primary_error"] is None
    state.last_phase = (
        "complete" if result["control_complete"] else "released_with_observation_error"
    )
    return result
