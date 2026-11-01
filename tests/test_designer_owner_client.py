"""Scoped client routing; all server calls are fake, plus one new file-only proof."""

from __future__ import annotations

import copy
import ctypes
import json
import ntpath
import os
import secrets
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import designer_owner_client as C
from xpedition_cli import native_process as P
from xpedition_cli._vendor.tool_driver_sdk.scope import Scope
from xpedition_cli.backends.native_xpedition import NativeBackend
from xpedition_cli.errors import CLIError

OP = "op-" + "a" * 32
CMD = "owner-command-" + "b" * 32


@pytest.fixture(autouse=True)
def no_cad(monkeypatch, request):
    hits = []

    def forbidden(*a, **k):
        hits.append(True)
        pytest.fail("Client fixtures must not start CAD, query COM or use old adapter")

    monkeypatch.setattr(NativeBackend, "_command_path", forbidden)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    if "real_sibling" not in request.node.name:
        monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    yield
    assert hits == []


def scoped(config):
    config.mkdir(parents=True)
    grant = config / "scope.json"
    grant.write_text('{"fixture":"not a production grant"}')
    return Scope(
        {"status": "granted", "scope_id": "scope-fixture", "effects": ["session"]},
        {"fixture": True},
        str(grant),
    )


@pytest.fixture
def api(monkeypatch, tmp_path):
    source = {"owner.py": "a" * 64}
    data = NS(
        source=source,
        launches=[],
        submits=[],
        observes=[],
        ready=True,
        pending=False,
        failed=None,
        service_phase="public_dispatch_ready",
        service_state="verified_alive",
    )
    actual = {"pid": 123, "executable": "C:/vendor/viewdraw.exe", "creation_time_100ns": "100"}
    seed = {
        "version": 2,
        "domain": "schematic",
        "launcher_identity": {
            "pid": 99,
            "executable": "C:/launcher/viewdraw.exe",
            "creation_time_100ns": "90",
        },
    }
    app = {
        "pid": 123,
        "public_fields": {"FullName": actual["executable"]},
        "document": {"open": False, "path": None, "designs": [], "status": "observed"},
    }

    def read(path, key):
        assert key == b"k" * 32
        if path.name == "intent.json":
            return json.loads(path.read_text()), "fixture-mac-verified"
        return {
            "expected_native_generation": {
                "pid": 123,
                "executable": actual["executable"],
                "birth_filetime_100ns": "100",
            },
            "startup_seed": seed,
            "startup_provenance": {"not_validated_seed": True},
        }, "fixture"

    def context(ticket):
        return ticket.root, b"k" * 32, read(ticket.root / "intent.json", b"k" * 32)[0]

    def launch(root, plan, editing_scope, observation_seconds):
        pending = C.read_pending_start(OP, data.config)
        assert pending["root"] == str(root) and pending["authority_granted"] is False
        data.launches.append((root, copy.deepcopy(plan)))
        root.mkdir()
        (root / "integrity-key.bin").write_bytes(b"k" * 32)
        (root / "intent.json").write_text(
            json.dumps(
                {
                    "execution_epoch": "d" * 32,
                    "source_sha256": source,
                    "editing_scope": editing_scope,
                }
            )
        )
        return {
            "ticket": NS(root=root, epoch="d" * 32),
            "observation": {
                "connection_observation": "session_specific_native" if data.ready else "unobserved",
                "phase": data.service_phase,
                "owner_state": data.service_state,
            },
        }

    def packet(result):
        return {
            "command_id": CMD,
            "observations": []
            if data.pending
            else [{"phase": "command_returned", "details": {"result": result}}],
        }

    def submit(ticket, method, params, **kwargs):
        data.submits.append((method, copy.deepcopy(params), kwargs))
        if data.failed is not None:
            return {
                "command_id": CMD,
                "observations": [
                    {"phase": "command_failed", "details": {"adapter_error": data.failed}}
                ],
            }
        return packet(
            {"application": copy.deepcopy(app), "adapter": {"build": "fixture"}}
            if method == "identity"
            else {"saved": True}
        )

    def observe(ticket, command_id, **kwargs):
        data.observes.append(command_id)
        return packet({"saved": True})

    owner = NS(
        _source_binding=lambda: source,
        _context=context,
        OwnerTicket=lambda root, epoch: NS(root=root, epoch=epoch),
        L=NS(_plain_path=lambda path: path, _read=read),
        launch_public_owner=launch,
        observe_owner=lambda *a, **k: {
            "connection_observation": "session_specific_native" if data.ready else "unobserved",
            "phase": data.service_phase,
            "owner_state": data.service_state,
        },
        submit_owner_adapter_command=submit,
        observe_owner_command=observe,
    )
    monkeypatch.setattr(C, "_modules", lambda: owner)
    monkeypatch.setattr(
        P,
        "resolve_startup_process",
        lambda domain, s, identity: {
            "process_identity": actual,
            "startup_provenance": {"formal": True},
        },
    )
    config = tmp_path / "config"
    scope = scoped(config)
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(config))
    request = C.make_start_request(scope, OP, config, "C:/sdk/XENTP2604/SDD_HOME")
    data.request, data.config, data.owner, data.app, data.actual = (
        request,
        config,
        owner,
        app,
        actual,
    )
    return data


