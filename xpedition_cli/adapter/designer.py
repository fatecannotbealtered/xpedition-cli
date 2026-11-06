"""Designer sessions and the schematic commands: ``schematic get``, ``create``, ``check``, ``export``.

Designer (``viewdraw.exe``) is reached through COM as ``Viewdraw.Application``. Its automation
object cannot tell its process ID, and ``GetActiveObject`` binds whichever instance registered
itself, so a Designer is driven only while exactly one runs (knowledge
``designer-one-instance-only``). Every COM object belongs to the thread that attached it: work
that must stay bounded runs in a helper thread that attaches there itself (``_watched``,
``com.call_with_timeout``).

Facts a later reader needs, all measured on XPED2604 by the old implementation:

- ``IsProjectOpened`` can report false after a call timed out while the project is still open,
  and opening the project Designer already has is refused with 64185. The open project's path is
  read and believed first.
- After sheet operations (adding sheets, changing their size or border), ``GetActiveDesign``
  reports the schematic instead of the board design and ``DesignComponents`` fails with a type
  mismatch until the project is reopened (knowledge ``designer-reopen-after-sheet-changes``).
- ``SchematicSheetDocuments`` lists only the sheets open in a window; ``GetAvailableSheets``
  lists every sheet. ``SchematicSheetDocuments.Add`` fails with error 670; the New Sheet command
  (34165) creates a sheet and activates it.
- Every drawing call goes to the active view, and changing a sheet's size or border can bring
  another sheet's window to the front, so the active sheet is checked before each operation.
- A modal dialog holds an out-of-process call open until someone answers it.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from .._driver.targets import normalize, path_of
from . import base, sessions
from .base import DESIGNER, Context, Handler, need_input
from .plan import PlanError, pdf_pages, project_file, review_engine, verification
from .plan import schematic_layout as L
from .platform import com, dialogs, env, processes

APPLICATION = "Xpedition Designer"
IMAGE = "viewdraw.exe"
PROGID = "Viewdraw.Application"
NEW_SHEET_COMMAND = 34165
SELECT_ALL_COMMAND = 57642
DISP_E_TYPEMISMATCH = 0x80020005 - (1 << 32)
REOPEN = "designer-reopen-after-sheet-changes"
ONE_INSTANCE = "designer-one-instance-only"
PROMPTS = "designer-project-prompts"
SEVERAL = "Several Designer instances are running; close all but one."
SIZE_NAMES = ("A", "B", "C", "D", "E", "A4", "A3", "A2", "A1", "A0")
SCH2PDF_COLOUR = 1  # colour on white, as the old export
SIGN_IN_MARKERS = ("login", "log in", "sign in", "sign-in", "登录")
# Opening a project in a Designer that has none open: only "Cannot create project" is answered.
# The change-project question ("close all open documents?") is never answered here, because the
# open documents may hold changes nobody saved; it is reported to the person instead.
OPEN_PROMPTS = tuple(rule for rule in dialogs.DESIGNER_PROMPTS if rule[0] == "Cannot create project")

# A save question while closing with --discard is not answered by the tool: which button
# discards is not recorded, so the person is told what was asked for.
_DISCARD = " (the unsaved changes are to be discarded: answer No to a question about saving them)"
POLL_S = 0.5
# Opening, closing, saving and quitting run in the runtime's long-job worker (DCLI-065): they
# may take minutes, and these limits only tell a Designer that hangs from a slow one.
OPEN_TIMEOUT_S = 150.0  # opening a project took 54 to 80 s live, and longer after library changes
OPEN_SETTLE_S = 6.0  # the old open waited this long before reading the project
REOPEN_OPEN_TIMEOUT_S = 150.0
CLOSE_TIMEOUT_S = 120.0  # closing a project took 30 to 57 s live
SAVE_TIMEOUT_S = 120.0
QUIT_TIMEOUT_S = 60.0
QUIT_WAIT_S = 60.0
START_PROCESS_SHARE = 0.55  # of the start wait spent waiting for COM; the rest opens the project
VERIFY_WAIT_S = 15.0
READ_BUDGET_S = 25.0
SIZE_BUDGET_S = 10.0
EXPORT_TIMEOUT_S = 38.0

_VERSION: str | None = None


def __getattr__(name: str) -> Any:
    """``VERSION`` is read when asked for, so that importing the handler table reads no registry."""
    if name == "VERSION":
        return _version()
    raise AttributeError(name)


def _version() -> str:
    global _VERSION
    if _VERSION is None:
        try:
            _VERSION = env.version()
        except AdapterError:
            _VERSION = ""
    return _VERSION


def _settle(seconds: float) -> None:
    time.sleep(seconds)


# ------------------------------------------------------------------ COM members


def _get(obj: Any, name: str) -> Any:
    """A member that is a property on one release and a method on another; failures raise.

    Only a routine is called: a property that returns a COM object (``GetLocation`` returns a
    point) must not be called, since that would read the object's default member instead."""
    value = getattr(obj, name)
    if inspect.isroutine(value):
        return value()
    return value


def _try_get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        value = _get(obj, name)
    except Exception:  # noqa: BLE001 - the member is absent or unreadable
        return default
    return default if value is None else value


def _items(collection: Any) -> Iterator[Any]:
    """A COM collection's items: ``Count`` and a 1-based ``Item``."""
    if collection is None:
        return
    count = int(collection.Count)
    for index in range(1, count + 1):
        yield collection.Item(index)


def _strings(value: Any) -> list[str]:
    """Designer's ``IStringList``: ``GetCount()`` and a 1-based ``GetItem(i)``."""
    return [str(value.GetItem(index)) for index in range(1, int(value.GetCount()) + 1)]


