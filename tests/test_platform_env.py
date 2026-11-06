"""The install, its environment and its command-line programs (adapter/platform/env.py).

Every test runs against a fake registry and a fake install in a temporary folder; nothing
reads the real registry or starts a program.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xpedition_cli._driver import AdapterError
from xpedition_cli.adapter.platform import dialogs, env


class FakeKey:
    def __init__(self, hive: str, path: str) -> None:
        self.hive, self.path = hive, path

    def __enter__(self) -> FakeKey:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeWinreg:
    """``winreg`` over a dict: ``{(hive, path): {"values": {name: value}, "subkeys": [names]}}``.
    A value is a string (REG_SZ), a ``(value, kind)`` pair, or an OSError to raise."""

    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CLASSES_ROOT = "HKCR"
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(self, keys: dict | None = None) -> None:
        self.keys = keys or {}

    def _node(self, key: FakeKey) -> dict:
        return self.keys[(key.hive, key.path)]

    def OpenKey(self, parent, sub):  # noqa: N802 - winreg's spelling
        hive, path = (parent.hive, f"{parent.path}\\{sub}") if isinstance(parent, FakeKey) else (parent, sub)
        if (hive, path) not in self.keys:
            raise FileNotFoundError(path)
        return FakeKey(hive, path)

    def QueryValueEx(self, key, name):  # noqa: N802
        values = self._node(key).get("values", {})
        if name not in values:
            raise FileNotFoundError(name)
        value = values[name]
        if isinstance(value, OSError):
            raise value
        return value if isinstance(value, tuple) else (value, self.REG_SZ)

    def QueryInfoKey(self, key):  # noqa: N802
        node = self._node(key)
        return len(node.get("subkeys", [])), len(node.get("values", {})), 0

    def EnumKey(self, key, index):  # noqa: N802
        subkeys = self._node(key).get("subkeys", [])
        if index >= len(subkeys):
            raise OSError("no more keys")
        return subkeys[index]


USER = ("HKCU", env.USER_ENVIRONMENT)
MACHINE = ("HKLM", env.MACHINE_ENVIRONMENT)


def environment_keys(user: dict | None = None, machine: dict | None = None) -> dict:
    keys = {}
    if user is not None:
        keys[USER] = {"values": user}
    if machine is not None:
        keys[MACHINE] = {"values": machine}
    return keys


class EnvCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()  # long folder names, as the tool reports them
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.registry({})  # never the real registry
        self.environ()

    def environ(self, **values: str) -> None:
        patcher = mock.patch.dict(os.environ, values, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def registry(self, keys: dict) -> FakeWinreg:
        fake = FakeWinreg(keys)
        patcher = mock.patch.dict(sys.modules, {"winreg": fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def install(self, release: str = "XPED2604", programs: tuple[str, ...] = ("ExpeditionPCB.exe", "viewdraw.exe")) -> Path:
        home = self.tmp / release / "SDD_HOME"
        for folder in ("common/win64/bin", "common/win64/lib", "wg/win64/bin", "standard"):
            (home / folder).mkdir(parents=True, exist_ok=True)
        for name in programs:
            (home / "common" / "win64" / "bin" / name).write_bytes(b"launcher stub")
        (home / "wg" / "win64" / "bin" / "ExpeditionPCB.exe").write_bytes(b"the real program, never started directly")
        return home.resolve()


class FindingTheInstall(EnvCase):
    def test_an_explicit_sdd_home_is_used(self) -> None:
        home = self.install()
        self.environ(XPEDITION_SDD_HOME=str(home))
        self.assertEqual(env.sdd_home(), home)

    def test_a_folder_without_the_layout_program_is_skipped_for_the_release_registry(self) -> None:
        home = self.install("XENTP2604")
        empty = self.tmp / "Other" / "SDD_HOME"
        empty.mkdir(parents=True)
        self.environ(SDD_HOME=str(empty))
        self.registry({
            ("HKLM", env.RELEASES): {"subkeys": ["MGC_1", "MGC_2"]},
            ("HKLM", env.RELEASES + "\\MGC_1"): {"values": {"SDD_HOME": str(self.tmp / "gone")}},
            ("HKLM", env.RELEASES + "\\MGC_2"): {"values": {"SDD_HOME": str(home)}},
        })
        self.assertEqual(env.sdd_home(), home)

    def test_the_users_environment_settings_are_read(self) -> None:
        home = self.install()
        self.registry(environment_keys(user={"SDD_HOME": str(home)}))
        self.assertEqual(env.sdd_home(), home)

    def test_a_release_beside_the_licence_file_is_found(self) -> None:
        home = self.install()
        licence = self.tmp / "license.dat"
        licence.write_text("synthetic; not a licence", encoding="utf-8")
        self.environ(MGLS_LICENSE_FILE=str(licence))
        self.assertEqual(env.sdd_home(), home)

    def test_a_licence_server_is_not_read_as_a_folder(self) -> None:
        self.environ(MGLS_LICENSE_FILE="28000@synthetic-host")
        self.assertIsNone(env.find_sdd_home())

    def test_no_install_is_backend_unavailable_and_nothing_was_sent(self) -> None:
        with self.assertRaises(AdapterError) as caught:
            env.sdd_home()
        self.assertEqual(caught.exception.data["code"], "E_BACKEND_UNAVAILABLE")
        self.assertEqual(caught.exception.data["dispatch_state"], "not_started")
        self.assertIn("ExpeditionPCB.exe", str(caught.exception))

    def test_release_and_version_come_from_the_release_folder(self) -> None:
        for release, number in (("XPED2604", "2604"), ("XENTP2604", "2604")):
            with self.subTest(release=release):
                self.environ(XPEDITION_SDD_HOME=str(self.install(release)))
                self.assertEqual((env.release(), env.version()), (release, number))

    def test_the_version_falls_back_to_the_install_record(self) -> None:
        home = self.install("Release")
        (home.parent / "InstallVersion.txt").write_text("Install Id: 1\nFlow Id: Xpedition Enterprise 2604\n", encoding="utf-8")
        self.environ(XPEDITION_SDD_HOME=str(home))
        self.assertEqual(env.version(), "2604")
        (home.parent / "InstallVersion.txt").unlink()
        self.assertEqual(env.version(), "")


class Launchers(EnvCase):
    """Both applications and every command-line program start through common/win64/bin: starting
    the real program under wg or wv skips the release setup and exits with STATUS_DLL_NOT_FOUND."""

    def setUp(self) -> None:
        super().setUp()
        self.home = self.install(programs=("ExpeditionPCB.exe", "viewdraw.exe", "LibraryManager.exe"))
        self.environ(XPEDITION_SDD_HOME=str(self.home))

    def test_every_program_starts_through_the_common_launcher(self) -> None:
        for name in ("viewdraw", "ExpeditionPCB", "LibraryManager.exe"):
            with self.subTest(name=name):
                path = env.launcher(name)
                self.assertEqual(path.parent, self.home / "common" / "win64" / "bin")
                self.assertNotIn("wg", path.parts)
                self.assertNotIn("wv", path.parts)

    def test_a_missing_launcher_names_the_program(self) -> None:
        with self.assertRaises(AdapterError) as caught:
            env.launcher("sch2pdf")
        self.assertEqual(caught.exception.data["code"], "E_BACKEND_UNAVAILABLE")
        self.assertIn("sch2pdf.exe", str(caught.exception))

    def test_a_name_with_a_path_is_refused(self) -> None:
        for name in ("..\\wg\\win64\\bin\\ExpeditionPCB", "C:/x", ""):
            with self.subTest(name=name), self.assertRaises(AdapterError) as caught:
                env.launcher(name)
            self.assertEqual(caught.exception.data["code"], "E_VALIDATION")


class Environment(EnvCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.install()
        self.base = {"XPEDITION_SDD_HOME": str(self.home), "XPEDITION_MGC_WD": str(self.tmp / "work"),
                     "PATH": os.pathsep.join(["C:\\Windows", ""])}

    def configure(self, registered: dict | None = None, **initial: str) -> dict[str, str]:
        user = {k[1]: v for k, v in (registered or {}).items() if k[0] == "user"}
        machine = {k[1]: v for k, v in (registered or {}).items() if k[0] == "machine"}
        self.registry(environment_keys(user=user, machine=machine))
        self.environ(**{**self.base, **initial})
        env.configure_environment()
        return dict(os.environ)

    def test_the_release_variables_are_set(self) -> None:
        (self.home.parent / "MGC_HOME.ixw").mkdir()
        values = self.configure()
        self.assertEqual(values["SDD_HOME"], str(self.home))
        self.assertEqual(values["SDD_PLATFORM"], "win64")
        self.assertEqual(values["SDD_VERSION"], "XPED2604")
        self.assertEqual(values["MGC_HOME"], str(self.home.parent / "MGC_HOME.ixw"))
        self.assertEqual(values["MGC_WD"], str(self.tmp / "work"))
        self.assertTrue((self.tmp / "work").is_dir())
        self.assertEqual(values["WDIR"].split(os.pathsep), [str(self.tmp / "work"), str(self.home / "standard")])
        path = values["PATH"].split(os.pathsep)
        self.assertEqual(path[:3], [str(self.home / "common/win64/bin"), str(self.home / "common/win64/lib"),
                                    str(self.home / "wg/win64/bin")])
        self.assertIn("C:\\Windows", path)

    def test_it_is_idempotent(self) -> None:
        first = self.configure()
        env.configure_environment()
        self.assertEqual(dict(os.environ), first)

    def test_explicit_values_are_kept_but_sdd_home_follows_the_install(self) -> None:
        values = self.configure(SDD_PLATFORM="win64-explicit", SDD_HOME=str(self.tmp / "stale"), MGC_WD=str(self.tmp / "own"))
        self.assertEqual(values["SDD_PLATFORM"], "win64-explicit")
        self.assertEqual(values["SDD_HOME"], str(self.home))
        self.assertEqual(values["MGC_WD"], str(self.tmp / "own"))

    # -- licence settings: never invented, never printed, the person's explicit values kept

    def test_existing_user_licence_settings_reach_the_programs(self) -> None:
        values = self.configure({
            ("user", "MGLS_LICENSE_FILE"): "28000@synthetic-user-host",
            ("user", "SALT_LICENSE_SERVER"): "29000@synthetic-user-host",
            ("machine", "SALT_LICENSE_SERVER"): "29000@synthetic-machine-host",
        })
        self.assertEqual(values["MGLS_LICENSE_FILE"], "28000@synthetic-user-host")
        self.assertEqual(values["SALT_LICENSE_SERVER"], "29000@synthetic-user-host")

    def test_the_machine_setting_is_used_when_the_user_has_none(self) -> None:
        values = self.configure({("machine", "SALT_LICENSE_SERVER"): "29000@synthetic-host"})
        self.assertEqual(values["SALT_LICENSE_SERVER"], "29000@synthetic-host")

    def test_an_explicit_process_value_is_kept_even_empty(self) -> None:
        for configured in ("28000@explicit-process-host", ""):
            with self.subTest(configured=configured):
                values = self.configure({("user", "MGLS_LICENSE_FILE"): "28000@synthetic-user-host"},
                                        MGLS_LICENSE_FILE=configured)
                self.assertEqual(values["MGLS_LICENSE_FILE"], configured)

    def test_an_unreadable_setting_is_not_invented_or_printed(self) -> None:
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err), mock.patch.object(sys, "stdout", io.StringIO()) as out:
            values = self.configure({("user", "SALT_LICENSE_SERVER"): PermissionError("synthetic private diagnostic")})
        self.assertNotIn("SALT_LICENSE_SERVER", values)
        self.assertEqual((err.getvalue(), out.getvalue()), ("", ""))

    def test_the_licence_file_fallback_does_not_replace_a_configured_source(self) -> None:
        (self.tmp / "LICENSE.DAT").write_text("synthetic fallback; not a licence", encoding="utf-8")
        values = self.configure({("user", "MGLS_LICENSE_FILE"): "28000@synthetic-user-host"})
        self.assertEqual(values["MGLS_LICENSE_FILE"], "28000@synthetic-user-host")

    def test_the_licence_file_beside_the_install_is_the_last_resort(self) -> None:
        (self.tmp / "LICENSE.DAT").write_text("synthetic fallback; not a licence", encoding="utf-8")
        self.assertEqual(Path(self.configure()["MGLS_LICENSE_FILE"]), self.tmp / "LICENSE.DAT")

    def test_an_expandable_setting_sees_a_licence_setting_not_yet_loaded(self) -> None:
        for dependency in ("LM_LICENSE_FILE", "SALT_LICENSE_SERVER"):
            with self.subTest(dependency=dependency):
                values = self.configure({("user", "MGLS_LICENSE_FILE"): (f"%{dependency}%", 2),
                                         ("user", dependency): "28000@synthetic-configured-host"})
                self.assertEqual(values["MGLS_LICENSE_FILE"], "28000@synthetic-configured-host")

    def test_references_resolve_transitively_and_in_any_case(self) -> None:
        values = self.configure({
            ("user", "MGLS_LICENSE_FILE"): ("%lm_license_file%", 2),
            ("user", "LM_LICENSE_FILE"): ("%salt_license_server%", 2),
            ("machine", "SALT_LICENSE_SERVER"): "28000@synthetic-configured-host",
        })
        self.assertEqual(values["MGLS_LICENSE_FILE"], "28000@synthetic-configured-host")

    def test_a_plain_string_stays_literal(self) -> None:
        values = self.configure({("user", "MGLS_LICENSE_FILE"): "%LM_LICENSE_FILE%"}, LM_LICENSE_FILE="28000@explicit-host")
        self.assertEqual(values["MGLS_LICENSE_FILE"], "%LM_LICENSE_FILE%")

    def test_an_explicit_dependency_wins_even_empty(self) -> None:
        for value in ("28000@explicit-host", ""):
            with self.subTest(value=value):
                values = self.configure({("user", "MGLS_LICENSE_FILE"): ("%LM_LICENSE_FILE%", 2),
                                         ("user", "LM_LICENSE_FILE"): "28000@registry-host"}, LM_LICENSE_FILE=value)
                self.assertEqual((values["MGLS_LICENSE_FILE"], values["LM_LICENSE_FILE"]), (value, value))

    def test_process_variables_can_be_referenced(self) -> None:
        values = self.configure({("user", "MGLS_LICENSE_FILE"): ("%licence_root%/synthetic.lic", 2)}, LICENCE_ROOT="D:/synthetic")
        self.assertEqual(values["MGLS_LICENSE_FILE"], "D:/synthetic/synthetic.lic")

    def test_unresolvable_references_stop_without_values_or_a_partial_environment(self) -> None:
        cases = (
            ({("user", "MGLS_LICENSE_FILE"): ("private-prefix/%PRIVATE_MISSING_NAME%", 2)}, "unresolved_reference"),
            ({("user", "MGLS_LICENSE_FILE"): ("%MGLS_LICENSE_FILE%", 2)}, "cyclic_reference"),
            ({("user", "MGLS_LICENSE_FILE"): ("%LM_LICENSE_FILE%", 2), ("user", "LM_LICENSE_FILE"): ("%MGLS_LICENSE_FILE%", 2)},
             "cyclic_reference"),
        )
        for registered, reason in cases:
            with self.subTest(reason=reason, registered=registered):
                registered = {**registered, ("user", "SALT_LICENSE_SERVER"): "private-host-value"}
                with self.assertRaises(AdapterError) as caught:
                    self.configure(registered)
                data = caught.exception.data
                self.assertEqual((data["code"], data["reason"]), ("E_CONFIG", reason))
                self.assertIn(data["setting"], {"MGLS_LICENSE_FILE", "LM_LICENSE_FILE"})
                shown = str(caught.exception) + repr(data)
                for secret in ("private-prefix", "PRIVATE_MISSING_NAME", "private-host-value"):
                    self.assertNotIn(secret, shown)
                self.assertNotIn("MGLS_LICENSE_FILE", os.environ)
                self.assertNotIn("SALT_LICENSE_SERVER", os.environ)


class Probe(EnvCase):
    def test_a_found_install_reports_only_the_context_keys(self) -> None:
        home = self.install()
        self.environ(XPEDITION_SDD_HOME=str(home), SALT_LICENSE_SERVER="29000@synthetic-host")
        result = env.probe()
        self.assertEqual(result, {"found": True, "path": str(home), "version": "2604", "license": "unknown"})
        self.assertNotIn("synthetic-host", repr(result))

    def test_no_install_is_not_found(self) -> None:
        result = env.probe()
        self.assertFalse(result["found"])
        self.assertEqual(set(result), {"found", "path", "version", "license"})

    def test_the_licence_is_missing_only_when_no_source_is_configured(self) -> None:
        self.environ(XPEDITION_SDD_HOME=str(self.install()))
        self.assertEqual(env.probe()["license"], "missing")
        self.registry(environment_keys(machine={"LM_LICENSE_FILE": "28000@synthetic-host"}))
        self.assertEqual(env.probe()["license"], "unknown")

    def test_the_licence_file_beside_the_install_counts_as_a_source(self) -> None:
        self.environ(XPEDITION_SDD_HOME=str(self.install()))
        (self.tmp / "LICENSE.DAT").write_text("synthetic; not a licence", encoding="utf-8")
        self.assertEqual(env.probe()["license"], "unknown")

    def test_probe_starts_nothing(self) -> None:
        self.environ(XPEDITION_SDD_HOME=str(self.install()))
        with mock.patch.object(env.subprocess, "Popen", side_effect=AssertionError("probe started a program")):
            env.probe()


class Logs(unittest.TestCase):
    def test_output_is_read_as_utf8_first_and_never_fails(self) -> None:
        self.assertEqual(env.tool_text("警告: 无效的值".encode()), "警告: 无效的值")
        self.assertEqual(env.tool_text(b"\xef\xbb\xbfDone.\r\n"), "Done.\r\n")
        # a Chinese code page's bytes: whatever this machine's code page makes of them, the reader
        # returns text and keeps the ASCII around them
        self.assertIn("ERROR: stop", env.tool_text("警告".encode("gbk") + b" ERROR: stop\r\n"))

    def test_only_a_leading_or_labelled_error_word_counts(self) -> None:
        text = "\n".join([
            "Checking for file format errors... none found",
            "Processing Part 'X'",
            "ERROR: cannot open the database",
            "  错误：无法打开数据库",
            "失败",
            '无法添加单元 "SOT23"',
            "遇到 2 个错误",
            "遇到 0 个错误",
            "3 errors were found",
            "0 errors found",
            "No errors.",
            "Unable to add cell \"QFN\"",
        ])
        self.assertEqual(env.tool_log_errors(text), [
            "ERROR: cannot open the database", "错误：无法打开数据库", "失败", '无法添加单元 "SOT23"', "遇到 2 个错误",
            "3 errors were found", 'Unable to add cell "QFN"',
        ])

    def test_warnings_in_a_log_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "parts.log"
            log.write_bytes('Processing Part \'X\'\n\t警告: 无效的值 "3.3V" (对于特性 "Value")。\n'.encode())
            summary = env.read_tool_log(log)
            self.assertEqual((summary["errors"], len(summary["warnings"])), ([], 1))
            log.write_bytes(b"Processing Part 'X'\r\n\tWarning: invalid value \"3.3V\" (Value).\r\n")
            self.assertEqual(len(env.read_tool_log(log)["warnings"]), 1)
            self.assertEqual(env.read_tool_log(Path(folder) / "missing.log")["errors"], [])


class FakePopen:
    """A started program: exits after ``polls`` polls (never when None)."""

    started: list[FakePopen] = []
    polls: int | None = 1
    exit_code = 0
    output = b""

    def __init__(self, command, **kwargs) -> None:
        self.command, self.kwargs = command, kwargs
        self.pid = 4100
        self.stdout = io.BytesIO(type(self).output)
        self.stderr = io.BytesIO(b"")
        self.count = 0
        self.returncode = None
        FakePopen.started.append(self)

    def poll(self):
        self.count += 1
        limit = type(self).polls
        if self.returncode is None and limit is not None and self.count >= limit:
            self.returncode = type(self).exit_code
        return self.returncode

    def kill(self) -> None:
        self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode


class RunTool(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()  # long folder names, as the tool reports them
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.program = self.tmp / "bin" / "PartsDB2HKP.exe"
        self.program.parent.mkdir()
        self.program.write_bytes(b"launcher stub")
        self.dialogs: dict[int, list[dialogs.Dialog]] = {}
        self.licence: dict[int, list[str]] = {}
        self.dismissed: list[int] = []
        self.killed: list[int] = []
        FakePopen.started, FakePopen.polls, FakePopen.exit_code, FakePopen.output = [], 1, 0, b""
        for target, name, value in (
            (env, "configure_environment", lambda: None),
            (env, "launcher", lambda name: self.program),
            (env, "TOOL_POLL_S", 0.001),
            (env.subprocess, "Popen", FakePopen),
            (env.processes, "descendants", lambda pid: [4200]),  # the launcher's real program
            (env.dialogs, "find_dialogs", lambda pid=None: list(self.dialogs.get(pid, []))),
            (env.dialogs, "licence_windows", lambda pid: list(self.licence.get(pid, []))),
            (env.dialogs, "dismiss", lambda dialog: self.dismissed.append(dialog.hwnd) or True),
            (env, "_kill_tree", lambda process: self.killed.append(process.pid) or process.kill()),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_program_runs_from_the_launcher_folder_with_its_arguments(self) -> None:
        FakePopen.output = "Exported 3 parts\r\n".encode()
        run = env.run_tool("PartsDB2HKP", ["-o", "D:/work/demo/parts.hkp"])
        started = FakePopen.started[0]
        self.assertEqual(started.command, [str(self.program), "-o", "D:/work/demo/parts.hkp"])
        self.assertEqual(started.kwargs["cwd"], str(self.program.parent))
        self.assertEqual((run.exit_code, run.timed_out, run.blocked_by_license), (0, False, False))
        self.assertEqual(run.stdout, "Exported 3 parts\r\n")

    def test_dialogs_of_the_launchers_child_are_dismissed_and_reported_once(self) -> None:
        FakePopen.polls = 3
        box = dialogs.Dialog(hwnd=11, pid=4200, process="partsdb2hkp.exe", title="PartsDB2HKP", text="Invalid argument -x",
                             buttons=["OK"])
        self.dialogs[4200] = [box]
        run = env.run_tool("PartsDB2HKP", ["-x"])
        self.assertEqual(run.dialogs, [{"title": "PartsDB2HKP", "text": "Invalid argument -x", "dismissed": True}])
        self.assertIn(11, self.dismissed)

    def test_a_licence_dialog_is_never_dismissed_and_blocks(self) -> None:
        self.dialogs[4100] = [dialogs.Dialog(hwnd=12, pid=4100, process="partsdb2hkp.exe", title="License Error",
                                             text="", buttons=[])]
        run = env.run_tool("PartsDB2HKP", [])
        self.assertTrue(run.blocked_by_license)
        self.assertEqual(self.dismissed, [])
        self.assertEqual(run.dialogs, [{"title": "License Error", "text": "", "dismissed": False}])

    def test_a_sign_in_window_blocks_without_being_a_dialog(self) -> None:
        self.licence[4200] = ["Siemens 登录"]
        self.assertTrue(env.run_tool("PartsDB2HKP", []).blocked_by_license)

    def test_a_program_that_runs_out_of_time_is_ended_with_its_children(self) -> None:
        FakePopen.polls = None
        run = env.run_tool("PartsDB2HKP", [], timeout_s=0.01)
        self.assertTrue(run.timed_out)
        self.assertEqual(self.killed, [4100])
        self.assertEqual(run.exit_code, 1)

    def test_the_log_is_read_in_the_code_page(self) -> None:
        log = self.tmp / "export.log"
        log.write_bytes("错误: 无法打开\r\n".encode())
        run = env.run_tool("PartsDB2HKP", [], log=log)
        self.assertEqual(env.tool_log_errors(run.log_text or ""), ["错误: 无法打开"])
        self.assertIsNone(env.run_tool("PartsDB2HKP", [], log=self.tmp / "none.log").log_text)

    def test_a_program_that_cannot_start_was_not_started(self) -> None:
        with mock.patch.object(env.subprocess, "Popen", side_effect=OSError("synthetic")):
            with self.assertRaises(AdapterError) as caught:
                env.run_tool("PartsDB2HKP", [])
        self.assertEqual((caught.exception.data["code"], caught.exception.data["dispatch_state"]),
                         ("E_NATIVE_FAILURE", "not_started"))


if __name__ == "__main__":
    unittest.main()
