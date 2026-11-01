"""Offline boundaries/oracles only: no Xpedition COM, licence or GUI invocation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import checks
import engine
import processes

PACK = Path(__file__).resolve().parent
ROOT = PACK.parent.parent
# Unit oracles are self-contained and synthetic. Real native runs still load
# their independently frozen inputs through the unchanged manifest/engine.
INPUTS = PACK / "fixtures" / "unit-oracles"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Boundaries(unittest.TestCase):
    def test_grant_timestamp_uses_spec_utc_form(self):
        self.assertTrue(engine.utc().endswith("Z"))

    def test_escape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            with self.assertRaises(checks.Stop):
                checks.within(base / "safe/../../outside", base / "safe")

    def test_false_prefix_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            with self.assertRaises(checks.Stop):
                checks.within(base / "safe-other/file", base / "safe")

    def test_candidate_tamper_refused(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            p = base / "candidate"
            p.write_bytes(b"approved")
            manifest = {
                "task_root": str(base),
                "scratch_root": str(base / "xpedition-native-acceptance/run"),
                "results_root": str(base / "xpedition-results/run"),
                "pinned_files": [{"source": str(p), "sha256": sha(b"approved")}],
                "seed_files": [],
                "entry_files": [],
            }
            with patch.object(engine, "ROOT", base):
                self.assertEqual(engine.verify(manifest)["status"], "passed")
                p.write_bytes(b"changed")
                with self.assertRaises(checks.Stop) as e:
                    engine.verify(manifest)
                self.assertEqual(e.exception.code, "E_HASH")

    def test_arbitrary_command_rejected_before_process(self):
        runner = engine.Runner.__new__(engine.Runner)
        with self.assertRaises(checks.Stop) as e:
            runner.cli("unsafe", "powershell run-anything", {})
        self.assertEqual(e.exception.code, "E_ALLOWLIST")

    def test_destructive_session_commands_absent(self):
        self.assertNotIn("session close", engine.COMMANDS)
        self.assertNotIn("session start", engine.COMMANDS)
        self.assertNotIn("project restore", engine.COMMANDS)

    def test_no_flag_passthrough(self):
        child = subprocess.run(
            [sys.executable, "-B", str(PACK / "run.py"), "--run", "--command", "whoami"],
            capture_output=True,
        )
        self.assertEqual(child.returncode, 2)
        self.assertIn(b"unrecognized arguments", child.stderr)

    def test_conflicting_modes_refused(self):
        child = subprocess.run(
            [sys.executable, "-B", str(PACK / "run.py"), "--run", "--verify-only"],
            capture_output=True,
        )
        self.assertEqual(child.returncode, 2)

    def test_unknown_native_step_refused_before_manifest(self):
        child = subprocess.run(
            [sys.executable, "-B", str(PACK / "native_worker.py"), "--step", "arbitrary-command"],
            capture_output=True,
        )
        self.assertEqual(child.returncode, 2)
        self.assertIn(b"invalid choice", child.stderr)

    def test_entry_hash_guard_precedes_engine_import(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            body = (PACK / "run.py").read_text(encoding="utf-8")
            approved = b'raise AssertionError("Engine must not be imported")\n'
            m = {"entry_files": [{"name": "engine.py", "sha256": sha(approved)}]}
            raw = json.dumps(m).encode()
            (base / "manifest.json").write_bytes(raw)
            import re

            body = re.sub(
                r'MANIFEST_SHA256 = "[^"]+"', 'MANIFEST_SHA256 = "' + sha(raw) + '"', body
            )
            (base / "run.py").write_text(body, encoding="utf-8")
            (base / "engine.py").write_bytes(b'raise RuntimeError("Unapproved engine ran")\n')
            child = subprocess.run(
                [sys.executable, "-B", str(base / "run.py"), "--run"], capture_output=True
            )
            self.assertEqual(child.returncode, 2)
            self.assertIn(b"entry helper changed", child.stdout)
            self.assertNotIn(b"Unapproved engine ran", child.stdout)

    def test_unknown_result_stops_without_next_call(self):
        runner = engine.Runner.__new__(engine.Runner)
        seen = []
        runner.native = lambda step: seen.append(step)

        def cli(label, command, options, phase=None, token=None):
            seen.append(label)
            if phase == "plan":
                return {"preview": {"can_apply": True}, "confirm_token": "bound-token"}
            return {"receipt": {"outcome": {"execution": "completed", "change": "unknown"}}}

        runner.cli = cli
        with self.assertRaises(checks.Stop) as e:
            runner.apply("fixed", "project create", {})
        self.assertEqual(e.exception.code, "E_OUTCOME_UNKNOWN")
        self.assertEqual(seen, ["guard", "fixed-plan", "fixed-apply"])

    def test_public_sdk_receipt_position_is_consumed(self):
        runner = engine.Runner.__new__(engine.Runner)
        runner.native = lambda step: None
        calls = []

        def cli(label, command, options, phase=None, token=None):
            calls.append(command)
            if phase == "plan":
                return {"preview": {"can_apply": True}, "confirm_token": "bound-token"}
            if command == "operation show":
                return {"operation_status": {"state": "succeeded"}}
            return {
                "receipt": {
                    "operation_id": "recorded-operation",
                    "outcome": {"execution": "completed", "change": "applied"},
                }
            }

        runner.cli = cli
        runner.write = lambda name, value: None
        receipt = runner.apply("fixed", "project create", {})
        self.assertEqual(receipt["operation_id"], "recorded-operation")
        self.assertEqual(calls, ["project create", "project create", "operation show"])

    def test_missing_receipt_stops_without_replay(self):
        runner = engine.Runner.__new__(engine.Runner)
        runner.native = lambda step: None
        runner.cli = lambda label, command, options, phase=None, token=None: (
            {"preview": {"can_apply": True}, "confirm_token": "token"}
            if phase == "plan"
            else {"outcome": {"execution": "completed", "change": "applied"}}
        )
        with self.assertRaises(checks.Stop) as e:
            runner.apply("fixed", "project create", {})
        self.assertEqual(e.exception.code, "E_PROTOCOL")


class IndependentOracles(unittest.TestCase):
    def test_missing_getter_not_zero(self):
        with self.assertRaises(checks.Stop):
            checks.close(None, 0, 0.01, "actual")

    def test_nan_refused(self):
        with self.assertRaises(checks.Stop):
            checks.close(float("nan"), 20, 0.01, "actual")

    def test_positive_ccw_requires_positive_statement(self):
        self.assertFalse(
            checks.positive_ccw([{"documentation": "Use clockwise or counterclockwise rotation."}])
        )
        self.assertTrue(
            checks.positive_ccw(
                [{"documentation": "Positive angles are measured counter-clockwise."}]
            )
        )

    def test_clockwise_positive_not_misread(self):
        self.assertFalse(
            checks.positive_ccw(
                [
                    {
                        "documentation": "Positive angles are clockwise; "
                        "negative angles are counterclockwise."
                    }
                ]
            )
        )

    def test_conflicting_orientation_sources_block(self):
        self.assertFalse(
            checks.positive_ccw(
                [
                    {"documentation": "Positive angles are counterclockwise."},
                    {"documentation": "Positive angles are clockwise."},
                ]
            )
        )

    def test_full_native_paths_preserved(self):
        expected = json.loads((INPUTS / "expected-connections.json").read_text(encoding="utf-8"))
        record = {
            "project": "Board1",
            "sheets": [{"number": 1, "name": "Schematic1.1"}],
            "components": [
                {
                    "refdes": "J1",
                    "uid": "real-j1",
                    "sheet": 1,
                    "pins": [
                        {"number": "1", "net": "QA_SIGNAL"},
                        {"number": "2", "net": "QA_RETURN"},
                    ],
                },
                {
                    "refdes": "R1",
                    "uid": "real-r1",
                    "sheet": 1,
                    "pins": [
                        {"number": "1", "net": "QA_SIGNAL"},
                        {"number": "2", "net": "QA_RETURN"},
                    ],
                },
            ],
        }
        verdict = checks.schematic(record, expected)
        self.assertEqual(verdict["nets"], expected["nets"])
        record["components"][1]["sheet"] = 2
        with self.assertRaises(checks.Stop):
            checks.schematic(record, expected)

    def test_unintended_net_merge_detected(self):
        expected = json.loads((INPUTS / "expected-connections.json").read_text(encoding="utf-8"))
        record = {
            "project": "Board1",
            "sheets": [{"number": 1, "name": "Schematic1.1"}],
            "components": [
                {
                    "refdes": ref,
                    "uid": ref,
                    "sheet": 1,
                    "pins": [{"number": str(pin), "net": "QA_SIGNAL"} for pin in (1, 2)],
                }
                for ref in ("J1", "R1")
            ],
        }
        with self.assertRaises(checks.Stop):
            checks.schematic(record, expected)

    def test_orphan_input_hint_not_actual(self):
        before = {
            "components": [{"uid": "baseline", "refdes": "J1"}],
            "unnamed_components": [],
            "connections": [],
        }
        added = {"uid": "new", "refdes": "", "x": None, "y": None, "pins": []}
        after = {
            "components": before["components"],
            "unnamed_components": [added],
            "connections": [],
        }
        error = {
            "details": {
                "orphan_placed": True,
                "orphan_actual": added,
                "requested_position": {"x": 500, "y": 400},
            }
        }
        with self.assertRaises(checks.Stop):
            checks.orphan(before, after, error, {"real_add_calls": 1, "setter_faults": 1})

    def test_orphan_expected_partial_never_engineering_success(self):
        before = {
            "components": [{"uid": "baseline", "refdes": "J1"}],
            "unnamed_components": [],
            "connections": [],
        }
        added = {"uid": "new", "refdes": "", "x": 500, "y": 400, "pins": []}
        after = {
            "components": before["components"],
            "unnamed_components": [added],
            "connections": [],
        }
        error = {"details": {"orphan_placed": True, "orphan_actual": added}}
        verdict = checks.orphan(before, after, error, {"real_add_calls": 1, "setter_faults": 1})
        self.assertEqual(verdict["engineering_task_outcome"], "partial")
        self.assertEqual(verdict["persistence"].split(";")[0], "unknown")
        with self.assertRaises(checks.Stop):
            checks.orphan(before, after, error, {"real_add_calls": 2, "setter_faults": 1})

    def test_baseline_connections_must_be_unchanged(self):
        before = {
            "components": [{"uid": "baseline", "refdes": "J1"}],
            "unnamed_components": [],
            "connections": [],
        }
        added = {"uid": "new", "refdes": "", "x": 500, "y": 400, "pins": []}
        after = {
            "components": before["components"],
            "unnamed_components": [added],
            "connections": [{"net": "NEW", "pins": ["J1.1", "R1.1"]}],
        }
        with self.assertRaises(checks.Stop):
            checks.orphan(
                before,
                after,
                {"details": {"orphan_placed": True, "orphan_actual": added}},
                {"real_add_calls": 1, "setter_faults": 1},
            )

    def test_pad_semantics_cannot_be_inferred_from_inputs(self):
        expected = json.loads((INPUTS / "expected-geometry.json").read_text(encoding="utf-8"))
        record = {
            "layer_count": 4,
            "components": [
                {
                    "refdes": "J1",
                    "pins": [
                        {
                            "number": "1",
                            "pads": [{"layer": 1, "type_enum_names": [], "geometries": []}],
                        }
                    ],
                }
            ],
        }
        with self.assertRaises(checks.Stop) as e:
            checks.pad_correspondence(record, expected)
        self.assertEqual(e.exception.code, "E_PAD_LAYER_CONTRACT")


@unittest.skipUnless(os.name == "nt", "Windows finite process job")
class FiniteProcesses(unittest.TestCase):
    def test_contained_python_readonly_exit(self):
        result = processes.run(
            [sys.executable, "-B", "-c", 'print("finite-child-ok")'],
            cwd=ROOT,
            env=os.environ.copy(),
            timeout=10,
        )
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"].strip(), "finite-child-ok")
        self.assertFalse(result["timed_out"])

    def test_timeout_kills_only_new_child(self):
        import _winapi

        # The deadline can expire before Python prints anything. Observe actual
        # process handles, and retain an unrelated owned sentinel outside the job.
        sentinel = subprocess.Popen(
            [sys.executable, "-B", "-c", "import sys; sys.stdin.buffer.read()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        children = []
        original_popen = subprocess.Popen

        def created(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            children.append(process)
            return process

        try:
            with patch.object(processes.subprocess, "Popen", side_effect=created):
                result = processes.run(
                    [sys.executable, "-B", "-c", "import threading; threading.Event().wait()"],
                    cwd=ROOT,
                    env=os.environ.copy(),
                    timeout=0.5,
                )
            self.assertTrue(result["timed_out"])
            self.assertTrue(result["child_execution_started"])
            self.assertEqual(len(children), 1)
            self.assertEqual(result["pid"], children[0].pid)
            self.assertNotEqual(result["pid"], sentinel.pid)
            self.assertIsInstance(result["exit_code"], int)
            self.assertEqual(
                _winapi.WaitForSingleObject(int(children[0]._handle), 0),
                _winapi.WAIT_OBJECT_0,
            )
            self.assertEqual(
                _winapi.WaitForSingleObject(int(sentinel._handle), 0),
                _winapi.WAIT_TIMEOUT,
            )
            self.assertIsNone(sentinel.poll())
        finally:
            for process in [*children, sentinel]:
                if process.poll() is None:
                    process.kill()  # Only the test's Popen handles; never a CAD PID.
                process.wait(timeout=10)
            if sentinel.stdin:
                sentinel.stdin.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
