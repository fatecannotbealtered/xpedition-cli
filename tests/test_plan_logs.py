"""The stock tools' logs read back: the packager, JobWizard and forward annotation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xpedition_cli.adapter.plan import logs

PACKAGER_LOG = r"""
                                    Packager
     Common Data Base has been read

     Target PDB Name: Integration\LocalPartsDB.pdb
     ERROR:  There is no Part Number: R in the Parts
      DataBase for symbols with Part Name: R and Part Label: (null).

     ERROR:  There is no Part Number: C in the Parts

     Testing of Packaging is being terminated with 2 errors and 0 warnings.
      Design has NOT been packaged.
"""

ANNOTATION_LOG = (
    b"     Clustering 39 Symbols:\r\n"
    b"     WARNING:  \r\n"
    b"\tUnable to update the cell 'CLI_SOIC4' in the Library Manager.\r\n"
    b"     18 nets were found containing 95 pins\r\n"
    b"     39 components were found\r\n"
    b"     ERROR:  Cell CLI_SOIC4 has 4 unique Alphanumeric Pin Numbers while\r\n"
    b"      Part Number XC6206-3.3 has 3.\r\n"
    b"     DataBase Load is being terminated with 1 errors and 2 warnings.\r\n"
)


class LogsTest(unittest.TestCase):
    def tmp_dir(self) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name)

    def test_packager_messages_extract_errors_and_verdict(self):
        log = self.tmp_dir() / "PartPkg.log"
        log.write_text(PACKAGER_LOG, encoding="utf-8")
        errors, verdict = logs.packager_messages(log)
        self.assertEqual(
            errors,
            ["ERROR:  There is no Part Number: R in the Parts", "ERROR:  There is no Part Number: C in the Parts"],
        )
        self.assertEqual(verdict, "Design has NOT been packaged.")
        self.assertEqual(logs.parse_packager_log(PACKAGER_LOG), (errors, verdict))

    def test_a_clean_packager_run_has_no_errors_and_no_verdict(self):
        self.assertEqual(logs.parse_packager_log("     Packaging completed.\n     0 errors.\n"), ([], ""))

    def test_packager_messages_tolerate_a_missing_log(self):
        self.assertEqual(logs.packager_messages(self.tmp_dir() / "absent.log"), ([], ""))

    def test_annotation_summary_reads_counts_errors_and_the_verdict(self):
        log = self.tmp_dir() / "ForwardAnnotation.txt"
        log.write_bytes(ANNOTATION_LOG)
        summary = logs.annotation_summary(log)
        self.assertEqual((summary["components"], summary["nets"], summary["pins"]), (39, 18, 95))
        self.assertEqual(summary["errors"], ["ERROR:  Cell CLI_SOIC4 has 4 unique Alphanumeric Pin Numbers while"])
        self.assertEqual(summary["warnings"], ["WARNING:"])
        self.assertIs(summary["completed"], False)
        log.write_bytes(b"Forward-Annotation on the Layout Design has been successfully completed.\r\n")
        self.assertIs(logs.annotation_summary(log)["completed"], True)

    def test_a_missing_annotation_log_has_not_completed(self):
        summary = logs.annotation_summary(self.tmp_dir() / "none.txt")
        self.assertEqual(
            summary,
            {"errors": [], "warnings": [], "components": None, "nets": None, "pins": None, "completed": False},
        )

    def test_jobwizard_messages_find_the_copy_count_and_failures(self):
        # The bytes stay ASCII here: JobWizard writes the machine's ANSI code page, and
        # the decoding fallback is covered by the next test.
        log = self.tmp_dir() / "jobwizard.log"
        log.write_bytes(b"Job Wizard\r\n\r\nSuccessfully copied 58 file(s).\r\n")
        self.assertEqual(logs.jobwizard_messages(log), ([], 58))
        log.write_bytes(b"Job Wizard\r\nERROR: unable to read the design information.\r\n")
        self.assertEqual(logs.jobwizard_messages(log), (["ERROR: unable to read the design information."], None))
        self.assertEqual(logs.jobwizard_messages(log.with_name("none.log")), ([], None))

    def test_jobwizard_messages_survive_bytes_the_local_code_page_cannot_read(self):
        """The real logs are Chinese. Whatever the running machine's ANSI code page makes of
        those bytes, the reader must not raise and must still see the lines around them."""
        log = self.tmp_dir() / "jobwizard.log"
        log.write_bytes("未能从通用数据库中获取设计信息。\r\n".encode() + b"ERROR: the run failed.\r\n")
        errors, copied = logs.jobwizard_messages(log)
        self.assertIsNone(copied)
        self.assertTrue(any("ERROR: the run failed." in line for line in errors))

    def test_tool_text_reads_utf8_first_and_never_fails(self):
        self.assertEqual(logs.tool_text("﻿plain".encode()), "plain")
        self.assertEqual(logs.tool_text("错误".encode()), "错误")
        self.assertIsInstance(logs.tool_text(b"\xff\xfe\x80 broken"), str)


if __name__ == "__main__":
    unittest.main()
