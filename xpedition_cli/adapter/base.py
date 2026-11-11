"""Shared pieces of the adapter: the handler interface, the call context and preview helpers.

Every domain command has one ``Handler`` in an area module (``project``, ``library``,
``designer``, ``layout``). The adapter (``__init__.py``) routes ``read``, ``preview``, ``apply``
and ``read_back`` to it by the command path.

Rules every handler follows (DCLI-260, adapter.schema.json):

- Inputs arrive in standard units: lengths in millimetres (``width_mm``), angles in degrees.
- Report facts only. The runtime compares the read-back with the preview's ``after`` values and
  derives the receipt; a handler never decides that a write "succeeded".
- A failure raises ``AdapterError(code, message, dispatch_state=...)``. A write capability says
  ``not_started`` when nothing reached Xpedition, ``dispatched`` when it did.
- ``after`` in a preview is the value Xpedition will actually store, in the same form the
  read-back returns, so that the runtime can compare them.
- A session-mode write never saves. Only ``doc save`` saves (DCLI-201).
- What the application's state stands in the way of (a part already in the library with other
  content, a board with no outline) is not bad input: a preview reports it in ``blocked_by``,
  and an ``apply`` that finds it raises ``blocked(...)``, E_CONFLICT ``application_state``. The
  capabilities without a preview (open a session or a document) raise ``blocked(...)`` too.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from .._driver import AdapterError
from .._driver import session_id as runtime_session_id
from .._driver.state import AdapterStore
from .._driver.targets import file_key

DESIGNER = "designer"
LAYOUT = "layout"


@dataclass
class SessionRef:
    """A session ID taken apart: ``designer-4120-6553f1a0`` is Designer, process 4120, started at
    that time (DCLI-203)."""

    id: str
    kind: str
    pid: int


def parse_session(session_id: str | None) -> SessionRef | None:
    if not session_id:
        return None
    parts = session_id.split("-")
    if len(parts) != 3 or parts[0] not in (DESIGNER, LAYOUT) or not parts[1].isdigit():
        raise AdapterError("E_NOT_FOUND", f"There is no session {session_id}.", dispatch_state="not_started")
    return SessionRef(session_id, parts[0], int(parts[1]))


def make_session_id(kind: str, pid: int) -> str:
    """A session ID that stays the same for the life of the process and is never reused (DCLI-203)."""
    return runtime_session_id(kind, pid)


class Context:
    """What one capability call knows: the request, the adapter store and the target helpers."""

    def __init__(self, store: AdapterStore, request: dict[str, Any]) -> None:
        self.store = store
        self.request = request
        self.inputs: dict[str, Any] = dict(request.get("inputs") or {})
        self.session = parse_session(request.get("session"))

    @property
    def command(self) -> str:
        return str(self.request.get("command", ""))

    @property
    def operation_id(self) -> str | None:
        return self.request.get("operation_id")

    def document_path(self) -> str:
        """The document's file. Document IDs are absolute paths, in file and session mode."""
        doc = self.request.get("document")
        if not doc:
            raise AdapterError("E_VALIDATION", f"{self.command} needs a document.", dispatch_state="not_started")
        return os.path.abspath(str(doc))

    def need_session(self, kind: str) -> SessionRef:
        if self.session is None or self.session.kind != kind:
            name = "Designer" if kind == DESIGNER else "Layout"
            raise AdapterError("E_VALIDATION", f"{self.command} runs in a {name} session; name it with --session.",
                               dispatch_state="not_started")
        return self.session


def same_path(left: Any, right: Any) -> bool:
    """Whether two paths name the same file, also when one is written with short (8.3) folder
    names, as a temporary folder can be, and the other with long ones."""
    a, b = str(left), str(right)
    if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
        return True
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def key(path: str, domain_path: str | None = None) -> str:
    """The target key of a file, or of an object in it (DCLI-028): ``file:///D:/p/x.prj#/parts/R1``."""
    return file_key(os.path.abspath(path), domain_path)


def target(path: str, kind: str, domain_path: str | None = None, label: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"key": key(path, domain_path), "kind": kind}
    if label:
        out["label"] = label
    return out


def change(target_key: str, field: str, before: Any, after: Any) -> dict[str, Any]:
    return {"target": target_key, "field": field, "before": before, "after": after}


RESULT_CHARS = 6000  # what a result may take of the 8000 characters of one response, beside its envelope and receipt


