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
from contextlib import nullcontext
from typing import Any, Protocol

from .._driver import AdapterError
from .._driver.state import AdapterStore
from . import jobs
from .._driver.targets import file_key, normalize
from .base import DESIGNER, LAYOUT, SessionRef, make_session_id, parse_session
from .platform import icdb

INSTANCE_TIMEOUT_S = 1.2  # one instance that does not answer in time is reported unresponsive; with the dialog look it stays inside the 2.5 s the adapter's sessions gets (DCLI-205)
START_WAIT_S = 300.0  # session open runs in the long-job worker: start, register for COM and open the file
KINDS = {"project": DESIGNER, "board": LAYOUT}  # the document kinds, and the program that opens each
STAMP_FILES = 50000  # at most this many files are looked at to see that a save reached the disk


class Application(Protocol):
    """What ``designer`` and ``layout`` each provide."""

    APPLICATION: str  # the name in session objects, such as "Xpedition Designer"
    VERSION: str

    def open_documents(self, pid: int, timeout_s: float) -> list[str] | None:
        """The files the instance has open, or None when it does not answer within ``timeout_s``."""

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

    def read_targets(self, pid: int, path: str, targets: list[str], fields: list[str], store: Any = None) -> dict[str, Any]:
        """Read the named fields of the named target keys: ``{observed, unreadable}``; ``store``, the
        adapter's, holds what earlier writes kept for reading back, such as a routing plan."""


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


COLLECTIONS = ("#/traces/", "#/vias/")  # a net's traces or vias: their fields describe all of them together


def _superseded(earlier: dict[str, Any], change: dict[str, Any]) -> bool:
    """Whether a later change makes what an earlier write expected of a field unreliable: the
    later one is on an object that holds the earlier one's or that it holds, such as a net's traces
    and its traces on one layer; it makes or deletes the very object, whose other fields then start
    afresh; or it changes a net's traces or vias, whose other fields, such as the count a plane's
    bend ties expected or the padstack a via delete left, then describe another set. A later change
    of another field of the same single object leaves it standing."""
    a, b = str(earlier.get("target") or ""), str(change.get("target") or "")
    if a.startswith(b + "/") or b.startswith(a + "/"):
        return True
    if a != b or change.get("field") == earlier.get("field"):
        return False
    return change.get("field") == "exists" or any(marker in a for marker in COLLECTIONS)


def note_change(store: AdapterStore, session: str, path: str, changes: list[dict[str, Any]]) -> None:
    """A session-mode write changed the document in memory: dirty, new revision, and the changes
    kept for the next save preview (DCLI-058). A later change of a field replaces an earlier one,
    keeping the earlier one's ``before``: the save stores only the last value. A later write also
    drops what earlier writes expected that it makes unreliable (``_superseded``), such as the
    trace count a trace delete expected before a trace create drew
    the net again, or the hatch a plane delete expected before a plane create poured the layer
    again: the later write is what the save must find."""
    state = doc_state(store, session, path)
    state["revision"] = int(state.get("revision", 0)) + 1
    state["dirty"] = True
    earlier = list(state.get("changes", []))
    befores = {(c.get("target"), c.get("field")): c["before"] for c in earlier if "before" in c}
    kept = [c for c in earlier if not any(_superseded(c, change) for change in changes)]
    for change in changes:
        entry = dict(change, origin="tool")
        if (change.get("target"), change.get("field")) in befores:
            entry["before"] = befores[(change.get("target"), change.get("field"))]
        kept = [c for c in kept if not (c.get("target") == change.get("target") and c.get("field") == change.get("field"))] + [entry]
    state["changes"] = kept
    store.set(_doc_key(session, path), state)


def note_saved(store: AdapterStore, session: str, path: str) -> None:
    state = doc_state(store, session, path)
    state.update(revision=int(state.get("revision", 0)) + 1, dirty=False, changes=[])
    store.set(_doc_key(session, path), state)


def forget(store: AdapterStore, session: str, path: str) -> None:
    store.delete(_doc_key(session, path))
    forget_writes(store, session, path)


def forget_writes(store: AdapterStore, session: str, path: str) -> None:
    """Layout starts its Undo stack afresh when it opens a board: the tool's writes doc undo would take back go."""
    from .layout import forget_writes as forget_board_writes

    forget_board_writes(session, path, store)


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


def _instances(kind: str) -> list[dict[str, Any]] | None:
    try:
        return _app(kind).instances(INSTANCE_TIMEOUT_S)
    except AdapterError:
        return None


