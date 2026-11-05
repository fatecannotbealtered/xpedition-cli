"""Designer's verification logs, read into findings: `vdrc.log` and `grc.log`."""

from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import verification

VDRC_LOG = """\
Verification Report

SEVERITY: Error
  GROUP: Connectivity : 1 Error(s)
    drc-SINGLE-PIN - [flatnet : SPARE, component: U1($2I14), pin: $1P7] Net has a single pin

SEVERITY: Warning
  GROUP: Interconnectivity : 2 Warning(s)
    drc-BI-GROUND - [flatnet : GND, component: U1($2I14), pin: $1P11] BI pin connected to ground
    drc-NO-NAME - [component: R1($2I20)]

SEVERITY: Note
  GROUP: Naming : 1 Note(s)
    drc-NET-CASE - [flatnet : vcc] Net name is not upper case
  this line is not a finding
"""

GRC_LOG = """\
Graphical rules report
  Off-grid pins
      U1 pin 3 at (101, 200) is off the grid
  Dangling wires
      No issues found
    Overlapping labels
      label SDA overlaps label SCL on sheet 2
Summary
"""


class VdrcTest(unittest.TestCase):
    def test_each_finding_carries_its_severity_rule_and_fields(self):
        findings = verification.parse_vdrc(VDRC_LOG)
        self.assertEqual(
            [(f["severity"], f["source"], f["refdes"], f["net"]) for f in findings],
            [
                ("high", "xpedition/verify:drc-SINGLE-PIN", "U1", "SPARE"),
                ("medium", "xpedition/verify:drc-BI-GROUND", "U1", "GND"),
                ("medium", "xpedition/verify:drc-NO-NAME", "R1", None),
                ("low", "xpedition/verify:drc-NET-CASE", None, "vcc"),
            ],
        )
        first = findings[0]
        self.assertEqual(first["finding"], "Net has a single pin")
        self.assertEqual(first["evidence"], ["Connectivity", "flatnet : SPARE, component: U1($2I14), pin: $1P7", "$1P7"])
        self.assertEqual(first["confidence"], 1.0)
        # a finding without a message is named by its rule
        self.assertEqual(findings[2]["finding"], "drc-NO-NAME")

    def test_an_unknown_severity_is_info_and_an_empty_log_has_nothing(self):
        findings = verification.parse_vdrc("SEVERITY: Fatal\n  rule-X - [flatnet : A] broken\n")
        self.assertEqual([f["severity"] for f in findings], ["info"])
        self.assertEqual(verification.parse_vdrc(""), [])


class GrcTest(unittest.TestCase):
    def test_issues_are_read_under_their_report_and_check(self):
        findings = verification.parse_grc(GRC_LOG)
        self.assertEqual(
            [(f["source"], f["finding"]) for f in findings],
            [
                ("xpedition/grc:Off-grid pins", "U1 pin 3 at (101, 200) is off the grid"),
                ("xpedition/grc:Overlapping labels", "label SDA overlaps label SCL on sheet 2"),
            ],
        )
        self.assertEqual(findings[0]["evidence"], ["Graphical rules report", "Off-grid pins"])
        self.assertTrue(all(f["severity"] == "low" and f["refdes"] is None for f in findings))

    def test_no_issues_and_an_empty_log_give_no_findings(self):
        self.assertEqual(verification.parse_grc("Graphical rules report\n  Dangling wires\n      No issues found\n"), [])
        self.assertEqual(verification.parse_grc(""), [])


if __name__ == "__main__":
    unittest.main()
