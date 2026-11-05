"""Long jobs (DCLI-065): work that can take longer than one call runs in a worker process.

``start`` writes a job file into the adapter store and starts a detached worker that runs the
handler's ``run``. The worker reports progress and the final facts in the same file;
``status`` reads it and ``cancel`` asks the worker to stop between two steps. A worker that is
gone without a final state leaves the job ``unknown``, which ``operation resolve`` settles.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from .._driver.records import pid_alive
from .._driver.state import AdapterStore

WORKER_ENV = "XPEDITION_CLI_WORKER"
FINAL = ("completed", "failed", "partial", "unknown", "cancelled")
CANCEL_WAIT_S = 20.0


def store_dir(store: AdapterStore) -> Path:
    """The folder of the adapter store; the worker writes its job file there."""
    return store._file("probe").parent  # noqa: SLF001 - the runtime keeps the folder private


def job_file(store: AdapterStore, job_id: str) -> Path:
    return store_dir(store) / f"{job_id}.json"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def worker_command(path: Path) -> tuple[list[str], dict[str, str]]:
    env = dict(os.environ)
    env[WORKER_ENV] = str(path)
    if getattr(sys, "frozen", False):
        return [sys.executable], env  # the native program checks WORKER_ENV before the CLI starts
    return [sys.executable, "-m", "xpedition_cli.adapter.worker"], env


def start(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    """Start the worker for one write; return the apply facts that name the job."""
    job_id = "job-" + uuid.uuid4().hex[:12]
    path = job_file(store, job_id)
    record = {"id": job_id, "state": "running", "request": request, "started_at": utc_now(),
              "cancel_requested": False, "progress": None, "pid": None}
    write(path, record)
    command, env = worker_command(path)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    log = path.with_suffix(".log").open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                creationflags=flags, close_fds=True)
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise AdapterError("E_NATIVE_FAILURE", f"The worker for the job could not start: {exc}",
                           dispatch_state="not_started") from None
    finally:
        log.close()
    record["pid"] = proc.pid
    current = read(path) or record
    current["pid"] = proc.pid
    write(path, current)
    return {"dispatch_state": "dispatched", "change": "not_applicable", "delivery": "not_applicable",
            "persistence": "not_applicable", "job": {"id": job_id, "state": "running"}}


def running_on(store: AdapterStore, session: str) -> str | None:
    """The ID of a job still running on a session, if any; that session is busy."""
    folder = store_dir(store)
    for path in folder.glob("job-*.json"):
        record = read(path)
        if record and record.get("state") == "running" and (record.get("request") or {}).get("session") == session:
            if record.get("pid") is None or pid_alive(int(record["pid"])):
                return str(record["id"])
    return None


def status(store: AdapterStore, job_id: str) -> dict[str, Any]:
    path = job_file(store, job_id)
    record = read(path)
    if record is None:
        raise AdapterError("E_NOT_FOUND", f"There is no job {job_id}.")
    state = record.get("state", "unknown")
    if state not in FINAL and record.get("pid") and not pid_alive(int(record["pid"])):
        state = "unknown"
        record.update(state=state, native_message="The worker ended without reporting a result.")
        write(path, record)
    out: dict[str, Any] = {"state": state}
    if record.get("progress"):
        out["progress"] = record["progress"]
    for name in ("change", "delivery", "persistence", "result", "files", "native_message", "action"):
        if record.get(name) is not None:
            out[name] = record[name]
    return out


def cancel(store: AdapterStore, job_id: str) -> dict[str, Any]:
    path = job_file(store, job_id)
    record = read(path)
    if record is None:
        raise AdapterError("E_NOT_FOUND", f"There is no job {job_id}.", dispatch_state="not_started")
    if record.get("state") in FINAL:
        return {"dispatch_state": "not_started", "change": "not_applied", "delivery": "not_applicable",
                "persistence": "not_applicable"}
    record["cancel_requested"] = True
    write(path, record)
    end = time.monotonic() + CANCEL_WAIT_S
    while time.monotonic() < end:
        current = read(path) or {}
        if current.get("state") in FINAL:
            break
        time.sleep(0.5)
    return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
            "persistence": "not_applicable"}


class Cancelled(Exception):
    """The person asked to stop the job; the worker stops between two steps."""


class Progress:
    """What a handler's ``run`` calls to report progress and to notice a cancel request."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._last = 0.0

    def __call__(self, done: float, total: float | None, unit: str) -> None:
        record = read(self.path) or {}
        if record.get("cancel_requested"):
            raise Cancelled()
        now = time.monotonic()
        if now - self._last >= 1.0 or (total is not None and done >= total):
            record["progress"] = {"done": done, "total": total, "unit": unit}
            write(self.path, record)
            self._last = now

    def blocked(self, action: str, message: str) -> None:
        """A dialog or a sign-in waits for the person; the job shows ``blocked`` until it clears."""
        record = read(self.path) or {}
        record.update(state="blocked", action=action, native_message=message)
        write(self.path, record)

    def unblocked(self) -> None:
        record = read(self.path) or {}
        if record.get("state") == "blocked":
            record.update(state="running", action=None)
            write(self.path, record)
