"""Dialogs (adapter/platform/dialogs.py): finding, answering, dismissing and reporting them.

Xpedition's questions are Qt windows that hold the automation call open until someone clicks.
Rules cover the expected ones. Anything else is not pressed but reported, so that a call that
never returns can be explained. user32 and pywinauto are faked.
"""
from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from xpedition_cli.adapter.platform import dialogs

LAYOUT_PID = 4242


class FakeWin:
    """user32 over a dict of windows: ``{hwnd: {title, class, pid, visible, owner, style, children, top}}``."""

    def __init__(self, windows: dict[int, dict], images: dict[int, str] | None = None) -> None:
        self.data = windows
        self.images = images or {LAYOUT_PID: "expeditionpcb.exe"}
        self.read: list[int] = []
        self.pressed: list[tuple[int, int]] = []

    def windows(self) -> list[int]:
        return [h for h, w in self.data.items() if w.get("top", True)]

    def children(self, hwnd: int) -> list[int]:
        self.read.append(hwnd)
        return list(self.data[hwnd].get("children", []))

    def text(self, hwnd: int) -> str:
        return self.data[hwnd].get("title", "")

    def class_name(self, hwnd: int) -> str:
        return self.data[hwnd].get("class", "")

    def visible(self, hwnd: int) -> bool:
        return self.data[hwnd].get("visible", True)

    def exists(self, hwnd: int) -> bool:
        return hwnd in self.data

    def owner(self, hwnd: int) -> int:
        return self.data[hwnd].get("owner", 0)

    def style(self, hwnd: int) -> int:
        return self.data[hwnd].get("style", 0)

    def pid(self, hwnd: int) -> int:
        return self.data[hwnd].get("pid", 0)

    def image(self, pid: int) -> str:
        return self.images.get(pid, "")

    def press(self, hwnd: int, button: int) -> None:
        self.pressed.append((hwnd, button))


def message_box(title: str, text: str, buttons: list[str], *, pid: int = LAYOUT_PID, first: int = 100) -> dict[int, dict]:
    children = {first + 1: {"title": text, "class": "Static", "pid": pid, "top": False}}
    for index, caption in enumerate(buttons):
        children[first + 2 + index] = {"title": caption, "class": "Button", "pid": pid, "top": False}
    return {first: {"title": title, "class": dialogs.DIALOG_CLASS, "pid": pid, "children": list(children)}, **children}


class Win32Case(unittest.TestCase):
    def use(self, win: FakeWin) -> FakeWin:
        patcher = mock.patch.object(dialogs, "_win", win)
        patcher.start()
        self.addCleanup(patcher.stop)
        return win


