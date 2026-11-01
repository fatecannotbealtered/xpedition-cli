"""Physical-frame capture through Win32 API doubles; never touches the desktop."""

import ctypes
import struct
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from xpedition_cli import win_dialogs as W


class Function:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class CaptureAPI:
    hwnd, pid = 101, 42

    def __init__(self, fault=None, dpi=120):
        self.fault, self.dpi = fault, dpi
        self.context = -2
        self.physical = (40, 30, 240, 150)
        self.monitors = [(0, 0, 320, 240)]
        self.events = []
        self.copied = False
        self.selected = False
        self.bits_selected = None
        self.output_race = None
        self.user = SimpleNamespace()
        self.gdi = SimpleNamespace()
        self.dwm = SimpleNamespace()
        self.kernel = SimpleNamespace()
        for namespace, entries in (
            (
                self.user,
                {
                    "SetThreadDpiAwarenessContext": self.set_context,
                    "GetThreadDpiAwarenessContext": lambda: (
                        0 if self.fault == "dpi_context" else self.context
                    ),
                    "AreDpiAwarenessContextsEqual": lambda a, b: self.scalar(a) == self.scalar(b),
                    "GetWindowRect": self.window_rect,
                    "GetWindowThreadProcessId": self.window_pid,
                    "IsWindow": lambda hwnd: self.fault != "invalid_hwnd",
                    "IsWindowVisible": lambda hwnd: self.fault != "hidden",
                    "IsIconic": lambda hwnd: self.fault == "minimized",
                    "GetForegroundWindow": self.foreground,
                    "GetAncestor": lambda hwnd, flag: 0 if self.fault == "root_read" else hwnd,
                    "GetDpiForWindow": lambda hwnd: 0 if self.fault == "dpi_read" else self.dpi,
                    "EnumDisplayMonitors": self.enum_monitors,
                    "GetDC": lambda hwnd: 0 if self.fault == "get_dc" else 201,
                    "ReleaseDC": lambda hwnd, dc: self.release("ReleaseDC"),
                    "OpenInputDesktop": lambda *a: 0 if self.fault == "locked" else 301,
                    "CloseDesktop": lambda *a: 1,
                },
            ),
            (
                self.gdi,
                {
                    "CreateCompatibleDC": lambda dc: 0 if self.fault == "memory_dc" else 202,
                    "CreateCompatibleBitmap": self.bitmap,
                    "SelectObject": self.select,
                    "BitBlt": self.bitblt,
                    "GetDIBits": self.get_bits,
                    "DeleteObject": lambda obj: self.release("DeleteObject"),
                    "DeleteDC": lambda dc: self.release("DeleteDC"),
                },
            ),
            (self.dwm, {"DwmGetWindowAttribute": self.dwm_rect}),
            (
                self.kernel,
                {
                    "OpenProcess": lambda *a: 302,
                    "QueryFullProcessImageNameW": self.image_name,
                    "CloseHandle": lambda handle: 1,
                },
            ),
        ):
            for name, function in entries.items():
                setattr(namespace, name, Function(function))

    @staticmethod
    def scalar(value):
        return value.value if hasattr(value, "value") else value

    def set_context(self, value):
        value = self.scalar(value)
        self.events.append(("dpi", value))
        if (value in {-4, ctypes.c_void_p(-4).value} and self.fault == "dpi_set") or (
            value == -2 and self.fault == "dpi_restore"
        ):
            return 0
        old = self.context
        if not (value == -2 and self.fault == "dpi_restore_noeffect"):
            self.context = value
        return old

    @staticmethod
    def rect(pointer, bounds):
        target = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        target.left, target.top, target.right, target.bottom = bounds

    def window_rect(self, hwnd, pointer):
        if self.fault == "get_rect":
            return 0
        physical = self.context in {-4, ctypes.c_void_p(-4).value}
        bounds = (
            self.physical if physical else tuple(round(v * 96 / self.dpi) for v in self.physical)
        )
        if self.fault == "wrong_rect":
            bounds = (0, 0, 10, 10)
        if self.fault == "drift_rect" and self.copied:
            bounds = tuple(v + 1 for v in bounds)
        self.rect(pointer, bounds)
        return 1

    def dwm_rect(self, hwnd, attribute, pointer, size):
        assert attribute == 9
        if self.fault == "dwm":
            return -1
        bounds = self.physical
        if self.fault == "bad_bounds":
            bounds = (40, 30, 40, 150)
        if self.fault == "drift_bounds" and self.copied:
            bounds = tuple(v + 1 for v in bounds)
        self.rect(pointer, bounds)
        return 0

    def window_pid(self, hwnd, pointer):
        value = self.pid
        if self.fault == "wrong_pid" or self.fault == "drift_pid" and self.copied:
            value += 1
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = value
        return 0 if self.fault == "pid_read" else 7

    def foreground(self):
        return (
            999
            if self.fault == "wrong_foreground"
            or (self.fault == "drift_foreground" and self.copied)
            else self.hwnd
        )

    def enum_monitors(self, dc, clip, callback, data):
        if self.fault == "monitor_enum":
            return 0
        for i, bounds in enumerate(self.monitors):
            rect = wintypes.RECT(*bounds)
            callback(i + 1, 0, ctypes.byref(rect), data)
        return 1

    def image_name(self, handle, flags, buffer, size):
        buffer.value = "C:/synthetic/expeditionpcb.exe"
        return 1

    def bitmap(self, dc, width, height):
        self.events.append(("bitmap", width, height))
        return 0 if self.fault == "bitmap" else 203

    def select(self, dc, value):
        self.events.append(("select", value))
        if value == 203:
            if self.fault == "select":
                return 0
            self.selected = True
            return 204
        if self.fault == "deselect":
            return 0
        self.selected = False
        return 203

    def bitblt(self, *args):
        self.events.append(("BitBlt", args[3], args[4], args[6], args[7]))
        self.copied = True
        return not (self.fault or "").startswith("bitblt")

    def get_bits(self, dc, bitmap, start, count, buffer, info, usage):
        self.bits_selected = self.selected
        if self.output_race:
            self.output_race.write_bytes(b"other writer")
        ctypes.memset(buffer, 64, ctypes.sizeof(buffer))
        if self.fault == "dibits":
            return 0
        return count - 1 if self.fault == "short_read" else count

    def release(self, name):
        self.events.append((name,))
        return 0 if self.fault in {name, "bitblt_and_" + name} else 1

    def install(self, monkeypatch):
        monkeypatch.setattr(W, "_api", lambda: (ctypes, self.user, self.kernel))
        monkeypatch.setattr(
            ctypes,
            "WinDLL",
            lambda name, **kwargs: {"gdi32": self.gdi, "dwmapi": self.dwm}[name],
            raising=False,
        )
        monkeypatch.setattr(
            ctypes, "WINFUNCTYPE", getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE), raising=False
        )
        monkeypatch.setenv("XPEDITION_EXPECTED_PID", str(self.pid))


