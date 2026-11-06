"""Layout sessions, and the pieces the board commands share.

Layout (``ExpeditionPCB.exe``) is reached through COM. Only an instance started through the
launcher registers in the running object table, and ``GetActiveObject`` reaches one instance
only, so one Layout at a time can be driven (``instances`` reports any other as blocked).

Every attach for work quietens Layout's trivial dialogs (``quiet_gui``) and takes the automation
licence token on the open board (``licensed``): Layout refuses automation calls on a document
that has not been validated, with product code 10279 (knowledge ``xpedition-license-sign-in``).

COM work runs in a helper thread with a deadline (``call``): a COM object belongs to the thread
that made it, so the thread attaches itself, and nothing COM leaves it. A call that runs out of
time reports the dialog that holds Layout, if one does, as ``E_HUMAN_REQUIRED``.

The board commands live in ``layout_board`` (forward annotation, outline, placement, planes,
routing) and ``layout_output`` (reading, checking and the fabrication package); ``HANDLERS``
collects them. Their target keys are read back by ``read_keys``, which ``read_targets`` also
uses after ``doc save`` has reopened the board.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote, unquote

from .._driver import AdapterError
from .._driver.targets import normalize
from . import base, jobs
from .plan import PlanError
from .platform import com, dialogs, env, processes

T = TypeVar("T")

APPLICATION = "Xpedition Layout"
PROCESS = "ExpeditionPCB.exe"
PROGID_BASES = ("MGCPCB.ExpeditionPCBApplication", "MGCPCB.Application")
LICENSING_PROGID = "MGCPCBAutomationLicensing.Application"
DESIGNER_PROGID = "Viewdraw.Application"
UNIT_MILS = 2  # EPcbUnit: 2 mils, 3 inch, 4 mm, 5 um
UNIT_MM = 4
TH_TO_MM = 0.0254  # geometry (PointsArray, CircleX...) comes in the document's unit; the templates count in mils
# Layout's write-only switches that keep trivial dialogs from holding an automation call open.
QUIET_SWITCHES = ("SuppressTrivialDialogs", "SuppressNotepadDialogs", "SuppressVariantDataOutOfDateDialog", "SingleThreaded")
OTHER_INSTANCE = "Another Layout instance is running; only one can be driven."
UNIDENTIFIED = "Several Layout instances are running and COM cannot tell which one it reaches; close all but one."

READ_BUDGET_S = 25.0  # read, preview and read_back calls end at 30 s
WRITE_BUDGET_S = 40.0  # apply calls end at 45 s
PROBE_S = 3.0
START_POLL_S = 1.0
OPEN_MIN_S = 10.0  # time a start needs left to open the board too
# Opening, closing, saving and quitting run in the runtime's long-job worker (DCLI-065): they
# may take minutes, and these limits only tell a Layout that hangs from a slow one.
LIFECYCLE_BUDGET_S = 150.0
JOB_BUDGET_S = 3600.0  # work in the long-job worker: this only tells a Layout that hangs from a slow one


def job_budget(normal_s: float) -> float:
    """The time a step may take: the long-job worker's limit there, else the call's."""
    return JOB_BUDGET_S if jobs.in_worker() else normal_s
QUIT_CALL_S = 60.0
QUIT_WAIT_S = 60.0
CLOSE_SETTLE_S = 0.5
READ_TARGETS_S = 8.0

ANNOTATION_KEY = "/forward-annotation"
OUTLINE_KEY = "/outline"
ROUTING_KEY = "/routing"
COMPONENTS_KEY = "/components"
NETS_KEY = "/nets"
PLANES_KEY = "/planes"
ANNOTATION_STATES = {1: "required", 2: "in_synch", 3: "no_data"}  # ProjectIntegration.ForwardAnnotationStatus
PLANE_DYNAMIC = 1  # PlaneAssignment.PlaneDataState; a Draft plane (3) generates no copper
PLANE_DRAFT = 3
PLANE_STATES = {1: "dynamic", 2: "static", 3: "draft"}
CANCEL_BUTTONS = frozenset({"cancel", "取消", "close", "关闭", "help", "帮助"})
SIGN_IN_MARKERS = ("sign", "login", "log in", "登录")
WS_MINIMIZEBOX = 0x00020000


def __getattr__(name: str) -> Any:
    """``VERSION`` and ``HANDLERS`` are computed on first use: the version reads the install,
    and the handler modules import this one."""
    if name == "VERSION":
        try:
            return env.version()
        except AdapterError:
            return ""
    if name == "HANDLERS":
        from . import layout_board, layout_output

        return [*layout_board.HANDLERS, *layout_output.HANDLERS]
    raise AttributeError(name)


# ------------------------------------------------------------------ small helpers


def same_path(left: Any, right: Any) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def items(collection: Any) -> list[Any]:
    """The items of a COM collection (one-based ``Item``); none for a missing collection."""
    if collection is None:
        return []
    return [collection.Item(index) for index in range(1, int(collection.Count) + 1)]


def collection(owner: Any, name: str) -> list[Any]:
    """A collection member's items. ``Components``, ``Nets`` and the like are parameterised
    properties whose parameters all default to "everything"; read bare, both the late-bound and
    the generated object accept them."""
    return items(com.member(owner, name))


def count(owner: Any, name: str) -> int | None:
    try:
        value = com.member(owner, name)
        return int(value.Count) if value is not None else 0
    except Exception:  # noqa: BLE001 - a count that cannot be read is not zero
        return None


def name_of(obj: Any) -> str:
    try:
        return str(obj.Name) if obj is not None else ""
    except Exception:  # noqa: BLE001 - an object without a name
        return ""


def net_name(obj: Any) -> str:
    try:
        return name_of(obj.Net)
    except Exception:  # noqa: BLE001 - no net
        return ""


def segment(name: str) -> str:
    """A name as one segment of a target key: ``/`` and ``#`` cannot stand in a net name there."""
    return quote(str(name), safe="+-_.~$!()*,;=:@")


