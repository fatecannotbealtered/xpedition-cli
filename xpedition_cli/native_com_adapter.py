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

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from pathlib import Path, PureWindowsPath
from typing import Any

from .contract_gen import CODES, SCHEMA_VERSION


class AdapterError(Exception):
    """An error that can be represented by the NativeBackend protocol."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


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

        for progid in (
            "MGCPCB.Application",
            "MGCPCB.Application.60",
            "MGCPCB.ExpeditionPCBApplication",
            "MGCPCB.ExpeditionPCBApplication.60",
        ):
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

        for progid in ("Viewdraw.Application", "Viewdraw.Application.60"):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid):
                    return True
            except OSError:
                continue
    except ImportError:
        return False
    return False


def _configure_environment() -> Path | None:
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
    os.environ.setdefault("__COMPAT_LAYER", "WIN7RTM")
    if not os.environ.get("MGLS_LICENSE_FILE"):
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
    if "class not registered" in lower or "-2147221005" in lower:
        return AdapterError(
            "E_BACKEND_UNAVAILABLE",
            "Xpedition COM automation is not registered",
            {
                "action": action,
                "hint": "run the product's official post-install registration as Administrator",
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
                "action": action,
                "reason": (
                    "the registered LocalServer32 binary needs the release environment "
                    "set up by the launcher in common/win64/bin"
                ),
                "hint": "start the product through its launcher, then attach instead of activating",
            },
        )
    if _is_refused_as_already_satisfied(exc):
        return AdapterError(
            "E_CONFLICT",
            "Xpedition refused the request against its current state",
            {
                "action": action,
                "reason": (
                    "the application reports this operation as already satisfied, which is "
                    "what an open project and a stale session look like from here"
                ),
                "hint": "check session status; after a timed-out call, stop and start the session",
            },
        )
    if _is_license_call_missing(exc):
        return AdapterError(
            "E_AUTH",
            "Xpedition requires the automation licensing call before this operation",
            {
                "action": action,
                "reason": "the session did not hold an automation license for this call",
                "hint": "acquire the automation license through the adapter, then retry the task",
            },
        )
    if "license" in lower or "token" in lower:
        return AdapterError(
            "E_AUTH", "Xpedition rejected the automation license", {"action": action}
        )
    return AdapterError("E_SERVER", f"Xpedition COM operation failed: {text}", {"action": action})


def _active_object(client: Any) -> Any:
    causes: list[str] = []
    for progid in (
        "MGCPCB.Application",
        "MGCPCB.Application.60",
        "MGCPCB.ExpeditionPCBApplication",
        "MGCPCB.ExpeditionPCBApplication.60",
    ):
        try:
            return client.GetActiveObject(progid)
        except Exception as exc:
            causes.append(f"{progid}: {type(exc).__name__}: {exc}")
    raise AdapterError(
        "E_NOT_FOUND",
        "a running Xpedition Layout automation session was not found",
        {
            "progids": ["MGCPCB.Application", "MGCPCB.ExpeditionPCBApplication"],
            # "not found" is the usual reason, not the only one; say what failed
            "causes": causes,
            "_untrusted": ["causes"],
            # GetActiveObject only binds a session the user already started, so
            # say which application that is. Layout and Designer are separate
            # products with separate COM classes: a running Designer does not
            # satisfy a `pcb` command, and the reverse is equally true.
            "application": "Xpedition Layout",
            "serves_commands": "pcb *",
            "hint": (
                "start Xpedition Layout, or run: "
                "xpedition-cli session start --backend native_xpedition --kind pcb"
            ),
        },
    )


def _designer_bound(client: Any, app: Any) -> Any:
    """Designer through its makepy wrapper, generated if it is missing.

    The Designer calls here were written against the early-bound wrapper, which
    fills in optional arguments: late-bound, `Documents.Open(design)` fails with
    "parameter not optional". Which of the two `GetActiveObject` returns depends
    only on whether gen_py happens to hold the wrapper -- it did on the machine
    this was built on, and a %TEMP% cleanup or a fresh machine takes it away. So
    the wrapper is generated on first use rather than assumed.
    """
    gencache = getattr(client, "gencache", None)
    if gencache is None:
        return app
    try:
        return gencache.EnsureDispatch(app)
    except Exception:
        return app


def _viewdraw_active(client: Any) -> Any:
    causes: list[str] = []
    for progid in ("Viewdraw.Application", "Viewdraw.Application.60"):
        try:
            return _designer_bound(client, client.GetActiveObject(progid))
        except Exception as exc:
            causes.append(f"{progid}: {type(exc).__name__}: {exc}")
    raise AdapterError(
        "E_NOT_FOUND",
        "a running Xpedition Designer automation session was not found",
        {
            "progids": ["Viewdraw.Application", "Viewdraw.Application.60"],
            "application": "Xpedition Designer (DxDesigner)",
            "serves_commands": "schematic *, agent snapshot",
            "hint": (
                "start Xpedition Designer, or run: "
                "xpedition-cli session start --backend native_xpedition --kind schematic"
            ),
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
        try:
            gencache.EnsureDispatch(app)
        except Exception:
            pass
    if dynamic is not None:
        try:
            return dynamic.Dispatch(app)
        except Exception:
            pass
    return app


def _application(client: Any, attach_only: bool = False) -> Any:
    _configure_environment()
    try:
        if attach_only:
            app = _typed(client, _active_object(client))
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
            for progid in (
                "MGCPCB.ExpeditionPCBApplication",
                "MGCPCB.ExpeditionPCBApplication.60",
            ):
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
    _configure_environment()
    try:
        if attach_only:
            return _viewdraw_active(client)
        try:
            return _viewdraw_active(client)
        except AdapterError as error:
            if error.code != "E_NOT_FOUND":
                raise
        last_error: Exception | None = None
        for progid in ("Viewdraw.Application", "Viewdraw.Application.60"):
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


def _items(collection: Any) -> Iterable[Any]:
    count = int(_value(collection, "Count", default=0) or 0)
    for index in range(1, count + 1):
        try:
            yield collection.Item(index)
        except Exception:
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
    return {
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

    * the schematic changed since the last `library build --package`, so the
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
                "session stop, then session start, and reopen the project"
                if after_sheet_change
                else "run library build --package, then retry"
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


def _designer_component_record(component: Any) -> dict[str, Any]:
    refdes = str(_value(component, "Refdes", "RefDes", default="") or "")
    location = _component_location(component)
    uid = ""
    try:
        uid = str(_com_member(component, "UID") or "")
    except Exception:
        uid = ""
    attributes: dict[str, str] = {}
    try:
        for attribute in _items(_com_member(component, "Attributes")):
            name = str(_value(attribute, "Name", default="") or "")
            if name:
                attributes[name] = str(_value(attribute, "Value", default="") or "")
    except Exception:
        pass
    part_number = attributes.get("Part Number", "")
    pins: list[dict[str, Any]] = []
    try:
        for connection in _items(_com_member(component, "GetConnections")):
            pin = _value(connection, "CompPin", default=None)
            net = _value(connection, "Net", default=None)
            number = _value(pin, "Number", default=None)
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
    except Exception:
        pass
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
        "uid": uid,
        "sheet": _uid_sheet(uid),
        "x": location.get("x"),
        "y": location.get("y"),
        "rotation": _value(component, "Orientation", default=0),
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
    records = [_designer_component_record(item) for item in _items(components_collection)]
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
    sheets: list[dict[str, Any]] = []
    try:
        for sheet in _items(app.SchematicSheetDocuments()):
            sheets.append(
                {
                    "name": str(_value(sheet, "Name", default="")),
                    "full_name": str(_value(sheet, "FullName", default="")),
                }
            )
    except Exception:
        pass
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
    doc, _prompts = _open_layout_document(app, _layout_board_path(params))
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
        for connection in _items(_com_member(component, "GetConnections")):
            pin = _value(connection, "CompPin", default=None)
            if str(_value(pin, "Number", default="")) == str(number):
                return pin
    except Exception as exc:
        raise _com_error(exc, "find_component_pin") from exc
    raise AdapterError(
        "E_NOT_FOUND",
        f"pin {number!r} was not found on component",
        {"number": str(number)},
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
    try:
        segments = _com_member(net, "GetSegments")
        segment = next(iter(_items(segments)), None) if segments is not None else None
        if segment is None:
            raise AdapterError(
                "E_SERVER",
                "schematic net has no segment to carry its label",
                {"net": str(name)},
            )
        x = int(operation.get("x", operation.get("x1", 0)))
        y = int(operation.get("y", operation.get("y1", 0)))
        net.AddLabel(segment, str(name), x, y)
    except AdapterError:
        raise
    except Exception as exc:
        raise _com_error(exc, "label_schematic_net") from exc


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
        # The instance is already on the sheet here. Neither the component nor the
        # block exposes a delete entry point in this API, so a failure past this
        # point cannot be rolled back: name the orphan instead of dropping it
        # silently, so the caller can remove it and re-read the snapshot.
        try:
            component.Refdes = refdes
        except Exception as exc:
            error = _com_error(exc, "set_schematic_refdes")
            location = _component_location(component)
            error.details.update(
                {
                    "orphan_placed": True,
                    "orphan_library": library,
                    "orphan_device": device,
                    "orphan_symbol": symbol,
                    "orphan_x": location.get("x", x),
                    "orphan_y": location.get("y", y),
                    "requested_refdes": refdes,
                    "hint": (
                        "an unnamed symbol was left on the sheet and this API cannot "
                        "delete it; remove it in Designer before retrying"
                    ),
                    "_untrusted": ["orphan_library", "orphan_device", "orphan_symbol"],
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
        net = _add_schematic_net(block, operation, pin_objects[0], pin_objects[1], client)
        _label_schematic_net(net, str(operation.get("net", "")), operation)
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
        Path("common") / "win64" / "bin" / "viewdraw.exe"
        if domain == "schematic"
        else Path("common") / "win64" / "bin" / "ExpeditionPCB.exe"
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
    with _PromptAnswerer(DESIGNER_PROMPTS, process_name="viewdraw.exe"):
        return app.OpenProject(path)


_APPLICATION_PROGIDS = {
    "schematic": ("Viewdraw.Application", "Viewdraw.Application.60"),
    "pcb": ("MGCPCB.Application", "MGCPCB.ExpeditionPCBApplication"),
}


def _still_registered(client: Any, domain: str) -> bool:
    for progid in _APPLICATION_PROGIDS[domain]:
        try:
            client.GetActiveObject(progid)
            return True
        except Exception:
            continue
    return False


def _quit_and_confirm(client: Any, app: Any, domain: str, wait: float = 20.0) -> None:
    """Quit the application and check that it went.

    A call cut short by a timeout can leave a question up -- a snapshot that had
    asked Designer to switch projects left "close all open documents?" on screen --
    and `Quit` then returns without quitting: the stop reported success with
    Designer still running behind the dialog. The known questions are answered while
    it quits, and an application still registered afterwards is an error that names
    what holds it.
    """
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
                "hint": f"answer or close the dialog in {name}, then session stop again",
                "_untrusted": ["blocking_dialogs"],
            },
        )


def _open_designer_project(app: Any, params: dict[str, Any]) -> Any:
    path = params.get("project") or params.get("path")
    if not path:
        return None
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
            # Layout's start-up notices (the OpenGL graphics box) appear before its
            # automation object exists; press them, or the start never completes.
            try:
                from . import win_dialogs

                win_dialogs.dismiss_all()
            except Exception:
                pass
            time.sleep(0.5)
    return False


JOB_WIZARD_TIMEOUT = 240.0
# EPcbUnit: 0 current, 2 mils, 3 inch, 4 mm, 5 µm (win32com constants epcbUnit*)
UNIT_CODES = {"current": 0, "mils": 2, "mil": 2, "th": 2, "inch": 3, "mm": 4, "um": 5}
UNIT_MM = 4
# The stock templates open with "Loc: Assembly Bottom", which hides top-side parts.
DEFAULT_DISPLAY_SCHEME = "Loc: All On"
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
)


def _start_by_launcher(client: Any, domain: str, wait: float = LAUNCH_WAIT) -> Any:
    """Start the application through its launcher and wait for its automation object.

    An instance that COM activation starts never registers itself in the running
    object table, so nothing can attach to it afterwards; one started through the
    launcher in `common/win64/bin` does, once its start-up (about a minute for
    Layout) is over. None when the launcher is not installed.
    """
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
    try:
        text = raw.decode("mbcs")
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", "replace")
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
            for line in log_path.read_bytes().decode("mbcs", "replace").splitlines()
            if line.lstrip().startswith("WARNING:")
        )
    except (OSError, LookupError):
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
            {"path": str(output_path), "hint": "choose another output path or remove the file"},
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
    try:
        completed = subprocess.run(
            arguments,
            cwd=str(executable.parent),
            capture_output=True,
            timeout=float(params.get("timeout", 600.0)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(
            "E_TIMEOUT", "sch2pdf timed out", {"project": str(project_path)}
        ) from exc
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot start sch2pdf: {exc}") from exc
    stdout = completed.stdout.decode("utf-8", "replace")
    exported = completed.returncode == 0 and output_path.is_file()
    return {
        "exported": exported,
        "format": "pdf",
        "path": str(output_path),
        "size": output_path.stat().st_size if output_path.is_file() else 0,
        "sheets": _sch2pdf_sheets(stdout),
        "exit_code": completed.returncode,
        "messages": [line.strip() for line in stdout.splitlines() if line.strip()][-8:],
        "_untrusted": ["path", "sheets", "messages"],
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
                "run library build --package"
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
    try:
        from . import win_dialogs

        win_dialogs.dismiss_all()
    except Exception:
        pass


def _ensure_project(app: Any, project_path: Path) -> None:
    """Open `project_path` in Designer unless it is the project already open."""
    opened = False
    try:
        opened = bool(app.IsProjectOpened())
    except Exception:
        opened = False
    # Ask for the open project's path whatever IsProjectOpened() claimed. After a
    # call times out it reports false with the project still open, and opening a
    # project Designer already has is refused with 64185 -- whose text is about
    # scripts and GUIs and points nowhere near the cause. The path is the
    # trustworthy answer, so read it first and believe it.
    current = ""
    try:
        current = str(_com_member(_com_member(app, "GetProjectData"), "GetProjectFilePath") or "")
    except Exception:
        current = ""
    same = False
    if current:
        try:
            same = Path(current).resolve() == project_path
        except OSError:
            same = False
    if not same:
        try:
            if opened or current:
                app.CloseProject()
                _settle(4.0)
            _open_project_answering(app, str(project_path))
            _settle(6.0)
        except Exception as exc:
            error = _com_error(exc, "open_project")
            error.details.setdefault("requested_project", str(project_path))
            if current:
                error.details.setdefault("open_project", current)
                error.details.setdefault("_untrusted", []).append("open_project")
            raise error from exc
    try:
        if app.ActiveView is None:
            app.Documents.Open(app.GetActiveDesign())
            _settle(3.0)
    except Exception as exc:
        raise _com_error(exc, "open_design_view") from exc


def _reopen_project(app: Any, project_path: Path) -> None:
    """Sheet operations leave `DesignComponents` unusable until the project is reopened."""
    try:
        app.CloseProject()
        _settle(4.0)
        _open_project_answering(app, str(project_path))
        _settle(8.0)
        if app.ActiveView is None:
            app.Documents.Open(app.GetActiveDesign())
            _settle(3.0)
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

    Designer has no automation call that makes a project, so a new one is a copy
    of a known-good project: the folder minus backups, logs, scratch space and
    the layout templates, the `.prj` renamed, and the absolute central-library
    keys that pointed into the template folder pointed into the copy. If the
    template is the project Designer has open, it is closed first (the iCDB
    server holds its files) and the copy is opened afterwards.
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
    if target_path.exists() or (target_dir.exists() and any(target_dir.iterdir())):
        raise AdapterError(
            "E_CONFLICT", "the new project folder is not empty", {"path": str(target_dir)}
        )
    if source_dir == target_dir or source_dir in target_dir.parents:
        raise AdapterError("E_USAGE", "the new project cannot live inside the template folder")
    app: Any = None
    try:
        app = _viewdraw_application(client, attach_only=True)
    except AdapterError:
        app = None
    template_closed = False
    if app is not None:
        try:
            if bool(app.IsProjectOpened()):
                current = str(
                    _com_member(_com_member(app, "GetProjectData"), "GetProjectFilePath") or ""
                )
                if current and Path(current).resolve() == template_path:
                    app.CloseProject()
                    _settle(5.0)
                    template_closed = True
        except Exception as exc:
            raise _com_error(exc, "close_template_project") from exc
    try:
        shutil.copytree(
            source_dir,
            target_dir,
            ignore=shutil.ignore_patterns(*CLONE_IGNORE),
            dirs_exist_ok=True,
        )
        copied = target_dir / template_path.name
        if copied != target_path:
            copied.rename(target_path)
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot copy the template project: {exc}") from exc
    rewritten: list[dict[str, str]] = []
    try:
        text = target_path.read_text(encoding="utf-8", errors="surrogateescape")
        lines: list[str] = []
        for line in text.splitlines(keepends=True):
            for key in ("KEY CentralLibrary ", "KEY DBCFile "):
                if not line.startswith(key):
                    continue
                value = line[len(key) :].strip().strip('"')
                library = Path(value)
                if not library.is_absolute():
                    continue
                try:
                    relative = library.resolve().relative_to(source_dir)
                except ValueError:
                    continue
                new_value = str(target_dir / relative)
                newline = line[len(line.rstrip("\r\n")) :]
                line = f'{key}"{new_value}"{newline}'
                rewritten.append({"key": key.split()[1], "value": new_value})
            lines.append(line)
        target_path.write_text("".join(lines), encoding="utf-8", errors="surrogateescape")
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot rewrite the project file: {exc}") from exc
    # A template can list symbol partitions whose parts database was never copied.
    # Designer raises a modal dialog for one of those the first time it places a
    # part, which blocks the draw; fix it now, while nothing is running.
    parts_databases = _repair_missing_parts_databases(target_path)
    files = 0
    size = 0
    for path in target_dir.rglob("*"):
        if path.is_file():
            files += 1
            size += path.stat().st_size
    opened = False
    if app is not None and bool(params.get("open", True)):
        _ensure_project(app, target_path)
        opened = True
    return {
        "project": target_path.stem,
        "path": str(target_path),
        "template": str(template_path),
        "created": True,
        "files": files,
        "bytes": size,
        "rewritten": rewritten,
        "parts_databases": parts_databases,
        "template_closed": template_closed,
        "opened": opened,
        "_untrusted": ["project", "path", "template", "rewritten", "parts_databases"],
    }


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
    return {
        "matches": not differences and not broken,
        "nets_checked": len(expected),
        "differences": differences,
        "links_checked": len(verify.get("links") or []),
        "links_broken": broken,
    }


LIBRARY_TOOL_TIMEOUT = 300.0


def _close_if_open(app: Any, project_path: Path) -> bool:
    """Close `project_path` if it is the project Designer has open; True if it was."""
    try:
        if not bool(app.IsProjectOpened()):
            return False
        current = str(_com_member(_com_member(app, "GetProjectData"), "GetProjectFilePath") or "")
        if current and Path(current).resolve() == project_path:
            app.CloseProject()
            _settle(4.0)
            return True
    except Exception as exc:
        raise _com_error(exc, "close_project") from exc
    return False


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

            for dialog in win_dialogs.find_dialogs(only_xpedition=False):
                title = str(dialog.get("title", "")).strip().lower()
                owner = str(dialog.get("process", "")).lower()
                if owner != executable.name.lower() and title != name.lower():
                    continue
                key = (str(dialog.get("title", "")), str(dialog.get("text", "")))
                if key not in seen:
                    seen.add(key)
                    dialogs.append({"title": key[0], "text": key[1]})
                win_dialogs.dismiss(int(dialog["hwnd"]))
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


def _tool_log(path: Path) -> dict[str, Any]:
    """A converter's log in the system code page, with its error lines pulled out."""
    try:
        raw = path.read_bytes()
    except OSError:
        return {"path": str(path), "errors": [], "lines": []}
    try:
        text = raw.decode("mbcs")
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", "replace")
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
    return {"path": str(path), "errors": errors, "lines": lines[-40:]}


