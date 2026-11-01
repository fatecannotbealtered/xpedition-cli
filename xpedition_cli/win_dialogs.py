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


def _bound_pid(pid: int | None = None) -> int | None:
    value = pid if pid is not None else os.environ.get("XPEDITION_EXPECTED_PID")
    if value is None:
        return None
    number = int(value)
    if number <= 0:
        raise ValueError("A bound window process ID must be positive")
    return number


def _window_is_bound(ctypes: Any, user32: Any, hwnd: int, pid: int | None = None) -> bool:
    expected = _bound_pid(pid)
    if expected is None:
        return True
    from ctypes import wintypes

    actual = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(actual))
    return actual.value == expected


def _foreground_is_bound() -> bool:
    api = _api()
    if api is None or _bound_pid() is None:
        return False
    ctypes, user32, _kernel32 = api
    from ctypes import wintypes

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    return _window_is_bound(ctypes, user32, int(user32.GetForegroundWindow() or 0))


def same_root_window(left: int, right: int) -> bool:
    """A dialog/editor in the same PID is not the bound Layout board window."""
    api = _api()
    if api is None or not left or not right:
        return False
    ctypes, user32, _ = api
    from ctypes import wintypes

    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    return bool(
        _window_is_bound(ctypes, user32, left)
        and _window_is_bound(ctypes, user32, right)
        and user32.GetAncestor(left, 2)
        and user32.GetAncestor(left, 2) == user32.GetAncestor(right, 2)
    )


def foreground_matches(hwnd: int) -> bool:
    api = _api()
    if api is None or desktop_locked():
        return False
    _, user32, _ = api
    from ctypes import wintypes

    user32.GetForegroundWindow.restype = wintypes.HWND
    return same_root_window(int(user32.GetForegroundWindow() or 0), hwnd)


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


def find_dialogs(only_xpedition: bool = True, *, pid: int | None = None) -> list[dict[str, Any]]:
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
        if expected_pid is not None and pid.value != expected_pid:
            return True
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

    expected_pid = _bound_pid(pid)
    user32.EnumWindows(callback_type(callback), 0)
    return found


def dismiss(hwnd: int, button: int = IDOK, *, pid: int | None = None) -> bool:
    """Press the dialog's default button."""
    api = _api()
    if api is None:
        return False
    ctypes, user32, _kernel32 = api
    if not _window_is_bound(ctypes, user32, hwnd, pid):
        return False
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
        if not _window_is_bound(ctypes, user32, hwnd):
            return True
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


def bring_to_front(hwnd: int, *, preserve_state: bool = False) -> bool:
    """Raise a window over other applications; returns whether it took the foreground.

    Windows refuses `SetForegroundWindow` from a background process, but a window
    may still be made topmost and then released, which puts it on top. With
    `preserve_state`, only a minimized window is restored; an already maximized
    or normal window keeps its size. Existing callers retain their restore behavior.
    """
    api = _api()
    if api is None:
        return False
    ctypes, user32, _kernel32 = api
    if not _window_is_bound(ctypes, user32, hwnd):
        return False
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
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    if preserve_state:
        user32.IsIconic.argtypes = (wintypes.HWND,)
        user32.IsIconic.restype = wintypes.BOOL
    if not preserve_state or user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, sw_restore)
    user32.SetForegroundWindow(hwnd)
    user32.SwitchToThisWindow(hwnd, True)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # HWND_TOPMOST
    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)  # HWND_NOTOPMOST
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


LOCK_SCREEN_PROCESSES = ("lockapp.exe", "logonui.exe")


def desktop_locked() -> bool:
    """Whether the session is locked: the lock screen (`LockApp.exe`) or the sign-in
    screen (`LogonUI.exe`) in front, or an input desktop this process may not open. A
    capture then shows the lock screen's picture, not the window."""
    api = _api()
    if api is None:
        return False
    ctypes, user32, kernel32 = api
    from ctypes import wintypes

    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    user32.CloseDesktop.argtypes = (wintypes.HANDLE,)
    desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desktop:
        return True
    user32.CloseDesktop(desktop)
    user32.GetForegroundWindow.restype = wintypes.HWND
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
    return _process_name(ctypes, kernel32, pid.value) in LOCK_SCREEN_PROCESSES


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


