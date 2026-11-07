"""trace delete, via delete and plane delete (adapter/layout_edit.py) on fake COM objects."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from test_layout_fakes import BOARD, FakeApp, FakeDoc, context, world  # noqa: E402

from xpedition_cli._driver import AdapterError  # noqa: E402
from xpedition_cli.adapter import base  # noqa: E402
from xpedition_cli.adapter import layout_edit as E  # noqa: E402
from xpedition_cli.adapter.plan import stitching as ST  # noqa: E402


def key(domain: str) -> str:
    return base.key(BOARD, domain)


class Item(SimpleNamespace):
    def __init__(self, owner: list, **fields) -> None:
        super().__init__(**fields)
        self.owner = owner

    def Delete(self) -> None:  # noqa: N802
        self.owner.remove(self)


def board() -> FakeDoc:
    doc = FakeDoc(nets=["A", "B", "GND"])
    for net, layer in (("A", 1), ("A", 1), ("A", 2), ("B", 1)):
        doc.traces.append(Item(doc.traces, Net=SimpleNamespace(Name=net), Layer=layer))
    for net in ("A", "GND", "GND"):
        doc.vias.append(Item(doc.vias, Net=SimpleNamespace(Name=net)))
    doc.shapes.append(Item(doc.shapes, Net=SimpleNamespace(Name="GND"), Layer=2))
    return doc


class TraceDeleteTest(unittest.TestCase):
    def test_preview_counts_apply_deletes_and_read_back_finds_none(self):
        doc = board()
        with world(FakeApp(doc)):
            out = E.TraceDelete().preview(context("trace delete", {"nets": "A"}))
            facts = E.TraceDelete().apply(context("trace delete", {"nets": "A"}))
            back = E.TraceDelete().read_back(context("trace delete", targets=[key("/traces/A")], fields=["count"]))
        self.assertEqual([(c["target"], c["before"], c["after"]) for c in out["changes"]], [(key("/traces/A"), 3, 0)])
        self.assertEqual((facts["change"], facts["result"]["deleted"]), ("applied", 3))
        self.assertEqual(back["observed"], {key("/traces/A"): {"count": 0}})
        self.assertEqual([t.Net.Name for t in doc.traces], ["B"])
        self.assertEqual(len(doc.vias), 3)  # vias stay

    def test_layers_limit_what_goes(self):
        doc = board()
        with world(FakeApp(doc)):
            out = E.TraceDelete().preview(context("trace delete", {"nets": "A,B", "layers": "1"}))
            E.TraceDelete().apply(context("trace delete", {"nets": "A,B", "layers": "1"}))
        self.assertEqual([(c["target"], c["before"]) for c in out["changes"]], [(key("/traces/A/1"), 2), (key("/traces/B/1"), 1)])
        self.assertEqual([(t.Net.Name, t.Layer) for t in doc.traces], [("A", 2)])

    def test_nothing_to_delete_and_unknown_names(self):
        with world(FakeApp(board())):
            self.assertEqual(E.TraceDelete().preview(context("trace delete", {"nets": "GND"}))["changes"], [])
            for inputs, wanted in (({"nets": "NOPE"}, "E_NOT_FOUND"), ({"nets": "A", "layers": "9"}, "E_VALIDATION"),
                                   ({"nets": " , "}, "E_VALIDATION")):
                with self.subTest(inputs=inputs), self.assertRaises(AdapterError) as caught:
                    E.TraceDelete().preview(context("trace delete", inputs))
                self.assertEqual(caught.exception.data["code"], wanted)


class ViaDeleteTest(unittest.TestCase):
    def test_the_vias_of_a_net_go(self):
        doc = board()
        with world(FakeApp(doc)):
            out = E.ViaDelete().preview(context("via delete", {"nets": "GND"}))
            facts = E.ViaDelete().apply(context("via delete", {"nets": "GND"}))
            back = E.ViaDelete().read_back(context("via delete", targets=[key("/vias/GND")], fields=["count"]))
        self.assertEqual([(c["target"], c["before"], c["after"]) for c in out["changes"]], [(key("/vias/GND"), 2, 0)])
        self.assertEqual(facts["result"]["deleted"], 2)
        self.assertEqual(back["observed"], {key("/vias/GND"): {"count": 0}})
        self.assertEqual([v.Net.Name for v in doc.vias], ["A"])


class PlaneDeleteTest(unittest.TestCase):
    def test_a_plane_shape_goes_and_a_missing_one_is_not_found(self):
        doc = board()
        with world(FakeApp(doc)):
            out = E.PlaneDelete().preview(context("plane delete", {"net": "GND", "layer": 2}))
            facts = E.PlaneDelete().apply(context("plane delete", {"net": "GND", "layer": 2}))
            with self.assertRaises(AdapterError) as missing:
                E.PlaneDelete().preview(context("plane delete", {"net": "GND", "layer": 2}))
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in out["changes"]], [("exists", True, False)])
        self.assertEqual((facts["change"], facts["result"]["deleted"]), ("applied", 1))
        self.assertEqual(doc.shapes, [])
        self.assertEqual(missing.exception.data["code"], "E_NOT_FOUND")


class ViaCreateTest(unittest.TestCase):
    """The plan and the placing; the board's geometry, the library row and the clearances are given."""

    def setUp(self):
        self.doc = board()
        self.doc.placed_vias = []

        def put_via(x, y, stack, net, component, fixed, unit):
            self.doc.placed_vias.append((x, y, stack.Name, net.Name))
            self.doc.vias.append(Item(self.doc.vias, Net=SimpleNamespace(Name=net.Name), Name=stack.Name))

        self.doc.PutVia = put_via
        self.doc.TransactionStart = lambda mode: self.doc.calls.append(("TransactionStart", mode))
        self.doc.TransactionEnd = lambda keep: self.doc.calls.append(("TransactionEnd", keep))
        self.doc.PutPadstack = lambda first, last, name, mirrored, central: SimpleNamespace(Name=name)
        outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
        geometry = ST.Board(outline=outline, planes={2: [outline], 3: [outline]}, pads=[ST.polygon(circle=[5.0, 2.5, 0.3])])
        self.patches = [mock.patch.object(E, "_stitch_board", lambda doc, net: geometry),
                        mock.patch.object(E, "_via_row", lambda path, name: {"type": "via", "pad_mm": 0.4, "drill_mm": 0.2}),
                        mock.patch.object(E, "_clearances", lambda path: ST.Clearances(0.15, 0.15, 0.2)),
                        mock.patch.object(E, "_class_via", lambda doc, net: "026VIA")]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_grid_is_planned_placed_with_drc_off_and_read_back(self):
        inputs = {"net": "GND", "pitch_mm": 1.0}
        with world(FakeApp(self.doc)):
            preview = E.ViaCreate().preview(context("via create", inputs))
            facts = E.ViaCreate().run(context("via create", inputs), None)
            back = E.ViaCreate().read_back(context("via create", targets=[key("/vias/GND")], fields=["count"]))
        (change,) = preview["changes"]
        self.assertEqual((change["target"], change["before"]), (key("/vias/GND"), 2))
        placed = facts["result"]["placed"]
        self.assertEqual(change["after"], 2 + placed)
        self.assertGreater(placed, 0)
        self.assertEqual(back["observed"], {key("/vias/GND"): {"count": 2 + placed}})
        self.assertIn(("TransactionStart", 0), self.doc.calls)  # online DRC off: the plan kept the clearances
        self.assertTrue(all(name == "026VIA" for _x, _y, name, _net in self.doc.placed_vias))  # the class's via by default
        self.assertNotIn((5.0, 2.5), [(x, y) for x, y, _n, _net in self.doc.placed_vias])  # never on the pad

    def test_one_via_and_one_that_cannot_stand(self):
        with world(FakeApp(self.doc)):
            facts = E.ViaCreate().run(context("via create", {"net": "GND", "x_mm": 2.0, "y_mm": 2.0, "padstack": "VIA_D0.2_P0.4"}), None)
            blocked = E.ViaCreate().preview(context("via create", {"net": "GND", "x_mm": 5.0, "y_mm": 2.6}))
        self.assertEqual(self.doc.placed_vias, [(2.0, 2.0, "VIA_D0.2_P0.4", "GND")])
        self.assertEqual(facts["result"]["placed"], 1)
        self.assertFalse(blocked["can_apply"])
        self.assertIn("too close to a pad", blocked["blocked_by"][0])

    def test_inputs_must_name_one_way_to_place(self):
        with world(FakeApp(self.doc)):
            for inputs, wanted in (({"net": "GND"}, "E_VALIDATION"), ({"net": "GND", "x_mm": 1.0}, "E_VALIDATION"),
                                   ({"net": "GND", "x_mm": 1.0, "y_mm": 1.0, "pitch_mm": 1.0}, "E_VALIDATION"),
                                   ({"net": "NOPE", "pitch_mm": 1.0}, "E_NOT_FOUND")):
                with self.subTest(inputs=inputs), self.assertRaises(AdapterError) as caught:
                    E.ViaCreate().preview(context("via create", inputs))
                self.assertEqual(caught.exception.data["code"], wanted)

    def test_a_net_with_copper_on_one_layer_cannot_be_stitched(self):
        outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
        with world(FakeApp(self.doc)), mock.patch.object(E, "_stitch_board", lambda doc, net: ST.Board(outline=outline, planes={2: [outline]})):
            with self.assertRaises(AdapterError) as caught:
                E.ViaCreate().preview(context("via create", {"net": "GND", "pitch_mm": 1.0}))
        self.assertEqual(caught.exception.data.get("conflict"), "application_state")


if __name__ == "__main__":
    unittest.main()