REJECTED_CELL = re.compile(r'(?:无法添加单元|unable to add cell|cannot add cell)\s*"([^"]+)"', re.I)


def _rejected_cells(path: Path) -> list[str]:
    """The cells a `HKP2CellDB` log says it could not add (it then saves nothing at all)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    try:
        text = raw.decode("mbcs")
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", "replace")
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
        raise AdapterError(
            "E_NOT_FOUND",
            f"the project has no LIST {list_name} section",
            {"project": str(project_path)},
        )
    end = text.find("ENDLIST", index)
    if end < 0:
        raise AdapterError("E_VALIDATION", f"the project's LIST {list_name} has no ENDLIST")
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text[:end] + f'VALUE "{entry}"{newline}' + text[end:]
    project_path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return True


def _library_import(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Import generated padstacks, cells and parts into the project's central library.

    The texts go through the stock converters (`HKP2PadstackDB`, `HKP2CellDB`,
    `HKP2PartsDB`), which also register a new partition in the `.lmc`. Designer's
    project is closed meanwhile, because it holds the library, and the partition's
    parts database is added to the project's `LIST PDBs` so the packager searches it.
    """
    project = params.get("project") or params.get("path")
    if not project:
        raise AdapterError("E_USAGE", "library_import requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    if not project_path.is_file():
        raise AdapterError("E_NOT_FOUND", "project file was not found", {"path": str(project_path)})
    partition = str(params.get("partition") or "Case")
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", partition):
        raise AdapterError(
            "E_USAGE", "partition must be a plain identifier", {"partition": partition}
        )
    lmc, root = _central_library(project_path)
    if not lmc.is_file():
        raise AdapterError("E_NOT_FOUND", "the central library was not found", {"path": str(lmc)})
    work = root / "Work" / "xpedition-cli"
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot create the work folder: {exc}") from exc
    app: Any = None
    try:
        app = _viewdraw_application(client, attach_only=True)
    except AdapterError:
        app = None
    reopen = False
    if app is not None:
        try:
            if bool(app.IsProjectOpened()):
                current = str(
                    _com_member(_com_member(app, "GetProjectData"), "GetProjectFilePath") or ""
                )
                if current and Path(current).resolve() == project_path:
                    app.CloseProject()
                    _settle(4.0)
                    reopen = True
        except Exception as exc:
            raise _com_error(exc, "close_project_for_library") from exc
    steps: list[dict[str, Any]] = []
    padstack_db = root / "Layout" / "PadstackDB.psk"
    jobs = [
        (
            "padstacks",
            "HKP2PadstackDB",
            lambda src, log: ["-i", src, "-o", str(padstack_db), "-c", str(lmc), "-m", "-l", log],
        ),
        (
            "cells",
            "HKP2CellDB",
            lambda src, log: [
                "-i",
                src,
                "-o",
                str(root / "CellDBLibs" / f"{partition}.cel"),
                "-psk",
                str(padstack_db),
                "-c",
                str(lmc),
                "-m",
                "-l",
                log,
            ],
        ),
        (
            "parts",
            "HKP2PartsDB",
            lambda src, log: [
                "-p",
                str(project_path),
                "-i",
                src,
                "-o",
                str(root / "PartsDBLibs" / f"{partition}.pdb"),
                "-r",
                "-l",
                log,
            ],
        ),
    ]
    registered = False
    cells: list[str] = []
    cells_missing: list[str] = []
    try:
        for step, tool, arguments in jobs:
            text = params.get(step)
            if not text:
                continue
            source = work / f"{step}.hkp"
            log = work / f"{step}.log"
            try:
                source.write_text(str(text), encoding="ascii", errors="replace")
                if log.exists():
                    log.unlink()
            except OSError as exc:
                raise AdapterError("E_IO", f"cannot write {source.name}: {exc}") from exc
            run = _run_library_tool(tool, arguments(str(source), str(log)))
            run["step"] = step
            run["log"] = _tool_log(log)
            steps.append(run)
            if run["exit_code"] != 0 or run["dialogs"] or run["log"]["errors"]:
                break
        if params.get("parts") and not any(
            s["step"] == "parts" and s["exit_code"] != 0 for s in steps
        ):
            registered = _ensure_prj_pdb(project_path, f"PartsDBLibs\\{partition}.pdb")
            cells = _sync_prj_cells(project_path, root)
            wanted = [str(item) for item in params.get("cell_partitions") or []]
            if wanted:
                # parts referencing cells of other partitions (KiCad imports): Layout's
                # Database Load only searches the partitions the design lists
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
        "partition": partition,
        "steps": steps,
        "failed": failed,
        "pdb_registered": registered,
        "cells_registered": cells,
        "cells_missing": cells_missing,
        "ok": not failed,
        "_untrusted": ["project", "library", "steps"],
    }


