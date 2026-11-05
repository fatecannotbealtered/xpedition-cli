"""The dialogs Xpedition raises: find them, answer the known ones, report the rest.

Out-of-process COM calls run on the program's main thread, so a modal dialog holds the call
open until someone answers it. While a call waits, a ``PromptAnswerer`` thread reads the
program's windows through UI Automation (pywinauto) and presses the answer a rule gives.
Rules are ``(marker, button, option)``: when ``marker`` occurs in a window's title or text,
the radio button whose caption contains ``option`` (if any) is selected and the button
captioned exactly ``button`` is pressed. Xpedition's own questions are Qt windows, so they
are read through UI Automation; the message boxes of the command-line programs are
``#32770`` dialogs, read and dismissed through user32.

Safety rules kept from the measured behaviour:

- A dialog no rule covers is never pressed, not even its default button: pressing the
  default of every ``#32770`` ends the packager's progress box and forward annotation then
  fails. It is reported instead, so that a call that never returns can be explained.
- Licence and sign-in windows are not read and never pressed; only a person can complete them.
- Document windows (titles starting with ``[``) and the program's main window are left alone.

Everything degrades to "no dialogs" off Windows or without pywinauto.
"""
from __future__ import annotations

import os
import threading
import warnings
from dataclasses import dataclass, field
from typing import Any

from . import processes

Rule = tuple[str, str, str | None]

XPEDITION_PROCESSES = frozenset({
    "viewdraw.exe", "expeditionpcb.exe", "librarymanager.exe", "packagerui.exe", "package.exe", "cellditor.exe",
    "celleditor70.exe",
})
DIALOG_CLASS = "#32770"
WM_COMMAND = 0x0111
BM_CLICK = 0x00F5
IDOK = 1
GW_OWNER = 4
GWL_STYLE = -16
WS_MINIMIZEBOX = 0x00020000
TEXT_LIMIT = 300
LICENCE_MARKERS = ("license", "licence", "licensing", "login", "log in", "sign in", "sign-in", "许可证", "授权", "登录")
LICENCE_NOTE = "A licence or sign-in window: only a person can complete it, and its contents were not read."
PROCESSING_MARKERS = ("processing", "处理")

# Layout's questions while a board opens or annotates, and the answer an unattended run gives:
# a stale lock from a killed session is opened, the recovery box loads the database the user
# last saved, and the offer to forward-annotate is declined so that annotation stays an
# explicit step.
LAYOUT_PROMPTS: tuple[Rule, ...] = (
    ("确定设计状态", "Open", None),
    ("design status", "Open", None),
    ("数据库恢复", "确定", "用户"),
    ("database recovery", "OK", "user"),
    ("正向标注", "否(N)", None),
    ("forward annotat", "No", None),
)
# The same when the board is opened in order to annotate it: Layout's own "annotate now?" is
# answered Yes, because an annotation Layout starts itself succeeds where the explicit call
# after a declined prompt fails in the packaging phase.
LAYOUT_PROMPTS_ANNOTATE: tuple[Rule, ...] = tuple(
    (marker, ("是(Y)" if button == "否(N)" else "Yes" if button == "No" else button), option)
    for marker, button, option in LAYOUT_PROMPTS
)
# Designer asks before it switches projects while sheets are open; an unattended run closes
# them (the operation that opened them saved them).
DESIGNER_PROMPTS: tuple[Rule, ...] = (
    ("更改当前项目", "Yes", None),
    ("change the current project", "Yes", None),
    ("Cannot create project", "OK", None),
)
# Layout's Batch DRC dialog (Analysis > Batch DRC...).
DRC_PROMPTS: tuple[Rule, ...] = (
    ("批量 DRC", "确定", None),
    ("Batch DRC", "OK", None),
)
# The dialogs of Layout's Output menu commands.
OUTPUT_PROMPTS: tuple[Rule, ...] = (
    ("NC 钻孔生成", "确定", None),
    ("NC Drill Generation", "OK", None),
    ("ODB++ 设计输出", "确定(O)", None),
    ("ODB++ Design Output", "OK", None),
    ("Gerber 输出", "确定(O)", None),
    ("Gerber Output", "OK", None),
)


@dataclass
class Dialog:
    """A visible ``#32770`` dialog: its window, owner, title, static text and button captions."""

    hwnd: int
    pid: int
    process: str
    title: str
    text: str
    buttons: list[str] = field(default_factory=list)


