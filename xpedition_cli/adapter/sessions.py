"""Sessions and documents (DCLI-201 to DCLI-213): the running Designer and Layout instances.

A session is one running program: ``designer-<pid>-<start>`` or ``layout-<pid>-<start>``. Its
document is the project Designer has open (``.prj``) or the board Layout has open (``.pcb``);
document IDs are the absolute file paths. In file mode the document is a project file.

The application-specific work lives in ``designer`` and ``layout``; each offers the same
functions (see ``Application``). This module adds what the runtime needs around them: session
objects, the documents' ``dirty`` and ``revision`` values, which sessions the tool started, and
the list of the tool's changes since the last save, which ``doc save`` previews (DCLI-058,
DCLI-277). Xpedition reports neither a change counter nor an unsaved flag through COM, so
``dirty`` and ``revision`` come from the tool's own writes; a person's edits in the application
are not seen (knowledge entry ``xpedition-external-edits-not-tracked``).
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Protocol

from .._driver import AdapterError
from .._driver.state import AdapterStore
from . import jobs
from .base import DESIGNER, LAYOUT, SessionRef, make_session_id, parse_session

INSTANCE_TIMEOUT_S = 2.0  # one instance that does not answer in time is reported unresponsive (DCLI-205)
START_WAIT_S = 40.0


class Application(Protocol):
    """What ``designer`` and ``layout`` each provide."""

    APPLICATION: str  # the name in session objects, such as "Xpedition Designer"
    VERSION: str

    def instances(self, timeout_s: float) -> list[dict[str, Any]]:
        """Every running process of this program: ``{pid, state, documents: [path], blocker, version}``.
        ``state`` is ready, starting, busy, unresponsive or blocked; ``blocker`` is
        ``{action, title}`` when blocked."""

    def start(self, path: str | None, wait_s: float) -> dict[str, Any]:
        """Start the program through the Xpedition launcher, optionally opening ``path``;
        return ``{pid, state}`` of the real process once it is known."""

    def quit(self, pid: int, discard: bool) -> None: ...

    def open_document(self, pid: int, path: str) -> None: ...

    def save_document(self, pid: int, path: str) -> None: ...

    def close_document(self, pid: int, path: str, discard: bool) -> None: ...

    def reopen_document(self, pid: int, path: str) -> None:
        """Close and open the saved document again, so that reads come from the file."""

    def read_targets(self, pid: int, path: str, targets: list[str], fields: list[str]) -> dict[str, Any]:
        """Read the named fields of the named target keys: ``{observed, unreadable}``."""


def _app(kind: str) -> Application:
    if kind == DESIGNER:
        from . import designer

        return designer  # type: ignore[return-value]
    from . import layout

    return layout  # type: ignore[return-value]


def _doc_key(session: str, path: str) -> str:
    return "doc-" + hashlib.sha256(f"{session}|{os.path.normcase(os.path.abspath(path))}".encode()).hexdigest()[:24]


# ------------------------------------------------------------------ document state


def doc_state(store: AdapterStore, session: str, path: str) -> dict[str, Any]:
    return store.get(_doc_key(session, path), {"revision": 0, "dirty": False, "changes": []})


def note_change(store: AdapterStore, session: str, path: str, changes: list[dict[str, Any]]) -> None:
    """A session-mode write changed the document in memory: dirty, new revision, and the changes
    kept for the next save preview (DCLI-058)."""
    state = doc_state(store, session, path)
    state["revision"] = int(state.get("revision", 0)) + 1
    state["dirty"] = True
    state["changes"] = list(state.get("changes", [])) + [dict(c, origin="tool") for c in changes]
    store.set(_doc_key(session, path), state)


def note_saved(store: AdapterStore, session: str, path: str) -> None:
    state = doc_state(store, session, path)
    state.update(revision=int(state.get("revision", 0)) + 1, dirty=False, changes=[])
    store.set(_doc_key(session, path), state)


def forget(store: AdapterStore, session: str, path: str) -> None:
    store.delete(_doc_key(session, path))


def _document(store: AdapterStore, session: str | None, path: str) -> dict[str, Any]:
    state = doc_state(store, session or "file", path)
    try:
        stat = os.stat(path)
        disk = "in_sync"
        stamp = f"{stat.st_mtime_ns:x}-{stat.st_size:x}"
    except OSError:
        disk, stamp = "missing", "0"
    revision = f"r{state.get('revision', 0)}" if session else stamp
    return {"id": path, "path": path, "session": session, "revision": revision, "dirty": bool(state.get("dirty")),
            "readonly": False, "disk": disk}


# ------------------------------------------------------------------ sessions


def _owned(store: AdapterStore) -> list[str]:
    return list(store.get("owned", []))


def list_sessions(store: AdapterStore) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    complete = True
    for kind in (DESIGNER, LAYOUT):
        app = _app(kind)
        try:
            found = app.instances(INSTANCE_TIMEOUT_S)
        except AdapterError:
            complete = False
            continue
        for inst in found:
            sid = make_session_id(kind, int(inst["pid"]))
            state = inst.get("state", "ready")
            if state == "ready" and jobs.running_on(store, sid):
                state = "busy"  # a long job drives this instance
            docs = [os.path.abspath(p) for p in inst.get("documents", [])]
            session: dict[str, Any] = {
                "id": sid, "application": {"name": app.APPLICATION, "version": inst.get("version") or app.VERSION},
                "pid": int(inst["pid"]), "owned": sid in _owned(store), "generation": 1, "state": state,
                "documents": [{"id": p, "path": p, "dirty": bool(doc_state(store, sid, p).get("dirty"))} for p in docs],
                "plugin_loaded": None,
            }
            if state == "blocked" and inst.get("blocker"):
                session["blocker"] = inst["blocker"]
            items.append(session)
    return {"items": items, "complete": complete}


def _session(store: AdapterStore, session_id: str) -> tuple[SessionRef, dict[str, Any]]:
    ref = parse_session(session_id)
    assert ref is not None
    for item in list_sessions(store)["items"]:
        if item["id"] == session_id:
            return ref, item
    raise AdapterError("E_NOT_FOUND", f"There is no session {session_id}.", dispatch_state="not_started")


def documents(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    if request.get("session"):
        _, item = _session(store, request["session"])
        return {"items": [_document(store, item["id"], d["path"]) for d in item["documents"]]}
    path = os.path.abspath(str(request.get("path", "")))
    if os.path.isfile(path) and path.lower().endswith(".prj"):
        return {"items": [_document(store, None, path)]}
    return {"items": []}


def open_session(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    path = request.get("path")
    if not path:
        raise AdapterError("E_VALIDATION", "session open needs --doc: a project (.prj) starts Designer, a board (.pcb) starts Layout.",
                           dispatch_state="not_started")
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".prj", ".pcb"):
        raise AdapterError("E_VALIDATION", f"{path} is neither a project (.prj) nor a board (.pcb).", dispatch_state="not_started")
    if not os.path.isfile(path):
        raise AdapterError("E_NOT_FOUND", f"There is no file {path}.", dispatch_state="not_started")
    kind = DESIGNER if ext == ".prj" else LAYOUT
    started = _app(kind).start(path, START_WAIT_S)
    sid = make_session_id(kind, int(started["pid"]))
    store.set("owned", sorted(set(_owned(store)) | {sid}))
    for item in list_sessions(store)["items"]:
        if item["id"] == sid:
            return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
                    "persistence": "not_applicable", "session": item}
    session = {"id": sid, "application": {"name": _app(kind).APPLICATION, "version": _app(kind).VERSION},
               "pid": int(started["pid"]), "owned": True, "generation": 1, "state": started.get("state", "starting"),
               "documents": [], "plugin_loaded": None}
    return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
            "persistence": "not_applicable", "session": session}


def close_session(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    ref, item = _session(store, request["session"])
    _app(ref.kind).quit(ref.pid, bool(request.get("discard")))
    for doc in item["documents"]:
        forget(store, ref.id, doc["path"])
    store.set("owned", [s for s in _owned(store) if s != ref.id])
    return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
            "persistence": "unchanged" if not request.get("discard") else "not_applicable"}


def open_doc(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    ref, _ = _session(store, request["session"])
    path = os.path.abspath(request["path"])
    want = ".prj" if ref.kind == DESIGNER else ".pcb"
    if not path.lower().endswith(want):
        raise AdapterError("E_VALIDATION", f"This session opens {want} files.", dispatch_state="not_started")
    _app(ref.kind).open_document(ref.pid, path)
    note_saved(store, ref.id, path)
    return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
            "persistence": "not_applicable", "document": _document(store, ref.id, path)}


def save_doc(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    ref, _ = _session(store, request["session"])
    path = os.path.abspath(request["document"])
    _app(ref.kind).save_document(ref.pid, path)
    pending = doc_state(store, ref.id, path).get("changes", [])
    note_saved(store, ref.id, path)
    store.set(_doc_key(ref.id, path) + "-saved", {"changes": pending})
    return {"dispatch_state": "dispatched", "change": "not_applicable", "delivery": "not_applicable",
            "persistence": "saved", "document": _document(store, ref.id, path)}


def close_doc(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    ref, _ = _session(store, request["session"])
    path = os.path.abspath(request["document"])
    _app(ref.kind).close_document(ref.pid, path, bool(request.get("discard")))
    forget(store, ref.id, path)
    return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
            "persistence": "not_applicable"}


# ------------------------------------------------------------------ doc save preview and read-back


def save_preview(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    ref = parse_session(request.get("session"))
    assert ref is not None
    path = os.path.abspath(request["document"])
    changes = [c for c in doc_state(store, ref.id, path).get("changes", []) if c.get("field")]
    return {"action": f"Save {path}.", "changes": changes, "external_changes": [], "can_apply": True}


def save_read_back(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    """Read the saved changes from the file: the document is opened again first (DCLI-057)."""
    ref = parse_session(request.get("session"))
    assert ref is not None
    path = os.path.abspath(request["document"])
    app = _app(ref.kind)
    app.reopen_document(ref.pid, path)
    return app.read_targets(ref.pid, path, list(request.get("targets", [])), list(request.get("fields", [])))