def _register_cell_partitions(
    project_path: Path, library_root: Path, partitions: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Add `CellDBLibs\\<partition>.cel` to the design's `LIST 2dCellLibraries` for every
    partition whose cell database exists; the entries added and the partitions missing."""
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


KICAD_IMPORT_TIMEOUT = 900.0
KICAD_IMPORT_RUNS = 60  # converter runs allowed per partition while isolating bad cells


def _import_kicad_partition(
    plan: Any, work: Path, lmc: Path, padstack_db: Path, cell_db: Path, record: dict[str, Any]
) -> bool:
    """Run the converters for one partition plan; `record` gets every run, the cells the
    converter refused and the ones it crashed on. HKP2CellDB saves nothing when it refuses
    a cell, so refused cells are dropped and the rest imported again; when it crashes
    without naming one, the set is halved until the cell at fault is alone."""
    from . import library_hkp

    partition = plan.partition
    record.setdefault("steps", [])
    record["rejected"] = []
    record["crashed"] = []
    runs = 0
    pad_extra = ["-o", str(padstack_db), "-c", str(lmc), "-m"]
    cell_extra = ["-o", str(cell_db), "-psk", str(padstack_db), "-c", str(lmc), "-m"]

    def convert(
        step: str, tool: str, extra: list[str], text: str, tag: str
    ) -> tuple[bool, list[str]]:
        nonlocal runs
        runs += 1
        source = work / f"{partition}.{step}{tag}.hkp"
        log = work / f"{partition}.{step}{tag}.log"
        try:
            source.write_text(text, encoding="ascii", errors="replace")
            if log.exists():
                log.unlink()
        except OSError as exc:
            raise AdapterError("E_IO", f"cannot write {source.name}: {exc}") from exc
        arguments = ["-i", str(source), *extra, "-l", str(log)]
        run = _run_library_tool(tool, arguments, timeout=KICAD_IMPORT_TIMEOUT)
        errors = _tool_log(log)["errors"]
        record["steps"].append(
            {
                "step": step,
                "run": tag or "all",
                "exit_code": run["exit_code"],
                "dialogs": run["dialogs"],
                "errors": errors[:5],
            }
        )
        ok = run["exit_code"] == 0 and not run["dialogs"] and not errors
        return ok, ([] if ok else _rejected_cells(log))

    def import_cells(names: list[str], tag: str) -> bool:
        subset = library_hkp.LibraryPlan(partition=partition)
        subset.pads, subset.holes, subset.padstacks = plan.pads, plan.holes, plan.padstacks
        subset.cells = {name: plan.cells[name] for name in names}
        ok, rejected = convert(
            "cells", "HKP2CellDB", cell_extra, library_hkp.render_cells(subset), tag
        )
        if ok:
            return True
        rejected = [name for name in rejected if name in subset.cells]
        if rejected:
            record["rejected"] += rejected
            for name in rejected:
                del plan.cells[name]
            remaining = [name for name in names if name not in rejected]
            return import_cells(remaining, tag + "r") if remaining else True
        if len(names) == 1:
            record["crashed"] += names
            del plan.cells[names[0]]
            return True
        if runs > KICAD_IMPORT_RUNS:
            return False
        half = len(names) // 2
        left = import_cells(names[:half], tag + "a")
        right = import_cells(names[half:], tag + "b")
        return left and right

    pad_ok, _ = convert(
        "padstacks", "HKP2PadstackDB", pad_extra, library_hkp.render_padstacks(plan), ""
    )
    return pad_ok and import_cells(sorted(plan.cells), "")


def _kicad_import(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Convert KiCad footprint libraries into cell partitions of the project's central library.

    Every `.pretty` folder becomes one partition named after it (`Package_SO`,
    `Connector_JST`, ...): its padstacks merge into the library's padstack database
    through `HKP2PadstackDB`, its cells go through `HKP2CellDB`. Nothing is registered in
    the project: `library build` registers the partitions a design's parts reference.
    Designer's project is closed meanwhile, and `Work/xpedition-cli/kicad/progress.json`
    is rewritten after every library so a long run can be watched.
    """
    from . import kicad_footprints

    project = params.get("project") or params.get("path")
    if not project:
        raise AdapterError("E_USAGE", "kicad_import requires the project path")
    project_path = Path(str(project)).expanduser().resolve()
    if not project_path.is_file():
        raise AdapterError("E_NOT_FOUND", "project file was not found", {"path": str(project_path)})
    root = (
        Path(str(params["root"])).expanduser()
        if params.get("root")
        else kicad_footprints.default_root()
    )
    if root is None or not root.is_dir():
        raise AdapterError(
            "E_NOT_FOUND",
            "the KiCad footprint folder was not found",
            {"hint": "pass root, or set XPEDITION_KICAD_FOOTPRINTS"},
        )
    pretties = kicad_footprints.libraries(root)
    wanted = [str(item) for item in params.get("libraries") or []]
    if wanted:
        stems = {(w[:-7] if w.lower().endswith(".pretty") else w).lower() for w in wanted}
        pretties = [p for p in pretties if p.name[:-7].lower() in stems]
        missing = sorted(stems - {p.name[:-7].lower() for p in pretties})
        if missing:
            raise AdapterError(
                "E_NOT_FOUND", "KiCad libraries were not found", {"libraries": missing}
            )
    limit = int(params.get("limit") or 0)
    if limit > 0:
        pretties = pretties[:limit]
    lmc, library_root = _central_library(project_path)
    if not lmc.is_file():
        raise AdapterError("E_NOT_FOUND", "the central library was not found", {"path": str(lmc)})
    work = library_root / "Work" / "xpedition-cli" / "kicad"
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot create the work folder: {exc}") from exc
    padstack_db = library_root / "Layout" / "PadstackDB.psk"
    app, reopen = _release_project(client, project_path)
    if reopen:
        _settle(4.0)
    partitions: list[dict[str, Any]] = []
    failed: list[str] = []
    started_all = time.monotonic()
    try:
        for pretty in pretties:
            started = time.monotonic()
            plan, issues = kicad_footprints.convert_library(pretty)
            partition = plan.partition
            record: dict[str, Any] = {
                "library": pretty.name[:-7],
                "partition": partition,
                "cells": len(plan.cells),
                "padstacks": len(plan.padstacks),
                "issues": len(issues),
                "issue_samples": issues[:5],
                "steps": [],
                "ok": True,
            }
            if not plan.cells:
                record["skipped"] = "no cells"
                partitions.append(record)
                continue
            cell_db = library_root / "CellDBLibs" / f"{partition}.cel"
            record["ok"] = _import_kicad_partition(plan, work, lmc, padstack_db, cell_db, record)
            record["cells"] = len(plan.cells)
            record["seconds"] = round(time.monotonic() - started, 1)
            if not record["ok"]:
                failed.append(partition)
            partitions.append(record)
            try:
                (work / "progress.json").write_text(
                    json.dumps(
                        {
                            "done": len(partitions),
                            "total": len(pretties),
                            "failed": failed,
                            "seconds": round(time.monotonic() - started_all, 1),
                            "partitions": partitions,
                        },
                        ensure_ascii=False,
                        indent=1,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
    finally:
        if reopen and app is not None:
            try:
                _ensure_project(app, project_path)
            except AdapterError:
                pass
    return {
        "project": str(project_path),
        "library": str(lmc),
        "root": str(root),
        "libraries": len(pretties),
        "cells": sum(int(p["cells"]) for p in partitions if p["ok"] and "skipped" not in p),
        "partitions": partitions,
        "failed": failed,
        "seconds": round(time.monotonic() - started_all, 1),
        "ok": not failed,
        "_untrusted": ["project", "library", "root", "partitions"],
    }


def _release_project(client: Any, project_path: Path) -> tuple[Any, bool]:
    """Close `project_path` in Designer if it is open there; the app and whether to reopen."""
    try:
        app = _viewdraw_application(client, attach_only=True)
    except AdapterError:
        return None, False
    try:
        return app, _close_if_open(app, project_path)
    except AdapterError:
        return app, False


def _sync_prj_cells(project_path: Path, library_root: Path) -> list[str]:
    """Give the design's `LIST 2dCellLibraries` a cell partition for every parts partition
    in its `LIST PDBs` that the library has; the entries added.

    Layout's Database Load reads that list to find cells for the parts it packages and
    stops with "No cell library search paths found" when the list is missing.
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
    try:
        text = raw.decode("mbcs")
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", "replace")
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
    try:
        text = raw.decode("mbcs")
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", "replace")
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
                answered, blocking = win_dialogs.answer_prompts(self.rules, self.process_name)
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
    current = _value(app, "ActiveDocument", default=None)
    if current is not None:
        full = str(_value(current, "FullName", default="") or "")
        try:
            if full and Path(full).resolve() == pcb_path:
                return current, []
        except OSError:
            pass
    with _PromptAnswerer(rules) as prompts:
        try:
            doc = app.OpenDocument(str(pcb_path))
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
    """End the running Layout, which holds no document any more; True if there was one.

    `Application.Quit` leaves Layout on its start page with the files an output dialog
    opened still held (`LogFiles/DrillPrefs.txt` after NC drill), so a Layout that
    is still running afterwards is ended by its process — only when no document is
    open in it, so nothing unsaved can be lost.
    """
    try:
        app: Any = _application(client, attach_only=True)
    except AdapterError:
        app = None  # running but not reachable (after an earlier Quit): its process goes
    if app is not None and _value(app, "ActiveDocument", default=None) is not None:
        return False
    if app is not None:
        try:
            app.Quit()
        except Exception:
            pass
        _settle(6.0)
    pids = _layout_processes()
    if app is None and not pids:
        return False
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
    _settle(4.0)
    return True


def _close_board_if_open(client: Any, pcb_path: Path) -> bool:
    """Close `pcb_path` in Layout if it is the document open there; True if it was."""
    try:
        app = _application(client, attach_only=True)
    except AdapterError:
        return False
    current = _value(app, "ActiveDocument", default=None)
    if current is None:
        return False
    full = str(_value(current, "FullName", default="") or "")
    try:
        if not full or Path(full).resolve() != pcb_path.resolve():
            return False
    except OSError:
        return False
    try:
        doc = _licensed_document(app)
        doc.Close(False)
        _settle(3.0)
        return True
    except Exception as exc:
        raise _com_error(exc, "close_document") from exc


def _backup_layout_folder(folder: Path) -> str | None:
    """Zip a layout folder beside the project before it is deleted, and answer the
    archive's path. Deleting a board is otherwise irreversible: the folder holds the
    placement, the routing, the pours and the output setups, none of which live
    anywhere else. `LogFiles` and `*.bak` are left out. None when there is nothing to
    keep; an archive that cannot be written is an error, not a silent skip."""
    import zipfile
    from datetime import datetime

    if not folder.is_dir() or not any(folder.rglob("*")):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = folder.parent / f"{folder.name}-backup-{stamp}.zip"
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in sorted(folder.rglob("*")):
                relative = item.relative_to(folder)
                if not item.is_file() or relative.parts[:1] == ("LogFiles",):
                    continue
                if item.name.endswith(".bak"):
                    continue
                try:
                    bundle.write(item, str(Path(folder.name) / relative))
                except OSError:
                    continue  # a file Layout still holds open is not worth failing for
    except OSError as exc:
        raise AdapterError(
            "E_IO",
            f"cannot back the layout folder up before deleting it: {exc}",
            {"folder": str(folder), "archive": str(archive)},
        ) from exc
    return str(archive)


def _remove_board(client: Any, project_path: Path, pcb_relative: str, name: str) -> dict[str, Any]:
    """Remove a design's layout data so the board can be created afresh: the folder is
    zipped up beside the project, the board is closed in Layout, the `PCB` folder
    deleted, and `PCBDesignPath` and `LayoutTemplate` cleared in the `.prj`. A board
    created again picks up every part's current cell, which forward annotation never
    changes on an existing component."""
    from . import project_file

    folder = project_path.parent / "PCB"
    backup = _backup_layout_folder(folder)
    candidates = []
    if pcb_relative:
        candidates.append(project_path.parent / pcb_relative)
    candidates.append(folder / f"{name}.pcb")
    closed = False
    for candidate in candidates:
        if candidate.exists():
            closed = _close_board_if_open(client, candidate) or closed
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
        if not deleted and _quit_layout(client):
            # a file an output dialog opened (LogFiles/DrillPrefs.txt after NC drill)
            # stays held by the Layout process after the document closed; without
            # Layout the folder goes, and the next step starts Layout again
            for _attempt in range(10):
                try:
                    shutil.rmtree(folder)
                    deleted = True
                    break
                except OSError as exc:
                    last_error = exc
                    if not any(folder.rglob("*")):
                        deleted = True
                        break
                    time.sleep(2.0)
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
                "the outline is too small for the plan; enlarge it with pcb outline",
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
    """A net class with its trace widths, through Constraint Manager's automation.

    Layout's own automation reads rules only; `ConstraintsAuto` (the Constraint
    Manager server) loads the board's layout-context design, and there a net class can
    be added (`NetClasses.Add`), nets assigned (`AssignNet`) and, per scheme and layer,
    the trace-width constraints set (`Constraint.Value`, thousandths of an inch, types
    283 minimum / 439 typical / 183 expansion). Layout picks the change up through
    `ProjectIntegration.SynchCES`; a reopen alone does not. Without `apply`, the
    current classes and widths are reported with the plan.
    """
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
        all_nets = {str(net.Name): net for net in _collection(design.Nets)}
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


def _side_name(obj: Any) -> str:
    name = str(_value(obj, "SideName", default="") or "").lower()
    if name in ("top", "bottom"):
        return name
    return "bottom" if int(_value(obj, "Side", default=1) or 1) == 2 else "top"


def _board_model(doc: Any) -> dict[str, Any]:
    """Everything the renderer draws, in mm: outline, pads, vias, traces, planes,
    holes, silkscreen graphics and texts."""
    model: dict[str, Any] = {
        "layers": int(_value(doc, "LayerCount", default=2) or 2),
        "outline": [],
        "pads": [],
        "vias": [],
        "traces": [],
        "planes": [],
        "holes": [],
        "silk": [],
        "texts": [],
    }
    outline = _value(doc, "BoardOutline", default=None)
    if outline is not None:
        record = _geometry_record(outline.Geometry, kind="outline")
        if record:
            model["outline"].append(record)
    for component in _items(_com_member(doc, "Components")):
        refdes = str(_value(component, "RefDes", default=""))
        for pin in _items(component.Pins):
            net = str(_value(pin.Net, "Name", default="")) if _value(pin, "Net") else ""
            for pad in _items(pin.Pads):
                layer = int(_value(pad, "Layer", default=0) or 0)
                for geometry in _items(pad.Geometries):
                    record = _geometry_record(
                        geometry, layer=layer, net=net, kind="pad", refdes=refdes
                    )
                    if record:
                        model["pads"].append(record)
            for hole in _items(pin.Holes):
                try:
                    model["holes"].append(
                        [
                            float(pin.PositionX) * TH_TO_MM,
                            float(pin.PositionY) * TH_TO_MM,
                            float(hole.GetDrillSize(UNIT_MM)),
                        ]
                    )
                except Exception:
                    continue
    for via in _items(_com_member(doc, "Vias")):
        net = str(_value(via.Net, "Name", default="")) if _value(via, "Net") else ""
        ring = None
        for pad in _items(via.Pads):
            for geometry in _items(pad.Geometries):
                ring = _geometry_record(geometry, layer=int(_value(pad, "Layer", default=1) or 1))
                if ring:
                    break
            if ring:
                break
        if ring:
            model["vias"].append({**ring, "net": net, "kind": "via"})
        for hole in _items(via.Holes):
            try:
                model["holes"].append(
                    [
                        float(via.PositionX) * TH_TO_MM,
                        float(via.PositionY) * TH_TO_MM,
                        float(hole.GetDrillSize(UNIT_MM)),
                    ]
                )
            except Exception:
                continue
    for mounting in _items(_com_member(doc, "MountingHoles")):
        for hole in _items(mounting.Holes):
            try:
                model["holes"].append(
                    [
                        float(mounting.PositionX) * TH_TO_MM,
                        float(mounting.PositionY) * TH_TO_MM,
                        float(hole.GetDrillSize(UNIT_MM)),
                    ]
                )
            except Exception:
                continue
    for trace in _items(_com_member(doc, "Traces")):
        net = str(_value(trace.Net, "Name", default="")) if _value(trace, "Net") else ""
        geometry = trace.Geometry
        record = _geometry_record(
            geometry,
            layer=int(_value(trace, "Layer", default=1) or 1),
            width=float(_value(geometry, "LineWidth", default=0) or 0) * TH_TO_MM,
            net=net,
            kind="trace",
        )
        if record:
            model["traces"].append(record)
    for plane in _items(_com_member(doc, "PlaneShapes")):
        net = str(_value(plane.Net, "Name", default="")) if _value(plane, "Net") else ""
        layer = int(_value(plane, "Layer", default=1) or 1)
        generated = [
            _geometry_record(item.Geometry, layer=layer, net=net, kind="plane")
            for item in _items(_value(plane, "GeneratedPlanes", default=None) or ())
        ]
        generated = [item for item in generated if item]
        if not generated:
            record = _geometry_record(plane.Geometry, layer=layer, net=net, kind="plane_draft")
            generated = [record] if record else []
        model["planes"].extend(generated)
    for gfx in _items(_com_member(doc, "FabricationLayerGfxs")):
        if int(_value(gfx, "Type", default=0) or 0) != FAB_GFX_SILKSCREEN:
            continue
        geometry = gfx.Geometry
        filled = bool(_value(geometry, "Filled", default=False))
        width = float(_value(geometry, "LineWidth", default=0) or 0) * TH_TO_MM
        record = _geometry_record(
            geometry,
            side=_side_name(gfx),
            width=0.0 if filled else (width or 0.12),
            kind="silk",
        )
        if record:
            model["silk"].append(record)
    for text in _items(_com_member(doc, "FabricationLayerTexts")):
        if int(_value(text, "Type", default=0) or 0) != FAB_GFX_SILKSCREEN:
            continue
        try:
            fmt = text.Format
            model["texts"].append(
                {
                    "x": float(text.PositionX) * TH_TO_MM,
                    "y": float(text.PositionY) * TH_TO_MM,
                    "text": str(text.TextString),
                    "height": float(_value(fmt, "Height", default=40) or 40) * TH_TO_MM,
                    "orientation": float(_value(fmt, "Orientation", default=0) or 0),
                    "side": _side_name(text),
                    "mirrored": bool(_value(fmt, "Mirrored", default=False)),
                }
            )
        except Exception:
            continue
    return model


def _render_board(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """A PNG of the board drawn from its geometry (see `board_render`), so the
    picture does not depend on the desktop being unlocked or the window on screen."""
    from . import board_render

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
    try:
        scale = float(params.get("scale") or 20.0)
    except (TypeError, ValueError) as exc:
        raise AdapterError("E_USAGE", "scale is pixels per millimetre") from exc
    if not 1.0 <= scale <= 200.0:
        raise AdapterError("E_VALIDATION", "scale must be between 1 and 200 px/mm")
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    model = _board_model(doc)
    picture = board_render.render(
        board_render.model_from_dict(model), output_path, scale=scale, side=side
    )
    model_path = params.get("model_output")
    if model_path:
        Path(str(model_path)).expanduser().resolve().write_text(
            json.dumps(model, ensure_ascii=False), encoding="utf-8"
        )
    return {
        "pcb": str(pcb_path),
        "side": side,
        "scale": scale,
        "picture": picture,
        "layers": model["layers"],
        "prompts": prompts,
        "_untrusted": ["pcb", "picture", "prompts"],
    }


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
    opens: dict[str, int] = {}
    for net in _items(_com_member(doc, "Nets")):
        name = str(_value(net, "Name", default=""))
        if name in names:
            opens[name] = int(_com_member(net, "NumberOfOpens") or 0)
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
    """Traces and vias placed where the caller says (`Document.PutTrace` /
    `PutVia`), the person's hand routing through the CLI.

    Each trace is a net, a layer, a width and its points (mm); each via a net and a
    point, with the board's via padstack unless another is named. Layout's online
    DRC refuses a trace or via that violates a clearance ("DRC 违反"), so a bad item
    fails on its own and the rest go on. The plan reports, per trace end, the nearest
    pin and whether it is that trace's net; after the run, the open count of every
    touched net says what is still unconnected.
    """
    pcb_path = _layout_board_path(params)
    items = plan_items({"items": params.get("items") or []})
    apply = bool(params.get("apply", False))
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    layers = int(_value(doc, "LayerCount", default=0) or 0)
    nets: dict[str, Any] = {}
    for item in items:
        if item["net"] not in nets:
            nets[item["net"]] = _find_net(doc, item["net"])
        if item["kind"] == "trace" and layers and not 1 <= item["layer"] <= layers:
            raise AdapterError(
                "E_VALIDATION",
                "layer is outside the board's stackup",
                {"layer": item["layer"], "layers": layers},
            )
    pins = _pin_index(doc)
    plan: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, item in enumerate(items):
        record: dict[str, Any] = {"index": index, **item}
        if item["kind"] == "trace":
            ends = []
            for point in (item["points"][0], item["points"][-1]):
                near = _nearest_pin(pins, point)
                ends.append(near)
                if near is None:
                    continue
                if near["distance"] > ENDPOINT_TOLERANCE_MM:
                    warnings.append(
                        f"item {index}: end {point} is {near['distance']} mm from the nearest pin"
                    )
                elif near["net"] != item["net"]:
                    warnings.append(
                        f"item {index}: end {point} sits on {near['refdes']}.{near['pin']} "
                        f"of net {near['net'] or '(none)'}, not {item['net']}"
                    )
            record["ends"] = ends
            record["length"] = round(
                sum(
                    math.hypot(b[0] - a[0], b[1] - a[1])
                    for a, b in zip(item["points"], item["points"][1:], strict=False)
                ),
                3,
            )
        else:
            near = _nearest_pin(pins, item["at"])
            record["nearest_pin"] = near
        plan.append(record)
    names = set(nets)
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "items": plan,
        "nets": sorted(names),
        "opens_before": _net_opens(doc, names),
        "warnings": warnings,
        "applied": False,
        "saved": False,
        "prompts": prompts,
        "_untrusted": ["pcb", "items", "warnings", "prompts"],
    }
    if not apply:
        return result
    padstacks: dict[str, Any] = {}
    placed = 0
    failed = 0
    pace = _pace(params)
    for record, item in zip(plan, items, strict=True):
        if pace and (placed or failed):
            time.sleep(pace)  # let Layout repaint: the person watches the routing grow
        try:
            if item["kind"] == "trace":
                rows = _points_rows(item["points"])
                trace = doc.PutTrace(
                    item["layer"],
                    nets[item["net"]],
                    float(item["width"]),
                    len(item["points"]),
                    rows,
                    None,
                    0,
                    UNIT_MM,
                )
                record["id"] = str(_value(trace, "UniqueId", default=""))
            else:
                key = item.get("padstack") or ""
                if key not in padstacks:
                    padstacks[key] = _via_padstack(doc, item.get("padstack"), item["at"], layers)
                via = doc.PutVia(
                    float(item["at"][0]),
                    float(item["at"][1]),
                    padstacks[key],
                    nets[item["net"]],
                    None,
                    0,
                    UNIT_MM,
                )
                record["id"] = str(_value(via, "UniqueId", default=""))
                record["padstack"] = str(_value(padstacks[key], "Name", default=""))
            record["ok"] = True
            placed += 1
        except Exception as exc:
            text = str(exc)
            record["ok"] = False
            record["error"] = "DRC violation: Layout refused it" if "DRC" in text else text[-160:]
            failed += 1
    regenerated = _regenerate_planes(doc)
    try:
        doc.Save()
        saved = True
    except Exception as exc:
        raise _com_error(exc, "save_after_hand_route") from exc
    result.update(
        {
            "placed": placed,
            "failed": failed,
            "opens_after": _net_opens(doc, names),
            "planes_regenerated": regenerated,
            "routing": _routing_counts(doc),
            "applied": True,
            "saved": saved,
        }
    )
    return result


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
    """Delete the traces and vias of the named nets (or of every net with `all`); with
    `at` (x, y mm) and optionally `layer`, only the trace with a vertex there or the via
    centred there (within 0.1 mm), the way a person picks one segment to delete."""
    pcb_path = _layout_board_path(params)
    names = [str(item).strip() for item in (params.get("nets") or []) if str(item).strip()]
    everything = bool(params.get("all", False))
    at = params.get("at")
    point = parse_points(at, 1)[0] if at else None
    layer = int(params["layer"]) if params.get("layer") not in (None, "") else None
    if not names and not everything and point is None:
        raise AdapterError("E_USAGE", "name the nets to unroute, or all, or a point")
    apply = bool(params.get("apply", False))
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    if names:
        for name in names:
            _find_net(doc, name)
    wanted = set(names)
    counts = {"traces": 0, "vias": 0}
    victims: list[Any] = []
    for key in ("Traces", "Vias"):
        for item in _items(_com_member(doc, key)):
            net = _value(item, "Net", default=None)
            net_name = str(_value(net, "Name", default="")) if net else ""
            if not (everything or net_name in wanted or (point is not None and not wanted)):
                continue
            if point is not None and not _touches(item, point, layer, 0.1):
                continue
            counts[key.lower()] += 1
            victims.append(item)
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "nets": sorted(wanted) if not everything else "all",
        "at": list(point) if point else None,
        "layer": layer,
        "to_delete": counts,
        "applied": False,
        "saved": False,
        "prompts": prompts,
        "_untrusted": ["pcb", "prompts"],
    }
    if not apply:
        return result
    deleted = 0
    for item in victims:
        try:
            item.Delete()
            deleted += 1
        except Exception:
            continue
    regenerated = _regenerate_planes(doc)
    try:
        doc.Save()
        saved = True
    except Exception as exc:
        raise _com_error(exc, "save_after_unroute") from exc
    result.update(
        {
            "deleted": deleted,
            "planes_regenerated": regenerated,
            "routing": _routing_counts(doc),
            "applied": True,
            "saved": saved,
        }
    )
    return result


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
            component.Place(x, y, target_rotation, True, 0, UNIT_MM, 0)
        except Exception as exc:
            text = str(exc)
            if was_placed:
                try:  # back where it was
                    component.Place(
                        before["x"], before["y"], before["rotation"], True, 0, UNIT_MM, 0
                    )
                except Exception:
                    pass
            raise AdapterError(
                "E_VALIDATION",
                "Layout refused the position"
                + (" (placement DRC: it touches another part)" if "DRC" in text else ""),
                {"refdes": refdes, "detail": text[-160:]},
            ) from exc
    finally:
        if previous_drc is not None:
            try:
                doc.RespectComponentPlacementDRC = previous_drc
            except Exception:
                pass
    try:
        doc.Save()
        saved = True
    except Exception as exc:
        raise _com_error(exc, "save_after_move") from exc
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
        }
    )
    return result


def _board_geometry(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """The board's geometry as data (what `pcb render` draws, plus the components with
    their pins), written to `output` as JSON or returned inline."""
    pcb_path = _layout_board_path(params)
    output = params.get("output")
    output_path = Path(str(output)).expanduser().resolve() if output else None
    if output_path is not None:
        if output_path.suffix.lower() != ".json":
            raise AdapterError("E_VALIDATION", "geometry output must end in .json")
        if output_path.exists() and not bool(params.get("replace", False)):
            raise AdapterError(
                "E_CONFLICT", "output file already exists", {"path": str(output_path)}
            )
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    model = _board_model(doc)
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
            record["pins"] = [
                {k: v for k, v in pin.items() if k != "refdes"}
                for pin in _pin_index_of(component, refdes)
            ]
        components.append(record)
    model["components"] = components
    counts = {key: len(value) for key, value in model.items() if isinstance(value, list)}
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "layers": model["layers"],
        "counts": counts,
        "prompts": prompts,
        "_untrusted": ["pcb", "prompts", "model"],
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        result["path"] = str(output_path)
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


def _tidy_labels(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Move every part's silkscreen reference designator to a free spot beside the
    part (`board_layout.place_labels`): clear of other parts, pads, holes, the other
    labels and the board's edge. The text objects are the cells' own (`Component.
    FabricationLayerTexts` on the silkscreen), moved with `Move`."""
    from . import board_layout

    pcb_path = _layout_board_path(params)
    try:
        gap = float(params.get("gap") if params.get("gap") is not None else board_layout.LABEL_GAP)
    except (TypeError, ValueError) as exc:
        raise AdapterError("E_USAGE", "gap is millimetres") from exc
    apply = bool(params.get("apply", False))
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    board = _board_rect(doc)
    labels: list[dict[str, Any]] = []
    texts: dict[str, Any] = {}
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
            height = float(_value(fmt, "Height", default=40) or 40) * TH_TO_MM
            width = max(len(refdes), 1) * height * 0.82 + 0.2
            orientation = float(_value(fmt, "Orientation", default=0.0) or 0.0) % 180
            if abs(orientation - 90) < 1:
                width, height = height, width  # the text stands upright with its part
            try:
                tx0, ty0, tx1, ty1 = _extrema_mm(text_obj)
                if tx1 - tx0 > 0.1 and ty1 - ty0 > 0.1:
                    width, height = tx1 - tx0, ty1 - ty0
            except Exception:
                pass
            x = float(text_obj.PositionX) * TH_TO_MM
            y = float(text_obj.PositionY) * TH_TO_MM
        except Exception:
            continue
        texts[refdes] = text_obj
        labels.append(
            {
                "refdes": refdes,
                "width": round(width, 3),
                "height": round(height, 3),
                "part": [round(v, 3) for v in part],
                "x": round(x, 3),
                "y": round(y, 3),
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
    plan = board_layout.place_labels(labels, obstacles, board, gap)
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "labels": plan,
        "moved": sum(1 for item in plan if item["moved"]),
        "unplaced": [item["refdes"] for item in plan if not item["placed"]],
        "applied": False,
        "saved": False,
        "prompts": prompts,
        "_untrusted": ["pcb", "labels", "prompts"],
    }
    if not apply:
        return result
    moved = 0
    failures: list[dict[str, Any]] = []
    for item in plan:
        if not item["moved"]:
            continue
        text_obj = texts[item["refdes"]]
        try:
            text_obj.Move(float(item["x"]), float(item["y"]), UNIT_MM)
            moved += 1
        except Exception as exc:
            failures.append({"refdes": item["refdes"], "error": str(exc)[-120:]})
    try:
        doc.Save()
        saved = True
    except Exception as exc:
        raise _com_error(exc, "save_after_labels") from exc
    result.update({"moved": moved, "failures": failures, "applied": True, "saved": saved})
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


def _mounting_holes(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Read, and with `apply` place, mounting holes: one in each corner of the board
    outline, `inset` mm from both edges, through `Document.PutMountingHoleEx` with the
    central library's `MH-C<diameter>-NONPLATED` padstack (the library build and the
    KiCad import both create the 2.2 mm one). A corner that already has a hole within
    0.5 mm is left alone."""
    from .library_hkp import _num

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
    padstack = f"MH-C{_num(diameter)}-NONPLATED"
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
        # the holes follow the outline: after `pcb outline` grew the board, the old
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
    """Read, and with `apply` replace, the board outline: a `width` × `height` mm
    rectangle from the origin through `Document.PutBoardOutline`, with the route
    border 0.3 mm inside it and the manufacturing outline on it, because the
    template's copies of those keep their old size otherwise."""
    pcb_path = _layout_board_path(params)
    width = params.get("width")
    height = params.get("height")
    apply = bool(params.get("apply", False))
    try:
        radius_mm = float(params.get("radius") or 0.0)
    except (TypeError, ValueError) as exc:
        raise AdapterError("E_USAGE", "radius is millimetres") from exc
    if apply or width is not None or height is not None:
        try:
            width_mm, height_mm = float(width), float(height)
        except (TypeError, ValueError) as exc:
            raise AdapterError("E_USAGE", "width and height are millimetres") from exc
        if width_mm <= 0 or height_mm <= 0:
            raise AdapterError("E_VALIDATION", "width and height must be positive")
        if radius_mm < 0 or radius_mm >= min(width_mm, height_mm) / 2:
            raise AdapterError(
                "E_VALIDATION", "the corner radius must be below half of the shorter side"
            )
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    before = _board_rect(doc)
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "before": {"min_x": before[0], "min_y": before[1], "max_x": before[2], "max_y": before[3]},
        "unit": "mm",
        "applied": False,
        "saved": False,
        "prompts": prompts,
        "_untrusted": ["pcb", "prompts"],
    }
    if width is not None and height is not None:
        result["requested"] = {"width": width_mm, "height": height_mm, "radius": radius_mm}
    if not apply:
        return result
    rows = _rounded_points(0.0, 0.0, width_mm, height_mm, radius_mm)
    try:
        doc.PutBoardOutline(len(rows[0]), rows, 0.15, UNIT_MM)
    except Exception as exc:
        raise _com_error(exc, "put_board_outline") from exc
    companions: dict[str, Any] = {}
    inset = ROUTE_BORDER_INSET_MM
    try:
        border = _rounded_points(
            inset, inset, width_mm - inset, height_mm - inset, max(radius_mm - inset, 0.0)
        )
        doc.PutRouteBorder(len(border[0]), border, 0.15, UNIT_MM)
        companions["route_border"] = {"inset": inset, "radius": max(radius_mm - inset, 0.0)}
    except Exception as exc:
        companions["route_border"] = {"error": str(exc)[:120]}
    try:
        doc.PutManufacturingOutline(len(rows[0]), rows, UNIT_MM)
        companions["manufacturing_outline"] = {"inset": 0.0, "radius": radius_mm}
    except Exception as exc:
        companions["manufacturing_outline"] = {"error": str(exc)[:120]}
    try:
        doc.Save()
    except Exception as exc:
        raise _com_error(exc, "save_after_outline") from exc
    after = _board_rect(doc)
    result.update(
        {
            "after": {"min_x": after[0], "min_y": after[1], "max_x": after[2], "max_y": after[3]},
            "companions": companions,
            "applied": True,
            "saved": True,
        }
    )
    return result


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
    """A plane shape for `net` on `layer`, inset by `margin` from the outline's rectangle
    (`Document.PutPlaneShape`); `apply` false only lists the shapes the board has."""
    pcb_path = _layout_board_path(params)
    net_name = str(params.get("net") or "GND")
    try:
        layer = int(params.get("layer") or 2)
        margin = float(params.get("margin") if params.get("margin") is not None else 1.0)
    except (TypeError, ValueError) as exc:
        raise AdapterError("E_USAGE", "layer is an integer and margin millimetres") from exc
    apply = bool(params.get("apply", False))
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    layers = int(_value(doc, "LayerCount", default=0) or 0)
    if layers and not 1 <= layer <= layers:
        raise AdapterError(
            "E_VALIDATION",
            "layer is outside the board's stackup",
            {"layer": layer, "layers": layers},
        )
    net = next(
        (
            candidate
            for candidate in _items(_com_member(doc, "Nets"))
            if str(_value(candidate, "Name", default="")) == net_name
        ),
        None,
    )
    if net is None:
        raise AdapterError("E_NOT_FOUND", "the board has no such net", {"net": net_name})
    min_x, min_y, max_x, max_y = _board_rect(doc)
    radius = max(_outline_radius(doc) - margin, 0.0)
    replace = bool(params.get("replace", False))
    existing = _plane_shapes(doc)
    duplicates = [item for item in existing if item["net"] == net_name and item["layer"] == layer]
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "net": net_name,
        "layer": layer,
        "layers": layers,
        "margin": margin,
        "rectangle": {
            "min_x": min_x + margin,
            "min_y": min_y + margin,
            "max_x": max_x - margin,
            "max_y": max_y - margin,
            "radius": radius,
        },
        "existing": existing,
        "replace": replace,
        "applied": False,
        "saved": False,
        "prompts": prompts,
        "_untrusted": ["pcb", "existing", "prompts"],
    }
    if duplicates and not replace:
        raise AdapterError(
            "E_CONFLICT",
            "the board already has a plane shape for this net on this layer (replace it)",
            {"net": net_name, "layer": layer},
        )
    if not apply:
        return result
    removed = 0
    if duplicates:
        # the shape follows the outline; after `pcb outline` changed it, the old one
        # can stick out past a rounded corner
        for shape in list(_items(_com_member(doc, "PlaneShapes"))):
            try:
                shape_net = _value(shape, "Net", default=None)
                name = str(_value(shape_net, "Name", default="")) if shape_net else ""
                if name == net_name and _value(shape, "Layer", default=None) == layer:
                    shape.Delete()
                    removed += 1
            except Exception as exc:
                raise _com_error(exc, "delete_plane_shape") from exc
    result["removed"] = removed
    points = _rounded_points(min_x + margin, min_y + margin, max_x - margin, max_y - margin, radius)
    try:
        # the component argument must be None: win32com cannot turn the typelib's
        # default 0 into an IDispatch pointer
        # bRouteObstruct False: the pour neither blocks the router nor, laid over
        # traces already routed, counts as a DRC violation ("DRC 违反" stops the
        # call otherwise); the copper flows around the traces when the plane data
        # is generated
        shape = doc.PutPlaneShape(
            layer, len(points[0]), points, net, False, 0, 0.0, 0.0, None, UNIT_MM
        )
    except Exception as exc:
        raise _com_error(exc, "put_plane_shape") from exc
    unobstructed = _unobstruct_planes(doc, net_name, layer, shape)
    generated = _generate_planes(doc, net_name)
    try:
        doc.Save()
    except Exception as exc:
        raise _com_error(exc, "save_after_pour") from exc
    result.update(
        {
            "applied": True,
            "saved": True,
            "shapes": _plane_shapes(doc),
            "generated": generated,
            "route_unobstructed": unobstructed,
        }
    )
    return result


