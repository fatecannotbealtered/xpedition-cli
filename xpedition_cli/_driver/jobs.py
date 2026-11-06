"""Long jobs the runtime runs itself, in a worker process of the same tool (DCLI-065).

A command registered with ``long: true`` does its work outside the call that asks for it:
the call does every check, records the job and starts a worker, then returns the job at once.
The worker is the tool's own program, started with ``job get --job <id>`` and the variable
``<PREFIX>_JOB_WORKER=<id>``. It makes the adapter call with the long deadline, records the
outcome and ends. ``job get`` reads the record; a worker that ended without a result leaves
an unknown outcome for a write and a failed job for a read.

Read jobs are kept under ``jobs/`` in the state directory; they change nothing, so they are
not operations. Write jobs live in their operation record, like a job the application runs.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .records import pid_alive, process_start, utc_now
from .state import read_json, write_json

if TYPE_CHECKING:
    from .runtime import Run

WINDOWS = sys.platform == "win32"
FINAL = ("completed", "failed", "partial", "unknown", "cancelled")


JOB_ID = re.compile(r"job-[0-9a-f]{10}")


def new_job_id() -> str:
    return "job-" + secrets.token_hex(5)


def new_nonce() -> str:
    return secrets.token_hex(8)


def worker_of(run: Run, job_id: str, nonce: str | None) -> bool:
    """Whether this process is the worker started for this job: its variable names the job
    and the nonce the job recorded before the worker started."""
    job, _, given = (run.worker or "").partition(":")
    return bool(nonce) and job == job_id and secrets.compare_digest(given, nonce or "")


def worker_deadline_ms(run: Run) -> int:
    return int(run.contract["jobs"]["worker_max_seconds"]) * 1000


def worker_command(run: Run) -> list[str]:
    """How to start this tool again: the frozen program, ``python -m <package>``, or the script."""
    if run.tool.worker_command is not None:
        return list(run.tool.worker_command)
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    if spec is not None and spec.name:
        name = spec.name[: -len(".__main__")] if spec.name.endswith(".__main__") else spec.name
        return [sys.executable, "-m", name]
    return [sys.executable, os.path.abspath(sys.argv[0])]


def start_worker(run: Run, job_id: str, nonce: str) -> dict[str, int]:
    """Start the worker detached from this call, with no window (DCLI-065)."""
    env = dict(run.env)
    env[f"{run.tool.prefix}_JOB_WORKER"] = f"{job_id}:{nonce}"
    args = worker_command(run) + ["job", "get", "--job", job_id]
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                              "env": env, "close_fds": True}
    if WINDOWS:
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            proc = subprocess.Popen(args, creationflags=flags | 0x01000000, **kwargs)  # CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            proc = subprocess.Popen(args, creationflags=flags, **kwargs)
    else:
        proc = subprocess.Popen(args, start_new_session=True, **kwargs)
    return {"pid": proc.pid, "started": process_start(proc.pid)}


def worker_alive(job: dict[str, Any]) -> bool:
    worker = job.get("worker") or {}
    pid = int(worker.get("pid", 0))
    if not pid_alive(pid):
        return False
    return worker.get("started") is None or process_start(pid) == worker["started"]


class ReadJobs:
    """Read jobs, one JSON file each under ``jobs/``."""

    def __init__(self, run: Run) -> None:
        self.run = run

    def _dir(self) -> Path:
        return self.run.state.sub("jobs")

    def save(self, job: dict[str, Any]) -> None:
        write_json(self._dir() / f"{job['id']}.json", job)

    def load(self, job_id: str) -> dict[str, Any] | None:
        if not JOB_ID.fullmatch(job_id):
            return None  # the ID names a file, so it never holds a path (DSEC-030)
        path = self.run.state.path / "jobs" / f"{job_id}.json"
        if not path.is_file():
            return None
        try:
            data = read_json(path)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def all(self) -> list[dict[str, Any]]:
        folder = self.run.state.path / "jobs"
        out = []
        if folder.is_dir():
            for path in sorted(folder.glob("job-*.json")):
                try:
                    data = read_json(path)
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict):
                    out.append(data)
        return out


def submit_read(run: Run, request: dict[str, Any], untrusted: list[str]) -> dict[str, Any]:
    """Record a read job and start its worker; return the job for the call's result."""
    store = ReadJobs(run)
    job = {"id": new_job_id(), "kind": "read", "command": request["command"], "state": "running",
           "cancel_requested": False, "started_at": utc_now(), "finished_at": None, "_request": request,
           "_untrusted": untrusted, "worker": {"nonce": new_nonce()}}
    store.save(job)  # recorded before the worker starts, so the worker finds itself named
    try:
        started = start_worker(run, job["id"], job["worker"]["nonce"])
    except OSError as exc:
        with run.mutex.hold():
            latest = store.load(job["id"]) or job
            latest.update(state="failed", ok=False, code="E_UNKNOWN", message=f"The worker could not start: {exc}",
                          finished_at=utc_now())
            store.save(latest)
        return latest
    with run.mutex.hold():
        latest = store.load(job["id"]) or job
        latest["worker"] = {**latest.get("worker", {}), **started}
        store.save(latest)
    return latest


