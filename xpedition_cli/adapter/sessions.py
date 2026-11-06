"""Sessions and documents (DCLI-201 to DCLI-213): the running Designer and Layout instances.

A session is one running program: ``designer-<pid>-<start>`` or ``layout-<pid>-<start>``. Its
document is the project Designer has open (``.prj``) or the board Layout has open (``.pcb``);
document IDs are the absolute file paths. In file mode the document is a project file.

The application-specific work lives in ``designer`` and ``layout``; each offers the same
functions (see ``Application``). This module adds what the runtime needs around them: session
objects, the documents' ``dirty`` and ``revision`` values, which sessions the tool started, and
the list of the tool's changes since the last save, which ``doc save`` previews (DCLI-058,
DCLI-277). Xpedition reports neither a change counter nor an unsaved flag through COM, so
``dirty`` and ``revision`` come from the tool's own writes: ``dirty`` is ``null`` (cannot tell)
for a document the tool did not open or save itself, and a person's edits in the application
are not seen (knowledge entry ``xpedition-external-edits-not-tracked``).

A document's ``kind`` is ``project`` (.prj) or ``board`` (.pcb). An instance COM cannot reach,
such as one of two Designers, is ``unreachable`` (DCLI-204).
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Protocol

from .._driver import AdapterError
from .._driver.state import AdapterStore
from . import jobs
from .._driver.targets import file_key, normalize
from .base import DESIGNER, LAYOUT, SessionRef, make_session_id, parse_session

INSTANCE_TIMEOUT_S = 2.0  # one instance that does not answer in time is reported unresponsive (DCLI-205)
START_WAIT_S = 300.0  # session open runs in the long-job worker: start, register for COM and open the file
KINDS = {"project": DESIGNER, "board": LAYOUT}  # the document kinds, and the program that opens each
STAMP_FILES = 50000  # at most this many files are looked at to see that a save reached the disk


class Application(Protocol):
    """What ``designer`` and ``layout`` each provide."""

    APPLICATION: str  # the name in session objects, such as "Xpedition Designer"
    VERSION: str

    def instances(self, timeout_s: float) -> list[dict[str, Any]]:
        """Every running process of this program: ``{pid, state, documents: [path], blocker, version}``.
        ``state`` is ready, starting, busy, unresponsive, blocked or unreachable; ``blocker``
        is ``{action, title}`` when blocked, and ``reason`` says why an instance is unreachable."""

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


def preview_key(request: dict[str, Any]) -> str:
    """Where a session-mode write's preview changes wait for its apply (or its job) to finish."""
    import json

    keep = {k: request.get(k) for k in ("command", "inputs", "session", "document")}
    return "pv-" + hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:24]


def changed(facts: dict[str, Any]) -> bool:
    """Whether a write changed the document in memory, so that it now has unsaved changes."""
    return facts.get("dispatch_state") != "not_started" and facts.get("change") in ("applied", "partial", "unknown")


def _doc_key(session: str, path: str) -> str:
    return "doc-" + hashlib.sha256(f"{session}|{os.path.normcase(os.path.abspath(path))}".encode()).hexdigest()[:24]


# ------------------------------------------------------------------ document state


def doc_state(store: AdapterStore, session: str, path: str) -> dict[str, Any]:
    """The tool's record of a document: ``dirty`` is None when the tool neither opened nor saved it."""
    return store.get(_doc_key(session, path), {"revision": 0, "dirty": None, "changes": []})


def doc_kind(path: str) -> str:
    return "board" if path.lower().endswith(".pcb") else "project"