def start(api):
    with C.bind_start_request(api.request):
        handled, reply = C.route_invocation("start", {"domain": "schematic"}, 0.2)
        assert handled
        return reply


def auth(api, **extra):
    return {
        "command": "schematic edit",
        "scope_id": "scope-fixture",
        "scope_sha256": api.request.editing_scope["scope_sha256"],
        "source_operation_id": OP,
        **extra,
    }


def test_start_is_sibling_and_returns_safe_reference_plus_complete_old_shape(api):
    with C.bind_start_request(api.request):
        reply = NativeBackend().invoke("start", {"domain": "schematic"}, timeout_seconds=0.2)
        identity = NativeBackend().invoke("identity", {"domain": "schematic"}, timeout_seconds=0.2)
        assert identity["application"] == reply["application_identity"]
    assert len(api.launches) == 1 and reply["attached"] is True
    assert reply["launcher_pid"] == 99 and reply["startup_seed"] != reply["startup_provenance"]
    assert reply["startup_provenance"] == {"formal": True}
    assert not api.request.root.is_relative_to(api.config)
    assert set(reply["owner_ref"]) == {
        "protocol",
        "owner_id",
        "owner_epoch_sha256",
        "source_bundle_sha256",
    }
    assert str(api.request.root) not in json.dumps(reply)
    assert "d" * 32 not in json.dumps(reply)


def test_start_same_windows_generation_accepts_executable_case_only(api, monkeypatch):
    observed = {**api.actual, "executable": api.actual["executable"].upper()}
    assert observed != api.actual
    if os.name != "nt":
        monkeypatch.setattr(P, "_normalized", lambda path: ntpath.normcase(ntpath.abspath(path)))
    assert P.same_identity(observed, api.actual) is True
    monkeypatch.setattr(
        P,
        "resolve_startup_process",
        lambda *a: {"process_identity": observed, "startup_provenance": {"formal": True}},
    )
    reply = start(api)
    assert reply["process_identity"] == api.actual
    assert reply["startup_provenance"] == {"formal": True}
    assert len(api.launches) == len(api.submits) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"executable": "C:/different-installation/viewdraw.exe"},
        {"creation_time_100ns": "101"},
        {"pid": 124},
        {"creation_time_100ns": None},
    ],
)
def test_start_resolved_generation_mismatch_preserves_unknown(api, monkeypatch, changes):
    monkeypatch.setattr(
        P,
        "resolve_startup_process",
        lambda *a: {
            "process_identity": {**api.actual, **changes},
            "startup_provenance": {"formal": True},
        },
    )
    with pytest.raises(CLIError) as failure:
        start(api)
    assert failure.value.code == "E_OUTCOME_UNKNOWN"
    assert failure.value.details["reason"] == "owner_startup_process_identity_mismatch"
    assert failure.value.details["sdk_operation_id"] == OP
    assert failure.value.details["request_replayed"] is False
    assert failure.value.details["write_attempted"] is True
    assert len(api.launches) == len(api.submits) == 1


