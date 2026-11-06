"""Help for the commands the runtime runs as long jobs (DCLI-065).

A command registered ``long: true`` returns a job at once; the runtime runs the adapter's
``apply`` in a worker process of this tool, with the long deadline, and ``job get`` reports how
it ended. This module holds what the adapter needs around that:

- ``in_worker``: whether this process is such a worker, so a step may take longer than a call;
- ``running``: while a worker drives an instance, ``sessions`` reports that instance ``busy``,
  a dialog the worker answers itself does not block it, and another worker is refused;
- ``Progress``: what a handler's ``run`` reports steps and dialogs to;
- ``read`` and ``write``: small JSON files, such as the library cache's index.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from .._driver.records import pid_alive
from .._driver.state import AdapterStore
from .platform import processes

WORKER_ENV = "XPEDITION_CLI_JOB_WORKER"  # set by the runtime in a long job's worker


def in_worker() -> bool:
    return bool(os.environ.get(WORKER_ENV))


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


def _running_key(session: str) -> str:
    return "running-" + session.replace("-", "_").lower()[:80]


_depth: dict[str, int] = {}  # how deep this process is in ``running`` per session


def _marks(store: AdapterStore, session: str) -> dict[str, dict[str, Any]]:
    """The marks of the workers that drive a session, by process ID."""
    marks = store.get(_running_key(session))
    if not isinstance(marks, dict):
        return {}
    return {key: mark for key, mark in marks.items() if isinstance(mark, dict)}


def _alive(mark: dict[str, Any]) -> bool:
    """Whether the worker that left a mark still runs: its process ID is alive and, when both
    are known, started when the mark says, since Windows reuses the ID of an ended process."""
    pid = int(mark.get("pid", 0) or 0)
    if not pid_alive(pid):
        return False
    recorded = int(mark.get("start_time", 0) or 0)
    now = processes.start_time(pid)
    return now is not None and (not recorded or not now or now == recorded)


def _put(store: AdapterStore, session: str, mark: dict[str, Any] | None) -> None:
    """Set or remove this process's mark, and drop the marks of workers that have ended."""
    me = str(os.getpid())
    marks = {key: m for key, m in _marks(store, session).items() if key != me and _alive(m)}
    if mark is not None:
        marks[me] = mark
    if marks:
        store.set(_running_key(session), marks)
    else:
        store.delete(_running_key(session))


@contextmanager
def running(store: AdapterStore, session: str | None, command: str,
            answers: tuple[tuple[str, str, str | None], ...] = ()) -> Iterator[None]:
    """Mark the instance of ``session`` as driven by this worker until the block ends, with the
    dialog rules (``platform.dialogs``) the worker answers itself meanwhile. A session that
    another worker drives is ``E_BUSY``: nothing is sent to it."""
    if not session:
        yield
        return
    if _depth.get(session, 0) == 0:
        other = running_on(store, session)
        if other is not None:
            raise AdapterError("E_BUSY", f"Another xpedition-cli job ({other}) drives session {session}; nothing was sent.",
                               dispatch_state="not_started", retry_after_ms=5000)
        _put(store, session, {"pid": os.getpid(), "start_time": processes.start_time(os.getpid()) or 0, "command": command,
                              "started_at": utc_now(), "answers": [list(rule) for rule in answers]})
    _depth[session] = _depth.get(session, 0) + 1
    try:
        yield
    finally:
        _depth[session] -= 1
        if _depth[session] == 0:
            del _depth[session]
            _put(store, session, None)


def _live_marks(store: AdapterStore, session: str) -> list[dict[str, Any]]:
    return [mark for key, mark in _marks(store, session).items() if key != str(os.getpid()) and _alive(mark)]


def running_on(store: AdapterStore, session: str) -> str | None:
    """The command another live worker is running on a session, if any; that session is busy."""
    marks = _live_marks(store, session)
    return str(marks[0].get("command", "")) if marks else None


def answered_by_job(store: AdapterStore, session: str) -> list[tuple[str, str, str | None]] | None:
    """The dialog rules the live workers on the session answer themselves, or None without one."""
    marks = _live_marks(store, session)
    if not marks:
        return None
    return [tuple(rule) for mark in marks for rule in mark.get("answers", []) if isinstance(rule, list) and len(rule) == 3]


class Progress:
    """What a handler's ``run`` calls with its steps and with the dialogs it waits on.

    The runtime's job record has no progress for the work it runs, so the steps are only kept
    for the worker's own diagnostics. A dialog that waits for a person shows as a ``blocked``
    session meanwhile, and the runtime then reports the job ``blocked`` too (DCLI-220).
    """

    def __init__(self) -> None:
        self.last: dict[str, Any] | None = None
        self.dialog: dict[str, str] | None = None

    def __call__(self, done: float, total: float | None, unit: str) -> None:
        self.last = {"done": done, "total": total, "unit": unit}

    def blocked(self, action: str, message: str) -> None:
        self.dialog = {"action": action, "message": message}

    def unblocked(self) -> None:
        self.dialog = None