def plane_key(net: str, layer: int) -> str:
    return f"{PLANES_KEY}/{segment(net)}/{int(layer)}"


def plan_error(exc: PlanError) -> AdapterError:
    code = "E_VALIDATION" if exc.code in ("E_USAGE", "E_CONFLICT", "E_VALIDATION") else exc.code
    return AdapterError(code, exc.message, dispatch_state="not_started")


def com_error(exc: BaseException, what: str, dispatch_state: str | None = None) -> AdapterError:
    """The AdapterError of a failed COM call; a licence refusal names the sign-in knowledge."""
    if isinstance(exc, AdapterError):
        return exc
    error = com.to_adapter_error(exc, what, dispatch_state=dispatch_state)
    if error.data.get("code") == "E_HUMAN_REQUIRED":
        error.data.setdefault("knowledge", ["xpedition-license-sign-in"])
    return error


def failed_step(exc: BaseException, what: str, done: list[str]) -> dict[str, Any]:
    """A step of a several-step write failed. Before any step took effect it is an error; after
    one did, the change is partial (a lost connection stays unknown)."""
    error = com_error(exc, what, dispatch_state=None if isinstance(exc, AdapterError) else com.call_state(exc))
    if not done:
        raise error
    if error.data.get("code") == "E_BACKEND_UNAVAILABLE":
        raise AdapterError("E_BACKEND_UNAVAILABLE", f"Layout was lost while {what}, after {', '.join(done)}.",
                           native_message=error.data.get("native_message"))
    return base.facts("partial", "unchanged", native_message=f"{what} failed after {', '.join(done)}: {error}")


def extrema_mm(obj: Any) -> tuple[float, float, float, float]:
    """An object's extents in mm, whatever the document's unit."""
    extrema = obj.Extrema
    return (float(extrema.GetMinX(UNIT_MM)), float(extrema.GetMinY(UNIT_MM)),
            float(extrema.GetMaxX(UNIT_MM)), float(extrema.GetMaxY(UNIT_MM)))


UNIT_SCALES = (0.0254, 25.4, 1.0, 0.001)  # mm per mil, inch, mm and um: the units a board may count in


def geometry_scale(doc: Any) -> float | None:
    """mm per unit of the raw geometry (PointsArray, CircleX, LineWidth), which comes in the board's
    current unit. It is measured on the board outline: its width in mm, which ``Extrema`` gives in
    any unit, over its width in raw units, taken as the nearest board unit. Without an outline, a
    board in mils (the stock templates) counts in mils; otherwise None."""
    try:
        outline = doc.BoardOutline
        if outline is not None:
            x0, _, x1, _ = extrema_mm(outline)
            points = outline.Geometry.PointsArray
            xs = [float(value) for value in points[0]] if points else []
            raw = max(xs) - min(xs) if xs else 0.0
            if raw > 0 and x1 > x0:
                ratio = (x1 - x0) / raw
                nearest = min(UNIT_SCALES, key=lambda unit: abs(ratio / unit - 1.0))
                return nearest if abs(ratio / nearest - 1.0) < 0.02 else None
    except Exception:  # noqa: BLE001 - the outline cannot be measured; fall back on the unit
        pass
    try:
        return TH_TO_MM if int(doc.CurrentUnit) == UNIT_MILS else None
    except Exception:  # noqa: BLE001 - the unit cannot be read
        return None


def board_rect(doc: Any) -> tuple[float, float, float, float]:
    """The board outline's extents in mm. ``Geometry.GetRect*`` answers only for a plain rectangle
    and calls a rounded outline "incorrect geometry", so the extents are read instead."""
    try:
        outline = doc.BoardOutline
        rect = extrema_mm(outline) if outline is not None else None
    except Exception as exc:  # noqa: BLE001 - classified
        raise com_error(exc, "reading the board outline", "not_started") from None
    if rect is None or rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise base.blocked("The board has no outline; set one with pcb update first.")
    return rect


