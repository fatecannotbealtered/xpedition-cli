"""Guard-only tests: no pytest, SDK, product import, or native CAD requests.

Run only inside a separately verified filesystem/process sandbox with TEMP and
TMP pointing inside its disposable run root.  This file is intentionally not a
pytest test: test collection must not import the product before preflight.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).absolute().parent))
import offline_test_guard as guard  # noqa: E402


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.task = Path(self.temporary.name).resolve() / "task"
        self.repo = self.task / "xpedition-cli"
        self.tests = self.repo / "tests"
        self.tests.mkdir(parents=True)
        (self.tests / "conftest.py").write_text("", encoding="utf-8")
        (self.tests / "test_sample.py").write_text("", encoding="utf-8")
        self.runs = self.task / "xpedition-offline-runs"
        self.run = self.runs / "one"
        self.run.mkdir(parents=True)
        self.profile = Path(self.temporary.name).resolve() / "old-profile"
        self.profile.mkdir()
        self.inherited = dict(os.environ)
        self.inherited["USERPROFILE" if os.name == "nt" else "HOME"] = str(self.profile)

    def layout(self, selectors=("test_sample.py",)):
        return guard.make_layout(self.repo, self.run, selectors, self.inherited)

    def test_valid_plan_contains_default_journal_and_declared_paths(self):
        layout = self.layout()
        environment = guard.make_environment(layout, self.inherited)
        guard.verify_environment(layout, environment)
        self.assertEqual(layout.journal, layout.config / "driver-v2")
        self.assertEqual(environment["XPEDITION_CLI_CONFIG_DIR"], str(layout.config))
        self.assertEqual(environment["USERPROFILE"], str(layout.home))
        self.assertEqual(environment["TEMP"], str(layout.temp))
        self.assertNotIn("PYTEST_ADDOPTS", environment)
        self.assertFalse((self.profile / ".xpedition-cli").exists())

    def test_run_root_must_be_in_expected_parent_and_empty(self):
        elsewhere = self.task / "elsewhere"
        elsewhere.mkdir()
        with self.assertRaises(guard.IsolationError):
            guard.make_layout(self.repo, elsewhere, ("test_sample.py",), self.inherited)
        (self.run / "already-present").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(guard.IsolationError, "run_root_must_be_empty"):
            self.layout()

    def test_parent_traversal_and_copied_or_option_selectors_are_rejected(self):
        with self.assertRaises(guard.IsolationError):
            guard.make_layout(
                self.repo,
                str(self.runs / "missing" / ".." / "one"),
                ("test_sample.py",),
                self.inherited,
            )
        for selector in ("--basetemp", "../test_sample.py", str(self.tests / "test_sample.py")):
            with self.subTest(selector=selector), self.assertRaises(guard.IsolationError):
                self.layout((selector,))

    def test_inherited_profile_missing_is_refused(self):
        inherited = dict(self.inherited)
        inherited.pop("USERPROFILE" if os.name == "nt" else "HOME", None)
        with self.assertRaisesRegex(guard.IsolationError, "original_user_profile_not_declared"):
            guard.make_layout(self.repo, self.run, ("test_sample.py",), inherited)

    def test_default_config_overlap_is_refused_without_reading_it(self):
        dummy_default = self.runs / ".xpedition-cli"
        dummy_default.mkdir()
        inherited = dict(self.inherited)
        inherited["USERPROFILE" if os.name == "nt" else "HOME"] = str(self.runs)
        with self.assertRaisesRegex(guard.IsolationError, "run_root_overlaps_default_config"):
            guard.make_layout(self.repo, dummy_default, ("test_sample.py",), inherited)

    def test_external_journal_or_changed_environment_is_refused(self):
        layout = self.layout()
        environment = guard.make_environment(layout, self.inherited)
        external = dataclasses.replace(layout, journal=self.profile / "driver-v2")
        with self.assertRaisesRegex(guard.IsolationError, "journal_outside_config"):
            guard.verify_environment(external, environment)
        for key in ("XPEDITION_CLI_CONFIG_DIR", "HOME", "USERPROFILE", "TEMP", "APPDATA"):
            altered = dict(environment)
            altered[key] = str(self.profile / ".xpedition-cli")
            with self.subTest(key=key), self.assertRaises(guard.IsolationError):
                guard.verify_environment(layout, altered)

    def test_child_environment_cannot_omit_or_replace_expected_values(self):
        environment = guard.make_environment(self.layout(), self.inherited)
        guard.verify_inherited_environment(environment, dict(environment))
        for mode in ("missing", "replaced"):
            altered = dict(environment)
            if mode == "missing":
                del altered["XPEDITION_CLI_CONFIG_DIR"]
            else:
                altered["XPEDITION_CLI_CONFIG_DIR"] = str(self.profile)
            with self.subTest(mode=mode), self.assertRaises(guard.IsolationError):
                guard.verify_inherited_environment(environment, altered)

    @unittest.skipIf(os.name == "nt", "Linux locale boundary")
    def test_linux_locale_is_fixed_not_inherited_and_missing_or_changed_is_refused(self):
        layout = self.layout()
        inherited = dict(self.inherited, LANG="untrusted", LC_ALL="C", LC_CTYPE="untrusted")
        environment = guard.make_environment(layout, inherited)
        for key in ("LANG", "LC_ALL", "LC_CTYPE"):
            self.assertEqual(environment[key], "C.UTF-8")
            for mode in ("missing", "changed"):
                altered = dict(environment)
                if mode == "missing":
                    del altered[key]
                else:
                    altered[key] = "C"
                with self.subTest(key=key, mode=mode):
                    with self.assertRaisesRegex(guard.IsolationError, "fixed_linux_locale"):
                        guard.verify_environment(layout, altered)
                    with self.assertRaisesRegex(guard.IsolationError, "child_environment_changed"):
                        guard.verify_inherited_environment(environment, altered)

    @unittest.skipIf(os.name == "nt", "Linux locale boundary")
    def test_linux_real_child_preserves_complete_environment_without_product_import(self):
        layout = self.layout()
        environment = guard.make_environment(layout, self.inherited)
        guard._create_run_directories(layout)
        guard._probe_child_inheritance(layout, environment)
        self.assertNotIn("pytest", sys.modules)
        self.assertFalse(any(name.startswith("xpedition_cli") for name in sys.modules))

    @unittest.skipIf(os.name == "nt", "Linux locale boundary")
    def test_unsupported_child_locale_stops_before_pytest(self):
        layout = self.layout()
        environment = guard.make_environment(layout, self.inherited)
        with (
            mock.patch.object(guard, "make_layout", return_value=layout),
            mock.patch.object(guard, "make_environment", return_value=environment),
            mock.patch.object(
                guard.subprocess,
                "run",
                side_effect=guard.subprocess.CalledProcessError(1, "locale-preflight"),
            ),
            mock.patch.object(guard, "_run_pytest") as run_pytest,
        ):
            result = guard.main(
                ["--run-root", str(self.run), "--externally-verified-isolation", "test_sample.py"]
            )
        self.assertEqual(result, 2)
        run_pytest.assert_not_called()

    def test_symlink_run_root_is_refused_when_platform_supports_it(self):
        alias = self.runs / "alias"
        try:
            alias.symlink_to(self.run, target_is_directory=True)
        except OSError as error:
            self.skipTest("test sandbox cannot create a disposable symlink: " + str(error))
        with self.assertRaisesRegex(guard.IsolationError, "symlink_or_reparse_point"):
            guard.make_layout(self.repo, alias, ("test_sample.py",), self.inherited)

    def test_missing_external_attestation_cannot_launch_or_create_directories(self):
        self.assertTrue(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)
        self.assertNotIn("pytest", sys.modules)
        self.assertFalse(any(name.startswith("xpedition_cli") for name in sys.modules))
        with self.assertRaisesRegex(guard.IsolationError, "external_os_isolation_attestation"):
            guard.require_external_isolation_attestation(False)
        layout = self.layout()
        environment = guard.make_environment(layout, self.inherited)
        with (
            mock.patch.object(guard, "make_layout", return_value=layout),
            mock.patch.object(guard, "make_environment", return_value=environment),
            mock.patch.object(guard, "_create_run_directories") as create,
            mock.patch.object(guard, "_run_pytest") as run_pytest,
        ):
            self.assertEqual(guard.main(["--run-root", str(self.run), "test_sample.py"]), 2)
        create.assert_not_called()
        run_pytest.assert_not_called()

    def test_attestation_enables_only_the_mocked_launcher_after_preflight(self):
        self.assertTrue(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)
        layout = self.layout()
        environment = guard.make_environment(layout, self.inherited)
        with (
            mock.patch.object(guard, "make_layout", return_value=layout),
            mock.patch.object(guard, "make_environment", return_value=environment),
            mock.patch.object(guard, "_create_run_directories") as create,
            mock.patch.object(guard, "_probe_child_inheritance") as probe,
            mock.patch.object(guard, "_run_pytest", return_value=0) as run_pytest,
        ):
            result = guard.main(
                ["--run-root", str(self.run), "--externally-verified-isolation", "test_sample.py"]
            )
        self.assertEqual(result, 0)
        create.assert_called_once_with(layout)
        probe.assert_called_once_with(layout, environment)
        run_pytest.assert_called_once_with(layout, environment)


if __name__ == "__main__":
    unittest.main()
