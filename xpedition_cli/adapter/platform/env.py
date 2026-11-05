"""The Xpedition install: SDD_HOME, its launchers and command-line programs, and the
environment they need.

Every Xpedition program is started through its launcher in ``SDD_HOME/common/win64/bin``.
The launchers are small stubs that set up the release environment and then start the real
program under ``wg``, ``wv`` and the other product folders. Starting the real program
directly fails (``STATUS_DLL_NOT_FOUND``), and so does COM activation
(``CO_E_SERVER_EXEC_FAILURE``). Because a launcher starts the real program as a child
process, the dialogs of a command-line program belong to that child, not to the launcher.

Licence settings are never printed, returned or put into an error message: only the names
of the variables appear.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..._driver import AdapterError
from . import dialogs, processes

BIN = ("common", "win64", "bin")
PCB_PROGRAM = "ExpeditionPCB.exe"  # the file that tells an SDD_HOME folder apart
PLATFORM = "win64"
LICENCE_VARIABLES = ("MGLS_LICENSE_FILE", "SALT_LICENSE_SERVER", "LM_LICENSE_FILE")
USER_ENVIRONMENT = "Environment"
MACHINE_ENVIRONMENT = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
RELEASES = r"Software\Mentor Graphics\Releases"  # the Release Switcher's list of installs
TOOL_TIMEOUT_S = 300.0
TOOL_POLL_S = 0.3
OUTPUT_LIMIT = 20000  # characters of a program's output kept, from the end
READER_WAIT_S = 5.0


# ------------------------------------------------------------------ the registry


def _winreg() -> Any:
    try:
        import winreg
    except ImportError:
        return None
    return winreg


def _registry_values(name: str) -> list[tuple[str, bool]]:
    """A variable of the person's Windows environment, from the user's settings, then the
    machine's: ``[(value, expandable)]``. Only string values count."""
    reg = _winreg()
    if reg is None:
        return []
    found: list[tuple[str, bool]] = []
    for hive, subkey in ((reg.HKEY_CURRENT_USER, USER_ENVIRONMENT), (reg.HKEY_LOCAL_MACHINE, MACHINE_ENVIRONMENT)):
        try:
            with reg.OpenKey(hive, subkey) as key:
                value, kind = reg.QueryValueEx(key, name)
        except OSError:
            continue
        if isinstance(value, str) and kind in (reg.REG_SZ, reg.REG_EXPAND_SZ):
            found.append((value, kind == reg.REG_EXPAND_SZ))
    return found


def registry_environment(name: str) -> tuple[str, bool] | None:
    """The person's setting of a variable, ``(value, expandable)``; the user's wins over the machine's."""
    values = _registry_values(name)
    return values[0] if values else None


def _release_homes() -> list[str]:
    """The SDD_HOME of every release the Release Switcher knows, user's first."""
    reg = _winreg()
    if reg is None:
        return []
    homes: list[str] = []
    for hive in (reg.HKEY_CURRENT_USER, reg.HKEY_LOCAL_MACHINE):
        try:
            with reg.OpenKey(hive, RELEASES) as releases:
                count = reg.QueryInfoKey(releases)[0]
                for index in range(count):
                    try:
                        name = reg.EnumKey(releases, index)
                        with reg.OpenKey(releases, name) as release:
                            value, _ = reg.QueryValueEx(release, "SDD_HOME")
                    except OSError:
                        continue
                    if isinstance(value, str) and value:
                        homes.append(value)
        except OSError:
            continue
    return homes


# ------------------------------------------------------------------ the install


def _licence_file_setting() -> str | None:
    value = os.environ.get("MGLS_LICENSE_FILE")
    if value is None:
        found = registry_environment("MGLS_LICENSE_FILE")
        value = found[0] if found else None
    return value


def _licence_parents(value: str | None) -> list[Path]:
    """The folders of the licence files a setting names; a ``port@host`` entry names a server."""
    parents: list[Path] = []
    for item in (value or "").split(";"):
        item = item.strip().strip('"')
        if not item or "@" in item:
            continue
        path = Path(os.path.expandvars(item)).expanduser()
        if path.is_absolute() and path.parent.is_dir():
            parents.append(path.parent)
    return parents


def _as_home(value: str | os.PathLike[str] | None) -> Path | None:
    if not value:
        return None
    try:
        path = Path(os.path.expandvars(str(value))).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    return path if path.joinpath(*BIN, PCB_PROGRAM).is_file() else None


