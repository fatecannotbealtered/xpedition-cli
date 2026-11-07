"""The driver standard commands (DCLI-200 to DCLI-240) and the domain read.

Sessions, documents, long jobs, devices and the host component, built on the adapter
capabilities. A capability the adapter lacks gives ``E_UNSUPPORTED``. The observe commands
report states as data and never fail because of them (DCLI-204).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import TYPE_CHECKING, Any

from .errors import DriverError, usage
from .output import Result, page
from .records import utc_now
from .registry import Command, Flag
from .runtime import READ_DEADLINE_MS, SESSION_DEADLINE_MS, Run, Where, parallel
from .targets import file_key
from .writes import Engine, Verification, WriteSpec, combine, dispatch, read_back, record_state, slim_receipt, slim_result, verify_files

if TYPE_CHECKING:
    from .tool import Tool

LIMIT = 20
MAX_LIMIT = 100
POLL_AFTER_MS = 1000
FINAL_JOB_STATES = ("completed", "failed", "partial", "unknown", "cancelled")


def _limit() -> Flag:
    return Flag("--limit", "integer", f"Items per page; default {LIMIT}, at most {MAX_LIMIT}.", maximum=MAX_LIMIT, default=LIMIT)


def _offset() -> Flag:
    return Flag("--offset", "integer", "Start position of the page; default 0.", default=0)


def _session(required: bool = False) -> Flag:
    return Flag("--session", "string", "The session ID of the instance (DCLI-202).", required=required)


def _doc(summary: str = "The document: its ID, or its path.", required: bool = False) -> Flag:
    return Flag("--doc", "string", summary, required=required)


def make(tool: Tool, path: str, summary: str, flags: list[Flag], handler: Any, *, schema: str, capabilities: list[str],
         examples: list[str], target_kind: str = "object", untrusted: list[str] | None = None, kind: str = "standard",
         tier_when: list[dict[str, str]] | None = None) -> Command:
    spec = tool.contract.standard.get(path) or tool.contract.builtin.get(path)
    if path in tool.contract.standard:
        effects, modes = [spec["effect"]], [m for m in spec["modes"] if m in tool.modes]
    else:
        effects, modes = ["observe"], list(tool.modes)
    no_adapter = not capabilities
    return Command(
        path=path, summary=summary, effects=effects, modes=modes, target_kind=target_kind, flags=flags, kind=kind,
        untrusted_fields=untrusted or [], adapter_capabilities=capabilities, halt=bool(spec.get("halt")) if spec else False,
        output_schema={"$ref": schema}, examples=[f"{tool.name} {e}".rstrip() for e in examples],
        maturity="stable" if no_adapter else "draft", tier_when=tier_when or [],
        needs_scope=spec.get("needs_scope", True) if spec else True,
        counts_for_risk=spec.get("counts_for_risk", True) if spec else True, handler=handler,
    )


def commands(tool: Tool) -> list[Command]:
    out: list[Command] = []
    std = tool.contract.standard

    def offered(path: str) -> bool:
        spec = std[path]
        if spec.get("requires") == "host_component" and not tool.host_component:
            return False
        return any(m in tool.modes for m in spec["modes"])

    obj = "objects.schema.json#/$defs/"
    table = [
        ("session list", "List every running instance with its state and documents.", [Flag("--state", "string", "Only sessions in this state; all for every state.",
         choices=tuple(s["state"] for s in tool.contract["session_states"]) + ("all",)), _limit(), _offset()], session_list,
         obj + "page", ["sessions"], ["session list"], "session", [".items[*].blocker.title", ".items[*].documents[*].path"], None),
        ("session get", "The full state of one instance.", [_session()], session_get, obj + "session", ["sessions"],
         ["session get --session app-1234-5f3a"], "session", [".blocker.title", ".documents[*].path"], None),
        ("session open", "Start a new instance, optionally opening a document.", [Flag("--doc", "path", "A document to open in the new instance."),
         Flag("--kind", "string", "The kind of document the instance is for, when the application runs a separate program for it.")],
         lambda run: Engine(run).execute(SessionOpen(run, run.call.command)), "receipt.schema.json", ["open_session"],
         ["session open"], "session", [], None),
        ("session close", "Close an instance; refused while it has unsaved changes.", [_session(),
         Flag("--discard", "boolean", "Discard unsaved changes; dangerous.")],
         lambda run: Engine(run).execute(SessionClose(run, run.call.command)), "receipt.schema.json", ["close_session"],
         ["session close --session app-1234-5f3a"], "session", [],
         [{"when": "close_unowned_session", "tier": "dangerous"}, {"when": "discard_unsaved_changes", "tier": "dangerous"}]),
        ("doc list", "List the documents of every instance, or the files under a folder in file mode.",
         [_session(), _doc("File mode: the folder or file to look at."), _limit(), _offset()], doc_list, obj + "page",
         ["documents"], ["doc list"], "document", [".items[*].path"], None),
        ("doc get", "The state of one document.", [_doc(required=True), _session()], doc_get, obj + "document", ["documents"],
         ["doc get --doc design.dat"], "document", [".path"], None),
        ("doc create", "Create a document: untitled in an instance, or at --path, from --template when given.",
         [_session(), Flag("--kind", "string", "The kind of document, when the application has several."),
          Flag("--path", "path", "Where the new document lives; needed in file mode and when the application names the file at once.", output=True),
          Flag("--template", "path", "A template the application starts the document from.")],
         lambda run: Engine(run).execute(DocCreate(run, run.call.command)), "receipt.schema.json", ["create_doc"],
         ["doc create --session app-1234-5f3a", "doc create --kind part --template start.prt --path D:/work/bracket.prt"],
         "document", [], [{"when": "target_exists", "tier": "two_step"}]),
        ("doc open", "Open a file in an instance.", [_session(), Flag("--doc", "path", "The file to open.", required=True)],
         lambda run: Engine(run).execute(DocOpen(run, run.call.command)), "receipt.schema.json", ["open_doc"],
         ["doc open --session app-1234-5f3a --doc design.dat"], "document", [], None),
        ("doc save", "Save a document to its path; the preview lists every change since the last save.",
         [_doc(), _session(), Flag("--output", "path", "Write the full preview, with every change, to this file.")],
         lambda run: Engine(run).execute(DocSave(run, run.call.command)), "receipt.schema.json", ["save_doc", "preview", "read_back"],
         ["doc save --doc design.dat"], "document", [], None),
        ("doc save-as", "Save a document as a new file.", [_doc(), _session(), Flag("--output", "path", "The new file.", required=True, output=True)],
         lambda run: Engine(run).execute(DocSaveAs(run, run.call.command)), "receipt.schema.json", ["save_doc_as"],
         ["doc save-as --doc design.dat --output design-v2.dat"], "file", [],
         [{"when": "target_exists", "tier": "two_step"}]),
        ("doc close", "Close a document; refused with unsaved changes unless --discard.",
         [_doc(), _session(), Flag("--discard", "boolean", "Discard unsaved changes; dangerous.")],
         lambda run: Engine(run).execute(DocClose(run, run.call.command)), "receipt.schema.json", ["close_doc"],
         ["doc close --doc design.dat"], "document", [], [{"when": "discard_unsaved_changes", "tier": "dangerous"}]),
        ("doc undo", "Undo this tool's most recent change to a document.", [_doc(), _session()],
         lambda run: Engine(run).execute(DocUndo(run, run.call.command)), "receipt.schema.json", ["undo", "read_back"],
         ["doc undo --doc design.dat"], "document", [], None),
        ("job list", "List the long jobs this tool started, in every session.", [Flag("--state", "string", "Only jobs in this state; all for every state.",
         choices=tuple(tool.contract["jobs"]["states"]) + ("all",)), _limit(), _offset()], job_list, obj + "page", ["job_status"],
         ["job list"], "job", [], None),
        ("job get", "The state of one long job; --wait waits until it ends or the time runs out.",
         [Flag("--job", "string", "The job ID.", required=True), Flag("--wait", "duration", "Wait up to this long, at most 600s, for the job to end.")],
         job_get, obj + "job", ["job_status"], ["job get --job 7 --wait 60s"], "job", [], None),
        ("job cancel", "Ask to cancel a long job this tool started; a halt command.", [Flag("--job", "string", "The job ID.", required=True)],
         job_cancel, "receipt.schema.json", ["cancel", "job_status"],
         ["job cancel --job 7"], "job", [], None),
        ("device list", "List the devices with their state.", [_limit(), _offset()], device_list, obj + "page", ["devices"],
         ["device list"], "device", [], None),
        ("device get", "The state of one device.", [Flag("--device", "string", "The device ID.")], device_get, obj + "device",
         ["devices"], ["device get --device cnc-01"], "device", [], None),
        ("plugin get", "The host component version on disk and the version each instance has loaded.", [], plugin_get,
         obj + "plugin", ["plugin_status"], ["plugin get"], "plugin", [], None),
        ("plugin install", "Install or upgrade the host component shipped with this tool; running instances keep the version they loaded.",
         [],
         lambda run: Engine(run).execute(PluginInstall(run, run.call.command)), "receipt.schema.json",
         ["plugin_install", "plugin_status"], ["plugin install"], "plugin", [], None),
        ("plugin remove", "Remove the host component.", [], lambda run: Engine(run).execute(PluginRemove(run, run.call.command)),
         "receipt.schema.json", ["plugin_remove", "plugin_status"], ["plugin remove"], "plugin", [], None),
    ]
    for path, summary, flags, handler, schema, caps, examples, target_kind, untrusted, tier_when in table:
        if offered(path):
            out.append(make(tool, path, summary, flags, handler, schema=schema, capabilities=caps, examples=examples,
                            target_kind=target_kind, untrusted=untrusted, tier_when=tier_when))
    return out


def add_target_flags(tool: Tool, command: Command) -> None:
    """The runtime adds --session, --doc and --device by mode (DCLI-027), and --output for the
    full preview of a write that has no --output of its own (DCLI-054)."""
    names = [f.name for f in command.flags]
    if command.verb == "list":
        # Every list pages the same way (DCLI-006, DCLI-026).
        if "--limit" not in names:
            command.flags.append(_limit())
        if "--offset" not in names:
            command.flags.append(_offset())
    extra = []
    if "session" in command.modes:
        extra.append(_session())
    if "session" in command.modes or "file" in command.modes:
        extra.append(_doc("The document: its ID or path; in file mode, the file path."))
    if "device" in command.modes:
        extra.append(Flag("--device", "string", "The device ID."))
    command.flags = extra + command.flags
    if command.writes and "--output" not in names:
        command.flags.append(Flag("--output", "path", "Write the full preview, with every change, to this file.", runtime=True))
    if "artifact" in command.effects:
        # The runtime finds an existing file of an output flag itself; for a file no output flag
        # names, such as a new document named by --doc, the preview reports it (DCLI-051).
        command.tier_when = [{"when": "target_exists", "tier": "two_step"}] + command.tier_when
    tiers = tool.tiers
    if command.writes and not command.halt and any(tiers.base(command, m) == "direct" for m in command.modes):
        command.tier_when = command.tier_when + [{"when": "preview_touches_other_targets", "tier": "two_step"}]


# ------------------------------------------------------------------ reads


def _page(run: Run, items: list[Any], untrusted: list[str], **extra: Any) -> Result:
    call = run.call
    assert call is not None
    offset = call.values.get("--offset", 0)
    limit = call.values.get("--limit", LIMIT)
    data, start = page(items, offset, limit)
    data.update(extra)
    return Result(data, untrusted, offset=start)


def domain_read(run: Run, command: Command) -> Result:
    """A domain read through the adapter's ``read`` (DCLI-260)."""
    where = run.where(command)
    if where.session is not None:
        run.gate_session(where.session)
    if where.device is not None:
        run.gate_device(where.device)
    inputs = run.inputs(command)
    request: dict[str, Any] = {"command": command.path, "inputs": inputs.adapter}
    if where.session:
        request["session"] = where.session["id"]
    if where.document:
        request["document"] = where.document["id"]
    if where.device:
        request["device"] = where.device["id"]
    untrusted = [f".{f}" for f in command.untrusted_fields] + [f".items[*].{f}" for f in command.untrusted_fields]
    if command.long:
        # A read that can take longer than a call may runs in a worker; no scope and no
        # confirmation, because it changes nothing (DCLI-065).
        from .jobs import submit_read

        job = submit_read(run, request, untrusted)
        out = {"id": job["id"], "state": job["state"], "poll": run.cmd("job get", [("--job", job["id"]), ("--wait", "60s")])}
        return Result({"job": out}, [])
    result = run.capability("read", request).get("result")
    if command.verb == "list" and not (isinstance(result, dict) and isinstance(result.get("items"), list)):
        # --limit and --offset page the items; a list without them could not be paged (DCLI-006).
        raise DriverError("E_PROTOCOL", f"{command.path} is a list, but the adapter returned no items list; "
                                        "a list returns its entries in items.")
    offset = None
    if isinstance(result, dict) and isinstance(result.get("items"), list) and "count" not in result:
        # The runtime pages a list the adapter returns whole (DCLI-006).
        rest = {k: v for k, v in result.items() if k != "items"}
        result, offset = page(result["items"], run.call.values.get("--offset", 0), run.call.values.get("--limit", LIMIT))
        result.update(rest)
    if isinstance(result, dict) and (where.device or {}).get("simulated"):
        result = {**result, "simulated": True}  # results from a simulated device say so (DSEC-062)
    return Result(result, untrusted, offset=offset)