def test_start_resolution_failure_has_safe_reason_without_raw_error(api, monkeypatch):
    def denied(*a):
        raise ValueError("C:/private/project epoch=" + "e" * 32)

    monkeypatch.setattr(P, "resolve_startup_process", denied)
    with pytest.raises(CLIError) as failure:
        start(api)
    assert failure.value.code == "E_OUTCOME_UNKNOWN"
    assert failure.value.details["reason"] == "owner_startup_process_resolution_incomplete"
    assert failure.value.details["error_type"] == "ValueError"
    assert "C:/private" not in json.dumps(failure.value.details)
    assert "e" * 32 not in json.dumps(failure.value.details)
    assert len(api.launches) == len(api.submits) == 1


def test_resolved_locator_revalidates_scope_registry_and_routes_original_shape(api):
    reference = start(api)["owner_ref"]
    calls = []
    locator = C.resolve_owner(reference, lambda: calls.append("normal-driver-source-validation"))
    with C.bind_owner_routing(lambda *a: locator, OP, auth(api)):
        assert NativeBackend().invoke("save", {"domain": "schematic"}, timeout_seconds=0.1) == {
            "saved": True
        }
    assert len(calls) >= 2
    assert api.submits[-1][2]["authorization_binding"] == auth(api)


def test_unknown_keeps_sdk_and_owner_ids_and_never_resubmits(api):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    api.pending = True
    before = len(api.submits)
    with (
        C.bind_owner_routing(lambda *a: locator, OP, auth(api)),
        pytest.raises(CLIError) as failure,
    ):
        C.call_owner(locator, "save", {"domain": "schematic"}, 0)
    assert failure.value.code == "E_OUTCOME_UNKNOWN"
    assert failure.value.details["sdk_operation_id"] == OP
    assert failure.value.details["owner_command_id"] == CMD
    assert failure.value.details["request_replayed"] is False
    assert len(api.submits) == before + 1


def test_pending_locator_does_not_outlive_typed_start_context(api):
    start(api)
    with pytest.raises(CLIError, match="Dedicated"):
        C.call_owner(api.request.locator, "identity", {}, 0)


def test_locator_source_or_scope_drift_refuses_before_submit(api):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    before = len(api.submits)
    locator.private["editing_scope"]["scope_id"] = "foreign"
    with C.bind_owner_routing(lambda *a: locator, OP, auth(api)), pytest.raises(CLIError):
        C.call_owner(locator, "save", {}, 0)
    assert len(api.submits) == before


def test_pending_close_validation_does_not_dispatch_com(api):
    reference = start(api)["owner_ref"]
    checks = []
    locator = C.resolve_owner(reference, lambda: checks.append(True))
    before = len(api.submits)
    with C.bind_owner_routing(lambda *a: locator, OP, auth(api)):
        assert C.validate_routed_owner(reference) is None
    assert checks and len(api.submits) == before


def test_optional_source_binding_is_fixed_and_unknown_auth_fields_refuse(api):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    with C.bind_owner_routing(
        lambda *a: locator, OP, auth(api, source_binding={"template_sha256": "a" * 64})
    ):
        C.call_owner(locator, "save", {}, 0)
    before = len(api.submits)
    with (
        C.bind_owner_routing(lambda *a: locator, OP, auth(api, qualified=True)),
        pytest.raises(CLIError),
    ):
        C.call_owner(locator, "save", {}, 0)
    assert len(api.submits) == before