def _candidates() -> list[str]:
    values: list[str] = [os.environ.get("XPEDITION_SDD_HOME") or "", os.environ.get("SDD_HOME") or ""]
    for name in ("XPEDITION_SDD_HOME", "SDD_HOME"):
        values.extend(value for value, _ in _registry_values(name))
    values.extend(_release_homes())
    for parent in _licence_parents(_licence_file_setting()):
        values.extend(str(item) for item in sorted(parent.glob("*/SDD_HOME")))
    return values


def find_sdd_home() -> Path | None:
    """The first SDD_HOME that holds ``common/win64/bin/ExpeditionPCB.exe``: from
    XPEDITION_SDD_HOME or SDD_HOME (process, then the person's settings), the release
    registry, or a release folder beside the licence file. None when there is none."""
    for value in _candidates():
        home = _as_home(value)
        if home is not None:
            return home
    return None


def sdd_home() -> Path:
    home = find_sdd_home()
    if home is None:
        raise AdapterError(
            "E_BACKEND_UNAVAILABLE",
            "Xpedition was not found: no SDD_HOME named by XPEDITION_SDD_HOME, SDD_HOME or the Mentor Graphics "
            "release registry holds common/win64/bin/ExpeditionPCB.exe.",
            dispatch_state="not_started")
    return home


def release() -> str:
    """The release folder that holds SDD_HOME, such as ``XPED2604`` or ``XENTP2604``."""
    return sdd_home().parent.name