def _capture_monitor_coverage(ctypes: Any, user32: Any, bounds: tuple[int, ...]) -> None:
    """Require full coverage, including gaps between desktop monitor rectangles."""
    from ctypes import wintypes

    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HANDLE,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )
    monitors, errors = [], []

    def callback(_monitor, _dc, rect, _data):
        try:
            value = rect.contents
            row = (value.left, value.top, value.right, value.bottom)
            if row[0] >= row[2] or row[1] >= row[3] or len(monitors) >= 64:
                raise ValueError("Invalid or excessive monitor bounds")
            monitors.append(row)
            return True
        except Exception as error:
            errors.append(type(error).__name__)
            return False

    user32.EnumDisplayMonitors.argtypes = (
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        callback_type,
        wintypes.LPARAM,
    )
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    if not user32.EnumDisplayMonitors(0, None, callback_type(callback), 0) or errors:
        raise OSError("EnumDisplayMonitors could not establish physical screen bounds")
    remaining = [bounds]
    for ml, mt, mr, mb in monitors:
        uncovered = []
        for left, top, right, bottom in remaining:
            il, it, ir, ib = max(left, ml), max(top, mt), min(right, mr), min(bottom, mb)
            if il >= ir or it >= ib:
                uncovered.append((left, top, right, bottom))
                continue
            uncovered.extend(
                rect
                for rect in (
                    (left, top, right, it),
                    (left, ib, right, bottom),
                    (left, it, il, ib),
                    (ir, it, right, ib),
                )
                if rect[0] < rect[2] and rect[1] < rect[3]
            )
        remaining = uncovered
    if remaining:
        error = OSError("The physical window frame is not fully on the visible monitor area")
        error.details = {
            "stage": "physical_monitor_coverage",
            "coordinate_space": "physical_pixels",
            "frame_bounds": list(bounds),
            "monitor_bounds": [list(rect) for rect in monitors],
            "uncovered_rectangles": [list(rect) for rect in remaining[:64]],
            "uncovered_count": len(remaining),
            "details_complete": len(remaining) <= 64,
            "output_created": False,
            "next_action": (
                "Use the normal GUI to restore, move or resize the bound window fully inside "
                "the visible monitors, then review a new preview. Capture never crops the frame."
            ),
        }
        raise error


def _capture_window_state(ctypes: Any, user32: Any, dwmapi: Any, hwnd: int, expected: int) -> dict:
    from ctypes import wintypes

    if _bound_pid() != expected or desktop_locked():
        raise OSError("Window capture needs the same bound process and an unlocked desktop")
    if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        raise OSError("The bound window is not a visible, restored window")
    pid = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or pid.value != expected:
        raise OSError("GetWindowThreadProcessId did not confirm the bound capture process")
    root = user32.GetAncestor(hwnd, 2)  # GA_ROOT; a different same-PID window is insufficient.
    foreground = user32.GetForegroundWindow()
    if not root or not foreground or user32.GetAncestor(foreground, 2) != root:
        raise OSError("The bound capture window is not in the foreground")
    outer, frame = wintypes.RECT(), wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(outer)):
        raise OSError("GetWindowRect failed during physical capture")
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame)) != 0:
        raise OSError("DwmGetWindowAttribute did not provide physical frame bounds")
    rect = (outer.left, outer.top, outer.right, outer.bottom)
    bounds = (frame.left, frame.top, frame.right, frame.bottom)
    left, top, right, bottom = bounds
    if not (rect[0] <= left < right <= rect[2] and rect[1] <= top < bottom <= rect[3]):
        raise OSError("DWM frame bounds disagree with the physical window rectangle")
    width, height = right - left, bottom - top
    if width > 16384 or height > 16384 or width * height > 16_777_216:
        raise OSError("Physical window frame exceeds the bounded capture size")
    dpi = user32.GetDpiForWindow(hwnd)
    if not dpi:
        raise OSError("GetDpiForWindow failed during physical capture")
    _capture_monitor_coverage(ctypes, user32, bounds)
    return {"pid": pid.value, "root": int(root), "bounds": bounds, "outer": rect, "dpi": int(dpi)}


