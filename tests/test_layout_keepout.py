"""keepout create and delete, and pcb update --outline, on fake COM objects."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

from test_layout_fakes import BOARD, Collection, Extrema, FakeApp, FakeComponent, FakeDoc, context, world  # noqa: E402

from xpedition_cli._driver import AdapterError  # noqa: E402
from xpedition_cli.adapter import base  # noqa: E402
from xpedition_cli.adapter import layout as L  # noqa: E402
from xpedition_cli.adapter import layout_board as B  # noqa: E402
from xpedition_cli.adapter import layout_keepout as K  # noqa: E402


def key(domain: str) -> str:
    return base.key(BOARD, domain)


class KeepoutTest(unittest.TestCase):
    def setUp(self):
        self.doc = FakeDoc(layers=4)
        self.keepouts: list = []
        self.doc.props = {K.PLACEMENT_OBSTRUCTS_DISPID: lambda *args: Collection(self.keepouts)}
        scale = L.geometry_scale(self.doc)

        def put(layer, width, count, points, height, component, unit):
            xs, ys = points[0], points[1]
            item = SimpleNamespace(Component=None, Side=1 if layer == 1 else 512, Height=height / scale,
                                   Extrema=Extrema((min(xs), min(ys), max(xs), max(ys))))
            item.Delete = lambda item=item: self.keepouts.remove(item)
            self.keepouts.append(item)
            return item

        self.doc.PutPlacementObstruct = put

    def test_a_stiffener_is_drawn_read_back_and_deleted(self):
        area = {"x1_mm": 0.0, "y1_mm": 0.0, "x2_mm": 9.0, "y2_mm": 18.0, "height_mm": 0.15, "side": "bottom"}
        with world(FakeApp(self.doc)):
            preview = K.KeepoutCreate().preview(context("keepout create", area))
            facts = K.KeepoutCreate().apply(context("keepout create", area))
            target = preview["changes"][0]["target"]
            back = K.KeepoutCreate().read_back(context("keepout create", targets=[target], fields=["exists", "height_mm"]))
            again = K.KeepoutCreate().preview(context("keepout create", area))
            gone = K.KeepoutDelete().apply(context("keepout delete", {"x_mm": 4.0, "y_mm": 9.0, "side": "bottom"}))
            after = K.KeepoutCreate().read_back(context("keepout create", targets=[target], fields=["exists"]))
        self.assertEqual(target, key("/keepouts/placement/bottom/0,0,9,18"))
        self.assertIn("no part taller than 0.15 mm, as a stiffener's area", preview["action"])
        self.assertEqual((facts["result"]["created"], facts["result"]["height_mm"]), (True, 0.15))
        self.assertEqual(back["observed"][target], {"exists": True, "height_mm": 0.15})
        self.assertEqual(again["changes"], [])
        self.assertEqual(gone["result"], {"side": "bottom", "rect_mm": [0.0, 0.0, 9.0, 18.0]})
        self.assertEqual(after["observed"][target], {"exists": False})

    def test_a_wrong_height_or_a_point_in_no_keepout_is_refused(self):
        with world(FakeApp(self.doc)):
            with self.assertRaises(AdapterError) as height:
                K.KeepoutCreate().preview(context("keepout create", {"x1_mm": 0.0, "y1_mm": 0.0, "x2_mm": 9.0, "y2_mm": 18.0,
                                                                    "height_mm": 0.0}))
            with self.assertRaises(AdapterError) as missing:
                K.KeepoutDelete().preview(context("keepout delete", {"x_mm": 4.0, "y_mm": 9.0}))
        self.assertEqual((height.exception.data["code"], missing.exception.data["code"]), ("E_VALIDATION", "E_NOT_FOUND"))


class PolygonOutlineTest(unittest.TestCase):
    EARS = {"points_mm": [[0, 0, 1], [70, 0, 1], [70, 40, 1], [34, 40], [34, 42, 0.5], [30, 42, 0.5], [30, 40], [0, 40, 1]]}

    def test_a_polygon_outline_is_written_and_read_back(self):
        doc = FakeDoc()
        with world(FakeApp(doc)):
            preview = B.OutlineUpdate().preview(context("pcb update", {"outline": self.EARS}))
            facts = B.OutlineUpdate().apply(context("pcb update", {"outline": self.EARS}))
        self.assertEqual([call[0] for call in doc.calls], ["PutBoardOutline", "PutRouteBorder", "PutManufacturingOutline"])
        self.assertIn("the polygon of 8 corners within 0,0 to 70,42 mm", preview["action"])
        self.assertEqual([(c["field"], c["after"]) for c in preview["changes"]],
                         [("width_mm", 70.0), ("height_mm", 42.0), ("extents_mm", [0.0, 0.0, 70.0, 42.0])])
        self.assertEqual(facts["result"]["corners"], 8)

    def test_both_shapes_at_once_or_a_part_outside_are_refused(self):
        parts = [FakeComponent("R1", 3.0, 1.5, placed=True, x=50.0, y=41.0)]
        with world(FakeApp(FakeDoc(width=70.0, height=48.0, components=parts))):
            with self.assertRaises(AdapterError) as both:
                B.OutlineUpdate().preview(context("pcb update", {"outline": self.EARS, "width_mm": 70.0}))
            with self.assertRaises(AdapterError) as outside:
                B.OutlineUpdate().preview(context("pcb update", {"outline": self.EARS}))
        self.assertEqual(both.exception.data["code"], "E_VALIDATION")
        self.assertIn("R1", str(outside.exception))


if __name__ == "__main__":
    unittest.main()
