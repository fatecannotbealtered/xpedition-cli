"""Fresh temporary SDK/MAC fixtures only; no real CAD, COM or process API."""

from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from test_designer_owner_recovery import no_native, scene  # noqa: F401 - isolated fixtures

from xpedition_cli import designer_native_owner as owner
from xpedition_cli import designer_owner_client as C
from xpedition_cli import resources, session
from xpedition_cli import session_ownership as O
from xpedition_cli.errors import CLIError


@pytest.fixture
def prepared(scene, monkeypatch):  # noqa: F811 - imported fresh fixture
    # The real snapshot reader has a Win32 share-mode contract. This suite only
    # reads its own ordinary temporary MAC file and must never open a Win32 DLL.
    monkeypatch.setattr(owner.L, "_snapshot_reader", lambda path: path.open("rb"))
    start_id, close_id = scene.operation, "op-original-close-preparation"
    source = owner._source_binding()
    scene.intent["source_sha256"] = source
    scene.reference["source_bundle_sha256"] = C._digest(source)
    (scene.root / "intent.json").write_bytes(owner.L._pack(scene.intent, scene.key))
    locator = C._locator_path(scene.reference, scene.config)
    private = json.loads(locator.read_text())
    private["source_binding"] = source
    locator.write_text(json.dumps(private))
    actual = {
        "pid": 103,
        "executable": scene.launch["expected_native_generation"]["executable"],
        "creation_time_100ns": "30",
    }
    state = {
        "state": "closing",
        "domain": "schematic",
        "pid": 103,
        "process_identity": actual,
        "owner_ref": copy.deepcopy(scene.reference),
        "startup_operation_id": start_id,
        "closing_operation_id": close_id,
        "close_outcome": None,
        "closing_started_at": "2026-10-27T00:00:00Z",
    }
    pointer = {
        "domain": "schematic",
        "process_identity": actual,
        "startup_operation_id": start_id,
        "source_sha256": "a" * 64,
    }
    (scene.config / "native-ownership.json").write_text(
        json.dumps({"schema_version": 1, "entries": [pointer]})
    )
    commands = json.loads(resources.locate("contract/driver-v2/product_contract.json").read_text())[
        "reference"
    ]["commands"]
    capability = next(
        row["driver"]["capability_id"] for row in commands if row["path"] == "session close"
    )
    target = {
        "mode": "session",
        "engine_id": "xpedition-schematic",
        "instance_id": "103",
        "object_id": "schematic",
        "locator": "fixture:owned",
        "extensions": {
            "command": "session close",
            "domain": "schematic",
            "process_identity": actual,
            "application": {
                "pid": 103,
                "document": {"open": False, "path": None, "status": "observed"},
            },
        },
    }
    record = {
        "operation_id": close_id,
        "kind": "single",
        "command": "session close",
        "status": "planned",
        "receipt": None,
        "resolution": None,
        "started_at": None,
        "scope_id": scene.scope.scope_id,
        "plan_id": "plan-close-original",
        "capability_id": capability,
        "effects": ["session"],
        "extra": {"last_refusal": {"code": "E_CONFLICT", "at": "2026-10-27T00:00:00Z"}},
        "plan": {
            "operation_id": close_id,
            "plan_id": "plan-close-original",
            "capability_id": capability,
            "authorization_scope_id": scene.scope.scope_id,
            "effects": ["session"],
            "target": target,
            "can_apply": True,
        },
    }
    start_record = {
        "receipt": {"extensions": {"result": {"owner_ref": copy.deepcopy(scene.reference)}}}
    }
    calls = []

    def original(domain, process, operation_id, get_record, validator, scope_id):
        calls.append(operation_id)
        assert domain == "schematic" and operation_id == start_id
        assert O.same_identity(process, actual) and scope_id == scene.scope.scope_id
        assert get_record(start_id) == start_record
        return {"startup_operation_id": start_id}, copy.deepcopy(pointer)

    monkeypatch.setattr(O, "original_start", original)
    scene.record["receipt"]["extensions"]["result"]["owner_ref"] = copy.deepcopy(scene.reference)
    (scene.root / "commands").mkdir()
    (scene.root / "events").mkdir()
    event = {
        "execution_id": scene.intent["execution_id"],
        "execution_epoch": scene.intent["execution_epoch"],
        "sequence": 1,
        "previous_sha256": None,
        "phase": "public_dispatch_ready",
        "details": {},
    }
    raw = owner.L._pack(event, scene.key)
    (scene.root / "events/event-0001.json").write_bytes(raw)
    (scene.root / "snapshot.json").write_bytes(
        owner.L._pack(
            {
                "execution_id": event["execution_id"],
                "execution_epoch": event["execution_epoch"],
                "sequence": 1,
                "event_sha256": hashlib.sha256(raw).hexdigest(),
            },
            scene.key,
        )
    )
    ticket = owner.OwnerTicket(scene.root, scene.intent["execution_epoch"])
    case = NS(
        scene=scene,
        state=state,
        record=record,
        start_record=start_record,
        start_id=start_id,
        close_id=close_id,
        calls=calls,
        ticket=ticket,
        count=0,
    )

    def publish(method="identity", *, sdk_id=None, complete=True):
        case.count += 1
        command_id = "owner-command-" + f"{case.count:032x}"
        directory = scene.root / "commands" / command_id
        directory.mkdir()
        operation = sdk_id or close_id
        binding = {
            "command": "session close",
            "scope_id": scene.scope.scope_id,
            "scope_sha256": scene.intent["editing_scope"]["scope_sha256"],
            "source_operation_id": operation,
        }
        request = {
            "command_id": command_id,
            "epoch": ticket.epoch,
            "command": "adapter",
            "method": method,
            "params": {"domain": "schematic"},
            "sdk_operation_id": operation,
            "authorization_binding": binding,
        }
        owner._command_write(directory / "request.json", request, scene.key)
        owner._command_write(
            directory / "initial.json",
            {
                "command_id": command_id,
                "epoch": ticket.epoch,
                "original_outcome": "unknown",
                "immutable": True,
            },
            scene.key,
        )
        journal = owner._CommandJournal(directory, scene.key, request)
        journal.append("command_received")
        journal.append("command_started")
        if complete:
            journal.append("command_returned", result={"fixture": True})
        return directory

    case.publish = publish
    publish()
    case.observe = lambda: O.observe_prepared_close(
        case.state,
        scene.scope,
        lambda op: copy.deepcopy(
            record if op == close_id else start_record if op == start_id else None
        ),
        scene.validator,
        scene.config,
    )
    return case


