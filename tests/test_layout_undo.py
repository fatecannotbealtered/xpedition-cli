"""doc undo on a board: each board write is one Layout transaction, one step on Layout's Undo stack,
and the adapter remembers the writes so that undo knows what it takes back."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

from xpedition_cli._driver import AdapterError
from xpedition_cli._driver.state import AdapterStore, StateDir
from xpedition_cli.adapter import base, sessions
from xpedition_cli.adapter import layout as L

sys.path.insert(0, os.path.dirname(__file__))

from test_layout_fakes import BOARD, SESSION, FakeApp, FakeDoc, world  # noqa: E402


def _context(store: AdapterStore, command: str) -> base.Context:
    return base.Context(store, {"command": command, "inputs": {}, "session": SESSION, "document": BOARD})


class UndoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AdapterStore(StateDir(self.tmp.name))
        self.doc = FakeDoc(BOARD)
        self.doc.TransactionStart = lambda mode: self.doc.calls.append(("TransactionStart", mode))
        self.doc.TransactionEnd = lambda keep: self.doc.calls.append(("TransactionEnd", keep))
        self.app = FakeApp(self.doc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_board_write_runs_in_one_transaction_and_another_write_does_not(self):
        with world(self.app):
            L.in_layout(_context(self.store, "via create"), lambda app, doc: base.facts(), budget_s=5, what="x", write=True)
            self.assertEqual([c for c in self.doc.calls if c[0].startswith("Transaction")],
                             [("TransactionStart", 0), ("TransactionEnd", True)])  # one Undo step
            self.doc.calls.clear()
            L.in_layout(_context(self.store, "constraint import"), lambda app, doc: base.facts(), budget_s=5, what="x", write=True)
            L.in_layout(_context(self.store, "via create"), lambda app, doc: {}, budget_s=5, what="a read")
        self.assertFalse([c for c in self.doc.calls if c[0].startswith("Transaction")])

    def test_a_checked_write_runs_with_drc_and_a_refusal_at_its_end_is_a_conflict(self):
        ends = iter([False])
        self.doc.TransactionEnd = lambda keep: self.doc.calls.append(("TransactionEnd", keep)) or next(ends)
        with world(self.app), self.assertRaises(AdapterError) as refused:
            L.in_layout(_context(self.store, "component move"), lambda app, doc: base.facts(), budget_s=5, what="moving it",
                        write=True, checked=True)
        self.assertEqual(refused.exception.data["code"], "E_CONFLICT")
        self.assertIn(("TransactionStart", L.DRC_MODE_DRC), self.doc.calls)

    def test_a_write_may_commit_its_step_early_and_it_ends_once(self):
        def work(app, doc):
            L.end_step(doc)  # as plane create does for a layer's first plane shape
            doc.calls.append(("after",))
            return base.facts()

        with world(self.app):
            L.in_layout(_context(self.store, "plane create"), work, budget_s=5, what="pouring", write=True)
        self.assertEqual([c for c in self.doc.calls if c[0] in ("TransactionStart", "TransactionEnd", "after")],
                         [("TransactionStart", 0), ("TransactionEnd", True), ("after",)])

    def test_undo_takes_back_the_last_write_and_the_save_then_expects_the_board_before_it(self):
        change = base.change(base.key(BOARD, "/vias/GND"), "count", 74, 75)
        L.remember_write(self.store, SESSION, BOARD, "via create", [change])
        sessions.note_change(self.store, SESSION, BOARD, [change])
        with world(self.app):
            facts = L.undo(_context(self.store, "doc undo"))
        self.assertEqual(facts["change"], "applied")
        self.assertEqual(self.app.Gui.commands, [L.ID_EDIT_UNDO])
        kept = sessions.doc_state(self.store, SESSION, BOARD)["changes"]
        self.assertEqual([(c["field"], c["after"]) for c in kept], [("count", 74)])
        with world(self.app), self.assertRaises(AdapterError) as nothing:
            L.undo(_context(self.store, "doc undo"))  # taken back already: nothing left
        self.assertEqual(nothing.exception.data["code"], "E_UNSUPPORTED")

    def test_a_write_that_changed_nothing_leaves_no_step_to_take_back(self):
        L.remember_write(self.store, SESSION, BOARD, "via create", [base.change(base.key(BOARD, "/vias/GND"), "count", 74, 75)])
        L.remember_write(self.store, SESSION, BOARD, "component move", [], changed=False)  # no step of Layout's
        with world(self.app):
            self.assertEqual(L.undo(_context(self.store, "doc undo"))["change"], "applied")  # the via create
        self.assertEqual(self.app.Gui.commands, [L.ID_EDIT_UNDO])

    def test_a_first_plane_on_a_layer_clears_what_undo_can_take_back(self):
        L.remember_write(self.store, SESSION, BOARD, "component move", [])
        L.undo_cleared(self.store, SESSION, BOARD)  # plane create found no plane of the net on the layer
        L.remember_write(self.store, SESSION, BOARD, "plane create", [])
        with world(self.app), self.assertRaises(AdapterError) as refused:
            L.undo(_context(self.store, "doc undo"))
        self.assertEqual(refused.exception.data["code"], "E_UNSUPPORTED")
        self.assertIn("plane delete", str(refused.exception))
        self.assertEqual(self.app.Gui.commands, [])
        L.remember_write(self.store, SESSION, BOARD, "via create", [])  # a later write can be taken back as usual
        with world(self.app):
            self.assertEqual(L.undo(_context(self.store, "doc undo"))["change"], "applied")

    def test_a_first_plane_that_poured_nothing_clears_nothing(self):
        L.remember_write(self.store, SESSION, BOARD, "via create", [])
        L.undo_cleared(self.store, SESSION, BOARD)
        L.remember_write(self.store, SESSION, BOARD, "plane create", [], changed=False)  # it made no plane
        L.remember_write(self.store, SESSION, BOARD, "trace create", [])
        with world(self.app):
            self.assertEqual(L.undo(_context(self.store, "doc undo"))["change"], "applied")  # the trace create
            self.assertEqual(L.undo(_context(self.store, "doc undo"))["change"], "applied")  # and the via create before it
        self.assertEqual(self.app.Gui.commands, [L.ID_EDIT_UNDO, L.ID_EDIT_UNDO])

    def test_undo_is_held_to_the_write_the_runtime_names(self):
        L.remember_write(self.store, SESSION, BOARD, "via create", [], operation_id="op_a")
        L.remember_write(self.store, SESSION, BOARD, "component move", [], changed=False, operation_id="op_b")  # no step of Layout's
        ctx = base.Context(self.store, {"command": "doc undo", "inputs": {}, "session": SESSION, "document": BOARD,
                                        "undoes": {"operation_id": "op_x", "command": "trace create"}})
        with world(self.app), self.assertRaises(AdapterError) as other:
            L.undo(ctx)  # Layout's last step is another write's
        self.assertEqual(other.exception.data["code"], "E_CONFLICT")
        self.assertEqual(self.app.Gui.commands, [])
        ctx.request["undoes"] = {"operation_id": "op_a", "command": "via create"}
        with world(self.app):
            self.assertEqual(L.undo(ctx)["change"], "applied")
        self.assertEqual(self.app.Gui.commands, [L.ID_EDIT_UNDO])

    def test_undo_refuses_a_write_that_left_no_undo_step(self):
        L.remember_write(self.store, SESSION, BOARD, "constraint import", [])
        with world(self.app), self.assertRaises(AdapterError) as refused:
            L.undo(_context(self.store, "doc undo"))
        self.assertEqual(refused.exception.data["code"], "E_UNSUPPORTED")
        self.assertIn("constraint import", str(refused.exception))
        self.assertEqual(self.app.Gui.commands, [])  # nothing sent

    def test_opening_the_board_again_forgets_the_writes(self):
        L.remember_write(self.store, SESSION, BOARD, "via create", [])
        sessions.forget(self.store, SESSION, BOARD)
        with world(self.app), self.assertRaises(AdapterError):
            L.undo(_context(self.store, "doc undo"))


if __name__ == "__main__":
    unittest.main()