def _dirty(store: AdapterStore, session: str, path: str) -> bool | None:
    value = doc_state(store, session, path).get("dirty")
    return None if value is None else bool(value)


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
    dirty = _dirty(store, session, path) if session else False  # a file on disk has no unsaved changes
    return {"id": path, "path": path, "session": session, "kind": doc_kind(path), "revision": revision, "dirty": dirty,
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
            known = store.get("docs-" + sid.replace("-", "_").lower()[:90])
            if state == "ready":
                docs = [os.path.abspath(p) for p in inst.get("documents", [])]
                store.set("docs-" + sid.replace("-", "_").lower()[:90], docs)
            elif inst.get("documents"):
                docs = [os.path.abspath(p) for p in inst["documents"]]
            else:
                # a busy, blocked or unresponsive instance cannot say what it has open: report
                # what it had when it last answered, and say that the list may be incomplete
                docs = list(known or [])
                complete = False
            session: dict[str, Any] = {
                "id": sid, "application": {"name": app.APPLICATION, "version": inst.get("version") or app.VERSION},
                "pid": int(inst["pid"]), "owned": sid in _owned(store), "generation": 1, "state": state,
                "documents": [{"id": p, "path": p, "dirty": _dirty(store, sid, p)} for p in docs],
                "plugin_loaded": None,
            }
            if state == "blocked" and inst.get("blocker"):
                session["blocker"] = inst["blocker"]
            if state == "unreachable" and inst.get("reason"):
                session["extra"] = {"reason": inst["reason"]}
            items.append(session)
    return {"items": items, "complete": complete}


def session_of(store: AdapterStore, session_id: str) -> tuple[SessionRef, dict[str, Any]]:
    """The session and its row in the session list; E_NOT_FOUND when it has gone."""
    return _session(store, session_id)


def document_view(store: AdapterStore, session: str | None, path: str) -> dict[str, Any]:
    """A document as the documents capability reports it."""
    return _document(store, session, path)


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
    if os.path.isdir(path):
        # a folder: the project files in it and in its subfolders one level down
        found = sorted({os.path.join(root, name) for root, dirs, names in _walk(path, 1) for name in names
                        if name.lower().endswith(".prj")})
        return {"items": [_document(store, None, p) for p in found]}
    return {"items": []}


def _walk(top: str, depth: int) -> list[tuple[str, list[str], list[str]]]:
    out = []
    for root, dirs, names in os.walk(top):
        out.append((root, dirs, names))
        if root[len(top):].count(os.sep) >= depth:
            dirs[:] = []
    return out


def open_session(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    """Start Designer for a project or Layout for a board: by the file given, else by ``kind``
    (``project`` or ``board``), else Designer with no project, where doc create makes one."""
    path, wanted = request.get("path"), request.get("kind")
    if wanted is not None and wanted not in KINDS:
        raise AdapterError("E_VALIDATION", f"The document kind {wanted!r} is not one of {', '.join(sorted(KINDS))}.",
                           dispatch_state="not_started")
    if path:
        path = os.path.abspath(path)
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".prj", ".pcb"):
            raise AdapterError("E_VALIDATION", f"{path} is neither a project (.prj) nor a board (.pcb).", dispatch_state="not_started")
        if wanted is not None and doc_kind(path) != wanted:
            raise AdapterError("E_VALIDATION", f"{path} is a {doc_kind(path)}, not a {wanted}.", dispatch_state="not_started")
        if not os.path.isfile(path):
            raise AdapterError("E_NOT_FOUND", f"There is no file {path}.", dispatch_state="not_started")
    kind = KINDS[doc_kind(path)] if path else KINDS[wanted or "project"]
    started = _app(kind).start(path, START_WAIT_S)
    sid = make_session_id(kind, int(started["pid"]))
    store.set("owned", sorted(set(_owned(store)) | {sid}))
    if path:
        note_saved(store, sid, path)  # opened from the file by the tool: no unsaved changes yet
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
    already = any(os.path.normcase(d["path"]) == os.path.normcase(path) for d in _session(store, ref.id)[1]["documents"])
    _app(ref.kind).open_document(ref.pid, path)
    if not already:
        note_saved(store, ref.id, path)  # freshly opened from the file: clean; an open document keeps its state
    return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
            "persistence": "not_applicable", "document": _document(store, ref.id, path)}


def disk_stamp(path: str) -> tuple[int, int]:
    """The newest modification time and the number of files in the document's folder (the
    project folder, or the board's PCB folder): what a save writes into."""
    newest, count = 0, 0
    for root, _dirs, names in os.walk(os.path.dirname(os.path.abspath(path))):
        for name in names:
            try:
                newest = max(newest, os.stat(os.path.join(root, name)).st_mtime_ns)
            except OSError:
                continue
            count += 1
            if count >= STAMP_FILES:
                return newest, count
    return newest, count


def save_doc(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    ref, _ = _session(store, request["session"])
    path = os.path.abspath(request["document"])
    before, started = disk_stamp(path), time.time_ns()
    _app(ref.kind).save_document(ref.pid, path)
    if jobs.in_worker():
        # Closing and opening the document again makes Xpedition write it out (Designer's
        # database server writes the project files only when the project closes), and the
        # read-back then reads what was saved (DCLI-057).
        _app(ref.kind).reopen_document(ref.pid, path)
    after = disk_stamp(path)
    pending = doc_state(store, ref.id, path).get("changes", [])
    note_saved(store, ref.id, path)
    # The read-back says `saved` only when the save was seen to write the files (DCLI-057).
    reached = after != before and after[0] >= started - 2_000_000_000
    store.set(_doc_key(ref.id, path) + "-saved", {"changes": pending, "reached_disk": reached})
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
    """Read the saved changes back from the document the save closed and opened again, so they
    come from the saved files (DCLI-057); the document's ``saved`` says whether the save was seen
    to write the files on disk."""
    ref = parse_session(request.get("session"))
    assert ref is not None
    path = os.path.abspath(request["document"])
    doc = file_key(path)
    targets = [t for t in request.get("targets", []) if normalize(t) != normalize(doc)]
    fields = [f for f in request.get("fields", []) if f != "saved"]
    out: dict[str, Any] = {"observed": {}, "unreadable": []}
    if targets:
        out = _app(ref.kind).read_targets(ref.pid, path, targets, fields)
    if "saved" in request.get("fields", []):
        mark = store.get(_doc_key(ref.id, path) + "-saved", {}) or {}
        observed = out.setdefault("observed", {})
        key = next((k for k in observed if normalize(k) == normalize(doc)), doc)
        observed.setdefault(key, {})["saved"] = bool(mark.get("reached_disk"))
    return out
