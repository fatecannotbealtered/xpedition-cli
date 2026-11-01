"""Request-local, read-only retirement evidence for an ordinary empty PCB Quit.

This does not resolve old writes or authorize Save, Close, Open or a replay.
It only lets the existing owned-empty-session checks be reached after a proven
older diagnostic generation exited. No qualification slot is resurrected.
"""

from __future__ import annotations

import math
import time

from . import close_diagnostic as D
from . import diagnostic_exit as E
from . import pcb_document_guard as G
from .cell_refresh import digest
from .errors import CLIError
from .native_identity import (
    adapter_identity,
    invalidate_diagnostic_content,
    same_diagnostic_candidate,
)
from .native_process import owned_startup_launchers, same_identity
from .operation_budget import checkpoint

PROTOCOL = "retired-diagnostic-empty-close-admission-v1"


def refuse(reason):
    raise CLIError(
        "E_CONFLICT",
        "Original PCB evidence does not permit empty-session close admission",
        {
            "write_attempted": False,
            "native_write_attempted": False,
            "state_write_attempted": False,
            "empty_close_admission": {"reason": reason, "read_only": True},
        },
    )


def _record(store, operation_id):
    checkpoint()
    value = E._bounded_record(store, operation_id)
    checkpoint()
    return value


def _finished_exits(store):
    """Bound the finished-history census before decoding any JSON."""
    checkpoint()
    connection = store.connection()
    sizes = "+".join(f"COALESCE(octet_length({name}),0)" for name in G._TEXT_COLUMNS)
    started, instructions = time.monotonic(), [0]

    def progress():
        instructions[0] += 1000
        return int(
            instructions[0] > G.MAX_SQL_INSTRUCTIONS
            or time.monotonic() - started > G.MAX_READ_SECONDS
        )

    # Text only narrows the census, never grants authority. The complete original
    # JSON/receipt/certificate chain below remains mandatory for every match.
    # Oversized plans are selected for refusal without scanning their contents.
    connection.set_progress_handler(progress, 1000)
    try:
        rows = connection.execute(
            "SELECT CASE WHEN typeof(operation_id)='text' AND octet_length(operation_id)<=96 "
            "THEN operation_id ELSE NULL END," + sizes + " FROM operations "
            "WHERE command='session close' AND status='succeeded' AND resolution IS NULL "
            "AND CASE WHEN COALESCE(octet_length(plan),0)>? THEN 1 "
            "ELSE instr(plan,?)>0 END LIMIT ?",
            (G.MAX_RECORD_BYTES, '"diagnostic_exit_proof"', G.MAX_ACTIVE_RECORDS + 1),
        ).fetchall()
    finally:
        connection.set_progress_handler(None, 0)
    if len(rows) > G.MAX_ACTIVE_RECORDS or sum(row[1] for row in rows) > G.MAX_ACTIVE_BYTES:
        refuse("exit_history_unbounded")
    if any(not E.ID.fullmatch(str(row[0])) or row[1] > G.MAX_RECORD_BYTES for row in rows):
        refuse("exit_record_unbounded")
    return [_record(store, row[0]) for row in rows]


def _launcher_exit(observation, expected):
    event = observation.get("exit_observation") or {}
    actual = event.get("observed_process_identity") or {}
    return (
        observation.get("process_absence_observed") is True
        and same_identity(observation.get("process_identity") or {}, expected)
        and event.get("source")
        == "pinned Windows HANDLE/GetProcessId/GetProcessTimes/WaitForSingleObject"
        and event.get("target_pid") == str(expected["pid"])
        and event.get("original_generation_ended") is True
        and event.get("pid_reused") is False
        and type(event.get("wait_result")) is int
        and event["wait_result"] == 0
        and actual.get("pid") == expected["pid"]
        and actual.get("creation_time_100ns") == expected["creation_time_100ns"]
        and type(event.get("observations")) is int
        and event["observations"] > 0
    )