def test_full_locator_inventory_refuses_before_owner_start(api):
    directory = api.config / "dedicated-owner-locators"
    directory.mkdir()
    for index in range(64):
        (directory / (f"{index:032x}.json")).write_text("{}")
    with C.bind_start_request(api.request), pytest.raises(CLIError) as failure:
        C.route_invocation("start", {"domain": "schematic"}, 0.1)
    assert failure.value.code == "E_IO" and api.launches == []
    assert api.request.started is False


def test_known_server_predispatch_rejection_keeps_ids_and_does_not_become_unknown(api):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    api.failed = {
        "code": "E_CONFLICT",
        "details": {
            "native_request_dispatched": False,
            "native_call_unknown": False,
            "reason": "fixed_rejection",
        },
    }
    with (
        C.bind_owner_routing(lambda *a: locator, OP, auth(api)),
        pytest.raises(CLIError) as failure,
    ):
        C.call_owner(locator, "save", {}, 0)
    assert failure.value.code == "E_CONFLICT"
    assert failure.value.details["owner_command_id"] == CMD
    assert failure.value.details["write_attempted"] is False


def test_locator_publication_failure_after_launch_retains_reference_and_unknown(api, monkeypatch):
    original = Path.open

    def failed_publish(path, mode="r", *args, **kwargs):
        if path.parent.name == "dedicated-owner-locators" and mode == "x":
            raise OSError("synthetic locator write failure")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failed_publish)
    with C.bind_start_request(api.request), pytest.raises(CLIError) as failure:
        C.route_invocation("start", {"domain": "schematic"}, 0.1)
    assert failure.value.code == "E_OUTCOME_UNKNOWN" and len(api.launches) == 1
    assert failure.value.details["owner_ref"] == api.request.locator.reference
    assert failure.value.details["write_attempted"] is True
    assert failure.value.details["state_write_attempted"] is True


def test_observe_race_keeps_original_owner_id_without_second_submit(api):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    api.pending = True
    before = len(api.submits)

    def raced(*a, **k):
        raise ValueError("execution_snapshot_not_complete")

    api.owner.observe_owner_command = raced
    with (
        C.bind_owner_routing(lambda *a: locator, OP, auth(api)),
        pytest.raises(CLIError) as failure,
    ):
        C.call_owner(locator, "save", {}, 0.03)
    assert failure.value.details["owner_command_id"] == CMD
    assert len(api.submits) == before + 1


def test_observer_cannot_substitute_a_different_terminal_command_id(api):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    api.pending = True
    api.owner.observe_owner_command = lambda *a, **k: {
        "command_id": "owner-command-" + "c" * 32,
        "observations": [{"phase": "command_returned", "details": {"result": {"saved": True}}}],
    }
    with (
        C.bind_owner_routing(lambda *a: locator, OP, auth(api)),
        pytest.raises(CLIError) as failure,
    ):
        C.call_owner(locator, "save", {}, 0.1)
    assert failure.value.code == "E_OUTCOME_UNKNOWN"
    assert failure.value.details["owner_command_id"] == CMD


@pytest.mark.parametrize(
    "phase", ["owner_unresolved", "owner_cleanup_failed", "owner_cleanup_complete"]
)
def test_nonserving_owner_refuses_new_request_before_submit(api, monkeypatch, phase):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    api.service_phase = phase
    before = len(api.submits)
    monkeypatch.setattr(C.time, "sleep", lambda *a: pytest.fail("Must not wait for full deadline"))
    with (
        C.bind_owner_routing(lambda *a: locator, OP, auth(api)),
        pytest.raises(CLIError) as failure,
    ):
        C.call_owner(locator, "save", {}, 180)
    assert failure.value.code == "E_BACKEND_UNAVAILABLE"
    assert failure.value.details["owner_command_id"] is None
    assert failure.value.details["new_request_published"] is False
    assert failure.value.details["dispatch_state"] == "not_started"
    assert failure.value.details["write_attempted"] is False
    assert failure.value.details["native_request_dispatched"] is False
    assert failure.value.details["owner_service_observation"]["phase"] == phase
    assert len(api.submits) == before
    from xpedition_cli import output
    from xpedition_cli._vendor.tool_driver_sdk.validation import ContractValidator

    # A predispatch refusal must remain consumable by the public CLI contract,
    # rather than being replaced by E_UNKNOWN for an invalid unknown outcome.
    ContractValidator(Path(C.__file__).parent / "_vendor/spec").validate(
        "response", output.failure(failure.value, C.time.monotonic())
    )


