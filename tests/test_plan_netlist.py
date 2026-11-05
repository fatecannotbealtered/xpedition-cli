"""The checks an agent relies on to call a drawn schematic good must not pass what is not.

Each of these used to answer "fine" about a result that was not: a design whose two
sheets both had an R201, and a drawn schematic carrying a net the plan never asked for.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from xpedition_cli.adapter.plan import netlist, schematic_layout

EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "demo-sensor-board.json"


def _design() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


# -- the read-back after a draw -------------------------------------------------------

VERIFY = {
    "nets": {"VCC": ["R1.1", "U1.1"], "GND": ["R2.2", "U1.2"]},
    "links": [["R1.2", "R2.1"]],
    "parts": ["R1", "R2", "U1"],
    "no_connect": ["U1.3"],
}


def _snapshot(*extra_components: dict) -> dict:
    return {
        "components": [
            {"refdes": "R1", "pins": [{"number": "1", "net": "VCC"}, {"number": "2"}]},
            {"refdes": "R2", "pins": [{"number": "1"}, {"number": "2", "net": "GND"}]},
            {
                "refdes": "U1",
                "pins": [
                    {"number": "1", "net": "VCC"},
                    {"number": "2", "net": "GND"},
                    {"number": "3"},
                ],
            },
            *extra_components,
        ]
    }


# R1.2 and R2.1 meet in an unnamed junction the plan asked for (a `none` ladder node)
JUNCTION = {"R1.2": "$1N7", "R2.1": "$1N7"}


class NetlistTest(unittest.TestCase):
    def test_a_reference_designator_used_on_two_sheets_is_refused(self):
        design = _design()
        # R301 on sheet 4 renamed to R201, which sheet 3 already has: Designer numbers one
        # design, and the expected netlist put R201.2 on two nets at once
        design["sheets"][3] = json.loads(json.dumps(design["sheets"][3]).replace('"R301"', '"R201"'))
        with self.assertRaisesRegex(schematic_layout.DesignError, "R201.*sheet 3 and sheet 4"):
            schematic_layout.plan(design)

    def test_the_plan_tells_the_read_back_its_parts_and_no_connect_pins(self):
        params = schematic_layout.plan_to_params(_design(), "X.prj")
        verify = params["verify"]
        self.assertEqual(len(verify["parts"]), 30)
        self.assertEqual(verify["parts"], sorted(verify["parts"]))
        self.assertEqual(verify["no_connect"], ["U101.4"])
        parts = {part["refdes"]: part["sheet"] for part in schematic_layout.plan(_design()).parts}
        self.assertEqual(parts["J101"], 2)
        self.assertEqual(parts["R201"], 3)

    def test_a_drawing_that_is_exactly_the_plan_matches(self):
        result = netlist.compare_netlist(_snapshot(), VERIFY, JUNCTION)
        self.assertIs(result["matches"], True)
        self.assertEqual(result["extra_nets"], [])
        self.assertEqual(result["unplanned_components"], [])
        self.assertEqual(result["no_connects_joined"], [])

    def test_wiring_the_plan_never_asked_for_does_not_match(self):
        # a template's leftover part and wire, joined to a pin the plan marked no-connect:
        # every planned net still reads back exactly, which used to be all that was checked
        leftover = {"refdes": "J9", "pins": [{"number": "1", "net": "OLD_NET"}]}
        identities = {**JUNCTION, "U1.3": "OLD_NET", "J9.1": "OLD_NET"}
        result = netlist.compare_netlist(_snapshot(leftover), VERIFY, identities)
        self.assertEqual(result["differences"], [])
        self.assertEqual(result["links_broken"], [])
        self.assertIs(result["matches"], False)
        self.assertEqual(result["extra_nets"], [{"net": "OLD_NET", "pins": ["J9.1", "U1.3"]}])
        self.assertEqual(result["unplanned_components"], ["J9"])
        self.assertEqual(result["no_connects_joined"], [
            {"pin": "U1.3", "net": "OLD_NET", "pins": ["J9.1", "U1.3"]}
        ])

    def test_a_planned_part_wired_into_an_unnamed_net_does_not_match(self):
        identities = {"R1.2": "$1N7", "R2.1": "$1N7", "U1.1": "$1N7"}
        result = netlist.compare_netlist(_snapshot(), VERIFY, identities)
        self.assertIs(result["matches"], False)
        self.assertEqual(result["extra_nets"], [{"net": "$1N7", "pins": ["R1.2", "R2.1", "U1.1"]}])

    def test_a_request_from_before_parts_were_sent_still_compares(self):
        older = {"nets": VERIFY["nets"], "links": VERIFY["links"]}
        leftover = {"refdes": "J9", "pins": [{"number": "1", "net": "OLD_NET"}]}
        result = netlist.compare_netlist(_snapshot(leftover), older, JUNCTION)
        # without the plan's part list nothing is called unplanned
        self.assertIs(result["matches"], True)
        self.assertEqual(result["unplanned_components"], [])
