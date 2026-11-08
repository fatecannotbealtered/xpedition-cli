"""The BOM's rows, checks, CSV text and comparison with an earlier BOM."""
from __future__ import annotations

import csv
import io
import unittest

from xpedition_cli.adapter.plan import PlanError
from xpedition_cli.adapter.plan import bom


def part(refdes: str, number: str, value: str = "", sheet: int = 1) -> dict:
    return {"refdes": refdes, "part_number": number, "value": value, "sheet": sheet}


DESIGN = [part("R10", "RES-1K", "1k"), part("R2", "RES-1K", "1k", 2), part("C1", "CAP-100N", "100n"),
          part("R3", "RES-1K", "1k0"), part("TP1", ""), part("TP2", "")]
DESCRIPTIONS = {"RES-1K": "Resistor, 1 kOhm", "CAP-100N": "Capacitor, 100 nF"}


class RowsTest(unittest.TestCase):
    def test_grouped_by_part_number_and_value_in_reference_order(self):
        table = bom.rows(DESIGN, DESCRIPTIONS)
        self.assertEqual([(r["Reference"], r["Quantity"]) for r in table],
                         [("C1", 1), ("R2, R10", 2), ("R3", 1), ("TP1", 1), ("TP2", 1)])
        self.assertEqual(table[1]["Description"], "Resistor, 1 kOhm")
        self.assertEqual([r["Item"] for r in table], [1, 2, 3, 4, 5])

    def test_one_row_per_component(self):
        table = bom.rows(DESIGN, DESCRIPTIONS, per_component=True)
        self.assertEqual([r["Reference"] for r in table], ["C1", "R2", "R3", "R10", "TP1", "TP2"])
        self.assertEqual(table[1]["Sheet"], 2)

    def test_csv_text_has_the_header_and_quotes_commas(self):
        text = bom.csv_text(bom.rows(DESIGN, DESCRIPTIONS))
        lines = list(csv.reader(io.StringIO(text)))
        self.assertEqual(lines[0], bom.GROUPED_COLUMNS)
        self.assertEqual(lines[2], ["2", "2", "R2, R10", "RES-1K", "1k", "Resistor, 1 kOhm"])
        per = list(csv.reader(io.StringIO(bom.csv_text(bom.rows(DESIGN, {}, per_component=True), per_component=True))))
        self.assertEqual(per[0], bom.COMPONENT_COLUMNS)


class CheckTest(unittest.TestCase):
    def test_every_rule(self):
        design = DESIGN + [part("C1", "CAP-100N", "100n", 3), part("U1", "MCU-9")]
        findings = bom.check(design, {"RES-1K", "CAP-100N"})
        rules = {f["rule"]: f for f in findings}
        self.assertEqual(rules["missing_part_number"]["objects"], ["TP1", "TP2"])
        self.assertIn("sheets 1, 3", rules["duplicate_refdes"]["message"])
        self.assertEqual(rules["value_mismatch"]["severity"], "warning")
        self.assertIn("1k on R2, R10; 1k0 on R3", rules["value_mismatch"]["message"])
        self.assertEqual(rules["not_in_library"]["objects"], ["U1"])
        self.assertEqual(len({f["id"] for f in findings}), len(findings))

    def test_without_the_library_its_rule_is_skipped(self):
        self.assertNotIn("not_in_library", {f["rule"] for f in bom.check([part("U1", "MCU-9")], None)})

    def test_a_clean_bom_has_no_findings(self):
        self.assertEqual(bom.check([part("R1", "RES-1K", "1k")], {"RES-1K"}), [])


class BaselineTest(unittest.TestCase):
    def test_this_tools_own_bom_reads_back(self):
        text = bom.csv_text(bom.rows(DESIGN, DESCRIPTIONS))
        earlier = bom.read_baseline(("\ufeff" + text).encode("utf-8"))
        self.assertEqual(earlier["R10"], {"part_number": "RES-1K", "value": "1k"})
        self.assertEqual(len(earlier), 6)

    def test_another_tools_bom_in_gbk_with_ranges_and_a_title_line(self):
        text = "物料清单\n位号,料号,值\nR1-R3,RES-1K,1k\n\"C1 C2\",CAP-100N,100n\n"
        earlier = bom.read_baseline(text.encode("gbk"))
        self.assertEqual(sorted(earlier), ["C1", "C2", "R1", "R2", "R3"])

    def test_a_file_without_the_columns_is_refused(self):
        with self.assertRaises(PlanError) as caught:
            bom.read_baseline(b"Name,Count\nx,1\n")
        self.assertEqual(caught.exception.code, "E_VALIDATION")

    def test_designators(self):
        self.assertEqual(bom.designators("R1, R2 R5;R7-R9"), ["R1", "R2", "R5", "R7", "R8", "R9"])
        self.assertEqual(bom.designators("C10-C12"), ["C10", "C11", "C12"])
        self.assertEqual(bom.designators("U1-A"), ["U1-A"])

    def test_compare_added_removed_and_changed(self):
        earlier = {"R1": {"part_number": "RES-1K", "value": "10k"}, "R2": {"part_number": "RES-1K", "value": "1k"},
                   "C1": {"part_number": "CAP-1U", "value": "100n"}}
        now = [part("R1", "RES-1K", "1k"), part("C1", "CAP-100N", "100n"), part("D1", "LED")]
        found = bom.compare(now, earlier, values=bom.baseline_has_values(earlier))
        self.assertEqual(found["added"], [{"refdes": "D1", "part_number": "LED"}])
        self.assertEqual(found["removed"], [{"refdes": "R2", "part_number": "RES-1K"}])
        self.assertEqual(found["changed"], [{"refdes": "C1", "field": "part_number", "before": "CAP-1U", "after": "CAP-100N"},
                                            {"refdes": "R1", "field": "value", "before": "10k", "after": "1k"}])
        self.assertEqual(bom.compare(now, earlier, values=False)["changed"][-1]["refdes"], "C1")


if __name__ == "__main__":
    unittest.main()