def _unobstruct_planes(doc: Any, net_name: str, layer: int, shape: Any = None) -> int:
    """Clear `RouteObstructed` on the net's plane shapes of `layer`; how many changed."""
    changed = 0
    candidates = [shape] if shape is not None else []
    candidates += list(_items(_com_member(doc, "PlaneShapes")))
    for item in candidates:
        try:
            item_net = _value(item, "Net", default=None)
            name = str(_value(item_net, "Name", default="")) if item_net else ""
            if name != net_name or int(_value(item, "Layer", default=0) or 0) != layer:
                continue
            if bool(_value(item, "RouteObstructed", default=False)):
                item.RouteObstructed = False
                changed += 1
        except Exception:
            continue
    return changed


PLANE_DATA_DYNAMIC = 1  # epcbPlaneDataStateDynamic; 3 is Draft, shapes without copper


def _generate_planes(doc: Any, net_name: str) -> dict[str, Any]:
    """Turn the net's plane assignments from Draft to Dynamic, which makes Layout
    generate the plane data (the copper with its thermal ties); a shape left in Draft
    connects nothing, and every via to it counts as dangling in Batch DRC."""
    switched = 0
    for assignment in _items(_com_member(doc, "PlaneAssignments")):
        net = _value(assignment, "Net", default=None)
        if net is None or str(_value(net, "Name", default="")) != net_name:
            continue
        try:
            if int(_value(assignment, "PlaneDataState", default=0) or 0) != PLANE_DATA_DYNAMIC:
                assignment.PlaneDataState = PLANE_DATA_DYNAMIC
                switched += 1
        except Exception:
            continue
    _settle(3.0)
    planes = _com_member(doc, "GeneratedPlanes")
    try:
        count = int(planes.Count) if planes is not None else None
    except Exception:
        count = None
    return {"assignments_switched": switched, "generated_planes": count}


