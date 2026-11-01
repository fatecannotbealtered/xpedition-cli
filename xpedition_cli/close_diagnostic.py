"""One-shot closed-board diagnostic. No DRC engine, reopen or recovery path.

The SDK owns confirmation and the immutable journal. This module deliberately
does not use generic CAD snapshots or manufacture a DRC/Go terminal marker.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path

from . import drc_failure_observation as files
from .cell_refresh import digest
from .errors import CLIError
from .native_identity import (
    adapter_identity,
    invalidate_diagnostic_content,
    same_diagnostic_candidate,
    stable_diagnostic_candidate,
)
from .operation_budget import checkpoint, operation_stage

COMMAND = "pcb diagnostics close"
CONTRACT = "owned-isolated-close-diagnostic-v1"
WORKER_SECONDS = 180  # Existing close request cap; no old budget is changed.
STATE_SECONDS = 60
MAX_HISTORY = 128
MAX_HISTORY_BYTES = 8 * 1024 * 1024
MAX_HISTORY_PROJECT_BYTES = 8 * 1024 * 1024
MAX_HISTORY_PATHS = 128
PROGID = re.compile(r"MGCPCB\.(?:Application|ExpeditionPCBApplication)\.[0-9]{1,5}\Z")


def refuse(message):
    raise CLIError("E_CONFLICT", message, {"write_attempted": False})


def _snapshot(facts):
    stable = copy.deepcopy(facts)
    identity = stable.get("identity")
    if isinstance(identity, dict) and "adapter" in identity:
        identity["adapter"] = stable_diagnostic_candidate(identity["adapter"])
    revision = digest(stable)
    return {
        "id": "snapshot-" + revision[:24],
        "source": "live",
        "revision": revision,
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "dependencies": [],
        "extensions": {"facts": facts},
    }


def _record(path):
    return files.drc._read_contents(Path(path)).record


def origin_file(path):
    def reserve(size):
        if size > 1024 * 1024 + 1:
            raise ValueError("Isolation registration exceeds 1 MiB")

    contents = files.drc._read_contents(Path(path), reserve=reserve)
    return json.loads(contents.data), contents.record


def _related_history(plan, command, project, board, budget):
    """Correlate bounded historical targets by physical PCB, not locator spelling."""
    from .pcb_document_guard import _board, _same

    if not isinstance(plan, dict) or not isinstance(plan.get("target"), dict):
        raise ValueError("Unclassified operation after closed-copy creation")
    target = plan["target"]
    extension = target.get("extensions") or {}
    if not isinstance(extension, dict):
        raise ValueError("Unclassified operation target")
    declared_command = extension.get("command")
    if declared_command is not None and declared_command != command:
        raise ValueError("Historical operation command disagrees with its target")
    if (
        command in {"session start", "session attach", "session close"}
        and extension.get("domain") == "schematic"
    ):
        return False
    relevant = str(command).startswith("pcb ") or command in {
        "session start",
        "session attach",
        "session close",
        "manufacturing export",
        "project backup",
        "project restore",
        "project create",
    }
    effects = plan.get("effects")
    if not isinstance(effects, list):
        if relevant or target.get("engine_id") == "xpedition-pcb":
            raise ValueError("Historical operation effects are unavailable")
        return False
    paths = [extension.get("project"), target.get("object_id")]
    if isinstance(extension.get("input_paths"), list):
        paths.extend(extension["input_paths"])
    candidates, seen = [], set()
    for index, name in enumerate(paths):
        if index % 64 == 0:
            checkpoint()
        if not isinstance(name, str) or not name:
            continue
        path = Path(name)
        if path.suffix.lower() in {".prj", ".pcb"}:
            if not path.is_absolute():
                raise ValueError("Historical board path is not absolute")
            if path not in seen:
                seen.add(path)
                candidates.append(path)
                budget["paths"] += 1
                if budget["paths"] > MAX_HISTORY_PATHS:
                    raise ValueError("Historical board path count exceeded")
    if not relevant:
        relevant = bool(set(effects) & {"change", "persist"}) and (
            target.get("engine_id") == "xpedition-pcb" or bool(candidates)
        )
    if not relevant:
        return False
    if not candidates:
        # Empty-session Quit has no engineering write. Anything else affecting
        # PCB or a project cannot be classified as unrelated without evidence.
        if command == "session close" and plan.get("effects") == ["session"]:
            return False
        raise ValueError("Historical PCB relationship is unavailable")
    for candidate in candidates:
        checkpoint()
        before = None
        if candidate.suffix.lower() == ".prj":
            before = candidate.lstat()
            if not stat.S_ISREG(before.st_mode) or getattr(
                before, "st_file_attributes", 0
            ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError("Historical project is not a regular file")
            budget["project_bytes"] += before.st_size
            if budget["project_bytes"] > MAX_HISTORY_PROJECT_BYTES:
                raise ValueError("Historical project read budget exceeded")
        related_board = _board(str(candidate))
        if before is not None:
            after = candidate.lstat()
            if (
                not os.path.samestat(before, after)
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise ValueError("Historical project changed during classification")
        checkpoint()
        if _same(related_board, Path(board)):
            return True
    return False


def _copy_record(store, operation_id):
    """Read only the copy evidence from the SDK connection, after a byte bound."""
    connection = store.connection()
    connection.execute("SAVEPOINT close_diagnostic_copy")
    try:
        sizes = connection.execute(
            "SELECT COALESCE(octet_length(operation_id),0), "
            "COALESCE(octet_length(command),0), COALESCE(octet_length(status),0), "
            "COALESCE(octet_length(scope_id),0), COALESCE(octet_length(receipt),0) "
            "FROM operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if sizes is None:
            return None
        if sum(sizes) > MAX_HISTORY_BYTES:
            raise ValueError("Closed-copy receipt read budget exceeded")
        row = connection.execute(
            "SELECT operation_id,command,status,scope_id,receipt FROM operations "
            "WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        connection.execute("RELEASE close_diagnostic_copy")


def fresh_copy(
    origin,
    project,
    board,
    *,
    current_operation,
    startup_operation,
    store,
    journal,
    scope=None,
    post_open_token=None,
):
    """Authenticate genesis against the same SDK store and bounded physical history."""
    from .component_copy_origin import verify_origin
    from .pcb_document_guard import journal_identity, require_copy_qualification

    if journal_identity(store) != journal:
        raise ValueError("The SDK operation journal identity changed")
    proof = verify_origin(
        origin,
        project,
        board,
        record_reader=lambda operation_id: _copy_record(store, operation_id),
    )
    require_copy_qualification(
        store, proof["operation_id"], board, startup_operation, current_operation
    )
    from . import closed_copy_startup

    if closed_copy_startup.has_preparation(store, proof["operation_id"]):
        # A new preparation can never fall back after failed final publication,
        # even if this Open happened to leave the two files byte-identical.
        transition = closed_copy_startup.verify(
            store,
            proof,
            startup_operation,
            scope,
            journal,
            current_operation,
            post_open_token,
        )
        proof["post_open_transition"] = transition
    else:
        if post_open_token is not None:
            raise ValueError("Post-Open proof has no original preparation")
        # Compatibility only for the original, strict untouched-genesis route.
        # Never construct transition evidence from a historical startup receipt.
        for role, path in (("project", project), ("board", board)):
            if _record(path)["sha256"] != proof["genesis"][role + "_sha256"]:
                raise ValueError("Diagnostic requires unchanged closed-copy genesis: " + role)
    verify_history(
        store, proof["operation_id"], current_operation, startup_operation, project, board, journal
    )
    return proof


def verify_history(store, copy_id, current_operation, startup_operation, project, board, journal):
    """Bounded unchanged exclusion rule shared by preparation and pre-Save checks."""
    from .pcb_document_guard import journal_identity

    if journal_identity(store) != journal:
        raise ValueError("The SDK operation journal identity changed")
    connection = store.connection()
    steps = [0]

    def progress():
        steps[0] += 1000
        return int(steps[0] > 2_000_000)

    connection.set_progress_handler(progress, 1000)
    saved = False
    try:
        connection.execute("SAVEPOINT close_diagnostic_history")
        saved = True
        stamp = connection.execute(
            "SELECT rowid FROM operations WHERE operation_id=?",
            (copy_id,),
        ).fetchone()
        if stamp is None:
            raise ValueError("Closed-copy history is unavailable")
        # Do not allocate unbounded headers, or cast a potentially oversized plan
        # to a blob, before enforcing the existing history byte budget.
        headers = (
            "CASE WHEN typeof(operation_id)='text' AND octet_length(operation_id)<=96 "
            "THEN operation_id END, "
            "CASE WHEN typeof(command)='text' AND octet_length(command)<=512 "
            "THEN command END, "
            "CASE WHEN typeof(status)='text' AND octet_length(status)<=64 "
            "THEN status END"
        )
        rows = connection.execute(
            "SELECT " + headers + ",octet_length(plan),octet_length(operation_id), "
            "octet_length(command),octet_length(status) FROM operations "
            "WHERE rowid>=? AND (status IS NULL OR status<>'planned') ORDER BY rowid LIMIT ?",
            (stamp[0], MAX_HISTORY + 1),
        ).fetchall()
        if any(not all(row[:3]) for row in rows):
            raise ValueError("Closed-copy history header is invalid or unbounded")
        if (
            len(rows) > MAX_HISTORY
            or sum(sum(value or 0 for value in row[3:]) for row in rows) > MAX_HISTORY_BYTES
        ):
            raise ValueError("Closed-copy history observation budget exceeded")
        allowed = {copy_id, current_operation, startup_operation}
        budget = {"project_bytes": 0, "paths": 0}
        for item in rows:
            operation_id, command, status = item[:3]
            checkpoint()
            if operation_id in allowed or status == "planned":
                continue
            row = connection.execute(
                "SELECT plan FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            plan = json.loads(row[0] or "null")
            if _related_history(plan, command, project, board, budget):
                raise ValueError("Isolation copy has a prior operation: " + command)
    finally:
        connection.set_progress_handler(None, 0)
        if saved:
            connection.execute("RELEASE close_diagnostic_history")
    if journal_identity(store) != journal:
        raise ValueError("The SDK operation journal identity changed")


def state(backend, *, pid, progid):
    if type(pid) is not int or pid <= 0 or not PROGID.fullmatch(str(progid)):
        refuse("Diagnostic requires an explicit PID and one versioned Layout ProgID")
    invalidate_diagnostic_content()
    return backend.invoke(
        "close_diagnostic_state",
        {"pid": pid, "progid": progid, "observe_only": True},
        timeout_seconds=STATE_SECONDS,
    )


def owned_candidate(adapter, pointer, worker):
    """The authenticated start and this diagnostic must use the same candidate."""
    reader = getattr(adapter, "ownership_record_get", None)
    if not callable(reader):
        raise ValueError("Original startup candidate reader is unavailable")
    record = reader(pointer["startup_operation_id"])
    facts = record["receipt"]["snapshot_after"]["extensions"]["facts"]
    if not same_diagnostic_candidate(facts.get("adapter"), adapter_identity()) or not (
        same_diagnostic_candidate(facts.get("worker_adapter"), worker)
    ):
        raise ValueError("Owned startup, controller and worker are different candidates")


def target(adapter, call, options):
    from .driver import _owned_process_identity
    from .native_com_adapter import _layout_board_path

    try:
        project = str(files.drc._plain_path(Path(options["project"])))
        if Path(project).suffix.lower() != ".prj":
            refuse("Diagnostic requires the new isolated .prj, not a bare board")
        board = str(files.drc._plain_path(_layout_board_path(options)))
        pid, progid = int(options["pid"]), options["progid"]
        origin, origin_record = origin_file(options["isolated_origin"])
        from .pcb_document_guard import journal_identity

        store = adapter.diagnostic_store()
        journal = journal_identity(store)
        actual = _owned_process_identity("pcb", str(pid))
        _, pointer = adapter._ownership("pcb", actual)
        if not pointer:
            refuse("Diagnostic requires the original verified owned startup receipt")
        proof = fresh_copy(
            origin,
            project,
            board,
            current_operation=call.operation_id,
            startup_operation=pointer["startup_operation_id"],
            store=store,
            journal=journal,
            scope=call.extras.get("scope"),
            post_open_token=options.get("post_open_token"),
        )
        identity = state(adapter.backend, pid=pid, progid=progid)
        if not same_diagnostic_candidate(identity.get("adapter"), adapter_identity()):
            raise ValueError("Diagnostic controller and worker are different candidates")
        owned_candidate(adapter, pointer, identity["adapter"])
        bound = files.binding(
            project,
            board,
            identity["application"],
            identity["adapter"],
            adapter_identity(),
            actual,
            files.drc.planned_side_effects(Path(board)),
        )
        bound.update(
            diagnostic_contract=CONTRACT,
            diagnostic_operation_id=call.operation_id,
            progid=progid,
            ownership_pointer=pointer,
            isolated_origin=origin_record,
            copy_proof=proof,
            journal_identity=journal,
        )
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
        refuse("Closed diagnostic admission failed: " + str(error))
    return {
        "mode": "session",
        "engine_id": "xpedition-pcb",
        "instance_id": str(pid),
        "object_id": project,
        "locator": Path(project).as_uri(),
        "extensions": {
            "domain": "pcb",
            "command": COMMAND,
            "project": project,
            "application": identity["application"],
            "worker_adapter": identity["adapter"],
            "close_diagnostic_binding": bound,
        },
    }


def before(adapter, target):
    from .driver import _owned_process_identity

    bound = target["extensions"]["close_diagnostic_binding"]
    try:
        from .pcb_document_guard import journal_identity

        store = adapter.diagnostic_store()
        journal = journal_identity(store)
        if journal != bound["journal_identity"]:
            raise ValueError("Original SDK operation journal changed")
        origin, record = origin_file(bound["isolated_origin"]["path"])
        if record != bound["isolated_origin"]:
            raise ValueError("Isolation registration changed")
        proof = fresh_copy(
            origin,
            bound["project"],
            bound["board"],
            current_operation=target["extensions"].get("source_operation_id"),
            startup_operation=bound["ownership_pointer"]["startup_operation_id"],
            store=store,
            journal=journal,
            scope=adapter.ownership_scope(),
            post_open_token=(bound["copy_proof"].get("post_open_transition") or {}).get("token"),
        )
        if proof != bound["copy_proof"]:
            raise ValueError("Original copy proof changed")
        process = _owned_process_identity("pcb", target["instance_id"])
        _, pointer = adapter._ownership("pcb", process)
        if pointer != bound["ownership_pointer"]:
            raise ValueError("Original startup ownership changed")
        identity = state(adapter.backend, pid=process["pid"], progid=bound["progid"])
        files.require_identity(
            bound,
            identity,
            adapter_identity(),
            process,
            closed=False,
            candidate_match=same_diagnostic_candidate,
        )
        owned_candidate(adapter, pointer, identity["adapter"])
        adapter._engineering_guard(
            target,
            identity["application"],
            exclude_operation_id=target["extensions"].get("source_operation_id"),
        )
        frame = files._capture(bound)
        if any(row.get("read_error") for row in frame["files"].values()):
            raise ValueError("Pre-dispatch bound file set is unreadable")
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
        refuse("Closed diagnostic confirmation binding failed: " + str(error))
    return _snapshot({"identity": identity, "process": process, "files": frame, "proof": proof})


def before_apply(adapter, target, operation_id):
    """Recheck copy history after SDK dispatch, without querying native CAD."""
    bound = target["extensions"]["close_diagnostic_binding"]
    try:
        from .pcb_document_guard import journal_identity

        invalidate_diagnostic_content()
        if not same_diagnostic_candidate(bound["controller"], adapter_identity()):
            raise ValueError("Diagnostic controller candidate changed before write")
        store = adapter.diagnostic_store()
        journal = journal_identity(store)
        if journal != bound["journal_identity"]:
            raise ValueError("Original SDK operation journal changed")
        origin, record = origin_file(bound["isolated_origin"]["path"])
        if record != bound["isolated_origin"]:
            raise ValueError("Isolation registration changed")
        proof = fresh_copy(
            origin,
            bound["project"],
            bound["board"],
            current_operation=operation_id,
            startup_operation=bound["ownership_pointer"]["startup_operation_id"],
            store=store,
            journal=journal,
            scope=adapter.ownership_scope(),
            post_open_token=(bound["copy_proof"].get("post_open_transition") or {}).get("token"),
        )
        if proof != bound["copy_proof"]:
            raise ValueError("Original copy proof changed")
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
        refuse("Closed diagnostic write admission failed: " + str(error))


def terminal_observed(result):
    if not isinstance(result, dict) or result.get("contract") != CONTRACT:
        return False
    life = result.get("board_lifecycle") or {}
    if not isinstance(life, dict):
        return False
    return (
        type(life.get("save_count")) is int
        and life["save_count"] == 1
        and type(life.get("close_count")) is int
        and type(life.get("save_acknowledged")) is bool
        and life.get("save_acknowledged") is life.get("save_result")
        and result.get("terminal_state") == "returned"
        and (
            life.get("save_returned") is True
            and type(life.get("save_result")) is bool
            and (
                life["save_result"] is False
                and life.get("close_count") == 0
                and life.get("close_returned") is False
                or life["save_result"] is True
                and life.get("close_returned") is True
                and type(life.get("close_result")) is bool
                and life.get("close_count") == 1
            )
        )
    )


def observe(adapter, bound, result):
    from .driver import _owned_process_identity

    if not terminal_observed(result) or result.get("binding_sha256") != digest(bound):
        raise ValueError("Only the same returned diagnostic permits native observation")
    frames, issues, attempts = [], [], []
    observation_uncertain = False
    requests = 0
    for index in range(2):
        attempt = {"frame": index + 1, "identity": None, "process": None}
        attempts.append(attempt)
        # Failure terminates observation: never probe a possibly pending COM call again.
        try:
            checkpoint()
            requests += 1
            identity = state(
                adapter.backend, pid=bound["generation"]["pid"], progid=bound["progid"]
            )
            attempt["identity"] = copy.deepcopy(identity)
            process = _owned_process_identity("pcb", str(bound["generation"]["pid"]))
            attempt["process"] = copy.deepcopy(process)
            files.require_identity(
                bound,
                identity,
                adapter_identity(),
                process,
                closed=True,
                candidate_match=same_diagnostic_candidate,
            )
        except Exception as error:
            observation_uncertain = getattr(error, "code", None) in {
                "E_TIMEOUT",
                "E_OUTCOME_UNKNOWN",
            }
            attempt["error"] = {
                **files._error(error),
                "code": getattr(error, "code", None),
                "details": copy.deepcopy(getattr(error, "details", None)),
            }
            issues.append(
                {
                    "frame": index + 1,
                    "reason": "closed_identity_unverified",
                    "error": attempt["error"],
                }
            )
            break
        try:
            capture = files._capture(bound)
        except Exception as error:
            # Keep preceding complete frames and the failed attempt. The shared
            # capture did not return a frame: never invent its partial file rows.
            attempt["file_observation_error"] = {
                **files._error(error),
                "code": getattr(error, "code", None),
                "details": copy.deepcopy(getattr(error, "details", None)),
                "partial_frame_available": False,
            }
            issues.append(
                {
                    "frame": index + 1,
                    "reason": "file_frame_not_returned",
                    "error": attempt["file_observation_error"],
                }
            )
            break
        frames.append({"identity": identity, "process": process, **capture})
        for role, row in capture["files"].items():
            if row.get("read_error") or role in {"project", "board"} and row.get("exists") is False:
                issues.append(
                    {
                        "frame": index + 1,
                        "role": role,
                        "reason": "file_unreadable_or_missing",
                    }
                )
    if len(frames) == 2:
        for role, row in frames[0]["files"].items():
            if row != frames[1]["files"][role]:
                issues.append({"role": role, "reason": "file_changed_between_frames"})
    life = result["board_lifecycle"]
    if life.get("save_acknowledged") is not True or life.get("close_result") is not True:
        issues.append({"reason": "save_or_close_not_acknowledged"})
    return {
        "status": "unobserved"
        if observation_uncertain and not frames
        else "complete"
        if len(frames) == 2 and not issues
        else "incomplete",
        "binding": copy.deepcopy(bound),
        "frames": frames,
        "issues": issues,
        "identity_attempts": attempts,
        "native_request_dispatched": requests > 0,
        "native_request_attempts": requests,
        "persistence_verified": False,
        "execution_qualified": False,
        "reopen_attempted": False,
        "replay_allowed": False,
    }


def finish(adapter, call, plan, result, cause, uncertain):
    """Own the entire after-dispatch path; generic observations are unreachable."""
    from .driver import DriverError, _unobserved_receipt

    bound = plan["target"]["extensions"]["close_diagnostic_binding"]
    receipt = _unobserved_receipt(call, result, cause, None, persistence_unverified=True)
    life = (result or {}).get("board_lifecycle") or {}
    if not isinstance(life, dict):
        life = {}
    receipt["extensions"].update(
        save_acknowledged=life.get("save_acknowledged") is True,
        persistence_independently_reopened=False,
        readback_error=None,
    )
    receipt["outcome"].update(execution="unknown", change="unknown", persistence="unknown")
    observation = {
        "status": "unobserved",
        "native_request_dispatched": False,
        "original_call_terminal_observed": False,
        "reason": "original_close_call_terminal_unobserved",
        "frames": [],
    }
    if (
        not uncertain
        and terminal_observed(result)
        and result.get("binding_sha256") == digest(bound)
    ):
        try:
            from .driver import _native_binding, _observe_only

            with (
                _native_binding(plan["target"]),
                _observe_only(),
                operation_stage("observe_after"),
            ):
                observation = observe(adapter, bound, result)
            observation["original_call_terminal_observed"] = True
        except Exception as error:
            observation = {
                **observation,
                "status": "incomplete" if observation.get("frames") else "unobserved",
                "original_call_terminal_observed": True,
                "reason": "observation_failed",
                "error": {
                    **files._error(error),
                    "code": getattr(error, "code", None),
                    "details": copy.deepcopy(getattr(error, "details", None)),
                },
            }
        receipt["snapshot_after"] = _snapshot({"close_diagnostic": observation})
        receipt["outcome"]["execution"] = (
            "completed"
            if life.get("save_result") is True and life.get("close_result") is True
            else "failed"
        )
    if observation.get("frames"):
        baseline = plan["base_snapshot"]["extensions"]["facts"]["files"]["files"]
        current = observation["frames"][-1]["files"]
        observation["file_changes_from_confirmed_snapshot"] = [
            {"role": role, "before": row, "after": current[role]}
            for role, row in baseline.items()
            if row != current[role]
        ]
        # Reflect the complete comparison in the stored snapshot as well.
        receipt["snapshot_after"] = _snapshot({"close_diagnostic": observation})
    complete = observation["status"] == "complete"
    receipt["extensions"].update(
        independent_readback=observation,
        independent_readback_completed=complete,
        diagnostic_only=True,
        candidate_native_qualified=False,
    )
    reason = (
        "Closed diagnostic scope observed; save/reopen, geometry, hazards and PCB "
        "acceptance remain unverified"
        if complete
        else "Closed diagnostic incomplete; preserve this operation and do not replay or query CAD"
    )
    receipt["outcome"]["verification"].update(
        status="passed" if complete else "incomplete",
        scope=CONTRACT,
        coverage="complete" if complete else "none",
        reason=reason,
        snapshot_id=(receipt.get("snapshot_after") or {}).get("id"),
        evidence_refs=[receipt["snapshot_after"]["id"]] if receipt.get("snapshot_after") else [],
        checks=[
            {
                "id": "bound-two-frame-close-diagnostic",
                "status": "passed" if complete else "incomplete",
                "required": True,
                "reason": reason,
                **({"evidence_ref": receipt["snapshot_after"]["id"]} if complete else {}),
            }
        ],
        not_checked=[
            {
                "id": "save-reopen-and-pcb-acceptance",
                "required": False,
                "reason": reason,
            }
        ],
    )
    receipt["failed_stage"] = None if complete else "verify"
    receipt["recovery"]["reason"] = "Inspect this receipt; no automatic reopen or replay"
    # A useful diagnostic may complete with persistence unknown. Never turn that
    # limited result into the SDK's fully successful engineering write receipt.
    raise DriverError(
        "E_OUTCOME_UNKNOWN",
        reason,
        receipt=receipt,
        details={"diagnostic_complete": complete, "replay_allowed": False},
        dispatch_state="dispatched",
    ) from cause
