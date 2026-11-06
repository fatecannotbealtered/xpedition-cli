"""Help for the commands the runtime runs as long jobs (DCLI-065).

A command registered ``long: true`` returns a job at once; the runtime runs the adapter's
``apply`` in a worker process of this tool, with the long deadline, and ``job get`` reports how
it ended. This module holds what the adapter needs around that:

- ``in_worker``: whether this process is such a worker, so a step may take longer than a call;
- ``running``: while a worker drives an instance, ``sessions`` reports that instance ``busy``;
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

from .._driver.records import pid_alive
from .._driver.state import AdapterStore

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


@contextmanager
def running(store: AdapterStore, session: str | None, command: str) -> Iterator[None]:
    """Mark the instance of ``session`` as driven by this worker until the block ends."""
    if not session:
        yield
        return
    store.set(_running_key(session), {"pid": os.getpid(), "command": command, "started_at": utc_now()})
    try:
        yield
    finally:
        store.delete(_running_key(session))


def running_on(store: AdapterStore, session: str) -> str | None:
    """The command a live worker is running on a session, if any; that session is busy."""
    mark = store.get(_running_key(session))
    if isinstance(mark, dict) and pid_alive(int(mark.get("pid", 0))) and int(mark.get("pid", 0)) != os.getpid():
        return str(mark.get("command", ""))
    return None


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
