"""Find and dismiss modal dialogs that block Xpedition automation (Windows only).

A message box raised while an agent drives the product turns the next COM call
into a hang. Layout's `Gui` suppression switches cover its own trivial dialogs
but not Designer's project and sheet dialogs. This enumerates visible `#32770`
dialogs owned by an Xpedition process and presses their default button.

Everything degrades to "no dialogs" on other platforms or when user32 is not
available, so callers can invoke it unconditionally.
"""

from __future__ import annotations

import os
from typing import Any

XPEDITION_PROCESSES = {
    "viewdraw.exe",
    "expeditionpcb.exe",
    "librarymanager.exe",
    "packagerui.exe",
    "package.exe",
    "cellditor.exe",
    "celleditor70.exe",
}
DIALOG_CLASS = "#32770"
WM_COMMAND = 0x0111
BM_CLICK = 0x00F5
IDOK = 1


def _api() -> tuple[Any, Any, Any] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.GetDlgItem.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetDlgItem.restype = wintypes.HWND
    return ctypes, user32, kernel32


def _text(ctypes: Any, user32: Any, hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _class(ctypes: Any, user32: Any, hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _process_name(ctypes: Any, kernel32: Any, pid: int) -> str:
    from ctypes import wintypes

    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def find_dialogs(only_xpedition: bool = True) -> list[dict[str, Any]]:
    """Visible modal dialogs, optionally only those owned by an Xpedition process."""
    api = _api()
    if api is None:
        return []
    ctypes, user32, kernel32 = api
    from ctypes import wintypes

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[dict[str, Any]] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or _class(ctypes, user32, hwnd) != DIALOG_CLASS:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _process_name(ctypes, kernel32, pid.value)
        if only_xpedition and name not in XPEDITION_PROCESSES:
            return True
        body: list[str] = []

        def child(child_hwnd: int, _l: int) -> bool:
            if _class(ctypes, user32, child_hwnd) == "Static":
                text = _text(ctypes, user32, child_hwnd)
                if text:
                    body.append(text)
            return True

        user32.EnumChildWindows(hwnd, callback_type(child), 0)
        found.append(
            {
                "hwnd": hwnd,
                "pid": pid.value,
                "process": name,
                "title": _text(ctypes, user32, hwnd),
                "text": " | ".join(body),
            }
        )
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found


def dismiss(hwnd: int, button: int = IDOK) -> bool:
    """Press the dialog's default button."""
    api = _api()
    if api is None:
        return False
    _ctypes, user32, _kernel32 = api
    # PostMessage, not SendMessage: a start-up box on a busy UI thread (Layout's
    # graphics-initialisation notice) would otherwise block the caller for good.
    control = user32.GetDlgItem(hwnd, button)
    if control:
        user32.PostMessageW(control, BM_CLICK, 0, 0)
    else:
        user32.PostMessageW(hwnd, WM_COMMAND, button, 0)
    return True


def dismiss_all(only_xpedition: bool = True) -> list[dict[str, Any]]:
    """Dismiss every visible Xpedition dialog; returns what was dismissed."""
    dialogs = find_dialogs(only_xpedition)
    for dialog in dialogs:
        dismiss(dialog["hwnd"])
    return dialogs


# -- top-level windows: find, raise, capture -------------------------------------------


def main_window(process_name: str) -> dict[str, Any] | None:
    """The largest visible titled window owned by `process_name` (e.g. `viewdraw.exe`)."""
    api = _api()
    if api is None:
        return None
    ctypes, user32, kernel32 = api
    from ctypes import wintypes

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    found: list[dict[str, Any]] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _text(ctypes, user32, hwnd)
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if _process_name(ctypes, kernel32, pid.value) != process_name.lower():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append(
            {
                "hwnd": hwnd,
                "title": title,
                "left": rect.left,
                "top": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            }
        )
        return True

    user32.EnumWindows(callback_type(callback), 0)
    if not found:
        return None
    return max(found, key=lambda w: w["width"] * w["height"])


def bring_to_front(hwnd: int) -> bool:
    """Raise a window over other applications; returns whether it took the foreground.

    Windows refuses `SetForegroundWindow` from a background process, but a window
    may still be made topmost and then released, which puts it on top.
    """
    api = _api()
    if api is None:
        return False
    ctypes, user32, _kernel32 = api
    from ctypes import wintypes

    user32.SetWindowPos.argtypes = (
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )
    user32.GetForegroundWindow.restype = wintypes.HWND
    sw_restore, flags = 9, 0x0001 | 0x0002 | 0x0040  # SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW
    user32.ShowWindow(hwnd, sw_restore)
    user32.SwitchToThisWindow(hwnd, True)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # HWND_TOPMOST
    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)  # HWND_NOTOPMOST
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


def png_bytes(width: int, height: int, bgra: bytes) -> bytes:
    """Encode a top-down 32-bit BGRA buffer as an RGB PNG (no external library)."""
    import struct
    import zlib

    stride = width * 4
    rows = []
    for y in range(height):
        row = bgra[y * stride : (y + 1) * stride]
        rgb = bytearray(width * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        rows.append(b"\x00" + bytes(rgb))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
        + chunk(b"IEND", b"")
    )


def capture_window(hwnd: int, path: str) -> dict[str, Any]:
    """Copy the screen area of a window into a PNG file (the window must be visible)."""
    api = _api()
    if api is None:
        raise OSError("window capture needs Windows")
    ctypes, user32, _kernel32 = api
    from ctypes import wintypes

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise OSError("window has no visible area")
    screen = user32.GetDC(0)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    try:
        gdi32.SelectObject(memory, bitmap)
        gdi32.BitBlt(memory, 0, 0, width, height, screen, rect.left, rect.top, 0x00CC0020)

        class BitmapInfoHeader(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        info = BitmapInfoHeader()
        info.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.biWidth = width
        info.biHeight = -height  # top-down
        info.biPlanes = 1
        info.biBitCount = 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        raw = buffer.raw
        data = png_bytes(width, height, raw)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(0, screen)
    with open(path, "wb") as handle:
        handle.write(data)
    result: dict[str, Any] = {"path": path, "width": width, "height": height, "bytes": len(data)}
    # a screen capture of a locked or switched-off desktop is all black; say so
    # instead of handing over a picture of nothing
    if raw and not any(raw[index] for index in range(0, len(raw), 4093)):
        result["blank"] = True
        result["hint"] = "the desktop is locked or the window is not on screen"
    return result


# -- Qt prompts through UI Automation ------------------------------------------------

NOTICE_BUTTONS = ("OK", "确定")


def top_windows(process_name: str, titled_only: bool = True) -> list[dict[str, Any]]:
    """Every visible top-level window owned by `process_name` (titled ones by default;
    a Qt combo box shows its list in an untitled popup window)."""
    api = _api()
    if api is None:
        return []
    ctypes, user32, kernel32 = api
    from ctypes import wintypes

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[dict[str, Any]] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _text(ctypes, user32, hwnd)
        if not title and titled_only:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if _process_name(ctypes, kernel32, pid.value) != process_name.lower():
            return True
        found.append({"hwnd": hwnd, "pid": pid.value, "title": title})
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found


def _press(element: Any) -> None:
    try:
        element.invoke()
    except Exception:
        element.click_input()


def press_control(process_name: str, control_name: str) -> bool:
    """Press the toolbar control named `control_name` in `process_name`'s main window
    (Layout's toolbar buttons are UI Automation check boxes such as `VIEW_FITBOARD`).
    Needs `pywinauto`; False when it is missing or the control was not found."""
    try:
        from pywinauto import Desktop
    except Exception:
        return False
    window = main_window(process_name)
    if window is None:
        return False
    try:
        root = Desktop(backend="uia").window(handle=window["hwnd"])
        for candidate in root.descendants():
            if str(candidate.element_info.name or "").strip() != control_name:
                continue
            if candidate.element_info.control_type not in ("Button", "CheckBox", "MenuItem"):
                continue
            _press(candidate)
            return True
    except Exception:
        return False
    return False


def choose_combo_item(process_name: str, combo_name: str, item: str) -> dict[str, Any] | None:
    """Pick `item` in the toolbar combo box `combo_name` of `process_name`'s main window.

    Layout's display schemes have no automation setter, so the toolbar combo
    (`CMD_DISPLAY_SCHEMES`) is driven through UI Automation: expand it, then press
    the list item, which Qt shows in an untitled popup window of its own. Needs
    `pywinauto`; None when it is missing or nothing matched, else what was picked.
    """
    try:
        from pywinauto import Desktop
    except Exception:
        return None
    window = main_window(process_name)
    if window is None:
        return None
    desk = Desktop(backend="uia")
    try:
        root = desk.window(handle=window["hwnd"])
        combo = next(
            (
                candidate
                for candidate in root.descendants(control_type="ComboBox")
                if str(candidate.element_info.name or "").strip() == combo_name
            ),
            None,
        )
    except Exception:
        return None
    if combo is None:
        return None
    try:
        combo.expand()
    except Exception:
        try:
            combo.click_input()
        except Exception:
            return None
    import time

    from pywinauto.keyboard import send_keys

    time.sleep(0.8)
    target = item.strip().lower()

    def visible_entry() -> Any:
        # the popup exposes only the rows on screen, so look after every page
        for candidate in top_windows(process_name, titled_only=False):
            if candidate["hwnd"] == window["hwnd"]:
                continue
            try:
                element = desk.window(handle=candidate["hwnd"])
                for entry in element.descendants(control_type="ListItem"):
                    if str(entry.element_info.name or "").strip().lower() == target:
                        return entry
            except Exception:
                continue
        return None

    try:
        send_keys("{HOME}")
        time.sleep(0.3)
    except Exception:
        pass
    for _page in range(12):
        entry = visible_entry()
        if entry is not None:
            try:
                entry.click_input()
            except Exception:
                _press(entry)
            return {"combo": combo_name, "item": str(entry.element_info.name)}
        try:
            send_keys("{PGDN}")
        except Exception:
            break
        time.sleep(0.4)
    try:
        send_keys("{ESC}")
    except Exception:
        pass
    return None


def answer_prompts(
    rules: tuple[tuple[str, str, str | None], ...], process_name: str = "expeditionpcb.exe"
) -> list[dict[str, Any]]:
    """Answer the Qt prompts of an Xpedition process by pressing the button a rule names.

    Layout's questions (design status, database recovery, forward annotation) are Qt
    windows, not `#32770` dialogs, so their text and buttons are read through UI
    Automation, which needs `pywinauto`; without it nothing is answered. A rule is
    `(marker, button, option)`: when `marker` occurs in the window's title or text,
    the radio button whose caption contains `option` (if any) is selected and the
    button captioned `button` is pressed. A window with a single OK-style button
    and no matching rule is a notice and is pressed too. Document windows, whose
    titles are bracketed, are left alone. Returns what was answered.
    """
    try:
        from pywinauto import Desktop
    except Exception:
        return []
    answered: list[dict[str, Any]] = []
    for window in top_windows(process_name):
        if window["title"].startswith("["):
            continue
        try:
            element = Desktop(backend="uia").window(handle=window["hwnd"])
            texts = [
                str(item.element_info.name)
                for item in element.descendants(control_type="Text")
                if item.element_info.name
            ]
            buttons: dict[str, Any] = {}
            for item in element.descendants(control_type="Button"):
                caption = str(item.element_info.name or "").strip()
                if caption:
                    buttons.setdefault(caption, item)
        except Exception:
            continue
        haystack = (window["title"] + " " + " ".join(texts)).lower()
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
                except Exception:
                    pass
            _press(buttons[button])
            pressed = button
            break
        if pressed is None and texts and len(buttons) == 1:
            caption = next(iter(buttons))
            if caption in NOTICE_BUTTONS:
                _press(buttons[caption])
                pressed = caption
        if pressed is not None:
            answered.append(
                {"title": window["title"], "text": " ".join(texts)[:300], "pressed": pressed}
            )
    return answered