def _retired(store, original, exits, scope, validator, candidate, journal):
    bound = E._original(original, validator, scope.scope_id, candidate)
    if bound.get("journal_identity") != journal:
        refuse("retired_journal_changed")
    original_sha = E._record_digest(original)
    matches = []
    for record in exits:
        target = (record.get("plan") or {}).get("target") or {}
        proof = (target.get("extensions") or {}).get("diagnostic_exit_proof")
        if isinstance(proof, dict) and proof.get("diagnostic_id") == original["operation_id"]:
            matches.append((record, proof))
    if len(matches) != 1:
        refuse("retired_exit_missing_or_ambiguous")
    record, proof = matches[0]
    plan, receipt = record["plan"], record["receipt"]
    validator.validate("plan", plan)
    validator.validate("receipt", receipt)
    result = receipt["extensions"]["result"]
    life, event = result["lifecycle"], result["exit_observation"]
    outcome, extension = receipt["outcome"], receipt["extensions"]["diagnostic_exit"]
    process = bound["generation"]["process"]
    if (
        record.get("scope_id") != scope.scope_id
        or record.get("effects") != ["session"]
        or record.get("started_at") is None
        or record.get("resolution") is not None
        or record.get("status") != "succeeded"
        or record.get("plan_id") != plan.get("plan_id")
        or proof.get("binding") != bound
        or proof.get("record_sha256") != original_sha
        or proof.get("scope_sha256") != E._scope_digest(scope)
        or proof.get("seal_sha256")
        != E._seal_digest(
            original["operation_id"],
            original_sha,
            proof["budget_sha256"],
            proof["scope_sha256"],
            proof["copy_sha256"],
        )
        or proof.get("publication_sha256")
        != E._publication_digest(original["operation_id"], proof["seal_sha256"])
        or any(
            receipt.get(key) != plan.get(key)
            for key in (
                "operation_id",
                "plan_id",
                "capability_id",
                "authorization_scope_id",
                "target",
                "effects",
            )
        )
        or receipt.get("operation_id") != record["operation_id"]
        or receipt.get("snapshot_before") != plan.get("base_snapshot")
        or receipt.get("failed_stage") is not None
        or receipt["extensions"].get("handler_error") is not None
        or outcome.get("execution") != "completed"
        or outcome.get("persistence") != "unchanged"
        or outcome["verification"].get("status") != "passed"
        or outcome["verification"].get("coverage") != "complete"
        or outcome["verification"].get("scope") != "owned-diagnostic-empty-exit-v1"
        or extension.get("diagnostic_operation_id") != original["operation_id"]
        or extension.get("replay_allowed") is not False
        or extension.get("native_readback_dispatched") is not False
        or result.get("contract") != "owned-diagnostic-empty-exit-v1"
        or result.get("binding_sha256") != digest(bound)
        or not same_identity(result.get("process_identity") or {}, process)
        or result.get("closed") is not True
        or result.get("process_absence_observed") is not True
        or result.get("closing_pending") is not False
        or any(
            type(life.get(key)) is not int or life[key] != count
            for key, count in (("save_count", 0), ("close_count", 0), ("quit_count", 1))
        )
        or any(
            life.get(key) is not True
            for key in ("empty_document_observed", "quit_dispatched", "quit_reply_received")
        )
        or event.get("source") != "original pinned Windows process handle/GetProcessTimes"
        or type(event.get("observations")) is not int
        or event["observations"] < 1
        or event.get("com_unregistration_is_exit_evidence") is not False
        or receipt["snapshot_after"]["extensions"]["facts"]["diagnostic_exit"] != result
    ):
        refuse("retired_exit_binding_incomplete")
    from ._vendor.tool_driver_sdk import tokens
    from .drc_engine import _plain_path, _read_contents

    secret_path = _plain_path(store._directory() / "confirm.secret")
    if secret_path.stat().st_size != 64:
        refuse("existing_exit_secret_unavailable")
    secret_bytes = _read_contents(secret_path).data
    if len(secret_bytes) != 64:
        refuse("existing_exit_secret_changed")
    claims = tokens.verify(
        bytes.fromhex(secret_bytes.decode("ascii")), proof.get("qualification_token"), 0
    )
    if claims != E._token_claims(
        original["operation_id"],
        original_sha,
        proof["budget_sha256"],
        proof["scope_sha256"],
        proof["copy_sha256"],
        proof["seal_sha256"],
        proof["publication_sha256"],
        bound,
    ):
        refuse("retired_exit_certificate_mismatch")
    from .session_ownership import original_start

    owner, pointer = original_start(
        "pcb",
        process,
        bound["ownership_pointer"]["startup_operation_id"],
        lambda op: _record(store, op),
        validator,
        scope.scope_id,
    )
    if pointer != bound["ownership_pointer"]:
        refuse("retired_startup_changed")
    D.owned_candidate(_ReaderAdapter(store), pointer, bound["worker"])
    launchers = owned_startup_launchers(owner.get("startup_provenance"), process)
    chain = result.get("owned_launch_chain") or {}
    associated = chain.get("associated_processes")
    if (
        chain.get("normal_exit_complete") is not True
        or chain.get("all_owned_launchers_absent") is not True
        or not isinstance(associated, list)
        or len(associated) != len(launchers)
        or any(
            not same_identity(row.get("process_identity") or {}, expected)
            or not _launcher_exit(row.get("observation") or {}, expected)
            for row, expected in zip(associated, launchers, strict=True)
        )
    ):
        refuse("retired_launcher_exit_incomplete")
    return {
        "operation_id": original["operation_id"],
        "record_sha256": original_sha,
        "exit_operation_id": record["operation_id"],
        "exit_record_sha256": E._record_digest(record),
        "process": process,
    }


