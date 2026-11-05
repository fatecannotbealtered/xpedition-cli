"""The worker process of a long job: ``python -m xpedition_cli.adapter.worker`` with the job file
named in ``XPEDITION_CLI_WORKER`` (the native program checks that variable before the CLI starts).

It runs the handler's ``run`` and writes the final facts into the job file (DCLI-065).
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from . import jobs, sessions


class _FileStore:
    """The adapter store as the worker sees it: the same folder as the CLI's store."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _file(self, key: str) -> Path:
        return self.folder / f"{key}.json"

    def get(self, key: str, default: Any = None) -> Any:
        value = jobs.read(self._file(key))
        return default if value is None else value

    def set(self, key: str, value: Any) -> None:
        jobs.write(self._file(key), value)

    def delete(self, key: str) -> None:
        self._file(key).unlink(missing_ok=True)


def run(path: Path) -> int:
    from . import HANDLERS
    from .base import Context

    record = jobs.read(path)
    if record is None:
        return 2
    request = record["request"]
    handler = HANDLERS.get(request.get("command", ""))
    final: dict[str, Any]
    try:
        if handler is None or not handler.long_job:
            raise AdapterError("E_UNSUPPORTED", f"{request.get('command')!r} is not a long job.", dispatch_state="not_started")
        ctx = Context(_FileStore(path.parent), request)  # type: ignore[arg-type]
        out = handler.run(ctx, jobs.Progress(path))
        final = {"state": out.pop("state", "completed"), "delivery": "not_applicable", **out}
    except jobs.Cancelled:
        final = {"state": "cancelled", "change": "partial", "native_message": "Stopped on request between two steps."}
    except AdapterError as exc:
        data = exc.data
        started = data.get("dispatch_state") != "not_started"
        final = {"state": "failed", "change": data.get("change", "unknown" if started else "not_applied"),
                 "persistence": data.get("persistence", "unknown" if started else "not_applicable"),
                 "native_message": f"{data.get('code', 'E_NATIVE_FAILURE')}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - the job's outcome is then not known
        final = {"state": "unknown", "native_message": f"{type(exc).__name__}: {exc}"}
        traceback.print_exc()
    if request.get("session") and request.get("document") and sessions.changed({"dispatch_state": "dispatched", **final}):
        store = _FileStore(path.parent)
        kept = store.get(sessions.preview_key(request), {}) or {}
        sessions.note_change(store, request["session"], request["document"], kept.get("changes", []))  # type: ignore[arg-type]
        store.delete(sessions.preview_key(request))
    current = jobs.read(path) or record
    current.update(final, finished_at=jobs.utc_now())
    if current.get("action") and current["state"] != "blocked":
        current["action"] = None
    jobs.write(path, current)
    return 0


def main() -> None:
    target = os.environ.get(jobs.WORKER_ENV)
    if not target:
        sys.exit("XPEDITION_CLI_WORKER names no job file")
    sys.exit(run(Path(target)))


if __name__ == "__main__":
    main()
