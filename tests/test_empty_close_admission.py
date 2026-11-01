"""Ordinary empty Quit across retired generations; recording fake CAD/OS only."""

from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace

import pytest
from test_diagnostic_exit import _exit_args, _install_exit_worker

from xpedition_cli import native_com_adapter as bridge
from xpedition_cli import native_process
from xpedition_cli.cli import sessions
from xpedition_cli.errors import CLIError

pytest_plugins = ("test_diagnostic_exit",)


def _run_write(cli, args):
    preview = cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.envelope["ok"], result.envelope
    return result


def _raw(store, operation_id):
    row = (
        store.connection()
        .execute(
            "SELECT status,receipt,resolution,extra FROM operations WHERE operation_id=?",
            (operation_id,),
        )
        .fetchone()
    )
    return tuple(row) if row else None


@pytest.fixture
def retired(completed_diagnostic, monkeypatch, request):
    case, diagnostic_id, original = completed_diagnostic
    observe_launchers = sessions._observe_owned_launchers
    _install_exit_worker(case, monkeypatch)
    # Keep the product's complete launcher-chain aggregation. Only OS facts are fake.
    monkeypatch.setattr(sessions, "_observe_owned_launchers", observe_launchers)

    def observe_exit(domain, pid, *, timeout=0, expected_identity=None):
        assert domain == "pcb" and expected_identity["pid"] == int(pid)
        ended = not case.startup.alive[int(pid)]
        return {
            "pid": None if ended else int(pid),
            "process_identity": copy.deepcopy(expected_identity),
            "process_absence_observed": ended,
            "exit_observation": {
                "source": "pinned Windows HANDLE/GetProcessId/GetProcessTimes/WaitForSingleObject",
                "target_pid": str(pid),
                "original_generation_ended": ended,
                "pid_reused": False,
                "wait_result": 0 if ended else 258,
                "observed_process_identity": copy.deepcopy(expected_identity),
                "observations": 1,
                "elapsed_seconds": 0.0,
                "budget_seconds": timeout,
                "image_observation": "unobserved_after_exit",
                "image_query_winerror": None,
            },
        }

    monkeypatch.setattr(sessions, "observe_exit", observe_exit)
    monkeypatch.setattr(native_process, "observe_exit", observe_exit)
    old_identity = copy.deepcopy(case.startup.identities[22220])
    exited = _run_write(case.cli, _exit_args(diagnostic_id, token=case.exit_qualification_token))
    store = case.cli._store_open()
    exit_id = exited.envelope["data"]["receipt"]["operation_id"]
    assert store.get(diagnostic_id) == original
    # Simulate new OS generations, not resurrection of the previously pinned handles.
    for identity in case.startup.identities.values():
        identity["creation_time_100ns"] = str(int(identity["creation_time_100ns"]) + 100000000)
    case.startup.alive.update({22220: True, 24552: True})
    case.startup.launched = case.startup.opened = False
    case.dispatched = False
    component = None
    if getattr(request, "param", None) == "component":
        from test_component_connect_sdk import sdk_case

        environment = {
            key: os.environ.get(key)
            for key in ("XPEDITION_CLI_CONFIG_DIR", "XPEDITION_CLI_SCOPE_FILE")
        }
        root = case.tmp_path / "component"
        root.mkdir()
        import test_board_component_connect as component_fixture

        original_backup = component_fixture.backup

        def backup_with_real_board_relation(path, source_root, project):
            # The component-only fixture historically has a minimal .prj. This
            # integration needs a real parseable physical board relationship.
            # Populate both trees before their archive/genesis pins are made.
            project_bytes = (
                b"SECTION DesignInfo\nENDSECTION\n"
                b'SECTION iCDB\nKEY iCDBDir "./database"\nLIST Designs\n'
                b'VALUE "Board1"\nENDLIST\nENDSECTION\n'
                b'SECTION Board1\nKEY ConfigType "PCB"\n'
                b'KEY PCBDesignPath "PCB/Board.pcb"\nENDSECTION\n'
            )
            project.write_bytes(project_bytes)
            (source_root.parent / "isolated/Board.prj").write_bytes(project_bytes)
            return original_backup(path, source_root, project)

        monkeypatch.setattr(component_fixture, "backup", backup_with_real_board_relation)
        component = sdk_case(root, monkeypatch)
        for key, value in environment.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        identity = case.startup.identities[22220]
        component.case.identity["application"]["pid"] = identity["pid"]
        component.case.identity["generation"] = {
            "pid": identity["pid"],
            "executable": identity["executable"],
            "birth_filetime_100ns": identity["creation_time_100ns"],
            "alive": True,
        }
        new_board = component.case.board
        document = component.case.application.ActiveDocument
        original_close = document.Close

        def close_with_unapproved_output(save):
            result = original_close(save)
            (component.case.project.parent / "undeclared-after-close.dat").write_bytes(
                b"new output"
            )
            case.startup.opened = False
            return result

        monkeypatch.setattr(document, "Close", close_with_unapproved_output)
    else:
        new_board = case.tmp_path / "new-project/Board.pcb"
        new_board.parent.mkdir()
        new_board.write_bytes(b"new independent synthetic board")
    original_invoke = case.backend._invoke

    def invoke(method, params, **kwargs):
        if component is not None and method in {"hand_route", "board_info"}:
            case.requests.append(method)
            if method == "board_info" and component.case.application.ActiveDocument is None:
                raise CLIError("E_NOT_FOUND", "Synthetic returned adapter error: document closed")
            return component.backend.invoke(method, params, **kwargs)
        if method == "close":
            case.requests.append(method)
            assert params["save_project"] is None
            try:
                return bridge.dispatch(method, params)
            except bridge.AdapterError as error:
                raise CLIError(error.code, error.message, error.details) from error
        result = original_invoke(method, params, **kwargs)
        if method == "identity":
            result["application"]["document"] = {
                "open": case.startup.opened,
                "path": str(new_board) if case.startup.opened else None,
                "status": "observed",
            }
        if method in {"open", "snapshot"}:
            result["project"] = str(new_board)
        return result

    monkeypatch.setattr(case.backend, "_invoke", invoke)
    started = _run_write(
        case.cli,
        ["session", "start", "--kind", "pcb", "--project", str(new_board), "--quiet"],
    )
    case.startup.opened = component is not None
    if component is not None:
        from xpedition_cli.cli import pcb
        from xpedition_cli.operation_budget import native_call_budget

        # Both the public handler and independent SDK observations must use the
        # same recording transport, as they do with the real NativeBackend.
        monkeypatch.setattr(pcb, "native", lambda: case.backend)

        def transport_invoke(method, params, **kwargs):
            error = None
            with native_call_budget(method, kwargs.get("timeout_seconds", 30.0)):
                try:
                    result = invoke(method, params, **kwargs)
                except CLIError as caught:
                    if caught.code == "E_TIMEOUT":
                        raise
                    # NativeBackend parses returned adapter errors after its
                    # transport budget context has completed, not inside it.
                    error = caught
            if error is not None:
                raise error
            return result

        monkeypatch.setattr(case.backend, "invoke", transport_invoke)
    _pythoncom, client = bridge._import_com()
    app = client.GetActiveObject("MGCPCB.Application.151")
    monkeypatch.setattr(bridge, "_application", lambda *args, **kwargs: app)
    # Ordinary close uses the generic wrapper path; model its local cache setup
    # without touching real generated COM wrappers in this Linux-only fixture.
    monkeypatch.setattr(bridge, "_heal_gen_py_cache", lambda: case.events.append("wrapper-cache"))
    monkeypatch.setattr(bridge, "application_identity", lambda app, domain: {"pid": 22220})
    value = SimpleNamespace(
        case=case,
        store=store,
        diagnostic_id=diagnostic_id,
        exit_id=exit_id,
        startup_id=started.envelope["data"]["receipt"]["operation_id"],
        old_identity=old_identity,
        new_board=new_board,
        component=component,
        args=["session", "close", "--kind", "pcb", "--pid", "22220", "--quiet"],
        original=_raw(store, diagnostic_id),
    )
    case.requests.clear()
    case.events.clear()
    return value