class FindAndDismiss(Win32Case):
    def test_visible_dialogs_are_found_with_their_text_and_buttons(self) -> None:
        win = self.use(FakeWin({
            **message_box("PartsDB2HKP", "Invalid argument -x", ["&OK"]),
            1: {"title": "Xpedition Layout", "class": "Qt5QWindowIcon", "pid": LAYOUT_PID},
            2: {"title": "Hidden", "class": dialogs.DIALOG_CLASS, "pid": LAYOUT_PID, "visible": False},
        }))
        found = dialogs.find_dialogs(LAYOUT_PID)
        self.assertEqual(found, [dialogs.Dialog(hwnd=100, pid=LAYOUT_PID, process="expeditionpcb.exe", title="PartsDB2HKP",
                                                text="Invalid argument -x", buttons=["OK"])])
        self.assertEqual(win.read, [100])

    def test_checkboxes_and_group_boxes_are_no_buttons(self) -> None:
        # they share the Button class with push buttons; BS_AUTOCHECKBOX 3, BS_GROUPBOX 7
        windows = message_box("Exporting", "Writing files", ["Cancel"])
        windows[100]["children"] += [150, 151]
        windows[150] = {"title": "Progress", "class": "Button", "pid": LAYOUT_PID, "top": False, "style": 0x50000007}
        windows[151] = {"title": "Close when done", "class": "Button", "pid": LAYOUT_PID, "top": False, "style": 0x50010003}
        self.use(FakeWin(windows))
        self.assertEqual(dialogs.find_dialogs(LAYOUT_PID)[0].buttons, ["Cancel"])

    def test_without_a_pid_only_xpedition_processes_count(self) -> None:
        self.use(FakeWin({**message_box("Notepad", "Save?", ["Yes"], pid=9, first=200), **message_box("Layout", "x", ["OK"])},
                         images={LAYOUT_PID: "expeditionpcb.exe", 9: "notepad.exe"}))
        self.assertEqual([d.hwnd for d in dialogs.find_dialogs()], [100])
        self.assertEqual([d.hwnd for d in dialogs.find_dialogs(9)], [200])  # a named process counts whatever its image

    def test_a_licence_dialog_is_listed_without_reading_it(self) -> None:
        win = self.use(FakeWin(message_box("License Error", "server 28000@synthetic-host is down", ["OK"])))
        found = dialogs.find_dialogs(LAYOUT_PID)
        self.assertEqual((found[0].title, found[0].text, found[0].buttons), ("License Error", "", []))
        self.assertEqual(win.read, [])

    def test_dismiss_presses_ok_on_the_same_dialog_only(self) -> None:
        win = self.use(FakeWin(message_box("PartsDB2HKP", "Done", ["OK"])))
        dialog = dialogs.find_dialogs(LAYOUT_PID)[0]
        self.assertTrue(dialogs.dismiss(dialog))
        self.assertEqual(win.pressed, [(100, dialogs.IDOK)])
        moved = dialogs.Dialog(hwnd=100, pid=5000, process="x.exe", title="PartsDB2HKP", text="Done")  # the window has another owner now
        self.assertFalse(dialogs.dismiss(moved))
        gone = dialogs.Dialog(hwnd=999, pid=LAYOUT_PID, process="x.exe", title="PartsDB2HKP", text="Done")
        self.assertFalse(dialogs.dismiss(gone))
        self.assertEqual(len(win.pressed), 1)

    def test_a_licence_dialog_is_never_dismissed(self) -> None:
        win = self.use(FakeWin(message_box("Siemens 许可证", "", ["OK"])))
        self.assertFalse(dialogs.dismiss(dialogs.find_dialogs(LAYOUT_PID)[0]))
        self.assertEqual(win.pressed, [])

    def test_top_windows_licence_and_processing_titles(self) -> None:
        self.use(FakeWin({
            1: {"title": "Xpedition Layout - [Demo.pcb]", "class": "Qt5QWindowIcon", "pid": LAYOUT_PID, "style": dialogs.WS_MINIMIZEBOX},
            2: {"title": "Processing...", "class": "Qt5QWindowIcon", "pid": LAYOUT_PID, "owner": 1},
            3: {"title": "Siemens Login", "class": "Qt5QWindowIcon", "pid": LAYOUT_PID},
            4: {"title": "", "class": "Qt5QWindowIcon", "pid": LAYOUT_PID},
            5: {"title": "Other program", "class": "X", "pid": 9},
        }))
        self.assertEqual([w["hwnd"] for w in dialogs.top_windows(LAYOUT_PID)], [1, 2, 3])
        self.assertEqual([w["hwnd"] for w in dialogs.top_windows()], [1, 2, 3])
        self.assertEqual(len(dialogs.top_windows(LAYOUT_PID, titled_only=False)), 4)
        self.assertEqual(dialogs.licence_windows(LAYOUT_PID), ["Siemens Login"])
        self.assertEqual(dialogs.processing_windows(LAYOUT_PID), ["Processing..."])

    def test_licence_and_sign_in_titles(self) -> None:
        for title in ("Siemens Login", "License Error", "Licensing", "Sign in", "许可证错误", "授权", "用户登录"):
            with self.subTest(title=title):
                self.assertTrue(dialogs.is_licence_window(title))
        for title in ("Xpedition Layout", "Batch DRC", "数据库恢复", ""):
            with self.subTest(title=title):
                self.assertFalse(dialogs.is_licence_window(title))


PRESSES: list[str] = []  # every press, in order


class Element:
    def __init__(self, name: str) -> None:
        self.element_info = SimpleNamespace(name=name)
        self.pressed = False

    def click_input(self) -> None:
        self.pressed = True
        PRESSES.append(self.element_info.name)

    invoke = click_input


class UiaWindow:
    def __init__(self, texts: list[str], buttons: list[str], radios: list[str] | None = None) -> None:
        self.texts = [Element(t) for t in texts]
        self.buttons = [Element(b) for b in buttons]
        self.radios = [Element(r) for r in radios or []]

    def descendants(self, control_type: str) -> list[Element]:
        return {"Text": self.texts, "Button": self.buttons, "RadioButton": self.radios}.get(control_type, [])