def test_owner_becomes_unresolved_after_submit_returns_original_id_without_delay(api, monkeypatch):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    original = api.owner.submit_owner_adapter_command
    api.pending = True
    before = len(api.submits)

    def submitted(*a, **k):
        answer = original(*a, **k)
        api.service_phase = "owner_unresolved"
        return answer

    api.owner.submit_owner_adapter_command = submitted
    monkeypatch.setattr(C.time, "sleep", lambda *a: pytest.fail("Unresolved must stop observation"))
    with (
        C.bind_owner_routing(lambda *a: locator, OP, auth(api)),
        pytest.raises(CLIError) as failure,
    ):
        C.call_owner(locator, "save", {}, 180)
    assert failure.value.details["sdk_operation_id"] == OP
    assert failure.value.details["owner_command_id"] == CMD
    assert failure.value.details["new_request_published"] is True
    assert len(api.submits) == before + 1 and api.observes == [CMD]


def test_cleanup_result_publication_gets_one_final_same_id_read(api):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    original = api.owner.submit_owner_adapter_command
    api.pending = True

    def submitted(*a, **k):
        answer = original(*a, **k)
        api.service_phase, api.service_state = "owner_cleanup_complete", "verified_exited"
        return answer

    api.owner.submit_owner_adapter_command = submitted
    api.owner.observe_owner_command = lambda *a, **k: {
        "command_id": CMD,
        "observations": [{"phase": "command_returned", "details": {"result": {"closed": True}}}],
    }
    with C.bind_owner_routing(lambda *a: locator, OP, auth(api)):
        assert C.call_owner(locator, "close", {}, 180) == {"closed": True}


def test_already_submitted_close_can_wait_through_public_stopping_without_reentry(api):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    original = api.owner.submit_owner_adapter_command
    api.pending = True
    before = len(api.submits)

    def submitted(*a, **k):
        answer = original(*a, **k)
        api.service_phase = "public_dispatch_stopping"
        return answer

    api.owner.submit_owner_adapter_command = submitted
    api.owner.observe_owner_command = lambda *a, **k: {
        "command_id": CMD,
        "observations": [{"phase": "command_returned", "details": {"result": {"closed": True}}}],
    }
    with C.bind_owner_routing(lambda *a: locator, OP, auth(api)):
        assert C.call_owner(locator, "close", {}, 0.2) == {"closed": True}
    assert len(api.submits) == before + 1


@pytest.mark.parametrize(
    "phase,state",
    [
        ("unobserved", "unobservable"),
        ("native_process_exit_observed", "verified_alive"),
        ("owner_cleanup_complete", "verified_exited"),
    ],
)
def test_submitted_close_waits_for_original_terminal_chain_during_shutdown_publication(
    api, phase, state
):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    original = api.owner.submit_owner_adapter_command
    api.pending = True
    before = len(api.submits)
    queries = []

    def submitted(*a, **k):
        answer = original(*a, **k)
        api.service_phase, api.service_state = phase, state
        return answer

    def observe(ticket, command_id, **kwargs):
        queries.append(command_id)
        return {
            "command_id": CMD,
            "observations": []
            if len(queries) == 1
            else [
                {
                    "phase": "command_returned",
                    "details": {"result": {"closed": True, "fixed_receipt_fixture": True}},
                }
            ],
        }

    api.owner.submit_owner_adapter_command = submitted
    api.owner.observe_owner_command = observe
    with C.bind_owner_routing(lambda *a: locator, OP, auth(api)):
        assert C.call_owner(locator, "close", {}, 0.2) == {
            "closed": True,
            "fixed_receipt_fixture": True,
        }
    assert len(api.submits) == before + 1
    assert queries == [CMD, CMD]


