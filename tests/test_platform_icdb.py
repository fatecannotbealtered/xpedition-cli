"""Unit tests of the wait for Xpedition's database server to let a closed project go. No
Xpedition is needed: the server's address file and process are faked."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from xpedition_cli._driver.state import AdapterStore, StateDir
from xpedition_cli.adapter import sessions
from xpedition_cli.adapter.platform import icdb

SERVER = [mock.Mock(pid=900)]


class ProjectFolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name, "Demo")
        (self.project / "database" / "cdbsvr").mkdir(parents=True)
        (self.project / "PCB").mkdir()
        self.address = self.project / "database" / "cdbsvr" / "sAddress.adr"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_project_file_and_its_board_belong_to_the_same_project(self):
        folder = os.path.normcase(str(self.project))
        self.assertEqual(icdb.project_folder(self.project / "Demo.prj"), folder)
        self.assertEqual(icdb.project_folder(self.project / "PCB" / "Demo.pcb"), folder)
        self.assertIsNone(icdb.project_folder(Path(self.tmp.name, "loose.pcb")))

    def test_an_address_file_with_no_server_running_is_stale(self):
        self.address.write_text("host:1", encoding="utf-8")
        folder = icdb.project_folder(self.project / "Demo.prj")
        with mock.patch.object(icdb.processes, "processes", return_value=[]):
            self.assertFalse(icdb.served(folder))
        with mock.patch.object(icdb.processes, "processes", return_value=SERVER):
            self.assertTrue(icdb.served(folder))

    def test_the_wait_ends_when_the_server_lets_go_or_its_time_is_up(self):
        self.address.write_text("host:1", encoding="utf-8")
        folder = icdb.project_folder(self.project / "Demo.prj")
        with mock.patch.object(icdb.processes, "processes", return_value=SERVER):
            self.assertFalse(icdb.wait_released(folder, budget_s=0.2))
            timer = threading.Timer(0.2, self.address.unlink)
            timer.start()
            self.assertTrue(icdb.wait_released(folder, budget_s=5.0))
            timer.join()


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AdapterStore(StateDir(os.path.join(self.tmp.name, "state")))
        self.project = Path(self.tmp.name, "Demo")
        (self.project / "database" / "cdbsvr").mkdir(parents=True)
        (self.project / "PCB").mkdir()
        self.prj = str(self.project / "Demo.prj")
        self.pcb = str(self.project / "PCB" / "Demo.pcb")
        (self.project / "database" / "cdbsvr" / "sAddress.adr").write_text("host:1", encoding="utf-8")
        patcher = mock.patch.object(icdb.processes, "processes", return_value=SERVER)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def listing(self, *items):
        return mock.patch.object(sessions, "list_sessions", return_value={"items": list(items), "complete": True})

    def test_a_project_no_longer_served_is_not_waited_for(self):
        (self.project / "database" / "cdbsvr" / "sAddress.adr").unlink()
        with mock.patch.object(sessions, "list_sessions") as listed:
            self.assertEqual(sessions.release(self.store, [self.prj], "designer-1-1"), [])
        listed.assert_not_called()

    def test_a_project_another_session_has_open_is_not_waited_for(self):
        layout = {"id": "layout-2-1", "documents": [{"path": self.pcb}]}
        with self.listing(layout), mock.patch.object(icdb, "wait_released") as waited:
            self.assertEqual(sessions.release(self.store, [self.prj], "designer-1-1"), [])
        waited.assert_not_called()

    def test_the_closing_session_does_not_hold_its_own_project(self):
        closing = {"id": "designer-1-1", "documents": [{"path": self.prj}]}
        with self.listing(closing), mock.patch.object(icdb, "wait_released", return_value=True) as waited:
            self.assertEqual(sessions.release(self.store, [self.prj], "designer-1-1"), [])
        waited.assert_called_once_with(icdb.project_folder(self.prj))

    def test_a_project_still_served_when_the_wait_ends_gets_a_warning(self):
        with self.listing(), mock.patch.object(icdb, "wait_released", return_value=False):
            notices = sessions.release(self.store, [self.pcb])
        self.assertEqual([n["severity"] for n in notices], ["warning"])
        self.assertIn("sAddress.adr", notices[0]["message"])

    def test_session_close_reports_done_once_the_server_let_its_project_go(self):
        item = {"id": "designer-1-1", "documents": [{"path": self.prj}]}
        app = mock.Mock()
        with mock.patch.object(sessions, "_session", return_value=(sessions._ref("designer-1-1"), item)), \
                mock.patch.object(sessions, "_app", return_value=app), \
                mock.patch.object(sessions, "release", return_value=[{"severity": "warning", "message": "held"}]) as released:
            out = sessions.close_session(self.store, {"session": "designer-1-1"})
        app.quit.assert_called_once_with(1, False)
        released.assert_called_once_with(self.store, [self.prj], "designer-1-1")
        self.assertEqual(out["notices"], [{"severity": "warning", "message": "held"}])


if __name__ == "__main__":
    unittest.main()