def test_readonly_preparation_proof_preserves_original_sdk_and_mac(prepared):
    case = prepared
    before = {
        path.relative_to(case.scene.root): path.read_bytes()
        for path in case.scene.root.rglob("*")
        if path.is_file()
    }
    original = copy.deepcopy(case.record)
    proof = case.observe()
    assert proof.original_operation_id == case.close_id and proof.matches(case.state)
    assert proof.process_identity == case.state["process_identity"]
    assert proof.safe_summary["close_request_count"] == 0
    assert proof.safe_summary["identity_request_count"] == 1
    assert len(case.calls) >= 3
    assert case.record == original
    assert before == {
        path.relative_to(case.scene.root): path.read_bytes()
        for path in case.scene.root.rglob("*")
        if path.is_file()
    }
    assert str(case.scene.root) not in json.dumps(proof.safe_summary)


@pytest.mark.parametrize(
    "bad", ["close", "other_close", "write", "incomplete", "missing", "mac", "unknown_sdk"]
)
def test_queued_write_unknown_or_incomplete_census_never_becomes_prepared(prepared, bad):
    case = prepared
    if bad == "close":
        case.publish("close")
    elif bad == "other_close":
        case.publish("close", sdk_id="op-other-close")
    elif bad == "write":
        case.publish("save")
    elif bad == "incomplete":
        case.publish(complete=False)
    elif bad == "missing":
        directory = case.publish()
        (directory / "request.json").unlink()
    elif bad == "mac":
        directory = case.publish()
        (directory / "request.json").write_bytes(b"invalid mac")
    else:
        case.record["status"] = "unknown"
    with pytest.raises(CLIError):
        case.observe()
    assert case.state["state"] == "closing"


def test_only_matching_typed_proof_can_transition_session_metadata(prepared):
    case = prepared
    proof = case.observe()
    session._write_state(case.state)
    before_record = copy.deepcopy(case.record)
    with pytest.raises(CLIError):
        session.record_close_prepared_not_dispatched(proof.safe_summary)
    before = session.read_state()
    session.record_close_prepared_not_dispatched(proof)
    current = session.read_state()
    assert current["state"] == "close_prepared_not_dispatched"
    assert current["owner_ref"] == before["owner_ref"]
    assert current["process_identity"] == before["process_identity"]
    assert current["startup_operation_id"] == before["startup_operation_id"]
    assert current["previous_close_preparation_context"] == before
    assert current["previous_close_preparation"] == proof.safe_summary
    assert current["closing_operation_id"] == case.close_id
    assert case.record == before_record
    with pytest.raises(CLIError):
        session.record_close_prepared_not_dispatched(proof)

    context = {
        "domain": "schematic",
        "pid": current["pid"],
        "process_identity": current["process_identity"],
        "startup_operation_id": current["startup_operation_id"],
    }
    session.record_native_closing(
        "schematic",
        current["process_identity"],
        operation_id="op-new-normal-close",
        ownership_record=context,
    )
    closing = session.read_state()
    assert closing["closing_operation_id"] == "op-new-normal-close"
    assert closing["previous_close_preparation"] == proof.safe_summary
    assert closing["previous_close_preparation_context"] == before
    assert case.record == before_record


def test_state_drift_or_persistence_failure_does_not_silently_transition(prepared, monkeypatch):
    proof = prepared.observe()
    session._write_state({**prepared.state, "changed": True})
    with pytest.raises(CLIError):
        session.record_close_prepared_not_dispatched(proof)
    session._write_state(prepared.state)
    monkeypatch.setattr(session, "_write_state", lambda _value: None)
    with pytest.raises(CLIError) as caught:
        session.record_close_prepared_not_dispatched(proof)
    assert caught.value.code == "E_IO"
    assert caught.value.details["native_request_dispatched"] is False
    assert caught.value.details["state_write_attempted"] is True
    assert caught.value.details["write_attempted"] is False
    assert session.read_state() == prepared.state