def test_cleanup_and_process_exit_without_terminal_receipt_remain_bounded_unknown(api):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    original = api.owner.submit_owner_adapter_command
    api.pending = True
    before = len(api.submits)

    def submitted(*a, **k):
        answer = original(*a, **k)
        api.service_phase, api.service_state = "owner_cleanup_complete", "verified_exited"
        return answer

    api.owner.submit_owner_adapter_command = submitted
    with (
        C.bind_owner_routing(lambda *a: locator, OP, auth(api)),
        pytest.raises(CLIError) as failure,
    ):
        C.call_owner(locator, "close", {}, 0.035)
    assert failure.value.code == "E_OUTCOME_UNKNOWN"
    assert failure.value.details["owner_command_id"] == CMD
    assert failure.value.details["sdk_operation_id"] == OP
    assert failure.value.details["request_replayed"] is False
    assert len(api.submits) == before + 1 and api.observes
    assert set(api.observes) == {CMD}


def test_received_diagnostic_failure_is_not_project_write_or_state_drift(api):
    from xpedition_cli import designer_native_owner as O

    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    error = ValueError("fixed diagnostic failure")
    error.native_boundary_trace_incomplete = True
    error.native_call_returned = True
    error.engineering_write_attempted = False
    error.fixed_document_member_attempted = False
    error.fixed_document_cleanup_observed = None
    error.fixed_document_failed_callpoint = "git.create_comtypes_table"
    api.failed = O._adapter_failure(error)
    before = len(api.submits)
    with (
        C.bind_owner_routing(lambda *args: locator, OP, auth(api)),
        pytest.raises(CLIError) as failed,
    ):
        C.call_owner(locator, "clone_project", {}, 0.1)
    assert failed.value.code == "E_OUTCOME_UNKNOWN"
    assert failed.value.details["owner_command_received"] is True
    assert failed.value.details["native_metadata_call_entered"] is True
    assert failed.value.details["native_request_dispatched"] is True
    assert failed.value.details["write_attempted"] is False
    assert failed.value.details["engineering_write_attempted"] is False
    assert failed.value.details["fixed_document_cleanup_observed"] is None
    assert "conflict" not in failed.value.details
    assert (
        "Nothing" not in failed.value.message and failed.value.details["request_replayed"] is False
    )
    assert len(api.submits) == before + 1


def test_readonly_preview_failure_without_sdk_operation_stays_contract_valid(api):
    from xpedition_cli import designer_native_owner as O
    from xpedition_cli import output
    from xpedition_cli._vendor.tool_driver_sdk.validation import ContractValidator

    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    query = "query-readonly-preview-with-no-sdk-operation"
    api.failed = O._adapter_failure(O.OwnerError("native_boundary_trace_incomplete"))
    before = len(api.submits)
    with (
        C.bind_owner_routing(
            lambda *args: locator,
            query,
            auth(api, command="session close", source_operation_id=query),
        ),
        pytest.raises(CLIError) as failed,
    ):
        C.call_owner(locator, "identity", {}, 0.1)
    error = failed.value
    assert error.code == "E_BACKEND_UNAVAILABLE"
    assert error.details["readonly_request_id"] == query
    assert error.details["owner_command_received"] is True
    assert error.details["native_request_dispatched"] is True
    assert error.details["native_observation_dispatch_state"] == "dispatched"
    assert error.details["dispatch_state"] == "not_started"
    assert error.details["dispatch_state_scope"] == "effectful_sdk_operation_only"
    assert error.details["engineering_write_attempted"] is False
    assert error.details["write_attempted"] is False
    assert error.details["owner_command_lookup"]["queues_new_request"] is False
    assert "operation_id" not in error.details and "sdk_operation_id" not in error.details
    assert "conflict" not in error.details and len(api.submits) == before + 1
    envelope = output.failure(error, C.time.monotonic())
    assert envelope["error"]["code"] != "E_UNKNOWN"
    ContractValidator(Path(C.__file__).parent / "_vendor/spec").validate("response", envelope)


