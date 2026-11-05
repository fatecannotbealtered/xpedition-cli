"""The driver standard commands (DCLI-200 to DCLI-240) and the domain read.

Sessions, documents, long jobs, devices and the host component, built on the adapter
capabilities. A capability the adapter lacks gives ``E_UNSUPPORTED``. The observe commands
report states as data and never fail because of them (DCLI-204).
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

from .errors import DriverError, usage
from .output import Result, page
from .records import utc_now
from .registry import Command, Flag
from .runtime import READ_DEADLINE_MS, SESSION_DEADLINE_MS, Run, Where, parallel
from .targets import file_key
from .writes import Engine, Verification, WriteSpec, dispatch, read_back, record_state, verify_files

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
        ("session open", "Start a new instance, optionally opening a document.", [Flag("--doc", "path", "A document to open in the new instance.")],
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
        ("doc create", "Create an untitled document in an instance.", [_session(), Flag("--kind", "string", "The kind of document, when the application has several.")],
         lambda run: Engine(run).execute(DocCreate(run, run.call.command)), "receipt.schema.json", ["create_doc"],
         ["doc create --session app-1234-5f3a"], "document", [], None),
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
         lambda run: Engine(run).execute(JobCancel(run, run.call.command)), "receipt.schema.json", ["cancel", "job_status"],
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
    if "artifact" in command.effects and any(f.output for f in command.flags):
        command.tier_when = [{"when": "target_exists", "tier": "two_step"}]
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
    result = run.capability("read", request).get("result")
    untrusted = [f".{f}" for f in command.untrusted_fields] + [f".items[*].{f}" for f in command.untrusted_fields]
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
        results = parallel(ready, lambda s: run.documents(s["id"], None, SESSION_DEADLINE_MS), SESSION_DEADLINE_MS / 1000 + 0.5)
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


def job_view(record: dict[str, Any]) -> dict[str, Any]:
    job = record["_job"]
    out: dict[str, Any] = {"id": job["id"], "operation_id": record["operation_id"], "state": job["state"],
                           "cancel_requested": job.get("cancel_requested", False)}
    if job.get("progress"):
        out["progress"] = job["progress"]
    out["started_at"] = job["started_at"]
    if job["state"] in FINAL_JOB_STATES:
        out["finished_at"] = job.get("finished_at", record.get("finished_at", utc_now()))
        out["receipt"] = record.get("receipt")
        out["result"] = job.get("result", {})
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


def _settle_job(run: Run, record: dict[str, Any], status: dict[str, Any]) -> None:
    """Complete the job's operation record with a full receipt (DCLI-065)."""
    from .writes import Engine as _Engine

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
    if execution == "completed" and (change in ("applied", "partial") or persistence == "saved" or delivery == "delivered"):
        if status.get("files"):
            verification = verify_files(status, [])
        elif record.get("_request"):
            changes = record.get("_changes", [c for c in record["preview"].get("changes", []) if c.get("origin", "tool") == "tool"])
            request = {**record["_request"], "operation_id": record["operation_id"],
                       "targets": sorted({c["target"] for c in changes}) or None,
                       "fields": sorted({c["field"] for c in changes if "field" in c}) or None}
            request = {k: v for k, v in request.items() if v is not None}
            verification = read_back(run, request, changes, record.get("_tolerances", {}))
        else:
            verification = Verification("incomplete", notes=["The job's outputs cannot be read back."])
    receipt = dict(record.get("receipt") or {})
    receipt["outcome"] = {"execution": execution, "change": change, "delivery": delivery, "persistence": persistence,
                          "verification": verification.status}
    receipt["observed"], receipt["mismatched"] = verification.observed, verification.mismatched
    receipt["notes"] = [f"Long job {job['id']} ended {state}."] + verification.notes
    if isinstance(status.get("native_message"), str):
        receipt["notes"].append(f"The application said: {status['native_message']}")
    record["receipt"] = receipt
    cause = "E_INTERRUPTED" if state == "cancelled" else None
    ok, code = _Engine(run).derive(receipt, cause)
    job.update(ok=ok, code=code, finished_at=utc_now(), result=status.get("result", {}))
    record["state"] = record_state(receipt, code)
    record["finished_at"] = job["finished_at"]
    if record["state"] == "unknown":
        run.locks.hold_as(record["operation_id"], "unknown", record.get("_keys", []))
    else:
        run.locks.release(record["operation_id"])


def job_list(run: Run) -> Result:
    state = run.call.values.get("--state", "all")
    records = [r for r in run.records.all() if "_job" in r]
    for record in records:
        if record["_job"]["state"] not in FINAL_JOB_STATES and run.backend.has("job_status"):
            try:
                poll_job(run, record)
            except DriverError:
                pass  # the job keeps its last known state
    items = [job_view(r) for r in records if state == "all" or r["_job"]["state"] == state]
    return _page(run, items, [])