def session_list(run: Run) -> Result:
    sessions = run.sessions()
    state = run.call.values.get("--state", "all")
    items = [s for s in sessions["items"] if state == "all" or s["state"] == state]
    return _page(run, items, [".items[*].blocker.title", ".items[*].documents[*].path"], complete=sessions["complete"])


def session_get(run: Run) -> Result:
    return Result(run.session(run.call.values.get("--session")), [".blocker.title", ".documents[*].path"])


def doc_list(run: Run) -> Result:
    call = run.call
    items: list[dict[str, Any]] = []
    complete = True
    folder = call.values.get("--doc")
    if folder and "file" in run.tool.modes and not call.values.get("--session"):
        items = run.documents(None, folder, READ_DEADLINE_MS)
    elif "session" in run.tool.modes:
        sessions = run.sessions()
        complete = sessions["complete"]
        chosen = [run.session(call.values["--session"])] if call.values.get("--session") else sessions["items"]
        ready = [s for s in chosen if s["state"] in ("ready", "busy", "blocked", "starting")]
        for s in chosen:
            if s not in ready:
                complete = False
                _notice_unanswered(run, s, s["state"])
        results = parallel(ready, lambda s: run.documents(s["id"], None, SESSION_DEADLINE_MS), SESSION_DEADLINE_MS / 1000 + 0.2)
        for s, (status, value) in zip(ready, results):
            if status == "ok":
                items.extend(value)
            else:
                complete = False
                _notice_unanswered(run, s, "unresponsive")
    else:
        raise usage("doc list needs --doc: the folder or file to look at.", "--doc")
    return _page(run, items, [".items[*].path"], complete=complete)


def _notice_unanswered(run: Run, session: dict[str, Any], state: str) -> None:
    run.notices.append({"type": "session_unanswered", "severity": "warning",
                        "message": f"Session {session['id']} is {state}; its documents are not listed."})


def doc_get(run: Run) -> Result:
    command = run.call.command
    where = run.where(command)
    if where.document is None:
        raise DriverError("E_NOT_FOUND", "No document matches --doc.")
    return Result(where.document, [".path"])


def device_list(run: Run) -> Result:
    return _page(run, run.devices(), [])


def device_get(run: Run) -> Result:
    return Result(run.device(run.call.values.get("--device")))


def plugin_get(run: Run) -> Result:
    return Result(run.capability("plugin_status", {}))


# ------------------------------------------------------------------ long jobs


def _job_record(run: Run, job_id: str) -> dict[str, Any]:
    for record in run.records.all():
        if record.get("_job", {}).get("id") == job_id:
            return record
    raise DriverError("E_NOT_FOUND", f"This tool started no job {job_id}.",
                      next_action=f"Run `{run.tool.name} job list` to see the jobs.")