PLANE_DATA_DRAFT = 3  # epcbPlaneDataStateDraft


def _regenerate_planes(doc: Any) -> int:
    """Regenerate every plane's data (Draft, then Dynamic again); how many were toggled.

    Dynamic plane data does not follow vias the router adds afterwards: Batch DRC
    reported the ground vias dangling and the ground net partial until the plane
    was generated again.
    """
    toggled = 0
    for assignment in _items(_com_member(doc, "PlaneAssignments")):
        try:
            # a plane found back in Draft (the state the board on KiCad footprints showed
            # after its pour was saved) is simply generated; a Dynamic one is toggled
            if int(_value(assignment, "PlaneDataState", default=0) or 0) == PLANE_DATA_DYNAMIC:
                assignment.PlaneDataState = PLANE_DATA_DRAFT
                _settle(1.0)
            assignment.PlaneDataState = PLANE_DATA_DYNAMIC
            toggled += 1
        except Exception:
            continue
    if toggled:
        _settle(4.0)
    return toggled


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


def _hazard_record(hazard: Any, names: dict[int, str]) -> dict[str, Any]:
    kind = _value(hazard, "Type", default=None)
    record: dict[str, Any] = {
        "type": kind,
        "kind": names.get(int(kind), str(kind)) if isinstance(kind, int) else str(kind),
        "description": str(_value(hazard, "Description", default="")).strip(),
        "accepted": bool(_value(hazard, "Accepted", default=False)),
    }
    try:
        record["x"] = round(float(hazard.GetPositionX(UNIT_MM)), 3)
        record["y"] = round(float(hazard.GetPositionY(UNIT_MM)), 3)
    except Exception:
        pass
    try:
        record["required"] = round(float(hazard.GetRequiredClearance(UNIT_MM)), 3)
        record["actual"] = round(float(hazard.GetActualClearance(UNIT_MM)), 3)
    except Exception:
        pass
    objects: list[str] = []
    try:
        for item in _items(_com_member(hazard, "Objects")):
            objects.append(
                str(
                    _value(item, "RefDes", default=None)
                    or _value(item, "Name", default=None)
                    or type(item).__name__
                )
            )
    except Exception:
        pass
    record["objects"] = objects[:6]
    return record