def _point(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return int(value[0]), int(value[1])
    x, y = getattr(value, "X", None), getattr(value, "Y", None)
    return (int(x) if isinstance(x, (int, float)) else None, int(y) if isinstance(y, (int, float)) else None)


@contextmanager
def _com_errors(what: str, dispatch_state: str | None = None) -> Iterator[None]:
    """A COM failure inside the block becomes the ``AdapterError`` its number says."""
    try:
        yield
    except AdapterError:
        raise
    except Exception as exc:  # noqa: BLE001 - classified by its number
        raise com.to_adapter_error(exc, what, dispatch_state=dispatch_state or com.call_state(exc)) from None


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ------------------------------------------------------------------ processes and attaching


def _designer_processes() -> list[processes.ProcessInfo]:
    """The running Designers. The launcher in ``common/win64/bin`` has the same image name as
    the real program it starts, so a process run from that folder is not a Designer."""
    found = processes.processes(IMAGE)
    home = env.find_sdd_home()
    if home is None:
        return found
    launchers = os.path.normcase(str(home.joinpath(*env.BIN)))
    return [p for p in found if not p.path or os.path.normcase(os.path.dirname(p.path)) != launchers]


def _require_single(pid: int) -> None:
    pids = [p.pid for p in _designer_processes()]
    if pid not in pids:
        raise AdapterError("E_NOT_FOUND", f"Designer process {pid} is not running.", dispatch_state="not_started")
    if len(pids) > 1:
        raise AdapterError("E_BACKEND_UNAVAILABLE", SEVERAL + " COM reaches only one of them, and it cannot tell which.",
                           dispatch_state="not_started", knowledge=[ONE_INSTANCE])


def _attach_or_none() -> Any | None:
    """The running Designer's application object, bound to its own type library; None when no
    Designer is registered for automation."""
    obj = com.active_object(com.progids(PROGID))
    return None if obj is None else com.bind_typelib(obj)


def _attach() -> Any:
    app = _attach_or_none()
    if app is None:
        raise AdapterError("E_BACKEND_UNAVAILABLE", "No Designer is registered for automation: it is still starting, or it "
                           "was not started through the Xpedition launcher.", dispatch_state="not_started",
                           knowledge=["xpedition-start-through-launcher"])
    return app


def _connect(pid: int) -> Any:
    """Attach to the one running Designer, after checking that it is process ``pid``."""
    _require_single(pid)
    return _attach()


def _project_state(app: Any) -> tuple[bool | None, str]:
    """``(IsProjectOpened, the open project's path)``. The path is read whatever
    ``IsProjectOpened`` says: after a timed-out call it reports false with the project open."""
    with _com_errors("reading whether a project is open", "not_started"):
        opened = _try_get(app, "IsProjectOpened")
        try:
            path = str(_get(_get(app, "GetProjectData"), "GetProjectFilePath") or "")
        except Exception:
            if opened:
                raise
            path = ""
    return (opened if isinstance(opened, bool) else None), path


def _check_open(app: Any, prj: str) -> None:
    _, current = _project_state(app)
    if not current or not _same_path(current, prj):
        raise base.blocked(f"Designer has {current or 'no project'} open, not {prj}; open the project with doc open first.")


def _blocker(title: str) -> dict[str, Any]:
    """A session blocker for a window that waits for a person, with the entry that tells how to handle it."""
    action = _blocker_action(title)
    return {"action": action, "title": title,
            "knowledge": [PROMPTS] if action == "dismiss_dialog" else ["xpedition-license-sign-in"]}


def _blocker_action(title: str) -> str:
    lower = (title or "").lower()
    if any(marker in lower for marker in SIGN_IN_MARKERS):
        return "sign_in"
    if dialogs.is_licence_window(title):
        return "activate_license"
    return "dismiss_dialog"


# ------------------------------------------------------------------ bounded calls that watch for dialogs


@dataclass
class _Outcome:
    done: bool
    value: Any = None
    error: BaseException | None = None
    dialogs: list[dict[str, Any]] = field(default_factory=list)
    answered: list[str] = field(default_factory=list)


def _watched(pid: int, action: Callable[[Any], Any], *, rules: tuple[dialogs.Rule, ...] = (), timeout_s: float,
             stop_on_dialog: bool = True) -> _Outcome:
    """Run ``action(app)`` in a helper thread that attaches to Designer itself, answering the
    prompts ``rules`` covers meanwhile.

    It returns once the action ends, once a dialog no rule covers has stayed up for two polls
    (``stop_on_dialog``), or after ``timeout_s``. The helper is left running in the last two
    cases: a COM call cannot be cancelled, and the dialog holds it until a person answers."""
    box: dict[str, Any] = {}
    finished = threading.Event()

    def work() -> None:
        try:
            with com.initialized():
                box["value"] = action(_attach())
        except BaseException as exc:  # noqa: BLE001 - handed to the caller
            box["error"] = exc
        finally:
            finished.set()

    threading.Thread(target=work, name="xpedition-designer-call", daemon=True).start()
    end = time.monotonic() + timeout_s
    answered: list[str] = []
    previous: set[tuple[str, str]] = set()
    seen: list[dict[str, Any]] = []
    while not finished.wait(POLL_S):
        seen = []
        try:
            answered += dialogs.answer_prompts(rules, pid, blocking=seen)
        except Exception:  # noqa: BLE001 - a failed scan is tried again on the next poll
            seen = []
        current = {(d.get("title", ""), d.get("text", "")) for d in seen}
        if stop_on_dialog and current & previous:
            return _Outcome(False, dialogs=seen, answered=answered)
        previous = current
        if time.monotonic() > end:
            return _Outcome(False, dialogs=seen, answered=answered)
    return _Outcome(True, value=box.get("value"), error=box.get("error"), answered=answered)


def _must(outcome: _Outcome, what: str, timeout_s: float) -> Any:
    """The value of a finished call, or the error that says why it did not finish."""
    if outcome.done:
        if outcome.error is None:
            return outcome.value
        if isinstance(outcome.error, AdapterError):
            raise outcome.error
        raise com.to_adapter_error(outcome.error, what, dispatch_state=com.call_state(outcome.error))
    if outcome.dialogs:
        title = str(outcome.dialogs[0].get("title", ""))
        raise AdapterError("E_HUMAN_REQUIRED", f"Designer shows a dialog while {what}; a person must answer it, and the call "
                           "goes on once it is answered.", dispatch_state="dispatched", action=_blocker_action(title),
                           title=title, knowledge=[PROMPTS])
    raise AdapterError("E_TIMEOUT", f"Designer did not finish {what} within {timeout_s:g} s; run session get before anything "
                       "else to see whether a dialog holds it.", dispatch_state="dispatched", knowledge=[PROMPTS])


# ------------------------------------------------------------------ sessions (sessions.Application)


def _identity() -> dict[str, Any]:
    """What one attach tells, in the helper thread of ``instances``."""
    app = _attach_or_none()
    if app is None:
        return {"state": "starting", "documents": []}
    opened, path = _project_state(app)
    return {"state": "ready", "documents": [path] if path and opened is not False else []}


def _dialog_blocker(pid: int) -> dict[str, str] | None:
    """A visible prompt of Designer that waits for a person, as a session blocker."""
    licence = dialogs.licence_windows(pid)
    if licence:
        return _blocker(licence[0])
    try:
        found = dialogs.blocking_dialogs(pid)
    except Exception:  # noqa: BLE001 - an unreadable desktop shows no dialog
        return None
    if found:
        return _blocker(str(found[0].get("title", "")))
    return None


def _instance(pid: int, timeout_s: float) -> dict[str, Any]:
    out: dict[str, Any] = {"pid": pid, "state": "ready", "documents": [], "version": _version()}
    licence = dialogs.licence_windows(pid)
    if licence:  # a licence or sign-in window comes before COM registration; COM is not tried
        out.update(state="blocked", blocker=_blocker(licence[0]))
        return out
    try:
        out.update(com.call_with_timeout(_identity, timeout_s))
    except TimeoutError:
        out["state"] = "unresponsive"
    except AdapterError as exc:
        code = exc.data.get("code")
        out["state"] = {"E_BUSY": "busy", "E_BACKEND_UNAVAILABLE": "starting"}.get(code, "unresponsive")
        if code == "E_HUMAN_REQUIRED":
            out.update(state="blocked", blocker={"action": "activate_license", "title": "Xpedition automation licence",
                                                 "knowledge": ["xpedition-license-sign-in"]})
    except Exception:  # noqa: BLE001 - one Designer that cannot be read must not hide the others
        out["state"] = "unresponsive"
    if out["state"] != "blocked":
        blocker = _dialog_blocker(pid)
        if blocker is not None:
            out.update(state="blocked", blocker=blocker)
        elif out["state"] == "ready" and dialogs.processing_windows(pid):
            out["state"] = "busy"
    return out


def instances(timeout_s: float) -> list[dict[str, Any]]:
    """Every running Designer: ``{pid, state, documents, blocker, version}``. With several, none is
    attached: COM would reach one of them without telling which, so each is unreachable (DCLI-204)."""
    found = _designer_processes()
    if len(found) > 1:
        return [{"pid": p.pid, "state": "unreachable", "documents": [], "version": _version(), "reason": SEVERAL}
                for p in found]
    return [_instance(p.pid, timeout_s) for p in found]


def _new_designer(process: subprocess.Popen[Any], deadline: float) -> int | None:
    """The PID of the Designer the launcher started: a ``viewdraw.exe`` that is not the launcher
    (none ran before, or the start was refused)."""
    while time.monotonic() < deadline:
        fresh = [p for p in _designer_processes() if p.pid != process.pid]
        if fresh:
            children = [p for p in fresh if p.parent_pid == process.pid]
            return (children or fresh)[0].pid
        code = process.poll()
        if code not in (None, 0):
            raise AdapterError("E_NATIVE_FAILURE", f"The Designer launcher ended with exit code {code} and started no Designer.",
                               dispatch_state="dispatched", knowledge=["xpedition-start-through-launcher"])
        _settle(POLL_S)
    return None


def _wait_registered(pid: int, deadline: float) -> str:
    """Wait until the new Designer registers for automation: ready, blocked by a licence or
    sign-in window, or still starting when the time is up."""
    while time.monotonic() < deadline:
        if not processes.alive(pid):
            raise AdapterError("E_NATIVE_FAILURE", f"Designer (process {pid}) ended while it was starting.",
                               dispatch_state="dispatched")
        if dialogs.licence_windows(pid):
            return "blocked"
        try:
            if com.call_with_timeout(lambda: _attach_or_none() is not None, 3.0):
                return "ready"
        except (TimeoutError, AdapterError):
            pass
        _settle(1.0)
    return "starting"


def start(path: str | None, wait_s: float) -> dict[str, Any]:
    """Start Designer through its launcher (knowledge ``xpedition-start-through-launcher``) and open
    ``path`` in it; ``{pid, state}`` of the real Designer process."""
    running = _designer_processes()
    if running:
        raise base.blocked(f"Designer already runs (process {running[0].pid}); a second one could not be driven. Use the "
                           "running one: open the project in it with doc open.", knowledge=[ONE_INSTANCE])
    env.configure_environment()
    program = env.launcher("viewdraw")
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    begun = time.monotonic()
    try:
        process = subprocess.Popen([str(program)], cwd=str(program.parent), env=dict(os.environ), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError as exc:
        raise AdapterError("E_NATIVE_FAILURE", f"The Designer launcher could not be started: {exc}", dispatch_state="not_started") from None
    pid = _new_designer(process, begun + wait_s * START_PROCESS_SHARE)
    if pid is None:
        raise AdapterError("E_TIMEOUT", f"No Designer process appeared within {wait_s * START_PROCESS_SHARE:g} s of starting the "
                           "launcher; run session list to see whether it came up later.", dispatch_state="dispatched")
    state = _wait_registered(pid, begun + wait_s * START_PROCESS_SHARE)
    if state == "ready" and path:
        remaining = max(5.0, begun + wait_s - 3.0 - time.monotonic())
        try:
            # A fresh Designer has no project open, so its project questions are answered as before.
            _open_project(pid, path, dialogs.DESIGNER_PROMPTS, remaining, settle=False)
        except AdapterError as exc:
            # Designer runs either way; the session list that follows shows its state and
            # documents, and doc open can try the project again.
            state = {"E_HUMAN_REQUIRED": "blocked", "E_TIMEOUT": "busy"}.get(str(exc.data.get("code")), state)
    return {"pid": pid, "state": state}


def _open_project(pid: int, path: str, rules: tuple[dialogs.Rule, ...], timeout_s: float, *, settle: bool = True) -> None:
    def open_it(app: Any) -> Any:
        return app.OpenProject(path)

    _must(_watched(pid, open_it, rules=rules, timeout_s=timeout_s), f"opening {path}", timeout_s)
    if settle:
        _settle(OPEN_SETTLE_S)
    with com.initialized(), _com_errors(f"checking that {path} opened", "dispatched"):
        app = _attach()
        _, current = _project_state(app)
        if not current or not _same_path(current, path):
            raise AdapterError("E_NATIVE_FAILURE", f"Designer did not open {path}; it reports {current or 'no project'} open.",
                               dispatch_state="dispatched")
        _show_design(app)


def _show_design(app: Any) -> None:
    """Open sheet 1 of the board design's schematic (its ``RootBlock``) when the project opened
    without a view: drawing and reading go to the active view. Never ``Documents.Open`` with
    the design's name: in a project whose schematic has another name (a new project's design
    ``Board1`` has root block ``Schematic1``), Designer then creates an empty schematic named
    after the design (seen live)."""
    if _try_get(app, "ActiveView") is not None:
        return
    root = _root_block(_project_state(app)[1])
    if root:
        try:
            _sheet_documents(app).Open(root, "1")
            _settle(1.0)
        except Exception:  # noqa: BLE001 - a project without its view still reads
            pass


def _root_block(prj: str) -> str:
    """The schematic of the project's board design: ``KEY RootBlock`` in the design's section."""
    if not prj:
        return ""
    try:
        text = Path(prj).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    board = project_file.board_design(project_file.designs(text))
    if board is None:
        return ""
    section = re.search(rf"^SECTION {re.escape(board.name)}[ \t]*\r?\n(.*?)^ENDSECTION", text, re.M | re.S)
    found = re.search(r'^KEY RootBlock[ \t]+"([^"]*)"', section.group(1), re.M) if section else None
    return found.group(1) if found else ""


NEW_SCHEMATIC_COMMAND = 34158  # File > New > Schematic: creates the board design and its schematic
NEW_PROJECT_TIMEOUT_S = 60.0
FIRST_OPEN_TIMEOUT_S = 150.0  # a project's first open builds its database; it took more than 30 s live
NEW_SCHEMATIC_TIMEOUT_S = 60.0
LIBRARY_IGNORE = ("Templates", "Work", "LogFiles")  # layout templates (hundreds of MB) are copied by pcb create when needed


def new_project(pid: int, target: str, template: str, library: str, progress: Any) -> dict[str, Any]:
    """Create a project with Designer's ``NewProject`` from a setup template, give it its own copy
    of the template's central library, and create its board design with a schematic.

    Seen live on Xpedition Enterprise 2604: ``NewProject`` writes the project folder and its
    empty database without opening it; the first ``OpenProject`` builds the database and can
    take more than 30 s; ``File > New > Schematic`` then creates design ``Board1`` with root
    block ``Schematic1``. Creating the design with ``AddiCDBDesign`` instead leaves a design
    without a block, and Designer then reports "The block ... could not be found" again and again."""
    import shutil

    folder = os.path.dirname(target)
    progress(0, 5, "steps")

    def create(app: Any) -> Any:
        return app.NewProject(target, library, "", template)

    accepted = _must(_watched(pid, create, rules=dialogs.DESIGNER_PROMPTS, timeout_s=NEW_PROJECT_TIMEOUT_S),
                     f"creating {target}", NEW_PROJECT_TIMEOUT_S)
    if accepted is not True or not os.path.isfile(target):
        raise AdapterError("E_NATIVE_FAILURE", f"Designer's NewProject did not create {target} (it answered {accepted!r}).",
                           dispatch_state="dispatched")
    progress(1, 5, "steps")
    source = os.path.dirname(library)
    copy = os.path.join(folder, os.path.basename(source))
    try:
        shutil.copytree(source, copy, ignore=shutil.ignore_patterns(*LIBRARY_IGNORE))
        own = os.path.join(copy, os.path.basename(library))
        _point_library_at(target, library, own)
    except (OSError, shutil.Error) as exc:
        raise AdapterError("E_NATIVE_FAILURE", f"The central library could not be copied into the project: {exc}.",
                           dispatch_state="dispatched", change="partial", persistence="partial") from None
    progress(2, 5, "steps")
    _open_project(pid, target, dialogs.DESIGNER_PROMPTS, FIRST_OPEN_TIMEOUT_S)
    progress(3, 5, "steps")

    def new_schematic(app: Any) -> Any:
        app.ExecuteCommandByID(NEW_SCHEMATIC_COMMAND)
        return [str(name) for name in _strings(_get(_get(app, "GetProjectData"), "GetiCDBDesigns"))]

    designs = _must(_watched(pid, new_schematic, rules=dialogs.DESIGNER_PROMPTS, timeout_s=NEW_SCHEMATIC_TIMEOUT_S),
                    "creating the board design and its schematic", NEW_SCHEMATIC_TIMEOUT_S)
    progress(4, 7, "steps")
    save_document(pid, target)
    # Designer finds symbols and parts only in the partitions the design's section lists, and the
    # project file can be changed only while the project is closed: list the stock symbol
    # partitions a drawing uses (ground, no-connect, borders) and the library's PartQuest
    # partition, then open the project again. Opening and closing are slow (tens of seconds).
    progress(5, 7, "steps")

    def close(app: Any) -> Any:
        return app.CloseProject()

    _must(_watched(pid, close, timeout_s=FIRST_OPEN_TIMEOUT_S), f"closing {target} to list its partitions", FIRST_OPEN_TIMEOUT_S)
    listed = _list_partitions(target, os.path.dirname(own))
    progress(6, 7, "steps")
    _open_project(pid, target, dialogs.DESIGNER_PROMPTS, FIRST_OPEN_TIMEOUT_S)
    progress(7, 7, "steps")
    return {"designs": designs, "library": own, "listed": listed}


STOCK_SYMBOL_PARTITIONS = ("Globals", "builtin", "Borders")
DEFAULT_PARTITION = "PartQuest"


def _list_partitions(project: str, library_root: str) -> list[str]:
    """Add the stock symbol partitions and the PartQuest partition to the board design's lists,
    as far as the library holds them; the entries the project gained."""
    from . import library as lib

    root, path = Path(library_root), Path(project)
    sep = "\\"
    wanted = [("Symbols", f"SymbolLibs{sep}{name}") for name in (*STOCK_SYMBOL_PARTITIONS, DEFAULT_PARTITION)
              if (root / "SymbolLibs" / name).is_dir()]
    if (root / "PartsDBLibs" / f"{DEFAULT_PARTITION}.pdb").is_file():
        wanted.append(("PDBs", f"PartsDBLibs{sep}{DEFAULT_PARTITION}.pdb"))
    if (root / "CellDBLibs" / f"{DEFAULT_PARTITION}.cel").is_file():
        wanted.append(("2dCellLibraries", f"CellDBLibs{sep}{DEFAULT_PARTITION}.cel"))
    added = []
    for list_name, entry in wanted:
        if lib.ensure_prj_list(path, list_name, entry):
            added.append(f"{list_name}: {entry}")
    return added


def _point_library_at(project: str, stock: str, own: str) -> None:
    """Name the project's own copy of the library in the project file: ``KEY CentralLibrary``,
    and ``KEY DBCFile`` when it names the stock library's databook configuration."""
    with open(project, encoding="utf-8", errors="surrogateescape") as handle:
        lines = handle.read().splitlines(keepends=True)
    stock_dbc = os.path.splitext(os.path.basename(stock))[0] + ".dbc"
    own_dbc = os.path.join(os.path.dirname(own), stock_dbc)
    out = []
    for line in lines:
        newline = "\r\n" if line.endswith("\r\n") else "\n"
        if line.startswith("KEY CentralLibrary "):
            line = f'KEY CentralLibrary "{own}"{newline}'
        elif line.startswith("KEY DBCFile ") and line.rstrip().strip('"').endswith(stock_dbc) and os.path.isfile(own_dbc):
            line = f'KEY DBCFile "{own_dbc}"{newline}'
        out.append(line)
    with open(project, "w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
        handle.write("".join(out))


def open_document(pid: int, path: str) -> None:
    """Open ``path`` in the Designer that has no project open (NCA ``_ensure_project``), or do
    nothing when it already has ``path`` open. A different open project is never closed here."""
    _require_single(pid)
    with com.initialized():
        opened, current = _project_state(_attach())
    if current and _same_path(current, path):
        return  # opening the open project again is refused with 64185
    if current and opened is not False:
        raise base.blocked(f"Designer has another project open ({current}); close it with doc close first.")
    _open_project(pid, path, OPEN_PROMPTS, OPEN_TIMEOUT_S)


def save_document(pid: int, path: str) -> None:
    """Save every open document of the project: ``Documents.SaveAll`` (``ActiveDocument.Save``
    alone misses the other open sheets)."""
    _require_single(pid)
    with com.initialized():
        _check_open(_attach(), path)

    def save(app: Any) -> Any:
        return _get(app, "Documents").SaveAll()

    _must(_watched(pid, save, timeout_s=SAVE_TIMEOUT_S), f"saving {path}", SAVE_TIMEOUT_S)


def close_document(pid: int, path: str, discard: bool) -> None:
    """Close the project. Nothing is saved here: the runtime refuses to close unsaved changes
    unless ``discard`` asks to lose them."""
    _require_single(pid)
    with com.initialized():
        opened, current = _project_state(_attach())
    if not current or not _same_path(current, path):
        if opened:
            raise AdapterError("E_NOT_FOUND", f"Designer does not have {path} open; it has {current or 'another project'}.",
                               dispatch_state="not_started")
        return

    def close(app: Any) -> Any:
        return app.CloseProject()

    _must(_watched(pid, close, timeout_s=CLOSE_TIMEOUT_S), f"closing {path}{_DISCARD if discard else ''}", CLOSE_TIMEOUT_S)
    end = time.monotonic() + 5.0
    while True:
        with com.initialized():
            opened, current = _project_state(_attach())
        if opened is False or not current:
            return
        if time.monotonic() > end:
            raise AdapterError("E_NATIVE_FAILURE", f"Designer still reports {path} open after closing it.", dispatch_state="dispatched")
        _settle(POLL_S)


def reopen_document(pid: int, path: str) -> None:
    """Close and open the saved project, so that what is read comes from the files (used by
    ``doc save``'s read-back right after the save, so nothing is unsaved)."""
    close_document(pid, path, False)
    _require_single(pid)
    _open_project(pid, path, OPEN_PROMPTS, REOPEN_OPEN_TIMEOUT_S)


def quit(pid: int, discard: bool) -> None:  # noqa: A001 - the name sessions.Application gives
    """Quit Designer and check that the process went (NCA ``_quit_and_confirm``).

    ``Quit`` returns without quitting while a question is on screen ("close all open
    documents?" was left up by a timed-out call), so the process is watched, and a dialog that
    holds it is named."""
    _require_single(pid)

    def quit_it(app: Any) -> Any:
        return app.Quit()

    outcome = _watched(pid, quit_it, timeout_s=QUIT_TIMEOUT_S)
    gone = outcome.done and outcome.error is not None and com.error_code(outcome.error) == "E_BACKEND_UNAVAILABLE"
    if (outcome.done and outcome.error is not None and not gone) or (not outcome.done and outcome.dialogs):
        _must(outcome, f"quitting{_DISCARD if discard else ''}", QUIT_TIMEOUT_S)  # a lost connection is Designer going away
    end = time.monotonic() + QUIT_WAIT_S
    while processes.alive(pid) and time.monotonic() < end:
        _settle(POLL_S)
    if not processes.alive(pid):
        return
    blocker = _dialog_blocker(pid)
    if blocker is not None:
        raise AdapterError("E_HUMAN_REQUIRED", "Designer did not quit: a dialog waits for a person.", dispatch_state="dispatched",
                           action=blocker["action"], title=blocker["title"], knowledge=[PROMPTS])
    raise AdapterError("E_NATIVE_FAILURE", f"Designer (process {pid}) did not quit within {QUIT_WAIT_S:g} s.", dispatch_state="dispatched",
                       knowledge=[PROMPTS])


def read_targets(pid: int, path: str, targets: list[str], fields: list[str]) -> dict[str, Any]:
    """Read the fields of the targets ``schematic create`` names from the open project."""
    with com.initialized():
        app = _connect(pid)
        _check_open(app, path)
        return _read_fields(app, path, targets, fields, time.monotonic() + READ_BUDGET_S)


# ------------------------------------------------------------------ sheets


def _sheet_documents(app: Any) -> Any:
    return _get(app, "SchematicSheetDocuments")


def _schematic_name(app: Any) -> str:
    """The schematic that owns the sheets: the board design's root block (``Schematic1`` in a
    stock project), else the first schematic Designer lists."""
    root = _root_block(_project_state(app)[1])
    if root:
        return root
    docs = _sheet_documents(app)
    try:
        names = _strings(docs.GetAvailableSchematics())
        if names:
            return names[0]
    except Exception:  # noqa: BLE001 - the open windows tell it too
        pass
    for document in _items(docs):
        name = str(_try_get(document, "Name", "") or "")
        if "." in name:
            return name.rpartition(".")[0]
    return "Schematic1"


def _sheet_numbers(app: Any) -> list[int]:
    """Every sheet of the schematic, not just the ones open in a window."""
    docs = _sheet_documents(app)
    numbers = {int(item) for item in _strings(docs.GetAvailableSheets(_schematic_name(app))) if item.isdigit()}
    if not numbers:
        for document in _items(docs):
            number = str(_try_get(document, "Name", "") or "").rpartition(".")[2]
            if number.isdigit():
                numbers.add(int(number))
    return sorted(numbers)


def _active_sheet(app: Any) -> int | None:
    try:
        return int(app.ActiveView.Block.SheetNum)
    except Exception:  # noqa: BLE001 - no sheet window is active
        return None


def _find_sheet_document(app: Any, wanted: str) -> Any | None:
    for document in _items(_sheet_documents(app)):
        if str(_try_get(document, "Name", "") or "") == wanted:
            return document
    return None


def _activate_sheet(app: Any, number: int) -> None:
    """Bring sheet ``number``'s window to the front. ``SchematicSheetDocuments.Open`` activates a
    sheet only the first time; once its window exists it returns without switching, so the
    document is activated as well."""
    schematic = _schematic_name(app)
    wanted = f"{schematic}.{number}"
    document = _find_sheet_document(app, wanted)
    if document is None:
        _sheet_documents(app).Open(schematic, str(number))
        _settle(1.5)
        document = _find_sheet_document(app, wanted)
    if document is not None:
        document.Activate()
        _settle(0.8)


def _size_name(code: int) -> str:
    """A ``VdSheetSize`` code as the size's name: the plain, landscape (11-20) and portrait
    (21-30) families each run A, B, C, D, E, A4, A3, A2, A1, A0. A set landscape code reads
    back as its plain one."""
    for first in (0, 11, 21):
        if first <= code < first + len(SIZE_NAMES):
            return SIZE_NAMES[code - first]
    return f"code {code}"


def _sheet_sizes(app: Any, numbers: list[int], budget_s: float) -> dict[int, str]:
    """The size of each sheet: a sheet's size is read from its active view, so each sheet is
    brought to the front in turn and the sheet that was active is brought back. Sheets not
    reached within ``budget_s`` read as unknown."""
    sizes: dict[int, str] = {}
    original = _active_sheet(app)
    end = time.monotonic() + budget_s
    try:
        for number in numbers:
            if time.monotonic() > end:
                break
            try:
                if _active_sheet(app) != number:
                    _activate_sheet(app, number)
                if _active_sheet(app) == number:
                    sizes[number] = _size_name(int(app.ActiveView.Block.SheetSize))
            except Exception:  # noqa: BLE001 - this sheet's size stays unknown
                continue
    finally:
        if original is not None and _active_sheet(app) != original:
            try:
                _activate_sheet(app, original)
            except Exception:  # noqa: BLE001 - the read is done; the window order is cosmetic
                pass
    return {number: sizes.get(number, "unknown") for number in numbers}


# ------------------------------------------------------------------ reading the design


class _Unreadable(Exception):
    """The connectivity could not be read in the time or the way tried."""


@dataclass
class Snapshot:
    """The parts of the design with their pins and nets, as Designer reports them."""

    design: str
    components: list[dict[str, Any]]

    def nets(self) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for component in self.components:
            for pin in component["pins"]:
                if pin["net"] and pin["number"] is not None:
                    entry = found.setdefault(pin["net"], {"name": pin["net"], "unnamed": pin["unnamed"], "pins": set()})
                    entry["pins"].add(f"{component['refdes']}.{pin['number']}")
        return found

    def part_numbers(self) -> dict[str, str]:
        return {component["refdes"]: component["part_number"] for component in self.components}

    def project(self) -> dict[str, Any]:
        """The design in the shape ``review_engine`` reads."""
        nets = self.nets()
        return {
            "project": self.design,
            "components": [{"refdes": c["refdes"], "internal_part_no": c["part_number"],
                            "pins": [{"number": p["number"], "net": p["net"], "no_connect": p["no_connect"]} for p in c["pins"]]}
                           for c in self.components],
            "nets": [{"name": name, "unnamed": entry["unnamed"]} for name, entry in sorted(nets.items())],
            "connections": [{"net": name, "pins": sorted(entry["pins"])} for name, entry in sorted(nets.items())
                            if len(entry["pins"]) >= 2],
        }


def _net_name(net: Any) -> str:
    """A net's name. A Designer net carries no ``Name`` of its own on a live sheet: the name is
    the text of the label on one of its segments, ``net.GetLabel(segment).TextString``."""
    direct = _try_get(net, "Name")
    if direct:
        return str(direct)
    for segment in _items(_try_get(net, "GetSegments")):
        try:
            label = net.GetLabel(segment)
        except Exception:  # noqa: BLE001 - this segment carries no label
            continue
        if label is None:
            continue
        text = _try_get(label, "TextString") or _try_get(label, "Text")
        if text:
            return str(text)
    return ""


def _net_identity(net: Any) -> str:
    """The logical name of a net, its label, or Designer's own ``$<sheet>N<id>`` UID when it has
    no name: two pins joined by a bare wire share no name, but their UID tells the net apart."""
    logical = _try_get(net, "LogicalNetName")
    if logical:
        return str(logical)
    return _net_name(net) or str(_try_get(net, "UID", "") or "")


def _attribute(component: Any, name: str) -> str:
    for attribute in _items(_try_get(component, "Attributes")):
        if str(_get(attribute, "Name")) == name:
            return str(_get(attribute, "Value") or "")
    return ""


def _component_record(component: Any) -> dict[str, Any]:
    refdes = str(_try_get(component, "Refdes") or _try_get(component, "RefDes") or "")
    x, y = _point(_try_get(component, "GetLocation"))
    pins: list[dict[str, Any]] = []
    if refdes:
        for connection in _items(_get(component, "GetConnections")):
            pin = _get(connection, "CompPin")
            net = _try_get(connection, "Net")
            number = _get(pin, "Number")
            identity = _net_identity(net) if net is not None else ""
            px, py = _point(_try_get(pin, "GetLocation"))
            pins.append({"number": str(number) if number is not None else None, "net": identity or None,
                         "unnamed": identity.startswith("$"), "x": px, "y": py, "no_connect": False})
    return {"refdes": refdes, "x": x, "y": y, "part_number": _attribute(component, "Part Number") if refdes else "",
            "pins": pins}


def _snapshot(design: str, records: list[dict[str, Any]]) -> Snapshot:
    """Parts and their pins. Symbols without a reference designator are power, ground,
    no-connect and off-page marks; a no-connect sits exactly on the pin end it marks, so an
    unconnected pin whose end has such a mark is open on purpose."""
    marks = {(r["x"], r["y"]) for r in records if not r["refdes"] and r["x"] is not None}
    parts = [r for r in records if r["refdes"]]
    for record in parts:
        for pin in record["pins"]:
            if not pin["net"] and pin["x"] is not None:
                pin["no_connect"] = (pin["x"], pin["y"]) in marks
    return Snapshot(design, parts)


def _board_design(prj: str) -> str:
    try:
        text = Path(prj).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = project_file.board_design(project_file.designs(text))
    return found.name if found else ""


def _design_collection(app: Any, method: str, design: str) -> Any:
    """``DesignComponents`` and friends. Current releases want the design's root block (Board1
    resolves to Schematic1 through ``GetiCDBDesignRootBlock``); older ones took the name."""
    function = getattr(app, method)
    root: Any = design
    try:
        root = _get(app, "GetProjectData").GetiCDBDesignRootBlock(design) or design
    except Exception:  # noqa: BLE001 - the design name is tried as it is
        pass
    errors: list[BaseException] = []
    for args in (("", root, "-1", "STD", True), ("", root, "-1", "", True), ("", root, "", "", True),
                 ("", design), ("", design, "", "", True)):
        try:
            return function(*args)
        except Exception as exc:  # noqa: BLE001 - the next argument form is tried
            errors.append(exc)
    raise next((e for e in errors if com.hresult(e) is not None), errors[-1])


def _design_read_error(exc: BaseException, active: str, board: str) -> AdapterError:
    """Why the design could not be read. After sheet operations ``GetActiveDesign`` reports the
    schematic instead of the board design and the read fails with a type mismatch."""
    native = com.com_text(exc)
    if active and board and active != board:
        return base.blocked(f"Designer cannot read the design until the project is reopened: after sheets were added, "
                            f"removed or resized it reports {active} as the active design instead of {board}. Save, close and "
                            "open the project (doc save, doc close, doc open), then read again.", knowledge=[REOPEN],
                            native_message=native)
    if com.hresult(exc) == DISP_E_TYPEMISMATCH:
        return base.blocked("Designer could not read the design's components (type mismatch). Sheets changed since the "
                            "project was opened, or the schematic changed since it was last packaged: save, close and open the "
                            "project (doc save, doc close, doc open) and read again; if it persists, run packager run.",
                            knowledge=[REOPEN], native_message=native)
    return com.to_adapter_error(exc, "reading the design's components", dispatch_state="not_started")


def _design_snapshot(app: Any, prj: str, deadline: float | None = None) -> Snapshot:
    """The whole design through ``DesignComponents``."""
    board = _board_design(prj)
    active = str(_try_get(app, "GetActiveDesign", "") or "")
    design = board or active
    if not design:
        raise AdapterError("E_NOT_FOUND", "Designer reports no active design.", dispatch_state="not_started")
    try:
        records = []
        for component in _items(_design_collection(app, "DesignComponents", design)):
            if deadline is not None and time.monotonic() > deadline:
                raise _Unreadable("out of time")
            records.append(_component_record(component))
    except (_Unreadable, AdapterError):
        raise
    except Exception as exc:  # noqa: BLE001 - classified
        raise _design_read_error(exc, active, board) from None
    return _snapshot(design, records)


def _sheet_snapshot(app: Any, prj: str, deadline: float) -> Snapshot:
    """The design read sheet by sheet: each sheet is activated and the components of its block
    are read, nets joining across sheets by their label name.

    NEW AND UNPROVEN: no live run has tried it. It exists for the read right after schematic
    create, when ``DesignComponents`` fails until the project is reopened. Whether
    ``ActiveView.Block.Components`` and the sheet-level nets report the same names as the
    design-level read (power nets above all) is the first thing to check live."""
    design = _board_design(prj) or str(_try_get(app, "GetActiveDesign", "") or "")
    original = _active_sheet(app)
    records: list[dict[str, Any]] = []
    try:
        for number in _sheet_numbers(app):
            if time.monotonic() > deadline:
                raise _Unreadable("out of time")
            if _active_sheet(app) != number:
                _activate_sheet(app, number)
            if _active_sheet(app) != number:
                raise _Unreadable(f"sheet {number} could not be activated")
            block = app.ActiveView.Block
            records += [_component_record(component) for component in _items(_get(block, "Components"))]
    finally:
        if original is not None and _active_sheet(app) != original:
            try:
                _activate_sheet(app, original)
            except Exception:  # noqa: BLE001 - the read is done
                pass
    return _snapshot(design, records)


def _read_connectivity(app: Any, prj: str, deadline: float) -> Snapshot | None:
    """The design-level read; after sheet operations, the sheet-by-sheet read; None when neither works."""
    try:
        return _design_snapshot(app, prj, deadline)
    except AdapterError as exc:
        if REOPEN not in (exc.data.get("knowledge") or []):
            return None
    except Exception:  # noqa: BLE001 - unreadable
        return None
    try:
        return _sheet_snapshot(app, prj, deadline)
    except Exception:  # noqa: BLE001 - unreadable
        return None


NET_PATH = "/schematic/nets/"
COMPONENT_PATH = "/schematic/components/"


def _read_fields(app: Any, prj: str, targets: list[str], fields: list[str], deadline: float) -> dict[str, Any]:
    """``{observed, unreadable}`` for net targets (``pins``), component targets
    (``part_number``) and symbol files (``exists``)."""
    document = base.key(prj)
    wanted = set(fields) if fields else {"pins", "part_number", "exists"}
    observed: dict[str, dict[str, Any]] = {}
    unreadable: set[str] = set()
    nets: dict[str, str] = {}
    parts: dict[str, str] = {}
    for target in targets:
        # The object's path follows the project's key and a "#"; a folder name may hold a "#" too.
        head, rest = target[:len(document)], target[len(document):]
        domain = rest[1:] if rest.startswith("#") and normalize(head) == normalize(document) else ""
        if domain.startswith(NET_PATH) and "pins" in wanted:
            nets[target] = domain[len(NET_PATH):]
        elif domain.startswith(COMPONENT_PATH) and "part_number" in wanted:
            parts[target] = domain[len(COMPONENT_PATH):]
        elif "#" not in target and "exists" in wanted:
            path = path_of(target)
            if path:
                observed[target] = {"exists": os.path.isfile(path)}
    if nets or parts:
        snap = _read_connectivity(app, prj, deadline)
        if snap is None:
            unreadable.update(["pins"] if nets else [])
            unreadable.update(["part_number"] if parts else [])
        else:
            pins = {name: sorted(entry["pins"]) for name, entry in snap.nets().items()}
            numbers = snap.part_numbers()
            for target, name in nets.items():
                observed[target] = {"pins": pins.get(name, [])}
            for target, refdes in parts.items():
                observed[target] = {"part_number": numbers.get(refdes)}
    return {"observed": observed, "unreadable": sorted(unreadable)}


# ------------------------------------------------------------------ schematic get


class SchematicGet(Handler):
    command = "schematic get"

    def read(self, ctx: Context) -> dict[str, Any]:
        ref = ctx.need_session(DESIGNER)
        prj = ctx.document_path()
        with com.initialized(), _com_errors("reading the schematic", "not_started"):
            app = _connect(ref.pid)
            _check_open(app, prj)
            snap = _design_snapshot(app, prj)
            schematic = _schematic_name(app)
            numbers = _sheet_numbers(app)
            sizes = _sheet_sizes(app, numbers, SIZE_BUDGET_S)
        return {"design": snap.design, "sheets": [{"number": n, "name": f"{schematic}.{n}", "size": sizes[n]} for n in numbers],
                "components": len(snap.components), "nets": len(snap.nets())}


# ------------------------------------------------------------------ schematic create


@dataclass
class _Draw:
    """A planned drawing and what it needs before it can run."""

    prj: str
    design_file: str
    params: dict[str, Any]
    ops: list[dict[str, Any]]
    symbol_files: list[tuple[Path, str]]
    blocked_by: list[str]
    warnings: list[str]

    @property
    def partition(self) -> str:
        return str(self.params["library"])

    @property
    def sheets(self) -> list[int]:
        return [int(op["number"]) for op in self.ops if op.get("op") == "open_sheet"]

    @property
    def nets(self) -> dict[str, list[str]]:
        return {str(name): sorted(pins) for name, pins in self.params["verify"]["nets"].items()}

    @property
    def parts(self) -> list[dict[str, Any]]:
        return [op for op in self.ops if op.get("op") == "place_part"]


def _plan_error(exc: PlanError) -> AdapterError:
    code = exc.code if exc.code in ("E_VALIDATION", "E_NOT_FOUND") else "E_VALIDATION"
    return AdapterError(code, exc.message, dispatch_state="not_started")


def _read_design(path: str) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise AdapterError("E_NOT_FOUND", f"There is no design file {path}.", dispatch_state="not_started") from None
    except OSError as exc:
        raise AdapterError("E_VALIDATION", f"The design file {path} cannot be read: {exc.strerror}.", dispatch_state="not_started") from None
    try:
        design = json.loads(text)
    except ValueError as exc:
        raise AdapterError("E_VALIDATION", f"The design file {path} is not JSON: {exc}.", dispatch_state="not_started") from None
    if not isinstance(design, dict):
        raise AdapterError("E_VALIDATION", f"The design file {path} must hold one JSON object.", dispatch_state="not_started")
    return design


def _library_parts(prj: str, design: dict[str, Any]) -> dict[str, Any] | None:
    """The central-library parts the design's symbols name (``{"part": NUMBER}``), as the planner
    takes them: by number, with partition, symbol name, symbol file text and cell."""
    numbers = L.library_part_numbers(design)
    if not numbers:
        return None
    from . import library

    try:
        held = library.load_library(prj)
    except PlanError as exc:
        raise _plan_error(exc) from None
    found: dict[str, Any] = {}
    missing: list[str] = []
    for number in numbers:
        rows = held.find_parts(number)
        if len(rows) > 1:
            raise AdapterError("E_AMBIGUOUS", f"The central library holds part {number} in several partitions "
                               f"({', '.join(r['partition'] for r in rows[:8])}); keep one.", dispatch_state="not_started",
                               candidates=[{"part": number, "partition": r["partition"]} for r in rows])
        try:
            symbol = held.find_symbol(rows[0]["symbols"][0]) if rows and rows[0]["symbols"] else None
        except PlanError as exc:
            raise _plan_error(exc) from None
        try:
            text = Path(symbol["path"]).read_text(encoding="utf-8", errors="replace") if symbol and symbol.get("path") else None
        except OSError:
            text = None
        if text is None:
            missing.append(number)
            continue
        found[number] = {"partition": symbol["partition"], "symbol": symbol["name"], "text": text, "cell": rows[0]["cell"]}
    if missing:
        failed = ", ".join(f"{f.get('kind')} {f.get('partition') or ''}".strip() for f in held.failed[:8])
        raise AdapterError("E_VALIDATION", f"The design names parts the central library does not hold with a symbol: "
                           f"{', '.join(missing)}. Check them with part list and part get, or add them with part create."
                           + (f" The converters could not export {failed}, so they may be there." if failed else ""),
                           dispatch_state="not_started", knowledge=["library-converter-rules"] if failed else [])
    return found


def _list_values(text: str, name: str) -> list[str]:
    """The entries of every ``LIST name`` block of a ``.prj``."""
    values: list[str] = []
    for block in re.finditer(rf"^LIST {re.escape(name)}[ \t]*\r?\n(.*?)^ENDLIST", text, re.S | re.M):
        values += re.findall(r'^VALUE[ \t]+"([^"]*)"', block.group(1), re.M)
    return values


def _listed(entries: list[str], tail: str) -> bool:
    tail = tail.lower()
    return any(entry.replace("\\", "/").rstrip("/").lower().endswith(tail) for entry in entries)


def _unregistered(prj: str, ops: list[dict[str, Any]]) -> list[str]:
    """The ``.prj`` list entries the drawing needs and the project lacks. Designer finds symbols
    and parts only in the partitions its ``.prj`` lists in ``LIST Symbols`` and ``LIST PDBs``."""
    text = Path(prj).read_text(encoding="utf-8", errors="replace")
    symbols, pdbs = _list_values(text, "Symbols"), _list_values(text, "PDBs")
    used = sorted({str(op["library"]) for op in ops if op.get("op") in ("place_part", "place_symbol") and op.get("library")}
                  - {"builtin"})
    missing: list[str] = []
    for partition in used:
        if not _listed(symbols, f"SymbolLibs/{partition}"):
            missing.append(f"LIST Symbols: SymbolLibs\\{partition}")
        if partition != "Globals" and not _listed(pdbs, f"PartsDBLibs/{partition}.pdb"):
            missing.append(f"LIST PDBs: PartsDBLibs\\{partition}.pdb")
    return missing


def _symbol_folder(prj: str, partition: str) -> Path | None:
    library = project_file.central_library(Path(prj))
    return None if library is None else library.parent / "SymbolLibs" / partition / "sym"


def _symbol_plan(folder: Path, symbols: dict[str, str]) -> list[tuple[Path, str]]:
    """The symbol files to write, each as the next version of its name (``<name>.<n+1>``). A name
    whose current version already holds the same text is left alone. The planner names every
    symbol by its content, so a changed symbol arrives under a new name (knowledge
    ``designer-symbol-cache``)."""
    versions: dict[str, list[tuple[int, Path]]] = {}
    if folder.is_dir():
        for path in folder.iterdir():
            stem, _, number = path.name.rpartition(".")
            if stem and number.isdigit() and path.is_file():
                versions.setdefault(stem, []).append((int(number), path))
    planned: list[tuple[Path, str]] = []
    for name, text in sorted(symbols.items()):
        existing = sorted(versions.get(name, []))
        if existing:
            try:
                current = existing[-1][1].read_text(encoding="utf-8", errors="replace")
            except OSError:
                current = None
            if current is not None and current.replace("\r\n", "\n") == text.replace("\r\n", "\n"):
                continue
        path = folder / f"{name}.{existing[-1][0] + 1 if existing else 1}"
        if path.resolve().parent != folder.resolve():
            raise AdapterError("E_VALIDATION", f"The symbol name {name!r} is not a plain file name.", dispatch_state="not_started")
        planned.append((path, text))
    return planned


def _issue_text(issue: dict[str, Any]) -> str:
    where = f"sheet {issue.get('sheet')}" if issue.get("sheet") is not None else "the design"
    return f"{issue.get('check', 'issue')} on {where}: {issue.get('message', '')}".strip()


def _plan_draw(ctx: Context) -> _Draw:
    """Plan the drawing from ``--design``; nothing is changed."""
    prj = ctx.document_path()
    design_file = os.path.abspath(str(need_input(ctx, "design")))
    if not os.path.isfile(prj):
        raise AdapterError("E_NOT_FOUND", f"There is no project file {prj}.", dispatch_state="not_started")
    design = _read_design(design_file)
    parts = _library_parts(prj, design)
    try:
        params = L.plan_to_params(design, prj, parts)
    except PlanError as exc:
        raise _plan_error(exc) from None
    except (KeyError, TypeError, ValueError) as exc:
        raise AdapterError("E_VALIDATION", f"The design file cannot be planned: {type(exc).__name__}: {exc}.",
                           dispatch_state="not_started") from None
    # The old draw saved each sheet; in a session only doc save saves (DCLI-058).
    ops = [op for op in params["ops"] if op.get("op") != "save"]
    blocked: list[str] = []
    warnings: list[str] = []
    for issue in params["summary"].get("issues", []):
        # DS-15: a label box or symbol on another net's wire end makes Designer refuse the draw
        # (6031, 6035) minutes in, or merge two nets with nothing said.
        (blocked if issue.get("check") == "DS-15" else warnings).append(_issue_text(issue))
    files: list[tuple[Path, str]] = []
    symbols = dict(params.get("symbols") or {})
    if symbols:
        folder = _symbol_folder(prj, str(params["library"]))
        if folder is None:
            blocked.append("The project names no central library (KEY CentralLibrary), so the drawing's symbol files have "
                           "nowhere to go.")
        elif not str(folder).isascii():
            blocked.append(f"The symbol folder {folder} is not an ASCII path; Designer loads no new symbol from it. Copy the "
                           "project and its library to an ASCII path (knowledge xpedition-ascii-paths-only).")
        else:
            files = _symbol_plan(folder, symbols)
    missing = _unregistered(prj, ops)
    if missing:
        blocked.append(f"The project file does not list what the drawing uses ({'; '.join(missing)}). Designer finds symbols "
                       "and parts only in partitions its .prj lists, and the .prj can be changed only while the project is "
                       "closed: run doc save and doc close, register the partition with part create, then doc open, and run "
                       "schematic create again.")
    return _Draw(prj, design_file, params, ops, files, blocked, warnings)


def _net_key(prj: str, name: str) -> str:
    return base.key(prj, NET_PATH + name)


def _component_key(prj: str, refdes: str) -> str:
    return base.key(prj, COMPONENT_PATH + refdes)


class SchematicCreate(Handler):
    command = "schematic create"
    long_job = True

    def preview(self, ctx: Context) -> dict[str, Any]:
        ctx.need_session(DESIGNER)
        draw = _plan_draw(ctx)
        prj = draw.prj
        targets = [base.target(prj, "sheet", f"/schematic/sheets/{n}", label=f"sheet {n}") for n in draw.sheets]
        changes: list[dict[str, Any]] = []
        for name, pins in sorted(draw.nets.items()):
            targets.append(base.target(prj, "net", NET_PATH + name, label=name))
            changes.append(base.change(_net_key(prj, name), "pins", None, pins))
        for op in draw.parts:
            refdes = str(op["refdes"])
            targets.append(base.target(prj, "component", COMPONENT_PATH + refdes, label=refdes))
            changes.append(base.change(_component_key(prj, refdes), "part_number", None, str(op.get("part", ""))))
        for path, _ in draw.symbol_files:
            targets.append(base.target(str(path), "symbol_file", label=path.name))
            changes.append(base.change(base.key(str(path)), "exists", False, True))
        action = (f"Replace sheets {', '.join(map(str, draw.sheets))} of the open schematic with the drawing of "
                  f"{os.path.basename(draw.design_file)}: {len(draw.parts)} parts, {len(draw.nets)} named nets, "
                  f"{len(draw.ops)} operations; write {len(draw.symbol_files)} symbol files into "
                  f"SymbolLibs/{draw.partition}/sym. Every object on those sheets is deleted first, and nothing is saved: "
                  "run doc save afterwards.")
        if draw.warnings:
            action += f" The plan has {len(draw.warnings)} layout warnings, listed in the result."
        return base.preview(action, targets, changes, draw.blocked_by)

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        ref = ctx.need_session(DESIGNER)
        draw = _plan_draw(ctx)
        if draw.blocked_by:
            raise base.blocked(" ".join(draw.blocked_by))
        tick = _Progress(progress)
        with com.initialized():
            with _com_errors("attaching to Designer", "not_started"):
                app = _connect(ref.pid)
                _check_open(app, draw.prj)
                wire = _wire_kind()
            written = _write_symbols(draw.symbol_files)
            drawer = _Drawer(app, draw.partition, wire, written=bool(written))
            with _JobWatch(ref.pid, tick) as watch:
                drawer.watch = watch
                drawer.run(draw.ops, tick)
            warnings = draw.warnings + drawer.warnings + _after_draw_warnings(app, draw)
        result = {"sheets": draw.sheets, "parts": len(draw.parts), "nets": len(draw.nets), "operations": drawer.done,
                  "symbols_written": [str(path) for path in written], "warnings": warnings}
        return {"change": "applied", "persistence": "unchanged", "result": result}

    def read_back(self, ctx: Context) -> dict[str, Any]:
        """Read the connectivity from the open project, without reopening it: the design-level
        read, and when that fails as it does after sheet operations, the sheet-by-sheet read."""
        ref = ctx.need_session(DESIGNER)
        prj = ctx.document_path()
        targets = list(ctx.request.get("targets") or [])
        fields = list(ctx.request.get("fields") or [])
        with com.initialized(), _com_errors("reading the schematic back", "not_started"):
            app = _connect(ref.pid)
            _check_open(app, prj)
            return _read_fields(app, prj, targets, fields, time.monotonic() + READ_BUDGET_S)


def _wire_kind() -> Any:
    """``VdBusOrWire.VD_WIRE`` from the running Designer's type library."""
    return com.constants(["VD_WIRE"])["VD_WIRE"]


def _write_symbols(files: list[tuple[Path, str]]) -> list[Path]:
    """Write the drawing's symbol files: the persist effect of schematic create."""
    written: list[Path] = []
    for path, text in files:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise AdapterError("E_NATIVE_FAILURE", f"The symbol file {path} could not be written: {exc.strerror}.",
                               dispatch_state="dispatched" if written else "not_started",
                               change="partial" if written else "not_applied", persistence="unchanged") from None
        written.append(path)
    return written


def _after_draw_warnings(app: Any, draw: _Draw) -> list[str]:
    warnings: list[str] = []
    try:
        left = [n for n in _sheet_numbers(app) if n not in draw.sheets]
    except Exception:  # noqa: BLE001 - the note is left out
        left = []
    if left:
        warnings.append(f"Sheets {', '.join(map(str, left))} are not in the design and keep what was on them.")
    library = project_file.central_library(Path(draw.prj))
    if draw.parts and library is not None and not (library.parent / "PartsDBLibs" / f"{draw.partition}.pdb").is_file():
        warnings.append(f"The {draw.partition} parts database does not exist, so Designer draws each part's value itself, "
                        "once over the reference designator; create the parts with part create.")
    return warnings


class _Progress:
    """The job's progress, shared by the drawing loop and the dialog watch."""

    def __init__(self, progress: Any) -> None:
        self._progress = progress
        self._lock = threading.Lock()

    def __call__(self, done: int, total: int) -> None:
        with self._lock:
            self._progress(done, total, "operations")

    def blocked(self, action: str, message: str) -> None:
        with self._lock:
            self._progress.blocked(action, message)

    def unblocked(self) -> None:
        with self._lock:
            self._progress.unblocked()


class _JobWatch:
    """While the drawing runs: answer Designer's known prompts, and show the job ``blocked`` while
    a dialog no rule covers waits for a person (a missing parts database raises one), so that a
    drawing that stopped can be explained (DCLI-066)."""

    def __init__(self, pid: int, progress: _Progress, interval_s: float = 1.0) -> None:
        self.pid = pid
        self.progress = progress
        self.interval_s = interval_s
        self.titles: list[str] = []
        self.blocked = False
        self._last: set[tuple[str, str]] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _JobWatch:
        self._thread = threading.Thread(target=self._run, name="xpedition-designer-watch", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self.blocked:
            self.progress.unblocked()
            self.blocked = False

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.poll()

    def poll(self) -> None:
        seen: list[dict[str, Any]] = []
        try:
            dialogs.answer_prompts(dialogs.DESIGNER_PROMPTS, self.pid, blocking=seen)
        except Exception:  # noqa: BLE001 - a failed scan is tried again
            return
        current = {(str(d.get("title", "")), str(d.get("text", ""))) for d in seen}
        lasting = current & self._last  # up for two polls: not a window passing by
        self._last = current
        if lasting and not self.blocked:
            title = sorted(lasting)[0][0]
            if title not in self.titles:
                self.titles.append(title)
            self.progress.blocked(_blocker_action(title), f"Designer shows the dialog {title!r} and waits for a person.")
            self.blocked = True
        elif not current and self.blocked:
            self.progress.unblocked()
            self.blocked = False


class _Drawer:
    """Carries out the planner's drawing operations on the active sheet (NCA ``_draw``)."""

    def __init__(self, app: Any, partition: str, wire: Any, *, written: bool = False) -> None:
        self.app = app
        self.partition = partition
        self.wire = wire
        self.written = written
        self.sheet: int | None = None
        self.components: dict[str, Any] = {}
        self.warnings: list[str] = []
        self.done = 0
        self.total = 0
        self.watch: _JobWatch | None = None

    def run(self, ops: list[dict[str, Any]], tick: Callable[[int, int], None]) -> None:
        self.total = len(ops)
        for index, op in enumerate(ops):
            tick(index, self.total)
            kind = str(op.get("op", ""))
            try:
                self.step(op, kind)
            except AdapterError as exc:
                raise self._located(exc, index, op, kind) from None
            except Exception as exc:  # noqa: BLE001 - classified by its number
                raise self._located(com.to_adapter_error(exc, f"drawing a {kind}", dispatch_state="dispatched"), index, op, kind) from None
            self.done += 1
        tick(self.total, self.total)

    def _located(self, error: AdapterError, index: int, op: dict[str, Any], kind: str) -> AdapterError:
        """The error with where the drawing stopped; nothing is saved, so the sheets drawn so far
        hold a partial drawing until schematic create runs again."""
        what = kind + (f" {op['refdes']}" if op.get("refdes") else "")
        message = (f"{error} The drawing stopped at operation {index + 1} of {self.total} ({what}) on sheet {self.sheet}; "
                   f"{self.done} operations were drawn and nothing is saved. Fix the cause and run schematic create again, "
                   "which redraws every sheet.")
        if self.watch is not None and self.watch.titles:
            message += f" Designer showed: {', '.join(repr(t) for t in self.watch.titles)}."
        data = {k: v for k, v in error.data.items() if k not in ("code", "dispatch_state", "change", "persistence")}
        started = self.done > 0 or self.written
        return AdapterError(str(error.data.get("code", "E_NATIVE_FAILURE")), message, dispatch_state="dispatched",
                            change="partial" if started else "not_applied", persistence="unchanged", **data)

    def block(self) -> Any:
        return self.app.ActiveView.Block

    def step(self, op: dict[str, Any], kind: str) -> None:
        if kind == "open_sheet":
            _open_sheet(self.app, int(op["number"]))
            self.sheet = int(op["number"])
            return
        _ensure_sheet(self.app, self.sheet)
        handler = getattr(self, "op_" + kind, None)
        if handler is None:
            raise AdapterError("E_VALIDATION", f"The plan holds an unknown drawing operation {kind!r}.", dispatch_state="dispatched")
        handler(op)

    def op_wipe_sheet(self, op: dict[str, Any]) -> None:
        self.app.ExecuteCommandByID(SELECT_ALL_COMMAND)
        _settle(0.5)
        self.block().DeleteSelected(False)
        _settle(0.5)

    def op_set_sheet(self, op: dict[str, Any]) -> None:
        """Size first, border second: the border change alone leaves the page size as it was, and a
        size set straight after a border change was dropped on some sheets."""
        size = op.get("size")
        if size is not None:
            self.block().SheetSize = int(size)
            _settle(1.0)
            _ensure_sheet(self.app, self.sheet)
        border = str(op.get("border") or "")
        if border:
            self.block().ChangeBorder(border)
            _settle(1.0)
            _ensure_sheet(self.app, self.sheet)
        if size is None:
            return
        wanted = int(size)
        for _attempt in range(2):
            if L.sheet_size_taken(int(self.block().SheetSize), wanted):
                return
            self.block().SheetSize = wanted
            _settle(1.0)
            _ensure_sheet(self.app, self.sheet)
        held = int(self.block().SheetSize)
        if not L.sheet_size_taken(held, wanted):
            self.warnings.append(f"Sheet {self.sheet}: the page size did not take (Designer holds size code {held}, the design "
                                 f"asked for {wanted}); check the PDF's pages after schematic export.")

    def op_place_part(self, op: dict[str, Any]) -> None:
        component = self.block().AddPartInstance(str(op.get("library") or self.partition), str(op.get("part", "")),
                                                 str(op["symbol"]), int(op["x"]), int(op["y"]))
        if int(op.get("orientation", 0)):
            component.Orientation = int(op["orientation"])
        component.Refdes = str(op["refdes"])
        self.components[str(op["refdes"])] = component
        wanted = {str(a["name"]): a for a in op.get("attributes") or [] if a.get("name")}
        if not wanted:
            return
        for attribute in _items(_try_get(component, "Attributes")):
            spec = wanted.get(str(_try_get(attribute, "Name", "")))
            if spec is None:
                continue
            if spec.get("orientation") is not None:
                attribute.Orientation = int(spec["orientation"])
            # Which corner of the text sits at the location. Set before the location: a symbol's
            # refdes arrives right-anchored, so the planned x would otherwise be where it ends.
            if spec.get("origin") is not None:
                attribute.Origin = int(spec["origin"])
            if spec.get("x") is not None and spec.get("y") is not None:
                attribute.SetLocation(int(spec["x"]), int(spec["y"]))

    def op_place_symbol(self, op: dict[str, Any]) -> None:
        instance = self.block().AddSymbolInstance(str(op.get("library") or self.partition), str(op["symbol"]),
                                                  int(op["x"]), int(op["y"]))
        if int(op.get("orientation", 0)):
            instance.Orientation = int(op["orientation"])

    def _pin(self, reference: Any) -> Any:
        if not reference:
            return None
        refdes, separator, number = str(reference).partition(".")
        component = self.components.get(refdes)
        if not separator or component is None:
            raise AdapterError("E_VALIDATION", f"The pin {reference!r} names no part this drawing placed.", dispatch_state="dispatched")
        matches = [pin for pin in (_get(c, "CompPin") for c in _items(_get(component, "GetConnections")))
                   if str(_get(pin, "Number")) == number]
        if len(matches) != 1:
            raise AdapterError("E_NATIVE_FAILURE", f"Designer shows {len(matches)} pins {number} on {refdes}, not one.",
                               dispatch_state="dispatched", knowledge=["designer-pin-on-wire-corner"])
        return matches[0]

    def op_wire(self, op: dict[str, Any]) -> None:
        """An orthogonal polyline: one ``AddNet`` per segment, the first segment attached to the
        start pin and the last to the end pin; a label goes on the first segment."""
        points = [(int(p[0]), int(p[1])) for p in op.get("points", [])]
        if len(points) < 2:
            raise AdapterError("E_VALIDATION", "A wire needs at least two points.", dispatch_state="dispatched")
        start, end = self._pin(op.get("start")), self._pin(op.get("end"))
        block = self.block()
        net = None
        last = len(points) - 2
        for index, ((ax, ay), (bx, by)) in enumerate(zip(points, points[1:], strict=False)):
            created = block.AddNet(ax, ay, bx, by, start if index == 0 else None, end if index == last else None, self.wire)
            net = net if net is not None else created
        label = op.get("label")
        if label and net is not None:
            segment = next(iter(_items(_get(net, "GetSegments"))), None)
            if segment is None:
                raise AdapterError("E_NATIVE_FAILURE", "The new wire has no segment to label.", dispatch_state="dispatched")
            net.AddLabel(segment, str(label["net"]), int(label["x"]), int(label["y"]))

    def op_box(self, op: dict[str, Any]) -> None:
        self.block().AddBox(int(op["x1"]), int(op["y1"]), int(op["x2"]), int(op["y2"]))

    def op_text(self, op: dict[str, Any]) -> None:
        text = self.block().AddText(str(op["text"]), int(op["x"]), int(op["y"]))
        if int(op.get("size", 0) or 0):
            text.Size = int(op["size"])


def _open_sheet(app: Any, number: int) -> None:
    """Make sheet ``number`` active, creating sheets through Designer's New Sheet command."""
    known = _sheet_numbers(app)
    attempts = 0
    while number not in known and attempts < 4:
        app.ExecuteCommandByID(NEW_SHEET_COMMAND)
        _settle(1.5)
        known = _sheet_numbers(app)
        attempts += 1
    if number not in known:
        raise AdapterError("E_NATIVE_FAILURE", f"Sheet {number} could not be created; the schematic has sheets "
                           f"{', '.join(map(str, known)) or 'none'}.", dispatch_state="dispatched")
    if _active_sheet(app) != number:
        _activate_sheet(app, number)
    if _active_sheet(app) != number:
        raise AdapterError("E_NATIVE_FAILURE", f"Sheet {number} could not be brought to the front.", dispatch_state="dispatched")


def _ensure_sheet(app: Any, number: int | None) -> None:
    """Bring sheet ``number`` back to the front when another sheet window took over."""
    if number is None or _active_sheet(app) == number:
        return
    _activate_sheet(app, number)
    if _active_sheet(app) != number:
        raise AdapterError("E_NATIVE_FAILURE", f"Sheet {number} is not the active sheet: another sheet window took over.",
                           dispatch_state="dispatched")


# ------------------------------------------------------------------ schematic check


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _finding(item: dict[str, Any]) -> dict[str, Any]:
    """A review or verification finding in the command's shape."""
    severity = {"high": "error", "medium": "warning"}.get(str(item.get("severity")), "info")
    rule = str(item.get("source") or "")
    objects = [str(o) for o in (item.get("refdes"), item.get("net")) if o]
    if rule.startswith("cli/"):
        objects += [str(e) for e in item.get("evidence") or [] if str(e) not in objects]
    message = str(item.get("finding") or "")
    if item.get("suggestion"):
        message += f" ({item['suggestion']})"
    digest = hashlib.sha256(json.dumps([rule, message, objects], ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
    return {"id": f"F-{digest}", "severity": severity, "rule": rule, "message": message, "objects": objects}


def _note(text: str) -> dict[str, Any]:
    return {"severity": "info", "refdes": None, "net": None, "source": "xpedition-cli/incomplete", "finding": text, "evidence": []}


def _run_verification(pid: int, vdrc: Path, grc: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Designer's Full Verification (34155): its findings, notes on what was not seen, and
    whether Designer is free again. The wait is bounded: a verification that takes longer is
    reported as not finished."""
    before = {path: _mtime(path) for path in (vdrc, grc)}
    started = time.monotonic()

    def verify(app: Any) -> Any:
        return app.ExecuteCommandByID(verification.FULL_VERIFICATION_COMMAND)

    outcome = _watched(pid, verify, timeout_s=VERIFY_WAIT_S)
    if outcome.done and outcome.error is not None:
        _must(outcome, "starting Designer's verification", VERIFY_WAIT_S)
    notes: list[dict[str, Any]] = []
    if not outcome.done and outcome.dialogs:
        title = str(outcome.dialogs[0].get("title", ""))
        notes.append(_note(f"Designer shows the dialog {title!r} during its verification; a person must answer it."))
    while _mtime(vdrc) <= before[vdrc] and time.monotonic() - started < VERIFY_WAIT_S + 2.0:
        _settle(POLL_S)
    findings: list[dict[str, Any]] = []
    if _mtime(vdrc) > before[vdrc]:
        _settle(POLL_S)  # let Designer finish writing the log
        findings += verification.parse_vdrc(env.tool_text(vdrc.read_bytes()))
    else:
        notes.append(_note(f"Designer's verification did not write LogFiles/vdrc.log within {VERIFY_WAIT_S:g} s; its findings "
                           "are not included. Run schematic check again once Designer is idle."))
    if _mtime(grc) > before[grc]:
        findings += verification.parse_grc(env.tool_text(grc.read_bytes()))
    return findings, notes, outcome.done


class SchematicCheck(Handler):
    command = "schematic check"

    def read(self, ctx: Context) -> dict[str, Any]:
        ref = ctx.need_session(DESIGNER)
        prj = ctx.document_path()
        started = time.monotonic()
        with com.initialized(), _com_errors("checking the schematic", "not_started"):
            _check_open(_connect(ref.pid), prj)
        logs = Path(prj).parent / "LogFiles"
        designer, notes, free = _run_verification(ref.pid, logs / "vdrc.log", logs / "grc.log")
        project: dict[str, Any] = {"project": "", "components": [], "nets": [], "connections": []}
        remaining = READ_BUDGET_S - (time.monotonic() - started)
        if not free or remaining < 2.0:
            notes.append(_note("Designer was still busy, so this tool's netlist rules did not run."))
        else:
            try:
                project = com.call_with_timeout(lambda: _design_snapshot(_attach(), prj).project(), remaining)
            except TimeoutError:
                notes.append(_note("Designer did not return the design in time, so this tool's netlist rules did not run."))
            except AdapterError as exc:
                notes.append(_note(f"This tool's netlist rules did not run: {exc}"))
        review = review_engine.run_review(project, designer + notes)
        findings = [_finding(item) for item in review["findings"]]
        return {"findings": findings, "errors": sum(f["severity"] == "error" for f in findings),
                "warnings": sum(f["severity"] == "warning" for f in findings), "complete": not notes,
                "logs": [str(logs / "vdrc.log"), str(logs / "grc.log")]}


# ------------------------------------------------------------------ schematic export


def _printed_sheets(stdout: str) -> list[str]:
    """``sch2pdf`` reports every sheet it renders as ``Printed <schematic> Sheet <n>``."""
    return [line.strip()[len("Printed "):] for line in stdout.splitlines() if line.strip().startswith("Printed ")]


def _export_paths(ctx: Context) -> tuple[str, str]:
    prj = ctx.document_path()
    out = os.path.abspath(str(need_input(ctx, "output")))
    if not out.lower().endswith(".pdf"):
        raise AdapterError("E_VALIDATION", f"--output must name a .pdf file, not {out}.", dispatch_state="not_started")
    if not os.path.isfile(prj):
        raise AdapterError("E_NOT_FOUND", f"There is no project file {prj}.", dispatch_state="not_started")
    return prj, out


def _unsaved(ctx: Context, prj: str) -> bool:
    session = ctx.request.get("session")
    return bool(session) and bool(sessions.doc_state(ctx.store, session, prj).get("dirty"))


class SchematicExport(Handler):
    """``sch2pdf`` renders the project database headless, so it works while Designer has the
    project open; it reads what was last saved, and prints only what lies inside each sheet's
    border. Designer's own Generate PDF command (34622) is a dialog."""

    command = "schematic export"

    def preview(self, ctx: Context) -> dict[str, Any]:
        prj, out = _export_paths(ctx)
        blocked: list[str] = []
        try:
            env.launcher("sch2pdf")
        except AdapterError as exc:
            blocked.append(str(exc))
        exists = os.path.isfile(out)
        action = f"Export the schematic of {prj} to {out} with sch2pdf, in colour on white"
        action += ", replacing that file." if exists else "."
        if _unsaved(ctx, prj):
            action += " Designer holds unsaved changes; the PDF shows the project as last saved (doc save first)."
        changes = [base.change(base.key(out), "exists", exists, True)]
        return base.preview(action, [base.target(out, "file", label=os.path.basename(out))], changes, blocked)

    def apply(self, ctx: Context) -> dict[str, Any]:
        prj, out = _export_paths(ctx)
        folder = Path(out).parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AdapterError("E_VALIDATION", f"The folder {folder} cannot be created: {exc.strerror}.", dispatch_state="not_started") from None
        # Written beside the target first, so that a failed run leaves an existing file as it was.
        partial = folder / f"{Path(out).stem}.partial-{uuid.uuid4().hex[:8]}.pdf"
        try:
            run = env.run_tool("sch2pdf", ["-project", prj, "-a", str(partial), "-c", str(SCH2PDF_COLOUR)], timeout_s=EXPORT_TIMEOUT_S)
            self._check(run, partial)
            os.replace(partial, out)
        finally:
            partial.unlink(missing_ok=True)
        found = pdf_pages.pages(Path(out).read_bytes())
        printed = _printed_sheets(run.stdout)
        pages = [{"sheet": printed[i] if i < len(printed) else f"page {page['page']}", "width_mm": page["width_mm"],
                  "height_mm": page["height_mm"]} for i, page in enumerate(found)]
        warnings = pdf_pages.warnings(found)
        if _unsaved(ctx, prj):
            warnings.append("Designer holds unsaved changes; the PDF shows the project as last saved.")
        warnings += [f"sch2pdf showed the dialog {d['title']!r}: {d['text']}" for d in run.dialogs]
        result = {"path": out, "pages": pages, "warnings": warnings}
        return base.facts("not_applicable", "saved", result=result, files=[{"path": out}])

    @staticmethod
    def _check(run: env.ToolRun, partial: Path) -> None:
        if run.blocked_by_license:
            raise AdapterError("E_HUMAN_REQUIRED", "sch2pdf waits for a licence or sign-in; a person must complete it.",
                               dispatch_state="dispatched", action="activate_license", title="sch2pdf",
                               knowledge=["xpedition-license-sign-in"])
        if run.timed_out:
            raise AdapterError("E_TIMEOUT", f"sch2pdf did not finish within {EXPORT_TIMEOUT_S:g} s and was ended.",
                               dispatch_state="dispatched")
        if run.exit_code != 0 or not partial.is_file() or partial.stat().st_size == 0:
            said = [line for line in (run.stdout + "\n" + run.stderr).splitlines() if line.strip()][-6:]
            said += [f"{d['title']}: {d['text']}" for d in run.dialogs]
            raise AdapterError("E_NATIVE_FAILURE", f"sch2pdf wrote no PDF (exit code {run.exit_code}).", dispatch_state="dispatched",
                               native_message=" | ".join(said)[-1500:])

    def read_back(self, ctx: Context) -> dict[str, Any]:
        observed = {}
        for target in ctx.request.get("targets") or []:
            path = path_of(target) if "#" not in target else None
            if path:
                observed[target] = {"exists": os.path.isfile(path)}
        return {"observed": observed, "unreadable": []}


HANDLERS: list[Handler] = [SchematicGet(), SchematicCreate(), SchematicCheck(), SchematicExport()]
