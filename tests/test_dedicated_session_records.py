"""Pure same-owner handler/state regressions; every real native boundary is forbidden."""

from __future__ import annotations

import copy
import ctypes
import json
from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as N
from xpedition_cli import native_process as P
from xpedition_cli import session
from xpedition_cli.backends import NativeBackend
from xpedition_cli.cli import sessions as S
from xpedition_cli.designer_owner_client import validate_owner_ref
from xpedition_cli.errors import CLIError


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append("forbidden native boundary")
        raise AssertionError("This suite must not access CAD, COM, OS process or window APIs")

    for target, names in (
        (NativeBackend, ("invoke", "status", "require_implemented")),
        (P, ("process_identity", "processes", "ProcessHandle")),
        (S, ("process_identity", "processes", "ProcessHandle", "running_applications")),
        (N, ("_import_com", "_viewdraw_application", "_viewdraw_active")),
        (ctypes, ("WinDLL", "OleDLL")),
    ):
        for name in names:
            monkeypatch.setattr(target, name, forbidden, raising=False)
    yield hits
    assert hits == []


@pytest.fixture
def owner(monkeypatch):
    actual = {
        "pid": 4242,
        "executable": "C:/fixture/SDD_HOME/wv/win64/bin/viewdraw.exe",
        "creation_time_100ns": "134000000000000001",
    }
    chain = {"process_chain": [actual], "parent_edges": []}
    provenance = {
        "version": P.STARTUP_LINEAGE_VERSION,
        "domain": "schematic",
        "launcher_identity": actual,
        "ownership": "proven_this_start",
        **chain,
    }
    reference = {
        "protocol": "designer-owned-session-v1",
        "owner_id": "a" * 32,
        "owner_epoch_sha256": "b" * 64,
        "source_bundle_sha256": "c" * 64,
    }
    application = {
        "pid": 4242,
        "public_fields": {"FullName": actual["executable"]},
        "document": {"status": "observed", "open": False, "path": None},
    }
    reply = {
        "domain": "schematic",
        "started": True,
        "attached": True,
        "automation_ready": True,
        "pid": 4242,
        "executable": actual["executable"],
        "launcher_pid": 4242,
        "process_identity": actual,
        "startup_provenance": provenance,
        "startup_seed": {
            "version": P.STARTUP_LINEAGE_VERSION,
            "domain": "schematic",
            "launcher_identity": actual,
            "candidates": [chain],
        },
        "owner_ref": reference,
        "application_identity": application,
        "adapter_identity": {"source": "pure same-owner fixture"},
    }
    case = SimpleNamespace(reply=reply, calls=[], identities=[], error=None)

    class Backend:
        def invoke(self, method, params, **kwargs):
            case.calls.append((method, copy.deepcopy(params)))
            if method == "start":
                if case.error:
                    raise case.error
                return copy.deepcopy(reply)
            if method == "read_project":
                return {"observation": "fixture_project_query", "is_project_open": False}
            assert method == "identity"
            if case.identities:
                value = case.identities.pop(0)
                if isinstance(value, Exception):
                    raise value
                return copy.deepcopy(value)
            return {"application": copy.deepcopy(application), "adapter": {"source": "pure"}}

    case.backend = Backend()
    monkeypatch.setattr(S, "native", lambda: case.backend)
    return case


def _start():
    return S.start({"kind": "schematic", "_operation_id": "op-owned-start"})


def test_start_records_one_owner_without_health_rot_or_os_rediscovery(owner):
    result = _start()
    assert [name for name, _params in owner.calls] == ["start", "identity", "identity"]
    assert owner.calls[0][1] == {"domain": "schematic", "visible": True}
    assert result["started"] is result["attached"] is True
    recorded = session.read_state()
    assert recorded["owner_ref"] == result["owner_ref"]
    assert recorded["startup_operation_id"] == "op-owned-start"
    assert recorded["startup_provenance"] == result["startup_provenance"]
    assert set(recorded["owner_ref"]) == {
        "protocol",
        "owner_id",
        "owner_epoch_sha256",
        "source_bundle_sha256",
    }


