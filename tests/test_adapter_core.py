"""Unit tests of the adapter's core: session IDs and target keys, document state and the save
preview, and the commands the runtime runs as long jobs. No Xpedition is needed."""
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
from xpedition_cli.adapter import base, jobs, sessions


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
        in_folder = sessions.documents(self.store, {"path": self.tmp.name})["items"]
        self.assertEqual([d["id"] for d in in_folder], [os.path.abspath(self.doc)])
        self.assertEqual(sessions.documents(self.store, {"path": os.path.join(self.tmp.name, "none.txt")})["items"], [])

    def test_documents_have_a_kind_and_unknown_ones_may_be_dirty(self):
        found = sessions.documents(self.store, {"path": self.doc})["items"][0]
        self.assertEqual((found["kind"], found["dirty"]), ("project", False))  # a file on disk
        self.assertIsNone(sessions.doc_state(self.store, self.sid, self.doc)["dirty"])  # never opened by the tool
        self.assertEqual(sessions.doc_kind("D:/w/PCB/Demo.pcb"), "board")

    def _fake_app(self) -> mock.Mock:
        app = mock.Mock(APPLICATION="Xpedition", VERSION="2604")
        app.start.return_value = {"pid": 42, "state": "starting"}
        app.instances.return_value = []
        return app

    def test_session_open_starts_the_program_of_the_kind(self):
        designer_app, layout_app = self._fake_app(), self._fake_app()
        apps = {base.DESIGNER: designer_app, base.LAYOUT: layout_app}
        with mock.patch.object(sessions, "_app", side_effect=lambda kind: apps[kind]):
            out = sessions.open_session(self.store, {"operation_id": "op_1", "kind": "board"})
            self.assertTrue(out["session"]["id"].startswith("layout-42-"))
            layout_app.start.assert_called_once_with(None, sessions.START_WAIT_S)
            sessions.open_session(self.store, {"operation_id": "op_2"})
            designer_app.start.assert_called_once_with(None, sessions.START_WAIT_S)
            sessions.open_session(self.store, {"operation_id": "op_3", "path": self.doc})
            sid = [s for s in sessions._owned(self.store) if s.startswith("designer-")][0]
            self.assertFalse(sessions.doc_state(self.store, sid, os.path.abspath(self.doc))["dirty"])  # opened by the tool

    def test_session_open_refuses_a_wrong_kind_before_starting_anything(self):
        app = self._fake_app()
        with mock.patch.object(sessions, "_app", return_value=app):
            for request in ({"kind": "library"}, {"kind": "board", "path": self.doc}):
                with self.assertRaises(AdapterError) as caught:
                    sessions.open_session(self.store, {"operation_id": "op_1", **request})
                self.assertEqual((caught.exception.data["code"], caught.exception.data["dispatch_state"]),
                                 ("E_VALIDATION", "not_started"))
        app.start.assert_not_called()

    def test_an_unreachable_instance_says_why(self):
        app = self._fake_app()
        app.instances.return_value = [{"pid": 7, "state": "unreachable", "documents": [], "reason": "Two run."}]
        with mock.patch.object(sessions, "_app", return_value=app):
            items = sessions.list_sessions(self.store)["items"]
        self.assertEqual((items[0]["state"], items[0]["extra"]), ("unreachable", {"reason": "Two run."}))
        self.assertNotIn("blocker", items[0])

    def _save(self, writes: bool) -> dict:
        app = self._fake_app()
        app.save_document.side_effect = (lambda pid, path: Path(path).write_text("saved", encoding="utf-8")) if writes else None
        app.read_targets.return_value = {"observed": {base.key(self.doc, "/a"): {"v": 2}}, "unreadable": []}
        sid = "designer-1-1"
        doc = os.path.abspath(self.doc)
        os.utime(doc, ns=(1_000_000_000, 1_000_000_000))
        with mock.patch.object(sessions, "_session", return_value=(base.parse_session(sid), {"documents": []})), \
                mock.patch.object(sessions, "_app", return_value=app):
            sessions.save_doc(self.store, {"session": sid, "document": doc, "operation_id": "op_1"})
            return sessions.save_read_back(self.store, {"session": sid, "document": doc,
                                                         "targets": [base.key(doc, "/a"), base.key(doc)],
                                                         "fields": ["v", "saved"]})

    def test_a_save_in_the_long_job_worker_reopens_the_document(self):
        app = self._fake_app()
        app.save_document.side_effect = None
        app.reopen_document.side_effect = lambda pid, path: Path(path).write_text("written on close", encoding="utf-8")
        sid, doc = "designer-1-1", os.path.abspath(self.doc)
        os.utime(doc, ns=(1_000_000_000, 1_000_000_000))
        with mock.patch.object(sessions, "_session", return_value=(base.parse_session(sid), {"documents": []})),                 mock.patch.object(sessions, "_app", return_value=app),                 mock.patch.dict(os.environ, {jobs.WORKER_ENV: "job-0123456789:ab"}):
            sessions.save_doc(self.store, {"session": sid, "document": doc, "operation_id": "op_1"})
            out = sessions.save_read_back(self.store, {"session": sid, "document": doc, "targets": [base.key(doc)],
                                                        "fields": ["saved"]})
        app.reopen_document.assert_called_once_with(1, doc)
        self.assertEqual(out["observed"][base.key(doc)], {"saved": True})

    def test_the_save_read_back_says_saved_only_when_the_files_changed(self):
        out = self._save(writes=True)
        doc_key = base.key(os.path.abspath(self.doc))
        self.assertEqual(out["observed"][doc_key], {"saved": True})
        self.assertEqual(out["observed"][base.key(os.path.abspath(self.doc), "/a")], {"v": 2})
        self.assertFalse(self._save(writes=False)["observed"][doc_key]["saved"])


