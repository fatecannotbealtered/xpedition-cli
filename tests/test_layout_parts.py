"""Components on the board: component list and component get on a board, and component move."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_layout_fakes import BOARD, FakeApp, FakeComError, FakeComponent, FakeDoc, context, world  # noqa: E402

from xpedition_cli._driver import AdapterError  # noqa: E402
from xpedition_cli.adapter import base, designer, layout_parts  # noqa: E402


def refused() -> FakeComError:
    """Layout's placement check refusing a spot: Xpedition's number 10203 in the EXCEPINFO."""
    return FakeComError(-2147352567, "Exception occurred.", (0, "Xpedition Layout", "DRC violation", None, 10203, -2147220989))


class Part(FakeComponent):
    """A component that also moves, turns, flips sides and may be anchored."""

    def __init__(self, *args: Any, anchor: int = 0, side: int = 1, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.Anchor = anchor
        self.Side = side
        self.refuse: Exception | None = None

    def Move(self, x: float, y: float, unit: int) -> None:  # noqa: N802 - COM name
        self.calls.append(("Move", x, y))
        if self.refuse:
            raise self.refuse
        self.x, self.y = x, y

    def Place(self, x: float, y: float, rotation: float, top: bool, a: int, unit: int, b: int) -> None:  # noqa: N802
        self.calls.append(("Place", x, y, rotation, top))
        if self.refuse:
            raise self.refuse
        self.Placed, self.x, self.y, self.Orientation, self.Side = True, x, y, rotation, 1 if top else 512


class Control:
    """The editor control: records the parts' online DRC mode as the move sets and restores it."""

    def __init__(self) -> None:
        self.modes: list[int] = []
        self._mode = 2

    @property
    def PartsOnlineDRC(self) -> int:  # noqa: N802 - COM name
        return self._mode

    @PartsOnlineDRC.setter
    def PartsOnlineDRC(self, value: int) -> None:  # noqa: N802 - COM name
        self._mode = value
        self.modes.append(value)


class BoardCase(unittest.TestCase):
    def setUp(self) -> None:
        pins = {"1": (-0.8, 0.0, "5V"), "2": (0.8, 0.0, "LED_A")}
        self.r1 = Part("R1", pins=pins, placed=True, x=10.0, y=10.0, cell="RESC1608X50N", part_number="RES-1K")
        self.r10 = Part("R10", placed=True, x=20.0, y=10.0, rotation=90.0, side=512)
        self.u1 = Part("U1", placed=False)
        self.doc = FakeDoc(components=[self.r10, self.u1, self.r1])
        self.doc.EditorControl = Control()
        self.app = FakeApp(self.doc)
        patcher = world(self.app)
        patcher.__enter__()
        self.addCleanup(patcher.__exit__, None, None, None)

    def read(self, command: str, **inputs: Any) -> dict[str, Any]:
        handler = next(h for h in designer.HANDLERS if h.command == command)
        return handler.read(context(command, inputs))

    def move(self) -> Any:
        return next(h for h in layout_parts.HANDLERS if h.command == "component move")


class ReadTest(BoardCase):
    def test_component_list_on_a_board(self):
        items = self.read("component list")["items"]
        self.assertEqual([i["refdes"] for i in items], ["R1", "R10", "U1"])
        self.assertEqual(items[0], {"refdes": "R1", "part_number": "RES-1K", "cell": "RESC1608X50N", "placed": True, "x_mm": 10.0,
                                    "y_mm": 10.0, "rotation_deg": 0.0, "side": "top", "anchor": "none"})
        self.assertEqual((items[1]["side"], items[1]["rotation_deg"]), ("bottom", 90.0))
        self.assertEqual((items[2]["placed"], items[2]["x_mm"]), (False, None))

    def test_a_sheet_is_refused_on_a_board(self):
        with self.assertRaises(AdapterError) as caught:
            self.read("component list", sheet=1)
        self.assertEqual(caught.exception.data["code"], "E_VALIDATION")

    def test_component_get_on_a_board_lists_its_pins_where_they_stand(self):
        found = self.read("component get", refdes="r1")
        self.assertEqual(found["refdes"], "R1")
        self.assertEqual(found["pins"], [{"number": "1", "net": "5V", "x_mm": 9.2, "y_mm": 10.0},
                                         {"number": "2", "net": "LED_A", "x_mm": 10.8, "y_mm": 10.0}])
        with self.assertRaises(AdapterError) as caught:
            self.read("component get", refdes="Q9")
        self.assertEqual(caught.exception.data["code"], "E_NOT_FOUND")