@pytest.mark.parametrize("project", ["arbitrary.prj", "missing/path.prj"])
def test_start_rejects_project_before_backend_or_state_changes(owner, project):
    session._write_state({"state": "peer", "domain": "pcb", "original": True})
    before = session._state_path().read_bytes()
    with pytest.raises(CLIError) as caught:
        S.start({"kind": "schematic", "project": project, "_operation_id": "op-owned-start"})
    assert caught.value.details["write_attempted"] is False
    assert session._state_path().read_bytes() == before
    assert owner.calls == []


def test_start_without_sdk_operation_never_dispatches(owner):
    with pytest.raises(CLIError) as caught:
        S.start({"kind": "schematic"})
    assert caught.value.code == "E_CONFIG"
    assert owner.calls == []
    assert session.read_state() == {}


def test_predispatch_refusal_preserves_previous_state(owner):
    session._write_state({"domain": "pcb", "state": "started", "sentinel": "peer"})
    before = session._state_path().read_bytes()
    owner.error = CLIError("E_CONFLICT", "pure refusal", {"write_attempted": False})
    with pytest.raises(CLIError) as caught:
        _start()
    assert caught.value.details["write_attempted"] is False
    assert session._state_path().read_bytes() == before
    assert [name for name, _ in owner.calls] == ["start"]


def test_unknown_retains_original_id_and_ref_without_relaunch(owner):
    peer = {"domain": "pcb", "state": "attached", "sentinel": "original peer"}
    session._write_state(peer)
    owner.error = CLIError(
        "E_OUTCOME_UNKNOWN",
        "fixture pending",
        {"write_attempted": True, "native_result": {"owner_ref": owner.reply["owner_ref"]}},
    )
    with pytest.raises(CLIError):
        _start()
    state = session.read_state()
    assert state["state"] == "startup_incomplete"
    assert state["startup_operation_id"] == "op-owned-start"
    assert state["owner_ref"] == owner.reply["owner_ref"]
    assert state["retained_peer_context"] == peer
    with pytest.raises(CLIError) as caught:
        _start()
    assert caught.value.details["write_attempted"] is False
    assert [name for name, _ in owner.calls] == ["start"]


def test_unknown_client_top_level_reference_is_retained(owner):
    owner.error = CLIError(
        "E_OUTCOME_UNKNOWN",
        "fixture client pending",
        {
            "write_attempted": True,
            "owner_ref": owner.reply["owner_ref"],
            "sdk_operation_id": "op-owned-start",
            "owner_command_id": None,
        },
    )
    with pytest.raises(CLIError):
        _start()
    assert session.read_state()["owner_ref"] == owner.reply["owner_ref"]
    assert session.read_state()["startup_operation_id"] == "op-owned-start"


@pytest.mark.parametrize("proof_kind", ["typed", "json", "changed_state"])
@pytest.mark.parametrize("old_state", ["startup_incomplete", "started"])
def test_only_same_state_typed_closed_generation_proof_allows_new_start(
    owner, proof_kind, old_state
):
    from xpedition_cli import designer_owner_recovery as R

    old_ref = {**owner.reply["owner_ref"], "owner_id": "f" * 32}
    peer = {"domain": "pcb", "state": "attached", "original": "PCB peer"}
    previous = {
        "domain": "schematic",
        "state": old_state,
        "pid": None,
        "owner_ref": old_ref,
        "startup_operation_id": "op-original-unknown",
        "retained_peer_context": peer,
    }
    session._write_state(previous)
    summary = {
        "protocol": R.PROTOCOL if old_state == "startup_incomplete" else R.ENDED_PROTOCOL,
        "original_startup_operation_id": "op-original-unknown",
        "original_owner_ref": old_ref,
        "original_generations_absent": True,
        "generations": [
            {"role": role, "original_generation_absent": True}
            for role in ("owner", "launcher", "worker")
        ],
    }
    # Unit-only opaque evidence; real source/kernel production is covered by the
    # separate new-MAC recovery suite, never by these handler fixture flags.
    proof = R.RecoveryObservation(R._SEAL, summary, previous, [])
    if proof_kind == "json":
        proof = summary
    elif proof_kind == "changed_state":
        session._write_state({**previous, "cause": "changed after proof"})
    before = session._state_path().read_bytes()
    options = {
        "kind": "schematic",
        "_operation_id": "op-owned-start",
        "_previous_start_observation": proof,
    }
    if proof_kind != "typed":
        with pytest.raises(CLIError) as caught:
            S.start(options)
        assert caught.value.details["write_attempted"] is False
        assert session._state_path().read_bytes() == before
        assert owner.calls == []
        return
    result = S.start(options)
    recorded = session.read_state()
    field = (
        "previous_failed_start"
        if old_state == "startup_incomplete"
        else "previous_session_generation"
    )
    assert result[field] == summary
    assert field + "_context" not in result
    assert recorded[field + "_context"] == previous
    assert recorded[field] == summary
    assert recorded["retained_peer_context"] == peer
    assert recorded["owner_ref"] != old_ref
    assert recorded["startup_operation_id"] == "op-owned-start"
    assert [name for name, _ in owner.calls] == ["start", "identity", "identity"]