class Prompts(unittest.TestCase):
    """Stands in for pywinauto so the scan runs without a desktop."""

    RULES = (("change the current project", "Yes", None),)

    def setUp(self) -> None:
        PRESSES.clear()
        self.windows: dict[int, UiaWindow] = {}
        self.listing: list[dict] = []
        desktop = SimpleNamespace(window=lambda handle: self.windows[handle])
        for name, value in (("top_windows", lambda pid=None, **kw: list(self.listing)), ("_desktop", lambda: desktop)):
            patcher = mock.patch.object(dialogs, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def install(self, specs: list[tuple[str, list[str], list[str]]], **extra) -> None:
        for handle, (title, texts, buttons) in enumerate(specs):
            self.windows[handle] = UiaWindow(texts, buttons, extra.get("radios"))
            self.listing.append({"hwnd": handle, "pid": LAYOUT_PID, "title": title, "owner": 1, "style": 0, "class": "Qt5QWindowIcon"})

    def pressed(self) -> list[str]:
        return [b.element_info.name for w in self.windows.values() for b in w.buttons + w.radios if b.pressed]

    def test_a_known_prompt_is_answered_and_not_reported(self) -> None:
        self.install([("Xpedition Designer", ["change the current project?"], ["Yes", "No"])])
        blocking: list = []
        self.assertEqual(dialogs.answer_prompts(self.RULES, LAYOUT_PID, blocking=blocking), ["Xpedition Designer: Yes"])
        self.assertEqual(blocking, [])

    def test_a_mnemonic_ampersand_does_not_prevent_an_exact_rule(self) -> None:
        self.install([("Xpedition Layout", ["新更改已经可以进行正向标注"], ["否(&N)", "是(&Y)"])])
        self.assertEqual(dialogs.answer_prompts(dialogs.LAYOUT_PROMPTS, LAYOUT_PID), ["Xpedition Layout: 否(N)"])
        self.assertEqual(self.pressed(), ["否(&N)"])

    def test_the_option_radio_is_chosen_before_the_button(self) -> None:
        self.install([("Database Recovery", ["database recovery: choose a source"], ["OK", "Cancel"])],
                     radios=["Backup", "User saved"])
        dialogs.answer_prompts(dialogs.LAYOUT_PROMPTS, LAYOUT_PID)
        self.assertEqual(PRESSES, ["User saved", "OK"])

    def test_an_unknown_dialog_is_reported_with_its_text_and_buttons_and_not_pressed(self) -> None:
        self.install([("Xpedition Designer", ["Unable to access the PartsDB at D:/work/demo/Lib/Case.pdb"], ["Retry", "Cancel"])])
        blocking: list = []
        self.assertEqual(dialogs.answer_prompts(self.RULES, LAYOUT_PID, blocking=blocking), [])
        self.assertEqual(len(blocking), 1)
        self.assertIn("PartsDB", blocking[0]["text"])
        self.assertEqual(blocking[0]["buttons"], ["Cancel", "Retry"])
        self.assertEqual(self.pressed(), [])

    def test_an_unmatched_single_ok_is_reported_without_pressing(self) -> None:
        self.install([("Unexpected native message", ["Unknown warning"], ["OK"])])
        self.assertEqual(dialogs.blocking_dialogs(LAYOUT_PID)[0]["buttons"], ["OK"])
        self.assertEqual(self.pressed(), [])

    def test_document_and_main_windows_are_left_alone(self) -> None:
        self.install([("[Schematic1.1]", ["sheet content"], ["Close"]), ("Xpedition Layout - Demo.pcb", ["change the current project"],
                                                                         ["Yes"])])
        self.listing[1].update(owner=0, style=dialogs.WS_MINIMIZEBOX)
        blocking: list = []
        self.assertEqual(dialogs.answer_prompts(self.RULES, LAYOUT_PID, blocking=blocking), [])
        self.assertEqual(blocking, [])
        self.assertEqual(self.pressed(), [])

    def test_a_login_window_is_not_inspected_even_if_a_rule_matches(self) -> None:
        self.install([("Siemens Login", ["change the current project"], ["Yes"])])
        self.windows.clear()  # reading it now would fail: proves its contents are not accessed
        blocking: list = []
        self.assertEqual(dialogs.answer_prompts(self.RULES, LAYOUT_PID, blocking=blocking), [])
        self.assertEqual((blocking[0]["title"], blocking[0]["buttons"]), ("Siemens Login", []))
        self.assertEqual(blocking[0]["text"], dialogs.LICENCE_NOTE)

    def test_a_licence_text_in_an_unremarkable_window_is_not_pressed(self) -> None:
        self.install([("Xpedition Designer", ["change the current project? Your license expires today"], ["Yes"])])
        blocking: list = []
        self.assertEqual(dialogs.answer_prompts(self.RULES, LAYOUT_PID, blocking=blocking), [])
        self.assertEqual(blocking[0]["text"], dialogs.LICENCE_NOTE)
        self.assertEqual(self.pressed(), [])

    def test_blocking_dialogs_press_nothing(self) -> None:
        self.install([("Batch DRC", ["Batch DRC"], ["OK", "Cancel"])])
        self.assertEqual(dialogs.blocking_dialogs(LAYOUT_PID), [{"title": "Batch DRC", "text": "Batch DRC", "buttons": ["Cancel", "OK"]}])
        self.assertEqual(self.pressed(), [])

    def test_without_ui_automation_only_licence_windows_are_reported(self) -> None:
        self.install([("Siemens Login", [], []), ("Batch DRC", ["Batch DRC"], ["OK"])])
        with mock.patch.object(dialogs, "_desktop", lambda: None):
            blocking: list = []
            self.assertEqual(dialogs.answer_prompts(dialogs.DRC_PROMPTS, LAYOUT_PID, blocking=blocking), [])
        self.assertEqual([b["title"] for b in blocking], ["Siemens Login"])


class Answerer(unittest.TestCase):
    def test_a_persistent_dialog_is_reported_once_and_answers_are_kept(self) -> None:
        dialog = {"title": "Xpedition Designer", "text": "Unable to access", "buttons": ["OK"]}
        calls: list = []

        def fake(rules, pid, *, blocking=None):
            calls.append((rules, pid))
            blocking.append(dict(dialog))
            return ["Xpedition Layout: Open"] if len(calls) == 1 else []

        with mock.patch.object(dialogs, "answer_prompts", fake):
            with dialogs.PromptAnswerer(dialogs.LAYOUT_PROMPTS, LAYOUT_PID, interval_s=0.01) as answerer:
                deadline = time.monotonic() + 5.0
                while len(calls) < 3 and time.monotonic() < deadline:
                    time.sleep(0.01)
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(calls[0], (dialogs.LAYOUT_PROMPTS, LAYOUT_PID))
        self.assertEqual(answerer.blocking_dialogs(), [dialog], "a dialog seen on every poll is reported once")
        self.assertEqual(answerer.answered, ["Xpedition Layout: Open"])

    def test_a_failed_scan_is_tried_again(self) -> None:
        def broken(rules, pid, *, blocking=None):
            raise OSError("the window went away")

        with mock.patch.object(dialogs, "answer_prompts", broken):
            answerer = dialogs.PromptAnswerer(dialogs.DESIGNER_PROMPTS, LAYOUT_PID)
            answerer.poll()
        self.assertEqual((answerer.answered, answerer.blocking_dialogs()), ([], []))

    def test_the_thread_stops_on_exit(self) -> None:
        with mock.patch.object(dialogs, "answer_prompts", lambda rules, pid, blocking=None: []):
            with dialogs.PromptAnswerer((), LAYOUT_PID, interval_s=0.01) as answerer:
                thread = answerer._thread
        self.assertIsInstance(thread, threading.Thread)
        self.assertFalse(thread.is_alive())


class RuleTables(unittest.TestCase):
    def test_annotating_answers_yes_where_opening_answers_no(self) -> None:
        opening = dict((marker, button) for marker, button, _ in dialogs.LAYOUT_PROMPTS)
        annotating = dict((marker, button) for marker, button, _ in dialogs.LAYOUT_PROMPTS_ANNOTATE)
        self.assertEqual((opening["正向标注"], annotating["正向标注"]), ("否(N)", "是(Y)"))
        self.assertEqual((opening["forward annotat"], annotating["forward annotat"]), ("No", "Yes"))
        self.assertEqual(opening["design status"], annotating["design status"])

    def test_every_table_has_chinese_and_english_markers(self) -> None:
        for name in ("DESIGNER_PROMPTS", "LAYOUT_PROMPTS", "LAYOUT_PROMPTS_ANNOTATE", "DRC_PROMPTS", "OUTPUT_PROMPTS"):
            markers = [marker for marker, _, _ in getattr(dialogs, name)]
            with self.subTest(table=name):
                self.assertTrue(any(any("\u4e00" <= c <= "\u9fff" for c in m) for m in markers))
                self.assertTrue(any(m.isascii() for m in markers))
                self.assertFalse(any(dialogs.is_licence_window(m) for m in markers))

    def test_the_output_and_drc_dialogs_are_confirmed(self) -> None:
        self.assertIn(("Batch DRC", "OK", None), dialogs.DRC_PROMPTS)
        self.assertEqual({m for m, _, _ in dialogs.OUTPUT_PROMPTS if m.isascii()},
                         {"NC Drill Generation", "ODB++ Design Output", "Gerber Output"})


if __name__ == "__main__":
    unittest.main()
