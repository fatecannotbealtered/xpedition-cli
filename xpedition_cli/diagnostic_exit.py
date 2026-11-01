"""Bounded, same-journal authority for an owned empty-Layout exit.

The diagnostic receipt remains unknown.  This table holds only a digest of
that receipt after the SDK has validated its final response; it cannot turn an
older receipt into authority or authorize another engineering write.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re

from . import close_diagnostic as diagnostic
from . import drc_failure_observation as observation
from .cell_refresh import digest
from .errors import CLIError
from .native_identity import (
    adapter_identity,
    invalidate_diagnostic_content,
    same_diagnostic_candidate,
)

TABLE = "pcb_diagnostic_exit_v1"
MAX_SLOTS = 64
SQL = """CREATE TABLE pcb_diagnostic_exit_v1 (
    diagnostic_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    record_sha256 TEXT,
    budget_sha256 TEXT,
    scope_sha256 TEXT NOT NULL,
    copy_sha256 TEXT,
    exit_operation_id TEXT,
    seal_sha256 TEXT,
    publication_sha256 TEXT,
    copy_operation_id TEXT
)"""
ID = re.compile(r"op-[A-Za-z0-9_-]{1,96}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


def refuse(reason, *, code="E_CONFLICT"):
    raise CLIError(
        code,
        "The original close diagnostic does not authorize this empty-session exit",
        {
            "write_attempted": False,
            "native_write_attempted": False,
            "diagnostic_exit": {"reason": reason},
        },
    )


def _schema(connection):
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    return (row[0] or "").strip().rstrip(";") if row else None


def ensure_table(store):
    """Initialize an empty table only; existing diagnostics are never adopted."""
    with store._transaction() as connection:
        actual = _schema(connection)
        if actual is None:
            connection.execute(SQL)
        elif actual != SQL.strip().rstrip(";"):
            refuse("exit_qualification_schema_changed")


def _checked(connection):
    if _schema(connection) != SQL.strip().rstrip(";"):
        refuse("exit_qualification_schema_missing_or_changed")
    rows = connection.execute(f"SELECT state,COUNT(*) FROM {TABLE} GROUP BY state").fetchall()
    counts = dict(rows)
    if set(counts) - {"reserved", "sealed", "ready", "observing", "claimed"}:
        refuse("exit_qualification_state_corrupt")
    active = sum(counts.values())
    if active > MAX_SLOTS:
        refuse("exit_qualification_capacity_corrupt")
    return active


def _proven_undispatched(operation_id, row):
    if row is None or row[0] != diagnostic.COMMAND or any(value is not None for value in row[3:6]):
        return False
    if row[1] == "planned":
        return True
    if row[1] != "failed" or not isinstance(row[2], str) or len(row[2]) > 8192:
        return False
    try:
        extra = json.loads(row[2])
    except (TypeError, ValueError):
        return False
    return (
        isinstance(extra, dict)
        and extra.get("stage") == "superseded_by_replay"
        and extra.get("dispatch_state") == "not_started"
        and isinstance(extra.get("replay_of"), str)
        and bool(ID.fullmatch(extra["replay_of"]))
        and extra["replay_of"] != operation_id
    )


def _release_planned_reservations(connection):
    """Release only SDK plans that prove no native dispatch, including replay."""
    rows = connection.execute(
        f"SELECT q.diagnostic_id,o.command,o.status,o.extra,o.started_at,o.receipt,o.resolution "
        f"FROM {TABLE} q "
        "LEFT JOIN operations o ON o.operation_id=q.diagnostic_id "
        "WHERE q.state='reserved' LIMIT ?",
        (MAX_SLOTS,),
    ).fetchall()
    for operation_id, command, status, extra, started, receipt, resolution in rows:
        if _proven_undispatched(
            operation_id, (command, status, extra, started, receipt, resolution)
        ):
            connection.execute(
                f"DELETE FROM {TABLE} WHERE diagnostic_id=? AND state='reserved'",
                (operation_id,),
            )


def require_capacity(store):
    """Called before SDK diagnostic dispatch while the shared lock is held."""
    with store._transaction() as connection:
        _checked(connection)
        _release_planned_reservations(connection)
        if _checked(connection) >= MAX_SLOTS:
            refuse("exit_qualification_capacity_reached")


def reject_unfinished_observations(store):
    """An interrupted exact-generation read may still be using the COM server."""
    connection = store.connection()
    if _schema(connection) is None:
        return
    _checked(connection)
    if connection.execute(f"SELECT 1 FROM {TABLE} WHERE state='observing' LIMIT 1").fetchone():
        refuse("diagnostic_exit_observation_unresolved")


def _scope_digest(scope):
    if not getattr(scope, "granted", False) or not isinstance(getattr(scope, "record", None), dict):
        refuse("diagnostic_scope_unavailable")
    return digest(scope.record)


def reserve(store, operation_id, scope):
    if not isinstance(operation_id, str) or not ID.fullmatch(operation_id):
        refuse("diagnostic_operation_id_invalid")
    with store._transaction() as connection:
        if _checked(connection) >= MAX_SLOTS:
            refuse("exit_qualification_capacity_reached")
        operation = connection.execute(
            "SELECT command,status,scope_id,started_at,receipt,resolution "
            "FROM operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if operation is None or tuple(operation) != (
            diagnostic.COMMAND,
            "planned",
            scope.scope_id,
            None,
            None,
            None,
        ):
            refuse("diagnostic_reservation_not_a_fresh_plan")
        if connection.execute(
            f"SELECT 1 FROM {TABLE} WHERE diagnostic_id=?", (operation_id,)
        ).fetchone():
            refuse("exit_qualification_already_reserved")
        connection.execute(
            f"INSERT INTO {TABLE} (diagnostic_id,state,scope_sha256) VALUES (?, 'reserved', ?)",
            (operation_id, _scope_digest(scope)),
        )


def unreserve_undispatched(store, operation_id):
    """Remove only a reservation whose SDK dispatch definitely did not occur."""
    with store._transaction() as connection:
        _checked(connection)
        record = connection.execute(
            "SELECT command,status,extra,started_at,receipt,resolution "
            "FROM operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if not _proven_undispatched(operation_id, record):
            refuse("undispatched_diagnostic_not_proven")
        connection.execute(
            f"DELETE FROM {TABLE} WHERE diagnostic_id=? AND state='reserved'", (operation_id,)
        )


def _record_digest(record):
    stable = {
        key: record.get(key)
        for key in (
            "operation_id",
            "capability_id",
            "command",
            "status",
            "resolution",
            "scope_id",
            "effects",
            "target_key",
            "request_digest",
            "plan",
            "receipt",
            "extra",
        )
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf8")
    ).hexdigest()


def _bounded_record(store, operation_id):
    from .pcb_document_guard import _TEXT_COLUMNS, MAX_RECORD_BYTES

    lengths = "+".join(f"COALESCE(octet_length({name}),0)" for name in _TEXT_COLUMNS)
    row = (
        store.connection()
        .execute(
            "SELECT " + lengths + " FROM operations WHERE operation_id=?",
            (operation_id,),
        )
        .fetchone()
    )
    if not row or row[0] > MAX_RECORD_BYTES:
        refuse("original_diagnostic_record_unavailable_or_unbounded")
    return store.get(operation_id)


def _copy_digest(store, bound, operation_id):
    """Bind the consumed row without assuming Save retained the board file ID."""
    from .pcb_document_guard import verify_qualification_store

    verify_qualification_store(store)
    row = (
        store.connection()
        .execute(
            "SELECT copy_operation_id,board_path,board_device,board_file_id,"
            "startup_operation_id,used_by FROM pcb_close_qualification_v1 "
            "WHERE copy_operation_id=?",
            (bound["copy_proof"]["operation_id"],),
        )
        .fetchone()
    )
    if (
        not row
        or not all(isinstance(value, str) and value for value in row)
        or os.path.normcase(os.path.abspath(row[1]))
        != os.path.normcase(os.path.abspath(bound["board"]))
        or row[4] != bound["ownership_pointer"]["startup_operation_id"]
        or row[5] != operation_id
    ):
        refuse("original_copy_use_not_recorded")
    return digest(list(row))


def _budget_ok(budget):
    if not (
        isinstance(budget, dict)
        and budget.get("mode") == "confirm"
        and budget.get("dispatch_started") is True
        and budget.get("failed_stage") is None
        and budget.get("first_failed_stage") is None
        and isinstance(budget.get("stages"), list)
        and budget["stages"]
        and all(
            isinstance(row, dict) and row.get("status") == "completed" for row in budget["stages"]
        )
        and isinstance(budget.get("native_calls"), list)
        and budget["native_calls"]
        and type(budget.get("native_call_count")) is int
        and budget["native_call_count"] == len(budget["native_calls"])
        and all(
            isinstance(row, dict) and row.get("status") == "returned"
            for row in budget["native_calls"]
        )
    ):
        return False
    stages = {row.get("stage") for row in budget["stages"]}
    calls = [(row.get("method"), row.get("stage")) for row in budget["native_calls"]]
    return (
        {"observe_confirm", "dispatch", "observe_after"} <= stages
        and calls.count(("close_diagnostic", "dispatch")) == 1
        and calls.count(("close_diagnostic_state", "observe_after")) == 2
        and calls.count(("close_diagnostic_state", "observe_confirm")) >= 1
    )


def _seal_digest(operation_id, record_sha, budget_sha, scope_sha, copy_sha):
    return digest(
        {
            "version": "diagnostic-seal-v1",
            "operation_id": operation_id,
            "record_sha256": record_sha,
            "budget_sha256": budget_sha,
            "scope_sha256": scope_sha,
            "copy_sha256": copy_sha,
        }
    )


def _publication_digest(operation_id, seal_sha):
    return digest(
        {
            "version": "diagnostic-publication-v1",
            "operation_id": operation_id,
            "seal_sha256": seal_sha,
            "staged_commit_readback": "completed_within_diagnostic_budget",
        }
    )


def _original(record, validator, scope_id, candidate):
    """Validate the original accepted SDK receipt, never an external copy."""
    if not isinstance(record, dict) or (
        record.get("command") != diagnostic.COMMAND
        or record.get("capability_id") != "pcb-diagnostics-close"
        or record.get("status") != "unknown"
        or record.get("resolution") is not None
        or record.get("scope_id") != scope_id
        or not isinstance(record.get("receipt"), dict)
        or not isinstance(record.get("plan"), dict)
    ):
        refuse("original_diagnostic_record_unavailable")
    receipt, plan = record["receipt"], record["plan"]
    try:
        validator.validate("receipt", receipt)
        target = plan["target"]
        bound = target["extensions"]["close_diagnostic_binding"]
        extensions = receipt["extensions"]
        result = extensions["result"]
        readback = extensions["independent_readback"]
        life = result["board_lifecycle"]
        frames = readback["frames"]
        if (
            receipt["operation_id"] != record["operation_id"]
            or receipt["plan_id"] != plan["plan_id"]
            or receipt["authorization_scope_id"] != scope_id
            or plan["authorization_scope_id"] != scope_id
            or target.get("engine_id") != "xpedition-pcb"
            or target.get("instance_id") != str(bound["generation"]["pid"])
            or target.get("object_id") != bound["project"]
            or target["extensions"].get("domain") != "pcb"
            or target["extensions"].get("command") != diagnostic.COMMAND
            or receipt["target"] != target
            or receipt["effects"] != plan["effects"]
            or record.get("effects") != plan["effects"]
            or receipt.get("failed_stage") is not None
            or receipt["outcome"]["execution"] != "completed"
            or receipt["outcome"]["persistence"] != "unknown"
            or receipt["outcome"]["verification"]["status"] != "passed"
            or receipt["outcome"]["verification"]["scope"] != diagnostic.CONTRACT
            or extensions.get("handler_error") is not None
            or extensions.get("readback_error") is not None
            or extensions.get("independent_readback_completed") is not True
            or extensions.get("save_acknowledged") is not True
            or extensions.get("persistence_independently_reopened") is not False
            or extensions.get("diagnostic_only") is not True
            or extensions.get("candidate_native_qualified") is not False
            or bound.get("diagnostic_contract") != diagnostic.CONTRACT
            or bound.get("reopen_authorized") is not False
            or result.get("binding_sha256") != digest(bound)
            or not diagnostic.terminal_observed(result)
            or life.get("save_result") is not True
            or life.get("close_result") is not True
            or readback.get("status") != "complete"
            or readback.get("original_call_terminal_observed") is not True
            or readback.get("binding") != bound
            or readback.get("issues") != []
            or readback.get("error") is not None
            or readback.get("native_request_attempts") != 2
            or readback.get("native_request_dispatched") is not True
            or readback.get("reopen_attempted") is not False
            or readback.get("replay_allowed") is not False
            or readback.get("persistence_verified") is not False
            or readback.get("execution_qualified") is not False
            or not isinstance(frames, list)
            or len(frames) != 2
            or not same_diagnostic_candidate(bound.get("controller"), candidate)
            or result.get("persistence_verified") is not False
            or result.get("reopen_attempted") is not False
            or receipt["outcome"]["verification"]["coverage"] != "complete"
            or receipt["snapshot_after"]["extensions"]["facts"]["close_diagnostic"] != readback
        ):
            raise ValueError("diagnostic_receipt_incomplete")
        attempts = readback.get("identity_attempts")
        if (
            not isinstance(attempts, list)
            or len(attempts) != 2
            or any(
                not isinstance(item, dict)
                or item.get("error")
                or item.get("file_observation_error")
                for item in attempts
            )
        ):
            raise ValueError("diagnostic_attempt_incomplete")
        roles = {"project", "board"} | {item["role"] for item in bound["side_effects"]}
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict) or set(frame["files"]) != roles:
                raise ValueError("diagnostic_frame_incomplete")
            if (
                attempts[index].get("frame") != index + 1
                or attempts[index].get("identity") != frame["identity"]
                or attempts[index].get("process") != frame["process"]
                or frame["identity"].get("automation_progid") != bound["progid"]
            ):
                raise ValueError("diagnostic_frame_identity_mismatch")
            observation.require_identity(
                bound,
                frame["identity"],
                candidate,
                frame["process"],
                closed=True,
                candidate_match=same_diagnostic_candidate,
            )
            if any(
                not isinstance(row, dict) or row.get("read_error")
                for row in frame["files"].values()
            ):
                raise ValueError("diagnostic_frame_unreadable")
            if any(frame["files"][role].get("exists") is not True for role in ("project", "board")):
                raise ValueError("diagnostic_file_missing")
        if frames[0]["files"] != frames[1]["files"]:
            raise ValueError("diagnostic_frame_changed")
        if not diagnostic.PROGID.fullmatch(str(bound.get("progid", ""))):
            raise ValueError("diagnostic_progid_invalid")
    except CLIError:
        raise
    except Exception:
        refuse("original_diagnostic_receipt_invalid")
    return bound


def finalize(store, operation_id, output, validator, scope):
    """Validate, commit and read back a seal before publishing exit authority."""
    from .operation_budget import current_budget

    envelope = output.envelope
    error = envelope.get("error") if isinstance(envelope, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    if not (
        envelope.get("ok") is False
        and error.get("code") == "E_OUTCOME_UNKNOWN"
        and isinstance(details, dict)
        and details.get("diagnostic_complete") is True
        and _budget_ok(details.get("operation_budget"))
    ):
        return False
    record = _bounded_record(store, operation_id)
    if not isinstance(record, dict) or record.get("receipt") != details.get("receipt"):
        return False
    budget = current_budget()
    if budget is None or budget.command != diagnostic.COMMAND or budget.mode != "confirm":
        return False
    invalidate_diagnostic_content()
    bound = _original(record, validator, scope.scope_id, adapter_identity())
    copy_sha = _copy_digest(store, bound, operation_id)
    budget_sha = digest(details["operation_budget"])
    record_sha = _record_digest(record)
    scope_sha = _scope_digest(scope)
    seal_sha = _seal_digest(operation_id, record_sha, budget_sha, scope_sha, copy_sha)
    with store._transaction() as connection:
        _checked(connection)
        diagnostic.checkpoint()
        changed = connection.execute(
            f"UPDATE {TABLE} SET state='sealed',record_sha256=?,budget_sha256=?,copy_sha256=?, "
            "seal_sha256=?,publication_sha256=NULL,copy_operation_id=? "
            "WHERE diagnostic_id=? AND state='reserved' AND scope_sha256=?",
            (
                record_sha,
                budget_sha,
                copy_sha,
                seal_sha,
                bound["copy_proof"]["operation_id"],
                operation_id,
                scope_sha,
            ),
        ).rowcount
        if changed != 1:
            refuse("exit_qualification_reservation_missing")
        diagnostic.checkpoint()
    # The diagnostic deadline includes the first COMMIT and this readback.
    # A late or failed COMMIT may leave 'sealed', which is never authority.
    observed = (
        store.connection()
        .execute(
            f"SELECT state,record_sha256,budget_sha256,scope_sha256,copy_sha256, "
            f"seal_sha256,publication_sha256 FROM {TABLE} WHERE diagnostic_id=?",
            (operation_id,),
        )
        .fetchone()
    )
    if observed is None or tuple(observed) != (
        "sealed",
        record_sha,
        budget_sha,
        scope_sha,
        copy_sha,
        seal_sha,
        None,
    ):
        refuse("exit_qualification_seal_readback_failed")
    confirmed = _bounded_record(store, operation_id)
    if (
        _record_digest(confirmed) != record_sha
        or _copy_digest(store, bound, operation_id) != copy_sha
    ):
        refuse("exit_qualification_source_changed")
    diagnostic.checkpoint()
    publication_sha = _publication_digest(operation_id, seal_sha)
    # The row is only a candidate. Even a durable ready COMMIT grants nothing
    # unless its readback, signing and final response finish within this request.
    with store._transaction() as connection:
        _checked(connection)
        changed = connection.execute(
            f"UPDATE {TABLE} SET state='ready',publication_sha256=? "
            "WHERE diagnostic_id=? AND state='sealed' AND record_sha256=? AND budget_sha256=? "
            "AND scope_sha256=? AND copy_sha256=? AND seal_sha256=? "
            "AND publication_sha256 IS NULL",
            (publication_sha, operation_id, record_sha, budget_sha, scope_sha, copy_sha, seal_sha),
        ).rowcount
        if changed != 1:
            refuse("exit_qualification_publication_failed")
    observed = (
        store.connection()
        .execute(
            f"SELECT state,record_sha256,budget_sha256,scope_sha256,copy_sha256, "
            f"seal_sha256,publication_sha256,copy_operation_id FROM {TABLE} WHERE diagnostic_id=?",
            (operation_id,),
        )
        .fetchone()
    )
    if observed is None or tuple(observed) != (
        "ready",
        record_sha,
        budget_sha,
        scope_sha,
        copy_sha,
        seal_sha,
        publication_sha,
        bound["copy_proof"]["operation_id"],
    ):
        refuse("exit_qualification_publication_readback_failed")
    diagnostic.checkpoint()
    from ._vendor.tool_driver_sdk import tokens

    token = tokens.issue(
        store.secret(),
        _token_claims(
            operation_id,
            record_sha,
            budget_sha,
            scope_sha,
            copy_sha,
            seal_sha,
            publication_sha,
            bound,
        ),
    )
    diagnostic.checkpoint()
    return token


def _token_claims(
    operation_id, record_sha, budget_sha, scope_sha, copy_sha, seal_sha, publication_sha, bound
):
    # This is a qualification certificate, NOT a confirm token. Its lifetime
    # is the durable row/process generation, not the original request deadline.
    # Use the existing SDK HMAC codec with an exact domain and exp=now=0;
    # ordinary expiring dry-run/confirm authorization is still required to Quit.
    return {
        "purpose": "diagnostic-empty-exit-qualification-v1",
        "exp": 0,
        "diagnostic_id": operation_id,
        "record_sha256": record_sha,
        "budget_sha256": budget_sha,
        "scope_sha256": scope_sha,
        "copy_sha256": copy_sha,
        "seal_sha256": seal_sha,
        "publication_sha256": publication_sha,
        "binding_sha256": digest(bound),
    }


def require_ready(
    store,
    operation_id,
    validator,
    scope,
    candidate=None,
    *,
    claimed_by=None,
    qualification_token=None,
):
    if not isinstance(operation_id, str) or not ID.fullmatch(operation_id):
        refuse("diagnostic_operation_id_invalid")
    connection = store.connection()
    _checked(connection)
    row = connection.execute(
        f"SELECT state,record_sha256,budget_sha256,exit_operation_id,scope_sha256,copy_sha256, "
        "seal_sha256,publication_sha256,copy_operation_id "
        f"FROM {TABLE} "
        "WHERE diagnostic_id=?",
        (operation_id,),
    ).fetchone()
    expected_states = {"claimed"} if claimed_by is not None else {"ready"}
    if (
        not row
        or row[0] not in expected_states
        or not isinstance(row[1], str)
        or not SHA256.fullmatch(row[1])
        or not isinstance(row[2], str)
        or not SHA256.fullmatch(row[2])
        or row[3] != claimed_by
        or row[4] != _scope_digest(scope)
        or not isinstance(row[5], str)
        or not SHA256.fullmatch(row[5])
        or not isinstance(row[6], str)
        or not SHA256.fullmatch(row[6])
        or not isinstance(row[7], str)
        or not SHA256.fullmatch(row[7])
    ):
        refuse("exit_qualification_not_ready")
    if row[6] != _seal_digest(operation_id, row[1], row[2], row[4], row[5]):
        refuse("exit_qualification_seal_changed")
    if row[7] != _publication_digest(operation_id, row[6]):
        refuse("exit_qualification_publication_changed")
    record = _bounded_record(store, operation_id)
    bound = _original(record, validator, scope.scope_id, candidate or adapter_identity())
    if (
        row[1] != _record_digest(record)
        or row[5] != _copy_digest(store, bound, operation_id)
        or row[8] != bound["copy_proof"]["operation_id"]
    ):
        refuse("original_diagnostic_record_changed")
    from ._vendor.tool_driver_sdk import tokens

    try:
        claims = tokens.verify(store.secret(), qualification_token, 0)
    except tokens.TokenError:
        refuse("exit_qualification_token_invalid")
    if claims != _token_claims(operation_id, row[1], row[2], row[4], row[5], row[6], row[7], bound):
        refuse("exit_qualification_token_mismatch")
    return {
        "qualification_token": qualification_token,
        "diagnostic_id": operation_id,
        "record_sha256": row[1],
        "budget_sha256": row[2],
        "scope_sha256": row[4],
        "copy_sha256": row[5],
        "seal_sha256": row[6],
        "publication_sha256": row[7],
        "binding": copy.deepcopy(bound),
        "last_identity": copy.deepcopy(
            record["receipt"]["extensions"]["independent_readback"]["frames"][-1]["identity"]
        ),
        "last_files": copy.deepcopy(
            record["receipt"]["extensions"]["independent_readback"]["frames"][-1]["files"]
        ),
    }


def authority(
    adapter, store, operation_id, scope, pid, *, claimed_by=None, qualification_token=None
):
    """Prove same journal, candidate, owner and consumed copy without CAD."""
    from .driver import _owned_process_identity
    from .native_process import same_identity
    from .pcb_document_guard import (
        journal_identity,
        read_active_records,
        require_shared_journal,
    )

    candidate = adapter_identity()
    require_shared_journal(store, candidate)
    reject_unfinished_observations(store)
    proof = require_ready(
        store,
        operation_id,
        adapter.ownership_validator,
        scope,
        candidate,
        claimed_by=claimed_by,
        qualification_token=qualification_token,
    )
    bound = proof["binding"]
    try:
        if (
            int(pid) != bound["generation"]["pid"]
            or journal_identity(store) != bound["journal_identity"]
        ):
            raise ValueError("diagnostic_generation_or_journal_changed")
        expected = bound["generation"]["process"]
        actual = _owned_process_identity("pcb", str(pid))
        if not same_identity(actual, expected):
            raise ValueError("diagnostic_process_changed")
        owner, pointer = adapter._ownership("pcb", actual, scope_id=scope.scope_id)
        if pointer != bound["ownership_pointer"]:
            raise ValueError("diagnostic_owner_changed")
        diagnostic.owned_candidate(adapter, pointer, bound["worker"])
        for record in read_active_records(store):
            if record.get("operation_id") in {operation_id, claimed_by}:
                continue
            plan = record.get("plan") or {}
            target = plan.get("target") or {}
            extension = target.get("extensions") or {}
            if (
                target.get("engine_id") == "xpedition-schematic"
                or extension.get("domain") == "schematic"
            ):
                continue
            effects = set(record.get("effects") or [])
            command = str(record.get("command") or "")
            if (
                command.startswith("pcb ")
                or command
                in {
                    "session start",
                    "session attach",
                    "session close",
                    "project create",
                    "project backup",
                    "project restore",
                    "manufacturing export",
                }
                or effects & {"change", "persist", "session"}
            ):
                refuse("other_unresolved_operation")
    except CLIError:
        raise
    except Exception:
        refuse("diagnostic_ownership_changed")
    return proof, owner


def claim(store, operation_id, exit_operation_id, proof):
    if not isinstance(exit_operation_id, str) or not ID.fullmatch(exit_operation_id):
        refuse("exit_operation_id_invalid")
    with store._transaction() as connection:
        _checked(connection)
        changed = connection.execute(
            f"UPDATE {TABLE} SET state='claimed',exit_operation_id=? "
            "WHERE diagnostic_id=? AND state='ready' AND record_sha256=? "
            "AND budget_sha256=? AND scope_sha256=? AND copy_sha256=? "
            "AND seal_sha256=? AND publication_sha256=?",
            (
                exit_operation_id,
                operation_id,
                proof["record_sha256"],
                proof["budget_sha256"],
                proof["scope_sha256"],
                proof["copy_sha256"],
                proof["seal_sha256"],
                proof["publication_sha256"],
            ),
        ).rowcount
        if changed != 1:
            refuse("exit_qualification_already_claimed")


def release_completed(store, proof, exit_operation_id, output, validator):
    """Release a consumed slot only after the exit's final response validates."""
    from .native_process import same_identity

    envelope = output.envelope
    if not isinstance(envelope, dict) or envelope.get("ok") is not True:
        return False
    receipt = (envelope.get("data") or {}).get("receipt")
    record = _bounded_record(store, exit_operation_id)
    if (
        not isinstance(receipt, dict)
        or not isinstance(record, dict)
        or record.get("command") != "session close"
        or record.get("status") != "succeeded"
        or record.get("resolution") is not None
        or record.get("receipt") != receipt
    ):
        return False
    validator.validate("receipt", receipt)
    try:
        outcome = receipt["outcome"]
        extension = receipt["extensions"]["diagnostic_exit"]
        result = receipt["extensions"]["result"]
        life = result["lifecycle"]
        target_proof = record["plan"]["target"]["extensions"]["diagnostic_exit_proof"]
        if (
            receipt["operation_id"] != exit_operation_id
            or receipt["authorization_scope_id"] != record["scope_id"]
            or receipt["target"] != record["plan"]["target"]
            or receipt["effects"] != ["session"]
            or receipt["failed_stage"] is not None
            or target_proof != proof
            or extension.get("diagnostic_operation_id") != proof["diagnostic_id"]
            or extension.get("replay_allowed") is not False
            or extension.get("native_readback_dispatched") is not False
            or outcome.get("execution") != "completed"
            or outcome.get("persistence") != "unchanged"
            or outcome["verification"].get("status") != "passed"
            or outcome["verification"].get("scope") != "owned-diagnostic-empty-exit-v1"
            or outcome["verification"].get("coverage") != "complete"
            or result.get("contract") != "owned-diagnostic-empty-exit-v1"
            or result.get("binding_sha256") != digest(proof["binding"])
            or not same_identity(
                result["process_identity"], proof["binding"]["generation"]["process"]
            )
            or result.get("closed") is not True
            or result.get("process_absence_observed") is not True
            or (result.get("owned_launch_chain") or {}).get("normal_exit_complete") is not True
            or type(life.get("save_count")) is not int
            or life["save_count"] != 0
            or type(life.get("close_count")) is not int
            or life["close_count"] != 0
            or type(life.get("quit_count")) is not int
            or life["quit_count"] != 1
            or life.get("empty_document_observed") is not True
            or life.get("quit_dispatched") is not True
            or life.get("quit_reply_received") is not True
            or receipt["snapshot_after"]["extensions"]["facts"]["diagnostic_exit"] != result
        ):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    original = _bounded_record(store, proof["diagnostic_id"])
    if (
        not isinstance(original, dict)
        or original.get("command") != diagnostic.COMMAND
        or original.get("status") != "unknown"
        or original.get("resolution") is not None
        or _record_digest(original) != proof["record_sha256"]
        or _copy_digest(store, proof["binding"], proof["diagnostic_id"]) != proof["copy_sha256"]
    ):
        return False
    with store._transaction() as connection:
        _checked(connection)
        changed = connection.execute(
            f"DELETE FROM {TABLE} "
            "WHERE diagnostic_id=? AND state='claimed' AND exit_operation_id=? "
            "AND record_sha256=? AND budget_sha256=? AND scope_sha256=? AND copy_sha256=? "
            "AND seal_sha256=? AND publication_sha256=? "
            "AND EXISTS (SELECT 1 FROM operations WHERE operation_id=? "
            "AND command=? AND status='unknown' AND resolution IS NULL)",
            (
                proof["diagnostic_id"],
                exit_operation_id,
                proof["record_sha256"],
                proof["budget_sha256"],
                proof["scope_sha256"],
                proof["copy_sha256"],
                proof["seal_sha256"],
                proof["publication_sha256"],
                proof["diagnostic_id"],
                diagnostic.COMMAND,
            ),
        ).rowcount
        return changed == 1