def capture_window(hwnd: int, path: str) -> dict[str, Any]:
    """Capture a bound foreground frame in physical pixels, without persistent DPI changes.

    GetWindowRect is DPI-virtualized; DWM frame bounds are physical pixels. A
    temporary own-thread PMv2 context also makes screen/monitor APIs consistent.
    No output is created until both observations and all resource cleanup succeed.
    """
    from pathlib import Path

    if Path(path).exists():
        raise FileExistsError("Window capture does not replace an existing output")
    expected = _bound_pid()
    if expected is None or not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
        raise OSError("Window capture requires an existing positive HWND/process binding")
    api = _api()
    if api is None:
        raise OSError("window capture needs Windows")
    ctypes, user32, _kernel32 = api
    from ctypes import wintypes

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    handle, integer, uint = wintypes.HANDLE, ctypes.c_int, wintypes.UINT
    signatures = (
        (user32, "SetThreadDpiAwarenessContext", (handle,), handle),
        (user32, "GetThreadDpiAwarenessContext", (), handle),
        (user32, "AreDpiAwarenessContextsEqual", (handle, handle), wintypes.BOOL),
        (user32, "IsWindow", (handle,), wintypes.BOOL),
        (user32, "IsWindowVisible", (handle,), wintypes.BOOL),
        (user32, "IsIconic", (handle,), wintypes.BOOL),
        (user32, "GetWindowRect", (handle, ctypes.POINTER(wintypes.RECT)), wintypes.BOOL),
        (
            user32,
            "GetWindowThreadProcessId",
            (handle, ctypes.POINTER(wintypes.DWORD)),
            wintypes.DWORD,
        ),
        (user32, "GetForegroundWindow", (), handle),
        (user32, "GetAncestor", (handle, uint), handle),
        (user32, "GetDpiForWindow", (handle,), uint),
        (user32, "GetDC", (handle,), handle),
        (user32, "ReleaseDC", (handle, handle), integer),
        (
            dwmapi,
            "DwmGetWindowAttribute",
            (handle, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD),
            ctypes.c_long,
        ),
        (gdi32, "CreateCompatibleDC", (handle,), handle),
        (gdi32, "CreateCompatibleBitmap", (handle, integer, integer), handle),
        (gdi32, "SelectObject", (handle, handle), handle),
        (
            gdi32,
            "BitBlt",
            (handle, integer, integer, integer, integer, handle, integer, integer, wintypes.DWORD),
            wintypes.BOOL,
        ),
        (
            gdi32,
            "GetDIBits",
            (handle, handle, uint, uint, ctypes.c_void_p, ctypes.c_void_p, uint),
            integer,
        ),
        (gdi32, "DeleteObject", (handle,), wintypes.BOOL),
        (gdi32, "DeleteDC", (handle,), wintypes.BOOL),
    )
    try:
        for dll, name, arguments, result in signatures:
            function = getattr(dll, name)
            function.argtypes, function.restype = arguments, result
    except AttributeError as error:
        raise OSError("Required physical capture API is unavailable") from error
    pmv2 = ctypes.c_void_p(-4)
    previous = user32.SetThreadDpiAwarenessContext(pmv2)
    if not previous:
        raise OSError("SetThreadDpiAwarenessContext could not set temporary PMv2")
    screen = memory = bitmap = old_bitmap = None
    error = None
    cleanup_errors = []

    def require(value, message):
        if not value:
            raise OSError(message)

    def cleanup(name, function, *args):
        try:
            if not function(*args):
                cleanup_errors.append(name)
        except Exception:
            cleanup_errors.append(name)

    try:
        require(
            user32.AreDpiAwarenessContextsEqual(user32.GetThreadDpiAwarenessContext(), pmv2),
            "Temporary thread DPI awareness could not be verified",
        )
        before = _capture_window_state(ctypes, user32, dwmapi, hwnd, expected)
        left, top, right, bottom = before["bounds"]
        width, height = right - left, bottom - top
        screen = user32.GetDC(0)
        require(screen, "GetDC failed during physical capture")
        memory = gdi32.CreateCompatibleDC(screen)
        require(memory, "CreateCompatibleDC failed during physical capture")
        bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
        require(bitmap, "CreateCompatibleBitmap failed during physical capture")
        old_bitmap = gdi32.SelectObject(memory, bitmap)
        require(
            old_bitmap and old_bitmap not in {-1, ctypes.c_void_p(-1).value},
            "SelectObject failed during physical capture",
        )
        require(
            gdi32.BitBlt(memory, 0, 0, width, height, screen, left, top, 0x40CC0020),
            "BitBlt failed during physical capture",
        )
        # GetDIBits requires the bitmap NOT to be selected into a device context.
        previous_bitmap, old_bitmap = old_bitmap, None
        restored = gdi32.SelectObject(memory, previous_bitmap)
        require(restored == bitmap, "SelectObject could not restore the capture DC bitmap")

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
        require(
            gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(info), 0) == height,
            "GetDIBits failed or returned fewer than the required physical scan lines",
        )
        require(
            (info.biWidth, info.biHeight, info.biPlanes, info.biBitCount, info.biCompression)
            == (width, -height, 1, 32, 0),
            "GetDIBits returned an unexpected pixel format or extent",
        )
        raw = buffer.raw
        require(
            _capture_window_state(ctypes, user32, dwmapi, hwnd, expected) == before,
            "Capture window bounds, process identity or DPI changed during screen copy",
        )
    except Exception as caught:
        error = caught
    finally:
        if old_bitmap and old_bitmap not in {-1, ctypes.c_void_p(-1).value}:
            cleanup(
                "SelectObject restore", lambda: gdi32.SelectObject(memory, old_bitmap) == bitmap
            )
        # Delete the DC first even if deselection failed, then release our bitmap.
        if memory:
            cleanup("DeleteDC", gdi32.DeleteDC, memory)
        if bitmap:
            cleanup("DeleteObject", gdi32.DeleteObject, bitmap)
        if screen:
            cleanup("ReleaseDC", user32.ReleaseDC, 0, screen)
        cleanup("thread DPI restoration", user32.SetThreadDpiAwarenessContext, previous)
        cleanup(
            "thread DPI restoration verification",
            lambda: user32.AreDpiAwarenessContextsEqual(
                user32.GetThreadDpiAwarenessContext(), previous
            ),
        )
    if cleanup_errors:
        detail = "; ".join(cleanup_errors)
        raise OSError(
            f"{error or 'Physical capture completed'}; cleanup failed: {detail}"
        ) from error
    if error is not None:
        raise error
    data = png_bytes(width, height, raw)
    with open(path, "xb") as handle:
        handle.write(data)
    result: dict[str, Any] = {
        "path": path,
        "width": width,
        "height": height,
        "bytes": len(data),
        "window_hwnd": hwnd,
        "window_pid": before["pid"],
        "bounds_px": list(before["bounds"]),
        "coordinate_space": "physical_pixels",
        "bounds_verified": True,
        "method": "gdi_physical_frame",
        "dpi": before["dpi"],
    }
    # a screen capture of a locked or switched-off desktop is all black; say so
    # instead of handing over a picture of nothing
    if raw and not any(raw[index] for index in range(0, len(raw), 4093)):
        result["blank"] = True
        result["hint"] = "the desktop is locked or the window is not on screen"
    return result


