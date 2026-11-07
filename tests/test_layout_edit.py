"""trace delete, via delete and plane delete (adapter/layout_edit.py) on fake COM objects."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

from test_layout_fakes import BOARD, FakeApp, FakeDoc, context, world  # noqa: E402

from xpedition_cli._driver import AdapterError  # noqa: E402
from xpedition_cli.adapter import base  # noqa: E402
from xpedition_cli.adapter import layout_edit as E  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