def begin_observation(store, operation_id, request_id, proof):
    """Durably make interrupted/timeout observations non-retryable."""
    if not isinstance(request_id, str) or not ID.fullmatch(request_id):
        refuse("exit_request_id_invalid")
    with store._transaction() as connection:
        _checked(connection)
        changed = connection.execute(
            f"UPDATE {TABLE} SET state='observing',exit_operation_id=? "
            "WHERE diagnostic_id=? AND state='ready' AND record_sha256=? "
            "AND budget_sha256=? AND scope_sha256=? AND copy_sha256=? "
            "AND seal_sha256=? AND publication_sha256=?",
            (
                request_id,
                operation_id,
                proof["record_sha256"],
                proof["budget_sha256"],
                proof["scope_sha256"],
                proof["copy_sha256"],
                proof["seal_sha256"],
                proof["publication_sha256"],
            ),
        ).rowcount
        if changed != 1:
            refuse("exit_qualification_observation_unavailable")


def finish_observation(store, operation_id, request_id):
    """Release only a fully returned, budget-complete read observation."""
    with store._transaction() as connection:
        _checked(connection)
        changed = connection.execute(
            f"UPDATE {TABLE} SET state='ready',exit_operation_id=NULL "
            "WHERE diagnostic_id=? AND state='observing' AND exit_operation_id=?",
            (operation_id, request_id),
        ).rowcount
        if changed != 1:
            refuse("exit_qualification_observation_lost")