@pytest.mark.parametrize("change", ["pid", "image", "open", "unobserved", "null_path"])
def test_real_empty_identity_drift_stays_incomplete(owner, change):
    value = {"application": copy.deepcopy(owner.reply["application_identity"])}
    app = value["application"]
    if change == "pid":
        app["pid"] += 1
    elif change == "image":
        app["public_fields"]["FullName"] = "C:/different/viewdraw.exe"
    elif change == "open":
        app["document"].update(open=True, path="C:/other/project.prj")
    elif change == "unobserved":
        app["document"]["status"] = "unobserved"
    else:
        app["document"]["path"] = ""
    owner.identities = [value]
    with pytest.raises(CLIError) as caught:
        _start()
    assert caught.value.details["write_attempted"] is True
    assert session.read_state()["state"] == "startup_incomplete"
    assert session.read_state()["owner_ref"] == owner.reply["owner_ref"]
    assert [name for name, _ in owner.calls] == ["start", "identity"]


def test_record_failure_does_not_claim_complete_start(owner, monkeypatch):
    monkeypatch.setattr(session, "_write_state", lambda _state: None)
    with pytest.raises(CLIError) as caught:
        _start()
    assert caught.value.code == "E_IO"
    assert caught.value.details["native_result"]["attached"] is False
    assert session.read_state() == {}
    assert [name for name, _ in owner.calls] == ["start", "identity"]


@pytest.mark.parametrize(
    "field", ["process_identity", "startup_seed", "startup_provenance", "application_identity"]
)
def test_malformed_start_readback_remains_unknown(owner, field):
    owner.reply[field] = ["not a native proof mapping"]
    with pytest.raises(CLIError) as caught:
        _start()
    assert caught.value.details["write_attempted"] is True
    assert session.read_state()["state"] == "startup_incomplete"
    assert [name for name, _ in owner.calls] == ["start"]


@pytest.mark.parametrize("pending", [False, True])
def test_malformed_reference_never_leaks_private_fields(owner, pending):
    secret = "fixture-private-value-never-public"
    owner.reply["owner_ref"]["epoch"] = secret
    if pending:
        owner.error = CLIError(
            "E_OUTCOME_UNKNOWN",
            "fixture pending",
            {"write_attempted": True, "native_result": {"owner_ref": owner.reply["owner_ref"]}},
        )
    with pytest.raises(CLIError) as caught:
        _start()
    assert secret not in json.dumps(caught.value.details)
    assert secret not in session._state_path().read_text()
    assert "owner_ref" not in session.read_state()
    assert [name for name, _ in owner.calls] == ["start"]


def test_completed_start_preserves_peer_and_attach_cannot_replace_owner(owner):
    peer = {"domain": "pcb", "state": "attached", "sentinel": "peer"}
    session._write_state(peer)
    result = _start()
    assert session.read_state()["retained_peer_context"] == peer
    session.record_native_attach(
        {
            "domain": "schematic",
            "pid": result["pid"],
            "process_identity": result["process_identity"],
            "owner_ref": {"forged": "ignored"},
        }
    )
    assert session.read_state()["owner_ref"] == result["owner_ref"]
    assert session.read_state()["retained_peer_context"] == peer
    assert session.read_state()["startup_operation_id"] == "op-owned-start"