@pytest.mark.parametrize("dpi", [120, 144])
def test_capture_uses_physical_frame_and_restores_thread(tmp_path, monkeypatch, dpi):
    api = CaptureAPI(dpi=dpi)
    api.install(monkeypatch)
    output = tmp_path / "frame.png"
    result = W.capture_window(api.hwnd, str(output))
    assert (result["width"], result["height"]) == (200, 120)
    assert struct.unpack(">II", output.read_bytes()[16:24]) == (200, 120)
    assert (
        result.items()
        >= {
            "window_hwnd": api.hwnd,
            "window_pid": api.pid,
            "coordinate_space": "physical_pixels",
            "method": "gdi_physical_frame",
            "bounds_verified": True,
            "dpi": dpi,
        }.items()
    )
    assert result["bounds_px"] == list(api.physical)
    assert ("BitBlt", 200, 120, 40, 30) in api.events
    assert api.bits_selected is False
    assert api.context == -2
    assert all((name,) in api.events for name in ("DeleteObject", "DeleteDC", "ReleaseDC"))


@pytest.mark.parametrize(
    "fault",
    [
        "dpi_set",
        "dpi_restore",
        "dpi_restore_noeffect",
        "dpi_context",
        "dpi_read",
        "get_rect",
        "dwm",
        "bad_bounds",
        "wrong_rect",
        "drift_rect",
        "drift_bounds",
        "wrong_pid",
        "pid_read",
        "drift_pid",
        "invalid_hwnd",
        "hidden",
        "minimized",
        "wrong_foreground",
        "drift_foreground",
        "root_read",
        "locked",
        "monitor_enum",
        "get_dc",
        "memory_dc",
        "bitmap",
        "select",
        "deselect",
        "bitblt",
        "dibits",
        "short_read",
        "DeleteObject",
        "DeleteDC",
        "ReleaseDC",
    ],
)
def test_api_failures_never_publish_a_capture(tmp_path, monkeypatch, fault):
    api = CaptureAPI(fault)
    api.install(monkeypatch)
    output = tmp_path / "must-not-exist.png"
    with pytest.raises(OSError):
        W.capture_window(api.hwnd, str(output))
    assert not output.exists()
    if fault not in {"dpi_restore", "dpi_restore_noeffect"}:
        assert api.context == -2
    assert sum(e[0] == "BitBlt" for e in api.events) <= 1