def _refused_locally(value):
    case = value.case
    requests, events = list(case.requests), list(case.events)
    result = case.cli.run([*value.args, "--dry-run"])
    assert not result.envelope["ok"], result.envelope
    assert case.requests == requests and case.events == events
    return result


def test_retired_diagnostic_allows_only_new_owned_empty_quit(retired):
    value, case = retired, retired.case
    _run_write(case.cli, value.args)
    assert case.events.count("Application.Quit") == 1
    assert not any(
        event.startswith("Document.Save") or event.startswith("Document.Close")
        for event in case.events
    )
    assert _raw(value.store, value.diagnostic_id) == value.original
    requests, events = list(case.requests), list(case.events)
    repeated = case.cli.run([*value.args, "--dry-run"])
    assert not repeated.envelope["ok"], repeated.envelope
    assert case.requests == requests and case.events == events


@pytest.mark.parametrize("missing", ["exit", "startup"])
def test_missing_original_ledger_cannot_be_replaced_by_cached_ownership(retired, missing):
    operation_id = {
        "diagnostic": retired.diagnostic_id,
        "exit": retired.exit_id,
        "startup": retired.startup_id,
    }[missing]
    retired.store.connection().execute(
        "DELETE FROM operations WHERE operation_id=?", (operation_id,)
    )
    _refused_locally(retired)