def _version_of(home: Path) -> str:
    match = re.search(r"(\d{4})$", home.parent.name)
    if match:
        return match.group(1)
    try:
        text = (home.parent / "InstallVersion.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.lower().startswith("flow id:"):
            tokens = [t for t in line.split(":", 1)[1].split() if any(c.isdigit() for c in t)]
            if tokens:
                return tokens[-1]
    return ""


def version() -> str:
    """The release number, such as ``2604``; empty when the install does not tell it."""
    return _version_of(sdd_home())


# ------------------------------------------------------------------ the environment


def inherit_licence_environment() -> None:
    """Copy the licence variables from the person's Windows settings when the process lacks them.

    A long-running terminal or agent can predate the licence setup. A value the process has,
    even an empty one, is kept. ``REG_EXPAND_SZ`` values are expanded against the process
    environment and the other licence settings; the whole expansion is checked before any
    variable is set, and an error names the setting, never a value.
    """
    reg = _winreg()
    if reg is None:
        return
    effective = {name.upper(): value for name, value in os.environ.items()}
    configured: dict[str, tuple[str, bool]] = {}
    for name in LICENCE_VARIABLES:
        if name in effective:
            continue
        found = registry_environment(name)
        if found is not None:
            configured[name] = found
    visiting: set[str] = set()

    def resolve(name: str, setting: str) -> str:
        name = name.upper()
        if name in effective:
            return effective[name]
        reason = "cyclic_reference" if name in visiting else "unresolved_reference" if name not in configured else None
        if reason:
            raise AdapterError(
                "E_CONFIG", f"The licence setting {setting} cannot be expanded ({reason}); check that variable in the "
                "Windows environment settings.", dispatch_state="not_started", setting=setting, reason=reason)
        value, expandable = configured[name]
        visiting.add(name)
        try:
            if expandable:
                value = re.sub(r"%([^%]+)%", lambda match: resolve(match[1], name), value)
            effective[name] = value
            return value
        finally:
            visiting.discard(name)

    expanded = {name: resolve(name, name) for name in configured}
    for name, value in expanded.items():
        os.environ.setdefault(name, value)


def configure_environment() -> None:
    """Set the variables Xpedition's programs need in this process; calling it again changes nothing.

    SDD_HOME and SDD_VERSION follow the install found, so that the launcher and its
    environment agree; the other variables keep a value the process already has. The licence
    variables are copied from the person's settings only when missing.
    """
    inherit_licence_environment()
    home = sdd_home()
    root = home.parent
    env = os.environ
    env["SDD_HOME"] = str(home)
    env["SDD_VERSION"] = root.name
    env.setdefault("SDD_PLATFORM", PLATFORM)
    env.setdefault("SDD_WG", "wg")
    env.setdefault("SDD_WV", "wv")
    env.setdefault("SDD_DX", "dx")
    mgc_home = root / "MGC_HOME.ixw"
    if mgc_home.exists():
        env.setdefault("MGC_HOME", str(mgc_home))
        env.setdefault("SDD_MGC_HOME", str(mgc_home))
    env.setdefault("TARGET", str(root))
    env.setdefault("MENTOR_ROOT", str(root))
    workdir = Path(env.get("XPEDITION_MGC_WD") or env.get("MGC_WD") or Path.home() / ".xpedition" / root.name / "wdir").expanduser()
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    env.setdefault("MGC_WD", str(workdir))
    env.setdefault("WDIR", os.pathsep.join([str(workdir), str(home / "standard")]))
    env.setdefault("MGC_TMPDIR", env.get("TEMP") or str(workdir))
    env.setdefault("VCO", "ixw")
    if "MGLS_LICENSE_FILE" not in env:
        licence = root.parent / "LICENSE.DAT"
        if licence.is_file():
            env["MGLS_LICENSE_FILE"] = str(licence)
    folders = [str(home / product / PLATFORM / kind) for product in ("common", "wg") for kind in ("bin", "lib")]
    prefix = [folder for folder in folders if os.path.isdir(folder)]
    ours = {os.path.normcase(os.path.normpath(folder)) for folder in prefix}
    rest = [item for item in env.get("PATH", "").split(os.pathsep) if item and os.path.normcase(os.path.normpath(item)) not in ours]
    env["PATH"] = os.pathsep.join(prefix + rest)


def launcher(name: str) -> Path:
    """``SDD_HOME/common/win64/bin/<name>.exe``: how every Xpedition program must be started."""
    stem = name[:-4] if name.lower().endswith(".exe") else name
    if not stem or stem in (".", "..") or any(c in stem for c in '/\\:*?"<>|'):
        raise AdapterError("E_VALIDATION", f"{name!r} is not the name of an Xpedition program.", dispatch_state="not_started")
    path = sdd_home().joinpath(*BIN, f"{stem}.exe")
    if not path.is_file():
        raise AdapterError("E_BACKEND_UNAVAILABLE", f"This Xpedition install has no {stem}.exe in {path.parent}.",
                           dispatch_state="not_started")
    return path


# ------------------------------------------------------------------ command-line programs


@dataclass
class ToolRun:
    """What one run of a command-line program did. ``dialogs`` holds ``{title, text, dismissed}``
    for each dialog it showed; a licence or sign-in dialog is never dismissed and sets
    ``blocked_by_license``."""

    exit_code: int | None
    stdout: str
    stderr: str
    dialogs: list[dict[str, Any]] = field(default_factory=list)
    log_text: str | None = None
    timed_out: bool = False
    blocked_by_license: bool = False


def _reader(stream: Any, sink: list[bytes]) -> threading.Thread:
    def drain() -> None:
        try:
            for chunk in iter(lambda: stream.read(65536), b""):
                sink.append(chunk)
        except (OSError, ValueError):
            pass

    thread = threading.Thread(target=drain, name="xpedition-tool-output", daemon=True)
    thread.start()
    return thread


def _kill_tree(process: Any) -> None:
    """End a program that ran out of time, with the programs its launcher started."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def _watch_dialogs(pid: int, found: list[dict[str, Any]], seen: set[tuple[str, str]]) -> bool:
    """Dismiss the program's dialogs, except licence and sign-in ones; True when one of those is up."""
    blocked = False
    pids = [pid, *processes.descendants(pid)]
    for owner in pids:
        if dialogs.licence_windows(owner):
            blocked = True
        for dialog in dialogs.find_dialogs(owner):
            licence = dialogs.is_licence_window(f"{dialog.title} {dialog.text}")
            dismissed = False if licence else dialogs.dismiss(dialog)
            blocked = blocked or licence
            key = (dialog.title, dialog.text)
            if key not in seen:
                seen.add(key)
                found.append({"title": dialog.title, "text": dialog.text, "dismissed": dismissed})
    return blocked


def run_tool(name: str, args: list[str], *, cwd: str | os.PathLike[str] | None = None, timeout_s: float = TOOL_TIMEOUT_S,
             log: Path | None = None) -> ToolRun:
    """Run one of Xpedition's command-line programs through its launcher and wait for it.

    The programs are GUI-subsystem programs: a wrong argument or a failed licence check is a
    modal box, not an exit code. Every 0.3 s the boxes of the program and of the processes its
    launcher started are read and dismissed, each reported once in ``dialogs``. Licence and
    sign-in windows are left for the person and reported through ``blocked_by_license``. A
    program still running after ``timeout_s`` is ended with its children (``timed_out``).
    ``log`` names a log file the program writes; it is read when the program is done.
    """
    configure_environment()
    program = launcher(name)
    command = [str(program), *(str(arg) for arg in args)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(command, cwd=str(cwd or program.parent), env=dict(os.environ), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    except OSError as exc:
        raise AdapterError("E_NATIVE_FAILURE", f"{program.name} could not be started: {exc}", dispatch_state="not_started") from None
    out: list[bytes] = []
    err: list[bytes] = []
    readers = [_reader(process.stdout, out), _reader(process.stderr, err)]
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    blocked = timed_out = False
    started = time.monotonic()
    while True:
        code = process.poll()
        try:
            blocked = _watch_dialogs(process.pid, found, seen) or blocked
        except AdapterError:
            pass  # the process list could not be read this time; the next poll tries again
        if code is not None:
            break
        if time.monotonic() - started > timeout_s:
            timed_out = True
            _kill_tree(process)
            try:
                code = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                code = None
            break
        time.sleep(TOOL_POLL_S)
    for reader in readers:
        reader.join(READER_WAIT_S)  # a child that outlives the launcher can keep the pipes open
    log_text = None
    if log is not None:
        try:
            log_text = tool_text(Path(log).read_bytes())
        except OSError:
            log_text = None
    return ToolRun(exit_code=code, stdout=tool_text(b"".join(out))[-OUTPUT_LIMIT:], stderr=tool_text(b"".join(err))[-OUTPUT_LIMIT:],
                   dialogs=found, log_text=log_text, timed_out=timed_out, blocked_by_license=blocked)


# ------------------------------------------------------------------ their output and logs


def tool_text(raw: bytes) -> str:
    """A program's log or output as text.

    The programs write the system code page (``mbcs``, Windows only). UTF-8 is tried first:
    ASCII is UTF-8, and a Chinese code page practically never decodes as UTF-8, while ``mbcs``
    on an English machine turns UTF-8 bytes into mojibake without failing. Whatever is left is
    read as UTF-8 with the undecodable bytes replaced, so a log never makes a reader fail.
    """
    for encoding in ("utf-8-sig", "mbcs"):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


# The programs log in the interface language ("错误" is "error", "失败" "failed"). Their routine
# "checking for file format errors... none found" lines mention errors too, so only a line that
# starts with the word or carries it as a label counts, or one that reports a count above zero.
ERROR_LINE = re.compile(
    r"^\s*(?:error|错误|失败)|(?:error|错误)\s*[:：]|无法添加|遇到\s*[1-9]\d*\s*个错误|unable to add|cannot add|"
    r"\b[1-9]\d* errors? (?:were )?(?:found|encountered)", re.I)
# A warning is not a failure, but it can mean data changed on the way in: an invalid Value is
# stored as 0 ("无效的值 ... (对于特性 \"Value\")").
WARNING_LINE = re.compile(r"警告|warning", re.I)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def tool_log_errors(text: str) -> list[str]:
    """The error lines of a program's log or output."""
    return [line for line in _lines(text) if ERROR_LINE.search(line)]


def tool_log_warnings(text: str) -> list[str]:
    return [line for line in _lines(text) if WARNING_LINE.search(line)]


def read_tool_log(path: Path) -> dict[str, Any]:
    """A program's log: ``{path, errors, warnings, lines}`` (the first 40 warnings, the last 40 lines)."""
    try:
        text = tool_text(Path(path).read_bytes())
    except OSError:
        return {"path": str(path), "errors": [], "warnings": [], "lines": []}
    return {"path": str(path), "errors": tool_log_errors(text), "warnings": tool_log_warnings(text)[:40],
            "lines": _lines(text)[-40:]}


# ------------------------------------------------------------------ probe


def licence_state(home: Path | None = None) -> str:
    """``missing`` when no licence source is configured at all (none of the licence variables in
    the process or the person's settings, no LICENSE.DAT beside the install); otherwise
    ``unknown``: whether a licence is valid cannot be told without running Xpedition."""
    for name in LICENCE_VARIABLES:
        if os.environ.get(name) or any(value for value, _ in _registry_values(name)):
            return "unknown"
    if home is not None and (home.parent.parent / "LICENSE.DAT").is_file():
        return "unknown"
    return "missing"


def probe() -> dict[str, Any]:
    """The ``application`` object of ``context`` and ``doctor``. Reads the registry and the
    install folder only; starts nothing."""
    home = find_sdd_home()
    if home is None:
        return {"found": False, "path": "", "version": "", "license": licence_state(None)}
    return {"found": True, "path": str(home), "version": _version_of(home), "license": licence_state(home)}