class MoveTest(BoardCase):
    def preview(self, **inputs: Any) -> dict[str, Any]:
        return self.move().preview(context("component move", inputs))

    def apply(self, **inputs: Any) -> dict[str, Any]:
        return self.move().apply(context("component move", inputs))

    def test_the_preview_lists_what_changes(self):
        out = self.preview(refdes="R1", x_mm=15.0, y_mm=10.0, rotation_deg=90.0)
        key = base.key(BOARD, "/components/R1")
        self.assertEqual({(c["field"], c["before"], c["after"]) for c in out["changes"]},
                         {("x_mm", 10.0, 15.0), ("rotation_deg", 0.0, 90.0)})
        self.assertEqual({c["target"] for c in out["changes"]}, {key})
        self.assertIn("no other component moves", out["action"])

    def test_a_move_where_it_stands_changes_nothing(self):
        out = self.preview(refdes="R1", x_mm=10.0, y_mm=10.0)
        self.assertEqual(out["changes"], [])
        facts = self.apply(refdes="R1", x_mm=10.0, y_mm=10.0)
        self.assertEqual(facts["change"], "not_applied")
        self.assertEqual(self.r1.calls, [])

    def test_a_move_on_its_side_moves_and_turns_without_shoving(self):
        facts = self.apply(refdes="R1", x_mm=15.0, y_mm=12.0, rotation_deg=450.0)
        self.assertEqual(self.r1.calls, [("Move", 15.0, 12.0)])
        self.assertEqual((self.r1.x, self.r1.y, self.r1.Orientation), (15.0, 12.0, 90.0))
        self.assertEqual(self.doc.EditorControl.modes, [layout_parts.NO_SHOVE, 2])
        self.assertEqual(facts["result"], {"refdes": "R1", "placed": True, "x_mm": 15.0, "y_mm": 12.0, "rotation_deg": 90.0,
                                           "side": "top", "inside_board": True})

    def test_a_side_change_or_an_unplaced_part_is_placed(self):
        self.apply(refdes="R1", side="bottom")
        self.assertEqual(self.r1.calls[-1], ("Place", 10.0, 10.0, 0.0, False))
        self.assertEqual(self.read("component get", refdes="R1")["side"], "bottom")
        facts = self.apply(refdes="U1", x_mm=60.0, y_mm=5.0)
        self.assertEqual(self.u1.calls[-1], ("Place", 60.0, 5.0, 0.0, True))
        self.assertFalse(facts["result"]["inside_board"])

    def test_an_unplaced_part_needs_a_position_and_a_move_needs_something(self):
        for inputs in ({"refdes": "U1", "rotation_deg": 90.0}, {"refdes": "R1"}, {"refdes": "R1", "side": "left"}):
            with self.assertRaises(AdapterError) as caught:
                self.preview(**inputs)
            self.assertEqual(caught.exception.data["code"], "E_VALIDATION")

    def test_an_anchored_part_is_blocked(self):
        self.r1.Anchor = 2
        out = self.preview(refdes="R1", x_mm=15.0)
        self.assertIn("fixed", out["blocked_by"][0])
        with self.assertRaises(AdapterError) as caught:
            self.apply(refdes="R1", x_mm=15.0)
        self.assertEqual(caught.exception.data["conflict"], "application_state")

    def test_a_spot_layout_refuses_is_a_conflict_and_the_mode_comes_back(self):
        self.r1.refuse = refused()
        with self.assertRaises(AdapterError) as caught:
            self.apply(refdes="R1", x_mm=20.0, y_mm=10.0)
        self.assertEqual((caught.exception.data["code"], caught.exception.data["knowledge"]), ("E_CONFLICT", ["layout-placement-drc"]))
        self.assertEqual(self.doc.EditorControl.PartsOnlineDRC, 2)
        self.r1.refuse = FakeComError(text="Catastrophic failure")
        with self.assertRaises(AdapterError) as caught:
            self.apply(refdes="R1", x_mm=20.0, y_mm=10.0)
        self.assertEqual(caught.exception.data["code"], "E_NATIVE_FAILURE")

    def test_the_read_back_reads_the_side(self):
        self.apply(refdes="R10", x_mm=25.0)
        ctx = context("component move", {"refdes": "R10"}, targets=[base.key(BOARD, "/components/R10")],
                      fields=["x_mm", "side"])
        observed = self.move().read_back(ctx)["observed"][base.key(BOARD, "/components/R10")]
        self.assertEqual(observed, {"x_mm": 25.0, "side": "bottom"})


