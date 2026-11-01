"""Public SDK qualification and empty-exit path, with recording fake CAD only."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from xpedition_cli import close_diagnostic as D
from xpedition_cli import diagnostic_exit as E
from xpedition_cli import driver, native_process
from xpedition_cli import native_close_diagnostic as native_exit
from xpedition_cli import native_com_adapter as bridge
from xpedition_cli.backends import native_xpedition
from xpedition_cli.cli import sessions
from xpedition_cli.errors import CLIError

pytest_plugins = ("test_close_diagnostic",)


@pytest.fixture
def completed_diagnostic(case):
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    case.diagnostic_confirm_token = preview.envelope["data"]["confirm_token"]
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", result.envelope
    details = result.envelope["error"]["details"]
    assert details["diagnostic_complete"] is True, result.envelope
    case.exit_qualification_token = details["diagnostic_exit_token"]
    from xpedition_cli._vendor.tool_driver_sdk import tokens

    claims = tokens.verify(case.cli._store_open().secret(), case.exit_qualification_token, 0)
    assert claims["budget_sha256"] == E.digest(details["diagnostic_exit_validated_budget"])
    assert (
        details["operation_budget"]["wall_ms"]
        >= details["diagnostic_exit_validated_budget"]["wall_ms"]
    )
    operation_id = details["receipt"]["operation_id"]
    store = case.cli._store_open()
    row = (
        store.connection()
        .execute(
            f"SELECT state,record_sha256,budget_sha256,exit_operation_id FROM {E.TABLE} "
            "WHERE diagnostic_id=?",
            (operation_id,),
        )
        .fetchone()
    )
    assert row and row[0] == "ready" and row[1] and row[2] and row[3] is None, {
        "row": tuple(row) if row else None,
        "envelope": result.envelope,
        "stderr": result.stderr,
    }
    return case, operation_id, copy.deepcopy(store.get(operation_id))


def test_final_response_seals_one_same_journal_qualification(completed_diagnostic):
    case, operation_id, original = completed_diagnostic
    scope = case.cli._scope()
    proof = E.require_ready(
        case.cli._store_open(),
        operation_id,
        case.cli.validator,
        scope,
        qualification_token=case.exit_qualification_token,
    )
    assert proof["diagnostic_id"] == operation_id
    assert original["status"] == "unknown" and original["resolution"] is None
    assert case.cli._store_open().get(operation_id) == original


def test_published_qualification_does_not_expire_with_original_budget(
    completed_diagnostic, monkeypatch
):
    case, operation_id, original = completed_diagnostic
    store = case.cli._store_open()
    proof = E.require_ready(
        store,
        operation_id,
        case.cli.validator,
        case.cli._scope(),
        qualification_token=getattr(case, "exit_qualification_token", None),
    )
    monkeypatch.setattr(time, "monotonic", lambda: 10**12)
    assert (
        E.require_ready(
            store,
            operation_id,
            case.cli.validator,
            case.cli._scope(),
            qualification_token=getattr(case, "exit_qualification_token", None),
        )
        == proof
    )
    assert "seal_deadline" not in proof
    assert store.get(operation_id) == original


def test_changed_publication_proof_refuses_before_any_cad(completed_diagnostic):
    case, operation_id, original = completed_diagnostic
    store = case.cli._store_open()
    store.connection().execute(
        f"UPDATE {E.TABLE} SET publication_sha256=? WHERE diagnostic_id=?",
        ("0" * 64, operation_id),
    )
    _refused_without_cad(
        case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    )
    assert store.get(operation_id) == original


@pytest.mark.parametrize("missing", ["stages", "native_calls"])
def test_empty_budget_evidence_never_authorizes(missing):
    budget = {
        "mode": "confirm",
        "dispatch_started": True,
        "failed_stage": None,
        "first_failed_stage": None,
        "stages": [
            {"stage": stage, "status": "completed"}
            for stage in ("observe_confirm", "dispatch", "observe_after")
        ],
        "native_calls": [
            {"method": method, "stage": stage, "status": "returned"}
            for method, stage in (
                ("close_diagnostic_state", "observe_confirm"),
                ("close_diagnostic", "dispatch"),
                ("close_diagnostic_state", "observe_after"),
                ("close_diagnostic_state", "observe_after"),
            )
        ],
        "native_call_count": 4,
    }
    assert E._budget_ok(budget) is True
    budget[missing] = []
    if missing == "native_calls":
        budget["native_call_count"] = 0
    assert E._budget_ok(budget) is False


def _install_exit_worker(case, monkeypatch, *, mode="normal"):
    backend = case.cli.adapter.backend
    previous = backend.invoke
    _pythoncom, client = bridge._import_com()
    app = client.GetActiveObject("MGCPCB.Application.151")
    original_handle = native_process.ProcessHandle

    class Handle(original_handle):
        def close(self):
            pass

    def quit_once():
        case.events.append("Application.Quit")
        if mode == "lost_reply":
            raise RuntimeError("synthetic lost Quit reply")
        if mode in {"normal", "missing_result", "wrapped_timeout"}:
            case.startup.alive[22220] = False
            case.startup.alive[24552] = False

    def invoke(method, params, **kwargs):
        if method in {"diagnostic_exit_state", "diagnostic_exit_quit"}:
            case.requests.append(method)
            if method == "diagnostic_exit_quit" and mode == "timeout":
                case.events.append("Application.Quit")
                raise CLIError(
                    "E_TIMEOUT", "synthetic worker timeout", {"native_request_dispatched": True}
                )
            try:
                worker_params = {
                    **params,
                    "_diagnostic_exit_deadline": time.monotonic() + kwargs["timeout_seconds"],
                }
                result = bridge.dispatch(method, worker_params)
            except bridge.AdapterError as error:
                raise CLIError(error.code, error.message, error.details) from error
            if method == "diagnostic_exit_quit" and mode == "missing_result":
                return None
            if method == "diagnostic_exit_quit" and mode == "wrapped_timeout":
                raise CLIError(
                    "E_NATIVE_FAILURE",
                    "synthetic wrapped timeout",
                    {"cause": {"code": "E_TIMEOUT"}},
                )
            return result
        return previous(method, params, **kwargs)

    monkeypatch.setattr(native_process, "ProcessHandle", Handle)
    monkeypatch.setattr(app, "Quit", quit_once, raising=False)
    monkeypatch.setattr(backend, "invoke", invoke)
    monkeypatch.setattr(
        sessions,
        "_observe_owned_launchers",
        lambda owner, result, wait_seconds=0: {
            "normal_exit_complete": not case.startup.alive[24552],
            "associated_processes": [],
        },
    )


def test_public_completed_diagnostic_exits_once_without_mutating_original(
    completed_diagnostic, monkeypatch
):
    case, operation_id, original = completed_diagnostic
    _install_exit_worker(case, monkeypatch)
    store = case.cli._store_open()
    qualification_before = tuple(
        store.connection()
        .execute(
            "SELECT * FROM pcb_close_qualification_v1 WHERE copy_operation_id=?",
            (case.origin["copy_operation_id"],),
        )
        .fetchone()
    )
    token = case.exit_qualification_token
    args = [
        "session",
        "close",
        "--kind",
        "pcb",
        "--pid",
        "22220",
        "--diagnostic-operation-id",
        operation_id,
        *(["--diagnostic-exit-token", token] if token else []),
        "--quiet",
    ]
    preview = case.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    assert preview.envelope["data"]["preview"]["effects"] == ["session"]
    before_save, before_close = (
        case.events.count("Document.Save"),
        case.events.count("Document.Close(True)"),
    )
    token = preview.envelope["data"]["confirm_token"]
    applied = case.cli.run([*args, "--confirm", token])
    assert applied.envelope["ok"], applied.envelope
    assert case.events.count("Application.Quit") == 1
    assert case.events.count("Document.Save") == before_save
    assert case.events.count("Document.Close(True)") == before_close
    assert store.get(operation_id) == original
    assert (
        tuple(
            store.connection()
            .execute(
                "SELECT * FROM pcb_close_qualification_v1 WHERE copy_operation_id=?",
                (case.origin["copy_operation_id"],),
            )
            .fetchone()
        )
        == qualification_before
    )
    marker = (
        store.connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()
    )
    assert marker is None
    with pytest.raises(CLIError):
        E.reserve(store, operation_id, case.cli._scope())
    with pytest.raises(CLIError):
        E.require_ready(
            store,
            operation_id,
            case.cli.validator,
            case.cli._scope(),
            qualification_token=getattr(case, "exit_qualification_token", None),
        )
    requests, events = list(case.requests), list(case.events)
    replay = case.cli.run([*case.args, "--confirm", case.diagnostic_confirm_token])
    assert not replay.envelope["ok"], replay.envelope
    assert case.requests == requests and case.events == events
    assert (
        store.connection()
        .execute(f"SELECT 1 FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()
        is None
    )
    requests, events = list(case.requests), list(case.events)
    repeated = case.cli.run([*args, "--confirm", token])
    assert not repeated.envelope["ok"], repeated.envelope
    assert case.requests == requests and case.events == events
    assert store.get(operation_id) == original


def test_save_replacing_board_file_keeps_original_qualification_and_allows_exit(case, monkeypatch):
    _pythoncom, client = bridge._import_com()
    document = client.GetActiveObject("MGCPCB.Application.151").ActiveDocument
    original_save = document.Save
    initial_ino = case.board.stat().st_ino
    copy_id = case.origin["copy_operation_id"]
    store = case.cli._store_open()
    before_copy = tuple(
        store.connection()
        .execute("SELECT * FROM pcb_close_qualification_v1 WHERE copy_operation_id=?", (copy_id,))
        .fetchone()
    )

    def replace_on_save():
        assert original_save() is True
        temporary = case.board.with_name("atomic-save-test.pcb")
        temporary.write_bytes(b"new closed board bytes from the synthetic Save")
        os.replace(temporary, case.board)
        return True

    monkeypatch.setattr(document, "Save", replace_on_save)
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    details = result.envelope["error"]["details"]
    assert details["diagnostic_complete"] is True, result.envelope
    assert case.board.stat().st_ino != initial_ino
    case.exit_qualification_token = details["diagnostic_exit_token"]
    operation_id = details["receipt"]["operation_id"]
    after_copy = tuple(
        store.connection()
        .execute("SELECT * FROM pcb_close_qualification_v1 WHERE copy_operation_id=?", (copy_id,))
        .fetchone()
    )
    copy_columns = [
        row[1]
        for row in store.connection().execute("PRAGMA table_info(pcb_close_qualification_v1)")
    ]
    used_by_index = copy_columns.index("used_by")
    assert before_copy[used_by_index] is None
    assert after_copy[used_by_index] == operation_id
    assert all(
        after == before
        for index, (before, after) in enumerate(zip(before_copy, after_copy, strict=True))
        if index != used_by_index
    )
    _install_exit_worker(case, monkeypatch)
    args = _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    exit_preview = case.cli.run([*args, "--dry-run"])
    assert exit_preview.envelope["ok"], exit_preview.envelope
    applied = case.cli.run([*args, "--confirm", exit_preview.envelope["data"]["confirm_token"]])
    assert applied.envelope["ok"], applied.envelope
    assert (
        tuple(
            store.connection()
            .execute(
                "SELECT * FROM pcb_close_qualification_v1 WHERE copy_operation_id=?", (copy_id,)
            )
            .fetchone()
        )
        == after_copy
    )


def _exit_args(operation_id, *, pid="22220", token=None):
    return [
        "session",
        "close",
        "--kind",
        "pcb",
        "--pid",
        pid,
        "--diagnostic-operation-id",
        operation_id,
        *(["--diagnostic-exit-token", token] if token else []),
        "--quiet",
    ]


def _refused_without_cad(case, args):
    requests, events = list(case.requests), list(case.events)
    result = case.cli.run([*args, "--dry-run"])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["code"] in {"E_CONFLICT", "E_FORBIDDEN"}, result.envelope
    assert case.requests == requests and case.events == events
    return result


def test_public_complete_receipt_without_marker_cannot_be_adopted(completed_diagnostic):
    case, operation_id, original = completed_diagnostic
    store = case.cli._store_open()
    store.connection().execute(f"DELETE FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
    assert store.get(operation_id) == original
    _refused_without_cad(
        case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    )
    assert (
        store.connection()
        .execute(f"SELECT 1 FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()
        is None
    )
    assert store.get(operation_id) == original


def test_public_rejects_external_forged_scope_pid_and_candidate_before_cad(
    completed_diagnostic, monkeypatch
):
    case, operation_id, original = completed_diagnostic
    store = case.cli._store_open()
    _refused_without_cad(case, _exit_args("op-not-the-original"))
    _refused_without_cad(
        case, _exit_args(operation_id, pid="22221", token=case.exit_qualification_token)
    )
    _refused_without_cad(
        case,
        [
            "session",
            "close",
            "--kind",
            "schematic",
            "--pid",
            "22220",
            "--diagnostic-operation-id",
            operation_id,
            "--quiet",
        ],
    )
    _refused_without_cad(
        case,
        [
            *_exit_args(operation_id, token=getattr(case, "exit_qualification_token", None)),
            "--save-project",
            str(case.project),
        ],
    )
    scope_path = case.tmp_path / "scope.json"
    scope_bytes = scope_path.read_bytes()
    changed_scope = json.loads(scope_bytes)
    changed_scope["granted_by"] = "different synthetic grant with the same scope ID"
    try:
        scope_path.write_text(json.dumps(changed_scope), encoding="utf8")
        _refused_without_cad(
            case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
        )
    finally:
        scope_path.write_bytes(scope_bytes)
    with monkeypatch.context() as altered:
        real_identity = E.adapter_identity

        def changed_candidate():
            identity = copy.deepcopy(real_identity())
            identity["build_sha256"] = "a" * 64
            return identity

        altered.setattr(E, "adapter_identity", changed_candidate)
        _refused_without_cad(
            case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
        )
    row = (
        store.connection()
        .execute("SELECT receipt FROM operations WHERE operation_id=?", (operation_id,))
        .fetchone()
    )
    saved = row[0]
    forged = json.loads(saved)
    forged["extensions"]["result"]["board_lifecycle"]["close_result"] = False
    try:
        store.connection().execute(
            "UPDATE operations SET receipt=? WHERE operation_id=?",
            (json.dumps(forged), operation_id),
        )
        _refused_without_cad(
            case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
        )
    finally:
        store.connection().execute(
            "UPDATE operations SET receipt=? WHERE operation_id=?", (saved, operation_id)
        )
    assert store.get(operation_id) == original


def test_public_wrong_journal_and_reused_pid_refuse_before_cad(completed_diagnostic, monkeypatch):
    from xpedition_cli.driver import Adapter, runtime

    case, operation_id, original = completed_diagnostic
    with monkeypatch.context() as changed:
        old = driver._owned_process_identity

        def reused(domain, pid):
            result = copy.deepcopy(old(domain, pid))
            result["creation_time_100ns"] = "133000000000720001"
            return result

        changed.setattr(driver, "_owned_process_identity", reused)
        _refused_without_cad(
            case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
        )
    requests, events = list(case.requests), list(case.events)
    with runtime(
        Adapter(case.cli.adapter.backend), state_dir=case.tmp_path / "foreign-journal"
    ) as other:
        result = other.run(
            [
                *_exit_args(operation_id, token=getattr(case, "exit_qualification_token", None)),
                "--dry-run",
            ]
        )
        assert not result.envelope["ok"], result.envelope
        assert result.envelope["error"]["code"] == "E_CONFLICT", result.envelope
    assert case.requests == requests and case.events == events
    assert case.cli._store_open().get(operation_id) == original


def test_public_other_unresolved_operation_refuses_before_cad(completed_diagnostic):
    case, operation_id, original = completed_diagnostic
    store = case.cli._store_open()
    store.connection().execute(
        "INSERT INTO operations (operation_id,capability_id,command,status,"
        "effects,plan,created_at,updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "op-other-unknown",
            "pcb-placement-move",
            "pcb placement move",
            "unknown",
            '["change","persist"]',
            "{}",
            1.0,
            1.0,
        ),
    )
    result = _refused_without_cad(
        case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    )
    assert (
        result.envelope["error"]["details"]["diagnostic_exit"]["reason"]
        == "other_unresolved_operation"
    )
    assert store.get(operation_id) == original


def test_public_competing_unknown_after_preview_stops_before_cad(completed_diagnostic, monkeypatch):
    case, operation_id, original = completed_diagnostic
    _install_exit_worker(case, monkeypatch)
    args = _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    preview = case.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    store = case.cli._store_open()
    store.connection().execute(
        "INSERT INTO operations (operation_id,capability_id,command,status,"
        "effects,plan,created_at,updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "op-competing-unknown",
            "pcb-placement-move",
            "pcb placement move",
            "unknown",
            '["change","persist"]',
            "{}",
            1.0,
            1.0,
        ),
    )
    requests, events = list(case.requests), list(case.events)
    result = case.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["code"] == "E_CONFLICT", result.envelope
    assert case.requests == requests and case.events == events
    assert store.get(operation_id) == original


@pytest.mark.parametrize("change", ["progid", "nonempty", "read_timeout", "candidate"])
def test_public_drift_after_preview_never_quits_or_reobserves(
    completed_diagnostic, monkeypatch, change
):
    case, operation_id, original = completed_diagnostic
    _install_exit_worker(case, monkeypatch)
    args = _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    preview = case.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    original_invoke = case.cli.adapter.backend.invoke
    if change == "nonempty":
        case.closed = False
    if change == "candidate":
        identity = E.adapter_identity

        def changed_candidate():
            result = copy.deepcopy(identity())
            result["build_sha256"] = "b" * 64
            return result

        monkeypatch.setattr(E, "adapter_identity", changed_candidate)

    def changed(method, params, **kwargs):
        if method == "diagnostic_exit_state" and change == "read_timeout":
            case.requests.append(method)
            raise CLIError(
                "E_TIMEOUT", "synthetic exit-state timeout", {"native_request_dispatched": True}
            )
        result = original_invoke(method, params, **kwargs)
        if method == "diagnostic_exit_state" and change == "progid":
            result["automation_progid"] = "MGCPCB.Application.152"
        return result

    monkeypatch.setattr(case.cli.adapter.backend, "invoke", changed)
    result = case.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    assert "Application.Quit" not in case.events
    _refused_without_cad(case, args)
    if change == "read_timeout":
        other_board = case.tmp_path / "unrelated.pcb"
        other_board.write_bytes(b"synthetic board")
        ordinary = _refused_without_cad(
            case,
            [
                "session",
                "close",
                "--kind",
                "pcb",
                "--pid",
                "22220",
                "--save-project",
                str(other_board),
                "--quiet",
            ],
        )
        assert (
            ordinary.envelope["error"]["details"]["diagnostic_exit"]["reason"]
            == "diagnostic_exit_observation_unresolved"
        )
    assert case.cli._store_open().get(operation_id) == original


@pytest.mark.parametrize(
    "fault",
    [
        "save_false",
        "save_reply_lost",
        "close_reply_lost",
        "close_untyped",
        "worker_timeout",
        "nested_timeout",
    ],
)
def test_incomplete_native_diagnostic_never_grants_exit(case, fault):
    case.fault = fault
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    details = result.envelope["error"]["details"]
    assert details["diagnostic_complete"] is False
    operation_id = details["receipt"]["operation_id"]
    marker = (
        case.cli._store_open()
        .connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()
    )
    assert marker[0] == "reserved"
    _refused_without_cad(
        case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    )


def test_response_validation_rejection_never_seals_exit(case, monkeypatch):
    from xpedition_cli._vendor.tool_driver_sdk.validation import ContractError

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    validate = case.cli.validator.validate

    def reject(kind, value):
        details = (
            ((value.get("error") or {}).get("details") or {}) if isinstance(value, dict) else {}
        )
        if kind == "response" and details.get("diagnostic_complete") is True:
            raise ContractError("synthetic final response rejection")
        return validate(kind, value)

    monkeypatch.setattr(case.cli.validator, "validate", reject)
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", result.envelope
    operation_id = preview.envelope["data"]["preview"]["operation_id"]
    record = case.cli._store_open().get(operation_id)
    assert record["status"] == "unknown" and record["receipt"] is None
    assert record["extra"].get("rejected_receipt")
    assert (
        case.cli._store_open()
        .connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()[0]
        == "reserved"
    )
    _refused_without_cad(
        case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    )


def test_finalization_crash_window_never_seals_exit(case, monkeypatch):
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope

    def failed_finalize(*args, **kwargs):
        raise OSError("synthetic post-validation storage failure")

    monkeypatch.setattr(E, "finalize", failed_finalize)
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.envelope["error"]["details"]["diagnostic_complete"] is True, result.envelope
    operation_id = result.envelope["error"]["details"]["receipt"]["operation_id"]
    assert "qualification unavailable" in result.stderr
    assert (
        case.cli._store_open()
        .connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()[0]
        == "reserved"
    )
    _refused_without_cad(
        case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    )


def test_finalization_interrupted_after_update_rolls_back_authority(case, monkeypatch):
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    finalize = E.finalize
    checkpoints = []

    def interrupt_during_finalization(*args, **kwargs):
        original_checkpoint = D.checkpoint

        def interrupted():
            checkpoints.append("checkpoint")
            if len(checkpoints) == 2:
                raise OSError("synthetic failure after qualification update")
            original_checkpoint()

        with monkeypatch.context() as changed:
            changed.setattr(D, "checkpoint", interrupted)
            return finalize(*args, **kwargs)

    monkeypatch.setattr(E, "finalize", interrupt_during_finalization)
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.envelope["error"]["details"]["diagnostic_complete"] is True
    assert checkpoints == ["checkpoint", "checkpoint"]
    assert "qualification unavailable" in result.stderr
    operation_id = result.envelope["error"]["details"]["receipt"]["operation_id"]
    store = case.cli._store_open()
    original = copy.deepcopy(store.get(operation_id))
    marker = (
        store.connection()
        .execute(
            f"SELECT state,record_sha256,budget_sha256,copy_sha256 FROM {E.TABLE} "
            "WHERE diagnostic_id=?",
            (operation_id,),
        )
        .fetchone()
    )
    assert tuple(marker) == ("reserved", None, None, None)
    _refused_without_cad(
        case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    )
    assert store.get(operation_id) == original


def test_seal_commit_returning_after_deadline_never_grants_exit(case, monkeypatch):
    from contextlib import contextmanager

    from xpedition_cli.operation_budget import current_budget

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    finalize = E.finalize

    def commit_after_deadline(store, *args, **kwargs):
        budget = current_budget()
        original_transaction = store._transaction

        @contextmanager
        def late_transaction():
            with original_transaction() as connection:
                yield connection

            def late_clock():
                return budget.deadline + 1

            budget.clock = late_clock
            monkeypatch.setattr(time, "monotonic", late_clock)

        with monkeypatch.context() as scoped:
            scoped.setattr(store, "_transaction", late_transaction)
            return finalize(store, *args, **kwargs)

    monkeypatch.setattr(E, "finalize", commit_after_deadline)
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.envelope["error"]["details"]["diagnostic_complete"] is True
    operation_id = result.envelope["error"]["details"]["receipt"]["operation_id"]
    store = case.cli._store_open()
    assert (
        store.connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()[0]
        == "sealed"
    )
    _refused_without_cad(
        case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    )
    original = copy.deepcopy(store.get(operation_id))
    E.require_capacity(store)
    marker = (
        store.connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()
    )
    assert marker[0] == "sealed"
    with pytest.raises(CLIError):
        E.reserve(store, operation_id, case.cli._scope())
    assert store.get(operation_id) == original and original["status"] == "unknown"


def test_metadata_publication_after_diagnostic_deadline_has_no_authority(case, monkeypatch):
    from contextlib import contextmanager

    from xpedition_cli.operation_budget import current_budget

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    finalize = E.finalize

    def delayed_publication(store, *args, **kwargs):
        budget = current_budget()
        original_transaction = store._transaction
        count = 0

        @contextmanager
        def delayed_transaction():
            nonlocal count
            count += 1
            with original_transaction() as connection:
                yield connection
            if count == 2:

                def late_clock():
                    return budget.deadline + 1

                budget.clock = late_clock
                monkeypatch.setattr(time, "monotonic", late_clock)

        with monkeypatch.context() as scoped:
            scoped.setattr(store, "_transaction", delayed_transaction)
            return finalize(store, *args, **kwargs)

    monkeypatch.setattr(E, "finalize", delayed_publication)
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    operation_id = result.envelope["error"]["details"]["receipt"]["operation_id"]
    store = case.cli._store_open()
    assert (
        store.connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()[0]
        == "ready"
    )
    assert "diagnostic_exit_token" not in result.envelope["error"]["details"]
    with pytest.raises(CLIError):
        E.require_ready(store, operation_id, case.cli.validator, case.cli._scope())
    _refused_without_cad(case, _exit_args(operation_id))


def test_publication_crash_before_commit_leaves_only_sealed(case, monkeypatch):
    from contextlib import contextmanager

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    finalize = E.finalize

    def failed_publication(store, *args, **kwargs):
        original_transaction = store._transaction
        count = 0

        @contextmanager
        def interrupted_transaction():
            nonlocal count
            count += 1
            with original_transaction() as connection:
                yield connection
                if count == 2:
                    raise RuntimeError("synthetic publication interruption")

        with monkeypatch.context() as scoped:
            scoped.setattr(store, "_transaction", interrupted_transaction)
            return finalize(store, *args, **kwargs)

    monkeypatch.setattr(E, "finalize", failed_publication)
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    operation_id = result.envelope["error"]["details"]["receipt"]["operation_id"]
    store = case.cli._store_open()
    assert (
        store.connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()[0]
        == "sealed"
    )
    _refused_without_cad(
        case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    )
    E.require_capacity(store)
    assert (
        store.connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()[0]
        == "sealed"
    )


def test_committed_publication_with_lost_return_is_resolved_by_journal(case, monkeypatch):
    from contextlib import contextmanager

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    finalize = E.finalize

    def lost_publication_return(store, *args, **kwargs):
        original_transaction = store._transaction
        count = 0

        @contextmanager
        def commit_then_fail():
            nonlocal count
            count += 1
            with original_transaction() as connection:
                yield connection
            if count == 2:
                raise RuntimeError("synthetic lost publication return")

        with monkeypatch.context() as scoped:
            scoped.setattr(store, "_transaction", commit_then_fail)
            return finalize(store, *args, **kwargs)

    monkeypatch.setattr(E, "finalize", lost_publication_return)
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    operation_id = result.envelope["error"]["details"]["receipt"]["operation_id"]
    store = case.cli._store_open()
    requests, events = list(case.requests), list(case.events)
    assert (
        store.connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()[0]
        == "ready"
    )
    assert "diagnostic_exit_token" not in result.envelope["error"]["details"]
    with pytest.raises(CLIError):
        E.require_ready(store, operation_id, case.cli.validator, case.cli._scope())
    _refused_without_cad(case, _exit_args(operation_id))
    assert case.requests == requests and case.events == events


def test_two_frames_then_context_timeout_never_seals_exit(case, monkeypatch):
    from xpedition_cli.operation_budget import current_budget

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    observe = D.observe

    def expired(*args):
        result = observe(*args)
        assert result["status"] == "complete" and len(result["frames"]) == 2
        budget = current_budget()
        budget.active_stage["_deadline"] = budget.clock()
        return result

    monkeypatch.setattr(D, "observe", expired)
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    details = result.envelope["error"]["details"]
    assert details["diagnostic_complete"] is False
    operation_id = details["receipt"]["operation_id"]
    assert (
        case.cli._store_open()
        .connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()[0]
        == "reserved"
    )
    _refused_without_cad(
        case, _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    )


def test_capacity_is_checked_before_diagnostic_dispatch(case):
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    store = case.cli._store_open()
    E.ensure_table(store)
    store.connection().executemany(
        f"INSERT INTO {E.TABLE} (diagnostic_id,state,scope_sha256) VALUES (?, 'reserved', ?)",
        [(f"op-reserved-{index}", "0" * 64) for index in range(E.MAX_SLOTS)],
    )
    requests, events = list(case.requests), list(case.events)
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["code"] == "E_CONFLICT", result.envelope
    assert (
        result.envelope["error"]["details"]["diagnostic_exit"]["reason"]
        == "exit_qualification_capacity_reached"
    )
    assert case.requests == requests and case.events == events


def test_definitely_undispatched_reservation_reclaims_one_slot(case):
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    operation_id = preview.envelope["data"]["preview"]["operation_id"]
    store = case.cli._store_open()
    E.ensure_table(store)
    E.reserve(store, operation_id, case.cli._scope())
    requests, events = list(case.requests), list(case.events)
    E.require_capacity(store)
    assert (
        store.connection()
        .execute(f"SELECT 1 FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()
        is None
    )
    assert store.get(operation_id)["status"] == "planned"
    assert case.requests == requests and case.events == events


def test_sdk_superseded_replay_reservation_is_not_published(case):
    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    operation_id = preview.envelope["data"]["preview"]["operation_id"]
    store = case.cli._store_open()
    E.ensure_table(store)
    E.reserve(store, operation_id, case.cli._scope())
    extra = json.dumps(
        {
            "stage": "superseded_by_replay",
            "dispatch_state": "not_started",
            "replay_of": "op-earlier",
        }
    )
    store.connection().execute(
        "UPDATE operations SET status='failed',extra=? WHERE operation_id=?", (extra, operation_id)
    )
    requests, events = list(case.requests), list(case.events)
    E.require_capacity(store)
    assert (
        store.connection()
        .execute(f"SELECT 1 FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()
        is None
    )
    assert case.requests == requests and case.events == events


@pytest.mark.parametrize(
    "mode", ["lost_reply", "still_running", "missing_result", "timeout", "wrapped_timeout"]
)
def test_public_quit_unknown_never_retries_or_queries_cad(completed_diagnostic, monkeypatch, mode):
    case, operation_id, original = completed_diagnostic
    _install_exit_worker(case, monkeypatch, mode=mode)
    if mode == "still_running":
        monkeypatch.setattr(native_process, "EXIT_OBSERVATION_SECONDS", 0)
    args = _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    preview = case.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    token = preview.envelope["data"]["confirm_token"]
    result = case.cli.run([*args, "--confirm", token])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", result.envelope
    assert case.events.count("Application.Quit") == 1
    assert (
        "Application.ActiveDocument" not in case.events[case.events.index("Application.Quit") + 1 :]
    )
    _refused_without_cad(case, args)
    _refused_without_cad(case, ["session", "close", "--kind", "pcb", "--pid", "22220", "--quiet"])
    assert case.events.count("Application.Quit") == 1
    store = case.cli._store_open()
    assert store.get(operation_id) == original
    E.require_capacity(store)
    assert (
        store.connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()[0]
        == "claimed"
    )


def test_public_quit_return_then_stage_deadline_stays_unknown_without_more_cad(
    completed_diagnostic, monkeypatch
):
    from xpedition_cli.operation_budget import current_budget

    case, operation_id, original = completed_diagnostic
    _install_exit_worker(case, monkeypatch)
    args = _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    preview = case.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    invoke = case.cli.adapter.backend.invoke

    def expire_after_return(method, params, **kwargs):
        result = invoke(method, params, **kwargs)
        if method == "diagnostic_exit_quit":
            budget = current_budget()
            budget.active_stage["_deadline"] = budget.clock()
        return result

    monkeypatch.setattr(case.cli.adapter.backend, "invoke", expire_after_return)
    result = case.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", result.envelope
    assert case.events.count("Application.Quit") == 1
    assert (
        "Application.ActiveDocument" not in case.events[case.events.index("Application.Quit") + 1 :]
    )
    _refused_without_cad(case, args)
    _refused_without_cad(case, ["session", "close", "--kind", "pcb", "--pid", "22220", "--quiet"])
    assert case.cli._store_open().get(operation_id) == original


def test_public_late_worker_getter_stops_before_next_cad_call(completed_diagnostic, monkeypatch):
    case, operation_id, original = completed_diagnostic
    _install_exit_worker(case, monkeypatch)
    args = _exit_args(operation_id, token=getattr(case, "exit_qualification_token", None))
    preview = case.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    invoke = case.cli.adapter.backend.invoke
    clock = [0.0]
    _pythoncom, client = bridge._import_com()

    class LateClient:
        def GetActiveObject(self, progid):
            app = client.GetActiveObject(progid)
            clock[0] = 2.0
            return app

    def late_worker(method, params, **kwargs):
        if method != "diagnostic_exit_quit":
            return invoke(method, params, **kwargs)
        case.requests.append(method)
        monkeypatch.setattr(native_exit, "time", SimpleNamespace(monotonic=lambda: clock[0]))
        return native_exit.exit_empty(LateClient(), {**params, "_diagnostic_exit_deadline": 1.0})

    monkeypatch.setattr(case.cli.adapter.backend, "invoke", late_worker)
    result = case.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", result.envelope
    last_get = max(index for index, item in enumerate(case.events) if item.startswith("GetActive"))
    assert case.events[last_get:] == ["GetActiveObject:MGCPCB.Application.151"]
    assert "Application.Quit" not in case.events
    _refused_without_cad(case, args)
    assert case.cli._store_open().get(operation_id) == original


def test_nonterminating_worker_timeout_never_kills_helper(monkeypatch):
    created = []
    recorded = []

    class Worker:
        pid = 12345
        returncode = None

        def __init__(self, *args, **kwargs):
            self.killed = False
            self.request = json.loads(kwargs["stdin"].read())
            created.append(self)

        def communicate(self, timeout):
            raise subprocess.TimeoutExpired("synthetic-worker", timeout)

        def kill(self):
            self.killed = True

    monkeypatch.setattr(native_xpedition.subprocess, "Popen", Worker)
    monkeypatch.setattr(native_xpedition, "record_native_timeout", recorded.append)
    with pytest.raises(CLIError) as caught:
        native_xpedition._run_nonterminating_exit_worker(
            ["synthetic-worker"],
            input='{"method":"diagnostic_exit_quit"}',
            timeout=0.01,
            stderr=subprocess.PIPE,
        )
    assert caught.value.code == "E_TIMEOUT"
    assert caught.value.details["worker_terminated"] is False
    assert created and created[0].killed is False
    assert isinstance(created[0].request["params"]["_diagnostic_exit_deadline"], float)
    assert recorded == ["diagnostic_exit_quit"]


@pytest.mark.parametrize("fault", ["missing", "altered", "receipt", "scope", "binding", "purpose"])
def test_public_qualification_token_binding_refuses_without_cad(completed_diagnostic, fault):
    from xpedition_cli._vendor.tool_driver_sdk import tokens

    case, operation_id, original = completed_diagnostic
    token = case.exit_qualification_token
    if fault == "missing":
        token = None
    elif fault == "altered":
        body, mac = token.split(".")
        token = body + "." + ("A" if mac[0] != "A" else "B") + mac[1:]
    else:
        store = case.cli._store_open()
        claims = tokens.verify(store.secret(), token, 0)
        key = {
            "receipt": "record_sha256",
            "scope": "scope_sha256",
            "binding": "binding_sha256",
            "purpose": "purpose",
        }[fault]
        claims[key] = "wrong-binding"
        token = tokens.issue(store.secret(), claims)
    _refused_without_cad(case, _exit_args(operation_id, token=token))
    assert case.cli._store_open().get(operation_id) == original


def test_public_delayed_preview_uses_certificate_not_original_deadline(
    completed_diagnostic, monkeypatch
):
    case, operation_id, original = completed_diagnostic
    _install_exit_worker(case, monkeypatch)
    monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: monotonic() + 1000000)
    args = _exit_args(operation_id, token=case.exit_qualification_token)
    preview = case.cli.run([*args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    result = case.cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.envelope["ok"], result.envelope
    assert case.events.count("Application.Quit") == 1
    assert case.cli._store_open().get(operation_id) == original


@pytest.mark.parametrize("fault", ["sign_late", "render_late", "response_rejected"])
def test_final_certificate_response_failure_never_discloses_authority(case, monkeypatch, fault):
    from xpedition_cli._vendor.tool_driver_sdk import tokens
    from xpedition_cli._vendor.tool_driver_sdk.validation import ContractError
    from xpedition_cli.operation_budget import current_budget

    preview = case.cli.run([*case.args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    store = case.cli._store_open()
    finalize = E.finalize
    preserved = []

    def capture_original(*args, **kwargs):
        preserved.append(copy.deepcopy(store.get(args[1])))
        return finalize(*args, **kwargs)

    monkeypatch.setattr(E, "finalize", capture_original)

    def expire():
        budget = current_budget()
        budget.clock = lambda: budget.deadline + 1

    if fault == "sign_late":
        issue = tokens.issue

        def delayed_issue(secret, claims):
            value = issue(secret, claims)
            if claims.get("purpose") == "diagnostic-empty-exit-qualification-v1":
                expire()
            return value

        monkeypatch.setattr(tokens, "issue", delayed_issue)
    elif fault == "render_late":
        render = case.cli._render

        def delayed_render(envelope, flags):
            value = render(envelope, flags)
            if (envelope.get("error", {}).get("details") or {}).get("diagnostic_exit_token"):
                expire()
            return value

        monkeypatch.setattr(case.cli, "_render", delayed_render)
    else:
        validate = case.cli.validator.validate

        def rejected(kind, value):
            if kind == "response" and (value.get("error", {}).get("details") or {}).get(
                "diagnostic_exit_token"
            ):
                raise ContractError("synthetic certificate response rejection")
            return validate(kind, value)

        monkeypatch.setattr(case.cli.validator, "validate", rejected)
    result = case.cli.run([*case.args, "--confirm", preview.envelope["data"]["confirm_token"]])
    details = result.envelope["error"]["details"]
    assert details["diagnostic_complete"] is True
    assert "diagnostic_exit_token" not in details
    assert b'"diagnostic_exit_token"' not in result.stdout
    operation_id = details["receipt"]["operation_id"]
    assert store.get(operation_id) == preserved[0]
    assert (
        store.connection()
        .execute(f"SELECT state FROM {E.TABLE} WHERE diagnostic_id=?", (operation_id,))
        .fetchone()[0]
        == "ready"
    )
    if fault != "response_rejected":
        assert details["operation_budget"]["remaining_ms"] == 0
        assert details["operation_budget"]["wall_ms"] >= 1000
    _refused_without_cad(case, _exit_args(operation_id))
