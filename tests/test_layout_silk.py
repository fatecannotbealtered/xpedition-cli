"""silkscreen-text create and delete on fake COM objects; the board model and its bends are given."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from test_layout_fakes import BOARD, Collection, FakeApp, FakeDoc, context, world  # noqa: E402

from xpedition_cli._driver import AdapterError  # noqa: E402
from xpedition_cli.adapter import base  # noqa: E402
from xpedition_cli.adapter import layout_silk as T  # noqa: E402


class FakeText(SimpleNamespace):
    def __init__(self, owner: list, words: str, x: float, y: float, side: int, rotation: float, height: float) -> None:
        super().__init__(Type=T.FAB_SILKSCREEN, Component=None, TextString=words, Side=side, FixLock=0,
                         Format=SimpleNamespace(Orientation=rotation, Height=height, PenWidth=0.15))
        self.owner, self.x, self.y = owner, x, y

    def GetPositionX(self, unit):  # noqa: N802
        return self.x

    def GetPositionY(self, unit):  # noqa: N802
        return self.y

    def Delete(self) -> None:  # noqa: N802
        self.owner.remove(self)


def model(pads=(), texts=()):
    outline = {"path": [[0, 0, 0], [50, 0, 0], [50, 40, 0], [0, 40, 0], [0, 0, 0]], "kind": "outline"}
    return {"layers": 4, "outline": [outline], "components": [], "pads": list(pads), "vias": [], "traces": [], "holes": [],
            "silk": [], "texts": list(texts), "cutouts": []}


class SilkscreenTextTest(unittest.TestCase):
    def setUp(self):
        self.doc = FakeDoc()
        self.model = model()
        self.bends: list = []

        def put_text(words, x, y, fab, side, height, rotation, pen, font, attr, horiz, vert, component, unit, angle_unit):
            text = FakeText(self.doc.texts, words, x, y, side, rotation, height)
            self.doc.texts.append(text)
            return text

        self.doc.PutFabricationLayerText = put_text
        for patch in (mock.patch.object(T.O, "board_model", lambda doc, full=True, **kw: self.model),
                      mock.patch.object(T.O, "bend_boxes", lambda doc, flex=None: self.bends),
                      mock.patch.object(T.L, "extrema_mm", lambda item: (item.x - 3, item.y - 0.5, item.x + 3, item.y + 0.5))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_text_is_placed_read_back_found_again_and_deleted(self):
        words = {"text": "BOARD_A", "x_mm": 20.0, "y_mm": 10.0}
        with world(FakeApp(self.doc)):
            preview = T.SilkscreenTextCreate().preview(context("silkscreen-text create", words))
            facts = T.SilkscreenTextCreate().apply(context("silkscreen-text create", words))
            target = preview["changes"][0]["target"]
            back = T.SilkscreenTextCreate().read_back(context("silkscreen-text create", targets=[target], fields=["exists", "x_mm"]))
            again = T.SilkscreenTextCreate().preview(context("silkscreen-text create", words))
            gone = T.SilkscreenTextDelete().apply(context("silkscreen-text delete", {"text": "BOARD_A"}))
            after = T.SilkscreenTextCreate().read_back(context("silkscreen-text create", targets=[target], fields=["exists"]))
        self.assertEqual(target, base.key(BOARD, "/silkscreen-text/top/BOARD_A"))
        self.assertIn("covering about 16.43,8.97 to 23.57,10.82 mm", preview["action"])
        self.assertEqual((facts["result"]["created"], facts["result"]["x_mm"]), (True, 20.0))
        self.assertEqual(back["observed"][target], {"exists": True, "x_mm": 20.0})
        self.assertEqual(again["changes"], [])
        self.assertEqual(gone["result"]["text"], "BOARD_A")
        self.assertEqual(after["observed"][target], {"exists": False})

    def test_a_text_that_would_not_print_whole_is_refused(self):
        self.model = model(pads=[{"layer": 1, "net": "GND", "refdes": "J1", "pin": "1", "path": [[19, 9.5, 0], [21, 9.5, 0], [21, 10.5, 0],
                                                                                               [19, 10.5, 0], [19, 9.5, 0]]}])
        self.bends = [(0.0, 30.0, 50.0, 33.0)]
        with world(FakeApp(self.doc)):
            pad = T.SilkscreenTextCreate().preview(context("silkscreen-text create", {"text": "BOARD_A", "x_mm": 20.0, "y_mm": 10.0}))
            edge = T.SilkscreenTextCreate().preview(context("silkscreen-text create", {"text": "BOARD_A", "x_mm": 48.0, "y_mm": 10.0}))
            bend = T.SilkscreenTextCreate().preview(context("silkscreen-text create", {"text": "BOARD_A", "x_mm": 30.0, "y_mm": 31.0,
                                                                                      "rotation_deg": 90.0}))
        self.assertIn("runs over J1.1, where the board house clips", pad["blocked_by"][0])
        self.assertIn("reaches past the board outline", edge["blocked_by"][0])
        self.assertTrue(any("bend zone" in reason for reason in bend["blocked_by"]))

    def test_the_same_words_elsewhere_or_a_missing_text_are_named(self):
        self.doc.texts.append(FakeText(self.doc.texts, "BOARD_A", 5.0, 5.0, 1, 0.0, 1.0))
        with world(FakeApp(self.doc)):
            moved = T.SilkscreenTextCreate().preview(context("silkscreen-text create", {"text": "BOARD_A", "x_mm": 20.0, "y_mm": 10.0}))
            with self.assertRaises(AdapterError) as missing:
                T.SilkscreenTextDelete().preview(context("silkscreen-text delete", {"text": "BOARD_B"}))
            with self.assertRaises(AdapterError) as wrong:
                T.SilkscreenTextCreate().preview(context("silkscreen-text create", {"text": "", "x_mm": 1.0, "y_mm": 1.0}))
        self.assertIn("holds 'BOARD_A' at (5, 5) mm already", moved["blocked_by"][0])
        self.assertEqual(missing.exception.data["code"], "E_NOT_FOUND")
        self.assertIn("it holds 'BOARD_A'", str(missing.exception))
        self.assertEqual(wrong.exception.data["code"], "E_VALIDATION")

    def test_the_same_words_on_the_other_side_are_another_text(self):
        self.model = model(texts=[{"text": "BOARD_A", "kind": "text", "side": "bottom", "refdes": None, "height": 1.0, "pen": 0.15,
                                   "extents": [16.0, 9.0, 24.0, 11.0]}])
        self.model["components"] = [{"refdes": "U3", "placed": True, "side": "bottom", "extents": [15.0, 8.0, 25.0, 12.0]}]
        with world(FakeApp(self.doc)):
            top = T.SilkscreenTextCreate().preview(context("silkscreen-text create", {"text": "BOARD_A", "x_mm": 20.0, "y_mm": 25.0}))
        self.assertTrue(top["can_apply"], top.get("blocked_by"))

    def test_a_slanted_or_empty_size_is_refused(self):
        with world(FakeApp(self.doc)):
            for inputs in ({"rotation_deg": 45.0}, {"height_mm": 0.0}, {"pen_width_mm": 0.0}):
                with self.assertRaises(AdapterError) as caught:
                    T.SilkscreenTextCreate().preview(context("silkscreen-text create", {"text": "A", "x_mm": 20.0, "y_mm": 10.0, **inputs}))
                self.assertEqual(caught.exception.data["code"], "E_VALIDATION")

    def test_a_text_goes_on_a_user_draft_layer_by_name(self):
        layer = SimpleNamespace(Name="silk")
        self.doc.user_texts = []
        self.doc.UserLayers = Collection([SimpleNamespace(Name="Default User Layer"), layer])
        type(self.doc).UserLayerTexts = property(lambda doc: Collection(doc.user_texts))
        self.addCleanup(lambda: delattr(type(self.doc), "UserLayerTexts"))

        def put_user_text(words, x, y, user_layer, height, rotation, pen, font, attr, horiz, vert, component, unit, angle_unit):
            text = FakeText(self.doc.user_texts, words, x, y, 1, rotation, height)
            text.UserLayer = user_layer
            self.doc.user_texts.append(text)
            return text

        self.doc.PutUserLayerText = put_user_text
        words = {"text": "BOARD_A", "x_mm": 20.0, "y_mm": 10.0, "user_layer": "silk"}
        with world(FakeApp(self.doc)):
            preview = T.SilkscreenTextCreate().preview(context("silkscreen-text create", words))
            facts = T.SilkscreenTextCreate().apply(context("silkscreen-text create", words))
            target = preview["changes"][0]["target"]
            back = T.SilkscreenTextCreate().read_back(context("silkscreen-text create", targets=[target], fields=["exists"]))
            listed = T.listed(self.doc)
            with self.assertRaises(AdapterError) as missing:
                T.SilkscreenTextCreate().preview(context("silkscreen-text create", {**words, "user_layer": "Silkscreen"}))
            gone = T.SilkscreenTextDelete().apply(context("silkscreen-text delete", {"text": "BOARD_A", "user_layer": "silk"}))
        self.assertEqual(target, base.key(BOARD, "/silkscreen-text/user:silk/BOARD_A"))
        self.assertIn("on the user layer silk, printed on the top", preview["action"])
        self.assertEqual((facts["result"]["user_layer"], self.doc.texts), ("silk", []))
        self.assertEqual(back["observed"][target], {"exists": True})
        self.assertEqual(listed[0]["user_layer"], "silk")
        self.assertEqual(missing.exception.data["code"], "E_NOT_FOUND")
        self.assertIn("its user layers are: Default User Layer, silk", str(missing.exception))
        self.assertEqual((gone["result"]["user_layer"], self.doc.user_texts), ("silk", []))

    def test_the_box_turns_with_the_text(self):
        flat = T.corners(10.0, 10.0, "ABCD", 1.0, 0.15, 0.0)
        upright = T.corners(10.0, 10.0, "ABCD", 1.0, 0.15, 90.0)
        self.assertAlmostEqual(flat[1][0] - flat[0][0], 4 * T.CHAR_WIDTH + 0.15, places=3)
        self.assertAlmostEqual(upright[1][1] - upright[0][1], 4 * T.CHAR_WIDTH + 0.15, places=3)
        self.assertAlmostEqual(min(p[0] for p in upright), 10.0 - 0.75 - 0.075, places=3)  # the top of the letters to the left
        self.assertAlmostEqual(max(p[0] for p in upright), 10.0 + 0.95 + 0.075, places=3)


if __name__ == "__main__":
    unittest.main()