def list_sessions(store: AdapterStore) -> dict[str, Any]:
    """Designer and Layout are asked side by side: the runtime gives this call 2.5 s of the list's
    5 s (DCLI-205), and an instance that does not answer takes its probe and a look for a dialog."""
    from concurrent.futures import ThreadPoolExecutor

    items: list[dict[str, Any]] = []
    complete = True
    with ThreadPoolExecutor(2, thread_name_prefix="xpedition-sessions") as pool:
        answers = list(pool.map(_instances, (DESIGNER, LAYOUT)))
    for kind, found in zip((DESIGNER, LAYOUT), answers, strict=True):
        app = _app(kind)
        if found is None:
            complete = False
            continue
        for inst in found:
            sid = make_session_id(kind, int(inst["pid"]))
            state = inst.get("state", "ready")
            answers = jobs.answered_by_job(store, sid)
            if answers is not None:
                # a long job of this tool drives the instance: busy, unless a dialog the job does
                # not answer itself waits for a person
                if state in ("ready", "unresponsive") or (state == "blocked" and _answered(inst, answers)):
                    state = "busy"
            known = store.get(_docs_key(sid))
            if state == "ready":
                docs = [os.path.abspath(p) for p in inst.get("documents", [])]
                store.set(_docs_key(sid), docs)
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


def _answered(inst: dict[str, Any], answers: list[tuple[str, str, str | None]]) -> bool:
    """Whether the instance's blocker is a dialog the running job answers itself: as the job's
    answerer does (``dialogs.answer_prompts``), a rule's marker is in its title or text and the
    rule's button is on it."""
    blocker = inst.get("blocker") or {}
    haystack = f"{blocker.get('title', '')} {inst.get('blocker_text', '')}".lower()
    buttons = {str(b).replace("&", "").strip() for b in inst.get("blocker_buttons") or []}
    return any(marker.lower() in haystack and button in buttons for marker, button, _option in answers)


def _session(store: AdapterStore, session_id: str) -> tuple[SessionRef, dict[str, Any]]:
    ref = parse_session(session_id)
    assert ref is not None
    for item in list_sessions(store)["items"]:
        if item["id"] == session_id:
            return ref, item
    raise AdapterError("E_NOT_FOUND", f"There is no session {session_id}.", dispatch_state="not_started")


DOCS_TIMEOUT_S = 1.2  # the runtime gives a session's documents 2 s (DCLI-205)


def _docs_key(session_id: str) -> str:
    return "docs-" + session_id.replace("-", "_").lower()[:90]


