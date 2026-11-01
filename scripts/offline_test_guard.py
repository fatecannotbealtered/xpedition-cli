"""Prepare an isolated pytest launch without importing pytest or the product.

Invoke the guard with ``python -I -S -B``.  The caller must first supply an
empty run directory directly below ``<task>/xpedition-offline-runs``.  Every
declared writable path is derived from that directory.  The normal repository
``tests/conftest.py`` remains a second layer, not the startup boundary.

IMPORTANT: path and environment checks are not an OS sandbox.  In particular,
test code can replace an environment, address absolute paths, or use COM.  The
caller must independently verify the surrounding operating-system isolation
before explicitly attesting it.  This script cannot verify that attestation.
``--check-only`` checks a plan and does not launch tests or verify the OS.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


class IsolationError(Exception):
    """A precondition for the isolated test launch was not met."""


@dataclass(frozen=True)
class Layout:
    repository: Path
    run: Path
    home: Path
    config: Path
    journal: Path
    appdata: Path
    localappdata: Path
    temp: Path
    work: Path
    pytest_base: Path
    logs: Path
    cache: Path
    xdg_config: Path
    blocked_native: Path
    tests: tuple[Path, ...]


def _lexical_path(value: str | os.PathLike[str]) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or "\x00" in raw or os.pathsep in raw:
        raise IsolationError("path_missing_or_ambiguous")
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise IsolationError("path_must_be_absolute_without_parent_traversal")
    if os.name == "nt":
        if path.drive.startswith("\\\\") or not re.fullmatch(r"[A-Za-z]:", path.drive):
            raise IsolationError("network_or_device_path_not_allowed")
        if ":" in raw[len(path.drive) :]:
            raise IsolationError("alternate_data_stream_not_allowed")
        for part in path.parts[1:]:
            if part.endswith((" ", ".")) or "~" in part or PureWindowsPath(part).is_reserved():
                raise IsolationError("windows_path_alias_not_allowed")
    elif raw.startswith("//"):
        raise IsolationError("network_path_not_allowed")
    return path


def _existing_path(value: str | os.PathLike[str], *, directory: bool) -> Path:
    """Reject links and reparse points on every existing path component."""
    path = _lexical_path(value)
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except OSError as error:
            raise IsolationError("path_missing_or_inaccessible") from error
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & reparse:
            raise IsolationError("symlink_or_reparse_point_not_allowed")
    if directory and not path.is_dir():
        raise IsolationError("expected_directory")
    if not directory:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IsolationError("expected_regular_file_with_one_link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise IsolationError("path_cannot_be_resolved") from error
    if os.path.normcase(os.path.normpath(str(path))) != os.path.normcase(str(resolved)):
        raise IsolationError("path_alias_not_allowed")
    return resolved


def _same_or_inside(child: Path, parent: Path) -> bool:
    """Compare Windows paths case-insensitively and reject cross-volume paths."""
    a = os.path.normcase(os.path.normpath(str(child)))
    b = os.path.normcase(os.path.normpath(str(parent)))
    try:
        return os.path.commonpath((a, b)) == b
    except ValueError:
        return False


def _profile_default(inherited: Mapping[str, str]) -> Path:
    profile = inherited.get("USERPROFILE") if os.name == "nt" else inherited.get("HOME")
    if not profile:
        raise IsolationError("original_user_profile_not_declared")
    # Deliberately lexical: do not open or modify the existing default root.
    return _lexical_path(profile) / ".xpedition-cli"


def make_layout(
    repository: str | os.PathLike[str],
    run_root: str | os.PathLike[str],
    selectors: Sequence[str],
    inherited: Mapping[str, str],
) -> Layout:
    repo = _existing_path(repository, directory=True)
    tests_dir = _existing_path(repo / "tests", directory=True)
    _existing_path(tests_dir / "conftest.py", directory=False)
    task_root = _existing_path(repo.parent, directory=True)
    allowed_parent = _existing_path(task_root / "xpedition-offline-runs", directory=True)
    run = _existing_path(run_root, directory=True)
    if run.parent != allowed_parent:
        raise IsolationError("run_root_must_be_a_direct_task_run")
    if _same_or_inside(run, repo) or _same_or_inside(repo, run):
        raise IsolationError("run_root_overlaps_repository")
    default = _profile_default(inherited)
    if _same_or_inside(run, default) or _same_or_inside(default, run):
        raise IsolationError("run_root_overlaps_default_config")
    if _same_or_inside(repo, default) or _same_or_inside(default, repo):
        raise IsolationError("repository_overlaps_default_config")
    try:
        if next(run.iterdir(), None) is not None:
            raise IsolationError("run_root_must_be_empty")
    except OSError as error:
        raise IsolationError("run_root_unreadable") from error

    if not selectors:
        raise IsolationError("no_test_modules_selected")
    selected = []
    for selector in selectors:
        # Do not allow command-line options, alternate roots, plugins, or a
        # caller-supplied SDK state directory through the pytest argument list.
        if not re.fullmatch(r"test_[A-Za-z0-9_]+\.py", selector):
            raise IsolationError("only_direct_test_module_names_are_allowed")
        selected.append(_existing_path(tests_dir / selector, directory=False))

    config = run / "config"
    return Layout(
        repository=repo,
        run=run,
        home=run / "home",
        config=config,
        journal=config / "driver-v2",
        appdata=run / "appdata",
        localappdata=run / "localappdata",
        temp=run / "temp",
        work=run / "work",
        pytest_base=run / "pytest",
        logs=run / "logs",
        cache=run / "cache",
        xdg_config=run / "xdg-config",
        blocked_native=run / "no-live-native-adapter.exe",
        tests=tuple(selected),
    )


def make_environment(layout: Layout, inherited: Mapping[str, str]) -> dict[str, str]:
    """Construct an allowlisted environment; never inherit tokens or SDK paths."""
    python_dir = str(Path(sys.executable).parent)
    environment = {
        "HOME": str(layout.home),
        "USERPROFILE": str(layout.home),
        "APPDATA": str(layout.appdata),
        "LOCALAPPDATA": str(layout.localappdata),
        "TEMP": str(layout.temp),
        "TMP": str(layout.temp),
        "TMPDIR": str(layout.temp),
        "XDG_CACHE_HOME": str(layout.cache),
        "XDG_CONFIG_HOME": str(layout.xdg_config),
        "MPLCONFIGDIR": str(layout.cache / "matplotlib"),
        "PIP_CACHE_DIR": str(layout.cache / "pip"),
        "XPEDITION_CLI_CONFIG_DIR": str(layout.config),
        "XPEDITION_NATIVE_COMMAND": str(layout.blocked_native),
        "PYTHONPATH": os.pathsep.join((str(layout.repository), str(layout.repository / "tests"))),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    if os.name == "nt":
        system_root = inherited.get("SystemRoot") or inherited.get("SYSTEMROOT")
        if not system_root or not Path(system_root).is_absolute():
            raise IsolationError("system_root_not_declared")
        drive, tail = os.path.splitdrive(str(layout.home))
        if not drive or not tail.startswith("\\"):
            raise IsolationError("isolated_home_is_not_drive_absolute")
        environment.update(
            SYSTEMROOT=system_root,
            WINDIR=system_root,
            HOMEDRIVE=drive,
            HOMEPATH=tail,
            PATHEXT=".COM;.EXE;.BAT;.CMD",
            PATH=os.pathsep.join((python_dir, str(Path(system_root) / "System32"))),
        )
    else:
        # Pin the Linux child locale rather than inheriting the user's locale.
        # Without this, CPython may inject LC_CTYPE while coercing the C locale,
        # correctly failing our exact child-environment comparison below.
        environment.update(
            HOMEDRIVE="",
            HOMEPATH=str(layout.home),
            PATH=python_dir,
            LANG="C.UTF-8",
            LC_ALL="C.UTF-8",
            LC_CTYPE="C.UTF-8",
        )
    verify_environment(layout, environment)
    return environment


def verify_environment(layout: Layout, environment: Mapping[str, str]) -> None:
    expected = {
        "HOME": layout.home,
        "USERPROFILE": layout.home,
        "APPDATA": layout.appdata,
        "LOCALAPPDATA": layout.localappdata,
        "TEMP": layout.temp,
        "TMP": layout.temp,
        "TMPDIR": layout.temp,
        "XDG_CACHE_HOME": layout.cache,
        "XDG_CONFIG_HOME": layout.xdg_config,
        "MPLCONFIGDIR": layout.cache / "matplotlib",
        "PIP_CACHE_DIR": layout.cache / "pip",
        "XPEDITION_CLI_CONFIG_DIR": layout.config,
        "XPEDITION_NATIVE_COMMAND": layout.blocked_native,
    }
    if not _same_or_inside(layout.journal, layout.config):
        raise IsolationError("journal_outside_config")
    for path in (
        layout.work,
        layout.pytest_base,
        layout.logs,
        layout.cache,
        layout.xdg_config,
        layout.journal,
    ):
        if path == layout.run or not _same_or_inside(path, layout.run):
            raise IsolationError("declared_write_path_outside_run")
    for key, path in expected.items():
        if environment.get(key) != str(path) or not _same_or_inside(path, layout.run):
            raise IsolationError("isolated_environment_mismatch:" + key)
    for key in (
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUTF8",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    ):
        if environment.get(key) != "1":
            raise IsolationError("python_or_pytest_isolation_missing:" + key)
    if environment.get("PYTHONPATH") != os.pathsep.join(
        (str(layout.repository), str(layout.repository / "tests"))
    ):
        raise IsolationError("python_import_path_changed")
    if "PYTEST_ADDOPTS" in environment or "PYTHONHOME" in environment:
        raise IsolationError("uncontrolled_interpreter_options")
    if os.name == "nt":
        drive, tail = os.path.splitdrive(str(layout.home))
        if environment.get("HOMEDRIVE") != drive or environment.get("HOMEPATH") != tail:
            raise IsolationError("windows_home_components_changed")
    elif environment.get("HOMEDRIVE") != "" or environment.get("HOMEPATH") != str(layout.home):
        raise IsolationError("home_components_changed")
    if os.name != "nt":
        for key in ("LANG", "LC_ALL", "LC_CTYPE"):
            if environment.get(key) != "C.UTF-8":
                raise IsolationError("fixed_linux_locale_missing_or_changed:" + key)


def verify_inherited_environment(expected: Mapping[str, str], actual: Mapping[str, str]) -> None:
    """Check the guard's first child before pytest; later children need OS isolation."""
    if dict(expected) != dict(actual):
        raise IsolationError("child_environment_changed")