class _ReaderAdapter:
    def __init__(self, store):
        self.ownership_record_get = lambda op: _record(store, op)


def _never_dispatched(record, validator):
    if not (
        record.get("status") == "planned"
        and record.get("started_at") is None
        and record.get("receipt") is None
        and record.get("resolution") is None
    ):
        return False
    plan = record.get("plan")
    validator.validate("plan", plan)
    if (
        record.get("operation_id") != plan.get("operation_id")
        or record.get("plan_id") != plan.get("plan_id")
        or record.get("capability_id") != plan.get("capability_id")
        or record.get("scope_id") != plan.get("authorization_scope_id")
        or record.get("effects") != plan.get("effects")
        or record.get("target_key") != G.target_key(plan["target"])
        or (plan["target"].get("extensions") or {}).get("command") != record.get("command")
    ):
        refuse("undispatched_plan_binding_incomplete")
    return True


def _current_failure(record, process, pointer, validator, worker):
    """Accept only the returned component Save/Close file-policy failure shape.

    The native error is a parsed reply, not a successful high-level handler.
    Nothing here asserts persistence or repairs the SDK unknown receipt.
    """
    from . import board_component_connect as C
    from .native_component_transaction import SOURCE
    from .operation_budget import profile_for

    plan, receipt = record.get("plan"), record.get("receipt")
    validator.validate("plan", plan)
    validator.validate("receipt", receipt)
    target = plan["target"]
    extension = target["extensions"]
    evidence = receipt["extensions"]
    handler = evidence["handler_error"]
    result = handler["details"]["native_result"]
    trace, stages = result["trace"], result["stages"]
    facts = plan["base_snapshot"]["extensions"]["facts"]
    identity = trace["identity"]
    generation = identity["generation"]
    actual = {
        "pid": generation["pid"],
        "executable": generation["executable"],
        "creation_time_100ns": generation["birth_filetime_100ns"],
    }
    if (
        record.get("command") != "pcb routing trace"
        or record.get("status") != "unknown"
        or record.get("resolution") is not None
        or record.get("started_at") is None
        or record.get("scope_id") != plan.get("authorization_scope_id")
        or record.get("operation_id") != plan.get("operation_id")
        or record.get("plan_id") != plan.get("plan_id")
        or record.get("capability_id") != plan.get("capability_id")
        or record.get("effects") != plan.get("effects")
        or record.get("target_key") != G.target_key(target)
        or any(
            receipt.get(key) != plan.get(key)
            for key in (
                "operation_id",
                "plan_id",
                "capability_id",
                "authorization_scope_id",
                "target",
                "effects",
            )
        )
        or receipt.get("snapshot_before") != plan.get("base_snapshot")
        or target.get("mode") != "session"
        or target.get("engine_id") != "xpedition-pcb"
        or target.get("instance_id") != str(process["pid"])
        or extension.get("command") != "pcb routing trace"
        or extension.get("domain") != "pcb"
        or not isinstance(extension.get("component_connect_request"), dict)
        or not extension["component_connect_request"]
        or not same_diagnostic_candidate(facts.get("adapter"), adapter_identity())
        or not same_diagnostic_candidate(
            facts.get("worker_adapter"), extension.get("worker_adapter")
        )
        or not same_diagnostic_candidate(facts.get("worker_adapter"), worker)
        or identity != facts["component_state"]["identity"]
        or result.get("before_transaction_identity") != identity
        or generation.get("alive") is not True
        or not same_identity(actual, process)
        or trace.get("guard") != C.GUARD
        or trace.get("fingerprint") != C._fingerprint(trace)
        or result.get("state_guard") != C.GUARD
        or result.get("state_digest") != trace["fingerprint"]
        or result.get("transaction_source") != SOURCE
        or result.get("component_transaction_entered") is not True
        or result.get("component_transaction_dispatched") is not True
        or result.get("transaction_state") != "ended"
        or result.get("transaction_state_unknown") is not False
        or result.get("transaction_drc_acknowledged") is not True
        or handler.get("code") != "E_VERIFY_INCOMPLETE"
        or handler["details"].get("write_attempted") is not True
        or evidence.get("result") != result
        or evidence.get("independent_readback_completed") is not False
        or evidence.get("readback_error") != {"type": "CLIError", "code": "E_NOT_FOUND"}
        or receipt["outcome"].get("execution") != "failed"
        or receipt["outcome"].get("change") != "partial"
        or receipt["outcome"].get("persistence") != "unknown"
        or result.get("write_attempted") is not True
        or result.get("saved") is not True
        or result.get("applied") is not True
        or result.get("persistence_verified") is not False
        or result.get("change_status") != "partial"
        or result.get("persistence_state") != "unknown"
        or result.get("closed_proxy_release") != C.CLOSED_PROXY_RELEASE
        or result.get("closed_dependency_changes", {}).get("valid") is not False
        or result.get("post_write_error")
        != {
            "code": "E_VERIFY_INCOMPLETE",
            "cause": "ValueError",
            "details": {},
            "message": "Closed tree contains unreadable or undeclared protected changes",
            "stage": "closed_snapshot",
        }
    ):
        refuse("current_component_failure_binding_incomplete")
    expected_stages = {
        "preflight": "complete",
        "readback": "complete",
        "protection": "passed",
        "save": "acknowledged",
        "close": "confirmed",
        "closed_snapshot": "started",
        "recovery_archive": "not_started",
        "reopen": "not_started",
        "input": "complete",
        "net_identity": "complete",
        "transaction_start": "acknowledged",
        "put_trace": "returned",
        "trace_identity": "complete",
        "transaction_end": "acknowledged",
    }
    calls = [
        "Net.UniqueId",
        "TransactionStart",
        "PutTrace",
        "Trace.UniqueId",
        "TransactionEnd",
        "Save",
        "Close",
    ]
    events = result["native_calls_returned"]
    if (
        stages != expected_stages
        or not isinstance(events, list)
        or [event.get("call") for event in events] != calls
        or any(
            event.get("attempted") is not True or event.get("returned") is not True
            for event in events
        )
        or any(
            events[index].get("reply", {}).get("type") != "bool"
            or events[index]["reply"].get("value") is not True
            for index in (1, 4, 5, 6)
        )
        or events[6].get("active_document_none") is not True
        or result.get("counters") != {name: {"attempted": 1, "returned": 1} for name in calls[:5]}
        or any(
            type(result["counters"][name][field]) is not int
            for name in calls[:5]
            for field in ("attempted", "returned")
        )
        or result.get("native_trace_returned") is not True
        or result.get("uid_known") is not True
        or str(events[0]["reply"].get("value")) != result.get("net_uid")
        or str(events[3]["reply"].get("value")) != result.get("trace_uid")
    ):
        refuse("current_component_terminal_chain_incomplete")
    budget = evidence["operation_budget"]
    profile = profile_for("pcb routing trace")
    call_shape = [
        ("identity", "observe_confirm", 60),
        ("board_info", "observe_confirm", 1200),
        ("identity", "setup", 60),
        ("hand_route", "dispatch", 1200),
        ("hand_route", "dispatch", 3600),
        ("identity", "observe_after", 60),
        ("board_info", "observe_after", 1200),
    ]
    native = budget["native_calls"]

    def finite(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    if (
        budget.get("profile") != profile
        or budget.get("profile_id") != profile["id"]
        or budget.get("mode") != "confirm"
        or budget.get("dispatch_started") is not True
        or budget.get("failed_stage") != "observe_after"
        or budget.get("first_failed_stage") != "dispatch"
        or type(budget.get("native_call_count")) is not int
        or budget["native_call_count"] != 7
        or not finite(budget.get("wall_ms"))
        or not finite(budget.get("remaining_ms"))
        or budget["remaining_ms"] <= 0
        or budget["wall_ms"] >= profile["total_seconds"]["confirm"] * 1000
        or not isinstance(native, list)
        or len(native) != 7
        or any(
            (row.get("method"), row.get("stage"), row.get("limit_seconds")) != shape
            or row.get("status") != "returned"
            or not finite(row.get("wall_ms"))
            or row["wall_ms"] >= shape[2] * 1000
            for row, shape in zip(native, call_shape, strict=True)
        )
    ):
        refuse("current_native_request_terminal_unproven")
    expected_stage_rows = [
        ("observe_confirm", "completed"),
        ("dispatch", "failed"),
        ("observe_after", "failed"),
    ]
    stage_rows = budget.get("stages")
    if (
        not isinstance(stage_rows, list)
        or len(stage_rows) != 3
        or any(
            (row.get("stage"), row.get("status")) != shape
            or row.get("limit_seconds") != profile["stage_seconds"][shape[0]]
            or not finite(row.get("wall_ms"))
            or row["wall_ms"] >= profile["stage_seconds"][shape[0]] * 1000
            for row, shape in zip(stage_rows, expected_stage_rows, strict=True)
        )
    ):
        refuse("current_request_stage_terminal_unproven")
    return {
        "operation_id": record["operation_id"],
        "record_sha256": E._record_digest(record),
        "startup_operation_id": pointer["startup_operation_id"],
    }


def _prepare(adapter, store, params, scope, validator, *, exclude_operation_id=None):
    """Build an ephemeral proof; no native request or persistent write is made."""
    if params.get("kind") != "pcb" or any(
        params.get(key)
        for key in (
            "save-project",
            "save_project",
            "diagnostic-operation-id",
            "diagnostic_operation_id",
            "diagnostic-exit-token",
            "diagnostic_exit_token",
            "project",
        )
    ):
        return None
    records = G.read_active_records(store, exclude_operation_id=exclude_operation_id)
    originals = [
        record
        for record in records
        if record.get("command") == D.COMMAND and not _never_dispatched(record, validator)
    ]
    if not originals:
        return None
    try:
        checkpoint()
        live_scope = adapter.ownership_scope()
        if live_scope.scope_id != scope.scope_id or E._scope_digest(live_scope) != E._scope_digest(
            scope
        ):
            refuse("current_scope_changed")
        invalidate_diagnostic_content()
        candidate = adapter_identity()
        G.require_shared_journal(store, candidate)
        journal = G.journal_identity(store)
        E.reject_unfinished_observations(store)
        from .driver import _owned_process_identity

        process = _owned_process_identity("pcb", str(params.get("pid") or ""))
        owner, pointer = adapter._ownership("pcb", process, scope_id=scope.scope_id)
        if not isinstance(pointer, dict):
            refuse("current_original_start_missing")
        start = _record(store, pointer["startup_operation_id"])
        facts = start["receipt"]["snapshot_after"]["extensions"]["facts"]
        worker = facts["worker_adapter"]
        D.owned_candidate(_ReaderAdapter(store), pointer, worker)
        if not same_diagnostic_candidate(candidate, facts.get("adapter")):
            refuse("current_candidate_changed")
        exits = _finished_exits(store)
        retired = [
            _retired(store, row, exits, scope, validator, candidate, journal) for row in originals
        ]
        if any(same_identity(row["process"], process) for row in retired):
            refuse("retired_generation_not_current_owner")
        failures = []
        for row in records:
            if row in originals:
                continue
            if _never_dispatched(row, validator):
                continue
            if row.get("scope_id") != scope.scope_id:
                refuse("current_failure_scope_changed")
            failures.append(_current_failure(row, process, pointer, validator, worker))
            if len(failures) > 1:
                refuse("multiple_current_native_failures")
        checkpoint()
        return {
            "protocol": PROTOCOL,
            "current_process": process,
            "ownership_pointer": pointer,
            "startup_record_sha256": E._record_digest(start),
            "candidate": candidate,
            "worker": worker,
            "scope_id": scope.scope_id,
            "scope_sha256": E._scope_digest(scope),
            "journal_identity": journal,
            "exempt_records": retired,
            "current_failures": failures,
        }
    except CLIError:
        raise
    except Exception:
        refuse("admission_evidence_incomplete")


def prepare(adapter, store, params, scope, validator, *, exclude_operation_id=None):
    try:
        return _prepare(
            adapter, store, params, scope, validator, exclude_operation_id=exclude_operation_id
        )
    except CLIError:
        raise
    except Exception:
        refuse("admission_evidence_incomplete")


def _revalidate(adapter, store, params, scope, validator, proof, *, exclude_operation_id=None):
    if exclude_operation_id is not None:
        # The public driver supplies its own operation, never a caller-selected
        # exclusion. Validate any existing row before excluding it from census.
        exists = (
            store.connection()
            .execute("SELECT 1 FROM operations WHERE operation_id=?", (exclude_operation_id,))
            .fetchone()
        )
        if exists:
            row = _record(store, exclude_operation_id)
            plan = row.get("plan") or {}
            target = plan.get("target") or {}
            extension = target.get("extensions") or {}
            validator.validate("plan", plan)
            if (
                row.get("command") != "session close"
                or row.get("status") not in {"planned", "dispatched"}
                or (row.get("status") == "planned" and row.get("started_at") is not None)
                or (
                    row.get("status") == "dispatched"
                    and (
                        type(row.get("started_at")) not in (int, float)
                        or type(row.get("lease_until")) not in (int, float)
                        or not math.isfinite(row["started_at"])
                        or not math.isfinite(row["lease_until"])
                        or row["lease_until"] <= store.clock()
                    )
                )
                or row.get("operation_id") != exclude_operation_id
                or plan.get("operation_id") != exclude_operation_id
                or row.get("plan_id") != plan.get("plan_id")
                or row.get("capability_id") != plan.get("capability_id")
                or row.get("capability_id") != "session-close"
                or row.get("target_key") != G.target_key(target)
                or extension.get("source_operation_id") != exclude_operation_id
                or row.get("receipt") is not None
                or row.get("resolution") is not None
                or row.get("scope_id") != scope.scope_id
                or plan.get("authorization_scope_id") != scope.scope_id
                or row.get("effects") != ["session"]
                or plan.get("effects") != ["session"]
                or target.get("engine_id") != "xpedition-pcb"
                or target.get("instance_id") != str(proof["current_process"]["pid"])
                or extension.get("command") != "session close"
                or extension.get("domain") != "pcb"
                or extension.get("save_project")
                or extension.get("empty_close_admission") != proof
                or not same_identity(
                    extension.get("process_identity") or {}, proof["current_process"]
                )
            ):
                refuse("current_close_exclusion_mismatch")
    current = prepare(
        adapter, store, params, scope, validator, exclude_operation_id=exclude_operation_id
    )
    if proof is None or current != proof:
        refuse("admission_evidence_changed")
    return current


def revalidate(adapter, store, params, scope, validator, proof, *, exclude_operation_id=None):
    try:
        return _revalidate(
            adapter,
            store,
            params,
            scope,
            validator,
            proof,
            exclude_operation_id=exclude_operation_id,
        )
    except CLIError:
        raise
    except Exception:
        refuse("admission_revalidation_incomplete")