def documents(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    """A session's documents ask that one instance only, within the runtime's 2 s; one that does
    not answer in time reports what it had when it last answered."""
    if request.get("session"):
        session_id = request["session"]
        ref = parse_session(session_id)
        assert ref is not None
        if make_session_id(ref.kind, ref.pid) != session_id:
            raise AdapterError("E_NOT_FOUND", f"There is no session {session_id}.", dispatch_state="not_started")
        paths = _app(ref.kind).open_documents(ref.pid, DOCS_TIMEOUT_S)
        if paths is None:
            paths = list(store.get(_docs_key(session_id)) or [])
        else:
            paths = [os.path.abspath(p) for p in paths]
            store.set(_docs_key(session_id), paths)
        return {"items": [_document(store, session_id, p) for p in paths]}
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


def _ref(session_id: str) -> SessionRef:
    ref = parse_session(session_id)
    if ref is None:
        raise AdapterError("E_NOT_FOUND", f"There is no session {session_id}.", dispatch_state="not_started")
    return ref


def _driving(store: AdapterStore, ref: SessionRef, command: str) -> Any:
    """While a long job's worker opens, saves or closes a document or quits an instance, that
    instance is busy from the job's first step, and the prompts the command answers itself do not
    block it."""
    if jobs.in_worker():
        return jobs.running(store, ref.id, command, tuple(getattr(_app(ref.kind), "ANSWERS", {}).get(command, ())))
    return nullcontext()


def release(store: AdapterStore, paths: list[str], closing: str | None = None) -> list[dict[str, str]]:
    """Wait until Xpedition's database server lets go of each project of ``paths`` that the call
    closed, so that the call reports done once the project's files are closed (``icdb``). A
    project a session other than ``closing`` still has open stays served and is not waited for.
    A notice for each project still served when the wait ends."""
    folders = {folder for folder in map(icdb.project_folder, paths) if folder is not None and icdb.served(folder)}
    if not folders:
        return []
    held = {icdb.project_folder(doc["path"]) for item in list_sessions(store)["items"] if item["id"] != closing
            for doc in item["documents"]}
    return [icdb.still_served(folder) for folder in sorted(folders - held) if not icdb.wait_released(folder)]


def close_session(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    with _driving(store, _ref(request["session"]), "session close"):
        ref, item = _session(store, request["session"])
        _app(ref.kind).quit(ref.pid, bool(request.get("discard")))
    for doc in item["documents"]:
        forget(store, ref.id, doc["path"])
    store.set("owned", [s for s in _owned(store) if s != ref.id])
    out: dict[str, Any] = {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
                           "persistence": "unchanged" if not request.get("discard") else "not_applicable"}
    notices = release(store, [doc["path"] for doc in item["documents"]], ref.id)
    if notices:
        out["notices"] = notices
    return out


def open_doc(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    ref = _ref(request["session"])
    path = os.path.abspath(request["path"])
    want = ".prj" if ref.kind == DESIGNER else ".pcb"
    if not path.lower().endswith(want):
        raise AdapterError("E_VALIDATION", f"This session opens {want} files.", dispatch_state="not_started")
    with _driving(store, ref, "doc open"):
        already = any(os.path.normcase(d["path"]) == os.path.normcase(path) for d in _session(store, ref.id)[1]["documents"])
        _app(ref.kind).open_document(ref.pid, path)
    if not already:
        note_saved(store, ref.id, path)  # freshly opened from the file: clean; an open document keeps its state
        forget_writes(store, ref.id, path)
    return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
            "persistence": "not_applicable", "document": _document(store, ref.id, path)}


def design_data(path: str) -> str:
    """Where a save writes the design: a project's database folder (``icdb.dat`` is written when
    the project closes) or a board's ``Layout`` folder. Not the document's own file: Layout rewrites
    a board's ``.pcb`` whenever it opens it, and Designer its logs, saved or not (seen live). The
    document's folder when that folder is missing."""
    folder = os.path.dirname(os.path.abspath(path))
    data = os.path.join(folder, "database" if path.lower().endswith(".prj") else "Layout")
    return data if os.path.isdir(data) else folder


def disk_stamp(path: str) -> tuple[int, int]:
    """The newest modification time and the number of the files a save of the document writes."""
    newest, count = 0, 0
    data = design_data(path)
    for root, dirs, names in os.walk(data):
        dirs[:] = [d for d in dirs if d.lower() != "cdbsvr"]  # the database server's logs and addresses, written on open
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
    ref = _ref(request["session"])
    path = os.path.abspath(request["document"])
    with _driving(store, ref, "doc save"):
        _session(store, ref.id)
        before, started = disk_stamp(path), time.time_ns()
        app = _app(ref.kind)
        app.save_document(ref.pid, path)
        after = disk_stamp(path)  # Layout writes the board when it saves
        if jobs.in_worker():
            # Closing and opening the document again makes Xpedition write it out (Designer's
            # database server writes the project files only when the project closes), and the
            # read-back then reads what was saved (DCLI-057). Opening writes files too, so what
            # the save wrote is measured before the document opens again.
            app.close_document(ref.pid, path, False)
            forget_writes(store, ref.id, path)
            if ref.kind == DESIGNER:
                after = disk_stamp(path)
            app.open_document(ref.pid, path)
            if ref.kind == DESIGNER and after == before:
                # Another program with the project open, such as Layout with its board, keeps the
                # database server running, so closing writes nothing; the server writes the
                # project when Designer opens it again (seen live).
                after = disk_stamp(path)
    pending = doc_state(store, ref.id, path).get("changes", [])
    note_saved(store, ref.id, path)
    # The read-back says `saved` only when the save was seen to write the files (DCLI-057).
    reached = after != before and after[0] >= started - 2_000_000_000
    store.set(_doc_key(ref.id, path) + "-saved", {"changes": pending, "reached_disk": reached})
    return {"dispatch_state": "dispatched", "change": "not_applicable", "delivery": "not_applicable",
            "persistence": "saved", "document": _document(store, ref.id, path)}


def close_doc(store: AdapterStore, request: dict[str, Any]) -> dict[str, Any]:
    ref = _ref(request["session"])
    path = os.path.abspath(request["document"])
    with _driving(store, ref, "doc close"):
        _session(store, ref.id)
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
        out = _app(ref.kind).read_targets(ref.pid, path, targets, fields, store)
    if "saved" in request.get("fields", []):
        mark = store.get(_doc_key(ref.id, path) + "-saved", {}) or {}
        observed = out.setdefault("observed", {})
        key = next((k for k in observed if normalize(k) == normalize(doc)), doc)
        observed.setdefault(key, {})["saved"] = bool(mark.get("reached_disk"))
    return out
