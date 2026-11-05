"""Unit tests of the adapter's core: session IDs and target keys, document state and the save
preview, long jobs and their worker. No Xpedition is needed."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from xpedition_cli import adapter as adapter_pkg
from xpedition_cli._driver import AdapterError
from xpedition_cli._driver.state import AdapterStore, StateDir
from xpedition_cli.adapter import base, jobs, sessions, worker


def store_in(folder: str) -> AdapterStore:
    return AdapterStore(StateDir(folder))


class BaseTest(unittest.TestCase):
    def test_session_ids_name_the_program_and_process(self):
        ref = base.parse_session("designer-4120-6553f1a0")
        self.assertEqual((ref.kind, ref.pid), ("designer", 4120))
        self.assertIsNone(base.parse_session(None))
        with self.assertRaises(AdapterError) as caught:
            base.parse_session("viewer-1-2")
        self.assertEqual(caught.exception.data["code"], "E_NOT_FOUND")

    def test_target_keys_follow_the_runtime(self):
        self.assertEqual(base.key("D:/work/demo/Demo.prj", "/parts/R1"), "file:///D:/work/demo/Demo.prj#/parts/R1")
        out = base.preview("Do it.", [], [], blocked_by=["no"])
        self.assertEqual((out["can_apply"], out["blocked_by"]), (False, ["no"]))

    def test_inputs_are_required_by_name(self):
        ctx = base.Context(mock.Mock(), {"command": "part get", "inputs": {}})
        with self.assertRaises(AdapterError) as caught:
            base.need_input(ctx, "file")
        self.assertIn("--file", str(caught.exception))


class DocumentStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store_in(self.tmp.name)
        self.doc = os.path.join(self.tmp.name, "Demo.prj")
        Path(self.doc).write_text("x", encoding="utf-8")
        self.sid = "designer-1-1"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_change_makes_the_document_dirty_and_a_save_clears_it(self):
        change = base.change(base.key(self.doc, "/schematic/nets/GND"), "pins", None, ["R1.2"])
        sessions.note_change(self.store, self.sid, self.doc, [change])
        state = sessions.doc_state(self.store, self.sid, self.doc)
        self.assertTrue(state["dirty"])
        self.assertEqual(state["changes"][0]["origin"], "tool")
        preview = sessions.save_preview(self.store, {"session": self.sid, "document": self.doc})
        self.assertEqual(len(preview["changes"]), 1)
        sessions.note_saved(self.store, self.sid, self.doc)
        self.assertFalse(sessions.doc_state(self.store, self.sid, self.doc)["dirty"])
        self.assertEqual(sessions.save_preview(self.store, {"session": self.sid, "document": self.doc})["changes"], [])

    def test_file_mode_documents_are_project_files(self):
        found = sessions.documents(self.store, {"path": self.doc})["items"]
        self.assertEqual([d["id"] for d in found], [os.path.abspath(self.doc)])
        self.assertEqual(found[0]["disk"], "in_sync")
        self.assertEqual(sessions.documents(self.store, {"path": self.tmp.name})["items"], [])

    def test_session_open_needs_a_project_or_a_board(self):
        with self.assertRaises(AdapterError) as caught:
            sessions.open_session(self.store, {"operation_id": "op_1"})
        self.assertEqual(caught.exception.data["dispatch_state"], "not_started")


class _LongHandler(base.Handler):
    command = "demo run"
    long_job = True

    def run(self, ctx: base.Context, progress: Any) -> dict[str, Any]:
        progress(1, 2, "steps")
        progress(2, 2, "steps")
        return {"change": "applied", "persistence": "unchanged", "result": {"done": True}}


class _RefusingHandler(_LongHandler):
    command = "demo refuse"

    def run(self, ctx: base.Context, progress: Any) -> dict[str, Any]:
        raise AdapterError("E_VALIDATION", "bad input", dispatch_state="not_started")


class JobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store_in(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _job(self, command: str) -> Path:
        path = jobs.job_file(self.store, "job-test")
        jobs.write(path, {"id": "job-test", "state": "running", "request": {"command": command, "inputs": {}},
                          "cancel_requested": False, "pid": None, "started_at": jobs.utc_now()})
        return path

    def test_the_worker_runs_the_handler_and_reports_the_result(self):
        path = self._job("demo run")
        with mock.patch.dict(adapter_pkg.HANDLERS, {"demo run": _LongHandler()}, clear=False):
            self.assertEqual(worker.run(path), 0)
        status = jobs.status(self.store, "job-test")
        self.assertEqual((status["state"], status["change"], status["result"]), ("completed", "applied", {"done": True}))
        self.assertEqual(status["progress"], {"done": 2, "total": 2, "unit": "steps"})

    def test_a_refused_job_changed_nothing(self):
        path = self._job("demo refuse")
        with mock.patch.dict(adapter_pkg.HANDLERS, {"demo refuse": _RefusingHandler()}, clear=False):
            worker.run(path)
        status = jobs.status(self.store, "job-test")
        self.assertEqual((status["state"], status["change"]), ("failed", "not_applied"))

    def test_a_cancel_request_stops_the_job_between_steps(self):
        path = self._job("demo run")
        record = jobs.read(path)
        record["cancel_requested"] = True
        jobs.write(path, record)
        with mock.patch.dict(adapter_pkg.HANDLERS, {"demo run": _LongHandler()}, clear=False):
            worker.run(path)
        self.assertEqual(jobs.status(self.store, "job-test")["state"], "cancelled")

    def test_a_worker_that_is_gone_leaves_the_job_unknown(self):
        path = self._job("demo run")
        record = jobs.read(path)
        record["pid"] = 999999
        jobs.write(path, record)
        with mock.patch.object(jobs, "pid_alive", return_value=False):
            status = jobs.status(self.store, "job-test")
        self.assertEqual(status["state"], "unknown")

    def test_an_unknown_job_is_not_found(self):
        with self.assertRaises(AdapterError) as caught:
            jobs.status(self.store, "job-none")
        self.assertEqual(caught.exception.data["code"], "E_NOT_FOUND")


class _ChangeHandler(base.Handler):
    command = "demo update"

    def preview(self, ctx: base.Context) -> dict[str, Any]:
        return base.preview("Change.", [], [base.change(base.key(ctx.document_path(), "/a"), "v", 1, 2)])

    def apply(self, ctx: base.Context) -> dict[str, Any]:
        return base.facts()


class AdapterTest(unittest.TestCase):
    def test_a_session_write_is_kept_for_the_save_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = store_in(tmp)
            doc = os.path.join(tmp, "Demo.pcb")
            request = {"command": "demo update", "inputs": {}, "session": "layout-2-2", "document": doc}
            ad = adapter_pkg.Adapter(store)
            with mock.patch.dict(adapter_pkg.HANDLERS, {"demo update": _ChangeHandler()}, clear=False):
                ad.preview(dict(request))
                ad.apply({**request, "operation_id": "op_1"})
            state = sessions.doc_state(store, "layout-2-2", doc)
            self.assertTrue(state["dirty"])
            self.assertEqual(state["changes"][0]["after"], 2)

    def test_an_unknown_command_is_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            ad = adapter_pkg.Adapter(store_in(tmp))
            with self.assertRaises(AdapterError) as caught:
                ad.read({"command": "nothing get", "inputs": {}})
            self.assertEqual(caught.exception.data["code"], "E_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