def outline_radius(doc: Any, outline: Any) -> float | None:
    """The board outline's corner radius in mm: the arc-centre rows of its points array carry it.
    0 for square corners; None when the corners are rounded and the board's unit cannot be measured."""
    points = outline.Geometry.PointsArray
    radii = [abs(float(value)) for value in (points[2] if points else ())]
    biggest = max(radii, default=0.0)
    if biggest == 0:
        return 0.0
    scale = geometry_scale(doc)
    return round(biggest * scale, 4) if scale is not None else None


def find_net(doc: Any, name: str) -> Any | None:
    for net in collection(doc, "Nets"):
        if name_of(net) == name:
            return net
    return None


def find_component(doc: Any, refdes: str) -> Any | None:
    try:
        return doc.FindComponent(str(refdes))
    except Exception:  # noqa: BLE001 - not found reads as None
        return None


# ------------------------------------------------------------------ processes and attaching


def _progids() -> list[str]:
    return com.progids(*PROGID_BASES)


def _dispatch(progid: str) -> Any:
    try:
        import win32com.client
    except ImportError:
        raise AdapterError("E_BACKEND_UNAVAILABLE", "pywin32 is needed to reach Xpedition through COM, and it is not installed.",
                           dispatch_state="not_started") from None
    return win32com.client.Dispatch(progid)


def _is_launcher(proc: processes.ProcessInfo) -> bool:
    """The launcher stub in ``common/win64/bin`` has the same image name as Layout and stays alive
    while Layout runs; it is not an instance."""
    parts = [part.lower() for part in Path(proc.path).parts] if proc.path else []
    return parts[-4:-1] == ["common", "win64", "bin"]


def layout_processes() -> list[processes.ProcessInfo]:
    return [proc for proc in processes.processes(PROCESS) if not _is_launcher(proc)]


def app_pid(app: Any) -> int | None:
    """The process of the Layout COM reaches: its main window's owner (``Gui.HWND``), else the one
    live process running its executable, else the only Layout process."""
    try:
        pid = processes.window_pid(int(app.Gui.HWND))
        if pid:
            return pid
    except Exception:  # noqa: BLE001 - the next way is tried
        pass
    running = layout_processes()
    try:
        program = str(app.FullName or "")
    except Exception:  # noqa: BLE001 - no executable path
        program = ""
    if program:
        matches = [proc.pid for proc in running if proc.path and same_path(proc.path, program)]
        if len(matches) == 1:
            return matches[0]
    return running[0].pid if len(running) == 1 else None


def quiet_gui(app: Any) -> dict[str, bool]:
    """Set Layout's dialog suppression switches; an unattended run cannot answer a message box.
    Dialogs raised while Layout starts up come before automation and stay out of reach."""
    applied: dict[str, bool] = {}
    try:
        gui = app.Gui
    except Exception:  # noqa: BLE001 - no GUI object on this release
        return applied
    for switch in QUIET_SWITCHES:
        try:
            setattr(gui, switch, True)
            applied[switch] = True
        except Exception:  # noqa: BLE001 - an older release lacks the switch
            applied[switch] = False
    return applied


def licensed(app: Any) -> Any | None:
    """The active board with the automation licence token taken: ``Validate(0)`` gives a key,
    ``MGCPCBAutomationLicensing.Application.GetToken`` turns it into a token, ``Validate(token)``
    accepts it. None when no board is open."""
    try:
        doc = app.ActiveDocument
    except Exception as exc:  # noqa: BLE001 - classified
        raise com_error(exc, "reading Layout's active board", "not_started") from None
    if doc is None:
        return None
    try:
        key = doc.Validate(0)
        token = _dispatch(LICENSING_PROGID).GetToken(key)
        doc.Validate(token)
    except Exception as exc:  # noqa: BLE001 - classified
        raise com_error(exc, "taking the Layout automation licence token", "not_started") from None
    return doc


def attach(pid: int | None = None) -> Any:
    """Layout's application object in this thread, with its dialogs quietened. With ``pid``,
    refuse when COM reaches another Layout process than the session's."""
    app = com.active_object(_progids())
    if app is None:
        raise AdapterError("E_BACKEND_UNAVAILABLE", "No Layout is registered for automation: it is not running, or still "
                           "starting (Layout registers about a minute after its launcher). Check with session list.",
                           dispatch_state="not_started", knowledge=["xpedition-start-through-launcher"])
    if pid is not None:
        found = app_pid(app)
        if found is not None and found != int(pid):
            raise AdapterError("E_NOT_FOUND", f"COM reaches Layout process {found}, not the session's process {pid}; "
                               "only one Layout can be driven at a time.", dispatch_state="not_started")
    quiet_gui(app)
    return app