@pytest.mark.parametrize("field", ["receipt", "resolution", "scope_id"])
def test_changed_exit_ledger_never_grants_empty_close(retired, field):
    if field == "receipt":
        value = retired.store.get(retired.exit_id)["receipt"]
        value["extensions"]["result"]["process_absence_observed"] = False
        value = json.dumps(value)
    elif field == "resolution":
        value = json.dumps({"outcome": "not_applied"})
    else:
        value = "foreign-scope"
    retired.store.connection().execute(
        f"UPDATE operations SET {field}=? WHERE operation_id=?", (value, retired.exit_id)
    )
    _refused_locally(retired)


def test_reused_current_pid_without_owned_birth_refuses_before_com(retired):
    retired.case.startup.identities[22220]["creation_time_100ns"] = "133999999999999999"
    _refused_locally(retired)


def test_current_nonempty_document_never_quits(retired):
    retired.case.startup.opened = True
    result = retired.case.cli.run([*retired.args, "--dry-run"])
    assert not result.envelope["ok"], result.envelope
    assert "Application.Quit" not in retired.case.events
    assert "Document.Save" not in retired.case.events
    assert "Document.Close(True)" not in retired.case.events


def _change_exit_result(value, change):
    receipt = value.store.get(value.exit_id)["receipt"]
    result = receipt["extensions"]["result"]
    change(result)
    # Keep duplicate DTOs equal, so the negative checks the missing native/OS
    # evidence rather than merely noticing the copied snapshot differs.
    receipt["snapshot_after"]["extensions"]["facts"]["diagnostic_exit"] = copy.deepcopy(result)
    value.store.connection().execute(
        "UPDATE operations SET receipt=? WHERE operation_id=?", (json.dumps(receipt), value.exit_id)
    )


@pytest.mark.parametrize(
    "fault",
    [
        "old_alive",
        "ack_only",
        "launcher_alive",
        "launcher_pid_reused",
        "launcher_birth",
        "launcher_missing",
        "rot_only",
    ],
)
def test_old_exit_needs_exact_retired_application_and_launcher_evidence(retired, fault):
    def change(result):
        if fault == "old_alive":
            result.update(closed=False, process_absence_observed=False, closing_pending=True)
        elif fault == "ack_only":
            result.pop("exit_observation")
        elif fault == "rot_only":
            result["exit_observation"]["source"] = "COM unregistration"
        else:
            chain = result["owned_launch_chain"]
            if fault == "launcher_missing":
                chain["associated_processes"] = []
                return
            observation = chain["associated_processes"][0]["observation"]
            if fault == "launcher_alive":
                observation["process_absence_observed"] = False
                observation["exit_observation"]["wait_result"] = 258
            elif fault == "launcher_pid_reused":
                observation["exit_observation"]["pid_reused"] = True
            else:
                observation["exit_observation"]["observed_process_identity"][
                    "creation_time_100ns"
                ] = "1"

    _change_exit_result(retired, change)
    _refused_locally(retired)
    assert _raw(retired.store, retired.diagnostic_id) == retired.original


def test_reclaimed_copy_qualification_is_not_required_or_resurrected(retired):
    from xpedition_cli import diagnostic_exit as E

    copy_id = retired.case.origin["copy_operation_id"]
    assert (
        retired.store.connection()
        .execute(f"SELECT 1 FROM {E.TABLE} WHERE diagnostic_id=?", (retired.diagnostic_id,))
        .fetchone()
        is None
    )
    retired.store.connection().execute(
        "DELETE FROM pcb_close_qualification_v1 WHERE copy_operation_id=?", (copy_id,)
    )
    _run_write(retired.case.cli, retired.args)
    assert (
        retired.store.connection()
        .execute("SELECT 1 FROM pcb_close_qualification_v1 WHERE copy_operation_id=?", (copy_id,))
        .fetchone()
        is None
    )
    assert _raw(retired.store, retired.diagnostic_id) == retired.original