def test_verified_close_retains_tombstone_peer_and_status_does_not_reconnect(owner, monkeypatch):
    from xpedition_cli import session_ownership

    peer = {"domain": "pcb", "state": "attached", "sentinel": "original PCB"}
    session._write_state(peer)
    result = _start()
    removed = []
    monkeypatch.setattr(
        session_ownership, "remove", lambda domain, actual: removed.append((domain, actual))
    )
    before = session._state_path().read_bytes()
    with pytest.raises(CLIError):
        session.clear_native()
    assert session._state_path().read_bytes() == before
    session.record_native_closing(
        "schematic", result["process_identity"], operation_id="op-terminal-close"
    )
    terminal = {
        "closed": True,
        "process_absence_observed": True,
        "process_identity": result["process_identity"],
        "owned_launch_chain": {"normal_exit_complete": True},
        "lifecycle": {
            "empty_document_observed": True,
            "quit_count": 1,
            "quit_dispatched": True,
            "quit_reply_received": True,
        },
    }
    session.clear_native(closed_observation=terminal)
    ended = session.read_state()
    assert ended["state"] == "closed" and ended["xpedition_process"] is False
    assert ended["owner_ref"] == result["owner_ref"]
    assert ended["startup_operation_id"] == result["startup_operation_id"]
    assert ended["closing_operation_id"] == "op-terminal-close"
    assert ended["startup_provenance"] == result["startup_provenance"]
    assert ended["retained_peer_context"] == peer
    assert removed == []
    owner.calls.clear()
    observed = S.status({})
    assert observed["recorded"]["state"] == "closed"
    assert observed["designer"]["running"] is False and owner.calls == []
    session.clear_native()
    assert session.read_state() == ended
    session.record_native_start({"domain": "pcb", "pid": 8181})
    assert session.read_state()["retained_peer_context"] == ended


@pytest.mark.parametrize("layout_active", [False, True])
def test_closed_owner_or_retained_closed_peer_requires_proof_for_new_empty_generation(
    owner, layout_active
):
    from xpedition_cli import designer_owner_recovery as R

    old_ref = {**owner.reply["owner_ref"], "owner_id": "e" * 32}
    pcb = {"domain": "pcb", "state": "started", "pid": 7070}
    ended = {
        "domain": "schematic",
        "state": "closed",
        "startup_operation_id": "op-ended-owner",
        "owner_ref": old_ref,
        "retained_peer_context": pcb,
    }
    previous = {**pcb, "retained_peer_context": ended} if layout_active else ended
    summary = {
        "protocol": R.ENDED_PROTOCOL,
        "original_startup_operation_id": "op-ended-owner",
        "original_owner_ref": old_ref,
        "original_generations_absent": True,
    }
    session._write_state(previous)
    # An opaque unit fixture exercises handler association only. The separate
    # recovery suite checks original SDK/MAC/generation production of this type.
    proof = R.RecoveryObservation(R._SEAL, summary, previous, [])
    result = S.start(
        {"kind": "schematic", "_operation_id": "op-new-empty", "_previous_start_observation": proof}
    )
    current = session.read_state()
    assert result["previous_session_generation"] == summary
    assert current["previous_session_generation_context"] == previous
    assert current["retained_peer_context"] == (previous if layout_active else pcb)
    assert current["startup_operation_id"] == "op-new-empty"
    assert current["owner_ref"] != old_ref


def test_other_generation_attach_cannot_inherit_owner(owner):
    result = _start()
    changed = {**result["process_identity"], "creation_time_100ns": "134000000000000002"}
    session.record_native_attach({"domain": "schematic", "pid": 4242, "process_identity": changed})
    assert "owner_ref" not in session.read_state()
    assert session.read_state()["startup_provenance"] is None