def job_view(record: dict[str, Any], run: Run | None = None) -> dict[str, Any]:
    job = record["_job"]
    out: dict[str, Any] = {"id": job["id"], "operation_id": record["operation_id"], "state": job["state"],
                           "cancel_requested": job.get("cancel_requested", False)}
    if job.get("progress"):
        out["progress"] = job["progress"]
    out["started_at"] = job["started_at"]
    if job["state"] in FINAL_JOB_STATES:
        out["finished_at"] = job.get("finished_at", record.get("finished_at", utc_now()))
        out["receipt"] = slim_receipt(run, record["receipt"]) if run is not None and record.get("receipt") else record.get("receipt")
        out["result"] = slim_result(run, job.get("result", {})) if run is not None else job.get("result", {})
        out["ok"] = job.get("ok", False)
        if job.get("code"):
            out["code"] = job["code"]
    if job["state"] == "blocked":
        out["action"] = job.get("action", "other")
    return out


def poll_job(run: Run, record: dict[str, Any]) -> dict[str, Any]:
    """Ask the application for the job state; settle the record when the job ends (DCLI-065)."""
    job = record["_job"]
    if job["state"] in FINAL_JOB_STATES:
        return record
    if job.get("worker"):
        # The worker writes the outcome into the record; a worker that ended without one
        # leaves the outcome unknown (DCLI-063, DCLI-065).
        from .jobs import worker_alive

        latest = run.records.load(record["operation_id"]) or record
        if latest["_job"]["state"] in FINAL_JOB_STATES or worker_alive(latest["_job"]):
            return latest
        latest = run.records.load(record["operation_id"]) or latest
        if latest["_job"]["state"] not in FINAL_JOB_STATES:
            latest["_job"]["state"] = "unknown"
            _settle_job(run, latest, {"state": "unknown", "native_message": "The worker that ran this job ended without a result."})
            run.records.save(latest)
        return latest
    call_info = record.get("_call") or {}
    request: dict[str, Any] = {"job": job["id"]}
    if call_info.get("session"):
        request["session"] = call_info["session"]
    try:
        status = run.capability("job_status", request, READ_DEADLINE_MS)
    except DriverError as exc:
        if exc.code not in ("E_NOT_FOUND", "E_BACKEND_UNAVAILABLE"):
            raise
        # The application no longer knows the job, or its instance is gone: the outcome
        # is unknown and the operation can be resolved (DCLI-063).
        status = {"state": "unknown", "native_message": exc.message}
    if status.get("state") not in run.contract["jobs"]["states"]:
        status = {**status, "state": "unknown"}
    job["state"] = status["state"]
    if status.get("progress"):
        job["progress"] = status["progress"]
    if status["state"] == "blocked":
        job["action"] = status.get("action", "other")
    if status["state"] in FINAL_JOB_STATES:
        _settle_job(run, record, status)
    run.records.save(record)
    return record


def _settle_job(run: Run, record: dict[str, Any], status: dict[str, Any], made: Verification | None = None) -> None:
    """Complete the job's operation record with a full receipt (DCLI-065). ``made`` is the
    verification a standard command's worker already did with that command's own read-back."""
    from .writes import Engine as _Engine, to_read_back

    job, state = record["_job"], status["state"]
    execution = {"completed": "completed", "partial": "completed", "failed": "failed", "cancelled": "interrupted",
                 "unknown": "unknown"}[state]
    dims = run.contract["receipt"]["dimensions"]
    change, delivery, persistence = (status.get(d) if status.get(d) in dims[d] else "unknown"
                                     for d in ("change", "delivery", "persistence"))
    if state == "cancelled" and "change" not in status:
        change = "not_applied"
    if state in ("cancelled", "failed"):
        delivery = delivery if "delivery" in status else "not_applicable"
        persistence = persistence if "persistence" in status else "not_applicable"
    if state == "partial" and change == "applied":
        change = "partial"
    verification = Verification("not_applicable")
    if made is not None and execution == "completed":
        verification = made
    elif execution == "completed" and to_read_back(change, delivery, persistence, _job_changes(record)):
        verification = _job_verification(run, record, status)
    if status.get("_note"):
        verification.notes.insert(0, status["_note"])
    receipt = dict(record.get("receipt") or {})
    receipt["outcome"] = {"execution": execution, "change": change, "delivery": delivery, "persistence": persistence,
                          "verification": verification.status}
    receipt["observed"], receipt["mismatched"] = verification.observed, verification.mismatched
    receipt["notes"] = [f"Long job {job['id']} ended {state}."] + verification.notes
    if isinstance(status.get("native_message"), str):
        receipt["notes"].append(f"The application said: {status['native_message']}")
    record["receipt"] = receipt
    cause = "E_INTERRUPTED" if state == "cancelled" else status.get("code")
    ok, code = _Engine(run).derive(receipt, cause)
    job.update(ok=ok, code=code, finished_at=utc_now(), result=status.get("result", {}))
    record["state"] = record_state(receipt, code)
    record["finished_at"] = job["finished_at"]
    if record["state"] == "unknown":
        run.locks.hold_as(record["operation_id"], "unknown", record.get("_keys", []))
    else:
        run.locks.release(record["operation_id"])


def _job_changes(record: dict[str, Any]) -> list[dict[str, Any]]:
    """What a job's preview expects, with the files the call named (DCLI-051, DCLI-057)."""
    from .writes import file_checks

    changes = record.get("_changes", [c for c in record["preview"].get("changes", []) if c.get("origin", "tool") == "tool"])
    return changes + file_checks(record, changes)


def _job_verification(run: Run, record: dict[str, Any], status: dict[str, Any]) -> Verification:
    """A job's outputs: the files it made and the fields it changed, both when it did both (DCLI-057)."""
    from .writes import combine, is_file_target

    command = run.tool.commands.get(record.get("command", ""))
    if command is not None and command.verify_after_save and (record.get("_call") or {}).get("mode") == "session":
        return Verification("deferred", notes=["The application shows this change correctly only after a save; doc save verifies it."])
    changes = _job_changes(record)
    files = [c for c in changes if c.get("field") == "exists" and is_file_target(c["target"])]
    fields = [c for c in changes if c not in files]
    parts = []
    if status.get("files") or files:
        parts.append(verify_files(status, files))
    if fields and record.get("_request"):
        request = {**record["_request"], "operation_id": record["operation_id"],
                   "targets": sorted({c["target"] for c in fields}),
                   "fields": sorted({c["field"] for c in fields if "field" in c})}
        parts.append(read_back(run, request, fields, record.get("_tolerances", {})))
    if not parts:
        return Verification("incomplete", notes=["The job's outputs cannot be read back."])
    return combine(parts)


def run_write_job(run: Run, record: dict[str, Any]) -> None:
    """In the worker: apply the write with the long deadline, then settle it like any job (DCLI-065)."""
    if record.get("_argv"):
        return run_standard_job(run, record)
    request = {**(record.get("_request") or {}), "operation_id": record["operation_id"]}

    def apply() -> Any:
        # The checks that run right before any dispatch run here too (DCLI-057, DSEC-061).
        with _BeforeDispatch(run, run, record):
            if request.get("device"):
                run.gate_device(run.fresh_device(request["device"]))
            if request.get("session"):
                run.gate_session(run.session(request["session"]))
        return dispatch(run, "apply", request)

    status, _facts = _job_dispatch(run, apply, strict=True)
    if status["state"] == "completed":
        run.settle_hang = lambda note: _settle_hung(run, run, record, status, Verification("incomplete", notes=[note]))
    try:
        if status["state"] == "completed":
            # The read_back_mismatch row of a long write is caused here, in the worker (DVER-030).
            from .writes import pause_before_read_back

            changes = record.get("_changes") or []
            note = pause_before_read_back(run, record["operation_id"], record.get("command", ""), status, changes,
                                          lambda: _job_verification(run, record, status))
            if note:
                status["_note"] = note
        record["_job"]["state"] = status["state"]
        _settle_job(run, record, status)
    finally:
        run.settle_hang = None
    _keep_notices(record, run)
    run.records.save(record)


def _settle_hung(run: Run, sub: Run, record: dict[str, Any], status: dict[str, Any], made: Verification) -> None:
    """In the worker, a read after the dispatch hangs: the job ends with what is known before
    the hang ends the worker (DCLI-278)."""
    record["_job"]["state"] = status["state"]
    _settle_job(run, record, status, made)
    _keep_notices(record, sub)
    run.records.save(record)