class _LongHandler(base.Handler):
    command = "demo run"
    long_job = True

    def __init__(self) -> None:
        self.busy_seen: str | None = None

    def run(self, ctx: base.Context, progress: Any) -> dict[str, Any]:
        progress(1, 2, "steps")
        # another process asking meanwhile sees the instance busy
        with mock.patch.object(jobs.os, "getpid", return_value=1):
            self.busy_seen = jobs.running_on(ctx.store, "layout-2-2")
        progress(2, 2, "steps")
        return {"change": "applied", "persistence": "unchanged", "result": {"done": True}}


class _RefusingHandler(_LongHandler):
    command = "demo refuse"

    def run(self, ctx: base.Context, progress: Any) -> dict[str, Any]:
        raise AdapterError("E_VALIDATION", "bad input", dispatch_state="not_started")


class LongJobTest(unittest.TestCase):
    """The runtime calls apply in its worker; the adapter runs the handler's run there."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store_in(self.tmp.name)
        self.adapter = adapter_pkg.Adapter(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, command: str) -> dict[str, Any]:
        return {"command": command, "inputs": {}, "session": "layout-2-2", "document": os.path.join(self.tmp.name, "Demo.pcb"),
                "operation_id": "op_1"}

    def test_apply_runs_the_handler_and_reports_its_facts(self):
        handler = _LongHandler()
        with mock.patch.dict(adapter_pkg.HANDLERS, {"demo run": handler}, clear=False):
            facts = self.adapter.apply(self.request("demo run"))
        self.assertEqual((facts["dispatch_state"], facts["change"], facts["delivery"], facts["result"]),
                         ("dispatched", "applied", "not_applicable", {"done": True}))
        self.assertEqual(handler.busy_seen, "demo run")
        self.assertIsNone(jobs.running_on(self.store, "layout-2-2"))

    def test_a_refused_job_leaves_the_instance_free(self):
        with mock.patch.dict(adapter_pkg.HANDLERS, {"demo refuse": _RefusingHandler()}, clear=False):
            with self.assertRaises(AdapterError) as caught:
                self.adapter.apply(self.request("demo refuse"))
        self.assertEqual(caught.exception.data["dispatch_state"], "not_started")
        self.assertIsNone(jobs.running_on(self.store, "layout-2-2"))

    def test_a_worker_that_is_gone_does_not_keep_the_instance_busy(self):
        with mock.patch.object(jobs.os, "getpid", return_value=999999):
            with jobs.running(self.store, "layout-2-2", "demo run"):
                with mock.patch.object(jobs, "pid_alive", return_value=False):
                    self.assertIsNone(jobs.running_on(self.store, "layout-2-2"))

    def test_the_worker_is_known_by_the_runtime_variable(self):
        with mock.patch.dict(os.environ, {"XPEDITION_CLI_JOB_WORKER": "job-0123456789:ab"}):
            self.assertTrue(jobs.in_worker())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(jobs.in_worker())


class _ChangeHandler(base.Handler):
    command = "demo update"

    def preview(self, ctx: base.Context) -> dict[str, Any]:
        return base.preview("Change.", [], [base.change(base.key(ctx.document_path(), "/a"), "v", 1, 2)])

    def apply(self, ctx: base.Context) -> dict[str, Any]:
        return base.facts()


class _HeldHandler(_ChangeHandler):
    command = "demo held"

    def preview(self, ctx: base.Context) -> dict[str, Any]:
        raise base.blocked("The board has no outline.", knowledge=["layout-placement-drc"])

    def apply(self, ctx: base.Context) -> dict[str, Any]:
        raise base.blocked("The board has no outline.")


class AdapterTest(unittest.TestCase):
    def test_what_the_application_state_stands_in_the_way_of_is_a_blocked_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            ad = adapter_pkg.Adapter(store_in(tmp))
            request = {"command": "demo held", "inputs": {}, "document": os.path.join(tmp, "Demo.pcb")}
            with mock.patch.dict(adapter_pkg.HANDLERS, {"demo held": _HeldHandler()}, clear=False):
                out = ad.preview(dict(request))
                with self.assertRaises(AdapterError) as caught:
                    ad.apply({**request, "operation_id": "op_1"})
            self.assertEqual((out["can_apply"], out["blocked_by"], out["knowledge"], out["targets"], out["changes"]),
                             (False, ["The board has no outline."], ["layout-placement-drc"], [], []))
            self.assertEqual((caught.exception.data["code"], caught.exception.data["conflict"], caught.exception.data["dispatch_state"]),
                             ("E_CONFLICT", "application_state", "not_started"))

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
