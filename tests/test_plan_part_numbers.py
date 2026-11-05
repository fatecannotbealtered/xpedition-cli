"""A value is the part number a drawn part carries, and a part number names one part."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from xpedition_cli.adapter.plan import library_hkp as H
from xpedition_cli.adapter.plan import schematic_layout as L

EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "demo-sensor-board.json"


def _demo() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _connectors(design: dict) -> list[dict]:
    return [
        block
        for sheet in design["sheets"]
        for block in sheet.get("blocks", [])
        if block.get("kind") == "connector"
    ]


class PartNumbersTest(unittest.TestCase):
    def test_one_value_drawn_with_two_symbols_is_refused_when_planned(self):
        design = _demo()
        first, second = _connectors(design)[:2]
        self.assertNotEqual(first["symbol"], second["symbol"])
        second["value"] = first["value"]
        with self.assertRaisesRegex(L.DesignError, "share the value"):
            L.plan(design)

    def test_one_value_on_two_cells_is_refused_when_built(self):
        design = _demo()
        resistors = [
            item
            for sheet in design["sheets"]
            for block in sheet.get("blocks", [])
            for item in (block.get("path") or [])[1::2]
            if isinstance(item, dict) and item.get("symbol") == "RES"
        ]
        first, second = resistors[:2]
        second["value"] = first["value"]
        H.plan_library(design)  # the same value on the same cell is one part: fine
        design = copy.deepcopy(design)
        design["packages"] = {second["refdes"]: "0603"}
        with self.assertRaisesRegex(ValueError, "need different parts"):
            H.plan_library(design)