class _BeforeDispatch:
    """Around the reads right before a worker's dispatch: when one of them hangs, the watchdog
    settles the job as failed with E_BUSY, because nothing was sent, and frees its targets before
    it ends the worker (DCLI-278). The dispatch begins only when the watchdog has not settled it."""

    def __init__(self, run: Run, sub: Run, record: dict[str, Any]) -> None:
        self.run, self.sub, self.record = run, sub, record
        self.lock = threading.Lock()
        self.reading = False
        self.settled = False

    def settle(self, note: str) -> None:
        with self.lock:
            if not self.reading:
                return  # the reads ended in time; the hang is the dispatch's own
            self.settled = True
            status = {"state": "failed", "change": "not_applied", "code": "E_BUSY",
                      "_note": "The application did not answer a read before the dispatch in time; nothing was sent."}
            _settle_hung(self.run, self.sub, self.record, status, Verification("not_applicable"))

    def __enter__(self) -> None:
        self.reading = True
        self.run.settle_hang = self.settle

    def __exit__(self, *exc: object) -> None:
        with self.lock:
            self.reading = False
            self.run.settle_hang = None
            settled = self.settled
        if settled:
            threading.Event().wait()  # the watchdog ends the worker; nothing may be sent meanwhile


def run_standard_job(run: Run, record: dict[str, Any]) -> None:
    """In the worker: run a standard write the tool registered as long, such as opening a large
    project, with the long deadline and that command's own checks and read-back (DCLI-065)."""
    from .runtime import Run as _Run
    from .writes import pause_before_read_back

    operation_id = record["operation_id"]
    call = run.tool.parser.parse(record["_argv"])
    sub = _Run(run.tool, call, run.env)
    sub.on_hang = run.on_hang  # an in-process call that hangs still ends the worker (DCLI-278)
    made: Verification | None = None
    try:
        spec = LONG_SPECS[record["command"]](sub, call.command)

        def apply() -> Any:
            with _BeforeDispatch(run, sub, record):
                spec.prepare()  # the state checks that refuse a write, right before dispatch (DCLI-057)
            confirmed = record.get("_call") or {}
            now = {"session": (spec.where.session or {}).get("id"), "document": (spec.where.document or {}).get("id")}
            for key, value in now.items():
                if confirmed.get(key) and value != confirmed[key]:
                    raise DriverError("E_CONFLICT", f"The {key} is now {value}, not the confirmed {confirmed[key]}; nothing was sent.",
                                      conflict="token_mismatch")
            return spec.apply(operation_id)

        status, facts = _job_dispatch(sub, apply, strict=False)
        if status["state"] == "completed":
            run.settle_hang = lambda note: _settle_hung(
                run, sub, record, status, made if made is not None else Verification("incomplete", notes=[note]))
            changes = record.get("_changes") or []
            note = pause_before_read_back(sub, operation_id, record["command"], facts, changes,
                                          lambda: spec.probe(operation_id, facts, changes))
            try:
                made = spec.verify(operation_id, facts, changes)
            except Exception as exc:  # noqa: BLE001 - after dispatch, a failure to verify is not a known outcome
                made = Verification("incomplete", notes=[f"The read-back could not be done: {type(exc).__name__}: {exc}"])
            if note:
                made.notes.insert(0, note)
            try:
                status["result"] = spec.result(facts, made)
            except Exception as exc:  # noqa: BLE001
                made.notes.append(f"The result could not be read after the write: {exc}")
            _fresh_document(sub, spec, record)
        run.settle_hang = None
        record["_job"]["state"] = status["state"]
        _settle_job(run, record, status, made)
        _keep_notices(record, sub)
        run.records.save(record)
    finally:
        run.settle_hang = None
        sub.close()


def _keep_notices(record: dict[str, Any], run: Run) -> None:
    """Notices the adapter gave in the worker stay with the job; job get shows them (DCLI-260)."""
    kept = [n for n in run.notices if n.get("type") == "application"]
    if kept:
        record["_job"]["notices"] = kept


def _fresh_document(run: Run, spec: WriteSpec, record: dict[str, Any]) -> None:
    """The receipt names the document as the job left it, so the next save sees no change from outside."""
    doc, session = spec.where.document, spec.where.session
    receipt = record.get("receipt") or {}
    if not doc or not session or "document" not in receipt:
        return
    try:
        run._sessions = None
        now = next((d for d in run.documents(session["id"]) if d.get("id") == doc["id"]), None)
    except DriverError:
        return
    if now is not None:
        receipt["document"] = {"id": now["id"], "revision": now.get("revision"), "dirty": now.get("dirty")}