def _target(adapter, call, options):
    from .operation_budget import profile_for

    store = adapter.diagnostic_store()
    scope = call.extras.get("scope")
    if scope is None:
        refuse("exit_scope_unavailable")
    live_scope = adapter.ownership_scope()
    if scope.scope_id != live_scope.scope_id or _scope_digest(scope) != _scope_digest(live_scope):
        refuse("exit_scope_changed")
    scope = live_scope
    proof, _owner = authority(
        adapter,
        store,
        options.get("diagnostic_operation_id"),
        scope,
        options.get("pid"),
        qualification_token=options.get("diagnostic_exit_token"),
    )
    bound = proof["binding"]
    application = proof["last_identity"]["application"]
    target = {
        "mode": "session",
        "engine_id": "xpedition-pcb",
        "instance_id": str(bound["generation"]["pid"]),
        "object_id": "pcb",
        "locator": f"xpedition://pcb/{bound['generation']['pid']}",
        "extensions": {
            "domain": "pcb",
            "command": "session close",
            "project": None,
            "application": copy.deepcopy(application),
            "worker_adapter": copy.deepcopy(bound["worker"]),
            "process_identity": copy.deepcopy(bound["generation"]["process"]),
            "ownership_pointer": copy.deepcopy(bound["ownership_pointer"]),
            "input_paths": [],
            "operation_budget_profile": profile_for("session close"),
            "source_operation_id": call.operation_id,
            "diagnostic_exit_proof": proof,
        },
    }
    missing = scope.missing(
        effects=["session"], target=target, paths=[(bound["project"], "observe")]
    )
    if missing:
        from ._vendor.tool_driver_sdk.scope import forbidden

        raise forbidden(scope, missing)
    return target


