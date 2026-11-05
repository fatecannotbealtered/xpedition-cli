"""`schematic check`'s own rules, on the design as Designer reads it back."""

from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import review_engine as R
from xpedition_cli.adapter.plan import schematic_attribute_display as D


def _project(components, nets=None, connections=None):
    return {
        "project": "t",
        "revision": "R0",
        "sheets": [],
        "components": components,
        "nets": nets or [],
        "connections": connections or [],
    }


def _part(refdes, part, *pins, **extra):
    """pins are (number, net) pairs; net None means open; 'NC' means marked no-connect."""
    plist = []
    for number, net in pins:
        if net == "NC":
            plist.append({"number": number, "net": None, "no_connect": True})
        else:
            plist.append({"number": number, "net": net, "no_connect": False})
    return {"refdes": refdes, "internal_part_no": part, "pins": plist, **extra}


def _sources(findings):
    return sorted(f["source"] for f in findings)


class ReviewEngineTest(unittest.TestCase):
    def test_open_pin_reported_unless_marked_no_connect(self):
        project = _project(
            [
                _part("U1", "IC", ("1", "+3V3"), ("2", "GND"), ("3", None), ("4", "NC")),
                _part("C1", "CAP", ("1", "+3V3"), ("2", "GND")),
            ]
        )
        findings = R.schematic_findings(project)
        open_pins = [f for f in findings if f["source"] == "cli/open-pin"]
        self.assertEqual([f["evidence"] for f in open_pins], [["U1.3"]])

    def test_single_pin_net_and_unnamed_junction_rules(self):
        project = _project(
            [
                _part("U1", "IC", ("1", "+3V3"), ("2", "GND"), ("3", "LONELY")),
                _part("C1", "CAP", ("1", "+3V3"), ("2", "GND")),
                _part("R1", "RES", ("1", "$2N9"), ("2", "GND")),
                _part("R2", "RES", ("1", "$2N9"), ("2", "GND")),
                _part("R3", "RES", ("1", "$2N9"), ("2", "GND")),
            ],
            nets=[{"name": "$2N9", "unnamed": True}],
        )
        findings = R.schematic_findings(project)
        self.assertEqual([f["net"] for f in findings if f["source"] == "cli/single-pin-net"], ["LONELY"])
        self.assertEqual([f["net"] for f in findings if f["source"] == "cli/unnamed-net"], ["$2N9"])

    def test_decoupling_and_i2c_pullups(self):
        project = _project(
            [
                _part("U1", "MCU", ("1", "+3V3"), ("2", "GND"), ("3", "I2C_SDA"), ("4", "I2C_SCL")),
                _part("U2", "SENSOR", ("1", "+5V"), ("2", "GND"), ("3", "I2C_SDA"), ("4", "I2C_SCL")),
                _part("C1", "CAP", ("1", "+3V3"), ("2", "GND")),
                _part("R1", "RES", ("1", "I2C_SDA"), ("2", "+3V3")),
            ]
        )
        findings = R.schematic_findings(project)
        decoupling = [(f["refdes"], f["net"]) for f in findings if f["source"] == "cli/decoupling"]
        self.assertEqual(decoupling, [("U2", "+5V")])
        pullups = [f["net"] for f in findings if f["source"] == "cli/i2c-pullup"]
        self.assertEqual(pullups, ["I2C_SCL"])

    def test_missing_part_numbers_are_one_summary_finding(self):
        project = _project(
            [_part("R1", "", ("1", "A"), ("2", "GND")), _part("R2", "", ("1", "A"), ("2", "GND"))]
        )
        findings = [
            f for f in R.schematic_findings(project) if f["source"] == "cli/missing-part-number"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["evidence"], ["R1", "R2"])

    def test_net_name_and_refdes_prefix_rules(self):
        project = _project([_part("R1", "RES", ("1", "sda_low"), ("2", "GND"), ("3", "GND"))])
        findings = R.schematic_findings(project)
        self.assertEqual([f["net"] for f in findings if f["source"] == "cli/net-name"], ["sda_low"])
        self.assertEqual([f["refdes"] for f in findings if f["source"] == "cli/refdes-prefix"], ["R1"])

    def test_power_and_ground_recognition(self):
        self.assertTrue(R.is_power_net("+3V3"))
        self.assertTrue(R.is_power_net("VBUS"))
        self.assertTrue(R.is_power_net("5V"))
        self.assertTrue(R.is_power_net("VDD_IO"))
        self.assertTrue(R.is_ground_net("GND"))
        self.assertTrue(R.is_ground_net("AGND"))
        self.assertTrue(R.is_ground_net("VSS"))
        self.assertFalse(R.is_power_net("GND"))
        self.assertFalse(R.is_power_net("LID_DET"))

    def test_run_review_merges_extra_findings_and_sorts_by_severity(self):
        project = _project(
            [
                _part("U1", "IC", ("1", "+3V3"), ("2", "GND")),
                _part("C1", "CAP", ("1", "+3V3"), ("2", "GND")),
            ]
        )
        extra = [
            {
                "severity": "low",
                "refdes": None,
                "net": None,
                "source": "xpedition/grc:x",
                "finding": "tool said so",
                "evidence": [],
                "suggestion": "",
                "confidence": 1.0,
            }
        ]
        report = R.run_review(project, extra)
        self.assertEqual(report["summary"]["total"], 1)
        self.assertEqual(report["findings"][0]["source"], "xpedition/grc:x")
        self.assertIs(report["summary"]["valid"], False)

    def test_i2c_pullup_counts_one_series_resistor_away(self):
        project = _project(
            [
                _part("U1", "MCU", ("1", "+3V3"), ("2", "GND"), ("3", "I2C_SDA"), ("4", "I2C_SCL")),
                _part("R1", "RES", ("1", "I2C_SDA"), ("2", "+3V3")),
                _part("R2", "RES", ("1", "I2C_SCL"), ("2", "+3V3")),
                _part("R3", "RES", ("1", "I2C_SDA"), ("2", "SDA_HOST")),
                _part("R4", "RES", ("1", "I2C_SCL"), ("2", "SCL_HOST")),
                _part("J1", "CON", ("1", "SDA_HOST"), ("2", "SCL_HOST"), ("3", "GND")),
                _part("R5", "RES", ("1", "SDA_AUX"), ("2", "AUX_X")),
                _part("U2", "SENSOR", ("1", "SDA_AUX"), ("2", "GND")),
                _part("C1", "CAP", ("1", "+3V3"), ("2", "GND")),
            ]
        )
        findings = R.schematic_findings(project)
        self.assertEqual([f["net"] for f in findings if f["source"] == "cli/i2c-pullup"], ["SDA_AUX"])

    def test_verify_project_reports_the_structure_it_finds(self):
        project = _project(
            [_part("R1", "RES", ("1", "A")), _part("R1", "RES", ("1", "B")), _part("U1", "IC", ("1", "A"))],
            nets=[{"name": "A"}, {"name": "A"}, {"name": "B"}],
            connections=[{"net": "A", "pins": ["R1.1"]}, {"net": "GONE", "pins": ["U1.1"]}],
        )
        checks = R.verify_project(project)
        self.assertEqual(
            checks,
            {
                "valid": False,
                "component_count": 3,
                "net_count": 3,
                "connection_count": 2,
                "duplicate_refdes": ["R1"],
                "duplicate_nets": ["A"],
                "connections_with_missing_nets": ["GONE"],
            },
        )
        clean = _project([_part("R1", "RES", ("1", "A"))], nets=[{"name": "A"}], connections=[{"net": "A", "pins": ["R1.1"]}])
        self.assertIs(R.verify_project(clean)["valid"], True)
        structure = [f for f in R.run_review(project)["findings"] if f["source"] == "cli/structure"]
        self.assertEqual(len(structure), 3)
        self.assertTrue(all(f["severity"] == "high" for f in structure))


class AttributeDisplayTest(unittest.TestCase):
    def test_display_names_compare_without_case_or_spaces(self):
        self.assertEqual(D.canonical_name(" Ref  Designator "), "refdesignator")
        self.assertTrue(D.is_display_name("part number"))
        self.assertTrue(D.is_display_name("REF DESIGNATOR"))
        self.assertFalse(D.is_display_name("Manufacturer"))
        self.assertEqual(len(D.DISPLAY_NAMES), 6)