def require_external_isolation_attestation(attested: bool) -> None:
    """Require the caller's independently verified boundary; this is not proof."""
    if attested is not True:
        raise IsolationError("external_os_isolation_attestation_required")


def _create_run_directories(layout: Layout) -> None:
    # The external execution boundary must already have been verified.
    for path in (
        layout.home,
        layout.config,
        layout.appdata,
        layout.localappdata,
        layout.temp,
        layout.work,
        layout.logs,
        layout.cache,
        layout.xdg_config,
    ):
        path.mkdir()
        _existing_path(path, directory=True)
    if layout.blocked_native.exists():
        raise IsolationError("native_blocker_path_exists")


def _probe_child_inheritance(layout: Layout, environment: Mapping[str, str]) -> None:
    verify_environment(layout, environment)
    code = "import json,os; print(json.dumps(dict(os.environ), sort_keys=True))"
    if os.name != "nt":
        # Fail before pytest if this host does not support the declared locale.
        code = "import locale; locale.setlocale(locale.LC_CTYPE, ''); " + code
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=layout.work,
        env=dict(environment),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30,
        check=True,
    )
    actual = json.loads(result.stdout)
    verify_inherited_environment(environment, actual)


def _run_pytest(layout: Layout, environment: Mapping[str, str]) -> int:
    """Fixed arguments prevent a caller from swapping the root or plugin list."""
    command = [
        sys.executable,
        "-B",
        "-s",
        "-m",
        "pytest",
        "--rootdir",
        str(layout.repository),
        "--confcutdir",
        str(layout.repository / "tests"),
        "--basetemp",
        str(layout.pytest_base),
        "-p",
        "no:cacheprovider",
        "--junitxml",
        str(layout.logs / "junit.xml"),
        *(str(path) for path in layout.tests),
    ]
    with (layout.logs / "stdout.txt").open("xb") as stdout:
        with (layout.logs / "stderr.txt").open("xb") as stderr:
            completed = subprocess.run(
                command, cwd=layout.work, env=dict(environment), stdout=stdout, stderr=stderr
            )
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--externally-verified-isolation",
        action="store_true",
        help="attest independent OS isolation; this guard cannot verify that assertion",
    )
    parser.add_argument("modules", nargs="+")
    args = parser.parse_args(argv)
    try:
        if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
            raise IsolationError("start_guard_with_python_I_S_B")
        if any(name == "pytest" or name.startswith("xpedition_cli") for name in sys.modules):
            raise IsolationError("product_or_pytest_already_imported")
        repository = Path(__file__).absolute().parent.parent
        layout = make_layout(repository, args.run_root, args.modules, os.environ)
        environment = make_environment(layout, os.environ)
        if args.check_only:
            print(
                json.dumps(
                    {"plan_valid": True, "os_isolation_verified": False, "tests_started": False}
                )
            )
            return 0
        require_external_isolation_attestation(args.externally_verified_isolation)
        _create_run_directories(layout)
        _probe_child_inheritance(layout, environment)
        return _run_pytest(layout, environment)
    except (IsolationError, OSError, subprocess.SubprocessError, ValueError) as error:
        print("offline test guard refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