def fitted(out: dict[str, Any], keys: list[str], limit: int = RESULT_CHARS) -> dict[str, Any]:
    """``out`` with the lists under ``keys`` halved, in that order, until its JSON fits ``limit``
    characters, so that a long result comes back cut instead of refused; a ``<key>_count`` or an
    ``--output`` file keeps what is cut."""
    import json

    def size() -> int:
        return len(json.dumps(out, ensure_ascii=False, default=str))

    for key in keys:
        while size() > limit and isinstance(out.get(key), list) and out[key]:
            out[key] = out[key][:len(out[key]) // 2]
    return out


def within(rows: list[str], limit: int) -> list[str]:
    """The first of ``rows`` that together take no more than ``limit`` characters, at least one."""
    out: list[str] = []
    for row in rows:
        if out and sum(len(r) for r in out) + len(row) > limit:
            break
        out.append(row)
    return out


def preview(action: str, targets: list[dict[str, Any]], changes: list[dict[str, Any]],
            blocked_by: list[str] | None = None, knowledge: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"action": action, "targets": targets, "changes": changes, "can_apply": not blocked_by}
    if blocked_by:
        out["blocked_by"] = blocked_by
        if knowledge:
            out["knowledge"] = knowledge
    return out


def blocked(reason: str, knowledge: list[str] | None = None, **extra: Any) -> AdapterError:
    """The application's state stands in the way, and nothing was sent: E_CONFLICT ``application_state``.
    The adapter's ``preview`` turns it into a blocked preview."""
    if knowledge:
        extra["knowledge"] = knowledge
    return AdapterError("E_CONFLICT", reason, dispatch_state="not_started", conflict="application_state", **extra)


def facts(change_state: str = "applied", persistence: str = "unchanged", *, result: Any = None,
          files: list[dict[str, Any]] | None = None, native_message: str | None = None) -> dict[str, Any]:
    """The facts ``apply`` reports (write_facts): the change reached Xpedition."""
    out: dict[str, Any] = {"dispatch_state": "dispatched", "change": change_state, "delivery": "not_applicable",
                           "persistence": persistence}
    if result is not None:
        out["result"] = result
    if files:
        out["files"] = files
    if native_message:
        out["native_message"] = native_message
    return out


class Handler:
    """One domain command. Subclasses implement ``read`` (observe) or the three write methods.

    A handler of a command registered ``long: true`` (DCLI-065) sets ``long_job = True`` and
    implements ``run`` instead of ``apply``; the runtime calls ``apply`` in its long job's worker,
    and the adapter runs ``run`` there. ``run`` reports its steps through
    ``progress(done, total, unit)`` and returns the facts: ``change``, ``persistence``,
    ``result`` and, for files it wrote, ``files``.
    """

    command = ""
    long_job = False
    answers: tuple[tuple[str, str, str | None], ...] = ()  # dialog rules the work answers itself (platform.dialogs)

    def long(self, ctx: Context) -> bool:
        """Whether this call runs ``run``; ``long_job`` unless a handler decides per call."""
        return self.long_job

    def read(self, ctx: Context) -> dict[str, Any]:
        raise AdapterError("E_UNSUPPORTED", f"{self.command} does not read.")

    def preview(self, ctx: Context) -> dict[str, Any]:
        raise AdapterError("E_UNSUPPORTED", f"{self.command} does not write.")

    def apply(self, ctx: Context) -> dict[str, Any]:
        raise AdapterError("E_UNSUPPORTED", f"{self.command} does not write.", dispatch_state="not_started")

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        raise AdapterError("E_UNSUPPORTED", f"{self.command} is not a long job.")

    def read_back(self, ctx: Context) -> dict[str, Any]:
        raise AdapterError("E_UNSUPPORTED", f"{self.command} cannot read back.")


def need_input(ctx: Context, name: str) -> Any:
    if name not in ctx.inputs:
        raise AdapterError("E_VALIDATION", f"{ctx.command} needs --{name.replace('_', '-')}.", dispatch_state="not_started")
    return ctx.inputs[name]


REPLACE_TRIES = 10
REPLACE_WAIT_S = 0.2


def replace_file(source: Any, target: Any) -> None:
    """``os.replace``, tried again for two seconds while the file is refused: on Windows a virus scanner
    or the search indexer holds a file it has just seen being written for a moment. A file another
    program keeps open stays refused, and its PermissionError reaches the caller."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT_S)
