"""One confirmed start preserves a proved original generation, never a current PID."""

# Imported pytest fixtures are injected again through their function parameters.
# ruff: noqa: F811

import copy
import inspect
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_designer_owner_recovery import closed_scene, no_native, scene  # noqa: F401

from xpedition_cli import designer_owner_recovery as R
from xpedition_cli import driver as D
from xpedition_cli import session
from xpedition_cli.errors import CLIError
from xpedition_cli.operation_budget import operation_request


def begin_request(scene, monkeypatch):
    old_get = getattr(
        scene,
        "get_record",
        lambda op: copy.deepcopy(scene.record) if op == scene.operation else None,
    )
    preview = scene.observe()
    operation = "op-" + "9" * 24
    target = {
        "mode": "file",
        "extensions": {
            "command": "session start",
            "domain": "schematic",
            "source_operation_id": operation,
            "previous_start_observation": preview.safe_summary,
            "designer_startup_route": D.DESIGNER_START_ROUTE,
            "designer_startup_generation": operation,
        },
    }
    plan = {
        "operation_id": operation,
        "plan_id": "plan-current",
        "target": target,
        "base_snapshot": {"id": "before", "extensions": {"facts": {}}},
    }
    current_record = {
        "operation_id": operation,
        "status": "dispatched",
        "scope_id": scene.scope.scope_id,
        "plan": copy.deepcopy(plan),
    }

    def get_record(op):
        return copy.deepcopy(current_record) if op == operation else old_get(op)

    adapter = NS(_revision_request_token=object())
    monkeypatch.setattr(session, "_state_path", lambda: scene.config / "session.json")
    session._write_state(scene.state)
    observation, witness = R.observe_confirmed_start(
        adapter, operation, plan, scene.scope, get_record, scene.validator, scene.config
    )
    original = copy.deepcopy(scene.record)
    previous = session.read_state()
    new_reference = {**scene.reference, "owner_id": "9" * 32}
    association = (
        "previous_failed_start"
        if preview.safe_summary["protocol"] == R.PROTOCOL
        else "previous_session_generation"
    )
    session.record_native_start(
        {
            "domain": "schematic",
            "pid": 900,
            "owner_ref": new_reference,
            "startup_operation_id": operation,
            association: observation.safe_summary,
        }
    )
    return NS(
        scene=scene,
        adapter=adapter,
        operation=operation,
        plan=plan,
        target=target,
        record=current_record,
        get_record=get_record,
        observation=observation,
        witness=witness,
        original=original,
        previous=previous,
        association=association,
        observe_after=lambda: witness.observe_after(
            adapter, target, scene.scope, get_record, scene.validator, scene.config
        ),
    )


@pytest.mark.parametrize("later", ["access_denied", "pid_reused"])
def test_same_request_terminal_original_generation_does_not_reopen_current_pid(
    scene, monkeypatch, later
):
    request = begin_request(scene, monkeypatch)
    opened = list(scene.opened)
    if later == "pid_reused":
        scene.current[103].update(birth_filetime_100ns="999", alive=True)
    scene.kernel.OpenProcess = lambda *_args: pytest.fail("A proved ended generation cannot revive")
    result = request.observe_after()
    assert result.safe_summary == request.observation.safe_summary
    assert scene.opened == opened and scene.record == request.original
    assert all(row["pid_reused"] is None for row in result.diagnostics)
    assert all(
        row["source"] == "same_request_original_generation_terminal_witness"
        for row in result.diagnostics
    )
    assert not hasattr(scene.kernel, "TerminateProcess")


@pytest.mark.parametrize(
    "change",
    [
        "request",
        "adapter",
        "operation",
        "target",
        "context",
        "summary",
        "plan",
        "status",
        "scope",
        "mac",
        "source",
        "epoch",
    ],
)
def test_same_request_witness_rechecks_authority_and_never_crosses_request(
    scene, monkeypatch, change
):
    request = begin_request(scene, monkeypatch)
    state = session.read_state()
    if change == "request":
        request.adapter._revision_request_token = object()
    elif change == "adapter":
        request.adapter = NS(_revision_request_token=request.adapter._revision_request_token)
    elif change == "operation":
        state["startup_operation_id"] = "op-other"
    elif change == "target":
        request.target["extensions"]["source_operation_id"] = "op-other"
    elif change == "context":
        state[request.association + "_context"]["changed"] = True
    elif change == "summary":
        state[request.association]["original_status"] = "succeeded"
    elif change == "plan":
        request.record["plan"]["plan_id"] = "plan-other"
    elif change == "status":
        request.record["status"] = "unknown"
    elif change == "scope":
        Path(scene.scope.grant_file).write_bytes(b"{invalid")
    elif change == "mac":
        (scene.root / "owner.json").write_bytes(b"invalid mac")
    else:
        changed = copy.deepcopy(scene.intent)
        if change == "source":
            changed["source_sha256"] = {"foreign.py": "f" * 64}
        else:
            changed["execution_epoch"] = "f" * 32
        (scene.root / "intent.json").write_bytes(R.L._pack(changed, scene.key))
    session._write_state(state)
    opened = list(scene.opened)
    with pytest.raises(CLIError):
        request.witness.observe_after(
            request.adapter,
            request.target,
            scene.scope,
            request.get_record,
            scene.validator,
            scene.config,
        )
    assert scene.opened == opened and scene.record == request.original