def _job_dispatch(run: Run, apply: Any, strict: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """Make the dispatch in a job's worker and turn what came back into the job's end state.
    ``strict``: apply reports all three dimensions; other write capabilities report what applies.
    A busy instance or device took nothing, so the worker tries again after ``retry_after_ms``
    for up to ``jobs.busy_retry_seconds`` before the job fails with E_BUSY (DCLI-065)."""
    end = time.monotonic() + float(run.contract["jobs"]["busy_retry_seconds"])
    while True:
        status, facts, wait_ms = _job_dispatch_once(run, apply, strict)
        left = end - time.monotonic()
        if status.get("code") != "E_BUSY" or status.get("change") != "not_applied" or left <= 0:
            return status, facts
        time.sleep(min(max(wait_ms, 100) / 1000, left))
        run._sessions = None  # the next try reads the instance and the device again
        run._devices = None


def _job_dispatch_once(run: Run, apply: Any, strict: bool) -> tuple[dict[str, Any], dict[str, Any], int]:
    from .adapter import BadProtocol, BadReply, CapabilityError, Lost, Unanswered

    status: dict[str, Any]
    facts: Any = {}
    wait_ms = 500
    try:
        facts = apply()
    except DriverError as exc:
        status = {"state": "failed", "change": "not_applied", "code": exc.code, "native_message": exc.message}
        wait_ms = exc.details.get("retry_after_ms", wait_ms)
    except CapabilityError as exc:
        code, ds = run.host_error(exc, exc.dispatch_state, write=True).code, exc.dispatch_state
        if ds == "not_started" or (ds == "dispatched" and code in ("E_VALIDATION", "E_NOT_FOUND", "E_UNSUPPORTED")):
            status = {"state": "failed", "change": "not_applied", "code": code}
        elif ds == "dispatched" and code == "E_NATIVE_FAILURE":
            status = {"state": "failed", "change": "rolled_back", "code": code}
        else:
            status = {"state": "unknown"}
        if isinstance(exc.data.get("native_message"), str):
            status["native_message"] = exc.data["native_message"]
        wait_ms = exc.data.get("retry_after_ms", wait_ms)
    except BadReply as exc:
        status = {"state": "unknown", "native_message": str(exc)}
    except Unanswered as exc:
        status = {"state": "unknown"} if exc.sent else {"state": "failed", "change": "not_applied", "code": "E_BUSY"}
    except (Lost, BadProtocol) as exc:
        status = {"state": "failed", "change": "not_applied", "code": "E_BACKEND_UNAVAILABLE", "native_message": str(exc)}
    else:
        if not isinstance(facts, dict) or facts.get("dispatch_state") not in ("not_started", "dispatched", "unknown"):
            status = {"state": "unknown"}
        elif facts["dispatch_state"] == "not_started":
            status = {"state": "failed", "change": "not_applied"}
        elif facts["dispatch_state"] == "unknown":
            status = {"state": "unknown"}
        else:
            status = {"state": "completed", **{d: facts[d] for d in ("change", "delivery", "persistence", "files", "result",
                                                                       "native_message") if d in facts}}
            if not strict:
                for dim in ("change", "delivery", "persistence"):
                    status.setdefault(dim, "not_applicable")
    if not isinstance(wait_ms, int) or isinstance(wait_ms, bool):
        wait_ms = 500
    return status, facts if isinstance(facts, dict) else {}, wait_ms


def job_list(run: Run) -> Result:
    state = run.call.values.get("--state", "all")
    records = [r for r in run.records.all() if "_job" in r]
    for record in records:
        if record["_job"]["state"] not in FINAL_JOB_STATES and (record["_job"].get("worker") or run.backend.has("job_status")):
            try:
                poll_job(run, record)
            except DriverError:
                pass  # the job keeps its last known state
    from .jobs import ReadJobs, read_view, settle_dead_read

    reads = [settle_dead_read(run, job) for job in ReadJobs(run).all()]
    worker_sessions = [(r.get("_call") or {}).get("session") for r in records
                       if r["_job"].get("worker") and r["_job"]["state"] == "running"]
    worker_sessions += [(j.get("_request") or {}).get("session") for j in reads if j["state"] == "running"]
    blocked = _blocked_sessions(run) if any(worker_sessions) else {}
    views = []
    for record in records:
        view = job_view(record, run)
        if record["_job"].get("worker"):
            _show_blocked(run, view, blocked.get((record.get("_call") or {}).get("session")))
        views.append(view)
    for job in reads:
        view = read_view(job)
        _show_blocked(run, view, blocked.get((job.get("_request") or {}).get("session")))
        views.append(view)
    return _page(run, [v for v in views if state == "all" or v["state"] == state], [])


def _blocked_sessions(run: Run) -> dict[str, dict[str, Any]]:
    """The blocker of every session a dialog holds, such as a dialog that opened while a job the
    runtime runs was working in it (DCLI-220). It asks ``sessions`` with the same limit as
    ``session list``; a session it cannot see counts as not blocked."""
    if not run.backend.has("sessions"):
        return {}
    try:
        items = run.capability("sessions", {}, int(run.contract["session_read_deadline"]["adapter_sessions_ms"])).get("items", [])
    except DriverError:
        return {}
    out = {}
    for session in items if isinstance(items, list) else []:
        if isinstance(session, dict) and isinstance(session.get("id"), str) and session.get("state") == "blocked":
            out[session["id"]] = session.get("blocker") if isinstance(session.get("blocker"), dict) else {}
    return out


def _session_blocker(run: Run, session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    return _blocked_sessions(run).get(session_id)


def _wait_job(run: Run, end: float, session_id: str | None, step: Any, final: Any) -> dict[str, Any] | None:
    """Wait until ``final()`` holds, the time is up, or a dialog blocks the job's session;
    ``step()`` refreshes the job. Returns the blocker, if a dialog was found."""
    look = 0.0
    while True:
        if step() is False or final():
            return None
        if session_id and time.monotonic() >= look:
            look = time.monotonic() + 5
            blocker = _session_blocker(run, session_id)
            if blocker is not None:
                return blocker
        if time.monotonic() >= end:
            return None
        time.sleep(min(0.5, max(0.0, end - time.monotonic())))


def _show_blocked(run: Run, view: dict[str, Any], blocker: dict[str, Any] | None) -> None:
    """A running job whose session a dialog holds waits for the person: it shows as blocked
    with what the person does, and runs on once they have (DCLI-066, DCLI-220)."""
    if blocker is None or view["state"] != "running":
        return
    view["state"] = "blocked"
    action = blocker.get("action")
    view["action"] = action if action in run.contract["human_required_actions"] else "other"
    ids = run.knowledge_ids(blocker)
    if ids:
        view["knowledge"] = ids


def job_get(run: Run) -> Result:
    from .jobs import FINAL, ReadJobs, read_view, run_read, settle_dead_read, worker_of

    call = run.call
    wait = call.values.get("--wait", 0.0)
    if wait > run.contract["jobs"]["max_wait_seconds"]:
        raise usage(f"--wait is at most {run.contract['jobs']['max_wait_seconds']}s.", "--wait")
    job_id = call.values["--job"]
    read_job = ReadJobs(run).load(job_id)
    if read_job is not None:
        if run.worker and worker_of(run, job_id, read_job.get("worker", {}).get("nonce")):
            run_read(run, read_job)  # this process is the job's worker (DCLI-065)
            return Result({"id": job_id, "state": "completed"}, [])
        end = time.monotonic() + wait
        holder = {"job": read_job}

        def refresh_read() -> None:
            holder["job"] = settle_dead_read(run, ReadJobs(run).load(job_id) or holder["job"])

        blocker = _wait_job(run, end, (read_job.get("_request") or {}).get("session"), refresh_read,
                            lambda: holder["job"]["state"] in FINAL)
        read_job = holder["job"]
        view = read_view(read_job)
        _show_blocked(run, view, blocker)
        run.notices.extend(read_job.get("notices") or [])
        if view["state"] not in FINAL:
            view["poll_after_ms"] = POLL_AFTER_MS
        return Result(view, [".message"] + [".result" + p for p in read_job.get("_untrusted", [])])
    record = _job_record(run, job_id)
    if run.worker and worker_of(run, job_id, (record["_job"].get("worker") or {}).get("nonce")):
        with run.mutex.hold():
            latest = run.records.load(record["operation_id"]) or record
            claimed = latest["_job"]["worker"].get("claimed") or latest["_job"]["state"] in FINAL_JOB_STATES
            if not claimed:
                latest["_job"]["worker"]["claimed"] = True
                run.records.save(latest)
        if not claimed:
            run_write_job(run, latest)  # this process is the job's worker; a job runs once (DCLI-065)
        return Result({"id": job_id, "state": "completed"}, [])
    end = time.monotonic() + wait
    held = {"record": record}

    def refresh_write() -> bool:
        try:
            held["record"] = poll_job(run, held["record"])
        except DriverError as exc:
            # An observe command reports the state as data (DCLI-204): the last known one.
            run.notices.append({"type": "job_unanswered", "severity": "warning",
                                "message": f"The application did not report job {held['record']['_job']['id']} ({exc.code}); "
                                           "this is its last known state."})
            return False
        return True

    # A job the application runs reports blocked itself; one the runtime runs is blocked when
    # a dialog holds its session.
    session_id = (record.get("_call") or {}).get("session") if record["_job"].get("worker") else None
    blocker = _wait_job(run, end, session_id, refresh_write,
                        lambda: held["record"]["_job"]["state"] in FINAL_JOB_STATES + ("blocked",))
    record = held["record"]
    view = job_view(record, run)
    _show_blocked(run, view, blocker)
    run.notices.extend(record["_job"].get("notices") or [])
    if view["state"] not in FINAL_JOB_STATES:
        view["poll_after_ms"] = POLL_AFTER_MS
    return Result(view, [".receipt.notes", ".receipt.target.label", ".receipt.observed", ".result"])


# ------------------------------------------------------------------ standard writes


class _Lifecycle(WriteSpec):
    """A standard write whose preview the runtime builds itself."""

    action = ""

    def preview(self) -> dict[str, Any]:
        return {"action": self.action, "targets": self.targets(), "changes": self.changes(), "can_apply": True}

    def targets(self) -> list[dict[str, Any]]:
        return []

    def changes(self) -> list[dict[str, Any]]:
        return []

    def facts_result(self, facts: dict[str, Any]) -> Any:
        return {}

    def result(self, facts: dict[str, Any], verification: Verification) -> Any:
        return self.facts_result(facts)


class SessionOpen(_Lifecycle):
    def prepare(self) -> None:
        self.where = Where("session")
        path, kind = self.call.values.get("--doc"), self.call.values.get("--kind")
        self.bound = {"inputs": {"path": path, "kind": kind}}
        self.action = f"Start a new instance of {self.run.tool.application}" + (f" for {kind} documents" if kind else "") + (
            f" and open {path}." if path else ".")

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {"operation_id": operation_id}
        for flag, key in (("--doc", "path"), ("--kind", "kind")):
            if self.call.values.get(flag):
                request[key] = self.call.values[flag]
        return dispatch(self.run, "open_session", request)

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        sid = (facts.get("session") or {}).get("id")
        self.run._sessions = None
        found = [s for s in self.run.sessions()["items"] if s["id"] == sid]
        if found:
            return Verification("passed", {"session": {"id": sid, "state": found[0]["state"]}})
        return Verification("failed", {"session": {"id": sid, "exists": False}}, ["exists@session"])

    def facts_result(self, facts: dict[str, Any]) -> Any:
        return facts.get("session", {})


class SessionClose(_Lifecycle):
    def prepare(self) -> None:
        run = self.run
        session = run.session(self.call.values.get("--session"))
        self.where = Where("session", session=session)
        self.bound = {"inputs": {"discard": bool(self.call.values.get("--discard"))}}
        if not session.get("owned"):
            self.conditions.append("close_unowned_session")
        # dirty null means the application cannot tell; such a document may hold unsaved work (DCLI-210).
        dirty = [d for d in session.get("documents", []) if d.get("dirty") is not False]
        if self.call.values.get("--discard"):
            self.conditions.append("discard_unsaved_changes")
        elif dirty:
            raise DriverError("E_CONFLICT", f"Session {session['id']} has or may have unsaved changes in {len(dirty)} documents; nothing was closed.",
                              conflict="unsaved_changes",
                              next_action=f"Save them with `{run.tool.name} doc save`, or close with --discard if the user asked to lose them.")
        run.gate_session(session)
        self.action = f"Close session {session['id']}" + (" and discard its unsaved changes." if self.call.values.get("--discard") else ".")

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        return dispatch(self.run, "close_session", {"operation_id": operation_id, "session": self.where.session["id"],
                                                     "discard": bool(self.call.values.get("--discard"))})

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        sid = self.where.session["id"]
        self.run._sessions = None
        try:
            alive = [s for s in self.run.sessions()["items"] if s["id"] == sid]
        except DriverError as exc:
            return Verification("incomplete", notes=[f"The session list could not be read: {exc.message}"])
        if alive:
            return Verification("failed", {"session": {"id": sid, "state": alive[0]["state"]}}, ["closed@session"])
        drop_deferred(self.run, sid, None, "The session was closed without a save; the change was not verified.")
        return Verification("passed", {"session": {"id": sid, "state": "closed"}})

    def facts_result(self, facts: dict[str, Any]) -> Any:
        return {"id": self.where.session["id"], "state": "closed"}


class _OnDocument(_Lifecycle):
    """A standard write on one document of one session."""

    saving = False

    def prepare(self) -> None:
        run = self.run
        self.where = run.where(self.call.command)
        if self.where.session is not None and self.where.session["state"] != "ready":
            run.gate_session(self.where.session)  # a blocked or busy instance says so first (DCLI-204)
        if self.where.document is None:
            raise DriverError("E_NOT_FOUND", "No document matches; name it with --doc.")
        self.bound = {"inputs": self.inputs(), "document": self.where.document["id"]}
        self.gates(saving=self.saving)

    def inputs(self) -> dict[str, Any]:
        return {}

    def doc_target(self) -> dict[str, Any]:
        return {"key": self.run.doc_key(self.where.document), "kind": "document", "label": self.where.document.get("path") or self.where.document["id"]}

    def request(self, operation_id: str) -> dict[str, Any]:
        return {"operation_id": operation_id, "session": self.where.session["id"], "document": self.where.document["id"]}

    def facts_result(self, facts: dict[str, Any]) -> Any:
        return facts.get("document", {"id": self.where.document["id"]})

    def untrusted_result(self) -> list[str]:
        return [".result.path"]


class DocCreate(_Lifecycle):
    """A new document: untitled in an instance, or at --path, from --template when given (DCLI-212)."""

    def prepare(self) -> None:
        import os

        run = self.run
        values = self.call.values
        self.path, self.template, kind = values.get("--path"), values.get("--template"), values.get("--kind")
        file_mode = "file" in run.tool.modes and (values.get("--session") is None or "session" not in run.tool.modes)
        if file_mode:
            if self.path is None:
                raise usage("doc create in file mode needs --path: the file to create.", "--path")
            self.where = Where("file")
        else:
            session = run.session(values.get("--session"))
            self.where = Where("session", session=session)
        self.exists = bool(self.path) and os.path.exists(self.path)
        if self.exists and file_mode and "session" in run.tool.modes:
            holders = run.holders(self.path)
            if holders:
                raise DriverError("E_CONFLICT", f"Session {holders[0]['id']} has {self.path} open; creating it again would undo its changes.",
                                  conflict="file_open_in_session",
                                  next_action="Choose another path, or close the document there first.")
        if self.exists:
            self.conditions.append("target_exists")
        inputs: dict[str, Any] = {"kind": kind, "path": self.path}
        if self.template:
            from .runtime import _file_digest

            digest = _file_digest(self.template, "--template")
            inputs["template"] = {"path": self.template, **digest}
        self.bound = {"inputs": inputs}
        if self.where.session is not None:
            run.gate_session(self.where.session)
        where = f"in session {self.where.session['id']}" if self.where.session else "as a file"
        self.action = (f"Create the document {self.path} {where}." if self.path else f"Create an untitled document {where}.") + (
            f" Start from the template {self.template}." if self.template else "")

    def targets(self) -> list[dict[str, Any]]:
        return [{"key": file_key(self.path), "kind": "document", "label": self.path}] if self.path else []

    def changes(self) -> list[dict[str, Any]]:
        return [{"target": file_key(self.path), "field": "exists", "before": self.exists, "after": True}] if self.path else []

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {"operation_id": operation_id}
        if self.where.session is not None:
            request["session"] = self.where.session["id"]
        for flag, key in (("--kind", "kind"), ("--path", "path"), ("--template", "template")):
            if self.call.values.get(flag):
                request[key] = self.call.values[flag]
        if self.path:
            request["overwrite"] = self.exists
        return dispatch(self.run, "create_doc", request)

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        parts = []
        if self.where.session is not None:
            doc_id = (facts.get("document") or {}).get("id")
            docs = self.run.documents(self.where.session["id"])
            if any(d["id"] == doc_id for d in docs):
                parts.append(Verification("passed", {"document": {"id": doc_id, "exists": True}}))
            else:
                parts.append(Verification("failed", {"document": {"id": doc_id, "exists": False}}, ["exists@document"]))
        if self.path:
            # With --path the file exists once the call returns, written by the tool or the application (DCLI-212).
            parts.append(verify_files(facts, changes))
        return combine(parts) if parts else Verification("incomplete", notes=["Nothing could be checked."])

    def facts_result(self, facts: dict[str, Any]) -> Any:
        out = dict(facts.get("document", {}))
        if facts.get("files"):
            out["files"] = facts["files"]
        return out

    def untrusted_result(self) -> list[str]:
        return [".result.path", ".result.files[*].path"]


class DocOpen(_Lifecycle):
    def prepare(self) -> None:
        run = self.run
        session = run.session(self.call.values.get("--session"))
        self.where = Where("session", session=session)
        self.path = self.call.values["--doc"]
        self.bound = {"inputs": {"path": self.path}}
        run.gate_session(session)
        self.action = f"Open {self.path} in session {session['id']}."

    def targets(self) -> list[dict[str, Any]]:
        return [{"key": file_key(self.path), "kind": "document", "label": self.path}]

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        return dispatch(self.run, "open_doc", {"operation_id": operation_id, "session": self.where.session["id"], "path": self.path})

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        docs = self.run.documents(self.where.session["id"])
        key = file_key(self.path)
        if any(d.get("path") and file_key(d["path"]) == key for d in docs):
            return Verification("passed", {key: {"open": True}})
        return Verification("failed", {key: {"open": False}}, [f"open@{key}"])

    def facts_result(self, facts: dict[str, Any]) -> Any:
        return facts.get("document", {})


class DocSave(_OnDocument):
    """``doc save``: the preview lists every change since the last save (DCLI-058, DCLI-277)."""

    saving = True

    def preview(self) -> dict[str, Any]:
        run = self.run
        request = {"command": "doc save", "inputs": {}, "session": self.where.session["id"], "document": self.where.document["id"]}
        pv = dict(run.capability("preview", request, READ_DEADLINE_MS))
        key = run.doc_key(self.where.document)
        pv["targets"] = [self.doc_target()]
        external = list(pv.get("external_changes", []))
        if not external and self._changed_outside(key):
            external = [{"target": key, "before": None, "after": None, "origin": "external"}]
        pv["external_changes"] = external
        blocked = [f"Operation {e['operation_id']} on this document has an unknown outcome; settle it with "
                   f"`{run.tool.name} operation resolve --id {e['operation_id']}`."
                   for e in run.locks.holders([key]) if e.get("holder") == "unknown"]
        if blocked:
            pv["can_apply"] = False
            pv["blocked_by"] = list(pv.get("blocked_by", [])) + blocked
        self.action = pv.get("action") or f"Save {self.where.document.get('path')}."
        return pv

    def _changed_outside(self, key: str) -> bool:
        """The revision moved since the tool's last write, with no external change listed."""
        doc = self.where.document
        for record in self.run.records.all():
            receipt = record.get("receipt") or {}
            if receipt.get("document", {}).get("id") == doc["id"] and record.get("state") == "completed":
                return receipt["document"].get("revision") != doc["revision"]
        return False

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        return dispatch(self.run, "save_doc", self.request(operation_id))

    def stored_request(self, child: int | None = None) -> dict[str, Any] | None:
        return {"command": "doc save", "inputs": {}, "session": self.where.session["id"], "document": self.where.document["id"]}

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        """The saved file is read again and the changed fields are found in it, with the
        changes earlier writes left for the save to verify (DCLI-057)."""
        deferred = self._deferred()
        fields = [c for c in changes if c.get("field")]
        expected: dict[tuple[str, str], dict[str, Any]] = {}
        for record in deferred:
            for change in record.get("_changes", []):
                if change.get("field"):
                    expected[(change["target"], change["field"])] = change  # a later write wins
        known = {(c["target"], c["field"]) for c in fields}
        fields += [c for k, c in expected.items() if k not in known]
        verification = self._verify_fields(operation_id, fields)
        self._settle_deferred(deferred, expected, verification, operation_id)
        return verification

    def probe(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        """The same read-back as verify, without settling the deferred records."""
        fields = [c for c in changes if c.get("field")]
        known = {(c["target"], c["field"]) for c in fields}
        for record in self._deferred():
            for change in record.get("_changes", []):
                if change.get("field") and (change["target"], change["field"]) not in known:
                    fields.append(change)
                    known.add((change["target"], change["field"]))
        return self._verify_fields(operation_id, fields)

    def _deferred(self) -> list[dict[str, Any]]:
        """The writes on this document whose verification waits for a save, oldest first."""
        return deferred_records(self.run, self.where.session["id"], self.where.document["id"])

    def _settle_deferred(self, deferred: list[dict[str, Any]], expected: dict[tuple[str, str], dict[str, Any]],
                         verification: Verification, save_id: str) -> None:
        bad = set(verification.mismatched)
        for record in deferred:
            receipt = record["receipt"]
            own = [c for c in record.get("_changes", []) if c.get("field")]
            keys = {f"{c['field']}@{c['target']}" for c in own}
            superseded = [c for c in own if expected.get((c["target"], c["field"])) is not c]
            if keys & bad:
                status = "failed"
            elif verification.status in ("passed", "failed") and not superseded:
                status = "passed"
            else:
                status = "incomplete"
            receipt["outcome"]["verification"] = status
            receipt["mismatched"] = sorted(keys & bad)
            receipt.setdefault("notes", []).append(
                f"doc save {save_id} verified this change: {status}." if not superseded else
                f"doc save {save_id} could not verify every field: a later write changed it again.")
            self.run.records.save(record)

    def _verify_fields(self, operation_id: str, fields: list[dict[str, Any]]) -> Verification:
        if not fields:
            # Nothing to find in the file: check instead that the document is saved and clean.
            docs = self.run.documents(self.where.session["id"])
            doc = next((d for d in docs if d.get("id") == self.where.document["id"]), None)
            observed = {"document": {"id": self.where.document["id"], "dirty": (doc or {}).get("dirty"),
                                     "disk": (doc or {}).get("disk")}}
            if doc is not None and doc.get("dirty") is False and doc.get("disk", "in_sync") == "in_sync":
                return Verification("passed", observed, notes=["The save held no field change; the document is clean."])
            return Verification("incomplete", observed, notes=["The document is not reported clean after the save."])
        # The document's `saved` says the save reached the file on disk: true when the fields
        # were read from that file, or when the adapter read them from the instance and saw
        # the file change (DCLI-057).
        fields = fields + [{"target": self.run.doc_key(self.where.document), "field": "saved", "after": True}]
        request = {"command": "doc save", "inputs": {}, "session": self.where.session["id"], "document": self.where.document["id"],
                   "operation_id": operation_id, "targets": sorted({c["target"] for c in fields}),
                   "fields": sorted({c["field"] for c in fields})}
        return read_back(self.run, request, fields, {})


def deferred_records(run: Run, session: str, doc_id: str | None) -> list[dict[str, Any]]:
    """The writes in a session, on one document or on all, whose verification waits for a save."""
    out = [r for r in run.records.all()
           if (r.get("receipt") or {}).get("outcome", {}).get("verification") == "deferred"
           and (r.get("receipt") or {}).get("session", {}).get("id") == session
           and (doc_id is None or (r.get("receipt") or {}).get("document", {}).get("id") == doc_id)]
    return sorted(out, key=lambda r: r.get("planned_at", ""))


def drop_deferred(run: Run, session: str, doc_id: str | None, why: str) -> None:
    """Changes discarded before a save can never be verified: their records say so (DCLI-057)."""
    for record in deferred_records(run, session, doc_id):
        record["receipt"]["outcome"]["verification"] = "not_run"
        record["receipt"].setdefault("notes", []).append(why)
        run.records.save(record)


class DocSaveAs(_OnDocument):
    def prepare(self) -> None:
        super().prepare()
        self.out = self.call.values["--output"]
        import os

        self.exists = os.path.exists(self.out)
        if self.exists:
            self.conditions.append("target_exists")
        self.bound["inputs"] = {"output": self.out}
        self.action = f"Save document {self.where.document['id']} as {self.out}."

    def targets(self) -> list[dict[str, Any]]:
        return [{"key": file_key(self.out), "kind": "file", "label": self.out}]

    def changes(self) -> list[dict[str, Any]]:
        return [{"target": file_key(self.out), "field": "exists", "before": self.exists, "after": True}]

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        return dispatch(self.run, "save_doc_as", {**self.request(operation_id), "path": self.out, "overwrite": self.exists})

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        return verify_files(facts, changes)

    def facts_result(self, facts: dict[str, Any]) -> Any:
        return {"document": facts.get("document", {}), "files": facts.get("files", [])}

    def untrusted_result(self) -> list[str]:
        return [".result.document.path", ".result.files[*].path"]


class DocClose(_OnDocument):
    def prepare(self) -> None:
        super().prepare()
        doc = self.where.document
        discard = bool(self.call.values.get("--discard"))
        self.bound["inputs"] = {"discard": discard}
        if discard:
            self.conditions.append("discard_unsaved_changes")
        elif doc.get("dirty") is not False:
            state = "has" if doc.get("dirty") else "may have"
            raise DriverError("E_CONFLICT", f"Document {doc['id']} {state} unsaved changes; nothing was closed.", conflict="unsaved_changes",
                              next_action=f"Save it with `{self.run.tool.name} doc save --doc {doc['id']}`, or close with --discard if the user asked to lose the changes.")
        self.action = f"Close document {doc['id']}" + (" and discard its unsaved changes." if discard else ".")

    def gates(self, saving: bool = False) -> None:
        if self.where.session is not None:
            self.run.gate_session(self.where.session)

    def targets(self) -> list[dict[str, Any]]:
        return [self.doc_target()]

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        return dispatch(self.run, "close_doc", {**self.request(operation_id), "discard": bool(self.call.values.get("--discard"))})

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        doc_id = self.where.document["id"]
        if any(d["id"] == doc_id for d in self.run.documents(self.where.session["id"])):
            return Verification("failed", {"document": {"id": doc_id, "open": True}}, ["closed@document"])
        drop_deferred(self.run, self.where.session["id"], doc_id, "The document was closed without a save; the change was not verified.")
        return Verification("passed", {"document": {"id": doc_id, "open": False}})

    def facts_result(self, facts: dict[str, Any]) -> Any:
        return {"id": self.where.document["id"], "closed": True}

    def refresh(self) -> None:
        self.run._sessions = None
        WriteSpec.refresh(self)


class DocUndo(_OnDocument):
    """Undo this tool's most recent change; read-back checks the fields are back (DCLI-067)."""

    def prepare(self) -> None:
        super().prepare()
        doc = self.where.document
        records = self.run.records.all()
        states = {r["operation_id"]: r.get("state") for r in records}
        self.pending = [r["operation_id"] for r in records if r["command"] == "doc undo" and r.get("state") in ("dispatched", "unknown")
                        and (r.get("_call") or {}).get("document") == doc["id"]]
        candidates = [r for r in records
                      if (r.get("receipt") or {}).get("document", {}).get("id") == doc["id"] and r.get("state") == "completed"
                      and r.get("_request") and r["command"] not in ("doc undo", "doc save")
                      and (not r.get("_undone_by") or states.get(r["_undone_by"]) == "failed")]
        self.last = max(candidates, key=lambda r: r.get("_finished", 0), default=None)
        self.action = f"Undo {self.last['command']} ({self.last['operation_id']}) on document {doc['id']}." if self.last else "Undo."

    def targets(self) -> list[dict[str, Any]]:
        return [self.doc_target()]

    def changes(self) -> list[dict[str, Any]]:
        if not self.last:
            return []
        return [{"target": c["target"], "field": c["field"], "before": c.get("after"), "after": c.get("before")}
                for c in self.last["preview"].get("changes", []) if c.get("origin", "tool") == "tool" and "field" in c]

    def preview(self) -> dict[str, Any]:
        pv = super().preview()
        if self.pending:
            pv.update(can_apply=False, blocked_by=[f"Undo {op} has an unknown outcome; settle it first with "
                                                   f"`{self.run.tool.name} operation resolve --id {op}`." for op in self.pending])
        elif not self.last:
            pv.update(can_apply=False, blocked_by=["This tool has no change on this document to undo."])
        return pv

    def stored_request(self, child: int | None = None) -> dict[str, Any] | None:
        return self.last.get("_request") if self.last else None

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        # Mark the change as being undone before the undo is sent, so a second undo never
        # picks it again, whatever this one's outcome (DCLI-067).
        self.last["_undone_by"] = operation_id
        self.run.records.save(self.last)
        return dispatch(self.run, "undo", self.request(operation_id))

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        if not self.last or not changes:
            return Verification("incomplete", notes=["There was no recorded change to compare."])
        request = {**self.last["_request"], "operation_id": operation_id, "targets": sorted({c["target"] for c in changes}),
                   "fields": sorted({c["field"] for c in changes})}
        return read_back(self.run, request, changes, self.last.get("_tolerances", {}))


def job_cancel(run: Run) -> Result:
    """A read job is stopped by ending its worker, which changes nothing; any other job is
    asked to cancel through the application, as a halt command (DCLI-221)."""
    from .jobs import FINAL, ReadJobs, read_view, stop_read

    job = ReadJobs(run).load(run.call.values["--job"])
    if job is None:
        return Engine(run).execute(JobCancel(run, run.call.command))
    if job["state"] in FINAL:
        raise DriverError("E_VALIDATION", f"Job {job['id']} has already ended ({job['state']}); there is nothing to cancel.")
    return Result(read_view(stop_read(run, job)), [".message"])


class JobCancel(_Lifecycle):
    """``job cancel``: a halt command; its target is the job's target (DCLI-221, DSEC-018)."""

    def prepare(self) -> None:
        run = self.run
        self.job_record = _job_record(run, self.call.values["--job"])
        job = self.job_record["_job"]
        if job["state"] in FINAL_JOB_STATES:
            raise DriverError("E_VALIDATION", f"Job {job['id']} has already ended ({job['state']}); there is nothing to cancel.")
        if job.get("worker"):
            # The runtime's worker is applying the change; stopping it midway would leave the
            # outcome unknown, so the job runs to its end (DCLI-221).
            raise DriverError("E_UNSUPPORTED", f"Job {job['id']} is a write the tool runs itself; it cannot be stopped midway. "
                                               "Wait for it to end.", next_action=f"Run `{run.tool.name} job get --job {job['id']} --wait 60s`.")
        info = self.job_record.get("_call") or {}
        session = run.session(info["session"]) if info.get("session") else None
        device = run.device(info["device"]) if info.get("device") else None
        self.where = Where(info.get("mode", "session"), session=session, device=device)
        if session is not None:
            run.gate_session(session, halt=True)
        if device is not None:
            run.gate_device(device, halt=True)
        self.bound = {"inputs": {"job": job["id"]}}
        self.action = f"Ask the application to cancel job {job['id']}."

    def targets(self) -> list[dict[str, Any]]:
        target = self.job_record.get("target")
        if target is None:
            return []
        return [dict(t) for t in target] if isinstance(target, list) else [dict(target)]

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        info = self.job_record.get("_call") or {}
        request: dict[str, Any] = {"operation_id": operation_id, "job": self.job_record["_job"]["id"]}
        if info.get("session"):
            request["session"] = info["session"]
        facts = dispatch(self.run, "cancel", request)
        with self.run.mutex.hold():
            latest = self.run.records.load(self.job_record["operation_id"]) or self.job_record
            latest["_job"]["cancel_requested"] = True
            self.run.records.save(latest)
        return facts

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        return Verification("passed", {"job": {"id": self.job_record["_job"]["id"], "cancel_requested": True}})

    def facts_result(self, facts: dict[str, Any]) -> Any:
        record = poll_job(self.run, self.run.records.load(self.job_record["operation_id"]) or self.job_record)
        return job_view(record)


class PluginInstall(_Lifecycle):
    """``plugin install``: checks the manifest, then installs (DSEC-050, DSEC-051)."""

    def prepare(self) -> None:
        run = self.run
        self.where = Where(run.tool.modes[0])
        manifest = self.check_manifest()
        self.version = (manifest or {}).get("version")
        self.bound = {"inputs": {"version": self.version}}
        self.action = f"Install the host component{' ' + self.version if self.version else ''}; running instances keep the version they loaded."

    def check_manifest(self) -> dict[str, Any] | None:
        path = self.run.tool.plugin_manifest
        if path is None:
            return None
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DriverError("E_INTEGRITY", f"The host component manifest cannot be read: {exc}. Nothing was installed.") from None
        for name, digest in manifest.get("files", {}).items():
            try:
                actual = hashlib.sha256((path.parent / name).read_bytes()).hexdigest()
            except OSError:
                actual = None
            if actual != digest:
                raise DriverError("E_INTEGRITY", f"The host component file {name} does not match its manifest. Nothing was installed.")
        theirs = str(manifest.get("protocol_version", ""))
        ours = self.run.contract["host_protocol"]["version"]
        if theirs.split(".")[0] != ours.split(".")[0]:
            raise DriverError("E_PROTOCOL", f"The host component speaks protocol {theirs} and this CLI {ours}. Nothing was installed.")
        return manifest

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {"operation_id": operation_id, "state_dir": str(self.run.state.ensure())}
        if self.version:
            request["version"] = self.version
        self.facts = dispatch(self.run, "plugin_install", request)
        return self.facts

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        status = self.run.capability("plugin_status", {})
        wanted = facts.get("version") or self.version
        observed = {"plugin": {"version_on_disk": status.get("version_on_disk")}}
        if wanted and status.get("version_on_disk") != wanted:
            return Verification("failed", observed, ["version_on_disk@plugin"])
        return Verification("passed", observed)

    def facts_result(self, facts: dict[str, Any]) -> Any:
        return self.run.capability("plugin_status", {})


class PluginRemove(PluginInstall):
    def prepare(self) -> None:
        self.where = Where(self.run.tool.modes[0])
        self.bound = {"inputs": {}}
        self.action = "Remove the host component; running instances keep the version they loaded."

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        return dispatch(self.run, "plugin_remove", {"operation_id": operation_id})

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        status = self.run.capability("plugin_status", {})
        observed = {"plugin": {"version_on_disk": status.get("version_on_disk")}}
        if status.get("version_on_disk"):
            return Verification("failed", observed, ["version_on_disk@plugin"])
        return Verification("passed", observed)




# The standard writes a tool may register as long jobs (contract ``jobs.standard_long``, DCLI-065).
LONG_SPECS: dict[str, type[WriteSpec]] = {
    "session open": SessionOpen, "session close": SessionClose, "doc create": DocCreate, "doc open": DocOpen,
    "doc save": DocSave, "doc save-as": DocSaveAs, "doc close": DocClose,
}
