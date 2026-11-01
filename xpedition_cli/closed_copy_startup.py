"""One authorized first-Open transition, limited to the project and board files.

Copy genesis stays immutable provenance. This certificate records observed byte
changes, never claims them harmless or proves native persistence/engineering.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import stat
from pathlib import Path

from . import close_diagnostic as D
from . import pcb_document_guard as G
from ._vendor.tool_driver_sdk import tokens
from .cell_refresh import digest
from .native_identity import (
    adapter_identity,
    invalidate_diagnostic_content,
    same_diagnostic_candidate,
    stable_diagnostic_candidate,
)
from .operation_budget import checkpoint, current_budget

PURPOSE = "closed-copy-first-open-baseline-v1"
MAX_PROOF_BYTES = 64 * 1024
MAX_CAPTURE_BYTES = 64 * 1024 * 1024


def refuse(reason):
    raise G._qualification_error("post_open_" + reason)


def _scope(scope):
    if not getattr(scope, "granted", False) or not isinstance(getattr(scope, "record", None), dict):
        refuse("scope_unavailable")
    return digest(scope.record)


def _encoded(value):
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if len(raw.encode("utf8")) > MAX_PROOF_BYTES:
        refuse("proof_unbounded")
    return raw


def _row(store, copy_id):
    G.verify_qualification_store(store)
    connection = store.connection()
    lengths = connection.execute(
        "SELECT COALESCE(octet_length(startup_preflight),0),"
        "COALESCE(octet_length(startup_baseline),0),octet_length(board_path),"
        "COALESCE(octet_length(startup_operation_id),0),COALESCE(octet_length(used_by),0) "
        "FROM pcb_close_qualification_v1 "
        "WHERE copy_operation_id=?",
        (copy_id,),
    ).fetchone()
    if (
        lengths is None
        or any(value > MAX_PROOF_BYTES for value in lengths[:2])
        or lengths[2] > 4096
        or any(value > 96 for value in lengths[3:])
    ):
        refuse("qualification_missing_or_unbounded")
    return connection.execute(
        "SELECT board_path,board_device,board_file_id,startup_operation_id,used_by,"
        "startup_preflight,startup_baseline FROM pcb_close_qualification_v1 "
        "WHERE copy_operation_id=?",
        (copy_id,),
    ).fetchone()


def has_preparation(store, copy_id):
    return _row(store, copy_id)[5] is not None


def _capture(project, board):
    from .drc_engine import _read_contents

    used = 0

    def reserve(size):
        nonlocal used
        used += size
        if used > MAX_CAPTURE_BYTES:
            refuse("file_capture_unbounded")

    result = {}
    for role, path in (("project", project), ("board", board)):
        checkpoint()
        result[role] = _read_contents(Path(path), reserve=reserve).record
        checkpoint()
    return result


def _raw_copy(proof):
    return {key: value for key, value in proof.items() if key != "post_open_transition"}


def _copy_receipt(store, copy_id):
    row = D._copy_record(store, copy_id)
    if not row or row["command"] != "project create" or row["status"] != "succeeded":
        refuse("copy_record_missing")
    receipt = row["receipt"]
    if isinstance(receipt, str):
        receipt = json.loads(receipt)
    return row, receipt


def prepare(store, record, params, scope, validator):
    """Reserve only a new current startup, before SDK/native dispatch, under its lock."""
    if (
        record.get("command") != "session start"
        or params.get("kind") != "pcb"
        or not params.get("project")
    ):
        return None
    G.verify_qualification_store(store)
    invalidate_diagnostic_content()
    candidate = stable_diagnostic_candidate(adapter_identity())
    G.require_shared_journal(store, candidate)
    journal = G.journal_identity(store)
    operation_id = record["operation_id"]
    current = store.get(operation_id)
    if (
        not isinstance(current, dict)
        or any(current.get(key) is not None for key in ("started_at", "receipt", "resolution"))
        or current.get("status") != "planned"
        or current.get("scope_id") != scope.scope_id
    ):
        refuse("startup_not_fresh_plan")
    rows = (
        store.connection()
        .execute(
            "SELECT copy_operation_id,board_path,board_device,board_file_id "
            "FROM pcb_close_qualification_v1 LIMIT ?",
            (G.MAX_COPY_QUALIFICATIONS + 1,),
        )
        .fetchall()
    )
    if len(rows) > G.MAX_COPY_QUALIFICATIONS:
        refuse("qualification_capacity_corrupt")
    requested = Path(params["project"]).resolve()
    requested_board, requested_info = G._plain_board(G._board(str(requested)))
    requested_identity = (str(requested_info.st_dev), str(requested_info.st_ino))
    matches = []
    receipt_bytes = 0
    for copy_id, board_path, device, inode in rows:
        checkpoint()
        # An unrelated retained row may name a deleted board. Classify using
        # its recorded physical identity, never stat somebody else's old path.
        same_board = str(requested_board) == board_path or requested_identity == (device, inode)
        original = D._copy_record(store, copy_id)
        if (
            not original
            or original["command"] != "project create"
            or original["status"] != "succeeded"
        ):
            if same_board:
                refuse("copy_record_missing")
            continue
        receipt = original["receipt"]
        if isinstance(receipt, str):
            receipt = json.loads(receipt)
        receipt_bytes += len(json.dumps(receipt).encode("utf8"))
        if receipt_bytes > D.MAX_HISTORY_BYTES:
            refuse("copy_history_unbounded")
        project = Path(receipt["target"]["object_id"]).resolve()
        if requested not in (project, Path(board_path).resolve()) and not same_board:
            continue
        matches.append((copy_id, original, receipt, str(project), board_path))
    if not matches:
        return None
    if len(matches) != 1:
        refuse("copy_ambiguous")
    copy_id, original, receipt, project, board = matches[0]
    row = _row(store, copy_id)
    if row[3] is not None or row[4] is not None or row[5] is not None or row[6] is not None:
        refuse("first_open_already_reserved_or_used")
    if original["scope_id"] != scope.scope_id:
        refuse("copy_scope_changed")
    validator.validate("receipt", receipt)
    origin = copy.deepcopy(receipt["extensions"]["result"]["closed_copy"]["registration"]["data"])
    origin["copy_operation_id"] = copy_id
    copy_proof = D.fresh_copy(
        origin,
        project,
        board,
        current_operation=operation_id,
        startup_operation=None,
        store=store,
        journal=journal,
    )
    before = _capture(project, board)
    if before["board"]["file_identity"] != {"device": row[1], "inode": row[2]}:
        refuse("copy_physical_identity_changed")
    for role in ("project", "board"):
        if before[role]["sha256"] != copy_proof["genesis"][role + "_sha256"]:
            refuse("genesis_changed_before_start")
    prepared = {
        "purpose": PURPOSE,
        "copy_id": copy_id,
        "startup_id": operation_id,
        "project": project,
        "board": board,
        "journal": journal,
        "scope_id": scope.scope_id,
        "scope_sha256": _scope(scope),
        "candidate": candidate,
        "copy_proof_sha256": digest(copy_proof),
        "copy_receipt_sha256": digest(receipt),
        "genesis_sha256": digest(copy_proof["genesis"]),
        "before": before,
    }
    raw = _encoded(prepared)
    checkpoint()
    with store._transaction() as connection:
        G.verify_qualification_store(store)
        changed = connection.execute(
            "UPDATE pcb_close_qualification_v1 SET startup_operation_id=?,startup_preflight=? "
            "WHERE copy_operation_id=? AND startup_operation_id IS NULL AND used_by IS NULL "
            "AND startup_preflight IS NULL AND startup_baseline IS NULL",
            (operation_id, raw, copy_id),
        ).rowcount
        if changed != 1:
            refuse("preparation_raced")
        checkpoint()
    checkpoint()
    if _row(store, copy_id)[5] != raw or G.journal_identity(store) != journal:
        refuse("preparation_readback_failed")
    checkpoint()
    return prepared


def release_undispatched(store, operation_id, prepared):
    """Release this preflight only after SDK admission definitely refused dispatch."""
    if not prepared or prepared.get("startup_id") != operation_id:
        refuse("release_preparation_mismatch")
    if G.journal_identity(store) != prepared["journal"]:
        refuse("release_journal_changed")
    checkpoint()
    with store._transaction() as connection:
        G.verify_qualification_store(store)
        record = connection.execute(
            "SELECT command,status,scope_id,started_at,receipt,resolution FROM operations "
            "WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if record is None or tuple(record) != (
            "session start",
            "planned",
            prepared["scope_id"],
            None,
            None,
            None,
        ):
            refuse("release_not_proven_undispatched")
        changed = connection.execute(
            "UPDATE pcb_close_qualification_v1 "
            "SET startup_operation_id=NULL,startup_preflight=NULL "
            "WHERE copy_operation_id=? AND startup_operation_id=? AND startup_preflight=? "
            "AND startup_baseline IS NULL AND used_by IS NULL "
            "AND EXISTS (SELECT 1 FROM operations WHERE operation_id=? "
            "AND command='session start' AND status='planned' AND scope_id=? "
            "AND started_at IS NULL AND receipt IS NULL AND resolution IS NULL)",
            (
                prepared["copy_id"],
                operation_id,
                _encoded(prepared),
                operation_id,
                prepared["scope_id"],
            ),
        ).rowcount
        if changed != 1:
            refuse("release_compare_and_swap_failed")
        checkpoint()
    checkpoint()
    return True


def _startup(store, startup_id, scope, validator, prepared):
    from .diagnostic_exit import _bounded_record
    from .session_ownership import original_start

    record = _bounded_record(store, startup_id)
    result = ((record or {}).get("receipt") or {}).get("extensions", {}).get("result") or {}
    actual = result.get("process_identity") or {}
    _, pointer = original_start("pcb", actual, startup_id, store.get, validator, scope.scope_id)
    document = result.get("application_document") or {}
    if (
        document.get("open") is not True
        or document.get("status") != "observed"
        or Path(document.get("path") or "").resolve() != Path(prepared["board"])
        or Path(result.get("requested_project") or "").resolve()
        not in (Path(prepared["project"]), Path(prepared["board"]))
    ):
        refuse("startup_document_unverified")
    facts = record["receipt"]["snapshot_after"]["extensions"]["facts"]
    if not same_diagnostic_candidate(
        prepared["candidate"], facts.get("adapter")
    ) or not same_diagnostic_candidate(prepared["candidate"], facts.get("worker_adapter")):
        refuse("startup_candidate_changed")
    return record, actual, pointer


def _claims(proof):
    return {"purpose": PURPOSE, "exp": 0, "proof_sha256": digest(proof)}


def finalize(adapter, store, operation_id, prepared, output, validator, scope):
    """Publish a candidate and sign only after its final COMMIT/readback is on time."""
    if (
        not prepared
        or not isinstance(output.envelope, dict)
        or output.envelope.get("ok") is not True
    ):
        return None
    budget = current_budget()
    if budget is None or budget.command != "session start" or budget.mode != "confirm":
        refuse("startup_budget_unavailable")
    report = budget.report()
    calls = report["native_calls"]
    if (
        report["dispatch_started"] is not True
        or report["failed_stage"] is not None
        or report["first_failed_stage"] is not None
        or not calls
        or any(c.get("status") != "returned" for c in calls)
        or any(c.get("method") not in {"health", "start", "identity", "open"} for c in calls)
        or any(c.get("stage") != "dispatch" for c in calls if c.get("method") in {"start", "open"})
        or sum(c.get("method") == "open" for c in calls) != 1
        or sum(c.get("method") == "start" for c in calls) != 1
        or any(s.get("status") != "completed" for s in report["stages"])
    ):
        refuse("single_open_budget_unverified")
    start_index = next(i for i, c in enumerate(calls) if c["method"] == "start")
    open_index = next(i for i, c in enumerate(calls) if c["method"] == "open")
    if start_index >= open_index or not any(
        c["method"] == "identity" for c in calls[open_index + 1 :]
    ):
        refuse("single_open_order_unverified")
    invalidate_diagnostic_content()
    if (
        prepared["startup_id"] != operation_id
        or prepared["scope_id"] != scope.scope_id
        or prepared["scope_sha256"] != _scope(scope)
        or G.journal_identity(store) != prepared["journal"]
        or not same_diagnostic_candidate(prepared["candidate"], adapter_identity())
    ):
        refuse("preparation_binding_changed")
    row = _row(store, prepared["copy_id"])
    if (
        row[3] != operation_id
        or row[4] is not None
        or row[5] != _encoded(prepared)
        or row[6] is not None
    ):
        refuse("preparation_missing_or_used")
    copied, copy_receipt = _copy_receipt(store, prepared["copy_id"])
    if (
        copied["scope_id"] != prepared["scope_id"]
        or digest(copy_receipt) != prepared["copy_receipt_sha256"]
    ):
        refuse("copy_receipt_changed_during_start")
    record, actual, pointer = _startup(store, operation_id, scope, validator, prepared)
    if (output.envelope.get("data") or {}).get("receipt") != record["receipt"]:
        refuse("startup_final_receipt_changed")
    from .native_process import process_identity, same_identity

    if not same_identity(process_identity("pcb", actual["pid"]), actual):
        refuse("startup_process_changed")
    D.verify_history(
        store,
        prepared["copy_id"],
        operation_id,
        operation_id,
        prepared["project"],
        prepared["board"],
        prepared["journal"],
    )
    after = _capture(prepared["project"], prepared["board"])
    if any(
        after[role]["file_identity"] != prepared["before"][role]["file_identity"]
        for role in ("project", "board")
    ):
        refuse("physical_identity_changed_during_open")
    proof = {
        "preflight": copy.deepcopy(prepared),
        "after": after,
        "startup_receipt_sha256": digest(record["receipt"]),
        "ownership_pointer": pointer,
        "process": actual,
        "validated_budget": report,
        "file_changes": [
            {"role": role, "before": prepared["before"][role], "after": after[role]}
            for role in ("project", "board")
            if prepared["before"][role] != after[role]
        ],
        "coverage": ["project", "board"],
        "icdb_observed": False,
        "persistence_verified": False,
        "execution_qualified": False,
    }
    raw = _encoded(proof)
    checkpoint()
    with store._transaction() as connection:
        changed = connection.execute(
            "UPDATE pcb_close_qualification_v1 SET startup_baseline=? "
            "WHERE copy_operation_id=? AND startup_operation_id=? AND used_by IS NULL "
            "AND startup_preflight=? AND startup_baseline IS NULL",
            (
                raw,
                prepared["copy_id"],
                operation_id,
                _encoded(prepared),
            ),
        ).rowcount
        if changed != 1:
            refuse("baseline_publication_failed")
        checkpoint()
    row = _row(store, prepared["copy_id"])
    if tuple(row) != (
        prepared["board"],
        after["board"]["file_identity"]["device"],
        after["board"]["file_identity"]["inode"],
        operation_id,
        None,
        _encoded(prepared),
        raw,
    ):
        refuse("baseline_readback_failed")
    checkpoint()
    token = tokens.issue(store.secret(), _claims(proof))
    checkpoint()
    return {"token": token, "proof": proof}


def verify(
    store,
    copy_proof,
    startup_id,
    scope,
    journal,
    current_operation,
    token,
    *,
    validator=None,
):
    """No mint/recovery path: a live row AND the delivered certificate are required."""
    if validator is None:
        from ._vendor.tool_driver_sdk.validation import ContractValidator
        from .driver import VENDOR

        validator = ContractValidator(VENDOR)
    row = _row(store, copy_proof["operation_id"])
    if (
        row[5] is None
        or row[6] is None
        or row[3] != startup_id
        or row[4] not in (None, current_operation)
    ):
        refuse("baseline_unavailable_or_used")
    prepared, proof = json.loads(row[5]), json.loads(row[6])
    if proof.get("preflight") != prepared:
        refuse("preflight_changed")
    try:
        claims = tokens.verify(store.secret(), token, 0)
    except tokens.TokenError:
        refuse("token_invalid")
    if claims != _claims(proof):
        refuse("token_mismatch")
    invalidate_diagnostic_content()
    if (
        prepared["copy_id"] != copy_proof["operation_id"]
        or prepared["startup_id"] != startup_id
        or prepared["copy_proof_sha256"] != digest(_raw_copy(copy_proof))
        or prepared["genesis_sha256"] != digest(copy_proof["genesis"])
        or prepared["scope_id"] != scope.scope_id
        or prepared["scope_sha256"] != _scope(scope)
        or prepared["journal"] != journal
        or G.journal_identity(store) != journal
        or not same_diagnostic_candidate(prepared["candidate"], adapter_identity())
    ):
        refuse("baseline_binding_changed")
    _, receipt = _copy_receipt(store, prepared["copy_id"])
    if digest(receipt) != prepared["copy_receipt_sha256"]:
        refuse("copy_receipt_changed")
    record, actual, pointer = _startup(store, startup_id, scope, validator, prepared)
    if (
        digest(record["receipt"]) != proof["startup_receipt_sha256"]
        or actual != proof["process"]
        or pointer != proof["ownership_pointer"]
    ):
        refuse("startup_receipt_changed")
    D.verify_history(
        store,
        prepared["copy_id"],
        current_operation,
        startup_id,
        prepared["project"],
        prepared["board"],
        journal,
    )
    if (
        row[0] != prepared["board"]
        or {"device": row[1], "inode": row[2]} != proof["after"]["board"]["file_identity"]
        or _capture(prepared["project"], prepared["board"]) != proof["after"]
    ):
        refuse("baseline_files_changed")
    checkpoint()
    return {"token": token, "proof": proof}


class _ReadOnlyStore:
    """Worker reader: never initialize/prune a journal or create/replace a secret."""

    def __init__(self, path):
        from .drc_engine import _plain_path

        self.path = _plain_path(Path(path))
        self.directory = self.path.parent
        self._connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        self._connection.row_factory = sqlite3.Row

    def _directory(self):
        return self.directory

    def connection(self):
        return self._connection

    def get(self, operation_id):
        from ._vendor.tool_driver_sdk.store import _decode
        from .pcb_document_guard import _TEXT_COLUMNS, MAX_RECORD_BYTES

        sizes = "+".join(f"COALESCE(octet_length({name}),0)" for name in _TEXT_COLUMNS)
        row = self._connection.execute(
            "SELECT " + sizes + " FROM operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            return None
        if row[0] > MAX_RECORD_BYTES:
            refuse("worker_record_unbounded")
        row = self._connection.execute(
            "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        return _decode(row) if row is not None else None

    def secret(self):
        from .drc_engine import _plain_path, _read_contents

        path = _plain_path(self.directory / "confirm.secret")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != 64:
            refuse("worker_secret_unavailable")
        raw = _read_contents(path).data
        if len(raw) != 64:
            refuse("worker_secret_changed")
        return bytes.fromhex(raw.decode("ascii"))


def worker_verify(bound):
    """Check the live row even when a caller omitted the transition certificate.

    Return False only for an authenticated legacy row without a preparation;
    the caller must still perform its original strict genesis byte checks.
    """
    from ._vendor.tool_driver_sdk.scope import ScopeResolver
    from ._vendor.tool_driver_sdk.validation import ContractValidator
    from .driver import VENDOR
    from .native_process import same_identity

    transition = bound["copy_proof"].get("post_open_transition")
    store = _ReadOnlyStore(bound["journal_identity"]["path"])
    try:
        current = bound.get("diagnostic_operation_id")
        row = _row(store, bound["copy_proof"]["operation_id"])
        if G.journal_identity(store) != bound["journal_identity"]:
            refuse("worker_journal_changed")
        if not current or row[4] != current:
            refuse("worker_diagnostic_not_consumed")
        if row[5] is None:
            if transition is not None or row[6] is not None:
                refuse("worker_unprepared_certificate")
            return False
        if not isinstance(transition, dict):
            refuse("worker_transition_missing")
        proof = transition["proof"]
        validator = ContractValidator(VENDOR)
        scope = ScopeResolver("xpedition-cli", validator=validator).resolve()
        verified = verify(
            store,
            bound["copy_proof"],
            bound["ownership_pointer"]["startup_operation_id"],
            scope,
            bound["journal_identity"],
            current,
            transition["token"],
            validator=validator,
        )
        if (
            verified != transition
            or proof["ownership_pointer"] != bound["ownership_pointer"]
            or not same_identity(proof["process"], bound["generation"]["process"])
        ):
            refuse("worker_transition_changed")
        if (
            proof["preflight"]["project"] != bound["project"]
            or proof["preflight"]["board"] != bound["board"]
        ):
            refuse("worker_paths_changed")
        checkpoint()
        return True
    finally:
        store.connection().close()