def test_summary_or_fake_witness_cannot_replace_typed_evidence(scene, monkeypatch):
    request = begin_request(scene, monkeypatch)
    for fake in (
        request.observation.safe_summary,
        NS(observe_after=lambda *args: request.observation),
    ):
        with pytest.raises(CLIError):
            R.observe_exited_failed_start(
                session.read_state(),
                scene.scope,
                request.get_record,
                scene.validator,
                scene.config,
                expected_summary=request.observation.safe_summary,
                _same_request=fake,
            )
        with pytest.raises(CLIError):
            D.Adapter(backend=NS())._observe(
                request.target, after_dispatch=True, start_generation_witness=fake
            )


def test_first_start_without_an_original_generation_keeps_no_witness(scene, monkeypatch, tmp_path):
    fresh = tmp_path / "genuinely-new-config"
    fresh.mkdir()
    monkeypatch.setattr(session, "_state_path", lambda: fresh / "session.json")
    assert R.observe_confirmed_start(
        NS(_revision_request_token=object()),
        "op-first",
        {},
        scene.scope,
        lambda _op: pytest.fail("First start has no original SDK record"),
        scene.validator,
        fresh,
    ) == (None, None)


def test_acquisition_does_not_accept_a_cached_or_json_observation(scene, monkeypatch):
    request = begin_request(scene, monkeypatch)
    session._write_state(request.previous)
    scene.kernel.OpenProcess = lambda *_args: None
    monkeypatch.setattr(R.ctypes, "get_last_error", lambda: 5, raising=False)
    with pytest.raises(CLIError) as caught:
        R.observe_confirmed_start(
            request.adapter,
            request.operation,
            request.plan,
            scene.scope,
            request.get_record,
            scene.validator,
            scene.config,
        )
    assert caught.value.details["process_observation"]["win32_error"] == 5
    assert scene.record == request.original


def test_closing_witness_still_rechecks_the_entire_same_id_close_mac_chain(
    closed_scene, monkeypatch
):
    scene = closed_scene
    request = begin_request(scene, monkeypatch)
    scene.kernel.OpenProcess = lambda *_args: pytest.fail("No current PID reopening")
    assert request.observe_after().safe_summary == request.observation.safe_summary
    scene.close_result["lifecycle"]["quit_reply_received"] = False
    scene.publish()
    with pytest.raises(CLIError) as caught:
        request.observe_after()
    assert caught.value.details["reason"] == "original_close_empty_quit_terminal_incomplete"
    assert scene.close_record["status"] == "unknown" and scene.close_record["resolution"] is None
    assert "scope_file" not in json.dumps(caught.value.details)


def test_ended_witness_does_not_upgrade_normal_close_qualification(closed_scene, monkeypatch):
    closed_scene.state["state"] = "closed"
    request = begin_request(closed_scene, monkeypatch)
    closed_scene.kernel.OpenProcess = lambda *_args: pytest.fail("No current PID reopening")
    summary = request.observe_after().safe_summary
    assert summary["protocol"] == R.ENDED_PROTOCOL
    assert summary["normal_close_qualification"] == "unobserved"


def test_real_apply_keeps_its_fresh_proof_local_through_dispatch_and_readback(scene, monkeypatch):
    request = begin_request(scene, monkeypatch)
    session._write_state(request.previous)
    adapter = D.Adapter(backend=NS())
    adapter.ownership_record_get = request.get_record
    adapter.ownership_validator = scene.validator
    monkeypatch.setattr(D, "config_dir", lambda: scene.config)
    token_folder = scene.config / "handler-plans"
    token_folder.mkdir()
    (token_folder / (request.operation + ".json")).write_text('{"token":"offline-fixture"}')
    sent, observed = [], []

    def handler(options):
        proof = options["_previous_start_observation"]
        assert type(proof) is R.RecoveryObservation and proof.matches(session.read_state())
        sent.append(options["_operation_id"])
        session.record_native_start(
            {
                "domain": "schematic",
                "pid": 900,
                "owner_ref": {**scene.reference, "owner_id": "9" * 32},
                "startup_operation_id": request.operation,
                request.association: proof.safe_summary,
            }
        )
        scene.kernel.OpenProcess = lambda *_args: pytest.fail(
            "No current PID probing after dispatch"
        )
        return {"started": True}

    class ReadbackReached(BaseException):
        pass

    def observe(target, *, after_dispatch, start_generation_witness):
        assert after_dispatch and type(start_generation_witness) is R.SameRequestStartWitness
        result = start_generation_witness.observe_after(
            adapter, target, scene.scope, request.get_record, scene.validator, scene.config
        )
        observed.append(result.safe_summary)
        raise ReadbackReached  # Stop before unrelated native startup-verification assertions.

    monkeypatch.setattr(D, "_handler", lambda _command: handler)
    monkeypatch.setattr(adapter, "_observe", observe)
    call = NS(
        command="session start",
        params={"kind": "schematic"},
        operation_id=request.operation,
        plan_id=request.plan["plan_id"],
        extras={"scope": scene.scope},
        dangerous=False,
    )
    with operation_request("session start", "confirm", quiet=True):
        with pytest.raises(ReadbackReached):
            inspect.unwrap(D.Adapter.apply)(adapter, call, request.plan)
    assert sent == [request.operation] and observed == [request.observation.safe_summary]
    assert scene.record == request.original
    assert not hasattr(adapter, "start_generation_witness")