@pytest.mark.parametrize(
    "kind", ["typed", "json", "wrong_protocol", "wrong_close_id", "changed_state"]
)
def test_pending_close_needs_same_state_typed_original_terminal_proof(owner, kind):
    from xpedition_cli import designer_owner_recovery as R

    previous = {
        "domain": "schematic",
        "state": "closing",
        "startup_operation_id": "op-old-start",
        "closing_operation_id": "op-old-unknown-close",
        "owner_ref": {**owner.reply["owner_ref"], "owner_id": "e" * 32},
    }
    summary = {
        "protocol": "designer-owned-close-ended-v1",
        "original_startup_operation_id": previous["startup_operation_id"],
        "original_close_operation_id": previous["closing_operation_id"],
        "original_owner_ref": previous["owner_ref"],
        "original_generations_absent": True,
    }
    if kind == "wrong_protocol":
        summary["protocol"] = R.ENDED_PROTOCOL
    elif kind == "wrong_close_id":
        summary["original_close_operation_id"] = "op-another-close"
    session._write_state(previous)
    # Handler association only. Production of this opaque type from the original
    # SDK/MAC close chain and all ended generations is tested by recovery tests.
    proof = R.RecoveryObservation(R._SEAL, summary, previous, [])
    if kind == "json":
        proof = summary
    elif kind == "changed_state":
        session._write_state({**previous, "cause": "later observation"})
    before = session._state_path().read_bytes()
    options = {
        "kind": "schematic",
        "_operation_id": "op-owned-start",
        "_previous_start_observation": proof,
    }
    if kind != "typed":
        with pytest.raises(CLIError):
            S.start(options)
        assert owner.calls == []
        assert session._state_path().read_bytes() == before
        return
    result = S.start(options)
    assert result["previous_session_generation"] == summary
    assert session.read_state()["previous_session_generation_context"] == previous
    assert [name for name, _ in owner.calls] == ["start", "identity", "identity"]


def test_close_preserves_same_original_start_reference_from_registry_context(owner):
    result = _start()
    original = session.read_state()
    context = {key: value for key, value in original.items() if key != "owner_ref"}
    session.record_native_closing(
        "schematic",
        result["process_identity"],
        operation_id="op-owned-close",
        ownership_record=context,
    )
    assert session.read_state()["owner_ref"] == result["owner_ref"]
    assert session.read_state()["closing_operation_id"] == "op-owned-close"


def test_close_does_not_inherit_reference_from_different_start(owner):
    result = _start()
    context = {key: value for key, value in session.read_state().items() if key != "owner_ref"}
    context["startup_operation_id"] = "op-different-start"
    session.record_native_closing(
        "schematic",
        result["process_identity"],
        operation_id="op-owned-close",
        ownership_record=context,
    )
    assert "owner_ref" not in session.read_state()


def test_close_refuses_original_source_reference_conflict_before_recording(owner):
    result = _start()
    before = session._state_path().read_bytes()
    context = session.read_state()
    context["owner_ref"]["owner_id"] = "e" * 32
    with pytest.raises(CLIError) as caught:
        session.record_native_closing(
            "schematic",
            result["process_identity"],
            operation_id="op-owned-close",
            ownership_record=context,
        )
    assert caught.value.code == "E_CONFLICT"
    assert session._state_path().read_bytes() == before


def test_same_original_operation_cannot_replace_locator(owner):
    result = _start()
    result["owner_ref"]["owner_id"] = "d" * 32
    with pytest.raises(CLIError) as caught:
        session.record_native_start(result)
    assert caught.value.code == "E_CONFLICT"
    assert session.read_state()["owner_ref"]["owner_id"] == "a" * 32


def test_status_uses_only_same_owner_identity(owner):
    _start()
    owner.calls.clear()
    result = S.status({})
    assert result["designer"]["running"] is True
    assert result["layout"]["running"] is None
    assert [name for name, _ in owner.calls] == ["identity", "read_project"]
    assert result["designer"]["project_state"] == {
        "observation": "fixture_project_query",
        "is_project_open": False,
    }


