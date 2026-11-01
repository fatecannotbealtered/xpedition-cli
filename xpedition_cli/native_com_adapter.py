"""Small, opt-in bridge for the licensed Xpedition PCB COM API.

The CLI talks to this module through the NativeBackend JSON protocol.  The
bridge deliberately keeps COM objects inside this process: no COM object is
serialized and no shell command is constructed from request data.  It uses
the public automation ProgIDs and methods demonstrated by the Xpedition
installation's own examples.

This module is optional.  On non-Windows hosts, or before the Xpedition
automation classes have been registered, it returns a structured
``E_BACKEND_UNAVAILABLE`` response instead of pretending that native support
is available.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterable
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from .contract_gen import SCHEMA_VERSION
from .error_contract import CODES
from .errors import TRACE_RETURNED_WARNING
from .native_identity import adapter_identity, application_progids
from .native_identity import application_identity as _legacy_application_identity
from .native_process import processes

_BOUND_PROGIDS: dict[str, str] = {}
_OWNER_BOUNDARY_TRACE = ContextVar("designer_owner_boundary_trace", default=None)


@contextmanager
def _owner_boundary_trace(trace):
    """Private owner-supplied sink; no CLI path or request selector is used."""
    token = _OWNER_BOUNDARY_TRACE.set(trace)
    try:
        yield
    finally:
        _OWNER_BOUNDARY_TRACE.reset(token)


def _owner_application():
    from .designer_owner_dispatch import DispatchError, current_application

    try:
        return current_application()
    except DispatchError as error:
        raise AdapterError(
            "E_CONFLICT", "Dedicated owner context is invalid", {"reason": str(error)}
        ) from None


def application_identity(app, domain, **kwargs):
    from .designer_owner_dispatch import DispatchError
    from .designer_owner_dispatch import application_identity as owner_identity

    try:
        value = owner_identity(app, domain)
    except DispatchError as error:
        raise AdapterError(
            "E_CONFLICT", "Dedicated owner identity is invalid", {"reason": str(error)}
        ) from None
    return value if value is not None else _legacy_application_identity(app, domain, **kwargs)


def _check_bound_instance(app: Any, domain: str) -> None:
    expected = (
        os.environ.get("XPEDITION_EXPECTED_PID")
        if os.environ.get("XPEDITION_EXPECTED_DOMAIN") == domain
        else os.environ.get(f"XPEDITION_EXPECTED_{domain.upper()}_PID")
    )
    if expected:
        identity = application_identity(app, domain)
        if identity.get("pid") != int(expected):
            raise AdapterError(
                "E_CONFLICT",
                "The active native instance changed before dispatch.",
                {"requested_pid": int(expected), "application": identity},
            )


class AdapterError(Exception):
    """An error that can be represented by the NativeBackend protocol."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@contextmanager
def _native_step(name: str):
    """Opt-in, flushed boundary evidence; no arguments or native exception text."""
    owned = _OWNER_BOUNDARY_TRACE.get()
    if owned is not None:
        # CoUninitialize must still balance its CoInitialize if diagnostic I/O
        # fails. The sticky sink failure is checked by the outer dispatch.
        cleanup = name == "worker.CoUninitialize"
        try:
            owned.emit(name, "begin")
        except Exception as error:
            if getattr(owned, "failed", False):
                error.native_boundary_trace_incomplete = True
            if not cleanup:
                raise
        result = {}
        try:
            yield result
        except BaseException as error:
            try:
                owned.emit(
                    name,
                    "error",
                    error={
                        "type": type(error).__name__,
                        "code": getattr(error, "code", None),
                        "hresult": getattr(error, "hresult", None),
                    },
                )
            except Exception:
                if getattr(owned, "failed", False):
                    error.native_boundary_trace_incomplete = True
                pass  # Retain the primary native exception and its known facts.
            raise
        else:
            try:
                owned.emit(name, "end", **result)
            except Exception as error:
                error.native_call_returned = True
                error.native_result_summary = result.get("result")
                if getattr(owned, "failed", False):
                    error.native_boundary_trace_incomplete = True
                if not cleanup:
                    raise
        return
    filename = os.environ.get("XPEDITION_NATIVE_TRACE_FILE")
    result = {}
    if not filename:
        yield result
        return
    started = time.monotonic()
    identifier = f"{os.getpid()}-{uuid.uuid4().hex}"

    def emit(event, **fields):
        path = Path(filename).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "id": identifier,
            "pid": os.getpid(),
            "step": name,
            "event": event,
            "at": datetime.now(UTC).isoformat(),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            **fields,
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    begin_missing = False
    try:
        emit("begin")
    except OSError:
        # This boundary may be cleanup after a completed write. An optional
        # trace failure must not replace its result or prevent native cleanup.
        begin_missing = True
    try:
        yield result
    except BaseException as error:
        try:
            emit(
                "error",
                error={
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "hresult": getattr(error, "hresult", None),
                },
            )
        except OSError:
            # Preserve the primary CAD exception if writing its diagnostic fails.
            error.add_note("Native boundary trace could not record the exception")
        raise
    else:
        try:
            emit("end", **result, begin_missing=begin_missing)
            if begin_missing:
                sys.stderr.write("Native trace begin was not recorded; inspect the result.\n")
        except OSError:
            # The native call already returned. Diagnostic persistence must not
            # replace that known result or invite a retry of its side effects.
            try:
                sys.stderr.write(TRACE_RETURNED_WARNING + "\n")
                sys.stderr.flush()
            except OSError:
                pass


def _native_call(name: str, function):
    with _native_step(name) as evidence:
        value = function()
        if value is None or type(value) in (bool, int, float):
            summary = value
        elif isinstance(value, str):
            summary = {
                "type": "str",
                "length": len(value),
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
        elif isinstance(value, (list, tuple, dict)):
            summary = {"type": type(value).__name__, "count": len(value)}
        else:
            typename = type(value).__name__
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", typename):
                summary = {"type": typename}
            else:
                # ctypes/comtypes use names such as POINTER(GIT). These are
                # diagnostics, not interface identity. Preserve a digest while
                # retaining the sink's strict identifier/address restrictions.
                summary = {
                    "type": "OpaqueNativeObject",
                    "length": len(typename),
                    "sha256": hashlib.sha256(typename.encode("utf-8")).hexdigest(),
                }
        evidence["result"] = summary
        return value


def _response(
    data: dict[str, Any] | None = None, error: AdapterError | None = None
) -> dict[str, Any]:
    if error is not None:
        return {
            "ok": False,
            "schema_version": SCHEMA_VERSION,
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
                "retryable": bool(CODES.get(error.code, {}).get("retryable", False)),
            },
            "meta": {"duration_ms": 0},
        }
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "data": data or {},
        "meta": {"duration_ms": 0},
    }


def _heal_gen_py_cache(root: Path | None = None) -> list[str]:
    """Remove win32com gen_py entries whose generated module is gone.

    win32com caches each type library's generated wrapper under gen_py, which
    lives in %TEMP%. A temp cleaner that deletes the .py files but leaves
    __pycache__ turns an entry into an empty namespace package, and win32com
    then fails every GetActiveObject on that library with "has no attribute
    'CLSIDToClassMap'" -- which the attach probes read as "Designer is not
    running". An entry is only a cache, rebuilt on demand. One being generated
    right now has its .py files and no __pycache__ yet, so it is left alone.
    """
    if root is None:
        try:
            import win32com  # type: ignore[import-not-found]
        except ImportError:
            return []
        gen_path = str(getattr(win32com, "__gen_path__", "") or "")
        if not gen_path:
            return []
        root = Path(gen_path)
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    removed: list[str] = []
    for entry in entries:
        if (
            entry.is_dir()
            and entry.name != "__pycache__"
            and (entry / "__pycache__").is_dir()
            and not (entry / "__init__.py").exists()
        ):
            if entry.is_symlink() or not entry.resolve().is_relative_to(root.resolve()):
                continue
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                removed.append(entry.name)
    if removed:
        print(
            f"repaired the win32com type-library cache: removed {', '.join(removed)}",
            file=sys.stderr,
            flush=True,
        )
    return removed


def _import_com() -> tuple[Any, Any]:
    if os.name != "nt":
        raise AdapterError(
            "E_BACKEND_UNAVAILABLE",
            "Xpedition COM automation is available only on Windows",
            {"platform": os.name},
        )
    try:
        import pythoncom  # type: ignore[import-not-found]
        import win32com.client  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AdapterError(
            "E_BACKEND_UNAVAILABLE",
            "pywin32 is required for the native Xpedition adapter",
            {"install": "python -m pip install xpedition-cli[native]"},
        ) from exc
    return pythoncom, win32com.client


def _sdd_home() -> Path | None:
    values = [os.environ.get("XPEDITION_SDD_HOME"), os.environ.get("SDD_HOME")]
    license_file = os.environ.get("MGLS_LICENSE_FILE")
    if os.name == "nt":
        try:
            import winreg

            for hive, subkey in (
                (winreg.HKEY_CURRENT_USER, "Environment"),
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ),
            ):
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        for name in ("XPEDITION_SDD_HOME", "SDD_HOME", "MGLS_LICENSE_FILE"):
                            try:
                                value, _ = winreg.QueryValueEx(key, name)
                            except OSError:
                                continue
                            if name == "MGLS_LICENSE_FILE" and not license_file:
                                license_file = str(value)
                            elif name != "MGLS_LICENSE_FILE":
                                values.append(str(value))
                except OSError:
                    continue
            # Siemens/Mentor installs also publish the active release under
            # the Release Switcher registry key, without setting process
            # environment variables for every client application.
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"Software\Mentor Graphics\Releases") as releases:
                        count = winreg.QueryInfoKey(releases)[0]
                        for index in range(count):
                            try:
                                release_name = winreg.EnumKey(releases, index)
                                with winreg.OpenKey(releases, release_name) as release:
                                    value, _ = winreg.QueryValueEx(release, "SDD_HOME")
                                    values.append(str(value))
                            except OSError:
                                continue
                except OSError:
                    continue
        except ImportError:
            pass
    if license_file:
        license_parent = Path(license_file).expanduser().parent
        if license_parent.exists():
            values.extend(str(item) for item in license_parent.glob("*/SDD_HOME"))
    for value in values:
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if (path / "common" / "win64" / "bin" / "ExpeditionPCB.exe").is_file():
            return path
    return None


def _com_registered() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        for progid in application_progids("pcb"):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid):
                    return True
            except OSError:
                continue
    except ImportError:
        return False
    return False


def _viewdraw_registered() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        for progid in application_progids("schematic"):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid):
                    return True
            except OSError:
                continue
    except ImportError:
        return False
    return False


def _inherit_license_environment() -> None:
    """Fill missing process settings from the operator's Windows environment.

    A long-running terminal/agent can predate installation or license setup.
    Preserve explicit process values, including empty values. Read only the
    existing configuration; never create settings, inspect license files, or
    include server/file values in diagnostics.
    """
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return
    effective = {name.upper(): value for name, value in os.environ.items()}
    configured: dict[str, tuple[str, int]] = {}
    for name in ("MGLS_LICENSE_FILE", "SALT_LICENSE_SERVER", "LM_LICENSE_FILE"):
        if name in effective:
            continue
        for hive, subkey in (
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        ):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, kind = winreg.QueryValueEx(key, name)
                if not isinstance(value, str) or kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                    continue
            except OSError:
                continue
            configured[name] = (value, kind)
            break

    # Expand only after collecting all effective license settings. Windows'
    # ExpandEnvironmentStrings uses the caller's current environment, which may
    # not yet contain a referenced license setting read later in the loop.
    visiting: set[str] = set()

    def resolve(name: str, setting: str) -> str:
        name = name.upper()
        if name in effective:
            return effective[name]
        reason = (
            "cyclic_reference"
            if name in visiting
            else "unresolved_reference"
            if name not in configured
            else None
        )
        if reason:
            raise AdapterError(
                "E_CONFIG",
                "Cannot expand configured native license environment; inspect the named setting.",
                {"setting": setting, "reason": reason},
            )
        value, kind = configured[name]
        visiting.add(name)
        try:
            if kind == winreg.REG_EXPAND_SZ:
                value = re.sub(r"%([^%]+)%", lambda match: resolve(match[1], name), value)
            effective[name] = value
            return value
        finally:
            visiting.remove(name)

    # Validate the whole expansion before changing any process variable. Neither
    # unresolved names nor configured values are copied into an error message.
    expanded = {name: resolve(name, name) for name in configured}
    for name, value in expanded.items():
        os.environ.setdefault(name, value)


def _configure_environment() -> Path | None:
    _inherit_license_environment()
    sdd_home = _sdd_home()
    if sdd_home is None:
        return None
    os.environ.setdefault("SDD_HOME", str(sdd_home))
    os.environ.setdefault("SDD_PLATFORM", "win64")
    os.environ.setdefault("SDD_VERSION", sdd_home.parent.name)
    os.environ.setdefault("SDD_WG", "wg")
    os.environ.setdefault("SDD_WV", "wv")
    os.environ.setdefault("SDD_DX", "dx")
    root = sdd_home.parent
    mgc_home = root / "MGC_HOME.ixw"
    if mgc_home.exists():
        os.environ.setdefault("MGC_HOME", str(mgc_home))
        os.environ.setdefault("SDD_MGC_HOME", str(mgc_home))
    os.environ.setdefault("TARGET", str(root))
    os.environ.setdefault("MENTOR_ROOT", str(root))
    workdir = Path(
        os.environ.get("XPEDITION_MGC_WD")
        or os.environ.get("MGC_WD")
        or (Path.home() / ".xpedition" / sdd_home.parent.name / "wdir")
    ).expanduser()
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    os.environ.setdefault("MGC_WD", str(workdir))
    os.environ.setdefault("WDIR", os.pathsep.join([str(workdir), str(sdd_home / "standard")]))
    os.environ.setdefault("MGC_TMPDIR", os.environ.get("TEMP", str(workdir)))
    os.environ.setdefault("VCO", "ixw")
    # Keep the application's supported OS environment. Compatibility shims must
    # be explicitly configured by the operator, not silently injected here.
    if "MGLS_LICENSE_FILE" not in os.environ:
        license_file = root.parent / "LICENSE.DAT"
        if license_file.is_file():
            os.environ["MGLS_LICENSE_FILE"] = str(license_file)
    path_items = [
        sdd_home / "common" / "win64" / "bin",
        sdd_home / "common" / "win64" / "lib",
        sdd_home / "wg" / "win64" / "bin",
        sdd_home / "wg" / "win64" / "lib",
    ]
    existing = os.environ.get("PATH", "").split(os.pathsep)
    prefix = [str(item) for item in path_items if item.is_dir()]
    os.environ["PATH"] = os.pathsep.join(prefix + [item for item in existing if item])
    return sdd_home


# Xpedition reports "the automation code does not contain the licensing call
# required for authentication" through EXCEPINFO, as product code 10279 with
# interface-specific scode 0x8004022D. Both numbers are stable; the COM
# description is not -- on a Chinese installation it reads "自动化代码不包含身份
# 验证所需的许可调用。", which contains neither "license" nor "token", so a
# message-only test downgrades a licensing failure to a retryable E_SERVER and
# an agent retries it forever. Ordinary automation faults do not carry this
# EXCEPINFO: DISP_E_TYPEMISMATCH, for one, arrives as (hresult, text, None, n).
_LICENSE_CALL_MISSING_CODE = 10279
_LICENSE_CALL_MISSING_SCODE = -2147220947


def _com_excepinfo(exc: Exception) -> tuple[int | None, int | None]:
    """Return (product code, scode) from a pywin32 com_error's EXCEPINFO."""
    args = getattr(exc, "args", ())
    if len(args) >= 3 and isinstance(args[2], tuple) and len(args[2]) >= 6:
        info = args[2]
        code = info[4] if isinstance(info[4], int) else None
        scode = info[5] if isinstance(info[5], int) else None
        return code, scode
    return None, None


def _is_license_call_missing(exc: Exception) -> bool:
    code, scode = _com_excepinfo(exc)
    return code == _LICENSE_CALL_MISSING_CODE and scode == _LICENSE_CALL_MISSING_SCODE


# Designer refuses an operation it considers already satisfied -- opening the
# project it already has open, most often -- with product code 64185 and the text
# "Xpedition supports only scripts without GUI". That text describes neither the
# request nor the state that refused it, and it is what a stale session surfaces
# as: after a timed-out call IsProjectOpened() reported false, so the next
# command asked Designer to open a project it still had, and got this.
_SCRIPT_WITHOUT_GUI_CODE = 64185


def _is_refused_as_already_satisfied(exc: Exception) -> bool:
    code, _ = _com_excepinfo(exc)
    return code == _SCRIPT_WITHOUT_GUI_CODE


def _com_error(exc: Exception, action: str) -> AdapterError:
    text = str(exc)
    lower = text.lower()
    args = getattr(exc, "args", ())
    hresult = getattr(exc, "hresult", None)
    if hresult is None and args and isinstance(args[0], int):
        hresult = args[0]
    product_code, scode = _com_excepinfo(exc)
    native = {
        "hresult": hresult,
        "hresult_hex": f"0x{hresult & 0xFFFFFFFF:08X}" if isinstance(hresult, int) else None,
        "product_code": product_code,
        "scode": scode,
        "scode_hex": f"0x{scode & 0xFFFFFFFF:08X}" if isinstance(scode, int) else None,
        "source": args[2][1]
        if len(args) > 2 and isinstance(args[2], tuple) and len(args[2]) > 1
        else None,
    }
    details = {"action": action, "native": native}
    if hresult in {-2147467263, -2147467262} or scode in {-2147467263, -2147467262}:
        return AdapterError(
            "E_BACKEND_UNAVAILABLE",
            "The native server does not implement this automation interface",
            {
                **details,
                "reason": "E_NOTIMPL or E_NOINTERFACE from the actual call",
                "hint": "Use a documented supported interface or application workflow; "
                "do not retry activation",
            },
        )
    if hresult == -2147024891 or scode == -2147024891:
        return AdapterError("E_FORBIDDEN", "The native call was denied", details)
    if "class not registered" in lower or "-2147221005" in lower:
        return AdapterError(
            "E_BACKEND_UNAVAILABLE",
            "Xpedition COM automation is not registered",
            {
                **details,
                "hint": "inspect the installed interfaces and supported launcher; "
                "do not register a guessed class",
            },
        )
    # CO_E_SERVER_EXEC_FAILURE (0x80080005). Every Xpedition LocalServer32 entry points
    # at the real binary under <SDD_HOME>/<product>/win64/bin, but those binaries need
    # the release environment that the small launcher in common/win64/bin establishes.
    # COM activates them directly, without it, so they fail to start. Launch through the
    # launcher first, then attach with GetActiveObject.
    if "-2146959355" in text or "0x80080005" in lower:
        return AdapterError(
            "E_BACKEND_UNAVAILABLE",
            "Xpedition COM server could not be started by COM activation",
            {
                **details,
                "reason": (
                    "COM activation failed; a launch-context mismatch is one possible cause, "
                    "which requires a separate supported-launch/attachment comparison"
                ),
                "hint": "start the product through its launcher, then attach instead of activating",
            },
        )
    if _is_refused_as_already_satisfied(exc):
        return AdapterError(
            "E_CONFLICT",
            "Xpedition refused the request against its current state",
            {
                **details,
                "reason": (
                    "the application reports this operation as already satisfied, which is "
                    "what an open project and a stale session look like from here"
                ),
                "hint": "inspect the existing session and actual target; "
                "reconcile a timed-out operation before another write",
            },
        )
    if _is_license_call_missing(exc):
        return AdapterError(
            "E_AUTH",
            "Xpedition requires the automation licensing call before this operation",
            {
                **details,
                "reason": "the session did not hold an automation license for this call",
                "hint": "acquire the automation license through the adapter, then retry the task",
            },
        )
    if "license" in lower or "token" in lower:
        return AdapterError("E_AUTH", "Xpedition rejected the automation license", details)
    return AdapterError("E_SERVER", f"Xpedition COM operation failed: {_com_text(exc)}", details)


def _com_text(exc: Exception) -> str:
    """What the application said: its EXCEPINFO source and description when it gave
    them, not the whole com_error tuple."""
    args = getattr(exc, "args", ())
    info = args[2] if len(args) > 2 and isinstance(args[2], tuple) else ()
    if len(info) > 2 and info[2]:
        source = str(info[1] or "Xpedition").strip()
        return f"{source}: {str(info[2]).strip()}"
    if len(args) > 1 and isinstance(args[1], str) and args[1].strip():
        return args[1].strip()
    return str(exc)


def _propget(obj: Any, dispid: int, *args: Any) -> Any:
    """A property that takes arguments, read through its dispid.

    Read by name, pywin32 fetches the property without them and hands the arguments
    to the result's default member instead -- `doc.Nets(1)` is `doc.Nets.Item(1)`.
    """
    import pythoncom

    try:
        value = obj._oleobj_.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYGET, 1, *args)
    except Exception as exc:
        raise _com_error(exc, f"read property {dispid}") from exc
    if type(value).__name__ == "PyIDispatch":
        import win32com.client

        return win32com.client.Dispatch(value)
    return value


def _attachment_failure(error, progid, domain):
    """Only documented absent-object/class HRESULTs permit another alias lookup."""
    hresult = getattr(error, "hresult", None)
    if hresult is None and error.args and type(error.args[0]) is int:
        hresult = error.args[0]
    if hresult in {-2147221021, -2147221005, -2147221164}:
        return  # MK_E_UNAVAILABLE, CO_E_CLASSSTRING, REGDB_E_CLASSNOTREG
    raise AdapterError(
        "E_FORBIDDEN" if hresult == -2147024891 else "E_NATIVE_FAILURE",
        "Native automation attachment failed; this does not establish application absence",
        {
            "stage": "GetActiveObject",
            "progid": progid,
            "domain": domain,
            "hresult": hresult,
            "cause_type": type(error).__name__,
            "write_attempted": False,
            "hint": "Inspect existing processes and the normal application window. "
            "Do not launch a duplicate or change registration/licensing based on this error.",
        },
    ) from error


def _active_object(client: Any) -> Any:
    causes: list[str] = []
    for progid in application_progids("pcb"):
        try:
            app = client.GetActiveObject(progid)
            _check_bound_instance(app, "pcb")
            _BOUND_PROGIDS["pcb"] = progid
            return app
        except AdapterError:
            raise
        except Exception as exc:
            _attachment_failure(exc, progid, "pcb")
            causes.append(f"{progid}: {type(exc).__name__}: {exc}")
    raise AdapterError(
        "E_NOT_FOUND",
        "a running Xpedition Layout automation session was not found",
        {
            "progids": list(application_progids("pcb")),
            # "not found" is the usual reason, not the only one; say what failed
            "causes": causes,
            "_untrusted": ["causes"],
            # GetActiveObject only binds a session the user already started, so
            # say which application that is. Layout and Designer are separate
            # products with separate COM classes: a running Designer does not
            # satisfy a `pcb` command, and the reverse is equally true.
            "application": "Xpedition Layout",
            "serves_commands": "pcb *",
            "hint": "Inspect existing processes first; if none exist, use session start --kind pcb",
        },
    )


def _current_type_wrapper(client: Any, app: Any) -> Any:
    """Bind this process's Python classes to the running application's typelib.

    Some calls were written against early-bound wrappers. Late-bound compound
    interface calls can have a different dispatch shape; the current 151
    Documents.Open contract has one required BSTR and no optional parameters.
    Which wrapper the legacy GetActiveObject path returns depends
    only on whether gen_py happens to hold the wrapper -- it did on the machine
    this was built on, and a %TEMP% cleanup or a fresh machine takes it away. So
    the wrapper is generated on first use rather than assumed.
    """
    gencache = getattr(client, "gencache", None)
    if gencache is None:
        return app
    if getattr(app, "_oleobj_", None) is not None:
        try:
            # EnsureDispatch trusts any existing CLSID wrapper, including an old
            # release sharing this IID. Bind from the running object's typelib.
            info = app._oleobj_.GetTypeInfo()
            library, _ = info.GetContainingTypeLib()
            guid, lcid, _, major, minor, _ = library.GetLibAttr()
            module = gencache.EnsureModule(guid, lcid, major, minor)
            gencache.AddModuleToCache(guid, lcid, major, minor, bFlushNow=False)
            classes = client.CLSIDToClass
            identifiers = {*module.CLSIDToClassMap, *module.CLSIDToPackageMap}
            for iid in identifiers:
                if (
                    classes.HasClass(iid)
                    and not classes.GetClass(iid).__module__.startswith(module.__name__ + ".")
                    and classes.GetClass(iid).__module__ != module.__name__
                ):
                    current = gencache.GetModuleForCLSID(iid)
                    classes.RegisterCLSIDsFromDict(current.CLSIDToClassMap)
            wrapper = gencache.GetClassForCLSID(str(info.GetTypeAttr()[0]))
            if wrapper is None:
                raise TypeError("The running application typelib has no application wrapper")
            return wrapper(app._oleobj_)
        except Exception as exc:
            raise _com_error(exc, "bind_current_typelib") from exc
    try:
        return gencache.EnsureDispatch(app)
    except Exception:
        return app


def _designer_bound(client: Any, app: Any) -> Any:
    return _current_type_wrapper(client, app)


def _viewdraw_active(client: Any) -> Any:
    owned = _owner_application()
    if owned is not None:
        return owned
    causes: list[str] = []
    for progid in application_progids("schematic"):
        try:
            app = client.GetActiveObject(progid)
            _check_bound_instance(app, "schematic")
            _BOUND_PROGIDS["schematic"] = progid
            return _designer_bound(client, app)
        except AdapterError:
            raise
        except Exception as exc:
            _attachment_failure(exc, progid, "schematic")
            causes.append(f"{progid}: {type(exc).__name__}: {exc}")
    raise AdapterError(
        "E_NOT_FOUND",
        "a running Xpedition Designer automation session was not found",
        {
            "progids": list(application_progids("schematic")),
            "application": "Xpedition Designer (DxDesigner)",
            "serves_commands": "schematic *, library *, bom *",
            "hint": "Inspect existing processes first; if none exist, "
            "use session start --kind schematic",
            # "not found" is the usual reason, not the only one; say what failed
            "causes": causes,
            "_untrusted": ["causes"],
        },
    )


def _typed(client: Any, app: Any) -> Any:
    """Load the application's type library and hand back a late-bound `app`.

    win32com fills `client.constants` with the enumerations the snapshot code reads
    (`epcbSelectAll` and friends) only when the generated class for the type library is
    loaded, and a bare `GetActiveObject` does not load it. Once it is generated, however,
    win32com wraps every later object in the generated classes, whose parameterised
    properties (`Document.Components(...)`) are not callable the way the late-bound ones
    are. So the library is loaded for its constants and the object is returned late-bound.
    """
    gencache = getattr(client, "gencache", None)
    dynamic = getattr(client, "dynamic", None)
    if gencache is not None:
        app = _current_type_wrapper(client, app)
    if dynamic is not None:
        try:
            return dynamic.Dispatch(app)
        except Exception:
            pass
    return app


def _application(client: Any, attach_only: bool = False, quiet: bool = True) -> Any:
    _configure_environment()
    if (
        os.environ.get("XPEDITION_OBSERVE_ONLY") == "1"
        or os.environ.get("XPEDITION_REQUIRE_EXISTING") == "1"
    ):
        return _typed(client, _active_object(client))
    try:
        if attach_only:
            app = _typed(client, _active_object(client))
            if quiet:
                _quiet_gui(app, "pcb")
            return app
        try:
            app = _typed(client, _active_object(client))
            _quiet_gui(app, "pcb")
            return app
        except AdapterError as error:
            if error.code != "E_NOT_FOUND":
                raise
            started = _start_by_launcher(client, "pcb")
            if started is not None:
                app = _typed(client, started)
                _quiet_gui(app, "pcb")
                return app
            last_error: Exception | None = None
            for progid in application_progids("pcb"):
                try:
                    app = _typed(client, client.Dispatch(progid))
                    _quiet_gui(app, "pcb")
                    return app
                except Exception as exc:
                    last_error = exc
            if last_error is not None:
                raise _com_error(last_error, "create_application") from last_error
            raise AdapterError(
                "E_BACKEND_UNAVAILABLE", "Xpedition COM class is unavailable"
            ) from None
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "create_application") from exc


def _viewdraw_application(client: Any, attach_only: bool = False) -> Any:
    owned = _owner_application()
    if owned is not None:
        return owned
    _require_dedicated_designer()
    _configure_environment()
    if (
        os.environ.get("XPEDITION_OBSERVE_ONLY") == "1"
        or os.environ.get("XPEDITION_REQUIRE_EXISTING") == "1"
    ):
        return _viewdraw_active(client)
    try:
        if attach_only:
            return _viewdraw_active(client)
        try:
            return _viewdraw_active(client)
        except AdapterError as error:
            if error.code != "E_NOT_FOUND":
                raise
        last_error: Exception | None = None
        for progid in application_progids("schematic"):
            try:
                return _designer_bound(client, client.Dispatch(progid))
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise _com_error(last_error, "create_designer_application") from last_error
        raise AdapterError(
            "E_BACKEND_UNAVAILABLE", "Xpedition Designer COM class is unavailable"
        ) from None
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "create_designer_application") from exc


def _quiet_gui(app: Any, domain: str) -> dict[str, bool]:
    """Stop Layout from blocking automation on modal dialogs.

    An unattended agent cannot answer a message box, so any dialog Xpedition raises
    mid-operation turns a COM call into a hang. The Layout GUI exposes write-only
    suppression properties for exactly this; set them on every attach rather than only
    when opening a document. Dialogs raised while the application is still starting up
    happen before automation exists and are out of reach here.
    """
    applied: dict[str, bool] = {}
    if domain != "pcb":
        return applied
    try:
        gui = app.Gui
    except Exception:
        return applied
    for name in (
        "SuppressTrivialDialogs",
        "SuppressNotepadDialogs",
        "SuppressVariantDataOutOfDateDialog",
        "SingleThreaded",
    ):
        try:
            setattr(gui, name, True)
            applied[name] = True
        except Exception:
            applied[name] = False
    return applied


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if value is not None:
            return value
    return default


def _items(collection: Any, *, strict: bool = False) -> Iterable[Any]:
    if strict:
        count = collection.Count
        if type(count) is not int or not 0 <= count <= 1_000_000:
            raise ValueError("strict collection Count must be an integer from 0 to 1000000")
    else:
        count = int(_value(collection, "Count", default=0) or 0)
    for index in range(1, count + 1):
        try:
            yield collection.Item(index)
        except Exception:
            if strict:
                raise
            try:
                yield collection.Item(index - 1)
            except Exception as exc:
                raise _com_error(exc, "enumerate_collection") from exc


def _licensed_document(app: Any) -> Any:
    try:
        doc = app.ActiveDocument
        if doc is None:
            raise AdapterError("E_NOT_FOUND", "Xpedition has no active document")
        key = doc.Validate(0)
        _, client = _import_com()
        server = client.Dispatch("MGCPCBAutomationLicensing.Application")
        token = server.GetToken(key)
        doc.Validate(token)
        return doc
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "license_document") from exc


def _constants(client: Any, names: list[str]) -> dict[str, Any]:
    owned = _owner_application()
    if owned is not None and names == ["VD_WIRE"]:
        from .designer_owner_dispatch import fixed_wire_constant

        return {"VD_WIRE": fixed_wire_constant(owned)}
    constants = getattr(client, "constants", None)
    values: dict[str, Any] = {}
    for name in names:
        value = getattr(constants, name, None) if constants is not None else None
        if value is None:
            raise AdapterError(
                "E_BACKEND_UNAVAILABLE",
                "Xpedition automation type-library constants are unavailable",
                {"missing_constant": name},
            )
        values[name] = value
    return values


def _component_record(component: Any) -> dict[str, Any]:
    cell = _value(component, "Cell", default=None)
    cell_name = _value(cell, "Name", "name", default=None) if cell is not None else None
    row = {
        "refdes": str(_value(component, "RefDes", "ReferenceDesignator", "Name", default="")),
        "part_number": str(_value(component, "PartNumber", "PartNo", default="")),
        "footprint": str(cell_name or _value(component, "CellName", default="")),
        "x": _position_mm(component, "X"),
        "y": _position_mm(component, "Y"),
        "unit": "mm",
        "rotation": _value(component, "Orientation", "Rotation", default=0),
        "side": _side(component),
        "placed": bool(_value(component, "Placed", default=False)),
    }
    row["uid"] = _value(component, "UniqueId", default=None)
    row["pins"] = [
        {
            "number": str(_value(pin, "Name", default="")),
            "net": str(_value(_value(pin, "Net"), "Name", default="")),
            "x": _position_mm(pin, "X") if row["placed"] else None,
            "y": _position_mm(pin, "Y") if row["placed"] else None,
        }
        for pin in _items(_com_member(component, "Pins"))
    ]
    return row


def _position_mm(component: Any, axis: str) -> Any:
    """A component's position in millimetres, whatever the document's current unit."""
    getter = getattr(component, f"GetPosition{axis}", None)
    if callable(getter):
        try:
            return round(float(getter(UNIT_MM)), 3)
        except Exception:
            pass
    return _value(component, f"Position{axis}", default=None)


def _side(component: Any) -> str:
    """`Side` is 1 on the top and 512 on the bottom (epcbSideTop / epcbSideBottom); a
    top-side part also reports `Layer` 1, the bottom one the last layer."""
    side = _value(component, "Side", default=None)
    if side in (1, 1.0):
        return "top"
    if side in (512, 512.0):
        return "bottom"
    return "top" if _value(component, "Layer", default=1) == 1 else "bottom"


def _routing_record(item: Any) -> dict[str, Any]:
    """A trace or via: its net and layer (a via reports the layer it starts on)."""
    net = _value(item, "Net", default=None)
    return {
        "net": str(_value(net, "Name", default="")) if net is not None else "",
        "layer": _value(item, "Layer", default=None),
    }


def _net_record(net: Any) -> dict[str, Any]:
    return {"name": str(_value(net, "Name", default=""))}


def _snapshot(app: Any, client: Any) -> dict[str, Any]:
    doc = _licensed_document(app)
    # `Components` and `Nets` are parameterised properties whose parameters are all
    # optional and default to "everything" (all selection states, all component and cell
    # types, any name). Reading them bare is the one form both the late-bound object and
    # the generated class accept; passing the filters raises DISP_E_BADPARAMCOUNT.
    try:
        components_collection = _com_member(doc, "Components")
        nets_collection = _com_member(doc, "Nets")
        components = [_component_record(item) for item in _items(components_collection)]
        nets = [_net_record(item) for item in _items(nets_collection)]
        tracks = [_routing_record(item) for item in _items(_com_member(doc, "Traces"))]
        vias = [_routing_record(item) for item in _items(_com_member(doc, "Vias"))]
        name = str(_value(doc, "Name", "FileName", default="xpedition-document"))
        return {
            "project": name,
            "revision": "native",
            "components": components,
            "nets": nets,
            "connections": [],
            "pcb": {
                "components": components,
                "nets": nets,
                "footprints": sorted(
                    {item["footprint"] for item in components if item.get("footprint")}
                ),
                "tracks": tracks,
                "vias": vias,
            },
            "metadata": {
                "backend": "native_xpedition",
                "source": "MGCPCB.ExpeditionPCBApplication",
                # the board's layers are not read as records yet; their count is
                "layer_count": int(_value(doc, "LayerCount", default=0) or 0) or None,
                "_untrusted": ["project", "components", "nets"],
            },
        }
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "snapshot") from exc


def _point_record(point: Any) -> dict[str, Any]:
    if isinstance(point, (tuple, list)) and len(point) >= 2:
        return {"x": point[0], "y": point[1]}
    return {
        "x": _value(point, "X", "x", default=None),
        "y": _value(point, "Y", "y", default=None),
    }


def _com_member(obj: Any, name: str) -> Any:
    """Read a COM member that may be a property or a method on this release.

    Several Xpedition members that read like accessors (``GetLocation``,
    ``GetConnections``, ``DefaultFilePath``) are properties, not methods. Calling one
    raises DISP_E_PARAMNOTOPTIONAL, and when the call sits inside a bare ``except`` the
    data silently comes back empty instead of failing loudly. Try the property first and
    fall back to the call form for releases that expose a method.
    """
    try:
        value = getattr(obj, name)
    except Exception:
        return None
    if value is None:
        return None
    if hasattr(value, "Count") or not callable(value):
        return value
    try:
        return value()
    except Exception:
        return value


def _component_location(component: Any) -> dict[str, Any]:
    """Read a Designer component position.

    ``GetLocation`` is a *property* returning a point object on current Xpedition
    Standard, not a method: calling it yields the point's default member instead of
    the point, which is why coordinates used to come back as null. Read the property
    first and keep the call form as a fallback for releases that expose a method.
    """
    empty = {"x": None, "y": None}
    try:
        location = component.GetLocation
    except Exception:
        return empty
    record = _point_record(location)
    if record.get("x") is not None or record.get("y") is not None:
        return record
    try:
        return _point_record(location())
    except Exception:
        return empty


def _designer_collection(app: Any, method: str, design_name: str) -> Any:
    try:
        function = getattr(app, method)
        # Current Xpedition Standard expects the root schematic block as the
        # second argument.  The active design name (for example ``Board1``)
        # resolves to that block (for example ``Schematic1``) through the
        # public project-data API.  Older releases may accept the name
        # directly, so retain the compatibility fallbacks below.
        root_block: Any = design_name
        if _owner_application() is not None:
            project_data = _designer_project_data(app)
            root_block = _designer_declared_call(
                project_data, "GetiCDBDesignRootBlock", 12, 8, ((8, 1),), (design_name,)
            )
            if type(root_block) is not str or not root_block:
                raise AdapterError("E_NATIVE_FAILURE", "Native design root block is unavailable")
        else:
            try:
                project_data = app.GetProjectData()
                root_block = project_data.GetiCDBDesignRootBlock(design_name)
            except Exception:
                pass
        for args in (
            ("", root_block, "-1", "STD", True),
            ("", root_block, "-1", "", True),
            ("", root_block, "", "", True),
        ):
            try:
                return function(*args)
            except Exception:
                continue
        try:
            return function("", design_name)
        except Exception:
            return function("", design_name, "", "", True)
    except Exception as exc:
        raise _designer_collection_error(app, exc, method, design_name) from exc


def _designer_collection_error(
    app: Any, exc: Exception, method: str, design_name: str
) -> AdapterError:
    """Say why a design collection could not be read, not just that COM said no.

    Two ordinary mid-design states both surface as a bare type mismatch here, and
    they have different recoveries, so an operator cannot act on the COM error:

    * the schematic changed since the last `library materialize, then schematic package`, so the
      packaged design no longer matches the sheets -- re-package;
    * a sheet was added or removed, after which `GetActiveDesign` reports the
      schematic instead of the block until the project is closed and reopened
      (`COMPATIBILITY.md`), which re-packaging does *not* clear.

    That `GetActiveDesign` tell is what separates them.
    """
    error = _com_error(exc, method)
    if error.code != "E_SERVER":
        return error
    # Only a name counts. `_com_member` hands back the bound method when a release
    # exposes this as a method it cannot call, and that is not an answer.
    value = _com_member(app, "GetActiveDesign")
    active = value.strip() if isinstance(value, str) else ""
    after_sheet_change = bool(active) and active != design_name
    error.details.update(
        {
            "design": design_name,
            "active_design": active,
            "likely_cause": (
                "a sheet was added or removed; the project has to be reopened"
                if after_sheet_change
                else "the schematic has changed since it was last packaged"
            ),
            "hint": (
                "session close, then session start, and reopen the project"
                if after_sheet_change
                else "run library materialize, then schematic package, then retry"
            ),
            "_untrusted": ["active_design"],
        }
    )
    return error


def _designer_net_identity(net: Any) -> str:
    """A net's name, or Designer's own `$<sheet>N<id>` id when it carries no label."""
    if net is None:
        return ""
    for member in ("LogicalNetName",):
        try:
            value = _com_member(net, member)
            if value:
                return str(value)
        except Exception:
            pass
    name = _designer_net_name(net)
    if name:
        return name
    try:
        return str(_com_member(net, "UID") or "")
    except Exception:
        return ""


def _uid_sheet(uid: str) -> int | None:
    """`$4I916` -> sheet 4: the sheet number is the first field of a Designer UID."""
    match = re.match(r"\$(\d+)[A-Z]", uid or "")
    return int(match.group(1)) if match else None


def _designer_attribute_display(attribute: Any, owner: dict[str, Any]) -> dict[str, Any]:
    """Read formal IVdAttr getters without the best-effort `_value` defaults."""
    record: dict[str, Any] = {
        "owner": dict(owner),
        "display": {"units": "sheet_unit"},
        "getter_errors": {},
    }
    for member, key in (("Name", "name"), ("Value", "value")):
        try:
            value = getattr(attribute, member)
            if value is None:
                raise ValueError("getter returned no value")
            record[key] = str(value)
        except Exception as exc:
            record["getter_errors"][member] = f"{type(exc).__name__}: {exc}"
    try:
        member = attribute.GetLocation
        point = member() if callable(member) and not hasattr(member, "X") else member
        x, y = point.X, point.Y
        if type(x) is not int or type(y) is not int:
            raise ValueError("GetLocation did not return integer X/Y")
        record["display"].update(x=x, y=y)
    except Exception as exc:
        record["getter_errors"]["GetLocation"] = f"{type(exc).__name__}: {exc}"
    for member, key in (
        ("Visible", "visible"),
        ("Size", "size"),
        ("Orientation", "orientation"),
        ("Origin", "origin"),
    ):
        try:
            value = getattr(attribute, member)
            if type(value) is not int:
                raise ValueError(f"{member} did not return an integer")
            record["display"][key] = value
        except Exception as exc:
            record["getter_errors"][member] = f"{type(exc).__name__}: {exc}"
    return record


def _designer_attributes(component: Any, owner: dict[str, Any]) -> tuple[dict, list, dict, list]:
    """Bound display reads to six named families; arbitrary free text is uncovered."""
    from .schematic_attribute_display import DISPLAY_NAMES, canonical_name

    attributes: dict[str, str] = {}
    displays: list[dict] = []
    pairs: list[tuple[Any, dict]] = []
    errors: list[str] = []
    seen_names: set[str] = set()
    complete = False
    count = None
    maximum = 256
    try:
        collection = component.Attributes
        if callable(collection) and not hasattr(collection, "Count"):
            collection = collection()
        count = collection.Count
        if type(count) is not int or count < 0:
            raise ValueError("attribute Count is not a non-negative integer")
        for index in range(1, min(count, maximum) + 1):
            attribute = collection.Item(index)
            name = attribute.Name
            if not isinstance(name, str):
                raise ValueError(f"attribute {index} Name was not read")
            duplicate = name in seen_names
            if duplicate:
                errors.append(f"attribute {index} duplicate original Name: {name!r}")
            seen_names.add(name)
            try:
                value = attribute.Value
                if not isinstance(value, str):
                    raise ValueError("Value returned no value")
                if not duplicate:
                    attributes[name] = value
            except Exception as exc:
                errors.append(f"attribute {index} Value: {type(exc).__name__}: {exc}")
            if canonical_name(name) in {canonical_name(item) for item in DISPLAY_NAMES}:
                record = _designer_attribute_display(attribute, owner)
                displays.append(record)
                pairs.append((attribute, record))
                if any(key in record["getter_errors"] for key in ("Name", "Value")):
                    errors.append(f"attribute {index} repeated Name/Value getter failed")
                elif record.get("name") != name or record.get("value") != attributes.get(name):
                    errors.append(f"attribute {index} Name/Value changed during observation")
        complete = count <= maximum and not errors
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    coverage = {
        "names": list(DISPLAY_NAMES),
        "enumeration_complete": complete,
        "attribute_count": count,
        "attribute_limit": maximum,
        "truncated": type(count) is int and count > maximum,
        "errors": errors,
        "other_text_graphics_observed": False,
        "exact_font_envelope_observed": False,
        "units": "sheet_unit",
        "sheet_unit_mils": 10,
    }
    return attributes, displays, coverage, pairs


def _designer_component_record(component: Any) -> dict[str, Any]:
    refdes = ""
    identity_errors: list[str] = []
    try:
        try:
            observed_refdes = component.Refdes
        except AttributeError:
            observed_refdes = component.RefDes
        if not isinstance(observed_refdes, str):
            raise ValueError("Refdes did not return a string")
        refdes = observed_refdes
    except Exception as exc:
        identity_errors.append(f"Refdes: {type(exc).__name__}: {exc}")
    location = _component_location(component)
    uid = ""
    try:
        observed_uid = component.UID
        observed_uid = observed_uid() if callable(observed_uid) else observed_uid
        if not isinstance(observed_uid, str) or not observed_uid:
            raise ValueError("UID did not return a non-empty string")
        uid = observed_uid
    except Exception as exc:
        identity_errors.append(f"UID: {type(exc).__name__}: {exc}")
    owner = {"refdes": refdes, "uid": uid, "sheet": _uid_sheet(uid)}
    attributes, displays, attribute_coverage, _pairs = _designer_attributes(component, owner)
    part_number = attributes.get("Part Number", "")
    pins: list[dict[str, Any]] = []
    pins_complete = False
    pin_errors: list[str] = []
    try:
        connections = component.GetConnections
        if callable(connections) and not hasattr(connections, "Count"):
            connections = connections()
        for connection in _items(connections, strict=True):
            pin = connection.CompPin
            net = connection.Net
            number = pin.Number
            identity = _designer_net_identity(net) if net is not None else ""
            px = py = None
            try:
                point = _com_member(pin, "GetLocation")
                px, py = int(point.X), int(point.Y)
            except Exception:
                pass
            if number is not None or identity:
                pins.append(
                    {
                        "number": str(number) if number is not None else None,
                        "net": identity or None,
                        "net_unnamed": bool(identity.startswith("$")),
                        "x": px,
                        "y": py,
                        "no_connect": False,
                    }
                )
            if number is None:
                pin_errors.append("a connection's CompPin.Number was not read")
            if net is not None and not identity:
                pin_errors.append("a connected pin's native net identity was not read")
        pins_complete = not pin_errors
    except Exception as exc:
        pin_errors.append(f"{type(exc).__name__}: {exc}")
    return {
        "refdes": refdes,
        "name": part_number or refdes,
        "internal_part_no": part_number,
        "value": attributes.get("VALUE") or attributes.get("Value") or part_number,
        "description": attributes.get("Description", attributes.get("DESCRIPTION", "")),
        "package": attributes.get("Package", attributes.get("PKG_TYPE", "")),
        "manufacturer": attributes.get("Manufacturer", ""),
        "mpn": attributes.get("MPN", attributes.get("Manufacturer Part Number", "")),
        "attributes": attributes,
        "attribute_display": displays,
        "attribute_display_coverage": attribute_coverage,
        "pin_observation_coverage": {"complete": pins_complete, "errors": pin_errors},
        "identity_observation_coverage": {
            "complete": not identity_errors,
            "errors": identity_errors,
        },
        "uid": uid,
        "sheet": _uid_sheet(uid),
        "x": location.get("x"),
        "y": location.get("y"),
        "rotation": _value(component, "Orientation", default=None),
        "pins": pins,
    }


def _designer_net_name(net: Any) -> str:
    """Read a schematic net's name.

    A Designer net carries no ``Name``; the name lives on the label attached to one of
    its segments, reachable as ``net.GetLabel(segment).TextString``. Reading ``Name``
    alone reports every net on a live sheet as unnamed.
    """
    direct = _value(net, "Name", default=None)
    if direct:
        return str(direct)
    segments = _com_member(net, "GetSegments")
    if segments is None:
        return ""
    for segment in _items(segments):
        try:
            label = net.GetLabel(segment)
        except Exception:
            continue
        text = _value(label, "TextString", "Text", default=None) if label is not None else None
        if text:
            return str(text)
    return ""


def _designer_snapshot(app: Any, params: dict[str, Any]) -> dict[str, Any]:
    try:
        design_name = str(_value(app, "GetActiveDesign", default="")() or "")
    except Exception:
        design_name = ""
    if not design_name:
        design_name = str(params.get("design") or "")
    if not design_name:
        raise AdapterError(
            "E_NOT_FOUND",
            "Xpedition Designer has no active design",
            {"hint": "open a Designer project before requesting a schematic snapshot"},
        )
    components_collection = _designer_collection(app, "DesignComponents", design_name)
    records = [
        _designer_component_record(item) for item in _items(components_collection, strict=True)
    ]
    # Symbols without a reference designator are power, ground, no-connect and
    # off-page marks. A no-connect sits exactly on the pin end it marks, so a pin
    # with no net whose end coincides with such a symbol is deliberately open.
    marks: set[tuple[int, int]] = set()
    parts: list[dict[str, Any]] = []
    for record in records:
        if record["refdes"]:
            parts.append(record)
        elif record.get("x") is not None and record.get("y") is not None:
            marks.add((int(record["x"]), int(record["y"])))
    for record in parts:
        for pin in record["pins"]:
            if not pin.get("net") and pin.get("x") is not None:
                pin["no_connect"] = (int(pin["x"]), int(pin["y"])) in marks
    nets: dict[str, dict[str, Any]] = {}
    pins_by_net: dict[str, set[str]] = {}
    for record in parts:
        for pin in record["pins"]:
            identity = pin.get("net")
            if not identity or pin.get("number") is None:
                continue
            entry = nets.setdefault(
                identity, {"name": identity, "unnamed": bool(pin.get("net_unnamed")), "sheets": []}
            )
            sheet = record.get("sheet")
            if sheet is not None and sheet not in entry["sheets"]:
                entry["sheets"].append(sheet)
            pins_by_net.setdefault(identity, set()).add(f"{record['refdes']}.{pin['number']}")
    # every sheet of the schematic: SchematicSheetDocuments alone lists only the
    # ones open in a window, which read a four-sheet design as one sheet
    sheets: list[dict[str, Any]] = []
    try:
        numbers = _sheet_numbers(app)
    except AdapterError:
        if _owner_application() is not None:
            raise
        numbers = {}
    for number in sorted(numbers):
        sheets.append(
            {
                "number": number,
                "name": f"{numbers[number]}.{number}",
                "parts": sum(1 for record in parts if record.get("sheet") == number),
            }
        )
    connections = [
        {"net": name, "pins": sorted(pins)}
        for name, pins in sorted(pins_by_net.items())
        if len(pins) >= 2
    ]
    unnamed_symbols = len(records) - len(parts)
    return {
        "project": design_name,
        "revision": "native",
        "sheets": sheets,
        "components": parts,
        "unnamed_components": [record for record in records if not record["refdes"]],
        "nets": [nets[name] for name in sorted(nets)],
        "connections": connections,
        "metadata": {
            "backend": "native_xpedition",
            "domain": "schematic",
            "source": "Viewdraw.Application",
            "unnamed_symbols": unnamed_symbols,
            "_untrusted": ["project", "sheets", "components", "nets", "connections"],
        },
    }


def _designer_active_component(app: Any, params: dict[str, Any]) -> Any:
    design_name = str(params.get("design") or "")
    if not design_name:
        try:
            design_name = str(app.GetActiveDesign() or "")
        except Exception:
            design_name = ""
    collection = _designer_collection(app, "DesignComponents", design_name)
    refdes = str(params.get("refdes") or "")
    for component in _items(collection):
        value = str(_value(component, "Refdes", "RefDes", "Name", default=""))
        if value == refdes:
            return component
    raise AdapterError("E_NOT_FOUND", f"component {refdes!r} was not found", {"refdes": refdes})


def _open_requested_document(app: Any, params: dict[str, Any]) -> Any:
    """The board a request names, opened in Layout; None when it names nothing.

    A `.prj` resolves to its board design's `PCBDesignPath`, and Layout's questions
    while the board opens are answered.
    """
    if not (params.get("project") or params.get("path")):
        return None
    requested = _layout_board_path(params)
    if os.environ.get("XPEDITION_OBSERVE_ONLY") == "1" or params.get("observe_only"):
        doc = _value(app, "ActiveDocument", default=None)
        current = (
            str(_value(doc, "FullName", "FileName", default="") or "") if doc is not None else ""
        )
        if not current or Path(current).resolve() != Path(requested).resolve():
            raise AdapterError(
                "E_CONFLICT",
                "observation requires this board to be already open",
                {
                    "requested_project": str(requested),
                    "open_project": current,
                    "next_action": (
                        "open the owned project through an explicit confirmed session attachment"
                    ),
                },
            )
        return doc
    doc, _prompts = _open_layout_document(app, requested)
    return doc


def _find_component(doc: Any, refdes: str) -> Any:
    try:
        component = doc.FindComponent(str(refdes))
    except Exception as exc:
        raise _com_error(exc, "find_component") from exc
    if component is None:
        raise AdapterError(
            "E_NOT_FOUND",
            f"component {refdes!r} was not found",
            {"refdes": str(refdes)},
        )
    return component


def _apply_operation(doc: Any, operation: dict[str, Any], client: Any) -> dict[str, Any]:
    kind = str(operation.get("type", ""))
    if kind in {"place_component", "place_pcb_component"}:
        component = _find_component(doc, str(operation.get("refdes", "")))
        # Place(dX, dY, dOrientation, bTop, eFixType, eUnit, eAngleUnit): the fourth
        # argument is "top side", the fifth the fix type (0 = none), verified on XPED2604.
        try:
            component.Place(
                float(operation["x"]),
                float(operation["y"]),
                float(operation.get("rotation", 0)),
                str(operation.get("side", "top")).lower() != "bottom",
                0,
                _unit_code(operation.get("unit")),
                0,
            )
        except Exception as exc:
            raise _com_error(exc, "place_component") from exc
        return {"type": kind, "refdes": str(operation["refdes"]), "applied": True}
    if kind in {"move_component", "move_pcb_component"}:
        component = _find_component(doc, str(operation.get("refdes", "")))
        try:
            component.Move(
                float(operation["x"]), float(operation["y"]), _unit_code(operation.get("unit"))
            )
        except Exception as exc:
            raise _com_error(exc, "move_component") from exc
        return {"type": kind, "refdes": str(operation["refdes"]), "applied": True}
    raise AdapterError(
        "E_BACKEND_UNAVAILABLE",
        f"native COM adapter does not implement operation {kind!r} yet",
        {
            "operation": kind,
            "supported": [
                "place_component",
                "place_pcb_component",
                "move_component",
                "move_pcb_component",
            ],
        },
    )


def _designer_pin(component: Any, number: str) -> Any:
    try:
        connections = _native_call(
            "connect.pin.GetConnections", lambda: _com_member(component, "GetConnections")
        )
        matches = []
        for connection in _items(connections, strict=True):
            pin = _native_call(
                "connect.pin.CompPin", lambda connection=connection: connection.CompPin
            )
            observed = _native_call("connect.pin.Number", lambda pin=pin: pin.Number)
            if str(observed) == str(number):
                matches.append(pin)
    except Exception as exc:
        raise _com_error(exc, "find_component_pin") from exc
    if len(matches) == 1:
        return matches[0]
    raise AdapterError(
        "E_NOT_FOUND" if not matches else "E_CONFLICT",
        "The requested component pin was not uniquely observed",
        {"action": "find_component_pin", "matches": len(matches)},
    )


def _add_schematic_net(
    block: Any, operation: dict[str, Any], pin1: Any = None, pin2: Any = None, client: Any = None
) -> Any:
    constants = _constants(client, ["VD_WIRE"])
    x1 = int(operation.get("x1", operation.get("x", 0)))
    y1 = int(operation.get("y1", operation.get("y", 0)))
    x2 = int(operation.get("x2", x1))
    y2 = int(operation.get("y2", y1))
    try:
        return block.AddNet(x1, y1, x2, y2, pin1, pin2, constants["VD_WIRE"])
    except Exception as exc:
        raise _com_error(exc, "add_schematic_net") from exc


def _label_schematic_net(net: Any, name: str, operation: dict[str, Any]) -> None:
    if not name:
        return
    progress = {
        "phase": "label_segment",
        "segment_observed": None,
        "label_attempted": False,
        "label_returned": False,
        "label_returned_nonnull": None,
    }
    try:
        segments = _native_call("connect.net.GetSegments", lambda: _com_member(net, "GetSegments"))
        segment = (
            _native_call(
                "connect.net.FirstSegment", lambda: next(iter(_items(segments, strict=True)), None)
            )
            if segments is not None
            else None
        )
        progress["segment_observed"] = segment is not None
        if segment is None:
            raise AdapterError(
                "E_SERVER",
                "schematic net has no segment to carry its label",
                {"action": "label_schematic_net"},
            )
        x = int(operation.get("x", operation.get("x1", 0)))
        y = int(operation.get("y", operation.get("y1", 0)))
        progress["phase"] = "add_label"

        def add_label():
            progress["label_attempted"] = True
            result = net.AddLabel(segment, str(name), x, y)
            progress["label_returned"] = True
            progress["label_returned_nonnull"] = result is not None
            return result

        _native_call("connect.net.AddLabel", add_label)
    except Exception as exc:
        error = exc if isinstance(exc, AdapterError) else _com_error(exc, "label_schematic_net")
        error.details = {**(error.details or {}), "schematic_connect_progress": dict(progress)}
        if error is exc:
            raise
        raise error from exc


def _pin_point(pin: Any, *, role="pin") -> tuple[int, int]:
    try:
        point = _native_call(f"connect.{role}.GetLocation", lambda: _com_member(pin, "GetLocation"))
        x = _native_call(f"connect.{role}.X", lambda: point.X)
        y = _native_call(f"connect.{role}.Y", lambda: point.Y)
        return int(x), int(y)
    except Exception as exc:
        raise _com_error(exc, "pin_location") from exc


def _wire_between_pins(
    block: Any, first: Any, second: Any, operation: dict[str, Any], client: Any
) -> tuple[Any, tuple[int, int]]:
    """A wire from one pin to the other, through `points` when the operation names them,
    else one horizontal and one vertical segment; the net, and where its label goes."""
    progress = {
        "phase": "wire_constant",
        "segments_planned": None,
        "segments_attempted": 0,
        "segments_returned": 0,
        "last_segment_index": None,
        "last_returned_nonnull": None,
    }
    try:
        wire = _native_call("connect.VD_WIRE", lambda: _constants(client, ["VD_WIRE"])["VD_WIRE"])
        progress["phase"] = "first_pin_location"
        start = _pin_point(first, role="first_pin")
        progress["phase"] = "second_pin_location"
        end = _pin_point(second, role="second_pin")
        middle = [
            (int(point[0]), int(point[1]))
            for point in operation.get("points") or []
            if isinstance(point, (list, tuple)) and len(point) == 2
        ]
        if not middle and start[0] != end[0] and start[1] != end[1]:
            middle = [(end[0], start[1])]
        route = [start, *middle, end]
        route = [
            point for index, point in enumerate(route) if index == 0 or point != route[index - 1]
        ]
        net = None
        last = len(route) - 2
        progress.update(phase="add_net", segments_planned=last + 1)
        if last < 0:
            raise AdapterError(
                "E_CHANGESET_INVALID",
                "Connection has no nonzero segment",
                {"action": "add_schematic_net", "reason": "zero_length_connection_route"},
            )
        for index, (a, b) in enumerate(zip(route, route[1:], strict=False)):

            def add_segment(index=index, a=a, b=b):
                progress["last_segment_index"] = index
                progress["segments_attempted"] += 1
                created = block.AddNet(
                    a[0],
                    a[1],
                    b[0],
                    b[1],
                    first if index == 0 else None,
                    second if index == last else None,
                    wire,
                )
                progress["segments_returned"] += 1
                progress["last_returned_nonnull"] = created is not None
                return created

            created = _native_call(f"connect.AddNet.segment_{index}", add_segment)
            if created is None:
                raise AdapterError(
                    "E_SERVER",
                    "AddNet returned no native net object",
                    {"action": "add_schematic_net", "reason": "native_net_return_missing"},
                )
            net = net if net is not None else created
    except Exception as exc:
        error = exc if isinstance(exc, AdapterError) else _com_error(exc, "add_schematic_net")
        error.details = {**(error.details or {}), "schematic_connect_progress": dict(progress)}
        if error is exc:
            raise
        raise error from exc
    # the label goes over the middle of the first segment, clear of the part at its end
    (ax, ay), (bx, by) = route[0], route[1]
    label = ((ax + bx) // 2, ay + 2) if ay == by else (ax + 2, (ay + by) // 2)
    return net, label


VD_MASK_BOX = 2  # VdObjectTypeMask.VDM_BOX
VD_ALL = 0
VD_NET_CONNECTIONS = 23  # IVdNet.Connections, an optional-argument property
VD_BOX_LOCATION = 8  # IVdBox.GetLocation(VdCorner)
VD_BOX_SELECTED = 12  # IVdBox.Selected
VD_INVISIBLE = 0


def _net_connections(net: Any) -> list[Any]:
    """The pins a wire object joins. `Connections` takes an optional filter that late
    binding cannot leave out, so it is read by its dispid with no argument."""
    import pythoncom
    import win32com.client

    raw = net._oleobj_.Invoke(VD_NET_CONNECTIONS, 0, pythoncom.DISPATCH_PROPERTYGET, 1)
    return list(_items(win32com.client.Dispatch(raw)))


def _wire_owners(net: Any) -> list[tuple[str, str, Any]]:
    """(refdes, pin number, component) of every pin on a wire object; a power, ground or
    no-connect symbol has no reference designator."""
    owners = []
    for connection in _net_connections(net):
        pin = _value(connection, "CompPin", default=None)
        component = _value(pin, "Component", default=None) if pin is not None else None
        refdes = str(_value(component, "Refdes", default="") or "") if component else ""
        owners.append((refdes, str(_value(pin, "Number", default="") or ""), component))
    return owners


def _wire_labels(net: Any) -> list[tuple[str, tuple[int, int] | None]]:
    labels = []
    for segment in _items(_com_member(net, "GetSegments")):
        label = net.GetLabel(segment)
        if label is None:
            continue
        where = None
        try:
            point = _com_member(label, "GetLocation")
            where = (int(point.X), int(point.Y))
        except Exception:
            pass
        labels.append((str(label.TextString), where))
    return labels


def _select_wire(net: Any) -> None:
    for segment in _items(_com_member(net, "GetSegments")):
        net.SelectSegment(segment)


def _remove_label_boxes(app: Any, points: list[tuple[int, int]]) -> int:
    """Delete the boxes drawn around labels that are gone: the planner draws a boxed
    label as a label plus a box, and removing the wire takes only the label."""
    import pythoncom
    import win32com.client

    if not points:
        return 0
    view = app.ActiveView
    # a delete takes everything selected: start from nothing
    view.Block.DeSelectAll()
    removed = 0
    for raw in _items(view.Query(VD_MASK_BOX, VD_ALL)):
        try:
            low = win32com.client.Dispatch(
                raw._oleobj_.Invoke(VD_BOX_LOCATION, 0, pythoncom.DISPATCH_METHOD, 1, 0)
            )
            high = win32com.client.Dispatch(
                raw._oleobj_.Invoke(VD_BOX_LOCATION, 0, pythoncom.DISPATCH_METHOD, 1, 1)
            )
            x1, y1, x2, y2 = int(low.X), int(low.Y), int(high.X), int(high.Y)
        except Exception:
            continue
        if any(x1 <= x <= x2 and y1 - 5 <= y <= y2 + 5 for x, y in points):
            raw._oleobj_.Invoke(VD_BOX_SELECTED, 0, pythoncom.DISPATCH_PROPERTYPUT, 0, True)
            removed += 1
    if removed:
        view.Block.DeleteSelected(False)
    return removed


LABEL_CHAR_WIDTH = 6  # sheet units a label character takes, as the planner sizes boxes


def _resize_label_boxes(app: Any, moves: list[tuple[int, int, int, bool]]) -> int:
    """Grow or shrink the box around each renamed label by `delta` units: to the right,
    or to the left for a label drawn left of its wire. `moves` holds (x, y, delta, left)
    with the label's position before the rename.

    The box is replaced, not moved: `IVdBox.SetLocation` changes the view's object, and
    reopening the project showed it was never saved with the sheet.
    """
    import pythoncom
    import win32com.client

    if not moves:
        return 0
    view = app.ActiveView
    # a label moved or an attribute added stays selected, and the delete below would
    # take it with the boxes: start from nothing
    view.Block.DeSelectAll()
    replacements: list[tuple[int, int, int, int]] = []
    for raw in _items(view.Query(VD_MASK_BOX, VD_ALL)):
        try:
            low = win32com.client.Dispatch(
                raw._oleobj_.Invoke(VD_BOX_LOCATION, 0, pythoncom.DISPATCH_METHOD, 1, 0)
            )
            high = win32com.client.Dispatch(
                raw._oleobj_.Invoke(VD_BOX_LOCATION, 0, pythoncom.DISPATCH_METHOD, 1, 1)
            )
            x1, y1, x2, y2 = int(low.X), int(low.Y), int(high.X), int(high.Y)
        except Exception:
            continue
        for x, y, delta, left in moves:
            if x1 <= x <= x2 and y1 - 5 <= y <= y2 + 5:
                replacements.append((x1 - delta, y1, x2, y2) if left else (x1, y1, x2 + delta, y2))
                raw._oleobj_.Invoke(VD_BOX_SELECTED, 0, pythoncom.DISPATCH_PROPERTYPUT, 0, True)
                break
    if replacements:
        view.Block.DeleteSelected(False)
        for x1, y1, x2, y2 in replacements:
            view.Block.AddBox(x1, y1, x2, y2)
    return len(replacements)


def _pin_connection(component: Any, number: str) -> Any:
    for connection in _items(_com_member(component, "GetConnections")):
        pin = _value(connection, "CompPin", default=None)
        if str(_value(pin, "Number", default="")) == str(number):
            return connection
    raise AdapterError("E_NOT_FOUND", f"pin {number!r} was not found on component")


def _cut_wire(
    block: Any, net: Any, keep: Any, stub: bool, segment: Any = None
) -> tuple[list[tuple[int, int]], int]:
    """Select what goes with a pin's wire: the whole wire and the symbols on it when it
    served only `keep` (a refdes, or a (refdes, pin) pair), else the segment at the pin.
    Returns the label points going with it and the symbols selected."""
    points: list[tuple[int, int]] = []
    marks = 0
    if stub:
        points = [where for _text, where in _wire_labels(net) if where is not None]
        _select_wire(net)
        for refdes, _number, component in _wire_owners(net):
            if refdes == "" and component is not None:
                component.Selected = True
                marks += 1
    elif segment is not None:
        label = net.GetLabel(segment)
        if label is not None:
            try:
                point = _com_member(label, "GetLocation")
                points.append((int(point.X), int(point.Y)))
            except Exception:
                pass
        net.SelectSegment(segment)
    return points, marks


def _set_designer_property_display(component: Any, operation: dict[str, Any]) -> dict[str, Any]:
    from .errors import CLIError
    from .schematic_attribute_display import canonical_name, project_display, target_record

    before = _designer_component_record(component)
    try:
        expected = copy.deepcopy(before)
        actual_record = target_record(before, str(operation["name"]))
        if "display_before" in operation and operation["display_before"] != actual_record:
            raise CLIError("E_CONFLICT", "attribute display changed since preview")
        _prior, wanted = project_display(expected, operation)
        fresh_attributes, fresh_records, fresh_coverage, pairs = _designer_attributes(
            component, actual_record["owner"]
        )
        if (
            fresh_coverage.get("enumeration_complete") is not True
            or fresh_attributes != before["attributes"]
            or fresh_records != before["attribute_display"]
            or fresh_coverage != before["attribute_display_coverage"]
        ):
            raise CLIError(
                "E_CONFLICT",
                "attribute enumeration changed or became incomplete before display setters",
                {"coverage": fresh_coverage},
            )
        matches = [
            (attribute, record)
            for attribute, record in pairs
            if canonical_name(record.get("name")) == canonical_name(operation["name"])
        ]
        if len(matches) != 1 or matches[0][1] != actual_record:
            raise CLIError("E_CONFLICT", "attribute changed during display preflight")
    except CLIError as exc:
        raise AdapterError(
            exc.code, str(exc), {"write_attempted": False, **(exc.details or {})}
        ) from exc
    attribute = matches[0][0]
    attempted: list[str] = []
    try:
        attempted.append("Value")
        attribute.Value = operation["value"]
        attempted.append("SetLocation")
        attribute.SetLocation(operation["display"]["x"], operation["display"]["y"])
        attempted.append("Visible")
        attribute.Visible = 3  # formal VDVALUEVISIBLE, not bool(True)/VDVISIBLE
        if "size" in operation["display"]:
            attempted.append("Size")
            attribute.Size = operation["display"]["size"]
        observed = _designer_attribute_display(attribute, actual_record["owner"])
        if observed != wanted:
            raise AdapterError(
                "E_PROJECT_INVALID",
                "attribute display setters did not read back exactly",
                {"stage": "display_read_back", "expected": wanted, "observed": observed},
            )
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, AdapterError)
            else _com_error(exc, "set_schematic_property_display")
        )
        error.details.update(
            write_attempted=True,
            attempted_fields=attempted,
            saved=False,
            save_acknowledged=False,
            persistence_verified=False,
            observed=_designer_attribute_display(attribute, actual_record["owner"]),
            hint="partial attribute fields may have changed; inspect before another write",
        )
        raise error from exc
    return {
        "type": "set_property",
        "refdes": operation["refdes"],
        "name": actual_record["name"],
        "added": False,
        "applied": True,
        "before": actual_record,
        "observed": observed,
    }


def _apply_designer_display_changes(
    app: Any, params: dict[str, Any], client: Any, design_name: str
) -> dict[str, Any]:
    """Verify before saving, then normally close/reopen and verify actual getters."""
    from .changeset import apply_operations
    from .errors import CLIError
    from .schematic_attribute_display import (
        freeze_save_metadata,
        same_save_metadata,
        verify_display_changes,
    )

    operations = params["operations"]
    stage = "display_owner_preflight"
    owned = False
    operation_index = None
    operation_call_attempted = False
    applied = []

    def failure(error, reason):
        from .designer_owner_dispatch import DispatchError

        if isinstance(error, AdapterError):
            result = error
        elif isinstance(error, CLIError):
            result = AdapterError(error.code, str(error), error.details or {})
        elif isinstance(error, DispatchError):
            result = AdapterError("E_CONFLICT", "Dedicated display owner is not admitted")
        else:
            result = _com_error(error, stage)
        result.details.setdefault("stage", stage)
        result.details.update(
            reason=reason,
            display_failure={
                "stage": stage,
                "reason": reason,
                "operations_requested": len(operations),
                "operations_returned": len(applied),
                "operation_index": operation_index,
                "operation_call_attempted": operation_call_attempted,
            },
        )
        return result

    try:
        held = _owner_application()
        if held is not None and held is not app:
            raise CLIError("E_CONFLICT", "display requires the exact held owner Application")
        owned = held is not None and held is app
        identity_reader = application_identity
        if owned:
            from .designer_owner_dispatch import (
                _CURRENT,
                require_current_document_owner,
            )
            from .designer_owner_dispatch import application_identity as owner_identity

            require_current_document_owner(app=app)
            active = _CURRENT.get()
            if active is None or active[1] != "apply_changeset":
                raise CLIError("E_CONFLICT", "display requires the current changeset owner")
            identity_reader = owner_identity
        stage = "display_snapshot_preflight"
        before = _designer_snapshot(app, {**params, "design": design_name})
        project_path = Path(str(params.get("project") or "")).expanduser().resolve()
        stage = "display_identity_preflight"
        identity_before = identity_reader(app, "schematic")
        document_before = identity_before.get("document") or {}
        if (
            not params.get("project")
            or params.get("save", True) is not True
            or type(identity_before.get("pid")) is not int
            or identity_before["pid"] <= 0
            or document_before.get("open") is not True
            or document_before.get("status") != "observed"
            or (
                not owned
                and document_before.get("source") != "IsProjectOpened/GetProjectData public getters"
            )
            or not isinstance(document_before.get("path"), str)
            or not document_before["path"]
            or Path(document_before["path"]).resolve() != project_path
            or not isinstance(document_before.get("designs"), list)
            or not document_before["designs"]
            or not all(isinstance(name, str) and name for name in document_before["designs"])
        ):
            raise CLIError(
                "E_VALIDATION",
                "display lifecycle needs the exact open project and actual public identity getters",
            )
        stage = "display_model_preflight"
        apply_operations(before, operations)
        stage = "display_metadata_preflight"
        save_metadata = freeze_save_metadata(before)
        if not same_save_metadata(save_metadata, params.get("save_metadata")):
            raise CLIError(
                "E_CONFLICT", "native save metadata changed or was not declared in preview"
            )
        stage = "display_protection_preflight"
        available = verify_display_changes(before, before, operations)
        if not available["complete"]:
            raise CLIError(
                "E_VALIDATION", "display protection observations are incomplete", available
            )
        stage = "display_target_preflight"
        for operation in operations:
            from .schematic_attribute_display import target_record

            component = next(
                c for c in before["components"] if str(c["refdes"]) == str(operation["refdes"])
            )
            if "display_before" in operation and operation["display_before"] != target_record(
                component, str(operation["name"])
            ):
                raise CLIError("E_CONFLICT", "attribute display changed since preview")
    except Exception as exc:
        error = failure(exc, "display_preflight_refused")
        # No operation/helper mutation has been entered on this control path.
        # This does not imply that the preceding native reads were undispatched.
        error.details.update(write_attempted=False, saved=False)
        raise error from exc
    observed = None
    try:
        stage = "display_apply"
        for index, operation in enumerate(operations):
            operation_index = index
            operation_call_attempted = False
            _ensure_sheet(app, operation.get("sheet"))
            operation_call_attempted = True
            applied.append(_apply_designer_operation(app, operation, client, design_name, {}))
        operation_index = None
        stage = "display_before_save_observation"
        observed = _designer_snapshot(app, {**params, "design": design_name})
        stage = "display_before_save_verification"
        verification = verify_display_changes(
            before, observed, operations, save_metadata=save_metadata
        )
        if not verification["valid"]:
            raise AdapterError(
                "E_PROJECT_INVALID",
                "display or protected engineering records changed unexpectedly",
                {"stage": "before_save_verification", "verification": verification},
            )
    except Exception as exc:
        error = failure(exc, "display_apply_or_verification_failed")
        error.details.update(
            applied=applied,
            write_attempted=True,
            saved=False,
            save_acknowledged=False,
            persistence_verified=False,
        )
        if observed is not None:
            error.details["observed"] = observed
        raise error from exc
    lifecycle = {
        "source": "designer_save_close_reopen",
        "save_count": None if owned else 0,
        "close_count": None if owned else 0,
        "reopen_count": None if owned else 0,
        "save_acknowledged": None if owned else False,
        "closed_observed": None if owned else False,
        "close_returned": None,
        "before_identity": document_before,
        "before_pid": identity_before["pid"],
    }
    reopened = None
    try:
        stage = "display_save_close_reopen"
        if owned:
            from .designer_owner_dispatch import persist_changeset_project

            lifecycle = persist_changeset_project(app, str(project_path))
        else:
            # Historical direct-helper path. Public Designer dispatch still
            # requires its dedicated owner and cannot select this fallback.
            if not _close_if_open(app, project_path, lifecycle=lifecycle):
                raise AdapterError(
                    "E_CONFLICT", "the display target was not closed; reopen stopped"
                )
            _ensure_project(app, project_path, lifecycle=lifecycle)
        stage = "display_reopened_identity"
        identity_after = identity_reader(app, "schematic")
        lifecycle.update(
            reopened_identity=identity_after.get("document"), reopened_pid=identity_after.get("pid")
        )
        if (
            identity_after.get("document") != document_before
            or type(identity_after.get("pid")) is not int
            or identity_after.get("pid") != identity_before["pid"]
            or any(lifecycle[key] != 1 for key in ("save_count", "close_count", "reopen_count"))
        ):
            raise AdapterError(
                "E_CONFLICT", "reopened Designer project identity or lifecycle count differs"
            )
        stage = "display_reopened_observation"
        reopened = _designer_snapshot(app, {**params, "design": design_name})
        stage = "display_reopened_verification"
        persistence = verify_display_changes(
            before, reopened, operations, save_metadata=save_metadata
        )
        if not persistence["valid"]:
            raise AdapterError(
                "E_PROJECT_INVALID",
                "display or protected engineering records differ after reopen",
                {"stage": "after_reopen_verification", "verification": persistence},
            )
    except Exception as exc:
        error = failure(exc, "display_persistence_incomplete")
        if owned and stage == "display_save_close_reopen":
            actual_lifecycle = getattr(exc, "lifecycle", None)
            if isinstance(actual_lifecycle, dict):
                lifecycle = {**lifecycle, **actual_lifecycle}
        error.details.update(
            write_attempted=True,
            applied=applied,
            observed=observed,
            lifecycle=lifecycle,
            save_acknowledged=lifecycle["save_acknowledged"],
            saved=True
            if lifecycle["save_acknowledged"]
            else False
            if lifecycle["save_acknowledged"] is False and lifecycle["save_count"] == 0
            else None,
            persistence_verified=False,
        )
        if reopened is not None:
            error.details["read_back_after_reopen"] = reopened
        raise error from exc
    return {
        "applied": applied,
        "count": len(applied),
        "domain": "schematic",
        "saved": True,
        "save_acknowledged": True,
        "persistence_verified": True,
        "verification_before_save": verification,
        "verification": {
            **persistence,
            "persistence_verified": True,
            "not_checked": [
                item
                for item in persistence["not_checked"]
                if item != "independent_reopen_persistence"
            ],
        },
        "read_back_before_save": observed,
        "read_back_after_reopen": reopened,
        "metadata_changes": persistence["metadata_changes"],
        "save_metadata": save_metadata,
        "lifecycle": lifecycle,
    }


def _verify_designer_reopen_records(before, after, *, save_metadata=None):
    """Protect every observed engineering record across normal save/reopen.

    Native census order and the CLI's identifier representation are normalized
    by their existing explicit contracts. Only a separately bound border save
    timestamp may differ; no component, UID, pin, net or attribute is omitted.
    This does not claim full pre-edit equivalence for arbitrary edit operations.
    """
    from .models import normalise_project
    from .native_snapshot_revision import canonical
    from .schematic_attribute_display import (
        _native_border_datetime,
        freeze_save_metadata,
        same_save_metadata,
    )

    metadata_changes, issues = [], []
    try:
        for snapshot in (before, after):
            if (
                snapshot.get("revision") != "native"
                or (snapshot.get("metadata") or {}).get("backend") != "native_xpedition"
                or (snapshot.get("metadata") or {}).get("source") != "Viewdraw.Application"
            ):
                raise ValueError("native_designer_projection_required")
            if not all(
                isinstance(snapshot.get(name), list)
                for name in ("components", "unnamed_components", "nets", "connections", "sheets")
            ):
                raise ValueError("required_native_collections_unobserved")
            for row in [*snapshot["components"], *snapshot["unnamed_components"]]:
                if (
                    type(row.get("attributes")) is not dict
                    or type(row.get("attribute_display")) is not list
                    or (row.get("identity_observation_coverage") or {}).get("complete") is not True
                    or (row.get("pin_observation_coverage") or {}).get("complete") is not True
                    or (row.get("attribute_display_coverage") or {}).get("enumeration_complete")
                    is not True
                ):
                    raise ValueError("required_native_record_coverage_unobserved")
                names = [item.get("name") for item in row["attribute_display"]]
                if any(not isinstance(name, str) or not name for name in names) or len(
                    names
                ) != len(set(names)):
                    raise ValueError("native_attribute_identity_ambiguous")
        expected, observed = (
            canonical(normalise_project(before)),
            canonical(normalise_project(after)),
        )
        if save_metadata is not None:
            if not same_save_metadata(freeze_save_metadata(before), save_metadata):
                raise ValueError("native_save_metadata_binding_changed")
            owner = save_metadata["owner"]
            old = [
                row
                for row in expected["unnamed_components"]
                if {key: row.get(key) for key in owner} == owner
            ]
            new = [
                row
                for row in observed["unnamed_components"]
                if {key: row.get(key) for key in owner} == owner
            ]
            if len(old) != 1 or len(new) != 1:
                raise ValueError("native_save_metadata_owner_changed")
            value = new[0]["attributes"].get("@DATETIME")
            if not _native_border_datetime(value):
                raise ValueError("native_save_metadata_value_invalid")
            if old[0]["attributes"].get("@DATETIME") != value:
                metadata_changes.append(
                    {
                        "owner": copy.deepcopy(owner),
                        "field": "@DATETIME",
                        "before": old[0]["attributes"]["@DATETIME"],
                        "after": value,
                        "source": "native_save_metadata",
                    }
                )
                old[0]["attributes"]["@DATETIME"] = value
        for snapshot in (expected, observed):
            for row in [*snapshot["components"], *snapshot["unnamed_components"]]:
                row["attribute_display"] = sorted(
                    row["attribute_display"], key=lambda item: json.dumps(item, sort_keys=True)
                )
            snapshot.pop("revision", None)
            snapshot.pop("metadata", None)
        if expected != observed:
            issues.append({"kind": "native_records_changed_across_save_close_reopen"})
        complete = True
    except Exception as error:
        complete = False
        issues.append(
            {"kind": "required_native_protection_unavailable", "error_type": type(error).__name__}
        )
    return {
        "valid": complete and not issues,
        "complete": complete,
        "protection_verified": complete and not issues,
        "scope": "all observed native records before_save versus after_reopen",
        "metadata_changes": metadata_changes,
        "issues": issues,
    }


def _apply_designer_edit_changes(app, params, client, design_name):
    """Apply once, then verify a normal same-owner persistence lifecycle."""
    from .changeset import apply_operations
    from .native_verification import verify_native_changes
    from .schematic_attribute_display import freeze_save_metadata, same_save_metadata
    from .schematic_edit_protection import validate_supported, verify_schematic_edit_protection

    operations = params["operations"]
    save = params.get("save", True)
    if type(save) is not bool:
        raise AdapterError("E_VALIDATION", "save must be a boolean", {"write_attempted": False})
    if _owner_application() is not app:
        raise AdapterError(
            "E_HUMAN_REQUIRED",
            "Editing requires the original dedicated owner",
            {"write_attempted": False},
        )
    before = _designer_snapshot(app, {**params, "design": design_name})
    if not _verify_designer_reopen_records(before, before)["valid"]:
        raise AdapterError(
            "E_VALIDATION",
            "native edit protection observations are incomplete",
            {"write_attempted": False},
        )
    try:
        save_metadata = freeze_save_metadata(before)
        if not same_save_metadata(save_metadata, params.get("save_metadata")):
            raise AdapterError("E_CONFLICT", "native save metadata differs from preview")
        supported = validate_supported(before, operations, save_metadata=save_metadata)
        if supported.get("valid") is not True or supported.get("complete") is not True:
            raise AdapterError(
                "E_VALIDATION",
                "edit operation protection is unsupported",
                {"protection": supported},
            )
        expected, _changes = apply_operations(before, operations)
    except Exception as exc:
        error = (
            exc if isinstance(exc, AdapterError) else _com_error(exc, "edit_protection_preflight")
        )
        error.details["write_attempted"] = False
        error.dispatch_started = False
        raise error from exc
    applied, sheets_saved, deferred_nets = [], [], {}
    observed = reopened = verification = lifecycle = None
    operation_attempted = False
    try:
        for item in operations:
            operation_attempted = True
            if item.get("sheet") is not None:
                wanted, current = int(item["sheet"]), _active_sheet(app)
                if save and current is not None and current != wanted:
                    _designer_save_document(app)
                    sheets_saved.append(current)
                _ensure_sheet(app, wanted)
            result = _apply_designer_operation(app, item, client, design_name, deferred_nets)
            if result is not None:
                applied.append(result)
                if result.get("type") != "create_net":
                    for prior in applied:
                        if (
                            prior.get("type") == "create_net"
                            and prior.get("net") == result.get("net")
                            and prior.get("deferred")
                        ):
                            prior.update(applied=True, deferred=False)
        for name, operation in deferred_nets.items():
            net = _add_schematic_net(app.ActiveView.Block, operation, client=client)
            _label_schematic_net(net, name, operation)
            for prior in applied:
                if prior.get("type") == "create_net" and prior.get("net") == name:
                    prior.update(applied=True, deferred=False)
        if len(applied) != len(operations):
            raise AdapterError("E_CHANGESET_INVALID", "each native operation must be an object")
        observed = _designer_snapshot(app, {**params, "design": design_name})
        verification = verify_native_changes(expected, observed, operations)
        edit_protection = verify_schematic_edit_protection(
            before, observed, operations, save_metadata=save_metadata
        )
        if (
            not verification["valid"]
            or edit_protection.get("valid") is not True
            or edit_protection.get("complete") is not True
        ):
            raise AdapterError(
                "E_PROJECT_INVALID",
                "requested changes did not read back before save",
                {
                    "stage": "before_save_verification",
                    "verification": verification,
                    "edit_protection": edit_protection,
                },
            )
        if not save:
            return {
                "applied": applied,
                "count": len(applied),
                "domain": "schematic",
                "saved": False,
                "save_acknowledged": False,
                "persistence_verified": False,
                "verification": verification,
                "edit_protection": edit_protection,
                "read_back_before_save": observed,
                "sheets_saved": [],
            }
        from .designer_owner_dispatch import persist_changeset_project

        lifecycle = persist_changeset_project(app, str(params["project"]))
        reopened = _designer_snapshot(app, {**params, "design": design_name})
        target = verify_native_changes(expected, reopened, operations)
        protection = _verify_designer_reopen_records(
            observed, reopened, save_metadata=save_metadata
        )
        if not target["valid"] or not protection["valid"]:
            raise AdapterError(
                "E_PROJECT_INVALID",
                "engineering records differ after normal save/close/reopen",
                {
                    "stage": "after_reopen_verification",
                    "verification": target,
                    "protection": protection,
                },
            )
        if any(
            lifecycle.get(key) != 1 for key in ("save_count", "close_count", "reopen_count")
        ) or any(
            lifecycle.get(key) is not True
            for key in ("save_acknowledged", "closed_observed", "close_returned")
        ):
            raise AdapterError("E_PROJECT_INVALID", "normal persistence lifecycle is incomplete")
        return {
            "applied": applied,
            "count": len(applied),
            "domain": "schematic",
            "saved": True,
            "save_acknowledged": True,
            "persistence_verified": True,
            "lifecycle": lifecycle,
            "verification_before_save": verification,
            "edit_protection": edit_protection,
            "verification": {**target, "persistence_verified": True, "protection_verified": True},
            "protection": protection,
            "verification_source": "native_getters_after_edit_save_close_open",
            "read_back_before_save": observed,
            "read_back_after_reopen": reopened,
            "save_metadata": save_metadata,
            "metadata_changes": protection["metadata_changes"],
            "sheets_saved": sorted(set(sheets_saved)),
        }
    except Exception as exc:
        error = exc if isinstance(exc, AdapterError) else _com_error(exc, "edit_save_close_reopen")
        lifecycle = getattr(exc, "lifecycle", None) or lifecycle
        acknowledgement = lifecycle.get("save_acknowledged") if lifecycle else False
        error.details.update(
            applied=applied,
            write_attempted=operation_attempted,
            observed=observed,
            lifecycle=lifecycle,
            saved=True
            if acknowledgement is True
            else None
            if (lifecycle and lifecycle.get("save_count")) or sheets_saved
            else False,
            save_acknowledged=acknowledgement,
            persistence_verified=False,
            sheets_saved=sheets_saved,
        )
        if reopened is not None:
            error.details["read_back_after_reopen"] = reopened
        raise error from exc


def _apply_designer_operation(
    app: Any,
    operation: dict[str, Any],
    client: Any,
    design_name: str,
    deferred_nets: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    kind = str(operation.get("type", ""))
    try:
        block = app.ActiveView.Block
    except Exception as exc:
        raise _com_error(exc, "get_active_block") from exc
    # what an earlier operation left selected must not ride along with this one
    deselect = getattr(block, "DeSelectAll", None)
    if callable(deselect):
        try:
            deselect()
        except Exception as exc:
            raise _com_error(exc, "deselect_all") from exc
    if kind == "place_component":
        # No default library: "MISC" does not exist in a stock installation, so falling
        # back to it turned a missing parameter into Designer error 2005 ("Library: MISC
        # does not exist") in the message window, which says nothing about the real cause.
        library = str(operation.get("library", "") or "")
        device = str(operation.get("device_name", operation.get("part_number", "")))
        symbol = str(operation.get("symbol_name", operation.get("part_number", "")))
        missing = [
            name
            for name, value in (
                ("library", library),
                ("device_name", device),
                ("symbol_name", symbol),
            )
            if not value
        ]
        if missing:
            raise AdapterError(
                "E_CHANGESET_INVALID",
                "native schematic placement requires " + ", ".join(missing),
                {
                    "refdes": str(operation.get("refdes", "")),
                    "missing": missing,
                    "hint": "name the central-library partition the symbol lives in",
                },
            )
        x = int(operation.get("x", 0))
        y = int(operation.get("y", 0))
        refdes = str(operation["refdes"])
        try:
            component = block.AddPartInstance(library, device, symbol, x, y)
        except Exception as exc:
            raise _com_error(exc, "place_schematic_component") from exc
        if component is None:
            raise AdapterError(
                "E_SERVER",
                "AddPartInstance returned no component; its outcome is unknown",
                {"orphan_placed": None, "requested_refdes": refdes},
            )
        # The instance is already on the sheet here. Neither the component nor the
        # block exposes a delete entry point in this API, so a failure past this
        # point cannot be rolled back: name the orphan instead of dropping it
        # silently, so the caller can remove it and re-read the snapshot.
        try:
            component.Refdes = refdes
        except Exception as exc:
            error = _com_error(exc, "set_schematic_refdes")
            location = _component_location(component)
            actual = _designer_component_record(component)
            error.details.update(
                {
                    "orphan_placed": True,
                    "orphan_library": library,
                    "orphan_device": device,
                    "orphan_symbol": symbol,
                    "orphan_x": location.get("x"),
                    "orphan_y": location.get("y"),
                    "orphan_actual": actual,
                    "orphan_observation_source": "returned native component getters",
                    "requested_position": {"x": x, "y": y},
                    "requested_refdes": refdes,
                    "hint": (
                        "an unnamed symbol was left on the sheet and this API cannot "
                        "delete it; remove it in Designer before retrying"
                    ),
                    "_untrusted": [
                        "orphan_library",
                        "orphan_device",
                        "orphan_symbol",
                        "orphan_actual",
                    ],
                }
            )
            raise error from exc
        return {"type": kind, "refdes": refdes, "applied": True}
    if kind == "move_component":
        component = _designer_active_component(app, {"design": design_name, **operation})
        try:
            component.SetLocation(int(operation["x"]), int(operation["y"]))
        except Exception as exc:
            raise _com_error(exc, "move_schematic_component") from exc
        return {"type": kind, "refdes": str(operation["refdes"]), "applied": True}
    if kind == "delete_component":
        refdes = str(operation["refdes"])
        component = _designer_active_component(app, {"design": design_name, **operation})
        try:
            block.DeSelectAll()
            points: list[tuple[int, int]] = []
            marks = wires = 0
            for connection in _items(_com_member(component, "GetConnections")):
                net = _value(connection, "Net", default=None)
                if net is None:
                    continue
                owners = _wire_owners(net)
                # a wire only this part used goes with it, with its power and ground
                if all(owner in ("", refdes) for owner, _n, _c in owners):
                    found, count = _cut_wire(block, net, refdes, stub=True)
                    points += found
                    marks += count
                    wires += 1
            component.Selected = True
            # True: the wires the part leaves unconnected go too
            block.DeleteSelected(True)
            boxes = _remove_label_boxes(app, points)
        except AdapterError:
            raise
        except Exception as exc:
            raise _com_error(exc, "delete_schematic_component") from exc
        return {
            "type": kind,
            "refdes": refdes,
            "applied": True,
            "wires_removed": wires,
            "symbols_removed": marks,
            "label_boxes_removed": boxes,
        }
    if kind == "set_property":
        component = _designer_active_component(app, {"design": design_name, **operation})
        name, value = str(operation["name"]), str(operation["value"])
        if "display" in operation:
            return _set_designer_property_display(component, operation)
        try:
            attribute = component.FindAttribute(name)
            if attribute is None:
                location = _component_location(component)
                x = int(location.get("x") or 0)
                y = int(location.get("y") or 0)
                component.AddAttribute(f"{name}={value}", x, y - 20, VD_INVISIBLE)
                added = True
            else:
                attribute.Value = value
                added = False
        except Exception as exc:
            raise _com_error(exc, "set_schematic_property") from exc
        return {
            "type": kind,
            "refdes": str(operation["refdes"]),
            "name": name,
            "added": added,
            "applied": True,
        }
    if kind == "disconnect":
        pin_id = str(operation["pin"])
        refdes, _, number = pin_id.partition(".")
        component = _designer_active_component(
            app, {"design": design_name, **operation, "refdes": refdes}
        )
        try:
            connection = _pin_connection(component, number)
            net = _value(connection, "Net", default=None)
            if net is None:
                raise AdapterError("E_CONFLICT", f"pin {pin_id} is not connected")
            owners = _wire_owners(net)
            stub = all(owner == "" or (owner, pin) == (refdes, number) for owner, pin, _c in owners)
            block.DeSelectAll()
            points, marks = _cut_wire(
                block, net, (refdes, number), stub, _value(connection, "Segment", default=None)
            )
            block.DeleteSelected(True)
            boxes = _remove_label_boxes(app, points)
        except AdapterError:
            raise
        except Exception as exc:
            raise _com_error(exc, "disconnect_schematic_pin") from exc
        return {
            "type": kind,
            "pin": pin_id,
            "applied": True,
            "whole_wire": stub,
            "symbols_removed": marks,
            "label_boxes_removed": boxes,
        }
    if kind == "rename_net":
        old, new = str(operation["net"]), str(operation["name"])
        sheet = operation.get("sheet")
        renamed = 0
        moves: list[tuple[int, int, int, bool]] = []
        try:
            for component in _items(_designer_collection(app, "DesignComponents", design_name)):
                refdes = str(_value(component, "Refdes", default="") or "")
                if not refdes:
                    continue
                if sheet is not None:
                    uid = str(_com_member(component, "UID") or "")
                    if _uid_sheet(uid) not in (None, int(sheet)):
                        continue
                for connection in _items(_com_member(component, "GetConnections")):
                    net = _value(connection, "Net", default=None)
                    if net is None:
                        continue
                    for segment in _items(_com_member(net, "GetSegments")):
                        label = net.GetLabel(segment)
                        if label is None or str(label.TextString) != old:
                            continue
                        if any(owner == "" for owner, _n, _c in _wire_owners(net)):
                            raise AdapterError(
                                "E_VALIDATION",
                                f"net {old!r} is named by a power or ground symbol, not a "
                                "label; change it in the design and draw it again",
                                {"net": old},
                            )
                        # the label's place and which side of its wire it sits on,
                        # so its box can follow the new length
                        try:
                            point = _com_member(label, "GetLocation")
                            lx, ly = int(point.X), int(point.Y)
                            ends = [segment.Location(0), segment.Location(1)]
                            left = lx < min(int(end.X) for end in ends)
                            # Designer puts boxes on its 10-unit grid, so the label
                            # moves by whole grid steps too and stays inside its box
                            grow = LABEL_CHAR_WIDTH * (len(new) - len(old))
                            delta = 10 * math.ceil(grow / 10) if grow > 0 else -10 * (-grow // 10)
                            if delta:
                                moves.append((lx, ly, delta, left))
                                if left:
                                    label.SetLocation(lx - delta, ly)
                        except Exception:
                            pass
                        label.TextString = new
                        renamed += 1
        except AdapterError:
            raise
        except Exception as exc:
            raise _com_error(exc, "rename_schematic_net") from exc
        if not renamed:
            raise AdapterError(
                "E_NOT_FOUND",
                f"no label of net {old!r} on sheet {sheet}",
                {"net": old, "sheet": sheet},
            )
        try:
            boxes = _resize_label_boxes(app, moves)
        except Exception:
            boxes = 0
        return {
            "type": kind,
            "net": old,
            "name": new,
            "labels": renamed,
            "label_boxes_resized": boxes,
            "applied": True,
        }
    if kind == "create_net":
        deferred_nets[str(operation["name"])] = operation
        return {
            "type": kind,
            "net": str(operation["name"]),
            "applied": False,
            "deferred": True,
        }
    if kind == "connect":
        pins = operation.get("pins")
        if not isinstance(pins, list) or len(pins) != 2:
            raise AdapterError(
                "E_CHANGESET_INVALID",
                "native schematic connect currently requires exactly two pins",
                {"net": str(operation.get("net", ""))},
            )
        components: dict[str, Any] = {}
        for component in _items(_designer_collection(app, "DesignComponents", design_name)):
            refdes = str(_value(component, "Refdes", "RefDes", "Name", default=""))
            if refdes:
                components[refdes] = component
        pin_objects: list[Any] = []
        for pin_id in pins:
            refdes, separator, number = str(pin_id).partition(".")
            if not separator or refdes not in components:
                raise AdapterError("E_NOT_FOUND", f"schematic pin {pin_id!r} was not found")
            pin_objects.append(_designer_pin(components[refdes], number))
        net, label = _wire_between_pins(block, pin_objects[0], pin_objects[1], operation, client)
        # over the wire's first segment unless the operation places the label itself
        label_at = (
            operation
            if "x" in operation or "y" in operation
            else {**operation, "x": label[0], "y": label[1]}
        )
        _label_schematic_net(net, str(operation.get("net", "")), label_at)
        deferred_nets.pop(str(operation.get("net", "")), None)
        return {
            "type": kind,
            "net": str(operation["net"]),
            "pins": [str(item) for item in pins],
            "applied": True,
        }
    raise AdapterError(
        "E_BACKEND_UNAVAILABLE",
        f"native Designer adapter does not implement operation {kind!r} yet",
        {
            "operation": kind,
            "supported": ["place_component", "move_component", "create_net", "connect"],
        },
    )


def _domain(params: dict[str, Any]) -> str:
    explicit = str(params.get("domain") or params.get("kind") or "").lower()
    if explicit in {"schematic", "designer"}:
        return "schematic"
    if explicit in {"pcb", "layout"}:
        return "pcb"
    path = str(params.get("project") or params.get("path") or "").lower()
    return "schematic" if path.endswith((".prj", ".dproj")) else "pcb"


def _native_executable(domain: str) -> tuple[Path, Path]:
    sdd_home = _configure_environment()
    if sdd_home is None:
        raise AdapterError(
            "E_CONFIG", "Xpedition SDD_HOME could not be discovered", {"domain": domain}
        )
    # The common launcher establishes the release environment and then starts
    # the real binary.  Launching wg\win64\bin\ExpeditionPCB.exe or
    # wv\win64\bin\viewdraw.exe directly bypasses that setup on current
    # Standard releases and exits with STATUS_DLL_NOT_FOUND (0xC0000135).
    # Both domains therefore go through common\win64\bin: the launchers are
    # ~35 KB stubs beside the ~37 MB products they set up and start.
    relative = (
        Path("common")
        / "win64"
        / "bin"
        / {
            "schematic": "viewdraw.exe",
            "pcb": "ExpeditionPCB.exe",
            "library": "LibraryManager.exe",
        }[domain]
    )
    executable = sdd_home / relative
    if not executable.is_file():
        raise AdapterError(
            "E_NOT_FOUND",
            "Xpedition executable was not found",
            {"path": str(executable), "domain": domain},
        )
    return executable, executable.parent


def _open_project_answering(app: Any, path: str) -> Any:
    """Designer's `OpenProject` with its questions answered meanwhile.

    Switching projects while sheets are open asks whether to close them, and the
    call does not return until someone answers.
    """
    if _owner_application() is not None:
        from .designer_owner_dispatch import open_project227

        return open_project227(app, path)
    with _designer_prompt_context():
        return _native_call("Designer.OpenProject", lambda: app.OpenProject(path))


def _designer_prompt_context():
    # The dedicated owner has no permission to click another window or answer
    # any modal prompt. A blocking native call remains owned and observable.
    if _owner_application() is not None:
        return nullcontext(None)
    return _PromptAnswerer(DESIGNER_PROMPTS, process_name="viewdraw.exe")


_APPLICATION_PROGIDS = {
    "schematic": application_progids("schematic"),
    "pcb": application_progids("pcb"),
}


def _still_registered(client: Any, domain: str) -> bool:
    for progid in _APPLICATION_PROGIDS[domain]:
        try:
            client.GetActiveObject(progid)
            return True
        except Exception:
            continue
    return False


def _quit_and_confirm(
    client: Any, app: Any, domain: str, wait: float = 20.0, *, empty_document: bool = False
) -> dict | None:
    """Quit the application and check that it went.

    A call cut short by a timeout can leave a question up -- a snapshot that had
    asked Designer to switch projects left "close all open documents?" on screen --
    and `Quit` then returns without quitting: the stop reported success with
    Designer still running behind the dialog. The known questions are answered while
    it quits, and an application still registered afterwards is an error that names
    what holds it.
    """
    if empty_document:
        # Public session closure has already observed a saved/closed document.
        # Never answer GUI questions, acquire another COM application, or infer
        # process exit from ROT unregistration. The caller releases all of its
        # COM references before waiting on the original Windows process handle.
        try:
            app.Quit()
        except Exception as exc:
            raise AdapterError(
                "E_VERIFY_INCOMPLETE",
                "Quit was dispatched once but its reply was not received",
                {
                    "quit_dispatched": True,
                    "quit_reply_received": False,
                    "cause_type": type(exc).__name__,
                },
            ) from exc
        return {"quit_dispatched": True, "quit_reply_received": True, "quit_count": 1}
    rules, process = (
        (DESIGNER_PROMPTS, "viewdraw.exe")
        if domain == "schematic"
        else (LAYOUT_PROMPTS, "expeditionpcb.exe")
    )
    with _PromptAnswerer(rules, process_name=process) as prompts:
        app.Quit()
        deadline = time.monotonic() + wait
        running = _still_registered(client, domain)
        while running and time.monotonic() < deadline:
            time.sleep(1.0)
            running = _still_registered(client, domain)
    if running:
        name = "Designer" if domain == "schematic" else "Layout"
        raise AdapterError(
            "E_CONFLICT",
            f"Xpedition {name} did not quit",
            {
                "domain": domain,
                "blocking_dialogs": prompts.blocking_dialogs(),
                "hint": f"answer or close the dialog in {name}, then session close again",
                "_untrusted": ["blocking_dialogs"],
            },
        )


def _open_designer_project(app: Any, params: dict[str, Any]) -> Any:
    path = params.get("project") or params.get("path")
    if not path:
        return None
    if os.environ.get("XPEDITION_OBSERVE_ONLY") == "1" or params.get("observe_only"):
        current = (
            _designer_project_path(app)
            if _owner_application() is not None
            else str(_com_member(_com_member(app, "GetProjectData"), "GetProjectFilePath") or "")
        )
        if not current or Path(current).resolve() != Path(str(path)).expanduser().resolve():
            raise AdapterError(
                "E_CONFLICT",
                "observation requires this Designer project to be already open",
                {
                    "requested_project": str(path),
                    "open_project": current,
                    "next_action": (
                        "open the owned project through an explicit confirmed session attachment"
                    ),
                },
            )
        return _value(app, "ActiveDocument", default=None)
    if _owner_application() is not None:
        _ensure_project(app, Path(str(path)).expanduser().resolve())
        return app.ActiveDocument
    try:
        return _open_project_answering(app, str(Path(str(path)).expanduser().resolve()))
    except Exception as exc:
        raise _com_error(exc, "open_designer_project") from exc


def _wait_for_native_ready(
    process: subprocess.Popen[Any], client: Any, domain: str, timeout: float
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            unsigned_code = int(return_code) & 0xFFFFFFFF
            raise AdapterError(
                "E_SERVER",
                "Xpedition exited during startup",
                {
                    "domain": domain,
                    "exit_code": return_code,
                    "exit_code_hex": f"0x{unsigned_code:08X}",
                    "hint": "inspect the Xpedition Application Error event for the faulting module",
                },
            )
        try:
            if domain == "schematic":
                _viewdraw_active(client)
            else:
                _active_object(client)
            return True
        except AdapterError:
            # Startup may require human licensing. Never press unrelated instances'
            # dialogs or arbitrary default buttons while waiting for COM.
            time.sleep(0.5)
    return False


JOB_WIZARD_TIMEOUT = 240.0
# EPcbUnit: 0 current, 2 mils, 3 inch, 4 mm, 5 µm (win32com constants epcbUnit*)
UNIT_CODES = {"current": 0, "mils": 2, "mil": 2, "th": 2, "inch": 3, "mm": 4, "um": 5}
UNIT_MM = 4
# ProjectIntegration.ForwardAnnotationStatus: 1 required, 2 in synch, 3 no CES data
PRJINT_IN_SYNCH = 2
# Layout takes about a minute from the launcher to a registered automation object.
LAUNCH_WAIT = 150.0
LAYOUT_TEMPLATE_ROOT = (
    "standard",
    "templates",
    "dxdesigner",
    "TemplateLibrary",
    "Templates",
    "Layout",
)
# Layout's own questions while a board opens or annotates, and the answer an unattended
# run gives: a stale lock from a killed session is opened, the recovery box loads the
# database the user last saved, and the offer to forward-annotate is declined so that
# `forward_annotate` stays an explicit step.
LAYOUT_PROMPTS: tuple[tuple[str, str, str | None], ...] = (
    ("确定设计状态", "Open", None),
    ("design status", "Open", None),
    ("数据库恢复", "确定", "用户"),
    ("database recovery", "OK", "user"),
    ("正向标注", "否(N)", None),
    ("forward annotat", "No", None),
)
# The same prompts when the board is opened in order to annotate it: Layout's own
# "annotate now?" is answered Yes, because an annotation Layout starts itself
# succeeds where the explicit call after a declined prompt fails in the packaging
# phase.
LAYOUT_PROMPTS_ANNOTATE: tuple[tuple[str, str, str | None], ...] = tuple(
    (marker, ("是(Y)" if button == "否(N)" else "Yes" if button == "No" else button), option)
    for marker, button, option in LAYOUT_PROMPTS
)
# Designer asks before it switches projects while sheets are open; an unattended
# run closes them (they were saved by the operation that opened them).
DESIGNER_PROMPTS: tuple[tuple[str, str, str | None], ...] = (
    ("更改当前项目", "Yes", None),
    ("change the current project", "Yes", None),
    ("Cannot create project", "OK", None),
)


def _start_by_launcher(client: Any, domain: str, wait: float = LAUNCH_WAIT) -> Any:
    """Start the application through its launcher and wait for its automation object.

    An instance that COM activation starts never registers itself in the running
    object table, so nothing can attach to it afterwards; one started through the
    launcher in `common/win64/bin` does, once its start-up (about a minute for
    Layout) is over. None when the launcher is not installed.
    """
    if domain == "schematic":
        _require_dedicated_designer()
    try:
        executable, working_directory = _native_executable(domain)
    except AdapterError:
        return None
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # detached, own group
    try:
        process = subprocess.Popen(
            [str(executable)],
            cwd=str(working_directory),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except OSError as exc:
        raise _com_error(exc, "start_xpedition") from exc
    if not _wait_for_native_ready(process, client, domain, wait):
        raise AdapterError(
            "E_TIMEOUT",
            "the application did not register its automation object in time",
            {"domain": domain, "seconds": wait, "pid": process.pid},
        )
    return _viewdraw_active(client) if domain == "schematic" else _active_object(client)


_PACKAGER_ERROR = "     ERROR:"


def _packager_messages(log_path: Path) -> tuple[list[str], str]:
    """Pull the error lines and the verdict out of the packager report."""
    try:
        raw = log_path.read_bytes()
    except OSError:
        return [], ""
    text = _tool_text(raw)
    errors = [
        line.strip()
        for line in text.splitlines()
        if line.startswith(_PACKAGER_ERROR) or line.lstrip().startswith("ERROR:")
    ]
    verdict = ""
    for line in text.splitlines():
        if "has NOT been packaged" in line or "terminating" in line.lower():
            verdict = line.strip()
    return errors, verdict


def _package_design(params: dict[str, Any]) -> dict[str, Any]:
    """Run the forward-annotation packager without a GUI.

    `Package Design for Layout` in Designer launches `packagerui.exe`, a separate GUI
    process that waits for a human. The same work is done by `package.exe`, which is a
    console program — but it must be started through the launcher in `common/win64/bin`,
    not the real binary under `wg/win64/bin`: launched directly it cannot initialise its
    Qt platform plugin and puts up a message box instead.
    """
    project = params.get("project") or params.get("path")
    if not project:
        raise AdapterError("E_USAGE", "package requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    if not project_path.is_file():
        raise AdapterError("E_NOT_FOUND", "project file was not found", {"path": str(project_path)})
    from .project_file import pdb_path_repairs, pdb_registration_repairs

    repairs = pdb_path_repairs(project_path)
    registrations = pdb_registration_repairs(project_path)
    if repairs != (params.get("registration_repairs") or []):
        raise AdapterError("E_CONFLICT", "Parts database references changed after planning")
    if registrations != (params.get("partition_registrations") or []):
        raise AdapterError("E_CONFLICT", "Parts database registration changed after planning")
    if repairs or registrations:
        _pythoncom, client = _import_com()
        app, reopen = _release_project(client, project_path)
        text = project_path.read_text(encoding="utf-8", errors="surrogateescape")
        for repair in repairs:
            text = text.replace('VALUE "' + repair["from"] + '"', 'VALUE "' + repair["to"] + '"')
        project_path.write_text(text, encoding="utf-8", errors="surrogateescape")
        lmc, root = _central_library(project_path)
        work = root / "Work/xpedition-cli"
        work.mkdir(parents=True, exist_ok=True)
        try:
            for item in registrations:
                hkp = work / (item["partition"] + "-register.hkp")
                exported = _run_library_tool(
                    "PartsDB2HKP", ["-i", item["parts_database"], "-o", str(hkp), "-u", "mm", "-a"]
                )
                if exported["exit_code"] or exported["dialogs"] or not hkp.is_file():
                    raise AdapterError(
                        "E_SERVER",
                        "Cannot export partition for registration",
                        {"partial": True, "partition": item["partition"], "native": exported},
                    )
                registered = _run_library_tool(
                    "HKP2PartsDB",
                    [
                        "-c",
                        str(lmc),
                        "-i",
                        str(hkp),
                        "-o",
                        item["parts_database"],
                        "-r",
                        "-l",
                        str(work / (item["partition"] + "-register.log")),
                    ],
                )
                if registered["exit_code"] or registered["dialogs"]:
                    raise AdapterError(
                        "E_SERVER",
                        "Native partition registration failed",
                        {
                            "partial": True,
                            "partition": item["partition"],
                            "native": registered,
                            "log": _tool_log(work / (item["partition"] + "-register.log")),
                        },
                    )
        finally:
            if reopen:
                _ensure_project(app, project_path)
    sdd_home = _configure_environment()
    if sdd_home is None:
        raise AdapterError("E_CONFIG", "Xpedition SDD_HOME could not be discovered")
    executable = sdd_home / "common" / "win64" / "bin" / "package.exe"
    if not executable.is_file():
        raise AdapterError("E_NOT_FOUND", "packager was not found", {"path": str(executable)})
    design = str(params.get("design") or "Board1")
    # `-Replace` refreshes the packager's own copy of every needed part from the project
    # PDBs; `-Add` (the tool's default) keeps whatever an earlier run cached in
    # `Integration/LocalPartsDB.pdb`, so a rebuilt library would never be seen.
    mode = str(params.get("mode") or "Replace")
    if mode not in ("Add", "Refresh", "Replace", "CleanBuild"):
        raise AdapterError("E_USAGE", "package mode must be Add, Refresh, Replace or CleanBuild")
    log_path = project_path.parent / "Integration" / "PartPkg.log"
    try:
        # a clone may carry the template's old report; only this run's log counts
        log_path.unlink()
    except OSError:
        pass
    environment = os.environ.copy()
    environment.setdefault(
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        str(sdd_home / "common" / "win64" / "lib" / "plugins" / "platforms"),
    )
    try:
        started_ns = time.time_ns()
        completed = subprocess.run(
            [str(executable), f"-j{project_path}", f"-n{design}", f"-{mode}"],
            cwd=str(project_path.parent),
            env=environment,
            capture_output=True,
            timeout=float(params.get("timeout", 900.0)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError("E_TIMEOUT", "packager timed out", {"design": design}) from exc
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot start the packager: {exc}") from exc
    if not log_path.exists() and completed.stdout:
        # the packager echoes its whole report to stdout ("Copied from Log File")
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_bytes(completed.stdout)
        except OSError:
            pass
    errors, verdict = _packager_messages(log_path)
    warnings = 0
    try:
        warnings = sum(
            1
            for line in _tool_text(log_path.read_bytes()).splitlines()
            if line.lstrip().startswith("WARNING:")
        )
    except OSError:
        warnings = 0
    return {
        "packaged": completed.returncode == 0 and not errors,
        "exit_code": completed.returncode,
        "design": design,
        "mode": mode,
        "log_path": str(log_path),
        "errors": errors,
        "warnings": warnings,
        "verdict": verdict,
        "started_ns": started_ns,
        "log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest()
        if log_path.is_file()
        else None,
        "_untrusted": ["errors", "verdict", "log_path"],
    }


_SCH2PDF_COLORS = {
    0: "black on white, black text suppressed",
    1: "colour on white",
    2: "colour on black",
    3: "black on white with black text",
    4: "colour on white with coloured text",
}


def _sch2pdf_sheets(stdout: str) -> list[str]:
    """`sch2pdf` reports every rendered sheet as `Printed <schematic> Sheet <n>`."""
    sheets: list[str] = []
    for line in stdout.splitlines():
        text = line.strip()
        if text.startswith("Printed "):
            sheets.append(text[len("Printed ") :])
    return sheets


def _export_pdf(params: dict[str, Any]) -> dict[str, Any]:
    """Render a Designer project to PDF with the stock `sch2pdf` console program.

    Designer's own `Generate PDF` command (34622) is a dialog. `sch2pdf` under
    `common/win64/bin` does the same rendering headless and reads the project
    database directly, so it works while Designer still has the project open. It
    prints only what lies inside each sheet's border: objects placed outside the
    border are silently absent from the PDF.
    """
    project = params.get("project") or params.get("path")
    if not project:
        raise AdapterError("E_USAGE", "export_pdf requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    if not project_path.is_file():
        raise AdapterError("E_NOT_FOUND", "project file was not found", {"path": str(project_path)})
    output = params.get("output") or project_path.with_suffix(".pdf")
    output_path = Path(str(output)).expanduser().resolve()
    if output_path.suffix.lower() != ".pdf":
        raise AdapterError(
            "E_VALIDATION", "export_pdf output must end in .pdf", {"path": str(output_path)}
        )
    if output_path.exists() and not bool(params.get("replace", False)):
        raise AdapterError(
            "E_CONFLICT",
            "output file already exists",
            {"path": str(output_path), "hint": "--replace, or another --output"},
        )
    try:
        color = int(params.get("color", 1))
    except (TypeError, ValueError) as exc:
        raise AdapterError("E_VALIDATION", "color must be an integer from 0 to 4") from exc
    if color not in _SCH2PDF_COLORS:
        raise AdapterError(
            "E_VALIDATION", "color must be an integer from 0 to 4", {"choices": _SCH2PDF_COLORS}
        )
    sdd_home = _configure_environment()
    if sdd_home is None:
        raise AdapterError("E_CONFIG", "Xpedition SDD_HOME could not be discovered")
    executable = sdd_home / "common" / "win64" / "bin" / "sch2pdf.exe"
    if not executable.is_file():
        raise AdapterError("E_NOT_FOUND", "sch2pdf was not found", {"path": str(executable)})
    from .schematic_pdf_artifacts import decode_pdf, file_pin

    # The outer adapter deadline reserves time for serialization. A converter
    # receives only the remaining child allowance, never the parent's 600s.
    try:
        child_seconds = float(params.get("_native_process_timeout_seconds", 585.0))
    except (TypeError, ValueError) as exc:
        raise AdapterError(
            "E_VALIDATION", "Invalid sch2pdf child budget", {"write_attempted": False}
        ) from exc
    if not math.isfinite(child_seconds) or not 0 < child_seconds <= 585:
        raise AdapterError(
            "E_VALIDATION",
            "sch2pdf child budget is outside its bound",
            {"write_attempted": False},
        )
    source_before, producer_before = file_pin(project_path), file_pin(executable)
    expected = params.get("_pdf_expectation")
    generation = params.get("artifact_generation")
    if isinstance(expected, dict):
        old_output = file_pin(output_path) if output_path.is_file() else None
        if (
            expected.get("project") != source_before
            or expected.get("producer") != producer_before
            or expected.get("output") != str(output_path)
            or expected.get("previous_output") != old_output
            or expected.get("color") != color
            or expected.get("schematic") != (params.get("schematic") or None)
            or not generation
            or expected.get("generation_id") != generation
        ):
            raise AdapterError(
                "E_CONFLICT",
                "PDF inputs or signed invocation changed before producer dispatch",
                {"write_attempted": False},
            )
    arguments = [
        str(executable),
        "-project",
        str(project_path),
        "-a",
        str(output_path),
        "-c",
        str(color),
    ]
    if params.get("schematic"):
        arguments += ["-schematic", str(params["schematic"])]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    producer_started = time.monotonic()
    owned_producer = _owner_application() is not None
    try:
        if owned_producer:
            from .designer_owner_dispatch import run_pdf_producer

            produced = run_pdf_producer(arguments, executable.parent)
            completed = subprocess.CompletedProcess(
                arguments,
                produced["returncode"],
                stdout=produced["stdout"],
                stderr=produced["stderr"],
            )
        else:
            completed = subprocess.run(
                arguments,
                cwd=str(executable.parent),
                capture_output=True,
                timeout=child_seconds,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(
            "E_TIMEOUT",
            "sch2pdf timed out; the converter was not replayed",
            {
                "project": str(project_path),
                "output": str(output_path),
                "failed_stage": "sch2pdf_producer",
                "producer_dispatched": True,
                "producer_reply_received": False,
                "child_budget_seconds": child_seconds,
                "producer_wall_seconds": round(time.monotonic() - producer_started, 3),
                "generation_id": generation,
                "write_attempted": True,
            },
        ) from exc
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot start sch2pdf: {exc}") from exc
    producer_wall_seconds = round(time.monotonic() - producer_started, 3)
    inspection_started = time.monotonic()
    stdout = completed.stdout.decode("utf-8", "replace")
    exported = completed.returncode == 0 and output_path.is_file()
    from . import pdf_pages

    # each page's size, read back from the file: a page smaller than its border is
    # printed clipped, and nothing else says so
    pages = []
    validation: dict[str, Any] = {"status": "not_generated"}
    artifact = None
    if output_path.is_file():
        output_pin = file_pin(output_path)
        # Keep the historic page-size diagnostic even for malformed output.
        # It is producer evidence only; independent PDF parsing below is a
        # separate verdict and never accepts this regex as structural proof.
        with output_path.open("rb") as stream:
            reported_bytes = stream.read(output_pin["bytes"] + 1)
        if len(reported_bytes) == output_pin["bytes"]:
            pages = pdf_pages.pages(reported_bytes)
        try:
            decoded = decode_pdf(output_path)
            pages = [
                {
                    key: item[key]
                    for key in (
                        "page",
                        "width_pt",
                        "height_pt",
                        "width_mm",
                        "height_mm",
                        "orientation",
                    )
                }
                for item in decoded["pages"]
            ]
            validation = {"status": "passed", "decoded": decoded}
        except Exception as exc:
            validation = {
                "status": "failed",
                "cause_type": type(exc).__name__,
                "scope": "bounded PDF structure; no pixel/visual acceptance",
            }
        artifact = {
            "generation_id": generation,
            "project": str(project_path),
            "output": str(output_path),
            "sha256": output_pin["sha256"],
            "source_project_before": source_before,
            "source_project_after": file_pin(project_path),
            "producer_before": producer_before,
            "producer_after": file_pin(executable),
            "invocation": {"argv": arguments, "cwd": str(executable.parent), "shell": False},
            "printed_sheets": _sch2pdf_sheets(stdout),
        }
    return {
        "exported": exported,
        "format": "pdf",
        "path": str(output_path),
        "size": output_path.stat().st_size if output_path.is_file() else 0,
        "sheets": _sch2pdf_sheets(stdout),
        "pages": pages,
        "warnings": pdf_pages.warnings(pages),
        "exit_code": completed.returncode,
        "artifact": artifact,
        "producer_validation": validation,
        "producer_timing": {
            "child_budget_seconds": None if owned_producer else child_seconds,
            "observation_budget_seconds": child_seconds if owned_producer else None,
            "deadline_policy": "bounded_client_observation_no_producer_cancellation"
            if owned_producer
            else "legacy_subprocess_timeout",
            "wall_seconds": producer_wall_seconds,
            "artifact_inspection_seconds": round(time.monotonic() - inspection_started, 3),
            "profile_id": params.get("_native_budget_profile_id"),
        },
        "messages": [line.strip() for line in stdout.splitlines() if line.strip()][-8:],
        "_untrusted": ["path", "sheets", "messages", "artifact", "producer_validation"],
    }


_NEW_SHEET_COMMAND = 34165
_SELECT_ALL_COMMAND = 57642


def _warn_when_the_library_is_missing(
    warnings: list[dict[str, Any]], project_path: Path, partition: str, ops: list[Any]
) -> None:
    """Note a draw that places parts the library has no parts database for.

    Designer draws a part instance's value itself when the library has no part
    for it -- once rotated beside the body and once horizontally, the horizontal
    copy landing on the reference designator -- and its own "Text alignment"
    graphical check then fires on every such part. Building the library clears
    both. The draw itself is correct, so this is a note, not a failure.
    """
    if not any(isinstance(op, dict) and op.get("op") == "place_part" for op in ops):
        return
    try:
        parts_db = _symbol_library_root(project_path).parent / "PartsDBLibs" / f"{partition}.pdb"
    except AdapterError:
        return
    if parts_db.is_file():
        return
    warnings.append(
        {
            "check": "library_not_built",
            "partition": partition,
            "parts_database": str(parts_db),
            "message": (
                f"the {partition} parts database does not exist, so Designer draws each "
                "part's value itself -- twice, the horizontal copy over the reference "
                "designator -- and its Text alignment check fires on every such part; "
                "run library materialize, then schematic package"
            ),
        }
    )


def _symbol_library_root(project_path: Path) -> Path:
    """`SymbolLibs` beside the central library the project's `.prj` points at."""
    try:
        text = project_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot read the project file: {exc}") from exc
    for line in text.splitlines():
        if line.startswith("KEY CentralLibrary "):
            value = line[len("KEY CentralLibrary ") :].strip().strip('"')
            lmc = Path(value)
            if not lmc.is_absolute():
                lmc = project_path.parent / lmc
            return lmc.parent / "SymbolLibs"
    raise AdapterError(
        "E_NOT_FOUND", "the project names no CentralLibrary", {"project": str(project_path)}
    )


def _settle(seconds: float) -> None:
    time.sleep(seconds)


def _ensure_project(app: Any, project_path: Path, *, lifecycle: dict | None = None) -> None:
    """Open `project_path` in Designer unless it is the project already open."""
    try:
        opened = _native_call("ensure.IsProjectOpened", lambda: app.IsProjectOpened())
        if type(opened) is not bool:
            raise AdapterError("E_HUMAN_REQUIRED", "Designer open state is unavailable")
        if lifecycle is not None and opened is not False:
            raise AdapterError("E_CONFLICT", "Display reopen requires an observed closed project")
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "observe_designer_open_state") from exc
    # Ask for the open project's path whatever IsProjectOpened() claimed. After a
    # call times out it reports false with the project still open, and opening a
    # project Designer already has is refused with 64185 -- whose text is about
    # scripts and GUIs and points nowhere near the cause. The path is the
    # trustworthy answer, so read it first and believe it.
    current = ""
    try:
        current = str(
            _native_call(
                "ensure.GetProjectFilePath",
                lambda: (
                    _designer_project_path(app)
                    if _owner_application() is not None
                    else _com_member(
                        _native_call(
                            "ensure.GetProjectData", lambda: _com_member(app, "GetProjectData")
                        ),
                        "GetProjectFilePath",
                    )
                ),
            )
            or ""
        )
    except Exception as exc:
        if opened:
            raise _com_error(exc, "observe_designer_project_path") from exc
        current = ""
    same = False
    if opened and current:
        try:
            same = Path(current).resolve() == project_path
        except OSError:
            same = False
    if not same:
        if _owner_application() is not None and (
            opened or (current and Path(current).resolve() != project_path)
        ):
            raise AdapterError(
                "E_CONFLICT",
                "The held Designer has a different or unclosed project; "
                "no project switch was dispatched",
                {"write_attempted": False},
            )
        if os.environ.get("XPEDITION_OBSERVE_ONLY") == "1":
            raise AdapterError(
                "E_CONFLICT",
                "observation cannot switch Designer projects",
                {"requested_project": str(project_path), "open_project": current},
            )
        try:
            if opened:
                if not current:
                    raise AdapterError(
                        "E_HUMAN_REQUIRED", "Open Designer project identity is unavailable"
                    )
                _close_if_open(app, Path(current).resolve())
            if lifecycle is not None:
                lifecycle["reopen_count"] += 1
            _native_call(
                "ensure.OpenProject", lambda: _open_project_answering(app, str(project_path))
            )
            _settle(6.0)
        except AdapterError:
            raise
        except Exception as exc:
            error = _com_error(exc, "open_project")
            error.details.setdefault("requested_project", str(project_path))
            if current:
                error.details.setdefault("open_project", current)
                error.details.setdefault("_untrusted", []).append("open_project")
            raise error from exc
    if _owner_application() is not None:
        from .designer_owner_dispatch import _CURRENT, _project_context

        _grant, owner = _project_context(app, project_path)
        owner.confirm_project_open()
        if _CURRENT.get()[1] in {"open", "reopen_project"}:
            return  # Opening a project never silently creates/activates a sheet.
    try:
        if _native_call("ensure.ActiveView", lambda: app.ActiveView) is None:
            if os.environ.get("XPEDITION_OBSERVE_ONLY") == "1":
                raise AdapterError("E_CONFLICT", "observation cannot open a Designer view")
            active = str(
                _native_call(
                    "ensure.GetActiveDesign",
                    lambda: (
                        _designer_declared_call(
                            getattr(app, "_oleobj_", app), "GetActiveDesign", 229, 8
                        )
                        if _owner_application() is not None
                        else app.GetActiveDesign()
                    ),
                )
                or ""
            )
            designs = _native_call(
                "ensure.GetiCDBDesigns",
                lambda: _designer_design_names(app),
            )
            if _owner_application() is not None and designs and active not in designs:
                raise AdapterError("E_CONFLICT", "No observed active native design can be opened")
            if active and active in designs:
                _native_call(
                    "ensure.Documents.Open",
                    lambda: (
                        _designer_open_design(app, active)
                        if _owner_application() is not None
                        else app.Documents.Open(active)
                    ),
                )
                _settle(3.0)
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "open_design_view") from exc


def _reopen_project(app: Any, project_path: Path) -> None:
    """Sheet operations leave `DesignComponents` unusable until the project is reopened."""
    try:
        if not _close_if_open(app, project_path) and app.IsProjectOpened() is not False:
            raise AdapterError("E_CONFLICT", "Another Designer project is open; reopen stopped")
        _native_call("reopen.OpenProject", lambda: _open_project_answering(app, str(project_path)))
        _settle(8.0)
        if _native_call("reopen.ActiveView", lambda: app.ActiveView) is None:
            _native_call(
                "reopen.Documents.Open",
                lambda: (
                    _designer_open_design(app, _designer_active_design(app))
                    if _owner_application() is not None
                    else app.Documents.Open(
                        _native_call("reopen.GetActiveDesign", lambda: app.GetActiveDesign())
                    )
                ),
            )
            _settle(3.0)
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "reopen_project") from exc


CLONE_IGNORE = (
    "Templates",
    "Work",
    "LogFiles",
    "ProjectBackup",
    "Thumbnail",
    "*.bak",
    "PartPkg.log",
)


# Designer ships these and they carry no parts database of their own.
_STOCK_SYMBOL_PARTITIONS = frozenset({"globals", "builtin", "borders"})


def _repair_missing_parts_databases(project_path: Path) -> dict[str, Any]:
    """Give every user symbol partition a parts database, or report the ones without.

    Designer walks a design's symbol partitions when it places a part. A partition
    whose parts database is missing raises a modal dialog, which holds the draw
    open until a human clicks -- and a template can carry partitions whose `.pdb`
    was never copied: one clone listed three.

    An empty parts database is a stock artefact, so a missing one is filled by
    copying an unused stock database out of the project's own library. It is only
    copied when at least two unused databases are byte-identical, which is what
    makes "this file is an empty database" checkable rather than guessed. A
    library where that does not hold is reported and left alone.
    """
    from . import project_file

    report: dict[str, Any] = {"created": [], "registered": [], "missing": []}
    try:
        _, root = _central_library(project_path)
    except AdapterError:
        return report
    parts_dir = root / "PartsDBLibs"
    try:
        text = project_path.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return report
    # The project file spells these with backslashes whatever the host is.
    declared = {
        PureWindowsPath(entry).name.lower() for entry in project_file.list_entries(text, "PDBs")
    }
    wanted: list[str] = []
    for entry in project_file.list_entries(text, "Symbols"):
        name = PureWindowsPath(entry).name
        if name and name.lower() not in _STOCK_SYMBOL_PARTITIONS:
            wanted.append(name)

    def empty_database() -> bytes | None:
        """The bytes shared by two or more parts databases this design does not use."""
        if not parts_dir.is_dir():
            return None
        seen: dict[bytes, int] = {}
        for path in sorted(parts_dir.glob("*.pdb")):
            if path.name.lower() in declared:
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            seen[content] = seen.get(content, 0) + 1
        for content, count in seen.items():
            if count >= 2:
                return content
        return None

    donor: bytes | None = None
    changed = False
    for name in wanted:
        target = parts_dir / f"{name}.pdb"
        if not target.is_file():
            if donor is None:
                donor = empty_database()
            if donor is None:
                report["missing"].append(name)
                continue
            try:
                parts_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(donor)
            except OSError:
                report["missing"].append(name)
                continue
            report["created"].append(str(target))
        if target.name.lower() not in declared:
            text, added = project_file.add_entry(text, "PDBs", f"PartsDBLibs\\{target.name}")
            if added:
                declared.add(target.name.lower())
                report["registered"].append(target.name)
                changed = True
    if changed:
        try:
            project_path.write_text(text, encoding="utf-8", errors="surrogateescape")
        except OSError:
            pass
    return report


def _clone_project(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Create a project by copying a template project folder under a new name.

    Stock setup templates require native NewProject to initialize iCDB; a text
    LIST Designs alone does not initialize the database. Initialized projects
    retain the folder-clone path. Only the new owned project is saved/reopened.
    """
    import shutil

    template = params.get("template")
    project = params.get("project") or params.get("path")
    if not template or not project:
        raise AdapterError("E_USAGE", "clone_project requires template and project paths")
    template_path = Path(str(template)).expanduser().resolve()
    target_path = Path(str(project)).expanduser().resolve()
    if template_path.suffix.lower() != ".prj" or not template_path.is_file():
        raise AdapterError(
            "E_NOT_FOUND", "template project file was not found", {"template": str(template_path)}
        )
    if target_path.suffix.lower() != ".prj":
        raise AdapterError(
            "E_USAGE", "the new project path must end in .prj", {"project": str(target_path)}
        )
    if not str(target_path).isascii():
        raise AdapterError(
            "E_VALIDATION",
            "the new project path must be ASCII: Designer cannot load new symbol files "
            "from a folder whose path has other characters",
            {"project": str(target_path)},
        )
    source_dir = template_path.parent
    target_dir = target_path.parent
    from . import project_file

    state = project_file.template_state(template_path)
    if state["designs"] and not state["initialized"]:
        raise AdapterError(
            "E_PROJECT_INVALID",
            "Template declares designs without an initialized iCDB; "
            "it cannot be opened as a project",
            {
                "template": str(template_path),
                "database": state["database"],
                "write_attempted": False,
            },
        )
    requested_design = str(params.get("design") or "Board1")
    if params.get("design") and state["initialized"] and requested_design not in state["designs"]:
        raise AdapterError(
            "E_VALIDATION",
            "An initialized template retains its existing designs",
            {"designs": state["designs"], "write_attempted": False},
        )
    if target_path.exists() or (target_dir.exists() and any(target_dir.iterdir())):
        raise AdapterError(
            "E_CONFLICT", "the new project folder is not empty", {"path": str(target_dir)}
        )
    if source_dir == target_dir or source_dir in target_dir.parents:
        raise AdapterError("E_USAGE", "the new project cannot live inside the template folder")
    app: Any = None
    try:
        app = _native_call("create.attach", lambda: _viewdraw_application(client, attach_only=True))
    except AdapterError:
        app = None
    if app is None:
        raise AdapterError(
            "E_BACKEND_UNAVAILABLE",
            "Project creation requires an identified existing Designer instance",
            {"write_attempted": False},
        )
    template_closed = False
    if app is not None:
        try:
            opened = _native_call("create.IsProjectOpened", lambda: app.IsProjectOpened())
            if type(opened) is not bool:
                raise AdapterError("E_HUMAN_REQUIRED", "Designer state unavailable before creation")
            if opened:
                current = str(
                    _com_member(_com_member(app, "GetProjectData"), "GetProjectFilePath") or ""
                )
                if current and Path(current).resolve() == template_path:
                    template_closed = _close_if_open(app, template_path)
                else:
                    raise AdapterError(
                        "E_HUMAN_REQUIRED",
                        "Close the current owned project before creating another",
                        {"open_project": current, "write_attempted": False},
                    )
        except AdapterError:
            raise
        except Exception as exc:
            raise _com_error(exc, "close_template_project") from exc
    try:
        if state["creation_method"] == "native_new_project":
            library = project_file.central_library(template_path)
            if library is None or not library.is_file():
                raise AdapterError(
                    "E_PROJECT_INVALID",
                    "Setup template must reference a readable local central library",
                    {"write_attempted": False},
                )
            last_error_reader = None
            if _owner_application() is not None:
                from .designer_owner_dispatch import (
                    check_new_project_wrapper,
                    prepare_new_project_last_error,
                )

                check_new_project_wrapper(app)
                _native_call(
                    "create.document_members_preflight",
                    lambda: _designer_preflight_document_members(app),
                )
                last_error_reader = prepare_new_project_last_error(app)
            if target_dir.exists():
                # Previously checked empty; remove only this empty destination so
                # native NewProject owns its initial directory creation.
                target_dir.rmdir()
            with _designer_prompt_context():
                accepted = _native_call(
                    "create.NewProject",
                    lambda: app.NewProject(str(target_path), str(library), "", str(template_path)),
                )
            last_error = None
            if accepted is False and last_error_reader is not None:
                from .designer_owner_dispatch import capture_new_project_last_error

                last_error = capture_new_project_last_error(last_error_reader)
            observed = project_file.template_state(target_path) if target_path.is_file() else {}
            if accepted is not True or not observed.get("initialized"):
                raise AdapterError(
                    "E_PROJECT_INVALID",
                    "Native NewProject did not establish an initialized project",
                    {
                        "acknowledgement": accepted,
                        "observed": observed,
                        "partial": target_dir.exists(),
                        "write_attempted": True,
                        **(
                            {"new_project_last_error": last_error} if last_error is not None else {}
                        ),
                    },
                )
        ignored = (
            (*CLONE_IGNORE, template_path.name)
            if state["creation_method"] == "native_new_project"
            else CLONE_IGNORE
        )
        _native_call(
            "create.copytree",
            lambda: shutil.copytree(
                source_dir, target_dir, ignore=shutil.ignore_patterns(*ignored), dirs_exist_ok=True
            ),
        )
        if state["creation_method"] != "native_new_project":
            copied = target_dir / template_path.name
            if copied != target_path:
                copied.rename(target_path)
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot copy the template project: {exc}") from exc
    rewritten: list[dict[str, str]] = []
    relative_library_keys: list[dict[str, str]] = []
    try:
        text = target_path.read_text(encoding="utf-8", errors="surrogateescape")
        text, rewritten = project_file.relocate_library_references(text, source_dir, target_dir)
        if state["creation_method"] == "native_new_project":
            try:
                text, relative_library_keys = project_file.relative_owned_library_keys(
                    text, target_dir
                )
            except ValueError as error:
                raise AdapterError(
                    "E_PROJECT_INVALID",
                    "New project library references are not self-contained",
                    {"reason": str(error), "partial": True, "write_attempted": True},
                ) from error
        _native_call(
            "create.rewrite_prj",
            lambda: target_path.write_text(text, encoding="utf-8", errors="surrogateescape"),
        )
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot rewrite the project file: {exc}") from exc
    # A template can list symbol partitions whose parts database was never copied.
    # Designer raises a modal dialog for one of those the first time it places a
    # part, which blocks the draw; fix it now, while nothing is running.
    parts_databases = _native_call(
        "create.repair_parts_references", lambda: _repair_missing_parts_databases(target_path)
    )
    files = 0
    size = 0
    for path in target_dir.rglob("*"):
        if path.is_file():
            files += 1
            size += path.stat().st_size
    if state["creation_method"] == "native_new_project":
        _native_call("create.ensure_project", lambda: _ensure_project(app, target_path))
        if (
            _native_call(
                "create.AddiCDBDesign", lambda: _designer_add_design(app, requested_design)
            )
            is not True
        ):
            raise AdapterError(
                "E_PROJECT_INVALID",
                "Native initial design creation was refused",
                {"project": str(target_path), "partial": True, "write_attempted": True},
            )
    opened = False
    if app is not None and bool(params.get("open", True)):
        _native_call("create.ensure_project", lambda: _ensure_project(app, target_path))
        opened = True
    if not opened:
        raise AdapterError(
            "E_PROJECT_INVALID",
            "Created project was not loaded for native readback",
            {"partial": True, "write_attempted": True},
        )
    document = _native_call("create.ActiveDocument", lambda: app.ActiveDocument)
    if document is not None:
        _native_call(
            "create.Document.Save",
            lambda: _designer_save_document(app, document),
        )
    _native_call("create.reopen_project", lambda: _reopen_project(app, target_path))
    actual_path = _native_call(
        "create.final_path",
        lambda: (
            _designer_project_path(app)
            if _owner_application() is not None
            else str(_com_member(_com_member(app, "GetProjectData"), "GetProjectFilePath") or "")
        ),
    )
    actual_designs = _native_call(
        "create.final_designs",
        lambda: _designer_design_names(app),
    )
    expected_designs = state["designs"] if state["initialized"] else [requested_design]
    verified = (
        Path(actual_path).resolve() == target_path
        and actual_designs == expected_designs
        and project_file.template_state(target_path)["initialized"]
    )
    if not verified:
        raise AdapterError(
            "E_PROJECT_INVALID",
            "Reopened native project differs from the requested copy",
            {
                "project": str(target_path),
                "observed_path": actual_path,
                "observed_designs": actual_designs,
                "expected_designs": expected_designs,
                "partial": True,
                "write_attempted": True,
            },
        )
    if state["creation_method"] == "native_new_project":
        try:
            persisted = target_path.read_text(encoding="utf-8", errors="surrogateescape")
            project_file.relative_owned_library_keys(persisted, target_dir, require_relative=True)
            retained = True
        except (OSError, ValueError):
            retained = False
        if not retained:
            raise AdapterError(
                "E_VERIFY_INCOMPLETE",
                "Native save/close/reopen did not retain the new project's relative library keys",
                {"partial": True, "write_attempted": True, "native_reopen_returned": True},
            )
    files = 0
    size = 0
    for path in target_dir.rglob("*"):
        if path.is_file():
            files += 1
            size += path.stat().st_size
    return {
        "project": target_path.stem,
        "path": str(target_path),
        "template": str(template_path),
        "created": True,
        "files": files,
        "bytes": size,
        "rewritten": rewritten,
        **(
            {"relative_library_keys": relative_library_keys}
            if state["creation_method"] == "native_new_project"
            else {}
        ),
        "parts_databases": parts_databases,
        "template_closed": template_closed,
        "opened": opened,
        "creation_method": state["creation_method"],
        "persistence_verified": True,
        "verification": {
            "valid": True,
            "source": "native_getters_after_save_close_open",
            "expected": {"path": str(target_path), "designs": expected_designs},
            "observed": {"path": actual_path, "designs": actual_designs},
            "coverage": ["project_identity", "initialized_database", "design_names"]
            + (
                ["relative_library_keys_in_prj_after_native_reopen"]
                if state["creation_method"] == "native_new_project"
                else []
            ),
            "not_checked": ["full_template_object_equivalence"],
        },
        "_untrusted": ["project", "path", "template", "rewritten", "parts_databases"]
        + (["relative_library_keys"] if state["creation_method"] == "native_new_project" else []),
    }


_PROJECT_DATA_IID = "{956DD308-D5ED-40A1-83F2-84113B8937C1}"
_STRING_LIST_IID = "{3566B3FB-97C7-4F45-B1A6-E5469271F9AC}"
_DOCUMENTS_IID = "{BD2EDF77-7E28-11CE-822D-00001B4D36B5}"
_DOCUMENT_IID = "{4ADEF4E2-690A-11CE-9261-0020C5E26659}"
_SHEET_DOCUMENTS_IID = "{608CA7F4-A52A-4206-9580-3BEB17CE0073}"
_SHEET_DOCUMENT_IID = "{D8B95AF2-D943-4295-8D86-71B4F904C3AA}"
_IDISPATCH_IID = "{00020400-0000-0000-C000-000000000046}"
_IUNKNOWN_IID = "{00000000-0000-0000-C000-000000000046}"


def _designer_declared_call(raw, name, dispid, result_type, arguments=(), values=(), *, kind=1):
    """Current 151 METHOD only; never dynamic attribute/default dispatch."""
    member = _native_call("project_data." + name + ".member", lambda: raw.GetIDsOfNames(0, name))
    if type(member) is not int or member != dispid:
        raise AdapterError(
            "E_CONFLICT", "Designer project-data member mapping changed", {"member": name}
        )
    return _native_call(
        "project_data." + name,
        lambda: raw.InvokeTypes(dispid, 0, kind, (result_type, 0), arguments, *values),
    )


def _designer_typed_dispatch(value, iid):
    raw = getattr(value, "_oleobj_", value)
    if raw is None:
        raise AdapterError("E_NATIVE_FAILURE", "Designer project-data interface is unavailable")
    original = _native_call("project_data.canonical", lambda: raw.QueryInterface(_IUNKNOWN_IID))
    typed = _native_call(
        "project_data.typed_interface", lambda: raw.QueryInterface(iid, _IDISPATCH_IID)
    )
    repeated = _native_call(
        "project_data.typed_canonical", lambda: typed.QueryInterface(_IUNKNOWN_IID)
    )
    if original != repeated:
        raise AdapterError("E_CONFLICT", "Designer project-data canonical interface changed")
    return typed


def _designer_project_data(app):
    raw = getattr(app, "_oleobj_", app)
    # IVdApp and IVdAppProtectionProxy: DISPID228, METHOD, zero parameters.
    data = _designer_declared_call(raw, "GetProjectData", 228, 9)
    return _designer_typed_dispatch(data, _PROJECT_DATA_IID)


def _designer_design_names(app):
    """151 IProjectData9 -> IStringList1/2, with no makepy/default dependency.

    GetiCDBDesigns has cParams=cParamsOpt=0 and returns PTR IStringList, not
    optional input arguments. GetCount returns VT_INT (I4 in pywin32); GetItem
    takes one required VT_INT [in] and returns BSTR. Reject incomplete lists.
    """
    data = None
    try:
        data = _designer_project_data(app)
        result = _designer_declared_call(data, "GetiCDBDesigns", 9, 9)
        return _designer_string_names(result)
    finally:
        data = None


def _designer_string_names(result):
    strings = None
    try:
        strings = _designer_typed_dispatch(result, _STRING_LIST_IID)
        count = _designer_declared_call(strings, "GetCount", 1, 3)
        if type(count) is not int or not 0 <= count <= 64:
            raise AdapterError(
                "E_NATIVE_FAILURE", "Designer design census is not a bounded integer"
            )
        names = []
        for index in range(1, count + 1):
            name = _designer_declared_call(strings, "GetItem", 2, 8, ((3, 1),), (index,))
            if type(name) is not str or not name or len(name) > 4096 or name in names:
                raise AdapterError(
                    "E_NATIVE_FAILURE", "Designer design name is missing or duplicated"
                )
            names.append(name)
        repeated = _designer_declared_call(strings, "GetCount", 1, 3)
        if type(repeated) is not int or repeated != count:
            raise AdapterError("E_CONFLICT", "Designer design census changed during observation")
        return names
    finally:
        strings = None


def _designer_active_design(app):
    active = _designer_declared_call(getattr(app, "_oleobj_", app), "GetActiveDesign", 229, 8)
    if type(active) is not str or not active or active not in _designer_design_names(app):
        raise AdapterError(
            "E_CONFLICT", "Active native design is not in the observed design census"
        )
    return active


def _designer_documents(app):
    value = _designer_declared_call(getattr(app, "_oleobj_", app), "Documents", 1, 9, kind=2)
    return _designer_typed_dispatch(value, _DOCUMENTS_IID)


def _designer_open_design(app, design):
    if type(design) is not str or not design or design not in _designer_design_names(app):
        raise AdapterError("E_CONFLICT", "Only an observed native design can be opened")
    if _owner_application() is not None:
        from .designer_owner_dispatch import invoke_document_member

        return invoke_document_member(app, "open_design", design)
    documents = _designer_documents(app)
    opened = _designer_declared_call(documents, "Open", 7, 9, ((8, 1),), (design,))
    return _designer_typed_dispatch(opened, _DOCUMENT_IID)


def _designer_preflight_document_members(app):
    from .designer_owner_dispatch import preflight_document_members

    result = preflight_document_members(app)
    contracts = result.get("contracts") if isinstance(result, dict) else None
    cleanup = result.get("cleanup") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or result.get("scheme") != "designer-fixed-document-call-v1"
        or result.get("operation") != "preflight"
        or result.get("status") != "metadata_verified"
        or not isinstance(contracts, dict)
        or set(contracts) != {"open_design", "save_all", "save_document"}
        or not all(isinstance(contract, dict) and contract for contract in contracts.values())
        or result.get("invoke_attempted") is not False
        or not isinstance(cleanup, dict)
        or cleanup.get("status") != "observed"
        or cleanup.get("resources_released") is not True
    ):
        raise AdapterError(
            "E_CONFLICT",
            "Fixed Designer document preflight is incomplete",
            {"write_attempted": False},
        )
    return result


def _designer_save_document(app, document=None):
    if _owner_application() is not None:
        from .designer_owner_dispatch import invoke_document_member

        return invoke_document_member(app, "save_document")
    document = app.ActiveDocument if document is None else document
    return document.Save()


def _designer_save_all(app):
    if _owner_application() is not None:
        from .designer_owner_dispatch import invoke_document_member

        return invoke_document_member(app, "save_all")
    return app.Documents.SaveAll()


def _designer_project_path(app):
    data = _designer_project_data(app)
    try:
        path = _designer_declared_call(data, "GetProjectFilePath", 3, 8)
        if type(path) is not str or len(path) > 32768:
            raise AdapterError("E_NATIVE_FAILURE", "Native project path is unavailable")
        return path
    finally:
        data = None


def _designer_sheet_documents(app):
    value = _designer_declared_call(
        getattr(app, "_oleobj_", app), "SchematicSheetDocuments", 212, 9
    )
    return _designer_typed_dispatch(value, _SHEET_DOCUMENTS_IID)


def _designer_schematic_names(app):
    docs = _designer_sheet_documents(app)
    return _designer_string_names(_designer_declared_call(docs, "GetAvailableSchematics", 108, 9))


def _designer_sheet_names(app, schematic):
    if schematic not in _designer_schematic_names(app):
        raise AdapterError("E_NOT_FOUND", "The requested native schematic does not exist")
    docs = _designer_sheet_documents(app)
    return _designer_string_names(
        _designer_declared_call(docs, "GetAvailableSheets", 109, 9, ((8, 1),), (schematic,))
    )


def _designer_open_existing_sheet(app, schematic, number):
    if (
        type(number) is not int
        or number <= 0
        or str(number) not in _designer_sheet_names(app, schematic)
    ):
        raise AdapterError("E_NOT_FOUND", "The requested native sheet does not exist")
    docs = _designer_sheet_documents(app)
    value = _designer_declared_call(
        docs, "Open", 110, 9, ((8, 1), (8, 1)), (schematic, str(number))
    )
    return _designer_typed_dispatch(value, _SHEET_DOCUMENT_IID)


def _designer_add_design(app, name):
    """151 AddiCDBDesign36: one required BSTR [in], BOOL result."""
    data = _designer_project_data(app)
    try:
        result = _designer_declared_call(data, "AddiCDBDesign", 36, 11, ((8, 1),), (name,))
        if type(result) is not bool:
            raise AdapterError("E_NATIVE_FAILURE", "Designer AddiCDBDesign did not return BOOL")
        return result
    finally:
        data = None


def _string_list(value: Any) -> list[str]:
    """Designer's `IStringList`: `GetCount()` and 1-based `GetItem(i)`."""
    items: list[str] = []
    try:
        count = int(value.GetCount())
        for index in range(1, count + 1):
            items.append(str(value.GetItem(index)))
    except Exception:
        return items
    return items


def _schematic_name(app: Any) -> str:
    """The schematic that owns the sheets (`Schematic1` in a stock project)."""
    if _owner_application() is not None:
        names = _designer_schematic_names(app)
        if len(names) != 1:
            raise AdapterError("E_CONFLICT", "One unambiguous native schematic is required")
        return names[0]
    try:
        names = _string_list(app.SchematicSheetDocuments().GetAvailableSchematics())
        if names:
            return names[0]
    except Exception:
        pass
    try:
        for document in _items(app.SchematicSheetDocuments()):
            name = str(_value(document, "Name", default="") or "")
            if "." in name:
                return name.rpartition(".")[0]
    except Exception:
        pass
    return "Schematic1"


def _sheet_numbers(app: Any) -> dict[int, str]:
    """Every sheet of the schematic, not just the ones open in a window.

    `SchematicSheetDocuments` lists open documents only; `GetAvailableSheets`
    lists the sheets that exist.
    """
    schematic = _schematic_name(app)
    numbers: dict[int, str] = {}
    if _owner_application() is not None:
        for name in _designer_sheet_names(app, schematic):
            if not name.isascii() or not name.isdigit() or int(name) <= 0 or str(int(name)) != name:
                raise AdapterError(
                    "E_NATIVE_FAILURE", "Native sheet number is unavailable or ambiguous"
                )
            numbers[int(name)] = schematic
        return numbers
    try:
        for item in _string_list(app.SchematicSheetDocuments().GetAvailableSheets(schematic)):
            if item.isdigit():
                numbers[int(item)] = schematic
    except Exception as exc:
        raise _com_error(exc, "list_sheets") from exc
    if not numbers:
        try:
            for document in _items(app.SchematicSheetDocuments()):
                name = str(_value(document, "Name", default="") or "")
                base, _, number = name.rpartition(".")
                if number.isdigit():
                    numbers[int(number)] = base
        except Exception as exc:
            raise _com_error(exc, "list_sheets") from exc
    return numbers


def _open_sheet(app: Any, number: int) -> None:
    """Make sheet `number` active, creating sheets through Designer's New Sheet command.

    `SchematicSheetDocuments.Add` and `InsertSheet` fail with error 670 on current
    Xpedition Standard; the command works and activates the sheet it creates.
    """
    known = _sheet_numbers(app)
    attempts = 0
    while number not in known and attempts < 4:
        try:
            app.ExecuteCommandByID(_NEW_SHEET_COMMAND)
        except Exception as exc:
            raise _com_error(exc, "new_sheet") from exc
        _settle(1.5)
        known = _sheet_numbers(app)
        attempts += 1
    if number not in known:
        raise AdapterError(
            "E_BACKEND_UNAVAILABLE",
            f"sheet {number} could not be created",
            {"sheets": sorted(known)},
        )
    if _active_sheet(app) != number:
        _activate_sheet(app, number)
    if _active_sheet(app) != number:
        raise AdapterError(
            "E_CONFLICT",
            f"sheet {number} could not be activated",
            {"active_sheet": _active_sheet(app)},
        )


def _active_sheet(app: Any) -> int | None:
    try:
        return int(app.ActiveView.Block.SheetNum)
    except Exception:
        return None


def _activate_sheet(app: Any, number: int) -> None:
    """Bring sheet `number`'s window to the front.

    `SchematicSheetDocuments.Open` activates a sheet only the first time; once
    the window exists it returns without switching, so an already open sheet is
    activated through its document object.
    """
    schematic = _schematic_name(app)
    if _owner_application() is not None:
        document = _designer_open_existing_sheet(app, schematic, number)
        _designer_declared_call(document, "Activate", 105, 24)
        _settle(1.5)
        return
    wanted = f"{schematic}.{number}"
    try:
        for document in _items(app.SchematicSheetDocuments()):
            if str(_value(document, "Name", default="") or "") == wanted:
                document.Activate()
                _settle(0.8)
                return
        app.SchematicSheetDocuments().Open(schematic, str(number))
        _settle(1.5)
        for document in _items(app.SchematicSheetDocuments()):
            if str(_value(document, "Name", default="") or "") == wanted:
                document.Activate()
                _settle(0.8)
                return
    except Exception as exc:
        raise _com_error(exc, "activate_sheet") from exc


def _ensure_sheet(app: Any, number: int | None) -> None:
    """Make sheet `number` the active view again if another window took over.

    Changing a sheet's size or border can leave another sheet's window active,
    and every drawing call goes to the active view, so the invariant is checked
    before each operation.
    """
    if number is None or _active_sheet(app) == number:
        return
    _activate_sheet(app, number)
    current = _active_sheet(app)
    if current != number:
        raise AdapterError(
            "E_CONFLICT",
            f"sheet {number} is not the active sheet",
            {"active_sheet": current, "hint": "another sheet window took over; retry the draw"},
        )


def _draw_pin(app: Any, components: dict[str, Any], reference: Any) -> Any:
    if not reference:
        return None
    refdes, separator, number = str(reference).partition(".")
    if not separator:
        raise AdapterError(
            "E_CHANGESET_INVALID", f"pin reference {reference!r} is not REFDES.NUMBER"
        )
    component = components.get(refdes)
    if component is None:
        component = _designer_active_component(app, {"refdes": refdes})
        components[refdes] = component
    return _designer_pin(component, number)


def _designer_pin_identities(app: Any) -> dict[str, str]:
    """Pin -> net identity, using Designer's own `UID` (`$2N121`) for unlabelled nets.

    A label is the only name a net carries, so two pins joined by a bare wire share
    no name; the UID still tells them apart from every other net.
    """
    identities: dict[str, str] = {}
    try:
        design_name = str(_value(app, "GetActiveDesign", default="")() or "")
    except Exception:
        design_name = ""
    try:
        nets = _designer_collection(app, "DesignNets", design_name)
    except AdapterError:
        return identities
    for net in _items(nets):
        identity = _designer_net_name(net)
        if not identity:
            try:
                identity = str(_com_member(net, "UID") or "")
            except Exception:
                identity = ""
        if not identity:
            continue
        try:
            for connection in _items(_com_member(net, "Connections")):
                pin = _value(connection, "CompPin", default=None)
                component = _value(pin, "Component", "Parent", default=None)
                refdes = _value(component, "Refdes", "RefDes", "Name", default=None)
                number = _value(pin, "Number", default=None)
                if refdes and number is not None:
                    identities[f"{refdes}.{number}"] = identity
        except Exception:
            continue
    return identities


def _compare_netlist(
    snapshot: dict[str, Any], verify: dict[str, Any], identities: dict[str, str] | None = None
) -> dict[str, Any]:
    actual: dict[str, set[str]] = {}
    pin_net: dict[str, str] = dict(identities or {})
    for component in snapshot.get("components", []):
        refdes = str(component.get("refdes") or "")
        for pin in component.get("pins", []):
            net = pin.get("net")
            number = pin.get("number")
            if refdes and net and number is not None:
                ref = f"{refdes}.{number}"
                actual.setdefault(str(net), set()).add(ref)
                pin_net.setdefault(ref, str(net))
    differences: list[dict[str, Any]] = []
    expected = verify.get("nets") or {}
    for net, pins in expected.items():
        want = set(str(p) for p in pins)
        got = actual.get(str(net), set())
        if want != got:
            differences.append(
                {
                    "net": str(net),
                    "missing": sorted(want - got),
                    "unexpected": sorted(got - want),
                }
            )
    broken: list[list[str]] = []
    for link in verify.get("links") or []:
        if len(link) != 2:
            continue
        a, b = str(link[0]), str(link[1])
        if not pin_net.get(a) or pin_net.get(a) != pin_net.get(b):
            broken.append([a, b])
    extra, unplanned, joined = _beyond_the_plan(snapshot, verify, pin_net)
    return {
        "matches": not differences and not broken and not extra and not unplanned and not joined,
        "nets_checked": len(expected),
        "differences": differences,
        "links_checked": len(verify.get("links") or []),
        "links_broken": broken,
        "extra_nets": extra,
        "unplanned_components": unplanned,
        "no_connects_joined": joined,
    }


def _beyond_the_plan(
    snapshot: dict[str, Any], verify: dict[str, Any], pin_net: dict[str, str]
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """What the read-back holds that the plan does not ask for.

    Walking the expected nets finds a planned pin on the wrong net, but not a net the
    plan never named: a template's leftover wiring, a planned pin wired into an
    unnamed junction, a no-connect pin joined to something. Those come back here as
    nets beyond the plan (with at least one planned pin), parts the plan did not
    place, and no-connect pins that share a net with another pin.
    """
    expected = verify.get("nets") or {}
    parts = verify.get("parts")
    planned = (
        {str(item) for item in parts}
        if parts is not None
        # a request from before `parts` was sent: the parts its nets name
        else {str(pin).rsplit(".", 1)[0] for pins in expected.values() for pin in pins}
    )
    groups: dict[str, set[str]] = {}
    for ref, identity in pin_net.items():
        groups.setdefault(str(identity), set()).add(str(ref))
    linked = {
        frozenset((str(link[0]), str(link[1])))
        for link in verify.get("links") or []
        if len(link) == 2
    }
    extra: list[dict[str, Any]] = []
    for identity, pins in sorted(groups.items()):
        if identity in expected or len(pins) < 2:
            continue
        if not any(pin.rsplit(".", 1)[0] in planned for pin in pins):
            continue
        # an unnamed junction the plan asked for joins exactly the pins of its links
        if all(
            any(frozenset((pin, other)) in linked for other in pins if other != pin) for pin in pins
        ):
            continue
        extra.append({"net": identity, "pins": sorted(pins)})
    unplanned: list[str] = []
    if parts is not None:
        present = {
            str(component.get("refdes"))
            for component in snapshot.get("components", [])
            if component.get("refdes")
        }
        unplanned = sorted(present - planned)
    joined: list[dict[str, Any]] = []
    for ref in verify.get("no_connect") or []:
        identity = pin_net.get(str(ref))
        if identity is not None and len(groups.get(str(identity), ())) > 1:
            joined.append(
                {"pin": str(ref), "net": str(identity), "pins": sorted(groups[str(identity)])}
            )
    return extra, unplanned, joined


def sheet_size_taken(read_back: int, requested: int) -> bool:
    """Whether Designer holds the `VdSheetSize` a draw asked for.

    A landscape or portrait page code reads back as its plain size: VDSHEET_CL_SIZE
    (13) set, VDSHEET_CSIZE (2) read, and the page exports landscape all the same
    (1584 x 1224 pt, recorded 2026-09-29). Only the size can be checked this way; the
    export reads the orientation back from the PDF.
    """
    if read_back == requested:
        return True
    for first in (11, 21):  # the L family and the P family, each ten codes long
        if first <= requested < first + 10:
            return read_back == requested - first
    return False


LIBRARY_TOOL_TIMEOUT = 300.0
# the partition the planner writes symbols and parts to when a design names none; the
# stock central library registers it (schematic_layout.PARTITION)
DEFAULT_PARTITION = "PartQuest"


def _close_if_open(app: Any, project_path: Path, *, lifecycle: dict | None = None) -> bool:
    """Save all documents and confirm normal closure of the exact project."""
    try:
        opened = _native_call("close.IsProjectOpened.before", lambda: app.IsProjectOpened())
        if type(opened) is not bool:
            raise AdapterError("E_HUMAN_REQUIRED", "Designer open state is unavailable")
        if not opened:
            return False
        current = (
            _designer_project_path(app)
            if _owner_application() is not None
            else _com_member(_com_member(app, "GetProjectData"), "GetProjectFilePath")
        )
        if not isinstance(current, str) or not current:
            raise AdapterError("E_HUMAN_REQUIRED", "Open Designer project identity is unavailable")
        if Path(current).resolve() != project_path.resolve():
            return False
        # IVdAppProtectionProxy.Documents / IVdDocs.SaveAll in the installed
        # 151 type library. ActiveDocument.Save alone misses other open sheets.
        if lifecycle is not None:
            lifecycle["save_count"] += 1
        _native_call(
            "close.Documents.SaveAll",
            lambda: _designer_save_all(app),
        )
        if lifecycle is not None:
            lifecycle["save_acknowledged"] = True
        after_save = (
            _designer_project_path(app)
            if _owner_application() is not None
            else _com_member(_com_member(app, "GetProjectData"), "GetProjectFilePath")
        )
        if not isinstance(after_save, str) or Path(after_save).resolve() != project_path.resolve():
            raise AdapterError("E_CONFLICT", "Designer project changed during save; close stopped")
        if lifecycle is not None:
            lifecycle["close_count"] += 1
        closed = _native_call("close.CloseProject", lambda: app.CloseProject())
        _settle(0.5)
        observed = _native_call("close.IsProjectOpened.after", lambda: app.IsProjectOpened())
        if lifecycle is not None:
            lifecycle.update(close_returned=closed, closed_observed=observed is False)
        if closed is not True or observed is not False:
            raise AdapterError(
                "E_HUMAN_REQUIRED",
                "Designer closure was not confirmed; further writes stopped",
                {
                    "save_acknowledged": True,
                    "close_returned": closed,
                    "project_open_observed": observed,
                    "project_closed": False,
                },
            )
        return True
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "save_or_close_project") from exc


def _central_library(project_path: Path) -> tuple[Path, Path]:
    """The `.lmc` the project names and the library folder around it."""
    try:
        text = project_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot read the project file: {exc}") from exc
    for line in text.splitlines():
        if line.startswith("KEY CentralLibrary "):
            value = line[len("KEY CentralLibrary ") :].strip().strip('"')
            lmc = Path(value)
            if not lmc.is_absolute():
                lmc = project_path.parent / lmc
            return lmc, lmc.parent
    raise AdapterError(
        "E_NOT_FOUND", "the project names no CentralLibrary", {"project": str(project_path)}
    )


def _run_library_tool(
    name: str, args: list[str], timeout: float = LIBRARY_TOOL_TIMEOUT
) -> dict[str, Any]:
    """Run one of the `common/win64/bin` converters, pressing OK on its message boxes.

    The converters are GUI-subsystem programs: a wrong argument or a failed
    licence check is a modal box, not an exit code. Each box's text is returned
    in `dialogs` so the caller can report it.
    """
    sdd_home = _configure_environment()
    if sdd_home is None:
        raise AdapterError("E_CONFIG", "Xpedition SDD_HOME could not be discovered")
    executable = sdd_home / "common" / "win64" / "bin" / f"{name}.exe"
    if not executable.is_file():
        raise AdapterError("E_NOT_FOUND", f"{name} was not found", {"path": str(executable)})
    try:
        process = subprocess.Popen(
            [str(executable), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            cwd=str(executable.parent),
        )
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot start {name}: {exc}") from exc
    dialogs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    started = time.monotonic()
    while True:
        code = process.poll()
        try:
            from . import win_dialogs

            for dialog in win_dialogs.find_dialogs(only_xpedition=False, pid=process.pid):
                title = str(dialog.get("title", "")).strip().lower()
                owner = str(dialog.get("process", "")).lower()
                if owner != executable.name.lower() and title != name.lower():
                    continue
                key = (str(dialog.get("title", "")), str(dialog.get("text", "")))
                if key not in seen:
                    seen.add(key)
                    dialogs.append({"title": key[0], "text": key[1]})
                # Licensing/login and unknown prompts require the operator, even for our child.
                if any(
                    marker in (key[0] + " " + key[1]).lower()
                    for marker in (
                        "license",
                        "licence",
                        "licensing",
                        "login",
                        "sign in",
                        "许可证",
                        "授权",
                        "登录",
                    )
                ):
                    continue
                win_dialogs.dismiss(int(dialog["hwnd"]), pid=process.pid)
        except Exception:
            pass
        if code is not None:
            break
        if time.monotonic() - started > timeout:
            process.kill()
            raise AdapterError("E_TIMEOUT", f"{name} timed out", {"seconds": timeout})
        time.sleep(0.3)
    output = process.stdout.read() if process.stdout else ""
    return {"tool": name, "exit_code": code, "dialogs": dialogs, "stdout": output[-2000:]}


def _tool_text(raw: bytes) -> str:
    """A stock tool's log or output as text.

    The tools write the system code page (`mbcs`, which exists on Windows only). UTF-8
    is tried first: ASCII is UTF-8, and the code page's Chinese practically never
    decodes as UTF-8, while `mbcs` on an English machine decodes UTF-8 bytes into
    mojibake without raising. Whatever is left is read as UTF-8 with the bytes it
    cannot decode replaced, so a log never makes a reader fail.
    """
    for encoding in ("utf-8-sig", "mbcs"):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _tool_log(path: Path) -> dict[str, Any]:
    """A converter's log in the system code page, with its error lines pulled out."""
    try:
        raw = path.read_bytes()
    except OSError:
        return {"path": str(path), "errors": [], "lines": []}
    text = _tool_text(raw)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    # The converters log in the interface language ("错误" is "error"). Their routine
    # "checking for file format errors... none found" lines mention errors too, so only
    # a line that starts with the word or carries it as a label counts.
    errors = [
        line.strip()
        for line in lines
        if re.search(
            r"^\s*(?:error|错误|失败)|(?:error|错误)\s*[:：]|无法添加|遇到 \d+ 个错误|"
            r"unable to add|cannot add|\d+ errors? (?:were )?(?:found|encountered)",
            line,
            re.I,
        )
    ]
    # a warning is not a failure, but it can mean data was changed on the way in: an
    # invalid Value is stored as 0 ("无效的值 ... (对于特性 \"Value\")")
    warnings = [line.strip() for line in lines if re.search(r"警告|warning", line, re.I)]
    return {"path": str(path), "errors": errors, "warnings": warnings[:40], "lines": lines[-40:]}


REJECTED_CELL = re.compile(r'(?:无法添加单元|unable to add cell|cannot add cell)\s*"([^"]+)"', re.I)


def _rejected_cells(path: Path) -> list[str]:
    """The cells a `HKP2CellDB` log says it could not add (it then saves nothing at all)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    text = _tool_text(raw)
    return [match.group(1) for match in REJECTED_CELL.finditer(text)]


def _ensure_prj_pdb(project_path: Path, entry: str) -> bool:
    """Add a parts database to the design's `LIST PDBs` in the `.prj`; True if it was added."""
    return _ensure_prj_list(project_path, "PDBs", entry)


def _prj_lists(project_path: Path, entry: str) -> bool:
    """True if the `.prj` already names `entry` (case-insensitive)."""
    text = project_path.read_text(encoding="utf-8", errors="surrogateescape")
    return entry.lower() in text.lower()


def _ensure_prj_list(project_path: Path, list_name: str, entry: str) -> bool:
    """Add `entry` to `LIST <list_name>` of the `.prj`; True if it was added."""
    text = project_path.read_text(encoding="utf-8", errors="surrogateescape")
    if entry.lower() in text.lower():
        return False
    index = text.find(f"LIST {list_name}")
    if index < 0:
        from . import project_file

        board = project_file.board_design(project_file.designs(text))
        if board is None:
            raise AdapterError(
                "E_NOT_FOUND",
                "The project has no board design for library registration",
                {"project": str(project_path)},
            )
        section = re.search(
            r"^SECTION " + re.escape(board.name) + r"[ \t]*\r?\n(.*?)^ENDSECTION", text, re.M | re.S
        )
        if section is None:
            raise AdapterError("E_PROJECT_INVALID", "The board design section is missing")
        newline = "\r\n" if "\r\n" in text else "\n"
        position = section.end(1)
        text = text[:position] + f"LIST {list_name}{newline}ENDLIST{newline}" + text[position:]
        index = text.find(f"LIST {list_name}", section.start())
    end = text.find("ENDLIST", index)
    if end < 0:
        raise AdapterError("E_VALIDATION", f"the project's LIST {list_name} has no ENDLIST")
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text[:end] + f'VALUE "{entry}"{newline}' + text[end:]
    project_path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return True


LIBRARY_EXPORT_WORKERS = 6
LIBRARY_EXPORT_TIMEOUT = 120.0


def _library_export(params: dict[str, Any], client: Any = None) -> dict[str, Any]:
    """The central library as HKP text: each parts and cell partition and the padstacks.

    `PartsDB2HKP`, `CellDB2HKP` and `PadstackDB2HKP` run with `-a -u mm` (plain text,
    millimetres), several at once, into a cache folder under `cache`; a database whose
    size and time are those of the last export is not exported again. Nothing in the
    library is written. Symbols need no export: their files are text already, under
    the `symbols` folder returned.
    """
    project_path: Path | None = None
    if params.get("library"):
        # a library read on its own: another project's, or a copy of a company library
        lmc = Path(str(params["library"])).expanduser().resolve()
        if lmc.suffix.lower() != ".lmc":
            raise AdapterError(
                "E_VALIDATION", "a central library is a .lmc file", {"path": str(lmc)}
            )
        root = lmc.parent
    else:
        project = params.get("project")
        if not project:
            raise AdapterError("E_USAGE", "library_export requires the project or the library")
        project_path = Path(str(project)).expanduser().resolve()
        if not project_path.is_file():
            raise AdapterError(
                "E_NOT_FOUND", "project file was not found", {"path": str(project_path)}
            )
        lmc, root = _central_library(project_path)
    if not lmc.is_file():
        raise AdapterError("E_NOT_FOUND", "the central library was not found", {"path": str(lmc)})
    from . import native_library_manager

    registration = (
        native_library_manager.observe(client, lmc)
        if client is not None
        else {"status": "not_observed", "source": "No LibraryManager client was provided"}
    )
    cache_root = params.get("cache")
    if not cache_root:
        raise AdapterError("E_USAGE", "library_export requires a cache folder")
    digest = hashlib.sha1(str(lmc).casefold().encode("utf-8")).hexdigest()[:16]
    cache = Path(str(cache_root)).expanduser() / digest
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot create the cache folder: {exc}") from exc
    kinds = [str(k) for k in params.get("kinds") or ("parts", "cells", "padstacks")]
    wanted = params.get("partitions")
    chosen = {str(p).casefold() for p in wanted} if wanted else None
    sources: list[tuple[str, str, Path]] = []
    if "parts" in kinds:
        sources += [("parts", f.stem, f) for f in sorted((root / "PartsDBLibs").glob("*.pdb"))]
    if "cells" in kinds:
        sources += [("cells", f.stem, f) for f in sorted((root / "CellDBLibs").glob("*.cel"))]
    if chosen is not None:
        sources = [item for item in sources if item[1].casefold() in chosen]
    if "padstacks" in kinds and (root / "Layout" / "PadstackDB.psk").is_file():
        sources.append(("padstacks", "", root / "Layout" / "PadstackDB.psk"))
    index_path = cache / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index, dict):
            index = {}
    except (OSError, ValueError):
        index = {}
    tools = {"parts": "PartsDB2HKP", "cells": "CellDB2HKP", "padstacks": "PadstackDB2HKP"}

    def output_for(kind: str, partition: str) -> Path:
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", partition) or kind
        return cache / f"{kind}-{stem}.hkp"

    def export(item: tuple[str, str, Path]) -> dict[str, Any]:
        kind, partition, source = item
        output = output_for(kind, partition)
        record: dict[str, Any] = {"kind": kind, "partition": partition, "path": str(output)}
        try:
            info = source.stat()
        except OSError as exc:
            return {**record, "error": f"cannot read {source.name}: {exc}"}
        current = [int(info.st_mtime_ns), int(info.st_size)]
        if not params.get("fresh") and index.get(str(source)) == current and output.is_file():
            return {**record, "cached": True, "stamp": current}
        arguments = ["-i", str(source), "-o", str(output), "-u", "mm", "-a"]
        log = output.with_suffix(".log")
        if kind != "parts":
            arguments += ["-l", str(log)]
        try:
            if output.exists():
                output.unlink()
            run = _run_library_tool(tools[kind], arguments, timeout=LIBRARY_EXPORT_TIMEOUT)
        except (AdapterError, OSError) as exc:
            return {**record, "error": str(exc)}
        if run["exit_code"] != 0 or run["dialogs"] or not output.is_file():
            return {
                **record,
                "error": f"{tools[kind]} failed",
                "exit_code": run["exit_code"],
                "dialogs": run["dialogs"],
                "log": _tool_log(log)["errors"] if kind != "parts" else [],
            }
        return {**record, "cached": False, "stamp": current}

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(LIBRARY_EXPORT_WORKERS) as pool:
        results = list(pool.map(export, sources))
    files = []
    failed = []
    for (_kind, _partition, source), result in zip(sources, results, strict=True):
        if "error" in result:
            index.pop(str(source), None)
            failed.append(result)
            continue
        index[str(source)] = result.pop("stamp")
        files.append(result)
    try:
        index_path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    except OSError:
        pass
    return {
        "project": str(project_path) if project_path else None,
        "library": str(lmc),
        "root": str(root),
        "registration": registration,
        "symbols": str(root / "SymbolLibs"),
        "files": files,
        "failed": failed,
        "exported": sum(1 for item in files if not item.get("cached")),
        "_untrusted": ["project", "library", "root", "symbols", "files", "failed"],
    }


# a partition becomes a file and a folder name: ASCII, no separators, no trailing dot
PARTITION_NAME = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9 _.\-]{0,62}[A-Za-z0-9_])?$")


def _hkp_bytes(text: str) -> bytes:
    """An HKP text as the converters read it: the system code page. Written as ASCII
    with replacements, a Chinese description reached the library as question marks
    and nothing said so; a character the code page cannot hold is refused instead."""
    try:
        return text.encode("ascii")
    except UnicodeEncodeError:
        pass
    try:
        return text.encode("mbcs")
    except LookupError:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AdapterError(
            "E_VALIDATION",
            "the text holds characters the system code page cannot write",
            {"text": exc.object[max(0, exc.start - 20) : exc.end + 20], "_untrusted": ["text"]},
        ) from exc


def _next_symbol_version(folder: Path, name: str) -> int:
    """One past the highest version of symbol `name` in a `sym` folder, so the file
    written is the one Designer takes; 1 when there is none."""
    highest = 0
    if folder.is_dir():
        for path in folder.iterdir():
            stem, _, version = path.name.rpartition(".")
            if stem == name and version.isdigit():
                highest = max(highest, int(version))
    return highest + 1


def _import_units(params: dict[str, Any]) -> list[dict[str, Any]]:
    """What to import, partition by partition. Without `units`, the top-level
    `partition`, `parts`, `cells` and `symbols` are the one unit, as `library create` and
    `library materialize` send them; their partition is a plain identifier."""
    units = params.get("units")
    if units is None:
        partition = str(params.get("partition") or DEFAULT_PARTITION)
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", partition):
            raise AdapterError(
                "E_USAGE", "partition must be a plain identifier", {"partition": partition}
            )
        units = [
            {
                "partition": partition,
                "parts": params.get("parts") or "",
                "cells": params.get("cells") or "",
                "symbols": params.get("symbols") or {},
                "replace": bool(params.get("replace", True)),
            }
        ]
    if not isinstance(units, list):
        raise AdapterError("E_USAGE", "library_import units is a list")
    for unit in units:
        if not isinstance(unit, dict) or not PARTITION_NAME.fullmatch(
            str(unit.get("partition") or "")
        ):
            raise AdapterError(
                "E_USAGE",
                "a unit's partition must be ASCII letters, digits, spaces and _ . -",
                {"partition": str((unit or {}).get("partition"))[:80], "_untrusted": ["partition"]},
            )
        if not isinstance(unit.get("symbols") or {}, dict) or not isinstance(
            unit.get("symbol_files") or {}, dict
        ):
            raise AdapterError(
                "E_USAGE", "a unit's symbols map names to file text, symbol_files to files"
            )
    # Dimension updates must never rehome a resolved cell through a scalar part
    # partition. This check precedes registration, editor release and converters.
    for dependency in params.get("dimension_dependencies") or []:
        if dependency.get("partition") != dependency.get("part_partition"):
            raise AdapterError(
                "E_UNSUPPORTED",
                "Cross-partition pad dimension updates are not supported",
                {**dependency, "native_definition_write_started": False, "write_attempted": False},
            )
        from .library_read import parse_cells

        for unit in units:
            names = {c["name"] for c in parse_cells(unit.get("cells") or "", unit["partition"])}
            if dependency["name"] in names and dependency["partition"] != unit["partition"]:
                raise AdapterError(
                    "E_CONFLICT",
                    "Resolved cell partition does not match its import unit",
                    {
                        **dependency,
                        "import_partition": unit["partition"],
                        "native_definition_write_started": False,
                        "write_attempted": False,
                    },
                )
    return units


def _library_hkp_payload(text: Any, kind: str) -> str:
    """Exclude header-only HKP before any native library/project lifecycle.

    Generated empty HKP has nonempty Filetype/Version/Units records. Presence
    is determined from actual item records, not string truthiness. Unstructured
    legacy input remains the converter's validation responsibility.
    """
    from .library_read import ITEM_KEYWORDS, parse_records

    source = str(text or "")
    records = parse_records(source)
    if records:
        has_objects = any(
            record.keyword in ITEM_KEYWORDS[kind] and record.values for record in records
        )
        return source if has_objects else ""
    comments_only = all(
        line.lstrip().startswith("!") or not line.strip() for line in source.splitlines()
    )
    return source if source.strip() and not comments_only else ""


def _library_import(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Import padstacks, symbols, cells and parts into the project's central library.

    The texts go through the stock converters (`HKP2PadstackDB`, `HKP2CellDB`,
    `HKP2PartsDB`). A filename does not establish LMC registration: missing native
    partitions stop the write before any converter or symbol mutation. The padstack
    text comes first, then every unit's symbol files, cells and parts, in that order
    across the units, so each item finds what it refers to. Designer's project is closed
    meanwhile, because it holds the library. Each parts partition goes into the
    project's `LIST PDBs` so the packager searches it, each symbol partition into `LIST
    Symbols`, and `cell_partitions` into the design's cell list.
    """
    project = params.get("project") or params.get("path")
    if not project:
        raise AdapterError("E_USAGE", "library_import requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    if not project_path.is_file():
        raise AdapterError("E_NOT_FOUND", "project file was not found", {"path": str(project_path)})
    units = [
        {
            **unit,
            "parts": _library_hkp_payload(unit.get("parts"), "parts"),
            "cells": _library_hkp_payload(unit.get("cells"), "cells"),
        }
        for unit in _import_units(params)
    ]
    padstacks = _library_hkp_payload(params.get("padstacks"), "padstacks")
    if not padstacks and not any(
        unit.get("parts") or unit.get("cells") or unit.get("symbols") or unit.get("symbol_files")
        for unit in units
    ):
        raise AdapterError(
            "E_VALIDATION",
            "library_import has no library objects; empty HKP headers are not import input",
            {
                "materialization_not_needed": True,
                "write_attempted": False,
                "native_definition_write_started": False,
                "next_action": "run schematic package as a separate confirmed operation",
            },
        )
    lmc, root = _central_library(project_path)
    if not lmc.is_file():
        raise AdapterError("E_NOT_FOUND", "the central library was not found", {"path": str(lmc)})
    from .native_library_manager import require_partitions

    try:
        registration = require_partitions(client, lmc, units, params.get("required_partitions"))
    except AdapterError as exc:
        exc.details.setdefault("native_definition_write_started", False)
        raise
    work = root / "Work" / "xpedition-cli"
    app: Any = None
    try:
        app = _viewdraw_application(client, attach_only=True)
    except AdapterError:
        if os.environ.get("XPEDITION_EXPECTED_PID") or processes("schematic"):
            raise
        app = None
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot create the work folder: {exc}") from exc
    reopen = False
    if app is not None:
        reopen = _close_if_open(app, project_path)
    padstack_db = root / "Layout" / "PadstackDB.psk"
    steps: list[dict[str, Any]] = []
    records = [
        {
            "partition": str(unit["partition"]),
            "symbols_written": [],
            "symbols_registered": False,
            "pdb_registered": False,
        }
        for unit in units
    ]
    cells: list[str] = []
    cells_missing: list[str] = []

    def convert(step: str, tool: str, arguments: list[str], text: str, tag: str) -> bool:
        source = work / f"{step}{tag}.hkp"
        log = work / f"{step}{tag}.log"
        try:
            source.write_bytes(_hkp_bytes(str(text)))
            if log.exists():
                log.unlink()
        except OSError as exc:
            raise AdapterError("E_IO", f"cannot write {source.name}: {exc}") from exc
        run = _run_library_tool(tool, ["-i", str(source), *arguments, "-l", str(log)])
        run["step"] = step
        run["log"] = _tool_log(log)
        steps.append(run)
        return not (run["exit_code"] != 0 or run["dialogs"] or run["log"]["errors"])

    def write_symbols(unit: dict[str, Any], record: dict[str, Any]) -> None:
        symbols = unit.get("symbols") or {}
        files = unit.get("symbol_files") or {}
        if not symbols and not files:
            return
        target = root / "SymbolLibs" / str(unit["partition"]) / "sym"
        if not str(target).isascii():
            raise AdapterError(
                "E_VALIDATION",
                "Designer cannot load new symbol files from a folder whose path has "
                "non-ASCII characters; keep the project and its library on an ASCII path",
                {"path": str(target)},
            )
        try:
            target.mkdir(parents=True, exist_ok=True)
            folder = target.resolve()
            for name in [*symbols, *(n for n in files if n not in symbols)]:
                version = _next_symbol_version(target, str(name))
                path = (target / f"{name}.{version}").resolve()
                if path.parent != folder:
                    raise AdapterError(
                        "E_VALIDATION",
                        "a symbol name must be a plain file name",
                        {"symbol": str(name)[:80], "_untrusted": ["symbol"]},
                    )
                source = Path(str(files[name])) if name in files else None
                if source is not None and source.is_file():
                    # another library's file, byte for byte, whatever its code page
                    path.write_bytes(source.read_bytes())
                else:
                    path.write_text(str(symbols[name]), encoding="utf-8")
                record["symbols_written"].append(str(name))
        except OSError as exc:
            raise AdapterError("E_IO", f"cannot write symbol files: {exc}") from exc
        # Designer searches the symbol partitions its .prj lists
        record["symbols_registered"] = _ensure_prj_list(
            project_path, "Symbols", f"SymbolLibs\\{unit['partition']}"
        )

    try:
        ok = True
        if padstacks:
            ok = convert(
                "padstacks",
                "HKP2PadstackDB",
                ["-o", str(padstack_db), "-c", str(lmc), "-m"],
                padstacks,
                "",
            )
        for unit, record in zip(units, records, strict=True):
            if ok:
                write_symbols(unit, record)
        for index, unit in enumerate(units):
            if ok and unit.get("cells"):
                partition = str(unit["partition"])
                ok = convert(
                    "cells",
                    "HKP2CellDB",
                    [
                        "-o",
                        str(root / "CellDBLibs" / f"{partition}.cel"),
                        "-psk",
                        str(padstack_db),
                        "-c",
                        str(lmc),
                        "-m",
                    ],
                    unit["cells"],
                    f"-{index}" if len(units) > 1 else "",
                )
        for index, (unit, record) in enumerate(zip(units, records, strict=True)):
            if ok and unit.get("parts"):
                partition = str(unit["partition"])
                ok = convert(
                    "parts",
                    "HKP2PartsDB",
                    [
                        "-c",
                        str(lmc),
                        "-o",
                        str(root / "PartsDBLibs" / f"{partition}.pdb"),
                        # -r replaces a part of the same number; without it the database
                        # keeps the part it holds and drops the new one without a word
                        *(["-r"] if unit.get("replace", True) else []),
                    ],
                    unit["parts"],
                    f"-{index}" if len(units) > 1 else "",
                )
                if ok:
                    record["pdb_registered"] = _ensure_prj_pdb(
                        project_path, f"PartsDBLibs\\{partition}.pdb"
                    )
        if ok and any(unit.get("parts") for unit in units):
            cells = _sync_prj_cells(project_path, root)
            wanted = [str(item) for item in params.get("cell_partitions") or []]
            if wanted:
                # parts referencing cells of other partitions: Layout's Database Load
                # only searches the partitions the design lists
                added, cells_missing = _register_cell_partitions(project_path, root, wanted)
                cells += added
    finally:
        if reopen and app is not None:
            try:
                _ensure_project(app, project_path)
            except AdapterError:
                pass
    failed = [s["step"] for s in steps if s["exit_code"] != 0 or s["dialogs"] or s["log"]["errors"]]
    return {
        "project": str(project_path),
        "library": str(lmc),
        "partition": records[0]["partition"] if len(records) == 1 else None,
        "registration_before": registration,
        "units": records,
        "steps": steps,
        "failed": failed,
        "pdb_registered": any(r["pdb_registered"] for r in records),
        "symbols_written": [name for r in records for name in r["symbols_written"]],
        "symbols_registered": any(r["symbols_registered"] for r in records),
        "cells_registered": cells,
        "cells_missing": cells_missing,
        "cell_reference_evidence": {
            "source": "project LIST 2dCellLibraries reference update",
            "added": cells,
            "missing_files": cells_missing,
            "native_consumption": "not_observed",
            "not_verified": [
                "Layout database open",
                "native cell lookup",
                "pin/pad geometry",
                "PCB instances",
            ],
        },
        # a part whose cell partition is missing has no footprint: packaging and the
        # board would fail on it later, so the import has not succeeded
        "ok": not failed and not cells_missing,
        **(
            {"hint": "the parts reference cell partitions the library does not have"}
            if cells_missing
            else {}
        ),
        "_untrusted": ["project", "library", "steps", "units"],
    }


def _register_cell_partitions(
    project_path: Path, library_root: Path, partitions: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Write project references for existing cell files, without proving consumption.

    Return added references and missing files. File existence and text insertion
    do not prove Layout database opening, native cell lookup or board instances.
    """
    from . import project_file

    text = project_path.read_text(encoding="utf-8", errors="surrogateescape")
    added: list[str] = []
    missing: list[str] = []
    for partition in partitions:
        if not (library_root / "CellDBLibs" / f"{partition}.cel").is_file():
            missing.append(partition)
            continue
        try:
            text = project_file.ensure_list(text, project_file.CELL_LIST, "PDBs")
        except ValueError as exc:
            raise AdapterError("E_NOT_FOUND", str(exc), {"project": str(project_path)}) from exc
        entry = f"CellDBLibs\\{partition}.cel"
        text, done = project_file.add_entry(text, project_file.CELL_LIST, entry)
        if done:
            added.append(entry)
    if added:
        project_path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return added, missing


def _release_project(client: Any, project_path: Path) -> tuple[Any, bool]:
    """Close `project_path` in Designer if it is open there; the app and whether to reopen."""
    try:
        app = _viewdraw_application(client, attach_only=True)
    except AdapterError:
        if processes("schematic"):
            raise
        return None, False
    return app, _close_if_open(app, project_path)


def _sync_prj_cells(project_path: Path, library_root: Path) -> list[str]:
    """Give the design's `LIST 2dCellLibraries` a cell partition for every parts partition
    in its `LIST PDBs` that the library has; the entries added.

    This is reference maintenance only. The selected native library/scheme,
    database loading and cell lookup must be independently observed in Layout.
    """
    from . import project_file

    text = project_path.read_text(encoding="utf-8", errors="surrogateescape")
    available = [item.stem for item in (library_root / "CellDBLibs").glob("*.cel")]
    wanted = project_file.cell_entries_for(project_file.list_entries(text, "PDBs"), available)
    if not wanted:
        return []
    try:
        text = project_file.ensure_list(text, project_file.CELL_LIST, "PDBs")
    except ValueError as exc:
        raise AdapterError("E_NOT_FOUND", str(exc), {"project": str(project_path)}) from exc
    added: list[str] = []
    for entry in wanted:
        text, done = project_file.add_entry(text, project_file.CELL_LIST, entry)
        if done:
            added.append(entry)
    if added:
        project_path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return added


def _jobwizard_messages(log_path: Path) -> tuple[list[str], int | None]:
    """JobWizard's log: its error lines and the number of files it copied (None if none)."""
    try:
        raw = log_path.read_bytes()
    except OSError:
        return [], None
    text = _tool_text(raw)
    copied = None
    errors: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        match = re.search(r"Successfully copied (\d+) file", stripped)
        if match:
            copied = int(match.group(1))
        elif re.search(r"未能|失败|无法|failed|unable|error|cannot", stripped, re.I):
            errors.append(stripped)
    return errors, copied


def _annotation_summary(log_path: Path) -> dict[str, Any]:
    """What Layout's `ForwardAnnotation.txt` says: errors, warnings, counts, the verdict."""
    try:
        raw = log_path.read_bytes()
    except OSError:
        return {
            "errors": [],
            "warnings": [],
            "components": None,
            "nets": None,
            "pins": None,
            "completed": False,
        }
    text = _tool_text(raw)
    lines = [line.rstrip() for line in text.splitlines()]
    errors = [line.strip() for line in lines if re.search(r"^\s*ERROR\b", line)]
    warnings = [line.strip() for line in lines if re.search(r"^\s*WARNING\b", line)]
    components = nets = pins = None
    for line in lines:
        match = re.search(r"(\d+) components were found", line)
        if match:
            components = int(match.group(1))
        match = re.search(r"(\d+) nets were found containing (\d+) pins", line)
        if match:
            nets, pins = int(match.group(1)), int(match.group(2))
    completed = any("successfully completed" in line for line in lines)
    return {
        "errors": errors,
        "warnings": warnings,
        "components": components,
        "nets": nets,
        "pins": pins,
        "completed": completed,
    }


def _layout_board_path(params: dict[str, Any]) -> Path:
    """The `.pcb` a request names, directly or through its `.prj`'s board design."""
    from . import project_file

    raw = params.get("project") or params.get("path")
    if not raw:
        raise AdapterError("E_USAGE", "a project (.prj) or board (.pcb) path is required")
    path = Path(str(raw)).expanduser().resolve()
    if path.suffix.lower() == ".pcb":
        if not path.is_file():
            raise AdapterError("E_NOT_FOUND", "board file was not found", {"path": str(path)})
        return path
    if path.suffix.lower() != ".prj":
        raise AdapterError("E_USAGE", "expected a .prj or .pcb path", {"path": str(path)})
    if not path.is_file():
        raise AdapterError("E_NOT_FOUND", "project file was not found", {"path": str(path)})
    text = path.read_text(encoding="utf-8", errors="replace")
    design = project_file.board_design(project_file.designs(text), params.get("design"))
    if design is None:
        raise AdapterError("E_NOT_FOUND", "the project lists no PCB design", {"project": str(path)})
    if not design.pcb_path:
        raise AdapterError(
            "E_NOT_FOUND",
            "the design has no board yet",
            {"project": str(path), "design": design.name, "hint": "run pcb create first"},
        )
    # the `.prj` is written by Windows tools, so its relative paths carry backslashes;
    # off Windows those are ordinary filename characters and the board is never found
    board = Path(str(design.pcb_path).replace("\\", "/"))
    if not board.is_absolute():
        board = path.parent / board
    if not board.is_file():
        raise AdapterError(
            "E_NOT_FOUND",
            "the board the project names was not found",
            {"project": str(path), "pcb": str(board)},
        )
    return board.resolve()


class _PromptAnswerer:
    """Answer Layout's prompts from a helper thread while this thread waits in a COM call.

    Opening a board can stop on a design-status or database-recovery question, and
    the call that opens it does not return until someone answers. The thread reads
    the Qt windows through UI Automation (`win_dialogs.answer_prompts`) and presses
    the answer `LAYOUT_PROMPTS` gives; `answered` records what it did.
    """

    def __init__(
        self,
        rules: tuple[tuple[str, str, str | None], ...] = LAYOUT_PROMPTS,
        interval: float = 1.0,
        process_name: str = "expeditionpcb.exe",
    ) -> None:
        self.rules = rules
        self.interval = interval
        self.process_name = process_name
        domain = "schematic" if process_name.lower() == "viewdraw.exe" else "pcb"
        bound = (
            os.environ.get("XPEDITION_EXPECTED_PID")
            if os.environ.get("XPEDITION_EXPECTED_DOMAIN") == domain
            else os.environ.get(f"XPEDITION_EXPECTED_{domain.upper()}_PID")
        )
        self.pid = int(bound) if bound else None
        self.answered: list[dict[str, Any]] = []
        # Dialogs no rule covers, keyed by (title, text) so a dialog that sits
        # there through many polls is reported once. They are never pressed: an
        # unknown question is not ours to answer. Recording them is what turns
        # "the COM call never returned" into something an operator can act on.
        self.blocking: dict[tuple[str, str], dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _PromptAnswerer:
        self._thread = threading.Thread(target=self._run, name="layout-prompts", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        from . import win_dialogs

        # Only the rule-based answers: pressing the default button of every #32770
        # dialog as well (as the start-up wait does) ends the packager's progress box
        # during forward annotation and the run fails in its "packaging phase".
        while not self._stop.wait(self.interval):
            try:
                answered, blocking = (
                    win_dialogs.answer_prompts(self.rules, self.process_name, pid=self.pid)
                    if self.pid is not None
                    else win_dialogs.answer_prompts(self.rules, self.process_name)
                )
            except Exception:
                continue
            self.answered.extend(answered)
            for dialog in blocking:
                self.blocking.setdefault((dialog["title"], dialog["text"]), dialog)

    def blocking_dialogs(self) -> list[dict[str, Any]]:
        """The unanswerable dialogs seen so far, oldest first."""
        return list(self.blocking.values())


def _open_layout_document(
    app: Any,
    pcb_path: Path,
    rules: tuple[tuple[str, str, str | None], ...] = LAYOUT_PROMPTS,
) -> tuple[Any, list[dict[str, Any]]]:
    """The board open in Layout, opening `pcb_path` if it is not; with the prompts answered."""
    try:
        current = app.ActiveDocument
    except Exception as exc:
        raise AdapterError(
            "E_CONFLICT",
            "Layout active document is not observable; refusing OpenDocument",
            {
                "write_attempted": False,
                "next_action": "Observe and normally release "
                "the owned board before opening another.",
            },
        ) from exc
    if current is not None:
        full = str(_value(current, "FullName", default="") or "")
        try:
            if full and Path(full).resolve() == pcb_path:
                return current, []
        except OSError:
            pass
        raise AdapterError(
            "E_CONFLICT",
            "Save and normally close the current owned board before opening another",
            {
                "current_board": full or None,
                "requested_board": str(pcb_path),
                "write_attempted": False,
                "_untrusted": ["current_board", "requested_board"],
            },
        )
    with _PromptAnswerer(rules) as prompts:
        try:
            doc = _native_call("layout.OpenDocument", lambda: app.OpenDocument(str(pcb_path)))
        except Exception as exc:
            raise _com_error(exc, "open_document") from exc
    if doc is None:
        doc = _value(app, "ActiveDocument", default=None)
    if doc is None:
        raise AdapterError(
            "E_SERVER",
            "Layout opened no document",
            {"pcb": str(pcb_path), "prompts": prompts.answered},
        )
    return doc, prompts.answered


def _reopen_board(app: Any, pcb_path: Path) -> Any:
    """Persist the owned board and observe it through a newly opened document."""
    doc = _licensed_document(app)
    if Path(str(doc.FullName)).resolve() != pcb_path.resolve():
        raise AdapterError("E_CONFLICT", "The active board changed before save/reopen")
    _save_close_board(app, pcb_path)
    _native_call("layout.reopen", lambda: _open_layout_document(app, pcb_path))
    reopened = _licensed_document(app)
    if Path(str(reopened.FullName)).resolve() != pcb_path.resolve():
        raise AdapterError("E_CONFLICT", "The reopened board has another identity")
    return reopened


def _pcb_create(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Create the project's board from a layout template with JobWizard's command line.

    `JobWizard -createnew` copies `Templates/Layout/<template>` out of the central
    library into `PCB/<name>.pcb` and writes `PCBDesignPath` and `LayoutTemplate`
    into the `.prj`. It takes the first design in `LIST Designs`, so the board design
    is moved there first; the template is copied from the installation's stock set
    when the library lacks it; and the cell partitions matching the project's parts
    partitions are registered so the board can be forward-annotated afterwards.
    """
    from . import project_file

    project = params.get("project") or params.get("path")
    if not project:
        raise AdapterError("E_USAGE", "pcb_create requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    if project_path.suffix.lower() != ".prj" or not project_path.is_file():
        raise AdapterError("E_NOT_FOUND", "project file was not found", {"path": str(project_path)})
    template = str(params.get("template") or project_file.DEFAULT_LAYOUT_TEMPLATE)
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9 _().-]*$", template):
        raise AdapterError("E_USAGE", "template must be a plain name", {"template": template})
    name = str(params.get("name") or project_path.stem)
    if not re.match(r"^[A-Za-z0-9_-]+$", name):
        raise AdapterError("E_USAGE", "board name must be a plain identifier", {"name": name})
    text = project_path.read_text(encoding="utf-8", errors="surrogateescape")
    listed = project_file.designs(text)
    design = project_file.board_design(listed, params.get("design"))
    if design is None:
        raise AdapterError(
            "E_NOT_FOUND",
            "the project lists no PCB design",
            {"project": str(project_path), "designs": [item.name for item in listed]},
        )
    replace = bool(params.get("replace", False))
    removed: dict[str, Any] = {}
    if design.pcb_path and not replace:
        raise AdapterError(
            "E_CONFLICT",
            "the design already names a board",
            {"design": design.name, "pcb": design.pcb_path, "hint": "pass replace"},
        )
    pcb_path = project_path.parent / "PCB" / f"{name}.pcb"
    if pcb_path.exists() and not replace:
        raise AdapterError("E_CONFLICT", "the board file already exists", {"pcb": str(pcb_path)})
    if replace:
        removed = _remove_board(client, project_path, design.pcb_path, name)
        text = project_path.read_text(encoding="utf-8", errors="surrogateescape")
    lmc, root = _central_library(project_path)
    if not lmc.is_file():
        raise AdapterError("E_NOT_FOUND", "the central library was not found", {"path": str(lmc)})
    template_dir = root / "Templates" / "Layout" / template
    copied_template = False
    if not (template_dir / "template.pcb").is_file():
        sdd_home = _configure_environment()
        stock = sdd_home.joinpath(*LAYOUT_TEMPLATE_ROOT) if sdd_home else None
        source = stock / template if stock else None
        if source is None or not (source / "template.pcb").is_file():
            available = (
                sorted(p.name for p in stock.iterdir() if p.is_dir())
                if stock and stock.is_dir()
                else []
            )
            raise AdapterError(
                "E_NOT_FOUND",
                "the layout template is neither in the central library nor in the installation",
                {"template": template, "library": str(template_dir), "available": available},
            )
        try:
            shutil.copytree(source, template_dir, dirs_exist_ok=True)
        except OSError as exc:
            raise AdapterError("E_IO", f"cannot copy the layout template: {exc}") from exc
        copied_template = True
    work = root / "Work" / "xpedition-cli"
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot create the work folder: {exc}") from exc
    log = work / "jobwizard.log"
    app, reopen = _release_project(client, project_path)
    reordered = False
    cells: list[str] = []
    try:
        # Designer may have rewritten the file while it had the project open
        text = project_path.read_text(encoding="utf-8", errors="surrogateescape")
        moved = project_file.move_design_first(text, design.name)
        if moved != text:
            project_path.write_text(moved, encoding="utf-8", errors="surrogateescape")
            reordered = True
        if log.exists():
            log.unlink()
        run = _run_library_tool(
            "JobWizard",
            [
                "-createnew",
                "-f",
                "-prj",
                str(project_path),
                "-newpcb",
                str(pcb_path),
                "-template",
                template,
                "-lib",
                str(lmc),
                "-l",
                str(log),
            ],
            timeout=JOB_WIZARD_TIMEOUT,
        )
        errors, copied_files = _jobwizard_messages(log)
        if pcb_path.is_file():
            cells = _sync_prj_cells(project_path, root)
    finally:
        if reopen and app is not None:
            try:
                _ensure_project(app, project_path)
            except AdapterError:
                pass
    if not pcb_path.is_file() or errors or run["exit_code"] != 0 or run["dialogs"]:
        raise AdapterError(
            "E_SERVER",
            "JobWizard did not create the board",
            {
                "exit_code": run["exit_code"],
                "dialogs": run["dialogs"],
                "errors": errors,
                "log": str(log),
                "pcb": str(pcb_path),
            },
        )
    verification = None
    if params.get("verify"):
        layout = _application(client, attach_only=True)
        _open_layout_document(layout, pcb_path)
        board = _reopen_board(layout, pcb_path)
        verification = {
            "source": "native_board_getters_after_save_close_open",
            "path": str(board.FullName),
            "layer_count": board.LayerCount,
            "counts": _board_counts(board),
            "snapshot": _snapshot(layout, client),
        }
    return {
        "project": str(project_path),
        "design": design.name,
        "pcb": str(pcb_path),
        "template": template,
        "template_copied": copied_template,
        "designs_reordered": reordered,
        "cells_registered": cells,
        "copied_files": copied_files,
        "replaced": removed,
        "backup": (removed or {}).get("backup"),
        "log": str(log),
        "ok": True,
        "native_readback": verification,
        "persistence_verified": verification is not None,
        "_untrusted": ["project", "pcb", "log", "cells_registered", "replaced", "backup"],
    }


def _layout_processes() -> list[int]:
    """The process ids of the running Layout instances (`ExpeditionPCB.exe`)."""
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ExpeditionPCB.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        parts = [item.strip('"') for item in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == "expeditionpcb.exe" and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def _quit_layout(client: Any) -> bool:
    """A locked board folder cannot authorize implicit CAD shutdown.

    Retain the legacy helper name for its replacement-folder call site. Normal
    shutdown is a separately authorized session action; process termination is
    never a folder-unlock fallback, even after COM unregistration or Close.
    """
    pids = _layout_processes()
    if not pids:
        return False
    raise AdapterError(
        "E_HUMAN_REQUIRED",
        "The board folder is still locked; an existing Layout was not shut down",
        {
            "pids": pids,
            "shutdown_attempted": False,
            "hint": "Preserve the document and obtain explicit normal-session closure "
            "authorization before continuing replacement; no force-termination fallback",
        },
    )


def _close_board_if_open(client: Any, pcb_path: Path) -> bool:
    """Save and normally close only the matching board; unknown state stops."""
    try:
        app = _application(client, attach_only=True)
    except AdapterError:
        if processes("pcb"):
            raise
        return False
    try:
        current = app.ActiveDocument
        if current is None:
            return False
        full = current.FullName
        if not isinstance(full, str) or not full:
            raise AdapterError("E_HUMAN_REQUIRED", "Active board identity is unavailable")
        if Path(full).resolve() != pcb_path.resolve():
            return False
        _save_close_board(app, pcb_path)
        _settle(3.0)
        return True
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "observe_or_close_document") from exc


def _save_close_board(app: Any, pcb_path: Path, *, lifecycle: dict | None = None) -> None:
    """Preserve changes and require an independently observed closed document."""
    doc = _licensed_document(app)
    if Path(str(doc.FullName)).resolve() != pcb_path.resolve():
        raise AdapterError("E_CONFLICT", "Active board changed before save/close")
    try:
        if lifecycle is not None:
            lifecycle["save_count"] += 1
            lifecycle.update(save_reply_received=False, save_acknowledged=None)
        saved = _native_call("layout.SaveBeforeClose", lambda: doc.Save())
        if lifecycle is not None:
            lifecycle["save_returned"] = saved
            lifecycle["save_return_type"] = type(saved).__name__
            lifecycle["save_reply_received"] = True
            lifecycle["save_acknowledged"] = type(saved) is bool and saved is True
            if type(saved) is not bool:
                raise AdapterError(
                    "E_VERIFY_INCOMPLETE",
                    "Layout Save did not return its declared BOOL; Close was not dispatched",
                    {
                        "save_returned": saved,
                        "save_return_type": type(saved).__name__,
                        "save_acknowledged": False,
                        "close_count": 0,
                    },
                )
        if saved is False:
            raise AdapterError("E_NATIVE_FAILURE", "Layout did not acknowledge saving before close")
    except Exception as exc:
        if lifecycle is not None and isinstance(exc, AdapterError):
            raise
        raise _com_error(exc, "save_before_document_close") from exc
    try:
        if lifecycle is not None:
            lifecycle["close_count"] += 1
            lifecycle.update(close_reply_received=False, close_acknowledged=None)
        closed = _native_call("layout.CloseSavedDocument", lambda: doc.Close(True))
        if lifecycle is not None:
            lifecycle.update(
                close_returned=closed,
                close_return_type=type(closed).__name__,
                close_reply_received=True,
                close_acknowledged=type(closed) is bool and closed is True,
            )
        _settle(0.5)
        remaining = app.ActiveDocument
        if lifecycle is not None:
            lifecycle["closed_observed"] = remaining is None
            if type(closed) is not bool or closed is not True or remaining is not None:
                raise AdapterError(
                    "E_VERIFY_INCOMPLETE",
                    "Layout close return and independent document observation did not both verify",
                    {
                        "save_acknowledged": True,
                        "close_returned": closed,
                        "close_return_type": type(closed).__name__,
                        "close_acknowledged": type(closed) is bool and closed is True,
                        "document_closed": remaining is None,
                    },
                )
        if closed is False or remaining is not None:
            raise AdapterError(
                "E_HUMAN_REQUIRED",
                "Normal board closure was not confirmed; further dispatch stopped",
                {"save_acknowledged": True, "close_returned": closed, "document_closed": False},
            )
    except Exception as exc:
        if isinstance(exc, AdapterError) and (lifecycle is None or "closed_observed" in lifecycle):
            raise
        error = exc if isinstance(exc, AdapterError) else _com_error(exc, "close_saved_document")
        if lifecycle is not None:
            # Close may have taken effect before its reply was lost. Observe
            # only; never resend Close, Save, Open or Quit to obtain a receipt.
            try:
                lifecycle["closed_observed"] = app.ActiveDocument is None
            except Exception as observation_error:
                lifecycle["closed_observed"] = None
                lifecycle["close_observation_error"] = type(observation_error).__name__
            raise AdapterError(
                "E_VERIFY_INCOMPLETE",
                "Layout close reply/verification was lost; no lifecycle action was replayed",
                {
                    "save_acknowledged": True,
                    "document_closed": lifecycle["closed_observed"],
                    "native_error": {
                        "code": error.code,
                        "message": error.message,
                        "details": error.details,
                    },
                },
            ) from exc
        error.details.update(save_acknowledged=True, document_closed="unknown")
        raise error from exc


def _backup_layout_folder(folder: Path) -> str | None:
    """Back up every file, verify archive bytes/source stability, then finalize.

    Locked files, unreadable entries, links and source drift fail closed. An
    unfinished archive is retained with an .incomplete suffix, never returned as
    a recovery point. This covers the supplied folder, not external references.
    """
    import stat
    import uuid
    import zipfile
    from datetime import UTC, datetime

    archive = folder.parent / (
        f"{folder.name}-backup-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.zip"
    )
    unfinished = archive.with_suffix(".zip.incomplete")

    def files(directory: Path) -> list[Path]:
        paths = []
        for entry in sorted(directory.iterdir()):
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError(f"Backup cannot follow a link/reparse entry: {entry}")
            if stat.S_ISDIR(info.st_mode):
                paths.extend(files(entry))
            elif stat.S_ISREG(info.st_mode):
                paths.append(entry)
            else:
                raise OSError(f"Backup cannot preserve this file type: {entry}")
        return paths

    try:
        try:
            info = folder.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError("Backup source is not a regular directory")
        sources = files(folder)
        if not sources:
            return None
        rows = []
        with zipfile.ZipFile(unfinished, "x", zipfile.ZIP_DEFLATED) as bundle:
            for item in sources:
                relative = item.relative_to(folder)
                data = item.read_bytes()
                rows.append(
                    {
                        "path": relative.as_posix(),
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
                bundle.writestr((Path(folder.name) / relative).as_posix(), data)
            bundle.writestr(
                "__xpedition_backup__/manifest.json",
                json.dumps({"source": str(folder), "complete": True, "files": rows}),
            )
        with zipfile.ZipFile(unfinished) as bundle:
            if bundle.testzip() is not None:
                raise OSError("Backup archive CRC verification failed")
            for item, row in zip(sources, rows, strict=True):
                name = (Path(folder.name) / row["path"]).as_posix()
                if hashlib.sha256(bundle.read(name)).hexdigest() != row["sha256"]:
                    raise OSError(f"Backup archive content verification failed: {row['path']}")
                if hashlib.sha256(item.read_bytes()).hexdigest() != row["sha256"]:
                    raise AdapterError(
                        "E_CONFLICT",
                        "Source changed during backup",
                        {"path": str(item), "backup_complete": False, "archive": str(unfinished)},
                    )
        if files(folder) != sources:
            raise AdapterError(
                "E_CONFLICT",
                "Source file set changed during backup",
                {"backup_complete": False, "archive": str(unfinished)},
            )
        unfinished.rename(archive)
    except OSError as exc:
        raise AdapterError(
            "E_IO",
            f"cannot back the layout folder up before deleting it: {exc}",
            {"folder": str(folder), "archive": str(unfinished), "backup_complete": False},
        ) from exc
    return str(archive)


def _remove_board(client: Any, project_path: Path, pcb_relative: str, name: str) -> dict[str, Any]:
    """Remove a design's layout data so the board can be created afresh: the folder is
    zipped up beside the project, the board is closed in Layout, the `PCB` folder
    deleted, and `PCBDesignPath` and `LayoutTemplate` cleared in the `.prj`. A board
    created again picks up every part's current cell, which forward annotation never
    changes on an existing component."""
    from . import project_file

    _archive_library_state(client, project_path)
    _release_project(client, project_path)
    folder = project_path.parent / "PCB"
    candidates = []
    if pcb_relative:
        candidates.append(project_path.parent / pcb_relative)
    candidates.append(folder / f"{name}.pcb")
    closed = False
    for candidate in candidates:
        if candidate.exists():
            closed = _close_board_if_open(client, candidate) or closed
    # Save/close precedes a verified complete project archive. Any locked icdb or
    # changed/unreadable file stops before deletion; no partial archive is usable.
    _require_layout_released(client, project_path)
    backup = _backup_layout_folder(project_path.parent)
    deleted = False
    if folder.is_dir():
        # Layout lets go of the folder a moment after the document closes
        last_error: OSError | None = None
        for _attempt in range(10):
            try:
                shutil.rmtree(folder)
                deleted = True
                break
            except OSError as exc:
                last_error = exc
                if not any(folder.rglob("*")):
                    deleted = True  # only the empty directory itself is still held
                    break
                time.sleep(2.0)
        if not deleted:
            # Folder replacement does not grant ownership of a running CAD.
            _quit_layout(client)
        if not deleted:
            raise AdapterError(
                "E_IO",
                f"cannot remove the layout folder: {last_error}",
                {"folder": str(folder)},
            )
    text = project_path.read_text(encoding="utf-8", errors="surrogateescape")
    cleared = 0
    for key in ("PCBDesignPath", "LayoutTemplate"):
        text, count = re.subn(rf'^(KEY {key} )"[^"]*"', r'\1""', text, flags=re.M)
        cleared += count
    project_path.write_text(text, encoding="utf-8", errors="surrogateescape")
    _ = project_file  # the keys above are what the module reads back
    return {
        "closed_in_layout": closed,
        "folder_deleted": deleted,
        "keys_cleared": cleared,
        "backup": backup,
    }


def _prepare_project_release(params: dict[str, Any], client: Any) -> tuple:
    """Observe all release dependencies while retaining the exact COM chain."""
    from . import native_library_manager
    from .native_release_preflight import ReleasePreflight, process_generation

    project = params.get("project")
    if not project:
        raise AdapterError("E_USAGE", "release_project requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    # A project-local central library is part of the archive too. Designer and
    # Layout closure alone does not establish that LibraryManager stopped writing.
    _archive_library_state(client, project_path)
    board = None
    try:
        board = _layout_board_path({"project": str(project_path)})
    except AdapterError:
        board = None

    def application_reader(domain):
        if domain == "library":
            return native_library_manager.active(client)[0]
        if domain == "schematic":
            return _viewdraw_application(client, attach_only=True)
        # Preflight must not alter GUI suppression settings or authenticate a DB.
        return _application(client, attach_only=True, quiet=False)

    preflight = ReleasePreflight(
        project_path,
        board if board is not None and board.exists() else None,
        process_reader=processes,
        generation_reader=process_generation,
        application_reader=application_reader,
        identity_reader=application_identity,
    )
    # Report the library from the same retained baseline as the other writers,
    # not the earlier quick guard observation above.
    library_observation = preflight.baseline["library"]
    if not library_observation["processes"]:
        library_state = {"status": "no_library_manager_process"}
    elif not library_observation["document"]["open"]:
        library_state = {"status": "no_active_library"}
    else:
        library_state = {
            "status": "outside_project",
            "active_library": library_observation["document"]["path"],
            "protection": "External libraries are not included in this project archive",
        }
    return project_path, board, library_state, preflight


def _release_preflight_result(project_path, board, library_state, preflight):
    return {
        "project": str(project_path),
        "board": str(board) if board else None,
        "library_state": library_state,
        "release_preflight": preflight.baseline,
        "read_only": True,
        "save_count": 0,
        "close_count": 0,
    }


def _project_release_preflight(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Strict read-only observation for public archive preview/confirmation."""
    project_path, board, library_state, preflight = _prepare_project_release(params, client)
    preflight.revalidate()
    return _release_preflight_result(project_path, board, library_state, preflight)


def _let_go_of_project(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Preflight every writer, then save/close only the frozen owned documents."""
    project_path, board, library_state, preflight = _prepare_project_release(params, client)
    observed = _release_preflight_result(project_path, board, library_state, preflight)
    expected = params.get("expected_release_preflight")
    try:
        matches = isinstance(expected, dict) and json.dumps(
            expected, sort_keys=True, allow_nan=False
        ) == json.dumps(observed, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        matches = False
    if "expected_release_preflight" in params and not matches:
        raise AdapterError(
            "E_CONFLICT",
            "Project release dependencies changed since the bound preflight",
            {
                "write_attempted": False,
                "save_count": 0,
                "close_count": 0,
                "expected_release_preflight": params["expected_release_preflight"],
                "observed_release_preflight": observed,
            },
        )
    designer_closed = layout_closed = False
    lifecycle = {"save_count": 0, "close_count": 0}
    layout_lifecycle = {"save_count": 0, "close_count": 0}
    progress = {"designer": lifecycle, "layout": layout_lifecycle}
    closed_domains = []
    try:
        # Re-read the same retained COM chain and the complete OS writer set
        # immediately before the first side effect, rather than reacquiring ROT.
        preflight.revalidate()
        if preflight.targets("schematic"):
            designer_closed = _close_if_open(
                preflight.apps["schematic"], project_path, lifecycle=lifecycle
            )
            if designer_closed:
                preflight.confirm_designer_closed(lifecycle)
                closed_domains.append("schematic")
        preflight.revalidate(closed=closed_domains)
        if preflight.targets("pcb"):
            _save_close_board(preflight.apps["pcb"], board, lifecycle=layout_lifecycle)
            layout_closed = True
            closed_domains.append("pcb")
            _settle(2.0)
        # This checks remaining project documents and every OS writer using the
        # retained references. Do not reacquire/quiet an unrelated default ROT.
        preflight.revalidate(closed=closed_domains)
    except AdapterError as error:
        error.details.update(
            write_attempted=bool(lifecycle["save_count"] or layout_lifecycle["save_count"]),
            release_progress={
                **progress,
                "designer_closed": designer_closed,
                "layout_closed": layout_closed,
            },
        )
        raise
    return {
        "project": str(project_path),
        "designer_closed": designer_closed,
        "layout_closed": layout_closed,
        "quiescent": True,
        "library_state": library_state,
        "board": str(board) if board else None,
        "release_preflight": preflight.baseline,
        "post_close_identity": preflight.closed_observations,
        "release_progress": progress,
        "_untrusted": ["project", "board"],
    }


def _require_layout_released(client: Any, project: Path) -> None:
    """Observe remaining document state after any declared-board close attempt."""
    try:
        layout = _application(client, attach_only=True)
    except AdapterError:
        if processes("pcb"):
            raise
        return
    try:
        document = layout.ActiveDocument
        if document is None:
            return
        name = document.FullName
        if not isinstance(name, str) or not name:
            raise ValueError("Active board identity is unavailable")
        active_path = Path(name).resolve()
        if project.parent.resolve() in active_path.parents:
            raise AdapterError(
                "E_HUMAN_REQUIRED",
                "A board in this project remains open; archive/restore stopped",
                {
                    "board": str(active_path),
                    "backup_complete": False,
                    "action": "Preserve and normally release the remaining task document",
                },
            )
        # Identified outside documents are neither saved nor closed by this task.
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "observe_remaining_layout_document") from exc


def _archive_library_state(client: Any, project: Path) -> dict[str, Any]:
    """Refuse an active or unobservable library writer; never close its window."""
    from . import native_library_manager

    if not processes("library"):
        return {"status": "no_library_manager_process"}
    try:
        app, _ = native_library_manager.active(client)
        library = app.ActiveLibrary
        if library is None:
            return {"status": "no_active_library"}
        name = library.FullName
        if not isinstance(name, str) or not name:
            raise ValueError("Active library identity is unavailable")
        path = Path(name).resolve()
    except Exception as exc:
        raise AdapterError(
            "E_HUMAN_REQUIRED",
            "LibraryManager state is unavailable; project archive stopped",
            {
                "action": "Inspect LibraryManager and normally release the owned library",
                "backup_complete": False,
                "library_state": "not_observed",
                "write_attempted": False,
                "cause": str(exc),
            },
        ) from exc
    if path == project.parent or project.parent in path.parents:
        raise AdapterError(
            "E_HUMAN_REQUIRED",
            "The project library is still open; project archive stopped",
            {
                "action": "Save and normally close this owned library before archiving",
                "library": str(path),
                "backup_complete": False,
                "write_attempted": False,
            },
        )
    return {
        "status": "outside_project",
        "active_library": str(path),
        "protection": "External libraries are not included in this project archive",
    }


def _take_up_project(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Open the project in Designer again, when Designer is running (`reopen_project`)."""
    project = params.get("project")
    if not project:
        raise AdapterError("E_USAGE", "reopen_project requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    try:
        app = _viewdraw_application(client, attach_only=True)
    except AdapterError:
        return {"project": str(project_path), "reopened": False, "_untrusted": ["project"]}
    _ensure_project(app, project_path)
    return {"project": str(project_path), "reopened": True, "_untrusted": ["project"]}


def _board_counts(doc: Any) -> dict[str, Any]:
    """How many components and nets the board holds now."""
    counts: dict[str, Any] = {}
    for key in ("Components", "Nets"):
        collection = _com_member(doc, key)
        try:
            counts[key.lower()] = int(collection.Count) if collection is not None else None
        except Exception:
            counts[key.lower()] = None
    return counts


def _unit_code(unit: Any) -> int:
    """The `EPcbUnit` code for a unit name; the document's current unit by default."""
    if unit is None or unit == "":
        return 0
    if isinstance(unit, int):
        return unit
    try:
        return UNIT_CODES[str(unit).lower()]
    except KeyError as exc:
        raise AdapterError(
            "E_USAGE", "unknown unit", {"unit": str(unit), "known": sorted(UNIT_CODES)}
        ) from exc


def _board_rect(doc: Any) -> tuple[float, float, float, float]:
    """The board outline's bounding rectangle in millimetres."""
    outline = _value(doc, "BoardOutline", default=None)
    if outline is None:
        raise AdapterError("E_NOT_FOUND", "the board has no outline")
    try:
        # the outline's extents: `Geometry.GetRect*` only answers for a plain rectangle
        # and calls a rounded outline "incorrect geometry"
        return _extrema_mm(outline)
    except Exception as exc:
        raise _com_error(exc, "board_outline") from exc


def _extrema_mm(obj: Any) -> tuple[float, float, float, float]:
    extrema = obj.Extrema
    return (
        float(extrema.GetMinX(UNIT_MM)),
        float(extrema.GetMinY(UNIT_MM)),
        float(extrema.GetMaxX(UNIT_MM)),
        float(extrema.GetMaxY(UNIT_MM)),
    )


def _measure_component(component: Any, x: float, y: float) -> tuple[float, float]:
    """Width and height of a component's footprint in millimetres.

    Layout has no extents for an unplaced component, so one is placed at (x, y) for
    the measurement and unplaced again; a placed one is measured where it is.
    """
    was_placed = bool(_value(component, "Placed", default=False))
    if not was_placed:
        component.Place(x, y, 0.0, True, 0, UNIT_MM, 0)
    try:
        min_x, min_y, max_x, max_y = _extrema_mm(component)
        return max(max_x - min_x, 0.0), max(max_y - min_y, 0.0)
    finally:
        if not was_placed:
            component.UnPlace()


LABEL_LAYER_SILKSCREEN = 2  # epcbFabSilkscreen
SIDE_TOP = 1  # epcbSideTop
LABEL_PEN_MM = 0.15
LABEL_FONT = "VeriBest Gerber 0"


def _board_nets(doc: Any) -> list[Any]:
    """The board's nets as `board_layout.Net`: name, (refdes, pin) members, supply flag."""
    from . import board_layout

    nets: list[Any] = []
    for net in _items(_com_member(doc, "Nets")):
        name = str(_value(net, "Name", default=""))
        power = _com_member(net, "IsPower")
        pins: list[tuple[str, str]] = []
        for pin in _items(_com_member(net, "Pins")):
            try:
                pins.append((str(pin.Component.RefDes), str(_value(pin, "Name", default=""))))
            except Exception:
                continue
        nets.append(board_layout.Net(name, pins, bool(power)))
    return nets


def _delete_routing(doc: Any) -> dict[str, int]:
    """Delete every trace and via on the board; how many of each went."""
    counts = {"traces": 0, "vias": 0}
    for key in ("Traces", "Vias"):
        collection = _com_member(doc, key)
        if collection is None:
            continue
        for item in list(_items(collection)):
            try:
                item.Delete()
                counts[key.lower()] += 1
            except Exception:
                continue
    return counts


def _routing_counts(doc: Any) -> dict[str, int]:
    counts = {}
    for key in ("Traces", "Vias"):
        collection = _com_member(doc, key)
        try:
            counts[key.lower()] = int(collection.Count) if collection is not None else 0
        except Exception:
            counts[key.lower()] = 0
    return counts


class _placement_drc_off:
    """Layout's online placement DRC off for the duration, restored afterwards.

    With it on, `Place` refuses a part that would touch another one, including a
    part's own old footprint, and a first placement moves parts through each other.
    """

    def __init__(self, doc: Any) -> None:
        self.doc = doc
        self.previous: Any = None

    def __enter__(self) -> _placement_drc_off:
        try:
            self.previous = bool(self.doc.RespectComponentPlacementDRC)
            self.doc.RespectComponentPlacementDRC = False
        except Exception:
            self.previous = None
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.previous is not None:
            try:
                self.doc.RespectComponentPlacementDRC = self.previous
            except Exception:
                pass


def _survey_parts(
    doc: Any,
    board: tuple[float, float, float, float],
    include_placed: bool,
) -> tuple[list[Any], list[str], list[Any]]:
    """Every part to plan, measured on the board: the parts, the placed ones left
    alone, and the components that were parked for the survey.

    A part that is not placed has no pins in `Net.Pins`, so every unplaced part is
    measured alone at the board's centre (placed and unplaced again), then parked in
    a row grid below the outline where nothing touches — Layout refuses two identical
    footprints on one spot as a DRC violation but accepts a part outside the board —
    until the caller decides where it goes.
    """
    from . import board_layout

    centre = ((board[0] + board[2]) / 2, (board[1] + board[3]) / 2)
    parts: list[Any] = []
    skipped: list[str] = []
    lifted: list[tuple[Any, Any]] = []
    for component in _items(_com_member(doc, "Components")):
        refdes = str(_value(component, "RefDes", default=""))
        if not refdes:
            continue
        was_placed = bool(_value(component, "Placed", default=False))
        if was_placed and not include_placed:
            skipped.append(refdes)
            continue
        try:
            width, height, offset_x, offset_y, pins = _measure_part(component, *centre)
        except Exception as exc:
            raise _com_error(exc, f"measure_component {refdes}") from exc
        part = board_layout.Part(
            refdes,
            width,
            height,
            str(_value(component, "CellName", default="")),
            round(offset_x, 3),
            round(offset_y, 3),
            pins=pins,
        )
        parts.append(part)
        if not was_placed:
            lifted.append((component, part))
    # park the lifted parts in rows below the board, one gap apart
    gap = board_layout.GAP
    x = board[0]
    row_top = board[1] - 2 * gap
    row_height = 0.0
    parked: list[Any] = []
    for component, part in lifted:
        width, height = max(part.width, 0.5) + gap, max(part.height, 0.5) + gap
        if x > board[0] and x + width > board[2]:
            row_top -= row_height
            x = board[0]
            row_height = 0.0
        try:
            component.Place(
                round(x + width / 2 + part.offset_x, 3),
                round(row_top - height / 2 + part.offset_y, 3),
                0.0,
                True,
                0,
                UNIT_MM,
                0,
            )
        except Exception as exc:
            raise _com_error(exc, f"park_component {part.refdes}") from exc
        parked.append(component)
        x += width
        row_height = max(row_height, height)
    return parts, skipped, parked


def _pin_offsets(
    component: Any, centre: tuple[float, float], orientation: float
) -> dict[str, tuple[float, float]]:
    """Pin name -> offset from the footprint's centre, unturned, in mm."""
    offsets: dict[str, tuple[float, float]] = {}
    angle = math.radians(-orientation)
    for pin in _items(_com_member(component, "Pins")):
        try:
            name = str(_value(pin, "Name", default=""))
            dx = float(pin.GetPositionX(UNIT_MM)) - centre[0]
            dy = float(pin.GetPositionY(UNIT_MM)) - centre[1]
        except Exception:
            continue
        if orientation % 360:
            dx, dy = (
                dx * math.cos(angle) - dy * math.sin(angle),
                dx * math.sin(angle) + dy * math.cos(angle),
            )
        offsets[name] = (round(dx, 3), round(dy, 3))
    return offsets


def _measure_part(
    component: Any, x: float, y: float
) -> tuple[float, float, float, float, dict[str, tuple[float, float]]]:
    """Width, height, the cell origin's offset from the footprint's centre and the
    pins' offsets from that centre, in mm.

    Layout has no extents for an unplaced component, so one is placed at (x, y) for
    the measurement and unplaced again; a placed one is measured where it is.
    """
    was_placed = bool(_value(component, "Placed", default=False))
    if not was_placed:
        component.Place(x, y, 0.0, True, 0, UNIT_MM, 0)
    try:
        min_x, min_y, max_x, max_y = _extrema_mm(component)
        origin_x = float(component.GetPositionX(UNIT_MM))
        origin_y = float(component.GetPositionY(UNIT_MM))
        width, height = max(max_x - min_x, 0.0), max(max_y - min_y, 0.0)
        centre = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        offset_x, offset_y = origin_x - centre[0], origin_y - centre[1]
        try:
            orientation = float(_value(component, "Orientation", default=0.0) or 0.0)
        except (TypeError, ValueError):
            orientation = 0.0
        if not was_placed:
            orientation = 0.0
        pins = _pin_offsets(component, centre, orientation)
        if was_placed and orientation % 360:
            # a placed part is measured as it stands; the planner wants it unturned
            # (a connector standing at 90° came back 5.5 wide and 10.9 tall, and the
            # plan turned it again)
            angle = math.radians(-orientation)
            offset_x, offset_y = (
                offset_x * math.cos(angle) - offset_y * math.sin(angle),
                offset_x * math.sin(angle) + offset_y * math.cos(angle),
            )
            if round(orientation) % 180 == 90:
                width, height = height, width
        return width, height, offset_x, offset_y, pins
    finally:
        if not was_placed:
            component.UnPlace()


def _origin_for(item: Any, part: Any) -> tuple[float, float]:
    """Where to put the cell origin so the footprint's centre lands on the plan."""
    ox, oy = part.offset_x, part.offset_y
    if item.rotation % 360 == 90:
        ox, oy = -oy, ox
    elif item.rotation % 360 == 180:
        ox, oy = -ox, -oy
    elif item.rotation % 360 == 270:
        ox, oy = oy, -ox
    return round(item.x + ox, 3), round(item.y + oy, 3)


def _remove_labels(doc: Any, texts: set[str]) -> int:
    """Delete our earlier zone labels (silkscreen texts with one of `texts`)."""
    removed = 0
    try:
        collection = _com_member(doc, "FabricationLayerTexts")
    except Exception:
        return 0
    if collection is None:
        return 0
    try:
        for text in list(_items(collection)):
            try:
                if str(_value(text, "TextString", default="")) in texts:
                    text.Delete()
                    removed += 1
            except Exception:
                continue
    except Exception:
        return removed
    return removed


def _arrange_components(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Place the board's components the way `board_layout.arrange` plans them.

    Without `apply` this is the preview: the plan and its digest, after measuring
    every part (an unplaced part is placed and unplaced again for that; nothing is
    saved). With `apply`, the plan is recomputed, compared with the dry run's
    `digest`, applied through `Component.Place`, the sheet zone labels are written
    on the top silkscreen and the board is saved.
    """
    from . import board_layout

    pcb_path = _layout_board_path(params)
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    board = _board_rect(doc)
    include_placed = bool(params.get("all", False))
    zones = {str(k): str(v) for k, v in (params.get("zones") or {}).items()}
    with _placement_drc_off(doc):
        parts, skipped, lifted = _survey_parts(doc, board, include_placed)
        try:
            # a part that is not placed has no pins in `Net.Pins`, so the nets are
            # read while everything stands on the board
            nets = _board_nets(doc)
        except Exception as exc:
            raise _com_error(exc, "read_nets") from exc
        keepout = 0.0
        try:
            # once the corners hold mounting holes, the planner keeps them clear
            if int(_com_member(doc, "MountingHoles").Count) > 0:
                keepout = CORNER_KEEPOUT_MM
        except Exception:
            keepout = 0.0
        plan = board_layout.arrange(
            parts, board, nets, zones, grid=board_layout.GRID, corner_keepout=keepout
        )
        apply = bool(params.get("apply", False))
        pace = _pace(params)
        outside = [item.refdes for item in plan.placements if not item.inside]
        if apply and outside:
            # a part planned past the outline lands on the parked ones below it, and
            # Layout stops the placement with a DRC violation halfway through
            for component in lifted:
                try:
                    component.UnPlace()
                except Exception:
                    pass
            raise AdapterError(
                "E_VALIDATION",
                "the outline is too small for the plan; enlarge it with pcb outline set",
                {"outside": outside, "board": list(board)},
            )
        if not apply:
            for component in lifted:
                try:
                    component.UnPlace()
                except Exception:
                    pass
    digest = plan.digest()
    expected = str(params.get("digest") or "")
    if apply and expected and expected != digest:
        raise AdapterError(
            "E_CONFLICT",
            "the board changed since the dry run; run the dry run again",
            {"expected": expected, "digest": digest},
        )
    by_refdes = {part.refdes: part for part in parts}
    placed: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    saved = False
    routing = _routing_counts(doc)
    removed_routing = {"traces": 0, "vias": 0}
    if apply:
        with _placement_drc_off(doc):
            # a placement change makes every trace wrong, and a part placed onto a
            # trace is a DRC violation to Layout: the routing goes first
            removed_routing = _delete_routing(doc)
            # a part placed onto another one, or onto its own old footprint, is a DRC
            # violation to Layout, so everything in the plan is lifted first
            for item in plan.placements:
                component = _find_component(doc, item.refdes)
                if bool(_value(component, "Placed", default=False)):
                    try:
                        component.UnPlace()
                    except Exception as exc:
                        raise _com_error(exc, f"unplace_component {item.refdes}") from exc
            for item in plan.placements:
                component = _find_component(doc, item.refdes)
                origin_x, origin_y = _origin_for(item, by_refdes[item.refdes])
                try:
                    component.Place(origin_x, origin_y, item.rotation, True, 0, UNIT_MM, 0)
                    if pace:
                        time.sleep(pace)  # let Layout repaint between parts
                    record = item.as_dict()
                    record["placed"] = bool(_value(component, "Placed", default=False))
                    record["read_back"] = [
                        round(float(component.GetPositionX(UNIT_MM)), 3),
                        round(float(component.GetPositionY(UNIT_MM)), 3),
                    ]
                except Exception as exc:
                    raise _com_error(exc, f"place_component {item.refdes}") from exc
                placed.append(record)
        removed = _remove_labels(doc, {label.text for label in plan.labels})
        for label in plan.labels:
            try:
                doc.PutFabricationLayerText(
                    label.text,
                    label.x,
                    label.y,
                    LABEL_LAYER_SILKSCREEN,
                    SIDE_TOP,
                    board_layout.LABEL_HEIGHT,
                    0.0,
                    LABEL_PEN_MM,
                    LABEL_FONT,
                    0,
                    0,
                    0,
                    None,
                    UNIT_MM,
                    0,
                )
                labels.append({**label.as_dict(), "written": True})
            except Exception as exc:
                labels.append({**label.as_dict(), "written": False, "error": str(exc)[:120]})
        try:
            doc.Save()
            saved = True
        except Exception as exc:
            raise _com_error(exc, "save_after_arrange") from exc
    else:
        removed = 0
    return {
        "pcb": str(pcb_path),
        "board": {
            "min_x": board[0],
            "min_y": board[1],
            "max_x": board[2],
            "max_y": board[3],
            "unit": "mm",
        },
        "plan": [item.as_dict() for item in plan.placements],
        "labels": [label.as_dict() for label in plan.labels] if not apply else labels,
        "clusters": plan.clusters,
        "summary": plan.summary(),
        "nets": len(nets),
        "digest": digest,
        "skipped_placed": skipped,
        "corner_keepout": keepout,
        "routing": routing,
        "routing_removed": removed_routing,
        "applied": apply,
        "placed": placed,
        "labels_replaced": removed,
        "saved": saved,
        "prompts": prompts,
        "_untrusted": ["pcb", "plan", "labels", "clusters", "placed", "skipped_placed", "prompts"],
    }


def _outline_points(width: float, height: float) -> list[list[float]]:
    """A closed rectangle from the origin as Layout's points array (x row, y row, radius row)."""
    return [
        [0.0, width, width, 0.0, 0.0],
        [0.0, 0.0, height, height, 0.0],
        [0.0, 0.0, 0.0, 0.0, 0.0],
    ]


ROUTE_BORDER_INSET_MM = 0.3  # copper to board edge, the convention's minimum


def _rectangle_points(min_x: float, min_y: float, max_x: float, max_y: float) -> list[list[float]]:
    """A closed rectangle as Layout's points array (x row, y row, radius row)."""
    return [
        [min_x, max_x, max_x, min_x, min_x],
        [min_y, min_y, max_y, max_y, min_y],
        [0.0, 0.0, 0.0, 0.0, 0.0],
    ]


def _rounded_points(
    min_x: float, min_y: float, max_x: float, max_y: float, radius: float
) -> list[list[float]]:
    """A closed rectangle with rounded corners as Layout's points array. Each corner is
    three rows — the arc's start, its centre carrying the radius, its end — and the
    radius on the centre row is negative: that draws the 90° arc on a counter-clockwise
    outline, a positive one draws the other 270° (found by drawing both)."""
    if radius <= 0:
        return _rectangle_points(min_x, min_y, max_x, max_y)
    r = radius
    corners = [
        ((max_x - r, min_y), (max_x - r, min_y + r), (max_x, min_y + r)),
        ((max_x, max_y - r), (max_x - r, max_y - r), (max_x - r, max_y)),
        ((min_x + r, max_y), (min_x + r, max_y - r), (min_x, max_y - r)),
        ((min_x, min_y + r), (min_x + r, min_y + r), (min_x + r, min_y)),
    ]
    rows: list[tuple[float, float, float]] = []
    for start, centre, end in corners:
        rows += [(start[0], start[1], 0.0), (centre[0], centre[1], -r), (end[0], end[1], 0.0)]
    rows.append(rows[0])
    return [[row[0] for row in rows], [row[1] for row in rows], [row[2] for row in rows]]


OUTPUT_COMMANDS = {"ncdrill": 33018, "odb": 33017, "gerber": 33016}  # Output menu commands
OUTPUT_FORMATS = ("odb", "gerber", "ncdrill")
OUTPUT_PROMPTS: tuple[tuple[str, str, str | None], ...] = (
    ("NC 钻孔生成", "确定", None),
    ("NC Drill Generation", "OK", None),
    ("ODB++ 设计输出", "确定(O)", None),
    ("ODB++ Design Output", "OK", None),
    ("Gerber 输出", "确定(O)", None),
    ("Gerber Output", "OK", None),
)
OUTPUT_TIMEOUT = 300.0


def parse_output_formats(value: Any) -> list[str]:
    """`odb,gerber,ncdrill` (any order, any subset) as a list; everything by default."""
    if value is None or value == "":
        return list(OUTPUT_FORMATS)
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    formats: list[str] = []
    for item in items:
        name = str(item).strip().lower().replace("+", "")
        if name == "odbpp":
            name = "odb"
        if name == "drill":
            name = "ncdrill"
        if not name:
            continue
        if name not in OUTPUT_FORMATS:
            raise AdapterError(
                "E_VALIDATION", f"unknown output format {item!r}", {"choices": list(OUTPUT_FORMATS)}
            )
        if name not in formats:
            formats.append(name)
    return formats


def _output_dialogs() -> list[str]:
    """Titled Layout windows that are not documents: the output dialogs and progress boxes."""
    from . import win_dialogs

    return [
        window["title"]
        for window in win_dialogs.top_windows("expeditionpcb.exe")
        if window["title"] and not window["title"].startswith("[")
    ]


def _run_output_command(app: Any, command: int, timeout: float) -> dict[str, Any]:
    """Run one Output menu command: the helper thread presses its dialog's OK, and the
    call waits until the dialog and its progress box are gone."""
    started = time.monotonic()
    with _PromptAnswerer(OUTPUT_PROMPTS, interval=0.7) as answerer:
        try:
            accepted = app.Gui.ProcessCommand(command)
        except Exception as exc:
            raise _com_error(exc, f"output_command {command}") from exc
        if not accepted:
            raise AdapterError(
                "E_SERVER", "Layout did not accept the output command", {"id": command}
            )
        time.sleep(2.0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and _output_dialogs():
            time.sleep(1.0)
    return {
        "command": command,
        "seconds": round(time.monotonic() - started, 1),
        "prompts": answerer.answered,
        "finished": not _output_dialogs(),
    }


def _output_setups(config: Path) -> dict[str, Any]:
    """The output setup files with the changes the package needs (drill spans and the
    outline in the ODB++ job, the cells' silkscreen and the outline in the Gerber set)."""
    from . import fab_package

    setups: dict[str, Any] = {}
    for name, patcher in (
        ("ODBSetup.ocf", fab_package.patch_odb_setup),
        ("ODBSetup.eocf", fab_package.patch_odb_setup),
        ("PlotSetup.gpf", fab_package.patch_gerber_setup),
        ("GerberPlot.egpf", fab_package.patch_gerber_setup),
    ):
        path = config / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise AdapterError("E_IO", f"cannot read {name}: {exc}") from exc
        patched, changes = patcher(text)
        setups[name] = {"changes": changes, "text": patched}
    return setups


def _manufacturing_output(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Write the board's fabrication outputs and gather them into a package folder.

    Layout has no automation call for ODB++, Gerber or NC drill; their dialogs
    (`Output` menu commands 33017, 33016, 33018) are run with the helper thread pressing
    OK. The dialogs keep their settings in memory while the board is open and rewrite
    `Config/ODBSetup.ocf` and `Config/PlotSetup.gpf` on OK, so the setups are patched
    with the board closed (drill spans and the outline into the ODB++ job, the cells'
    silkscreen and the outline into the Gerber set) and the board is opened again.
    Without `apply`, only the plan is reported and Layout is not touched.
    """
    from . import fab_package

    pcb_path = _layout_board_path(params)
    formats = parse_output_formats(params.get("formats"))
    apply = bool(params.get("apply", False))
    board_dir = pcb_path.parent
    config = board_dir / "Config"
    output_root = board_dir / "Output"
    target = (
        Path(str(params["output"])).expanduser()
        if params.get("output")
        else output_root / f"fab-{pcb_path.stem}-{time.strftime('%Y%m%d-%H%M')}"
    )
    setups = _output_setups(config)
    odb_dir = (
        next((p for p in sorted((output_root / "ODBpp").glob("*")) if p.is_dir()), None)
        if (output_root / "ODBpp").is_dir()
        else None
    )
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "formats": formats,
        "package": str(target),
        "setup_changes": {name: item["changes"] for name, item in setups.items()},
        "existing": {
            "gerber": len(list((output_root / "Gerber").glob("*.gdo")))
            if (output_root / "Gerber").is_dir()
            else 0,
            "ncdrill": len(list((output_root / "NCDrill").glob("*.ncd")))
            if (output_root / "NCDrill").is_dir()
            else 0,
            "odb": str(odb_dir) if odb_dir else None,
        },
        "applied": False,
        "_untrusted": ["pcb", "package", "prompts"],
    }
    if not apply:
        return result
    # an output folder keeps an earlier export's files; anything older than this run
    # did not come from it and stays out of the package
    since = time.time()
    closed = False
    runs: list[dict[str, Any]] = []
    prompts: list[dict[str, Any]] = []
    timeout = float(params.get("timeout", OUTPUT_TIMEOUT))
    rounds = 0
    while True:
        rounds += 1
        if any(item["changes"] for item in setups.values()):
            closed = _close_board_if_open(client, pcb_path) or closed
            for name, item in setups.items():
                if not item["changes"]:
                    continue
                path = config / name
                backup = config / f"{name}.bak-xpedition-cli"
                try:
                    if not backup.exists():
                        backup.write_bytes(path.read_bytes())
                    path.write_text(item["text"], encoding="utf-8")
                except OSError as exc:
                    raise AdapterError("E_IO", f"cannot write {name}: {exc}") from exc
            if closed:
                _settle(3.0)
        app = _application(client, attach_only=not bool(params.get("start", True)))
        _doc, opened = _open_layout_document(app, pcb_path)
        prompts = prompts + opened
        doc = _licensed_document(app)
        for name in ("ncdrill", "odb", "gerber"):
            if name not in formats:
                continue
            run = _run_output_command(app, OUTPUT_COMMANDS[name], timeout)
            run["format"] = name
            run["round"] = rounds
            runs.append(run)
            prompts = prompts + run["prompts"]
        # a dialog opened earlier in this Layout session writes its own settings back
        # over the patched file when OK is pressed; when the patch is pending again
        # after the run, the board is closed, patched and run once more
        setups = _output_setups(config)
        if rounds >= 2 or not any(item["changes"] for item in setups.values()):
            break
    board = _board_rect(doc)
    size = {
        "width": round(board[2] - board[0], 3),
        "height": round(board[3] - board[1], 3),
        "unit": "mm",
    }
    try:
        layer_count = int(_value(doc, "LayerCount", default=0) or 0)
    except (TypeError, ValueError):
        layer_count = 0
    components = [
        _component_record(component)
        for component in _items(_com_member(doc, "Components"))
        if str(_value(component, "RefDes", default=""))
    ]
    odb_dir = (
        next((p for p in sorted((output_root / "ODBpp").glob("*")) if p.is_dir()), None)
        if (output_root / "ODBpp").is_dir()
        else None
    )
    try:
        manifest = fab_package.write_package(
            target,
            pcb_path.stem,
            size,
            layer_count,
            output_root / "Gerber" if "gerber" in formats else None,
            output_root / "NCDrill" if "ncdrill" in formats else None,
            odb_dir if "odb" in formats else None,
            components,
            requested=list(formats),
            runs=runs,
            since=since,
        )
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot write the package: {exc}") from exc
    result.update(manifest)
    result.update(
        {
            "runs": runs,
            "rounds": rounds,
            "closed_and_reopened": closed,
            "prompts": prompts,
            "applied": True,
            "_untrusted": ["pcb", "package", "prompts", "runs", "files", "gerber", "drill", "odb"],
        }
    )
    return result


CONSTRAINTS_PROGID = "ConstraintsAuto"  # Constraint Manager's automation server
CONSTRAINT_CONTEXT_LAYOUT = 1  # DesignParams.DesignContext; 0 answers "design context missing"
CONSTRAINT_MASK_ALL = 7
TRACE_WIDTH_TYPES = {283: "minimum", 439: "typical", 183: "expansion"}  # Constraint.ConstraintType
TH_PER_MM = 1000.0 / 25.4  # the constraint values come and go in thousandths of an inch
MASTER_SCHEME = "(Master)"


def _constraint_design(project_path: Path, board: str) -> tuple[Any, Any]:
    """Constraint Manager's automation with the board's layout-context design loaded."""
    _configure_environment()  # the server needs SDD_HOME and Layout's PATH
    _, client = _import_com()
    try:
        auto = client.Dispatch(CONSTRAINTS_PROGID)  # late-bound; makepy cannot read its typelib
    except Exception as exc:
        raise _com_error(exc, "constraints_automation") from exc
    design = auto.Design
    params = design.CreateDesignParams()
    params.ProjectFile = str(project_path)
    params.Board = board
    params.DesignContext = CONSTRAINT_CONTEXT_LAYOUT
    try:
        design.Load(params)
    except Exception as exc:
        raise _com_error(exc, f"constraints_load {board}") from exc
    return auto, design


CES_DESIGN_NETS = 1610743811  # IDesign.Nets([in, optional] long p_nNetMask = 1)
CES_NET_MASK = 1 | 4  # NM_Nets | NM_PowerNets: the default mask left out GND and +5V


def _ces_nets(design: Any) -> dict[str, Any]:
    """Every net of the loaded design by name, the power nets included."""
    nets: dict[str, Any] = {}
    for net in _collection(_propget(design, CES_DESIGN_NETS, CES_NET_MASK)):
        nets.setdefault(str(net.Name), net)
    return nets


def _collection(obj: Any) -> list[Any]:
    try:
        count = int(obj.Count)
    except Exception:
        return []
    return [obj.Item(index) for index in range(1, count + 1)]


def _class_widths(design: Any, class_name: str) -> dict[str, dict[str, float]]:
    """The trace widths (mm) of a net class per layer in the master scheme."""
    widths: dict[str, dict[str, float]] = {}
    schemes = design.PhysicalRules.Schemes
    scheme = next((s for s in _collection(schemes) if str(s.Name) == MASTER_SCHEME), None)
    if scheme is None:
        scheme = schemes.Item(1)
    try:
        scheme_class = scheme.NetClasses.Item(class_name)
    except Exception:
        return widths
    for layer in _collection(scheme_class.Layers):
        row: dict[str, float] = {}
        for constraint in _collection(layer.GetConstraints(CONSTRAINT_MASK_ALL)):
            kind = TRACE_WIDTH_TYPES.get(int(constraint.ConstraintType))
            if kind:
                row[kind] = round(float(constraint.Value) / TH_PER_MM, 4)
        widths[str(layer.Name)] = row
    return widths


def _net_rules(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """The complete class/assignment/all-layer width/synchronization path is ineligible."""
    raise AdapterError(
        "E_UNSUPPORTED",
        "The full widths command has no verified native contract; a single "
        "IConstraint.Value PUT cannot validate class creation, net assignment, all three "
        "width fields on every layer, Layout synchronization and persistence.",
        {"write_attempted": False, "can_apply": False, "native_state": "not_observed"},
    )

    # Historical implementation remains unreachable until its complete contract is verified.
    from . import project_file

    project = params.get("project") or params.get("path")
    if not project:
        raise AdapterError("E_USAGE", "net_rules requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    pcb_path = _layout_board_path(params)
    text = project_path.read_text(encoding="utf-8", errors="replace")
    design_entry = project_file.board_design(project_file.designs(text))
    if design_entry is None:
        raise AdapterError("E_NOT_FOUND", "the project has no board design")
    class_name = str(params.get("class") or "").strip()
    nets = [str(item).strip() for item in (params.get("nets") or []) if str(item).strip()]
    typical = params.get("width")
    apply = bool(params.get("apply", False))
    if apply and (not class_name or typical is None):
        raise AdapterError("E_USAGE", "net_rules needs a class name and a typical width")
    widths: dict[str, float] = {}
    if typical is not None:
        try:
            widths["typical"] = float(typical)
            widths["minimum"] = float(params.get("minimum") or widths["typical"] * 0.8)
            widths["expansion"] = float(params.get("expansion") or widths["typical"] * 1.2)
        except (TypeError, ValueError) as exc:
            raise AdapterError("E_USAGE", "widths are millimetres") from exc
        if min(widths.values()) <= 0:
            raise AdapterError("E_VALIDATION", "widths must be positive")
    _auto, design = _constraint_design(project_path, design_entry.name)
    try:
        classes = design.NetClasses
        existing = {str(item.Name): item for item in _collection(classes)}
        all_nets = _ces_nets(design)
        membership: dict[str, list[str]] = {name: [] for name in existing}
        for name, net in all_nets.items():
            try:
                membership.setdefault(str(net.NetClass.Name), []).append(name)
            except Exception:
                continue
        current = {
            name: {"nets": membership.get(name, []), "widths": _class_widths(design, name)}
            for name in existing
        }
        missing = [name for name in nets if name not in all_nets]
        result: dict[str, Any] = {
            "pcb": str(pcb_path),
            "board": design_entry.name,
            "class": class_name,
            "nets": nets,
            "widths": widths,
            "classes": current,
            "missing_nets": missing,
            "applied": False,
            "_untrusted": ["pcb", "classes", "prompts"],
        }
        if not apply:
            return result
        if missing:
            raise AdapterError("E_NOT_FOUND", "nets not in the design", {"nets": missing})
        target = existing.get(class_name)
        created = False
        if target is None:
            try:
                target = classes.Add(class_name)
                created = True
            except Exception as exc:
                raise _com_error(exc, f"add_net_class {class_name}") from exc
        assigned: list[str] = []
        for name in nets:
            net = all_nets[name]
            try:
                if str(net.NetClass.Name) != class_name:
                    target.AssignNet(net)
                    assigned.append(name)
            except Exception as exc:
                raise _com_error(exc, f"assign_net {name}") from exc
        schemes = design.PhysicalRules.Schemes
        scheme = next((s for s in _collection(schemes) if str(s.Name) == MASTER_SCHEME), None)
        if scheme is None:
            scheme = schemes.Item(1)
        scheme_class = scheme.NetClasses.Item(class_name)
        changed = 0
        for layer in _collection(scheme_class.Layers):
            for constraint in _collection(layer.GetConstraints(CONSTRAINT_MASK_ALL)):
                kind = TRACE_WIDTH_TYPES.get(int(constraint.ConstraintType))
                if kind and kind in widths:
                    try:
                        constraint.Value = round(widths[kind] * TH_PER_MM, 3)
                        changed += 1
                    except Exception as exc:
                        raise _com_error(exc, f"set_{kind}_width {layer.Name}") from exc
        after = _class_widths(design, class_name)
    finally:
        try:
            design.UnLoad()
        except Exception:
            pass
    # Layout reads the constraint database only on SynchCES
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    synched = False
    try:
        integration = _com_member(doc, "ProjectIntegration")
        if bool(_value(integration, "IsSynchCESAllowed", default=True)):
            synched = bool(integration.SynchCES)
            _settle(5.0)
    except Exception as exc:
        raise _com_error(exc, "synch_ces") from exc
    layout_view: dict[str, Any] = {}
    for item in _items(_com_member(doc, "NetClasses")):
        name = str(_value(item, "Name", default=""))
        try:
            layout_view[name] = {
                "minimum": round(float(item.MinTraceWidth(1, MASTER_SCHEME, UNIT_MM)), 4),
                "typical": round(float(item.TypicalTraceWidth(1, MASTER_SCHEME, UNIT_MM)), 4),
                "nets": [str(_value(net, "Name", default="")) for net in _items(item.Nets)],
            }
        except Exception:
            layout_view[name] = {}
    result.update(
        {
            "created": created,
            "assigned": assigned,
            "constraints_changed": changed,
            "after": after,
            "synched": synched,
            "layout": layout_view,
            "prompts": prompts,
            "applied": True,
            "seen_by_layout": class_name in layout_view
            and abs(float(layout_view[class_name].get("typical", 0)) - widths["typical"]) < 0.01,
        }
    )
    return result


TH_TO_MM = 0.0254  # geometry comes out of the automation in thousandths of an inch
FAB_GFX_SILKSCREEN = 2  # FabricationLayerGfx/Text.Type: 1 assembly, 2 silkscreen


def _hole_size(hole: Any) -> float:
    """A hole's size in mm: its drill, or for a slot, whose drill size reads 0, its
    length. Layout gives a slot as the line between its two end centres drawn with the
    slot's width (0.6 x 1.7 mm: a 1.1 mm line 0.6 mm wide), so the length is the
    line's extent plus that width -- or plus an arc's radius on both sides, for an
    outline drawn with arcs. A circle of that diameter covers the slot, so a clearance
    measured against it errs on the safe side."""
    size = float(hole.GetDrillSize(UNIT_MM))
    if size > 0:
        return size
    longest = 0.0
    for geometry in _items(_com_member(hole, "Geometries")):
        record = _geometry_record(geometry) or {}
        if record.get("circle"):
            longest = max(longest, 2 * float(record["circle"][2]))
        points = record.get("path") or []
        if points:
            width = float(_value(geometry, "LineWidth", default=0) or 0) * TH_TO_MM
            grow = max(width, 2 * max(abs(p[2]) for p in points))
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            longest = max(longest, max(xs) - min(xs) + grow, max(ys) - min(ys) + grow)
    return round(longest, 4)


def _geometry_record(geometry: Any, **extra: Any) -> dict[str, Any] | None:
    """A geometry as plain data: `circle` or `path` rows of (x, y, r) in mm, `cutouts`."""
    try:
        if bool(geometry.IsCircle()):
            return {
                "circle": [
                    float(geometry.CircleX) * TH_TO_MM,
                    float(geometry.CircleY) * TH_TO_MM,
                    float(geometry.CircleR) * TH_TO_MM,
                ],
                **extra,
            }
        rows = geometry.PointsArray
    except Exception:
        return None
    if not rows or len(rows) < 3:
        return None
    xs, ys, rs = rows[0], rows[1], rows[2]
    path = [
        [float(x) * TH_TO_MM, float(y) * TH_TO_MM, float(r) * TH_TO_MM]
        for x, y, r in zip(xs, ys, rs, strict=False)
    ]
    record: dict[str, Any] = {"path": path, **extra}
    try:
        cutouts = geometry.Cutouts
        for cut in _items(cutouts):
            inner = _geometry_record(cut)
            if inner and inner.get("path"):
                record.setdefault("cutouts", []).append(inner["path"])
    except Exception:
        pass
    return record


SIDE_BOTTOM = 512  # EPcbSide: epcbSideTop 1, epcbSideBottom 512


def _observed_side(obj: Any) -> str | None:
    """Observed side only; the renderer's historical top fallback is not evidence."""
    name = str(_value(obj, "SideName", default="") or "").lower()
    if name in {"top", "bottom"}:
        return name
    value = _value(obj, "Side", default=None)
    return {1: "top", SIDE_BOTTOM: "bottom"}.get(value)


def _fabrication_owner_observed(obj: Any) -> bool:
    """A missing Component getter must not silently turn into document ownership."""
    try:
        owner = obj.Component
        return owner is None or bool(str(owner.RefDes))
    except Exception:
        return False


def _fabrication_placement_observed(obj: Any) -> bool:
    try:
        owner = obj.Component
        if owner is None:
            return True
        value = owner.Placed
        return type(value) in (bool, int) and value in (-1, 0, 1)
    except Exception:
        return False


def _side_name(obj: Any) -> str:
    """`top` or `bottom` for a silkscreen text or graphic: its `SideName`, else its
    `Side` (`_side` reads a part's)."""
    name = str(_value(obj, "SideName", default="") or "").lower()
    if name in ("top", "bottom"):
        return name
    return "bottom" if int(_value(obj, "Side", default=1) or 1) == SIDE_BOTTOM else "top"


LAYOUT_LAYER_STACK = 157  # Document.LayerStack([in] VARIANT_BOOL bIncludeInsulationLayer)
LAYOUT_NET_CLASSES = 24  # Document.NetClasses([in] BSTR sNetClassName)
LAYOUT_OBSTRUCTS = 41  # Document.Obstructs(eSelectType, eSelectOnly, nLayerOnly)
LAYOUT_PLACEMENT_OBSTRUCTS = 40  # Document.PlacementObstructs(eSelectOnly, nLayerOnly)
LAYER_PROPERTIES_THICKNESS = 1  # LayerProperties.Thickness([in] EPcbUnit)
NET_CLASS_WIDTHS = {"minimum": 7, "typical": 8, "expansion": 9}  # NetClass.<kind>TraceWidth
LAYER_KINDS = {0: "conductor", 1: "fabrication", 2: "user", 3: "insulation"}  # EPcbLayerType
LAYER_USAGES = {1: "signal", 2: "plane", 3: "split_mixed", 4: "flooded_signal", 5: "plating"}
OBSTRUCT_KINDS = {  # EPcbObstructType bits
    1: "plane",
    2: "plane_no_connect",
    4: "test_point",
    8: "trace",
    16: "trace_via",
    32: "via",
    64: "panel",
    128: "pad",
    256: "tuning_pattern",
}


def _board_stackup(doc: Any) -> list[dict[str, Any]]:
    """Every layer of the stackup from the top, conductors and insulation alike."""
    layers: list[dict[str, Any]] = []
    for layer in _items(_propget(doc, LAYOUT_LAYER_STACK, True)):
        kind = LAYER_KINDS.get(int(_value(layer, "Type", default=-1) or 0), "other")
        row: dict[str, Any] = {
            "board_layer": _value(layer, "BoardLayer", default=None),
            "kind": kind,
        }
        if kind == "conductor":
            row["layer"] = _value(layer, "ConductorLayer", default=None)
        properties = _value(layer, "LayerProperties", default=None)
        if properties is not None:
            try:
                thickness = float(_propget(properties, LAYER_PROPERTIES_THICKNESS, UNIT_MM))
            except AdapterError:
                thickness = None
            row.update(
                {
                    "name": str(_value(properties, "StackupLayerName", default="") or ""),
                    "description": str(_value(properties, "Description", default="") or ""),
                    "thickness_mm": round(thickness, 4) if thickness is not None else None,
                }
            )
            if kind == "conductor":
                row["usage"] = LAYER_USAGES.get(
                    int(_value(properties, "LayerUsage", default=0) or 0), "other"
                )
            elif kind == "insulation":
                row["dielectric_constant"] = _value(properties, "DielectricConstant", default=None)
        layers.append(row)
    return layers


def _net_class_read_failure(location: str, error: Exception) -> dict[str, Any]:
    failure = {"location": location, "type": type(error).__name__, "message": str(error)}
    if isinstance(error, AdapterError):
        failure.update(code=error.code, details=error.details)
    elif getattr(error, "hresult", None) is not None:
        failure["hresult"] = error.hresult
        failure["native"] = _com_error(error, location).details.get("native")
    return failure


def _net_class_items(collection: Any, location: str, limit: int) -> tuple[list, dict]:
    """Bounded, one-based enumeration; failed reads never imply an empty collection."""
    rows = []
    report = {
        "complete": False,
        "count_before": None,
        "count_after": None,
        "observed_count": 0,
        "attempted_count": 0,
        "index_base": 1,
        "limit": limit,
        "failures": [],
    }

    def count(stage):
        try:
            value = collection.Count
            if type(value) is not int or not 0 <= value <= limit:
                raise ValueError(f"Count must be an integer in [0, {limit}]; observed {value!r}")
            return value
        except Exception as error:
            report["failures"].append(_net_class_read_failure(f"{location}.Count.{stage}", error))
            return None

    report["count_before"] = count("before")
    if report["count_before"] is None:
        return rows, report
    for index in range(1, report["count_before"] + 1):
        report["attempted_count"] += 1
        try:
            item = collection.Item(index)
            if item is None:
                raise ValueError("Item returned no object")
            rows.append((index, item))
        except Exception as error:
            report["failures"].append(_net_class_read_failure(f"{location}.Item[{index}]", error))
    report["observed_count"] = len(rows)
    report["count_after"] = count("after")
    if report["count_after"] != report["count_before"]:
        report["failures"].append(
            {
                "location": f"{location}.Count",
                "type": "CountChanged",
                "message": "Collection count changed or became unreadable during enumeration",
            }
        )
    report["complete"] = not report["failures"]
    return rows, report


_NET_CLASS_MEMBER_BUDGET = 65536


def _board_net_classes(
    doc: Any, layer_count: int, *, observation: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Named widths with separately observed collection and membership completeness."""
    classes: list[dict[str, Any]] = []
    report = {
        "complete": False,
        "enumeration_complete": False,
        "membership_complete": False,
        "collection": None,
        "failures": [],
        "index_basis": "one-based native Count/Item observation indices; not persistent IDs",
        "member_budget": _NET_CLASS_MEMBER_BUDGET,
        "member_attempted_count": 0,
        "member_budget_remaining": _NET_CLASS_MEMBER_BUDGET,
    }
    if observation is not None:
        observation.update(report)
        report = observation
    try:
        collection = _propget(doc, LAYOUT_NET_CLASSES, "*")
    except Exception as error:
        report["failures"].append(_net_class_read_failure("NetClasses", error))
        return classes
    native_classes, enumeration = _net_class_items(collection, "NetClasses", 4096)
    report["collection"] = enumeration
    report["enumeration_complete"] = enumeration["complete"]
    report["failures"].extend(enumeration["failures"])
    member_budget = report["member_budget"]
    for index, net_class in native_classes:
        location = f"NetClasses.Item[{index}]"
        name = None
        class_failures = []
        try:
            name = net_class.Name
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Class Name is not a nonempty string")
        except Exception as error:
            class_failures.append(_net_class_read_failure(f"{location}.Name", error))
            name = None
        nets = []
        named_members = []
        members = None
        try:
            native_nets = net_class.Nets
            native_members, members = _net_class_items(
                native_nets, f"{location}.Nets", member_budget
            )
            member_budget -= members["attempted_count"]
            report["member_attempted_count"] += members["attempted_count"]
            report["member_budget_remaining"] = member_budget
            for member_index, net in native_members:
                try:
                    net_name = net.Name
                    if not isinstance(net_name, str) or not net_name:
                        raise ValueError("Net Name is not a nonempty string")
                    nets.append(net_name)
                    named_members.append({"observation_index": member_index, "name": net_name})
                except Exception as error:
                    members["failures"].append(
                        _net_class_read_failure(f"{location}.Nets.Item[{member_index}].Name", error)
                    )
            members["complete"] = not members["failures"]
            class_failures.extend(members["failures"])
        except Exception as error:
            class_failures.append(_net_class_read_failure(f"{location}.Nets", error))
        widths: dict[str, dict[str, float]] = {}
        for layer in range(1, layer_count + 1):
            row: dict[str, float] = {}
            for kind, dispid in NET_CLASS_WIDTHS.items():
                try:
                    row[kind] = round(
                        float(_propget(net_class, dispid, layer, MASTER_SCHEME, UNIT_MM)), 4
                    )
                except AdapterError:
                    continue
            widths[str(layer)] = row
        classes.append(
            {
                "name": name,
                "observation_index": index,
                "nets": sorted(nets),
                "membership_observation": {
                    "complete": not class_failures,
                    "collection": members,
                    "named_member_count": len(nets),
                    "named_members": named_members,
                    "failures": class_failures,
                },
                "widths": widths,
            }
        )
        report["failures"].extend(class_failures)
    report["membership_complete"] = enumeration["complete"] and all(
        row["membership_observation"]["complete"] for row in classes
    )
    report["complete"] = report["enumeration_complete"] and report["membership_complete"]
    return classes


def _obstruct_kinds(mask: int) -> list[str]:
    return [name for bit, name in OBSTRUCT_KINDS.items() if mask & bit] or [f"type {mask}"]


def _board_keepouts(doc: Any, *, strict: bool = False) -> list[dict[str, Any]]:
    """The board's obstructs (route, via, plane ... keepouts) and placement keepouts,
    each with its layer and outline; a part's own keepouts name the part."""
    keepouts: list[dict[str, Any]] = []
    for item in _items(_propget(doc, LAYOUT_OBSTRUCTS, -1, 0, 0), strict=strict):
        component = item.Component if strict else _value(item, "Component", default=None)
        record = _geometry_record(
            item.Geometry,
            kind="obstruct",
            obstructs=_obstruct_kinds(
                int(item.ObstructType)
                if strict
                else int(_value(item, "ObstructType", default=0) or 0)
            ),
            layer=item.Layer if strict else _value(item, "Layer", default=None),
            layer_name=str(item.LayerName)
            if strict
            else str(_value(item, "LayerName", default="") or ""),
            refdes=(
                str(component.RefDes) if strict else str(_value(component, "RefDes", default=""))
            )
            if component is not None
            else None,
        )
        if strict and not record:
            raise AdapterError("E_UNSUPPORTED", "A protected keepout cannot be represented")
        if record:
            keepouts.append(record)
    for item in _items(_propget(doc, LAYOUT_PLACEMENT_OBSTRUCTS, 0, 0), strict=strict):
        record = _geometry_record(
            item.Geometry,
            kind="placement",
            obstructs=["placement"],
            layer=item.Layer if strict else _value(item, "Layer", default=None),
            refdes=None,
        )
        if strict and not record:
            raise AdapterError(
                "E_UNSUPPORTED", "A protected placement keepout cannot be represented"
            )
        if record:
            keepouts.append(record)
    return keepouts


def _cell_search_evidence(doc: Any) -> dict[str, Any]:
    """Read the board's library names, separately from files and placed instances.

    MGCPCB 155 declares CellNames as an optional-argument property. Its returned
    tuple is data, not a callable. The hosted Cell Editor's ActiveDatabase can
    name the PCB itself; this is an observed object path, not a resolved .cel path.
    """
    result: dict[str, Any] = {
        "status": "not_observed",
        "names": None,
        "database": None,
        "diagnostics": [],
        "source": "Document.CellNames and CellEditor.ActiveDatabase.FullName native getters",
        "cell_geometry_verified": False,
        "board_instances_verified": False,
        "_untrusted": ["names", "database"],
    }
    for action, getter, key in (
        ("Document.CellNames", lambda: list(doc.CellNames), "names"),
        (
            "CellEditor.ActiveDatabase.FullName",
            lambda: str(doc.CellEditor.ActiveDatabase.FullName),
            "database",
        ),
    ):
        try:
            result[key] = getter()
        except Exception as exc:
            error = _com_error(exc, action)
            result["diagnostics"].append(
                {
                    "action": action,
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                }
            )
    if result["names"] is not None:
        result["status"] = "observed"
    return result


def _selected_clearances(doc: Any, nets: list[str], layers: list[int]) -> dict[str, Any]:
    """151.0 published type-directed clearance getter; explicit Master rules only.

    This does not discover location-dependent schemes or assert the active scheme.
    Bound the query: selected net pairs on selected layers, not a board-wide matrix.
    """
    names = sorted(set(nets))
    if not 2 <= len(names) <= 8 or not layers or len(layers) > 8:
        raise AdapterError("E_VALIDATION", "Clearance reads require 2..8 nets and 1..8 layers")
    try:
        count = int(doc.LayerCount)
        if any(type(n) is not int or not 1 <= n <= count for n in layers):
            raise AdapterError("E_VALIDATION", "A selected layer is outside this board")
        objects = {name: _find_net(doc, name) for name in names}
        clearance = doc.Clearance
        rows = []
        for left in names:
            for right in names:
                if left == right:
                    continue
                for layer in layers:
                    for kind, enum in (("trace", 1), ("pad", 2), ("smd_pad", 5)):
                        value = float(
                            clearance.GetNetClearanceRuleByType(
                                1,
                                objects[left],
                                layer,
                                enum,
                                objects[right],
                                layer,
                                "(Master)",
                                UNIT_MM,
                            )
                        )
                        if not math.isfinite(value) or value < 0:
                            raise ValueError("invalid clearance")
                        rows.append(
                            {
                                "net": left,
                                "other_net": right,
                                "layer": layer,
                                "object": "trace",
                                "other_object": kind,
                                "clearance_mm": value,
                            }
                        )
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError(
            "E_NATIVE_FAILURE",
            "Selected clearance rules could not be read",
            {"write_attempted": False, "cause": type(exc).__name__},
        ) from exc
    return {
        "unit": "mm",
        "scheme": "(Master)",
        "active_scheme_verified": False,
        "source": "Document.Clearance.GetNetClearanceRuleByType; EPcbUnitMM=4",
        "rules": rows,
        "not_checked": ["location-dependent schemes", "via/plane and general-object rules"],
    }


def _board_info(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """The board in numbers, with its stackup, net classes and keepouts."""
    if params.get("drc_hazards") and not params.get("geometry"):
        raise AdapterError(
            "E_VALIDATION",
            "Existing hazard reads require attached geometry observation",
            {"write_attempted": False},
        )
    if params.get("geometry"):
        return _board_observation(params, client)
    pcb_path = _layout_board_path(params)
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    layer_count = int(_value(doc, "LayerCount", default=0) or 0)
    components = list(_items(_com_member(doc, "Components")))
    placed = sum(1 for item in components if bool(_value(item, "Placed", default=False)))
    stackup = _board_stackup(doc)
    keepouts = _board_keepouts(doc)

    def count(name: str) -> int | None:
        collection = _com_member(doc, name)
        try:
            return int(collection.Count) if collection is not None else None
        except Exception:
            return None

    thickness = [row["thickness_mm"] for row in stackup if row.get("thickness_mm") is not None]
    net_class_observation: dict[str, Any] = {}
    net_classes = _board_net_classes(doc, layer_count, observation=net_class_observation)
    from .board_labels import inventory as label_inventory

    return {
        "pcb": str(pcb_path),
        "label_inventory": label_inventory(doc, pcb_path, sys.modules[__name__]),
        "component_count": len(components),
        "placed_count": placed,
        "net_count": count("Nets"),
        "track_count": count("Traces"),
        "via_count": count("Vias"),
        "layer_count": layer_count,
        "thickness_mm": round(sum(thickness), 4) if thickness else None,
        "stackup": stackup,
        "net_classes": net_classes,
        "net_class_observation": net_class_observation,
        "keepout_count": len(keepouts),
        "cell_search": _cell_search_evidence(doc),
        "display": {
            "schemes": [str(name) for name in (_com_member(doc, "DisplaySchemes") or ())],
            "discovery_only": True,
            "effect_verification": (
                "Use pcb view preview/confirm for bound scheme and viewport verification"
            ),
        },
        **(
            {"clearances": _selected_clearances(doc, params["nets"], params["layers"])}
            if params.get("nets") or params.get("layers")
            else {}
        ),
        "prompts": prompts,
        "_untrusted": [
            "pcb",
            "stackup",
            "net_classes",
            "net_class_observation",
            "label_inventory",
            "prompts",
        ],
    }


def _board_observation(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Observe an already-open target without any document lifecycle or write call."""
    from .board_copper import evidence_boundary
    from .cell_refresh import capture, digest
    from .copper_ties import compare as compare_ties
    from .copper_ties import observe as observe_ties
    from .native_identity import adapter_identity, application_identity
    from .native_observation_stage import full_capture, material

    drc_hazards_observation = None
    general_route_observation = None
    routepass_components = None
    connectivity_snapshot = None
    component_scope = "component_request" in params
    routepass_request, routepass_state, routepass_evidence, routepass_identity = (
        None,
        None,
        None,
        None,
    )
    if component_scope:
        from . import board_routepass

        request = params["component_request"]
        if (
            not isinstance(request, dict)
            or set(request) != {"net", "layer"}
            or "routepass_request" in params
            or params.get("native_general_state")
        ):
            raise AdapterError(
                "E_VALIDATION", "One unambiguous component material selector is required"
            )
        try:
            routepass_request = board_routepass.validate(request)
        except ValueError as error:
            raise AdapterError("E_VALIDATION", str(error), {"write_attempted": False}) from error
    elif "routepass_request" in params:
        from . import board_routepass

        try:
            routepass_request = board_routepass.validate(params["routepass_request"])
        except ValueError as error:
            raise AdapterError(
                "E_VALIDATION", str(error), {"write_attempted": False, "persistence": "unknown"}
            ) from error
    if params.get("native_general_state") and (
        routepass_request is None or routepass_request["net"] != "GND"
    ):
        raise AdapterError(
            "E_VALIDATION",
            "Read-only General Route discovery requires an explicit GND material selector",
            {"write_attempted": False, "persistence": "unknown"},
        )

    def read_details():
        details = {
            "write_attempted": False,
            "engineering_write_dispatched": False,
            "native_query_only": True,
            "persistence": "unknown",
        }
        if routepass_request is not None:
            details.update(
                native_routepass_request=routepass_request,
                native_routepass_state=routepass_state,
                native_routepass_evidence=routepass_evidence,
                **(
                    {"native_routepass_components": routepass_components}
                    if routepass_request.get("continuation") or component_scope
                    else {}
                ),
            )
        if component_scope:
            details["connectivity_components"] = connectivity_snapshot
        if params.get("drc_hazards"):
            details["drc_hazards_observation"] = drc_hazards_observation
        if params.get("native_general_state"):
            details["native_general_state"] = general_route_observation
        return details

    started = datetime.now(UTC).isoformat()
    board = _layout_board_path(params).resolve()
    # Public read queries run under XPEDITION_OBSERVE_ONLY. The adapter also
    # enforces attach-only behavior when invoked directly.
    app = _application(client, attach_only=True, quiet=False)
    try:
        doc = _licensed_document(app)
        identity = application_identity(app, "pcb")
        active_board = Path(str(doc.FullName)).resolve()
        if routepass_request is not None:
            from .board_labels import _identity

            routepass_identity = _identity(app, board)
    except AdapterError as error:
        error.details = {
            **(error.details or {}),
            "write_attempted": False,
            "persistence": "unknown",
        }
        raise
    except Exception as error:
        raise AdapterError(
            "E_NATIVE_FAILURE",
            "Cannot read the current board identity",
            {"write_attempted": False, "persistence": "unknown", "cause": type(error).__name__},
        ) from error
    if not params.get("pid") or identity.get("pid") != int(params["pid"]) or active_board != board:
        raise AdapterError(
            "E_CONFLICT",
            "Cannot identify the already-open requested board",
            {"write_attempted": False},
        )
    if params.get("mask_inventory_only"):
        if params.get("start") is not False or any(
            key in params
            for key in (
                "component_request",
                "routepass_request",
                "native_general_state",
                "drc_hazards",
            )
        ):
            raise AdapterError(
                "E_VALIDATION", "Mask inventory requires an isolated attached read", read_details()
            )

        def active_board_matches(snapshot):
            document = snapshot.get("document")
            if not isinstance(document, dict):
                return False
            path = document.get("path")
            try:
                return (
                    document.get("open") is True
                    and document.get("status") == "observed"
                    and isinstance(path, str)
                    and bool(path)
                    and Path(path).is_absolute()
                    and Path(path).resolve() == board
                )
            except (OSError, ValueError):
                return False

        if not active_board_matches(identity):
            raise AdapterError(
                "E_CONFLICT", "Cannot identify the current active board", read_details()
            )
        try:
            from .native_process import process_identity, same_identity

            process = process_identity("pcb", identity["pid"])
            engine = _propget(doc, 326)  # Document.MaskEngine, supported 151.0 GET
            collection = _propget(engine, 0)  # MaskEngine.Masks, supported GET
            before, after = collection.Count, collection.Count
            if type(before) is not int or before < 0 or type(after) is not int or after != before:
                raise ValueError("Mask collection count is invalid or changed during observation")
            current_identity = application_identity(app, "pcb")
            if (
                current_identity.get("pid") != identity["pid"]
                or not active_board_matches(current_identity)
                or Path(str(doc.FullName)).resolve() != board
                or not same_identity(process, process_identity("pcb", identity["pid"]))
            ):
                raise ValueError("Board or process generation changed during observation")
        except Exception as error:
            raise AdapterError(
                "E_VERIFY_INCOMPLETE",
                "Current native mask inventory could not be established",
                {**read_details(), "cause": type(error).__name__},
            ) from error
        return {
            "pcb": str(board),
            "observation": {
                "application": current_identity,
                "process_identity": process,
                "temporary_masks": {
                    "count": before,
                    "count_before": before,
                    "count_after": after,
                    "source": "Document.MaskEngine.Masks.Count; two read-only GETs",
                    "owner_identity_established": False,
                    "cleanup_attempted": False,
                    "scope": "current collection count only; interrupted work remains unresolved",
                },
                "engineering_write_dispatched": False,
                "persistence": "not_observed",
            },
            "_untrusted": ["pcb", "observation.application", "observation.process_identity"],
        }
    # The existing curve/text projection uses mil-valued Current-unit properties.
    # Validate that precondition rather than changing Document.CurrentUnit or
    # silently treating millimetres as mils. Position/drill getters are explicit MM.
    try:
        native_unit = int(doc.CurrentUnit)
    except Exception as error:
        raise AdapterError(
            "E_NATIVE_FAILURE",
            "Cannot observe Document.CurrentUnit",
            {"write_attempted": False, "persistence": "unknown"},
        ) from error
    if native_unit != 2:
        raise AdapterError(
            "E_UNSUPPORTED",
            "This geometry reader requires native CurrentUnit=mils",
            {
                "native_unit": native_unit,
                "supported_native_unit": 2,
                "write_attempted": False,
                "persistence": "unknown",
            },
        )
    stage_contexts = []
    current_stage = None
    try:

        def observation():
            nonlocal current_stage
            if routepass_request is not None:
                # The source is a fresh strict native capture made inside this
                # lexical stage. JSON request fields can never supply a stage.
                context = full_capture(
                    doc,
                    sys.modules[__name__],
                    identity_reader=lambda: _identity(app, board),
                )
                current_stage = context.__enter__()
                stage_contexts.append(context)
                geometry = material(
                    current_stage, doc, sys.modules[__name__], kind="complete_capture"
                )
            else:
                geometry = capture(doc, sys.modules[__name__], strict=True)
            if geometry["model"].get("planes"):
                from .board_copper import observe as plane_observe

                return plane_observe(doc, sys.modules[__name__], geometry)
            return {"geometry": geometry}

        before = _native_call("pcb.inspect.geometry.before", observation)
        ties_before = observe_ties(doc, sys.modules[__name__])
        if params.get("native_general_state"):
            from . import board_general

            general_route_observation = {
                "state": None,
                "state_sha256": None,
                "identity": routepass_identity,
                "components": None,
                "configuration": None,
                "fromto_inventory": None,
                "selection": None,
                "selection_after": None,
                "selection_consistency": None,
                "engineering_write_dispatched": False,
                "selection_write_dispatched": False,
                "persistence": "unknown",
            }
            # Fail fast on located supported configuration reads before constructing
            # the expensive independent native material-mask matrix.
            general_route_observation["configuration"] = board_general.observe_configuration(
                doc, before["geometry"]["model"]["layers"], app, sys.modules[__name__]
            )
        if routepass_request is not None:
            from . import board_routepass, native_pad_mask

            routepass_state = _native_call(
                "pcb.inspect.native_routepass_state",
                lambda: (
                    board_routepass.observe_components(
                        doc,
                        sys.modules[__name__],
                        routepass_request,
                        _capture_stage=current_stage,
                    )
                    if component_scope
                    else board_routepass.observe(
                        doc,
                        sys.modules[__name__],
                        routepass_request,
                        _capture_stage=current_stage,
                    )
                ),
            )
            material_request = (
                routepass_state["native_mask_proof"]["request"]
                if component_scope
                else routepass_request
            )
            routepass_evidence = (
                native_pad_mask.verify_component_evidence(routepass_state, material_request)
                if component_scope
                else native_pad_mask.verify_evidence(routepass_state, routepass_request)
            )
            if routepass_evidence.get("valid") is not True:
                raise AdapterError(
                    "E_NATIVE_FAILURE",
                    "Native RoutePass material read is incomplete; "
                    "no route or persistence dispatched",
                    read_details(),
                )
            if (
                routepass_request.get("continuation")
                or params.get("native_general_state")
                or component_scope
            ):
                routepass_components = (
                    native_pad_mask.observe_components(routepass_state, material_request)
                    if component_scope
                    else native_pad_mask.observe_material_components(
                        routepass_state, routepass_request
                    )
                )
                if routepass_components.get("observation_valid") is not True:
                    raise AdapterError(
                        "E_NATIVE_FAILURE",
                        "Native RoutePass component observation is incomplete; "
                        "whole-net completion is a separate result",
                        read_details(),
                    )
                if component_scope:
                    from .board_components import build_snapshot

                    connectivity_snapshot = build_snapshot(
                        routepass_state,
                        routepass_components,
                        board_id=str(board),
                        net_id=material_request["net_uid"],
                        layer=material_request["layer"],
                    )
                    if connectivity_snapshot.get("valid") is not True:
                        raise AdapterError(
                            "E_NATIVE_FAILURE",
                            "Component snapshot is incomplete; no connectivity conclusion or write",
                            read_details(),
                        )
            if params.get("native_general_state"):
                from . import board_general

                general_route_observation.update(
                    {
                        "state": routepass_state,
                        "state_sha256": digest(routepass_state),
                        "components": routepass_components,
                    }
                )
                if (
                    general_route_observation["configuration"]["layer_count"]
                    != routepass_state["geometry"]["model"]["layers"]
                ):
                    raise AdapterError(
                        "E_CONFLICT",
                        "Configuration layer count differs from complete material observation",
                        read_details(),
                    )
                general_route_observation["fromto_inventory"] = [
                    row[0]
                    for row in board_general.observe_fromtos(
                        doc, routepass_state, sys.modules[__name__]
                    )
                ]
                general_route_observation["configuration_after"] = (
                    board_general.observe_configuration(
                        doc,
                        routepass_state["geometry"]["model"]["layers"],
                        app,
                        sys.modules[__name__],
                    )
                )
                general_route_observation["fromto_inventory_after"] = [
                    row[0]
                    for row in board_general.observe_fromtos(
                        doc, routepass_state, sys.modules[__name__]
                    )
                ]
                if (
                    general_route_observation["configuration"]
                    != general_route_observation["configuration_after"]
                    or general_route_observation["fromto_inventory"]
                    != general_route_observation["fromto_inventory_after"]
                ):
                    raise AdapterError(
                        "E_CONFLICT",
                        "Native General Route configuration changed during discovery",
                        read_details(),
                    )
                # These finite selected-object samples both follow the complete
                # material read. They do not bracket or atomically freeze that
                # earlier read, and discovery does not validate routing eligibility.
                for selection_field in ("selection", "selection_after"):
                    try:
                        general_route_observation[selection_field] = (
                            board_general.observe_selection(
                                doc, routepass_state, sys.modules[__name__]
                            )
                        )
                    except Exception as error:
                        general_route_observation["selection_read_error"] = {
                            "sample": selection_field,
                            "cause": type(error).__name__,
                            "message": str(error),
                            "observation": getattr(error, "selection_observation", None),
                            "fromto_read_error": getattr(error, "fromto_observation", None),
                        }
                        raise
                    if general_route_observation[selection_field].get("complete") is not True:
                        raise AdapterError(
                            "E_NATIVE_FAILURE",
                            "Native selected-object discovery is incomplete",
                            read_details(),
                        )
                if (
                    general_route_observation["selection"]
                    != general_route_observation["selection_after"]
                ):
                    raise AdapterError(
                        "E_CONFLICT",
                        "Native selected objects changed between post-material samples",
                        read_details(),
                    )
                general_route_observation["selection_consistency"] = (
                    "two_matching_selected_object_reads_after_complete_material; "
                    "no_material_time_window_or_atomic_freeze"
                )
        if params.get("drc_hazards"):
            major = (identity.get("type_library") or {}).get("major")
            if major != 151:
                raise AdapterError(
                    "E_UNSUPPORTED",
                    "Existing hazard object evidence requires observed151.0",
                    read_details(),
                )
            existing = _native_call(
                "pcb.inspect.existing_batch_hazards",
                lambda: _hazards(doc, client, HAZARDS_BATCH),
            )
            # These two values come from the installed151.0 EPcbHazardType,
            # qualified by the actual application type-library observation.
            finite_types = {67: "Proximity", 74: "PartialNets"}
            for row in existing:
                if row.get("type") in finite_types:
                    row["kind"] = finite_types[row["type"]]
                    row["kind_source"] = "observed MGCPCB151.0 EPcbHazardType"
            drc_hazards_observation = {
                "method": "Document.GetHazards(epcbHazardTypeBatch)",
                "freshness": "existing_current_document",
                "engine_executed": False,
                "save_close_open_dispatched": False,
                "collection_complete": True,
                "count": len(existing),
                "hazards": existing,
                "type_library_major": major,
                "passes": False,
                "not_checked": [
                    "fresh engine execution",
                    "configuration equivalence",
                    "complete rule applicability",
                    "manufacturing",
                ],
            }
        # No frame survives into the independent after-capture, Save, Close or
        # Open. Text getters are independently repeated on the after side too.
        if stage_contexts:
            stage_contexts.pop().__exit__(None, None, None)
            current_stage = None
        after = _native_call("pcb.inspect.geometry.after", observation)
        if routepass_request is not None:
            from . import board_labels

            texts_after = _native_call(
                "pcb.inspect.texts.after",
                lambda: board_labels.observe(
                    doc, board, sys.modules[__name__], _capture_stage=current_stage
                ),
            )
            if texts_after["texts"] != routepass_state["texts"]:
                raise AdapterError(
                    "E_CONFLICT",
                    "Complete native text material changed during observation",
                    read_details(),
                )
        ties_after = observe_ties(doc, sys.modules[__name__])
        json.dumps([before, after, ties_before, ties_after], allow_nan=False)
        observed = application_identity(app, "pcb")
        active_board = Path(str(_licensed_document(app).FullName)).resolve()
        after_unit = int(doc.CurrentUnit)
        if routepass_request is not None:
            from .board_labels import _identity

            if _identity(app, board) != routepass_identity:
                raise AdapterError(
                    "E_CONFLICT", "Native RoutePass observation identity changed", read_details()
                )
    except Exception as error:
        if general_route_observation is not None and hasattr(error, "configuration_observation"):
            general_route_observation["configuration"] = error.configuration_observation
        if general_route_observation is not None and hasattr(error, "fromto_observation"):
            general_route_observation["fromto_read_error"] = error.fromto_observation
        if isinstance(error, AdapterError):
            error.details = {
                **(error.details or {}),
                **read_details(),
            }
            raise
        raise AdapterError(
            "E_NATIVE_FAILURE",
            "Live geometry could not be completely read",
            {
                **read_details(),
                "cause": type(error).__name__,
                **({"read_error": str(error)} if routepass_request is not None else {}),
            },
        ) from error
    finally:
        while stage_contexts:
            stage_contexts.pop().__exit__(None, None, None)
    if (
        active_board != board
        or observed.get("pid") != identity["pid"]
        or after_unit != native_unit
        or digest(before) != digest(after)
        or (routepass_state is not None and routepass_state["geometry"] != after["geometry"])
    ):
        raise AdapterError(
            "E_CONFLICT",
            "Live board identity or geometry changed during observation",
            read_details(),
        )
    return {
        "pcb": str(board),
        "observation": {
            "requested_project": str(Path(params["project"]).resolve()),
            "started_at": started,
            "observed_at": datetime.now(UTC).isoformat(),
            "adapter": adapter_identity(),
            "native_unit": "mils",
            "output_units": "mm",
            "application": identity,
            "geometry": after["geometry"],
            "geometry_sha256": digest(after["geometry"]),
            **({"plane_state": after["plane_state"]} if "plane_state" in after else {}),
            "tie_inventory": compare_ties(ties_before, ties_after),
            "consistency": "two_matching_live_reads",
            "observation_cost": {
                "complete_full_board_captures": 2,
                "independent_native_model_getters": 2,
                "shared_before_capture_consumers": (
                    ["routing_identity", "text_identity", "whole_board_pin_material"]
                    if routepass_request is not None
                    else []
                ),
                "independent_complete_text_inventory_reads": (
                    2 if routepass_request is not None else 0
                ),
                "cross_phase_capture_reuse": False,
                "atomic_freeze": False,
            },
            "persistence": "unknown",
            "engineering_write_dispatched": False,
            "copper_evidence_boundary": evidence_boundary(),
            **({"connectivity_components": connectivity_snapshot} if component_scope else {}),
            **(
                {"drc_hazards_observation": drc_hazards_observation}
                if params.get("drc_hazards")
                else {}
            ),
            **(
                {"native_general_state": general_route_observation}
                if params.get("native_general_state")
                else {}
            ),
            **(
                {
                    "native_routepass_request": routepass_request,
                    "native_routepass_state": routepass_state,
                    "native_routepass_state_sha256": digest(routepass_state),
                    "native_routepass_evidence": routepass_evidence,
                    **(
                        {"native_routepass_components": routepass_components}
                        if routepass_request.get("continuation") or component_scope
                        else {}
                    ),
                    "native_routepass_identity": routepass_identity,
                    "native_routepass_consistency": (
                        "one complete raw/material read bracketed by two matching "
                        "independent geometry reads; no atomic freeze"
                    ),
                }
                if routepass_request is not None
                else {}
            ),
            "limitations": [
                "Live geometry does not establish historical execution or saving.",
                "Only the returned native geometry and public attributes are observed.",
                "Full thermal/tie conductor geometry lacks independent proof; body/cutouts, "
                "point witnesses, native opens and images do not establish complete pad "
                "contact or connection-leg clearance. DRC execution is separate.",
            ],
        },
        "_untrusted": ["pcb", "observation"],
    }


def _slot_record(hole: Any) -> dict[str, Any] | None:
    """A slot as drawn: the line between its end centres and its width (mm). None for
    a round hole. `holes` keeps a circle that covers the slot, for clearances."""
    if float(hole.GetDrillSize(UNIT_MM)) > 0:
        return None
    for geometry in _items(_com_member(hole, "Geometries")):
        record = _geometry_record(geometry) or {}
        width = float(_value(geometry, "LineWidth", default=0) or 0) * TH_TO_MM
        if record.get("path") and width > 0:
            return {"path": record["path"], "width": round(width, 4)}
    return None


def _via_ownership(via: Any, net: str) -> dict[str, Any]:
    """The installed 151.0 EPcbShapeType uses 1 for a round hole."""
    uid = via.UniqueId
    stack = via.CurrentPadstack
    declared = list(_items(stack.Holes, strict=True))
    if uid is None or not net or not str(stack.Name) or len(declared) != 1:
        raise ValueError("Missing via identity or single-hole padstack")
    hole = declared[0]
    shape = int(hole.ShapeType)
    drill = float(hole.GetDrillSize(UNIT_MM))
    if shape != 1 or not math.isfinite(drill) or drill <= 0 or not bool(hole.Plated):
        raise ValueError("Only round plated vias are supported")
    return {
        "uid": str(uid),
        "padstack": str(stack.Name),
        "center_mm": [float(via.GetPositionX(UNIT_MM)), float(via.GetPositionY(UNIT_MM))],
        "drill_mm": drill,
        "shape_type": shape,
        "plated": True,
        "span": [int(via.StartLayer), int(via.EndLayer)],
    }


def _board_model(doc: Any, *, strict: bool = False) -> dict[str, Any]:
    """Everything the renderer draws, in mm: outline, pads, vias, traces, planes,
    holes (a slot also as `slots`), silkscreen graphics and texts."""

    layout = {
        "units": "mm" if _value(doc, "CurrentUnit", default=None) == 2 else None,
        "collections": {},
        "component_envelopes": [],
        "unreadable_objects": [],
        "trace_envelopes": [],
    }
    try:
        count = doc.LayerCount
        if type(count) is not int or count < 1:
            raise ValueError("LayerCount is not a positive integer")
        layout["layer_count"] = {
            "observed": True,
            "value": count,
            "source": "native_document.LayerCount",
            "renderer_value": count,
            "renderer_source": "native_document.LayerCount",
        }
    except Exception as error:
        layout["layer_count"] = {
            "observed": False,
            "value": None,
            "reason": "native_layer_count_unobserved_or_invalid",
            "observation_error": type(error).__name__,
            "renderer_value": 2,
            "renderer_source": "display_fallback_only",
        }
        if strict:
            raise AdapterError(
                "E_UNSUPPORTED", "Actual conductor LayerCount is unobserved"
            ) from error

    def items(value):
        return _items(value, strict=strict)

    def net_name(value):
        if strict:
            return str(value.Net.Name) if value.Net is not None else ""
        return str(_value(value.Net, "Name", default="")) if _value(value, "Net") else ""

    def collection(name):
        value = getattr(doc, name) if strict else _com_member(doc, name)
        if strict and callable(value) and not hasattr(value, "Count"):
            value = value()
        values = list(items(value))
        if strict and int(value.Count) != len(values):
            raise AdapterError("E_NATIVE_FAILURE", f"Incomplete {name} collection")
        key = {
            "Components": "component_envelopes",
            "FabricationLayerTexts": "texts",
            "FabricationLayerGfxs": "silk",
            "Traces": "traces",
        }.get(name)
        if key:
            try:
                complete = value is not None and int(value.Count) == len(values)
            except Exception:
                complete = False
            layout["collections"][key] = {
                "complete": complete,
                "enumeration_complete": complete,
                "observed_count": len(values),
            }
            if key in {"traces", "silk"}:
                layout["collections"][key].update(
                    geometry_complete=True,
                    geometry_observed_count=0,
                    geometry_unreadable_count=0,
                    geometry_scope=(
                        "conversion of eligible native objects; rule eligibility is separate"
                    ),
                )
        return values

    def geometry_unreadable(key, obj, index, kind, reason, rule, error=None):
        row = layout["collections"][key]
        row["complete"] = row["geometry_complete"] = False
        row["geometry_unreadable_count"] += 1
        uid = _value(obj, "UniqueId", default=None)
        layout["unreadable_objects"].append(
            {
                "kind": kind,
                "uid": str(uid) if uid is not None else None,
                "observation_index": index,
                "reason": reason,
                "affected_rules": [rule],
                **({"observation_error": type(error).__name__} if error else {}),
            }
        )

    def require_shape(record):
        if strict and not record:
            raise AdapterError("E_UNSUPPORTED", "A native geometry could not be represented")
        return record

    model: dict[str, Any] = {
        "layers": layout["layer_count"]["renderer_value"],
        "outline": [],
        "pads": [],
        "vias": [],
        "traces": [],
        "planes": [],
        "holes": [],
        "via_holes": [],
        "slots": [],
        "silk": [],
        "texts": [],
        "layout_observation": layout,
    }
    outline = doc.BoardOutline if strict else _value(doc, "BoardOutline", default=None)
    if outline is not None:
        record = _geometry_record(outline.Geometry, kind="outline")
        require_shape(record)
        if record:
            model["outline"].append(record)
        try:
            cutouts = outline.Geometry.Cutouts
            cutout_count = cutouts.Count
            if type(cutout_count) is not int or cutout_count < 0:
                raise ValueError("Cutout Count is not a nonnegative integer")
            layout["board_outline"] = {
                "inner_inventory_observed": True,
                "inner_contour_count": cutout_count,
                "source": "native_board_outline.Geometry.Cutouts.Count",
            }
        except Exception as error:
            layout["board_outline"] = {
                "inner_inventory_observed": False,
                "inner_contour_count": None,
                "reason": "board_inner_contour_inventory_unobserved",
                "observation_error": type(error).__name__,
            }
    layout["collections"]["outline"] = {"complete": bool(model["outline"])}
    pad_inventory_complete = True
    for observation_index, component in enumerate(collection("Components")):
        placed = component.Placed if strict else _value(component, "Placed", default=None)
        if type(placed) not in (bool, int) or placed not in (-1, 0, 1):
            layout["collections"]["component_envelopes"]["complete"] = False
            layout["unreadable_objects"].append(
                {
                    "kind": "component",
                    "observation_index": observation_index,
                    "reason": "component_placement_state_unobserved",
                }
            )
            pad_inventory_complete = False
            if strict:
                from .layout_reminders import report

                raise AdapterError(
                    "E_UNSUPPORTED", "Component placement state is unobserved", report(model)
                )
            continue
        if not placed:
            row = layout["collections"]["component_envelopes"]
            row["excluded_unplaced_count"] = row.get("excluded_unplaced_count", 0) + 1
            continue  # an unplaced part's pads stand at its cell's own coordinates
        refdes = str(component.RefDes) if strict else str(_value(component, "RefDes", default=""))
        envelope = {
            "uid": str(_value(component, "UniqueId"))
            if _value(component, "UniqueId") is not None
            else None,
            "refdes": refdes,
            "observed_side": _observed_side(component),
        }
        try:
            envelope.update(
                extents=list(_extrema_mm(component)),
                envelope_source="native_component_extrema_world_mm",
            )
        except Exception as error:
            envelope["observation_error"] = type(error).__name__
        layout["component_envelopes"].append(envelope)
        pins = list(items(component.Pins))
        try:
            if int(component.Pins.Count) != len(pins):
                pad_inventory_complete = False
        except Exception:
            pad_inventory_complete = False
        for pin in pins:
            net = net_name(pin)
            name = str(pin.Name) if strict else str(_value(pin, "Name", default=""))
            pads = list(items(pin.Pads))
            try:
                if int(pin.Pads.Count) != len(pads):
                    pad_inventory_complete = False
            except Exception:
                pad_inventory_complete = False
            for pad in pads:
                layer = int(pad.Layer) if strict else int(_value(pad, "Layer", default=0) or 0)
                geometries = list(items(pad.Geometries))
                try:
                    if int(pad.Geometries.Count) != len(geometries):
                        pad_inventory_complete = False
                except Exception:
                    pad_inventory_complete = False
                for geometry in geometries:
                    record = _geometry_record(
                        geometry, layer=layer, net=net, kind="pad", refdes=refdes, pin=name
                    )
                    if record:
                        model["pads"].append(record)
                    else:
                        pad_inventory_complete = False
                    require_shape(record)
            for hole in items(pin.Holes):
                try:
                    model["holes"].append(
                        [
                            float(pin.PositionX) * TH_TO_MM,
                            float(pin.PositionY) * TH_TO_MM,
                            _hole_size(hole),
                        ]
                    )
                    slot = _slot_record(hole)
                except Exception:
                    if strict:
                        raise
                    continue
                if slot:
                    model["slots"].append(slot)
    layout["collections"]["pads"] = {
        "complete": pad_inventory_complete
        and layout["collections"].get("component_envelopes", {}).get("complete") is True,
        "scope": (
            "placed component pin copper pads; via pads are outside the text/pad advisory subset"
        ),
    }
    for via in collection("Vias"):
        net = net_name(via)
        ownership = None
        try:
            ownership = _via_ownership(via, net)
        except Exception as exc:
            if strict:
                raise AdapterError(
                    "E_UNSUPPORTED", "Via UID, padstack or round drill could not be observed"
                ) from exc
        ring = None
        pad_geometry = []
        for pad in items(via.Pads):
            shapes = []
            for geometry in items(pad.Geometries):
                shape = _geometry_record(
                    geometry,
                    layer=int(pad.Layer) if strict else int(_value(pad, "Layer", default=1) or 1),
                )
                require_shape(shape)
                if shape:
                    shapes.append(shape)
                    if ring is None:
                        ring = shape
            if strict and not shapes:
                raise AdapterError("E_UNSUPPORTED", "Via pad contours could not be observed")
            pad_geometry.append({"layer": int(pad.Layer), "geometry": shapes})
        if ring:
            model["vias"].append(
                {
                    **ring,
                    **(ownership or {}),
                    "net": net,
                    "kind": "via",
                    "pad_geometry": pad_geometry,
                }
            )
        require_shape(ring)
        for hole in items(via.Holes):
            try:
                if strict and (int(hole.ShapeType) != 1 or not bool(hole.Plated)):
                    raise AdapterError("E_UNSUPPORTED", "Actual via hole is not round and plated")
                geometry = [
                    float(hole.GetPositionX(UNIT_MM)),
                    float(hole.GetPositionY(UNIT_MM)),
                    float(hole.GetDrillSize(UNIT_MM)),
                ]
                model["holes"].append(geometry)
                if ownership:
                    # The native via.Holes relationship establishes ownership;
                    # coordinates alone never associate arbitrary holes to vias.
                    model["via_holes"].append(
                        {"uid": ownership["uid"], "net": net, "geometry": geometry}
                    )
            except Exception:
                if strict:
                    raise
                continue
    for mounting in collection("MountingHoles"):
        for hole in items(mounting.Holes):
            try:
                model["holes"].append(
                    [
                        float(mounting.PositionX) * TH_TO_MM,
                        float(mounting.PositionY) * TH_TO_MM,
                        _hole_size(hole),
                    ]
                )
                slot = _slot_record(hole)
            except Exception:
                if strict:
                    raise
                continue
            if slot:
                model["slots"].append(slot)
    from .layout_reminders import trace_geometry_binding

    for observation_index, trace in enumerate(collection("Traces")):
        net = net_name(trace)
        try:
            layer = trace.Layer
            if (
                type(layer) is not int
                or layer < 1
                or (layout["layer_count"]["observed"] and layer > layout["layer_count"]["value"])
            ):
                raise ValueError("Trace Layer is not an observed conductor number")
        except Exception as error:
            layer = None
            if strict:
                raise AdapterError("E_UNSUPPORTED", "Actual trace Layer is unobserved") from error
        try:
            geometry = trace.Geometry
            native_width = _value(geometry, "LineWidth", default=None)
            record = _geometry_record(
                geometry,
                layer=layer,
                width=float(geometry.LineWidth) * TH_TO_MM
                if strict
                else float(native_width or 0) * TH_TO_MM,
                net=net,
                kind="trace",
            )
        except Exception as error:
            geometry_unreadable(
                "traces",
                trace,
                observation_index,
                "trace",
                "trace_geometry_read_failed",
                "text_trace",
                error,
            )
            if strict:
                raise AdapterError("E_UNSUPPORTED", "Trace geometry could not be read") from error
            continue
        if record:
            model_index = len(model["traces"])
            model["traces"].append(record)
            layout["collections"]["traces"]["geometry_observed_count"] += 1
            uid = _value(trace, "UniqueId", default=None)
            layout["trace_envelopes"].append(
                {
                    "model_index": model_index,
                    "observation_index": observation_index,
                    "uid": str(uid) if uid is not None else None,
                    "geometry_sha256": trace_geometry_binding(record),
                    "width_source": "native_geometry_mils"
                    if native_width is not None
                    else "unobserved",
                }
            )
        else:
            geometry_unreadable(
                "traces",
                trace,
                observation_index,
                "trace",
                "trace_geometry_unrepresentable",
                "text_trace",
            )
        require_shape(record)
    try:
        model["keepouts"] = _board_keepouts(doc, strict=True) if strict else _board_keepouts(doc)
    except AdapterError:
        if strict:
            raise
        model["keepouts"] = []
    try:
        status = _route_status(doc)
        model["unrouted"] = status["unrouted"]
        model["connectivity"] = {"complete": True, "nets": status["net_status"]}
    except Exception as error:
        if strict:
            raise
        model["unrouted"] = None
        model["connectivity"] = {
            "complete": False,
            "nets": None,
            "error": getattr(error, "code", "E_NATIVE_FAILURE"),
        }
    for plane in collection("PlaneShapes"):
        from .board_copper import read_geometry

        net = net_name(plane)
        layer = int(plane.Layer) if strict else int(_value(plane, "Layer", default=1) or 1)
        generated = [
            {
                **read_geometry(item.Geometry, sys.modules[__name__]),
                "layer": int(item.Layer),
                "net": str(item.Net.Name),
                "kind": "plane",
            }
            for item in items(
                plane.GeneratedPlanes
                if strict
                else _value(plane, "GeneratedPlanes", default=None) or ()
            )
        ]
        for record in generated:
            require_shape(record)
        generated = [item for item in generated if item]
        if not generated:
            record = _geometry_record(plane.Geometry, layer=layer, net=net, kind="plane_draft")
            require_shape(record)
            generated = [record] if record else []
        model["planes"].extend(generated)
    for observation_index, gfx in enumerate(collection("FabricationLayerGfxs")):
        try:
            fabrication_type = int(gfx.Type)
        except Exception as error:
            geometry_unreadable(
                "silk",
                gfx,
                observation_index,
                "component_graphic",
                "graphic_fabrication_type_unobserved",
                "text_component_graphic",
                error,
            )
            if strict:
                from .layout_reminders import report

                raise AdapterError(
                    "E_UNSUPPORTED", "Graphic fabrication layer is unobserved", report(model)
                ) from error
            continue
        if fabrication_type != FAB_GFX_SILKSCREEN:
            row = layout["collections"]["silk"]
            row["excluded_non_silkscreen_count"] = row.get("excluded_non_silkscreen_count", 0) + 1
            continue
        owner, on_board = _fabrication_owner(gfx, strict=strict)
        if not on_board:
            if not _fabrication_placement_observed(gfx):
                geometry_unreadable(
                    "silk",
                    gfx,
                    observation_index,
                    "component_graphic",
                    "graphic_owner_placement_state_unobserved",
                    "text_component_graphic",
                )
                if strict:
                    from .layout_reminders import report

                    raise AdapterError(
                        "E_UNSUPPORTED",
                        "Graphic owner placement state is unobserved",
                        report(model),
                    )
            else:
                row = layout["collections"]["silk"]
                row["excluded_unplaced_count"] = row.get("excluded_unplaced_count", 0) + 1
            continue
        try:
            geometry = gfx.Geometry
            filled = bool(_value(geometry, "Filled", default=False))
            native_width = _value(geometry, "LineWidth", default=None)
            width = float(native_width or 0) * TH_TO_MM
            record = _geometry_record(
                geometry,
                side=_side_name(gfx),
                observed_side=_observed_side(gfx),
                owner_observed=_fabrication_owner_observed(gfx),
                fabrication_layer="silkscreen",
                width=0.0 if filled else (width or 0.12),
                width_source="native_geometry_mils"
                if filled or native_width
                else "renderer_fallback",
                kind="silk",
                refdes=owner,
            )
        except Exception as error:
            geometry_unreadable(
                "silk",
                gfx,
                observation_index,
                "component_graphic",
                "graphic_geometry_read_failed",
                "text_component_graphic",
                error,
            )
            if strict:
                raise AdapterError("E_UNSUPPORTED", "Graphic geometry could not be read") from error
            continue
        if record:
            try:
                record["extents"] = list(_extrema_mm(gfx))
            except Exception:
                pass
        if record:
            model["silk"].append(record)
            layout["collections"]["silk"]["geometry_observed_count"] += 1
        else:
            geometry_unreadable(
                "silk",
                gfx,
                observation_index,
                "component_graphic",
                "graphic_geometry_unrepresentable",
                "text_component_graphic",
            )
        require_shape(record)
    for observation_index, text in enumerate(collection("FabricationLayerTexts")):
        try:
            fabrication_type = int(text.Type)
        except Exception as error:
            layout["collections"]["texts"]["complete"] = False
            layout["unreadable_objects"].append(
                {
                    "kind": "text",
                    "observation_index": observation_index,
                    "reason": "text_fabrication_type_unobserved",
                }
            )
            if strict:
                from .layout_reminders import report

                raise AdapterError(
                    "E_UNSUPPORTED", "Text fabrication layer is unobserved", report(model)
                ) from error
            continue
        if fabrication_type != FAB_GFX_SILKSCREEN:
            row = layout["collections"]["texts"]
            row["excluded_non_silkscreen_count"] = row.get("excluded_non_silkscreen_count", 0) + 1
            continue
        owner, on_board = _fabrication_owner(text, strict=strict)
        if not on_board:
            if not _fabrication_placement_observed(text):
                layout["unreadable_objects"].append(
                    {
                        "kind": "text",
                        "observation_index": observation_index,
                        "reason": "text_owner_placement_state_unobserved",
                    }
                )
                layout["collections"]["texts"]["complete"] = False
                if strict:
                    from .layout_reminders import report

                    raise AdapterError(
                        "E_UNSUPPORTED", "Text owner placement state is unobserved", report(model)
                    )
            else:
                row = layout["collections"]["texts"]
                row["excluded_unplaced_count"] = row.get("excluded_unplaced_count", 0) + 1
            continue  # an unplaced part's texts stand at its cell's own coordinates
        record = None
        try:
            record = _text_record(text, owner)
            if strict:
                record.update(
                    height=float(text.Format.Height) * TH_TO_MM,
                    orientation=float(text.Format.Orientation),
                    mirrored=bool(text.Format.Mirrored),
                )
                if "extents" not in record or not record.get("strokes"):
                    raise AdapterError(
                        "E_UNSUPPORTED", "Text extents/strokes could not be observed"
                    )
            model["texts"].append(record)
        except Exception as error:
            if strict:
                from .layout_reminders import report

                if record is None:
                    layout["unreadable_objects"].append(
                        {"kind": "text", "refdes": owner, "reason": "text_record_read_failed"}
                    )
                failed = (
                    {**model, "texts": [*model["texts"], record]} if record is not None else model
                )
                raise AdapterError(
                    "E_UNSUPPORTED",
                    "Complete native text geometry could not be observed",
                    {
                        "observation_error": type(error).__name__,
                        **report(failed),
                    },
                ) from error
            layout["unreadable_objects"].append(
                {"kind": "text", "refdes": owner, "reason": "text_record_read_failed"}
            )
            continue
    return model


TEXT_KINDS = {1: "refdes", 4: "part_number"}  # FabricationLayerText.TextType; else "text"


def _fabrication_owner(item: Any, *, strict: bool = False) -> tuple[str | None, bool]:
    """The designator of the part a silkscreen item belongs to (None for the board's
    own) and whether it is on the board. The document's collections hold every part's
    texts, placed or not, an unplaced part's at its cell's own coordinates."""
    component = item.Component if strict else _value(item, "Component", default=None)
    if component is None:
        return None, True
    return (
        str(component.RefDes) if strict else str(_value(component, "RefDes", default="")),
        bool(component.Placed) if strict else bool(_value(component, "Placed", default=False)),
    )


def stroke_rows(values: Any) -> tuple[float, list[list[list[float]]]]:
    """`FabricationLayerText.StrokeText(unit)` as the pen width and the strokes, each a
    list of [x, y]: the array holds the pen width, then (x, y, code) rows in which 0
    starts a stroke, 1 continues it and 2 ends it."""
    numbers = [float(value) for value in values or ()]
    if not numbers:
        return 0.0, []
    pen, rows = numbers[0], numbers[1:]
    if (
        not all(math.isfinite(value) for value in numbers)
        or pen <= 0
        or not rows
        or len(rows) % 3
        or any(rows[index] not in (0, 1, 2) for index in range(2, len(rows), 3))
    ):
        raise AdapterError("E_UNSUPPORTED", "Native text stroke array is malformed")
    strokes: list[list[list[float]]] = []
    current: list[list[float]] = []
    for index in range(0, len(rows) - 2, 3):
        code = int(round(rows[index + 2]))
        if code == 0 and current:
            strokes.append(current)
            current = []
        current.append([round(rows[index], 4), round(rows[index + 1], 4)])
        if code == 2:
            strokes.append(current)
            current = []
    if current:
        strokes.append(current)
    return pen, strokes


def _text_record(text: Any, owner: str | None) -> dict[str, Any]:
    """A silkscreen text as the renderer and the board rules take it: where it stands,
    its size and turn, whose it is, its box (`extents`) and the strokes Layout draws it
    with (vector fonts only; `pen` is their width)."""
    fmt = text.Format
    height = _value(fmt, "Height", default=None)
    mirrored = _value(fmt, "Mirrored", default=None)
    record: dict[str, Any] = {
        "x": float(text.PositionX) * TH_TO_MM,
        "y": float(text.PositionY) * TH_TO_MM,
        "text": str(text.TextString),
        "height": float(height or 40) * TH_TO_MM,
        "height_source": "native_format_mils" if height is not None else "renderer_fallback",
        "orientation": _value(fmt, "Orientation", default=None),
        "side": _side_name(text),
        "observed_side": _observed_side(text),
        "fabrication_layer": "silkscreen" if _value(text, "Type") == FAB_GFX_SILKSCREEN else None,
        "uid": str(_value(text, "UniqueId")) if _value(text, "UniqueId") is not None else None,
        "mirrored": bool(mirrored)
        if type(mirrored) in (bool, int) and mirrored in (-1, 0, 1)
        else None,
        "owner_observed": _fabrication_owner_observed(text),
        "refdes": owner,
        "kind": TEXT_KINDS.get(int(_value(text, "TextType", default=0) or 0), "text"),
    }
    try:
        x1, y1, x2, y2 = _extrema_mm(text)
        if x1 <= x2 and y1 <= y2:
            record["extents"] = [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]
            record["envelope_source"] = "native_extrema_world_mm"
    except Exception:
        pass
    try:
        pen, strokes = stroke_rows(text.StrokeText(UNIT_MM))
    except Exception:
        pen, strokes = 0.0, []
    if strokes:
        record["strokes"] = strokes
        record["stroke_source"] = "native_vector_strokes_world_mm"
    if not pen:
        pen = float(_value(fmt, "PenWidth", default=0) or 0) * TH_TO_MM
    record["pen"] = round(pen, 4)
    return record


def _render_board(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """A PNG of the board drawn from its geometry (see `board_render`), so the
    picture does not depend on the desktop being unlocked or the window on screen.
    With `refdes`, only the part of the board around those parts, drawn larger."""
    from . import board_render, layout_reminders

    pcb_path = _layout_board_path(params)
    output = params.get("output")
    if not output:
        raise AdapterError("E_USAGE", "render_board requires an output path")
    output_path = Path(str(output)).expanduser().resolve()
    if output_path.suffix.lower() != ".png":
        raise AdapterError(
            "E_VALIDATION", "render output must end in .png", {"path": str(output_path)}
        )
    if output_path.exists() and not bool(params.get("replace", False)):
        raise AdapterError("E_CONFLICT", "output file already exists", {"path": str(output_path)})
    side = "bottom" if str(params.get("side") or "top").lower() == "bottom" else "top"
    scale: float | None = None
    if params.get("scale") is not None:
        try:
            scale = float(params["scale"])
        except (TypeError, ValueError) as exc:
            raise AdapterError("E_USAGE", "scale is pixels per millimetre") from exc
        if not 1.0 <= scale <= 200.0:
            raise AdapterError("E_VALIDATION", "scale must be between 1 and 200 px/mm")
    refdes = [str(name).strip() for name in params.get("refdes") or [] if str(name).strip()]
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    window = _parts_window(doc, refdes) if refdes else None
    if scale is None:
        scale = window_scale(window)
    model = _board_geometry({"project": params["project"], "start": False}, client)["model"]
    picture = board_render.render(
        board_render.model_from_dict(model), output_path, scale=scale, side=side, window=window
    )
    from PIL import Image, PngImagePlugin

    from . import board_artifacts

    binding = board_artifacts.provenance(params, pcb_path, model, "render")
    binding["view"] = {"side": side, "scale": scale, "refdes": refdes, "window": window}
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("xpedition", json.dumps(binding, ensure_ascii=False))
    with Image.open(output_path) as image:
        image.load()
        image.save(output_path, format="PNG", pnginfo=metadata)
    model_path = params.get("model_output")
    if model_path:
        Path(str(model_path)).expanduser().resolve().write_text(
            json.dumps(model, ensure_ascii=False), encoding="utf-8"
        )
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "side": side,
        "scale": scale,
        "picture": picture,
        "artifact": board_artifacts.finish(output_path, binding),
        "layers": model["layers"],
        "prompts": prompts,
        "_untrusted": ["pcb", "picture", "prompts", "layout_reminders", "layout_reminder_coverage"],
        **layout_reminders.report(model),
    }
    if window is not None:
        result.update({"refdes": refdes, "window": list(window)})
    return result


WINDOW_MARGIN_MM = 1.5  # around the parts a close look is taken at
WINDOW_PIXELS = 1600  # the longer side of a close-up render, when no scale is given
DEFAULT_RENDER_SCALE = 20.0  # px/mm for the whole board


def window_around(boxes: list[tuple[float, float, float, float]]) -> tuple[float, ...]:
    """The rectangle (mm) holding every box, with room around it: the larger of
    `WINDOW_MARGIN_MM` and a sixth of its longer side."""
    x1, y1 = min(box[0] for box in boxes), min(box[1] for box in boxes)
    x2, y2 = max(box[2] for box in boxes), max(box[3] for box in boxes)
    margin = max(WINDOW_MARGIN_MM, max(x2 - x1, y2 - y1) / 6)
    return (
        round(x1 - margin, 3),
        round(y1 - margin, 3),
        round(x2 + margin, 3),
        round(y2 + margin, 3),
    )


def window_scale(window: tuple[float, ...] | None) -> float:
    """The render scale when none is given: 20 px/mm for the whole board; for a window,
    what makes its longer side `WINDOW_PIXELS` wide, within 20 to 200 px/mm."""
    if window is None:
        return DEFAULT_RENDER_SCALE
    longest = max(window[2] - window[0], window[3] - window[1], 0.1)
    return round(min(200.0, max(DEFAULT_RENDER_SCALE, WINDOW_PIXELS / longest)), 1)


def _parts_window(doc: Any, refdes: list[str]) -> tuple[float, ...]:
    """The rectangle (mm) around the named parts and their silkscreen texts, with
    room around it: what `pcb view` zooms to and `pcb render` draws."""
    boxes: list[tuple[float, float, float, float]] = []
    for name in refdes:
        component = _find_component(doc, name)
        if not bool(_value(component, "Placed", default=False)):
            raise AdapterError(
                "E_VALIDATION",
                f"{name} is not placed; there is nothing to look at",
                {"refdes": name},
            )
        try:
            boxes.append(_extrema_mm(component))
        except Exception as exc:
            raise _com_error(exc, "component_extents") from exc
        for text in _items(_com_member(component, "FabricationLayerTexts")):
            if int(_value(text, "Type", default=0) or 0) != FAB_GFX_SILKSCREEN:
                continue
            try:
                box = _extrema_mm(text)
            except Exception:
                continue
            if box[0] <= box[2] and box[1] <= box[3]:
                boxes.append(box)
    return window_around(boxes)


# -- hand routing: traces, vias, unroute, move ------------------------------------------

VIA_PADSTACK_TYPE = 2  # Padstack.Type of a via padstack (1 is a pin padstack)
DEFAULT_TRACE_WIDTH_MM = 0.254  # the stock templates' minimum
ENDPOINT_TOLERANCE_MM = 1.0  # a trace end farther than this from any pin is "in the air"


def parse_points(value: Any, minimum: int = 2) -> list[tuple[float, float]]:
    """`"x,y x,y …"` (separated by blanks or semicolons), or a list of pairs → (x, y) mm."""
    if value is None or value == "":
        raise AdapterError("E_USAGE", "points are required (x,y pairs in millimetres)")
    if isinstance(value, str):
        raw: list[Any] = [token.split(",") for token in re.split(r"[;\s]+", value.strip()) if token]
    else:
        raw = list(value)
    points: list[tuple[float, float]] = []
    for item in raw:
        try:
            x, y = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError, KeyError) as exc:
            raise AdapterError(
                "E_USAGE", "each point is x,y in millimetres", {"point": str(item)}
            ) from exc
        points.append((round(x, 4), round(y, 4)))
    if len(points) < minimum:
        raise AdapterError(
            "E_VALIDATION", f"at least {minimum} point(s) needed", {"points": len(points)}
        )
    return points


def plan_items(data: Any) -> list[dict[str, Any]]:
    """The routing items of a plan file: `items` (kind trace/via, in order), or `traces`
    and `vias` lists; every item normalised with parsed points."""
    if not isinstance(data, dict):
        raise AdapterError("E_USAGE", "the plan is a JSON object")
    raw: list[dict[str, Any]] = []
    for item in data.get("items") or []:
        if isinstance(item, dict):
            raw.append(dict(item))
    for item in data.get("traces") or []:
        if isinstance(item, dict):
            raw.append({"kind": "trace", **item})
    for item in data.get("vias") or []:
        if isinstance(item, dict):
            raw.append({"kind": "via", **item})
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        kind = str(item.get("kind") or ("via" if "at" in item else "trace")).lower()
        net = str(item.get("net") or "").strip()
        if not net:
            raise AdapterError("E_USAGE", "every item names its net", {"item": index})
        if kind == "trace":
            try:
                layer = 1 if item.get("layer") is None else int(item["layer"])
                width = (
                    DEFAULT_TRACE_WIDTH_MM if item.get("width") is None else float(item["width"])
                )
            except (TypeError, ValueError) as exc:
                raise AdapterError("E_USAGE", "layer is an integer, width millimetres") from exc
            if width <= 0:
                raise AdapterError("E_VALIDATION", "width must be positive", {"item": index})
            items.append(
                {
                    "kind": "trace",
                    "net": net,
                    "layer": layer,
                    "width": width,
                    "points": parse_points(item.get("points"), 2),
                }
            )
        elif kind == "via":
            at = item.get("at")
            if at is None and "x" in item and "y" in item:
                at = [[item["x"], item["y"]]]
            elif isinstance(at, (list, tuple)) and at and not isinstance(at[0], (list, tuple)):
                at = [at]
            point = parse_points(at, 1)[0]
            items.append(
                {
                    "kind": "via",
                    "net": net,
                    "at": point,
                    "padstack": str(item.get("padstack") or "") or None,
                }
            )
        else:
            raise AdapterError("E_USAGE", "item kind is trace or via", {"kind": kind})
    if not items:
        raise AdapterError("E_VALIDATION", "the plan has no traces or vias")
    return items


def _points_rows(points: list[tuple[float, float]]) -> tuple[tuple[float, ...], ...]:
    return (
        tuple(float(p[0]) for p in points),
        tuple(float(p[1]) for p in points),
        tuple(0.0 for _ in points),
    )


def _find_net(doc: Any, name: str) -> Any:
    for net in _items(_com_member(doc, "Nets")):
        if str(_value(net, "Name", default="")) == name:
            return net
    raise AdapterError("E_NOT_FOUND", "the board has no such net", {"net": name})


def _via_padstack(
    doc: Any, name: str | None, at: tuple[float, float] = (0.0, 0.0), layers: int = 0
) -> Any:
    """The via padstack to place: by name (from the design, else pulled from the central
    library), else the one Layout's via assignments give for a through via at `at`
    (`Document.DefaultViaPadstack`), else the one the board's vias use, else the first
    via padstack of the design. A board whose vias were all deleted lists no via
    padstack any more; the default-via call still answers."""
    stacks = _com_member(doc, "Padstacks")
    first_via = None
    for item in _items(stacks):
        item_name = str(_value(item, "Name", default=""))
        if name and item_name == name:
            return item
        if first_via is None and int(_value(item, "Type", default=0) or 0) == VIA_PADSTACK_TYPE:
            first_via = item
    span = layers or int(_value(doc, "LayerCount", default=2) or 2)
    if name:
        try:
            pulled = doc.PutPadstack(1, span, name, False, True)
            if pulled is not None:
                return pulled
        except Exception as exc:
            raise AdapterError(
                "E_NOT_FOUND",
                "the board and the central library have no such padstack",
                {"padstack": name, "detail": str(exc)[-120:]},
            ) from exc
    vias = _com_member(doc, "Vias")
    try:
        if vias is not None and int(vias.Count) > 0:
            return vias.Item(1).CurrentPadstack
    except Exception:
        pass
    if first_via is not None:
        return first_via
    # a board whose vias were all deleted lists no via padstack: the central library's
    # first via padstack (the stock "026VIA" on this installation) is pulled in
    try:
        names = list(doc.GetPadstackNames(VIA_PADSTACK_TYPE, -1, "*", True) or ())
    except Exception:
        names = []
    for candidate in names:
        try:
            pulled = doc.PutPadstack(1, span, str(candidate), False, True)
        except Exception:
            continue
        if pulled is not None:
            return pulled
    raise AdapterError("E_NOT_FOUND", "the board and the central library have no via padstack")


def _pin_index(doc: Any) -> list[dict[str, Any]]:
    """Every pin on the board: refdes, pin, net, x, y (mm)."""
    pins: list[dict[str, Any]] = []
    for component in _items(_com_member(doc, "Components")):
        refdes = str(_value(component, "RefDes", default=""))
        if not bool(_value(component, "Placed", default=False)):
            continue
        for pin in _items(component.Pins):
            try:
                net = _value(pin, "Net", default=None)
                pins.append(
                    {
                        "refdes": refdes,
                        "pin": str(_value(pin, "Name", default="")),
                        "net": str(_value(net, "Name", default="")) if net else "",
                        "x": round(float(pin.GetPositionX(UNIT_MM)), 4),
                        "y": round(float(pin.GetPositionY(UNIT_MM)), 4),
                    }
                )
            except Exception:
                continue
    return pins


def _nearest_pin(pins: list[dict[str, Any]], point: tuple[float, float]) -> dict[str, Any] | None:
    best = None
    best_d = None
    for pin in pins:
        d = math.hypot(pin["x"] - point[0], pin["y"] - point[1])
        if best_d is None or d < best_d:
            best, best_d = pin, d
    if best is None:
        return None
    return {**best, "distance": round(best_d or 0.0, 3)}


def _net_opens(doc: Any, names: set[str]) -> dict[str, int]:
    opens = {
        row["net"]: row["opens"] for row in _route_status(doc)["net_status"] if row["net"] in names
    }
    if set(opens) != names:
        raise AdapterError("E_VERIFY_INCOMPLETE", "Requested net identity was not observed")
    return opens


def _pace(params: dict[str, Any]) -> float:
    """Seconds to wait between placed items (`pace`), so a person at the screen sees
    the work grow item by item; 0 places everything as fast as Layout takes it."""
    try:
        pace = float(params.get("pace") or 0.0)
    except (TypeError, ValueError) as exc:
        raise AdapterError("E_USAGE", "pace is seconds") from exc
    if pace < 0 or pace > 10:
        raise AdapterError("E_VALIDATION", "pace is 0 to 10 seconds", {"pace": pace})
    return pace


def _hand_route(params: dict[str, Any], client: Any) -> dict[str, Any]:
    if params.get("mode") == "component-connect":
        from .board_component_connect import execute

        return execute(params, client, sys.modules[__name__])
    from .board_trace import execute

    return execute(params, client, sys.modules[__name__])


def _touches(item: Any, at: tuple[float, float], layer: int | None, tolerance: float) -> bool:
    """Whether a trace (a vertex within `tolerance` mm of `at`, on `layer`) or a via
    (its centre) is the one the caller points at."""
    try:
        item_layer = _value(item, "Layer", default=None)
        if layer is not None and item_layer is not None and int(item_layer) != layer:
            return False
        geometry = _value(item, "Geometry", default=None)
        if geometry is not None:
            rows = geometry.PointsArray
            for x, y in zip(rows[0], rows[1], strict=False):
                if (
                    math.hypot(float(x) * TH_TO_MM - at[0], float(y) * TH_TO_MM - at[1])
                    <= tolerance
                ):
                    return True
            return False
        x = float(_value(item, "PositionX", default=0.0)) * TH_TO_MM
        y = float(_value(item, "PositionY", default=0.0)) * TH_TO_MM
        return math.hypot(x - at[0], y - at[1]) <= tolerance
    except Exception:
        return False


def _unroute_nets(params: dict[str, Any], client: Any) -> dict[str, Any]:
    from .board_unroute import execute

    return execute(params, client, sys.modules[__name__])


def _move_component(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Move one placed part to a position (its cell origin, mm) and rotation, with
    Layout's placement DRC on, so a part moved onto another one is refused."""
    pcb_path = _layout_board_path(params)
    refdes = str(params.get("refdes") or "").strip()
    if not refdes:
        raise AdapterError("E_USAGE", "move_component needs a reference designator")
    try:
        x = float(params["x"])
        y = float(params["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AdapterError("E_USAGE", "x and y are millimetres") from exc
    rotation = params.get("rotation")
    if rotation is not None:
        try:
            rotation = float(rotation) % 360
        except (TypeError, ValueError) as exc:
            raise AdapterError("E_USAGE", "rotation is degrees") from exc
    apply = bool(params.get("apply", False))
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    component = _find_component(doc, refdes)
    was_placed = bool(_value(component, "Placed", default=False))
    before = {
        "x": round(float(component.GetPositionX(UNIT_MM)), 3) if was_placed else None,
        "y": round(float(component.GetPositionY(UNIT_MM)), 3) if was_placed else None,
        "rotation": float(_value(component, "Orientation", default=0.0) or 0.0)
        if was_placed
        else None,
        "placed": was_placed,
    }
    target_rotation = rotation if rotation is not None else (before["rotation"] or 0.0)
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "refdes": refdes,
        "before": before,
        "target": {"x": x, "y": y, "rotation": target_rotation},
        "routing": _routing_counts(doc),
        "applied": False,
        "saved": False,
        "prompts": prompts,
        "_untrusted": ["pcb", "prompts"],
    }
    if not apply:
        return result
    previous_drc: Any = None
    try:
        previous_drc = bool(doc.RespectComponentPlacementDRC)
        doc.RespectComponentPlacementDRC = True
    except Exception:
        previous_drc = None
    try:
        if was_placed:
            try:
                component.UnPlace()
            except Exception as exc:
                raise _com_error(exc, f"unplace_component {refdes}") from exc
        try:
            _native_call(
                "placement.Place",
                lambda: component.Place(x, y, target_rotation, True, 0, UNIT_MM, 0),
            )
        except Exception as exc:
            text = str(exc)
            raise AdapterError(
                "E_VALIDATION",
                "Layout refused the position"
                + (" (placement DRC: it touches another part)" if "DRC" in text else ""),
                {
                    "refdes": refdes,
                    "detail": text[-160:],
                    "before": before,
                    "partial": True,
                    "rollback_attempted": False,
                },
            ) from exc
    finally:
        if previous_drc is not None:
            try:
                doc.RespectComponentPlacementDRC = previous_drc
            except Exception:
                pass
    try:
        _native_call("placement.Save", lambda: doc.Save())
        saved = True
    except Exception as exc:
        raise _com_error(exc, "save_after_move") from exc
    if params.get("verify"):
        doc = _reopen_board(app, pcb_path)
        component = _find_component(doc, refdes)
    result.update(
        {
            "after": {
                "x": round(float(component.GetPositionX(UNIT_MM)), 3),
                "y": round(float(component.GetPositionY(UNIT_MM)), 3),
                "rotation": float(_value(component, "Orientation", default=0.0) or 0.0),
                "placed": bool(_value(component, "Placed", default=False)),
            },
            "applied": True,
            "saved": saved,
            "persistence_verified": bool(params.get("verify")),
        }
    )
    return result


def filter_model(model: dict[str, Any], refdes: list[str], nets: list[str]) -> dict[str, Any]:
    """The part of a board model named by reference designators and nets.

    Kept whole: the outline, the holes and the keepouts, which every question about a
    part or a net is asked against. The parts kept keep their silkscreen and texts
    (their designators); the board's own are dropped. A filtered model says so in
    `filter`; measuring or planning against it would miss the rest.
    """
    if not refdes and not nets:
        return model
    parts, names = set(refdes), set(nets)
    components = [
        item
        for item in model.get("components", [])
        if item.get("refdes") in parts
        or any(pin.get("net") in names for pin in item.get("pins") or [])
    ]
    kept = {item.get("refdes") for item in components}
    filtered = {
        **model,
        "components": components,
        "pads": [
            pad
            for pad in model.get("pads", [])
            if pad.get("refdes") in kept
            and (not names or pad.get("net") in names or pad.get("refdes") in parts)
        ],
        "traces": [item for item in model.get("traces", []) if item.get("net") in names],
        "vias": [item for item in model.get("vias", []) if item.get("net") in names],
        "planes": [item for item in model.get("planes", []) if item.get("net") in names],
        "unrouted": [
            item
            for item in model.get("unrouted", [])
            if item.get("net") in names
            or any(
                item.get("net") == pin.get("net")
                for component in components
                for pin in component.get("pins") or []
            )
        ],
        "silk": [item for item in model.get("silk", []) if item.get("refdes") in kept],
        "texts": [item for item in model.get("texts", []) if item.get("refdes") in kept],
        "filter": {"refdes": sorted(parts), "nets": sorted(names)},
    }
    if "physical_instances" in model:
        filtered["physical_instances"] = [
            p for p in model["physical_instances"] if p["refdes"] in kept
        ]
    return filtered


def _board_geometry(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """The board's geometry as data (what `pcb render` draws, plus the components with
    their pins), written to `output` as JSON or returned inline."""
    pcb_path = _layout_board_path(params)
    output = params.get("output")
    output_path = Path(str(output)).expanduser().resolve() if output else None
    observing = (
        params.get("observe_only") is True or os.environ.get("XPEDITION_OBSERVE_ONLY") == "1"
    )
    if observing and output_path is not None:
        raise AdapterError(
            "E_VALIDATION",
            "Read-only geometry observation cannot publish an output file",
            {"write_attempted": False},
        )
    if output_path is not None:
        if output_path.suffix.lower() != ".json":
            raise AdapterError("E_VALIDATION", "geometry output must end in .json")
        if output_path.exists() and not bool(params.get("replace", False)):
            raise AdapterError(
                "E_CONFLICT", "output file already exists", {"path": str(output_path)}
            )
    app = _application(client, attach_only=observing or not bool(params.get("start", True)))
    if observing:
        if params.get("pid") is not None:
            observed_pid = application_identity(app, "pcb").get("pid")
            if (
                type(params["pid"]) is not int
                or params["pid"] <= 0
                or observed_pid != params["pid"]
            ):
                raise AdapterError(
                    "E_CONFLICT",
                    "Read-only geometry requires the bound existing process",
                    {"write_attempted": False},
                )
        _open_requested_document(app, {**params, "observe_only": True})
        # The observed ActiveDocument proxy is not necessarily validated for
        # Layout automation. Use the same licensing handshake as board_info,
        # then bind its returned document to the requested board again.
        doc = _licensed_document(app)
        try:
            actual_path = doc.FullName
            matches = (
                isinstance(actual_path, str)
                and bool(actual_path)
                and Path(actual_path).resolve() == pcb_path.resolve()
            )
        except Exception:
            matches = False
        if not matches:
            raise AdapterError(
                "E_CONFLICT",
                "The validated active board does not match the requested board",
                {"write_attempted": False},
            )
        prompts = []
    else:
        _doc, prompts = _open_layout_document(app, pcb_path)
        doc = _licensed_document(app)
    if _value(doc, "CurrentUnit", default=None) != 2:
        raise AdapterError(
            "E_UNSUPPORTED",
            "Snapshot/render geometry requires CurrentUnit=mils; no unit was changed",
            {"current_unit": _value(doc, "CurrentUnit", default=None), "unit_not_changed": True},
        )
    from . import board_artifacts, cell_refresh, layout_reminders
    from .native_observation_stage import material, raw_model

    # These fields become one returned/rendered model. Read that complete raw
    # model once in this stage; SDK before/after observations remain fresh.
    with raw_model(
        doc,
        sys.modules[__name__],
        identity_reader=lambda: {
            "application": application_identity(app, "pcb"),
            "board": str(Path(str(_licensed_document(app).FullName)).resolve()),
        },
    ) as stage:
        model = material(stage, doc, sys.modules[__name__], kind="raw_board_model")
        physical = cell_refresh.capture(doc, sys.modules[__name__], strict=True, _model_stage=stage)
    components: list[dict[str, Any]] = []
    for component in _items(_com_member(doc, "Components")):
        refdes = str(_value(component, "RefDes", default=""))
        placed = bool(_value(component, "Placed", default=False))
        record: dict[str, Any] = {
            "refdes": refdes,
            "cell": str(_value(component, "CellName", default="")),
            "placed": placed,
        }
        if placed:
            try:
                min_x, min_y, max_x, max_y = _extrema_mm(component)
                record.update(
                    {
                        "x": round(float(component.GetPositionX(UNIT_MM)), 3),
                        "y": round(float(component.GetPositionY(UNIT_MM)), 3),
                        "rotation": float(_value(component, "Orientation", default=0.0) or 0.0),
                        "extents": [
                            round(min_x, 3),
                            round(min_y, 3),
                            round(max_x, 3),
                            round(max_y, 3),
                        ],
                    }
                )
            except Exception:
                pass
            record["side"] = _side(component)
            record["pins"] = [
                {k: v for k, v in pin.items() if k != "refdes"}
                for pin in _pin_index_of(component, refdes)
            ]
        components.append(record)
    model["components"] = components
    model["physical_instances"] = physical["components"]
    model["stackup"] = physical["stackup"]
    model = filter_model(model, params.get("refdes") or [], params.get("nets") or [])
    counts = {key: len(value) for key, value in model.items() if isinstance(value, list)}
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "layers": model["layers"],
        "counts": counts,
        "observation_cost": {
            "complete_native_model_getters_for_this_projection": 1,
            "complete_physical_instance_captures": 1,
            "sdk_before_after_observations_included": False,
            "cross_phase_capture_reuse": False,
        },
        "prompts": prompts,
        "_untrusted": ["pcb", "prompts", "model", "layout_reminders", "layout_reminder_coverage"],
        **layout_reminders.report(model),
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        binding = board_artifacts.provenance(params, pcb_path, model, "snapshot")
        output_path.write_text(
            json.dumps({**model, "_provenance": binding}, ensure_ascii=False), encoding="utf-8"
        )
        result["path"] = str(output_path)
        result["artifact"] = board_artifacts.finish(output_path, binding)
    else:
        result["model"] = model
    return result


def _pin_index_of(component: Any, refdes: str) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []
    for pin in _items(component.Pins):
        try:
            net = _value(pin, "Net", default=None)
            pins.append(
                {
                    "refdes": refdes,
                    "pin": str(_value(pin, "Name", default="")),
                    "net": str(_value(net, "Name", default="")) if net else "",
                    "x": round(float(pin.GetPositionX(UNIT_MM)), 4),
                    "y": round(float(pin.GetPositionY(UNIT_MM)), 4),
                    "layer": int(_value(pin, "Layer", default=1) or 1),
                }
            )
        except Exception:
            continue
    return pins


def resized_box(
    width: float, height: float, text_height: float, pen: float, new_height: float, new_pen: float
) -> tuple[float, float]:
    """A text's box after its height and pen change: the strokes scale with the height
    (a vector font), and the pen adds its width once."""
    scale = new_height / text_height if text_height > 0 else 1.0
    return (
        round((width - pen) * scale + new_pen, 4),
        round((height - pen) * scale + new_pen, 4),
    )


def _label_size(params: dict[str, Any], key: str, low: float, high: float) -> float | None:
    if params.get(key) is None:
        return None
    try:
        value = float(params[key])
    except (TypeError, ValueError) as exc:
        raise AdapterError("E_USAGE", f"{key} is millimetres") from exc
    if not low <= value <= high:
        raise AdapterError("E_VALIDATION", f"{key} must be between {low:g} and {high:g} mm")
    return value


LABEL_STATE_GUARD = "labels_input_sha256_v1"


def _tidy_labels(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Move every part's silkscreen reference designator to a free spot beside the
    part (`board_layout.place_labels`): clear of other parts, pads, holes, the other
    labels and the board's edge. The text objects are the cells' own (`Component.
    FabricationLayerTexts` on the silkscreen), moved with `Move`. With `height` or
    `pen` (mm) every designator takes that size first (`Format.SetHeight` and
    `SetPenWidth`); with `min_height` only the lower ones are raised to it. The spots
    are found for the new sizes."""
    if "label_moves" in params:
        from . import board_labels

        return board_labels.execute(params, client, sys.modules[__name__])
    if params.get("apply"):
        raise AdapterError(
            "E_UNSUPPORTED",
            "Automatic label relocation/resizing lacks save-before-protection proof; "
            "use explicit --file moves",
            {"write_attempted": False, "can_apply": False},
        )
    from . import board_layout
    from .placement import digest

    pcb_path = _layout_board_path(params)
    try:
        gap = float(params.get("gap") if params.get("gap") is not None else board_layout.LABEL_GAP)
    except (TypeError, ValueError) as exc:
        raise AdapterError("E_USAGE", "gap is millimetres") from exc
    size = _label_size(params, "height", 0.3, 5.0)
    minimum = _label_size(params, "min_height", 0.3, 5.0)
    pen = _label_size(params, "pen", 0.05, 1.0)
    apply = bool(params.get("apply", False))
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    board = _board_rect(doc)
    labels: list[dict[str, Any]] = []
    label_state: list[dict[str, Any]] = []
    texts: dict[str, Any] = {}
    selected = set(str(v) for v in params.get("refdes") or [])
    protected: list[str] = []
    obstacles: list[tuple[float, float, float, float]] = []
    for component in _items(_com_member(doc, "Components")):
        refdes = str(_value(component, "RefDes", default=""))
        if not refdes or not bool(_value(component, "Placed", default=False)):
            continue
        try:
            part = _extrema_mm(component)
        except Exception:
            continue
        for pin in _items(component.Pins):
            try:
                px, py = float(pin.GetPositionX(UNIT_MM)), float(pin.GetPositionY(UNIT_MM))
                obstacles.append((px - 0.7, py - 0.7, px + 0.7, py + 0.7))
            except Exception:
                continue
        text_obj = None
        for text in _items(_com_member(component, "FabricationLayerTexts")):
            try:
                if (
                    int(_value(text, "Type", default=0) or 0) == FAB_GFX_SILKSCREEN
                    and str(_value(text, "TextString", default="")) == refdes
                ):
                    text_obj = text
                    break
            except Exception:
                continue
        if text_obj is None:
            continue
        try:
            fmt = text_obj.Format
            text_height = float(_value(fmt, "Height", default=40) or 40) * TH_TO_MM
            text_pen = float(_value(fmt, "PenWidth", default=0) or 0) * TH_TO_MM
            height = text_height
            width = max(len(refdes), 1) * height * 0.82 + 0.2
            orientation = float(_value(fmt, "Orientation", default=0.0) or 0.0) % 180
            if abs(orientation - 90) < 1:
                width, height = height, width  # the text stands upright with its part
            measured_box = None
            try:
                tx0, ty0, tx1, ty1 = _extrema_mm(text_obj)
                measured_box = (tx0, ty0, tx1, ty1)
                if tx1 - tx0 > 0.1 and ty1 - ty0 > 0.1:
                    width, height = tx1 - tx0, ty1 - ty0
            except Exception:
                pass
            x = float(text_obj.PositionX) * TH_TO_MM
            y = float(text_obj.PositionY) * TH_TO_MM
        except Exception:
            continue
        new_height = size or max(text_height, minimum or 0.0)
        new_pen = pen or text_pen
        label_state.append(
            {
                "refdes": refdes,
                "part": list(part),
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "text_height": text_height,
                "pen": text_pen,
                "orientation": orientation,
            }
        )
        if selected and refdes not in selected:
            # Fixed labels remain measured obstacles and part of the drift guard.
            # Filtering the output of place_labels would not preserve collisions.
            if measured_box is None:
                raise AdapterError(
                    "E_NATIVE_FAILURE",
                    "A protected designator has no measured bounds",
                    {"refdes": refdes, "write_attempted": False},
                )
            obstacles.append(measured_box)
            protected.append(refdes)
            continue
        resize = abs(new_height - text_height) > 1e-4 or abs(new_pen - text_pen) > 1e-4
        if resize:
            width, height = resized_box(width, height, text_height, text_pen, new_height, new_pen)
        texts[refdes] = text_obj
        labels.append(
            {
                "refdes": refdes,
                "width": round(width, 3),
                "height": round(height, 3),
                "part": [round(v, 3) for v in part],
                "x": round(x, 3),
                "y": round(y, 3),
                "text_height": round(new_height, 3),
                "pen": round(new_pen, 3),
                "resized": resize,
            }
        )
    for hole in _hole_records(doc):
        if hole.get("x") is None:
            continue
        r = float(hole.get("diameter") or MOUNTING_HOLE_DIAMETER_MM) / 2 + 0.6
        obstacles.append((hole["x"] - r, hole["y"] - r, hole["x"] + r, hole["y"] + r))
    # the free silkscreen texts (zone labels) stay where they are and are obstacles
    for text in _items(_com_member(doc, "FabricationLayerTexts")):
        try:
            if int(_value(text, "Type", default=0) or 0) != FAB_GFX_SILKSCREEN:
                continue
            if _value(text, "Component", default=None) is not None:
                continue
            tx0, ty0, tx1, ty1 = _extrema_mm(text)
            obstacles.append((tx0, ty0, tx1, ty1))
        except Exception:
            continue
    missing = selected - texts.keys()
    if missing:
        raise AdapterError(
            "E_NOT_FOUND",
            "Selected placed designators could not be read",
            {"refdes": sorted(missing), "write_attempted": False},
        )
    plan = board_layout.place_labels(labels, obstacles, board, gap)
    try:
        state_digest = digest(
            {
                "pcb": str(pcb_path),
                "board": list(board),
                "labels": sorted(label_state, key=lambda row: row["refdes"]),
                "obstacles": sorted(obstacles),
                "requested": {
                    "gap": gap,
                    "height": size,
                    "min_height": minimum,
                    "pen": pen,
                    "refdes": sorted(selected),
                },
            }
        )
    except (ValueError, TypeError) as exc:
        raise AdapterError(
            "E_PROJECT_INVALID",
            "the designator plan contains invalid observations",
            {"stage": "plan", "write_attempted": False},
        ) from exc
    if apply and (
        params.get("state_guard") != LABEL_STATE_GUARD or params.get("state_digest") != state_digest
    ):
        raise AdapterError(
            "E_CONFLICT",
            "the label layout changed or its guard is missing; run the dry run again",
            {"stage": "preflight", "write_attempted": False, "state_digest": state_digest},
        )
    sizes = {item["refdes"]: item for item in labels}
    for item in plan:
        label = sizes[item["refdes"]]
        item.update(
            {key: label[key] for key in ("text_height", "pen", "resized")},
        )
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "state_guard": LABEL_STATE_GUARD,
        "state_digest": state_digest,
        "labels": plan,
        "selected_refdes": sorted(texts),
        "protected_refdes": sorted(protected),
        "moved": sum(1 for item in plan if item["moved"]),
        "resized": sum(1 for item in plan if item["resized"]),
        "unplaced": [item["refdes"] for item in plan if not item["placed"]],
        "applied": False,
        "saved": False,
        "prompts": prompts,
        "_untrusted": ["pcb", "labels", "prompts"],
    }
    return result


MOUNTING_HOLE_DIAMETER_MM = 2.2  # an M2 screw
MOUNTING_HOLE_INSET_MM = 3.5  # from both edges to the hole's centre
CORNER_KEEPOUT_MM = 7.0  # what the placement planner leaves free at a corner with a hole


def _hole_records(doc: Any) -> list[dict[str, Any]]:
    """Every mounting hole on the board: position in mm and padstack name."""
    records: list[dict[str, Any]] = []
    for hole in _items(_com_member(doc, "MountingHoles")):
        record: dict[str, Any] = {}
        for key, getter in (("x", "GetPositionX"), ("y", "GetPositionY")):
            try:
                record[key] = round(float(getattr(hole, getter)(UNIT_MM)), 3)
            except Exception:
                record[key] = None
        try:
            padstack = _value(hole, "Padstack", default=None)
            record["padstack"] = str(_value(padstack, "Name", default="")) if padstack else ""
        except Exception:
            record["padstack"] = ""
        records.append(record)
    return records


LAYOUT_PADSTACK_NAMES = 227  # Document.PadstackNames(type, pin class, pattern, central)
_HOLE_PADSTACK = re.compile(
    r"^(?:MH-C(?P<a>\d+(?:\.\d+)?)-NONPLATED|TH_Round_(?P<b>\d+(?:\.\d+)?)_Nonplated)$"
)


def _mounting_hole_padstack(doc: Any, diameter: float) -> str:
    """The central library's non-plated round padstack of this diameter.

    The dry run checks it too: Layout only refuses a missing padstack when the hole
    is put, after the token was issued for a plan that could never be placed.
    """
    names = [str(name) for name in (_propget(doc, LAYOUT_PADSTACK_NAMES, -1, -1, "*", True) or ())]
    sizes: dict[float, str] = {}
    for name in names:
        match = _HOLE_PADSTACK.match(name)
        if match:
            sizes.setdefault(float(match.group("a") or match.group("b")), name)
    for size, name in sizes.items():
        if abs(size - diameter) < 1e-6:
            return name
    raise AdapterError(
        "E_NOT_FOUND",
        f"the central library has no non-plated {diameter:g} mm mounting-hole padstack",
        {
            "diameter": diameter,
            "available": sorted(sizes),
            "hint": "pick one of the available diameters; library materialize creates the "
            "MH-C<d>-NONPLATED padstack a design's mounting holes need",
        },
    )


def _mounting_holes(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Read, and with `apply` place, mounting holes: one in each corner of the board
    outline, `inset` mm from both edges, through `Document.PutMountingHoleEx` with the
    central library's `MH-C<diameter>-NONPLATED` padstack (the library materialize creates the
    2.2 mm one). A corner that already has a hole within 0.5 mm is left alone."""
    pcb_path = _layout_board_path(params)
    try:
        diameter = float(params.get("diameter") or MOUNTING_HOLE_DIAMETER_MM)
        inset = float(params.get("inset") or MOUNTING_HOLE_INSET_MM)
    except (TypeError, ValueError) as exc:
        raise AdapterError("E_USAGE", "diameter and inset are millimetres") from exc
    if diameter <= 0 or inset <= 0:
        raise AdapterError("E_VALIDATION", "diameter and inset must be positive")
    apply = bool(params.get("apply", False))
    replace = bool(params.get("replace", False))
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    board = _board_rect(doc)
    padstack = _mounting_hole_padstack(doc, diameter)
    existing = _hole_records(doc)
    corners = [
        (board[0] + inset, board[1] + inset),
        (board[2] - inset, board[1] + inset),
        (board[2] - inset, board[3] - inset),
        (board[0] + inset, board[3] - inset),
    ]
    planned = [
        {"x": round(x, 3), "y": round(y, 3)}
        for x, y in corners
        if replace
        or not any(
            item["x"] is not None and abs(item["x"] - x) < 0.5 and abs(item["y"] - y) < 0.5
            for item in existing
        )
    ]
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "board": {"min_x": board[0], "min_y": board[1], "max_x": board[2], "max_y": board[3]},
        "padstack": padstack,
        "diameter": diameter,
        "inset": inset,
        "existing": existing,
        "planned": planned,
        "replace": replace,
        "applied": False,
        "saved": False,
        "prompts": prompts,
        "_untrusted": ["pcb", "existing", "prompts"],
    }
    if not apply:
        return result
    removed = 0
    if replace:
        # the holes follow the outline: after `pcb outline set` grew the board, the old
        # corners are no longer corners
        for hole in list(_items(_com_member(doc, "MountingHoles"))):
            try:
                hole.Delete()
                removed += 1
            except Exception as exc:
                raise _com_error(exc, "delete_mounting_hole") from exc
    result["removed"] = removed
    placed: list[dict[str, Any]] = []
    for item in planned:
        try:
            doc.PutMountingHoleEx(
                item["x"], item["y"], padstack, True, 0, False, None, None, 0, UNIT_MM
            )
        except Exception as exc:
            raise _com_error(
                exc, f"put_mounting_hole {item['x']},{item['y']} with {padstack}"
            ) from exc
        placed.append(item)
    try:
        doc.Save()
    except Exception as exc:
        raise _com_error(exc, "save_after_holes") from exc
    result.update(
        {"placed": placed, "existing_after": _hole_records(doc), "applied": True, "saved": True}
    )
    return result


def _board_outline(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Existing finite rectangle edit with complete native/lifecycle readback."""
    from .outline_readback import execute

    return execute(params, client, sys.modules[__name__])


def _outline_state(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Read all three complete contours and protected material; never open/switch."""
    from .outline_readback import observe

    return observe(params, client, sys.modules[__name__])


POINTS_ARRAY_UNIT_MM = 0.0254  # `Geometry.PointsArray` comes back in thousandths of an inch


def _outline_radius(doc: Any) -> float:
    """The corner radius of the board outline in mm (0 for a plain rectangle): the arc
    rows of its points array carry the radius, whatever the document's unit."""
    outline = _value(doc, "BoardOutline", default=None)
    if outline is None:
        return 0.0
    try:
        points = outline.Geometry.PointsArray
        radii = [abs(float(value)) for value in points[2]] if points else []
    except Exception:
        return 0.0
    return round(max(radii) * POINTS_ARRAY_UNIT_MM, 3) if radii else 0.0


def _plane_shapes(doc: Any) -> list[dict[str, Any]]:
    shapes: list[dict[str, Any]] = []
    for shape in _items(_com_member(doc, "PlaneShapes")):
        try:
            net = _value(shape, "Net", default=None)
            shapes.append(
                {
                    "net": str(_value(net, "Name", default="")) if net is not None else "",
                    "layer": _value(shape, "Layer", default=None),
                }
            )
        except Exception:
            continue
    return shapes


def _plane_pour(params: dict[str, Any], client: Any) -> dict[str, Any]:
    from .board_copper import execute

    return execute(params, client, sys.modules[__name__])


PLANE_DATA_DYNAMIC = 1  # epcbPlaneDataStateDynamic; 3 is Draft, shapes without copper


PLANE_DATA_DRAFT = 3  # epcbPlaneDataStateDraft


def _regenerate_planes(doc: Any) -> int:
    """Global assignment toggling has no bounded protection contract; never dispatch it."""
    from .board_trace import refuse_broad

    refuse_broad(sys.modules[__name__], "global plane regeneration")


BATCH_DRC_COMMAND = 32769  # Document Menu Bar > 分析 > 批量 DRC... (an MFC command id)
HAZARDS_BATCH = 2  # epcbHazardTypeBatch
HAZARDS_ONLINE = 1  # epcbHazardTypeOnline
DRC_PROMPTS: tuple[tuple[str, str, str | None], ...] = (
    ("批量 DRC", "确定", None),
    ("Batch DRC", "OK", None),
)


# hazard kinds that are advice, not violations: a via under a part's body is normal
# for tented vias on a surface-mount board (Batch DRC lists them as a check anyway)
DRC_WARNING_KINDS = frozenset({"ViasUnderParts"})


def _hazard_type_names(client: Any) -> dict[int, str]:
    names: dict[int, str] = {}
    constants = getattr(client, "constants", None)
    for table in getattr(constants, "__dicts__", []) or []:
        for key, value in table.items():
            if key.startswith("epcbHazardType") and isinstance(value, int):
                names.setdefault(value, key[len("epcbHazardType") :])
    return names


def _hazard_placement_rectangle(item: Any) -> dict[str, Any]:
    """Observe only151.0 native straight closed placement rectangles.

    PlacementOutline has no PositionX/Y getter. A computed rectangle corner is
    shape evidence, never a substituted native position or persistent identity.
    """
    result = {
        "source": "IMGCPCBPlacementOutline151.Geometry.PointsArray",
        "unit": "mm",
        "complete": False,
        "points_array_mm": None,
        "rectangle_bounds_mm": None,
        "rectangle_min_corner_mm": None,
        "native_extrema_mm": None,
        "cutout_count": None,
        "position_getter_supported": False,
        "errors": [],
    }
    try:
        geometry = _propget(item, 8)
        raw = _propget(geometry, 1, UNIT_MM)
        if (
            not isinstance(raw, (tuple, list))
            or len(raw) != 3
            or any(not isinstance(row, (tuple, list)) or len(row) != 5 for row in raw)
        ):
            raise ValueError("Only a native five-point closed rectangle is supported")
        points = [list(row) for row in raw]
        if any(type(v) not in (int, float) or not math.isfinite(v) for row in points for v in row):
            raise ValueError("Nonfinite native placement geometry")
        result["points_array_mm"] = points
        vertices = list(zip(points[0], points[1], strict=True))
        xs, ys = sorted(set(points[0])), sorted(set(points[1]))
        if (
            any(points[2])
            or len(xs) != 2
            or len(ys) != 2
            or vertices[0] != vertices[-1]
            or set(vertices) != {(x, y) for x in xs for y in ys}
            or any(
                (left[0] == right[0]) == (left[1] == right[1])
                for left, right in zip(vertices, vertices[1:], strict=False)
            )
        ):
            raise ValueError("Native placement outline is not an axis-aligned rectangle")
        bounds = [xs[0], ys[0], xs[1], ys[1]]
        result["rectangle_bounds_mm"] = bounds
        result["rectangle_min_corner_mm"] = bounds[:2]
        cutouts = _propget(geometry, 22)
        count = cutouts.Count
        result["cutout_count"] = count
        if type(count) is not int or count != 0:
            raise ValueError("Placement cutouts are unsupported")
        extrema = _propget(item, 7)
        if extrema.IsValid() is not True:
            raise ValueError("Native placement extrema are invalid")
        observed = [_propget(extrema, n, UNIT_MM) for n in (1, 2, 3, 4)]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in observed):
            raise ValueError("Native placement extrema are incomplete")
        result["native_extrema_mm"] = observed
        if not (
            observed[0] <= bounds[0] < bounds[2] <= observed[2]
            and observed[1] <= bounds[1] < bounds[3] <= observed[3]
        ):
            raise ValueError("Native placement extrema do not contain the observed rectangle")
        result["complete"] = True
    except Exception as error:
        result["errors"].append({"cause": type(error).__name__, "reason": str(error)})
    return result


def _hazard_object_detail(item: Any) -> dict[str, Any]:
    """Read supported object-specific fields; never guess generic COM getters."""
    result = dict.fromkeys(
        ("refdes", "net", "pin_number", "position_mm", "uid", "component_uid", "layer", "type_name")
    )
    result["observation_errors"] = []

    def read(owner, dispid, field, *args):
        try:
            return _propget(owner, dispid, *args)
        except Exception as error:
            result["observation_errors"].append(
                {"field": field, "dispid": dispid, "cause": type(error).__name__}
            )
            return None

    try:
        name = item._oleobj_.GetTypeInfo().GetDocumentation(-1)[0]
        result["type_name"] = name
        if name not in {"IMGCPCBPin", "IMGCPCBPlacementOutline"}:
            raise ValueError("Native hazard object type is outside the finite151.0 reader")
        uid = read(item, 64 if name == "IMGCPCBPin" else 42, "uid")
        result["uid"] = str(uid) if uid is not None else None
        component = read(item, 16 if name == "IMGCPCBPin" else 5, "component")
        if component is not None:
            owner_uid = read(component, 87, "component_uid")
            result["component_uid"] = str(owner_uid) if owner_uid is not None else None
            refdes = read(component, 86, "refdes")
            result["refdes"] = refdes if isinstance(refdes, str) and refdes else None
        if name == "IMGCPCBPin":
            pin_name = read(item, 0, "pin_number")
            result["pin_number"] = pin_name if isinstance(pin_name, str) else None
            net = read(item, 8, "net")
            net_name = _value(net, "Name", default=None) if net is not None else None
            result["net"] = net_name if isinstance(net_name, str) and net_name else None
            position = [read(item, n, "position_mm", UNIT_MM) for n in (2, 3)]
            if all(type(v) in (int, float) and math.isfinite(v) for v in position):
                result["position_mm"] = position
                result["position_source"] = "IMGCPCBPin151.PositionX/PositionY(EPcbUnitMM4)"
            try:
                smd = item.IsSMD()
                if type(smd) is not bool:
                    raise ValueError("Native IsSMD returned no BOOL")
                result["is_smd"] = smd
                if smd:
                    result["layer"] = read(item, 48, "layer")
                    result["pin_layer_status"] = "observed_smd_layer"
                else:
                    result["pin_layer_status"] = "not_read_not_valid_for_through_hole_pin"
            except Exception as error:
                result["observation_errors"].append(
                    {"field": "is_smd", "cause": type(error).__name__}
                )
            from . import native_pad_mask

            result["layer_material_observation"] = native_pad_mask.observe_pin(
                item, 1, sys.modules[__name__]
            )
        else:
            result["layer"] = read(item, 31, "layer")
            result["position_source"] = "not_supported_on_IMGCPCBPlacementOutline151"
            result["placement_geometry_observation"] = _hazard_placement_rectangle(item)
    except Exception as error:
        result["observation_errors"].append(
            {"field": "type_specific_reader", "cause": type(error).__name__, "reason": str(error)}
        )
    result["unobserved_fields"] = [k for k, v in result.items() if v is None]
    return result


def _hazard_record(hazard: Any, names: dict[int, str]) -> dict[str, Any]:
    kind = _value(hazard, "Type", default=None)
    record: dict[str, Any] = {
        "type": kind,
        "kind": names.get(int(kind), str(kind)) if isinstance(kind, int) else str(kind),
        "description": str(_value(hazard, "Description", default="")).strip(),
        "accepted": bool(_value(hazard, "Accepted", default=False)),
    }
    try:
        position = [_propget(hazard, n, UNIT_MM) for n in (9, 10)]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in position):
            raise ValueError("Native hazard position is incomplete")
        record["x"], record["y"] = position
    except Exception as error:
        record["position_observation_error"] = {"cause": type(error).__name__, "reason": str(error)}
    # Installed151.0 IMGCPCBHazard declares indexed PROPERTYGET, not the
    # legacy GetRequiredClearance/GetActualClearance method spellings. Preserve
    # getter failures and unquantized units; a report field is not a substitute.
    proximity = {
        "source": "IMGCPCBHazard151.propertyget",
        "unit": "mm",
        "first_object_position_mm": [None, None],
        "second_object_position_mm": [None, None],
        "actual_clearance_mm": None,
        "required_clearance_mm": None,
        "complete": False,
        "errors": [],
    }
    fields = (
        ("first_object_position_mm", 0, 11),
        ("first_object_position_mm", 1, 12),
        ("second_object_position_mm", 0, 13),
        ("second_object_position_mm", 1, 14),
        ("actual_clearance_mm", None, 16),
        ("required_clearance_mm", None, 17),
    )
    for field, axis, dispid in fields:
        try:
            value = _propget(hazard, dispid, UNIT_MM)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("nonfinite or nonnumeric native hazard property")
            if axis is None:
                proximity[field] = value
                if value < 0:
                    raise ValueError("native clearance is undefined or unavailable")
            else:
                proximity[field][axis] = value
        except Exception as error:
            proximity["errors"].append(
                {"field": field, "axis": axis, "dispid": dispid, "cause": type(error).__name__}
            )
    proximity["complete"] = not proximity["errors"]
    record["proximity_observation"] = proximity
    record["actual"] = proximity["actual_clearance_mm"]
    record["required"] = proximity["required_clearance_mm"]
    objects: list[str] = []
    details = []
    try:
        collection = _com_member(hazard, "Objects")
        if type(collection.Count) is not int or not 0 <= collection.Count <= 6:
            raise ValueError("Native hazard object collection is outside the finite bound")
        for item in _items(collection, strict=True):
            detail = _hazard_object_detail(item)
            details.append(detail)
            objects.append(detail.get("refdes") or detail.get("type_name") or "unread_object")
        if len(details) != collection.Count:
            raise ValueError("Native hazard object collection changed during observation")
        record["object_collection_complete"] = True
    except Exception as error:
        record["object_collection_complete"] = False
        record["object_collection_error"] = {"cause": type(error).__name__, "reason": str(error)}
    record["objects"] = objects[:6]
    record["object_details"] = details
    return record


def _hazards(doc: Any, client: Any, kind: int) -> list[dict[str, Any]]:
    names = _hazard_type_names(client)
    try:
        collection = doc.GetHazards(kind)
        count = collection.Count
        if type(count) is not int or not 0 <= count <= 10000:
            raise ValueError("Native hazard Count is unavailable or outside the finite bound")
        rows = list(_items(collection, strict=True))
        if len(rows) != count or collection.Count != count:
            raise ValueError("Native hazard enumeration is incomplete or changed")
    except Exception as exc:
        raise AdapterError(
            "E_NATIVE_FAILURE",
            "Native hazards could not be completely observed; no zero count is inferred",
            {
                "stage": "GetHazards/Count/enumeration",
                "cause": type(exc).__name__,
                "hazards_read": False,
                "count": None,
                "write_attempted": False,
            },
        ) from exc
    return [_hazard_record(item, names) for item in rows]


def _processing_windows() -> int:
    from . import win_dialogs

    return sum(
        1
        for window in win_dialogs.top_windows("expeditionpcb.exe")
        if "processing" in window["title"].lower() or "处理" in window["title"]
    )


def _batch_drc(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Run the native DRC engine once, or observe existing hazards without Go.

    The run path delegates to drc_engine: it binds the board, selected inputs and
    fresh automatic reports. Reopened hazard matching and rule applicability are
    independent later checks. The no-run path reads existing GetHazards only and
    cannot establish current execution, completion or a pass.
    """
    timeout = float(params.get("timeout", 600.0))
    if not math.isfinite(timeout) or timeout <= 0:
        raise AdapterError("E_VALIDATION", "Batch DRC timeout must be finite and positive")
    if params.get("run", True):
        from . import drc_engine

        return drc_engine.run(params, client, sys.modules[__name__])
    pcb_path = _layout_board_path(params)
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    requested = bool(params.get("run", True))
    seconds = 0.0
    accepted = False
    processing_seen = False
    processing_finished = False
    timed_out = False
    reason = "check_not_run"
    if requested:
        started = time.monotonic()
        deadline = started + timeout
        with _PromptAnswerer(DRC_PROMPTS) as answerer:
            try:
                accepted = app.Gui.ProcessCommand(BATCH_DRC_COMMAND)
            except Exception as exc:
                raise _com_error(exc, "batch_drc_command") from exc
            if not accepted:
                raise AdapterError(
                    "E_SERVER",
                    "Layout did not accept the Batch DRC command",
                    {"id": BATCH_DRC_COMMAND},
                )
            while time.monotonic() < deadline:
                processing = _processing_windows()
                if processing:
                    processing_seen = True
                elif time.monotonic() - started > 6.0:
                    processing_finished = processing_seen
                    break
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
            else:
                timed_out = True
        prompts = prompts + answerer.answered
        seconds = round(time.monotonic() - started, 1)
        reason = (
            "wait_timed_out"
            if timed_out
            else "result_freshness_unconfirmed"
            if processing_finished
            else "processing_not_observed"
        )
    hazards_read = not requested or (processing_finished and not timed_out)
    hazards = _hazards(doc, client, HAZARDS_BATCH) if hazards_read else []
    online = (
        _hazards(doc, client, HAZARDS_ONLINE)
        if hazards_read and bool(params.get("online", False))
        else []
    )
    summary: dict[str, int] = {}
    for item in hazards:
        summary[item["kind"]] = summary.get(item["kind"], 0) + 1
    errors = sum(1 for item in hazards if item["kind"] not in DRC_WARNING_KINDS)
    return {
        "pcb": str(pcb_path),
        "ran": False,
        "completion": {
            "status": "incomplete" if requested else "not_run",
            "reason": reason,
            "command_accepted": bool(accepted),
            "processing_observed": processing_seen,
            "processing_finished": processing_finished,
            "timed_out": timed_out,
        },
        "source": {
            "kind": "native_checker",
            "method": "Document.GetHazards(epcbHazardTypeBatch)",
            "complete": False,
            "freshness": "unknown" if requested else "existing",
            "application_version": None,
            "checker_version": None,
            "input": {
                "requested_project": params.get("project"),
                "observed_pcb": _value(doc, "FullName", default=None),
                "snapshot_sha256": None,
            },
            "coverage": {
                "rule_config_sha256": None,
                "rules_checked": None,
                "excluded_rules": None,
            },
            "limitations": [
                "processing windows are not a native completion or result-generation source",
                "checker/application versions, input snapshot and rule coverage are unobserved",
            ],
        },
        "verification": {
            "valid": False,
            "status": "incomplete" if requested else "not_run",
            "reason": reason,
        },
        "hazards_read": hazards_read,
        "seconds": seconds,
        "count": len(hazards) if hazards_read else None,
        "summary": summary,
        "hazards": hazards,
        "online": online,
        "clean": False,
        "errors": errors if hazards_read else None,
        "warnings": len(hazards) - errors if hazards_read else None,
        "passes": False,
        "prompts": prompts,
        "_untrusted": ["pcb", "hazards", "online", "prompts", "source.input"],
    }


TOP_VIEW_SCHEME = "Loc: Top View"
TOP_VIEW_FILE = "Top View.dcs"
SCHEME_ITEMS_OFF = (
    "Assy_Outline_On",
    "Assy_Part_Number_On",
    "Assy_Ref_Des_On",
    "Tpoint_Assy_Ref_Des_On",
    "Tpoint_Silk_Ref_Des_On",  # the test point's own designator on the pad; the moved one stays
    "Drill_Drawing_Through_On",
    "Silk_Part_Number_On",
)


def top_view_scheme_text(all_on: str, layers: int) -> str:
    """The "Top View" display scheme from the stock "All On" one: plane copper drawn
    filled (`Option.Planes.Data.Fill`), the inner layers' pads and traces off, the
    assembly-layer texts and outlines and the drill drawing off — what `pcb render`
    shows, so a person at Layout's screen sees the pours and the silkscreen, not every
    layer at once."""
    text = all_on.replace('"Option.Planes.Data.Fill" "0"', '"Option.Planes.Data.Fill" "1"')
    inner = [str(n) for n in range(2, max(layers, 2))]
    for key in ("Trace_Layer_On", "Pad_Layer_On"):
        match = re.search(r"(\.\." + key + r" \(\s*\n\t)([TF](?: [TF])*)", text)
        if not match:
            continue
        flags = match.group(2).split(" ")
        for number in inner:
            index = int(number) - 1
            if index < len(flags):
                flags[index] = "F"
        text = text[: match.start(2)] + " ".join(flags) + text[match.end(2) :]
    for key in SCHEME_ITEMS_OFF:
        text = re.sub(r"(\.\." + key + r"\s+)True", r"\1False", text)
    # the key-value section is what this Layout reads; the blocks above are the
    # legacy form kept alongside ("d:1" = displayed)
    for number in inner:
        text = text.replace(f'"LayerControl.{number}" "d:1"', f'"LayerControl.{number}" "d:0"')
    # an item entry is `"name" "d:1" "e:1" …`: d is the display-control state and e
    # whether the item is drawn; both go off
    # … and the placement-level items a person looking at the finished board does
    # not want: the designator drawn at the cell, the cell origin marker, the
    # placement outlines and the pin numbers
    hidden = (
        r"Fabrication\.Assembly\.[^\"]*",
        r"Fabrication\.DrillDrawingThrough",
        r"Fabrication\.Silkscreen\.Part\.Text\.PartNumber\.[^\"]*",
        r"Place\.Part\.Text\.RefDes\.[^\"]*",
        r"Part\.Cell\.Origin\.[^\"]*",
        r"Part\.PlaceOutline\.[^\"]*",
        r"Part\.Pin\.NumberType\.[^\"]*",
    )
    text = re.sub(
        r'("(?:' + "|".join(hidden) + r')") "d:[01]" "e:[01]"',
        r'\1 "d:0" "e:0"',
        text,
    )
    for key in (
        "Option.Pin.Number.Top",
        "Option.Pin.Number.Bottom",
        "Option.Pin.Type.Top",
        "Option.Pin.Type.Bottom",
        "Option.Pin.NetName.Top",
        "Option.Pin.NetName.Bottom",
    ):
        text = text.replace(f'"{key}" "1"', f'"{key}" "0"')
    for key in (
        "Option.Fabrication.AssemblyItems.Top",
        "Option.Fabrication.AssemblyItems.Bottom",
        "Option.Fabrication.DrillDrawing",
    ):
        text = text.replace(f'"{key}" "1"', f'"{key}" "0"')
    return text


def _ensure_top_view_scheme(config: Path, layers: int) -> bool:
    from .board_view import ensure_scheme, scheme_plan

    plan = scheme_plan(
        config.parent / "bound.pcb", layers, {"top_view": True}, sys.modules[__name__]
    )
    return ensure_scheme(plan, sys.modules[__name__])


def _load_display_scheme(doc: Any, scheme: str) -> bool:
    """Switch the active view to a display scheme by name (`Loc: …` / `Sys: …`) through
    `ActiveView.DisplayControl.LoadScheme`; True when its `Name` reads the scheme back."""
    try:
        control = doc.ActiveView.DisplayControl
        control.LoadScheme(str(scheme))
        return str(_value(control, "Name", default="") or "").strip() == str(scheme).strip()
    except Exception:
        return False


def _fit_board_view(app: Any, doc: Any) -> bool:
    """Fit the board outline into the active view: `ActiveView.SetExtentsToBoard`, else
    Layout's own `VIEW_FITBOARD` command through `Application.Gui.ProcessCommand`."""
    try:
        doc.ActiveView.SetExtentsToBoard()
        return True
    except Exception:
        pass
    try:
        return bool(app.Gui.ProcessCommand("VIEW_FITBOARD"))
    except Exception:
        return False


def _zoom_view(doc: Any, window: tuple[float, ...]) -> bool:
    """Zoom the active view onto `window` (mm) with `ActiveView.SetExtentsEx`, which
    keeps the rectangle whole and widens the other way to the view's shape."""
    x1, y1, x2, y2 = window
    try:
        doc.ActiveView.SetExtentsEx(x1, y1, x2, y2, False, UNIT_MM)
        return True
    except Exception:
        pass
    try:
        doc.ActiveView.SetExtents(x1, y1, x2, y2, UNIT_MM)
        return True
    except Exception:
        return False


def display_scheme(params: dict[str, Any]) -> tuple[str, bool]:
    """The display scheme `pcb view` puts the board under, and whether it is the top
    view, which is written first on a board that lacks it: the scheme asked for, else
    the top view -- the board as it will be printed, one designator per part."""
    named = str(params.get("scheme") or "").strip()
    if named and not params.get("top_view"):
        return named, named == TOP_VIEW_SCHEME
    return TOP_VIEW_SCHEME, True


def _show_board(params: dict[str, Any], client: Any) -> dict[str, Any]:
    from .board_view import show

    return show(params, client, sys.modules[__name__])


# RoutePass.PassType values (EPcbAR…Pass) by the name the CLI takes
ROUTE_PASS_TYPES = {
    "fanout": 2,
    "route": 8,
    "novia": 6,
    "viamin": 14,
    "smooth": 10,
    "spread": 11,
    "expand": 1,
    "removehangers": 7,
}
ROUTE_ITEMS_ALL_NETS = 0  # epcbARAllNetsItem
ROUTE_ITEMS_SELECTED_NETS = 8  # epcbARSelectedNetsItem
ROUTE_ORDER_AUTO = 0  # epcbARAutoOrder
# RoutePass.LayerSelect(EPcbARLayerSelectType eType, long nLayer): 0 BottomUp, 1 TopDown,
# 2 AddLayer, 3 RemoveLayer. It used to be called with (layer number, wanted), which
# read layer 2 as AddLayer and 3 as RemoveLayer and refused the outer layers.
ROUTE_LAYER_REMOVE = 3
DEFAULT_ROUTE_PASSES = "route:1-5,viamin:1-3,smooth:1-3"


def parse_route_passes(text: str) -> list[dict[str, Any]]:
    """`route:1-5,viamin,smooth:2` → pass records with their effort range (1..5)."""
    passes: list[dict[str, Any]] = []
    for token in str(text or DEFAULT_ROUTE_PASSES).split(","):
        token = token.strip().lower()
        if not token:
            continue
        name, _sep, effort = token.partition(":")
        if name not in ROUTE_PASS_TYPES:
            raise AdapterError(
                "E_USAGE",
                "unknown route pass",
                {"pass": name, "known": sorted(ROUTE_PASS_TYPES)},
            )
        start, end = 1, 3
        if effort:
            first, _sep, last = effort.partition("-")
            try:
                start = int(first)
                end = int(last) if last else start
            except ValueError as exc:
                raise AdapterError("E_USAGE", "effort is N or N-M", {"pass": token}) from exc
        if not 1 <= start <= end <= 5:
            raise AdapterError("E_USAGE", "effort runs from 1 to 5", {"pass": token})
        passes.append({"pass": name, "type": ROUTE_PASS_TYPES[name], "effort": [start, end]})
    if not passes:
        raise AdapterError("E_USAGE", "no route passes given")
    return passes


def _route_status(doc: Any) -> dict[str, Any]:
    """Observed connectivity, never an empty/zero default on a COM read failure.

    Net identity is the native UID and unique nonempty name in this bound document.
    Enumeration and each required getter must succeed, even on repeated reads.
    """
    nets: list[dict[str, Any]] = []
    stage = "Nets"
    try:
        collection = doc.Nets
        if callable(collection) and not hasattr(collection, "Count"):
            collection = collection()
        count = collection.Count
        if type(count) is not int or count < 0:
            raise ValueError("Invalid count")
        for net in _items(collection, strict=True):
            stage = "Name"
            name = net.Name
            if not isinstance(name, str) or not name.strip() or any(n["net"] == name for n in nets):
                raise ValueError("Missing or duplicate net identity")
            stage = "UniqueId"
            uid = net.UniqueId
            if (
                type(uid) not in (str, int)
                or not str(uid).strip()
                or any(n["uid"] == str(uid) for n in nets)
            ):
                raise ValueError("Missing or duplicate native net UID")
            stage = "IsRouted"
            routed = net.IsRouted
            if callable(routed):
                routed = routed()  # IMGCPCBNet.IsRouted is a method in installed 151.0.
            if type(routed) not in (int, bool) or routed not in (-1, 0, 1):
                raise ValueError("Missing routed state")
            stage = "NumberOfOpens"
            opens = net.NumberOfOpens
            if type(opens) is not int or opens < 0:
                raise ValueError("Missing open count")
            nets.append({"net": name, "uid": str(uid), "routed": bool(routed), "opens": opens})
            stage = "Nets.Item"
        stage = "Nets.Count"
        if collection.Count != count or len(nets) != count:
            raise ValueError("Collection changed during observation")
    except Exception as exc:
        raise AdapterError(
            "E_NATIVE_FAILURE",
            "Native connectivity observation is incomplete",
            {"stage": stage, "observation_complete": False},
        ) from exc
    nets.sort(key=lambda item: item["net"])
    traces = _com_member(doc, "Traces")
    vias = _com_member(doc, "Vias")
    return {
        "observation_complete": True,
        "net_status": nets,
        "all_connected": all(item["routed"] and item["opens"] == 0 for item in nets),
        "nets": len(nets),
        "routed": sum(1 for item in nets if item["routed"]),
        "opens": sum(item["opens"] for item in nets),
        "traces": int(traces.Count) if traces is not None else None,
        "vias": int(vias.Count) if vias is not None else None,
        "unrouted": [item for item in nets if not item["routed"] or item["opens"] != 0],
    }


def parse_route_layers(value: Any) -> list[int]:
    """Routing layer numbers from `"1,4"`, a list, or nothing (every enabled layer)."""
    if value is None or value == "":
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    layers: list[int] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if not text.isdigit() or int(text) < 1:
            raise AdapterError("E_USAGE", f"routing layers must be positive numbers: {text!r}")
        if int(text) not in layers:
            layers.append(int(text))
    return sorted(layers)


def _traces_by_layer(doc: Any) -> dict[str, int]:
    """How many traces lie on each layer (`"1"`, `"4"`, ...)."""
    counts: dict[str, int] = {}
    for trace in _items(_com_member(doc, "Traces")):
        try:
            layer = str(int(_value(trace, "Layer", default=0) or 0))
        except Exception:
            layer = "?"
        counts[layer] = counts.get(layer, 0) + 1
    return dict(sorted(counts.items()))


def _route_pass(doc: Any, item: dict[str, Any], layers: list[int], selected: bool) -> Any:
    """One configured autorouter pass. `PassType` resets every parameter, so it goes
    first; the layers the pass may not use are removed one by one."""
    route_pass = doc.NewRoutePass()
    route_pass.PassType(item["type"], item["effort"][0], item["effort"][1], False, False)
    route_pass.Items(ROUTE_ITEMS_SELECTED_NETS if selected else ROUTE_ITEMS_ALL_NETS, None)
    route_pass.Order(ROUTE_ORDER_AUTO, 0)
    route_pass.Config(False, True)
    if layers:
        count = int(_value(doc, "LayerCount", default=0) or 0)
        for number in range(1, count + 1):
            if number not in layers:
                route_pass.LayerSelect(ROUTE_LAYER_REMOVE, number)
    return route_pass


def _select_nets(doc: Any, names: list[str]) -> None:
    """Select exactly the named nets (the route passes then work on those alone)."""
    try:
        doc.UnSelectAllNets()
    except Exception as exc:
        raise _com_error(exc, "unselect_nets") from exc
    for name in names:
        net = _find_net(doc, name)
        try:
            net.Selected = True
        except Exception as exc:
            raise _com_error(exc, f"select_net {name}") from exc


def _verify_board_edit_persistence(result, app, doc, pcb_path):
    """Retain known write/save facts when later observation or reopening fails."""
    from .cell_refresh import capture, digest

    result.update(change_status="applied", persistence_state="saved", persistence_verified=False)
    try:
        current = _native_call(
            "board_edit.capture_saved", lambda: capture(doc, sys.modules[__name__], strict=True)
        )
        reopened = _native_call("board_edit.reopen", lambda: _reopen_board(app, pcb_path))
        observed = _native_call(
            "board_edit.capture_reopened",
            lambda: capture(reopened, sys.modules[__name__], strict=True),
        )
        result["geometry_after"] = observed
        result["persistence_verified"] = digest(current) == digest(observed)
        if not result["persistence_verified"]:
            result["post_save_error"] = {"stage": "reopen_readback", "code": "E_CONFLICT"}
    except Exception as error:
        result["post_save_error"] = {
            "stage": "reopen_readback",
            "code": getattr(error, "code", "E_NATIVE_FAILURE"),
        }


def _route_board(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Autorouter passes cannot prove bounded impact/protection and are disabled."""
    if params.get("mode") == "bounded-general":
        from .board_general import execute

        limitation = (
            "MGCPCB151 Gui.ProcessCommand vCommand selector semantics for numeric35367 "
            "are not established; matching Route mode and complete selection did not "
            "produce an accepted native acknowledgement. Public dispatch remains disabled."
        )
        if params.get("apply"):
            raise AdapterError(
                "E_UNSUPPORTED",
                limitation,
                {
                    "write_attempted": False,
                    "native_dispatch_supported": False,
                    "engineering_write_dispatched": False,
                    "selection_write_dispatched": False,
                    "save_close_open_dispatched": False,
                },
            )
        # Pure material binding/comparison remains usable by separately controlled
        # GUI observations. It does not establish this public command selector.
        result = execute(params, client, sys.modules[__name__])
        result["native_dispatch_supported"] = False
        result["native_dispatch_limitation"] = limitation
        return result
    if params.get("mode") == "bounded-native":
        from .board_routepass import execute

        return execute(params, client, sys.modules[__name__])
    from .board_trace import refuse_broad

    refuse_broad(sys.modules[__name__], "autorouter")


def _invoke_forward_annotation(integration: Any) -> Any:
    """ProjectIntegration.tlb declares a no-argument method returning VARIANT_BOOL.

    This object can have no live typeinfo. A dynamic Python attribute probe then
    sends PROPERTYGET, which must not be used to discover a mutating method.
    Resolve the name from the actual object and dispatch the documented method once.
    """
    raw = getattr(integration, "_oleobj_", None)
    if raw is not None:
        member = raw.GetIDsOfNames("ForwardAnnotate")
        return _native_call(
            "annotation.ForwardAnnotate", lambda: raw.InvokeTypes(member, 0, 1, (11, 0), ())
        )
    return _native_call("annotation.ForwardAnnotate", lambda: integration.ForwardAnnotate())


def _forward_annotate(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Forward-annotate the packaged schematic into the board through Project Integration.

    `Document.ProjectIntegration` is a dispatch object without type information, so
    ForwardAnnotate is dispatched explicitly as the documented method. Designer stays
    released through Layout save/reopen; restoring it comes after board persistence. A
    failure is a COM error whose reasons are in `PCB/LogFiles/ForwardAnnotation.txt`.
    """
    pcb_path = _layout_board_path(params)
    # Layout's session with the project database must be younger than Designer's:
    # after `library materialize` cycled the Designer project, a board that stayed open in
    # Layout failed the packaging phase ("正向标注的封装阶段出现错误"), and closing
    # Designer's project instead broke Layout's own connection ("iCDB error getting
    # UID manager"). So the board is closed and opened again before annotating.
    reopened = _close_board_if_open(client, pcb_path)
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path, LAYOUT_PROMPTS_ANNOTATE)
    doc = _licensed_document(app)
    integration = _value(doc, "ProjectIntegration", default=None)
    if integration is None:
        raise AdapterError(
            "E_BACKEND_UNAVAILABLE", "this Layout exposes no ProjectIntegration object"
        )
    before = _com_member(integration, "ForwardAnnotationStatus")
    log_path = pcb_path.parent / "LogFiles" / "ForwardAnnotation.txt"
    answered_yes = any(
        item.get("pressed") in ("是(Y)", "Yes")
        and "正向标注" in str(item.get("text", ""))
        or item.get("pressed") in ("是(Y)", "Yes")
        and "annotat" in str(item.get("text", "")).lower()
        for item in prompts
    )
    if before == PRJINT_IN_SYNCH:
        # nothing left to annotate: either Layout just did it from its own prompt, or
        # the board was in synch already; asking again is reported as a failure
        # An existing report is not evidence of a new annotation. In particular,
        # a library-only refresh must never borrow this old netlist report.
        summary = (
            _annotation_summary(log_path)
            if answered_yes
            else {"components": None, "nets": None, "pins": None}
        )
        if params.get("verify"):
            doc = _reopen_board(app, pcb_path)
        return {
            "pcb": str(pcb_path),
            "outcome": "annotated_on_open" if answered_yes else "in_synch",
            "status_before": before,
            "status_after": before,
            "completed": True,
            "components_found": summary["components"],
            "nets_found": summary["nets"],
            "pins_found": summary["pins"],
            "board": _board_counts(doc),
            "errors": [],
            "warnings": [],
            "prompts": prompts,
            # _reopen_board saves through _save_close_board before reopening.
            "saved": bool(params.get("verify")),
            "persistence_verified": bool(params.get("verify")),
            "log": str(log_path) if answered_yes else None,
            "log_status": "generated_on_open_unverified" if answered_yes else "not_generated",
            "_untrusted": ["pcb", "prompts", "log"],
        }
    allowed = _com_member(integration, "IsForwardAnnotationAllowed")
    if allowed is False:
        raise AdapterError(
            "E_CONFLICT",
            "Layout does not allow forward annotation now",
            {"pcb": str(pcb_path), "status": before},
        )
    routing = _routing_counts(doc)
    removed_routing = {"traces": 0, "vias": 0}
    if bool(params.get("unroute", False)):
        # a cell or pin change on a routed part needs traces broken back, which
        # Layout refuses in its preventive DRC mode ("不能在预防模式下打断导线")
        removed_routing = _delete_routing(doc)
    # the explicit call succeeds with Designer's project closed and fails in its
    # packaging phase with it open, so Designer lets go of the project meanwhile
    designer_app: Any = None
    designer_reopen = False
    raw = params.get("project") or params.get("path")
    project_path = Path(str(raw)).expanduser().resolve() if raw else None
    if project_path is not None and project_path.suffix.lower() == ".prj":
        designer_app, designer_reopen = _release_project(client, project_path)
        if designer_reopen:
            _settle(5.0)
    # Dispatch once: a failure may leave a partially updated board.
    attempts: list[dict[str, Any]] = []
    outcome: Any = None
    answered: list[dict[str, Any]] = []
    primary_error = None
    native_result = None
    try:
        with _PromptAnswerer(LAYOUT_PROMPTS_ANNOTATE) as answerer:
            try:
                outcome = _invoke_forward_annotation(integration)
                attempts.append({"attempt": 1, "ok": True})
                answered.extend(answerer.answered)
            except Exception as exc:
                attempts.append({"attempt": 1, "ok": False, "reason": str(exc)[:200]})
                answered.extend(answerer.answered)
                summary = _annotation_summary(log_path)
                raise AdapterError(
                    "E_VALIDATION",
                    "forward annotation failed; inspect the retained board",
                    {
                        "pcb": str(pcb_path),
                        "errors": summary["errors"],
                        "warnings": summary["warnings"],
                        "log": str(log_path),
                        "attempts": attempts,
                        "prompts": prompts + answered,
                        "board_reopened": reopened,
                        "designer_released": designer_reopen,
                        "status_before": before,
                        "partial": True,
                        "automatic_retry": False,
                    },
                ) from exc
        summary = _annotation_summary(log_path)
        after = _com_member(integration, "ForwardAnnotationStatus")
        saved = False
        if bool(params.get("save", True)):
            try:
                _native_call("annotation.Save", lambda: doc.Save())
                saved = True
            except Exception as exc:
                raise _com_error(exc, "save_after_annotation") from exc
        if params.get("verify"):
            doc = _reopen_board(app, pcb_path)
        native_result = {
            "pcb": str(pcb_path),
            "outcome": "annotated" if outcome else "refused",
            "saved": saved,
            "persistence_verified": bool(params.get("verify")),
            "board": _native_call("annotation.board_readback", lambda: _board_counts(doc)),
            "errors": summary["errors"],
            "warnings": summary["warnings"],
        }
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if designer_reopen and designer_app is not None and project_path is not None:
            try:
                _native_call(
                    "annotation.restore_designer",
                    lambda: _ensure_project(designer_app, project_path),
                )
            except Exception as error:
                diagnostic = {"type": type(error).__name__, "code": getattr(error, "code", None)}
                if primary_error is not None:
                    if isinstance(primary_error, AdapterError):
                        primary_error.details["designer_restoration"] = diagnostic
                    else:
                        primary_error.add_note("Designer restoration also failed")
                else:
                    raise AdapterError(
                        "E_NATIVE_FAILURE",
                        "Board operation returned, but Designer restoration failed",
                        {"native_result": native_result, "designer_restoration": diagnostic},
                    ) from error
    return {
        "pcb": str(pcb_path),
        "outcome": "annotated" if outcome else "refused",
        "status_before": before,
        "status_after": after,
        "completed": summary["completed"],
        "components_found": summary["components"],
        "nets_found": summary["nets"],
        "pins_found": summary["pins"],
        "errors": summary["errors"],
        "warnings": summary["warnings"],
        "prompts": prompts + answered,
        "attempts": attempts,
        "saved": saved,
        "persistence_verified": bool(params.get("verify")),
        "log": str(log_path),
        "board": native_result["board"],
        "routing": routing,
        "routing_removed": removed_routing,
        "board_reopened": reopened,
        "_untrusted": ["pcb", "errors", "warnings", "prompts", "log"],
    }


# What a person watching Designer sees land one at a time under `--pace`.
_PACED_DRAW_OPERATIONS = {"place_part", "place_symbol", "wire", "box", "text"}


def _locate_draw_failure(
    error: AdapterError,
    *,
    index: int,
    op: dict[str, Any],
    kind: str,
    sheet: int | None,
    completed: int,
    total: int,
    planned: list[int],
    saved: list[int],
) -> None:
    """Say where a draw stopped and which sheets it had already saved.

    An index alone is not locatable: finding out what operation 269 was meant
    rebuilding the plan in a REPL and counting open_sheet records. And every
    sheet ends in a save, so the sheets saved before the failure are on disk:
    `--sheets` with the remaining ones resumes the draw instead of redrawing it
    all. What the raiser already put in the details is kept.
    """
    remaining = [number for number in planned if number not in saved]
    facts = {
        "index": index,
        "operation": kind,
        "sheet": sheet,
        "refdes": str(op.get("refdes", "")),
        "net": str(op.get("label") or ""),
        "symbol": str(op.get("symbol") or ""),
        "completed": completed,
        "total": total,
        "sheets_drawn": sorted(saved),
        "sheets_remaining": remaining,
    }
    for key, value in facts.items():
        error.details.setdefault(key, value)
    if saved and remaining:
        # appended, not instead: the raiser's own hint ("retry the draw") names
        # the cause, and on its own would redraw the saved sheets too
        resume = (
            f"sheets {','.join(str(n) for n in sorted(saved))} are saved, so once the cause "
            f"is fixed --sheets {','.join(str(n) for n in remaining)} draws the rest"
        )
        hint = str(error.details.get("hint") or "")
        error.details["hint"] = f"{hint}; {resume}" if hint else resume
    untrusted = error.details.setdefault("_untrusted", [])
    for key in ("refdes", "net", "symbol"):
        if key not in untrusted:
            untrusted.append(key)


def _draw(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Execute a drawing plan from `schematic_layout` on the project's sheets.

    Operations: `open_sheet`, `wipe_sheet`, `place_part`, `place_symbol`, `wire`
    (an orthogonal polyline whose ends may attach to pins and whose first segment
    may carry a label), `box`, `text`, `save`. Symbol files are written into the
    project's central-library partition first. With `verify`, the project is
    reopened afterwards and the netlist read back is compared with the plan.
    """
    project = params.get("project") or params.get("path")
    if not project:
        raise AdapterError("E_USAGE", "draw requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    if not project_path.is_file():
        raise AdapterError("E_NOT_FOUND", "project file was not found", {"path": str(project_path)})
    ops = params.get("ops")
    if not isinstance(ops, list) or not ops:
        raise AdapterError("E_USAGE", "draw requires a non-empty list of operations")
    library = str(params.get("library") or DEFAULT_PARTITION)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", library):
        raise AdapterError(
            "E_VALIDATION",
            "the symbol partition must be a plain identifier",
            {"partition": library[:80], "_untrusted": ["partition"]},
        )
    symbols = params.get("symbols") or {}
    if not isinstance(symbols, dict):
        raise AdapterError("E_USAGE", "draw symbols must map symbol names to file text")
    written: list[str] = []
    if symbols:
        target = _symbol_library_root(project_path) / library / "sym"
        if not str(target).isascii():
            raise AdapterError(
                "E_VALIDATION",
                "Designer cannot load new symbol files from a folder whose path has "
                "non-ASCII characters; keep the project and its library on an ASCII path",
                {"path": str(target)},
            )
        try:
            target.mkdir(parents=True, exist_ok=True)
            folder = target.resolve()
            for name, text in symbols.items():
                path = (target / f"{name}.1").resolve()
                if path.parent != folder:
                    # the planner refuses such a name; this holds for any caller
                    raise AdapterError(
                        "E_VALIDATION",
                        "a symbol name must be a plain file name",
                        {"symbol": str(name)[:80], "_untrusted": ["symbol"]},
                    )
                path.write_text(str(text), encoding="utf-8")
                written.append(str(name))
        except OSError as exc:
            raise AdapterError("E_IO", f"cannot write symbol files: {exc}") from exc
    app = _viewdraw_application(client, attach_only=True)
    # Designer searches the symbol partitions and parts databases its .prj lists: the
    # design's own, and every partition a library part is placed from. Add them with
    # the project closed, or the edit is lost when Designer writes the file back.
    needed: list[tuple[str, str]] = [("Symbols", f"SymbolLibs\\{library}")]
    others = sorted(
        {
            str(op.get("library"))
            for op in ops
            if isinstance(op, dict) and op.get("op") == "place_part" and op.get("library")
        }
        - {library}
    )
    if others:
        library_root = _symbol_library_root(project_path).parent
        for partition in others:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", partition):
                continue
            needed.append(("Symbols", f"SymbolLibs\\{partition}"))
            if (library_root / "PartsDBLibs" / f"{partition}.pdb").is_file():
                needed.append(("PDBs", f"PartsDBLibs\\{partition}.pdb"))
    missing = [(name, entry) for name, entry in needed if not _prj_lists(project_path, entry)]
    if missing:
        _close_if_open(app, project_path)
        for name, entry in missing:
            _ensure_prj_list(project_path, name, entry)
    _ensure_project(app, project_path)
    wire_kind = _constants(client, ["VD_WIRE"])["VD_WIRE"]
    components: dict[str, Any] = {}
    counts: dict[str, int] = {}
    warnings: list[dict[str, Any]] = []
    current_sheet: int | None = None
    planned = [
        int(op["number"]) for op in ops if isinstance(op, dict) and op.get("op") == "open_sheet"
    ]
    saved: list[int] = []
    pace = float(params.get("pace") or 0.0)
    # Designer can stop mid-draw on a modal dialog -- a missing parts database
    # raises one -- and the call that hit it does not return until someone
    # clicks. Watch for them here so a hang is attributable: the known ones are
    # answered, and anything else is recorded for the error envelope.
    last_report = time.monotonic()
    with _designer_prompt_context() as prompts:
        for index, op in enumerate(ops):
            if not isinstance(op, dict):
                raise AdapterError(
                    "E_CHANGESET_INVALID", "each draw operation must be an object", {"index": index}
                )
            kind = str(op.get("op", ""))
            try:
                if kind == "open_sheet":
                    _open_sheet(app, int(op["number"]))
                    current_sheet = int(op["number"])
                    counts[kind] = counts.get(kind, 0) + 1
                    continue
                _ensure_sheet(app, current_sheet)
                if kind == "wipe_sheet":
                    app.ExecuteCommandByID(_SELECT_ALL_COMMAND)
                    time.sleep(0.5)
                    app.ActiveView.Block.DeleteSelected(False)
                    _settle(0.5)
                elif kind == "set_sheet":
                    # Size first, border second: the border change alone leaves the page
                    # size untouched, and a size set straight after a border change was
                    # dropped on some sheets.
                    block = app.ActiveView.Block
                    if op.get("size") is not None:
                        block.SheetSize = int(op["size"])
                        _settle(1.0)
                        _ensure_sheet(app, current_sheet)
                    border = str(op.get("border") or "")
                    if border:
                        app.ActiveView.Block.ChangeBorder(border)
                        _settle(1.0)
                        _ensure_sheet(app, current_sheet)
                    if op.get("size") is not None:
                        wanted = int(op["size"])
                        for _attempt in range(2):
                            if sheet_size_taken(int(app.ActiveView.Block.SheetSize), wanted):
                                break
                            app.ActiveView.Block.SheetSize = wanted
                            _settle(1.0)
                            _ensure_sheet(app, current_sheet)
                        if not sheet_size_taken(int(app.ActiveView.Block.SheetSize), wanted):
                            warnings.append(
                                {
                                    "index": index,
                                    "operation": kind,
                                    "message": "sheet size did not take",
                                    "sheet_size": int(app.ActiveView.Block.SheetSize),
                                }
                            )
                elif kind == "place_part":
                    block = app.ActiveView.Block
                    component = block.AddPartInstance(
                        str(op.get("library") or library),
                        str(op.get("part", "")),
                        str(op["symbol"]),
                        int(op["x"]),
                        int(op["y"]),
                    )
                    orientation = int(op.get("orientation", 0))
                    if orientation:
                        component.Orientation = orientation
                    component.Refdes = str(op["refdes"])
                    components[str(op["refdes"])] = component
                    wanted = {
                        str(a.get("name")): a for a in op.get("attributes") or [] if a.get("name")
                    }
                    if wanted:
                        for attribute in _items(_com_member(component, "Attributes")):
                            spec = wanted.get(str(_value(attribute, "Name", default="")))
                            if spec is None:
                                continue
                            if spec.get("orientation") is not None:
                                attribute.Orientation = int(spec["orientation"])
                            # Which corner of the text sits at the location. Set it
                            # before the location: a symbol's refdes arrives
                            # right-anchored, so the planned x would otherwise be
                            # where the text ends rather than where it starts.
                            if spec.get("origin") is not None:
                                attribute.Origin = int(spec["origin"])
                            if spec.get("x") is not None and spec.get("y") is not None:
                                attribute.SetLocation(int(spec["x"]), int(spec["y"]))
                elif kind == "place_symbol":
                    block = app.ActiveView.Block
                    instance = block.AddSymbolInstance(
                        str(op.get("library") or library),
                        str(op["symbol"]),
                        int(op["x"]),
                        int(op["y"]),
                    )
                    orientation = int(op.get("orientation", 0))
                    if orientation:
                        instance.Orientation = orientation
                elif kind == "wire":
                    block = app.ActiveView.Block
                    points = [(int(p[0]), int(p[1])) for p in op.get("points", [])]
                    if len(points) < 2:
                        raise AdapterError(
                            "E_CHANGESET_INVALID",
                            "a wire needs at least two points",
                            {"index": index},
                        )
                    start = _draw_pin(app, components, op.get("start"))
                    end = _draw_pin(app, components, op.get("end"))
                    net = None
                    last = len(points) - 2
                    for segment_index, ((ax, ay), (bx, by)) in enumerate(
                        zip(points, points[1:], strict=False)
                    ):
                        pin_a = start if segment_index == 0 else None
                        pin_b = end if segment_index == last else None
                        created = block.AddNet(ax, ay, bx, by, pin_a, pin_b, wire_kind)
                        net = net if net is not None else created
                    label = op.get("label")
                    if label and net is not None:
                        segment = next(iter(_items(_com_member(net, "GetSegments"))), None)
                        if segment is None:
                            raise AdapterError(
                                "E_CONFLICT", "the wire has no segment to label", {"index": index}
                            )
                        net.AddLabel(segment, str(label["net"]), int(label["x"]), int(label["y"]))
                elif kind == "box":
                    app.ActiveView.Block.AddBox(
                        int(op["x1"]), int(op["y1"]), int(op["x2"]), int(op["y2"])
                    )
                elif kind == "text":
                    text = app.ActiveView.Block.AddText(str(op["text"]), int(op["x"]), int(op["y"]))
                    size = int(op.get("size", 0) or 0)
                    if size:
                        text.Size = size
                elif kind == "save":
                    _designer_save_document(app)
                    if current_sheet is not None and current_sheet not in saved:
                        saved.append(current_sheet)
                else:
                    raise AdapterError(
                        "E_CHANGESET_INVALID", f"unknown draw operation {kind!r}", {"index": index}
                    )
            except AdapterError as error:
                _locate_draw_failure(
                    error,
                    index=index,
                    op=op,
                    kind=kind,
                    sheet=current_sheet,
                    completed=sum(counts.values()),
                    total=len(ops),
                    planned=planned,
                    saved=saved,
                )
                raise
            except Exception as exc:
                error = _com_error(exc, f"draw_{kind or 'operation'}")
                _locate_draw_failure(
                    error,
                    index=index,
                    op=op,
                    kind=kind,
                    sheet=current_sheet,
                    completed=sum(counts.values()),
                    total=len(ops),
                    planned=planned,
                    saved=saved,
                )
                # A dialog Designer put up is the likeliest reason a call failed or
                # sat there, and it is not otherwise visible from the envelope: the
                # reported action and index name the operation that was interrupted,
                # not the question that interrupted it.
                dialogs = prompts.blocking_dialogs() if prompts is not None else None
                if dialogs:
                    error.details["blocking_dialogs"] = dialogs
                    error.details["_untrusted"].append("blocking_dialogs")
                raise error from exc
            counts[kind] = counts.get(kind, 0) + 1
            if pace and kind in _PACED_DRAW_OPERATIONS:
                time.sleep(pace)
            # Progress on the side channel (CLI-SPEC §4). A few hundred
            # operations run for minutes, and printing nothing until the call
            # returns leaves both a slow draw and a partial one unreadable.
            # `--quiet` suppresses this upstream, by capturing our stderr.
            now = time.monotonic()
            if now - last_report >= 2.0:
                last_report = now
                print(
                    f"draw: {sum(counts.values())}/{len(ops)} operations, sheet {current_sheet}",
                    file=sys.stderr,
                    flush=True,
                )
    _warn_when_the_library_is_missing(warnings, project_path, library, ops)
    # A draw wipes and redraws the sheets its design names. Any other sheet the
    # project has keeps whatever was on it -- a cloned template's content, which
    # would otherwise ship with the deliverable unremarked. A sheet the design
    # lists but this draw left alone (`--sheets`) is kept, not a leftover.
    drawn = set(planned)
    design_sheets = {int(number) for number in params.get("design_sheets") or []} | drawn
    try:
        untouched = sorted(number for number in _sheet_numbers(app) if number not in design_sheets)
    except AdapterError:
        untouched = []
    result: dict[str, Any] = {
        "applied": len(ops),
        "operations": counts,
        "symbols_written": written,
        "sheets_drawn": sorted(drawn),
        "sheets_not_drawn": untouched,
        "warnings": warnings,
        "project": str(project_path),
        "_untrusted": ["symbols_written", "warnings", "project", "netlist"],
    }
    kept = sorted(design_sheets - drawn)
    if kept:
        result["sheets_kept"] = kept
    verify = params.get("verify")
    if isinstance(verify, dict) and verify:
        if bool(params.get("reopen", True)):
            _reopen_project(app, project_path)
        result["netlist"] = _compare_netlist(
            _designer_snapshot(app, {}), verify, _designer_pin_identities(app)
        )
        result["persistence_verified"] = bool(
            params.get("reopen", True) and set(drawn) <= set(saved) and result["netlist"]["matches"]
        )
        result["verification_source"] = "native_getters_after_sheet_save_close_open"
    return result


_FULL_VERIFICATION_COMMAND = 34155
_QUICK_VERIFICATION_COMMAND = 46461
_VERIFY_SEVERITY = {"error": "high", "warning": "medium", "note": "low"}


def _parse_vdrc(text: str) -> list[dict[str, Any]]:
    """Designer's `LogFiles/vdrc.log`: SEVERITY / GROUP headers, then one finding per line.

    SEVERITY: Warning
      GROUP: Interconnectivity : 8 Warning(s)
        drc-BI-GROUND - [flatnet : GND, component: BT201($2I1014), pin: $1P11] BI pin ...
    """
    findings: list[dict[str, Any]] = []
    severity = "info"
    group = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("SEVERITY:"):
            severity = _VERIFY_SEVERITY.get(line.partition(":")[2].strip().lower(), "info")
            continue
        if line.startswith("GROUP:"):
            group = line.partition(":")[2].split(":")[0].strip()
            continue
        match = re.match(r"^(\S+)\s+-\s+\[(.*?)\]\s*(.*)$", line)
        if not match:
            continue
        rule, fields, message = match.groups()
        net = refdes = pin = None
        for field in fields.split(","):
            key, _, value = field.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "flatnet":
                net = value
            elif key == "component":
                refdes = value.split("(")[0].strip()
            elif key == "pin":
                pin = value
        findings.append(
            {
                "severity": severity,
                "refdes": refdes,
                "net": net,
                "source": f"xpedition/verify:{rule}",
                "finding": message.strip() or rule,
                "evidence": [item for item in (group, fields, pin) if item],
                "suggestion": "check the rule in Verify.ini or fix the connection",
                "confidence": 1.0,
            }
        )
    return findings


def _parse_grc(text: str) -> list[dict[str, Any]]:
    """Designer's `LogFiles/grc.log`: report / check headings with issue lines under them."""
    findings: list[dict[str, Any]] = []
    report = check = ""
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0 and line.endswith("report"):
            report = line
            continue
        if indent == 0:
            continue
        if indent <= 4:
            check = line
            continue
        if line.lower().startswith("no issues"):
            continue
        findings.append(
            {
                "severity": "low",
                "refdes": None,
                "net": None,
                "source": f"xpedition/grc:{check or report}",
                "finding": line,
                "evidence": [item for item in (report, check) if item],
                "suggestion": "fix the graphical issue on the sheet",
                "confidence": 1.0,
            }
        )
    return findings


def _verify(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Run Designer's own schematic verification and return its findings.

    `RunDesignIntegrityChecks` is a database integrity test; the electrical and
    graphical rules live behind the `Full Verification` (34155) and `Quick
    Verification` (46461) commands, which write `LogFiles/vdrc.log` and
    `LogFiles/grc.log` under the project.
    """
    project = params.get("project") or params.get("path")
    if not project:
        raise AdapterError("E_USAGE", "verify requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    if not project_path.is_file():
        raise AdapterError("E_NOT_FOUND", "project file was not found", {"path": str(project_path)})
    scheme = str(params.get("scheme") or "full").lower()
    if scheme not in ("full", "quick"):
        raise AdapterError("E_VALIDATION", "scheme must be full or quick", {"scheme": scheme})
    command = _FULL_VERIFICATION_COMMAND if scheme == "full" else _QUICK_VERIFICATION_COMMAND
    app = _viewdraw_application(client, attach_only=True)
    _ensure_project(app, project_path)
    logs = project_path.parent / "LogFiles"
    vdrc = logs / "vdrc.log"
    grc = logs / "grc.log"
    before = {path: (path.stat().st_mtime if path.is_file() else 0.0) for path in (vdrc, grc)}
    try:
        app.ExecuteCommandByID(command)
    except Exception as exc:
        raise _com_error(exc, "run_verification") from exc
    deadline = time.time() + float(params.get("timeout", 120.0))
    while time.time() < deadline:
        time.sleep(1.0)
        _settle(0.0)
        if vdrc.is_file() and vdrc.stat().st_mtime > before[vdrc]:
            break
    else:
        raise AdapterError(
            "E_TIMEOUT",
            "verification did not write LogFiles/vdrc.log",
            {"path": str(vdrc), "hint": "a dialog may be waiting in Designer"},
        )
    time.sleep(1.0)
    findings = _parse_vdrc(vdrc.read_text(encoding="utf-8", errors="replace"))
    if grc.is_file() and grc.stat().st_mtime > before[grc]:
        findings += _parse_grc(grc.read_text(encoding="utf-8", errors="replace"))
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return {
        "scheme": scheme,
        "findings": findings,
        "summary": {"total": len(findings), "by_severity": counts},
        "logs": [str(vdrc), str(grc)],
        "_untrusted": ["findings", "logs"],
    }


_FIT_ALL_COMMAND = 32775


def _show(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Raise the bound Designer without shrinking it, then activate and fit the sheet.

    With `output`, the window is also captured to a PNG so an agent can look at
    what it drew without a PDF (which garbles Chinese text).
    """
    project = params.get("project") or params.get("path")
    if not project:
        raise AdapterError("E_USAGE", "show requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    if not project_path.is_file():
        raise AdapterError("E_NOT_FOUND", "project file was not found", {"path": str(project_path)})
    try:
        sheet = int(params.get("sheet") or 1)
    except (TypeError, ValueError) as exc:
        raise AdapterError("E_VALIDATION", "sheet must be a positive integer") from exc
    if sheet < 1:
        raise AdapterError("E_VALIDATION", "sheet must be a positive integer", {"sheet": sheet})
    output = params.get("output")
    output_path = Path(str(output)).expanduser().resolve() if output else None
    if output_path is not None:
        if output_path.suffix.lower() != ".png":
            raise AdapterError(
                "E_VALIDATION", "show output must end in .png", {"path": str(output_path)}
            )
        if output_path.exists():
            raise AdapterError(
                "E_CONFLICT", "output file already exists", {"path": str(output_path)}
            )
    from . import win_dialogs

    view_attempted = False

    def require_unlocked() -> None:
        if win_dialogs.desktop_locked():
            raise AdapterError(
                "E_HUMAN_REQUIRED",
                "Unlock the interactive desktop before schematic view",
                {"write_attempted": view_attempted},
            )

    require_unlocked()
    app = _viewdraw_application(client, attach_only=True)
    expected = os.environ.get("XPEDITION_EXPECTED_PID")

    def observe_window(*, foreground: bool = False, active: bool = False) -> tuple[dict, dict]:
        require_unlocked()
        identity = application_identity(app, "schematic")
        observed = identity.get("document") or {}
        native_hwnd = (identity.get("pid_source") or {}).get("hwnd")
        observed_path = observed.get("path")
        if (
            not expected
            or str(identity.get("pid")) != expected
            or observed.get("open") is not True
            or not observed_path
            or Path(str(observed_path)).resolve() != project_path
            or not native_hwnd
        ):
            raise AdapterError(
                "E_CONFLICT",
                "Schematic view requires the bound application and actual project",
                {"application": identity, "requested_project": str(project_path)},
            )
        window = win_dialogs.main_window("viewdraw.exe")
        if not window or not win_dialogs.same_root_window(window["hwnd"], native_hwnd):
            raise AdapterError(
                "E_HUMAN_REQUIRED", "The visible window is not the bound Designer sheet window"
            )
        if foreground and (
            not win_dialogs.foreground_matches(int(window["hwnd"]))
            or not all(
                type(window.get(key)) is int and window[key] > 0 for key in ("width", "height")
            )
        ):
            raise AdapterError(
                "E_HUMAN_REQUIRED", "The bound Designer window or its final geometry is unavailable"
            )
        if active and _active_sheet(app) != sheet:
            raise AdapterError(
                "E_CONFLICT",
                "The active Designer sheet changed before capture",
                {"requested_sheet": sheet, "active_sheet": _active_sheet(app)},
            )
        return identity, window

    # Refuse a foreign project/window before any project or sheet activation.
    _identity, window = observe_window()
    _ensure_project(app, project_path)
    known = _sheet_numbers(app)
    if sheet not in known:
        raise AdapterError(
            "E_NOT_FOUND", f"sheet {sheet} does not exist", {"sheets": sorted(known)}
        )
    require_unlocked()
    view_attempted = True
    foreground_request_return = bool(
        win_dialogs.bring_to_front(int(window["hwnd"]), preserve_state=True)
    )
    time.sleep(1.0)
    # FitAll must use the raised/restored window's actual dimensions, not the
    # minimized geometry or the pre-restoration viewport.
    observe_window(foreground=True)
    _activate_sheet(app, sheet)
    if _active_sheet(app) != sheet:
        raise AdapterError(
            "E_CONFLICT",
            f"sheet {sheet} could not be activated",
            {"active_sheet": _active_sheet(app)},
        )
    observe_window(foreground=True, active=True)
    try:
        app.ExecuteCommandByID(_FIT_ALL_COMMAND)
    except Exception as exc:
        raise _com_error(exc, "fit_all") from exc
    time.sleep(0.8)
    result: dict[str, Any] = {
        "sheet": sheet,
        "sheet_size": None,
        "foreground": True,
        "foreground_request_return": foreground_request_return,
        "window": None,
        "screenshot": None,
        "_untrusted": ["window", "screenshot", "capture_context"],
    }
    try:
        result["sheet_size"] = int(app.ActiveView.Block.SheetSize)
    except Exception:
        pass
    try:
        identity, window = observe_window(foreground=True, active=True)
        result["window"] = {k: window[k] for k in ("title", "left", "top", "width", "height")}

        def capture_context(identity: dict, window: dict) -> dict:
            return {
                "project": str(identity["document"]["path"]),
                "sheet": _active_sheet(app),
                "pid": identity["pid"],
                "executable": identity.get("public_fields", {}).get("FullName"),
                "application_hwnd": identity["pid_source"]["hwnd"],
                "active_view_hwnd": identity.get("public_fields", {}).get("ActiveViewHandle"),
                "window_hwnd": int(window["hwnd"]),
                "same_root_verified": True,
                "foreground_verified": True,
            }

        result["capture_context"] = capture_context(identity, window)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            result["screenshot"] = win_dialogs.capture_window(int(window["hwnd"]), str(output_path))
            # The picture bytes alone cannot establish which project and sheet
            # remained active during capture. Read their native identities again.
            identity_after, window_after = observe_window(foreground=True, active=True)
            if capture_context(identity_after, window_after) != result["capture_context"]:
                raise AdapterError("E_CONFLICT", "The Designer capture context changed")
    except OSError as exc:
        details = getattr(exc, "details", None)
        raise AdapterError(
            "E_IO",
            f"cannot capture the window: {exc}",
            dict(details) if isinstance(details, dict) else {},
        ) from exc
    return result


def _dispatch_designer_host_bootstrap(params):
    """Retained legacy dispatch, unreachable while native execution is disabled.

    The helper's public observe surface also refuses execution. The new offline
    execution owner is not connected here and cannot qualify this CAD route.
    """
    _require_designer_host_execution()
    import importlib

    ownership = params.get("_host_bootstrap_ownership")
    generations = ownership.get("generations") if isinstance(ownership, dict) else None
    applications = (
        [row for row in generations if isinstance(row, dict) and row.get("role") == "application"]
        if isinstance(generations, list)
        else []
    )
    if (
        params.get("domain") != "schematic"
        or not isinstance(ownership, dict)
        or ownership.get("status") != "verified"
        or ownership.get("domain") != "schematic"
        or ownership.get("authority_granted") is not False
        or ownership.get("project_scope_verified") is not True
        or not isinstance(generations, list)
        or not 1 <= len(generations) <= 2
        or any(not isinstance(row, dict) for row in generations)
        or len(applications) != 1
        or type(applications[0].get("pid")) is not int
        or applications[0]["pid"] <= 0
    ):
        raise AdapterError(
            "E_CONFIG",
            "The fixed host experiment requires its original scoped SDK target",
            {"write_attempted": False},
        )
    bound = [
        name
        for name in (
            "XPEDITION_EXPECTED_PID",
            "XPEDITION_EXPECTED_DOMAIN",
            "XPEDITION_EXPECTED_SCHEMATIC_PID",
        )
        if os.environ.get(name)
    ]
    if bound or os.environ.get("XPEDITION_OBSERVE_ONLY") == "1":
        raise AdapterError(
            "E_CONFLICT",
            "The fixed host bootstrap refuses inherited bindings or read mode",
            {"write_attempted": False, "present_bound_environment_fields": bound},
        )
    try:
        pythoncom, client = _import_com()
        # Frozen hooks may already initialize the main thread as an STA.
        # Preload bindings here, before our explicitly initialized MTA worker.
        importlib.import_module("comtypes")
        importlib.import_module("comtypes.client")
    except Exception as error:
        raise AdapterError(
            "E_BACKEND_UNAVAILABLE",
            "The fixed host-bootstrap COM bindings are unavailable",
            {"write_attempted": False, "cause_type": type(error).__name__},
        ) from error
    from .audit import config_dir
    from .designer_host_bootstrap import observe

    progid = application_progids("schematic")[0]
    result = observe(
        ownership,
        config_dir(),
        expected_pid=applications[0]["pid"],
        candidate_factory=lambda: client.GetActiveObject(progid),
        com_module=pythoncom,
    )
    if not isinstance(result, dict):
        raise AdapterError(
            "E_NATIVE_FAILURE",
            "The fixed host-bootstrap worker did not return a diagnostic",
            {
                "write_attempted": True,
                "cause_type": "ResultGap",
                "original_session_metadata_preserved": True,
            },
        )
    return result


def _require_designer_host_execution():
    from .backends.native_xpedition import require_designer_host_execution
    from .errors import CLIError

    try:
        require_designer_host_execution()
    except CLIError as error:
        raise AdapterError(error.code, error.message, error.details) from error


def _require_dedicated_designer():
    from .designer_startup import require_dedicated_designer
    from .errors import CLIError

    try:
        require_dedicated_designer()
    except CLIError as error:
        raise AdapterError(error.code, error.message, error.details) from error


def dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    from .designer_owner_dispatch import DispatchError, check_adapter_dispatch

    try:
        owner_context = check_adapter_dispatch(method)
    except DispatchError as error:
        raise AdapterError(
            "E_CONFLICT", "Dedicated owner dispatch is invalid", {"reason": str(error)}
        ) from None
    if method == "open" and _domain(params) == "schematic" and not owner_context:
        _require_dedicated_designer()
    if method in {"start", "attach"}:
        explicit = str(params.get("domain") or params.get("kind") or "").lower()
        if explicit not in {"schematic", "designer", "pcb", "layout"}:
            raise AdapterError(
                "E_USAGE",
                "Native session start/attach requires an explicit domain or kind",
                {"write_attempted": False, "native_request_dispatched": False},
            )
        if explicit in {"schematic", "designer"}:
            _require_dedicated_designer()
    if method == "designer_host_bootstrap":
        _require_designer_host_execution()
    if method == "identity" and params.get("_designer_identity_probe") == "application-proxy":
        bound_fields = [
            name
            for name in (
                "XPEDITION_EXPECTED_PID",
                "XPEDITION_EXPECTED_DOMAIN",
                "XPEDITION_EXPECTED_SCHEMATIC_PID",
            )
            if os.environ.get(name)
        ]
        if bound_fields:
            raise AdapterError(
                "E_CONFLICT",
                "The explicit Designer proxy diagnostic refuses inherited "
                "instance-binding environment; use the public clean-environment entry",
                {
                    "write_attempted": False,
                    "native_query_only": True,
                    "present_bound_environment_fields": bound_fields,
                },
            )
    expected_build = os.environ.get("XPEDITION_EXPECTED_ADAPTER_BUILD")
    if expected_build and adapter_identity().get("build_sha256") != expected_build:
        raise AdapterError(
            "E_CONFLICT",
            "The native adapter build changed before dispatch.",
            {"expected_build_sha256": expected_build, "observed_adapter": adapter_identity()},
        )
    if method == "net_rules":
        return _net_rules(params, None)
    if method == "designer_host_bootstrap":
        return _dispatch_designer_host_bootstrap(params)
    if owner_context and method == "read_project":
        from .designer_owner_dispatch import dispatch_read_project

        return dispatch_read_project()
    if owner_context and method in {
        "release_project_preflight",
        "release_project",
        "reopen_project",
    }:
        from .designer_owner_dispatch import dispatch_project_lifecycle

        return dispatch_project_lifecycle(method, params)
    pythoncom, client = _import_com()
    pythoncom.CoInitialize()
    # before anything binds a running application through a cached wrapper
    if not owner_context and method not in {
        "close_diagnostic",
        "close_diagnostic_state",
        "diagnostic_exit_state",
        "diagnostic_exit_quit",
    }:
        _heal_gen_py_cache()
    previous_observe_only = os.environ.get("XPEDITION_OBSERVE_ONLY")
    try:
        if method in {
            "close_diagnostic",
            "close_diagnostic_state",
            "diagnostic_exit_state",
            "diagnostic_exit_quit",
        }:
            from .errors import CLIError
            from .native_close_diagnostic import _exit_checkpoint, execute, exit_empty, read_state

            try:
                return (
                    read_state(client, params, checkpoint=_exit_checkpoint(params))[0]
                    if method == "diagnostic_exit_state"
                    else read_state(client, params)[0]
                    if method == "close_diagnostic_state"
                    else exit_empty(client, params)
                    if method == "diagnostic_exit_quit"
                    else execute(client, params)
                )
            except CLIError as error:
                raise AdapterError(error.code, error.message, error.details) from error
        if method == "identity":
            domain = _domain(params)
            proxy_probe = params.get("_designer_identity_probe")
            if proxy_probe is not None and (
                domain != "schematic" or proxy_probe != "application-proxy"
            ):
                raise AdapterError("E_USAGE", "Unsupported explicit Designer identity diagnostic")
            app = _viewdraw_active(client) if domain == "schematic" else _active_object(client)
            if proxy_probe == "application-proxy":
                identity = application_identity(
                    app,
                    domain,
                    nativeom_ownership=params.get("_nativeom_ownership"),
                    designer_identity_probe=proxy_probe,
                )
            elif domain == "schematic" and "_nativeom_ownership" in params:
                identity = application_identity(
                    app, domain, nativeom_ownership=params["_nativeom_ownership"]
                )
            else:
                identity = application_identity(app, domain)
            try:
                if domain == "schematic":
                    current = _com_member(_com_member(app, "GetProjectData"), "GetProjectFilePath")
                else:
                    current = _value(
                        _value(app, "ActiveDocument", default=None), "FullName", default=None
                    )
                identity["active_project"] = str(current) if current else None
                identity["active_project_source"] = (
                    "GetProjectData.GetProjectFilePath"
                    if domain == "schematic"
                    else "ActiveDocument.FullName"
                )
            except Exception:
                identity["active_project"] = None
                identity["active_project_source"] = "unavailable"
            return {
                "adapter": adapter_identity(),
                "application": identity,
                "automation_progid": _BOUND_PROGIDS.get(domain),
            }
        if method == "health":
            sdd_home = _configure_environment()
            registered = _com_registered()
            designer_registered = _viewdraw_registered()
            requested_domain = params.get("domain")
            if requested_domain not in (None, "pcb", "schematic"):
                raise AdapterError("E_USAGE", "health domain must be pcb or schematic")
            probe_pcb = requested_domain in (None, "pcb")
            probe_schematic = requested_domain in (None, "schematic")
            running = False if probe_pcb else None
            designer_running = False if probe_schematic else None
            attachment_failures = {}
            if registered and probe_pcb:
                # a probe only looks: the dialog suppression and single-threaded mode a
                # command needs are set when that command attaches, not by `doctor`
                try:
                    _active_object(client)
                    running = True
                except AdapterError as error:
                    running = False
                    attachment_failures["pcb"] = {"code": error.code, "details": error.details}
            if designer_registered and probe_schematic:
                try:
                    _viewdraw_active(client)
                    designer_running = True
                except AdapterError as error:
                    designer_running = False
                    attachment_failures["schematic"] = {
                        "code": error.code,
                        "details": error.details,
                    }
            return {
                "ready": bool(sdd_home and (registered or designer_registered)),
                "application_running": running,
                "designer_application_running": designer_running,
                "attachment_failures": attachment_failures,
                "sdd_home": str(sdd_home) if sdd_home else None,
                "automation_progid": application_progids("pcb")[0],
                "registered_progids": {
                    "pcb": list(application_progids("pcb")),
                    "schematic": list(application_progids("schematic")),
                },
                "adapter_identity": adapter_identity(),
                "com_registered": registered,
                "designer_com_registered": designer_registered,
                "reason": (
                    None
                    if sdd_home and (registered or designer_registered)
                    else "Xpedition installation or COM registration is incomplete"
                ),
            }
        if method == "start":
            from .native_process import ProcessHandle, capture_startup_seed

            domain = _domain(params)
            pending = processes(domain)
            if pending:
                raise AdapterError(
                    "E_HUMAN_REQUIRED",
                    "A native process is already running; no duplicate was launched.",
                    {
                        "processes": pending,
                        "action": "Complete or inspect the existing startup/licensing window.",
                        "resume": "session inspect",
                    },
                )
            executable, working_directory = _native_executable(domain)
            try:
                process = subprocess.Popen(
                    [str(executable)],
                    cwd=str(working_directory),
                    env=os.environ.copy(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                error = _com_error(exc, "start_xpedition")
                error.details["write_attempted"] = False
                raise error from exc
            launched = {
                "started": True,
                "domain": domain,
                "pid": process.pid,
                "launcher_pid": process.pid,
                "executable": str(executable),
                "automation_ready": False,
                "automation_progid": _BOUND_PROGIDS.get(domain),
            }
            try:
                # Pin immediately after our own Popen, before any COM wait.
                # The launcher and the actual wg/wv process are distinct roles.
                with ProcessHandle(domain, process.pid) as launcher:
                    launcher.require_spawned(process)
                    if Path(launcher.identity["executable"]).resolve() != executable.resolve():
                        raise ValueError("The spawned launcher executable differs")
                    launched["launcher_identity"] = dict(launcher.identity)
                    launched["launcher_generation_source"] = "original_Popen_handle/GetProcessTimes"
                    launched["startup_seed"] = capture_startup_seed(domain, launcher)
                    launched["automation_ready"] = _wait_for_native_ready(
                        process, client, domain, float(params.get("startup_wait", 5.0))
                    )
                    launched["startup_seed"] = capture_startup_seed(domain, launcher)
                return launched
            except Exception as error:
                raise AdapterError(
                    getattr(error, "code", "E_VERIFY_INCOMPLETE"),
                    "The launcher was dispatched but actual application ownership is unproved",
                    {
                        "write_attempted": True,
                        "native_result": launched,
                        "cause_type": type(error).__name__,
                        "next_action": "Inspect the original ID and identity; never relaunch",
                    },
                ) from error
        if method == "attach":
            domain = _domain(params)
            app = (
                _viewdraw_application(client, attach_only=True)
                if domain == "schematic"
                else _application(client, attach_only=True)
            )
            return {
                "attached": True,
                "domain": domain,
                "automation_progid": _BOUND_PROGIDS.get(domain),
            }
        if method == "open":
            path = params.get("project") or params.get("path")
            if not path:
                raise AdapterError("E_USAGE", "native open requires project path")
            domain = _domain(params)
            app = (
                _viewdraw_application(client, attach_only=not bool(params.get("start", True)))
                if domain == "schematic"
                else _application(client, attach_only=not bool(params.get("start", True)))
            )
            prompts: list[dict[str, Any]] = []
            if domain == "schematic":
                doc = _open_designer_project(app, {"project": path})
                if owner_context:
                    from .designer_owner_dispatch import _project_context

                    _grant, owner = _project_context(app, path)
                    owner.confirm_project_open()
                    return {
                        "opened": True,
                        "domain": "schematic",
                        "project": owner.project,
                        "canonical_stable": True,
                        "open_project_verified": True,
                        "project_origin": owner.project_origin.safe_binding
                        if hasattr(owner.project_origin, "safe_binding")
                        else copy.deepcopy(owner.project_origin),
                    }
            else:
                doc, prompts = _open_layout_document(app, _layout_board_path(params))
            try:
                app.Visible = bool(params.get("visible", True))
            except Exception:
                pass
            _quiet_gui(app, domain)
            return {
                "opened": True,
                "domain": domain,
                "project": str(_value(doc, "Name", "FullName", "FileName", default=path)),
                "prompts": prompts,
            }
        if method == "snapshot":
            if params.get("observe_only"):
                os.environ["XPEDITION_OBSERVE_ONLY"] = "1"
            domain = _domain(params)
            app = (
                _viewdraw_application(
                    client,
                    attach_only=bool(params.get("observe_only")) or not bool(params.get("project")),
                )
                if domain == "schematic"
                else _application(
                    client,
                    attach_only=bool(params.get("observe_only")) or not bool(params.get("project")),
                )
            )
            if params.get("observe_only") is True and params.get("pid") is not None:
                observed_pid = application_identity(app, domain).get("pid")
                if (
                    type(params["pid"]) is not int
                    or params["pid"] <= 0
                    or observed_pid != params["pid"]
                ):
                    raise AdapterError(
                        "E_CONFLICT",
                        "Read-only snapshot requires the bound existing process",
                        {"write_attempted": False},
                    )
            if domain == "schematic":
                _open_designer_project(app, params)
                return _designer_snapshot(app, params)
            _open_requested_document(app, params)
            return _snapshot(app, client)
        if method == "save":
            domain = _domain(params)
            if domain == "schematic":
                app = _viewdraw_application(client, attach_only=True)
                if app.IsProjectOpened() is not True:
                    raise AdapterError(
                        "E_HUMAN_REQUIRED", "An open Designer project is required to save"
                    )
                _designer_save_all(app)
            else:
                app = _application(client, attach_only=True)
                doc = _licensed_document(app)
                doc.Save()
            return {"saved": True, "domain": domain}
        if method == "close":
            domain = _domain(params)
            app = (
                _viewdraw_application(client, attach_only=True)
                if domain == "schematic"
                else _application(client, attach_only=True, quiet=False)
            )
            doc = _value(app, "ActiveDocument", default=None)
            if domain == "schematic" and bool(params.get("document_only", False)):
                if doc is not None:
                    doc.Close()
            elif doc is not None and bool(params.get("document_only", False)):
                doc.Close(bool(params.get("save", False)))
            else:
                import gc

                from .native_process import EXIT_OBSERVATION_SECONDS, ProcessHandle

                expected = params.get("expected_process_identity")
                wait = float(params.get("exit_wait_seconds", EXIT_OBSERVATION_SECONDS))
                if not math.isfinite(wait) or not 0 <= wait <= EXIT_OBSERVATION_SECONDS:
                    raise AdapterError(
                        "E_VALIDATION",
                        "Exit wait is outside its public bound",
                        {"write_attempted": False},
                    )
                if not isinstance(expected, dict) or str(expected.get("pid")) != str(
                    params.get("pid")
                ):
                    raise AdapterError(
                        "E_CONFLICT",
                        "Normal closure requires the bound owned process generation",
                        {"write_attempted": False},
                    )
                identity = application_identity(app, domain)
                if identity.get("pid") != int(params["pid"]):
                    raise AdapterError(
                        "E_CONFLICT",
                        "The COM instance changed before close",
                        {"write_attempted": False},
                    )
                lifecycle = {"save_count": 0, "close_count": 0, "quit_count": 0}
                try:
                    pinned = ProcessHandle(domain, int(params["pid"]), expected)
                except (OSError, ValueError) as exc:
                    raise AdapterError(
                        "E_CONFLICT",
                        "Owned process identity changed before close",
                        {"write_attempted": False, "cause_type": type(exc).__name__},
                    ) from exc
                try:
                    # A failed ActiveDocument getter is unknown, never empty.
                    if domain == "pcb":
                        doc = app.ActiveDocument
                        save_project = params.get("save_project")
                        if doc is not None and save_project:
                            board_path = _layout_board_path({"project": save_project})
                            _save_close_board(app, board_path, lifecycle=lifecycle)
                        elif doc is not None:
                            raise AdapterError(
                                "E_CONFLICT",
                                "Session-only closure requires an empty Layout document",
                                {"write_attempted": False},
                            )
                        if app.ActiveDocument is not None:
                            raise AdapterError(
                                "E_VERIFY_INCOMPLETE",
                                "Layout document closure was not observed",
                                {"lifecycle": lifecycle},
                            )
                    elif domain == "schematic":
                        if params.get("save_project"):
                            raise AdapterError(
                                "E_UNSUPPORTED",
                                "Saving on session close currently supports Layout only",
                                {"write_attempted": False},
                            )
                        if app.IsProjectOpened() is not False:
                            raise AdapterError(
                                "E_CONFLICT",
                                "Session-only closure requires a closed Designer project",
                                {"write_attempted": False},
                            )
                        if app.ActiveDocument is not None:
                            raise AdapterError(
                                "E_CONFLICT",
                                "Designer still has an active document",
                                {"write_attempted": False},
                            )
                    lifecycle["empty_document_observed"] = True
                    lifecycle.update(quit_dispatched=True, quit_reply_received=False, quit_count=1)
                    lifecycle.update(_quit_and_confirm(client, app, domain, empty_document=True))
                    doc = None
                    app = None
                    gc.collect()
                    started = time.monotonic()
                    observations = 0
                    while True:
                        ended = pinned.exited()
                        observations += 1
                        elapsed = time.monotonic() - started
                        if ended or elapsed >= wait:
                            break
                        time.sleep(min(0.2, wait - elapsed))
                    return {
                        "closed": ended,
                        "domain": domain,
                        "process_identity": expected,
                        "process_absence_observed": ended,
                        "closing_pending": not ended,
                        "lifecycle": lifecycle,
                        "exit_observation": {
                            "source": "original pinned Windows process handle/GetProcessTimes",
                            "target_pid": str(params["pid"]),
                            "observations": observations,
                            "elapsed_seconds": round(elapsed, 3),
                            "budget_seconds": wait,
                            "com_unregistration_is_exit_evidence": False,
                        },
                    }
                except AdapterError as exc:
                    exc.details = {
                        **exc.details,
                        "lifecycle": lifecycle,
                        "process_identity": expected,
                    }
                    raise
                except Exception as exc:
                    raise AdapterError(
                        "E_VERIFY_INCOMPLETE",
                        "Normal closure observation was incomplete; no action was replayed",
                        {
                            "lifecycle": lifecycle,
                            "process_identity": expected,
                            "cause_type": type(exc).__name__,
                        },
                    ) from exc
                finally:
                    doc = None
                    app = None
                    gc.collect()
                    pinned.close()
            return {"closed": True, "domain": domain}
        if method == "apply_changeset":
            operations = params.get("operations")
            if not isinstance(operations, list) or not operations:
                raise AdapterError(
                    "E_CHANGESET_INVALID", "native apply_changeset requires operations"
                )
            domain = _domain(params)
            if domain == "schematic":
                from .schematic_attribute_display import is_display_only_operations

                display_requested = any(
                    isinstance(op, dict) and "display" in op for op in operations
                )
                if display_requested and not is_display_only_operations(operations):
                    raise AdapterError(
                        "E_VALIDATION",
                        "display edits require a separate display-only set_property batch",
                        {"write_attempted": False},
                    )
                app = _viewdraw_application(
                    client, attach_only=display_requested or not bool(params.get("project"))
                )
                _open_designer_project(
                    app, {**params, "observe_only": True} if display_requested else params
                )
                try:
                    design_name = str(app.GetActiveDesign() or "")
                except Exception:
                    design_name = str(params.get("design") or "")
                if display_requested:
                    return _apply_designer_display_changes(app, params, client, design_name)
                return _apply_designer_edit_changes(app, params, client, design_name)
            else:
                app = _application(client, attach_only=not bool(params.get("project")))
                _open_requested_document(app, params)
                doc = _licensed_document(app)
                applied = [
                    _apply_operation(doc, item, client)
                    for item in operations
                    if isinstance(item, dict)
                ]
            if len(applied) != len(operations):
                raise AdapterError("E_CHANGESET_INVALID", "each native operation must be an object")
            if bool(params.get("save", True)) and domain == "pcb":
                try:
                    doc.Save()
                except Exception as exc:
                    raise _com_error(exc, "save_after_changeset") from exc
            saved = False
            if bool(params.get("save", True)):
                saved = True
            result = {"applied": applied, "count": len(applied), "domain": domain, "saved": saved}
            return result
        if method == "package":
            return _package_design(params)
        if method == "export_pdf":
            return _export_pdf(params)
        if method == "draw":
            return _draw(params, client)
        if method == "show":
            return _show(params, client)
        if method == "verify":
            return _verify(params, client)
        if method == "clone_project":
            return _clone_project(params, client)
        if method == "library_import":
            return _library_import(params, client)
        if method == "library_export":
            return _library_export(params, client)
        if method == "release_project":
            return _let_go_of_project(params, client)
        if method == "release_project_preflight":
            return _project_release_preflight(params, client)
        if method == "reopen_project":
            return _take_up_project(params, client)
        if method == "pcb_create":
            return _pcb_create(params, client)
        if method == "forward_annotate":
            return _forward_annotate(params, client)
        if method == "silk_environment_state":
            from .native_silk_environment import observe
            from .silk_qualification import evaluate

            if params.get("observe_only") is not True:
                raise AdapterError("E_VALIDATION", "Silk surroundings are read-only")
            environment = observe(params, client, sys.modules[__name__])
            return {
                "silk_environment": environment,
                "qualification": evaluate(environment, params["text_uids"]),
                "can_apply": False,
                "_untrusted": ["silk_environment", "qualification"],
            }
        if method in {"refresh_cells", "cell_refresh_state"}:
            from . import cell_refresh

            adapter = sys.modules[__name__]
            if method == "cell_refresh_state":
                if params.get("placement_text_witness") is True:
                    from .placement_text_witness import observe as placement_observe

                    return placement_observe(params, client, adapter)
                return cell_refresh.observe(params, client, adapter)
            return cell_refresh.execute(params, client, adapter)
        if method == "arrange_components":
            return _arrange_components(params, client)
        if method == "board_view_state":
            from .board_view import observe

            return observe(params, client, sys.modules[__name__])
        if method == "show_board":
            return _show_board(params, client)
        if method == "board_outline":
            return _board_outline(params, client)
        if method == "outline_state":
            return _outline_state(params, client)
        if method == "mounting_holes":
            return _mounting_holes(params, client)
        if method == "manufacturing_output":
            return _manufacturing_output(params, client)
        if method == "render_board":
            return _render_board(params, client)
        if method == "hand_route":
            return _hand_route(params, client)
        if method == "unroute_nets":
            return _unroute_nets(params, client)
        if method == "placement_batch":
            from .errors import CLIError
            from .native_placement import run as run_placement

            try:
                return run_placement(params, client)
            except CLIError as error:
                raise AdapterError(error.code, error.message, error.details) from error
        if method == "move_component":
            return _move_component(params, client)
        if method == "board_info":
            return _board_info(params, client)
        if method == "board_geometry":
            return _board_geometry(params, client)
        if method == "tidy_labels":
            return _tidy_labels(params, client)
        if method == "plane_pour":
            return _plane_pour(params, client)
        if method == "route_board":
            return _route_board(params, client)
        if method == "routepass_state":
            from .board_routepass import observe_request

            return observe_request(params, client, sys.modules[__name__])
        if method == "batch_drc":
            return _batch_drc(params, client)
        raise AdapterError("E_USAGE", f"unknown native adapter method: {method}")
    finally:
        if owner_context:
            if previous_observe_only is None:
                os.environ.pop("XPEDITION_OBSERVE_ONLY", None)
            else:
                os.environ["XPEDITION_OBSERVE_ONLY"] = previous_observe_only
        _native_call("worker.CoUninitialize", pythoncom.CoUninitialize)


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise AdapterError("E_USAGE", "native adapter request must be a JSON object")
        method = str(request.get("method", ""))
        params = request.get("params")
        if not isinstance(params, dict):
            params = {}
        result = _response(_native_call(f"native.{method}", lambda: dispatch(method, params)))
    except AdapterError as exc:
        result = _response(error=exc)
    except json.JSONDecodeError as exc:
        result = _response(
            error=AdapterError(
                "E_USAGE", "native adapter input is not valid JSON", {"error": str(exc)}
            )
        )
    except Exception as exc:
        result = _response(
            error=AdapterError(
                "E_SERVER",
                "native adapter failed unexpectedly",
                {
                    "type": type(exc).__name__,
                    "error": str(exc),
                    **(
                        {"cleanup": exc.cleanup_diagnostics}
                        if hasattr(exc, "cleanup_diagnostics")
                        else {}
                    ),
                },
            )
        )
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    # Bytes, not text: the parent decodes UTF-8 whatever the console code page is.
    sys.stdout.buffer.write(payload.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