@pytest.mark.parametrize(
    "monitors", [[], [(0, 0, 150, 240)], [(0, 0, 100, 240), (110, 0, 320, 240)]]
)
def test_physical_frame_must_be_fully_on_visible_monitors(tmp_path, monkeypatch, monitors):
    api = CaptureAPI()
    api.monitors = monitors
    api.install(monkeypatch)
    with pytest.raises(OSError):
        W.capture_window(api.hwnd, str(tmp_path / "outside.png"))
    assert not api.copied


def test_adjacent_monitors_cover_complete_physical_frame(tmp_path, monkeypatch):
    api = CaptureAPI()
    api.monitors = [(0, 0, 100, 240), (100, 0, 320, 240)]
    api.install(monkeypatch)
    result = W.capture_window(api.hwnd, str(tmp_path / "spanning.png"))
    assert result["bounds_px"] == list(api.physical)


def test_negative_desktop_coordinates_remain_physical(tmp_path, monkeypatch):
    api = CaptureAPI()
    api.physical = (-200, 30, -40, 150)
    api.monitors = [(-320, 0, 0, 240)]
    api.install(monkeypatch)
    result = W.capture_window(api.hwnd, str(tmp_path / "left-monitor.png"))
    assert result["bounds_px"] == list(api.physical)
    assert ("BitBlt", 160, 120, -200, 30) in api.events


@pytest.mark.parametrize("cleanup", ["DeleteDC", "DeleteObject", "ReleaseDC"])
def test_cleanup_failure_retains_main_error_and_still_restores_dpi(tmp_path, monkeypatch, cleanup):
    api = CaptureAPI("bitblt_and_" + cleanup)
    api.install(monkeypatch)
    output = tmp_path / "failed.png"
    with pytest.raises(OSError) as caught:
        W.capture_window(api.hwnd, str(output))
    assert "BitBlt failed" in str(caught.value)
    assert cleanup in str(caught.value)
    assert not output.exists()
    assert api.context == -2
    assert all((name,) in api.events for name in ("DeleteObject", "DeleteDC", "ReleaseDC"))


def test_excessive_capture_refuses_before_gdi_allocation(tmp_path, monkeypatch):
    api = CaptureAPI()
    api.physical = (0, 0, 10000, 10000)
    api.monitors = [api.physical]
    api.install(monkeypatch)
    with pytest.raises(OSError, match="bounded capture size"):
        W.capture_window(api.hwnd, str(tmp_path / "excessive.png"))
    assert not api.copied
    assert not any(e[0] == "bitmap" for e in api.events)


def test_capture_requires_existing_process_binding(tmp_path, monkeypatch):
    api = CaptureAPI()
    api.install(monkeypatch)
    monkeypatch.delenv("XPEDITION_EXPECTED_PID")
    with pytest.raises(OSError):
        W.capture_window(api.hwnd, str(tmp_path / "unbound.png"))
    assert not api.copied


@pytest.mark.parametrize("race", [False, True])
def test_capture_does_not_overwrite_existing_output(tmp_path, monkeypatch, race):
    api = CaptureAPI()
    api.install(monkeypatch)
    output = tmp_path / "existing.png"
    if race:
        api.output_race = output
    else:
        output.write_bytes(b"other writer")
    with pytest.raises(FileExistsError):
        W.capture_window(api.hwnd, str(output))
    assert output.read_bytes() == b"other writer"