def active_path(app: Any) -> str:
    try:
        doc = app.ActiveDocument
        return str(doc.FullName or "") if doc is not None else ""
    except Exception as exc:  # noqa: BLE001 - classified
        raise com_error(exc, "reading Layout's active board", "not_started") from None


def board(app: Any, path: str) -> Any:
    """The licensed active board, which must be ``path``."""
    doc = licensed(app)
    if doc is None:
        raise base.blocked(f"Layout has no board open; open {path} with doc open first.")
    current = str(doc.FullName or "")
    if not same_path(current, path):
        raise base.blocked(f"Layout has {current} open, not {path}; name the open board with --doc.")
    return doc


# ------------------------------------------------------------------ dialogs and deadlines


def needs_person(dialog: dict[str, Any]) -> bool:
    """Whether a reported dialog waits for a person: a licence or sign-in window (reported without
    buttons), or one with a button other than Cancel, Close or Help. A progress box offers
    Cancel only and needs nobody."""
    buttons = dialog.get("buttons") or []
    if not buttons:
        return True
    names = {re.sub(r"\(.*?\)", "", str(button)).strip().lower() for button in buttons}
    return bool(names - CANCEL_BUTTONS)


def covered(dialog: dict[str, Any], rules: tuple[dialogs.Rule, ...] | list[dialogs.Rule]) -> bool:
    haystack = f"{dialog.get('title', '')} {dialog.get('text', '')}".lower()
    return any(marker.lower() in haystack for marker, _button, _option in rules)


def action_for(dialog: dict[str, Any]) -> str:
    if dialog.get("buttons"):
        return "dismiss_dialog"
    title = str(dialog.get("title") or "").lower()
    return "sign_in" if any(marker in title for marker in SIGN_IN_MARKERS) else "activate_license"


def blocker(dialog: dict[str, Any]) -> dict[str, Any]:
    """A session blocker for a window that waits for a person, with the entry that tells how to handle it."""
    action = action_for(dialog)
    return {"action": action, "title": str(dialog.get("title") or ""),
            "knowledge": ["layout-open-prompts"] if action == "dismiss_dialog" else ["xpedition-license-sign-in"]}


def human(dialog: dict[str, Any], what: str, dispatch_state: str | None) -> AdapterError:
    """E_HUMAN_REQUIRED for a dialog that holds Layout; its title goes in the details only."""
    action = action_for(dialog)
    if action == "dismiss_dialog":
        message = f"Layout shows a dialog that waits for a person while {what}; answer it in Layout, then go on."
        knowledge = ["layout-open-prompts"]
    else:
        message = f"Layout waits at a licence or sign-in window while {what}; only a person can complete it."
        knowledge = ["xpedition-license-sign-in"]
    return AdapterError("E_HUMAN_REQUIRED", message, dispatch_state=dispatch_state, action=action,
                        title=str(dialog.get("title") or ""), knowledge=knowledge)


def holding_dialog(pid: int | None, seen: list[dict[str, Any]] | None = None,
                   rules: tuple[dialogs.Rule, ...] = ()) -> dict[str, Any] | None:
    """The first dialog that holds Layout and waits for a person, if any."""
    found = list(seen or [])
    try:
        found.extend(dialogs.blocking_dialogs(pid))
    except Exception:  # noqa: BLE001 - the windows cannot be read
        pass
    for dialog in found:
        if needs_person(dialog) and not covered(dialog, rules):
            return dialog
    return None