def run_read(run: Run, job: dict[str, Any]) -> None:
    """In the worker: claim the job, make the read with the long deadline and record what came back."""
    from .errors import DriverError

    store = ReadJobs(run)
    with run.mutex.hold():
        latest = store.load(job["id"]) or job
        if latest.get("worker", {}).get("claimed") or latest["state"] in FINAL:
            return  # another worker has it: a job runs once
        latest.setdefault("worker", {})["claimed"] = True
        store.save(latest)
    job = latest
    try:
        result = run.capability("read", job["_request"], worker_deadline_ms(run)).get("result")
        if str(job.get("command", "")).split(" ")[-1] == "list" and not (isinstance(result, dict) and isinstance(result.get("items"), list)):
            raise DriverError("E_PROTOCOL", f"{job['command']} is a list, but the adapter returned no items list; "
                                            "a list returns its entries in items.")
        job.update(state="completed", ok=True, code=None, result=result)
    except DriverError as exc:
        job.update(state="failed", ok=False, code=exc.code, message=exc.message)
    kept = [n for n in run.notices if n.get("type") == "application"]
    if kept:
        job["notices"] = kept  # job get shows what the adapter said in the worker (DCLI-260)
    job["finished_at"] = utc_now()
    with run.mutex.hold():
        latest = store.load(job["id"]) or job
        job["worker"] = {**latest.get("worker", {}), **job.get("worker", {})}
        job["cancel_requested"] = latest.get("cancel_requested", False)
        store.save(job)


def settle_dead_read(run: Run, job: dict[str, Any]) -> dict[str, Any]:
    """A read job whose worker ended without a result has failed; nothing was changed."""
    if job["state"] not in FINAL and job.get("worker") and not worker_alive(job):
        latest = ReadJobs(run).load(job["id"]) or job
        if latest["state"] in FINAL:
            return latest
        if latest.get("cancel_requested"):
            latest.update(state="cancelled", ok=False, code="E_INTERRUPTED", finished_at=utc_now())
        else:
            latest.update(state="failed", ok=False, code="E_UNKNOWN", finished_at=utc_now(),
                          message="The worker that ran this job ended without a result.")
        ReadJobs(run).save(latest)
        return latest
    return job


def read_view(job: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": job["id"], "operation_id": None, "state": job["state"],
                           "cancel_requested": job.get("cancel_requested", False), "started_at": job["started_at"]}
    if job["state"] in FINAL:
        out["finished_at"] = job.get("finished_at") or utc_now()
        out["ok"] = bool(job.get("ok"))
        if job.get("code"):
            out["code"] = job["code"]
        if job.get("message"):
            out["message"] = job["message"]
        if job.get("ok"):
            out["result"] = job.get("result")
    return out


def stop_read(run: Run, job: dict[str, Any]) -> dict[str, Any]:
    """Cancel a read job: end its worker. A read changes nothing, so stopping it is safe."""
    worker = job.get("worker") or {}
    pid = int(worker.get("pid", 0))
    job["cancel_requested"] = True
    if pid and worker_alive(job):
        try:
            if WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            else:
                os.kill(pid, 15)
        except OSError:
            pass
    with run.mutex.hold():
        latest = ReadJobs(run).load(job["id"]) or job
        latest["cancel_requested"] = True
        if latest["state"] not in FINAL and not worker_alive(latest):
            latest.update(state="cancelled", ok=False, code="E_INTERRUPTED", finished_at=utc_now())
        ReadJobs(run).save(latest)
    return latest


__all__ = ["FINAL", "ReadJobs", "new_job_id", "read_view", "run_read", "settle_dead_read", "start_worker",
           "stop_read", "submit_read", "worker_alive", "worker_deadline_ms"]