def _hazards(doc: Any, client: Any, kind: int) -> list[dict[str, Any]]:
    names = _hazard_type_names(client)
    try:
        collection = doc.GetHazards(kind)
    except Exception as exc:
        raise _com_error(exc, "get_hazards") from exc
    return [_hazard_record(item, names) for item in _items(collection)]


def _processing_windows() -> int:
    from . import win_dialogs

    return sum(
        1
        for window in win_dialogs.top_windows("expeditionpcb.exe")
        if "processing" in window["title"].lower() or "处理" in window["title"]
    )


def _batch_drc(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Run Layout's Batch DRC and read the hazards back.

    There is no automation call for it and its driver refuses a command line, but the
    menu command runs through `Gui.ProcessCommand(32769)`; the dialog it opens is
    answered from a helper thread and the run is waited out. Hazards then come from
    `Document.GetHazards(epcbHazardTypeBatch)`, each with its type name, description,
    position, the objects involved and, for clearance checks, the required and actual
    distance. Without `run`, the hazards already on the board are reported.
    """
    pcb_path = _layout_board_path(params)
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    ran = False
    seconds = 0.0
    if bool(params.get("run", True)):
        started = time.monotonic()
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
            # the dialog is pressed by the thread; then a "Processing..." window shows
            # until the checks are done
            time.sleep(4.0)
            deadline = time.monotonic() + float(params.get("timeout", 600.0))
            while time.monotonic() < deadline:
                if _processing_windows() == 0 and time.monotonic() - started > 6.0:
                    break
                time.sleep(1.0)
        prompts = prompts + answerer.answered
        seconds = round(time.monotonic() - started, 1)
        ran = True
    hazards = _hazards(doc, client, HAZARDS_BATCH)
    online = _hazards(doc, client, HAZARDS_ONLINE) if bool(params.get("online", False)) else []
    summary: dict[str, int] = {}
    for item in hazards:
        summary[item["kind"]] = summary.get(item["kind"], 0) + 1
    errors = sum(1 for item in hazards if item["kind"] not in DRC_WARNING_KINDS)
    return {
        "pcb": str(pcb_path),
        "ran": ran,
        "seconds": seconds,
        "count": len(hazards),
        "summary": summary,
        "hazards": hazards,
        "online": online,
        "clean": not hazards,
        "errors": errors,
        "warnings": len(hazards) - errors,
        "passes": errors == 0,
        "prompts": prompts,
        "_untrusted": ["pcb", "hazards", "online", "prompts"],
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
    """Write `Top View.dcs` next to the board's other schemes if it is missing; True
    when it was written now (Layout lists a new scheme only after the board reopens)."""
    target = config / TOP_VIEW_FILE
    if target.is_file():
        return False
    source = config / "All On.dcs"
    if not source.is_file():
        raise AdapterError(
            "E_NOT_FOUND",
            "the board has no All On display scheme to start from",
            {"config": str(config)},
        )
    target.write_text(
        top_view_scheme_text(source.read_text(encoding="utf-8", errors="replace"), layers),
        encoding="utf-8",
    )
    return True


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


def _show_board(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Put the board in front of the person with a display scheme that shows parts.

    The stock templates open with the "Loc: Assembly Bottom" scheme, under which a
    board full of top-side parts looks empty. Layout exposes the scheme names
    (`Document.DisplaySchemes`) but no setter, so the toolbar combo is driven
    through UI Automation. With `output`, the window is captured to a PNG.
    """
    from . import win_dialogs

    pcb_path = _layout_board_path(params)
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
    scheme = str(params.get("scheme") or DEFAULT_DISPLAY_SCHEME)
    top_view = bool(params.get("top_view", False))
    if top_view:
        scheme = TOP_VIEW_SCHEME
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    scheme_written = False
    if top_view:
        layers = int(_value(doc, "LayerCount", default=2) or 2)
        scheme_written = _ensure_top_view_scheme(pcb_path.parent / "Config", layers)
        if scheme_written:
            # Layout fills its scheme list when the board opens
            _close_board_if_open(client, pcb_path)
            _settle(3.0)
            _doc, reopened = _open_layout_document(app, pcb_path)
            prompts = prompts + reopened
            doc = _licensed_document(app)
    schemes = [str(name) for name in (_com_member(doc, "DisplaySchemes") or ())]
    if schemes and scheme not in schemes:
        raise AdapterError(
            "E_NOT_FOUND", "unknown display scheme", {"scheme": scheme, "available": schemes}
        )
    try:
        app.Visible = True
    except Exception:
        pass
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "scheme": scheme,
        "scheme_applied": False,
        "scheme_written": scheme_written,
        "schemes": schemes,
        "components": _board_counts(doc).get("components"),
        "prompts": prompts,
        "_untrusted": ["pcb", "schemes", "prompts"],
    }
    # the scheme and the fit go through automation: `ActiveView.DisplayControl.LoadScheme`
    # (its `Name` reads the scheme back) and `ActiveView.SetExtentsToBoard`; the toolbar
    # (UI Automation) is only the fallback
    result["scheme_applied"] = _load_display_scheme(doc, scheme)
    result["fitted"] = _fit_board_view(app, doc)
    try:
        window = win_dialogs.main_window("expeditionpcb.exe")
        if window is not None:
            result["foreground"] = bool(win_dialogs.bring_to_front(int(window["hwnd"])))
            time.sleep(1.0)
            if not result["scheme_applied"]:
                picked = win_dialogs.choose_combo_item(
                    "expeditionpcb.exe", "CMD_DISPLAY_SCHEMES", scheme
                )
                result["scheme_applied"] = picked is not None
            if not result["fitted"]:
                result["fitted"] = bool(
                    win_dialogs.press_control("expeditionpcb.exe", "VIEW_FITBOARD")
                )
            time.sleep(1.5)
            window = win_dialogs.main_window("expeditionpcb.exe") or window
            result["window"] = {
                key: window[key] for key in ("title", "left", "top", "width", "height")
            }
            if output_path is not None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                result["screenshot"] = win_dialogs.capture_window(
                    int(window["hwnd"]), str(output_path)
                )
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot capture the window: {exc}") from exc
    return result


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
ROUTE_ORDER_AUTO = 0  # epcbARAutoOrder
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
    """Routed nets, open connections, traces and vias on the board."""
    nets: list[dict[str, Any]] = []
    for net in _items(_com_member(doc, "Nets")):
        name = str(_value(net, "Name", default=""))
        routed = bool(_com_member(net, "IsRouted"))
        opens = _com_member(net, "NumberOfOpens")
        nets.append({"net": name, "routed": routed, "opens": int(opens or 0)})
    traces = _com_member(doc, "Traces")
    vias = _com_member(doc, "Vias")
    return {
        "nets": len(nets),
        "routed": sum(1 for item in nets if item["routed"]),
        "opens": sum(item["opens"] for item in nets),
        "traces": int(traces.Count) if traces is not None else None,
        "vias": int(vias.Count) if vias is not None else None,
        "unrouted": [item for item in nets if not item["routed"]],
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


def _route_board(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Run Layout's autorouter passes on the board (`Document.NewRoutePass`).

    Each pass object takes a type (`PassType`), the nets to work on (`Items`), an
    order and a configuration, and runs with `Go`; the stock passes for a first
    board are Route (effort 1 to 5), Via Min and Smooth. Without `apply` only the
    routing state is reported. `layers` names the layers the passes may use: the outer
    two are always the router's (`RoutePass.LayerSelect` calls them invalid), so the
    list decides which inner layers join — `1,4` keeps every trace on the outside,
    where a four-layer template otherwise routes on three layers.
    """
    pcb_path = _layout_board_path(params)
    passes = parse_route_passes(str(params.get("passes") or DEFAULT_ROUTE_PASSES))
    layers = parse_route_layers(params.get("layers"))
    unroute = bool(params.get("unroute", False))
    apply = bool(params.get("apply", False))
    app = _application(client, attach_only=not bool(params.get("start", True)))
    _doc, prompts = _open_layout_document(app, pcb_path)
    doc = _licensed_document(app)
    before = _route_status(doc)
    result: dict[str, Any] = {
        "pcb": str(pcb_path),
        "passes": passes,
        "layers": layers,
        "unroute": unroute,
        "before": {key: value for key, value in before.items() if key != "unrouted"},
        "unrouted_before": [item["net"] for item in before["unrouted"]],
        "applied": False,
        "saved": False,
        "prompts": prompts,
        "_untrusted": ["pcb", "unrouted_before", "prompts"],
    }
    if not apply:
        return result
    if unroute:
        # start over: the passes only work on open connections, so a board routed on
        # three layers keeps its traces unless they are deleted first
        try:
            result["deleted"] = _delete_routing(doc)
        except Exception as exc:
            raise _com_error(exc, "delete_routing") from exc
    runs: list[dict[str, Any]] = []
    for item in passes:
        try:
            route_pass = doc.NewRoutePass()
            route_pass.PassType(item["type"], item["effort"][0], item["effort"][1], False, False)
            route_pass.Items(ROUTE_ITEMS_ALL_NETS, None)
            route_pass.Order(ROUTE_ORDER_AUTO, 0)
            route_pass.Config(False, True)
            if layers:
                # LayerSelect takes the inner layers only (the outer two are always
                # the router's and are "invalid parameters"), so `layers` decides
                # which inner layers join; `1,4` keeps the routing on the outside
                count = int(_value(doc, "LayerCount", default=0) or 0)
                for number in range(2, count):
                    route_pass.LayerSelect(number, number in layers)
        except Exception as exc:
            raise _com_error(exc, f"route_pass {item['pass']}") from exc
        started = time.monotonic()
        with _PromptAnswerer() as answerer:
            try:
                route_pass.Go()
            except Exception as exc:
                raise _com_error(exc, f"route_pass_go {item['pass']}") from exc
        status = _route_status(doc)
        runs.append(
            {
                **item,
                "seconds": round(time.monotonic() - started, 1),
                "routed": status["routed"],
                "opens": status["opens"],
                "traces": status["traces"],
                "vias": status["vias"],
                "prompts": answerer.answered,
            }
        )
    # vias the router added only tie into a plane after its data is generated again
    regenerated = _regenerate_planes(doc)
    after = _route_status(doc)
    if after["opens"] and any(item["pass"] == "route" for item in passes):
        # a pour on a signal layer may leave a pin cut off once the copper flows
        # around the new traces: one more round routes what is still open
        for item in passes:
            if item["pass"] not in ("route", "viamin", "smooth"):
                continue
            try:
                route_pass = doc.NewRoutePass()
                route_pass.PassType(
                    item["type"], item["effort"][0], item["effort"][1], False, False
                )
                route_pass.Items(ROUTE_ITEMS_ALL_NETS, None)
                route_pass.Order(ROUTE_ORDER_AUTO, 0)
                route_pass.Config(False, True)
                if layers:
                    count = int(_value(doc, "LayerCount", default=0) or 0)
                    for number in range(2, count):
                        route_pass.LayerSelect(number, number in layers)
                started = time.monotonic()
                with _PromptAnswerer() as answerer:
                    route_pass.Go()
            except Exception as exc:
                raise _com_error(exc, f"route_pass_again {item['pass']}") from exc
            status = _route_status(doc)
            runs.append(
                {
                    **item,
                    "round": 2,
                    "seconds": round(time.monotonic() - started, 1),
                    "routed": status["routed"],
                    "opens": status["opens"],
                    "traces": status["traces"],
                    "vias": status["vias"],
                    "prompts": answerer.answered,
                }
            )
        regenerated += _regenerate_planes(doc)
        after = _route_status(doc)
    try:
        doc.Save()
        saved = True
    except Exception as exc:
        raise _com_error(exc, "save_after_route") from exc
    result.update(
        {
            "runs": runs,
            "planes_regenerated": regenerated,
            "after": {key: value for key, value in after.items() if key != "unrouted"},
            "traces_by_layer": _traces_by_layer(doc),
            "unrouted": after["unrouted"],
            "complete": after["opens"] == 0,
            "applied": True,
            "saved": saved,
        }
    )
    result["_untrusted"] = ["pcb", "unrouted_before", "unrouted", "runs", "prompts"]
    return result


def _forward_annotate(params: dict[str, Any], client: Any) -> dict[str, Any]:
    """Forward-annotate the packaged schematic into the board through Project Integration.

    `Document.ProjectIntegration` is a dispatch object without type information, so
    its members are reached by name: reading `ForwardAnnotate` runs it (the packager,
    Database Load and the netload, about half a minute) and yields a boolean. A
    failure is a COM error whose reasons are in `PCB/LogFiles/ForwardAnnotation.txt`.
    """
    pcb_path = _layout_board_path(params)
    # Layout's session with the project database must be younger than Designer's:
    # after `library build` cycled the Designer project, a board that stayed open in
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
        summary = _annotation_summary(log_path)
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
            "saved": False,
            "log": str(log_path),
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
    # Layout asks a question mentioning 正向标注 during the run; the default rules
    # would decline it and the annotation would end as "failed". The first call on a
    # freshly opened board fails in its packaging phase and the second one succeeds
    # (verified twice on XPED2604), so one retry is part of the operation.
    attempts: list[dict[str, Any]] = []
    outcome: Any = None
    answered: list[dict[str, Any]] = []
    # the run is retried twice; the failures that motivated the retry turned out to
    # be the answerer thread ending the packager's progress box, fixed since
    retry_waits = (5.0, 15.0)
    try:
        for attempt in (1, 2, 3):
            with _PromptAnswerer(LAYOUT_PROMPTS_ANNOTATE) as answerer:
                try:
                    outcome = integration.ForwardAnnotate
                    if callable(outcome):
                        outcome = outcome()
                    attempts.append({"attempt": attempt, "ok": True})
                    answered.extend(answerer.answered)
                    break
                except Exception as exc:
                    attempts.append({"attempt": attempt, "ok": False, "reason": str(exc)[:200]})
                    answered.extend(answerer.answered)
                    if attempt == 3:
                        summary = _annotation_summary(log_path)
                        raise AdapterError(
                            "E_VALIDATION",
                            "forward annotation failed",
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
                            },
                        ) from exc
            _settle(retry_waits[min(attempt, len(retry_waits)) - 1])
    finally:
        if designer_reopen and designer_app is not None and project_path is not None:
            try:
                _ensure_project(designer_app, project_path)
            except AdapterError:
                pass
    summary = _annotation_summary(log_path)
    after = _com_member(integration, "ForwardAnnotationStatus")
    saved = False
    if bool(params.get("save", True)):
        try:
            doc.Save()
            saved = True
        except Exception as exc:
            raise _com_error(exc, "save_after_annotation") from exc
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
        "log": str(log_path),
        "board": _board_counts(doc),
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
    library = str(params.get("library") or "Case")
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
            for name, text in symbols.items():
                (target / f"{name}.1").write_text(str(text), encoding="utf-8")
                written.append(str(name))
        except OSError as exc:
            raise AdapterError("E_IO", f"cannot write symbol files: {exc}") from exc
    app = _viewdraw_application(client, attach_only=True)
    if not _prj_lists(project_path, f"SymbolLibs\\{library}"):
        # Designer searches the symbol partitions its .prj lists; add ours with the
        # project closed, or the edit is lost when Designer writes the file back
        _close_if_open(app, project_path)
        _ensure_prj_list(project_path, "Symbols", f"SymbolLibs\\{library}")
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
    with _PromptAnswerer(DESIGNER_PROMPTS, process_name="viewdraw.exe") as prompts:
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
                        for _attempt in range(2):
                            if int(app.ActiveView.Block.SheetSize) == int(op["size"]):
                                break
                            app.ActiveView.Block.SheetSize = int(op["size"])
                            _settle(1.0)
                            _ensure_sheet(app, current_sheet)
                        if int(app.ActiveView.Block.SheetSize) != int(op["size"]):
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
                    app.ActiveDocument.Save()
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
                dialogs = prompts.blocking_dialogs()
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
    """Put a sheet in front of the person: activate it, fit it, raise Designer's window.

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
    app = _viewdraw_application(client, attach_only=True)
    _ensure_project(app, project_path)
    known = _sheet_numbers(app)
    if sheet not in known:
        raise AdapterError(
            "E_NOT_FOUND", f"sheet {sheet} does not exist", {"sheets": sorted(known)}
        )
    _activate_sheet(app, sheet)
    if _active_sheet(app) != sheet:
        raise AdapterError(
            "E_CONFLICT",
            f"sheet {sheet} could not be activated",
            {"active_sheet": _active_sheet(app)},
        )
    try:
        app.ExecuteCommandByID(_FIT_ALL_COMMAND)
    except Exception as exc:
        raise _com_error(exc, "fit_all") from exc
    time.sleep(0.8)
    result: dict[str, Any] = {
        "sheet": sheet,
        "sheet_size": None,
        "foreground": False,
        "window": None,
        "screenshot": None,
        "_untrusted": ["window", "screenshot"],
    }
    try:
        result["sheet_size"] = int(app.ActiveView.Block.SheetSize)
    except Exception:
        pass
    try:
        from . import win_dialogs

        window = win_dialogs.main_window("viewdraw.exe")
        if window is not None:
            result["foreground"] = bool(win_dialogs.bring_to_front(int(window["hwnd"])))
            time.sleep(1.0)
            # re-read the geometry: a minimised window reports -32000 until restored
            window = win_dialogs.main_window("viewdraw.exe") or window
            result["window"] = {k: window[k] for k in ("title", "left", "top", "width", "height")}
            if output_path is not None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                result["screenshot"] = win_dialogs.capture_window(
                    int(window["hwnd"]), str(output_path)
                )
    except OSError as exc:
        raise AdapterError("E_IO", f"cannot capture the window: {exc}") from exc
    return result


def dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    pythoncom, client = _import_com()
    pythoncom.CoInitialize()
    # before anything binds a running application through a cached wrapper
    _heal_gen_py_cache()
    try:
        if method == "health":
            sdd_home = _configure_environment()
            registered = _com_registered()
            designer_registered = _viewdraw_registered()
            running = False
            designer_running = False
            dialog_suppression: dict[str, bool] = {}
            if registered:
                try:
                    app = _active_object(client)
                    running = True
                    dialog_suppression = _quiet_gui(app, "pcb")
                except AdapterError:
                    running = False
            if designer_registered:
                try:
                    _viewdraw_active(client)
                    designer_running = True
                except AdapterError:
                    designer_running = False
            return {
                "ready": bool(sdd_home and (registered or designer_registered)),
                "application_running": running,
                "designer_application_running": designer_running,
                "sdd_home": str(sdd_home) if sdd_home else None,
                "automation_progid": "MGCPCB.ExpeditionPCBApplication",
                "com_registered": registered,
                "designer_com_registered": designer_registered,
                # Startup dialogs fire before automation exists and are not covered here.
                "dialog_suppression": dialog_suppression,
                "reason": (
                    None
                    if sdd_home and (registered or designer_registered)
                    else "Xpedition installation or COM registration is incomplete"
                ),
            }
        if method == "start":
            domain = _domain(params)
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
                raise _com_error(exc, "start_xpedition") from exc
            ready = _wait_for_native_ready(
                process, client, domain, float(params.get("startup_wait", 5.0))
            )
            return {
                "started": True,
                "domain": domain,
                "pid": process.pid,
                "executable": str(executable),
                "automation_ready": ready,
                "automation_progid": (
                    "Viewdraw.Application"
                    if domain == "schematic"
                    else "MGCPCB.ExpeditionPCBApplication"
                ),
            }
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
                "automation_progid": (
                    "Viewdraw.Application" if domain == "schematic" else "MGCPCB.Application"
                ),
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
            domain = _domain(params)
            app = (
                _viewdraw_application(client, attach_only=not bool(params.get("project")))
                if domain == "schematic"
                else _application(client, attach_only=not bool(params.get("project")))
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
                document = _value(app, "ActiveDocument", default=None)
                if document is not None:
                    document.Save()
                else:
                    app.CloseProject()
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
                else _application(client, attach_only=True)
            )
            doc = _value(app, "ActiveDocument", default=None)
            if domain == "schematic" and bool(params.get("document_only", False)):
                if doc is not None:
                    doc.Close()
            elif doc is not None and bool(params.get("document_only", False)):
                doc.Close(bool(params.get("save", False)))
            else:
                _quit_and_confirm(client, app, domain)
            return {"closed": True, "domain": domain}
        if method == "apply_changeset":
            operations = params.get("operations")
            if not isinstance(operations, list) or not operations:
                raise AdapterError(
                    "E_CHANGESET_INVALID", "native apply_changeset requires operations"
                )
            domain = _domain(params)
            if domain == "schematic":
                app = _viewdraw_application(client, attach_only=not bool(params.get("project")))
                _open_designer_project(app, params)
                try:
                    design_name = str(app.GetActiveDesign() or "")
                except Exception:
                    design_name = str(params.get("design") or "")
                deferred_nets: dict[str, dict[str, Any]] = {}
                applied = []
                for item in operations:
                    if not isinstance(item, dict):
                        raise AdapterError(
                            "E_CHANGESET_INVALID", "each native operation must be an object"
                        )
                    result = _apply_designer_operation(
                        app, item, client, design_name, deferred_nets
                    )
                    if result is not None:
                        applied.append(result)
                        if result.get("type") == "create_net":
                            continue
                        for prior in applied:
                            if (
                                prior.get("type") == "create_net"
                                and prior.get("net") == result.get("net")
                                and prior.get("deferred")
                            ):
                                prior["applied"] = True
                                prior["deferred"] = False
                for name, operation in deferred_nets.items():
                    net = _add_schematic_net(app.ActiveView.Block, operation, client=client)
                    _label_schematic_net(net, name, operation)
                    for prior in applied:
                        if prior.get("type") == "create_net" and prior.get("net") == name:
                            prior["applied"] = True
                            prior["deferred"] = False
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
            if bool(params.get("save", True)) and domain == "schematic":
                try:
                    app.CloseProject()
                except Exception:
                    pass
            return {"applied": applied, "count": len(applied), "domain": domain}
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
        if method == "kicad_import":
            return _kicad_import(params, client)
        if method == "pcb_create":
            return _pcb_create(params, client)
        if method == "forward_annotate":
            return _forward_annotate(params, client)
        if method == "arrange_components":
            return _arrange_components(params, client)
        if method == "show_board":
            return _show_board(params, client)
        if method == "board_outline":
            return _board_outline(params, client)
        if method == "mounting_holes":
            return _mounting_holes(params, client)
        if method == "manufacturing_output":
            return _manufacturing_output(params, client)
        if method == "net_rules":
            return _net_rules(params, client)
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
        if method == "board_geometry":
            return _board_geometry(params, client)
        if method == "tidy_labels":
            return _tidy_labels(params, client)
        if method == "plane_pour":
            return _plane_pour(params, client)
        if method == "route_board":
            return _route_board(params, client)
        if method == "batch_drc":
            return _batch_drc(params, client)
        raise AdapterError("E_USAGE", f"unknown native adapter method: {method}")
    finally:
        pythoncom.CoUninitialize()


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
        result = _response(dispatch(method, params))
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
                {"type": type(exc).__name__, "error": str(exc)},
            )
        )
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    # Bytes, not text: the parent decodes UTF-8 whatever the console code page is.
    sys.stdout.buffer.write(payload.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
