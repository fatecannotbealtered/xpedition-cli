"""Read a bounded onedir build binding; this is neither authority nor admission.

The manifest is an external package observation. Its authenticity comes from
the separately pinned delivery archive/launcher, not from this JSON file itself.
The source map is compiled into PYZ; the runtime hashes the actual executable
and every collected payload and preserves that observation in the owner intent.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import stat
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

SCHEME = "designer-owner-onedir-v1"
GENERATED_MODULE = "xpedition_owner_build_binding"
MANIFEST_NAME = "owner-bundle-manifest.json"
OWNER_SOURCE_NAMES = (
    "designer_native_owner.py",
    "designer_owner_transport.py",
    "designer_native_launch.py",
    "designer_startup.py",
    "designer_host_bootstrap.py",
    "startup_input_seal.py",
    "execution_lifecycle.py",
    "designer_owner_dispatch.py",
    "native_com_adapter.py",
    "designer_source_binding.py",
    "designer_first_open.py",
    "schematic_edit_protection.py",
    "designer_dual_invoke.py",
)
MAX_FILES = 8192
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


class FrozenBindingError(ValueError):
    """Only fixed, non-secret explanations cross the internal entry boundary."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate_sources(value):
    if (
        type(value) is not dict
        or set(value) != set(OWNER_SOURCE_NAMES)
        or any(
            type(v) is not str or re.fullmatch(r"[a-f0-9]{64}", v) is None for v in value.values()
        )
    ):
        raise FrozenBindingError("compiled_owner_source_binding_incomplete")
    return dict(value)


def _plain(path, *, directory=False):
    info = path.lstat()
    if (
        path.is_symlink()
        or getattr(info, "st_file_attributes", 0) & 0x400
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    ):
        raise FrozenBindingError("onedir_payload_not_plain")
    return info


def _file(path, limit):
    before = _plain(path)
    if before.st_size > limit:
        raise FrozenBindingError("onedir_payload_byte_limit")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while block := stream.read(min(1024 * 1024, limit - size + 1)):
            size += len(block)
            if size > limit:
                raise FrozenBindingError("onedir_payload_byte_limit")
            digest.update(block)
    after = _plain(path)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or size != before.st_size:
        raise FrozenBindingError("onedir_payload_changed_during_read")
    return {"bytes": size, "sha256": digest.hexdigest()}


def collect_bundle_files(root):
    """Observe regular payloads only; exclude the non-self-hashing manifest."""
    root = Path(root).absolute()
    _plain(root, directory=True)
    pending, rows, names, directories, entries, total = [root], [], set(), 0, 0, 0
    while pending:
        directory = pending.pop()
        _plain(directory, directory=True)
        directories += 1
        if directories > MAX_FILES:
            raise FrozenBindingError("onedir_payload_file_limit")
        with os.scandir(directory) as scan:
            for entry in scan:
                entries += 1
                if entries > 2 * MAX_FILES:
                    raise FrozenBindingError("onedir_payload_file_limit")
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if relative == MANIFEST_NAME:
                    _plain(path)
                    continue
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    _plain(path, directory=True)
                    pending.append(path)
                    continue
                if len(rows) >= MAX_FILES:
                    raise FrozenBindingError("onedir_payload_file_limit")
                if relative.casefold() in names or len(relative) > 1024:
                    raise FrozenBindingError("onedir_payload_name_conflict")
                names.add(relative.casefold())
                facts = _file(path, min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total))
                total += facts["bytes"]
                rows.append({"path": relative, **facts})
    return sorted(rows, key=lambda row: row["path"])


def _manifest(path):
    before = _plain(path)
    if before.st_size > MAX_MANIFEST_BYTES:
        raise FrozenBindingError("onedir_manifest_byte_limit")
    with path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES or len(raw) != before.st_size:
        raise FrozenBindingError("onedir_manifest_byte_limit")
    after = _plain(path)
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise FrozenBindingError("onedir_manifest_changed_during_read")
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError):
        raise FrozenBindingError("onedir_manifest_invalid") from None
    return value, raw