def before(adapter, target, *, stage):
    """Observe exactly the original generation, with a crash-safe read claim."""
    from .driver import _native_binding, _observe_only
    from .operation_budget import operation_stage

    extension = target["extensions"]
    promised = extension["diagnostic_exit_proof"]
    operation_id = promised["diagnostic_id"]
    request_id = extension["source_operation_id"]
    scope = adapter.ownership_scope()
    store = adapter.diagnostic_store()
    invalidate_diagnostic_content()
    current, _owner = authority(
        adapter,
        store,
        operation_id,
        scope,
        target["instance_id"],
        qualification_token=promised.get("qualification_token"),
    )
    if current != promised:
        refuse("diagnostic_exit_proof_changed")
    missing = scope.missing(
        effects=["session"], target=target, paths=[(current["binding"]["project"], "observe")]
    )
    if missing:
        from ._vendor.tool_driver_sdk.scope import forbidden

        raise forbidden(scope, missing)
    begin_observation(store, operation_id, request_id, current)
    try:
        with _observe_only(), _native_binding(target), operation_stage(stage):
            bound = current["binding"]
            identity = adapter.backend.invoke(
                "diagnostic_exit_state",
                {
                    "pid": bound["generation"]["pid"],
                    "progid": bound["progid"],
                    "observe_only": True,
                },
                timeout_seconds=diagnostic.STATE_SECONDS,
            )
            if identity.get("automation_progid") != bound["progid"]:
                refuse("diagnostic_progid_changed")
            invalidate_diagnostic_content()
            observation.require_identity(
                bound,
                identity,
                adapter_identity(),
                identity["process"],
                closed=True,
                candidate_match=same_diagnostic_candidate,
            )
            captured = observation._capture(bound)
            if captured["files"] != current["last_files"]:
                refuse("closed_files_changed_after_diagnostic")
            snapshot = diagnostic._snapshot(
                {
                    "identity": identity,
                    "process": identity["process"],
                    "diagnostic_id": operation_id,
                    "record_sha256": current["record_sha256"],
                    "budget_sha256": current["budget_sha256"],
                    "files": captured,
                }
            )
    except CLIError:
        raise
    except Exception:
        refuse("empty_identity_observation_failed")
    finish_observation(store, operation_id, request_id)
    return snapshot