class Hole:
    def __init__(self, doc: Any, x: float, y: float, padstack: str, radius: float = 1.6, component: Any = None) -> None:
        self.doc, self.x, self.y, self.radius, self.Component = doc, x, y, radius, component
        self.CurrentPadstack = type("Padstack", (), {"Name": padstack})()

    def GetPositionX(self, unit: int) -> float:  # noqa: N802 - COM name
        return self.x

    def GetPositionY(self, unit: int) -> float:  # noqa: N802 - COM name
        return self.y

    @property
    def Extrema(self) -> Any:  # noqa: N802 - COM name
        from test_layout_fakes import Extrema

        return Extrema((self.x - self.radius, self.y - self.radius, self.x + self.radius, self.y + self.radius))

    def Delete(self) -> None:  # noqa: N802 - COM name
        self.doc.hole_list.remove(self)


LIBRARY_HOLE = {"name": "MH3.2", "type": "mounting_hole", "drill_mm": 3.2, "plated": False}


class HoleTest(BoardCase):
    def setUp(self) -> None:
        super().setUp()
        self.doc.hole_list = []
        type(self.doc).MountingHoles = property(lambda doc: __import__("test_layout_fakes").Collection(doc.hole_list))
        self.doc.PutMountingHoleEx = self.put
        self.puts: list[tuple] = []
        self.row: Any = dict(LIBRARY_HOLE)
        patcher = mock.patch.object(layout_parts, "_library_hole", lambda path, name: self.lookup(name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: delattr(type(self.doc), "MountingHoles"))

    def lookup(self, name: str) -> Any:
        if isinstance(self.row, Exception):
            raise self.row
        return self.row

    def put(self, x: float, y: float, name: str, central: bool, depth: int, mirrored: bool, net: Any, component: Any,
            anchor: int, unit: int) -> None:
        self.puts.append((x, y, name, central))
        self.doc.hole_list.append(Hole(self.doc, x, y, name))

    def handler(self, command: str) -> Any:
        return next(h for h in layout_parts.HANDLERS if h.command == command)

    def test_four_corners_inside_the_outline_and_no_second_time(self):
        inputs = {"padstack": "MH3.2", "corners": True, "inset_mm": 3.0}
        preview = self.handler("mounting-hole create").preview(context("mounting-hole create", inputs))
        self.assertEqual(len(preview["changes"]), 4)
        self.assertIn("3.2 mm unplated hole", preview["action"])
        facts = self.handler("mounting-hole create").apply(context("mounting-hole create", inputs))
        self.assertEqual([(x, y) for x, y, _n, _c in self.puts], [(3.0, 3.0), (47.0, 3.0), (47.0, 37.0), (3.0, 37.0)])
        self.assertTrue(all(central for *_rest, central in self.puts))
        self.assertTrue(all(h["created"] and h["exists"] for h in facts["result"]["holes"]))
        again = self.handler("mounting-hole create").apply(context("mounting-hole create", inputs))
        self.assertEqual(again["change"], "not_applied")
        self.assertEqual(len(self.puts), 4)

    def test_a_spot_past_the_outline_or_a_wrong_call_is_refused(self):
        cases = [{"padstack": "MH3.2", "x_mm": 1.0, "y_mm": 20.0}, {"padstack": "MH3.2", "corners": True},
                 {"padstack": "MH3.2", "x_mm": 10.0}, {"padstack": "MH3.2", "corners": True, "inset_mm": 3.0, "x_mm": 5.0}]
        for inputs in cases:
            with self.assertRaises(AdapterError) as caught:
                self.handler("mounting-hole create").preview(context("mounting-hole create", inputs))
            self.assertEqual(caught.exception.data["code"], "E_VALIDATION", inputs)

    def test_another_padstack_on_the_spot_blocks(self):
        self.doc.hole_list.append(Hole(self.doc, 10.0, 10.0, "MH2.4"))
        out = self.handler("mounting-hole create").preview(context("mounting-hole create", {"padstack": "MH3.2", "x_mm": 10.0,
                                                                                         "y_mm": 10.0}))
        self.assertIn("MH2.4", out["blocked_by"][0])

    def test_without_a_readable_library_the_board_must_hold_the_padstack(self):
        self.row = None
        with self.assertRaises(AdapterError) as caught:
            self.handler("mounting-hole create").preview(context("mounting-hole create", {"padstack": "MH3.2", "x_mm": 10.0,
                                                                                          "y_mm": 10.0}))
        self.assertEqual(caught.exception.data["code"], "E_NOT_FOUND")

    def test_list_and_delete_by_point_or_all_leaving_a_parts_holes(self):
        self.doc.hole_list += [Hole(self.doc, 10.0, 10.0, "MH3.2"), Hole(self.doc, 30.0, 10.0, "MH3.2"),
                               Hole(self.doc, 20.0, 20.0, "MH1", component=self.r1)]
        listed = self.handler("mounting-hole list").read(context("mounting-hole list"))["items"]
        self.assertEqual([(r["x_mm"], r["y_mm"]) for r in listed], [(10.0, 10.0), (30.0, 10.0)])
        delete = self.handler("mounting-hole delete")
        preview = delete.preview(context("mounting-hole delete", {"x_mm": 10.5, "y_mm": 9.5}))
        self.assertEqual([c["target"] for c in preview["changes"]], [base.key(BOARD, "/mounting-holes/10,10")])
        facts = delete.apply(context("mounting-hole delete", {"x_mm": 10.5, "y_mm": 9.5}))
        self.assertEqual(facts["result"]["deleted"], [{"x_mm": 10.0, "y_mm": 10.0, "padstack": "MH3.2"}])
        with self.assertRaises(AdapterError) as caught:
            delete.preview(context("mounting-hole delete", {"x_mm": 0.5, "y_mm": 0.5}))
        self.assertEqual(caught.exception.data["code"], "E_NOT_FOUND")
        delete.apply(context("mounting-hole delete", {"all": True}))
        self.assertEqual([h.Component for h in self.doc.hole_list], [self.r1])

    def test_a_locked_hole_is_not_deleted(self):
        hole = Hole(self.doc, 10.0, 10.0, "MH3.2")
        hole.Anchor = 3
        self.doc.hole_list.append(hole)
        delete = self.handler("mounting-hole delete")
        self.assertIn("locked", delete.preview(context("mounting-hole delete", {"all": True}))["blocked_by"][0])
        with self.assertRaises(AdapterError):
            delete.apply(context("mounting-hole delete", {"x_mm": 10.0, "y_mm": 10.0}))
        self.assertEqual(self.doc.hole_list, [hole])

    def test_the_read_back_finds_the_hole(self):
        self.handler("mounting-hole create").apply(context("mounting-hole create", {"padstack": "MH3.2", "x_mm": 10.0,
                                                                                    "y_mm": 12.5}))
        key = base.key(BOARD, "/mounting-holes/10,12.5")
        ctx = context("mounting-hole create", {}, targets=[key], fields=["exists"])
        self.assertEqual(self.handler("mounting-hole create").read_back(ctx)["observed"][key], {"exists": True})


class Text:
    """A silkscreen reference designator: centred on its anchor, ``width`` x ``height`` unturned."""

    def __init__(self, x: float, y: float, width: float = 1.6, height: float = 0.8, rotation: float = 0.0, kind: int = 2) -> None:
        self.x, self.y, self.width, self.height = x, y, width, height
        self.Type, self.TextType, self.Side, self.FixLock = kind, 1, 1, 0
        self.Format = type("Format", (), {"Orientation": rotation})()
        self.moves: list[tuple] = []

    def GetPositionX(self, unit: int) -> float:  # noqa: N802 - COM name
        return self.x

    def GetPositionY(self, unit: int) -> float:  # noqa: N802 - COM name
        return self.y

    @property
    def Extrema(self) -> Any:  # noqa: N802 - COM name
        from test_layout_fakes import Extrema

        w, h = (self.height, self.width) if round(self.Format.Orientation) % 180 == 90 else (self.width, self.height)
        return Extrema((self.x - w / 2, self.y - h / 2, self.x + w / 2, self.y + h / 2))

    def Move(self, x: float, y: float, unit: int) -> None:  # noqa: N802 - COM name
        self.moves.append((x, y))
        self.x, self.y = x, y


class RefdesTest(BoardCase):
    def setUp(self) -> None:
        super().setUp()
        from test_layout_fakes import Collection

        self.text = Text(10.0, 11.0)
        self.r1.FabricationLayerTexts = Collection([Text(10.0, 10.0, kind=1), self.text])
        self.r10.FabricationLayerTexts = Collection()
        self.doc.hole_list = []
        type(self.doc).MountingHoles = property(lambda doc: Collection(doc.hole_list))
        self.addCleanup(lambda: delattr(type(self.doc), "MountingHoles"))

    def handler(self) -> Any:
        return next(h for h in layout_parts.HANDLERS if h.command == "refdes move")

    def test_a_spot_and_a_rotation(self):
        out = self.handler().preview(context("refdes move", {"refdes": "R1", "x_mm": 20.0, "y_mm": 5.0, "rotation_deg": 90.0}))
        self.assertEqual({(c["field"], c["after"]) for c in out["changes"]}, {("x_mm", 20.0), ("y_mm", 5.0), ("rotation_deg", 90.0)})
        self.assertEqual(out["changes"][0]["target"], base.key(BOARD, "/refdes/R1"))
        facts = self.handler().apply(context("refdes move", {"refdes": "R1", "x_mm": 20.0, "y_mm": 5.0, "rotation_deg": 90.0}))
        self.assertEqual((self.text.x, self.text.y, self.text.Format.Orientation), (20.0, 5.0, 90.0))
        self.assertEqual(facts["result"]["overlaps"], [])  # R10 lies on the bottom
        over = self.handler().apply(context("refdes move", {"refdes": "R1", "x_mm": 10.0, "y_mm": 10.0}))
        self.assertEqual(over["result"]["overlaps"], ["R1"])  # over its own pads
        self.assertTrue(facts["result"]["inside_board"])

    def test_auto_finds_the_first_free_spot_beside_the_part(self):
        blocker = Part("C9", placed=True, x=10.0, y=10.9)  # above R1, where the first try would go
        blocker.FabricationLayerTexts = __import__("test_layout_fakes").Collection()
        self.doc.components.append(blocker)
        facts = self.handler().apply(context("refdes move", {"refdes": "R1", "auto": True}))
        self.assertEqual((facts["result"]["x_mm"], facts["result"]["y_mm"]), (10.0, 9.0))  # below: 0.2 mm under its extents
        self.assertEqual(facts["result"]["overlaps"], [])

    def test_auto_without_a_free_spot_is_blocked(self):
        from test_layout_fakes import Collection

        for i, (x, y) in enumerate(((10.0, 10.9), (10.0, 9.1), (11.6, 10.0), (8.4, 10.0), (10.0, 11.8), (10.0, 8.2), (12.2, 10.0),
                                    (7.8, 10.0), (10.0, 12.6), (10.0, 7.4), (13.0, 10.0), (7.0, 10.0))):
            part = Part(f"X{i}", placed=True, x=x, y=y, width=3.0, height=0.8)
            part.FabricationLayerTexts = Collection()
            self.doc.components.append(part)
        with self.assertRaises(AdapterError) as caught:
            self.handler().preview(context("refdes move", {"refdes": "R1", "auto": True}))
        self.assertEqual(caught.exception.data["conflict"], "application_state")

    def test_wrong_calls_and_missing_texts(self):
        for inputs, code in (({"refdes": "R1"}, "E_VALIDATION"), ({"refdes": "R1", "x_mm": 3.0}, "E_VALIDATION"),
                             ({"refdes": "R1", "auto": True, "x_mm": 3.0, "y_mm": 3.0}, "E_VALIDATION"),
                             ({"refdes": "U1", "auto": True}, "E_VALIDATION"), ({"refdes": "R10", "auto": True}, "E_NOT_FOUND")):
            with self.assertRaises(AdapterError) as caught:
                self.handler().preview(context("refdes move", inputs))
            self.assertEqual(caught.exception.data["code"], code, inputs)

    def test_a_locked_text_is_blocked_and_the_read_back_reads_it(self):
        self.text.FixLock = 2
        out = self.handler().preview(context("refdes move", {"refdes": "R1", "x_mm": 20.0, "y_mm": 5.0}))
        self.assertIn("locked", out["blocked_by"][0])
        self.text.FixLock = 0
        key = base.key(BOARD, "/refdes/R1")
        ctx = context("refdes move", {}, targets=[key], fields=["x_mm", "rotation_deg"])
        self.assertEqual(self.handler().read_back(ctx)["observed"][key], {"x_mm": 10.0, "rotation_deg": 0.0})


if __name__ == "__main__":
    unittest.main()