def verify_onedir_bundle(executable, sources):
    executable = Path(executable).absolute()
    expected_sources = validate_sources(sources)
    root = executable.parent
    _plain(root / "_internal", directory=True)
    value, raw = _manifest(root / MANIFEST_NAME)
    if (
        type(value) is not dict
        or set(value) != {"scheme", "executable", "source_sha256", "files"}
        or value.get("scheme") != SCHEME
        or value.get("executable") != executable.name
        or value.get("source_sha256") != expected_sources
        or type(value.get("files")) is not list
        or not 1 <= len(value["files"]) <= MAX_FILES
    ):
        raise FrozenBindingError("onedir_manifest_binding_mismatch")
    seen = set()
    for row in value["files"]:
        if type(row) is not dict or set(row) != {"path", "bytes", "sha256"}:
            raise FrozenBindingError("onedir_manifest_file_invalid")
        relative = row["path"]
        if (
            type(relative) is not str
            or not relative
            or len(relative) > 1024
            or "\\" in relative
            or ":" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in {".", ".."} for part in relative.split("/"))
            or relative == MANIFEST_NAME
            or relative.casefold() in seen
            or type(row["bytes"]) is not int
            or not 0 <= row["bytes"] <= MAX_FILE_BYTES
            or type(row["sha256"]) is not str
            or re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is None
        ):
            raise FrozenBindingError("onedir_manifest_file_invalid")
        seen.add(relative.casefold())
    rows = collect_bundle_files(root)
    if rows != value["files"] or not any(row["path"] == executable.name for row in rows):
        raise FrozenBindingError("onedir_payload_manifest_mismatch")
    return {
        "scheme": SCHEME,
        "compiled_source_sha256": expected_sources,
        "executable_sha256": next(row["sha256"] for row in rows if row["path"] == executable.name),
        "payload_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "payload_tree_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
        "payload_file_count": len(rows),
        "payload_total_bytes": sum(row["bytes"] for row in rows),
    }


def _onedir_paths():
    if not getattr(sys, "frozen", False) or not hasattr(sys, "_MEIPASS"):
        raise FrozenBindingError("frozen_onedir_owner_required")
    executable = Path(sys.executable).absolute()
    if Path(sys._MEIPASS).absolute() != executable.parent / "_internal":
        raise FrozenBindingError("independent_onefile_owner_unqualified")
    return executable, executable.parent / "_internal"


def frozen_owner_binding():
    """Only the explicit, fully collected onedir profile can reach this path."""
    executable, _ = _onedir_paths()
    try:
        compiled = importlib.import_module(GENERATED_MODULE)
    except ImportError:
        raise FrozenBindingError("compiled_owner_source_binding_missing") from None
    if getattr(compiled, "SCHEME", None) != SCHEME:
        raise FrozenBindingError("compiled_owner_source_binding_incomplete")
    return verify_onedir_bundle(executable, getattr(compiled, "SOURCES", None))


def frozen_source_binding():
    """Preserve the historical SHA-map shape with distinct frozen payload keys.

    This binds observed disk bytes within the existing external-delivery trust
    boundary; it does not authenticate the manifest or prove an executing image
    cannot have been replaced. Source-mode historical maps remain untouched.
    """
    facts = frozen_owner_binding()
    return {
        **facts["compiled_source_sha256"],
        "@frozen/executable": facts["executable_sha256"],
        "@frozen/payload_manifest": facts["payload_manifest_sha256"],
        "@frozen/payload_tree": facts["payload_tree_sha256"],
    }


def external_child_environment(inherited):
    """Remove only bundle/interpreter launch selectors from the CAD child copy."""
    environment = dict(inherited)
    if not (getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")):
        return environment
    _, internal = _onedir_paths()
    internal_name = os.path.normcase(os.path.normpath(str(internal)))
    for name in list(environment):
        upper = name.upper()
        if upper.startswith(("_PYI_", "PYINSTALLER_", "PYTHON")):
            del environment[name]
        elif upper == "PATH":
            paths = []
            for value in environment[name].split(os.pathsep):
                normalized = os.path.normcase(os.path.normpath(value.strip('"')))
                if normalized == internal_name or normalized.startswith(internal_name + os.sep):
                    continue
                paths.append(value)
            environment[name] = os.pathsep.join(paths)
    return environment


def _dll_directory_api():
    import ctypes
    from ctypes import wintypes as w

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetDllDirectoryW.argtypes, api.GetDllDirectoryW.restype = (w.DWORD, w.LPWSTR), w.DWORD
    api.SetDllDirectoryW.argtypes, api.SetDllDirectoryW.restype = (w.LPCWSTR,), w.BOOL
    return api


@contextmanager
def external_application_dll_search():
    """Clear PyInstaller's Windows DLL path only across the external Popen call.

    PyInstaller 6.21 documents SetDllDirectoryW(NULL) for external applications.
    Restore the observed nonempty directory even when process creation raises;
    no COM call, wait, or import belongs inside this small process-wide window.
    """
    if not (getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")):
        yield
        return
    _onedir_paths()
    if os.name != "nt":
        raise FrozenBindingError("windows_frozen_dll_search_required")
    import ctypes

    api = _dll_directory_api()
    buffer = ctypes.create_unicode_buffer(32768)
    count = int(api.GetDllDirectoryW(len(buffer), buffer))
    if not 0 < count < len(buffer) or len(buffer.value) != count:
        raise FrozenBindingError("frozen_dll_search_path_unobservable")
    previous = buffer.value
    if not api.SetDllDirectoryW(None):
        raise FrozenBindingError("frozen_dll_search_reset_failed")
    try:
        yield
    finally:
        if not api.SetDllDirectoryW(previous):
            raise FrozenBindingError("frozen_dll_search_restore_failed")
