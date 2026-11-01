"""Read registered versioned COM identities without activation or license secrets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from typing import Any

_DIAGNOSTIC_CONTENT_CACHE = ContextVar("xpedition_diagnostic_content", default=None)

_DIAGNOSTIC_SOURCE_FILES = (
    "__init__.py",
    "native_identity.py",
    "native_com_adapter.py",
    "native_close_diagnostic.py",
    "close_diagnostic.py",
    "closed_copy_startup.py",
    "empty_close_admission.py",
    "board_component_connect.py",
    "native_component_transaction.py",
    "diagnostic_exit.py",
    "pcb_document_guard.py",
    "component_copy_origin.py",
    "driver.py",
    "drc_failure_observation.py",
    "drc_engine.py",
    "project_isolated_copy.py",
    "project_backup.py",
    "project_file.py",
    "native_process.py",
    "native_observation_stage.py",
    "session_ownership.py",
    "session.py",
    "audit.py",
    "confirmation_store.py",
    "errors.py",
    "models.py",
    "resources.py",
    "operation_budget.py",
    "cell_refresh.py",
    "cli/diagnostics.py",
    "cli/registry.py",
    "cli/sessions.py",
    "backends/native_xpedition.py",
)


def _content_hash(path: Path, limit: int = 64 * 1024 * 1024) -> str:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or getattr(before, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or before.st_size > limit
    ):
        raise ValueError("diagnostic_content_not_bounded_regular_file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    after = path.lstat()
    if (
        len(raw) > limit
        or len(raw) != before.st_size
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ValueError("diagnostic_content_changed_during_read")
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def diagnostic_content_request():
    """Share byte observations only within a single public or worker request."""
    token = _DIAGNOSTIC_CONTENT_CACHE.set({})
    try:
        yield
    finally:
        _DIAGNOSTIC_CONTENT_CACHE.reset(token)


def invalidate_diagnostic_content():
    """Require a fresh byte observation at a diagnostic safety boundary."""
    cache = _DIAGNOSTIC_CONTENT_CACHE.get()
    if cache is not None:
        cache.clear()


def _diagnostic_content_binding(package: Path, executable: Path, frozen: bool, build_hash: str):
    cache = _DIAGNOSTIC_CONTENT_CACHE.get()
    if cache is None:
        return _diagnostic_content_binding_uncached(package, executable, frozen, build_hash)
    key = (
        str(package),
        str(executable),
        frozen,
        build_hash,
        getattr(sys, "_MEIPASS", None),
    )
    if key not in cache:
        cache[key] = _diagnostic_content_binding_uncached(package, executable, frozen, build_hash)
    # Evidence recipients must not be able to change the cached observation.
    return deepcopy(cache[key])


def _diagnostic_content_binding_uncached(
    package: Path, executable: Path, frozen: bool, build_hash: str
):
    """Observe code and resources by bytes; paths remain separate provenance."""
    from . import resources

    vendor = package / "_vendor"
    lock_path = vendor / "vendor-lock.json"
    lock_hash = _content_hash(lock_path)
    lock_bytes = lock_path.read_bytes()
    if hashlib.sha256(lock_bytes).hexdigest() != lock_hash:
        raise ValueError("diagnostic_vendor_lock_changed")
    lock = json.loads(lock_bytes)
    entries = lock["files"]
    if not isinstance(entries, dict) or not entries or len(entries) > 128:
        raise ValueError("diagnostic_vendor_lock_invalid")
    vendor_hashes, vendor_bytes = {}, 0
    for relative, expected in sorted(entries.items()):
        path = Path(relative)
        if not isinstance(relative, str) or path.is_absolute() or ".." in path.parts:
            raise ValueError("diagnostic_vendor_path_invalid")
        vendor_bytes += (vendor / path).lstat().st_size
        if vendor_bytes > 128 * 1024 * 1024:
            raise ValueError("diagnostic_vendor_byte_limit")
        observed = _content_hash(vendor / path)
        if observed != expected:
            raise ValueError("diagnostic_vendor_bytes_changed")
        vendor_hashes[relative] = observed
    contract = resources.locate("contract/driver-v2/product_contract.json")
    if contract is None:
        raise ValueError("diagnostic_product_contract_missing")
    if frozen:
        internal = getattr(sys, "_MEIPASS", None)
        if not internal:
            raise ValueError("diagnostic_frozen_payload_location_unavailable")
        if internal and os.path.normcase(os.path.abspath(internal)) == os.path.normcase(
            os.path.abspath(str(executable.parent / "_internal"))
        ):
            from .designer_frozen_binding import frozen_owner_binding

            bundle = frozen_owner_binding()
            code = {
                "scheme": "verified-onedir",
                "payload_tree_sha256": bundle["payload_tree_sha256"],
            }
        else:
            code = {"scheme": "onefile-executable", "executable_sha256": build_hash}
    else:
        code = {
            "scheme": "loaded-source-files",
            "files": {name: _content_hash(package / name) for name in _DIAGNOSTIC_SOURCE_FILES},
        }
    return {
        "scheme": "close-diagnostic-content-v1",
        "code": code,
        "runtime": {
            "executable_sha256": build_hash
            if frozen
            else _content_hash(executable, 256 * 1024 * 1024),
            "implementation": sys.implementation.name,
            "version": sys.version,
        },
        "vendor_lock_sha256": lock_hash,
        "vendor_files_sha256": hashlib.sha256(
            json.dumps(vendor_hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "product_contract_sha256": _content_hash(contract),
    }


def stable_diagnostic_candidate(value: dict[str, Any]) -> dict[str, Any]:
    """Compare content while retaining process-local locations as evidence only."""

    def sha(item):
        return isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) is not None

    if (
        not isinstance(value, dict)
        or not value.get("version")
        or not sha(value.get("build_sha256"))
        or not isinstance(value.get("diagnostic_content"), dict)
        or value["diagnostic_content"].get("scheme") != "close-diagnostic-content-v1"
    ):
        raise ValueError("diagnostic_candidate_content_unavailable")
    content = value["diagnostic_content"]
    code = content.get("code")
    runtime = content.get("runtime")
    if not isinstance(code, dict) or not all(
        sha(content.get(name))
        for name in (
            "vendor_lock_sha256",
            "vendor_files_sha256",
            "product_contract_sha256",
        )
    ):
        raise ValueError("diagnostic_candidate_content_unavailable")
    if (
        not isinstance(runtime, dict)
        or not sha(runtime.get("executable_sha256"))
        or not isinstance(runtime.get("implementation"), str)
        or not runtime["implementation"]
        or not isinstance(runtime.get("version"), str)
        or not runtime["version"]
        or value.get("frozen") is True
        and runtime["executable_sha256"] != value["build_sha256"]
    ):
        raise ValueError("diagnostic_candidate_content_unavailable")
    scheme = code.get("scheme")
    if scheme == "loaded-source-files":
        files = code.get("files")
        if (
            value.get("frozen") is not False
            or not isinstance(files, dict)
            or set(files) != set(_DIAGNOSTIC_SOURCE_FILES)
            or not all(sha(item) for item in files.values())
        ):
            raise ValueError("diagnostic_candidate_content_unavailable")
    elif scheme == "onefile-executable":
        if (
            value.get("frozen") is not True
            or code.get("executable_sha256") != value["build_sha256"]
        ):
            raise ValueError("diagnostic_candidate_content_unavailable")
    elif scheme == "verified-onedir":
        if value.get("frozen") is not True or not sha(code.get("payload_tree_sha256")):
            raise ValueError("diagnostic_candidate_content_unavailable")
    else:
        raise ValueError("diagnostic_candidate_content_unavailable")
    return {
        key: item for key, item in value.items() if key not in {"module_path", "python_executable"}
    }


def same_diagnostic_candidate(first, second) -> bool:
    try:
        return stable_diagnostic_candidate(first) == stable_diagnostic_candidate(second)
    except (TypeError, ValueError):
        return False


def application_progids(domain: str) -> tuple[str, ...]:
    bases = {
        "schematic": ("Viewdraw.Application",),
        "pcb": ("MGCPCB.Application", "MGCPCB.ExpeditionPCBApplication"),
        "library": ("LibraryManager.Application",),
    }[domain]
    fallback = (
        bases
        if domain == "library"
        else tuple(name for base in bases for name in (base, base + ".60"))
    )
    if os.name != "nt":
        return fallback
    try:
        import winreg

        found: set[str] = set()
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "") as registry:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(registry, index)
                except OSError:
                    break
                index += 1
                if name in bases or any(
                    name.startswith(base + ".") and name[len(base) + 1 :].isdigit()
                    for base in bases
                ):
                    found.add(name)
        if found:
            return tuple(
                sorted(
                    found,
                    key=lambda name: (
                        name.rsplit(".", 1)[-1].isdigit(),
                        int(name.rsplit(".", 1)[-1]) if name.rsplit(".", 1)[-1].isdigit() else 0,
                        -bases.index(
                            next(
                                base
                                for base in bases
                                if name == base or name.startswith(base + ".")
                            )
                        ),
                    ),
                    reverse=True,
                )
            )
    except (ImportError, OSError, AttributeError, TypeError):
        pass
    return fallback


def adapter_identity() -> dict[str, Any]:
    from . import __version__, cell_refresh, native_com_adapter, native_library_manager

    path = Path(native_com_adapter.__file__).resolve()
    frozen = bool(getattr(sys, "frozen", False))
    executable = Path(sys.executable).resolve()
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    build_hash = hashlib.sha256(executable.read_bytes()).hexdigest() if frozen else source_hash
    identity = {
        "version": __version__,
        "module_path": str(path),
        "module_sha256": source_hash,
        "build_sha256": build_hash,
        "identity_basis": "frozen_executable_bytes" if frozen else "loaded_source_bytes",
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if Path(__file__).is_file()
        else None,
        "library_manager_helper_sha256": hashlib.sha256(
            Path(native_library_manager.__file__).read_bytes()
        ).hexdigest()
        if Path(native_library_manager.__file__).is_file()
        else None,
        "cell_refresh_helper_sha256": hashlib.sha256(
            Path(cell_refresh.__file__).read_bytes()
        ).hexdigest()
        if Path(cell_refresh.__file__).is_file()
        else None,
        "python_executable": sys.executable,
        "client_bitness": struct.calcsize("P") * 8,
        "frozen": frozen,
    }
    for name in (
        "driver",
        "pcb_document_guard",
        "component_copy_origin",
        "drc_engine",
        "drc_failure_observation",
        "close_diagnostic",
        "closed_copy_startup",
        "empty_close_admission",
        "native_close_diagnostic",
        "diagnostic_exit",
    ):
        helper = Path(__file__).with_name(name + ".py")
        identity[name + "_helper_sha256"] = (
            hashlib.sha256(helper.read_bytes()).hexdigest() if helper.is_file() else None
        )
    handler = Path(__file__).with_name("cli") / "diagnostics.py"
    identity["close_diagnostic_cli_helper_sha256"] = (
        hashlib.sha256(handler.read_bytes()).hexdigest() if handler.is_file() else None
    )
    try:
        identity["diagnostic_content"] = _diagnostic_content_binding(
            Path(__file__).resolve().parent, executable, frozen, build_hash
        )
    except (OSError, TypeError, ValueError, KeyError, ImportError):
        # Other CLI features keep their existing behavior; the diagnostic and
        # its cooperative journal binding fail closed without this evidence.
        identity["diagnostic_content"] = None
    return identity


def application_identity(
    app: Any, domain: str, *, nativeom_ownership=None, designer_identity_probe=None
) -> dict[str, Any]:
    """Get type-library identity and only declared public non-secret scalar fields."""
    result: dict[str, Any] = {
        "domain": domain,
        "type_library": None,
        "public_fields": {},
    }
    proxy_probe = domain == "schematic" and designer_identity_probe == "application-proxy"
    members: set[str] = set()
    declared: set[str] = set()
    try:
        info = app._oleobj_.GetTypeInfo()
        lib, _ = info.GetContainingTypeLib()
        attrs = lib.GetLibAttr()
        result["type_library"] = {
            "guid": str(attrs[0]),
            "lcid": attrs[1],
            "syskind": attrs[2],
            "major": attrs[3],
            "minor": attrs[4],
            "flags": attrs[5],
            "name": lib.GetDocumentation(-1)[0],
        }
        attr = info.GetTypeAttr()
        for index in range(attr.cFuncs):
            desc = info.GetFuncDesc(index)
            declared.add(info.GetNames(desc[0])[0])
            if desc[4] == 2:  # INVOKE_PROPERTYGET; do not call undocumented methods
                members.add(info.GetNames(desc[0])[0])
    except Exception as exc:
        result["type_library_error"] = type(exc).__name__
    permitted = {
        "Version",
        "ApplicationVersion",
        "ProductVersion",
        "BuildVersion",
        "ProductName",
        "Name",
        "FullName",
        "HWnd",
        "Hwnd",
        "HWND",
        "WindowHandle",
        "ActiveViewHandle",
        "ProcessID",
        "InstanceGuid",
        "LicenseMode",
        "LicenseType",
        "ExpirationDate",
        "ExpiryDate",
        "ValidUntil",
    }
    for name in sorted(members & permitted):
        try:
            value = getattr(app, name)
            if value is None or isinstance(value, (str, int, float, bool)):
                result["public_fields"][name] = value
        except Exception:
            continue
    result["license_state"] = "unknown"
    result["expiration"] = None
    result["product_version"] = next(
        (
            result["public_fields"][name]
            for name in ("ProductVersion", "ApplicationVersion", "Version")
            if name in result["public_fields"]
        ),
        None,
    )
    result["pid"] = None
    result["document"] = {
        "open": None,
        "path": None,
        "designs": None,
        "status": "unknown",
    }
    try:

        def get(obj, name):
            value = getattr(obj, name)
            return value() if callable(value) else value

        if domain == "schematic" and {"IsProjectOpened", "GetProjectData"} <= declared:
            opened = get(app, "IsProjectOpened")
            data = get(app, "GetProjectData")
            path = get(data, "GetProjectFilePath")
            designs = get(data, "GetiCDBDesigns") if opened is True else None
            names = (
                [str(designs.GetItem(index)) for index in range(1, int(designs.GetCount()) + 1)]
                if designs is not None
                else []
            )
            result["document"] = {
                "open": opened if type(opened) is bool else None,
                "path": path if isinstance(path, str) and path else None,
                "designs": names,
                "status": "observed",
                "source": "IsProjectOpened/GetProjectData public getters",
            }
        elif domain == "pcb" and "ActiveDocument" in declared:
            doc = app.ActiveDocument
            path = get(doc, "FullName") if doc is not None else None
            result["document"] = {
                "open": doc is not None,
                "path": path,
                "designs": None,
                "status": "observed",
                "source": "ActiveDocument.FullName public getter",
            }
        elif domain == "library" and "ActiveLibrary" in declared:
            # A pywin32 Dispatch object is callable through its default member;
            # ActiveLibrary is already a property result, not a method to invoke.
            library = app.ActiveLibrary
            result["document"] = {
                "open": library is not None,
                "path": get(library, "FullName") if library is not None else None,
                "designs": None,
                "status": "observed",
                "source": "ActiveLibrary.FullName public getter",
            }
    except Exception as exc:
        result["document"]["error_type"] = type(exc).__name__
    if os.name == "nt" and not proxy_probe:
        import ctypes

        handles = {
            name: result["public_fields"].get(name)
            for name in ("HWnd", "Hwnd", "HWND", "WindowHandle", "ActiveViewHandle")
        }
        if domain == "pcb" and "Gui" in members:
            try:
                gui = app.Gui
                info = gui._oleobj_.GetTypeInfo()
                if any(
                    info.GetNames(info.GetFuncDesc(i)[0])[0] == "HWND"
                    and info.GetFuncDesc(i)[4] == 2
                    for i in range(info.GetTypeAttr().cFuncs)
                ):
                    handles["Gui.HWND"] = gui.HWND
            except Exception:
                pass
        for name, hwnd in handles.items():
            if isinstance(hwnd, int) and hwnd:
                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    result["pid"] = pid.value
                    result["pid_source"] = {
                        "property": name,
                        "hwnd": hwnd,
                        "api": "GetWindowThreadProcessId",
                    }
                    break
    if os.name == "nt" and domain == "schematic" and result["pid"] is None and not proxy_probe:
        from .designer_window_identity import observe as observe_designer_window

        binding = observe_designer_window(
            app, result["document"], result["public_fields"].get("FullName")
        )
        result["designer_window_binding"] = binding
        if binding.get("status") == "observed":
            result["pid"] = binding["pid"]
            result["pid_source"] = {
                "property": binding["source"],
                "hwnd": binding["hwnd"],
                "api": "GetWindowThreadProcessId",
            }
    if result["pid"] is None and isinstance(result["public_fields"].get("ProcessID"), int):
        result["pid"] = result["public_fields"]["ProcessID"]
        result["pid_source"] = {"property": "ProcessID"}
    if result["pid"] is None and isinstance(result["public_fields"].get("FullName"), str):
        from .native_process import unique_executable_pid

        match = unique_executable_pid(domain, result["public_fields"]["FullName"])
        if match:
            result["pid"] = match["pid"]
            result["pid_source"] = {
                "property": "FullName",
                "matching_process": match,
                "basis": "exact executable path and unique live process; ambiguity is refused",
            }
    if proxy_probe:
        from .designer_proxy_identity import observe as observe_application_proxy

        result["designer_application_proxy_observation"] = observe_application_proxy(
            app, result, nativeom_ownership
        )
    elif os.name == "nt" and domain == "schematic":
        from .designer_existing_views import eligible
        from .designer_existing_views import observe as observe_existing_views

        if eligible(result.get("designer_window_binding")):
            result["designer_existing_views_observation"] = observe_existing_views(
                app,
                result["document"],
                result["public_fields"].get("FullName"),
                result.get("pid"),
                result["designer_window_binding"],
            )
            graph = result["designer_existing_views_observation"]
            if (
                graph.get("observation_complete") is True
                and graph.get("document_count") == 0
                and graph.get("total_view_count") == 0
            ):
                from .designer_nativeom import observe as observe_nativeom

                result["designer_nativeom_observation"] = observe_nativeom(
                    app, result, nativeom_ownership
                )
    return result