class _Win32:
    """The user32 and kernel32 calls this module makes; tests replace the instance ``_win``."""

    def __init__(self) -> None:
        self._user32: Any = None
        self._kernel32: Any = None

    def _api(self) -> tuple[Any, Any] | None:
        if os.name != "nt":
            return None
        if self._user32 is None:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            for name, args, result in (
                ("GetWindowTextLengthW", (wintypes.HWND,), ctypes.c_int),
                ("GetWindowTextW", (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int), ctypes.c_int),
                ("GetClassNameW", (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int), ctypes.c_int),
                ("IsWindowVisible", (wintypes.HWND,), wintypes.BOOL),
                ("IsWindow", (wintypes.HWND,), wintypes.BOOL),
                ("GetWindow", (wintypes.HWND, wintypes.UINT), wintypes.HWND),
                ("GetWindowLongW", (wintypes.HWND, ctypes.c_int), wintypes.LONG),
                ("GetDlgItem", (wintypes.HWND, ctypes.c_int), wintypes.HWND),
                ("PostMessageW", (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM), wintypes.BOOL),
            ):
                function = getattr(user32, name)
                function.argtypes, function.restype = args, result
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                            ctypes.POINTER(wintypes.DWORD))
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            self._user32, self._kernel32 = user32, kernel32
        return self._user32, self._kernel32

    def _enum(self, parent: int | None) -> list[int]:
        api = self._api()
        if api is None:
            return []
        import ctypes
        from ctypes import wintypes

        found: list[int] = []
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(
            lambda hwnd, _lparam: found.append(int(hwnd or 0)) or True)
        if parent is None:
            api[0].EnumWindows(callback, 0)
        else:
            api[0].EnumChildWindows(wintypes.HWND(parent), callback, 0)
        return [hwnd for hwnd in found if hwnd]

    def windows(self) -> list[int]:
        return self._enum(None)

    def children(self, hwnd: int) -> list[int]:
        return self._enum(hwnd)

    def text(self, hwnd: int) -> str:
        api = self._api()
        if api is None:
            return ""
        import ctypes

        length = api[0].GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        api[0].GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def class_name(self, hwnd: int) -> str:
        api = self._api()
        if api is None:
            return ""
        import ctypes

        buffer = ctypes.create_unicode_buffer(256)
        api[0].GetClassNameW(hwnd, buffer, 256)
        return buffer.value

    def visible(self, hwnd: int) -> bool:
        api = self._api()
        return bool(api and api[0].IsWindowVisible(hwnd))

    def exists(self, hwnd: int) -> bool:
        api = self._api()
        return bool(api and api[0].IsWindow(hwnd))

    def owner(self, hwnd: int) -> int:
        api = self._api()
        return int(api[0].GetWindow(hwnd, GW_OWNER) or 0) if api else 0

    def style(self, hwnd: int) -> int:
        api = self._api()
        return int(api[0].GetWindowLongW(hwnd, GWL_STYLE)) & 0xFFFFFFFF if api else 0

    def pid(self, hwnd: int) -> int:
        return processes.window_pid(hwnd)

    def image(self, pid: int) -> str:
        """The lower-case file name of a process's executable, or empty."""
        api = self._api()
        if api is None or not pid:
            return ""
        import ctypes
        from ctypes import wintypes

        handle = api[1].OpenProcess(processes.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if api[1].QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return os.path.basename(buffer.value).lower()
            return ""
        finally:
            api[1].CloseHandle(handle)

    def press(self, hwnd: int, button: int) -> None:
        """Press a dialog button by its ID. PostMessage, not SendMessage: a box on a busy UI thread
        would otherwise block the caller for good."""
        api = self._api()
        if api is None:
            return
        control = api[0].GetDlgItem(hwnd, button)
        if control:
            api[0].PostMessageW(control, BM_CLICK, 0, 0)
        else:
            api[0].PostMessageW(hwnd, WM_COMMAND, button, 0)


_win = _Win32()


def is_licence_window(title: str) -> bool:
    """Whether a window title (or text) belongs to a licence or sign-in window, English or Chinese."""
    lower = (title or "").lower()
    return any(marker in lower for marker in LICENCE_MARKERS)


# ------------------------------------------------------------------ #32770 dialogs


def find_dialogs(pid: int | None = None) -> list[Dialog]:
    """The visible ``#32770`` dialogs of one process, or of every Xpedition process when ``pid``
    is None. A licence or sign-in dialog is listed with its title only."""
    found: list[Dialog] = []
    for hwnd in _win.windows():
        if not _win.visible(hwnd) or _win.class_name(hwnd) != DIALOG_CLASS:
            continue
        owner = _win.pid(hwnd)
        if pid is not None and owner != pid:
            continue
        name = _win.image(owner)
        if pid is None and name not in XPEDITION_PROCESSES:
            continue
        title = _win.text(hwnd)
        texts: list[str] = []
        buttons: list[str] = []
        if not is_licence_window(title):
            for child in _win.children(hwnd):
                kind = _win.class_name(child)
                if kind == "Static":
                    text = _win.text(child).strip()
                    if text:
                        texts.append(text)
                elif kind == "Button":
                    caption = _win.text(child).replace("&", "").strip()
                    if caption:
                        buttons.append(caption)
        found.append(Dialog(hwnd=hwnd, pid=owner, process=name, title=title, text=" | ".join(texts), buttons=buttons))
    return found


def dismiss(dialog: Dialog) -> bool:
    """Press the OK button of a dialog that is still there and still owned by the same process.
    A licence or sign-in dialog is never pressed."""
    if is_licence_window(f"{dialog.title} {dialog.text}"):
        return False
    if not _win.exists(dialog.hwnd) or _win.pid(dialog.hwnd) != dialog.pid or _win.class_name(dialog.hwnd) != DIALOG_CLASS:
        return False
    _win.press(dialog.hwnd, IDOK)
    return True


# ------------------------------------------------------------------ top-level windows


def top_windows(pid: int | None = None, *, titled_only: bool = True) -> list[dict[str, Any]]:
    """The visible top-level windows of one process, or of every Xpedition process:
    ``{hwnd, pid, title, owner, style, class}``."""
    rows: list[dict[str, Any]] = []
    for hwnd in _win.windows():
        if not _win.visible(hwnd):
            continue
        title = _win.text(hwnd)
        if titled_only and not title:
            continue
        owner = _win.pid(hwnd)
        if pid is not None:
            if owner != pid:
                continue
        elif _win.image(owner) not in XPEDITION_PROCESSES:
            continue
        rows.append({"hwnd": hwnd, "pid": owner, "title": title, "owner": _win.owner(hwnd), "style": _win.style(hwnd),
                     "class": _win.class_name(hwnd)})
    return rows


def _main_window(window: dict[str, Any]) -> bool:
    """A program's main window: not owned by another window and with a minimize box, which
    dialogs and message boxes do not have."""
    return not window.get("owner") and bool(window.get("style", 0) & WS_MINIMIZEBOX) and window.get("class") != DIALOG_CLASS


def licence_windows(pid: int | None) -> list[str]:
    """The titles of the licence and sign-in windows a process shows."""
    return [w["title"] for w in top_windows(pid) if is_licence_window(w["title"])]


def processing_windows(pid: int | None) -> list[str]:
    """The titles of the progress windows a process shows ("Processing...", "处理"): a sign it is busy."""
    return [w["title"] for w in top_windows(pid) if any(m in w["title"].lower() for m in PROCESSING_MARKERS)]


# ------------------------------------------------------------------ prompts through UI Automation

_PYWINAUTO: Any = None
_PYWINAUTO_LOCK = threading.Lock()


def _load_pywinauto() -> Any:
    """Import pywinauto, or False without it.

    pywinauto picks the COM apartment at import: multithreaded when the importing thread has
    none yet, which lets the UI Automation objects be used from any thread. So pythoncom is
    imported here first, and pywinauto in a new thread that has no apartment.
    """
    global _PYWINAUTO
    with _PYWINAUTO_LOCK:
        if _PYWINAUTO is None:
            box: dict[str, Any] = {}

            def load() -> None:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        import pywinauto
                    box["module"] = pywinauto
                except Exception:  # noqa: BLE001 - missing, or unusable on this desktop
                    box["module"] = False

            try:
                import pythoncom  # noqa: F401
            except ImportError:
                pass
            thread = threading.Thread(target=load, name="xpedition-uia-import", daemon=True)
            thread.start()
            thread.join(60.0)
            _PYWINAUTO = box.get("module", False)
    return _PYWINAUTO


def _desktop() -> Any:
    """pywinauto's UI Automation desktop, or None without pywinauto."""
    if os.name != "nt":
        return None
    module = _load_pywinauto()
    return module.Desktop(backend="uia") if module else None


def _press(element: Any) -> None:
    try:
        element.invoke()
    except Exception:  # noqa: BLE001 - a control without the Invoke pattern is clicked
        element.click_input()


def _report(blocking: list[dict[str, Any]] | None, title: str, text: str, buttons: list[str]) -> None:
    if blocking is not None:
        blocking.append({"title": title, "text": text[:TEXT_LIMIT], "buttons": buttons})


def answer_prompts(rules: tuple[Rule, ...] | list[Rule], pid: int | None, *,
                   blocking: list[dict[str, Any]] | None = None) -> list[str]:
    """Answer the prompts of one process (every Xpedition process when ``pid`` is None) by the
    rules; return what was pressed, as ``"<title>: <button>"``.

    A window no rule covers is not pressed. When ``blocking`` is a list, each such window that
    has buttons is added to it as ``{title, text, buttons}``; licence and sign-in windows are
    added with a fixed text and no buttons, without reading them.
    """
    answered: list[str] = []
    desktop = None
    for window in top_windows(pid):
        title = window["title"]
        if title.startswith("[") or _main_window(window):
            continue
        if is_licence_window(title):
            _report(blocking, title, LICENCE_NOTE, [])
            continue
        if desktop is None:
            desktop = _desktop()
            if desktop is None:
                continue  # without UI Automation the window cannot be read
        try:
            element = desktop.window(handle=window["hwnd"])
            texts = [str(item.element_info.name) for item in element.descendants(control_type="Text") if item.element_info.name]
            buttons: dict[str, Any] = {}
            for item in element.descendants(control_type="Button"):
                caption = str(item.element_info.name or "").strip().replace("&", "")
                if caption:
                    buttons.setdefault(caption, item)
        except Exception:  # noqa: BLE001 - the window went away or cannot be read
            continue
        text = " ".join(texts)
        haystack = f"{title} {text}".lower()
        if is_licence_window(haystack):
            _report(blocking, title, LICENCE_NOTE, [])
            continue
        pressed = None
        for marker, button, option in rules:
            if marker.lower() not in haystack or button not in buttons:
                continue
            if option:
                try:
                    for radio in element.descendants(control_type="RadioButton"):
                        if option.lower() in str(radio.element_info.name or "").lower():
                            _press(radio)
                            break
                except Exception:  # noqa: BLE001 - the button is pressed anyway, as before
                    pass
            _press(buttons[button])
            pressed = button
            break
        if pressed is not None:
            answered.append(f"{title}: {pressed}")
        elif buttons:
            _report(blocking, title, text, sorted(buttons))
    return answered


def blocking_dialogs(pid: int | None) -> list[dict[str, Any]]:
    """The dialogs a process shows, as ``{title, text, buttons}``, for reporting; nothing is pressed."""
    found: list[dict[str, Any]] = []
    answer_prompts((), pid, blocking=found)
    return found


class PromptAnswerer:
    """Answer a process's prompts from a helper thread while this thread waits in a COM call.

    ``answered`` lists what was pressed; ``blocking_dialogs()`` the dialogs seen that no rule
    covers, each once, oldest first. ``pid`` None watches every Xpedition process.
    """

    def __init__(self, rules: tuple[Rule, ...] | list[Rule], pid: int | None, interval_s: float = 1.0) -> None:
        self.rules = tuple(rules)
        self.pid = pid
        self.interval_s = interval_s
        self.answered: list[str] = []
        self._blocking: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> PromptAnswerer:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="xpedition-prompts", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.poll()

    def poll(self) -> None:
        """One scan: answer what the rules cover, remember the rest."""
        seen: list[dict[str, Any]] = []
        try:
            answered = answer_prompts(self.rules, self.pid, blocking=seen)
        except Exception:  # noqa: BLE001 - a failed scan is tried again on the next poll
            return
        with self._lock:
            self.answered.extend(answered)
            for dialog in seen:
                self._blocking.setdefault((dialog["title"], dialog["text"]), dialog)

    def blocking_dialogs(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._blocking.values())