def _drop_frames(exc: BaseException | None) -> None:
    """Forget the frames an exception keeps: they hold COM objects, which must be released while
    COM is still initialized in their thread."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        exc.__traceback__ = None
        if exc.__cause__ is not None:
            _drop_frames(exc.__cause__)
        exc = exc.__context__


def _clean(fn: Callable[[], T]) -> Callable[[], T]:
    def run() -> T:
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 - raised again, without the frames
            _drop_frames(exc)
            raise
    return run


def run_com(fn: Callable[[], T]) -> T:
    """Run a long job's COM work in this thread with COM initialized. An exception from ``fn`` is
    raised only once COM is uninitialized and the frames holding COM objects are gone."""
    error: BaseException | None = None
    value: Any = None
    with com.initialized():
        try:
            value = fn()
        except BaseException as exc:  # noqa: BLE001 - raised again below
            _drop_frames(exc)
            error = exc
    if error is not None:
        raise error
    return value


def call(fn: Callable[[], T], budget_s: float, pid: int | None, *, rules: tuple[dialogs.Rule, ...] = (),
         write: bool = False, what: str = "the call") -> T:
    """Run ``fn`` in a COM helper thread within ``budget_s``, with Layout's prompts answered by
    ``rules`` meanwhile. Out of time, the dialog that holds Layout is reported (E_HUMAN_REQUIRED),
    else E_TIMEOUT; for a write the outcome is then unknown."""
    answerer = dialogs.PromptAnswerer(rules, pid) if rules else None
    with answerer if answerer is not None else nullcontext():
        try:
            return com.call_with_timeout(_clean(fn), budget_s)
        except TimeoutError:
            state = None if write else "not_started"
            dialog = holding_dialog(pid, answerer.blocking_dialogs() if answerer is not None else None, rules)
            if dialog is not None:
                raise human(dialog, what, state) from None
            raise AdapterError("E_TIMEOUT", f"Layout did not finish {what} within {budget_s:g} s"
                               + ("; the change may have been made, so read the board before trying again." if write else "."),
                               dispatch_state=state) from None
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 - a COM fault the work did not classify
            raise com_error(exc, what, None if write else "not_started") from None


def in_layout(ctx: base.Context, work: Callable[[Any, Any], T], *, budget_s: float, what: str,
              rules: tuple[dialogs.Rule, ...] = (), write: bool = False) -> T:
    """Run ``work(app, doc)`` on the session's board, the request's document."""
    ref = ctx.need_session(base.LAYOUT)
    path = ctx.document_path()

    def body() -> T:
        app = attach(ref.pid)
        return work(app, board(app, path))

    return call(body, budget_s, ref.pid, rules=rules, write=write, what=what)


@contextmanager
def watch_dialogs(progress: Any, pid: int | None, rules: tuple[dialogs.Rule, ...], interval_s: float = 2.0) -> Iterator[None]:
    """While a long job waits in a COM call, show the job blocked when a dialog no rule answers
    waits for a person, and running again once it is gone."""
    stop = threading.Event()

    def loop() -> None:
        blocked = False
        while not stop.wait(interval_s):
            dialog = holding_dialog(pid, None, rules)
            if dialog is not None and not blocked:
                progress.blocked(action_for(dialog), f"Layout shows a dialog that waits for a person: {dialog.get('title', '')!r}")
                blocked = True
            elif dialog is None and blocked:
                progress.unblocked()
                blocked = False
        if blocked:
            progress.unblocked()

    thread = threading.Thread(target=loop, name="xpedition-layout-dialogs", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5.0)


# ------------------------------------------------------------------ sessions


def _probe() -> dict[str, Any]:
    """The Layout COM reaches: its process and its board. Light: no licence, no settings."""
    app = com.active_object(_progids())
    if app is None:
        return {"registered": False}
    doc = app.ActiveDocument
    return {"registered": True, "pid": app_pid(app), "documents": [str(doc.FullName)] if doc is not None else []}


def modal_box(pid: int) -> dict[str, Any] | None:
    """A standard dialog box (window class ``#32770``) over Layout that waits for a person.
    Layout goes on answering COM while such a box is up, so it is looked for even then; Layout's
    floating panels are Qt windows, never dialog boxes."""
    boxes = _quietly_list(lambda: [w for w in dialogs.top_windows(pid) if w.get("class") == "#32770" and w.get("owner")])
    return holding_dialog(pid) if boxes else None


def _quietly_list(fn: Callable[[], list[Any]]) -> list[Any]:
    try:
        return list(fn())
    except Exception:  # noqa: BLE001 - the windows cannot be read
        return []


def _held(pid: int, fallback: str) -> dict[str, Any]:
    """The state of a Layout process that does not answer: blocked by a dialog or a licence
    window, busy with a progress window, else ``fallback``."""
    dialog = holding_dialog(pid)
    if dialog is not None:
        return {"state": "blocked", "blocker": blocker(dialog)}
    try:
        if dialogs.processing_windows(pid):
            return {"state": "busy"}
    except Exception:  # noqa: BLE001 - the windows cannot be read
        pass
    return {"state": fallback}


def instances(timeout_s: float) -> list[dict[str, Any]]:
    """Every running Layout: the one COM reaches is ready with its board; any other is
    unreachable (DCLI-204), since COM cannot reach it (DCLI-205: each probe within ``timeout_s``)."""
    running = layout_processes()
    if not running:
        return []
    trouble = ""
    seen: dict[str, Any] | None = None
    try:
        seen = com.call_with_timeout(_probe, timeout_s)
    except TimeoutError:
        trouble = "unresponsive"
    except AdapterError as exc:
        trouble = "busy" if exc.data.get("code") == "E_BUSY" else "unresponsive"
    except Exception:  # noqa: BLE001 - a COM fault: the instance does not answer as it should
        trouble = "unresponsive"
    rows: list[dict[str, Any]] = []
    if seen is not None and seen.get("registered"):
        reached = seen.get("pid")
        if reached is None and len(running) == 1:
            reached = running[0].pid
        for proc in running:
            if proc.pid == reached:
                row: dict[str, Any] = {"pid": proc.pid, "state": "ready", "documents": list(seen.get("documents", []))}
                # A licence window is told by its title alone (no UI Automation); other windows are
                # dialog boxes only, since Layout's floating panels look like dialogs too.
                licence = _quietly_list(lambda pid=proc.pid: dialogs.licence_windows(pid))
                box = None if licence else modal_box(proc.pid)
                if licence:
                    row.update(state="blocked", blocker=blocker({"title": licence[0], "buttons": []}))
                elif box is not None:
                    row.update(state="blocked", blocker=blocker(box))
                rows.append(row)
            else:
                reason = OTHER_INSTANCE if reached is not None else UNIDENTIFIED
                rows.append({"pid": proc.pid, "state": "unreachable", "documents": [], "reason": reason})
        return rows
    for proc in running:
        # nothing registered yet means starting; a probe that failed means it does not answer
        rows.append({"pid": proc.pid, "documents": [], **_held(proc.pid, trouble or "starting")})
    return rows


def _started_pid(launcher_pid: int) -> int | None:
    """The Layout the launcher started: its child, else the only Layout (none ran before)."""
    running = layout_processes()
    children = set(processes.descendants(launcher_pid))
    for proc in running:
        if proc.pid in children:
            return proc.pid
    return running[0].pid if len(running) == 1 else None


def _registered() -> bool:
    try:
        return bool(com.call_with_timeout(lambda: com.active_object(_progids()) is not None, PROBE_S))
    except Exception:  # noqa: BLE001 - not yet
        return False


def start(path: str | None, wait_s: float) -> dict[str, Any]:
    """Start Layout through its launcher (COM activation without the launcher's environment fails,
    and only a launched Layout registers for automation), wait for it to register, then open
    ``path``. Layout takes about a minute to register: when ``wait_s`` runs out first, the
    session is returned ``starting`` and the board is opened later with doc open."""
    if layout_processes():
        raise base.blocked("Layout is already running, and COM can drive only one Layout; open the board in that session "
                           "with doc open, or close it with session close first.")
    env.configure_environment()
    program = env.launcher("ExpeditionPCB")
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        launcher = subprocess.Popen([str(program)], cwd=str(program.parent), env=dict(os.environ), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError as exc:
        raise AdapterError("E_NATIVE_FAILURE", f"The Layout launcher could not be started: {exc}", dispatch_state="not_started") from None
    deadline = time.monotonic() + wait_s
    pid: int | None = None
    registered = False
    while time.monotonic() < deadline:
        pid = pid or _started_pid(launcher.pid)
        code = launcher.poll()
        if code is not None and pid is None:
            raise AdapterError("E_NATIVE_FAILURE", f"The Layout launcher ended (exit code 0x{code & 0xFFFFFFFF:08X}) before Layout started.",
                               dispatch_state="dispatched", knowledge=["xpedition-start-through-launcher"])
        registered = pid is not None and _registered()
        if registered:
            break
        time.sleep(START_POLL_S)
    if pid is None:
        raise AdapterError("E_TIMEOUT", f"Layout did not start within {wait_s:g} s of its launcher; check session list.",
                           dispatch_state="dispatched")
    if not registered:
        return {"pid": pid, "state": "starting"}
    left = deadline - time.monotonic()
    if path and left >= OPEN_MIN_S:
        open_document(pid, path, budget_s=left)
    return {"pid": pid, "state": "ready"}


def quit(pid: int, discard: bool) -> None:
    """Quit Layout and check that its process went. The board is closed without saving first, as
    doc close does: the runtime has refused this tool's unsaved changes unless ``discard`` gives
    them up, and Layout asks to save a board on quit even right after it was saved (seen live).
    Layout's known prompts are answered meanwhile; one no rule covers is reported."""
    def work() -> None:
        app = attach(pid)
        doc = licensed(app)  # the licensed document: Layout refuses Close on an unlicensed one
        if doc is not None:
            try:
                doc.Close(False)
            except Exception as exc:  # noqa: BLE001 - classified
                raise com_error(exc, "closing the board without saving", com.call_state(exc)) from None
        try:
            app.Quit()
        except Exception as exc:  # noqa: BLE001 - classified
            raise com_error(exc, "quitting Layout", com.call_state(exc)) from None

    with dialogs.PromptAnswerer(dialogs.LAYOUT_PROMPTS, pid) as answerer:
        call(work, QUIT_CALL_S, pid, write=True, what="quitting Layout")
        _wait_gone(pid, QUIT_WAIT_S)
        seen = answerer.blocking_dialogs()
    if processes.alive(pid):
        dialog = holding_dialog(pid, seen, dialogs.LAYOUT_PROMPTS)
        if dialog is not None:
            raise human(dialog, "quitting", "dispatched")
        # Quit through automation leaves Layout on its start page, holding files (knowledge
        # layout-drillprefs-held). Its board was closed above and nothing else is open, so ending
        # the process loses nothing.
        if processes.end(pid):
            _wait_gone(pid, QUIT_WAIT_S)
    if processes.alive(pid):
        raise AdapterError("E_NATIVE_FAILURE", f"Layout was asked to quit, and its process {pid} still runs after {QUIT_WAIT_S:g} s; "
                           "it may stay on its start page holding files. Ask the person to end it.", dispatch_state="dispatched",
                           knowledge=["layout-drillprefs-held"])


def _wait_gone(pid: int, seconds: float) -> None:
    end = time.monotonic() + seconds
    while processes.alive(pid) and time.monotonic() < end:
        time.sleep(0.5)


def open_document(pid: int, path: str, budget_s: float = LIFECYCLE_BUDGET_S) -> None:
    """Open a board with Layout's open prompts answered (stale lock: Open; recovery: the saved
    database; "annotate now?": No). Refused while another board is open: opening one would raise
    Layout's Save All prompt."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise AdapterError("E_NOT_FOUND", f"There is no board {path}.", dispatch_state="not_started")

    def work() -> None:
        app = attach(pid)
        current = active_path(app)
        if current:
            if same_path(current, path):
                licensed(app)
                return
            raise base.blocked(f"Layout has another board open ({current}); close it with doc close first, since opening a "
                               "second board raises Layout's Save All prompt.", knowledge=["layout-open-prompts"])
        try:
            app.OpenDocument(path)
        except Exception as exc:  # noqa: BLE001 - classified
            raise com_error(exc, "opening the board", com.call_state(exc)) from None
        doc = licensed(app)
        if doc is None or not same_path(str(doc.FullName or ""), path):
            raise AdapterError("E_NATIVE_FAILURE", "Layout returned from OpenDocument without the board open.", dispatch_state="dispatched",
                               knowledge=["layout-open-prompts"])

    call(work, budget_s, pid, rules=dialogs.LAYOUT_PROMPTS, write=True, what="opening the board")


def save_document(pid: int, path: str) -> None:
    """``Document.Save``, which must answer True."""
    def work() -> None:
        doc = board(attach(pid), path)
        try:
            saved = doc.Save()
        except Exception as exc:  # noqa: BLE001 - classified
            raise com_error(exc, "saving the board", com.call_state(exc)) from None
        if saved is not True:
            raise AdapterError("E_NATIVE_FAILURE", f"Layout's Save answered {saved!r}, not True; the board may not be saved.",
                               dispatch_state="dispatched")

    call(work, LIFECYCLE_BUDGET_S, pid, rules=dialogs.LAYOUT_PROMPTS, write=True, what="saving the board")


def close_document(pid: int, path: str, discard: bool, budget_s: float = LIFECYCLE_BUDGET_S) -> None:
    """Close the board without saving (the caller has saved, or discards), then check it is gone.
    ``discard`` changes nothing here: the runtime refuses to close unsaved changes without it."""
    def work() -> None:
        app = attach(pid)
        doc = board(app, path)
        try:
            doc.Close(False)
        except Exception as exc:  # noqa: BLE001 - classified
            raise com_error(exc, "closing the board", com.call_state(exc)) from None
        time.sleep(CLOSE_SETTLE_S)
        current = active_path(app)
        if current and same_path(current, path):
            raise AdapterError("E_NATIVE_FAILURE", "Layout answered Close but still has the board open.", dispatch_state="dispatched")

    call(work, budget_s, pid, rules=dialogs.LAYOUT_PROMPTS, write=True, what="closing the board")


def reopen_document(pid: int, path: str) -> None:
    """Close the saved board and open it again, licensed, so that reads come from the file."""
    close_document(pid, path, False, budget_s=LIFECYCLE_BUDGET_S)
    open_document(pid, path, budget_s=LIFECYCLE_BUDGET_S)


def read_targets(pid: int, path: str, targets: list[str], fields: list[str]) -> dict[str, Any]:
    return call(lambda: read_keys(board(attach(pid), path), path, targets, fields), READ_TARGETS_S, pid,
                what="reading the board back")


# ------------------------------------------------------------------ the fields of the target keys


def annotation_state(doc: Any) -> dict[str, Any]:
    """The forward-annotation status (1 required, 2 in synch, 3 no CES data) and whether Layout
    allows forward annotation now. ``ProjectIntegration`` has no type information; its members
    are read as properties."""
    integration = com.member(doc, "ProjectIntegration")
    if integration is None:
        raise AdapterError("E_BACKEND_UNAVAILABLE", "This Layout offers no ProjectIntegration object.", dispatch_state="not_started")
    status = com.member(integration, "ForwardAnnotationStatus")
    allowed = com.member(integration, "IsForwardAnnotationAllowed")
    try:
        name = ANNOTATION_STATES.get(int(status), f"status {status}")
    except (TypeError, ValueError):
        name = f"status {status}"
    return {"status": name, "allowed": None if allowed is None else bool(allowed)}


def outline_values(doc: Any) -> dict[str, Any]:
    outline = doc.BoardOutline
    if outline is None:
        return {"width_mm": None, "height_mm": None, "radius_mm": None, "extents_mm": None}
    x0, y0, x1, y1 = extrema_mm(outline)
    try:
        radius = outline_radius(doc, outline)
    except Exception:  # noqa: BLE001 - the radius alone cannot be read
        radius = None
    return {"width_mm": round(x1 - x0, 4), "height_mm": round(y1 - y0, 4), "radius_mm": radius,
            "extents_mm": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]}


def component_values(component: Any) -> dict[str, Any]:
    """Where a component stands: its cell origin in mm and its rotation, or only ``placed``
    False (an unplaced part reports position 0, 0)."""
    if component is None:
        return {"placed": None, "x_mm": None, "y_mm": None, "rotation_deg": None}
    if not bool(component.Placed):
        return {"placed": False, "x_mm": None, "y_mm": None, "rotation_deg": None}
    return {"placed": True, "x_mm": round(float(component.GetPositionX(UNIT_MM)), 4),
            "y_mm": round(float(component.GetPositionY(UNIT_MM)), 4),
            "rotation_deg": round(float(component.Orientation or 0.0) % 360.0, 4)}


def routing_values(doc: Any) -> dict[str, Any]:
    return {"traces": count(doc, "Traces"), "vias": count(doc, "Vias")}


def route_status(doc: Any) -> dict[str, dict[str, Any]]:
    """Per net: ``routed`` (Layout calls it routed and it has no open connection) and ``opens``.
    ``IsRouted`` is a method in 151.0 and a property elsewhere."""
    rows: dict[str, dict[str, Any]] = {}
    for net in collection(doc, "Nets"):
        name = name_of(net)
        if not name:
            continue
        routed = com.member(net, "IsRouted")
        opens = int(net.NumberOfOpens or 0)
        rows[name] = {"routed": bool(routed) and opens == 0, "opens": opens}
    return rows


def plane_state(value: Any) -> str:
    try:
        return PLANE_STATES.get(int(value), f"state {value}")
    except (TypeError, ValueError):
        return f"state {value}"


def plane_values(doc: Any, net: str, layer: int) -> dict[str, Any]:
    exists = any(net_name(shape) == net and int(shape.Layer) == int(layer) for shape in collection(doc, "PlaneShapes"))
    state = None
    found = find_net(doc, net)
    if found is not None:
        try:
            assignment = doc.FindPlaneAssignment(int(layer), found)
            state = plane_state(assignment.PlaneDataState) if assignment is not None else None
        except Exception:  # noqa: BLE001 - no assignment for that net and layer
            state = None
    return {"exists": exists, "state": state}


def fragment(target: str, path: str) -> list[str] | None:
    """The domain path of a target key of ``path``, split into its (unquoted) segments."""
    if "#" not in target:
        return None
    file_part, domain = target.split("#", 1)
    if normalize(file_part) != normalize(base.key(path)):
        return None
    return [unquote(part) for part in domain.strip("/").split("/")]


def read_keys(doc: Any, path: str, targets: list[str], fields: list[str]) -> dict[str, Any]:
    """Read the named fields of the board's target keys, as ``read_back`` returns them."""
    observed: dict[str, dict[str, Any]] = {}
    unreadable: set[str] = set()
    cache: dict[str, Any] = {}

    def statuses() -> dict[str, dict[str, Any]]:
        if "routes" not in cache:
            cache["routes"] = route_status(doc)
        return cache["routes"]

    readers: dict[str, Callable[[list[str]], dict[str, Any]]] = {
        "forward-annotation": lambda parts: {"status": annotation_state(doc)["status"]},
        # a radius that cannot be read (a board not in mils) is left out: "not read back", never null
        "outline": lambda parts: {k: v for k, v in outline_values(doc).items() if not (k == "radius_mm" and v is None)},
        "routing": lambda parts: routing_values(doc),
        "components": lambda parts: component_values(find_component(doc, parts[1])),
        "planes": lambda parts: plane_values(doc, parts[1], int(parts[2])),
        "nets": lambda parts: {"routed": statuses().get(parts[1], {}).get("routed")},
    }
    sizes = {"components": 2, "planes": 3, "nets": 2}
    for target in targets:
        parts = fragment(target, path)
        reader = readers.get(parts[0]) if parts else None
        if reader is None or parts is None or len(parts) != sizes.get(parts[0], 1):
            unreadable.update(fields)
            continue
        try:
            values = reader(parts)
        except Exception:  # noqa: BLE001 - this key's fields cannot be read
            unreadable.update(fields)
            continue
        observed[target] = {field: values[field] for field in fields if field in values}
    return {"observed": observed, "unreadable": sorted(unreadable)}


class ReadsBack(base.Handler):
    """A board write whose preview changes are read back from the open board by ``read_keys``."""

    def read_back(self, ctx: base.Context) -> dict[str, Any]:
        path = ctx.document_path()
        targets = list(ctx.request.get("targets") or [])
        fields = list(ctx.request.get("fields") or [])
        return in_layout(ctx, lambda app, doc: read_keys(doc, path, targets, fields), budget_s=READ_BUDGET_S,
                         what="reading the board back")
