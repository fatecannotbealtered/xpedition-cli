"""Layout sessions and the shared Layout pieces (adapter/layout.py), on fake COM objects.

Nothing here starts or attaches to a real Layout: test_layout_fakes.world patches COM, the
process list, the dialogs and the licence server.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from test_layout_fakes import (  # noqa: E402
    BOARD,
    PID,
    FakeAnswerer,
    FakeApp,
    FakeComError,
    FakeDoc,
    FakeGui,
    Progress,
    context,
    licence_fault,
    process,
    world,
)

from xpedition_cli._driver import AdapterError  # noqa: E402
from xpedition_cli.adapter import base, sessions  # noqa: E402
from xpedition_cli.adapter import layout as L  # noqa: E402
from xpedition_cli.adapter.platform import com, dialogs, env, processes  # noqa: E402


def code(caught: unittest.case._AssertRaisesContext) -> str:
    return caught.exception.data["code"]


class QuietAndLicenceTest(unittest.TestCase):
    def test_every_suppression_switch_is_set_on_attach(self):
        gui = FakeGui()
        applied = L.quiet_gui(SimpleNamespace(Gui=gui))
        self.assertEqual(gui.written, {name: True for name in L.QUIET_SWITCHES})
        self.assertTrue(all(applied.values()))

    def test_a_switch_an_older_release_rejects_is_reported(self):
        gui = FakeGui(rejects={"SuppressVariantDataOutOfDateDialog"})
        applied = L.quiet_gui(SimpleNamespace(Gui=gui))
        self.assertFalse(applied["SuppressVariantDataOutOfDateDialog"])
        self.assertTrue(applied["SuppressTrivialDialogs"])

    def test_an_application_without_a_gui_is_left_alone(self):
        self.assertEqual(L.quiet_gui(SimpleNamespace()), {})

    def test_the_licence_token_goes_key_then_token(self):
        doc = FakeDoc()
        app = FakeApp(doc)
        with world(app) as fake:
            self.assertIs(L.licensed(app), doc)
        self.assertEqual(doc.validated, [0, "token-7"])
        self.assertEqual(fake.licensing.keys, [7])

    def test_a_refused_licence_call_needs_a_person(self):
        doc = FakeDoc()
        doc.Validate = mock.Mock(side_effect=licence_fault())
        with world(FakeApp(doc)), self.assertRaises(AdapterError) as caught:
            L.licensed(FakeApp(doc))
        self.assertEqual(code(caught), "E_HUMAN_REQUIRED")
        self.assertEqual(caught.exception.data["knowledge"], ["xpedition-license-sign-in"])
        self.assertEqual(caught.exception.data["dispatch_state"], "not_started")

    def test_no_board_means_no_licence_step(self):
        with world(FakeApp(None)):
            self.assertIsNone(L.licensed(FakeApp(None)))


class AttachTest(unittest.TestCase):
    def test_attach_quietens_and_checks_the_process(self):
        app = FakeApp(FakeDoc())
        with world(app):
            self.assertIs(L.attach(PID), app)
            self.assertTrue(app.Gui.written)
            with mock.patch.object(processes, "window_pid", lambda hwnd: 999), self.assertRaises(AdapterError) as caught:
                L.attach(PID)
        self.assertEqual(code(caught), "E_NOT_FOUND")

    def test_nothing_registered_is_unavailable(self):
        with world(None), self.assertRaises(AdapterError) as caught:
            L.attach(PID)
        self.assertEqual(code(caught), "E_BACKEND_UNAVAILABLE")

    def test_the_process_falls_back_to_the_executable_then_the_only_layout(self):
        app = FakeApp()
        app.Gui = SimpleNamespace()  # no HWND
        with world(app, running=[process(1), process(2, "D:/other/ExpeditionPCB.exe")]):
            self.assertEqual(L.app_pid(app), 1)
        with world(app, running=[process(5, "")]):
            self.assertEqual(L.app_pid(app), 5)

    def test_the_launcher_stub_is_not_an_instance(self):
        stub = process(7, "C:/Xpedition/SDD_HOME/common/win64/bin/ExpeditionPCB.exe")
        with world(FakeApp(), running=[stub, process()]):
            self.assertEqual([p.pid for p in L.layout_processes()], [PID])

    def test_the_board_must_be_the_requested_one(self):
        with world(FakeApp(FakeDoc("D:/work/demo/PCB/Other.pcb"))), self.assertRaises(AdapterError) as caught:
            L.board(L.attach(PID), BOARD)
        self.assertEqual((caught.exception.data["conflict"], caught.exception.data["dispatch_state"]), ("application_state", "not_started"))
        with world(FakeApp(None)), self.assertRaises(AdapterError) as caught:
            L.board(L.attach(PID), BOARD)
        self.assertIn("doc open", str(caught.exception))


class DeadlineTest(unittest.TestCase):
    def _timeout(self, fn, timeout):
        raise TimeoutError("late")

    def test_a_call_held_by_a_dialog_needs_a_person(self):
        question = {"title": "Save All", "text": "Save the changes?", "buttons": ["Yes", "No", "Cancel"]}
        with world(FakeApp(), blocking=[question]), mock.patch.object(com, "call_with_timeout", self._timeout):
            with self.assertRaises(AdapterError) as caught:
                L.call(lambda: None, 1.0, PID, write=True, what="saving")
        data = caught.exception.data
        self.assertEqual((data["code"], data["action"], data["title"]), ("E_HUMAN_REQUIRED", "dismiss_dialog", "Save All"))
        self.assertNotIn("dispatch_state", data)  # after a write was sent, its outcome is unknown
        self.assertNotIn("Save All", str(caught.exception))  # the title is untrusted: details only

    def test_a_licence_window_asks_for_the_licence(self):
        licence = {"title": "Siemens License Sign In", "text": dialogs.LICENCE_NOTE, "buttons": []}
        with world(FakeApp(), blocking=[licence]), mock.patch.object(com, "call_with_timeout", self._timeout):
            with self.assertRaises(AdapterError) as caught:
                L.call(lambda: None, 1.0, PID, what="reading")
        self.assertEqual(caught.exception.data["action"], "sign_in")
        self.assertEqual(caught.exception.data["dispatch_state"], "not_started")

    def test_a_progress_box_alone_is_a_timeout(self):
        progress = {"title": "Processing", "text": "", "buttons": ["Cancel"]}
        with world(FakeApp(), blocking=[progress]), mock.patch.object(com, "call_with_timeout", self._timeout):
            with self.assertRaises(AdapterError) as caught:
                L.call(lambda: None, 1.0, PID, what="reading")
        self.assertEqual(code(caught), "E_TIMEOUT")

    def test_dialogs_a_rule_answers_do_not_count(self):
        prompt = {"title": "Layout", "text": "确定设计状态", "buttons": ["Open", "Cancel"]}
        with world(FakeApp(), blocking=[]):
            self.assertIsNone(L.holding_dialog(PID, [prompt], dialogs.LAYOUT_PROMPTS))
            self.assertIs(L.holding_dialog(PID, [prompt], ()), prompt)

    def test_a_rules_dialog_without_its_button_waits_for_a_person(self):
        # the answerer presses a rule's button only; a box with the marker but not the button stays
        failed = {"title": "Layout", "text": "确定设计状态 failed", "buttons": ["OK"]}
        with world(FakeApp(), blocking=[]):
            self.assertIs(L.holding_dialog(PID, [failed], dialogs.LAYOUT_PROMPTS), failed)

    def test_a_raw_com_fault_is_classified(self):
        def fail():
            raise FakeComError(text="unexpected")

        with world(FakeApp()), self.assertRaises(AdapterError) as caught:
            L.call(fail, 1.0, PID, what="reading")
        self.assertEqual(code(caught), "E_NATIVE_FAILURE")

    def test_a_long_job_raises_only_after_com_is_uninitialized_and_without_its_frames(self):
        events = []

        class Initialized:
            def __enter__(self):
                events.append("init")

            def __exit__(self, *exc):
                events.append("uninit")

        def work():
            holder = object()  # noqa: F841 - a local the frame keeps
            raise AdapterError("E_NATIVE_FAILURE", "failed", dispatch_state="dispatched")

        with mock.patch.object(com, "initialized", Initialized), self.assertRaises(AdapterError) as caught:
            L.run_com(work)
        self.assertEqual(events, ["init", "uninit"])
        names = [frame.name for frame in traceback.extract_tb(caught.exception.__traceback__)]
        self.assertNotIn("work", names)

    def test_a_long_job_shows_blocked_while_a_dialog_waits(self):
        found = [{"title": "Question", "text": "", "buttons": ["OK"]}]
        progress = Progress()
        with world(FakeApp()), mock.patch.object(dialogs, "blocking_dialogs", lambda pid: list(found)):
            with L.watch_dialogs(progress, PID, (), interval_s=0.01):
                deadline = time.monotonic() + 2
                while not progress.events and time.monotonic() < deadline:
                    time.sleep(0.01)
                found.clear()
                while len(progress.events) < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
        self.assertEqual(progress.events[0][:2], ("blocked", "dismiss_dialog"))
        self.assertEqual(progress.events[1], ("unblocked",))

    def test_who_needs_a_person(self):
        self.assertFalse(L.needs_person({"buttons": ["取消(C)"]}))
        self.assertTrue(L.needs_person({"buttons": ["OK", "Cancel"]}))
        self.assertTrue(L.needs_person({"buttons": []}))


class InstancesTest(unittest.TestCase):
    def test_no_layout_no_instance(self):
        with world(FakeApp(), running=[]):
            self.assertEqual(L.instances(2.0), [])

    def test_the_reached_layout_is_ready_with_its_board_and_no_licence_step(self):
        doc = FakeDoc()
        with world(FakeApp(doc)):
            rows = L.instances(2.0)
        self.assertEqual(rows, [{"pid": PID, "state": "ready", "documents": [BOARD]}])
        self.assertEqual(doc.validated, [])

    def test_a_licence_window_blocks_the_reached_layout(self):
        with world(FakeApp(FakeDoc())), mock.patch.object(dialogs, "licence_windows", lambda pid: ["Siemens Licensing"]):
            row = L.instances(2.0)[0]
        self.assertEqual((row["state"], row["blocker"]), ("blocked", {"action": "activate_license", "title": "Siemens Licensing",
                                                                      "knowledge": ["xpedition-license-sign-in"]}))

    def test_a_dialog_box_blocks_the_layout_that_still_answers(self):
        question = dialogs.Dialog(hwnd=5, pid=PID, process="ExpeditionPCB.exe", title="Xpedition Layout", text="",
                                  buttons=["Save", "Don't Save", "Cancel"])
        progress = dialogs.Dialog(hwnd=6, pid=PID, process="ExpeditionPCB.exe", title="Opening", text="", buttons=["Cancel"])
        quiet = dialogs.Dialog(hwnd=7, pid=PID, process="ExpeditionPCB.exe", title="Note", text="", buttons=[])
        with world(FakeApp(FakeDoc())), mock.patch.object(dialogs, "find_dialogs", lambda pid=None: [progress, quiet]):
            self.assertEqual(L.instances(2.0)[0]["state"], "ready")  # a progress box waits for nobody
        with world(FakeApp(FakeDoc())), mock.patch.object(dialogs, "find_dialogs", lambda pid=None: [progress, question]):
            row = L.instances(2.0)[0]
        self.assertEqual((row["state"], row["blocker"]["action"], row["blocker"]["knowledge"]),
                         ("blocked", "dismiss_dialog", ["layout-open-prompts"]))
        self.assertEqual(row["blocker_buttons"], ["Save", "Don't Save", "Cancel"])  # for sessions to tell the job's own dialogs

    def test_another_layout_is_unreachable(self):
        with world(FakeApp(None), running=[process(), process(77, "D:/x/ExpeditionPCB.exe")]):
            rows = {row["pid"]: row for row in L.instances(2.0)}
        self.assertEqual(rows[PID]["state"], "ready")
        self.assertEqual((rows[77]["state"], rows[77]["reason"]), ("unreachable", L.OTHER_INSTANCE))

    def test_a_layout_not_yet_registered_is_starting(self):
        with world(None):
            self.assertEqual(L.instances(2.0)[0]["state"], "starting")

    def test_a_layout_that_does_not_answer_is_unresponsive_or_blocked(self):
        question = {"title": "Database Recovery", "text": "", "buttons": ["OK", "Cancel"]}

        def late(fn, timeout):
            if timeout != L.DIALOG_LOOK_S:
                raise TimeoutError  # the COM probe does not answer; the look for a dialog does
            return fn()

        with world(FakeApp()), mock.patch.object(com, "call_with_timeout", late):
            self.assertEqual(L.instances(2.0)[0]["state"], "unresponsive")
        with world(FakeApp(), blocking=[question]), mock.patch.object(com, "call_with_timeout", late):
            row = L.instances(2.0)[0]
        self.assertEqual((row["state"], row["blocker"]), ("blocked", {"action": "dismiss_dialog", "title": "Database Recovery",
                                                                      "knowledge": ["layout-open-prompts"]}))
        output = {"title": "ODB++ Design Output", "text": "", "buttons": ["OK", "Cancel"]}  # left open by an export that was stopped
        self.assertEqual(L.blocker(output)["knowledge"], ["layout-output-setups"])
        self.assertEqual(L.human(output, "exporting", None).data["knowledge"], ["layout-output-setups"])

    def test_a_dialog_look_that_hangs_leaves_the_layout_unresponsive(self):
        with world(FakeApp()), mock.patch.object(com, "call_with_timeout", mock.Mock(side_effect=TimeoutError)):
            self.assertEqual(L.instances(2.0)[0]["state"], "unresponsive")

    def test_a_refused_probe_is_busy(self):
        def busy(fn, timeout):
            raise AdapterError("E_BUSY", "busy", dispatch_state="not_started")

        with world(FakeApp()), mock.patch.object(com, "call_with_timeout", busy):
            self.assertEqual(L.instances(2.0)[0]["state"], "busy")

    def test_session_list_reads_layout_through_the_application_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            from xpedition_cli._driver.state import AdapterStore, StateDir

            store = AdapterStore(StateDir(tmp))
            with world(FakeApp(FakeDoc())), mock.patch("xpedition_cli.adapter.designer.instances", lambda timeout: []):
                items = sessions.list_sessions(store)["items"]
        self.assertEqual([(item["application"]["name"], item["pid"]) for item in items], [(L.APPLICATION, PID)])


class DocumentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "PCB", "Demo.pcb")
        Path(self.path).parent.mkdir()
        Path(self.path).write_bytes(b"board")

    def tearDown(self):
        self.tmp.cleanup()

    def test_open_answers_the_open_prompts_and_takes_the_licence(self):
        app = FakeApp(None)
        with world(app):
            L.open_document(PID, self.path)
        self.assertEqual(app.calls, [("OpenDocument", self.path)])
        self.assertEqual(FakeAnswerer.made[0].rules, dialogs.LAYOUT_PROMPTS)
        self.assertEqual(app.ActiveDocument.validated, [0, "token-7"])

    def test_open_tries_again_after_a_bare_exception(self):
        app = FakeApp(None)
        opened = app.OpenDocument
        refusals = [1]

        def refuse_once(path):
            if refusals:
                refusals.pop()
                app.calls.append(("OpenDocument", path))
                raise Exception(L.DISP_E_EXCEPTION, "Exception occurred.", None, None)
            return opened(path)

        app.OpenDocument = refuse_once
        with world(app), mock.patch.object(L, "OPEN_RETRY_S", 0.0):
            L.open_document(PID, self.path)
        self.assertEqual(app.calls, [("OpenDocument", self.path)] * 2)
        self.assertEqual(app.ActiveDocument.validated, [0, "token-7"])

    def test_a_board_whose_log_says_why_is_not_tried_again_and_its_log_viewers_end(self):
        app = FakeApp(None)

        def refuse(path):
            app.calls.append(("OpenDocument", path))
            logs = Path(path).parent / "LogFiles"
            logs.mkdir(exist_ok=True)
            (logs / L.OPEN_LOG).write_bytes(b"\r\nXpedition Layout Version: 126.0\r\n\r\nProject database [D:/demo/database] was not "
                                            b"closed or disconnected properly.\r\n")
            raise Exception(L.DISP_E_EXCEPTION, "Exception occurred.", None, None)

        app.OpenDocument = refuse
        viewers = [SimpleNamespace(pid=71, parent_pid=PID), SimpleNamespace(pid=72, parent_pid=999)]
        with world(app), mock.patch.object(L, "OPEN_RETRY_S", 0.0), \
                mock.patch.object(L.processes, "processes", return_value=viewers), mock.patch.object(L.processes, "end") as end:
            with self.assertRaises(AdapterError) as caught:
                L.open_document(PID, self.path)
        self.assertEqual(app.calls, [("OpenDocument", self.path)])
        self.assertIn("PCBLogFile.txt says: Project database [D:/demo/database] was not closed or disconnected properly.",
                      str(caught.exception))
        end.assert_called_once_with(71)

    def test_an_open_log_from_before_the_call_is_not_its_reason(self):
        log = Path(self.tmp.name) / L.OPEN_LOG
        log.write_bytes(b"Xpedition Layout Version: 126.0\r\n????????\r\nSomething old.\r\n")
        self.assertEqual(L.open_log_reason(log, time.time() - 60), "Something old.")
        log.write_bytes(b"\r\nXpedition Layout Version: 126.0\r\n\r\n????????\r\n")
        self.assertEqual(L.open_log_reason(log, time.time() - 60), "")
        os.utime(log, (time.time() - 600, time.time() - 600))
        self.assertEqual(L.open_log_reason(log, time.time()), "")
        self.assertEqual(L.open_log_reason(Path(self.tmp.name) / "missing.txt", 0.0), "")

    def test_open_refuses_while_another_board_is_open(self):
        app = FakeApp(FakeDoc(os.path.join(self.tmp.name, "Other.pcb")))
        with world(app), self.assertRaises(AdapterError) as caught:
            L.open_document(PID, self.path)
        self.assertEqual((caught.exception.data["conflict"], caught.exception.data["dispatch_state"]), ("application_state", "not_started"))
        self.assertIn("doc close", str(caught.exception))
        self.assertEqual(app.calls, [])

    def test_open_of_the_open_board_sends_nothing(self):
        app = FakeApp(FakeDoc(self.path))
        with world(app):
            L.open_document(PID, self.path)
        self.assertEqual(app.calls, [])

    def test_open_of_a_missing_board_is_not_found(self):
        with world(FakeApp(None)), self.assertRaises(AdapterError) as caught:
            L.open_document(PID, os.path.join(self.tmp.name, "None.pcb"))
        self.assertEqual(code(caught), "E_NOT_FOUND")

    def test_save_must_answer_true(self):
        doc = FakeDoc(self.path)
        with world(FakeApp(doc)):
            L.save_document(PID, self.path)
            doc.save_answer = False
            with self.assertRaises(AdapterError) as caught:
                L.save_document(PID, self.path)
        self.assertEqual((code(caught), caught.exception.data["dispatch_state"]), ("E_NATIVE_FAILURE", "dispatched"))

    def test_close_closes_without_saving_and_checks(self):
        doc = FakeDoc(self.path)
        app = FakeApp(doc)
        doc.Close = lambda save: (doc.calls.append(("Close", save)), setattr(app, "ActiveDocument", None))
        with world(app):
            L.close_document(PID, self.path, False)
        self.assertEqual(doc.calls, [("Close", False)])

    def test_a_board_still_open_after_close_is_a_failure(self):
        with world(FakeApp(FakeDoc(self.path))), self.assertRaises(AdapterError) as caught:
            L.close_document(PID, self.path, True)
        self.assertEqual(code(caught), "E_NATIVE_FAILURE")


class QuitTest(unittest.TestCase):
    def test_quit_closes_the_board_unsaved_then_goes(self):
        # Layout asks to save a board on quit even right after a save; the runtime has already
        # refused this tool's unsaved changes, so the board is closed without saving, as doc close does
        doc = FakeDoc()
        app = FakeApp(doc)
        with world(app):
            L.quit(PID, False)
        self.assertEqual((doc.calls, app.calls), ([("Close", False)], [("Quit",)]))

    def test_discard_closes_the_board_unsaved_first(self):
        doc = FakeDoc()
        app = FakeApp(doc)
        with world(app):
            L.quit(PID, True)
        self.assertEqual(doc.calls, [("Close", False)])

    def test_a_layout_held_by_a_dialog_needs_a_person(self):
        question = {"title": "Save changes?", "text": "", "buttons": ["Yes", "No"]}
        with world(FakeApp(FakeDoc()), blocking=[question]), mock.patch.object(processes, "alive", lambda pid: True), \
                mock.patch.object(L, "QUIT_WAIT_S", 0.0):
            with self.assertRaises(AdapterError) as caught:
                L.quit(PID, False)
        self.assertEqual((code(caught), caught.exception.data["action"]), ("E_HUMAN_REQUIRED", "dismiss_dialog"))

    def test_a_layout_left_on_its_start_page_is_ended(self):
        ended: list[int] = []
        alive = {PID: True}

        def end(pid: int) -> bool:
            ended.append(pid)
            alive[pid] = False
            return True

        with world(FakeApp(FakeDoc())), mock.patch.object(processes, "alive", lambda pid: alive[pid]), \
                mock.patch.object(processes, "end", end), mock.patch.object(L, "QUIT_WAIT_S", 0.0):
            L.quit(PID, False)
        self.assertEqual(ended, [PID])

    def test_a_layout_that_cannot_be_ended_is_reported_with_the_held_file(self):
        with world(FakeApp(FakeDoc())), mock.patch.object(processes, "alive", lambda pid: True), \
                mock.patch.object(processes, "end", lambda pid: False), mock.patch.object(L, "QUIT_WAIT_S", 0.0):
            with self.assertRaises(AdapterError) as caught:
                L.quit(PID, False)
        self.assertEqual(caught.exception.data["knowledge"], ["layout-drillprefs-held"])


class StartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.launcher = Path(self.tmp.name) / "common" / "win64" / "bin" / "ExpeditionPCB.exe"

    def tearDown(self):
        self.tmp.cleanup()

    def _patches(self, popen):
        return [mock.patch.object(env, "configure_environment", lambda: None), mock.patch.object(env, "launcher", lambda name: self.launcher),
                mock.patch.object(L.subprocess, "Popen", popen), mock.patch.object(L, "START_POLL_S", 0.0)]

    def test_a_running_layout_is_not_started_again(self):
        with world(FakeApp()), self.assertRaises(AdapterError) as caught:
            L.start(None, 1.0)
        self.assertEqual((code(caught), caught.exception.data["conflict"]), ("E_CONFLICT", "application_state"))

    def test_start_goes_through_the_launcher_and_reports_starting_until_registered(self):
        started = []
        running: list = []

        def popen(args, **kwargs):
            started.append(args)
            running.append(process())
            return SimpleNamespace(pid=11, poll=lambda: None)

        with world(None, running=running):
            patches = self._patches(popen)
            for patch in patches:
                patch.start()
            try:
                out = L.start(None, 0.05)
            finally:
                for patch in patches:
                    patch.stop()
        self.assertEqual(started, [[str(self.launcher)]])
        self.assertEqual(out, {"pid": PID, "state": "starting"})

    def test_a_registered_layout_opens_the_board(self):
        board = Path(self.tmp.name) / "Demo.pcb"
        board.write_bytes(b"board")
        app = FakeApp(None)
        running: list = []

        def popen(*args, **kwargs):
            running.append(process())
            return SimpleNamespace(pid=11, poll=lambda: None)

        with world(app, running=running):
            patches = self._patches(popen) + [mock.patch.object(L, "OPEN_MIN_S", 0.0)]
            for patch in patches:
                patch.start()
            try:
                out = L.start(str(board), 5.0)
            finally:
                for patch in patches:
                    patch.stop()
        self.assertEqual(out, {"pid": PID, "state": "ready"})
        self.assertEqual(app.calls, [("OpenDocument", str(board))])

    def _start_with_open_failing(self, error: Exception) -> tuple[object, mock.Mock]:
        board = Path(self.tmp.name) / "Demo.pcb"
        board.write_bytes(b"board")
        running: list = []

        def popen(*args, **kwargs):
            running.append(process())
            return SimpleNamespace(pid=11, poll=lambda: None)

        quit_ = mock.Mock()
        with world(FakeApp(None), running=running):
            patches = self._patches(popen) + [mock.patch.object(L, "OPEN_MIN_S", 0.0),
                                              mock.patch.object(L, "open_document", side_effect=error),
                                              mock.patch.object(L, "quit", quit_)]
            for patch in patches:
                patch.start()
            try:
                try:
                    return L.start(str(board), 5.0), quit_
                except AdapterError as exc:
                    return exc, quit_
            finally:
                for patch in patches:
                    patch.stop()

    def test_a_board_that_fails_to_open_quits_the_layout_started_for_it(self):
        failed, quit_ = self._start_with_open_failing(
            AdapterError("E_NATIVE_FAILURE", "Xpedition Layout: exception occurred (0x80020009)", dispatch_state="dispatched"))
        self.assertIsInstance(failed, AdapterError)
        self.assertEqual(failed.data["code"], "E_NATIVE_FAILURE")
        self.assertIn("The Layout started for it was quit.", str(failed))
        quit_.assert_called_once_with(PID, True)

    def test_a_board_held_by_a_dialog_or_still_opening_leaves_layout_as_a_session(self):
        blocked, quit_blocked = self._start_with_open_failing(AdapterError("E_HUMAN_REQUIRED", "a dialog", dispatch_state="dispatched"))
        busy, quit_busy = self._start_with_open_failing(AdapterError("E_TIMEOUT", "still opening", dispatch_state="dispatched"))
        self.assertEqual((blocked, busy), ({"pid": PID, "state": "blocked"}, {"pid": PID, "state": "busy"}))
        quit_blocked.assert_not_called()
        quit_busy.assert_not_called()

    def test_a_launcher_that_ends_without_layout_is_a_failure(self):
        popen = mock.Mock(return_value=SimpleNamespace(pid=11, poll=lambda: 3221225781))
        with world(None, running=[]):
            patches = self._patches(popen)
            for patch in patches:
                patch.start()
            try:
                with self.assertRaises(AdapterError) as caught:
                    L.start(None, 1.0)
            finally:
                for patch in patches:
                    patch.stop()
        self.assertIn("0xC0000135", str(caught.exception))


class ReadKeysTest(unittest.TestCase):
    def test_each_key_reads_its_own_fields_and_unknown_keys_are_unreadable(self):
        from test_layout_fakes import FakeComponent

        part = FakeComponent("R1", placed=True, x=10.0, y=5.0, rotation=-90.0)
        doc = FakeDoc(components=[part], nets=["N/1"], width=70.0, height=48.0)
        doc.net("N/1").routed, doc.net("N/1").NumberOfOpens = True, 0
        keys = [base.key(BOARD, "/outline"), base.key(BOARD, "/components/R1"), base.key(BOARD, f"/nets/{L.segment('N/1')}"),
                base.key(BOARD, "/forward-annotation"), base.key(BOARD, "/elsewhere"), base.key("D:/other.pcb", "/outline")]
        with world(FakeApp(doc)):
            out = L.read_keys(doc, BOARD, keys, ["width_mm", "x_mm", "rotation_deg", "routed", "status"])
        observed = out["observed"]
        self.assertEqual(observed[keys[0]], {"width_mm": 70.0})
        self.assertEqual(observed[keys[1]], {"x_mm": 10.0, "rotation_deg": 270.0})
        self.assertEqual(observed[keys[2]], {"routed": True})
        self.assertEqual(observed[keys[3]], {"status": "required"})
        self.assertNotIn(keys[4], observed)
        self.assertNotIn(keys[5], observed)
        self.assertIn("routed", out["unreadable"])

    def test_read_targets_reads_the_saved_board(self):
        doc = FakeDoc(width=30.0, height=20.0)
        with world(FakeApp(doc)):
            out = L.read_targets(PID, BOARD, [base.key(BOARD, "/outline")], ["height_mm"])
        self.assertEqual(out["observed"][base.key(BOARD, "/outline")], {"height_mm": 20.0})

    def test_the_outline_radius_is_read_in_the_unit_the_outline_shows(self):
        doc = FakeDoc(unit=L.UNIT_MM)
        self.assertEqual(L.outline_values(doc)["radius_mm"], 0.0)
        from test_layout_fakes import Outline

        from xpedition_cli.adapter.plan import outline as plan_outline

        doc.BoardOutline = Outline(plan_outline.rounded(0, 0, 40, 30, 3.0))
        self.assertEqual(L.outline_values(doc)["radius_mm"], 3.0)  # the fake's raw points are in mils, as its outline shows
        doc.CurrentUnit = L.UNIT_MILS
        self.assertEqual(L.outline_values(doc)["radius_mm"], 3.0)


class ModuleTest(unittest.TestCase):
    def test_the_handlers_of_the_board_commands(self):
        self.assertEqual(sorted(h.command for h in L.HANDLERS), sorted([
            "forward-annotation run", "pcb update", "placement run", "plane create", "router run", "pcb get", "pcb check", "pcb export",
            "constraint get", "constraint import", "trace check", "trace create", "trace delete", "via delete", "plane delete", "via update", "via create",
            "solder-mask create", "solder-mask delete", "pcb render", "component move", "mounting-hole list",
            "mounting-hole create", "mounting-hole delete", "refdes move", "silkscreen-text create", "silkscreen-text delete",
            "keepout create", "keepout delete"]))
        self.assertEqual([h.command for h in L.HANDLERS if h.long_job],
                         ["forward-annotation run", "placement run", "router run", "pcb export", "pcb render", "constraint import",
                          "trace create", "via create"])

    def test_the_version_is_empty_without_an_install(self):
        with mock.patch.object(env, "version", mock.Mock(side_effect=AdapterError("E_BACKEND_UNAVAILABLE", "none"))):
            self.assertEqual(L.VERSION, "")

    def test_a_write_on_another_session_kind_is_refused(self):
        ctx = base.Context(mock.Mock(), {"command": "pcb get", "inputs": {}, "session": "designer-1-1", "document": BOARD})
        with self.assertRaises(AdapterError):
            L.in_layout(ctx, lambda app, doc: None, budget_s=1.0, what="x")

    def test_targets_quote_what_a_key_cannot_hold(self):
        self.assertEqual(L.segment("+3V3/A#1"), "+3V3%2FA%231")
        self.assertEqual(L.fragment(base.key(BOARD, "/planes/+3V3%2FA%231/2"), BOARD), ["planes", "+3V3/A#1", "2"])
        self.assertIsNone(L.fragment(base.key(BOARD), BOARD))

    def test_context_helper_names_a_layout_session(self):
        self.assertEqual(context("pcb get").session.pid, PID)


if __name__ == "__main__":
    unittest.main()