def job_get(run: Run) -> Result:
    call = run.call
    wait = call.values.get("--wait", 0.0)
    if wait > run.contract["jobs"]["max_wait_seconds"]:
        raise usage(f"--wait is at most {run.contract['jobs']['max_wait_seconds']}s.", "--wait")
    record = _job_record(run, call.values["--job"])
    end = time.monotonic() + wait
    while True:
        try:
            record = poll_job(run, record)
        except DriverError as exc:
            # An observe command reports the state as data (DCLI-204): the last known one.
            run.notices.append({"type": "job_unanswered", "severity": "warning",
                                "message": f"The application did not report job {record['_job']['id']} ({exc.code}); this is its last known state."})
            break
        if record["_job"]["state"] in FINAL_JOB_STATES or time.monotonic() >= end:
            break
        time.sleep(min(0.5, max(0.0, end - time.monotonic())))
    view = job_view(record)
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
        path = self.call.values.get("--doc")
        self.bound = {"inputs": {"path": path}}
        self.echo = {"path": path} if path else {}
        self.action = f"Start a new instance of {self.run.tool.application}" + (f" and open {path}." if path else ".")

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {"operation_id": operation_id}
        if self.call.values.get("--doc"):
            request["path"] = self.call.values["--doc"]
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
        self.echo = dict(self.bound["inputs"])
        if not session.get("owned"):
            self.conditions.append("close_unowned_session")
        dirty = [d for d in session.get("documents", []) if d.get("dirty")]
        if self.call.values.get("--discard"):
            self.conditions.append("discard_unsaved_changes")
        elif dirty:
            raise DriverError("E_CONFLICT", f"Session {session['id']} has unsaved changes in {len(dirty)} documents; nothing was closed.",
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
        self.echo = self.bound["inputs"]
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
    def prepare(self) -> None:
        run = self.run
        session = run.session(self.call.values.get("--session"))
        self.where = Where("session", session=session)
        kind = self.call.values.get("--kind")
        self.bound = {"inputs": {"kind": kind}}
        self.echo = {"kind": kind} if kind else {}
        run.gate_session(session)
        self.action = f"Create an untitled document in session {session['id']}."

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        request = {"operation_id": operation_id, "session": self.where.session["id"]}
        if self.call.values.get("--kind"):
            request["kind"] = self.call.values["--kind"]
        return dispatch(self.run, "create_doc", request)

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        doc_id = (facts.get("document") or {}).get("id")
        docs = self.run.documents(self.where.session["id"])
        if any(d["id"] == doc_id for d in docs):
            return Verification("passed", {"document": {"id": doc_id, "exists": True}})
        return Verification("failed", {"document": {"id": doc_id, "exists": False}}, ["exists@document"])

    def facts_result(self, facts: dict[str, Any]) -> Any:
        return facts.get("document", {})


class DocOpen(_Lifecycle):
    def prepare(self) -> None:
        run = self.run
        session = run.session(self.call.values.get("--session"))
        self.where = Where("session", session=session)
        self.path = self.call.values["--doc"]
        self.bound = {"inputs": {"path": self.path}}
        self.echo = {"path": self.path}
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
        """The saved file is read again and the changed fields are found in it (DCLI-057)."""
        fields = [c for c in changes if c.get("field")]
        if not fields:
            # Nothing to find in the file: check instead that the document is saved and clean.
            docs = self.run.documents(self.where.session["id"])
            doc = next((d for d in docs if d.get("id") == self.where.document["id"]), None)
            observed = {"document": {"id": self.where.document["id"], "dirty": (doc or {}).get("dirty"),
                                     "disk": (doc or {}).get("disk")}}
            if doc is not None and doc.get("dirty") is False and doc.get("disk", "in_sync") == "in_sync":
                return Verification("passed", observed, notes=["The save held no field change; the document is clean."])
            return Verification("incomplete", observed, notes=["The document is not reported clean after the save."])
        request = {"command": "doc save", "inputs": {}, "session": self.where.session["id"], "document": self.where.document["id"],
                   "operation_id": operation_id, "targets": sorted({c["target"] for c in fields}),
                   "fields": sorted({c["field"] for c in fields})}
        return read_back(self.run, request, fields, {})


class DocSaveAs(_OnDocument):
    def prepare(self) -> None:
        super().prepare()
        self.out = self.call.values["--output"]
        import os

        self.exists = os.path.exists(self.out)
        if self.exists:
            self.conditions.append("target_exists")
        self.bound["inputs"] = self.echo = {"output": self.out}
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
        self.bound["inputs"] = self.echo = {"discard": discard}
        if discard:
            self.conditions.append("discard_unsaved_changes")
        elif doc.get("dirty"):
            raise DriverError("E_CONFLICT", f"Document {doc['id']} has unsaved changes; nothing was closed.", conflict="unsaved_changes",
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


class JobCancel(_Lifecycle):
    """``job cancel``: a halt command; its target is the job's target (DCLI-221, DSEC-018)."""

    def prepare(self) -> None:
        run = self.run
        self.job_record = _job_record(run, self.call.values["--job"])
        job = self.job_record["_job"]
        if job["state"] in FINAL_JOB_STATES:
            raise DriverError("E_VALIDATION", f"Job {job['id']} has already ended ({job['state']}); there is nothing to cancel.")
        info = self.job_record.get("_call") or {}
        session = run.session(info["session"]) if info.get("session") else None
        device = run.device(info["device"]) if info.get("device") else None
        self.where = Where(info.get("mode", "session"), session=session, device=device)
        if session is not None:
            run.gate_session(session, halt=True)
        if device is not None:
            run.gate_device(device, halt=True)
        self.bound = {"inputs": {"job": job["id"]}}
        self.echo = {"job": job["id"]}
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
        self.job_record["_job"]["cancel_requested"] = True
        self.run.records.save(self.job_record)
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
        self.echo = {"version": self.version} if self.version else {}
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
        self.bound, self.echo = {"inputs": {}}, {}
        self.action = "Remove the host component; running instances keep the version they loaded."

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        return dispatch(self.run, "plugin_remove", {"operation_id": operation_id})

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        status = self.run.capability("plugin_status", {})
        observed = {"plugin": {"version_on_disk": status.get("version_on_disk")}}
        if status.get("version_on_disk"):
            return Verification("failed", observed, ["version_on_disk@plugin"])
        return Verification("passed", observed)


