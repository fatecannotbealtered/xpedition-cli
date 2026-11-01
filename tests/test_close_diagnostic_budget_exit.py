"""The stage-exit checkpoint must not turn two captured frames into success."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xpedition_cli import close_diagnostic as diagnostic
from xpedition_cli.driver import DriverError
from xpedition_cli.operation_budget import current_budget, operation_request


def test_context_exit_timeout_keeps_two_frames_ack_and_failure(monkeypatch):
    now = [0.0]
    board = {"sha256": "synthetic"}
    target = {"extensions": {"close_diagnostic_binding": {"board": "synthetic"}}}
    plan = {
        "target": target,
        "base_snapshot": {"extensions": {"facts": {"files": {"files": {"board": board}}}}},
    }
    lifecycle = {"save_result": True, "close_result": True, "save_acknowledged": True}
    result = {
        "contract": diagnostic.CONTRACT,
        "binding_sha256": diagnostic.digest(target["extensions"]["close_diagnostic_binding"]),
        "board_lifecycle": lifecycle,
    }
    frames = [{"files": {"board": board}}, {"files": {"board": board}}]

    def returned_complete(*args):
        active = current_budget().active_stage
        assert active["stage"] == "observe_after"
        now[0] = active["_deadline"]
        return {"status": "complete", "frames": frames, "identity_attempts": [1, 2]}

    monkeypatch.setattr(diagnostic, "terminal_observed", lambda unused: True)
    monkeypatch.setattr(diagnostic, "observe", returned_complete)
    call = SimpleNamespace(command=diagnostic.COMMAND, effects=("session", "persist", "artifact"))
    with operation_request(
        diagnostic.COMMAND, "confirm", quiet=True, clock=lambda: now[0]
    ) as budget:
        with pytest.raises(DriverError) as stopped:
            diagnostic.finish(object(), call, plan, result, None, False)
        assert budget.report()["failed_stage"] == "observe_after"
    error = stopped.value
    receipt = error.receipt
    observation = receipt["extensions"]["independent_readback"]
    assert error.details["diagnostic_complete"] is False
    assert observation["status"] == "incomplete" and observation["frames"] == frames
    assert observation["error"]["code"] == "E_TIMEOUT"
    assert receipt["extensions"]["save_acknowledged"] is True
    assert receipt["extensions"]["result"]["board_lifecycle"] == lifecycle
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["extensions"]["independent_readback_completed"] is False
    assert receipt["outcome"]["persistence"] == "unknown"


def test_unknown_terminal_preserves_ack_and_never_observes(monkeypatch):
    from xpedition_cli.errors import CLIError

    monkeypatch.setattr(
        diagnostic, "observe", lambda *args: pytest.fail("unknown native terminal was observed")
    )
    lifecycle = {"save_acknowledged": True, "save_result": True, "close_returned": False}
    result = {"contract": diagnostic.CONTRACT, "board_lifecycle": lifecycle}
    cause = CLIError("E_NATIVE_FAILURE", "synthetic original failure", {"original": True})
    call = SimpleNamespace(command=diagnostic.COMMAND, effects=("session", "persist", "artifact"))
    plan = {"target": {"extensions": {"close_diagnostic_binding": {}}}}
    with pytest.raises(DriverError) as stopped:
        diagnostic.finish(object(), call, plan, result, cause, True)
    receipt = stopped.value.receipt
    assert receipt["extensions"]["save_acknowledged"] is True
    assert receipt["extensions"]["handler_error"]["code"] == "E_NATIVE_FAILURE"
    assert receipt["extensions"]["independent_readback"]["status"] == "unobserved"
    assert receipt["extensions"]["independent_readback"]["native_request_dispatched"] is False
    assert receipt["outcome"]["execution"] == receipt["outcome"]["persistence"] == "unknown"


def test_original_error_and_observer_timeout_both_survive(monkeypatch):
    from xpedition_cli.errors import CLIError

    target = {"extensions": {"close_diagnostic_binding": {"board": "synthetic"}}}
    plan = {"target": target}
    lifecycle = {"save_result": True, "close_result": True, "save_acknowledged": True}
    result = {
        "contract": diagnostic.CONTRACT,
        "binding_sha256": diagnostic.digest(target["extensions"]["close_diagnostic_binding"]),
        "board_lifecycle": lifecycle,
    }
    cause = CLIError("E_NATIVE_FAILURE", "synthetic original failure", {"original": True})
    monkeypatch.setattr(diagnostic, "terminal_observed", lambda unused: True)

    def timeout(*args):
        raise CLIError("E_TIMEOUT", "synthetic observer timeout", {"observer": True})

    monkeypatch.setattr(diagnostic, "observe", timeout)
    call = SimpleNamespace(command=diagnostic.COMMAND, effects=("session", "persist", "artifact"))
    with pytest.raises(DriverError) as stopped:
        diagnostic.finish(object(), call, plan, result, cause, False)
    receipt = stopped.value.receipt
    assert receipt["extensions"]["handler_error"] == {
        "code": "E_NATIVE_FAILURE",
        "details": {"original": True},
    }
    observation = receipt["extensions"]["independent_readback"]
    assert observation["error"]["code"] == "E_TIMEOUT"
    assert observation["error"]["details"] == {"observer": True}
    assert receipt["extensions"]["save_acknowledged"] is True
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["outcome"]["persistence"] == "unknown"
