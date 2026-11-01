"""Correlate active native PCB writes without rewriting SDK target identities."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager, suppress
from pathlib import Path

from . import project_file
from ._vendor.tool_driver_sdk.scope import file_path_of
from ._vendor.tool_driver_sdk.store import ACTIVE, effective_status, target_key
from .audit import config_dir
from .errors import CLIError

MAX_ACTIVE_RECORDS = 64
MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_ACTIVE_BYTES = 128 * 1024 * 1024
MAX_PROJECT_BYTES = 2 * 1024 * 1024
MAX_READ_SECONDS = 10.0
MAX_SQL_INSTRUCTIONS = 2_000_000
_CONFIRM_GATE = threading.Lock()
_TEXT_COLUMNS = (
    "operation_id",
    "plan_id",
    "capability_id",
    "command",
    "kind",
    "batch_id",
    "item_key",
    "target_key",
    "request_digest",
    "scope_id",
    "idempotency_key",
    "status",
    "effects",
    "plan",
    "receipt",
    "extra",
    "resolution",
    "item_index",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
    "plan_expires_at",
    "lease_until",
)


def requires_pcb_exclusion(command, params):
    """Cover the shared entry points that can change, release, or replace a board."""
    path = command["path"]
    return bool(
        path.startswith("pcb ")
        and set(command["driver"].get("effects") or []) & {"change", "persist"}
        or path in {"project backup", "project restore", "manufacturing export"}
        or path == "project create"
        and bool(params.get("closed-copy") or params.get("closed_copy"))
        or path in {"session start", "session attach", "session close"}
        and params.get("kind") != "schematic"
    )


@contextmanager
def exclusive_pcb_write(store):
    """Serialize cooperating PCB writes in this state store across aliases.

    The persistent lock file must not be deleted or replaced while clients run.
    Refuse immediately if another writer holds it; no CAD observation is needed.
    """
    if not _CONFIRM_GATE.acquire(blocking=False):
        _refuse("pcb_write_lock_held", live=True)
    descriptor = None
    locked = False
    try:
        try:
            # This is the same root as native-ownership.json, independent of
            # the optional SDK state_dir selected by an individual caller.
            path = config_dir() / "pcb-engineering.lock"
            try:
                prior = path.lstat()
            except FileNotFoundError:
                prior = None
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if prior is not None and (
                not stat.S_ISREG(prior.st_mode) or getattr(prior, "st_file_attributes", 0) & reparse
            ):
                raise OSError("PCB write lock is not a regular file")
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            opened, named = os.fstat(descriptor), path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or getattr(named, "st_file_attributes", 0) & reparse
                or not os.path.samestat(opened, named)
            ):
                raise OSError("PCB write lock identity changed")
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                _refuse("pcb_write_lock_held", live=True)
            locked = True
        except OSError as error:
            raise CLIError(
                "E_IO",
                "The PCB write exclusion cannot be established",
                {
                    "write_attempted": False,
                    "native_write_attempted": False,
                    "pcb_document_guard": {"reason": "pcb_write_lock_unavailable"},
                },
            ) from error
        yield
    finally:
        if descriptor is not None:
            if locked:
                with suppress(OSError):
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
            with suppress(OSError):
                os.close(descriptor)
        _CONFIRM_GATE.release()


def journal_identity(store):
    """Identify the actual SDK connection and its database file, without reopening it."""
    try:
        connection = store.connection()
        rows = connection.execute("PRAGMA database_list").fetchall()
        main = [row for row in rows if row[1] == "main"]
        if len(main) != 1 or not main[0][2]:
            raise ValueError("main_database_unavailable")
        named = store._directory() / "operations.sqlite3"
        actual = Path(main[0][2])
        info = named.lstat()
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & reparse
            or not os.path.samefile(named, actual)
            or not info.st_ino
        ):
            raise ValueError("database_file_identity_unavailable")
        return {
            "path": os.path.normcase(os.path.abspath(str(named))),
            "device": str(info.st_dev),
            "file_id": str(info.st_ino),
        }
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        raise CLIError(
            "E_IO",
            "The SDK journal identity cannot be verified",
            {
                "write_attempted": False,
                "native_write_attempted": False,
                "pcb_document_guard": {"reason": "journal_identity_unavailable"},
            },
        ) from error


def _refuse_unbound_prior_pcb():
    """Do not silently adopt an ownership root from a pre-protocol PCB session."""
    from .session_ownership import _read

    if any(entry.get("domain") == "pcb" for entry in _read().values()):
        _refuse("prior_pcb_ownership_without_journal_binding")
    path = config_dir() / "session.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & reparse
        or info.st_size > 262144
    ):
        raise ValueError("prior_session_unreadable_or_unbounded")
    with path.open("rb") as stream:
        raw = stream.read(262145)
    if len(raw) > 262144:
        raise ValueError("prior_session_unbounded")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("prior_session_invalid")
    pending = [value]
    examined = 0
    while pending:
        item = pending.pop()
        examined += 1
        if examined > 4096:
            raise ValueError("prior_session_structure_unbounded")
        if isinstance(item, dict):
            if item.get("domain") == "pcb":
                _refuse("prior_pcb_ownership_without_journal_binding")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def require_shared_journal(store, candidate, *, return_created=False):
    """Bind this ownership root to one real journal and one protocol candidate.

    Called only while holding exclusive_pcb_write. Never replace or repair an
    existing binding. A new task/candidate needs its own ownership root.
    """
    identity = journal_identity(store)
    try:
        from .native_identity import stable_diagnostic_candidate

        content = stable_diagnostic_candidate(candidate)
        digest = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    except (TypeError, ValueError) as error:
        raise CLIError(
            "E_CONFLICT",
            "The PCB candidate's code and resource identity is unavailable",
            {
                "write_attempted": False,
                "native_write_attempted": False,
                "pcb_document_guard": {"reason": "candidate_identity_unavailable"},
            },
        ) from error
    expected = {
        "protocol": "pcb-shared-journal-v1",
        "journal": identity,
        "candidate_sha256": digest,
    }
    raw = (json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path = config_dir() / "pcb-journal-binding-v1.json"
    created = False
    descriptor = None
    try:
        try:
            path.lstat()
        except FileNotFoundError:
            _refuse_unbound_prior_pcb()
        try:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
            )
            created = True
        except FileExistsError:
            pass
        if descriptor is not None:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        info = path.lstat()
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & reparse
            or info.st_size > 16384
        ):
            raise ValueError("binding_not_regular_or_bounded")
        with path.open("rb") as stream:
            observed = stream.read(16385)
        if len(observed) > 16384:
            raise ValueError("binding_not_bounded")
        if json.loads(observed) != expected:
            raise CLIError(
                "E_CONFLICT",
                "This ownership root is bound to another PCB journal or candidate",
                {
                    "write_attempted": False,
                    "native_write_attempted": False,
                    "pcb_document_guard": {"reason": "journal_or_candidate_mismatch"},
                },
            )
        if journal_identity(store) != identity:
            raise ValueError("journal_identity_changed")
        return (identity, created) if return_created else identity
    except CLIError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise CLIError(
            "E_IO",
            "The PCB ownership journal binding is unavailable or incomplete",
            {
                "write_attempted": created,
                "state_write_attempted": created,
                "native_write_attempted": False,
                "pcb_document_guard": {"reason": "journal_binding_unavailable"},
            },
        ) from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


# Product-owned, bounded eligibility only: never retain SDK plans or receipts and
# never attach triggers to SDK prune. A missing record makes only this diagnostic
# unavailable; the original SDK receipt remains the provenance authority.
MAX_COPY_QUALIFICATIONS = 64
_QUALIFICATION_TABLE = "pcb_close_qualification_v1"
_QUALIFICATION_LEGACY_SQL = """CREATE TABLE pcb_close_qualification_v1 (
    copy_operation_id TEXT PRIMARY KEY,
    board_path TEXT NOT NULL,
    board_device TEXT NOT NULL,
    board_file_id TEXT NOT NULL,
    startup_operation_id TEXT,
    used_by TEXT
)"""
# This spelling is SQLite's exact CREATE text after the two supported ALTERs.
# Existing eligibility rows are retained verbatim; NULL never grants a transition.
_QUALIFICATION_SQL = (
    _QUALIFICATION_LEGACY_SQL[:-1] + ", startup_preflight TEXT, startup_baseline TEXT)"
)


def _qualification_error(reason, *, code="E_CONFLICT"):
    return CLIError(
        code,
        "Closed diagnostic copy eligibility cannot be established",
        {
            "write_attempted": False,
            "native_write_attempted": False,
            "pcb_document_guard": {"reason": reason},
        },
    )


def _qualification_schema(connection):
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (_QUALIFICATION_TABLE,)
    ).fetchone()
    return (row[0] or "").strip().rstrip(";") if row else None


def verify_qualification_store(store):
    if _qualification_schema(store.connection()) != _QUALIFICATION_SQL.strip().rstrip(";"):
        raise _qualification_error("qualification_store_missing_or_changed")


def ensure_qualification_store(store, *, can_initialize):
    """Install/migrate exact schemas without resetting rows or adopting history."""
    with store._transaction() as connection:
        actual = _qualification_schema(connection)
        if actual == _QUALIFICATION_LEGACY_SQL.strip().rstrip(";") and not can_initialize:
            from .operation_budget import checkpoint

            count = connection.execute(
                "SELECT COUNT(*) FROM pcb_close_qualification_v1"
            ).fetchone()[0]
            if count > MAX_COPY_QUALIFICATIONS:
                raise _qualification_error("qualification_capacity_corrupt")
            checkpoint()
            connection.execute(
                "ALTER TABLE pcb_close_qualification_v1 ADD COLUMN startup_preflight TEXT"
            )
            connection.execute(
                "ALTER TABLE pcb_close_qualification_v1 ADD COLUMN startup_baseline TEXT"
            )
            checkpoint()
            actual = _qualification_schema(connection)
        if actual is None and can_initialize:
            connection.execute(_QUALIFICATION_SQL)
        elif actual is None:
            raise _qualification_error("qualification_store_missing_or_changed")
        elif can_initialize:
            if _qualification_schema(connection) != _QUALIFICATION_SQL.strip().rstrip(";"):
                raise _qualification_error("qualification_store_missing_or_changed")
            count = connection.execute(
                "SELECT COUNT(*) FROM pcb_close_qualification_v1"
            ).fetchone()[0]
            if count:
                raise _qualification_error("journal_already_bound_elsewhere")
        if _qualification_schema(connection) != _QUALIFICATION_SQL.strip().rstrip(";"):
            raise _qualification_error("qualification_store_missing_or_changed")


def _plain_board(path):
    path = Path(path)
    if not path.is_absolute() or path.suffix.lower() != ".pcb":
        raise ValueError("qualification_board_path_invalid")
    original = path.lstat()
    if not stat.S_ISREG(original.st_mode) or getattr(original, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    ):
        raise ValueError("qualification_board_identity_unavailable")
    path = path.resolve(strict=True)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or not info.st_ino
        or not os.path.samestat(original, info)
    ):
        raise ValueError("qualification_board_identity_unavailable")
    return path, info


def register_copy_qualification(store, receipt):
    """Register only a completed fresh copy, after final response validation.

    Only unreferenced already-used or no-longer-verifiable copies are evicted.
    If no slot is available, the ordinary copy remains successful; its later
    diagnostic explicitly refuses because no eligibility record exists.
    """
    verify_qualification_store(store)
    try:
        operation_id = receipt["operation_id"]
        result = receipt["extensions"]["result"]
        registration = result["closed_copy"]["registration"]["data"]
        outcome = receipt["outcome"]
        if (
            not isinstance(operation_id, str)
            or not operation_id.startswith("op-")
            or len(operation_id) > 96
            or result.get("creation_method") != "closed_backup_copy"
            or result.get("opened") is not False
            or outcome.get("execution") != "completed"
            or outcome.get("persistence") != "saved"
            or (outcome.get("verification") or {}).get("status") != "passed"
        ):
            raise ValueError("qualification_copy_receipt_invalid")
        board, info = _plain_board(registration["target_board"]["path"])
        if len(str(board)) > 4096:
            raise ValueError("qualification_board_path_unbounded")
        identity = (str(board), str(info.st_dev), str(info.st_ino))
        with store._transaction() as connection:
            if _qualification_schema(connection) != _QUALIFICATION_SQL.strip().rstrip(";"):
                raise _qualification_error("qualification_store_missing_or_changed")
            original = connection.execute(
                "SELECT command,status FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if original is None or tuple(original) != ("project create", "succeeded"):
                raise ValueError("qualification_copy_record_unavailable")
            existing = connection.execute(
                "SELECT board_path,board_device,board_file_id,startup_operation_id,used_by "
                "FROM pcb_close_qualification_v1 WHERE copy_operation_id=?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing[:3]) != identity:
                    raise ValueError("qualification_copy_identity_changed")
                return
            # Exit qualification still needs its original consumed copy row.
            # A reservation may not have learned the copy ID yet: conservatively
            # retain every copy until that reservation is sealed or released.
            # The bounded tables still enforce capacity; no tombstones accrue.
            from . import diagnostic_exit

            protected = ""
            if diagnostic_exit._schema(connection) is not None:
                diagnostic_exit._checked(connection)
                protected = (
                    f" AND NOT EXISTS (SELECT 1 FROM {diagnostic_exit.TABLE} q WHERE "
                    "q.copy_operation_id=pcb_close_qualification_v1.copy_operation_id "
                    "OR q.copy_operation_id IS NULL)"
                )
            # Eviction never grants eligibility: a used or receipt-less copy
            # cannot be re-registered without a new successful SDK dispatch.
            connection.execute(
                "DELETE FROM pcb_close_qualification_v1 WHERE (used_by IS NOT NULL OR "
                "NOT EXISTS (SELECT 1 FROM operations WHERE "
                "operations.operation_id=pcb_close_qualification_v1.copy_operation_id "
                "AND operations.command='project create' AND operations.status='succeeded'))"
                + protected
            )
            count = connection.execute(
                "SELECT COUNT(*) FROM pcb_close_qualification_v1"
            ).fetchone()[0]
            if count >= MAX_COPY_QUALIFICATIONS:
                raise _qualification_error("qualification_capacity_reached")
            connection.execute(
                "INSERT INTO pcb_close_qualification_v1 "
                "(copy_operation_id,board_path,board_device,board_file_id) VALUES (?,?,?,?)",
                (operation_id, *identity),
            )
    except CLIError:
        raise
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as error:
        raise _qualification_error("qualification_registration_failed", code="E_IO") from error


def require_copy_qualification(store, copy_id, board, startup_id, current_operation):
    verify_qualification_store(store)
    row = (
        store.connection()
        .execute(
            "SELECT board_path,board_device,board_file_id,startup_operation_id,used_by "
            "FROM pcb_close_qualification_v1 WHERE copy_operation_id=?",
            (copy_id,),
        )
        .fetchone()
    )
    if row is None:
        raise _qualification_error("copy_qualification_unavailable")
    try:
        actual, info = _plain_board(board)
        if (
            os.path.normcase(str(actual)) != os.path.normcase(row[0])
            or str(info.st_dev) != row[1]
            or str(info.st_ino) != row[2]
            or row[3] != startup_id
        ):
            raise ValueError("qualification_binding_changed")
        if row[4] is not None and row[4] != current_operation:
            raise _qualification_error("copy_qualification_already_used")
    except CLIError:
        raise
    except (TypeError, ValueError, OSError) as error:
        raise _qualification_error("copy_qualification_binding_changed") from error


def mark_dispatched_copy_use(store, record, params):
    """Persist compact use before the SDK may invoke the dispatched handler.

    A first owned session start against this board is reserved as preparation;
    every later potentially related write marks the copy used. An unclassifiable
    writer conservatively marks current copies used without blocking that writer.
    """
    verify_qualification_store(store)
    command, operation_id = record["command"], record["operation_id"]
    if (
        not isinstance(operation_id, str)
        or not operation_id.startswith("op-")
        or len(operation_id) > 96
    ):
        raise _qualification_error("qualification_operation_id_invalid")
    if command == "project create" and (params.get("closed-copy") or params.get("closed_copy")):
        return
    if command == "session close" and not (
        params.get("save-project") or params.get("save_project")
    ):
        return
    if command == "session start" and not params.get("project"):
        return
    if (
        store.connection()
        .execute("SELECT 1 FROM pcb_close_qualification_v1 WHERE used_by IS NULL LIMIT 1")
        .fetchone()
        is None
    ):
        return
    raw = params.get("project") or params.get("save-project") or params.get("save_project")
    try:
        board = _board(str(Path(str(raw)).expanduser().resolve())) if raw else None
        identity = _plain_board(board) if board is not None else None
    except (CLIError, OSError, TypeError, ValueError):
        identity = None
    with store._transaction() as connection:
        if _qualification_schema(connection) != _QUALIFICATION_SQL.strip().rstrip(";"):
            raise _qualification_error("qualification_store_missing_or_changed")
        rows = connection.execute(
            "SELECT copy_operation_id,board_path,board_device,board_file_id,"
            "startup_operation_id,used_by FROM pcb_close_qualification_v1 LIMIT ?",
            (MAX_COPY_QUALIFICATIONS + 1,),
        ).fetchall()
        if len(rows) > MAX_COPY_QUALIFICATIONS:
            raise _qualification_error("qualification_capacity_corrupt")
        for copy_id, old_path, device, file_id, startup, used_by in rows:
            if used_by is not None:
                continue
            same = identity is None
            if identity is not None:
                board_path, info = identity
                same = os.path.normcase(str(board_path)) == os.path.normcase(old_path) or (
                    str(info.st_dev),
                    str(info.st_ino),
                ) == (device, file_id)
            if not same:
                continue
            if command == "session start" and startup == operation_id:
                # Only this request's fresh-plan preflight can reserve a startup.
                # The SDK admission has now happened; don't count the same first
                # Open as a later writer. Other starts still consume the copy.
                prepared = connection.execute(
                    "SELECT startup_preflight IS NOT NULL FROM pcb_close_qualification_v1 "
                    "WHERE copy_operation_id=?",
                    (copy_id,),
                ).fetchone()
                if prepared is not None and prepared[0] == 1:
                    continue
            if command == "session start" and identity is not None and startup is None:
                connection.execute(
                    "UPDATE pcb_close_qualification_v1 SET startup_operation_id=? "
                    "WHERE copy_operation_id=? AND startup_operation_id IS NULL "
                    "AND used_by IS NULL",
                    (operation_id, copy_id),
                )
            else:
                connection.execute(
                    "UPDATE pcb_close_qualification_v1 SET used_by=? "
                    "WHERE copy_operation_id=? AND used_by IS NULL",
                    (operation_id, copy_id),
                )


def before_native_write(store, command, params, *, empty_close_admission=None):
    """Refuse related outstanding evidence before even querying native CAD.

    This read does not grant admission: the later signed-plan guard and the lock
    still apply. Unknown or damaged board relationships fail closed.
    """
    if command == "project create" and (params.get("closed-copy") or params.get("closed_copy")):
        # Closed-copy creation requires an absent/empty destination and never
        # changes the other, already-existing diagnostic board.
        return
    from .diagnostic_exit import reject_unfinished_observations

    reject_unfinished_observations(store)
    # A dispatched diagnostic-bound Quit with an unknown terminal must block
    # later PCB entry points before they can query the same native session.
    for record in read_active_records(store, command="session close"):
        target = (record.get("plan") or {}).get("target") or {}
        if (target.get("extensions") or {}).get("diagnostic_exit_proof"):
            _refuse(
                "diagnostic_exit_unresolved",
                record.get("operation_id"),
                live=effective_status(record, time.time())
                in {"running", "pending", "cancel_requested"},
            )
    records = read_active_records(
        store, command=None if command == "pcb diagnostics close" else "pcb diagnostics close"
    )
    for record in records:
        if empty_close_admission is not None:
            from .diagnostic_exit import _record_digest

            exempt = {
                row["operation_id"]: row["record_sha256"]
                for row in empty_close_admission["exempt_records"]
            }
            if record.get("operation_id") in exempt:
                if (
                    command != "session close"
                    or params.get("kind") != "pcb"
                    or params.get("save-project")
                    or params.get("save_project")
                    or record.get("command") != "pcb diagnostics close"
                    or _record_digest(record) != exempt[record["operation_id"]]
                ):
                    _refuse("empty_close_retirement_evidence_changed", record.get("operation_id"))
                continue
        old_command = record.get("command")
        if command != "pcb diagnostics close" and old_command != "pcb diagnostics close":
            continue
        file_writers = {"project backup", "project restore"}
        if not (
            str(old_command).startswith("pcb ")
            or old_command in {"session close", "manufacturing export", *file_writers}
        ):
            continue
        if old_command == "session close" and record.get("effects") == ["session"]:
            continue
        target = (record.get("plan") or {}).get("target")
        if not isinstance(target, dict):
            _refuse("active_engineering_authority_incomplete", record.get("operation_id"))
        extension = target.get("extensions") or {}
        if old_command in file_writers and target.get("mode") == "file":
            old_project = target.get("object_id")
        elif target.get("engine_id") == "xpedition-pcb" and extension.get("domain") == "pcb":
            old_project = extension.get("project")
        else:
            _refuse("active_engineering_authority_incomplete", record.get("operation_id"))
        incoming = params.get("project") or params.get("save-project") or params.get("save_project")
        if not incoming:
            _refuse("native_document_identity_incomplete", record.get("operation_id"))
        board = _board(str(Path(str(incoming)).expanduser().resolve()))
        previous = _board(old_project)
        if _same(board, previous):
            _refuse(
                "same_physical_pcb_unresolved",
                record.get("operation_id"),
                live=effective_status(record, time.time())
                in {"running", "pending", "cancel_requested"},
            )


def _refuse(reason, operation_id=None, *, live=False):
    details = {
        "conflict": "state_drift",
        "write_attempted": False,
        "native_write_attempted": False,
        "state_write_attempted": False,
        "pcb_document_guard": {"reason": reason, "read_only": True},
    }
    if live:
        details["conflict"] = "lock_held"
    if operation_id:
        details["related_operation_id"] = operation_id
        details["active_operation"] = operation_id
        details["conflict"] = "lock_held" if live else "unresolved_operation"
        details["lookup_argv"] = ["operation", "show", "--id", operation_id]
        details["next_action"] = (
            "Inspect the original operation and current native state. Do not replay or "
            "resolve it merely to continue; retain its history during explicit preservation."
        )
    raise CLIError(
        "E_CONFLICT",
        "The physical PCB cannot be safely changed or persisted while related native "
        "operation evidence remains unresolved.",
        details,
    )


def read_active_records(store, *, exclude_operation_id=None, command=None):
    """Bound bytes before the original SDK get decodes JSON; never scan finished history."""
    started, instructions = time.monotonic(), [0]

    def progress():
        instructions[0] += 1000
        return int(
            instructions[0] > MAX_SQL_INSTRUCTIONS or time.monotonic() - started > MAX_READ_SECONDS
        )

    connection = store.connection()
    connection.set_progress_handler(progress, 1000)
    savepoint = False
    try:
        connection.execute("SAVEPOINT pcb_document_guard_read")
        savepoint = True
        try:
            connection.execute("SELECT octet_length('')").fetchone()
        except sqlite3.OperationalError:
            raise CLIError(
                "E_UNSUPPORTED",
                "Byte-bounded active-operation reading requires SQLite octet_length",
                {
                    "write_attempted": False,
                    "native_write_attempted": False,
                    "state_write_attempted": False,
                    "pcb_document_guard": {
                        "reason": "active_store_octet_length_unavailable",
                        "read_only": True,
                    },
                },
            ) from None
        lengths = ",".join(
            f"COALESCE(octet_length({column}),0) AS n_{column}" for column in _TEXT_COLUMNS
        )
        marks = ",".join("?" for _ in ACTIVE)
        exclusion = " AND operation_id != ?" if exclude_operation_id is not None else ""
        arguments = (*ACTIVE,)
        if exclude_operation_id is not None:
            arguments = (*arguments, exclude_operation_id)
        command_filter = " AND command = ?" if command is not None else ""
        if command is not None:
            arguments = (*arguments, command)
        rows = connection.execute(
            "SELECT CASE WHEN typeof(operation_id)='text' "
            "AND octet_length(operation_id)<=96 THEN operation_id ELSE NULL END AS id,"
            + lengths
            + f" FROM operations WHERE status IN ({marks}) AND resolution IS NULL"
            + exclusion
            + command_filter
            + " LIMIT ?",
            (*arguments, MAX_ACTIVE_RECORDS + 1),
        ).fetchall()
        if len(rows) > MAX_ACTIVE_RECORDS:
            _refuse("active_record_count_limit")
        total = 0
        for row in rows:
            size = sum(row["n_" + column] for column in _TEXT_COLUMNS)
            total += size
            if not row["id"]:
                _refuse("active_record_header_invalid")
            if size > MAX_RECORD_BYTES or total > MAX_ACTIVE_BYTES:
                _refuse("active_record_byte_limit")
        records = []
        for row in rows:
            if time.monotonic() - started > MAX_READ_SECONDS:
                _refuse("active_record_read_budget")
            record = store.get(row["id"])
            if (
                not isinstance(record, dict)
                or record.get("status") not in ACTIVE
                or record.get("resolution") is not None
            ):
                _refuse("active_record_set_incomplete")
            records.append(record)
        if time.monotonic() - started > MAX_READ_SECONDS:
            _refuse("active_record_read_budget")
        return records
    except CLIError:
        raise
    except sqlite3.OperationalError as error:
        if progress():
            _refuse("active_record_read_budget")
        raise _safe_store_failure() from error
    except Exception as error:
        raise _safe_store_failure() from error
    finally:
        connection.set_progress_handler(None, 0)
        if savepoint:
            try:
                connection.execute("RELEASE pcb_document_guard_read")
            except sqlite3.Error as error:
                raise _safe_store_failure() from error


def _safe_store_failure():
    return CLIError(
        "E_IO",
        "The complete bounded active-operation evidence could not be read",
        {
            "write_attempted": False,
            "native_write_attempted": False,
            "state_write_attempted": False,
            "pcb_document_guard": {"reason": "active_record_read_incomplete", "read_only": True},
        },
    )


def applies(target, capabilities):
    extension = target.get("extensions") or {}
    native_document_target = (
        target.get("mode") == "session"
        and target.get("engine_id") == "xpedition-pcb"
        and extension.get("domain") == "pcb"
        and bool(extension.get("project"))
    )
    if not native_document_target:
        return False
    declared = capabilities.get(extension.get("command"))
    if not isinstance(declared, dict) or not isinstance(declared.get("effects"), list):
        _refuse("engineering_declaration_unavailable")
    return bool(set(declared["effects"]) & {"change", "persist"})


def _path(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        _refuse("native_document_identity_incomplete")
    return Path(value).resolve()


def _same(first, second):
    if os.path.normcase(str(first)) == os.path.normcase(str(second)):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        _refuse("physical_file_identity_unavailable")


def _board(project):
    path = _path(project)
    if path.suffix.lower() == ".pcb":
        if not path.is_file():
            _refuse("physical_file_identity_unavailable")
        return path
    if path.suffix.lower() != ".prj":
        _refuse("project_board_relation_unavailable")
    try:
        if path.stat().st_size > MAX_PROJECT_BYTES:
            _refuse("project_read_limit")
        with path.open("rb") as stream:
            raw = stream.read(MAX_PROJECT_BYTES + 1)
        if len(raw) > MAX_PROJECT_BYTES:
            _refuse("project_read_limit")
        designs = project_file.designs(raw.decode("utf-8-sig"))
        design = project_file.board_design(designs)
        if design is None or not design.pcb_path:
            _refuse("project_board_relation_unavailable")
        board = project_file.local_path(path.parent, design.pcb_path).resolve()
        if board.suffix.lower() != ".pcb" or not board.is_file():
            _refuse("physical_file_identity_unavailable")
        return board
    except CLIError:
        raise
    except (OSError, UnicodeError, ValueError):
        _refuse("project_board_relation_unavailable")


def _observed_board(application, instance):
    if not isinstance(application, dict) or str(application.get("pid")) != str(instance):
        _refuse("native_document_identity_incomplete")
    document = application.get("document") or {}
    if document.get("status") != "observed" or document.get("open") is not True:
        _refuse("native_document_identity_incomplete")
    board = _path(document.get("path"))
    if board.suffix.lower() != ".pcb":
        _refuse("native_document_identity_incomplete")
    return board


def _target_board(target, application):
    extension = target.get("extensions") or {}
    project = _path(extension.get("project"))
    if _path(target.get("object_id")) != project:
        _refuse("project_target_identity_inconsistent")
    try:
        locator = file_path_of(target.get("locator", ""))
    except ValueError:
        locator = None
    if locator is None or _path(locator) != project:
        _refuse("project_target_identity_inconsistent")
    board = _board(str(project))
    native = _observed_board(application, target.get("instance_id"))
    original_native = _observed_board(extension.get("application"), target.get("instance_id"))
    if not _same(board, native) or not _same(board, original_native):
        _refuse("project_native_document_inconsistent")
    return board


def check(target, application, records, validator, capabilities, *, exclude_operation_id=None):
    """Check only native engineering writes, never backup or empty-session Quit."""
    if not applies(target, capabilities):
        return
    started = time.monotonic()
    if not isinstance(records, list) or len(records) > MAX_ACTIVE_RECORDS:
        _refuse("active_record_set_incomplete")
    candidates = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("effects"), list):
            _refuse("active_record_set_incomplete")
        if record.get("operation_id") == exclude_operation_id:
            continue
        plan = record.get("plan")
        if not isinstance(plan, dict) or not isinstance(plan.get("target"), dict):
            if (
                str(record.get("command", "")).startswith("pcb ")
                or record.get("command") == "session close"
            ):
                _refuse("active_engineering_authority_incomplete")
            continue
        old_target = plan["target"]
        header = str(record.get("target_key", "")).split("|", 3)
        native_header = len(header) == 4 and header[:2] == ["session", "xpedition-pcb"]
        native_target = (
            old_target.get("mode") == "session" and old_target.get("engine_id") == "xpedition-pcb"
        )
        if not native_header and not native_target:
            continue
        if not isinstance(plan.get("effects"), list):
            _refuse("active_engineering_authority_incomplete")
        declared_effects = set(
            (capabilities.get(record.get("command")) or {}).get("effects") or []
        ) | set(
            (capabilities.get((old_target.get("extensions") or {}).get("command")) or {}).get(
                "effects"
            )
            or []
        )
        if not (set(record["effects"]) | set(plan["effects"]) | declared_effects) & {
            "change",
            "persist",
        }:
            continue
        if (
            record.get("command") == "session close"
            and not (old_target.get("extensions") or {}).get("project")
            and record["effects"] == plan["effects"] == ["session"]
        ):
            document = (
                ((plan.get("base_snapshot") or {}).get("extensions") or {}).get("facts") or {}
            ).get("application", {}).get("document") or {}
            if (
                document.get("status") == "observed"
                and document.get("open") is False
                and not document.get("path")
            ):
                continue
        candidates.append(record)
    if not candidates:
        return
    new_board = _target_board(target, application)
    for record in candidates:
        if time.monotonic() - started > MAX_READ_SECONDS:
            _refuse("active_record_verification_budget")
        plan = record["plan"]
        old_target = plan["target"]
        try:
            validator.validate("plan", plan)
            extension = old_target.get("extensions") or {}
            declared = capabilities.get(record.get("command")) or {}
            if (
                record.get("status") not in ACTIVE
                or record.get("resolution") is not None
                or record.get("operation_id") != plan.get("operation_id")
                or record.get("plan_id") != plan.get("plan_id")
                or record.get("capability_id") != plan.get("capability_id")
                or record.get("capability_id") != declared.get("capability_id")
                or record["effects"] != declared.get("effects")
                or record.get("scope_id") != plan.get("authorization_scope_id")
                or record.get("effects") != plan.get("effects")
                or record.get("command") != extension.get("command")
                or record.get("target_key") != target_key(old_target)
                or extension.get("domain") != "pcb"
            ):
                _refuse("active_engineering_authority_incomplete")
            receipt = record.get("receipt")
            if receipt is not None:
                validator.validate("receipt", receipt)
                if any(
                    receipt.get(key) != plan.get(key)
                    for key in (
                        "operation_id",
                        "plan_id",
                        "capability_id",
                        "target",
                        "authorization_scope_id",
                        "effects",
                    )
                ) or receipt.get("snapshot_before") != plan.get("base_snapshot"):
                    _refuse("active_engineering_authority_incomplete")
            facts = (plan.get("base_snapshot", {}).get("extensions") or {}).get("facts") or {}
            old_application = facts.get("application")
            if record.get("command") == "pcb diagnostics close":
                old_application = (facts.get("identity") or {}).get("application")
            old_board = _target_board(old_target, old_application)
        except CLIError:
            raise
        except Exception:
            _refuse("active_engineering_authority_incomplete")
        if _same(old_board, new_board):
            _refuse(
                "same_physical_pcb_unresolved",
                record["operation_id"],
                live=effective_status(record, time.time())
                in {"running", "pending", "cancel_requested"},
            )
    if time.monotonic() - started > MAX_READ_SECONDS:
        _refuse("active_record_verification_budget")
