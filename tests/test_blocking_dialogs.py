"""A modal dialog nobody can answer is reported instead of silently blocking.

Xpedition's questions are Qt windows that hold the automation call open until
someone clicks. Rules cover the expected ones. Anything else used to be skipped
without a trace, so a draw sat for 18 minutes and then failed with a COM error
naming the operation it was interrupted on -- never the dialog that interrupted
it. Unknown dialogs are still not pressed; they are reported.
"""

from __future__ import annotations

import sys
import types

import pytest

from xpedition_cli import win_dialogs
from xpedition_cli.native_com_adapter import _PromptAnswerer


class Element:
    def __init__(self, name: str) -> None:
        self.element_info = types.SimpleNamespace(name=name)
        self.pressed = False

    def click_input(self) -> None:
        self.pressed = True

    invoke = click_input


class Window:
    def __init__(self, texts: list[str], buttons: list[str]) -> None:
        self.texts = [Element(t) for t in texts]
        self.buttons = [Element(b) for b in buttons]

    def descendants(self, control_type: str):
        if control_type == "Text":
            return self.texts
        if control_type == "Button":
            return self.buttons
        return []


@pytest.fixture
def desktop(monkeypatch):
    """Stand in for pywinauto so the scan can run without a Windows desktop."""
    windows: dict[int, Window] = {}

    def install(specs: list[tuple[str, list[str], list[str]]]) -> None:
        listing = []
        for handle, (title, texts, buttons) in enumerate(specs):
            windows[handle] = Window(texts, buttons)
            listing.append({"title": title, "hwnd": handle})
        monkeypatch.setattr(win_dialogs, "top_windows", lambda process_name: listing)

    module = types.ModuleType("pywinauto")
    module.Desktop = lambda backend: types.SimpleNamespace(window=lambda handle: windows[handle])
    monkeypatch.setitem(sys.modules, "pywinauto", module)
    install.windows = windows
    return install


RULES = (("change the current project", "Yes", None),)


def test_a_known_prompt_is_answered_and_not_reported_as_blocking(desktop) -> None:
    desktop([("Designer", ["change the current project?"], ["Yes", "No"])])
    answered, blocking = win_dialogs.answer_prompts(RULES, "viewdraw.exe")
    assert [entry["pressed"] for entry in answered] == ["Yes"]
    assert blocking == []


def test_an_unknown_dialog_is_reported_with_its_text_and_buttons(desktop) -> None:
    desktop(
        [
            (
                "Xpedition Designer",
                ["Unable to access the PartsDB at C:/p/RcLib/PartsDBLibs/Case.pdb"],
                ["Retry", "Cancel"],
            )
        ]
    )
    answered, blocking = win_dialogs.answer_prompts(RULES, "viewdraw.exe")
    assert answered == []
    assert len(blocking) == 1
    assert "PartsDB" in blocking[0]["text"]
    assert blocking[0]["buttons"] == ["Cancel", "Retry"]


def test_an_unknown_dialog_is_not_pressed(desktop) -> None:
    install = desktop
    install([("Xpedition Designer", ["Delete the design?"], ["Yes", "No"])])
    win_dialogs.answer_prompts(RULES, "viewdraw.exe")
    # Answering an unknown question is worse than reporting it.
    assert not any(
        button.pressed for window in install.windows.values() for button in window.buttons
    )


def test_document_windows_are_neither_answered_nor_reported(desktop) -> None:
    desktop([("[Schematic1.1]", ["sheet content"], ["Close"])])
    assert win_dialogs.answer_prompts(RULES, "viewdraw.exe") == ([], [])


def test_the_answerer_reports_a_persistent_dialog_once(monkeypatch) -> None:
    dialog = {"title": "Xpedition Designer", "text": "Unable to access", "buttons": ["OK"]}
    monkeypatch.setattr(win_dialogs, "answer_prompts", lambda rules, process: ([], [dialog]))
    answerer = _PromptAnswerer(RULES, interval=0.01, process_name="viewdraw.exe")
    with answerer:
        deadline = 0.0
        while not answerer.blocking and deadline < 2.0:
            deadline += 0.02
            __import__("time").sleep(0.02)
    reported = answerer.blocking_dialogs()
    assert reported == [dialog], "a dialog seen on every poll must be reported once"
