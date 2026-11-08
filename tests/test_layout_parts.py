"""Components on the board: component list and component get on a board, and component move."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

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


if __name__ == "__main__":
    unittest.main()