def test_transient_global_observation_does_not_authorize_another_request(api):
    locator = C.resolve_owner(start(api)["owner_ref"], lambda: None)
    api.service_phase, api.service_state = "unobserved", "unobservable"
    before = len(api.submits)
    with (
        C.bind_owner_routing(lambda *a: locator, OP, auth(api)),
        pytest.raises(CLIError) as failure,
    ):
        C.call_owner(locator, "close", {}, 0.1)
    assert failure.value.code == "E_BACKEND_UNAVAILABLE"
    assert failure.value.details["dispatch_state"] == "not_started"
    assert failure.value.details["new_request_published"] is False
    assert failure.value.details["native_request_dispatched"] is False
    assert len(api.submits) == before


def test_start_ever_connected_but_unresolved_never_queues_identity(api, monkeypatch):
    api.service_phase = "owner_unresolved"
    monkeypatch.setattr(C.time, "sleep", lambda *a: pytest.fail("Must not wait 180 seconds"))
    with C.bind_start_request(api.request), pytest.raises(CLIError) as failure:
        C.route_invocation("start", {"domain": "schematic"}, 180)
    assert (
        failure.value.code == "E_OUTCOME_UNKNOWN"
        and failure.value.details["sdk_operation_id"] == OP
    )
    assert failure.value.details["owner_ref"] == api.request.locator.reference
    assert len(api.launches) == 1 and api.submits == []


def test_start_waits_for_explicit_public_dispatch_ready_not_only_handoff(api):
    api.service_phase = "native_connection_observed"
    calls = []

    def service(*a, **k):
        calls.append(True)
        api.service_phase = "public_dispatch_ready"
        return {
            "phase": api.service_phase,
            "owner_state": "verified_alive",
            "connection_observation": "session_specific_native",
        }

    api.owner.observe_owner = service
    reply = start(api)
    assert calls and reply["automation_ready"] is True
    assert len(api.submits) == 1


def test_pending_start_publication_failure_does_not_launch_owner(api, monkeypatch):
    original = Path.open

    def failed(path, mode="r", *args, **kwargs):
        if path.parent.name == "pending-starts" and mode == "x":
            raise OSError("synthetic pending publication failure")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failed)
    with C.bind_start_request(api.request), pytest.raises(CLIError) as failure:
        C.route_invocation("start", {"domain": "schematic"}, 0)
    assert failure.value.code == "E_IO" and api.launches == []
    assert api.request.started is False


def test_after_launch_failure_keeps_original_pending_address_without_relaunch(api):
    original = api.owner.launch_public_owner

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic caller observation failed")

    api.owner.launch_public_owner = failed
    with C.bind_start_request(api.request):
        with pytest.raises(CLIError) as failure:
            C.route_invocation("start", {"domain": "schematic"}, 0)
        assert failure.value.code == "E_OUTCOME_UNKNOWN"
        assert failure.value.details["sdk_operation_id"] == OP
        assert failure.value.details["owner_id"] == api.request.owner_id
        with pytest.raises(CLIError):
            C.route_invocation("start", {"domain": "schematic"}, 0)
    pending = C.read_pending_start(OP, api.config)
    assert pending["root"] == str(api.request.root) and len(api.launches) == 1
    assert pending["original_outcome"] == "unknown"
    assert "epoch" not in json.dumps(pending)