def test_invalid_or_unresolved_status_never_falls_back(owner):
    session._write_state({"domain": "schematic", "state": "startup_incomplete", "pid": 4242})
    result = S.status({})
    assert result["designer"]["running"] is None
    assert result["designer"]["identity_error"]["code"] == "E_VALIDATION"
    assert owner.calls == []


def test_status_owner_source_refusal_never_discovers_another_instance(owner):
    _start()
    owner.calls.clear()
    owner.identities = [CLIError("E_CONFLICT", "original source changed")]
    result = S.status({})
    assert result["designer"]["running"] is None
    assert result["designer"]["identity_error"]["code"] == "E_CONFLICT"
    assert [name for name, _ in owner.calls] == ["identity"]


def test_pending_close_status_does_not_reacquire_app_or_change_original_state(owner, monkeypatch):
    from xpedition_cli import designer_owner_client as client

    _start()
    state = session.read_state()
    state.update(state="closing", closing_operation_id="op-original-close")
    session._write_state(state)
    before = session._state_path().read_bytes()
    owner.calls.clear()
    observations = []
    phases = []

    def validate(reference):
        assert reference == state["owner_ref"]
        phases.append("original source/registry/locator validated")

    def observe(domain, pid, *, timeout, expected_identity):
        phases.append("OS-only exit observation")
        observations.append((domain, pid, timeout, expected_identity))
        return {"process_absence_observed": True, "process_identity": expected_identity}

    monkeypatch.setattr(S, "observe_exit", observe)
    monkeypatch.setattr(client, "validate_routed_owner", validate)
    result = S.status({})
    assert result["closure_observation"]["original_operation_id"] == "op-original-close"
    assert result["closure_observation"]["historical_receipt_changed"] is False
    assert result["closure_observation"]["quit_replayed"] is False
    assert session._state_path().read_bytes() == before
    assert observations == [("schematic", "4242", 0, state["process_identity"])]
    assert phases == ["original source/registry/locator validated", "OS-only exit observation"]
    assert owner.calls == []


def test_pending_close_bad_source_refuses_before_any_exit_observation(owner, monkeypatch):
    from xpedition_cli import designer_owner_client as client

    _start()
    state = session.read_state()
    state.update(state="closing", closing_operation_id="op-original-close")
    session._write_state(state)
    before = session._state_path().read_bytes()
    owner.calls.clear()

    def refuse(_reference):
        raise CLIError("E_CONFLICT", "original source changed")

    monkeypatch.setattr(client, "validate_routed_owner", refuse)
    result = S.status({})
    assert result["designer"]["running"] is None
    assert result["designer"]["identity_error"]["code"] == "E_CONFLICT"
    assert "closure_observation" not in result
    assert session._state_path().read_bytes() == before
    assert owner.calls == []


def test_stop_domain_requires_explicit_kind_and_same_owner_record(owner):
    _start()
    owner.calls.clear()
    assert S._stop_domain({"kind": "schematic"}) == "schematic"
    with pytest.raises(CLIError) as caught:
        S._stop_domain({})
    assert caught.value.code == "E_USAGE"
    session._write_state({"domain": "pcb"})
    with pytest.raises(CLIError):
        S._stop_domain({"kind": "schematic"})
    assert owner.calls == []


def test_layout_selection_only_requests_layout_health(owner, monkeypatch):
    calls = []

    class Layout:
        def invoke(self, method, params, **kwargs):
            calls.append((method, params))
            assert method == "health" and params == {"domain": "pcb"}
            return {"application_running": True}

    monkeypatch.setattr(S, "native", Layout)
    assert S._stop_domain({"kind": "pcb"}) == "pcb"
    assert S._stop_domain({}) == "pcb"
    assert calls == [("health", {"domain": "pcb"}), ("health", {"domain": "pcb"})]


def test_owner_reference_validation_does_not_accept_extra_capability_fields():
    reference = {
        "protocol": "designer-owned-session-v1",
        "owner_id": "a" * 32,
        "owner_epoch_sha256": "b" * 64,
        "source_bundle_sha256": "c" * 64,
    }
    assert validate_owner_ref(reference) == reference
    with pytest.raises(CLIError):
        validate_owner_ref({**reference, "endpoint": "fixture-private"})