def plan(adapter, call, options):
    from .operation_budget import operation_stage

    with operation_stage("setup"):
        target = _target(adapter, call, options)
    before_snapshot = before(adapter, target, stage="observe_plan")
    return {
        "target": target,
        "base_snapshot": before_snapshot,
        "level": "plan",
        "changes": [
            {
                "action": "quit_owned_empty_layout_once",
                "resource": target["locator"],
                "summary": "Quit only the diagnostic-owned empty Layout; no Save or Close",
            }
        ],
        "outputs": [],
        "unknowns": ["Original diagnostic persistence remains unknown"],
        "constraints": [
            "No Save, document Close, Open, Go, replay, GUI answer or forced termination"
        ],
        "trial_snapshot": None,
        "can_apply": True,
        "extensions": {"diagnostic_operation_id": options["diagnostic_operation_id"]},
    }


def apply(adapter, call, plan):
    """Use only the claimed proof and the one-shot exact-generation worker."""
    from .cli.sessions import _observe_owned_launchers
    from .driver import DriverError, _native_binding, _unobserved_receipt
    from .native_process import EXIT_OBSERVATION_SECONDS, same_identity
    from .operation_budget import current_budget, operation_stage
    from .session import clear_native, record_native_closing

    target = plan["target"]
    promised = target["extensions"]["diagnostic_exit_proof"]
    store = adapter.diagnostic_store()
    scope = call.extras.get("scope")
    if scope is None:
        refuse("exit_scope_unavailable")
    live_scope = adapter.ownership_scope()
    if scope.scope_id != live_scope.scope_id or _scope_digest(scope) != _scope_digest(live_scope):
        refuse("exit_scope_changed")
    scope = live_scope
    invalidate_diagnostic_content()
    current, owner = authority(
        adapter,
        store,
        promised["diagnostic_id"],
        scope,
        target["instance_id"],
        claimed_by=call.operation_id,
        qualification_token=promised.get("qualification_token"),
    )
    if (
        current != promised
        or observation._capture(current["binding"])["files"] != current["last_files"]
    ):
        refuse("diagnostic_exit_proof_changed")
    bound = current["binding"]
    invalidate_diagnostic_content()
    if not same_diagnostic_candidate(bound["controller"], adapter_identity()):
        refuse("diagnostic_candidate_changed_before_exit")
    missing = scope.missing(
        effects=["session"], target=target, paths=[(bound["project"], "observe")]
    )
    if missing:
        from ._vendor.tool_driver_sdk.scope import forbidden

        raise forbidden(scope, missing)
    actual = bound["generation"]["process"]
    record_native_closing("pcb", actual, operation_id=call.operation_id, ownership_record=owner)
    if _scope_digest(adapter.ownership_scope()) != promised["scope_sha256"]:
        refuse("exit_scope_changed_before_quit")
    result, failure = None, None
    try:
        with _native_binding(target), operation_stage("dispatch"):
            current_budget().dispatched = True
            result = adapter.backend.invoke(
                "diagnostic_exit_quit",
                {"binding": bound, "exit_wait_seconds": EXIT_OBSERVATION_SECONDS},
                timeout_seconds=diagnostic.WORKER_SECONDS,
            )
        life = result.get("lifecycle") or {}
        if not (
            result.get("contract") == "owned-diagnostic-empty-exit-v1"
            and result.get("binding_sha256") == digest(bound)
            and same_identity(result.get("process_identity") or {}, actual)
            and result.get("closed") is True
            and result.get("process_absence_observed") is True
            and life.get("save_count") == life.get("close_count") == 0
            and type(life.get("quit_count")) is int
            and life["quit_count"] == 1
            and life.get("empty_document_observed") is True
            and life.get("quit_dispatched") is True
            and life.get("quit_reply_received") is True
        ):
            raise ValueError("empty_exit_terminal_unverified")
        elapsed = float((result.get("exit_observation") or {}).get("elapsed_seconds", 0.0))
        chain = _observe_owned_launchers(
            owner, result, wait_seconds=max(0.0, EXIT_OBSERVATION_SECONDS - elapsed)
        )
        if chain.get("normal_exit_complete") is not True:
            raise ValueError("owned_launcher_exit_unverified")
        result["owned_launch_chain"] = chain
        clear_native(closed_observation=result)
    except Exception as error:
        failure = error
    if failure is not None:
        cause = (
            failure
            if isinstance(failure, CLIError)
            else CLIError(
                "E_OUTCOME_UNKNOWN",
                "The diagnostic-owned exit did not complete",
                {"cause_type": type(failure).__name__},
            )
        )
        receipt = _unobserved_receipt(call, result, cause, None)
        receipt["outcome"].update(execution="unknown", change="unknown", persistence="unchanged")
        receipt["failed_stage"] = "apply"
        receipt["extensions"]["diagnostic_exit"] = {
            "replay_allowed": False,
            "diagnostic_operation_id": promised["diagnostic_id"],
            "native_readback_dispatched": False,
        }
        raise DriverError(
            "E_OUTCOME_UNKNOWN",
            "The diagnostic-owned exit is unresolved; do not retry or query CAD",
            receipt=receipt,
            details={"replay_allowed": False, "cause_code": cause.code},
            dispatch_state="dispatched",
        ) from failure
    snapshot = diagnostic._snapshot({"diagnostic_exit": result})
    receipt = _unobserved_receipt(call, result, None, None)
    receipt["snapshot_after"] = snapshot
    receipt["outcome"].update(execution="completed", change="not_applied", persistence="unchanged")
    receipt["outcome"]["verification"].update(
        status="passed",
        scope="owned-diagnostic-empty-exit-v1",
        coverage="complete",
        snapshot_id=snapshot["id"],
        evidence_refs=[snapshot["id"]],
        checks=[
            {
                "id": "owned-process-and-launcher-exit",
                "status": "passed",
                "required": True,
                "reason": "The original owned process and its proven launchers exited normally",
                "evidence_ref": snapshot["id"],
            }
        ],
        not_checked=[],
        reason="The original owned process and its proven launchers exited normally",
    )
    receipt["failed_stage"] = None
    receipt["extensions"]["diagnostic_exit"] = {
        "replay_allowed": False,
        "diagnostic_operation_id": promised["diagnostic_id"],
        "native_readback_dispatched": False,
    }
    return receipt