def test_json_flags_and_pcb_cannot_activate_owner_route(api):
    assert C.owner_route_available("identity", {"domain": "schematic", "qualified": True}) is False
    with C.bind_owner_routing(lambda *a: pytest.fail("PCB resolver reached"), OP, auth(api)):
        assert C.owner_route_available("identity", {"domain": "pcb"}) is False
        assert C.route_invocation("designer_host_bootstrap", {"domain": "schematic"}, 0) == (
            False,
            None,
        )
    with pytest.raises(CLIError):
        C.validate_owner_ref({"qualified": True, "pid": 123})


@pytest.mark.parametrize(
    "method",
    [
        "batch_drc",
        "board_geometry",
        "board_info",
        "board_view_state",
        "cell_refresh_state",
        "hand_route",
        "manufacturing_output",
        "move_component",
        "outline_state",
        "placement_batch",
        "render_board",
        "routepass_state",
        "show_board",
    ],
)
def test_layout_specific_methods_with_project_do_not_use_designer_owner(api, method):
    selection = {"project": "C:/task/board.prj"}
    with C.bind_owner_routing(lambda *a: pytest.fail("Designer resolver reached"), OP, auth(api)):
        assert C.owner_route_available(method, selection) is False
        assert C.route_invocation(method, selection, 0.1) == (False, None)


def test_ambiguous_project_and_explicit_designer_still_require_owner(api):
    with C.bind_owner_routing(lambda *a: None, OP, auth(api)):
        for method, selection in [
            ("snapshot", {"project": "C:/task/design.prj"}),
            ("board_geometry", {"domain": "schematic", "project": "C:/task/design.prj"}),
        ]:
            assert C.owner_route_available(method, selection) is True
            with pytest.raises(CLIError) as error:
                C.route_invocation(method, selection, 0.1)
            assert error.value.details["reason"] == "validated_owner_locator_required"


@pytest.mark.skipif(os.name != "nt", reason="Explicit new Windows file-only proof")
def test_real_sibling_inputs_allow_three_config_atomic_replacements(monkeypatch):
    from xpedition_cli.startup_input_seal import InputsLease

    root = Path("C:/ExampleProjects") / (
        "xpedition-client-sibling-proof-20261027-" + secrets.token_hex(8)
    )
    root.mkdir(exist_ok=False)
    config = root / "config"
    scope = scoped(config)
    monkeypatch.setattr(C, "_modules", lambda: NS(_source_binding=lambda: {"fixture": "a" * 64}))
    request = C.make_start_request(scope, OP, config, "C:/sdk/XENTP2604/SDD_HOME")
    request.root.mkdir(parents=True)
    lease = InputsLease(request.root / "inputs")
    try:
        lease.prepare({"identity.vbs": b"' synthetic no CAD\n", "scripts.ini": b"[ViewDraw]\n"})
        assert lease.verify_prelaunch()["input_contract_verified"] is True
        paths = [
            config / "session.json",
            config / "native-ownership.json",
            config / "dedicated-owner-locators" / ("a" * 32 + ".json"),
        ]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old synthetic metadata")
            temp = path.with_suffix(".tmp")
            temp.write_bytes(b"new synthetic metadata")
            os.replace(temp, path)
            assert path.read_bytes() == b"new synthetic metadata"
        assert lease.verify_prelaunch()["input_contract_verified"] is True
    finally:
        facts = lease.terminal_release()
    assert facts["resources_released"] is True and facts["files_removed"] is False
    (root / "result.json").write_text(
        json.dumps(
            {
                "passed": True,
                "cad_used": False,
                "com_used": False,
                "config_atomic_replace_count": 3,
                "inputs_sibling_to_config": not request.root.is_relative_to(config),
                "retained_readonly_inputs": True,
                "account_wide_quota": "not_implemented",
            }
        )
    )