def test_exit_evidence_changed_after_preview_stops_before_com(retired):
    preview = retired.case.cli.run([*retired.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    _change_exit_result(retired, lambda result: result.update(process_absence_observed=False))
    requests, events = list(retired.case.requests), list(retired.case.events)
    result = retired.case.cli.run(
        [*retired.args, "--confirm", preview.envelope["data"]["confirm_token"]]
    )
    assert not result.envelope["ok"], result.envelope
    assert retired.case.requests == requests and retired.case.events == events
    assert _raw(retired.store, retired.diagnostic_id) == retired.original


def test_candidate_change_never_reuses_retired_exit_authority(retired, monkeypatch):
    from xpedition_cli import empty_close_admission as A

    original = A.adapter_identity

    def changed():
        result = original()
        result["build_sha256"] = "f" * 64
        return result

    monkeypatch.setattr(A, "adapter_identity", changed)
    _refused_locally(retired)


def _returned_component_failure(value):
    preview = value.case.cli.run([*value.component.args, "--dry-run", "--quiet"])
    assert preview.envelope["ok"], preview.envelope
    result = value.case.cli.run(
        [*value.component.args, "--confirm", preview.envelope["data"]["confirm_token"], "--quiet"]
    )
    assert result.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", result.envelope
    receipt = result.envelope["error"]["details"]["receipt"]
    native = receipt["extensions"]["handler_error"]["details"]["native_result"]
    assert native["post_write_error"]["stage"] == "closed_snapshot"
    assert (
        native["post_write_error"]["message"]
        == "Closed tree contains unreadable or undeclared protected changes"
    )
    assert native["persistence_verified"] is False
    assert [event["call"] for event in native["native_calls_returned"]] == [
        "Net.UniqueId",
        "TransactionStart",
        "PutTrace",
        "Trace.UniqueId",
        "TransactionEnd",
        "Save",
        "Close",
    ]
    return receipt["operation_id"]


@pytest.mark.parametrize("retired", ["component"], indirect=True)
def test_returned_component_closed_policy_failure_allows_only_empty_quit(retired):
    operation_id = _returned_component_failure(retired)
    original = _raw(retired.store, operation_id)
    events = list(retired.component.case.events)
    _run_write(retired.case.cli, retired.args)
    assert retired.component.case.events == events
    assert retired.case.events.count("Application.Quit") == 1
    assert _raw(retired.store, operation_id) == original
    assert _raw(retired.store, retired.diagnostic_id) == retired.original


@pytest.mark.parametrize("retired", ["component"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "transaction_unknown",
        "transaction_not_ended",
        "close_untyped",
        "close_missing",
        "open_started",
        "call_timeout",
        "call_missing",
        "foreign_generation",
        "counter_repeat",
    ],
)
def test_incomplete_or_changed_current_native_terminal_never_allows_quit(retired, fault):
    operation_id = _returned_component_failure(retired)
    receipt = retired.store.get(operation_id)["receipt"]
    evidence = receipt["extensions"]
    native = evidence["handler_error"]["details"]["native_result"]
    if fault == "transaction_unknown":
        native["transaction_state_unknown"] = True
    elif fault == "transaction_not_ended":
        native["transaction_state"] = "started"
    elif fault == "close_untyped":
        native["native_calls_returned"][-1]["reply"] = {"type": "int", "value": 1}
    elif fault == "close_missing":
        native["native_calls_returned"].pop()
    elif fault == "open_started":
        native["stages"]["reopen"] = "started"
    elif fault == "call_timeout":
        evidence["operation_budget"]["native_calls"][4]["status"] = "failed"
    elif fault == "call_missing":
        evidence["operation_budget"]["native_calls"].pop()
    elif fault == "foreign_generation":
        native["trace"]["identity"]["generation"]["birth_filetime_100ns"] = "123"
    else:
        native["counters"]["TransactionEnd"]["attempted"] = 2
    evidence["result"] = copy.deepcopy(native)
    retired.store.connection().execute(
        "UPDATE operations SET receipt=? WHERE operation_id=?", (json.dumps(receipt), operation_id)
    )
    current = _raw(retired.store, operation_id)
    _refused_locally(retired)
    assert _raw(retired.store, operation_id) == current
    assert _raw(retired.store, retired.diagnostic_id) == retired.original


@pytest.mark.parametrize("retired", ["component"], indirect=True)
def test_current_adapter_transport_timeout_never_allows_empty_close(retired, monkeypatch):
    original = retired.component.backend.invoke

    def timeout(method, params, **kwargs):
        if method == "hand_route" and params.get("apply") is True:
            raise CLIError(
                "E_TIMEOUT",
                "Synthetic adapter transport did not return",
                {"native_request_dispatched": True},
            )
        return original(method, params, **kwargs)

    monkeypatch.setattr(retired.component.backend, "invoke", timeout)
    preview = retired.case.cli.run([*retired.component.args, "--dry-run", "--quiet"])
    assert preview.envelope["ok"], preview.envelope
    result = retired.case.cli.run(
        [*retired.component.args, "--confirm", preview.envelope["data"]["confirm_token"], "--quiet"]
    )
    assert not result.envelope["ok"], result.envelope
    retired.case.startup.opened = False
    _refused_locally(retired)


def test_unrelated_finished_close_history_does_not_consume_retirement_census(retired):
    # Background journal rows only: these are deliberately not authority and are
    # never supplied as exit evidence. Ordinary closes have no diagnostic proof.
    connection = retired.store.connection()
    for index in range(65):
        connection.execute(
            "INSERT INTO operations (operation_id, capability_id, command, status, effects, "
            "plan, receipt, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"op-backgroundclose{index:08d}",
                "synthetic-ordinary-background-close",
                "session close",
                "succeeded",
                '["session"]',
                json.dumps({"target": {"extensions": {"command": "session close"}}}),
                "{}",
                1.0,
                1.0,
            ),
        )
    _run_write(retired.case.cli, retired.args)
    assert retired.case.events.count("Application.Quit") == 1
    assert _raw(retired.store, retired.diagnostic_id) == retired.original


@pytest.mark.parametrize("fault", [None, "response_candidate", "worker_build_gate"])
def test_first_identity_is_generation_build_bound_and_environment_is_restored(
    retired, monkeypatch, fault
):
    case = retired.case
    fields = (
        "XPEDITION_EXPECTED_PID",
        "XPEDITION_EXPECTED_DOMAIN",
        "XPEDITION_EXPECTED_ADAPTER_BUILD",
        "XPEDITION_REQUIRE_EXISTING",
    )
    previous = {key: "previous-" + key.lower() for key in fields}
    for key, value in previous.items():
        monkeypatch.setenv(key, value)
    startup = retired.store.get(retired.startup_id)
    worker = startup["receipt"]["snapshot_after"]["extensions"]["facts"]["worker_adapter"]
    expected = dict(zip(fields, ("22220", "pcb", worker["build_sha256"], "1"), strict=True))
    seen = []
    original = case.backend._invoke

    def invoke(method, params, **kwargs):
        if method == "identity":
            observed = {key: os.environ.get(key) for key in fields}
            seen.append(observed)
            assert observed == expected
            if fault == "worker_build_gate":
                try:
                    return bridge.dispatch(method, params)
                except bridge.AdapterError as error:
                    raise CLIError(error.code, error.message, error.details) from error
        result = original(method, params, **kwargs)
        if method == "identity" and fault == "response_candidate":
            result["adapter"]["build_sha256"] = "f" * 64
        return result

    monkeypatch.setattr(case.backend, "_invoke", invoke)
    if fault == "worker_build_gate":
        # Exercise the real worker's expected-build gate, before _import_com.
        original_identity = bridge.adapter_identity

        def different_worker():
            return {**original_identity(), "build_sha256": "f" * 64}

        monkeypatch.setattr(bridge, "adapter_identity", different_worker)
        monkeypatch.setattr(bridge, "_import_com", lambda: pytest.fail("COM before build gate"))
    if fault is None:
        _run_write(case.cli, retired.args)
        assert case.events.count("Application.Quit") == 1
    else:
        result = case.cli.run([*retired.args, "--dry-run"])
        assert not result.envelope["ok"], result.envelope
        assert len(seen) == 1
        assert "Application.Quit" not in case.events
    assert seen
    assert {key: os.environ.get(key) for key in fields} == previous
    assert _raw(retired.store, retired.diagnostic_id) == retired.original