# -- Qt prompts through UI Automation ------------------------------------------------

NOTICE_BUTTONS = ("OK", "确定")


def top_windows(
    process_name: str, titled_only: bool = True, *, pid: int | None = None
) -> list[dict[str, Any]]:
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
        actual_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(actual_pid))
        if not _window_is_bound(ctypes, user32, hwnd, pid):
            return True
        if _process_name(ctypes, kernel32, actual_pid.value) != process_name.lower():
            return True
        found.append({"hwnd": hwnd, "pid": actual_pid.value, "title": title})
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


def choose_combo_item(
    process_name: str, combo_name: str, item: str, *, expected_hwnd=None
) -> dict[str, Any] | None:
    """One bounded mouse selection; independent UI readback is diagnostic only.

    Native DisplayControl.Name remains the authoritative effect observation.
    No keyboard paging: Qt may send those keys to unrelated toolbar commands.
    """
    if desktop_locked() or _bound_pid() is None or not _foreground_is_bound():
        return None
    try:
        import time

        from pywinauto import Desktop

        window = main_window(process_name)
        if window is None:
            return None
        if expected_hwnd and not same_root_window(window["hwnd"], expected_hwnd):
            return None
        desk = Desktop(backend="uia")
        root = desk.window(handle=window["hwnd"])
        combos = [
            c
            for c in root.descendants(control_type="ComboBox")
            if str(c.element_info.name or "").strip() == combo_name and c.is_visible()
        ]
        if len(combos) != 1:
            return None
        combo = combos[0]
        rect = combo.rectangle()
        # Click the visible combo's arrow, not an Invoke on a neighbouring command.
        combo.click_input(coords=(rect.width() - 8, rect.height() // 2))
        time.sleep(0.3)
        entries = []
        for candidate in top_windows(process_name, titled_only=False):
            popup = desk.window(handle=candidate["hwnd"])
            for entry in popup.descendants(control_type="ListItem"):
                if str(entry.element_info.name or "").strip() == item and entry.is_visible():
                    entries.append(entry)
        if len(entries) != 1 or desktop_locked() or not _foreground_is_bound():
            return {
                "combo": combo_name,
                "item": item,
                "clicked": False,
                "selected": None,
                "reason": "target row is not uniquely visible",
            }
        entries[0].click_input()
        time.sleep(0.3)
        selected = None
        try:
            selected = combo.selected_text()
        except Exception:
            try:
                selected = str(combo.iface_value.CurrentValue)
            except Exception:
                pass
        return {
            "combo": combo_name,
            "item": item,
            "clicked": True,
            "selected": selected,
            "selection_observed": selected == item,
        }
    except Exception:
        return None


def answer_prompts(
    rules: tuple[tuple[str, str, str | None], ...],
    process_name: str = "expeditionpcb.exe",
    *,
    pid: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Answer the Qt prompts of an Xpedition process by pressing the button a rule names.

    Layout's questions (design status, database recovery, forward annotation) are Qt
    windows, not `#32770` dialogs, so their text and buttons are read through UI
    Automation, which needs `pywinauto`; without it nothing is answered. A rule is
    `(marker, button, option)`: when `marker` occurs in the window's title or text,
    the radio button whose caption contains `option` (if any) is selected and the
    button captioned `button` is pressed. A window with no matching rule is reported
    for the operator. Document windows are left alone. Login/licensing windows are
    not inspected.

    Returns `(answered, blocking)`. A dialog no rule covers is *not* pressed -- an
    unknown question is not ours to answer -- but it is reported, because it holds
    the automation call open until a human clicks it and nothing else in the
    process can see why the call never returned.
    """
    try:
        from pywinauto import Desktop
    except Exception:
        return [], []
    answered: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    listing = top_windows(process_name, pid=pid) if pid is not None else top_windows(process_name)
    for window in listing:
        if window["title"].startswith("["):
            continue
        if any(
            marker in window["title"].lower()
            for marker in (
                "license",
                "licence",
                "licensing",
                "login",
                "sign in",
                "许可证",
                "授权",
                "登录",
            )
        ):
            blocking.append(
                {
                    "title": window["title"],
                    "text": (
                        "Operator licensing/authentication required; contents were not inspected."
                    ),
                    "buttons": [],
                }
            )
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
                caption = str(item.element_info.name or "").strip().replace("&", "")
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
        if pressed is not None:
            answered.append(
                {"title": window["title"], "text": " ".join(texts)[:300], "pressed": pressed}
            )
        elif buttons:
            blocking.append(
                {
                    "title": window["title"],
                    "text": " ".join(texts)[:300],
                    "buttons": sorted(buttons),
                }
            )
    return answered, blocking
