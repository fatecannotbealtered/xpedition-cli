"""DS-17: a planned sheet as geometry, and the texts it overlaps."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from xpedition_cli.adapter.plan import schematic_layout as L
from xpedition_cli.adapter.plan import schematic_render as R
from xpedition_cli.adapter.plan import symbols as S

EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "demo-sensor-board.json"


def _design() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _block(design: dict, refdes: str) -> dict:
    for sheet in design["sheets"]:
        for block in sheet["blocks"]:
            if refdes in json.dumps(block):
                return block
    raise KeyError(refdes)


def _texts(symbol: S.Symbol) -> list[R.Text]:
    art = R.parse_symbol(symbol.render())
    return [
        R.Text(x, y, value, size, anchor, "symbol", symbol.name)
        for x, y, size, anchor, _name, value, visible in art.texts
        if visible and value
    ]


class SchematicRenderTest(unittest.TestCase):
    def test_a_power_symbol_writes_its_net_above_its_bar(self):
        # anchored by its upper centre the name hung through the bar, in Designer too
        art = R.parse_symbol(S.power_symbol("+3V3").render())
        [netname] = [t for t in art.texts if t[4] == "NETNAME"]
        x, y, size, anchor = netname[0], netname[1], netname[2], netname[3]
        bar = max(point[1] for line in art.lines for point in line)
        box = R.Text(x, y, "+3V3", size, anchor, "symbol", "power").box()
        self.assertEqual(anchor, 6)
        self.assertGreater(box[1], bar)

    def test_a_box_keeps_its_side_rows_clear_of_top_and_bottom_pin_names(self):
        # VDD on the top edge was written over the first left pin's name
        for rows in (2, 3, 4):
            left = [(str(i), f"IN{i}") for i in range(1, rows + 1)]
            right = [(str(i + 10), f"OUT{i}") for i in range(1, rows + 1)]
            symbol = S.box("CHIP", left, right, top=[("20", "VDD")], bottom=[("21", "GND")])
            boxes = [text.box() for text in _texts(symbol)]
            for i, a in enumerate(boxes):
                for b in boxes[i + 1 :]:
                    self.assertEqual(R._area(a, b), 0, msg=(rows, a, b))

    def test_texts_that_collide_are_reported(self):
        layout = R.SheetLayout(1, 1169, 827, [60, 150, 1110, 700])
        layout.texts = [
            R.Text(100, 300, "ABC", 8, 3, "text", "first"),
            R.Text(104, 302, "DEF", 8, 3, "text", "second"),
            R.Text(300, 300, "CLEAR", 8, 3, "text", "third"),
        ]
        layout.lines = [R.Line([(90, 304), (130, 304)], "wire", "wire U1.1")]
        layout.boxes = [
            R.Box((96, 296, 140, 312), "label", "label NET"),
            R.Box((290, 290, 400, 350), "frame", "frame"),  # CLEAR sits inside: fine
            R.Box((280, 302, 400, 400), "frame", "frame"),  # this edge runs through CLEAR
        ]
        messages = {issue["message"] for issue in R.text_overlaps(layout)}
        self.assertIn("first: text 'ABC' overlaps text of second 'DEF'", messages)
        self.assertIn("first: text 'ABC' is crossed by wire U1.1 (wire)", messages)
        self.assertIn("first: text 'ABC' overlaps the box of label NET", messages)
        self.assertIn("third: text 'CLEAR' is crossed by a frame's edge", messages)
        self.assertTrue(all(
            issue["check"] == "DS-17" and issue["sheet"] == 1 for issue in R.text_overlaps(layout)
        ))
        # a label's own text sits in its own box
        layout.texts = [R.Text(100, 300, "NET", 8, 3, "label", "label NET")]
        layout.lines = []
        self.assertEqual(R.text_overlaps(layout), [])

    def test_the_example_plans_clean_and_its_old_layout_did_not(self):
        self.assertEqual(L.plan(_design()).issues, [])
        design = _design()
        # the pull-up where it was: its label ran into the MCU's label of the same net
        _block(design, "R201")["x"] = 240
        issues = L.plan(design).issues
        self.assertEqual([(i["check"], i["object"], i["text"]) for i in issues], [
            ("DS-17", "label MCU_RST_N", "MCU_RST_N")
        ])

    def test_a_label_past_the_drawable_area_is_reported(self):
        design = _design()
        _block(design, "U202")["x"] = 960  # where it was: its alert label ran off the sheet
        issues = [i for i in L.plan(design).issues if i["check"] == "DS-07"]
        self.assertEqual([(i["object"], i["text"]) for i in issues], [
            ("label SENSOR_ALERT_N", "SENSOR_ALERT_N")
        ])

    def test_a_part_s_refdes_moves_off_its_own_power_symbol(self):
        plan = L.plan(_design())
        [j201] = [op for op in plan.ops if op["op"] == "place_part" and op["refdes"] == "J201"]
        attributes = {a["name"]: a for a in j201["attributes"]}
        # the first choice, above the header's upper-left, is where the +3V3 symbol of its
        # first pin stands; the refdes went to the next free spot, horizontally
        self.assertNotEqual(attributes["Ref Designator"]["origin"], 8)
        self.assertEqual(attributes["Ref Designator"]["orientation"], 0)
        self.assertEqual(attributes["Part Number"]["orientation"], 0)
        self.assertFalse([i for i in plan.issues if i.get("object") == "J201"])
